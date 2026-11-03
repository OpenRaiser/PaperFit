"""Focused layout-only prose safety and known reflow compatibility tests."""
import unittest

from layout_prose_integrity import compare_layout_prose
from scientific_integrity import scientific_payload_diff


def document(body):
    return "\\documentclass{article}\n\\begin{document}\n" + body + "\n\\end{document}\n"


class LayoutProseIntegrityTest(unittest.TestCase):
    def assert_pass(self, before, after):
        result = compare_layout_prose(before, after)
        self.assertTrue(result["checked"])
        self.assertEqual(result["status"], "pass", result)
        self.assertEqual(result["changed_tokens"], [])
        self.assertEqual(result["before"], result["after"])

    def assert_changed(self, before, after):
        result = compare_layout_prose(before, after)
        self.assertTrue(result["checked"])
        self.assertEqual(result["status"], "failed", result)
        self.assertTrue(result["changed_tokens"])
        self.assertNotEqual(result["before"]["sha256"], result["after"]["sha256"])

    def test_ordinary_claim_flips_even_with_no_word_count_change(self):
        pairs = [
            ("The method does not improve generalization.", "The method does improve generalization."),
            ("The method increases prediction error.", "The method decreases prediction error."),
            ("The procedure can improve the result.", "The procedure cannot improve the result."),
        ]
        for before, after in pairs:
            with self.subTest(before=before):
                self.assert_changed(document(before), document(after))

    def test_order_and_punctuation_are_protected(self):
        self.assert_changed(document("We compare A with B."), document("We compare B with A."))
        self.assert_changed(document("The method improves accuracy."), document("The method improves accuracy?"))
        self.assert_changed(document("The conclusion is now here."), document("The conclusion is nowhere."))

    def test_known_local_reflow_keeps_words_and_punctuation(self):
        before = document("The method improves generalization.\n\nThe result is stable.")
        after = document("{\\looseness=-1 The method\nimproves generalization.\n\\par}\n\nThe result is stable.")
        self.assert_pass(before, after)
        self.assert_pass(document("A clear result."), document("\\textbf{A clear} result."))
        self.assert_pass(document("The method improves accuracy."), document("The method\\\\[1pt]\nimproves accuracy."))

    def test_discretionary_breaks_join_the_original_token(self):
        self.assert_pass(document("BenchmarkOverflowToken"), document("Benchmark\\allowbreak Overflow\\allowbreak Token"))
        self.assert_pass(document("generalization"), document("generali\\-zation"))
        self.assert_changed(document("The method does not improve."), document("The method does\\allowbreak improve."))

    def test_command_prefix_and_escaped_command_are_not_normalized(self):
        self.assert_changed(document(r"The method \allowbreaknot improves."), document("The method improves."))
        self.assert_changed(document(r"The method \\allowbreak improves."), document("The method improves."))

    def test_unknown_invocation_and_argument_boundaries_remain_opaque(self):
        self.assert_changed(document(r"\claim{does not improve}"), document(r"\claim{does improve}"))
        self.assert_changed(document(r"\claim{a}{b}"), document(r"\claim{a b}"))
        self.assert_changed(document(r"\claim{stable}"), document(r"\otherclaim{stable}"))
        self.assert_pass(document("\\claim{stable result}"), document("\\claim{stable\nresult}"))
        self.assert_changed(document(r"\begin{mystery}A result.\end{mystery}"), document(r"\begin{other}A result.\end{other}"))

    def test_dynamic_or_unbounded_looseness_is_not_erased(self):
        for value in ["2", "-2", r"\myvalue"]:
            with self.subTest(value=value):
                self.assert_changed(document("A stable result."), document("\\looseness=" + value + " A stable result."))

    def test_independent_math_table_and_float_regions(self):
        before = document(r"A stable result. \begin{equation}x+y\end{equation}" +
                          r"\begin{table}\caption{Data}\label{tab:d}\begin{tabular}{cc}A&B\\\end{tabular}\end{table}" +
                          r"\begin{figure}\includegraphics[width=.4\linewidth]{plot.pdf}\caption{Plot}\label{fig:p}\end{figure}")
        after = before.replace(r"x+y", r"\begin{aligned}x&+y\end{aligned}").replace("{cc}", r"{p{2cm}p{3cm}}").replace(".4", ".8")
        self.assert_pass(before, after)
        self.assertEqual(scientific_payload_diff(before, after)["status"], "pass")
        # Masking is not permission to change data: the independent payload gate
        # must detect such a change even when this ordinary-prose channel passes.
        damaged = after.replace("A&B", "A&C")
        self.assert_pass(before, damaged)
        self.assertEqual(scientific_payload_diff(before, damaged)["status"], "failed")

    def test_macro_and_protected_arguments_have_independent_guard(self):
        before = document(r"\newcommand{\claim}{not}\section{Results}A stable result.")
        changed = before.replace("{not}", "{improved}").replace("{Results}", "{Conclusions}")
        self.assert_pass(before, changed)
        self.assertEqual(scientific_payload_diff(before, changed)["status"], "failed")

    def test_comment_changes_do_not_change_ordinary_prose(self):
        self.assert_pass(document("A stable result. % not improved\n"), document("A stable result. % improved\n"))

    def test_invalid_input_is_unavailable_not_pass(self):
        report = compare_layout_prose(None, "A result.")
        self.assertFalse(report["checked"])
        self.assertEqual(report["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
