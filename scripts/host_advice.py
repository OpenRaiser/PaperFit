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
    """Keep tool routing separate from the editing host's user-granted scope."""
    return {
        "role": "advisory",
        "verification_question": "请你看看这是否是对的：核对当前页图和源码，再决定是否采纳。",
        "host_choices": ["accept", "adapt", "reject"],
        "recommendation_is_authorization": False,
        "machine_status_is_visual_verification": False,
        "routing_note": "Executable describes a supported tool route, not a verified defect or the best repair.",
        "preservation_note": "Host judgment does not clear confirmed content loss, hiding, stale builds or template violations.",
        "editing_authority": {
            "authorization_source": "user_request_and_current_session",
            "native_editing_supported": True,
            "tool_routes_limit_host_edits": False,
            "strategy_menu_is_exhaustive": False,
            "ordinary_layout_reapproval_required": False,
            "per_finding_acknowledgement_required": False,
            "within_authorized_scope": [
                "edit relevant source, includes and project style files with native host tools",
                "combine related changes into a coherent candidate",
                "use independently anchored repairs absent from the tool menu",
                "accept, adapt or reject advice using current source and page evidence",
                "retain and investigate reviewable candidates rather than waiting for a person",
            ],
            "scope_note": "The user's request decides edit scope. Analysis-only requests do not authorize mutation; a layout request does not authorize changing scientific claims or ignoring explicit template requirements.",
        },
        "tool_status_scope": {
            "executable": "Optional automation route only; not proof of a defect or the preferred edit.",
            "human_review": "Legacy name for editing-host investigation; not an automatic request for a new human approval.",
            "approval_required": "Optional batch authorization or supported-scope limit; already authorized native host repairs remain available.",
            "blocked": "The reported route or checked condition is blocked. Investigate false positives; do not discard confirmed content damage or use stale evidence to certify success.",
        },
        "verification_scope": "Review coherent candidates, not a mandatory acknowledgement for every finding. Recompile changed candidates, inspect affected pages, and inspect all final physical pages before acceptance.",
    }


def recommendation_review(*, evidence_status: str = "unverified",
                          target_status: str = "requires_host_review") -> dict[str, Any]:
    """A machine finding cannot provide its own independent host confirmation."""
    return {
        "question": "请你看看这是否是对的",
        "verification": "pending_host",
        "evidence_status": evidence_status,
        "target_status": target_status,
        "reviewer": "editing_host",
        "acknowledgement_scope": "coherent_candidate",
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
