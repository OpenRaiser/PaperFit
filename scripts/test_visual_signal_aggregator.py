from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from visual_signal_aggregator import (  # noqa: E402
    _build_priority_objects,
    _native_b2_width_finding,
    _native_table_objects_for_page,
    _pair_object_clusters,
    _summarize_b2_width_findings,
)


class _FakeTable:
    def __init__(self, bbox):
        self.bbox = bbox


class _FakeTableFinder:
    def __init__(self, tables):
        self.tables = tables


class _FakePageWithTables:
    def __init__(self, tables):
        self._tables = tables

    def find_tables(self):
        return _FakeTableFinder(self._tables)


class VisualSignalAggregatorTest(unittest.TestCase):
    def test_b2_width_summary_counts_only_width_subtypes(self) -> None:
        summary = _summarize_b2_width_findings(
            [
                {
                    "taxonomy_defect_id": "B2",
                    "page": 3,
                    "defect_id": "B2-native-figure-overflow-width",
                    "metrics": {"subtype": "overflow_width", "object_kind": "figure_like"},
                },
                {
                    "taxonomy_defect_id": "B2",
                    "page": 4,
                    "defect_id": "B2-native-table-underfilled-width",
                    "metrics": {"subtype": "underfilled_width", "object_kind": "table_like"},
                },
                {
                    "taxonomy_defect_id": "B2",
                    "metrics": {"subtype": "caption_gap", "object_kind": "table_like"},
                },
                {
                    "taxonomy_defect_id": "D1",
                    "metrics": {"subtype": "overflow_width", "object_kind": "formula"},
                },
            ]
        )

        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["by_subtype"], {"overflow_width": 1, "underfilled_width": 1})
        self.assertEqual(summary["by_object_kind"], {"figure_like": 1, "table_like": 1})
        self.assertEqual(summary["pages"], [3, 4])
        self.assertEqual(
            summary["finding_ids"],
            ["B2-native-figure-overflow-width", "B2-native-table-underfilled-width"],
        )

    def test_native_table_column_overflow_generates_b2_finding_before_page_edge_overflow(self) -> None:
        finding = _native_b2_width_finding(
            page_index=2,
            kind="table_like",
            bbox=(300.0, 120.0, 585.0, 260.0),
            scaled_bbox=[900, 360, 1755, 780],
            page_width=612.0,
            width_context="column",
            expected_width=240.0,
            width_ratio=1.1875,
            page_ratio=0.4657,
            taxonomy={"skill_routing": {"float-optimizer": ["B2"]}},
        )

        self.assertIsNotNone(finding)
        assert finding is not None
        self.assertEqual(finding["taxonomy_defect_id"], "B2")
        self.assertEqual(finding["defect_id"], "B2-native-table-overflow-width")
        self.assertEqual(finding["metrics"]["subtype"], "overflow_width")
        self.assertEqual(finding["metrics"]["object_kind"], "table_like")
        self.assertEqual(finding["metrics"]["overflow_basis"], "column_width")
        self.assertEqual(finding["suggested_skill"], "float-optimizer")

    def test_native_table_underfilled_column_width_generates_b2_finding(self) -> None:
        finding = _native_b2_width_finding(
            page_index=3,
            kind="table_like",
            bbox=(64.0, 180.0, 214.0, 310.0),
            scaled_bbox=[192, 540, 642, 930],
            page_width=612.0,
            width_context="column",
            expected_width=300.0,
            width_ratio=0.5,
            page_ratio=0.2451,
            taxonomy={"skill_routing": {"float-optimizer": ["B2"]}},
        )

        self.assertIsNotNone(finding)
        assert finding is not None
        self.assertEqual(finding["defect_id"], "B2-native-table-underfilled-width")
        self.assertEqual(finding["severity"], "major")
        self.assertEqual(finding["metrics"]["subtype"], "underfilled_width")

    def test_native_page_context_underfilled_width_is_not_reported_without_overflow(self) -> None:
        finding = _native_b2_width_finding(
            page_index=5,
            kind="table_like",
            bbox=(150.0, 180.0, 450.0, 310.0),
            scaled_bbox=[450, 540, 1350, 930],
            page_width=612.0,
            width_context="page",
            expected_width=612.0,
            width_ratio=0.4902,
            page_ratio=0.4902,
            taxonomy={"skill_routing": {"float-optimizer": ["B2"]}},
        )

        self.assertIsNone(finding)

    def test_native_table_objects_filter_small_table_fragments(self) -> None:
        page = _FakePageWithTables(
            [
                _FakeTable((54.0, 54.0, 358.0, 236.0)),
                _FakeTable((464.0, 233.0, 603.0, 246.0)),
            ]
        )

        objects = _native_table_objects_for_page(
            page,
            page_area=612.0 * 792.0,
            page_width=612.0,
            page_height=792.0,
        )

        self.assertEqual(objects, [("table_like", (54.0, 54.0, 358.0, 236.0), "table")])

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

    def test_clean_page_pairing_hints_do_not_become_priority_objects(self) -> None:
        priority_objects = _build_priority_objects(
            object_pairings=[
                {
                    "page": 4,
                    "object_kind": "figure_like",
                    "object_bbox": [165, 177, 894, 339],
                    "caption_bbox": None,
                    "caption_gap_px": 488,
                    "object_width_ratio": 1.0,
                    "object_width_page_ratio": 0.3898,
                    "width_context": "column",
                    "expected_width_px": 729,
                }
            ],
            page_summaries=[{"page": 4, "status": "clean"}],
            visual_rules={
                "object_width_min_ratio": 0.95,
                "caption_pair_max_gap_px": 120,
                "priority_object_missing_caption_score": 35,
                "priority_object_low_width_score": 25,
                "priority_object_large_gap_score": 18,
                "priority_object_top_k": 6,
                "priority_object_max_per_page": 2,
            },
        )

        self.assertEqual(priority_objects, [])

    def test_nonclean_page_pairing_hints_can_be_priority_objects(self) -> None:
        priority_objects = _build_priority_objects(
            object_pairings=[
                {
                    "page": 4,
                    "object_kind": "figure_like",
                    "object_bbox": [165, 177, 894, 339],
                    "caption_bbox": None,
                    "caption_gap_px": 488,
                    "object_width_ratio": 1.0,
                    "object_width_page_ratio": 0.3898,
                    "width_context": "column",
                    "expected_width_px": 729,
                }
            ],
            page_summaries=[{"page": 4, "status": "minor"}],
            visual_rules={
                "object_width_min_ratio": 0.95,
                "caption_pair_max_gap_px": 120,
                "priority_object_missing_caption_score": 35,
                "priority_object_low_width_score": 25,
                "priority_object_large_gap_score": 18,
                "priority_object_top_k": 6,
                "priority_object_max_per_page": 2,
            },
        )

        self.assertEqual(len(priority_objects), 1)
        self.assertEqual(priority_objects[0]["severity"], "minor")


if __name__ == "__main__":
    unittest.main()
