from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from visual_action_plan import build_visual_action_plan  # noqa: E402


CROSSREFS = {
    "source_file": "main.tex",
    "floats": [
        {"label": "fig:one", "float_type": "figure", "number": 1, "line_number": 42},
        {"label": "tab:alpha", "float_type": "table", "number": 3, "line_number": 120},
    ],
}


def _finding(**overrides):
    entry = {
        "defect_id": "f-001",
        "taxonomy_defect_id": "B2",
        "severity": "major",
        "page": 7,
        "confidence": 0.88,
        "bbox": [100, 200, 500, 600],
        "description": "figure overflows column",
        "source": "pymupdf_native",
        "metrics": {"subtype": "overflow_width", "object_kind": "figure_like", "label": "fig:one"},
    }
    entry.update(overrides)
    return entry


class BuildActionPlanTest(unittest.TestCase):
    def test_every_input_has_an_entry_nothing_dropped(self):
        visual = {
            "findings": [_finding()],
            "hints": [
                _finding(defect_id="h-001", taxonomy_defect_id="A1", confidence=0.5, metrics={}),
            ],
        }
        plan = build_visual_action_plan(visual_report=visual, crossrefs_report=CROSSREFS)
        self.assertEqual(plan["summary"]["total_entries"], 2)
        self.assertEqual(plan["summary"]["dropped"], 0)
        self.assertEqual(len(plan["entries"]), 2)

    def test_anchored_finding_is_executable_with_source_anchor(self):
        visual = {"findings": [_finding()], "hints": []}
        plan = build_visual_action_plan(visual_report=visual, crossrefs_report=CROSSREFS)
        entry = plan["entries"][0]
        self.assertEqual(entry["status"], "executable")
        self.assertEqual(entry["anchor_status"], "resolved")
        self.assertEqual(entry["source_anchors"][0]["file"], "main.tex")
        self.assertEqual(entry["source_anchors"][0]["start_line"], 42)
        self.assertEqual(entry["label"], "fig:one")
        self.assertIn("adjust_float_width", entry["recommended_actions"])
        self.assertIsNotNone(entry["risk"])

    def test_unanchored_major_finding_goes_to_human_review_not_void(self):
        visual = {
            "findings": [_finding(metrics={"subtype": "overflow_width", "object_kind": "figure_like"})],
            "hints": [],
        }
        plan = build_visual_action_plan(visual_report=visual, crossrefs_report=CROSSREFS)
        entry = plan["entries"][0]
        self.assertEqual(entry["status"], "human_review")
        self.assertEqual(entry["anchor_status"], "unresolved")
        self.assertIn("unresolved", entry["status_reason"])

    def test_c2_image_resolution_is_blocked_with_reason(self):
        visual = {
            "findings": [_finding(taxonomy_defect_id="C2", metrics={})],
            "hints": [],
        }
        plan = build_visual_action_plan(visual_report=visual, crossrefs_report=CROSSREFS)
        entry = plan["entries"][0]
        self.assertEqual(entry["status"], "blocked")
        self.assertIn("no safe executor", entry["status_reason"])

    def test_e_family_blocked_by_design(self):
        visual = {"findings": [_finding(taxonomy_defect_id="E1", metrics={})], "hints": []}
        plan = build_visual_action_plan(visual_report=visual, crossrefs_report=CROSSREFS)
        self.assertEqual(plan["entries"][0]["status"], "blocked")

    def test_low_confidence_hint_stays_human_review(self):
        visual = {
            "findings": [],
            "hints": [_finding(taxonomy_defect_id="A1", confidence=0.55, metrics={})],
        }
        plan = build_visual_action_plan(visual_report=visual, crossrefs_report=CROSSREFS)
        entry = plan["entries"][0]
        self.assertEqual(entry["status"], "human_review")
        self.assertEqual(entry["origin"], "hint")

    def test_caption_number_resolves_when_label_missing(self):
        visual = {
            "findings": [
                _finding(metrics={"subtype": "underfilled_width", "object_kind": "table_like", "caption_number": 3})
            ],
            "hints": [],
        }
        plan = build_visual_action_plan(visual_report=visual, crossrefs_report=CROSSREFS)
        entry = plan["entries"][0]
        self.assertEqual(entry["anchor_status"], "resolved")
        self.assertEqual(entry["label"], "tab:alpha")
        self.assertEqual(entry["caption_number"], 3)

    def test_status_counts_summary(self):
        visual = {
            "findings": [
                _finding(),  # executable
                _finding(defect_id="f-002", taxonomy_defect_id="C2", metrics={}),  # blocked
                _finding(defect_id="f-003", metrics={}),  # unanchored major → human_review
            ],
            "hints": [],
        }
        plan = build_visual_action_plan(visual_report=visual, crossrefs_report=CROSSREFS)
        self.assertEqual(plan["summary"]["by_status"], {"executable": 1, "blocked": 1, "human_review": 1})


if __name__ == "__main__":
    unittest.main()
