import unittest
from scientific_integrity import scientific_payload_diff, combined_layout_uncertainty
from content_integrity_check import compute_content_diff
class CombinedUncertaintyTest(unittest.TestCase):
 def setUp(self):
  self.old=r'\documentclass{article}\opaque\addtolength{\textheight}{-8\baselineskip}\begin{document}See \ref{fig:a}. Result 17. \begin{equation}x+19=y\end{equation}\begin{figure}\caption{Measured result 42.}\end{figure}\end{document}'
  self.new=self.old.replace(r'\addtolength{\textheight}{-8\baselineskip}','').replace(r'\caption{Measured result 42.}',r'\caption{Measured result 42.}\label{fig:a}')
 def test_two_and_three_families_are_pending_with_raw_changes_retained(self):
  for new in [self.new,self.new.replace(r'\documentclass{article}',r'\documentclass{article}\AddToHook{env/thebibliography/begin}{\small}')]:
   raw=scientific_payload_diff(self.old,new)
   self.assertEqual(raw['status'],'failed')
   self.assertEqual(raw['assessment']['classification'],'manual_review')
   self.assertEqual(raw['combined_layout_uncertainty']['status'],'pending')
   diff=compute_content_diff(self.old,new,layout_only=True)
   self.assertEqual(diff['action_required'],'manual_review')
   self.assertEqual(diff['scientific_payload']['status'],'manual_review')
   self.assertEqual(diff['scientific_payload']['automated_report']['status'],'failed')
 def test_combo_cannot_mask_numeric_sign_caption_prose_or_reference_changes(self):
  for new in [self.new.replace('Result 17.','Result 18.'),self.new.replace('x+19','x-19'),
              self.new.replace('Measured result 42.','Measured result 43.'),
              self.new.replace('Result 17.',''),self.new.replace(r'\ref{fig:a}',r'\ref{other}')]:
   self.assertNotEqual(combined_layout_uncertainty(self.old,new)['status'],'pending')
   self.assertEqual(compute_content_diff(self.old,new,layout_only=True)['action_required'],'auto_rollback')
 def test_invented_duplicate_or_removed_labels_are_ineligible(self):
  for new in [self.new.replace(r'\label{fig:a}',r'\label{other}'),self.new.replace(r'\label{fig:a}',r'\label{fig:a}\label{fig:a}')]:
   self.assertNotEqual(combined_layout_uncertainty(self.old,new)['status'],'pending')
  old=self.old.replace(r'\caption{Measured result 42.}',r'\caption{Measured result 42.}\label{existing}')
  self.assertNotEqual(combined_layout_uncertainty(old,self.new)['status'],'pending')
 def test_body_font_policy_and_shadowed_controls_are_never_waived(self):
  new=self.new.replace(r'\begin{document}',r'\begin{document}\fontsize{5pt}{6pt}\selectfont')
  self.assertEqual(compute_content_diff(self.old,new,layout_only=True)['action_required'],'auto_rollback')
  old=r'\renewcommand{\addtolength}[2]{#2}'+self.old
  self.assertNotEqual(combined_layout_uncertainty(old,r'\renewcommand{\addtolength}[2]{#2}'+self.new)['status'],'pending')
if __name__=='__main__':unittest.main()
