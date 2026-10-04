#!/usr/bin/env python3
"""Source mutation integrity reports for source-changing runtime runs."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from runtime_snapshots import load_snapshot_manifest, validate_snapshot_manifest


def _sha256_file(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_source_mutation_report(
    *,
    project_root: Path,
    rollback_target: str | Path,
    output_path: Optional[str | Path] = None,
) -> Dict[str, Any]:
    root = project_root.resolve()
    manifest = load_snapshot_manifest(root, rollback_target)
    validation = validate_snapshot_manifest(root, manifest)
    files: List[Dict[str, Any]] = []
    changed_count = 0
    missing_count = 0

    for item in manifest.get("files") or []:
        rel = str(item.get("path") or "")
        current_path = root / rel
        before_sha = item.get("sha256")
        after_sha = _sha256_file(current_path)
        exists = current_path.is_file()
        changed = before_sha != after_sha
        if changed:
            changed_count += 1
        if not exists:
            missing_count += 1
        files.append(
            {
                "path": rel,
                "exists": exists,
                "before_sha256": before_sha,
                "after_sha256": after_sha,
                "changed": changed,
            }
        )

    report = {
        "schema_version": "1.0",
        "generated_at": datetime.now().isoformat(),
        "snapshot_id": manifest.get("snapshot_id"),
        "rollback_target": str(rollback_target),
        "snapshot_validation": validation,
        "summary": {
            "tracked_files": len(files),
            "changed_files": changed_count,
            "missing_files": missing_count,
        },
        "files": files,
    }
    if output_path is not None:
        out = Path(output_path)
        out_path = out if out.is_absolute() else root / out
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report
