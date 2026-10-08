"""Known length syntax and local scope compatibility without payload weakening."""
import unittest

from content_integrity_check import compute_content_diff
from layout_prose_integrity import compare_layout_prose
from scientific_integrity import _strip_layout, scientific_payload_diff


def document(body):
    return r'\documentclass{article}\begin{document}' + body + r'\end{document}'


class LayoutScalarScopeTests(unittest.TestCase):
    def assert_allowed(self, before, after):
        diff=compute_content_diff(document(before),document(after),layout_only=True)
        for channel in ('scientific_payload','layout_prose','layout_policy'):
            self.assertEqual(diff[channel]['status'],'pass',(channel,diff))
        self.assertIn(diff['action_required'],('none','log_only'))

    def assert_rejected(self,before,after):
        diff=compute_content_diff(document(before),document(after),layout_only=True)
        self.assertEqual(diff['action_required'],'auto_rollback',diff)
        return diff

    def test_braced_and_unbraced_known_first_arguments(self):
        for command in ('setlength','addtolength'):
            for register in ('intextsep','tabcolsep','abovecaptionskip','textheight'):
                with self.subTest(command=command,register=register):
                    before='\\'+command+'\\'+register+'{-10pt} The result remains -91.2.'
                    after='\\'+command+'{\\'+register+'}{10pt} The result remains -91.2.'
                    self.assert_allowed(before,after)

    def test_literal_register_multiple_and_glue_remain_layout(self):
        for length in (r'-8\baselineskip',r'.5\linewidth','10pt plus 2pt minus 1pt'):
            self.assert_allowed(r'\setlength\intextsep{-10pt} A result.',
                                r'\setlength{\intextsep}{'+length+'} A result.')

    def test_comment_and_whitespace_between_arguments(self):
        before='\\setlength % standard TeX comment\n \\intextsep {-10pt}\nA result.'
        self.assert_allowed(before,r'\setlength{\intextsep}{10pt} A result.')

    def test_unknown_register_or_dynamic_value_stays_opaque(self):
        forms=(r'\setlength\claim{-91.2pt}',r'\setlength{\claim}{-91.2pt}',
               r'\setlength\intextsep{\claim{91.2}}',
               r'\setlength{\intextsep}{\dimexpr 91.2pt+2pt\relax}',
               r'\setlength*\intextsep{91.2pt}',r'\setlengthfoo\intextsep{91.2pt}')
        for form in forms:
            with self.subTest(form=form):
                self.assertEqual(_strip_layout(form),form)
                self.assert_rejected(form+' A result.',form.replace('91.2','19.2')+' A result.')

    def test_malformed_length_cannot_consume_body_number(self):
        for form in (r'\setlength\intextsep -91.2pt',r'\setlength{\intextsep other}{-91.2pt}',
                     r'\setlength\intextsep{-91.2pt'):
            with self.subTest(form=form):
                self.assertEqual(_strip_layout(form),form)
                self.assert_rejected(form+' Result.',form.replace('91.2','19.2')+' Result.')

    def test_length_normalization_does_not_hide_body_number_sign_or_formula(self):
        before=r'\setlength\intextsep{-10pt} Accuracy is -91.2. $x=a+b$'
        reflow=before.replace('{-10pt}','{10pt}')
        for after in (reflow.replace('-91.2','91.2'),reflow.replace('91.2','19.2'),reflow.replace('a+b','a-b')):
            with self.subTest(after=after):
                diff=self.assert_rejected(before,after)
                self.assertEqual(diff['scientific_payload']['assessment']['classification'],'confirmed_payload_change')

    def test_direct_command_and_register_redefinitions_stay_opaque(self):
        for definition in (r'\def\setlength#1#2{#2}',r'\renewcommand{\addtolength}[2]{#2}',
                           r'\let\setlength=\claim',r'\def\intextsep{claim}'):
            for command in ('setlength','addtolength'):
                if command not in definition and 'intextsep' not in definition:continue
                with self.subTest(definition=definition,command=command):
                    before=definition+'\\'+command+'\\intextsep{-91.2pt} A result.'
                    self.assert_rejected(before,before.replace('-91.2pt','91.2pt'))
                    self.assertEqual(compare_layout_prose(document(before),document(before.replace('-91.2pt','91.2pt')))['status'],'failed')

    def test_balanced_standard_local_group_keeps_claim(self):
        before='The method does not improve recall. Another stable claim.'
        after=r'\begingroup\looseness=-1 The method does not improve recall.\par\endgroup Another stable claim.'
        self.assert_allowed(before,after)
        nested=r'\begingroup The method \begingroup does not\endgroup improve recall.\endgroup Another stable claim.'
        self.assert_allowed(before,nested)
        self.assert_allowed(before,r'\begingroup\bfseries '+before+r'\par\endgroup')

    def test_group_does_not_hide_prose_or_payload_changes(self):
        before='The method does not improve recall. Accuracy is -91.2.'
        after=r'\begingroup\looseness=-1 The method does not improve recall. Accuracy is -91.2.\par\endgroup'
        self.assert_rejected(before,after.replace('does not','does'))
        self.assert_rejected(before,after.replace('-91.2','91.2'))

    def test_group_redefinition_prefix_escape_and_unbalanced_scope_remain_opaque(self):
        for prefix in (r'\def\begingroup{not}',r'\renewcommand{\endgroup}{not}',r'\let\begingroup=\claim'):
            before=prefix+'A result.'
            after=prefix+r'\begingroup A result.\endgroup'
            self.assertEqual(compare_layout_prose(document(before),document(after))['status'],'failed')
        for scoped in (r'\begingroup A result.',r'A result.\endgroup',r'\begingroup A result.\endgroupstar',
                       r'\\begingroup A result.\\endgroup'):
            self.assertEqual(compare_layout_prose(document('A result.'),document(scoped))['status'],'failed')

    def test_scoping_definitions_or_stateful_unknowns_cannot_pass_by_masking(self):
        pairs=((r'\def\claim{not}A \claim result.',r'\begingroup\def\claim{not}\endgroup A \claim result.'),
               (r'\let\claim=\other A \claim result.',r'\begingroup\let\claim=\other\endgroup A \claim result.'),
               (r'\global\def\claim{not} A result.',r'\begingroup\global\def\claim{not}\endgroup A result.'),
               (r'\aftergroup\claim A result.',r'\begingroup\aftergroup\claim\endgroup A result.'),
               (r'\customState{not} A result.',r'\begingroup\customState{not}\endgroup A result.'))
        for before,after in pairs:
            with self.subTest(before=before):
                # Some inventories are unchanged; scope still prevents prose
                # normalization and the live layout-only diff fails closed.
                self.assertEqual(compare_layout_prose(document(before),document(after))['status'],'failed')
                self.assert_rejected(before,after)


if __name__=='__main__':unittest.main()
