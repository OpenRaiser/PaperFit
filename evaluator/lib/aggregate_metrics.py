#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import random
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, Iterable, List, Sequence, Tuple


CASE_FIELDS = [
    "method",
    "case_id",
    "venue_id",
    "paper_id",
    "profile",
    "column_type",
    "compile_success",
    "render_success",
    "hard_constraint_ok",
    "page_count",
    "target_pages",
    "page_budget_gap",
    "page_budget_gap_abs",
    "page_budget_hit",
    "overfull_count",
    "undefined_ref_count",
    "undefined_citation_count",
    "undefined_control_count",
    "crossref_ok",
    "content_ok",
    "semantic_home_ok",
    "semantic_home_ratio",
    "tex_changed",
    "changed_line_count",
    "changed_line_ratio",
    "vlm_score",
    "vlm_overall_professionalism",
    "vlm_space_utilization",
    "vlm_float_placement_quality",
    "vlm_typographic_consistency",
    "vlm_visual_balance",
    "vlm_prompt_tokens",
    "vlm_completion_tokens",
    "vlm_total_tokens",
    "claude_input_tokens",
    "claude_output_tokens",
    "claude_total_tokens",
    "claude_total_cost_usd",
    "claude_duration_api_ms",
    "claude_num_turns",
]

SUMMARY_NUMERIC_FIELDS = [
    "page_count",
    "target_pages",
    "page_budget_gap",
    "page_budget_gap_abs",
    "overfull_count",
    "undefined_ref_count",
    "undefined_citation_count",
    "undefined_control_count",
    "semantic_home_ratio",
    "changed_line_count",
    "changed_line_ratio",
    "vlm_score",
    "vlm_overall_professionalism",
    "vlm_space_utilization",
    "vlm_float_placement_quality",
    "vlm_typographic_consistency",
    "vlm_visual_balance",
    "vlm_prompt_tokens",
    "vlm_completion_tokens",
    "vlm_total_tokens",
    "claude_input_tokens",
    "claude_output_tokens",
    "claude_total_tokens",
    "claude_total_cost_usd",
    "claude_duration_api_ms",
    "claude_num_turns",
]

SUMMARY_BOOLEAN_FIELDS = [
    "compile_success",
    "render_success",
    "hard_constraint_ok",
    "page_budget_hit",
    "crossref_ok",
    "content_ok",
    "semantic_home_ok",
    "tex_changed",
]


def _parse_method_root(item: str) -> Tuple[str, Path]:
    if "=" not in item:
        raise ValueError(f"expected METHOD=PATH, got: {item}")
    method, raw_path = item.split("=", 1)
    method = method.strip().upper()
    path = Path(raw_path).resolve()
    if not method:
        raise ValueError(f"missing method name in: {item}")
    return method, path


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_case_metadata_from_run_dir(run_dir: Path) -> Dict[str, Any]:
    method_report_path = run_dir / "reports" / "method_report.json"
    if not method_report_path.exists():
        return {}
    method_report = _load_json(method_report_path)
    case_dir = Path((method_report.get("config") or {}).get("case_dir") or "")
    case_json = case_dir / "benchmark_case.json"
    if not case_json.exists():
        return {}
    payload = _load_json(case_json)
    case = payload.get("benchmark_case")
    return case if isinstance(case, dict) else {}


def _discover_method_rows(method: str, root: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for report_path in sorted(root.rglob("reports/evaluation_report.json")):
        run_dir = report_path.parent.parent
        venue_id = run_dir.parent.name
        paper_id = run_dir.name
        payload = _load_json(report_path)
        case_meta = _load_case_metadata_from_run_dir(run_dir)
        vlm_absolute = payload.get("vlm_absolute") or {}
        vlm_meta = payload.get("vlm_absolute_metadata") or {}
        vlm_usage = vlm_meta.get("usage") or {}
        agent_usage = payload.get("agent_usage") or payload.get("codex_usage") or payload.get("claude_usage") or {}
        claude_usage = agent_usage
        claude_usage_core = claude_usage.get("usage") or {}
        rows.append(
            {
                "method": method,
                "case_id": f"{venue_id}/{paper_id}",
                "venue_id": venue_id,
                "paper_id": paper_id,
                "profile": case_meta.get("profile"),
                "column_type": ((case_meta.get("source_layout") or {}).get("column_type")),
                "compile_success": bool(payload.get("compile_success")),
                "render_success": bool(payload.get("render_success")),
                "hard_constraint_ok": payload.get("hard_constraint_ok"),
                "page_count": payload.get("page_count"),
                "target_pages": payload.get("target_pages"),
                "page_budget_gap": payload.get("page_budget_gap"),
                "page_budget_gap_abs": payload.get("page_budget_gap_abs"),
                "page_budget_hit": payload.get("page_budget_hit"),
                "overfull_count": payload.get("overfull_count"),
                "undefined_ref_count": payload.get("undefined_ref_count"),
                "undefined_citation_count": payload.get("undefined_citation_count"),
                "undefined_control_count": payload.get("undefined_control_count"),
                "crossref_ok": (payload.get("crossref_integrity") or {}).get("ok"),
                "content_ok": (payload.get("content_integrity") or {}).get("ok"),
                "semantic_home_ok": (payload.get("semantic_home_hit") or {}).get("ok"),
                "semantic_home_ratio": (payload.get("semantic_home_hit") or {}).get("ratio"),
                "tex_changed": payload.get("tex_changed"),
                "changed_line_count": payload.get("changed_line_count"),
                "changed_line_ratio": payload.get("changed_line_ratio"),
                "vlm_score": vlm_absolute.get("vlm_score"),
                "vlm_overall_professionalism": vlm_absolute.get("overall_professionalism"),
                "vlm_space_utilization": vlm_absolute.get("space_utilization"),
                "vlm_float_placement_quality": vlm_absolute.get("float_placement_quality"),
                "vlm_typographic_consistency": vlm_absolute.get("typographic_consistency"),
                "vlm_visual_balance": vlm_absolute.get("visual_balance"),
                "vlm_prompt_tokens": vlm_usage.get("prompt_tokens"),
                "vlm_completion_tokens": vlm_usage.get("completion_tokens"),
                "vlm_total_tokens": vlm_usage.get("total_tokens"),
                "claude_input_tokens": claude_usage_core.get("input_tokens"),
                "claude_output_tokens": claude_usage_core.get("output_tokens"),
                "claude_total_tokens": claude_usage_core.get("total_tokens"),
                "claude_total_cost_usd": claude_usage.get("total_cost_usd"),
                "claude_duration_api_ms": claude_usage.get("duration_api_ms"),
                "claude_num_turns": claude_usage.get("num_turns"),
                "evaluation_report_path": str(report_path),
            }
        )
    return rows


def _mean(values: Iterable[float]) -> float | None:
    values = list(values)
    if not values:
        return None
    return mean(values)


def _median(values: Iterable[float]) -> float | None:
    values = list(values)
    if not values:
        return None
    return median(values)


def _round_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 4)


def _wilson_ci(successes: int, total: int, z: float = 1.96) -> Tuple[float | None, float | None]:
    """Wilson score 95% CI for a proportion."""
    if total == 0:
        return (None, None)
    phat = successes / total
    denom = 1 + z**2 / total
    centre = (phat + z**2 / (2 * total)) / denom
    margin = z * math.sqrt((phat * (1 - phat) + z**2 / (4 * total)) / total) / denom
    return (round(centre - margin, 4), round(centre + margin, 4))


def _bootstrap_ci(
    values: List[float], n_boot: int = 1000, ci_level: float = 0.95
) -> Tuple[float | None, float | None]:
    """Percentile bootstrap CI."""
    if len(values) < 2:
        return (None, None)
    estimates: List[float] = []
    rng = random.Random(42)  # fixed seed for reproducibility
    for _ in range(n_boot):
        sample = [rng.choice(values) for _ in values]
        estimates.append(statistics.mean(sample))
    alpha = (1 - ci_level) / 2
    sorted_est = sorted(estimates)
    lower_idx = int(alpha * n_boot)
    upper_idx = int((1 - alpha) * n_boot)
    return (round(sorted_est[lower_idx], 4), round(sorted_est[upper_idx], 4))


def _paired_ttest_pvalue(vals_a: List[float], vals_b: List[float]) -> float | None:
    """Two-sided paired t-test. Returns p-value."""
    if len(vals_a) != len(vals_b) or len(vals_a) < 2:
        return None
    n = len(vals_a)
    diffs = [a - b for a, b in zip(vals_a, vals_b)]
    mu_diff = statistics.mean(diffs)
    sd_diff = statistics.stdev(diffs) if n > 1 else 0.0
    if sd_diff == 0:
        return 0.0 if mu_diff == 0 else 1.0
    t_stat = mu_diff / (sd_diff / math.sqrt(n))
    # Two-sided p-value via normal approximation using error function
    p_value = 2 * (1 - 0.5 * (1 + math.erf(abs(t_stat) / math.sqrt(2))))
    return round(max(0.0, min(1.0, p_value)), 4)


def _summarize_method(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {"case_count": len(rows)}
    for field in SUMMARY_BOOLEAN_FIELDS:
        values = [row[field] for row in rows if row.get(field) is not None]
        count = sum(bool(value) for value in values)
        rate = _round_or_none(count / len(values)) if values else None
        total = len(values)
        summary[f"{field}_count"] = count
        summary[f"{field}_rate"] = rate
        # Wilson 95% CI for the rate (plain key)
        lower, upper = _wilson_ci(count, total)
        summary[f"{field}_ci_lower"] = lower
        summary[f"{field}_ci_upper"] = upper
        # Wilson CI also stored under the _rate suffix so _build_comparison_table finds them
        summary[f"{field}_rate_ci_lower"] = lower
        summary[f"{field}_rate_ci_upper"] = upper
    for field in SUMMARY_NUMERIC_FIELDS:
        values = [row[field] for row in rows if isinstance(row.get(field), (int, float))]
        summary[f"{field}_mean"] = _round_or_none(_mean(values))
        summary[f"{field}_median"] = _round_or_none(_median(values))
        # Bootstrap 95% CI for the mean (plain key)
        if len(values) >= 2:
            lower, upper = _bootstrap_ci(values)
            summary[f"{field}_ci_lower"] = lower
            summary[f"{field}_ci_upper"] = upper
        else:
            summary[f"{field}_ci_lower"] = None
            summary[f"{field}_ci_upper"] = None
        # Bootstrap CI also stored under the _mean suffix for _build_comparison_table
        if len(values) >= 2:
            summary[f"{field}_mean_ci_lower"] = summary[f"{field}_ci_lower"]
            summary[f"{field}_mean_ci_upper"] = summary[f"{field}_ci_upper"]
        else:
            summary[f"{field}_mean_ci_lower"] = None
            summary[f"{field}_mean_ci_upper"] = None
    return summary


def _group_rows_by_profile(rows: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("profile") or "unknown")].append(row)
    return dict(grouped)


def _build_comparison_table(
    *,
    summary_by_method: Dict[str, Dict[str, Any]],
    summary_by_method_profile: Dict[str, Dict[str, Dict[str, Any]]],
    methods: Sequence[str],
    output_path: Path,
) -> None:
    base_metrics = [
        "compile_success_rate",
        "render_success_rate",
        "hard_constraint_ok_rate",
        "page_budget_hit_rate",
        "page_budget_gap_abs_mean",
        "undefined_ref_count_mean",
        "undefined_citation_count_mean",
        "changed_line_ratio_mean",
        "changed_line_count_mean",
        "vlm_score_mean",
    ]
    # Build full field list: each base metric gets optional _ci_lower / _ci_upper
    metric_fields: List[str] = []
    for m in base_metrics:
        metric_fields.append(m)
        metric_fields.append(f"{m}_ci_lower")
        metric_fields.append(f"{m}_ci_upper")

    splits = ["overall", "easy", "medium", "hard"]
    rows: List[Dict[str, Any]] = []
    for split in splits:
        for metric in metric_fields:
            row: Dict[str, Any] = {"split": split, "metric": metric}
            for method in methods:
                if split == "overall":
                    value = summary_by_method.get(method, {}).get(metric)
                else:
                    value = summary_by_method_profile.get(method, {}).get(split, {}).get(metric)
                row[method] = value
            rows.append(row)
    fieldnames = ["split", "metric"] + list(methods)
    with output_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def aggregate_metrics(
    *,
    method_roots: Sequence[Tuple[str, Path]],
    output_dir: Path,
) -> Dict[str, Any]:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    all_rows: List[Dict[str, Any]] = []
    summary_by_method: Dict[str, Dict[str, Any]] = {}
    summary_by_method_profile: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for method, root in method_roots:
        rows = _discover_method_rows(method, root)
        all_rows.extend(rows)
        summary_by_method[method] = _summarize_method(rows)
        summary_by_method_profile[method] = {
            profile: _summarize_method(profile_rows)
            for profile, profile_rows in _group_rows_by_profile(rows).items()
        }

    case_csv_path = output_dir / "case_metrics.csv"
    with case_csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CASE_FIELDS)
        writer.writeheader()
        for row in all_rows:
            writer.writerow({key: row.get(key) for key in CASE_FIELDS})

    summary_rows: List[Dict[str, Any]] = []
    all_summary_keys = sorted({key for payload in summary_by_method.values() for key in payload})
    summary_csv_path = output_dir / "method_summary.csv"
    with summary_csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["method"] + all_summary_keys)
        writer.writeheader()
        for method, payload in sorted(summary_by_method.items()):
            row = {"method": method, **payload}
            writer.writerow(row)
            summary_rows.append(row)

    method_profile_summary_csv_path = output_dir / "method_profile_summary.csv"
    profile_summary_keys = sorted(
        {
            key
            for method_payload in summary_by_method_profile.values()
            for profile_payload in method_payload.values()
            for key in profile_payload
        }
    )
    with method_profile_summary_csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["method", "profile"] + profile_summary_keys)
        writer.writeheader()
        for method, profile_map in sorted(summary_by_method_profile.items()):
            for profile, payload in sorted(profile_map.items()):
                writer.writerow({"method": method, "profile": profile, **payload})

    pairwise_rows: List[Dict[str, Any]] = []
    grouped: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
    for row in all_rows:
        grouped[row["case_id"]][row["method"]] = row
    methods = [method for method, _ in method_roots]
    if len(methods) >= 2:
        anchor = methods[0]
        for case_id in sorted(grouped):
            case_map = grouped[case_id]
            if anchor not in case_map:
                continue
            base = case_map[anchor]
            for other in methods[1:]:
                if other not in case_map:
                    continue
                compare = case_map[other]
                pairwise_rows.append(
                    {
                        "case_id": case_id,
                        "anchor_method": anchor,
                        "compare_method": other,
                        "delta_page_budget_gap_abs": _numeric_delta(compare, base, "page_budget_gap_abs"),
                        "delta_overfull_count": _numeric_delta(compare, base, "overfull_count"),
                        "delta_undefined_ref_count": _numeric_delta(compare, base, "undefined_ref_count"),
                        "delta_undefined_citation_count": _numeric_delta(compare, base, "undefined_citation_count"),
                        "delta_changed_line_count": _numeric_delta(compare, base, "changed_line_count"),
                        "delta_changed_line_ratio": _numeric_delta(compare, base, "changed_line_ratio"),
                        "delta_claude_total_tokens": _numeric_delta(compare, base, "claude_total_tokens"),
                        "delta_claude_total_cost_usd": _numeric_delta(compare, base, "claude_total_cost_usd"),
                        "anchor_compile_success": base.get("compile_success"),
                        "compare_compile_success": compare.get("compile_success"),
                        "anchor_render_success": base.get("render_success"),
                        "compare_render_success": compare.get("render_success"),
                        "anchor_hard_constraint_ok": base.get("hard_constraint_ok"),
                        "compare_hard_constraint_ok": compare.get("hard_constraint_ok"),
                        "anchor_tex_changed": base.get("tex_changed"),
                        "compare_tex_changed": compare.get("tex_changed"),
                    }
                )
    pairwise_csv_path = output_dir / "pairwise_vs_anchor.csv"
    if pairwise_rows:
        with pairwise_csv_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(pairwise_rows[0].keys()))
            writer.writeheader()
            writer.writerows(pairwise_rows)

    # ── pairwise significance (paired t-test) ─────────────────────────────────
    significance_metrics = ["hard_constraint_ok", "page_budget_gap_abs", "vlm_score", "compile_success"]
    sig_summary: List[Dict[str, Any]] = []
    if len(methods) >= 2:
        for m_a, m_b in itertools.combinations(methods, 2):
            for metric in significance_metrics:
                vals_a: List[float] = []
                vals_b: List[float] = []
                deltas: List[float] = []
                for case_id in sorted(grouped):
                    case_map = grouped[case_id]
                    row_a = case_map.get(m_a)
                    row_b = case_map.get(m_b)
                    if not row_a or not row_b:
                        continue
                    val_a = row_a.get(metric)
                    val_b = row_b.get(metric)
                    if isinstance(val_a, (int, float)) and isinstance(val_b, (int, float)):
                        vals_a.append(float(val_a))
                        vals_b.append(float(val_b))
                        deltas.append(round(float(val_b) - float(val_a), 4))
                if len(vals_a) >= 2:
                    p = _paired_ttest_pvalue(vals_a, vals_b)
                    wins_a = sum(1 for a, b in zip(vals_a, vals_b) if a > b)
                    wins_b = sum(1 for a, b in zip(vals_a, vals_b) if a < b)
                    sig_summary.append({
                        "method_a": m_a,
                        "method_b": m_b,
                        "metric": metric,
                        "n": len(vals_a),
                        "delta_mean": round(statistics.mean(deltas), 4) if deltas else None,
                        "wins_a": wins_a,
                        "wins_b": wins_b,
                        "p_value": p,
                        "significant_0.05": (p < 0.05) if p is not None else None,
                        "significant_0.01": (p < 0.01) if p is not None else None,
                    })

    sig_csv_path = output_dir / "pairwise_significance.csv"
    if sig_summary:
        sig_fh_fieldnames = list(sig_summary[0].keys())
        with sig_csv_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=sig_fh_fieldnames)
            writer.writeheader()
            writer.writerows(sig_summary)

    comparison_table_csv_path = output_dir / "comparison_table.csv"
    _build_comparison_table(
        summary_by_method=summary_by_method,
        summary_by_method_profile=summary_by_method_profile,
        methods=[method for method, _ in method_roots],
        output_path=comparison_table_csv_path,
    )

    report = {
        "generated_at": datetime.now().isoformat(),
        "methods": [method for method, _ in method_roots],
        "output_dir": str(output_dir),
        "case_metrics_csv": str(case_csv_path),
        "method_summary_csv": str(summary_csv_path),
        "method_profile_summary_csv": str(method_profile_summary_csv_path),
        "comparison_table_csv": str(comparison_table_csv_path),
        "pairwise_vs_anchor_csv": str(pairwise_csv_path) if pairwise_rows else None,
        "pairwise_significance_csv": str(sig_csv_path) if sig_summary else None,
        "summary_by_method": summary_by_method,
        "summary_by_method_profile": summary_by_method_profile,
    }
    (output_dir / "aggregate_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return report


def _numeric_delta(left: Dict[str, Any], right: Dict[str, Any], field: str) -> float | None:
    a = left.get(field)
    b = right.get(field)
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return None
    return round(float(a) - float(b), 4)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Aggregate baseline evaluation metrics into summary tables")
    parser.add_argument(
        "--method-root",
        action="append",
        required=True,
        help="Method root in METHOD=/path/to/run form; may be repeated",
    )
    parser.add_argument("--output-dir", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    method_roots = [_parse_method_root(item) for item in args.method_root]
    report = aggregate_metrics(
        method_roots=method_roots,
        output_dir=Path(args.output_dir),
    )
    print(json.dumps(report["summary_by_method"], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
