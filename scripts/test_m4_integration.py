from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from gatekeeper_enforcer import make_decision  # noqa: E402
from runtime_repair_loop import _round_quality_regressed, build_repair_loop_policy  # noqa: E402
from runtime_types import TaskSpec  # noqa: E402


def _gatekeeper_state(**overrides):
    state = {
        "compile_success": True,
        "page_images_rendered": True,
        "task": {},
        "artifacts": {},
        "visual_signals_summary": {"updated_at": "2026-10-05T00:00:00"},
    }
    state.update(overrides)
    return state


def _run_gatekeeper(vlm_score=None, defects=None, **state_overrides):
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir).resolve()
        data_dir = root / "data"
        data_dir.mkdir()
        (data_dir / "visual_signal_report.json").write_text(
            json.dumps({"summary": {"pages_analyzed": 9}, "findings": []}),
            encoding="utf-8",
        )
        state = _gatekeeper_state(artifacts={"visual_signal_report": "data/visual_signal_report.json"}, **state_overrides)
        cwd = Path.cwd()
        try:
            os.chdir(root)
            return make_decision(
                state=state,
                defects_payload={"defects": defects or []},
                strict_mode=False,
                semantic_report_path=None,
                vlm_gated_score=vlm_score,
            )
        finally:
            os.chdir(cwd)


class GatekeeperVlmScoreTest(unittest.TestCase):
    def test_high_vlm_score_allows_done(self):
        decision = _run_gatekeeper(vlm_score=4.2)
        self.assertEqual(decision["decision"], "DONE")
        self.assertTrue(decision["checks"]["vlm_gated_score"]["pass"])

    def test_low_vlm_score_downgrades_done_to_continue(self):
        decision = _run_gatekeeper(vlm_score=2.1)
        self.assertEqual(decision["decision"], "CONTINUE")
        self.assertFalse(decision["checks"]["vlm_gated_score"]["pass"])
        self.assertTrue(any("VLM gated score" in r for r in decision["reasons"]))

    def test_vlm_score_never_upgrades_blocked(self):
        decision = _run_gatekeeper(vlm_score=4.8, compile_success=False)
        # 编译失败已经 BLOCKED，高分不能把它升回 DONE
        self.assertNotEqual(decision["decision"], "DONE")

    def test_vlm_score_none_is_noop(self):
        decision = _run_gatekeeper(vlm_score=None)
        self.assertEqual(decision["decision"], "DONE")
        self.assertNotIn("vlm_gated_score", decision["checks"])

    def test_vlm_score_does_not_affect_continue(self):
        # 已有缺陷时决策本来就是 CONTINUE，VLM 不改变它
        decision = _run_gatekeeper(
            vlm_score=4.5,
            defects=[{"id": "x", "defect_family": "A1", "severity": "major", "status": "open"}],
        )
        self.assertEqual(decision["decision"], "CONTINUE")


class RoundQualityRegressionTest(unittest.TestCase):
    def test_overfull_increase_is_regression(self):
        self.assertTrue(_round_quality_regressed({"overfull_hbox_total_before": 3, "overfull_hbox_total_after": 7}))

    def test_visual_findings_increase_is_regression(self):
        self.assertTrue(_round_quality_regressed({"visual_findings_before": 2, "visual_findings_after": 5}))

    def test_improvement_is_not_regression(self):
        self.assertFalse(_round_quality_regressed({"overfull_hbox_total_before": 5, "overfull_hbox_total_after": 2}))

    def test_no_change_is_not_regression(self):
        self.assertFalse(_round_quality_regressed({"overfull_hbox_total_before": 3, "overfull_hbox_total_after": 3}))

    def test_missing_metrics_not_regression(self):
        self.assertFalse(_round_quality_regressed({}))


class LoopPolicyQualityRegressedTest(unittest.TestCase):
    def _inputs(self, **overrides):
        from runtime_approval import TASK_RISK_PROFILES

        profile = TASK_RISK_PROFILES["full_vto"]
        inputs = {
            "task": {"task_type": "full_vto", "max_rounds": 5, "round_mode": "until-stable"},
            "state": {
                "current_round": 2,
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
            "approval": {
                "status": "approved_and_executed",
                "policy": {
                    "approval_scope": profile["approval_scope"],
                    "mutation_surface": profile["mutation_surface"],
                    "high_risk_operations": profile["high_risk_operations"],
                    "fresh_approval_required_for_high_risk_operations": True,
                },
            },
            "status": "continue",
            "gatekeeper_decision": "CONTINUE",
            "round_artifact_lineage": [{"round": 1, "actions": {}}],
            "round_metrics": [
                {"overfull_hbox_total_before": 5, "overfull_hbox_total_after": 5,
                 "visual_findings_before": 3, "visual_findings_after": 3},
                {"overfull_hbox_total_before": 5, "overfull_hbox_total_after": 8,
                 "visual_findings_before": 3, "visual_findings_after": 3},
            ],
            "rollback_streak": 0,
        }
        inputs.update(overrides)
        return inputs

    def test_quality_regressed_round_stops(self):
        policy = build_repair_loop_policy(**self._inputs())
        self.assertEqual(policy["stop_condition"], "quality_regressed")
        self.assertFalse(policy["next_round_allowed"])
        self.assertEqual(policy["next_round_reason"], "round_quality_regression_suggest_rollback")

    def test_improved_round_continues(self):
        policy = build_repair_loop_policy(
            **self._inputs(
                round_metrics=[
                    {"overfull_hbox_total_before": 5, "overfull_hbox_total_after": 3,
                     "visual_findings_before": 4, "visual_findings_after": 2},
                ],
            )
        )
        self.assertNotEqual(policy["stop_condition"], "quality_regressed")

    def test_dry_run_ignores_regression(self):
        policy = build_repair_loop_policy(
            **self._inputs(task={"task_type": "full_vto", "max_rounds": 5, "dry_run_source_mutation": True})
        )
        self.assertNotEqual(policy["stop_condition"], "quality_regressed")

    def test_no_effective_change_takes_precedence(self):
        policy = build_repair_loop_policy(
            **self._inputs(
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


class RoundMetricsVisualFieldsTest(unittest.TestCase):
    def test_task_spec_round_trip_includes_new_fields(self):
        spec = TaskSpec.from_dict({
            "task_type": "full_vto",
            "main_tex": "main.tex",
            "allow_source_mutation": True,
            "pre_repair_snapshot_required": True,
            "rollback_policy": "required",
            "round_mode": "fix-layout",
        })
        self.assertEqual(spec.max_rounds, 2)  # fix-layout preset


if __name__ == "__main__":
    unittest.main()
