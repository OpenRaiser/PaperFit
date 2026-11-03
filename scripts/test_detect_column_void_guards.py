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

from detect_column_void import analyze_page  # noqa: E402


PAGE_W, PAGE_H = 400, 600
LEFT_TEXT_X = (20, 180)
RIGHT_TEXT_X = (220, 380)
GUTTER = (190, 210)  # kept blank in two-column fixtures


def _make_page(tmpdir: Path, name: str, blocks: list[tuple[int, int, int, int]]) -> Path:
    img = np.full((PAGE_H, PAGE_W), 255, dtype=np.uint8)
    for x0, x1, y0, y1 in blocks:
        img[y0:y1, x0:x1] = 0
    path = tmpdir / name
    cv2.imwrite(str(path), img)
    return path


def _analyze(path: Path) -> dict:
    return analyze_page(
        path,
        split_x=None,
        void_row_thresh=0.035,
        min_void_ratio=0.30,
        min_void_lines=3,
        smooth_rows=1,
        merge_gap_px=12,
        cwd=path.parent,
    )


class ColumnVoidGuardTest(unittest.TestCase):
    def test_interior_void_in_two_column_page_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_page(
                Path(tmpdir),
                "page_003.png",
                [
                    (*LEFT_TEXT_X, 40, 130),    # text above the void
                    (*LEFT_TEXT_X, 400, 470),   # text below the void
                    (*RIGHT_TEXT_X, 40, 560),   # right column full of text
                ],
            )
            result = _analyze(path)
        self.assertEqual(result["layout_mode"], "two_column")
        left_candidates = [c for c in result["a5_candidates"] if c["column"] == "left"]
        self.assertEqual(len(left_candidates), 1)
        self.assertGreaterEqual(left_candidates[0]["void_ratio_of_column"], 0.30)

    def test_bottom_void_is_not_reported_as_a5(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_page(
                Path(tmpdir),
                "page_005.png",
                [
                    (*LEFT_TEXT_X, 40, 230),    # text only above the void
                    (*RIGHT_TEXT_X, 40, 560),
                ],
            )
            result = _analyze(path)
        self.assertEqual(result["layout_mode"], "two_column")
        # The trailing half-empty column stays visible in diagnostics...
        self.assertGreaterEqual(result["columns"]["left"]["max_void_ratio"], 0.30)
        # ...but it is A2/A4 semantics, not an intra-column A5 defect.
        self.assertEqual(result["a5_candidates"], [])

    def test_top_void_is_not_reported_as_a5(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_page(
                Path(tmpdir),
                "page_006.png",
                [
                    (*LEFT_TEXT_X, 230, 560),   # text only below the void
                    (*RIGHT_TEXT_X, 40, 560),
                ],
            )
            result = _analyze(path)
        self.assertEqual(result["layout_mode"], "two_column")
        self.assertEqual(result["a5_candidates"], [])

    def test_single_column_page_is_not_split_into_fake_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_page(
                Path(tmpdir),
                "page_001.png",
                [
                    (30, 370, 40, 560),  # full-width text crosses the "gutter"
                ],
            )
            result = _analyze(path)
        self.assertEqual(result["layout_mode"], "single_column_or_unclear")
        self.assertGreater(result["gutter_ink_ratio"], 0.0)
        self.assertEqual(result["a5_candidates"], [])


if __name__ == "__main__":
    unittest.main()
