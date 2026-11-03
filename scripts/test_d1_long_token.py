from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from overflow_fixers import (  # noqa: E402
    _fix_long_token_with_breaks,
    _insert_safe_breaks_in_token,
    fix_paragraph_overflow,
)


TEX = (
    "\\documentclass{article}\n"
    "\\begin{document}\n"
    "We evaluate ResNet50PretrainedOnImageNetWithDataAugmentationAndLearningRateSchedule on benchmarks.\n"
    "\\end{document}\n"
)


class InsertSafeBreaksTest(unittest.TestCase):
    def test_camel_case_boundary(self):
        result = _insert_safe_breaks_in_token("CamelCaseWord")
        self.assertIn(r"\allowbreak", result)
        # 断点在大写字母前（Camel|Case|Word）
        self.assertIn("Camel\\allowbreak ", result)

    def test_underscore_boundary(self):
        result = _insert_safe_breaks_in_token("some_long_identifier_name")
        self.assertIn(r"_\allowbreak ", result)

    def test_dot_slash_boundary(self):
        result = _insert_safe_breaks_in_token("path/to/file.txt")
        self.assertIn(r"/\allowbreak ", result)

    def test_no_break_in_simple_word(self):
        result = _insert_safe_breaks_in_token("simpleword")
        self.assertEqual(result, "simpleword")

    def test_mixed_boundaries(self):
        result = _insert_safe_breaks_in_token("ModelName_v2.0/bestCheckpoint")
        self.assertIn(r"\allowbreak", result)


class FixLongTokenTest(unittest.TestCase):
    def test_long_token_gets_breaks(self):
        long_token = "ThisIsAVeryLongModelIdentifierThatCannotBeBrokenNaturally"
        line = f"We use {long_token} for evaluation."
        tex = (
            "\\documentclass{article}\n"
            "\\begin{document}\n"
            f"{line}\n"
            "\\end{document}\n"
        )
        result = _fix_long_token_with_breaks(tex, line, line_number=3)
        self.assertIsNotNone(result)
        modified, fix = result
        self.assertTrue(fix.success)
        self.assertIn("\\allowbreak", modified)
        self.assertIn("断点", fix.action)

    def test_short_token_ignored(self):
        line = "We use ResNet for evaluation."
        # 无行号 → 仅扫描日志 context：短 token 不处理
        result = _fix_long_token_with_breaks(TEX, line, line_number=None)
        self.assertIsNone(result)

    def test_source_window_finds_token_when_context_lacks_it(self):
        # 日志 context 不含长 token，但按行号回源码窗口必须找到并修复
        line = "We use ResNet for evaluation."
        result = _fix_long_token_with_breaks(TEX, line, line_number=3)
        self.assertIsNotNone(result)
        self.assertIn("\\allowbreak", result[0])

    def test_command_name_not_touched(self):
        line = "\\newcommand{\\VeryLongCommandNameThatExceedsThirtyCharacters}{42}"
        tex = TEX + "\\newcommand{\\VeryLongCommandNameThatExceedsThirtyCharacters}{42}\n"
        result = _fix_long_token_with_breaks(tex, line, line_number=5)
        # 命令名本身绝不能被插入断点；窗口若修到正文 token 是允许的
        if result is not None:
            self.assertNotIn("\\allowbreak VeryLongCommandName", result[0])
            self.assertIn("\\newcommand{\\VeryLongCommandNameThatExceedsThirtyCharacters}", result[0])
        else:
            self.assertNotIn("\\allowbreak VeryLongCommandName", tex)

    def test_already_broken_not_re_processed(self):
        line = "We use Already\\allowbreak BrokenTokenName for evaluation."
        result = _fix_long_token_with_breaks(TEX, line, line_number=3)
        self.assertIsNone(result)


class FixParagraphOverflowIntegrationTest(unittest.TestCase):
    def test_long_token_strategy_fires_in_fix_paragraph(self):
        long_token = "BenchmarkModelNameThatIsWayTooLongToFitOnOneLineOfText"
        line = f"We compare against {long_token} and related baselines."
        tex = (
            "\\documentclass{article}\n"
            "\\begin{document}\n"
            f"{line}\n"
            "\\end{document}\n"
        )
        modified, fix = fix_paragraph_overflow(tex, overfull_line=line, overflow_amount=15.0, line_number=3)
        self.assertIsNotNone(fix)
        self.assertTrue(fix.success)
        self.assertIn("\\allowbreak", modified)


if __name__ == "__main__":
    unittest.main()
