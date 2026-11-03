#!/usr/bin/env python3
"回归报告生成：decision + funnel + 分层 → regression_report.json / .md。"

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from .decision import decide_regression
    from .funnel import build_funnel
except ImportError:  # flat import when scripts/regression itself is on sys.path
    from decision import decide_regression
    from funnel import build_funnel

REPORT_SCHEMA_VERSION = "1.0"

SAFETY_METRIC_LABELS = {
    "compile_success": "编译成功率",
    "render_success": "渲染成功率",
    "content_integrity_ok": "内容完整性",
    "page_budget_hit": "页数命中",
}


def build_regression_report(
    *,
    reference_version: str,
    candidate_version: str,
    safety_v1: Dict[str, float],
    safety_v2: Dict[str, float],
    case_records: List[Dict[str, Any]],
    paired_scores: List[Dict[str, Any]],
    absolute_vs_b0: Optional[Dict[str, Any]] = None,
    minimum_detectable_delta: float = 0.10,
    manifest_compatible: bool = True,
    safety_paired: Optional[Dict[str, Any]] = None,
    vlm_sample_accounting: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    funnel = build_funnel(case_records)
    decision = decide_regression(
        safety_v1=safety_v1,
        safety_v2=safety_v2,
        paired_scores=paired_scores,
        minimum_detectable_delta=minimum_detectable_delta,
        manifest_compatible=manifest_compatible,
        safety_paired=safety_paired,
    )

    # win/tie/loss/uncertain：来自双序对账后的 revision_pair 结果
    win_counts = {"candidate": 0, "reference": 0, "tie": 0, "uncertain": 0}
    for record in case_records:
        verdict = str(((record.get("vlm") or {}).get("reconciled")) or "")
        if verdict in win_counts:
            win_counts[verdict] += 1

    stratified = _stratify(case_records)

    report: Dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "comparison": {"reference": reference_version, "candidate": candidate_version},
        "decision": decision,
        "safety": {
            "v1": safety_v1,
            "v2": safety_v2,
            "paired": safety_paired,
            "note": "v1/v2 是各侧条件比例（分母不同，不可直接对比）；配对结论只看 paired",
            "regressions": [
                f"{SAFETY_METRIC_LABELS.get(key, key)}: {safety_v1.get(key)} -> {safety_v2.get(key)}"
                for key in safety_v1
                if not str(key).startswith("_")
                and isinstance(safety_v1.get(key), (int, float))
                and isinstance(safety_v2.get(key), (int, float))
                and safety_v2.get(key) < safety_v1.get(key)
            ],
        },
        "paired_visual": {
            "win_counts": win_counts,
            "sample_accounting": vlm_sample_accounting or {
                "vlm_responses": len(case_records),
                "dual_order_consistent_paired": len(paired_scores),
                "uncertain_excluded": None,
            },
            "paired_delta_mean": decision.get("paired_delta_mean"),
            "paired_delta_ci95": decision.get("paired_delta_ci95"),
        },
        "absolute_vs_b0": absolute_vs_b0,
        "stratified": stratified,
        "coverage": {
            "stages": funnel["overall"],
            "failure_reasons": funnel["failure_reasons"],
            "by_perturbation": funnel["by_perturbation"],
            "quality_denominator": funnel["quality_denominator"],
            "total_assigned": funnel["total_assigned"],
        },
        "conclusion_eligibility": {
            "eligible_for_verdict": decision.get("decision") not in {"INVALID_RUN"},
            "note": (
                "INCONCLUSIVE 表示当前样本量不足以区分改进与无变化，不得解读为改进"
                if decision.get("decision") == "INCONCLUSIVE"
                else None
            ),
        },
    }
    return report


def _stratify(case_records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Dict[str, int]]]:
    """按 扰动类型/栏型/难度/环境态 四个维度的 win-tie-loss-uncertain 计数。"""
    dimensions = {
        "by_perturbation": lambda r: [str(p) for p in (r.get("perturbation_types") or ["unknown"])],
        "by_column_type": lambda r: [str(r.get("column_type") or "unknown")],
        "by_profile": lambda r: [str(r.get("profile") or "unknown")],
        "by_environment": lambda r: [str(r.get("environment_state") or "unknown")],
    }
    result: Dict[str, Dict[str, Dict[str, int]]] = {}
    for name, key_fn in dimensions.items():
        buckets: Dict[str, Dict[str, int]] = defaultdict(lambda: {"candidate": 0, "reference": 0, "tie": 0, "uncertain": 0})
        for record in case_records:
            verdict = str(((record.get("vlm") or {}).get("reconciled")) or "uncertain")
            if verdict not in buckets.default_factory():
                verdict = "uncertain"
            for key in key_fn(record):
                buckets[key][verdict] += 1
        result[name] = dict(buckets)
    return result


def write_regression_report(report: Dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "regression_report.json"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path = output_dir / "regression_report.md"
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return md_path


def render_markdown(report: Dict[str, Any]) -> str:
    decision = report["decision"]
    comparison = report["comparison"]
    safety = report["safety"]
    paired = report["paired_visual"]
    coverage = report["coverage"]

    lines: List[str] = []
    lines.append(f"# 回归报告：{comparison['reference']} → {comparison['candidate']}")
    lines.append("")
    lines.append(f"## 结论：**{decision['decision']}**")
    for reason in decision.get("reasons") or []:
        lines.append(f"- {reason}")
    lines.append("")

    lines.append("## 安全指标（硬门禁）")
    lines.append("| 指标 | v1 | v2 |")
    lines.append("|---|---|---|")
    for key, value in safety["v1"].items():
        label = SAFETY_METRIC_LABELS.get(key, key)
        lines.append(f"| {label} | {value} | {safety['v2'].get(key)} |")
    if safety["regressions"]:
        lines.append("")
        lines.append("**安全回退：** " + "；".join(safety["regressions"]))
    lines.append("")

    lines.append("## 视觉对比（v1 vs v2，双序对账后）")
    wins = paired["win_counts"]
    lines.append(
        f"- candidate 胜 {wins.get('candidate', 0)} / reference 胜 {wins.get('reference', 0)} / "
        f"平 {wins.get('tie', 0)} / 不确定 {wins.get('uncertain', 0)}"
    )
    if paired.get("paired_delta_mean") is not None:
        lines.append(
            f"- paired delta：{paired['paired_delta_mean']:+.3f}（95% CI "
            f"[{paired['paired_delta_ci95'][0]:+.3f}, {paired['paired_delta_ci95'][1]:+.3f}]）"
        )
    lines.append("")

    if report.get("absolute_vs_b0"):
        lines.append("## 与 B0 的绝对对比")
        for key, value in report["absolute_vs_b0"].items():
            lines.append(f"- {key}: {value}")
        lines.append("")

    lines.append("## 分层结果")
    for dimension, buckets in (report.get("stratified") or {}).items():
        lines.append(f"### {dimension}")
        lines.append("| 分组 | candidate | reference | tie | uncertain |")
        lines.append("|---|---|---|---|---|")
        for bucket, counts in buckets.items():
            lines.append(f"| {bucket} | {counts['candidate']} | {counts['reference']} | {counts['tie']} | {counts['uncertain']} |")
        lines.append("")

    lines.append("## 覆盖率与失败原因")
    stages = coverage["stages"]
    lines.append(
        f"- assigned {stages.get('assigned', 0)} → injected {stages.get('injected', 0)} → "
        f"B0 rendered {stages.get('b0_rendered', 0)} → candidate rendered {stages.get('candidate_rendered', 0)} → "
        f"scored {stages.get('scored', 0)}"
    )
    if coverage.get("failure_reasons"):
        lines.append("- 失败原因：" + "；".join(f"{k} × {v}" for k, v in coverage["failure_reasons"].items()))
    lines.append("")

    eligibility = report.get("conclusion_eligibility") or {}
    if eligibility.get("note"):
        lines.append(f"> ⚠️ {eligibility['note']}")
        lines.append("")
    return "\n".join(lines)
