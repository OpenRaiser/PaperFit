from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from runtime_approval import TASK_RISK_PROFILES  # noqa: E402
from runtime_repair_loop import (  # noqa: E402
    DEFAULT_CANDIDATE_BATCH_LIMIT,
    build_repair_loop_policy,
)


def _task(**overrides):
    payload = {
        "task_type": "full_vto",
        "max_rounds": 3,
        "dry_run_source_mutation": False,
    }
    payload.update(overrides)
    return payload


def _approval(status: str = "approved_and_executed") -> dict:
    profile = TASK_RISK_PROFILES["full_vto"]
    return {
        "status": status,
        "policy": {
            "approval_scope": profile["approval_scope"],
            "mutation_surface": profile["mutation_surface"],
            "high_risk_operations": profile["high_risk_operations"],
            "fresh_approval_required_for_high_risk_operations": True,
        },
    }


def _base_inputs(**overrides):
    inputs = {
        "task": _task(),
        "state": {
            "current_round": 1,
            "repair_plan_summary": {"total_candidates": 2},
            "content_integrity": {"validation_status": "mutation_reported"},
            "failure_tracking": {},
        },
        "runtime_actions": {
            "repair_plan_executor": {
                "applied_count": 1,
                "planned_candidates": 2,
                "approval_scope_gate": {"status": "pass"},
            },
        },
        "artifact_manifest": {"freshness": {"status": "pass"}},
        "approval": _approval(),
        "status": "continue",
        "gatekeeper_decision": "CONTINUE",
        "round_artifact_lineage": [{"round": 1, "actions": {}}],
    }
    inputs.update(overrides)
    return inputs


class RepairLoopPolicyNoEffectiveChangeTest(unittest.TestCase):
    def test_no_op_batch_stops_with_no_effective_change(self) -> None:
        policy = build_repair_loop_policy(
            **_base_inputs(
                runtime_actions={
                    "repair_plan_executor": {
                        "applied_count": 0,
                        "planned_candidates": 2,
                        "approval_scope_gate": {"status": "pass"},
                    },
                },
            )
        )
        self.assertEqual(policy["stop_condition"], "no_effective_change")
        self.assertEqual(policy["next_round_reason"], "repair_executor_no_effective_change")
        self.assertFalse(policy["next_round_allowed"])

    def test_effective_batch_is_not_stopped_by_no_effective_change(self) -> None:
        policy = build_repair_loop_policy(**_base_inputs())
        self.assertNotEqual(policy["stop_condition"], "no_effective_change")
        self.assertTrue(policy["next_round_allowed"])
        self.assertEqual(policy["next_round_reason"], "ready")

    def test_dry_run_batch_is_not_labeled_no_effective_change(self) -> None:
        policy = build_repair_loop_policy(
            **_base_inputs(
                task=_task(dry_run_source_mutation=True),
                approval=_approval(status="approval_required"),
                runtime_actions={
                    "repair_plan_executor": {
                        "applied_count": 0,
                        "planned_candidates": 2,
                        "reason": "dry_run_source_mutation",
                    },
                },
            )
        )
        self.assertNotEqual(policy["stop_condition"], "no_effective_change")
        self.assertEqual(policy["execution_mode"], "report_only")
        self.assertEqual(policy["candidate_batch_limit"], 0)

    def test_skipped_executor_is_not_labeled_no_effective_change(self) -> None:
        policy = build_repair_loop_policy(
            **_base_inputs(
                runtime_actions={
                    "repair_plan_executor": {
                        "applied_count": 0,
                        "planned_candidates": 2,
                        "skipped": True,
                        "reason": "approval_scope_blocked",
                    },
                },
            )
        )
        self.assertNotEqual(policy["stop_condition"], "no_effective_change")

    def test_no_plan_candidates_is_not_labeled_no_effective_change(self) -> None:
        policy = build_repair_loop_policy(
            **_base_inputs(
                state={
                    "current_round": 1,
                    "repair_plan_summary": {"total_candidates": 0},
                    "content_integrity": {"validation_status": "mutation_reported"},
                    "failure_tracking": {},
                },
                runtime_actions={
                    "repair_plan_executor": {
                        "applied_count": 0,
                        "planned_candidates": 0,
                        "approval_scope_gate": {"status": "pass"},
                    },
                },
            )
        )
        self.assertNotEqual(policy["stop_condition"], "no_effective_change")


class RepairLoopPolicyBatchLimitTest(unittest.TestCase):
    def test_bounded_apply_reports_default_batch_limit(self) -> None:
        policy = build_repair_loop_policy(**_base_inputs())
        self.assertEqual(policy["candidate_batch_limit"], DEFAULT_CANDIDATE_BATCH_LIMIT)
        self.assertEqual(policy["candidate_batch_limit"], 8)  # 吞吐上调后

    def test_dry_run_reports_zero_batch_limit(self) -> None:
        policy = build_repair_loop_policy(
            **_base_inputs(
                task=_task(dry_run_source_mutation=True),
                approval=_approval(status="approval_required"),
                runtime_actions={
                    "repair_plan_executor": {
                        "applied_count": 0,
                        "planned_candidates": 2,
                        "reason": "dry_run_source_mutation",
                    },
                },
            )
        )
        self.assertEqual(policy["candidate_batch_limit"], 0)


if __name__ == "__main__":
    unittest.main()
