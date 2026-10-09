"""Tampered local inputs/PDFs must not reuse an isolated compile receipt.

These unit fixtures model receipt verification, not actual compiler execution.
The separate retained Windows smoke supplies real compilation evidence.
"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import pymupdf as fitz
sys.path.insert(0, str(Path(__file__).resolve().parent))
from isolated_build import SCHEMA, compile_isolated, inventory, sha, verify_isolated_build
import isolated_build
from page_fit_toolbox import build_toolbox


class IsolatedBuildTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='paperfit-isolated-')
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.root = self.parent / 'project'; self.root.mkdir()
        self.main = self.root / 'paper.tex'
        self.main.write_text(r'\documentclass{article}\begin{document}Fixed 91.2.\end{document}', encoding='utf-8')
        (self.root / 'body.tex').write_text('Fixed included words.', encoding='utf-8')
        (self.root / 'venue.sty').write_text(r'\def\bodyfontsize{10}', encoding='utf-8')
        (self.root / 'refs.bib').write_text('@article{a, title={Fixed work}}', encoding='utf-8')
        (self.root / 'figure.png').write_bytes(b'fixed asset bytes')
        (self.root / 'data').mkdir()
        (self.root / 'data/results.csv').write_text('method,score\nA,91.2\n', encoding='utf-8')
        self.build = self.parent / 'compiled'; self.build.mkdir()
        shutil.copytree(self.root, self.build / 'source')
        (self.build / 'build').mkdir()
        self.pdf = self.build / 'build/paper.pdf'
        with fitz.open() as document:
            document.new_page().insert_text((70, 70), 'Fixed 91.2.', fontsize=10)
            document.save(self.pdf)
        self.log = self.build / 'build/paper.log'; self.log.write_text('Output written.', encoding='utf-8')
        for name in ('stdout', 'stderr'):
            (self.build / (name + '.txt')).write_text('', encoding='utf-8')
        self.record = {
            'schema': SCHEMA, 'project_root': str(self.root.resolve()), 'main_relpath': 'paper.tex',
            'cwd': str(self.build / 'source'), 'compile_success': True, 'returncode': 0,
            'timed_out': False, 'launch_error': None, 'source_unchanged_by_tools': True,
            'source_inventory': inventory(self.root),
            'pdf': {'path': 'build/paper.pdf', 'sha256': sha(self.pdf)},
            'log': {'path': 'build/paper.log', 'sha256': sha(self.log)},
            'stdout_sha256': sha(self.build / 'stdout.txt'), 'stderr_sha256': sha(self.build / 'stderr.txt'),
        }
        self.save()

    def save(self):
        (self.build / 'build_record.json').write_text(json.dumps(self.record), encoding='utf-8')

    def verify(self, pdf=None):
        return verify_isolated_build(self.main, root=self.root, pdf=pdf or self.pdf, build_dir=self.build)

    def test_explicit_matching_receipt_can_measure_goal_but_not_accept(self):
        result = self.verify()
        self.assertEqual(result['status'], 'current')
        self.assertFalse(result['automatic_acceptance'])
        self.assertFalse(result['host_actual_page_views_established'])
        measured = build_toolbox(self.main, root=self.root, pdf=self.pdf, build_dir=self.build,
                                 target_pages=1, defects_stable=True)
        self.assertEqual(measured['phase'], 'verify_goal')
        self.assertEqual(measured['pdf_identity']['source_pdf_binding'], 'verified_isolated_build')
        self.assertIs(measured['page_objective']['target_met'], True)
        self.assertFalse(measured['automatic_acceptance'])
        self.assertEqual(measured['log_hints']['path'], str(self.log.resolve()))
        selected = build_toolbox(self.main, root=self.root, build_dir=self.build,
                                 target_pages=1)
        self.assertEqual(selected['pdf_identity']['path'], str(self.pdf.resolve()))
        self.assertEqual(selected['pdf_identity']['source_pdf_binding'], 'verified_isolated_build')
        self.assertEqual(selected['phase'], 'requires_host_defect_review')

    def test_includes_styles_bib_and_assets_are_bound_even_at_same_page_count(self):
        for name in ('paper.tex', 'body.tex', 'venue.sty', 'refs.bib', 'figure.png', 'data/results.csv'):
            path = self.root / name; original = path.read_bytes()
            with self.subTest(name=name):
                path.write_bytes(original + b'changed')
                self.assertEqual(self.verify()['reason'], 'source_inventory_changed')
                result = build_toolbox(self.main, root=self.root, pdf=self.pdf, build_dir=self.build,
                                      target_pages=1, defects_stable=True)
                self.assertIsNone(result['page_objective']['target_met'])
                self.assertEqual(result['phase'], 'needs_current_build')
            path.write_bytes(original)

    def test_snapshot_change_added_input_and_different_pdf_are_not_current(self):
        copied = self.build / 'source/body.tex'; original = copied.read_bytes()
        copied.write_bytes(b'a different compiled snapshot')
        self.assertEqual(self.verify()['reason'], 'source_inventory_changed')
        copied.write_bytes(original)
        added = self.root / 'new-asset.dat'; added.write_bytes(b'new')
        self.assertEqual(self.verify()['reason'], 'source_inventory_changed')
        added.unlink()
        other = self.parent / 'old.pdf'; shutil.copyfile(self.pdf, other)
        self.assertEqual(self.verify(other)['reason'], 'selected_pdf_not_this_build')
        with self.pdf.open('ab') as stream:
            stream.write(b'changed PDF bytes')
        self.assertEqual(self.verify()['reason'], 'pdf_changed')

    def test_failed_or_boolean_returncode_cannot_claim_success(self):
        for key, value in [('returncode', 1), ('returncode', False), ('timed_out', True),
                           ('source_unchanged_by_tools', False), ('compile_success', False)]:
            previous = self.record[key]
            with self.subTest(key=key, value=value):
                self.record[key] = value; self.save()
                self.assertEqual(self.verify()['reason'], 'compile_not_successful')
            self.record[key] = previous
        self.save()

    def test_log_change_and_malicious_receipt_path_are_not_followed(self):
        original = self.log.read_bytes(); self.log.write_bytes(b'! Fatal error')
        self.assertEqual(self.verify()['reason'], 'compile_log_changed_or_missing')
        self.log.write_bytes(original)
        self.record['pdf']['path'] = str(self.parent / 'outside.pdf'); self.save()
        self.assertEqual(self.verify()['reason'], 'pdf_changed')
        self.assertFalse((self.parent / 'outside.pdf').exists())

    def test_no_receipt_or_explicit_bad_receipt_cannot_fall_back_to_pdf_count(self):
        (self.build / 'build_record.json').write_text('{}', encoding='utf-8')
        result = build_toolbox(self.main, root=self.root, pdf=self.pdf, build_dir=self.build,
                              target_pages=1, defects_stable=True)
        self.assertIs(result['observed_pdf_objective']['met'], True)
        self.assertIsNone(result['page_objective']['target_met'])
        self.assertEqual(result['phase'], 'needs_current_build')

    def test_compile_destination_cannot_overwrite_project_or_existing_evidence(self):
        for destination in (self.root / 'new-build', self.build, self.parent):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                compile_isolated(self.main, root=self.root, build_dir=destination)
        self.assertFalse((self.root / 'new-build').exists())

    def test_timed_out_compiler_retains_failure_without_reusing_a_pdf(self):
        destination = self.parent / 'timed-out-build'
        child = mock.Mock(returncode=-9)
        child.wait.side_effect = subprocess.TimeoutExpired(['latexmk'], 1)
        with mock.patch.object(isolated_build.shutil, 'which', return_value='latexmk'), \
             mock.patch.object(isolated_build.subprocess, 'Popen', return_value=child), \
             mock.patch.object(isolated_build, '_terminate_tree') as terminate:
            record = compile_isolated(self.main, root=self.root, build_dir=destination, timeout=1)
        terminate.assert_called_once_with(child)
        self.assertIs(record['timed_out'], True)
        self.assertIs(record['compile_success'], False)
        self.assertIsNone(record['pdf']['sha256'])
        self.assertEqual(record['returncode'], -9)
        self.assertEqual(inventory(self.root), record['source_inventory'])
        self.assertTrue((destination / 'build_record.json').is_file())

    def test_unconfirmed_timeout_termination_still_retains_failed_attempt(self):
        destination = self.parent / 'termination-unknown-build'
        child = mock.Mock(returncode=None)
        child.wait.side_effect = subprocess.TimeoutExpired(['latexmk'], 1)
        with mock.patch.object(isolated_build.shutil, 'which', return_value='latexmk'), \
             mock.patch.object(isolated_build.subprocess, 'Popen', return_value=child), \
             mock.patch.object(isolated_build, '_terminate_tree',
                               side_effect=subprocess.TimeoutExpired(['latexmk'], 10)):
            record = compile_isolated(self.main, root=self.root, build_dir=destination, timeout=1)
        self.assertIs(record['compile_success'], False)
        self.assertIs(record['timed_out'], True)
        self.assertEqual(record['termination_error'], 'TimeoutExpired')
        self.assertIsNone(record['returncode'])
        self.assertTrue((destination / 'build_record.json').is_file())

    def test_bound_cli_report_cannot_invalidate_its_own_input_set(self):
        command = [sys.executable, '-X', 'utf8', '-B',
                   str(Path(isolated_build.__file__).with_name('page_fit_toolbox.py')),
                   '--main', str(self.main), '--root', str(self.root),
                   '--build-dir', str(self.build), '--target-pages', '1']
        before = inventory(self.root)
        for output in (self.root / 'data/report.json', self.build / 'source/report.json'):
            with self.subTest(output=output):
                result = subprocess.run(command + ['--output', str(output)],
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertFalse(output.exists())
                self.assertEqual(inventory(self.root), before)
        output = self.root / '.paperfit/reports/page-fit.json'
        result = subprocess.run(command + ['--output', str(output)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(output.read_text(encoding='utf-8'))['pdf_identity']['source_pdf_binding'],
                         'verified_isolated_build')
        self.assertEqual(inventory(self.root), before)
        self.assertEqual(self.verify()['status'], 'current')

    def test_missing_selected_build_artifacts_are_still_protected_report_paths(self):
        command = [sys.executable, '-X', 'utf8', '-B',
                   str(Path(isolated_build.__file__).with_name('page_fit_toolbox.py')),
                   '--main', str(self.main), '--root', str(self.root),
                   '--build-dir', str(self.build), '--target-pages', '1']
        for output in (self.pdf, self.log, self.build / 'build_record.json'):
            with self.subTest(output=output):
                original = output.read_bytes()
                output.unlink()
                result = subprocess.run(command + ['--output', str(output)],
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertFalse(output.exists())
                output.write_bytes(original)

    def test_late_artifact_change_during_inventory_barrier_is_not_current(self):
        original = inventory
        reads = 0
        def changing_inventory(path):
            nonlocal reads
            reads += 1
            result = original(path)
            if reads == 4:
                with self.pdf.open('ab') as stream:
                    stream.write(b'changed during final inventory')
            return result
        with mock.patch.object(isolated_build, 'inventory', side_effect=changing_inventory):
            result = self.verify()
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['reason'], 'build_artifact_changed_during_verification')

    def test_nested_main_cwd_is_bound_without_reading_arbitrary_receipt_paths(self):
        subdir = self.root / 'sub'; subdir.mkdir()
        nested = subdir / 'paper.tex'; nested.write_bytes(self.main.read_bytes())
        shutil.copytree(subdir, self.build / 'source/sub')
        self.record.update(main_relpath='sub/paper.tex', working_directory='main_parent',
                           cwd=str(self.build / 'source/sub'), source_inventory=inventory(self.root))
        self.save()
        result = verify_isolated_build(nested, root=self.root, pdf=self.pdf, build_dir=self.build)
        self.assertEqual(result['status'], 'current')
        self.record['cwd'] = str(self.build / 'source'); self.save()
        self.assertEqual(verify_isolated_build(nested, root=self.root, pdf=self.pdf, build_dir=self.build)['status'], 'unknown')
        self.record['working_directory'] = '../outside'; self.save()
        self.assertEqual(verify_isolated_build(nested, root=self.root, pdf=self.pdf, build_dir=self.build)['reason'], 'build_record_working_directory_invalid')

    def test_log_identity_validated_against_receipt_is_the_one_rechecked(self):
        real_sha = sha
        log_reads = 0
        def changing_sha(path):
            nonlocal log_reads
            result = real_sha(path)
            if path == self.log:
                log_reads += 1
                if log_reads == 1:
                    self.log.write_text('! changed after first validated hash', encoding='utf-8')
            return result
        with mock.patch.object(isolated_build, 'sha', side_effect=changing_sha):
            result = self.verify()
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['reason'], 'build_artifact_changed_during_verification')

    def test_explicit_old_clean_log_cannot_hide_current_build_reference_errors(self):
        self.log.write_text('LaTeX Warning: Reference eq:missing undefined.', encoding='utf-8')
        self.record['log']['sha256'] = sha(self.log)
        self.save()
        old = self.parent / 'old-clean.log'
        old.write_text('Output written with no errors.', encoding='utf-8')
        result = build_toolbox(self.main, root=self.root, build_dir=self.build,
                               target_pages=1, defects_stable=True, log=old)
        self.assertEqual(result['phase'], 'repair_defects')
        self.assertEqual(result['log_hints']['path'], str(self.log.resolve()))
        self.assertEqual(len(result['log_hints']['blockers']), 1)
        self.assertEqual(result['additional_log_hints']['path'], str(old.resolve()))
        self.assertEqual(result['additional_log_hints']['blockers'], [])

    def test_log_changed_after_binding_cannot_remain_current_during_diagnostics(self):
        real_hints = __import__('page_fit_toolbox')._log_hints
        def changed_hints(path):
            self.log.write_text('! Changed after the binding barrier', encoding='utf-8')
            return real_hints(path)
        with mock.patch('page_fit_toolbox._log_hints', side_effect=changed_hints):
            result = build_toolbox(self.main, root=self.root, build_dir=self.build,
                                   target_pages=1, defects_stable=True)
        self.assertIsNone(result['page_objective']['target_met'])
        self.assertEqual(result['pdf_identity']['source_pdf_binding'], 'not_verified')
        self.assertEqual(result['isolated_build_binding']['status'], 'unknown')


if __name__ == '__main__':
    unittest.main()
