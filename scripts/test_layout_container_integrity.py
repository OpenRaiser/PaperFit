"""Accept real container reflow while protecting scientific payload and floats."""
import unittest

from content_integrity_check import compute_content_diff, has_structure_regression
from scientific_integrity import minipage_layout_end


def document(body):
    return '\\documentclass{article}\n\\begin{document}\n' + body + '\n\\end{document}\n'


class LayoutContainerIntegrityTest(unittest.TestCase):
    def test_literal_minipage_reflow_preserves_body_number(self):
        body = 'Accuracy is 0.47 and the method does not improve recall.'
        for header in (r'{0.47\linewidth}', r'[t]{.47\columnwidth}',
                       r'[b][12pt][s]{6cm}', r'[c][][t]{\textwidth}'):
            with self.subTest(header=header):
                before = document(r'\begin{minipage}' + header + body + r'\end{minipage}')
                diff = compute_content_diff(before, document(body), layout_only=True)
                self.assertEqual(diff['scientific_payload']['status'], 'pass', diff)
                self.assertEqual(diff['layout_prose']['status'], 'pass', diff)
                self.assertEqual(diff['hash_comparison']['identical'], True, diff)
                self.assertEqual(diff['action_required'], 'none', diff)

    def test_body_number_change_in_same_container_is_rejected(self):
        before = document(r'\begin{minipage}[t]{0.47\linewidth}Accuracy is 0.47.\end{minipage}')
        diff = compute_content_diff(before, before.replace('Accuracy is 0.47', 'Accuracy is 0.74'), layout_only=True)
        self.assertEqual(diff['scientific_payload']['status'], 'failed')
        self.assertEqual(diff['action_required'], 'auto_rollback')

    def test_table_numeric_change_remains_rejected_during_reflow(self):
        table = r'\begin{table}\caption{Results}\label{tab:r}\begin{minipage}{0.47\linewidth}\begin{tabular}{lr}Method&Value\\A&0.47\\\end{tabular}\end{minipage}\end{table}'
        changed = table.replace(r'\begin{minipage}{0.47\linewidth}', '').replace(r'\end{minipage}', '').replace('A&0.47', 'A&0.74')
        diff = compute_content_diff(document(table), document(changed), layout_only=True)
        self.assertIn('tables', diff['scientific_payload']['changed_components'])
        self.assertEqual(diff['action_required'], 'auto_rollback')

    def test_unknown_or_malformed_minipage_arguments_stay_opaque(self):
        for header in (r'{\customWidth}', r'{\dimexpr.47\linewidth+2pt\relax}',
                       r'[\customAlign]{0.47\linewidth}', r'[t][\claim{42}]{0.47\linewidth}',
                       r'[t][12pt][\customInner]{0.47\linewidth}', r'[t{0.47\linewidth}'):
            with self.subTest(header=header):
                self.assertIsNone(minipage_layout_end(header, 0))
                before = document(r'\begin{minipage}' + header + 'The result remains.' + r'\end{minipage}')
                diff = compute_content_diff(before, document('The result remains.'), layout_only=True)
                self.assertEqual(diff['action_required'], 'auto_rollback', diff)

    def test_float_family_reflow_has_same_inventory(self):
        for family in ('figure', 'table'):
            inner = (r'\includegraphics{plot.pdf}' if family == 'figure' else
                     r'\begin{tabular}{lr}A&0.47\\\end{tabular}')
            payload = r'\caption{Accuracy is 0.47}\label{' + family + ':r}' + inner
            for environment, args in (('wrap'+family, r'{r}{0.47\linewidth}'),
                                      ('sideways'+family, '')):
                with self.subTest(environment=environment):
                    before = document(r'\begin{' + environment + '}' + args + payload + r'\end{' + environment + '}')
                    after = document(r'\begin{' + family + '*}' + payload + r'\end{' + family + '*}')
                    diff = compute_content_diff(before, after, layout_only=True)
                    self.assertFalse(has_structure_regression(diff), diff)
                    self.assertEqual(diff['scientific_payload']['status'], 'pass', diff)

    def test_reflow_does_not_hide_float_deletion_or_payload_loss(self):
        before = document(r'\begin{wrapfigure}{r}{0.47\linewidth}\caption{Result 0.47}\label{fig:r}\includegraphics{plot.pdf}\end{wrapfigure}')
        converted = before.replace(r'\begin{wrapfigure}{r}{0.47\linewidth}', r'\begin{figure}').replace(r'\end{wrapfigure}', r'\end{figure}')
        for changed in (document(''), converted.replace(r'\label{fig:r}', ''),
                        converted.replace(r'\includegraphics{plot.pdf}', ''),
                        converted.replace('Result 0.47', 'Result 0.74')):
            with self.subTest(changed=changed):
                diff = compute_content_diff(before, changed, layout_only=True)
                self.assertEqual(diff['action_required'], 'auto_rollback', diff)

    def test_commented_fake_floats_do_not_change_inventory(self):
        before = document(r'\begin{figure}\label{fig:r}\includegraphics{plot.pdf}\end{figure}')
        after = before + '% \\begin{wrapfigure}{r}{.47\\linewidth}\\label{fake}\\end{wrapfigure}\n'
        diff = compute_content_diff(before, after, layout_only=True)
        self.assertFalse(has_structure_regression(diff), diff)
        self.assertEqual(diff['action_required'], 'none', diff)

    def test_wrapped_float_after_bibliography_is_still_detected(self):
        before = document(r'\begin{wrapfigure}{r}{.47\linewidth}\label{fig:r}\includegraphics{plot.pdf}\end{wrapfigure}\bibliography{refs}')
        after = before.replace(r'\bibliography{refs}', '').replace(r'\begin{wrapfigure}', r'\bibliography{refs}\begin{wrapfigure}')
        diff = compute_content_diff(before, after, layout_only=True)
        self.assertTrue(has_structure_regression(diff), diff)
        self.assertEqual(diff['action_required'], 'auto_rollback', diff)


if __name__ == '__main__':
    unittest.main()
