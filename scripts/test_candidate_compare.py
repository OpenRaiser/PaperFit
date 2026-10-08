"""Real preserved-PDF regressions and explicit page-policy tradeoffs."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import fitz
sys.path.insert(0, str(Path(__file__).resolve().parent))
from candidate_compare import compare_candidates


class CandidateComparisonTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='paperfit-candidates-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def pdf(self, name, pages=1, point=(40, 100)):
        path = self.root / name
        with fitz.open() as document:
            for _ in range(pages):
                page = document.new_page(width=200, height=300)
                page.insert_text(point, 'COMPLETE CONTENT', fontsize=12)
            document.save(path)
        return path

    def test_gained_page_has_review_without_forced_selection(self):
        x, y = self.pdf('x.pdf'), self.pdf('y.pdf', 2)
        r = compare_candidates(x, y, target_pages=1, page_policy='at_most')
        self.assertEqual(r['status'], 'needs_host_review')
        self.assertEqual(r['warnings'][0]['kind'], 'page_constraint_lost')
        self.assertIsNone(r['selected_candidate'])
        self.assertFalse(r['automatic_acceptance'])

    def test_shorter_valid_document_does_not_lose_upper_bound(self):
        r = compare_candidates(self.pdf('x.pdf', 3), self.pdf('y.pdf', 2), target_pages=3, page_policy='at_most')
        self.assertEqual(r['status'], 'measured_no_regression')
        self.assertEqual(r['page_constraint'], {'before_met': True, 'after_met': True})

    def test_exact_target_loss_is_distinct(self):
        r = compare_candidates(self.pdf('x.pdf', 3), self.pdf('y.pdf', 2), target_pages=3, page_policy='exact')
        self.assertEqual(r['warnings'][0]['kind'], 'page_constraint_lost')

    def test_real_painted_clipping_increase_needs_inspection(self):
        r = compare_candidates(self.pdf('x.pdf'), self.pdf('y.pdf', point=(190, 100)))
        self.assertGreater(r['changes']['painted_outside_glyph_delta'], 0)
        self.assertEqual(r['warnings'][0]['kind'], 'painted_outside_glyphs_increased')

    def test_missing_or_invalid_pdf_is_unknown_not_zero(self):
        y = self.root / 'invalid.pdf';y.write_bytes(b'not a PDF')
        for path in [y, self.root / 'missing.pdf']:
            r = compare_candidates(self.pdf('x'+path.stem+'.pdf'), path)
            self.assertEqual(r['status'], 'unavailable')
            self.assertIsNone(r['after']['page_count'])
            self.assertEqual(r['changes'], {})

    def test_partial_measurement_cannot_supply_no_regression(self):
        with mock.patch('candidate_compare.check_pdf_visibility', return_value={'checked': True, 'complete_document': False, 'page_count': 1, 'summary': {'outside_glyph_count': 0}}):
            r = compare_candidates('x.pdf', 'y.pdf')
        self.assertEqual(r['status'], 'unavailable')

    def test_pdf_inputs_are_not_modified(self):
        x, y = self.pdf('x.pdf'), self.pdf('y.pdf')
        digest = lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
        old = digest(x), digest(y)
        compare_candidates(x, y)
        self.assertEqual((digest(x), digest(y)), old)

    def test_cli_cannot_overwrite_input_or_existing_evidence(self):
        x, y = self.pdf('x.pdf'), self.pdf('y.pdf')
        before = x.read_bytes();script = Path(__file__).with_name('candidate_compare.py')
        base = [sys.executable, '-B', str(script), '--before-pdf', str(x), '--after-pdf', str(y)]
        for output in [x, self.root / 'report.json']:
            if output != x:output.write_text('historical evidence', encoding='utf-8')
            r = subprocess.run(base+['--output', str(output)], capture_output=True)
            self.assertNotEqual(r.returncode, 0)
        self.assertEqual(x.read_bytes(), before)

    def test_page_policy_needs_explicit_positive_target(self):
        for target in [None, 0, True]:
            with self.assertRaises(ValueError):
                compare_candidates('x.pdf', 'y.pdf', target_pages=target, page_policy='exact')

    def test_cli_distinguishes_measured_review_and_unavailable_exit_codes(self):
        x, y = self.pdf('x.pdf'), self.pdf('y.pdf', 2)
        script = Path(__file__).with_name('candidate_compare.py')
        base = [sys.executable, '-B', str(script), '--before-pdf', str(x)]
        for after, options, code, status in [
            (x, [], 0, 'measured_no_regression'),
            (y, ['--target-pages', '1', '--page-policy', 'exact'], 2, 'needs_host_review'),
            (self.root / 'missing.pdf', [], 3, 'unavailable'),
        ]:
            result = subprocess.run(base+['--after-pdf', str(after)]+options, capture_output=True)
            self.assertEqual(result.returncode, code, result.stderr.decode())
            self.assertEqual(json.loads(result.stdout)['status'], status)


if __name__ == '__main__':
    unittest.main()
