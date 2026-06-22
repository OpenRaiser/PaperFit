from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import hashlib


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import orchestrator_runtime  # noqa: E402
from orchestrator_runtime import OrchestratorRuntime  # noqa: E402
from runtime_types import TaskSpec  # noqa: E402


class RuntimeSourceChangingTest(unittest.TestCase):
    def test_full_vto_runtime_creates_snapshot_and_runs_one_repair_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "Source changing fixture.\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            pdf = root / "main.pdf"
            pdf.write_bytes(b"%PDF fixture\n")
            core_calls = {"count": 0}

            def fake_core(
                self: OrchestratorRuntime,
                *,
                emit_event: object | None = None,
                runtime_actions: dict[str, object] | None = None,
                **_: object,
            ) -> dict[str, object]:
                core_calls["count"] += 1
                page_dir = root / "data" / "pages"
                page_dir.mkdir(parents=True, exist_ok=True)
                visual_report = root / "data" / "visual_signal_report.json"
                defect_report = root / "data" / "defect_report.json"
                gatekeeper_report = root / "data" / "gatekeeper_decision.json"
                page = page_dir / "page_001.png"
                page.write_bytes(b"png")
                visual_report.write_text("{}", encoding="utf-8")
                defect_report.write_text("{}", encoding="utf-8")
                gatekeeper_report.write_text("{}", encoding="utf-8")
                base_mtime = 1_800_000_000 + core_calls["count"] * 10
                os.utime(pdf, (base_mtime, base_mtime))
                os.utime(page, (base_mtime + 1, base_mtime + 1))
                os.utime(visual_report, (base_mtime + 2, base_mtime + 2))
                os.utime(defect_report, (base_mtime + 3, base_mtime + 3))
                os.utime(gatekeeper_report, (base_mtime + 4, base_mtime + 4))
                if core_calls["count"] == 1:
                    self.manager.update(
                        {
                            "compile_success": True,
                            "page_images_rendered": True,
                            "last_gatekeeper_decision": "CONTINUE",
                            "defect_summary": {
                                "initial_total": 1,
                                "resolved": 0,
                                "remaining": 1,
                            },
                            "repair_plan_summary": {
                                "schema_version": "1.0",
                                "total_candidates": 1,
                                "top_candidates": [],
                                "updated_at": None,
                            },
                            "artifacts": {
                                "page_images_dir": "data/pages",
                                "visual_signal_report": "data/visual_signal_report.json",
                                "defect_report": "data/defect_report.json",
                                "gatekeeper_decision": "data/gatekeeper_decision.json",
                            },
                        }
                    )
                else:
                    self.manager.update(
                        {
                            "compile_success": True,
                            "page_images_rendered": True,
                            "last_gatekeeper_decision": "DONE",
                            "defect_summary": {
                                "initial_total": 1,
                                "resolved": 1,
                                "remaining": 0,
                            },
                            "repair_plan_summary": {
                                "schema_version": "1.0",
                                "total_candidates": 0,
                                "top_candidates": [],
                                "updated_at": None,
                            },
                            "artifacts": {
                                "page_images_dir": "data/pages",
                                "visual_signal_report": "data/visual_signal_report.json",
                                "defect_report": "data/defect_report.json",
                                "gatekeeper_decision": "data/gatekeeper_decision.json",
                            },
                        }
                    )
                self._record_runtime_action(
                    "gatekeeper_enforcer",
                    {
                        "success": True,
                        "source": "generated",
                        "output_path": "data/gatekeeper_decision.json",
                        "decision": self.manager.load()["last_gatekeeper_decision"],
                    },
                    phase="verify",
                    state="VERIFYING",
                    emit_event=emit_event if callable(emit_event) else None,
                    runtime_actions=runtime_actions,
                )
                return self.manager.load()

            def fake_execute(
                self: OrchestratorRuntime,
                *_: object,
                **__: object,
            ) -> dict[str, object]:
                main_tex.write_text(
                    "\\documentclass{article}\n"
                    "\\begin{document}\n"
                    "Mutated by fake repair.\n"
                    "\\end{document}\n",
                    encoding="utf-8",
                )
                self.manager.update(
                    {
                        "repair_execution_summary": {
                            "schema_version": "1.0",
                            "status": "applied",
                            "applied_count": 1,
                            "selected_candidates": [],
                            "updated_at": None,
                        }
                    }
                )
                return self.manager.load()

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
                    return_value={
                        "success": True,
                        "pdf_path": str(pdf),
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
                mock.patch.object(OrchestratorRuntime, "execute_repair_plan", fake_execute),
            ):
                result = OrchestratorRuntime(state_path=str(root / "data" / "state.json")).run_task(
                    task_spec=spec,
                    output_path="data/run_result_full_vto.json",
                )

            self.assertEqual(result["status"], "done")
            self.assertEqual(core_calls["count"], 2)
            self.assertTrue(result["runtime_actions"]["pre_repair_snapshot"]["success"])
            self.assertEqual(result["runtime_actions"]["repair_plan_executor"]["applied_count"], 1)
            self.assertEqual(result["runtime_actions"]["source_mutation_integrity"]["changed_files"], 1)
            self.assertEqual(result["approval"]["status"], "approved_and_executed")
            self.assertFalse(result["approval"]["requires_approval"])
            self.assertTrue(result["approval"]["approval_granted"])
            self.assertEqual(result["approval"]["execution"]["applied_count"], 1)
            policy = result["repair_loop_policy"]
            self.assertEqual(policy["execution_mode"], "report_only")
            self.assertEqual(policy["candidate_batch_limit"], 1)
            self.assertEqual(policy["applied_count"], 1)
            self.assertEqual(policy["stop_condition"], "done")
            self.assertFalse(policy["next_round_allowed"])
            self.assertEqual(policy["next_round_reason"], "gatekeeper_done")
            self.assertEqual(policy["approval_scope_carry_forward"]["status"], "pass")
            self.assertEqual(policy["second_round_apply_readiness"]["status"], "blocked")
            self.assertFalse(policy["second_round_apply_readiness"]["checks"]["runtime_execution_mode_can_auto_apply"])
            self.assertIn("round_artifact_lineage", policy)
            self.assertEqual(result["round_artifact_lineage"][0]["round"], 1)
            self.assertIn("repair_plan_executor", result["round_artifact_lineage"][0]["actions"])
            self.assertIn("source_mutation_integrity", result["round_artifact_lineage"][0]["actions"])
            self.assertIn("post_repair_observe", result["runtime_actions"])
            state = json.loads((root / "data" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["content_integrity"]["validation_status"], "mutation_reported")
            self.assertTrue((root / state["content_integrity"]["rollback_target"]).is_file())
            self.assertEqual(state["artifacts"]["source_mutation_report"], "data/source_mutation_report.json")

            main_tex.write_text("mutated after repair\n", encoding="utf-8")
            rollback_state = OrchestratorRuntime(state_path=str(root / "data" / "state.json")).rollback_to_snapshot(
                rollback_target=state["content_integrity"]["rollback_target"],
            )
            self.assertEqual(rollback_state["content_integrity"]["validation_status"], "rolled_back")
            self.assertEqual(rollback_state["content_integrity"]["action_taken"], "restore_snapshot")
            self.assertEqual(rollback_state["artifacts"]["rollback_report"], "data/rollback_report.json")
            self.assertIn("\\documentclass{article}", main_tex.read_text(encoding="utf-8"))
            self.assertTrue((root / "data" / "rollback_report.json").is_file())

    def test_full_vto_dry_run_does_not_execute_repair_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "Dry run fixture.\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            original_sha = hashlib.sha256(main_tex.read_bytes()).hexdigest()
            pdf = root / "main.pdf"
            pdf.write_bytes(b"%PDF fixture\n")

            def fake_core(
                self: OrchestratorRuntime,
                **_: object,
            ) -> dict[str, object]:
                self.manager.update(
                    {
                        "compile_success": True,
                        "page_images_rendered": True,
                        "last_gatekeeper_decision": "CONTINUE",
                        "defect_summary": {
                            "initial_total": 1,
                            "resolved": 0,
                            "remaining": 1,
                        },
                        "repair_plan_summary": {
                            "schema_version": "1.0",
                            "total_candidates": 1,
                            "top_candidates": [],
                            "updated_at": None,
                        },
                    }
                )
                return self.manager.load()

            spec = TaskSpec.from_dict(
                {
                    "task_type": "full_vto",
                    "project_root": str(root),
                    "main_tex": "main.tex",
                    "allow_source_mutation": True,
                    "pre_repair_snapshot_required": True,
                    "dry_run_source_mutation": True,
                    "rollback_policy": "required",
                }
            )

            with (
                mock.patch.object(
                    orchestrator_runtime,
                    "compile_latex",
                    return_value={
                        "success": True,
                        "pdf_path": str(pdf),
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
                mock.patch.object(
                    OrchestratorRuntime,
                    "execute_repair_plan",
                    side_effect=AssertionError("dry-run must not execute repair plan"),
                ),
            ):
                result = OrchestratorRuntime(state_path=str(root / "data" / "state.json")).run_task(
                    task_spec=spec,
                    output_path="data/run_result_full_vto_dry_run.json",
                )

            self.assertEqual(hashlib.sha256(main_tex.read_bytes()).hexdigest(), original_sha)
            action = result["runtime_actions"]["repair_plan_executor"]
            self.assertTrue(action["skipped"])
            self.assertEqual(action["reason"], "dry_run_source_mutation")
            self.assertEqual(action["action_name"], "repair_plan_executor")
            self.assertEqual(action["phase"], "repair")
            self.assertEqual(action["runtime_state"], "REPAIRING")
            self.assertEqual(action["risk_level"], "high")
            self.assertTrue(action["requires_approval"])
            self.assertEqual(action["input_artifacts"]["main_tex"], "main.tex")
            self.assertTrue(action["input_artifacts"]["rollback_target"].endswith("snapshot_manifest.json"))
            self.assertEqual(action["output_artifacts"], {})
            self.assertEqual(result["approval"]["status"], "approval_required")
            self.assertTrue(result["approval"]["requires_approval"])
            self.assertFalse(result["approval"]["approval_granted"])
            self.assertEqual(result["approval"]["reason"], "dry_run_source_mutation")
            self.assertEqual(result["approval"]["plan"]["candidates"], 1)
            self.assertIn("--apply", result["approval"]["approval_mechanisms"])
            policy = result["repair_loop_policy"]
            self.assertEqual(policy["execution_mode"], "report_only")
            self.assertEqual(policy["round_limit"], spec.max_rounds)
            self.assertEqual(policy["candidate_batch_limit"], 0)
            self.assertEqual(policy["stop_condition"], "approval_required")
            self.assertFalse(policy["next_round_allowed"])
            self.assertEqual(policy["next_round_reason"], "dry_run_source_mutation")
            self.assertEqual(policy["approval_scope_carry_forward"]["status"], "pass")
            self.assertEqual(policy["second_round_apply_readiness"]["status"], "blocked")
            self.assertFalse(policy["second_round_apply_readiness"]["checks"]["source_mutation_executed"])
            self.assertEqual(result["round_artifact_lineage"][0]["round"], 1)
            self.assertIn("repair_plan_executor", result["round_artifact_lineage"][0]["actions"])
            self.assertNotIn("post_repair_observe", result["runtime_actions"])
            event_log = root / result["event_log"]
            events = [
                json.loads(line)
                for line in event_log.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            states = [event.get("state") for event in events]
            for expected in ["READY", "OBSERVING", "DIAGNOSING", "PLANNING", "REPAIRING", "VERIFYING", "CONTINUE"]:
                self.assertIn(expected, states)
            self.assertEqual(events[-1]["type"], "task_completed")
            self.assertEqual(events[-1]["state"], "CONTINUE")
            state = json.loads((root / "data" / "state.json").read_text(encoding="utf-8"))
            event_action = state["runtime_event_summary"]["actions"]["repair_plan_executor"]
            self.assertEqual(event_action["input_artifacts"]["main_tex"], "main.tex")
            self.assertTrue(event_action["input_artifacts"]["rollback_target"].endswith("snapshot_manifest.json"))

    def test_source_changing_run_result_reports_loop_policy_without_widening_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "Loop policy fixture.\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            pdf = root / "main.pdf"
            pdf.write_bytes(b"%PDF fixture\n")

            def fake_core(
                self: OrchestratorRuntime,
                **_: object,
            ) -> dict[str, object]:
                self.manager.update(
                    {
                        "compile_success": True,
                        "page_images_rendered": True,
                        "last_gatekeeper_decision": "CONTINUE",
                        "defect_summary": {
                            "initial_total": 2,
                            "resolved": 1,
                            "remaining": 1,
                        },
                        "repair_plan_summary": {
                            "schema_version": "1.0",
                            "total_candidates": 2,
                            "top_candidates": [],
                            "updated_at": None,
                        },
                    }
                )
                return self.manager.load()

            spec = TaskSpec.from_dict(
                {
                    "task_type": "full_vto",
                    "project_root": str(root),
                    "main_tex": "main.tex",
                    "allow_source_mutation": True,
                    "pre_repair_snapshot_required": True,
                    "dry_run_source_mutation": True,
                    "rollback_policy": "required",
                    "max_rounds": 3,
                }
            )

            with (
                mock.patch.object(
                    orchestrator_runtime,
                    "compile_latex",
                    return_value={
                        "success": True,
                        "pdf_path": str(pdf),
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
                    output_path="data/run_result_full_vto_dry_run.json",
                )

            policy = result["repair_loop_policy"]
            self.assertEqual(policy["execution_mode"], "report_only")
            self.assertEqual(policy["round_limit"], 3)
            self.assertEqual(policy["current_round"], 1)
            self.assertEqual(policy["candidate_batch_limit"], 0)
            self.assertEqual(policy["plan_candidates"], 2)
            self.assertFalse(policy["next_round_allowed"])
            self.assertEqual(policy["next_round_reason"], "dry_run_source_mutation")
            self.assertEqual(policy["approval_scope_carry_forward"]["status"], "pass")
            self.assertEqual(policy["second_round_apply_readiness"]["status"], "blocked")
            self.assertIn("repair_plan_executor", policy["round_artifact_lineage"][0]["actions"])


if __name__ == "__main__":
    unittest.main()
