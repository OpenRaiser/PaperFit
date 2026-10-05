from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from regression.round_acceptance import (  # noqa: E402
    AcceptanceDecision,
    RoundEvidence,
    detect_no_effect,
    evaluate_round_acceptance,
)


def _good_evidence(**overrides):
    """所有指标都改善或持平的证据。"""
    defaults = dict(
        page_count_before=12,
        page_count_after=10,
        target_pages=10,
        overfull_before=5,
        overfull_after=2,
        figures_before=8,
        figures_after=8,
        tables_before=3,
        tables_after=3,
        labels_before=15,
        labels_after=15,
        refs_before=20,
        refs_after=20,
        undefined_refs_before=3,
        undefined_refs_after=3,
        undefined_citations_before=1,
        undefined_citations_after=1,
        major_defects_before=5,
        major_defects_after=2,
        content_integrity_pass=True,
    )
    defaults.update(overrides)
    return RoundEvidence(**defaults)


class AcceptanceGateTest(unittest.TestCase):
    """方案第四节回归断言 A/B/C/H 的直接实现。"""

    def test_case_A_success_not_rejected(self):
        """A：12页→10页，目标10 → 接受。"""
        e = _good_evidence()
        d = evaluate_round_acceptance(e)
        self.assertTrue(d.accepted)
        self.assertFalse(d.rollback_required)

    def test_case_B_page_regression_rejected(self):
        """B：12页→13页，目标11 → 拒绝+回滚。"""
        e = _good_evidence(
            page_count_before=12,
            page_count_after=13,
            target_pages=11,
        )
        d = evaluate_round_acceptance(e)
        self.assertFalse(d.accepted)
        self.assertTrue(d.rollback_required)
        self.assertTrue(any("page_distance" in r for r in d.reasons))

    def test_case_C_b2_widen_rejected_by_pages(self):
        """C：11页→12页，目标9 → 拒绝（即使对象宽度改善了）。"""
        e = _good_evidence(
            page_count_before=11,
            page_count_after=12,
            target_pages=9,
        )
        d = evaluate_round_acceptance(e)
        self.assertFalse(d.accepted)

    def test_overfull_increase_rejected(self):
        e = _good_evidence(overfull_before=3, overfull_after=8)
        d = evaluate_round_acceptance(e)
        self.assertFalse(d.accepted)
        self.assertTrue(any("overfull" in r for r in d.reasons))

    def test_figure_loss_rejected(self):
        e = _good_evidence(figures_before=8, figures_after=6)
        d = evaluate_round_acceptance(e)
        self.assertFalse(d.accepted)
        self.assertTrue(any("figures lost" in r for r in d.reasons))

    def test_new_undefined_refs_rejected(self):
        """方案第 16 条：修前 5 个修后 7 个 → 新增 2 个，必须回滚。"""
        e = _good_evidence(undefined_refs_before=5, undefined_refs_after=7)
        d = evaluate_round_acceptance(e)
        self.assertFalse(d.accepted)
        self.assertTrue(any("new undefined refs" in r for r in d.reasons))

    def test_existing_undefined_refs_ok(self):
        """修前 5 个修后 5 个 → 不算新增，不拒绝。"""
        e = _good_evidence(undefined_refs_before=5, undefined_refs_after=5)
        d = evaluate_round_acceptance(e)
        self.assertTrue(d.accepted)

    def test_content_integrity_failure_rejected(self):
        e = _good_evidence(content_integrity_pass=False)
        d = evaluate_round_acceptance(e)
        self.assertFalse(d.accepted)

    def test_new_major_defects_rejected(self):
        e = _good_evidence(major_defects_before=2, major_defects_after=5)
        d = evaluate_round_acceptance(e)
        self.assertFalse(d.accepted)
        self.assertTrue(any("major defects" in r for r in d.reasons))

    def test_no_change_accepted(self):
        """所有指标持平 → 接受（不是拒绝，因为没有变差）。"""
        e = _good_evidence(
            page_count_before=10, page_count_after=10, target_pages=10,
            overfull_before=3, overfull_after=3,
            major_defects_before=2, major_defects_after=2,
        )
        d = evaluate_round_acceptance(e)
        self.assertTrue(d.accepted)


class PageDistanceTest(unittest.TestCase):
    def test_distance_computation(self):
        e = RoundEvidence(page_count_before=12, page_count_after=13, target_pages=10)
        self.assertEqual(e.page_distance_before, 2)
        self.assertEqual(e.page_distance_after, 3)

    def test_distance_shrinking_accepted(self):
        """12→10，目标10：distance 2→0 → 接受。"""
        e = _good_evidence()
        self.assertEqual(e.page_distance_before, 2)
        self.assertEqual(e.page_distance_after, 0)
        d = evaluate_round_acceptance(e)
        self.assertTrue(d.accepted)

    def test_overshoot_still_ok_if_closer(self):
        """12→8，目标10：distance 2→2 → 接受（没变差）。"""
        e = _good_evidence(page_count_before=12, page_count_after=8, target_pages=10)
        d = evaluate_round_acceptance(e)
        self.assertTrue(d.accepted)

    def test_no_target_pages_no_page_check(self):
        """没有 target_pages → 页距离检查跳过。"""
        e = _good_evidence(target_pages=None, page_count_before=None, page_count_after=None)
        d = evaluate_round_acceptance(e)
        self.assertTrue(d.accepted)


class NoEffectDetectionTest(unittest.TestCase):
    def test_source_changed_not_no_effect(self):
        result = detect_no_effect(source_changed=True, applied_count=2, run_failed=False)
        self.assertIsNone(result)

    def test_no_candidates(self):
        result = detect_no_effect(source_changed=False, applied_count=0, run_failed=False)
        self.assertEqual(result, "no_actionable_candidate")

    def test_anchor_failure(self):
        result = detect_no_effect(
            source_changed=False, applied_count=0, run_failed=False,
            rejection_reasons=["source anchor unresolved"],
        )
        self.assertEqual(result, "no_unique_source_anchor")

    def test_approval_blocked(self):
        result = detect_no_effect(
            source_changed=False, applied_count=0, run_failed=False,
            rejection_reasons=["approval scope blocked"],
        )
        self.assertEqual(result, "candidate_blocked_by_approval")

    def test_run_failed_not_no_effect(self):
        result = detect_no_effect(source_changed=False, applied_count=0, run_failed=True)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
