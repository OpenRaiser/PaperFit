from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.runtime_status import build_runtime_status


class RuntimeStatusTest(unittest.TestCase):
    def test_explicit_run_result_overrides_shared_gatekeeper_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "EVALUATING",
                "last_gatekeeper_decision": "CONTINUE",
                "task": {"type": "full_vto"},
                "defect_summary": {"remaining": 1},
                "artifacts": {"gatekeeper_decision": "data/gatekeeper_decision.json"},
            }
            run_result = {
                "run_id": "blocked-run",
                "status": "blocked",
                "gatekeeper_decision": "BLOCKED",
                "defect_summary": {"remaining": 7},
                "failure": {"failure_type": "post_repair_hard_guard_failed"},
            }
            shared_gatekeeper = {
                "decision": "CONTINUE",
                "remaining_defects": [{"id": "newer", "severity": "major"}],
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            (root / "data" / "run_result_blocked.json").write_text(json.dumps(run_result), encoding="utf-8")
            (root / "data" / "gatekeeper_decision.json").write_text(json.dumps(shared_gatekeeper), encoding="utf-8")

            status = build_runtime_status(
                project_root=root,
                run_result_path="data/run_result_blocked.json",
            )

            self.assertEqual(status["status"], "BLOCKED")
            self.assertEqual(status["gatekeeper_decision"], "BLOCKED")
            self.assertEqual(status["gatekeeper"]["decision"], "BLOCKED")
            self.assertEqual(status["gatekeeper"]["remaining_defects"], [])
            self.assertEqual(status["defect_summary"]["remaining"], 7)
            self.assertEqual(status["runtime"]["run_id"], "blocked-run")
            self.assertFalse(status["status_consistency"]["gatekeeper_artifact_matches_run_result"])

    def test_status_view_summarizes_state_event_projection_and_repair_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "EVALUATING",
                "last_gatekeeper_decision": "CONTINUE",
                "task": {"type": "visual_only"},
                "defect_summary": {"initial_total": 3, "resolved": 1, "remaining": 2},
                "runtime_event_summary": {
                    "run_id": "run1",
                    "event_log": "data/events/run1.ndjson",
                    "event_count": 7,
                    "last_event_type": "task_completed",
                    "last_phase": None,
                    "last_runtime_state": "CONTINUE",
                    "actions": {"compile": {"success": True}},
                },
                "artifacts": {
                    "task_spec": "data/task.json",
                    "page_images_dir": "data/pages",
                    "visual_signal_report": "data/visual_signal_report.json",
                    "defect_report": "data/defect_report.json",
                    "repair_plan": "data/repair_plan.json",
                    "repair_execution_report": None,
                    "rollback_report": None,
                },
                "repair_plan_summary": {
                    "total_candidates": 4,
                    "immutability_policy": "invalidate_on_source_change",
                    "source_fingerprint_sha256": "abc123",
                },
                "repair_execution_summary": {
                    "status": None,
                    "applied_count": 0,
                    "selected_candidates": [
                        {
                            "defect_id": "B2",
                            "object": "wide-results",
                            "page": 3,
                            "object_kind": "table_like",
                            "visual_width_subtype": "overflow_width",
                        }
                    ],
                    "b2_width_selected_candidates": {
                        "total": 1,
                        "by_subtype": {"overflow_width": 1},
                        "by_object_kind": {"table_like": 1},
                        "labels": ["wide-results"],
                        "objects": ["wide-results"],
                        "pages": [3],
                    },
                },
                "content_integrity": {
                    "validation_status": "snapshot_created",
                    "rollback_target": "data/snapshots/s1/snapshot_manifest.json",
                },
                "next_actions": ["Review defects"],
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            run_result = {
                "run_id": "run1",
                "gatekeeper_decision": "CONTINUE",
                "event_log": "data/events/run1.ndjson",
                "artifact_manifest": {
                    "freshness": {
                        "status": "pass",
                        "blocking_checks": [],
                    }
                },
            }
            (root / "data" / "run_result_check_visual.json").write_text(json.dumps(run_result), encoding="utf-8")

            status = build_runtime_status(project_root=root)

            self.assertEqual(status["main_tex"], "main.tex")
            self.assertEqual(status["task"]["type"], "visual_only")
            self.assertIsNone(status["task"]["target_pages"])
            self.assertEqual(status["runtime"]["event_count"], 7)
            self.assertTrue(status["runtime"]["actions"]["compile"]["success"])
            self.assertEqual(status["repair"]["plan_candidates"], 4)
            self.assertEqual(status["repair"]["plan_immutability_policy"], "invalidate_on_source_change")
            self.assertEqual(status["repair"]["selected_candidates"][0]["object"], "wide-results")
            self.assertEqual(status["repair"]["selected_candidates"][0]["object_kind"], "table_like")
            self.assertEqual(status["repair"]["selected_candidates"][0]["visual_width_subtype"], "overflow_width")
            self.assertEqual(status["repair"]["b2_width_selected_candidates"]["total"], 1)
            self.assertEqual(status["repair"]["b2_width_selected_candidates"]["labels"], ["wide-results"])
            self.assertEqual(status["artifact_freshness"]["status"], "pass")
            self.assertEqual(status["next_actions"], ["Review defects"])

    def test_status_view_discovers_full_vto_dry_run_result_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "EVALUATING",
                "task": {"type": "full_vto", "target_pages": 9, "page_budget_scope": None},
                "runtime_event_summary": {
                    "run_id": "run-dry",
                    "event_log": "data/events/run-dry.ndjson",
                    "event_count": 5,
                },
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            run_result = {
                "run_id": "run-dry",
                "gatekeeper_decision": "CONTINUE",
                "event_log": "data/events/run-dry.ndjson",
                "artifact_manifest": {
                    "freshness": {
                        "status": "pass",
                        "blocking_checks": [],
                    }
                },
            }
            (root / "data" / "run_result_full_vto_dry_run.json").write_text(json.dumps(run_result), encoding="utf-8")

            status = build_runtime_status(project_root=root)

            self.assertEqual(status["run_result_path"], "data/run_result_full_vto_dry_run.json")
            self.assertEqual(status["runtime"]["run_id"], "run-dry")
            self.assertEqual(status["gatekeeper_decision"], "CONTINUE")
            self.assertEqual(status["artifact_freshness"]["status"], "pass")

    def test_status_view_surfaces_gatekeeper_reasons_and_remaining_defects(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "EVALUATING",
                "last_gatekeeper_decision": "CONTINUE",
                "task": {"type": "full_vto", "target_pages": 9, "page_budget_scope": None},
                "runtime_event_summary": {"run_id": "run-page-budget", "event_count": 3},
                "artifacts": {"gatekeeper_decision": "data/gatekeeper_decision.json"},
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            gatekeeper = {
                "decision": "CONTINUE",
                "reasons": ["category A blocking defects remaining"],
                "remaining_defects": [
                    {
                        "id": "rule:underfull-1",
                        "defect_family": "underfull_hbox",
                        "severity": "minor",
                        "description": "in paragraph",
                    },
                    {
                        "id": "rule:underfull-2",
                        "defect_family": "underfull_hbox",
                        "severity": "minor",
                        "description": "in paragraph",
                    },
                    {
                        "id": "rule:underfull-3",
                        "defect_family": "underfull_hbox",
                        "severity": "minor",
                        "description": "in paragraph",
                    },
                    {
                        "id": "rule:underfull-4",
                        "defect_family": "underfull_hbox",
                        "severity": "minor",
                        "description": "in paragraph",
                    },
                    {
                        "id": "rule:underfull-5",
                        "defect_family": "underfull_hbox",
                        "severity": "minor",
                        "description": "in paragraph",
                    },
                    {
                        "id": "system:page_budget_violation",
                        "defect_family": "A3",
                        "category": "A",
                        "severity": "critical",
                        "page": 11,
                        "description": "Page budget violation: current_pages=11, target_pages=9",
                    }
                ],
            }
            (root / "data" / "gatekeeper_decision.json").write_text(
                json.dumps(gatekeeper),
                encoding="utf-8",
            )

            status = build_runtime_status(project_root=root)

            self.assertEqual(status["gatekeeper"]["decision"], "CONTINUE")
            self.assertEqual(status["gatekeeper"]["reasons"], ["category A blocking defects remaining"])
            self.assertEqual(status["gatekeeper"]["remaining_defects"][0]["defect_family"], "A3")
            self.assertEqual(status["artifacts"]["gatekeeper_decision"], "data/gatekeeper_decision.json")

    def test_status_view_surfaces_visual_b2_width_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "EVALUATING",
                "task": {"type": "full_vto"},
                "artifacts": {"visual_signal_report": "data/visual_signal_report.json"},
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            visual_report = {
                "summary": {
                    "total_findings": 3,
                    "b2_width_findings": {
                        "total": 2,
                        "by_subtype": {"overflow_width": 1, "underfilled_width": 1},
                        "by_object_kind": {"figure_like": 1, "table_like": 1},
                        "pages": [3, 4],
                        "finding_ids": ["B2-native-figure-overflow-width", "B2-native-table-underfilled-width"],
                    },
                }
            }
            (root / "data" / "visual_signal_report.json").write_text(
                json.dumps(visual_report),
                encoding="utf-8",
            )

            status = build_runtime_status(project_root=root)

            self.assertEqual(status["visual"]["total_findings"], 3)
            self.assertEqual(status["visual"]["b2_width_findings"]["total"], 2)
            self.assertEqual(
                status["visual"]["b2_width_findings"]["by_subtype"],
                {"overflow_width": 1, "underfilled_width": 1},
            )
            self.assertEqual(status["visual"]["b2_width_findings"]["pages"], [3, 4])

    def test_status_view_surfaces_repair_b2_width_candidate_summary_from_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "EVALUATING",
                "task": {"type": "full_vto"},
                "repair_plan_summary": {"total_candidates": 2},
                "artifacts": {"repair_plan": "data/repair_plan.json"},
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            repair_plan = {
                "summary": {
                    "total_candidates": 2,
                    "b2_width_findings": {
                        "total": 2,
                        "by_subtype": {"overflow_width": 2},
                        "by_object_kind": {"figure_like": 2},
                        "pages": [3],
                        "finding_ids": ["B2-native-figure-overflow-width"],
                    },
                    "b2_width_candidates": {
                        "total": 1,
                        "by_subtype": {"overflow_width": 1},
                        "by_object_kind": {"figure_like": 1},
                        "labels": ["fig:wide"],
                    },
                    "b2_width_targetable_candidates": {
                        "total": 1,
                        "by_subtype": {"overflow_width": 1},
                        "by_object_kind": {"figure_like": 1},
                        "labels": ["fig:wide"],
                    },
                    "b2_width_untargetable_candidates": {
                        "total": 1,
                        "by_subtype": {"underfilled_width": 1},
                        "by_reason": {"unsupported_label_or_object_kind": 1},
                        "labels": ["wide-only"],
                    },
                    "b2_width_unmatched_findings": 1,
                    "b2_width_unmatched_pages": [4],
                    "b2_width_unmatched_finding_ids": ["B2-native-table-underfilled-width"],
                },
                "candidates": [],
            }
            (root / "data" / "repair_plan.json").write_text(
                json.dumps(repair_plan),
                encoding="utf-8",
            )

            status = build_runtime_status(project_root=root)

            self.assertEqual(status["repair"]["plan_candidates"], 2)
            self.assertEqual(status["repair"]["b2_width_findings"]["total"], 2)
            self.assertEqual(status["repair"]["b2_width_findings"]["pages"], [3])
            self.assertEqual(status["repair"]["b2_width_candidates"]["total"], 1)
            self.assertEqual(status["repair"]["b2_width_candidates"]["labels"], ["fig:wide"])
            self.assertEqual(status["repair"]["b2_width_targetable_candidates"]["total"], 1)
            self.assertEqual(status["repair"]["b2_width_targetable_candidates"]["labels"], ["fig:wide"])
            self.assertEqual(status["repair"]["b2_width_untargetable_candidates"]["total"], 1)
            self.assertEqual(
                status["repair"]["b2_width_untargetable_candidates"]["by_reason"],
                {"unsupported_label_or_object_kind": 1},
            )
            self.assertEqual(status["repair"]["b2_width_unmatched_findings"], 1)
            self.assertEqual(status["repair"]["b2_width_unmatched_pages"], [4])
            self.assertEqual(
                status["repair"]["b2_width_unmatched_finding_ids"],
                ["B2-native-table-underfilled-width"],
            )

    def test_status_view_uses_repair_plan_summary_when_state_summary_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "EVALUATING",
                "task": {"type": "full_vto"},
                "artifacts": {"repair_plan": "data/repair_plan.json"},
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            repair_plan = {
                "summary": {
                    "total_candidates": 3,
                    "b2_width_candidates": {
                        "total": 2,
                        "by_subtype": {"overflow_width": 2},
                        "labels": ["wide-results", "untargeted"],
                    },
                    "b2_width_targetable_candidates": {
                        "total": 1,
                        "by_subtype": {"overflow_width": 1},
                        "labels": ["wide-results"],
                    },
                    "b2_width_unmatched_findings": 1,
                    "b2_width_unmatched_pages": [3],
                    "b2_width_unmatched_finding_ids": ["B2-native-figure-overflow-width"],
                },
                "candidates": [],
            }
            (root / "data" / "repair_plan.json").write_text(
                json.dumps(repair_plan),
                encoding="utf-8",
            )

            status = build_runtime_status(project_root=root)

            self.assertEqual(status["repair"]["plan_candidates"], 3)
            self.assertEqual(status["repair"]["b2_width_candidates"]["total"], 2)
            self.assertEqual(status["repair"]["b2_width_targetable_candidates"]["total"], 1)
            self.assertEqual(status["repair"]["b2_width_targetable_candidates"]["labels"], ["wide-results"])
            self.assertEqual(status["repair"]["b2_width_unmatched_pages"], [3])

    def test_status_view_preserves_explicit_empty_b2_unmatched_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "EVALUATING",
                "task": {"type": "full_vto"},
                "repair_plan_summary": {
                    "total_candidates": 1,
                    "b2_width_findings": {"total": 1},
                    "b2_width_candidates": {"total": 1, "labels": ["fig:wide"]},
                    "b2_width_unmatched_findings": 0,
                    "b2_width_unmatched_pages": [],
                    "b2_width_unmatched_finding_ids": [],
                },
                "artifacts": {"repair_plan": "data/repair_plan.json"},
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            stale_repair_plan = {
                "summary": {
                    "total_candidates": 1,
                    "b2_width_unmatched_findings": 1,
                    "b2_width_unmatched_pages": [4],
                    "b2_width_unmatched_finding_ids": ["B2-native-table-underfilled-width"],
                },
                "candidates": [],
            }
            (root / "data" / "repair_plan.json").write_text(
                json.dumps(stale_repair_plan),
                encoding="utf-8",
            )

            status = build_runtime_status(project_root=root)

            self.assertEqual(status["repair"]["b2_width_unmatched_findings"], 0)
            self.assertEqual(status["repair"]["b2_width_unmatched_pages"], [])
            self.assertEqual(status["repair"]["b2_width_unmatched_finding_ids"], [])

    def test_status_view_prefers_gatekeeper_artifact_decision_over_stale_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "DONE",
                "last_gatekeeper_decision": "DONE",
                "task": {"type": "full_vto", "target_pages": 9, "page_budget_scope": None},
                "artifacts": {"gatekeeper_decision": "data/gatekeeper_decision.json"},
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            gatekeeper = {
                "decision": "CONTINUE",
                "reasons": ["category A blocking defects remaining"],
                "remaining_defects": [
                    {
                        "id": "rule:underfull-1",
                        "defect_family": "underfull_hbox",
                        "severity": "minor",
                        "description": "in paragraph",
                    },
                    {
                        "id": "rule:underfull-2",
                        "defect_family": "underfull_hbox",
                        "severity": "minor",
                        "description": "in paragraph",
                    },
                    {
                        "id": "rule:underfull-3",
                        "defect_family": "underfull_hbox",
                        "severity": "minor",
                        "description": "in paragraph",
                    },
                    {
                        "id": "rule:underfull-4",
                        "defect_family": "underfull_hbox",
                        "severity": "minor",
                        "description": "in paragraph",
                    },
                    {
                        "id": "rule:underfull-5",
                        "defect_family": "underfull_hbox",
                        "severity": "minor",
                        "description": "in paragraph",
                    },
                    {
                        "id": "system:page_budget_violation",
                        "defect_family": "A3",
                        "severity": "critical",
                        "page": 11,
                        "description": "Page budget violation: current_pages=11, target_pages=9",
                    }
                ],
            }
            (root / "data" / "gatekeeper_decision.json").write_text(
                json.dumps(gatekeeper),
                encoding="utf-8",
            )

            status = build_runtime_status(project_root=root)

            self.assertEqual(status["status"], "EVALUATING")
            self.assertEqual(status["gatekeeper_decision"], "CONTINUE")
            self.assertEqual(status["gatekeeper"]["decision"], "CONTINUE")
            self.assertEqual(status["task"]["target_pages"], 9)
            self.assertIsNone(status["task"]["page_budget_scope"])
            self.assertTrue(status["status_consistency"]["stale_state_overridden"])
            self.assertEqual(
                status["status_consistency"]["reason"],
                "gatekeeper_artifact_overrides_stale_runtime_state",
            )
            self.assertEqual(
                status["status_consistency"]["stale_sections"],
                ["runtime.last_runtime_state", "repair_loop_policy"],
            )

    def test_status_view_discovers_template_migration_result_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "EVALUATING",
                "task": {"type": "template_migration"},
                "runtime_event_summary": {
                    "run_id": "run-template",
                    "event_log": "data/events/run-template.ndjson",
                    "event_count": 7,
                },
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            check_visual_result = {
                "run_id": "run-check",
                "artifact_manifest": {"freshness": {"status": "unknown"}},
            }
            template_result = {
                "run_id": "run-template",
                "task": {"task_type": "template_migration"},
                "gatekeeper_decision": "CONTINUE",
                "event_log": "data/events/run-template.ndjson",
                "artifact_manifest": {
                    "freshness": {
                        "status": "pass",
                        "blocking_checks": [],
                    }
                },
            }
            (root / "data" / "run_result_check_visual.json").write_text(
                json.dumps(check_visual_result),
                encoding="utf-8",
            )
            (root / "data" / "run_result_template_migration.json").write_text(
                json.dumps(template_result),
                encoding="utf-8",
            )

            status = build_runtime_status(project_root=root)

            self.assertEqual(status["run_result_path"], "data/run_result_template_migration.json")
            self.assertEqual(status["runtime"]["run_id"], "run-template")
            self.assertEqual(status["gatekeeper_decision"], "CONTINUE")
            self.assertEqual(status["artifact_freshness"]["status"], "pass")

    def test_status_view_reports_blocked_template_migration_apply_approval_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            reason = "template_migration_apply_requires_runtime_owned_migration_executor"
            state = {
                "main_tex": "main.tex",
                "status": "BLOCKED",
                "last_gatekeeper_decision": "BLOCKED",
                "task": {"type": "template_migration"},
                "next_actions": [
                    "Run template migration without --apply to inspect the dry-run plan and approval state",
                    "Add a runtime-owned template migration candidate executor before enabling direct template apply",
                ],
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            run_result = {
                "run_id": "template_migration_apply_blocked",
                "task": {
                    "task_type": "template_migration",
                    "dry_run_source_mutation": True,
                    "max_rounds": 3,
                },
                "status": "blocked",
                "gatekeeper_decision": "BLOCKED",
                "runtime_actions": {
                    "repair_plan_executor": {
                        "success": False,
                        "skipped": True,
                        "reason": reason,
                        "requires_approval": True,
                        "risk_level": "high",
                        "planned_candidates": 1,
                        "input_artifacts": {
                            "main_tex": "main.tex",
                            "target_template": "CVPR2026",
                        },
                        "output_artifacts": {},
                    },
                    "template_migration_apply_gate": {
                        "success": False,
                        "skipped": True,
                        "reason": reason,
                        "requires_approval": True,
                        "risk_level": "high",
                    },
                },
                "artifact_manifest": {
                    "freshness": {
                        "status": "unknown",
                        "blocking_checks": ["template_migration_apply_blocked_before_runtime"],
                    }
                },
                "failure": {
                    "failure_type": reason,
                    "reason": reason,
                    "next_actions": state["next_actions"],
                },
            }
            (root / "data" / "run_result_template_migration.json").write_text(
                json.dumps(run_result),
                encoding="utf-8",
            )

            status = build_runtime_status(project_root=root)

            self.assertEqual(status["run_result_path"], "data/run_result_template_migration.json")
            self.assertEqual(status["repair"]["skip_reason"], reason)
            self.assertEqual(status["repair"]["risk_level"], "high")
            self.assertTrue(status["repair"]["requires_approval"])
            self.assertEqual(status["approval"]["status"], "approval_required")
            self.assertTrue(status["approval"]["requires_approval"])
            self.assertFalse(status["approval"]["approval_granted"])
            self.assertEqual(status["approval"]["reason"], reason)
            self.assertEqual(status["approval"]["policy"]["approval_scope"], "template_migration")
            self.assertIn("template_migration", status["approval"]["policy"]["high_risk_operations"])
            self.assertEqual(status["next_actions"], state["next_actions"])

    def test_status_view_prefers_full_vto_result_over_check_visual_for_source_changing_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "EVALUATING",
                "task": {"type": "full_vto"},
                "runtime_event_summary": {"run_id": "run-dry", "event_count": 5},
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            check_visual_result = {
                "run_id": "run-check",
                "artifact_manifest": {"freshness": {"status": "unknown"}},
            }
            dry_run_result = {
                "run_id": "run-dry",
                "artifact_manifest": {"freshness": {"status": "pass", "blocking_checks": []}},
            }
            (root / "data" / "run_result_check_visual.json").write_text(
                json.dumps(check_visual_result),
                encoding="utf-8",
            )
            (root / "data" / "run_result_full_vto_dry_run.json").write_text(
                json.dumps(dry_run_result),
                encoding="utf-8",
            )

            status = build_runtime_status(project_root=root)

            self.assertEqual(status["run_result_path"], "data/run_result_full_vto_dry_run.json")
            self.assertEqual(status["runtime"]["run_id"], "run-dry")
            self.assertEqual(status["artifact_freshness"]["status"], "pass")

    def test_status_view_discovers_agent_result_and_reports_repair_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "EVALUATING",
                "task": {"type": "full_vto"},
                "runtime_event_summary": {"run_id": "run-agent", "event_count": 8},
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            agent_result = {
                "run_id": "run-agent",
                "runtime_actions": {
                    "repair_plan_executor": {
                        "success": True,
                        "skipped": True,
                        "reason": "dry_run_source_mutation",
                        "risk_level": "high",
                        "requires_approval": True,
                    }
                },
                "artifact_manifest": {"freshness": {"status": "pass", "blocking_checks": []}},
            }
            (root / "data" / "run_result_agent.json").write_text(
                json.dumps(agent_result),
                encoding="utf-8",
            )

            status = build_runtime_status(project_root=root)

            self.assertEqual(status["run_result_path"], "data/run_result_agent.json")
            self.assertEqual(status["runtime"]["run_id"], "run-agent")
            self.assertTrue(status["repair"]["skipped"])
            self.assertEqual(status["repair"]["skip_reason"], "dry_run_source_mutation")
            self.assertEqual(status["repair"]["risk_level"], "high")
            self.assertTrue(status["repair"]["requires_approval"])
            self.assertEqual(status["approval"]["status"], "approval_required")
            self.assertTrue(status["approval"]["requires_approval"])
            self.assertFalse(status["approval"]["approval_granted"])
            self.assertEqual(status["approval"]["reason"], "dry_run_source_mutation")
            self.assertIn("--apply", status["approval"]["approval_mechanisms"])

    def test_status_view_surfaces_repair_loop_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "EVALUATING",
                "task": {"type": "full_vto"},
                "runtime_event_summary": {"run_id": "run-agent", "event_count": 8},
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            agent_result = {
                "run_id": "run-agent",
                "repair_loop_policy": {
                    "schema_version": "1.0",
                    "execution_mode": "report_only",
                    "round_limit": 1,
                    "current_round": 1,
                    "candidate_batch_limit": 0,
                    "stop_condition": "approval_required",
                    "next_round_allowed": False,
                    "next_round_reason": "dry_run_source_mutation",
                },
                "round_artifact_lineage": [
                    {
                        "schema_version": "1.0",
                        "round": 1,
                        "actions": {
                            "repair_plan_executor": {
                                "phase": "repair",
                                "input_artifacts": {"repair_plan": "data/repair_plan.json"},
                                "output_artifacts": {},
                            }
                        },
                    }
                ],
                "artifact_manifest": {"freshness": {"status": "pass", "blocking_checks": []}},
            }
            (root / "data" / "run_result_agent.json").write_text(
                json.dumps(agent_result),
                encoding="utf-8",
            )

            status = build_runtime_status(project_root=root)

            policy = status["repair_loop_policy"]
            self.assertEqual(policy["execution_mode"], "report_only")
            self.assertEqual(policy["round_limit"], 1)
            self.assertFalse(policy["next_round_allowed"])
            self.assertEqual(policy["next_round_reason"], "dry_run_source_mutation")
            self.assertEqual(status["round_artifact_lineage"][0]["round"], 1)
            self.assertIn("repair_plan_executor", status["round_artifact_lineage"][0]["actions"])

    def test_status_view_surfaces_repair_loop_freshness_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "BLOCKED",
                "last_gatekeeper_decision": "DONE",
                "task": {"type": "full_vto"},
                "runtime_event_summary": {"run_id": "run-agent", "event_count": 9},
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            agent_result = {
                "run_id": "run-agent",
                "status": "blocked",
                "gatekeeper_decision": "DONE",
                "repair_loop_policy": {
                    "schema_version": "1.0",
                    "execution_mode": "report_only",
                    "round_limit": 2,
                    "current_round": 1,
                    "stop_condition": "artifact_freshness_not_pass",
                    "next_round_allowed": False,
                    "next_round_reason": "artifact_freshness_not_pass",
                    "second_round_apply_readiness": {
                        "schema_version": "1.0",
                        "status": "blocked",
                        "reason": "artifact_freshness_not_pass",
                        "checks": {
                            "artifact_freshness_pass": False,
                            "runtime_execution_mode_can_auto_apply": False,
                        },
                    },
                },
                "artifact_manifest": {
                    "freshness": {
                        "status": "stale_or_missing",
                        "blocking_checks": ["pdf_exists"],
                    }
                },
                "failure": {
                    "failure_type": "terminal_success_without_fresh_visual_evidence",
                    "reason": "gatekeeper_done_but_artifact_freshness_failed",
                    "artifact_freshness": {
                        "status": "stale_or_missing",
                        "blocking_checks": ["pdf_exists"],
                    },
                },
            }
            (root / "data" / "run_result_agent.json").write_text(
                json.dumps(agent_result),
                encoding="utf-8",
            )

            status = build_runtime_status(project_root=root)

            policy = status["repair_loop_policy"]
            self.assertEqual(status["run_result_path"], "data/run_result_agent.json")
            self.assertEqual(status["artifact_freshness"]["status"], "stale_or_missing")
            self.assertEqual(status["artifact_freshness"]["blocking_checks"], ["pdf_exists"])
            self.assertEqual(policy["stop_condition"], "artifact_freshness_not_pass")
            self.assertEqual(policy["next_round_reason"], "artifact_freshness_not_pass")
            self.assertFalse(policy["next_round_allowed"])
            self.assertFalse(
                policy["second_round_apply_readiness"]["checks"]["artifact_freshness_pass"]
            )
            self.assertEqual(
                status["terminal_success_guard"]["failure_type"],
                "terminal_success_without_fresh_visual_evidence",
            )

    def test_top_level_status_view_cli_uses_runtime_status_contract(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        cli = repo_root / "bin" / "paperfit.js"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "EVALUATING",
                "task": {"type": "full_vto"},
                "runtime_event_summary": {"run_id": "run-agent", "event_count": 8},
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            agent_result = {
                "run_id": "run-agent",
                "runtime_actions": {
                    "repair_plan_executor": {
                        "success": True,
                        "skipped": True,
                        "reason": "dry_run_source_mutation",
                        "risk_level": "high",
                        "requires_approval": True,
                    }
                },
                "artifact_manifest": {"freshness": {"status": "pass", "blocking_checks": []}},
            }
            (root / "data" / "run_result_agent.json").write_text(
                json.dumps(agent_result),
                encoding="utf-8",
            )

            result = subprocess.run(
                ["node", str(cli), "status-view"],
                cwd=root,
                check=True,
                text=True,
                capture_output=True,
            )
            status = json.loads(result.stdout)

            self.assertEqual(status["run_result_path"], "data/run_result_agent.json")
            self.assertEqual(status["runtime"]["run_id"], "run-agent")
            self.assertEqual(status["approval"]["status"], "approval_required")
            self.assertTrue(status["repair"]["requires_approval"])

    def test_top_level_status_view_cli_surfaces_freshness_block(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        cli = repo_root / "bin" / "paperfit.js"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "BLOCKED",
                "last_gatekeeper_decision": "DONE",
                "task": {"type": "full_vto"},
                "runtime_event_summary": {"run_id": "run-agent", "event_count": 9},
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            agent_result = {
                "run_id": "run-agent",
                "status": "blocked",
                "gatekeeper_decision": "DONE",
                "repair_loop_policy": {
                    "schema_version": "1.0",
                    "execution_mode": "report_only",
                    "stop_condition": "artifact_freshness_not_pass",
                    "next_round_allowed": False,
                    "next_round_reason": "artifact_freshness_not_pass",
                    "second_round_apply_readiness": {
                        "schema_version": "1.0",
                        "status": "blocked",
                        "reason": "artifact_freshness_not_pass",
                        "checks": {"artifact_freshness_pass": False},
                    },
                },
                "artifact_manifest": {
                    "freshness": {
                        "status": "stale_or_missing",
                        "blocking_checks": ["pdf_exists"],
                    }
                },
                "failure": {
                    "failure_type": "terminal_success_without_fresh_visual_evidence",
                    "reason": "gatekeeper_done_but_artifact_freshness_failed",
                    "artifact_freshness": {
                        "status": "stale_or_missing",
                        "blocking_checks": ["pdf_exists"],
                    },
                },
            }
            (root / "data" / "run_result_agent.json").write_text(
                json.dumps(agent_result),
                encoding="utf-8",
            )

            result = subprocess.run(
                ["node", str(cli), "status-view"],
                cwd=root,
                check=True,
                text=True,
                capture_output=True,
            )
            status = json.loads(result.stdout)

            policy = status["repair_loop_policy"]
            self.assertEqual(status["run_result_path"], "data/run_result_agent.json")
            self.assertEqual(status["artifact_freshness"]["status"], "stale_or_missing")
            self.assertEqual(status["artifact_freshness"]["blocking_checks"], ["pdf_exists"])
            self.assertEqual(policy["stop_condition"], "artifact_freshness_not_pass")
            self.assertEqual(policy["next_round_reason"], "artifact_freshness_not_pass")
            self.assertFalse(policy["next_round_allowed"])
            self.assertFalse(
                policy["second_round_apply_readiness"]["checks"]["artifact_freshness_pass"]
            )
            self.assertEqual(
                status["terminal_success_guard"]["failure_type"],
                "terminal_success_without_fresh_visual_evidence",
            )

    def test_top_level_status_cli_renders_runtime_status_contract(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        cli = repo_root / "bin" / "paperfit.js"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "EVALUATING",
                "task": {"type": "full_vto"},
                "runtime_event_summary": {"run_id": "run-agent", "event_count": 8},
                "defect_summary": {"initial_total": 4, "resolved": 1, "remaining": 3},
                "artifacts": {
                    "task_spec": "data/task.json",
                    "visual_signal_report": "data/visual_signal_report.json",
                    "defect_report": "data/defect_report.json",
                    "repair_plan": "data/repair_plan.json",
                },
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            agent_result = {
                "run_id": "run-agent",
                "runtime_actions": {
                    "repair_plan_executor": {
                        "success": True,
                        "skipped": True,
                        "reason": "dry_run_source_mutation",
                        "risk_level": "high",
                        "requires_approval": True,
                    }
                },
                "artifact_manifest": {"freshness": {"status": "pass", "blocking_checks": []}},
            }
            (root / "data" / "run_result_agent.json").write_text(
                json.dumps(agent_result),
                encoding="utf-8",
            )

            result = subprocess.run(
                ["node", str(cli), "status"],
                cwd=root,
                check=True,
                text=True,
                capture_output=True,
            )

            self.assertIn("RunResult: data/run_result_agent.json", result.stdout)
            self.assertIn("Artifact Freshness: pass", result.stdout)
            self.assertIn("Requires Approval: true", result.stdout)
            self.assertIn("Status: approval_required", result.stdout)

    def test_top_level_status_cli_renders_gatekeeper_blocking_detail(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        cli = repo_root / "bin" / "paperfit.js"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "DONE",
                "last_gatekeeper_decision": "DONE",
                "task": {"type": "full_vto", "target_pages": 9, "page_budget_scope": None},
                "runtime_event_summary": {"run_id": "run-page-budget", "event_count": 4},
                "artifacts": {
                    "gatekeeper_decision": "data/gatekeeper_decision.json",
                    "visual_signal_report": "data/visual_signal_report.json",
                },
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            visual_report = {
                "summary": {
                    "total_findings": 3,
                    "b2_width_findings": {
                        "total": 2,
                        "by_subtype": {"overflow_width": 1, "underfilled_width": 1},
                        "by_object_kind": {"figure_like": 1, "table_like": 1},
                        "pages": [3, 4],
                        "finding_ids": ["B2-native-figure-overflow-width", "B2-native-table-underfilled-width"],
                    },
                }
            }
            (root / "data" / "visual_signal_report.json").write_text(
                json.dumps(visual_report),
                encoding="utf-8",
            )
            gatekeeper = {
                "decision": "CONTINUE",
                "reasons": ["category A blocking defects remaining"],
                "remaining_defects": [
                    {
                        "id": "rule:underfull-1",
                        "defect_family": "underfull_hbox",
                        "severity": "minor",
                        "description": "in paragraph",
                    },
                    {
                        "id": "rule:underfull-2",
                        "defect_family": "underfull_hbox",
                        "severity": "minor",
                        "description": "in paragraph",
                    },
                    {
                        "id": "rule:underfull-3",
                        "defect_family": "underfull_hbox",
                        "severity": "minor",
                        "description": "in paragraph",
                    },
                    {
                        "id": "rule:underfull-4",
                        "defect_family": "underfull_hbox",
                        "severity": "minor",
                        "description": "in paragraph",
                    },
                    {
                        "id": "rule:underfull-5",
                        "defect_family": "underfull_hbox",
                        "severity": "minor",
                        "description": "in paragraph",
                    },
                    {
                        "id": "system:page_budget_violation",
                        "defect_family": "A3",
                        "severity": "critical",
                        "page": 11,
                        "description": "Page budget violation: current_pages=11, target_pages=9",
                    }
                ],
            }
            (root / "data" / "gatekeeper_decision.json").write_text(
                json.dumps(gatekeeper),
                encoding="utf-8",
            )
            run_result = {
                "run_id": "run-page-budget",
                "repair_loop_policy": {
                    "schema_version": "1.0",
                    "execution_mode": "bounded_apply",
                    "stop_condition": "done",
                    "next_round_reason": "gatekeeper_done",
                },
                "artifact_manifest": {"freshness": {"status": "pass", "blocking_checks": []}},
            }
            (root / "data" / "run_result_agent.json").write_text(
                json.dumps(run_result),
                encoding="utf-8",
            )
            repair_plan = {
                "summary": {
                    "total_candidates": 2,
                    "b2_width_findings": {
                        "total": 2,
                        "by_subtype": {"overflow_width": 2},
                        "by_object_kind": {"figure_like": 2},
                        "pages": [3],
                        "finding_ids": ["B2-native-figure-overflow-width"],
                    },
                    "b2_width_candidates": {
                        "total": 1,
                        "by_subtype": {"overflow_width": 1},
                        "by_object_kind": {"figure_like": 1},
                        "labels": ["fig:wide"],
                    },
                    "b2_width_targetable_candidates": {
                        "total": 1,
                        "by_subtype": {"overflow_width": 1},
                        "by_object_kind": {"figure_like": 1},
                        "labels": ["fig:wide"],
                    },
                    "b2_width_untargetable_candidates": {
                        "total": 1,
                        "by_subtype": {"underfilled_width": 1},
                        "by_reason": {"unsupported_label_or_object_kind": 1},
                        "labels": ["wide-only"],
                    },
                    "b2_width_unmatched_findings": 1,
                    "b2_width_unmatched_pages": [4],
                    "b2_width_unmatched_finding_ids": ["B2-native-table-underfilled-width"],
                },
                "candidates": [],
            }
            (root / "data" / "repair_plan.json").write_text(
                json.dumps(repair_plan),
                encoding="utf-8",
            )
            state["artifacts"]["repair_plan"] = "data/repair_plan.json"
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")

            result = subprocess.run(
                ["node", str(cli), "status"],
                cwd=root,
                check=True,
                text=True,
                capture_output=True,
            )

            self.assertIn("Gatekeeper Reasons: category A blocking defects remaining", result.stdout)
            self.assertIn("Page Budget: 9 (unspecified)", result.stdout)
            self.assertIn(
                "B2 Width Findings: 2 (overflow_width=1, underfilled_width=1) pages=3,4",
                result.stdout,
            )
            self.assertIn("B2 Width Candidates: 1 (overflow_width=1)", result.stdout)
            self.assertIn("B2 Width Candidate Labels: fig:wide", result.stdout)
            self.assertIn("B2 Width Targetable Candidates: 1 (overflow_width=1)", result.stdout)
            self.assertIn("B2 Width Targetable Labels: fig:wide", result.stdout)
            self.assertIn(
                "B2 Width Untargetable Candidates: 1 (unsupported_label_or_object_kind=1)",
                result.stdout,
            )
            self.assertIn("B2 Width Untargetable Labels: wide-only", result.stdout)
            self.assertIn("B2 Width Unmatched Findings: 1", result.stdout)
            self.assertIn("B2 Width Unmatched Pages: 4", result.stdout)
            self.assertIn("B2 Width Unmatched IDs: B2-native-table-underfilled-width", result.stdout)
            self.assertIn("Status: EVALUATING", result.stdout)
            self.assertIn(
                "Status Note: gatekeeper_artifact_overrides_stale_runtime_state",
                result.stdout,
            )
            self.assertIn(
                "Note: current gatekeeper artifact overrides this run state",
                result.stdout,
            )
            self.assertIn(
                "Note: policy is from the earlier run; current gatekeeper should drive next action",
                result.stdout,
            )
            self.assertIn("Gatekeeper Blocking Detail", result.stdout)
            self.assertIn("A3 [critical] p.11 - Page budget violation: current_pages=11, target_pages=9", result.stdout)
            self.assertLess(result.stdout.index("A3 [critical]"), result.stdout.index("underfull_hbox [minor]"))

    def test_top_level_status_cli_does_not_call_done_minor_defects_blocking(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        cli = repo_root / "bin" / "paperfit.js"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "DONE",
                "last_gatekeeper_decision": "DONE",
                "task": {"type": "full_vto"},
                "artifacts": {"gatekeeper_decision": "data/gatekeeper_decision.json"},
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            gatekeeper = {
                "decision": "DONE",
                "reasons": [],
                "remaining_defects": [
                    {
                        "id": "visual:tail_space",
                        "defect_family": "A2",
                        "severity": "minor",
                        "page": 11,
                        "description": "minor tail whitespace",
                    }
                ],
            }
            (root / "data" / "gatekeeper_decision.json").write_text(
                json.dumps(gatekeeper),
                encoding="utf-8",
            )

            result = subprocess.run(
                ["node", str(cli), "status"],
                cwd=root,
                check=True,
                text=True,
                capture_output=True,
            )

            self.assertIn("Gatekeeper: DONE", result.stdout)
            self.assertIn("Gatekeeper Remaining Defects", result.stdout)
            self.assertNotIn("Gatekeeper Blocking Detail", result.stdout)

    def test_top_level_status_cli_renders_freshness_block(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        cli = repo_root / "bin" / "paperfit.js"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "BLOCKED",
                "last_gatekeeper_decision": "DONE",
                "task": {"type": "full_vto"},
                "runtime_event_summary": {"run_id": "run-agent", "event_count": 9},
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            agent_result = {
                "run_id": "run-agent",
                "status": "blocked",
                "gatekeeper_decision": "DONE",
                "repair_loop_policy": {
                    "schema_version": "1.0",
                    "execution_mode": "report_only",
                    "stop_condition": "artifact_freshness_not_pass",
                    "next_round_allowed": False,
                    "next_round_reason": "artifact_freshness_not_pass",
                    "second_round_apply_readiness": {
                        "schema_version": "1.0",
                        "status": "blocked",
                        "reason": "artifact_freshness_not_pass",
                        "checks": {
                            "artifact_freshness_pass": False,
                            "source_mutation_executed": False,
                            "runtime_execution_mode_can_auto_apply": False,
                        },
                    },
                },
                "artifact_manifest": {
                    "freshness": {
                        "status": "stale_or_missing",
                        "blocking_checks": ["pdf_exists"],
                    }
                },
                "failure": {
                    "failure_type": "terminal_success_without_fresh_visual_evidence",
                    "reason": "gatekeeper_done_but_artifact_freshness_failed",
                    "artifact_freshness": {
                        "status": "stale_or_missing",
                        "blocking_checks": ["pdf_exists"],
                    },
                },
            }
            (root / "data" / "run_result_agent.json").write_text(
                json.dumps(agent_result),
                encoding="utf-8",
            )

            result = subprocess.run(
                ["node", str(cli), "status"],
                cwd=root,
                check=True,
                text=True,
                capture_output=True,
            )

            self.assertIn("Status: BLOCKED", result.stdout)
            self.assertIn("Gatekeeper: DONE", result.stdout)
            self.assertIn("Artifact Freshness: stale_or_missing", result.stdout)
            self.assertIn("Stop: artifact_freshness_not_pass", result.stdout)
            self.assertIn("Reason: artifact_freshness_not_pass", result.stdout)
            self.assertIn("Second Round Readiness: blocked", result.stdout)
            self.assertIn("Second Round Reason: artifact_freshness_not_pass", result.stdout)
            self.assertIn(
                "Second Round Failed Checks: artifact_freshness_pass, source_mutation_executed, runtime_execution_mode_can_auto_apply",
                result.stdout,
            )
            self.assertIn(
                "Failure: terminal_success_without_fresh_visual_evidence",
                result.stdout,
            )

    def test_top_level_status_cli_renders_candidate_gate_block(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        cli = repo_root / "bin" / "paperfit.js"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "BLOCKED",
                "last_gatekeeper_decision": "BLOCKED",
                "task": {"type": "full_vto"},
                "runtime_event_summary": {"run_id": "run-agent", "event_count": 6},
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            agent_result = {
                "run_id": "run-agent",
                "status": "blocked",
                "gatekeeper_decision": "BLOCKED",
                "repair_loop_policy": {
                    "schema_version": "1.0",
                    "execution_mode": "report_only",
                    "stop_condition": "approval_scope_blocked",
                    "next_round_allowed": False,
                    "next_round_reason": "approval_scope_blocked",
                    "candidate_approval_scope_gate": {
                        "schema_version": "1.0",
                        "status": "blocked",
                        "reason": "selected_candidate_exceeds_approval_scope",
                        "blocked_candidates": [
                            {
                                "index": 1,
                                "defect_family": "B1",
                                "risk": {
                                    "risk_level": "high",
                                    "operation": "float_movement_across_section_boundary",
                                },
                            }
                        ],
                    },
                    "second_round_apply_readiness": {
                        "schema_version": "1.0",
                        "status": "blocked",
                        "reason": "approval_scope_blocked",
                        "checks": {
                            "candidate_approval_scope_gate_pass": False,
                            "runtime_execution_mode_can_auto_apply": False,
                        },
                    },
                },
                "artifact_manifest": {
                    "freshness": {
                        "status": "pass",
                        "blocking_checks": [],
                    }
                },
            }
            (root / "data" / "run_result_agent.json").write_text(
                json.dumps(agent_result),
                encoding="utf-8",
            )

            result = subprocess.run(
                ["node", str(cli), "status"],
                cwd=root,
                check=True,
                text=True,
                capture_output=True,
            )

            self.assertIn("Stop: approval_scope_blocked", result.stdout)
            self.assertIn("Reason: approval_scope_blocked", result.stdout)
            self.assertIn("Candidate Gate: blocked", result.stdout)
            self.assertIn(
                "Candidate Gate Reason: selected_candidate_exceeds_approval_scope",
                result.stdout,
            )
            self.assertIn("Blocked Candidates: 1", result.stdout)
            self.assertIn("Blocked Operations: float_movement_across_section_boundary", result.stdout)
            self.assertIn("Second Round Readiness: blocked", result.stdout)
            self.assertIn("Second Round Reason: approval_scope_blocked", result.stdout)
            self.assertIn(
                "Second Round Failed Checks: candidate_approval_scope_gate_pass, runtime_execution_mode_can_auto_apply",
                result.stdout,
            )

    def test_top_level_status_cli_renders_table_b2_blocked_operation(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        cli = repo_root / "bin" / "paperfit.js"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "BLOCKED",
                "task": {"type": "full_vto"},
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            agent_result = {
                "run_id": "run-table-b2",
                "status": "blocked",
                "gatekeeper_decision": "BLOCKED",
                "repair_loop_policy": {
                    "schema_version": "1.0",
                    "execution_mode": "bounded_apply",
                    "stop_condition": "approval_scope_blocked",
                    "next_round_allowed": False,
                    "next_round_reason": "approval_scope_blocked",
                    "candidate_approval_scope_gate": {
                        "schema_version": "1.0",
                        "status": "blocked",
                        "reason": "selected_candidate_exceeds_approval_scope",
                        "blocked_candidates": [
                            {
                                "index": 1,
                                "defect_family": "B2",
                                "target": {"label": "wide-results", "object_kind": "table_like"},
                                "risk": {
                                    "risk_level": "high",
                                    "operation": "table_reconstruction",
                                },
                            }
                        ],
                    },
                },
                "artifact_manifest": {"freshness": {"status": "pass", "blocking_checks": []}},
            }
            (root / "data" / "run_result_agent.json").write_text(
                json.dumps(agent_result),
                encoding="utf-8",
            )

            result = subprocess.run(
                ["node", str(cli), "status"],
                cwd=root,
                check=True,
                text=True,
                capture_output=True,
            )

            self.assertIn("Candidate Gate: blocked", result.stdout)
            self.assertIn("Blocked Candidates: 1", result.stdout)
            self.assertIn("Blocked Operations: table_reconstruction", result.stdout)

    def test_top_level_status_cli_renders_carry_forward_block(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        cli = repo_root / "bin" / "paperfit.js"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "EVALUATING",
                "task": {"type": "full_vto"},
                "runtime_event_summary": {"run_id": "run-agent", "event_count": 5},
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            agent_result = {
                "run_id": "run-agent",
                "status": "continue",
                "gatekeeper_decision": "CONTINUE",
                "repair_loop_policy": {
                    "schema_version": "1.0",
                    "execution_mode": "report_only",
                    "stop_condition": "continue",
                    "next_round_allowed": False,
                    "next_round_reason": "multi_round_apply_not_enabled_in_current_runtime",
                    "approval_scope_carry_forward": {
                        "schema_version": "1.0",
                        "status": "blocked",
                        "reason": "approval_scope_contract_mismatch",
                        "checks": {
                            "approval_scope_matches": False,
                            "mutation_surface_within_scope": False,
                            "high_risk_operations_declared": True,
                            "fresh_approval_required_for_high_risk_operations": True,
                        },
                    },
                },
                "artifact_manifest": {
                    "freshness": {
                        "status": "pass",
                        "blocking_checks": [],
                    }
                },
            }
            (root / "data" / "run_result_agent.json").write_text(
                json.dumps(agent_result),
                encoding="utf-8",
            )

            result = subprocess.run(
                ["node", str(cli), "status"],
                cwd=root,
                check=True,
                text=True,
                capture_output=True,
            )

            self.assertIn("Approval Carry-forward: blocked", result.stdout)
            self.assertIn(
                "Approval Carry-forward Reason: approval_scope_contract_mismatch",
                result.stdout,
            )
            self.assertIn(
                "Approval Carry-forward Failed Checks: approval_scope_matches, mutation_surface_within_scope",
                result.stdout,
            )

    def test_top_level_status_cli_renders_bounded_apply_ready_next_round(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        cli = repo_root / "bin" / "paperfit.js"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "EVALUATING",
                "last_gatekeeper_decision": "CONTINUE",
                "task": {"type": "full_vto"},
                "runtime_event_summary": {"run_id": "run-bounded", "event_count": 16},
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            agent_result = {
                "run_id": "run-bounded",
                "status": "continue",
                "gatekeeper_decision": "CONTINUE",
                "repair_loop_policy": {
                    "schema_version": "1.0",
                    "execution_mode": "bounded_apply",
                    "round_limit": 3,
                    "current_round": 1,
                    "candidate_batch_limit": 1,
                    "stop_condition": "continue",
                    "next_round_allowed": True,
                    "next_round_reason": "ready",
                    "approval_scope_carry_forward": {
                        "schema_version": "1.0",
                        "status": "pass",
                        "reason": "approval_scope_carries_forward",
                        "checks": {
                            "approval_scope_matches": True,
                            "mutation_surface_within_scope": True,
                            "high_risk_operations_declared": True,
                            "fresh_approval_required_for_high_risk_operations": True,
                        },
                    },
                    "candidate_approval_scope_gate": {
                        "schema_version": "1.0",
                        "status": "pass",
                        "reason": "selected_candidates_within_approval_scope",
                        "blocked_candidates": [],
                    },
                    "second_round_apply_readiness": {
                        "schema_version": "1.0",
                        "status": "ready",
                        "reason": "ready",
                        "checks": {
                            "approval_scope_carry_forward_pass": True,
                            "round_artifact_lineage_present": True,
                            "artifact_freshness_pass": True,
                            "mutation_integrity_available": True,
                            "source_mutation_executed": True,
                            "candidate_approval_scope_gate_pass": True,
                            "within_round_limit": True,
                            "gatekeeper_continue": True,
                            "runtime_execution_mode_can_auto_apply": True,
                        },
                    },
                },
                "round_artifact_lineage": [
                    {
                        "schema_version": "1.0",
                        "round": 1,
                        "actions": {
                            "repair_plan_executor": {
                                "phase": "repair",
                                "input_artifacts": {"repair_plan": "data/repair_plan.json"},
                                "output_artifacts": {
                                    "repair_execution_report": "data/repair_execution_report.json"
                                },
                            },
                            "post_repair_observe": {
                                "phase": "observe",
                                "input_artifacts": {"main_tex": "main.tex"},
                                "output_artifacts": {"page_dir": "data/pages"},
                            },
                        },
                    }
                ],
                "artifact_manifest": {
                    "freshness": {
                        "status": "pass",
                        "blocking_checks": [],
                    }
                },
            }
            (root / "data" / "run_result_agent.json").write_text(
                json.dumps(agent_result),
                encoding="utf-8",
            )

            result = subprocess.run(
                ["node", str(cli), "status"],
                cwd=root,
                check=True,
                text=True,
                capture_output=True,
            )

            self.assertIn("Mode: bounded_apply", result.stdout)
            self.assertIn("Round Limit: 3", result.stdout)
            self.assertIn("Next Round Allowed: true", result.stdout)
            self.assertIn("Reason: ready", result.stdout)
            self.assertIn("Approval Carry-forward: pass", result.stdout)
            self.assertIn("Candidate Gate: pass", result.stdout)
            self.assertIn("Second Round Readiness: ready", result.stdout)
            self.assertIn("Second Round Reason: ready", result.stdout)
            self.assertNotIn("Second Round Failed Checks:", result.stdout)
            self.assertIn("Rounds: 1", result.stdout)
            self.assertIn("Actions: 2", result.stdout)

    def test_top_level_status_cli_renders_selected_visual_b2_candidate(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        cli = repo_root / "bin" / "paperfit.js"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "EVALUATING",
                "task": {"type": "full_vto"},
                "repair_execution_summary": {
                    "status": "success",
                    "applied_count": 1,
                    "selected_candidates": [
                        {
                            "defect_id": "B2",
                            "object": "wide-results",
                            "page": 3,
                            "object_kind": "table_like",
                            "visual_width_subtype": "overflow_width",
                        }
                    ],
                    "b2_width_selected_candidates": {
                        "total": 1,
                        "by_subtype": {"overflow_width": 1},
                        "by_object_kind": {"table_like": 1},
                        "labels": ["wide-results"],
                        "objects": ["wide-results"],
                        "pages": [3],
                    },
                },
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")

            result = subprocess.run(
                ["node", str(cli), "status"],
                cwd=root,
                check=True,
                text=True,
                capture_output=True,
            )

            self.assertIn("Execution: success", result.stdout)
            self.assertIn("Applied: 1", result.stdout)
            self.assertIn("B2 Width Selected Candidates: 1 (overflow_width=1)", result.stdout)
            self.assertIn("B2 Width Selected Labels: wide-results", result.stdout)
            self.assertIn("Selected: wide-results/overflow_width/table_like@p.3", result.stdout)

    def test_status_view_reads_selected_candidates_from_execution_report_when_state_summary_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "EVALUATING",
                "task": {"type": "full_vto"},
                "artifacts": {"repair_execution_report": "data/repair_execution_report.json"},
                "repair_execution_summary": {"status": None, "applied_count": 0},
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            execution_report = {
                "status": "success",
                "applied_count": 1,
                "selected_candidates": {
                    "float": [
                        {
                            "defect_id": "B2",
                            "object": "wide-results",
                            "page": 3,
                            "object_kind": "table_like",
                            "visual_width_subtype": "overflow_width",
                            "visual_object_width_ratio": 1.18,
                            "visual_overflow_pt": 18.4,
                        }
                    ],
                    "overflow": [],
                    "space_util": [],
                },
            }
            (root / "data" / "repair_execution_report.json").write_text(
                json.dumps(execution_report),
                encoding="utf-8",
            )

            status = build_runtime_status(project_root=root)

            self.assertEqual(status["repair"]["execution_status"], "success")
            self.assertEqual(status["repair"]["applied_count"], 1)
            selected = status["repair"]["selected_candidates"]
            self.assertEqual(selected[0]["object"], "wide-results")
            self.assertEqual(selected[0]["object_kind"], "table_like")
            self.assertEqual(selected[0]["visual_width_subtype"], "overflow_width")
            self.assertEqual(selected[0]["visual_overflow_pt"], 18.4)
            self.assertEqual(status["repair"]["b2_width_selected_candidates"]["total"], 1)
            self.assertEqual(status["repair"]["b2_width_selected_candidates"]["by_object_kind"], {"table_like": 1})
            self.assertEqual(status["repair"]["b2_width_selected_candidates"]["labels"], ["wide-results"])

    def test_status_view_prefers_nondry_result_and_reports_mutation_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "EVALUATING",
                "task": {"type": "full_vto"},
                "runtime_event_summary": {"run_id": "run-nondry", "event_count": 12},
                "artifacts": {
                    "repair_execution_report": "data/repair_execution_report.json",
                    "source_mutation_report": "data/source_mutation_report.json",
                },
                "repair_execution_summary": {"status": None, "applied_count": 0},
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            check_visual_result = {
                "run_id": "run-check",
                "artifact_manifest": {"freshness": {"status": "unknown"}},
            }
            nondry_result = {
                "run_id": "run-nondry",
                "runtime_actions": {
                    "repair_plan_executor": {
                        "status": "success",
                        "applied_count": 4,
                    }
                },
                "artifact_manifest": {"freshness": {"status": "pass", "blocking_checks": []}},
            }
            (root / "data" / "run_result_check_visual.json").write_text(
                json.dumps(check_visual_result),
                encoding="utf-8",
            )
            (root / "data" / "run_result_full_vto_nondry.json").write_text(
                json.dumps(nondry_result),
                encoding="utf-8",
            )

            status = build_runtime_status(project_root=root)

            self.assertEqual(status["run_result_path"], "data/run_result_full_vto_nondry.json")
            self.assertEqual(status["repair"]["execution_status"], "success")
            self.assertEqual(status["repair"]["applied_count"], 4)
            self.assertEqual(status["artifacts"]["source_mutation_report"], "data/source_mutation_report.json")
            self.assertEqual(status["approval"]["status"], "approved_and_executed")
            self.assertTrue(status["approval"]["approval_granted"])
            self.assertEqual(status["approval"]["execution"]["applied_count"], 4)


if __name__ == "__main__":
    unittest.main()
