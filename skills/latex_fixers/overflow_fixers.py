"""
Overflow Repair Fixers - Category D 缺陷修复

处理 overfull hbox、公式溢出、URL 溢出等问题。
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .utils import add_package_to_preamble, add_to_preamble, find_paragraph_end, find_paragraph_start


_PARAGRAPH_BOUNDARY_RE = re.compile(
    r"\\(?:begin|end|section|subsection|subsubsection|paragraph|chapter|maketitle|appendix|bibliography|bibliographystyle)\b"
)


def _document_body_start_line(lines: List[str]) -> int:
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


def fix_overfull_hbox(
    tex_content: str,
    line_number: int,
    overflow_type: str = "paragraph",
    overflow_amount: float | None = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    修复指定行的 overfull hbox 问题。

    Args:
        tex_content: .tex 文件内容
        line_number: 问题所在行号（从 1 开始）
        overflow_type: 溢出类型 (paragraph/table/formula)
        overflow_amount: 溢出量（pt）

    Returns:
        (modified_content, change_record)
    """
    lines = tex_content.split('\n')
    if line_number < 1 or line_number > len(lines):
        return tex_content, {"status": "failed", "reason": "行号超出范围"}

    target_line = lines[line_number - 1]
    change_record = {
        "defect_id": "D1",
        "line": line_number,
        "type": overflow_type,
        "overflow_amount": overflow_amount,
    }

    # 根据溢出类型选择修复策略
    if overflow_type == "table":
        return fix_table_overflow(tex_content, line_number)
    elif overflow_type == "formula":
        return fix_long_formula(tex_content, line_number)
    else:
        return fix_paragraph_overflow(tex_content, line_number)


def fix_paragraph_overflow(tex_content: str, line_number: int | None = None) -> Tuple[str, Dict[str, Any]]:
    """
    修复段落文本溢出。

    策略：
    1. 在长单词中插入断词点 \-
    2. 添加 \emergencystretch 允许额外拉伸
    """
    lines = tex_content.split('\n')
    change_record = {
        "defect_id": "D1-paragraph",
        "action": "none",
    }

    # 策略 1: 查找长单词并添加断词点
    if line_number:
        target_idx = line_number - 1
        if 0 <= target_idx < len(lines):
            line = lines[target_idx]
            # 查找长度超过 15 的单词
            long_words = re.findall(r'\b[a-zA-Z]{15,}\b', line)
            if long_words:
                for word in long_words[:2]:  # 最多处理 2 个单词
                    # 在元音后添加断词点
                    hyphenated = add_hyphenation_points(word)
                    line = line.replace(word, hyphenated, 1)
                lines[target_idx] = line
                change_record["action"] = f"insert_hyphenation in {long_words}"

    # 策略 2: 如果仍未解决，在段落前添加\emergencystretch
    if change_record["action"] == "none":
        if line_number:
            body_start = _document_body_start_line(lines)
            target_idx = max(body_start, line_number - 1)
            candidate_idx = None
            for idx in range(max(body_start, target_idx - 3), min(len(lines), target_idx + 4)):
                if not _is_paragraph_boundary_line(lines[idx]):
                    candidate_idx = idx
                    break

            if candidate_idx is not None:
                para_start = candidate_idx
                while para_start > body_start and not _is_paragraph_boundary_line(lines[para_start - 1]):
                    para_start -= 1
                para_end = candidate_idx
                while para_end + 1 < len(lines) and not _is_paragraph_boundary_line(lines[para_end + 1]):
                    para_end += 1

                lines.insert(para_start, "{\\emergencystretch=1.5em")
                lines.insert(para_end + 2, "}")
                change_record["action"] = f"add_emergencystretch at line {para_start}"

    return '\n'.join(lines), change_record


def add_hyphenation_points(word: str) -> str:
    """
    在单词中插入 LaTeX 断词点 \-。
    简单规则：在元音后、辅音前断词。
    """
    vowels = "aeiouAEIOU"
    result = []
    for i, char in enumerate(word):
        result.append(char)
        # 在元音后且后面还有辅音时插入断词点
        if char in vowels and i < len(word) - 2:
            next_char = word[i + 1]
            if next_char not in vowels and next_char.isalpha():
                result.append("\\-")
    return ''.join(result)


def _count_top_level_columns(column_spec: str) -> int:
    spec = column_spec.strip('{}')
    depth = 0
    count = 0
    idx = 0
    while idx < len(spec):
        char = spec[idx]
        if char == '{':
            depth += 1
        elif char == '}':
            depth = max(0, depth - 1)
        elif depth == 0 and char in {'l', 'r', 'c', 'X'}:
            count += 1
        elif depth == 0 and char in {'p', 'm', 'b'} and idx + 1 < len(spec) and spec[idx + 1] == '{':
            count += 1
        idx += 1
    return count


def _tighten_table_layout(table_tex: str) -> tuple[str, list[str]]:
    actions: list[str] = []
    tabular_match = re.search(r'\\begin\{tabularx?\}(?:\{[^}]+\})?(\{[^}]+\})', table_tex)
    if not tabular_match:
        return table_tex, actions

    column_spec = tabular_match.group(1)
    column_count = _count_top_level_columns(column_spec)
    updated = table_tex

    if column_count >= 6 and r'\begin{table*}' not in updated:
        updated = re.sub(r'\\begin\{table\}(?:\[[^\]]*\])?', r'\\begin{table*}[t]', updated, count=1)
        updated = updated.replace(r'\end{table}', r'\end{table*}', 1)
        updated = updated.replace(r'\linewidth', r'\textwidth')
        actions.append('promote_to_table_star')

    if column_count >= 6 and r'\small' not in updated and r'\footnotesize' not in updated:
        begin_match = re.search(r'\\begin\{table\*?\}(?:\[[^\]]*\])?', updated)
        if begin_match:
            updated = updated[:begin_match.end()] + '\n\\small' + updated[begin_match.end():]
            actions.append('add_small')

    tabcolsep_match = re.search(r'\\setlength\{\\tabcolsep\}\{([0-9.]+)mm\}', updated)
    if tabcolsep_match:
        current = float(tabcolsep_match.group(1))
        if current > 2.0:
            updated = (
                updated[:tabcolsep_match.start()]
                + r'\setlength{\tabcolsep}{2mm}'
                + updated[tabcolsep_match.end():]
            )
            actions.append('tighten_tabcolsep_to_2mm')
    elif column_count >= 5:
        centering_match = re.search(r'\\centering', updated)
        insert_at = centering_match.end() if centering_match else 0
        updated = updated[:insert_at] + '\n\\setlength{\\tabcolsep}{2mm}' + updated[insert_at:]
        actions.append('insert_tabcolsep_2mm')

    return updated, actions


def fix_table_overflow(tex_content: str, line_number: int | None = None) -> Tuple[str, Dict[str, Any]]:
    """
    修复表格溢出。

    策略：
    1. 将 tabular 替换为 tabularx
    2. 设置列宽为\linewidth
    3. 使用 p{width}列类型
    """
    change_record = {
        "defect_id": "D1-table",
        "action": "none",
    }

    table_pattern = re.compile(r'\\begin\{table\*?\}(?:\[[^\]]*\])?.*?\\end\{table\*?\}', re.DOTALL)
    matches = list(table_pattern.finditer(tex_content))
    if not matches:
        return tex_content, change_record

    target_match = matches[0]
    if line_number:
        for match in matches:
            line_start = tex_content.count('\n', 0, match.start()) + 1
            line_end = tex_content.count('\n', 0, match.end()) + 1
            if line_start <= line_number <= line_end:
                target_match = match
                break

    full_table = target_match.group(0)
    tightened_table, actions = _tighten_table_layout(full_table)
    if actions:
        modified = tex_content[:target_match.start()] + tightened_table + tex_content[target_match.end():]
        change_record["action"] = ", ".join(actions)
        return modified, change_record

    # 回退策略：查找 tabular 环境并替换为 tabularx
    tabular_pattern = r'\\begin\{tabular\}(\{[^}]*\})'

    def replace_with_tabularx(match):
        col_spec = match.group(1)
        cols = col_spec.strip('{}').split('|')
        new_cols = []
        for col in cols:
            if col.strip() in ['l', 'c', 'r']:
                new_cols.append('X')
            else:
                new_cols.append(col)
        new_spec = '{' + '|'.join(new_cols) + '}'
        change_record["action"] = f"replaced tabular with tabularx, col spec: {new_spec}"
        return f"\\begin{{tabularx}}{{\\linewidth}}{new_spec}"

    modified = re.sub(tabular_pattern, replace_with_tabularx, tex_content, count=1)
    if modified != tex_content and '\\usepackage{tabularx}' not in modified:
        modified = add_package_to_preamble(modified, "tabularx")
        change_record["packages_added"] = ["tabularx"]
    return modified, change_record


def _find_equation_block(tex_content: str, line_number: int | None = None):
    pattern = re.compile(r'\\begin\{equation\*?\}(.*?)\\end\{equation\*?\}', re.DOTALL)
    matches = list(pattern.finditer(tex_content))
    if not matches:
        return None
    if line_number is None:
        return matches[0]
    for match in matches:
        line_start = tex_content.count('\n', 0, match.start()) + 1
        line_end = tex_content.count('\n', 0, match.end()) + 1
        if line_start - 2 <= line_number <= line_end + 2:
            return match
    return matches[0]


def _rewrite_equation_body(body: str) -> str | None:
    normalized = " ".join(body.strip().split())
    if "=" not in normalized:
        return None
    lhs, rhs = normalized.split("=", 1)
    lhs = lhs.strip()
    rhs = rhs.strip()
    for token in [r" + \frac", r" - \frac", r" + \sum", r" - \sum", r" + ", r" - ", ") ("]:
        idx = rhs.rfind(token)
        if idx <= 0:
            continue
        if token == ") (":
            left = rhs[: idx + 1].rstrip()
            right = rhs[idx + 2 :].lstrip()
            return (
                "\\begin{equation}\n"
                "\\begin{split}\n"
                f"  {lhs} &= {left} \\\\\n"
                f"  &{right}\n"
                "\\end{split}\n"
                "\\end{equation}"
            )
        left = rhs[:idx].rstrip()
        right = rhs[idx:].lstrip()
        return (
            "\\begin{equation}\n"
            "\\begin{split}\n"
            f"  {lhs} &= {left} \\\\\n"
            f"  &{right}\n"
            "\\end{split}\n"
            "\\end{equation}"
        )
    return None


def fix_long_formula(tex_content: str, line_number: int | None = None) -> Tuple[str, Dict[str, Any]]:
    """
    修复长公式溢出。

    策略：
    1. 将 equation 替换为 multline 或 split
    2. 在运算符后添加换行\\\\
    """
    change_record = {
        "defect_id": "D2-formula",
        "action": "none",
    }

    match = _find_equation_block(tex_content, line_number)
    if not match:
        return tex_content, change_record

    original = match.group(0)
    rewritten = _rewrite_equation_body(match.group(1))
    if rewritten is None:
        stripped = match.group(1).strip()
        rewritten = "\\begin{multline}\n" + stripped + "\n\\end{multline}"
        change_record["action"] = "fallback_to_multline"
    else:
        change_record["action"] = "rewrite_equation_to_split"

    modified = tex_content[:match.start()] + rewritten + tex_content[match.end():]
    return modified, change_record


def fix_url_overflow(tex_content: str, url: str | None = None) -> Tuple[str, Dict[str, Any]]:
    """
    修复 URL 溢出。

    策略：
    1. 使用\\url{}命令包裹
    2. 添加\\urlbreaks 配置
    """
    change_record = {
        "defect_id": "D3-url",
        "action": "none",
    }

    # 策略 1: 查找裸 URL 并替换为\\url{}
    url_pattern = r'(https?://[^\s\}\]\)]+)'

    def wrap_with_url(match):
        raw_url = match.group(1)
        if not raw_url.startswith('\\url{'):
            change_record["action"] = f"wrapped URL with \\url command"
            return f"\\url{{{raw_url}}}"
        return raw_url

    modified = re.sub(url_pattern, wrap_with_url, tex_content)

    # 策略 2: 添加 URL 断行配置
    if change_record["action"] and '\\def\\UrlBreaks' not in modified:
        config_line = "\\def\\UrlBreaks{\\do\\/\\do-}"
        # 在导言区添加
        modified = add_to_preamble(modified, config_line)
        change_record["preamble_added"] = config_line

    return modified, change_record
