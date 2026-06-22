#!/usr/bin/env python3
"""Small explicit state machine for the PaperFit runtime harness."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Tuple


class IllegalTransitionError(ValueError):
    pass


@dataclass(frozen=True)
class RuntimeTransition:
    source: str
    event: str
    target: str


class RuntimeStateMachine:
    """Enforces legal high-level runtime phase transitions."""

    def __init__(self, transitions: Iterable[RuntimeTransition]):
        self._transitions: Dict[Tuple[str, str], str] = {
            (item.source, item.event): item.target for item in transitions
        }

    def transition(self, state: str, event: str) -> str:
        target = self._transitions.get((state, event))
        if target is None:
            raise IllegalTransitionError(f"Illegal runtime transition: {state} --{event}--> ?")
        return target


VISUAL_ONLY_STATE_MACHINE = RuntimeStateMachine(
    [
        RuntimeTransition("INIT", "task_validated", "READY"),
        RuntimeTransition("READY", "start_observe", "OBSERVING"),
        RuntimeTransition("OBSERVING", "artifacts_observed", "DIAGNOSING"),
        RuntimeTransition("DIAGNOSING", "diagnosis_complete", "VERIFYING"),
        RuntimeTransition("VERIFYING", "gatekeeper_done", "DONE"),
        RuntimeTransition("VERIFYING", "gatekeeper_continue", "CONTINUE"),
        RuntimeTransition("VERIFYING", "gatekeeper_blocked", "BLOCKED"),
    ]
)


SOURCE_CHANGING_STATE_MACHINE = RuntimeStateMachine(
    [
        RuntimeTransition("INIT", "task_validated", "READY"),
        RuntimeTransition("READY", "start_observe", "OBSERVING"),
        RuntimeTransition("OBSERVING", "compile_blocked", "BLOCKED"),
        RuntimeTransition("OBSERVING", "artifacts_observed", "DIAGNOSING"),
        RuntimeTransition("DIAGNOSING", "diagnosis_complete", "PLANNING"),
        RuntimeTransition("PLANNING", "plan_ready", "REPAIRING"),
        RuntimeTransition("PLANNING", "no_repair_candidates", "VERIFYING"),
        RuntimeTransition("REPAIRING", "repair_skipped", "VERIFYING"),
        RuntimeTransition("REPAIRING", "repair_applied", "VERIFYING"),
        RuntimeTransition("VERIFYING", "start_post_observe", "OBSERVING"),
        RuntimeTransition("DIAGNOSING", "post_repair_diagnosis_complete", "VERIFYING"),
        RuntimeTransition("VERIFYING", "gatekeeper_done", "DONE"),
        RuntimeTransition("VERIFYING", "gatekeeper_continue", "CONTINUE"),
        RuntimeTransition("VERIFYING", "gatekeeper_blocked", "BLOCKED"),
    ]
)
