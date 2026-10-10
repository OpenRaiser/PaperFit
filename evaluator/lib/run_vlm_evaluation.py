#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from PIL import Image, ImageDraw, ImageFont

from common_harness import write_json
from vlm_evaluator import (
    OpenAICompatibleVisionJudgeClient,
    VLMAbsoluteConfig,
    _extract_json_object,
    stitch_images,
)
from vlm_protocol import (
    V3_RESCUE_SYSTEM_PROMPT,
    V3_SYSTEM_PROMPT,
    build_v3_rescue_user_prompt,
    build_v3_user_prompt,
    extract_json_with_mode,
    normalize_v3_result,
    synthetic_v3_failure,
)


DEFAULT_METHODS: Dict[str, Path] = {
    "B0": Path("baseline_runs/B0"),
    "B1": Path("baseline_runs/B1"),
    "B2": Path("baseline_runs/B2"),
    "B3": Path("baseline_runs/B3"),
    "B4": Path("baseline_runs/B4"),
    "OURS": Path("baseline_runs/OURS_AGENT"),
}


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _discover_case_ids(method_root: Path) -> set[str]:
    return {
        f"{path.parent.parent.parent.name}/{path.parent.parent.name}"
        for path in method_root.rglob("reports/evaluation_report.json")
    }


def _run_dir(method_root: Path, case_id: str) -> Path:
    return method_root / case_id


def _load_case_payload(case_id: str, case_root: Path) -> Dict[str, Any]:
    case_path = case_root / case_id / "benchmark_case.json"
    if not case_path.exists():
        return {}
    payload = _load_json(case_path)
    benchmark_case = payload.get("benchmark_case")
    return benchmark_case if isinstance(benchmark_case, dict) else {}


def _load_eval(run_dir: Path) -> Dict[str, Any]:
    path = run_dir / "reports" / "evaluation_report.json"
    return _load_json(path) if path.exists() else {}


def _hard_constraints(evaluation: Dict[str, Any], case_payload: Dict[str, Any]) -> Dict[str, Any]:
    target_pages = evaluation.get("target_pages")
    if target_pages is None:
        target_pages = case_payload.get("target_pages")
    return {
        "compile_success": evaluation.get("compile_success"),
        "render_success": evaluation.get("render_success"),
        "page_budget_hit": evaluation.get("page_budget_hit"),
        "page_count": evaluation.get("page_count"),
        "target_pages": target_pages,
        "undefined_ref_count": evaluation.get("undefined_ref_count"),
        "content_fidelity": evaluation.get("content_fidelity"),
        "semantic_home_ok": evaluation.get("semantic_home_ok"),
    }


def _load_pages(pages_dir: Path) -> List[Image.Image]:
    image_paths = sorted(
        path
        for path in pages_dir.glob("page_*.png")
        if not path.name.startswith("._")
    )
    if not image_paths:
        raise FileNotFoundError(f"no page_*.png images found under {pages_dir}")
    images: List[Image.Image] = []
    for path in image_paths:
        with Image.open(path) as image:
            images.append(image.convert("RGB").copy())
    return images


def _labeled_comparison_image(
    *,
    b0_pages_dir: Path,
    candidate_pages_dir: Path,
    candidate_method: str,
    max_dim: int,
    swap_order: bool = False,
) -> Image.Image:
    b0_stitched = stitch_images(_load_pages(b0_pages_dir), max_dim=max_dim)
    candidate_stitched = stitch_images(_load_pages(candidate_pages_dir), max_dim=max_dim)

    target_h = max(b0_stitched.height, candidate_stitched.height)
    b0_stitched = _fit_height(b0_stitched, target_h)
    candidate_stitched = _fit_height(candidate_stitched, target_h)

    label_h = 44
    gap = 16
    if swap_order:
        left, right = candidate_stitched, b0_stitched
        left_label = f"LEFT: {candidate_method} candidate"
        right_label = "RIGHT: B0 perturbed input"
    else:
        left, right = b0_stitched, candidate_stitched
        left_label = "LEFT: B0 perturbed input"
        right_label = f"RIGHT: {candidate_method} candidate"
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
        canvas = canvas.resize((max(1, int(canvas.width * scale)), max(1, int(canvas.height * scale))), Image.LANCZOS)
    return canvas


def _labeled_candidate_image(
    *,
    candidate_pages_dir: Path,
    candidate_method: str,
    max_dim: int,
) -> Image.Image:
    candidate_stitched = stitch_images(_load_pages(candidate_pages_dir), max_dim=max_dim)
    label_h = 44
    total_w = candidate_stitched.width
    total_h = candidate_stitched.height + label_h
    canvas = Image.new("RGB", (total_w, total_h), "white")
    draw = ImageDraw.Draw(canvas)
    font = _default_font(24)
    small = _default_font(16)

    canvas.paste(candidate_stitched, (0, label_h))
    draw.rectangle((0, 0, total_w, label_h), fill=(229, 244, 236))
    draw.text((12, 8), f"{candidate_method} candidate; B0 did not render", fill=(20, 24, 32), font=font)
    draw.text((12, total_h - 22), "Pages are ordered left-to-right, top-to-bottom.", fill=(80, 80, 80), font=small)

    if max(canvas.width, canvas.height) > max_dim:
        scale = max_dim / max(canvas.width, canvas.height)
        canvas = canvas.resize((max(1, int(canvas.width * scale)), max(1, int(canvas.height * scale))), Image.LANCZOS)
    return canvas


def _fit_height(image: Image.Image, target_h: int) -> Image.Image:
    if image.height == target_h:
        return image
    scale = target_h / image.height
    return image.resize((max(1, int(image.width * scale)), target_h), Image.LANCZOS)


def _default_font(size: int) -> ImageFont.ImageFont:
    for candidate in (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
    ):
        path = Path(candidate)
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                pass
    return ImageFont.load_default()


def _select_cases(method_roots: Dict[str, Path], sample_size: int, seed: int, *, require_b0_pages: bool = True) -> List[str]:
    common: set[str] | None = None
    for root in method_roots.values():
        case_ids = _discover_case_ids(root)
        common = case_ids if common is None else common & case_ids
    case_ids = sorted(common or [])
    if require_b0_pages:
        b0_root = method_roots.get("B0")
        if b0_root is None:
            raise ValueError("B0 method root is required when require_b0_pages is true")
        case_ids = [
            case_id
            for case_id in case_ids
            if _has_rendered_pages(b0_root / case_id / "pages")
            and bool(_load_eval(b0_root / case_id).get("render_success"))
        ]
    if len(case_ids) < sample_size:
        raise ValueError(f"only {len(case_ids)} common cases found; requested {sample_size}")
    rng = random.Random(seed)
    return sorted(rng.sample(case_ids, sample_size))


def _has_rendered_pages(pages_dir: Path) -> bool:
    return any(path.name.startswith("page_") and path.suffix.lower() == ".png" for path in pages_dir.glob("page_*.png"))


def _parse_method_root(items: Sequence[str]) -> Dict[str, Path]:
    roots = dict(DEFAULT_METHODS)
    for item in items:
        if "=" not in item:
            raise ValueError(f"expected METHOD=PATH, got {item!r}")
        method, raw_path = item.split("=", 1)
        method = method.strip().upper()
        if not method:
            raise ValueError(f"empty method in {item!r}")
        roots[method] = Path(raw_path).resolve()
    return roots


def _filter_methods(method_roots: Dict[str, Path], methods: str | None) -> Dict[str, Path]:
    if not methods:
        return method_roots
    selected = [method.strip().upper() for method in methods.split(",") if method.strip()]
    if not selected:
        raise ValueError("--methods must contain at least one method id")
    missing = [method for method in selected if method not in method_roots]
    if missing:
        raise ValueError(f"unknown methods in --methods: {', '.join(missing)}")
    return {method: method_roots[method] for method in selected}


def _select_methods(method_roots: Dict[str, Path], methods: str | None) -> List[str]:
    if not methods:
        return list(method_roots)
    selected = [method.strip().upper() for method in methods.split(",") if method.strip()]
    if not selected:
        raise ValueError("--evaluate-methods must contain at least one method id")
    missing = [method for method in selected if method not in method_roots]
    if missing:
        raise ValueError(f"unknown methods in --evaluate-methods: {', '.join(missing)}")
    return selected


def _load_case_list(path: Path) -> List[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _build_config(args: argparse.Namespace) -> VLMAbsoluteConfig:
    if args.vlm_api_url and args.vlm_api_key and args.vlm_model_name:
        return VLMAbsoluteConfig(
            api_url=args.vlm_api_url,
            api_key=args.vlm_api_key,
            model_name=args.vlm_model_name,
            timeout_sec=args.vlm_api_timeout,
            protocol_version="v3",
            mode="stitched",
            max_image_dim=args.max_image_dim,
            jpeg_quality=args.jpeg_quality,
            max_retries=args.max_retries,
            verify_ssl=_parse_bool(args.vlm_verify_ssl, default=True),
            swap_pair_order=bool(args.swap_pair_order),
        )
    config = _config_from_env_or_claude_env()
    config.protocol_version = "v3"
    config.mode = "stitched"
    config.timeout_sec = args.vlm_api_timeout
    config.max_image_dim = args.max_image_dim
    config.jpeg_quality = args.jpeg_quality
    config.max_retries = args.max_retries
    config.swap_pair_order = bool(args.swap_pair_order)
    if args.vlm_verify_ssl is not None:
        config.verify_ssl = _parse_bool(args.vlm_verify_ssl, default=config.verify_ssl)
    return config


def _config_from_env_or_claude_env() -> VLMAbsoluteConfig:
    try:
        return VLMAbsoluteConfig.from_env()
    except ValueError:
        env_path = Path.home() / ".claude" / "env.json"
        if not env_path.exists():
            raise
        payload = _load_json(env_path)
        env = payload.get("env")
        if not isinstance(env, dict):
            raise
        api_url = env.get("BASELINE_VLM_API_URL") or env.get("BASE_URL") or env.get("ANTHROPIC_BASE_URL")
        api_key = env.get("BASELINE_VLM_API_KEY") or env.get("API_KEY") or env.get("ANTHROPIC_AUTH_TOKEN")
        model_name = (
            env.get("BASELINE_VLM_MODEL_NAME")
            or env.get("MODEL")
            or env.get("ANTHROPIC_MODEL")
            or env.get("ANTHROPIC_DEFAULT_SONNET_MODEL")
        )
        if not api_url or not api_key or not model_name:
            raise ValueError("Missing VLM API config in environment and ~/.claude/env.json")
        return VLMAbsoluteConfig(
            api_url=str(api_url),
            api_key=str(api_key),
            model_name=str(model_name),
            timeout_sec=240,
            protocol_version="v3",
            mode="stitched",
        )


def _evaluate_one(
    *,
    client: OpenAICompatibleVisionJudgeClient,
    config: VLMAbsoluteConfig,
    method: str,
    method_root: Path,
    b0_root: Path,
    case_root: Path,
    case_id: str,
    output_dir: Path,
    force: bool,
) -> Dict[str, Any]:
    method_out_dir = output_dir / method / case_id
    report_path = method_out_dir / "vlm_report.json"
    if report_path.exists() and not force:
        return _load_json(report_path)

    method_out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = method_out_dir / "raw_response.txt"
    comparison_path = method_out_dir / "comparison.png"

    run_dir = _run_dir(method_root, case_id)
    b0_run_dir = _run_dir(b0_root, case_id)
    case_payload = _load_case_payload(case_id, case_root)
    evaluation = _load_eval(run_dir)
    b0_evaluation = _load_eval(b0_run_dir)
    constraints = _hard_constraints(evaluation, case_payload)

    result: Dict[str, Any]
    raw_response = ""
    try:
        if constraints.get("render_success") is False or not (run_dir / "pages").exists():
            result = synthetic_v3_failure("candidate did not render successfully", hard_constraints=constraints)
        elif not _has_rendered_pages(b0_run_dir / "pages"):
            comparison = _labeled_candidate_image(
                candidate_pages_dir=run_dir / "pages",
                candidate_method=method,
                max_dim=config.max_image_dim,
            )
            comparison.save(comparison_path)
            prompt = build_v3_rescue_user_prompt(
                method_id=method,
                template_name=str(case_payload.get("template_name") or case_payload.get("venue_id") or case_id.split("/")[0]),
                profile=case_payload.get("profile"),
                target_pages=case_payload.get("target_pages") or evaluation.get("target_pages"),
                requested_perturbations=case_payload.get("requested_perturbations") or [],
                candidate_page_count=evaluation.get("page_count"),
                candidate_compile_success=constraints.get("compile_success"),
                candidate_render_success=constraints.get("render_success"),
                candidate_page_budget_hit=constraints.get("page_budget_hit"),
                candidate_undefined_ref_count=constraints.get("undefined_ref_count"),
            )
            raw_response, usage = client.analyze(
                images=[comparison],
                system_prompt=V3_RESCUE_SYSTEM_PROMPT,
                user_prompt=prompt,
            )
            raw_path.write_text(raw_response, encoding="utf-8")
            parsed, json_parse_mode = extract_json_with_mode(raw_response)
            result = normalize_v3_result(parsed, hard_constraints=constraints)
            result["evaluation_mode"] = "render_rescue_candidate_only"
            result["json_parse_mode"] = json_parse_mode
            result["usage"] = usage
        else:
            comparison = _labeled_comparison_image(
                b0_pages_dir=b0_run_dir / "pages",
                candidate_pages_dir=run_dir / "pages",
                candidate_method=method,
                max_dim=config.max_image_dim,
                swap_order=getattr(config, "swap_pair_order", False),
            )
            comparison.save(comparison_path)
            prompt = build_v3_user_prompt(
                method_id=method,
                template_name=str(case_payload.get("template_name") or case_payload.get("venue_id") or case_id.split("/")[0]),
                profile=case_payload.get("profile"),
                target_pages=case_payload.get("target_pages") or evaluation.get("target_pages"),
                requested_perturbations=case_payload.get("requested_perturbations") or [],
                swap_order=getattr(config, "swap_pair_order", False),
                b0_page_count=b0_evaluation.get("page_count"),
                candidate_page_count=evaluation.get("page_count"),
                candidate_compile_success=constraints.get("compile_success"),
                candidate_render_success=constraints.get("render_success"),
                candidate_page_budget_hit=constraints.get("page_budget_hit"),
                candidate_undefined_ref_count=constraints.get("undefined_ref_count"),
            )
            raw_response, usage = client.analyze(
                images=[comparison],
                system_prompt=V3_SYSTEM_PROMPT,
                user_prompt=prompt,
            )
            raw_path.write_text(raw_response, encoding="utf-8")
            parsed, json_parse_mode = extract_json_with_mode(raw_response)
            result = normalize_v3_result(parsed, hard_constraints=constraints)
            result["json_parse_mode"] = json_parse_mode
            result["usage"] = usage
    except Exception as exc:
        result = synthetic_v3_failure(f"VLM evaluation failed: {exc}", hard_constraints=constraints)
        result["error"] = str(exc)
        if raw_response:
            raw_path.write_text(raw_response, encoding="utf-8")

    report = {
        "method": method,
        "case_id": case_id,
        "generated_at": datetime.now().isoformat(),
        "run_dir": str(run_dir),
        "b0_run_dir": str(b0_run_dir),
        "pair_order": "swapped" if getattr(config, "swap_pair_order", False) else "normal",
        "comparison_image": str(comparison_path) if comparison_path.exists() else None,
        "raw_response_path": str(raw_path) if raw_path.exists() else None,
        "case_context": {
            "venue_id": case_payload.get("venue_id"),
            "profile": case_payload.get("profile"),
            "target_pages": case_payload.get("target_pages"),
            "requested_perturbations": case_payload.get("requested_perturbations") or [],
        },
        "hard_constraints": constraints,
        "vlm": result,
        "vlm_config": {
            **config.public_metadata(),
            "vto_protocol_version": "current",
            "api_key": "REDACTED",
        },
    }
    write_json(report_path, report)
    return report


def _summarize(reports: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_method: Dict[str, List[Dict[str, Any]]] = {}
    for report in reports:
        by_method.setdefault(report["method"], []).append(report)
    rows: List[Dict[str, Any]] = []
    for method in sorted(by_method):
        items = by_method[method]
        scores = [float(item["vlm"]["gated_v3_score"]) for item in items]
        raw_scores = [float(item["vlm"]["raw_v3_score"]) for item in items]
        absolute_scores = [float(item["vlm"]["v3_absolute_score"]) for item in items]
        aesthetic_scores = [
            float(item["vlm"]["final_aesthetic_score"])
            for item in items
            if item["vlm"].get("final_aesthetic_score") is not None
        ]
        repair_scores = [float(item["vlm"]["repair_score"]) for item in items]
        winners = [item["vlm"]["winner"] for item in items]
        rows.append(
            {
                "method": method,
                "case_count": len(items),
                "gated_score_mean": round(sum(scores) / len(scores), 4) if scores else None,
                "raw_score_mean": round(sum(raw_scores) / len(raw_scores), 4) if raw_scores else None,
                "absolute_score_mean": round(sum(absolute_scores) / len(absolute_scores), 4)
                if absolute_scores
                else None,
                "final_aesthetic_score_mean": round(sum(aesthetic_scores) / len(aesthetic_scores), 4)
                if aesthetic_scores
                else None,
                "repair_score_mean": round(sum(repair_scores) / len(repair_scores), 4) if repair_scores else None,
                "candidate_win_rate": round(sum(1 for w in winners if w == "candidate") / len(winners), 4) if winners else None,
                "tie_rate": round(sum(1 for w in winners if w == "tie") / len(winners), 4) if winners else None,
            }
        )
    return rows


def _write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _write_case_csv(path: Path, reports: List[Dict[str, Any]]) -> None:
    rows: List[Dict[str, Any]] = []
    for report in reports:
        v3 = report["vlm"]
        constraints = report["hard_constraints"]
        rows.append(
            {
                "case_id": report["case_id"],
                "method": report["method"],
                "profile": report["case_context"].get("profile"),
                "compile_success": constraints.get("compile_success"),
                "render_success": constraints.get("render_success"),
                "page_budget_hit": constraints.get("page_budget_hit"),
                "page_count": constraints.get("page_count"),
                "target_pages": constraints.get("target_pages"),
                "repair_outcome": v3.get("repair_outcome"),
                "repair_score": v3.get("repair_score"),
                "winner": v3.get("winner"),
                "confidence": v3.get("confidence"),
                "absolute_score": v3.get("v3_absolute_score"),
                "final_aesthetic_score": v3.get("final_aesthetic_score"),
                "raw_score": v3.get("raw_v3_score"),
                "gated_score": v3.get("gated_v3_score"),
                "constraint_gate_reasons": "; ".join(v3.get("constraint_gate_reasons") or []),
                "summary": v3.get("summary"),
            }
        )
    _write_csv(path, rows)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run VLM defect/constraint/pairwise evaluation on a fixed sample.")
    parser.add_argument("--output-dir", default="baseline_result/current_vlm_200case_B0_OURS")
    parser.add_argument("--case-root", default="benchmark_cases")
    parser.add_argument("--sample-size", type=int, default=200, help="Number of cases to evaluate (default: 200)")
    parser.add_argument("--seed", type=int, default=20260426)
    parser.add_argument("--method-root", action="append", default=[], help="Override METHOD=PATH")
    parser.add_argument("--methods", default=None, help="Comma-separated methods to evaluate, e.g. B0,B1,B2,B3,B4")
    parser.add_argument("--evaluate-methods", default=None, help="Comma-separated subset to score after method roots are loaded")
    parser.add_argument("--case-list-file", default=None, help="Evaluate exact case ids listed one per line")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--allow-missing-b0-pages", action="store_true", default=True,
                        help="Include non-renderable B0 cases using render-rescue evaluation (default)")
    parser.add_argument("--require-b0-pages", action="store_false", dest="allow_missing_b0_pages",
                        help="Restrict sampling to cases with successfully rendered B0 pages")
    parser.add_argument("--vlm-api-url", default=None)
    parser.add_argument("--vlm-api-key", default=None)
    parser.add_argument("--vlm-model-name", default=None)
    parser.add_argument("--vlm-api-timeout", type=int, default=240)
    parser.add_argument("--vlm-verify-ssl", choices=["true", "false"], default=None)
    parser.add_argument("--max-image-dim", type=int, default=1800)
    parser.add_argument("--jpeg-quality", type=int, default=78)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument(
        "--swap-pair-order",
        action="store_true",
        help="Place the candidate montage on the LEFT and B0 on the RIGHT (position-bias control; roles stay labeled)",
    )
    args = parser.parse_args(argv)

    method_roots = _filter_methods(_parse_method_root(args.method_root), args.methods)
    if "B0" not in method_roots:
        raise ValueError("B0 method root is required for pairwise VLM evaluation")
    evaluate_methods = _select_methods(method_roots, args.evaluate_methods)
    if args.case_list_file:
        selected_cases = _load_case_list(Path(args.case_list_file))
    else:
        selected_cases = _select_cases(
            method_roots,
            args.sample_size,
            args.seed,
            require_b0_pages=not args.allow_missing_b0_pages,
        )
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    case_root = Path(args.case_root).resolve()

    config = _build_config(args)
    client = OpenAICompatibleVisionJudgeClient(config)

    reports: List[Dict[str, Any]] = []
    for case_id in selected_cases:
        for method in evaluate_methods:
            root = method_roots[method]
            report = _evaluate_one(
                client=client,
                config=config,
                method=method,
                method_root=root,
                b0_root=method_roots["B0"],
                case_root=case_root,
                case_id=case_id,
                output_dir=output_dir,
                force=args.force,
            )
            reports.append(report)
            print(
                f"{method} {case_id}: gated={report['vlm']['gated_v3_score']} "
                f"repair={report['vlm']['repair_score']} winner={report['vlm']['winner']}",
                flush=True,
            )

    summary_rows = _summarize(reports)
    manifest = {
        "generated_at": datetime.now().isoformat(),
        "protocol_version": "v3",
        "swap_pair_order": bool(args.swap_pair_order),
        "sample_size": len(selected_cases),
        "seed": args.seed,
        "selected_cases": selected_cases,
        "method_roots": {method: str(path) for method, path in method_roots.items()},
        "summary": summary_rows,
    }
    write_json(output_dir / "vlm_manifest.json", manifest)
    write_json(output_dir / "vlm_reports.json", reports)
    _write_csv(output_dir / "method_summary.csv", summary_rows)
    _write_case_csv(output_dir / "case_scores.csv", reports)
    print(json.dumps({"selected_cases": selected_cases, "summary": summary_rows}, ensure_ascii=False, indent=2))
    return 0


def _parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return bool(default)
    text = value.strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean value: {value!r}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
