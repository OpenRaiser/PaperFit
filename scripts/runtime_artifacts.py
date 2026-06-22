#!/usr/bin/env python3
"""Artifact hashing and freshness checks for PaperFit runtime results."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


def _resolve(project_root: Path, value: Optional[str]) -> Optional[Path]:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else project_root / path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_entry(path: Optional[Path], project_root: Path) -> Dict[str, Any]:
    if path is None:
        return {"exists": False, "path": None}
    try:
        display_path = str(path.resolve().relative_to(project_root.resolve()))
    except ValueError:
        display_path = str(path)
    if not path.is_file():
        return {"exists": False, "path": display_path}
    stat = path.stat()
    return {
        "exists": True,
        "path": display_path,
        "bytes": stat.st_size,
        "mtime": stat.st_mtime,
        "sha256": _sha256_file(path),
    }


def _page_files(page_dir: Optional[Path]) -> list[Path]:
    if page_dir is None or not page_dir.is_dir():
        return []
    return sorted(
        path
        for path in page_dir.glob("page_*.png")
        if not path.name.startswith("._") and path.is_file()
    )


def _hash_page_set(paths: Iterable[Path], project_root: Path) -> Dict[str, Any]:
    pages = []
    aggregate = hashlib.sha256()
    newest_mtime: Optional[float] = None
    oldest_mtime: Optional[float] = None

    for path in paths:
        entry = _file_entry(path, project_root)
        pages.append(entry)
        rel = str(entry.get("path") or path.name)
        sha = str(entry.get("sha256") or "")
        aggregate.update(rel.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(sha.encode("ascii"))
        aggregate.update(b"\0")
        mtime = entry.get("mtime")
        if isinstance(mtime, (int, float)):
            newest_mtime = mtime if newest_mtime is None else max(newest_mtime, mtime)
            oldest_mtime = mtime if oldest_mtime is None else min(oldest_mtime, mtime)

    return {
        "exists": bool(pages),
        "count": len(pages),
        "aggregate_sha256": aggregate.hexdigest() if pages else None,
        "oldest_mtime": oldest_mtime,
        "newest_mtime": newest_mtime,
        "pages": pages,
    }


def _freshness_checks(entries: Dict[str, Any]) -> Dict[str, Any]:
    checks: Dict[str, Dict[str, Any]] = {}

    pdf = entries.get("pdf") or {}
    pages = entries.get("page_images") or {}
    visual = entries.get("visual_signal_report") or {}
    defect = entries.get("defect_report") or {}
    gatekeeper = entries.get("gatekeeper_decision") or {}

    pdf_mtime = pdf.get("mtime")
    page_newest = pages.get("newest_mtime")
    page_oldest = pages.get("oldest_mtime")
    visual_mtime = visual.get("mtime")
    defect_mtime = defect.get("mtime")
    gatekeeper_mtime = gatekeeper.get("mtime")

    checks["pdf_exists"] = {"pass": bool(pdf.get("exists"))}
    checks["page_images_exist"] = {
        "pass": bool(pages.get("exists")),
        "count": int(pages.get("count") or 0),
    }
    checks["page_images_not_older_than_pdf"] = {
        "pass": bool(page_oldest is not None and pdf_mtime is not None and page_oldest >= pdf_mtime),
        "pdf_mtime": pdf_mtime,
        "page_oldest_mtime": page_oldest,
    }
    checks["visual_not_older_than_pages"] = {
        "pass": bool(visual_mtime is not None and page_newest is not None and visual_mtime >= page_newest),
        "visual_mtime": visual_mtime,
        "page_newest_mtime": page_newest,
    }
    checks["defects_not_older_than_visual"] = {
        "pass": bool(defect_mtime is not None and visual_mtime is not None and defect_mtime >= visual_mtime),
        "defect_mtime": defect_mtime,
        "visual_mtime": visual_mtime,
    }
    checks["gatekeeper_not_older_than_defects"] = {
        "pass": bool(gatekeeper_mtime is not None and defect_mtime is not None and gatekeeper_mtime >= defect_mtime),
        "gatekeeper_mtime": gatekeeper_mtime,
        "defect_mtime": defect_mtime,
    }

    blocking = [name for name, item in checks.items() if not item.get("pass")]
    return {
        "status": "pass" if not blocking else "stale_or_missing",
        "blocking_checks": blocking,
        "checks": checks,
    }


def collect_artifact_manifest(
    project_root: Path,
    main_tex: str,
    artifacts: Dict[str, Any],
) -> Dict[str, Any]:
    """Collect artifact identity and freshness without mutating runtime state."""

    root = project_root.resolve()
    main_path = _resolve(root, main_tex)
    pdf_path = main_path.with_suffix(".pdf") if main_path is not None else None
    page_dir = _resolve(root, artifacts.get("page_images_dir"))

    entries: Dict[str, Any] = {
        "task_spec": _file_entry(_resolve(root, artifacts.get("task_spec")), root),
        "main_tex": _file_entry(main_path, root),
        "pdf": _file_entry(pdf_path, root),
        "page_images": _hash_page_set(_page_files(page_dir), root),
        "rule_report": _file_entry(_resolve(root, artifacts.get("rule_report")), root),
        "crossrefs_report": _file_entry(_resolve(root, artifacts.get("crossrefs_report")), root),
        "visual_signal_report": _file_entry(_resolve(root, artifacts.get("visual_signal_report")), root),
        "column_void_report": _file_entry(_resolve(root, artifacts.get("column_void_report")), root),
        "defect_report": _file_entry(_resolve(root, artifacts.get("defect_report")), root),
        "repair_plan": _file_entry(_resolve(root, artifacts.get("repair_plan")), root),
        "gatekeeper_decision": _file_entry(_resolve(root, artifacts.get("gatekeeper_decision")), root),
    }
    return {
        "schema_version": "1.0",
        "artifacts": entries,
        "freshness": _freshness_checks(entries),
    }
