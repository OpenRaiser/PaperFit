#!/usr/bin/env python3
"""
Minimal executable runtime helpers for orchestrator state transitions.

This does not replace the full Claude-driven workflow yet. It enforces the
state mutations that must stay consistent regardless of prompt text.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from runtime_artifacts import collect_artifact_manifest
from runtime_actions import compile_latex, inspect_endmatter_float_intrusion, render_pdf_pages
from runtime_approval import build_approval_object
from runtime_events import RuntimeEventWriter
from runtime_repair_plan import (
    attach_repair_plan_fingerprint,
    blocked_stale_repair_plan_report,
    validate_repair_plan_freshness,
)
from runtime_repair_loop import build_repair_loop_policy, build_round_artifact_lineage
from runtime_mutation_integrity import build_source_mutation_report
from runtime_snapshots import create_pre_repair_snapshot, restore_snapshot
from runtime_status import build_runtime_status
from runtime_state_machine import SOURCE_CHANGING_STATE_MACHINE, VISUAL_ONLY_STATE_MACHINE
from runtime_types import ActionResult, RunResult, SOURCE_CHANGING_TASK_TYPES, TaskSpec, load_task_spec
from state_manager import StateManager


class OrchestratorRuntime:
    def __init__(self, state_path: str = StateManager.DEFAULT_STATE_PATH):
        self.manager = StateManager(state_path=state_path)

    def _python_executable(self) -> str:
        return sys.executable or "python3"

    @staticmethod
    def infer_task_from_request(request: str) -> Dict[str, Any]:
        text = str(request or "").strip()
        lowered = text.lower()
        normalized = re.sub(r"\s+", " ", lowered)

        def _has_any(*needles: str) -> bool:
            return any(needle in normalized for needle in needles if needle)

        def _extract_target_pages() -> Optional[int]:
            patterns = [
                r"(?:压到|缩到|减到|控制到|调整到|正文压到)\s*(\d+)\s*页",
                r"(?:to|into|down to|fit in)\s*(\d+)\s*pages?",
                r"(?:=|target-pages?\s+)(\d+)",
            ]
            for pattern in patterns:
                match = re.search(pattern, normalized, re.IGNORECASE)
                if match:
                    return int(match.group(1))
            return None

        def _extract_table_target() -> Optional[str]:
            label_match = re.search(r"\b(tab:[\w:-]+)\b", text, re.IGNORECASE)
            if label_match:
                return label_match.group(1)
            number_match = re.search(r"\btable\s*(\d+)\b", normalized, re.IGNORECASE)
            if number_match:
                return f"Table {number_match.group(1)}"
            cn_match = re.search(r"表\s*(\d+)", text)
            if cn_match:
                return f"Table {cn_match.group(1)}"
            return None

        def _extract_template_name() -> Optional[str]:
            known_templates = [
                "cvpr", "iclr", "eccv", "neurips", "aaai", "icml", "ieee", "acm", "arxiv",
            ]
            for template in known_templates:
                match = re.search(rf"\b{template}[0-9]{{0,4}}\b", normalized, re.IGNORECASE)
                if match:
                    return match.group(0).upper()
            cn_match = re.search(r"迁移到\s*([A-Za-z]+[0-9]{0,4})", text)
            if cn_match:
                return cn_match.group(1)
            return None

        if _has_any("/paperfit-undo", "undo last change", "回滚最近", "撤销最近", "undo paperfit"):
            return {"task_type": "undo_last_change"}

        if _has_any("/show-status", "show status", "status", "当前状态", "查看状态", "最近结果", "进度"):
            return {"task_type": "status_query"}

        if _has_any(
            "/paperfit-priority",
            "paperfit priority",
            "priority",
            "priorities",
            "what should we fix first",
            "修复优先级",
            "优先修",
            "先修什么",
            "优先级",
        ):
            return {"task_type": "priority_query"}

        if _has_any("/repair-table", "repair table", "fix table", "修表", "修一下表格", "table ") and _extract_table_target():
            result: Dict[str, Any] = {"task_type": "repair_table"}
            target = _extract_table_target()
            if target:
                result["object_target"] = target
            return result

        if _has_any("/migrate-template", "migrate to", "migrate this paper to", "迁移到", "切到模板", "模板迁移"):
            result = {"task_type": "template_migration"}
            template_name = _extract_template_name()
            if template_name:
                result["template"] = template_name
            return result

        if _has_any("/adjust-length", "压到", "缩到", "减到", "页数", "target pages", "fit in", "reduce to", "shorten to") and _extract_target_pages() is not None:
            return {
                "task_type": "adjust_length",
                "target_pages": _extract_target_pages(),
            }

        if _has_any("/check-visual", "check visual", "visual only", "只做视觉", "只做诊断", "不要改代码", "不要改tex", "排版分析", "视觉诊断", "layout analysis", "analyze layout"):
            return {"task_type": "visual_only"}

        if _has_any(
            "/fix-layout",
            "修复排版",
            "修复当前论文",
            "修复当前项目",
            "完整修复",
            "repair this paper",
            "repair this paper layout",
            "repair paper layout",
            "repair the paper layout",
            "repair layout",
            "fix layout",
            "fix this paper layout",
            "improve layout",
            "排到投稿",
            "layout repair",
            "完整闭环",
        ):
            result = {"task_type": "full_vto"}
            target_pages = _extract_target_pages()
            if target_pages is not None:
                result["target_pages"] = target_pages
            return result

        result = {"task_type": "analyze_layout"}
        target_pages = _extract_target_pages()
        if target_pages is not None:
            result["target_pages"] = target_pages
        return result

    def init_task(
        self,
        main_tex: str,
        task_type: str = "full_vto",
        target_pages: Optional[int] = None,
        template: Optional[str] = None,
        strict_mode: bool = False,
        max_rounds: int = 10,
        column_type: Optional[str] = None,
        page_budget_scope: Optional[str] = None,
    ) -> Dict[str, Any]:
        state = self.manager.init_state(
            main_tex=main_tex,
            task_type=task_type,
            target_pages=target_pages,
            template=template,
            strict_mode=strict_mode,
            max_rounds=max_rounds,
            column_type=column_type,
            page_budget_scope=page_budget_scope,
        )
        self.manager.update({"status": "INITIALIZED", "agents_this_round": ["orchestrator-agent"]})
        return self.manager.load()

    def start_round(self) -> Dict[str, Any]:
        state = self.manager.next_round()
        self.manager.update({"agents_this_round": ["orchestrator-agent"]})
        return self.manager.load()

    def mark_compile(self, success: bool, report_path: Optional[str] = None) -> Dict[str, Any]:
        self.manager.load()
        patch: Dict[str, Any] = {
            "compile_success": success,
            "status": "EVALUATING" if success else "BLOCKED",
        }
        if report_path:
            patch["artifacts"] = {"rule_report": report_path}
        self.manager.update(patch)
        return self.manager.load()

    def mark_page_images_rendered(self, rendered: bool, page_dir: Optional[str] = None) -> Dict[str, Any]:
        self.manager.load()
        patch: Dict[str, Any] = {"page_images_rendered": rendered}
        if page_dir:
            patch["artifacts"] = {"page_images_dir": page_dir}
        self.manager.update(patch)
        return self.manager.load()

    def apply_gatekeeper_decision(self, decision: str, report_path: Optional[str] = None) -> Dict[str, Any]:
        self.manager.load()
        normalized = decision.upper()
        patch: Dict[str, Any] = {
            "last_gatekeeper_decision": normalized,
            "status": "DONE" if normalized == "DONE" else ("BLOCKED" if normalized == "BLOCKED" else "EVALUATING"),
        }
        if report_path:
            patch["artifacts"] = {"gatekeeper_decision": report_path}
        self.manager.update(patch)
        return self.manager.load()

    def ingest_column_void_report(self, report_path: str) -> Dict[str, Any]:
        self.manager.load()
        self.manager.ingest_column_void_report(report_path)
        return self.manager.load()

    def ingest_semantic_report(self, report_path: str) -> Dict[str, Any]:
        self.manager.load()
        self.manager.ingest_semantic_report(report_path)
        return self.manager.load()

    def ingest_defect_report(self, report_path: str) -> Dict[str, Any]:
        self.manager.load()
        self.manager.ingest_defect_report(report_path)
        return self.manager.load()

    def set_defect_summary(
        self, resolved: int, remaining: int, initial: Optional[int] = None
    ) -> Dict[str, Any]:
        self.manager.load()
        self.manager.update_defect_summary(resolved=resolved, remaining=remaining, initial=initial)
        return self.manager.load()

    def set_next_actions(self, actions: list[str]) -> Dict[str, Any]:
        self.manager.load()
        self.manager.update({"next_actions": actions})
        return self.manager.load()

    def set_agents_this_round(self, agents: list[str]) -> Dict[str, Any]:
        self.manager.load()
        self.manager.update({"agents_this_round": agents})
        return self.manager.load()

    def set_artifact(self, key: str, value: str) -> Dict[str, Any]:
        self.manager.load()
        artifacts = dict(self.manager.get("artifacts") or {})
        artifacts[key] = value
        self.manager.update({"artifacts": artifacts})
        return self.manager.load()

    def execute_repair_plan(
        self,
        main_tex: str,
        repair_plan_path: Optional[str] = None,
        output_path: Optional[str] = None,
        column_type: Optional[str] = None,
        max_candidates: int = 3,
    ) -> Dict[str, Any]:
        self.manager.load()
        repair_plan_path = repair_plan_path or (((self.manager.get("artifacts") or {}).get("repair_plan")) or "data/repair_plan.json")
        output_path = output_path or "data/repair_execution_report.json"
        task_config = self.manager.get("task") or {}
        freshness = self._validate_repair_plan_before_execution(
            repair_plan_path=repair_plan_path,
            main_tex=main_tex,
        )
        if not freshness.get("fresh"):
            report = blocked_stale_repair_plan_report(
                repair_plan_path=repair_plan_path,
                main_tex=main_tex,
                freshness=freshness,
            )
            out_path = Path(output_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            self.manager.ingest_repair_execution_report(output_path)
            self.set_next_actions(["Regenerate repair plan before applying source mutations"])
            return self.manager.load()
        report = self._run_repair_plan_executor(
            repair_plan_path=repair_plan_path,
            main_tex=main_tex,
            output_path=output_path,
            column_type=column_type or task_config.get("column_type"),
            target_pages=task_config.get("target_pages"),
            max_candidates=max_candidates,
        )
        if report:
            self.manager.ingest_repair_execution_report(output_path)
        return self.manager.load()

    def _validate_repair_plan_before_execution(
        self,
        *,
        repair_plan_path: str,
        main_tex: str,
    ) -> Dict[str, Any]:
        plan_path = Path(repair_plan_path)
        if not plan_path.is_absolute():
            plan_path = Path.cwd() / plan_path
        if not plan_path.is_file():
            return {
                "schema_version": "1.0",
                "status": "missing_plan",
                "fresh": False,
                "changed_files": [],
            }
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        return validate_repair_plan_freshness(
            project_root=Path.cwd(),
            main_tex=main_tex,
            repair_plan=plan,
        )

    def rollback_to_snapshot(
        self,
        rollback_target: str,
        output_path: str = "data/rollback_report.json",
    ) -> Dict[str, Any]:
        self.manager.load()
        project_root = (
            self.manager.state_path.parent.parent
            if self.manager.state_path.is_absolute()
            else Path.cwd()
        )
        report = restore_snapshot(
            project_root=project_root,
            rollback_target=rollback_target,
            output_path=output_path,
        )
        self.manager.update(
            {
                "artifacts": {"rollback_report": output_path},
                "content_integrity": {
                    "validation_status": "rolled_back",
                    "action_taken": "restore_snapshot",
                    "rollback_target": rollback_target,
                },
            }
        )
        state = self.manager.load()
        state["rollback_report"] = report
        return state

    def status_view(self, run_result_path: Optional[str] = None) -> Dict[str, Any]:
        project_root = (
            self.manager.state_path.parent.parent
            if self.manager.state_path.is_absolute()
            else Path.cwd()
        )
        return build_runtime_status(
            project_root=project_root,
            state_path=self.manager.state_path,
            run_result_path=run_result_path,
        )

    def run_task(self, task_spec: TaskSpec, output_path: Optional[str] = None) -> Dict[str, Any]:
        task_spec.validate()
        if task_spec.task_type != "visual_only":
            if task_spec.task_type in SOURCE_CHANGING_TASK_TYPES:
                return self._run_source_changing_task(task_spec=task_spec, output_path=output_path)
            raise ValueError("run-task currently supports visual_only and source-changing VTO tasks")
        if task_spec.allow_source_mutation:
            raise ValueError("visual_only tasks cannot mutate source")

        run_id = task_spec.task_id or datetime.now().strftime("pf_%Y%m%d_%H%M%S")
        project_root = Path(task_spec.project_root).resolve()
        event_log = str(Path("data") / "events" / f"{run_id}.ndjson")
        output_path = output_path or str(Path("data") / "run_result.json")

        cwd_before = Path.cwd()
        if not project_root.is_dir():
            raise FileNotFoundError(f"project_root not found: {project_root}")
        # Use project-relative state/artifact paths for the runtime contract.
        if not self.manager.state_path.is_absolute():
            self.manager.state_path = Path("data/state.json")
            self.manager.backup_dir = self.manager.state_path.parent / "backups"
            self.manager.archive_dir = self.manager.state_path.parent / "archives"
            self.manager.case_dir = self.manager.state_path.parent / "benchmarks" / "case"

        try:
            os.chdir(project_root)
            writer = RuntimeEventWriter(run_id=run_id, event_log=event_log)
            event_count = 0

            def emit_runtime_event(event_type: str, **kwargs: Any) -> Dict[str, Any]:
                nonlocal event_count
                event = writer.emit(event_type, **kwargs)
                event_count += 1
                self._project_runtime_event(
                    event=event,
                    event_log=event_log,
                    event_count=event_count,
                )
                return event

            runtime_state = "INIT"
            emit_runtime_event(
                "task_started",
                state=runtime_state,
                message="PaperFit runtime task started",
                payload={"task": task_spec.to_dict()},
            )

            runtime_state = VISUAL_ONLY_STATE_MACHINE.transition(runtime_state, "task_validated")
            emit_runtime_event("phase_completed", phase="task_validation", state=runtime_state)

            task_spec_path = Path("data") / "task.json"
            task_spec_path.parent.mkdir(parents=True, exist_ok=True)
            task_spec_path.write_text(
                json.dumps(task_spec.to_dict(), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            self.init_task(
                main_tex=task_spec.main_tex,
                task_type=task_spec.task_type,
                target_pages=task_spec.target_pages,
                template=task_spec.template,
                strict_mode=task_spec.strict_mode,
                max_rounds=task_spec.max_rounds,
                page_budget_scope=task_spec.page_budget_scope,
            )
            self.set_artifact("task_spec", str(task_spec_path))

            runtime_state = VISUAL_ONLY_STATE_MACHINE.transition(runtime_state, "start_observe")
            emit_runtime_event(
                "phase_started",
                phase="observe",
                state=runtime_state,
                message="Observing existing compile, render, and source artifacts",
            )

            runtime_actions = self._run_visual_only_observe_actions(task_spec=task_spec, emit_event=emit_runtime_event)
            compile_result = runtime_actions.get("compile") or {}
            if compile_result.get("timeout") and not compile_result.get("success"):
                state = self._mark_runtime_compile_blocked(
                    task_spec=task_spec,
                    compile_result=compile_result,
                )
            else:
                state = self._run_round_core(
                    main_tex=task_spec.main_tex,
                    log_file=task_spec.log_file,
                    page_dir=task_spec.page_dir,
                    template=task_spec.template,
                    target_pages=task_spec.target_pages,
                    column_void_report=task_spec.column_void_report,
                    emit_event=emit_runtime_event,
                    runtime_actions=runtime_actions,
                )

            runtime_state = VISUAL_ONLY_STATE_MACHINE.transition(runtime_state, "artifacts_observed")
            emit_runtime_event(
                "phase_completed",
                phase="observe",
                state=runtime_state,
                payload={"artifacts": state.get("artifacts") or {}},
            )

            runtime_state = VISUAL_ONLY_STATE_MACHINE.transition(runtime_state, "diagnosis_complete")
            emit_runtime_event(
                "phase_completed",
                phase="diagnose",
                state=runtime_state,
                payload={"defect_summary": state.get("defect_summary") or {}},
            )

            decision = str(state.get("last_gatekeeper_decision") or "CONTINUE").upper()
            artifact_manifest = collect_artifact_manifest(
                project_root=Path.cwd(),
                main_tex=task_spec.main_tex,
                artifacts=state.get("artifacts") or {},
            )
            terminal_guard_failure = self._terminal_visual_evidence_failure(
                decision=decision,
                artifact_manifest=artifact_manifest,
            )
            event = {
                "DONE": "gatekeeper_done",
                "BLOCKED": "gatekeeper_blocked",
            }.get(decision, "gatekeeper_continue")
            if terminal_guard_failure is not None:
                event = "gatekeeper_blocked"
            runtime_state = VISUAL_ONLY_STATE_MACHINE.transition(runtime_state, event)
            if terminal_guard_failure is not None:
                state = self._record_terminal_visual_guard_failure(terminal_guard_failure)
            emit_runtime_event(
                "gatekeeper_result",
                phase="verify",
                state=runtime_state,
                payload={
                    "decision": decision,
                    "defect_summary": state.get("defect_summary") or {},
                    "terminal_success_guard": terminal_guard_failure,
                },
            )
            emit_runtime_event(
                "artifact_manifest",
                phase="verify",
                state=runtime_state,
                payload=artifact_manifest,
            )

            failure = None
            if terminal_guard_failure is not None:
                failure = terminal_guard_failure
            elif runtime_state != "DONE":
                failure_tracking = state.get("failure_tracking") or {}
                failure = {
                    "failure_type": failure_tracking.get("last_failure_type") or "gatekeeper_continue",
                    "reason": decision,
                    "next_actions": state.get("next_actions") or [],
                }

            result = RunResult(
                run_id=run_id,
                task=task_spec,
                status=runtime_state.lower(),
                gatekeeper_decision=decision,
                state_path=str(self.manager.state_path),
                event_log=event_log,
                artifacts=state.get("artifacts") or {},
                defect_summary=state.get("defect_summary") or {},
                runtime_actions=runtime_actions,
                artifact_manifest=artifact_manifest,
                approval=build_approval_object(
                    task=task_spec.to_dict(),
                    state=state,
                    runtime_actions=runtime_actions,
                ),
                failure=failure,
            )
            result_payload = result.to_dict()
            terminal_event = "task_blocked" if runtime_state == "BLOCKED" else "task_completed"
            emit_runtime_event(terminal_event, state=runtime_state, payload=result_payload)
            out_path = Path(output_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(result_payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            return result_payload
        finally:
            os.chdir(cwd_before)

    def _run_source_changing_task(self, task_spec: TaskSpec, output_path: Optional[str] = None) -> Dict[str, Any]:
        if not task_spec.allow_source_mutation:
            raise ValueError(f"{task_spec.task_type} tasks require allow_source_mutation=true")

        run_id = task_spec.task_id or datetime.now().strftime("pf_%Y%m%d_%H%M%S")
        project_root = Path(task_spec.project_root).resolve()
        event_log = str(Path("data") / "events" / f"{run_id}.ndjson")
        output_path = output_path or str(Path("data") / "run_result.json")

        cwd_before = Path.cwd()
        if not project_root.is_dir():
            raise FileNotFoundError(f"project_root not found: {project_root}")
        if not self.manager.state_path.is_absolute():
            self.manager.state_path = Path("data/state.json")
            self.manager.backup_dir = self.manager.state_path.parent / "backups"
            self.manager.archive_dir = self.manager.state_path.parent / "archives"
            self.manager.case_dir = self.manager.state_path.parent / "benchmarks" / "case"

        try:
            os.chdir(project_root)
            writer = RuntimeEventWriter(run_id=run_id, event_log=event_log)
            event_count = 0

            def emit_runtime_event(event_type: str, **kwargs: Any) -> Dict[str, Any]:
                nonlocal event_count
                event = writer.emit(event_type, **kwargs)
                event_count += 1
                self._project_runtime_event(
                    event=event,
                    event_log=event_log,
                    event_count=event_count,
                )
                return event

            runtime_state = "INIT"
            emit_runtime_event(
                "task_started",
                state=runtime_state,
                message="PaperFit source-changing runtime task started",
                payload={"task": task_spec.to_dict()},
            )

            runtime_state = SOURCE_CHANGING_STATE_MACHINE.transition(runtime_state, "task_validated")
            emit_runtime_event("phase_completed", phase="task_validation", state=runtime_state)

            task_spec_path = Path("data") / "task.json"
            task_spec_path.parent.mkdir(parents=True, exist_ok=True)
            task_spec_path.write_text(
                json.dumps(task_spec.to_dict(), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            self.init_task(
                main_tex=task_spec.main_tex,
                task_type=task_spec.task_type,
                target_pages=task_spec.target_pages,
                template=task_spec.template,
                strict_mode=task_spec.strict_mode,
                max_rounds=task_spec.max_rounds,
                page_budget_scope=task_spec.page_budget_scope,
            )
            self.set_artifact("task_spec", str(task_spec_path))

            runtime_actions: Dict[str, Any] = {}
            snapshot = create_pre_repair_snapshot(
                project_root=project_root,
                main_tex=task_spec.main_tex,
            )
            self.manager.update(
                {
                    "pre_repair_snapshot": snapshot,
                    "content_integrity": {
                        "validation_status": "snapshot_created",
                        "rollback_target": snapshot.get("rollback_target"),
                    },
                }
            )
            self._record_runtime_action(
                "pre_repair_snapshot",
                {
                    "success": True,
                    "snapshot_id": snapshot.get("snapshot_id"),
                    "input_artifacts": {
                        "main_tex": task_spec.main_tex,
                    },
                    "rollback_target": snapshot.get("rollback_target"),
                    "output_artifacts": {
                        "rollback_target": snapshot.get("rollback_target"),
                    },
                    "files_count": len(snapshot.get("files") or []),
                },
                phase="prepare",
                state="READY",
                emit_event=emit_runtime_event,
                runtime_actions=runtime_actions,
            )

            runtime_state = SOURCE_CHANGING_STATE_MACHINE.transition(runtime_state, "start_observe")
            emit_runtime_event(
                "phase_started",
                phase="observe",
                state=runtime_state,
                message="Observing existing compile, render, and source artifacts",
            )
            initial_observe = self._run_visual_only_observe_actions(
                task_spec=task_spec,
                emit_event=emit_runtime_event,
            )
            runtime_actions["initial_observe"] = initial_observe
            compile_result = initial_observe.get("compile") or {}
            if compile_result.get("timeout") and not compile_result.get("success"):
                runtime_state = SOURCE_CHANGING_STATE_MACHINE.transition(runtime_state, "compile_blocked")
                state = self._mark_runtime_compile_blocked(
                    task_spec=task_spec,
                    compile_result=compile_result,
                )
            else:
                state = self._run_round_core(
                    main_tex=task_spec.main_tex,
                    log_file=task_spec.log_file,
                    page_dir=task_spec.page_dir,
                    template=task_spec.template,
                    target_pages=task_spec.target_pages,
                    column_void_report=task_spec.column_void_report,
                    emit_event=emit_runtime_event,
                    runtime_actions=runtime_actions,
                )

                runtime_state = SOURCE_CHANGING_STATE_MACHINE.transition(runtime_state, "artifacts_observed")
                emit_runtime_event(
                    "phase_completed",
                    phase="observe",
                    state=runtime_state,
                    payload={"artifacts": state.get("artifacts") or {}},
                )
                runtime_state = SOURCE_CHANGING_STATE_MACHINE.transition(runtime_state, "diagnosis_complete")
                emit_runtime_event(
                    "phase_completed",
                    phase="diagnose",
                    state=runtime_state,
                    payload={
                        "defect_summary": state.get("defect_summary") or {},
                        "repair_plan_summary": state.get("repair_plan_summary") or {},
                    },
                )
                repair_plan_summary = state.get("repair_plan_summary") or {}
                if int(repair_plan_summary.get("total_candidates") or 0) > 0:
                    runtime_state = SOURCE_CHANGING_STATE_MACHINE.transition(runtime_state, "plan_ready")
                    emit_runtime_event(
                        "phase_started",
                        phase="repair",
                        state=runtime_state,
                        payload={"repair_plan_summary": repair_plan_summary},
                    )
                    if task_spec.dry_run_source_mutation:
                        self._record_runtime_action(
                            "repair_plan_executor",
                            {
                                "success": True,
                                "skipped": True,
                                "reason": "dry_run_source_mutation",
                                "input_artifacts": {
                                    "repair_plan": (state.get("artifacts") or {}).get("repair_plan"),
                                    "main_tex": task_spec.main_tex,
                                    "rollback_target": snapshot.get("rollback_target"),
                                },
                                "output_artifacts": {},
                                "planned_candidates": int(repair_plan_summary.get("total_candidates") or 0),
                            },
                            phase="repair",
                            state="REPAIRING",
                            emit_event=emit_runtime_event,
                            runtime_actions=runtime_actions,
                        )
                        runtime_state = SOURCE_CHANGING_STATE_MACHINE.transition(runtime_state, "repair_skipped")
                        emit_runtime_event(
                            "phase_completed",
                            phase="repair",
                            state=runtime_state,
                            payload={"reason": "dry_run_source_mutation"},
                        )
                    else:
                        repair_state = self.execute_repair_plan(
                            main_tex=task_spec.main_tex,
                            output_path="data/repair_execution_report.json",
                            max_candidates=1,
                        )
                        repair_summary = repair_state.get("repair_execution_summary") or {}
                        self._record_runtime_action(
                            "repair_plan_executor",
                            {
                                "success": True,
                                "input_artifacts": {
                                    "repair_plan": (state.get("artifacts") or {}).get("repair_plan"),
                                    "main_tex": task_spec.main_tex,
                                    "rollback_target": snapshot.get("rollback_target"),
                                },
                                "output_path": "data/repair_execution_report.json",
                                "output_artifacts": {
                                    "repair_execution_report": "data/repair_execution_report.json",
                                },
                                "applied_count": int(repair_summary.get("applied_count") or 0),
                                "status": repair_summary.get("status"),
                            },
                            phase="repair",
                            state="REPAIRING",
                            emit_event=emit_runtime_event,
                            runtime_actions=runtime_actions,
                        )
                        runtime_state = SOURCE_CHANGING_STATE_MACHINE.transition(runtime_state, "repair_applied")
                        emit_runtime_event(
                            "phase_completed",
                            phase="repair",
                            state=runtime_state,
                            payload=repair_summary,
                        )
                        mutation_report = build_source_mutation_report(
                            project_root=project_root,
                            rollback_target=str(snapshot.get("rollback_target")),
                            output_path="data/source_mutation_report.json",
                        )
                        self.manager.update(
                            {
                                "artifacts": {"source_mutation_report": "data/source_mutation_report.json"},
                                "content_integrity": {
                                    "validation_status": "mutation_reported",
                                    "rollback_target": snapshot.get("rollback_target"),
                                },
                            }
                        )
                        self._record_runtime_action(
                            "source_mutation_integrity",
                            {
                                "success": True,
                                "input_artifacts": {
                                    "rollback_target": snapshot.get("rollback_target"),
                                    "main_tex": task_spec.main_tex,
                                },
                                "output_path": "data/source_mutation_report.json",
                                "output_artifacts": {
                                    "source_mutation_report": "data/source_mutation_report.json",
                                },
                                "changed_files": int((mutation_report.get("summary") or {}).get("changed_files") or 0),
                                "missing_files": int((mutation_report.get("summary") or {}).get("missing_files") or 0),
                            },
                            phase="verify",
                            state="VERIFYING",
                            emit_event=emit_runtime_event,
                            runtime_actions=runtime_actions,
                        )
                        runtime_state = SOURCE_CHANGING_STATE_MACHINE.transition(runtime_state, "start_post_observe")
                        emit_runtime_event(
                            "phase_started",
                            phase="observe",
                            state=runtime_state,
                            message="Observing post-repair compile and render artifacts",
                        )
                        post_observe = self._run_visual_only_observe_actions(
                            task_spec=task_spec,
                            emit_event=emit_runtime_event,
                        )
                        runtime_actions["post_repair_observe"] = post_observe
                        state = self._run_round_core(
                            main_tex=task_spec.main_tex,
                            log_file=task_spec.log_file,
                            page_dir=task_spec.page_dir,
                            template=task_spec.template,
                            target_pages=task_spec.target_pages,
                            column_void_report=task_spec.column_void_report,
                            emit_event=emit_runtime_event,
                            runtime_actions=runtime_actions,
                        )
                        runtime_state = SOURCE_CHANGING_STATE_MACHINE.transition(runtime_state, "artifacts_observed")
                        emit_runtime_event(
                            "phase_completed",
                            phase="observe",
                            state=runtime_state,
                            payload={"artifacts": state.get("artifacts") or {}},
                        )
                        runtime_state = SOURCE_CHANGING_STATE_MACHINE.transition(
                            runtime_state,
                            "post_repair_diagnosis_complete",
                        )
                        emit_runtime_event(
                            "phase_completed",
                            phase="diagnose",
                            state=runtime_state,
                            payload={
                                "defect_summary": state.get("defect_summary") or {},
                                "repair_plan_summary": state.get("repair_plan_summary") or {},
                            },
                        )
                else:
                    runtime_state = SOURCE_CHANGING_STATE_MACHINE.transition(runtime_state, "no_repair_candidates")
                    self._record_runtime_action(
                        "repair_plan_executor",
                        {
                            "success": False,
                            "skipped": True,
                            "reason": "no_repair_candidates",
                            "input_artifacts": {
                                "repair_plan": (state.get("artifacts") or {}).get("repair_plan"),
                                "main_tex": task_spec.main_tex,
                                "rollback_target": snapshot.get("rollback_target"),
                            },
                            "output_artifacts": {},
                        },
                        phase="repair",
                        state=runtime_state,
                        emit_event=emit_runtime_event,
                        runtime_actions=runtime_actions,
                    )
                    emit_runtime_event(
                        "phase_completed",
                        phase="repair",
                        state=runtime_state,
                        payload={"reason": "no_repair_candidates"},
                    )

            decision = str(state.get("last_gatekeeper_decision") or "CONTINUE").upper()
            artifact_manifest = collect_artifact_manifest(
                project_root=Path.cwd(),
                main_tex=task_spec.main_tex,
                artifacts=state.get("artifacts") or {},
            )
            terminal_guard_failure = self._terminal_visual_evidence_failure(
                decision=decision,
                artifact_manifest=artifact_manifest,
            )
            if runtime_state == "VERIFYING":
                event = {
                    "DONE": "gatekeeper_done",
                    "BLOCKED": "gatekeeper_blocked",
                }.get(decision, "gatekeeper_continue")
                if terminal_guard_failure is not None:
                    event = "gatekeeper_blocked"
                runtime_state = SOURCE_CHANGING_STATE_MACHINE.transition(runtime_state, event)
                if terminal_guard_failure is not None:
                    state = self._record_terminal_visual_guard_failure(terminal_guard_failure)
                emit_runtime_event(
                    "gatekeeper_result",
                    phase="verify",
                    state=runtime_state,
                    payload={
                        "decision": decision,
                        "defect_summary": state.get("defect_summary") or {},
                        "terminal_success_guard": terminal_guard_failure,
                    },
                )
            status = "done" if runtime_state == "DONE" else ("blocked" if runtime_state == "BLOCKED" else "continue")
            emit_runtime_event(
                "artifact_manifest",
                phase="verify",
                state=runtime_state,
                payload=artifact_manifest,
            )
            failure = None
            if terminal_guard_failure is not None:
                failure = terminal_guard_failure
            elif decision != "DONE":
                failure_tracking = state.get("failure_tracking") or {}
                failure = {
                    "failure_type": failure_tracking.get("last_failure_type") or "gatekeeper_continue",
                    "reason": decision,
                    "next_actions": state.get("next_actions") or [],
                }

            approval = build_approval_object(
                task=task_spec.to_dict(),
                state=state,
                runtime_actions=runtime_actions,
            )
            round_artifact_lineage = build_round_artifact_lineage(
                state=state,
                runtime_actions=runtime_actions,
            )
            repair_loop_policy = build_repair_loop_policy(
                task=task_spec.to_dict(),
                state=state,
                runtime_actions=runtime_actions,
                artifact_manifest=artifact_manifest,
                approval=approval,
                status=status,
                gatekeeper_decision=decision,
                round_artifact_lineage=round_artifact_lineage,
            )

            result = RunResult(
                run_id=run_id,
                task=task_spec,
                status=status,
                gatekeeper_decision=decision,
                state_path=str(self.manager.state_path),
                event_log=event_log,
                artifacts=state.get("artifacts") or {},
                defect_summary=state.get("defect_summary") or {},
                runtime_actions=runtime_actions,
                artifact_manifest=artifact_manifest,
                approval=approval,
                repair_loop_policy=repair_loop_policy,
                round_artifact_lineage=round_artifact_lineage,
                failure=failure,
            )
            result_payload = result.to_dict()
            terminal_event = "task_completed" if status != "blocked" else "task_blocked"
            emit_runtime_event(terminal_event, state=runtime_state, payload=result_payload)
            out_path = Path(output_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(result_payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            return result_payload
        finally:
            os.chdir(cwd_before)

    def _terminal_visual_evidence_failure(
        self,
        *,
        decision: str,
        artifact_manifest: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if str(decision or "").upper() != "DONE":
            return None
        freshness = (artifact_manifest.get("freshness") or {}) if isinstance(artifact_manifest, dict) else {}
        if freshness.get("status") == "pass":
            return None
        return {
            "failure_type": "terminal_success_without_fresh_visual_evidence",
            "reason": "gatekeeper_done_but_artifact_freshness_failed",
            "gatekeeper_decision": str(decision or "").upper(),
            "artifact_freshness": {
                "status": freshness.get("status") or "unknown",
                "blocking_checks": freshness.get("blocking_checks") or [],
            },
            "next_actions": [
                "Re-run compile/render/diagnose/gatekeeper before reporting DONE",
                "Inspect artifact freshness blocking checks",
            ],
        }

    def _record_terminal_visual_guard_failure(self, failure: Dict[str, Any]) -> Dict[str, Any]:
        self.manager.update_failure_tracking(
            decision="BLOCKED",
            failure_type=str(failure.get("failure_type") or "terminal_success_without_fresh_visual_evidence"),
        )
        self.manager.update(
            {
                "status": "BLOCKED",
                "terminal_success_guard": {
                    "status": "blocked",
                    "failure_type": failure.get("failure_type"),
                    "reason": failure.get("reason"),
                    "artifact_freshness": failure.get("artifact_freshness"),
                },
            }
        )
        return self.manager.load()

    def _run_visual_only_observe_actions(
        self,
        *,
        task_spec: TaskSpec,
        emit_event: Callable[..., Dict[str, Any]],
    ) -> Dict[str, Any]:
        project_root = Path.cwd()
        main_tex = project_root / task_spec.main_tex
        runtime_actions: Dict[str, Any] = {}
        compile_result = compile_latex(project_root, main_tex=main_tex)
        self._record_runtime_action(
            "compile",
            compile_result,
            phase="observe",
            state="OBSERVING",
            emit_event=emit_event,
            runtime_actions=runtime_actions,
        )

        render_result: Dict[str, Any] = {"success": False, "page_dir": task_spec.page_dir}
        visual_hard_guards: Dict[str, Any] = {"available": False, "hard_failures": []}
        pdf_path = compile_result.get("pdf_path")
        if compile_result.get("success") and pdf_path:
            render_result = render_pdf_pages(
                project_root,
                pdf_path=Path(str(pdf_path)),
                output_dir=task_spec.page_dir,
            )
            self._record_runtime_action(
                "render",
                render_result,
                phase="observe",
                state="OBSERVING",
                emit_event=emit_event,
                runtime_actions=runtime_actions,
                event_action="render_pages",
            )
            visual_hard_guards = inspect_endmatter_float_intrusion(Path(str(pdf_path)))
            self._record_runtime_action(
                "visual_hard_guards",
                visual_hard_guards,
                phase="observe",
                state="OBSERVING",
                emit_event=emit_event,
                runtime_actions=runtime_actions,
            )
        else:
            render_result = {
                "success": False,
                "skipped": True,
                "reason": "compile_failed",
                "page_dir": task_spec.page_dir,
            }
            self._record_runtime_action(
                "render",
                render_result,
                phase="observe",
                state="OBSERVING",
                emit_event=emit_event,
                runtime_actions=runtime_actions,
                event_action="render_pages",
                event_type="runtime_action_skipped",
            )

        return {
            "compile": runtime_actions.get("compile", compile_result),
            "render": runtime_actions.get("render", render_result),
            "visual_hard_guards": runtime_actions.get("visual_hard_guards", visual_hard_guards),
        }

    def _project_runtime_event(
        self,
        *,
        event: Dict[str, Any],
        event_log: str,
        event_count: int,
    ) -> None:
        try:
            current = self.manager.load()
        except FileNotFoundError:
            return

        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        actions = dict(((current.get("runtime_event_summary") or {}).get("actions")) or {})
        action_name = payload.get("action") if isinstance(payload, dict) else None
        if isinstance(action_name, str) and action_name:
            action_entry: Dict[str, Any] = {
                "last_event_type": event.get("type"),
                "phase": event.get("phase"),
                "runtime_state": event.get("state"),
                "updated_at": event.get("timestamp"),
            }
            result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            if result:
                if "success" in result:
                    action_entry["success"] = bool(result.get("success"))
                if "skipped" in result:
                    action_entry["skipped"] = bool(result.get("skipped"))
                if "returncode" in result:
                    action_entry["returncode"] = result.get("returncode")
                if "timeout" in result:
                    action_entry["timeout"] = bool(result.get("timeout"))
                for key in ("failure_type", "risk_level", "requires_approval", "input_artifacts", "output_artifacts"):
                    if result.get(key) is not None:
                        action_entry[key] = result.get(key)
            if payload.get("reason") is not None:
                action_entry["reason"] = payload.get("reason")
            elif result.get("reason") is not None:
                action_entry["reason"] = result.get("reason")
            actions[action_name] = action_entry

        self.manager.update(
            {
                "runtime_event_summary": {
                    "schema_version": "1.0",
                    "run_id": event.get("run_id"),
                    "event_log": event_log,
                    "event_count": event_count,
                    "last_event_type": event.get("type"),
                    "last_phase": event.get("phase"),
                    "last_runtime_state": event.get("state"),
                    "last_message": event.get("message"),
                    "last_event_at": event.get("timestamp"),
                    "last_action": action_name,
                    "actions": actions,
                }
            }
        )

    def _record_runtime_action(
        self,
        action: str,
        result: Dict[str, Any],
        *,
        phase: str,
        state: str,
        emit_event: Optional[Callable[..., Dict[str, Any]]] = None,
        runtime_actions: Optional[Dict[str, Any]] = None,
        event_action: Optional[str] = None,
        event_type: Optional[str] = None,
    ) -> None:
        event_action_name = event_action or action
        action_result = ActionResult.from_result(
            action_name=event_action_name,
            phase=phase,
            runtime_state=state,
            result=result,
        ).to_dict()
        if runtime_actions is not None:
            runtime_actions[action] = action_result
        if emit_event is not None:
            if event_type is None:
                event_type = "runtime_action_completed" if action_result.get("success") else "runtime_action_failed"
            emit_event(
                event_type,
                phase=phase,
                state=state,
                payload={"action": event_action_name, "result": action_result},
            )

    def _mark_runtime_compile_blocked(
        self,
        *,
        task_spec: TaskSpec,
        compile_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        timeout_sec = int(compile_result.get("timeout_sec") or 0)
        self.manager.update(
            {
                "compile_success": False,
                "page_images_rendered": False,
                "status": "BLOCKED",
                "last_gatekeeper_decision": "BLOCKED",
                "agents_this_round": ["orchestrator-agent", "rule-engine-agent"],
                "next_actions": [
                    f"Compilation exceeded {timeout_sec}s; inspect TeX macro recursion or oversized assets",
                ],
            }
        )
        self.manager.update_defect_summary(resolved=0, remaining=1, initial=1)
        self.manager.update_failure_tracking(
            decision="BLOCKED",
            failure_type="compile_timeout",
        )
        state = self.add_history_entry(
            decision="BLOCKED",
            defects_found=1,
            defects_resolved=0,
            note=f"runtime-compile-timeout:{Path(task_spec.main_tex).name}",
        )
        return self.manager.load() if not state else state

    def add_history_entry(
        self,
        decision: Optional[str] = None,
        defects_found: Optional[int] = None,
        defects_resolved: Optional[int] = None,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.manager.load()
        entry: Dict[str, Any] = {"round": self.manager.get("current_round")}
        if decision is not None:
            entry["decision"] = decision
        if defects_found is not None:
            entry["defects_found"] = defects_found
        if defects_resolved is not None:
            entry["defects_resolved"] = defects_resolved
        if note:
            entry["note"] = note
        self.manager.add_history_entry(entry)
        return self.manager.load()

    def archive_task(self) -> Dict[str, Any]:
        self.manager.load()
        archive_path = self.manager.archive()
        self.manager.update({"status": "ARCHIVED"})
        state = self.manager.load()
        state["archive_path"] = archive_path
        return state

    def inspect_project(
        self,
        main_tex: str,
        log_file: Optional[str] = None,
        page_dir: Optional[str] = None,
        template: Optional[str] = None,
        target_pages: Optional[int] = None,
        crossrefs_output: Optional[str] = None,
        rule_report_output: Optional[str] = None,
        visual_signal_output: Optional[str] = None,
        defect_report_output: Optional[str] = None,
        initialize_task: bool = True,
        record_history: bool = True,
        emit_event: Optional[Callable[..., Dict[str, Any]]] = None,
        runtime_actions: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if initialize_task:
            self.init_task(
                main_tex=main_tex,
                template=template,
                target_pages=target_pages,
            )
        else:
            self.manager.load()

        cwd = Path.cwd()
        main_tex_path = cwd / main_tex
        if log_file is None:
            log_file = str(main_tex_path.with_suffix(".log").name)
        if page_dir is None:
            page_dir = "page_images" if (cwd / "page_images").is_dir() else "data/pages"
        if crossrefs_output is None:
            crossrefs_output = "data/crossrefs.json"
        if rule_report_output is None:
            rule_report_output = "data/rule_report.json"
        if visual_signal_output is None:
            visual_signal_output = "data/visual_signal_report.json"
        if defect_report_output is None:
            defect_report_output = "data/defect_report.json"
        hygiene_report_output = "data/source_hygiene_report.json"
        repair_plan_output = "data/repair_plan.json"

        rule_report = self._parse_log_to_report(log_file=log_file, output_path=rule_report_output)
        self.mark_compile(success=bool(rule_report.get("compile_success")), report_path=rule_report_output)

        page_dir_path = cwd / page_dir
        rendered = page_dir_path.is_dir() and any(page_dir_path.glob("page_*.png"))
        self.mark_page_images_rendered(rendered=rendered, page_dir=page_dir)
        self.manager.update(
            {
                "artifacts": {
                    "visual_signal_report": None,
                    "repair_plan": None,
                    "defect_report": None,
                }
            }
        )

        crossrefs_report = self._extract_crossrefs(main_tex=main_tex, output_path=crossrefs_output)
        self.set_artifact("crossrefs_report", crossrefs_output)

        hygiene_report = self._run_source_hygiene_check(
            main_tex=main_tex,
            output_path=hygiene_report_output,
        )
        self.set_artifact("source_hygiene_report", hygiene_report_output)

        if rendered:
            visual_report = self._run_visual_signal_aggregator(
                pages_dir=page_dir,
                output_path=visual_signal_output,
                rule_report_output=rule_report_output,
                crossrefs_output=crossrefs_output,
            )
            visual_action_result = {
                "success": bool(visual_report),
                "input_artifacts": {
                    "page_dir": page_dir,
                    "rule_report": rule_report_output,
                    "crossrefs_report": crossrefs_output,
                    "column_void_report": (self.manager.load().get("artifacts") or {}).get("column_void_report"),
                },
                "output_path": visual_signal_output,
                "output_artifacts": {
                    "visual_signal_report": visual_signal_output,
                },
                "findings_count": len((visual_report or {}).get("findings") or []),
                "priority_pages": ((visual_report or {}).get("routing_hints") or {}).get("priority_pages") or [],
            }
            self._record_runtime_action(
                "visual_signal_aggregator",
                visual_action_result,
                phase="diagnose",
                state="DIAGNOSING",
                emit_event=emit_event,
                runtime_actions=runtime_actions,
            )
            if visual_report:
                self.manager.ingest_visual_signal_report(visual_signal_output)
                repair_plan = self._run_repair_plan_generator(
                    visual_signal_report=visual_signal_output,
                    output_path=repair_plan_output,
                    crossrefs_output=crossrefs_output,
                    rule_report_output=rule_report_output,
                    target_pages=target_pages,
                )
                self._record_runtime_action(
                    "repair_plan_generator",
                    {
                        "success": bool(repair_plan),
                        "input_artifacts": {
                            "visual_signal_report": visual_signal_output,
                            "crossrefs_report": crossrefs_output,
                            "rule_report": rule_report_output,
                        },
                        "output_path": repair_plan_output,
                        "output_artifacts": {
                            "repair_plan": repair_plan_output,
                        },
                        "candidates_count": len((repair_plan or {}).get("candidates") or []),
                    },
                    phase="plan",
                    state="DIAGNOSING",
                    emit_event=emit_event,
                    runtime_actions=runtime_actions,
                )
                if repair_plan:
                    repair_plan = attach_repair_plan_fingerprint(
                        project_root=Path.cwd(),
                        main_tex=main_tex,
                        repair_plan_path=repair_plan_output,
                    )
                    self.manager.ingest_repair_plan(repair_plan_output)

        defect_report = self._run_defect_report_builder(
            rule_report_output=rule_report_output,
            visual_signal_output=visual_signal_output,
            hygiene_report_output=hygiene_report_output,
            output_path=defect_report_output,
        )
        self._record_runtime_action(
            "defect_report_builder",
            {
                "success": bool(defect_report),
                "input_artifacts": {
                    "rule_report": rule_report_output,
                    "visual_signal_report": visual_signal_output,
                    "source_hygiene_report": hygiene_report_output,
                },
                "output_path": defect_report_output,
                "output_artifacts": {
                    "defect_report": defect_report_output,
                },
                "defects_count": len((defect_report or {}).get("defects") or []),
            },
            phase="diagnose",
            state="DIAGNOSING",
            emit_event=emit_event,
            runtime_actions=runtime_actions,
        )
        if defect_report:
            self.manager.ingest_defect_report(defect_report_output)

        agents = ["orchestrator-agent", "rule-engine-agent"]
        if rendered:
            agents.append("layout-detective-agent")
        self.set_agents_this_round(agents)
        defect_summary = (self.manager.load().get("defect_summary") or {})
        if record_history:
            self.add_history_entry(
                decision="CONTINUE" if rule_report.get("compile_success") else "BLOCKED",
                defects_found=int(defect_summary.get("remaining") or 0),
                defects_resolved=int(defect_summary.get("resolved") or 0),
                note=f"inspect-project:{Path(main_tex).name}",
            )

        state = self.manager.load()
        state["rule_report"] = rule_report
        state["crossrefs_report"] = crossrefs_report
        state["source_hygiene_report"] = hygiene_report
        return state

    def run_round(
        self,
        main_tex: str,
        log_file: Optional[str] = None,
        page_dir: Optional[str] = None,
        template: Optional[str] = None,
        target_pages: Optional[int] = None,
        crossrefs_output: Optional[str] = None,
        rule_report_output: Optional[str] = None,
        column_void_report: Optional[str] = None,
        semantic_report: Optional[str] = None,
        gatekeeper_report: Optional[str] = None,
        gatekeeper_output: Optional[str] = None,
        emit_event: Optional[Callable[..., Dict[str, Any]]] = None,
        runtime_actions: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Compatibility wrapper for older callers.

        New typed runtime paths should prefer explicit runtime actions and call
        the internal core only while this compatibility layer is being retired.
        """
        return self._run_round_core(
            main_tex=main_tex,
            log_file=log_file,
            page_dir=page_dir,
            template=template,
            target_pages=target_pages,
            crossrefs_output=crossrefs_output,
            rule_report_output=rule_report_output,
            column_void_report=column_void_report,
            semantic_report=semantic_report,
            gatekeeper_report=gatekeeper_report,
            gatekeeper_output=gatekeeper_output,
            emit_event=emit_event,
            runtime_actions=runtime_actions,
        )

    def _run_round_core(
        self,
        main_tex: str,
        log_file: Optional[str] = None,
        page_dir: Optional[str] = None,
        template: Optional[str] = None,
        target_pages: Optional[int] = None,
        crossrefs_output: Optional[str] = None,
        rule_report_output: Optional[str] = None,
        column_void_report: Optional[str] = None,
        semantic_report: Optional[str] = None,
        gatekeeper_report: Optional[str] = None,
        gatekeeper_output: Optional[str] = None,
        emit_event: Optional[Callable[..., Dict[str, Any]]] = None,
        runtime_actions: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self._ensure_initialized_state(main_tex=main_tex, template=template, target_pages=target_pages)
        self.start_round()

        state = self.inspect_project(
            main_tex=main_tex,
            log_file=log_file,
            page_dir=page_dir,
            template=template,
            target_pages=target_pages,
            crossrefs_output=crossrefs_output,
            rule_report_output=rule_report_output,
            visual_signal_output=None,
            initialize_task=False,
            record_history=False,
            emit_event=emit_event,
            runtime_actions=runtime_actions,
        )

        cwd = Path.cwd()
        if column_void_report:
            cv_path = Path(column_void_report)
            if not cv_path.is_absolute():
                cv_path = cwd / cv_path
            if cv_path.is_file():
                state = self.ingest_column_void_report(str(cv_path))
        if semantic_report:
            sem_path = Path(semantic_report)
            if not sem_path.is_absolute():
                sem_path = cwd / sem_path
            if sem_path.is_file():
                state = self.ingest_semantic_report(str(sem_path))
        if gatekeeper_report:
            gate_path = Path(gatekeeper_report)
            if not gate_path.is_absolute():
                gate_path = cwd / gate_path
            if gate_path.is_file():
                state = self.manager.ingest_gatekeeper_decision(str(gate_path))
                state = self.manager.load()

        rule_report = state.get("rule_report") or self._load_optional_json(
            (cwd / (rule_report_output or "data/rule_report.json"))
        )
        defect_summary = state.get("defect_summary") or {}
        visual_signal_report = self._load_optional_json(
            cwd / (((self.manager.load().get("artifacts") or {}).get("visual_signal_report")) or "data/visual_signal_report.json")
        )
        state = self.set_next_actions(
            self._derive_next_actions(rule_report=rule_report, visual_signal_report=visual_signal_report)
        )
        state = self.set_agents_this_round(
            self._derive_agents_this_round(state=state, visual_signal_report=visual_signal_report)
        )

        gatekeeper_output = gatekeeper_output or "data/gatekeeper_decision.json"
        gatekeeper_action_source = "generated"
        gatekeeper_action_path = gatekeeper_output
        if gatekeeper_report:
            gate_path = Path(gatekeeper_report)
            if not gate_path.is_absolute():
                gate_path = cwd / gate_path
            if gate_path.is_file():
                self.manager.ingest_gatekeeper_decision(str(gate_path))
                gatekeeper_action_source = "provided_report"
                try:
                    gatekeeper_action_path = str(gate_path.resolve().relative_to(cwd.resolve()))
                except ValueError:
                    gatekeeper_action_path = str(gate_path)
        else:
            self._run_gatekeeper(output_path=gatekeeper_output)

        state = self.manager.load()
        decision = state.get("last_gatekeeper_decision")
        if decision is None:
            decision = "CONTINUE" if state.get("compile_success") else "BLOCKED"
        self._record_runtime_action(
            "gatekeeper_enforcer",
            {
                "success": decision is not None,
                "source": gatekeeper_action_source,
                "input_artifacts": {
                    "state": str(self.manager.state_path),
                    "defect_report": ((self.manager.load().get("artifacts") or {}).get("defect_report")),
                    "semantic_patch_report": ((self.manager.load().get("artifacts") or {}).get("semantic_patch_report")),
                    "provided_gatekeeper_report": gatekeeper_action_path if gatekeeper_action_source == "provided_report" else None,
                },
                "output_path": gatekeeper_action_path,
                "output_artifacts": {
                    "gatekeeper_decision": gatekeeper_action_path,
                },
                "decision": decision,
            },
            phase="verify",
            state="VERIFYING",
            emit_event=emit_event,
            runtime_actions=runtime_actions,
        )
        failure_type = None
        if decision != "DONE":
            compile_diagnostics = ((rule_report or {}).get("compile_diagnostics") or {})
            failure_type = (
                compile_diagnostics.get("primary_category")
                if not state.get("compile_success")
                else ("blocked_gate" if decision == "BLOCKED" else "remaining_defects")
            )
        self.manager.update_failure_tracking(decision=str(decision), failure_type=failure_type)

        state = self.add_history_entry(
            decision=decision,
            defects_found=defect_summary["remaining"],
            defects_resolved=defect_summary["resolved"],
            note=f"run-round:{Path(main_tex).name}",
        )

        state = self.manager.load()
        state["rule_report"] = rule_report
        return state

    def _ensure_initialized_state(
        self,
        main_tex: str,
        template: Optional[str] = None,
        target_pages: Optional[int] = None,
    ) -> Dict[str, Any]:
        try:
            state = self.manager.load()
            if not state.get("main_tex"):
                raise FileNotFoundError
            return state
        except FileNotFoundError:
            return self.init_task(main_tex=main_tex, template=template, target_pages=target_pages)

    def _load_optional_json(self, path: Path) -> Dict[str, Any]:
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _has_pending_float_priority_candidates(self, state: Optional[Dict[str, Any]] = None) -> bool:
        current_state = state or self.manager.load()
        repair_plan_summary = current_state.get("repair_plan_summary") or {}
        top_candidates = repair_plan_summary.get("top_candidates") or []
        if any(str(item.get("defect_family") or "") in {"B1", "B2"} for item in top_candidates):
            return True

        repair_plan_path = ((current_state.get("artifacts") or {}).get("repair_plan"))
        if not repair_plan_path:
            return False

        plan_path = Path(repair_plan_path)
        if not plan_path.is_absolute():
            plan_path = Path.cwd() / plan_path
        if not plan_path.is_file():
            return False

        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return False
        return any(str(item.get("defect_family") or "") in {"B1", "B2"} for item in plan.get("candidates") or [])

    def _derive_defect_summary(self, defect_report: Optional[Dict[str, Any]]) -> Dict[str, int]:
        state = self.manager.load()
        current = state.get("defect_summary") or {}
        defects = (defect_report or {}).get("defects") or []
        remaining = sum(
            1 for defect in defects if str(defect.get("status") or "open").lower() not in {"resolved", "closed"}
        )
        initial_total = max(int(current.get("initial_total") or 0), int(current.get("resolved") or 0) + remaining)
        resolved = max(initial_total - remaining, 0)
        return {
            "initial_total": initial_total,
            "resolved": resolved,
            "remaining": remaining,
        }

    def _derive_next_actions(
        self,
        rule_report: Optional[Dict[str, Any]],
        visual_signal_report: Optional[Dict[str, Any]] = None,
    ) -> list[str]:
        report = rule_report or {}
        summary = report.get("summary") or {}
        actions: list[str] = []
        float_priority_pending = self._has_pending_float_priority_candidates()
        if not report.get("compile_success", False):
            actions.append("Fix compilation blockers before visual loop continues")
        if int(summary.get("overfull_hbox_total") or 0) > 0:
            actions.append("Route D-class overflow defects to overflow-repair")
        if int(summary.get("underfull_hbox_total") or 0) > 0:
            if float_priority_pending:
                actions.append("Defer paragraph looseness/text edits until figure/table placement and sizing repairs are done")
            else:
                actions.append("Review spacing and paragraph looseness issues")
        routing_hints = (visual_signal_report or {}).get("routing_hints") or {}
        for action in routing_hints.get("next_actions") or []:
            if action not in actions:
                actions.append(action)
        priority_pages = routing_hints.get("priority_pages") or []
        if priority_pages:
            actions.append(f"Review priority pages from visual signals: {', '.join(str(p) for p in priority_pages[:5])}")
        cross_page_hints = (visual_signal_report or {}).get("cross_page_hints") or []
        if cross_page_hints:
            actions.append("Review recurring cross-page visual defects before local patching")
        crossref_hints = (visual_signal_report or {}).get("crossref_hints") or []
        if crossref_hints:
            actions.append("Review crossref distance hints for float-placement issues before moving figures")
        priority_objects = (visual_signal_report or {}).get("priority_objects") or []
        if priority_objects:
            top_objects = []
            for item in priority_objects[:3]:
                page = int(item.get("page") or 0)
                kind = str(item.get("object_kind") or "object").replace("_like", "")
                top_objects.append(f"p.{page} {kind}")
            actions.append(f"Inspect priority objects from visual signals: {', '.join(top_objects)}")
        repair_plan = (self.manager.load().get("repair_plan_summary") or {})
        if int(repair_plan.get("total_candidates") or 0) > 0:
            actions.append(
                f"Execute top repair-plan candidates first: {int(repair_plan.get('total_candidates') or 0)} queued"
            )
        if int(summary.get("warnings") or 0) == 0:
            actions.append("Proceed to quality gate evaluation")
        return actions

    def _derive_agents_this_round(
        self,
        state: Dict[str, Any],
        visual_signal_report: Optional[Dict[str, Any]] = None,
    ) -> list[str]:
        agents = ["orchestrator-agent", "rule-engine-agent"]
        if state.get("page_images_rendered"):
            agents.append("layout-detective-agent")

        findings = (visual_signal_report or {}).get("findings") or []
        taxonomy_ids = {str(f.get("taxonomy_defect_id") or "") for f in findings}
        float_priority_pending = self._has_pending_float_priority_candidates(state=state)

        if state.get("compile_success") and (
            (state.get("defect_summary") or {}).get("remaining", 0) > 0 or findings
        ):
            agents.append("code-surgeon-agent")

        if taxonomy_ids.intersection({"A1", "A2", "A3", "A6"}) and not float_priority_pending:
            agents.append("semantic-polish-agent")
        priority_objects = (visual_signal_report or {}).get("priority_objects") or []
        if any(str(item.get("object_kind") or "") in {"figure_like", "table_like"} for item in priority_objects):
            agents.append("layout-detective-agent")

        deduped: list[str] = []
        for agent in agents:
            if agent not in deduped:
                deduped.append(agent)
        return deduped

    def _parse_log_to_report(self, log_file: str, output_path: str) -> Dict[str, Any]:
        repo_scripts = Path(__file__).resolve().parent
        cmd = [
            self._python_executable(),
            str(repo_scripts / "parse_log.py"),
            log_file,
            "--output",
            output_path,
        ]
        completed = subprocess.run(cmd, check=False, cwd=Path.cwd(), capture_output=True, text=True)

        report_path = Path(output_path)
        if not report_path.is_file():
            raise subprocess.CalledProcessError(
                completed.returncode,
                cmd,
                output=completed.stdout,
                stderr=completed.stderr,
            )

        report = json.loads(report_path.read_text(encoding="utf-8"))
        if completed.returncode != 0:
            report["parse_log_runtime"] = {
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        return report

    def _run_gatekeeper(self, output_path: str) -> Dict[str, Any]:
        repo_scripts = Path(__file__).resolve().parent
        state = self.manager.load()
        cmd = [
            self._python_executable(),
            str(repo_scripts / "gatekeeper_enforcer.py"),
            "--state",
            str(self.manager.state_path),
            "--output",
            output_path,
        ]
        defect_report = ((state.get("artifacts") or {}).get("defect_report"))
        if defect_report:
            cmd.extend(["--defects", defect_report])
        semantic_report = ((state.get("artifacts") or {}).get("semantic_patch_report"))
        if semantic_report:
            cmd.extend(["--semantic-report", semantic_report])
        if bool(((state.get("task") or {}).get("strict_mode"))):
            cmd.append("--strict")
        subprocess.run(cmd, check=True, cwd=Path.cwd(), capture_output=True, text=True)
        self.manager.ingest_gatekeeper_decision(output_path)
        return self.manager.load()

    def _run_visual_signal_aggregator(
        self,
        pages_dir: str,
        output_path: str,
        rule_report_output: Optional[str] = None,
        crossrefs_output: Optional[str] = None,
    ) -> Dict[str, Any]:
        repo_scripts = Path(__file__).resolve().parent
        cmd = [
            self._python_executable(),
            str(repo_scripts / "visual_signal_aggregator.py"),
            pages_dir,
            "--output",
            output_path,
        ]
        state = self.manager.load()
        column_void_report = ((state.get("artifacts") or {}).get("column_void_report"))
        if column_void_report:
            cmd.extend(["--column-void-report", column_void_report])
        if rule_report_output:
            cmd.extend(["--log-report", rule_report_output])
        if crossrefs_output:
            cmd.extend(["--crossrefs-report", crossrefs_output])
        try:
            subprocess.run(cmd, check=True, cwd=Path.cwd(), capture_output=True, text=True)
        except subprocess.CalledProcessError:
            return {}
        return json.loads(Path(output_path).read_text(encoding="utf-8"))

    def _run_repair_plan_generator(
        self,
        visual_signal_report: str,
        output_path: str,
        crossrefs_output: Optional[str] = None,
        rule_report_output: Optional[str] = None,
        target_pages: Optional[int] = None,
    ) -> Dict[str, Any]:
        repo_scripts = Path(__file__).resolve().parent
        cmd = [
            self._python_executable(),
            str(repo_scripts / "repair_plan_generator.py"),
            visual_signal_report,
            "--output",
            output_path,
        ]
        if crossrefs_output:
            cmd.extend(["--crossrefs-report", crossrefs_output])
        if rule_report_output:
            cmd.extend(["--rule-report", rule_report_output])
        if target_pages is not None:
            cmd.extend(["--target-pages", str(target_pages)])
        try:
            subprocess.run(cmd, check=True, cwd=Path.cwd(), capture_output=True, text=True)
        except subprocess.CalledProcessError:
            return {}
        return json.loads(Path(output_path).read_text(encoding="utf-8"))

    def _run_defect_report_builder(
        self,
        rule_report_output: Optional[str],
        visual_signal_output: Optional[str],
        hygiene_report_output: Optional[str],
        output_path: str,
    ) -> Dict[str, Any]:
        repo_scripts = Path(__file__).resolve().parent
        cmd = [
            self._python_executable(),
            str(repo_scripts / "defect_report_builder.py"),
            "--output",
            output_path,
        ]
        if rule_report_output:
            cmd.extend(["--rule-report", rule_report_output])
        if visual_signal_output:
            cmd.extend(["--visual-signal-report", visual_signal_output])
        if hygiene_report_output:
            cmd.extend(["--hygiene-report", hygiene_report_output])
        subprocess.run(cmd, check=True, cwd=Path.cwd(), capture_output=True, text=True)
        return json.loads(Path(output_path).read_text(encoding="utf-8"))

    def _run_source_hygiene_check(self, main_tex: str, output_path: str) -> Dict[str, Any]:
        repo_scripts = Path(__file__).resolve().parent
        cmd = [
            self._python_executable(),
            str(repo_scripts / "source_hygiene_check.py"),
            main_tex,
            "--output",
            output_path,
        ]
        subprocess.run(cmd, check=True, cwd=Path.cwd(), capture_output=True, text=True)
        return json.loads(Path(output_path).read_text(encoding="utf-8"))

    def _run_repair_plan_executor(
        self,
        repair_plan_path: str,
        main_tex: str,
        output_path: str,
        column_type: Optional[str] = None,
        target_pages: Optional[int] = None,
        max_candidates: int = 3,
    ) -> Dict[str, Any]:
        repo_scripts = Path(__file__).resolve().parent
        cmd = [
            self._python_executable(),
            str(repo_scripts / "repair_plan_executor.py"),
            repair_plan_path,
            main_tex,
            "--output",
            output_path,
            "--max-candidates",
            str(max_candidates),
        ]
        if column_type:
            cmd.extend(["--column-type", column_type])
        if target_pages is not None:
            cmd.extend(["--target-pages", str(target_pages)])
        result = subprocess.run(cmd, check=False, cwd=Path.cwd(), capture_output=True, text=True)
        output_file = Path(output_path)
        if output_file.is_file():
            return json.loads(output_file.read_text(encoding="utf-8"))

        fallback = {
            "schema_version": "1.0",
            "status": "failed",
            "applied_count": 0,
            "selected_candidates": [],
            "error": {
                "returncode": result.returncode,
                "stdout_tail": (result.stdout or "")[-4000:],
                "stderr_tail": (result.stderr or "")[-4000:],
            },
        }
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(fallback, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return fallback

    def _extract_crossrefs(self, main_tex: str, output_path: str) -> Dict[str, Any]:
        repo_scripts = Path(__file__).resolve().parent
        cmd = [
            self._python_executable(),
            str(repo_scripts / "extract_crossrefs.py"),
            main_tex,
            "--output",
            output_path,
        ]
        subprocess.run(cmd, check=True, cwd=Path.cwd(), capture_output=True, text=True)
        return json.loads(Path(output_path).read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="PaperFit orchestrator runtime helpers")
    parser.add_argument("--state", default=StateManager.DEFAULT_STATE_PATH, help="Path to state.json")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    init_parser = subparsers.add_parser("init-task", help="Initialize task state")
    init_parser.add_argument("main_tex")
    init_parser.add_argument("--task", default="full_vto")
    init_parser.add_argument("--target-pages", type=int)
    init_parser.add_argument("--template")
    init_parser.add_argument("--strict", action="store_true")
    init_parser.add_argument("--max-rounds", type=int, default=10)
    init_parser.add_argument("--column-type", choices=["single", "double"], default=None)
    init_parser.add_argument("--page-budget", dest="page_budget_scope", default=None)

    subparsers.add_parser("start-round", help="Advance to the next round and reset round-scoped fields")

    compile_parser = subparsers.add_parser("mark-compile", help="Record compile result")
    compile_parser.add_argument("--success", action="store_true")
    compile_parser.add_argument("--failure", action="store_true")
    compile_parser.add_argument("--rule-report", dest="report_path", default=None)

    render_parser = subparsers.add_parser("mark-render", help="Record page-image render state")
    render_parser.add_argument("--rendered", action="store_true")
    render_parser.add_argument("--not-rendered", action="store_true")
    render_parser.add_argument("--page-dir", default=None)

    column_void_parser = subparsers.add_parser("ingest-column-void", help="Merge OpenCV column-void report into state")
    column_void_parser.add_argument("report_json")

    semantic_parser = subparsers.add_parser("ingest-semantic", help="Merge semantic budget report into state")
    semantic_parser.add_argument("report_json")

    defect_parser = subparsers.add_parser("set-defect-summary", help="Update defect summary fields")
    defect_parser.add_argument("--resolved", type=int, required=True)
    defect_parser.add_argument("--remaining", type=int, required=True)
    defect_parser.add_argument("--initial", type=int, default=None)

    actions_parser = subparsers.add_parser("set-next-actions", help="Replace next_actions list")
    actions_parser.add_argument("actions", nargs="+")

    agents_parser = subparsers.add_parser("set-agents", help="Replace agents_this_round list")
    agents_parser.add_argument("agents", nargs="+")

    artifact_parser = subparsers.add_parser("set-artifact", help="Set one artifacts.* field")
    artifact_parser.add_argument("key")
    artifact_parser.add_argument("value")

    history_parser = subparsers.add_parser("add-history", help="Append one history entry")
    history_parser.add_argument("--decision", default=None)
    history_parser.add_argument("--defects-found", type=int, default=None)
    history_parser.add_argument("--defects-resolved", type=int, default=None)
    history_parser.add_argument("--note", default=None)

    gate_parser = subparsers.add_parser("gatekeeper", help="Apply gatekeeper decision")
    gate_parser.add_argument("decision", choices=["DONE", "CONTINUE", "BLOCKED"])
    gate_parser.add_argument("--report", default=None)

    subparsers.add_parser("archive-task", help="Archive current state and return archive path")

    inspect_parser = subparsers.add_parser(
        "inspect-project",
        help="Parse existing project artifacts (.tex/.log/page images/crossrefs) into state",
    )
    inspect_parser.add_argument("main_tex")
    inspect_parser.add_argument("--log-file", default=None)
    inspect_parser.add_argument("--page-dir", default=None)
    inspect_parser.add_argument("--template", default=None)
    inspect_parser.add_argument("--target-pages", type=int, default=None)
    inspect_parser.add_argument("--crossrefs-output", default=None)
    inspect_parser.add_argument("--rule-report-output", default=None)

    infer_parser = subparsers.add_parser(
        "infer-task",
        help="Infer PaperFit task type from a natural-language request",
    )
    infer_parser.add_argument("request")

    run_round_parser = subparsers.add_parser(
        "run-round",
        help="Execute one round of project inspection and state aggregation",
    )
    run_round_parser.add_argument("main_tex")
    run_round_parser.add_argument("--log-file", default=None)
    run_round_parser.add_argument("--page-dir", default=None)
    run_round_parser.add_argument("--template", default=None)
    run_round_parser.add_argument("--target-pages", type=int, default=None)
    run_round_parser.add_argument("--crossrefs-output", default=None)
    run_round_parser.add_argument("--rule-report-output", default=None)
    run_round_parser.add_argument("--column-void-report", default=None)
    run_round_parser.add_argument("--semantic-report", default=None)
    run_round_parser.add_argument("--gatekeeper-report", default=None)
    run_round_parser.add_argument("--gatekeeper-output", default=None)

    run_task_parser = subparsers.add_parser(
        "run-task",
        help="Run a typed PaperFit TaskSpec through the runtime contract",
    )
    run_task_parser.add_argument("task_json")
    run_task_parser.add_argument("--output", default=None)

    repair_exec_parser = subparsers.add_parser(
        "execute-repair-plan",
        help="Execute top repair-plan candidates and write execution report",
    )
    repair_exec_parser.add_argument("main_tex")
    repair_exec_parser.add_argument("--repair-plan", dest="repair_plan_path", default=None)
    repair_exec_parser.add_argument("--output", dest="output_path", default="data/repair_execution_report.json")
    repair_exec_parser.add_argument("--column-type", default=None)
    repair_exec_parser.add_argument("--max-candidates", type=int, default=3)

    rollback_parser = subparsers.add_parser(
        "rollback-to-snapshot",
        help="Restore source files from a pre-repair snapshot manifest",
    )
    rollback_parser.add_argument("rollback_target")
    rollback_parser.add_argument("--output", dest="output_path", default="data/rollback_report.json")

    status_parser = subparsers.add_parser(
        "status-view",
        help="Print compact runtime status for host adapters",
    )
    status_parser.add_argument("--run-result", default=None)

    args = parser.parse_args()
    runtime = OrchestratorRuntime(state_path=args.state)

    if args.command == "init-task":
        out = runtime.init_task(
            main_tex=args.main_tex,
            task_type=args.task,
            target_pages=args.target_pages,
            template=args.template,
            strict_mode=args.strict,
            max_rounds=args.max_rounds,
            column_type=args.column_type,
            page_budget_scope=args.page_budget_scope,
        )
    elif args.command == "start-round":
        out = runtime.start_round()
    elif args.command == "mark-compile":
        success = True if args.success else False if args.failure else None
        if success is None:
            raise SystemExit("mark-compile requires --success or --failure")
        out = runtime.mark_compile(success=success, report_path=args.report_path)
    elif args.command == "mark-render":
        rendered = True if args.rendered else False if args.not_rendered else None
        if rendered is None:
            raise SystemExit("mark-render requires --rendered or --not-rendered")
        out = runtime.mark_page_images_rendered(rendered=rendered, page_dir=args.page_dir)
    elif args.command == "ingest-column-void":
        out = runtime.ingest_column_void_report(report_path=args.report_json)
    elif args.command == "ingest-semantic":
        out = runtime.ingest_semantic_report(report_path=args.report_json)
    elif args.command == "set-defect-summary":
        out = runtime.set_defect_summary(
            resolved=args.resolved,
            remaining=args.remaining,
            initial=args.initial,
        )
    elif args.command == "set-next-actions":
        out = runtime.set_next_actions(actions=args.actions)
    elif args.command == "set-agents":
        out = runtime.set_agents_this_round(agents=args.agents)
    elif args.command == "set-artifact":
        out = runtime.set_artifact(key=args.key, value=args.value)
    elif args.command == "add-history":
        out = runtime.add_history_entry(
            decision=args.decision,
            defects_found=args.defects_found,
            defects_resolved=args.defects_resolved,
            note=args.note,
        )
    elif args.command == "gatekeeper":
        out = runtime.apply_gatekeeper_decision(decision=args.decision, report_path=args.report)
    elif args.command == "archive-task":
        out = runtime.archive_task()
    elif args.command == "inspect-project":
        out = runtime.inspect_project(
            main_tex=args.main_tex,
            log_file=args.log_file,
            page_dir=args.page_dir,
            template=args.template,
            target_pages=args.target_pages,
            crossrefs_output=args.crossrefs_output,
            rule_report_output=args.rule_report_output,
        )
    elif args.command == "infer-task":
        out = runtime.infer_task_from_request(args.request)
    elif args.command == "run-round":
        out = runtime.run_round(
            main_tex=args.main_tex,
            log_file=args.log_file,
            page_dir=args.page_dir,
            template=args.template,
            target_pages=args.target_pages,
            crossrefs_output=args.crossrefs_output,
            rule_report_output=args.rule_report_output,
            column_void_report=args.column_void_report,
            semantic_report=args.semantic_report,
            gatekeeper_report=args.gatekeeper_report,
            gatekeeper_output=args.gatekeeper_output,
        )
    elif args.command == "run-task":
        out = runtime.run_task(
            task_spec=load_task_spec(args.task_json),
            output_path=args.output,
        )
    elif args.command == "execute-repair-plan":
        out = runtime.execute_repair_plan(
            main_tex=args.main_tex,
            repair_plan_path=args.repair_plan_path,
            output_path=args.output_path,
            column_type=args.column_type,
            max_candidates=args.max_candidates,
        )
    elif args.command == "rollback-to-snapshot":
        out = runtime.rollback_to_snapshot(
            rollback_target=args.rollback_target,
            output_path=args.output_path,
        )
    elif args.command == "status-view":
        out = runtime.status_view(run_result_path=args.run_result)
    else:
        parser.print_help()
        raise SystemExit(1)

    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
