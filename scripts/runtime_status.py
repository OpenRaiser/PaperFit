#!/usr/bin/env python3
"""Compact runtime status summaries for host adapters."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from runtime_approval import build_approval_object
except ModuleNotFoundError:  # package import during unit tests
    from .runtime_approval import build_approval_object


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def build_runtime_status(
    *,
    project_root: Path,
    state_path: str | Path = "data/state.json",
    run_result_path: Optional[str | Path] = None,
) -> Dict[str, Any]:
    root = project_root.resolve()
    state_file = Path(state_path)
    if not state_file.is_absolute():
        state_file = root / state_file
    state = _load_json(state_file)

    event_summary = state.get("runtime_event_summary") or {}
    artifacts = state.get("artifacts") or {}
    defect_summary = state.get("defect_summary") or {}
    repair_plan_summary = state.get("repair_plan_summary") or {}
    repair_execution_summary = state.get("repair_execution_summary") or {}
    content_integrity = state.get("content_integrity") or {}

    result_path: Optional[Path] = None
    if run_result_path is not None:
        candidate = Path(run_result_path)
        result_path = candidate if candidate.is_absolute() else root / candidate
    else:
        task_type = (state.get("task") or {}).get("type")
        if task_type in {"full_vto", "adjust_length", "repair_table"}:
            result_names = (
                "run_result_full_vto_nondry.json",
                "run_result_agent.json",
                "run_result_fix_layout_typed.json",
                "run_result_full_vto_dry_run.json",
                "run_result_full_vto.json",
                "run_result.json",
                "run_result_check_visual.json",
            )
        else:
            result_names = (
                "run_result_check_visual.json",
                "run_result.json",
                "run_result_full_vto_dry_run.json",
                "run_result_full_vto.json",
            )
        for name in result_names:
            candidate = root / "data" / name
            if candidate.is_file():
                result_path = candidate
                break

    run_result = _load_json(result_path) if result_path is not None else {}
    freshness = ((run_result.get("artifact_manifest") or {}).get("freshness") or {})
    repair_action = ((run_result.get("runtime_actions") or {}).get("repair_plan_executor") or {})
    repair_loop_policy = run_result.get("repair_loop_policy")
    if not isinstance(repair_loop_policy, dict):
        repair_loop_policy = None
    round_artifact_lineage = run_result.get("round_artifact_lineage")
    if not isinstance(round_artifact_lineage, list):
        round_artifact_lineage = []
    if not round_artifact_lineage and isinstance(repair_loop_policy, dict):
        policy_lineage = repair_loop_policy.get("round_artifact_lineage")
        if isinstance(policy_lineage, list):
            round_artifact_lineage = policy_lineage
    approval = run_result.get("approval") if isinstance(run_result.get("approval"), dict) else None
    if approval is None:
        approval = build_approval_object(
            task=run_result.get("task") or (state.get("task") or {}),
            state=state,
            runtime_actions=run_result.get("runtime_actions") or {},
        )
    terminal_success_guard = state.get("terminal_success_guard")
    if terminal_success_guard is None:
        failure = run_result.get("failure") if isinstance(run_result, dict) else None
        if isinstance(failure, dict) and failure.get("failure_type") == "terminal_success_without_fresh_visual_evidence":
            terminal_success_guard = {
                "status": "blocked",
                "failure_type": failure.get("failure_type"),
                "reason": failure.get("reason"),
                "artifact_freshness": failure.get("artifact_freshness"),
            }

    status = {
        "schema_version": "1.0",
        "project_root": str(root),
        "state_path": str(state_file),
        "main_tex": state.get("main_tex"),
        "task_type": (state.get("task") or {}).get("type"),
        "status": state.get("status"),
        "gatekeeper_decision": state.get("last_gatekeeper_decision") or run_result.get("gatekeeper_decision"),
        "defect_summary": {
            "initial_total": int(defect_summary.get("initial_total") or 0),
            "resolved": int(defect_summary.get("resolved") or 0),
            "remaining": int(defect_summary.get("remaining") or 0),
        },
        "runtime": {
            "run_id": event_summary.get("run_id") or run_result.get("run_id"),
            "event_log": event_summary.get("event_log") or run_result.get("event_log"),
            "event_count": int(event_summary.get("event_count") or 0),
            "last_event_type": event_summary.get("last_event_type"),
            "last_phase": event_summary.get("last_phase"),
            "last_runtime_state": event_summary.get("last_runtime_state"),
            "actions": event_summary.get("actions") or {},
        },
        "artifacts": {
            "task_spec": artifacts.get("task_spec"),
            "page_images_dir": artifacts.get("page_images_dir"),
            "visual_signal_report": artifacts.get("visual_signal_report"),
            "defect_report": artifacts.get("defect_report"),
            "repair_plan": artifacts.get("repair_plan"),
            "repair_execution_report": artifacts.get("repair_execution_report"),
            "rollback_report": artifacts.get("rollback_report"),
            "source_mutation_report": artifacts.get("source_mutation_report"),
        },
        "repair": {
            "plan_candidates": int(repair_plan_summary.get("total_candidates") or 0),
            "plan_immutability_policy": repair_plan_summary.get("immutability_policy"),
            "plan_source_fingerprint_sha256": repair_plan_summary.get("source_fingerprint_sha256"),
            "execution_status": repair_execution_summary.get("status") or repair_action.get("status"),
            "applied_count": int(repair_execution_summary.get("applied_count") or repair_action.get("applied_count") or 0),
            "skipped": bool(repair_action.get("skipped")),
            "skip_reason": repair_action.get("reason") if repair_action.get("skipped") else None,
            "risk_level": repair_action.get("risk_level"),
            "requires_approval": repair_action.get("requires_approval"),
        },
        "approval": approval,
        "repair_loop_policy": repair_loop_policy,
        "round_artifact_lineage": round_artifact_lineage,
        "content_integrity": {
            "validation_status": content_integrity.get("validation_status"),
            "action_taken": content_integrity.get("action_taken"),
            "rollback_target": content_integrity.get("rollback_target"),
        },
        "artifact_freshness": {
            "status": freshness.get("status"),
            "blocking_checks": freshness.get("blocking_checks") or [],
        },
        "terminal_success_guard": terminal_success_guard,
        "next_actions": state.get("next_actions") or [],
    }
    if result_path is not None:
        try:
            status["run_result_path"] = str(result_path.resolve().relative_to(root))
        except ValueError:
            status["run_result_path"] = str(result_path)
    return status
