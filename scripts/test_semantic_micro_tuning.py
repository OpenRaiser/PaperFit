from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
for entry in (str(SCRIPTS_DIR), str(REPO_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from skills.latex_fixers.semantic_micro_tuning import (  # noqa: E402
    deep_expand,
    minimalist_shorten,
    semantic_intervention,
)


SHORTEN_TEX = (
    "We do this in order to reduce cost, due to the fact that it helps. "
    "The study was conducted by our team and the analysis was conducted by the same group."
)

EXPAND_TEX = (
    "This method works. The baseline improves accuracy. "
    "Our model improves speed. These results matter."
)


class MinimalistShortenTest(unittest.TestCase):
    def test_filler_phrases_are_removed_with_correct_count(self) -> None:
        modified, record = minimalist_shorten(SHORTEN_TEX)
        self.assertIn("to reduce cost", modified)
        self.assertIn("because it helps", modified)
        self.assertNotIn("in order to", modified)
        self.assertNotIn("due to the fact that", modified)
        # in order to→to (2) + due to the fact that→because (4) fillers,
        # plus two was-conducted-by rewrites (2 each).
        self.assertEqual(record["words_removed"], 10)

    def test_repeated_pattern_all_occurrences_are_replaced(self) -> None:
        modified, record = minimalist_shorten(SHORTEN_TEX)
        self.assertEqual(modified.count("was conducted by"), 0)
        # The rewrites drop the auxiliary and the stray leading space.
        self.assertIn("The study conducted our team", modified)
        self.assertNotIn("  conducted", modified)
        self.assertGreaterEqual(record["words_removed"], 5)

    def test_budget_is_respected(self) -> None:
        modified, record = minimalist_shorten(SHORTEN_TEX, max_words_to_remove=2)
        self.assertEqual(record["words_removed"], 2)

    def test_section_scoping(self) -> None:
        tex = (
            "\\section{Discussion}\nWe act in order to help.\n"
            "\\section{Conclusion}\nWe act in order to help again.\n"
        )
        modified, record = minimalist_shorten(tex, target_section="Discussion")
        self.assertNotIn("in order to help.\n\\section{Conclusion}", modified)
        self.assertIn("We act in order to help again.", modified)
        self.assertEqual(record["words_removed"], 2)


class DeepExpandTest(unittest.TestCase):
    def test_no_literal_group_reference_leaks_into_text(self) -> None:
        modified, _ = deep_expand(EXPAND_TEX)
        self.assertNotIn("\\1", modified)
        self.assertNotIn("\\2", modified)

    def test_leading_sentence_gets_transition_word(self) -> None:
        modified, record = deep_expand(EXPAND_TEX, max_words_to_add=2)
        self.assertTrue(
            modified.startswith("Notably, This method works."),
            modified[:60],
        )
        self.assertGreaterEqual(record["words_added"], 1)

    def test_self_containing_replacement_applies_once_per_occurrence(self) -> None:
        modified, _ = deep_expand("The method improves accuracy.", max_words_to_add=6)
        self.assertIn("significantly improves", modified)
        self.assertNotIn("significantly significantly", modified)
        self.assertNotIn("consistently significantly", modified)

    def test_causal_phrase_is_inserted_after_the_period(self) -> None:
        modified, _ = deep_expand("A works. B follows.", max_words_to_add=30)
        self.assertRegex(modified, r"A works\. This finding aligns with prior work\. B follows\.")
        self.assertNotIn(".This finding", modified)  # must not glue to the next word
        self.assertNotIn("works This", modified)  # must not lose the period

    def test_word_budget_caps_additions(self) -> None:
        modified, record = deep_expand(EXPAND_TEX, max_words_to_add=3)
        self.assertLessEqual(record["words_added"], 3)


class SemanticInterventionTest(unittest.TestCase):
    def test_auto_expand_on_positive_deficit(self) -> None:
        modified, record = semantic_intervention(EXPAND_TEX, page_deficit=1)
        self.assertEqual(record["intervention_type"], "expand")
        self.assertGreater(record.get("words_added", 0), 0)

    def test_auto_shorten_on_negative_deficit(self) -> None:
        modified, record = semantic_intervention(SHORTEN_TEX, page_deficit=-1)
        self.assertEqual(record["intervention_type"], "shorten")
        self.assertGreater(record.get("words_removed", 0), 0)

    def test_zero_deficit_is_a_noop(self) -> None:
        modified, record = semantic_intervention(EXPAND_TEX, page_deficit=0)
        self.assertEqual(modified, EXPAND_TEX)
        self.assertEqual(record["action"], "none")


if __name__ == "__main__":
    unittest.main()
