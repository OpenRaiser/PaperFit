"D1 安全守卫 + D2 amsmath 保障的真实回归测试。"

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from overflow_fixers import (  # noqa: E402
    _fix_long_token_with_breaks,
    fix_equation_overflow,
)

BS = "\\"
LONG = "PerturbationStressToken" + "X" * 50
PATH_TOKEN = "figures/num_judge_plot_results"
CITE_TOKEN = "Wang2024HelpSteer2PreferenceCRForAligning"
EQ_BODY = (
    BS + "mathcal{L}_{disturb} = x_{" + BS + "mathrm{BenchmarkOverflowSequenceAlphaBetaGammaDeltaEpsilon}}"
    + " + y_{" + BS + "mathrm{AnotherVeryLongSubscriptSequenceForTesting}}" 
)


class D1GuardTest(unittest.TestCase):
    def _doc(self, line: str) -> str:
        return BS + "documentclass{article}\n" + BS + "begin{document}\n" + line + "\n" + BS + "end{document}\n"

    def test_plain_body_token_still_fixed(self):
        doc = self._doc("text " + LONG + " trailing words")
        r = _fix_long_token_with_breaks(doc, "in paragraph", 3)
        self.assertIsNotNone(r)
        self.assertIn(BS + "allowbreak", r[0])

    def test_includegraphics_path_rejected(self):
        doc = self._doc(
            BS + "includegraphics[width=1.28" + BS + "linewidth]{" + PATH_TOKEN + ".pdf}"
        )
        r = _fix_long_token_with_breaks(doc, "in paragraph", 3)
        self.assertIsNone(r)

    def test_cite_key_rejected(self):
        doc = self._doc("see " + BS + "citep{" + CITE_TOKEN + "} for details")
        r = _fix_long_token_with_breaks(doc, "in paragraph", 3)
        self.assertIsNone(r)

    def test_label_key_rejected(self):
        doc = self._doc(BS + "label{fig:" + CITE_TOKEN + "}")
        r = _fix_long_token_with_breaks(doc, "in paragraph", 3)
        self.assertIsNone(r)

    def test_display_math_rejected(self):
        doc = self._doc(
            BS + "begin{equation}\nx_{" + BS + "mathrm{" + LONG + "}}\n" + BS + "end{equation}"
        )
        r = _fix_long_token_with_breaks(doc, "in paragraph", 4)
        self.assertIsNone(r)

    def test_bracketed_command_rejected(self):
        # includegraphics 带 [options] 的形态也必须被守卫识别
        doc = self._doc(
            BS + "includegraphics[trim={0.3cm 1cm}, clip, width=1.3" + BS + "linewidth]{" + PATH_TOKEN + ".pdf}"
        )
        r = _fix_long_token_with_breaks(doc, "in paragraph", 3)
        self.assertIsNone(r)


class D2AmsmathGuardTest(unittest.TestCase):
    def _doc(self, with_amsmath: bool) -> str:
        pkg = (BS + "usepackage{amsmath}\n") if with_amsmath else ""
        return (
            BS + "documentclass{article}\n"
            + pkg
            + BS + "begin{document}\n"
            + BS + "begin{equation}\n" + EQ_BODY + "\n" + BS + "end{equation}\n"
            + BS + "end{document}\n"
        )

    def test_amsmath_injected_when_missing(self):
        doc = self._doc(with_amsmath=False)
        modified, fix = fix_equation_overflow(doc, line_number=4)
        self.assertIsNotNone(fix)
        self.assertIn(BS + "usepackage{amsmath}", modified)
        # 重写体使用 amsmath 环境（multline/aligned 之一）
        self.assertTrue(
            BS + "begin{multline}" in modified or BS + "begin{aligned}" in modified
        )

    def test_amsmath_not_duplicated_when_present(self):
        doc = self._doc(with_amsmath=True)
        modified, fix = fix_equation_overflow(doc, line_number=5)
        self.assertIsNotNone(fix)
        self.assertEqual(modified.count(BS + "usepackage{amsmath}"), 1)

    def test_mathtools_counts_as_loaded(self):
        doc = self._doc(with_amsmath=False).replace(
            BS + "documentclass{article}\n",
            BS + "documentclass{article}\n" + BS + "usepackage{mathtools}\n",
        )
        modified, fix = fix_equation_overflow(doc, line_number=5)
        self.assertIsNotNone(fix)
        self.assertNotIn(BS + "usepackage{amsmath}", modified)

    def test_rewrite_preserves_equation_content_tokens(self):
        # 数学内容不丢：分块后每个子块仍是重写体的子串，字符集合守恒
        doc = self._doc(with_amsmath=False)
        modified, fix = fix_equation_overflow(doc, line_number=4)
        self.assertIsNotNone(fix)
        flat = modified.replace(BS, "").replace(" ", "").replace("\n", "")
        for chunk in ("BenchmarkOverflowSequenceAlpha", "BetaGammaDeltaEpsilon",
                      "AnotherVeryLongSubscript", "SequenceForTesting"):
            self.assertIn(chunk, flat)
        self.assertIn("mathcal{L}_{disturb}", flat)


if __name__ == "__main__":
    unittest.main()
