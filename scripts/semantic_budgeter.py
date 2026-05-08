#!/usr/bin/env python3
"""
semantic_budgeter.py - Controlled sentence-level budget planner for PaperFit.

Goals:
1. Estimate and execute bounded semantic edits for shorten/expand tasks.
2. Enforce hard protection for C2/C3/C4 content classes.
3. Produce an auditable patch report with per-edit deltas and risk notes.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from content_integrity_check import (
    classify_content_type,
    compute_content_diff,
    count_words,
    extract_academic_content,
    structure_regression_reasons,
)
from transactional_patch import atomic_write_text


FORBIDDEN_ENV_PATTERNS = [
    r"\\begin\{table\*?\}",
    r"\\begin\{figure\*?\}",
    r"\\begin\{tabular",
    r"\\begin\{equation\*?\}",
    r"\\begin\{align\*?\}",
    r"\\begin\{thebibliography\}",
    r"\\bibitem\{",
]

SHORTEN_PHRASE_RULES = [
    (r"\bin order to\b", "to"),
    (r"\bdue to the fact that\b", "because"),
    (r"\ba large number of\b", "many"),
    (r"\bat the present time\b", "now"),
    (r"\bit is worth noting that\b", ""),
    (r"\bit should be emphasized that\b", ""),
]

EXPAND_SENTENCES = [
    "This point clarifies why the design choices in this section are complementary.",
    "Taken together, this discussion improves the interpretability of the presentation.",
    "From a writing perspective, this connection makes the technical narrative easier to follow.",
]


@dataclass
class Paragraph:
    start: int
    end: int
    text: str
    section: str
    editable: bool
    reason: str


@dataclass
class EditAction:
    action_id: str
    type: str
    section: str
    start: int
    end: int
    before: str
    after: str
    delta_words: int
    risk_score: float
    techniques: List[str]
    blocked: bool = False
    blocked_reason: str = ""


def _load_yaml(path: Path) -> Dict[str, Any]:
    if yaml is None:
        return {}
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def _find_body_window(tex: str) -> Tuple[int, int]:
    begin = re.search(r"\\begin\{document\}", tex)
    if not begin:
        return 0, len(tex)
    start = begin.end()
    end_candidates = [
        re.search(r"\\bibliography\{", tex[start:]),
        re.search(r"\\begin\{thebibliography\}", tex[start:]),
        re.search(r"\\end\{document\}", tex[start:]),
    ]
    end = len(tex)
    for m in end_candidates:
        if m:
            end = min(end, start + m.start())
    return start, end


def _section_spans(tex: str, body_start: int, body_end: int) -> List[Tuple[int, str]]:
    spans: List[Tuple[int, str]] = []
    pattern = re.compile(r"\\(?:sub)*section\*?\{([^}]+)\}")
    for m in pattern.finditer(tex[body_start:body_end]):
        spans.append((body_start + m.start(), m.group(1).strip().lower()))
    if not spans:
        return [(body_start, "body")]
    return spans


def _section_at(pos: int, section_spans: List[Tuple[int, str]]) -> str:
    current = section_spans[0][1]
    for p, name in section_spans:
        if pos >= p:
            current = name
        else:
            break
    return current


def _contains_forbidden_env(paragraph: str) -> bool:
    return any(re.search(p, paragraph, flags=re.IGNORECASE) for p in FORBIDDEN_ENV_PATTERNS)


def _contains_float_reference_anchor(text: str) -> bool:
    return bool(
        re.search(
            r"\\(?:ref|autoref|cref|Cref)\{(?:fig|tab):[^}]+\}",
            text,
        )
    )


def _extract_paragraphs(tex: str, forbidden_sections: List[str]) -> List[Paragraph]:
    body_start, body_end = _find_body_window(tex)
    body = tex[body_start:body_end]
    section_spans = _section_spans(tex, body_start, body_end)
    paragraphs: List[Paragraph] = []

    for m in re.finditer(r"\n\s*\n", body):
        pass

    start_local = 0
    splits = list(re.finditer(r"\n\s*\n", body))
    for split in splits + [None]:
        end_local = split.start() if split else len(body)
        chunk = body[start_local:end_local]
        abs_start = body_start + start_local
        abs_end = body_start + end_local
        start_local = split.end() if split else len(body)

        if not chunk.strip():
            continue

        section = _section_at(abs_start, section_spans)
        leading_offset, normalized_chunk = _strip_leading_structural_commands(chunk)
        stripped = normalized_chunk.strip()
        abs_start += leading_offset

        editable = True
        reason = "ok"
        if section in forbidden_sections:
            editable = False
            reason = f"section '{section}' is forbidden"
        elif stripped.startswith("\\"):
            editable = False
            reason = "latex command paragraph"
        elif _contains_forbidden_env(stripped):
            editable = False
            reason = "contains protected environment"
        elif _contains_float_reference_anchor(stripped):
            editable = False
            reason = "contains protected float reference anchor"

        paragraphs.append(
            Paragraph(
                start=abs_start,
                end=abs_start + len(normalized_chunk),
                text=normalized_chunk,
                section=section,
                editable=editable,
                reason=reason,
            )
        )
    return paragraphs


def _strip_leading_structural_commands(chunk: str) -> Tuple[int, str]:
    """
    Remove leading sectioning or label-only lines from a paragraph chunk so
    semantic edits can target the actual prose.
    Returns: (char_offset_removed, normalized_chunk)
    """
    lines = chunk.splitlines(keepends=True)
    offset = 0
    idx = 0
    pattern = re.compile(r"^\s*\\(?:sub)*section\*?\{[^}]+\}\s*$")
    while idx < len(lines):
        raw = lines[idx]
        if not raw.strip():
            offset += len(raw)
            idx += 1
            continue
        if pattern.match(raw.strip()):
            offset += len(raw)
            idx += 1
            continue
        if re.match(r"^\s*\\label\{[^}]+\}\s*$", raw.strip()):
            offset += len(raw)
            idx += 1
            continue
        break
    if idx >= len(lines):
        return 0, chunk
    return offset, "".join(lines[idx:])


def _word_delta(before: str, after: str) -> int:
    b = count_words(extract_academic_content(before))
    a = count_words(extract_academic_content(after))
    return a - b


def _extract_protected_tokens(text: str) -> Dict[str, List[str]]:
    tokens = {
        "cite": re.findall(r"\\cite\{[^}]+\}", text),
        "ref": re.findall(r"\\(?:ref|autoref|cref|Cref)\{[^}]+\}", text),
        "label": re.findall(r"\\label\{[^}]+\}", text),
        "numbers": re.findall(r"\b\d+(?:\.\d+)?%?\b", extract_academic_content(text)),
    }
    return tokens


def _tokens_unchanged(before: str, after: str) -> bool:
    b = _extract_protected_tokens(before)
    a = _extract_protected_tokens(after)
    return b == a


def _risk_score(edit_type: str, delta_words: int, section: str) -> float:
    base = 0.2 if edit_type == "shorten" else 0.35
    size_penalty = min(0.5, abs(delta_words) / 60.0)
    section_penalty = 0.2 if section in {"conclusion", "conclusions"} else 0.0
    return round(min(1.0, base + size_penalty + section_penalty), 3)


def _shorten_text(text: str, filler_phrases: List[str]) -> Tuple[str, List[str]]:
    updated = text
    techniques: List[str] = []

    for phrase in filler_phrases:
        patt = re.compile(re.escape(phrase), flags=re.IGNORECASE)
        new_text, count = patt.subn("", updated)
        if count > 0:
            updated = new_text
            techniques.append("remove_filler_phrase")

    for patt, repl in SHORTEN_PHRASE_RULES:
        new_text, count = re.subn(patt, repl, updated, flags=re.IGNORECASE)
        if count > 0:
            updated = new_text
            techniques.append("phrase_compaction")

    new_text, count = re.subn(r"\b(very|quite|extremely|highly)\b\s*", "", updated, flags=re.IGNORECASE)
    if count > 0:
        updated = new_text
        techniques.append("remove_redundant_modifier")

    updated = re.sub(r"\s{2,}", " ", updated)
    updated = re.sub(r"\s+([,.;:])", r"\1", updated)
    return updated, techniques


def _expand_text(text: str) -> Tuple[str, List[str]]:
    stripped = text.rstrip()
    if not re.search(r"[.!?]\s*$", stripped):
        stripped += "."
    append = EXPAND_SENTENCES[hash(stripped) % len(EXPAND_SENTENCES)]
    return f"{stripped} {append}\n", ["add_explanatory_sentence"]


def _is_c234(text: str) -> bool:
    # Strict hard-protection checks for semantic edits.
    if re.search(r"\\(?:cite|ref|autoref|cref|Cref|label)\{", text):
        return True  # C2
    if re.search(r"\$[^$]*\$|\\\[[^\]]*\\\]|\\begin\{(?:equation|align|gather|multline)\*?\}", text):
        return True  # C3
    academic = extract_academic_content(text)
    if re.search(
        r"(\b\d+(?:\.\d+)?%?\b.*\b(?:accuracy|precision|recall|f1|auc|rmse|mae|loss|p)\b)|"
        r"(\b(?:accuracy|precision|recall|f1|auc|rmse|mae|loss|p)\b.*\b\d+(?:\.\d+)?%?\b)",
        academic,
        flags=re.IGNORECASE,
    ):
        return True  # C4
    t = classify_content_type(text).get("primary_type")
    return t in {"C2", "C3", "C4"}


def _apply_edit_guardrails(
    before: str,
    after: str,
    max_word_change_per_edit: int,
) -> Tuple[bool, str, int]:
    delta = _word_delta(before, after)
    if abs(delta) > max_word_change_per_edit:
        return False, f"delta_words {delta} exceeds per-edit budget {max_word_change_per_edit}", delta
    if _is_c234(before) or _is_c234(after):
        return False, "paragraph classified as C2/C3/C4", delta
    if not _tokens_unchanged(before, after):
        return False, "protected tokens changed (cite/ref/label/number)", delta
    return True, "ok", delta


def _canonical_section(name: str) -> str:
    s = (name or "").lower()
    if "related" in s:
        return "related_work"
    if "intro" in s:
        return "introduction"
    if "discuss" in s:
        return "discussion"
    if "conclu" in s:
        return "conclusion"
    if "method" in s or "approach" in s:
        return "method"
    if "experiment" in s:
        return "experiments"
    if "result" in s:
        return "results"
    return "body"


def _derive_target_from_page_metrics(
    page_metrics: Dict[str, Any],
    explicit_target: Optional[int],
) -> Tuple[int, List[Dict[str, Any]], Dict[str, int], Dict[str, Any]]:
    if explicit_target is not None:
        return explicit_target, [], {}, {"source": "explicit"}

    by_page = page_metrics.get("by_page") or []
    page_budget: List[Dict[str, Any]] = []
    total_expand_words = 0
    for item in by_page:
        ratio = float(item.get("max_void_ratio") or 0.0)
        if ratio < 0.2:
            continue
        quota = int(round(ratio * 180))
        if quota <= 0:
            continue
        page_budget.append(
            {
                "page_index": item.get("page_index"),
                "max_void_ratio": round(ratio, 4),
                "quota_words": quota,
            }
        )
        total_expand_words += quota

    if total_expand_words == 0:
        return 0, page_budget, {}, {"source": "metrics", "reason": "no_page_void_signal"}

    # Conservative cap to avoid over-expansion in a single round.
    target = min(220, total_expand_words)
    section_quota = {
        "discussion": int(round(target * 0.5)),
        "conclusion": int(round(target * 0.3)),
        "related_work": max(0, target - int(round(target * 0.5)) - int(round(target * 0.3))),
    }
    meta = {
        "source": "metrics",
        "pages_flagged": len(page_budget),
        "raw_target_words": total_expand_words,
    }
    return target, page_budget, section_quota, meta


def _load_page_metrics(metrics_path: Optional[Path]) -> Dict[str, Any]:
    if not metrics_path or not metrics_path.exists():
        return {"by_page": []}
    data = json.loads(metrics_path.read_text(encoding="utf-8"))

    # state.json format
    if isinstance(data, dict) and "cv_signals_summary" in data:
        by_page = (data.get("cv_signals_summary") or {}).get("by_page") or []
        return {"by_page": by_page, "source": "state"}

    # state slice / direct summary format
    if isinstance(data, dict) and "by_page" in data and "a5_candidate_count" in data:
        return {"by_page": data.get("by_page") or [], "source": "cv_summary"}

    # detect_column_void report format
    if isinstance(data, dict) and "pages" in data:
        by_page: List[Dict[str, Any]] = []
        for page in data.get("pages") or []:
            cands = page.get("a5_candidates") or []
            ratios = [float(c.get("void_ratio_of_column") or 0.0) for c in cands]
            if not ratios:
                continue
            by_page.append(
                {
                    "page_index": page.get("page_index"),
                    "max_void_ratio": max(ratios),
                    "a5_candidate_count": len(cands),
                }
            )
        return {"by_page": by_page, "source": "column_void_report"}

    return {"by_page": []}


def plan_and_apply_semantic_budget(
    tex_content: str,
    target_word_delta: int,
    max_edits: int,
    writing_rules: Dict[str, Any],
    section_quota: Optional[Dict[str, int]] = None,
) -> Tuple[str, List[EditAction], Dict[str, Any]]:
    semantic_cfg = (writing_rules.get("semantic_polish") or {})
    forbidden_sections = [s.lower() for s in semantic_cfg.get("forbidden_edit_sections", ["abstract", "acknowledgments"])]
    preferred_shorten = [s.lower() for s in semantic_cfg.get("preferred_shorten_sections", [])]
    preferred_expand = [s.lower() for s in semantic_cfg.get("preferred_expand_sections", [])]
    max_word_change_per_edit = int(semantic_cfg.get("max_word_change_per_edit", 15))
    filler_phrases = list((writing_rules.get("forbidden") or {}).get("filler_phrases", []))

    paragraphs = _extract_paragraphs(tex_content, forbidden_sections)
    editable = [p for p in paragraphs if p.editable]

    if target_word_delta < 0:
        direction = "shorten"
        editable.sort(key=lambda p: (p.section not in preferred_shorten, -len(p.text)))
    elif target_word_delta > 0:
        direction = "expand"
        editable.sort(key=lambda p: (p.section not in preferred_expand, -len(p.text)))
    else:
        return tex_content, [], {"direction": "none", "budget_hit": True}

    working = tex_content
    total_delta = 0
    actions: List[EditAction] = []
    quota_remaining = dict(section_quota or {})

    for idx, p in enumerate(editable):
        if len(actions) >= max_edits:
            break
        if direction == "shorten" and total_delta <= target_word_delta:
            break
        if direction == "expand" and total_delta >= target_word_delta:
            break
        sec = _canonical_section(p.section)
        if quota_remaining and quota_remaining.get(sec, 0) <= 0:
            continue

        before = working[p.start:p.end]
        if direction == "shorten":
            candidate, techniques = _shorten_text(before, filler_phrases)
        else:
            candidate, techniques = _expand_text(before)

        if candidate == before:
            continue

        ok, reason, delta = _apply_edit_guardrails(before, candidate, max_word_change_per_edit)
        action = EditAction(
            action_id=f"edit_{idx+1}",
            type=direction,
            section=p.section,
            start=p.start,
            end=p.end,
            before=before,
            after=candidate,
            delta_words=delta,
            risk_score=_risk_score(direction, delta, p.section),
            techniques=techniques,
            blocked=not ok,
            blocked_reason="" if ok else reason,
        )
        actions.append(action)

        if not ok:
            continue

        working = working[:p.start] + candidate + working[p.end:]
        shift = len(candidate) - len(before)
        total_delta += delta
        if quota_remaining:
            quota_remaining[sec] = max(0, quota_remaining.get(sec, 0) - abs(delta))

        for q in editable:
            if q.start > p.start:
                q.start += shift
                q.end += shift
        for q in paragraphs:
            if q.start > p.start:
                q.start += shift
                q.end += shift

    summary = {
        "direction": direction,
        "target_word_delta": target_word_delta,
        "achieved_word_delta": total_delta,
        "budget_hit": (total_delta <= target_word_delta if direction == "shorten" else total_delta >= target_word_delta),
        "applied_edits": len([a for a in actions if not a.blocked]),
        "blocked_edits": len([a for a in actions if a.blocked]),
        "section_quota_remaining": quota_remaining,
    }
    return working, actions, summary


def build_report(
    tex_file: Path,
    output_file: Path,
    original: str,
    updated: str,
    edits: List[EditAction],
    summary: Dict[str, Any],
    applied: bool,
) -> Dict[str, Any]:
    diff = compute_content_diff(original, updated)
    report = {
        "timestamp": datetime.now().isoformat(),
        "agent": "semantic-budgeter",
        "file": str(tex_file),
        "applied": applied,
        "summary": summary,
        "integrity": {
            "hash_identical": diff["hash_comparison"]["identical"],
            "word_change": diff["word_count"]["change"],
            "word_change_percentage": diff["word_count"]["change_percentage"],
            "sentence_added": diff["sentence_changes"]["added_count"],
            "sentence_deleted": diff["sentence_changes"]["deleted_count"],
            "violation_level": diff["violation"]["level"],
            "action_required": diff["action_required"],
        },
        "edits": [asdict(e) for e in edits],
    }
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    return report


def _passes_structure_write_gate(original: str, updated: str) -> Tuple[bool, str]:
    diff = compute_content_diff(original, updated)
    reasons = structure_regression_reasons(diff)
    if reasons:
        return False, "; ".join(reasons)
    return True, "pass"


def run(
    tex_file: Path,
    target_word_delta: Optional[int],
    max_edits: int,
    apply_changes: bool,
    output: Path,
    writing_rules_path: Path,
    page_metrics_path: Optional[Path] = None,
) -> Dict[str, Any]:
    original = tex_file.read_text(encoding="utf-8")
    writing_rules = _load_yaml(writing_rules_path)
    page_metrics = _load_page_metrics(page_metrics_path)
    resolved_target, page_budget, section_quota, target_meta = _derive_target_from_page_metrics(
        page_metrics=page_metrics,
        explicit_target=target_word_delta,
    )

    updated, edits, summary = plan_and_apply_semantic_budget(
        tex_content=original,
        target_word_delta=resolved_target,
        max_edits=max_edits,
        writing_rules=writing_rules,
        section_quota=section_quota,
    )
    summary["target_meta"] = target_meta
    summary["page_budget"] = page_budget
    gate_passed, gate_reason = _passes_structure_write_gate(original, updated)
    summary["write_gate"] = {
        "pass": gate_passed,
        "reason": gate_reason,
    }

    applied = False
    if apply_changes and updated != original:
        if gate_passed:
            atomic_write_text(tex_file, updated, backup_dir=tex_file.parent / "data" / "backups")
            applied = True
        else:
            summary["blocked_reason"] = f"structure_write_gate: {gate_reason}"

    report = build_report(
        tex_file=tex_file,
        output_file=output,
        original=original,
        updated=updated,
        edits=edits,
        summary=summary,
        applied=applied,
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="PaperFit semantic budget planner")
    parser.add_argument("tex_file", help="Path to main .tex file")
    parser.add_argument(
        "--target-word-delta",
        type=int,
        required=False,
        help="Target word delta. Negative=shorten, positive=expand",
    )
    parser.add_argument("--max-edits", type=int, default=6, help="Maximum number of edit actions")
    parser.add_argument("--apply", action="store_true", help="Write changes back to file")
    parser.add_argument(
        "--output",
        default="data/semantic_patch_report.json",
        help="Audit report output path",
    )
    parser.add_argument(
        "--writing-rules",
        default="config/writing_rules.yaml",
        help="Path to writing rules config",
    )
    parser.add_argument(
        "--page-metrics",
        default=None,
        help="Path to state.json or column-void report for page-level utilization input",
    )
    args = parser.parse_args()

    report = run(
        tex_file=Path(args.tex_file),
        target_word_delta=args.target_word_delta,
        max_edits=args.max_edits,
        apply_changes=args.apply,
        output=Path(args.output),
        writing_rules_path=Path(args.writing_rules),
        page_metrics_path=Path(args.page_metrics) if args.page_metrics else None,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"audit_report: {args.output}")


if __name__ == "__main__":
    main()
