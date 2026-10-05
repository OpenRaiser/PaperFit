#!/usr/bin/env python3
"""Run revision-pair (v1 vs v2) VLM comparisons for regression evaluation.

Both sides are repair outputs of the same case under two PaperFit versions.
Reuses the official V3 normalization and constraint gating via
lib/revision_pair.py; run twice (with and without --swap-pair-order) and
reconcile with lib/pair_symmetry.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

LIB_DIR = Path(__file__).resolve().parent / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from common_harness import write_json  # noqa: E402
from pair_symmetry import reconcile_pair_runs  # noqa: E402
from revision_pair import (  # noqa: E402
    REVISION_SYSTEM_PROMPT,
    build_hard_constraints,
    build_revision_user_prompt,
    iter_common_cases,
    labeled_revision_comparison_image,
    load_case_evaluation,
    normalize_revision_result,
)
from run_vlm_evaluation import _extract_json_object  # noqa: E402
from vlm_protocol import extract_json_with_mode  # noqa: E402


def evaluate_case(
    *,
    client: Any,
    reference_root: Path,
    candidate_root: Path,
    case_id: str,
    reference_method: str,
    candidate_method: str,
    output_dir: Path,
    max_image_dim: int,
    swap_order: bool,
    case_context: Optional[Dict[str, Any]] = None,
    force: bool = False,
) -> Dict[str, Any]:
    pair_tag = "swapped" if swap_order else "normal"
    case_out = output_dir / candidate_method / case_id.replace("/", "__")
    report_path = case_out / f"vlm_report_{pair_tag}.json"
    if report_path.exists() and not force:
        return json.loads(report_path.read_text(encoding="utf-8"))

    case_out.mkdir(parents=True, exist_ok=True)
    ref_eval = load_case_evaluation(reference_root, case_id)
    cand_eval = load_case_evaluation(candidate_root, case_id)
    context = case_context or {}
    
    
    
    constraints = build_hard_constraints(cand_eval, reference_evaluation=ref_eval)

    comparison_path = case_out / f"comparison_{pair_tag}.png"
    comparison = labeled_revision_comparison_image(
        reference_pages_dir=reference_root / case_id / "pages",
        candidate_pages_dir=candidate_root / case_id / "pages",
        reference_method=reference_method,
        candidate_method=candidate_method,
        max_dim=max_image_dim,
        swap_order=swap_order,
    )
    comparison.save(comparison_path)

    prompt = build_revision_user_prompt(
        reference_method=reference_method,
        candidate_method=candidate_method,
        template_name=str(context.get("template_name") or case_id.split("/")[0]),
        profile=context.get("profile"),
        target_pages=context.get("target_pages") or cand_eval.get("target_pages"),
        reference_page_count=ref_eval.get("page_count"),
        candidate_page_count=cand_eval.get("page_count"),
        candidate_compile_success=constraints.get("compile_success"),
        candidate_render_success=constraints.get("render_success"),
        candidate_page_budget_hit=constraints.get("page_budget_hit_absolute"),
        candidate_undefined_ref_count=cand_eval.get("undefined_ref_count"),
        reference_page_budget_hit=ref_eval.get("page_budget_hit"),
        reference_undefined_ref_count=ref_eval.get("undefined_ref_count"),
        swap_order=swap_order,
    )
    raw_response, usage = client.analyze(
        images=[comparison],
        system_prompt=REVISION_SYSTEM_PROMPT,
        user_prompt=prompt,
    )
    (case_out / f"raw_response_{pair_tag}.txt").write_text(raw_response, encoding="utf-8")

    parsed, json_parse_mode = extract_json_with_mode(raw_response)
    try:
        result = normalize_revision_result(
            parsed,
            hard_constraints=constraints,
        )
    except Exception as exc:
        from vlm_protocol import synthetic_v3_failure

        result = synthetic_v3_failure(f"revision-pair evaluation failed: {exc}", hard_constraints=constraints)
        result["error"] = str(exc)
    result["json_parse_mode"] = json_parse_mode
    result["usage"] = usage
    report = {
        "comparison_type": "revision_pair",
        "reference_method": reference_method,
        "candidate_method": candidate_method,
        "case_id": case_id,
        "generated_at": datetime.now().isoformat(),
        "pair_order": pair_tag,
        "hard_constraints": constraints,
        "vlm": result,
    }
    write_json(report_path, report)
    return report


def run_revision_evaluation(
    *,
    client: Any,
    reference_root: Path,
    candidate_root: Path,
    reference_method: str,
    candidate_method: str,
    output_dir: Path,
    case_ids: Sequence[str],
    case_root: Optional[Path],
    max_image_dim: int,
    swap_order: bool,
    force: bool = False,
) -> Dict[str, Any]:
    reports: list[Dict[str, Any]] = []
    for case_id in case_ids:
        context: Dict[str, Any] = {}
        benchmark_path = (case_root / case_id / "benchmark_case.json") if case_root else None
        if benchmark_path and benchmark_path.is_file():
            payload = json.loads(benchmark_path.read_text(encoding="utf-8"))
            context = payload.get("benchmark_case") or {}
        reports.append(
            evaluate_case(
                client=client,
                reference_root=reference_root,
                candidate_root=candidate_root,
                case_id=case_id,
                reference_method=reference_method,
                candidate_method=candidate_method,
                output_dir=output_dir,
                max_image_dim=max_image_dim,
                swap_order=swap_order,
                case_context=context,
                force=force,
            )
        )

    winners = {"candidate": 0, "b0": 0, "tie": 0}
    parse_modes = {"strict": 0, "rescued": 0, "failed": 0}
    for report in reports:
        winner = str((report.get("vlm") or {}).get("winner") or "")
        if winner in winners:
            winners[winner] += 1
        mode = str((report.get("vlm") or {}).get("json_parse_mode") or "unknown")
        if mode in parse_modes:
            parse_modes[mode] += 1
    total = len(reports) or 1
    summary = {
        "comparison_type": "revision_pair",
        "reference_method": reference_method,
        "candidate_method": candidate_method,
        "pair_order": "swapped" if swap_order else "normal",
        "case_count": len(reports),
        "candidate_wins": winners["candidate"],
        "reference_wins": winners["b0"],
        "ties": winners["tie"],
        "json_compliance": {
            "strict_rate": round(parse_modes["strict"] / total, 4),
            "rescued_rate": round(parse_modes["rescued"] / total, 4),
            "failed_rate": round(parse_modes["failed"] / total, 4),
            "counts": parse_modes,
            "warning": (
                "strict rate below 0.98: fallback scores may be masquerading as judgments"
                if parse_modes["strict"] / total < 0.98
                else None
            ),
        },
        "mean_gated_v3_score": (
            sum(float((r.get("vlm") or {}).get("gated_v3_score") or 0.0) for r in reports) / len(reports)
            if reports
            else None
        ),
        "reports": reports,
    }
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reference-root", required=True, help="Method root of the older version (e.g. runs/v1)")
    parser.add_argument("--candidate-root", required=True, help="Method root of the newer version (e.g. runs/v2)")
    parser.add_argument("--reference-method", default="v1")
    parser.add_argument("--candidate-method", default="v2")
    parser.add_argument("--case-root", default=None, help="Optional benchmark case root for context metadata")
    parser.add_argument("--case-list-file", default=None, help="Optional file of case ids (<paper>/<case> per line)")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--swap-pair-order", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--vlm-api-url", default=None)
    parser.add_argument("--vlm-api-key", default=None)
    parser.add_argument("--vlm-model-name", default=None)
    parser.add_argument("--vlm-api-timeout", type=int, default=240)
    parser.add_argument("--vlm-verify-ssl", choices=["true", "false"], default=None)
    parser.add_argument("--max-image-dim", type=int, default=1800)
    parser.add_argument("--jpeg-quality", type=int, default=78)
    parser.add_argument("--max-retries", type=int, default=2)
    args = parser.parse_args(argv)

    if args.case_list_file:
        case_ids = [
            line.strip()
            for line in Path(args.case_list_file).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        case_ids = iter_common_cases(Path(args.reference_root), Path(args.candidate_root))
    if not case_ids:
        print("no common cases with rendered pages under both roots", file=sys.stderr)
        return 1

    from vlm_evaluator import OpenAICompatibleVisionJudgeClient, VLMAbsoluteConfig

    if args.vlm_api_url and args.vlm_api_key and args.vlm_model_name:
        config = VLMAbsoluteConfig(
            api_url=args.vlm_api_url,
            api_key=args.vlm_api_key,
            model_name=args.vlm_model_name,
            timeout_sec=args.vlm_api_timeout,
            protocol_version="v3",
            mode="stitched",
            max_image_dim=args.max_image_dim,
            jpeg_quality=args.jpeg_quality,
            max_retries=args.max_retries,
            verify_ssl=(args.vlm_verify_ssl != "false"),
        )
    else:
        from run_vlm_evaluation import _config_from_env_or_claude_env

        config = _config_from_env_or_claude_env()
        config.protocol_version = "v3"
        config.mode = "stitched"
        config.timeout_sec = args.vlm_api_timeout
        config.max_image_dim = args.max_image_dim
        config.jpeg_quality = args.jpeg_quality
        config.max_retries = args.max_retries
    client = OpenAICompatibleVisionJudgeClient(config)

    summary = run_revision_evaluation(
        client=client,
        reference_root=Path(args.reference_root),
        candidate_root=Path(args.candidate_root),
        reference_method=args.reference_method,
        candidate_method=args.candidate_method,
        output_dir=Path(args.output_dir),
        case_ids=case_ids,
        case_root=Path(args.case_root) if args.case_root else None,
        max_image_dim=args.max_image_dim,
        swap_order=bool(args.swap_pair_order),
        force=bool(args.force),
    )
    write_json(Path(args.output_dir) / "revision_pair_summary.json", summary)
    print(
        json.dumps(
            {key: value for key, value in summary.items() if key != "reports"},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] if len(sys.argv) > 1 else None))
