#!/usr/bin/env python3
"""单 case 的 PaperFit 机器调用封装（回归 runner 的被测引擎入口）。

复用 typed runtime（orchestrator_runtime.py run-task）：一次调用完成
快照→编译→渲染→诊断→修复→复验，产物（state.json / run_result.json /
data/pages）全部落在工作目录，供后续导出与评测消费。

paperfit_runner 可注入（测试用假实现），真实实现走子进程。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ORCHESTRATOR = REPO_ROOT / "scripts" / "orchestrator_runtime.py"

PaperFitRunner = Callable[[Path, Path, Path], Dict[str, Any]]


@dataclass
class CaseRunResult:
    case_id: str
    paper_id: str
    status: str  # layout_evaluable / environment_ineligible / input_incomplete / source_compile_error / runner_crash / not_run
    reasons: List[str] = field(default_factory=list)
    work_dir: Optional[Path] = None
    run_result: Dict[str, Any] = field(default_factory=dict)


def build_task_spec(
    *,
    project_root: Path,
    main_tex_relpath: str,
    max_rounds: int = 2,
    apply: bool = True,
) -> Dict[str, Any]:
    return {
        "schema_version": "1.0",
        "task_type": "full_vto",
        "task_id": None,
        "project_root": str(project_root.resolve()),
        "main_tex": main_tex_relpath,
        "template": None,
        "target_pages": None,
        "page_budget_scope": None,
        "page_dir": "data/pages",
        "log_file": None,
        "column_void_report": None,
        "allow_source_mutation": True,
        "pre_repair_snapshot_required": True,
        "dry_run_source_mutation": not apply,
        "rollback_policy": "required",
        "strict_mode": False,
        "max_rounds": max_rounds,
        "user_request": None,
        "required_phases": [],
    }


def default_paperfit_runner(
    project_root: Path,
    orchestrator: Path,
    python_exe: str,
) -> PaperFitRunner:
    def _run(project: Path, task_path: Path, output_path: Path) -> Dict[str, Any]:
        completed = subprocess.run(
            [python_exe, str(orchestrator), "run-task", str(task_path), "--output", str(output_path)],
            cwd=str(project),
            capture_output=True,
            text=True,
            timeout=1800,
        )
        if output_path.is_file():
            return json.loads(output_path.read_text(encoding="utf-8"))
        return {
            "status": "runner_crash",
            "failure": {
                "failure_type": "paperfit_runner_exit",
                "reason": f"returncode={completed.returncode}",
                "stderr_tail": (completed.stderr or "")[-2000:],
            },
        }

    return _run


def run_paperfit_case(
    *,
    b0_project: Path,
    work_root: Path,
    case_id: str,
    paper_id: str,
    main_tex_relpath: str,
    runner: Optional[PaperFitRunner] = None,
    max_rounds: int = 2,
    apply: bool = True,
) -> CaseRunResult:
    result = CaseRunResult(case_id=case_id, paper_id=paper_id, status="not_run")
    work_dir = work_root / paper_id / case_id / "ours_project"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)

    # 拷贝冻结的 B0 工程作为被测输入（B0 原件不可动）
    shutil.copytree(b0_project, work_dir, dirs_exist_ok=True)

    task_spec = build_task_spec(
        project_root=work_dir,
        main_tex_relpath=main_tex_relpath,
        max_rounds=max_rounds,
        apply=apply,
    )
    data_dir = work_dir / "data"
    data_dir.mkdir(exist_ok=True)
    task_path = data_dir / "task.json"
    output_path = data_dir / "run_result.json"
    task_path.write_text(json.dumps(task_spec, indent=2, ensure_ascii=False), encoding="utf-8")

    if runner is None:
        runner = default_paperfit_runner(work_dir, ORCHESTRATOR, sys.executable)

    try:
        run_payload = runner(work_dir, task_path, output_path)
    except Exception as exc:  # runner 崩溃也算 case 状态，不许中断整个回归
        run_payload = {"status": "runner_crash", "failure": {"failure_type": "runner_exception", "reason": str(exc)}}

    result.work_dir = work_dir
    result.run_result = run_payload
    result.status = _derive_status(run_payload)
    if result.status != "layout_evaluable":
        failure = run_payload.get("failure") or {}
        reason = str(failure.get("reason") or failure.get("failure_type") or run_payload.get("status") or "")
        result.reasons.append(f"{failure.get('failure_type') or 'status'}: {reason}" if reason else str(run_payload.get("status")))
    return result


def _derive_status(run_payload: Dict[str, Any]) -> str:
    if run_payload.get("status") == "runner_crash":
        return "runner_crash"
    compile_action = ((run_payload.get("runtime_actions") or {}).get("compile") or {})
    if not compile_action.get("success"):
        case_status = str(compile_action.get("case_status") or "")
        if case_status in {"environment_ineligible", "input_incomplete", "source_compile_error"}:
            return case_status
        return "source_compile_error"
    render_action = ((run_payload.get("runtime_actions") or {}).get("render") or {})
    if not render_action.get("success"):
        return "input_incomplete"
    return "layout_evaluable"
