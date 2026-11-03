from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fitz
from compile_inputs import verify_native_build
from gatekeeper_enforcer import make_decision
from orchestrator_runtime import OrchestratorRuntime
from page_objective import page_objective_from_manifest
from runtime_actions import compile_latex_bounded
from runtime_artifacts import collect_artifact_manifest
from runtime_status import build_runtime_status


@unittest.skipUnless(shutil.which('pdflatex') and shutil.which('pdfinfo'), 'native input integration requires pdflatex and pdfinfo')
class RuntimeNativeInputBindingTest(unittest.TestCase):
    def fixture(self, root):
        main = root / 'main.tex'
        main.write_text(r'\documentclass{article}\usepackage{graphicx}\usepackage{layoutfixture}'
                        r'\begin{document}Baseline one page.\includegraphics[width=1cm]{figure.pdf}\ExtraContent\end{document}', encoding='utf-8')
        (root / 'layoutfixture.sty').write_text(r'\ProvidesPackage{layoutfixture}\newcommand{\ExtraContent}{}', encoding='utf-8')
        self.write_figure(root / 'figure.pdf', 'Original figure')
        report = compile_latex_bounded(root, main_tex=main, timeout_sec=60)
        self.assertTrue(report['success'], report)
        self.assertTrue(report['current_source_compile_success'], report)
        self.assertEqual(verify_native_build(main, root=root)['status'], 'current')
        self.render_reports(root)
        runtime = OrchestratorRuntime(state_path=str(root / 'data/state.json'))
        runtime.init_task('main.tex', task_type='visual_only', target_pages=1, page_policy='exact')
        runtime.manager.update({'compile_success': True, 'page_images_rendered': True,
                                'status': 'DONE', 'last_gatekeeper_decision': 'DONE',
                                'artifacts': self.artifacts(), 'visual_signals_summary': {'updated_at': 'fixture'}})
        state = runtime.manager.load()
        manifest = collect_artifact_manifest(root, 'main.tex', self.artifacts())
        self.assertEqual(manifest['freshness']['status'], 'pass')
        self.assertTrue(page_objective_from_manifest(state['task'], manifest)['target_met'])
        (root / 'data/run_result.json').write_text(json.dumps({'status': 'done', 'gatekeeper_decision': 'DONE',
            'task': state['task'], 'artifact_manifest': manifest}), encoding='utf-8')
        return runtime, state, report

    def write_figure(self, path, text):
        with fitz.open() as document:
            document.new_page().insert_text((72, 72), text)
            document.save(path)

    def artifacts(self):
        return {'page_images_dir': 'data/pages', 'visual_signal_report': 'data/visual_signal_report.json',
                'defect_report': 'data/defect_report.json', 'gatekeeper_decision': 'data/gatekeeper_decision.json'}

    def render_reports(self, root):
        pages = root / 'data/pages'
        pages.mkdir(parents=True, exist_ok=True)
        with fitz.open(root / 'main.pdf') as document:
            for n, page in enumerate(document):
                page.get_pixmap().save(pages / ('page_%03d.png' % (n + 1)))
        (root / 'data/visual_signal_report.json').write_text('{"summary":{"pages_analyzed":1},"findings":[]}', encoding='utf-8')
        (root / 'data/defect_report.json').write_text('{"defects":[]}', encoding='utf-8')
        (root / 'data/gatekeeper_decision.json').write_text('{"decision":"DONE"}', encoding='utf-8')

    def assert_pending(self, root, runtime, state):
        source_before = (root / 'main.tex').read_bytes()
        state_before = (root / 'data/state.json').read_bytes()
        manifest = collect_artifact_manifest(root, 'main.tex', self.artifacts())
        self.assertIn('native_build_inputs_current', manifest['freshness']['blocking_checks'])
        self.assertIsNone(page_objective_from_manifest(state['task'], manifest)['target_met'])
        decision = make_decision(state, {'defects': []}, False, None, project_root=root)
        self.assertEqual(decision['decision'], 'CONTINUE')
        self.assertFalse(decision['checks']['native_build_inputs']['pass'])
        self.assertEqual(build_runtime_status(project_root=root)['status'], 'EVALUATING')
        self.assertIsNotNone(runtime._terminal_visual_evidence_failure(decision='DONE', artifact_manifest=manifest))
        self.assertEqual((root / 'main.tex').read_bytes(), source_before)
        self.assertEqual((root / 'data/state.json').read_bytes(), state_before)

    def test_imported_style_and_asset_edits_invalidate_current_pdf_even_with_old_mtimes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime, state, report = self.fixture(root)
            manifest = collect_artifact_manifest(root, 'main.tex', self.artifacts())
            for policy, target, expected in (('exact', 1, True), ('exact', 2, False), ('at_most', 2, True)):
                task = {**state['task'], 'target_pages': target, 'page_policy': policy}
                self.assertIs(page_objective_from_manifest(task, manifest)['target_met'], expected)
            for scope in ('main_body', 'with_refs'):
                task = {**state['task'], 'page_budget_scope': scope}
                self.assertIsNone(page_objective_from_manifest(task, manifest)['target_met'])
            self.assertIn('layoutfixture.sty', report['compile_inputs']['after'])
            self.assertIn('figure.pdf', report['compile_inputs']['after'])
            style = root / 'layoutfixture.sty'
            original = style.read_bytes()
            timestamp = style.stat().st_mtime
            style.write_text(r'\ProvidesPackage{layoutfixture}\newcommand{\ExtraContent}{\newpage A second page.}', encoding='utf-8')
            os.utime(style, (timestamp, timestamp))
            self.assert_pending(root, runtime, state)
            style.write_bytes(original)
            os.utime(style, (timestamp, timestamp))
            self.assertEqual(verify_native_build(root / 'main.tex', root=root)['status'], 'current')
            figure = root / 'figure.pdf'
            timestamp = figure.stat().st_mtime
            self.write_figure(figure, 'Author replaced the imported figure')
            os.utime(figure, (timestamp, timestamp))
            self.assert_pending(root, runtime, state)

    def test_latest_failed_attempt_cannot_inherit_binding_after_source_rollback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime, state, _ = self.fixture(root)
            main = root / 'main.tex'
            original, timestamp = main.read_bytes(), main.stat().st_mtime
            baseline = (root / 'main.pdf').read_bytes()
            main.write_text(r'\documentclass{article}\UndefinedBeforeDocument\begin{document}Failure.\end{document}', encoding='utf-8')
            failure = compile_latex_bounded(root, main_tex=main, timeout_sec=60)
            self.assertFalse(failure['success'])
            self.assertTrue(failure['restored_baseline_pdf'])
            self.assertFalse(failure['current_source_compile_success'])
            self.assertEqual((root / 'main.pdf').read_bytes(), baseline)
            main.write_bytes(original)
            os.utime(main, (timestamp, timestamp))
            self.assert_pending(root, runtime, state)


if __name__ == '__main__':
    unittest.main()
