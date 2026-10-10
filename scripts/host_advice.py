"""Shared authority boundary for recommendations delivered to an editing host."""
from __future__ import annotations

from typing import Any
import json
import sys


def print_host_json(payload: dict[str, Any]) -> None:
    """Machine-readable output must not depend on a Windows console code page."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def host_guidance() -> dict[str, Any]:
    """Describe advice, without granting edits or clearing preservation checks."""
    return {
        "role": "advisory",
        "verification_question": "请你看看这是否是对的：核对当前页图和源码，再决定是否采纳。",
        "host_choices": ["accept", "adapt", "reject"],
        "recommendation_is_authorization": False,
        "machine_status_is_visual_verification": False,
        "routing_note": "Executable describes a supported tool route, not a verified defect or the best repair.",
        "preservation_note": "Host judgment does not clear confirmed content loss, hiding, stale builds or template violations.",
    }


def recommendation_review(*, evidence_status: str = "unverified",
                          target_status: str = "requires_host_review") -> dict[str, Any]:
    """A machine finding cannot provide its own independent host confirmation."""
    return {
        "question": "请你看看这是否是对的",
        "verification": "pending_host",
        "evidence_status": evidence_status,
        "target_status": target_status,
    }


def joint_layout_plan(objective: dict[str, Any]) -> dict[str, Any]:
    """Make the global objective available before local repairs are complete."""
    return {
        "available_before_defects_stable": True,
        "objective": objective,
        "host_choices": "Choose or combine interventions from current evidence; categories are not a fixed sequence.",
        "page_growth": "If a readability repair adds pages, identify a compensating layout trial and retain the prior candidate.",
        "failure_recovery": "If patch preparation fails or bytes do not change, restore/recheck the verified candidate; do not treat tool bookkeeping as a new layout result.",
        "verification": "A plan is not acceptance. Compare actual compiled pages, readability and preserved content before keeping it.",
    }
