#!/usr/bin/env python3
"""
Build a unified defect report from machine-readable diagnostics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def _load_json(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    candidate = Path(path)
    if not candidate.is_file():
        return {}
    return json.loads(candidate.read_text(encoding="utf-8"))


def _stable_id(prefix: str, payload: Dict[str, Any]) -> str:
    basis = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}:{digest}"


def _normalize_severity(value: Any, default: str = "minor") -> str:
    severity = str(value or default).strip().lower()
    if severity in {"critical", "major", "minor"}:
        return severity
    return default


def _rule_defect_family(entry: Dict[str, Any]) -> str:
    defect_type = str(entry.get("type") or "").strip().lower()
    subtype = str(entry.get("subtype") or "").strip().lower()
    if defect_type == "latex error":
        return "compile_error"
    if defect_type == "overfull hbox":
        return "overfull_alignment" if subtype == "alignment" else "overfull_hbox"
    if defect_type == "underfull hbox":
        return "underfull_hbox"
    if defect_type == "undefined reference":
        return "undefined_reference"
    if defect_type == "undefined citation":
        return "undefined_citation"
    if defect_type == "float too large":
        return "float_too_large"
    if defect_type == "package warning":
        return "package_warning"
    return defect_type.replace(" ", "_") or "rule_warning"


def _normalize_rule_report(rule_report: Dict[str, Any]) -> List[Dict[str, Any]]:
    defects: List[Dict[str, Any]] = []

    for entry in rule_report.get("errors") or []:
        payload = {
            "message": entry.get("message"),
            "line": entry.get("line"),
            "context": entry.get("context"),
        }
        defects.append(
            {
                "id": _stable_id("rule", {"family": "compile_error", **payload}),
                "source": "rule_report",
                "defect_family": "compile_error",
                "severity": "critical",
                "status": "open",
                "page": None,
                "line": entry.get("line"),
                "label": None,
                "description": entry.get("message") or entry.get("type") or "LaTeX error",
                "evidence": {
                    "rule_type": entry.get("type"),
                    "context": entry.get("context"),
                },
            }
        )

    for entry in rule_report.get("warnings") or []:
        family = _rule_defect_family(entry)
        payload = {
            "family": family,
            "type": entry.get("type"),
            "subtype": entry.get("subtype"),
            "lines": entry.get("lines"),
            "reference": entry.get("reference"),
            "citation": entry.get("citation"),
            "package": entry.get("package"),
            "message": entry.get("message"),
            "context": entry.get("context"),
            "overflow_pt": entry.get("overflow_pt"),
        }
        defects.append(
            {
                "id": _stable_id("rule", payload),
                "source": "rule_report",
                "defect_family": family,
                "severity": _normalize_severity(entry.get("severity")),
                "status": "open",
                "page": None,
                "line": entry.get("line"),
                "label": entry.get("reference") or entry.get("citation"),
                "description": (
                    entry.get("message")
                    or entry.get("context")
                    or entry.get("type")
                    or family
                ),
                "evidence": {
                    "rule_type": entry.get("type"),
                    "subtype": entry.get("subtype"),
                    "lines": entry.get("lines"),
                    "overflow_pt": entry.get("overflow_pt"),
                    "package": entry.get("package"),
                },
            }
        )

    return defects


def _normalize_visual_report(visual_report: Dict[str, Any]) -> List[Dict[str, Any]]:
    defects: List[Dict[str, Any]] = []

    for finding in visual_report.get("findings") or []:
        payload = {
            "source": finding.get("source"),
            "page": finding.get("page"),
            "taxonomy_defect_id": finding.get("taxonomy_defect_id"),
            "defect_id": finding.get("defect_id"),
            "bbox": finding.get("bbox"),
            "description": finding.get("description"),
        }
        defects.append(
            {
                "id": _stable_id("visual", payload),
                "source": str(finding.get("source") or "visual_signal_report"),
                "defect_family": (
                    str(finding.get("taxonomy_defect_id") or "")
                    or str(finding.get("defect_id") or "")
                    or "visual_signal"
                ),
                "severity": _normalize_severity(finding.get("severity")),
                "status": "open",
                "page": finding.get("page"),
                "line": None,
                "label": finding.get("label"),
                "description": finding.get("description") or str(finding.get("defect_id") or "visual finding"),
                "evidence": {
                    "defect_id": finding.get("defect_id"),
                    "bbox": finding.get("bbox"),
                    "category": finding.get("category"),
                    "confidence": finding.get("confidence"),
                    "metrics": finding.get("metrics") or {},
                    "suggested_skill": finding.get("suggested_skill"),
                },
            }
        )

    for object_item in visual_report.get("priority_objects") or []:
        reason = str(object_item.get("reason") or "")
        width_ratio = object_item.get("object_width_ratio")
        if "low_width_ratio" not in reason:
            continue
        if not isinstance(width_ratio, (int, float)):
            continue
        defects.append(
            {
                "id": _stable_id(
                    "visual",
                    {
                        "source": "priority_object",
                        "page": object_item.get("page"),
                        "object_kind": object_item.get("object_kind"),
                        "bbox": object_item.get("bbox"),
                        "reason": reason,
                    },
                ),
                "source": "visual_signal_report",
                "defect_family": "B2",
                "severity": "major" if float(width_ratio) < 0.5 else "minor",
                "status": "open",
                "page": object_item.get("page"),
                "line": None,
                "label": None,
                "description": (
                    f"{object_item.get('object_kind') or 'object'} width ratio {float(width_ratio):.3f}"
                ),
                "evidence": {
                    "bbox": object_item.get("bbox"),
                    "reason": reason,
                    "object_width_ratio": float(width_ratio),
                    "caption_gap_px": object_item.get("caption_gap_px"),
                    "has_caption_pair": object_item.get("has_caption_pair"),
                },
            }
        )

    return defects


def _normalize_hygiene_report(hygiene_report: Dict[str, Any]) -> List[Dict[str, Any]]:
    defects: List[Dict[str, Any]] = []

    for finding in hygiene_report.get("findings") or []:
        payload = {
            "family": finding.get("family"),
            "line": finding.get("line"),
            "column": finding.get("column"),
            "snippet": finding.get("snippet"),
        }
        defects.append(
            {
                "id": _stable_id("hygiene", payload),
                "source": "source_hygiene_report",
                "defect_family": str(finding.get("family") or "source_pollution"),
                "severity": _normalize_severity(finding.get("severity"), default="major"),
                "status": "open",
                "page": None,
                "line": finding.get("line"),
                "label": None,
                "description": finding.get("description") or "Source hygiene issue remains",
                "evidence": {
                    "column": finding.get("column"),
                    "snippet": finding.get("snippet"),
                    "source_file": hygiene_report.get("source_file"),
                },
            }
        )

    return defects


def build_defect_report(
    rule_report_path: Optional[str],
    visual_signal_report_path: Optional[str],
    hygiene_report_path: Optional[str],
    output_path: str,
) -> Dict[str, Any]:
    rule_report = _load_json(rule_report_path)
    visual_report = _load_json(visual_signal_report_path)
    hygiene_report = _load_json(hygiene_report_path)

    defects = _normalize_rule_report(rule_report)
    defects.extend(_normalize_visual_report(visual_report))
    defects.extend(_normalize_hygiene_report(hygiene_report))

    defects.sort(
        key=lambda item: (
            {"critical": 3, "major": 2, "minor": 1}.get(str(item.get("severity") or "").lower(), 0) * -1,
            int(item.get("page") or 0),
            str(item.get("defect_family") or ""),
            str(item.get("id") or ""),
        )
    )

    by_source: Dict[str, int] = {}
    by_severity: Dict[str, int] = {}
    by_family: Dict[str, int] = {}
    for defect in defects:
        by_source[str(defect.get("source") or "unknown")] = by_source.get(str(defect.get("source") or "unknown"), 0) + 1
        by_severity[str(defect.get("severity") or "unknown")] = by_severity.get(str(defect.get("severity") or "unknown"), 0) + 1
        by_family[str(defect.get("defect_family") or "unknown")] = by_family.get(str(defect.get("defect_family") or "unknown"), 0) + 1

    report = {
        "schema_version": "1.0",
        "generated_at": datetime.now().isoformat(),
        "inputs": {
            "rule_report": rule_report_path,
            "visual_signal_report": visual_signal_report_path,
            "hygiene_report": hygiene_report_path,
        },
        "summary": {
            "total_defects": len(defects),
            "open_defects": len(defects),
            "by_source": by_source,
            "by_severity": by_severity,
            "by_family": by_family,
        },
        "defects": defects,
    }

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a unified defect report")
    parser.add_argument("--rule-report", default=None)
    parser.add_argument("--visual-signal-report", default=None)
    parser.add_argument("--hygiene-report", default=None)
    parser.add_argument("--output", default="data/defect_report.json")
    args = parser.parse_args()

    report = build_defect_report(
        rule_report_path=args.rule_report,
        visual_signal_report_path=args.visual_signal_report,
        hygiene_report_path=args.hygiene_report,
        output_path=args.output,
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
