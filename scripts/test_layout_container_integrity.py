"""Accept real container reflow while protecting scientific payload and floats."""
import unittest

from content_integrity_check import compute_content_diff, has_structure_regression
from scientific_integrity import minipage_layout_end


def document(body):
    return '\\documentclass{article}\n\\begin{document}\n' + body + '\n\\end{document}\n'


class LayoutContainerIntegrityTest(unittest.TestCase):
    def test_inherited_shadowed_wrapper_transfer_between_labelled_captions_is_rejected(self):
        token = 'BenchmarkOverflowTokenWithoutBreakpoints1234567890'
        definition = r'\renewcommand{\nolinkurl}[1]{DifferentScientificIdentifier}'
        def figure(label, wrapped):
            value = r'\nolinkurl{' + token + '}' if wrapped else token
            return r'\begin{figure}\caption{Identifier ' + value + r'}\label{' + label + r'}\end{figure}'
        before = definition + '\n' + document(figure('fig:a', True) + figure('fig:b', False))
        after = definition + '\n' + document(figure('fig:a', False) + figure('fig:b', True))
        diff = compute_content_diff(before, after, layout_only=True)
        self.assertNotIn('shadowed_literal_url_invocations', diff['scientific_payload']['changed_components'])
        self.assertIn('protected_arguments', diff['scientific_payload']['changed_components'])
        self.assertEqual(diff['action_required'], 'auto_rollback', diff)

    def test_inherited_shadowed_wrapper_transfer_between_fields_of_same_float_is_rejected(self):
        token = 'BenchmarkOverflowTokenWithoutBreakpoints1234567890'
        definition = r'\renewcommand{\nolinkurl}[1]{DifferentScientificIdentifier}'
        wrapped = r'\textbf{\nolinkurl{' + token + '}}'
        plain = r'\textbf{' + token + '}'
        def figure(caption, note):
            return (r'\begin{figure}\caption{Identifier ' + caption + r'}\label{fig:r}' +
                    r'\footnote{Identifier ' + note + r'}\end{figure}')
        before = definition + '\n' + document(figure(wrapped, plain))
        after = definition + '\n' + document(figure(plain, wrapped))
        diff = compute_content_diff(before, after, layout_only=True)
        self.assertNotIn('shadowed_literal_url_invocations', diff['scientific_payload']['changed_components'])
        self.assertIn('protected_arguments', diff['scientific_payload']['changed_components'])
        self.assertEqual(diff['action_required'], 'auto_rollback', diff)

    def test_inherited_shadowed_wrapper_transfer_between_table_cells_is_rejected(self):
        token = 'BenchmarkOverflowTokenWithoutBreakpoints1234567890'
        for command, invocation in [('nolinkurl', r'\nolinkurl{' + token + '}'),
                                    ('urlstyle', r'\urlstyle{same}' + token)]:
            with self.subTest(command=command):
                definition = '\\renewcommand{\\' + command + '}[1]{DifferentScientificIdentifier}'
                def table(first, second):
                    return (r'\begin{table}\label{tab:r}\begin{tabular}{ll}' +
                            r'\makecell{' + first + r'}&\makecell{' + second + r'}\\\end{tabular}\end{table}')
                before = definition + '\n' + document(table(invocation, token))
                after = definition + '\n' + document(table(token, invocation))
                diff = compute_content_diff(before, after, layout_only=True)
                self.assertNotIn('shadowed_literal_url_invocations', diff['scientific_payload']['changed_components'])
                self.assertIn('tables', diff['scientific_payload']['changed_components'])
                self.assertEqual(diff['action_required'], 'auto_rollback', diff)

    def test_shadowed_url_commands_cannot_hide_new_rendered_content(self):
        token = 'BenchmarkOverflowTokenWithoutBreakpointsAntibodyDesignerLayoutRegressionSequenceColumnWidthStressCase'
        for definition, wrapper in (
            (r'\renewcommand{\nolinkurl}[1]{DifferentScientificIdentifier}', r'\nolinkurl{' + token + '}'),
            (r'\def\nolinkurl#1{DifferentScientificIdentifier}', r'\nolinkurl{' + token + '}'),
            (r'\let\nolinkurl=\DifferentScientificIdentifier', r'\nolinkurl{' + token + '}'),
            (r'\renewcommand{\urlstyle}[1]{DifferentScientificIdentifier}', r'\urlstyle{same}' + token),
        ):
            with self.subTest(definition=definition):
                before = document(definition + '\nIdentifier ' + token + '.')
                after = before.replace('Identifier ' + token, 'Identifier ' + wrapper)
                diff = compute_content_diff(before, after, layout_only=True)
                self.assertEqual(diff['scientific_payload']['status'], 'failed', diff)
                self.assertEqual(diff['layout_prose']['status'], 'failed', diff)
                self.assertEqual(diff['action_required'], 'auto_rollback', diff)

    def test_shadowed_wrapper_in_caption_is_protected_by_complete_source(self):
        token = 'BenchmarkOverflowTokenWithoutBreakpoints1234567890'
        before = (r'\renewcommand{\nolinkurl}[1]{DifferentScientificIdentifier}' + '\n' +
                  document(r'\begin{figure}\caption{Identifier ' + token + r'}\end{figure}'))
        after = before.replace('Identifier ' + token, r'Identifier \nolinkurl{' + token + '}')
        diff = compute_content_diff(before, after, layout_only=True)
        self.assertIn('shadowed_literal_url_invocations', diff['scientific_payload']['changed_components'])
        self.assertEqual(diff['action_required'], 'auto_rollback', diff)

    def test_commented_shadow_definition_does_not_reject_standard_wrapper(self):
        token = 'BenchmarkOverflowTokenWithoutBreakpoints1234567890'
        before = document('% ' + r'\renewcommand{\nolinkurl}[1]{DifferentScientificIdentifier}' + '\nIdentifier ' + token + '.')
        after = before.replace('Identifier ' + token, r'Identifier \nolinkurl{' + token + '}')
        diff = compute_content_diff(before, after, layout_only=True)
        self.assertEqual(diff['scientific_payload']['status'], 'pass', diff)
        self.assertEqual(diff['layout_prose']['status'], 'pass', diff)
        self.assertIn(diff['action_required'], {'none', 'log_only'}, diff)

    def test_current_font_long_identifier_wrapper_is_only_presentation(self):
        token = 'BenchmarkOverflowTokenWithoutBreakpointsAntibodyDesignerLayoutRegressionSequenceColumnWidthStressCase'
        before = document('The identifier is ' + token + '. The method does not improve recall.')
        after = before.replace(token, r'{\urlstyle{same}\nolinkurl{' + token + '}}')
        diff = compute_content_diff(before, after, layout_only=True)
        for channel in ('scientific_payload', 'layout_prose', 'layout_policy'):
            self.assertEqual(diff[channel]['status'], 'pass', diff)
        self.assertEqual(diff['word_count']['change'], 0, diff)
        self.assertIn(diff['action_required'], {'none', 'log_only'}, diff)

    def test_paragraph_boundary_survives_removing_vertical_spacer(self):
        before = document(r'PFVoidTop\par\vspace{120pt}PFVoidBottom\par The method does not improve recall.')
        after = before.replace(r'\vspace{120pt}', ' ')
        diff = compute_content_diff(before, after, layout_only=True)
        self.assertEqual(diff['layout_prose']['status'], 'pass', diff)
        self.assertEqual(diff['scientific_payload']['status'], 'pass', diff)
        self.assertEqual(diff['layout_policy']['status'], 'pass', diff)
        self.assertNotEqual(diff['action_required'], 'auto_rollback', diff)
        unsafe = after.replace('does not improve', 'does improve')
        self.assertEqual(compute_content_diff(before, unsafe, layout_only=True)['action_required'], 'auto_rollback')

    def test_identifier_character_change_remains_rejected(self):
        token = 'BenchmarkOverflowTokenWithoutBreakpoints1234567890'
        before = document('The identifier is ' + token + '.')
        after = before.replace(token, r'{\urlstyle{same}\nolinkurl{' + token.replace('123', '321') + '}}')
        diff = compute_content_diff(before, after, layout_only=True)
        self.assertEqual(diff['layout_prose']['status'], 'failed', diff)
        self.assertEqual(diff['action_required'], 'auto_rollback', diff)

    def test_other_url_forms_and_style_macros_remain_opaque(self):
        for wrapper in (r'\nolinkurl{https://example.org/claim?value=0.47}',
                        r'\nolinkurl{\UnknownScientificClaim}', r'\nolinkurl*{'+ 'A'*40+'}',
                        r'\urlstyle{\UnknownStyle}', r'\nolinkurl{short}'):
            with self.subTest(wrapper=wrapper):
                diff = compute_content_diff(document(wrapper), document(''), layout_only=True)
                self.assertEqual(diff['layout_prose']['status'], 'failed', diff)
                self.assertEqual(diff['action_required'], 'auto_rollback', diff)

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
