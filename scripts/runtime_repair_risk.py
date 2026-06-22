#!/usr/bin/env python3
"""Candidate-level risk classification for source-changing repair plans."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def classify_repair_candidate_risk(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """Classify the mutation risk for one repair-plan candidate."""

    defect_family = str(candidate.get("defect_family") or "")
    candidate_type = str(candidate.get("candidate_type") or "")
    proposed_action = str(candidate.get("proposed_action") or "")
    target = candidate.get("target") if isinstance(candidate.get("target"), dict) else {}
    target_kind = str(target.get("object_kind") or "")
    target_float_type = str(target.get("float_type") or "")
    section_distance = _as_int(candidate.get("section_distance"), 0)

    operation = "layout_macros"
    mutation_surface: List[str] = ["layout_macros"]
    risk_level = "medium"
    reason = "layout_macro_adjustment"

    if defect_family.startswith("A") or defect_family.startswith("C"):
        operation = "spacing"
        mutation_surface = ["spacing"]
        risk_level = "low"
        reason = "spacing_or_consistency_adjustment"
    elif defect_family == "B1":
        operation = "float_placement"
        mutation_surface = ["float_placement"]
        risk_level = "medium"
        reason = "float_placement_near_reference"
        if section_distance >= 1:
            operation = "float_movement_across_section_boundary"
            risk_level = "high"
            reason = "float_movement_may_cross_section_boundary"
    elif defect_family in {"B2", "B3"}:
        operation = "float_placement" if defect_family == "B3" else "layout_macros"
        mutation_surface = ["float_placement"] if defect_family == "B3" else ["layout_macros"]
        risk_level = "medium"
        reason = "float_layout_adjustment"
        if target_kind == "table_like" or target_float_type == "table" or candidate.get("source_table_env"):
            operation = "table_reconstruction"
            mutation_surface = ["table_environment", "table_placement"]
            risk_level = "high"
            reason = "table_layout_reconstruction_risk"
    elif defect_family.startswith("D"):
        operation = "layout_macros"
        mutation_surface = ["layout_macros"]
        risk_level = "medium"
        reason = "overflow_layout_repair"

    if candidate_type == "global" and proposed_action == "review_paragraph_spacing_and_looseness":
        operation = "semantic_text_edit"
        mutation_surface = ["text_spans"]
        risk_level = "high"
        reason = "semantic_text_edit_requires_fresh_approval"
    if proposed_action == "template_migration":
        operation = "template_migration"
        mutation_surface = ["documentclass", "preamble", "template_macros"]
        risk_level = "high"
        reason = "template_migration_requires_fresh_approval"

    return {
        "schema_version": "1.0",
        "risk_level": risk_level,
        "operation": operation,
        "mutation_surface": mutation_surface,
        "requires_fresh_approval": risk_level == "high",
        "reason": reason,
    }


def annotate_repair_candidates(candidates: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return candidates with a stable risk object attached."""

    annotated: List[Dict[str, Any]] = []
    for candidate in candidates:
        item = dict(candidate)
        if not isinstance(item.get("risk"), dict):
            item["risk"] = classify_repair_candidate_risk(item)
        annotated.append(item)
    return annotated
