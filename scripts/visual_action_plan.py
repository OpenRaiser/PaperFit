#!/usr/bin/env python3
"统一视觉诊断协议：visual_action_plan.json。"

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ACTION_SCHEMA_VERSION = "1.0"

# 家族 → 建议动作（与 repair_plan_generator/fixer_registry 的路由一致）
FAMILY_ACTIONS: Dict[str, List[str]] = {
    "A1": ["apply_looseness", "widow_orphan_penalty"],
    "A2": ["compress_trailing_whitespace"],
    "A3": ["reduce_page_count", "expand_page_count"],
    "A4": ["balance_final_columns"],
    "A5": ["reflow_column_void"],
    "A6": ["review_density_shift"],
    "B1": [
        "normalize_float_position_near_reference",
        "move_float_closer_to_first_reference",
    ],
    "B2": ["adjust_float_width"],
    "B3": ["decluster_float_sequence", "pack_late_floats_before_endmatter"],
    "B4": ["convert_to_longtable", "force_here_placement"],
    "B5": ["decluster_float_sequence"],
    "C1": ["remove_resizebox_around_tabular"],
    "C2": [],  # 图像分辨率：仅诊断，无安全执行器
    "C3": ["normalize_caption_setup"],
    "C4": ["normalize_caption_spacing"],
    "D1": ["repair_overfull_boxes"],
    "D2": ["split_display_formula"],
    "D3": ["wrap_url_breaking"],
}

# 这些家族的执行器当前被封锁（docs/DEFERRED_DESIGN_WORK.md）
BLOCKED_FAMILIES: Dict[str, str] = {
    "E1": "template_width_migration executor is apply_blocked by design",
    "E2": "template_page_budget executor is apply_blocked by design",
    "E3": "template_macro_compat executor is apply_blocked by design",
    "C2": "image resolution has no safe executor; enlarging would fake success",
}

CONFIDENCE_REVIEW_THRESHOLD = 0.6


def _family_of(finding: Dict[str, Any]) -> str:
    return str(finding.get("taxonomy_defect_id") or finding.get("defect_id") or "")[:2].upper() if finding.get("taxonomy_defect_id") or finding.get("defect_id") else ""


def _object_type(finding: Dict[str, Any]) -> str:
    metrics = finding.get("metrics") or {}
    kind = str(metrics.get("object_kind") or "")
    if kind:
        return normalized_visual_object_kind(kind)
    family = _family_of(finding)
    if family.startswith("D"):
        return "equation_or_text"
    if family.startswith("A"):
        return "column_or_page"
    return "unknown"


def _float_kind(value: Any) -> Optional[str]:
    kind = str(value or "").strip().lower()
    if kind.endswith("*"):
        kind = kind[:-1]
    return {
        "figure": "figure",
        "figure_like": "figure",
        "table": "table",
        "table_like": "table",
    }.get(kind)


def normalized_visual_object_kind(value: Any) -> str:
    """Use the source-to-layout protocol's float kinds for model aliases."""
    raw = str(value or "")
    float_kind = _float_kind(raw)
    return {"figure": "figure_like", "table": "table_like"}.get(float_kind, raw)


def vlm_table_size_semantics(finding: Dict[str, Any]) -> Optional[str]:
    """Disambiguate the VLM vocabulary from the existing CV C1/C2 taxonomy."""
    if finding.get("source") != "vlm_detector":
        return None
    metrics = finding.get("metrics") or {}
    if _float_kind(metrics.get("object_kind")) != "table":
        return None
    source_code = str(metrics.get("defect_type") or "").strip().lower()
    family = _family_of(finding)
    if family == "C2" and source_code == "c2":
        return "overflow_width"
    if family == "C1" and source_code == "c1":
        return "undersized_table"
    return None


def _resolve_anchor(finding: Dict[str, Any], crossrefs_report: Dict[str, Any]) -> Dict[str, Any]:
    """尝试把对象类 finding 解析到源码：caption 编号 → label → 源码行。"""
    metrics = finding.get("metrics") or {}
    label = str(metrics.get("label") or finding.get("label") or "")
    caption_number = metrics.get("caption_number")
    object_kind = _float_kind(metrics.get("object_kind"))
    floats = [flt for flt in crossrefs_report.get("floats") or [] if isinstance(flt, dict)]
    float_entry: Optional[Dict[str, Any]] = None
    anchor_reason: Optional[str] = None
    # Resolve an explicit label across the whole source before considering a
    # caption fallback. Figure and table counters both commonly start at 1.
    label_matches = [flt for flt in floats if label and str(flt.get("label") or "") == label]
    if len(label_matches) == 1:
        candidate = label_matches[0]
        if object_kind and _float_kind(candidate.get("float_type")) != object_kind:
            anchor_reason = "object_kind_label_mismatch"
        else:
            float_entry = candidate
    elif label_matches:
        anchor_reason = "ambiguous_label"
    elif caption_number is not None:
        number_matches = [
            flt for flt in floats
            if flt.get("number") == caption_number
            and (object_kind is None or _float_kind(flt.get("float_type")) == object_kind)
        ]
        if len(number_matches) == 1:
            float_entry = number_matches[0]
        elif number_matches:
            anchor_reason = "ambiguous_caption_number"
    source_file = crossrefs_report.get("source_file")
    if float_entry is not None:
        line = float_entry.get("line_number")
        anchors = [
            {
                "file": source_file,
                "start_line": int(line) if line is not None else None,
                "end_line": None,
            }
        ]
        return {
            "label": float_entry.get("label"),
            "caption_number": float_entry.get("number") or caption_number,
            "source_anchors": anchors,
            "anchor_status": "resolved",
            "anchor_reason": None,
        }
    return {
        "label": label or None,
        "caption_number": caption_number,
        "source_anchors": [],
        "anchor_status": "unresolved",
        "anchor_reason": anchor_reason,
    }


def document_scope_b1_authorized(candidate: Dict[str, Any]) -> bool:
    """Validate host approval and structured evidence, never model permission."""
    if candidate.get("document_scope_approved") is not True:
        return False
    for key in ("hard_guard_evidence", "visual_hard_guard", "hard_guard"):
        evidence = candidate.get(key)
        if not isinstance(evidence, dict):
            continue
        kind = str(evidence.get("failure_type") or evidence.get("type") or "")
        if kind in {"endmatter_float_intrusion", "body_float_intrudes_endmatter",
                    "float_only_pages", "float_stacking"}:
            return True
        if (evidence.get("available") is True
                and isinstance(evidence.get("intrusions"), list) and evidence["intrusions"]
                and isinstance(evidence.get("hard_failures"), list) and evidence["hard_failures"]):
            return True
    return False


def _status_for(family: str, *, confidence: float, anchor_status: str, severity: str, anchor_reason: Optional[str] = None, document_scope_authorized: bool = False) -> Dict[str, Any]:
    if family in BLOCKED_FAMILIES:
        return {"status": "blocked", "reason": BLOCKED_FAMILIES[family]}
    if anchor_reason:
        return {"status": "human_review", "reason": f"source anchor unresolved: {anchor_reason}"}
    if confidence < CONFIDENCE_REVIEW_THRESHOLD:
        return {"status": "human_review", "reason": f"confidence {confidence} below {CONFIDENCE_REVIEW_THRESHOLD}"}
    if not FAMILY_ACTIONS.get(family):
        return {"status": "human_review", "reason": f"no action mapping for family {family or 'unknown'}"}
    if family == "B1" and anchor_status == "unresolved":
        if document_scope_authorized:
            return {"status": "executable", "reason": "host-approved document scope with structured evidence"}
        return {"status": "human_review", "reason": "source anchor unresolved; document scope requires explicit host approval and structured evidence"}
    if anchor_status == "unresolved" and severity in {"major", "critical"}:
        # 页级/对象级缺陷允许无锚点进入人工复核——绝不静默丢弃
        return {"status": "human_review", "reason": "source anchor unresolved"}
    return {"status": "executable", "reason": None}


def build_visual_action_plan(
    *,
    visual_report: Dict[str, Any],
    crossrefs_report: Dict[str, Any],
    include_hints: bool = True,
    document_scope_approvals: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    findings: List[Dict[str, Any]] = list(visual_report.get("findings") or [])
    entries: List[Dict[str, Any]] = []

    def _emit(finding: Dict[str, Any], *, is_hint: bool) -> None:
        source_family = _family_of(finding)
        table_semantics = vlm_table_size_semantics(finding)
        family = "B2" if table_semantics == "overflow_width" else source_family
        metrics = finding.get("metrics") or {}
        confidence_raw = finding.get("confidence")
        confidence = float(confidence_raw) if isinstance(confidence_raw, (int, float)) else 0.5
        severity = str(finding.get("severity") or "minor")
        finding_id = str(finding.get("defect_id") or finding.get("id") or f"finding-{len(entries) + 1:04d}")
        # Separate host input is the only approval authority. Finding fields,
        # including VLM metrics, cannot grant source mutation permissions.
        approval = (document_scope_approvals or {}).get(finding_id) or {}
        document_authorized = (isinstance(approval, dict)
                               and document_scope_b1_authorized(approval))
        anchor = _resolve_anchor(finding, crossrefs_report)
        decision = _status_for(
            family,
            confidence=confidence,
            anchor_status=anchor["anchor_status"],
            severity=severity,
            anchor_reason=anchor["anchor_reason"],
            document_scope_authorized=document_authorized,
        )
        if table_semantics == "undersized_table":
            decision = {
                "status": "human_review",
                "reason": "VLM undersized table needs source width evidence; it does not imply a resizebox or style defect",
            }
        if is_hint and decision["status"] == "executable":
            decision = {"status": "human_review", "reason": "low-confidence hint below promotion threshold"}
        entry: Dict[str, Any] = {
            "finding_id": finding_id,
            "defect_type": table_semantics or str(metrics.get("subtype") or family or "unknown"),
            "family": family or None,
            "severity": severity,
            "page": finding.get("page"),
            "bbox": finding.get("bbox"),
            "object_type": "table_like" if table_semantics else _object_type(finding),
            "label": anchor["label"],
            "caption_number": anchor["caption_number"],
            "source_anchors": anchor["source_anchors"],
            "anchor_status": anchor["anchor_status"],
            "anchor_reason": anchor["anchor_reason"],
            "evidence": [
                str(finding.get("description") or ""),
                str(finding.get("source") or ""),
            ],
            "confidence": confidence,
            "recommended_actions": [] if table_semantics == "undersized_table" else FAMILY_ACTIONS.get(family, []),
            "risk": _risk_for_family(family),
            "status": decision["status"],
            "status_reason": decision["reason"],
            "origin": "hint" if is_hint else "finding",
        }
        if family == "B1" and document_authorized and anchor["anchor_status"] == "unresolved":
            entry["recommended_actions"] = ["normalize_float_placements_document_level"]
            for key in ("document_scope_approved", "hard_guard_evidence", "visual_hard_guard",
                        "hard_guard", "endmatter_float_intrusion", "body_float_intrudes_endmatter"):
                if approval.get(key) is not None:
                    entry[key] = approval[key]
        if table_semantics:
            entry["source_taxonomy_defect_id"] = source_family
            entry["source_defect_type"] = metrics.get("defect_type")
            entry["routing_reason"] = "VLM table-size vocabulary differs from the CV consistency taxonomy"
        width_subtype = table_semantics or str(metrics.get("subtype") or "")
        if family == "B2" and width_subtype in {"overflow_width", "underfilled_width"}:
            entry["visual_width_subtype"] = width_subtype
        entries.append(entry)

    for finding in findings:
        _emit(finding, is_hint=False)
    if include_hints:
        for hint in visual_report.get("hints") or []:
            _emit(hint, is_hint=True)

    counts: Dict[str, int] = {}
    for entry in entries:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1

    return {
        "schema_version": ACTION_SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "total_entries": len(entries),
            "findings": len(findings),
            "hints": len(entries) - len(findings),
            "by_status": counts,
            "dropped": 0,  # 协议保证：输入的每条 finding/hint 都有 entry
        },
        "entries": entries,
    }


def _risk_for_family(family: str) -> str:
    if family.startswith("B") and family in {"B1"}:
        return "medium"
    if family.startswith("B"):
        return "medium"
    if family.startswith("A") or family.startswith("C"):
        return "low"
    if family.startswith("D"):
        return "medium"
    return "unknown"


def write_visual_action_plan(plan: Dict[str, Any], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output_path
