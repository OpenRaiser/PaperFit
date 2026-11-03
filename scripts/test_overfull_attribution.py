from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from overfull_attribution import (  # noqa: E402
    REPAIRABLE_THRESHOLD_PT,
    VISIBLE_THRESHOLD_PT,
    _classify_object_type,
    attribute_overfull,
    parse_overfull_warnings,
)


TEX = """\\documentclass{article}
\\usepackage{graphicx}
\\begin{document}
Short paragraph.

\\begin{equation}
f(x) = a + b + c
\\end{equation}

\\begin{table}
\\begin{tabular}{ll}
a & b \\\\
\\end{tabular}
\\end{table}

ThisIsAVeryLongIdentifierThatCannotBeBroken = 42

\\url{https://example.com/very/long/path/that/overflows}

\\begin{thebibliography}{99}
\\bibitem{x} Author. Title. 2024.
\\end{thebibliography}
\\end{document}
"""


LOG = """Output written on main.pdf (9 pages).
Overfull \\hbox (12.34pt too wide) at lines 8--10
Overfull \\hbox (5.67pt too wide) detected at line 12
Overfull \\hbox (1.23pt too wide) at lines 16--17
Overfull \\hbox (15.00pt too wide) at lines 19--20
Overfull \\hbox (8.90pt too wide) at lines 22--23
Overfull \\hbox (3.45pt too wide) at lines 26--27
"""


class ParseWarningsTest(unittest.TestCase):
    def test_parses_overflow_pt_and_lines(self):
        warnings = parse_overfull_warnings(LOG)
        self.assertEqual(len(warnings), 6)
        self.assertAlmostEqual(warnings[0]["overflow_pt"], 12.34)
        self.assertEqual(warnings[0]["line_start"], 8)

    def test_handles_detected_at_line(self):
        warnings = parse_overfull_warnings(LOG)
        self.assertEqual(warnings[1]["line_start"], 12)

    def test_empty_log(self):
        self.assertEqual(parse_overfull_warnings(""), [])


class ClassifyObjectTypeTest(unittest.TestCase):
    """用独立小文档测分类（避免上下文窗口跨环境干扰）。"""

    def _classify(self, tex_snippet: str, target_line: int) -> str:
        # 构造刚好让 target_line 对准 snippet 的完整文档
        preamble = "\\documentclass{article}\n\\begin{document}\n"
        offset = preamble.count("\n")
        full = preamble + tex_snippet + "\n\\end{document}\n"
        return _classify_object_type(full, target_line + offset)

    def test_equation(self):
        self.assertEqual(
            self._classify("\\begin{equation}\nf(x) = a+b+c\n\\end{equation}", 1), "equation"
        )

    def test_table(self):
        self.assertEqual(
            self._classify("\\begin{table}\n\\begin{tabular}{ll}\na & b\n\\end{tabular}\n\\end{table}", 2), "table"
        )

    def test_long_token(self):
        self.assertEqual(
            self._classify("Some text with ThisIsAVeryLongIdentifierThatCannotBeBrokenAtAll = 42", 1), "long_token"
        )

    def test_url(self):
        self.assertEqual(
            self._classify("\\url{https://example.com/very/long/path}", 1), "url"
        )

    def test_bibliography(self):
        self.assertEqual(
            self._classify("\\begin{thebibliography}{99}\n\\bibitem{x} Author\n\\end{thebibliography}", 1), "bibliography"
        )

    def test_paragraph(self):
        self.assertEqual(
            self._classify("Just a normal paragraph with some text that wraps.", 1), "paragraph"
        )

    def test_unknown_line(self):
        self.assertEqual(_classify_object_type(TEX, 999), "unknown")
        self.assertEqual(_classify_object_type(TEX, None), "unknown")


class AttributeOverfullTest(unittest.TestCase):
    def test_full_attribution_pipeline(self):
        report = attribute_overfull(LOG, TEX, source_file="main.tex")
        self.assertEqual(report["raw_overfull_count"], 6)
        # 不同行号 → 不归并，数相等
        self.assertEqual(report["attributed_overfull_count"], 6)
        self.assertGreater(report["visible_overfull_count"], 0)
        self.assertGreater(report["repairable_overfull_count"], 0)
        self.assertGreater(report["weighted_overflow_score"], 0)

    def test_visibility_threshold(self):
        # 1.23pt < 2pt threshold → not visible
        report = attribute_overfull(
            "Overfull \\hbox (1.23pt too wide) at lines 4--5\n",
            TEX,
        )
        self.assertEqual(report["visible_overfull_count"], 0)

    def test_repairable_threshold(self):
        # 1.23pt < 5pt → not repairable
        report = attribute_overfull(
            "Overfull \\hbox (1.23pt too wide) at lines 4--5\n",
            TEX,
        )
        self.assertEqual(report["repairable_overfull_count"], 0)

    def test_merges_repeated_warnings(self):
        repeated_log = "\n".join(
            f"Overfull \\hbox ({10 + i}.00pt too wide) at lines 8--10" for i in range(5)
        )
        report = attribute_overfull(repeated_log, TEX)
        # 同一行 → 归并为 1 个
        self.assertEqual(report["attributed_overfull_count"], 1)
        self.assertEqual(report["attributions"][0]["repeated_count"], 5)

    def test_by_object_type_breakdown(self):
        report = attribute_overfull(LOG, TEX)
        self.assertIn("by_object_type", report)
        # 至少有一种类型被分类（上下文窗口跨环境，类型可能合并）
        types = report["by_object_type"]
        self.assertGreater(len(types), 0)

    def test_sorts_by_severity(self):
        report = attribute_overfull(LOG, TEX)
        pts = [a["overflow_pt"] for a in report["attributions"]]
        self.assertEqual(pts, sorted(pts, reverse=True))

    def test_empty_log(self):
        report = attribute_overfull("", TEX)
        self.assertEqual(report["raw_overfull_count"], 0)
        self.assertEqual(report["attributions"], [])


class ThresholdsTest(unittest.TestCase):
    def test_thresholds_are_sane(self):
        self.assertGreater(VISIBLE_THRESHOLD_PT, 0)
        self.assertGreater(REPAIRABLE_THRESHOLD_PT, VISIBLE_THRESHOLD_PT)


if __name__ == "__main__":
    unittest.main()
