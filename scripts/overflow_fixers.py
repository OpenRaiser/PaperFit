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
except ImportError:
    compute_content_diff = None
    structure_regression_reasons = None

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


def _rewrite_equation_to_multline(equation_body: str) -> Optional[str]:
    normalized = " ".join(equation_body.strip().split())
    if "=" not in normalized:
        return None

    lhs, rhs = normalized.split("=", 1)
    lhs = lhs.strip()
    rhs = rhs.strip()
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

    # 策略 1: 为长单词添加断词点
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
    # 定位表格环境
    if table_label:
        # 通过标签定位表格
        pattern = r'(\\begin\{(?:table|table\*|sidewaystable)\}(?:\[[htbp]+\])?.*?)(\\begin\{tabular\})(\{[^}]+\})(.*?)(\\end\{tabular\})(.*?\\end\{(?:table|table\*|sidewaystable)\})'
    else:
        # 查找最近的 tabular 环境
        pattern = r'(\\begin\{(?:table|table\*|sidewaystable)\}(?:\[[htbp]+\])?.*?)(\\begin\{tabular\})(\{[^}]+\})(.*?)(\\end\{tabular\})(.*?\\end\{(?:table|table\*|sidewaystable)\})'

    matches = list(re.finditer(pattern, tex_content, re.DOTALL))

    if not matches:
        return tex_content, None

    # 如果有 line_number，找到最接近的表格
    target_match = matches[0]
    if line_number:
        for match in matches:
            if match.start() <= line_number <= match.end():
                target_match = match
                break

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
        new_table, begin_count = begin_pattern.subn(
            f"\\begin{{tabularx}}{{\\linewidth}}{new_column_spec}",
            full_table,
            count=1,
        )
        if begin_count <= 0:
            return tex_content, None

        new_table, end_count = re.subn(
            r"\\end\{tabular\}",
            r"\\end{tabularx}",
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
        action="将长公式改为 multline 并在可断点处换行",
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

        if defect_id == "D1":
            # 判断是段落溢出还是表格溢出
            if "table" in object_name.lower() or "tab:" in object_name.lower():
                new_content, fix_result = fix_table_overflow(
                    tex_content,
                    table_label=object_name if object_name.startswith("tab:") else None,
                    line_number=line_number,
                )
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
