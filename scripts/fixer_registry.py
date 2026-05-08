#!/usr/bin/env python3
"""
Canonical fixer routing registry for PaperFit.

This module defines the execution truth source for code-surgeon repairs:
- `scripts/repair_plan_executor.py` is the only script-level execution entry
- `scripts/*_fixers.py` are canonical file-level execution adapters
- `skills/latex_fixers/*` are shared rewrite libraries, not end-to-end entrypoints
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from float_fixers import fix_float_defects
from overflow_fixers import fix_overflow_defects
from space_util_fixers import fix_space_util_defects


REGISTRY_VERSION = "1.0"
CANONICAL_EXECUTION_ENTRY = "scripts/repair_plan_executor.py"


@dataclass(frozen=True)
class FixerRoute:
    family: str
    adapter_module: str
    shared_library_module: Optional[str]
    callable_name: str
    status: str
    notes: str


_ROUTES: Dict[str, FixerRoute] = {
    "A": FixerRoute(
        family="A",
        adapter_module="scripts/space_util_fixers.py",
        shared_library_module="skills/latex_fixers/space_util_fixers.py",
        callable_name="fix_space_util_defects",
        status="canonical_execution_adapter",
        notes="Script adapter owns file IO, report assembly, and end-to-end execution semantics.",
    ),
    "B": FixerRoute(
        family="B",
        adapter_module="scripts/float_fixers.py",
        shared_library_module="skills/latex_fixers/float_fixers.py",
        callable_name="fix_float_defects",
        status="canonical_execution_adapter",
        notes="Script adapter owns file IO, hard content gate, and end-to-end execution semantics.",
    ),
    "D": FixerRoute(
        family="D",
        adapter_module="scripts/overflow_fixers.py",
        shared_library_module="skills/latex_fixers/overflow_fixers.py",
        callable_name="fix_overflow_defects",
        status="canonical_execution_adapter",
        notes="Script adapter owns file IO and structured fix report generation.",
    ),
    "GLOBAL_PARAGRAPH_SPACING": FixerRoute(
        family="GLOBAL_PARAGRAPH_SPACING",
        adapter_module="scripts/repair_plan_executor.py",
        shared_library_module=None,
        callable_name="_execute_global_actions",
        status="inline_executor_action",
        notes="Temporary executor-local action until A/D routing is fully absorbed into candidate execution.",
    ),
}


def get_canonical_route(family: str) -> FixerRoute:
    return _ROUTES[family]


def canonical_execution_manifest() -> Dict[str, Any]:
    return {
        "registry_version": REGISTRY_VERSION,
        "canonical_execution_entry": CANONICAL_EXECUTION_ENTRY,
        "families": {
            family: asdict(route)
            for family, route in _ROUTES.items()
        },
    }


def supported_repair_families() -> List[str]:
    return [family for family in _ROUTES.keys() if family != "GLOBAL_PARAGRAPH_SPACING"]


def execute_float_candidates(
    main_tex: str,
    defects: List[Dict[str, Any]],
    column_type: Optional[str] = None,
) -> Dict[str, Any]:
    template_type = "double_column" if str(column_type or "").lower() == "double" else "single_column"
    report = fix_float_defects(
        tex_file_path=main_tex,
        defects=defects,
        template_type=template_type,
    )
    result = report.to_dict()
    result["canonical_route"] = asdict(get_canonical_route("B"))
    return result


def execute_overflow_candidates(
    main_tex: str,
    defects: List[Dict[str, Any]],
) -> Dict[str, Any]:
    report = fix_overflow_defects(
        tex_file_path=main_tex,
        defects=defects,
    )
    result = report.to_dict()
    result["canonical_route"] = asdict(get_canonical_route("D"))
    return result


def execute_space_util_candidates(
    main_tex: str,
    defects: List[Dict[str, Any]],
    target_pages: Optional[int] = None,
    column_type: Optional[str] = None,
) -> Dict[str, Any]:
    template_type = "double_column" if str(column_type or "").lower() == "double" else "single_column"
    report = fix_space_util_defects(
        tex_file_path=main_tex,
        defects=defects,
        target_pages=target_pages,
        template_type=template_type,
    )
    result = report.to_dict()
    result["canonical_route"] = asdict(get_canonical_route("A"))
    return result
