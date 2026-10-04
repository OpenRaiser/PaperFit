"""Generic revision-pair comparison (v1 vs v2) for regression evaluation.

The official V3 protocol assumes the reference side is the *perturbed,
unrepaired input* (B0). Stuffing an older PaperFit version into the B0 slot
would pollute that semantics. This module keeps the official response schema
and scoring normalization, but uses revision wording: both sides are repair
outputs and the question is "did the candidate version improve over the
reference version".

Wire-format mapping: the model answers with reference_*/candidate keys and
winner in {reference, candidate, tie}; to_v3_payload() renames them to the
official b0_* keys so normalize_v3_result() can be reused unchanged.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

_LIB_DIR = Path(__file__).resolve().parent
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

from run_vlm_evaluation import (  # noqa: E402
    _default_font,
    _fit_height,
    _load_pages,
    stitch_images,
)
from vlm_protocol import normalize_v3_result  # noqa: E402

try:  # Pillow is required for montage building, same as the official runner
    from PIL import Image, ImageDraw
except ImportError as exc:  # pragma: no cover
    raise ImportError("revision_pair requires Pillow") from exc


REVISION_SYSTEM_PROMPT = """You are an expert evaluator for Visual Typesetting Optimization (VTO) in academic papers.

You compare two rendered versions of the same paper produced by two revisions of a typesetting system.

The provided image is a side-by-side montage:
- LEFT / RIGHT: two versions of the same repaired paper, labeled on the image.

Use a defect-aware, constraint-aware, pairwise-aware workflow:
1. Identify visible layout defects in each version.
2. Check whether the candidate version fixes defects that remain in the reference version.
3. Check whether the candidate version introduces new visible layout defects.
4. Consider hard constraints such as page budget using the numeric context supplied in the prompt.
5. Judge which version has the better layout.

Rules:
- Judge only visible layout quality and the provided hard-constraint metadata.
- Do not judge scientific novelty, correctness, or writing quality.
- Identical-looking versions must be reported as tie with repair_outcome "unchanged".
- A version that violates the page budget, fails rendering, or shows major new defects must be penalized.
- Return valid JSON only.
"""


def build_revision_user_prompt(
    *,
    reference_method: str,
    candidate_method: str,
    template_name: str,
    profile: str | None,
    target_pages: int | None,
    reference_page_count: int | None,
    candidate_page_count: int | None,
    candidate_compile_success: bool | None,
    candidate_render_success: bool | None,
    candidate_page_budget_hit: bool | None,
    candidate_undefined_ref_count: int | None,
    swap_order: bool = False,
) -> str:
    target_pages_text = "unknown" if target_pages is None else str(target_pages)
    reference_pages_text = "unknown" if reference_page_count is None else str(reference_page_count)
    candidate_pages_text = "unknown" if candidate_page_count is None else str(candidate_page_count)
    if swap_order:
        layout_lines = (
            f"- LEFT montage: candidate version {candidate_method}.\n"
            f"- RIGHT montage: reference version {reference_method}."
        )
    else:
        layout_lines = (
            f"- LEFT montage: reference version {reference_method}.\n"
            f"- RIGHT montage: candidate version {candidate_method}."
        )
    return f"""Compare the two revisions using the V3 response schema.

Image layout:
{layout_lines}
- Pages are arranged left-to-right, top-to-bottom inside each montage.
- Side roles are defined by these labels only; judge layout quality, not paper content.

Case context:
- Reference version: {reference_method}
- Candidate version: {candidate_method}
- Template / venue: {template_name}
- Difficulty profile: {profile or "unknown"}
- Target pages: {target_pages_text}
- Reference rendered pages: {reference_pages_text}
- Candidate rendered pages: {candidate_pages_text}

Candidate hard-constraint metadata (applies to the candidate version on whichever side it appears):
- compile_success: {candidate_compile_success}
- render_success: {candidate_render_success}
- page_budget_hit: {candidate_page_budget_hit}
- undefined_ref_count: {candidate_undefined_ref_count}

Respond with a single JSON object using exactly these keys:
- reference_defects, candidate_defects: lists of visible layout defects (same defect vocabulary as V3).
- target_defect_assessment: assessment of remaining layout issues per side.
- new_defects_introduced: defects visible in the candidate but not the reference.
- scores: object with defect_resolution, constraint_alignment, visual_quality,
  new_defect_avoidance, publication_readiness — each integer 1-5, describing
  the candidate relative to the reference.
- final_aesthetic_scores: object with overall_professionalism, space_utilization,
  float_placement_quality, typographic_consistency, visual_balance — each integer 1-5.
- repair_outcome: one of worse, unchanged, slightly_improved, clearly_improved, fully_fixed
  (candidate relative to reference).
- repair_score: -1..3 integer consistent with repair_outcome.
- winner: one of reference, candidate, tie.
- confidence: 0..1.
- summary: one-sentence justification.
"""


def to_v3_payload(revision_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Rename revision wire keys onto the official V3 schema."""
    payload = dict(revision_payload)
    if "reference_defects" in payload:
        payload["b0_defects"] = payload.pop("reference_defects")
    winner = str(payload.get("winner") or "").strip().lower()
    if winner == "reference":
        payload["winner"] = "b0"
    return payload


def normalize_revision_result(
    revision_payload: Dict[str, Any],
    *,
    hard_constraints: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    result = normalize_v3_result(to_v3_payload(revision_payload), hard_constraints=hard_constraints or {})
    result["revision_pair"] = True
    return result


def labeled_revision_comparison_image(
    *,
    reference_pages_dir: Path,
    candidate_pages_dir: Path,
    reference_method: str,
    candidate_method: str,
    max_dim: int,
    swap_order: bool = False,
) -> "Image.Image":
    reference_stitched = stitch_images(_load_pages(reference_pages_dir), max_dim=max_dim)
    candidate_stitched = stitch_images(_load_pages(candidate_pages_dir), max_dim=max_dim)

    target_h = max(reference_stitched.height, candidate_stitched.height)
    reference_stitched = _fit_height(reference_stitched, target_h)
    candidate_stitched = _fit_height(candidate_stitched, target_h)

    if swap_order:
        left, right = candidate_stitched, reference_stitched
        left_label = f"LEFT: candidate {candidate_method}"
        right_label = f"RIGHT: reference {reference_method}"
    else:
        left, right = reference_stitched, candidate_stitched
        left_label = f"LEFT: reference {reference_method}"
        right_label = f"RIGHT: candidate {candidate_method}"

    label_h = 44
    gap = 16
    total_w = left.width + right.width + gap
    total_h = target_h + label_h
    canvas = Image.new("RGB", (total_w, total_h), "white")
    draw = ImageDraw.Draw(canvas)
    font = _default_font(24)
    small = _default_font(16)

    canvas.paste(left, (0, label_h))
    canvas.paste(right, (left.width + gap, label_h))
    draw.rectangle((0, 0, left.width, label_h), fill=(235, 238, 242))
    draw.rectangle((left.width + gap, 0, total_w, label_h), fill=(229, 244, 236))
    draw.text((12, 8), left_label, fill=(20, 24, 32), font=font)
    draw.text((left.width + gap + 12, 8), right_label, fill=(20, 24, 32), font=font)
    draw.line((left.width + gap // 2, 0, left.width + gap // 2, total_h), fill=(40, 40, 40), width=3)
    draw.text((12, total_h - 22), "Pages in each side are ordered left-to-right, top-to-bottom.", fill=(80, 80, 80), font=small)

    if max(canvas.width, canvas.height) > max_dim:
        scale = max_dim / max(canvas.width, canvas.height)
        canvas = canvas.resize(
            (max(1, int(canvas.width * scale)), max(1, int(canvas.height * scale))),
            Image.LANCZOS,
        )
    return canvas


def iter_common_cases(reference_root: Path, candidate_root: Path) -> list[str]:
    """Case ids present (with rendered pages) under both method roots."""

    def _case_ids(root: Path) -> set[str]:
        found: set[str] = set()
        if not root.is_dir():
            return found
        for pages_dir in root.glob("*/*/pages"):
            if not any(pages_dir.glob("page_*.png")):
                continue
            case_id = f"{pages_dir.parent.parent.name}/{pages_dir.parent.name}"
            found.add(case_id)
        return found

    common = _case_ids(reference_root) & _case_ids(candidate_root)
    return sorted(common)


def load_case_evaluation(method_root: Path, case_id: str) -> Dict[str, Any]:
    from run_vlm_evaluation import _load_json

    return _load_json(method_root / case_id / "reports" / "evaluation_report.json")


def build_hard_constraints(candidate_evaluation: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "compile_success": candidate_evaluation.get("compile_success"),
        "render_success": candidate_evaluation.get("render_success"),
        "page_budget_hit": candidate_evaluation.get("page_budget_hit"),
        "undefined_ref_count": candidate_evaluation.get("undefined_ref_count"),
    }
