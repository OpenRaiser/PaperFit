"""Real PDF counts and source-navigation failure modes for the host toolbox."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import fitz
sys.path.insert(0, str(Path(__file__).resolve().parent))
from page_fit_toolbox import build_toolbox, source_inventory
from host_context import collect_artifact_manifest


class PageFitToolboxTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='paperfit-page-fit-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.main = self.root / 'paper.tex'
        self.main.write_text(r'\documentclass{article}'+'\n'+r'\begin{document}Text.\end{document}', encoding='utf-8')

    def pdf(self, name='paper.pdf', pages=1):
        path = self.root / name
        with fitz.open() as document:
            for number in range(pages):
                page = document.new_page(width=595, height=842)
                page.insert_text((70, 100), 'Frozen content '+str(number), fontsize=10)
            document.save(path)
        return path

    def test_exact_goal_both_directions_and_upper_bound(self):
        for count, target, policy, direction, met in [
            (2, 1, 'exact', 'compress', False), (1, 2, 'exact', 'expand', False),
            (2, 2, 'exact', 'hold', True), (1, 2, 'at_most', 'hold', True),
        ]:
            r = build_toolbox(self.main, pdf=self.pdf(str(count)+policy+str(target)+'.pdf', count),
                              target_pages=target, page_policy=policy)
            self.assertEqual(r['observed_pdf_objective']['direction'], direction)
            self.assertIs(r['observed_pdf_objective']['met'], met)
            self.assertIsNone(r['page_objective']['target_met'])
            self.assertFalse(r['automatic_acceptance'])
            self.assertEqual(r['phase'], 'needs_current_build')

    def test_body_and_references_are_not_total_pages(self):
        for scope in ['main_body', 'with_refs']:
            r = build_toolbox(self.main, pdf=self.pdf(scope+'.pdf', 2),
                              target_pages=2, page_budget_scope=scope)
            self.assertIsNone(r['observed_pdf_objective']['met'])
            self.assertEqual(r['observed_pdf_objective']['direction'], 'unknown')
            self.assertEqual(r['page_objective']['status'], 'unknown')

    def test_missing_invalid_pdf_never_supplies_zero_or_success(self):
        bad = self.root / 'bad.pdf'; bad.write_bytes(b'not a PDF')
        for path in [bad, self.root / 'missing.pdf']:
            r = build_toolbox(self.main, pdf=path, target_pages=1, defects_stable=True)
            self.assertIsNone(r['observed_pdf_objective']['physical_pdf_pages'])
            self.assertIsNone(r['page_objective']['target_met'])
            self.assertEqual(r['phase'], 'needs_current_measurement')

    def test_defects_stable_assertion_cannot_override_compile_or_ref_failures(self):
        log = self.root / 'paper.log'
        log.write_text("! Undefined control sequence.\nLaTeX Warning: Reference eq:a undefined.\n", encoding='utf-8')
        r = build_toolbox(self.main, pdf=self.pdf(), target_pages=1, defects_stable=True)
        self.assertEqual(r['phase'], 'repair_defects')
        self.assertEqual(len(r['log_hints']['blockers']), 2)
        self.assertFalse(r['defects_stable']['automatically_verified'])

    def test_old_pdf_and_clean_log_are_not_fresh_source_binding(self):
        (self.root/'paper.log').write_text('Output written on paper.pdf (1 page).', encoding='utf-8')
        pdf = self.pdf()
        self.main.write_text(self.main.read_text()+'\n% a later source edit', encoding='utf-8')
        r = build_toolbox(self.main, pdf=pdf, target_pages=1, defects_stable=True)
        self.assertIsNone(r['page_objective']['target_met'])
        self.assertEqual(r['pdf_identity']['source_pdf_binding'], 'not_verified')

    def test_real_include_locations_types_and_assets(self):
        self.main.write_text(r'\documentclass{article}'+'\n'+r'\input{layout}'+'\n'+r'\begin{document}\input{body}\end{document}', encoding='utf-8')
        (self.root/'layout.tex').write_text(r'\setlength{\abovedisplayskip}{8pt plus 2pt}'+'\n'+r'\setlength{\itemsep}{2pt}', encoding='utf-8')
        (self.root/'body.tex').write_text(
            r'\begin{figure}[H]\includegraphics{a}\includegraphics{b}\caption{Two panels}\label{fig:1}\end{figure}'+'\n'+
            r'\begin{table}[t]\caption{Results}\label{tab:1}\begin{tabular}{cc}1&2\end{tabular}\end{table}'+'\n'+
            r'\begin{equation}x=1\end{equation}', encoding='utf-8')
        r = build_toolbox(self.main, pdf=self.pdf(), target_pages=1)
        entries = r['source_inventory']['entries']
        lengths = [e for e in entries if e['kind'] == 'spacing_register']
        self.assertEqual([(e['file'], e['line']) for e in lengths], [('layout.tex',1),('layout.tex',2)])
        objects = r['source_inventory']['objects']
        self.assertEqual([o['kind'] for o in objects], ['figure','table'])
        self.assertEqual(len(objects[0]['graphics']), 2)
        self.assertEqual(r['source_inventory']['counts']['display_environments'], 1)

    def test_comment_definition_and_archived_code_not_proposed(self):
        self.main.write_text(
            r'\documentclass{article}'+'\n'+r'% \vspace{-99pt}'+'\n'+
            r'\newcommand{\opaque}{\vspace{-80pt}}'+'\n'+
            r'\begin{document}\vspace{2pt}Text.\end{document}'+'\n'+r'\vspace{-70pt}', encoding='utf-8')
        local = [e for e in source_inventory(self.main,self.root)['entries'] if e['kind']=='local_spacing']
        self.assertEqual([e['value'] for e in local], ['2pt'])

    def test_missing_include_is_explicit_partial_coverage(self):
        self.main.write_text(r'\input{absent}', encoding='utf-8')
        r = build_toolbox(self.main, pdf=self.pdf(), target_pages=1)
        self.assertEqual(r['source_inventory']['coverage']['status'], 'partial')
        self.assertIn('missing_include', [w['reason'] for w in r['source_inventory']['warnings']])

    def test_trial_is_measured_and_never_selected(self):
        old,new=self.pdf('old.pdf',2),self.pdf('new.pdf',1)
        r=build_toolbox(self.main,pdf=new,before_pdf=old,attempt_tier=3,target_pages=1,defects_stable=True)
        self.assertEqual(r['trial']['comparison']['changes']['page_count_delta'],-1)
        self.assertEqual(r['trial']['tier'],3)
        self.assertIsNone(r['trial']['comparison']['selected_candidate'])
        self.assertEqual(r['trial']['decision'],'pending_host_content_template_and_full_visual_review')
        self.assertEqual(r['review_required']['physical_pages'],[1])

    def test_cli_cannot_overwrite_any_reachable_source_or_evidence(self):
        included=self.root/'included.tex';included.write_text('FROZEN INCLUDE',encoding='utf-8')
        self.main.write_text(r'\input{included}',encoding='utf-8');self.pdf()
        report=self.root/'earlier.json';report.write_text('FROZEN REPORT',encoding='utf-8')
        before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in [self.main,included,report]}
        base=[sys.executable,'-B',str(Path(__file__).with_name('page_fit_toolbox.py')),
              '--main',str(self.main),'--target-pages','1']
        for output in [self.main,included,report]:
            proc=subprocess.run(base+['--output',str(output)],capture_output=True,
                                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
            self.assertNotEqual(proc.returncode,0)
        self.assertEqual(before,{p:hashlib.sha256(p.read_bytes()).hexdigest() for p in before})

    def test_cli_stdout_is_one_json_document(self):
        self.pdf()
        command = [sys.executable, '-B', str(Path(__file__).with_name('page_fit_toolbox.py')),
                   '--main', str(self.main), '--target-pages', '1']
        result = subprocess.run(command, capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace'))
        report = json.loads(result.stdout)
        self.assertEqual(report['observed_pdf_objective']['physical_pdf_pages'], 1)
        self.assertIsNone(report['page_objective']['target_met'])

    def test_trial_tier_requires_preserved_baseline(self):
        for tier in [0,7,True,1]:
            with self.assertRaises(ValueError):
                build_toolbox(self.main,target_pages=1,attempt_tier=tier)


    def snapshot_current_artifacts(self, *, native=True):
        """Real identity collector; no stub for the toolbox binding decision."""
        if native:
            # A synthetic native attempt, not a runtime snapshot claiming it
            # compiled. The PDF is a real test PDF; no TeX process is invoked.
            from compile_inputs import NativeCompileEvidence
            from compile_result import source_fingerprint
            evidence = NativeCompileEvidence(self.root, self.main, compiler='latexmk')
            names = source_fingerprint(self.root, self.main.relative_to(self.root).as_posix())
            self.main.with_suffix('.fls').write_text('PWD '+str(self.main.parent)+'\n'+
                ''.join('INPUT '+str(self.root/name)+'\n' for name in names), encoding='utf-8')
            with fitz.open(self.main.with_suffix('.pdf')) as document:
                count = len(document)
            evidence.finalize({'success':True, 'engine':'pdflatex', 'command':['latexmk','-pdf','-recorder',self.main.name],
                'cwd':str(self.main.parent), 'returncode':0, 'timeout':False}, page_count=count)
        data = self.root / 'data'
        (data / 'pages').mkdir(parents=True, exist_ok=True)
        with fitz.open(self.main.with_suffix('.pdf')) as document:
            for index, page in enumerate(document, 1):
                page.get_pixmap().save(data / 'pages' / f'page_{index:03d}.png')
        for name, value in [
            ('visual_signal_report.json', {'findings': [], 'summary': {'total_findings': 0}}),
            ('defect_report.json', {'defects': []}),
            ('gatekeeper_decision.json', {'decision': 'DONE'}),
        ]:
            (data / name).write_text(json.dumps(value), encoding='utf-8')
        artifacts = {name: str(data / filename) for name, filename in {
            'page_images_dir': 'pages', 'visual_signal_report': 'visual_signal_report.json',
            'defect_report': 'defect_report.json', 'gatekeeper_decision': 'gatekeeper_decision.json',
        }.items()}
        snapshot = collect_artifact_manifest(self.root, str(self.main), artifacts)
        (data / 'run_result_fixture.json').write_text(
            json.dumps({'artifact_manifest': snapshot}), encoding='utf-8')

    def test_current_snapshot_and_host_review_can_enter_goal_verification(self):
        self.pdf()
        self.snapshot_current_artifacts()
        report = build_toolbox(self.main, root=self.root, target_pages=1, defects_stable=True)
        self.assertEqual(report['pdf_identity']['source_pdf_binding'], 'verified_native_inputs')
        self.assertIs(report['page_objective']['target_met'], True)
        self.assertEqual(report['phase'], 'verify_goal')
        self.assertFalse(report['automatic_acceptance'])
        unresolved = build_toolbox(self.main, root=self.root, target_pages=2, defects_stable=True)
        self.assertEqual(unresolved['page_objective']['status'], 'unmet')
        self.assertEqual(unresolved['phase'], 'page_fit')
        unreviewed = build_toolbox(self.main, root=self.root, target_pages=1)
        self.assertEqual(unreviewed['phase'], 'requires_host_defect_review')

    def test_included_source_edit_cannot_verify_an_old_on_target_pdf(self):
        included = self.root / 'layout.tex'
        self.main.write_text(r'\documentclass{article}\input{layout}\begin{document}Frozen words.\end{document}', encoding='utf-8')
        included.write_text(r'\setlength{\intextsep}{12pt}', encoding='utf-8')
        self.pdf()
        self.snapshot_current_artifacts()
        bound = build_toolbox(self.main, root=self.root, target_pages=1, defects_stable=True)
        self.assertEqual(bound['phase'], 'verify_goal')
        included.write_text(r'\setlength{\intextsep}{20pt}', encoding='utf-8')
        stale = build_toolbox(self.main, root=self.root, target_pages=1, defects_stable=True)
        self.assertIs(stale['observed_pdf_objective']['met'], True)
        self.assertEqual(stale['page_objective']['status'], 'unknown')
        self.assertIsNone(stale['page_objective']['target_met'])
        self.assertIs(stale['artifact_freshness']['identity']['source_set_matches_snapshot'], False)
        self.assertEqual(stale['phase'], 'needs_current_build')

    def test_unknown_snapshot_cannot_enter_page_fit_with_host_assertion(self):
        self.pdf(pages=2)
        report = build_toolbox(self.main, root=self.root, target_pages=1, defects_stable=True)
        self.assertEqual(report['observed_pdf_objective']['direction'], 'compress')
        self.assertEqual(report['page_objective']['status'], 'unknown')
        self.assertEqual(report['phase'], 'needs_current_build')

    def test_historical_runtime_snapshot_alone_retains_observation_but_not_current_goal(self):
        self.pdf()
        self.snapshot_current_artifacts(native=False)
        report = build_toolbox(self.main, root=self.root, target_pages=1, defects_stable=True)
        self.assertTrue(report['observed_pdf_objective']['met'])
        self.assertIsNone(report['page_objective']['target_met'])
        self.assertEqual(report['native_build_binding']['status'], 'unknown')
        self.assertEqual(report['phase'], 'needs_current_build')

    def test_subdirectory_main_uses_compile_working_directory_include(self):
        folder = self.root / 'src'; folder.mkdir()
        main = folder / 'paper.tex'
        main.write_text(r'\documentclass{article}\begin{document}\input{shared/results}\end{document}', encoding='utf-8')
        (self.root / 'shared').mkdir()
        (self.root / 'shared/results.tex').write_text(
            r'\begin{table}[htbp]\begin{tabular}{lr}A&91.2\\\end{tabular}\caption{Fixed results.}\label{tab:root}\end{table}', encoding='utf-8')
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            report = build_toolbox(main, pdf=self.pdf(), target_pages=1)
        finally:
            os.chdir(previous)
        self.assertEqual(report['root'], str(self.root.resolve()))
        self.assertEqual(report['root_resolution'], 'working_directory')
        self.assertEqual(report['source_inventory']['objects'][0]['file'], 'shared/results.tex')
        self.assertNotIn('missing_include', [w['reason'] for w in report['source_inventory']['warnings']])

    def test_explicit_root_and_main_parent_fallback_are_explained(self):
        self.pdf()
        explicit = build_toolbox(self.main, root=self.root, target_pages=1)
        self.assertEqual(explicit['root_resolution'], 'explicit')
        elsewhere = self.root / 'elsewhere'; elsewhere.mkdir()
        previous = Path.cwd()
        try:
            os.chdir(elsewhere)
            fallback = build_toolbox(self.main, target_pages=1)
        finally:
            os.chdir(previous)
        self.assertEqual(fallback['root_resolution'], 'main_parent_fallback')
        self.assertEqual(fallback['root'], str(self.main.parent.resolve()))

    def test_conditional_child_without_floats_keeps_review_status(self):
        self.main.write_text(r'\documentclass{article}'+'\n'+r'\begin{document}\iffalse\input{unused}\fi Fixed words.\end{document}', encoding='utf-8')
        (self.root / 'unused.tex').write_text(
            r'\setlength{\abovedisplayskip}{25pt}'+'\n'+
            r'\begin{equation}x=y+z\end{equation}'+'\n'+
            r'\begin{itemize}\item Inactive step.\end{itemize}', encoding='utf-8')
        inventory = source_inventory(self.main, self.root)
        self.assertEqual(inventory['objects'], [])
        children = [entry for entry in inventory['entries'] if entry['file'] == 'unused.tex']
        self.assertTrue(children)
        self.assertTrue(all(entry['anchor_status'] == 'requires_host_review' for entry in children))
        self.assertEqual(inventory['navigation_scope']['status'], 'requires_host_review')
        self.assertIn('conditional_tex_requires_host_review', inventory['navigation_scope']['uncertainty_reasons'])
        self.assertIn('Literal markers', inventory['counts_scope'])

    def test_repeated_include_and_source_relative_fallback_need_review(self):
        (self.root / 'repeat.tex').write_text(r'\begin{enumerate}\item Fixed words.\end{enumerate}', encoding='utf-8')
        self.main.write_text(r'\input{repeat}\input{repeat}', encoding='utf-8')
        inventory = source_inventory(self.main, self.root)
        self.assertTrue(all(entry['anchor_status'] == 'requires_host_review' for entry in inventory['entries']))
        self.assertIn('repeated_include_requires_host_review', inventory['navigation_scope']['uncertainty_reasons'])
        sections = self.root / 'sections'; sections.mkdir()
        (sections / 'entry.tex').write_text(r'\input{local}', encoding='utf-8')
        (sections / 'local.tex').write_text(r'\setlength{\topsep}{8pt}\begin{itemize}\item Fixed words.\end{itemize}', encoding='utf-8')
        self.main.write_text(r'\input{sections/entry}', encoding='utf-8')
        inventory = source_inventory(self.main, self.root)
        child = [entry for entry in inventory['entries'] if entry['file'] == 'sections/local.tex']
        self.assertTrue(child)
        self.assertTrue(all(entry['anchor_status'] == 'requires_host_review' for entry in child))
        self.assertIn('source_relative_include_fallback', inventory['navigation_scope']['uncertainty_reasons'])

    def test_uncertain_include_does_not_reanimate_code_after_end_document(self):
        self.main.write_text(r'\documentclass{article}'+'\n'+
            r'\begin{document}\iffalse\input{unused}\fi\vspace{2pt}Fixed words.\end{document}'+'\n'+
            r'\vspace{-90pt}\renewcommand{\arraystretch}{9}', encoding='utf-8')
        (self.root/'unused.tex').write_text(r'\begin{equation}x=1\end{equation}', encoding='utf-8')
        entries = source_inventory(self.main,self.root)['entries']
        self.assertEqual([e['value'] for e in entries if e['kind']=='local_spacing'], ['2pt'])
        self.assertFalse(any(e['kind']=='table_spacing' for e in entries))

    def test_arraystretch_real_include_anchor_excludes_nonexecuting_definitions(self):
        self.main.write_text(r'\documentclass{article}'+'\n'+
            r'\newcommand{\UnusedTable}{\renewcommand{\arraystretch}{9}}'+'\n'+
            r'\begin{document}\input{table}\end{document}', encoding='utf-8')
        table = self.root/'table.tex'
        table.write_text('% '+r'\renewcommand{\arraystretch}{8}'+'\n'+
            r'\renewcommand{\arraystretch}{1.25}'+'\n'+
            r'\verb|\renewcommand{\arraystretch}{7}|'+'\n'+
            r'\detokenize{\renewcommand{\arraystretch}{6}}'+'\n'+
            r'\begin{verbatim}\renewcommand{\arraystretch}{5}\end{verbatim}'+'\n'+
            r'\begin{table}[htbp]\begin{tabular}{lr}A&91.2\\\end{tabular}\caption{Fixed measurements.}\label{tab:fixed}\end{table}', encoding='utf-8')
        entries = [e for e in source_inventory(self.main,self.root)['entries'] if e['kind']=='table_spacing']
        self.assertEqual([(e['file'],e['line'],e['value']) for e in entries],[('table.tex',2,'1.25')])
        self.assertEqual(entries[0]['syntax'], r'\renewcommand{\arraystretch}{1.25}')
        self.assertEqual(entries[0]['source_sha256'], hashlib.sha256(table.read_bytes()).hexdigest())
        self.assertEqual(entries[0]['anchor_status'], 'literal')

    def test_arraystretch_supported_no_argument_forms_and_nonpositive_review(self):
        self.main.write_text(r'\documentclass{article}'+'\n'+
            r'\renewcommand*\arraystretch[0]{1.2}'+'\n'+
            r'\def\arraystretch{1.1}'+'\n'+
            r'\gdef\arraystretch{0}'+'\n'+
            r'\renewcommand{\arraystretch}{-0.5}'+'\n'+
            r'\renewcommand{\arraystretch}[1]{#1}'+'\n'+
            r'\begin{document}Fixed words.\end{document}', encoding='utf-8')
        entries = [e for e in source_inventory(self.main,self.root)['entries'] if e['kind']=='table_spacing']
        self.assertEqual([e['numeric_value'] for e in entries], [1.2,1.1,0.0,-0.5])
        self.assertEqual([e['anchor_status'] for e in entries], ['literal','literal','requires_host_review','requires_host_review'])
        self.assertTrue(all(e['value_status']=='nonpositive_requires_review' for e in entries[2:]))

    def test_arraystretch_in_conditional_child_is_not_an_executed_recipe(self):
        self.main.write_text(r'\iffalse\input{unused}\fi', encoding='utf-8')
        (self.root/'unused.tex').write_text(r'\renewcommand{\arraystretch}{1.4}', encoding='utf-8')
        entries = source_inventory(self.main,self.root)['entries']
        self.assertEqual(len(entries),1)
        self.assertEqual(entries[0]['value'],'1.4')
        self.assertEqual(entries[0]['anchor_status'],'requires_host_review')


    def test_register_read_in_body_is_not_certified_as_assignment(self):
        self.main.write_text(
            r'\documentclass{article}\begin{document}'
            r'The native value is \the\itemsep=4pt; observed score is 91.2.'
            r'\end{document}', encoding='utf-8')
        hints = [entry for entry in source_inventory(self.main, self.root)['entries']
                 if entry['kind'] == 'direct_spacing_assignment']
        self.assertEqual(len(hints), 1)
        self.assertEqual(hints[0]['anchor_status'], 'requires_host_review')
        self.assertEqual(hints[0]['assignment_status'], 'unverified_context')
        self.assertIn('register read', hints[0]['context_caution'])


if __name__=='__main__':
    unittest.main()
