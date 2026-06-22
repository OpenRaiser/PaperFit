#!/usr/bin/env python3
"""Append-only runtime event log helpers for PaperFit."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


class RuntimeEventWriter:
    def __init__(self, run_id: str, event_log: str):
        self.run_id = run_id
        self.path = Path(event_log)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(
        self,
        event_type: str,
        *,
        phase: Optional[str] = None,
        state: Optional[str] = None,
        message: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        event: Dict[str, Any] = {
            "schema_version": "1.0",
            "run_id": self.run_id,
            "timestamp": datetime.now().isoformat(),
            "type": event_type,
        }
        if phase is not None:
            event["phase"] = phase
        if state is not None:
            event["state"] = state
        if message is not None:
            event["message"] = message
        if payload:
            event["payload"] = payload

        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event
