"""Caption associations and reference-counter order remain explicit evidence."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scientific_integrity as scientific
from content_integrity_check import compute_content_diff
from test_scientific_layout_objects import combined, separate


def document(body):
    return (r"\documentclass{article}\begin{document}\section{Methods}"
            "Body remains 91.2 exactly. " + body + r"\end{document}")


BODY = r"\caption{Measured values}\label{tab:a}\begin{tabular}{cc}A&1\\B&2\end{tabular}"
TABLE = r"\begin{table}" + BODY + r"\end{table}"
BEFORE_LABEL = TABLE.replace(r"\caption{Measured values}\label{tab:a}",
                             r"\label{tab:a}\caption{Measured values}")
COMPOUND = (r"\begin{figure}\includegraphics{a.pdf}\caption{Measured A}\label{fig:a}"
            r"\includegraphics{b.pdf}\caption{Measured B}\label{fig:b}\end{figure}")


class CaptionBindingTests(unittest.TestCase):
    def test_label_counter_order_drift_is_pending_with_raw_failure(self):
        for before, after in ((TABLE, BEFORE_LABEL), (BEFORE_LABEL, TABLE)):
            with self.subTest(before=before):
                result = compute_content_diff(document(before), document(after), layout_only=True)
                self.assertEqual(result["action_required"], "manual_review", result)
                self.assertEqual(result["scientific_payload"]["status"], "manual_review", result)
                self.assertEqual(result["scientific_payload"]["automated_report"]["raw_comparison"]["status"], "failed")
                self.assertEqual(result["scientific_payload"]["reference_anchor_uncertainty"]["status"], "pending")

    def test_unchanged_old_before_caption_order_is_not_universally_forbidden(self):
        after = BEFORE_LABEL.replace("{cc}", "{lr}")
        self.assertEqual(scientific.scientific_payload_diff(document(BEFORE_LABEL), document(after))["status"], "pass")

    def test_intentional_counter_control_and_label_relation_are_reported(self):
        before = BEFORE_LABEL.replace(r"\label{tab:a}", r"\refstepcounter{table}\label{tab:a}")
        inventory = scientific.standard_layout_object_inventory(before)
        self.assertTrue(inventory["complete"])
        binding = inventory["objects"][0].get("label_binding")
        self.assertIsInstance(binding, dict)
        self.assertEqual(binding["relation"], "before_caption")
        self.assertEqual(binding["counter_controls"][0]["command"], "refstepcounter")
        self.assertEqual(scientific.scientific_payload_diff(before, before.replace("{cc}", "{lr}"))["status"], "pass")
        after = before.replace(r"\refstepcounter{table}\label{tab:a}", r"\label{tab:a}\refstepcounter{table}")
        result = scientific.scientific_payload_diff(before, after)
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["reference_anchor_uncertainty"]["status"], "pending", result)

    def test_label_order_review_cannot_mask_caption_cell_math_or_counter_drift(self):
        before = TABLE.replace("A&1", r"A$x+1$&1")
        after = before.replace(r"\caption{Measured values}\label{tab:a}", r"\label{tab:a}\caption{Measured values}")
        for changed in (after.replace("Measured values", "Different conclusion"),
                        after.replace("B&2", "B&3"), after.replace("$x+1$", "$x-1$"),
                        after.replace(r"\label{tab:a}", r"\refstepcounter{table}\label{tab:a}")):
            with self.subTest(changed=changed):
                result = scientific.scientific_payload_diff(before, changed)
                self.assertEqual(result["status"], "failed", result)
                self.assertNotEqual(result["reference_anchor_uncertainty"]["status"], "pending", result)

    def test_shadowed_counter_controls_do_not_supply_standard_binding_review(self):
        for definition in (r"\def\refstepcounter#1{#1}",
                           r"\newcommand{\holder}{\def\refstepcounter#1{#1}}"):
            with self.subTest(definition=definition):
                before = definition + BEFORE_LABEL.replace(r"\label{tab:a}", r"\refstepcounter{table}\label{tab:a}")
                binding = scientific.standard_layout_object_inventory(before)["objects"][0]["label_binding"]
                self.assertFalse(binding["counter_controls"][0]["supported"])
                after = before.replace(r"\refstepcounter{table}\label{tab:a}", r"\label{tab:a}\refstepcounter{table}")
                result = scientific.scientific_payload_diff(before, after)
                self.assertEqual(result["status"], "failed", result)
                self.assertNotEqual(result["reference_anchor_uncertainty"]["status"], "pending", result)

    def test_unowned_compound_caption_exchange_cannot_use_global_counter(self):
        after = COMPOUND.replace("Measured A", "TEMP").replace("Measured B", "Measured A").replace("TEMP", "Measured B")
        self.assertFalse(scientific.standard_layout_object_inventory(COMPOUND)["complete"])
        result = compute_content_diff(document(COMPOUND), document(after), layout_only=True)
        self.assertEqual(result["action_required"], "auto_rollback", result)
        self.assertEqual(result["scientific_payload"]["status"], "failed", result)

    def test_unowned_compound_label_exchange_or_missing_duplicate_is_not_preserved(self):
        changes = [COMPOUND.replace("fig:a", "TEMP").replace("fig:b", "fig:a").replace("TEMP", "fig:b"),
                   COMPOUND.replace(r"\caption{Measured A}", "", 1),
                   COMPOUND.replace(r"\caption{Measured A}", r"\caption{Measured A}\caption{Measured A}", 1)]
        for after in changes:
            with self.subTest(after=after):
                self.assertEqual(scientific.scientific_payload_diff(COMPOUND, after)["status"], "failed")

    def test_supported_composition_and_whole_object_order_remain_available(self):
        for after in (combined(), combined(minipage=True)):
            self.assertEqual(scientific.scientific_payload_diff(separate(), after)["status"], "pass")
        first, second = COMPOUND.split(r"\includegraphics{b.pdf}")
        first = first.removeprefix(r"\begin{figure}")
        second = r"\includegraphics{b.pdf}" + second.removesuffix(r"\end{figure}")
        before = r"\begin{figure}\begingroup" + first + r"\endgroup\begingroup" + second + r"\endgroup\end{figure}"
        after = r"\begin{figure}\begingroup" + second + r"\endgroup\begingroup" + first + r"\endgroup\end{figure}"
        self.assertEqual(scientific.scientific_payload_diff(before, after)["status"], "pass")

    def test_identified_migration_does_not_erase_caption_label_order(self):
        before = TABLE
        after = BODY.replace(r"\caption{Measured values}\label{tab:a}", r"\label{tab:a}\caption{Measured values}")
        result = scientific.scientific_payload_diff(before, after)
        self.assertEqual(result["status"], "failed", result)
        self.assertNotEqual(result["layout_equivalence"]["status"], "proven", result)


if __name__ == "__main__":
    unittest.main()
