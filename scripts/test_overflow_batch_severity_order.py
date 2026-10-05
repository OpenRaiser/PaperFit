"回归：同优先级 D 族候选必须按溢出量降序进批次。"

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from repair_plan_executor import (  # noqa: E402
    _build_overflow_defects,
    _build_space_util_defects,
    _has_catastrophic_overflow_candidates,
)
from overflow_fixers import (  # noqa: E402
    _fix_long_token_with_breaks,
    _insert_safe_breaks_in_token,
)


MAIN_TEX = r"""\documentclass{article}
\begin{document}
Text paragraph at line one hundred fifty four with some content.
More text at line two twenty three.
\begin{equation}
\mathcal{L}_{disturb} = x_{long} + y_{long} + z_{long}
\end{equation}
\end{document}
"""


def _candidate(line_number: int, pt: float) -> dict:
    return {
        "candidate_type": "log_warning",
        "page": None,
        "target": {"scope": "overflow"},
        "defect_family": "D1",
        "priority_score": 95,
        "severity": "major",
        "proposed_action": "repair_overfull_boxes",
        "description": f"context for line {line_number}",
        "overflow_amount": pt,
        "line_number": line_number,
        "subtype": "paragraph",
    }


class TestOverflowBatchSeverityOrder(unittest.TestCase):
    def test_heaviest_overflow_enters_limited_batch_first(self):
        plan = {
            "candidates": [
                _candidate(154, 173.1),
                _candidate(223, 94.1),
                _candidate(336, 269.2),
                _candidate(354, 738.6),
            ]
        }
        # 批次上限 2：必须拿到 738.6pt 和 269.2pt，而不是行号靠前的两个
        defects = _build_overflow_defects(plan, main_tex="main.tex", max_candidates=2)
        amounts = [d.get("overflow_amount") for d in defects]
        self.assertEqual(len(defects), 2)
        self.assertEqual(amounts, [738.6, 269.2])

    def test_display_math_candidate_converted_to_d2(self):
        # 738pt 公式在 display math 内 → 必须被改判为 D2 equation_overflow
        lines = MAIN_TEX.splitlines()
        eq_line = next(i + 1 for i, l in enumerate(lines) if "mathcal" in l)
        plan = {"candidates": [_candidate(154, 173.1), _candidate(eq_line, 738.6)]}
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            tex = Path(td) / "main.tex"
            tex.write_text(MAIN_TEX, encoding="utf-8")
            defects = _build_overflow_defects(plan, main_tex=str(tex), max_candidates=2)
        self.assertEqual(defects[0]["defect_id"], "D2")
        self.assertEqual(defects[0]["object"], "equation_overflow")
        self.assertEqual(defects[0]["overflow_amount"], 738.6)


class TestLongTokenFixes(unittest.TestCase):
    "Regression checks."

    def test_interval_chunking_breaks_boundaryless_runs(self):
        # 56 连 X 没有任何自然边界：必须按固定间隔补断点，否则无法回行
        token = "PerturbationStressToken" + "X" * 56
        breakable = _insert_safe_breaks_in_token(token)
        self.assertNotEqual(breakable, token)
        # 去掉断点标记后，任意连续片段 ≤ 12 字符
        for chunk in breakable.replace("\\allowbreak ", "\n").split("\n"):
            self.assertLessEqual(len(chunk), 12, f"chunk too long: {chunk}")

    def test_source_window_preferred_over_log_context(self):
        # 日志 context 在 79 列折行，token 被截断；源码窗口必须拿到完整 token
        tex = (
            "prefix text\n"
            "\\label{sec:framework} BenchmarkOverflowTokenWithoutBreakpoints"
            "AntibodyDesignerLayoutRegressionSequenceColumnWidthStressCase\n"
            "suffix text\n"
        )
        truncated_context = "BenchmarkOverflowTokenWithoutBreakpointsAntibodyDesignerLayoutR"
        result = _fix_long_token_with_breaks(tex, truncated_context, line_number=2)
        self.assertIsNotNone(result)
        modified = result[0]
        self.assertIn("\\allowbreak", modified)
        # 完整 token（101 字符）被替换到末尾（Case），而不是 63 字符的折行前缀
        self.assertIn("Case", modified)
        self.assertIn("Regression", modified)
        self.assertNotIn("RegressionSequenceColumnWidthStressCase", modified)

    def test_math_parity_guard_no_false_positive(self):
        # 前文出现过 $...$ 不代表 token 在数学环境内：配对 $ 后的正文 token 必须可修
        tex = "math $a + b$ here\nplain text PerturbationStressToken" + "X" * 40 + " end\n"
        result = _fix_long_token_with_breaks(tex, "in paragraph", line_number=2)
        self.assertIsNotNone(result)
        self.assertIn("\\allowbreak", result[0])

    def test_unclosed_math_still_guarded(self):
        # 真正处于行内数学（$ 未闭合）内的 token 仍然跳过
        tex = "open math $x + PerturbationStressToken" + "X" * 40 + "\n"
        result = _fix_long_token_with_breaks(tex, "in paragraph", line_number=1)
        self.assertIsNone(result)


class TestCatastrophicOverflowPreemption(unittest.TestCase):
    "Regression checks."

    def test_catastrophic_detected(self):
        plan = {"candidates": [{"defect_family": "D1", "overflow_amount": 100.0}]}
        self.assertTrue(_has_catastrophic_overflow_candidates(plan))

    def test_below_threshold_not_catastrophic(self):
        plan = {"candidates": [{"defect_family": "D1", "overflow_amount": 99.9}]}
        self.assertFalse(_has_catastrophic_overflow_candidates(plan))

    def test_a4_skipped_for_single_column(self):
        plan = {
            "candidates": [
                {"defect_family": "A4", "target": {"scope": "last_page"}, "height_difference": 0.2},
                {"defect_family": "A2", "target": {"scope": "tail_whitespace"}, "whitespace_ratio": 0.4},
            ]
        }
        single = _build_space_util_defects(plan, max_candidates=3, column_type="single")
        self.assertEqual([d["defect_id"] for d in single], ["A2"])
        double = _build_space_util_defects(plan, max_candidates=3, column_type="double")
        self.assertEqual([d["defect_id"] for d in double], ["A4", "A2"])


if __name__ == "__main__":
    unittest.main()
