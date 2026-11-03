from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from visual_signal_aggregator import _pair_object_clusters  # noqa: E402


# Rendered 220-DPI A4-ish page: 1700 x 2200 px. Left column spans x 100..760.
LEFT_COLUMN_TABLE = {
    "page": 3,
    "kind": "table_like",
    "bbox": [120, 300, 700, 500],
    "source": "test",
}
LEFT_COLUMN_CAPTION = {
    "page": 3,
    "kind": "caption_like",
    "bbox": [120, 510, 700, 540],
    "source": "test",
}
FULL_PAGE_FIGURE = {
    "page": 3,
    "kind": "figure_like",
    "bbox": [100, 900, 1600, 1400],
    "source": "test",
}


class PairingPageWidthTest(unittest.TestCase):
    def test_real_page_size_drives_page_ratio(self) -> None:
        pairings = _pair_object_clusters(
            [LEFT_COLUMN_TABLE, LEFT_COLUMN_CAPTION],
            page_sizes={3: (1700, 2200)},
        )
        self.assertEqual(len(pairings), 1)
        pairing = pairings[0]
        # 580px wide object on a 1700px page -> ~0.34, not ~1.0 from the old
        # bbox-edge "page width" of 700px.
        self.assertAlmostEqual(pairing["object_width_page_ratio"], 580 / 1700, places=3)
        self.assertEqual(pairing["width_context"], "column")
        self.assertIsNotNone(pairing["caption_bbox"])

    def test_full_width_object_is_page_context(self) -> None:
        pairings = _pair_object_clusters(
            [FULL_PAGE_FIGURE],
            page_sizes={3: (1700, 2200)},
        )
        self.assertEqual(pairings[0]["width_context"], "page")
        self.assertAlmostEqual(pairings[0]["object_width_page_ratio"], 1500 / 1700, places=3)

    def test_missing_page_size_falls_back_to_bbox_edge(self) -> None:
        pairings = _pair_object_clusters([LEFT_COLUMN_TABLE, LEFT_COLUMN_CAPTION])
        self.assertEqual(len(pairings), 1)
        # Legacy fallback: page width becomes the max bbox right edge (700).
        self.assertAlmostEqual(pairings[0]["object_width_page_ratio"], 580 / 700, places=3)


if __name__ == "__main__":
    unittest.main()
