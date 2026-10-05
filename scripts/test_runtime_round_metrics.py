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

import orchestrator_runtime  # noqa: E402
from orchestrator_runtime import OrchestratorRuntime  # noqa: E402
from runtime_types import TaskSpec  # noqa: E402


class RoundMetricsTest(unittest.TestCase):
    def test_round_metrics_report_before_after_deltas(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "data").mkdir()
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\\documentclass{article}\n\\begin{document}\nFixture.\n\\end{document}\n",
                encoding="utf-8",
            )
            pdf = root / "main.pdf"
            pdf.write_bytes(b"%PDF fixture\n")
            core_calls = {"count": 0}

            def fake_core(
                self: OrchestratorRuntime,
                *,
                emit_event: object | None = None,
                runtime_actions: dict[str, object] | None = None,
                **_: object,
            ) -> dict[str, object]:
                core_calls["count"] += 1
                page_dir = root / "data" / "pages"
                page_dir.mkdir(parents=True, exist_ok=True)
                page = page_dir / "page_001.png"
                page.write_bytes(b"png")
                visual_report = root / "data" / "visual_signal_report.json"
                defect_report = root / "data" / "defect_report.json"
                gatekeeper_report = root / "data" / "gatekeeper_decision.json"
                visual_report.write_text(json.dumps({"summary": {"pages_analyzed": 8}}), encoding="utf-8")
                defect_report.write_text("{}", encoding="utf-8")
                gatekeeper_report.write_text("{}", encoding="utf-8")
                base_mtime = 1_800_000_000 + core_calls["count"] * 10
                os.utime(pdf, (base_mtime, base_mtime))
                os.utime(page, (base_mtime + 1, base_mtime + 1))
                os.utime(visual_report, (base_mtime + 2, base_mtime + 2))
                os.utime(defect_report, (base_mtime + 3, base_mtime + 3))
                os.utime(gatekeeper_report, (base_mtime + 4, base_mtime + 4))
                if core_calls["count"] == 1:
                    repair_plan = root / "data" / "repair_plan.json"
                    repair_plan.write_text(
                        json.dumps(
                            {
                                "schema_version": "1.0",
                                "summary": {"total_candidates": 1},
                                "candidates": [
                                    {
                                        "candidate_type": "source_anchor",
                                        "defect_family": "B1",
                                        "proposed_action": "move_float_closer_to_first_reference",
                                        "section_distance": 0,
                                        "target": {"label": "fig:near", "float_type": "figure"},
                                    }
                                ],
                            }
                        ),
                        encoding="utf-8",
                    )
                    self.manager.update(
                        {
                            "compile_success": True,
                            "page_images_rendered": True,
                            "last_gatekeeper_decision": "CONTINUE",
                            "defect_summary": {"initial_total": 3, "resolved": 0, "remaining": 3},
                            "repair_plan_summary": {
                                "schema_version": "1.0",
                                "total_candidates": 1,
                                "top_candidates": [],
                                "updated_at": None,
                            },
                            "artifacts": {
                                "page_images_dir": "data/pages",
                                "visual_signal_report": "data/visual_signal_report.json",
                                "defect_report": "data/defect_report.json",
                                "repair_plan": "data/repair_plan.json",
                                "gatekeeper_decision": "data/gatekeeper_decision.json",
                            },
                        }
                    )
                    rule_report = {"summary": {"overfull_hbox_total": 4}}
                else:
                    self.manager.update(
                        {
                            "compile_success": True,
                            "page_images_rendered": True,
                            "last_gatekeeper_decision": "DONE",
                            "defect_summary": {"initial_total": 3, "resolved": 2, "remaining": 1},
                            "repair_plan_summary": {
                                "schema_version": "1.0",
                                "total_candidates": 0,
                                "top_candidates": [],
                                "updated_at": None,
                            },
                            "artifacts": {
                                "page_images_dir": "data/pages",
                                "visual_signal_report": "data/visual_signal_report.json",
                                "defect_report": "data/defect_report.json",
                                "gatekeeper_decision": "data/gatekeeper_decision.json",
                            },
                        }
                    )
                    rule_report = {"summary": {"overfull_hbox_total": 1}}
                state = self.manager.load()
                state["rule_report"] = rule_report
                return state

            def fake_execute(
                self: OrchestratorRuntime,
                *_: object,
                **__: object,
            ) -> dict[str, object]:
                self.manager.update(
                    {
                        "repair_execution_summary": {
                            "schema_version": "1.0",
                            "status": "applied",
                            "applied_count": 1,
                            "selected_candidates": [],
                            "updated_at": None,
                        }
                    }
                )
                return self.manager.load()

            spec = TaskSpec.from_dict(
                {
                    "task_type": "full_vto",
                    "project_root": str(root),
                    "main_tex": "main.tex",
                    "allow_source_mutation": True,
                    "pre_repair_snapshot_required": True,
                    "rollback_policy": "required",
                }
            )

            with (
                mock.patch.object(
                    orchestrator_runtime,
                    "compile_latex",
                    return_value={"success": True, "pdf_path": str(pdf), "timeout": False, "log_file": "main.log"},
                ),
                mock.patch.object(
                    orchestrator_runtime,
                    "render_pdf_pages",
                    return_value={"success": True, "page_dir": "data/pages"},
                ),
                mock.patch.object(
                    orchestrator_runtime,
                    "inspect_endmatter_float_intrusion",
                    return_value={"available": True, "hard_failures": []},
                ),
                mock.patch.object(OrchestratorRuntime, "_run_round_core", fake_core),
                mock.patch.object(OrchestratorRuntime, "execute_repair_plan", fake_execute),
            ):
                result = OrchestratorRuntime(state_path=str(root / "data" / "state.json")).run_task(
                    task_spec=spec,
                    output_path="data/run_result_full_vto.json",
                )

            self.assertEqual(result["status"], "done")
            self.assertEqual(len(result["round_metrics"]), 1)
            metrics = result["round_metrics"][0]
            self.assertEqual(metrics["round"], 1)
            self.assertEqual(metrics["overfull_hbox_total_before"], 4)
            self.assertEqual(metrics["overfull_hbox_total_after"], 1)
            self.assertEqual(metrics["defects_remaining_before"], 3)
            self.assertEqual(metrics["defects_remaining_after"], 1)
            on_disk = json.loads((root / "data" / "run_result_full_vto.json").read_text(encoding="utf-8"))
            self.assertEqual(on_disk["round_metrics"], result["round_metrics"])


if __name__ == "__main__":
    unittest.main()
