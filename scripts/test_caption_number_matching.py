from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
for entry in (str(SCRIPTS_DIR), str(REPO_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import extract_crossrefs  # noqa: E402
from repair_plan_generator import _match_priority_objects_to_labels  # noqa: E402


# 源码里 figure 顺序 = one, two；渲染页上两个图的位置与源码顺序相反，
# 旧的索引匹配会把 fig:one 标到第二个对象上。
CROSSREFS = {
    "floats": [
        {"label": "fig:one", "float_type": "figure", "number": 1, "number_source": "source_order", "line_number": 5},
        {"label": "fig:two", "float_type": "figure", "number": 2, "number_source": "source_order", "line_number": 12},
        {"label": "tab:alpha", "float_type": "table", "number": 1, "number_source": "source_order", "line_number": 20},
    ]
}

VISUAL = {
    "priority_objects": [
        {"page": 3, "object_kind": "figure_like", "bbox": [100, 100, 500, 400], "priority_score": 90,
         "reason": "underfilled_width:0.500", "object_width_ratio": 0.5, "visual_width_subtype": "underfilled_width"},
        {"page": 3, "object_kind": "figure_like", "bbox": [100, 700, 500, 1000], "priority_score": 88,
         "reason": "underfilled_width:0.600", "object_width_ratio": 0.6, "visual_width_subtype": "underfilled_width"},
        {"page": 3, "object_kind": "table_like", "bbox": [100, 1200, 500, 1500], "priority_score": 70,
         "reason": "caption_gap:22", "caption_gap_px": 22},
    ],
    "object_pairings": [
        {"page": 3, "object_kind": "figure_like", "object_bbox": [100, 100, 500, 400]},
        {"page": 3, "object_kind": "figure_like", "object_bbox": [100, 700, 500, 1000]},
        {"page": 3, "object_kind": "table_like", "object_bbox": [100, 1200, 500, 1500]},
    ],
    "caption_anchors": [
        # 第 3 页顶部对象是 Figure 2，底部对象是 Figure 1 —— 与源码顺序相反
        {"page": 3, "object_kind": "figure_like", "caption_number": 2, "caption_text": "Figure 2: later float",
         "rendered_bbox": [100, 410, 500, 440]},
        {"page": 3, "object_kind": "figure_like", "caption_number": 1, "caption_text": "Figure 1: earlier float",
         "rendered_bbox": [100, 1010, 500, 1040]},
        {"page": 3, "object_kind": "table_like", "caption_number": 1, "caption_text": "Table 1: alpha",
         "rendered_bbox": [100, 1510, 500, 1540]},
    ],
    "findings": [],
}


class CaptionNumberMatchingTest(unittest.TestCase):
    def test_caption_numbers_override_source_order(self) -> None:
        matched = _match_priority_objects_to_labels(VISUAL, CROSSREFS)
        by_bbox_y = {item["bbox"][1]: item for item in matched}
        top = by_bbox_y[100]
        bottom = by_bbox_y[700]
        self.assertEqual(top.get("label"), "fig:two")
        self.assertEqual(top.get("match_strategy"), "caption_number")
        self.assertEqual(top.get("caption_number"), 2)
        self.assertEqual(bottom.get("label"), "fig:one")
        self.assertEqual(bottom.get("caption_number"), 1)

    def test_table_matched_by_caption_number(self) -> None:
        matched = _match_priority_objects_to_labels(VISUAL, CROSSREFS)
        table = next(item for item in matched if item.get("object_kind") == "table_like")
        self.assertEqual(table.get("label"), "tab:alpha")
        self.assertEqual(table.get("match_strategy"), "caption_number")

    def test_falls_back_when_anchor_number_unknown(self) -> None:
        visual = dict(VISUAL)
        visual["caption_anchors"] = [
            {"page": 3, "object_kind": "figure_like", "caption_number": 9,
             "rendered_bbox": [100, 410, 500, 440]},
        ]
        matched = _match_priority_objects_to_labels(visual, CROSSREFS)
        strategies = {item.get("match_strategy") for item in matched}
        self.assertNotIn("caption_number", strategies)

    def test_no_anchors_keeps_legacy_matching(self) -> None:
        visual = dict(VISUAL)
        visual["caption_anchors"] = []
        matched = _match_priority_objects_to_labels(visual, CROSSREFS)
        self.assertTrue(matched)
        for item in matched:
            self.assertIsNone(item.get("caption_number"))


class CrossrefsNumberAnnotationTest(unittest.TestCase):
    def test_floats_carry_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "Ref ~\\ref{fig:a} and ~\\ref{fig:b}.\n"
                "\\begin{figure}\n\\includegraphics{x.png}\n\\caption{A}\n\\label{fig:a}\n\\end{figure}\n"
                "\\begin{figure}\n\\includegraphics{y.png}\n\\caption{B}\n\\label{fig:b}\n\\end{figure}\n"
                "See Figure 2 again.\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            output = root / "crossrefs.json"
            argv = ["extract_crossrefs.py", str(main_tex), "--output", str(output)]
            with mock.patch.object(sys, "argv", argv):
                extract_crossrefs.main()
            payload = json.loads(output.read_text(encoding="utf-8"))
            numbers = {flt["label"]: flt.get("number") for flt in payload["floats"]}
            self.assertEqual(numbers.get("fig:a"), 1)
            self.assertEqual(numbers.get("fig:b"), 2)
            sources = {flt["label"]: flt.get("number_source") for flt in payload["floats"]}
            self.assertEqual(sources.get("fig:a"), "source_order")


class PymupdfCaptionAnchorTest(unittest.TestCase):
    def test_caption_anchors_extracted_from_real_pdf(self) -> None:
        import fitz

        from visual_signal_aggregator import _extract_pymupdf_native_signals

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pdf = root / "sample.pdf"
            doc = fitz.open()
            page = doc.new_page()
            page.insert_text((72, 100), "Figure 2: later float appears first", fontsize=10)
            page.insert_text((72, 400), "Body text mentioning Figure 2 inline.")
            doc.save(str(pdf))
            doc.close()

            result = _extract_pymupdf_native_signals(
                str(pdf),
                pages_path=root / "pages",
                taxonomy={},
            )
            anchors = result.get("caption_anchors") or []
            # Only the caption-style block counts; the inline mention must not.
            self.assertEqual(len(anchors), 1)
            self.assertEqual(anchors[0]["caption_number"], 2)
            self.assertEqual(anchors[0]["object_kind"], "figure_like")
            self.assertEqual(anchors[0]["page"], 1)
            self.assertTrue(anchors[0]["pdf_bbox"])


if __name__ == "__main__":
    unittest.main()
