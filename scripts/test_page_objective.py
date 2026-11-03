from __future__ import annotations
import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from page_objective import evaluate_page_objective, page_distance
from runtime_types import TaskSpec
from state_schema import normalize_state, validate_state
from regression.round_acceptance import RoundEvidence, evaluate_round_acceptance
from runtime_repair_loop import _round_improved, _no_improvement_streak, build_repair_loop_policy

class PageObjectiveTest(unittest.TestCase):
    def objective(self, count, **kwargs):
        return evaluate_page_objective(target_pages=3, page_count=count,
            evidence_status="current", pdf_sha256="a"*64, **kwargs)

    def test_exact_is_bidirectional_and_at_most_preserves_underfill(self):
        for n, distance in ((2, 1), (3, 0), (4, 1)):
            with self.subTest(n=n):
                result = self.objective(n, page_policy="exact")
                self.assertEqual(result["target_met"], n == 3)
                self.assertEqual(result["distance"], distance)
        self.assertTrue(self.objective(2)["target_met"])
        self.assertEqual(page_distance(2, 3), 0)

    def test_missing_identity_count_and_scope_cannot_mean_success(self):
        for kwargs in ({}, {"pdf_sha256": "bad"}, {"pdf_sha256": "a"*64, "page_count": None}):
            result = evaluate_page_objective(target_pages=3, evidence_status="current", **kwargs)
            self.assertIsNone(result["target_met"])
        for policy in ("exact", "at_most"):
            for scope in ("main_body", "with_refs"):
                with self.subTest(policy=policy, scope=scope):
                    result = self.objective(3, page_policy=policy, page_budget_scope=scope)
                    self.assertIsNone(result["target_met"])
                    self.assertEqual(result["reason"], "scope_boundary_unverified")
        self.assertEqual(evaluate_page_objective()["status"], "not_requested")

    def test_policy_survives_typed_and_legacy_state_round_trip(self):
        spec = TaskSpec.from_dict({"task_type":"visual_only", "main_tex":"main.tex",
                                  "target_pages":3, "page_policy":"exact"})
        self.assertEqual(spec.to_dict()["page_policy"], "exact")
        state = normalize_state({"main_tex":"main.tex", "task":{"target_pages":3}})
        self.assertEqual(validate_state(state)["task"]["page_policy"], "at_most")
        state["task"]["page_policy"] = "exact"
        self.assertEqual(validate_state(state)["task"]["page_policy"], "exact")
        state["task"]["page_policy"] = "none"
        with self.assertRaises(ValueError): validate_state(state)

    def test_round_distance_only_requires_exact_when_requested(self):
        self.assertTrue(evaluate_round_acceptance(RoundEvidence(
            page_count_before=3, page_count_after=2, target_pages=3)).accepted)
        result = evaluate_round_acceptance(RoundEvidence(page_count_before=3,
            page_count_after=2, target_pages=3, page_policy="exact"))
        self.assertFalse(result.accepted)
        self.assertTrue(result.rollback_required)
        self.assertTrue(evaluate_round_acceptance(RoundEvidence(page_count_before=1,
            page_count_after=2, target_pages=3, page_policy="exact")).accepted)

    def test_page_progress_is_not_stalled_by_unchanged_defect_counts(self):
        rounds = [{"page_gap_before":3,"page_gap_after":2,"overfull_hbox_total_before":0,
                   "overfull_hbox_total_after":0,"defects_remaining_before":0,"defects_remaining_after":0},
                  {"page_gap_before":2,"page_gap_after":1,"overfull_hbox_total_before":0,
                   "overfull_hbox_total_after":0,"defects_remaining_before":0,"defects_remaining_after":0}]
        self.assertTrue(_round_improved(rounds[0]))
        self.assertEqual(_no_improvement_streak(rounds), 0)

    def test_injected_done_cannot_end_loop_with_unmet_page_objective(self):
        for policy in ("exact", "at_most"):
            for count in (None, 4):
                manifest={"freshness":{"status":"pass"}, "artifacts":{"pdf":{
                    "page_count":count,"page_count_status":"available","sha256":"a"*64}}}
                result=build_repair_loop_policy(task={"task_type":"adjust_length", "max_rounds":2,
                    "target_pages":3,"page_policy":policy}, state={"current_round":1},
                    runtime_actions={}, artifact_manifest=manifest, approval={"status":"approved"},
                    status="done", gatekeeper_decision="DONE")
                self.assertNotEqual(result["stop_condition"], "done")
                self.assertIsNot(result["page_objective"]["target_met"], True)

if __name__ == "__main__": unittest.main()
