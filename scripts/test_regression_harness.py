from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from regression.decision import (  # noqa: E402
    check_safety,
    decide_regression,
    paired_bootstrap_ci,
)
from regression.funnel import build_funnel, load_case_records  # noqa: E402
from regression.manifest import (  # noqa: E402
    build_run_manifest,
    check_manifest_compatibility,
)


def _record(**overrides):
    record = {
        "case_id": "case-x",
        "paper_id": "paper-1",
        "perturbation_types": ["D1"],
        "assigned": True,
        "injection_status": "success",
        "b0_status": "layout_evaluable",
        "candidate_status": "layout_evaluable",
        "vlm": {"effective": True},
    }
    record.update(overrides)
    return record


class FunnelTest(unittest.TestCase):
    def test_every_stage_counted_and_failures_visible(self) -> None:
        records = [
            _record(),
            _record(case_id="case-inject-fail", injection_status="target_not_found"),
            _record(case_id="case-b0-fail", b0_status="environment_ineligible"),
            _record(case_id="case-cand-fail", candidate_status="source_compile_error"),
            _record(case_id="case-vlm-junk", vlm={"effective": False}),
        ]
        funnel = build_funnel(records)
        self.assertEqual(funnel["overall"]["assigned"], 5)
        self.assertEqual(funnel["overall"]["injected"], 4)
        self.assertEqual(funnel["overall"]["b0_rendered"], 3)
        self.assertEqual(funnel["overall"]["candidate_rendered"], 2)
        self.assertEqual(funnel["overall"]["scored"], 1)
        self.assertEqual(funnel["quality_denominator"], 1)
        self.assertIn("injection:target_not_found", funnel["failure_reasons"])
        self.assertIn("b0:environment_ineligible", funnel["failure_reasons"])
        self.assertIn("candidate:source_compile_error", funnel["failure_reasons"])
        self.assertIn("vlm:invalid_response", funnel["failure_reasons"])

    def test_breakdown_by_perturbation_type(self) -> None:
        records = [
            _record(perturbation_types=["D1", "D2"]),
            _record(perturbation_types=["D2"]),
        ]
        funnel = build_funnel(records)
        self.assertEqual(funnel["by_perturbation"]["D1"]["scored"], 1)
        self.assertEqual(funnel["by_perturbation"]["D2"]["scored"], 2)

    def test_load_case_records_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cases.jsonl"
            path.write_text(
                json.dumps(_record()) + "\n" + json.dumps(_record(case_id="case-y")) + "\n",
                encoding="utf-8",
            )
            records = load_case_records(path)
            self.assertEqual(len(records), 2)


class PairedBootstrapTest(unittest.TestCase):
    def test_deterministic_with_seed(self):
        pairs = [("p1", 0.3), ("p2", -0.1), ("p3", 0.5), ("p4", 0.2)]
        first = paired_bootstrap_ci(pairs, iterations=500, seed=7)
        second = paired_bootstrap_ci(pairs, iterations=500, seed=7)
        self.assertEqual(first, second)

    def test_clustered_by_paper(self):
        # 同一论文 10 个扰动全部 +1，另一篇 1 个 -1：聚类后应接近均值而非被稀释
        pairs = [("paper-a", 1.0)] * 10 + [("paper-b", -1.0)]
        mean, _, _ = paired_bootstrap_ci(pairs, iterations=1000)
        self.assertAlmostEqual(mean, (10 * 1.0 - 1.0) / 11, places=6)


class SafetyCheckTest(unittest.TestCase):
    def test_regressions_detected(self):
        regressions = check_safety(
            {"compile_success": 0.98, "render_success": 0.95, "content_integrity_ok": 1.0, "page_budget_hit": 0.80},
            {"compile_success": 0.90, "render_success": 0.95, "content_integrity_ok": 1.0, "page_budget_hit": 0.82},
        )
        self.assertEqual(len(regressions), 1)
        self.assertIn("compile_success", regressions[0])


class DecideRegressionTest(unittest.TestCase):
    def _scores(self, deltas, papers=None):
        papers = papers or [f"p{i}" for i in range(len(deltas))]
        return [
            {"paper_id": paper, "case_id": f"case-{i}", "delta": delta}
            for i, (paper, delta) in enumerate(zip(papers, deltas))
        ]

    def test_safety_regression_vetoes(self):
        result = decide_regression(
            safety_v1={"compile_success": 0.99, "render_success": 0.99, "content_integrity_ok": 1.0, "page_budget_hit": 0.8},
            safety_v2={"compile_success": 0.85, "render_success": 0.99, "content_integrity_ok": 1.0, "page_budget_hit": 0.8},
            paired_scores=self._scores([0.5] * 50),
        )
        self.assertEqual(result["decision"], "REGRESSED")

    def test_clear_improvement(self):
        deltas = [0.35] * 30 + [0.30] * 30
        result = decide_regression(
            safety_v1={k: 1.0 for k in ("compile_success", "render_success", "content_integrity_ok")},
            safety_v2={k: 1.0 for k in ("compile_success", "render_success", "content_integrity_ok")},
            paired_scores=self._scores(deltas),
            minimum_detectable_delta=0.10,
        )
        self.assertEqual(result["decision"], "IMPROVED")

    def test_noisy_small_difference_is_inconclusive(self):
        # 52% 微弱优势：30 个 case 必须得到 INCONCLUSIVE
        deltas = [0.1] * 16 + [-0.1] * 14
        result = decide_regression(
            safety_v1={k: 1.0 for k in ("compile_success", "render_success", "content_integrity_ok")},
            safety_v2={k: 1.0 for k in ("compile_success", "render_success", "content_integrity_ok")},
            paired_scores=self._scores(deltas),
        )
        self.assertEqual(result["decision"], "INCONCLUSIVE")
        self.assertIn("crosses zero", " ".join(result["reasons"]))

    def test_manifest_incompatibility_invalidates_run(self):
        result = decide_regression(
            safety_v1={}, safety_v2={}, paired_scores=self._scores([0.5] * 10),
            manifest_compatible=False,
        )
        self.assertEqual(result["decision"], "INVALID_RUN")

    def test_no_scores_inconclusive(self):
        result = decide_regression(
            safety_v1={k: 1.0 for k in ("compile_success", "render_success")},
            safety_v2={k: 1.0 for k in ("compile_success", "render_success")},
            paired_scores=[],
        )
        self.assertEqual(result["decision"], "INCONCLUSIVE")


class ManifestTest(unittest.TestCase):
    def test_compatible_when_core_factors_match(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            base = dict(
                project_root=root,
                run_id="RUN-1",
                reference_version="v1",
                candidate_version="v2",
                judge_model_name="deepseek-v4.1-flash",
                judge_prompt_hash="abc123",
                benchmark_pack_hash="pack-1",
                injector_version="inj-1",
            )
            manifest_a = build_run_manifest(**base)
            manifest_b = build_run_manifest(**{**base, "run_id": "RUN-2"})
            result = check_manifest_compatibility(manifest_a, manifest_b)
            self.assertTrue(result["compatible"], result["differences"])

    def test_judge_change_requires_new_epoch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            base = dict(
                project_root=root,
                run_id="RUN-1",
                reference_version="v1",
                candidate_version="v2",
                judge_model_name="deepseek-v4.1-flash",
                judge_prompt_hash="abc123",
            )
            manifest_a = build_run_manifest(**base)
            manifest_b = build_run_manifest(**{**base, "judge_model_name": "other-model"})
            result = check_manifest_compatibility(manifest_a, manifest_b)
            self.assertFalse(result["compatible"])
            self.assertIn("judge", result["differences"])

    def test_dataset_hash_changes_flagged(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            data_file = root / "cases.jsonl"
            data_file.write_text("{}", encoding="utf-8")
            base = dict(
                project_root=root,
                run_id="RUN-1",
                reference_version="v1",
                candidate_version="v2",
                dataset_files=[data_file],
            )
            manifest_a = build_run_manifest(**base)
            data_file.write_text('{"changed": true}', encoding="utf-8")
            manifest_b = build_run_manifest(**{**base, "run_id": "RUN-2"})
            result = check_manifest_compatibility(manifest_a, manifest_b)
            self.assertFalse(result["compatible"])
            self.assertIn("dataset", result["differences"])


if __name__ == "__main__":
    unittest.main()
