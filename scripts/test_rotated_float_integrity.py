"""Native starred rotation preserves payload without hiding changed results."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from content_integrity_check import compute_content_diff,has_scientific_regression,has_structure_regression

def doc(body):
    return '\\documentclass[twocolumn]{article}\n\\usepackage{rotating}\n\\begin{document}\nThe claim remains unchanged.\n'+body+'\n\\end{document}\n'

class NativeRotatedFloatTest(unittest.TestCase):
    table=r'\begin{table*}[t]\caption{Accuracy $a+b$}\label{tab:result}\begin{tabular}{lr}Method&Score\\A&91.2\\B&88.1\\\end{tabular}\end{table*}'
    figure=r'\begin{figure*}[t]\includegraphics[width=\linewidth]{unchanged.pdf}\caption{Error $a+b$}\label{fig:result}\end{figure*}'

    def assert_preserved(self,before,after):
        diff=compute_content_diff(doc(before),doc(after),layout_only=True)
        self.assertEqual(diff['scientific_payload']['status'],'pass',diff)
        self.assertEqual(diff['layout_prose']['status'],'pass',diff)
        self.assertEqual(diff['layout_policy']['status'],'pass',diff)
        self.assertFalse(has_scientific_regression(diff),diff)
        self.assertFalse(has_structure_regression(diff),diff)
        self.assertEqual(diff['structure_changes']['before'],diff['structure_changes']['after'],diff)
        self.assertIn(diff['action_required'],{'none','log_only'},diff)

    def test_native_double_column_rotated_table_keeps_caption_cells_and_math(self):
        rotated=self.table.replace('table*','sidewaystable*').replace('[t]','[p]')
        self.assert_preserved(self.table,rotated)

    def test_native_double_column_rotated_figure_keeps_asset_and_caption(self):
        self.assert_preserved(self.figure,self.figure.replace('figure*','sidewaysfigure*'))

    def test_rotated_double_column_floats_can_return_to_ordinary_containers(self):
        self.assert_preserved(self.table.replace('table*','sidewaystable*'),self.table)
        self.assert_preserved(self.figure.replace('figure*','sidewaysfigure*'),self.figure)

    def test_rotation_does_not_exempt_changed_numbers_signs_or_labels(self):
        rotated=self.table.replace('table*','sidewaystable*')
        for wrong in [rotated.replace('91.2','12.9'),rotated.replace('a+b','a-b'),rotated.replace(r'\label{tab:result}','')]:
            with self.subTest(candidate=wrong):
                diff=compute_content_diff(doc(self.table),doc(wrong),layout_only=True)
                self.assertTrue(has_scientific_regression(diff),diff)
                self.assertEqual(diff['scientific_payload']['status'],'failed',diff)
                self.assertEqual(diff['action_required'],'auto_rollback',diff)

    def test_rotation_does_not_exempt_changed_figure_assets_or_caption(self):
        rotated=self.figure.replace('figure*','sidewaysfigure*')
        for wrong in [rotated.replace('unchanged.pdf','another.pdf'),rotated.replace('Error','Accuracy'),rotated.replace(r'\label{fig:result}','')]:
            with self.subTest(candidate=wrong):
                diff=compute_content_diff(doc(self.figure),doc(wrong),layout_only=True)
                self.assertEqual(diff['action_required'],'auto_rollback',diff)

    def test_unknown_float_container_cannot_use_standard_rotation_recognition(self):
        unknown=self.table.replace('table*','customrotatedtable*')
        diff=compute_content_diff(doc(self.table),doc(unknown),layout_only=True)
        self.assertTrue(has_structure_regression(diff),diff)
        self.assertEqual(diff['action_required'],'auto_rollback',diff)

if __name__=='__main__':unittest.main()
