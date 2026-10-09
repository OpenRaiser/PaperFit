"""Real PDF observations and native receipts from an explicitly mocked compiler.

No manuscript, TeX compiler, VLM or external API is used by these tests.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compile_inputs
import runtime_actions
from host_context import build_host_context
from page_fit_toolbox import build_toolbox
from pdf_test_support import write_test_pdf, write_test_png
from runtime_artifacts import collect_artifact_manifest


class NativeObservationBindingTests(unittest.TestCase):
    def fixture(self, *, mode='stable', nested=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        folder = root/'manuscript' if nested else root
        folder.mkdir(exist_ok=True)
        main = folder/'paper.tex'
        main.write_text(r'\documentclass{article}\begin{document}\input{body}\input{paper.bbl}\end{document}', encoding='utf-8')
        (folder/'body.tex').write_text('Scientific statement and 42.', encoding='utf-8')
        (folder/'data').mkdir()
        (folder/'data/results.csv').write_bytes(b'method,value\nA,42\n')
        (folder/'data/metadata.json').write_bytes(b'{"count":42}\n')
        write_test_png(folder/'figure.png')
        if mode != 'generated_bbl':
            main.with_suffix('.bbl').write_bytes(b'Author supplied exact bibliography 42.')
        def compiler(command, **kwargs):
            self.assertEqual(Path(kwargs['cwd']), folder)
            write_test_pdf(main.with_suffix('.pdf'))
            if mode == 'generated_bbl':
                main.with_suffix('.bbl').write_bytes(b'New generated bibliography without prehash.')
            if mode != 'missing_fls':
                main.with_suffix('.fls').write_text('PWD '+str(folder)+'\n'+''.join(
                    'INPUT '+name+'\n' for name in ['paper.tex','body.tex','paper.bbl','figure.png','data/results.csv','data/metadata.json']), encoding='utf-8')
            return subprocess.CompletedProcess(command, 0, 'Output written on paper.pdf (1 page).', '')
        with patch.dict(os.environ, {'PAPERFIT_BOUNDED_COMPILE':'0'}), \
                patch.object(runtime_actions, 'run_command', side_effect=compiler), \
                patch.object(runtime_actions, 'pdf_page_count', return_value=1):
            result = runtime_actions.compile_latex(root, main_tex=main, engine='pdflatex')
        return root, main, result

    def snapshot(self, root, main):
        data = root/'data'
        (data/'pages').mkdir(parents=True, exist_ok=True)
        write_test_png(data/'pages/page_001.png')
        for name,payload in [('visual_signal_report.json', {'findings':[]}),
                             ('defect_report.json', {'defects':[]}),
                             ('gatekeeper_decision.json', {'decision':'DONE'})]:
            (data/name).write_text(json.dumps(payload), encoding='utf-8')
        manifest = collect_artifact_manifest(root, str(main), {
            'page_images_dir':'data/pages', 'visual_signal_report':'data/visual_signal_report.json',
            'defect_report':'data/defect_report.json', 'gatekeeper_decision':'data/gatekeeper_decision.json'})
        (data/'run_result_fixture.json').write_text(json.dumps({'artifact_manifest':manifest}), encoding='utf-8')

    def observations(self, root, main, pdf=None):
        before = {path.relative_to(root).as_posix():path.read_bytes() for path in root.rglob('*') if path.is_file()}
        host = build_host_context(main, root=root, pdf=pdf, target_pages=1, page_policy='exact', page_budget_scope='with_appendix')
        toolbox = build_toolbox(main, root=root, pdf=pdf, target_pages=1, defects_stable=True)
        self.assertEqual(before, {path.relative_to(root).as_posix():path.read_bytes() for path in root.rglob('*') if path.is_file()})
        self.assertEqual(host['pdf_layout_profile']['page_count'], 1)
        self.assertTrue(toolbox['observed_pdf_objective']['met'])
        self.assertFalse(toolbox['automatic_acceptance'])
        return host, toolbox

    def assert_unknown(self, host, toolbox):
        self.assertIsNone(host['pdf_visibility']['applicable_to_current_source'])
        self.assertTrue(host['pdf_visibility']['geometry_pass'])
        self.assertFalse(host['pdf_visibility']['pass'])
        self.assertIsNone(host['page_objective']['target_met'])
        self.assertIsNone(toolbox['page_objective']['target_met'])
        self.assertEqual(toolbox['phase'], 'needs_current_build')
        self.assertEqual(host['native_build_binding']['status'], 'unknown')
        self.assertEqual(toolbox['native_build_binding']['status'], 'unknown')

    def test_complete_native_inputs_bind_without_vlm_render_or_runtime_snapshot(self):
        for nested in [False, True]:
            with self.subTest(nested=nested):
                root,main,result = self.fixture(nested=nested)
                self.assertTrue(result['current_source_compile_success'])
                host,toolbox = self.observations(root,main)
                self.assertTrue(host['pdf_visibility']['applicable_to_current_source'])
                self.assertEqual(host['pdf_visibility']['source_pdf_binding'], 'verified_native_inputs')
                self.assertTrue(host['page_objective']['target_met'])
                self.assertTrue(toolbox['page_objective']['target_met'])
                self.assertEqual(toolbox['phase'], 'verify_goal')
                self.assertNotEqual(host['artifact_freshness']['manifest_status'], 'available')

    def test_imported_assets_data_and_bibliography_drift_invalidate_matching_snapshot(self):
        for name in ['figure.png','data/results.csv','data/metadata.json','paper.bbl']:
            with self.subTest(input=name):
                root,main,_ = self.fixture()
                self.snapshot(root,main)
                path = main.parent/name
                path.write_bytes(path.read_bytes()+b'Changed exact input')
                host,toolbox = self.observations(root,main)
                self.assertTrue(host['artifact_freshness']['identity']['source_set_matches_snapshot'])
                self.assertTrue(host['artifact_freshness']['identity']['pdf_matches_snapshot'])
                self.assert_unknown(host,toolbox)

    def test_missing_fls_or_new_generated_bbl_remains_observation_despite_fresh_snapshot(self):
        for mode in ['missing_fls','generated_bbl']:
            with self.subTest(mode=mode):
                root,main,result = self.fixture(mode=mode)
                self.assertTrue(result['compile_process_success'])
                self.assertFalse(result['current_source_compile_success'])
                self.snapshot(root,main)
                self.assert_unknown(*self.observations(root,main))

    def test_missing_invalid_or_latest_failed_journal_never_falls_back(self):
        for mode in ['missing','invalid','latest_failed','latest_unbound','trailing_empty']:
            with self.subTest(mode=mode):
                root,main,_ = self.fixture()
                self.snapshot(root,main)
                journal = root/'data/compile/journal.jsonl'
                if mode == 'missing':journal.unlink()
                elif mode == 'invalid':journal.write_text('[]\n', encoding='utf-8')
                elif mode == 'trailing_empty':
                    with journal.open('a', encoding='utf-8') as handle:handle.write('\n')
                else:
                    latest = json.loads(journal.read_text(encoding='utf-8').splitlines()[-1])
                    if mode == 'latest_failed':latest['success'] = False
                    else:latest['compile_inputs']['bound'] = False
                    with journal.open('a', encoding='utf-8') as handle:handle.write(json.dumps(latest)+'\n')
                self.assert_unknown(*self.observations(root,main))

    def test_changed_recorder_or_identical_pdf_copy_cannot_inherit_native_binding(self):
        for mode in ['fls','pdf_copy']:
            with self.subTest(mode=mode):
                root,main,_ = self.fixture()
                if mode == 'fls':
                    recorder = main.with_suffix('.fls')
                    recorder.write_text(recorder.read_text()+'INPUT paper.tex\n', encoding='utf-8')
                    selected = None
                else:
                    selected = root/'copy.pdf'
                    shutil.copyfile(main.with_suffix('.pdf'),selected)
                self.assert_unknown(*self.observations(root,main,pdf=selected))

    def test_input_change_during_verification_is_caught_by_late_barrier(self):
        root,main,_ = self.fixture()
        original = compile_inputs.live_compile_inputs
        calls = []
        def changing(project, source):
            live = original(project,source)
            calls.append(True)
            if len(calls) == 1:
                (main.parent/'data/results.csv').write_bytes(b'changed during verification')
            return live
        with patch.object(compile_inputs, 'live_compile_inputs', side_effect=changing):
            binding = compile_inputs.verify_native_build(main, root=root)
        self.assertEqual(binding['status'], 'unknown')
        self.assertIn('changed during verification', binding['reason'])


if __name__ == '__main__':unittest.main()
