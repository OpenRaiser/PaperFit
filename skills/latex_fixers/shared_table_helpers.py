"""
Shared helpers for table-width normalization.

These helpers are intentionally reused by both `scripts/float_fixers.py`
and `skills/latex_fixers/float_fixers.py` so that resizebox removal,
tabular->tabularx rewriting, and tabcolsep tightening share one code path.
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable, Dict, List, Optional, Tuple


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


def mask_latex_comments(text: str) -> str:
    """Keep source offsets while excluding comments (respect escaped percent)."""
    chars = list(text)
    cursor = 0
    while cursor < len(text):
        if text[cursor] == "\\":
            cursor += 2
            continue
        if text[cursor] == "%":
            end = text.find("\n", cursor)
            end = len(text) if end < 0 else end
            chars[cursor:end] = " " * (end - cursor)
            cursor = end
        cursor += 1
    return "".join(chars)


def convert_last_text_column_to_x(column_spec: str) -> str:
    spec = _strip_outer_column_spec(column_spec)
    parsed = _parse_column_spec(spec)
    if parsed is None:
        return column_spec
    spec, columns = parsed
    text_columns = [(i, c) for i, c in columns if c in "lrc"]
    if not text_columns:
        return "{" + spec + "}"
    last_text_idx, _ = text_columns[-1]
    new_spec = spec[:last_text_idx] + "X" + spec[last_text_idx + 1 :]
    return "{" + new_spec + "}"


def convert_preserve_first_column_to_x(column_spec: str) -> str:
    spec = _strip_outer_column_spec(column_spec)
    parsed = _parse_column_spec(spec)
    if parsed is None:
        return column_spec
    spec, columns = parsed
    replacements = {position for position, kind in columns[1:] if kind in "lrc"}
    return "{" + "".join("X" if i in replacements else char for i, char in enumerate(spec)) + "}"


def _parse_column_spec(spec: str, depth: int = 0) -> Optional[Tuple[str, List[Tuple[int, str]]]]:
    """Parse standard array columns while treating decorators/widths as opaque.

    Repetition is expanded only after its complete nested specification parses.
    Unknown custom column types fail closed instead of rewriting macro letters.
    """
    if depth > 16:
        return None
    parts: List[str] = []
    columns: List[Tuple[int, str]] = []
    cursor = 0
    length = 0
    while cursor < len(spec):
        char = spec[cursor]
        if char == "%":
            end = spec.find("\n", cursor)
            end = len(spec) if end < 0 else end + 1
            item = spec[cursor:end]
        elif char.isspace() or char == "|":
            end = cursor + 1
            item = char
        elif char in "lrcX":
            columns.append((length, char))
            end = cursor + 1
            item = char
        elif char in "pmb@!><":
            group_start = cursor + 1
            while group_start < len(spec) and spec[group_start].isspace():
                group_start += 1
            group = read_braced_group(spec, group_start)
            if group is None:
                return None
            end = group[1]
            item = spec[cursor:end]
            if char in "pmb":
                columns.append((length, char))
        elif char == "*":
            count_start = cursor + 1
            while count_start < len(spec) and spec[count_start].isspace():
                count_start += 1
            count_group = read_braced_group(spec, count_start)
            if count_group is None:
                return None
            count_text = spec[count_group[0] + 1:count_group[1] - 1].strip()
            if not count_text.isdigit() or not 1 <= int(count_text) <= 64:
                return None
            repeat_start = count_group[1]
            while repeat_start < len(spec) and spec[repeat_start].isspace():
                repeat_start += 1
            repeat_group = read_braced_group(spec, repeat_start)
            if repeat_group is None:
                return None
            nested = _parse_column_spec(spec[repeat_group[0] + 1:repeat_group[1] - 1], depth + 1)
            if nested is None:
                return None
            nested_spec, nested_columns = nested
            count = int(count_text)
            if len(columns) + count * len(nested_columns) > 256:
                return None
            for repeat in range(count):
                columns.extend((length + repeat * len(nested_spec) + pos, kind) for pos, kind in nested_columns)
            item = nested_spec * count
            end = repeat_group[1]
        else:
            return None
        parts.append(item)
        length += len(item)
        cursor = end
    return "".join(parts), columns


def count_table_columns(column_spec: str) -> int:
    parsed = _parse_column_spec(_strip_outer_column_spec(column_spec))
    return len(parsed[1]) if parsed is not None else 0


def tighten_tabcolsep_for_overflow(
    block: str, overflow_pt: float, initial_padding_pt: Optional[float] = 6.0,
) -> Optional[Dict[str, Any]]:
    """Spend measured excess width on padding, retaining at least 3pt per side.

    This keeps numeric alignment and the author's font size. Only a standard,
    unscaled tabular with a known local/default padding can be adjusted safely.
    """
    try:
        excess = float(overflow_pt)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(excess) or excess <= 0:
        return None
    tabular = find_first_tabular_span(block)
    if tabular is None or find_resizebox_wrapping_span(block, tabular["begin_start"], tabular["end_end"]):
        return None
    parsed = _parse_column_spec(_strip_outer_column_spec(tabular["column_spec"]))
    if parsed is None or len(parsed[1]) < 2:
        return None
    # Explicit inter-column inserts replace tabcolsep, so 2*n is no longer valid.
    if any(char in parsed[0] for char in "@!"):
        return None
    before_tabular = mask_latex_comments(block[:tabular["begin_start"]])
    matches = list(re.finditer(r"\\setlength\s*\{\s*\\tabcolsep\s*\}\s*\{\s*([0-9]+(?:\.[0-9]+)?)\s*(pt|mm)\s*\}", before_tabular))
    if r"\tabcolsep" in before_tabular and not matches:
        return None
    current_pt = initial_padding_pt
    match = matches[-1] if matches else None
    if match:
        current_pt = float(match.group(1)) * (72.27 / 25.4 if match.group(2) == "mm" else 1.0)
    if current_pt is None or not math.isfinite(current_pt) or current_pt <= 3.0:
        return None
    column_count = len(parsed[1])
    target_pt = max(3.0, current_pt - (excess + 0.2) / (2 * column_count))
    # Round downward, leaving a small allowance for TeX's dimension rounding.
    target_pt = math.floor(target_pt * 1000) / 1000
    command = f"\\setlength{{\\tabcolsep}}{{{target_pt:g}pt}}"
    if match:
        updated = block[:match.start()] + command + block[match.end():]
        before = block[match.start():match.end()]
    else:
        at = tabular["begin_start"]
        updated = block[:at] + command + "\n" + block[at:]
        before = f"inherited tabcolsep={current_pt:g}pt"
    return {"updated": updated, "before": before, "after": command, "columns": column_count,
            "width_saved_pt": 2 * column_count * (current_pt - target_pt)}


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
        if char == "%":
            newline = text.find("\n", index)
            if newline < 0:
                return None
            index = newline + 1
            continue
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
    resizebox_pos = mask_latex_comments(block).rfind(r"\resizebox", 0, inner_start)
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
    # Mask comments without moving offsets; a commented example is not a target.
    active = mask_latex_comments(tex_content)
    begin_match = re.search(r"\\begin\{tabular\}", active)
    if not begin_match:
        return None
    cursor = begin_match.end()
    optional_position = ""
    while cursor < len(active) and active[cursor].isspace():
        cursor += 1
    if cursor < len(active) and active[cursor] == "[":
        optional_end = active.find("]", cursor + 1)
        if optional_end < 0:
            return None
        optional_position = tex_content[cursor:optional_end + 1]
        cursor = optional_end + 1
        while cursor < len(active) and active[cursor].isspace():
            cursor += 1
    column_span = read_braced_group(tex_content, cursor)
    if not column_span:
        return None
    depth = 1
    end_match = None
    for match in re.finditer(r"\\(begin|end)\{tabular\}", active[column_span[1]:]):
        depth += 1 if match.group(1) == "begin" else -1
        if depth == 0:
            end_match = match
            break
    if end_match is None:
        return None
    return {
        "begin_start": begin_match.start(),
        "begin_end": begin_match.end(),
        "column_start": column_span[0],
        "column_end": column_span[1],
        "column_spec": tex_content[column_span[0] : column_span[1]],
        "optional_position": optional_position,
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

    new_full = f"\\begin{{tabularx}}{{{width_spec}}}{tabular.get('optional_position', '')}{new_column_spec}"
    updated = (updated[:tabular["begin_start"]] + new_full
               + updated[tabular["column_end"]:tabular["end_start"]]
               + r"\end{tabularx}" + updated[tabular["end_end"]:])

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

    new_full = f"\\begin{{tabular*}}{{{width_spec}}}{tabular.get('optional_position', '')}{stretched_spec}"
    updated = (updated[:tabular["begin_start"]] + new_full
               + updated[tabular["column_end"]:tabular["end_start"]]
               + r"\end{tabular*}" + updated[tabular["end_end"]:])

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
