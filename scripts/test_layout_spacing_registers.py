"""Known layout registers must not become science/prose numeric payload."""
from __future__ import annotations
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parent))
from content_integrity_check import compute_content_diff
from scientific_integrity import layout_length_end, layout_assignment_end

BODY = r"""The method does not reduce accuracy. Accuracy is -91.2.
\begin{equation}x=a+b\label{eq:a}\end{equation}
\begin{itemize}\item First contribution.\item Second contribution.\end{itemize}
"""
def manuscript(body: str, preamble: str = "") -> str:
    return "\\documentclass{article}\n" + preamble + "\n\\begin{document}\n" + body + "\n\\end{document}\n"

class KnownLayoutSpacingTests(unittest.TestCase):
    def assert_preserved(self, before: str, after: str) -> None:
        diff = compute_content_diff(before, after, layout_only=True)
        self.assertEqual(diff["scientific_payload"]["status"], "pass", diff["scientific_payload"])
        self.assertEqual(diff["layout_prose"]["status"], "pass", diff["layout_prose"])
        self.assertNotEqual(diff["action_required"], "auto_rollback", diff["violation"])

    def test_display_glue_preamble_and_body(self):
        for register in ("abovedisplayskip", "belowdisplayskip", "abovedisplayshortskip", "belowdisplayshortskip"):
            for in_body in (False, True):
                with self.subTest(register=register, in_body=in_body):
                    before = r"\setlength{\%s}{12pt plus 3pt minus 9pt}" % register
                    after = r"\setlength{\%s}{6pt plus 2pt minus 3pt}" % register
                    self.assert_preserved(manuscript(before + BODY) if in_body else manuscript(BODY, before),
                                          manuscript(after + BODY) if in_body else manuscript(BODY, after))

    def test_list_registers_braced_and_token_arguments(self):
        for register in ("itemsep", "topsep", "parsep", "partopsep", "leftmargin", "rightmargin", "listparindent", "itemindent", "labelsep", "labelwidth"):
            for braced in (False, True):
                with self.subTest(register=register, braced=braced):
                    target = ("{\\%s}" if braced else "\\%s") % register
                    before = r"\setlength" + target + "{4pt}"
                    after = r"\setlength" + target + "{2pt}"
                    self.assert_preserved(manuscript(before + BODY), manuscript(after + BODY))

    def test_finite_known_register_expressions(self):
        for before, after in [
            (r"\setlength{\itemsep}{.3\baselineskip}", r"\setlength{\itemsep}{.2\baselineskip}"),
            (r"\setlength{\topsep}{\dimexpr 2\baselineskip-3pt\relax}", r"\setlength{\topsep}{\dimexpr \baselineskip-2pt\relax}"),
            (r"\setlength{\itemsep}{\dimexpr\baselineskip/2\relax plus 1pt}", r"\setlength{\itemsep}{\dimexpr\baselineskip/3\relax plus .5pt}"),
            (r"\setlength{\parskip}{0pt plus 1fil}", r"\setlength{\parskip}{0pt plus 2fil}"),
        ]:
            with self.subTest(before=before):
                self.assert_preserved(manuscript(before + BODY), manuscript(after + BODY))

    def test_primitive_layout_assignments_with_following_claim(self):
        for before, after in [
            (r"\itemsep=4pt plus 2pt minus 1pt", r"\itemsep=2pt plus 1pt minus .5pt"),
            (r"\abovedisplayskip 12pt", r"\abovedisplayskip 6pt"),
            (r"\topsep=\dimexpr\baselineskip-2pt\relax", r"\topsep=\dimexpr\baselineskip-3pt\relax"),
            (r"\tabcolsep=6pt", r"\tabcolsep=4pt"),
        ]:
            with self.subTest(before=before):
                self.assert_preserved(manuscript(before + " " + BODY), manuscript(after + " " + BODY))
                diff = compute_content_diff(manuscript(before + " " + BODY), manuscript(after + " " + BODY.replace("-91.2", "-19.2")), layout_only=True)
                self.assertEqual(diff["action_required"], "auto_rollback")
                self.assertEqual(diff["scientific_payload"]["status"], "failed")

    def test_malformed_or_unknown_expressions_remain_opaque(self):
        for value in ("2pt plus 91.2", r"\dimexpr 2pt+\score\relax",
                      r"\dimexpr 2pt/0\relax", "91.2", "2ptfoobar",
                      r"2\unknownskip", r"\dimexpr2pt-1pt"):
            with self.subTest(value=value):
                self.assertIsNone(layout_length_end("{\\itemsep}{" + value + "}", 0))
                before = manuscript(r"\setlength{\itemsep}{" + value + "}" + BODY)
                after = before.replace("91.2", "19.2") if "91.2" in value else before.replace(value, value + "7")
                self.assertEqual(compute_content_diff(before, after, layout_only=True)["action_required"], "auto_rollback")

    def test_incomplete_primitive_glue_does_not_hide_numbers(self):
        for value in ("=2pt plus 91.2", "=2pt minus 7", "=2pt plus \\unknownskip"):
            with self.subTest(value=value):
                self.assertIsNone(layout_assignment_end(value, 0, "itemsep"))
        self.assertEqual(layout_assignment_end("=2pt 91.2", 0, "itemsep"), 4)
        before = manuscript(r"\itemsep=2pt 91.2. " + BODY)
        self.assertEqual(compute_content_diff(before, before.replace("2pt 91.2", "2pt 19.2"), layout_only=True)["action_required"], "auto_rollback")

    def test_dimexpr_does_not_swallow_the_following_body_or_scientific_number(self):
        setting = r"\topsep=\dimexpr 2pt+1pt\relax"
        value = setting[len(r"\topsep"):] + " 91.2. The method does not reduce accuracy."
        self.assertEqual(layout_assignment_end(value, 0, "topsep"), len(value.split(" 91.2")[0]))
        before = manuscript(setting + " 91.2. " + BODY)
        for after in (before.replace(" 91.2.", " 19.2."),
                      before.replace("does not reduce", "does reduce")):
            with self.subTest(after=after):
                self.assertEqual(compute_content_diff(before, after, layout_only=True)["action_required"], "auto_rollback")

    def test_directly_redefined_registers_and_expression_primitives_stay_protected(self):
        for declaration in (r"\def\itemsep{91.2}", r"\newlength{\itemsep}",
                            r"\let\itemsep\score", r"\def\dimexpr{score}", r"\def\relax{score}"):
            with self.subTest(declaration=declaration):
                before = manuscript(r"\setlength{\itemsep}{\dimexpr2pt+1pt\relax}" + BODY, declaration)
                after = before.replace("2pt+1pt", "2pt+7pt")
                self.assertEqual(compute_content_diff(before, after, layout_only=True)["action_required"], "auto_rollback")

    def test_known_layout_command_cannot_mask_changed_math_or_table_data(self):
        before = manuscript(r"\setlength{\itemsep}{4pt}" + BODY + r"\begin{tabular}{lr}A&91.2\\B&89.3\\\end{tabular}")
        for after in (before.replace("4pt", "2pt").replace("x=a+b", "x=a-b"),
                      before.replace("4pt", "2pt").replace("A&91.2", "A&19.2"),
                      before.replace("4pt", "2pt").replace("does not reduce", "does reduce")):
            with self.subTest(after=after):
                self.assertEqual(compute_content_diff(before, after, layout_only=True)["action_required"], "auto_rollback")

    def test_grouped_register_changes_preserve_ordered_body_claims(self):
        before = manuscript(BODY)
        after = manuscript(r"\begingroup\itemsep=2pt " + BODY + r"\endgroup")
        self.assert_preserved(before, after)
        self.assertEqual(compute_content_diff(before, after.replace("does not reduce", "does reduce"), layout_only=True)["action_required"], "auto_rollback")

if __name__ == "__main__":
    unittest.main()
