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
from repair_plan_generator import _has_visual_b2_width_candidate  # noqa: E402
from repair_plan_generator import _summarize_b2_width_conversion  # noqa: E402
from repair_plan_generator import _summarize_b2_width_candidates  # noqa: E402
from repair_plan_generator import _summarize_targetable_b2_width_candidates  # noqa: E402
from repair_plan_generator import _summarize_untargetable_b2_width_candidates  # noqa: E402
from repair_plan_generator import _summarize_visual_b2_width_findings  # noqa: E402
from repair_plan_generator import generate_repair_plan  # noqa: E402
from repair_plan_executor import _effective_changes  # noqa: E402
from repair_plan_executor import _build_float_defects  # noqa: E402
from repair_plan_executor import _summarize_selected_b2_width_candidates  # noqa: E402
from repair_plan_executor import execute_repair_plan  # noqa: E402
from runtime_repair_plan import (  # noqa: E402
    attach_repair_plan_fingerprint,
    validate_repair_plan_freshness,
)
from space_util_fixers import fix_page_budget_excess  # noqa: E402
from state_manager import StateManager  # noqa: E402


class RuntimeRepairPlanTest(unittest.TestCase):
    def test_visual_b2_presence_ignores_source_only_narrow_width(self) -> None:
        candidates = [
            {
                "candidate_type": "source_anchor",
                "defect_family": "B2",
                "source_width_spec": "0.35\\linewidth",
                "target": {"label": "fig:small"},
            },
            {
                "candidate_type": "log_warning",
                "defect_family": "D1",
                "priority_score": 95,
                "overflow_amount": 12.0,
            },
        ]

        self.assertFalse(_has_visual_b2_width_candidate(candidates))

        candidates[0]["visual_width_subtype"] = "underfilled_width"

        self.assertTrue(_has_visual_b2_width_candidate(candidates))

    def test_b2_width_candidate_summary_counts_only_visual_subtypes(self) -> None:
        summary = _summarize_b2_width_candidates(
            [
                {
                    "defect_family": "B2",
                    "visual_width_subtype": "overflow_width",
                    "target": {"label": "fig:wide", "object_kind": "figure_like"},
                },
                {
                    "defect_family": "B2",
                    "visual_width_subtype": "underfilled_width",
                    "target": {"label": "tab:small", "object_kind": "table_like"},
                },
                {
                    "defect_family": "B2",
                    "source_width_spec": "0.35\\linewidth",
                    "target": {"label": "fig:source_only", "object_kind": "figure_like"},
                },
            ]
        )

        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["by_subtype"], {"overflow_width": 1, "underfilled_width": 1})
        self.assertEqual(summary["by_object_kind"], {"figure_like": 1, "table_like": 1})
        self.assertEqual(summary["labels"], ["fig:wide", "tab:small"])

    def test_b2_targetable_candidate_summary_excludes_untargetable_visual_b2(self) -> None:
        summary = _summarize_targetable_b2_width_candidates(
            [
                {
                    "defect_family": "B2",
                    "visual_width_subtype": "overflow_width",
                    "target": {"label": "wide-results", "object_kind": "table_like"},
                },
                {
                    "defect_family": "B2",
                    "visual_width_subtype": "overflow_width",
                    "target": {"object_kind": "figure_like"},
                },
                {
                    "defect_family": "B2",
                    "visual_width_subtype": "underfilled_width",
                    "target": {"label": "source-only"},
                },
            ]
        )

        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["by_subtype"], {"overflow_width": 1})
        self.assertEqual(summary["by_object_kind"], {"table_like": 1})
        self.assertEqual(summary["labels"], ["wide-results"])

    def test_b2_untargetable_candidate_summary_reports_reasons(self) -> None:
        summary = _summarize_untargetable_b2_width_candidates(
            [
                {
                    "defect_family": "B2",
                    "visual_width_subtype": "overflow_width",
                    "target": {"label": "wide-results", "object_kind": "table_like"},
                },
                {
                    "defect_family": "B2",
                    "visual_width_subtype": "overflow_width",
                    "target": {"object_kind": "figure_like"},
                },
                {
                    "defect_family": "B2",
                    "visual_width_subtype": "underfilled_width",
                    "target": {"label": "source-only"},
                },
            ]
        )

        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["by_subtype"], {"overflow_width": 1, "underfilled_width": 1})
        self.assertEqual(
            summary["by_reason"],
            {"missing_label": 1, "unsupported_label_or_object_kind": 1},
        )
        self.assertEqual(summary["labels"], ["source-only"])

    def test_visual_b2_width_finding_summary_prefers_existing_summary(self) -> None:
        summary = _summarize_visual_b2_width_findings(
            {
                "summary": {
                    "b2_width_findings": {
                        "total": 3,
                        "by_subtype": {"overflow_width": 2, "underfilled_width": 1},
                        "by_object_kind": {"figure_like": 3},
                        "pages": [3, 4],
                        "finding_ids": ["B2-native-figure-overflow-width"],
                    }
                },
                "findings": [],
            }
        )

        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["by_subtype"], {"overflow_width": 2, "underfilled_width": 1})
        self.assertEqual(summary["pages"], [3, 4])
        self.assertEqual(summary["finding_ids"], ["B2-native-figure-overflow-width"])

    def test_b2_width_conversion_reports_only_unmatched_findings(self) -> None:
        visual_report = {
            "findings": [
                {
                    "taxonomy_defect_id": "B2",
                    "defect_id": "B2-native-figure-overflow-width",
                    "page": 3,
                    "metrics": {
                        "subtype": "overflow_width",
                        "object_kind": "figure_like",
                        "pdf_bbox": [10.001, 20.0, 310.0, 220.0],
                    },
                },
                {
                    "taxonomy_defect_id": "B2",
                    "defect_id": "B2-native-table-underfilled-width",
                    "page": 4,
                    "metrics": {
                        "subtype": "underfilled_width",
                        "object_kind": "table_like",
                        "pdf_bbox": [40.0, 50.0, 140.0, 120.0],
                    },
                },
            ]
        }
        candidates = [
            {
                "defect_family": "B2",
                "page": 3,
                "visual_width_subtype": "overflow_width",
                "visual_pdf_bbox": [10.0, 20.0, 310.0, 220.0],
                "target": {"label": "fig:wide", "object_kind": "figure_like"},
            }
        ]

        summary = _summarize_b2_width_conversion(visual_report, candidates)

        self.assertEqual(summary["unmatched_count"], 1)
        self.assertEqual(summary["unmatched_pages"], [4])
        self.assertEqual(summary["unmatched_finding_ids"], ["B2-native-table-underfilled-width"])

    def test_b2_width_conversion_clears_summary_only_pages_when_fully_matched(self) -> None:
        visual_report = {
            "summary": {
                "b2_width_findings": {
                    "total": 1,
                    "pages": [3],
                    "finding_ids": ["B2-native-figure-overflow-width"],
                }
            },
            "findings": [],
        }
        candidates = [
            {
                "defect_family": "B2",
                "visual_width_subtype": "overflow_width",
                "target": {"label": "fig:wide", "object_kind": "figure_like"},
            }
        ]

        summary = _summarize_b2_width_conversion(visual_report, candidates)

        self.assertEqual(summary["unmatched_count"], 0)
        self.assertEqual(summary["unmatched_pages"], [])
        self.assertEqual(summary["unmatched_finding_ids"], [])

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

    def test_repair_plan_generator_attaches_candidate_risk_to_visually_correlated_crossref(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            visual_path = root / "visual_signal_report.json"
            crossrefs_path = root / "crossrefs_report.json"
            output_path = root / "repair_plan.json"
            visual_path.write_text(
                json.dumps(
                    {
                        "summary": {"pages_analyzed": 2},
                        "findings": [
                            {
                                "page": 2,
                                "taxonomy_defect_id": "B3",
                                "severity": "major",
                                "description": "float placement pressure",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
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

    def test_repair_plan_suppresses_source_only_crossref_without_visual_pressure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            visual_path = root / "visual_signal_report.json"
            crossrefs_path = root / "crossrefs_report.json"
            output_path = root / "repair_plan.json"
            visual_path.write_text(
                json.dumps(
                    {
                        "summary": {"pages_analyzed": 11},
                        "findings": [
                            {
                                "page": 11,
                                "taxonomy_defect_id": "A2",
                                "severity": "minor",
                                "description": "last-page trailing whitespace",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            crossrefs_path.write_text(
                json.dumps(
                    {
                        "distances": [
                            {
                                "label": "tab:far",
                                "float_type": "table",
                                "severity": "major",
                                "line_distance": 57,
                                "section_distance": 2,
                            }
                        ],
                        "floats": [
                            {
                                "label": "tab:far",
                                "float_type": "table",
                                "section": "Results",
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

            self.assertEqual(plan["summary"]["source_anchor_candidates"], 0)
            self.assertFalse(any(candidate["defect_family"] == "B1" for candidate in plan["candidates"]))

    def test_repair_plan_suppresses_source_only_width_without_visual_b2(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            visual_path = root / "visual_signal_report.json"
            crossrefs_path = root / "crossrefs_report.json"
            output_path = root / "repair_plan.json"
            visual_path.write_text(json.dumps({"summary": {"pages_analyzed": 4}, "findings": []}), encoding="utf-8")
            crossrefs_path.write_text(
                json.dumps(
                    {
                        "floats": [
                            {
                                "label": "fig:small_by_design",
                                "float_type": "figure",
                                "line_number": 42,
                                "width_spec": "0.35\\linewidth",
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

            self.assertEqual(plan["summary"]["source_anchor_candidates"], 0)
            self.assertFalse(any(candidate["defect_family"] == "B2" for candidate in plan["candidates"]))

    def test_repair_plan_does_not_add_unrelated_source_width_when_visual_b2_exists(self) -> None:
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
                                "page": 3,
                                "taxonomy_defect_id": "B2",
                                "defect_id": "B2-native-float-overflow-width",
                                "category": "B",
                                "severity": "major",
                                "confidence": 0.9,
                                "bbox": [976, 165, 1909, 758],
                                "metrics": {
                                    "subtype": "overflow_width",
                                    "object_kind": "figure_like",
                                    "overflow_pt": 12.774,
                                    "object_width_ratio": 1.28,
                                    "width_context": "column",
                                },
                            }
                        ],
                        "object_pairings": [
                            {
                                "page": 3,
                                "object_kind": "figure_like",
                                "object_bbox": [976, 165, 1909, 758],
                                "object_width_ratio": 1.28,
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
                                "label": "fig:overflow",
                                "float_type": "figure",
                                "line_number": 120,
                                "width_spec": "1.28\\linewidth",
                            },
                            {
                                "label": "fig:small_by_design",
                                "float_type": "figure",
                                "line_number": 240,
                                "width_spec": "0.35\\linewidth",
                            },
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

            b2_candidates = [candidate for candidate in plan["candidates"] if candidate["defect_family"] == "B2"]
            self.assertEqual(len(b2_candidates), 1)
            self.assertEqual(b2_candidates[0]["target"]["label"], "fig:overflow")
            self.assertEqual(b2_candidates[0]["candidate_type"], "object")
            self.assertEqual(b2_candidates[0]["visual_width_subtype"], "overflow_width")
            self.assertEqual(plan["summary"]["b2_width_findings"]["total"], 1)
            self.assertEqual(plan["summary"]["b2_width_candidates"]["total"], 1)
            self.assertEqual(plan["summary"]["b2_width_candidates"]["by_subtype"], {"overflow_width": 1})
            self.assertEqual(plan["summary"]["b2_width_unmatched_findings"], 0)
            self.assertEqual(plan["summary"]["b2_width_unmatched_pages"], [])
            self.assertEqual(plan["summary"]["b2_width_unmatched_finding_ids"], [])
            self.assertFalse(
                any((candidate.get("target") or {}).get("label") == "fig:small_by_design" for candidate in plan["candidates"])
            )

    def test_repair_plan_suppresses_underfull_only_log_without_visual_pressure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            visual_path = root / "visual_signal_report.json"
            rule_path = root / "rule_report.json"
            output_path = root / "repair_plan.json"
            visual_path.write_text(
                json.dumps(
                    {
                        "summary": {"pages_analyzed": 11},
                        "findings": [
                            {
                                "page": 11,
                                "taxonomy_defect_id": "A2",
                                "severity": "minor",
                                "description": "last-page trailing whitespace",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            rule_path.write_text(
                json.dumps({"summary": {"underfull_hbox_total": 5}}),
                encoding="utf-8",
            )

            plan = generate_repair_plan(
                visual_signal_report=str(visual_path),
                output_path=str(output_path),
                rule_report=str(rule_path),
            )

            self.assertEqual(plan["summary"]["total_candidates"], 0)
            self.assertEqual(plan["candidates"], [])

    def test_repair_plan_keeps_underfull_log_with_visual_ac_pressure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            visual_path = root / "visual_signal_report.json"
            rule_path = root / "rule_report.json"
            output_path = root / "repair_plan.json"
            visual_path.write_text(
                json.dumps(
                    {
                        "summary": {"pages_analyzed": 4},
                        "findings": [
                            {
                                "page": 3,
                                "taxonomy_defect_id": "A1",
                                "severity": "major",
                                "description": "widow/orphan pressure",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            rule_path.write_text(
                json.dumps({"summary": {"underfull_hbox_total": 2}}),
                encoding="utf-8",
            )

            plan = generate_repair_plan(
                visual_signal_report=str(visual_path),
                output_path=str(output_path),
                rule_report=str(rule_path),
            )

            self.assertEqual(plan["summary"]["total_candidates"], 1)
            self.assertEqual(plan["candidates"][0]["defect_family"], "A/C")

    def test_repair_plan_prioritizes_urgent_d_over_c4_when_no_b2_width_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            visual_path = root / "visual_signal_report.json"
            crossrefs_path = root / "crossrefs_report.json"
            rule_path = root / "rule_report.json"
            output_path = root / "repair_plan.json"
            visual_path.write_text(
                json.dumps(
                    {
                        "summary": {"pages_analyzed": 4},
                        "priority_objects": [
                            {
                                "page": 4,
                                "object_kind": "figure_like",
                                "bbox": [165, 177, 894, 339],
                                "priority_score": 425,
                                "severity": "minor",
                                "reason": "caption_gap:460",
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
                                "line_number": 169,
                                "width_spec": "\\linewidth",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            rule_path.write_text(
                json.dumps(
                    {
                        "summary": {"overfull_hbox_total": 1},
                        "overfull_hbox": [
                            {
                                "subtype": "paragraph",
                                "overflow_pt": 13.21004,
                                "lines": "221",
                                "severity": "major",
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
                rule_report=str(rule_path),
            )

            self.assertGreaterEqual(plan["summary"]["total_candidates"], 1)
            self.assertEqual(plan["candidates"][0]["defect_family"], "D1")
            self.assertEqual(plan["candidates"][0]["line_number"], 221)
            self.assertFalse(any(candidate["defect_family"] == "C4" for candidate in plan["candidates"]))

    def test_repair_plan_prioritizes_urgent_d_over_untargetable_visual_b2(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            visual_path = root / "visual_signal_report.json"
            rule_path = root / "rule_report.json"
            output_path = root / "repair_plan.json"
            visual_path.write_text(
                json.dumps(
                    {
                        "summary": {"pages_analyzed": 4},
                        "findings": [
                            {
                                "source": "pymupdf_native",
                                "page": 3,
                                "taxonomy_defect_id": "B2",
                                "defect_id": "B2-native-figure-overflow-width",
                                "category": "B",
                                "severity": "major",
                                "confidence": 0.9,
                                "bbox": [976, 165, 1909, 758],
                                "metrics": {
                                    "subtype": "overflow_width",
                                    "object_kind": "figure_like",
                                    "overflow_pt": 12.7,
                                    "object_width_ratio": 1.28,
                                    "width_context": "column",
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            rule_path.write_text(
                json.dumps(
                    {
                        "summary": {"overfull_hbox_total": 1},
                        "overfull_hbox": [
                            {
                                "subtype": "paragraph",
                                "overflow_pt": 13.21004,
                                "lines": "221",
                                "severity": "major",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            plan = generate_repair_plan(
                visual_signal_report=str(visual_path),
                output_path=str(output_path),
                rule_report=str(rule_path),
            )

            self.assertEqual(plan["summary"]["b2_width_findings"]["total"], 1)
            self.assertEqual(plan["summary"]["b2_width_candidates"]["total"], 0)
            self.assertEqual(plan["summary"]["b2_width_targetable_candidates"]["total"], 0)
            self.assertEqual(plan["candidates"][0]["defect_family"], "D1")
            self.assertEqual(plan["candidates"][0]["line_number"], 221)

    def test_repair_plan_does_not_promote_caption_gap_hint_without_c4_finding(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            visual_path = root / "visual_signal_report.json"
            crossrefs_path = root / "crossrefs_report.json"
            output_path = root / "repair_plan.json"
            visual_path.write_text(
                json.dumps(
                    {
                        "summary": {"pages_analyzed": 4},
                        "priority_objects": [
                            {
                                "page": 4,
                                "object_kind": "figure_like",
                                "bbox": [165, 177, 894, 339],
                                "priority_score": 425,
                                "severity": "minor",
                                "reason": "caption_gap:460",
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
                                "line_number": 169,
                                "width_spec": "\\linewidth",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            plan = generate_repair_plan(
                visual_signal_report=str(visual_path),
                output_path=str(output_path),
                crossrefs_report=str(crossrefs_path),
            )

            self.assertEqual(plan["summary"]["total_candidates"], 0)

    def test_repair_plan_does_not_expand_unmatched_b3_to_source_order_clusters(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            visual_path = root / "visual_signal_report.json"
            crossrefs_path = root / "crossrefs_report.json"
            output_path = root / "repair_plan.json"
            visual_path.write_text(
                json.dumps(
                    {
                        "summary": {"pages_analyzed": 8},
                        "findings": [
                            {
                                "page": 7,
                                "taxonomy_defect_id": "B3",
                                "defect_id": "B3-float-clustering",
                                "severity": "minor",
                                "description": "3 close float blocks",
                            }
                        ],
                        "object_pairings": [],
                    }
                ),
                encoding="utf-8",
            )
            crossrefs_path.write_text(
                json.dumps(
                    {
                        "floats": [
                            {"label": "fig:a", "float_type": "figure", "line_number": 10},
                            {"label": "fig:b", "float_type": "figure", "line_number": 20},
                            {"label": "fig:c", "float_type": "figure", "line_number": 30},
                            {"label": "fig:d", "float_type": "figure", "line_number": 40},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            plan = generate_repair_plan(
                visual_signal_report=str(visual_path),
                output_path=str(output_path),
                crossrefs_report=str(crossrefs_path),
            )

            self.assertFalse(any(candidate["defect_family"] == "B3" for candidate in plan["candidates"]))

    def test_repair_plan_does_not_repair_minor_tail_findings_without_page_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            visual_path = root / "visual_signal_report.json"
            crossrefs_path = root / "crossrefs_report.json"
            output_path = root / "repair_plan.json"
            visual_path.write_text(
                json.dumps(
                    {
                        "summary": {"pages_analyzed": 11},
                        "findings": [
                            {
                                "page": 11,
                                "taxonomy_defect_id": "A2",
                                "defect_id": "A2-trailing-whitespace",
                                "severity": "minor",
                                "description": "last page trailing whitespace",
                                "metrics": {"whitespace_ratio": 0.24},
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
                            {"label": "fig:a", "float_type": "figure", "line_number": 10},
                            {"label": "fig:b", "float_type": "figure", "line_number": 20},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            plan = generate_repair_plan(
                visual_signal_report=str(visual_path),
                output_path=str(output_path),
                crossrefs_report=str(crossrefs_path),
            )

            self.assertFalse(any(candidate.get("candidate_type") == "tail_float_packing" for candidate in plan["candidates"]))
            self.assertFalse(any(candidate["defect_family"] == "A2" for candidate in plan["candidates"]))

    def test_repair_plan_does_not_pack_tail_floats_from_final_page_a4_without_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            visual_path = root / "visual_signal_report.json"
            crossrefs_path = root / "crossrefs_report.json"
            output_path = root / "repair_plan.json"
            visual_path.write_text(
                json.dumps(
                    {
                        "summary": {"pages_analyzed": 11},
                        "findings": [
                            {
                                "page": 11,
                                "taxonomy_defect_id": "A4",
                                "defect_id": "A4-column-imbalance",
                                "severity": "major",
                                "description": "final page column imbalance",
                                "metrics": {"height_diff_ratio": 0.85},
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
                            {"label": "fig:a", "float_type": "figure", "line_number": 10},
                            {"label": "fig:b", "float_type": "figure", "line_number": 20},
                            {"label": "tab:c", "float_type": "table", "line_number": 30},
                            {"label": "tab:d", "float_type": "table", "line_number": 40},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            plan = generate_repair_plan(
                visual_signal_report=str(visual_path),
                output_path=str(output_path),
                crossrefs_report=str(crossrefs_path),
            )

            self.assertFalse(any(candidate.get("candidate_type") == "tail_float_packing" for candidate in plan["candidates"]))
            self.assertTrue(any(candidate["defect_family"] == "A4" for candidate in plan["candidates"]))

    def test_repair_plan_keeps_tail_float_packing_for_explicit_final_page_b3(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            visual_path = root / "visual_signal_report.json"
            crossrefs_path = root / "crossrefs_report.json"
            output_path = root / "repair_plan.json"
            visual_path.write_text(
                json.dumps(
                    {
                        "summary": {"pages_analyzed": 11},
                        "findings": [
                            {
                                "page": 11,
                                "taxonomy_defect_id": "B3",
                                "defect_id": "B3-float-clustering",
                                "severity": "major",
                                "description": "final page float cluster",
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
                            {"label": "fig:a", "float_type": "figure", "line_number": 10},
                            {"label": "fig:b", "float_type": "figure", "line_number": 20},
                            {"label": "tab:c", "float_type": "table", "line_number": 30},
                            {"label": "tab:d", "float_type": "table", "line_number": 40},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            plan = generate_repair_plan(
                visual_signal_report=str(visual_path),
                output_path=str(output_path),
                crossrefs_report=str(crossrefs_path),
            )

            self.assertTrue(any(candidate.get("candidate_type") == "tail_float_packing" for candidate in plan["candidates"]))

    def test_repair_plan_does_not_treat_near_full_width_hint_as_b2(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            visual_path = root / "visual_signal_report.json"
            crossrefs_path = root / "crossrefs_report.json"
            output_path = root / "repair_plan.json"
            visual_path.write_text(
                json.dumps(
                    {
                        "summary": {"pages_analyzed": 4},
                        "priority_objects": [
                            {
                                "page": 4,
                                "object_kind": "figure_like",
                                "bbox": [1006, 176, 1683, 720],
                                "priority_score": 31,
                                "severity": "minor",
                                "reason": "low_width_ratio:0.929",
                                "object_width_ratio": 0.9287,
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
                                "label": "fig:near_full_width",
                                "float_type": "figure",
                                "line_number": 200,
                                "width_spec": "\\linewidth",
                                "section": "Method",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            plan = generate_repair_plan(
                visual_signal_report=str(visual_path),
                output_path=str(output_path),
                crossrefs_report=str(crossrefs_path),
            )

            self.assertEqual(plan["summary"]["total_candidates"], 0)
            self.assertEqual(plan["candidates"], [])

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

    def test_visual_b2_width_matching_prefers_source_width_over_noisy_object_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            visual_path = root / "visual_signal_report.json"
            crossrefs_path = root / "crossrefs_report.json"
            output_path = root / "repair_plan.json"
            visual_path.write_text(
                json.dumps(
                    {
                        "summary": {"pages_analyzed": 4},
                        "priority_objects": [
                            {
                                "page": 2,
                                "object_kind": "figure_like",
                                "bbox": [402, 245, 657, 409],
                                "priority_score": 97,
                                "severity": "major",
                                "reason": "low_width_ratio:0.350",
                                "object_width_ratio": 0.35,
                            },
                            {
                                "page": 4,
                                "object_kind": "figure_like",
                                "bbox": [194, 870, 872, 1413],
                                "priority_score": 39,
                                "severity": "major",
                                "reason": "low_width_ratio:0.930",
                                "object_width_ratio": 0.93,
                            },
                        ],
                        "findings": [
                            {
                                "source": "pymupdf_native",
                                "page": 4,
                                "taxonomy_defect_id": "B2",
                                "defect_id": "B2-native-float-overflow-width",
                                "category": "B",
                                "severity": "major",
                                "confidence": 0.9,
                                "bbox": [976, 939, 1909, 1357],
                                "metrics": {
                                    "subtype": "overflow_width",
                                    "object_kind": "figure_like",
                                    "overflow_pt": 12.777,
                                    "object_width_ratio": 1.28,
                                    "object_width_page_ratio": 0.4887,
                                    "width_context": "column",
                                    "pdf_bbox": [319.5, 307.446, 624.777, 444.264],
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
                                "object_bbox": [976, 165, 1705, 629],
                                "object_width_ratio": 1.0,
                            },
                            {
                                "page": 4,
                                "object_kind": "figure_like",
                                "object_bbox": [194, 870, 872, 1413],
                                "object_width_ratio": 0.93,
                            },
                            {
                                "page": 4,
                                "object_kind": "figure_like",
                                "object_bbox": [976, 939, 1909, 1357],
                                "object_width_ratio": 1.28,
                            },
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
                                "line_number": 169,
                                "width_spec": "0.35\\linewidth",
                                "section": "Introduction",
                            },
                            {
                                "label": "fig:problem",
                                "float_type": "figure",
                                "line_number": 203,
                                "width_spec": "\\linewidth",
                                "section": "Preliminaries",
                            },
                            {
                                "label": "fig:overview",
                                "float_type": "figure",
                                "line_number": 238,
                                "width_spec": "0.98\\linewidth",
                                "section": "Overview",
                            },
                            {
                                "label": "fig:edge",
                                "float_type": "figure",
                                "line_number": 254,
                                "width_spec": "1.28\\linewidth",
                                "section": "Protein Complex Invariant Embedding",
                            },
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

            overflow = next(
                candidate
                for candidate in plan["candidates"]
                if candidate.get("visual_width_subtype") == "overflow_width"
            )
            self.assertEqual(overflow["target"]["label"], "fig:edge")
            self.assertEqual(overflow["source_width_spec"], "1.28\\linewidth")
            self.assertEqual(overflow["match_strategy"], "source_width_visual_b2")

    def test_visual_b2_without_page_or_width_match_does_not_use_global_label_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            visual_path = root / "visual_signal_report.json"
            crossrefs_path = root / "crossrefs_report.json"
            output_path = root / "repair_plan.json"
            visual_path.write_text(
                json.dumps(
                    {
                        "summary": {"pages_analyzed": 6},
                        "findings": [
                            {
                                "source": "pymupdf_native",
                                "page": 5,
                                "taxonomy_defect_id": "B2",
                                "defect_id": "B2-native-float-overflow-width",
                                "category": "B",
                                "severity": "major",
                                "confidence": 0.9,
                                "bbox": [976, 600, 1909, 980],
                                "metrics": {
                                    "subtype": "overflow_width",
                                    "object_kind": "figure_like",
                                    "overflow_pt": 14.2,
                                    "object_width_ratio": 1.28,
                                    "width_context": "column",
                                },
                            }
                        ],
                        "object_pairings": [
                            {
                                "page": 2,
                                "object_kind": "figure_like",
                                "object_bbox": [402, 245, 657, 409],
                                "object_width_ratio": 0.35,
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
                                "label": "fig:small",
                                "float_type": "figure",
                                "line_number": 80,
                                "width_spec": "0.35\\linewidth",
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

            self.assertFalse(any(candidate["defect_family"] == "B2" for candidate in plan["candidates"]))
            self.assertEqual(plan["summary"]["b2_width_findings"]["total"], 1)
            self.assertEqual(plan["summary"]["b2_width_candidates"]["total"], 0)
            self.assertEqual(plan["summary"]["b2_width_unmatched_findings"], 1)
            self.assertEqual(plan["summary"]["b2_width_unmatched_pages"], [5])
            self.assertEqual(
                plan["summary"]["b2_width_unmatched_finding_ids"],
                ["B2-native-float-overflow-width"],
            )

    def test_visual_table_b2_generates_table_object_candidate(self) -> None:
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
                                "page": 4,
                                "taxonomy_defect_id": "B2",
                                "defect_id": "B2-native-table-overflow-width",
                                "category": "B",
                                "severity": "major",
                                "confidence": 0.91,
                                "bbox": [120, 640, 930, 1160],
                                "metrics": {
                                    "subtype": "overflow_width",
                                    "object_kind": "table_like",
                                    "overflow_pt": 18.4,
                                    "object_width_ratio": 1.18,
                                    "object_width_page_ratio": 0.48,
                                    "width_context": "column",
                                },
                            }
                        ],
                        "object_pairings": [
                            {
                                "page": 4,
                                "object_kind": "table_like",
                                "object_bbox": [120, 640, 930, 1160],
                                "object_width_ratio": 1.18,
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
                                "label": "tab:wide",
                                "float_type": "table",
                                "line_number": 320,
                                "table_env": "tabular",
                                "tabcolsep": "6pt",
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

            b2_candidates = [candidate for candidate in plan["candidates"] if candidate["defect_family"] == "B2"]
            self.assertEqual(len(b2_candidates), 1)
            self.assertEqual(b2_candidates[0]["target"]["label"], "tab:wide")
            self.assertEqual(b2_candidates[0]["target"]["object_kind"], "table_like")
            self.assertEqual(b2_candidates[0]["visual_width_subtype"], "overflow_width")
            self.assertEqual(b2_candidates[0]["visual_overflow_pt"], 18.4)
            self.assertEqual(b2_candidates[0]["source_table_env"], "tabular")

    def test_visual_table_b2_uses_kind_specific_source_order_when_pairings_are_figure_only(self) -> None:
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
                                "page": 4,
                                "taxonomy_defect_id": "B2",
                                "defect_id": "B2-native-table-overflow-width",
                                "category": "B",
                                "severity": "major",
                                "confidence": 0.91,
                                "bbox": [120, 640, 930, 1160],
                                "metrics": {
                                    "subtype": "overflow_width",
                                    "object_kind": "table_like",
                                    "overflow_pt": 18.4,
                                    "object_width_ratio": 1.18,
                                    "object_width_page_ratio": 0.48,
                                    "width_context": "column",
                                    "pdf_bbox": [40.0, 210.0, 320.0, 380.0],
                                },
                            }
                        ],
                        "object_pairings": [
                            {
                                "page": 2,
                                "object_kind": "figure_like",
                                "object_bbox": [402, 245, 657, 409],
                                "object_width_ratio": 0.35,
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
                                "label": "fig:small",
                                "float_type": "figure",
                                "line_number": 120,
                                "width_spec": "0.35\\linewidth",
                            },
                            {
                                "label": "tab:wide",
                                "float_type": "table",
                                "line_number": 320,
                                "table_env": "tabular",
                                "tabcolsep": "6pt",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            plan = generate_repair_plan(
                visual_signal_report=str(visual_path),
                output_path=str(output_path),
                crossrefs_report=str(crossrefs_path),
            )

            b2_candidates = [candidate for candidate in plan["candidates"] if candidate["defect_family"] == "B2"]
            self.assertEqual(len(b2_candidates), 1)
            self.assertEqual(b2_candidates[0]["target"]["label"], "tab:wide")
            self.assertEqual(b2_candidates[0]["target"]["object_kind"], "table_like")
            self.assertEqual(b2_candidates[0]["match_strategy"], "source_order_visual_b2")
            self.assertEqual(b2_candidates[0]["source_table_env"], "tabular")
            self.assertEqual(plan["summary"]["b2_width_unmatched_findings"], 0)
            self.assertEqual(plan["summary"]["b2_width_unmatched_pages"], [])
            self.assertEqual(plan["summary"]["b2_width_unmatched_finding_ids"], [])

    def test_visual_b2_width_matching_consumes_duplicate_source_width_labels(self) -> None:
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
                                "page": 3,
                                "taxonomy_defect_id": "B2",
                                "defect_id": "B2-native-float-overflow-width",
                                "category": "B",
                                "severity": "major",
                                "confidence": 0.9,
                                "bbox": [976, 165, 1909, 758],
                                "metrics": {
                                    "subtype": "overflow_width",
                                    "object_kind": "figure_like",
                                    "overflow_pt": 12.774,
                                    "object_width_ratio": 1.28,
                                    "object_width_page_ratio": 0.4988,
                                    "width_context": "column",
                                },
                            },
                            {
                                "source": "pymupdf_native",
                                "page": 4,
                                "taxonomy_defect_id": "B2",
                                "defect_id": "B2-native-float-overflow-width",
                                "category": "B",
                                "severity": "major",
                                "confidence": 0.9,
                                "bbox": [976, 939, 1909, 1357],
                                "metrics": {
                                    "subtype": "overflow_width",
                                    "object_kind": "figure_like",
                                    "overflow_pt": 12.777,
                                    "object_width_ratio": 1.28,
                                    "object_width_page_ratio": 0.4887,
                                    "width_context": "column",
                                },
                            },
                        ],
                        "object_pairings": [
                            {
                                "page": 3,
                                "object_kind": "figure_like",
                                "object_bbox": [976, 165, 1909, 758],
                                "object_width_ratio": 1.28,
                            },
                            {
                                "page": 4,
                                "object_kind": "figure_like",
                                "object_bbox": [976, 939, 1909, 1357],
                                "object_width_ratio": 1.28,
                            },
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
                                "label": "fig:problem",
                                "float_type": "figure",
                                "line_number": 203,
                                "width_spec": "1.28\\linewidth",
                            },
                            {
                                "label": "fig:edge",
                                "float_type": "figure",
                                "line_number": 254,
                                "width_spec": "1.28\\linewidth",
                            },
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

            overflow_candidates = [
                candidate
                for candidate in plan["candidates"]
                if candidate.get("visual_width_subtype") == "overflow_width"
            ]
            self.assertEqual(
                [candidate["target"]["label"] for candidate in overflow_candidates],
                ["fig:problem", "fig:edge"],
            )
            self.assertEqual(
                [candidate["match_strategy"] for candidate in overflow_candidates],
                ["source_width_visual_b2", "source_width_visual_b2"],
            )

    def test_visual_b2_source_order_fallback_skips_width_matched_labels(self) -> None:
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
                                "page": 3,
                                "taxonomy_defect_id": "B2",
                                "defect_id": "B2-native-figure-overflow-width",
                                "category": "B",
                                "severity": "major",
                                "confidence": 0.9,
                                "bbox": [976, 165, 1909, 758],
                                "metrics": {
                                    "subtype": "overflow_width",
                                    "object_kind": "figure_like",
                                    "overflow_pt": 12.7,
                                    "object_width_ratio": 1.28,
                                    "width_context": "column",
                                },
                            },
                            {
                                "source": "pymupdf_native",
                                "page": 4,
                                "taxonomy_defect_id": "B2",
                                "defect_id": "B2-native-figure-overflow-width",
                                "category": "B",
                                "severity": "major",
                                "confidence": 0.9,
                                "bbox": [976, 940, 1909, 1360],
                                "metrics": {
                                    "subtype": "overflow_width",
                                    "object_kind": "figure_like",
                                    "overflow_pt": 11.2,
                                    "object_width_ratio": 1.18,
                                    "width_context": "column",
                                },
                            },
                        ],
                        "object_pairings": [
                            {
                                "page": 2,
                                "object_kind": "table_like",
                                "object_bbox": [120, 640, 930, 1160],
                                "object_width_ratio": 1.0,
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
                                "label": "fig:wide",
                                "float_type": "figure",
                                "line_number": 110,
                                "width_spec": "1.28\\linewidth",
                            },
                            {
                                "label": "fig:next",
                                "float_type": "figure",
                                "line_number": 220,
                            },
                            {
                                "label": "tab:paired",
                                "float_type": "table",
                                "line_number": 300,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            plan = generate_repair_plan(
                visual_signal_report=str(visual_path),
                output_path=str(output_path),
                crossrefs_report=str(crossrefs_path),
            )

            overflow_candidates = [
                candidate
                for candidate in plan["candidates"]
                if candidate.get("visual_width_subtype") == "overflow_width"
            ]
            self.assertEqual(
                [candidate["target"]["label"] for candidate in overflow_candidates],
                ["fig:wide", "fig:next"],
            )
            self.assertEqual(
                [candidate["match_strategy"] for candidate in overflow_candidates],
                ["source_width_visual_b2", "source_order_visual_b2"],
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
                    "allow_floatbarrier": True,
                },
            ]
        }

        defects = _build_float_defects(plan, max_candidates=1)

        self.assertEqual(len(defects), 1)
        self.assertEqual(defects[0]["object"], "fig:problem")
        self.assertEqual(defects[0]["visual_width_subtype"], "overflow_width")
        self.assertEqual(defects[0]["visual_overflow_pt"], 12.774)
        self.assertTrue(defects[0]["allow_floatbarrier"])
        defects = _build_float_defects(plan, max_candidates=2)
        self.assertEqual(defects[1]["object"], "fig:visual_small")
        self.assertEqual(defects[1]["visual_width_subtype"], "underfilled_width")

    def test_float_defect_selection_skips_source_only_b2_width_candidate(self) -> None:
        plan = {
            "candidates": [
                {
                    "candidate_type": "source_anchor",
                    "defect_family": "B2",
                    "priority_score": 190,
                    "target": {"label": "fig:source_small"},
                    "source_width_spec": "0.35\\linewidth",
                },
                {
                    "candidate_type": "object",
                    "defect_family": "D1",
                    "priority_score": 80,
                    "target": {"scope": "overflow"},
                    "line_number": 120,
                },
            ]
        }

        defects = _build_float_defects(plan, max_candidates=1)

        self.assertEqual(defects, [])

    def test_execute_repair_plan_prioritizes_urgent_overflow_over_source_only_b2_width(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\n".join(
                    [
                        "\\documentclass{article}",
                        "\\begin{document}",
                        "A paragraph with BenchmarkEquationOverflowSequenceAlphaBetaGammaDeltaEpsilonZetaEtaThetaIotaKappa.",
                        "\\begin{figure}",
                        "\\includegraphics[width=0.35\\linewidth]{figs/small.pdf}",
                        "\\caption{Small by design}",
                        "\\label{fig:small}",
                        "\\end{figure}",
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
                        "candidates": [
                            {
                                "candidate_type": "source_anchor",
                                "defect_family": "B2",
                                "priority_score": 190,
                                "target": {"label": "fig:small", "float_type": "figure"},
                                "source_width_spec": "0.35\\linewidth",
                                "proposed_action": "adjust_float_width",
                            },
                            {
                                "candidate_type": "log_warning",
                                "defect_family": "D1",
                                "priority_score": 95,
                                "severity": "major",
                                "target": {"scope": "overflow"},
                                "proposed_action": "repair_overfull_boxes",
                                "overflow_amount": 13.2,
                                "line_number": 3,
                                "description": "long paragraph token",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with (
                mock.patch("repair_plan_executor.execute_float_candidates") as execute_float,
                mock.patch(
                    "repair_plan_executor.execute_overflow_candidates",
                    return_value={"skill": "overflow-repair", "status": "noop", "changes": [], "unresolved": []},
                ) as execute_overflow,
            ):
                report = execute_repair_plan(
                    repair_plan_path=str(plan_path),
                    main_tex=str(main_tex),
                    output_path=str(output_path),
                    max_candidates=1,
                )

            execute_float.assert_not_called()
            execute_overflow.assert_called_once()
            self.assertEqual(report["selected_candidates"]["float"], [])
            self.assertEqual(len(report["selected_candidates"]["overflow"]), 1)
            self.assertEqual(report["selected_candidates"]["overflow"][0]["defect_id"], "D1")
            self.assertTrue(report["selection_policy"]["urgent_overflow_first"])
            self.assertEqual(
                report["selection_policy"]["reason"],
                "urgent_d_overflow_without_visual_b2_width_candidate",
            )

    def test_execute_repair_plan_prioritizes_urgent_overflow_over_untargetable_visual_b2(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\n".join(
                    [
                        "\\documentclass{article}",
                        "\\begin{document}",
                        "BenchmarkEquationOverflowSequenceAlphaBetaGammaDeltaEpsilon",
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
                        "candidates": [
                            {
                                "candidate_type": "object",
                                "defect_family": "B2",
                                "priority_score": 190,
                                "target": {"object_kind": "figure_like"},
                                "visual_width_subtype": "overflow_width",
                                "visual_overflow_pt": 12.7,
                            },
                            {
                                "candidate_type": "log_warning",
                                "defect_family": "D1",
                                "priority_score": 95,
                                "severity": "major",
                                "target": {"scope": "overflow"},
                                "proposed_action": "repair_overfull_boxes",
                                "overflow_amount": 13.2,
                                "line_number": 3,
                                "description": "long paragraph token",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with (
                mock.patch("repair_plan_executor.execute_float_candidates") as execute_float,
                mock.patch(
                    "repair_plan_executor.execute_overflow_candidates",
                    return_value={"skill": "overflow-repair", "status": "noop", "changes": [], "unresolved": []},
                ) as execute_overflow,
            ):
                report = execute_repair_plan(
                    repair_plan_path=str(plan_path),
                    main_tex=str(main_tex),
                    output_path=str(output_path),
                    max_candidates=1,
                )

            execute_float.assert_not_called()
            execute_overflow.assert_called_once()
            self.assertEqual(report["selected_candidates"]["float"], [])
            self.assertEqual(len(report["selected_candidates"]["overflow"]), 1)
            self.assertTrue(report["selection_policy"]["urgent_overflow_first"])
            self.assertEqual(
                report["selection_policy"]["reason"],
                "urgent_d_overflow_without_visual_b2_width_candidate",
            )

    def test_float_defect_selection_accepts_visual_table_b2_candidate(self) -> None:
        plan = {
            "candidates": [
                {
                    "candidate_type": "object",
                    "defect_family": "B2",
                    "priority_score": 160,
                    "page": 4,
                    "target": {"label": "tab:wide", "object_kind": "table_like"},
                    "visual_width_subtype": "overflow_width",
                    "visual_overflow_pt": 18.4,
                    "visual_object_width_ratio": 1.18,
                },
                {
                    "candidate_type": "object",
                    "defect_family": "B2",
                    "priority_score": 150,
                    "page": 2,
                    "target": {"label": "fig:small", "object_kind": "figure_like"},
                    "visual_width_subtype": "underfilled_width",
                    "visual_object_width_ratio": 0.42,
                },
            ]
        }

        defects = _build_float_defects(plan, max_candidates=1)

        self.assertEqual(len(defects), 1)
        self.assertEqual(defects[0]["object"], "tab:wide")
        self.assertEqual(defects[0]["object_kind"], "table_like")
        self.assertEqual(defects[0]["visual_width_subtype"], "overflow_width")
        self.assertEqual(defects[0]["visual_overflow_pt"], 18.4)

    def test_float_defect_selection_accepts_object_kind_without_label_prefix(self) -> None:
        plan = {
            "candidates": [
                {
                    "candidate_type": "object",
                    "defect_family": "B2",
                    "priority_score": 160,
                    "page": 4,
                    "target": {"label": "wide-results", "object_kind": "table_like"},
                    "visual_width_subtype": "overflow_width",
                    "visual_overflow_pt": 18.4,
                    "visual_object_width_ratio": 1.18,
                }
            ]
        }

        defects = _build_float_defects(plan, max_candidates=1)

        self.assertEqual(len(defects), 1)
        self.assertEqual(defects[0]["object"], "wide-results")
        self.assertEqual(defects[0]["object_kind"], "table_like")
        self.assertEqual(defects[0]["visual_width_subtype"], "overflow_width")

    def test_selected_b2_width_candidate_summary_counts_selected_float_defects(self) -> None:
        summary = _summarize_selected_b2_width_candidates(
            [
                {
                    "defect_id": "B2",
                    "object": "wide-results",
                    "page": 4,
                    "object_kind": "table_like",
                    "visual_width_subtype": "overflow_width",
                },
                {
                    "defect_id": "B2",
                    "object": "fig:small",
                    "page": 2,
                    "object_kind": "figure_like",
                    "visual_width_subtype": "underfilled_width",
                },
                {
                    "defect_id": "B2",
                    "object": "source-only",
                    "page": 1,
                },
                {
                    "defect_id": "D1",
                    "object": "paragraph_overflow",
                    "page": 3,
                },
            ]
        )

        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["by_subtype"], {"overflow_width": 1, "underfilled_width": 1})
        self.assertEqual(summary["by_object_kind"], {"table_like": 1, "figure_like": 1})
        self.assertEqual(summary["labels"], ["fig:small", "wide-results"])
        self.assertEqual(summary["pages"], [2, 4])

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
            self.assertEqual(report["b2_width_selected_candidates"]["total"], 1)
            self.assertEqual(report["b2_width_selected_candidates"]["by_subtype"], {"overflow_width": 1})
            self.assertEqual(report["b2_width_selected_candidates"]["labels"], ["fig:problem"])
            self.assertEqual(report["selected_candidates"]["overflow"], [])
            self.assertEqual(report["overflow_report"]["status"], "noop")
            self.assertIn("deferred D-class overflow", report["overflow_report"]["unresolved"][0])
            self.assertEqual(report["applied_count"], 1)

    def test_execute_repair_plan_applies_object_kind_table_b2_without_label_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\n".join(
                    [
                        "\\documentclass{article}",
                        "\\begin{document}",
                        "\\begin{table}[t]",
                        "\\centering",
                        "\\caption{Results}",
                        "\\label{wide-results}",
                        "\\begin{tabular}{lll}",
                        "A & B & C \\\\",
                        "1 & 2 & 3 \\\\",
                        "\\end{tabular}",
                        "\\end{table}",
                        "\\end{document}",
                    ]
                )
                + "\n",
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
                                "target": {"label": "wide-results", "object_kind": "table_like"},
                                "visual_width_subtype": "overflow_width",
                                "visual_overflow_pt": 18.4,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch("float_fixers._passes_hard_content_gate", return_value=(True, "pass")):
                report = execute_repair_plan(
                    repair_plan_path=str(plan_path),
                    main_tex=str(main_tex),
                    output_path=str(output_path),
                    max_candidates=1,
                )

            updated = main_tex.read_text(encoding="utf-8")
            self.assertEqual(report["status"], "success")
            self.assertEqual(report["applied_count"], 1)
            self.assertEqual(report["selected_candidates"]["float"][0]["object"], "wide-results")
            self.assertEqual(report["selected_candidates"]["float"][0]["object_kind"], "table_like")
            self.assertEqual(report["fix_report"]["changes"][0]["object"], "wide-results")
            self.assertIn("\\begin{tabular*}{\\linewidth}", updated)
            self.assertIn("\\label{wide-results}", updated)

    def test_execute_repair_plan_does_not_defer_global_action_for_source_only_b2(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\n".join(
                    [
                        "\\documentclass{article}",
                        "\\begin{document}",
                        "First sentence.\\\\",
                        "Second sentence.",
                        "\\end{document}",
                    ]
                )
                + "\n",
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
                                "candidate_type": "source_anchor",
                                "defect_family": "B2",
                                "priority_score": 160,
                                "target": {"label": "fig:source-small"},
                                "source_width_spec": "0.35\\linewidth",
                            },
                            {
                                "candidate_type": "global",
                                "defect_family": "A/C",
                                "priority_score": 40,
                                "severity": "minor",
                                "target": {"scope": "paragraph_spacing"},
                                "proposed_action": "review_paragraph_spacing_and_looseness",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = execute_repair_plan(
                repair_plan_path=str(plan_path),
                main_tex=str(main_tex),
                output_path=str(output_path),
                max_candidates=1,
            )

            updated = main_tex.read_text(encoding="utf-8")
            self.assertEqual(report["selected_candidates"]["float"], [])
            self.assertEqual(report["global_report"]["status"], "success")
            self.assertEqual(report["global_report"]["applied_count"], 1)
            self.assertNotIn("deferred global text actions until B1/B2", report["global_report"].get("unresolved") or [])
            self.assertIn("First sentence.", updated)
            self.assertNotIn("First sentence.\\\\", updated)

    def test_state_manager_preserves_b2_targetable_candidate_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            data_dir = root / "data"
            data_dir.mkdir()
            plan_path = data_dir / "repair_plan.json"
            plan_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "summary": {
                            "total_candidates": 2,
                            "b2_width_candidates": {
                                "total": 2,
                                "by_subtype": {"overflow_width": 2},
                                "labels": ["wide-results", "untargeted"],
                            },
                            "b2_width_targetable_candidates": {
                                "total": 1,
                                "by_subtype": {"overflow_width": 1},
                                "labels": ["wide-results"],
                            },
                            "b2_width_untargetable_candidates": {
                                "total": 1,
                                "by_subtype": {"overflow_width": 1},
                                "by_reason": {"unsupported_label_or_object_kind": 1},
                                "labels": ["untargeted"],
                            },
                        },
                        "candidates": [],
                    }
                ),
                encoding="utf-8",
            )

            manager = StateManager(state_path=str(data_dir / "state.json"))
            manager.init_state(main_tex="main.tex", task_type="full_vto")
            result = manager.ingest_repair_plan(str(plan_path))

            summary = result["repair_plan_summary"]
            self.assertEqual(summary["b2_width_candidates"]["total"], 2)
            self.assertEqual(summary["b2_width_targetable_candidates"]["total"], 1)
            self.assertEqual(summary["b2_width_targetable_candidates"]["labels"], ["wide-results"])
            self.assertEqual(summary["b2_width_untargetable_candidates"]["total"], 1)
            self.assertEqual(
                summary["b2_width_untargetable_candidates"]["by_reason"],
                {"unsupported_label_or_object_kind": 1},
            )

    def test_state_manager_preserves_selected_visual_b2_execution_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            data_dir = root / "data"
            data_dir.mkdir()
            report_path = data_dir / "repair_execution_report.json"
            report_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "status": "success",
                        "applied_count": 1,
                        "selected_candidates": {
                            "float": [
                                {
                                    "defect_id": "B2",
                                    "object": "wide-results",
                                    "page": 3,
                                    "object_kind": "table_like",
                                    "visual_width_subtype": "overflow_width",
                                    "visual_object_width_ratio": 1.18,
                                    "visual_overflow_pt": 18.4,
                                }
                            ],
                            "overflow": [],
                            "space_util": [],
                        },
                        "b2_width_selected_candidates": {
                            "total": 1,
                            "by_subtype": {"overflow_width": 1},
                            "by_object_kind": {"table_like": 1},
                            "labels": ["wide-results"],
                            "objects": ["wide-results"],
                            "pages": [3],
                        },
                    }
                ),
                encoding="utf-8",
            )

            manager = StateManager(state_path=str(data_dir / "state.json"))
            manager.init_state(main_tex="main.tex", task_type="full_vto")
            result = manager.ingest_repair_execution_report(str(report_path))

            selected = result["repair_execution_summary"]["selected_candidates"]
            self.assertEqual(selected[0]["defect_id"], "B2")
            self.assertEqual(selected[0]["object_kind"], "table_like")
            self.assertEqual(selected[0]["visual_width_subtype"], "overflow_width")
            self.assertEqual(selected[0]["visual_overflow_pt"], 18.4)
            self.assertEqual(
                result["repair_execution_summary"]["b2_width_selected_candidates"],
                {
                    "total": 1,
                    "by_subtype": {"overflow_width": 1},
                    "by_object_kind": {"table_like": 1},
                    "labels": ["wide-results"],
                    "objects": ["wide-results"],
                    "pages": [3],
                },
            )

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
