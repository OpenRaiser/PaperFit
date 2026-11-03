from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import orchestrator_runtime  # noqa: E402
import paperfit_command  # noqa: E402
import runtime_actions  # noqa: E402
from orchestrator_runtime import OrchestratorRuntime  # noqa: E402
from runtime_types import TaskSpec  # noqa: E402


def _fake_compile_result(
    *,
    success: bool,
    case_status: str,
    missing_sty: list[str] | None = None,
    missing_images: list[str] | None = None,
) -> dict:
    return {
        "success": success,
        "timeout": False,
        "timeout_sec": 120,
        "case_status": case_status,
        "missing_sty": missing_sty or [],
        "missing_images": missing_images or [],
        "pdf_path": None if not success else "main.pdf",
    }


class CompileLatexCaseStatusTest(unittest.TestCase):
    """runtime_actions.compile_latex must classify its own log output."""

    def _run_compile(self, tmpdir: Path, *, returncode: int, log_text: str, create_pdf: bool) -> dict:
        main_tex = tmpdir / "main.tex"
        main_tex.write_text("\\documentclass{article}\n\\begin{document}x\\end{document}\n", encoding="utf-8")

        def fake_run_command(cmd, *, cwd, timeout=None):
            if create_pdf:
                (tmpdir / "main.pdf").write_bytes(b"%PDF-1.4 fake")
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=returncode,
                stdout=log_text,
                stderr="",
            )

        with mock.patch.object(runtime_actions, "run_command", side_effect=fake_run_command):
            return runtime_actions.compile_latex(tmpdir, main_tex=main_tex)

    def test_missing_sty_is_environment_ineligible(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._run_compile(
                Path(tmpdir),
                returncode=1,
                log_text="File `algpseudocode.sty' not found.",
                create_pdf=False,
            )
            self.assertFalse(result["success"])
            self.assertEqual(result["case_status"], "environment_ineligible")
            self.assertEqual(result["missing_sty"], ["algpseudocode"])

    def test_missing_image_is_input_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._run_compile(
                Path(tmpdir),
                returncode=1,
                log_text="File `figs/overview.pdf' not found.",
                create_pdf=False,
            )
            self.assertEqual(result["case_status"], "input_incomplete")
            self.assertEqual(result["missing_images"], ["figs/overview.pdf"])

    def test_missing_image_takes_precedence_over_missing_sty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._run_compile(
                Path(tmpdir),
                returncode=1,
                log_text=(
                    "File `figs/overview.png' not found.\n"
                    "File `algpseudocode.sty' not found."
                ),
                create_pdf=False,
            )
            self.assertEqual(result["case_status"], "input_incomplete")

    def test_success_with_overfull_is_layout_evaluable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._run_compile(
                Path(tmpdir),
                returncode=0,
                log_text=(
                    "Output written on main.pdf (3 pages).\n"
                    "Overfull \\hbox (12.34pt too wide) detected at lines 8--12"
                ),
                create_pdf=True,
            )
            self.assertTrue(result["success"])
            self.assertEqual(result["case_status"], "layout_evaluable")

    def test_success_without_layout_signals_is_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._run_compile(
                Path(tmpdir),
                returncode=0,
                log_text="Output written on main.pdf (3 pages).",
                create_pdf=True,
            )
            self.assertTrue(result["success"])
            self.assertEqual(result["case_status"], "eligible")


class VisualOnlyCompileCaseStatusTest(unittest.TestCase):
    """environment/input-ineligible compiles must block before round observation."""

    def _run_visual_only(self, root: Path, compile_result: dict) -> dict:
        main_tex = root / "main.tex"
        main_tex.write_text(
            "\\documentclass{article}\n\\begin{document}\nFixture.\n\\end{document}\n",
            encoding="utf-8",
        )
        with (
            mock.patch.object(orchestrator_runtime, "compile_latex", return_value=compile_result),
            mock.patch.object(orchestrator_runtime, "render_pdf_pages") as render_mock,
            mock.patch.object(
                OrchestratorRuntime,
                "_run_round_core",
                side_effect=AssertionError("_run_round_core should not run for ineligible compile"),
            ),
        ):
            report = paperfit_command._run_visual_only(
                root,
                main_tex=main_tex,
                template=None,
                target_pages=None,
            )
        render_mock.assert_not_called()
        return report

    def test_environment_ineligible_blocks_before_round_observation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "data").mkdir()
            report = self._run_visual_only(
                root,
                _fake_compile_result(
                    success=False,
                    case_status="environment_ineligible",
                    missing_sty=["algpseudocode"],
                ),
            )
            self.assertEqual(report["state_summary"]["status"], "BLOCKED")
            self.assertEqual(report["state_summary"]["last_gatekeeper_decision"], "BLOCKED")
            self.assertEqual(report["run_result"]["status"], "blocked")
            self.assertEqual(
                report["run_result"]["failure"]["failure_type"],
                "environment_ineligible",
            )
            state = json.loads((root / "data" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["defect_summary"]["remaining"], 0)
            self.assertTrue(
                any("algpseudocode" in str(action) for action in state["next_actions"]),
                state["next_actions"],
            )
            compile_action = state["runtime_event_summary"]["actions"]["compile"]
            self.assertEqual(compile_action.get("case_status"), "environment_ineligible")

    def test_input_incomplete_blocks_before_round_observation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "data").mkdir()
            report = self._run_visual_only(
                root,
                _fake_compile_result(
                    success=False,
                    case_status="input_incomplete",
                    missing_images=["figs/overview.pdf"],
                ),
            )
            self.assertEqual(report["run_result"]["failure"]["failure_type"], "input_incomplete")
            state = json.loads((root / "data" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["defect_summary"]["remaining"], 0)
            self.assertTrue(
                any("figs/overview.pdf" in str(action) for action in state["next_actions"]),
                state["next_actions"],
            )

    def test_source_compile_error_still_flows_through_round_core(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "data").mkdir()
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\\documentclass{article}\n\\begin{document}\nBroken fixture.\n\\end{document}\n",
                encoding="utf-8",
            )
            core_calls = {"count": 0}

            def fake_core(*args, **kwargs):
                core_calls["count"] += 1
                runtime = OrchestratorRuntime(state_path=str(root / "data" / "state.json"))
                runtime.manager.update(
                    {
                        "status": "BLOCKED",
                        "last_gatekeeper_decision": "BLOCKED",
                    }
                )
                return runtime.manager.load()

            with (
                mock.patch.object(
                    orchestrator_runtime,
                    "compile_latex",
                    return_value=_fake_compile_result(
                        success=False,
                        case_status="source_compile_error",
                    ),
                ),
                mock.patch.object(OrchestratorRuntime, "_run_round_core", fake_core),
            ):
                report = paperfit_command._run_visual_only(
                    root,
                    main_tex=main_tex,
                    template=None,
                    target_pages=None,
                )
            self.assertEqual(core_calls["count"], 1)
            self.assertNotEqual(
                report["run_result"]["failure"]["failure_type"],
                "environment_ineligible",
            )


class SourceChangingCompileCaseStatusTest(unittest.TestCase):
    def test_environment_ineligible_blocks_source_changing_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "data").mkdir()
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\\documentclass{article}\n\\begin{document}\nFixture.\n\\end{document}\n",
                encoding="utf-8",
            )
            spec = TaskSpec.from_dict(
                {
                    "task_type": "full_vto",
                    "project_root": str(root),
                    "main_tex": "main.tex",
                    "allow_source_mutation": True,
                    "pre_repair_snapshot_required": True,
                    "rollback_policy": "required",
                }
            )
            with (
                mock.patch.object(
                    orchestrator_runtime,
                    "compile_latex",
                    return_value=_fake_compile_result(
                        success=False,
                        case_status="environment_ineligible",
                        missing_sty=["algpseudocode"],
                    ),
                ),
                mock.patch.object(orchestrator_runtime, "render_pdf_pages") as render_mock,
                mock.patch.object(
                    OrchestratorRuntime,
                    "_run_round_core",
                    side_effect=AssertionError("_run_round_core should not run for ineligible compile"),
                ),
            ):
                result = OrchestratorRuntime(state_path=str(root / "data" / "state.json")).run_task(
                    task_spec=spec,
                    output_path="data/run_result_full_vto.json",
                )
            render_mock.assert_not_called()
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["failure"]["failure_type"], "environment_ineligible")
            self.assertTrue(result["runtime_actions"]["pre_repair_snapshot"]["success"])
            state = json.loads((root / "data" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "BLOCKED")
            self.assertEqual(state["defect_summary"]["remaining"], 0)


if __name__ == "__main__":
    unittest.main()
