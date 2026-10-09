#!/usr/bin/env python3
"""Artifact hashing and freshness checks for PaperFit runtime results."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

try:
    from runtime_snapshots import discover_source_files
except ModuleNotFoundError:  # package import during unit tests
    from .runtime_snapshots import discover_source_files


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
        display_path = path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        display_path = path.resolve().as_posix()
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


def _pdf_entry(path: Optional[Path], project_root: Path) -> Dict[str, Any]:
    entry = _file_entry(path, project_root)
    entry.update({"page_count": None, "page_count_status": "missing"})
    if not entry["exists"]:
        return entry
    try:
        try:
            import pymupdf as fitz
        except ImportError:  # Older PyMuPDF distributions.
            import fitz

        with fitz.open(path) as document:
            if not document.is_pdf or document.needs_pass or document.page_count <= 0:
                entry["page_count_status"] = "unreadable"
            else:
                entry.update({"page_count": document.page_count, "page_count_status": "available"})
    except ImportError:
        entry["page_count_status"] = "dependency_unavailable"
    except (OSError, RuntimeError, ValueError):
        entry["page_count_status"] = "unreadable"
    return entry


def _page_files(page_dir: Optional[Path]) -> list[Path]:
    if page_dir is None or not page_dir.is_dir():
        return []
    return sorted(
        path
        for path in page_dir.glob("page_*.png")
        if not path.name.startswith("._") and path.is_file()
    )


def _page_image_entry(path: Path, project_root: Path) -> Dict[str, Any]:
    """Verify both PNG structure and decoded pixels before counting visual evidence."""
    entry = _file_entry(path, project_root)
    entry["image_status"] = "missing"
    if not entry["exists"]:
        return entry
    try:
        from PIL import Image
    except ImportError:
        entry["image_status"] = "dependency_unavailable"
        return entry
    try:
        with Image.open(path) as image:
            if image.format != "PNG":
                entry["image_status"] = "invalid_format"
                return entry
            image.verify()
        # verify() checks the container; load() also checks compressed pixel data.
        with Image.open(path) as image:
            image.load()
            entry.update({"image_status": "available", "width": image.width,
                          "height": image.height})
    except (OSError, ValueError, SyntaxError, EOFError, Image.DecompressionBombError):
        entry["image_status"] = "decode_failed"
    return entry


def _hash_page_set(paths: Iterable[Path], project_root: Path) -> Dict[str, Any]:
    pages = []
    aggregate = hashlib.sha256()
    newest_mtime: Optional[float] = None
    oldest_mtime: Optional[float] = None
    page_numbers: list[int] = []
    invalid_names: list[str] = []
    invalid_images: list[Dict[str, Any]] = []

    for path in paths:
        entry = _page_image_entry(path, project_root)
        match = re.fullmatch(r"page_([0-9]+)\.png", path.name, re.IGNORECASE)
        number = int(match.group(1)) if match else None
        entry["page_number"] = number
        if number is None or number <= 0:
            invalid_names.append(path.name)
        else:
            page_numbers.append(number)
        if entry.get("image_status") != "available":
            invalid_images.append({"page": number, "filename": path.name,
                                   "reason": entry.get("image_status")})
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
        "page_numbers": sorted(page_numbers),
        "invalid_page_names": invalid_names,
        "invalid_page_images": invalid_images,
        "aggregate_sha256": aggregate.hexdigest() if pages else None,
        "oldest_mtime": oldest_mtime,
        "newest_mtime": newest_mtime,
        "pages": pages,
    }


def _source_set_entry(paths: Iterable[Path], project_root: Path) -> Dict[str, Any]:
    files = [_file_entry(path, project_root) for path in paths]
    newest_mtime: Optional[float] = None
    for entry in files:
        mtime = entry.get("mtime")
        if isinstance(mtime, (int, float)):
            newest_mtime = mtime if newest_mtime is None else max(newest_mtime, mtime)
    return {
        "exists": bool(files) and all(entry.get("exists") for entry in files),
        "count": len(files),
        "newest_mtime": newest_mtime,
        "files": files,
    }


def _freshness_checks(entries: Dict[str, Any]) -> Dict[str, Any]:
    checks: Dict[str, Dict[str, Any]] = {}

    source_set = entries.get("source_set") or {}
    pdf = entries.get("pdf") or {}
    pages = entries.get("page_images") or {}
    visual = entries.get("visual_signal_report") or {}
    defect = entries.get("defect_report") or {}
    gatekeeper = entries.get("gatekeeper_decision") or {}

    source_newest = source_set.get("newest_mtime")
    pdf_mtime = pdf.get("mtime")
    page_newest = pages.get("newest_mtime")
    page_oldest = pages.get("oldest_mtime")
    visual_mtime = visual.get("mtime")
    defect_mtime = defect.get("mtime")
    gatekeeper_mtime = gatekeeper.get("mtime")

    checks["pdf_exists"] = {"pass": bool(pdf.get("exists"))}
    checks["pdf_not_older_than_source_set"] = {
        "pass": bool(pdf_mtime is not None and source_newest is not None and pdf_mtime >= source_newest),
        "source_newest_mtime": source_newest,
        "pdf_mtime": pdf_mtime,
    }
    checks["page_images_exist"] = {
        "pass": bool(pages.get("exists")),
        "count": int(pages.get("count") or 0),
    }
    expected_count = pdf.get("page_count")
    count_available = isinstance(expected_count, int) and not isinstance(expected_count, bool) and expected_count > 0
    checks["pdf_page_count_available"] = {
        "pass": count_available,
        "page_count": expected_count,
        "status": pdf.get("page_count_status"),
    }
    numbers = pages.get("page_numbers") or []
    checks["page_images_complete"] = {
        "pass": bool(count_available and not pages.get("invalid_page_names")
                     and int(pages.get("count") or 0) == expected_count
                     and numbers == list(range(1, expected_count + 1))),
        "expected_page_count": expected_count,
        "rendered_page_count": int(pages.get("count") or 0),
        "page_numbers": numbers,
        "invalid_page_names": pages.get("invalid_page_names") or [],
    }
    checks["page_images_readable"] = {
        "pass": bool(pages.get("exists") and not pages.get("invalid_page_images")
                     and all(page.get("image_status") == "available"
                             for page in pages.get("pages") or [])),
        "invalid_page_images": pages.get("invalid_page_images") or [],
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

    try:
        source_paths = discover_source_files(root, main_tex)
    except OSError:
        source_paths = [main_path] if main_path is not None else []

    entries: Dict[str, Any] = {
        "task_spec": _file_entry(_resolve(root, artifacts.get("task_spec")), root),
        "main_tex": _file_entry(main_path, root),
        "source_set": _source_set_entry(source_paths, root),
        "pdf": _pdf_entry(pdf_path, root),
        "page_images": _hash_page_set(_page_files(page_dir), root),
        "rule_report": _file_entry(_resolve(root, artifacts.get("rule_report")), root),
        "crossrefs_report": _file_entry(_resolve(root, artifacts.get("crossrefs_report")), root),
        "visual_signal_report": _file_entry(_resolve(root, artifacts.get("visual_signal_report")), root),
        "column_void_report": _file_entry(_resolve(root, artifacts.get("column_void_report")), root),
        "defect_report": _file_entry(_resolve(root, artifacts.get("defect_report")), root),
        "repair_plan": _file_entry(_resolve(root, artifacts.get("repair_plan")), root),
        "gatekeeper_decision": _file_entry(_resolve(root, artifacts.get("gatekeeper_decision")), root),
    }
    visual_path = _resolve(root, artifacts.get("visual_signal_report"))
    vision_diagnosis: Dict[str, Any] = {}
    if visual_path is not None and visual_path.is_file():
        try:
            visual_report = json.loads(visual_path.read_text(encoding="utf-8"))
            diagnosis = visual_report.get("vlm_diagnosis")
            if isinstance(diagnosis, dict):
                vision_diagnosis = {
                    key: diagnosis.get(key)
                    for key in ("status", "configured", "clean", "coverage", "missing_page_numbers")
                }
        except (OSError, ValueError, AttributeError):
            pass
    return {
        "schema_version": "1.0",
        "artifacts": entries,
        "freshness": _freshness_checks(entries),
        "vision_diagnosis": vision_diagnosis,
    }
