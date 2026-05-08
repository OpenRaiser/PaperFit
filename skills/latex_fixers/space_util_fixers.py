"""
Space Utilization Fixers - Category A 缺陷修复

处理孤行寡行、末页留白、页数预算、双栏不齐等问题。
"""

import re
from typing import Any, Dict, List, Tuple

from .utils import add_package_to_preamble, add_to_preamble, find_paragraph_end, find_paragraph_start


def fix_widow_orphan(
    tex_content: str,
    page_number: int | None = None,
    paragraph_line: int | None = None,
    short_line_threshold: float = 0.25,
) -> Tuple[str, Dict[str, Any]]:
    """
    修复孤行/寡行问题（A1 缺陷）- 根除策略。

    绝对禁止：
    - 段落末尾出现仅占一行 1/4 宽度或单独跨页的"小尾巴"

    执行逻辑（优先级从高到低）：
    1. 优先调用 \\looseness=-1（紧缩排版）或 \\looseness=1（扩展排版）
    2. 若物理空间依然不兼容，触发自然语言层的"句法微缩/扩写"逻辑
    3. 精准增删 3-5 个单词以平齐右边界

    Args:
        tex_content: .tex 文件内容
        page_number: 问题所在页码
        paragraph_line: 问题段落起始行号
        short_line_threshold: 段末短行阈值（默认 0.25=1/4 栏宽）

    Returns:
        (modified_content, change_record)
    """
    change_record = {
        "defect_id": "A1-widow-orphan",
        "action": "none",
        "page": page_number,
        "strategies_attempted": [],
        "semantic_intervention_needed": False,
    }

    lines = tex_content.split('\n')

    # 策略 0: 首先添加全局防护（如果尚未存在）
    if '\\widowpenalty' not in tex_content:
        preamble_additions = [
            "\\widowpenalty=10000",
            "\\clubpenalty=10000",
            "\\displaywidowpenalty=10000",
        ]
        tex_content = add_to_preamble(tex_content, '\n'.join(preamble_additions))
        change_record["strategies_attempted"].append("global_penalty")

    # 策略 1: 优先使用 \\looseness=-1 紧缩段落（消除小尾巴）
    if paragraph_line and 0 <= paragraph_line - 1 < len(lines):
        target_idx = paragraph_line - 1

        # 查找段落边界
        para_start = find_paragraph_start(lines, target_idx)
        para_end = find_paragraph_end(lines, para_start)

        if para_start >= 0 and para_end >= 0:
            para_content = '\n'.join(lines[para_start:para_end + 1])

            # 检查是否已有\looseness 设置
            if '\\looseness' not in para_content:
                # 尝试 \\looseness=-1（紧缩，减少一行）
                lines.insert(para_start, "{\\looseness=-1 ")
                lines.insert(para_end + 2, "}")
                change_record["action"] = f"added \\looseness=-1 to paragraph at line {para_start}"
                change_record["strategies_attempted"].append("looseness=-1")
                change_record["note"] = "紧缩排版以消除段末小尾巴"
                return '\n'.join(lines), change_record

    # 策略 2: 如果已有 \\looseness=-1 但仍然有问题，尝试 \\looseness=1（扩展）
    if paragraph_line:
        target_idx = paragraph_line - 1
        para_start = find_paragraph_start(lines, target_idx)
        para_end = find_paragraph_end(lines, para_start)

        if para_start >= 0 and para_end >= 0:
            para_content = '\n'.join(lines[para_start:para_end + 1])

            if '\\looseness=-1' in para_content:
                # 替换为 \\looseness=1
                para_content = para_content.replace('\\looseness=-1', '\\looseness=1')
                # 重新构建
                new_lines = lines[:para_start] + para_content.split('\n') + lines[para_end + 1:]
                change_record["action"] = f"changed to \\looseness=1 for paragraph at line {para_start}"
                change_record["strategies_attempted"].append("looseness=1")
                change_record["note"] = "扩展排版以填充空白"
                return '\n'.join(new_lines), change_record

    # 策略 3: 如果无法定位具体段落，标记需要语义干预
    change_record["semantic_intervention_needed"] = True
    change_record["action"] = "looseness_exhausted_requires_semantic"
    change_record["note"] = "排版手段已用尽，需要语义级句法微缩/扩写（增删 3-5 词）"

    return tex_content, change_record


def detect_short_tail(
    tex_content: str,
    paragraph_line: int,
    threshold_ratio: float = 0.25,
) -> Tuple[bool, float]:
    """
    检测段落末尾是否存在"小尾巴"（长度小于栏宽 1/4 的行）。

    Args:
        tex_content: .tex 文件内容
        paragraph_line: 段落起始行号
        threshold_ratio: 短行阈值（默认 0.25）

    Returns:
        (is_short_tail, line_ratio) - 是否为小尾巴及其相对长度
    """
    lines = tex_content.split('\n')

    if not (0 <= paragraph_line - 1 < len(lines)):
        return False, 0.0

    para_start = find_paragraph_start(lines, paragraph_line - 1)
    para_end = find_paragraph_end(lines, para_start)

    if para_start < 0 or para_end < 0:
        return False, 0.0

    # 获取段落最后一行
    last_line = lines[para_end].strip()

    # 计算行长（字符数近似）
    if not last_line:
        return True, 0.0  # 空行视为小尾巴

    # 估算栏宽（典型 LaTeX 文档约 80-100 字符）
    estimated_column_width = 80
    line_ratio = len(last_line) / estimated_column_width

    return line_ratio < threshold_ratio, line_ratio


def fix_trailing_whitespace(
    tex_content: str,
    target_pages: int | None = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    修复末页大面积留白问题（A2 缺陷）。

    策略：
    1. 前移浮动体到末页
    2. 调整垂直间距
    3. 扩写结论部分
    """
    change_record = {
        "defect_id": "A2-trailing-whitespace",
        "action": "none",
    }

    # 策略 1: 查找可前移的浮动体
    # 查找 figure/table 环境及其位置参数
    float_pattern = r'\\begin\{(figure|table)\}(\[([^\]]*)\])?'

    matches = list(re.finditer(float_pattern, tex_content))
    if matches:
        # 尝试将最后一个浮动体的位置参数改为 [h] 强制放在当前位置
        last_float = matches[-1]
        if last_float.group(2):  # 有位置参数
            old_param = last_float.group(3)
            if 'h' not in old_param.lower():
                # 替换为 [ht]
                new_content = tex_content[:last_float.start()]
                new_content += f"\\begin{{{last_float.group(1)}}}[ht]"
                new_content += tex_content[last_float.end():]
                tex_content = new_content
                change_record["action"] = "adjusted float position to fill whitespace"

    # 策略 2: 微调最后一节前的间距
    if change_record["action"] == "none":
        # 查找最后一个\section 或\section*
        section_pattern = r'\\section(\*)?\{[^}]+\}'
        sections = list(re.finditer(section_pattern, tex_content))
        if len(sections) >= 2:
            # 在倒数第二节后添加负间距
            last_section = sections[-1]
            # 查找节前的位置
            insert_pos = last_section.start()
            # 检查是否已有\vspace
            context_before = tex_content[max(0, insert_pos - 100):insert_pos]
            if '\\vspace' not in context_before:
                tex_content = tex_content[:insert_pos] + "\\vspace{-0.3em}\n" + tex_content[insert_pos:]
                change_record["action"] = "added negative vspace before last section"

    return tex_content, change_record


def fix_page_budget(
    tex_content: str,
    current_pages: int,
    target_pages: int,
) -> Tuple[str, Dict[str, Any]]:
    """
    修复页数预算问题（A3 缺陷）。

    策略根据超页或缺页情况不同：
    - 超页：压缩内容、精简文字
    - 缺页：扩展内容、增加分页
    """
    change_record = {
        "defect_id": "A3-page-budget",
        "action": "none",
        "current_pages": current_pages,
        "target_pages": target_pages,
        "delta": current_pages - target_pages,
    }

    if current_pages > target_pages:
        # 超页：需要压缩
        change_record["strategy"] = "compress"
        tex_content, compress_changes = compress_to_fewer_pages(tex_content)
        if compress_changes["action"] != "none":
            change_record["action"] = compress_changes["action"]
            change_record["details"] = compress_changes.get("details", [])

    elif current_pages < target_pages:
        # 缺页：需要扩展
        change_record["strategy"] = "expand"
        tex_content, expand_changes = expand_to_more_pages(tex_content)
        if expand_changes["action"] != "none":
            change_record["action"] = expand_changes["action"]
            change_record["details"] = expand_changes.get("details", [])

    return tex_content, change_record


def compress_to_fewer_pages(tex_content: str) -> Tuple[str, Dict[str, Any]]:
    """压缩到更少页数"""
    changes = {"action": "none", "details": []}

    # 策略 1: 缩小浮动体尺寸
    includegraphics_pattern = r'\\includegraphics\[([^\]]*)\]\{([^}]+)\}'

    def shrink_graphicx(match):
        options = match.group(1).split(',')
        filename = match.group(2)
        new_options = []
        width_changed = False
        for opt in options:
            opt = opt.strip()
            if 'width' in opt and 'linewidth' in opt:
                # 将\linewidth 改为 0.95\linewidth
                new_options.append('width=0.95\\linewidth')
                width_changed = True
            else:
                new_options.append(opt)
        if width_changed:
            changes["details"].append(f"shrank {filename} to 95% linewidth")
        return f"\\includegraphics[{','.join(new_options)}]{{{filename}}}"

    modified = re.sub(includegraphics_pattern, shrink_graphicx, tex_content)

    if modified != tex_content:
        changes["action"] = "compressed figure sizes"
        return modified, changes

    # 策略 2: 压缩垂直间距
    if '\\vspace{' in modified:
        # 查找并缩小\vspace
        vspace_pattern = r'\\vspace\{([^}]+)\}'

        def shrink_vspace(match):
            space = match.group(1)
            # 简单处理：如果是数字，减少 20%
            try:
                # 处理类似 1em, 2cm 等
                num_match = re.match(r'([\d.]+)(\w+)', space)
                if num_match:
                    num = float(num_match.group(1))
                    unit = num_match.group(2)
                    new_num = num * 0.8
                    changes["details"].append(f"compressed vspace from {space} to {new_num}{unit}")
                    return f"\\vspace{{{new_num}{unit}}}"
            except (ValueError, TypeError):
                pass
            return match.group(0)

        modified = re.sub(vspace_pattern, shrink_vspace, modified)

    if modified != tex_content:
        changes["action"] = "compressed vertical spaces"

    return modified, changes


def expand_to_more_pages(tex_content: str) -> Tuple[str, Dict[str, Any]]:
    """扩展到更多页数"""
    changes = {"action": "none", "details": []}

    # 策略 1: 增大浮动体尺寸
    includegraphics_pattern = r'\\includegraphics\[([^\]]*)\]\{([^}]+)\}'

    def expand_graphicx(match):
        options = match.group(1).split(',')
        filename = match.group(2)
        new_options = []
        width_changed = False
        for opt in options:
            opt = opt.strip()
            if 'width' in opt:
                if 'linewidth' in opt:
                    # 确保是完整\linewidth
                    new_options.append('width=\\linewidth')
                else:
                    new_options.append(opt)
                width_changed = True
            else:
                new_options.append(opt)
        if width_changed and '0.95\\linewidth' not in str(new_options):
            changes["details"].append(f"expanded {filename} to full linewidth")
        return f"\\includegraphics[{','.join(new_options)}]{{{filename}}}"

    modified = re.sub(includegraphics_pattern, expand_graphicx, tex_content)

    if modified != tex_content:
        changes["action"] = "expanded figure sizes"
        return modified, changes

    # 策略 2: 在合适位置增加分页
    # 在\section 前添加\newpage（谨慎使用）
    section_pattern = r'(\\section\{[^}]+\})'
    sections = list(re.finditer(section_pattern, tex_content))

    if len(sections) >= 2:
        # 在最后一节前分页
        last_section = sections[-1]
        insert_pos = last_section.start()
        # 检查是否已有\newpage
        context_before = tex_content[max(0, insert_pos - 50):insert_pos]
        if '\\newpage' not in context_before and '\\section' not in context_before:
            tex_content = tex_content[:insert_pos] + "\\newpage\n" + tex_content[insert_pos:]
            changes["action"] = "added page break before last section"

    return tex_content, changes


def fix_unbalanced_columns(
    tex_content: str,
    is_two_column: bool = True,
) -> Tuple[str, Dict[str, Any]]:
    """
    修复双栏末页左右栏高度不齐问题（A4 缺陷）。

    策略：
    1. 使用 flushend 宏包自动平衡
    2. 使用\balance 命令
    3. 微调最后一段行数
    """
    change_record = {
        "defect_id": "A4-unbalanced-columns",
        "action": "none",
    }

    if not is_two_column:
        change_record["note"] = "not a two-column layout, skipping"
        return tex_content, change_record

    # 策略 1: 添加 flushend 宏包
    if '\\usepackage{flushend}' not in tex_content and '\\usepackage{balance}' not in tex_content:
        tex_content = add_package_to_preamble(tex_content, "flushend")
        change_record["action"] = "added flushend package for automatic column balancing"
        return tex_content, change_record

    # 策略 2: 在文末添加\balance 命令
    if '\\usepackage{balance}' in tex_content:
        # 查找\end{document}
        doc_end = tex_content.find('\\end{document}')
        if doc_end > 0:
            # 向前查找最后的内容
            content_before_end = tex_content[:doc_end].rstrip()
            if '\\balance' not in content_before_end[-200:]:  # 最后 200 字符内没有
                tex_content = content_before_end + "\n\\balance\n" + tex_content[doc_end:]
                change_record["action"] = "added \\balance command before \\end{document}"

    return tex_content, change_record
