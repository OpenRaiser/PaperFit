from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from source_map import build_source_map, masked_tex
from host_context import build_host_context

class HostContextTests(unittest.TestCase):
    def fixture(self, files):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        root = Path(t.name)
        for name, text in files.items():
            dest = root / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
        return root

    def test_real_source_lines_and_caption_graphics(self):
        root = self.fixture({"main.tex": r"\input{sections/body}",
            "sections/body.tex": "\n" + r"\begin{figure}[p]" + "\n" +
            r"\caption[short]{A {nested} caption \includegraphics[width=.1\linewidth]{icon}}" + "\n" +
            r"\includegraphics[width=.6\linewidth]{body}" + "\n" +
            r"\label{fig:same}\end{figure}" + "\n" + r"\ref{fig:same}"})
        result = build_source_map(root / "main.tex")
        obj = result["objects"][0]
        self.assertEqual(obj["file"], "sections/body.tex")
        self.assertEqual(obj["start_line"], 2)
        self.assertEqual([g["in_caption"] for g in obj["graphics"]], [True, False])
        self.assertEqual(obj["references"][0]["line"], 6)
        self.assertIn("{nested}", obj["captions"][0])

    def test_comments_literal_examples_are_not_objects(self):
        root = self.fixture({"main.tex": "% " + r"\begin{figure}\label{bad}\end{figure}" + "\n" +
            r"\begin{verbatim}\begin{figure}\label{bad2}\end{figure}\end{verbatim}" + "\n" +
            r"\verb|\begin{figure}| \begin{table}\label{ok}\end{table}"})
        result = build_source_map(root / "main.tex")
        self.assertEqual([o["labels"] for o in result["objects"]], [["ok"]])
        self.assertEqual(masked_tex("x \\% y % hidden\nz").count("\n"), 1)

    def test_include_cycle_external_and_duplicate_labels(self):
        root = self.fixture({"main.tex": r"\input{a}\input{../outside}" + "\n" +
                             r"\begin{table}\label{same}\end{table}",
                             "a.tex": r"\input{main}\begin{figure}\label{same}\end{figure}"})
        (root.parent / "outside.tex").write_text("outside", encoding="utf-8")
        self.addCleanup((root.parent / "outside.tex").unlink)
        result = build_source_map(root / "main.tex")
        reasons = {w["reason"] for w in result["warnings"]}
        self.assertTrue({"include_cycle", "external_include", "duplicate_float_label"} <= reasons)
        self.assertTrue(all(o["anchor_status"] == "ambiguous" for o in result["objects"]))

    def test_brief_resolves_included_label_without_flattened_line_guess(self):
        root = self.fixture({"main.tex": r"\input{table}",
                             "table.tex": "\n" + r"\begin{table}\label{tab:one}\end{table}"})
        data = root / "data"
        data.mkdir()
        (data / "visual_action_plan.json").write_text(json.dumps({"entries": [{
            "label": "tab:one", "object_type": "table_like", "status": "executable", "page": 2,
            "source_anchors": [{"file": "main.tex", "start_line": 99}]}]}))
        result = build_host_context(root / "main.tex", root=root)
        self.assertEqual(result["findings"][0]["source_anchor"],
                         {"file": "table.tex", "start_line": 2, "end_line": 2})

    def test_missing_or_invalid_diagnostics_are_not_clean(self):
        root = self.fixture({"main.tex": r"\begin{document}Hello\end{document}"})
        result = build_host_context(root / "main.tex", root=root)
        self.assertEqual(result["reports"]["visual"], "missing")
        self.assertNotEqual(result["vision"].get("clean"), True)
        data = root / "data"
        data.mkdir()
        (data / "visual_signal_report.json").write_text("[]")
        self.assertEqual(build_host_context(root / "main.tex", root=root)["reports"]["visual"], "invalid")

    def test_truncated_brief_discloses_full_diagnosis_and_ambiguous_labels(self):
        root = self.fixture({"main.tex": r"\input{a}\input{b}",
            "a.tex": r"\begin{figure}\label{same}\end{figure}",
            "b.tex": r"\begin{figure}\label{same}\end{figure}"})
        data = root / "data"
        data.mkdir()
        (data / "visual_action_plan.json").write_text(json.dumps({"entries": [
            {"label": "same", "object_type": "figure_like", "status": "executable", "page": p} for p in [1,2]]}))
        result = build_host_context(root / "main.tex", root=root, limit=1)
        self.assertEqual(result["finding_counts"], {"total": 2, "shown": 1, "omitted": 1})
        self.assertEqual(result["findings"][0]["status"], "human_review")
        self.assertIsNone(result["findings"][0]["source_anchor"])


class StaticSourceSafetyTests(unittest.TestCase):
    fixture = HostContextTests.fixture

    def test_macro_definitions_do_not_import_unused_tables_or_emit_objects(self):
        definitions = [
            r'\newcommand{\unused}[1]{\input{table_5}\begin{figure}\label{ghost}\end{figure}}',
            r'\def\unused#1{\input{table_5}\ref{ghost}}',
            r'\newenvironment{unused}{\begin{figure}\input{table_5}}{\end{figure}}',
            r'\NewDocumentCommand{\unused}{m}{\input{table_5}}',
            r'\NewDocumentEnvironment{unused}{m}{\input{table_5}}{}',
        ]
        for definition in definitions:
            with self.subTest(definition=definition):
                root = self.fixture({'main.tex': definition + '\n' + r'\input{table_4}',
                    'table_4.tex': r'\begin{table}\caption{Active}\label{tab:active}\end{table}',
                    'table_5.tex': r'\begin{table}\caption{Unused}\label{tab:unused}\end{table}'})
                result = build_source_map(root / 'main.tex')
                self.assertEqual({x['file'] for x in result['files']}, {'main.tex', 'table_4.tex'})
                self.assertEqual([x['labels'] for x in result['objects']], [['tab:active']])
                self.assertEqual(result['references'], [])

    def test_percent_inside_inline_and_environment_literals_is_not_a_comment(self):
        for literal in (r'\verb|\input{table_5}%|', r'\verb%\input{table_5}%',
                        r'\lstinline|\input{table_5}%|',
                        '\\begin{verbatim}\n% literal\n\\input{table_5}\n\\end{verbatim}'):
            with self.subTest(literal=literal):
                root = self.fixture({'main.tex': literal + '\n' + r'\input{table_4}',
                    'table_4.tex': r'\begin{table}\label{active}\end{table}',
                    'table_5.tex': r'\begin{table}\label{unused}\end{table}'})
                result = build_source_map(root / 'main.tex')
                self.assertEqual([x['labels'] for x in result['objects']], [['active']])
                self.assertEqual(len(masked_tex(literal)), len(literal))
                self.assertEqual(masked_tex(literal).count('\n'), literal.count('\n'))

    def test_escaped_control_sequences_are_not_float_or_include_commands(self):
        root = self.fixture({'main.tex': r'\\input{unused} \\begin{figure}\\label{ghost}\\end{figure}' + '\n' +
                            r'\begin{table}\label{active}\end{table}',
                            'unused.tex': r'\begin{table}\label{unused}\end{table}'})
        result = build_source_map(root / 'main.tex')
        self.assertEqual([x['labels'] for x in result['objects']], [['active']])
        self.assertEqual([x['file'] for x in result['files']], ['main.tex'])

    def test_starred_caption_and_short_caption_icons_are_distinct_from_body(self):
        root = self.fixture({'main.tex': r'\begin{figure}\caption*[{Short ] text \includegraphics{short}}]{Long {nested} \includegraphics*{long}}' +
                            r'\includegraphics*[width=.6\linewidth]{body}\label{fig:active}\end{figure}'})
        obj = build_source_map(root / 'main.tex')['objects'][0]
        self.assertEqual([x['file'] for x in obj['graphics']], ['short', 'long', 'body'])
        self.assertEqual([x['in_caption'] for x in obj['graphics']], [True, True, False])
        self.assertIn('Long {nested}', obj['captions'][0])

    def test_preamble_guard_does_not_make_later_unconditional_float_uncertain(self):
        root = self.fixture({'main.tex': r'\ifdefined\pdfinfo\pdfinfo{Metadata}\fi' + '\n' +
                            r'\begin{figure}\label{active}\end{figure}'})
        obj = build_source_map(root / 'main.tex')['objects'][0]
        self.assertFalse(obj['conditional_source'])
        self.assertEqual(obj['anchor_status'], 'resolved')

    def test_conditionals_inside_float_and_around_include_require_review(self):
        root = self.fixture({'main.tex': r'\ifdefined\flag\input{table}\fi' + '\n' +
                            r'\begin{figure}\ifnum1=1\includegraphics{one}\else\includegraphics{two}\fi\label{conditional}\end{figure}',
                            'table.tex': r'\begin{table}\label{included}\end{table}'})
        result = build_source_map(root / 'main.tex')
        self.assertTrue(all(x['anchor_status'] == 'requires_host_review' for x in result['objects']))

    def test_repeated_include_marks_transitive_object_as_uncertain(self):
        root = self.fixture({'main.tex': r'\input{section}\input{section}',
                            'section.tex': r'\input{table}',
                            'table.tex': r'\begin{table}\label{same}\end{table}'})
        result = build_source_map(root / 'main.tex')
        self.assertEqual(result['objects'][0]['anchor_status'], 'requires_host_review')
        self.assertIn('repeated_include_requires_host_review', {x['reason'] for x in result['warnings']})

    def test_dynamic_label_is_navigation_candidate_not_resolved_anchor(self):
        root = self.fixture({'main.tex': r'\begin{table}\label{\prefix:one}\end{table}'})
        self.assertEqual(build_source_map(root / 'main.tex')['objects'][0]['anchor_status'], 'requires_host_review')

    def test_archived_input_after_document_end_is_not_an_active_table(self):
        root = self.fixture({'main.tex': r'\begin{document}\input{active}\end{document}\input{unused}',
                            'active.tex': r'\begin{table}\label{active}\end{table}',
                            'unused.tex': r'\begin{table}\label{unused}\end{table}'})
        result = build_source_map(root / 'main.tex')
        self.assertEqual([x['labels'] for x in result['objects']], [['active']])
        self.assertNotIn('unused.tex', {x['file'] for x in result['files']})

    def test_endinput_stops_following_lines_but_not_current_line(self):
        root = self.fixture({'main.tex': r'\input{section}',
                            'section.tex': r'\endinput \input{active}' + '\n' + r'\input{unused}',
                            'active.tex': r'\begin{table}\label{active}\end{table}',
                            'unused.tex': r'\begin{table}\label{unused}\end{table}'})
        result = build_source_map(root / 'main.tex')
        self.assertEqual([x['labels'] for x in result['objects']], [['active']])

    def test_declared_project_root_wins_over_same_named_source_neighbor(self):
        root = self.fixture({'sections/main.tex': r'\input{table}',
                            'table.tex': r'\begin{table}\label{actual}\end{table}',
                            'sections/table.tex': r'\begin{table}\label{unused}\end{table}'})
        result = build_source_map(root / 'sections/main.tex', root=root)
        self.assertEqual(result['objects'][0]['file'], 'table.tex')
        self.assertEqual(result['objects'][0]['labels'], ['actual'])

    def test_source_relative_fallback_is_disclosed_and_requires_review(self):
        root = self.fixture({'sections/main.tex': r'\input{table}',
                            'sections/table.tex': r'\begin{table}\label{fallback}\end{table}'})
        result = build_source_map(root / 'sections/main.tex', root=root)
        self.assertEqual(result['objects'][0]['file'], 'sections/table.tex')
        self.assertEqual(result['objects'][0]['anchor_status'], 'requires_host_review')
        self.assertIn('source_relative_include_fallback', {x['reason'] for x in result['warnings']})

    def test_new_diagnosis_overrides_stale_legacy_clean_summary(self):
        root = self.fixture({'main.tex': 'Text'})
        data = root / 'data'; data.mkdir()
        for status in ['failed', 'partial', 'completed']:
            diagnosis = {'configured': True, 'status': status, 'clean': None if status != 'completed' else False, 'coverage': .5}
            (data / 'visual_signal_report.json').write_text(json.dumps({
                'machine_sources': {'vlm': {'status': 'completed', 'clean': True}}, 'vlm_diagnosis': diagnosis}))
            result = build_host_context(root / 'main.tex', root=root)
            self.assertEqual(result['vision'], diagnosis)
            self.assertEqual(result['vision_source'], 'vlm_diagnosis')

    def test_invalid_or_inconsistent_diagnosis_cannot_inherit_legacy_clean(self):
        root = self.fixture({'main.tex': 'Text'})
        data = root / 'data'; data.mkdir()
        for diagnosis in [None, [], {'status': 'failed', 'clean': True}]:
            (data / 'visual_signal_report.json').write_text(json.dumps({
                'machine_sources': {'vlm': {'status': 'completed', 'clean': True}}, 'vlm_diagnosis': diagnosis}))
            result = build_host_context(root / 'main.tex', root=root)
            self.assertIsNone(result['vision']['clean'])
            self.assertIn(result['vision']['diagnosis_report_status'], ['invalid', 'inconsistent'])

    def test_subdirectory_main_uses_explicit_root_and_project_data(self):
        root = self.fixture({'sections/main.tex': r'\input{table}',
                            'table.tex': r'\begin{table}\label{actual}\end{table}',
                            'sections/table.tex': r'\begin{table}\label{unused}\end{table}'})
        data = root / 'data'; data.mkdir()
        (data / 'visual_action_plan.json').write_text(json.dumps({'entries': [{
            'label': 'actual', 'object_type': 'table', 'status': 'executable', 'page': 1}]}))
        result = build_host_context(root / 'sections/main.tex', root=root)
        self.assertEqual(result['project_root_resolution'], 'explicit')
        self.assertEqual(result['reports']['action_plan'], 'available')
        self.assertEqual(result['findings'][0]['source_anchor']['file'], 'table.tex')

    def test_subdirectory_main_defaults_to_compilation_cwd(self):
        from unittest.mock import patch
        root = self.fixture({'sections/main.tex': r'\input{table}',
                            'table.tex': r'\begin{table}\label{actual}\end{table}'})
        with patch('host_context.Path.cwd', return_value=root):
            result = build_host_context(root / 'sections/main.tex')
        self.assertEqual(result['project_root_resolution'], 'working_directory')
        self.assertEqual(result['source_map']['objects'][0]['file'], 'table.tex')

    def test_malformed_entry_array_does_not_crash_or_claim_available_plan(self):
        root = self.fixture({'main.tex': 'Text'})
        data = root / 'data'; data.mkdir()
        (data / 'visual_action_plan.json').write_text('{"entries": null}')
        result = build_host_context(root / 'main.tex', root=root)
        self.assertEqual(result['reports']['action_plan'], 'invalid')
        self.assertEqual(result['finding_counts']['total'], 0)


class ObjectFamilyNavigationTests(unittest.TestCase):
    fixture = HostContextTests.fixture

    def test_wrapped_objects_resolve_real_lines_without_importing_unused_shadow(self):
        source = '\n'.join([
            r'\input{tables/active}',
            r'\begin{wrapfigure}[11]{r}[8pt]{0.4\textwidth}',
            r'\includegraphics[width=\linewidth]{body.pdf}',
            r'\caption{Wrapped \includegraphics[height=1ex]{arrow.pdf}}',
            r'\label{fig:wrapped}',
            r'\end{wrapfigure}',
            r'\begin{wraptable}{r}{0.6\textwidth}',
            r'\caption{Ablation}',
            r'\label{tab:wrapped}',
            r'\begin{tabular}{lr}A & 1\\\end{tabular}',
            r'\end{wraptable}',
            r'% \input{tables/unused}',
            r'\ref{fig:wrapped}\ref{tab:wrapped}\ref{tab:active}',
        ])
        root = self.fixture({'main.tex': source,
            'tables/active.tex': '\n'+r'\begin{sidewaystable*}[p]\caption{Active}\label{tab:active}\end{sidewaystable*}',
            'tables/unused.tex': r'\begin{table}\caption{Wrong shadow}\label{tab:wrapped}\end{table}'})
        result = build_source_map(root/'main.tex')
        by_label = {label:obj for obj in result['objects'] for label in obj['labels']}
        self.assertEqual(set(by_label), {'fig:wrapped','tab:wrapped','tab:active'})
        figure = by_label['fig:wrapped']
        self.assertEqual((figure['kind'],figure['environment'],figure['file'],figure['start_line'],figure['end_line']),
                         ('figure','wrapfigure','main.tex',2,6))
        self.assertEqual(figure['environment_arguments'],
                         {'lines':'11','placement':'r','overhang':'8pt','width':r'0.4\textwidth'})
        self.assertEqual([(g['file'],g['line'],g['in_caption']) for g in figure['graphics']],
                         [('body.pdf',3,False),('arrow.pdf',4,True)])
        table = by_label['tab:wrapped']
        self.assertEqual((table['kind'],table['start_line'],table['end_line']),('table',7,11))
        self.assertEqual(table['environment_arguments'],{'placement':'r','width':r'0.6\textwidth'})
        self.assertEqual(by_label['tab:active']['file'],'tables/active.tex')
        self.assertEqual({f['file'] for f in result['files']},{'main.tex','tables/active.tex'})
        self.assertTrue(all(obj['anchor_status']=='resolved' for obj in result['objects']))
        self.assertEqual(result['object_marker_coverage']['status'],'static_markers_mapped')
        data=root/'data';data.mkdir()
        (data/'visual_action_plan.json').write_text(json.dumps({'entries':[
            {'label':label,'object_type':kind,'status':'executable','page':1}
            for label,kind in [('fig:wrapped','figure_like'),('tab:wrapped','table_like'),('tab:active','table_like')]]}))
        context=build_host_context(root/'main.tex',root=root)
        hits={finding['label']:finding for finding in context['findings']}
        self.assertEqual(hits['tab:wrapped']['source_anchor'],{'file':'main.tex','start_line':7,'end_line':11})
        self.assertEqual(hits['fig:wrapped']['source_environment'],'wrapfigure')
        self.assertEqual(hits['tab:active']['source_anchor']['file'],'tables/active.tex')
        self.assertEqual(context['source_navigation_coverage'],result['object_marker_coverage'])

    def test_sideways_standard_and_starred_families_have_canonical_kinds(self):
        for env,kind in [('sidewaysfigure','figure'),('sidewaysfigure*','figure'),
                         ('sidewaystable','table'),('sidewaystable*','table')]:
            with self.subTest(environment=env):
                root=self.fixture({'main.tex':r'\begin{'+env+r'}[tp]\caption{Rotated}\label{object}\end{'+env+'}'})
                obj=build_source_map(root/'main.tex')['objects'][0]
                self.assertEqual(obj['kind'],kind)
                self.assertEqual(obj['environment'],env)
                self.assertEqual(obj['environment_arguments'],{'placement':'tp'})
                self.assertEqual(obj['anchor_status'],'resolved')

    def test_longtable_after_split_repair_keeps_table_navigation(self):
        root=self.fixture({'main.tex':r'\begin{longtable}[c]{lr}\caption{Loss}\label{tab:long}A & 1\\\end{longtable}\ref{tab:long}'})
        result=build_source_map(root/'main.tex');obj=result['objects'][0]
        self.assertEqual((obj['kind'],obj['environment']),('table','longtable'))
        self.assertEqual(obj['environment_arguments'],{'placement':'c','column_specification':'lr'})
        self.assertEqual(obj['anchor_status'],'resolved')
        self.assertEqual(result['object_marker_coverage']['mapped_caption_markers'],1)

    def test_unknown_caption_carrier_discloses_partial_navigation(self):
        root=self.fixture({'main.tex':r'\begin{customfloat}\caption{Not supported}\label{unknown}\end{customfloat}'})
        data=root/'data';data.mkdir()
        (data/'visual_action_plan.json').write_text(json.dumps({'entries':[{'label':'unknown','object_type':'figure','status':'executable','page':1}]}))
        result=build_host_context(root/'main.tex',root=root)
        coverage=result['source_navigation_coverage']
        self.assertEqual(coverage['status'],'partial')
        self.assertEqual(coverage['caption_markers'],1)
        self.assertEqual(coverage['mapped_caption_markers'],0)
        self.assertEqual(coverage['unmapped_markers'][0]['environment'],'customfloat')
        self.assertEqual(result['findings'][0]['status'],'human_review')
        self.assertIsNone(result['findings'][0]['source_anchor'])

    def test_captionof_and_unknown_float_environment_are_not_silent_coverage(self):
        root=self.fixture({'main.tex':r'\begin{minipage}{.5\linewidth}\captionof{table}{Outside}\label{inline}\end{minipage}'+'\n'+
                          r'\begin{SCfigure}\label{sidecaption}\end{SCfigure}'})
        result=build_source_map(root/'main.tex')
        self.assertEqual(result['objects'],[])
        markers=result['object_marker_coverage']['unmapped_markers']
        self.assertEqual({m['reason'] for m in markers},{'unmapped_caption_marker','unsupported_object_environment'})
        self.assertEqual(result['object_marker_coverage']['status'],'partial')

    def test_malformed_wrap_arguments_require_review_even_with_literal_label(self):
        root=self.fixture({'main.tex':r'\begin{wrapfigure}{r}\caption{Missing width}\label{bad}\end{wrapfigure}'})
        obj=build_source_map(root/'main.tex')['objects'][0]
        self.assertEqual(obj['argument_status'],'requires_host_review')
        self.assertEqual(obj['anchor_status'],'requires_host_review')
        self.assertEqual(build_source_map(root/'main.tex')['object_marker_coverage']['status'],'partial')

    def test_nested_and_unclosed_known_objects_cannot_claim_complete_anchors(self):
        for source in [r'\begin{figure}\begin{wrapfigure}{r}{.4\linewidth}\caption{Nested}\label{inner}\end{wrapfigure}\label{outer}\end{figure}',
                       r'\begin{wraptable}{r}{.4\linewidth}\caption{Never closed}\label{unclosed}']:
            with self.subTest(source=source):
                root=self.fixture({'main.tex':source});result=build_source_map(root/'main.tex')
                self.assertEqual(result['object_marker_coverage']['status'],'partial')
                self.assertTrue(all(obj['anchor_status']!='resolved' for obj in result['objects']))

    def test_wrapped_examples_inside_macros_comments_and_literals_do_not_emit_markers(self):
        source='\n'.join([r'% \begin{wraptable}{r}{1cm}\caption{Comment}\end{wraptable}',
                         r'\newcommand{\unused}{\begin{wrapfigure}{r}{1cm}\caption{Macro}\end{wrapfigure}}',
                         r'\verb|\captionof{table}{Literal}|',
                         r'\begin{figure}\caption{Real}\label{active}\end{figure}'])
        root=self.fixture({'main.tex':source});result=build_source_map(root/'main.tex')
        self.assertEqual([o['labels'] for o in result['objects']],[['active']])
        self.assertEqual(result['object_marker_coverage']['caption_markers'],1)
        self.assertEqual(result['object_marker_coverage']['unmapped_markers'],[])

if __name__ == '__main__':
    unittest.main()
