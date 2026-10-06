#!/usr/bin/env python3
"单 case 的 PaperFit 机器调用封装（回归 runner 的被测引擎入口）。"

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ORCHESTRATOR = REPO_ROOT / "scripts" / "orchestrator_runtime.py"

PaperFitRunner = Callable[[Path, Path, Path], Dict[str, Any]]

RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_CRASH = "runner_crash"
RUN_STATUS_NOT_RUN = "not_run"

RENDER_STATUS_RENDERED = "rendered"
RENDER_STATUS_FAILED = "failed"

REPAIR_EFFECT_ACCEPTED = "accepted"
REPAIR_EFFECT_NO_EFFECT = "no_effect"
REPAIR_EFFECT_GUARD_ROLLED_BACK = "guard_rolled_back"
REPAIR_EFFECT_BLOCKED = "blocked"
REPAIR_EFFECT_APPROVAL_REQUIRED = "approval_required"
REPAIR_EFFECT_COMPILE_FAILED = "compile_failed"
# payload 无任何修复执行痕迹（旧假实现 / 极早期 payload）时的诚实值：
# 整体 status 维持旧口径 layout_evaluable，但 repair_effect 列不冒充 accepted
REPAIR_EFFECT_UNKNOWN = "unknown"
REPAIR_EFFECT_NOT_RUN = "not_run"

RUNNER_TIMEOUT_SEC = 1800
REPAIR_EXECUTION_REPORT_RELPATH = Path("data") / "repair_execution_report.json"

# 编译失败时 runtime 语义化的 case_status（整词表见 runtime_compile_case_status）
COMPILE_CASE_STATUSES = {"environment_ineligible", "input_incomplete", "source_compile_error"}


@dataclass
class CaseRunResult:
    case_id: str
    paper_id: str
    # 兼容聚合列：layout_evaluable / no_effect / guard_rolled_back / approval_required /
    # blocked / environment_ineligible / input_incomplete / source_compile_error /
    # runner_crash / not_run
    status: str
    reasons: List[str] = field(default_factory=list)
    work_dir: Optional[Path] = None
    run_result: Dict[str, Any] = field(default_factory=dict)
    
    run_status: str = RUN_STATUS_NOT_RUN
    candidate_render_status: str = RENDER_STATUS_FAILED
    repair_effect_status: str = REPAIR_EFFECT_NOT_RUN


def build_task_spec(
    *,
    project_root: Path,
    main_tex_relpath: str,
    max_rounds: int = 2,
    apply: bool = True,
    target_pages: Optional[int] = None,
    template: Optional[str] = None,
    column_type: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "schema_version": "1.0",
        "task_type": "full_vto",
        "task_id": None,
        "project_root": str(project_root.resolve()),
        "main_tex": main_tex_relpath,
        "template": template,
        "target_pages": target_pages,
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


def _coerce_output_text(value: Any) -> str:
    """子进程输出统一成可读 str（无输出给空串，绝不让 None 流入下游）。"""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return ""


def _read_repair_execution_summary(project: Path, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    "读工作目录里的 repair_execution_report.json，归一成执行摘要。"
    candidates: List[Path] = []
    artifacts = payload.get("artifacts")
    if isinstance(artifacts, dict):
        rel = artifacts.get("repair_execution_report")
        if isinstance(rel, str) and rel:
            candidates.append(project / rel)
    candidates.append(project / REPAIR_EXECUTION_REPORT_RELPATH)
    for path in candidates:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        try:
            source = str(path.resolve().relative_to(project.resolve()))
        except ValueError:
            source = str(path)
        applied = int(data.get("applied_count") or 0)
        
        
        
        status = str(data.get("status") or "")
        run_result = payload if isinstance(payload, dict) else {}
        totals = run_result.get("execution_totals")
        records = run_result.get("round_execution_records")
        if isinstance(records, list) and records:
            if not isinstance(totals, dict):
                totals = {}
            try:
                acc_total = int(totals.get("accepted")) if isinstance(totals.get("accepted"), int) else sum(
                    int(r.get("accepted") or 0) for r in records if isinstance(r, dict)
                )
                appl_total = int(totals.get("applied")) if isinstance(totals.get("applied"), int) else sum(
                    int(r.get("applied") or 0) for r in records if isinstance(r, dict)
                )
            except (TypeError, ValueError):
                acc_total, appl_total = 0, 0
            if acc_total > 0 or appl_total > 0:
                applied = appl_total
                status = str((records[-1] if isinstance(records[-1], dict) else {}).get("status") or "success")
        return {
            "status": status,
            "applied_count": applied,
            "accepted_count": int(data.get("accepted_count") or 0),
            "rejected_count": int(data.get("rejected_count") or 0),
            "rolled_back_count": int(data.get("rolled_back_count") or 0),
            "source": source,
        }
    return None


def default_paperfit_runner(
    project_root: Path,
    orchestrator: Path,
    python_exe: str,
) -> PaperFitRunner:
    def _run(project: Path, task_path: Path, output_path: Path) -> Dict[str, Any]:
        command = [
            python_exe,
            str(orchestrator),
            "run-task",
            str(task_path),
            "--output",
            str(output_path),
        ]
        started = time.monotonic()
        timed_out = False
        try:
            completed = subprocess.run(
                command,
                cwd=str(project),
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=RUNNER_TIMEOUT_SEC,
            )
            returncode = int(completed.returncode)
            stdout = _coerce_output_text(completed.stdout)
            stderr = _coerce_output_text(completed.stderr)
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            returncode = -1
            stdout = _coerce_output_text(exc.stdout)
            stderr = _coerce_output_text(exc.stderr)
        duration_sec = round(time.monotonic() - started, 3)

        payload: Optional[Dict[str, Any]] = None
        if output_path.is_file():
            try:
                loaded = json.loads(output_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                loaded = None
            if isinstance(loaded, dict):
                payload = loaded
        if payload is None:
            reason = f"timeout after {RUNNER_TIMEOUT_SEC}s" if timed_out else f"returncode={returncode}"
            payload = {
                "status": "runner_crash",
                "failure": {
                    "failure_type": "paperfit_runner_exit",
                    "reason": reason,
                    "stderr_tail": stderr[-2000:],
                },
            }

        
        
        payload["paperfit_process"] = {
            "command": command,
            "cwd": str(project),
            "returncode": returncode,
            "duration_sec": duration_sec,
            "timed_out": timed_out,
            "stdout": stdout,
            "stderr": stderr,
        }
        # 退出码 0 也要读 execution report：真实 run_result.json 顶层不带
        # repair_execution_summary，no_effect 只能从执行报告本体判定
        if payload.get("repair_execution_summary") is None:
            summary = _read_repair_execution_summary(project, payload)
            if summary is not None:
                payload["repair_execution_summary"] = summary
        return payload

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
    target_pages: Optional[int] = None,
    template: Optional[str] = None,
    column_type: Optional[str] = None,
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
        target_pages=target_pages,
        template=template,
        column_type=column_type,
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
    states = _derive_case_states(run_payload)
    result.status = states["status"]
    result.run_status = states["run_status"]
    result.candidate_render_status = states["candidate_render_status"]
    result.repair_effect_status = states["repair_effect_status"]
    if result.status != "layout_evaluable":
        if result.status == "no_effect":
            summary = _execution_summary(run_payload)
            result.reasons.append(
                "repair_no_effect: "
                f"repair_execution_summary status={summary.get('status')!r}, "
                f"applied_count={int(summary.get('applied_count') or 0)} (candidate source equals B0)"
            )
        else:
            failure = run_payload.get("failure") or {}
            reason = str(failure.get("reason") or failure.get("failure_type") or run_payload.get("status") or "")
            result.reasons.append(
                f"{failure.get('failure_type') or 'status'}: {reason}" if reason else str(run_payload.get("status"))
            )
    return result


def _compile_action(run_payload: Dict[str, Any]) -> Dict[str, Any]:
    runtime_actions = run_payload.get("runtime_actions") or {}
    compile_action = runtime_actions.get("compile") or {}
    if not compile_action:
        # typed runtime 把编译动作嵌在 initial_observe 里
        initial_observe = runtime_actions.get("initial_observe") or {}
        compile_action = initial_observe.get("compile") or {}
    return compile_action if isinstance(compile_action, dict) else {}


def _render_action(run_payload: Dict[str, Any]) -> Dict[str, Any]:
    runtime_actions = run_payload.get("runtime_actions") or {}
    render_action = runtime_actions.get("render") or {}
    if not render_action:
        initial_observe = runtime_actions.get("initial_observe") or {}
        render_action = initial_observe.get("render") or {}
    return render_action if isinstance(render_action, dict) else {}


def _execution_summary(run_payload: Dict[str, Any]) -> Dict[str, Any]:
    "归一读取修复执行摘要（）。"
    summary = run_payload.get("repair_execution_summary")
    if isinstance(summary, dict) and summary.get("status") is not None:
        return summary
    records = run_payload.get("round_execution_records")
    if isinstance(records, list):
        rounds = [item for item in records if isinstance(item, dict)]
        if rounds:
            totals = run_payload.get("execution_totals")
            if not isinstance(totals, dict):
                totals = {}
            applied = totals.get("applied")
            applied_total = int(applied) if isinstance(applied, int) else sum(int(r.get("applied") or 0) for r in rounds)
            accepted = totals.get("accepted")
            accepted_total = (
                int(accepted) if isinstance(accepted, int) else sum(int(r.get("accepted") or 0) for r in rounds)
            )
            return {
                # applied 总数为 0 时整轮零有效修改（noop 或全回滚），统一按 noop 口径
                "status": "noop" if applied_total == 0 else str(rounds[-1].get("status") or "success"),
                "applied_count": applied_total,
                "accepted_count": accepted_total,
            }
    executor = (run_payload.get("runtime_actions") or {}).get("repair_plan_executor")
    if isinstance(executor, dict) and executor.get("status") is not None:
        return {
            "status": executor.get("status"),
            "applied_count": int(executor.get("applied_count") or 0),
        }
    return {}


def _classify_repair_effect(
    run_payload: Dict[str, Any],
    *,
    failure_type: str,
    compile_ok: bool,
) -> str:
    """修复效果三列判定（与渲染成败正交）。"""
    # 安全系统正确拦截：修复触发硬门禁并已回滚，源码等价于 B0
    if failure_type == "post_repair_hard_guard_failed":
        return REPAIR_EFFECT_GUARD_ROLLED_BACK
    if not compile_ok:
        return REPAIR_EFFECT_COMPILE_FAILED
    run_status = str(run_payload.get("status") or "")
    if run_status == "approval_required" or failure_type == "approval_required":
        return REPAIR_EFFECT_APPROVAL_REQUIRED
    if run_status == "blocked":
        return REPAIR_EFFECT_BLOCKED
    summary = _execution_summary(run_payload)
    if not summary:
        return REPAIR_EFFECT_UNKNOWN
    if int(summary.get("applied_count") or 0) > 0:
        return REPAIR_EFFECT_ACCEPTED
    # status=noop（或 failed 后全部回滚）且 applied=0：候选源码等价 B0
    return REPAIR_EFFECT_NO_EFFECT


def _derive_case_states(run_payload: Dict[str, Any]) -> Dict[str, str]:
    """从 run payload 派生三列状态 + 兼容聚合 status。"""
    if run_payload.get("status") == "runner_crash":
        return {
            "status": "runner_crash",
            "run_status": RUN_STATUS_CRASH,
            "candidate_render_status": RENDER_STATUS_FAILED,
            "repair_effect_status": REPAIR_EFFECT_UNKNOWN,
        }
    failure = run_payload.get("failure") or {}
    failure_type = str(failure.get("failure_type") or "")
    compile_ok = bool(_compile_action(run_payload).get("success"))
    render_ok = bool(_render_action(run_payload).get("success"))
    effect = _classify_repair_effect(run_payload, failure_type=failure_type, compile_ok=compile_ok)

    if failure_type == "post_repair_hard_guard_failed":
        # 即使 PDF 可渲染也是回滚态：修复没有存活，不许进可评估桶
        status = "guard_rolled_back"
    elif not compile_ok:
        compile_action = _compile_action(run_payload)
        case_status = str(compile_action.get("case_status") or "")
        if case_status in COMPILE_CASE_STATUSES:
            status = case_status
        elif effect == REPAIR_EFFECT_APPROVAL_REQUIRED:
            status = "approval_required"
        else:
            status = "source_compile_error"
    elif not render_ok:
        status = "input_incomplete"
    elif effect == REPAIR_EFFECT_NO_EFFECT:
        status = "no_effect"
    elif effect == REPAIR_EFFECT_APPROVAL_REQUIRED:
        status = "approval_required"
    elif effect == REPAIR_EFFECT_BLOCKED:
        status = "blocked"
    else:
        status = "layout_evaluable"

    return {
        "status": status,
        "run_status": RUN_STATUS_COMPLETED,
        "candidate_render_status": RENDER_STATUS_RENDERED if (compile_ok and render_ok) else RENDER_STATUS_FAILED,
        "repair_effect_status": effect,
    }


def _derive_status(run_payload: Dict[str, Any]) -> str:
    """兼容入口：只取聚合 status（三列版见 _derive_case_states）。"""
    return _derive_case_states(run_payload)["status"]
