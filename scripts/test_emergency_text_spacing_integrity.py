"""Literal paragraph stretch and text space syntax without payload exemptions."""
import unittest

from layout_prose_integrity import compare_layout_prose
from scientific_integrity import scientific_payload_diff
from layout_policy import compare_layout_policy


def document(body, preamble=''):
    return '\\documentclass{article}\n' + preamble + '\n\\begin{document}\n' + body + '\n\\end{document}\n'


class EmergencyTextSpacingIntegrityTest(unittest.TestCase):
    body = 'The method does not improve recall. Accuracy is -91.2. $x=a+b$'

    def assert_preserved(self, before, after):
        self.assertEqual(scientific_payload_diff(before, after)['status'], 'pass')
        self.assertEqual(compare_layout_prose(before, after)['status'], 'pass')

    def assert_not_preserved(self, before, after):
        science = scientific_payload_diff(before, after)
        prose = compare_layout_prose(before, after)
        self.assertTrue(science['status'] != 'pass' or prose['status'] != 'pass', (science, prose))

    def test_literal_emergency_assignments_and_length_commands_preserve_body(self):
        for setting in (r'\emergencystretch=2em', r'\emergencystretch = .5pt',
                        r'\emergencystretch 0pt', r'\emergencystretch=-1.25ex',
                        r'\emergencystretch=\linewidth', r'\emergencystretch=.1\linewidth',
                        r'\setlength{\emergencystretch}{2em}', r'\setlength\emergencystretch{.5pt}',
                        r'\addtolength{\emergencystretch}{1em}'):
            with self.subTest(setting=setting):
                self.assert_preserved(document(self.body), document('{' + setting + '\n' + self.body + r'\par}'))

    def test_standard_group_can_contain_only_known_stretch_and_text_spaces(self):
        after = r'\begingroup\emergencystretch=2em The method does not\qquad improve recall. Accuracy is -91.2. $x=a+b$\par\endgroup'
        self.assert_preserved(document(self.body), document(after))

    def test_text_quad_and_qquad_are_spaces_and_have_no_arguments(self):
        for command in ('quad', 'qquad'):
            with self.subTest(command=command):
                self.assert_preserved(document('PFVoidTop PFVoidBottom.'), document('PFVoidTop\\' + command + ' PFVoidBottom.'))
                self.assert_preserved(document('A not improved result.'), document('A\\' + command + '{not} improved result.'))
                self.assert_not_preserved(document('AB'), document('A\\' + command + ' B'))
                self.assert_not_preserved(document('A not improved result.'), document('A\\' + command + ' improved result.'))

    def test_normalization_keeps_body_numbers_signs_negation_and_math(self):
        safe = r'{\emergencystretch=2em ' + self.body.replace('not improve', r'not\quad improve') + r'\par}'
        for wrong in (safe.replace('-91.2', '91.2'), safe.replace('91.2', '12.9'),
                      safe.replace('not\\quad ', '\\quad '), safe.replace('a+b', 'a-b')):
            with self.subTest(wrong=wrong):
                self.assert_not_preserved(document(self.body), document(wrong))

    def test_dynamic_unknown_malformed_and_prefix_assignments_remain_opaque(self):
        for setting in (r'\emergencystretch=\claim', r'\emergencystretch=\dimexpr2em+1pt\relax',
                        r'\emergencystretch=2\customlength', r'\emergencystretch=2unknown',
                        r'\emergencystretch=2emword', r'\emergencystretch*{2em}',
                        r'\emergencystretchfoo=2em', r'\setlength{\emergencystretch}{\claim{91.2}}',
                        r'\setlength{\emergencystretch}{\dimexpr2em+1pt\relax}',
                        r'\setlength{\emergencystretch}{2\customlength}',
                        r'\setlength{\emergencystretch}{2em plus 91.2em}',
                        r'\setlength{\emergencystretch}{2em minus 91.2em}',
                        r'\setlength{\emergencystretch}{2em'):
            with self.subTest(setting=setting):
                self.assert_not_preserved(document(self.body), document(setting + ' ' + self.body))

    def test_assignment_stops_before_body_values_and_commands(self):
        before = document('Accuracy is -91.2 and not improved.')
        self.assert_preserved(before, document(r'\emergencystretch=2em Accuracy is -91.2 and not improved.'))
        for tail in ('Accuracy is 91.2 and not improved.', 'Accuracy is -91.2 and improved.',
                     r'\claim{not} Accuracy is -91.2 and not improved.'):
            with self.subTest(tail=tail):
                self.assert_not_preserved(before, document(r'\emergencystretch=2em ' + tail))

    def test_redefined_space_and_register_invocations_remain_opaque(self):
        definitions = (r'\def\quad{not}', r'\renewcommand{\qquad}{not}', r'\let\quad=\claim',
                       r'\def\emergencystretch{not}', r'\let\emergencystretch=\claim',
                       r'\newdimen\emergencystretch', r'\dimendef\emergencystretch=1',
                       r'\skipdef\quad=1', r'\expandafter\def\csname quad\endcsname{not}')
        for definition in definitions:
            with self.subTest(definition=definition):
                invocation = r'\emergencystretch=2em ' if 'emergencystretch' in definition else (r'\qquad ' if 'qquad' in definition else r'\quad ')
                self.assert_not_preserved(document(self.body, definition), document(invocation + self.body, definition))

    def test_inherited_math_space_redefinition_is_not_erased(self):
        self.assert_not_preserved(document('$a+b$', r'\def\quad{+c}'), document(r'$a\quad+b$', r'\def\quad{+c}'))

    def test_nested_literal_redefinitions_shadow_spaces_stretch_and_dimension_references(self):
        cases = ((r'\def\quad{not}', r'\quad '),
                 (r'\gdef\qquad{not}', r'\qquad '),
                 (r'\let\quad=\claim', r'\quad '),
                 (r'\renewcommand{\qquad}{not}', r'\qquad '),
                 (r'\def\emergencystretch{not}', r'\emergencystretch=2em '),
                 (r'\newdimen \emergencystretch', r'\emergencystretch=2em '),
                 (r'\def\linewidth{91.2pt not}', r'\emergencystretch=.1\linewidth '),
                 (r'\let\baselineskip=\claim', r'\setlength{\emergencystretch}{\baselineskip} '),
                 (r'\newdimen \linewidth', r'\emergencystretch=.1\linewidth '))
        for definition, invocation in cases:
            with self.subTest(definition=definition):
                preamble = r'\newcommand{\activate}{' + definition + '}'
                before = document(r'\activate ' + self.body, preamble)
                after = document(r'\activate ' + invocation + self.body, preamble)
                self.assert_not_preserved(before, after)
        preamble = r'\newcommand{\activate}{\def\quad{+c}}'
        self.assert_not_preserved(document(r'\activate $a+b$', preamble),
                                  document(r'\activate $a\quad+b$', preamble))
        preamble = r'\newcommand{\activate}{\def\left{+c}}'
        self.assert_not_preserved(document(r'\activate $(a+b)$', preamble),
                                  document(r'\activate $\left(a+b\right)$', preamble))

    def test_whitespace_and_comment_separated_redefinitions_are_still_shadowed(self):
        for definition in (r'\renewcommand \quad{not}', '\\renewcommand % comment\n \\quad{not}',
                           r'\newdimen \emergencystretch', '\\dimendef\n\\emergencystretch=1',
                           r'\newlength {\emergencystretch}', r'\skipdef \quad=1'):
            with self.subTest(definition=definition):
                invocation = r'\emergencystretch=2em ' if 'emergencystretch' in definition else r'\quad '
                self.assert_not_preserved(document(self.body, definition), document(invocation + self.body, definition))
                science = scientific_payload_diff(document(self.body), document(self.body, definition))
                self.assertEqual(science['status'], 'failed')
                self.assertTrue(science['assessment']['requires_host_review'])

    def test_new_redefinition_requires_review_even_with_unchanged_invocation(self):
        before = document(r'\quad A stable result.')
        after = document(r'\quad A stable result.', r'\def\quad{not}')
        result = scientific_payload_diff(before, after)
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(result['assessment']['requires_host_review'])

    def test_unused_new_register_redefinition_still_requires_review(self):
        for definition in (r'\newdimen\emergencystretch', r'\newlength{\quad}', r'\dimendef\emergencystretch=1'):
            with self.subTest(definition=definition):
                result = scientific_payload_diff(document(self.body), document(self.body, definition))
                self.assertEqual(result['status'], 'failed')
                self.assertIn('macro_definitions', result['changed_components'])
                self.assertTrue(result['assessment']['requires_host_review'])

    def test_shadowed_dimension_references_are_not_known_lengths(self):
        for definition in (r'\def\linewidth{not}', r'\newdimen\linewidth', r'\dimendef\linewidth=1'):
            with self.subTest(definition=definition):
                before = document(self.body, definition)
                self.assert_not_preserved(before, document(r'\emergencystretch=.1\linewidth ' + self.body, definition))

    def test_unknown_constructed_names_and_catcode_effects_stay_conservative(self):
        for definition in (r'\expandafter\def\csname\name\endcsname{not}', r'\catcode`\@=11'):
            with self.subTest(definition=definition):
                before = document(self.body, definition)
                self.assert_not_preserved(before, document(r'\emergencystretch=2em ' + self.body, definition))
                self.assert_not_preserved(before, document(r'\quad ' + self.body, definition))

    def test_presentation_recognition_does_not_autoapprove_new_scaling(self):
        table = r'\begin{tabular}{lr}Method&Score\\A&91.2\\\end{tabular}'
        before = document(self.body + table)
        after = document(r'\emergencystretch=2em ' + self.body + r'\resizebox{.1\linewidth}{!}{' + table + '}')
        self.assertEqual(compare_layout_policy(before, after)['status'], 'manual_review')

    def test_commented_redefinitions_do_not_shadow_standard_controls(self):
        self.assert_preserved(document(self.body, '% ' + r'\def\emergencystretch{not}' + '\n'),
                              document(r'\emergencystretch=2em ' + self.body, '% ' + r'\def\emergencystretch{not}' + '\n'))

    def test_escaped_controls_and_spacing_prefixes_remain_visible(self):
        for invocation in (r'\\quad ', r'\quadfoo ', r'\qquadfoo ', r'\quad* ', r'\\emergencystretch=2em '):
            with self.subTest(invocation=invocation):
                self.assert_not_preserved(document(self.body), document(invocation + self.body))


if __name__ == '__main__':
    unittest.main()
