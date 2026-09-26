from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import hashlib


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import orchestrator_runtime  # noqa: E402
from orchestrator_runtime import OrchestratorRuntime  # noqa: E402
from runtime_types import TaskSpec  # noqa: E402


class RuntimeSourceChangingTest(unittest.TestCase):
    def test_full_vto_runtime_creates_snapshot_and_runs_one_repair_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "Source changing fixture.\n"
                "\\end{document}\n",
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
                visual_report = root / "data" / "visual_signal_report.json"
                defect_report = root / "data" / "defect_report.json"
                gatekeeper_report = root / "data" / "gatekeeper_decision.json"
                page = page_dir / "page_001.png"
                page.write_bytes(b"png")
                visual_report.write_text("{}", encoding="utf-8")
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
                            "defect_summary": {
                                "initial_total": 1,
                                "resolved": 0,
                                "remaining": 1,
                            },
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
                else:
                    self.manager.update(
                        {
                            "compile_success": True,
                            "page_images_rendered": True,
                            "last_gatekeeper_decision": "DONE",
                            "defect_summary": {
                                "initial_total": 1,
                                "resolved": 1,
                                "remaining": 0,
                            },
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
                self._record_runtime_action(
                    "gatekeeper_enforcer",
                    {
                        "success": True,
                        "source": "generated",
                        "output_path": "data/gatekeeper_decision.json",
                        "decision": self.manager.load()["last_gatekeeper_decision"],
                    },
                    phase="verify",
                    state="VERIFYING",
                    emit_event=emit_event if callable(emit_event) else None,
                    runtime_actions=runtime_actions,
                )
                return self.manager.load()

            def fake_execute(
                self: OrchestratorRuntime,
                *_: object,
                **__: object,
            ) -> dict[str, object]:
                main_tex.write_text(
                    "\\documentclass{article}\n"
                    "\\begin{document}\n"
                    "Mutated by fake repair.\n"
                    "\\end{document}\n",
                    encoding="utf-8",
                )
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
                    return_value={
                        "success": True,
                        "pdf_path": str(pdf),
                        "timeout": False,
                        "log_file": "main.log",
                    },
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
            self.assertEqual(core_calls["count"], 2)
            self.assertTrue(result["runtime_actions"]["pre_repair_snapshot"]["success"])
            self.assertEqual(result["runtime_actions"]["repair_plan_executor"]["applied_count"], 1)
            self.assertEqual(result["runtime_actions"]["repair_plan_executor"]["approval_scope_gate"]["status"], "pass")
            self.assertEqual(result["runtime_actions"]["source_mutation_integrity"]["changed_files"], 1)
            self.assertEqual(result["approval"]["status"], "approved_and_executed")
            self.assertFalse(result["approval"]["requires_approval"])
            self.assertTrue(result["approval"]["approval_granted"])
            self.assertEqual(result["approval"]["execution"]["applied_count"], 1)
            policy = result["repair_loop_policy"]
            self.assertEqual(policy["execution_mode"], "bounded_apply")
            self.assertEqual(policy["candidate_batch_limit"], 1)
            self.assertEqual(policy["applied_count"], 1)
            self.assertEqual(policy["stop_condition"], "done")
            self.assertFalse(policy["next_round_allowed"])
            self.assertEqual(policy["next_round_reason"], "gatekeeper_done")
            self.assertEqual(policy["approval_scope_carry_forward"]["status"], "pass")
            self.assertEqual(policy["second_round_apply_readiness"]["status"], "blocked")
            self.assertFalse(policy["second_round_apply_readiness"]["checks"]["runtime_execution_mode_can_auto_apply"])
            self.assertIn("round_artifact_lineage", policy)
            self.assertEqual(result["round_artifact_lineage"][0]["round"], 1)
            self.assertIn("repair_plan_executor", result["round_artifact_lineage"][0]["actions"])
            self.assertIn("source_mutation_integrity", result["round_artifact_lineage"][0]["actions"])
            self.assertIn("post_repair_observe", result["runtime_actions"])
            state = json.loads((root / "data" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["content_integrity"]["validation_status"], "mutation_reported")
            self.assertTrue((root / state["content_integrity"]["rollback_target"]).is_file())
            self.assertEqual(state["artifacts"]["source_mutation_report"], "data/source_mutation_report.json")

            main_tex.write_text("mutated after repair\n", encoding="utf-8")
            rollback_state = OrchestratorRuntime(state_path=str(root / "data" / "state.json")).rollback_to_snapshot(
                rollback_target=state["content_integrity"]["rollback_target"],
            )
            self.assertEqual(rollback_state["content_integrity"]["validation_status"], "rolled_back")
            self.assertEqual(rollback_state["content_integrity"]["action_taken"], "restore_snapshot")
            self.assertEqual(rollback_state["artifacts"]["rollback_report"], "data/rollback_report.json")
            self.assertIn("\\documentclass{article}", main_tex.read_text(encoding="utf-8"))
            self.assertTrue((root / "data" / "rollback_report.json").is_file())

    def test_post_repair_hard_guard_failure_rolls_back_and_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            main_tex = root / "main.tex"
            original_tex = (
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "Original safe fixture.\n"
                "\\end{document}\n"
            )
            main_tex.write_text(original_tex, encoding="utf-8")
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
                visual_report = root / "data" / "visual_signal_report.json"
                defect_report = root / "data" / "defect_report.json"
                gatekeeper_report = root / "data" / "gatekeeper_decision.json"
                repair_plan = root / "data" / "repair_plan.json"
                page.write_bytes(b"png")
                visual_report.write_text("{}", encoding="utf-8")
                defect_report.write_text("{}", encoding="utf-8")
                gatekeeper_report.write_text(json.dumps({"decision": "CONTINUE"}), encoding="utf-8")
                repair_plan.write_text(
                    json.dumps(
                        {
                            "schema_version": "1.0",
                            "summary": {"total_candidates": 1},
                            "candidates": [
                                {
                                    "candidate_type": "object",
                                    "defect_family": "B2",
                                    "priority_score": 192,
                                    "target": {"label": "fig:unsafe"},
                                    "proposed_action": "adjust_float_width",
                                    "visual_width_subtype": "overflow_width",
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                base_mtime = 1_800_030_000 + core_calls["count"] * 100
                for offset, path in enumerate([pdf, page, visual_report, defect_report, gatekeeper_report]):
                    os.utime(path, (base_mtime + offset, base_mtime + offset))
                self.manager.update(
                    {
                        "compile_success": True,
                        "page_images_rendered": True,
                        "last_gatekeeper_decision": "CONTINUE",
                        "defect_summary": {
                            "initial_total": 1,
                            "resolved": 0,
                            "remaining": 1,
                        },
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
                self._record_runtime_action(
                    "gatekeeper_enforcer",
                    {
                        "success": True,
                        "source": "generated",
                        "output_path": "data/gatekeeper_decision.json",
                        "decision": "CONTINUE",
                    },
                    phase="verify",
                    state="VERIFYING",
                    emit_event=emit_event if callable(emit_event) else None,
                    runtime_actions=runtime_actions,
                )
                return self.manager.load()

            def fake_execute(self: OrchestratorRuntime, *_: object, **__: object) -> dict[str, object]:
                main_tex.write_text(
                    "\\documentclass{article}\n"
                    "\\begin{document}\n"
                    "Mutated unsafe fixture.\n"
                    "\\end{document}\n",
                    encoding="utf-8",
                )
                self.manager.update(
                    {
                        "repair_execution_summary": {
                            "schema_version": "1.0",
                            "status": "success",
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
                    "max_rounds": 2,
                }
            )

            hard_guard_reports = [
                {"available": True, "hard_failures": []},
                {
                    "available": True,
                    "endmatter_start_page": 8,
                    "endmatter_heading": "Acknowledgements",
                    "intrusions": [{"page": 8, "captions": ["Figure 7:"]}],
                    "hard_failures": [
                        "Body float caption detected on endmatter pages starting at page 8 (Acknowledgements): page 8: Figure 7:"
                    ],
                },
            ]

            with (
                mock.patch.object(
                    orchestrator_runtime,
                    "compile_latex",
                    return_value={
                        "success": True,
                        "pdf_path": str(pdf),
                        "timeout": False,
                        "log_file": "main.log",
                    },
                ),
                mock.patch.object(
                    orchestrator_runtime,
                    "render_pdf_pages",
                    return_value={"success": True, "page_dir": "data/pages"},
                ),
                mock.patch.object(
                    orchestrator_runtime,
                    "inspect_endmatter_float_intrusion",
                    side_effect=hard_guard_reports,
                ),
                mock.patch.object(OrchestratorRuntime, "_run_round_core", fake_core),
                mock.patch.object(OrchestratorRuntime, "execute_repair_plan", fake_execute),
            ):
                result = OrchestratorRuntime(state_path=str(root / "data" / "state.json")).run_task(
                    task_spec=spec,
                    output_path="data/run_result_full_vto.json",
                )

            self.assertEqual(core_calls["count"], 1)
            self.assertEqual(main_tex.read_text(encoding="utf-8"), original_tex)
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["gatekeeper_decision"], "BLOCKED")
            self.assertEqual(result["failure"]["failure_type"], "post_repair_hard_guard_failed")
            self.assertTrue(result["runtime_actions"]["rollback_to_snapshot"]["success"])
            self.assertEqual(
                result["runtime_actions"]["post_repair_observe"]["visual_hard_guards"]["hard_failures"],
                hard_guard_reports[1]["hard_failures"],
            )
            policy = result["repair_loop_policy"]
            self.assertEqual(policy["stop_condition"], "post_repair_hard_guard_failed")
            self.assertEqual(policy["next_round_reason"], "post_repair_hard_guard_failed")
            self.assertFalse(policy["next_round_allowed"])
            state = json.loads((root / "data" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["content_integrity"]["validation_status"], "rolled_back")
            self.assertEqual(state["content_integrity"]["action_taken"], "restore_snapshot")
            self.assertEqual(state["artifacts"]["rollback_report"], "data/rollback_report_post_repair_hard_guard.json")
            self.assertTrue((root / "data" / "rollback_report_post_repair_hard_guard.json").is_file())

    def test_retryable_post_repair_hard_guard_survives_task_init(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            runtime = OrchestratorRuntime(state_path=str(root / "data" / "state.json"))
            runtime.init_task(main_tex="main.tex", task_type="full_vto", max_rounds=2)
            evidence = {
                "status": "blocked",
                "failure_type": "post_repair_hard_guard_failed",
                "reason": "post_repair_visual_hard_guard_failed",
                "visual_hard_guards": {
                    "available": True,
                    "endmatter_start_page": 8,
                    "endmatter_heading": "Acknowledgements",
                    "intrusions": [{"page": 8, "captions": ["Figure 7:"]}],
                    "hard_failures": ["Body float caption detected on endmatter pages"],
                },
            }
            runtime.manager.update({"post_repair_hard_guard": evidence})

            loaded = runtime._load_retryable_post_repair_hard_guard()
            runtime.init_task(main_tex="main.tex", task_type="full_vto", max_rounds=2)
            runtime._restore_retryable_post_repair_hard_guard(loaded)

            state = runtime.manager.load()
            self.assertEqual(state["post_repair_hard_guard"], evidence)

            runtime._clear_resolved_post_repair_hard_guard(
                post_observe={"visual_hard_guards": {"available": True, "hard_failures": []}}
            )
            self.assertIsNone(runtime.manager.load()["post_repair_hard_guard"])
            self.assertEqual(
                OrchestratorRuntime._selected_float_labels_from_state(
                    {
                        "repair_execution_summary": {
                            "selected_candidates": [
                                {"defect_id": "B2", "object": "fig:problem", "page": 3}
                            ]
                        }
                    }
                ),
                {"fig:problem"},
            )

    def test_hard_guard_learning_annotates_only_visual_b2_figure_width_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            cwd_before = Path.cwd()
            try:
                os.chdir(root)
                runtime = OrchestratorRuntime(state_path=str(root / "data" / "state.json"))
                runtime.init_task(main_tex="main.tex", task_type="full_vto", max_rounds=2)
                runtime.manager.update(
                    {
                        "post_repair_hard_guard": {
                            "status": "blocked",
                            "failure_type": "post_repair_hard_guard_failed",
                            "reason": "post_repair_visual_hard_guard_failed",
                            "visual_hard_guards": {
                                "available": True,
                                "endmatter_start_page": 8,
                                "endmatter_heading": "Acknowledgements",
                                "intrusions": [{"page": 8, "captions": ["Figure 7:"]}],
                                "hard_failures": ["Body float caption detected on endmatter pages"],
                            },
                        }
                    }
                )
                plan_path = root / "data" / "repair_plan.json"
                plan_path.write_text(
                    json.dumps(
                        {
                            "schema_version": "1.0",
                            "candidates": [
                                {
                                    "candidate_type": "object",
                                    "defect_family": "B2",
                                    "priority_score": 192,
                                    "target": {"label": "fig:problem"},
                                    "proposed_action": "adjust_float_width",
                                    "visual_width_subtype": "overflow_width",
                                    "evidence_sources": ["visual_signal_report"],
                                },
                                {
                                    "candidate_type": "object",
                                    "defect_family": "B2",
                                    "priority_score": 126,
                                    "target": {"label": "fig:other"},
                                    "proposed_action": "adjust_float_width",
                                    "visual_width_subtype": "underfilled_width",
                                },
                                {
                                    "candidate_type": "source_anchor",
                                    "defect_family": "B2",
                                    "priority_score": 70,
                                    "target": {"label": "fig:source_only"},
                                    "proposed_action": "adjust_float_width",
                                },
                                {
                                    "candidate_type": "object",
                                    "defect_family": "B2",
                                    "priority_score": 125,
                                    "target": {"label": "tab:wide"},
                                    "proposed_action": "adjust_float_width",
                                    "visual_width_subtype": "overflow_width",
                                },
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                runtime._retryable_post_repair_hard_guard_labels = {"fig:problem"}

                learning = runtime._annotate_repair_plan_with_post_repair_hard_guard_learning(
                    repair_plan_path="data/repair_plan.json",
                )

                self.assertEqual(learning["status"], "applied")
                self.assertEqual(learning["annotated_candidates"], 1)
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                visual_fig = plan["candidates"][0]
                self.assertTrue(visual_fig["allow_floatbarrier"])
                self.assertTrue(visual_fig["requires_floatbarrier"])
                self.assertTrue(visual_fig["endmatter_float_intrusion"])
                self.assertIn("post_repair_hard_guard", visual_fig["evidence_sources"])
                self.assertEqual(
                    visual_fig["hard_guard_evidence"]["failure_type"],
                    "endmatter_float_intrusion",
                )
                self.assertNotIn("allow_floatbarrier", plan["candidates"][1])
                self.assertNotIn("allow_floatbarrier", plan["candidates"][2])
                self.assertNotIn("allow_floatbarrier", plan["candidates"][3])
                self.assertEqual(
                    plan["post_repair_hard_guard_learning"]["policy"],
                    "allow_floatbarrier_only_after_endmatter_intrusion_failure",
                )
            finally:
                os.chdir(cwd_before)

    def test_apply_max_rounds_two_executes_second_repair_round(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "Round zero fixture.\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            pdf = root / "main.pdf"
            pdf.write_bytes(b"%PDF fixture\n")
            core_calls = {"count": 0}
            execute_calls = {"count": 0}

            def write_runtime_artifacts(decision: str, base_mtime: int) -> None:
                page_dir = root / "data" / "pages"
                page_dir.mkdir(parents=True, exist_ok=True)
                page = page_dir / "page_001.png"
                visual_report = root / "data" / "visual_signal_report.json"
                defect_report = root / "data" / "defect_report.json"
                gatekeeper_report = root / "data" / "gatekeeper_decision.json"
                page.write_bytes(b"png")
                visual_report.write_text("{}", encoding="utf-8")
                defect_report.write_text("{}", encoding="utf-8")
                gatekeeper_report.write_text(json.dumps({"decision": decision}), encoding="utf-8")
                for offset, path in enumerate([pdf, page, visual_report, defect_report, gatekeeper_report]):
                    os.utime(path, (base_mtime + offset, base_mtime + offset))

            def write_repair_plan(label: str) -> None:
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
                                    "target": {"scope": "overflow", "label": label},
                                    "description": f"repair {label}",
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )

            def fake_core(
                self: OrchestratorRuntime,
                *,
                emit_event: object | None = None,
                runtime_actions: dict[str, object] | None = None,
                **_: object,
            ) -> dict[str, object]:
                core_calls["count"] += 1
                decision = "DONE" if core_calls["count"] == 3 else "CONTINUE"
                write_runtime_artifacts(decision, 1_800_010_000 + core_calls["count"] * 100)
                if decision == "CONTINUE":
                    write_repair_plan(f"round-{core_calls['count']}")
                    total_candidates = 1
                else:
                    total_candidates = 0
                self.manager.update(
                    {
                        "compile_success": True,
                        "page_images_rendered": True,
                        "last_gatekeeper_decision": decision,
                        "defect_summary": {
                            "initial_total": 2,
                            "resolved": 2 if decision == "DONE" else core_calls["count"] - 1,
                            "remaining": 0 if decision == "DONE" else 1,
                        },
                        "repair_plan_summary": {
                            "schema_version": "1.0",
                            "total_candidates": total_candidates,
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
                self._record_runtime_action(
                    "gatekeeper_enforcer",
                    {
                        "success": True,
                        "source": "generated",
                        "output_path": "data/gatekeeper_decision.json",
                        "decision": decision,
                    },
                    phase="verify",
                    state="VERIFYING",
                    emit_event=emit_event if callable(emit_event) else None,
                    runtime_actions=runtime_actions,
                )
                return self.manager.load()

            def fake_execute(self: OrchestratorRuntime, *_: object, **__: object) -> dict[str, object]:
                execute_calls["count"] += 1
                main_tex.write_text(
                    "\\documentclass{article}\n"
                    "\\begin{document}\n"
                    f"Mutated by fake repair round {execute_calls['count']}.\n"
                    "\\end{document}\n",
                    encoding="utf-8",
                )
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
                    "max_rounds": 2,
                }
            )

            with (
                mock.patch.object(
                    orchestrator_runtime,
                    "compile_latex",
                    return_value={
                        "success": True,
                        "pdf_path": str(pdf),
                        "timeout": False,
                        "log_file": "main.log",
                    },
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
            self.assertEqual(core_calls["count"], 3)
            self.assertEqual(execute_calls["count"], 2)
            policy = result["repair_loop_policy"]
            self.assertEqual(policy["execution_mode"], "bounded_apply")
            self.assertEqual(policy["current_round"], 2)
            self.assertEqual(policy["round_limit"], 2)
            self.assertFalse(policy["next_round_allowed"])
            self.assertEqual(policy["next_round_reason"], "gatekeeper_done")
            self.assertTrue(
                policy["second_round_apply_readiness"]["checks"]["runtime_execution_mode_can_auto_apply"]
            )
            self.assertEqual([entry["round"] for entry in result["round_artifact_lineage"]], [1, 2])
            for entry in result["round_artifact_lineage"]:
                self.assertIn("repair_plan_executor", entry["actions"])
                self.assertIn("post_repair_observe", entry["actions"])
                self.assertIn("gatekeeper_enforcer", entry["actions"])

    def test_second_round_scope_gate_blocks_without_second_executor_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "Scope gate fixture.\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            pdf = root / "main.pdf"
            pdf.write_bytes(b"%PDF fixture\n")
            core_calls = {"count": 0}
            execute_calls = {"count": 0}

            def write_runtime_artifacts(base_mtime: int) -> None:
                page_dir = root / "data" / "pages"
                page_dir.mkdir(parents=True, exist_ok=True)
                page = page_dir / "page_001.png"
                visual_report = root / "data" / "visual_signal_report.json"
                defect_report = root / "data" / "defect_report.json"
                gatekeeper_report = root / "data" / "gatekeeper_decision.json"
                page.write_bytes(b"png")
                visual_report.write_text("{}", encoding="utf-8")
                defect_report.write_text("{}", encoding="utf-8")
                gatekeeper_report.write_text(json.dumps({"decision": "CONTINUE"}), encoding="utf-8")
                for offset, path in enumerate([pdf, page, visual_report, defect_report, gatekeeper_report]):
                    os.utime(path, (base_mtime + offset, base_mtime + offset))

            def write_repair_plan(high_risk: bool) -> None:
                if high_risk:
                    candidate = {
                        "candidate_type": "source_anchor",
                        "defect_family": "B1",
                        "priority_score": 200,
                        "section_distance": 2,
                        "target": {"label": "fig:far", "float_type": "figure"},
                        "proposed_action": "move_float_closer_to_first_reference",
                    }
                else:
                    candidate = {
                        "candidate_type": "object",
                        "defect_family": "D1",
                        "priority_score": 100,
                        "target": {"scope": "overflow", "label": "eq:first"},
                        "description": "first bounded repair",
                    }
                (root / "data" / "repair_plan.json").write_text(
                    json.dumps(
                        {
                            "schema_version": "1.0",
                            "summary": {"total_candidates": 1},
                            "candidates": [candidate],
                        }
                    ),
                    encoding="utf-8",
                )

            def fake_core(
                self: OrchestratorRuntime,
                *,
                emit_event: object | None = None,
                runtime_actions: dict[str, object] | None = None,
                **_: object,
            ) -> dict[str, object]:
                core_calls["count"] += 1
                write_runtime_artifacts(1_800_020_000 + core_calls["count"] * 100)
                write_repair_plan(high_risk=core_calls["count"] >= 2)
                self.manager.update(
                    {
                        "compile_success": True,
                        "page_images_rendered": True,
                        "last_gatekeeper_decision": "CONTINUE",
                        "defect_summary": {
                            "initial_total": 2,
                            "resolved": max(0, core_calls["count"] - 1),
                            "remaining": 1,
                        },
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
                self._record_runtime_action(
                    "gatekeeper_enforcer",
                    {
                        "success": True,
                        "source": "generated",
                        "output_path": "data/gatekeeper_decision.json",
                        "decision": "CONTINUE",
                    },
                    phase="verify",
                    state="VERIFYING",
                    emit_event=emit_event if callable(emit_event) else None,
                    runtime_actions=runtime_actions,
                )
                return self.manager.load()

            def fake_execute(self: OrchestratorRuntime, *_: object, **__: object) -> dict[str, object]:
                execute_calls["count"] += 1
                if execute_calls["count"] > 1:
                    raise AssertionError("second high-risk round must be blocked before executor")
                main_tex.write_text(
                    "\\documentclass{article}\n"
                    "\\begin{document}\n"
                    "First bounded repair applied.\n"
                    "\\end{document}\n",
                    encoding="utf-8",
                )
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
                    "max_rounds": 2,
                }
            )

            with (
                mock.patch.object(
                    orchestrator_runtime,
                    "compile_latex",
                    return_value={
                        "success": True,
                        "pdf_path": str(pdf),
                        "timeout": False,
                        "log_file": "main.log",
                    },
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

            self.assertEqual(execute_calls["count"], 1)
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["gatekeeper_decision"], "BLOCKED")
            self.assertEqual(result["failure"]["failure_type"], "approval_scope_blocked")
            action = result["runtime_actions"]["repair_plan_executor"]
            self.assertTrue(action["skipped"])
            self.assertEqual(action["reason"], "approval_scope_blocked")
            self.assertEqual(action["approval_scope_gate"]["status"], "blocked")
            policy = result["repair_loop_policy"]
            self.assertEqual(policy["stop_condition"], "approval_scope_blocked")
            self.assertFalse(policy["next_round_allowed"])
            self.assertFalse(
                policy["second_round_apply_readiness"]["checks"]["candidate_approval_scope_gate_pass"]
            )
            self.assertEqual([entry["round"] for entry in result["round_artifact_lineage"]], [1, 2])
            self.assertIn("post_repair_observe", result["round_artifact_lineage"][0]["actions"])
            self.assertIn("repair_plan_executor", result["round_artifact_lineage"][1]["actions"])
            self.assertNotIn("source_mutation_integrity", result["round_artifact_lineage"][1]["actions"])

    def test_full_vto_runtime_blocks_high_risk_candidate_before_executor(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "High-risk gate fixture.\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            original_sha = hashlib.sha256(main_tex.read_bytes()).hexdigest()
            pdf = root / "main.pdf"
            pdf.write_bytes(b"%PDF fixture\n")

            def fake_core(
                self: OrchestratorRuntime,
                **_: object,
            ) -> dict[str, object]:
                page_dir = root / "data" / "pages"
                page_dir.mkdir(parents=True, exist_ok=True)
                page = page_dir / "page_001.png"
                page.write_bytes(b"png")
                visual_report = root / "data" / "visual_signal_report.json"
                defect_report = root / "data" / "defect_report.json"
                gatekeeper_report = root / "data" / "gatekeeper_decision.json"
                repair_plan = root / "data" / "repair_plan.json"
                visual_report.write_text("{}", encoding="utf-8")
                defect_report.write_text("{}", encoding="utf-8")
                gatekeeper_report.write_text("{}", encoding="utf-8")
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
                                    "section_distance": 2,
                                    "target": {"label": "fig:far", "float_type": "figure"},
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                base_mtime = 1_800_001_000
                for offset, path in enumerate([pdf, page, visual_report, defect_report, gatekeeper_report]):
                    os.utime(path, (base_mtime + offset, base_mtime + offset))
                self.manager.update(
                    {
                        "compile_success": True,
                        "page_images_rendered": True,
                        "last_gatekeeper_decision": "CONTINUE",
                        "defect_summary": {
                            "initial_total": 1,
                            "resolved": 0,
                            "remaining": 1,
                        },
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
                    return_value={
                        "success": True,
                        "pdf_path": str(pdf),
                        "timeout": False,
                        "log_file": "main.log",
                    },
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
                mock.patch.object(
                    OrchestratorRuntime,
                    "execute_repair_plan",
                    side_effect=AssertionError("high-risk candidate must be blocked before executor"),
                ),
            ):
                result = OrchestratorRuntime(state_path=str(root / "data" / "state.json")).run_task(
                    task_spec=spec,
                    output_path="data/run_result_full_vto.json",
                )

            self.assertEqual(hashlib.sha256(main_tex.read_bytes()).hexdigest(), original_sha)
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["gatekeeper_decision"], "BLOCKED")
            self.assertEqual(result["failure"]["failure_type"], "approval_scope_blocked")
            action = result["runtime_actions"]["repair_plan_executor"]
            self.assertTrue(action["skipped"])
            self.assertEqual(action["reason"], "approval_scope_blocked")
            self.assertEqual(action["approval_scope_gate"]["status"], "blocked")
            self.assertEqual(
                action["approval_scope_gate"]["blocked_candidates"][0]["risk"]["operation"],
                "float_movement_across_section_boundary",
            )
            self.assertNotIn("source_mutation_integrity", result["runtime_actions"])
            self.assertNotIn("post_repair_observe", result["runtime_actions"])
            self.assertEqual(result["approval"]["status"], "approval_required")
            self.assertEqual(result["repair_loop_policy"]["stop_condition"], "approval_scope_blocked")
            self.assertEqual(result["repair_loop_policy"]["next_round_reason"], "approval_scope_blocked")
            self.assertFalse(
                result["repair_loop_policy"]["second_round_apply_readiness"]["checks"][
                    "candidate_approval_scope_gate_pass"
                ]
            )

    def test_full_vto_dry_run_does_not_execute_repair_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "Dry run fixture.\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            original_sha = hashlib.sha256(main_tex.read_bytes()).hexdigest()
            pdf = root / "main.pdf"
            pdf.write_bytes(b"%PDF fixture\n")

            def fake_core(
                self: OrchestratorRuntime,
                **_: object,
            ) -> dict[str, object]:
                self.manager.update(
                    {
                        "compile_success": True,
                        "page_images_rendered": True,
                        "last_gatekeeper_decision": "CONTINUE",
                        "defect_summary": {
                            "initial_total": 1,
                            "resolved": 0,
                            "remaining": 1,
                        },
                        "repair_plan_summary": {
                            "schema_version": "1.0",
                            "total_candidates": 1,
                            "top_candidates": [],
                            "updated_at": None,
                        },
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
                    "dry_run_source_mutation": True,
                    "rollback_policy": "required",
                }
            )

            with (
                mock.patch.object(
                    orchestrator_runtime,
                    "compile_latex",
                    return_value={
                        "success": True,
                        "pdf_path": str(pdf),
                        "timeout": False,
                        "log_file": "main.log",
                    },
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
                mock.patch.object(
                    OrchestratorRuntime,
                    "execute_repair_plan",
                    side_effect=AssertionError("dry-run must not execute repair plan"),
                ),
            ):
                result = OrchestratorRuntime(state_path=str(root / "data" / "state.json")).run_task(
                    task_spec=spec,
                    output_path="data/run_result_full_vto_dry_run.json",
                )

            self.assertEqual(hashlib.sha256(main_tex.read_bytes()).hexdigest(), original_sha)
            action = result["runtime_actions"]["repair_plan_executor"]
            self.assertTrue(action["skipped"])
            self.assertEqual(action["reason"], "dry_run_source_mutation")
            self.assertEqual(action["action_name"], "repair_plan_executor")
            self.assertEqual(action["phase"], "repair")
            self.assertEqual(action["runtime_state"], "REPAIRING")
            self.assertEqual(action["risk_level"], "high")
            self.assertTrue(action["requires_approval"])
            self.assertEqual(action["input_artifacts"]["main_tex"], "main.tex")
            self.assertTrue(action["input_artifacts"]["rollback_target"].endswith("snapshot_manifest.json"))
            self.assertEqual(action["output_artifacts"], {})
            self.assertEqual(result["approval"]["status"], "approval_required")
            self.assertTrue(result["approval"]["requires_approval"])
            self.assertFalse(result["approval"]["approval_granted"])
            self.assertEqual(result["approval"]["reason"], "dry_run_source_mutation")
            self.assertEqual(result["approval"]["plan"]["candidates"], 1)
            self.assertIn("--apply", result["approval"]["approval_mechanisms"])
            policy = result["repair_loop_policy"]
            self.assertEqual(policy["execution_mode"], "report_only")
            self.assertEqual(policy["round_limit"], spec.max_rounds)
            self.assertEqual(policy["candidate_batch_limit"], 0)
            self.assertEqual(policy["stop_condition"], "approval_required")
            self.assertFalse(policy["next_round_allowed"])
            self.assertEqual(policy["next_round_reason"], "dry_run_source_mutation")
            self.assertEqual(policy["approval_scope_carry_forward"]["status"], "pass")
            self.assertEqual(policy["second_round_apply_readiness"]["status"], "blocked")
            self.assertFalse(policy["second_round_apply_readiness"]["checks"]["source_mutation_executed"])
            self.assertEqual(result["round_artifact_lineage"][0]["round"], 1)
            self.assertIn("repair_plan_executor", result["round_artifact_lineage"][0]["actions"])
            self.assertNotIn("post_repair_observe", result["runtime_actions"])
            event_log = root / result["event_log"]
            events = [
                json.loads(line)
                for line in event_log.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            states = [event.get("state") for event in events]
            for expected in ["READY", "OBSERVING", "DIAGNOSING", "PLANNING", "REPAIRING", "VERIFYING", "CONTINUE"]:
                self.assertIn(expected, states)
            self.assertEqual(events[-1]["type"], "task_completed")
            self.assertEqual(events[-1]["state"], "CONTINUE")
            state = json.loads((root / "data" / "state.json").read_text(encoding="utf-8"))
            event_action = state["runtime_event_summary"]["actions"]["repair_plan_executor"]
            self.assertEqual(event_action["input_artifacts"]["main_tex"], "main.tex")
            self.assertTrue(event_action["input_artifacts"]["rollback_target"].endswith("snapshot_manifest.json"))

    def test_source_changing_run_result_reports_loop_policy_without_widening_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "Loop policy fixture.\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            pdf = root / "main.pdf"
            pdf.write_bytes(b"%PDF fixture\n")

            def fake_core(
                self: OrchestratorRuntime,
                **_: object,
            ) -> dict[str, object]:
                self.manager.update(
                    {
                        "compile_success": True,
                        "page_images_rendered": True,
                        "last_gatekeeper_decision": "CONTINUE",
                        "defect_summary": {
                            "initial_total": 2,
                            "resolved": 1,
                            "remaining": 1,
                        },
                        "repair_plan_summary": {
                            "schema_version": "1.0",
                            "total_candidates": 2,
                            "top_candidates": [],
                            "updated_at": None,
                        },
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
                    "dry_run_source_mutation": True,
                    "rollback_policy": "required",
                    "max_rounds": 3,
                }
            )

            with (
                mock.patch.object(
                    orchestrator_runtime,
                    "compile_latex",
                    return_value={
                        "success": True,
                        "pdf_path": str(pdf),
                        "timeout": False,
                        "log_file": "main.log",
                    },
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
            ):
                result = OrchestratorRuntime(state_path=str(root / "data" / "state.json")).run_task(
                    task_spec=spec,
                    output_path="data/run_result_full_vto_dry_run.json",
                )

            policy = result["repair_loop_policy"]
            self.assertEqual(policy["execution_mode"], "report_only")
            self.assertEqual(policy["round_limit"], 3)
            self.assertEqual(policy["current_round"], 1)
            self.assertEqual(policy["candidate_batch_limit"], 0)
            self.assertEqual(policy["plan_candidates"], 2)
            self.assertFalse(policy["next_round_allowed"])
            self.assertEqual(policy["next_round_reason"], "dry_run_source_mutation")
            self.assertEqual(policy["approval_scope_carry_forward"]["status"], "pass")
            self.assertEqual(policy["second_round_apply_readiness"]["status"], "blocked")
            self.assertIn("repair_plan_executor", policy["round_artifact_lineage"][0]["actions"])


if __name__ == "__main__":
    unittest.main()
