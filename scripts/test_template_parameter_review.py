"""Offline, source-level attacks; no compiler, API or automatic host approval."""
import hashlib,tempfile,unittest
from pathlib import Path
from template_parameter_review import classify_template_glue_override, suggest_evidence, verify_template_glue_evidence

class TemplateParameterReviewTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.before=r'\documentclass{native}\begin{document}Accuracy is 91.2. $x+y=2$\end{document}'
  self.override=r'\renewcommand{\ReferenceGap}{4pt plus .5pt}'
  self.after=self.before.replace(r'\begin{document}',self.override+r'\begin{document}')
  self.native='\\def\\ReferenceGap{0pt plus .5pt}\n\\def\\thebibliography#1{\\itemsep\\ReferenceGap\\relax}\n'
 def fixture(self,main=None,native=None):
  (self.root/'main.tex').write_text(main or self.after,encoding='utf-8')
  (self.root/'native.cls').write_text(native or self.native,encoding='utf-8')
  return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.iterdir() if p.is_file()}
 def checked(self,files,classification):
  evidence=suggest_evidence(self.root,files,classification)
  self.assertTrue(evidence)
  self.assertFalse(evidence[0]['template_checked'])
  evidence[0]['template_checked']=True
  return evidence
 def test_arbitrary_api_added_or_changed_only_pending(self):
  for name in ['ReferenceGap','NovelTemplateSpace']:
   original=self.before
   candidate=self.after.replace('ReferenceGap',name)
   result=classify_template_glue_override(original,candidate)
   self.assertEqual(result['status'],'pending');self.assertEqual(result['macros'],[name])
   self.assertEqual(result['residual_science']['status'],'pass')
   self.assertEqual(result['layout_prose']['status'],'pass');self.assertTrue(result['requires_host_review'])
   changed=classify_template_glue_override(candidate,candidate.replace('4pt plus .5pt','5pt minus .2pt'))
   self.assertEqual(changed['status'],'pending')
 def test_numeric_math_claim_and_alias_changes_cannot_be_classified(self):
  for candidate in [self.after.replace('91.2','12'),self.after.replace('x+y','x-y'),
                    self.after.replace('Accuracy is','Accuracy is certainly'),
                    self.after+r'\let\CopiedGap\ReferenceGap']:
   self.assertEqual(classify_template_glue_override(self.before,candidate)['status'],'not_applicable')
 def test_nonfinite_parameterized_negative_zero_or_shadowed_override_rejected(self):
  for operation in [r'\renewcommand{\ReferenceGap}{0pt}',r'\renewcommand{\ReferenceGap}{-2pt}',
                    r'\renewcommand{\ReferenceGap}{4pt plus -1pt}',r'\renewcommand{\ReferenceGap}{4fil}',
                    r'\renewcommand{\ReferenceGap}[1]{4pt}',r'\renewcommand{\ReferenceGap}{\unknown}',
                    r'\def\renewcommand#1#2{#2}\renewcommand{\ReferenceGap}{4pt}']:
   result=classify_template_glue_override(self.before,self.before.replace(r'\begin{document}',operation+r'\begin{document}'))
   self.assertEqual(result['status'],'not_applicable',operation)
 def test_inactive_definition_and_literal_cannot_create_pending(self):
  for wrapper in ['% '+self.override+'\n',r'\newcommand{\unused}{'+self.override+'}',
                  r'\verb|'+self.override+'|',r'\detokenize{'+self.override+'}']:
   self.assertEqual(classify_template_glue_override(self.before,wrapper+self.before)['status'],'not_applicable',wrapper)
 def test_actual_imported_template_direct_assignment_and_setlength_verified(self):
  classification=classify_template_glue_override(self.before,self.after)
  for consumer in [r'\itemsep\ReferenceGap\relax',r'\setlength{\itemsep}{\ReferenceGap}']:
   native='\\def\\ReferenceGap{0pt plus .5pt}\n'+consumer+'\n'
   files=self.fixture(native=native);evidence=self.checked(files,classification)
   proof=verify_template_glue_evidence(self.root,files,classification,evidence)
   self.assertEqual(proof['status'],'verified');self.assertEqual(proof['verification_method'],'explicit_host_review')
 def test_stale_unimported_false_confirmation_and_missing_definition_fail_closed(self):
  classification=classify_template_glue_override(self.before,self.after)
  files=self.fixture();evidence=self.checked(files,classification)
  false=[dict(evidence[0],template_checked=False)]
  wrong=[dict(evidence[0],sha256='0'*64)]
  for bad in [false,wrong,[],evidence+evidence]:
   with self.assertRaises(ValueError):verify_template_glue_evidence(self.root,files,classification,bad)
  with self.assertRaises(ValueError):verify_template_glue_evidence(self.root,{'main.tex':files['main.tex']},classification,evidence)
  (self.root/'native.cls').write_text(self.native+'% changed\n',encoding='utf-8')
  with self.assertRaises(ValueError):verify_template_glue_evidence(self.root,files,classification,evidence)
  files=self.fixture(native='\\itemsep\\ReferenceGap\\relax\n')
  self.assertEqual(suggest_evidence(self.root,files,classification),[])
 def test_literal_body_math_caption_alias_and_register_shadow_are_not_layout_api(self):
  classification=classify_template_glue_override(self.before,self.after)
  attacks=[r'\ReferenceGap',r'$x=\ReferenceGap$',r'\caption{Accuracy \ReferenceGap}',
           r'\let\Alias\ReferenceGap',r'\def\Alias{\ReferenceGap}',r'\def\itemsep{7}']
  for attack in attacks:
   files=self.fixture(main=self.after+attack);evidence=self.checked(files,classification)
   with self.assertRaises(ValueError,msg=attack):verify_template_glue_evidence(self.root,files,classification,evidence)
  # An actually imported bibliography can print the macro as a scientific literal too.
  self.fixture()
  (self.root/'ref.bib').write_text(r'@article{x, title={Accuracy \ReferenceGap}}',encoding='utf-8')
  files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.iterdir() if p.is_file()}
  evidence=self.checked(files,classification)
  with self.assertRaises(ValueError):verify_template_glue_evidence(self.root,files,classification,evidence)
  # A retained imported .bbl is executed TeX too, independently of its .bib.
  (self.root/'ref.bib').unlink()
  (self.root/'main.bbl').write_text(r'\begin{thebibliography}{1}\bibitem{r}Accuracy \ReferenceGap\end{thebibliography}',encoding='utf-8')
  files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.iterdir() if p.is_file()}
  evidence=self.checked(files,classification)
  with self.assertRaises(ValueError):verify_template_glue_evidence(self.root,files,classification,evidence)
 def test_native_bounded_conditional_glue_and_exact_current_override(self):
  classification=classify_template_glue_override(self.before,self.after)
  native=self.native+r'\nativeconfonly{\def\ReferenceGap{0.5\baselineskip plus .25\baselineskip minus .25\baselineskip}}'
  files=self.fixture(native=native);evidence=self.checked(files,classification)
  self.assertEqual(verify_template_glue_evidence(self.root,files,classification,evidence)['status'],'verified')
  files=self.fixture(main=self.after.replace('4pt plus .5pt','5pt'))
  evidence=self.checked(files,classification)
  with self.assertRaises(ValueError):verify_template_glue_evidence(self.root,files,classification,evidence)

 def test_constructed_or_dynamic_usage_is_not_every_use_evidence(self):
  classification=classify_template_glue_override(self.before,self.after)
  attacks=[r'Visible \csname ReferenceGap\endcsname.',
           r'\def\GapName{ReferenceGap}\csname\GapName\endcsname',
           r'\def\outer{\csname ReferenceGap\endcsname}\outer',
           r'\expandafter\def\csname renewcommand\endcsname#1#2{#2}',
           r'\expandafter\def\csname itemsep\endcsname{7}',
           r'\csname Reference^^47ap\endcsname']
  for attack in attacks:
   files=self.fixture(main=self.after+attack);evidence=self.checked(files,classification)
   with self.assertRaises(ValueError,msg=attack):verify_template_glue_evidence(self.root,files,classification,evidence)
  files=self.fixture(native=self.native+r'\csname unrelatedliteral\endcsname')
  self.assertEqual(verify_template_glue_evidence(self.root,files,classification,self.checked(files,classification))['status'],'verified')

 def test_nested_reader_and_register_shadow_cannot_be_verified(self):
  for target in ['renewcommand','setlength','itemsep']:
   attack='\\def\\outer{\\def\\'+target+'#1#2{#2}}\\outer\n'
   old=attack+self.before;new=attack+self.after
   classification=classify_template_glue_override(old,new)
   if target=='renewcommand':
    self.assertNotEqual(classification['status'],'pending')
   else:
    self.assertEqual(classification['status'],'pending')
    files=self.fixture(main=new);evidence=self.checked(files,classification)
    with self.assertRaises(ValueError):verify_template_glue_evidence(self.root,files,classification,evidence)

 def test_inert_reader_examples_do_not_shadow_literal_template_api(self):
  classification=classify_template_glue_override(self.before,self.after)
  for example in [r'\detokenize{\def\renewcommand#1#2{#2}}',
                  r'\verb|\def\setlength#1#2{#2}|',
                  '% '+r'\csname ReferenceGap\endcsname'+'\n']:
   files=self.fixture(main=self.after+example);evidence=self.checked(files,classification)
   self.assertEqual(verify_template_glue_evidence(self.root,files,classification,evidence)['status'],'verified')

 def test_active_character_rewriting_cannot_establish_every_use_evidence(self):
  classification=classify_template_glue_override(self.before,self.after)
  for attack in [r'Visible \^^52eferenceGap.', r'\def\^^69temsep{7}',
                 r'\def\outer{\^^52eferenceGap}\outer']:
   files=self.fixture(main=self.after+attack);evidence=self.checked(files,classification)
   with self.assertRaisesRegex(ValueError,'constructed or dynamic'):
    verify_template_glue_evidence(self.root,files,classification,evidence)
  for inert in ['% '+r'\^^52eferenceGap'+'\n', r'\verb|\^^52eferenceGap|',
                r'\detokenize{\^^52eferenceGap}']:
   files=self.fixture(main=self.after+inert);evidence=self.checked(files,classification)
   self.assertEqual(verify_template_glue_evidence(self.root,files,classification,evidence)['status'],'verified')

if __name__=='__main__':unittest.main()
