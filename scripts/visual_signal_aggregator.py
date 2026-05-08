#!/usr/bin/env python3
"""
Aggregate structured visual signals for layout diagnosis.

This converts raw page images plus optional machine reports into a compact JSON
artifact that downstream agents can consume more reliably than bare PDFs.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    yaml = None

from cv_detector import BatchCVDetector


FALLBACK_SKILL_ROUTING = {
    "space-util-fixer": ["A1", "A2", "A3", "A4", "A5", "A6"],
    "float-optimizer": ["B1", "B2", "B3", "B4", "B5"],
    "consistency-polisher": ["C1", "C2", "C3", "C4"],
    "overflow-repair": ["D1", "D2", "D3"],
    "template-migrator": ["E1", "E2", "E3"],
    "semantic-micro-tuning": ["A1", "A2", "A3", "E2"],
}


def _load_json(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    p = Path(path)
    if not p.is_file():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _load_taxonomy(path: Path) -> Dict[str, Any]:
    if yaml is None or not path.is_file():
        return {"skill_routing": FALLBACK_SKILL_ROUTING}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {"skill_routing": FALLBACK_SKILL_ROUTING}


def _load_layout_rules(path: Path) -> Dict[str, Any]:
    if yaml is None or not path.is_file():
        return {
            "visual_signals": {
                "object_width_min_ratio": 0.75,
                "caption_gap_variance_px": 40,
                "caption_pair_max_gap_px": 120,
            }
        }
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _defect_family(defect_id: str) -> str:
    return str(defect_id).split("-", 1)[0]


def _skill_for_defect(defect_id: str, taxonomy: Dict[str, Any]) -> Optional[str]:
    family = _defect_family(defect_id)
    routing = taxonomy.get("skill_routing") or {}
    for skill, defects in routing.items():
        if family in (defects or []):
            return skill
    return None


def _severity_rank(severity: str) -> int:
    return {"critical": 3, "major": 2, "minor": 1}.get(str(severity).lower(), 0)


def _confidence_value(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip().lower()
    if text in {"high", "strong"}:
        return 0.9
    if text in {"medium", "moderate"}:
        return 0.75
    if text in {"low", "weak"}:
        return 0.5
    try:
        return float(text)
    except ValueError:
        return 0.0


def _should_promote_signal(confidence: Any, visual_rules: Dict[str, Any]) -> bool:
    threshold = float(visual_rules.get("min_confidence_to_route") or 0.75)
    return _confidence_value(confidence) >= threshold


def _count_by_category(entries: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for entry in entries:
        category = str(entry.get("category") or _defect_family(str(entry.get("taxonomy_defect_id") or ""))[:1] or "").upper()
        if not category:
            continue
        counts[category] = counts.get(category, 0) + 1
    return counts


def _pair_object_clusters(object_clusters: List[Dict[str, Any]], caption_pair_max_gap_px: int = 120) -> List[Dict[str, Any]]:
    by_page: Dict[int, List[Dict[str, Any]]] = {}
    for cluster in object_clusters:
        page = int(cluster.get("page") or 0)
        by_page.setdefault(page, []).append(cluster)

    pairings: List[Dict[str, Any]] = []
    for page, clusters in by_page.items():
        page_width = 1
        for cluster in clusters:
            bbox = cluster.get("bbox")
            if bbox:
                page_width = max(page_width, int(bbox[2]))
        captions = [c for c in clusters if c.get("kind") == "caption_like" and c.get("bbox")]
        objects = [
            c for c in clusters
            if c.get("kind") in {"figure_like", "table_like"} and c.get("bbox")
        ]
        captions.sort(key=lambda c: c["bbox"][1])
        objects.sort(key=lambda c: c["bbox"][1])

        used_caption_ids: set[int] = set()
        for obj in objects:
            ox1, oy1, ox2, oy2 = obj["bbox"]
            best = None
            best_gap = None
            for idx, cap in enumerate(captions):
                if idx in used_caption_ids:
                    continue
                _, cy1, _, cy2 = cap["bbox"]
                gap = min(abs(cy1 - oy2), abs(oy1 - cy2))
                if best_gap is None or gap < best_gap:
                    best_gap = gap
                    best = (idx, cap)
            pairings.append(
                {
                    "page": page,
                    "object_kind": obj.get("kind"),
                    "object_bbox": obj.get("bbox"),
                    "caption_bbox": best[1].get("bbox") if best and best_gap is not None and best_gap <= caption_pair_max_gap_px else None,
                    "caption_gap_px": int(best_gap) if best_gap is not None else None,
                    "object_width_ratio": float(round((ox2 - ox1) / float(max(page_width, 1)), 4)),
                }
            )
            if best and best_gap is not None and best_gap <= caption_pair_max_gap_px:
                used_caption_ids.add(best[0])
    return pairings


def _summarize_pairings_by_kind(object_pairings: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    for kind in ("figure_like", "table_like"):
        pairs = [p for p in object_pairings if p.get("object_kind") == kind]
        captioned = [p for p in pairs if p.get("caption_bbox")]
        caption_gaps = [int(p["caption_gap_px"]) for p in captioned if p.get("caption_gap_px") is not None]
        width_ratios = [float(p["object_width_ratio"]) for p in pairs if p.get("object_width_ratio") is not None]
        summary[kind] = {
            "count": len(pairs),
            "caption_paired_count": len(captioned),
            "missing_caption_pair_count": len([p for p in pairs if not p.get("caption_bbox")]),
            "caption_gap_min_px": min(caption_gaps, default=None),
            "caption_gap_max_px": max(caption_gaps, default=None),
            "object_width_ratio_min": min(width_ratios, default=None),
            "object_width_ratio_max": max(width_ratios, default=None),
        }
    return summary


def _build_priority_objects(
    object_pairings: List[Dict[str, Any]],
    page_summaries: List[Dict[str, Any]],
    visual_rules: Dict[str, Any],
) -> List[Dict[str, Any]]:
    page_status_bonus = {"critical": 20, "major": 12, "minor": 4, "clean": 0}
    page_status = {
        int(item.get("page") or 0): str(item.get("status") or "clean")
        for item in page_summaries
    }
    min_width_ratio = float(visual_rules.get("object_width_min_ratio") or 0.75)
    caption_pair_max_gap_px = int(visual_rules.get("caption_pair_max_gap_px") or 120)
    missing_caption_score = int(visual_rules.get("priority_object_missing_caption_score") or 35)
    low_width_score = int(visual_rules.get("priority_object_low_width_score") or 25)
    large_gap_score = int(visual_rules.get("priority_object_large_gap_score") or 18)
    max_items = int(visual_rules.get("priority_object_top_k") or 6)
    max_per_page = int(visual_rules.get("priority_object_max_per_page") or 2)

    candidates_by_key: Dict[tuple[Any, ...], Dict[str, Any]] = {}
    for pairing in object_pairings:
        kind = str(pairing.get("object_kind") or "")
        if kind not in {"figure_like", "table_like"}:
            continue

        score = page_status_bonus.get(page_status.get(int(pairing.get("page") or 0), "clean"), 0)
        reasons: List[str] = []
        width_ratio = float(pairing.get("object_width_ratio") or 0.0)
        caption_gap = pairing.get("caption_gap_px")
        has_caption = bool(pairing.get("caption_bbox"))

        if width_ratio < min_width_ratio:
            score += low_width_score + int(round((min_width_ratio - width_ratio) * 100))
            reasons.append(f"low_width_ratio:{width_ratio:.3f}")
        if not has_caption:
            score += missing_caption_score
            reasons.append("missing_caption_pair")
        if caption_gap is not None and int(caption_gap) > caption_pair_max_gap_px:
            score += large_gap_score + int(int(caption_gap) - caption_pair_max_gap_px)
            reasons.append(f"large_caption_gap:{int(caption_gap)}")

        if not reasons:
            continue

        entry = {
            "page": int(pairing.get("page") or 0),
            "object_kind": kind,
            "bbox": pairing.get("object_bbox"),
            "priority_score": score,
            "severity": page_status.get(int(pairing.get("page") or 0), "clean"),
            "reason": ", ".join(reasons),
            "object_width_ratio": round(width_ratio, 4),
            "caption_gap_px": int(caption_gap) if caption_gap is not None else None,
            "has_caption_pair": has_caption,
        }
        bbox = pairing.get("object_bbox") or []
        rounded_bbox = tuple(int(round(v / 4.0) * 4) for v in bbox) if bbox else ()
        dedupe_key = (
            entry["page"],
            entry["object_kind"],
            rounded_bbox,
            entry["reason"],
        )
        existing = candidates_by_key.get(dedupe_key)
        if existing is None or int(entry["priority_score"]) > int(existing.get("priority_score") or 0):
            candidates_by_key[dedupe_key] = entry

    candidates = list(candidates_by_key.values())
    candidates.sort(
        key=lambda item: (
            -int(item.get("priority_score") or 0),
            -_severity_rank(str(item.get("severity") or "")),
            int(item.get("page") or 0),
        )
    )
    selected: List[Dict[str, Any]] = []
    page_counts: Dict[int, int] = {}
    for item in candidates:
        page = int(item.get("page") or 0)
        if page_counts.get(page, 0) >= max_per_page:
            continue
        selected.append(item)
        page_counts[page] = page_counts.get(page, 0) + 1
        if len(selected) >= max_items:
            break
    return selected


def aggregate_visual_signals(
    pages_dir: str,
    output_path: str,
    taxonomy_path: str,
    column_void_report: Optional[str] = None,
    log_report: Optional[str] = None,
    crossrefs_report: Optional[str] = None,
) -> Dict[str, Any]:
    pages_path = Path(pages_dir)
    taxonomy = _load_taxonomy(Path(taxonomy_path))
    layout_rules = _load_layout_rules(Path(__file__).resolve().parent.parent / "config" / "layout_rules.yaml")
    visual_rules = layout_rules.get("visual_signals") or {}
    cv_report = BatchCVDetector(str(pages_path)).run_batch()
    column_void = _load_json(column_void_report)
    log_data = _load_json(log_report)
    crossrefs = _load_json(crossrefs_report)

    findings: List[Dict[str, Any]] = []
    hints: List[Dict[str, Any]] = []
    pages_flagged: Dict[int, List[str]] = {}
    hinted_pages: Dict[int, List[str]] = {}
    by_skill: Dict[str, int] = {}
    object_clusters: List[Dict[str, Any]] = []

    for page_result in cv_report.get("page_results") or []:
        page_num = int(page_result.get("page_number") or 0)
        for block in page_result.get("object_blocks") or []:
            object_clusters.append(
                {
                    "page": page_num,
                    "kind": block.get("kind"),
                    "bbox": block.get("bbox"),
                    "area_ratio": block.get("area_ratio"),
                    "source": "cv_page_structure",
                }
            )
        for det in page_result.get("detections") or []:
            defect_id = str(det.get("defect_id") or "")
            family = _defect_family(defect_id)
            skill = _skill_for_defect(defect_id, taxonomy)
            entry = {
                "source": "cv_detector",
                "page": page_num,
                "defect_id": defect_id,
                "taxonomy_defect_id": family,
                "category": det.get("category"),
                "severity": det.get("severity"),
                "confidence": det.get("confidence"),
                "description": det.get("description"),
                "bbox": det.get("bbox"),
                "metrics": det.get("metrics") or {},
                "suggested_skill": skill,
            }
            if _should_promote_signal(det.get("confidence"), visual_rules):
                findings.append(entry)
                pages_flagged.setdefault(page_num, []).append(family)
                if skill:
                    by_skill[skill] = by_skill.get(skill, 0) + 1
            else:
                hints.append(entry)
                hinted_pages.setdefault(page_num, []).append(family)
            if det.get("bbox"):
                object_clusters.append(
                    {
                        "page": page_num,
                        "kind": "detected_block",
                        "bbox": det.get("bbox"),
                        "taxonomy_defect_id": family,
                        "source": "cv_detector",
                        "confidence": det.get("confidence"),
                    }
                )

    for page in column_void.get("pages") or []:
        page_index = page.get("page_index")
        for cand in page.get("a5_candidates") or []:
            skill = _skill_for_defect("A5", taxonomy)
            entry = {
                "source": "column_void",
                "page": page_index,
                "defect_id": "A5-cv-candidate",
                "taxonomy_defect_id": "A5",
                "category": "A",
                "severity": "major",
                "confidence": cand.get("confidence"),
                "description": (
                    f"A5 candidate in {cand.get('column')} column, "
                    f"void_ratio={cand.get('void_ratio_of_column')}"
                ),
                "bbox": None,
                "metrics": cand,
                "suggested_skill": skill,
            }
            if page_index is not None:
                object_clusters.append(
                    {
                        "page": int(page_index),
                        "kind": "column_void_candidate",
                        "bbox": None,
                        "taxonomy_defect_id": "A5",
                        "source": "column_void",
                        "confidence": cand.get("confidence"),
                        "column": cand.get("column"),
                        "y0_frac": cand.get("y0_frac"),
                        "y1_frac": cand.get("y1_frac"),
                    }
                )
            if _should_promote_signal(cand.get("confidence"), visual_rules):
                findings.append(entry)
                if page_index is not None:
                    pages_flagged.setdefault(int(page_index), []).append("A5")
                if skill:
                    by_skill[skill] = by_skill.get(skill, 0) + 1
            else:
                hints.append(entry)
                if page_index is not None:
                    hinted_pages.setdefault(int(page_index), []).append("A5")

    findings.sort(
        key=lambda item: (
            -_severity_rank(str(item.get("severity") or "")),
            -_confidence_value(item.get("confidence")),
            int(item.get("page") or 0),
            str(item.get("taxonomy_defect_id") or ""),
        )
    )
    hints.sort(
        key=lambda item: (
            -_severity_rank(str(item.get("severity") or "")),
            -_confidence_value(item.get("confidence")),
            int(item.get("page") or 0),
            str(item.get("taxonomy_defect_id") or ""),
        )
    )

    page_summaries = []
    for page_result in cv_report.get("page_results") or []:
        page_num = int(page_result.get("page_number") or 0)
        page_findings = [f for f in findings if int(f.get("page") or 0) == page_num]
        page_hints = [f for f in hints if int(f.get("page") or 0) == page_num]
        page_metrics = page_result.get("page_metrics") or {}
        highest = "clean"
        if any(str(f.get("severity")) == "critical" for f in page_findings):
            highest = "critical"
        elif any(str(f.get("severity")) == "major" for f in page_findings):
            highest = "major"
        elif page_findings:
            highest = "minor"
        page_summaries.append(
            {
                "page": page_num,
                "status": highest,
                "finding_count": len(page_findings),
                "hint_count": len(page_hints),
                "defect_ids": sorted({str(f.get("taxonomy_defect_id")) for f in page_findings}),
                "hint_defect_ids": sorted({str(f.get("taxonomy_defect_id")) for f in page_hints}),
                "page_metrics": page_metrics,
            }
        )

    next_actions: List[str] = []
    if any(f.get("taxonomy_defect_id") == "A5" for f in findings):
        next_actions.append("Inspect A5 column-void candidates before float repairs")
    if any(f.get("taxonomy_defect_id") == "B3" for f in findings):
        next_actions.append("Review pages with clustered floats and restore text between floats")
    if any(f.get("taxonomy_defect_id") == "D1" for f in findings):
        next_actions.append("Prioritize D-class overflow fixes using log + page evidence")

    total_pages = int(cv_report.get("pages_analyzed") or 0)
    tail_findings = [
        f for f in findings
        if total_pages > 0
        and int(f.get("page") or 0) >= max(1, total_pages - 1)
        and str(f.get("taxonomy_defect_id") or "") in {"A2", "A4", "B3", "B5", "D1"}
    ]
    if tail_findings:
        next_actions.append("Review last/reference/appendix-page findings only after float/table/formula migration defects")

    figure_like_count = sum(1 for c in object_clusters if c.get("kind") == "figure_like")
    table_like_count = sum(1 for c in object_clusters if c.get("kind") == "table_like")
    caption_like_count = sum(1 for c in object_clusters if c.get("kind") == "caption_like")
    if figure_like_count > 0 and caption_like_count == 0:
        next_actions.append("Review figure-caption pairing on pages with figure-like blocks")
    if table_like_count > 0:
        next_actions.append("Review table-like block width usage and caption spacing")

    findings_by_taxonomy: Dict[str, List[Dict[str, Any]]] = {}
    for finding in findings:
        findings_by_taxonomy.setdefault(str(finding.get("taxonomy_defect_id") or ""), []).append(finding)

    cross_page_hints: List[Dict[str, Any]] = []
    for defect_id, grouped in findings_by_taxonomy.items():
        pages = sorted({int(f.get("page") or 0) for f in grouped})
        if len(pages) >= 2 and defect_id in {"A6", "B3", "B5", "C1", "C3", "C4", "D1"}:
            cross_page_hints.append(
                {
                    "taxonomy_defect_id": defect_id,
                    "pages": pages,
                    "hint": f"Cross-page review suggested for {defect_id} on pages {', '.join(str(p) for p in pages)}",
                }
            )

    if cross_page_hints:
        next_actions.append("Review cross-page consistency and recurring defect patterns before patching")

    tail_page_hints: List[Dict[str, Any]] = []
    for finding in tail_findings:
        tail_page_hints.append(
            {
                "taxonomy_defect_id": finding.get("taxonomy_defect_id"),
                "page": finding.get("page"),
                "severity": finding.get("severity"),
                "hint": "Last/reference/appendix page finding is secondary to float/table/formula migration defects",
            }
        )

    crossref_hints: List[Dict[str, Any]] = []
    for item in (crossrefs.get("distances") or []):
        line_distance = int(item.get("line_distance") or 0)
        section_distance = int(item.get("section_distance") or 0)
        severity = str(item.get("severity") or "none")
        if severity in {"major", "minor"} or line_distance >= 50 or section_distance >= 1:
            crossref_hints.append(
                {
                    "label": item.get("label"),
                    "float_type": item.get("float_type"),
                    "line_distance": line_distance,
                    "section_distance": section_distance,
                    "severity": severity,
                    "hint": (
                        f"Potential B1 source-distance issue for {item.get('label')}: "
                        f"line_distance={line_distance}, section_distance={section_distance}"
                    ),
                }
            )
    if crossref_hints:
        next_actions.append("Review crossref distance hints for potential B1 float-placement issues")

    object_pairings = _pair_object_clusters(
        object_clusters,
        caption_pair_max_gap_px=int(visual_rules.get("caption_pair_max_gap_px") or 120),
    )
    low_width_pairs = [
        p for p in object_pairings
        if p.get("object_kind") in {"figure_like", "table_like"}
        and (p.get("object_width_ratio") or 0.0) < float(visual_rules.get("object_width_min_ratio") or 0.75)
    ]
    inconsistent_caption_gaps = [
        p for p in object_pairings if p.get("caption_gap_px") is not None
    ]
    consistency_summary = {
        "paired_objects": len(object_pairings),
        "low_width_pair_count": len(low_width_pairs),
        "caption_gap_min_px": min((int(p["caption_gap_px"]) for p in inconsistent_caption_gaps), default=None),
        "caption_gap_max_px": max((int(p["caption_gap_px"]) for p in inconsistent_caption_gaps), default=None),
        "object_width_ratio_min": min((float(p["object_width_ratio"]) for p in object_pairings), default=None),
        "object_width_ratio_max": max((float(p["object_width_ratio"]) for p in object_pairings), default=None),
        "by_kind": _summarize_pairings_by_kind(object_pairings),
    }
    if low_width_pairs:
        next_actions.append("Review low-utilization figure/table blocks for potential B2 width mismatch")
    if len(inconsistent_caption_gaps) >= 2:
        gaps = [int(p["caption_gap_px"]) for p in inconsistent_caption_gaps]
        if max(gaps) - min(gaps) >= int(visual_rules.get("caption_gap_variance_px") or 40):
            next_actions.append("Review caption-to-object spacing variance for potential C4 inconsistency")

    priority_objects = _build_priority_objects(
        object_pairings=object_pairings,
        page_summaries=page_summaries,
        visual_rules=visual_rules,
    )
    if priority_objects:
        next_actions.append("Inspect top-priority figure/table objects before issuing repair prompts")

    report = {
        "schema_version": "1.1",
        "generated_at": datetime.now().isoformat(),
        "pages_dir": str(pages_path),
        "summary": {
            "pages_analyzed": cv_report.get("pages_analyzed", 0),
            "total_findings": len(findings),
            "total_hints": len(hints),
            "category_breakdown": _count_by_category(findings),
            "hint_category_breakdown": _count_by_category(hints),
            "pages_flagged_count": len(pages_flagged),
            "hinted_pages_count": len(hinted_pages),
            "highest_severity": next(
                (
                    sev
                    for sev in ("critical", "major", "minor")
                    if any(str(f.get("severity")) == sev for f in findings)
                ),
                "clean",
            ),
        },
        "machine_sources": {
            "cv_detector": {
                "page_results_count": len(cv_report.get("page_results") or []),
                "total_detections": cv_report.get("total_detections", 0),
            },
            "column_void_report": column_void_report if column_void else None,
            "log_report": log_report if log_data else None,
            "crossrefs_report": crossrefs_report if crossrefs else None,
        },
        "routing_hints": {
            "by_skill": by_skill,
            "priority_pages": sorted({s["page"] for s in page_summaries if s["status"] in {"critical", "major"}}),
            "next_actions": next_actions,
        },
        "object_clusters": object_clusters,
        "object_pairings": object_pairings,
        "priority_objects": priority_objects,
        "consistency_summary": consistency_summary,
        "cross_page_hints": cross_page_hints,
        "tail_page_hints": tail_page_hints,
        "crossref_hints": crossref_hints,
        "page_summaries": page_summaries,
        "findings": findings,
        "hints": hints,
    }

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate structured visual signals from page images")
    parser.add_argument("pages_dir", help="Directory with page_*.png files")
    parser.add_argument("--output", default="data/visual_signal_report.json")
    parser.add_argument("--taxonomy", default=str(Path(__file__).resolve().parent.parent / "config" / "vto_taxonomy.yaml"))
    parser.add_argument("--column-void-report", default=None)
    parser.add_argument("--log-report", default=None)
    parser.add_argument("--crossrefs-report", default=None)
    args = parser.parse_args()

    report = aggregate_visual_signals(
        pages_dir=args.pages_dir,
        output_path=args.output,
        taxonomy_path=args.taxonomy,
        column_void_report=args.column_void_report,
        log_report=args.log_report,
        crossrefs_report=args.crossrefs_report,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
