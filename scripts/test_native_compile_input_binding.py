"""Offline same-attempt compiler checks; every compiler and counter is mocked."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paperfit_command
import runtime_actions
from compile_inputs import bind_compile_inputs, live_compile_inputs, snapshot_project_inputs


PATHS = [
    (runtime_actions, 'compile_latex', 'run_command', 'pdf_page_count', False),
    (runtime_actions, 'compile_latex_bounded', 'run_command', 'pdf_page_count', True),
    (paperfit_command, '_compile', '_run', '_pdf_page_count', False),
    (paperfit_command, '_compile_bounded', '_run', '_pdf_page_count', True),
]


class NativeCompileInputBindingTest(unittest.TestCase):
    def test_save_as_rejects_self_and_nested_destinations_without_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve() / 'original'
            root.mkdir()
            self.fixture(root)
            (root / 'outputs').mkdir()
            before = {path.relative_to(root):path.read_bytes() for path in root.rglob('*') if path.is_file()}
            for destination in [root, root/'candidate', root/'outputs'/'candidate',
                                root/'outputs'/'..'/'outputs'/'candidate']:
                with self.subTest(destination=str(destination)), self.assertRaisesRegex(ValueError, 'outside the source project'):
                    paperfit_command._copy_project(root, destination)
                self.assertEqual(before, {path.relative_to(root):path.read_bytes() for path in root.rglob('*') if path.is_file()})
                self.assertEqual(sorted(path.relative_to(root).as_posix() for path in root.rglob('*') if path.is_dir()), ['data','outputs'])

    def test_save_as_preserves_author_bibliography_and_scientific_data_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder).resolve()
            original, copied = directory / 'original', directory / 'copied'
            original.mkdir()
            main = self.fixture(original, nested=True)
            (main.parent / 'data' / 'metadata.json').write_bytes(b'{"count":42,"note":"exact scientific input"}\n')
            main.with_suffix('.aux').write_bytes(b'Generated auxiliary fixture')
            expected = {path.relative_to(original):path.read_bytes()
                        for path in original.rglob('*') if path.is_file() and path.suffix != '.aux'}
            self.assertEqual(paperfit_command._copy_project(original, copied), copied)
            actual = {path.relative_to(copied):path.read_bytes()
                      for path in copied.rglob('*') if path.is_file()}
            self.assertEqual(actual, expected)
            self.assertEqual(main.with_suffix('.bbl').read_bytes(), expected[main.with_suffix('.bbl').relative_to(original)])
            self.assertFalse((copied / 'manuscript' / 'paper.aux').exists())

    def fixture(self, root: Path, *, bibliography=True, nested=False):
        directory=root/'manuscript' if nested else root
        directory.mkdir(parents=True,exist_ok=True)
        main=directory/'paper.tex'
        main.write_text(r'\documentclass{article}\begin{document}\input{body}\input{paper.bbl}\end{document}',encoding='utf-8')
        (directory/'body.tex').write_text('Exact scientific claim and value 42.',encoding='utf-8')
        (directory/'figure.png').write_bytes(b'Original graphic input fixture')
        (directory/'data').mkdir()
        (directory/'data'/'results.csv').write_bytes(b'method,value\nA,42\n')
        if bibliography:
            main.with_suffix('.bbl').write_text(r'\bibitem{a}Author supplied reference 42.',encoding='utf-8')
        return main

    def run_compile(self, root, main, entry, *, engine='pdflatex', mutation=None,
                    recorder=True, generated_bbl=False, returncode=0, stdout=None):
        module,function,runner,counter,bounded=entry
        calls=[]
        original_bbl=main.with_suffix('.bbl').read_bytes() if main.with_suffix('.bbl').is_file() else None
        def run(command,**kwargs):
            calls.append({'command':command,'cwd':str(kwargs['cwd'])})
            if original_bbl is not None and mutation is None:
                self.assertEqual(main.with_suffix('.bbl').read_bytes(),original_bbl)
            main.with_suffix('.pdf').write_bytes(b'Synthetic PDF from pre-mutation input')
            if generated_bbl:
                main.with_suffix('.bbl').write_text(r'\bibitem{a}Generated reference fixture.',encoding='utf-8')
            if recorder:
                names=['paper.tex','body.tex','figure.png','data/results.csv']
                if main.with_suffix('.bbl').is_file():names.append('paper.bbl')
                if (main.parent/'refs.bib').is_file():names.append('refs.bib')
                main.with_suffix('.fls').write_text('PWD '+str(main.parent)+'\n'+''.join('INPUT '+name+'\n' for name in names),encoding='utf-8')
            if mutation is not None and len(calls)==(3 if bounded else 1):
                target=main.parent/mutation
                target.write_bytes(target.read_bytes()+b'Changed scientific input 99')
            return subprocess.CompletedProcess(command,returncode,stdout or 'Output written on paper.pdf (1 page).','TIMEOUT' if returncode==124 else '')
        with patch.dict(os.environ,{'PAPERFIT_BOUNDED_COMPILE':'0','PAPERFIT_TEX_ENGINE':'xelatex'}), \
                patch.object(module,runner,side_effect=run), \
                patch.object(module,counter,return_value=1), \
                patch.object(module.shutil,'which',side_effect=lambda name:name):
            result=getattr(module,function)(root,main_tex=main,engine=engine,**({'timeout_sec':60} if bounded else {}))
        return result,calls

    def test_initial_manual_bbl_is_bound_and_never_removed_before_compile(self):
        for entry in PATHS:
            with self.subTest(path=entry[1]),tempfile.TemporaryDirectory() as folder:
                root=Path(folder).resolve();main=self.fixture(root)
                original=main.with_suffix('.bbl').read_bytes()
                result,_=self.run_compile(root,main,entry)
                self.assertTrue(result['compile_process_success'])
                self.assertTrue(result['success'])
                self.assertTrue(result['current_source_compile_success'])
                self.assertTrue(result['compile_inputs']['bound'])
                self.assertIn('paper.bbl',result['compile_inputs']['before'])
                self.assertEqual(result['compile_inputs']['before']['paper.bbl'],result['compile_inputs']['after']['paper.bbl'])
                self.assertEqual(main.with_suffix('.bbl').read_bytes(),original)
                self.assertIsNone(result['preview_pdf_path'])

    def test_source_include_bbl_graphic_and_csv_mutations_never_report_current_success(self):
        for entry in PATHS:
            for engine in ['pdflatex','xelatex','lualatex']:
                for mutation in ['paper.tex','body.tex','paper.bbl','figure.png','data/results.csv']:
                    with self.subTest(path=entry[1],engine=engine,mutation=mutation),tempfile.TemporaryDirectory() as folder:
                        root=Path(folder).resolve();main=self.fixture(root)
                        result,_=self.run_compile(root,main,entry,engine=engine,mutation=mutation)
                        self.assertTrue(result['compile_process_success'])
                        self.assertFalse(result['success'])
                        self.assertFalse(result['current_source_compile_success'])
                        self.assertEqual(result['source_pdf_binding'],'unverified')
                        self.assertIsNone(result['pdf_path'])
                        self.assertEqual(result['preview_pdf_path'],str(main.with_suffix('.pdf')))
                        journal=json.loads((root/'data/compile/journal.jsonl').read_text(encoding='utf-8').splitlines()[-1])
                        self.assertFalse(journal['success'])
                        self.assertFalse(journal['current_source_compile_success'])
                        if mutation in ['paper.tex','body.tex']:
                            self.assertFalse(result['source_unchanged_during_compile'])
                        else:
                            self.assertTrue(result['imported_inputs_changed_during_compile'])

    def test_new_generated_bbl_is_transparent_unknown_observation_not_process_failure(self):
        for entry in PATHS:
            with self.subTest(path=entry[1]),tempfile.TemporaryDirectory() as folder:
                root=Path(folder).resolve();main=self.fixture(root,bibliography=False)
                main.write_text(r'\documentclass{article}\begin{document}\input{body}\bibliography{refs}\end{document}',encoding='utf-8')
                (root/'refs.bib').write_text('@article{a,title={Exact reference}}',encoding='utf-8')
                result,_=self.run_compile(root,main,entry,generated_bbl=True)
                self.assertTrue(result['compile_process_success'])
                self.assertTrue(result['success'])  # Physical compile can be rendered.
                self.assertFalse(result['current_source_compile_success'])
                self.assertFalse(result['compile_inputs']['bound'])
                self.assertIsNone(result['compile_inputs']['before']['paper.bbl'])
                self.assertIn('paper.bbl',result['compile_inputs']['missing_prebuild_inputs'])
                generated=result['compile_inputs']['generated_bibliography'][0]
                self.assertEqual(generated['path'],'paper.bbl')
                self.assertEqual(generated['derivation_status'],'unverified')
                self.assertTrue(generated['source_databases'][0]['unchanged'])
                self.assertEqual(result['preview_pdf_path'],str(main.with_suffix('.pdf')))
                self.assertEqual(result['source_pdf_binding'],'unverified')
                # A second actual attempt now has the bibliography as a real
                # pre-build input; no invented initial prehash is needed.
                stable,_=self.run_compile(root,main,entry)
                self.assertTrue(stable['compile_inputs']['bound'])

    def test_missing_recorder_keeps_observation_without_current_binding(self):
        for entry in PATHS:
            with self.subTest(path=entry[1]),tempfile.TemporaryDirectory() as folder:
                root=Path(folder).resolve();main=self.fixture(root)
                result,_=self.run_compile(root,main,entry,recorder=False)
                self.assertTrue(result['compile_process_success'])
                self.assertTrue(result['success'])
                self.assertFalse(result['current_source_compile_success'])
                self.assertFalse(result['compile_inputs']['bound'])
                self.assertIn('missing_current_recorder',result['compile_inputs']['unavailable'])
                self.assertEqual(result['preview_pdf_path'],str(main.with_suffix('.pdf')))

    def test_all_engines_record_actual_command_and_nested_working_directory(self):
        for entry in PATHS:
            for engine in ['pdflatex','xelatex','lualatex']:
                with self.subTest(path=entry[1],engine=engine),tempfile.TemporaryDirectory() as folder:
                    root=Path(folder).resolve();main=self.fixture(root,nested=True)
                    result,calls=self.run_compile(root,main,entry,engine=engine)
                    self.assertTrue(result['current_source_compile_success'])
                    self.assertEqual(result['engine'],engine)
                    self.assertEqual(result['cwd'],str(main.parent))
                    self.assertTrue(all(row['cwd']==str(main.parent) for row in calls))
                    self.assertTrue(all('-recorder' in row['command'] for row in calls))
                    self.assertIn('manuscript/paper.bbl',result['compile_inputs']['after'])
                    self.assertEqual(result['main_tex'],'manuscript/paper.tex')
                    if entry[-1]:self.assertTrue(all(row['command'][0]==engine for row in calls))
                    else:self.assertIn({'pdflatex':'-pdf','xelatex':'-xelatex','lualatex':'-lualatex'}[engine],calls[0]['command'])

    def test_timeout_preview_is_failed_in_all_native_paths(self):
        for entry in PATHS:
            with self.subTest(path=entry[1]),tempfile.TemporaryDirectory() as folder:
                root=Path(folder).resolve();main=self.fixture(root)
                result,_=self.run_compile(root,main,entry,returncode=124)
                self.assertFalse(result['compile_process_success'])
                self.assertFalse(result['success'])
                self.assertFalse(result['current_source_compile_success'])
                self.assertTrue(result['timeout'])
                self.assertIsNone(result['pdf_path'])
                self.assertEqual(result['preview_pdf_path'],str(main.with_suffix('.pdf')))

    def test_raw_stdout_is_retained_beyond_compact_report_tail(self):
        message='EARLY_COMPILER_EVIDENCE\n'+'x'*5000+'\nOutput written on paper.pdf (1 page).'
        for entry in PATHS:
            with self.subTest(path=entry[1]),tempfile.TemporaryDirectory() as folder:
                root=Path(folder).resolve();main=self.fixture(root)
                result,calls=self.run_compile(root,main,entry,stdout=message)
                archived=(root/result['stdout_path']).read_text(encoding='utf-8')
                self.assertEqual(archived.count('EARLY_COMPILER_EVIDENCE'),len(calls))
                self.assertNotIn('EARLY_COMPILER_EVIDENCE',result['stdout_tail'])
                self.assertTrue(result['current_source_compile_success'])

    def test_bbl_mutation_is_visible_to_live_recorder_binding(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();main=self.fixture(root)
            before=snapshot_project_inputs(root,main)
            main.with_suffix('.fls').write_text('PWD '+str(root)+'\nINPUT paper.tex\nINPUT paper.bbl\n',encoding='utf-8')
            main.with_suffix('.bbl').write_bytes(b'Changed author supplied reference')
            self.assertIn('paper.bbl',live_compile_inputs(root,main)['files'])
            self.assertFalse(bind_compile_inputs(root,main,before)['bound'])


if __name__=='__main__':unittest.main()
