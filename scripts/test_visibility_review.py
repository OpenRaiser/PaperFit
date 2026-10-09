from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from visibility_review import review_pdf_visibility, finding_id, sha256
from gatekeeper_enforcer import make_decision
from visual_signal_aggregator import aggregate_visual_signals
try:
    import fitz
except ImportError:
    fitz = None


@unittest.skipIf(fitz is None, 'PyMuPDF unavailable')
class VisibilityReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.pdf = self.root / 'main.pdf'
        with fitz.open() as doc:
            page = doc.new_page(width=200, height=200)
            page.insert_text((180, 50), 'VISIBLE-AND-OUTSIDE', fontsize=12)
            doc.save(self.pdf)
        self.image = self.root / 'page.png'
        with fitz.open(self.pdf) as doc:
            doc[0].get_pixmap().save(self.image)
        self.reviews = self.root / 'data' / 'pdf_visibility_reviews.json'

    def review(self):
        return review_pdf_visibility(self.pdf, root=self.root, reviews_path=self.reviews)

    def accept(self, **change):
        report = self.review()
        entry = {'finding_id': report['findings'][0]['finding_id'],
                 'disposition': 'intentional_clipping', 'reason': 'Reviewed page: decorative overset label is intentionally cropped.',
                 'inspected_page_image': {'path': 'page.png', 'page': 1, 'render_dpi': 72, 'sha256': sha256(self.image)}}
        entry.update(change)
        payload = {'schema': 'paperfit_visibility_reviews.v1', 'pdf_sha256': sha256(self.pdf), 'reviews': [entry]}
        self.reviews.parent.mkdir(exist_ok=True)
        self.reviews.write_text(json.dumps(payload), encoding='utf-8')
        return payload

    def test_unreviewed_geometry_continues_and_does_not_claim_semantic_damage(self):
        report = self.review()
        self.assertFalse(report['pass'])
        self.assertEqual(report['unresolved_finding_count'], 1)
        self.assertIn('scientific/prose/template guards still apply', report['review_scope'])

    def test_exact_visual_review_allows_intentional_clip(self):
        self.accept()
        report = self.review()
        self.assertTrue(report['pass'])
        self.assertEqual(report['reviewed_finding_count'], 1)

    def test_stale_pdf_review_cannot_authorize_recompiled_document(self):
        self.accept()
        with fitz.open(self.pdf) as doc:
            doc[0].insert_text((10, 100), 'Changed document')
            doc.save(self.root / 'next.pdf')
        (self.root / 'next.pdf').replace(self.pdf)
        self.assertFalse(self.review()['pass'])

    def test_changed_inspected_raster_invalidates_review(self):
        self.accept()
        self.image.write_bytes(b'changed')
        self.assertFalse(self.review()['pass'])

    def test_corrupt_or_unrelated_image_with_matching_file_hash_cannot_authorize(self):
        from PIL import Image
        for corrupt in (False, True):
            if corrupt:
                self.image.write_bytes(b'not a readable PNG')
            else:
                Image.new('RGB', (200,200), 'white').save(self.image)
            self.accept()
            self.assertFalse(self.review()['pass'])

    def test_two_paint_operations_have_distinct_review_ids(self):
        first = {'page': 1, 'bbox': [180,40,300,50], 'paint_operation_index': 0, 'painted_text_excerpt': 'ABCDE'}
        second = {**first, 'paint_operation_index': 1, 'painted_text_excerpt': 'FGHIJ'}
        self.assertNotEqual(finding_id(first), finding_id(second))

    def test_blank_reason_bare_ignore_other_finding_and_wrong_page_rejected(self):
        for change in ({'reason': ''}, {'disposition': 'ignore'}, {'finding_id': 'other'},
                       {'inspected_page_image': {'path': 'page.png', 'page': 2, 'sha256': sha256(self.image)}}):
            self.accept(**change)
            self.assertFalse(self.review()['pass'], change)

    def test_live_gate_finds_clipping_even_with_empty_visual_report(self):
        state = {'main_tex': str(self.root/'main.tex'), 'compile_success': True, 'page_images_rendered': True,
                 'artifacts': {'visual_signal_report': 'data/visual_signal_report.json'},
                 'visual_signals_summary': {'updated_at': 'now'}}
        with patch('gatekeeper_enforcer.Path.cwd', return_value=self.root):
            result = make_decision(state, {'defects': []}, False, None)
        self.assertEqual(result['decision'], 'CONTINUE')
        self.assertFalse(result['checks']['pdf_visibility']['pass'])
        self.accept()
        with patch('gatekeeper_enforcer.Path.cwd', return_value=self.root):
            result = make_decision(state, {'defects': []}, False, None)
        self.assertEqual(result['decision'], 'DONE')

    def test_explicit_external_pdf_is_measured_without_approving_crop(self):
        self.accept()
        child = self.root / 'child'; child.mkdir()
        report = review_pdf_visibility(self.pdf, root=child, reviews_path=self.reviews,
                                       allow_external_measurement=True)
        self.assertTrue(report['checked'])
        self.assertEqual(report['artifact_location'], 'external_explicit_pdf')
        self.assertEqual(report['review_report_status'], 'external_measurement_only')
        self.assertEqual(report['reviewed_finding_count'], 0)
        self.assertFalse(report['pass'])
        self.assertEqual(report['pdf_sha256'], sha256(self.pdf))

    def test_no_pdf_or_external_pdf_is_unavailable(self):
        self.assertFalse(review_pdf_visibility(self.root/'missing.pdf', root=self.root)['pass'])
        self.assertFalse(review_pdf_visibility(self.pdf, root=self.root/'child')['pass'])

    def test_aggregator_preserves_new_overflow_after_review_and_deduplicates_extraction(self):
        with fitz.open(self.pdf) as doc:
            doc[0].insert_text((180, 120), 'SECOND-UNREVIEWED-OVERFLOW', fontsize=12)
            doc.save(self.root/'next.pdf')
        (self.root/'next.pdf').replace(self.pdf)
        with fitz.open(self.pdf) as doc:
            doc[0].get_pixmap().save(self.image)
        self.accept()  # Review one paint operation; the other must remain open.
        native = {'available': True, 'findings': [{'defect_id':'D1-native-right-overflow',
                    'taxonomy_defect_id':'D1','page':1,'severity':'major','confidence':.92}], 'object_clusters': []}
        with patch('visual_signal_aggregator.BatchCVDetector') as detector, patch(
                'visual_signal_aggregator._extract_pymupdf_native_signals', return_value=native):
            detector.return_value.run_batch.return_value = {'pages_analyzed':1,'page_results':[]}
            result = aggregate_visual_signals(str(self.root/'pages'), str(self.root/'data/visual_signal_report.json'),
                        str(self.root/'taxonomy.yaml'), pdf_path=str(self.pdf), project_root=str(self.root))
        self.assertEqual(len(result['findings']), 1)
        self.assertEqual(result['findings'][0]['source'], 'pymupdf_texttrace')
        self.assertEqual(len(result['hints']), 1)
        self.assertEqual(result['hints'][0]['status'], 'reviewed_intentional_clipping')

    def test_nested_main_uses_explicit_compilation_root_for_reviews(self):
        self.accept()
        nested = self.root/'source'; nested.mkdir()
        self.pdf.replace(nested/'main.pdf'); self.pdf = nested/'main.pdf'
        with patch('visual_signal_aggregator.BatchCVDetector') as detector, patch(
                'visual_signal_aggregator._extract_pymupdf_native_signals', return_value={'findings':[], 'object_clusters':[]}):
            detector.return_value.run_batch.return_value = {'pages_analyzed':1,'page_results':[]}
            result = aggregate_visual_signals(str(self.root/'pages'), str(self.root/'data/visual_signal_report.json'),
                        str(self.root/'taxonomy.yaml'), pdf_path=str(self.pdf), project_root=str(self.root))
        self.assertTrue(result['machine_sources']['pdf_visibility']['pass'])


if __name__ == '__main__':
    unittest.main()
