from __future__ import annotations

import json
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
from orchestrator_runtime import OrchestratorRuntime  # noqa: E402
from runtime_types import TaskSpec  # noqa: E402


class RuntimeTerminalGuardTest(unittest.TestCase):
    def test_done_gatekeeper_is_blocked_without_fresh_visual_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "Terminal guard fixture.\n"
                "\\end{document}\n",
                encoding="utf-8",
            )

            def fake_core(
                self: OrchestratorRuntime,
                **_: object,
            ) -> dict[str, object]:
                self.manager.update(
                    {
                        "compile_success": True,
                        "page_images_rendered": True,
                        "last_gatekeeper_decision": "DONE",
                        "status": "DONE",
                        "defect_summary": {
                            "initial_total": 1,
                            "resolved": 1,
                            "remaining": 0,
                        },
                        "artifacts": {
                            "page_images_dir": "data/pages",
                            "visual_signal_report": "data/visual_signal_report.json",
                            "defect_report": "data/defect_report.json",
                            "gatekeeper_decision": "data/gatekeeper_decision.json",
                        },
                    }
                )
                return self.manager.load()

            spec = TaskSpec.from_dict(
                {
                    "task_type": "visual_only",
                    "project_root": str(root),
                    "main_tex": "main.tex",
                    "allow_source_mutation": False,
                }
            )

            with (
                mock.patch.object(
                    orchestrator_runtime,
                    "compile_latex",
                    return_value={
                        "success": True,
                        "pdf_path": str(root / "main.pdf"),
                        "timeout": False,
                        "log_file": "main.log",
                    },
                ),
                mock.patch.object(
                    orchestrator_runtime,
                    "render_pdf_pages",
                    return_value={"success": True, "page_dir": "data/pages"},
                ),
                mock.patch.object(
                    orchestrator_runtime,
                    "inspect_endmatter_float_intrusion",
                    return_value={"available": True, "hard_failures": []},
                ),
                mock.patch.object(OrchestratorRuntime, "_run_round_core", fake_core),
            ):
                result = OrchestratorRuntime(state_path=str(root / "data" / "state.json")).run_task(
                    task_spec=spec,
                    output_path="data/run_result_check_visual.json",
                )

            self.assertEqual(result["gatekeeper_decision"], "DONE")
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(
                result["failure"]["failure_type"],
                "terminal_success_without_fresh_visual_evidence",
            )
            self.assertEqual(result["artifact_manifest"]["freshness"]["status"], "stale_or_missing")
            self.assertIn(
                "pdf_exists",
                result["failure"]["artifact_freshness"]["blocking_checks"],
            )

            state = json.loads((root / "data" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "BLOCKED")
            self.assertEqual(state["last_gatekeeper_decision"], "DONE")
            self.assertEqual(
                state["terminal_success_guard"]["failure_type"],
                "terminal_success_without_fresh_visual_evidence",
            )
            self.assertEqual(
                state["failure_tracking"]["last_failure_type"],
                "terminal_success_without_fresh_visual_evidence",
            )
            status_view = OrchestratorRuntime(state_path=str(root / "data" / "state.json")).status_view()
            self.assertEqual(status_view["status"], "BLOCKED")
            self.assertEqual(
                status_view["terminal_success_guard"]["failure_type"],
                "terminal_success_without_fresh_visual_evidence",
            )

            events = [
                json.loads(line)
                for line in (root / result["event_log"]).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(events[-1]["type"], "task_blocked")
            self.assertEqual(events[-1]["state"], "BLOCKED")

    def test_legacy_completion_status_requires_fresh_visual_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\\documentclass{article}\n\\begin{document}\nFixture.\n\\end{document}\n",
                encoding="utf-8",
            )

            completion = paperfit_command._layout_completion_status(
                {
                    "project_root": str(root),
                    "stop_reason": "done",
                    "round_count": 1,
                    "blocked_reasons": [],
                    "final_state_summary": {
                        "main_tex": "main.tex",
                        "last_gatekeeper_decision": "DONE",
                        "defect_summary": {
                            "initial_total": 1,
                            "resolved": 1,
                            "remaining": 0,
                        },
                        "artifacts": {
                            "page_images_dir": "data/pages",
                            "visual_signal_report": "data/visual_signal_report.json",
                            "defect_report": "data/defect_report.json",
                            "gatekeeper_decision": "data/gatekeeper_decision.json",
                        },
                    },
                }
            )

            self.assertEqual(completion["status"], "blocked")
            self.assertEqual(
                completion["terminal_success_guard"]["failure_type"],
                "terminal_success_without_fresh_visual_evidence",
            )
            self.assertIn(
                "terminal_success_without_fresh_visual_evidence",
                completion["failure_reasons"],
            )


if __name__ == "__main__":
    unittest.main()
