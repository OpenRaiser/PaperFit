#!/usr/bin/env python3
"""版本对版本的回归判定：IMPROVED / REGRESSED / NON_INFERIOR / INCONCLUSIVE / INVALID_RUN。

设计原则（见 docs/REGRESSION_DESIGN.md）：
- 安全指标（编译/渲染/内容完整性/页预算）一票否决：任何回退直接 REGRESSED；
- 视觉结论以 v1-vs-v2 配对分数差的 bootstrap 置信区间为准，区间跨 0 必须
  输出 INCONCLUSIVE，不许把 52% vs 48% 说成改进；
- 多个扰动来自同一论文时按论文聚类 bootstrap；
- manifest 不兼容输出 INVALID_RUN（新评测 epoch）。
"""

from __future__ import annotations

import random
import statistics
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

SAFETY_KEYS = (
    "compile_success",
    "render_success",
    "content_integrity_ok",
    "page_budget_hit",
)


def _deltas(pairs: Sequence[Tuple[str, float]]) -> List[float]:
    return [float(score) for _, score in pairs]


def paired_bootstrap_ci(
    pairs: Sequence[Tuple[str, float]],
    *,
    confidence: float = 0.95,
    iterations: int = 10000,
    seed: int = 20261005,
) -> Tuple[float, float, float]:
    """按论文聚类重抽样的配对均值差置信区间。

    pairs: (paper_id, score_delta) — 同一论文的多个扰动被视为一个簇。
    返回 (mean_delta, low, high)。
    """
    if not pairs:
        return (0.0, 0.0, 0.0)
    clusters: Dict[str, List[float]] = defaultdict(list)
    for paper_id, delta in pairs:
        clusters[paper_id].append(float(delta))
    cluster_means = [statistics.fmean(values) for values in clusters.values()]
    if len(cluster_means) == 1:
        return (cluster_means[0], cluster_means[0], cluster_means[0])

    rng = random.Random(seed)
    cluster_lists = list(clusters.values())  # 稳定顺序，保证可复现
    means: List[float] = []
    for _ in range(iterations):
        sampled: List[float] = []
        for _ in range(len(cluster_lists)):
            sampled.extend(cluster_lists[rng.randrange(len(cluster_lists))])
        means.append(statistics.fmean(sampled))
    means.sort()
    alpha = (1.0 - confidence) / 2.0
    low = means[int(alpha * len(means))]
    high = means[int((1.0 - alpha) * len(means)) - 1]
    observed = statistics.fmean([delta for _, delta in pairs])
    return (observed, low, high)


def check_safety(safety_v1: Dict[str, float], safety_v2: Dict[str, float], *, tolerance_pp: float = 0.0) -> List[str]:
    """安全指标回退检测（比例型，v2 < v1 - tolerance 即回退）。"""
    regressions: List[str] = []
    for key in SAFETY_KEYS:
        before = safety_v1.get(key)
        after = safety_v2.get(key)
        if before is None or after is None:
            continue
        if after < before - tolerance_pp:
            regressions.append(f"{key}: {before:.3f} -> {after:.3f}")
    return regressions


def decide_regression(
    *,
    safety_v1: Dict[str, float],
    safety_v2: Dict[str, float],
    paired_scores: Sequence[Dict[str, Any]],
    win_counts: Optional[Dict[str, int]] = None,
    minimum_detectable_delta: float = 0.10,
    confidence: float = 0.95,
    seed: int = 20261005,
    manifest_compatible: bool = True,
) -> Dict[str, Any]:
    """paired_scores: [{paper_id, case_id, delta(v2-v1 的 gated 分差), 双序对账后的有效分}]"""
    if not manifest_compatible:
        return {
            "decision": "INVALID_RUN",
            "reasons": ["run manifests incompatible (dataset/injection/render/judge/aggregation); new evaluation epoch"],
        }

    safety_regressions = check_safety(safety_v1, safety_v2)
    if safety_regressions:
        return {
            "decision": "REGRESSED",
            "reasons": [f"safety regression: {item}" for item in safety_regressions],
            "safety_regressions": safety_regressions,
        }

    if not paired_scores:
        return {
            "decision": "INCONCLUSIVE",
            "reasons": ["no scored cases available for visual comparison"],
        }

    pairs = [(str(item.get("paper_id") or item.get("case_id")), float(item.get("delta") or 0.0)) for item in paired_scores]
    mean_delta, low, high = paired_bootstrap_ci(pairs, confidence=confidence, seed=seed)

    reasons: List[str] = []
    if low > 0 and mean_delta >= minimum_detectable_delta:
        decision = "IMPROVED"
        reasons.append(f"paired delta {mean_delta:+.3f} (95% CI [{low:+.3f}, {high:+.3f}]) above MDE {minimum_detectable_delta}")
    elif high < 0:
        decision = "REGRESSED"
        reasons.append(f"paired delta {mean_delta:+.3f} (95% CI [{low:+.3f}, {high:+.3f}]) below zero")
    elif low >= -0.02 and high <= 0.02:
        decision = "NON_INFERIOR"
        reasons.append(f"paired delta {mean_delta:+.3f} tightly around zero (CI [{low:+.3f}, {high:+.3f}])")
    else:
        decision = "INCONCLUSIVE"
        reasons.append(
            f"confidence interval [{low:+.3f}, {high:+.3f}] crosses zero; "
            f"cannot distinguish from no change at current sample size"
        )

    summary: Dict[str, Any] = {
        "decision": decision,
        "reasons": reasons,
        "paired_delta_mean": round(mean_delta, 4),
        "paired_delta_ci95": [round(low, 4), round(high, 4)],
        "scored_cases": len(pairs),
        "minimum_detectable_delta": minimum_detectable_delta,
    }
    if win_counts:
        summary["win_counts"] = win_counts
    return summary
