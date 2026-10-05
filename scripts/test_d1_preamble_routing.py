from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from overflow_fixers import (  # noqa: E402
    _line_is_preamble_textheight_shrink,
    _remove_preamble_textheight_shrink,
    fix_overflow_defects,
)


TEX_WITH_DISTURBANCE = (
    "\\documentclass{article}\n"
    "\\usepackage{graphicx}\n"
    "\\title{Test}\n"
    "% DISTURBANCE:E2_template_page_budget_shift:BEGIN\n"
    "\\addtolength{\\textheight}{-8\\baselineskip}\n"
    "% DISTURBANCE:E2_template_page_budget_shift:END\n"
    "\\begin{document}\n"
    "\\maketitle\n"
    "Some content that overflows because the page got shorter.\n"
    "\\end{document}\n"
)

TEX_WITH_STANDALONE = (
    "\\documentclass{article}\n"
    "\\title{Test}\n"
    "\\addtolength{\\textheight}{-0.5in}\n"
    "\\begin{document}\n"
    "Content.\n"
    "\\end{document}\n"
)

TEX_NORMAL = (
    "\\documentclass{article}\n"
    "\\begin{document}\n"
    "Normal content with no textheight changes.\n"
    "\\end{document}\n"
)


class PreambleTextheightShrinkTest(unittest.TestCase):
    def test_detects_disturbance_line(self):
        # Line 5 = \addtolength inside the disturbance block
        self.assertTrue(_line_is_preamble_textheight_shrink(TEX_WITH_DISTURBANCE, 5))

    def test_detects_nearby_lines(self):
        # Line 4 or 6 (within the block context) also detected
        self.assertTrue(_line_is_preamble_textheight_shrink(TEX_WITH_DISTURBANCE, 4))
        self.assertTrue(_line_is_preamble_textheight_shrink(TEX_WITH_DISTURBANCE, 6))

    def test_rejects_body_lines(self):
        # Line 8+ is after \begin{document}
        self.assertFalse(_line_is_preamble_textheight_shrink(TEX_WITH_DISTURBANCE, 8))

    def test_rejects_normal_tex(self):
        self.assertFalse(_line_is_preamble_textheight_shrink(TEX_NORMAL, 3))

    def test_detects_standalone_shrink(self):
        self.assertTrue(_line_is_preamble_textheight_shrink(TEX_WITH_STANDALONE, 3))


class RemoveTextheightShrinkTest(unittest.TestCase):
    def test_removes_disturbance_block(self):
        modified, fix = _remove_preamble_textheight_shrink(TEX_WITH_DISTURBANCE, 5)
        self.assertIsNotNone(fix)
        self.assertTrue(fix.success)
        self.assertNotIn("DISTURBANCE:E2", modified)
        self.assertNotIn("\\addtolength{\\textheight}", modified)
        self.assertIn("\\begin{document}", modified)  # 文档完整

    def test_removes_standalone_shrink(self):
        modified, fix = _remove_preamble_textheight_shrink(TEX_WITH_STANDALONE, 3)
        self.assertIsNotNone(fix)
        self.assertNotIn("\\addtolength{\\textheight}", modified)

    def test_noop_on_normal(self):
        modified, fix = _remove_preamble_textheight_shrink(TEX_NORMAL, 3)
        self.assertIsNone(fix)
        self.assertEqual(modified, TEX_NORMAL)


class OverflowDefectsRoutingTest(unittest.TestCase):
    def test_d1_preamble_routes_to_textheight_removal(self):
        """D1 候选在导言区 textheight 缩小行 → 路由到移除而不是段落修复。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            tex = Path(tmpdir) / "main.tex"
            tex.write_text(TEX_WITH_DISTURBANCE, encoding="utf-8")
            report = fix_overflow_defects(
                str(tex),
                defects=[
                    {
                        "defect_id": "D1",
                        "page": 0,
                        "line_number": 5,
                        "object": "",
                        "description": "Overfull hbox",
                        "overflow_amount": 31.0,
                    }
                ],
            )
            report_dict = report.to_dict()
            self.assertGreaterEqual(len(report_dict.get("changes", [])), 1)
            updated = tex.read_text(encoding="utf-8")
            self.assertNotIn("DISTURBANCE:E2", updated)
            self.assertNotIn("\\addtolength{\\textheight}", updated)
            changes = report_dict["changes"]
            self.assertTrue(any("textheight" in str(c.get("action", "")) for c in changes))

    def test_d1_body_line_still_uses_paragraph_fixer(self):
        """正文行的 D1 候选仍走段落修复路径。"""
        body_tex = (
            "\\documentclass{article}\n"
            "\\begin{document}\n"
            "Some normal text.\n"
            "\\end{document}\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            tex = Path(tmpdir) / "main.tex"
            tex.write_text(body_tex, encoding="utf-8")
            report = fix_overflow_defects(
                str(tex),
                defects=[
                    {
                        "defect_id": "D1",
                        "page": 0,
                        "line_number": 3,
                        "object": "",
                        "description": "some normal text that overflows",
                        "overflow_amount": 12.0,
                    }
                ],
            )
            # 段落修复可能或不可能成功，但不会走 textheight 移除
            changes = report.to_dict()["changes"]
            self.assertFalse(any("textheight" in str(c.get("action", "")) for c in changes))

if __name__ == "__main__":
    unittest.main()
