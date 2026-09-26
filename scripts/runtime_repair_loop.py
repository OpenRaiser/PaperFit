#!/usr/bin/env python3
"""Repair loop policy summaries for bounded source-changing runs."""

from __future__ import annotations

from typing import Any, Dict, List

try:
    from runtime_approval import build_approval_scope_carry_forward_check
except ModuleNotFoundError:  # package import during unit tests
    from .runtime_approval import build_approval_scope_carry_forward_check


SOURCE_CHANGING_TASK_TYPES = {
    "full_vto",
    "repair_table",
    "adjust_length",
    "template_migration",
}


def _task_type(task: Dict[str, Any]) -> str:
    return str(task.get("task_type") or task.get("type") or "")


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _readiness_block_reason(checks: Dict[str, bool]) -> str:
    reason_by_check = {
        "approval_scope_carry_forward_pass": "approval_scope_carry_forward_blocked",
        "round_artifact_lineage_present": "round_artifact_lineage_missing",
        "artifact_freshness_pass": "artifact_freshness_not_pass",
        "mutation_integrity_available": "mutation_integrity_missing",
        "source_mutation_executed": "source_mutation_not_executed",
        "candidate_approval_scope_gate_pass": "approval_scope_blocked",
        "within_round_limit": "round_limit_reached",
        "gatekeeper_continue": "gatekeeper_not_continue",
        "runtime_execution_mode_can_auto_apply": "runtime_execution_mode_cannot_auto_apply",
    }
    for check_name, passed in checks.items():
        if passed is False:
            return reason_by_check.get(check_name, check_name)
    return "readiness_blocked"


def build_round_artifact_lineage(
    *,
    state: Dict[str, Any],
    runtime_actions: Dict[str, Any],
    round_number: int | None = None,
) -> List[Dict[str, Any]]:
    """Summarize action-level artifact flow for the current runtime round."""

    current_round = max(1, _as_int(round_number if round_number is not None else state.get("current_round"), 1))
    actions: Dict[str, Any] = {}
    for action_name, action in (runtime_actions or {}).items():
        if not isinstance(action, dict):
            continue
        input_artifacts = action.get("input_artifacts") if isinstance(action.get("input_artifacts"), dict) else {}
        output_artifacts = action.get("output_artifacts") if isinstance(action.get("output_artifacts"), dict) else {}
        if not input_artifacts and not output_artifacts:
            continue
        actions[action_name] = {
            "phase": action.get("phase"),
            "runtime_state": action.get("runtime_state"),
            "input_artifacts": input_artifacts,
            "output_artifacts": output_artifacts,
        }
    return [
        {
            "schema_version": "1.0",
            "round": current_round,
            "actions": actions,
        }
    ]


def build_repair_loop_policy(
    *,
    task: Dict[str, Any],
    state: Dict[str, Any],
    runtime_actions: Dict[str, Any],
    artifact_manifest: Dict[str, Any],
    approval: Dict[str, Any],
    status: str,
    gatekeeper_decision: str,
    round_artifact_lineage: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any] | None:
    """Build the V1 source-changing loop policy and next-round readiness."""

    task_type = _task_type(task)
    if task_type not in SOURCE_CHANGING_TASK_TYPES:
        return None

    repair_plan_summary = state.get("repair_plan_summary") or {}
    content_integrity = state.get("content_integrity") or {}
    repair_action = (runtime_actions.get("repair_plan_executor") or {}) if isinstance(runtime_actions, dict) else {}
    freshness = (artifact_manifest.get("freshness") or {}) if isinstance(artifact_manifest, dict) else {}
    approval_policy = approval.get("policy") or {}
    approval_scope_gate = repair_action.get("approval_scope_gate") if isinstance(repair_action, dict) else None
    if not isinstance(approval_scope_gate, dict):
        approval_scope_gate = None

    max_rounds = max(1, _as_int(task.get("max_rounds"), 1))
    current_round = max(1, _as_int(state.get("current_round"), 1))
    plan_candidates = _as_int(repair_plan_summary.get("total_candidates") or repair_action.get("planned_candidates"), 0)
    applied_count = _as_int(repair_action.get("applied_count"), 0)
    dry_run = bool(task.get("dry_run_source_mutation")) or repair_action.get("reason") == "dry_run_source_mutation"
    runtime_execution_mode_can_auto_apply = not dry_run and max_rounds > 1
    execution_mode = "report_only" if dry_run else "bounded_apply"

    stop_condition = "continue"
    gatekeeper_done = str(status or "").lower() == "done" or str(gatekeeper_decision or "").upper() == "DONE"
    freshness_status = freshness.get("status")

    failure_tracking = state.get("failure_tracking") or {}
    failure_type = str(failure_tracking.get("last_failure_type") or "")

    if failure_type == "post_repair_hard_guard_failed":
        stop_condition = "post_repair_hard_guard_failed"
    elif approval_scope_gate and approval_scope_gate.get("status") != "pass":
        stop_condition = "approval_scope_blocked"
    elif gatekeeper_done and freshness_status != "pass":
        stop_condition = "artifact_freshness_not_pass"
    elif approval.get("status") == "approval_required":
        stop_condition = "approval_required"
    elif gatekeeper_done:
        stop_condition = "done"
    elif str(status or "").lower() == "blocked":
        stop_condition = "blocked"
    elif freshness_status and freshness_status != "pass":
        stop_condition = "artifact_freshness_not_pass"
    elif current_round >= max_rounds:
        stop_condition = "round_limit_reached"

    next_round_reason = "multi_round_apply_not_enabled_in_current_runtime"
    if stop_condition == "post_repair_hard_guard_failed":
        next_round_reason = "post_repair_hard_guard_failed"
    elif stop_condition == "approval_scope_blocked":
        next_round_reason = "approval_scope_blocked"
    elif stop_condition == "artifact_freshness_not_pass":
        next_round_reason = "artifact_freshness_not_pass"
    elif dry_run:
        next_round_reason = "dry_run_source_mutation"
    elif approval.get("status") == "approval_required":
        next_round_reason = "approval_required"
    elif stop_condition == "done":
        next_round_reason = "gatekeeper_done"
    elif stop_condition == "blocked":
        next_round_reason = "runtime_blocked"
    elif stop_condition == "round_limit_reached":
        next_round_reason = "round_limit_reached"

    approval_scope_carry_forward = build_approval_scope_carry_forward_check(
        task=task,
        approval=approval,
    )
    round_lineage = round_artifact_lineage or []
    readiness_checks = {
        "approval_scope_carry_forward_pass": approval_scope_carry_forward.get("status") == "pass",
        "round_artifact_lineage_present": bool(round_lineage),
        "artifact_freshness_pass": freshness.get("status") == "pass",
        "mutation_integrity_available": bool(content_integrity.get("validation_status")),
        "source_mutation_executed": applied_count > 0,
        "candidate_approval_scope_gate_pass": approval_scope_gate is None or approval_scope_gate.get("status") == "pass",
        "within_round_limit": current_round < max_rounds,
        "gatekeeper_continue": str(gatekeeper_decision or "").upper() == "CONTINUE",
        "runtime_execution_mode_can_auto_apply": runtime_execution_mode_can_auto_apply,
    }
    readiness_status = "ready" if all(readiness_checks.values()) else "blocked"
    next_round_allowed = readiness_status == "ready" and stop_condition == "continue"
    if next_round_allowed:
        next_round_reason = "ready"
    elif stop_condition == "continue":
        next_round_reason = _readiness_block_reason(readiness_checks)

    return {
        "schema_version": "1.0",
        "execution_mode": execution_mode,
        "task_type": task_type,
        "round_limit": max_rounds,
        "current_round": current_round,
        "candidate_batch_limit": 0 if dry_run else 1,
        "dry_run_source_mutation": dry_run,
        "approval_scope": approval_policy.get("approval_scope"),
        "mutation_surface": approval_policy.get("mutation_surface") or [],
        "high_risk_operations": approval_policy.get("high_risk_operations") or [],
        "fresh_approval_required_for_high_risk_operations": bool(
            approval_policy.get("fresh_approval_required_for_high_risk_operations")
        ),
        "plan_candidates": plan_candidates,
        "applied_count": applied_count,
        "artifact_freshness": freshness.get("status"),
        "mutation_integrity_status": content_integrity.get("validation_status"),
        "stop_condition": stop_condition,
        "next_round_allowed": next_round_allowed,
        "next_round_reason": next_round_reason,
        "approval_scope_carry_forward": approval_scope_carry_forward,
        "candidate_approval_scope_gate": approval_scope_gate,
        "round_artifact_lineage": round_lineage,
        "second_round_apply_readiness": {
            "schema_version": "1.0",
            "status": readiness_status,
            "reason": next_round_reason if readiness_status == "blocked" else "ready",
            "checks": readiness_checks,
        },
    }
