import unittest
from scientific_integrity import scientific_payload_diff
class MathFontEnvironmentReflowTest(unittest.TestCase):
 def source(self,formula):
  return r'\documentclass{article}\begin{document}\begin{equation}\begin{small}'+formula+r'\end{small}\end{equation}\end{document}'
 def test_standard_font_environment_does_not_turn_alignment_into_science(self):
  a=self.source(r'x_2+17=y+19')
  b=self.source(r'\begin{aligned}x_2+17&=y\\&+19\end{aligned}')
  self.assertEqual(scientific_payload_diff(a,b)['status'],'pass')
 def test_changed_sign_or_number_inside_font_environment_is_rejected(self):
  a=self.source(r'x_2+17=y+19')
  for b in [self.source(r'\begin{aligned}x_2+17&=y\\&-19\end{aligned}'),self.source(r'x_2+18=y+19')]:
   self.assertEqual(scientific_payload_diff(a,b)['status'],'failed')
 def test_unknown_and_redefined_font_or_alignment_environment_remain_opaque(self):
  a=self.source(r'x_2+17=y+19')
  b=self.source(r'\begin{aligned}x_2+17&=y\\&+19\end{aligned}')
  for prefix in [r'\renewenvironment{small}{Data 42}{}',r'\renewenvironment{aligned}{Data 42}{}']:
   self.assertEqual(scientific_payload_diff(prefix+a,prefix+b)['status'],'failed')
  self.assertEqual(scientific_payload_diff(a.replace('{small}','{unknown}'),b.replace('{small}','{unknown}'))['status'],'failed')
if __name__=='__main__':unittest.main()
