#!/usr/bin/env python3
"""Host-facing approval summaries for source-changing PaperFit actions."""

from __future__ import annotations

from typing import Any, Dict


SOURCE_CHANGING_TASK_TYPES = {
    "full_vto",
    "repair_table",
    "adjust_length",
    "template_migration",
}

TASK_RISK_PROFILES: Dict[str, Dict[str, Any]] = {
    "full_vto": {
        "approval_scope": "bounded_layout_repair",
        "mutation_surface": ["layout_macros", "float_placement", "spacing"],
        "high_risk_operations": [
            "semantic_text_edit",
            "table_reconstruction",
            "template_migration",
            "float_movement_across_section_boundary",
            "bibliography_or_endmatter_edit",
            "paper_object_deletion_or_hiding",
        ],
    },
    "repair_table": {
        "approval_scope": "table_repair",
        "mutation_surface": ["table_environment", "table_caption", "table_placement"],
        "high_risk_operations": [
            "table_reconstruction",
            "semantic_text_edit",
            "paper_object_deletion_or_hiding",
        ],
    },
    "adjust_length": {
        "approval_scope": "length_adjustment",
        "mutation_surface": ["text_spans", "spacing", "float_placement"],
        "high_risk_operations": [
            "semantic_text_edit",
            "bibliography_or_endmatter_edit",
            "paper_object_deletion_or_hiding",
        ],
    },
    "template_migration": {
        "approval_scope": "template_migration",
        "mutation_surface": ["documentclass", "preamble", "template_macros", "layout_macros"],
        "high_risk_operations": [
            "template_migration",
            "semantic_text_edit",
            "table_reconstruction",
            "bibliography_or_endmatter_edit",
            "paper_object_deletion_or_hiding",
        ],
    },
}


def _risk_profile(task_type: str) -> Dict[str, Any]:
    profile = TASK_RISK_PROFILES.get(task_type) or {}
    return {
        "approval_scope": profile.get("approval_scope") or task_type or "unknown",
        "mutation_surface": list(profile.get("mutation_surface") or []),
        "high_risk_operations": list(profile.get("high_risk_operations") or []),
        "fresh_approval_required_for_high_risk_operations": True,
    }


def _task_type(task: Dict[str, Any]) -> str:
    return str(task.get("task_type") or task.get("type") or "")


def _repair_action(runtime_actions: Dict[str, Any]) -> Dict[str, Any]:
    action = runtime_actions.get("repair_plan_executor") or {}
    return action if isinstance(action, dict) else {}


def _max_risk(*values: Any) -> str:
    order = {"low": 0, "medium": 1, "high": 2}
    risk = "low"
    for value in values:
        candidate = str(value or "").lower()
        if order.get(candidate, 0) > order.get(risk, 0):
            risk = candidate
    return risk


def build_approval_object(
    *,
    task: Dict[str, Any],
    state: Dict[str, Any],
    runtime_actions: Dict[str, Any],
) -> Dict[str, Any]:
    """Build a compact approval contract for host adapters.

    This object intentionally duplicates a few fields from repair/action state so
    callers do not need to infer approval semantics from low-level runtime data.
    """

    task_type = _task_type(task)
    if task_type not in SOURCE_CHANGING_TASK_TYPES:
        return {
            "schema_version": "1.0",
            "status": "not_applicable",
            "requires_approval": False,
            "approval_granted": False,
            "risk_level": "low",
            "reason": "task_does_not_change_source",
            "action": None,
            "approval_mechanisms": [],
            "artifacts": {},
        }

    action = _repair_action(runtime_actions)
    repair_plan_summary = state.get("repair_plan_summary") or {}
    content_integrity = state.get("content_integrity") or {}
    artifacts = state.get("artifacts") or {}

    action_skipped = bool(action.get("skipped"))
    action_reason = action.get("reason")
    dry_run = bool(task.get("dry_run_source_mutation")) or action_reason == "dry_run_source_mutation"
    plan_candidates = int(
        repair_plan_summary.get("total_candidates")
        or action.get("planned_candidates")
        or 0
    )
    applied_count = int(action.get("applied_count") or 0)
    action_requires_approval = bool(action.get("requires_approval"))
    risk_level = _max_risk(action.get("risk_level"), "high" if plan_candidates > 0 else "low")
    risk_profile = _risk_profile(task_type)

    if dry_run and (plan_candidates > 0 or action_reason == "dry_run_source_mutation"):
        status = "approval_required"
        requires_approval = True
        approval_granted = False
        reason = str(action_reason or "dry_run_source_mutation")
    elif not dry_run and applied_count > 0:
        status = "approved_and_executed"
        requires_approval = False
        approval_granted = True
        reason = "source_mutation_executed"
    elif not dry_run and action.get("status"):
        status = "approved_no_effective_change"
        requires_approval = False
        approval_granted = True
        reason = str(action.get("status"))
    elif action_skipped:
        status = "not_required"
        requires_approval = False
        approval_granted = False
        reason = str(action_reason or "repair_skipped")
    else:
        status = "not_required"
        requires_approval = action_requires_approval
        approval_granted = not dry_run and action_requires_approval
        reason = "no_repair_candidates" if plan_candidates == 0 else "approval_state_unknown"

    return {
        "schema_version": "1.0",
        "status": status,
        "requires_approval": requires_approval,
        "approval_granted": approval_granted,
        "risk_level": risk_level,
        "reason": reason,
        "action": "repair_plan_executor",
        "requested_operation": "apply_repair_plan_candidates",
        "approval_mechanisms": [
            "--apply",
            "PAPERFIT_TYPED_FIX_LAYOUT_APPLY=1",
        ],
        "policy": {
            "source_mutation_default": "dry_run",
            "source_mutation_requires_explicit_approval": True,
            "rollback_policy": task.get("rollback_policy"),
            "pre_repair_snapshot_required": bool(task.get("pre_repair_snapshot_required")),
            "approval_scope": risk_profile["approval_scope"],
            "mutation_surface": risk_profile["mutation_surface"],
            "high_risk_operations": risk_profile["high_risk_operations"],
            "fresh_approval_required_for_high_risk_operations": risk_profile[
                "fresh_approval_required_for_high_risk_operations"
            ],
        },
        "plan": {
            "candidates": plan_candidates,
            "immutability_policy": repair_plan_summary.get("immutability_policy"),
            "source_fingerprint_sha256": repair_plan_summary.get("source_fingerprint_sha256"),
        },
        "execution": {
            "dry_run_source_mutation": dry_run,
            "skipped": action_skipped,
            "skip_reason": action_reason if action_skipped else None,
            "applied_count": applied_count,
            "status": action.get("status"),
        },
        "artifacts": {
            "repair_plan": artifacts.get("repair_plan"),
            "rollback_target": content_integrity.get("rollback_target"),
            "repair_execution_report": artifacts.get("repair_execution_report"),
            "source_mutation_report": artifacts.get("source_mutation_report"),
        },
    }


def build_approval_scope_carry_forward_check(
    *,
    task: Dict[str, Any],
    approval: Dict[str, Any],
) -> Dict[str, Any]:
    """Check whether a later repair round can reuse the same approval scope.

    This is a reporting contract only. It does not grant permission to mutate
    source; hosts still need an explicit apply approval for source-changing work.
    """

    task_type = _task_type(task)
    if task_type not in SOURCE_CHANGING_TASK_TYPES:
        return {
            "schema_version": "1.0",
            "status": "not_applicable",
            "task_type": task_type,
            "reason": "task_does_not_change_source",
        }

    expected = _risk_profile(task_type)
    policy = approval.get("policy") if isinstance(approval.get("policy"), dict) else {}
    mutation_surface = policy.get("mutation_surface") if isinstance(policy.get("mutation_surface"), list) else []
    high_risk_operations = (
        policy.get("high_risk_operations") if isinstance(policy.get("high_risk_operations"), list) else []
    )
    expected_surface = set(expected["mutation_surface"])
    expected_high_risk = set(expected["high_risk_operations"])
    actual_surface = set(str(item) for item in mutation_surface)
    actual_high_risk = set(str(item) for item in high_risk_operations)

    checks = {
        "approval_scope_matches": policy.get("approval_scope") == expected["approval_scope"],
        "mutation_surface_within_scope": bool(actual_surface) and actual_surface.issubset(expected_surface),
        "high_risk_operations_declared": expected_high_risk.issubset(actual_high_risk),
        "fresh_approval_required_for_high_risk_operations": bool(
            policy.get("fresh_approval_required_for_high_risk_operations")
        ),
    }
    status = "pass" if all(checks.values()) else "blocked"
    reason = "approval_scope_can_carry_forward" if status == "pass" else "approval_scope_contract_mismatch"

    return {
        "schema_version": "1.0",
        "status": status,
        "task_type": task_type,
        "reason": reason,
        "expected_approval_scope": expected["approval_scope"],
        "approval_scope": policy.get("approval_scope"),
        "mutation_surface": mutation_surface,
        "high_risk_operations": high_risk_operations,
        "checks": checks,
    }
