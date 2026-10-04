#!/usr/bin/env python3
"""Run repair workflows for the configured representative benchmark cases."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_ROOT = PACKAGE_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from paperfit_command import _detect_main_tex, _handle_paperfit_request, _resolve_template_key  # noqa: E402
from runtime_status import build_runtime_status  # noqa: E402


DEFAULT_CONFIG = PACKAGE_ROOT / "config" / "benchmark_representatives.json"
DEFAULT_REQUEST = "repair this paper layout with minimal semantic change"


def _apply_permission_error(
    item: Dict[str, Any],
    *,
    copy_suffix: Optional[str],
    allow_copy_apply: bool,
) -> Optional[str]:
    if copy_suffix:
        if not allow_copy_apply:
            return "controlled copy apply requires --allow-copy-apply"
        if item.get("allow_controlled_copy_apply") is not True:
            return "representative config forbids controlled copy apply"
        return None
    if item.get("allow_source_apply") is not True:
        return "representative config forbids direct source apply"
    return None


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _selected_representatives(config: Dict[str, Any], conferences: Optional[set[str]]) -> List[Dict[str, Any]]:
    selected = []
    for item in config.get("representatives") or []:
        if not isinstance(item, dict):
            continue
        conference = str(item.get("conference") or "")
        case_name = str(item.get("case") or "")
        if not conference or not case_name:
            continue
        if conferences and conference not in conferences:
            continue
        selected.append(item)
    return selected


def _target_pages(case_root: Path) -> Optional[int]:
    task = _load_json(case_root / "data" / "task.json")
    candidates: Iterable[Any] = (
        (task.get("task") or {}).get("target_pages") if isinstance(task.get("task"), dict) else None,
        task.get("target_pages"),
    )
    for value in candidates:
        if value is None:
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


def _run_one(
    *,
    benchmark_root: Path,
    item: Dict[str, Any],
    run_id: str,
    apply_source_mutation: bool,
    max_rounds: int,
    request: str,
) -> Dict[str, Any]:
    conference = str(item["conference"])
    case_name = str(item["case"])
    case_root = (benchmark_root / conference / case_name).resolve()
    mode = "apply" if apply_source_mutation else "dry_run"
    output_stem = f"repair_{mode}_{run_id}"
    run_result_path = f"data/run_result_{output_stem}.json"
    report_path = f"data/report_{output_stem}.json"
    status_view_path = case_root / "data" / f"status_view_{output_stem}.json"

    summary: Dict[str, Any] = {
        "conference": conference,
        "case": case_name,
        "copied_from": item.get("copied_from"),
        "case_root": str(case_root),
        "mode": mode,
        "run_result_path": run_result_path,
        "report_path": report_path,
        "status_view_path": str(status_view_path),
        "ok": False,
    }
    if item.get("copy_error"):
        summary["error"] = item["copy_error"]
        return summary
    if not case_root.is_dir():
        summary["error"] = "case root not found"
        return summary

    previous_cwd = Path.cwd()
    try:
        os.chdir(case_root)
        main_tex = _detect_main_tex(case_root, item.get("main_tex"))
        template = _resolve_template_key(item.get("template"))
        target_pages = _target_pages(case_root)
        report = _handle_paperfit_request(
            case_root,
            request=request,
            main_tex=main_tex,
            template=template,
            target_pages=target_pages,
            max_rounds=max_rounds,
            save_as=None,
            apply_source_mutation=apply_source_mutation,
            run_result_output_path=run_result_path,
            report_output_path=report_path,
            report_mode=f"representative_repair_{mode}",
        )
        status_view = build_runtime_status(
            project_root=case_root,
            state_path="data/state.json",
            run_result_path=run_result_path,
        )
        _write_json(status_view_path, status_view)
        run_result = report.get("run_result") or _load_json(case_root / run_result_path)
        repair_action = ((run_result.get("runtime_actions") or {}).get("repair_plan_executor") or {})
        repair = status_view.get("repair") or {}
        result_status = str(run_result.get("status") or "").lower()
        gatekeeper_decision = str(run_result.get("gatekeeper_decision") or "").upper()
        freshness = ((run_result.get("artifact_manifest") or {}).get("freshness") or {}).get("status")
        applied_count = int(repair_action.get("applied_count") or repair.get("applied_count") or 0)
        planned_candidates = int(repair_action.get("planned_candidates") or 0)
        if apply_source_mutation:
            accepted = (
                result_status in {"continue", "done"}
                and gatekeeper_decision in {"CONTINUE", "DONE"}
                and freshness == "pass"
                and applied_count > 0
            )
            outcome = "repaired" if result_status == "done" else (
                "applied_with_remaining_defects" if accepted else "blocked_or_invalid_apply"
            )
        else:
            accepted = (
                result_status in {"continue", "done"}
                and gatekeeper_decision in {"CONTINUE", "DONE"}
                and freshness == "pass"
                and planned_candidates > 0
            )
            outcome = "ready_for_apply_review" if accepted else "dry_run_not_actionable"
        summary.update(
            {
                "ok": accepted,
                "execution_completed": True,
                "outcome": outcome,
                "status": status_view.get("status"),
                "run_result_status": result_status,
                "gatekeeper_decision": gatekeeper_decision,
                "approval_status": (status_view.get("approval") or {}).get("status"),
                "freshness": freshness,
                "remaining_defects": (status_view.get("defect_summary") or {}).get("remaining"),
                "planned_candidates": planned_candidates,
                "applied_count": applied_count,
                "repair_reason": repair_action.get("reason"),
                "repair_requires_approval": repair_action.get("requires_approval"),
                "b2_findings": ((repair.get("b2_width_findings") or {}).get("total")),
                "b2_targetable": ((repair.get("b2_width_targetable_candidates") or {}).get("total")),
                "b2_selected": ((repair.get("b2_width_selected_candidates") or {}).get("total")),
            }
        )
    except Exception as error:  # pragma: no cover - operational runner
        summary["error"] = str(error)
        summary["traceback"] = traceback.format_exc()
    finally:
        os.chdir(previous_cwd)

    return summary


def _prepare_case_copy(
    *,
    benchmark_root: Path,
    copy_root: Path,
    item: Dict[str, Any],
    suffix: str,
    resume_existing: bool,
) -> Dict[str, Any]:
    conference = str(item["conference"])
    case_name = str(item["case"])
    source = (benchmark_root / conference / case_name).resolve()
    destination = (copy_root / conference / f"{case_name}{suffix}").resolve()
    copied_item = dict(item)
    copied_item["case"] = destination.name
    copied_item["copied_from"] = str(source)
    if destination.exists():
        if not resume_existing:
            copied_item["copy_status"] = "blocked_existing"
            copied_item["copy_error"] = "copy already exists; pass --resume-existing-copy to reuse it"
            return copied_item
        copied_item["copy_status"] = "resumed_existing"
        return copied_item
    if not source.is_dir():
        copied_item["copy_status"] = "source_missing"
        return copied_item
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns(
            "data",
            ".git",
            "__pycache__",
            ".DS_Store",
            "._*",
        ),
    )
    copied_item["copy_status"] = "created"
    return copied_item


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--benchmark-root",
        default=os.environ.get("PAPERFIT_BENCHMARK_ROOT", "benchmark_cases"),
        help="Benchmark cases root; defaults to PAPERFIT_BENCHMARK_ROOT or ./benchmark_cases",
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--conference", action="append", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--run-id", default=datetime.now().strftime("%Y%m%d_%H%M%S"))
    parser.add_argument("--max-rounds", type=int, default=1)
    parser.add_argument("--request", default=DEFAULT_REQUEST)
    parser.add_argument("--apply", action="store_true", help="Allow bounded source mutation on the selected cases.")
    parser.add_argument("--copy-suffix", default=None, help="Copy each selected case before running, appending this suffix to the case name.")
    parser.add_argument("--copy-root", default=None, help="Root for copied cases. Defaults to --benchmark-root.")
    parser.add_argument("--allow-copy-apply", action="store_true", help="Acknowledge controlled-copy source mutation when config allows it.")
    parser.add_argument("--resume-existing-copy", action="store_true", help="Reuse an existing controlled copy and its runtime state.")
    parser.add_argument("--summary-output", default=None)
    args = parser.parse_args()

    config = _load_json(Path(args.config))
    conferences = set(args.conference or []) or None
    representatives = _selected_representatives(config, conferences)
    if args.limit is not None:
        representatives = representatives[: max(args.limit, 0)]

    benchmark_root = Path(args.benchmark_root).resolve()
    mode = "apply" if args.apply else "dry_run"
    summary_path = Path(args.summary_output) if args.summary_output else (
        PACKAGE_ROOT / "output" / "repair_runs" / f"representative_repair_{mode}_{args.run_id}.json"
    )

    results: List[Dict[str, Any]] = []
    print(
        json.dumps(
            {
                "event": "batch_start",
                "mode": mode,
                "run_id": args.run_id,
                "case_count": len(representatives),
                "benchmark_root": str(benchmark_root),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    for index, item in enumerate(representatives, start=1):
        permission_error = None
        if args.apply:
            permission_error = _apply_permission_error(
                item,
                copy_suffix=args.copy_suffix,
                allow_copy_apply=bool(args.allow_copy_apply),
            )
        elif item.get("allow_repair_dry_run") is not True:
            permission_error = "representative config forbids repair dry-run"
        if permission_error:
            result = {
                "conference": item.get("conference"),
                "case": item.get("case"),
                "mode": mode,
                "ok": False,
                "execution_completed": False,
                "outcome": "permission_blocked",
                "error": permission_error,
            }
            results.append(result)
            print(json.dumps({"event": "case_done", **result}, ensure_ascii=False), flush=True)
            continue
        run_item = item
        run_benchmark_root = benchmark_root
        if args.copy_suffix:
            copy_root = Path(args.copy_root).resolve() if args.copy_root else benchmark_root
            run_item = _prepare_case_copy(
                benchmark_root=benchmark_root,
                copy_root=copy_root,
                item=item,
                suffix=args.copy_suffix,
                resume_existing=bool(args.resume_existing_copy),
            )
            run_benchmark_root = copy_root
        print(
            json.dumps(
                {
                    "event": "case_start",
                    "index": index,
                    "total": len(representatives),
                    "conference": run_item.get("conference"),
                    "case": run_item.get("case"),
                    "copied_from": run_item.get("copied_from"),
                    "copy_status": run_item.get("copy_status"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        result = _run_one(
            benchmark_root=run_benchmark_root,
            item=run_item,
            run_id=args.run_id,
            apply_source_mutation=bool(args.apply),
            max_rounds=max(args.max_rounds, 1),
            request=args.request,
        )
        results.append(result)
        print(json.dumps({"event": "case_done", **result}, ensure_ascii=False), flush=True)

    summary = {
        "schema_version": "1.0",
        "run_id": args.run_id,
        "mode": mode,
        "apply_source_mutation": bool(args.apply),
        "max_rounds": max(args.max_rounds, 1),
        "benchmark_root": str(benchmark_root),
        "config": str(Path(args.config).resolve()),
        "case_count": len(results),
        "execution_completed_count": sum(1 for item in results if item.get("execution_completed")),
        "ok_count": sum(1 for item in results if item.get("ok")),
        "failed_count": sum(1 for item in results if not item.get("ok")),
        "outcome_counts": {
            outcome: sum(1 for item in results if item.get("outcome") == outcome)
            for outcome in sorted({str(item.get("outcome")) for item in results if item.get("outcome")})
        },
        "results": results,
    }
    _write_json(summary_path, summary)
    print(json.dumps({"event": "batch_done", "summary_output": str(summary_path), **summary}, ensure_ascii=False), flush=True)
    return 0 if summary["failed_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
