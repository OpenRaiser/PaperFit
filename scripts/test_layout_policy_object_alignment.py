"""Inherited wrappers follow unique local objects, with multiplicity intact."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layout_policy import compare_layout_policy


TABLE_A = r"\begin{tabular}{lc}Method&Score\\A&93.1\\\end{tabular}"
TABLE_B = r"\begin{tabular}{lc}Method&Score\\B&82.4\\\end{tabular}"
WRAPPER_A = r"\resizebox{0.75\textwidth}{!}{" + TABLE_A + "}"
WRAPPER_B = r"\resizebox{0.85\textwidth}{!}{" + TABLE_B + "}"
PREFIX = (r"\begin{figure}\caption{First}\label{fig:first}\end{figure}"
          r"\begin{figure}\caption{Second}\label{fig:second}\end{figure}")
COMBINED_PREFIX = (r"\begin{figure}\captionof{figure}{First}\label{fig:first}"
                   r"\captionof{figure}{Second}\label{fig:second}\end{figure}")


def compound(first=WRAPPER_A, second=WRAPPER_B, *, first_label="tab:a",
             second_label="tab:b", width=".45\\linewidth", environment="table*"):
    return (f"\\begin{{{environment}}}[t]"
            + r"\begin{minipage}[t]{" + width + "}"
            + r"\caption{First results}" + first + r"\label{" + first_label + "}"
            + r"\end{minipage}\begin{minipage}[t]{.50\linewidth}"
            + r"\caption{Second results}" + second + r"\label{" + second_label + "}"
            + r"\end{minipage}" + f"\\end{{{environment}}}")


class LocalObjectAlignmentTest(unittest.TestCase):
    def test_unchanged_wrappers_survive_other_float_combination(self):
        result = compare_layout_policy(PREFIX + compound(), COMBINED_PREFIX + compound())
        self.assertEqual(result["status"], "pass", result)

    def test_wrapper_changes_still_block_after_other_float_combination(self):
        for changed in (WRAPPER_A.replace("0.75", "0.65"),
                        WRAPPER_A.replace("93.1", "94.1"),
                        WRAPPER_A + WRAPPER_A):
            with self.subTest(changed=changed):
                result = compare_layout_policy(PREFIX + compound(),
                                               COMBINED_PREFIX + compound(first=changed))
                self.assertEqual(result["blocking_count"], 1, result)

    def test_duplicate_removal_cannot_pay_for_a_new_smaller_wrapper(self):
        before = PREFIX + compound(first=WRAPPER_A * 2)
        after = COMBINED_PREFIX + compound(first=WRAPPER_A + WRAPPER_A.replace("0.75", "0.65"))
        result = compare_layout_policy(before, after)
        self.assertEqual(result["blocking_count"], 1, result)
        self.assertEqual(result["findings"][0]["arguments"], [r"0.65\textwidth", "!"])

    def test_identical_payload_wrapper_cannot_transfer_between_local_labels(self):
        before = PREFIX + compound(first=WRAPPER_A, second=TABLE_A)
        after = COMBINED_PREFIX + compound(first=TABLE_A, second=WRAPPER_A)
        result = compare_layout_policy(before, after)
        self.assertEqual(result["blocking_count"], 1, result)

    def test_added_scaled_duplicate_object_cannot_be_set_deduplicated(self):
        before = PREFIX + compound(first=WRAPPER_A, second=TABLE_A)
        after = COMBINED_PREFIX + compound(first=WRAPPER_A, second=WRAPPER_A)
        result = compare_layout_policy(before, after)
        self.assertEqual(result["blocking_count"], 1, result)

    def test_exact_local_container_parameters_and_float_environment_are_retained(self):
        for changed in (compound(width=r".25\linewidth"), compound(environment="figure*")):
            with self.subTest(changed=changed):
                result = compare_layout_policy(PREFIX + compound(), COMBINED_PREFIX + changed)
                self.assertEqual(result["status"], "blocked", result)

    def test_duplicate_or_multiple_local_labels_remain_ambiguous(self):
        for source in (compound(second_label="tab:a"),
                       compound(first=WRAPPER_A + r"\label{tab:extra}")):
            with self.subTest(source=source):
                result = compare_layout_policy(PREFIX + source, COMBINED_PREFIX + source)
                self.assertEqual(result["status"], "blocked", result)

    def test_new_body_font_shrink_is_not_hidden_by_float_object_alignment(self):
        before = PREFIX + compound() + "Normal scientific prose."
        after = COMBINED_PREFIX + compound() + r"{\small Normal scientific prose.}"
        result = compare_layout_policy(before, after)
        self.assertEqual(result["blocking_count"], 1, result)
        self.assertEqual(result["findings"][0]["rule"], "body_font_shrink")


if __name__ == "__main__":
    unittest.main()
