"""Preserved logical objects survive standard composition without losing owners."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scientific_integrity as scientific
import layout_prose_integrity as prose


TABLE_A = r"\begin{tabular}{lr}A$\ddagger$&91.2\\\end{tabular}"
TABLE_B = r"\begin{tabular}{lr}B$\ddagger$&82.4\\\end{tabular}"
SHADOWED_REGISTER = r"\renewcommand\tabcolsep{2pt}"


def object_body(label, body, *, caption=None, captionof=False, star=False,
                short=None, family="table"):
    cap = r"\captionof" + ("*" if star else "") + "{" + family + "}" if captionof else r"\caption" + ("*" if star else "")
    if short is not None:
        cap += "[" + short + "]"
    return (r"\setlength{\tabcolsep}{2pt}" + body + cap
            + "{" + (caption if caption is not None else "Results " + label) + "}"
            + r"\label{" + label + "}")


def separate(a=TABLE_A, b=TABLE_B):
    return (SHADOWED_REGISTER + r"\begin{table}" + object_body("tab:a", a)
            + r"\end{table}\begin{table}" + object_body("tab:b", b) + r"\end{table}")


def combined(a=TABLE_A, b=TABLE_B, *, minipage=False, math_a="", math_b=""):
    groups = []
    for label, body, math in (("tab:a", a, math_a), ("tab:b", b, math_b)):
        content = object_body(label, math + body, captionof=True)
        groups.append((r"\begin{minipage}[t]{.45\linewidth}" + content + r"\end{minipage}")
                      if minipage else r"\begingroup" + content + r"\endgroup")
    return SHADOWED_REGISTER + r"\begin{figure*}" + r"\par\medskip".join(groups) + r"\end{figure*}"


class ScientificLayoutObjectTests(unittest.TestCase):
    def test_standard_group_composition_retains_all_scientific_channels(self):
        self.assertEqual(scientific.scientific_payload_diff(separate(), combined())["status"], "pass")

    def test_standard_minipage_composition_retains_all_scientific_channels(self):
        self.assertEqual(scientific.scientific_payload_diff(separate(), combined(minipage=True))["status"], "pass")

    def test_captionof_alias_preserves_caption_text_and_type(self):
        before = r"\begin{table}" + object_body("tab:a", TABLE_A) + r"\end{table}"
        after = r"\begin{table}" + object_body("tab:a", TABLE_A, captionof=True) + r"\end{table}"
        self.assertEqual(scientific.scientific_payload_diff(before, after)["status"], "pass")

    def test_captionof_long_caption_negation_change_is_protected(self):
        before = combined().replace("Results tab:a", "The method does not improve accuracy.")
        after = before.replace("does not improve", "does improve")
        self.assertEqual(scientific.scientific_payload_diff(before, after)["status"], "failed")

    def test_short_caption_and_star_are_protected(self):
        before = r"\begin{table}" + object_body("tab:a", TABLE_A, captionof=True, short="91.2 result") + r"\end{table}"
        for after in (before.replace("91.2 result", "92.2 result"), before.replace(r"\captionof", r"\captionof*")):
            with self.subTest(after=after):
                self.assertEqual(scientific.scientific_payload_diff(before, after)["status"], "failed")

    def test_table_data_cannot_swap_between_same_container_labels(self):
        self.assertEqual(scientific.scientific_payload_diff(combined(), combined(TABLE_B, TABLE_A))["status"], "failed")

    def test_math_cannot_swap_between_same_container_labels(self):
        before = combined(math_a=r"$x+1$", math_b=r"$y-2$")
        after = combined(math_a=r"$y-2$", math_b=r"$x+1$")
        self.assertEqual(scientific.scientific_payload_diff(before, after)["status"], "failed")

    def test_caption_type_change_is_protected(self):
        before = combined()
        after = before.replace(r"\captionof{table}", r"\captionof{figure}", 1)
        self.assertEqual(scientific.scientific_payload_diff(before, after)["status"], "failed")

    def test_duplicate_label_or_new_duplicate_payload_cannot_fake_composition(self):
        for after in (combined().replace("tab:b", "tab:a"), combined(TABLE_A, TABLE_A)):
            with self.subTest(after=after):
                self.assertEqual(scientific.scientific_payload_diff(separate(), after)["status"], "failed")

    def test_unsupported_captionof_invocation_keeps_opaque_text(self):
        for invocation in (r"\captionof{unknown}{The method does not improve.}",
                           r"\captionof{\dynamic}{The method does not improve.}"):
            before = r"\begin{figure}" + invocation + r"\label{fig:a}\end{figure}"
            after = before.replace("does not improve", "does improve")
            with self.subTest(invocation=invocation):
                self.assertEqual(scientific.scientific_payload_diff(before, after)["status"], "failed")

    def test_shadowed_captionof_invocation_keeps_opaque_text(self):
        before = r"\newcommand{\captionof}[2]{#2}" + combined()
        after = before.replace("Results tab:a", "Different conclusion")
        self.assertEqual(scientific.scientific_payload_diff(before, after)["status"], "failed")

    def test_inventory_exposes_explicit_complete_object_coverage(self):
        inventory = getattr(scientific, "standard_layout_object_inventory", None)
        self.assertTrue(callable(inventory))
        report = inventory(combined())
        self.assertTrue(report["complete"], report)
        self.assertEqual([(o["family"], o["label"]) for o in report["objects"]], [("table", "tab:a"), ("table", "tab:b")])
        unsupported = inventory(combined() + r"\begin{figure}Unowned content.\end{figure}")
        self.assertFalse(unsupported["complete"], unsupported)

    def test_complete_object_can_leave_float_for_standard_minipage(self):
        before = r"\begin{table}" + object_body("tab:a", TABLE_A) + r"\end{table}"
        after = r"\begin{minipage}{.9\linewidth}" + object_body("tab:a", TABLE_A, captionof=True) + r"\end{minipage}"
        self.assertEqual(scientific.scientific_payload_diff(before, after)["status"], "pass")
        self.assertEqual(prose.compare_layout_prose(before, after)["status"], "pass")

    def test_caption_fields_are_bound_to_their_own_objects(self):
        before = combined()
        after = before.replace("Results tab:a", "SWAP").replace("Results tab:b", "Results tab:a").replace("SWAP", "Results tab:b")
        self.assertEqual(scientific.scientific_payload_diff(before, after)["status"], "failed")

    def test_local_ordinary_claims_and_identifiers_cannot_swap(self):
        for a, b in (("The method does not improve.", "The method improves."),
                     (r"\cite{paperA}", r"\cite{paperB}")):
            before = combined(math_a=a, math_b=b)
            after = combined(math_a=b, math_b=a)
            with self.subTest(a=a):
                self.assertEqual(scientific.scientific_payload_diff(before, after)["status"], "failed")

    def test_ordered_math_and_table_payload_inside_one_object_is_protected(self):
        for a, b in ((r"$x+1$$y-2$", r"$y-2$$x+1$"),
                     (TABLE_A + TABLE_B, TABLE_B + TABLE_A)):
            before = r"\begin{table}" + object_body("tab:a", a) + r"\end{table}"
            after = r"\begin{table}" + object_body("tab:a", b) + r"\end{table}"
            with self.subTest(a=a):
                self.assertEqual(scientific.scientific_payload_diff(before, after)["status"], "failed")

    def test_duplicate_labels_do_not_allow_data_exchange(self):
        before = combined().replace("tab:b", "tab:a")
        after = combined(TABLE_B, TABLE_A).replace("tab:b", "tab:a")
        self.assertFalse(scientific.standard_layout_object_inventory(before)["complete"])
        self.assertEqual(scientific.scientific_payload_diff(before, after)["status"], "failed")

    def test_unowned_float_gap_words_and_numbers_remain_protected(self):
        before = combined().replace(r"\par\medskip", " The method does not improve 91.2. ")
        for after in (before.replace("does not improve", "does improve"), before.replace("91.2.", "92.2.")):
            with self.subTest(after=after):
                self.assertEqual(scientific.scientific_payload_diff(before, after)["status"], "failed")
        self.assertFalse(scientific.standard_layout_object_inventory(before)["complete"])

    def test_opaque_scopes_or_nested_label_do_not_get_complete_coverage(self):
        sources = [
            combined(minipage=True).replace(r"{.45\linewidth}", r"{\dynamicwidth}"),
            r"\newenvironment{minipage}[1]{}{}" + combined(minipage=True),
            r"\def\begingroup{+c}" + combined(),
            combined().replace(r"\begingroup", r"\begingroup*", 1),
            r"\hidden{" + combined() + "}",
            r"\begin{unknown}" + combined() + r"\end{unknown}",
            r"\ifcondition" + combined() + r"\fi",
            r"\begin{table}" + object_body("tab:a", TABLE_A).replace(r"\label{tab:a}", "").replace("A$", r"A\label{tab:a}$") + r"\end{table}",
        ]
        for source in sources:
            with self.subTest(source=source):
                self.assertFalse(scientific.standard_layout_object_inventory(source)["complete"])

    def test_malformed_caption_invocations_remain_opaque(self):
        for cap in (r"\captionof{table}[short{The method does not improve.}",
                    r"\captionof{table}{The method does not improve."):
            before = r"\begin{table}" + TABLE_A + cap
            self.assertEqual(scientific.scientific_payload_diff(before, before.replace("does not improve", "does improve"))["status"], "failed")
            self.assertFalse(scientific.standard_layout_object_inventory(before)["complete"])

    def test_graphics_order_path_star_and_full_options_are_reported(self):
        source = r"\begin{figure}" + object_body("fig:a", r"\includegraphics*[width=.4\linewidth,trim=1 2 3 4]{a.pdf}\includegraphics{b.pdf}", family="figure") + r"\end{figure}"
        graphics = scientific.standard_layout_object_inventory(source)["objects"][0]["graphics"]
        self.assertEqual([(g["path"], g["star"], g["options"]) for g in graphics], [("a.pdf", True, "[width=.4\\linewidth,trim=1 2 3 4]"), ("b.pdf", False, "")])
        self.assertEqual(scientific.scientific_payload_diff(source, source.replace(r"\includegraphics*", r"\includegraphics", 1))["status"], "failed")

    def test_identified_migration_cannot_waive_caption_star_short_or_refs(self):
        body = r"\caption[Exact 91.2]{Results \ref{eq:a} then \ref{eq:b}}\label{tab:a}\begin{tabular}{lr}A&91.2\\\end{tabular}"
        before = r"\begin{table}" + body + r"\end{table}"
        for after in (body.replace(r"\caption", r"\caption*", 1),
                      body.replace("Exact 91.2", "Exact 92.2"),
                      body.replace("eq:a", "SWAP").replace("eq:b", "eq:a").replace("SWAP", "eq:b")):
            with self.subTest(after=after):
                report = scientific.scientific_payload_diff(before, after)
                self.assertEqual(report["status"], "failed", report)
                self.assertNotEqual(report["layout_equivalence"]["status"], "proven", report)
        report = scientific.scientific_payload_diff(before.replace(r"\caption", r"\caption*", 1), body)
        self.assertEqual(report["status"], "failed", report)
        self.assertNotEqual(report["layout_equivalence"]["status"], "proven", report)

    def test_global_conditional_or_nested_length_definitions_stay_shadowed(self):
        declarations = [r"\begin{table}\global\def\tabcolsep{2pt}\end{table}",
                        r"\newcommand{\install}{\begin{table}\def\tabcolsep{2pt}\end{table}}",
                        r"\begin{table}\ifcondition\def\tabcolsep{2pt}\fi\end{table}"]
        for declaration in declarations:
            with self.subTest(declaration=declaration):
                before = declaration + r"\setlength{\tabcolsep}{3pt}"
                after = before.replace(r"\tabcolsep}{3pt}", r"\tabcolsep}{4pt}")
                self.assertEqual(scientific.scientific_payload_diff(before, after)["status"], "failed")

    def test_identified_proof_cannot_waive_other_object_or_asset_changes(self):
        body = r"\caption{Exact values}\label{tab:first}\begin{tabular}{lr}A&91.2\\\end{tabular}"
        before = r"\begin{table}" + body + r"\end{table}" + combined(math_a=r"$x+1$ The method does not improve.")
        after = body + combined(math_a=r"$x+1$ The method does not improve.")
        for changed in (after.replace("does not improve", "does improve"),
                        after.replace("82.4", "83.4"),
                        after.replace("$x+1$", "$x-1$")):
            with self.subTest(changed=changed):
                report = scientific.scientific_payload_diff(before, changed)
                self.assertEqual(report["status"], "failed", report)
                self.assertNotEqual(report["layout_equivalence"]["status"], "proven", report)
        asset = r"\begin{figure}\includegraphics{a.pdf}\caption{Measured object}\label{fig:a}\end{figure}"
        report = scientific.scientific_payload_diff(before + asset, after + asset.replace("a.pdf", "b.pdf"))
        self.assertEqual(report["status"], "failed", report)
        self.assertNotEqual(report["layout_equivalence"]["status"], "proven", report)

    def test_cell_reference_order_is_attached_to_the_exact_cell(self):
        body = TABLE_A.replace("A$", r"A\ref{eq:a} then \ref{eq:b}$")
        before = r"\begin{table}" + object_body("tab:a", body) + r"\end{table}"
        after = before.replace("eq:a", "SWAP").replace("eq:b", "eq:a").replace("SWAP", "eq:b")
        self.assertEqual(scientific.scientific_payload_diff(before, after)["status"], "failed")

    def test_shadowed_adjustbox_options_are_not_standard_layout(self):
        prefix = r"\newenvironment{adjustbox}[1]{#1}{}"
        before = prefix + r"\begin{table}\begin{adjustbox}{width=91.2pt}" + TABLE_A + r"\end{adjustbox}\caption{Results}\label{tab:a}\end{table}"
        self.assertFalse(scientific.standard_layout_object_inventory(before)["complete"])
        after = before.replace("width=91.2pt", "width=92.2pt")
        self.assertEqual(scientific.scientific_payload_diff(before, after)["status"], "failed")


if __name__ == "__main__":
    unittest.main()
