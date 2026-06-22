#!/usr/bin/env python3
"""Snapshot and rollback metadata helpers for source-changing PaperFit tasks."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set


SOURCE_EXTENSIONS = {".tex", ".bib", ".bst", ".cls", ".sty"}


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


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _candidate_tex_path(project_root: Path, base_dir: Path, raw_name: str) -> Path:
    name = raw_name.strip()
    path = Path(name)
    if not path.suffix:
        path = path.with_suffix(".tex")
    if path.is_absolute():
        return path
    candidate = (base_dir / path).resolve()
    if candidate.exists():
        return candidate
    return (project_root / path).resolve()


def _iter_included_tex(project_root: Path, tex_path: Path, seen: Set[Path]) -> Iterable[Path]:
    if tex_path in seen or not tex_path.is_file():
        return
    seen.add(tex_path)
    yield tex_path

    text = _read_text(tex_path)
    for match in re.finditer(r"\\(?:input|include)\{([^}]+)\}", text):
        child = _candidate_tex_path(project_root, tex_path.parent, match.group(1))
        if child.is_file() and child.resolve().is_relative_to(project_root.resolve()):
            yield from _iter_included_tex(project_root, child.resolve(), seen)


def _iter_bibliography_files(project_root: Path, tex_files: Iterable[Path]) -> Iterable[Path]:
    seen: Set[Path] = set()
    for tex_path in tex_files:
        text = _read_text(tex_path)
        for match in re.finditer(r"\\(?:bibliography|addbibresource)\{([^}]+)\}", text):
            for raw_name in match.group(1).split(","):
                name = raw_name.strip()
                if not name:
                    continue
                path = Path(name)
                if not path.suffix:
                    path = path.with_suffix(".bib")
                candidate = path if path.is_absolute() else (tex_path.parent / path).resolve()
                if not candidate.exists():
                    candidate = (project_root / path).resolve()
                if candidate.is_file() and candidate.suffix in SOURCE_EXTENSIONS and candidate not in seen:
                    seen.add(candidate)
                    yield candidate


def discover_source_files(project_root: Path, main_tex: str) -> List[Path]:
    root = project_root.resolve()
    main_path = (root / main_tex).resolve()
    tex_files = list(_iter_included_tex(root, main_path, set()))
    files: List[Path] = list(tex_files)
    files.extend(_iter_bibliography_files(root, tex_files))
    deduped: Dict[str, Path] = {}
    for path in files:
        if path.is_file() and path.suffix in SOURCE_EXTENSIONS:
            try:
                path.resolve().relative_to(root)
            except ValueError:
                continue
            deduped[str(path.resolve())] = path.resolve()
    return [deduped[key] for key in sorted(deduped)]


def create_pre_repair_snapshot(
    *,
    project_root: Path,
    main_tex: str,
    snapshot_root: str = "data/snapshots",
    snapshot_id: str | None = None,
) -> Dict[str, Any]:
    root = project_root.resolve()
    snapshot_id = snapshot_id or datetime.now().strftime("pre_repair_%Y%m%d_%H%M%S")
    snapshot_dir = root / snapshot_root / snapshot_id
    files_dir = snapshot_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    entries: List[Dict[str, Any]] = []
    for source in discover_source_files(root, main_tex):
        rel = _project_relative(root, source)
        backup_path = files_dir / rel
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, backup_path)
        entries.append(
            {
                "path": rel,
                "backup_path": _project_relative(root, backup_path),
                "bytes": source.stat().st_size,
                "sha256": _sha256_file(source),
            }
        )

    manifest = {
        "schema_version": "1.0",
        "snapshot_id": snapshot_id,
        "created_at": datetime.now().isoformat(),
        "project_root": str(root),
        "main_tex": main_tex,
        "snapshot_dir": _project_relative(root, snapshot_dir),
        "rollback_target": _project_relative(root, snapshot_dir / "snapshot_manifest.json"),
        "files": entries,
    }
    manifest_path = snapshot_dir / "snapshot_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def load_snapshot_manifest(project_root: Path, rollback_target: str | Path) -> Dict[str, Any]:
    root = project_root.resolve()
    path = Path(rollback_target)
    manifest_path = path if path.is_absolute() else root / path
    if not manifest_path.is_file():
        raise FileNotFoundError(f"snapshot manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("snapshot manifest must be a JSON object")
    return manifest


def validate_snapshot_manifest(project_root: Path, manifest: Dict[str, Any]) -> Dict[str, Any]:
    root = project_root.resolve()
    errors: List[str] = []
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        errors.append("manifest.files must be a non-empty list")

    checked_files: List[Dict[str, Any]] = []
    for item in files if isinstance(files, list) else []:
        if not isinstance(item, dict):
            errors.append("manifest.files entries must be objects")
            continue
        rel_path = item.get("path")
        backup_rel = item.get("backup_path")
        expected_sha = item.get("sha256")
        if not isinstance(rel_path, str) or not rel_path:
            errors.append("snapshot file entry missing path")
            continue
        if Path(rel_path).is_absolute() or ".." in Path(rel_path).parts:
            errors.append(f"unsafe snapshot source path: {rel_path}")
            continue
        if not isinstance(backup_rel, str) or not backup_rel:
            errors.append(f"snapshot file entry missing backup_path for {rel_path}")
            continue
        if Path(backup_rel).is_absolute() or ".." in Path(backup_rel).parts:
            errors.append(f"unsafe snapshot backup path: {backup_rel}")
            continue
        backup_path = root / backup_rel
        if not backup_path.is_file():
            errors.append(f"snapshot backup missing: {backup_rel}")
            continue
        actual_sha = _sha256_file(backup_path)
        if isinstance(expected_sha, str) and expected_sha and actual_sha != expected_sha:
            errors.append(f"snapshot backup hash mismatch: {backup_rel}")
        checked_files.append(
            {
                "path": rel_path,
                "backup_path": backup_rel,
                "sha256": actual_sha,
                "backup_exists": True,
            }
        )

    return {
        "schema_version": "1.0",
        "valid": not errors,
        "errors": errors,
        "files_checked": checked_files,
    }


def restore_snapshot(
    *,
    project_root: Path,
    rollback_target: str | Path,
    output_path: Optional[str | Path] = None,
) -> Dict[str, Any]:
    root = project_root.resolve()
    manifest = load_snapshot_manifest(root, rollback_target)
    validation = validate_snapshot_manifest(root, manifest)
    if not validation.get("valid"):
        raise ValueError("Invalid snapshot manifest: " + "; ".join(validation.get("errors") or []))

    restored: List[Dict[str, Any]] = []
    for item in manifest.get("files") or []:
        rel_path = str(item["path"])
        backup_rel = str(item["backup_path"])
        destination = root / rel_path
        backup = root / backup_rel
        before_sha = _sha256_file(destination) if destination.is_file() else None
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup, destination)
        after_sha = _sha256_file(destination)
        restored.append(
            {
                "path": rel_path,
                "backup_path": backup_rel,
                "before_sha256": before_sha,
                "after_sha256": after_sha,
                "restored": True,
            }
        )

    report = {
        "schema_version": "1.0",
        "restored_at": datetime.now().isoformat(),
        "snapshot_id": manifest.get("snapshot_id"),
        "rollback_target": str(rollback_target),
        "validation": validation,
        "restored_files": restored,
    }
    if output_path is not None:
        out = Path(output_path)
        out_path = out if out.is_absolute() else root / out
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report
