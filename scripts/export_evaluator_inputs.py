#!/usr/bin/env python3
"把 PaperFit 运行产物导出成官方评估器（evaluator/）的 method-root 输入布局。"

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
EVALUATOR_LIB = SCRIPTS_DIR.parent / "evaluator" / "lib"

sys.path.insert(0, str(EVALUATOR_LIB))

from evaluators import basic_evaluation  # noqa: E402


def _derive_compile_report(project_root: Path, main_tex: Path, pdf_path: Optional[Path]) -> Dict[str, Any]:
    """从运行产物推断编译报告；推断不出 success 时保守为 False。"""
    log_candidates = [
        project_root / f"{main_tex.stem}.log",
        project_root / "data" / "logs" / "paperfit_compile.log",
    ]
    log_path = next((candidate for candidate in log_candidates if candidate.is_file()), None)
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path else ""
    fatal = any(marker in log_text for marker in ("Fatal error", "Emergency stop", "! LaTeX Error:"))
    success = bool(pdf_path and pdf_path.is_file()) and bool(log_path) and not fatal
    return {"success": success, "tex_log_path": str(log_path) if log_path else None}


def _derive_render_report(pages_dir: Path) -> Dict[str, Any]:
    pages = sorted(pages_dir.glob("page_*.png")) if pages_dir.is_dir() else []
    return {"success": bool(pages), "page_count": len(pages)}


def export_case_inputs(
    *,
    project_root: Path,
    output_root: Path,
    paper_id: str,
    case_id: str,
    method_id: str = "OURS",
    case_dir: Optional[Path] = None,
    pages_dir: Optional[Path] = None,
    main_tex: Optional[Path] = None,
    pdf_path: Optional[Path] = None,
    compile_report: Optional[Dict[str, Any]] = None,
    render_report: Optional[Dict[str, Any]] = None,
    case_status: Optional[str] = None,
) -> Dict[str, Any]:
    project_root = project_root.resolve()
    main_tex = (main_tex or project_root / "main.tex").resolve()
    if not main_tex.is_file():
        raise FileNotFoundError(f"main tex not found: {main_tex}")
    pages_dir = (pages_dir or project_root / "data" / "pages").resolve()
    pdf_path = (pdf_path or project_root / f"{main_tex.stem}.pdf").resolve()
    pdf_path = pdf_path if pdf_path.is_file() else None

    derived_render = _derive_render_report(pages_dir)
    render = render_report if render_report is not None else derived_render
    compile_rep = compile_report if compile_report is not None else _derive_compile_report(
        project_root, main_tex, pdf_path
    )

    # 资格分类（与 runtime 的 case_status 同词表）：未显式给出时按渲染可评测性推断
    if case_status is None:
        case_status = "layout_evaluable" if render.get("success") else "input_incomplete"

    report = basic_evaluation(
        method_id=method_id,
        main_tex=main_tex,
        pdf_path=pdf_path,
        compile_report=compile_rep,
        render_report=render,
        case_dir=case_dir.resolve() if case_dir else None,
    )
    
    report["paper_id"] = paper_id
    report["case_id"] = case_id
    report["case_status"] = case_status
    # 官方快照不含论文 Program Score 的 content_embedding_similarity 维：
    # 如实标注缺失，不用自选 embedding 模型冒充论文复现。
    report["program_score_dimensions"] = {
        "available_signals": [
            "compile_success",
            "page_budget_hit",
            "undefined_ref_count",
            "undefined_citation_count",
            "undefined_control_count",
            "content_integrity",
            "semantic_home_hit",
            "changed_line_count",
        ],
        "embedding_similarity": "not_available_in_official_snapshot",
    }

    case_out = output_root.resolve() / paper_id / case_id
    pages_out = case_out / "pages"
    if pages_out.exists():
        shutil.rmtree(pages_out)
    pages_out.mkdir(parents=True, exist_ok=True)
    if render.get("success"):
        for page in sorted(pages_dir.glob("page_*.png")):
            shutil.copy2(page, pages_out / page.name)

    from common_harness import write_json  # noqa: E402  (evaluator helper)

    write_json(case_out / "reports" / "evaluation_report.json", report)
    return {
        "paper_id": paper_id,
        "case_id": case_id,
        "method_id": method_id,
        "case_dir": str(case_out),
        "pages_copied": len(list(pages_out.glob("page_*.png"))),
        "case_status": case_status,
        "report": report,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project-root", default=".", help="PaperFit 运行所在的论文项目根")
    parser.add_argument("--output-root", required=True, help="评估器 method-root（如 runs/OURS）")
    parser.add_argument("--paper-id", required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--method-id", default="OURS")
    parser.add_argument("--case-dir", default=None, help="含 benchmark_case.json 与扰动源 main.tex 的原始 case 目录")
    parser.add_argument("--pages-dir", default=None)
    parser.add_argument("--main-tex", default=None)
    parser.add_argument("--pdf", default=None)
    parser.add_argument("--case-status", default=None, help="eligible/environment_ineligible/input_incomplete/source_compile_error/layout_evaluable")
    args = parser.parse_args()

    summary = export_case_inputs(
        project_root=Path(args.project_root),
        output_root=Path(args.output_root),
        paper_id=args.paper_id,
        case_id=args.case_id,
        method_id=args.method_id,
        case_dir=Path(args.case_dir) if args.case_dir else None,
        pages_dir=Path(args.pages_dir) if args.pages_dir else None,
        main_tex=Path(args.main_tex) if args.main_tex else None,
        pdf_path=Path(args.pdf) if args.pdf else None,
        case_status=args.case_status,
    )
    summary.pop("report")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
