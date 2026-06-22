from __future__ import annotations

import hashlib
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

import paperfit_command  # noqa: E402
import orchestrator_runtime  # noqa: E402
from orchestrator_runtime import OrchestratorRuntime  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CheckVisualRuntimeTest(unittest.TestCase):
    def test_infer_repair_this_paper_layout_as_full_vto(self) -> None:
        inferred = OrchestratorRuntime.infer_task_from_request(
            "repair this paper layout with minimal semantic change"
        )
        self.assertEqual(inferred["task_type"], "full_vto")

    def test_infer_priority_request_as_read_only_priority_query(self) -> None:
        inferred = OrchestratorRuntime.infer_task_from_request("/paperfit-priority")
        self.assertEqual(inferred["task_type"], "priority_query")

    def test_check_visual_uses_visual_only_runtime_contract_without_source_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            data_dir = root / "data"
            data_dir.mkdir()
            pages_dir = data_dir / "pages"
            pages_dir.mkdir()
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "A small PaperFit fixture.\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            pdf = root / "main.pdf"
            pdf.write_bytes(b"%PDF fixture\n")
            (pages_dir / "page_001.png").write_bytes(b"png fixture\n")
            (data_dir / "column_void_report.json").write_text(
                json.dumps(
                    {
                        "a5_candidate_pages": [1],
                        "max_void_ratio": 0.5,
                    }
                ),
                encoding="utf-8",
            )

            tex_hash_before = _sha256(main_tex)
            captured: dict[str, object] = {}

            def fake_run_round(
                self: OrchestratorRuntime,
                main_tex: str,
                log_file: str | None = None,
                page_dir: str | None = None,
                template: str | None = None,
                target_pages: int | None = None,
                column_void_report: str | None = None,
                emit_event: object | None = None,
                runtime_actions: dict[str, object] | None = None,
                **_: object,
            ) -> dict[str, object]:
                captured.update(
                    {
                        "main_tex": main_tex,
                        "log_file": log_file,
                        "page_dir": page_dir,
                        "template": template,
                        "target_pages": target_pages,
                        "column_void_report": column_void_report,
                    }
                )
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
                        "artifacts": {
                            "page_images_dir": "data/pages",
                            "column_void_report": "data/column_void_report.json",
                            "gatekeeper_decision": "data/gatekeeper_decision.json",
                        },
                    }
                )
                self._record_runtime_action(
                    "visual_signal_aggregator",
                    {
                        "success": True,
                        "output_path": "data/visual_signal_report.json",
                        "findings_count": 0,
                        "priority_pages": [],
                    },
                    phase="diagnose",
                    state="DIAGNOSING",
                    emit_event=emit_event if callable(emit_event) else None,
                    runtime_actions=runtime_actions,
                )
                self._record_runtime_action(
                    "defect_report_builder",
                    {
                        "success": True,
                        "output_path": "data/defect_report.json",
                        "defects_count": 1,
                    },
                    phase="diagnose",
                    state="DIAGNOSING",
                    emit_event=emit_event if callable(emit_event) else None,
                    runtime_actions=runtime_actions,
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
                gatekeeper = root / "data" / "gatekeeper_decision.json"
                gatekeeper.write_text('{"decision":"CONTINUE"}\n', encoding="utf-8")
                return self.manager.load()

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
                mock.patch.object(OrchestratorRuntime, "_run_round_core", fake_run_round),
            ):
                report = paperfit_command._run_visual_only(
                    root,
                    main_tex=main_tex,
                    template="AAAI2025",
                    target_pages=9,
                )

            self.assertEqual(tex_hash_before, _sha256(main_tex))
            self.assertEqual(
                report["pre_compile_sanitization"]["skipped_reason"],
                "visual_only_disallows_source_mutation",
            )
            self.assertEqual(captured["main_tex"], "main.tex")
            self.assertEqual(captured["page_dir"], "data/pages")
            self.assertEqual(captured["column_void_report"], "data/column_void_report.json")

            task = json.loads((data_dir / "task.json").read_text(encoding="utf-8"))
            self.assertFalse((data_dir / "task_visual_only.json").exists())
            self.assertEqual(task["task_type"], "visual_only")
            self.assertFalse(task["allow_source_mutation"])
            self.assertEqual(task["required_phases"], ["observe", "diagnose", "verify"])
            self.assertEqual(task["column_void_report"], "data/column_void_report.json")

            state = json.loads((data_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["task"]["type"], "visual_only")
            self.assertEqual(state["artifacts"]["task_spec"], "data/task.json")
            event_summary = state["runtime_event_summary"]
            self.assertEqual(event_summary["event_log"].split("/")[:2], ["data", "events"])
            self.assertEqual(event_summary["last_event_type"], "task_completed")
            self.assertEqual(event_summary["last_runtime_state"], "CONTINUE")
            self.assertGreaterEqual(event_summary["event_count"], 8)
            self.assertTrue(event_summary["actions"]["compile"]["success"])
            self.assertTrue(event_summary["actions"]["render_pages"]["success"])
            self.assertTrue(event_summary["actions"]["visual_signal_aggregator"]["success"])
            self.assertTrue(event_summary["actions"]["defect_report_builder"]["success"])
            self.assertEqual(event_summary["actions"]["gatekeeper_enforcer"]["runtime_state"], "VERIFYING")

            run_result = json.loads((data_dir / "run_result_check_visual.json").read_text(encoding="utf-8"))
            self.assertEqual(run_result["task"]["task_type"], "visual_only")
            self.assertFalse(run_result["task"]["allow_source_mutation"])
            self.assertEqual(run_result["gatekeeper_decision"], "CONTINUE")
            self.assertEqual(run_result["defect_summary"]["remaining"], 1)
            self.assertTrue(run_result["runtime_actions"]["compile"]["success"])
            self.assertEqual(run_result["runtime_actions"]["compile"]["action_name"], "compile")
            self.assertEqual(run_result["runtime_actions"]["compile"]["phase"], "observe")
            self.assertFalse(run_result["runtime_actions"]["compile"]["requires_approval"])
            self.assertTrue(run_result["runtime_actions"]["render"]["success"])
            self.assertEqual(run_result["runtime_actions"]["render"]["action_name"], "render_pages")
            self.assertTrue(run_result["runtime_actions"]["visual_signal_aggregator"]["success"])
            self.assertTrue(run_result["runtime_actions"]["defect_report_builder"]["success"])
            self.assertEqual(run_result["runtime_actions"]["gatekeeper_enforcer"]["decision"], "CONTINUE")
            self.assertEqual(
                run_result["artifact_manifest"]["artifacts"]["task_spec"]["path"],
                "data/task.json",
            )
            self.assertEqual(
                run_result["artifact_manifest"]["artifacts"]["main_tex"]["sha256"],
                tex_hash_before,
            )

            event_log = root / run_result["event_log"]
            events = [
                json.loads(line)
                for line in event_log.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(events[0]["type"], "task_started")
            self.assertIn("runtime_action_completed", [event["type"] for event in events])
            self.assertIn("gatekeeper_result", [event["type"] for event in events])
            self.assertEqual(events[-1]["type"], "task_completed")

            check_report = json.loads((data_dir / "check_visual_report.json").read_text(encoding="utf-8"))
            self.assertEqual(check_report["runtime_contract"], "visual_only")
            self.assertEqual(check_report["run_result"]["task"]["task_type"], "visual_only")
            self.assertEqual(check_report["state_summary"]["run_result_path"], "data/run_result_check_visual.json")
            self.assertIn(check_report["state_summary"]["artifact_freshness"]["status"], {"pass", "stale_or_missing"})
            self.assertIn("runtime", check_report["state_summary"])

    def test_check_visual_compile_timeout_blocks_before_round_observation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            data_dir = root / "data"
            data_dir.mkdir()
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "Timeout fixture.\n"
                "\\end{document}\n",
                encoding="utf-8",
            )

            with (
                mock.patch.object(
                    orchestrator_runtime,
                    "compile_latex",
                    return_value={
                        "success": False,
                        "timeout": True,
                        "timeout_sec": 12,
                        "pdf_path": None,
                    },
                ),
                mock.patch.object(orchestrator_runtime, "render_pdf_pages") as render_mock,
                mock.patch.object(
                    OrchestratorRuntime,
                    "_run_round_core",
                    side_effect=AssertionError("_run_round_core should not run after compile timeout"),
                ),
            ):
                report = paperfit_command._run_visual_only(
                    root,
                    main_tex=main_tex,
                    template=None,
                    target_pages=None,
                )

            render_mock.assert_not_called()
            self.assertEqual(report["compile"]["timeout"], True)
            self.assertEqual(report["state_summary"]["status"], "BLOCKED")
            self.assertEqual(report["state_summary"]["last_gatekeeper_decision"], "BLOCKED")
            self.assertEqual(report["run_result"]["status"], "blocked")
            self.assertEqual(report["run_result"]["runtime_actions"]["render"]["success"], False)
            self.assertEqual(
                report["run_result"]["failure"]["failure_type"],
                "compile_timeout",
            )
            self.assertEqual(
                json.loads((data_dir / "state.json").read_text(encoding="utf-8"))["artifacts"]["task_spec"],
                "data/task.json",
            )
            timeout_state = json.loads((data_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(timeout_state["runtime_event_summary"]["last_event_type"], "task_blocked")
            self.assertEqual(timeout_state["runtime_event_summary"]["last_runtime_state"], "BLOCKED")
            self.assertTrue(timeout_state["runtime_event_summary"]["actions"]["compile"]["timeout"])
            self.assertEqual(
                timeout_state["runtime_event_summary"]["actions"]["render_pages"]["reason"],
                "compile_failed",
            )

    def test_fix_layout_routes_full_vto_to_typed_runtime_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\\documentclass{article}\n\\begin{document}\nFixture.\n\\end{document}\n",
                encoding="utf-8",
            )

            with (
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch.object(
                    OrchestratorRuntime,
                    "infer_task_from_request",
                    return_value={"task_type": "full_vto"},
                ),
                mock.patch.object(paperfit_command, "_build_portrait", return_value={}),
                mock.patch.object(
                    paperfit_command,
                    "_run_typed_source_changing",
                    return_value={"mode": "typed_fix_layout"},
                ) as typed_runner,
                mock.patch.object(paperfit_command, "_run_fix_layout") as legacy_runner,
            ):
                report = paperfit_command._handle_paperfit_request(
                    root,
                    request="fix layout",
                    main_tex=main_tex,
                    template=None,
                    target_pages=None,
                    max_rounds=1,
                    save_as=None,
                )

            self.assertEqual(report["mode"], "typed_fix_layout")
            self.assertTrue(typed_runner.called)
            self.assertFalse(legacy_runner.called)
            self.assertFalse(typed_runner.call_args.kwargs["apply_source_mutation"])

    def test_run_agent_request_uses_agent_result_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\\documentclass{article}\n\\begin{document}\nFixture.\n\\end{document}\n",
                encoding="utf-8",
            )

            with (
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch.object(
                    OrchestratorRuntime,
                    "infer_task_from_request",
                    return_value={"task_type": "full_vto"},
                ),
                mock.patch.object(paperfit_command, "_build_portrait", return_value={}),
                mock.patch.object(
                    paperfit_command,
                    "_run_typed_source_changing",
                    return_value={"mode": "paperfit_agent"},
                ) as typed_runner,
            ):
                report = paperfit_command._handle_paperfit_request(
                    root,
                    request="repair this paper layout with minimal semantic change",
                    main_tex=main_tex,
                    template=None,
                    target_pages=None,
                    max_rounds=1,
                    save_as=None,
                    run_result_output_path="data/run_result_agent.json",
                    report_output_path="data/agent_report.json",
                    report_mode="paperfit_agent",
                )

            self.assertEqual(report["mode"], "paperfit_agent")
            self.assertEqual(typed_runner.call_args.kwargs["run_result_output_path"], "data/run_result_agent.json")
            self.assertEqual(typed_runner.call_args.kwargs["report_output_path"], "data/agent_report.json")
            self.assertEqual(typed_runner.call_args.kwargs["report_mode"], "paperfit_agent")

    def test_status_query_uses_runtime_status_without_visual_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            data_dir = root / "data"
            data_dir.mkdir()
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\\documentclass{article}\n\\begin{document}\nStatus fixture.\n\\end{document}\n",
                encoding="utf-8",
            )
            state = {
                "main_tex": "main.tex",
                "status": "EVALUATING",
                "task": {"type": "full_vto"},
                "runtime_event_summary": {"run_id": "run-status", "event_count": 4},
            }
            (data_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
            run_result = {
                "run_id": "run-status",
                "artifact_manifest": {"freshness": {"status": "pass", "blocking_checks": []}},
            }
            (data_dir / "run_result_agent.json").write_text(json.dumps(run_result), encoding="utf-8")

            with (
                mock.patch.object(
                    OrchestratorRuntime,
                    "infer_task_from_request",
                    return_value={"task_type": "status_query"},
                ),
                mock.patch.object(paperfit_command, "_build_portrait") as build_portrait,
                mock.patch.object(paperfit_command, "_run_visual_only") as visual_runner,
            ):
                report = paperfit_command._handle_paperfit_request(
                    root,
                    request="show status",
                    main_tex=main_tex,
                    template=None,
                    target_pages=None,
                    max_rounds=1,
                    save_as=None,
                )

            self.assertEqual(report["mode"], "status_query")
            self.assertEqual(report["runtime_contract"], "status_view")
            self.assertEqual(report["status_view"]["run_result_path"], "data/run_result_agent.json")
            self.assertEqual(report["status_view"]["runtime"]["run_id"], "run-status")
            self.assertFalse(build_portrait.called)
            self.assertFalse(visual_runner.called)
            self.assertTrue((data_dir / "status_query_report.json").is_file())

    def test_priority_query_uses_runtime_status_without_visual_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            data_dir = root / "data"
            data_dir.mkdir()
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\\documentclass{article}\n\\begin{document}\nPriority fixture.\n\\end{document}\n",
                encoding="utf-8",
            )
            state = {
                "main_tex": "main.tex",
                "status": "EVALUATING",
                "task": {"type": "full_vto"},
                "runtime_event_summary": {"run_id": "run-priority", "event_count": 5},
                "defect_summary": {"initial_total": 4, "resolved": 1, "remaining": 3},
                "next_actions": ["Review float placement before text edits"],
            }
            (data_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
            run_result = {
                "run_id": "run-priority",
                "runtime_actions": {
                    "repair_plan_executor": {
                        "skipped": True,
                        "reason": "dry_run_source_mutation",
                        "risk_level": "high",
                        "requires_approval": True,
                    }
                },
                "artifact_manifest": {"freshness": {"status": "pass", "blocking_checks": []}},
            }
            (data_dir / "run_result_agent.json").write_text(json.dumps(run_result), encoding="utf-8")

            with (
                mock.patch.object(
                    OrchestratorRuntime,
                    "infer_task_from_request",
                    return_value={"task_type": "priority_query"},
                ),
                mock.patch.object(paperfit_command, "_build_portrait") as build_portrait,
                mock.patch.object(paperfit_command, "_run_visual_only") as visual_runner,
            ):
                report = paperfit_command._handle_paperfit_request(
                    root,
                    request="/paperfit-priority",
                    main_tex=main_tex,
                    template=None,
                    target_pages=None,
                    max_rounds=1,
                    save_as=None,
                )

            self.assertEqual(report["mode"], "priority_query")
            self.assertEqual(report["runtime_contract"], "status_view")
            self.assertEqual(report["status_view"]["run_result_path"], "data/run_result_agent.json")
            self.assertEqual(report["status_view"]["runtime"]["run_id"], "run-priority")
            self.assertFalse(report["reader_mutator_boundary"]["state_mutation"])
            self.assertFalse(report["reader_mutator_boundary"]["source_mutation"])
            self.assertEqual(report["priority_items"][0]["priority"], "review_repair_plan_approval")
            self.assertFalse(build_portrait.called)
            self.assertFalse(visual_runner.called)
            self.assertTrue((data_dir / "priority_query_report.json").is_file())

    def test_undo_request_does_not_fall_through_to_visual_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\\documentclass{article}\n\\begin{document}\nUndo fixture.\n\\end{document}\n",
                encoding="utf-8",
            )

            with (
                mock.patch.object(
                    OrchestratorRuntime,
                    "infer_task_from_request",
                    return_value={"task_type": "undo_last_change"},
                ),
                mock.patch.object(paperfit_command, "_build_portrait") as build_portrait,
                mock.patch.object(paperfit_command, "_run_visual_only") as visual_runner,
            ):
                report = paperfit_command._handle_paperfit_request(
                    root,
                    request="undo last change",
                    main_tex=main_tex,
                    template=None,
                    target_pages=None,
                    max_rounds=1,
                    save_as=None,
                )

            self.assertEqual(report["mode"], "undo_last_change")
            self.assertEqual(report["status"], "manual_action_required")
            self.assertFalse(build_portrait.called)
            self.assertFalse(visual_runner.called)

    def test_fix_layout_legacy_env_routes_full_vto_to_legacy_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\\documentclass{article}\n\\begin{document}\nFixture.\n\\end{document}\n",
                encoding="utf-8",
            )

            with (
                mock.patch.dict(os.environ, {"PAPERFIT_LEGACY_FIX_LAYOUT": "1"}, clear=True),
                mock.patch.object(
                    OrchestratorRuntime,
                    "infer_task_from_request",
                    return_value={"task_type": "full_vto"},
                ),
                mock.patch.object(paperfit_command, "_build_portrait", return_value={}),
                mock.patch.object(paperfit_command, "_run_typed_source_changing") as typed_runner,
                mock.patch.object(
                    paperfit_command,
                    "_run_fix_layout",
                    return_value={
                        "mode": "fix_layout",
                        "final_state_summary": {},
                        "stop_reason": "done",
                        "round_count": 1,
                    },
                ) as legacy_runner,
            ):
                report = paperfit_command._handle_paperfit_request(
                    root,
                    request="fix layout",
                    main_tex=main_tex,
                    template=None,
                    target_pages=None,
                    max_rounds=1,
                    save_as=None,
                )

            self.assertEqual(report["mode"], "fix_layout")
            self.assertFalse(typed_runner.called)
            self.assertTrue(legacy_runner.called)

    def test_legacy_fix_layout_summaries_use_runtime_status_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "data").mkdir()
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\\documentclass{article}\n\\begin{document}\nLegacy fixture.\n\\end{document}\n",
                encoding="utf-8",
            )

            def fake_run_round(
                project_root: Path,
                *,
                main_tex: Path,
                template: str | None,
                target_pages: int | None,
                page_dir: str,
            ) -> dict[str, object]:
                state = {
                    "main_tex": main_tex.name,
                    "status": "EVALUATING",
                    "last_gatekeeper_decision": "CONTINUE",
                    "task": {"type": "full_vto", "template": template, "target_pages": target_pages},
                    "defect_summary": {"initial_total": 2, "resolved": 0, "remaining": 2},
                    "runtime_event_summary": {
                        "run_id": "legacy-run",
                        "event_log": "data/events/legacy-run.ndjson",
                        "event_count": 3,
                        "last_event_type": "task_completed",
                        "last_runtime_state": "CONTINUE",
                        "actions": {"gatekeeper_enforcer": {"success": True}},
                    },
                    "repair_plan_summary": {"total_candidates": 1},
                    "artifacts": {
                        "visual_signal_report": "data/visual_signal_report.json",
                        "defect_report": "data/defect_report.json",
                        "repair_plan": "data/repair_plan.json",
                    },
                    "next_actions": ["Review defects"],
                }
                (project_root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
                return state

            with (
                mock.patch.object(
                    paperfit_command,
                    "_compile",
                    return_value={"success": True, "pdf_path": None, "timeout": False},
                ),
                mock.patch.object(paperfit_command, "_sanitize_precompile_sources", return_value={"changed": False}),
                mock.patch.object(paperfit_command, "_run_round", side_effect=fake_run_round),
                mock.patch.object(
                    paperfit_command,
                    "_execute_repair_plan",
                    return_value={"repair_execution_summary": {"status": "noop", "applied_count": 0}},
                ),
            ):
                report = paperfit_command._run_fix_layout(
                    root,
                    main_tex=main_tex,
                    template="AAAI2024",
                    target_pages=None,
                    max_rounds=1,
                )

            iteration_summary = report["iterations"][0]["state_summary"]
            final_summary = report["final_state_summary"]
            self.assertEqual(iteration_summary["runtime"]["run_id"], "legacy-run")
            self.assertEqual(iteration_summary["repair"]["plan_candidates"], 1)
            self.assertIn("artifact_freshness", iteration_summary)
            self.assertEqual(final_summary["runtime"]["run_id"], "legacy-run")
            self.assertEqual(final_summary["next_actions"], ["Review defects"])

    def test_typed_fix_layout_zero_env_disables_default_route(self) -> None:
        with mock.patch.dict(os.environ, {"PAPERFIT_TYPED_FIX_LAYOUT": "0"}, clear=True):
            self.assertFalse(paperfit_command._typed_fix_layout_enabled())

        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(paperfit_command._typed_fix_layout_enabled())

    def test_typed_fix_layout_default_builds_dry_run_source_changing_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "data").mkdir()
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "Typed fix-layout fixture.\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            captured: dict[str, object] = {}

            def fake_run_task(
                self: OrchestratorRuntime,
                task_spec: object,
                output_path: str | None = None,
            ) -> dict[str, object]:
                captured["task"] = task_spec
                captured["output_path"] = output_path
                (root / "data" / "state.json").write_text(
                    json.dumps(
                        {
                            "project": "PaperFit",
                            "version": "1.0",
                            "main_tex": "main.tex",
                            "task": {"type": "full_vto"},
                            "artifacts": {},
                            "defect_summary": {"initial_total": 0, "resolved": 0, "remaining": 0},
                            "status": "EVALUATING",
                        }
                    ),
                    encoding="utf-8",
                )
                return {
                    "task": task_spec.to_dict(),
                    "status": "continue",
                    "gatekeeper_decision": "CONTINUE",
                    "runtime_actions": {},
                    "defect_summary": {"remaining": 0},
                }

            with mock.patch.dict(os.environ, {}, clear=True):
                with mock.patch.object(OrchestratorRuntime, "run_task", fake_run_task):
                    report = paperfit_command._run_typed_source_changing(
                        root,
                        task_type="full_vto",
                        main_tex=main_tex,
                        template=None,
                        target_pages=None,
                        max_rounds=1,
                        user_request="fix-layout",
                    )

            task = captured["task"]
            self.assertEqual(task.task_type, "full_vto")
            self.assertTrue(task.allow_source_mutation)
            self.assertTrue(task.pre_repair_snapshot_required)
            self.assertTrue(task.dry_run_source_mutation)
            self.assertEqual(task.rollback_policy, "required")
            self.assertEqual(captured["output_path"], "data/run_result_fix_layout_typed.json")
            self.assertEqual(report["mode"], "typed_fix_layout")
            self.assertEqual(report["run_result_path"], "data/run_result_fix_layout_typed.json")
            self.assertTrue(report["dry_run_source_mutation"])
            self.assertEqual(report["state_summary"]["run_result_path"], "data/run_result_fix_layout_typed.json")
            self.assertIn("artifact_freshness", report["state_summary"])

    def test_typed_fix_layout_apply_builds_non_dry_run_source_changing_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "data").mkdir()
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "Typed fix-layout apply fixture.\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            captured: dict[str, object] = {}

            def fake_run_task(
                self: OrchestratorRuntime,
                task_spec: object,
                output_path: str | None = None,
            ) -> dict[str, object]:
                captured["task"] = task_spec
                captured["output_path"] = output_path
                return {
                    "task": task_spec.to_dict(),
                    "status": "continue",
                    "runtime_actions": {},
                    "defect_summary": {"remaining": 0},
                }

            with mock.patch.dict(os.environ, {}, clear=True):
                with mock.patch.object(OrchestratorRuntime, "run_task", fake_run_task):
                    report = paperfit_command._run_typed_source_changing(
                        root,
                        task_type="full_vto",
                        main_tex=main_tex,
                        template=None,
                        target_pages=None,
                        max_rounds=1,
                        user_request="fix-layout",
                        apply_source_mutation=True,
                    )

            task = captured["task"]
            self.assertTrue(task.allow_source_mutation)
            self.assertFalse(task.dry_run_source_mutation)
            self.assertEqual(captured["output_path"], "data/run_result_fix_layout_typed.json")
            self.assertFalse(report["dry_run_source_mutation"])


if __name__ == "__main__":
    unittest.main()
