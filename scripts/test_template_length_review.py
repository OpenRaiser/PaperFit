import unittest
from scientific_integrity import scientific_payload_diff
from content_integrity_check import compute_content_diff
class FiniteTemplateLengthReviewTest(unittest.TestCase):
 def test_standard_margins_are_presentation_not_scientific_values(self):
  before=r'\documentclass{article}\begin{document}A result is 17.\end{document}'
  for name in ['topmargin','marginparsep','oddsidemargin','headsep','footskip']:
   after=before.replace(r'\begin{document}',r'\setlength{\%s}{-12pt}\begin{document}'%name)
   diff=compute_content_diff(before,after,layout_only=True)
   self.assertEqual(diff['scientific_payload']['status'],'pass',name)
   self.assertEqual(diff['layout_prose']['status'],'pass',name)
 def test_opaque_prefix_length_change_is_pending_never_pass(self):
  before=r'\opaque\addtolength{\textheight}{-8\baselineskip}\begin{document}Result 17.\end{document}'
  after=before.replace(r'\addtolength{\textheight}{-8\baselineskip}','')
  diff=compute_content_diff(before,after,layout_only=True)
  self.assertEqual(diff['scientific_payload']['status'],'manual_review')
  self.assertEqual(diff['scientific_payload']['automated_report']['status'],'failed')
  self.assertEqual(diff['scientific_payload']['raw_comparison']['status'],'failed')
  self.assertEqual(diff['scientific_payload']['assessment']['classification'],'manual_review')
  self.assertEqual(diff['action_required'],'manual_review')
 def test_actual_numeric_loss_and_macro_shadow_are_still_rejected(self):
  old=r'\opaque\addtolength{\textheight}{-8\baselineskip}\begin{document}Result 17.\end{document}'
  for after in [old.replace('17','18'),old.replace('Result 17.','')]:
   self.assertEqual(compute_content_diff(old,after,layout_only=True)['action_required'],'auto_rollback')
  old=r'\renewcommand{\topmargin}{17}\begin{document}\setlength{\topmargin}{12pt}Result 17.\end{document}'
  after=old.replace(r'\setlength{\topmargin}{12pt}','')
  self.assertEqual(compute_content_diff(old,after,layout_only=True)['action_required'],'auto_rollback')
 def test_standard_double_column_and_bibliography_glue_is_finite(self):
  before=r'\begin{document}Result 17.\end{document}'
  for name in ['dbltextfloatsep','dblfloatsep','bibsep']:
   after=before.replace(r'\begin{document}',r'\setlength{\%s}{3pt plus 1pt minus 1pt}\begin{document}'%name)
   diff=compute_content_diff(before,after,layout_only=True)
   self.assertEqual(diff['scientific_payload']['status'],'pass',name)
   self.assertEqual(diff['layout_prose']['status'],'pass',name)
   unsafe=r'\renewcommand{\%s}{17}'%name+after
   self.assertNotEqual(scientific_payload_diff(unsafe,unsafe.replace(r'\setlength{\%s}{3pt plus 1pt minus 1pt}'%name,''))['status'],'pass')
 def test_dynamic_dimension_is_not_covered_by_pending_exception(self):
  old=r'\opaque\setlength{\textheight}{\reader{17}}\begin{document}Result 17.\end{document}'
  after=old.replace(r'\setlength{\textheight}{\reader{17}}','')
  self.assertNotEqual(scientific_payload_diff(old,after).get('layout_argument_uncertainty',{}).get('status'),'pending')
if __name__=='__main__':unittest.main()
