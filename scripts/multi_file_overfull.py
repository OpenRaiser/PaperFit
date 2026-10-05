#!/usr/bin/env python3
"""多文件 overfull 解析：从 LaTeX 日志识别溢出发生在哪个 \\input 的子文件里。

LaTeX 日志用 (filename.tex 标记进入/退出子文件。overfull 警告的
"at lines N--M" 行号属于最近打开的子文件，不是 main.tex。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# 日志中的子文件进出标记
_FILE_OPEN_RE = re.compile(r"^\(([\w\-./\\]+\.\w+)", re.MULTILINE)
_OVERFULL_RE = re.compile(
    r"Overfull \\hbox \((\d+(?:\.\d+)?)pt too wide\).*?at lines (\d+)--(\d+)",
    re.MULTILINE,
)


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
    file_stack: List[str] = [main_tex_name]
    
    # 逐行扫描
    for line in log_text.splitlines():
        # 检测子文件打开: (filename.tex
        open_match = re.match(r"^\(([\w\-./\\]+\.\w+)", line)
        if open_match:
            file_stack.append(open_match.group(1))
            continue
        
        # 检测 overfull 警告
        overfull_match = re.search(
            r"Overfull \\hbox \((\d+(?:\.\d+)?)pt too wide\).*?at lines (\d+)--(\d+)",
            line,
        )
        if overfull_match:
            pt = float(overfull_match.group(1))
            line_start = int(overfull_match.group(2))
            line_end = int(overfull_match.group(3))
            # 当前行号属于栈顶文件
            current_file = file_stack[-1] if file_stack else main_tex_name
            results.append({
                "overflow_pt": pt,
                "file": current_file,
                "line_start": line_start,
                "line_end": line_end,
                "file_display": current_file,
            })
            continue
        
        # 检测文件关闭: )
        # 简化：行尾的 ) 表示关闭一个文件
        stripped = line.rstrip()
        if stripped.endswith(")") and not stripped.endswith("\\)") and len(file_stack) > 1:
            file_stack.pop()
    
    return results


def find_file_in_project(project_root: Path, filename: str) -> Optional[Path]:
    """在项目目录里查找文件（可能在子目录）。"""
    # 直接匹配
    direct = project_root / filename
    if direct.is_file():
        return direct
    # 递归查找
    for candidate in project_root.rglob(filename):
        return candidate
    return None


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
