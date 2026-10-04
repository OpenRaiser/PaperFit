from __future__ import annotations

from typing import Any, Dict, Iterable, List


DEFECT_TYPES: tuple[str, ...] = (
    "large_whitespace_block",
    "half_column_blank",
    "overwide_float_or_crop",
    "underutilized_table_width",
    "float_clustering",
    "caption_detachment",
    "column_balance_break",
    "typography_inconsistency",
    "reference_tail_gap",
    "page_density_mismatch",
)

SEVERITY_LEVELS: tuple[str, ...] = ("minor", "moderate", "major", "critical")

SEVERITY_RANK: Dict[str, int] = {
    "minor": 0,
    "moderate": 1,
    "major": 2,
    "critical": 3,
}

DEFECT_TYPE_ALIASES: Dict[str, str] = {
    "large_whitespace": "large_whitespace_block",
    "large_whitespace_area": "large_whitespace_block",
    "half_column_gap": "half_column_blank",
    "blank_half_column": "half_column_blank",
    "overwide_float": "overwide_float_or_crop",
    "overwide_table": "overwide_float_or_crop",
    "cropped_float": "overwide_float_or_crop",
    "cropped_table": "overwide_float_or_crop",
    "underutilized_table": "underutilized_table_width",
    "table_width_underutilized": "underutilized_table_width",
    "float_stack": "float_clustering",
    "caption_detached": "caption_detachment",
    "caption_misalignment": "caption_detachment",
    "column_balance": "column_balance_break",
    "balance_break": "column_balance_break",
    "typographic_inconsistency": "typography_inconsistency",
    "reference_tail": "reference_tail_gap",
    "density_mismatch": "page_density_mismatch",
}

SEVERITY_ALIASES: Dict[str, str] = {
    "low": "minor",
    "medium": "moderate",
    "med": "moderate",
    "severe": "major",
    "high": "major",
    "very_severe": "critical",
    "extreme": "critical",
}


V3_SCORE_FIELDS: tuple[str, ...] = (
    "defect_resolution",
    "constraint_alignment",
    "visual_quality",
    "new_defect_avoidance",
    "publication_readiness",
)

V3_SCORE_WEIGHTS: Dict[str, float] = {
    "defect_resolution": 0.35,
    "constraint_alignment": 0.25,
    "visual_quality": 0.20,
    "new_defect_avoidance": 0.10,
    "publication_readiness": 0.10,
}

FINAL_AESTHETIC_SCORE_FIELDS: tuple[str, ...] = (
    "overall_professionalism",
    "space_utilization",
    "float_placement_quality",
    "typographic_consistency",
    "visual_balance",
)

FINAL_AESTHETIC_SCORE_WEIGHTS: Dict[str, float] = {
    "overall_professionalism": 0.25,
    "space_utilization": 0.20,
    "float_placement_quality": 0.25,
    "typographic_consistency": 0.15,
    "visual_balance": 0.15,
}

V3_COMPONENT_WEIGHTS: Dict[str, float] = {
    "repair_absolute": 0.35,
    "pairwise_repair": 0.40,
    "final_aesthetic": 0.25,
}

COMPILE_DIRTY_PENALTY = 0.60
COMPILE_DIRTY_SOFT_CAP = 3.25
SOFT_CAP_SLOPE = 0.05

REPAIR_OUTCOMES: tuple[str, ...] = (
    "worse",
    "unchanged",
    "slightly_improved",
    "clearly_improved",
    "fully_fixed",
)

REPAIR_SCORE_BY_OUTCOME: Dict[str, int] = {
    "worse": -1,
    "unchanged": 0,
    "slightly_improved": 1,
    "clearly_improved": 2,
    "fully_fixed": 3,
}

V3_SYSTEM_PROMPT = """You are an expert evaluator for Visual Typesetting Optimization (VTO) in academic papers.

You evaluate whether a candidate repair improves a perturbed LaTeX paper layout.

The provided image is a side-by-side montage:
- LEFT: B0, the perturbed input without any repair.
- RIGHT: the candidate method output for the same paper.

Use a defect-aware, constraint-aware, pairwise-aware workflow:
1. Identify visible layout defects in B0 and in the candidate.
2. Check whether the candidate fixes the target perturbation types.
3. Check whether the candidate introduces new visible layout defects.
4. Consider hard constraints such as page budget using the numeric context supplied in the prompt.
5. Score the candidate as a VTO repair.
6. Separately score whether the final candidate layout is aesthetically polished and publication-like.

Rules:
- Judge only visible layout quality and the provided hard-constraint metadata.
- Do not judge scientific novelty, correctness, or writing quality.
- B0 should not receive repair credit merely for looking like a readable paper; unchanged output is repair_score 0.
- A candidate that violates the page budget, fails rendering, or introduces major new defects must be penalized.
- A candidate that improves one defect but creates a worse defect should not be considered a successful repair.
- A candidate can receive repair credit for improving B0 while still receiving a lower final aesthetic score if the final paper remains visually unpolished.
- Return valid JSON only.
"""


V3_RESCUE_SYSTEM_PROMPT = """You are an expert evaluator for Visual Typesetting Optimization (VTO) in academic papers.

You evaluate a candidate repair for a perturbed LaTeX paper whose B0 perturbed input did not render into page images.

The provided image contains only the candidate method output. In this rescue mode, a candidate that compiles/renders successfully has repaired a critical B0 failure mode, but it must still be judged for visible final layout quality and hard constraints.

Use a defect-aware, constraint-aware, final-layout-aware workflow:
1. Identify visible layout defects in the candidate.
2. Judge whether the candidate is a usable render rescue relative to non-renderable B0.
3. Score whether the final candidate layout is aesthetically polished and publication-like.
4. Consider hard constraints such as page budget using the numeric context supplied in the prompt.

Rules:
- Judge only visible layout quality and the provided hard-constraint metadata.
- Do not judge scientific novelty, correctness, or writing quality.
- If the candidate renders successfully while B0 did not render, do not mark it unchanged merely because B0 is not visible.
- A renderable candidate with severe visible defects or page-budget failure should receive limited rescue credit.
- Return valid JSON only.
"""


V3_RESPONSE_EXAMPLE = """{
  "protocol_version": "v3",
  "evaluation_mode": "defect_constraint_pairwise",
  "b0_defects": [
    {
      "page": 8,
      "defect_type": "reference_tail_gap",
      "severity": "major",
      "evidence": "The reference page leaves a large empty lower half."
    }
  ],
  "candidate_defects": [
    {
      "page": 4,
      "defect_type": "float_clustering",
      "severity": "moderate",
      "evidence": "Several figures are stacked consecutively with little intervening text."
    }
  ],
  "target_defect_assessment": [
    {
      "target": "A4_column_imbalance",
      "status": "fixed",
      "evidence": "The candidate balances the two columns on the affected page better than B0."
    }
  ],
  "new_defects_introduced": [
    {
      "page": 4,
      "defect_type": "float_clustering",
      "severity": "moderate",
      "evidence": "The candidate newly clusters multiple figures on one page."
    }
  ],
  "repair_outcome": "clearly_improved",
  "repair_score": 2,
  "winner": "candidate",
  "confidence": 0.82,
  "scores": {
    "defect_resolution": 4,
    "constraint_alignment": 5,
    "visual_quality": 4,
    "new_defect_avoidance": 3,
    "publication_readiness": 4
  },
  "final_aesthetic_scores": {
    "overall_professionalism": 4,
    "space_utilization": 4,
    "float_placement_quality": 4,
    "typographic_consistency": 4,
    "visual_balance": 3
  },
  "summary": "The candidate clearly improves the target column imbalance while introducing only a moderate float-clustering issue."
}"""


def build_v3_rescue_user_prompt(
    *,
    method_id: str,
    template_name: str,
    profile: str | None,
    target_pages: int | None,
    requested_perturbations: Iterable[str],
    candidate_page_count: int | None,
    candidate_compile_success: bool | None,
    candidate_render_success: bool | None,
    candidate_page_budget_hit: bool | None,
    candidate_undefined_ref_count: int | None,
) -> str:
    target_pages_text = "unknown" if target_pages is None else str(target_pages)
    candidate_page_count_text = "unknown" if candidate_page_count is None else str(candidate_page_count)
    perturbation_text = ", ".join(str(p) for p in requested_perturbations) or "unknown"
    defect_type_text = ", ".join(DEFECT_TYPES)
    severity_text = ", ".join(SEVERITY_LEVELS)
    return f"""Evaluate the candidate method output using the V3 render-rescue VTO evaluation protocol.

Image layout:
- The image contains ONLY the candidate output from method {method_id}.
- B0 did not render and has no page images, so there is no left-side visual anchor.
- Pages are arranged left-to-right, top-to-bottom.

Case context:
- Method: {method_id}
- Template / venue: {template_name}
- Difficulty profile: {profile or "unknown"}
- Target pages: {target_pages_text}
- B0 render status: not renderable / no rendered page images
- Candidate rendered pages: {candidate_page_count_text}
- Requested perturbations / target defects: {perturbation_text}

Candidate hard-constraint metadata:
- compile_success: {candidate_compile_success}
- render_success: {candidate_render_success}
- page_budget_hit: {candidate_page_budget_hit}
- undefined_ref_count: {candidate_undefined_ref_count}

Allowed defect_type values:
- {defect_type_text}

Allowed severity values:
- {severity_text}

Repair outcome values for render-rescue mode:
- worse: candidate also fails to render or is visibly unusable
- unchanged: candidate gives no meaningful rescue; use rarely when candidate is not materially usable
- slightly_improved: candidate renders but has severe layout defects or hard-constraint failure
- clearly_improved: candidate renders and is a usable paper layout with remaining imperfections
- fully_fixed: candidate renders cleanly, satisfies constraints, and looks close to publication-ready

Candidate scoring fields, each integer 1-5:
- defect_resolution: how well the candidate rescues the non-renderable B0 failure and handles requested layout defects when visible
- constraint_alignment: page budget and visible hard-constraint compliance
- visual_quality: final visible layout quality of the candidate
- new_defect_avoidance: whether the candidate avoids severe visible layout defects
- publication_readiness: whether the candidate looks close to a publishable paper layout

Final aesthetic scoring fields, each integer 1-5:
- overall_professionalism
- space_utilization
- float_placement_quality
- typographic_consistency
- visual_balance

Important:
- final_aesthetic_scores judge the candidate layout as a final paper on its own.
- Score 5 in final_aesthetic_scores means close to polished top-tier published-paper visual quality.
- If page_budget_hit is false, constraint_alignment should be no higher than 2 unless target_pages is unknown.
- If render_success is false, all candidate scoring fields and final aesthetic scoring fields should be 1.

Return JSON only using exactly this structure:
{V3_RESPONSE_EXAMPLE}
"""


def build_v3_user_prompt(
    *,
    method_id: str,
    template_name: str,
    profile: str | None,
    target_pages: int | None,
    requested_perturbations: Iterable[str],
    b0_page_count: int | None,
    candidate_page_count: int | None,
    candidate_compile_success: bool | None,
    candidate_render_success: bool | None,
    candidate_page_budget_hit: bool | None,
    candidate_undefined_ref_count: int | None,
) -> str:
    target_pages_text = "unknown" if target_pages is None else str(target_pages)
    b0_page_count_text = "unknown" if b0_page_count is None else str(b0_page_count)
    candidate_page_count_text = "unknown" if candidate_page_count is None else str(candidate_page_count)
    perturbation_text = ", ".join(str(p) for p in requested_perturbations) or "unknown"
    defect_type_text = ", ".join(DEFECT_TYPES)
    severity_text = ", ".join(SEVERITY_LEVELS)
    return f"""Evaluate the candidate method output using the V3 VTO evaluation protocol.

Image layout:
- LEFT montage: B0 perturbed input.
- RIGHT montage: candidate output from method {method_id}.
- Pages are arranged left-to-right, top-to-bottom inside each montage.

Case context:
- Method: {method_id}
- Template / venue: {template_name}
- Difficulty profile: {profile or "unknown"}
- Target pages: {target_pages_text}
- B0 rendered pages: {b0_page_count_text}
- Candidate rendered pages: {candidate_page_count_text}
- Requested perturbations / target defects: {perturbation_text}

Candidate hard-constraint metadata:
- compile_success: {candidate_compile_success}
- render_success: {candidate_render_success}
- page_budget_hit: {candidate_page_budget_hit}
- undefined_ref_count: {candidate_undefined_ref_count}

Allowed defect_type values:
- {defect_type_text}

Allowed severity values:
- {severity_text}

Target defect status values:
- fixed
- partially_fixed
- unchanged
- worse
- not_visible_or_uncertain

Repair outcome values:
- worse: candidate is visually or constraint-wise worse than B0
- unchanged: candidate is effectively the same as B0
- slightly_improved: candidate fixes a small part of the target defect but leaves major issues
- clearly_improved: candidate substantially improves target defects without severe new problems
- fully_fixed: candidate fixes target defects, satisfies constraints, and introduces no meaningful new defects

Repair score mapping:
- worse = -1
- unchanged = 0
- slightly_improved = 1
- clearly_improved = 2
- fully_fixed = 3

Candidate scoring fields, each integer 1-5:
- defect_resolution: how well target perturbation defects are fixed relative to B0
- constraint_alignment: page budget and visible hard-constraint compliance
- visual_quality: final visible layout quality of the candidate
- new_defect_avoidance: whether the candidate avoids introducing new layout defects
- publication_readiness: whether the candidate looks close to a publishable paper layout

Final aesthetic scoring fields, each integer 1-5:
- overall_professionalism: whether the candidate looks like a polished professional academic paper
- space_utilization: whether pages use available text/float space efficiently without obvious blank waste
- float_placement_quality: whether figures, tables, and captions are sized and placed harmoniously
- typographic_consistency: whether text, captions, tables, headings, and references look visually consistent
- visual_balance: whether columns, pages, and the final/reference pages feel balanced and well composed

Important:
- If method is B0, the candidate is the unmodified perturbed input and should receive repair_outcome "unchanged" unless the metadata or images show otherwise.
- If page_budget_hit is false, constraint_alignment should be no higher than 2 unless the target_pages metadata is unknown.
- If render_success is false, all candidate scoring fields and final aesthetic scoring fields should be 1.
- If the candidate introduces a major new defect, new_defect_avoidance should be no higher than 2.
- final_aesthetic_scores judge the RIGHT/candidate layout as a final paper on its own, not merely whether it improved over B0.
- Score 5 in final_aesthetic_scores means close to polished top-tier published-paper visual quality.

Return JSON only using exactly this structure:
{V3_RESPONSE_EXAMPLE}
"""


def normalize_v3_result(payload: Dict[str, Any], *, hard_constraints: Dict[str, Any] | None = None) -> Dict[str, Any]:
    _require_dict(payload, "v3 payload")
    b0_defects = _normalize_defects(payload.get("b0_defects"))
    candidate_defects = _normalize_defects(payload.get("candidate_defects"))
    target_assessment = _normalize_target_assessment(payload.get("target_defect_assessment"))
    new_defects = _normalize_defects(payload.get("new_defects_introduced"))
    scores = _normalize_scores(payload.get("scores"))
    final_aesthetic_scores = _normalize_final_aesthetic_scores(payload.get("final_aesthetic_scores"))

    repair_outcome = payload.get("repair_outcome")
    if not isinstance(repair_outcome, str):
        repair_outcome = "unchanged"
    repair_outcome = repair_outcome.strip().lower()
    if repair_outcome not in REPAIR_OUTCOMES:
        repair_outcome = "unchanged"

    repair_score_raw = payload.get("repair_score")
    if isinstance(repair_score_raw, (int, float)):
        repair_score = int(round(float(repair_score_raw)))
        repair_score = max(-1, min(3, repair_score))
    else:
        repair_score = REPAIR_SCORE_BY_OUTCOME[repair_outcome]

    winner = payload.get("winner")
    if isinstance(winner, str):
        winner = winner.strip().lower()
    if winner not in {"b0", "candidate", "tie"}:
        winner = "candidate" if repair_score > 0 else ("b0" if repair_score < 0 else "tie")

    confidence_raw = payload.get("confidence")
    confidence = float(confidence_raw) if isinstance(confidence_raw, (int, float)) else 0.5
    confidence = round(max(0.0, min(1.0, confidence)), 4)

    summary = payload.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        summary = "V3 VTO evaluation completed."

    absolute_score = _weighted_score(scores, V3_SCORE_FIELDS, V3_SCORE_WEIGHTS)
    final_aesthetic_score = _weighted_score(
        final_aesthetic_scores,
        FINAL_AESTHETIC_SCORE_FIELDS,
        FINAL_AESTHETIC_SCORE_WEIGHTS,
    )
    pairwise_repair_score_5pt = round(1.0 + ((repair_score + 1) / 4.0) * 4.0, 4)
    raw_v3_score = round(
        V3_COMPONENT_WEIGHTS["repair_absolute"] * absolute_score
        + V3_COMPONENT_WEIGHTS["pairwise_repair"] * pairwise_repair_score_5pt
        + V3_COMPONENT_WEIGHTS["final_aesthetic"] * final_aesthetic_score,
        4,
    )
    gated_v3_score, gate_reasons = _apply_constraint_gate(raw_v3_score, hard_constraints or {}, new_defects)

    return {
        "protocol_version": "v3",
        "evaluation_mode": "defect_constraint_pairwise",
        "b0_defects": b0_defects,
        "candidate_defects": candidate_defects,
        "target_defect_assessment": target_assessment,
        "new_defects_introduced": new_defects,
        "repair_outcome": repair_outcome,
        "repair_score": repair_score,
        "pairwise_repair_score_5pt": pairwise_repair_score_5pt,
        "winner": winner,
        "confidence": confidence,
        "scores": scores,
        "final_aesthetic_scores": final_aesthetic_scores,
        "v3_absolute_score": absolute_score,
        "final_aesthetic_score": final_aesthetic_score,
        "raw_v3_score": raw_v3_score,
        "gated_v3_score": gated_v3_score,
        "constraint_gate_reasons": gate_reasons,
        "defect_counts": {
            "b0": _defect_summary(b0_defects),
            "candidate": _defect_summary(candidate_defects),
            "new": _defect_summary(new_defects),
        },
        "score_weights": dict(V3_SCORE_WEIGHTS),
        "final_aesthetic_score_weights": dict(FINAL_AESTHETIC_SCORE_WEIGHTS),
        "component_weights": dict(V3_COMPONENT_WEIGHTS),
        "summary": summary.strip(),
    }


def synthetic_v3_failure(reason: str, *, hard_constraints: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = {
        "scores": {field: 1 for field in V3_SCORE_FIELDS},
        "final_aesthetic_scores": {field: 1 for field in FINAL_AESTHETIC_SCORE_FIELDS},
        "repair_outcome": "worse",
        "repair_score": -1,
        "winner": "b0",
        "confidence": 1.0,
        "summary": reason,
    }
    return normalize_v3_result(payload, hard_constraints=hard_constraints)


def _weighted_score(scores: Dict[str, int], fields: Iterable[str], weights: Dict[str, float]) -> float:
    return round(sum(scores[key] * weights[key] for key in fields), 4)


def _apply_constraint_gate(
    score: float,
    hard_constraints: Dict[str, Any],
    new_defects: List[Dict[str, Any]],
) -> tuple[float, List[str]]:
    gated = float(score)
    reasons: List[str] = []

    if hard_constraints.get("compile_success") is False:
        reasons.append("compile_success is false")
        if hard_constraints.get("render_success") is not False:
            gated = _soft_cap(gated - COMPILE_DIRTY_PENALTY, COMPILE_DIRTY_SOFT_CAP)
    if hard_constraints.get("render_success") is False:
        gated = min(gated, 1.0)
        reasons.append("render_success is false")
    if hard_constraints.get("page_budget_hit") is False:
        gated = min(gated, 2.5)
        reasons.append("page_budget_hit is false")
    undefined_refs = hard_constraints.get("undefined_ref_count")
    if isinstance(undefined_refs, int) and undefined_refs > 0:
        gated = min(gated, 3.0)
        reasons.append("undefined references remain")
    if any(SEVERITY_RANK[d["severity"]] >= SEVERITY_RANK["major"] for d in new_defects):
        gated = min(gated, 3.0)
        reasons.append("candidate introduced at least one major new defect")
    return round(max(1.0, gated), 4), reasons


def _soft_cap(score: float, cap: float) -> float:
    if score <= cap:
        return score
    return cap - SOFT_CAP_SLOPE * (5.0 - min(score, 5.0))


def _normalize_scores(value: Any) -> Dict[str, int]:
    _require_dict(value, "scores")
    scores: Dict[str, int] = {}
    for field in V3_SCORE_FIELDS:
        raw = value.get(field)
        if not isinstance(raw, int) or not (1 <= raw <= 5):
            raise ValueError(f"scores.{field} must be an integer from 1 to 5")
        scores[field] = int(raw)
    return scores


def _normalize_final_aesthetic_scores(value: Any) -> Dict[str, int]:
    _require_dict(value, "final_aesthetic_scores")
    scores: Dict[str, int] = {}
    for field in FINAL_AESTHETIC_SCORE_FIELDS:
        raw = value.get(field)
        if not isinstance(raw, int) or not (1 <= raw <= 5):
            raise ValueError(f"final_aesthetic_scores.{field} must be an integer from 1 to 5")
        scores[field] = int(raw)
    return scores


def _normalize_defects(value: Any) -> List[Dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        return []
    defects: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        page = item.get("page")
        if isinstance(page, str) and page.isdigit():
            page = int(page)
        if not isinstance(page, int) or page < 1:
            continue
        defect_type = _normalize_defect_type(item.get("defect_type"))
        severity = _normalize_severity(item.get("severity"))
        evidence = item.get("evidence")
        if not defect_type or not severity:
            continue
        if not isinstance(evidence, str) or not evidence.strip():
            evidence = f"Visible {defect_type} on page {page}."
        defects.append(
            {
                "page": page,
                "defect_type": defect_type,
                "severity": severity,
                "evidence": evidence.strip(),
            }
        )
    return defects


def _normalize_target_assessment(value: Any) -> List[Dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        return []
    allowed = {"fixed", "partially_fixed", "unchanged", "worse", "not_visible_or_uncertain"}
    normalized: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        target = item.get("target")
        status = item.get("status")
        evidence = item.get("evidence")
        if not isinstance(target, str) or not target.strip():
            continue
        if not isinstance(status, str):
            status = "not_visible_or_uncertain"
        status = status.strip().lower()
        if status not in allowed:
            status = "not_visible_or_uncertain"
        if not isinstance(evidence, str) or not evidence.strip():
            evidence = f"Target defect {target.strip()} assessed as {status}."
        normalized.append({"target": target.strip(), "status": status, "evidence": evidence.strip()})
    return normalized


def _normalize_defect_type(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    key = value.strip().lower()
    normalized = DEFECT_TYPE_ALIASES.get(key, key)
    return normalized if normalized in DEFECT_TYPES else None


def _normalize_severity(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    key = value.strip().lower()
    normalized = SEVERITY_ALIASES.get(key, key)
    return normalized if normalized in SEVERITY_LEVELS else None


def _defect_summary(defects: List[Dict[str, Any]]) -> Dict[str, int]:
    return {
        "total": len(defects),
        "critical": sum(1 for d in defects if d["severity"] == "critical"),
        "major": sum(1 for d in defects if d["severity"] == "major"),
        "moderate": sum(1 for d in defects if d["severity"] == "moderate"),
        "minor": sum(1 for d in defects if d["severity"] == "minor"),
    }


def _require_dict(payload: Any, label: str) -> None:
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
