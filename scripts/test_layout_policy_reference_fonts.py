"""Native reference hooks remain separate from body font and scientific acceptance."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from layout_policy import compare_layout_policy, snapshot_source_edit_checks
from runtime_snapshots import create_pre_repair_snapshot

HOOK=r'\AddToHook{env/thebibliography/begin}{\small}'
BODY=r'The score is 91.2. The method does not change $x+y=1$.\bibliography{refs}'
def manuscript(preamble='', body=BODY):
    return '\\documentclass{article}\n'+preamble+'\n\\begin{document}\n'+body+'\n\\end{document}\n'

class ReferenceFontPolicyTest(unittest.TestCase):
    def test_native_literal_reference_hook_is_local_but_does_not_approve_body_font(self):
        before=manuscript()
        for target in ('env/thebibliography/begin','cmd/thebibliography/after'):
            for font in ('small','footnotesize','normalsize'):
                with self.subTest(font=font,target=target):
                    hook=HOOK.replace('small',font).replace('env/thebibliography/begin',target)
                    self.assertEqual(compare_layout_policy(before,manuscript(hook))['status'],'pass')
                    self.assertEqual(compare_layout_policy(before,manuscript(hook, r'\small '+BODY))['status'],'blocked')

    def test_other_hook_targets_do_not_gain_the_reference_exception(self):
        for target in ('begindocument','env/document/begin','env/thebibliography/after','env/itemize/begin','env/customrefs/begin','cmd/thebibliography/before','cmd/section/after'):
            with self.subTest(target=target):
                self.assertEqual(compare_layout_policy(manuscript(),manuscript(HOOK.replace('env/thebibliography/begin',target)))['status'],'blocked')

    def test_extra_code_or_optional_hook_label_is_not_a_finite_native_declaration(self):
        for hook in (
            HOOK.replace(r'\small',r'\small\input{extra}'),
            HOOK.replace(r'\small',r'\small\gdef\payload{Changed}'),
            HOOK.replace(r'\small',r'{\small}'),
            HOOK.replace(r'{\small}',r'[label]{\small}'),
            HOOK.replace(r'\small',r'\small\normalsize'),
        ):
            with self.subTest(hook=hook):
                self.assertNotEqual(compare_layout_policy(manuscript(),manuscript(hook))['status'],'pass')

    def test_body_registered_hook_and_fontsize_are_not_exempt(self):
        self.assertEqual(compare_layout_policy(manuscript(),manuscript('',HOOK+BODY))['status'],'blocked')
        for font in (r'\tiny',r'\scriptsize',r'\fontsize{4}{5}\selectfont',r'\fontsize{9}{11}\selectfont'):
            with self.subTest(font=font):
                self.assertEqual(compare_layout_policy(manuscript(),manuscript(HOOK.replace(r'\small',font)))['status'],'blocked')

    def test_dormant_and_literal_hooks_do_not_establish_scope_for_a_real_body_font(self):
        for inactive in ('% '+HOOK+'\n',r'\verb|'+HOOK+'|',r'\newcommand{\example}{'+HOOK+'}',r'\detokenize{'+HOOK+'}'):
            with self.subTest(inactive=inactive):
                self.assertEqual(compare_layout_policy(manuscript(),manuscript(inactive))['status'],'pass')
                self.assertEqual(compare_layout_policy(manuscript(),manuscript(inactive,r'\small '+BODY))['status'],'blocked')

    def test_shadowed_hook_font_or_bibliography_does_not_self_certify_scope(self):
        for definition in (
            r'\renewcommand{\AddToHook}[2]{#2}',
            r'\renewcommand{\small}{\fontsize{1}{1}\selectfont}',
            r'\renewenvironment{thebibliography}[1]{}{}',
        ):
            with self.subTest(definition=definition):
                before=manuscript(definition)
                self.assertNotEqual(compare_layout_policy(before,manuscript(definition+HOOK))['status'],'pass')

    def test_non_hook_lookalike_or_escaped_marker_is_not_reference_scope(self):
        for text in (r'\OtherAddToHook{env/thebibliography/begin}{\small}',r'\AddToHookSomething{env/thebibliography/begin}{\small}'):
            self.assertEqual(compare_layout_policy(manuscript(),manuscript(text))['status'],'blocked')
        self.assertEqual(compare_layout_policy(manuscript(),manuscript('\\'+HOOK))['status'],'blocked')

class SnapshotSourceReviewAdapterTest(unittest.TestCase):
    def fixture(self,directory):
        root=Path(directory)
        main=root/'main.tex'
        before=manuscript(r'\ifdefined\foo\relax\fi'+'\n'+r'\setlength\parindent{0pt}')
        main.write_text(before,encoding='utf-8')
        snapshot=create_pre_repair_snapshot(project_root=root,main_tex='main.tex',snapshot_id='source-review')
        main.write_text(manuscript(r'\ifdefined\foo\relax\fi'),encoding='utf-8')
        return root,main,snapshot,before

    def test_explicit_unique_review_reaches_source_validator_and_keeps_raw_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root,main,snapshot,before=self.fixture(directory)
            request={'file':'main.tex','approved':True}
            def validator(original,repaired,**kwargs):
                self.assertEqual(original,before)
                self.assertEqual(repaired,main.read_text(encoding='utf-8'))
                self.assertEqual(kwargs['project_root'],root)
                self.assertIs(kwargs['review'],request)
                raw=kwargs['scientific']
                self.assertEqual(raw['status'],'failed')
                return {**raw,'status':'pass','automated_report':raw},kwargs['prose'],{'checked':True,'status':'verified'}
            with mock.patch('source_review.apply_source_review',side_effect=validator) as apply:
                result=snapshot_source_edit_checks(root,snapshot['rollback_target'],source_reviews=[request])
            self.assertEqual(apply.call_count,1)
            self.assertEqual(result['scientific_payload']['status'],'pass')
            self.assertEqual(result['scientific_payload']['file_reports'][0]['automated_report']['status'],'failed')
            self.assertEqual(result['source_review_decisions'][0]['status'],'verified')

    def test_duplicate_review_requests_do_not_choose_an_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            root,_,snapshot,_=self.fixture(directory)
            request={'file':'main.tex','approved':True}
            from source_review import apply_source_review
            with mock.patch('source_review.apply_source_review', wraps=apply_source_review) as apply:
                result=snapshot_source_edit_checks(root,snapshot['rollback_target'],source_reviews=[request,request])
            self.assertEqual(apply.call_count,1)
            self.assertIsNone(apply.call_args.kwargs['review'])
            self.assertNotEqual(result['scientific_payload']['status'],'pass')
            self.assertEqual(result['source_review_decisions'][0]['status'],'manual_review')
            self.assertIn('multiple',result['source_review_decisions'][0]['reason'])

    def test_weak_wrong_file_review_does_not_approve_actual_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root,_,snapshot,_=self.fixture(directory)
            result=snapshot_source_edit_checks(root,snapshot['rollback_target'],source_reviews=[{'file':'unused.tex','approved':True}])
            self.assertNotEqual(result['scientific_payload']['status'],'pass')
            self.assertNotEqual(result['source_review_decisions'][0]['status'],'verified')

if __name__=='__main__':
    unittest.main()
