"验收：溢出对象化、严重度排序、token 分类、锚点置信度。"

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from overflow_objects import (  # noqa: E402
    build_overflow_objects,
    severe_overflow_count,
    total_overflow_pt,
)


TEX = (
    "\\documentclass{article}\n\\begin{document}\n"
    "Normal text at line three.\n"
    "PerturbationStressToken" + "X" * 50 + " end\n"          # L4: long_token, 173pt
    "\\begin{equation}\n\\mathcal{L}_{big} = x + y + z\n\\end{equation}\n"  # L5-7: display_math, 738pt
    "See https://example.com/a/very/long/url/that/overflows/the/column/line end\n"  # L8: url
    "Text $a + b + c + d + e + f$ inline math overflow here\n"  # L9: inline_math
    "\\end{document}\n"
)


def _rule_report(warnings):
    return {
        "log_file": None,
        "summary": {"overfull_hbox_total": len(warnings)},
        "overfull_hbox": warnings,
    }


def _warning(line, pt, page=1):
    return {"overflow_pt": pt, "lines": f"{line}--{line}", "page": page, "subtype": "paragraph", "context": "..."}


class BuildOverflowObjectsTest(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "main.tex").write_text(TEX, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _build(self, warnings):
        return build_overflow_objects(_rule_report(warnings), self.root, "main.tex")

    def test_738pt_ranks_above_1pt(self):
        objs = self._build([_warning(4, 1.0), _warning(6, 738.6)])
        self.assertEqual(objs[0].width_pt, 738.6)
        self.assertEqual(objs[0].severity, "critical")
        self.assertGreater(objs[0].severity_score, objs[1].severity_score)

    def test_zone_weight_boosts_math_and_table(self):
        # 同样 50pt：display_math 区域 > body 区域
        objs = self._build([_warning(3, 50.0), _warning(6, 50.0)])
        by_zone = {o.zone: o for o in objs}
        self.assertGreater(by_zone["display_math"].severity_score, by_zone["body"].severity_score)

    def test_token_type_classification(self):
        objs = self._build([
            _warning(4, 173.0),   # long token
            _warning(6, 738.0),   # display math
            _warning(8, 30.0),    # url
            _warning(9, 20.0),    # inline math
        ])
        types = {o.overflow_id: o.token_type for o in objs}
        all_types = {o.token_type for o in objs}
        self.assertIn("long_token", all_types)
        self.assertIn("display_math", all_types)
        self.assertIn("url_or_doi", all_types)
        self.assertIn("inline_math", all_types)

    def test_token_text_is_truncated_summary(self):
        objs = self._build([_warning(4, 173.0)])
        self.assertLessEqual(len(objs[0].token_text), 80)

    def test_anchor_fields_present(self):
        objs = self._build([_warning(4, 173.0)])
        top = objs[0]
        self.assertEqual(top.source_file, "main.tex")
        self.assertEqual(top.source_line, 4)
        self.assertEqual(top.source_anchor["start_line"], 4)
        # 文件存在且行号有效 → 置信度 1.0，severity_score == width_pt × zone 权重
        self.assertGreaterEqual(top.severity_score, top.width_pt * 0.99)

    def test_severe_and_total_helpers(self):
        objs = self._build([_warning(6, 738.6), _warning(4, 173.1), _warning(3, 12.0), _warning(9, 1.0)])
        self.assertEqual(severe_overflow_count(objs), 2)
        self.assertAlmostEqual(total_overflow_pt(objs), 924.7, places=1)

    def test_no_warnings_returns_empty(self):
        self.assertEqual(self._build([]), [])


if __name__ == "__main__":
    unittest.main()
