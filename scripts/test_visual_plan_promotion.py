from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from repair_plan_generator import _promote_visual_action_plan, generate_repair_plan  # noqa: E402


def _make_project(root: Path) -> Path:
    """创建带 visual_action_plan.json 的项目。"""
    data = root / "data"
    data.mkdir(parents=True)

    visual = {
        "summary": {"pages_analyzed": 8},
        "findings": [
            {
                "defect_id": "vf-001",
                "taxonomy_defect_id": "B2",
                "severity": "major",
                "page": 5,
                "confidence": 0.9,
                "bbox": [100, 200, 500, 600],
                "description": "figure overflows column",
                "source": "pymupdf_native",
                "metrics": {"subtype": "overflow_width", "object_kind": "figure_like", "label": "fig:wide"},
            },
        ],
        "hints": [],
    }
    (data / "visual_signal_report.json").write_text(json.dumps(visual), encoding="utf-8")

    action_plan = {
        "schema_version": "1.0",
        "summary": {"total_entries": 3, "by_status": {"executable": 1, "blocked": 1, "human_review": 1}},
        "entries": [
            {
                "finding_id": "vf-001",
                "defect_type": "overflow_width",
                "family": "B2",
                "severity": "major",
                "page": 5,
                "label": "fig:wide",
                "source_anchors": [{"file": "main.tex", "start_line": 42, "end_line": None}],
                "anchor_status": "resolved",
                "confidence": 0.9,
                "recommended_actions": ["adjust_float_width"],
                "risk": "medium",
                "status": "executable",
                "evidence": ["figure overflows column"],
            },
            {
                "finding_id": "vf-002",
                "family": "C2",
                "severity": "major",
                "status": "blocked",
                "status_reason": "image resolution has no safe executor",
                "anchor_status": "unresolved",
            },
            {
                "finding_id": "vf-003",
                "family": "A1",
                "severity": "major",
                "status": "human_review",
                "status_reason": "source anchor unresolved",
                "anchor_status": "unresolved",
            },
        ],
    }
    (data / "visual_action_plan.json").write_text(json.dumps(action_plan), encoding="utf-8")

    rule = {"summary": {"overfull_hbox_total": 0, "underfull_hbox_total": 0}}
    (data / "rule_report.json").write_text(json.dumps(rule), encoding="utf-8")

    return data


class VisualPlanPromotionTest(unittest.TestCase):
    def test_executable_entry_promoted_to_candidate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data = _make_project(Path(tmpdir))
            candidates = _promote_visual_action_plan(str(data / "visual_signal_report.json"))
            self.assertEqual(len(candidates), 1)
            c = candidates[0]
            self.assertEqual(c["defect_family"], "B2")
            self.assertEqual(c["proposed_action"], "adjust_float_width")
            self.assertEqual(c["finding_id"], "vf-001")
            self.assertEqual(c["candidate_type"], "visual_plan")

    def test_blocked_and_review_not_promoted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data = _make_project(Path(tmpdir))
            candidates = _promote_visual_action_plan(str(data / "visual_signal_report.json"))
            families = [c["defect_family"] for c in candidates]
            self.assertNotIn("C2", families)  # blocked
            self.assertNotIn("A1", families)  # human_review

    def test_no_action_plan_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data = Path(tmpdir) / "data"
            data.mkdir()
            candidates = _promote_visual_action_plan(str(data / "visual_signal_report.json"))
            self.assertEqual(candidates, [])

    def test_end_to_end_plan_includes_visual_candidates(self):
        """完整链路：visual_action_plan → repair_plan.json → 候选列表。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            data = _make_project(Path(tmpdir))
            plan = generate_repair_plan(
                visual_signal_report=str(data / "visual_signal_report.json"),
                output_path=str(data / "repair_plan.json"),
                rule_report=str(data / "rule_report.json"),
            )
            all_candidates = plan.get("candidates") or []
            visual_plan_candidates = [c for c in all_candidates if c.get("candidate_type") == "visual_plan"]
            self.assertGreaterEqual(len(visual_plan_candidates), 1)
            self.assertTrue(any(c.get("finding_id") == "vf-001" for c in visual_plan_candidates))


if __name__ == "__main__":
    unittest.main()
