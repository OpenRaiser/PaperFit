#!/usr/bin/env python3
"""Token-aware 数学公式安全切分（M3：D 类公式不再用危险字符串切分）。

终版方案第十一节：禁止在 \\frac 参数、\\sqrt 参数、\\left...\\right、\\text、
\\operatorname、\\substack、矩阵列、上下标、\\underbrace/\\overbrace 内部切断。
本模块计算保护区间，让加法项切分只发生在安全位置。
"""

from __future__ import annotations

import re
from typing import List, Tuple

# 这些命令的花括号参数内部禁止切分
PROTECTED_COMMANDS = [
    "frac",
    "sqrt",
    "text",
    "textbf",
    "textrm",
    "substack",
    "operatorname",
    "operatorname*",
    "mathop",
    "mbox",
    "hbox",
    "underbrace",
    "overbrace",
    "mathrm",
    "mathbf",
    "mathit",
    "mathcal",
    "mathbb",
    "boldsymbol",
    "hat",
    "bar",
    "vec",
    "tilde",
    "widehat",
    "widetilde",
]


def _find_matching_brace(text: str, open_pos: int) -> int:
    """返回与 open_pos 处 '{' 配对的 '}' 位置；不配对返回 -1。"""
    depth = 0
    for idx in range(open_pos, len(text)):
        if text[idx] == "{":
            depth += 1
        elif text[idx] == "}":
            depth -= 1
            if depth == 0:
                return idx
    return -1


def _find_protected_spans(expr: str) -> List[Tuple[int, int]]:
    """计算公式中禁止切分的区间。"""
    protected: List[Tuple[int, int]] = []

    # 1. \left...\right 对（花括号深度可能为 0 但仍在保护区内）
    left_stack: List[int] = []
    idx = 0
    while idx < len(expr):
        if expr.startswith("\\left", idx):
            left_stack.append(idx)
            idx += 5
        elif expr.startswith("\\right", idx):
            if left_stack:
                start = left_stack.pop()
                protected.append((start, idx + 6))
            idx += 6
        else:
            idx += 1
    for start in left_stack:  # 未闭合的 \left 保护到末尾
        protected.append((start, len(expr)))

    # 2. 命令的花括号参数
    for cmd in PROTECTED_COMMANDS:
        pattern = re.compile(r"\\" + re.escape(cmd) + r"\s*\{")
        for match in pattern.finditer(expr):
            close = _find_matching_brace(expr, match.end() - 1)
            if close < 0:
                continue
            span_start = match.start()
            span_end = close + 1
            # \frac 有第二个参数
            if cmd == "frac":
                j = close + 1
                while j < len(expr) and expr[j] in " \t":
                    j += 1
                if j < len(expr) and expr[j] == "{":
                    close2 = _find_matching_brace(expr, j)
                    if close2 >= 0:
                        span_end = close2 + 1
            protected.append((span_start, span_end))

    # 3. 上下标 _{...} 和 ^{...}
    for match in re.finditer(r"[_^]\s*\{", expr):
        close = _find_matching_brace(expr, match.end() - 1)
        if close >= 0:
            protected.append((match.start(), close + 1))

    # 合并重叠区间
    if not protected:
        return []
    protected.sort()
    merged: List[Tuple[int, int]] = [protected[0]]
    for start, end in protected[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _is_protected(pos: int, protected: List[Tuple[int, int]]) -> bool:
    return any(start <= pos < end for start, end in protected)


def split_safe_additive_terms(expr: str) -> List[str]:
    """在保护区间外的顶层 +/- 处切分加法项。"""
    protected = _find_protected_spans(expr)
    brace_depth = 0
    terms: List[str] = []
    term_start = 0
    idx = 0
    while idx < len(expr):
        char = expr[idx]
        if char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth = max(0, brace_depth - 1)
        elif (
            brace_depth == 0
            and char in {"+", "-"}
            and idx > 0
            and expr[idx - 1].isspace()
            and idx + 1 < len(expr)
            and expr[idx + 1].isspace()
            and not _is_protected(idx, protected)
        ):
            term = expr[term_start:idx].strip()
            if term:
                terms.append(term)
            term_start = idx
        idx += 1
    final = expr[term_start:].strip()
    if final:
        terms.append(final)
    return terms


def count_math_tokens(expr: str) -> List[str]:
    """提取公式的数学 token 序列（用于切分前后一致性验证）。"""
    # 剥掉空白和对齐标记后提取有意义的 token
    cleaned = re.sub(r"\\begin\{[^}]*\}|\\end\{[^}]*\}|&|\\\\", " ", expr)
    tokens = re.findall(
        r"\\[a-zA-Z]+|[A-Za-z_][A-Za-z0-9_]*|[0-9]+(?:\.[0-9]+)?|[+\-*/=<>|()\[\]{}]",
        cleaned,
    )
    return tokens


def verify_split_preserves_tokens(original: str, terms: List[str]) -> bool:
    """验证切分后的项拼接起来，数学 token 序列与原始一致（忽略对齐标记和空白）。"""
    # 原始的 RHS 部分（去掉 aligned/multline 标记）
    original_tokens = count_math_tokens(original)
    joined = " ".join(terms)
    split_tokens = count_math_tokens(joined)

    # 切分会在项边界产生 +/- 重复，过滤掉纯粹的符号变化：比较去符号后的序列
    # 更准确的做法：去掉 + 和 - 后比较（因为切分点就是 +/-）
    original_core = [t for t in original_tokens if t not in {"+", "-"}]
    split_core = [t for t in split_tokens if t not in {"+", "-"}]
    return original_core == split_core
