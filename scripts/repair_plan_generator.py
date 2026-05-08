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


def _load_json(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    p = Path(path)
    if not p.is_file():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _candidate_severity_rank(severity: str) -> int:
    return {"critical": 3, "major": 2, "minor": 1}.get(str(severity).lower(), 0)


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


def _priority_objects_with_pairing_fallback(visual_report: Dict[str, Any]) -> List[Dict[str, Any]]:
    enriched: List[Dict[str, Any]] = []
    seen_keys: set[tuple[Any, ...]] = set()

    for item in visual_report.get("priority_objects") or []:
        enriched_item = dict(item)
        key = _object_key(enriched_item)
        seen_keys.add(key)
        enriched.append(enriched_item)

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
    for item in _priority_objects_with_pairing_fallback(visual_report):
        kind = str(item.get("object_kind") or "")
        bbox = item.get("bbox") or []
        page = int(item.get("page") or 0)
        label = None
        match_strategy = None
        width_spec = None
        table_env = None
        tabcolsep = None
        best_distance = None
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
        if label is None:
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
    for item in _dedupe_matched_objects(_match_priority_objects_to_labels(visual_report, crossrefs_report)):
        reason = str(item.get("reason") or "")
        label = str(item.get("label") or "")
        if not label:
            continue
        semantic_distance = distance_lookup.get(label)
        semantic_float = float_lookup.get(label)
        semantic_home = _build_semantic_home(semantic_distance, semantic_float)
        width_ratio = _extract_ratio_from_reason(reason, "low_width_ratio")
        source_width_is_sufficient = _is_width_already_sufficient(item.get("width_spec"))
        if "low_width_ratio" in reason:
            defect_family = "B2"
        elif "caption_gap" in reason:
            defect_family = "C4"
        else:
            defect_family = "B?"
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
                "semantic_home": semantic_home,
                "ref_line": semantic_distance.get("ref_line") if semantic_distance else None,
                "float_line": semantic_distance.get("float_line") if semantic_distance else None,
                "line_distance": semantic_distance.get("line_distance") if semantic_distance else None,
                "section_distance": semantic_distance.get("section_distance") if semantic_distance else None,
                "reference_source": semantic_distance.get("reference_source") if semantic_distance else None,
                "reference_text": semantic_distance.get("reference_text") if semantic_distance else None,
                "float_section": semantic_float.get("section") if semantic_float else None,
                "evidence_sources": (
                    ["visual_signal_report", "crossrefs_report"]
                    if semantic_home
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

    b3_findings = [
        finding
        for finding in visual_report.get("findings") or []
        if str(finding.get("taxonomy_defect_id") or "") in {"B3", "B5"}
    ]
    if not b3_findings:
        return candidates

    source_labels: List[str] = []
    for flt in crossrefs_report.get("floats") or []:
        label = str(flt.get("label") or "")
        if label and label not in source_labels:
            source_labels.append(label)
    if len(source_labels) < 2:
        return candidates

    pages = [int(finding.get("page") or 0) for finding in b3_findings if int(finding.get("page") or 0) > 0]
    for index, start in enumerate(range(0, len(source_labels), 4)):
        labels = source_labels[start:start + 4]
        if len(labels) < 2:
            continue
        page = pages[min(index, len(pages) - 1)] if pages else 0
        candidates.append(
            {
                "candidate_type": "source_order_cluster",
                "page": page,
                "target": {
                    "labels": labels,
                },
                "defect_family": "B3",
                "priority_score": 78,
                "severity": "major",
                "proposed_action": "decluster_float_sequence",
                "rationale": "visual B3 float clustering with source-order fallback labels",
                "evidence_sources": ["visual_signal_report", "crossrefs_report"],
            }
        )
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
        if taxonomy_id in {"A2", "A4"} and page == current_pages:
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

        if taxonomy_id == "A2" and page == current_pages:
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
                    "priority_score": 90,
                    "severity": "major",
                    "proposed_action": "compress_trailing_whitespace",
                    "rationale": str(finding.get("description") or "last-page trailing whitespace"),
                    "description": str(finding.get("description") or "last-page trailing whitespace"),
                    "whitespace_ratio": whitespace_ratio,
                    "current_pages": current_pages,
                    "evidence_sources": ["visual_signal_report", "last_page_guard"],
                }
            )

        if taxonomy_id == "A4" and page == current_pages:
            height_difference = (metrics.get("height_diff_ratio") or 0.0)
            candidates.append(
                {
                    "candidate_type": "visual_tail",
                    "page": page,
                    "target": {"scope": "column_balance"},
                    "defect_family": "A4",
                    "priority_score": 82,
                    "severity": str(finding.get("severity") or "major"),
                    "proposed_action": "balance_final_columns",
                    "rationale": str(finding.get("description") or "last-page column imbalance"),
                    "description": str(finding.get("description") or "last-page column imbalance"),
                    "height_difference": height_difference,
                    "current_pages": current_pages,
                    "evidence_sources": ["visual_signal_report", "last_page_guard"],
                }
            )

    return candidates


def _build_crossref_candidates(crossrefs_report: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    distance_lookup = _build_distance_lookup(crossrefs_report)
    float_lookup = _build_float_lookup(crossrefs_report)
    for item in crossrefs_report.get("distances") or []:
        severity = str(item.get("severity") or "none")
        if severity not in {"major", "minor"}:
            continue
        label = str(item.get("label") or "")
        semantic_home = _build_semantic_home(item, float_lookup.get(label))
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
                "evidence_sources": ["crossrefs_report"],
            }
        )
        width_spec = item.get("width_spec")
        if item.get("float_type") == "figure" and width_spec and not _is_width_already_sufficient(width_spec):
            factor_match = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)\\(?:linewidth|columnwidth|textwidth)\s*$", str(width_spec))
            try:
                width_factor = float(factor_match.group(1)) if factor_match else 0.8
            except ValueError:
                width_factor = 0.8
            candidates.append(
                {
                    "candidate_type": "source_anchor",
                    "page": None,
                    "target": {
                        "label": label,
                        "float_type": item.get("float_type"),
                    },
                    "defect_family": "B2",
                    "priority_score": 70 if width_factor <= 0.65 else 62,
                    "severity": "major" if width_factor <= 0.65 else "minor",
                    "proposed_action": "adjust_float_width",
                    "rationale": f"source_width_spec={width_spec}",
                    "semantic_home": semantic_home,
                    "ref_line": semantic_distance.get("ref_line") if semantic_distance else None,
                    "float_line": semantic_distance.get("float_line") if semantic_distance else None,
                    "line_distance": semantic_distance.get("line_distance") if semantic_distance else None,
                    "section_distance": semantic_distance.get("section_distance") if semantic_distance else None,
                    "reference_source": semantic_distance.get("reference_source") if semantic_distance else None,
                    "reference_text": semantic_distance.get("reference_text") if semantic_distance else None,
                    "float_section": item.get("section"),
                    "source_width_spec": width_spec,
                    "evidence_sources": ["crossrefs_report"],
                }
            )
    for item in crossrefs_report.get("floats") or []:
        label = str(item.get("label") or "")
        if not label:
            continue
        width_spec = item.get("width_spec")
        if item.get("float_type") == "figure" and width_spec and not _is_width_already_sufficient(width_spec):
            factor_match = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)\\(?:linewidth|columnwidth|textwidth)\s*$", str(width_spec))
            try:
                width_factor = float(factor_match.group(1)) if factor_match else 0.8
            except ValueError:
                width_factor = 0.8
            semantic_home = _build_semantic_home(distance_lookup.get(label), item)
            candidates.append(
                {
                    "candidate_type": "source_anchor",
                    "page": None,
                    "target": {
                        "label": label,
                        "float_type": item.get("float_type"),
                    },
                    "defect_family": "B2",
                    "priority_score": 70 if width_factor <= 0.65 else 62,
                    "severity": "major" if width_factor <= 0.65 else "minor",
                    "proposed_action": "adjust_float_width",
                    "rationale": f"source_width_spec={width_spec}",
                    "semantic_home": semantic_home,
                    "ref_line": (distance_lookup.get(label) or {}).get("ref_line"),
                    "float_line": (distance_lookup.get(label) or {}).get("float_line"),
                    "line_distance": (distance_lookup.get(label) or {}).get("line_distance"),
                    "section_distance": (distance_lookup.get(label) or {}).get("section_distance"),
                    "reference_source": (distance_lookup.get(label) or {}).get("reference_source"),
                    "reference_text": (distance_lookup.get(label) or {}).get("reference_text"),
                    "float_section": item.get("section"),
                    "source_width_spec": width_spec,
                    "evidence_sources": ["crossrefs_report"],
                }
            )
        float_position = str(item.get("float_position") or "")
        if float_position not in {"p", "!p", "b", "!b"}:
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
                "evidence_sources": ["crossrefs_report"],
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


def _build_log_candidates(rule_report: Dict[str, Any]) -> List[Dict[str, Any]]:
    summary = rule_report.get("summary") or {}
    candidates: List[Dict[str, Any]] = []
    if int(summary.get("underfull_hbox_total") or 0) > 0:
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
                "evidence_sources": ["rule_report"],
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
    crossref_candidates = _build_crossref_candidates(crossrefs)
    semantic_band_candidates = _build_semantic_band_candidates(
        crossrefs_report=crossrefs,
        existing_candidates=object_candidates + b3_candidates + crossref_candidates,
    )
    space_candidates = _build_space_candidates(visual_report=visual_report, target_pages=target_pages)
    visual_space_candidates = _build_visual_space_candidates(
        visual_report=visual_report,
        target_pages=target_pages,
    )
    log_candidates = _build_log_candidates(rule_report_data)

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
    candidates.sort(
        key=lambda item: (
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
