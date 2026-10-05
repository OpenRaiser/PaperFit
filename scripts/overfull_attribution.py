#!/usr/bin/env python3
"""Overfull 归因诊断：从 raw warning 到可见缺陷到修复链路的完整追踪。

回答"为什么 118 个 overfull 一个都没修"：
  raw warning → 归并去重 → 源码定位 → 可见性分类 → 候选生成 → 执行结果
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 日志里 Overfull 行的标准格式
_OVERFULL_RE = re.compile(
    r"Overfull \\hbox \((\d+(?:\.\d+)?)pt too wide\)"
    r"(?:.*?detected at line (\d+))?"
    r"(?:.*?at lines (\d+)--(\d+))?"
)

# 归并键：同一源行 + 相近溢出量视为同一问题
_MERGE_LINE_TOLERANCE = 3
_MERGE_PT_TOLERANCE = 2.0

# 可见性阈值：< 2pt 肉眼基本不可见
VISIBLE_THRESHOLD_PT = 2.0
# 可修复阈值：> 5pt 才值得生成候选
REPAIRABLE_THRESHOLD_PT = 5.0


@dataclass
class AttributedOverfull:
    """一条归因后的 overfull 记录。"""
    warning_id: str
    overflow_pt: float
    source_line: Optional[int]
    source_file: Optional[str]
    page: Optional[int]
    object_type: str          # paragraph / table / equation / url / code / bibliography / unknown
    visible: bool             # 溢出量 ≥ 可见阈值
    repairable: bool          # 溢出量 ≥ 修复阈值
    repeated_count: int       # 归并了多少条 raw warning
    group_id: Optional[str]   # 同源对象分组


def parse_overfull_warnings(log_text: str) -> List[Dict[str, Any]]:
    """从编译日志解析原始 overfull 警告。"""
    warnings = []
    for match in _OVERFULL_RE.finditer(log_text):
        pt = float(match.group(1))
        line_start = None
        if match.group(2):
            line_start = int(match.group(2))
        elif match.group(3):
            line_start = int(match.group(3))
        warnings.append({
            "overflow_pt": pt,
            "line_start": line_start,
            "line_end": int(match.group(4)) if match.group(4) else line_start,
            "raw_text": match.group(0)[:120],
        })
    return warnings


def _classify_object_type(
    tex_content: str,
    line_number: Optional[int],
) -> str:
    """根据源码行号推断溢出对象类型。"""
    if line_number is None or not tex_content:
        return "unknown"
    lines = tex_content.splitlines()
    if line_number < 1 or line_number > len(lines):
        return "unknown"
    # 向上向下各看 10 行找环境
    context = "\n".join(lines[max(0, line_number - 11):line_number + 10])
    if re.search(r"\\begin\{tabular|\\begin\{tabularx|\\begin\{longtable|\\begin\{array", context):
        return "table"
    if re.search(r"\\begin\{equation|\\begin\{align|\\begin\{multline|\\begin\{gather|\\\[", context):
        return "equation"
    if re.search(r"\\url\{|\\href\{|https?://|doi\.", context, re.IGNORECASE):
        return "url"
    if re.search(r"\\texttt\{|\\verb|\\begin\{lstlisting|\\begin\{verbatim|\\begin\{algorithm", context):
        return "code"
    if re.search(r"\\bibliography\{|\\bibliographystyle\{|\\begin\{thebibliography", context):
        return "bibliography"
    if re.search(r"\\mbox\{|\\hbox\{", context):
        return "mbox"
    # 长 token（>40 字符的连续字母数字）
    context_lines = context.splitlines()
    for cl in context_lines:
        if len(re.findall(r"[A-Za-z0-9_]{40,}", cl)) > 0:
            return "long_token"
    return "paragraph"


def attribute_overfull(
    log_text: str,
    tex_content: str,
    *,
    source_file: Optional[str] = None,
) -> Dict[str, Any]:
    """完整的 overfull 归因报告。"""
    raw = parse_overfull_warnings(log_text)

    # 按行号归并（相近行号 + 相近溢出量 = 同一源对象）
    groups: Dict[str, List[Dict]] = defaultdict(list)
    for warning in raw:
        line = warning.get("line_start")
        pt = warning["overflow_pt"]
        group_key = f"line_{line}" if line else f"pt_{int(pt)}"
        groups[group_key].append(warning)

    attributed: List[AttributedOverfull] = []
    for idx, (group_key, group_warnings) in enumerate(sorted(groups.items())):
        # 取组内最大溢出量
        max_pt = max(w["overflow_pt"] for w in group_warnings)
        representative = max(group_warnings, key=lambda w: w["overflow_pt"])
        line = representative.get("line_start")
        obj_type = _classify_object_type(tex_content, line)
        attributed.append(AttributedOverfull(
            warning_id=f"of-{idx + 1:04d}",
            overflow_pt=max_pt,
            source_line=line,
            source_file=source_file,
            page=None,  # 页码需要从渲染结果获取，此处暂缺
            object_type=obj_type,
            visible=max_pt >= VISIBLE_THRESHOLD_PT,
            repairable=max_pt >= REPAIRABLE_THRESHOLD_PT,
            repeated_count=len(group_warnings),
            group_id=group_key,
        ))

    # 统计
    raw_count = len(raw)
    attributed_count = len(attributed)
    visible_count = sum(1 for a in attributed if a.visible)
    repairable_count = sum(1 for a in attributed if a.repairable)
    by_type: Dict[str, int] = defaultdict(int)
    for a in attributed:
        by_type[a.object_type] += 1

    return {
        "raw_overfull_count": raw_count,
        "attributed_overfull_count": attributed_count,
        "visible_overfull_count": visible_count,
        "repairable_overfull_count": repairable_count,
        "weighted_overflow_score": round(sum(a.overflow_pt for a in attributed if a.visible), 1),
        "by_object_type": dict(by_type),
        "by_visibility": {
            "visible": visible_count,
            "subtle": attributed_count - visible_count,
        },
        "attributions": [
            {
                "warning_id": a.warning_id,
                "overflow_pt": a.overflow_pt,
                "source_line": a.source_line,
                "source_file": a.source_file,
                "object_type": a.object_type,
                "visible": a.visible,
                "repairable": a.repairable,
                "repeated_count": a.repeated_count,
                "group_id": a.group_id,
            }
            for a in sorted(attributed, key=lambda x: -x.overflow_pt)
        ],
    }
