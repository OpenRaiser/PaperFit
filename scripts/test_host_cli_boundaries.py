"""Keep optional CLI batches distinct from user-authorized native host repairs."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paperfit_command
from orchestrator_runtime import OrchestratorRuntime
from runtime_repair_plan import attach_repair_plan_fingerprint
from runtime_snapshots import load_snapshot_manifest, restore_snapshot


@contextlib.contextmanager
def project_cwd(root: Path):
    previous = Path.cwd()
    try:
        os.chdir(root)
        yield
    finally:
        os.chdir(previous)


class HostCliBoundaryTest(unittest.TestCase):
    def fixture(self) -> Path:
        temporary = tempfile.TemporaryDirectory(prefix="pf-host-cli-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "main.tex").write_text(
            "\\documentclass{article}\n\\input{local}\n\\begin{document}\n"
            "Scientific PaperFitWidowTailToken example reports 17.3.\n"
            "% DISTURBANCE:E2_template_page_budget_shift:BEGIN\n"
            "Scientific content remains in this marked region.\n"
            "% DISTURBANCE:E2_template_page_budget_shift:END\n"
            "\\input{body}\n\\end{document}\n", encoding="utf-8")
        (root / "local.tex").write_text("% Local project settings.\n", encoding="utf-8")
        (root / "body.tex").write_text("Preserved included scientific words.\n", encoding="utf-8")
        return root

    def test_legacy_dry_run_preserves_all_sources_and_never_executes_repairs(self):
        root = self.fixture()
        before = {path.name: path.read_bytes() for path in root.glob("*.tex")}
        summary = {"main_tex": "main.tex", "last_gatekeeper_decision": "CONTINUE",
                   "defect_summary": {"remaining": 1}, "artifacts": {}}
        with (mock.patch.dict(os.environ, {"PAPERFIT_RUN_UNTIL_DONE": "0",
                                          "PAPERFIT_TYPED_FIX_LAYOUT_APPLY": "1"}),
              mock.patch.object(paperfit_command, "_compile", return_value={"success": True, "pdf_path": None}),
              mock.patch.object(paperfit_command, "_run_round", return_value={}),
              mock.patch.object(paperfit_command, "_summarize_runtime_status", return_value=summary),
              mock.patch.object(paperfit_command, "_sanitize_precompile_sources") as sanitizer,
              mock.patch.object(paperfit_command, "_execute_repair_plan") as executor):
            report = paperfit_command._run_fix_layout(
                root, main_tex=root / "main.tex", template=None, target_pages=None, max_rounds=1)
        self.assertEqual(before, {path.name: path.read_bytes() for path in root.glob("*.tex")})
        sanitizer.assert_not_called()
        executor.assert_not_called()
        self.assertTrue(report["dry_run_source_mutation"])
        self.assertEqual(report["stop_reason"], "approval_required")
        self.assertEqual(paperfit_command._layout_completion_status(report)["status"], "approval_required")
        self.assertTrue(report["host_guidance"]["editing_authority"]["native_editing_supported"])
        self.assertFalse(report["host_guidance"]["editing_authority"]["ordinary_layout_reapproval_required"])

    def test_legacy_apply_does_not_sanitize_benchmark_markers(self):
        root = self.fixture()
        before = (root / "main.tex").read_bytes()
        summary = {"main_tex": "main.tex", "last_gatekeeper_decision": "CONTINUE",
                   "defect_summary": {"remaining": 1}, "artifacts": {}}
        with (mock.patch.dict(os.environ, {"PAPERFIT_RUN_UNTIL_DONE": "0"}),
              mock.patch.object(paperfit_command, "_compile", return_value={"success": True, "pdf_path": None}),
              mock.patch.object(paperfit_command, "_run_round", return_value={}),
              mock.patch.object(paperfit_command, "_summarize_runtime_status", return_value=summary),
              mock.patch.object(paperfit_command, "_sanitize_precompile_sources") as sanitizer,
              mock.patch.object(paperfit_command, "_execute_repair_plan", return_value={
                  "repair_execution_summary": {"applied_count": 0, "status": "noop"}}) as executor):
            report = paperfit_command._run_fix_layout(
                root, main_tex=root / "main.tex", template=None, target_pages=None,
                max_rounds=1, apply_source_mutation=True)
        self.assertEqual(before, (root / "main.tex").read_bytes())
        sanitizer.assert_not_called()
        executor.assert_called_once()
        self.assertFalse(report["dry_run_source_mutation"])

    def test_legacy_executor_creates_real_restorable_snapshot_and_retains_freshness_guard(self):
        root = self.fixture()
        before = {path.name: path.read_bytes() for path in root.glob("*.tex")}
        with project_cwd(root), mock.patch.dict(os.environ, {}, clear=True):
            runtime = OrchestratorRuntime(state_path=str(root / "data/state.json"))
            runtime.init_task(main_tex="main.tex")
            plan = root / "data/repair_plan.json"
            plan.write_text(json.dumps({"candidates": []}), encoding="utf-8")
            attach_repair_plan_fingerprint(project_root=root, main_tex="main.tex", repair_plan_path=plan)
            result = paperfit_command._execute_repair_plan(root, main_tex=root / "main.tex")
            rollback = result["pre_repair_snapshot"]["rollback_target"]
            manifest = load_snapshot_manifest(root, rollback)
            self.assertEqual({entry["path"] for entry in manifest["files"]}, set(before))
            (root / "body.tex").write_text("Temporary later edit.\n", encoding="utf-8")
            restore_snapshot(project_root=root, rollback_target=rollback)
            self.assertEqual(before, {path.name: path.read_bytes() for path in root.glob("*.tex")})
            (root / "body.tex").write_text("Changed after plan generation.\n", encoding="utf-8")
            current = {path.name: path.read_bytes() for path in root.glob("*.tex")}
            result = paperfit_command._execute_repair_plan(root, main_tex=root / "main.tex")
            self.assertEqual(result["repair_execution_summary"]["status"], "blocked_stale_repair_plan")
            self.assertEqual(current, {path.name: path.read_bytes() for path in root.glob("*.tex")})

    def test_real_cli_unsupported_migration_returns_blocked_exit(self):
        root = self.fixture()
        env = {key: value for key, value in os.environ.items() if not key.endswith("API_KEY")}
        env.update(PAPERFIT_PYTHON=sys.executable, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
        cli = Path(__file__).resolve().parent.parent / "bin/paperfit.js"
        result = subprocess.run(["node", str(cli), "migrate-template", "NeurIPS2025",
                                 "--main", "main.tex", "--apply"], cwd=root, env=env,
                                capture_output=True, text=True, encoding="utf-8", timeout=45)
        self.assertEqual(result.returncode, 2, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["reason"], "template_migration_apply_requires_runtime_owned_migration_executor")

    def test_natural_language_source_task_exit_codes_and_read_only_entries(self):
        root = self.fixture()
        cases = [
            ({"runtime_contract": "full_vto", "run_result": {"status": "blocked"}}, 2),
            ({"runtime_contract": "repair_table", "run_result": {"status": "approval_required"}}, 3),
            ({"run_result": {"task": {"task_type": "adjust_length"}, "status": "continue"}}, 1),
            ({"runtime_contract": "template_migration", "status": "blocked"}, 2),
            ({"runtime_contract": "visual_only", "run_result": {"status": "blocked"}}, 0),
            ({"mode": "status_query", "status": "EVALUATING"}, 0),
            ({"mode": "priority_query", "status": "analysis"}, 0),
        ]
        for entry in ("slash", "run-agent"):
            for report, expected in cases:
                with self.subTest(entry=entry, report=report):
                    output = io.StringIO()
                    with (project_cwd(root),
                          mock.patch.object(sys, "argv", ["paperfit_command.py", entry, "request", "--main", "main.tex"]),
                          mock.patch.object(paperfit_command, "_handle_paperfit_request", return_value=report),
                          contextlib.redirect_stdout(output)):
                        try:
                            paperfit_command.main()
                            code = 0
                        except SystemExit as exc:
                            code = exc.code
                    self.assertEqual(code, expected)
                    self.assertEqual(json.loads(output.getvalue()), report)


if __name__ == "__main__":
    unittest.main()
