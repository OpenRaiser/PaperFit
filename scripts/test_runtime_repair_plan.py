from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from orchestrator_runtime import OrchestratorRuntime  # noqa: E402
from repair_plan_generator import generate_repair_plan  # noqa: E402
from repair_plan_executor import _effective_changes  # noqa: E402
from repair_plan_executor import _build_float_defects  # noqa: E402
from repair_plan_executor import execute_repair_plan  # noqa: E402
from runtime_repair_plan import (  # noqa: E402
    attach_repair_plan_fingerprint,
    validate_repair_plan_freshness,
)
from space_util_fixers import fix_page_budget_excess  # noqa: E402
from state_manager import StateManager  # noqa: E402


class RuntimeRepairPlanTest(unittest.TestCase):
    def test_effective_changes_excludes_failed_and_noop_entries(self) -> None:
        report = {
            "changes": [
                {"success": True, "before": "a", "after": "b"},
                {"success": True, "before": "\\vspace{3pt}", "after": "\\vspace{3pt}"},
                {"success": False, "before": "\\bibliographystyle{ACM-Reference-Format}", "after": "\\bibliographystyle{abbrv}"},
            ]
        }

        self.assertEqual(_effective_changes(report), [{"success": True, "before": "a", "after": "b"}])

    def test_page_budget_excess_does_not_suggest_bibliography_style_change(self) -> None:
        tex = "\n".join(
            [
                "\\documentclass{article}",
                "\\begin{document}",
                "\\includegraphics[width=1.38\\linewidth]{figure.pdf}",
                "\\vspace{3pt}",
                "Body text.",
                "\\bibliographystyle{ACM-Reference-Format}",
                "\\bibliography{reference}",
                "\\end{document}",
            ]
        )

        updated, changes = fix_page_budget_excess(tex, current_pages=10, target_pages=9)

        self.assertIn("\\includegraphics[width=1.0\\linewidth]{figure.pdf}", updated)
        self.assertIn("\\vspace{2.4pt}", updated)
        self.assertIn("\\bibliographystyle{ACM-Reference-Format}", updated)
        self.assertNotIn("\\bibliographystyle{abbrv}", updated)
        self.assertEqual([c.object_name for c in changes], ["图片", "垂直间距"])

    def test_repair_plan_fingerprint_detects_source_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            (root / "main.tex").write_text(
                "\\documentclass{article}\n\\begin{document}\nOriginal\n\\end{document}\n",
                encoding="utf-8",
            )
            plan_path = root / "data" / "repair_plan.json"
            plan_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "summary": {"total_candidates": 1},
                        "candidates": [{"candidate_type": "global"}],
                    }
                ),
                encoding="utf-8",
            )

            plan = attach_repair_plan_fingerprint(
                project_root=root,
                main_tex="main.tex",
                repair_plan_path="data/repair_plan.json",
            )
            fresh = validate_repair_plan_freshness(
                project_root=root,
                main_tex="main.tex",
                repair_plan=plan,
            )
            self.assertTrue(fresh["fresh"])

            (root / "main.tex").write_text(
                "\\documentclass{article}\n\\begin{document}\nMutated\n\\end{document}\n",
                encoding="utf-8",
            )
            stale = validate_repair_plan_freshness(
                project_root=root,
                main_tex="main.tex",
                repair_plan=plan,
            )
            self.assertFalse(stale["fresh"])
            self.assertEqual(stale["status"], "stale")
            self.assertEqual(stale["changed_files"][0]["path"], "main.tex")

    def test_repair_plan_generator_attaches_candidate_risk(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            visual_path = root / "visual_signal_report.json"
            crossrefs_path = root / "crossrefs_report.json"
            output_path = root / "repair_plan.json"
            visual_path.write_text(json.dumps({"summary": {"pages_analyzed": 2}}), encoding="utf-8")
            crossrefs_path.write_text(
                json.dumps(
                    {
                        "distances": [
                            {
                                "label": "fig:near",
                                "float_type": "figure",
                                "severity": "major",
                                "line_distance": 45,
                                "section_distance": 0,
                            }
                        ],
                        "floats": [
                            {
                                "label": "fig:near",
                                "float_type": "figure",
                                "section": "Method",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            plan = generate_repair_plan(
                visual_signal_report=str(visual_path),
                output_path=str(output_path),
                crossrefs_report=str(crossrefs_path),
            )

            self.assertEqual(plan["summary"]["total_candidates"], 1)
            self.assertEqual(plan["candidates"][0]["risk"]["risk_level"], "medium")
            self.assertEqual(plan["candidates"][0]["risk"]["operation"], "float_placement")
            persisted = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertIn("risk", persisted["candidates"][0])

    def test_repair_plan_prioritizes_native_b2_overflow_width_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            visual_path = root / "visual_signal_report.json"
            crossrefs_path = root / "crossrefs_report.json"
            output_path = root / "repair_plan.json"
            visual_path.write_text(
                json.dumps(
                    {
                        "summary": {"pages_analyzed": 4},
                        "findings": [
                            {
                                "source": "pymupdf_native",
                                "page": 2,
                                "taxonomy_defect_id": "B2",
                                "defect_id": "B2-native-underfilled-width",
                                "category": "B",
                                "severity": "major",
                                "confidence": 0.88,
                                "description": "native figure_like uses 0.350 of inferred column width",
                                "bbox": [402, 245, 657, 409],
                                "metrics": {
                                    "subtype": "underfilled_width",
                                    "object_kind": "figure_like",
                                    "object_width_ratio": 0.35,
                                    "object_width_page_ratio": 0.1364,
                                    "width_context": "column",
                                },
                            },
                            {
                                "source": "pymupdf_native",
                                "page": 3,
                                "taxonomy_defect_id": "B2",
                                "defect_id": "B2-native-float-overflow-width",
                                "category": "B",
                                "severity": "major",
                                "confidence": 0.9,
                                "description": "native figure_like exceeds page right edge by 12.77pt",
                                "bbox": [976, 165, 1909, 758],
                                "metrics": {
                                    "subtype": "overflow_width",
                                    "object_kind": "figure_like",
                                    "overflow_pt": 12.774,
                                    "object_width_ratio": 1.2799,
                                    "object_width_page_ratio": 0.4988,
                                    "width_context": "column",
                                    "pdf_bbox": [319.5, 54.0, 624.774, 248.191],
                                },
                            }
                        ],
                        "object_pairings": [
                            {
                                "page": 2,
                                "object_kind": "figure_like",
                                "object_bbox": [402, 245, 657, 409],
                                "object_width_ratio": 0.35,
                            },
                            {
                                "page": 3,
                                "object_kind": "figure_like",
                                "object_bbox": [976, 165, 1909, 758],
                                "object_width_ratio": 1.2799,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            crossrefs_path.write_text(
                json.dumps(
                    {
                        "floats": [
                            {
                                "label": "fig:motivation",
                                "float_type": "figure",
                                "line_number": 168,
                                "width_spec": "0.35\\linewidth",
                                "section": "Introduction",
                            },
                            {
                                "label": "fig:problem",
                                "float_type": "figure",
                                "line_number": 202,
                                "width_spec": "1.28\\linewidth",
                                "section": "Preliminaries",
                            }
                        ],
                        "distances": [],
                    }
                ),
                encoding="utf-8",
            )

            plan = generate_repair_plan(
                visual_signal_report=str(visual_path),
                output_path=str(output_path),
                crossrefs_report=str(crossrefs_path),
            )

            self.assertGreaterEqual(plan["summary"]["total_candidates"], 1)
            first = plan["candidates"][0]
            self.assertEqual(first["defect_family"], "B2")
            self.assertEqual(first["target"]["label"], "fig:problem")
            self.assertEqual(first["visual_width_subtype"], "overflow_width")
            self.assertEqual(first["visual_overflow_pt"], 12.774)
            self.assertGreater(
                first["priority_score"],
                next(c for c in plan["candidates"] if c["target"].get("label") == "fig:motivation")["priority_score"],
            )

    def test_float_defect_selection_prefers_visual_b2_overflow_width(self) -> None:
        plan = {
            "candidates": [
                {
                    "candidate_type": "source_anchor",
                    "defect_family": "B2",
                    "priority_score": 70,
                    "target": {"label": "fig:source_small"},
                    "source_width_spec": "0.35\\linewidth",
                },
                {
                    "candidate_type": "object",
                    "defect_family": "B2",
                    "priority_score": 125,
                    "page": 2,
                    "target": {"label": "fig:visual_small"},
                    "source_width_spec": "0.35\\linewidth",
                    "visual_width_subtype": "underfilled_width",
                    "visual_object_width_ratio": 0.35,
                },
                {
                    "candidate_type": "object",
                    "defect_family": "B2",
                    "priority_score": 122,
                    "page": 3,
                    "target": {"label": "fig:problem"},
                    "source_width_spec": "1.28\\linewidth",
                    "visual_width_subtype": "overflow_width",
                    "visual_object_width_ratio": 1.2799,
                    "visual_overflow_pt": 12.774,
                },
            ]
        }

        defects = _build_float_defects(plan, max_candidates=1)

        self.assertEqual(len(defects), 1)
        self.assertEqual(defects[0]["object"], "fig:problem")
        self.assertEqual(defects[0]["visual_width_subtype"], "overflow_width")
        self.assertEqual(defects[0]["visual_overflow_pt"], 12.774)
        defects = _build_float_defects(plan, max_candidates=2)
        self.assertEqual(defects[1]["object"], "fig:visual_small")
        self.assertEqual(defects[1]["visual_width_subtype"], "underfilled_width")

    def test_execute_repair_plan_defers_overflow_when_float_candidate_selected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\n".join(
                    [
                        "\\documentclass{article}",
                        "\\begin{document}",
                        "\\begin{figure}",
                        "\\includegraphics[width=1.28\\linewidth]{figs/problem.pdf}",
                        "\\caption{Problem}",
                        "\\label{fig:problem}",
                        "\\end{figure}",
                        "\\begin{equation}",
                        "\\mathcal{L}_{\\mathrm{disturb}} = x_{BenchmarkEquationOverflowSequenceAlphaBetaGammaDeltaEpsilon}",
                        "\\end{equation}",
                        "\\end{document}",
                    ]
                ),
                encoding="utf-8",
            )
            plan_path = root / "repair_plan.json"
            output_path = root / "repair_execution_report.json"
            plan_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "candidates": [
                            {
                                "candidate_type": "object",
                                "defect_family": "B2",
                                "priority_score": 192,
                                "page": 3,
                                "target": {"label": "fig:problem"},
                                "source_width_spec": "1.28\\linewidth",
                                "visual_width_subtype": "overflow_width",
                                "visual_overflow_pt": 12.774,
                            },
                            {
                                "candidate_type": "object",
                                "defect_family": "D2",
                                "priority_score": 180,
                                "page": 3,
                                "line_number": 8,
                                "target": {"scope": "equation_overflow"},
                                "description": "BenchmarkEquationOverflowSequenceAlphaBetaGammaDeltaEpsilon",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch(
                "repair_plan_executor.execute_float_candidates",
                return_value={
                    "status": "success",
                    "changes": [{"before": "width=1.28\\linewidth", "after": "width=\\linewidth"}],
                    "unresolved": [],
                },
            ) as float_fix, mock.patch(
                "repair_plan_executor.execute_overflow_candidates",
                side_effect=AssertionError("overflow fixer must be deferred while float candidate is selected"),
            ):
                report = execute_repair_plan(
                    repair_plan_path=str(plan_path),
                    main_tex=str(main_tex),
                    output_path=str(output_path),
                    max_candidates=1,
                )

            float_fix.assert_called_once()
            self.assertEqual(len(report["selected_candidates"]["float"]), 1)
            self.assertEqual(report["selected_candidates"]["float"][0]["object"], "fig:problem")
            self.assertEqual(report["selected_candidates"]["overflow"], [])
            self.assertEqual(report["overflow_report"]["status"], "noop")
            self.assertIn("deferred D-class overflow", report["overflow_report"]["unresolved"][0])
            self.assertEqual(report["applied_count"], 1)

    def test_execute_repair_plan_blocks_stale_plan_before_executor(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\\documentclass{article}\n\\begin{document}\nOriginal\n\\end{document}\n",
                encoding="utf-8",
            )
            plan_path = root / "data" / "repair_plan.json"
            plan_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "summary": {"total_candidates": 1},
                        "candidates": [{"candidate_type": "global"}],
                    }
                ),
                encoding="utf-8",
            )
            attach_repair_plan_fingerprint(
                project_root=root,
                main_tex="main.tex",
                repair_plan_path=plan_path,
            )

            manager = StateManager(state_path=str(root / "data" / "state.json"))
            manager.init_state(main_tex="main.tex", task_type="full_vto")
            manager.update({"artifacts": {"repair_plan": "data/repair_plan.json"}})
            main_tex.write_text(
                "\\documentclass{article}\n\\begin{document}\nChanged\n\\end{document}\n",
                encoding="utf-8",
            )

            runtime = OrchestratorRuntime(state_path=str(root / "data" / "state.json"))
            cwd_before = Path.cwd()
            try:
                os.chdir(root)
                with mock.patch.object(
                    OrchestratorRuntime,
                    "_run_repair_plan_executor",
                    side_effect=AssertionError("stale repair plan must not execute"),
                ):
                    state = runtime.execute_repair_plan(main_tex="main.tex")
            finally:
                os.chdir(cwd_before)

            report = json.loads((root / "data" / "repair_execution_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "blocked_stale_repair_plan")
            self.assertEqual(state["repair_execution_summary"]["status"], "blocked_stale_repair_plan")
            self.assertEqual(state["next_actions"], ["Regenerate repair plan before applying source mutations"])


if __name__ == "__main__":
    unittest.main()
