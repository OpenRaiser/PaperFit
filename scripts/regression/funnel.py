#!/usr/bin/env python3
"""回归评测的 case 漏斗（assigned → injected → b0_rendered → candidate_rendered → scored）。

设计原则（见 docs/REGRESSION_DESIGN.md）：
- 幸存者偏差必须显式化：每个阶段的分母都要报告，失败 case 不许静默消失；
- ITT（按计划分配）与 per-protocol（按可评估）两套估计并存；
- 按扰动类型分层报告，不只给总数。
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import json

STAGES = ("assigned", "injected", "b0_rendered", "candidate_rendered", "scored")


def load_case_records(path: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _stage_flags(record: Dict[str, Any]) -> Dict[str, bool]:
    injection_status = str(record.get("injection_status") or "")
    b0_status = str(record.get("b0_status") or "")
    candidate_status = str(record.get("candidate_status") or "")
    vlm = record.get("vlm") or {}
    # 漏斗必须单调：前一阶段失败的 case 不可能在后一阶段被计数
    injected = injection_status == "success"
    b0_rendered = injected and b0_status == "layout_evaluable"
    candidate_rendered = b0_rendered and candidate_status == "layout_evaluable"
    scored = candidate_rendered and bool(vlm.get("effective", True))
    return {
        "assigned": bool(record.get("assigned", True)),
        "injected": injected,
        "b0_rendered": b0_rendered,
        "candidate_rendered": candidate_rendered,
        "scored": scored,
    }


def build_funnel(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    records = list(records)
    overall = {stage: 0 for stage in STAGES}
    by_perturbation: Dict[str, Dict[str, int]] = defaultdict(lambda: {stage: 0 for stage in STAGES})
    failure_reasons: Dict[str, int] = defaultdict(int)

    for record in records:
        flags = _stage_flags(record)
        for stage in STAGES:
            if flags[stage]:
                overall[stage] += 1
        perturbations = record.get("perturbation_types") or ["unknown"]
        for perturbation in perturbations:
            for stage in STAGES:
                if flags[stage]:
                    by_perturbation[perturbation][stage] += 1
        injection_status = str(record.get("injection_status") or "")
        b0_status = str(record.get("b0_status") or "")
        candidate_status = str(record.get("candidate_status") or "")
        if not flags["injected"]:
            failure_reasons[f"injection:{injection_status or 'missing'}"] += 1
        elif not flags["b0_rendered"]:
            failure_reasons[f"b0:{b0_status or 'missing'}"] += 1
        elif not flags["candidate_rendered"]:
            failure_reasons[f"candidate:{candidate_status or 'missing'}"] += 1
        elif not flags["scored"]:
            failure_reasons["vlm:invalid_response"] += 1

    return {
        "stages": STAGES,
        "overall": overall,
        "by_perturbation": dict(by_perturbation),
        "failure_reasons": dict(failure_reasons),
        "total_assigned": overall["assigned"],
        "quality_denominator": overall["scored"],
    }
