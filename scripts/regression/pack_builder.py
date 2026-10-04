#!/usr/bin/env python3
"""冻结 benchmark pack 生成器（B0 侧一次性生成并冻结）。

流程（docs/REGRESSION_DESIGN.md）：
  原始 arXiv 包 → 解包 → 定位主 tex → 扰动注入（regression/perturb.py）
  → 编译 B0（compile_fn 可注入）→ 渲染页图（render_fn 可注入）
  → 写冻结 manifest（源码/页图 sha256 全记录）。

生成之后所有 PaperFit 版本复用同一份 pack，禁止重新注入。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from .perturb import INJECTOR_VERSION, apply_perturbations
except ImportError:  # flat import when scripts/regression itself is on sys.path
    from perturb import INJECTOR_VERSION, apply_perturbations

PACK_SCHEMA_VERSION = "1.0"
MAIN_TEX_HINTS = ("paper", "main", "ms", "article")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_archive(archive: Path, dest: Path) -> Path:
    """解包 tar.gz 并返回"项目根"（含主 tex 的目录，兼容包内多一层目录）。"""
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:*") as tar:
        try:
            tar.extractall(dest, filter="data")  # py3.12+ 安全过滤
        except TypeError:
            tar.extractall(dest)
    # 若解包后只有一个子目录且主 tex 在其中，则下钻一层
    entries = [item for item in dest.iterdir() if not item.name.startswith("__MACOSX")]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return dest


def find_main_tex(project_root: Path) -> Optional[Path]:
    """选择含 \\documentclass 且行数最多的 .tex（平铺与一层子目录都查）。"""
    candidates: List[Path] = []
    for pattern in ("*.tex", "*/*.tex"):
        candidates.extend(project_root.glob(pattern))
    best: Optional[Path] = None
    best_lines = -1
    for candidate in sorted(candidates):
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "\\documentclass" not in text or "\\begin{document}" not in text:
            continue
        lines = text.count("\n")
        name_score = 1 if any(hint in candidate.name.lower() for hint in MAIN_TEX_HINTS) else 0
        score = lines + name_score * 10_000
        if score > best_lines:
            best_lines = score
            best = candidate
    return best


@dataclass
class CaseBuildResult:
    case_id: str
    paper_id: str
    status: str  # success / injection_failed / compile_failed / render_failed
    injection_status: str
    reasons: List[str] = field(default_factory=list)
    case_dir: Optional[Path] = None


def build_case(
    *,
    archive: Path,
    pack_root: Path,
    paper_id: str,
    case_id: str,
    venue_id: str,
    perturbation_ids: List[str],
    seed: int,
    target_pages: Optional[int],
    profile: Optional[str],
    compile_fn: Callable[[Path], Dict[str, Any]],
    render_fn: Callable[[Path, Path], Dict[str, Any]],
    column_type: Optional[str] = None,
    skip_injection: bool = False,
) -> CaseBuildResult:
    """构建并冻结一个 B0 case。compile_fn(project_root) / render_fn(project_root, pages_dir)。

    skip_injection=True：数据集本身已是扰动 B0（如 paperfit-data），原样冻结。
    """
    result = CaseBuildResult(case_id=case_id, paper_id=paper_id, status="success", injection_status="success")
    case_dir = pack_root / paper_id / case_id
    work_dir = case_dir / "b0_project"
    pages_dir = case_dir / "pages"
    source_manifest: Dict[str, Any] = {
        "schema_version": PACK_SCHEMA_VERSION,
        "archive": str(archive),
        "archive_sha256": _sha256_file(archive),
        "injector_version": INJECTOR_VERSION,
        "seed": seed,
        "perturbation_ids": perturbation_ids,
        "generated_at": datetime.now().isoformat(),
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = extract_archive(archive, Path(tmpdir) / "extracted")
        main_tex = find_main_tex(project_root)
        if main_tex is None:
            result.status = "injection_failed"
            result.injection_status = "main_tex_not_found"
            result.reasons.append("no .tex with \\documentclass found in archive")
            _write_failure_manifest(case_dir, source_manifest, result)
            return result

        original_tex = main_tex.read_text(encoding="utf-8", errors="replace")
        source_manifest["main_tex_relpath"] = str(main_tex.relative_to(project_root))
        source_manifest["original_main_tex_sha256"] = _sha256_text(original_tex)

        if skip_injection:
            perturbed_tex = original_tex
            injection_results = []
            source_manifest["injection_mode"] = "skipped_by_design"
        else:
            perturbed_tex, injection_results = apply_perturbations(original_tex, perturbation_ids)
            source_manifest["injection_mode"] = "injector" 
        source_manifest["injection_results"] = [
            {"perturbation_id": item.perturbation_id, "status": item.status, "reason": item.reason,
             "edits": item.edits}
            for item in injection_results
        ]
        if any(item.status != "success" for item in injection_results):
            failed = [item.perturbation_id for item in injection_results if item.status != "success"]
            result.status = "injection_failed"
            result.injection_status = "perturbation_failed"
            result.reasons.append(f"failed perturbations: {', '.join(failed)}")
            _write_failure_manifest(case_dir, source_manifest, result)
            return result

        # 冻结 B0 工程：整包拷贝 + 覆盖扰动后的主 tex
        if work_dir.exists():
            shutil.rmtree(work_dir)
        shutil.copytree(project_root, work_dir)
        (work_dir / main_tex.relative_to(project_root)).write_text(perturbed_tex, encoding="utf-8")
        source_manifest["perturbed_main_tex_sha256"] = _sha256_text(perturbed_tex)

    compile_report = compile_fn(work_dir)
    _write_json(case_dir / "compile.json", compile_report)
    if not compile_report.get("success"):
        result.status = "compile_failed"
        result.injection_status = "success"
        result.reasons.append("B0 compile failed after perturbation")
        _write_json(case_dir / "source_manifest.json", source_manifest)
        result.case_dir = case_dir
        return result

    if pages_dir.exists():
        shutil.rmtree(pages_dir)
    render_report = render_fn(work_dir, pages_dir)
    _write_json(case_dir / "render.json", render_report)
    if not render_report.get("success"):
        result.status = "render_failed"
        result.reasons.append("B0 render failed")
        _write_json(case_dir / "source_manifest.json", source_manifest)
        result.case_dir = case_dir
        return result

    page_hashes = {
        page.name: _sha256_file(page) for page in sorted(pages_dir.glob("page_*.png"))
    }
    source_manifest["page_hashes"] = page_hashes
    source_manifest["page_count"] = len(page_hashes)
    _write_json(case_dir / "source_manifest.json", source_manifest)

    # 评估器格式的 case 元数据（嵌套 benchmark_case 键）
    _write_json(
        case_dir / "benchmark_case.json",
        {
            "benchmark_case": {
                "case_id": case_id,
                "paper_id": paper_id,
                "venue_id": venue_id,
                "template_name": venue_id,
                "profile": profile,
                "column_type": column_type,
                "target_pages": target_pages,
                "requested_perturbations": perturbation_ids,
                "seed": seed,
                "injector_version": INJECTOR_VERSION,
                "frozen": True,
            }
        },
    )
    result.case_dir = case_dir
    return result


def build_pack(
    *,
    entries: List[Dict[str, Any]],
    pack_root: Path,
    compile_fn: Callable[[Path], Dict[str, Any]],
    render_fn: Callable[[Path, Path], Dict[str, Any]],
    skip_injection: bool = False,
) -> Dict[str, Any]:
    """批量构建冻结 pack 并写 pack_manifest.json。entries: 见 build_case 参数（archive/paper_id/...）。"""
    results: List[Dict[str, Any]] = []
    for entry in entries:
        build = build_case(
            archive=Path(entry["archive"]),
            pack_root=pack_root,
            paper_id=entry["paper_id"],
            case_id=entry["case_id"],
            venue_id=entry["venue_id"],
            perturbation_ids=entry["perturbation_ids"],
            seed=int(entry.get("seed") or 0),
            target_pages=entry.get("target_pages"),
            profile=entry.get("profile"),
            compile_fn=compile_fn,
            render_fn=render_fn,
            column_type=entry.get("column_type"),
            skip_injection=skip_injection,
        )
        results.append(
            {
                "case_id": build.case_id,
                "paper_id": build.paper_id,
                "status": build.status,
                "injection_status": build.injection_status,
                "reasons": build.reasons,
            }
        )
    pack_manifest = {
        "schema_version": PACK_SCHEMA_VERSION,
        "pack_root": str(pack_root),
        "injector_version": INJECTOR_VERSION,
        "generated_at": datetime.now().isoformat(),
        "case_results": results,
        "summary": {
            "total": len(results),
            "success": sum(1 for item in results if item["status"] == "success"),
            "injection_failed": sum(1 for item in results if item["status"] == "injection_failed"),
            "compile_failed": sum(1 for item in results if item["status"] == "compile_failed"),
            "render_failed": sum(1 for item in results if item["status"] == "render_failed"),
        },
        "frozen": True,
    }
    _write_json(pack_root / "pack_manifest.json", pack_manifest)
    return pack_manifest


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_failure_manifest(case_dir: Path, source_manifest: Dict[str, Any], result: CaseBuildResult) -> None:
    source_manifest["failure"] = {"status": result.status, "reasons": result.reasons}
    _write_json(case_dir / "source_manifest.json", source_manifest)
    result.case_dir = case_dir
