"""Artifact-backed source-review integration, with synthetic offline receipts.

The PDF/images are real files; no native compiler/API runs or manuscript edits.
The collector, imported-input verification, classifier and policy are not mocked.
"""
import copy, os, tempfile, unittest
from pathlib import Path
from compile_result import source_fingerprint
from content_integrity_check import compute_content_diff
from scientific_integrity import scientific_payload_diff
from source_review import build_source_review_request, validate_source_review
from test_layout_policy_host_review import CurrentProject

class TemplateParameterSourceReviewTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.p=CurrentProject(self.root,'')
  self.old='\\documentclass{native}\n\\begin{document}\nResult 91.2. $x+y=2$.\n\\bibliography{references}\n\\end{document}\n'
  self.new=self.old.replace(r'\begin{document}',r'\renewcommand{\ReferenceGap}{4pt plus .5pt}'+'\n'+r'\begin{document}')
  self.template=self.root/'native.cls'
  self.template.write_text('\\LoadClass{article}\n\\def\\ReferenceGap{0pt plus .5pt}\n\\def\\thebibliography#1{\\itemsep\\ReferenceGap\\relax}\n',encoding='utf-8')
  self.refresh(self.new)
 def refresh(self,text):
  self.new=text;(self.root/'main.tex').write_text(text,encoding='utf-8')
  self.p.retime();os.utime(self.template,(1700000000,1700000000))
  fp=source_fingerprint(self.root,'main.tex')
  self.p.attempt.update(source_fingerprint=fp,source_fingerprint_before=dict(fp))
  self.p.record_inputs(['main.tex','references.bib','native.cls']);self.p.set_attempt(self.p.attempt)
 def request(self):
  return build_source_review_request(self.old,self.new,project_root=self.root,file='main.tex',main_tex='main.tex',page_images_dir='data/pages')
 def signed(self):
  request=self.request();self.assertEqual(request['binding_status'],'available')
  request.update(approved=True,source_reviewed=True,all_pages_inspected=True,content_preservation_confirmed=True,
                 template_checked=True,intended_object_checked=True,reason='Synthetic fixture host inspected native declaration, exact source delta and all current page images.')
  for evidence in request['template_parameter_evidence']:evidence['template_checked']=True
  return request
 def decision(self,request):
  return validate_source_review(self.old,self.new,project_root=self.root,file='main.tex',review=request)
 def diff(self,review=None):
  return compute_content_diff(self.old,self.new,file='main.tex',layout_only=True,project_root=self.root,source_review=review)
 def test_raw_failed_unsigned_or_no_review_remain_pending(self):
  raw=scientific_payload_diff(self.old,self.new)
  self.assertEqual(raw['status'],'failed');self.assertEqual(raw['assessment']['classification'],'manual_review')
  self.assertEqual(raw['template_parameter_uncertainty']['status'],'pending')
  request=self.request();self.assertFalse(request['approved']);self.assertEqual(request['binding_status'],'available')
  self.assertTrue(request['template_parameter_evidence']);self.assertFalse(request['template_parameter_evidence'][0]['template_checked'])
  for review in [None,request]:
   diff=self.diff(review);self.assertEqual(diff['action_required'],'manual_review')
   self.assertEqual(diff['scientific_payload']['status'],'manual_review')
   self.assertEqual(diff['scientific_payload']['automated_status'],'failed')
   self.assertEqual(diff['source_review']['status'],'manual_review')
 def test_residual_scientific_numbers_signs_and_claims_remain_hard_rejections(self):
  valid=self.new
  for text in [valid.replace('91.2','12'),valid.replace('x+y','x-y'),valid.replace('Result 91.2.','Result 91.2. Superior.')]:
   self.refresh(text);diff=self.diff()
   self.assertEqual(diff['action_required'],'auto_rollback')
   self.assertNotEqual(diff['source_review'].get('status'),'verified')
   self.assertNotIn('template_parameter_uncertainty',diff['scientific_payload'])
 def test_current_binding_without_typed_evidence_or_with_forged_binding_rejects(self):
  signed=self.signed()
  for attack in ['no_evidence','unchecked_evidence','wrong_hash','wrong_consumer','forged_pdf_binding','no_receipt']:
   review=copy.deepcopy(signed)
   if attack=='no_evidence':review['template_parameter_evidence']=[]
   elif attack=='unchecked_evidence':review['template_parameter_evidence'][0]['template_checked']=False
   elif attack=='wrong_hash':review['template_parameter_evidence'][0]['sha256']='0'*64
   elif attack=='wrong_consumer':review['template_parameter_evidence'][0]['consumer_line']=1
   elif attack=='forged_pdf_binding':review['binding']['pdf']['sha256']='0'*64
   else:
    old=copy.deepcopy(self.p.attempt);self.p.attempt.pop('compile_inputs');self.p.set_attempt(self.p.attempt)
   self.assertEqual(self.decision(review)['status'],'manual_review',attack)
   if attack=='no_receipt':self.p.attempt=old;self.p.set_attempt(old)
 def test_explicit_current_native_template_review_pass_keeps_raw_failure(self):
  diff=self.diff(self.signed());science=diff['scientific_payload']
  self.assertEqual(diff['source_review']['status'],'verified')
  self.assertEqual(science['status'],'pass');self.assertEqual(science['verification_method'],'explicit_host_review')
  self.assertEqual(science['automated_status'],'failed')
  self.assertEqual(science['automated_report']['raw_comparison']['status'],'failed')
  self.assertEqual(diff['action_required'],'none')
 def test_template_or_pages_change_and_stale_template_evidence_cannot_authorize(self):
  signed=self.signed();old=self.template.stat()
  self.template.write_text(self.template.read_text(encoding='utf-8')+'% Changed imported template\n',encoding='utf-8')
  os.utime(self.template,ns=(old.st_atime_ns,old.st_mtime_ns))
  self.assertEqual(self.decision(signed)['status'],'manual_review')
  self.refresh(self.new)
  fresh=self.signed();fresh['template_parameter_evidence']=signed['template_parameter_evidence']
  self.assertEqual(self.decision(fresh)['status'],'manual_review')
  fresh=self.signed();page=self.root/'data/pages/page_002.png';page.write_bytes(page.read_bytes()+b'changed')
  self.assertEqual(self.decision(fresh)['status'],'manual_review')
 def test_explicit_template_review_cannot_waive_body_font_policy(self):
  self.refresh(self.new.replace(r'\begin{document}',r'\begin{document}\fontsize{5pt}{6pt}\selectfont'))
  diff=self.diff(self.signed())
  self.assertEqual(diff['layout_policy']['status'],'blocked')
  self.assertEqual(diff['action_required'],'auto_rollback')

if __name__=='__main__':unittest.main()
