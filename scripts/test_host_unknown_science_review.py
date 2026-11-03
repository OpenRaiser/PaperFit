"""Unproved macro edits remain candidates; confirmed content losses still roll back."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from content_integrity_check import compute_content_diff
from layout_policy import snapshot_source_edit_checks
from repair_plan_executor import _enforce_structure_integrity
from runtime_snapshots import create_pre_repair_snapshot
from scientific_integrity import scientific_host_review_report, scientific_payload_diff

BASE = '\n'.join([
    r'\documentclass{article}',
    r'\newcommand{\myspread}{\hspace{2pt}}',
    r'\begin{document}',
    r'A \myspread B. The retained result is 17.',
    r'\begin{equation}x+y=17\label{eq:one}\end{equation}',
    r'\begin{table}\caption{Measured results.}\label{tab:one}',
    r'\begin{tabular}{lr}Method&Score\\A&42\\\end{tabular}\end{table}',
    r'\end{document}',
]) + '\n'
PENDING = BASE.replace(r'\hspace{2pt}', r'\hspace{3pt}')


class UnknownScienceReviewTest(unittest.TestCase):
    def test_uncertain_macro_diff_keeps_raw_failure_and_pending_action(self):
        raw = scientific_payload_diff(BASE, PENDING)
        self.assertEqual(raw['assessment']['classification'], 'manual_review')
        self.assertEqual(raw['assessment']['confirmed_changed_components'], [])
        self.assertEqual(raw['status'], 'failed')
        result = compute_content_diff(BASE, PENDING, layout_only=True)
        self.assertEqual(result['action_required'], 'manual_review')
        self.assertEqual(result['scientific_payload']['status'], 'manual_review')
        self.assertEqual(result['scientific_payload']['automated_report'], raw)
        self.assertEqual(result['layout_prose']['status'], 'pass')
        self.assertFalse(result['source_uncertainty']['eligible'])
        self.assertEqual(raw['status'], 'failed')

    def test_executed_candidate_is_retained_without_success_or_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            main = root / 'main.tex'
            main.write_text(PENDING, encoding='utf-8')
            report = _enforce_structure_integrity(
                main_tex='main.tex', original_tex=BASE,
                original_bytes=BASE.encode('utf-8'), project_root=root,
                report={'status': 'success', 'applied_count': 1})
            self.assertEqual(main.read_text(encoding='utf-8'), PENDING)
            self.assertEqual(report['status'], 'manual_review')
            self.assertFalse(report['success'])
            self.assertFalse(report['content_integrity']['rollback_performed'])
            self.assertTrue(report['review_required'])

    def test_snapshot_uses_same_pending_disposition(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            main = root / 'main.tex'
            main.write_text(BASE, encoding='utf-8')
            snapshot = create_pre_repair_snapshot(
                project_root=root, main_tex='main.tex', snapshot_id='unknown-macro')
            main.write_text(PENDING, encoding='utf-8')
            result = snapshot_source_edit_checks(root, snapshot['rollback_target'])
            self.assertEqual(result['status'], 'manual_review')
            science = result['scientific_payload']
            self.assertEqual(science['status'], 'manual_review')
            self.assertEqual(science['file_reports'][0]['automated_report']['status'], 'failed')

    def test_confirmed_changes_still_restore_source_including_other_unknown_edit(self):
        changes = [
            ('table value', PENDING.replace('A&42', 'A&43')),
            ('math sign', PENDING.replace('x+y=17', 'x-y=17')),
            ('label', PENDING.replace(r'\label{eq:one}', r'\label{eq:other}')),
            ('ordinary prose', PENDING.replace('The retained result is 17.', '')),
            ('body number', PENDING.replace('result is 17.', 'result is 18.')),
            ('table caption', PENDING.replace('Measured results.', 'Changed results.')),
            ('deleted math', PENDING.replace('x+y=17', 'x=17')),
        ]
        for name, candidate in changes:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                main = root / 'main.tex'
                main.write_text(candidate, encoding='utf-8')
                report = _enforce_structure_integrity(
                    main_tex='main.tex', original_tex=BASE,
                    original_bytes=BASE.encode('utf-8'), project_root=root,
                    report={'status': 'success', 'applied_count': 1})
                self.assertEqual(report['status'], 'failed')
                self.assertEqual(main.read_text(encoding='utf-8'), BASE)
                self.assertEqual(report['applied_count'], 0)

    def test_missing_or_unchecked_assessment_never_qualifies(self):
        raw = scientific_payload_diff(BASE, PENDING)
        for mutate in [
            lambda r: r.pop('assessment'),
            lambda r: r.update(checked=False),
            lambda r: r.pop('presentation_macro_uncertainty'),
            lambda r: r['assessment'].pop('confirmed_changed_components'),
            lambda r: r['assessment'].update(confirmed_changed_components=['tables']),
            lambda r: r['assessment'].update(classification='confirmed_payload_change'),
        ]:
            report = copy.deepcopy(raw)
            mutate(report)
            self.assertEqual(scientific_host_review_report(report)['status'], 'failed')

    def test_macro_payload_math_aliases_and_opaque_math_are_not_relaxed(self):
        changes = [
            (r'\newcommand{\combine}{+}$x\combine y$',
             r'\newcommand{\combine}{-}$x\combine y$'),
            (r'\DeclareMathOperator{\combine}{arg max}$\combine x$',
             r'\DeclareMathOperator{\combine}{arg min}$\combine x$'),
            (r'\let\combine\plus$x\combine y$',
             r'\let\combine\minus$x\combine y$'),
            (r'\newcommand{\result}{Retained scientific words.}\result',
             r'\newcommand{\result}{Deleted words.}\result'),
            (r'\begin{custommath}x+y\end{custommath}',
             r'\begin{custommath}x-y\end{custommath}'),
        ]
        for before, after in changes:
            with self.subTest(before=before):
                result = compute_content_diff(before, after, layout_only=True)
                self.assertEqual(result['action_required'], 'auto_rollback')
                self.assertEqual(result['scientific_payload']['status'], 'failed')

    def test_shadowed_spacing_or_changed_macro_signatures_are_not_presentation(self):
        for before, after in [
            (r'\renewcommand{\hspace}[1]{#1}'+BASE,
             r'\renewcommand{\hspace}[1]{#1}'+PENDING),
            (BASE, PENDING.replace(r'\newcommand{\myspread}', r'\newcommand{\myspread}[1]')),
        ]:
            with self.subTest(before=before):
                raw = scientific_payload_diff(before, after)
                self.assertNotEqual(raw.get('presentation_macro_uncertainty', {}).get('status'), 'pending')
                self.assertEqual(compute_content_diff(before, after, layout_only=True)['action_required'], 'auto_rollback')

    def test_hidden_graphics_and_font_changes_inside_macros_are_not_relaxed(self):
        bodies = [
            (r'\scalebox{1}{\includegraphics{x.pdf}}', r'\scalebox{0}{\includegraphics{x.pdf}}'),
            (r'\scalebox{1}{\includegraphics{x.pdf}}', r'\scalebox{-1}{\includegraphics{x.pdf}}'),
            (r'\includegraphics[width=1pt]{x.pdf}', r'\includegraphics[width=0pt]{x.pdf}'),
            (r'\includegraphics{x.pdf}', r'\includegraphics[draft]{x.pdf}'),
            (r'\resizebox{1pt}{!}{\includegraphics{x.pdf}}', r'\resizebox{0pt}{!}{\includegraphics{x.pdf}}'),
            (r'\fontsize{10pt}{12pt}\selectfont', r'\fontsize{0pt}{0pt}\selectfont'),
            ('Retained visible words.', r'\phantom{Retained visible words.}'),
            ('Retained visible words.', r'\small Retained visible words.'),
        ]
        for before, after in bodies:
            with self.subTest(after=after):
                wrap = lambda value: r'\documentclass{article}\newcommand{\plot}{' + value + r'}\begin{document}\plot\end{document}'
                result = compute_content_diff(wrap(before), wrap(after), layout_only=True)
                self.assertEqual(result['action_required'], 'auto_rollback')
                self.assertEqual(result['scientific_payload']['status'], 'failed')

    def test_cli_reports_pending_with_nonzero_exit_and_no_implicit_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before, after = root / 'before.tex', root / 'after.tex'
            output = root / 'diff.json'
            before.write_text(BASE, encoding='utf-8')
            after.write_text(PENDING, encoding='utf-8')
            result = subprocess.run([
                sys.executable, '-B', str(Path(__file__).with_name('content_integrity_check.py')),
                'diff', str(before), str(after), '--output', str(output)],
                capture_output=True, text=True,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.assertEqual(result.returncode, 2, result.stderr)
            report = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(report['action_required'], 'manual_review')
            self.assertEqual(report['scientific_payload']['automated_report']['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
