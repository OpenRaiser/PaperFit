#!/usr/bin/env python3
"""
Helpers for loading templates.yaml together with template_registry_seed.yaml.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def package_root() -> Path:
    env = os.environ.get("PAPERFIT_PACKAGE_ROOT")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parent.parent


def _yaml_load(path: Path) -> Dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("PyYAML is required for template_registry.py") from exc
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def load_template_registry(root: Optional[Path] = None) -> Dict[str, Any]:
    base = root or package_root()
    path = base / "config" / "template_registry_seed.yaml"
    data = _yaml_load(path)
    data["_path"] = str(path)
    return data


def load_raw_templates(root: Optional[Path] = None) -> Dict[str, Any]:
    base = root or package_root()
    path = base / "config" / "templates.yaml"
    data = _yaml_load(path)
    return data.get("templates") or {}


def _resolve_prepared_root(base: Path) -> Path:
    candidates = [
        base / "config" / "host_assets" / "template_kits_prepared",
        base.parent / "PaperFit-release" / "config" / "host_assets" / "template_kits_prepared",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _status_score(status: Optional[str]) -> int:
    scores = {
        "downloaded": 40,
        "shared_asset": 30,
        "fallback_saved": 20,
        "partial": 10,
        "unresolved": 0,
    }
    return scores.get(str(status or "").strip(), -1)


def _resolve_local_path(base: Path, local_path: Optional[str]) -> Optional[Path]:
    if not local_path:
        return None
    path = Path(local_path)
    if path.is_absolute():
        return path
    candidates = [base / local_path, base.parent / local_path]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _match_registry_venue(
    template_id: str,
    template_meta: Dict[str, Any],
    registry: Dict[str, Any],
) -> Tuple[Optional[str], str]:
    venues = registry.get("venues") or {}
    if template_id in venues:
        return template_id, "exact_template_id"

    family = template_meta.get("family")
    year = template_meta.get("year")
    if family and year is not None:
        for venue_id, venue in venues.items():
            if venue.get("family") == family and venue.get("year") == year:
                return venue_id, "exact_family_year"

    if not family:
        return None, "unmatched"

    candidates = []
    for venue_id, venue in venues.items():
        if venue.get("family") != family:
            continue
        candidates.append(
            (
                int(venue.get("year") or 0),
                _status_score(venue.get("status")),
                venue_id,
            )
        )
    if not candidates:
        return None, "unmatched"
    candidates.sort(reverse=True)
    return candidates[0][2], "family_latest"


def enrich_template_metadata(
    template_id: str,
    template_meta: Dict[str, Any],
    registry: Dict[str, Any],
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    base = root or package_root()
    enriched = copy.deepcopy(template_meta)
    assets = registry.get("assets") or {}
    venues = registry.get("venues") or {}

    venue_id, match_type = _match_registry_venue(template_id, enriched, registry)
    resolution: Dict[str, Any] = {
        "matched": venue_id is not None,
        "match_type": match_type,
        "matched_venue_id": venue_id,
        "registry_path": registry.get("_path"),
    }

    if venue_id is not None:
        venue = copy.deepcopy(venues.get(venue_id) or {})
        asset_ref = venue.get("asset_ref")
        asset = copy.deepcopy(assets.get(asset_ref) or {}) if asset_ref else {}
        asset_abs = _resolve_local_path(base, asset.get("local_path"))
        asset_exists = bool(asset_abs and asset_abs.exists())
        resolution.update(
            {
                "venue_status": venue.get("status"),
                "asset_ref": asset_ref,
                "asset_status": asset.get("status"),
                "asset_type": asset.get("asset_type"),
                "asset_local_path": asset.get("local_path"),
                "asset_local_abspath": str(asset_abs) if asset_abs else None,
                "asset_exists": asset_exists,
                "asset_machine_verified": asset.get("machine_verified"),
                "asset_source_url": asset.get("download_url")
                or asset.get("mirror_page")
                or asset.get("official_page"),
            }
        )
        prepared_root = _resolve_prepared_root(base)
        prepared_dir = prepared_root / asset_ref if asset_ref else None
        prepared_manifest = prepared_dir / "manifest.json" if prepared_dir else None
        resolution.update(
            {
                "prepared_dir": str(prepared_dir) if prepared_dir else None,
                "prepared_manifest": str(prepared_manifest) if prepared_manifest else None,
                "prepared_exists": bool(prepared_dir and prepared_dir.exists()),
                "prepared_manifest_exists": bool(prepared_manifest and prepared_manifest.exists()),
            }
        )
        enriched["registry_asset"] = {
            "venue": venue,
            "asset": asset,
            "resolution": resolution,
        }
        official_assets = copy.deepcopy(enriched.get("official_assets") or {})
        official_assets["registry_asset_ref"] = asset_ref
        official_assets["registry_match_type"] = match_type
        official_assets["registry_status"] = venue.get("status")
        official_assets["local_asset_path"] = asset.get("local_path")
        official_assets["local_asset_abspath"] = str(asset_abs) if asset_abs else None
        official_assets["local_asset_exists"] = asset_exists
        official_assets["asset_type"] = asset.get("asset_type")
        official_assets["asset_status"] = asset.get("status")
        official_assets["machine_verified"] = asset.get("machine_verified")
        official_assets["prepared_dir"] = resolution["prepared_dir"]
        official_assets["prepared_manifest"] = resolution["prepared_manifest"]
        official_assets["prepared_exists"] = resolution["prepared_exists"]
        if not official_assets.get("source_url"):
            official_assets["source_url"] = resolution["asset_source_url"]
        enriched["official_assets"] = official_assets
    else:
        enriched["registry_asset"] = {
            "venue": None,
            "asset": None,
            "resolution": resolution,
        }
    return enriched


def load_templates(root: Optional[Path] = None, include_registry: bool = True) -> Dict[str, Any]:
    base = root or package_root()
    templates = load_raw_templates(base)
    if not include_registry:
        return templates
    registry = load_template_registry(base)
    return {
        key: enrich_template_metadata(key, meta or {}, registry, base)
        for key, meta in templates.items()
    }
