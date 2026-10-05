from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from runtime_repair_loop import (  # noqa: E402
    _no_improvement_streak,
    _round_improved,
    build_repair_loop_policy,
)
from runtime_types import ROUND_MODE_PRESETS, TaskSpec  # noqa: E402


def _task(**overrides):
    payload = {
        "task_type": "full_vto",
        "max_rounds": 5,
        "round_mode": "until-stable",
        "dry_run_source_mutation": False,
    }
    payload.update(overrides)
    return payload


def _approval():
    from runtime_approval import TASK_RISK_PROFILES

    profile = TASK_RISK_PROFILES["full_vto"]
    return {
        "status": "approved_and_executed",
        "policy": {
            "approval_scope": profile["approval_scope"],
            "mutation_surface": profile["mutation_surface"],
            "high_risk_operations": profile["high_risk_operations"],
            "fresh_approval_required_for_high_risk_operations": True,
        },
    }


def _inputs(**overrides):
    inputs = {
        "task": _task(),
        "state": {
            "current_round": 3,
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
        "round_metrics": [],
        "rollback_streak": 0,
    }
    inputs.update(overrides)
    return inputs


class RoundModePresetsTest(unittest.TestCase):
    _BASE = {
        "task_type": "full_vto",
        "main_tex": "main.tex",
        "allow_source_mutation": True,
        "pre_repair_snapshot_required": True,
        "rollback_policy": "required",
    }

    def test_presets_map_to_expected_rounds(self):
        self.assertEqual(ROUND_MODE_PRESETS, {"safe": 1, "fix-layout": 2, "until-stable": 5})

    def test_spec_applies_preset(self):
        for mode, expected in ROUND_MODE_PRESETS.items():
            spec = TaskSpec.from_dict({**self._BASE, "round_mode": mode})
            self.assertEqual(spec.max_rounds, expected)
            self.assertEqual(spec.round_mode, mode)

    def test_mode_overrides_explicit_max_rounds(self):
        spec = TaskSpec.from_dict({**self._BASE, "round_mode": "safe", "max_rounds": 10})
        self.assertEqual(spec.max_rounds, 1)

    def test_invalid_mode_rejected(self):
        with self.assertRaises(ValueError):
            TaskSpec.from_dict({**self._BASE, "round_mode": "yolo"})

    def test_to_dict_round_trips(self):
        spec = TaskSpec.from_dict({**self._BASE, "round_mode": "fix-layout"})
        payload = spec.to_dict()
        self.assertEqual(payload["round_mode"], "fix-layout")
        self.assertEqual(payload["max_rounds"], 2)


class RoundImprovementTest(unittest.TestCase):
    def test_overfull_drop_is_improvement(self):
        self.assertTrue(_round_improved({"overfull_hbox_total_before": 5, "overfull_hbox_total_after": 1}))
        self.assertTrue(_round_improved({"defects_remaining_before": 8, "defects_remaining_after": 6}))

    def test_no_change_or_worse_is_not_improvement(self):
        self.assertFalse(_round_improved({"overfull_hbox_total_before": 5, "overfull_hbox_total_after": 5}))
        self.assertFalse(_round_improved({"overfull_hbox_total_before": 3, "overfull_hbox_total_after": 7}))

    def test_missing_metrics_returns_none(self):
        self.assertIsNone(_round_improved({}))

    def test_streak_counts_trailing_non_improving_rounds(self):
        metrics = [
            {"overfull_hbox_total_before": 5, "overfull_hbox_total_after": 3},  # improved
            {"overfull_hbox_total_before": 3, "overfull_hbox_total_after": 3},  # no change
            {"overfull_hbox_total_before": 3, "overfull_hbox_total_after": 4},  # worse
        ]
        self.assertEqual(_no_improvement_streak(metrics), 2)
        self.assertEqual(_no_improvement_streak(metrics[:2]), 1)
        self.assertEqual(_no_improvement_streak(metrics[:1]), 0)


class LoopPolicyStopConditionsTest(unittest.TestCase):
    def test_two_non_improving_rounds_stop(self):
        policy = build_repair_loop_policy(
            **_inputs(
                round_metrics=[
                    {"overfull_hbox_total_before": 5, "overfull_hbox_total_after": 5},
                    {"overfull_hbox_total_before": 5, "overfull_hbox_total_after": 5},
                ],
            )
        )
        self.assertEqual(policy["stop_condition"], "no_improvement_streak")
        self.assertFalse(policy["next_round_allowed"])
        self.assertEqual(policy["next_round_reason"], "two_rounds_without_quality_improvement")

    def test_recent_improvement_resets_streak(self):
        policy = build_repair_loop_policy(
            **_inputs(
                round_metrics=[
                    {"overfull_hbox_total_before": 5, "overfull_hbox_total_after": 5},
                    {"overfull_hbox_total_before": 5, "overfull_hbox_total_after": 5},
                    {"overfull_hbox_total_before": 5, "overfull_hbox_total_after": 1},
                ],
            )
        )
        self.assertNotEqual(policy["stop_condition"], "no_improvement_streak")

    def test_two_consecutive_rollbacks_stop(self):
        policy = build_repair_loop_policy(**_inputs(rollback_streak=2))
        self.assertEqual(policy["stop_condition"], "consecutive_rollbacks")
        self.assertFalse(policy["next_round_allowed"])
        self.assertEqual(policy["next_round_reason"], "two_consecutive_rollbacks_hand_off_to_host")

    def test_single_rollback_does_not_stop(self):
        policy = build_repair_loop_policy(**_inputs(rollback_streak=1))
        self.assertEqual(policy["stop_condition"], "continue")

    def test_dry_run_ignores_improvement_streak(self):
        policy = build_repair_loop_policy(
            **_inputs(
                task=_task(dry_run_source_mutation=True),
                round_metrics=[
                    {"overfull_hbox_total_before": 5, "overfull_hbox_total_after": 5},
                    {"overfull_hbox_total_before": 5, "overfull_hbox_total_after": 5},
                ],
            )
        )
        self.assertNotEqual(policy["stop_condition"], "no_improvement_streak")

    def test_no_effective_change_takes_precedence(self):
        policy = build_repair_loop_policy(
            **_inputs(
                runtime_actions={
                    "repair_plan_executor": {
                        "applied_count": 0,
                        "planned_candidates": 2,
                        "approval_scope_gate": {"status": "pass"},
                    },
                },
                rollback_streak=2,
            )
        )
        self.assertEqual(policy["stop_condition"], "no_effective_change")


if __name__ == "__main__":
    unittest.main()
