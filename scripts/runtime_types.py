#!/usr/bin/env python3
"""Typed runtime contracts for PaperFit harness execution."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


VALID_TASK_TYPES = {
    "analyze_layout",
    "visual_only",
    "full_vto",
    "repair_table",
    "adjust_length",
    "template_migration",
    "status_query",
    "priority_query",
    "undo_last_change",
}
SOURCE_CHANGING_TASK_TYPES = {
    "full_vto",
    "repair_table",
    "adjust_length",
    "template_migration",
}


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _path_artifacts_from_result(result: Dict[str, Any]) -> Dict[str, Any]:
    artifacts: Dict[str, Any] = {}
    for key in (
        "output_path",
        "pdf_path",
        "page_dir",
        "compile_log",
        "log_file",
        "rollback_target",
    ):
        value = result.get(key)
        if value:
            artifacts[key] = value
    output_files = result.get("output_files")
    if isinstance(output_files, list):
        artifacts["output_files_count"] = len(output_files)
    return artifacts


def _action_risk_level(action_name: str, result: Dict[str, Any]) -> str:
    if action_name == "repair_plan_executor":
        return "high"
    if action_name in {"source_mutation_integrity", "rollback_to_snapshot"}:
        return "medium"
    if result.get("requires_approval"):
        return "high"
    return str(result.get("risk_level") or "low")


def _action_requires_approval(action_name: str, result: Dict[str, Any]) -> bool:
    if result.get("requires_approval") is not None:
        return _as_bool(result.get("requires_approval"), False)
    return action_name == "repair_plan_executor"


@dataclass
class ActionResult:
    """Normalized runtime action result emitted by the harness."""

    action_name: str
    phase: str
    runtime_state: str
    success: bool
    skipped: bool = False
    failure_type: Optional[str] = None
    command_or_strategy: Optional[str] = None
    input_artifacts: Dict[str, Any] = field(default_factory=dict)
    output_artifacts: Dict[str, Any] = field(default_factory=dict)
    freshness: Optional[Dict[str, Any]] = None
    risk_level: str = "low"
    requires_approval: bool = False
    details: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_result(
        cls,
        *,
        action_name: str,
        phase: str,
        runtime_state: str,
        result: Dict[str, Any],
    ) -> "ActionResult":
        raw = dict(result)
        skipped = _as_bool(raw.get("skipped"), False)
        if "success" in raw:
            success = _as_bool(raw.get("success"), False)
        elif "available" in raw:
            success = _as_bool(raw.get("available"), True)
        else:
            success = True

        failure_type = raw.get("failure_type")
        if failure_type is None and not success:
            failure_type = raw.get("reason") or "action_failed"

        command = raw.get("command")
        if isinstance(command, list):
            command_or_strategy = " ".join(str(part) for part in command)
        else:
            command_or_strategy = raw.get("command_or_strategy") or raw.get("strategy")
            if command_or_strategy is not None:
                command_or_strategy = str(command_or_strategy)

        input_artifacts = raw.get("input_artifacts") if isinstance(raw.get("input_artifacts"), dict) else {}
        output_artifacts = raw.get("output_artifacts") if isinstance(raw.get("output_artifacts"), dict) else {}
        output_artifacts = {**_path_artifacts_from_result(raw), **output_artifacts}
        freshness = raw.get("freshness") if isinstance(raw.get("freshness"), dict) else None
        risk_level = _action_risk_level(action_name, raw)

        return cls(
            action_name=action_name,
            phase=phase,
            runtime_state=runtime_state,
            success=success,
            skipped=skipped,
            failure_type=str(failure_type) if failure_type is not None else None,
            command_or_strategy=command_or_strategy,
            input_artifacts=input_artifacts,
            output_artifacts=output_artifacts,
            freshness=freshness,
            risk_level=risk_level,
            requires_approval=_action_requires_approval(action_name, raw),
            details=raw,
        )

    def to_dict(self) -> Dict[str, Any]:
        payload = dict(self.details)
        payload.update(
            {
                "schema_version": "1.0",
                "action_name": self.action_name,
                "phase": self.phase,
                "runtime_state": self.runtime_state,
                "success": self.success,
                "skipped": self.skipped,
                "failure_type": self.failure_type,
                "command_or_strategy": self.command_or_strategy,
                "input_artifacts": self.input_artifacts,
                "output_artifacts": self.output_artifacts,
                "freshness": self.freshness,
                "risk_level": self.risk_level,
                "requires_approval": self.requires_approval,
            }
        )
        return payload


@dataclass
class TaskSpec:
    """Normalized task request consumed by the runtime harness."""

    task_type: str
    main_tex: str
    schema_version: str = "1.0"
    task_id: Optional[str] = None
    project_root: str = "."
    template: Optional[str] = None
    target_pages: Optional[int] = None
    page_budget_scope: Optional[str] = None
    page_dir: str = "data/pages"
    log_file: Optional[str] = None
    column_void_report: Optional[str] = None
    allow_source_mutation: bool = False
    pre_repair_snapshot_required: bool = False
    dry_run_source_mutation: bool = False
    rollback_policy: Optional[str] = None
    strict_mode: bool = False
    max_rounds: int = 10
    user_request: Optional[str] = None
    required_phases: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "TaskSpec":
        raw_type = payload.get("task_type", payload.get("type"))
        if raw_type is None:
            raise ValueError("TaskSpec requires task_type")
        raw_main = payload.get("main_tex")
        if raw_main is None:
            raise ValueError("TaskSpec requires main_tex")

        target_pages = payload.get("target_pages")
        if target_pages is not None:
            target_pages = int(target_pages)

        required_phases = payload.get("required_phases") or []
        if not isinstance(required_phases, list):
            raise ValueError("TaskSpec.required_phases must be a list")

        spec = cls(
            schema_version=str(payload.get("schema_version") or "1.0"),
            task_id=payload.get("task_id"),
            task_type=str(raw_type),
            project_root=str(payload.get("project_root") or "."),
            main_tex=str(raw_main),
            template=payload.get("template"),
            target_pages=target_pages,
            page_budget_scope=payload.get("page_budget_scope"),
            page_dir=str(payload.get("page_dir") or "data/pages"),
            log_file=payload.get("log_file"),
            column_void_report=payload.get("column_void_report"),
            allow_source_mutation=_as_bool(payload.get("allow_source_mutation"), False),
            pre_repair_snapshot_required=_as_bool(payload.get("pre_repair_snapshot_required"), False),
            dry_run_source_mutation=_as_bool(payload.get("dry_run_source_mutation"), False),
            rollback_policy=payload.get("rollback_policy"),
            strict_mode=_as_bool(payload.get("strict_mode"), False),
            max_rounds=int(payload.get("max_rounds") or 10),
            user_request=payload.get("user_request"),
            required_phases=[str(item) for item in required_phases],
        )
        spec.validate()
        return spec

    def validate(self) -> None:
        if self.task_type not in VALID_TASK_TYPES:
            raise ValueError(f"Unsupported task_type: {self.task_type}")
        if not self.main_tex:
            raise ValueError("TaskSpec.main_tex must be non-empty")
        if self.max_rounds < 1:
            raise ValueError("TaskSpec.max_rounds must be >= 1")
        if self.task_type == "visual_only" and self.allow_source_mutation:
            raise ValueError("visual_only tasks cannot allow source mutation")
        if self.task_type in SOURCE_CHANGING_TASK_TYPES:
            if not self.allow_source_mutation:
                raise ValueError(f"{self.task_type} tasks require allow_source_mutation=true")
            if not self.pre_repair_snapshot_required:
                raise ValueError(f"{self.task_type} tasks require pre_repair_snapshot_required=true")
            if not self.rollback_policy:
                raise ValueError(f"{self.task_type} tasks require rollback_policy")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "task_type": self.task_type,
            "project_root": self.project_root,
            "main_tex": self.main_tex,
            "template": self.template,
            "target_pages": self.target_pages,
            "page_budget_scope": self.page_budget_scope,
            "page_dir": self.page_dir,
            "log_file": self.log_file,
            "column_void_report": self.column_void_report,
            "allow_source_mutation": self.allow_source_mutation,
            "pre_repair_snapshot_required": self.pre_repair_snapshot_required,
            "dry_run_source_mutation": self.dry_run_source_mutation,
            "rollback_policy": self.rollback_policy,
            "strict_mode": self.strict_mode,
            "max_rounds": self.max_rounds,
            "user_request": self.user_request,
            "required_phases": self.required_phases,
        }


@dataclass
class RunResult:
    """Stable runtime result returned to host adapters."""

    run_id: str
    task: TaskSpec
    status: str
    gatekeeper_decision: Optional[str]
    state_path: str
    event_log: str
    artifacts: Dict[str, Any]
    defect_summary: Dict[str, Any]
    runtime_actions: Dict[str, Any] = field(default_factory=dict)
    artifact_manifest: Optional[Dict[str, Any]] = None
    approval: Optional[Dict[str, Any]] = None
    repair_loop_policy: Optional[Dict[str, Any]] = None
    round_artifact_lineage: List[Dict[str, Any]] = field(default_factory=list)
    failure: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": "1.0",
            "run_id": self.run_id,
            "task": self.task.to_dict(),
            "status": self.status,
            "gatekeeper_decision": self.gatekeeper_decision,
            "state_path": self.state_path,
            "event_log": self.event_log,
            "artifacts": self.artifacts,
            "runtime_actions": self.runtime_actions,
            "artifact_manifest": self.artifact_manifest,
            "approval": self.approval,
            "repair_loop_policy": self.repair_loop_policy,
            "round_artifact_lineage": self.round_artifact_lineage,
            "defect_summary": self.defect_summary,
            "failure": self.failure,
        }


def load_task_spec(path: str) -> TaskSpec:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("TaskSpec file must contain a JSON object")
    return TaskSpec.from_dict(payload)
