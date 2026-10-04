#!/usr/bin/env python3
"""Apply bounded Category C consistency repairs.

C1 removes a \\resizebox wrapped around the first tabular so table text
keeps a normal size. C2 image-resolution issues stay diagnostic only.
"""

from __future__ import annotations

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


def _insert_caption_setup(tex: str) -> str:
    if r"\captionsetup" in tex or r"\usepackage{caption}" not in tex:
        return tex
    marker = r"\usepackage{caption}"
    index = tex.find(marker)
    line_end = tex.find("\n", index)
    if line_end < 0:
        line_end = len(tex)
    return tex[:line_end] + "\n" + _CAPTION_SETUP + tex[line_end:]


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
