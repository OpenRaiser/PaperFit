"""Bounded presentation syntax and exact identified object equivalence."""
import unittest
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from content_integrity_check import compute_content_diff, has_structure_regression
from scientific_integrity import scientific_payload_diff, _strip_layout

def doc(body):
    return r'\documentclass{article}\begin{document}Body remains 91.2 exactly. '+body+r'\end{document}'

def table(rows=r'A&1\\B&2', label='tab:values', wrapper='table'):
    return (r'\begin{'+wrapper+r'}\centering\caption{Measured values}\label{'+label+
            r'}\begin{tabular}{cc}'+rows+r'\end{tabular}\end{'+wrapper+r'}')

class BoundedTableParameters(unittest.TestCase):
    def test_unrelated_local_literal_register_definition_does_not_poison_other_table(self):
        prefix=r'\begin{table}\renewcommand\tabcolsep{2pt}\begin{tabular}{c}X\end{tabular}\end{table}'
        before=doc(prefix+r'\begin{table}\setlength{\tabcolsep}{3pt}\begin{tabular}{c}4.2\end{tabular}\end{table}')
        after=before.replace(r'\tabcolsep}{3pt}',r'\tabcolsep}{4pt}')
        self.assertEqual(scientific_payload_diff(before,after)['status'],'pass')

    def test_unknown_or_global_definition_remains_protected(self):
        for declaration in (r'\renewcommand\tabcolsep{2pt}',
                            r'\begin{table}\renewcommand\tabcolsep{\unknown{2}}\end{table}',
                            r'\begin{table}\gdef\tabcolsep{2pt}\end{table}'):
            with self.subTest(declaration=declaration):
                before=doc(declaration+r'\setlength{\tabcolsep}{3pt}')
                after=before.replace(r'\tabcolsep}{3pt}',r'\tabcolsep}{4pt}')
                self.assertEqual(scientific_payload_diff(before,after)['status'],'failed')

    def test_standard_adjustbox_options_are_presentation(self):
        a=doc(table().replace(r'\begin{tabular}',r'\begin{adjustbox}{max width=1\linewidth,height=8pt}\begin{tabular}').replace(r'\end{tabular}',r'\end{tabular}\end{adjustbox}'))
        b=a.replace(r'max width=1\linewidth,height=8pt',r'max width=.8\linewidth,height=10pt')
        self.assertEqual(scientific_payload_diff(a,b)['status'],'pass')
        self.assertEqual(scientific_payload_diff(a,doc(table()))['status'],'pass')

    def test_unknown_adjustbox_options_are_not_ignored(self):
        for value in ('payload=91.2',r'width=\unknown{91.2}'):
            with self.subTest(value=value):
                a=doc(table().replace(r'\begin{tabular}',r'\begin{adjustbox}{'+value+r'}\begin{tabular}').replace(r'\end{tabular}',r'\end{tabular}\end{adjustbox}'))
                b=a.replace('91.2','91.3')
                self.assertEqual(scientific_payload_diff(a,b)['status'],'failed')

    def test_standard_fontsize_and_following_scientific_number(self):
        a=doc(r'\begin{table}\fontsize{8}{9.5}\selectfont\begin{tabular}{cc}A&91.2\end{tabular}\end{table}')
        self.assertEqual(scientific_payload_diff(a,a.replace('{8}{9.5}','{9pt}{11pt}'))['status'],'pass')
        self.assertEqual(scientific_payload_diff(a,a.replace('A&91.2','A&91.3'))['status'],'failed')

    def test_unknown_or_shadowed_font_arguments_remain_protected(self):
        for value in (r'\fontsize{\unknown{8}}{9.5}',r'\renewcommand{\fontsize}[2]{#1#2}\fontsize{8}{9.5}'):
            a=doc(r'\begin{table}'+value+r'\begin{tabular}{c}A\end{tabular}\end{table}')
            self.assertEqual(scientific_payload_diff(a,a.replace('{8}','{9}'))['status'],'failed')

    def test_reader_and_unknown_prefix_still_protect_the_length(self):
        for prefix in (r'\the ',r'\unknown ',r'\unknown{arg} '):
            a=doc(prefix+r'\itemsep=91.2pt')
            b=a.replace('=91.2pt','=91.3pt')
            self.assertEqual(scientific_payload_diff(a,b)['status'],'failed')

    def test_body_font_and_unreadable_table_remain_independent_policy_blocks(self):
        for body in (r'\small Body remains 91.2 exactly.',
                     r'\begin{table}\fontsize{1}{1}\selectfont\begin{tabular}{c}A\end{tabular}\end{table}'):
            result=compute_content_diff(doc(''),doc(body),layout_only=True)
            self.assertEqual(result['layout_policy']['status'],'blocked',result)
            self.assertEqual(result['action_required'],'auto_rollback',result)

class IdentifiedObjectProof(unittest.TestCase):
    def assert_proven(self,before,after):
        result=compute_content_diff(doc(before),doc(after),layout_only=True)
        science=result['scientific_payload']
        self.assertEqual(science['status'],'pass',result)
        self.assertEqual(science['raw_comparison']['status'],'failed',result)
        self.assertEqual(science['layout_equivalence']['status'],'proven',result)
        self.assertEqual(result['layout_prose']['status'],'pass',result)
        self.assertFalse(has_structure_regression(result),result)
        self.assertNotEqual(result['action_required'],'auto_rollback',result)
        return result

    def assert_not_proven(self,before,after):
        result=compute_content_diff(doc(before),doc(after),layout_only=True)
        self.assertNotEqual(result['scientific_payload'].get('layout_equivalence',{}).get('status'),'proven',result)
        self.assertEqual(result['action_required'],'auto_rollback',result)

    def test_table_unwrapped_to_identical_caption_label_and_ordered_cells(self):
        bare=r'\caption{Measured values}\centering\label{tab:values}\begin{tabular}{cc}A&1\\B&2\end{tabular}'
        result=self.assert_proven(table(),bare)
        obj=result['scientific_payload']['layout_equivalence']['objects'][0]
        self.assertEqual(obj['label'],'tab:values')
        self.assertEqual(len(obj['payload_sha256']),64)

    def test_equation_migration_into_standard_float(self):
        equation=r'\begin{equation}\label{eq:values}y=x_2+91.2\end{equation}'
        self.assert_proven(equation,r'\begin{figure*}\centering'+equation+r'\end{figure*}')

    def test_changed_math_number_or_operator_is_not_equivalent(self):
        equation=r'\begin{equation}\label{eq:values}y=x_2+91.2\end{equation}'
        for changed in (equation.replace('91.2','91.3'),equation.replace('+','-')):
            self.assert_not_proven(equation,r'\begin{figure*}'+changed+r'\end{figure*}')

    def test_reordered_rows_or_cells_or_changed_caption_rejected(self):
        for rows,caption in [(r'B&2\\A&1','Measured values'),(r'A&2\\B&1','Measured values'),(r'A&1\\B&2','Other values')]:
            bare=r'\caption{'+caption+r'}\label{tab:values}\begin{tabular}{cc}'+rows+r'\end{tabular}'
            self.assert_not_proven(table(),bare)

    def test_duplicate_or_missing_labels_never_bind(self):
        equation=r'\begin{equation}\label{eq:values}x=1\end{equation}'
        self.assert_not_proven(equation+r'\label{eq:values}',r'\begin{figure*}'+equation+r'\end{figure*}\label{eq:values}')
        self.assert_not_proven(equation.replace(r'\label{eq:values}',''),r'\begin{figure*}'+equation.replace(r'\label{eq:values}','')+r'\end{figure*}')

    def test_swapping_payloads_between_identified_tables_rejected(self):
        before=table(r'A&1',label='tab:a')+table(r'B&2',label='tab:b')
        after=table(r'B&2',label='tab:a',wrapper='sidewaystable')+table(r'A&1',label='tab:b')
        self.assert_not_proven(before,after)

    def test_unknown_macro_conditional_and_include_require_review(self):
        for prefix,equation in [
            ('',r'\begin{equation}\label{eq:values}x=\unknown{1}\end{equation}'),
            (r'\newcommand{\custom}[1]{#1}',r'\begin{equation}\label{eq:values}x=\custom{1}\end{equation}'),
            (r'\iftrue A\fi',r'\begin{equation}\label{eq:values}x=1\end{equation}'),
            (r'\input{header}',r'\begin{equation}\label{eq:values}x=1\end{equation}')]:
            self.assert_not_proven(prefix+equation,prefix+r'\begin{figure*}'+equation+r'\end{figure*}')

    def test_unbound_body_number_and_prose_are_not_waived(self):
        equation=r'\begin{equation}\label{eq:values}x=1\end{equation}'
        self.assert_not_proven('Observed 91.2. '+equation,'Observed 91.3. '+r'\begin{figure*}'+equation+r'\end{figure*}')
        result=compute_content_diff(doc('Claim stays. '+equation),doc('Claim removed. '+r'\begin{figure*}'+equation+r'\end{figure*}'),layout_only=True)
        # An exact scientific object proof cannot waive independent body prose.
        self.assertEqual(result['scientific_payload']['layout_equivalence']['status'],'proven')
        self.assertEqual(result['layout_prose']['status'],'failed')
        self.assertEqual(result['action_required'],'auto_rollback')

    def test_label_formula_binding_ref_and_cell_footnote_remain_protected(self):
        equation=r'\begin{equation}\label{eq:values}x=1\end{equation}\ref{eq:values}'
        self.assert_not_proven(equation,r'\begin{figure*}'+equation.replace('eq:values','eq:other')+r'\end{figure*}')
        before=table(r'A&1\footnote{Exact 91.2}')
        after=r'\caption{Measured values}\label{tab:values}\begin{tabular}{cc}A&1\footnote{Exact 91.3}\end{tabular}'
        self.assert_not_proven(before,after)

    def test_adjacent_unknown_reader_cannot_consume_the_object_token(self):
        equation=r'\begin{equation}\label{eq:values}x=1\end{equation}'
        for reader in (r'\the ',r'\unknown '):
            self.assert_not_proven(reader+equation,reader+r'\begin{figure*}'+equation+r'\end{figure*}')

    def test_unchanged_outside_unknown_macro_keeps_opaque_residual(self):
        equation=r'\begin{equation}\label{eq:values}x=1\end{equation}'
        outside=r'\customheading{Title 91.2} Ordinary text. '
        self.assert_proven(outside+equation,outside+r'\begin{figure*}'+equation+r'\end{figure*}')
        self.assert_not_proven(outside+equation,outside.replace('91.2','91.3')+r'\begin{figure*}'+equation+r'\end{figure*}')

    def test_table_to_figure_counter_family_is_not_a_legal_proof(self):
        self.assert_not_proven(table(),table(wrapper='figure'))

    def test_redefined_environment_and_unused_macro_cannot_supply_proof(self):
        equation=r'\begin{equation}\label{eq:values}x=1\end{equation}'
        prefix=r'\newenvironment{figure}{\begingroup}{\endgroup}'
        self.assert_not_proven(prefix+equation,prefix+r'\begin{figure}'+equation+r'\end{figure}')
        before=r'\newcommand{\hidden}{'+equation+'}'
        after=r'\newcommand{\hidden}{}'+r'\begin{figure*}'+equation+r'\end{figure*}'
        self.assert_not_proven(before,after)
        self.assert_not_proven(r'\unknown{'+equation+'}',r'\unknown{}'+r'\begin{figure*}'+equation+r'\end{figure*}')

    def test_actual_diff_cli_emits_raw_failure_and_proof_and_rejects_drift(self):
        before=doc(table())
        after=doc(r'\caption{Measured values}\label{tab:values}\begin{tabular}{cc}A&1\\B&2\end{tabular}')
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);a=root/'before.tex';b=root/'after.tex';out=root/'diff.json'
            a.write_text(before,encoding='utf-8')
            for content,code in [(after,0),(after.replace('B&2','B&3'),3)]:
                b.write_text(content,encoding='utf-8')
                result=subprocess.run([sys.executable,'-B',str(Path(__file__).with_name('content_integrity_check.py')),
                                       'diff',str(a),str(b),'--output',str(out)],
                                      capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                self.assertEqual(result.returncode,code,result.stderr)
                report=json.loads(out.read_text(encoding='utf-8'))
                if code==0:
                    self.assertEqual(report['scientific_payload']['raw_comparison']['status'],'failed')
                    self.assertEqual(report['scientific_payload']['layout_equivalence']['status'],'proven')
                else:
                    self.assertEqual(report['action_required'],'auto_rollback')

    def test_unknown_environment_cannot_supply_an_equivalence(self):
        equation=r'\begin{equation}\label{eq:values}x=1\end{equation}'
        self.assert_not_proven(equation,r'\begin{figure*}\begin{mystery}'+equation+r'\end{mystery}\end{figure*}')

if __name__=='__main__':
    unittest.main()
