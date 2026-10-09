"""Inherited scaling follows an unchanged, uniquely labelled float family."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from content_integrity_check import compute_content_diff
from layout_policy import compare_layout_policy


TABULAR = r"\begin{tabular}{lc}Method&Score\\A&93.1\\\end{tabular}"
GRAPHIC = r"\includegraphics[width=0.6\linewidth]{plot.pdf}"


def float_source(environment="wraptable", *, label="tab:results", body=TABULAR, scale="0.9", extra_labels=""):
    options = "[14]{r}{0.55\\textwidth}" if environment.startswith("wrap") else "[tbp]"
    return (f"\\begin{{{environment}}}" + options + "\n"
            + r"\caption{Results}\label{" + label + "}" + extra_labels + "\n"
            + r"\scalebox{" + scale + "}{" + body + "}\n"
            + f"\\end{{{environment}}}\n")


class FloatAnchorPolicyTest(unittest.TestCase):
    def test_unchanged_inherited_scaling_follows_all_table_family_reflows(self):
        environments = ["wraptable", "wraptable*", "table", "table*", "sidewaystable", "sidewaystable*"]
        for before in environments:
            for after in environments:
                with self.subTest(before=before, after=after):
                    report = compare_layout_policy(float_source(before), float_source(after))
                    self.assertEqual(report["status"], "pass", report)

    def test_unchanged_inherited_scaling_follows_all_figure_family_reflows(self):
        environments = ["wrapfigure", "wrapfigure*", "figure", "figure*", "sidewaysfigure", "sidewaysfigure*"]
        for before in environments:
            for after in environments:
                with self.subTest(before=before, after=after):
                    report = compare_layout_policy(float_source(before, label="fig:results", body=GRAPHIC),
                                                   float_source(after, label="fig:results", body=GRAPHIC))
                    self.assertEqual(report["status"], "pass", report)

    def test_actual_eccv_shape_retains_scientific_channel(self):
        before = float_source()
        after = float_source("table")
        report = compute_content_diff(before, after)
        self.assertEqual(report["scientific_payload"]["status"], "pass", report)
        self.assertEqual(report["layout_policy"]["status"], "pass", report)

    def test_factor_change_requires_review_across_reflow(self):
        self.assertEqual(compare_layout_policy(float_source(), float_source("table", scale="0.8"))["status"], "manual_review")

    def test_scaled_body_change_requires_review_across_reflow(self):
        self.assertEqual(compare_layout_policy(float_source(), float_source("table", body=TABULAR.replace("93.1", "94.1")))["status"], "manual_review")

    def test_label_change_requires_review_across_reflow(self):
        self.assertEqual(compare_layout_policy(float_source(), float_source("table", label="tab:other"))["status"], "manual_review")

    def test_float_family_change_requires_review(self):
        self.assertEqual(compare_layout_policy(float_source(), float_source("figure"))["status"], "manual_review")

    def test_new_scaling_requires_review_during_reflow(self):
        before = float_source().replace(r"\scalebox{0.9}{" + TABULAR + "}", TABULAR)
        self.assertEqual(compare_layout_policy(before, float_source("table"))["status"], "manual_review")

    def test_duplicate_label_cannot_anchor_reflow(self):
        duplicate = r"\label{tab:results}"
        self.assertEqual(compare_layout_policy(float_source() + duplicate, float_source("table") + duplicate)["status"], "manual_review")

    def test_multiple_labels_cannot_anchor_reflow(self):
        self.assertEqual(compare_layout_policy(float_source(extra_labels=r"\label{tab:second}"),
                                              float_source("table", extra_labels=r"\label{tab:second}"))["status"], "manual_review")

    def test_unlabelled_float_cannot_anchor_reflow(self):
        before = float_source().replace(r"\label{tab:results}", "")
        after = float_source("table").replace(r"\label{tab:results}", "")
        self.assertEqual(compare_layout_policy(before, after)["status"], "manual_review")

    def test_scaler_transfer_to_unrelated_float_requires_review(self):
        first = float_source("table")
        second = float_source("table", label="tab:other")
        unscaled_first = first.replace(r"\scalebox{0.9}{" + TABULAR + "}", TABULAR)
        unscaled_second = second.replace(r"\scalebox{0.9}{" + TABULAR + "}", TABULAR)
        self.assertEqual(compare_layout_policy(first + unscaled_second, unscaled_first + second)["status"], "manual_review")

    def test_scaler_transfer_between_duplicate_labels_requires_review(self):
        scaled = float_source("table")
        unscaled = scaled.replace(r"\scalebox{0.9}{" + TABULAR + "}", TABULAR)
        self.assertEqual(compare_layout_policy(scaled + unscaled, unscaled + scaled)["status"], "manual_review")

    def test_nested_ambiguous_float_cannot_anchor_reflow(self):
        before = r"\begin{table}" + float_source() + r"\end{table}"
        after = r"\begin{table}" + float_source("table*") + r"\end{table}"
        self.assertEqual(compare_layout_policy(before, after)["status"], "manual_review")

    def test_nested_factor_has_one_independent_finding(self):
        body = r"\resizebox{1.29\linewidth}{!}{" + TABULAR + "}"
        before = float_source(body=body)
        after = float_source("table", body=body.replace("1.29\\linewidth", "\\linewidth"))
        report = compare_layout_policy(before, after)
        self.assertEqual(report["review_count"], 1, report)
        self.assertEqual(report["blocking_count"], 0, report)
        self.assertEqual(report["findings"][0]["command"], "resizebox")

    def test_duplicate_scaler_introduction_requires_review(self):
        before = float_source()
        after = float_source("table").replace(r"\scalebox{0.9}{" + TABULAR + "}",
                                              (r"\scalebox{0.9}{" + TABULAR + "}") * 2)
        self.assertEqual(compare_layout_policy(before, after)["review_count"], 1)
        self.assertEqual(compare_layout_policy(before, after)["blocking_count"], 0)

    def test_scaling_cannot_use_spacing_approval(self):
        before = float_source()
        after = float_source("table", scale="0.8")
        approval = {"approved": True, "file": "main.tex", "line": 3, "command": "scalebox", "arguments": ["0.8"],
                    "evidence": {"source": "host_review", "reason": "checked", "scope": "local", "visual_inspected": True, "page_images": ["page.png"]}}
        self.assertEqual(compare_layout_policy(before, after, approvals=[approval], file="main.tex")["status"], "manual_review")


if __name__ == "__main__":
    unittest.main()
