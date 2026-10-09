"""Artifact-backed executor/gatekeeper review integration.

All compile receipts and page images in these temporary fixtures are synthetic;
these are offline policy regressions, not manuscript quality or compiler claims.
"""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from compile_result import source_fingerprint
from compile_inputs import bind_compile_inputs, snapshot_project_inputs
from gatekeeper_enforcer import make_decision
from layout_policy import build_layout_review_request
from repair_plan_executor import _enforce_structure_integrity, _explicit_review_inputs
from runtime_snapshots import create_pre_repair_snapshot
from source_review import build_source_review_request
from test_layout_policy_host_review import BASE, CurrentProject, digest

SCRIPTS = Path(__file__).resolve().parent


def sign_layout(request):
    for approval in request['approvals']:
        approval['approved'] = True
        approval['evidence'].update(reason='Explicit synthetic full-page review fixture.',
                                    visual_inspected=True, all_pages_inspected=True)
        for key in ('no_blank_padding', 'readability_confirmed'):
            if key in approval['evidence']:
                approval['evidence'][key] = True
    return request


def sign_source(request):
    request.update(approved=True, source_reviewed=True, all_pages_inspected=True,
                   content_preservation_confirmed=True, template_checked=True,
                   intended_object_checked=True, reason='Explicit source and all-pages fixture review.')
    return request


def rebind(p, *, main='main.tex', files=None):
    """Freeze an internally coherent synthetic native receipt after fixture setup."""
    (p.root / main).write_text(p.repaired, encoding='utf-8')
    (p.root / 'baseline.before').write_text(p.original, encoding='utf-8')
    before = snapshot_project_inputs(p.root, p.root / main)
    recorder = (p.root / main).with_suffix('.fls')
    recorder.write_text('PWD ' + str(p.root) + '\n' + ''.join('INPUT ' + name + '\n' for name in files or [main, 'references.bib']), encoding='utf-8')
    fp = source_fingerprint(p.root, main)
    p.attempt.update(main_tex=main, pdf_path=str(p.pdf), pdf_sha256=digest(p.pdf),
                     source_fingerprint=fp, source_fingerprint_before=dict(fp),
                     compile_inputs=bind_compile_inputs(p.root, p.root / main, before))
    p.context['main_tex'] = main
    for name in files or [main, 'references.bib']:
        os.utime(p.root / name, (1700000000, 1700000000))
    os.utime(p.pdf, (1700000010, 1700000010))
    p.set_attempt(p.attempt)


def snapshot(p, *, main='main.tex', before_files=None):
    files = before_files or {main: p.original}
    after = {name: (p.root / name).read_bytes() for name in files}
    try:
        for name, text in files.items():
            (p.root / name).write_text(text, encoding='utf-8')
        result = create_pre_repair_snapshot(project_root=p.root, main_tex=main, snapshot_id='baseline')
    finally:
        for name, content in after.items():
            (p.root / name).write_bytes(content)
            os.utime(p.root / name, (1700000000, 1700000000))
    return result['rollback_target']


def enforce(p, request=None, source_requests=(), *, main='main.tex', before_files=None):
    return _enforce_structure_integrity(
        main_tex=main, original_tex=p.original, original_bytes=p.original.encode('utf-8'),
        original_sources={p.root / name: text.encode('utf-8') for name, text in (before_files or {main: p.original}).items()},
        report={'status': 'success', 'applied_count': 1}, project_root=p.root,
        layout_policy_approvals=request['approvals'] if request else (),
        review_context=request['context'] if request else None,
        source_reviews=source_requests,
    )


def state_for(p, baseline, *, target=None):
    state = {'compile_success': True, 'page_images_rendered': True,
             'task': {'main_tex': p.context['main_tex']},
             'artifacts': {'pre_repair_snapshot': {'rollback_target': baseline}},
             'content_integrity': {'validation_status': 'mutation_pending', 'rollback_target': baseline}}
    if target is not None:
        state['task'].update(target_pages=target, page_policy='exact', page_budget_scope='with_appendix')
    return state


class HostReviewRuntimeIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_unsigned_spacing_candidate_is_retained_without_success(self):
        p = CurrentProject(self.root, r'\newpage')
        before = (self.root / 'main.tex').read_bytes()
        report = enforce(p)
        self.assertEqual(report['status'], 'manual_review')
        self.assertFalse(report['success'])
        self.assertFalse(report['content_integrity']['rollback_performed'])
        self.assertEqual((self.root / 'main.tex').read_bytes(), before)
        baseline = snapshot(p)
        decision = make_decision(state_for(p, baseline), {}, False, None, project_root=p.root)
        self.assertEqual(decision['checks']['layout_policy']['status'], 'manual_review')
        self.assertNotEqual(decision['decision'], 'DONE')

    def test_bound_page_break_passes_executor_and_live_gate_but_not_page_goal(self):
        p = CurrentProject(self.root, r'\pagebreak[4]')
        baseline = snapshot(p)
        request = p.signed()
        report = enforce(p, request)
        self.assertEqual(report['layout_policy']['status'], 'pass')
        self.assertEqual(report['content_integrity']['status'], 'pass')
        self.assertEqual(report['status'], 'success')
        decision = make_decision(state_for(p, baseline, target=3), {}, False, None,
                                 layout_policy_approvals=request['approvals'],
                                 project_root=p.root, review_context=request['context'])
        self.assertTrue(decision['checks']['layout_policy']['pass'])
        self.assertFalse(decision['checks']['page_objective']['pass'])
        self.assertNotEqual(decision['decision'], 'DONE')

    def test_changed_imported_style_or_same_mtime_page_cannot_reuse_review(self):
        for attack in ('style', 'page'):
            with self.subTest(attack=attack), tempfile.TemporaryDirectory() as directory:
                p = CurrentProject(Path(directory), r'\newpage')
                p.add_resources()
                baseline = snapshot(p)
                request = p.signed()
                path = p.root / 'localstyle.sty' if attack == 'style' else next(p.pages.glob('*.png'))
                stat = path.stat()
                path.write_bytes(path.read_bytes() + b'changed')
                os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
                report = enforce(p, request)
                self.assertEqual(report['status'], 'manual_review')
                self.assertFalse(report['success'])
                self.assertEqual((p.root / 'main.tex').read_text(encoding='utf-8'), p.repaired)
                decision = make_decision(state_for(p, baseline), {}, False, None,
                                         layout_policy_approvals=request['approvals'], project_root=p.root,
                                         review_context=request['context'])
                self.assertFalse(decision['checks']['layout_policy']['pass'])
                self.assertNotEqual(decision['decision'], 'DONE')

    def test_math_payload_change_cannot_be_authorized_by_signed_layout_review(self):
        p = CurrentProject(self.root, r'\newpage')
        p.original = p.original.replace('Result words.', r'Result $x+y=17$.')
        p.repaired = p.repaired.replace('Result words.', r'Result $x-y=17$.')
        rebind(p)
        request = p.signed()
        report = enforce(p, request)
        self.assertEqual(report['layout_policy']['status'], 'pass')
        self.assertEqual(report['status'], 'failed')
        self.assertTrue(report['scientific_payload']['rollback_performed'])
        self.assertEqual((p.root / 'main.tex').read_bytes(), p.original.encode('utf-8'))

    def test_review_for_another_main_is_not_used_by_executor_or_gate(self):
        p = CurrentProject(self.root, r'\newpage')
        baseline = snapshot(p)
        request = p.signed()
        context = {**request['context'], 'main_tex': 'other.tex'}
        request['context'] = context
        self.assertEqual(enforce(p, request)['status'], 'manual_review')
        decision = make_decision(state_for(p, baseline), {}, False, None, project_root=p.root,
                                 review_context=context, layout_policy_approvals=request['approvals'])
        self.assertFalse(decision['checks']['layout_policy']['pass'])

    def test_valid_include_source_review_is_consumed_and_raw_report_retained(self):
        p = CurrentProject(self.root, '')
        p.original = p.repaired = BASE.replace('Result words.', r'\input{sections/part}')
        (p.root / 'sections').mkdir()
        old = r'See \ref{fig:a}.\begin{figure}\caption{Result 17.}\end{figure}'
        new = old.replace(r'\caption{Result 17.}', r'\caption{Result 17.}\label{fig:a}')
        (p.root / 'sections/part.tex').write_text(new, encoding='utf-8')
        rebind(p, files=['main.tex', 'references.bib', 'sections/part.tex'])
        before = {'main.tex': p.original, 'sections/part.tex': old}
        baseline = snapshot(p, before_files=before)
        request = sign_source(build_source_review_request(old, new, project_root=p.root,
                              file='sections/part.tex', **p.context))
        report = enforce(p, source_requests=[request], before_files=before)
        self.assertEqual(report['status'], 'success')
        channel = next(item for item in report['scientific_payload']['file_reports'] if item['file'] == 'sections/part.tex')
        self.assertEqual(channel['status'], 'pass')
        self.assertEqual(channel['automated_status'], 'failed')
        self.assertEqual(channel['automated_report']['raw_comparison']['status'], 'failed')
        decision = make_decision(state_for(p, baseline), {}, False, None, project_root=p.root,
                                 source_reviews=[request])
        self.assertTrue(decision['checks']['scientific_payload']['pass'])
        gate_channel = next(item for item in decision['checks']['scientific_payload']['file_reports'] if item['file'] == 'sections/part.tex')
        self.assertEqual(gate_channel['automated_status'], 'failed')

    def test_unreviewed_hook_raw_pass_is_pending_in_executor_and_gate(self):
        p = CurrentProject(self.root, '')
        p.repaired = p.original.replace(r'\begin{document}', r'\AddToHook{env/thebibliography/begin}{\small}' + '\n' + r'\begin{document}')
        rebind(p)
        baseline = snapshot(p)
        report = enforce(p)
        self.assertEqual(report['status'], 'manual_review')
        self.assertFalse(report['content_integrity']['rollback_performed'])
        self.assertEqual((p.root / 'main.tex').read_text(encoding='utf-8'), p.repaired)
        decision = make_decision(state_for(p, baseline), {}, False, None, project_root=p.root)
        self.assertEqual(decision['checks']['scientific_payload']['status'], 'manual_review')
        self.assertNotEqual(decision['decision'], 'DONE')

    def test_signed_reference_hook_review_unlocks_only_current_candidate(self):
        p = CurrentProject(self.root, '')
        p.repaired = p.original.replace(r'\begin{document}', r'\AddToHook{env/thebibliography/begin}{\small}' + '\n' + r'\begin{document}')
        rebind(p)
        baseline = snapshot(p)
        request = sign_source(build_source_review_request(p.original, p.repaired,
                              project_root=p.root, file='main.tex', **p.context))
        report = enforce(p, source_requests=[request])
        self.assertEqual(report['status'], 'success')
        self.assertEqual(report['layout_prose']['file_reports'][0]['automated_status'], 'pass')
        decision = make_decision(state_for(p, baseline), {}, False, None, project_root=p.root,
                                 source_reviews=[request])
        self.assertTrue(decision['checks']['layout_prose']['pass'])
        (p.root / 'main.tex').write_text(p.repaired.replace('Result words.', 'Changed content.'), encoding='utf-8')
        decision = make_decision(state_for(p, baseline), {}, False, None, project_root=p.root,
                                 source_reviews=[request])
        self.assertEqual(decision['decision'], 'BLOCKED')
        self.assertFalse(decision['checks']['layout_prose']['pass'])

    def test_include_math_loss_restores_all_source_bytes(self):
        p = CurrentProject(self.root, '')
        p.original = p.repaired = BASE.replace('Result words.', r'\input{part}')
        old, new = r'Result $x+y=17$.', r'Result $x-y=17$.'
        (p.root / 'part.tex').write_text(new, encoding='utf-8')
        rebind(p, files=['main.tex', 'references.bib', 'part.tex'])
        report = enforce(p, before_files={'main.tex': p.original, 'part.tex': old})
        self.assertEqual(report['status'], 'failed')
        self.assertEqual((p.root / 'part.tex').read_bytes(), old.encode('utf-8'))
        self.assertEqual((p.root / 'main.tex').read_bytes(), p.original.encode('utf-8'))

    def test_explicit_project_root_handles_main_in_subdirectory(self):
        p = CurrentProject(self.root, r'\newpage')
        nested = p.root / 'sections'
        nested.mkdir()
        (p.root / 'main.tex').replace(nested / 'main.tex')
        p.pdf.replace(nested / 'main.pdf')
        p.pdf = nested / 'main.pdf'
        rebind(p, main='sections/main.tex')
        baseline = snapshot(p, main='sections/main.tex')
        request = sign_layout(build_layout_review_request(p.original, p.repaired,
                              project_root=p.root, file='sections/main.tex', **p.context))
        report = enforce(p, request, main='sections/main.tex')
        self.assertEqual(report['layout_policy']['status'], 'pass')
        decision = make_decision(state_for(p, baseline), {}, False, None, project_root=p.root,
                                 layout_policy_approvals=request['approvals'], review_context=request['context'])
        self.assertTrue(decision['checks']['layout_policy']['pass'])

    def test_unreviewed_and_duplicate_include_source_uncertainty_remain_pending(self):
        p = CurrentProject(self.root, '')
        p.original = p.repaired = BASE.replace('Result words.', r'\input{part}')
        old = r'See \ref{fig:a}.\begin{figure}\caption{Result 17.}\end{figure}'
        new = old.replace(r'\caption{Result 17.}', r'\caption{Result 17.}\label{fig:a}')
        (p.root / 'part.tex').write_text(new, encoding='utf-8')
        rebind(p, files=['main.tex', 'references.bib', 'part.tex'])
        before = {'main.tex': p.original, 'part.tex': old}
        baseline = snapshot(p, before_files=before)
        request = sign_source(build_source_review_request(old, new, project_root=p.root,
                              file='part.tex', **p.context))
        for reviews in ([], [request, copy.deepcopy(request)]):
            with self.subTest(review_count=len(reviews)):
                report = enforce(p, source_requests=reviews, before_files=before)
                self.assertEqual(report['status'], 'manual_review')
                self.assertFalse(report['success'])
                self.assertEqual((p.root / 'part.tex').read_text(encoding='utf-8'), new)
                self.assertEqual(report['scientific_payload']['file_reports'][1]['automated_status'], 'failed')
                decision = make_decision(state_for(p, baseline), {}, False, None,
                                         project_root=p.root, source_reviews=reviews)
                self.assertEqual(decision['checks']['scientific_payload']['status'], 'manual_review')
                self.assertNotEqual(decision['decision'], 'DONE')

    def test_stale_source_review_does_not_drop_pending_include(self):
        p = CurrentProject(self.root, '')
        p.original = p.repaired = BASE.replace('Result words.', r'\input{part}')
        old = r'See \ref{fig:a}.\begin{figure}\caption{Result 17.}\end{figure}'
        new = old.replace(r'\caption{Result 17.}', r'\caption{Result 17.}\label{fig:a}')
        (p.root / 'part.tex').write_text(new, encoding='utf-8')
        rebind(p, files=['main.tex', 'references.bib', 'part.tex'])
        baseline = snapshot(p, before_files={'main.tex': p.original, 'part.tex': old})
        request = sign_source(build_source_review_request(old, new, project_root=p.root,
                              file='part.tex', **p.context))
        stat = p.pdf.stat()
        p.pdf.write_bytes(p.pdf.read_bytes() + b'\n')
        os.utime(p.pdf, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        report = enforce(p, source_requests=[request], before_files={'main.tex': p.original, 'part.tex': old})
        self.assertEqual(report['status'], 'manual_review')
        self.assertFalse(report['content_integrity']['rollback_performed'])
        decision = make_decision(state_for(p, baseline), {}, False, None, project_root=p.root,
                                 source_reviews=[request])
        self.assertFalse(decision['checks']['scientific_payload']['pass'])
        self.assertNotEqual(decision['decision'], 'DONE')

    def test_body_font_reduction_and_nonpositive_scale_are_never_reviewable(self):
        for operation in (r'\fontsize{4pt}{5pt}\selectfont', r'\scalebox{0}{Visible words}'):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as directory:
                p = CurrentProject(Path(directory), operation)
                request = p.signed()
                report = enforce(p, request)
                self.assertEqual(report['layout_policy']['status'], 'blocked')
                self.assertEqual(report['status'], 'failed')
                self.assertFalse(report['success'])
                self.assertEqual((p.root / 'main.tex').read_bytes(), p.original.encode('utf-8'))

    def test_removed_main_restores_original_instead_of_reporting_skipped_success(self):
        p = CurrentProject(self.root, '')
        before = p.original.encode('utf-8')
        (p.root / 'main.tex').unlink()
        report = enforce(p)
        self.assertEqual(report['status'], 'failed')
        self.assertFalse(report['success'])
        self.assertTrue(report['content_integrity']['rollback_performed'])
        self.assertEqual((p.root / 'main.tex').read_bytes(), before)

    def test_weak_legacy_approvals_stay_pending(self):
        p = CurrentProject(self.root, r'\newpage')
        request = p.signed()
        request.pop('context')
        report = _enforce_structure_integrity(main_tex='main.tex', original_tex=p.original,
                  report={'status': 'success'}, project_root=p.root,
                  layout_policy_approvals=request['approvals'])
        self.assertEqual(report['status'], 'manual_review')
        self.assertFalse(report['success'])


class ExplicitReviewCliIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def run_cli(self, script, args):
        return subprocess.run([sys.executable, '-B', str(SCRIPTS / script), '--root', str(self.root), *args],
            cwd=SCRIPTS, capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0, timeout=30)

    def test_duplicate_json_and_wrong_main_fail_before_mutation_or_decision_write(self):
        p = CurrentProject(self.root, r'\newpage')
        before = (self.root / 'main.tex').read_bytes()
        (self.root / 'state.json').write_text(json.dumps({'task': {'main_tex': 'main.tex'}}), encoding='utf-8')
        for bad in ('{"schema_version":"paperfit.layout_review.v1","approvals":[],"approvals":[]}',
                    json.dumps({**p.signed(), 'context': {**p.context, 'main_tex': 'other.tex'}})):
            with self.subTest(payload=bad[:45]):
                (self.root / 'review.json').write_text(bad, encoding='utf-8')
                for script, args in [('repair_plan_executor.py', ['--main-tex', 'main.tex', '--repair-plan', 'missing.json']),
                                     ('gatekeeper_enforcer.py', ['--state', 'state.json'])]:
                    result = self.run_cli(script, args + ['--layout-review-json', 'review.json'])
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual((self.root / 'main.tex').read_bytes(), before)
                    self.assertFalse((self.root / 'data/gatekeeper_decision.json').exists())

    def test_gate_cli_reads_bound_review_with_explicit_root_from_another_cwd(self):
        p = CurrentProject(self.root, r'\newpage')
        baseline = snapshot(p)
        (self.root / 'state.json').write_text(json.dumps(state_for(p, baseline)), encoding='utf-8')
        (self.root / 'review.json').write_text(json.dumps(p.signed()), encoding='utf-8')
        result = self.run_cli('gatekeeper_enforcer.py', ['--state', 'state.json', '--layout-review-json', 'review.json'])
        self.assertEqual(result.returncode, 0, result.stderr)
        decision = json.loads((self.root / 'data/gatekeeper_decision.json').read_text(encoding='utf-8'))
        self.assertTrue(decision['checks']['layout_policy']['pass'])
        self.assertNotEqual(decision['decision'], 'DONE')  # no visual inspection summary in this fixture

    def test_loader_rejects_mixed_or_escaping_context(self):
        p = CurrentProject(self.root, r'\newpage')
        for context in ({**p.context, 'page_images_dir': '../pages'}, {**p.context, 'unsupported': 'data/pages'}):
            request = {**p.signed(), 'context': context}
            (self.root / 'review.json').write_text(json.dumps(request), encoding='utf-8')
            with self.assertRaises(ValueError):
                _explicit_review_inputs(self.root, 'main.tex', ['review.json'])
        valid = p.signed()
        (self.root / 'review.json').write_text(json.dumps(valid), encoding='utf-8')
        (self.root / 'state.json').write_text(json.dumps({'task': {'main_tex': 'main.tex'}}), encoding='utf-8')
        result = self.run_cli('gatekeeper_enforcer.py', ['--state', 'state.json', '--layout-review-json', 'review.json', '--layout-policy-approvals', 'review.json'])
        self.assertEqual(result.returncode, 2)


if __name__ == '__main__':
    unittest.main()
