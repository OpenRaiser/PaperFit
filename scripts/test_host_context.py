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

if __name__ == "__main__":
    unittest.main()

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

if __name__ == "__main__":
    unittest.main()
