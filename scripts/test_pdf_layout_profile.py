"""Actual PDF observations, unknown measurements and output-preservation checks."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import fitz
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pdf_layout_profile import profile_pdf_layout
from candidate_compare import compare_candidates
from host_context import build_host_context


class LayoutProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def make(self, name, size=10, rotate=0, text=True):
        path = self.root / name
        with fitz.open() as doc:
            page = doc.new_page(width=400, height=500)
            if text:
                page.insert_text((40, 80), 'A paragraph has many ordinary words.', fontsize=size)
                page.insert_text((40, 92), 'Its next line also has ordinary words.', fontsize=size)
                page.insert_text((40, 104), 'alone.', fontsize=size)
            page.set_rotation(rotate)
            doc.save(path)
        return path

    def test_pdf_identity_actual_sizes_and_conservative_line_candidate(self):
        path = self.make('ordinary.pdf')
        r = profile_pdf_layout(path)
        self.assertTrue(r['checked'])
        self.assertEqual(r['pdf_sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(r['font_size_median_pt'], 10)
        self.assertFalse(r['automatic_acceptance'])
        candidates = r['pages'][0]['short_terminal_line_candidates']
        self.assertEqual([c['text'] for c in candidates], ['alone.'])
        self.assertIn('requires_page_and_source_review', candidates[0]['classification'])

    def test_small_type_change_is_measured_without_a_font_guard(self):
        old, new = self.make('old.pdf', 10), self.make('new.pdf', 6)
        report = compare_candidates(old, new)
        self.assertEqual(report['before']['layout_observations']['font_size_median_pt'], 10)
        self.assertEqual(report['after']['layout_observations']['font_size_median_pt'], 6)
        self.assertIsNone(report['selected_candidate'])
        self.assertFalse(report['automatic_acceptance'])

    def test_missing_invalid_and_blank_have_distinct_unknown_measurements(self):
        invalid = self.root / 'invalid.pdf'; invalid.write_bytes(b'not pdf')
        for path in [invalid, self.root / 'missing.pdf']:
            r = profile_pdf_layout(path)
            self.assertFalse(r['checked'])
            self.assertIsNone(r['page_count'])
            self.assertIsNone(r['font_size_median_pt'])
        blank = profile_pdf_layout(self.make('blank.pdf', text=False))
        self.assertTrue(blank['checked'])
        self.assertIsNone(blank['font_size_median_pt'])
        self.assertIsNone(blank['pages'][0]['text_extent_bbox'])

    def test_rotated_page_profile_does_not_assume_two_column_template(self):
        r = profile_pdf_layout(self.make('rotated.pdf', rotate=90))
        self.assertEqual(r['pages'][0]['width_pt'], 500)
        self.assertEqual(r['pages'][0]['height_pt'], 400)
        self.assertEqual(r['font_size_median_pt'], 10)
        self.assertNotIn('template', r)

    def test_terminal_line_candidates_survive_all_quarter_turns(self):
        observations = []
        for rotation in [0, 90, 180, 270]:
            r = profile_pdf_layout(self.make(f'rotation-{rotation}.pdf', rotate=rotation))
            candidates = r['pages'][0]['short_terminal_line_candidates']
            observations.append([(c['text'], c['width_fraction_of_block']) for c in candidates])
        self.assertEqual(observations, [observations[0]] * 4)
        self.assertEqual(observations[0][0][0], 'alone.')

    def test_profiles_preserve_pdf_and_refuse_report_overwrite(self):
        pdf = self.make('source.pdf')
        script = Path(__file__).with_name('pdf_layout_profile.py')
        original = pdf.read_bytes()
        report = self.root / 'old.json'; report.write_text('existing evidence')
        for out in [pdf, report]:
            result = subprocess.run([sys.executable, '-B', str(script), '--pdf', str(pdf), '--output', str(out)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
        self.assertEqual(pdf.read_bytes(), original)
        self.assertEqual(report.read_text(), 'existing evidence')

    def test_cli_keeps_first_valid_report(self):
        pdf = self.make('source.pdf')
        out = self.root / 'profile.json'
        result = subprocess.run([sys.executable, '-B', str(Path(__file__).with_name('pdf_layout_profile.py')), '--pdf', str(pdf), '--output', str(out)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(out.read_text())['checked'])

    def test_explicit_isolated_pdf_not_stale_neighbor_and_not_self_binding(self):
        main = self.root / 'paper.tex'
        main.write_text(r'\documentclass{article}\begin{document}Text.\end{document}')
        neighbor = self.make('paper.pdf', 6)
        selected = self.make('fresh-isolated.pdf', 10)
        r = build_host_context(main, root=self.root, pdf=selected)
        expected = hashlib.sha256(selected.read_bytes()).hexdigest()
        self.assertEqual(r['selected_pdf'], str(selected.resolve()))
        self.assertEqual(r['pdf_selection'], 'explicit')
        self.assertEqual(r['pdf_layout_profile']['pdf_sha256'], expected)
        self.assertNotEqual(r['pdf_layout_profile']['pdf_sha256'], hashlib.sha256(neighbor.read_bytes()).hexdigest())
        self.assertIsNone(r['pdf_layout_profile']['applicable_to_current_source'])
        self.assertFalse(r['pdf_visibility']['pass'])

    def test_both_unmet_candidate_distance_is_not_hidden_by_same_status(self):
        one = self.make('one.pdf')
        def multi(name, count):
            out = self.root / name
            with fitz.open() as doc:
                for _ in range(count):
                    with fitz.open(one) as source:
                        doc.insert_pdf(source)
                doc.save(out)
            return out
        r = compare_candidates(multi('three.pdf', 3), multi('four.pdf', 4), target_pages=2, page_policy='at_most')
        self.assertEqual(r['page_distance'], {'before': 1, 'after': 2, 'delta': 1})
        self.assertIn('page_constraint_distance_increased', [w['kind'] for w in r['warnings']])


if __name__ == '__main__':
    unittest.main()
