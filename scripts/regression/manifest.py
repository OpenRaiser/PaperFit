#!/usr/bin/env python3
"""回归运行的 run_manifest 与跨版本兼容性检查。

设计原则（见 docs/REGRESSION_DESIGN.md）：
- 代码/数据/注入/渲染/裁判/聚合六类因子必须可追溯；
- 两次运行比较前先做兼容检查，任一不兼容 → 新评测 epoch（INVALID_RUN），
  不许直接比总分。
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


COMPATIBILITY_KEYS = {
    "dataset": ["dataset_manifest_hash"],
    "injection": ["injector_version", "benchmark_pack_hash"],
    "render": ["render_dpi", "montage_version", "image_format"],
    "judge": ["judge_model_name", "judge_prompt_hash"],
    "aggregation": ["aggregation_version", "decision_version"],
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_head(root: Path) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def build_run_manifest(
    *,
    project_root: Path,
    run_id: str,
    reference_version: str,
    candidate_version: str,
    dataset_files: Optional[List[Path]] = None,
    benchmark_pack_hash: Optional[str] = None,
    injector_version: Optional[str] = None,
    render_dpi: int = 220,
    judge_model_name: Optional[str] = None,
    judge_prompt_hash: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    dataset_hashes = {str(path): file_sha256(path) for path in (dataset_files or []) if path.is_file()}
    manifest: Dict[str, Any] = {
        "schema_version": "1.0",
        "run_id": run_id,
        "generated_at": datetime.now().isoformat(),
        "comparison": {
            "reference_version": reference_version,
            "candidate_version": candidate_version,
            "reference_git_sha": git_head(project_root),  # 调用方在 checkout v1 时再生成一份
        },
        "code": {
            "paperfit_git_sha": git_head(project_root),
            "python_version": sys.version.split()[0],
            "node_version": extra.get("node_version") if extra else None,
            "platform": platform.platform(),
        },
        "dataset": {
            "dataset_manifest_hash": hashlib.sha256(
                json.dumps(dataset_hashes, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "file_hashes": dataset_hashes,
        },
        "injection": {
            "injector_version": injector_version,
            "benchmark_pack_hash": benchmark_pack_hash,
        },
        "render": {
            "render_dpi": render_dpi,
            "montage_version": "v3-stitched",
            "image_format": "jpeg-q78",
        },
        "judge": {
            "judge_model_name": judge_model_name,
            "judge_prompt_hash": judge_prompt_hash,
        },
        "aggregation": {
            "aggregation_version": "regression-v1",
            "decision_version": "decision-v1",
        },
    }
    if extra:
        manifest["extra"] = extra
    return manifest


def check_manifest_compatibility(manifest_a: Dict[str, Any], manifest_b: Dict[str, Any]) -> Dict[str, Any]:
    """逐类比较兼容因子；返回兼容状态与差异清单。"""
    differences: Dict[str, List[str]] = {}
    compatible = True
    for category, keys in COMPATIBILITY_KEYS.items():
        category_diffs = []
        for key in keys:
            value_a = _nested_get(manifest_a, category, key)
            value_b = _nested_get(manifest_b, category, key)
            if value_a is not None and value_b is not None and value_a != value_b:
                category_diffs.append(f"{category}.{key}: {value_a} != {value_b}")
        if category_diffs:
            compatible = False
            differences[category] = category_diffs
    return {
        "compatible": compatible,
        "differences": differences,
        "epoch_required": not compatible,
    }


def _nested_get(manifest: Dict[str, Any], category: str, key: str) -> Any:
    section = manifest.get(category)
    if isinstance(section, dict):
        return section.get(key)
    return None
