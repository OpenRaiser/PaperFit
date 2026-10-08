"验收：跨轮执行统计不可变。"

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
from pdf_test_support import write_test_pdf, write_test_png  # noqa: E402
from orchestrator_runtime import OrchestratorRuntime  # noqa: E402
from runtime_types import TaskSpec  # noqa: E402


class RoundExecutionRecordsTest(unittest.TestCase):
    def test_round1_accepted_survives_round2_approval_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\\documentclass{article}\n\\begin{document}\nFixture.\n\\end{document}\n",
                encoding="utf-8",
            )
            pdf = root / "main.pdf"
            write_test_pdf(pdf)
            core_calls = {"count": 0}
            execute_calls = {"count": 0}
            gate_calls = {"count": 0}
            original_tex = main_tex.read_text(encoding="utf-8")

            def write_runtime_artifacts(decision: str, base_mtime: int) -> None:
                page_dir = root / "data" / "pages"
                page_dir.mkdir(parents=True, exist_ok=True)
                page = page_dir / "page_001.png"
                visual_report = root / "data" / "visual_signal_report.json"
                defect_report = root / "data" / "defect_report.json"
                gatekeeper_report = root / "data" / "gatekeeper_decision.json"
                write_test_png(page)
                visual_report.write_text(json.dumps({"summary": {"pages_analyzed": 8}}), encoding="utf-8")
                defect_report.write_text("{}", encoding="utf-8")
                gatekeeper_report.write_text(json.dumps({"decision": decision}), encoding="utf-8")
                for offset, path in enumerate([pdf, page, visual_report, defect_report, gatekeeper_report]):
                    os.utime(path, (base_mtime + offset, base_mtime + offset))

            def fake_core(
                self: OrchestratorRuntime,
                *,
                emit_event: object | None = None,
                runtime_actions: dict[str, object] | None = None,
                **_: object,
            ) -> dict[str, object]:
                core_calls["count"] += 1
                write_runtime_artifacts("CONTINUE", 1_800_020_000 + core_calls["count"] * 100)
                (root / "data" / "repair_plan.json").write_text(
                    json.dumps(
                        {
                            "schema_version": "1.0",
                            "summary": {"total_candidates": 1},
                            "candidates": [
                                {
                                    "candidate_type": "object",
                                    "defect_family": "D1",
                                    "priority_score": 100,
                                    "target": {"scope": "overflow", "label": f"r{core_calls['count']}"},
                                    "description": "repair",
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
                        "defect_summary": {"initial_total": 2, "resolved": 1, "remaining": 1},
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
                return self.manager.load()

            def fake_execute(self: OrchestratorRuntime, *_: object, **__: object) -> dict[str, object]:
                execute_calls["count"] += 1
                main_tex.write_text(original_tex + f"% accepted edit r{execute_calls['count']}\n", encoding="utf-8")
                self.manager.update(
                    {
                        "repair_execution_summary": {
                            "schema_version": "1.0",
                            "status": "success",
                            "applied_count": 2,
                            "accepted_count": 2,
                            "rejected_count": 0,
                            "rolled_back_count": 0,
                            "candidate_outcomes": [],
                            "selected_candidates": [],
                            "updated_at": None,
                        }
                    }
                )
                return self.manager.load()

            def fake_gate(**kwargs):
                gate_calls["count"] += 1
                if gate_calls["count"] == 1:
                    return {
                        "schema_version": "1.1",
                        "status": "pass",
                        "reason": "safe_subset_within_approval_scope",
                        "allowed_candidates": [{"index": 1}],
                        "deferred_candidates": [],
                        "blocked_candidates": [],
                        "candidate_risks": [],
                    }
                return {
                    "schema_version": "1.1",
                    "status": "approval_required",
                    "reason": "no_candidate_allowed_under_current_scope",
                    "allowed_candidates": [],
                    "deferred_candidates": [
                        {"index": 1, "deferred_reason": "high_risk_operation_requires_fresh_approval"}
                    ],
                    "blocked_candidates": [{"index": 1}],
                    "candidate_risks": [],
                }

            spec = TaskSpec.from_dict(
                {
                    "task_type": "full_vto",
                    "project_root": str(root),
                    "main_tex": "main.tex",
                    "allow_source_mutation": True,
                    "pre_repair_snapshot_required": True,
                    "rollback_policy": "required",
                    "max_rounds": 2,
                }
            )

            with (
                mock.patch.object(
                    orchestrator_runtime, "compile_latex",
                    return_value={"success": True, "pdf_path": str(pdf)},
                ),
                mock.patch.object(
                    orchestrator_runtime, "render_pdf_pages", return_value={"success": True},
                ),
                mock.patch.object(OrchestratorRuntime, "_run_round_core", fake_core),
                mock.patch.object(OrchestratorRuntime, "execute_repair_plan_phased", fake_execute),
                mock.patch.object(
                    orchestrator_runtime,
                    "build_candidate_approval_scope_gate",
                    side_effect=fake_gate,
                ),
            ):
                result = OrchestratorRuntime(state_path=str(root / "data" / "state.json")).run_task(
                    task_spec=spec,
                    output_path="data/run_result_round_records.json",
                )

            self.assertEqual(execute_calls["count"], 1, "轮 2 必须被审批挡在执行器之前")
            self.assertEqual(result["status"], "approval_required")
            records = result["round_execution_records"]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["round"], 1)
            self.assertEqual(records[0]["accepted"], 2)
            self.assertEqual(records[0]["applied"], 2)
            totals = result["execution_totals"]
            self.assertEqual(totals["accepted"], 2, "轮 2 的重置不得抹掉轮 1 的 accepted")
            self.assertEqual(totals["applied"], 2)
            # 轮 1 已接受的源码修改保留（无质量/完整性理由不得回滚）
            self.assertIn("% accepted edit r1", main_tex.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
