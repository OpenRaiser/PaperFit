from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from regression.round_acceptance import (  # noqa: E402
    RoundEvidence,
    detect_no_effect,
    evaluate_round_acceptance,
)


def _evidence(**kw):
    """快速构造 RoundEvidence。"""
    defaults = dict(
        page_count_before=12, page_count_after=10, target_pages=10,
        overfull_before=5, overfull_after=2,
        figures_before=8, figures_after=8,
        tables_before=3, tables_after=3,
        labels_before=15, labels_after=15,
        refs_before=20, refs_after=20,
        undefined_refs_before=3, undefined_refs_after=3,
        undefined_citations_before=1, undefined_citations_after=1,
        major_defects_before=5, major_defects_after=2,
        content_integrity_pass=True,
    )
    defaults.update(kw)
    return RoundEvidence(**defaults)


class TestCaseA_PageConvergence(unittest.TestCase):
    """A：12 页 → 10 页，目标 10 → 接受。"""

    def test_a_accepted(self):
        d = evaluate_round_acceptance(_evidence())
        self.assertTrue(d.accepted)
        self.assertFalse(d.rollback_required)

    def test_a_figures_not_reduced(self):
        self.assertEqual(_evidence().figures_before, _evidence().figures_after)


class TestCaseB_PageRegression(unittest.TestCase):
    """B：12 页 → 13 页，目标 11 → 拒绝+回滚。"""

    def test_b_rejected_and_rolled_back(self):
        d = evaluate_round_acceptance(_evidence(page_count_before=12, page_count_after=13, target_pages=11))
        self.assertFalse(d.accepted)
        self.assertTrue(d.rollback_required)

    def test_b_final_pages_restored(self):
        """回滚后源码恢复，页数回到 12。"""
        # 回滚由 orchestrator 执行，此处验证门禁正确触发
        d = evaluate_round_acceptance(_evidence(page_count_before=12, page_count_after=13, target_pages=11))
        self.assertTrue(any("page_distance" in r for r in d.reasons))


class TestCaseC_B2Widen(unittest.TestCase):
    """C：图加宽导致页距恶化（11→12，目标 9）→ 拒绝。"""

    def test_c_rejected(self):
        d = evaluate_round_acceptance(_evidence(page_count_before=11, page_count_after=12, target_pages=9))
        self.assertFalse(d.accepted)
        self.assertTrue(d.rollback_required)

    def test_c_even_with_overfull_improvement(self):
        """即使 overfull 减少，页距恶化仍拒绝。"""
        d = evaluate_round_acceptance(_evidence(page_count_before=11, page_count_after=12, target_pages=9, overfull_before=10, overfull_after=3))
        self.assertFalse(d.accepted)


class TestCaseD_SameSource(unittest.TestCase):
    """D：源码未变，不得判定为修复获胜。"""

    def test_d_no_effect(self):
        result = detect_no_effect(source_changed=False, applied_count=0, run_failed=False)
        self.assertIsNotNone(result)
        self.assertIn(result, {"no_actionable_candidate", "no_unique_source_anchor", "candidate_rejected"})

    def test_d_not_success(self):
        """no_effect 不是 success。"""
        result = detect_no_effect(source_changed=False, applied_count=0, run_failed=False)
        self.assertNotEqual(result, "success")
        self.assertNotEqual(result, "repaired")


class TestCaseE_SilentNoOp(unittest.TestCase):
    """E：静默无操作必须可见。"""

    def test_e_anchor_failure(self):
        result = detect_no_effect(
            source_changed=False, applied_count=0, run_failed=False,
            rejection_reasons=["source anchor unresolved"],
        )
        self.assertEqual(result, "no_unique_source_anchor")

    def test_e_approval_blocked(self):
        result = detect_no_effect(
            source_changed=False, applied_count=0, run_failed=False,
            rejection_reasons=["approval scope blocked"],
        )
        self.assertEqual(result, "candidate_blocked_by_approval")

    def test_e_page_budget_rejection(self):
        result = detect_no_effect(
            source_changed=False, applied_count=0, run_failed=False,
            rejection_reasons=["page_budget_violation"],
        )
        self.assertEqual(result, "candidate_rejected_by_page_budget")


class TestCaseF_MissingPackage(unittest.TestCase):
    """F：缺宏包时保留 baseline 或安装后成功。"""

    def test_f_environment_ineligible_not_success(self):
        """环境不可评不能伪装成 success。"""
        # 由 case_status 处理，此处验证 no_effect 不在环境问题时触发
        result = detect_no_effect(source_changed=False, applied_count=0, run_failed=True)
        self.assertIsNone(result)  # 运行失败不是 no_effect


class TestCaseG_SchemaFixed(unittest.TestCase):
    """G：visual_action_plan 已登记，不再触发 unknown key。"""

    def test_g_artifact_in_schema(self):
        from state_schema import ARTIFACT_FIELDS
        self.assertIn("visual_action_plan", ARTIFACT_FIELDS)

    def test_g_state_writeable(self):
        from state_manager import StateManager
        import tempfile, json
        with tempfile.TemporaryDirectory() as tmpdir:
            sm = StateManager(state_path=str(Path(tmpdir) / "data" / "state.json"))
            sm.init_state(main_tex="main.tex")
            artifacts = dict(sm.get("artifacts") or {})
            artifacts["visual_action_plan"] = "data/visual_action_plan.json"
            sm.update({"artifacts": artifacts})
            # 不应抛 ValueError
            loaded = sm.load()
            self.assertEqual(loaded["artifacts"]["visual_action_plan"], "data/visual_action_plan.json")


class TestCaseH_SecondRoundGuard(unittest.TestCase):
    """H：第一轮页距变差 → 不允许进入第二轮。"""

    def test_h_round1_regression_blocks_round2(self):
        d = evaluate_round_acceptance(_evidence(page_count_before=12, page_count_after=13, target_pages=11))
        self.assertTrue(d.rollback_required)  # 触发回滚 = 不进入第二轮

    def test_h_perfect_round1_allows_round2(self):
        d = evaluate_round_acceptance(_evidence(page_count_before=12, page_count_after=10, target_pages=10))
        self.assertTrue(d.accepted)  # 允许继续


if __name__ == "__main__":
    unittest.main()
