"""Standard column balancing is layout; unknown readers and claims stay protected."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from content_integrity_check import compute_content_diff


def document(body, preamble=''):
    return '\\documentclass{article}\n' + preamble + '\n\\begin{document}\n' + body + '\n\\end{document}\n'


BODY = r'The method does not reduce accuracy. Result is 91.2. $x=a+b$'


class BalanceLayoutTests(unittest.TestCase):
    def preserved(self, before, after):
        result = compute_content_diff(before, after, layout_only=True)
        self.assertEqual(result['scientific_payload']['status'], 'pass', result)
        self.assertEqual(result['layout_prose']['status'], 'pass', result)
        self.assertNotEqual(result['action_required'], 'auto_rollback', result)

    def rejected(self, before, after):
        result = compute_content_diff(before, after, layout_only=True)
        self.assertEqual(result['action_required'], 'auto_rollback', result)
        return result

    def test_native_package_and_zero_argument_bibliography_flow(self):
        before = document(BODY + '\n' + r'\bibliography{refs}')
        for control in ('balance', 'nobalance'):
            with self.subTest(control=control):
                after = document(BODY + '\n\\' + control + '\n' + r'\bibliography{refs}', r'\usepackage{balance}')
                self.preserved(before, after)

    def test_following_group_numbers_and_negated_claim_are_not_arguments(self):
        for tail in (r'91.2. ', r'{91.2}. ', r'-91.2. ', r'+91.2. ',
                     r' The method does not reduce accuracy. ',
                     r'{The method does not reduce accuracy.} '):
            for control in ('balance', 'nobalance'):
                with self.subTest(control=control, tail=tail):
                    before = document(tail + BODY)
                    after = document('\\' + control + tail + BODY)
                    self.preserved(before, after)
                    # Mutate ONLY the immediately following value/claim, so
                    # an unchanged later occurrence cannot rescue a missed bug.
                    changed = (after.replace('91.2', '19.2', 1) if '91.2' in tail
                               else after.replace('does not reduce', 'does reduce', 1))
                    self.rejected(before, changed)

    def test_direct_and_nested_shadowed_controls_stay_opaque(self):
        for control in ('balance', 'nobalance'):
            for declaration in (r'\def\%s{91.2}', r'\let\%s\claim',
                                r'\renewcommand{\%s}{Claim}',
                                r'\newenvironment{%s}{91.2}{}',
                                r'\def\outer{\def\%s{91.2}}',
                                r'\newcommand{\outer}{\renewcommand{\%s}{Claim}}',
                                r'\def\outer{\newenvironment{%s}{91.2}{}}'):
                with self.subTest(control=control, declaration=declaration):
                    preamble = declaration % control
                    before = document('\\' + control + '\n' + BODY, preamble)
                    after = document(BODY, preamble)
                    result = self.rejected(before, after)
                    self.assertEqual(result['layout_prose']['status'], 'failed', result)

    def test_unknown_reader_cannot_lose_adjacent_balance_argument(self):
        for control in ('balance', 'nobalance'):
            for reader in (r'\unknown', r'\the', r'\unknown[opt]{arg}'):
                with self.subTest(control=control, reader=reader):
                    before = document(reader + '\\' + control + ' ' + BODY)
                    after = document(reader + ' ' + BODY)
                    self.rejected(before, after)

    def test_starred_and_name_prefix_controls_are_not_whitelisted(self):
        for control in (r'\balance*', r'\nobalance*', r'\balanceextra', r'\nobalanceextra'):
            with self.subTest(control=control):
                self.rejected(document(control + ' ' + BODY), document(BODY))

    def test_balance_cannot_hide_math_operators_table_data_or_negation(self):
        before = document(BODY + r'\begin{tabular}{lr}A&91.2\\B&89.3\\\end{tabular}')
        for mutation in (('a+b', 'a-b'), ('A&91.2', 'A&19.2'),
                         ('does not reduce', 'does reduce')):
            with self.subTest(mutation=mutation):
                after = document(r'\balance ' + BODY + r'\begin{tabular}{lr}A&91.2\\B&89.3\\\end{tabular}')
                self.rejected(before, after.replace(*mutation))

    def test_unknown_constructed_names_keep_balance_opaque(self):
        preamble = r'\csname custom\endcsname'
        self.rejected(document(r'\balance ' + BODY, preamble), document(BODY, preamble))


if __name__ == '__main__':
    unittest.main()
