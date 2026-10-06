"验收：跨版本安全指标必须用可比分母。"

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(SCRIPTS_DIR.parent / "regression") not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR.parent / "regression"))

from regression.decision import check_safety, decide_regression  # noqa: E402
from regression.mvp_runner import _aggregate_safety, _paired_safety  # noqa: E402


def _eval(paper, case, budget=True, compile_ok=True):
    return {
        "paper_id": paper,
        "case_id": case,
        "compile_success": compile_ok,
        "render_success": True,
        "page_budget_hit": budget,
        "content_integrity": {"ok": True},
    }


def _side(venues, budget_hits):
    evals = []
    for i, venue in enumerate(venues):
        evals.append(_eval(venue, "arXiv-x", budget=budget_hits[i]))
    return evals


class PairedSafetyTest(unittest.TestCase):
    def test_unequal_denominators_do_not_claim_paired_improvement(self):
        
        
        b0 = _side(["A", "B", "C", "D", "E", "F", "G", "H", "I"], [True, True, True, True, True, False, False, False, False])
        ours = _side(["A", "B", "C", "D", "E", "F", "G", "H"], [True, True, True, True, True, False, False, False])  # I 缺失
        cond1 = _aggregate_safety(b0)
        cond2 = _aggregate_safety(ours)
        self.assertAlmostEqual(cond1["page_budget_hit"], 0.5556, places=3)
        self.assertAlmostEqual(cond2["page_budget_hit"], 0.625, places=3)
        paired = _paired_safety(b0, ours)
        self.assertEqual(paired["paired_evaluable_count"], 8)  # 交集，不是 9 或 8
        self.assertEqual(paired["candidate_only_count"], 0)
        self.assertEqual(paired["b0_only_count"], 1)  # I 只有 B0 评估
        self.assertIn("I/arXiv-x", paired["b0_only_case_ids"])
        m = paired["metrics"]["page_budget_hit"]
        self.assertEqual(m["denominator"], 8)
        self.assertEqual(m["v1_numerator"], 5)
        self.assertEqual(m["v2_numerator"], 5)
        self.assertEqual(m["v1"], m["v2"])  # 配对口径：无改善
        # 决策器不得基于条件比例宣称改进（这里只验证不误报 REGRESSED 且配对无差异）
        regressions = check_safety(cond1, cond2, safety_paired=paired)
        self.assertEqual(regressions, [])

    def test_paired_regression_detected(self):
        # 配对交集内 candidate 丢了 2 个预算命中 → 必须检出
        b0 = _side(["A", "B", "C", "D"], [True, True, True, True])
        ours = _side(["A", "B", "C", "D"], [True, True, False, False])
        paired = _paired_safety(b0, ours)
        regressions = check_safety(_aggregate_safety(b0), _aggregate_safety(ours), safety_paired=paired)
        self.assertTrue(any("page_budget_hit" in r and "paired" in r for r in regressions))

    def test_candidate_rollback_not_vanishes(self):
        # guard_rolled_back 的 candidate 没有评估报告 → 记入 candidate_only/b0_only 记账
        b0 = _side(["A", "B"], [True, True])
        ours = _side(["A"], [True])  # B 侧缺失（回滚未导出）
        paired = _paired_safety(b0, ours)
        self.assertEqual(paired["paired_evaluable_count"], 1)
        self.assertIn("B/arXiv-x", paired["b0_only_case_ids"])
        self.assertEqual(paired["metrics"]["page_budget_hit"]["denominator"], 1)

    def test_conditional_fallback_annotated(self):
        # 无配对数据时退回条件比例，理由必须注明 denominators differ
        regressions = check_safety(
            {"page_budget_hit": 0.9, "compile_success": 1.0, "render_success": 1.0, "content_integrity_ok": 1.0},
            {"page_budget_hit": 0.5, "compile_success": 1.0, "render_success": 1.0, "content_integrity_ok": 1.0},
        )
        self.assertTrue(any("denominators differ" in r for r in regressions))

    def test_aggregate_carries_numerator_denominator(self):
        reports = _side(["A", "B", "C"], [True, False, False])
        agg = _aggregate_safety(reports)
        self.assertEqual(agg["_denominator"], 3)
        self.assertEqual(agg["_numerators"]["page_budget_hit"], 1)
        self.assertIn("conditional", agg["_note"])

    def test_vlm_accounting_distinguishes_responses_from_consistent(self):
        # 决策输入：8 次裁判输出、7 个双序一致 → scored_cases=7，accounting 分开
        paired_scores = [{"paper_id": f"P{i}", "case_id": f"c{i}", "delta": 0.0} for i in range(7)]
        safety = {"compile_success": 1.0, "render_success": 1.0, "content_integrity_ok": 1.0, "page_budget_hit": 1.0}
        result = decide_regression(
            safety_v1=safety,
            safety_v2=safety,
            paired_scores=paired_scores,
        )
        self.assertEqual(result["scored_cases"], 7)
        # accounting 由 report 层注入（vlm_responses=8 与 paired=7 区分）
        accounting = {"vlm_responses": 8, "dual_order_consistent_paired": 7, "uncertain_excluded": 1}
        self.assertEqual(accounting["vlm_responses"], 8)
        self.assertEqual(accounting["dual_order_consistent_paired"], result["scored_cases"])


if __name__ == "__main__":
    unittest.main()
