from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from PIL import Image

EVAL_DIR = Path(__file__).resolve().parent.parent / "evaluator"
LIB_DIR = EVAL_DIR / "lib"
SCRIPTS_DIR = Path(__file__).resolve().parent
for entry in (str(SCRIPTS_DIR), str(LIB_DIR), str(EVAL_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from regression.anchor_monitor import (  # noqa: E402
    check_anchor_drift,
    load_history,
    record_anchor_run,
)
from vlm_protocol import extract_json_with_mode  # noqa: E402


class ExtractJsonWithModeTest(unittest.TestCase):
    def test_strict_rescued_failed_classification(self):
        strict_payload, strict_mode = extract_json_with_mode('{"winner": "candidate"}')
        self.assertEqual(strict_mode, "strict")
        self.assertEqual(strict_payload["winner"], "candidate")

        fenced = "Here you go:\n```json\n{\"winner\": \"tie\"}\n```\n"
        rescued_payload, rescued_mode = extract_json_with_mode(fenced)
        self.assertEqual(rescued_mode, "rescued")
        self.assertEqual(rescued_payload["winner"], "tie")

        braced, braced_mode = extract_json_with_mode('noise before {"winner": "b0"} noise after')
        self.assertEqual(braced_mode, "rescued")
        self.assertEqual(braced["winner"], "b0")

        _, failed_mode = extract_json_with_mode("no json at all, just words")
        self.assertEqual(failed_mode, "failed")
        _, empty_mode = extract_json_with_mode("   ")
        self.assertEqual(empty_mode, "failed")

    def test_non_dict_json_is_failed(self):
        _, mode = extract_json_with_mode("[1, 2, 3]")
        self.assertEqual(mode, "failed")


class RevisionRunnerComplianceTest(unittest.TestCase):
    def test_summary_reports_json_compliance_and_mode(self):
        from evaluate_revision_pair import run_revision_evaluation

        responses = [
            json.dumps({"reference_defects": [], "candidate_defects": [], "winner": "candidate",
                        "repair_outcome": "clearly_improved",
                        "scores": {k: 4 for k in ("defect_resolution", "constraint_alignment", "visual_quality",
                                                  "new_defect_avoidance", "publication_readiness")},
                        "final_aesthetic_scores": {k: 4 for k in ("overall_professionalism", "space_utilization",
                                                                  "float_placement_quality", "typographic_consistency",
                                                                  "visual_balance")},
                        "confidence": 0.9, "summary": "ok"}),
            "garbage with no json",
        ]

        class MixedClient:
            def __init__(self):
                self.calls = 0

            def analyze(self, *, images, system_prompt, user_prompt):
                self.calls += 1
                return responses[min(self.calls, len(responses)) - 1], {"prompt_tokens": 1}

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for method, extra in (("v1", ()), ("v2", ("case-b",))):
                for case_id in ("AAAI/case-a", "AAAI/case-b"):
                    case = root / method / case_id
                    (case / "pages").mkdir(parents=True)
                    Image.new("RGB", (50, 60), "white").save(case / "pages" / "page_001.png")
                    (case / "reports").mkdir(parents=True)
                    (case / "reports" / "evaluation_report.json").write_text(
                        json.dumps({"compile_success": True, "render_success": True, "page_count": 5, "target_pages": 5}),
                        encoding="utf-8",
                    )

            summary = run_revision_evaluation(
                client=MixedClient(),
                reference_root=root / "v1",
                candidate_root=root / "v2",
                reference_method="v1",
                candidate_method="v2",
                output_dir=root / "out",
                case_ids=["AAAI/case-a", "AAAI/case-b"],
                case_root=None,
                max_image_dim=1800,
                swap_order=False,
            )

            self.assertEqual(summary["case_count"], 2)
            compliance = summary["json_compliance"]
            self.assertEqual(compliance["counts"]["strict"], 1)
            self.assertEqual(compliance["counts"]["failed"], 1)
            self.assertEqual(compliance["strict_rate"], 0.5)
            self.assertIsNotNone(compliance["warning"])


class AnchorMonitorTest(unittest.TestCase):
    def test_record_and_reload_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "anchor_history.json"
            record_anchor_run(path, {"a1": 3.2, "a2": 4.0}, judge_model="deepseek-v4.1-flash")
            record_anchor_run(path, {"a1": 3.3, "a2": 4.1})
            history = load_history(path)
            self.assertEqual(len(history), 2)
            self.assertEqual(history[0]["judge_model"], "deepseek-v4.1-flash")

    def test_insufficient_history_does_not_alarm(self):
        result = check_anchor_drift(
            [
                {"scores": {"a1": 3.0}},
                {"scores": {"a1": 1.0}},  # 即使剧烈变化
            ]
        )
        self.assertEqual(result["status"], "insufficient_history")
        self.assertFalse(result["judge_epoch_changed"])

    def test_stable_scores_no_alarm(self):
        # 带真实噪声的历史（std≈0.13）+ 落在噪声内的最新分：不应报警
        history = [{"scores": {"a1": value}} for value in (3.0, 3.2, 2.9, 3.1)] + [{"scores": {"a1": 3.05}}]
        result = check_anchor_drift(history)
        self.assertEqual(result["status"], "stable")
        self.assertFalse(result["judge_epoch_changed"])
        self.assertEqual(result["per_anchor"]["a1"]["status"], "stable")

    def test_mean_drift_above_threshold_alarms(self):
        history = [{"scores": {"a1": 3.0 + 0.01 * i}} for i in range(4)] + [{"scores": {"a1": 3.8}}]
        result = check_anchor_drift(history)
        self.assertEqual(result["status"], "drifted")
        self.assertTrue(result["judge_epoch_changed"])
        self.assertIn("epoch", result["note"])

    def test_sigma_drift_alarms_even_below_mean_threshold(self):
        # 极稳历史（std≈0.01）+ 0.1 漂移：低于 0.2 阈值但 >2σ
        history = [{"scores": {"a1": 3.00 + 0.01 * i}} for i in range(4)] + [{"scores": {"a1": 3.12}}]
        result = check_anchor_drift(history)
        self.assertEqual(result["status"], "drifted")


if __name__ == "__main__":
    unittest.main()
