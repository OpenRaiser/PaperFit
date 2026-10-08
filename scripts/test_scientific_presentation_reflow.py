"""Bounded real-LaTeX reflow must preserve every protected symbol and datum."""
import unittest
from unittest.mock import patch

from content_integrity_check import _unwrap_supported_text_wrappers, compute_content_diff
from scientific_integrity import _math_tokens, _strip_layout, scientific_payload_diff


def document(body):
    return r'\documentclass{article}\usepackage{amsmath}\begin{document}' + body + r'\end{document}'


class ScientificPresentationReflowTest(unittest.TestCase):
    def assert_allowed(self, before, after):
        diff = compute_content_diff(document(before), document(after), layout_only=True)
        for channel in ('scientific_payload', 'layout_policy', 'layout_prose'):
            self.assertEqual(diff[channel]['status'], 'pass', diff)
        self.assertIn(diff['action_required'], {'none', 'log_only'}, diff)

    def assert_rejected(self, before, after):
        diff = compute_content_diff(document(before), document(after), layout_only=True)
        self.assertEqual(diff['scientific_payload']['status'], 'failed', diff)
        self.assertEqual(diff['action_required'], 'auto_rollback', diff)

    def test_cross_line_invisible_delimiters_preserve_visible_parentheses(self):
        before = r'\begin{equation}f(x)=\left(a+b+c+d+e+f\right)\label{eq:f}\end{equation}'
        after = r'\begin{equation}\begin{aligned}f(x)&=\left(a+b+c\right.\\&\left.+d+e+f\right)\label{eq:f}\end{aligned}\end{equation}'
        self.assert_allowed(before, after)

    def test_standard_delimiter_sizes_keep_literal_symbols(self):
        for size in ('big', 'Big', 'bigg', 'Bigg'):
            with self.subTest(size=size):
                self.assert_allowed(r'\begin{equation}x=(a+b)\end{equation}',
                                    r'\begin{equation}x=' + '\\' + size + 'l(a+b' + '\\' + size + r'r)\end{equation}')
        self.assertEqual(_math_tokens(r'\left\{a+b\right\}'), _math_tokens(r'\{a+b\}'))
        self.assertEqual(_math_tokens(r'\Bigl\langle a+b\Bigr\rangle'), _math_tokens(r'\langle a+b\rangle'))

    def test_delimiter_changes_and_loss_remain_rejected(self):
        before = r'\begin{equation}x=(a+b)\end{equation}'
        for after in (r'\begin{equation}x=\Bigl[a+b\Bigr]\end{equation}',
                      r'\begin{equation}x=\left.a+b\right.\end{equation}',
                      r'\begin{equation}x=\Bigl(a-b\Bigr)\end{equation}'):
            with self.subTest(after=after):
                self.assert_rejected(before, after)

    def test_actual_decimal_dot_is_not_an_invisible_delimiter(self):
        self.assertNotEqual(_math_tokens('x=0.47'), _math_tokens('x=047'))
        self.assertNotEqual(_math_tokens(r'x=\Big.47'), _math_tokens('x=47'))
        self.assertNotEqual(_math_tokens(r'x=\middle.47'), _math_tokens('x=47'))

    def test_unsupported_delimiters_and_control_prefixes_stay_opaque(self):
        for expression in (r'\Bigl\customDelimiter a+b', r'\Bigland(a+b)',
                           r'\leftarrow a+b', r'\bigcup A', r'\Bigl{a+b}'):
            with self.subTest(expression=expression):
                self.assertNotEqual(_math_tokens(expression), _math_tokens('a+b'))

    def test_directly_shadowed_size_commands_are_not_standard_layout(self):
        for definition in (r'\renewcommand{\Bigl}{\neg}', r'\def\Bigl{\neg}',
                           r'\let\Bigl=\neg'):
            with self.subTest(definition=definition):
                self.assert_rejected(definition + r'\begin{equation}x=(a+b)\end{equation}',
                                     definition + r'\begin{equation}x=\Bigl(a+b)\end{equation}')

    def test_shadowed_left_and_right_stay_in_inline_table_math(self):
        definition = r'\def\left{\neg}'
        before = definition + r'\begin{table}\begin{tabular}{l}$x=(a+b)$\\\end{tabular}\end{table}'
        after = before.replace('x=(a+b)', r'x=\left(a+b)')
        self.assert_rejected(before, after)

    def test_commented_redefinition_does_not_disable_real_standard_delimiter(self):
        before = '% ' + r'\def\Bigl{\neg}' + '\n' + r'\begin{equation}x=(a+b)\end{equation}'
        after = before.replace('x=(a+b)', r'x=\Bigl(a+b)')
        self.assert_allowed(before, after)

    def test_delimiter_in_unknown_math_environment_is_not_normalized(self):
        before = r'\begin{custommath}x=(a+b)\end{custommath}'
        after = before.replace('x=(a+b)', r'x=\Bigl(a+b)')
        self.assert_rejected(before, after)

    def test_standard_three_option_parbox_keeps_body_number_and_caption(self):
        before = r'\begin{table}\parbox{.8\linewidth}{Header and data: 91.2.}\caption{Results}\label{tab:a}\end{table}'
        for header in (r'[t][2cm][t]{.8\linewidth}', r'[b][][s]{.8\columnwidth}',
                       r'[c][12pt][b]{4cm}'):
            with self.subTest(header=header):
                after = before.replace(r'{.8\linewidth}', header)
                self.assert_allowed(before, after)

    def test_parbox_reflow_does_not_hide_numeric_or_sign_changes(self):
        before = r'\begin{table}\parbox{.8\linewidth}{Header and data: -91.2.}\caption{Results}\label{tab:a}\end{table}'
        reflow = before.replace(r'{.8\linewidth}', r'[t][2cm][t]{.8\linewidth}')
        for after in (reflow.replace('-91.2', '-19.2'), reflow.replace('-91.2', '91.2')):
            with self.subTest(after=after):
                self.assert_rejected(before, after)

    def test_parbox_cell_reflow_retains_scientific_cells(self):
        before = r'\begin{table}\begin{tabular}{ll}A&\parbox{4cm}{Value 91.2}\\\end{tabular}\end{table}'
        after = before.replace(r'\parbox{4cm}', r'\parbox[t][2cm][t]{4cm}')
        self.assert_allowed(before, after)
        self.assert_rejected(before, after.replace('91.2', '12.9'))

    def test_unknown_dynamic_or_malformed_parbox_options_do_not_disappear(self):
        for header in (r'[\customAlign]{4cm}', r'[t][\claim{42}][t]{4cm}',
                       r'[t][2cm][\customInner]{4cm}', r'{\customWidth}',
                       r'{\dimexpr4cm+2pt\relax}', r'[t{4cm}'):
            with self.subTest(header=header):
                before = r'\begin{table}\parbox' + header + r'{Value 91.2}\end{table}'
                after = r'\begin{table}Value 91.2\end{table}'
                diff = scientific_payload_diff(document(before), document(after))
                wrapper = r'\parbox' + header + r'{Value 91.2}'
                self.assertEqual(_strip_layout(wrapper, text_wrappers=True), wrapper)
                self.assertEqual(_unwrap_supported_text_wrappers(wrapper), wrapper)
                # Numeric unknown parameters retain their original payload.
                if '42' in header or '2cm' in header or '4cm' in header:
                    self.assertEqual(diff['status'], 'failed', diff)

    def test_malformed_parbox_body_is_not_normalized(self):
        for wrapper in (r'\parbox{4cm}  Missing body', r'\parbox[t][2cm][t]{4cm}{Unclosed body'):
            with self.subTest(wrapper=wrapper):
                self.assertEqual(_strip_layout(wrapper, text_wrappers=True), wrapper)
                self.assertEqual(_unwrap_supported_text_wrappers(wrapper), wrapper)

    def test_wrapper_reflow_keeps_plain_words_and_rejects_negation_loss(self):
        before = r'\begin{table}\parbox{4cm}{The method does not improve recall.}\end{table}'
        after = before.replace(r'\parbox{4cm}', r'\parbox[t][2cm][t]{4cm}')
        self.assert_allowed(before, after)
        self.assertEqual(compute_content_diff(document(before), document(after.replace('does not', 'does')),
                                              layout_only=True)['action_required'], 'auto_rollback')

    def test_directly_shadowed_parbox_retains_literal_parameters(self):
        definition = r'\renewcommand{\parbox}[2]{#1 #2}'
        before = definition + r'\begin{table}\parbox{4cm}{Value 91.2}\end{table}'
        after = before.replace('{4cm}', '{2cm}')
        self.assert_rejected(before, after)
        wrapper = r'\parbox[t][2cm][t]{4cm}{Value 91.2}'
        self.assertEqual(_unwrap_supported_text_wrappers(wrapper, opaque_commands=frozenset({'parbox'})), wrapper)

    def test_same_count_plain_cell_claim_change_is_rejected(self):
        before = r'\begin{table}\begin{tabular}{l}\parbox{4cm}{The method increases recall.}\\\end{tabular}\end{table}'
        after = before.replace(r'\parbox{4cm}', r'\parbox[t][2cm][t]{4cm}').replace('increases', 'decreases')
        self.assert_rejected(before, after)

    def test_literal_value_and_sign_drift_are_confirmed_token_changes(self):
        for before, after in ((r'\begin{equation}x=a+b\end{equation}', r'\begin{equation}x=a-b\end{equation}'),
                              ('Accuracy is 91.2.', 'Accuracy is 19.2.')):
            with self.subTest(before=before):
                result = scientific_payload_diff(document(before), document(after))
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['assessment']['classification'], 'confirmed_payload_change')
                self.assertFalse(result['assessment']['semantic_damage_proven'])
                self.assertTrue(result['assessment']['confirmed_changed_components'])

    def test_unknown_macro_or_math_environment_rewrite_requires_review(self):
        for before, after in ((r'\def\custom{a+b}', r'\def\custom{b+a}'),
                              (r'\begin{custommath}a+b\end{custommath}', r'\begin{custommath}b+a\end{custommath}')):
            with self.subTest(before=before):
                result = scientific_payload_diff(document(before), document(after))
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['assessment']['classification'], 'manual_review')
                self.assertTrue(result['assessment']['requires_host_review'])

    def test_checker_exception_is_unavailable_and_content_gate_fails_closed(self):
        with patch('scientific_integrity._payload', side_effect=ValueError('fixture checker fault')):
            result = compute_content_diff(document('The result remains.'), document('The result remains.'), layout_only=True)
        self.assertFalse(result['scientific_payload']['checked'])
        self.assertEqual(result['scientific_payload']['status'], 'unavailable')
        self.assertEqual(result['scientific_payload']['assessment']['classification'], 'checker_error')
        self.assertEqual(result['action_required'], 'auto_rollback')

    def test_supported_preservation_assessment_does_not_claim_visible_quality(self):
        result = scientific_payload_diff(document(r'\begin{equation}x=(a+b)\end{equation}'),
                                         document(r'\begin{equation}x=\Bigl(a+b\Bigr)\end{equation}'))
        self.assertEqual(result['assessment']['classification'], 'supported_preservation')
        self.assertFalse(result['assessment']['semantic_damage_proven'])


if __name__ == '__main__':
    unittest.main()
