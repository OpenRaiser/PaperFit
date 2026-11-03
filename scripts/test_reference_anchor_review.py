import unittest
from scientific_integrity import scientific_payload_diff
from content_integrity_check import compute_content_diff
class MissingReferenceAnchorReviewTest(unittest.TestCase):
 def setUp(self):
  self.old=r'\begin{document}See \ref{fig:a}. \begin{figure}\includegraphics{a.pdf}\caption{Result 17.}% \label{fig:a}'+'\n'+r'\end{figure}\end{document}'
 def test_missing_referenced_anchor_is_pending_not_automatically_preserved(self):
  new=self.old.replace(r'\caption{Result 17.}',r'\caption{Result 17.}\label{fig:a}')
  diff=compute_content_diff(self.old,new,layout_only=True)
  self.assertEqual(diff['scientific_payload']['status'],'manual_review')
  self.assertEqual(diff['scientific_payload']['automated_report']['status'],'failed')
  self.assertEqual(diff['scientific_payload']['reference_anchor_uncertainty']['status'],'pending')
  self.assertEqual(diff['scientific_payload']['assessment']['classification'],'manual_review')
  self.assertEqual(diff['action_required'],'manual_review')
 def test_unreferenced_or_duplicate_label_cannot_use_review_exception(self):
  for inserted in [r'\label{other}',r'\label{fig:a}\label{fig:a}']:
   new=self.old.replace(r'\caption{Result 17.}',r'\caption{Result 17.}'+inserted)
   self.assertNotEqual(scientific_payload_diff(self.old,new)['reference_anchor_uncertainty']['status'],'pending')
 def test_label_recovery_cannot_mask_scientific_loss(self):
  new=self.old.replace(r'\caption{Result 17.}',r'\caption{Result 18.}\label{fig:a}')
  self.assertEqual(compute_content_diff(self.old,new,layout_only=True)['action_required'],'auto_rollback')
 def test_removed_or_renamed_existing_anchor_is_rejected(self):
  old=self.old.replace(r'\caption{Result 17.}',r'\caption{Result 17.}\label{old}')
  for new in [old.replace(r'\label{old}',r'\label{fig:a}'),old.replace(r'\label{old}',r'\label{old}\label{fig:a}').replace('See','Hide')]:
   self.assertNotEqual(scientific_payload_diff(old,new)['reference_anchor_uncertainty']['status'],'pending')
 def test_redefined_label_and_unknown_reader_are_not_auto_pass(self):
  old=r'\renewcommand{\label}[1]{Changed #1}'+self.old
  new=old.replace(r'\caption{Result 17.}',r'\caption{Result 17.}\label{fig:a}')
  self.assertNotEqual(scientific_payload_diff(old,new)['reference_anchor_uncertainty']['status'],'pending')
if __name__=='__main__':unittest.main()
