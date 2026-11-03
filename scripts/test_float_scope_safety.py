from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import float_fixers
import repair_plan_executor
import repair_plan_generator
import visual_action_plan
from orchestrator_runtime import OrchestratorRuntime
from runtime_types import TaskSpec


class FloatSourceScopeSafetyTest(unittest.TestCase):
    def test_flush_uses_active_real_bibliography_and_keeps_comments_literal(self):
        fake = ('% \\bibliography{old}\n'
                '\\begin{verbatim}\n\\bibliography{example}\n\\end{verbatim}\n'
                '\\verb|\\bibliography{inline}|\n')
        for bibliography in ('\\bibliography{refs}', '\\printbibliography[title={References}]', '\\begin{thebibliography}{99}'):
            with self.subTest(bibliography=bibliography):
                source = '\\documentclass{article}\n\\begin{document}\n' + fake + bibliography + '\n\\end{document}\n'
                after, change = float_fixers._flush_floats_before_endmatter(source)
                self.assertIsNotNone(change)
                self.assertIn(fake, after)
                self.assertEqual(after.count('\\clearpage'), 1)
                self.assertIn('\\clearpage\n' + bibliography, after)
                active = float_fixers._mask_inactive_latex(after)
                self.assertNotIn('old', active)
                self.assertNotIn('example', active)
                self.assertNotIn('inline', active)

    def test_flush_does_not_treat_bibliographystyle_or_commented_source_as_anchor(self):
        for source in ('% \\bibliography{old}\n', '\\bibliographystyle{plain}\n', '\\begin{lstlisting}\n\\bibliography{example}\n\\end{lstlisting}\n'):
            with self.subTest(source=source):
                after, change = float_fixers._flush_floats_before_endmatter(source)
                self.assertEqual(after, source)
                self.assertIsNone(change)

    def test_placement_normalization_excludes_comments_and_literal_code(self):
        fake = ('% \\begin{figure}[p]\n'
                '\\begin{verbatim}\n\\begin{figure}[p]\n\\end{verbatim}\n'
                '\\begin{minted}{latex}\n\\begin{table}[p]\n\\end{minted}\n'
                '\\verb|\\begin{figure}[p]|\n')
        active_float = '\\begin{figure}[p]\n\\caption{Kept}\n\\end{figure}\n'
        after, change = float_fixers._normalize_float_only_placements(fake + active_float)
        self.assertIsNotNone(change)
        self.assertEqual(after, fake + active_float.replace('[p]', '[htbp]'))

    def test_no_anchor_b1_requires_explicit_document_scope(self):
        source = '\\documentclass{article}\n\\begin{document}\n\\begin{figure}[p]\n\\caption{Author choice}\\label{fig:chosen}\n\\end{figure}\n\\bibliography{refs}\n\\end{document}\n'
        for approval in (None, False, 'true'):
            with self.subTest(approval=approval), tempfile.TemporaryDirectory() as tmp:
                main = Path(tmp) / 'main.tex'
                main.write_text(source, encoding='utf-8')
                defect = {'defect_id': 'B1', 'object': None, 'severity': 'major', 'description': 'float drift near references'}
                if approval is not None:
                    defect['document_scope_approved'] = approval
                report = float_fixers.fix_float_defects(str(main), [defect])
                self.assertEqual(main.read_text(encoding='utf-8'), source)
                self.assertEqual(report.changes, [])

    def test_explicit_document_scope_retains_data_and_literal_placements(self):
        fake = '\\begin{verbatim}\n\\begin{figure}[p]\n\\end{verbatim}\n'
        source = '\\documentclass{article}\n\\begin{document}\n' + fake + '\\begin{figure}[p]\n\\includegraphics[width=.4\\linewidth]{plot.pdf}\n\\caption{Accuracy 97.3}\\label{fig:chosen}\n\\end{figure}\n\\bibliography{refs}\n\\end{document}\n'
        with tempfile.TemporaryDirectory() as tmp:
            main = Path(tmp) / 'main.tex'
            main.write_text(source, encoding='utf-8')
            report = float_fixers.fix_float_defects(str(main), [{'defect_id': 'B1', 'object': None, 'severity': 'major', 'document_scope_approved': True, 'hard_guard_evidence': {'type': 'float_stacking', 'pages': [5]}}])
            after = main.read_text(encoding='utf-8')
            self.assertTrue(report.changes)
            self.assertIn(fake, after)
            self.assertIn('\\begin{figure}[htbp]', after)
            self.assertIn('\\includegraphics[width=.4\\linewidth]{plot.pdf}', after)
            self.assertIn('\\caption{Accuracy 97.3}\\label{fig:chosen}', after)
            self.assertNotIn('\\clearpage', after)

    def test_clearpage_needs_structured_evidence_even_when_document_scope_approved(self):
        base = '\\documentclass{article}\n\\begin{document}\n\\begin{figure}[p]\n\\caption{Same}\\label{fig:chosen}\n\\end{figure}\n% \\bibliography{old}\n\\bibliography{refs}\n\\end{document}\n'
        for evidence in (False, True):
            with self.subTest(evidence=evidence), tempfile.TemporaryDirectory() as tmp:
                main = Path(tmp) / 'main.tex'
                main.write_text(base, encoding='utf-8')
                float_fixers.fix_float_defects(str(main), [{'defect_id': 'B1', 'object': None, 'severity': 'major', 'description': 'reference/endmatter', 'document_scope_approved': True, 'hard_guard_evidence': {'type': 'float_stacking', 'pages': [5]}, 'endmatter_float_intrusion': evidence}])
                after = main.read_text(encoding='utf-8')
                self.assertIn('% \\bibliography{old}\n', after)
                self.assertEqual('\\clearpage' in after, evidence)
                if evidence:
                    self.assertIn('\\clearpage\n\\bibliography{refs}', after)

    def test_b1_relocation_does_not_guess_caption_or_main_graphic_dimensions(self):
        source = ('\\documentclass{article}\n\\begin{document}\nSee Figure~\\ref{fig:late}.\n\n'
                  + ('Background prose.\n\n' * 25)
                  + '\\begin{figure}[p]\n\\caption{Token \\includegraphics[width=.1\\linewidth]{arrow.pdf}}\n'
                  + '\\includegraphics[width=.4\\linewidth]{main.pdf}\n\\label{fig:late}\n\\end{figure}\n\\end{document}\n')
        after, result = float_fixers.fix_float_reference_distance(source, 'fig:late', 10, 1, ref_line=3)
        self.assertIsNotNone(result)
        self.assertIn('\\includegraphics[width=.1\\linewidth]{arrow.pdf}', after)
        self.assertIn('\\includegraphics[width=.4\\linewidth]{main.pdf}', after)
        self.assertEqual(after.count('\\includegraphics'), 2)

    def test_verbatim_target_label_is_not_a_repair_anchor(self):
        source = ('\\begin{verbatim}\n\\begin{figure}[t]\n\\includegraphics[width=1.4\\linewidth]{example.pdf}\n'
                  '\\caption{Example}\\label{fig:example}\n\\end{figure}\n\\end{verbatim}\n')
        after, result = float_fixers.fix_figure_width_mismatch(source, 'fig:example', visual_width_subtype='overflow_width')
        self.assertEqual(after, source)
        self.assertIsNone(result)


class FloatCandidateBoundarySafetyTest(unittest.TestCase):
    def test_unanchored_finding_is_not_authority_for_document_wide_repairs(self):
        candidate = {'defect_family': 'B1', 'target': {}, 'page': 2, 'severity': 'major', 'proposed_action': 'move_float_near_reference'}
        self.assertEqual(repair_plan_executor._build_float_defects({'candidates': [candidate]}, 10), [])
        approved = dict(candidate, document_scope_approved=True, endmatter_float_intrusion=True, hard_guard_evidence={'type': 'float_stacking', 'pages': [5]})
        defects = repair_plan_executor._build_float_defects({'candidates': [approved]}, 10)
        self.assertEqual(len(defects), 1)
        self.assertIs(defects[0]['document_scope_approved'], True)
        self.assertIs(defects[0]['endmatter_float_intrusion'], True)

    def test_failed_guard_reports_review_without_mutating_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = b'\\documentclass{article}\n\\begin{document}Kept 3.14\\end{document}\n'
            main = root / 'main.tex'
            main.write_bytes(original)
            runtime = OrchestratorRuntime.__new__(OrchestratorRuntime)
            runtime._record_runtime_action = mock.Mock()
            spec = TaskSpec.from_dict({'task_type': 'full_vto', 'main_tex': 'main.tex', 'project_root': str(root), 'allow_source_mutation': True, 'pre_repair_snapshot_required': True, 'rollback_policy': 'required'})
            with mock.patch.object(float_fixers, 'fix_float_defects') as fixer:
                report = runtime._heal_endmatter_float_intrusion(project_root=root, task_spec=spec, post_observe={'visual_hard_guards': {'intrusions': [{'page': 8, 'captions': ['Figure 7:']}]}}, emit_event=lambda **kwargs: {}, runtime_actions={})
            fixer.assert_not_called()
            self.assertFalse(report['applied'])
            self.assertTrue(report['review_required'])
            self.assertEqual(main.read_bytes(), original)


class DocumentScopeProtocolTest(unittest.TestCase):
    def finding(self, severity='major', **overrides):
        finding = {'defect_id': 'vlm-b1', 'taxonomy_defect_id': 'B1', 'source': 'vlm_detector',
                   'severity': severity, 'confidence': .9, 'page': 5, 'metrics': {},
                   'description': 'Floats cluster on the final page.'}
        finding.update(overrides)
        return finding

    def approval(self):
        return {'document_scope_approved': True,
                'hard_guard_evidence': {'type': 'float_stacking', 'pages': [5]}}

    def promote(self, entry):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / 'visual_action_plan.json').write_text(json.dumps({'entries': [entry]}), encoding='utf-8')
            return repair_plan_generator._promote_visual_action_plan(str(directory / 'visual_signal_report.json'))

    def test_unanchored_b1_is_review_for_all_severities(self):
        for severity in ('minor', 'major', 'critical'):
            with self.subTest(severity=severity):
                plan = visual_action_plan.build_visual_action_plan(visual_report={'findings': [self.finding(severity)]}, crossrefs_report={})
                entry = plan['entries'][0]
                self.assertEqual(entry['status'], 'human_review')
                self.assertNotIn('normalize_float_placements_document_level', entry['recommended_actions'])
                self.assertEqual(self.promote(entry), [])

    def test_model_fields_cannot_grant_document_scope_permission(self):
        finding = self.finding(**self.approval())
        finding['metrics'] = dict(self.approval())
        plan = visual_action_plan.build_visual_action_plan(visual_report={'findings': [finding]}, crossrefs_report={})
        self.assertEqual(plan['entries'][0]['status'], 'human_review')
        self.assertNotIn('document_scope_approved', plan['entries'][0])

    def test_separate_host_approval_and_evidence_reach_candidate_executor(self):
        plan = visual_action_plan.build_visual_action_plan(visual_report={'findings': [self.finding()]}, crossrefs_report={}, document_scope_approvals={'vlm-b1': self.approval()})
        entry = plan['entries'][0]
        self.assertEqual(entry['status'], 'executable')
        self.assertIn('normalize_float_placements_document_level', entry['recommended_actions'])
        candidates = self.promote(entry)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]['proposed_action'], 'normalize_float_placements_document_level')
        self.assertEqual(candidates[0]['hard_guard_evidence'], self.approval()['hard_guard_evidence'])
        defects = repair_plan_executor._build_float_defects({'candidates': candidates}, 3)
        self.assertEqual(len(defects), 1)
        self.assertTrue(visual_action_plan.document_scope_b1_authorized(defects[0]))

    def test_approval_without_structure_is_review_and_not_executed(self):
        plan = visual_action_plan.build_visual_action_plan(visual_report={'findings': [self.finding()]}, crossrefs_report={}, document_scope_approvals={'vlm-b1': {'document_scope_approved': True}})
        self.assertEqual(plan['entries'][0]['status'], 'human_review')
        candidate = {'defect_family': 'B1', 'target': {}, 'page': 5, 'document_scope_approved': True}
        self.assertEqual(repair_plan_executor._build_float_defects({'candidates': [candidate]}, 3), [])

    def test_legacy_unanchored_executable_claim_is_not_promoted(self):
        for severity in ('minor', 'major'):
            entry = {'family': 'B1', 'finding_id': 'old-b1', 'page': 5, 'label': None,
                     'status': 'executable', 'confidence': .9, 'severity': severity,
                     'source_anchors': [], 'anchor_status': 'unresolved'}
            self.assertEqual(self.promote(entry), [])


if __name__ == '__main__':
    unittest.main()
