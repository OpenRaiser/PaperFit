"""
Shared utility functions for LaTeX fixers.

Common helper functions used across overflow_fixers, float_fixers, and space_util_fixers.
"""

import re
from typing import List


def add_package_to_preamble(tex_content: str, package_name: str) -> str:
    """在导言区添加宏包引用"""
    # 检查宏包是否已存在
    if f'\\usepackage{{{package_name}}}' in tex_content:
        return tex_content

    # 查找最后一个\usepackage
    usepackage_pattern = r'\\usepackage[^}]*\{[^}]*\}'
    matches = list(re.finditer(usepackage_pattern, tex_content))

    if matches:
        last_match = matches[-1]
        insert_pos = last_match.end()
        return tex_content[:insert_pos] + f"\n\\usepackage{{{package_name}}}" + tex_content[insert_pos:]
    else:
        doc_begin = tex_content.find('\\begin{document}')
        if doc_begin >= 0:
            return tex_content[:doc_begin] + f"\\usepackage{{{package_name}}}\n" + tex_content[doc_begin:]
    return tex_content


def add_to_preamble(tex_content: str, content: str) -> str:
    """在导言区添加内容"""
    doc_begin = tex_content.find('\\begin{document}')
    if doc_begin >= 0:
        return tex_content[:doc_begin] + content + "\n" + tex_content[doc_begin:]
    return tex_content


def find_paragraph_start(lines: List[str], target_idx: int) -> int:
    """向前查找段落开始"""
    for i in range(target_idx, -1, -1):
        line = lines[i].strip()
        if not line or line.startswith('\\begin') or line.startswith('\\section'):
            return i + 1 if i < target_idx else i
    return 0


def find_paragraph_end(lines: List[str], start_idx: int) -> int:
    """向后查找段落结束"""
    for i in range(start_idx, len(lines)):
        line = lines[i].strip()
        if not line or line.startswith('\\end') or line.startswith('\\section'):
            return i - 1 if i > start_idx else i
    return len(lines) - 1
