from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from regression.run_case import (  # noqa: E402
    build_task_spec,
    run_paperfit_case,
)


def _make_b0_project(root: Path) -> tuple[Path, str]:
    project = root / "b0_project"
    project.mkdir(parents=True)
    (project / "paper.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\nplain body with enough words here\n\\end{document}\n",
        encoding="utf-8",
    )
    return project, "paper.tex"


def _fake_runner_factory(payload_builder):
    def runner(project: Path, task_path: Path, output_path: Path) -> dict:
        payload = payload_builder()
        output_path.write_text(json.dumps(payload), encoding="utf-8")
        return payload

    return runner


def _run_payload(*, compile_ok=True, render_ok=True, case_status=None):
    return {
        "status": "done" if compile_ok else "blocked",
        "runtime_actions": {
            "compile": {
                "success": compile_ok,
                "case_status": case_status or ("layout_evaluable" if compile_ok else "source_compile_error"),
            },
            "render": {"success": render_ok},
        },
        "failure": None,
    }


class TaskSpecTest(unittest.TestCase):
    def test_spec_requires_apply_machinery(self):
        spec = build_task_spec(project_root=Path("."), main_tex_relpath="paper.tex", apply=True)
        self.assertTrue(spec["allow_source_mutation"])
        self.assertTrue(spec["pre_repair_snapshot_required"])
        self.assertFalse(spec["dry_run_source_mutation"])
        self.assertEqual(spec["task_type"], "full_vto")

    def test_dry_run_spec(self):
        spec = build_task_spec(project_root=Path("."), main_tex_relpath="paper.tex", apply=False)
        self.assertTrue(spec["dry_run_source_mutation"])


class RunPaperFitCaseTest(unittest.TestCase):
    def test_happy_path_layout_evaluable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            b0, main = _make_b0_project(root)
            result = run_paperfit_case(
                b0_project=b0,
                work_root=root / "runs",
                case_id="case-a",
                paper_id="AAAI",
                main_tex_relpath=main,
                runner=_fake_runner_factory(lambda: _run_payload()),
            )
            self.assertEqual(result.status, "layout_evaluable")
            self.assertEqual(result.reasons, [])
            # 工作目录是从 B0 拷贝的独立副本，B0 原件未被动过
            self.assertTrue((result.work_dir / "paper.tex").exists())
            self.assertTrue((result.work_dir / "data" / "task.json").exists())
            self.assertIn("plain body", b0.joinpath(main).read_text(encoding="utf-8"))

    def test_compile_failure_maps_case_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            b0, main = _make_b0_project(root)
            result = run_paperfit_case(
                b0_project=b0,
                work_root=root / "runs",
                case_id="case-b",
                paper_id="ICLR",
                main_tex_relpath=main,
                runner=_fake_runner_factory(
                    lambda: _run_payload(compile_ok=False, case_status="environment_ineligible")
                ),
            )
            self.assertEqual(result.status, "environment_ineligible")
            self.assertTrue(result.reasons)

    def test_runner_crash_is_captured_not_raised(self):
        def crashing_runner(project, task_path, output_path):
            raise RuntimeError("boom")

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            b0, main = _make_b0_project(root)
            result = run_paperfit_case(
                b0_project=b0,
                work_root=root / "runs",
                case_id="case-c",
                paper_id="ECCV",
                main_tex_relpath=main,
                runner=crashing_runner,
            )
            self.assertEqual(result.status, "runner_crash")
            self.assertIn("boom", result.reasons[0])

    def test_guard_rollback_maps_to_dedicated_status(self):
        payload = {
            "status": "blocked",
            "runtime_actions": {
                "initial_observe": {
                    "compile": {"success": True, "case_status": "layout_evaluable"},
                    "render": {"success": True},
                }
            },
            "failure": {"failure_type": "post_repair_hard_guard_failed", "reason": "BLOCKED"},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            b0, main = _make_b0_project(root)
            result = run_paperfit_case(
                b0_project=b0,
                work_root=root / "runs",
                case_id="case-guard",
                paper_id="NeurIPS",
                main_tex_relpath=main,
                runner=_fake_runner_factory(lambda: payload),
            )
            self.assertEqual(result.status, "guard_rolled_back")

    def test_compile_action_nested_in_initial_observe(self):
        payload = {
            "status": "done",
            "runtime_actions": {
                "initial_observe": {
                    "compile": {"success": True, "case_status": "layout_evaluable"},
                    "render": {"success": True},
                }
            },
            "failure": None,
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            b0, main = _make_b0_project(root)
            result = run_paperfit_case(
                b0_project=b0,
                work_root=root / "runs",
                case_id="case-nested",
                paper_id="ICML",
                main_tex_relpath=main,
                runner=_fake_runner_factory(lambda: payload),
            )
            self.assertEqual(result.status, "layout_evaluable")

    def test_missing_output_reported(self):
        def silent_runner(project, task_path, output_path):
            return {"status": "runner_crash", "failure": {"failure_type": "paperfit_runner_exit", "reason": "returncode=1"}}

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            b0, main = _make_b0_project(root)
            result = run_paperfit_case(
                b0_project=b0,
                work_root=root / "runs",
                case_id="case-d",
                paper_id="CVPRICCV",
                main_tex_relpath=main,
                runner=silent_runner,
            )
            self.assertEqual(result.status, "runner_crash")


if __name__ == "__main__":
    unittest.main()
