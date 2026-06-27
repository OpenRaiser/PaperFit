from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from visual_signal_aggregator import _pair_object_clusters  # noqa: E402


class VisualSignalAggregatorTest(unittest.TestCase):
    def test_pairings_use_column_width_for_two_column_objects(self) -> None:
        clusters = [
            {"page": 1, "kind": "figure_like", "bbox": [165, 100, 895, 300]},
            {"page": 1, "kind": "caption_like", "bbox": [0, 305, 1870, 330]},
            {"page": 1, "kind": "table_like", "bbox": [975, 100, 1705, 300]},
            {"page": 1, "kind": "caption_like", "bbox": [0, 305, 1870, 330]},
        ]

        pairings = _pair_object_clusters(clusters)

        self.assertEqual(len(pairings), 2)
        self.assertEqual({p["width_context"] for p in pairings}, {"column"})
        self.assertTrue(all(abs(p["object_width_ratio"] - 1.0) < 0.01 for p in pairings))
        self.assertTrue(all(abs(p["object_width_page_ratio"] - 0.3904) < 0.01 for p in pairings))

    def test_pairings_preserve_low_ratio_for_genuinely_small_column_object(self) -> None:
        clusters = [
            {"page": 1, "kind": "figure_like", "bbox": [165, 100, 445, 300]},
            {"page": 1, "kind": "caption_like", "bbox": [0, 305, 1870, 330]},
            {"page": 1, "kind": "table_like", "bbox": [975, 100, 1675, 300]},
            {"page": 1, "kind": "caption_like", "bbox": [0, 305, 1870, 330]},
        ]

        pairings = _pair_object_clusters(clusters)
        small = next(p for p in pairings if p["object_kind"] == "figure_like")

        self.assertEqual(small["width_context"], "column")
        self.assertLess(small["object_width_ratio"], 0.6)

    def test_pairings_use_content_width_for_page_spanning_objects(self) -> None:
        clusters = [
            {"page": 1, "kind": "figure_like", "bbox": [165, 100, 1705, 500]},
            {"page": 1, "kind": "caption_like", "bbox": [0, 505, 1870, 530]},
            {"page": 1, "kind": "table_like", "bbox": [165, 700, 895, 900]},
            {"page": 1, "kind": "caption_like", "bbox": [0, 905, 1870, 930]},
        ]

        pairings = _pair_object_clusters(clusters)
        spanning = next(p for p in pairings if p["object_kind"] == "figure_like")

        self.assertEqual(spanning["width_context"], "page")
        self.assertTrue(abs(spanning["object_width_ratio"] - 1.0) < 0.01)
        self.assertLess(spanning["object_width_page_ratio"], 0.9)


if __name__ == "__main__":
    unittest.main()
