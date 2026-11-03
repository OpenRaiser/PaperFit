#!/usr/bin/env python3
"""把 overfull 警告拆成可定位、可排序的结构化缺陷对象。"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from multi_file_overfull import resolve_overfull_to_source
except ModuleNotFoundError:  # package import
    from .multi_file_overfull import resolve_overfull_to_source

OVERFLOW_OBJECT_SCHEMA_VERSION = "1.0"

SEVERE_OVERFLOW_PT = 100.0


_ZONE_VISIBILITY_WEIGHT = {
    "title": 1.5,
    "abstract": 1.3,
    "display_math": 1.4,
    "table": 1.4,
    "caption": 1.3,
    "figure": 1.2,
    "body": 1.0,
    "unknown": 1.0,
}

_URL_RE = re.compile(r"(?:https?://|doi:|10\.\d{4,9}/)\S+", re.IGNORECASE)
_MBOX_RE = re.compile(r"\\mbox\{|\\hbox\{|\\makebox\[[^\]]*\]\{")
_INLINE_MATH_RE = re.compile(r"\$[^$\n]+\$")
_LONG_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_/.-]{29,}")


@dataclass
class OverflowObject:
    """一个可定位、可排序的溢出缺陷对象。"""

    overflow_id: str
    page: int
    source_file: str
    source_line: Optional[int]
    width_pt: float
    token_type: str
    token_text: str  # 安全摘要：截断到 80 字符，不含敏感内容
    severity: str  # critical | major | minor
    severity_score: float
    source_anchor: Optional[Dict[str, Any]] = None
    repair_family: str = "D"
    zone: str = "unknown"
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["schema_version"] = OVERFLOW_OBJECT_SCHEMA_VERSION
        return data


def _classify_token_type(line_text: str, full_context: str) -> Tuple[str, str]:
    """返回 (token_type, token 摘要)。full_context 是警告行对应的源码上下文。"""
    if not line_text:
        return "unknown", ""
    if _URL_RE.search(line_text):
        match = _URL_RE.search(line_text)
        return "url_or_doi", match.group(0)[:80] if match else ""
    if _MBOX_RE.search(line_text):
        match = _MBOX_RE.search(line_text)
        return "mbox_or_hbox", line_text.strip()[:80]
    long_token = _LONG_TOKEN_RE.search(line_text)
    if long_token and len(long_token.group(0)) >= 30:
        return "long_token", long_token.group(0)[:80]
    if "$" in line_text and _INLINE_MATH_RE.search(line_text):
        return "inline_math", line_text.strip()[:80]
    if any(env in full_context for env in ("\\begin{equation", "\\begin{align", "\\begin{gather", "\\[", "displaymath")):
        return "display_math", line_text.strip()[:80]
    if "includegraphics" in full_context:
        return "graphics_width", line_text.strip()[:80]
    if any(env in full_context for env in ("\\begin{tabular", "\\begin{tabularx", "\\toprule", "\\midrule")):
        return "table_width", line_text.strip()[:80]
    if re.search(r"\\[a-zA-Z]+box", line_text):
        return "macro_box", line_text.strip()[:80]
    return "unknown", line_text.strip()[:80]


def _classify_zone(source_lines: List[str], line_number: Optional[int]) -> str:
    """按行号就近找分区信号，估计溢出所在区域。"""
    if line_number is None or line_number <= 0:
        return "unknown"
    start = max(0, line_number - 1)
    if start >= len(source_lines):
        return "unknown"
    if re.search(r"\\(sub)*section\{|\\title\{", source_lines[start]):
        return "title"
    near = "\n".join(source_lines[max(0, start - 15) : start + 2]).lower()
    if "\\caption" in "\n".join(source_lines[max(0, start - 6) : start + 2]).lower():
        return "caption"
    if "\\begin{tabular" in near or "\\toprule" in near:
        return "table"
    if "\\includegraphics" in near or "\\begin{figure" in near:
        return "figure"
    if "\\begin{equation" in near or "\\begin{align" in near:
        return "display_math"
    # abstract：向上扫到 \begin{document} 或 200 行为止
    for offset in range(start, max(0, start - 200), -1):
        line = source_lines[offset]
        if "\\begin{abstract" in line or "abstract" in line.lower()[:20]:
            return "abstract"
        if "\\end{abstract" in line or "\\section" in line or "\\maketitle" in line:
            break
    return "body"


def _anchor_confidence(source_file: str, line_number: Optional[int], project_root: Optional[Path]) -> float:
    """锚点置信度：文件存在且行号在文件范围内 → 1.0；只解析出文件 → 0.6；都没有 → 0.2。"""
    if line_number is None:
        return 0.6 if source_file else 0.2
    if not project_root:
        return 0.6
    path = Path(project_root) / source_file
    if not path.is_file():
        return 0.3
    try:
        total = path.read_text(encoding="utf-8", errors="replace").count("\n") + 1
    except OSError:
        return 0.3
    return 1.0 if 1 <= line_number <= total else 0.4


def build_overflow_objects(
    rule_report: Dict[str, Any],
    project_root: Path,
    main_tex: str = "main.tex",
    max_objects: int = 200,
) -> List[OverflowObject]:
    """从 rule_report 的 overfull_hbox 警告构建结构化溢出对象（按 severity_score 降序）。"""

    project_root = Path(project_root)
    source_cache: Dict[str, List[str]] = {}

    def _load_lines(rel: str) -> List[str]:
        if rel not in source_cache:
            path = project_root / rel
            try:
                source_cache[rel] = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                source_cache[rel] = []
        return source_cache[rel]

    objects: List[OverflowObject] = []
    warnings = rule_report.get("overfull_hbox") or []
    if not warnings:
        summary = rule_report.get("summary") or {}
        total = int(summary.get("overfull_hbox_total") or 0)
        if total and len(warnings) < total:
            # rule_report 只有总数没有明细时无法对象化（调用方应传完整报告）
            return []

    # 多文件解析：日志行号 → 实际 include 文件（一次解析建索引）
    # Local line numbers repeat across includes. Match each warning occurrence,
    # not the last file that happened to report that line number.
    resolutions_by_warning = defaultdict(deque)
    log_file = str(rule_report.get("log_file") or "")
    if log_file:
        log_path = Path(log_file)
        if not log_path.is_file() and (project_root / log_file).is_file():
            log_path = project_root / log_file
        if log_path.is_file():
            try:
                log_text = log_path.read_text(encoding="utf-8", errors="replace")
                for resolution in resolve_overfull_to_source(log_text, project_root, main_tex) or []:
                    line_start = resolution.get("line_start")
                    if line_start is not None:
                        key = (int(line_start), int(resolution.get("line_end") or line_start),
                               float(resolution.get("overflow_pt") or 0.0))
                        resolutions_by_warning[key].append(resolution)
            except Exception:
                pass

    for idx, warning in enumerate(warnings):
        try:
            width_pt = float(warning.get("overflow_pt") or 0.0)
        except (TypeError, ValueError):
            width_pt = 0.0
        lines_field = str(warning.get("lines") or "").strip()
        line_number: Optional[int] = None
        line_end: Optional[int] = None
        if lines_field:
            head = lines_field.split("--", 1)[0].strip()
            try:
                line_number = int(head)
                line_end = int(lines_field.split("--", 1)[-1].strip())
            except ValueError:
                line_number = None
        page = int(warning.get("page") or 0)

        source_file, resolved_line = main_tex, line_number
        key = (line_number, line_end, width_pt)
        matches = resolutions_by_warning.get(key)
        resolution = matches.popleft() if matches else None
        if resolution:
            source_file = str(resolution.get("file") or main_tex)
            resolved_line = resolution.get("line_start") or line_number
        # A parser/host may already supply the file identity. Keep it even when
        # the legacy log navigator cannot parse that particular warning form.
        anchor = warning.get("source_anchor") or {}
        explicit_file = warning.get("source_file") or anchor.get("file")
        if explicit_file:
            source_file = str(explicit_file)
            resolved_line = anchor.get("start_line") or resolved_line

        source_lines = _load_lines(source_file)
        line_text = source_lines[resolved_line - 1] if resolved_line and 0 < resolved_line <= len(source_lines) else ""
        context = "\n".join(source_lines[max(0, (resolved_line or 1) - 4) : (resolved_line or 1) + 3])
        token_type, token_text = _classify_token_type(line_text, context)
        zone = _classify_zone(source_lines, resolved_line)
        confidence = _anchor_confidence(source_file, resolved_line, project_root)
        visibility = _ZONE_VISIBILITY_WEIGHT.get(zone, 1.0)
        severity_score = width_pt * visibility * confidence

        if width_pt >= SEVERE_OVERFLOW_PT:
            severity = "critical"
        elif width_pt >= 10.0:
            severity = "major"
        else:
            severity = "minor"

        objects.append(
            OverflowObject(
                overflow_id=f"ovf-{idx:03d}",
                page=page,
                source_file=source_file,
                source_line=resolved_line,
                width_pt=round(width_pt, 2),
                token_type=token_type,
                token_text=token_text,
                severity=severity,
                severity_score=round(severity_score, 2),
                source_anchor=(
                    {"file": source_file, "start_line": resolved_line, "end_line": line_end}
                    if resolved_line
                    else None
                ),
                repair_family="D",
                zone=zone,
                extra={"subtype": str(warning.get("subtype") or ""), "warning_index": idx},
            )
        )

    objects.sort(key=lambda obj: -obj.severity_score)
    return objects[:max_objects]


def index_by_warning(objects: List[OverflowObject]) -> Dict[int, OverflowObject]:
    """按 rule_report 中 overfull_hbox 列表的下标建索引，供生成器回填字段。"""
    return {int(obj.extra.get("warning_index", -1)): obj for obj in objects}


def severe_overflow_count(objects: List[OverflowObject]) -> int:
    return sum(1 for obj in objects if obj.width_pt >= SEVERE_OVERFLOW_PT)


def total_overflow_pt(objects: List[OverflowObject]) -> float:
    return round(sum(obj.width_pt for obj in objects), 2)
