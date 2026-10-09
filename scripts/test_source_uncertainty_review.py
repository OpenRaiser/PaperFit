from __future__ import annotations
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from source_review import (build_source_review_request, validate_source_review, reviewable_uncertainty,
                           empty_table_skeleton_uncertainty, load_source_review)
from content_integrity_check import compute_content_diff
from scientific_integrity import reference_font_hooks

class SourceUncertaintyReviewTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name)
  self.old=r'\begin{document}See \ref{fig:a}. \begin{figure}\caption{Result 17.}\end{figure}\end{document}'
  self.new=self.old.replace(r'\caption{Result 17.}',r'\caption{Result 17.}\label{fig:a}')
  (self.root/'main.tex').write_text(self.new,encoding='utf-8')
  self.live={'status':'available','blocking_checks':[],'binding':{'source_set':['bound'],'pdf':{'sha256':'pdf'},'imported_inputs':{'files':[]},'page_images':{'aggregate_sha256':'pages'}}}
  self.patch=patch('source_review.collect_layout_review_binding',side_effect=lambda *a,**k:copy.deepcopy(self.live))
  self.patch.start();self.addCleanup(self.patch.stop)
  # These tests explicitly mock the binding service. Actual byte barriers are
  # exercised independently with real fixture files and native-style receipts.
  barrier=patch('source_review.verify_layout_review_binding_current')
  barrier.start();self.addCleanup(barrier.stop)
 def request(self):
  return build_source_review_request(self.old,self.new,project_root=self.root,file='main.tex',main_tex='main.tex',page_images_dir='data/pages')
 def approved(self):
  r=self.request()
  r.update(approved=True,source_reviewed=True,all_pages_inspected=True,content_preservation_confirmed=True,template_checked=True,intended_object_checked=True,reason='Checked the intended Figure and its references in all current pages.')
  return r
 def test_unsigned_request_never_authorizes(self):
  result=validate_source_review(self.old,self.new,project_root=self.root,file='main.tex',review=self.request())
  self.assertEqual(result['status'],'manual_review')
 def test_bound_review_retains_raw_automated_failure(self):
  diff=compute_content_diff(self.old,self.new,file='main.tex',layout_only=True,project_root=self.root,source_review=self.approved())
  self.assertEqual(diff['source_review']['status'],'verified')
  science=diff['scientific_payload']
  self.assertEqual(science['status'],'pass')
  self.assertEqual(science['verification_method'],'explicit_host_review')
  self.assertEqual(science['automated_status'],'failed')
  self.assertEqual(science['automated_report']['raw_comparison']['status'],'failed')
  self.assertEqual(diff['action_required'],'none')
 def test_baseline_source_page_pdf_import_or_compile_change_invalidates(self):
  review=self.approved()
  for field in ['source_set','pdf','page_images','imported_inputs','compile_evidence']:
   original=copy.deepcopy(self.live)
   self.live['binding'][field]={'changed':True}
   self.assertEqual(validate_source_review(self.old,self.new,project_root=self.root,file='main.tex',review=review)['status'],'manual_review')
   self.live=original
  self.assertEqual(validate_source_review(self.old+'!',self.new,project_root=self.root,file='main.tex',review=review)['status'],'manual_review')
 def test_partial_visual_review_or_bare_boolean_is_insufficient(self):
  for field in ['source_reviewed','all_pages_inspected','content_preservation_confirmed','template_checked','intended_object_checked']:
   r=self.approved();r[field]=False
   self.assertNotEqual(validate_source_review(self.old,self.new,project_root=self.root,file='main.tex',review=r)['status'],'verified')
  r=self.approved();r['approved']='true'
  self.assertNotEqual(validate_source_review(self.old,self.new,project_root=self.root,file='main.tex',review=r)['status'],'verified')
 def test_current_numeric_change_and_removed_caption_stay_rejected(self):
  review=self.approved()
  for new in [self.new.replace('17','18'),self.new.replace('Result 17.','')]:
   (self.root/'main.tex').write_text(new,encoding='utf-8')
   diff=compute_content_diff(self.old,new,file='main.tex',layout_only=True,project_root=self.root,source_review=review)
   self.assertEqual(diff['action_required'],'auto_rollback')
 def test_finite_length_requires_actual_compiled_declaration_excerpt(self):
  self.old=r'\opaque\addtolength{\textheight}{-8\baselineskip}\begin{document}Result 17.\end{document}'
  self.new=self.old.replace(r'\addtolength{\textheight}{-8\baselineskip}','')
  (self.root/'main.tex').write_text(self.new,encoding='utf-8')
  review=self.approved()
  self.assertEqual(validate_source_review(self.old,self.new,project_root=self.root,file='main.tex',review=review)['status'],'manual_review')
 def test_missing_binding_or_outside_file_stays_pending(self):
  review=self.approved();self.live['status']='unavailable'
  self.assertEqual(validate_source_review(self.old,self.new,project_root=self.root,file='main.tex',review=review)['status'],'manual_review')
  self.assertEqual(validate_source_review(self.old,self.new,project_root=self.root,file='../main.tex',review=review)['status'],'manual_review')
 def test_duplicate_json_key_rejected(self):
  p=self.root/'review.json';p.write_text('{"schema_version":"paperfit.source_uncertainty_review.v1","approved":false,"approved":true}',encoding='utf-8')
  with self.assertRaises(ValueError):load_source_review(p)

class NarrowSyntaxReviewTest(unittest.TestCase):
 def test_empty_unmatched_skeleton_is_pending_not_autopass(self):
  old=r'\documentclass{article}\begin{document}Before 17.'+'\n'+r'\begin{tabular}{cc}'+'\n% disabled cell 42\n'+r'}}'+'\nAfter 19.\\end{document}'
  new=old.replace(r'\begin{tabular}{cc}',r'% \begin{tabular}{cc}').replace('\n}}\n','\n% }}\n')
  c=reviewable_uncertainty(old,new)
  self.assertTrue(c['eligible'])
  self.assertEqual(c['scientific_payload']['status'],'pass')
  self.assertNotEqual(c['layout_prose']['status'],'pass')
  self.assertIn('empty_table_skeleton_uncertainty',c['kinds'])
 def test_nonempty_matched_redefined_or_unrelated_deletion_is_ineligible(self):
  for old,new in [
   (r'\begin{tabular}{cc}17&19\\}}',r''),
   (r'\begin{tabular}{cc}}\end{tabular}',r''),
   (r'\renewenvironment{tabular}[1]{Data #1}{}\begin{tabular}{cc}}',r'\renewenvironment{tabular}[1]{Data #1}{}'),
   (r'Before 17.\begin{tabular}{cc}}After 19.',r'Before 17.')]:
   self.assertNotEqual(empty_table_skeleton_uncertainty(old,new)['status'],'pending')
 def test_bibliography_hook_is_finite_preamble_and_cannot_mask_content_change(self):
  old=r'\documentclass{article}\begin{document}Result 17.\end{document}'
  hook=r'\AddToHook{env/thebibliography/begin}{\small}'
  new=old.replace(r'\begin{document}',hook+r'\begin{document}')
  self.assertEqual(len(reference_font_hooks(new)),1)
  self.assertTrue(reviewable_uncertainty(old,new)['eligible'])
  self.assertFalse(reviewable_uncertainty(old,new.replace('17','18'))['eligible'])
  for invalid in [r'\AddToHook{env/figure/begin}{\small}',r'\AddToHook{env/thebibliography/begin}{\small Hidden}',r'\AddToHook{env/thebibliography/begin}{\tiny}',r'\renewcommand{\AddToHook}[2]{#2}'+hook]:
   self.assertFalse(reference_font_hooks(old.replace(r'\begin{document}',invalid+r'\begin{document}')))
if __name__=='__main__':unittest.main()
