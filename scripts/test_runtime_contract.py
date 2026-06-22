from __future__ import annotations

import unittest

from scripts.runtime_approval import build_approval_object, build_approval_scope_carry_forward_check
from scripts.runtime_state_machine import (
    IllegalTransitionError,
    SOURCE_CHANGING_STATE_MACHINE,
    VISUAL_ONLY_STATE_MACHINE,
)
from scripts.runtime_types import ActionResult, TaskSpec


class RuntimeContractTest(unittest.TestCase):
    def test_visual_only_rejects_source_mutation(self) -> None:
        with self.assertRaises(ValueError):
            TaskSpec.from_dict(
                {
                    "task_type": "visual_only",
                    "main_tex": "main.tex",
                    "allow_source_mutation": True,
                }
            )

    def test_visual_only_state_machine_rejects_repair_transition(self) -> None:
        state = VISUAL_ONLY_STATE_MACHINE.transition("INIT", "task_validated")
        self.assertEqual(state, "READY")
        with self.assertRaises(IllegalTransitionError):
            VISUAL_ONLY_STATE_MACHINE.transition(state, "start_repair")

    def test_source_changing_state_machine_covers_dry_run_repair_path(self) -> None:
        state = "INIT"
        for event, expected in [
            ("task_validated", "READY"),
            ("start_observe", "OBSERVING"),
            ("artifacts_observed", "DIAGNOSING"),
            ("diagnosis_complete", "PLANNING"),
            ("plan_ready", "REPAIRING"),
            ("repair_skipped", "VERIFYING"),
            ("gatekeeper_continue", "CONTINUE"),
        ]:
            state = SOURCE_CHANGING_STATE_MACHINE.transition(state, event)
            self.assertEqual(state, expected)

        with self.assertRaises(IllegalTransitionError):
            SOURCE_CHANGING_STATE_MACHINE.transition("PLANNING", "gatekeeper_done")

    def test_task_spec_preserves_column_void_report(self) -> None:
        spec = TaskSpec.from_dict(
            {
                "task_type": "visual_only",
                "main_tex": "main.tex",
                "column_void_report": "data/column_void_report.json",
            }
        )
        self.assertEqual(spec.column_void_report, "data/column_void_report.json")
        self.assertEqual(
            spec.to_dict()["column_void_report"],
            "data/column_void_report.json",
        )

    def test_source_changing_task_requires_snapshot_and_rollback_contract(self) -> None:
        with self.assertRaises(ValueError):
            TaskSpec.from_dict(
                {
                    "task_type": "full_vto",
                    "main_tex": "main.tex",
                    "allow_source_mutation": True,
                }
            )

        spec = TaskSpec.from_dict(
            {
                "task_type": "full_vto",
                "main_tex": "main.tex",
                "allow_source_mutation": True,
                "pre_repair_snapshot_required": True,
                "dry_run_source_mutation": True,
                "rollback_policy": "required",
            }
        )
        self.assertTrue(spec.allow_source_mutation)
        self.assertTrue(spec.pre_repair_snapshot_required)
        self.assertTrue(spec.dry_run_source_mutation)
        self.assertEqual(spec.rollback_policy, "required")

    def test_action_result_preserves_legacy_fields_and_adds_contract_fields(self) -> None:
        action = ActionResult.from_result(
            action_name="repair_plan_executor",
            phase="repair",
            runtime_state="REPAIRING",
            result={
                "success": True,
                "skipped": True,
                "reason": "dry_run_source_mutation",
                "planned_candidates": 3,
            },
        ).to_dict()

        self.assertEqual(action["schema_version"], "1.0")
        self.assertEqual(action["action_name"], "repair_plan_executor")
        self.assertEqual(action["phase"], "repair")
        self.assertEqual(action["runtime_state"], "REPAIRING")
        self.assertTrue(action["success"])
        self.assertTrue(action["skipped"])
        self.assertEqual(action["reason"], "dry_run_source_mutation")
        self.assertEqual(action["planned_candidates"], 3)
        self.assertEqual(action["risk_level"], "high")
        self.assertTrue(action["requires_approval"])

    def test_action_result_preserves_input_and_output_artifacts(self) -> None:
        action = ActionResult.from_result(
            action_name="defect_report_builder",
            phase="diagnose",
            runtime_state="DIAGNOSING",
            result={
                "success": True,
                "input_artifacts": {
                    "rule_report": "data/rule_report.json",
                    "visual_signal_report": "data/visual_signal_report.json",
                },
                "output_path": "data/defect_report.json",
                "output_artifacts": {
                    "defect_report": "data/defect_report.json",
                },
            },
        ).to_dict()

        self.assertEqual(action["input_artifacts"]["rule_report"], "data/rule_report.json")
        self.assertEqual(action["input_artifacts"]["visual_signal_report"], "data/visual_signal_report.json")
        self.assertEqual(action["output_artifacts"]["output_path"], "data/defect_report.json")
        self.assertEqual(action["output_artifacts"]["defect_report"], "data/defect_report.json")

    def test_approval_object_exposes_task_risk_policy(self) -> None:
        approval = build_approval_object(
            task={
                "task_type": "repair_table",
                "dry_run_source_mutation": True,
                "rollback_policy": "required",
                "pre_repair_snapshot_required": True,
            },
            state={"repair_plan_summary": {"total_candidates": 2}},
            runtime_actions={
                "repair_plan_executor": {
                    "skipped": True,
                    "reason": "dry_run_source_mutation",
                    "risk_level": "high",
                    "requires_approval": True,
                }
            },
        )

        self.assertEqual(approval["status"], "approval_required")
        self.assertEqual(approval["policy"]["approval_scope"], "table_repair")
        self.assertIn("table_environment", approval["policy"]["mutation_surface"])
        self.assertIn("table_reconstruction", approval["policy"]["high_risk_operations"])
        self.assertTrue(approval["policy"]["fresh_approval_required_for_high_risk_operations"])

    def test_approval_scope_carry_forward_reports_contract_match(self) -> None:
        approval = build_approval_object(
            task={
                "task_type": "full_vto",
                "dry_run_source_mutation": True,
                "rollback_policy": "required",
                "pre_repair_snapshot_required": True,
            },
            state={"repair_plan_summary": {"total_candidates": 1}},
            runtime_actions={
                "repair_plan_executor": {
                    "skipped": True,
                    "reason": "dry_run_source_mutation",
                }
            },
        )

        check = build_approval_scope_carry_forward_check(
            task={"task_type": "full_vto"},
            approval=approval,
        )

        self.assertEqual(check["status"], "pass")
        self.assertEqual(check["approval_scope"], "bounded_layout_repair")
        self.assertTrue(check["checks"]["approval_scope_matches"])
        self.assertTrue(check["checks"]["fresh_approval_required_for_high_risk_operations"])


if __name__ == "__main__":
    unittest.main()
