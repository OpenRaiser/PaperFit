from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from regression.candidate_value import compute_candidate_value  # noqa: E402


class CandidateValueTest(unittest.TestCase):
    def test_local_high_severity_anchored_scores_high(self):
        result = compute_candidate_value(
            {
                "defect_family": "B2",
                "severity": "major",
                "candidate_type": "object",
                "target": {"label": "fig:one"},
                "proposed_action": "adjust_float_width",
                "visual_confidence": 0.9,
            }
        )
        self.assertTrue(result["rankable"])
        self.assertGreater(result["candidate_value"], 30)
        # 局部对象、有锚点、major → 高价值低惩罚
        self.assertEqual(result["components"]["scope_penalty"], 0.0)
        self.assertEqual(result["components"]["anchor_penalty"], 0.0)

    def test_global_low_severity_scores_lower(self):
        result = compute_candidate_value(
            {
                "defect_family": "A3",
                "severity": "minor",
                "candidate_type": "global",
                "proposed_action": "reduce_page_count",
            }
        )
        self.assertLess(result["candidate_value"], 0)

    def test_review_candidate_not_rankable(self):
        result = compute_candidate_value(
            {
                "defect_family": "B2",
                "severity": "major",
                "candidate_type": "object",
                "requires_human_review": True,
            }
        )
        self.assertFalse(result["rankable"])

    def test_unanchored_penalty(self):
        anchored = compute_candidate_value(
            {"defect_family": "B2", "severity": "major", "candidate_type": "object", "target": {"label": "fig:x"}}
        )
        unanchored = compute_candidate_value(
            {"defect_family": "B2", "severity": "major", "candidate_type": "object", "target": {}}
        )
        self.assertLess(unanchored["candidate_value"], anchored["candidate_value"])
        self.assertGreater(unanchored["components"]["anchor_penalty"], 0)

    def test_low_confidence_halves_benefit(self):
        high = compute_candidate_value(
            {"defect_family": "B2", "severity": "major", "candidate_type": "object", "visual_confidence": 0.9, "target": {"label": "fig:x"}}
        )
        low = compute_candidate_value(
            {"defect_family": "B2", "severity": "major", "candidate_type": "object", "visual_confidence": 0.3, "target": {"label": "fig:x"}}
        )
        self.assertGreater(high["components"]["benefit"], low["components"]["benefit"])

    def test_semantic_edit_has_content_risk(self):
        layout_only = compute_candidate_value(
            {"defect_family": "B2", "severity": "major", "candidate_type": "object", "proposed_action": "adjust_float_width", "target": {"label": "fig:x"}}
        )
        semantic = compute_candidate_value(
            {"defect_family": "A3", "severity": "major", "candidate_type": "global", "proposed_action": "semantic_text_edit"}
        )
        self.assertGreater(semantic["components"]["content_risk"], layout_only["components"]["content_risk"])

    def test_b4_longtable_has_compile_cost(self):
        b4 = compute_candidate_value(
            {"defect_family": "B4", "severity": "major", "candidate_type": "object", "target": {"label": "tab:x"}}
        )
        b2 = compute_candidate_value(
            {"defect_family": "B2", "severity": "major", "candidate_type": "object", "target": {"label": "fig:x"}}
        )
        self.assertGreater(b4["components"]["compile_cost"], b2["components"]["compile_cost"])

    def test_critical_scores_above_major(self):
        critical = compute_candidate_value(
            {"defect_family": "B2", "severity": "critical", "candidate_type": "object", "target": {"label": "fig:x"}}
        )
        major = compute_candidate_value(
            {"defect_family": "B2", "severity": "major", "candidate_type": "object", "target": {"label": "fig:x"}}
        )
        self.assertGreater(critical["candidate_value"], major["candidate_value"])


if __name__ == "__main__":
    unittest.main()
