"""Allow a standard multiline cell without overlooking table content changes."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from content_integrity_check import compute_content_diff, has_scientific_regression
from layout_policy import compare_layout_policy
from layout_prose_integrity import compare_layout_prose
from scientific_integrity import scientific_payload_diff


BEFORE = r"""\documentclass[twocolumn]{article}
\usepackage{tabularx,makecell}
\begin{document}
The measured values are summarized below.
\begin{table}
\begin{tabular}{lr}
Long descriptive method name and configuration & Score \\
Reference baseline method & 1.25 \\
\end{tabular}
\caption{Results.}\label{tab:results}
\end{table}
The interpretation is unchanged.
\end{document}
"""


def wrapped_table(alignment: str | None = 'l', *, star: bool = False) -> str:
    optional = '' if alignment is None else '[' + alignment + ']'
    wrapper = r'\makecell' + ('*' if star else '') + optional
    return (BEFORE.replace(r'\begin{tabular}{lr}', r'\begin{tabularx}{\linewidth}{Xr}')
            .replace(r'\end{tabular}', r'\end{tabularx}')
            .replace('Long descriptive method name and configuration',
                     wrapper + r'{Long descriptive\\method name\\and configuration}'))


class MakecellIntegrityTest(unittest.TestCase):
    def test_compiled_positive_fixture_passes_all_three_channels(self) -> None:
        after = wrapped_table()
        for check in (compare_layout_policy, compare_layout_prose, scientific_payload_diff):
            result = check(BEFORE, after)
            self.assertEqual(result['status'], 'pass', (check.__name__, result))
        diff = compute_content_diff(BEFORE, after)
        self.assertFalse(has_scientific_regression(diff), diff)
        self.assertEqual(diff['action_required'], 'log_only', diff)

    def test_default_and_literal_standard_alignments(self) -> None:
        alignments = (None, 'l', 'c', 'r', 't', 'b', 'tl', 'tc', 'tr',
                      'bl', 'bc', 'br', 'lt', 'ct', 'rt', 'lb', 'cb', 'rb', 'cc')
        for alignment in alignments:
            with self.subTest(alignment=alignment):
                self.assertEqual(scientific_payload_diff(BEFORE, wrapped_table(alignment))['status'], 'pass')

    def test_words_data_caption_and_label_remain_protected(self) -> None:
        after = wrapped_table()
        for old, new in (('1.25', '1.30'), ('configuration}', 'implementation}'),
                         ('Reference baseline method', 'Other baseline method'),
                         ('Results.', 'Different results.'), ('tab:results', 'tab:other')):
            with self.subTest(change=(old, new)):
                diff = compute_content_diff(BEFORE, after.replace(old, new))
                self.assertTrue(has_scientific_regression(diff), diff)
                self.assertEqual(diff['action_required'], 'auto_rollback', diff)

    def test_unknown_and_dynamic_alignment_is_not_exempt(self) -> None:
        for alignment in (r'\alignment', r'{p{2cm}}', r'l@{99}', 'll', 'tb', ''):
            with self.subTest(alignment=alignment):
                result = scientific_payload_diff(BEFORE, wrapped_table(alignment))
                self.assertEqual(result['status'], 'failed', result)
                self.assertIn('tables', result['changed_components'])
        self.assertEqual(scientific_payload_diff(BEFORE, wrapped_table(star=True))['status'], 'failed')

    def test_unsupported_wrapper_keeps_body_structure_opaque(self) -> None:
        before = wrapped_table(r'\alignment')
        after = before.replace(r'Long descriptive\\method name', 'Long descriptive method name')
        self.assertEqual(scientific_payload_diff(before, after)['status'], 'failed')

    def test_macro_override_remains_protected(self) -> None:
        after = wrapped_table().replace(r'\begin{document}',
                                        r'\renewcommand{\makecell}[1]{Changed words}\begin{document}')
        result = scientific_payload_diff(BEFORE, after)
        self.assertEqual(result['status'], 'failed', result)
        self.assertIn('macro_definitions', result['changed_components'])


if __name__ == '__main__':
    unittest.main()
