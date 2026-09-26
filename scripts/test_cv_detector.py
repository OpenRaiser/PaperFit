from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cv_detector import BatchCVDetector, CVDefectDetector  # noqa: E402


def _write_page(path: Path, rectangles: list[tuple[int, int, int, int]]) -> None:
    image = np.full((900, 700, 3), 255, dtype=np.uint8)
    for x1, y1, x2, y2 in rectangles:
        cv2.rectangle(image, (x1, y1), (x2, y2), (0, 0, 0), thickness=-1)
    cv2.imwrite(str(path), image)


def _write_page_with_text_lines(
    path: Path,
    rectangles: list[tuple[int, int, int, int]],
    text_lines: int,
) -> None:
    image = np.full((900, 700, 3), 255, dtype=np.uint8)
    for x1, y1, x2, y2 in rectangles:
        cv2.rectangle(image, (x1, y1), (x2, y2), (0, 0, 0), thickness=-1)
    for idx in range(text_lines):
        y = 700 + idx * 12
        cv2.rectangle(image, (70, y), (420, y + 3), (0, 0, 0), thickness=-1)
    cv2.imwrite(str(path), image)


class CVDetectorTest(unittest.TestCase):
    def test_a2_whitespace_is_only_reported_on_last_page_when_total_pages_known(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            page_path = Path(tmp) / "page_001.png"
            _write_page(page_path, [(80, 80, 620, 420)])

            first_page = CVDefectDetector(str(page_path), page_number=1, total_pages=2)
            last_page = CVDefectDetector(str(page_path), page_number=2, total_pages=2)

            self.assertEqual(first_page.detect_whitespace(), [])
            self.assertEqual(first_page.detect_trailing_whitespace_bottom(), [])
            self.assertEqual(len(last_page.detect_whitespace()), 1)
            self.assertEqual(len(last_page.detect_trailing_whitespace_bottom()), 1)

    def test_batch_run_reports_only_one_a2_signal_for_trailing_last_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pages_dir = Path(tmp)
            _write_page(pages_dir / "page_001.png", [(80, 80, 620, 420)])
            _write_page(pages_dir / "page_002.png", [(80, 80, 620, 420)])

            report = BatchCVDetector(str(pages_dir)).run_batch()
            a2_detections = [
                det
                for page in report["page_results"]
                for det in page["detections"]
                if str(det["defect_id"]).startswith("A2-")
            ]

            self.assertEqual(len(a2_detections), 1)
            self.assertEqual(a2_detections[0]["page"], 2)

    def test_a1_short_line_is_hint_confidence_not_routed_finding_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            page_path = Path(tmp) / "page_001.png"
            _write_page(page_path, [(80, 80, 620, 420), (80, 500, 140, 520)])

            detector = CVDefectDetector(str(page_path), page_number=1)
            detections = detector.detect_lone_short_line()

            self.assertEqual(len(detections), 1)
            self.assertLess(detections[0].confidence, 0.75)

    def test_b3_requires_three_or_more_close_float_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            page_path = Path(tmp) / "page_001.png"
            _write_page(
                page_path,
                [
                    (120, 80, 580, 250),
                    (120, 290, 580, 460),
                ],
            )

            detector = CVDefectDetector(str(page_path), page_number=1)

            self.assertEqual(detector.detect_float_clustering(min_distance=100), [])

    def test_b3_reports_close_float_group_with_real_group_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            page_path = Path(tmp) / "page_001.png"
            _write_page(
                page_path,
                [
                    (120, 80, 580, 250),
                    (120, 290, 580, 460),
                    (120, 500, 580, 670),
                ],
            )

            detector = CVDefectDetector(str(page_path), page_number=1)
            detections = detector.detect_float_clustering(min_distance=100)

            self.assertEqual(len(detections), 1)
            self.assertEqual(detections[0].metrics["float_count"], 3)

    def test_b3_is_not_reported_when_page_has_substantial_text_support(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            page_path = Path(tmp) / "page_001.png"
            _write_page_with_text_lines(
                page_path,
                [
                    (120, 80, 580, 250),
                    (120, 290, 580, 460),
                    (120, 500, 580, 670),
                ],
                text_lines=12,
            )

            detector = CVDefectDetector(str(page_path), page_number=1)

            self.assertEqual(detector.detect_float_clustering(min_distance=100), [])


if __name__ == "__main__":
    unittest.main()
