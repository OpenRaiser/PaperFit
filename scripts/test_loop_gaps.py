from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from diagnosis_ingest import classify_compile_case, findings_to_candidates, parse_host_diagnosis  # noqa: E402
from repair_plan_executor import execute_repair_plan  # noqa: E402
from repair_plan_generator import _build_consistency_candidates, generate_repair_plan  # noqa: E402
from runtime_repair_risk import conflict_keys_for  # noqa: E402


TEX = r"""
\documentclass{article}
\usepackage{caption}
\begin{document}
\begin{table}
\resizebox{\linewidth}{!}{%
\begin{tabular}{ll}
a & b \\
\end{tabular}}
\caption{Results.}
\end{table}
\end{document}
"""


class ConsistencyAndDiagnosisTests(unittest.TestCase):
    def test_c3_does_not_become_a_resizebox_candidate(self) -> None:
        candidates = _build_consistency_candidates({"findings": [{"taxonomy_defect_id": "C3"}]})
        self.assertEqual([item["defect_family"] for item in candidates], ["C3"])

    def test_c3_inserts_caption_setup_without_removing_table_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tex = root / "main.tex"
            tex.write_text(TEX, encoding="utf-8")
            plan = root / "plan.json"
            plan.write_text(json.dumps({"candidates": [{"defect_family": "C3"}]}), encoding="utf-8")
            report = execute_repair_plan(str(plan), str(tex), str(root / "out.json"))
            updated = tex.read_text(encoding="utf-8")
            self.assertIn(r"\captionsetup{font=small,labelfont=bf,skip=6pt}", updated)
            self.assertIn(r"\resizebox", updated)
            self.assertIn("Results.", updated)
            self.assertGreaterEqual(report["applied_count"], 1)

    def test_json_diagnosis_without_anchor_is_review_only(self) -> None:
        parsed = parse_host_diagnosis(
            '{"findings":[{"page":7,"family":"D","subfamily":"D2","description":"formula","confidence":0.91}]}'
        )
        candidates = findings_to_candidates(parsed["findings"])
        self.assertEqual(parsed["parse_mode"], "json")
        self.assertTrue(candidates[0]["requires_human_review"])
        self.assertEqual(candidates[0]["source_anchor_status"], "unresolved")

    def test_anchored_diagnosis_can_enter_the_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            visual = root / "visual.json"
            visual.write_text(json.dumps({"findings": []}), encoding="utf-8")
            diagnosis = root / "diagnosis.json"
            diagnosis.write_text(
                json.dumps(
                    {
                        "findings": [
                            {
                                "page": 4,
                                "family": "D",
                                "subfamily": "D1",
                                "description": "overfull token",
                                "confidence": 0.9,
                                "line_number": 12,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            plan = generate_repair_plan(
                str(visual),
                str(root / "plan.json"),
                diagnosis_report=str(diagnosis),
            )
            diagnosis_candidates = [item for item in plan["candidates"] if item["candidate_type"] == "diagnosis"]
            self.assertEqual(len(diagnosis_candidates), 1)
            self.assertFalse(diagnosis_candidates[0]["requires_human_review"])
            self.assertIn("source-line:12", diagnosis_candidates[0]["conflict_keys"])

    def test_prose_fallback_is_not_an_automatic_edit(self) -> None:
        parsed = parse_host_diagnosis("第 7 页右栏 D2 公式挤穿")
        self.assertEqual(parsed["parse_mode"], "fallback")
        candidates = findings_to_candidates(parsed["findings"])
        self.assertTrue(candidates[0]["requires_human_review"])
        self.assertEqual(candidates[0]["proposed_action"], "review_only")

    def test_compile_cases_stay_distinct(self) -> None:
        self.assertEqual(
            classify_compile_case("File `figs/motivation.pdf' not found", success=False)["case_status"],
            "input_incomplete",
        )
        self.assertEqual(
            classify_compile_case("File `forloop.sty' not found", success=False)["case_status"],
            "environment_ineligible",
        )
        self.assertEqual(
            classify_compile_case("! LaTeX Error: something", success=False)["case_status"],
            "source_compile_error",
        )
        self.assertEqual(
            classify_compile_case("Overfull \\hbox (12pt too wide)", success=True)["case_status"],
            "layout_evaluable",
        )

    def test_nonoverlapping_batch_selects_both_families(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tex = root / "main.tex"
            tex.write_text("\\documentclass{article}\n\\begin{document}\nHi\n\\end{document}\n", encoding="utf-8")
            plan = root / "plan.json"
            plan.write_text(
                json.dumps(
                    {
                        "candidates": [
                            {
                                "candidate_type": "object",
                                "defect_family": "B2",
                                "page": 2,
                                "target": {"label": "fig:one", "object_kind": "figure_like"},
                                "visual_width_subtype": "overflow_width",
                                "conflict_keys": ["object:fig:one"],
                            },
                            {
                                "candidate_type": "log_warning",
                                "defect_family": "D2",
                                "page": 7,
                                "line_number": 40,
                                "target": {"scope": "equation_overflow"},
                                "description": "long formula",
                                "conflict_keys": ["source-line:40"],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch(
                "repair_plan_executor.execute_float_candidates",
                return_value={"status": "success", "changes": [{"before": "a", "after": "b"}], "unresolved": []},
            ) as float_fix, mock.patch(
                "repair_plan_executor.execute_overflow_candidates",
                return_value={"status": "success", "changes": [{"before": "c", "after": "d"}], "unresolved": []},
            ) as overflow_fix:
                report = execute_repair_plan(str(plan), str(tex), str(root / "out.json"), max_candidates=3)
            float_fix.assert_called_once()
            overflow_fix.assert_called_once()
            self.assertEqual(report["selection_policy"]["reason"], "nonconflicting_batch")
            self.assertEqual(len(report["selected_candidates"]["float"]), 1)
            self.assertEqual(len(report["selected_candidates"]["overflow"]), 1)

    def test_same_object_is_a_conflict(self) -> None:
        left = conflict_keys_for({"defect_family": "B2", "target": {"label": "fig:one"}, "candidate_type": "object"})
        right = conflict_keys_for(
            {"defect_family": "D1", "target": {"label": "fig:one"}, "line_number": 8, "candidate_type": "log_warning"}
        )
        self.assertTrue(set(left) & set(right))


if __name__ == "__main__":
    unittest.main()
