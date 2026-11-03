from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

OVERFULL_HBOX_PATTERN = re.compile(r"Overfull\s+\\+hbox")
UNDEFINED_REF_PATTERN = re.compile(r"LaTeX Warning: Reference `[^']+'[^\n]*undefined")
UNDEFINED_CITATION_PATTERN = re.compile(r"LaTeX Warning: Citation `[^']+'[^\n]*undefined")
UNDEFINED_CONTROL_PATTERN = re.compile(r"! Undefined control sequence\.")
LABEL_PATTERN = re.compile(r"\\label\{([^}]+)\}")


def _read_text(path: Path, *, errors: str = "strict") -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors=errors)


def _load_benchmark_case(case_dir: Optional[Path]) -> Dict[str, Any]:
    if not case_dir:
        return {}
    path = case_dir / "benchmark_case.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    case = payload.get("benchmark_case")
    return case if isinstance(case, dict) else {}


def _strip_tex_comments(tex: str) -> str:
    """Remove LaTeX comment lines (lines starting with %) to avoid false counts."""
    return re.sub(r"^[ \t]*%[^\n]*\n?", "", tex, flags=re.MULTILINE)


def _tex_signal_counts(tex: str) -> Dict[str, int]:
    clean = _strip_tex_comments(tex)
    return {
        "figure_env": len(re.findall(r"\\begin\{figure\*?\}", clean)),
        "table_env": len(re.findall(r"\\begin\{table\*?\}", clean)),
        "caption": len(re.findall(r"\\caption\{", clean)),
        "label": len(re.findall(r"\\label\{", clean)),
        "includegraphics": len(re.findall(r"\\includegraphics(?:\[[^\]]*\])?\{", clean)),
        "citation": len(re.findall(r"\\cite[a-zA-Z*]*(?:\[[^\]]*\])?(?:\[[^\]]*\])?\{", clean)),
        "bibliography_cmd": len(
            re.findall(r"\\bibliography\{|\\addbibresource\{|\\printbibliography\b|\\begin\{thebibliography\}", clean)
        ),
    }


def _read_project_tex(project_root: Path) -> str:
    if not project_root.exists():
        return ""
    chunks: list[str] = []
    for path in sorted(project_root.rglob("*.tex")):
        rel_parts = path.relative_to(project_root).parts
        if path.name.startswith("._") or any(part in {"data", ".git", "__pycache__"} for part in rel_parts):
            continue
        try:
            chunks.append(f"\n% BEGIN {path.relative_to(project_root).as_posix()}\n")
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return "\n".join(chunks)


def _content_integrity(source_tex: str, final_tex: str) -> Dict[str, Any]:
    source_counts = _tex_signal_counts(source_tex)
    final_counts = _tex_signal_counts(final_tex)
    dropped_keys = [key for key, before in source_counts.items() if final_counts.get(key, 0) < before]
    source_labels = set(LABEL_PATTERN.findall(_strip_tex_comments(source_tex)))
    final_labels = set(LABEL_PATTERN.findall(_strip_tex_comments(final_tex)))
    missing_labels = sorted(source_labels - final_labels)
    return {
        "ok": not dropped_keys and not missing_labels,
        "source_counts": source_counts,
        "final_counts": final_counts,
        "dropped_signal_keys": dropped_keys,
        "missing_labels": missing_labels[:100],
    }


def _semantic_home_hit(source_tex: str, final_tex: str) -> Dict[str, Any]:
    source_labels = set(LABEL_PATTERN.findall(_strip_tex_comments(source_tex)))
    final_labels = set(LABEL_PATTERN.findall(_strip_tex_comments(final_tex)))
    if not source_labels:
        return {"ratio": None, "retained": 0, "total": 0, "ok": True}
    retained = len(source_labels & final_labels)
    total = len(source_labels)
    return {
        "ratio": round(retained / total, 4),
        "retained": retained,
        "total": total,
        "ok": retained == total,
    }


def _crossref_integrity(log_text: str) -> Dict[str, Any]:
    undefined_ref_count = len(UNDEFINED_REF_PATTERN.findall(log_text))
    undefined_citation_count = len(UNDEFINED_CITATION_PATTERN.findall(log_text))
    return {
        "ok": undefined_ref_count == 0 and undefined_citation_count == 0,
        "undefined_ref_count": undefined_ref_count,
        "undefined_citation_count": undefined_citation_count,
    }


def _crossref_delta_integrity(source_log_text: str, final_log_text: str) -> Dict[str, Any]:
    source = _crossref_integrity(source_log_text)
    final = _crossref_integrity(final_log_text)
    new_undefined_ref_count = max(0, final["undefined_ref_count"] - source["undefined_ref_count"])
    new_undefined_citation_count = max(0, final["undefined_citation_count"] - source["undefined_citation_count"])
    return {
        "ok": new_undefined_ref_count == 0 and new_undefined_citation_count == 0,
        "source_undefined_ref_count": source["undefined_ref_count"],
        "source_undefined_citation_count": source["undefined_citation_count"],
        "final_undefined_ref_count": final["undefined_ref_count"],
        "final_undefined_citation_count": final["undefined_citation_count"],
        "new_undefined_ref_count": new_undefined_ref_count,
        "new_undefined_citation_count": new_undefined_citation_count,
    }


def _line_change_count(source_tex: str, final_tex: str) -> int:
    source_lines = source_tex.splitlines()
    final_lines = final_tex.splitlines()
    matcher = difflib.SequenceMatcher(a=source_lines, b=final_lines)
    changed = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        changed += max(i2 - i1, j2 - j1, 1)
    return changed


def _changed_line_ratio(changed_line_count: Optional[int], final_tex: str) -> float | None:
    if changed_line_count is None:
        return None
    final_line_count = max(len(final_tex.splitlines()), 1)
    return round(changed_line_count / final_line_count, 4)


try:
    from common_harness import pdf_page_count
except ImportError:  # pragma: no cover - package import fallback
    from .common_harness import pdf_page_count


def _synthetic_vlm_report(
    *,
    reason: str,
    error_detail: str | None,
    protocol_version: str = "v3",
) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "vlm_score": 1.0,
        "vlm_synthetic": True,
        "vlm_failure_reason": reason,
        "vlm_failure_detail": error_detail,
        "protocol_version": protocol_version,
    }
    if protocol_version == "v3":
        base["scores"] = {
            "overall_professionalism": 1,
            "space_utilization": 1,
            "float_placement_quality": 1,
            "typographic_consistency": 1,
            "visual_balance": 1,
        }
        base["observed_defects"] = []
        base["gate_flags"] = {}
        base["summary"] = (
            f"VLM evaluation skipped ({reason}). "
            "Compile/render failure means no visual quality can be assessed; "
            "minimum score (1.0) is assigned per protocol policy."
        )
    else:
        for dim in (
            "overall_professionalism",
            "space_utilization",
            "float_placement_quality",
            "typographic_consistency",
            "visual_balance",
        ):
            base[dim] = 1
        base["summary"] = (
            f"VLM evaluation skipped ({reason}); "
            "minimum score assigned."
        )
    return base


def basic_evaluation(
    *,
    method_id: str,
    main_tex: Path,
    pdf_path: Optional[Path],
    compile_report: Dict[str, Any],
    render_report: Dict[str, Any],
    case_dir: Optional[Path] = None,
    vlm_absolute_report: Optional[Dict[str, Any]] = None,
    vlm_absolute_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    benchmark_case = _load_benchmark_case(case_dir)
    target_pages = benchmark_case.get("target_pages")
    page_count = pdf_page_count(pdf_path)
    tex_log_path = compile_report.get("tex_log_path")
    tex_log_text = _read_text(Path(tex_log_path), errors="replace") if tex_log_path else ""
    source_main_tex = Path(case_dir or main_tex.parent) / main_tex.name
    source_tex = _read_text(source_main_tex) if source_main_tex.exists() else ""
    final_tex = _read_text(main_tex) if main_tex.exists() else ""
    source_project_tex = _read_project_tex(Path(case_dir)) if case_dir else source_tex
    final_project_tex = _read_project_tex(main_tex.parent) or final_tex

    if source_project_tex:
        content_integrity = _content_integrity(source_project_tex, final_project_tex)
        semantic_home = _semantic_home_hit(source_project_tex, final_project_tex)
        tex_changed = source_tex != final_tex
        changed_line_count = _line_change_count(source_tex, final_tex)
    else:
        content_integrity = {
            "ok": None,
            "source_counts": {},
            "final_counts": _tex_signal_counts(final_project_tex),
            "dropped_signal_keys": [],
            "missing_labels": [],
        }
        semantic_home = {"ratio": None, "retained": 0, "total": 0, "ok": None}
        tex_changed = None
        changed_line_count = None

    crossref_integrity = _crossref_integrity(tex_log_text)
    source_log_text = _read_text(Path(case_dir) / "main.log", errors="replace") if case_dir else ""
    crossref_delta_integrity = (
        _crossref_delta_integrity(source_log_text, tex_log_text)
        if source_log_text and tex_log_text
        else None
    )
    overfull_count = len(OVERFULL_HBOX_PATTERN.findall(tex_log_text))
    undefined_control_count = len(UNDEFINED_CONTROL_PATTERN.findall(tex_log_text))
    page_budget_gap = (page_count - target_pages) if page_count is not None and target_pages is not None else None
    page_budget_gap_abs = abs(page_budget_gap) if page_budget_gap is not None else None
    page_budget_hit = (page_budget_gap_abs == 0) if page_budget_gap_abs is not None else None
    hard_constraint_ok = all(
        value is True
        for value in [
            bool(compile_report.get("success")),
            bool(render_report.get("success")),
            content_integrity.get("ok"),
            semantic_home.get("ok"),
        ]
    ) and undefined_control_count == 0

    payload: Dict[str, Any] = {
        "method_id": method_id,
        "compile_success": bool(compile_report.get("success")),
        "render_success": bool(render_report.get("success")),
        "page_count": page_count,
        "target_pages": target_pages,
        "page_budget_gap": page_budget_gap,
        "page_budget_gap_abs": page_budget_gap_abs,
        "page_budget_hit": page_budget_hit,
        "overfull_count": overfull_count,
        "undefined_ref_count": crossref_integrity["undefined_ref_count"],
        "undefined_citation_count": crossref_integrity["undefined_citation_count"],
        "undefined_control_count": undefined_control_count,
        "crossref_integrity": crossref_integrity,
        "crossref_delta_integrity": crossref_delta_integrity,
        "content_integrity": content_integrity,
        "semantic_home_hit": semantic_home,
        "hard_constraint_ok": hard_constraint_ok,
        "tex_changed": tex_changed,
        "changed_line_count": changed_line_count,
        "changed_line_ratio": _changed_line_ratio(changed_line_count, final_tex),
        "main_tex": str(main_tex),
        "pdf_path": str(pdf_path) if pdf_path and pdf_path.exists() else None,
        "notes": [
            "程序指标现包含 page_budget、hard constraints、overfull、crossref、content_integrity 与 semantic_home_hit。",
            "如提供 vlm_absolute_report，可将标准化后的 VLM 绝对评分结果回填到 evaluation_report。",
        ],
    }
    compile_success = bool(compile_report.get("success"))
    render_success = bool(render_report.get("success"))
    if vlm_absolute_report is not None:
        payload["vlm_absolute"] = _normalize_vlm_absolute_report(
            vlm_absolute_report,
            metadata=vlm_absolute_metadata,
        )
    elif vlm_absolute_metadata is not None and vlm_absolute_metadata.get("vlm_absolute_error"):
        payload["vlm_absolute"] = _synthetic_vlm_report(
            reason="vlm_call_failed",
            error_detail=str(vlm_absolute_metadata.get("vlm_absolute_error", ""))[:200],
            protocol_version="v3",
        )
    elif not compile_success:
        payload["vlm_absolute"] = _synthetic_vlm_report(
            reason="compile_failed",
            error_detail=None,
            protocol_version="v3",
        )
    elif not render_success:
        payload["vlm_absolute"] = _synthetic_vlm_report(
            reason="render_failed",
            error_detail=None,
            protocol_version="v3",
        )
    if vlm_absolute_metadata is not None:
        payload["vlm_absolute_metadata"] = vlm_absolute_metadata
    return payload


def _normalize_vlm_absolute_report(
    payload: Dict[str, Any],
    *,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized = dict(payload)
    normalized.setdefault("protocol_version", "v3")
    return normalized


def _canonicalize_vlm_absolute_report(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "overall_professionalism": payload.get("overall_professionalism"),
        "space_utilization": payload.get("space_utilization"),
        "float_placement_quality": payload.get("float_placement_quality"),
        "typographic_consistency": payload.get("typographic_consistency"),
        "visual_balance": payload.get("visual_balance"),
        "major_issues": payload.get("major_issues"),
        "summary": payload.get("summary"),
    }
