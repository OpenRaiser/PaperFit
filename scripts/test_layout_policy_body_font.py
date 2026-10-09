"""Body font protection is distinct from table typography and PDF naturalness."""
from __future__ import annotations
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parent))
from content_integrity_check import compute_content_diff
from layout_policy import compare_layout_policy
from scientific_integrity import arraystretch_declarations

TABULAR = r"\begin{tabular}{lr}Method&Score\\A&91.2\\B&89.3\\\end{tabular}"
TABLE = r"\begin{table}[t]\centering" + TABULAR + r"\caption{Results}\label{tab:r}\end{table}"
PROSE = "The method does not reduce accuracy. The score is 91.2."
def manuscript(body: str, preamble: str = "") -> str:
    return "\\documentclass{article}\n" + preamble + "\n\\begin{document}\n" + body + "\n\\end{document}\n"

class BodyFontLayoutPolicyTests(unittest.TestCase):
    def test_new_ordinary_body_small_declarations_are_hard_policy_failures(self):
        before = manuscript(PROSE + TABLE)
        for command in ("small", "footnotesize", "scriptsize", "tiny"):
            with self.subTest(command=command):
                after = manuscript("\\" + command + " " + PROSE + TABLE)
                diff = compute_content_diff(before, after, layout_only=True)
                self.assertEqual(diff["scientific_payload"]["status"], "pass")
                self.assertEqual(diff["layout_prose"]["status"], "pass")
                self.assertEqual(diff["layout_policy"]["status"], "blocked")
                self.assertEqual(diff["layout_policy"]["findings"][0]["rule"], "body_font_shrink")
                self.assertEqual(diff["action_required"], "auto_rollback")

    def test_local_body_group_and_document_hook_do_not_bypass_font_policy(self):
        for after in (manuscript(r"{\small " + PROSE + "}"),
                      manuscript(PROSE, r"\AtBeginDocument{\small}"),
                      manuscript(PROSE, r"\small")):
            with self.subTest(after=after):
                self.assertEqual(compare_layout_policy(manuscript(PROSE), after)["status"], "blocked")

    def test_table_and_float_local_normal_font_choices_are_allowed(self):
        before = manuscript(PROSE + TABLE)
        for command in (r"\small ", r"\footnotesize ", r"\scriptsize ", r"\fontsize{9}{11}\selectfont "):
            with self.subTest(command=command):
                after = manuscript(PROSE + TABLE.replace(r"\centering", r"\centering" + command))
                diff = compute_content_diff(before, after, layout_only=True)
                self.assertEqual(diff["scientific_payload"]["status"], "pass")
                self.assertEqual(diff["layout_policy"]["status"], "pass")
                self.assertNotEqual(diff["action_required"], "auto_rollback")

    def test_font_before_bare_tabular_is_allowed_only_in_table_only_scope(self):
        self.assertEqual(compare_layout_policy(manuscript(PROSE + TABULAR),
                                               manuscript(PROSE + r"{\small " + TABULAR + "}"))["status"], "pass")
        self.assertEqual(compare_layout_policy(manuscript(TABULAR + PROSE),
                                               manuscript(r"\small " + TABULAR + PROSE))["status"], "blocked")
        self.assertEqual(compare_layout_policy(manuscript(TABULAR + PROSE),
                                               manuscript(r"{\small " + TABULAR + PROSE + "}"))["status"], "blocked")

    def test_caption_and_footnote_local_font_are_not_ordinary_body_shrink(self):
        for before, after in [
            (TABLE, TABLE.replace("{Results}", r"{\small Results}")),
            (PROSE + r"\footnote{Additional experiment.}", PROSE + r"\footnote{\footnotesize Additional experiment.}"),
        ]:
            with self.subTest(after=after):
                self.assertEqual(compare_layout_policy(manuscript(before), manuscript(after))["status"], "pass")

    def test_moving_an_existing_table_font_to_body_is_not_grandfathered(self):
        before = manuscript(PROSE + TABLE.replace(r"\centering", r"\centering\small "))
        after = manuscript(r"\small " + PROSE + TABLE)
        self.assertEqual(compare_layout_policy(before, after)["status"], "blocked")

    def test_body_fontsize_needs_review_without_a_template_font_baseline(self):
        before = manuscript(PROSE)
        after = manuscript(r"\fontsize{9}{11}\selectfont " + PROSE)
        report = compare_layout_policy(before, after)
        self.assertEqual(report["status"], "manual_review")
        self.assertEqual(report["findings"][0]["rule"], "body_font_size")
        self.assertEqual(compute_content_diff(before, after, layout_only=True)["action_required"], "manual_review")

    def test_zero_or_negative_fontsize_is_not_table_typography(self):
        for size in ("0", "-2"):
            after = manuscript(TABLE.replace(r"\centering", r"\centering\fontsize{" + size + r"}{11}\selectfont "))
            self.assertEqual(compare_layout_policy(manuscript(TABLE), after)["status"], "blocked")

    def test_explicit_sub_5pt_table_font_is_a_hard_readability_failure(self):
        for size in ("1", "4.9", "4.99pt"):
            with self.subTest(size=size):
                before = manuscript(TABLE)
                after = manuscript(TABLE.replace(r"\centering", r"\centering\fontsize{" + size + r"}{6}\selectfont "))
                diff = compute_content_diff(before, after, layout_only=True)
                self.assertEqual(diff["scientific_payload"]["status"], "pass")
                self.assertEqual(diff["layout_policy"]["status"], "blocked")
                self.assertEqual(diff["layout_policy"]["findings"][0]["rule"], "unreadable_explicit_font_size")
                self.assertEqual(diff["action_required"], "auto_rollback")
        legal = manuscript(TABLE.replace(r"\centering", r"\centering\fontsize{5}{6}\selectfont "))
        self.assertEqual(compare_layout_policy(manuscript(TABLE), legal)["status"], "pass")

    def test_unknown_or_relative_table_fontsize_cannot_self_approve(self):
        for spec in (r"\fontsize{\tablefont}{11}", r"\fontsize{.5\baselineskip}{11}",
                     r"\fontsize{9}{\tableleading}", r"\fontsize{9}{0}"):
            with self.subTest(spec=spec):
                after = manuscript(TABLE.replace(r"\centering", r"\centering" + spec + r"\selectfont "))
                self.assertNotEqual(compare_layout_policy(manuscript(TABLE), after)["status"], "pass")

    def test_native_math_subscripts_and_conventional_table_declarations_are_legal(self):
        before = manuscript(r"The formula is $x_{i_j}=a^2$." + TABLE)
        after = before.replace(r"\centering", r"\centering\footnotesize ")
        self.assertEqual(compare_layout_policy(before, after)["status"], "pass")
        self.assertEqual(compute_content_diff(before, after, layout_only=True)["scientific_payload"]["status"], "pass")

    def test_inherited_body_font_and_removal_are_grandfathered(self):
        before = manuscript(r"\small " + PROSE)
        self.assertEqual(compare_layout_policy(before, before + "% comment\n")["status"], "pass")
        self.assertEqual(compare_layout_policy(before, manuscript(PROSE))["status"], "pass")

    def test_comments_literals_macro_definitions_and_escaped_commands_are_not_executed(self):
        inactive = "% \\small\n\\verb|\\small|\n\\newcommand{\\tablefont}{\\small}\n\\\\small\n"
        self.assertEqual(compare_layout_policy("", inactive)["status"], "pass")

    def test_normal_positive_template_spacing_is_not_hard_rejected(self):
        before = manuscript(PROSE, r"\setlength{\parskip}{6pt}")
        after = manuscript(PROSE, r"\setlength{\parskip}{3pt}")
        self.assertEqual(compare_layout_policy(before, after)["status"], "pass")
        self.assertEqual(compare_layout_policy("", r"\vspace{-0.2em}")["status"], "manual_review")

    def test_negative_spacing_and_positive_line_compression_require_visual_review(self):
        for command in (r"\setlength{\parskip}{-10pt}", r"\addtolength{\itemsep}{-1pt}",
                        r"\parskip=-2pt", r"\linespread{0.9}"):
            with self.subTest(command=command):
                report = compare_layout_policy("", command)
                self.assertEqual(report["status"], "manual_review")
                diff = compute_content_diff(manuscript(PROSE), manuscript(command + " " + PROSE), layout_only=True)
                self.assertEqual(diff["scientific_payload"]["status"], "pass")
                self.assertEqual(diff["action_required"], "manual_review")

    def test_nonpositive_body_baseline_or_line_factor_is_hard_blocked(self):
        for command in (r"\setlength{\baselineskip}{0pt}", r"\baselineskip=-2pt", r"\linespread{0}"):
            with self.subTest(command=command):
                self.assertEqual(compare_layout_policy("", command)["status"], "blocked")

    def test_visual_spacing_receipt_is_exact_and_cannot_approve_body_small(self):
        command = r"\setlength{\parskip}{-2pt}"
        finding = compare_layout_policy("", command, file="main.tex")["findings"][0]
        approval = {**{key: finding[key] for key in ("file", "line", "command", "arguments")},
                    "approved": True, "evidence": {"source": "host_review", "reason": "all affected pages reviewed",
                                                  "scope": "local", "visual_inspected": True, "page_images": ["page_001.png"]}}
        self.assertEqual(compare_layout_policy("", command, file="main.tex", approvals=[approval])["status"], "pass")
        self.assertEqual(compare_layout_policy("", command.replace("-2pt", "-3pt"), file="main.tex", approvals=[approval])["status"], "manual_review")
        body = manuscript(r"\small " + PROSE)
        finding = compare_layout_policy(manuscript(PROSE), body, file="main.tex")["findings"][0]
        approval.update({key: finding[key] for key in ("file", "line", "command", "arguments")})
        self.assertEqual(compare_layout_policy(manuscript(PROSE), body, file="main.tex", approvals=[approval])["status"], "blocked")

    def test_same_scope_normal_font_reset_keeps_body_font_unchanged(self):
        before = manuscript(TABLE + PROSE)
        for after in (manuscript(r"{\small " + TABLE + r"\normalsize " + PROSE + "}"),
                      manuscript(r"\small " + TABLE + r"\normalsize " + PROSE),
                      manuscript(r"\small\normalsize " + TABLE + PROSE)):
            with self.subTest(after=after):
                self.assertEqual(compare_layout_policy(before, after)["status"], "pass")

    def test_nested_normal_reset_does_not_end_outer_font_leak(self):
        before = manuscript(TABLE + PROSE)
        for after in (manuscript(r"\small " + TABLE.replace(r"\centering", r"\centering\normalsize ") + PROSE),
                      manuscript(r"\small {" + TABLE + r"\normalsize} " + PROSE)):
            with self.subTest(after=after):
                self.assertEqual(compare_layout_policy(before, after)["status"], "blocked")

    def test_redefined_fonts_or_normal_reset_need_review_not_a_scope_bypass(self):
        for declaration, body in [
            (r"\renewcommand{\normalsize}{\tiny}", r"\small " + TABLE + r"\normalsize " + PROSE),
            (r"\renewcommand{\small}{\fontsize{1}{1}\selectfont}", TABLE.replace(r"\centering", r"\centering\small ")),
            (r"\renewcommand{\fontsize}[2]{\tiny}", TABLE.replace(r"\centering", r"\centering\fontsize{9}{11}\selectfont ")),
        ]:
            with self.subTest(declaration=declaration):
                self.assertNotEqual(compare_layout_policy(manuscript(TABLE + PROSE, declaration),
                                                          manuscript(body, declaration))["status"], "pass")

    def test_nonpositive_arraystretch_blocks_but_positive_factors_stay_legal(self):
        for command in (r"\renewcommand{\arraystretch}", r"\renewcommand\arraystretch",
                        r"\newcommand{\arraystretch}", r"\providecommand{\arraystretch}",
                        r"\def\arraystretch", r"\gdef\arraystretch"):
            for value in ("0", "-0.1"):
                with self.subTest(command=command, value=value):
                    after = manuscript(TABLE.replace(r"\centering", r"\centering" + command + "{" + value + "}"))
                    report = compare_layout_policy(manuscript(TABLE), after)
                    self.assertEqual(report["status"], "blocked")
                    self.assertEqual(report["findings"][0]["rule"], "nonpositive_table_row_stretch")
        for value in (".9", "1", "1.2"):
            after = manuscript(TABLE.replace(r"\centering", r"\centering\renewcommand{\arraystretch}{" + value + "}"))
            self.assertEqual(compare_layout_policy(manuscript(TABLE), after)["status"], "pass")

    def test_arraystretch_literal_and_dormant_macro_examples_are_not_executed(self):
        command = r"\renewcommand{\arraystretch}{0}"
        inactive = ["% " + command + "\n", r"\verb|" + command + "|",
                    r"\lstinline|" + command + "|",
                    r"\detokenize{" + command + "}",
                    r"\unexpanded{" + command + "}",
                    r"\begin{verbatim}" + command + r"\end{verbatim}",
                    r"\begin{filecontents}{demo.tex}" + command + r"\end{filecontents}",
                    r"\newcommand{\example}{" + command + "}",
                    r"\string\renewcommand{\arraystretch}{0}",
                    "\\\\" + command[1:]]
        for source in inactive:
            with self.subTest(source=source):
                self.assertEqual(arraystretch_declarations(source), [])
                self.assertEqual(compare_layout_policy("", source)["status"], "pass")

    def test_arraystretch_zero_argument_declaration_reflow_preserves_payload(self):
        for signature in ("[0]", "[ 0 ]"):
            before = manuscript(TABLE.replace(r"\centering", r"\centering\renewcommand{\arraystretch}" + signature + "{1.2}"))
            after = before.replace("{1.2}", "{1.0}")
            diff = compute_content_diff(before, after, layout_only=True)
            self.assertEqual(diff["scientific_payload"]["status"], "pass")
            self.assertEqual(diff["layout_prose"]["status"], "pass")
            self.assertIn(diff["action_required"], ("none", "log_only"))
            self.assertEqual(arraystretch_declarations(after)[0]["value"], 1.0)
        source = r"\renewcommand{\arraystretch}[1]{#1}"
        self.assertEqual(arraystretch_declarations(source), [])
        source = r"\def\arraystretch#1{#1}"
        self.assertEqual(arraystretch_declarations(source), [])

    def test_arraystretch_inventory_preserves_real_offsets(self):
        source = "% \\\\renewcommand{\\\\arraystretch}{0}\n" + r"\renewcommand{\arraystretch}{0}"
        record = arraystretch_declarations(source)[0]
        self.assertEqual(record["start"], source.index("\n") + 1)
        self.assertEqual(record["end"], len(source))
        self.assertEqual(record["line"], 2)

    def test_layout_controls_cannot_hide_a_changed_scientific_number(self):
        before = manuscript(TABLE + PROSE)
        after = manuscript(TABLE.replace(r"\centering", r"\centering\footnotesize ").replace("A&91.2", "A&19.2") + PROSE)
        self.assertEqual(compute_content_diff(before, after, layout_only=True)["action_required"], "auto_rollback")

if __name__ == "__main__":
    unittest.main()
