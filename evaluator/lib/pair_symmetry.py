"""Reconcile pairwise VLM verdicts from normal and position-swapped runs.

Position-bias control: evaluate every case twice (default order and
--swap-pair-order). Roles stay labeled, so verdict semantics are identical;
if the two runs disagree, the case is position-sensitive and should be
counted as uncertain rather than as a win.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def _extract_verdict(report: Dict[str, Any]) -> Dict[str, Any]:
    vlm = report.get("vlm") if isinstance(report.get("vlm"), dict) else report
    winner = str(vlm.get("winner") or "").strip().lower()
    if winner not in {"b0", "candidate", "tie"}:
        winner = "unknown"
    repair_score = vlm.get("repair_score")
    return {"winner": winner, "repair_score": repair_score}


def reconcile_pair_runs(
    normal_report: Dict[str, Any],
    swapped_report: Dict[str, Any],
) -> Dict[str, Any]:
    normal = _extract_verdict(normal_report)
    swapped = _extract_verdict(swapped_report)

    conflict_note = None
    if normal["winner"] == "unknown" or swapped["winner"] == "unknown":
        agreement = "unknown"
        winner: Optional[str] = None
        conflict_note = "at least one run has no usable winner verdict"
    elif normal["winner"] == swapped["winner"]:
        agreement = "agree"
        winner = normal["winner"]
    else:
        agreement = "conflict"
        winner = "uncertain"
        conflict_note = (
            f"normal-order winner={normal['winner']} but swapped-order winner={swapped['winner']}; "
            "position bias is likely"
        )

    scores = [normal["repair_score"], swapped["repair_score"]]
    numeric_scores = [int(score) for score in scores if isinstance(score, (int, float))]

    return {
        "agreement": agreement,
        "winner": winner,
        "repair_score_normal": normal["repair_score"],
        "repair_score_swapped": swapped["repair_score"],
        "score_sign_agreement": (
            None
            if len(numeric_scores) < 2
            else (numeric_scores[0] > 0) == (numeric_scores[1] > 0) or (numeric_scores[0] == 0 and numeric_scores[1] == 0)
        ),
        "note": conflict_note or "both orders agree",
    }
