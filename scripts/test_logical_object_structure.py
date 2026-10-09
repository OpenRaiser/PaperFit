"""Container composition may retain all objects; content loss may not."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from content_integrity_check import compute_content_diff, has_structure_regression
from test_scientific_layout_objects import combined, separate, TABLE_A, TABLE_B


def doc(body):
    return r'\documentclass{article}\begin{document}Fixed ordinary prose. ' + body + r'\end{document}'


class LogicalObjectStructureTest(unittest.TestCase):
    def test_complete_group_and_minipage_composition_keeps_raw_counts_and_passes(self):
        for after in (combined(), combined(minipage=True)):
            with self.subTest(after=after):
                result = compute_content_diff(doc(separate()), doc(after), layout_only=True)
                self.assertEqual(result['scientific_payload']['status'], 'pass', result)
                self.assertEqual(result['layout_prose']['status'], 'pass', result)
                self.assertEqual(result['layout_policy']['status'], 'pass', result)
                structure = result['structure_changes']
                self.assertEqual(structure['before']['table_count'], 2)
                self.assertEqual(structure['after']['table_count'], 0)
                self.assertEqual(structure['after']['figure_count'], 1)
                self.assertEqual(structure['container_rearrangement']['status'], 'supported_objects_preserved')
                self.assertFalse(has_structure_regression(result), result)
                self.assertNotEqual(result['action_required'], 'auto_rollback', result)

    def test_changed_cells_caption_math_or_body_cannot_gain_container_exemption(self):
        before = doc(separate())
        for after in (combined(TABLE_B, TABLE_A), combined().replace('91.2', '91.3'),
                      combined().replace('Results tab:a', 'Other conclusion'),
                      combined() + r'$x-1$', combined().replace('tab:b', 'tab:a')):
            with self.subTest(after=after):
                result = compute_content_diff(before, doc(after), layout_only=True)
                self.assertEqual(result['action_required'], 'auto_rollback', result)
                self.assertEqual(result['structure_changes']['container_rearrangement']['status'], 'not_established')

    def test_unowned_extra_float_claims_and_unlabelled_removal_stay_blocked(self):
        before = doc(separate() + r'\begin{figure}Unowned scientific content.\end{figure}')
        after = doc(combined() + r'\begin{figure}Unowned scientific content.\end{figure}')
        result = compute_content_diff(before, after, layout_only=True)
        self.assertEqual(result['structure_changes']['container_rearrangement']['status'], 'not_established')
        self.assertTrue(has_structure_regression(result), result)
        removed = compute_content_diff(before, doc(combined()), layout_only=True)
        self.assertEqual(removed['action_required'], 'auto_rollback', removed)

    def test_combining_objects_after_bibliography_does_not_hide_endmatter_intrusion(self):
        before = doc(separate() + r'\bibliography{refs}')
        after = doc(r'\bibliography{refs}' + combined())
        result = compute_content_diff(before, after, layout_only=True)
        self.assertEqual(result['action_required'], 'auto_rollback', result)
        self.assertEqual(result['structure_changes']['container_rearrangement']['status'], 'not_established')
        self.assertEqual(result['structure_changes']['container_rearrangement']['new_objects_after_bibliography'],
                         ['tab:a', 'tab:b'])


if __name__ == '__main__':
    unittest.main()
