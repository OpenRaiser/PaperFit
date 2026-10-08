"""Scientific preservation at actual write and executor rollback boundaries."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from content_integrity_check import compute_content_diff, has_scientific_regression
from float_fixers import _passes_hard_content_gate, fix_float_defects
from overflow_fixers import _passes_structure_write_gate as overflow_gate
from space_util_fixers import _passes_structure_write_gate as space_gate
from repair_plan_executor import execute_repair_plan
from test_overflow_fixers import LONG_EQUATION
from test_float_fixers_b4_gate import SPLIT_TABLE_TEX


def manuscript(body: str) -> str:
    return '\n'.join([r'\documentclass{article}', r'\begin{document}',
                      'The measurements are reported below.', body,
                      'The same interpretation applies to all results.', r'\end{document}']) + '\n'


class ScientificPayloadTest(unittest.TestCase):
    def assert_rejected(self, before: str, after: str) -> None:
        diff = compute_content_diff(before, after)
        self.assertTrue(has_scientific_regression(diff), diff)
        self.assertEqual(diff['action_required'], 'auto_rollback')
        for gate in (_passes_hard_content_gate, overflow_gate, space_gate):
            self.assertFalse(gate(before, after)[0], gate.__name__)

    def test_math_operators_grouping_and_text_boundaries(self) -> None:
        changes = [
            (r'x+1+z', r'x+2+z'), (r'x+1+z', r'x+1'), (r'x+z', r'x-z'),
            (r'x_{ab}', r'x_a b'), (r'x^{a+b}', r'x^a+b'),
            (r'\frac{a+b}{c}', r'\frac{a}{b+c}'),
            (r'\sum_{i=1}^{n}', r'\sum_{i=n}^{1}'),
            (r'\text{a b}', r'\text{ab}'), (r'\mathrm{a b}', r'\mathrm{ab}'),
            (r'\operatorname{arg max}', r'\operatorname{argmax}'),
            (r'\begin{pmatrix}a&b\\c&d\end{pmatrix}', r'\begin{pmatrix}a&b&c&d\end{pmatrix}'),
            (r'\begin{cases}a&x>0\\b&x<0\end{cases}', r'\begin{cases}a&x>0&b&x<0\end{cases}'),
        ]
        for before, after in changes:
            with self.subTest(before=before):
                self.assert_rejected(manuscript('$'+before+'$'), manuscript('$'+after+'$'))

    def test_ordered_cells_and_rendered_column_insertions(self) -> None:
        table = r'\begin{table}\caption{Accuracy results}\label{tab:results}\begin{tabular}{lrr}Method&Accuracy&Runtime\\A&\textbf{91.2}&88.1\\B&88.1&91.2\\\end{tabular}\end{table}'
        changes = [
            table.replace(r'\textbf{91.2}', r'\textbf{12.0}'),
            table.replace(r'A&\textbf{91.2}&88.1', r'A&\textbf{88.1}&91.2'),
            table.replace(r'A&\textbf{91.2}&88.1\\B&88.1&91.2', r'A&\textbf{88.1}&91.2\\B&91.2&88.1'),
            table.replace('{lrr}', '{l@{42}rr}'),
            table.replace('Method&Accuracy', r'\multicolumn{1}{c@{ERROR}}{Method}&Accuracy'),
        ]
        for changed in changes:
            with self.subTest(changed=changed):
                self.assert_rejected(manuscript(table), manuscript(changed))

    def test_formula_label_binding_and_all_identifiers(self) -> None:
        equations = r'\begin{equation}a=1\label{eq:a}\end{equation}\begin{equation}b=2\label{eq:b}\end{equation}'
        # Same labels and same formula multiset, but equations are now mislabeled.
        self.assert_rejected(manuscript(equations), manuscript(equations.replace('a=1','b=2',1).replace('b=2\\label{eq:b}','a=1\\label{eq:b}')))
        refs = r'Equation \eqref{eq:a}, \ref{tab:a}, \nameref{sec:a}; \citep{paperA,paperB}.'
        for token in ('eq:a', 'tab:a', 'sec:a', 'paperA'):
            with self.subTest(identifier=token):
                self.assert_rejected(manuscript(refs), manuscript(refs.replace(token, token+'changed')))

    def test_legal_math_table_layout_and_comments(self) -> None:
        before = manuscript(r'\begin{equation}x_a+y_b=1\label{eq:a}\end{equation}')
        after = manuscript(r'\begin{equation}\begin{split}x_{a}+y_{b}&=1\label{eq:a}\end{split}\end{equation}')
        self.assertFalse(has_scientific_regression(compute_content_diff(before, after)))
        table = r'\begin{table}\caption{Accuracy 91.2}\label{tab:a}\begin{tabular}{lrr}Method&A&B\\ours&\textbf{91.2}&88.1\\\end{tabular}\end{table}'
        styled = table.replace(r'\begin{tabular}{lrr}', r'\small\setlength{\tabcolsep}{2pt}\begin{tabular}{p{0.4\linewidth}rr}').replace(r'\textbf{91.2}', '91.2')
        self.assertFalse(has_scientific_regression(compute_content_diff(manuscript(table), manuscript(styled))))
        unchanged = before + '% $changed=999$ \\label{fake}\n'
        self.assertFalse(has_scientific_regression(compute_content_diff(before, unchanged)))

    def test_macro_definition_drift_cannot_hide_behind_unchanged_formula(self) -> None:
        definitions = [
            (r'\newcommand{\combine}{+}', r'\newcommand{\combine}{-}'),
            (r'\renewcommand{\combine}[1]{#1+1}', r'\renewcommand{\combine}[1]{#1-1}'),
            (r'\def\combine#1{#1+1}', r'\def\combine#1{#1-1}'),
            (r'\DeclareMathOperator{\combine}{arg max}', r'\DeclareMathOperator{\combine}{arg min}'),
            (r'\let\combine\plus', r'\let\combine\minus'),
        ]
        for before, after in definitions:
            with self.subTest(before=before):
                self.assert_rejected(manuscript(before+r'$x\combine y$'), manuscript(after+r'$x\combine y$'))
        before = manuscript(r'\renewcommand{\arraystretch}{1.0}$x+1$')
        after = manuscript(r'\renewcommand{\arraystretch}{1.3}$x+1$')
        self.assertFalse(has_scientific_regression(compute_content_diff(before, after)))

    def test_known_caption_style_and_float_counters_are_layout(self) -> None:
        before = manuscript('$x+1$')
        after = before.replace(r'\begin{document}', r'\captionsetup{font=small,labelfont=bf,skip=6pt}\setcounter{topnumber}{3}\begin{document}')
        self.assertFalse(has_scientific_regression(compute_content_diff(before, after)))
        unsafe = before.replace(r'\begin{document}', r'\captionsetup{name=ScientificClaim42}\begin{document}')
        self.assert_rejected(before, unsafe)
        self.assert_rejected(before, before.replace(r'\begin{document}', r'\setcounter{equation}{42}\begin{document}'))

    def test_unknown_math_rewrite_is_not_assumed_equivalent(self) -> None:
        self.assert_rejected(manuscript('$x+x$'), manuscript('$2x$'))

    def test_longtable_repeated_head_mutation_is_rejected(self) -> None:
        before = manuscript(r'\begin{longtable}{lr}Method&Value\\\endfirsthead Method&Value\\\endhead\endfoot\endlastfoot A&1\\\end{longtable}')
        after = before.replace(r'\endfirsthead Method&Value', r'\endfirsthead Method&DIFFERENT')
        self.assert_rejected(before, after)


class ScientificExecutorRollbackTest(unittest.TestCase):
    def run_candidate(self, before: str, after: str) -> tuple:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tex = root/'main.tex'
            # CRLF must be restored byte for byte, not silently rewritten as LF.
            original = before.replace('\n', '\r\n').encode('utf-8')
            tex.write_bytes(original)
            plan = root/'plan.json'
            plan.write_text(json.dumps({'candidates': [{
                'candidate_type': 'log_warning', 'defect_family': 'D2',
                'line_number': 4, 'overflow_amount': 12.0,
                'target': {'scope': 'overflow'},
            }]}), encoding='utf-8')
            def buggy_fix(**kwargs):
                Path(kwargs['main_tex']).write_bytes(after.encode('utf-8'))
                return {'status': 'success', 'changes': [{'success': True, 'defect_id': 'D2'}], 'unresolved': []}
            # Simulate a buggy repair strategy; the real public executor and all
            # integrity guards remain unmocked.
            with mock.patch('repair_plan_executor.execute_overflow_candidates', side_effect=buggy_fix):
                report = execute_repair_plan(str(plan), str(tex), str(root/'report.json'))
            return report, tex.read_bytes(), original

    def test_four_previously_silent_mutations_rollback_exactly(self) -> None:
        equation = r'\begin{equation}E=x+1+z\label{eq:result}\end{equation}'
        table = r'\begin{tabular}{lr}Method&Score\\ours&\textbf{91.2}\\\end{tabular}'
        fixtures = [
            (equation, equation.replace('1', '2')),
            (equation, equation.replace('+z', '')),
            (table, table.replace('91.2', '12.0')),
            (equation, equation.replace(r'\label{eq:result}', '')),
            (r'$\text{a b}$', r'$\text{ab}$'),
            (r'\newcommand{\combine}{+}$x\combine y$', r'\newcommand{\combine}{-}$x\combine y$'),
            (r'\def\combine#1{#1+1}$x\combine y$', r'\def\combine#1{#1-1}$x\combine y$'),
        ]
        for before, after in fixtures:
            with self.subTest(before=before, after=after):
                report, final, original = self.run_candidate(manuscript(before), manuscript(after))
                self.assertEqual(report['status'], 'failed', report)
                self.assertTrue(report['content_integrity']['rollback_performed'])
                self.assertEqual(report['applied_count'], 0)
                self.assertEqual(final, original)

    def test_layout_only_candidate_passes_real_executor_boundary(self) -> None:
        before = manuscript(r'\begin{equation}x+z=1\label{eq:a}\end{equation}')
        after = manuscript(r'\begin{equation}\begin{aligned}x+z&={}\\1\label{eq:a}\end{aligned}\end{equation}')
        report, final, original = self.run_candidate(before, after)
        self.assertEqual(report['status'], 'success', report)
        self.assertFalse(report['content_integrity']['rollback_performed'])
        self.assertNotEqual(final, original)

    def test_actual_d2_fix_lands_and_preserves_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tex = root/'main.tex'
            tex.write_text(manuscript(r'\begin{equation}'+LONG_EQUATION+r'\end{equation}'), encoding='utf-8')
            plan = root/'plan.json'
            plan.write_text(json.dumps({'candidates': [{
                'candidate_type': 'log_warning', 'defect_family': 'D2', 'line_number': 4,
                'overflow_amount': 12.0, 'target': {'scope': 'overflow'},
            }]}), encoding='utf-8')
            report = execute_repair_plan(str(plan), str(tex), str(root/'report.json'))
            self.assertEqual(report['status'], 'success', report)
            self.assertIn(r'\begin{aligned}', tex.read_text(encoding='utf-8'))
            self.assertEqual(report['content_integrity']['diff']['scientific_payload']['status'], 'pass')

    def test_actual_b1_b2_and_b4_layout_fixes_preserve_science(self) -> None:
        figure = manuscript(r'\begin{figure}[p]\includegraphics[width=1.25\linewidth]{asset.pdf}\caption{Measured score $a+1$}\label{fig:a}\end{figure}')
        fixtures = [
            (figure, {'defect_id': 'B1', 'object': 'fig:a', 'ref_page': 1, 'page': 5}),
            (figure, {'defect_id': 'B2', 'object': 'fig:a', 'page': 2, 'visual_width_subtype': 'overflow_width'}),
            (SPLIT_TABLE_TEX, {'defect_id': 'B4', 'object': 'tab:split', 'page': 2}),
        ]
        for source, candidate in fixtures:
            with self.subTest(family=candidate['defect_id']), tempfile.TemporaryDirectory() as directory:
                tex = Path(directory)/'main.tex'
                tex.write_text(source, encoding='utf-8')
                report = fix_float_defects(str(tex), [candidate], template_type='double_column')
                self.assertEqual(report.status, 'success', report.to_dict())
                self.assertNotEqual(source, tex.read_text(encoding='utf-8'))
                self.assertFalse(has_scientific_regression(compute_content_diff(source, tex.read_text(encoding='utf-8'))))

    def test_cli_returns_nonzero_for_scientific_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before, after = root/'before.tex', root/'after.tex'
            before.write_text(manuscript('$x+1$'), encoding='utf-8')
            after.write_text(manuscript('$x+2$'), encoding='utf-8')
            result = subprocess.run([sys.executable, str(SCRIPTS_DIR/'content_integrity_check.py'),
                                     'diff', str(before), str(after), '--output', str(root/'diff.json')],
                                    capture_output=True, text=True,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertEqual(json.loads((root/'diff.json').read_text())['scientific_payload']['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
