from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from math_safe_breaking import (  # noqa: E402
    _find_matching_brace,
    _find_protected_spans,
    _is_protected,
    count_math_tokens,
    split_safe_additive_terms,
    verify_split_preserves_tokens,
)


class ProtectedSpansTest(unittest.TestCase):
    def test_left_right_pair_protected(self):
        expr = r"a + \left( b + c \right) + d"
        spans = _find_protected_spans(expr)
        self.assertTrue(len(spans) >= 1)
        # \\left(...\\right) 内的 "+" 不允许切分
        inner_plus = expr.index("+", expr.index("\\left"))  # 第一个内部 +
        self.assertTrue(_is_protected(inner_plus, spans))
        # \\left 前面的 "+" 允许切分
        outer_plus = expr.index("+")
        self.assertFalse(_is_protected(outer_plus, spans))

    def test_frac_args_protected(self):
        expr = r"x + \frac{a + b}{c + d} + y"
        spans = _find_protected_spans(expr)
        # \\frac{a + b}{c + d} 整体是一个保护区间
        frac_start = expr.index("\\frac")
        frac_end = expr.index("}", expr.index("}", expr.index("{", frac_start)) + 1) + 1
        for pos in range(frac_start, frac_end):
            self.assertTrue(_is_protected(pos, spans), f"pos {pos} inside frac should be protected")

    def test_subscript_protected(self):
        expr = r"a_{i + j} + b"
        spans = _find_protected_spans(expr)
        inner_plus = expr.index("+")
        self.assertTrue(_is_protected(inner_plus, spans))

    def test_unclosed_left_protects_to_end(self):
        expr = r"a + \left( b + c"
        spans = _find_protected_spans(expr)
        self.assertTrue(_is_protected(len(expr) - 1, spans))

    def test_matching_brace(self):
        self.assertEqual(_find_matching_brace("{abc{def}ghi}", 0), 12)
        self.assertEqual(_find_matching_brace("{abc", 0), -1)

    def test_overlapping_spans_merged(self):
        expr = r"\frac{\left(a + b\right)}{c}"
        spans = _find_protected_spans(expr)
        # 合并后应该是一个连续区间或至少不重叠
        for i in range(len(spans) - 1):
            self.assertLess(spans[i][1], spans[i + 1][0])


class SafeSplitTest(unittest.TestCase):
    def test_simple_addition_splits(self):
        expr = "a + b + c + d + e"
        terms = split_safe_additive_terms(expr)
        self.assertEqual(len(terms), 5)

    def test_left_right_does_not_split_inside(self):
        expr = r"a + \left(b + c\right) + d + e"
        terms = split_safe_additive_terms(expr)
        # \\left(b + c\\right) 内部 "+" 不切分 → a / \\left...\\right / d / e = 4 项
        self.assertEqual(len(terms), 4)
        self.assertTrue(any(r"\left(b + c\right)" in t for t in terms))

    def test_frac_does_not_split_inside(self):
        expr = r"x + \frac{a + b}{c} + y + z"
        terms = split_safe_additive_terms(expr)
        # \\frac{a + b}{c} 保持完整
        self.assertEqual(len(terms), 4)
        self.assertTrue(any("\\frac{a + b}{c}" in t for t in terms))

    def test_subscript_does_not_split_inside(self):
        expr = r"a_{m + n} + b + c"
        terms = split_safe_additive_terms(expr)
        self.assertEqual(len(terms), 3)
        self.assertIn("a_{m + n}", terms[0])

    def test_mixed_protection(self):
        expr = r"\alpha + \frac{\beta + \gamma}{\delta} + \left(\epsilon + \zeta\right) + \eta"
        terms = split_safe_additive_terms(expr)
        self.assertEqual(len(terms), 4)
        self.assertTrue(any("\\frac{\\beta + \\gamma}{\\delta}" in t for t in terms))
        self.assertTrue(any("\\left(\\epsilon + \\zeta\\right)" in t for t in terms))


class TokenVerificationTest(unittest.TestCase):
    def test_simple_split_preserves_tokens(self):
        original = "a + b + c + d"
        terms = split_safe_additive_terms(original)
        self.assertTrue(verify_split_preserves_tokens(original, terms))

    def test_frac_split_preserves_tokens(self):
        original = r"x + \frac{a}{b} + y"
        terms = split_safe_additive_terms(original)
        self.assertTrue(verify_split_preserves_tokens(original, terms))

    def test_left_right_split_preserves_tokens(self):
        original = r"\alpha + \left(\beta + \gamma\right) + \delta"
        terms = split_safe_additive_terms(original)
        self.assertTrue(verify_split_preserves_tokens(original, terms))

    def test_content_loss_detected(self):
        original = r"x + \frac{a}{b} + y + z + w"
        # 模拟内容丢失：只保留部分项
        truncated = split_safe_additive_terms(original)[:2]
        self.assertFalse(verify_split_preserves_tokens(original, truncated))

    def test_changed_additive_sign_rejected(self):
        self.assertFalse(verify_split_preserves_tokens("a + b - c", ["a", "- b", "+ c"]))

    def test_missing_additive_operator_rejected(self):
        self.assertFalse(verify_split_preserves_tokens("a + b + c", ["a", "b", "+ c"]))

    def test_unary_minus_preserved(self):
        original = "-a + b - c"
        self.assertTrue(verify_split_preserves_tokens(original, split_safe_additive_terms(original)))
        self.assertFalse(verify_split_preserves_tokens(original, ["a", "+ b", "- c"]))

    def test_sign_inside_fraction_is_not_layout(self):
        self.assertFalse(verify_split_preserves_tokens(r"x + \frac{a-b}{c}", ["x", r"+ \frac{a+b}{c}"]))

    def test_math_token_extraction(self):
        tokens = count_math_tokens(r"\alpha + \beta_1 = 3.14")
        self.assertIn("\\alpha", tokens)
        self.assertIn("\\beta", tokens)
        self.assertIn("3.14", tokens)


if __name__ == "__main__":
    unittest.main()
