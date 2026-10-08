"""Paragraph parameters must remain local until TeX breaks the targeted paragraph."""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from space_util_fixers import fix_paragraph_looseness, fix_widow_orphan, _passes_structure_write_gate
from scientific_integrity import _strip_layout


PARAGRAPH = (
    "The procedure compares each observation with the reference distribution and records the resulting score. "
    "Every observation remains in the analysis, and the comparison uses the same parameters throughout. "
    "The reported summary describes the complete set of observations and retains the assumptions used in the calculation. "
    "A separate check confirms that the labels and values remain unchanged after the document is formatted."
)


class ParagraphLoosenessTest(unittest.TestCase):
    def test_local_assignment_ends_paragraph_inside_group_and_preserves_neighbours(self):
        source = "Previous paragraph.\n\n" + PARAGRAPH + "\n\nNext paragraph.\n"
        updated, result = fix_paragraph_looseness(source, source.index(PARAGRAPH))
        self.assertIsNotNone(result)
        self.assertEqual(updated, "Previous paragraph.\n\n{\\looseness=-1 " + PARAGRAPH
                         + "\n\\par}\n\nNext paragraph.\n")
        self.assertEqual(_passes_structure_write_gate(source, updated), (True, "pass"))

    def test_text_target_matches_one_paragraph_without_consuming_separator(self):
        source = "Previous paragraph.\n\n" + PARAGRAPH + "\n\nNext paragraph.\n"
        updated, result = fix_widow_orphan(source, paragraph_start_line=3, paragraph_text=PARAGRAPH)
        self.assertIsNotNone(result)
        self.assertEqual(updated, "Previous paragraph.\n\n{\\looseness=-1 " + PARAGRAPH
                         + "\n\\par}\n\nNext paragraph.\n")

    def test_explicit_par_is_reused_and_following_paragraph_is_outside_scope(self):
        source = "First paragraph.\\par Second paragraph.\n\n"
        updated, result = fix_paragraph_looseness(source, 0)
        self.assertIsNotNone(result)
        self.assertEqual(updated, "{\\looseness=-1 First paragraph.\\par} Second paragraph.\n\n")
        self.assertEqual(updated.count(r"\par"), 1)

    def test_comment_cannot_swallow_inserted_termination(self):
        source = "Paragraph with a trailing comment. % retain this comment\n\nNext."
        updated, result = fix_paragraph_looseness(source, 0)
        self.assertIsNotNone(result)
        self.assertIn("% retain this comment\n\\par}\n\nNext.", updated)

    def test_document_end_and_whitespace_only_blank_line_are_boundaries(self):
        source = "\\begin{document}\n" + PARAGRAPH + "\n \t\nNext.\n\\end{document}"
        updated, result = fix_paragraph_looseness(source, source.index(PARAGRAPH), 1)
        self.assertIsNotNone(result)
        self.assertIn("\n\\par}\n \t\nNext.\n\\end{document}", updated)
        final = "\\begin{document}\n" + PARAGRAPH + "\n\\end{document}"
        updated, result = fix_paragraph_looseness(final, final.index(PARAGRAPH))
        self.assertIsNotNone(result)
        self.assertTrue(updated.endswith("\n\n\\par}\\end{document}"))

    def test_mid_paragraph_offset_and_existing_assignment_are_not_repaired(self):
        for source, offset in (("Before " + PARAGRAPH, 7),
                               ("{\\looseness=-1 " + PARAGRAPH + "\\par}", 15),
                               ("Paragraph.\\looseness=-1\n\n", 0)):
            with self.subTest(source=source[:30]):
                self.assertEqual(fix_paragraph_looseness(source, offset), (source, None))
        source = "Before " + PARAGRAPH
        self.assertEqual(fix_widow_orphan(source, paragraph_text=PARAGRAPH), (source, None))

    def test_inactive_or_unbalanced_source_is_not_a_paragraph_target(self):
        for source in ("% " + PARAGRAPH + "\n",
                       "\\begin{verbatim}\n\n" + PARAGRAPH + "\n\n\\end{verbatim}",
                       "\\newcommand{\\fixture}{\n\n" + PARAGRAPH + "\n\n}",
                       PARAGRAPH + "}\n\n"):
            with self.subTest(source=source[:30]):
                self.assertEqual(fix_paragraph_looseness(source, source.index(PARAGRAPH)), (source, None))

    def test_ambiguous_text_requires_line_identity(self):
        source = PARAGRAPH + "\n\n" + PARAGRAPH + "\n\n"
        self.assertEqual(fix_widow_orphan(source, paragraph_text=PARAGRAPH), (source, None))
        updated, result = fix_widow_orphan(source, paragraph_start_line=3, paragraph_text=PARAGRAPH)
        self.assertIsNotNone(result)
        self.assertTrue(updated.startswith(PARAGRAPH + "\n\n{\\looseness=-1 "))

    def test_only_bounded_literal_looseness_is_layout_and_scientific_numbers_stay_protected(self):
        source = "The measured value was -2.75 for 30 observations.\n\n"
        for assignment in (r"\looseness=-1 ", r"\looseness 0 ", r"\looseness=+1 "):
            updated = "{" + assignment + source + r"\par}"
            with self.subTest(assignment=assignment):
                self.assertEqual(_passes_structure_write_gate(source, updated), (True, "pass"))
                self.assertFalse(_passes_structure_write_gate(source, updated.replace("-2.75", "-2.70"))[0])
        for assignment in (r"\looseness=2 ", r"\looseness=-10 ", r"\looseness=1.5 ",
                           r"\looseness=\count0 ", r"\looseness=1\numexpr2 ",
                           r"\\looseness=-1 "):
            with self.subTest(unsupported=assignment):
                self.assertIn(assignment.strip(), _strip_layout(assignment + "Scientific value -2.75."))

    @unittest.skipUnless(shutil.which("pdflatex"), "pdflatex not installed")
    def test_tex_line_breaking_changes_only_with_termination_in_scope(self):
        updated, result = fix_paragraph_looseness(PARAGRAPH + "\n\n", 0)
        self.assertIsNotNone(result)
        variants = {"before": PARAGRAPH, "old": "{\\looseness=-1 " + PARAGRAPH + "}", "new": updated}
        source = "\\documentclass{article}\n\\begin{document}\n"
        for name, paragraph in variants.items():
            source += ("\\setbox0=\\vbox{\\hsize=310pt\\relax\n" + paragraph
                       + "\\par}\n\\typeout{A1HEIGHT:" + name + ":\\the\\ht0}\n")
        source += "\\typeout{A1OUTER:\\the\\looseness}\n\\end{document}\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.tex").write_text(source, encoding="utf-8")
            result = subprocess.run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "main.tex"],
                                    cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout[-2000:])
            log = (root / "main.log").read_text(encoding="utf-8", errors="replace")
        heights = {name: float(height) for name, height in re.findall(r"A1HEIGHT:(\w+):([0-9.]+)pt", log)}
        self.assertEqual(heights["before"], heights["old"])
        self.assertAlmostEqual(heights["old"] - heights["new"], 12.0, places=4)
        self.assertIn("A1OUTER:0", log)


if __name__ == "__main__":
    unittest.main()
