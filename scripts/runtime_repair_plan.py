#!/usr/bin/env python3
"""Repair-plan immutability and source-freshness helpers."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from runtime_snapshots import discover_source_files


IMMUTABILITY_POLICY = "invalidate_on_source_change"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _project_relative(project_root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root.resolve()))
    except ValueError:
        return str(path.resolve())


def collect_source_fingerprint(project_root: Path, main_tex: str) -> Dict[str, Any]:
    root = project_root.resolve()
    files: List[Dict[str, Any]] = []
    aggregate = hashlib.sha256()
    for path in discover_source_files(root, main_tex):
        rel = _project_relative(root, path)
        sha = _sha256_file(path)
        stat = path.stat()
        files.append(
            {
                "path": rel,
                "bytes": stat.st_size,
                "sha256": sha,
            }
        )
        aggregate.update(rel.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(sha.encode("ascii"))
        aggregate.update(b"\0")

    return {
        "schema_version": "1.0",
        "main_tex": main_tex,
        "aggregate_sha256": aggregate.hexdigest(),
        "files": files,
    }


def attach_repair_plan_fingerprint(
    *,
    project_root: Path,
    main_tex: str,
    repair_plan_path: str | Path,
) -> Dict[str, Any]:
    root = project_root.resolve()
    path = Path(repair_plan_path)
    plan_path = path if path.is_absolute() else root / path
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["immutability"] = {
        "schema_version": "1.0",
        "policy": IMMUTABILITY_POLICY,
        "generated_at": datetime.now().isoformat(),
        "source_fingerprint": collect_source_fingerprint(root, main_tex),
    }
    plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return plan


def validate_repair_plan_freshness(
    *,
    project_root: Path,
    main_tex: str,
    repair_plan: Dict[str, Any],
) -> Dict[str, Any]:
    immutability = repair_plan.get("immutability") or {}
    expected = immutability.get("source_fingerprint") or {}
    current = collect_source_fingerprint(project_root, main_tex)

    if not expected:
        return {
            "schema_version": "1.0",
            "status": "missing_fingerprint",
            "fresh": False,
            "policy": immutability.get("policy"),
            "expected_aggregate_sha256": None,
            "current_aggregate_sha256": current.get("aggregate_sha256"),
            "changed_files": [],
        }

    expected_files = {str(item.get("path")): item for item in expected.get("files") or []}
    current_files = {str(item.get("path")): item for item in current.get("files") or []}
    changed_files: List[Dict[str, Any]] = []
    for rel in sorted(set(expected_files) | set(current_files)):
        before = expected_files.get(rel)
        after = current_files.get(rel)
        if before is None:
            changed_files.append({"path": rel, "change": "added", "current_sha256": after.get("sha256") if after else None})
        elif after is None:
            changed_files.append({"path": rel, "change": "removed", "expected_sha256": before.get("sha256")})
        elif before.get("sha256") != after.get("sha256"):
            changed_files.append(
                {
                    "path": rel,
                    "change": "modified",
                    "expected_sha256": before.get("sha256"),
                    "current_sha256": after.get("sha256"),
                }
            )

    fresh = (
        expected.get("aggregate_sha256") == current.get("aggregate_sha256")
        and not changed_files
        and immutability.get("policy") == IMMUTABILITY_POLICY
    )
    return {
        "schema_version": "1.0",
        "status": "fresh" if fresh else "stale",
        "fresh": fresh,
        "policy": immutability.get("policy"),
        "expected_aggregate_sha256": expected.get("aggregate_sha256"),
        "current_aggregate_sha256": current.get("aggregate_sha256"),
        "changed_files": changed_files,
    }


def blocked_stale_repair_plan_report(
    *,
    repair_plan_path: str,
    main_tex: str,
    freshness: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "schema_version": "1.0",
        "status": "blocked_stale_repair_plan",
        "applied_count": 0,
        "selected_candidates": [],
        "repair_plan": repair_plan_path,
        "main_tex": main_tex,
        "freshness": freshness,
        "error": {
            "type": "stale_repair_plan",
            "message": "Repair plan source fingerprint no longer matches current source files; regenerate the repair plan before applying patches.",
        },
    }
