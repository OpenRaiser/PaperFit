from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from repair_plan_generator import _build_object_candidates  # noqa: E402


def _visual(with_label: bool):
    metrics = {
        "subtype": "overflow_width",
        "object_kind": "figure_like",
        "pdf_bbox": [72.0, 100.0, 300.0, 260.0],
    }
    finding = {
        "defect_id": "f-001",
        "taxonomy_defect_id": "B2",
        "severity": "major",
        "page": 5,
        "confidence": 0.9,
        "bbox": [120, 240, 700, 620],
        "description": "figure overflows",
        "source": "pymupdf_native",
        "metrics": metrics,
    }
    crossrefs = {
        "floats": [
            {"label": "fig:x", "float_type": "figure", "number": 1, "line_number": 30, "width_spec": "0.9\\linewidth"}
        ],
    }
    if with_label:
        finding["metrics"]["label"] = "fig:x"
        finding["metrics"]["caption_number"] = 1
    else:
        # 真正的"无锚"：源里没有任何浮动体可供 fallback 贴标签
        crossrefs = {"floats": []}
    return {"findings": [finding], "hints": [], "caption_anchors": []}, crossrefs


class UnanchoredCandidatesTest(unittest.TestCase):
    def test_anchored_object_still_becomes_b2_candidate(self):
        visual, crossrefs = _visual(with_label=True)
        candidates = _build_object_candidates(visual, crossrefs)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["defect_family"], "B2")
        self.assertFalse(candidates[0].get("requires_human_review"))

    def test_unanchored_object_becomes_review_candidate_not_dropped(self):
        visual, crossrefs = _visual(with_label=False)
        candidates = _build_object_candidates(visual, crossrefs)
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["defect_family"], "B2")
        self.assertTrue(candidate["requires_human_review"])
        self.assertEqual(candidate["proposed_action"], "review_only")
        self.assertEqual(candidate["anchor_status"], "unresolved")
        self.assertIsNone(candidate["target"]["label"])
        self.assertIn("unresolved", candidate["rationale"])


if __name__ == "__main__":
    unittest.main()
