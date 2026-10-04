"""
Shared helpers for table-width normalization.

These helpers are intentionally reused by both `scripts/float_fixers.py`
and `skills/latex_fixers/float_fixers.py` so that resizebox removal,
tabular->tabularx rewriting, and tabcolsep tightening share one code path.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, Optional, Tuple


def ensure_tabularx_package(tex_content: str) -> str:
    if r"\usepackage{tabularx}" in tex_content:
        return tex_content
    match = re.search(r"\\begin\{document\}", tex_content)
    if not match:
        return tex_content
    insert_pos = match.start()
    return tex_content[:insert_pos] + "\\usepackage{tabularx}\n" + tex_content[insert_pos:]


def _strip_outer_column_spec(column_spec: str) -> str:
    spec = column_spec.strip()
    if spec.startswith("{") and spec.endswith("}"):
        return spec[1:-1]
    return spec


def convert_last_text_column_to_x(column_spec: str) -> str:
    spec = _strip_outer_column_spec(column_spec)
    text_columns = []
    for i, c in enumerate(spec):
        if c in "lrc":
            text_columns.append((i, c))
    if not text_columns:
        return "{" + spec + "}"
    last_text_idx, _ = text_columns[-1]
    new_spec = spec[:last_text_idx] + "X" + spec[last_text_idx + 1 :]
    return "{" + new_spec + "}"


def convert_preserve_first_column_to_x(column_spec: str) -> str:
    spec = _strip_outer_column_spec(column_spec)
    result = []
    seen_first_text = False
    for char in spec:
        if char in ["l", "c", "r"]:
            if not seen_first_text:
                result.append(char)
                seen_first_text = True
            else:
                result.append("X")
        else:
            result.append(char)
    return "{" + "".join(result) + "}"


def shrink_first_fixed_text_column_for_tabularx(column_spec: str) -> str:
    spec = _strip_outer_column_spec(column_spec)
    x_count = spec.count("X")
    if x_count < 6:
        return column_spec

    def replace(match: re.Match[str]) -> str:
        value = float(match.group(1))
        unit = match.group(2)
        if value < 0.17:
            return match.group(0)
        target = max(0.10, value - 0.08)
        return f"p{{{target:.2f}\\{unit}}}"

    updated, count = re.subn(
        r"p\{([0-9]+(?:\.[0-9]+)?)\\(textwidth|linewidth)\}",
        replace,
        spec,
        count=1,
    )
    return "{" + updated + "}" if count else column_spec


def is_plain_alignment_column_spec(column_spec: str) -> bool:
    spec = _strip_outer_column_spec(column_spec).replace(" ", "")
    return bool(spec) and all(char in "lrc|@" for char in spec)


def convert_plain_alignment_to_stretched_spec(column_spec: str) -> str:
    spec = _strip_outer_column_spec(column_spec).strip()
    if not spec:
        return column_spec
    if "extracolsep" in spec:
        return "{" + spec + "}"
    return "{@{\\extracolsep{\\fill}}" + spec + "}"


def remove_resizebox_around_first_tabular(
    tex_content: str,
    tighten_tabcolsep: bool = False,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    tabular = find_first_tabular_span(tex_content)
    if not tabular:
        return tex_content, None

    resizebox_span = find_resizebox_wrapping_span(
        tex_content,
        tabular["begin_start"],
        tabular["end_end"],
    )
    if not resizebox_span:
        return tex_content, None

    resizebox_start, resizebox_end = resizebox_span
    inner_tabular = tex_content[tabular["begin_start"]:tabular["end_end"]]
    updated = tex_content[:resizebox_start] + inner_tabular + tex_content[resizebox_end:]

    tabcolsep_adjustment = tighten_tabcolsep_in_block(updated) if tighten_tabcolsep else None
    if tabcolsep_adjustment:
        updated = updated.replace(
            tabcolsep_adjustment["before"],
            tabcolsep_adjustment["after"],
            1,
        )

    return updated, {
        "column_spec_before": tabular["column_spec"],
        "column_spec_after": tabular["column_spec"],
        "removed_resizebox": True,
        "tightened_tabcolsep": bool(tabcolsep_adjustment),
        "tabcolsep_before": (tabcolsep_adjustment or {}).get("before"),
        "tabcolsep_after": (tabcolsep_adjustment or {}).get("after"),
        "preserved_tabular": True,
    }


def tighten_tabcolsep_in_block(block: str) -> Optional[Dict[str, str]]:
    match = re.search(
        r"\\setlength\{\\tabcolsep\}\{([0-9]+(?:\.[0-9]+)?)(mm|pt)\}",
        block,
    )
    if not match:
        return None

    current_value = float(match.group(1))
    unit = match.group(2)
    if unit == "pt":
        if current_value <= 0:
            return None
        target_value = 0.0
    else:
        if current_value <= 2.0:
            return None
        if current_value >= 4.0:
            target_value = 2.0
        elif current_value >= 3.0:
            target_value = 2.5
        else:
            target_value = 2.0

    before = match.group(0)
    after = before.replace(match.group(1), f"{target_value:g}", 1)
    return {"before": before, "after": after}


def read_braced_group(text: str, start: int) -> Optional[Tuple[int, int]]:
    if start >= len(text) or text[start] != "{":
        return None

    depth = 0
    index = start
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return start, index + 1
        index += 1
    return None


def find_resizebox_wrapping_span(
    block: str,
    inner_start: int,
    inner_end: int,
) -> Optional[Tuple[int, int]]:
    resizebox_pos = block.rfind(r"\resizebox", 0, inner_start)
    if resizebox_pos == -1:
        return None

    cursor = resizebox_pos + len(r"\resizebox")
    groups = []
    for _ in range(3):
        while cursor < len(block) and block[cursor].isspace():
            cursor += 1
        group_span = read_braced_group(block, cursor)
        if not group_span:
            return None
        groups.append(group_span)
        cursor = group_span[1]

    content_start, content_end = groups[2]
    prefix = block[content_start + 1 : inner_start]
    suffix = block[inner_end : content_end - 1]
    whitespace_or_comment = r"(?:\s|%[^\n]*(?:\n|$))*"
    if not re.fullmatch(whitespace_or_comment, prefix):
        return None
    if not re.fullmatch(whitespace_or_comment, suffix):
        return None
    return resizebox_pos, content_end


def find_first_tabular_span(tex_content: str) -> Optional[Dict[str, Any]]:
    begin_match = re.search(r"\\begin\{tabular\}", tex_content)
    if not begin_match:
        return None
    column_span = read_braced_group(tex_content, begin_match.end())
    if not column_span:
        return None
    end_match = re.search(r"\\end\{tabular\}", tex_content[column_span[1] :], re.DOTALL)
    if not end_match:
        return None
    return {
        "begin_start": begin_match.start(),
        "begin_end": begin_match.end(),
        "column_start": column_span[0],
        "column_end": column_span[1],
        "column_spec": tex_content[column_span[0] : column_span[1]],
        "end_start": column_span[1] + end_match.start(),
        "end_end": column_span[1] + end_match.end(),
    }


def rewrite_first_tabular_to_tabularx(
    tex_content: str,
    width_spec: str,
    spec_converter: Callable[[str], str],
    tighten_tabcolsep: bool = False,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    tabular = find_first_tabular_span(tex_content)
    if not tabular:
        tabcolsep_adjustment = tighten_tabcolsep_in_block(tex_content) if tighten_tabcolsep else None
        if tabcolsep_adjustment:
            updated = tex_content.replace(
                tabcolsep_adjustment["before"],
                tabcolsep_adjustment["after"],
                1,
            )
            return updated, {
                "column_spec_before": None,
                "column_spec_after": None,
                "removed_resizebox": False,
                "tightened_tabcolsep": True,
                "tabcolsep_before": tabcolsep_adjustment["before"],
                "tabcolsep_after": tabcolsep_adjustment["after"],
            }
        return tex_content, None

    updated = tex_content
    column_spec = tabular["column_spec"]
    new_column_spec = spec_converter(column_spec)
    new_column_spec = shrink_first_fixed_text_column_for_tabularx(new_column_spec)

    resizebox_span = find_resizebox_wrapping_span(
        updated,
        tabular["begin_start"],
        tabular["end_end"],
    )
    if resizebox_span:
        resizebox_start, resizebox_end = resizebox_span
        inner_tabular = updated[tabular["begin_start"] : tabular["end_end"]]
        updated = updated[:resizebox_start] + inner_tabular + updated[resizebox_end:]
        tabular = find_first_tabular_span(updated)
        if not tabular:
            return tex_content, None
        column_spec = tabular["column_spec"]
        new_column_spec = spec_converter(column_spec)
        new_column_spec = shrink_first_fixed_text_column_for_tabularx(new_column_spec)

    old_full = f"\\begin{{tabular}}{column_spec}"
    new_full = f"\\begin{{tabularx}}{{{width_spec}}}{new_column_spec}"
    updated = updated.replace(old_full, new_full, 1)
    updated = updated.replace(r"\end{tabular}", r"\end{tabularx}", 1)

    tabcolsep_adjustment = tighten_tabcolsep_in_block(updated) if tighten_tabcolsep else None
    if tabcolsep_adjustment:
        updated = updated.replace(
            tabcolsep_adjustment["before"],
            tabcolsep_adjustment["after"],
            1,
        )

    return updated, {
        "column_spec_before": column_spec,
        "column_spec_after": new_column_spec,
        "removed_resizebox": resizebox_span is not None,
        "tightened_tabcolsep": bool(tabcolsep_adjustment),
        "tabcolsep_before": (tabcolsep_adjustment or {}).get("before"),
        "tabcolsep_after": (tabcolsep_adjustment or {}).get("after"),
    }


def rewrite_first_tabular_to_tabular_star(
    tex_content: str,
    width_spec: str,
    tighten_tabcolsep: bool = False,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    tabular = find_first_tabular_span(tex_content)
    if not tabular:
        return tex_content, None

    updated = tex_content
    column_spec = tabular["column_spec"]
    stretched_spec = convert_plain_alignment_to_stretched_spec(column_spec)

    resizebox_span = find_resizebox_wrapping_span(
        updated,
        tabular["begin_start"],
        tabular["end_end"],
    )
    if resizebox_span:
        resizebox_start, resizebox_end = resizebox_span
        inner_tabular = updated[tabular["begin_start"] : tabular["end_end"]]
        updated = updated[:resizebox_start] + inner_tabular + updated[resizebox_end:]
        tabular = find_first_tabular_span(updated)
        if not tabular:
            return tex_content, None
        column_spec = tabular["column_spec"]
        stretched_spec = convert_plain_alignment_to_stretched_spec(column_spec)

    old_full = f"\\begin{{tabular}}{column_spec}"
    new_full = f"\\begin{{tabular*}}{{{width_spec}}}{stretched_spec}"
    updated = updated.replace(old_full, new_full, 1)
    updated = updated.replace(r"\end{tabular}", r"\end{tabular*}", 1)

    tabcolsep_adjustment = tighten_tabcolsep_in_block(updated) if tighten_tabcolsep else None
    if tabcolsep_adjustment:
        updated = updated.replace(
            tabcolsep_adjustment["before"],
            tabcolsep_adjustment["after"],
            1,
        )

    return updated, {
        "column_spec_before": column_spec,
        "column_spec_after": stretched_spec,
        "removed_resizebox": resizebox_span is not None,
        "tightened_tabcolsep": bool(tabcolsep_adjustment),
        "tabcolsep_before": (tabcolsep_adjustment or {}).get("before"),
        "tabcolsep_after": (tabcolsep_adjustment or {}).get("after"),
        "stretched_tabular": True,
    }
