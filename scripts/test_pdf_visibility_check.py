"""Real PDF glyph geometry regressions; no paper/case-specific exceptions."""
from __future__ import annotations

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
from pdf_visibility_check import check_pdf_visibility


class PdfVisibilityCheckTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='paperfit-visibility-')
        self.addCleanup(self.temp.cleanup)
        self.pdf = Path(self.temp.name) / 'fixture.pdf'

    def write_pdf(self, *, point=(40, 120), text='VISIBLE TEXT', rotation=0,
                  crop=None, render_mode=0, opacity=1.0, actual_text=None,
                  clip=None, pages=1, gray=False):
        with fitz.open() as document:
            for _ in range(pages):
                page = document.new_page(width=200, height=300)
                if gray:
                    page.draw_rect(page.rect, color=None, fill=(0.5, 0.5, 0.5))
                if text:
                    page.insert_text(point, text, fontsize=12, render_mode=render_mode, fill_opacity=opacity)
                    xref = page.get_contents()[-1]
                    stream = document.xref_stream(xref)
                    if actual_text is not None:
                        stream = b'/Span << /ActualText (' + actual_text.encode('ascii') + b') >> BDC\n' + stream + b'\nEMC'
                    if clip is not None:
                        stream = (f'q\n{clip} re W n\n'.encode('ascii') + stream + b'\nQ')
                    document.update_stream(xref, stream)
                if crop:
                    page.set_cropbox(fitz.Rect(crop))
                page.set_rotation(rotation)
            document.save(self.pdf)

    def test_in_bounds_pdf_passes_and_bytes_are_unchanged(self):
        self.write_pdf()
        before = self.pdf.read_bytes()
        report = check_pdf_visibility(self.pdf)
        self.assertEqual(report['status'], 'pass')
        self.assertTrue(report['checked'])
        self.assertTrue(report['complete_document'])
        self.assertEqual(report['pdf_sha256'], hashlib.sha256(before).hexdigest())
        self.assertEqual(self.pdf.read_bytes(), before)

    def test_right_overflow_uses_glyphs_not_clipped_text_blocks(self):
        self.write_pdf(point=(180, 120), text='LONG UNBROKEN EQUATION SUFFIX')
        with fitz.open(self.pdf) as document:
            page = document[0]
            self.assertLess(max(block[2] for block in page.get_text('blocks')),
                            max(span['bbox'][2] for span in page.get_texttrace()) - 100)
        report = check_pdf_visibility(self.pdf)
        self.assertEqual(report['status'], 'needs_review')
        self.assertGreater(report['summary']['max_overflow_pt'], 100)
        self.assertGreater(report['summary']['fully_outside_glyph_count'], 10)
        self.assertTrue(report['findings'][0]['geometry_confirmed'])
        self.assertFalse(report['findings'][0]['scientific_damage_confirmed'])

    def test_entirely_off_page_text_still_requires_review(self):
        self.write_pdf(point=(240, 120), text='WHOLE TERM')
        with fitz.open(self.pdf) as document:
            self.assertEqual(document[0].get_text().strip(), '')
        report = check_pdf_visibility(self.pdf)
        self.assertEqual(report['status'], 'needs_review')
        self.assertEqual(report['summary']['outside_glyph_count'], report['summary']['fully_outside_glyph_count'])

    def test_rotation_maps_to_physical_page_edges(self):
        for rotation, side in ((0, 'right'), (90, 'bottom'), (180, 'left'), (270, 'top')):
            with self.subTest(rotation=rotation):
                self.write_pdf(point=(180, 120), text='OUTSIDE TEXT', rotation=rotation)
                report = check_pdf_visibility(self.pdf)
                self.assertEqual(set(report['findings'][0]['overflow_pt']), {side})
                self.assertEqual(report['pages'][0]['rotation'], rotation)

    def test_valid_rotated_page_does_not_compare_unrotated_width(self):
        self.write_pdf(point=(40, 250), text='VISIBLE', rotation=90)
        report = check_pdf_visibility(self.pdf)
        self.assertEqual(report['status'], 'pass', report)
        self.assertEqual(report['pages'][0]['visible_rect'], [0.0, 0.0, 300.0, 200.0])

    def test_offset_cropbox_is_the_visible_boundary(self):
        self.write_pdf(point=(160, 120), text='CROPPED TERM', crop=(30, 10, 190, 290))
        report = check_pdf_visibility(self.pdf)
        self.assertEqual(report['status'], 'needs_review')
        self.assertEqual(report['pages'][0]['cropbox'], [30.0, 10.0, 190.0, 290.0])
        self.assertEqual(report['pages'][0]['visible_rect'], [0.0, 0.0, 160.0, 280.0])
        self.assertGreater(report['summary']['max_overflow_pt'], 30)

    def test_whitespace_span_tail_is_not_visible_content_loss(self):
        self.write_pdf(point=(170, 120), text='OK' + ' ' * 30)
        self.assertEqual(check_pdf_visibility(self.pdf)['status'], 'pass')

    def test_hidden_ocr_and_zero_opacity_text_do_not_trigger(self):
        for mode, opacity in ((3, 1.0), (0, 0.0)):
            with self.subTest(mode=mode, opacity=opacity):
                self.write_pdf(point=(240, 120), text='INVISIBLE OCR', render_mode=mode, opacity=opacity)
                report = check_pdf_visibility(self.pdf)
                self.assertEqual(report['status'], 'pass')
                self.assertEqual(report['pages'][0]['painted_glyph_count'], 0)

    def test_fill_and_stroke_do_not_duplicate_glyph_counts(self):
        self.write_pdf(point=(240, 120), text='OFF', render_mode=2)
        report = check_pdf_visibility(self.pdf)
        self.assertEqual(report['summary']['outside_glyph_count'], 3)
        self.assertEqual(report['pages'][0]['painted_glyph_count'], 3)

    def test_zero_width_ligature_continuation_does_not_invalidate_real_geometry(self):
        self.write_pdf(point=(240, 120), text='OFF')
        with fitz.open(self.pdf) as document:
            trace = document[0].get_texttrace()
        trace[0]['chars'] = tuple(trace[0]['chars']) + ((108, -1, (940.0, 120.0), (940.0, 110.0, 940.0, 122.0)),)
        with mock.patch.object(fitz.Page, 'get_texttrace', return_value=trace):
            report = check_pdf_visibility(self.pdf)
        self.assertEqual(report['status'], 'needs_review')
        self.assertEqual(report['summary']['outside_glyph_count'], 3)

    def test_actualtext_replacement_does_not_hide_drawn_overflow(self):
        self.write_pdf(point=(180, 120), text='LONG VISIBLE EQUATION SUFFIX', actual_text='short source')
        report = check_pdf_visibility(self.pdf)
        self.assertEqual(report['status'], 'needs_review')
        self.assertIn('EQUATION', report['findings'][0]['painted_text_excerpt'])
        self.assertNotIn('short source', report['findings'][0]['painted_text_excerpt'])

    def test_long_accessibility_text_does_not_create_false_geometry(self):
        self.write_pdf(text='VISIBLE', actual_text='very long accessibility equation source ' * 20)
        self.assertEqual(check_pdf_visibility(self.pdf)['status'], 'pass')

    def test_blank_and_gray_pages_are_not_classified_as_invalid(self):
        for gray in (False, True):
            self.write_pdf(text='', gray=gray)
            self.assertEqual(check_pdf_visibility(self.pdf)['status'], 'pass')

    def test_tolerance_is_applied_to_glyph_not_span_tail(self):
        self.write_pdf(point=(200, 120), text='I')
        self.assertEqual(check_pdf_visibility(self.pdf, tolerance_pt=0.75)['status'], 'needs_review')
        self.assertEqual(check_pdf_visibility(self.pdf, tolerance_pt=4.0)['status'], 'pass')
        for tolerance in (-1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                check_pdf_visibility(self.pdf, tolerance_pt=tolerance)

    def test_explicit_clipping_records_uncertainty_instead_of_confirming_damage(self):
        self.write_pdf(point=(180, 120), text='CLIPPED DECORATION', clip='0 0 100 300')
        finding = check_pdf_visibility(self.pdf)['findings'][0]
        self.assertTrue(finding['explicit_clip_context'])
        self.assertFalse(finding['scientific_damage_confirmed'])
        self.assertEqual(finding['status'], 'needs_review')

    def test_selection_reports_incomplete_coverage_and_invalid_selection(self):
        self.write_pdf(pages=3)
        report = check_pdf_visibility(self.pdf, page_numbers=[2])
        self.assertEqual(report['status'], 'pass')
        self.assertFalse(report['complete_document'])
        self.assertEqual(report['checked_page_count'], 1)
        for pages in ([], [0], [4], [True]):
            self.assertEqual(check_pdf_visibility(self.pdf, page_numbers=pages)['status'], 'unavailable')

    def test_missing_dependency_and_unreadable_pdf_never_pass(self):
        self.write_pdf()
        with mock.patch('pdf_visibility_check.fitz', None):
            report = check_pdf_visibility(self.pdf)
            self.assertFalse(report['checked'])
            self.assertEqual(report['status'], 'unavailable')
        self.pdf.write_bytes(b'corrupt PDF')
        self.assertEqual(check_pdf_visibility(self.pdf)['status'], 'unavailable')
        self.assertEqual(check_pdf_visibility(Path(self.temp.name) / 'missing.pdf')['status'], 'unavailable')

    def test_cli_outputs_evidence_and_review_exit_code(self):
        self.write_pdf(point=(240, 120), text='OFF PAGE')
        output = Path(self.temp.name) / 'report.json'
        run = subprocess.run([sys.executable, str(Path(__file__).with_name('pdf_visibility_check.py')),
                              str(self.pdf), '--output', str(output)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertEqual(json.loads(output.read_text(encoding='utf-8'))['status'], 'needs_review')


if __name__ == '__main__':
    unittest.main()
