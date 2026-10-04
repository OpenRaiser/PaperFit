#!/usr/bin/env python3
"裁判锚点漂移报警器（零人工标注）。"

from __future__ import annotations

import json
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ANCHOR_HISTORY_SCHEMA = "1.0"
DEFAULT_MEAN_DRIFT_THRESHOLD = 0.2
DEFAULT_SIGMA_THRESHOLD = 2.0
MIN_HISTORY_RUNS = 3


def load_history(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("runs") or []


def record_anchor_run(
    history_path: Path,
    scores: Dict[str, float],
    *,
    judge_model: Optional[str] = None,
    prompt_hash: Optional[str] = None,
) -> Dict[str, Any]:
    """追加一次锚点运行到历史文件并返回本次记录。"""
    history = load_history(history_path)
    entry = {
        "recorded_at": datetime.now().isoformat(),
        "judge_model": judge_model,
        "prompt_hash": prompt_hash,
        "scores": {key: float(value) for key, value in scores.items()},
    }
    history.append(entry)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(
        json.dumps({"schema_version": ANCHOR_HISTORY_SCHEMA, "runs": history}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return entry


def check_anchor_drift(
    history: List[Dict[str, Any]],
    *,
    mean_drift_threshold: float = DEFAULT_MEAN_DRIFT_THRESHOLD,
    sigma_threshold: float = DEFAULT_SIGMA_THRESHOLD,
    min_history_runs: int = MIN_HISTORY_RUNS,
) -> Dict[str, Any]:
    """对历史里最后一次运行做漂移判定（分母 = 之前的历史运行）。"""
    if len(history) < min_history_runs + 1:
        return {
            "status": "insufficient_history",
            "note": f"need {min_history_runs + 1} runs to judge drift, have {len(history)}",
            "judge_epoch_changed": False,
        }

    latest = history[-1]["scores"]
    prior_runs = history[:-1]

    per_anchor: Dict[str, Dict[str, Any]] = {}
    any_drift = False
    for anchor_id, latest_score in latest.items():
        prior_scores = [float(run["scores"][anchor_id]) for run in prior_runs if anchor_id in run.get("scores", {})]
        if len(prior_scores) < min_history_runs:
            per_anchor[anchor_id] = {"status": "insufficient_history"}
            continue
        prior_mean = statistics.fmean(prior_scores)
        prior_std = statistics.pstdev(prior_scores) if len(prior_scores) > 1 else 0.0
        drift = float(latest_score) - prior_mean
        sigma_multiple = abs(drift) / prior_std if prior_std > 0 else None
        drifted = abs(drift) > mean_drift_threshold or (
            sigma_multiple is not None and sigma_multiple > sigma_threshold
        )
        if drifted:
            any_drift = True
        per_anchor[anchor_id] = {
            "status": "drifted" if drifted else "stable",
            "latest": float(latest_score),
            "prior_mean": round(prior_mean, 4),
            "prior_std": round(prior_std, 4),
            "drift": round(drift, 4),
            "sigma_multiple": round(sigma_multiple, 4) if sigma_multiple is not None else None,
        }

    overall_mean_drift = None
    latest_values = list(latest.values())
    prior_means = [
        statistics.fmean([float(run["scores"][key]) for key in latest if key in run.get("scores", {})])
        for run in prior_runs
        if any(key in run.get("scores", {}) for key in latest)
    ]
    if latest_values and prior_means:
        overall_mean_drift = round(statistics.fmean(latest_values) - statistics.fmean(prior_means), 4)

    return {
        "status": "drifted" if any_drift else "stable",
        "judge_epoch_changed": any_drift,
        "overall_mean_drift": overall_mean_drift,
        "per_anchor": per_anchor,
        "note": (
            "judge epoch change suspected: treat cross-epoch score comparisons as invalid; "
            "re-baseline or investigate the provider/model change"
            if any_drift
            else "no drift beyond thresholds"
        ),
    }
