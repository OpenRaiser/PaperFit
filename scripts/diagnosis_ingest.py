#!/usr/bin/env python3
"""Turn a host diagnosis into repair candidates, and classify compile failures.

Visual prose is not a repair plan. Strict JSON is preferred. A weak fallback
only records a review-only finding and never becomes an automatic edit.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)
_PAGE_RE = re.compile(r"(?:page|第)\s*(\d+)", re.IGNORECASE)
_FAMILY_RE = re.compile(r"\b([A-E][0-9])\b")
_MISSING_IMAGE_RE = re.compile(
    r"File `([^`]+?\.(?:pdf|png|jpe?g|eps))' not found",
    re.IGNORECASE,
)
_OVERFULL_RE = re.compile(r"Overfull \\hbox")


def _loads_json(text: str) -> Any:
    raw = _FENCE_RE.sub("", text or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None


def _anchor(item: Dict[str, Any]) -> Dict[str, Any]:
    source = item.get("source_anchor") if isinstance(item.get("source_anchor"), dict) else {}
    label = str(source.get("label") or item.get("label") or "")
    line = source.get("line_start", item.get("line_number"))
    try:
        line_number = int(line) if line not in (None, "") else None
    except (TypeError, ValueError):
        line_number = None
    return {"label": label, "line_number": line_number}


def _normalize_finding(item: Dict[str, Any], *, fallback: bool) -> Dict[str, Any]:
    subfamily = str(item.get("subfamily") or "")
    family = str(item.get("family") or "")
    defect_family = subfamily or (family if len(family) > 1 else "")
    anchor = _anchor(item)
    try:
        confidence = float(item.get("confidence") if item.get("confidence") is not None else 1.0)
    except (TypeError, ValueError):
        confidence = 0.0
    anchored = bool(anchor["label"] or anchor["line_number"] is not None)
    needs_review = bool(item.get("needs_review") or item.get("requires_human_review") or fallback)
    if not anchored or confidence < 0.6:
        needs_review = True
    return {
        "page": item.get("page"),
        "region": item.get("region"),
        "family": family[:1] if family else (defect_family[:1] if defect_family else ""),
        "subfamily": defect_family,
        "description": str(item.get("description") or ""),
        "confidence": confidence,
        "label": anchor["label"],
        "line_number": anchor["line_number"],
        "requires_human_review": needs_review,
        "source_anchor_status": "resolved" if anchored else "unresolved",
    }


def parse_host_diagnosis(text: str) -> Dict[str, Any]:
    """Parse strict JSON, a fenced JSON block, or one weak prose finding."""

    data = _loads_json(text or "")
    raw_findings: List[Any] = []
    parse_mode = "fallback"
    if isinstance(data, dict) and isinstance(data.get("findings"), list):
        raw_findings = data["findings"]
        parse_mode = "json"
    elif isinstance(data, list):
        raw_findings = data
        parse_mode = "json"

    findings: List[Dict[str, Any]] = []
    if parse_mode == "json":
        for item in raw_findings:
            if isinstance(item, dict):
                findings.append(_normalize_finding(item, fallback=False))
    else:
        page_match = _PAGE_RE.search(text or "")
        family_match = _FAMILY_RE.search(text or "")
        if page_match or family_match:
            findings.append(
                _normalize_finding(
                    {
                        "page": int(page_match.group(1)) if page_match else None,
                        "subfamily": family_match.group(1) if family_match else "",
                        "description": " ".join((text or "").split()),
                        "confidence": 0.3,
                        "needs_review": True,
                    },
                    fallback=True,
                )
            )
    return {"parse_mode": parse_mode, "findings": findings}


def findings_to_candidates(findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Map anchored findings to candidates. Unanchored findings stay review-only."""

    candidates: List[Dict[str, Any]] = []
    for finding in findings:
        family = str(finding.get("subfamily") or finding.get("family") or "")
        if not family:
            continue
        review = bool(finding.get("requires_human_review"))
        label = str(finding.get("label") or "")
        target: Dict[str, Any] = {"scope": "diagnosis"}
        if label:
            target["label"] = label
        candidates.append(
            {
                "candidate_type": "diagnosis",
                "page": finding.get("page"),
                "target": target,
                "defect_family": family,
                "priority_score": 60 if review else 78,
                "severity": "major",
                "proposed_action": "review_only" if review else "repair_from_diagnosis",
                "rationale": str(finding.get("description") or "host diagnosis"),
                "description": str(finding.get("description") or ""),
                "line_number": finding.get("line_number"),
                "requires_human_review": review,
                "source_anchor_status": finding.get("source_anchor_status") or "unresolved",
                "evidence_sources": ["host_diagnosis"],
            }
        )
    return candidates


def classify_compile_case(log_text: str, *, success: bool) -> Dict[str, Any]:
    """Separate missing packages, missing figures, source errors, and layout warnings."""

    log = log_text or ""
    missing_sty: List[str] = []
    for match in re.finditer(r"File `([A-Za-z0-9_@+-]+)\.sty' not found", log):
        name = match.group(1)
        if name not in missing_sty:
            missing_sty.append(name)
    missing_images: List[str] = []
    for match in _MISSING_IMAGE_RE.finditer(log):
        name = match.group(1)
        if name not in missing_images:
            missing_images.append(name)
    if missing_images:
        status = "input_incomplete"
    elif missing_sty:
        status = "environment_ineligible"
    elif not success:
        status = "source_compile_error"
    elif _OVERFULL_RE.search(log):
        status = "layout_evaluable"
    else:
        status = "eligible"
    return {
        "case_status": status,
        "missing_sty": missing_sty,
        "missing_images": missing_images,
    }
