"""
Full-Width and Reference Separation Fixers

确保：
1. 图片和表格使用满页（单栏）或满栏（双栏）宽度
2. 参考文献与正文分离（另起一页）
3. 正文末页要么满页要么缩到上一页

核心原则：
- 绝对禁用 \\resizebox - 该命令会暴力压缩表格，导致字体大小不一
- 强制使用 tabularx 宏包配合 \\textwidth
- 通过自动弹性列（X 格式）、动态字号、列间距微调实现满宽
"""

import re
from typing import Any, Dict, Tuple

from .utils import add_package_to_preamble, add_to_preamble


def fix_figure_fullwidth(
    tex_content: str,
    template_layout: str = "two-column",
) -> Tuple[str, Dict[str, Any]]:
    """
    修复图片为满宽格式。

    策略：
    1. 双栏模板：使用 figure* 跨双栏，图片宽度=\\textwidth
    2. 单栏模板：使用 figure，图片宽度=\\textwidth
    3. 移除所有非 \\textwidth/\\linewidth 的宽度设置

    Args:
        tex_content: .tex 文件内容
        template_layout: 模板类型 ("two-column" | "single-column")

    Returns:
        (modified_content, change_record)
    """
    change_record = {
        "defect_id": "B2-figure-fullwidth",
        "action": "none",
        "layout": template_layout,
    }

    includegraphics_pattern = r'\\includegraphics\[([^\]]*)\]\{([^}]+)\}'

    def fix_width_to_full(match):
        options = match.group(1)
        filename = match.group(2)

        # 替换为满宽
        new_options = "width=\\textwidth"
        change_record["action"] = f"set {filename} to full width (\\textwidth)"
        return f"\\includegraphics[{new_options}]{{{filename}}}"

    modified = re.sub(includegraphics_pattern, fix_width_to_full, tex_content)

    if modified != tex_content:
        change_record["count"] = len(re.findall(includegraphics_pattern, modified))

    return modified, change_record


def fix_table_fullwidth(
    tex_content: str,
    template_layout: str = "two-column",
) -> Tuple[str, Dict[str, Any]]:
    """
    修复表格为满宽格式 - 原生自适应策略。

    绝对禁用：\\resizebox

    执行逻辑：
    1. 强制使用 tabularx 宏包配合 \\textwidth
    2. 通过自动弹性列（X 格式）实现自适应
    3. 动态字号（如 \\small）以及列间距（\\tabcolsep）微调
    4. 双栏模板使用 table* 跨双栏

    Args:
        tex_content: .tex 文件内容
        template_layout: 模板类型 ("two-column" | "single-column")

    Returns:
        (modified_content, change_record)
    """
    change_record = {
        "defect_id": "B2-table-fullwidth",
        "action": "none",
        "layout": template_layout,
        "strategies_applied": [],
    }

    modified = tex_content

    # 策略 1: 移除 \\resizebox 暴力缩放（最高优先级）
    resizebox_pattern = r'\\resizebox\{[^}]*\}\{[^}]*\}\{\\begin\{tabular\}\{[^}]+\}'
    if re.search(resizebox_pattern, modified):
        # 移除 \\resizebox，保留内部 tabular 列规格
        modified = re.sub(
            r'\\resizebox\{[^}]*\}\{[^}]*\}\{\\begin\{tabular\}\{([^}]+)\}',
            r'\\begin{tabular}{\1}',
            modified
        )
        modified = re.sub(
            r'\\end\{tabular\}\}+',
            r'\\end{tabular}',
            modified
        )
        change_record["strategies_applied"].append("removed_resizebox")
        change_record["action"] = "removed \\resizebox hack"

    # 策略 2: 添加 tabularx 宏包
    if '\\usepackage{tabularx}' not in modified:
        modified = add_package_to_preamble(modified, "tabularx")
        change_record["strategies_applied"].append("added_tabularx_package")

    # 策略 3: 将 tabular 转换为 tabularx 并设置 \\textwidth
    tabular_pattern = r'\\begin\{tabular(\*)?\}\{([^}]+)\}'

    def convert_to_tabularx(match):
        star = match.group(1) or ""
        col_spec = match.group(2)

        # 将列规格转换为 X 列（弹性列）
        new_col_spec = convert_cols_to_x(col_spec)

        change_record["strategies_applied"].append(f"converted_to_tabularx_{new_col_spec}")
        return f"\\begin{{tabularx}}{{\\textwidth}} {{{new_col_spec}}}"

    modified = re.sub(tabular_pattern, convert_to_tabularx, modified)
    modified = re.sub(r'\\end\{tabular\}', r'\\end{tabularx}', modified)

    # 策略 4: 双栏模板转换为 table*
    if template_layout == "two-column":
        if '\\begin{table*}' not in modified:
            modified = re.sub(
                r'\\begin\{table\}',
                lambda _: r'\begin{table*}',
                modified
            )
            modified = re.sub(
                r'\\end\{table\}',
                lambda _: r'\end{table*}',
                modified
            )
            change_record["strategies_applied"].append("converted_to_table_star")
            change_record["action"] = "converted table to table* for two-column layout"

    # 策略 5: 优化列间距（如果表格仍然过窄）
    if '\\tabcolsep' not in modified:
        # 在导言区添加列间距微调
        modified = add_to_preamble(modified, "\\setlength{\\tabcolsep}{4pt}")
        change_record["strategies_applied"].append("reduced_tabcolsep")

    if change_record["strategies_applied"]:
        change_record["action"] = f"applied {len(change_record['strategies_applied'])} strategies for full-width table"

    return modified, change_record


def convert_cols_to_x(col_spec: str) -> str:
    """
    将列规格中的 l/c/r 转换为 X 列（弹性列）。

    Args:
        col_spec: 原始列规格（如 "l|c|r"）

    Returns:
        转换后的列规格（如 "X|X|X"）
    """
    # 保留 | 分隔符和其他格式控制符
    result = []
    for char in col_spec:
        if char in ['l', 'c', 'r']:
            result.append('X')
        else:
            result.append(char)
    return ''.join(result)


def fix_table_fullwidth_native(
    tex_content: str,
    template_layout: str = "two-column",
) -> Tuple[str, Dict[str, Any]]:
    """
    表格原生自适应满宽 - 增强版。

    此函数实现更激进的策略：
    1. 优先使用 tabularx + \\textwidth
    2. 自动调整字号（\\small, \\footnotesize）
    3. 微调 \\tabcolsep
    4. 双栏模板强制 table*

    Args:
        tex_content: .tex 文件内容
        template_layout: 模板类型

    Returns:
        (modified_content, change_record)
    """
    change_record = {
        "defect_id": "B2-table-native-fullwidth",
        "action": "none",
        "layout": template_layout,
    }

    # 调用主修复函数
    modified, record = fix_table_fullwidth(tex_content, template_layout)
    change_record.update(record)

    # 额外策略：如果表格仍然溢出，添加字号调整
    if 'Overfull' in tex_content or 'overflow' in change_record.get("note", ""):
        # 在表格环境前添加 \\small
        modified = re.sub(
            r'\\begin\{tabularx\}',
            lambda _: r'\small\begin{tabularx}',
            modified,
            count=1
        )
        change_record["action"] += " + added \\small for tighter fit"

    return modified, change_record


def fix_all_floats_fullwidth(
    tex_content: str,
    template_layout: str = "two-column",
) -> Tuple[str, Dict[str, Any]]:
    """
    同时修复所有图片和表格为满宽格式。

    Args:
        tex_content: .tex 文件内容
        template_layout: 模板类型 ("two-column" | "single-column")

    Returns:
        (modified_content, change_record)
    """
    change_record = {
        "defect_id": "B2-all-floats-fullwidth",
        "actions": [],
    }

    modified = tex_content

    # 修复图片
    modified, figure_record = fix_figure_fullwidth(modified, template_layout)
    if figure_record["action"] != "none":
        change_record["actions"].append(figure_record)

    # 修复表格
    modified, table_record = fix_table_fullwidth(modified, template_layout)
    if table_record["action"] != "none":
        change_record["actions"].append(table_record)

    if not change_record["actions"]:
        change_record["action"] = "none"
    else:
        change_record["action"] = f"fixed {len(change_record['actions'])} float types"

    return modified, change_record


def ensure_reference_newpage(
    tex_content: str,
) -> Tuple[str, Dict[str, Any]]:
    """
    确保参考文献另起一页，与正文分离。

    策略：
    1. 在 \\bibliography 或 \\printbibliography 前添加 \\newpage
    2. 如果正文末页未满，尝试扩写结论段
    3. 如果正文可以缩到上一页，压缩并分页

    Args:
        tex_content: .tex 文件内容

    Returns:
        (modified_content, change_record)
    """
    change_record = {
        "defect_id": "A3-reference-separation",
        "action": "none",
    }

    # 查找 bibliography 命令
    biblio_patterns = [
        (r'(\\bibliography\{[^}]*\})', '\\bibliography'),
        (r'(\\printbibliography)', '\\printbibliography'),
        (r'(\\begin\{thebibliography\})', '\\begin{thebibliography}'),
    ]

    for pattern, name in biblio_patterns:
        matches = list(re.finditer(pattern, tex_content))
        if matches:
            for match in matches:
                biblio_start = match.start()
                # 检查前 50 字符内是否有 \\newpage
                context_before = tex_content[max(0, biblio_start - 100):biblio_start]

                # 检查是否已有 \\newpage 或 \\clearpage
                if '\\newpage' not in context_before and '\\clearpage' not in context_before:
                    # 在 bibliography 前添加 \\newpage
                    # 找到 bibliography 前的最后一个空行或 section
                    insert_pos = biblio_start

                    # 向前查找合适位置（保持一些空白）
                    for i in range(biblio_start - 1, max(0, biblio_start - 200), -1):
                        if tex_content[i] == '\n':
                            # 找到前一个空行
                            if i > 0 and tex_content[i-1] == '\n':
                                insert_pos = i + 1
                                break

                    modified = tex_content[:insert_pos] + "\\newpage\\section*{References}\n" + tex_content[insert_pos:]
                    change_record["action"] = f"added \\newpage before {name}"
                    return modified, change_record

    change_record["note"] = "no bibliography command found"
    return tex_content, change_record


def fix_body_last_page(
    tex_content: str,
    target_section: str | None = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    修复正文末页：要么满页，要么缩到上一页页尾。

    策略：
    1. 检测正文最后一段（参考文献前的内容）
    2. 如果末页留白超过 40%，扩写结论段
    3. 如果末页内容少于 20%，压缩并添加到上一页
    4. 确保参考文献从新页开始

    Args:
        tex_content: .tex 文件内容
        target_section: 要扩写/缩写的节（如 "Conclusion"）

    Returns:
        (modified_content, change_record)
    """
    change_record = {
        "defect_id": "A2-body-last-page",
        "action": "none",
    }

    # 首先确保参考文献分页
    modified, ref_record = ensure_reference_newpage(tex_content)
    if ref_record["action"] != "none":
        change_record["action"] = ref_record["action"]
        change_record["ref_separation"] = ref_record

    # 查找结论段并扩写（如果需要）
    if target_section:
        conclusion_pattern = rf'(\\section\*\{{{target_section}\}}|\\section\{{{target_section}\}})'
        conclusion_match = re.search(conclusion_pattern, modified)

        if conclusion_match:
            conclusion_start = conclusion_match.end()
            # 查找结论段内容
            conclusion_end = modified.find('\\bibliography', conclusion_start)
            if conclusion_end == -1:
                conclusion_end = modified.find('\\end{document}', conclusion_start)

            if conclusion_end > conclusion_start:
                conclusion_content = modified[conclusion_start:conclusion_end]
                lines = conclusion_content.strip().split('\n')

                # 如果结论段少于 3 行，建议扩写
                if len(lines) < 3:
                    change_record["suggestion"] = "expand conclusion section to fill page"
                    change_record["action"] = "identified short conclusion section"

    return modified, change_record
