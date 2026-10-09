from __future__ import annotations

import errno
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import URLError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import runtime_actions
import compile_recovery
import vlm_diagnosis


class CompileObservationSafetyTest(unittest.TestCase):
    def run_compile(self, root, *, returncode=1, log='! Undefined control sequence.\nl.3 \\IEEEmembership\n', create_pdf=True, baseline=False, tolerant=True, record_inputs=False):
        main = root / 'main.tex'
        source = '\\documentclass{article}\n\\begin{document}\n\\IEEEmembership{Member, IEEE}\n\\end{document}\n'
        main.write_text(source, encoding='utf-8')
        if baseline:
            (root / 'main.pdf').write_bytes(b'%PDF-old')
        def run(cmd, *, cwd, timeout=None):
            if create_pdf:
                (root / 'main.pdf').write_bytes(b'%PDF-new')
            if record_inputs:
                (root / 'main.fls').write_text('PWD ' + str(root) + '\nINPUT main.tex\n', encoding='utf-8')
            return subprocess.CompletedProcess(cmd, returncode, log, '')
        with mock.patch.dict(os.environ, {'PAPERFIT_BOUNDED_COMPILE': '0'}), \
                mock.patch.object(runtime_actions, 'run_command', side_effect=run) as runner, \
                mock.patch.object(runtime_actions, 'pdf_page_count', return_value=1), \
                mock.patch.object(compile_recovery, 'apply_deterministic_recovery', side_effect=AssertionError('compile observation must not repair')):
            result = runtime_actions.compile_latex(root, main_tex=main, tolerant=tolerant)
        self.assertEqual(main.read_text(encoding='utf-8'), source)
        self.assertEqual(runner.call_count, 1)
        return result

    def test_nonstop_error_pdf_is_only_a_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self.run_compile(Path(tmp))
            self.assertFalse(report['success'])
            self.assertEqual(report['failure_class'], 'undefined_control_sequence')
            self.assertEqual(report['case_status'], 'source_compile_error')
            self.assertIsNone(report['pdf_path'])
            self.assertTrue(report['pdf_fresh'])
            self.assertTrue(report['preview_pdf_path'])

    def test_fatal_error_cannot_mutate_manuscript(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self.run_compile(Path(tmp), log='! Undefined control sequence.\nl.3 \\IEEEmembership\n! LaTeX Error: stopped\n')
            self.assertFalse(report['success'])

    def test_restored_old_pdf_is_not_fresh_or_successful(self):
        for returncode in (1, 124, 0):
            with self.subTest(returncode=returncode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                report = self.run_compile(root, returncode=returncode, log='', create_pdf=False, baseline=True)
                self.assertFalse(report['success'])
                self.assertFalse(report['pdf_fresh'])
                self.assertFalse(report['partial_pdf_available'])
                self.assertTrue(report['restored_baseline_pdf'])
                self.assertIsNone(report['pdf_path'])
                self.assertEqual((root / 'main.pdf').read_bytes(), b'%PDF-old')

    def test_fresh_timeout_preview_is_available_but_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self.run_compile(Path(tmp), returncode=124, log='TIMEOUT')
            self.assertFalse(report['success'])
            self.assertTrue(report['partial_pdf_available'])
            self.assertEqual(report['failure_class'], 'timed_out')

    def test_completed_fresh_pdf_is_successful(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self.run_compile(Path(tmp), returncode=0, log='Output written on main.pdf (1 page).', record_inputs=True)
            self.assertTrue(report['success'])
            self.assertTrue(report['pdf_fresh'])
            self.assertIsNone(report['preview_pdf_path'])

    def test_completed_pdf_without_recorded_inputs_remains_unknown_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self.run_compile(Path(tmp), returncode=0, log='Output written on main.pdf (1 page).')
            self.assertTrue(report['success'])
            self.assertTrue(report['pdf_fresh'])
            self.assertTrue(report['preview_pdf_path'])
            self.assertFalse(report['compile_inputs']['bound'])


class TemplateRecoverySafetyTest(unittest.TestCase):
    def test_incremental_log_proven_shims_preserve_author_arguments(self):
        source = '\\documentclass{article}\n% \\begin{document}\n\\begin{document}\n\\IEEEmembership{Senior Member, IEEE}\n\\IEEEPARstart{A}{study}\n\\end{document}\n'
        first, actions = compile_recovery.fix_template_macro_shims(source, '! Undefined control sequence.\nl.4 \\IEEEmembership\n')
        self.assertEqual(len(actions), 1)
        second, actions = compile_recovery.fix_template_macro_shims(first, '! Undefined control sequence.\nl.5 \\IEEEPARstart\n')
        self.assertEqual(len(actions), 1)
        self.assertIn('\\providecommand{\\IEEEPARstart}[2]{#1#2}', second)
        self.assertIn('\\IEEEmembership{Senior Member, IEEE}', second)
        third, actions = compile_recovery.fix_template_macro_shims(second, '! Undefined control sequence.\nl.5 \\IEEEPARstart\n')
        self.assertEqual(third, second)
        self.assertEqual(actions, [])
        self.assertLess(second.index('\\providecommand'), second.index('\\begin{document}\n\\IEEEmembership'))
        self.assertIn('% \\begin{document}\n', second)

    def test_unrelated_context_macro_and_metadata_are_not_guessed(self):
        source = '\\documentclass{article}\n\\begin{document}\\IEEEmembership{Member} \\ACMConf{Conference 2026}\\end{document}'
        for log in ('! Undefined control sequence.\nl.3 \\IEEEmembership{Member} \\unknown\n', '! Undefined control sequence.\nl.3 \\ACMConf\n'):
            after, actions = compile_recovery.fix_template_macro_shims(source, log)
            self.assertEqual(after, source)
            self.assertEqual(actions, [])

    def test_failed_recovery_preserves_exact_bytes_without_external_snapshot(self):
        for snapshot_create in (None, lambda: (_ for _ in ()).throw(RuntimeError('snapshot unavailable'))):
            with self.subTest(snapshot=snapshot_create), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                original = b'\\documentclass{article}\r\n\\begin{document}\\ref{fig:a\\_b}\\end{document}\r\n'
                (root / 'main.tex').write_bytes(original)
                log = root / 'error.log'
                log.write_text('! Unknown error\n', encoding='utf-8')
                def compile_fn(_root, _main):
                    return {'success': False, 'error_signature': 'same', 'log_path': 'error.log'}
                result = compile_recovery.recover_compile(root, 'main.tex', compile_fn=compile_fn, snapshot_create=snapshot_create)
                self.assertTrue(result.rolled_back)
                self.assertEqual((root / 'main.tex').read_bytes(), original)

    def test_compile_exception_after_edit_restores_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = b'\\documentclass{article}\n\\begin{document}\\ref{fig:a\\_b}\\end{document}\n'
            (root / 'main.tex').write_bytes(original)
            (root / 'error.log').write_text('! Unknown error\n', encoding='utf-8')
            compile_fn = mock.Mock(side_effect=[{'success': False, 'error_signature': 'one', 'log_path': 'error.log'}, RuntimeError('compile failed')])
            result = compile_recovery.recover_compile(root, 'main.tex', compile_fn=compile_fn)
            self.assertTrue(result.rolled_back)
            self.assertEqual(result.stopped_reason, 'compile_exception')
            self.assertEqual((root / 'main.tex').read_bytes(), original)


class VLMTransportRoutingSafetyTest(unittest.TestCase):
    def request(self, error, *, opt_in=False):
        settings = {'DEEPSEEK_API_KEY': 'offline-test-key'}
        if opt_in:
            settings['PAPERFIT_VLM_DIRECT_FALLBACK'] = '1'
        with mock.patch.dict(os.environ, settings, clear=True), \
                mock.patch.object(vlm_diagnosis, 'urlopen', side_effect=error), \
                mock.patch.object(vlm_diagnosis, 'build_opener') as builder:
            with self.assertRaisesRegex(RuntimeError, 'network connection failed'):
                vlm_diagnosis._default_client().analyze(images=[], system_prompt='Inspect', user_prompt='Inspect')
        builder.assert_not_called()

    def test_default_does_not_bypass_host_network_route(self):
        self.request(URLError(ConnectionRefusedError(errno.ECONNREFUSED, 'refused')))

    def test_opt_in_never_retries_ambiguous_timeout_or_tls_failure(self):
        import ssl
        for reason in (TimeoutError('timeout'), ssl.SSLError('certificate failed')):
            with self.subTest(reason=type(reason).__name__):
                self.request(URLError(reason), opt_in=True)

    def test_opt_in_connection_refusal_rebuilds_the_direct_request(self):
        result = {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"defects": []}'}}]}
        with mock.patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'offline-test-key', 'PAPERFIT_VLM_DIRECT_FALLBACK': '1'}, clear=True), \
                mock.patch.object(vlm_diagnosis, 'urlopen', side_effect=URLError(ConnectionRefusedError(errno.ECONNREFUSED, 'refused'))) as routed, \
                mock.patch.object(vlm_diagnosis, 'build_opener') as builder:
            builder.return_value.open.return_value = io.BytesIO(json.dumps(result).encode())
            text, _metadata = vlm_diagnosis._default_client().analyze(images=[], system_prompt='Inspect', user_prompt='Inspect')
        self.assertEqual(text, '{"defects": []}')
        request = builder.return_value.open.call_args.args[0]
        self.assertIsNot(request, routed.call_args.args[0])
        self.assertEqual(request.full_url, 'https://api.deepseek.com/chat/completions')


if __name__ == '__main__':
    unittest.main()
