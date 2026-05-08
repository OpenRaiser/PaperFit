#!/usr/bin/env python3
"""
Expand template kit assets into a normalized prepared directory with manifests.
"""
from __future__ import annotations

import argparse
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from template_registry import load_template_registry, package_root


TEXT_EXTENSIONS = {
    ".tex",
    ".sty",
    ".cls",
    ".bst",
    ".bib",
    ".md",
    ".txt",
    ".yml",
    ".yaml",
    ".json",
}


def to_manifest_path(root: Path, path: Path) -> str:
    """Store paths relative to the package root when possible."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def detect_top_level_prefix(zip_names: List[str]) -> Optional[str]:
    first_parts = set()
    for name in zip_names:
        clean = name.strip("/")
        if not clean:
            continue
        first_parts.add(clean.split("/", 1)[0])
    if len(first_parts) != 1:
        return None
    prefix = next(iter(first_parts))
    return prefix


def guess_entry_candidates(base_dir: Path) -> Dict[str, List[str]]:
    patterns = {
        "main_tex_candidates": ["*.tex"],
        "style_candidates": ["*.sty", "*.cls"],
        "bibliography_candidates": ["*.bst", "*.bib"],
        "source_candidates": ["*.dtx", "*.ins"],
    }
    result: Dict[str, List[str]] = {}
    for key, globs in patterns.items():
        found: List[str] = []
        for glob in globs:
            for path in sorted(base_dir.rglob(glob)):
                if path.is_file():
                    rel = path.relative_to(base_dir)
                    if any(part == "__MACOSX" for part in rel.parts):
                        continue
                    if path.name.startswith("._") or path.name == ".DS_Store":
                        continue
                    found.append(str(rel))
        result[key] = found[:50]
    return result


def summarize_text_snippet(path: Path) -> Optional[str]:
    if path.suffix.lower() not in TEXT_EXTENSIONS:
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = [line.rstrip() for line in text.splitlines()[:20]]
    cleaned = [line for line in lines if line.strip()]
    if not cleaned:
        return None
    return "\n".join(cleaned[:8])


def prepare_zip_asset(asset_id: str, asset: Dict[str, Any], src: Path, dst_root: Path, root: Path) -> Dict[str, Any]:
    prepared_dir = dst_root / asset_id
    payload_dir = prepared_dir / "payload"
    if prepared_dir.exists():
        shutil.rmtree(prepared_dir)
    payload_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(src) as zf:
        zip_names = zf.namelist()
        zf.extractall(payload_dir)

    stripped_prefix = detect_top_level_prefix(zip_names)
    effective_root = payload_dir / stripped_prefix if stripped_prefix and (payload_dir / stripped_prefix).exists() else payload_dir
    candidates = guess_entry_candidates(effective_root)

    sample_files: Dict[str, Optional[str]] = {}
    for key in ("main_tex_candidates", "style_candidates", "source_candidates"):
        if candidates[key]:
            sample_rel = candidates[key][0]
            sample_files[key[:-11] + "_sample"] = summarize_text_snippet(effective_root / sample_rel)

    manifest = {
        "asset_id": asset_id,
        "status": "prepared",
        "asset_type": asset.get("asset_type"),
        "source_local_path": to_manifest_path(root, src),
        "prepared_dir": to_manifest_path(root, prepared_dir),
        "payload_dir": to_manifest_path(root, payload_dir),
        "effective_root": to_manifest_path(root, effective_root),
        "zip_entry_count": len(zip_names),
        "stripped_top_level_prefix": stripped_prefix,
        **candidates,
        **sample_files,
    }
    (prepared_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def prepare_file_asset(asset_id: str, asset: Dict[str, Any], src: Path, dst_root: Path, root: Path) -> Dict[str, Any]:
    prepared_dir = dst_root / asset_id
    payload_dir = prepared_dir / "payload"
    if prepared_dir.exists():
        shutil.rmtree(prepared_dir)
    payload_dir.mkdir(parents=True, exist_ok=True)
    copied = payload_dir / src.name
    shutil.copy2(src, copied)
    manifest = {
        "asset_id": asset_id,
        "status": "prepared",
        "asset_type": asset.get("asset_type"),
        "source_local_path": to_manifest_path(root, src),
        "prepared_dir": to_manifest_path(root, prepared_dir),
        "payload_dir": to_manifest_path(root, payload_dir),
        "copied_file": to_manifest_path(root, copied),
        "main_tex_candidates": [],
        "style_candidates": [],
        "bibliography_candidates": [],
        "source_candidates": [],
        "file_sample": summarize_text_snippet(copied),
    }
    (prepared_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def resolve_asset_path(root: Path, local_path: str) -> Path:
    path = Path(local_path)
    if path.is_absolute():
        return path
    candidates = [root / local_path, root.parent / local_path]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def prepare_assets(root: Path, force: bool = True) -> Dict[str, Any]:
    root = root.resolve()
    registry = load_template_registry(root)
    assets = registry.get("assets") or {}
    prepared_root = root / "config" / "host_assets" / "template_kits_prepared"
    prepared_root.mkdir(parents=True, exist_ok=True)

    prepared: Dict[str, Any] = {}
    for asset_id, asset in assets.items():
        local_path = asset.get("local_path")
        if not local_path:
            prepared[asset_id] = {"status": "skipped", "reason": "missing_local_path"}
            continue
        src = resolve_asset_path(root, str(local_path))
        if not src.exists():
            prepared[asset_id] = {
                "status": "missing",
                "reason": "source_not_found",
                "source_local_path": to_manifest_path(root, src),
            }
            continue
        if src.suffix.lower() == ".zip":
            manifest = prepare_zip_asset(asset_id, asset, src, prepared_root, root)
        else:
            manifest = prepare_file_asset(asset_id, asset, src, prepared_root, root)
        prepared[asset_id] = manifest

    summary = {
        "prepared_root": to_manifest_path(root, prepared_root),
        "assets": prepared,
    }
    (prepared_root / "manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare template assets for PaperFit")
    parser.add_argument("--package-root", default=None)
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()

    root = Path(args.package_root).resolve() if args.package_root else package_root()
    summary = prepare_assets(root)
    if args.print_summary:
        print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
