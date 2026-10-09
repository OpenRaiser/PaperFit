from __future__ import annotations
import sys, tempfile, unittest
from pathlib import Path
import cv2
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
from cv_detector import CVDefectDetector, BatchCVDetector

class ContinuousTrailingWhitespaceTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'page_001.png'
    def detector(self,image,page=1,total=1):
        self.assertTrue(cv2.imwrite(str(self.path),image))
        return CVDefectDetector(str(self.path),page_number=page,total_pages=total)
    def blank(self): return np.full((1000,700,3),255,np.uint8)
    def sparse(self,footer=False):
        image=self.blank()
        for y in range(100,510,20):
            cv2.rectangle(image,(70,y),(610,y+4),(0,0,0),-1)
        if footer: cv2.putText(image,'12',(338,945),cv2.FONT_HERSHEY_SIMPLEX,0.55,(0,0,0),1,cv2.LINE_AA)
        return image
    def both(self,image,**kwargs):
        d=self.detector(image,**kwargs)
        return d,d.detect_whitespace(),d.detect_trailing_whitespace_bottom()
    def test_dense_low_ink_page_is_not_tail_whitespace(self):
        image=self.blank()
        for y in range(80,910,14):
            for x in range(75,625,9): cv2.rectangle(image,(x,y),(x+2,y+2),(0,0,0),-1)
        d,a,b=self.both(image)
        self.assertGreater(d.measure_trailing_whitespace()['bottom_whitespace_ratio'],0.95)
        self.assertEqual(a,[]); self.assertEqual(b,[])
    def test_two_column_content_at_bottom_prevents_false_tail(self):
        image=self.sparse()
        for y in range(530,920,16): cv2.rectangle(image,(375,y),(615,y+3),(0,0,0),-1)
        _,a,b=self.both(image);self.assertEqual(a,[]);self.assertEqual(b,[])
    def test_genuine_continuous_tail_agrees_in_both_paths(self):
        _,a,b=self.both(self.sparse())
        self.assertEqual(len(a),1);self.assertEqual(len(b),1)
        self.assertEqual(a[0].metrics,b[0].metrics)
        self.assertFalse(a[0].metrics['host_review_required'])
        self.assertGreater(a[0].metrics['whitespace_ratio'],0.45)
    def test_sparse_tail_with_page_number_is_uncertain_host_hint(self):
        d,a,b=self.both(self.sparse(True))
        self.assertEqual(len(a),1);self.assertEqual(len(b),1)
        self.assertEqual(a[0].metrics,b[0].metrics)
        self.assertTrue(a[0].metrics['host_review_required'])
        self.assertLess(a[0].confidence,0.75)
        self.assertLess(a[0].metrics['raw_trailing_whitespace_ratio'],0.10)
        self.assertGreater(a[0].metrics['whitespace_ratio'],0.40)
        start,end=a[0].metrics['continuous_blank_start_px'],a[0].metrics['continuous_blank_end_px']
        self.assertTrue(np.all(d.gray[start:end]>d.thresholds['whitespace_pixel_threshold']))
    def test_near_bottom_wide_content_is_not_removed_as_footer(self):
        image=self.sparse();cv2.rectangle(image,(120,928),(580,943),(0,0,0),-1)
        d,a,b=self.both(image);self.assertEqual(a,[]);self.assertEqual(b,[])
        self.assertIsNone(d.measure_trailing_whitespace()['footer_candidate_bbox'])
    def test_footer_like_short_line_without_isolated_gap_is_content(self):
        image=self.sparse()
        for y in range(530,920,16): cv2.rectangle(image,(70,y),(610,y+3),(0,0,0),-1)
        cv2.putText(image,'12',(338,945),cv2.FONT_HERSHEY_SIMPLEX,0.55,(0,0,0),1,cv2.LINE_AA)
        d,a,b=self.both(image);self.assertEqual(a,[]);self.assertEqual(b,[])
        self.assertIsNone(d.measure_trailing_whitespace()['footer_candidate_bbox'])
    def test_bottom_band_is_never_certified_as_page_number(self):
        image=self.sparse();cv2.putText(image,'x',(344,945),cv2.FONT_HERSHEY_SIMPLEX,0.55,(0,0,0),1,cv2.LINE_AA)
        _,a,b=self.both(image);self.assertEqual(len(a),1)
        self.assertEqual(a[0].metrics['footer_classification'],'unverified_small_isolated_band')
        self.assertTrue(a[0].metrics['host_review_required'])
    def test_non_last_page_does_not_report_even_with_footer(self):
        _,a,b=self.both(self.sparse(True),page=1,total=2);self.assertEqual(a,[]);self.assertEqual(b,[])
    def test_blank_last_page_is_full_tail(self):
        _,a,b=self.both(self.blank());self.assertEqual(len(a),1);self.assertEqual(len(b),1)
        self.assertEqual(a[0].metrics['whitespace_ratio'],1.0)
        self.assertEqual(a[0].metrics['continuous_blank_start_px'],0)
        self.assertFalse(a[0].metrics['host_review_required'])
    def test_custom_threshold_uses_same_tail_height_unit(self):
        d=self.detector(self.sparse())
        self.assertEqual(d.detect_whitespace(0.60),[])
        self.assertEqual(d.detect_trailing_whitespace_bottom(0.60),[])
        self.assertEqual(len(d.detect_whitespace(0.40)),1)
        self.assertEqual(len(d.detect_trailing_whitespace_bottom(0.40)),1)
    def test_batch_footer_tail_reports_once_on_final_page(self):
        image=self.sparse(True);cv2.imwrite(str(self.path),image)
        cv2.imwrite(str(self.path.parent/'page_002.png'),image)
        report=BatchCVDetector(str(self.path.parent)).run_batch()
        hits=[d for p in report['page_results'] for d in p['detections'] if d['defect_id'].startswith('A2-')]
        self.assertEqual(len(hits),1);self.assertEqual(hits[0]['page'],2)
        self.assertTrue(hits[0]['metrics']['host_review_required'])
    def test_auxiliary_statistics_are_available_when_no_defect(self):
        image=self.blank();cv2.rectangle(image,(70,100),(610,910),(0,0,0),-1)
        d=self.detector(image);self.assertEqual(d.detect_whitespace(),[])
        metrics=d.summarize_page_structure()['trailing_whitespace']
        self.assertEqual(metrics['white_pixel_ratio_role'],'auxiliary_only')
        self.assertEqual(metrics['measurement'],'continuous_ink_free_rows')

if __name__=='__main__': unittest.main()
