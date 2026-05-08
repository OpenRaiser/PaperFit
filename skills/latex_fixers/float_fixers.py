"""
Float Optimizer Fixers - Category B 缺陷修复

处理浮动体位置、尺寸、堆叠、跨页分裂等问题。
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .utils import add_package_to_preamble
from .shared_table_helpers import (
    convert_preserve_first_column_to_x,
    ensure_tabularx_package,
    rewrite_first_tabular_to_tabularx,
)


def fix_float_placement(
    tex_content: str,
    float_label: str | None = None,
    float_type: str = "figure",
) -> Tuple[str, Dict[str, Any]]:
    r"""
    修复浮动体远离引用的问题（B1 缺陷）。

    策略：
    1. 调整位置参数为 [htbp]
    2. 在引用点后插入\FloatBarrier
    3. 移动浮动体源码位置
    """
    change_record = {
        "defect_id": "B1-float-placement",
        "action": "none",
        "object": float_label or "unknown",
    }

    # 策略 1: 查找浮动体环境并调整位置参数
    float_pattern = rf'\\begin\{{{float_type}\}}\[([^\]]*)\]'

    def fix_position_param(match):
        current_param = match.group(1)
        # 如果参数不是理想的 [htbp]，则替换
        if 'h' not in current_param.lower() or 't' not in current_param.lower():
            change_record["action"] = f"changed position from [{current_param}] to [htbp]"
            return f"\\begin{{{float_type}}}[htbp]"
        return match.group(0)

    modified = re.sub(float_pattern, fix_position_param, tex_content, count=1)

    # 策略 2: 如果仍需要改进，在引用点后插入\FloatBarrier
    if change_record["action"] == "none" and float_label:
        # 查找引用该浮动体的\ref
        ref_pattern = rf'\\ref\{{{float_label}\}}'
        ref_match = re.search(ref_pattern, modified)
        if ref_match:
            # 在引用点后插入\FloatBarrier
            insert_pos = ref_match.end()
            # 检查是否已有\FloatBarrier
            context = modified[insert_pos:insert_pos + 50]
            if '\\FloatBarrier' not in context:
                modified = modified[:insert_pos] + f"\\FloatBarrier%\n" + modified[insert_pos:]
                change_record["action"] = f"added \\FloatBarrier after reference to {float_label}"
                # 添加 placeins 宏包
                modified = add_package_to_preamble(modified, "placeins")

    return modified, change_record


def fix_float_width(
    tex_content: str,
    float_type: str = "figure",
    is_two_column: bool = False,
) -> Tuple[str, Dict[str, Any]]:
    r"""
    修复浮动体大小不适配栏宽的问题（B2 缺陷）。

    策略：
    1. 图片宽度设为\linewidth 或\columnwidth
    2. 表格使用 tabularx
    3. 跨栏图表使用 figure*/table*
    """
    change_record = {
        "defect_id": "B2-float-width",
        "action": "none",
    }

    if float_type == "figure":
        # 查找\includegraphics 并调整宽度
        includegraphics_pattern = r'\\includegraphics\[([^\]]*)\]\{([^}]+)\}'

        def fix_graphicx_width(match):
            options = match.group(1)
            filename = match.group(2)

            # 解析当前选项
            option_pairs = options.split(',')
            width_found = False
            new_options = []
            for opt in option_pairs:
                opt = opt.strip()
                if 'width' in opt:
                    width_found = True
                    # 替换为\linewidth
                    new_options.append(f"width=\\linewidth")
                else:
                    new_options.append(opt)

            if not width_found:
                new_options.append("width=\\linewidth")
                change_record["action"] = f"added width=\\linewidth to {filename}"
            else:
                change_record["action"] = f"normalized width to \\linewidth for {filename}"

            return f"\\includegraphics[{','.join(new_options)}]{{{filename}}}"

        modified = re.sub(includegraphics_pattern, fix_graphicx_width, tex_content, count=1)

    elif float_type == "table":
        # 表格宽度修复委托给 overflow_fixers
        return fix_table_width(tex_content)

    return modified, change_record


def fix_float_fullwidth(
    tex_content: str,
    float_type: str = "table",
    float_label: str | None = None,
    template_layout: str = "two-column",
) -> Tuple[str, Dict[str, Any]]:
    r"""
    修复浮动体为满页/满栏格式（用户首选格式）。

    策略：
    1. 双栏模板：使用 table*/figure* 跨双栏
    2. 单栏模板：使用 tabularx 占满\textwidth
    3. 避免使用 \resizebox 暴力缩放

    Args:
        tex_content: .tex 文件内容
        float_type: figure 或 table
        float_label: 浮动体 label（用于定位）
        template_layout: 模板类型 ("two-column" | "single-column")

    Returns:
        (modified_content, change_record)
    """
    change_record = {
        "defect_id": "B2-fullwidth",
        "action": "none",
        "object": float_label or "unknown",
        "layout": template_layout,
    }

    if template_layout == "two-column":
        star_float_pattern = rf'\\begin\{{{float_type}\*\}}'
        already_starred = re.search(star_float_pattern, tex_content) is not None
        modified = tex_content

        # 转换为 starred 版本（跨双栏）
        if not already_starred:
            modified = modified.replace(
                f'\\begin{{{float_type}}}',
                f'\\begin{{{float_type}*}}',
                1
            )
            modified = modified.replace(
                f'\\end{{{float_type}}}',
                f'\\end{{{float_type}*}}',
                1
            )

        rewrite_info = None
        if float_type == "table":
            modified, rewrite_info = rewrite_first_tabular_to_tabularx(
                modified,
                width_spec=r"\textwidth",
                spec_converter=convert_preserve_first_column_to_x,
                tighten_tabcolsep=False,
            )
            if rewrite_info is not None:
                modified = ensure_tabularx_package(modified)
                change_record["packages_added"] = ["tabularx"]
                strategies = []
                if rewrite_info.get("removed_resizebox"):
                    strategies.append("removed_resizebox")
                if rewrite_info.get("column_spec_after"):
                    strategies.append(
                        f"converted_to_tabularx_{rewrite_info['column_spec_after']}"
                    )
                if strategies:
                    change_record["strategies_applied"] = strategies

        if modified != tex_content:
            if float_type == "table" and rewrite_info is not None:
                change_record["action"] = (
                    "converted table to table* and normalized tabular to tabularx full width"
                )
            elif already_starred:
                change_record["action"] = f"normalized existing {float_type}* full-width float"
            else:
                change_record["action"] = f"converted {float_type} to {float_type}* for full-column width"
            change_record["note"] = "table*/figure* 将跨双栏显示，通常放置在页面顶部或底部"
            return modified, change_record

    elif template_layout == "single-column":
        # 单栏模板：确保表格使用 tabularx 占满\textwidth
        if float_type == "table":
            return fix_table_fullwidth_single(tex_content)
        else:
            # Figure 在单栏模板中只需设置 width=\textwidth
            includegraphics_pattern = r'\\includegraphics\[([^\]]*)\]\{([^}]+)\}'

            def fix_width_to_textwidth(match):
                options = match.group(1)
                filename = match.group(2)
                option_pairs = options.split(',')
                new_options = []
                width_found = False

                for opt in option_pairs:
                    opt = opt.strip()
                    if 'width' in opt:
                        width_found = True
                        new_options.append('width=\\textwidth')
                    else:
                        new_options.append(opt)

                if not width_found:
                    new_options.append('width=\\textwidth')

                change_record["action"] = f"set {filename} width to \\textwidth"
                return f"\\includegraphics[{','.join(new_options)}]{{{filename}}}"

            modified = re.sub(includegraphics_pattern, fix_width_to_textwidth, tex_content, count=1)
            return modified, change_record

    if template_layout == "two-column" and re.search(star_float_pattern, tex_content):
        change_record["action"] = f"already using {float_type}* environment"
    return tex_content, change_record


def fix_table_fullwidth_single(tex_content: str) -> Tuple[str, Dict[str, Any]]:
    r"""
    单栏模板中表格满页宽度的修复。

    策略：
    1. 将 tabular 转换为 tabularx
    2. 宽度设为\textwidth
    3. 优先使用 X 列类型，而非\resizebox
    """
    change_record = {
        "defect_id": "B2-table-fullwidth-single",
        "action": "none",
    }

    # 检查是否已使用 tabularx
    if '\\begin{tabularx}' in tex_content:
        change_record["action"] = "already using tabularx"
        return tex_content, change_record

    modified, rewrite_info = rewrite_first_tabular_to_tabularx(
        tex_content,
        width_spec=r"\textwidth",
        spec_converter=convert_preserve_first_column_to_x,
        tighten_tabcolsep=False,
    )
    if rewrite_info is None:
        return tex_content, change_record

    change_record["action"] = (
        "replaced \\resizebox with tabularx"
        if rewrite_info.get("removed_resizebox")
        else f"converted tabular to tabularx with spec {rewrite_info['column_spec_after']}"
    )
    modified = ensure_tabularx_package(modified)
    change_record["packages_added"] = ["tabularx"]
    return modified, change_record


def fix_table_width(tex_content: str) -> Tuple[str, Dict[str, Any]]:
    """修复表格宽度"""
    change_record = {
        "defect_id": "B2-table-width",
        "action": "none",
    }

    modified, rewrite_info = rewrite_first_tabular_to_tabularx(
        tex_content,
        width_spec=r"\linewidth",
        spec_converter=convert_preserve_first_column_to_x,
        tighten_tabcolsep=False,
    )
    if rewrite_info is None:
        return tex_content, change_record

    change_record["action"] = "converted tabular to tabularx with \\linewidth"
    modified = ensure_tabularx_package(modified)
    change_record["packages_added"] = ["tabularx"]
    return modified, change_record


def fix_float_clustering(
    tex_content: str,
    cluster_start_line: int | None = None,
    cluster_count: int = 3,
) -> Tuple[str, Dict[str, Any]]:
    r"""
    修复浮动体连续堆叠问题（B3 缺陷）。

    策略：
    1. 分散浮动体位置参数
    2. 在浮动体之间插入正文
    3. 使用\FloatBarrier 控制
    """
    change_record = {
        "defect_id": "B3-float-clustering",
        "action": "none",
        "cluster_count": cluster_count,
    }

    # 查找连续的浮动体环境
    float_positions = []
    positions_param_pattern = r'\\begin\{(figure|table)\}(\[([^\]]*)\])?'

    for match in re.finditer(positions_param_pattern, tex_content):
        float_positions.append({
            "start": match.start(),
            "end": match.end(),
            "type": match.group(1),
            "param": match.group(3) if match.group(3) else "",
        })

    if len(float_positions) >= cluster_count:
        # 对连续的浮动体应用不同的位置参数
        position_prefs = ['[t]', '[b]', '[p]']
        for i, fp in enumerate(float_positions[:cluster_count]):
            pref = position_prefs[i % len(position_prefs)]
            # 替换位置参数
            old_pattern = rf'\\begin\{{{fp["type"]}\}}(\[{re.escape(fp["param"])}\])?'
            new_replace = f"\\begin{{{fp['type']}}}{pref}"

            # 只替换一次
            tex_content = re.sub(old_pattern, new_replace, tex_content, count=1)

        change_record["action"] = f"dispersed {cluster_count} floats with different position preferences"

    return tex_content, change_record


def fix_split_float(
    tex_content: str,
    float_type: str = "table",
    float_label: str | None = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    修复浮动体跨页分裂问题（B4 缺陷）。

    策略：
    1. 长表格使用 longtable 环境
    2. 强制表格不跨页
    3. 拆分过大的图片组
    """
    change_record = {
        "defect_id": "B4-split-float",
        "action": "none",
        "object": float_label or "unknown",
    }

    if float_type == "table":
        # 策略 1: 将普通表格转换为 longtable
        table_pattern = r'\\begin\{table\}[^\\]*\\begin\{tabular\}'

        if re.search(table_pattern, tex_content):
            # 转换为 longtable
            modified = tex_content.replace(
                '\\begin{table}',
                '\\usepackage{longtable}\n\\begin{longtable}'
            )
            modified = modified.replace('\\end{table}', '\\end{longtable}')
            change_record["action"] = "converted table to longtable"

        # 策略 2: 添加表头重复配置
        if 'longtable' in change_record.get("action", ""):
            change_record["note"] = "请手动添加\\endfirsthead 和\\endhead 配置"

    elif float_type == "figure":
        # 拆分过大的 figure 环境（需要人工判断拆分点）
        change_record["action"] = "manual_review_required"
        change_record["note"] = "请检查 figure 环境中的子图，考虑拆分为多个独立环境"

    return tex_content, change_record
