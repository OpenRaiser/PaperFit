from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from float_fixers import fix_split_table, fix_wide_float_in_double_column  # noqa: E402


TWO_FIGURE_TEX = """\\documentclass{article}
\\begin{document}
\\begin{figure}[ht]
  \\includegraphics{a.png}
  \\caption{First figure}
  \\label{fig:one}
\\end{figure}
\\begin{figure}[ht]
  \\includegraphics{b.png}
  \\caption{Second figure}
  \\label{fig:two}
\\end{figure}
\\end{document}
"""

SPLIT_TABLE_TEX = """\\documentclass{article}
\\begin{document}
\\begin{table}[h]
\\centering
\\caption{A table that splits}
\\label{tab:split}
\\begin{tabular}{|l|c|c|}
\\hline
A & B & C \\\\
\\hline
1 & 2 & 3 \\\\
\\hline
4 & 5 & 6 \\\\
\\hline
\\end{tabular}
\\end{table}
\\end{document}
"""


class WideFloatTargetingTest(unittest.TestCase):
    def _blocks(self, tex: str) -> tuple[str, str]:
        first_end = tex.index("\\label{fig:one}")
        second_begin = tex.index("\\begin{figure", first_end)
        return tex[:first_end], tex[second_begin:]

    def test_only_labeled_figure_is_promoted_to_star(self) -> None:
        updated, result = fix_wide_float_in_double_column(TWO_FIGURE_TEX, "fig:two")
        self.assertIsNotNone(result)
        self.assertTrue(result.success)
        first_block, second_block = self._blocks(updated)
        self.assertIn("\\begin{figure}[ht]", first_block)
        self.assertNotIn("\\begin{figure*}", first_block)
        self.assertIn("\\begin{figure*}[ht]", second_block)
        self.assertIn("\\end{figure*}", second_block)
        self.assertEqual(updated.count("\\begin{figure*}"), 1)
        self.assertEqual(updated.count("\\end{figure*}"), 1)

    def test_first_figure_can_still_be_targeted(self) -> None:
        updated, result = fix_wide_float_in_double_column(TWO_FIGURE_TEX, "fig:one")
        self.assertIsNotNone(result)
        self.assertTrue(result.success)
        self.assertEqual(updated.count("\\begin{figure*}"), 1)
        first_block, second_block = self._blocks(updated)
        self.assertIn("\\begin{figure*}[ht]", first_block)
        self.assertIn("\\begin{figure}[ht]", second_block)
        self.assertNotIn("\\end{figure*}", second_block)

    def test_unknown_label_is_a_noop(self) -> None:
        updated, result = fix_wide_float_in_double_column(TWO_FIGURE_TEX, "fig:missing")
        self.assertIsNone(result)
        self.assertEqual(updated, TWO_FIGURE_TEX)


class SplitTableMulticolumnTest(unittest.TestCase):
    def test_multicolumn_uses_real_column_count(self) -> None:
        updated, result = fix_split_table(SPLIT_TABLE_TEX, "tab:split")
        self.assertIsNotNone(result)
        self.assertTrue(result.success)
        # {|l|c|c|} has 3 columns, not the 7 characters the old code counted.
        self.assertIn(r"\multicolumn{3}{r}{Continued on next page}", updated)
        self.assertNotIn(r"\multicolumn{7}", updated)
        self.assertIn("\\begin{longtable}{|l|c|c|}", updated)


if __name__ == "__main__":
    unittest.main()
