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

from cv_detector import CVDefectDetector  # noqa: E402


PAGE_W, PAGE_H = 800, 1000
STRIP_W = 40
STRIP_H = 60  # 2400 dark pixels, well above the 100-pixel floor


def _page_with_strip(tmpdir: Path, name: str, *, side: str | None) -> Path:
    img = np.full((PAGE_H, PAGE_W, 3), 255, dtype=np.uint8)
    if side == "right":
        img[:, PAGE_W - STRIP_W:] = (10, 10, 10)
    elif side == "left":
        img[:, :STRIP_W] = (10, 10, 10)
    elif side == "top":
        img[:STRIP_H, :] = (10, 10, 10)
    path = tmpdir / name
    cv2.imwrite(str(path), img)
    return path


def _overflow_ids(path: Path) -> tuple[set[str], dict[str, int]]:
    detector = CVDefectDetector(str(path), page_number=1, total_pages=2)
    detections = detector.detect_overflow()
    ids = {str(d.defect_id) for d in detections}
    metrics: dict[str, int] = {}
    for d in detections:
        metrics.update({str(k): int(v) for k, v in (d.metrics or {}).items()})
    return ids, metrics


class OverflowDetectionTest(unittest.TestCase):
    def test_right_overflow_reported_with_per_pixel_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _page_with_strip(Path(tmpdir), "right.png", side="right")
            ids, metrics = _overflow_ids(path)
        self.assertIn("D1-overfull-hbox", ids)
        # The margin band is 20px; the strip extends 40px, so the band fully
        # intersects the strip: 20px * 1000 rows = 20000 dark pixels.
        self.assertEqual(metrics["right_overflow_pixels"], 20 * PAGE_H)

    def test_left_overflow_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _page_with_strip(Path(tmpdir), "left.png", side="left")
            ids, metrics = _overflow_ids(path)
        self.assertIn("D1-left-overflow", ids)
        self.assertEqual(metrics["left_overflow_pixels"], 20 * PAGE_H)
        self.assertNotIn("D1-overfull-hbox", ids)

    def test_top_overflow_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _page_with_strip(Path(tmpdir), "top.png", side="top")
            ids, metrics = _overflow_ids(path)
        self.assertIn("D1-top-overflow", ids)
        self.assertEqual(metrics["top_overflow_pixels"], 20 * PAGE_W)

    def test_clean_page_reports_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _page_with_strip(Path(tmpdir), "clean.png", side=None)
            ids, _ = _overflow_ids(path)
        self.assertEqual(ids, set())


if __name__ == "__main__":
    unittest.main()
