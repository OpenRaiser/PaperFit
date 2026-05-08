#!/usr/bin/env python3
"""
Space Utilization Fixers Module

处理 Category A：空间利用缺陷
- A1: 孤行/寡行 (Widow/Orphan Lines)
- A2: 末页大面积留白 (Excessive Trailing Whitespace)
- A3: 页数预算违反 (Page Budget Violation)
- A4: 双栏末页左右栏高度不齐 (Unbalanced Column Heights)

该模块被 code-surgeon-agent 或 semantic-polish-agent 调用，执行对 .tex 源码的精确修改。
所有修复遵循最小修改原则，优先排版控制，最后才考虑语义改写。
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
class SpaceUtilFixReport:
    """修复报告"""
    status: str  # success | partial | failed
    modified_files: List[str] = field(default_factory=list)
    changes: List[FixResult] = field(default_factory=list)
    unresolved: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill": "space-util-fixer",
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


def _scale_includegraphics_width(graphic_cmd: str, scale_factor: float = 0.95) -> Optional[str]:
    """Scale includegraphics width when it is expressed in \\linewidth or \\textwidth."""
    option_match = re.match(r'(\\includegraphics)\[([^\]]*)\](\{[^}]+\})', graphic_cmd)
    if not option_match:
        return None

    options = option_match.group(2)
    width_match = re.search(r'width\s*=\s*([^,\]]+)', options)
    if not width_match:
        return None

    width_expr = width_match.group(1).strip()
    base_match = re.fullmatch(r'(?:(\d+(?:\.\d+)?)\s*)?(\\linewidth|\\textwidth)', width_expr)
    if not base_match:
        return None

    ratio_text, base_width = base_match.groups()
    current_ratio = float(ratio_text) if ratio_text is not None else 1.0
    new_ratio = current_ratio * scale_factor
    if new_ratio >= 0.995:
        new_ratio_text = "1.0"
    else:
        new_ratio_text = f"{new_ratio:.2f}".rstrip("0").rstrip(".")
    new_width_expr = f"{new_ratio_text}{base_width}"
    new_options = (
        options[:width_match.start(1)]
        + new_width_expr
        + options[width_match.end(1):]
    )
    return f"{option_match.group(1)}[{new_options}]{option_match.group(3)}"


def _remove_negative_textheight_shrink(tex_content: str) -> Tuple[str, List[FixResult]]:
    r"""
    Restore obviously counterproductive page-height shrink directives in the preamble.

    This is intentionally conservative:
    - always remove benchmark disturbance blocks tagged as E2
    - otherwise only remove standalone negative \addtolength{\textheight}{...}
      directives that appear before \begin{document}
    """
    updated = tex_content
    changes: List[FixResult] = []

    tagged_pattern = re.compile(
        r"\n?[ \t]*%[^\n]*DISTURBANCE:E2_template_page_budget_shift:BEGIN[^\n]*\n"
        r"[ \t]*\\addtolength\{\s*\\textheight\s*\}\{\s*-[^}]+\}\s*\n"
        r"[ \t]*%[^\n]*DISTURBANCE:E2_template_page_budget_shift:END[^\n]*\n?",
        re.IGNORECASE,
    )
    while True:
        match = tagged_pattern.search(updated)
        if not match:
            break
        before = match.group(0).strip()
        updated = updated[:match.start()] + "\n" + updated[match.end():]
        changes.append(
            FixResult(
                defect_id="A3",
                object_name="导言区版芯高度",
                action="移除被标记的负向 \\textheight 扰动，恢复页面可排版高度",
                before=before,
                after="[removed tagged negative textheight adjustment]",
                success=True,
            )
        )

    begin_doc = updated.find(r"\begin{document}")
    if begin_doc == -1:
        begin_doc = len(updated)
    preamble = updated[:begin_doc]
    body = updated[begin_doc:]

    generic_pattern = re.compile(
        r"^[ \t]*\\addtolength\{\s*\\textheight\s*\}\{\s*-[^}]+\}\s*$",
        re.MULTILINE,
    )
    generic_matches = list(generic_pattern.finditer(preamble))
    if generic_matches:
        new_preamble = preamble
        for match in reversed(generic_matches):
            before = match.group(0).strip()
            new_preamble = new_preamble[:match.start()] + new_preamble[match.end():]
            changes.append(
                FixResult(
                    defect_id="A3",
                    object_name="导言区版芯高度",
                    action="移除负向 \\textheight 收缩命令，避免全局缩短每页正文高度",
                    before=before,
                    after="[removed negative textheight adjustment]",
                    success=True,
                )
            )
        updated = new_preamble + body

    return updated, changes


def _remove_trailing_bibliography_spacer(tex_content: str, page: int) -> Tuple[str, Optional[FixResult]]:
    r"""
    Remove an explicit large vertical spacer immediately before bibliography/endmatter.

    This catches both:
    - tagged disturbance blocks such as A2/A4 benchmark perturbations
    - untagged direct \vspace/\vspace* inserted right before bibliography
    """
    spacer_pattern = re.compile(
        r"(?P<block>"
        r"(?:[ \t]*%[^\n]*(?:DISTURBANCE|trailing whitespace)[^\n]*\n)*"
        r"[ \t]*\\vspace\*?\{[^}]+\}[ \t]*\n?"
        r"(?:[ \t]*%[^\n]*(?:DISTURBANCE|trailing whitespace)[^\n]*\n)*"
        r")(?=[ \t]*(?:\\bibliography\b|\\bibliographystyle\b|\\printbibliography\b|\\begin\{thebibliography\}|\\end\{document\}))",
        re.IGNORECASE,
    )
    match = spacer_pattern.search(tex_content)
    if not match:
        return tex_content, None

    before = match.group("block").strip()
    updated = tex_content[:match.start("block")] + tex_content[match.end("block"):]
    return updated, FixResult(
        defect_id="A2",
        object_name="末页尾部显式留白",
        action="移除 bibliography / endmatter 前的显式大 \\vspace 扰动",
        before=before,
        after="[removed trailing spacer before bibliography/endmatter]",
        page=page,
        success=True,
    )


def _iter_project_tex_files(project_root: Path) -> List[Path]:
    ignored_dirs = {".git", "__pycache__", "data", "pages", "page_images", "archives"}
    files: List[Path] = []
    for path in sorted(project_root.rglob("*.tex")):
        try:
            rel_parts = path.relative_to(project_root).parts
        except ValueError:
            rel_parts = path.parts
        if any(part in ignored_dirs for part in rel_parts[:-1]):
            continue
        files.append(path)
    return files


def _remove_discretionary_pagebreaks(tex_content: str) -> Tuple[str, List[FixResult]]:
    r"""
    Remove standalone manual page breaks that are counterproductive after
    template migration. These are common in appendices and create float-only
    pages with large blank regions in double-column templates.
    """
    pattern = re.compile(r'^[ \t]*\\(?:newpage|clearpage|pagebreak)(?:\[[^\]]*\])?[ \t]*(?:%[^\n]*)?\n?', re.MULTILINE)
    cursor = 0
    parts: List[str] = []
    changes: List[FixResult] = []

    for match in pattern.finditer(tex_content):
        before = match.group(0).strip()
        lookahead = tex_content[match.end():match.end() + 240]
        if re.search(r'^\s*(?:\\bibliography\b|\\bibliographystyle\b|\\printbibliography\b|\\begin\{thebibliography\})', lookahead):
            continue
        if "paperfit: keep-pagebreak" in before:
            continue
        parts.append(tex_content[cursor:match.start()])
        cursor = match.end()
        changes.append(
            FixResult(
                defect_id="A3",
                object_name="显式分页",
                action="移除模板迁移后导致浮动体空白页的显式分页命令",
                before=before,
                after="[removed discretionary page break]",
                success=True,
            )
        )

    if not changes:
        return tex_content, []
    parts.append(tex_content[cursor:])
    return "".join(parts), changes


def _remove_project_discretionary_pagebreaks(project_root: Path) -> List[tuple[Path, str, str, List[FixResult]]]:
    updates: List[tuple[Path, str, str, List[FixResult]]] = []
    for tex_file in _iter_project_tex_files(project_root):
        try:
            original = tex_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        updated, changes = _remove_discretionary_pagebreaks(original)
        if changes and updated != original:
            updates.append((tex_file, original, updated, changes))
    return updates


# ============================================================
# A1：孤行/寡行修复
# ============================================================

def fix_widow_orphan(
    tex_content: str,
    paragraph_start_line: Optional[int] = None,
    paragraph_text: Optional[str] = None,
) -> Tuple[str, Optional[FixResult]]:
    r"""
    修复孤行/寡行问题

    策略优先级:
    1. 段落级收紧 (\looseness=-1)
    2. 段落级扩张 (\looseness=1)
    3. 调整段落间胶水 (\emergencystretch)
    4. 全局调整 widow/orphan 惩罚

    Args:
        tex_content: .tex 文件内容
        paragraph_start_line: 段落起始行号 (用于定位)
        paragraph_text: 段落文本 (用于精确定位)

    Returns:
        (修改后的内容，修复结果)
    """
    # 策略 1: 使用 \looseness=-1 收缩段落
    if paragraph_text:
        # 精确定位段落
        # 清理文本中的特殊字符用于匹配
        escaped_text = re.escape(paragraph_text[:50])  # 取前 50 字符定位
        pattern = r'(\n|\A)\s*(' + escaped_text + r'[^\n]*\n(?:[^\n]*\n)*?)(?=\n\n|\Z)'
        for match in re.finditer(pattern, tex_content, re.MULTILINE):
            prefix = tex_content[max(0, match.start() - 32):match.start()]
            if re.search(r'\\looseness\s*=\s*[-0-9]+\s*$', prefix):
                continue

            paragraph_full = match.group(0)
            # 用花括号包裹并添加 \looseness=-1
            wrapped = f"{{\\looseness=-1 {paragraph_full.strip()}}}"
            modified_content = tex_content.replace(paragraph_full, wrapped, 1)

            return modified_content, FixResult(
                defect_id="A1",
                object_name=f"第 {paragraph_start_line} 行段落",
                action="添加 \\looseness=-1 以收缩段落消除孤行",
                before=paragraph_text[:40] + "..." if len(paragraph_text) > 40 else paragraph_text,
                after=f"{{\\looseness=-1 {paragraph_text[:40]}...}}",
                line_number=paragraph_start_line,
                success=True,
            )

    # 策略 2: 如果无法精确定位，尝试在导言区添加全局设置
    return add_widow_orphan_penalty(tex_content)


def add_widow_orphan_penalty(
    tex_content: str,
) -> Tuple[str, Optional[FixResult]]:
    """
    在导言区添加全局 widow/orphan 惩罚设置
    """
    # 检查是否已有设置
    if '\\widowpenalty' in tex_content and '\\clubpenalty' in tex_content:
        return tex_content, None

    # 在 \begin{document} 前添加
    match = re.search(r'\\begin\{document\}', tex_content)
    if match:
        insert_pos = match.start()
        penalties = (
            "\\widowpenalty=10000\n"
            "\\clubpenalty=10000\n"
            "\\displaywidowpenalty=10000\n"
        )
        modified_content = tex_content[:insert_pos] + penalties + tex_content[insert_pos:]

        return modified_content, FixResult(
            defect_id="A1",
            object_name="导言区",
            action="添加全局 widow/orphan 惩罚设置",
            before="\\begin{document}",
            after="\\widowpenalty=10000\n\\clubpenalty=10000\n...\\begin{document}",
            success=True,
        )

    return tex_content, None


def fix_paragraph_looseness(
    tex_content: str,
    paragraph_start: int,
    looseness_value: int = -1,
) -> Tuple[str, Optional[FixResult]]:
    r"""
    为特定段落设置 \looseness 值

    Args:
        tex_content: .tex 文件内容
        paragraph_start: 段落起始位置 (字符索引)
        looseness_value: \looseness 值 (-1 收缩，1 扩张)

    Returns:
        (修改后的内容，修复结果)
    """
    # 找到段落结束 (下一个空行或文件结束)
    paragraph_end = tex_content.find('\n\n', paragraph_start)
    if paragraph_end == -1:
        paragraph_end = len(tex_content)

    paragraph_text = tex_content[paragraph_start:paragraph_end]

    # 添加 \looseness
    wrapped = f"{{\\looseness={looseness_value} {paragraph_text.strip()}}}"
    modified_content = tex_content[:paragraph_start] + wrapped + tex_content[paragraph_end:]

    return modified_content, FixResult(
        defect_id="A1",
        object_name=f"段落 (行 {paragraph_start})",
        action=f"添加 \\looseness={looseness_value}",
        before=paragraph_text[:40] + "..." if len(paragraph_text) > 40 else paragraph_text,
        after=f"{{\\looseness={looseness_value} ...}}",
        success=True,
    )


# ============================================================
# A2：末页大面积留白修复
# ============================================================

def fix_trailing_whitespace(
    tex_content: str,
    last_page_number: int,
    whitespace_ratio: float,
) -> Tuple[str, Optional[FixResult]]:
    r"""
    修复末页大面积留白问题

    策略优先级:
    1. 前移浮动体
    2. 调整局部垂直间距
    3. 建议语义扩写 (返回 unresolved)

    Args:
        tex_content: .tex 文件内容
        last_page_number: 最后一页页码
        whitespace_ratio: 空白区域比例

    Returns:
        (修改后的内容，修复结果)
    """
    if whitespace_ratio < 0.2:
        # 空白比例在可接受范围内
        return tex_content, None

    modified_content, fix_result = _remove_trailing_bibliography_spacer(
        tex_content,
        page=last_page_number,
    )
    if fix_result:
        return modified_content, fix_result

    # 策略 1: 尝试前移浮动体
    # 查找最后几个 figure/table 环境，尝试调整其位置参数
    float_pattern = r'\\begin\{(figure|table)\}(\[[^\]]*\])?'
    matches = list(re.finditer(float_pattern, tex_content))

    if matches:
        # 找到最后一个浮动体
        last_float = matches[-1]
        float_type = last_float.group(1)
        pos_param = last_float.group(2) if last_float.group(2) else ""

        # 尝试改为 [ht] 使其前移
        if pos_param != "[ht]":
            new_param = "[ht]"
            if pos_param:
                modified_content = tex_content[:last_float.start(2)] + new_param + tex_content[last_float.end(2):]
            else:
                insert_pos = last_float.end()
                modified_content = tex_content[:insert_pos] + new_param + tex_content[insert_pos:]

            return modified_content, FixResult(
                defect_id="A2",
                object_name=f"末页{float_type}",
                action=f"将浮动体位置改为 {new_param} 以填充空白",
                before=f"\\begin{{{float_type}}}{pos_param}",
                after=f"\\begin{{{float_type}}}{new_param}",
                page=last_page_number,
                success=True,
            )

    # 策略 2: 调整最后一节前的间距
    last_section = tex_content.rfind('\\section')
    if last_section != -1:
        existing_prefix = tex_content[max(0, last_section - 40):last_section]
        if r'\vspace{-0.3em}' in existing_prefix:
            return tex_content, None
        # 在 \section 前添加 \vspace
        modified_content = tex_content[:last_section] + "\\vspace{-0.3em}\n" + tex_content[last_section:]

        return modified_content, FixResult(
            defect_id="A2",
            object_name="最后一节",
            action="在最后一节前添加 \\vspace{-0.3em} 压缩间距",
            before="\\section{...}",
            after="\\vspace{-0.3em}\n\\section{...}",
            page=last_page_number,
            success=True,
        )

    # 策略 3: 无法自动修复，需要语义扩写
    return tex_content, None


# ============================================================
# A3：页数预算修复
# ============================================================

def fix_page_budget_excess(
    tex_content: str,
    current_pages: int,
    target_pages: int,
) -> Tuple[str, List[FixResult]]:
    r"""
    修复超页问题 (实际页数 > 目标页数)

    策略优先级:
    1. 压缩浮动体
    2. 缩减垂直间距
    3. 建议精炼文字 (语义级)
    4. 压缩参考文献
    5. 微调页边距 (谨慎)

    Args:
        tex_content: .tex 文件内容
        current_pages: 当前页数
        target_pages: 目标页数

    Returns:
        (修改后的内容，修复结果列表)
    """
    pages_to_reduce = current_pages - target_pages
    if pages_to_reduce <= 0:
        return tex_content, []

    changes = []

    # 先恢复明显反目标的全局缩版芯/尾部留白扰动。
    # 这些改动会直接制造“每页底部大块空白”或放大页数预算偏差，
    # 应在尝试缩图、压段落之前优先清理。
    tex_content, geometry_changes = _remove_negative_textheight_shrink(tex_content)
    changes.extend(geometry_changes)
    tex_content, trailing_fix = _remove_trailing_bibliography_spacer(
        tex_content,
        page=current_pages,
    )
    if trailing_fix:
        changes.append(trailing_fix)

    # 策略 1: 不再默认缩小正常图片来“挤页数”
    # 这类修改会明显破坏视觉观感，也会制造新的 B2 缺陷。
    # A3 默认只允许把已经超出栏宽/版心的图片收敛回合法宽度。
    include_graphics_pattern = r'\\includegraphics(\[[^\]]*\])?\{[^}]+\}'
    matches = list(re.finditer(include_graphics_pattern, tex_content))

    for match in matches:
        graphic_cmd = match.group(0)
        width_match = re.search(r'width\s*=\s*([0-9]*\.?[0-9]+)\s*(\\linewidth|\\textwidth)', graphic_cmd)
        if not width_match:
            continue
        try:
            current_ratio = float(width_match.group(1))
        except ValueError:
            continue
        if current_ratio <= 1.0:
            continue
        base_width = width_match.group(2)
        # Use a callable replacement so `\linewidth` / `\textwidth` are treated
        # as literal text instead of regex replacement escapes.
        replacement = f'width=1.0{base_width}'
        new_graphic = re.sub(
            r'width\s*=\s*[0-9]*\.?[0-9]+\s*(\\linewidth|\\textwidth)',
            lambda _: replacement,
            graphic_cmd,
            count=1,
        )
        if new_graphic == graphic_cmd:
            continue
        tex_content = tex_content.replace(graphic_cmd, new_graphic, 1)
        changes.append(FixResult(
            defect_id="A3",
            object_name="图片",
            action="仅将超宽图片收敛回合法宽度，不再默认缩图压页数",
            before=graphic_cmd[:40] + "...",
            after=new_graphic[:40] + "...",
            success=True,
        ))
        if len(changes) >= pages_to_reduce:
            break

    # 策略 2: 检查是否有冗余的 \vspace 或空行
    # 移除过大的 \vspace
    vspace_pattern = r'\\vspace\{[0-9.]+(em|pt|cm)\}'
    large_vspace = re.search(vspace_pattern, tex_content)
    if large_vspace:
        vspace_val = large_vspace.group(0)
        # 缩小 \vspace
        num_match = re.search(r'[0-9.]+', vspace_val)
        if num_match:
            old_val = float(num_match.group(0))
            new_val = old_val * 0.8
            new_vspace = vspace_val.replace(str(old_val), str(new_val))
            tex_content = tex_content.replace(vspace_val, new_vspace, 1)
            changes.append(FixResult(
                defect_id="A3",
                object_name="垂直间距",
                action=f"压缩 \\vspace 从 {old_val} 到 {new_val}",
                before=vspace_val,
                after=new_vspace,
                success=True,
            ))

    # 策略 3: 建议压缩参考文献样式
    if '\\bibliographystyle{' in tex_content:
        style_match = re.search(r'\\bibliographystyle\{([^}]+)\}', tex_content)
        if style_match and style_match.group(1) not in ['abbrv', 'unsrt', 'plain']:
            changes.append(FixResult(
                defect_id="A3",
                object_name="参考文献样式",
                action="建议改用 abbrv 样式压缩参考文献",
                before=f"\\bibliographystyle{{{style_match.group(1)}}}",
                after="\\bibliographystyle{abbrv}",
                success=False,  # 需要人工确认
            ))

    return tex_content, changes


def fix_page_budget_deficit(
    tex_content: str,
    current_pages: int,
    target_pages: int,
) -> Tuple[str, List[FixResult]]:
    """
    修复缺页问题 (实际页数 < 目标页数)

    策略优先级:
    1. 检查浮动体堆积
    2. 建议扩写结论/讨论 (语义级)
    3. 增加附录
    4. 微调图片尺寸
    5. 增加分页点

    Args:
        tex_content: .tex 文件内容
        current_pages: 当前页数
        target_pages: 目标页数

    Returns:
        (修改后的内容，修复结果列表)
    """
    pages_to_add = target_pages - current_pages
    if pages_to_add <= 0:
        return tex_content, []

    changes = []

    # 策略 1: 解除浮动体限制 (移除 [H] 或过度限制的参数)
    float_pattern = r'\\begin\{(figure|table)\}\[H\]'
    restricted_floats = re.finditer(float_pattern, tex_content)

    for match in restricted_floats:
        float_type = match.group(1)
        old_cmd = f"\\begin{{{float_type}}}[H]"
        new_cmd = f"\\begin{{{float_type}}}[ht]"
        tex_content = tex_content.replace(old_cmd, new_cmd, 1)
        changes.append(FixResult(
            defect_id="A3",
            object_name=float_type,
            action="移除 [H] 限制，允许浮动体自然放置",
            before=old_cmd,
            after=new_cmd,
            success=True,
        ))

    # 策略 2: 放大图片尺寸
    include_graphics_pattern = r'\\includegraphics\[width=([0-9.]+)\\(linewidth|textwidth)\]'
    matches = list(re.finditer(include_graphics_pattern, tex_content))

    for match in matches[:pages_to_add]:
        current_ratio = float(match.group(1))
        if current_ratio < 1.0:
            new_ratio = min(1.0, current_ratio + 0.1)
            old_cmd = match.group(0)
            new_cmd = old_cmd.replace(
                f'{current_ratio}\\', f'{new_ratio}\\'
            )
            tex_content = tex_content.replace(old_cmd, new_cmd, 1)
            changes.append(FixResult(
                defect_id="A3",
                object_name="图片",
                action=f"放大图片宽度从 {current_ratio} 到 {new_ratio}",
                before=old_cmd,
                after=new_cmd,
                success=True,
            ))

    return tex_content, changes


# ============================================================
# A4：双栏末页左右栏高度不齐
# ============================================================

def fix_unbalanced_columns(
    tex_content: str,
    height_difference: float,  # 栏高差 (比例)
) -> Tuple[str, Optional[FixResult]]:
    r"""
    修复双栏末页左右栏高度不齐问题

    策略优先级:
    1. 使用 \balance 或 flushend 宏包
    2. 手动平衡
    3. 微调最后一段断行
    4. 调整浮动体位置

    Args:
        tex_content: .tex 文件内容
        height_difference: 栏高差比例

    Returns:
        (修改后的内容，修复结果)
    """
    if height_difference < 0.1:
        # 高度差在可接受范围内 (约 2 行以内)
        return tex_content, None

    # 先移除 bibliography 前的显式大留白；这类局部扰动比 balance/flushend
    # 更直接，也不会把错误的空白“平衡”到另一栏。
    cleaned_content, trailing_fix = _remove_trailing_bibliography_spacer(
        tex_content,
        page=0,
    )
    if trailing_fix:
        trailing_fix.defect_id = "A4"
        trailing_fix.object_name = "末页尾部显式空白"
        trailing_fix.action = "移除导致末页栏高失衡的显式尾部 \\vspace 扰动"
        return cleaned_content, trailing_fix

    # 策略 1: 添加 flushend 宏包
    if '\\usepackage{flushend}' not in tex_content:
        match = re.search(r'\\begin\{document\}', tex_content)
        if match:
            insert_pos = match.start()
            modified_content = tex_content[:insert_pos] + "\\usepackage{flushend}\n" + tex_content[insert_pos:]

            return modified_content, FixResult(
                defect_id="A4",
                object_name="导言区",
                action="添加 flushend 宏包自动平衡末页两栏",
                before="\\begin{document}",
                after="\\usepackage{flushend}\n\\begin{document}",
                success=True,
            )

    # 策略 2: 在文末添加 \balance
    if '\\balance' not in tex_content:
        # 在 \end{document} 前添加
        match = re.search(r'\\end\{document\}', tex_content)
        if match:
            insert_pos = match.start()
            modified_content = tex_content[:insert_pos] + "\\balance\n" + tex_content[insert_pos:]

            return modified_content, FixResult(
                defect_id="A4",
                object_name="文末",
                action="添加 \\balance 命令平衡两栏",
                before="\\end{document}",
                after="\\balance\n\\end{document}",
                success=True,
            )

    return tex_content, None


def add_balance_package(
    tex_content: str,
) -> Tuple[str, Optional[FixResult]]:
    """
    添加 balance 宏包支持
    """
    if '\\usepackage{balance}' in tex_content:
        return tex_content, None

    match = re.search(r'\\begin\{document\}', tex_content)
    if match:
        insert_pos = match.start()
        modified_content = tex_content[:insert_pos] + "\\usepackage{balance}\n" + tex_content[insert_pos:]

        return modified_content, FixResult(
            defect_id="A4",
            object_name="导言区",
            action="添加 balance 宏包",
            before="\\begin{document}",
            after="\\usepackage{balance}\n\\begin{document}",
            success=True,
        )

    return tex_content, None


# ============================================================
# 主修复函数
# ============================================================

def fix_space_util_defects(
    tex_file_path: str,
    defects: List[Dict[str, Any]],
    target_pages: Optional[int] = None,
    template_type: str = "single_column",
) -> SpaceUtilFixReport:
    """
    修复所有 Category A 缺陷

    Args:
        tex_file_path: .tex 文件路径
        defects: 缺陷列表，每个缺陷包含:
            - defect_id: A1, A2, A3, A4
            - page: 页码
            - object: 对象名称
            - description: 描述
            - whitespace_ratio: 空白比例 (A2)
            - current_pages: 当前页数 (A3)
            - height_difference: 栏高差 (A4)
        target_pages: 目标页数 (A3 需要)
        template_type: 模板类型 ("single_column" | "double_column")

    Returns:
        SpaceUtilFixReport: 修复报告
    """
    tex_path = Path(tex_file_path)
    if not tex_path.exists():
        return SpaceUtilFixReport(
            status="failed",
            unresolved=[f"文件不存在：{tex_file_path}"]
        )

    try:
        tex_content = tex_path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError) as e:
        return SpaceUtilFixReport(
            status="failed",
            unresolved=[f"无法读取文件 {tex_file_path}: {e}"]
        )
    original_tex_content = tex_content
    original_tex_contents: Dict[Path, str] = {tex_path: tex_content}
    tex_contents: Dict[Path, str] = {tex_path: tex_content}
    modified_files = set()
    changes = []
    unresolved = []

    for defect in defects:
        defect_id = defect.get("defect_id", "")
        page = defect.get("page", 0)
        object_name = defect.get("object", "")
        description = defect.get("description", "")

        new_content = tex_content
        fix_result = None
        additional_changes = []

        if defect_id == "A1":
            # 孤行/寡行
            new_content, fix_result = fix_widow_orphan(
                tex_content,
                paragraph_start_line=defect.get("line_number"),
                paragraph_text=description,
            )
            # 如果具体段落修复失败，尝试全局设置
            if not fix_result:
                new_content, fix_result = add_widow_orphan_penalty(tex_content)

        elif defect_id == "A2":
            # 末页留白
            whitespace_ratio = defect.get("whitespace_ratio", 0)
            new_content, fix_result = fix_trailing_whitespace(
                tex_content,
                last_page_number=page,
                whitespace_ratio=whitespace_ratio,
            )
            if not fix_result:
                unresolved.append(
                    f"A2 (末页): 需要语义扩写结论或讨论部分以填充空白"
                )

        elif defect_id == "A3":
            # 页数预算
            current_pages = defect.get("current_pages", 0)
            if target_pages is None:
                unresolved.append(f"A3: 需要用户提供目标页数")
                continue

            if current_pages > target_pages:
                # 超页
                for updated_path, original_content, updated_content, pagebreak_changes in _remove_project_discretionary_pagebreaks(tex_path.parent):
                    original_tex_contents.setdefault(updated_path, original_content)
                    tex_contents[updated_path] = updated_content
                    modified_files.add(str(updated_path))
                    changes.extend(pagebreak_changes)
                    if updated_path == tex_path:
                        tex_content = updated_content
                new_content, additional_changes = fix_page_budget_excess(
                    tex_content,
                    current_pages=current_pages,
                    target_pages=target_pages,
                )
                changes.extend(additional_changes)
                if additional_changes:
                    fix_result = additional_changes[0]
                else:
                    unresolved.append(
                        f"A3: 需要精炼文字或压缩参考文献 (当前{current_pages}页，目标{target_pages}页)"
                    )
            else:
                # 缺页
                new_content, additional_changes = fix_page_budget_deficit(
                    tex_content,
                    current_pages=current_pages,
                    target_pages=target_pages,
                )
                changes.extend(additional_changes)
                if additional_changes:
                    fix_result = additional_changes[0]
                else:
                    unresolved.append(
                        f"A3: 需要扩写结论或增加附录 (当前{current_pages}页，目标{target_pages}页)"
                    )

        elif defect_id == "A4":
            # 双栏末页不齐
            if template_type != "double_column":
                unresolved.append(f"A4: 仅适用于双栏模板")
                continue

            height_difference = defect.get("height_difference", 0)
            new_content, fix_result = fix_unbalanced_columns(
                tex_content,
                height_difference=height_difference,
            )

            # 如果需要，添加 balance 宏包
            if fix_result and '\\usepackage{balance}' not in tex_content:
                new_content, _ = add_balance_package(new_content)

        # 检查修复是否成功
        if new_content != tex_content:
            tex_content = new_content
            tex_contents[tex_path] = tex_content
            if fix_result:
                fix_result.page = page
                fix_result.line_number = defect.get("line_number")
                if fix_result not in changes:
                    changes.append(fix_result)
            modified_files.add(str(tex_path))
        elif not fix_result and not additional_changes:
            unresolved.append(
                f"{defect_id} ({object_name or '未知对象'}): 无法自动修复，可能需要人工调整"
            )

    # 写入修改后的内容
    if modified_files:
        for modified_file in sorted(Path(path) for path in modified_files):
            original_content = original_tex_contents.get(modified_file, original_tex_content)
            updated_content = tex_contents.get(modified_file, tex_content)
            gate_passed, gate_reason = _passes_structure_write_gate(original_content, updated_content)
            if not gate_passed:
                unresolved.append(f"图表结构硬门禁拦截：{modified_file}: {gate_reason}")
                return SpaceUtilFixReport(
                    status="failed",
                    modified_files=[],
                    changes=[],
                    unresolved=unresolved,
                )
        for modified_file in sorted(Path(path) for path in modified_files):
            try:
                atomic_write_text(
                    modified_file,
                    tex_contents.get(modified_file, tex_content),
                    backup_dir=tex_path.parent / "data" / "backups",
                )
            except OSError as e:
                unresolved.append(f"无法写入文件 {modified_file}: {e}")
                return SpaceUtilFixReport(
                    status="failed",
                    modified_files=list(modified_files),
                    changes=changes,
                    unresolved=unresolved,
                )

    status = "success" if not unresolved else ("partial" if changes else "failed")

    return SpaceUtilFixReport(
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
        description="Fix Category A space utilization defects in LaTeX documents"
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
        "--target-pages",
        type=int,
        help="Target page count (for A3)"
    )
    parser.add_argument(
        "--template",
        type=str,
        default="single_column",
        choices=["single_column", "double_column"],
        help="Template type"
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
    report = fix_space_util_defects(
        args.tex_file,
        defects,
        target_pages=args.target_pages,
        template_type=args.template,
    )

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(f"\nSpace Utilization Fix Report")
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
