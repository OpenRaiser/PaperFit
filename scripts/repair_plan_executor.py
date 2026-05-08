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

from content_integrity_check import compute_content_diff, has_structure_regression
from fixer_registry import (
    CANONICAL_EXECUTION_ENTRY,
    canonical_execution_manifest,
    execute_float_candidates,
    execute_overflow_candidates,
    execute_space_util_candidates,
)
from transactional_patch import atomic_write_text


_DISPLAY_MATH_BEGIN_RE = re.compile(r"\\begin\{(equation\*?|align\*?|multline\*?|gather\*?)\}")
_DISPLAY_MATH_END_RE = re.compile(r"\\end\{(equation\*?|align\*?|multline\*?|gather\*?)\}")


def _display_math_anchor_line(main_tex: str, line_number: Optional[int], tolerance: int = 2) -> Optional[int]:
    if line_number is None or line_number < 1:
        return None

    tex_path = Path(main_tex)
    if not tex_path.is_file():
        return None

    lines = tex_path.read_text(encoding="utf-8").splitlines()
    env_stack: List[tuple[str, int]] = []
    display_ranges: List[tuple[int, int]] = []

    for idx, line in enumerate(lines, start=1):
        begin_match = _DISPLAY_MATH_BEGIN_RE.search(line)
        if begin_match:
            env_stack.append((begin_match.group(1), idx))

        end_match = _DISPLAY_MATH_END_RE.search(line)
        if end_match:
            end_env = end_match.group(1)
            for stack_index in range(len(env_stack) - 1, -1, -1):
                if env_stack[stack_index][0] == end_env:
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

        if defect_family not in {"B1", "B2"} or not label:
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
        }
        if defect_family == "B1":
            defect["ref_page"] = candidate.get("page") or 0
            defect.update(merged_b1_metadata.get(str(label)) or {})
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

    # Keep one strong semantic-distance B1 candidate and up to two clearly narrow
    # B2 figures so late-page objects are not permanently starved by early-page
    # top-5 truncation.
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

    narrow_figure_candidates = [
        candidate
        for candidate in (plan.get("candidates") or [])
        if str(candidate.get("defect_family") or "") == "B2"
        and str(((candidate.get("target") or {}).get("label")) or "").startswith("fig:")
        and (_parse_width_ratio(candidate.get("source_width_spec")) or 1.0) <= 0.5
    ]
    narrow_figure_candidates.sort(
        key=lambda item: (
            _parse_width_ratio(item.get("source_width_spec")) or 1.0,
            int(item.get("page") or 0),
            -int(item.get("priority_score") or 0),
            str(((item.get("target") or {}).get("label")) or ""),
        )
    )
    deduped_narrow_figure_candidates: List[Dict[str, Any]] = []
    seen_narrow_labels: set[str] = set()
    for candidate in narrow_figure_candidates:
        label = str(((candidate.get("target") or {}).get("label")) or "")
        if not label or label in seen_narrow_labels:
            continue
        seen_narrow_labels.add(label)
        deduped_narrow_figure_candidates.append(candidate)
    for candidate in deduped_narrow_figure_candidates[:2]:
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
    seen: set[tuple[str, str, Optional[int], str]] = set()
    saw_overflow_candidate = False

    for candidate in plan.get("candidates") or []:
        defect_id = str(candidate.get("defect_family") or "")
        if defect_id not in {"D1", "D2", "D3"}:
            continue
        saw_overflow_candidate = True

        target = candidate.get("target") or {}
        object_name = str(target.get("label") or target.get("scope") or defect_id)
        line_number = candidate.get("line_number")
        description = str(candidate.get("description") or "").strip()
        if defect_id == "D1" and _line_is_within_display_math(main_tex, line_number):
            defect_id = "D2"
            object_name = "equation_overflow"
        dedupe_key = (defect_id, object_name, line_number, description)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        defects.append(
            {
                "defect_id": defect_id,
                "object": object_name,
                "page": candidate.get("page") or 0,
                "line_number": line_number,
                "description": description,
                "overflow_amount": candidate.get("overflow_amount") or 0,
                "url": candidate.get("url"),
                "subtype": candidate.get("subtype"),
            }
        )
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


def _build_space_util_defects(plan: Dict[str, Any], max_candidates: int) -> List[Dict[str, Any]]:
    defects: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for candidate in plan.get("candidates") or []:
        defect_id = str(candidate.get("defect_family") or "")
        if not defect_id.startswith("A") or defect_id == "A/C":
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


def _has_float_priority_candidates(plan: Dict[str, Any]) -> bool:
    return any(str(candidate.get("defect_family") or "") in {"B1", "B2", "B3"} for candidate in plan.get("candidates") or [])


def _passes_global_content_gate(original_tex: str, repaired_tex: str) -> bool:
    diff = compute_content_diff(original_tex, repaired_tex)
    return (
        diff["hash_comparison"]["identical"]
        or (
            diff["sentence_changes"]["deleted_count"] == 0
            and diff["sentence_changes"]["added_count"] == 0
            and diff["word_count"]["change"] == 0
        )
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


def _has_structure_regression(diff: Dict[str, Any]) -> bool:
    return has_structure_regression(diff)


def _enforce_structure_integrity(
    *,
    main_tex: str,
    original_tex: str,
    report: Dict[str, Any],
) -> Dict[str, Any]:
    tex_path = Path(main_tex)
    if not tex_path.is_file():
        integrity = {
            "checked": False,
            "status": "skipped",
            "reason": f"main tex not found: {main_tex}",
        }
        report["content_integrity"] = integrity
        return report

    final_tex = tex_path.read_text(encoding="utf-8")
    diff = compute_content_diff(original_tex, final_tex)
    integrity = {
        "checked": True,
        "status": "pass",
        "rollback_performed": False,
        "diff": diff,
    }

    if final_tex != original_tex and _has_structure_regression(diff):
        atomic_write_text(tex_path, original_tex, backup_dir=tex_path.parent / "data" / "backups")
        integrity["status"] = "failed"
        integrity["rollback_performed"] = True
        integrity["failure_reasons"] = (diff.get("violation") or {}).get("reasons") or ["structure regression detected"]
        report["status"] = "failed"
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
        report["rollback_reason"] = "structure_integrity_violation"

    report["content_integrity"] = integrity
    return report


def execute_repair_plan(
    repair_plan_path: str,
    main_tex: str,
    output_path: str,
    column_type: Optional[str] = None,
    target_pages: Optional[int] = None,
    max_candidates: int = 5,
) -> Dict[str, Any]:
    plan = _load_json(repair_plan_path)
    tex_path = Path(main_tex)
    original_tex = tex_path.read_text(encoding="utf-8") if tex_path.is_file() else ""
    float_defects = _build_float_defects(plan, max_candidates=max_candidates)
    overflow_defects = _build_overflow_defects(plan, main_tex=main_tex, max_candidates=max_candidates)
    space_defects = _build_space_util_defects(plan, max_candidates=max_candidates)

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
        },
        "applied_count": 0,
        "status": "noop",
        "fix_report": {},
        "overflow_report": {},
        "space_report": {},
        "global_report": {},
    }

    if float_defects:
        fix_report = execute_float_candidates(
            main_tex=main_tex,
            defects=float_defects,
            column_type=column_type,
        )
        report["fix_report"] = fix_report
        report["applied_count"] = len(fix_report.get("changes") or [])
        report["status"] = fix_report.get("status") or "partial"

    if overflow_defects:
        overflow_report = execute_overflow_candidates(
            main_tex=main_tex,
            defects=overflow_defects,
        )
        report["overflow_report"] = overflow_report
        report["applied_count"] += len(overflow_report.get("changes") or [])
        if report["status"] == "noop":
            report["status"] = overflow_report.get("status") or "partial"
        elif overflow_report.get("changes"):
            report["status"] = "partial" if report["status"] != "success" or overflow_report.get("unresolved") else "success"

    if space_defects:
        space_report = execute_space_util_candidates(
            main_tex=main_tex,
            defects=space_defects,
            target_pages=target_pages,
            column_type=column_type,
        )
        report["space_report"] = space_report
        report["applied_count"] += len(space_report.get("changes") or [])
        if report["status"] == "noop":
            report["status"] = space_report.get("status") or "partial"
        elif space_report.get("changes"):
            report["status"] = "partial" if report["status"] != "success" or space_report.get("unresolved") else "success"

    global_actions = _build_global_actions(plan, max_candidates=max_candidates)
    if global_actions:
        if _has_float_priority_candidates(plan):
            global_report = _build_deferred_global_report(
                "deferred global text actions until B1/B2 float placement and sizing candidates are resolved"
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
    args = parser.parse_args()

    report = execute_repair_plan(
        repair_plan_path=args.repair_plan,
        main_tex=args.main_tex,
        output_path=args.output,
        column_type=args.column_type,
        target_pages=args.target_pages,
        max_candidates=args.max_candidates,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
