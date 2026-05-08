#!/usr/bin/env python3
"""
Executable gatekeeper decision enforcer for PaperFit.

This script turns gatekeeper policy into deterministic checks so DONE cannot be
emitted when hard requirements are unmet.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml
except ImportError:
    yaml = None

from state_schema import normalize_state, validate_state
from transactional_patch import atomic_write_text


DEFAULT_GATEKEEPER_RULES: Dict[str, Any] = {
    "require_visual_signal_report_when_rendered": True,
    "require_visual_summary_when_rendered": True,
    "block_repair_execution_statuses": ["failed", "error"],
    "category_thresholds": {
        "normal": {
            "A": ["critical", "major"],
            "B": ["critical", "major"],
            "C": [],
            "D": ["critical", "major"],
            "E": ["critical", "major", "minor", "unknown"],
            "SYSTEM": ["critical", "major", "unknown"],
        },
        "strict": {
            "A": ["critical", "major", "minor", "unknown"],
            "B": ["critical", "major", "minor", "unknown"],
            "C": ["critical", "major", "minor", "unknown"],
            "D": ["critical", "major", "minor", "unknown"],
            "E": ["critical", "major", "minor", "unknown"],
            "SYSTEM": ["critical", "major", "minor", "unknown"],
        },
    },
}


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_layout_rules() -> Dict[str, Any]:
    config_path = Path(__file__).resolve().parent.parent / "config" / "layout_rules.yaml"
    if yaml is None or not config_path.is_file():
        return {"gatekeeper": DEFAULT_GATEKEEPER_RULES}
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    data.setdefault("gatekeeper", DEFAULT_GATEKEEPER_RULES)
    return data


def _extract_defects(defects_payload: Any) -> List[Dict[str, Any]]:
    if isinstance(defects_payload, dict):
        if isinstance(defects_payload.get("defects"), list):
            return defects_payload["defects"]
        if isinstance(defects_payload.get("items"), list):
            return defects_payload["items"]
    elif isinstance(defects_payload, list):
        return defects_payload
    return []


def _normalize_severity(value: Any) -> str:
    sev = str(value or "unknown").strip().lower()
    if sev in {"blocker", "critical"}:
        return "critical"
    if sev in {"high", "major"}:
        return "major"
    if sev in {"low", "minor"}:
        return "minor"
    return "unknown"


def _category_for_defect(defect: Dict[str, Any]) -> str:
    family = str(
        defect.get("defect_family")
        or defect.get("taxonomy_defect_id")
        or defect.get("defect_id")
        or ""
    ).strip()
    match = re.match(r"^([A-E])(?:\d+|[-_].*)?$", family, re.IGNORECASE)
    if match:
        return match.group(1).upper()

    family_key = family.lower()
    if family_key in {"overfull_hbox", "overfull_alignment", "underfull_hbox"}:
        return "D"
    if family_key in {"float_too_large"}:
        return "B"
    if family_key in {
        "placeholder_token",
        "debug_token",
        "unresolved_marker",
        "replacement_character",
        "suspicious_math_payload",
        "title_stray_text",
    }:
        return "C"
    return "SYSTEM"


def _empty_severity_counts() -> Dict[str, int]:
    return {"critical": 0, "major": 0, "minor": 0, "unknown": 0}


def _summarize_defects(state: Dict[str, Any], defects_payload: Any) -> Dict[str, Any]:
    open_defects: List[Dict[str, Any]] = []
    by_severity = _empty_severity_counts()
    by_category = {
        "A": _empty_severity_counts(),
        "B": _empty_severity_counts(),
        "C": _empty_severity_counts(),
        "D": _empty_severity_counts(),
        "E": _empty_severity_counts(),
        "SYSTEM": _empty_severity_counts(),
    }

    task = state.get("task") or {}
    target_pages = int(task.get("target_pages") or 0)
    page_budget_scope = str(task.get("page_budget_scope") or "")
    ignore_endmatter_tail_space = (
        page_budget_scope == "main_body"
        and target_pages > 0
        and str(task.get("column_type") or "") == "double"
    )

    for defect in _extract_defects(defects_payload):
        status = str(defect.get("status", "open")).lower()
        if status in {"resolved", "fixed", "done", "closed"}:
            continue
        severity = _normalize_severity(defect.get("severity"))
        category = _category_for_defect(defect)
        family = (
            defect.get("defect_family")
            or defect.get("taxonomy_defect_id")
            or defect.get("defect_id")
        )
        if (
            ignore_endmatter_tail_space
            and str(family or "") in {"A2", "A4"}
            and int(defect.get("page") or 0) > target_pages
        ):
            continue
        by_severity[severity] += 1
        by_category[category][severity] += 1
        open_defects.append(
            {
                "id": defect.get("id") or defect.get("defect_id"),
                "defect_family": family,
                "category": category,
                "severity": severity,
                "page": defect.get("page"),
                "label": defect.get("label"),
                "description": defect.get("description"),
            }
        )

    if not open_defects:
        remaining = int(((state.get("defect_summary") or {}).get("remaining")) or 0)
        if remaining > 0:
            by_severity["unknown"] = remaining
            by_category["SYSTEM"]["unknown"] = remaining
            open_defects = [
                {
                    "id": None,
                    "defect_family": "unclassified_remaining",
                    "category": "SYSTEM",
                    "severity": "unknown",
                    "page": None,
                    "label": None,
                    "description": (
                        "remaining defects present in state summary without "
                        "explicit defect_report entries"
                    ),
                }
                for _ in range(remaining)
            ]

    return {
        "open_defects": open_defects,
        "by_severity": by_severity,
        "by_category": by_category,
    }


def _validate_semantic_report(path: Path) -> Tuple[bool, str]:
    if not path.exists():
        return False, "semantic_patch_report missing"
    data = _load_json(path)
    required = {"summary", "edits", "integrity"}
    if not required.issubset(set(data.keys())):
        return False, "semantic_patch_report missing required fields"
    return True, "ok"


def _category_passes(
    category: str,
    counts: Dict[str, int],
    strict_mode: bool,
    rules: Dict[str, Any],
) -> Dict[str, Any]:
    thresholds = (
        (rules.get("category_thresholds") or {}).get("strict" if strict_mode else "normal")
        or {}
    )
    blocked_severities = list(thresholds.get(category) or [])
    blocking_count = sum(int(counts.get(sev) or 0) for sev in blocked_severities)
    return {
        "pass": blocking_count == 0,
        "counts": counts,
        "blocked_severities": blocked_severities,
        "blocking_count": blocking_count,
    }


def _load_rule_report_from_state(state: Dict[str, Any]) -> Dict[str, Any]:
    artifacts = state.get("artifacts") or {}
    candidate = artifacts.get("rule_report")
    if not candidate:
        return {}
    path = Path(candidate)
    if not path.is_absolute():
        path = Path.cwd() / path
    return _load_json(path)


def make_decision(
    state: Dict[str, Any],
    defects_payload: Dict[str, Any],
    strict_mode: bool,
    semantic_report_path: Path | None,
) -> Dict[str, Any]:
    rules = (_load_layout_rules().get("gatekeeper") or DEFAULT_GATEKEEPER_RULES)
    checks: Dict[str, Dict[str, Any]] = {}
    reasons: List[str] = []
    decision = "DONE"

    compile_success = bool(state.get("compile_success"))
    checks["compile_success"] = {"pass": compile_success, "value": state.get("compile_success")}
    if not compile_success:
        decision = "CONTINUE"
        reasons.append("compile_success=false")

    pages_rendered = bool(state.get("page_images_rendered"))
    checks["page_images_rendered"] = {
        "pass": pages_rendered,
        "value": state.get("page_images_rendered"),
    }
    if not pages_rendered:
        decision = "CONTINUE"
        reasons.append("page_images_rendered=false")

    artifacts = state.get("artifacts") or {}
    visual_summary = state.get("visual_signals_summary") or {}
    require_visual_report = bool(rules.get("require_visual_signal_report_when_rendered", True))
    require_visual_summary = bool(rules.get("require_visual_summary_when_rendered", True))
    visual_report_present = bool(artifacts.get("visual_signal_report"))
    visual_summary_present = bool(visual_summary.get("updated_at"))
    visual_pass = (not require_visual_report or visual_report_present) and (
        not require_visual_summary or visual_summary_present
    )
    checks["visual_inspection"] = {
        "pass": visual_pass if pages_rendered else False,
        "report_present": visual_report_present,
        "summary_present": visual_summary_present,
        "priority_pages": list(visual_summary.get("priority_pages") or []),
        "cross_page_hint_count": len(visual_summary.get("cross_page_hints") or []),
        "crossref_hint_count": len(visual_summary.get("crossref_hints") or []),
    }
    if pages_rendered and not visual_pass:
        decision = "CONTINUE"
        reasons.append("visual signal evidence missing/incomplete")

    semantic_summary = state.get("semantic_budget_summary")
    semantic_used = semantic_summary is not None or artifacts.get("semantic_patch_report") is not None
    if semantic_used:
        if semantic_report_path is None:
            candidate = artifacts.get("semantic_patch_report")
            semantic_report_path = Path(candidate) if candidate else None
        ok = False
        msg = "semantic report path unavailable"
        if semantic_report_path is not None:
            ok, msg = _validate_semantic_report(semantic_report_path)
        summary_ok = isinstance(semantic_summary, dict)
        checks["semantic_report"] = {
            "pass": ok and summary_ok,
            "detail": msg,
            "summary_present": summary_ok,
            "direction": (semantic_summary or {}).get("direction")
            if isinstance(semantic_summary, dict)
            else None,
        }
        if not ok:
            decision = "CONTINUE"
            reasons.append("semantic report missing/invalid")
        elif not summary_ok:
            decision = "CONTINUE"
            reasons.append("semantic budget summary missing")
    else:
        checks["semantic_report"] = {"pass": True, "detail": "not_used"}

    repair_summary = state.get("repair_execution_summary") or {}
    repair_status = str(repair_summary.get("status") or "").lower()
    repair_used = bool(
        artifacts.get("repair_execution_report")
        or repair_summary.get("updated_at")
        or repair_status
    )
    blocked_repair_statuses = {
        str(s).lower() for s in (rules.get("block_repair_execution_statuses") or [])
    }
    repair_pass = (not repair_used) or repair_status not in blocked_repair_statuses
    checks["repair_execution"] = {
        "pass": repair_pass,
        "used": repair_used,
        "status": repair_summary.get("status"),
        "applied_count": int(repair_summary.get("applied_count") or 0),
        "selected_candidates": list(repair_summary.get("selected_candidates") or []),
    }
    if repair_used and not repair_pass:
        decision = "CONTINUE"
        reasons.append(f"repair execution status={repair_status}")

    rule_report = _load_rule_report_from_state(state)
    rule_summary = rule_report.get("summary") or {}
    undefined_references = int(rule_summary.get("undefined_references") or 0)
    undefined_citations = int(rule_summary.get("citation_issues") or 0)
    checks["reference_integrity"] = {
        "pass": undefined_references == 0 and undefined_citations == 0,
        "undefined_references": undefined_references,
        "undefined_citations": undefined_citations,
    }
    if undefined_references > 0 or undefined_citations > 0:
        if decision != "BLOCKED":
            decision = "CONTINUE"
        reasons.append("undefined references/citations remain")

    ci = state.get("content_integrity") or {}
    violation = ci.get("violation_level")
    checks["content_integrity"] = {
        "pass": violation in (None, 0, 1),
        "violation_level": violation,
        "action_taken": ci.get("action_taken"),
    }
    if violation == 3:
        decision = "BLOCKED"
        reasons.append("content_integrity level 3 violation")
    elif violation == 2 and decision != "BLOCKED":
        decision = "CONTINUE"
        reasons.append("content_integrity level 2 violation")
    elif semantic_used and violation is None and decision != "BLOCKED":
        decision = "CONTINUE"
        reasons.append("content_integrity missing for semantic round")

    defect_summary = _summarize_defects(state, defects_payload)
    defect_counts = defect_summary["by_severity"]
    checks["defects"] = {
        "pass": True,
        "counts": defect_counts,
        "open_total": len(defect_summary["open_defects"]),
    }

    alignment_blockers = sum(
        1
        for defect in defect_summary["open_defects"]
        if str(defect.get("defect_family") or "") == "overfull_alignment"
        and str(defect.get("severity") or "") in {"critical", "major"}
    )
    checks["log_alignment_overflow"] = {
        "pass": alignment_blockers == 0,
        "blocking_count": alignment_blockers,
    }
    if alignment_blockers > 0:
        if decision != "BLOCKED":
            decision = "CONTINUE"
        reasons.append("major alignment overflow remains")

    for category in ("A", "B", "C", "D", "E", "SYSTEM"):
        result = _category_passes(
            category=category,
            counts=defect_summary["by_category"][category],
            strict_mode=strict_mode,
            rules=rules,
        )
        checks[f"category_{category}"] = result
        if not result["pass"]:
            checks["defects"]["pass"] = False
            if decision != "BLOCKED":
                decision = "CONTINUE"
            reasons.append(f"category {category} blocking defects remaining")

    if checks["defects"]["pass"]:
        checks["defects"]["pass"] = alignment_blockers == 0

    return {
        "gatekeeper": "quality-gatekeeper-enforcer",
        "timestamp": datetime.now().isoformat(),
        "decision": decision,
        "strict_mode": strict_mode,
        "checks": checks,
        "reasons": reasons,
        "defect_counts": defect_counts,
        "defect_counts_by_category": defect_summary["by_category"],
        "remaining_defects": defect_summary["open_defects"][:20],
        "gates_passed": {
            "compile": checks["compile_success"]["pass"],
            "page_images": checks["page_images_rendered"]["pass"],
            "visual_inspection": checks["visual_inspection"]["pass"],
            "category_A": checks["category_A"]["pass"],
            "category_B": checks["category_B"]["pass"],
            "category_C": checks["category_C"]["pass"],
            "category_D": checks["category_D"]["pass"],
            "category_E": checks["category_E"]["pass"],
            "content_integrity": checks["content_integrity"]["pass"],
            "repair_execution": checks["repair_execution"]["pass"],
            "semantic_report": checks["semantic_report"]["pass"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="PaperFit gatekeeper decision enforcer")
    parser.add_argument("--state", default="data/state.json", help="Path to state.json")
    parser.add_argument("--defects", default=None, help="Path to defects json")
    parser.add_argument("--strict", action="store_true", help="Strict mode: minor defects block DONE")
    parser.add_argument(
        "--semantic-report", default=None, help="Path to semantic_patch_report.json"
    )
    parser.add_argument(
        "--output",
        default="data/gatekeeper_decision.json",
        help="Output decision json path",
    )
    parser.add_argument(
        "--update-state", action="store_true", help="Write decision fields back into state.json"
    )
    args = parser.parse_args()

    state_path = Path(args.state)
    state = normalize_state(_load_json(state_path))
    defects_path: Optional[Path] = Path(args.defects) if args.defects else None
    if defects_path is None:
        candidate = ((state.get("artifacts") or {}).get("defect_report"))
        if candidate:
            defects_path = Path(candidate)
    defects_payload = _load_json(defects_path) if defects_path else {}
    decision = make_decision(
        state=state,
        defects_payload=defects_payload,
        strict_mode=args.strict or bool(((state.get("task") or {}).get("strict_mode"))),
        semantic_report_path=Path(args.semantic_report) if args.semantic_report else None,
    )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        out_path,
        json.dumps(decision, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    if args.update_state and state_path.exists():
        state["last_gatekeeper_decision"] = decision["decision"]
        state["status"] = (
            "DONE"
            if decision["decision"] == "DONE"
            else ("BLOCKED" if decision["decision"] == "BLOCKED" else "EVALUATING")
        )
        try:
            state["artifacts"]["gatekeeper_decision"] = str(
                out_path.resolve().relative_to(Path.cwd().resolve())
            )
        except ValueError:
            state["artifacts"]["gatekeeper_decision"] = str(out_path.resolve())
        state = validate_state(state)
        atomic_write_text(
            state_path,
            json.dumps(state, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    print(
        json.dumps(
            {"decision": decision["decision"], "reasons": decision["reasons"]},
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"decision_report: {out_path}")


if __name__ == "__main__":
    main()
