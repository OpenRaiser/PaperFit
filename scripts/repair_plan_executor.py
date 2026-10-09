#!/usr/bin/env python3
"""
Execute a bounded subset of repair-plan candidates against a TeX source file.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from content_integrity_check import compute_content_diff, has_structure_regression, has_scientific_regression
from fixer_registry import (
    CANONICAL_EXECUTION_ENTRY,
    canonical_execution_manifest,
    execute_consistency_candidates,
    execute_float_candidates,
    execute_overflow_candidates,
    execute_space_util_candidates,
)
from transactional_patch import atomic_write_text
from visual_action_plan import document_scope_b1_authorized
from source_map import masked_tex, within
from layout_policy import combine_policy_reports, compare_layout_policy, _review_json, _relative_path
from source_review import load_source_review
from runtime_snapshots import discover_source_files
from scientific_integrity import scientific_payload_diff


_DISPLAY_MATH_BEGIN_RE = re.compile(r"\\begin\{(equation\*?|align\*?|multline\*?|gather\*?)\}")
_DISPLAY_MATH_END_RE = re.compile(r"\\end\{(equation\*?|align\*?|multline\*?|gather\*?)\}")
# `\[` / `\]` 短语法显示数学：D1→D2 路由同样要识别（与 overflow_fixers 的
# _BRACKET_DISPLAY_RE 对齐），否则短语法溢出被当段落溢出处理而无改动。
# (?<!\\) 排除 `\\[0.5em]`（换行+间距）被误认。
_BRACKET_DISPLAY_OPEN_RE = re.compile(r"(?<!\\)\\\[")
_BRACKET_DISPLAY_CLOSE_RE = re.compile(r"(?<!\\)\\\]")


def _display_math_anchor_line(main_tex: str, line_number: Optional[int], tolerance: int = 2) -> Optional[int]:
    if line_number is None or line_number < 1:
        return None

    tex_path = Path(main_tex)
    if not tex_path.is_file():
        return None

    lines = masked_tex(tex_path.read_text(encoding="utf-8")).splitlines()
    env_stack: List[tuple[str, int]] = []
    display_ranges: List[tuple[int, int]] = []

    for idx, line in enumerate(lines, start=1):
        begin_match = _DISPLAY_MATH_BEGIN_RE.search(line)
        if begin_match:
            env_stack.append((begin_match.group(1), idx))
        elif _BRACKET_DISPLAY_OPEN_RE.search(line):
            env_stack.append(("[bracket-display]", idx))

        end_match = _DISPLAY_MATH_END_RE.search(line)
        if end_match:
            end_env = end_match.group(1)
            for stack_index in range(len(env_stack) - 1, -1, -1):
                if env_stack[stack_index][0] == end_env:
                    _, start_line = env_stack.pop(stack_index)
                    display_ranges.append((start_line, idx))
                    break
        elif _BRACKET_DISPLAY_CLOSE_RE.search(line):
            for stack_index in range(len(env_stack) - 1, -1, -1):
                if env_stack[stack_index][0] == "[bracket-display]":
                    _, start_line = env_stack.pop(stack_index)
                    display_ranges.append((start_line, idx))
                    break

    for start_line, end_line in display_ranges:
        if start_line - tolerance <= line_number <= end_line + tolerance:
            return start_line
    return None


def _line_is_within_display_math(main_tex: str, line_number: Optional[int], tolerance: int = 2) -> bool:
    return _display_math_anchor_line(main_tex, line_number, tolerance=tolerance) is not None


def _load_json(path: str) -> Dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"JSON file not found: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def _effective_changes(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    changes = report.get("changes") or []
    effective: List[Dict[str, Any]] = []
    for change in changes:
        if not isinstance(change, dict):
            continue
        if change.get("success") is False:
            continue
        before = str(change.get("before") or "")
        after = str(change.get("after") or "")
        if before == after:
            continue
        effective.append(change)
    return effective


def _build_float_defects(plan: Dict[str, Any], max_candidates: int) -> List[Dict[str, Any]]:
    defects: List[Dict[str, Any]] = []
    seen_labels: set[tuple[str, str]] = set()
    seen_objects: set[str] = set()
    merged_b1_metadata: Dict[str, Dict[str, Any]] = {}
    current_pages = max(
        [int(candidate.get("current_pages") or 0) for candidate in plan.get("candidates") or []]
        + [int(candidate.get("page") or 0) for candidate in plan.get("candidates") or []]
        + [0]
    )
    tail_float_pressure = any(
        str(candidate.get("candidate_type") or "") == "tail_float_packing"
        or (
            str(candidate.get("candidate_type") or "") == "visual_tail"
            and str(candidate.get("defect_family") or "") in {"A2", "A4"}
            and int(candidate.get("page") or 0) >= max(1, current_pages - 1)
        )
        for candidate in plan.get("candidates") or []
    )

    def _parse_width_ratio(width_spec: Any) -> Optional[float]:
        text = str(width_spec or "").strip()
        if not text:
            return None
        match = re.fullmatch(r"(?:(\d+(?:\.\d+)?)\s*)?(\\linewidth|\\textwidth)", text)
        if not match:
            return None
        ratio_text = match.group(1)
        return float(ratio_text) if ratio_text is not None else 1.0

    def _append_candidate(candidate: Dict[str, Any]) -> None:
        nonlocal defects
        if len(defects) >= max_candidates:
            return

        defect_family = str(candidate.get("defect_family") or "")
        target = candidate.get("target") or {}
        label = target.get("label")

        if defect_family == "B3":
            labels = [str(item) for item in (target.get("labels") or []) if str(item)]
            if len(labels) < 2:
                return
            dedupe_key = (defect_family, ",".join(sorted(labels)))
            if dedupe_key in seen_labels:
                return
            seen_labels.add(dedupe_key)
            defects.append(
                {
                    "defect_id": defect_family,
                    "object": labels[0],
                    "labels": labels,
                    "page": candidate.get("page") or 0,
                    "line_number": None,
                    "avoid_input_migration": tail_float_pressure,
                    "tail_float_packing": tail_float_pressure
                    or str(candidate.get("candidate_type") or "") == "tail_float_packing",
                }
            )
            return

        if defect_family not in {"B1", "B2"}:
            return
        if not label:
            # Unanchored visual findings require explicit document-scope authorization.
            if defect_family == "B1" and document_scope_b1_authorized(candidate):
                doc_key = "document"
                if doc_key in seen_objects:
                    return
                seen_objects.add(doc_key)
                severity = str(candidate.get("severity") or "major").lower()
                defects.append(
                    {
                        "defect_id": "B1",
                        "object": None,
                        "page": candidate.get("page") or 0,
                        "line_number": None,
                        "finding_id": candidate.get("finding_id"),
                        "severity": severity,
                        "description": str(candidate.get("rationale") or "page-level float stacking"),
                        "document_scope_approved": True,
                        "endmatter_float_intrusion": candidate.get("endmatter_float_intrusion") is True,
                        "hard_guard_evidence": candidate.get("hard_guard_evidence"),
                        "visual_hard_guard": candidate.get("visual_hard_guard"),
                        "hard_guard": candidate.get("hard_guard"),
                    }
                )
            return
        if defect_family == "B2" and str(candidate.get("visual_width_subtype") or "") not in {
            "overflow_width",
            "underfilled_width",
        }:
            return

        object_key = str(label)
        if object_key in seen_objects:
            return
        dedupe_key = (defect_family, str(label))
        if dedupe_key in seen_labels:
            return
        seen_labels.add(dedupe_key)
        seen_objects.add(object_key)

        defect = {
            "defect_id": defect_family,
            "object": label,
            "page": candidate.get("page") or 0,
            "line_number": None,
            
            "finding_id": candidate.get("finding_id"),
        }
        if defect_family == "B1":
            defect["ref_page"] = candidate.get("page") or 0
            defect.update(merged_b1_metadata.get(str(label)) or {})
            for key in ("document_scope_approved", "allow_floatbarrier", "requires_floatbarrier",
                        "hard_guard_float_intrusion", "endmatter_float_intrusion",
                        "body_float_intrudes_endmatter", "visual_hard_guard", "hard_guard", "hard_guard_evidence"):
                if candidate.get(key) is not None:
                    defect[key] = candidate[key]
        if defect_family == "B2":
            target_object_kind = target.get("object_kind")
            if target_object_kind is not None:
                defect["object_kind"] = target_object_kind
            for key in (
                "source_table_env",
                "source_tabcolsep",
                "source_width_spec",
                "visual_width_subtype",
                "visual_object_width_ratio",
                "visual_object_width_page_ratio",
                "visual_width_context",
                "visual_overflow_pt",
                "visual_pdf_bbox",
                "visual_confidence",
                "allow_floatbarrier",
                "requires_floatbarrier",
                "hard_guard_float_intrusion",
                "endmatter_float_intrusion",
                "body_float_intrudes_endmatter",
                "visual_hard_guard",
                "hard_guard",
                "hard_guard_evidence",
            ):
                if candidate.get(key) is not None:
                    defect[key] = candidate.get(key)
        defects.append(defect)

    for candidate in plan.get("candidates") or []:
        if str(candidate.get("defect_family") or "") != "B1":
            continue
        label = str(((candidate.get("target") or {}).get("label")) or "")
        if not label:
            continue
        current = merged_b1_metadata.setdefault(label, {})
        for key in (
            "ref_line",
            "float_line",
            "line_distance",
            "section_distance",
            "reference_source",
            "reference_text",
            "float_section",
            "semantic_home",
            "semantic_band",
        ):
            if candidate.get(key) is not None:
                current[key] = candidate.get(key)

    # Keep strong semantic-distance B1 candidates and visually confirmed B2
    # width candidates so targetable float/table fixes are not starved by
    # generic page-level signals.
    visual_width_candidates = [
        candidate
        for candidate in (plan.get("candidates") or [])
        if str(candidate.get("defect_family") or "") == "B2"
        and _is_targetable_visual_b2_candidate(candidate)
    ]
    visual_width_candidates.sort(
        key=lambda item: (
            0 if str(item.get("visual_width_subtype") or "") == "overflow_width" else 1,
            0 if str(item.get("visual_width_subtype") or "") == "underfilled_width" else 1,
            -float(item.get("visual_overflow_pt") or 0.0),
            float(item.get("visual_object_width_ratio") or (_parse_width_ratio(item.get("source_width_spec")) or 1.0)),
            int(item.get("page") or 0),
            -int(item.get("priority_score") or 0),
            str(((item.get("target") or {}).get("label")) or ""),
        )
    )
    deduped_visual_width_candidates: List[Dict[str, Any]] = []
    seen_width_labels: set[str] = set()
    for candidate in visual_width_candidates:
        label = str(((candidate.get("target") or {}).get("label")) or "")
        if not label or label in seen_width_labels:
            continue
        seen_width_labels.add(label)
        deduped_visual_width_candidates.append(candidate)
    for candidate in deduped_visual_width_candidates[:2]:
        _append_candidate(candidate)

    far_anchor_candidates = [
        candidate
        for candidate in (plan.get("candidates") or [])
        if str(candidate.get("defect_family") or "") == "B1"
        and str(((candidate.get("target") or {}).get("label")) or "")
        and (
            int(candidate.get("line_distance") or 0) >= 20
            or int(candidate.get("section_distance") or 0) >= 1
        )
    ]
    far_anchor_candidates.sort(
        key=lambda item: (
            -int(item.get("section_distance") or 0),
            -int(item.get("line_distance") or 0),
            -int(item.get("priority_score") or 0),
            str(((item.get("target") or {}).get("label")) or ""),
        )
    )
    for candidate in far_anchor_candidates[:2]:
        _append_candidate(candidate)

    b3_cluster_candidates = [
        candidate
        for candidate in (plan.get("candidates") or [])
        if str(candidate.get("defect_family") or "") == "B3"
        and len((candidate.get("target") or {}).get("labels") or []) >= 2
    ]
    b3_cluster_candidates.sort(
        key=lambda item: (
            -int(item.get("priority_score") or 0),
            int(item.get("page") or 0),
            ",".join(str(label) for label in ((item.get("target") or {}).get("labels") or [])),
        )
    )
    for candidate in b3_cluster_candidates[:2]:
        _append_candidate(candidate)

    for candidate in plan.get("candidates") or []:
        _append_candidate(candidate)
        if len(defects) >= max_candidates:
            break
    return defects


def _scan_long_unbreakable_tokens(main_tex: str, max_candidates: int) -> List[Dict[str, Any]]:
    tex_path = Path(main_tex)
    if not tex_path.is_file():
        return []

    tex_content = tex_path.read_text(encoding="utf-8")
    # Restrict the fallback to obviously abnormal tokens so we do not
    # rewrite ordinary citation keys or bibliography identifiers.
    defects: List[Dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    token_matches = list(re.finditer(r"(?<!\\)\b[A-Za-z][A-Za-z0-9]{39,}\b", tex_content))
    token_matches.sort(key=lambda match: len(match.group(0)), reverse=True)
    for match in token_matches:
        token = match.group(0)
        line_number = tex_content.count("\n", 0, match.start()) + 1
        display_anchor_line = _display_math_anchor_line(main_tex, line_number)
        if display_anchor_line is not None:
            dedupe_key = ("display_math", display_anchor_line)
        else:
            dedupe_key = (token, line_number)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        if display_anchor_line is not None:
            defect_id = "D2"
            object_name = "equation_overflow"
            line_number = display_anchor_line
        else:
            defect_id = "D1"
            object_name = "paragraph_overflow"
        defects.append(
            {
                "defect_id": defect_id,
                "object": object_name,
                "page": 0,
                "line_number": line_number,
                "description": token,
                "overflow_amount": 0,
                "source": "tex_long_token_fallback",
            }
        )
        if len(defects) >= max_candidates:
            break
    return defects


def _build_overflow_defects(
    plan: Dict[str, Any],
    main_tex: str,
    max_candidates: int,
) -> List[Dict[str, Any]]:
    defects: List[Dict[str, Any]] = []
    seen: set[tuple[str, str, str, Optional[int], str]] = set()
    saw_overflow_candidate = False

    # 批次内的 D 族候选按溢出量降序处理：平局优先级下最重的溢出先进批次，
    # 738pt 的公式溢出不能被行号靠前的 3pt 级段落溢出挤出名额
    d_family_candidates = [
        candidate
        for candidate in plan.get("candidates") or []
        if str(candidate.get("defect_family") or "") in {"D1", "D2", "D3"}
    ]
    d_family_candidates.sort(key=lambda c: -float(c.get("overflow_amount") or 0.0))

    for candidate in d_family_candidates:
        defect_id = str(candidate.get("defect_family") or "")
        saw_overflow_candidate = True

        target = candidate.get("target") or {}
        object_name = str(target.get("label") or target.get("scope") or defect_id)
        line_number = candidate.get("line_number")
        description = str(candidate.get("description") or "").strip()
        source_anchor = candidate.get("source_anchor") or {}
        source_file = str(candidate.get("source_file") or source_anchor.get("file") or "")
        source_root = Path.cwd().resolve()
        main_path = Path(main_tex).resolve()
        if not within(main_path, source_root):
            source_root = main_path.parent
        is_main_source = not source_file or (source_root / source_file).resolve() == main_path
        if defect_id == "D1" and is_main_source and _line_is_within_display_math(main_tex, line_number):
            defect_id = "D2"
            object_name = "equation_overflow"
        dedupe_key = (defect_id, source_file, object_name, line_number, description)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        defect_entry: Dict[str, Any] = {
            "defect_id": defect_id,
            "object": object_name,
            "page": candidate.get("page") or 0,
            "line_number": line_number,
            "description": description,
            "overflow_amount": candidate.get("overflow_amount") or 0,
            "url": candidate.get("url"),
            "subtype": candidate.get("subtype"),
        }
        # 透传 proposed_action 让 executor 的路由能识别 textheight 移除等特殊动作
        if candidate.get("proposed_action"):
            defect_entry["proposed_action"] = candidate["proposed_action"]
        
        if candidate.get("finding_id"):
            defect_entry["finding_id"] = candidate["finding_id"]
        for field in ("source_anchor", "source_file", "overflow_id"):
            if candidate.get(field):
                defect_entry[field] = candidate[field]
        defects.append(defect_entry)
        if len(defects) >= max_candidates:
            break

    if defects:
        has_actionable_context = any(
            defect.get("description")
            or defect.get("line_number")
            or defect.get("object") not in {"overflow", "table_overflow", "D1"}
            for defect in defects
        )
        if has_actionable_context:
            return defects

    if not saw_overflow_candidate:
        return []

    fallback_defects = _scan_long_unbreakable_tokens(main_tex=main_tex, max_candidates=max_candidates)
    return fallback_defects or defects


def _build_consistency_defects(plan: Dict[str, Any], max_candidates: int) -> List[Dict[str, Any]]:
    defects: List[Dict[str, Any]] = []
    for candidate in plan.get("candidates") or []:
        family = str(candidate.get("defect_family") or "")
        if not family.startswith("C"):
            continue
        defect: Dict[str, Any] = {"defect_id": family, "defect_family": family}
        if isinstance(candidate.get("style_policy"), dict):
            defect["style_policy"] = candidate["style_policy"]
        defects.append(defect)
        if len(defects) >= max_candidates:
            break
    return defects


def _build_space_util_defects(
    plan: Dict[str, Any],
    max_candidates: int,
    column_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    defects: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    # A4（双栏末页不齐）在单栏模板上必然被 fixer 拒绝，选进去就是浪费批次席位
    is_double_column = str(column_type or "").lower() == "double"

    for candidate in plan.get("candidates") or []:
        defect_id = str(candidate.get("defect_family") or "")
        if not defect_id.startswith("A") or defect_id == "A/C":
            continue
        if defect_id == "A4" and not is_double_column:
            continue
        target = candidate.get("target") or {}
        object_name = str(target.get("label") or target.get("scope") or defect_id)
        dedupe_key = (defect_id, object_name)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        defects.append(
            {
                "defect_id": defect_id,
                "object": object_name,
                "page": candidate.get("page") or 0,
                "line_number": candidate.get("line_number"),
                "description": str(candidate.get("description") or candidate.get("rationale") or ""),
                "whitespace_ratio": candidate.get("whitespace_ratio"),
                "current_pages": candidate.get("current_pages"),
                "height_difference": candidate.get("height_difference"),
                "finding_id": candidate.get("finding_id"),
            }
        )
        if len(defects) >= max_candidates:
            break
    return defects


def _build_global_actions(plan: Dict[str, Any], max_candidates: int) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    for candidate in plan.get("candidates") or []:
        if str(candidate.get("candidate_type") or "") != "global":
            continue
        if str(candidate.get("proposed_action") or "") != "review_paragraph_spacing_and_looseness":
            continue
        actions.append(candidate)
        if len(actions) >= max_candidates:
            break
    return actions


def _candidate_label(candidate: Dict[str, Any]) -> str:
    return str(((candidate.get("target") or {}).get("label")) or "")


def _candidate_object_kind(candidate: Dict[str, Any]) -> str:
    return str(((candidate.get("target") or {}).get("object_kind")) or "")


def _is_targetable_visual_b2_candidate(candidate: Dict[str, Any]) -> bool:
    label = _candidate_label(candidate)
    if not label:
        return False
    if str(candidate.get("visual_width_subtype") or "") not in {"overflow_width", "underfilled_width"}:
        return False
    object_kind = _candidate_object_kind(candidate)
    return object_kind in {"figure_like", "table_like"} or label.startswith(("fig:", "tab:"))


def _summarize_selected_b2_width_candidates(defects: List[Dict[str, Any]]) -> Dict[str, Any]:
    selected = [
        defect
        for defect in defects
        if str(defect.get("defect_id") or "") == "B2"
        and str(defect.get("visual_width_subtype") or "") in {"overflow_width", "underfilled_width"}
    ]
    summary: Dict[str, Any] = {
        "total": len(selected),
        "by_subtype": {},
        "by_object_kind": {},
        "labels": [],
        "objects": [],
        "pages": [],
    }
    labels: List[str] = []
    objects: List[str] = []
    pages: List[int] = []
    for defect in selected:
        subtype = str(defect.get("visual_width_subtype") or "unknown")
        summary["by_subtype"][subtype] = int(summary["by_subtype"].get(subtype, 0)) + 1

        object_kind = str(defect.get("object_kind") or "")
        if object_kind:
            summary["by_object_kind"][object_kind] = int(summary["by_object_kind"].get(object_kind, 0)) + 1

        object_name = str(defect.get("object") or "")
        if object_name:
            labels.append(object_name)
            objects.append(object_name)

        try:
            page = int(defect.get("page") or 0)
        except (TypeError, ValueError):
            page = 0
        if page > 0:
            pages.append(page)

    summary["labels"] = sorted(set(labels))
    summary["objects"] = sorted(set(objects))
    summary["pages"] = sorted(set(pages))
    return summary


def _has_float_priority_candidates(plan: Dict[str, Any]) -> bool:
    for candidate in plan.get("candidates") or []:
        family = str(candidate.get("defect_family") or "")
        if family == "B1" and _candidate_label(candidate):
            return True
        if family == "B2" and _is_targetable_visual_b2_candidate(candidate):
            return True
        if family == "B3" and len((candidate.get("target") or {}).get("labels") or []) >= 2:
            return True
    return False


def _has_visual_b2_width_candidates(plan: Dict[str, Any]) -> bool:
    for candidate in plan.get("candidates") or []:
        if str(candidate.get("defect_family") or "") != "B2":
            continue
        if _is_targetable_visual_b2_candidate(candidate):
            return True
    return False


def _has_urgent_overflow_candidates(plan: Dict[str, Any]) -> bool:
    for candidate in plan.get("candidates") or []:
        if str(candidate.get("defect_family") or "") not in {"D1", "D2", "D3"}:
            continue
        overflow_amount = float(candidate.get("overflow_amount") or 0.0)
        priority_score = int(candidate.get("priority_score") or 0)
        if priority_score >= 90 or overflow_amount >= 5.0:
            return True
    return False


def _has_catastrophic_overflow_candidates(plan: Dict[str, Any]) -> bool:
    "≥100pt 的溢出是灾难级（内容冲出页面），优先级高于视觉 B2 宽度微调。"
    for candidate in plan.get("candidates") or []:
        if str(candidate.get("defect_family") or "") not in {"D1", "D2", "D3"}:
            continue
        if float(candidate.get("overflow_amount") or 0.0) >= 100.0:
            return True
    return False


def _passes_global_content_gate(original_tex: str, repaired_tex: str) -> bool:
    diff = compute_content_diff(original_tex, repaired_tex)
    return (
        not has_scientific_regression(diff)
        and (diff["hash_comparison"]["identical"]
        or (
            diff["sentence_changes"]["deleted_count"] == 0
            and diff["sentence_changes"]["added_count"] == 0
            and diff["word_count"]["change"] == 0
        ))
    )


def _repair_paragraph_spacing(tex_content: str) -> tuple[str, int]:
    lines = tex_content.splitlines(keepends=True)
    updated_lines: List[str] = []
    changes = 0

    protected_prefixes = (
        "\\begin{",
        "\\end{",
        "\\item",
        "\\bibitem",
        "\\caption",
        "\\section",
        "\\subsection",
        "\\subsubsection",
        "\\paragraph",
        "\\vspace",
        "\\hspace",
        "\\label",
        "\\includegraphics",
        "\\author",
        "\\title",
        "\\affiliation",
        "\\thanks",
    )

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.endswith(r"\\"):
            updated_lines.append(line)
            continue
        if any(stripped.startswith(prefix) for prefix in protected_prefixes):
            updated_lines.append(line)
            continue
        if "&" in stripped:
            updated_lines.append(line)
            continue
        if re.search(r"\\(?:hline|cline|toprule|midrule|bottomrule)\b", stripped):
            updated_lines.append(line)
            continue
        if not re.search(r"[.!?:;]\s*\\\\\s*$", stripped):
            updated_lines.append(line)
            continue

        next_nonempty = ""
        for future in lines[index + 1:]:
            candidate = future.strip()
            if candidate:
                next_nonempty = candidate
                break
        if next_nonempty.startswith(("\\begin{equation", "\\begin{align", "\\begin{gather", "\\[")):
            updated_lines.append(line)
            continue

        newline = "\n" if line.endswith("\n") else ""
        updated_lines.append(re.sub(r"\\\\\s*$", "", line.rstrip("\n")) + newline)
        changes += 1

    return "".join(updated_lines), changes


def _execute_global_actions(
    main_tex: str,
    actions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    tex_path = Path(main_tex)
    if not actions or not tex_path.is_file():
        return {"applied_count": 0, "status": "noop", "changes": [], "unresolved": []}

    original = tex_path.read_text(encoding="utf-8")
    updated = original
    applied_changes: List[Dict[str, Any]] = []
    unresolved: List[str] = []

    for action in actions:
        proposed_action = str(action.get("proposed_action") or "")
        if proposed_action != "review_paragraph_spacing_and_looseness":
            unresolved.append(f"unsupported global action: {proposed_action}")
            continue
        repaired, changed_count = _repair_paragraph_spacing(updated)
        if changed_count == 0:
            unresolved.append("paragraph_spacing: no eligible forced line breaks found")
            continue
        if not _passes_global_content_gate(updated, repaired):
            unresolved.append("paragraph_spacing: content gate rejected candidate patch")
            continue
        updated = repaired
        applied_changes.append(
            {
                "action": proposed_action,
                "target": action.get("target") or {"scope": "paragraph_spacing"},
                "applied_edits": changed_count,
            }
        )

    if updated != original:
        atomic_write_text(tex_path, updated, backup_dir=tex_path.parent / "data" / "backups")

    status = "success" if applied_changes and not unresolved else "partial" if applied_changes else "noop"
    return {
        "applied_count": len(applied_changes),
        "status": status,
        "changes": applied_changes,
        "unresolved": unresolved,
    }


def _build_deferred_global_report(reason: str) -> Dict[str, Any]:
    return {
        "applied_count": 0,
        "status": "noop",
        "changes": [],
        "unresolved": [reason],
    }


def _build_deferred_skill_report(skill: str, reason: str) -> Dict[str, Any]:
    return {
        "skill": skill,
        "status": "noop",
        "changes": [],
        "unresolved": [reason],
    }


def _has_structure_regression(diff: Dict[str, Any]) -> bool:
    return has_structure_regression(diff)


def _explicit_review_inputs(root: Path, main_tex: str, layout_paths=(), source_paths=()):
    """Load only explicitly requested, project-contained, main-matched receipts."""
    expected = _relative_path(root, main_tex)
    approvals, context, source_reviews = [], None, []
    for path in layout_paths:
        request = _review_json(root / _relative_path(root, path))
        candidate = request.get("context")
        if (not isinstance(candidate, dict) or
                set(candidate) - {"main_tex", "page_images_dir", "compile_journal"} or
                not {"main_tex", "page_images_dir"} <= set(candidate) or
                any(not isinstance(value, str) or not value.strip() for value in candidate.values())):
            raise ValueError("layout review context incomplete or unsupported")
        if _relative_path(root, candidate["main_tex"]) != expected:
            raise ValueError("layout review main differs from the actual task main")
        for value in candidate.values():
            _relative_path(root, value)
        if context is not None and context != candidate:
            raise ValueError("layout reviews use different compile/page contexts")
        context = candidate
        if any(not isinstance(item, dict) for item in request["approvals"]):
            raise ValueError("layout review approvals must be objects")
        approvals.extend(request["approvals"])
    for path in source_paths:
        request = load_source_review(root / _relative_path(root, path))
        candidate = request.get("context")
        if (not isinstance(candidate, dict) or set(candidate) != {"main_tex", "page_images_dir", "compile_journal"}
                or any(not isinstance(value, str) or not value.strip() for value in candidate.values())):
            raise ValueError("source review context incomplete or unsupported")
        if _relative_path(root, candidate["main_tex"]) != expected:
            raise ValueError("source review main differs from the actual task main")
        for value in candidate.values():
            _relative_path(root, value)
        _relative_path(root, request.get("file", ""))
        source_reviews.append(request)
    return approvals, context, source_reviews


def _matched_source_review(source_reviews, file: str):
    matching = [item for item in source_reviews or () if isinstance(item, dict) and item.get("file") == file]
    # Ambiguous input must not select whichever approval happens to come first.
    return matching[0] if len(matching) == 1 else None


def _main_matched_context(root: Path, main: Path, context):
    if not isinstance(context, dict):
        return None
    try:
        expected = main.resolve().relative_to(root).as_posix()
        return context if _relative_path(root, context.get("main_tex", "")) == expected else None
    except (ValueError, TypeError):
        return None


def _enforce_structure_integrity(
    *,
    main_tex: str,
    original_tex: str,
    report: Dict[str, Any],
    original_bytes: Optional[bytes] = None,
    original_sources: Optional[Dict[Path, bytes]] = None,
    layout_policy_approvals: Optional[List[Dict[str, Any]]] = None,
    project_root: Optional[Path] = None,
    review_context: Optional[Dict[str, str]] = None,
    source_reviews: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    tex_path = (Path(project_root) / main_tex).resolve() if project_root is not None else Path(main_tex)
    root = Path(project_root).resolve() if project_root is not None else Path.cwd().resolve() if within(tex_path.resolve(), Path.cwd().resolve()) else tex_path.resolve().parent
    if not within(tex_path.resolve(), root):
        raise ValueError("main TeX outside project root")
    if not tex_path.is_file():
        # The caller captured this tracked file before execution. Its removal is
        # a source loss, not an unverified presentation change awaiting review.
        sources = original_sources or {tex_path.resolve(): original_bytes if original_bytes is not None else original_tex.encode("utf-8")}
        if any(not within(source.resolve(), root) for source in sources):
            raise ValueError("refusing rollback outside project root")
        for source, baseline_bytes in sources.items():
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(baseline_bytes)
        report.update(status="failed", success=False, applied_count=0, rollback_reason="tracked_source_deleted")
        report["content_integrity"] = {"checked": False, "status": "failed", "rollback_performed": True,
                                       "reason": f"tracked main tex removed: {main_tex}"}
        return report

    final_tex = tex_path.read_text(encoding="utf-8")
    review_context = _main_matched_context(root, tex_path, review_context)
    relative_main = tex_path.resolve().relative_to(root).as_posix()
    source_reviews = [item for item in source_reviews or () if isinstance(item, dict) and _main_matched_context(root, tex_path, item.get("context")) is not None]
    diff = compute_content_diff(
        original_tex, final_tex, layout_policy_approvals=[item for item in layout_policy_approvals or () if isinstance(item, dict) and item.get("file") == relative_main],
        file=relative_main, layout_only=True, project_root=root, review_context=review_context,
        source_review=_matched_source_review(source_reviews, relative_main),
    )
    sources = original_sources or {tex_path.resolve(): original_bytes if original_bytes is not None else original_tex.encode("utf-8")}
    policy_reports = []
    prose_reports = []
    scientific_reports = []
    file_diffs = []
    tracked_source_deleted = False
    for source, baseline_bytes in sources.items():
        if source.suffix.lower() != ".tex":
            continue
        if not within(source.resolve(), root):
            raise ValueError(f"tracked source outside project root: {source}")
        relative = source.resolve().relative_to(root).as_posix()
        if not source.is_file():
            tracked_source_deleted = True
            unavailable = {"file": relative, "checked": False, "status": "manual_review", "reason": "tracked TeX source unavailable"}
            policy_reports.append({**unavailable, "findings": [{"file": relative, "status": "manual_review", "rule": "tracked_source_unavailable", "reason": unavailable["reason"]}]})
            prose_reports.append(dict(unavailable))
            scientific_reports.append(dict(unavailable))
            continue
        repaired_bytes = source.read_bytes()
        before_source = baseline_bytes.decode("utf-8", errors="surrogateescape")
        after_source = repaired_bytes.decode("utf-8", errors="surrogateescape")
        if source.resolve() == tex_path.resolve():
            file_diff = diff
        elif baseline_bytes == repaired_bytes:
            unchanged = {"checked": True, "status": "pass", "comparison": "unchanged_source", "findings": []}
            file_diff = {"scientific_payload": dict(unchanged), "layout_prose": dict(unchanged), "layout_policy": dict(unchanged)}
        else:
            file_diff = compute_content_diff(
                before_source, after_source, file=relative, layout_only=True,
                layout_policy_approvals=[item for item in layout_policy_approvals or () if isinstance(item, dict) and item.get("file") == relative],
                project_root=root, review_context=review_context,
                source_review=_matched_source_review(source_reviews, relative),
            )
        file_diffs.append({"file": relative, "diff": file_diff})
        policy_reports.append(file_diff["layout_policy"])
        prose_reports.append({"file": relative, **file_diff["layout_prose"]})
        scientific_reports.append({"file": relative, **file_diff["scientific_payload"]})
    policy = combine_policy_reports(policy_reports)
    policy["rollback_performed"] = False
    report["layout_policy"] = policy
    prose_findings = [item for item in prose_reports if item.get("status") != "pass"]
    prose_checked = bool(prose_reports) and all(item.get("checked") is True for item in prose_reports)
    prose = {"checked": prose_checked, "status": "blocked" if any(item.get("status") in {"failed", "blocked"} for item in prose_reports) else "manual_review" if not prose_checked or prose_findings else "pass", "findings": prose_findings, "file_reports": prose_reports, "rollback_performed": False}
    report["layout_prose"] = prose
    scientific_findings = [item for item in scientific_reports if item.get("status") != "pass"]
    scientific_checked = bool(scientific_reports) and all(item.get("checked") is True for item in scientific_reports)
    scientific = {"checked": scientific_checked, "status": "blocked" if any(item.get("status") in {"failed", "blocked"} for item in scientific_reports) else "manual_review" if not scientific_checked or scientific_findings else "pass", "findings": scientific_findings, "file_reports": scientific_reports, "rollback_performed": False}
    report["scientific_payload"] = scientific
    integrity = {
        "checked": True,
        "status": "pass",
        "rollback_performed": False,
        "diff": diff,
        "file_diffs": file_diffs,
    }

    # Reviewable uncertainty preserves the candidate for an actual native build
    # and full-page review. Confirmed content loss or forbidden operations still
    # restore every tracked source byte.
    scientific_failed = tracked_source_deleted or scientific["status"] == "blocked" or any(
        entry["diff"].get("action_required") == "auto_rollback" and
        (_has_structure_regression(entry["diff"]) or has_scientific_regression(entry["diff"]))
        for entry in file_diffs
    )
    policy_failed = policy["status"] == "blocked"
    prose_failed = prose["status"] == "blocked"
    if scientific_failed or policy_failed or prose_failed:
        if original_bytes is not None:
            tex_path.write_bytes(original_bytes)
        else:
            atomic_write_text(tex_path, original_tex, backup_dir=tex_path.parent / "data" / "backups")
        for source, baseline_bytes in sources.items():
            if not within(source.resolve(), root):
                raise ValueError(f"refusing rollback outside project root: {source}")
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(baseline_bytes)
        if policy_failed or prose_failed:
            policy["rollback_performed"] = policy_failed
            prose["rollback_performed"] = prose_failed
        if scientific_failed:
            scientific["rollback_performed"] = True
            integrity["status"] = "failed"
            integrity["rollback_performed"] = True
            integrity["failure_reasons"] = [f"{item['file']}: {reason}" for item in scientific_findings for reason in item.get("regression_reasons", [])] or (diff.get("violation") or {}).get("reasons") or ["scientific validation or structure regression detected"]
        report["status"] = "failed"
        report["success"] = False
        report["applied_count"] = 0
        report["fix_report"] = {
            **(report.get("fix_report") or {}),
            "status": "failed",
        }
        report["overflow_report"] = {
            **(report.get("overflow_report") or {}),
            "status": "failed" if report.get("overflow_report") else "noop",
        }
        report["space_report"] = {
            **(report.get("space_report") or {}),
            "status": "failed" if report.get("space_report") else "noop",
        }
        report["global_report"] = {
            **(report.get("global_report") or {}),
            "status": "failed" if report.get("global_report") else "noop",
        }
        report["rollback_reason"] = "layout_policy_violation" if policy_failed and not scientific_failed else "layout_prose_changed" if prose_failed and not scientific_failed else "scientific_integrity_violation" if scientific_findings or has_scientific_regression(diff) else "structure_integrity_violation"

    elif any(channel["status"] == "manual_review" for channel in (policy, prose, scientific)) or any(
        entry["diff"].get("action_required") == "manual_review" for entry in file_diffs
    ):
        integrity["status"] = "manual_review"
        report["status"] = "manual_review"
        report["success"] = False
        report["review_required"] = True
        report["review_note"] = "Candidate retained; compile and inspect every current page before submitting an explicitly bound review."

    report["content_integrity"] = integrity
    return report


def _actionable_candidates(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        candidate
        for candidate in plan.get("candidates") or []
        if isinstance(candidate, dict) and not candidate.get("requires_human_review")
    ]


def _conflict_keys(candidate: Dict[str, Any]) -> List[str]:
    keys = candidate.get("conflict_keys")
    if not isinstance(keys, list):
        return []
    return [str(key) for key in keys if str(key)]


def _plan_uses_conflict_batch(plan: Dict[str, Any]) -> bool:
    candidates = _actionable_candidates(plan)
    return bool(candidates) and all(_conflict_keys(candidate) for candidate in candidates)


def _select_conflict_batch(plan: Dict[str, Any], max_candidates: int) -> List[Dict[str, Any]]:
    """Keep a priority-ordered set of candidates whose edit ranges do not overlap."""

    selected: List[Dict[str, Any]] = []
    used: set[str] = set()
    for candidate in _actionable_candidates(plan):
        keys = set(_conflict_keys(candidate))
        if "global:layout" in keys and selected:
            continue
        if any("global:layout" in _conflict_keys(item) for item in selected):
            continue
        if keys & used:
            continue
        selected.append(candidate)
        used.update(keys)
        if len(selected) >= max_candidates:
            break
    return selected


def execute_repair_plan(
    repair_plan_path: str,
    main_tex: str,
    output_path: str,
    column_type: Optional[str] = None,
    target_pages: Optional[int] = None,
    max_candidates: int = 5,
    layout_policy_approvals: Optional[List[Dict[str, Any]]] = None,
    project_root: Optional[Path] = None,
    review_context: Optional[Dict[str, str]] = None,
    source_reviews: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    if project_root is not None:
        project_root = Path(project_root).resolve()
        path = (project_root / main_tex).resolve()
        if not within(path, project_root):
            raise ValueError("main TeX outside project root")
        main_tex = str(path)
    if project_root is not None:
        repair_plan_path = str(Path(project_root) / repair_plan_path)
        output_path = str(Path(project_root) / output_path)
    plan = _load_json(repair_plan_path)
    review_skipped = len((plan.get("candidates") or [])) - len(_actionable_candidates(plan))
    tex_path = Path(main_tex)
    original_bytes = tex_path.read_bytes() if tex_path.is_file() else b""
    original_tex = original_bytes.decode("utf-8")
    source_root = Path(project_root).resolve() if project_root is not None else Path.cwd().resolve() if within(tex_path.resolve(), Path.cwd().resolve()) else tex_path.resolve().parent
    original_sources = {source: source.read_bytes() for source in discover_source_files(source_root, str(tex_path.resolve()))}
    protected_outputs = {tex_path.resolve(), Path(repair_plan_path).resolve(), *original_sources}
    if Path(output_path).resolve() in protected_outputs:
        raise ValueError('repair report cannot replace a manuscript or repair plan input')
    if _plan_uses_conflict_batch(plan):
        
        
        actionable_plan = {"candidates": _actionable_candidates(plan), **{k: v for k, v in plan.items() if k != "candidates"}}
        catastrophic_overflow = _has_catastrophic_overflow_candidates(actionable_plan)
        if catastrophic_overflow:
            batched_plan = {
                "candidates": [
                    c
                    for c in sorted(
                        (c for c in actionable_plan["candidates"] if str(c.get("defect_family") or "") in {"D1", "D2", "D3"}),
                        key=lambda c: -float(c.get("overflow_amount") or 0.0),
                    )
                ][:max_candidates]
            }
            selection_reason = "catastrophic_d_overflow_preempts_conflict_batch"
        else:
            batched_plan = {"candidates": _select_conflict_batch(plan, max_candidates=max_candidates)}
            selection_reason = "nonconflicting_batch"
        float_defects = _build_float_defects(batched_plan, max_candidates=max_candidates)
        overflow_defects = _build_overflow_defects(
            batched_plan,
            main_tex=main_tex,
            max_candidates=max_candidates,
        )
        space_defects = _build_space_util_defects(batched_plan, max_candidates=max_candidates, column_type=column_type)
        consistency_defects = _build_consistency_defects(batched_plan, max_candidates=max_candidates)
        urgent_overflow_first = False
        execution_plan = batched_plan
    else:
        actionable_plan = {"candidates": _actionable_candidates(plan), **{k: v for k, v in plan.items() if k != "candidates"}}
        catastrophic_overflow = _has_catastrophic_overflow_candidates(actionable_plan)
        urgent_overflow_first = (
            _has_urgent_overflow_candidates(actionable_plan) and not _has_visual_b2_width_candidates(actionable_plan)
        ) or catastrophic_overflow
        if urgent_overflow_first:
            overflow_defects = _build_overflow_defects(
                actionable_plan,
                main_tex=main_tex,
                max_candidates=max_candidates,
            )
            float_defects = [] if overflow_defects else _build_float_defects(actionable_plan, max_candidates=max_candidates)
        else:
            float_defects = _build_float_defects(actionable_plan, max_candidates=max_candidates)
            overflow_defects = [] if float_defects else _build_overflow_defects(
                actionable_plan,
                main_tex=main_tex,
                max_candidates=max_candidates,
            )
        space_defects = [] if (float_defects or overflow_defects) else _build_space_util_defects(
            actionable_plan,
            max_candidates=max_candidates,
            column_type=column_type,
        )
        consistency_defects = _build_consistency_defects(actionable_plan, max_candidates=max_candidates)
        selection_reason = (
            "catastrophic_d_overflow_preempts_visual_b2_width"
            if catastrophic_overflow
            else (
                "urgent_d_overflow_without_visual_b2_width_candidate"
                if urgent_overflow_first
                else "default_float_first_order"
            )
        )
        execution_plan = actionable_plan

    report: Dict[str, Any] = {
        "schema_version": "1.0",
        "generated_at": datetime.now().isoformat(),
        "canonical_execution_entry": CANONICAL_EXECUTION_ENTRY,
        "execution_manifest": canonical_execution_manifest(),
        "repair_plan": repair_plan_path,
        "main_tex": main_tex,
        "selected_candidates": {
            "float": float_defects,
            "overflow": overflow_defects,
            "space_util": space_defects,
            "consistency": consistency_defects,
        },
        "b2_width_selected_candidates": _summarize_selected_b2_width_candidates(float_defects),
        "selection_policy": {
            "urgent_overflow_first": urgent_overflow_first,
            "reason": selection_reason,
            "review_skipped": review_skipped,
        },
        "applied_count": 0,
        "status": "noop",
        "fix_report": {},
        "overflow_report": {},
        "space_report": {},
        "consistency_report": {},
        "global_report": {},
    }

    if float_defects:
        fix_report = execute_float_candidates(
            main_tex=main_tex,
            defects=float_defects,
            column_type=column_type,
        )
        report["fix_report"] = fix_report
        report["applied_count"] = len(_effective_changes(fix_report))
        report["status"] = fix_report.get("status") or "partial"

    if overflow_defects:
        overflow_report = execute_overflow_candidates(
            main_tex=main_tex,
            defects=overflow_defects,
        )
        report["overflow_report"] = overflow_report
        overflow_changes = _effective_changes(overflow_report)
        report["applied_count"] += len(overflow_changes)
        if report["status"] == "noop":
            report["status"] = overflow_report.get("status") or "partial"
        elif overflow_changes:
            report["status"] = "partial" if report["status"] != "success" or overflow_report.get("unresolved") else "success"
    elif float_defects:
        report["overflow_report"] = _build_deferred_skill_report(
            "overflow-repair",
            "deferred D-class overflow actions until selected B-class float sizing/placement candidates are resolved",
        )

    if space_defects:
        space_report = execute_space_util_candidates(
            main_tex=main_tex,
            defects=space_defects,
            target_pages=target_pages,
            column_type=column_type,
        )
        report["space_report"] = space_report
        space_changes = _effective_changes(space_report)
        report["applied_count"] += len(space_changes)
        if report["status"] == "noop":
            report["status"] = space_report.get("status") or "partial"
        elif space_changes:
            report["status"] = "partial" if report["status"] != "success" or space_report.get("unresolved") else "success"
    elif float_defects:
        report["space_report"] = _build_deferred_skill_report(
            "space-util-fixer",
            "deferred A-class space actions until selected B-class float sizing/placement candidates are resolved",
        )
    elif overflow_defects:
        report["space_report"] = _build_deferred_skill_report(
            "space-util-fixer",
            "deferred A-class space actions until selected D-class overflow candidates are resolved",
        )

    if consistency_defects:
        consistency_report = execute_consistency_candidates(
            main_tex=main_tex,
            defects=consistency_defects,
        )
        report["consistency_report"] = consistency_report
        consistency_changes = _effective_changes(consistency_report)
        report["applied_count"] += len(consistency_changes)
        if report["status"] == "noop":
            report["status"] = consistency_report.get("status") or "partial"
        elif consistency_changes:
            report["status"] = "partial" if report["status"] != "success" else "success"

    global_actions = _build_global_actions(execution_plan, max_candidates=max_candidates)
    if global_actions:
        if _has_float_priority_candidates(execution_plan):
            global_report = _build_deferred_global_report(
                "deferred global text actions until B1/B2 float placement and sizing candidates are resolved"
            )
        elif overflow_defects:
            global_report = _build_deferred_global_report(
                "deferred global text actions until D-class overflow candidates are resolved"
            )
        elif space_defects:
            global_report = _build_deferred_global_report(
                "deferred global text actions until A-class space-util candidates are resolved"
            )
        else:
            global_report = _execute_global_actions(main_tex=main_tex, actions=global_actions)
        report["global_report"] = global_report
        report["applied_count"] += int(global_report.get("applied_count") or 0)
        if report["status"] == "noop":
            report["status"] = global_report.get("status") or "partial"
        elif global_report.get("applied_count"):
            report["status"] = "partial" if report["status"] != "success" or global_report.get("unresolved") else "success"

    report = _enforce_structure_integrity(
        main_tex=main_tex,
        original_tex=original_tex,
        report=report,
        original_bytes=original_bytes,
        original_sources=original_sources,
        layout_policy_approvals=layout_policy_approvals,
        project_root=source_root, review_context=review_context, source_reviews=source_reviews,
    )

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute a bounded repair plan subset")
    parser.add_argument("repair_plan")
    parser.add_argument("main_tex")
    parser.add_argument("--output", default="data/repair_execution_report.json")
    parser.add_argument("--column-type", default=None)
    parser.add_argument("--target-pages", type=int, default=None)
    parser.add_argument("--max-candidates", type=int, default=5)
    parser.add_argument("--layout-policy-approvals", default=None, help="Separate host-reviewed exact spacing/endmatter evidence JSON")
    parser.add_argument("--root", type=Path, default=None, help="Project root for nested sources and explicit review evidence")
    parser.add_argument("--layout-review-json", action="append", default=[], help="Strict current-source/PDF/all-pages bound layout review; repeat for changed files")
    parser.add_argument("--source-review-json", action="append", default=[], help="Explicit classified-source uncertainty review; repeat for changed files")
    args = parser.parse_args()
    main = Path(args.main_tex)
    root = args.root.resolve() if args.root is not None else Path.cwd().resolve() if within(main.resolve(), Path.cwd().resolve()) else main.resolve().parent
    main = (root / main).resolve() if args.root is not None else main.resolve()
    try:
        output = (root / args.output).resolve() if args.root is not None else Path(args.output).resolve()
        review_inputs = [root / value for value in (
            args.layout_policy_approvals, *args.layout_review_json, *args.source_review_json) if value]
        protected = {main, (root / args.repair_plan).resolve(),
                     *(path.resolve() for path in review_inputs),
                     *(path.resolve() for path in discover_source_files(root, str(main)))}
        if output in protected:
            raise ValueError('repair report cannot replace a manuscript, repair plan or review input')
        if args.layout_review_json and args.layout_policy_approvals:
            raise ValueError("use strict --layout-review-json separately from legacy --layout-policy-approvals")
        approvals, context, source_reviews = _explicit_review_inputs(root, str(main), args.layout_review_json, args.source_review_json)
        if args.layout_policy_approvals:
            approvals = _load_json(str(root / args.layout_policy_approvals)).get("approvals") or []
    except (OSError, ValueError, TypeError, UnicodeError) as error:
        parser.error(str(error))

    report = execute_repair_plan(
        repair_plan_path=args.repair_plan,
        main_tex=str(main),
        output_path=str(root / args.output) if args.root is not None else args.output,
        column_type=args.column_type,
        target_pages=args.target_pages,
        max_candidates=args.max_candidates,
        layout_policy_approvals=approvals,
        project_root=root, review_context=context, source_reviews=source_reviews,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
