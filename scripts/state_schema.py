#!/usr/bin/env python3
"""
State schema helpers for PaperFit.
"""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Dict, List, Optional


STATE_VERSION = "1.0"
LEGACY_FIELD_MIGRATIONS = {
    "compile_pass": "compile_success",
}
TASK_FIELDS = (
    "type",
    "target_pages",
    "template",
    "strict_mode",
    "column_type",
    "page_budget_scope",
    "portrait_path",
    "portrait_refreshed_at",
    "portrait_scanned",
)
ARTIFACT_FIELDS = (
    "rule_report",
    "crossrefs_report",
    "source_hygiene_report",
    "page_images_dir",
    "column_void_report",
    "column_void_schema_version",
    "semantic_patch_report",
    "gatekeeper_decision",
    "visual_signal_report",
    "defect_report",
    "repair_plan",
    "repair_execution_report",
)
DEFECT_SUMMARY_FIELDS = ("initial_total", "resolved", "remaining")
CONTENT_INTEGRITY_FIELDS = (
    "validation_status",
    "violation_level",
    "action_taken",
    "rollback_target",
)
FAILURE_TRACKING_FIELDS = (
    "consecutive_failures",
    "stalled",
    "conservative_mode",
    "manual_review_required",
    "last_failure_type",
    "last_failure_round",
    "last_failure_at",
)
ROOT_FIELDS = (
    "project",
    "version",
    "created_at",
    "updated_at",
    "archived_at",
    "main_tex",
    "task",
    "artifacts",
    "cv_signals_summary",
    "visual_signals_summary",
    "repair_plan_summary",
    "repair_execution_summary",
    "current_round",
    "max_rounds",
    "status",
    "compile_success",
    "page_images_rendered",
    "defect_summary",
    "agents_this_round",
    "last_gatekeeper_decision",
    "next_actions",
    "history",
    "pre_repair_snapshot",
    "content_integrity",
    "semantic_budget_summary",
    "failure_tracking",
)


def build_default_state(
    main_tex: str = "",
    task_type: str = "full_vto",
    target_pages: Optional[int] = None,
    template: Optional[str] = None,
    strict_mode: bool = False,
    max_rounds: int = 10,
    column_type: Optional[str] = None,
    page_budget_scope: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "project": "PaperFit",
        "version": STATE_VERSION,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "main_tex": main_tex,
        "task": {
            "type": task_type,
            "target_pages": target_pages,
            "template": template,
            "strict_mode": strict_mode,
            "column_type": column_type,
            "page_budget_scope": page_budget_scope,
            "portrait_path": None,
            "portrait_refreshed_at": None,
            "portrait_scanned": None,
        },
        "artifacts": {
            "rule_report": None,
            "crossrefs_report": None,
            "page_images_dir": "data/pages",
            "column_void_report": None,
            "column_void_schema_version": None,
            "semantic_patch_report": None,
            "gatekeeper_decision": None,
            "visual_signal_report": None,
            "defect_report": None,
            "repair_plan": None,
            "repair_execution_report": None,
        },
        "cv_signals_summary": {
            "schema_version": "1.0",
            "tool": "detect_column_void",
            "a5_candidate_pages": [],
            "a5_candidate_count": 0,
            "pages_flagged_count": 0,
            "by_page": [],
            "updated_at": None,
        },
        "visual_signals_summary": {
            "schema_version": "1.0",
            "priority_pages": [],
            "priority_objects": [],
            "cross_page_hints": [],
            "crossref_hints": [],
            "consistency_summary": None,
            "updated_at": None,
        },
        "repair_plan_summary": {
            "schema_version": "1.0",
            "total_candidates": 0,
            "top_candidates": [],
            "updated_at": None,
        },
        "repair_execution_summary": {
            "schema_version": "1.0",
            "status": None,
            "applied_count": 0,
            "selected_candidates": [],
            "updated_at": None,
        },
        "current_round": 0,
        "max_rounds": max_rounds,
        "status": "INITIALIZED",
        "compile_success": None,
        "page_images_rendered": False,
        "defect_summary": {
            "initial_total": 0,
            "resolved": 0,
            "remaining": 0,
        },
        "agents_this_round": [],
        "last_gatekeeper_decision": None,
        "next_actions": [],
        "history": [],
        "pre_repair_snapshot": None,
        "content_integrity": {
            "validation_status": None,
            "violation_level": None,
            "action_taken": None,
            "rollback_target": None,
        },
        "semantic_budget_summary": None,
        "failure_tracking": {
            "consecutive_failures": 0,
            "stalled": False,
            "conservative_mode": False,
            "manual_review_required": False,
            "last_failure_type": None,
            "last_failure_round": None,
            "last_failure_at": None,
        },
        "archived_at": None,
    }


def deep_update(target: Dict[str, Any], source: Dict[str, Any]) -> None:
    for key, value in source.items():
        if key in target and isinstance(target[key], dict) and isinstance(value, dict):
            deep_update(target[key], value)
        else:
            target[key] = value


def normalize_state(state: Dict[str, Any]) -> Dict[str, Any]:
    normalized = copy.deepcopy(state)

    for legacy_key, canonical_key in LEGACY_FIELD_MIGRATIONS.items():
        if legacy_key in normalized and canonical_key not in normalized:
            normalized[canonical_key] = normalized.pop(legacy_key)
        else:
            normalized.pop(legacy_key, None)

    task = normalized.get("task") or {}
    defaults = build_default_state(
        main_tex=str(normalized.get("main_tex") or ""),
        task_type=str(task.get("type") or "full_vto"),
        target_pages=task.get("target_pages"),
        template=task.get("template"),
        strict_mode=bool(task.get("strict_mode", False)),
        max_rounds=int(normalized.get("max_rounds") or 10),
        column_type=task.get("column_type"),
        page_budget_scope=task.get("page_budget_scope"),
    )
    defaults["created_at"] = normalized.get("created_at") or defaults["created_at"]
    defaults["updated_at"] = normalized.get("updated_at") or defaults["updated_at"]
    deep_update(defaults, normalized)
    defaults["version"] = str(defaults.get("version") or STATE_VERSION)
    return defaults


def validate_state(state: Dict[str, Any]) -> Dict[str, Any]:
    current = normalize_state(state)
    errors: List[str] = []

    if not isinstance(current.get("project"), str) or not current["project"]:
        errors.append("project must be a non-empty string")
    unknown_root_fields = sorted(set(current.keys()) - set(ROOT_FIELDS))
    if unknown_root_fields:
        errors.append("state contains unknown keys: " + ", ".join(unknown_root_fields))
    if not isinstance(current.get("version"), str) or not current["version"]:
        errors.append("version must be a non-empty string")
    if not isinstance(current.get("main_tex"), str):
        errors.append("main_tex must be a string")
    if not isinstance(current.get("task"), dict):
        errors.append("task must be an object")
    else:
        unknown_task_fields = sorted(set(current["task"].keys()) - set(TASK_FIELDS))
        if unknown_task_fields:
            errors.append("task contains unknown keys: " + ", ".join(unknown_task_fields))
    if not isinstance(current.get("artifacts"), dict):
        errors.append("artifacts must be an object")
    else:
        artifact_keys = set(current["artifacts"].keys())
        unknown_artifacts = sorted(artifact_keys - set(ARTIFACT_FIELDS))
        if unknown_artifacts:
            errors.append(
                "artifacts contains unknown keys: " + ", ".join(unknown_artifacts)
            )
        for key in ARTIFACT_FIELDS:
            value = current["artifacts"].get(key)
            if value is not None and not isinstance(value, str):
                errors.append(f"artifacts.{key} must be a string or null")
    if not isinstance(current.get("cv_signals_summary"), dict):
        errors.append("cv_signals_summary must be an object")
    if not isinstance(current.get("visual_signals_summary"), dict):
        errors.append("visual_signals_summary must be an object")
    if not isinstance(current.get("repair_plan_summary"), dict):
        errors.append("repair_plan_summary must be an object")
    if not isinstance(current.get("repair_execution_summary"), dict):
        errors.append("repair_execution_summary must be an object")
    if not isinstance(current.get("defect_summary"), dict):
        errors.append("defect_summary must be an object")
    else:
        unknown_defect_fields = sorted(
            set(current["defect_summary"].keys()) - set(DEFECT_SUMMARY_FIELDS)
        )
        if unknown_defect_fields:
            errors.append(
                "defect_summary contains unknown keys: " + ", ".join(unknown_defect_fields)
            )
    if not isinstance(current.get("content_integrity"), dict):
        errors.append("content_integrity must be an object")
    else:
        unknown_content_fields = sorted(
            set(current["content_integrity"].keys()) - set(CONTENT_INTEGRITY_FIELDS)
        )
        if unknown_content_fields:
            errors.append(
                "content_integrity contains unknown keys: " + ", ".join(unknown_content_fields)
            )
    if not isinstance(current.get("failure_tracking"), dict):
        errors.append("failure_tracking must be an object")
    else:
        unknown_failure_fields = sorted(
            set(current["failure_tracking"].keys()) - set(FAILURE_TRACKING_FIELDS)
        )
        if unknown_failure_fields:
            errors.append(
                "failure_tracking contains unknown keys: " + ", ".join(unknown_failure_fields)
            )
    if not isinstance(current.get("history"), list):
        errors.append("history must be a list")
    if not isinstance(current.get("agents_this_round"), list):
        errors.append("agents_this_round must be a list")
    if not isinstance(current.get("next_actions"), list):
        errors.append("next_actions must be a list")

    for key in ("current_round", "max_rounds"):
        if not isinstance(current.get(key), int):
            errors.append(f"{key} must be an integer")

    for key in ("compile_success", "page_images_rendered"):
        value = current.get(key)
        if value is not None and not isinstance(value, bool):
            errors.append(f"{key} must be a boolean or null")

    if current.get("last_gatekeeper_decision") is not None and not isinstance(
        current.get("last_gatekeeper_decision"), str
    ):
        errors.append("last_gatekeeper_decision must be a string or null")

    task = current.get("task") or {}
    if task.get("strict_mode") is not None and not isinstance(task.get("strict_mode"), bool):
        errors.append("task.strict_mode must be a boolean")

    failure_tracking = current.get("failure_tracking") or {}
    if not isinstance(failure_tracking.get("consecutive_failures"), int):
        errors.append("failure_tracking.consecutive_failures must be an integer")
    for key in ("stalled", "conservative_mode", "manual_review_required"):
        if not isinstance(failure_tracking.get(key), bool):
            errors.append(f"failure_tracking.{key} must be a boolean")

    if errors:
        raise ValueError("Invalid state schema: " + "; ".join(errors))

    return current
