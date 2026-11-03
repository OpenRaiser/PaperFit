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
from repair_plan_generator import _build_consistency_candidates, _match_priority_objects_to_labels, _promote_visual_action_plan  # noqa: E402
from repair_plan_executor import execute_repair_plan  # noqa: E402
from visual_action_plan import build_visual_action_plan  # noqa: E402


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


class VisualActionCaptionKindTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        main = self.root / "main.tex"
        # Real source extraction produces Table 1 before Figure 1. Matching
        # the first number alone would send a figure-width repair to the table.
        main.write_text(
            "\\documentclass{article}\n"
            "\\begin{document}\n"
            "\\begin{table}\n\\caption{Measurements}\n\\label{tab:one}\n"
            "\\begin{tabular}{cc}A & B\\\\\n1 & 2\\end{tabular}\n\\end{table}\n"
            "\\begin{figure}\n\\includegraphics{diagram.pdf}\n"
            "\\caption{Overview}\n\\label{fig:one}\n\\end{figure}\n"
            "\\end{document}\n",
            encoding="utf-8",
        )
        output = self.root / "crossrefs.json"
        with mock.patch.object(sys, "argv", ["extract_crossrefs.py", str(main), "--output", str(output)]):
            extract_crossrefs.main()
        self.crossrefs = json.loads(output.read_text(encoding="utf-8"))

    def _entry(self, metrics, *, severity="major", crossrefs=None, family="B2", source="vlm_detector"):
        visual = {"findings": [{
            "defect_id": "vlm-B2-001", "taxonomy_defect_id": family,
            "severity": severity, "confidence": 0.9, "page": 1,
            "source": source, "metrics": metrics,
        }]}
        plan = build_visual_action_plan(visual_report=visual, crossrefs_report=crossrefs or self.crossrefs)
        return plan["entries"][0]

    def test_same_number_resolves_with_vlm_and_cv_kind_aliases(self) -> None:
        for kind, label in [("figure", "fig:one"), ("figure_like", "fig:one"),
                            ("table", "tab:one"), ("table_like", "tab:one")]:
            with self.subTest(kind=kind):
                entry = self._entry({"object_kind": kind, "caption_number": 1})
                self.assertEqual(entry["label"], label)
                self.assertEqual(entry["anchor_status"], "resolved")
                self.assertEqual(entry["status"], "executable")
                expected = next(flt for flt in self.crossrefs["floats"] if flt["label"] == label)
                self.assertEqual(entry["source_anchors"][0]["start_line"], expected["line_number"])

    def test_legacy_number_without_kind_is_reviewed_for_all_severities(self) -> None:
        for severity in ("minor", "major", "critical"):
            with self.subTest(severity=severity):
                entry = self._entry({"caption_number": 1}, severity=severity)
                self.assertEqual(entry["anchor_status"], "unresolved")
                self.assertEqual(entry["anchor_reason"], "ambiguous_caption_number")
                self.assertEqual(entry["status"], "human_review")
                self.assertEqual(entry["source_anchors"], [])
                self.assertIsNone(entry["label"])

    def test_explicit_label_is_not_preempted_by_earlier_caption_match(self) -> None:
        entry = self._entry({"caption_number": 1, "label": "fig:one"})
        self.assertEqual(entry["label"], "fig:one")
        self.assertEqual(entry["anchor_status"], "resolved")

    def test_kind_never_falls_back_to_other_counter(self) -> None:
        crossrefs = {**self.crossrefs, "floats": [self.crossrefs["floats"][0]]}
        entry = self._entry({"object_kind": "figure", "caption_number": 1}, crossrefs=crossrefs)
        self.assertEqual(entry["anchor_status"], "unresolved")
        self.assertEqual(entry["source_anchors"], [])
        self.assertIsNone(entry["label"])

    def test_label_kind_conflict_stays_unanchored(self) -> None:
        entry = self._entry({"object_kind": "table", "label": "fig:one", "caption_number": 1}, severity="minor")
        self.assertEqual(entry["anchor_status"], "unresolved")
        self.assertEqual(entry["anchor_reason"], "object_kind_label_mismatch")
        self.assertEqual(entry["status"], "human_review")
        self.assertEqual(entry["source_anchors"], [])

    def test_duplicate_numbers_within_kind_are_not_guessed(self) -> None:
        figure = next(flt for flt in self.crossrefs["floats"] if flt["float_type"] == "figure")
        crossrefs = {**self.crossrefs, "floats": [figure, {**figure, "label": "fig:duplicate", "line_number": 99}]}
        entry = self._entry({"object_kind": "figure", "caption_number": 1}, crossrefs=crossrefs)
        self.assertEqual(entry["anchor_status"], "unresolved")
        self.assertEqual(entry["status"], "human_review")
        self.assertEqual(entry["anchor_reason"], "ambiguous_caption_number")

    def test_legacy_unique_number_still_resolves(self) -> None:
        figure = next(flt for flt in self.crossrefs["floats"] if flt["float_type"] == "figure")
        crossrefs = {**self.crossrefs, "floats": [figure]}
        entry = self._entry({"caption_number": 1}, crossrefs=crossrefs)
        self.assertEqual(entry["anchor_status"], "resolved")
        self.assertEqual(entry["label"], "fig:one")

    def test_vlm_oversized_table_routes_through_promotion_to_table_executor(self) -> None:
        finding = {
            "defect_id": "vf-vlm-c2", "taxonomy_defect_id": "C2",
            "source": "vlm_detector", "severity": "major", "confidence": 0.95,
            "page": 1, "description": "Table 1 visibly extends beyond its column",
            "metrics": {"defect_type": "c2", "object_kind": "table", "caption_number": 1},
        }
        raw_before = json.dumps(finding, sort_keys=True)
        plan = build_visual_action_plan(visual_report={"findings": [finding]}, crossrefs_report=self.crossrefs)
        self.assertEqual(json.dumps(finding, sort_keys=True), raw_before)
        entry = plan["entries"][0]
        self.assertEqual(entry["family"], "B2")
        self.assertEqual(entry["source_taxonomy_defect_id"], "C2")
        self.assertEqual(entry["source_defect_type"], "c2")
        self.assertEqual(entry["defect_type"], "overflow_width")
        self.assertEqual(entry["object_type"], "table_like")
        self.assertEqual(entry["label"], "tab:one")
        self.assertEqual(entry["status"], "executable")

        data = self.root / "data"
        data.mkdir()
        (data / "visual_action_plan.json").write_text(json.dumps(plan), encoding="utf-8")
        candidates = _promote_visual_action_plan(str(data / "visual_signal_report.json"))
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["visual_width_subtype"], "overflow_width")
        self.assertEqual(candidates[0]["target"]["object_kind"], "table_like")
        self.assertEqual(candidates[0]["target"]["label"], "tab:one")
        self.assertEqual(candidates[0]["proposed_action"], "adjust_float_width")
        repair_plan = data / "repair_plan.json"
        repair_plan.write_text(json.dumps({"candidates": candidates}), encoding="utf-8")
        main = self.root / "main.tex"
        before = main.read_text(encoding="utf-8")
        report = execute_repair_plan(str(repair_plan), str(main), str(data / "execution.json"))
        after = main.read_text(encoding="utf-8")
        self.assertGreater(report["applied_count"], 0)
        self.assertIn("\\begin{tabular*}", after)
        self.assertEqual(before.split("\\begin{figure}", 1)[1], after.split("\\begin{figure}", 1)[1])
        self.assertIn("\\caption{Measurements}", after)
        self.assertIn("\\label{tab:one}", after)
        self.assertIn("1 & 2", after)

    def test_cv_image_resolution_c2_keeps_original_blocked_route(self) -> None:
        entry = self._entry({"defect_type": "c2", "object_kind": "table_like", "caption_number": 1}, family="C2", source="cv_detector")
        self.assertEqual(entry["family"], "C2")
        self.assertEqual(entry["status"], "blocked")
        self.assertNotIn("source_taxonomy_defect_id", entry)

    def test_vlm_c2_without_table_semantics_is_not_remapped(self) -> None:
        entry = self._entry({"defect_type": "c2", "object_kind": "figure", "caption_number": 1}, family="C2")
        self.assertEqual(entry["family"], "C2")
        self.assertEqual(entry["status"], "blocked")

    def test_vlm_undersized_table_is_reviewed_and_not_global_consistency_repair(self) -> None:
        metrics = {"defect_type": "c1", "object_kind": "table", "caption_number": 1}
        entry = self._entry(metrics, family="C1")
        self.assertEqual(entry["status"], "human_review")
        self.assertEqual(entry["recommended_actions"], [])
        visual = {"findings": [{"taxonomy_defect_id": "C1", "source": "vlm_detector", "metrics": metrics}]}
        self.assertEqual(_build_consistency_candidates(visual), [])
        visual["findings"].append({"taxonomy_defect_id": "C1", "source": "cv_detector", "metrics": {}})
        self.assertTrue(_build_consistency_candidates(visual))


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
