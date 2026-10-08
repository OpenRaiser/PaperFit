"""Native starred rotation preserves payload without hiding changed results."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from content_integrity_check import compute_content_diff,has_scientific_regression

def doc(body):
    return '\\documentclass[twocolumn]{article}\n\\usepackage{rotating}\n\\begin{document}\nThe claim remains unchanged.\n'+body+'\n\\end{document}\n'

class NativeRotatedFloatTest(unittest.TestCase):
    table=r'\begin{table*}[t]\caption{Accuracy $a+b$}\label{tab:result}\begin{tabular}{lr}Method&Score\\A&91.2\\B&88.1\\\end{tabular}\end{table*}'
    figure=r'\begin{figure*}[t]\includegraphics[width=\linewidth]{unchanged.pdf}\caption{Error $a+b$}\label{fig:result}\end{figure*}'

    def assert_preserved(self,before,after):
        diff=compute_content_diff(doc(before),doc(after))
        self.assertEqual(diff['scientific_payload']['status'],'pass',diff)
        self.assertEqual(diff['layout_prose']['status'],'pass',diff)
        self.assertFalse(has_scientific_regression(diff),diff)

    def test_native_double_column_rotated_table_keeps_caption_cells_and_math(self):
        rotated=self.table.replace('table*','sidewaystable*').replace('[t]','[p]')
        self.assert_preserved(self.table,rotated)

    def test_native_double_column_rotated_figure_keeps_asset_and_caption(self):
        self.assert_preserved(self.figure,self.figure.replace('figure*','sidewaysfigure*'))

    def test_rotation_does_not_exempt_changed_numbers_signs_or_labels(self):
        rotated=self.table.replace('table*','sidewaystable*')
        for wrong in [rotated.replace('91.2','12.9'),rotated.replace('a+b','a-b'),rotated.replace(r'\label{tab:result}','')]:
            with self.subTest(candidate=wrong):
                diff=compute_content_diff(doc(self.table),doc(wrong))
                self.assertTrue(has_scientific_regression(diff),diff)
                self.assertEqual(diff['scientific_payload']['status'],'failed',diff)

if __name__=='__main__':unittest.main()
