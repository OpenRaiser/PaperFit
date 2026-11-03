#!/usr/bin/env python3
"""Apply bounded Category C consistency repairs.

C1 removes a \\resizebox wrapped around the first tabular so table text
keeps a normal size. C3 inserts a uniform captionsetup. C4 normalizes
caption spacing by dropping manual \\vspace gaps inserted before captions
(falling back to captionsetup insertion). C2 image-resolution issues stay
diagnostic only.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from transactional_patch import atomic_write_text

try:
    from skills.latex_fixers.shared_table_helpers import remove_resizebox_around_first_tabular
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from skills.latex_fixers.shared_table_helpers import remove_resizebox_around_first_tabular


_CAPTION_SETUP = r"\captionsetup{font=small,labelfont=bf,skip=6pt}"

# C4: 手工加在浮动体内容与 \caption 之间的 \vspace 是 caption 间距
# 方差过大的常见来源；统一交给 captionsetup 的 skip 管理。
_VSPACE_BEFORE_CAPTION_RE = re.compile(r"(\\vspace\{[^}]*\})\s*(\\caption)")

# C 类风格策略：支持的字号档位（style_policy.table_font 取这些名字）
_FONT_SIZE_COMMANDS = (
    r"\tiny",
    r"\scriptsize",
    r"\footnotesize",
    r"\small",
    r"\normalsize",
    r"\large",
    r"\Large",
    r"\LARGE",
    r"\huge",
    r"\Huge",
)
_FONT_COMMAND_BY_NAME = {cmd.lstrip("\\").lower(): cmd for cmd in _FONT_SIZE_COMMANDS}


def _normalize_table_font_consistency(tex: str, policy_command: str) -> tuple[str, int]:
    """把表格块内显式字号统一到策略字号。

    只动已经显式声明字号的表格（未声明的继承模板默认，不碰），
    修改是纯排版 token 替换，不影响内容完整性门禁。
    """
    changed = 0

    def _reblock(match: "re.Match[str]") -> str:
        nonlocal changed
        block = match.group(0)
        present = [cmd for cmd in _FONT_SIZE_COMMANDS if cmd in block]
        if not present or set(present) == {policy_command}:
            return block
        updated_block = block
        for cmd in present:
            if cmd != policy_command:
                updated_block = updated_block.replace(cmd, policy_command)
        if updated_block != block:
            changed += 1
        return updated_block

    updated = re.sub(
        r"\\begin\{table\*?\}.*?\\end\{table\*?\}",
        _reblock,
        tex,
        flags=re.DOTALL,
    )
    return updated, changed


def _insert_caption_setup(tex: str) -> str:
    if r"\captionsetup" in tex or r"\usepackage{caption}" not in tex:
        return tex
    marker = r"\usepackage{caption}"
    index = tex.find(marker)
    line_end = tex.find("\n", index)
    if line_end < 0:
        line_end = len(tex)
    return tex[:line_end] + "\n" + _CAPTION_SETUP + tex[line_end:]


def _normalize_caption_spacing(tex: str) -> tuple[str, int]:
    removed = 0

    def _drop(match: "re.Match[str]") -> str:
        nonlocal removed
        removed += 1
        return match.group(2)

    return _VSPACE_BEFORE_CAPTION_RE.sub(_drop, tex), removed


def fix_consistency_defects(
    main_tex: str,
    defects: List[Dict[str, Any]],
) -> Dict[str, Any]:
    tex_path = Path(main_tex)
    original = tex_path.read_text(encoding="utf-8") if tex_path.is_file() else ""
    updated = original
    changes: List[Dict[str, Any]] = []
    unresolved: List[Dict[str, Any]] = []

    wants_c1 = any(str(item.get("defect_id") or item.get("defect_family") or "").startswith("C1") for item in defects)
    wants_c3 = any(str(item.get("defect_id") or item.get("defect_family") or "").startswith("C3") for item in defects)
    wants_c4 = any(str(item.get("defect_id") or item.get("defect_family") or "").startswith("C4") for item in defects)
    if wants_c1 and r"\resizebox" in updated and r"\begin{tabular}" in updated:
        rewritten, info = remove_resizebox_around_first_tabular(updated)
        if info and rewritten != updated:
            updated = rewritten
            changes.append(
                {
                    "success": True,
                    "defect_id": "C1",
                    "action": "remove_resizebox_around_tabular",
                    "before": info.get("column_spec_before") or "resizebox",
                    "after": "tabular",
                }
            )
        elif wants_c1:
            unresolved.append({"defect_id": "C1", "reason": "resizebox_tabular_not_found"})

    if wants_c3:
        rewritten = _insert_caption_setup(updated)
        if rewritten != updated:
            updated = rewritten
            changes.append(
                {
                    "success": True,
                    "defect_id": "C3",
                    "action": "normalize_caption_setup",
                    "before": "usepackage{caption}",
                    "after": "captionsetup",
                }
            )
        else:
            unresolved.append({"defect_id": "C3", "reason": "caption_package_or_setup_unchanged"})

    if wants_c4:
        rewritten, removed = _normalize_caption_spacing(updated)
        if removed:
            updated = rewritten
            changes.append(
                {
                    "success": True,
                    "defect_id": "C4",
                    "action": "normalize_caption_spacing",
                    "before": "\\vspace{...}\\caption",
                    "after": "\\caption (spacing governed by captionsetup skip)",
                    "applied_edits": removed,
                }
            )
        elif r"\captionsetup" not in updated:
            fallback = _insert_caption_setup(updated)
            if fallback != updated:
                updated = fallback
                changes.append(
                    {
                        "success": True,
                        "defect_id": "C4",
                        "action": "normalize_caption_setup",
                        "before": "usepackage{caption}",
                        "after": "captionsetup",
                    }
                )
            else:
                unresolved.append(
                    {
                        "defect_id": "C4",
                        "reason": "caption_spacing_not_normalizable",
                    }
                )

    # C 类风格策略执行：候选携带 style_policy.table_font 时，
    # 把表格内显式字号统一到策略档位。
    table_font_policy = None
    for item in defects:
        policy = item.get("style_policy") if isinstance(item.get("style_policy"), dict) else {}
        requested = str(policy.get("table_font") or "").lstrip("\\").lower()
        if requested:
            table_font_policy = _FONT_COMMAND_BY_NAME.get(requested)
            if table_font_policy is None:
                unresolved.append(
                    {
                        "defect_id": "C_style_policy",
                        "reason": f"unsupported table_font policy: {requested}",
                    }
                )
            break
    if table_font_policy is not None:
        rewritten, normalized = _normalize_table_font_consistency(updated, table_font_policy)
        if normalized:
            updated = rewritten
            changes.append(
                {
                    "success": True,
                    "defect_id": "C_style_policy",
                    "action": "normalize_table_font_consistency",
                    "before": "mixed explicit font sizes in table blocks",
                    "after": table_font_policy,
                    "applied_edits": normalized,
                }
            )
        else:
            unresolved.append(
                {
                    "defect_id": "C_style_policy",
                    "reason": "no_table_blocks_with_explicit_font_sizes",
                }
            )

    for item in defects:
        family = str(item.get("defect_id") or item.get("defect_family") or "")
        if family.startswith("C2"):
            unresolved.append({"defect_id": "C2", "reason": "diagnosis_only"})

    if updated != original and tex_path.is_file():
        atomic_write_text(tex_path, updated)

    status = "success" if changes and not unresolved else ("partial" if changes else "noop")
    return {
        "schema_version": "1.0",
        "skill": "consistency-polisher",
        "status": status,
        "changes": changes,
        "unresolved": unresolved,
    }
