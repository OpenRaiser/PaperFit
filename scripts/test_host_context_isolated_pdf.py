"""Explicit PDF observation and isolated receipt binding, without model calls.

The compiler receipt fixtures are constructed test data, not real compilation
claims. Tests exercise the real verifier with complete current/copied inputs and
real PDF bytes so source, asset, output, and selection changes remain detectable.
"""
from __future__ import annotations

import json
import contextlib
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from host_context import build_host_context, bind_visibility_to_current_source, main as host_context_main
from isolated_build import SCHEMA, inventory, sha
from visibility_review import review_pdf_visibility

try:
    import fitz
except ImportError:
    fitz = None


@unittest.skipIf(fitz is None, 'PyMuPDF unavailable')
class ExplicitIsolatedPDFTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.root = self.base/'paper'
        self.root.mkdir()
        self.main = self.root/'main.tex'
        self.main.write_text(r'\documentclass{article}\begin{document}Exact scientific text\input{body}\end{document}', encoding='utf-8')
        (self.root/'body.tex').write_text('Exact included words and 42 results.', encoding='utf-8')
        (self.root/'plot.dat').write_bytes(b'0 1\n1 2\n')
        (self.root/'data').mkdir()
        (self.root/'data'/'results.csv').write_bytes(b'method,value\nA,42\n')
        (self.root/'data'/'parameters.json').write_bytes(b'{"seed": 42}\n')
        self.build = self.base/'isolated'
        self.source = self.build/'source'
        self.output = self.build/'build'
        self.source.mkdir(parents=True)
        self.output.mkdir()
        for row in inventory(self.root):
            (self.source/row['path']).parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(self.root/row['path'], self.source/row['path'])
        self.pdf = self.output/'main.pdf'
        self.write_pdf(self.pdf)
        self.record = self.write_receipt()

    def write_pdf(self, path, *, clip=False, pages=2):
        with fitz.open() as doc:
            for number in range(pages):
                page = doc.new_page(width=200, height=200)
                page.insert_text((180 if clip else 20,50), f'Page {number+1} readable text',fontsize=12)
            doc.save(path)

    def write_receipt(self):
        log=self.output/(self.main.stem+'.log');log.write_bytes(b'Test compiler log fixture\n')
        stdout=self.build/'stdout.txt';stdout.write_bytes(b'Test stdout\n')
        stderr=self.build/'stderr.txt';stderr.write_bytes(b'')
        record={'schema':SCHEMA,'project_root':str(self.root),'main_relpath':self.main.relative_to(self.root).as_posix(),
                'cwd':str(self.source),'source_inventory':inventory(self.root),
                'source_unchanged_by_tools':True,'compile_success':True,'returncode':0,
                'timed_out':False,'launch_error':None,
                'pdf':{'path':'build/'+self.pdf.name,'sha256':sha(self.pdf)},
                'log':{'path':'build/'+log.name,'sha256':sha(log)},
                'stdout_sha256':sha(stdout),'stderr_sha256':sha(stderr)}
        (self.build/'build_record.json').write_text(json.dumps(record),encoding='utf-8')
        return record

    def context(self, **kwargs):
        return build_host_context(self.main,root=self.root,target_pages=2,page_policy='exact',**kwargs)

    def assert_observed_unbound(self, result):
        vis=result['pdf_visibility']
        self.assertTrue(vis['checked'])
        self.assertEqual(vis['checked_page_count'],2)
        self.assertEqual(vis['geometry_status'],'pass')
        self.assertTrue(vis['geometry_pass'])
        self.assertEqual(vis['status'],'unverified_current_source')
        self.assertFalse(vis['pass'])
        self.assertIsNone(vis['applicable_to_current_source'])
        self.assertEqual(vis['source_pdf_binding'],'unverified')
        self.assertTrue(result['pdf_layout_profile']['checked'])
        self.assertEqual(result['pdf_layout_profile']['page_count'],2)
        self.assertIsNone(result['pdf_layout_profile']['applicable_to_current_source'])
        self.assertEqual(result['page_objective']['evidence_status'],'unknown')

    def test_default_runtime_boundary_does_not_read_external_pdf(self):
        with patch('visibility_review.check_pdf_visibility') as check:
            result=review_pdf_visibility(self.pdf,root=self.root)
        check.assert_not_called()
        self.assertFalse(result['checked'])
        self.assertEqual(result['reason'],'pdf_outside_project')

    def test_explicit_external_geometry_is_complete_readonly_observation(self):
        before={p: p.read_bytes() for p in self.base.rglob('*') if p.is_file()}
        result=review_pdf_visibility(self.pdf,root=self.root,allow_external_pdf=True)
        self.assertTrue(result['checked'])
        self.assertTrue(result['complete_document'])
        self.assertTrue(result['geometry_pass'])
        self.assertFalse(result['pass'])
        self.assertIsNone(result['applicable_to_current_source'])
        self.assertEqual(result['pdf_sha256'],sha(self.pdf))
        self.assertEqual(before,{p:p.read_bytes() for p in self.base.rglob('*') if p.is_file()})

    def test_host_explicit_pdf_without_build_is_viewable_not_current(self):
        self.assert_observed_unbound(self.context(pdf=self.pdf))
        self.assertTrue(any('read-only page inspection' in t for t in self.context(pdf=self.pdf)['host_tasks']))

    def test_external_same_hash_cannot_borrow_neighbor_runtime_snapshot(self):
        vis=review_pdf_visibility(self.pdf,root=self.root,allow_external_pdf=True)
        snapshot={'manifest_status':'available','identity':{
            'main_tex_matches_snapshot':True,'source_set_matches_snapshot':True,
            'pdf_matches_snapshot':True,'pdf':{'sha256':sha(self.pdf)}},
            'checks':{'pdf_not_older_than_source_set':{'pass':True}}}
        result=bind_visibility_to_current_source(vis,snapshot)
        self.assertFalse(result['pass'])
        self.assertEqual(result['source_pdf_binding'],'unverified')
        self.assertTrue(result['geometry_pass'])

    def test_verified_isolated_build_binds_source_pdf_profile_and_page_count(self):
        result=self.context(pdf=self.pdf,build_dir=self.build)
        self.assertEqual(result['isolated_build_binding']['status'],'current')
        self.assertEqual(result['pdf_visibility']['source_pdf_binding'],'verified_isolated_build')
        self.assertTrue(result['pdf_visibility']['applicable_to_current_source'])
        self.assertTrue(result['pdf_visibility']['pass'])
        self.assertEqual(result['pdf_visibility']['status'],'pass')
        self.assertTrue(result['pdf_layout_profile']['applicable_to_current_source'])
        self.assertEqual(result['page_objective']['evidence_status'],'current')
        self.assertIsNone(result['vision']['clean'])  # Build identity never certifies VLM snapshot.

    def test_build_dir_alone_selects_known_output_not_receipt_path(self):
        result=self.context(build_dir=self.build)
        self.assertEqual(result['selected_pdf'],str(self.pdf))
        self.assertEqual(result['pdf_selection'],'isolated_build')
        self.assertTrue(result['pdf_visibility']['applicable_to_current_source'])

    def test_public_cli_build_dir_selects_and_verifies_isolated_pdf(self):
        output=self.base/'host_context.json'
        result=subprocess.run([sys.executable,'-B',str(Path(__file__).with_name('host_context.py')),
            '--main',str(self.main),'--root',str(self.root),'--build-dir',str(self.build),
            '--target-pages','2','--page-policy','exact','--output',str(output)],
            capture_output=True,text=True,check=False)
        self.assertEqual(result.returncode,0,result.stderr)
        payload=json.loads(output.read_text(encoding='utf-8'))
        self.assertEqual(payload['selected_pdf'],str(self.pdf))
        self.assertEqual(payload['pdf_visibility']['source_pdf_binding'],'verified_isolated_build')
        self.assertTrue(payload['pdf_layout_profile']['applicable_to_current_source'])

    def test_nested_main_cli_uses_explicit_or_containing_cwd_root_and_build_pdf(self):
        nested=self.root/'manuscript';nested.mkdir()
        self.main=self.main.rename(nested/'article.tex')
        (self.root/'body.tex').rename(nested/'body.tex')
        for name in ['main.tex','body.tex']:(self.source/name).unlink()
        for name in ['article.tex','body.tex']:
            target=self.source/'manuscript'/name
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(nested/name,target)
        self.pdf=self.pdf.rename(self.output/'article.pdf')
        record=self.write_receipt()
        record.update(working_directory='main_parent',cwd=str(self.source/'manuscript'))
        (self.build/'build_record.json').write_text(json.dumps(record),encoding='utf-8')
        for explicit_root in [True,False]:
            with self.subTest(explicit_root=explicit_root):
                output=self.base/('nested_explicit.json' if explicit_root else 'nested_cwd.json')
                command=[sys.executable,'-B',str(Path(__file__).with_name('host_context.py')),
                         '--main',str(self.main),'--build-dir',str(self.build),'--output',str(output)]
                if explicit_root:command.extend(['--root',str(self.root)])
                result=subprocess.run(command,cwd=self.root,capture_output=True,text=True,check=False)
                self.assertEqual(result.returncode,0,result.stderr)
                payload=json.loads(output.read_text(encoding='utf-8'))
                self.assertEqual(payload['project_root'],str(self.root))
                self.assertEqual(payload['project_root_resolution'],'explicit' if explicit_root else 'working_directory')
                self.assertEqual(payload['selected_pdf'],str(self.pdf))
                self.assertEqual(payload['isolated_build_binding']['status'],'current')
                self.assertTrue(payload['pdf_visibility']['applicable_to_current_source'])

    def test_cli_refuses_existing_source_pdf_report_receipt_and_directory(self):
        report=self.base/'existing_report.json';report.write_bytes(b'Existing reviewed evidence')
        targets=[self.main,self.root/'body.tex',self.pdf,report,self.build/'build_record.json',self.root]
        for target in targets:
            with self.subTest(target=str(target)):
                before={p:p.read_bytes() for p in self.base.rglob('*') if p.is_file()}
                result=subprocess.run([sys.executable,'-B',str(Path(__file__).with_name('host_context.py')),
                    '--main',str(self.main),'--root',str(self.root),'--pdf',str(self.pdf),
                    '--output',str(target)],capture_output=True,text=True,check=False)
                self.assertEqual(result.returncode,2)
                self.assertIn(str(target),result.stderr)
                self.assertIn('Choose a new report output path',result.stderr)
                self.assertEqual(before,{p:p.read_bytes() for p in self.base.rglob('*') if p.is_file()})

    def test_exclusive_report_creation_preserves_file_created_after_precheck(self):
        target=self.base/'raced_report.json'
        before={p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        sentinel=b'Concurrent reviewed evidence'
        def finish_observation(*args,**kwargs):
            target.write_bytes(sentinel)
            return {'finding_counts':{},'source_map':{'objects':[],'warnings':[]}}
        error=io.StringIO()
        with patch('sys.argv',['host_context.py','--main',str(self.main),'--output',str(target)]), \
                patch('host_context.build_host_context',side_effect=finish_observation), \
                contextlib.redirect_stderr(error):
            with self.assertRaises(SystemExit) as stopped:
                host_context_main()
        self.assertEqual(stopped.exception.code,2)
        self.assertEqual(target.read_bytes(),sentinel)
        self.assertIn('Choose a new report output path',error.getvalue())
        self.assertEqual(before,{p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_cli_refuses_existing_output_links_including_dangling_link(self):
        link=self.base/'existing_link.json'
        original=self.main.read_bytes()
        missing=self.base/'missing_target.json'
        try:
            link.symlink_to(missing)
        except OSError as error:
            self.skipTest(f'Symlink creation unavailable: {error}')
        for target in [missing,self.main]:
            with self.subTest(target=str(target)):
                if link.is_symlink():link.unlink()
                link.symlink_to(target)
                result=subprocess.run([sys.executable,'-B',str(Path(__file__).with_name('host_context.py')),
                    '--main',str(self.main),'--root',str(self.root),'--output',str(link)],
                    capture_output=True,text=True,check=False)
                self.assertEqual(result.returncode,2,result.stderr)
                self.assertIn('Choose a new report output path',result.stderr)
                self.assertTrue(link.is_symlink())
                self.assertFalse(missing.exists())
                self.assertEqual(self.main.read_bytes(),original)

    def test_cli_missing_selected_build_artifacts_are_reserved_report_paths(self):
        for target in [self.pdf,self.output/'main.log',self.build/'build_record.json',
                       self.build/'stdout.txt',self.build/'stderr.txt']:
            with self.subTest(target=str(target)):
                original=target.read_bytes()
                target.unlink()
                before={p:p.read_bytes() for p in self.base.rglob('*') if p.is_file()}
                result=subprocess.run([sys.executable,'-B',str(Path(__file__).with_name('host_context.py')),
                    '--main',str(self.main),'--root',str(self.root),'--build-dir',str(self.build),
                    '--output',str(target)],capture_output=True,text=True,check=False)
                self.assertEqual(result.returncode,2,result.stderr)
                self.assertIn('reserved for source or a build artifact',result.stderr)
                self.assertFalse(target.exists())
                self.assertEqual(before,{p:p.read_bytes() for p in self.base.rglob('*') if p.is_file()})
                target.write_bytes(original)

    def test_cli_missing_explicit_or_neighbor_pdf_is_reserved(self):
        for explicit in [False,True]:
            with self.subTest(explicit=explicit):
                target=self.base/'missing_external.pdf' if explicit else self.main.with_suffix('.pdf')
                before={p:p.read_bytes() for p in self.base.rglob('*') if p.is_file()}
                command=[sys.executable,'-B',str(Path(__file__).with_name('host_context.py')),
                         '--main',str(self.main),'--root',str(self.root),'--output',str(target)]
                if explicit:command.extend(['--pdf',str(target)])
                result=subprocess.run(command,capture_output=True,text=True,check=False)
                self.assertEqual(result.returncode,2,result.stderr)
                self.assertIn('reserved for source or a build artifact',result.stderr)
                self.assertFalse(target.exists())
                self.assertEqual(before,{p:p.read_bytes() for p in self.base.rglob('*') if p.is_file()})

    def test_cli_missing_pdf_still_allows_readonly_new_report(self):
        self.pdf.unlink()
        output=self.base/'missing_pdf_observation.json'
        before={p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result=subprocess.run([sys.executable,'-B',str(Path(__file__).with_name('host_context.py')),
            '--main',str(self.main),'--root',str(self.root),'--build-dir',str(self.build),
            '--output',str(output)],capture_output=True,text=True,check=False)
        self.assertEqual(result.returncode,0,result.stderr)
        payload=json.loads(output.read_text(encoding='utf-8'))
        self.assertFalse(payload['pdf_visibility']['checked'])
        self.assertIsNone(payload['pdf_visibility']['applicable_to_current_source'])
        self.assertEqual(payload['isolated_build_binding']['status'],'unknown')
        self.assertFalse(self.pdf.exists())
        self.assertEqual(before,{p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_bound_cli_output_cannot_invalidate_its_ordinary_input_inventory(self):
        for target in [self.root/'new_report.json',self.source/'new_report.json',
                       self.root/'data'/'new_report.json',self.source/'data'/'new_report.json']:
            with self.subTest(target=str(target)):
                before={p:p.read_bytes() for p in self.base.rglob('*') if p.is_file()}
                result=subprocess.run([sys.executable,'-B',str(Path(__file__).with_name('host_context.py')),
                    '--main',str(self.main),'--root',str(self.root),'--build-dir',str(self.build),
                    '--output',str(target)],capture_output=True,text=True,check=False)
                self.assertEqual(result.returncode,2,result.stderr)
                self.assertIn('would add an isolated-build input',result.stderr)
                self.assertIn('.paperfit/reports/',result.stderr)
                self.assertFalse(target.exists())
                self.assertEqual(before,{p:p.read_bytes() for p in self.base.rglob('*') if p.is_file()})

    def test_bound_cli_new_hidden_report_does_not_change_verified_inputs(self):
        for tree in [self.root,self.source]:
            with self.subTest(tree=str(tree)):
                output=tree/'.paperfit'/'reports'/'new_report.json'
                result=subprocess.run([sys.executable,'-B',str(Path(__file__).with_name('host_context.py')),
                    '--main',str(self.main),'--root',str(self.root),'--build-dir',str(self.build),
                    '--output',str(output)],capture_output=True,text=True,check=False)
                self.assertEqual(result.returncode,0,result.stderr)
                payload=json.loads(output.read_text(encoding='utf-8'))
                self.assertEqual(payload['isolated_build_binding']['status'],'current')
                self.assertTrue(self.context(build_dir=self.build)['pdf_visibility']['applicable_to_current_source'])

    def test_external_clipping_is_observed_even_without_build_binding(self):
        clipped=self.base/'clipped.pdf';self.write_pdf(clipped,clip=True)
        result=self.context(pdf=clipped)
        vis=result['pdf_visibility']
        self.assertTrue(vis['checked'])
        self.assertEqual(vis['geometry_status'],'needs_review')
        self.assertFalse(vis['geometry_pass'])
        self.assertGreater(vis['unresolved_finding_count'],0)
        self.assertIsNone(vis['applicable_to_current_source'])
        self.assertFalse(vis['pass'])

    def test_changed_included_source_or_asset_retains_geometry_but_loses_binding(self):
        for name in ['body.tex','plot.dat','data/results.csv','data/parameters.json']:
            with self.subTest(name=name):
                original=(self.root/name).read_bytes()
                (self.root/name).write_bytes(original+b'changed')
                result=self.context(pdf=self.pdf,build_dir=self.build)
                self.assert_observed_unbound(result)
                self.assertEqual(result['isolated_build_binding']['reason'],'source_inventory_changed')
                (self.root/name).write_bytes(original)

    def test_changed_retained_snapshot_cannot_bind_original_pdf(self):
        (self.source/'body.tex').write_text('Changed copied source',encoding='utf-8')
        result=self.context(pdf=self.pdf,build_dir=self.build)
        self.assert_observed_unbound(result)
        self.assertEqual(result['isolated_build_binding']['reason'],'source_inventory_changed')

    def test_changed_pdf_bytes_lose_binding_despite_same_page_count(self):
        altered=self.output/'altered.pdf';self.write_pdf(altered)
        with fitz.open(altered) as doc:
            doc[0].insert_text((20,80),'Changed PDF body',fontsize=12)
            doc.save(self.output/'changed.pdf')
        (self.output/'changed.pdf').replace(self.pdf)
        result=self.context(pdf=self.pdf,build_dir=self.build)
        self.assert_observed_unbound(result)
        self.assertEqual(result['isolated_build_binding']['reason'],'pdf_changed')

    def test_identical_pdf_copy_is_observed_but_not_selected_build_output(self):
        copy=self.base/'copy.pdf';shutil.copyfile(self.pdf,copy)
        result=self.context(pdf=copy,build_dir=self.build)
        self.assert_observed_unbound(result)
        self.assertEqual(result['isolated_build_binding']['reason'],'selected_pdf_not_this_build')

    def test_missing_failed_or_tampered_receipt_does_not_hide_geometry(self):
        path=self.build/'build_record.json';original=path.read_bytes()
        for change in ['missing','failed','wrong_pdf_path']:
            with self.subTest(change=change):
                if change=='missing':path.unlink()
                else:
                    record=dict(self.record)
                    if change=='failed':record['compile_success']=False
                    else:record['pdf']={'path':'../../unrelated.pdf','sha256':sha(self.pdf)}
                    path.write_text(json.dumps(record),encoding='utf-8')
                result=self.context(pdf=self.pdf,build_dir=self.build)
                self.assert_observed_unbound(result)
                self.assertEqual(result['isolated_build_binding']['status'],'unknown')
                path.write_bytes(original)

    def test_verified_source_binding_does_not_accept_physical_clipping(self):
        replacement=self.output/'clipped.pdf';self.write_pdf(replacement,clip=True)
        replacement.replace(self.pdf);self.write_receipt()
        result=self.context(pdf=self.pdf,build_dir=self.build)
        self.assertTrue(result['pdf_visibility']['applicable_to_current_source'])
        self.assertEqual(result['pdf_visibility']['source_pdf_binding'],'verified_isolated_build')
        self.assertEqual(result['pdf_visibility']['status'],'needs_review')
        self.assertFalse(result['pdf_visibility']['pass'])
        self.assertGreater(result['pdf_visibility']['unresolved_finding_count'],0)
        self.assertTrue(all(f['scientific_damage_confirmed'] is False for f in result['pdf_visibility']['findings']))

    def test_invalid_external_pdf_never_becomes_empty_pass(self):
        self.pdf.write_bytes(b'not PDF')
        result=self.context(pdf=self.pdf)
        self.assertFalse(result['pdf_visibility']['checked'])
        self.assertFalse(result['pdf_visibility']['pass'])
        self.assertFalse(result['pdf_layout_profile']['checked'])
        self.assertIsNone(result['pdf_visibility']['applicable_to_current_source'])

    def test_external_review_paths_are_not_implicitly_trusted(self):
        external_review=self.base/'review.json'
        external_review.write_text(json.dumps({'schema':'paperfit_visibility_reviews.v1','pdf_sha256':sha(self.pdf),'reviews':[]}),encoding='utf-8')
        result=review_pdf_visibility(self.pdf,root=self.root,reviews_path=external_review,allow_external_pdf=True)
        self.assertEqual(result['review_report_status'],'missing_or_external')
        self.assertFalse(result['pass'])


if __name__=='__main__':
    unittest.main()
