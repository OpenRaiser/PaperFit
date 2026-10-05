#!/usr/bin/env python3
"""
Overflow Fixers Module

处理 Category D：溢出与对齐缺陷
- D1: Overfull hbox（段落文本、表格单元格、公式溢出栏宽）
- D2: 长公式未合理断行
- D3: URL/长标识符溢出

该模块被 code-surgeon-agent 调用，执行对 .tex 源码的精确修改。
所有修复遵循最小修改原则，不改变学术内容。
"""

import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Any

try:
    from content_integrity_check import compute_content_diff, structure_regression_reasons
    from math_safe_breaking import split_safe_additive_terms, verify_split_preserves_tokens
except ImportError:
    compute_content_diff = None
    structure_regression_reasons = None
    from math_safe_breaking import split_safe_additive_terms, verify_split_preserves_tokens

from transactional_patch import atomic_write_text


# ============================================================
# 数据结构定义
# ============================================================

@dataclass
class FixResult:
    """修复结果"""
    defect_id: str
    object_name: str
    action: str
    before: str
    after: str
    page: int = 0
    line_number: Optional[int] = None
    success: bool = False


@dataclass
class OverflowFixReport:
    """修复报告"""
    status: str  # success | partial | failed
    modified_files: List[str] = field(default_factory=list)
    changes: List[FixResult] = field(default_factory=list)
    unresolved: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill": "overflow-repair",
            "status": self.status,
            "modified_files": self.modified_files,
            "changes": [
                {
                    "defect_id": c.defect_id,
                    "object": c.object_name,
                    "action": c.action,
                    "before": c.before,
                    "after": c.after,
                    "page": c.page,
                    "line_number": c.line_number,
                    "success": c.success,
                }
                for c in self.changes
            ],
            "unresolved": self.unresolved,
        }


def _passes_structure_write_gate(original_tex: str, updated_tex: str) -> Tuple[bool, str]:
    if compute_content_diff is None or structure_regression_reasons is None:
        return False, "缺少内容完整性依赖，按 fail-closed 策略阻断写入"

    diff = compute_content_diff(original_tex, updated_tex)
    reasons = structure_regression_reasons(diff)
    if reasons:
        return False, "；".join(reasons)
    return True, "pass"


_PARAGRAPH_BOUNDARY_RE = re.compile(
    r"\\(?:begin|end|section|subsection|subsubsection|paragraph|chapter|maketitle|appendix|bibliography|bibliographystyle)\b"
)
_DISPLAY_MATH_ENV_RE = re.compile(r"\\begin\{(equation\*?|align\*?|multline\*?|gather\*?)\}(.*?)\\end\{\1\}", re.DOTALL)
_TABLE_LIKE_ENV_RE = re.compile(
    r"\\(begin|end)\{(table\*?|tabular\*?|tabularx|longtable|sidewaystable)\}"
)


def _split_lines_with_offsets(tex_content: str) -> Tuple[List[str], List[int]]:
    lines = tex_content.splitlines(keepends=True)
    if not lines:
        return [""], [0]

    offsets: List[int] = []
    cursor = 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line)
    return lines, offsets


def _document_body_line_index(lines: List[str]) -> int:
    for idx, line in enumerate(lines):
        if r"\begin{document}" in line:
            return idx + 1
    return 0


def _is_paragraph_boundary_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if stripped.startswith("%"):
        return True
    return bool(_PARAGRAPH_BOUNDARY_RE.match(stripped))


def _line_span_to_offsets(
    lines: List[str],
    offsets: List[int],
    start_idx: int,
    end_idx: int,
) -> Tuple[int, int]:
    return offsets[start_idx], offsets[end_idx] + len(lines[end_idx])


def _find_paragraph_span_by_line_number(
    tex_content: str,
    line_number: Optional[int],
) -> Optional[Tuple[int, int]]:
    if line_number is None or line_number < 1:
        return None

    lines, offsets = _split_lines_with_offsets(tex_content)
    if line_number > len(lines):
        return None

    body_line_idx = _document_body_line_index(lines)
    target_idx = max(body_line_idx, line_number - 1)

    candidate_idx = None
    search_start = max(body_line_idx, target_idx - 3)
    search_end = min(len(lines), target_idx + 4)
    for idx in range(search_start, search_end):
        if not _is_paragraph_boundary_line(lines[idx]):
            candidate_idx = idx
            break

    if candidate_idx is None:
        return None

    start_idx = candidate_idx
    while start_idx > body_line_idx and not _is_paragraph_boundary_line(lines[start_idx - 1]):
        start_idx -= 1

    end_idx = candidate_idx
    while end_idx + 1 < len(lines) and not _is_paragraph_boundary_line(lines[end_idx + 1]):
        end_idx += 1

    return _line_span_to_offsets(lines, offsets, start_idx, end_idx)


def _normalize_for_match(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _candidate_search_fragments(overfull_line: str) -> List[str]:
    normalized = _normalize_for_match(overfull_line)
    if not normalized:
        return []

    fragments: List[str] = [normalized]
    words = normalized.split()
    for word_count in (12, 8, 5):
        if len(words) >= word_count:
            fragments.append(" ".join(words[:word_count]))

    alpha_words = [word for word in re.findall(r"[A-Za-z]{8,}", normalized)]
    if alpha_words:
        fragments.append(max(alpha_words, key=len))

    deduped: List[str] = []
    for fragment in fragments:
        if fragment and fragment not in deduped:
            deduped.append(fragment)
    return deduped


def _iter_body_paragraph_spans(tex_content: str):
    lines, offsets = _split_lines_with_offsets(tex_content)
    body_line_idx = _document_body_line_index(lines)
    idx = body_line_idx
    while idx < len(lines):
        while idx < len(lines) and _is_paragraph_boundary_line(lines[idx]):
            idx += 1
        if idx >= len(lines):
            break

        start_idx = idx
        while idx + 1 < len(lines) and not _is_paragraph_boundary_line(lines[idx + 1]):
            idx += 1
        end_idx = idx
        start_offset, end_offset = _line_span_to_offsets(lines, offsets, start_idx, end_idx)
        yield start_offset, end_offset
        idx += 1


def _find_paragraph_span_by_text(
    tex_content: str,
    overfull_line: str,
) -> Optional[Tuple[int, int]]:
    fragments = _candidate_search_fragments(overfull_line)
    if not fragments:
        return None

    normalized_fragments = [_normalize_for_match(fragment) for fragment in fragments]
    for start_offset, end_offset in _iter_body_paragraph_spans(tex_content):
        paragraph = tex_content[start_offset:end_offset]
        normalized_paragraph = _normalize_for_match(paragraph)
        if any(fragment and fragment in normalized_paragraph for fragment in normalized_fragments):
            return start_offset, end_offset
    return None


def _line_is_preamble_textheight_shrink(tex_content: str, line_number: Optional[int]) -> bool:
    """检测行号是否在导言区且附近有 \\addtolength{\\textheight}{-...}。"""
    if line_number is None or line_number < 1:
        return False
    begin_doc = tex_content.find("\\begin{document}")
    if begin_doc < 0:
        return False
    # 行号必须在 \\begin{document} 之前
    lines_before_doc = tex_content[:begin_doc].count("\n") + 1
    if line_number > lines_before_doc:
        return False
    # 附近 5 行内有负向 textheight 修改
    lines = tex_content.splitlines()
    context = "\n".join(lines[max(0, line_number - 6):line_number + 5])
    return bool(re.search(r"\\addtolength\s*\{\s*\\textheight\s*\}\s*\{\s*-", context))


def _remove_preamble_textheight_shrink(
    tex_content: str, line_number: Optional[int]
) -> Tuple[str, Optional[FixResult]]:
    """移除导言区导致全文溢出的负向 textheight 修改（含扰动标记块）。"""
    if not _line_is_preamble_textheight_shrink(tex_content, line_number):
        return tex_content, None

    # 优先移除带 DISTURBANCE 标记的完整块
    tagged = re.compile(
        r"\n?[ \t]*%[^\n]*DISTURBANCE:E2[^\n]*:BEGIN[^\n]*\n"
        r"[ \t]*\\addtolength\s*\{\s*\\textheight\s*\}\s*\{\s*-[^}]+\}[^\n]*\n"
        r"[ \t]*%[^\n]*DISTURBANCE:E2[^\n]*:END[^\n]*\n?",
        re.IGNORECASE,
    )
    match = tagged.search(tex_content)
    if match:
        removed = match.group(0)
        modified = tex_content[:match.start()] + tex_content[match.end():]
        return modified, FixResult(
            defect_id="D1→A3",
            object_name="导言区 textheight 缩小",
            action="移除导言区的负向 textheight 修改（含扰动标记块）",
            before=removed.strip()[:80],
            after="(removed)",
            line_number=line_number,
            success=True,
        )

    # 兜底：移除孤立负向 textheight 行
    standalone = re.compile(
        r"^([ \t]*\\addtolength\s*\{\s*\\textheight\s*\}\s*\{\s*-[^}]+\}[ \t]*)$",
        re.MULTILINE,
    )
    match = standalone.search(tex_content)
    if match and match.start() < tex_content.find("\\begin{document}", 0):
        removed_line = match.group(0)
        modified = tex_content[:match.start()] + tex_content[match.end():]
        return modified, FixResult(
            defect_id="D1→A3",
            object_name="导言区 textheight 缩小",
            action="移除导言区的负向 textheight 修改",
            before=removed_line.strip(),
            after="(removed)",
            line_number=line_number,
            success=True,
        )

    return tex_content, None


def _resolve_overfull_files(project_root: Path, main_tex_name: str) -> Dict[int, Path]:
    """解析日志，把 overfull 行号映射到实际源文件。

    返回 {line_number: actual_file_path}。不在映射里的行号属于 main.tex。
    """
    try:
        from multi_file_overfull import find_file_in_project, resolve_overfull_to_source
        log_path = project_root / f"{Path(main_tex_name).stem}.log"
        if not log_path.is_file():
            return {}
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        resolutions = resolve_overfull_to_source(log_text, project_root, main_tex_name)
        mapping: Dict[int, Path] = {}
        for r in resolutions:
            if r["file"] == main_tex_name:
                continue  # main.tex 本身的溢出不需要映射
            actual = find_file_in_project(project_root, r["file"])
            if actual is not None:
                mapping[r["line_start"]] = actual
        return mapping
    except Exception:
        return {}


def _fix_overfull_in_included_file(
    included_path: Path,
    line_number: int,
    overflow_amount: float,
    description: str,
) -> Optional[Tuple[str, FixResult]]:
    """在子文件里执行 D1 段落溢出修复。策略链：长token → 引用拆分 → sloppy → stretch。"""
    try:
        content = included_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    stretch = "3em" if overflow_amount > 20 else ("2em" if overflow_amount > 10 else "1em")

    # 策略 1: D1-A 长 token
    long_token_result = _fix_long_token_with_breaks(content, description, line_number)
    if long_token_result is not None:
        modified, fix = long_token_result
        from transactional_patch import atomic_write_text
        atomic_write_text(included_path, modified)
        fix.object_name = f"{included_path.name} 长 token"
        return str(included_path), fix

    # 策略 2: 引用串拆分——\citep{a,b,c,d} → \citep{a,b} \citep{c,d}
    citation_result = _split_citation_clusters(content, line_number)
    if citation_result is not None:
        from transactional_patch import atomic_write_text
        atomic_write_text(included_path, citation_result[0])
        return str(included_path), citation_result[1]

    # 策略 3: \sloppy 段落——允许 TeX 更宽松的断行
    sloppy_result = _apply_sloppy_to_paragraph(content, line_number)
    if sloppy_result is not None:
        from transactional_patch import atomic_write_text
        atomic_write_text(included_path, sloppy_result[0])
        return str(included_path), sloppy_result[1]

    # 策略 4: 分级 emergencystretch
    paragraph_span = _find_paragraph_span_by_line_number(content, line_number)
    if paragraph_span is None:
        paragraph_span = _find_paragraph_span_by_text(content, description)
    if paragraph_span is not None:
        modified = _wrap_paragraph_with_emergencystretch(content, paragraph_span, stretch=stretch)
        if modified != content:
            from transactional_patch import atomic_write_text
            atomic_write_text(included_path, modified)
            return str(included_path), FixResult(
                defect_id="D1",
                object_name=f"{included_path.name} 段落",
                action=f"在子文件 {included_path.name} L{line_number} 段落添加 \\emergencystretch={stretch}",
                before=f"(original paragraph at L{line_number})",
                after=f"{{\\emergencystretch={stretch} ...}}",
                line_number=line_number,
                success=True,
            )
    return None


# 多 key 引用串：\citep{a,b,c,d} 或 \cite{a,b,c,d}
_CITE_CLUSTER_RE = re.compile(r"(\\cite[pt]?\{)([A-Za-z0-9_\-]+(?:,[A-Za-z0-9_\-]+){2,})(\})")


def _split_citation_clusters(
    content: str,
    line_number: Optional[int] = None,
) -> Optional[Tuple[str, FixResult]]:
    """拆分长引用串允许 TeX 在两个 \\citep 之间断行。

    \\citep{a,b,c,d} → \\citep{a,b} \\citep{c,d}
    语义不变（引用同一组论文），只是允许断行。
    """
    lines = content.splitlines(keepends=True)
    # 目标行号附近 ±2 行
    search_start = max(0, (line_number or 1) - 3)
    search_end = min(len(lines), (line_number or len(lines)) + 2)

    for idx in range(search_start, search_end):
        line = lines[idx]
        match = _CITE_CLUSTER_RE.search(line)
        if not match:
            continue
        keys = match.group(2).split(",")
        if len(keys) < 3:
            continue
        # 拆成两半
        mid = (len(keys) + 1) // 2
        first_half = ",".join(keys[:mid])
        second_half = ",".join(keys[mid:])
        cmd = match.group(1).rstrip("{")  # \citep or \cite or \citet
        replacement = f"{cmd}{{{first_half}}} {cmd}{{{second_half}}}"
        new_line = line[:match.start()] + replacement + line[match.end():]
        lines[idx] = new_line
        modified = "".join(lines)
        return modified, FixResult(
            defect_id="D1",
            object_name=f"引用串拆分",
            action=f"拆分 {cmd}{{{match.group(2)[:30]}...}} 为两个引用以允许断行",
            before=f"{cmd}{{{match.group(2)[:40]}}}",
            after=replacement[:60],
            line_number=line_number,
            success=True,
        )
    return None


def _apply_sloppy_to_paragraph(
    content: str,
    line_number: Optional[int] = None,
) -> Optional[Tuple[str, FixResult]]:
    """对指定段落应用 \\sloppy 模式（允许更宽松的断行和间距）。"""
    paragraph_span = _find_paragraph_span_by_line_number(content, line_number)
    if paragraph_span is None:
        paragraph_span = _find_paragraph_span_by_text(content, "")
    if paragraph_span is None:
        return None
    start, end = paragraph_span
    paragraph = content[start:end]
    if r"\sloppy" in paragraph:
        return None  # 已有 sloppy
    wrapped = f"{{\\sloppy\n{paragraph.rstrip()}\n}}"
    modified = content[:start] + wrapped + content[end:]
    return modified, FixResult(
        defect_id="D1",
        object_name="段落 sloppy 模式",
        action=f"对 L{line_number} 段落应用 \\sloppy（tolerance=9999, emergencystretch=3em）",
        before=f"(original paragraph)",
        after=f"{{\\sloppy ...}}",
        line_number=line_number,
        success=True,
    )


def _batch_fix_included_file_overfull(
    included_path: Path,
    line_numbers: List[int],
    max_overflow: float,
) -> Optional[Tuple[str, FixResult]]:
    """对一个子文件里的多个溢出行一次性全部修复。

    策略：对整个文件范围应用 \\sloppy（一次修完比逐段修更高效且更一致），
    加上引用串拆分。
    """
    try:
        content = included_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    original = content
    actions = []

    # 1. 拆分文件里所有 ≥3 key 的引用串
    cite_count = 0
    for match in _CITE_CLUSTER_RE.finditer(content):
        keys = match.group(2).split(",")
        if len(keys) < 3:
            continue
        mid = (len(keys) + 1) // 2
        cmd = match.group(1).rstrip("{")
        replacement = f"{cmd}{{{','.join(keys[:mid])}}} {cmd}{{{','.join(keys[mid:])}}}"
        content = content.replace(match.group(0), replacement, 1)
        cite_count += 1
    if cite_count > 0:
        actions.append(f"拆分 {cite_count} 处引用串")

    # 1.5 把 ~\citep 替换为空格+\citep（允许 TeX 在引用前断行）
    tilde_count = content.count("~\\citep") + content.count("~\\cite")
    if tilde_count > 0:
        content = content.replace("~\\citep", " \\citep").replace("~\\cite", " \\cite")
        actions.append(f"释放 {tilde_count} 处引用前断行点")

    # 1.7 缩小过宽的 wraptable（>0.45\textwidth 的缩到 0.45）
    # wraptable 压缩正文宽度导致周围所有段落溢出
    wraptable_count = 0
    for wt_match in re.finditer(r"\\begin\{wraptable\}\{[lr]\}\{([\d.]+)\\textwidth\}", content):
        try:
            width = float(wt_match.group(1))
        except ValueError:
            continue
        if width > 0.45:
            old_spec = wt_match.group(0)
            new_spec = old_spec.replace(f"{width}\\textwidth", "0.45\\textwidth")
            content = content.replace(old_spec, new_spec, 1)
            wraptable_count += 1
    if wraptable_count > 0:
        actions.append(f"缩小 {wraptable_count} 处过宽 wraptable 至 0.45\\textwidth")

    # 2. 对整个正文区域应用 \\sloppy
    begin_doc = content.find("\\begin{document}")
    if begin_doc < 0:
        begin_doc = 0
    if "\\sloppy" not in content:
        insert_pos = content.find("\n", begin_doc) + 1
        if insert_pos > 0:
            content = content[:insert_pos] + "\\sloppy\n" + content[insert_pos:]
            actions.append("全文 \\sloppy 模式")

    if content == original:
        return None

    from transactional_patch import atomic_write_text
    atomic_write_text(included_path, content)
    return str(included_path), FixResult(
        defect_id="D1",
        object_name=f"{included_path.name} 批量溢出修复",
        action=f"批量修复 {included_path.name} 的 {len(line_numbers)} 处溢出：{'；'.join(actions)}",
        before=f"({len(line_numbers)} overfull paragraphs at lines {line_numbers[:5]})",
        after=f"({'; '.join(actions)})",
        line_number=line_numbers[0] if line_numbers else None,
        success=True,
    )


def _fix_long_token_with_breaks(
    tex_content: str,
    overfull_line: str,
    line_number: Optional[int] = None,
) -> Optional[Tuple[str, FixResult]]:
    """D1-A：为不可断长 token（≥30 字符标识符）添加安全断点。

    在 camelCase 边界、下划线、句点、斜杠处插入 \\allowbreak，
    不用 \\texttt 包裹（那会改变字体观感——终版方案 D1-A 第 5 条）。
    """
    
    
    
    long_tokens: List[str] = []
    if line_number is not None:
        try:
            source_lines = tex_content.splitlines()
            # 警告行号是段落范围，token 可能比段落起始行还靠前 1-2 行（TeX 行统计
            # 与源行有偏移），窗口从 line_number-3 起向下 14 行
            start_idx = max(0, line_number - 3)
            window = "\n".join(source_lines[start_idx : start_idx + 14])
            long_tokens = re.findall(r"[A-Za-z][A-Za-z0-9_/.]{29,}", window)
        except Exception:
            long_tokens = []
    if not long_tokens:
        long_tokens = re.findall(r"[A-Za-z][A-Za-z0-9_/.]{29,}", overfull_line)

    if not long_tokens:
        return None

    # 取最长的
    token = max(long_tokens, key=len)

    # 已经有 \allowbreak 的不重复处理
    if "\\allowbreak" in overfull_line:
        return None

    # 在安全边界插入 \allowbreak
    breakable = _insert_safe_breaks_in_token(token)
    if breakable == token:
        return None  # 没有可断点

    # 在全文中替换（只替换第一次出现，且确认不在数学环境/命令名内）
    if f"\\{token}" in tex_content:
        return None  # 可能是命令名，不动
    token_pos = tex_content.find(token)
    if token_pos < 0:
        return None
    prefix = tex_content[:token_pos]
    # 数学环境判定用 $ 配对奇偶：旧正则 \$[^$]*token 对"前方出现过任意 $"
    # 一律误判成数学环境，导致正文长 token 永远修不了
    if (prefix.count("$") - prefix.count(r"\$")) % 2 == 1:
        return None  # 在行内数学环境内

    modified = tex_content.replace(token, breakable, 1)
    if modified == tex_content:
        return None

    return modified, FixResult(
        defect_id="D1",
        object_name="段落文本",
        action=f"为长标识符 '{token[:20]}...' ({len(token)} 字符) 添加安全断点",
        before=token[:60] + "..." if len(token) > 60 else token,
        after=breakable[:60] + "..." if len(breakable) > 60 else breakable,
        line_number=line_number,
        success=True,
    )


def _insert_safe_breaks_in_token(token: str) -> str:
    """在 camelCase 边界、下划线、句点、斜杠后插入 \\allowbreak。

    无自然边界的长串（如 56 连 X 的注入 token）按固定间隔补断点，
    保证任意连续不可断片段不超过 12 字符——否则 173pt 级溢出无法回行。
    """
    result = list(token)
    insert_positions = []

    for idx in range(1, len(token) - 1):
        char = token[idx]
        prev = token[idx - 1]
        next_char = token[idx + 1]

        # camelCase 边界：小写→大写
        if prev.islower() and char.isupper():
            insert_positions.append(idx)
        # 下划线/句点/斜杠后
        elif char in "_/." and next_char.isalnum():
            insert_positions.append(idx + 1)

    # 固定间隔补断：相邻断点（含首尾）间距超过 12 的空档内，每 10 字符断一次
    bounded = sorted(set(insert_positions))
    interval_positions = []
    prev_cut = 0
    for pos in bounded:
        while pos - prev_cut > 12:
            prev_cut += 10
            if prev_cut < pos:
                interval_positions.append(prev_cut)
        prev_cut = pos
    while len(token) - prev_cut > 12:
        prev_cut += 10
        interval_positions.append(prev_cut)

    # 从后往前插入（避免位置偏移）
    for pos in reversed(sorted(set(insert_positions) | set(interval_positions))):
        result.insert(pos, r"\allowbreak ")

    return "".join(result)


def _wrap_paragraph_with_emergencystretch(
    tex_content: str,
    paragraph_span: Tuple[int, int],
    stretch: str = "1em",
) -> str:
    start_offset, end_offset = paragraph_span
    paragraph = tex_content[start_offset:end_offset]
    if r"\emergencystretch" in paragraph:
        return tex_content

    stripped_paragraph = paragraph.rstrip("\n")
    trailing_newlines = paragraph[len(stripped_paragraph):]
    wrapped = f"{{\\emergencystretch={stretch}\n{stripped_paragraph}\n}}{trailing_newlines}"
    return tex_content[:start_offset] + wrapped + tex_content[end_offset:]


def _offset_to_line_number(tex_content: str, offset: int) -> int:
    return tex_content.count("\n", 0, offset) + 1


def _find_display_math_match(
    tex_content: str,
    line_number: Optional[int] = None,
    equation_label: Optional[str] = None,
    tolerance: int = 2,
):
    matches = list(_DISPLAY_MATH_ENV_RE.finditer(tex_content))
    if not matches:
        return None

    if equation_label:
        for match in matches:
            if f"\\label{{{equation_label}}}" in match.group(0):
                return match

    if line_number is None:
        return matches[0]

    exact_match = None
    best_match = None
    best_distance = None
    for match in matches:
        start_line = _offset_to_line_number(tex_content, match.start())
        end_line = _offset_to_line_number(tex_content, match.end())
        if start_line <= line_number <= end_line:
            exact_match = match
            break
        if start_line - tolerance <= line_number <= end_line + tolerance:
            distance = min(abs(line_number - start_line), abs(line_number - end_line))
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_match = match
            continue
        distance = min(abs(line_number - start_line), abs(line_number - end_line))
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_match = match

    if exact_match is not None:
        return exact_match
    if best_distance is not None and best_distance <= tolerance:
        return best_match
    return None


def _find_safe_concat_fraction_break(rhs: str) -> Optional[Tuple[str, str]]:
    token = r", \frac{"
    idx = rhs.rfind(token)
    if idx <= 0:
        return None
    left = rhs[: idx + 1].rstrip()
    right = rhs[idx + 2 :].lstrip()
    return left, right


def _choose_equation_break(rhs: str) -> Optional[Tuple[str, str]]:
    candidates = [
        ") (",
        r" + \frac",
        r" - \frac",
        r" + \sum",
        r" - \sum",
        r" + ",
        r" - ",
    ]
    for token in candidates:
        idx = rhs.rfind(token)
        if idx <= 0:
            continue
        split_at = idx + (1 if token == ") (" else 0)
        left = rhs[:split_at].rstrip()
        right = rhs[split_at:].lstrip()
        if token == ") (" and right.startswith("("):
            return left, right
        if token.strip() in {"+", "-"} and right[:1] not in {"+", "-"}:
            right = token.strip() + " " + right
        return left, right
    return None


def _split_top_level_additive_terms(rhs: str) -> List[str]:
    """M3 token-aware 安全切分：委托 math_safe_breaking，不在保护区间内断行。"""
    return split_safe_additive_terms(rhs)


def _rewrite_equation_to_aligned_terms(lhs: str, rhs: str) -> Optional[str]:
    terms = _split_top_level_additive_terms(rhs)
    if len(terms) < 3:
        return None

    # M3 token 验证：切分后数学 token 序列必须与原始一致
    if not verify_split_preserves_tokens(rhs, terms):
        return None

    lines = [
        "\\begin{equation}",
        "\\begin{aligned}",
        f"  {lhs} &= {terms[0]} \\\\",
    ]
    for term in terms[1:-1]:
        lines.append(f"  &\\quad {term} \\\\")
    lines.append(f"  &\\quad {terms[-1]}")
    lines.extend(["\\end{aligned}", "\\end{equation}"])
    return "\n".join(lines)


def _chunk_identifier_for_math_subscript(identifier: str, max_chunk_len: int = 30) -> List[str]:
    parts = re.findall(r"[A-Z][a-z0-9]*|[a-z0-9]+", identifier)
    if not parts:
        parts = [identifier[idx: idx + max_chunk_len] for idx in range(0, len(identifier), max_chunk_len)]

    chunks: List[str] = []
    current = ""
    for part in parts:
        if current and len(current) + len(part) > max_chunk_len:
            chunks.append(current)
            current = part
        else:
            current += part
    if current:
        chunks.append(current)
    if len(chunks) <= 1 and len(identifier) > max_chunk_len:
        return [identifier[idx: idx + max_chunk_len] for idx in range(0, len(identifier), max_chunk_len)]
    return chunks


def _break_long_mathrm_subscripts(expression: str) -> str:
    def replace(match: re.Match[str]) -> str:
        identifier = match.group(1)
        if len(identifier) < 40:
            return match.group(0)
        chunks = _chunk_identifier_for_math_subscript(identifier)
        if len(chunks) <= 1:
            return match.group(0)
        body = r"\\".join(f"\\mathrm{{{chunk}}}" for chunk in chunks)
        return f"_{{\\substack{{{body}}}}}"

    return re.sub(r"_\{\\mathrm\{([A-Za-z][A-Za-z0-9]{39,})\}\}", replace, expression)


def _rewrite_equation_to_multline(equation_body: str) -> Optional[str]:
    normalized = " ".join(equation_body.strip().split())
    if "=" not in normalized:
        return None

    lhs, rhs = normalized.split("=", 1)
    lhs = lhs.strip()
    rhs = _break_long_mathrm_subscripts(rhs.strip())
    concat_fraction_break = _find_safe_concat_fraction_break(rhs)
    if concat_fraction_break is not None:
        first_line_rhs, second_line_rhs = concat_fraction_break
        return (
            "\\begin{equation}\n"
            "\\begin{aligned}\n"
            f"  {lhs} &= {first_line_rhs} \\\\\n"
            f"  &{second_line_rhs}\n"
            "\\end{aligned}\n"
            "\\end{equation}"
        )

    aligned_terms = _rewrite_equation_to_aligned_terms(lhs, rhs)
    if aligned_terms is not None:
        return aligned_terms

    break_pair = _choose_equation_break(rhs)
    if break_pair is None:
        return None

    first_line_rhs, second_line_rhs = break_pair
    return (
        "\\begin{multline}\n"
        f"  {lhs} = {first_line_rhs} \\\\\n"
        f"  {second_line_rhs}\n"
        "\\end{multline}"
    )


def _line_is_within_display_math(tex_content: str, line_number: Optional[int], tolerance: int = 2) -> bool:
    return _find_display_math_match(tex_content, line_number=line_number, tolerance=tolerance) is not None


def _line_is_within_table_like_env(tex_content: str, line_number: Optional[int]) -> bool:
    if line_number is None or line_number <= 0:
        return False

    lines = tex_content.splitlines()
    depth = 0
    for idx, line in enumerate(lines, start=1):
        for match in _TABLE_LIKE_ENV_RE.finditer(line):
            if match.group(1) == "begin":
                depth += 1
            else:
                depth = max(0, depth - 1)
        if idx == line_number:
            return depth > 0
    return False


def _ensure_tabularx_package(tex_content: str) -> str:
    if r"\usepackage{tabularx}" in tex_content:
        return tex_content
    begin_match = re.search(r"\\begin\{document\}", tex_content)
    if not begin_match:
        return tex_content
    return tex_content[:begin_match.start()] + "\\usepackage{tabularx}\n" + tex_content[begin_match.start():]


# ============================================================
# D1: 段落文本溢出修复
# ============================================================

def fix_paragraph_overflow(
    tex_content: str,
    overfull_line: str,
    overflow_amount: float,
    line_number: Optional[int] = None,
) -> Tuple[str, Optional[FixResult]]:
    r"""
    修复段落文本溢出

    策略优先级:
    1. 引入断词点 (\-)
    2. 调整段落级容差 (\emergencystretch)
    3. 返回未解决 (需语义改写)

    Args:
        tex_content: .tex 文件内容
        overfull_line: 溢出的文本行
        overflow_amount: 溢出量 (pt)

    Returns:
        (修改后的内容，修复结果)
    """
    # 安全守卫：表格/对齐环境中的 D1 不能按普通段落包裹，否则会破坏 \toprule/\midrule 等语法。
    if _line_is_within_table_like_env(tex_content, line_number):
        return tex_content, None

    # 策略 0 (D1-A): 不可断长 token（≥30 字符标识符）→ 语义安全断点
    # 优先于盲断词：camelCase/下划线/斜杠边界比每 4-5 字母的 \- 更精准
    long_token_result = _fix_long_token_with_breaks(tex_content, overfull_line, line_number)
    if long_token_result is not None:
        return long_token_result

    # 策略 1: 为长单词添加断词点（适合 12-29 字符的普通长词）
    # 查找长度 > 10 的单词 (可能是复合词或长学术术语)
    long_words = re.findall(r'\b[a-zA-Z]{10,}\b', overfull_line)

    if long_words:
        # 为最长的单词添加断词点
        longest_word = max(long_words, key=len)
        if len(longest_word) >= 12:
            # 在音节边界处添加断词点 (简化：每 4-5 个字母)
            hyphenated = add_hyphenation_points(longest_word)
            modified_content = tex_content.replace(longest_word, hyphenated, 1)
            return modified_content, FixResult(
                defect_id="D1",
                object_name=f"段落文本",
                action=f"为长单词 '{longest_word}' 添加断词点",
                before=longest_word,
                after=hyphenated,
                success=True,
            )

    # 策略 2: 为正文局部段落添加\emergencystretch，禁止触碰导言区或整篇包裹
    paragraph_span = _find_paragraph_span_by_line_number(tex_content, line_number)
    if paragraph_span is None:
        paragraph_span = _find_paragraph_span_by_text(tex_content, overfull_line)

    if paragraph_span is not None:
        modified_content = _wrap_paragraph_with_emergencystretch(tex_content, paragraph_span, stretch="1em")
        if modified_content != tex_content:
            return modified_content, FixResult(
            defect_id="D1",
            object_name="段落文本",
            action="为正文局部段落添加\\emergencystretch=1em 以允许额外拉伸",
            before=overfull_line[:80] + "..." if len(overfull_line) > 80 else overfull_line,
            after=f"{{\\emergencystretch=1em ...}}",
            success=True,
        )

    # 策略 3: 无法自动修复，需要语义改写
    return tex_content, None


def add_hyphenation_points(word: str) -> str:
    """
    为长单词添加断词点

    简化实现：在元音 - 辅音边界处添加
    """
    if len(word) < 12:
        return word

    # 简化：每 4-5 个字母添加一个断词点 (在实际应用中应使用更精确的音节划分)
    vowels = "aeiouAEIOU"
    result = []
    i = 0
    while i < len(word):
        result.append(word[i])
        # 在元音后检查是否可以断词
        if word[i] in vowels and i > 3 and i < len(word) - 3:
            # 检查下一个字母是否是辅音
            if i + 1 < len(word) and word[i + 1] not in vowels:
                result.append(r'\-')
        i += 1

    return ''.join(result)


def _count_top_level_table_columns(column_spec: str) -> int:
    spec = column_spec.strip("{}")
    brace_depth = 0
    count = 0
    idx = 0
    while idx < len(spec):
        char = spec[idx]
        if char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth = max(0, brace_depth - 1)
        elif brace_depth == 0 and char in {"l", "r", "c", "X"}:
            count += 1
        elif brace_depth == 0 and char in {"p", "m", "b"} and idx + 1 < len(spec) and spec[idx + 1] == "{":
            count += 1
        idx += 1
    return count


def _tighten_table_block_layout(block: str) -> Tuple[str, List[str]]:
    actions: List[str] = []
    tabular_match = re.search(r"\\begin\{tabularx?\}(?:\{[^}]+\})?(\{[^}]+\})", block)
    if not tabular_match:
        return block, actions

    column_spec = tabular_match.group(1)
    column_count = _count_top_level_table_columns(column_spec)
    updated = block

    if column_count >= 6 and r"\begin{table*}" not in updated:
        updated = re.sub(r"\\begin\{table\}(?:\[[^\]]*\])?", r"\\begin{table*}[t]", updated, count=1)
        updated = updated.replace(r"\end{table}", r"\end{table*}", 1)
        updated = updated.replace(r"\linewidth", r"\textwidth")
        actions.append("promoted wide table to table*")

    if column_count >= 6 and r"\small" not in updated and r"\footnotesize" not in updated:
        begin_match = re.search(r"\\begin\{table\*?\}(?:\[[^\]]*\])?", updated)
        if begin_match:
            updated = updated[: begin_match.end()] + "\n\\small" + updated[begin_match.end() :]
            actions.append("added \\small to wide table")

    tabcolsep_match = re.search(r"\\setlength\{\\tabcolsep\}\{([0-9.]+)mm\}", updated)
    if tabcolsep_match:
        current = float(tabcolsep_match.group(1))
        if current > 2.0:
            updated = (
                updated[: tabcolsep_match.start()]
                + r"\setlength{\tabcolsep}{2mm}"
                + updated[tabcolsep_match.end() :]
            )
            actions.append("tightened \\tabcolsep to 2mm")
    elif column_count >= 5:
        centering_match = re.search(r"\\centering", updated)
        insert_at = centering_match.end() if centering_match else 0
        updated = updated[:insert_at] + "\n\\setlength{\\tabcolsep}{2mm}" + updated[insert_at:]
        actions.append("inserted \\tabcolsep 2mm")

    return updated, actions


# ============================================================
# D1: 表格单元格溢出修复
# ============================================================

def fix_table_overflow(
    tex_content: str,
    table_label: Optional[str] = None,
    line_number: Optional[int] = None,
) -> Tuple[str, Optional[FixResult]]:
    """
    修复表格单元格溢出

    策略优先级:
    1. 改用 tabularx 环境
    2. 手动设置列宽 (p{宽度})
    3. 精简表头
    4. 调整字号

    Args:
        tex_content: .tex 文件内容
        table_label: 表格标签 (如 "tab:results")
        line_number: 溢出行号

    Returns:
        (修改后的内容，修复结果)
    """
    # 定位表格环境：table+tabular 块（label 与行号在选择阶段使用）
    pattern = r'(\\begin\{(?:table|table\*|sidewaystable)\}(?:\[[htbp]+\])?.*?)(\\begin\{tabular\})(\{[^}]+\})(.*?)(\\end\{tabular\})(.*?\\end\{(?:table|table\*|sidewaystable)\})'

    matches = list(re.finditer(pattern, tex_content, re.DOTALL))

    if not matches:
        return tex_content, None

    def _line_of(offset: int) -> int:
        # match.start()/end() are character offsets; defects carry 1-indexed
        # line numbers, so convert before comparing.
        return tex_content.count("\n", 0, offset) + 1

    # 有 label 时优先在携带该 label 的表格里选择
    candidates = matches
    if table_label:
        labeled = [
            match
            for match in matches
            if f"\\label{{{table_label}}}" in match.group(0)
        ]
        if labeled:
            candidates = labeled

    target_match = candidates[0]
    if line_number is not None:
        containing = [
            match
            for match in candidates
            if _line_of(match.start()) <= line_number <= _line_of(match.end())
        ]
        if containing:
            target_match = containing[0]
        else:
            # 溢出行不在任何候选表格内：选行距最近的表格
            target_match = min(
                candidates,
                key=lambda match: min(
                    abs(line_number - _line_of(match.start())),
                    abs(line_number - _line_of(match.end())),
                ),
            )

    full_table = target_match.group(0)
    column_spec = target_match.group(3)

    tightened_table, tighten_actions = _tighten_table_block_layout(full_table)
    if tighten_actions:
        modified_content = tex_content.replace(full_table, tightened_table, 1)
        return modified_content, FixResult(
            defect_id="D1",
            object_name=table_label or "表格",
            action="; ".join(tighten_actions),
            before=full_table[:120] + "..." if len(full_table) > 120 else full_table,
            after=tightened_table[:120] + "..." if len(tightened_table) > 120 else tightened_table,
            line_number=line_number,
            success=True,
        )

    # 策略 1: 改用 tabularx
    # 检查是否有文本列 (l, r, c) 可以改为 X 列
    if _has_top_level_text_columns(column_spec):
        # 将 tabular 改为 tabularx，将部分列改为 X 列
        new_column_spec = convert_to_tabularx_columns(column_spec)
        begin_pattern = re.compile(r"\\begin\{tabular\}\s*" + re.escape(column_spec))
        # 替换文本含 \linewidth / 列规格的反斜杠，必须用函数替换，
        # 走模板替换会因非法转义（如 \l）抛 re.error。
        begin_replacement = f"\\begin{{tabularx}}{{\\linewidth}}{new_column_spec}"
        new_table, begin_count = begin_pattern.subn(
            lambda _match: begin_replacement,
            full_table,
            count=1,
        )
        if begin_count <= 0:
            return tex_content, None

        new_table, end_count = re.subn(
            r"\\end\{tabular\}",
            lambda _match: "\\end{tabularx}",
            new_table,
            count=1,
        )
        if end_count <= 0:
            return tex_content, None

        modified_content = tex_content.replace(full_table, new_table, 1)
        modified_content = _ensure_tabularx_package(modified_content)
        return modified_content, FixResult(
            defect_id="D1",
            object_name=table_label or "表格",
            action=f"将 tabular 改为 tabularx，列规格从 {column_spec} 改为 {new_column_spec}",
            before=f"\\begin{{tabular}}{column_spec}",
            after=f"\\begin{{tabularx}}{{\\linewidth}}{new_column_spec}",
            line_number=line_number,
            success=True,
        )

    # 策略 2: 添加\small 字号
    if '\\small' not in full_table and '\\footnotesize' not in full_table:
        # 在表格环境内、tabular 之前添加 \small
        tabular_match = re.search(r'\\begin\{tabular\}', full_table)
        if tabular_match:
            insert_pos = tabular_match.start()
            new_table = full_table[:insert_pos] + "\\small\n" + full_table[insert_pos:]
            modified_content = tex_content.replace(full_table, new_table, 1)
            return modified_content, FixResult(
                defect_id="D1",
                object_name=table_label or "表格",
                action="在表格环境内添加\\small 字号以压缩表格",
                before=full_table[:100] + "...",
                after=new_table[:100] + "...",
                line_number=line_number,
                success=True,
            )

        table_env_match = re.search(
            r'\\begin\{(table[^}]*)\}(\[[htbp]+\])?',
            full_table
        )
        if table_env_match:
            insert_pos = table_env_match.end(0)
            new_table = full_table[:insert_pos] + "\n\\small" + full_table[insert_pos:]
            modified_content = tex_content.replace(full_table, new_table, 1)
            return modified_content, FixResult(
                defect_id="D1",
                object_name=table_label or "表格",
                action="在表格环境内添加\\small 字号以压缩表格",
                before=full_table[:100] + "...",
                after=new_table[:100] + "...",
                line_number=line_number,
                success=True,
            )

    return tex_content, None


def convert_to_tabularx_columns(column_spec: str) -> str:
    """
    将 tabular 列规格转换为 tabularx 列规格

    策略：将最宽的文本列改为 X 列
    """
    # 移除两侧的 { }
    spec = column_spec.strip('{}')

    # 统计列类型
    text_columns = []  # l, r, c 列的位置
    brace_depth = 0
    for i, c in enumerate(spec):
        if c == '{':
            brace_depth += 1
            continue
        if c == '}':
            brace_depth = max(0, brace_depth - 1)
            continue
        if brace_depth == 0 and c in 'lrc':
            text_columns.append((i, c))

    if not text_columns:
        return column_spec

    # 将最后一个文本列改为 X 列 (通常是描述性列)
    last_text_idx, last_type = text_columns[-1]
    new_spec = spec[:last_text_idx] + 'X' + spec[last_text_idx + 1:]

    return '{' + new_spec + '}'


def _has_top_level_text_columns(column_spec: str) -> bool:
    spec = column_spec.strip('{}')
    brace_depth = 0
    for c in spec:
        if c == '{':
            brace_depth += 1
            continue
        if c == '}':
            brace_depth = max(0, brace_depth - 1)
            continue
        if brace_depth == 0 and c in 'lrc':
            return True
    return False


# ============================================================
# D2: 长公式溢出修复
# ============================================================

def fix_equation_overflow(
    tex_content: str,
    equation_label: Optional[str] = None,
    line_number: Optional[int] = None,
) -> Tuple[str, Optional[FixResult]]:
    """
    修复长公式溢出

    策略优先级:
    1. equation → multline
    2. equation → align/split
    3. 引入中间变量简化

    Args:
        tex_content: .tex 文件内容
        equation_label: 公式标签
        line_number: 溢出行号

    Returns:
        (修改后的内容，修复结果)
    """
    match = _find_display_math_match(
        tex_content,
        line_number=line_number,
        equation_label=equation_label,
    )
    if not match:
        return tex_content, None

    original_equation = match.group(0)
    equation_body = match.group(2)
    rewritten_equation = _rewrite_equation_to_multline(equation_body)
    if rewritten_equation is None:
        rewritten_equation = f"\\begin{{multline}}\n{equation_body.strip()}\n\\end{{multline}}"

    modified_content = tex_content[:match.start()] + rewritten_equation + tex_content[match.end():]
    return modified_content, FixResult(
        defect_id="D2",
        object_name=equation_label or "公式",
        action="将长公式改为多行对齐结构，并在可断点处换行",
        before=original_equation[:120] + "..." if len(original_equation) > 120 else original_equation,
        after=rewritten_equation[:120] + "..." if len(rewritten_equation) > 120 else rewritten_equation,
        line_number=line_number,
        success=True,
    )


# ============================================================
# D3: URL 溢出修复
# ============================================================

def fix_url_overflow(
    tex_content: str,
    url: Optional[str] = None,
) -> Tuple[str, Optional[FixResult]]:
    r"""
    修复 URL 溢出

    策略优先级:
    1. 使用\url 命令
    2. 启用参考文献断行
    3. 手动添加断行点

    Args:
        tex_content: .tex 文件内容
        url: 溢出的 URL

    Returns:
        (修改后的内容，修复结果)
    """
    if url:
        # 策略 1: 将裸 URL 改为\url 命令
        # 查找裸 URL (以 http://或 https://开头)
        url_pattern = r'(?<!\\)(https?://' + re.escape(url.replace('https://', '').replace('http://', '')) + r')'
        match = re.search(url_pattern, tex_content)
        if match:
            bare_url = match.group(1)
            modified_content = tex_content.replace(bare_url, f"\\url{{{bare_url}}}", 1)
            return modified_content, FixResult(
                defect_id="D3",
                object_name="URL",
                action=f"将裸 URL 改为\\url 命令",
                before=bare_url[:50] + "..." if len(bare_url) > 50 else bare_url,
                after=f"\\url{{{bare_url[:50]}...}}" if len(bare_url) > 50 else f"\\url{{{bare_url}}}",
                success=True,
            )

    # 策略 2: 检查导言区是否有 url 宏包和断行设置
    # 若没有，添加断行设置
    if '\\usepackage{url}' not in tex_content and '\\usepackage{hyperref}' not in tex_content:
        # 在\begin{document} 前添加宏包
        match = re.search(r'\\begin\{document\}', tex_content)
        if match:
            insert_pos = match.start()
            before = tex_content[:insert_pos]
            after = tex_content[insert_pos:]
            new_content = before + "\\usepackage{url}\n\\def\\UrlBreaks{\\do\\/\\do-}\n" + after
            return new_content, FixResult(
                defect_id="D3",
                object_name="URL 断行设置",
                action="添加 url 宏包和断行设置",
                before="\\begin{document}",
                after="\\usepackage{url}\n\\def\\UrlBreaks{\\do\\/\\do-}\n\\begin{document}",
                success=True,
            )

    return tex_content, None


def fix_bibliography_url_breaking(
    tex_content: str,
) -> Tuple[str, Optional[FixResult]]:
    r"""
    为参考文献添加 URL 断行设置

    针对 biblatex 或 natbib 的 URL 断行配置
    """
    # 检查是否使用 biblatex
    if '\\usepackage{biblatex}' in tex_content or '\\bibliographystyle{biblatex}' in tex_content:
        # 添加 biblatex 的 URL 断行计数器
        if '\\setcounter{biburlnumpenalty}' not in tex_content:
            match = re.search(r'\\begin\{document\}', tex_content)
            if match:
                insert_pos = match.start()
                before = tex_content[:insert_pos]
                after = tex_content[insert_pos:]
                additions = (
                    "\\setcounter{biburlnumpenalty}{100}\n"
                    "\\setcounter{biburlucpenalty}{100}\n"
                    "\\setcounter{biburllcpenalty}{100}\n"
                )
                new_content = before + additions + after
                return new_content, FixResult(
                    defect_id="D3",
                    object_name="参考文献 URL 断行",
                    action="添加 biblatex URL 断行计数器",
                    before="\\begin{document}",
                    after=additions + "\\begin{document}",
                    success=True,
                )

    return tex_content, None


# ============================================================
# 主修复函数
# ============================================================

def fix_overflow_defects(
    tex_file_path: str,
    defects: List[Dict[str, Any]],
) -> OverflowFixReport:
    """
    修复所有 Category D 缺陷

    Args:
        tex_file_path: .tex 文件路径
        defects: 缺陷列表，每个缺陷包含:
            - defect_id: D1, D2, D3
            - page: 页码
            - line_number: 行号 (可选)
            - object: 对象名称 (如表格标签、公式标签)
            - description: 描述
            - overflow_amount: 溢出量 (D1 可选)

    Returns:
        OverflowFixReport: 修复报告
    """
    tex_path = Path(tex_file_path)
    if not tex_path.exists():
        return OverflowFixReport(
            status="failed",
            unresolved=[f"文件不存在：{tex_file_path}"]
        )

    try:
        tex_content = tex_path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError) as e:
        return OverflowFixReport(
            status="failed",
            unresolved=[f"无法读取文件 {tex_file_path}: {e}"]
        )
    original_tex_content = tex_content

    # 多文件解析：确定 D1 溢出实际发生在哪个 \input 的子文件
    # LaTeX 日志的行号属于最近打开的子文件，不是 main.tex
    multi_file_map = _resolve_overfull_files(tex_path.parent, tex_path.name)

    modified_files = set()
    changes = []
    unresolved = []

    for defect in defects:
        defect_id = defect.get("defect_id", "")
        page = defect.get("page", 0)
        line_number = defect.get("line_number")
        object_name = defect.get("object", "")
        overflow_amount = defect.get("overflow_amount", 0)

        new_content = tex_content
        fix_result = None

        # 多文件批量修复：同一子文件的多个溢出一次性全修
        if defect_id == "D1" and line_number is not None and multi_file_map:
            included_path = multi_file_map.get(line_number)
            if included_path is not None and included_path.is_file():
                # 收集同一子文件的所有 D1 行号
                all_lines_for_file = [
                    ln for ln, fp in multi_file_map.items() if fp == included_path
                ]
                batch_result = _batch_fix_included_file_overfull(
                    included_path,
                    all_lines_for_file,
                    overflow_amount,
                )
                if batch_result is not None:
                    file_path_str, batch_fix = batch_result
                    changes.append(batch_fix)
                    modified_files.add(file_path_str)
                    # 跳过同文件的其他 D1 候选（已批量修复）
                    continue

        if defect_id == "D1":
            # 判断是段落溢出还是表格溢出
            if "table" in object_name.lower() or "tab:" in object_name.lower():
                new_content, fix_result = fix_table_overflow(
                    tex_content,
                    table_label=object_name if object_name.startswith("tab:") else None,
                    line_number=line_number,
                )
            elif str(defect.get("proposed_action") or "") == "remove_preamble_textheight_shrink" or _line_is_preamble_textheight_shrink(tex_content, line_number):
                
                
                new_content, fix_result = _remove_preamble_textheight_shrink(
                    tex_content, line_number
                )
                if fix_result is not None:
                    fix_result.defect_id = "D1→A3"
            elif _line_is_within_display_math(tex_content, line_number):
                new_content, fix_result = fix_equation_overflow(
                    tex_content,
                    line_number=line_number,
                )
            elif _line_is_within_table_like_env(tex_content, line_number):
                new_content, fix_result = fix_table_overflow(
                    tex_content,
                    line_number=line_number,
                )
            else:
                new_content, fix_result = fix_paragraph_overflow(
                    tex_content,
                    overfull_line=defect.get("description", ""),
                    overflow_amount=overflow_amount,
                    line_number=line_number,
                )

        elif defect_id == "D2":
            new_content, fix_result = fix_equation_overflow(
                tex_content,
                equation_label=object_name if object_name.startswith("eq:") else None,
                line_number=line_number,
            )

        elif defect_id == "D3":
            # 尝试修复 URL
            url = defect.get("url", "")
            new_content, fix_result = fix_url_overflow(tex_content, url=url)
            if not fix_result:
                # 尝试修复参考文献 URL 断行
                new_content, fix_result = fix_bibliography_url_breaking(tex_content)

        # 检查修复是否成功
        if fix_result and new_content != tex_content:
            tex_content = new_content
            fix_result.page = page
            fix_result.line_number = line_number
            changes.append(fix_result)
            modified_files.add(str(tex_path))
        else:
            unresolved.append(
                f"{defect_id} ({object_name or '未知对象'}): 无法自动修复，可能需要语义改写或人工调整"
            )

    # 写入修改后的内容
    if modified_files:
        gate_passed, gate_reason = _passes_structure_write_gate(original_tex_content, tex_content)
        if not gate_passed:
            unresolved.append(f"图表结构硬门禁拦截：{gate_reason}")
            return OverflowFixReport(
                status="failed",
                modified_files=[],
                changes=[],
                unresolved=unresolved,
            )
        try:
            atomic_write_text(tex_path, tex_content, backup_dir=tex_path.parent / "data" / "backups")
        except OSError as e:
            unresolved.append(f"无法写入文件 {tex_path}: {e}")
            return OverflowFixReport(
                status="failed",
                modified_files=list(modified_files),
                changes=changes,
                unresolved=unresolved,
            )

    status = "success" if not unresolved else ("partial" if changes else "failed")

    return OverflowFixReport(
        status=status,
        modified_files=list(modified_files),
        changes=changes,
        unresolved=unresolved,
    )


# ============================================================
# CLI 入口
# ============================================================

def main():
    """命令行接口"""
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Fix Category D overflow defects in LaTeX documents"
    )
    parser.add_argument(
        "tex_file",
        help="Path to .tex file"
    )
    parser.add_argument(
        "--defects",
        type=str,
        help="JSON string or file path containing defect list"
    )
    parser.add_argument(
        "--json",
        "-j",
        action="store_true",
        help="Output JSON report"
    )

    args = parser.parse_args()

    # 解析缺陷列表
    defects = []
    if args.defects:
        if Path(args.defects).exists():
            with open(args.defects, 'r', encoding='utf-8') as f:
                defects = json.load(f)
        else:
            defects = json.loads(args.defects)

    # 执行修复
    report = fix_overflow_defects(args.tex_file, defects)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(f"\nOverflow Fix Report")
        print("=" * 50)
        print(f"Status: {report.status}")
        print(f"Modified files: {report.modified_files}")
        print(f"Changes: {len(report.changes)}")
        for change in report.changes:
            print(f"  - [{change.defect_id}] {change.object_name}: {change.action}")
        if report.unresolved:
            print(f"\nUnresolved: {len(report.unresolved)}")
            for u in report.unresolved:
                print(f"  - {u}")


if __name__ == "__main__":
    main()
