from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from regression.report import build_regression_report, render_markdown, write_regression_report  # noqa: E402


def _case(case_id, paper_id, verdict, **extra):
    record = {
        "case_id": case_id,
        "paper_id": paper_id,
        "perturbation_types": ["D1"],
        "column_type": "double",
        "profile": "hard",
        "assigned": True,
        "injection_status": "success",
        "b0_status": "layout_evaluable",
        "candidate_status": "layout_evaluable",
        "vlm": {"reconciled": verdict},
        "environment_state": "clean",
    }
    record.update(extra)
    return record


class RegressionReportTest(unittest.TestCase):
    def _report(self, deltas, verdicts=None):
        records = [
            _case(f"case-{i}", f"paper-{i % 5}", (verdicts or ["candidate"] * len(deltas))[i])
            for i in range(len(deltas))
        ]
        paired = [
            {"paper_id": f"paper-{i % 5}", "case_id": f"case-{i}", "delta": delta}
            for i, delta in enumerate(deltas)
        ]
        return build_regression_report(
            reference_version="v1",
            candidate_version="v2",
            safety_v1={"compile_success": 1.0, "render_success": 1.0, "content_integrity_ok": 1.0},
            safety_v2={"compile_success": 1.0, "render_success": 1.0, "content_integrity_ok": 1.0},
            case_records=records,
            paired_scores=paired,
        )

    def test_full_report_shape(self):
        report = self._report([0.4] * 40)
        self.assertEqual(report["decision"]["decision"], "IMPROVED")
        self.assertEqual(report["paired_visual"]["win_counts"]["candidate"], 40)
        self.assertEqual(report["coverage"]["stages"]["scored"], 40)
        self.assertIn("by_perturbation", report["stratified"])
        self.assertIn("by_column_type", report["stratified"])
        self.assertTrue(report["conclusion_eligibility"]["eligible_for_verdict"])

    def test_inconclusive_report_carries_warning(self):
        deltas = [0.1] * 16 + [-0.1] * 14
        report = self._report(deltas)
        self.assertEqual(report["decision"]["decision"], "INCONCLUSIVE")
        self.assertIsNotNone(report["conclusion_eligibility"]["note"])

    def test_markdown_contains_required_sections(self):
        report = self._report(
            [0.4] * 40,
            verdicts=["candidate"] * 30 + ["reference"] * 5 + ["tie"] * 3 + ["uncertain"] * 2,
        )
        markdown = render_markdown(report)
        for section in ("回归报告", "结论", "安全指标", "视觉对比", "分层结果", "覆盖率"):
            self.assertIn(section, markdown)
        self.assertIn("candidate 胜 30", markdown)
        self.assertIn("assigned 40 → injected 40", markdown)

    def test_write_report_produces_json_and_md(self):
        report = self._report([0.3] * 30)
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = write_regression_report(report, Path(tmpdir) / "out")
            self.assertTrue(md_path.exists())
            payload = json.loads((md_path.parent / "regression_report.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["comparison"]["candidate"], "v2")

    def test_safety_regression_flows_into_report(self):
        records = [_case(f"case-{i}", f"paper-{i}", "candidate") for i in range(10)]
        report = build_regression_report(
            reference_version="v1",
            candidate_version="v2",
            safety_v1={"compile_success": 0.98, "render_success": 1.0},
            safety_v2={"compile_success": 0.80, "render_success": 1.0},
            case_records=records,
            paired_scores=[{"paper_id": f"paper-{i}", "delta": 0.5} for i in range(10)],
        )
        self.assertEqual(report["decision"]["decision"], "REGRESSED")
        self.assertTrue(report["safety"]["regressions"])
        markdown = render_markdown(report)
        self.assertIn("安全回退", markdown)


if __name__ == "__main__":
    unittest.main()
