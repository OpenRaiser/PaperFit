from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runtime_actions
import paperfit_command
from compile_inputs import verify_native_build


class BoundedCompileBaselineTest(unittest.TestCase):
    def pdf_bytes(self, label):
        try:
            import pymupdf as fitz
        except ImportError:
            import fitz
        with fitz.open() as document:
            document.new_page().insert_text((72, 72), label)
            return document.tobytes()

    def implementations(self):
        return ((runtime_actions, 'compile_latex_bounded', 'run_command'),
                (paperfit_command, '_compile_bounded', '_run'))

    def compile_fixture(self, module, function, runner, root, *, returncode=1, produced=None):
        main = root / 'main.tex'
        source = r'\documentclass{article}\begin{document}Fixture.\end{document}'
        main.write_text(source, encoding='utf-8')
        old_pdf = self.pdf_bytes('Pre-attempt baseline')
        (root / 'main.pdf').write_bytes(old_pdf)
        preview = self.pdf_bytes('Current failed preview') if produced == 'readable' else b'invalid PDF'
        original_runner = getattr(module, runner)

        def run(command, *, cwd, timeout=None):
            if command[0] == 'pdfinfo':
                return original_runner(command, cwd=cwd, timeout=timeout)
            if produced is not None:
                (root / 'main.pdf').write_bytes(preview)
            (root / 'main.fls').write_text('PWD ' + str(root) + '\nINPUT main.tex\n', encoding='utf-8')
            return subprocess.CompletedProcess(command, returncode, 'failed' if returncode else '', '')

        def which(name):
            return 'fixture-pdflatex' if name == 'pdflatex' else None

        with mock.patch.object(module.shutil, 'which', side_effect=which), \
                mock.patch.object(module, runner, side_effect=run):
            result = getattr(module, function)(root, main_tex=main, timeout_sec=60)
        self.assertEqual(main.read_text(encoding='utf-8'), source)
        archive = Path(result['baseline_pdf'])
        self.assertEqual(archive.parent.name, result['attempt_id'])
        self.assertEqual(archive.read_bytes(), old_pdf)
        journal = json.loads((root / 'data/compile/journal.jsonl').read_text(encoding='utf-8').splitlines()[-1])
        self.assertEqual(journal['baseline_pdf'], str(archive))
        return result, old_pdf, preview

    def test_failed_or_timed_out_attempt_restores_baseline_without_certifying_it(self):
        for module, function, runner in self.implementations():
            for returncode in (0, 1, 124):
                for produced in (None, 'invalid'):
                    with self.subTest(module=module.__name__, returncode=returncode, produced=produced), tempfile.TemporaryDirectory() as temporary:
                        root = Path(temporary)
                        result, baseline, _ = self.compile_fixture(module, function, runner, root, returncode=returncode, produced=produced)
                        self.assertEqual((root / 'main.pdf').read_bytes(), baseline)
                        self.assertTrue(result['restored_baseline_pdf'])
                        self.assertFalse(result['success'])
                        self.assertFalse(result['pdf_fresh'])
                        self.assertFalse(result['partial_pdf_available'])
                        self.assertFalse(result['current_source_compile_success'])
                        self.assertIsNone(result['pdf_path'])
                        self.assertEqual(result['preview_pdf_path'], str(root / 'main.pdf'))
                        self.assertEqual(verify_native_build(root / 'main.tex', root=root)['status'], 'unknown')

    def test_readable_failed_preview_survives_and_baseline_archive_remains(self):
        for module, function, runner in self.implementations():
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                result, _, preview = self.compile_fixture(module, function, runner, root, produced='readable')
                self.assertEqual((root / 'main.pdf').read_bytes(), preview)
                self.assertFalse(result['restored_baseline_pdf'])
                self.assertFalse(result['success'])
                self.assertFalse(result['current_source_compile_success'])
                self.assertTrue(result['pdf_fresh'])

    def test_successful_current_build_retains_baseline_and_native_binding(self):
        for module, function, runner in self.implementations():
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                result, _, current = self.compile_fixture(module, function, runner, root, returncode=0, produced='readable')
                self.assertEqual((root / 'main.pdf').read_bytes(), current)
                self.assertFalse(result['restored_baseline_pdf'])
                self.assertTrue(result['success'])
                self.assertTrue(result['pdf_fresh'])
                self.assertTrue(result['current_source_compile_success'])
                self.assertEqual(verify_native_build(root / 'main.tex', root=root)['status'], 'current')

    def test_later_failed_attempt_keeps_both_baseline_archives(self):
        for module, function, runner in self.implementations():
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                first, baseline, preview = self.compile_fixture(module, function, runner, root, produced='readable')
                original_runner = getattr(module, runner)
                def no_pdf(command, *, cwd, timeout=None):
                    if command[0] == 'pdfinfo':
                        return original_runner(command, cwd=cwd, timeout=timeout)
                    return subprocess.CompletedProcess(command, 1, 'failed without PDF', '')
                with mock.patch.object(module.shutil, 'which', side_effect=lambda name: 'fixture-pdflatex' if name == 'pdflatex' else None), \
                        mock.patch.object(module, runner, side_effect=no_pdf):
                    second = getattr(module, function)(root, main_tex=root / 'main.tex', timeout_sec=60)
                self.assertNotEqual(first['attempt_id'], second['attempt_id'])
                self.assertEqual(Path(first['baseline_pdf']).read_bytes(), baseline)
                self.assertEqual(Path(second['baseline_pdf']).read_bytes(), preview)
                self.assertEqual((root / 'main.pdf').read_bytes(), preview)
                self.assertTrue(second['restored_baseline_pdf'])
                self.assertFalse(second['current_source_compile_success'])


if __name__ == '__main__':
    unittest.main()
