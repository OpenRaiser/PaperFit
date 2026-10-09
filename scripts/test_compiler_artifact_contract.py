from __future__ import annotations
import hashlib,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parent))
import paperfit_command,runtime_actions
from tex_engine import resolve_tex_engine

class CompilerArtifactContractTest(unittest.TestCase):
    def test_utf8_bom_does_not_hide_original_engine_comment(self):
        with tempfile.TemporaryDirectory() as folder:
            main=Path(folder)/'paper.tex'
            main.write_text('% !TeX program = xelatex\nBody',encoding='utf-8-sig')
            with mock.patch.dict(os.environ,{},clear=True):
                self.assertEqual(resolve_tex_engine(main),'xelatex')

    def test_engine_precedence_and_invalid_command_are_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            main=Path(folder)/'paper.tex';main.write_text('% !TeX program = xelatex\nBody',encoding='utf-8')
            with mock.patch.dict(os.environ,{},clear=True):
                self.assertEqual(resolve_tex_engine(main),'xelatex')
                self.assertEqual(resolve_tex_engine(main,'lualatex'),'lualatex')
                with mock.patch.dict(os.environ,{'PAPERFIT_TEX_ENGINE':'pdflatex'}):
                    self.assertEqual(resolve_tex_engine(main),'pdflatex')
                for value in ['tectonic','pdflatex --shell-escape','xelatex; echo bad']:
                    with self.assertRaises(ValueError):resolve_tex_engine(main,value)

    def test_each_component_uses_selected_engine_in_standard_and_bounded_modes(self):
        for module,standard,bounded,runner,counter in [
            (runtime_actions,'compile_latex','compile_latex_bounded','run_command','pdf_page_count'),
            (paperfit_command,'_compile','_compile_bounded','_run','_pdf_page_count')]:
            for engine in ['xelatex','lualatex']:
                for use_bounded in [False,True]:
                    with self.subTest(component=module.__name__,engine=engine,bounded=use_bounded),tempfile.TemporaryDirectory() as folder:
                        root=Path(folder);main=root/'paper.tex';main.write_text('\\documentclass{article}',encoding='utf-8')
                        commands=[]
                        def run(command,**kwargs):
                            commands.append(command);main.with_suffix('.pdf').write_bytes(b'PDF preview')
                            return subprocess.CompletedProcess(command,0,'Output written on paper.pdf (1 page).','')
                        with mock.patch.dict(os.environ,{'PAPERFIT_TEX_ENGINE':engine,'PAPERFIT_BOUNDED_COMPILE':'0'}),mock.patch.object(module,runner,side_effect=run),mock.patch.object(module,counter,return_value=1),mock.patch.object(module.shutil,'which',side_effect=lambda name:name):
                            result=getattr(module,bounded)(root,main_tex=main,timeout_sec=60) if use_bounded else getattr(module,standard)(root,main_tex=main)
                        self.assertTrue(result['success'])
                        self.assertTrue(all(c[0]==engine for c in commands) if use_bounded else ('-'+engine in commands[0]))

    def test_legacy_cli_timeout_with_readable_preview_remains_failed(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);main=root/'paper.tex';main.write_text('\\documentclass{article}',encoding='utf-8')
            def run(command,**kwargs):
                main.with_suffix('.pdf').write_bytes(b'Partial preview')
                return subprocess.CompletedProcess(command,124,'Output written on paper.pdf (1 page).','TIMEOUT')
            with mock.patch.dict(os.environ,{'PAPERFIT_BOUNDED_COMPILE':'0','PAPERFIT_TEX_ENGINE':'pdflatex'}),mock.patch.object(paperfit_command,'_run',side_effect=run):
                result=paperfit_command._compile(root,main_tex=main)
            self.assertFalse(result['success'])
            self.assertTrue(result['partial_pdf_available'])
            self.assertIsNone(result['pdf_path'])
            self.assertEqual(result['preview_pdf_path'],str(main.with_suffix('.pdf')))

    def test_compile_time_source_mutation_never_binds_preview_to_later_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);main=root/'paper.tex';main.write_text('\\documentclass{article}',encoding='utf-8')
            def run(command,**kwargs):
                if command[0]=='pdfinfo':return subprocess.CompletedProcess(command,0,'Pages: 1','')
                main.with_suffix('.pdf').write_bytes(b'Preview built from earlier source')
                main.write_text('\\documentclass{article}\nNew result 99',encoding='utf-8')
                return subprocess.CompletedProcess(command,0,'Output written on paper.pdf (1 page).','')
            with mock.patch.dict(os.environ,{'PAPERFIT_BOUNDED_COMPILE':'0','PAPERFIT_TEX_ENGINE':'pdflatex'}),mock.patch.object(runtime_actions,'run_command',side_effect=run):
                result=runtime_actions.compile_latex(root,main_tex=main)
            self.assertFalse(result['success'])
            self.assertFalse(result['source_unchanged_during_compile'])
            self.assertNotEqual(result['source_fingerprint_before'],result['source_fingerprint'])
            journal=json.loads((root/'data/compile/journal.jsonl').read_text(encoding='utf-8').splitlines()[-1])
            self.assertFalse(journal['success'])
            self.assertEqual(journal['source_fingerprint'],result['source_fingerprint'])

if __name__=='__main__':unittest.main()
