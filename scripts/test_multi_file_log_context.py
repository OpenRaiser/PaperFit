from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from multi_file_overfull import find_file_in_project, resolve_overfull_to_source


class TeXFileContextTest(unittest.TestCase):
    def test_inline_nested_files_and_display_equation(self):
        log = r'''(./main.tex (./settings.sty)
[1] (./sections/first.tex
Package example Warning: text (ordinary diagnostic)
Overfull \hbox (12.0pt too wide) detected at line 6
[] equation

) (./sections/second.tex
Overfull \hbox (13.0pt too wide) in paragraph at lines 6--7
[] text

)
Overfull \hbox (8.0pt too wide) in paragraph at lines 10--11
'''
        results = resolve_overfull_to_source(log, Path('.'))
        self.assertEqual([item['file'] for item in results],
                         ['./sections/first.tex', './sections/second.tex', './main.tex'])
        self.assertEqual([(item['line_start'], item['line_end']) for item in results],
                         [(6, 6), (6, 7), (10, 11)])

    def test_quoted_filename_with_spaces(self):
        log = '("./sections/experiment results.tex"\n' + r'Overfull \hbox (9.0pt too wide) at lines 4--5'
        results = resolve_overfull_to_source(log, Path('.'))
        self.assertEqual(results[0]['file'], './sections/experiment results.tex')

    def test_box_dump_glyphs_do_not_change_source_context(self):
        # Reduced from a real MiKTeX compile: the child paragraph starts with
        # an unmatched visible '('; TeX repeats it in the warning's box dump.
        log = r'''(main.tex
(sections/child.tex
Overfull \hbox (1018.89204pt too wide) in paragraph at lines 1--1
\OT1/cmr/m/n/10 (ChildWWWWWWWWWWWWWWWWWWWW
 []

)
Overfull \hbox (471.94495pt too wide) in paragraph at lines 4--4
\OT1/cmr/m/n/10 MainZZZZZZZZZZZZ
 []

'''
        results = resolve_overfull_to_source(log, Path('.'))
        self.assertEqual([item['file'] for item in results],
                         ['sections/child.tex', 'main.tex'])

    def test_underfull_dump_does_not_close_child_or_open_fake_file(self):
        log = r'''(main.tex (sections/child.tex
Underfull \hbox (badness 10000) in paragraph at lines 1--2
\OT1/cmr/m/n/10 ) (fake.tex
 []

Overfull \hbox (9.0pt too wide) detected at line 3
 []

)
Overfull \hbox (4.0pt too wide) at lines 5--5
'''
        results = resolve_overfull_to_source(log, Path('.'))
        self.assertEqual([item['file'] for item in results],
                         ['sections/child.tex', 'main.tex'])

    def test_wrapped_standalone_close_glyph_is_not_a_file_close(self):
        # Actual TeX wraps ')' after 63 W glyphs onto its own dump line.
        log = r'''(main.tex (sections/child.tex
Overfull \hbox (306.3904pt too wide) in paragraph at lines 1--1
\OT1/cmr/m/n/10 WWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWW
)
 []

Overfull \hbox (732.5025pt too wide) in paragraph at lines 2--2
\OT1/cmr/m/n/10 ChildAgainWWWWWWWWWWWWWWWWWWWWWWWWWWWW
 []

)
Overfull \hbox (288.61151pt too wide) in paragraph at lines 4--4
'''
        results = resolve_overfull_to_source(log, Path('.'))
        self.assertEqual([item['file'] for item in results],
                         ['sections/child.tex', 'sections/child.tex', 'main.tex'])

    def test_ambiguous_basename_and_outside_project_are_unresolved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for folder in ['a', 'b']:
                (root / folder).mkdir()
                (root / folder / 'section.tex').write_text('content', encoding='utf-8')
            self.assertIsNone(find_file_in_project(root, 'section.tex'))
            self.assertEqual(find_file_in_project(root, 'a/section.tex'), root / 'a/section.tex')
            self.assertIsNone(find_file_in_project(root, '../section.tex'))


if __name__ == '__main__':
    unittest.main()
