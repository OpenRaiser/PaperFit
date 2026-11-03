#!/usr/bin/env python3
"""多文件 overfull 解析：从 LaTeX 日志识别溢出发生在哪个 \\input 的子文件里。

LaTeX 日志用 (filename.tex 标记进入/退出子文件。overfull 警告的
"at lines N--M" 行号属于最近打开的子文件，不是 main.tex。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# TeX 的文件标记可能在行中，含带空格的引号路径；诊断里的普通括号
# 也必须平衡，不能把每个行尾 ')' 都当作源文件退出。
_FILE_OPEN_RE = re.compile(
    r'\((?:"([^"\r\n]+\.(?:tex|sty|cls|aux|bbl|def|cfg|fd|clo|ltx|ldf))"'
    r'|([^\s()"<>]+\.(?:tex|sty|cls|aux|bbl|def|cfg|fd|clo|ltx|ldf))(?=[\s)]|$))',
    re.IGNORECASE,
)
_OVERFULL_RE = re.compile(
    r"Overfull \\hbox \((\d+(?:\.\d+)?)pt too wide\).*?"
    r"(?:at lines (\d+)--(\d+)|detected at line (\d+))",
)
_BOX_WARNING_RE = re.compile(r"(?:Overfull|Underfull) \\[hv]box\b")


def resolve_overfull_to_source(
    log_text: str,
    project_root: Path,
    main_tex_name: str = "main.tex",
) -> List[Dict]:
    """把每条 overfull 警告解析到实际源文件和行号。

    返回 [{overflow_pt, file, line_start, line_end, file_display}, ...]
    """
    results: List[Dict] = []

    # 扫描日志，跟踪当前打开的文件栈
    # None 表示普通括号组，源文件上下文由最近的非空栈项提供。
    file_stack: List[Optional[str]] = [main_tex_name]
    in_box_dump = False

    # 逐行扫描
    for line in log_text.splitlines():
        # 检测 overfull 警告
        overfull_match = _OVERFULL_RE.search(line)
        if overfull_match:
            pt = float(overfull_match.group(1))
            line_start = int(overfull_match.group(2) or overfull_match.group(4))
            line_end = int(overfull_match.group(3) or line_start)
            # 当前行号属于栈顶文件
            current_file = next((item for item in reversed(file_stack) if item), main_tex_name)
            results.append({
                "overflow_pt": pt,
                "file": current_file,
                "line_start": line_start,
                "line_end": line_end,
                "file_display": current_file,
            })
            in_box_dump = True
            continue

        if _BOX_WARNING_RE.search(line):
            in_box_dump = True
            continue
        if in_box_dump:
            # TeX prints manuscript glyphs here, including unmatched parentheses
            # and strings that look like filenames. They are not file markers.
            if not line.strip():
                in_box_dump = False
            # Even a standalone ')' can be a wrapped manuscript glyph. Only
            # TeX's blank diagnostic terminator ends this region.
            continue

        pos = 0
        while pos < len(line):
            char = line[pos]
            if char == "(" and (pos == 0 or line[pos - 1] != "\\"):
                opened = _FILE_OPEN_RE.match(line, pos)
                if opened:
                    file_stack.append(opened.group(1) or opened.group(2))
                    pos = opened.end()
                    continue
                file_stack.append(None)
            elif char == ")" and (pos == 0 or line[pos - 1] != "\\") and len(file_stack) > 1:
                file_stack.pop()
            pos += 1

    return results


def find_file_in_project(project_root: Path, filename: str) -> Optional[Path]:
    """在项目目录里查找文件（可能在子目录）。"""
    # 直接匹配
    root = project_root.resolve()
    direct = (root / filename).resolve()
    try:
        direct.relative_to(root)
    except ValueError:
        return None
    if direct.is_file():
        return direct
    # 旧日志有时仅保留文件名；重名时不猜测，交给宿主定位。
    candidates = [candidate for candidate in root.rglob(filename) if candidate.is_file()]
    return candidates[0] if len(candidates) == 1 else None


def get_overfull_context(
    project_root: Path,
    resolution: Dict,
    context_lines: int = 3,
) -> str:
    """获取溢出位置的源码上下文。"""
    file_path = find_file_in_project(project_root, resolution["file"])
    if file_path is None:
        return ""
    try:
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    start = max(0, resolution["line_start"] - 1 - context_lines)
    end = min(len(lines), resolution["line_end"] + context_lines)
    return "\n".join(f"{i+1:4}: {lines[i]}" for i in range(start, end))
