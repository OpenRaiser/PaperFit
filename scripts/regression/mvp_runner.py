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
    # B0 冻结用容错口径：数据集源码允许带可恢复错误，PDF 可读即成功
    return compile_latex(project_root, main_tex=main_tex, tolerant=True)


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
    runtime_actions = run_result.get("runtime_actions") or {}
    compile_action = runtime_actions.get("compile") or (runtime_actions.get("initial_observe") or {}).get("compile") or {}
    render_action = runtime_actions.get("render") or (runtime_actions.get("initial_observe") or {}).get("render") or {}
    main_tex = work_dir / main_rel
    if not main_tex.is_file():
        return None
    
    
    
    canonical_compile = None
    try:
        canonical_compile = compile_latex(work_dir, main_tex=main_tex, tolerant=True)
    except Exception:
        canonical_compile = None
    if canonical_compile and canonical_compile.get("success"):
        # 页图随规范编译重渲染，避免导出中间态页面
        try:
            render_pdf_pages(work_dir, pdf_path=work_dir / f"{main_tex.stem}.pdf", output_dir="data/pages")
        except Exception:
            pass
    compile_report = {
        "success": bool((canonical_compile or {}).get("success") or compile_action.get("success")),
        "tex_log_path": str(work_dir / f"{main_tex.stem}.log"),
    }
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
        compile_report=compile_report,
        render_report={"success": bool(render_action.get("success"))},
        case_status=case_status,
    )


def _aggregate_safety(evaluation_reports: List[Dict[str, Any]]) -> Dict[str, Any]:
    "按本侧可评估样本计算的条件比例（不得用于声称配对改进）。"
    if not evaluation_reports:
        return {}
    total = len(evaluation_reports)

    def num(key: str) -> int:
        return sum(1 for report in evaluation_reports if report.get(key))

    content_num = sum(
        1 for report in evaluation_reports if (report.get("content_integrity") or {}).get("ok") is True
    )
    return {
        "compile_success": round(num("compile_success") / total, 4),
        "render_success": round(num("render_success") / total, 4),
        "content_integrity_ok": round(content_num / total, 4),
        "page_budget_hit": round(num("page_budget_hit") / total, 4),
        "_note": "conditional on this side's evaluable samples; NOT a paired comparison",
        "_denominator": total,
        "_numerators": {
            "compile_success": num("compile_success"),
            "render_success": num("render_success"),
            "content_integrity_ok": content_num,
            "page_budget_hit": num("page_budget_hit"),
        },
    }


def _paired_safety(
    b0_evaluations: List[Dict[str, Any]], ours_evaluations: List[Dict[str, Any]]
) -> Dict[str, Any]:
    "双侧都可评估的配对交集指标 + 样本记账。"

    def _index(evals: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for report in evals:
            if not isinstance(report, dict):
                continue
            key = f"{report.get('paper_id')}/{report.get('case_id')}"
            if report.get("paper_id") and report.get("case_id"):
                out[key] = report
        return out

    b0 = _index(b0_evaluations)
    ours = _index(ours_evaluations)
    common = sorted(set(b0) & set(ours))
    metrics: Dict[str, Any] = {}
    for key in ("compile_success", "render_success", "page_budget_hit"):
        v1_hits = [cid for cid in common if b0[cid].get(key)]
        v2_hits = [cid for cid in common if ours[cid].get(key)]
        metrics[key] = {
            "v1": round(len(v1_hits) / len(common), 4) if common else None,
            "v2": round(len(v2_hits) / len(common), 4) if common else None,
            "v1_numerator": len(v1_hits),
            "v2_numerator": len(v2_hits),
            "denominator": len(common),
            "paired_case_ids": common,
        }
    v1_int = sum(1 for cid in common if (b0[cid].get("content_integrity") or {}).get("ok") is True)
    v2_int = sum(1 for cid in common if (ours[cid].get("content_integrity") or {}).get("ok") is True)
    metrics["content_integrity_ok"] = {
        "v1": round(v1_int / len(common), 4) if common else None,
        "v2": round(v2_int / len(common), 4) if common else None,
        "v1_numerator": v1_int,
        "v2_numerator": v2_int,
        "denominator": len(common),
        "paired_case_ids": common,
    }
    return {
        "schema_version": "1.0",
        "paired_evaluable_count": len(common),
        "paired_case_ids": common,
        "b0_only_count": len(set(b0) - set(ours)),
        "b0_only_case_ids": sorted(set(b0) - set(ours)),
        "candidate_only_count": len(set(ours) - set(b0)),
        "candidate_only_case_ids": sorted(set(ours) - set(b0)),
        "metrics": metrics,
    }


def run_mvp(
    *,
    paperfit_data: Path,
    out_root: Path,
    case_count: int = 10,
    max_rounds: int = 2,
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
                
                
                target_pages=benchmark.get("target_pages"),
                template=entry.get("venue_id"),
                column_type=benchmark.get("column_type"),
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
    paired_scores: List[Dict[str, Any]] = []

    # VLM 配置时真正执行 revision pair 评测
    if vlm_configured:
        paired_scores = _run_vlm_revision_pairs(
            methods_root=methods_root,
            run_dir=run_dir,
            case_records=case_records,
        )
    report = build_regression_report(
        reference_version="B0(perturbed)",
        candidate_version="OURS(paperfit HEAD)",
        safety_v1=_aggregate_safety(b0_evaluations),
        safety_paired=_paired_safety(b0_evaluations, ours_evaluations),
        vlm_sample_accounting=_load_json(run_dir / "vlm_sample_accounting.json"),
        safety_v2=_aggregate_safety(ours_evaluations),
        case_records=case_records,
        paired_scores=paired_scores,
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


def _apply_vlm_pair_results(
    *,
    normal_reports: List[Dict[str, Any]],
    swapped_reports: List[Dict[str, Any]],
    common_cases: List[str],
    case_records: List[Dict[str, Any]],
    records_by_case: Dict[tuple, Dict[str, Any]],
    paired: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """把双序 VLM 裁决转换成 delta 样本并回写 case_records。

    - delta = 双序 repair_score 均值（符号恒为 candidate−reference，tie=0），
      绝不用"gated 均值 − 3.0"这类常数冒充分差。
    - 双序冲突/缺裁决/缺分数的 case 是不确定项：剔出 CI 样本，单独返回。
    """
    evaluator_lib = REPO_ROOT / "evaluator" / "lib"
    if str(evaluator_lib) not in sys.path:
        sys.path.insert(0, str(evaluator_lib))
    from pair_symmetry import reconcile_pair_runs

    def _pair_score(report: Dict[str, Any]) -> Optional[float]:
        # repair_score ∈ [-1,3]，符号方向恒为 candidate−reference（tie=0）；
        # gated_v3_score 是绝对分（无符号），不能作为配对差使用
        vlm = report.get("vlm") or {}
        value = vlm.get("repair_score")
        if isinstance(value, (int, float)):
            return float(value)
        return None

    uncertain_cases: List[Dict[str, Any]] = []
    normal_by_case = {r.get("case_id"): r for r in normal_reports}
    swapped_by_case = {r.get("case_id"): r for r in swapped_reports}
    for case_id in common_cases:
        n = normal_by_case.get(case_id, {})
        s = swapped_by_case.get(case_id, {})
        reconciled = reconcile_pair_runs(n, s)
        paper_id, _, arxiv_id = case_id.partition("/")

        normal_score = _pair_score(n)
        swapped_score = _pair_score(s)
        entry = {
            "paper_id": paper_id,
            "case_id": case_id,
            "gated_score_normal": float(((n.get("vlm") or {}).get("gated_v3_score") or 0)),
            "gated_score_swapped": float(((s.get("vlm") or {}).get("gated_v3_score") or 0)),
            "repair_score_normal": normal_score,
            "repair_score_swapped": swapped_score,
            "reconciled": reconciled.get("winner"),
            "agreement": reconciled.get("agreement"),
        }

        # 回写 case_records 使报告 win_counts 正确（复合键 venue + arXiv-id）
        record = records_by_case.get((paper_id, arxiv_id))
        if record is not None:
            record["vlm"] = {
                "reconciled": reconciled.get("winner"),
                "agreement": reconciled.get("agreement"),
                "gated_score": entry["gated_score_normal"],
                "repair_score_normal": normal_score,
                "repair_score_swapped": swapped_score,
            }
        else:
            print(f"VLM write-back miss: no case_record for {case_id}", file=sys.stderr)

        if reconciled.get("agreement") == "agree" and normal_score is not None and swapped_score is not None:
            entry["delta"] = (normal_score + swapped_score) / 2
            paired.append(entry)
        else:
            uncertain_cases.append(entry)
    return uncertain_cases


def _run_vlm_revision_pairs(
    *,
    methods_root: Path,
    run_dir: Path,
    case_records: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """VLM 配置时真正执行 revision pair 评测（正常+换位双序），结果回写 case_records。"""
    import os

    b0_root = methods_root / "B0"
    ours_root = methods_root / "OURS"
    if not b0_root.is_dir() or not ours_root.is_dir():
        return []

    common_cases = []
    for venue in sorted(b0_root.iterdir()):
        if not venue.is_dir():
            continue
        for case_dir in sorted(venue.iterdir()):
            if not case_dir.is_dir():
                continue
            b0_pages = case_dir / "pages"
            ours_pages = ours_root / venue.name / case_dir.name / "pages"
            if b0_pages.is_dir() and any(b0_pages.glob("page_*.png")) and ours_pages.is_dir() and any(ours_pages.glob("page_*.png")):
                common_cases.append(f"{venue.name}/{case_dir.name}")

    if not common_cases:
        return []

    paired: List[Dict[str, Any]] = []
    api_url = os.environ.get("BASELINE_VLM_API_URL", "")
    api_key = os.environ.get("BASELINE_VLM_API_KEY", "")
    model = os.environ.get("BASELINE_VLM_MODEL_NAME", "")

    
    
    records_by_case = {
        (str(r.get("paper_id") or ""), str(r.get("case_id") or "")): r for r in case_records
    }

    try:
        evaluator_dir = REPO_ROOT / "evaluator"
        evaluator_lib = evaluator_dir / "lib"
        for path_entry in (str(evaluator_dir), str(evaluator_lib)):
            if path_entry not in sys.path:
                sys.path.insert(0, path_entry)

        from evaluate_revision_pair import run_revision_evaluation
        from vlm_evaluator import OpenAICompatibleVisionJudgeClient, VLMAbsoluteConfig

        config = VLMAbsoluteConfig(
            api_url=api_url,
            api_key=api_key,
            model_name=model,
            timeout_sec=240,
            protocol_version="v3",
            mode="stitched",
        )
        client = OpenAICompatibleVisionJudgeClient(config)

        normal_summary = run_revision_evaluation(
            client=client,
            reference_root=b0_root,
            candidate_root=ours_root,
            reference_method="B0",
            candidate_method="OURS",
            output_dir=run_dir / "vlm",
            case_ids=common_cases,
            case_root=None,
            max_image_dim=1800,
            swap_order=False,
        )

        swapped_summary = run_revision_evaluation(
            client=client,
            reference_root=b0_root,
            candidate_root=ours_root,
            reference_method="B0",
            candidate_method="OURS",
            output_dir=run_dir / "vlm",
            case_ids=common_cases,
            case_root=None,
            max_image_dim=1800,
            swap_order=True,
        )

        from pair_symmetry import reconcile_pair_runs  # noqa: F401  (保证可导入，实际使用在 _apply_vlm_pair_results)

        uncertain_cases = _apply_vlm_pair_results(
            normal_reports=normal_summary.get("reports", []),
            swapped_reports=swapped_summary.get("reports", []),
            common_cases=common_cases,
            case_records=case_records,
            records_by_case=records_by_case,
            paired=paired,
        )

        print(
            f"VLM revision pairs: {len(paired)} scored, "
            f"{len(uncertain_cases)} uncertain (excluded from CI)"
        )
        
        try:
            (run_dir / "vlm_sample_accounting.json").write_text(
                json.dumps(
                    {
                        "vlm_responses": len(common_cases),
                        "dual_order_consistent_paired": len(paired),
                        "uncertain_excluded": len(uncertain_cases),
                    },
                    ensure_ascii=False,
                    indent=1,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass
    except Exception as exc:
        print(f"VLM evaluation failed: {exc}", file=sys.stderr)

    return paired


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--paperfit-data", required=True, type=Path)
    parser.add_argument("--out", default=Path("regression_runs"), type=Path)
    parser.add_argument("--cases", type=int, default=10)
    parser.add_argument("--max-rounds", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true", help="PaperFit 只诊断不写回（默认 apply）")
    parser.add_argument(
        "--inject",
        action="store_true",
        help="对未扰动源码执行二次注入。默认跳过：paperfit-data 的 raw 源码本身已是扰动 B0"
        "（含 DISTURBANCE 标记），二次注入会叠加扰动并打断 4/10 案例的编译（实测教训）",
    )
    args = parser.parse_args()

    result = run_mvp(
        paperfit_data=args.paperfit_data,
        out_root=args.out,
        case_count=args.cases,
        max_rounds=args.max_rounds,
        apply=not args.dry_run,
        inject=args.inject,
    )
    print(json.dumps({key: value for key, value in result.items() if key != "report"}, ensure_ascii=False, indent=2))
    print(f"report: {result['run_dir']}/reports/regression_report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
