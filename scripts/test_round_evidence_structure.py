from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from regression.round_acceptance import build_round_evidence_from_state, evaluate_round_acceptance


class RoundEvidenceStructureTest(unittest.TestCase):
    def test_after_counts_use_repaired_structure(self):
        before = {'figure_count': 3, 'table_count': 2,
                  'figure_labels': ['fig:a', 'fig:b', 'fig:c'], 'table_labels': ['tab:a', 'tab:b']}
        after = {'figure_count': 2, 'table_count': 1,
                 'figure_labels': ['fig:a', 'fig:b'], 'table_labels': ['tab:a']}
        state = {'content_integrity': {'structure_changes': {'before': before, 'after': after}}}
        evidence = build_round_evidence_from_state(state_before=state, state_after=state,
            rule_report_before={'summary': {'overfull_hbox_total': 2}},
            rule_report_after={'summary': {'overfull_hbox_total': 1}})
        self.assertEqual((evidence.figures_before, evidence.figures_after), (3, 2))
        self.assertEqual((evidence.tables_before, evidence.tables_after), (2, 1))
        self.assertEqual((evidence.labels_before, evidence.labels_after), (5, 3))
        self.assertFalse(evaluate_round_acceptance(evidence).accepted)


if __name__ == '__main__':
    unittest.main()
