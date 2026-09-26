#!/usr/bin/env python3
"""
Generate a structured repair plan from machine-readable diagnostics.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from runtime_repair_risk import annotate_repair_candidates


def _load_json(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    p = Path(path)
    if not p.is_file():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _candidate_severity_rank(severity: str) -> int:
    return {"critical": 3, "major": 2, "minor": 1}.get(str(severity).lower(), 0)


def _active_visual_families(visual_report: Dict[str, Any]) -> set[str]:
    return {
        str(finding.get("taxonomy_defect_id") or "")
        for finding in visual_report.get("findings") or []
    }


def _has_visual_float_placement_pressure(visual_report: Dict[str, Any]) -> bool:
    return bool(_active_visual_families(visual_report) & {"B1", "B3", "B5"})


def _has_visual_b2_width_candidate(candidates: List[Dict[str, Any]]) -> bool:
    return any(_is_targetable_b2_width_candidate(candidate) for candidate in candidates)


def _summarize_b2_width_candidates(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "total": 0,
        "by_subtype": {},
        "by_object_kind": {},
        "labels": [],
    }
    for candidate in candidates:
        if str(candidate.get("defect_family") or "") != "B2":
            continue
        subtype = str(candidate.get("visual_width_subtype") or "")
        if subtype not in {"overflow_width", "underfilled_width"}:
            continue
        target = candidate.get("target") or {}
        object_kind = str(target.get("object_kind") or "unknown")
        label = str(target.get("label") or "")
        summary["total"] += 1
        summary["by_subtype"][subtype] = int(summary["by_subtype"].get(subtype) or 0) + 1
        summary["by_object_kind"][object_kind] = int(summary["by_object_kind"].get(object_kind) or 0) + 1
        if label:
            summary["labels"].append(label)
    return summary


def _is_targetable_b2_width_candidate(candidate: Dict[str, Any]) -> bool:
    if str(candidate.get("defect_family") or "") != "B2":
        return False
    if str(candidate.get("visual_width_subtype") or "") not in {"overflow_width", "underfilled_width"}:
        return False
    target = candidate.get("target") or {}
    label = str(target.get("label") or "")
    if not label:
        return False
    object_kind = str(target.get("object_kind") or "")
    return object_kind in {"figure_like", "table_like"} or label.startswith(("fig:", "tab:"))


def _b2_width_untargetable_reason(candidate: Dict[str, Any]) -> Optional[str]:
    if str(candidate.get("defect_family") or "") != "B2":
        return None
    if str(candidate.get("visual_width_subtype") or "") not in {"overflow_width", "underfilled_width"}:
        return None
    target = candidate.get("target") or {}
    label = str(target.get("label") or "")
    if not label:
        return "missing_label"
    object_kind = str(target.get("object_kind") or "")
    if object_kind not in {"figure_like", "table_like"} and not label.startswith(("fig:", "tab:")):
        return "unsupported_label_or_object_kind"
    return None


def _summarize_targetable_b2_width_candidates(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    targetable = [candidate for candidate in candidates if _is_targetable_b2_width_candidate(candidate)]
    return _summarize_b2_width_candidates(targetable)


def _summarize_untargetable_b2_width_candidates(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary = _summarize_b2_width_candidates(
        [
            candidate
            for candidate in candidates
            if _b2_width_untargetable_reason(candidate) is not None
        ]
    )
    by_reason: Dict[str, int] = {}
    for candidate in candidates:
        reason = _b2_width_untargetable_reason(candidate)
        if reason is None:
            continue
        by_reason[reason] = int(by_reason.get(reason) or 0) + 1
    summary["by_reason"] = by_reason
    return summary


def _summarize_visual_b2_width_findings(visual_report: Dict[str, Any]) -> Dict[str, Any]:
    existing = (visual_report.get("summary") or {}).get("b2_width_findings")
    if isinstance(existing, dict):
        summary = {
            "total": int(existing.get("total") or 0),
            "by_subtype": existing.get("by_subtype") or {},
            "by_object_kind": existing.get("by_object_kind") or {},
            "pages": existing.get("pages") or [],
            "finding_ids": existing.get("finding_ids") or [],
        }
        if summary["total"] > 0 and (not summary["pages"] or not summary["finding_ids"]):
            fallback = _summarize_visual_b2_width_findings({"findings": visual_report.get("findings") or []})
            if not summary["pages"]:
                summary["pages"] = fallback.get("pages") or []
            if not summary["finding_ids"]:
                summary["finding_ids"] = fallback.get("finding_ids") or []
        return summary

    summary: Dict[str, Any] = {
        "total": 0,
        "by_subtype": {},
        "by_object_kind": {},
        "pages": [],
        "finding_ids": [],
    }
    pages: set[int] = set()
    for finding in visual_report.get("findings") or []:
        if str(finding.get("taxonomy_defect_id") or "") != "B2":
            continue
        metrics = finding.get("metrics") or {}
        subtype = str(metrics.get("subtype") or "")
        if subtype not in {"overflow_width", "underfilled_width"}:
            continue
        object_kind = str(metrics.get("object_kind") or "unknown")
        summary["total"] += 1
        summary["by_subtype"][subtype] = int(summary["by_subtype"].get(subtype) or 0) + 1
        summary["by_object_kind"][object_kind] = int(summary["by_object_kind"].get(object_kind) or 0) + 1
        if finding.get("page") is not None:
            pages.add(int(finding.get("page") or 0))
        finding_id = str(finding.get("defect_id") or finding.get("id") or "")
        if finding_id:
            summary["finding_ids"].append(finding_id)
    summary["pages"] = sorted(pages)
    return summary


def _rounded_pdf_bbox(value: Any) -> tuple[float, ...]:
    if not isinstance(value, list):
        return ()
    rounded: List[float] = []
    for item in value:
        if not isinstance(item, (int, float)):
            return ()
        rounded.append(round(float(item), 2))
    return tuple(rounded)


def _b2_width_finding_entries(visual_report: Dict[str, Any]) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for finding in visual_report.get("findings") or []:
        if str(finding.get("taxonomy_defect_id") or "") != "B2":
            continue
        metrics = finding.get("metrics") or {}
        subtype = str(metrics.get("subtype") or "")
        if subtype not in {"overflow_width", "underfilled_width"}:
            continue
        entries.append(
            {
                "finding_id": str(finding.get("defect_id") or finding.get("id") or ""),
                "page": int(finding.get("page") or 0) if finding.get("page") is not None else None,
                "subtype": subtype,
                "object_kind": str(metrics.get("object_kind") or "unknown"),
                "pdf_bbox": _rounded_pdf_bbox(metrics.get("pdf_bbox")),
            }
        )
    return entries


def _b2_width_entry_key(entry: Dict[str, Any]) -> tuple[Any, ...]:
    return (
        entry.get("page"),
        entry.get("subtype"),
        entry.get("object_kind"),
        entry.get("pdf_bbox") or (),
    )


def _b2_width_candidate_key(candidate: Dict[str, Any]) -> tuple[Any, ...]:
    target = candidate.get("target") or {}
    return (
        int(candidate.get("page") or 0) if candidate.get("page") is not None else None,
        str(candidate.get("visual_width_subtype") or ""),
        str(target.get("object_kind") or "unknown"),
        _rounded_pdf_bbox(candidate.get("visual_pdf_bbox")),
    )


def _summarize_b2_width_conversion(
    visual_report: Dict[str, Any],
    candidates: List[Dict[str, Any]],
) -> Dict[str, Any]:
    entries = _b2_width_finding_entries(visual_report)
    remaining = list(entries)
    for candidate in candidates:
        if str(candidate.get("defect_family") or "") != "B2":
            continue
        if str(candidate.get("visual_width_subtype") or "") not in {"overflow_width", "underfilled_width"}:
            continue
        key = _b2_width_candidate_key(candidate)
        for index, entry in enumerate(remaining):
            if _b2_width_entry_key(entry) == key:
                remaining.pop(index)
                break

    if entries:
        return {
            "unmatched_count": len(remaining),
            "unmatched_pages": sorted(
                {int(entry["page"]) for entry in remaining if entry.get("page") is not None}
            ),
            "unmatched_finding_ids": [
                entry["finding_id"] for entry in remaining if entry.get("finding_id")
            ],
        }

    b2_width_findings = _summarize_visual_b2_width_findings(visual_report)
    b2_width_candidates = _summarize_b2_width_candidates(candidates)
    unmatched_count = max(
        0,
        int(b2_width_findings.get("total") or 0) - int(b2_width_candidates.get("total") or 0),
    )
    return {
        "unmatched_count": unmatched_count,
        "unmatched_pages": (b2_width_findings.get("pages") or []) if unmatched_count > 0 else [],
        "unmatched_finding_ids": (b2_width_findings.get("finding_ids") or []) if unmatched_count > 0 else [],
    }


def _is_urgent_overflow_candidate(candidate: Dict[str, Any]) -> bool:
    if str(candidate.get("defect_family") or "") not in {"D1", "D2", "D3"}:
        return False
    overflow_amount = float(candidate.get("overflow_amount") or 0.0)
    priority_score = int(candidate.get("priority_score") or 0)
    return priority_score >= 90 or overflow_amount >= 5.0


def _is_width_already_sufficient(width_spec: Any) -> bool:
    spec = str(width_spec or "").replace(" ", "")
    if not spec:
        return False
    if spec in {r"\linewidth", r"\columnwidth", r"\textwidth"}:
        return True
    if spec.endswith((r"\linewidth", r"\columnwidth", r"\textwidth")):
        try:
            factor = float(spec.split("\\", 1)[0])
            return factor >= 0.95
        except ValueError:
            return False
    return False


def _width_spec_ratio(width_spec: Any) -> Optional[float]:
    spec = str(width_spec or "").replace(" ", "")
    if not spec or spec == "none":
        return None
    if spec in {r"\linewidth", r"\columnwidth", r"\textwidth"}:
        return 1.0
    match = re.fullmatch(r"([0-9]*\.?[0-9]+)\\(?:linewidth|columnwidth|textwidth)", spec)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _parse_mm_value(value: Any) -> Optional[float]:
    match = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)mm\s*$", str(value or ""))
    if not match:
        return None
    return float(match.group(1))


def _extract_ratio_from_reason(reason: str, key: str) -> Optional[float]:
    match = re.search(rf"{re.escape(key)}:([0-9]+(?:\.[0-9]+)?)", str(reason or ""))
    if not match:
        return None
    return float(match.group(1))


def _current_page_count(visual_report: Dict[str, Any]) -> int:
    summary = visual_report.get("summary") or {}
    pages_analyzed = int(summary.get("pages_analyzed") or 0)
    if pages_analyzed > 0:
        return pages_analyzed
    page_numbers = [
        int(item.get("page") or 0)
        for item in (visual_report.get("page_summaries") or [])
        if int(item.get("page") or 0) > 0
    ]
    return max(page_numbers, default=0)


def _object_key(item: Dict[str, Any]) -> tuple[Any, ...]:
    bbox = item.get("bbox") or item.get("object_bbox") or []
    rounded_bbox = tuple(int(round(v / 4.0) * 4) for v in bbox) if bbox else ()
    return (
        int(item.get("page") or 0),
        str(item.get("object_kind") or ""),
        rounded_bbox,
    )


def _dedupe_matched_objects(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: Dict[tuple[Any, ...], Dict[str, Any]] = {}
    for item in items:
        target_label = str(item.get("label") or "")
        key = (
            ("label", target_label)
            if target_label
            else (
                "page_bbox",
                *_object_key(item),
            )
        )
        existing = deduped.get(key)
        if existing is None or int(item.get("priority_score") or 0) > int(existing.get("priority_score") or 0):
            deduped[key] = item
    return list(deduped.values())


def _pairing_priority_score(kind: str, width_ratio: Optional[float], caption_gap_px: Optional[int]) -> int:
    if width_ratio is not None:
        if width_ratio < 0.38:
            return 96
        if width_ratio < 0.40:
            return 93
        if width_ratio < 0.50:
            return 86
        if width_ratio < 0.65:
            return 72
    if caption_gap_px is not None:
        return 68 if caption_gap_px >= 18 else 58
    return 50 if kind == "table_like" else 45


def _pairing_reason(pairing: Dict[str, Any]) -> str:
    width_ratio = pairing.get("object_width_ratio")
    caption_gap = pairing.get("caption_gap_px")
    if isinstance(width_ratio, (int, float)) and float(width_ratio) < 0.70:
        return f"low_width_ratio:{float(width_ratio):.3f}"
    if caption_gap is not None:
        return f"caption_gap:{int(caption_gap)}"
    return "pairing_candidate"


def _pairing_severity(width_ratio: Optional[float], caption_gap_px: Optional[int]) -> str:
    if width_ratio is not None:
        if width_ratio < 0.50:
            return "major"
        if width_ratio < 0.65:
            return "minor"
    if caption_gap_px is not None and caption_gap_px >= 18:
        return "major"
    return "minor"


def _visual_b2_finding_priority_items(visual_report: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for finding in visual_report.get("findings") or []:
        if str(finding.get("taxonomy_defect_id") or "") != "B2":
            continue
        metrics = finding.get("metrics") or {}
        subtype = str(metrics.get("subtype") or "")
        if subtype not in {"overflow_width", "underfilled_width"}:
            continue
        bbox = finding.get("bbox") or []
        width_ratio_raw = metrics.get("object_width_ratio")
        width_ratio = float(width_ratio_raw) if isinstance(width_ratio_raw, (int, float)) else None
        overflow_pt = float(metrics.get("overflow_pt") or 0.0)
        if subtype == "overflow_width":
            priority_score = 180 + int(min(30, max(0.0, overflow_pt)))
            reason = f"overflow_width:{overflow_pt:.3f}pt"
        else:
            ratio_for_score = width_ratio if width_ratio is not None else 1.0
            priority_score = 100 + int(round(max(0.0, 0.85 - ratio_for_score) * 50))
            reason = f"underfilled_width:{ratio_for_score:.3f}"
        items.append(
            {
                "page": int(finding.get("page") or 0),
                "object_kind": str(metrics.get("object_kind") or "figure_like"),
                "bbox": bbox,
                "priority_score": priority_score,
                "severity": str(finding.get("severity") or "major"),
                "reason": reason,
                "object_width_ratio": width_ratio,
                "object_width_page_ratio": metrics.get("object_width_page_ratio"),
                "width_context": metrics.get("width_context"),
                "visual_width_subtype": subtype,
                "overflow_pt": overflow_pt if subtype == "overflow_width" else None,
                "pdf_bbox": metrics.get("pdf_bbox"),
                "source": str(finding.get("source") or "visual_signal_report"),
                "finding_defect_id": finding.get("defect_id"),
                "confidence": finding.get("confidence"),
            }
        )
    return items


def _priority_objects_with_pairing_fallback(visual_report: Dict[str, Any]) -> List[Dict[str, Any]]:
    enriched: List[Dict[str, Any]] = []
    seen_keys: set[tuple[Any, ...]] = set()

    for item in visual_report.get("priority_objects") or []:
        enriched_item = dict(item)
        key = _object_key(enriched_item)
        seen_keys.add(key)
        enriched.append(enriched_item)

    for item in _visual_b2_finding_priority_items(visual_report):
        key = _object_key(item)
        existing = next((candidate for candidate in enriched if _object_key(candidate) == key), None)
        if existing is not None:
            if int(item.get("priority_score") or 0) > int(existing.get("priority_score") or 0):
                existing.update(item)
            continue
        seen_keys.add(key)
        enriched.append(item)

    for pairing in visual_report.get("object_pairings") or []:
        key = _object_key(pairing)
        if key in seen_keys:
            continue
        width_ratio_raw = pairing.get("object_width_ratio")
        width_ratio = float(width_ratio_raw) if isinstance(width_ratio_raw, (int, float)) else None
        caption_gap_raw = pairing.get("caption_gap_px")
        caption_gap = int(caption_gap_raw) if isinstance(caption_gap_raw, (int, float)) else None
        reason = _pairing_reason(pairing)
        object_kind = str(pairing.get("object_kind") or "")
        if object_kind not in {"table_like", "figure_like"}:
            continue
        if "low_width_ratio" not in reason and "caption_gap" not in reason:
            continue
        enriched.append(
            {
                "page": int(pairing.get("page") or 0),
                "object_kind": object_kind,
                "bbox": pairing.get("object_bbox") or [],
                "priority_score": _pairing_priority_score(object_kind, width_ratio, caption_gap),
                "severity": _pairing_severity(width_ratio, caption_gap),
                "reason": reason,
                "object_width_ratio": width_ratio,
                "caption_gap_px": caption_gap,
                "has_caption_pair": pairing.get("caption_bbox") is not None,
                "source": "object_pairings_fallback",
            }
        )
        seen_keys.add(key)

    return enriched


def _build_distance_lookup(crossrefs_report: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    lookup: Dict[str, Dict[str, Any]] = {}
    for item in crossrefs_report.get("distances") or []:
        label = str(item.get("label") or "")
        if label:
            lookup[label] = item
    return lookup


def _build_float_lookup(crossrefs_report: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    lookup: Dict[str, Dict[str, Any]] = {}
    for item in crossrefs_report.get("floats") or []:
        label = str(item.get("label") or "")
        if label:
            lookup[label] = item
    return lookup


def _build_semantic_home(
    distance_item: Optional[Dict[str, Any]],
    float_item: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not distance_item and not float_item:
        return None

    semantic_home: Dict[str, Any] = {}
    if distance_item:
        semantic_home.update(
            {
                "ref_line": distance_item.get("ref_line"),
                "float_line": distance_item.get("float_line"),
                "line_distance": distance_item.get("line_distance"),
                "section_distance": distance_item.get("section_distance"),
                "reference_source": distance_item.get("reference_source"),
                "reference_text": distance_item.get("reference_text"),
                "ref_before_float": distance_item.get("ref_before_float"),
            }
        )
    if float_item:
        semantic_home.update(
            {
                "float_position": float_item.get("float_position"),
                "float_section": float_item.get("section"),
                "float_type": float_item.get("float_type"),
            }
        )

    semantic_home = {key: value for key, value in semantic_home.items() if value is not None}
    return semantic_home or None


def _match_priority_objects_to_labels(
    visual_report: Dict[str, Any],
    crossrefs_report: Dict[str, Any],
) -> List[Dict[str, Any]]:
    floats = crossrefs_report.get("floats") or []
    by_kind: Dict[str, List[Dict[str, Any]]] = {"figure_like": [], "table_like": []}
    for flt in floats:
        float_type = str(flt.get("float_type") or "")
        kind = "figure_like" if float_type == "figure" else "table_like" if float_type == "table" else None
        if kind:
            by_kind[kind].append(flt)

    ordered_objects_by_kind: Dict[str, List[Dict[str, Any]]] = {"figure_like": [], "table_like": []}
    for pairing in visual_report.get("object_pairings") or []:
        kind = str(pairing.get("object_kind") or "")
        if kind not in ordered_objects_by_kind:
            continue
        bbox = pairing.get("object_bbox") or []
        ordered_objects_by_kind[kind].append(
            {
                "page": int(pairing.get("page") or 0),
                "bbox": bbox,
            }
        )

    for kind, items in ordered_objects_by_kind.items():
        items.sort(key=lambda item: (item["page"], item["bbox"][1] if item["bbox"] else 0, item["bbox"][0] if item["bbox"] else 0))
        source_floats = by_kind.get(kind) or []
        source_floats.sort(key=lambda item: (int(item.get("line_number") or 0), int(item.get("char_offset") or 0)))
        for idx, obj in enumerate(items):
            if idx < len(source_floats):
                obj["label"] = source_floats[idx].get("label")
                obj["match_strategy"] = "ordered_object_pairing"
                obj["width_spec"] = source_floats[idx].get("width_spec")
                obj["table_env"] = source_floats[idx].get("table_env")
                obj["tabcolsep"] = source_floats[idx].get("tabcolsep")

    if not any(ordered_objects_by_kind.values()):
        offsets = {"figure_like": 0, "table_like": 0}
        matched: List[Dict[str, Any]] = []
        for item in _priority_objects_with_pairing_fallback(visual_report):
            kind = str(item.get("object_kind") or "")
            enriched = dict(item)
            source_floats = by_kind.get(kind) or []
            idx = offsets.get(kind, 0)
            if idx < len(source_floats):
                enriched["label"] = source_floats[idx].get("label")
                enriched["match_strategy"] = "source_order_proxy"
                enriched["width_spec"] = source_floats[idx].get("width_spec")
                enriched["table_env"] = source_floats[idx].get("table_env")
                enriched["tabcolsep"] = source_floats[idx].get("tabcolsep")
                offsets[kind] = idx + 1
            matched.append(enriched)
        return matched

    matched: List[Dict[str, Any]] = []
    width_matched_labels: set[str] = set()
    source_order_offsets = {"figure_like": 0, "table_like": 0}
    for item in _priority_objects_with_pairing_fallback(visual_report):
        kind = str(item.get("object_kind") or "")
        bbox = item.get("bbox") or []
        page = int(item.get("page") or 0)
        label = None
        match_strategy = None
        width_spec = None
        table_env = None
        tabcolsep = None

        visual_width_subtype = str(item.get("visual_width_subtype") or "")
        visual_width_ratio = item.get("object_width_ratio")
        if kind == "figure_like" and visual_width_subtype in {"overflow_width", "underfilled_width"}:
            width_candidates: List[tuple[float, int, Dict[str, Any]]] = []
            for source_index, source_float in enumerate(by_kind.get(kind) or []):
                source_label = str(source_float.get("label") or "")
                if source_label in width_matched_labels:
                    continue
                ratio = _width_spec_ratio(source_float.get("width_spec"))
                if ratio is None:
                    continue
                if visual_width_subtype == "overflow_width" and ratio <= 1.02:
                    continue
                if visual_width_subtype == "underfilled_width" and ratio >= 0.85:
                    continue
                if isinstance(visual_width_ratio, (int, float)):
                    distance = abs(float(visual_width_ratio) - ratio)
                else:
                    distance = 0.0
                width_candidates.append((distance, source_index, source_float))
            if width_candidates:
                _, _, best_float = min(width_candidates, key=lambda value: (value[0], value[1]))
                label = best_float.get("label")
                match_strategy = "source_width_visual_b2"
                width_spec = best_float.get("width_spec")
                table_env = best_float.get("table_env")
                tabcolsep = best_float.get("tabcolsep")
                if label:
                    width_matched_labels.add(str(label))

        if label is None and visual_width_subtype in {"overflow_width", "underfilled_width"} and not ordered_objects_by_kind.get(kind):
            source_floats = by_kind.get(kind) or []
            idx = source_order_offsets.get(kind, 0)
            while idx < len(source_floats) and str(source_floats[idx].get("label") or "") in width_matched_labels:
                idx += 1
            if idx < len(source_floats):
                source_float = source_floats[idx]
                label = source_float.get("label")
                match_strategy = "source_order_visual_b2"
                width_spec = source_float.get("width_spec")
                table_env = source_float.get("table_env")
                tabcolsep = source_float.get("tabcolsep")
                source_order_offsets[kind] = idx + 1
                if label:
                    width_matched_labels.add(str(label))

        best_distance = None
        if label is None:
            for obj in ordered_objects_by_kind.get(kind) or []:
                if int(obj.get("page") or 0) != page:
                    continue
                obox = obj.get("bbox") or []
                if not bbox or not obox:
                    continue
                distance = sum(abs(int(a) - int(b)) for a, b in zip(bbox, obox))
                if best_distance is None or distance < best_distance:
                    best_distance = distance
                    label = obj.get("label")
                    match_strategy = obj.get("match_strategy")
                    width_spec = obj.get("width_spec")
                    table_env = obj.get("table_env")
                    tabcolsep = obj.get("tabcolsep")
        if label is None and visual_width_subtype not in {"overflow_width", "underfilled_width"}:
            for obj in ordered_objects_by_kind.get(kind) or []:
                label = obj.get("label")
                match_strategy = obj.get("match_strategy")
                width_spec = obj.get("width_spec")
                table_env = obj.get("table_env")
                tabcolsep = obj.get("tabcolsep")
                if label:
                    break
        enriched = dict(item)
        if label:
            enriched["label"] = label
            enriched["match_strategy"] = match_strategy
            enriched["width_spec"] = width_spec
            enriched["table_env"] = table_env
            enriched["tabcolsep"] = tabcolsep
        matched.append(enriched)
    return matched


def _build_object_candidates(visual_report: Dict[str, Any], crossrefs_report: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    distance_lookup = _build_distance_lookup(crossrefs_report)
    float_lookup = _build_float_lookup(crossrefs_report)
    active_visual_families = _active_visual_families(visual_report)
    for item in _dedupe_matched_objects(_match_priority_objects_to_labels(visual_report, crossrefs_report)):
        reason = str(item.get("reason") or "")
        label = str(item.get("label") or "")
        if not label:
            continue
        semantic_distance = distance_lookup.get(label)
        semantic_float = float_lookup.get(label)
        semantic_home = _build_semantic_home(semantic_distance, semantic_float)
        width_ratio = _extract_ratio_from_reason(reason, "low_width_ratio")
        visual_width_subtype = str(item.get("visual_width_subtype") or "")
        if visual_width_subtype == "underfilled_width":
            width_ratio = _extract_ratio_from_reason(reason, "underfilled_width") or width_ratio
        source_width_is_sufficient = _is_width_already_sufficient(item.get("width_spec"))
        if visual_width_subtype in {"overflow_width", "underfilled_width"}:
            defect_family = "B2"
        elif "caption_gap" in reason or "missing_caption_pair" in reason:
            if "C4" not in active_visual_families:
                continue
            defect_family = "C4"
        else:
            continue
        tabcolsep_mm = _parse_mm_value(item.get("tabcolsep"))
        action = (
            "normalize_float_position_near_reference"
            if defect_family == "B1"
            else "adjust_float_width"
            if defect_family == "B2"
            else "normalize_caption_spacing"
        )
        candidates.append(
            {
                "candidate_type": "object",
                "page": int(item.get("page") or 0),
                "target": {
                    "object_kind": item.get("object_kind"),
                    "bbox": item.get("bbox"),
                    "label": item.get("label"),
                },
                "defect_family": defect_family,
                "priority_score": int(item.get("priority_score") or 0),
                "severity": str(item.get("severity") or "major"),
                "proposed_action": action,
                "rationale": reason,
                "match_strategy": item.get("match_strategy"),
                "source_width_spec": item.get("width_spec"),
                "source_table_env": item.get("table_env"),
                "source_tabcolsep": item.get("tabcolsep"),
                "visual_width_subtype": visual_width_subtype or None,
                "visual_object_width_ratio": width_ratio,
                "visual_object_width_page_ratio": item.get("object_width_page_ratio"),
                "visual_width_context": item.get("width_context"),
                "visual_overflow_pt": item.get("overflow_pt"),
                "visual_pdf_bbox": item.get("pdf_bbox"),
                "visual_confidence": item.get("confidence"),
                "semantic_home": semantic_home,
                "ref_line": semantic_distance.get("ref_line") if semantic_distance else None,
                "float_line": semantic_distance.get("float_line") if semantic_distance else None,
                "line_distance": semantic_distance.get("line_distance") if semantic_distance else None,
                "section_distance": semantic_distance.get("section_distance") if semantic_distance else None,
                "reference_source": semantic_distance.get("reference_source") if semantic_distance else None,
                "reference_text": semantic_distance.get("reference_text") if semantic_distance else None,
                "float_section": semantic_float.get("section") if semantic_float else None,
                "evidence_sources": (
                    [str(item.get("source") or "visual_signal_report"), "visual_signal_report", "crossrefs_report"]
                    if visual_width_subtype and semantic_home
                    else ["visual_signal_report", "crossrefs_report"]
                    if semantic_home
                    else [str(item.get("source") or "visual_signal_report"), "visual_signal_report"]
                    if visual_width_subtype
                    else ["visual_signal_report"]
                ),
            }
        )
    return candidates


def _build_b3_candidates(visual_report: Dict[str, Any], crossrefs_report: Dict[str, Any]) -> List[Dict[str, Any]]:
    matched_objects = _dedupe_matched_objects(_match_priority_objects_to_labels(visual_report, crossrefs_report))
    labels_by_page: Dict[int, List[str]] = {}
    for item in matched_objects:
        label = str(item.get("label") or "")
        if not label:
            continue
        page = int(item.get("page") or 0)
        labels = labels_by_page.setdefault(page, [])
        if label not in labels:
            labels.append(label)

    candidates: List[Dict[str, Any]] = []
    for finding in visual_report.get("findings") or []:
        taxonomy_id = str(finding.get("taxonomy_defect_id") or "")
        if taxonomy_id not in {"B3", "B5"}:
            continue
        page = int(finding.get("page") or 0)
        labels = labels_by_page.get(page) or []
        if len(labels) < 2:
            continue
        candidates.append(
            {
                "candidate_type": "page_cluster",
                "page": page,
                "target": {
                    "labels": labels[:4],
                },
                "defect_family": "B3",
                "priority_score": 84 if taxonomy_id == "B5" else 78 if str(finding.get("severity") or "") == "major" else 60,
                "severity": str(finding.get("severity") or "major"),
                "proposed_action": "decluster_float_sequence",
                "rationale": str(finding.get("description") or finding.get("defect_id") or f"visual {taxonomy_id} float clustering"),
                "evidence_sources": ["visual_signal_report"],
            }
        )
    if candidates:
        return candidates

    return candidates


def _build_tail_float_packing_candidates(
    visual_report: Dict[str, Any],
    crossrefs_report: Dict[str, Any],
    target_pages: Optional[int] = None,
) -> List[Dict[str, Any]]:
    current_pages = _current_page_count(visual_report)
    if current_pages <= 0:
        return []

    has_tail_pressure = False
    tail_reasons: List[str] = []
    for finding in visual_report.get("findings") or []:
        taxonomy_id = str(finding.get("taxonomy_defect_id") or "")
        page = int(finding.get("page") or 0)
        if target_pages is not None and target_pages > 0 and page > target_pages:
            continue
        severity = str(finding.get("severity") or "")
        has_page_budget_pressure = target_pages is not None and target_pages > 0
        has_major_tail_pressure = severity in {"major", "critical"}
        explicit_float_tail_pressure = taxonomy_id in {"B3", "B5"} and has_major_tail_pressure
        budget_tail_space_pressure = taxonomy_id in {"A2", "A4"} and has_page_budget_pressure
        if page == current_pages and (explicit_float_tail_pressure or budget_tail_space_pressure):
            has_tail_pressure = True
            description = str(finding.get("description") or taxonomy_id)
            if description not in tail_reasons:
                tail_reasons.append(description)

    if not has_tail_pressure:
        return []

    labels: List[str] = []
    for flt in crossrefs_report.get("floats") or []:
        label = str(flt.get("label") or "")
        if label and label not in labels:
            labels.append(label)
    if len(labels) < 2:
        return []

    return [
        {
            "candidate_type": "tail_float_packing",
            "page": current_pages,
            "target": {
                "labels": labels[-4:],
                "scope": "late_page_float_compaction",
            },
            "defect_family": "B3",
            "priority_score": 91,
            "severity": "major",
            "proposed_action": "pack_late_floats_before_endmatter",
            "rationale": "; ".join(tail_reasons) or "last-page tail float pressure",
            "current_pages": current_pages,
            "evidence_sources": ["visual_signal_report", "crossrefs_report", "last_page_guard"],
        }
    ]


def _build_visual_space_candidates(
    visual_report: Dict[str, Any],
    target_pages: Optional[int] = None,
) -> List[Dict[str, Any]]:
    current_pages = _current_page_count(visual_report)
    if current_pages <= 0:
        return []

    candidates: List[Dict[str, Any]] = []
    for finding in visual_report.get("findings") or []:
        taxonomy_id = str(finding.get("taxonomy_defect_id") or "")
        page = int(finding.get("page") or 0)
        if target_pages is not None and target_pages > 0 and page > target_pages:
            continue
        metrics = finding.get("metrics") or {}
        severity = str(finding.get("severity") or "minor")
        has_page_budget_pressure = target_pages is not None and target_pages > 0
        should_plan_tail_repair = has_page_budget_pressure or severity in {"major", "critical"}

        if taxonomy_id == "A2" and page == current_pages and should_plan_tail_repair:
            whitespace_ratio = (
                metrics.get("bottom_whitespace_ratio")
                or metrics.get("whitespace_ratio")
                or 0.0
            )
            candidates.append(
                {
                    "candidate_type": "visual_tail",
                    "page": page,
                    "target": {"scope": "trailing_whitespace"},
                    "defect_family": "A2",
                    "priority_score": 90 if severity in {"major", "critical"} else 64,
                    "severity": severity,
                    "proposed_action": "compress_trailing_whitespace",
                    "rationale": str(finding.get("description") or "last-page trailing whitespace"),
                    "description": str(finding.get("description") or "last-page trailing whitespace"),
                    "whitespace_ratio": whitespace_ratio,
                    "current_pages": current_pages,
                    "evidence_sources": ["visual_signal_report", "last_page_guard"],
                }
            )

        if taxonomy_id == "A4" and page == current_pages and should_plan_tail_repair:
            height_difference = (metrics.get("height_diff_ratio") or 0.0)
            candidates.append(
                {
                    "candidate_type": "visual_tail",
                    "page": page,
                    "target": {"scope": "column_balance"},
                    "defect_family": "A4",
                    "priority_score": 82 if severity in {"major", "critical"} else 58,
                    "severity": severity,
                    "proposed_action": "balance_final_columns",
                    "rationale": str(finding.get("description") or "last-page column imbalance"),
                    "description": str(finding.get("description") or "last-page column imbalance"),
                    "height_difference": height_difference,
                    "current_pages": current_pages,
                    "evidence_sources": ["visual_signal_report", "last_page_guard"],
                }
            )

    return candidates


def _build_crossref_candidates(
    crossrefs_report: Dict[str, Any],
    visual_report: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    distance_lookup = _build_distance_lookup(crossrefs_report)
    float_lookup = _build_float_lookup(crossrefs_report)
    visual_report = visual_report or {}
    allow_source_b1 = _has_visual_float_placement_pressure(visual_report)
    for item in crossrefs_report.get("distances") or []:
        severity = str(item.get("severity") or "none")
        label = str(item.get("label") or "")
        semantic_home = _build_semantic_home(item, float_lookup.get(label))
        if allow_source_b1 and severity in {"major", "minor"}:
            candidates.append(
                {
                    "candidate_type": "source_anchor",
                    "page": None,
                    "target": {
                        "label": item.get("label"),
                        "float_type": item.get("float_type"),
                    },
                    "defect_family": "B1",
                    "priority_score": 80 if severity == "major" else 55,
                    "severity": severity,
                    "proposed_action": "move_float_closer_to_first_reference",
                    "rationale": (
                        f"crossref distance line={int(item.get('line_distance') or 0)}, "
                        f"section={int(item.get('section_distance') or 0)}"
                    ),
                    "semantic_home": semantic_home,
                    "ref_line": item.get("ref_line"),
                    "float_line": item.get("float_line"),
                    "line_distance": item.get("line_distance"),
                    "section_distance": item.get("section_distance"),
                    "reference_source": item.get("reference_source"),
                    "reference_text": item.get("reference_text"),
                    "float_section": (float_lookup.get(label) or {}).get("section"),
                    "evidence_sources": ["crossrefs_report", "visual_signal_report"],
                }
            )
    for item in crossrefs_report.get("floats") or []:
        label = str(item.get("label") or "")
        if not label:
            continue
        float_position = str(item.get("float_position") or "")
        if not allow_source_b1 or float_position not in {"p", "!p", "b", "!b"}:
            continue
        semantic_distance = distance_lookup.get(label)
        semantic_home = _build_semantic_home(semantic_distance, item)
        candidates.append(
            {
                "candidate_type": "source_anchor",
                "page": None,
                "target": {
                    "label": label,
                    "float_type": item.get("float_type"),
                },
                "defect_family": "B1",
                "priority_score": 72 if "p" in float_position else 66,
                "severity": "major" if "p" in float_position else "minor",
                "proposed_action": "normalize_float_position_near_reference",
                "rationale": (
                    f"float_position={float_position}"
                    + (
                        f", semantic_home={semantic_distance.get('reference_text')}"
                        if semantic_distance and semantic_distance.get("reference_text")
                        else ""
                    )
                ),
                "semantic_home": semantic_home,
                "ref_line": semantic_distance.get("ref_line") if semantic_distance else None,
                "float_line": semantic_distance.get("float_line") if semantic_distance else None,
                "line_distance": semantic_distance.get("line_distance") if semantic_distance else None,
                "section_distance": semantic_distance.get("section_distance") if semantic_distance else None,
                "reference_source": semantic_distance.get("reference_source") if semantic_distance else None,
                "reference_text": semantic_distance.get("reference_text") if semantic_distance else None,
                "float_section": item.get("section"),
                "source_float_position": float_position,
                "evidence_sources": ["crossrefs_report", "visual_signal_report"],
            }
        )
    return candidates


def _build_semantic_band_candidates(
    crossrefs_report: Dict[str, Any],
    existing_candidates: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    distance_lookup = _build_distance_lookup(crossrefs_report)
    float_lookup = _build_float_lookup(crossrefs_report)
    existing_b1_labels = {
        str(((candidate.get("target") or {}).get("label")) or "")
        for candidate in existing_candidates
        if str(candidate.get("defect_family") or "") == "B1"
    }

    candidates: List[Dict[str, Any]] = []
    added_labels: set[str] = set()
    strong_anchors = [
        candidate
        for candidate in existing_candidates
        if str(candidate.get("defect_family") or "") == "B1"
        and str(candidate.get("candidate_type") or "") == "source_anchor"
        and (
            int(candidate.get("line_distance") or 0) >= 40
            or int(candidate.get("section_distance") or 0) >= 1
        )
    ]

    for anchor in strong_anchors:
        anchor_label = str(((anchor.get("target") or {}).get("label")) or "")
        anchor_ref_line = anchor.get("ref_line")
        if not anchor_label or anchor_ref_line is None:
            continue

        for label, item in distance_lookup.items():
            if label == anchor_label or label in existing_b1_labels or label in added_labels:
                continue
            ref_line = item.get("ref_line")
            if ref_line is None:
                continue
            line_distance = int(item.get("line_distance") or 0)
            section_distance = int(item.get("section_distance") or 0)
            if section_distance == 0 and line_distance <= 6:
                continue
            delta = abs(int(ref_line) - int(anchor_ref_line))
            if delta > 70:
                continue

            semantic_home = _build_semantic_home(item, float_lookup.get(label))
            candidates.append(
                {
                    "candidate_type": "semantic_band",
                    "page": None,
                    "target": {
                        "label": label,
                        "float_type": item.get("float_type"),
                    },
                    "defect_family": "B1",
                    "priority_score": max(74, 79 - delta // 12),
                    "severity": "major" if delta <= 36 else "minor",
                    "proposed_action": "normalize_float_position_near_reference",
                    "rationale": (
                        f"semantic_band anchor={anchor_label}, "
                        f"anchor_ref_line={int(anchor_ref_line)}, "
                        f"companion_ref_line={int(ref_line)}, "
                        f"delta={delta}"
                    ),
                    "semantic_home": semantic_home,
                    "ref_line": item.get("ref_line"),
                    "float_line": item.get("float_line"),
                    "line_distance": item.get("line_distance"),
                    "section_distance": item.get("section_distance"),
                    "reference_source": item.get("reference_source"),
                    "reference_text": item.get("reference_text"),
                    "float_section": (float_lookup.get(label) or {}).get("section"),
                    "semantic_band": {
                        "anchor_label": anchor_label,
                        "anchor_ref_line": anchor_ref_line,
                        "delta_ref_line": delta,
                    },
                    "evidence_sources": ["crossrefs_report", "semantic_band"],
                }
            )
            added_labels.add(label)

    return candidates


def _build_space_candidates(
    visual_report: Dict[str, Any],
    target_pages: Optional[int],
) -> List[Dict[str, Any]]:
    current_pages = _current_page_count(visual_report)
    if current_pages <= 0 or target_pages is None or target_pages <= 0:
        return []

    candidates: List[Dict[str, Any]] = []
    if current_pages > target_pages:
        overflow_pages = current_pages - target_pages
        candidates.append(
            {
                "candidate_type": "global",
                "page": current_pages,
                "target": {"scope": "page_budget"},
                "defect_family": "A3",
                "priority_score": 92,
                "severity": "critical" if overflow_pages >= 2 else "major",
                "proposed_action": "reduce_page_count",
                "rationale": f"current_pages={current_pages}, target_pages={target_pages}",
                "current_pages": current_pages,
                "target_pages": target_pages,
                "evidence_sources": ["visual_signal_report"],
            }
        )
        candidates.append(
            {
                "candidate_type": "global",
                "page": current_pages,
                "target": {"scope": "trailing_whitespace"},
                "defect_family": "A2",
                "priority_score": 86,
                "severity": "major",
                "proposed_action": "compress_trailing_whitespace",
                "rationale": (
                    f"current_pages={current_pages}, target_pages={target_pages}, "
                    "probe_last_page_for_budget_recovery"
                ),
                "whitespace_ratio": 0.3,
                "evidence_sources": ["visual_signal_report", "page_budget"],
            }
        )
    elif current_pages < target_pages:
        candidates.append(
            {
                "candidate_type": "global",
                "page": current_pages,
                "target": {"scope": "page_budget"},
                "defect_family": "A3",
                "priority_score": 82,
                "severity": "major",
                "proposed_action": "expand_page_count",
                "rationale": f"current_pages={current_pages}, target_pages={target_pages}",
                "current_pages": current_pages,
                "target_pages": target_pages,
                "evidence_sources": ["visual_signal_report"],
            }
        )
    return candidates


def _build_log_candidates(
    rule_report: Dict[str, Any],
    visual_report: Optional[Dict[str, Any]] = None,
    target_pages: Optional[int] = None,
) -> List[Dict[str, Any]]:
    summary = rule_report.get("summary") or {}
    visual_report = visual_report or {}
    active_visual_families = _active_visual_families(visual_report)
    has_page_budget_pressure = target_pages is not None and target_pages > 0
    candidates: List[Dict[str, Any]] = []
    if (
        int(summary.get("underfull_hbox_total") or 0) > 0
        and (has_page_budget_pressure or bool(active_visual_families & {"A1", "C1", "C2", "C3", "C4"}))
    ):
        candidates.append(
            {
                "candidate_type": "global",
                "page": None,
                "target": {"scope": "paragraph_spacing"},
                "defect_family": "A/C",
                "priority_score": 40,
                "severity": "minor",
                "proposed_action": "review_paragraph_spacing_and_looseness",
                "rationale": f"underfull_hbox_total={int(summary.get('underfull_hbox_total') or 0)}",
                "evidence_sources": ["rule_report", "visual_signal_report"] if active_visual_families else ["rule_report"],
            }
        )
    if int(summary.get("overfull_hbox_total") or 0) > 0:
        detailed_candidates = 0
        for warning in rule_report.get("overfull_hbox") or []:
            subtype = str(warning.get("subtype") or "paragraph")
            overflow_pt = float(warning.get("overflow_pt") or 0.0)
            lines = str(warning.get("lines") or "").strip() or None
            line_number = int(lines.split("--", 1)[0]) if lines else None
            candidates.append(
                {
                    "candidate_type": "log_warning",
                    "page": None,
                    "target": {
                        "scope": "table_overflow" if subtype == "alignment" else "overflow",
                    },
                    "defect_family": "D1",
                    "priority_score": min(95, 80 + int(max(overflow_pt, 0.0) // 5)),
                    "severity": str(warning.get("severity") or "major"),
                    "proposed_action": "repair_overfull_boxes",
                    "rationale": (
                        f"overfull_hbox subtype={subtype}, "
                        f"overflow_pt={overflow_pt:.2f}, "
                        f"lines={lines or 'unknown'}"
                    ),
                    "description": str(warning.get("context") or "").strip(),
                    "overflow_amount": overflow_pt,
                    "line_number": line_number,
                    "lines": lines,
                    "subtype": subtype,
                    "evidence_sources": ["rule_report"],
                }
            )
            detailed_candidates += 1

        if detailed_candidates == 0:
            candidates.append(
                {
                    "candidate_type": "global",
                    "page": None,
                    "target": {"scope": "overflow"},
                    "defect_family": "D1",
                    "priority_score": 85,
                    "severity": "major",
                    "proposed_action": "repair_overfull_boxes",
                    "rationale": f"overfull_hbox_total={int(summary.get('overfull_hbox_total') or 0)}",
                    "evidence_sources": ["rule_report"],
                }
            )
    return candidates


def generate_repair_plan(
    visual_signal_report: str,
    output_path: str,
    crossrefs_report: Optional[str] = None,
    rule_report: Optional[str] = None,
    target_pages: Optional[int] = None,
) -> Dict[str, Any]:
    visual_report = _load_json(visual_signal_report)
    crossrefs = _load_json(crossrefs_report)
    rule_report_data = _load_json(rule_report)

    object_candidates = _build_object_candidates(visual_report, crossrefs)
    b3_candidates = _build_b3_candidates(visual_report, crossrefs)
    tail_float_candidates = _build_tail_float_packing_candidates(
        visual_report,
        crossrefs,
        target_pages=target_pages,
    )
    crossref_candidates = _build_crossref_candidates(crossrefs, visual_report=visual_report)
    semantic_band_candidates = _build_semantic_band_candidates(
        crossrefs_report=crossrefs,
        existing_candidates=object_candidates + b3_candidates + crossref_candidates,
    )
    space_candidates = _build_space_candidates(visual_report=visual_report, target_pages=target_pages)
    visual_space_candidates = _build_visual_space_candidates(
        visual_report=visual_report,
        target_pages=target_pages,
    )
    log_candidates = _build_log_candidates(
        rule_report_data,
        visual_report=visual_report,
        target_pages=target_pages,
    )

    candidates = (
        object_candidates
        + tail_float_candidates
        + b3_candidates
        + crossref_candidates
        + semantic_band_candidates
        + space_candidates
        + visual_space_candidates
        + log_candidates
    )
    candidates = annotate_repair_candidates(candidates)
    has_visual_b2_width_candidate = _has_visual_b2_width_candidate(candidates)
    b2_width_findings = _summarize_visual_b2_width_findings(visual_report)
    b2_width_candidates = _summarize_b2_width_candidates(candidates)
    b2_width_targetable_candidates = _summarize_targetable_b2_width_candidates(candidates)
    b2_width_untargetable_candidates = _summarize_untargetable_b2_width_candidates(candidates)
    b2_width_conversion = _summarize_b2_width_conversion(visual_report, candidates)
    candidates.sort(
        key=lambda item: (
            0 if _is_urgent_overflow_candidate(item) and not has_visual_b2_width_candidate else 1,
            -int(item.get("priority_score") or 0),
            -_candidate_severity_rank(str(item.get("severity") or "")),
            int(item.get("page") or 0) if item.get("page") is not None else 10**6,
            str((item.get("target") or {}).get("label") or ""),
        )
    )

    plan = {
        "schema_version": "1.0",
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "total_candidates": len(candidates),
            "object_candidates": len([c for c in candidates if c["candidate_type"] == "object"]),
            "source_anchor_candidates": len([c for c in candidates if c["candidate_type"] == "source_anchor"]),
            "semantic_band_candidates": len([c for c in candidates if c["candidate_type"] == "semantic_band"]),
            "global_candidates": len([c for c in candidates if c["candidate_type"] == "global"]),
            "b2_width_findings": b2_width_findings,
            "b2_width_candidates": b2_width_candidates,
            "b2_width_targetable_candidates": b2_width_targetable_candidates,
            "b2_width_untargetable_candidates": b2_width_untargetable_candidates,
            "b2_width_unmatched_findings": b2_width_conversion["unmatched_count"],
            "b2_width_unmatched_pages": b2_width_conversion["unmatched_pages"],
            "b2_width_unmatched_finding_ids": b2_width_conversion["unmatched_finding_ids"],
        },
        "candidates": candidates,
    }

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a structured repair plan")
    parser.add_argument("visual_signal_report")
    parser.add_argument("--output", default="data/repair_plan.json")
    parser.add_argument("--crossrefs-report", default=None)
    parser.add_argument("--rule-report", default=None)
    parser.add_argument("--target-pages", type=int, default=None)
    args = parser.parse_args()

    plan = generate_repair_plan(
        visual_signal_report=args.visual_signal_report,
        output_path=args.output,
        crossrefs_report=args.crossrefs_report,
        rule_report=args.rule_report,
        target_pages=args.target_pages,
    )
    print(json.dumps(plan, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
