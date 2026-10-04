#!/usr/bin/env python3
"""Read-only local web monitor for PaperFit runtime artifacts."""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

try:
    from runtime_status import build_runtime_status
except ModuleNotFoundError:
    from .runtime_status import build_runtime_status


PACKAGE_ROOT = Path(__file__).resolve().parent.parent
UI_ROOT = Path(__file__).resolve().parent / "monitor_ui"
REPRESENTATIVE_CONFIG_PATH = PACKAGE_ROOT / "config" / "benchmark_representatives.json"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def _load_json(path: Optional[Path]) -> Dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _load_representative_config() -> Dict[str, Any]:
    config = _load_json(REPRESENTATIVE_CONFIG_PATH)
    representatives = config.get("representatives")
    if not isinstance(representatives, list):
        config["representatives"] = []
    fixtures = config.get("fixtures")
    if not isinstance(fixtures, list):
        config["fixtures"] = []
    excluded = config.get("excluded_attempts")
    if not isinstance(excluded, list):
        config["excluded_attempts"] = []
    return config


def _representative_map(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for item in config.get("representatives") or []:
        if not isinstance(item, dict):
            continue
        conference = item.get("conference")
        case_name = item.get("case")
        if isinstance(conference, str) and conference and isinstance(case_name, str) and case_name:
            result[conference] = item
    return result


def _representative_metadata(item: Dict[str, Any]) -> Dict[str, Any]:
    keys = (
        "conference",
        "case",
        "main_tex",
        "template",
        "evidence_role",
        "selection_reason",
        "allow_repair_dry_run",
        "allow_source_apply",
        "allow_controlled_copy_apply",
        "notes",
    )
    return {key: item.get(key) for key in keys if key in item}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _max_int(current: Optional[int], value: Any) -> Optional[int]:
    parsed = _to_int(value, -1)
    if parsed < 0:
        return current
    return parsed if current is None else max(current, parsed)


def _dict_total(value: Any) -> int:
    if isinstance(value, dict):
        return _to_int(value.get("total"))
    return _to_int(value)


def _merge_action_success(current: Optional[bool], action: Any) -> Optional[bool]:
    if not isinstance(action, dict) or "success" not in action:
        return current
    success = action.get("success")
    if success is True:
        return True
    if success is False and current is None:
        return False
    return current


def _status_artifact_present(case_root: Path, artifact_value: Any) -> bool:
    if not artifact_value:
        return False
    if not isinstance(artifact_value, str):
        return True
    candidate = case_root / artifact_value
    return candidate.exists() if not Path(artifact_value).is_absolute() else Path(artifact_value).exists()


def _existing_page_count(case_root: Path) -> int:
    count = 0
    for rel in ("data/pages", "data/page_images", "page_images", "pages"):
        directory = case_root / rel
        if not directory.is_dir():
            continue
        count = max(
            count,
            sum(1 for child in directory.iterdir() if child.is_file() and not child.name.startswith("._") and child.suffix.lower() in IMAGE_EXTENSIONS),
        )
    return count


def _safe_project_path(root: Path, value: Optional[str]) -> Optional[Path]:
    if not value:
        return None
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    return resolved


def _relative(root: Path, path: Optional[Path]) -> Optional[str]:
    if path is None:
        return None
    try:
        return str(path.resolve().relative_to(root))
    except (OSError, ValueError):
        return str(path)


def _first_existing(root: Path, names: Iterable[str]) -> Optional[Path]:
    for name in names:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def _status_view(root: Path) -> Dict[str, Any]:
    try:
        return build_runtime_status(project_root=root, state_path="data/state.json")
    except Exception as error:  # pragma: no cover - defensive monitor path
        return {
            "schema_version": "1.0",
            "project_root": str(root),
            "status": "UNKNOWN",
            "monitor_error": str(error),
        }


def _artifact_path(root: Path, status: Dict[str, Any], key: str, fallback: Iterable[str]) -> Optional[Path]:
    artifact = ((status.get("artifacts") or {}).get(key))
    path = _safe_project_path(root, str(artifact)) if artifact else None
    if path and path.exists():
        return path
    return _first_existing(root, fallback)


def _page_number(path: Path) -> Optional[int]:
    match = re.search(r"(?:page|p)[_-]?(\d+)", path.stem, re.IGNORECASE)
    if match:
        return int(match.group(1))
    numbers = re.findall(r"\d+", path.stem)
    return int(numbers[-1]) if numbers else None


def _image_url(path: str) -> str:
    return f"/api/file?path={path}"


def _page_images(root: Path, status: Dict[str, Any]) -> List[Dict[str, Any]]:
    dirs: List[Path] = []
    artifact = ((status.get("artifacts") or {}).get("page_images_dir"))
    artifact_dir = _safe_project_path(root, str(artifact)) if artifact else None
    if artifact_dir and artifact_dir.is_dir():
        dirs.append(artifact_dir)
    for rel in ("data/pages", "data/page_images", "page_images", "pages"):
        candidate = root / rel
        if candidate.is_dir() and candidate not in dirs:
            dirs.append(candidate)

    images: List[Path] = []
    for directory in dirs:
        for child in directory.iterdir():
            if child.is_file() and child.suffix.lower() in IMAGE_EXTENSIONS:
                images.append(child)
    images = sorted(set(images), key=lambda item: (_page_number(item) or 10_000, item.name))
    result: List[Dict[str, Any]] = []
    for image in images:
        rel = _relative(root, image)
        if rel is None:
            continue
        result.append(
            {
                "name": image.name,
                "path": rel,
                "url": _image_url(rel),
                "page": _page_number(image),
                "mtime": image.stat().st_mtime,
            }
        )
    return result


def _event_log_paths(root: Path, status: Dict[str, Any]) -> List[Path]:
    paths: List[Path] = []
    event_log = ((status.get("runtime") or {}).get("event_log"))
    event_path = _safe_project_path(root, str(event_log)) if event_log else None
    if event_path and event_path.is_file():
        paths.append(event_path)
    event_dir = root / "data" / "events"
    if event_dir.is_dir():
        paths.extend(sorted(event_dir.glob("*.ndjson")))
    unique: List[Path] = []
    for path in paths:
        if path not in unique:
            unique.append(path)
    return unique


def _events(root: Path, status: Dict[str, Any], limit: int = 300) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for path in _event_log_paths(root, status):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                event["_source"] = _relative(root, path)
                events.append(event)
    events.sort(key=lambda item: str(item.get("timestamp") or ""))
    return events[-limit:]


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _summary_text(item: Dict[str, Any]) -> str:
    for key in ("description", "summary", "message", "reason", "type", "id"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "未记录说明"


def _family(item: Dict[str, Any]) -> str:
    for key in ("defect_family", "family", "category", "type", "id"):
        value = item.get(key)
        if isinstance(value, str) and value:
            match = re.search(r"\b([A-E]\d)\b", value)
            return match.group(1) if match else value
    return "unknown"


def _page(item: Dict[str, Any]) -> Optional[int]:
    for key in ("page", "page_number", "page_index"):
        value = item.get(key)
        if isinstance(value, int):
            return value + 1 if key == "page_index" else value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    bbox = item.get("bbox") or item.get("pdf_bbox")
    if isinstance(bbox, dict):
        return _page(bbox)
    return None


def _severity(item: Dict[str, Any]) -> str:
    value = item.get("severity") or item.get("level") or item.get("risk") or "unknown"
    return str(value)


def _finding_id(prefix: str, index: int, item: Dict[str, Any]) -> str:
    value = item.get("id") or item.get("finding_id") or item.get("defect_id")
    return str(value) if value else f"{prefix}-{index + 1}"


def _collect_findings_from_report(report: Dict[str, Any], prefix: str) -> List[Dict[str, Any]]:
    candidates: List[Any] = []
    for key in (
        "findings",
        "defects",
        "remaining_defects",
        "visual_findings",
        "native_findings",
        "priority_objects",
        "objects",
    ):
        candidates.extend(_as_list(report.get(key)))
    summary = report.get("summary")
    if isinstance(summary, dict):
        candidates.extend(_as_list(summary.get("findings")))
        candidates.extend(_as_list(summary.get("remaining_defects")))

    findings: List[Dict[str, Any]] = []
    for index, item in enumerate(candidates):
        if not isinstance(item, dict):
            continue
        findings.append(
            {
                "id": _finding_id(prefix, index, item),
                "family": _family(item),
                "page": _page(item),
                "severity": _severity(item),
                "summary": _summary_text(item),
                "subtype": item.get("visual_width_subtype") or item.get("subtype") or item.get("kind"),
                "object": item.get("object") or item.get("label") or item.get("object_label"),
                "bbox": item.get("bbox") or item.get("pdf_bbox"),
                "source": prefix,
            }
        )
    return findings


def _findings(visual_report: Dict[str, Any], defect_report: Dict[str, Any], gatekeeper: Dict[str, Any]) -> List[Dict[str, Any]]:
    seen = set()
    merged: List[Dict[str, Any]] = []
    for finding in (
        _collect_findings_from_report(visual_report, "visual")
        + _collect_findings_from_report(defect_report, "defect")
        + _collect_findings_from_report(gatekeeper, "gatekeeper")
    ):
        key = (finding.get("id"), finding.get("page"), finding.get("source"))
        if key in seen:
            continue
        seen.add(key)
        merged.append(finding)
    return merged


def _repair_candidates(repair_plan: Dict[str, Any], status: Dict[str, Any]) -> Dict[str, Any]:
    candidates = repair_plan.get("candidates")
    if not isinstance(candidates, list):
        candidates = repair_plan.get("repair_candidates")
    if not isinstance(candidates, list):
        candidates = []
    selected = (((status.get("repair") or {}).get("selected_candidates")) or [])
    return {
        "total": len(candidates),
        "items": candidates[:80],
        "selected": selected,
        "summary": repair_plan.get("summary") or {},
        "b2": {
            "findings": (status.get("repair") or {}).get("b2_width_findings"),
            "candidates": (status.get("repair") or {}).get("b2_width_candidates"),
            "targetable": (status.get("repair") or {}).get("b2_width_targetable_candidates"),
            "selected": (status.get("repair") or {}).get("b2_width_selected_candidates"),
            "unmatched": (status.get("repair") or {}).get("b2_width_unmatched_findings"),
        },
    }


def _capability_claim(status: Dict[str, Any], findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    repair = status.get("repair") or {}
    b2_evidence = any(str(item.get("family", "")).startswith("B2") for item in findings)
    b2_selected = ((repair.get("b2_width_selected_candidates") or {}).get("total") or 0) > 0
    b2_targetable = ((repair.get("b2_width_targetable_candidates") or {}).get("total") or 0) > 0
    stable = b2_selected or b2_targetable or b2_evidence
    return {
        "level": "局部 checkpoint" if stable else "证据不足",
        "scope": "B2 图表宽度检测 -> 修复选择 -> hard guard -> targeted retry",
        "conclusion": "稳定" if stable else "等待运行证据",
        "global_status": "不能声明整体完成",
        "limits": [
            "仍需多 defect family 证据",
            "仍需多模板 benchmark 证据",
            "仍需 rendered page-level Gatekeeper 结果",
            "仍需 staged multi-round apply 验证",
        ],
    }


def _json_values(value: Any) -> Iterable[Any]:
    if isinstance(value, dict):
        for child in value.values():
            yield from _json_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _json_values(child)
    else:
        yield value


def _has_b2_signal(payload: Dict[str, Any]) -> bool:
    for value in _json_values(payload):
        if isinstance(value, str) and "B2" in value:
            return True
    return False


def _quality_status(features: Dict[str, Any], quality: Dict[str, Any], has_evidence: bool) -> Dict[str, Any]:
    reasons: List[str] = []
    if not has_evidence:
        return {
            "status": "missing",
            "tone": "muted",
            "label": "缺少证据",
            "reasons": ["missing RunResult or StatusView artifacts"],
        }

    if features.get("freshness_pass"):
        reasons.append("fresh artifacts")
    if quality.get("page_images_count", 0) > 0:
        reasons.append("rendered page images")
    if features.get("gatekeeper_continue"):
        reasons.append("Gatekeeper CONTINUE")
    if quality.get("repair_candidates", 0) > 0:
        reasons.append("repair candidates available")
    if quality.get("b2_targetable", 0) > 0:
        reasons.append("targetable B2 candidates")
    if features.get("source_apply"):
        reasons.append("source apply evidence")
    if features.get("rollback"):
        reasons.append("rollback evidence")

    if quality.get("compile_success") is False:
        reasons.append("compile action failed")
        status, tone, label = "blocked", "bad", "阻断"
    elif quality.get("render_success") is False:
        reasons.append("render action failed")
        status, tone, label = "blocked", "bad", "阻断"
    elif features.get("gatekeeper_blocked"):
        reasons.append("Gatekeeper BLOCKED")
        status, tone, label = "blocked", "bad", "阻断"
    elif not features.get("freshness_pass"):
        reasons.append("freshness not passing")
        status, tone, label = "partial", "warn", "部分证据"
    elif quality.get("page_images_count", 0) <= 0:
        reasons.append("missing page images")
        status, tone, label = "partial", "warn", "部分证据"
    elif features.get("gatekeeper_continue") and (
        quality.get("repair_candidates", 0) > 0 or quality.get("b2_targetable", 0) > 0
    ):
        status, tone, label = "ready_for_repair_dry_run", "good", "可修复干跑"
    elif features.get("gatekeeper_continue"):
        status, tone, label = "diagnosis_only", "info", "诊断可用"
    else:
        reasons.append("Gatekeeper decision missing")
        status, tone, label = "partial", "warn", "部分证据"

    return {
        "status": status,
        "tone": tone,
        "label": label,
        "reasons": reasons,
    }


def _runtime_evidence_paths(
    case_root: Path,
    run_results: List[Path],
    status_views: List[Path],
) -> List[Path]:
    if run_results:
        latest_run_result = max(run_results, key=lambda item: item.stat().st_mtime)
        expected_relative = str(latest_run_result.relative_to(case_root))
        matching_status_views: List[Path] = []
        for status_path in status_views:
            payload = _load_json(status_path)
            selected = payload.get("run_result_path")
            if selected and str(selected) in {expected_relative, latest_run_result.name}:
                matching_status_views.append(status_path)
        paths = [latest_run_result]
        if matching_status_views:
            paths.append(max(matching_status_views, key=lambda item: item.stat().st_mtime))
        return sorted(paths, key=lambda item: item.stat().st_mtime)
    if status_views:
        return [max(status_views, key=lambda item: item.stat().st_mtime)]
    return []


def _observe_actions(runtime_actions: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
    compile_action = runtime_actions.get("compile") or {}
    render_action = runtime_actions.get("render_pages") or runtime_actions.get("render") or {}
    for key in ("post_repair_observe", "initial_observe"):
        observe = runtime_actions.get(key) or {}
        if not compile_action and isinstance(observe.get("compile"), dict):
            compile_action = observe.get("compile") or {}
        if not render_action and isinstance(observe.get("render"), dict):
            render_action = observe.get("render") or {}
    return compile_action, render_action


def _case_evidence(case_root: Path) -> Dict[str, Any]:
    data_dir = case_root / "data"
    run_results = sorted(
        path for path in data_dir.glob("run_result*.json") if not path.name.startswith("._")
    ) if data_dir.is_dir() else []
    status_views = sorted(
        path for path in data_dir.glob("status_view*.json") if not path.name.startswith("._")
    ) if data_dir.is_dir() else []
    json_paths = _runtime_evidence_paths(case_root, run_results, status_views)
    features = {
        "freshness_pass": False,
        "gatekeeper_continue": False,
        "gatekeeper_blocked": False,
        "source_apply": False,
        "rollback": False,
        "candidate_gate_pass": False,
        "candidate_gate_blocked": False,
        "b2_evidence": False,
    }
    latest_status = None
    latest_gatekeeper = None
    latest_file = None
    quality: Dict[str, Any] = {
        "compile_success": None,
        "render_success": None,
        "page_images_count": 0,
        "visual_report_exists": False,
        "defect_report_exists": False,
        "repair_plan_exists": False,
        "gatekeeper_report_exists": False,
        "status_view_exists": bool(status_views),
        "remaining_defects": None,
        "repair_candidates": 0,
        "b2_findings": 0,
        "b2_targetable": 0,
        "b2_selected": 0,
        "failure_type": None,
    }

    for path in json_paths:
        payload = _load_json(path)
        if not payload:
            continue
        latest_file = path
        latest_status = payload.get("status") or latest_status
        latest_gatekeeper = payload.get("gatekeeper_decision") or latest_gatekeeper
        freshness = payload.get("artifact_freshness") or (payload.get("artifact_manifest") or {}).get("freshness") or {}
        if freshness.get("status") == "pass":
            features["freshness_pass"] = True
        gatekeeper = payload.get("gatekeeper_decision") or (payload.get("gatekeeper") or {}).get("decision")
        if gatekeeper == "CONTINUE":
            features["gatekeeper_continue"] = True
        if gatekeeper == "BLOCKED":
            features["gatekeeper_blocked"] = True
        repair = payload.get("repair") or {}
        runtime_actions = payload.get("runtime_actions") or ((payload.get("runtime") or {}).get("actions") or {})
        compile_action, render_action = _observe_actions(runtime_actions)
        repair_action = runtime_actions.get("repair_plan_executor") or {}
        quality["compile_success"] = _merge_action_success(quality.get("compile_success"), compile_action)
        quality["render_success"] = _merge_action_success(quality.get("render_success"), render_action)

        manifest = payload.get("artifact_manifest") or {}
        manifest_artifacts = manifest.get("artifacts") or {}
        if isinstance(manifest_artifacts, dict):
            page_images = manifest_artifacts.get("page_images") or {}
            if isinstance(page_images, dict) and page_images.get("exists"):
                quality["page_images_count"] = max(_to_int(quality.get("page_images_count")), _to_int(page_images.get("count")))
            for artifact_key, quality_key in (
                ("visual_signal_report", "visual_report_exists"),
                ("defect_report", "defect_report_exists"),
                ("repair_plan", "repair_plan_exists"),
                ("gatekeeper_decision", "gatekeeper_report_exists"),
            ):
                artifact = manifest_artifacts.get(artifact_key) or {}
                if isinstance(artifact, dict) and artifact.get("exists"):
                    quality[quality_key] = True

        status_artifacts = payload.get("artifacts") or {}
        if isinstance(status_artifacts, dict):
            for artifact_key, quality_key in (
                ("visual_signal_report", "visual_report_exists"),
                ("defect_report", "defect_report_exists"),
                ("repair_plan", "repair_plan_exists"),
                ("gatekeeper_decision", "gatekeeper_report_exists"),
            ):
                if _status_artifact_present(case_root, status_artifacts.get(artifact_key)):
                    quality[quality_key] = True
            if _status_artifact_present(case_root, status_artifacts.get("page_images_dir")):
                quality["page_images_count"] = max(_to_int(quality.get("page_images_count")), _existing_page_count(case_root))

        defect_summary = payload.get("defect_summary") or {}
        quality["remaining_defects"] = _max_int(quality.get("remaining_defects"), defect_summary.get("remaining"))
        gatekeeper_payload = payload.get("gatekeeper") or {}
        if quality.get("remaining_defects") is None and isinstance(gatekeeper_payload.get("remaining_defects"), list):
            quality["remaining_defects"] = len(gatekeeper_payload.get("remaining_defects") or [])
        visual = payload.get("visual") or {}
        quality["repair_candidates"] = max(
            _to_int(quality.get("repair_candidates")),
            _to_int(repair.get("plan_candidates")),
            _to_int(repair_action.get("planned_candidates")),
            _to_int(repair_action.get("candidate_count")),
            _to_int(repair_action.get("candidates_count")),
        )
        quality["b2_findings"] = max(
            _to_int(quality.get("b2_findings")),
            _dict_total(repair.get("b2_width_findings")),
            _dict_total(visual.get("b2_width_findings")),
        )
        quality["b2_targetable"] = max(
            _to_int(quality.get("b2_targetable")),
            _dict_total(repair.get("b2_width_targetable_candidates")),
        )
        quality["b2_selected"] = max(
            _to_int(quality.get("b2_selected")),
            _dict_total(repair.get("b2_width_selected_candidates")),
        )
        failure = payload.get("failure") or {}
        if isinstance(failure.get("failure_type"), str):
            quality["failure_type"] = failure.get("failure_type")

        approval = payload.get("approval") or {}
        if (
            approval.get("status") == "approved_and_executed"
            or int(repair.get("applied_count") or 0) > 0
            or int(repair_action.get("applied_count") or 0) > 0
        ):
            features["source_apply"] = True
        integrity = payload.get("content_integrity") or {}
        if (
            integrity.get("validation_status") == "rolled_back"
            or "after_rollback" in path.name
            or (payload.get("failure") or {}).get("failure_type") == "post_repair_hard_guard_failed"
            or any(
                "rollback_to_snapshot" in ((lineage.get("actions") or {}))
                for lineage in (payload.get("round_artifact_lineage") or [])
                if isinstance(lineage, dict)
            )
        ):
            features["rollback"] = True
        loop = payload.get("repair_loop_policy") or {}
        candidate_gate = loop.get("candidate_approval_scope_gate") or repair_action.get("approval_scope_gate") or {}
        if candidate_gate.get("status") == "pass":
            features["candidate_gate_pass"] = True
        if candidate_gate.get("status") == "blocked":
            features["candidate_gate_blocked"] = True
        b2_selected = repair.get("b2_width_selected_candidates") or {}
        b2_findings = repair.get("b2_width_findings") or {}
        if (
            int(b2_selected.get("total") or 0) > 0
            or int(b2_findings.get("total") or 0) > 0
            or _has_b2_signal(payload)
            ):
            features["b2_evidence"] = True

    quality["page_images_count"] = max(_to_int(quality.get("page_images_count")), _existing_page_count(case_root))
    quality.update(_quality_status(features, quality, bool(run_results or status_views)))

    return {
        "root": str(case_root),
        "name": case_root.name,
        "run_results": len(run_results),
        "status_views": len(status_views),
        "has_evidence": bool(run_results or status_views),
        "evidence_mode": "latest_run",
        "selected_evidence_files": [str(path) for path in json_paths],
        "latest_file": str(latest_file) if latest_file else None,
        "latest_status": latest_status,
        "latest_gatekeeper": latest_gatekeeper,
        "features": features,
        "quality": quality,
    }


def _sum_feature(cases: List[Dict[str, Any]], feature: str) -> int:
    return sum(1 for case in cases if (case.get("features") or {}).get(feature))


def _sum_quality(cases: List[Dict[str, Any]], status: str) -> int:
    return sum(1 for case in cases if ((case.get("quality") or {}).get("status")) == status)


def _representative_case(
    conference_name: str,
    cases: List[Dict[str, Any]],
    representative_config: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    config_item = representative_config.get(conference_name) or {}
    preferred = config_item.get("case")
    if isinstance(preferred, str) and preferred:
        for case in cases:
            if case.get("name") == preferred:
                selected = dict(case)
                selected["representative_reason"] = "curated_config"
                selected["representative_config"] = _representative_metadata(config_item)
                return selected
    if not cases:
        return None

    def score(case: Dict[str, Any]) -> tuple:
        features = case.get("features") or {}
        quality = case.get("quality") or {}
        evidence_score = 0
        if case.get("has_evidence"):
            evidence_score += 100
        if features.get("freshness_pass"):
            evidence_score += 25
        if quality.get("status") == "ready_for_repair_dry_run":
            evidence_score += 20
        if quality.get("status") == "diagnosis_only":
            evidence_score += 12
        if features.get("source_apply"):
            evidence_score += 15
        if features.get("rollback"):
            evidence_score += 10
        if features.get("b2_evidence"):
            evidence_score += 8
        evidence_score += int(case.get("run_results") or 0) + int(case.get("status_views") or 0)
        copy_penalty = 1 if "_copy" in str(case.get("name") or "") else 0
        return (evidence_score, -copy_penalty, str(case.get("name") or ""))

    selected = dict(sorted(cases, key=score, reverse=True)[0])
    selected["representative_reason"] = "best_available"
    if preferred:
        selected["configured_case_missing"] = preferred
    return selected


def _benchmark_summary(root: Optional[Path]) -> Dict[str, Any]:
    config = _load_representative_config()
    representative_config = _representative_map(config)
    if root is None or not root.exists():
        return {
            "root": str(root) if root else None,
            "available": False,
            "representative_config": {
                "path": str(REPRESENTATIVE_CONFIG_PATH),
                "schema_version": config.get("schema_version"),
                "selection_mode": config.get("selection_mode") or "one_per_conference",
                "configured_count": len(representative_config),
            },
            "conferences": [],
            "cases": [],
            "fixtures": [],
            "totals": {},
        }

    conference_dirs = sorted(path for path in root.iterdir() if path.is_dir())
    conferences: List[Dict[str, Any]] = []
    selected_cases: List[Dict[str, Any]] = []
    fixtures: List[Dict[str, Any]] = []
    for conference_dir in conference_dirs:
        case_roots = sorted(path for path in conference_dir.iterdir() if path.is_dir())
        case_evidence = [_case_evidence(case_root) for case_root in case_roots]
        for case in case_evidence:
            case["conference"] = conference_dir.name
        evidence_cases = [case for case in case_evidence if case.get("has_evidence")]
        if conference_dir.name == "approval_gate":
            fixtures.extend(evidence_cases)
            continue
        selected_case = _representative_case(conference_dir.name, case_evidence, representative_config)
        selected_case_list = [selected_case] if selected_case else []
        selected_cases.extend(selected_case_list)
        run_result_count = sum(int(case.get("run_results") or 0) for case in case_evidence)
        status_view_count = sum(int(case.get("status_views") or 0) for case in case_evidence)
        conferences.append(
            {
                "name": conference_dir.name,
                "root": str(conference_dir),
                "pool_case_count": len(case_roots),
                "pool_evidence_case_count": len(evidence_cases),
                "selected_case_count": len(selected_case_list),
                "selected_evidence_case_count": sum(1 for case in selected_case_list if case.get("has_evidence")),
                "case_count": len(selected_case_list),
                "evidence_case_count": sum(1 for case in selected_case_list if case.get("has_evidence")),
                "pool_run_results": run_result_count,
                "pool_status_views": status_view_count,
                "run_results": sum(int(case.get("run_results") or 0) for case in selected_case_list),
                "status_views": sum(int(case.get("status_views") or 0) for case in selected_case_list),
                "freshness_pass_cases": _sum_feature(selected_case_list, "freshness_pass"),
                "gatekeeper_continue_cases": _sum_feature(selected_case_list, "gatekeeper_continue"),
                "gatekeeper_blocked_cases": _sum_feature(selected_case_list, "gatekeeper_blocked"),
                "source_apply_cases": _sum_feature(selected_case_list, "source_apply"),
                "rollback_cases": _sum_feature(selected_case_list, "rollback"),
                "candidate_gate_pass_cases": _sum_feature(selected_case_list, "candidate_gate_pass"),
                "candidate_gate_blocked_cases": _sum_feature(selected_case_list, "candidate_gate_blocked"),
                "b2_evidence_cases": _sum_feature(selected_case_list, "b2_evidence"),
                "ready_quality_cases": _sum_quality(selected_case_list, "ready_for_repair_dry_run"),
                "diagnosis_quality_cases": _sum_quality(selected_case_list, "diagnosis_only"),
                "partial_quality_cases": _sum_quality(selected_case_list, "partial"),
                "blocked_quality_cases": _sum_quality(selected_case_list, "blocked"),
                "missing_quality_cases": _sum_quality(selected_case_list, "missing"),
                "coverage_status": "present" if any(case.get("has_evidence") for case in selected_case_list) else "missing",
                "selected_case": selected_case,
                "sample_cases": selected_case_list,
            }
        )

    totals = {
        "conference_count": len(conferences),
        "fixture_count": len(fixtures),
        "pool_case_count": sum(item["pool_case_count"] for item in conferences),
        "pool_evidence_case_count": sum(item["pool_evidence_case_count"] for item in conferences),
        "selected_case_count": sum(item["selected_case_count"] for item in conferences),
        "case_count": sum(item["case_count"] for item in conferences),
        "evidence_case_count": sum(item["evidence_case_count"] for item in conferences),
        "run_results": sum(item["run_results"] for item in conferences),
        "status_views": sum(item["status_views"] for item in conferences),
        "freshness_pass_cases": sum(item["freshness_pass_cases"] for item in conferences),
        "source_apply_cases": sum(item["source_apply_cases"] for item in conferences),
        "rollback_cases": sum(item["rollback_cases"] for item in conferences),
        "b2_evidence_cases": sum(item["b2_evidence_cases"] for item in conferences),
        "ready_quality_cases": sum(item["ready_quality_cases"] for item in conferences),
        "diagnosis_quality_cases": sum(item["diagnosis_quality_cases"] for item in conferences),
        "partial_quality_cases": sum(item["partial_quality_cases"] for item in conferences),
        "blocked_quality_cases": sum(item["blocked_quality_cases"] for item in conferences),
        "missing_quality_cases": sum(item["missing_quality_cases"] for item in conferences),
    }
    return {
        "root": str(root),
        "available": True,
        "selection_mode": config.get("selection_mode") or "one_per_conference",
        "representative_config": {
            "path": str(REPRESENTATIVE_CONFIG_PATH),
            "schema_version": config.get("schema_version"),
            "selection_mode": config.get("selection_mode") or "one_per_conference",
            "configured_count": len(representative_config),
            "excluded_attempt_count": len(config.get("excluded_attempts") or []),
            "fixture_count": len(config.get("fixtures") or []),
        },
        "conferences": conferences,
        "cases": selected_cases,
        "fixtures": fixtures,
        "totals": totals,
    }


def build_snapshot(project_root: Path, benchmark_root: Optional[Path] = None) -> Dict[str, Any]:
    root = project_root.resolve()
    status = _status_view(root)
    artifacts = status.get("artifacts") or {}
    visual_path = _artifact_path(root, status, "visual_signal_report", ("data/visual_signal_report.json",))
    defect_path = _artifact_path(root, status, "defect_report", ("data/defect_report.json",))
    gatekeeper_path = _artifact_path(root, status, "gatekeeper_decision", ("data/gatekeeper_decision.json", "data/gatekeeper_result.json"))
    repair_plan_path = _artifact_path(root, status, "repair_plan", ("data/repair_plan.json",))
    repair_execution_path = _artifact_path(root, status, "repair_execution_report", ("data/repair_execution_report.json",))
    source_mutation_path = _artifact_path(root, status, "source_mutation_report", ("data/source_mutation_report.json",))
    rollback_path = _artifact_path(root, status, "rollback_report", ("data/rollback_report.json", "data/rollback_report_post_repair_hard_guard.json"))

    visual_report = _load_json(visual_path)
    defect_report = _load_json(defect_path)
    gatekeeper_report = _load_json(gatekeeper_path)
    repair_plan = _load_json(repair_plan_path)
    repair_execution = _load_json(repair_execution_path)
    source_mutation = _load_json(source_mutation_path)
    rollback = _load_json(rollback_path)
    findings = _findings(visual_report, defect_report, gatekeeper_report)
    page_images = _page_images(root, status)

    artifact_rows = []
    for key, path in {
        "state": root / "data" / "state.json",
        "RunResult": _safe_project_path(root, str(status.get("run_result_path"))) if status.get("run_result_path") else None,
        "visual_signal_report": visual_path,
        "defect_report": defect_path,
        "repair_plan": repair_plan_path,
        "repair_execution_report": repair_execution_path,
        "source_mutation_report": source_mutation_path,
        "rollback_report": rollback_path,
        "page_images_dir": _safe_project_path(root, str(artifacts.get("page_images_dir"))) if artifacts.get("page_images_dir") else None,
    }.items():
        artifact_rows.append(
            {
                "key": key,
                "path": _relative(root, path),
                "exists": bool(path and path.exists()),
            }
        )

    return {
        "schema_version": "1.0",
        "project_root": str(root),
        "status": status,
        "events": _events(root, status),
        "page_images": page_images,
        "findings": findings,
        "reports": {
            "visual_signal_report": visual_report,
            "defect_report": defect_report,
            "gatekeeper": gatekeeper_report,
            "repair_plan": repair_plan,
            "repair_execution": repair_execution,
            "source_mutation": source_mutation,
            "rollback": rollback,
        },
        "repair": _repair_candidates(repair_plan, status),
        "artifacts": artifact_rows,
        "capability_claim": _capability_claim(status, findings),
        "benchmark": _benchmark_summary(benchmark_root),
    }


class MonitorRequestHandler(BaseHTTPRequestHandler):
    server_version = "PaperFitMonitor/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[paperfit monitor] " + fmt % args + "\n")

    @property
    def project_root(self) -> Path:
        return self.server.project_root  # type: ignore[attr-defined]

    @property
    def benchmark_root(self) -> Optional[Path]:
        return self.server.benchmark_root  # type: ignore[attr-defined]

    def _send_bytes(
        self,
        body: bytes,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
        *,
        send_body: bool = True,
    ) -> None:
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.end_headers()
        if send_body:
            self.wfile.write(body)

    def _send_json(self, payload: Dict[str, Any], status: HTTPStatus = HTTPStatus.OK, *, send_body: bool = True) -> None:
        self._send_bytes(
            json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            "application/json; charset=utf-8",
            status,
            send_body=send_body,
        )

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self._send_json({"error": message}, status)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        if parsed.path == "/api/snapshot":
            self._send_json(build_snapshot(self.project_root, self.benchmark_root))
            return
        if parsed.path == "/api/file":
            query = parse_qs(parsed.query)
            rel = unquote((query.get("path") or [""])[0])
            path = _safe_project_path(self.project_root, rel)
            if path is None or not path.is_file():
                self._send_error(HTTPStatus.NOT_FOUND, "file not found")
                return
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self._send_bytes(path.read_bytes(), content_type)
            return
        self._serve_static(parsed.path)

    def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        if parsed.path == "/api/snapshot":
            self._send_json(build_snapshot(self.project_root, self.benchmark_root), send_body=False)
            return
        if parsed.path == "/api/file":
            query = parse_qs(parsed.query)
            rel = unquote((query.get("path") or [""])[0])
            path = _safe_project_path(self.project_root, rel)
            if path is None or not path.is_file():
                self._send_json({"error": "file not found"}, HTTPStatus.NOT_FOUND, send_body=False)
                return
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self._send_bytes(path.read_bytes(), content_type, send_body=False)
            return
        self._serve_static(parsed.path, send_body=False)

    def _serve_static(self, request_path: str, *, send_body: bool = True) -> None:
        rel = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        path = (UI_ROOT / rel).resolve()
        try:
            path.relative_to(UI_ROOT)
        except ValueError:
            self._send_error(HTTPStatus.FORBIDDEN, "forbidden")
            return
        if not path.is_file():
            path = UI_ROOT / "index.html"
        content_type = mimetypes.guess_type(path.name)[0] or "text/html"
        if path.suffix == ".js":
            content_type = "text/javascript; charset=utf-8"
        elif path.suffix in {".html", ".css"}:
            content_type = f"{content_type}; charset=utf-8"
        self._send_bytes(path.read_bytes(), content_type, send_body=send_body)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the PaperFit Observatory monitor")
    parser.add_argument("--project", default=".", help="Paper project root")
    parser.add_argument("--benchmark-root", default=None, help="Optional benchmark cases root")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    project_root = Path(args.project).resolve()
    benchmark_root = Path(args.benchmark_root).resolve() if args.benchmark_root else None
    if not project_root.exists():
        raise SystemExit(f"project root does not exist: {project_root}")
    if not UI_ROOT.is_dir():
        raise SystemExit(f"monitor UI assets not found: {UI_ROOT}")

    server = ThreadingHTTPServer((args.host, args.port), MonitorRequestHandler)
    server.project_root = project_root  # type: ignore[attr-defined]
    server.benchmark_root = benchmark_root  # type: ignore[attr-defined]
    url = f"http://{args.host}:{args.port}/"
    print(f"PaperFit Observatory: {url}", flush=True)
    print(f"Project: {project_root}", flush=True)
    if benchmark_root:
        print(f"Benchmark root: {benchmark_root}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nPaperFit Observatory stopped.", flush=True)


if __name__ == "__main__":
    main()
