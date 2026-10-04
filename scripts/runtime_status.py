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


def _first_present(*values: Any, default: Any = None) -> Any:
    for value in values:
        if value is not None:
            return value
    return default


def _sort_defects_for_display(defects: Any) -> list[Dict[str, Any]]:
    if not isinstance(defects, list):
        return []
    severity_rank = {"critical": 0, "major": 1, "unknown": 2, "minor": 3}

    def rank(defect: Any) -> tuple[int, int]:
        if not isinstance(defect, dict):
            return (2, 1)
        severity = str(defect.get("severity") or "unknown").lower()
        has_page = defect.get("page") is not None
        return (severity_rank.get(severity, 2), 0 if has_page else 1)

    return sorted(
        [defect for defect in defects if isinstance(defect, dict)],
        key=rank,
    )


def _selected_candidates_from_report(report: Dict[str, Any]) -> list[Dict[str, Any]]:
    selected_payload = report.get("selected_candidates") or []
    selected_items: list[Dict[str, Any]] = []
    if isinstance(selected_payload, list):
        selected_items = [item for item in selected_payload if isinstance(item, dict)]
    elif isinstance(selected_payload, dict):
        for group_items in selected_payload.values():
            if not isinstance(group_items, list):
                continue
            selected_items.extend([item for item in group_items if isinstance(item, dict)])

    selected: list[Dict[str, Any]] = []
    for item in selected_items[:5]:
        selected_item = {
            "defect_id": item.get("defect_id"),
            "object": item.get("object"),
            "page": item.get("page"),
        }
        for key in (
            "object_kind",
            "visual_width_subtype",
            "visual_object_width_ratio",
            "visual_overflow_pt",
        ):
            if item.get(key) is not None:
                selected_item[key] = item.get(key)
        selected.append(selected_item)
    return selected


def _summarize_selected_b2_width_candidates(selected_candidates: list[Dict[str, Any]]) -> Dict[str, Any]:
    selected = [
        candidate
        for candidate in selected_candidates
        if str(candidate.get("defect_id") or "") == "B2"
        and str(candidate.get("visual_width_subtype") or "") in {"overflow_width", "underfilled_width"}
    ]
    summary: Dict[str, Any] = {
        "total": len(selected),
        "by_subtype": {},
        "by_object_kind": {},
        "labels": [],
        "objects": [],
        "pages": [],
    }
    labels: list[str] = []
    objects: list[str] = []
    pages: list[int] = []
    for candidate in selected:
        subtype = str(candidate.get("visual_width_subtype") or "unknown")
        summary["by_subtype"][subtype] = int(summary["by_subtype"].get(subtype, 0)) + 1

        object_kind = str(candidate.get("object_kind") or "")
        if object_kind:
            summary["by_object_kind"][object_kind] = int(summary["by_object_kind"].get(object_kind, 0)) + 1

        object_name = str(candidate.get("object") or "")
        if object_name:
            labels.append(object_name)
            objects.append(object_name)

        try:
            page = int(candidate.get("page") or 0)
        except (TypeError, ValueError):
            page = 0
        if page > 0:
            pages.append(page)

    summary["labels"] = sorted(set(labels))
    summary["objects"] = sorted(set(objects))
    summary["pages"] = sorted(set(pages))
    return summary


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
    task = state.get("task") or {}

    result_path: Optional[Path] = None
    if run_result_path is not None:
        candidate = Path(run_result_path)
        result_path = candidate if candidate.is_absolute() else root / candidate
    else:
        task_type = task.get("type")
        if task_type in {"full_vto", "adjust_length", "repair_table", "template_migration"}:
            result_names = (
                "run_result_template_migration.json",
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
    explicit_run_result = run_result_path is not None and bool(run_result)
    if explicit_run_result:
        result_defect_summary = run_result.get("defect_summary")
        if isinstance(result_defect_summary, dict):
            defect_summary = result_defect_summary
    gatekeeper_report_path: Optional[Path] = None
    gatekeeper_artifact = artifacts.get("gatekeeper_decision")
    if gatekeeper_artifact:
        candidate = Path(str(gatekeeper_artifact))
        gatekeeper_report_path = candidate if candidate.is_absolute() else root / candidate
    gatekeeper_report = _load_json(gatekeeper_report_path) if gatekeeper_report_path is not None else {}
    visual_report_path: Optional[Path] = None
    visual_artifact = artifacts.get("visual_signal_report")
    if visual_artifact:
        candidate = Path(str(visual_artifact))
        visual_report_path = candidate if candidate.is_absolute() else root / candidate
    visual_report = _load_json(visual_report_path) if visual_report_path is not None else {}
    repair_plan_path: Optional[Path] = None
    repair_plan_artifact = artifacts.get("repair_plan")
    if repair_plan_artifact:
        candidate = Path(str(repair_plan_artifact))
        repair_plan_path = candidate if candidate.is_absolute() else root / candidate
    repair_plan = _load_json(repair_plan_path) if repair_plan_path is not None else {}
    repair_execution_path: Optional[Path] = None
    repair_execution_artifact = artifacts.get("repair_execution_report")
    if repair_execution_artifact:
        candidate = Path(str(repair_execution_artifact))
        repair_execution_path = candidate if candidate.is_absolute() else root / candidate
    repair_execution_report = _load_json(repair_execution_path) if repair_execution_path is not None else {}
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

    if explicit_run_result:
        gatekeeper_decision = (
            run_result.get("gatekeeper_decision")
            or state.get("last_gatekeeper_decision")
            or gatekeeper_report.get("decision")
        )
    else:
        gatekeeper_decision = (
            gatekeeper_report.get("decision")
            or state.get("last_gatekeeper_decision")
            or run_result.get("gatekeeper_decision")
        )
    gatekeeper_report_matches_run = (
        not explicit_run_result
        or not gatekeeper_report.get("decision")
        or gatekeeper_report.get("decision") == gatekeeper_decision
    )
    remaining_defects = _sort_defects_for_display(
        gatekeeper_report.get("remaining_defects") if gatekeeper_report_matches_run else []
    )
    run_result_status = str(run_result.get("status") or "").upper()
    run_result_runtime_state = {
        "DONE": "DONE",
        "CONTINUE": "EVALUATING",
        "BLOCKED": "BLOCKED",
        "FAILED": "BLOCKED",
    }.get(run_result_status, run_result_status)
    runtime_state = run_result_runtime_state if explicit_run_result and run_result_runtime_state else state.get("status")
    if gatekeeper_decision == "CONTINUE" and runtime_state == "DONE":
        runtime_state = "EVALUATING"
    elif gatekeeper_decision == "BLOCKED":
        runtime_state = "BLOCKED"
    status_consistency = {
        "state_status": state.get("status"),
        "effective_status": runtime_state,
        "gatekeeper_decision": gatekeeper_decision,
        "stale_state_overridden": runtime_state != state.get("status"),
        "stale_sections": [],
        "reason": None,
        "gatekeeper_artifact_matches_run_result": gatekeeper_report_matches_run,
    }
    if status_consistency["stale_state_overridden"]:
        status_consistency["reason"] = (
            "explicit_run_result_overrides_current_runtime_state"
            if explicit_run_result
            else "gatekeeper_artifact_overrides_stale_runtime_state"
        )
        status_consistency["stale_sections"] = [
            "runtime.last_runtime_state",
            "repair_loop_policy",
        ]
    repair_plan_json_summary = repair_plan.get("summary") or {}
    b2_width_candidates = (
        repair_plan_summary.get("b2_width_candidates")
        or repair_plan_json_summary.get("b2_width_candidates")
    )
    b2_width_targetable_candidates = (
        repair_plan_summary.get("b2_width_targetable_candidates")
        or repair_plan_json_summary.get("b2_width_targetable_candidates")
    )
    b2_width_untargetable_candidates = (
        repair_plan_summary.get("b2_width_untargetable_candidates")
        or repair_plan_json_summary.get("b2_width_untargetable_candidates")
    )
    b2_width_findings = (
        repair_plan_summary.get("b2_width_findings")
        or repair_plan_json_summary.get("b2_width_findings")
        or (visual_report.get("summary") or {}).get("b2_width_findings")
    )
    b2_width_unmatched_findings = (
        repair_plan_summary.get("b2_width_unmatched_findings")
        if repair_plan_summary.get("b2_width_unmatched_findings") is not None
        else repair_plan_json_summary.get("b2_width_unmatched_findings")
    )
    if b2_width_unmatched_findings is None and isinstance(b2_width_findings, dict) and isinstance(b2_width_candidates, dict):
        b2_width_unmatched_findings = max(
            0,
            int(b2_width_findings.get("total") or 0) - int(b2_width_candidates.get("total") or 0),
        )
    b2_width_unmatched_pages = _first_present(
        repair_plan_summary.get("b2_width_unmatched_pages"),
        repair_plan_json_summary.get("b2_width_unmatched_pages"),
        default=[],
    )
    b2_width_unmatched_finding_ids = _first_present(
        repair_plan_summary.get("b2_width_unmatched_finding_ids"),
        repair_plan_json_summary.get("b2_width_unmatched_finding_ids"),
        default=[],
    )
    execution_status = (
        repair_execution_summary.get("status")
        or repair_execution_report.get("status")
        or repair_action.get("status")
    )
    if repair_execution_summary.get("status") is not None:
        applied_count = int(repair_execution_summary.get("applied_count") or 0)
    elif repair_execution_report.get("applied_count") is not None:
        applied_count = int(repair_execution_report.get("applied_count") or 0)
    else:
        applied_count = int(repair_action.get("applied_count") or 0)
    selected_candidates = (
        repair_execution_summary.get("selected_candidates")
        or _selected_candidates_from_report(repair_execution_report)
    )
    b2_width_selected_candidates = (
        repair_execution_summary.get("b2_width_selected_candidates")
        or repair_execution_report.get("b2_width_selected_candidates")
        or _summarize_selected_b2_width_candidates(selected_candidates)
    )

    status = {
        "schema_version": "1.0",
        "project_root": str(root),
        "state_path": str(state_file),
        "main_tex": state.get("main_tex"),
        "task_type": task.get("type"),
        "task": {
            "type": task.get("type"),
            "target_pages": task.get("target_pages"),
            "page_budget_scope": task.get("page_budget_scope"),
            "template": task.get("template"),
        },
        "status": runtime_state,
        "gatekeeper_decision": gatekeeper_decision,
        "status_consistency": status_consistency,
        "gatekeeper": {
            "decision": gatekeeper_decision,
            "reasons": gatekeeper_report.get("reasons") or [] if gatekeeper_report_matches_run else [],
            "remaining_defects": remaining_defects,
        },
        "visual": {
            "total_findings": int((visual_report.get("summary") or {}).get("total_findings") or 0),
            "b2_width_findings": (visual_report.get("summary") or {}).get("b2_width_findings"),
        },
        "defect_summary": {
            "initial_total": int(defect_summary.get("initial_total") or 0),
            "resolved": int(defect_summary.get("resolved") or 0),
            "remaining": int(defect_summary.get("remaining") or 0),
        },
        "runtime": {
            "run_id": run_result.get("run_id") or event_summary.get("run_id") if explicit_run_result else event_summary.get("run_id") or run_result.get("run_id"),
            "event_log": run_result.get("event_log") or event_summary.get("event_log") if explicit_run_result else event_summary.get("event_log") or run_result.get("event_log"),
            "event_count": int(event_summary.get("event_count") or 0),
            "last_event_type": event_summary.get("last_event_type"),
            "last_phase": event_summary.get("last_phase"),
            "last_runtime_state": event_summary.get("last_runtime_state"),
            "actions": event_summary.get("actions") or {},
        },
        "artifacts": {
            "task_spec": artifacts.get("task_spec"),
            "page_images_dir": artifacts.get("page_images_dir"),
            "gatekeeper_decision": artifacts.get("gatekeeper_decision"),
            "visual_signal_report": artifacts.get("visual_signal_report"),
            "defect_report": artifacts.get("defect_report"),
            "repair_plan": artifacts.get("repair_plan"),
            "repair_execution_report": artifacts.get("repair_execution_report"),
            "rollback_report": artifacts.get("rollback_report"),
            "source_mutation_report": artifacts.get("source_mutation_report"),
        },
        "repair": {
            "plan_candidates": int(
                repair_plan_summary.get("total_candidates")
                if repair_plan_summary.get("total_candidates") is not None
                else repair_plan_json_summary.get("total_candidates") or 0
            ),
            "plan_immutability_policy": repair_plan_summary.get("immutability_policy"),
            "plan_source_fingerprint_sha256": repair_plan_summary.get("source_fingerprint_sha256"),
            "execution_status": execution_status,
            "applied_count": applied_count,
            "selected_candidates": selected_candidates,
            "b2_width_selected_candidates": b2_width_selected_candidates,
            "b2_width_findings": b2_width_findings,
            "b2_width_candidates": b2_width_candidates,
            "b2_width_targetable_candidates": b2_width_targetable_candidates,
            "b2_width_untargetable_candidates": b2_width_untargetable_candidates,
            "b2_width_unmatched_findings": b2_width_unmatched_findings,
            "b2_width_unmatched_pages": b2_width_unmatched_pages,
            "b2_width_unmatched_finding_ids": b2_width_unmatched_finding_ids,
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
