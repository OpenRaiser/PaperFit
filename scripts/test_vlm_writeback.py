"""Regression checks."""

import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from regression.mvp_runner import _apply_vlm_pair_results  # noqa: E402


def _report(case_id: str, winner: str, repair_score):
    return {
        "case_id": case_id,
        "vlm": {"winner": winner, "repair_score": repair_score, "gated_v3_score": 4},
    }


class TestVlmWriteback(unittest.TestCase):
    def _run(self, normal, swapped, common):
        records = [
            {"paper_id": "IJCV", "case_id": "arXiv-a", "vlm": {"reconciled": None}},
            {"paper_id": "ICLR", "case_id": "arXiv-b", "vlm": {"reconciled": None}},
        ]
        index = {(str(r["paper_id"]), str(r["case_id"])): r for r in records}
        paired = []
        uncertain = _apply_vlm_pair_results(
            normal_reports=normal,
            swapped_reports=swapped,
            common_cases=common,
            case_records=records,
            records_by_case=index,
            paired=paired,
        )
        return records, paired, uncertain

    def test_writeback_uses_composite_key(self):
        # 评测 case_id = "IJCV/arXiv-a"；records 的 case_id = "arXiv-a"
        records, paired, uncertain = self._run(
            normal=[_report("IJCV/arXiv-a", "candidate", 2)],
            swapped=[_report("IJCV/arXiv-a", "candidate", 2)],
            common=["IJCV/arXiv-a"],
        )
        self.assertEqual(records[0]["vlm"]["reconciled"], "candidate")
        self.assertEqual(len(paired), 1)
        self.assertEqual(uncertain, [])

    def test_delta_is_mean_repair_score_not_gated_minus_3(self):
        # 双序 repair_score = 2 和 1 → delta = 1.5（旧实现会是 (4+4)/2-3.0 = 1.0）
        records, paired, _ = self._run(
            normal=[_report("IJCV/arXiv-a", "candidate", 2)],
            swapped=[_report("IJCV/arXiv-a", "candidate", 1)],
            common=["IJCV/arXiv-a"],
        )
        self.assertEqual(paired[0]["delta"], 1.5)

    def test_conflicting_order_excluded_from_ci(self):
        # 双序裁决相反 → position bias，不得以 delta=0 进入 CI
        records, paired, uncertain = self._run(
            normal=[_report("IJCV/arXiv-a", "candidate", 2)],
            swapped=[_report("IJCV/arXiv-a", "b0", -1)],
            common=["IJCV/arXiv-a"],
        )
        self.assertEqual(paired, [])
        self.assertEqual(len(uncertain), 1)
        self.assertNotIn("delta", uncertain[0])
        # 回写仍然记录 uncertain 裁决，报告可见
        self.assertEqual(records[0]["vlm"]["reconciled"], "uncertain")

    def test_missing_score_excluded(self):
        records, paired, uncertain = self._run(
            normal=[_report("IJCV/arXiv-a", "candidate", None)],
            swapped=[_report("IJCV/arXiv-a", "candidate", 1)],
            common=["IJCV/arXiv-a"],
        )
        self.assertEqual(paired, [])
        self.assertEqual(len(uncertain), 1)

    def test_writeback_miss_is_auditable(self):
        # records 里没有对应 case 时不得静默丢失（stderr 提示）。
        # 夹具说明：IJCV/arXiv-ZZ 是假 case，records 里只有 IJCV/arXiv-a 与
        # ICLR/arXiv-b 两个真实 case，复合键必然 miss——本测试要审计的正是
        # 这条 miss 路径。stderr 在夹具内捕获断言，不再漏到测试运行日志。
        with contextlib.redirect_stderr(io.StringIO()) as captured:
            records, paired, uncertain = self._run(
                normal=[_report("IJCV/arXiv-ZZ", "candidate", 2)],
                swapped=[_report("IJCV/arXiv-ZZ", "candidate", 2)],
                common=["IJCV/arXiv-ZZ"],
            )
        self.assertEqual(records[0]["vlm"]["reconciled"], None)  # 未被误写
        self.assertEqual(len(paired), 1)  # 统计仍然完整
        # miss 审计信息必须真实写出且指名假 case，而不是污染测试输出
        self.assertIn("VLM write-back miss", captured.getvalue())
        self.assertIn("IJCV/arXiv-ZZ", captured.getvalue())


if __name__ == "__main__":
    unittest.main()
