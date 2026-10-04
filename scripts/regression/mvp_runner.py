#!/usr/bin/env python3
"端到端回归编排：冻结 pack → 真实 PaperFit → 双侧导出 → 报告。"

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
for entry in (str(SCRIPTS_DIR.parent), str(REPO_ROOT), str(SCRIPTS_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from regression.funnel import build_funnel  # noqa: E402
from regression.manifest import build_run_manifest, file_sha256  # noqa: E402
from regression.pack_builder import build_pack, find_main_tex  # noqa: E402
from regression.report import build_regression_report, write_regression_report  # noqa: E402
from regression.run_case import run_paperfit_case  # noqa: E402
from runtime_actions import compile_latex, render_pdf_pages  # noqa: E402

SUPPORTED_PERTURBATIONS = ["D1_long_token_overflow", "D2_long_formula", "B2_shrink_graphics", "C1_table_resizebox"]
FALLBACK_PERTURBATION = "D1_long_token_overflow"


def load_cases_metadata(paperfit_data: Path) -> List[Dict[str, Any]]:
    cases = []
    with (paperfit_data / "metadata" / "cases.jsonl").open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def select_cases(cases: List[Dict[str, Any]], *, count: int) -> List[Dict[str, Any]]:
    """按会议轮转抽取，优先体积小的论文（编译快、依赖少）。"""
    by_venue: Dict[str, List[Dict[str, Any]]] = {}
    for case in cases:
        by_venue.setdefault(str(case.get("venue_id") or "?"), []).append(case)
    for venue_cases in by_venue.values():
        venue_cases.sort(key=lambda item: int(item.get("asset_bytes") or 10**9))
    selected: List[Dict[str, Any]] = []
    venues = sorted(by_venue)
    index = 0
    while len(selected) < count:
        progressed = False
        for venue in venues:
            queue = by_venue[venue]
            if index < len(queue) and len(selected) < count:
                selected.append(queue[index])
                progressed = True
                if len(selected) >= count:
                    break
        if not progressed:
            break
        index += 1
    return selected


def choose_perturbation(case: Dict[str, Any], *, used_counts: Dict[str, int]) -> str:
    """从 case 请求的扰动里挑一个我们支持且计数最少的，保证四类各有≥2。"""
    requested = [str(item) for item in (case.get("requested_perturbations") or [])]
    candidates = [item for item in SUPPORTED_PERTURBATIONS if item in requested]
    if not candidates:
        candidates = list(SUPPORTED_PERTURBATIONS)
    return min(candidates, key=lambda item: used_counts.get(item, 0))


def real_compile_fn(project_root: Path) -> Dict[str, Any]:
    main_tex = find_main_tex(project_root)
    if main_tex is None:
        return {"success": False, "reason": "main_tex_not_found"}
    return compile_latex(project_root, main_tex=main_tex)


def real_render_fn(project_root: Path, pages_dir: Path) -> Dict[str, Any]:
    main_tex = find_main_tex(project_root)
    if main_tex is None:
        return {"success": False, "reason": "main_tex_not_found"}
    pdf = project_root / f"{main_tex.stem}.pdf"
    if not pdf.is_file():
        return {"success": False, "reason": "pdf_missing"}
    return render_pdf_pages(project_root, pdf_path=pdf, output_dir=str(pages_dir))


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def export_b0_side(case_dir: Path, method_root: Path, paper_id: str, case_id: str) -> Optional[Dict[str, Any]]:
    """把冻结的 B0 工程导出成评估器 method-root（B0 侧）。"""
    from export_evaluator_inputs import export_case_inputs

    source_manifest = _load_json(case_dir / "source_manifest.json")
    b0_project = case_dir / "b0_project"
    main_rel = source_manifest.get("main_tex_relpath")
    if not b0_project.is_dir() or not main_rel:
        return None
    compile_report = _load_json(case_dir / "compile.json")
    render_report = _load_json(case_dir / "render.json")
    return export_case_inputs(
        project_root=b0_project,
        output_root=method_root,
        paper_id=paper_id,
        case_id=case_id,
        method_id="B0",
        case_dir=case_dir,
        pages_dir=case_dir / "pages",
        main_tex=b0_project / main_rel,
        pdf_path=b0_project / f"{Path(main_rel).stem}.pdf",
        compile_report={"success": bool(compile_report.get("success")), "tex_log_path": str(b0_project / f"{Path(main_rel).stem}.log")},
        render_report={"success": bool(render_report.get("success"))},
        case_status="layout_evaluable" if render_report.get("success") else "input_incomplete",
    )


def export_ours_side(
    run_result_dir: Path,
    work_dir: Path,
    case_dir: Path,
    method_root: Path,
    paper_id: str,
    case_id: str,
    main_rel: str,
    case_status: str,
) -> Optional[Dict[str, Any]]:
    from export_evaluator_inputs import export_case_inputs

    if not work_dir.is_dir():
        return None
    run_result = _load_json(run_result_dir / "run_result.json")
    if not run_result:
        return None
    compile_action = (run_result.get("runtime_actions") or {}).get("compile") or {}
    render_action = (run_result.get("runtime_actions") or {}).get("render") or {}
    main_tex = work_dir / main_rel
    if not main_tex.is_file():
        return None
    return export_case_inputs(
        project_root=work_dir,
        output_root=method_root,
        paper_id=paper_id,
        case_id=case_id,
        method_id="OURS",
        case_dir=case_dir,
        pages_dir=work_dir / "data" / "pages",
        main_tex=main_tex,
        pdf_path=work_dir / f"{main_tex.stem}.pdf",
        compile_report={
            "success": bool(compile_action.get("success")),
            "tex_log_path": str(work_dir / f"{main_tex.stem}.log"),
        },
        render_report={"success": bool(render_action.get("success"))},
        case_status=case_status,
    )


def _aggregate_safety(evaluation_reports: List[Dict[str, Any]]) -> Dict[str, float]:
    if not evaluation_reports:
        return {}
    total = len(evaluation_reports)

    def rate(key: str) -> float:
        values = [1.0 if report.get(key) else 0.0 for report in evaluation_reports]
        return round(sum(values) / total, 4)

    content_ok = [
        1.0 if ((report.get("content_integrity") or {}).get("ok") is True) else 0.0
        for report in evaluation_reports
    ]
    return {
        "compile_success": rate("compile_success"),
        "render_success": rate("render_success"),
        "content_integrity_ok": round(sum(content_ok) / total, 4),
        "page_budget_hit": rate("page_budget_hit"),
    }


def run_mvp(
    *,
    paperfit_data: Path,
    out_root: Path,
    case_count: int = 10,
    max_rounds: int = 1,
    apply: bool = True,
    paperfit_runner=None,
    compile_fn=None,
    render_fn=None,
    inject: bool = True,
) -> Dict[str, Any]:
    paperfit_data = paperfit_data.resolve()
    out_root = out_root.resolve()
    run_id = f"MVP1_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir = out_root / run_id
    pack_root = run_dir / "benchmark_pack"
    methods_root = run_dir / "methods"

    cases_metadata = load_cases_metadata(paperfit_data)
    selected = select_cases(cases_metadata, count=case_count)

    used_counts: Dict[str, int] = {}
    entries: List[Dict[str, Any]] = []
    for case in selected:
        archive = paperfit_data / "raw" / str(case["venue_id"]) / f"{case['paper_id']}.tar.gz"
        if not archive.is_file():
            continue
        perturbation = choose_perturbation(case, used_counts=used_counts)
        used_counts[perturbation] = used_counts.get(perturbation, 0) + 1
        entries.append(
            {
                "archive": str(archive),
                "paper_id": str(case["venue_id"]),
                "case_id": str(case["paper_id"]),
                "venue_id": str(case["venue_id"]),
                "perturbation_ids": [perturbation],
                "seed": int(case.get("seed") or 0) % (2**31),
                "target_pages": case.get("target_pages"),
                "profile": case.get("profile"),
                "column_type": case.get("column_type"),
            }
        )

    compile_fn = compile_fn or real_compile_fn
    render_fn = render_fn or real_render_fn
    pack_manifest_path = pack_root / "pack_manifest.json"
    if pack_manifest_path.is_file() and len((pack_root.glob("*/*/pages"))) > 0:
        # 已有冻结 pack：直接复用（B0 只生成一次的原则），不重新编译
        pack_manifest = json.loads(pack_manifest_path.read_text(encoding="utf-8"))
    else:
        pack_manifest = build_pack(
            entries=entries,
            pack_root=pack_root,
            compile_fn=compile_fn,
            render_fn=render_fn,
            skip_injection=not inject,
        )

    case_records: List[Dict[str, Any]] = []
    b0_evaluations: List[Dict[str, Any]] = []
    ours_evaluations: List[Dict[str, Any]] = []
    applied_any = 0

    for entry, pack_result in zip(entries, pack_manifest["case_results"]):
        case_id = entry["case_id"]
        paper_id = entry["paper_id"]
        case_dir = pack_root / paper_id / case_id
        source_manifest = _load_json(case_dir / "source_manifest.json")
        main_rel = source_manifest.get("main_tex_relpath")
        benchmark = (_load_json(case_dir / "benchmark_case.json").get("benchmark_case") or {})
        record: Dict[str, Any] = {
            "case_id": case_id,
            "paper_id": paper_id,
            "perturbation_types": entry["perturbation_ids"],
            "column_type": benchmark.get("column_type"),
            "profile": benchmark.get("profile"),
            "assigned": True,
            "injection_status": (
                "skipped_by_design"
                if not inject
                else pack_result["injection_status"] if pack_result["status"] != "injection_failed" else (pack_result.get("injection_status") or "failed")
            ),
            "b0_status": "layout_evaluable" if pack_result["status"] == "success" else "input_incomplete",
            "candidate_status": "not_run",
            "environment_state": "clean",
            "vlm": {"reconciled": None},
        }

        if pack_result["status"] == "success" and main_rel:
            b0_export = export_b0_side(case_dir, methods_root / "B0", paper_id, case_id)
            if b0_export:
                b0_evaluations.append(b0_export["report"])
            run = run_paperfit_case(
                b0_project=case_dir / "b0_project",
                work_root=run_dir / "ours_runs",
                case_id=case_id,
                paper_id=paper_id,
                main_tex_relpath=main_rel,
                runner=paperfit_runner,
                max_rounds=max_rounds,
                apply=apply,
            )
            record["candidate_status"] = run.status
            if run.status == "guard_rolled_back":
                # 修复被硬门禁拦截并已回滚：源码等价 B0，工作区页图是回滚前渲染，
                # 导出它们会失真——如实记录，不导出 OURS 侧
                record["environment_state"] = "guard_rolled_back"
            elif run.status != "not_run" and run.status != "runner_crash":
                ours_export = export_ours_side(
                    run_result_dir=run.work_dir / "data" if run.work_dir else run_dir,
                    work_dir=run.work_dir,
                    case_dir=case_dir,
                    method_root=methods_root / "OURS",
                    paper_id=paper_id,
                    case_id=case_id,
                    main_rel=main_rel,
                    case_status=run.status,
                )
                if ours_export:
                    ours_evaluations.append(ours_export["report"])
                    if run.status == "layout_evaluable":
                        applied_any += 1
            if run.status == "runner_crash":
                record["environment_state"] = "runner_crash"
        elif pack_result["status"] in {"compile_failed", "render_failed"}:
            record["b0_status"] = "environment_ineligible" if pack_result["status"] == "compile_failed" else "input_incomplete"
            record["environment_state"] = pack_result["status"]

        case_records.append(record)

    vlm_configured = _vlm_configured()
    report = build_regression_report(
        reference_version="B0(perturbed)",
        candidate_version="OURS(paperfit HEAD)",
        safety_v1=_aggregate_safety(b0_evaluations),
        safety_v2=_aggregate_safety(ours_evaluations),
        case_records=case_records,
        paired_scores=[] if not vlm_configured else [],
        absolute_vs_b0=None,
        manifest_compatible=True,
    )
    if not vlm_configured:
        report["decision"] = {
            "decision": "INCONCLUSIVE",
            "reasons": ["VLM judge not configured (BASELINE_VLM_* env missing); visual layer not evaluated"],
        }
        report["conclusion_eligibility"]["note"] = "视觉层未评测（裁判未配置），本报告只覆盖安全与覆盖率层"

    manifest = build_run_manifest(
        project_root=REPO_ROOT,
        run_id=run_id,
        reference_version="B0",
        candidate_version="OURS",
        dataset_files=[paperfit_data / "metadata" / "cases.jsonl"],
        benchmark_pack_hash=None,
        injector_version=pack_manifest.get("injector_version"),
        judge_model_name=None,
    )
    manifest["pack_summary"] = pack_manifest["summary"]
    manifest["applied_cases"] = applied_any

    (run_dir / "reports").mkdir(parents=True, exist_ok=True)
    (run_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (run_dir / "case_records.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in case_records) + "\n", encoding="utf-8"
    )
    write_regression_report(report, run_dir / "reports")
    return {"run_dir": str(run_dir), "pack_summary": pack_manifest["summary"], "decision": report["decision"]["decision"], "report": report}


def _vlm_configured() -> bool:
    import os

    return bool(os.environ.get("BASELINE_VLM_API_URL") and os.environ.get("BASELINE_VLM_API_KEY") and os.environ.get("BASELINE_VLM_MODEL_NAME"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--paperfit-data", required=True, type=Path)
    parser.add_argument("--out", default=Path("regression_runs"), type=Path)
    parser.add_argument("--cases", type=int, default=10)
    parser.add_argument("--max-rounds", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true", help="PaperFit 只诊断不写回（默认 apply）")
    parser.add_argument(
        "--no-inject",
        action="store_true",
        help="数据集已是扰动 B0（paperfit-data 即如此）：跳过二次注入，原样冻结",
    )
    args = parser.parse_args()

    result = run_mvp(
        paperfit_data=args.paperfit_data,
        out_root=args.out,
        case_count=args.cases,
        max_rounds=args.max_rounds,
        apply=not args.dry_run,
        inject=not args.no_inject,
    )
    print(json.dumps({key: value for key, value in result.items() if key != "report"}, ensure_ascii=False, indent=2))
    print(f"report: {result['run_dir']}/reports/regression_report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
