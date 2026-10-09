"""Real phased/outer runtime control-flow regressions with offline observations.

Compiler, render and diagnosis observations are synthetic. Snapshot discovery,
source diff guards, the phased callback, native runtime loop and checkpoint files
are exercised without model API calls or a manuscript quality claim.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import pymupdf

import orchestrator_runtime
from orchestrator_runtime import OrchestratorRuntime
from pdf_test_support import write_test_pdf, write_test_png
from repair_plan_executor import _enforce_structure_integrity
from runtime_repair_plan import attach_repair_plan_fingerprint
from runtime_types import TaskSpec

BASE = '\\documentclass{article}\n\\begin{document}\nResult $x+y=17$.\\input{part}\n\\end{document}\n'
CHILD = 'Retained source and number 42.\n'


class RuntimePendingSourceReviewTest(unittest.TestCase):
    def run_fixture(self, kind='layout', *, final_pages=2, hard_visual=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / 'data').mkdir()
        main = root / 'main.tex'
        child = root / 'part.tex'
        main.write_text(BASE, encoding='utf-8')
        child.write_text(CHILD, encoding='utf-8')
        os.utime(main, (1700000000, 1700000000))
        os.utime(child, (1700000000, 1700000000))
        pdf = root / 'main.pdf'
        write_test_pdf(pdf)
        counters = {'observe': 0, 'execute': 0, 'core': 0, 'trial_compile': 0}

        def compile_observation(*args, **kwargs):
            counters['observe'] += 1
            count = 1 if counters['observe'] == 1 else final_pages
            write_test_pdf(pdf, page_count=count)
            return {'success': True, 'pdf_path': str(pdf), 'timeout': False, 'log_file': 'main.log'}

        def render_observation(*args, **kwargs):
            pages = root / 'data/pages'
            pages.mkdir(exist_ok=True)
            for old in pages.glob('page_*.png'):
                old.unlink()
            count = 1 if counters['observe'] == 1 else final_pages
            for index in range(1, count + 1):
                write_test_png(pages / f'page_{index:03d}.png')
            return {'success': True, 'page_dir': 'data/pages'}

        def fake_core(runtime, **kwargs):
            counters['core'] += 1
            count = 1 if counters['core'] == 1 else final_pages
            data = root / 'data'
            for filename, payload in [
                ('visual_signal_report.json', {'summary': {'pages_analyzed': count}, 'findings': []}),
                ('defect_report.json', {'defects': []}), ('gatekeeper_decision.json', {'decision': 'DONE'}),
                ('rule_report.json', {'summary': {'overfull_hbox_total': 0}})]:
                (data / filename).write_text(json.dumps(payload), encoding='utf-8')
            candidates = [
                {'candidate_type': 'log_warning', 'defect_family': 'D2', 'line_number': 3,
                 'proposed_action': 'repair_overfull_boxes', 'overflow_amount': 12,
                 'target': {'scope': 'overflow'}},
                {'candidate_type': 'visual_object', 'defect_family': 'B2', 'line_number': 3,
                 'proposed_action': 'adjust_float_width', 'target': {'label': 'fig:later', 'float_type': 'figure'}}]
            (data / 'repair_plan.json').write_text(json.dumps({'candidates': candidates}), encoding='utf-8')
            attach_repair_plan_fingerprint(project_root=root, main_tex='main.tex', repair_plan_path='data/repair_plan.json')
            runtime.manager.update({'compile_success': True, 'page_images_rendered': True,
                # A legacy or mocked clean outer gate is deliberately insufficient.
                'last_gatekeeper_decision': 'CONTINUE' if counters['core'] == 1 or kind.startswith('loss') else 'DONE',
                'defect_summary': {'initial_total': 1, 'remaining': 1 if counters['core'] == 1 else 0, 'resolved': 0},
                'repair_plan_summary': {'total_candidates': len(candidates)},
                'artifacts': {'page_images_dir': 'data/pages', 'visual_signal_report': 'data/visual_signal_report.json',
                              'defect_report': 'data/defect_report.json', 'gatekeeper_decision': 'data/gatekeeper_decision.json',
                              'repair_plan': 'data/repair_plan.json', 'rule_report': 'data/rule_report.json'}})
            state = runtime.manager.load()
            state['rule_report'] = {'summary': {'overfull_hbox_total': 0}}
            return state

        def actual_guarded_edit(plan_path, main_tex, output_path, **kwargs):
            counters['execute'] += 1
            sources = {main: main.read_bytes(), child: child.read_bytes()}
            before = main.read_text(encoding='utf-8')
            if kind == 'layout':
                main.write_text(before.replace('Result', r'\newpage' + '\nResult'), encoding='utf-8')
            elif kind == 'source':
                main.write_text(before.replace(r'\begin{document}', r'\AddToHook{env/thebibliography/begin}{\small}' + '\n' + r'\begin{document}'), encoding='utf-8')
            elif kind == 'loss_math':
                child.write_text(CHILD + r'\begin{equation}x-y=17\end{equation}', encoding='utf-8')
            elif kind == 'loss_include':
                child.unlink()
            elif kind == 'loss_main':
                main.unlink()
            report = _enforce_structure_integrity(main_tex='main.tex', original_tex=before,
                original_bytes=sources[main], original_sources=sources, project_root=root,
                report={'status': 'success', 'applied_count': 1, 'main_tex': 'main.tex'})
            Path(output_path).write_text(json.dumps(report), encoding='utf-8')
            return report

        def forbidden_trial(*args, **kwargs):
            counters['trial_compile'] += 1
            return {'success': True, 'pdf_path': str(pdf)}

        spec = TaskSpec.from_dict({'task_type': 'full_vto', 'project_root': str(root), 'main_tex': 'main.tex',
            'target_pages': 1, 'page_policy': 'exact', 'page_budget_scope': 'with_appendix',
            'allow_source_mutation': True, 'pre_repair_snapshot_required': True, 'rollback_policy': 'required', 'max_rounds': 2})
        guards = {'available': True, 'hard_failures': [{'kind': 'synthetic_review_finding'}] if hard_visual else []}
        with (mock.patch('runtime_actions.pdf_page_count', side_effect=lambda path: len(pymupdf.open(path))),
              mock.patch.object(orchestrator_runtime, 'compile_latex', side_effect=compile_observation),
              mock.patch.object(orchestrator_runtime, 'render_pdf_pages', side_effect=render_observation),
              mock.patch.object(orchestrator_runtime, 'inspect_endmatter_float_intrusion', return_value=guards),
              mock.patch.object(OrchestratorRuntime, '_run_round_core', fake_core),
              mock.patch('repair_plan_executor.execute_repair_plan', side_effect=actual_guarded_edit),
              mock.patch('runtime_actions.compile_latex', side_effect=forbidden_trial)):
            result = OrchestratorRuntime(state_path=str(root / 'data/state.json')).run_task(
                task_spec=spec, output_path='data/run_result.json')
        return root, counters, result

    def test_phased_pending_stops_next_edit_and_outer_page_gap_never_rolls_it_back(self):
        root, counts, result = self.run_fixture()
        self.assertEqual(counts['execute'], 1)
        self.assertEqual(counts['trial_compile'], 0)
        self.assertEqual(counts['observe'], 2)  # baseline + one actual outer observation
        self.assertEqual(result['status'], 'continue')
        self.assertFalse(result['success'])
        self.assertEqual(result['gatekeeper_decision'], 'CONTINUE')
        self.assertEqual(result['failure']['failure_type'], 'source_review_pending')
        self.assertIn(r'\newpage', (root / 'main.tex').read_text(encoding='utf-8'))
        self.assertEqual((root / 'part.tex').read_text(encoding='utf-8'), CHILD)
        self.assertNotIn('rollback_after_quality_regression', result['runtime_actions'])
        self.assertFalse(result['runtime_actions']['repair_plan_executor']['success'])
        self.assertEqual(result['repair_loop_policy']['stop_condition'], 'source_review_pending')
        self.assertFalse(result['repair_loop_policy']['next_round_allowed'])
        self.assertEqual(result['round_metrics'][0]['page_objective_after']['target_met'], False)
        self.assertEqual(result['round_metrics'][0]['acceptance']['rollback_required'], False)
        checkpoint = result['source_review_pending']
        self.assertFalse(checkpoint['approval_generated'])
        self.assertTrue(Path(checkpoint['report_path']).is_file())
        saved = json.loads(Path(checkpoint['report_path']).read_text(encoding='utf-8'))
        self.assertEqual(saved['source_edit_checks']['status'], 'manual_review')
        copies = checkpoint['artifact_copies']
        self.assertEqual(sum(item['original_path'].endswith('.png') for item in copies), 2)
        self.assertTrue(any(item['original_path'] == 'main.pdf' for item in copies))
        for item in copies:
            self.assertEqual(hashlib.sha256(Path(item['copy_path']).read_bytes()).hexdigest(), item['sha256'])
        self.assertEqual(result['round_execution_records'][0]['accepted'], 0)
        self.assertEqual(result['round_execution_records'][0]['rollback_scope'], 'none')

    def test_source_hook_pending_and_its_raw_pass_are_preserved(self):
        root, counts, result = self.run_fixture('source', final_pages=1)
        self.assertEqual(result['status'], 'continue')
        self.assertFalse(result['success'])
        self.assertEqual(counts['execute'], 1)
        self.assertEqual(counts['trial_compile'], 0)
        self.assertIn(r'\AddToHook', (root / 'main.tex').read_text(encoding='utf-8'))
        raw = result['source_review_pending']['source_edit_checks']['scientific_payload']['file_reports'][0]
        self.assertEqual(raw['status'], 'manual_review')
        self.assertEqual(raw['automated_status'], 'pass')
        self.assertEqual(raw['automated_report']['status'], 'pass')
        self.assertFalse(result['source_review_pending']['approval_generated'])

    def test_pending_visual_findings_do_not_trigger_more_source_healing(self):
        root, counts, result = self.run_fixture(hard_visual=True)
        self.assertEqual(counts['execute'], 1)
        self.assertEqual(counts['observe'], 2)
        self.assertIn(r'\newpage', (root / 'main.tex').read_text(encoding='utf-8'))
        self.assertTrue(result['source_review_pending']['observation']['visual_hard_guards']['hard_failures'])
        self.assertNotEqual(result['status'], 'done')

    def test_confirmed_math_loss_and_tracked_file_deletion_still_restore(self):
        for kind in ('loss_math', 'loss_include', 'loss_main'):
            with self.subTest(kind=kind):
                root, _, result = self.run_fixture(kind, final_pages=1)
                self.assertEqual((root / 'main.tex').read_text(encoding='utf-8'), BASE)
                self.assertEqual((root / 'part.tex').read_text(encoding='utf-8'), CHILD)
                self.assertNotIn('source_review_pending', result)
                raw = json.loads((root / 'data/phased_local_report.json').read_text(encoding='utf-8'))
                self.assertEqual(raw['status'], 'failed')
                self.assertTrue(raw['content_integrity']['rollback_performed'])


if __name__ == '__main__':
    unittest.main()
