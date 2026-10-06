#!/usr/bin/env python3
"冻结 benchmark pack 生成器（B0 侧一次性生成并冻结）。"

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePath
from typing import Any, Callable, Dict, List, Optional, Set

try:
    from .perturb import INJECTOR_VERSION, apply_perturbations
except ImportError:  # flat import when scripts/regression itself is on sys.path
    from perturb import INJECTOR_VERSION, apply_perturbations

PACK_SCHEMA_VERSION = "1.0"
MAIN_TEX_HINTS = ("paper", "main", "ms", "article")

# case 构建阶段（顺序即流水线顺序）；值：pending / ok / failed / skipped
STAGE_ORDER = ("copy", "source_validation", "compile", "render", "ready")

# copytree 冻结时丢弃的编译缓存/运行产物（arXiv 包与 paperfit-data 里常见，
# 既压路径长度又避免把机器产物当"作者源码"冻结）
PACK_COPY_IGNORED_DIR_NAMES = {"out", "validation_artifacts", "__pycache__", "__macosx"}
PACK_COPY_IGNORED_SUFFIXES = (
    ".aux",
    ".log",
    ".fls",
    ".fdb_latexmk",
    ".bbl",
    ".blg",
    ".out",
    ".synctex.gz",
    ".toc",
    ".lof",
    ".lot",
)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def case_dir_key(case_id: str) -> str:
    """case 物理目录短 key：sha256(case_id)[:12]，压长路径且免非法文件名字符。"""
    return hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:12]


def resolve_case_dir(pack_root: Path, paper_id: str, case_id: str) -> Path:
    """按短 key 定位 pack 内的 case 目录。

    优先短 key 布局；兼容本改动之前冻结的 pack（目录名即人类可读 case_id）。
    """
    short = pack_root / paper_id / case_dir_key(case_id)
    if short.is_dir():
        return short
    legacy = pack_root / paper_id / case_id
    if legacy.is_dir():
        return legacy
    return short


def _pack_copy_ignore(directory: str, names: List[str]) -> Set[str]:
    """shutil.copytree 的 ignore 回调：滤掉编译缓存与运行产物目录。"""
    ignored: Set[str] = set()
    in_data_dir = PurePath(directory).name.lower() == "data"
    for name in names:
        lowered = name.lower()
        if lowered in PACK_COPY_IGNORED_DIR_NAMES:
            ignored.add(name)
        elif lowered == "pages" and in_data_dir:
            # data/pages 是 PaperFit 运行时页图，不是作者源码
            ignored.add(name)
        elif any(lowered.endswith(suffix) for suffix in PACK_COPY_IGNORED_SUFFIXES):
            ignored.add(name)
    return ignored


def _within_root(root: Path, target: Path) -> bool:
    return target == root or root in target.parents


def extract_archive(archive: Path, dest: Path) -> Path:
    """解包 tar.gz 并返回"项目根"（含主 tex 的目录，兼容包内多一层目录）。

    解包前逐成员校验 resolve 后仍在目标根内（防 "../" 或绝对路径穿越、
    防符号/硬链接指向根外），任一成员越界即抛 RuntimeError，不解包。
    """
    dest.mkdir(parents=True, exist_ok=True)
    dest_root = dest.resolve()
    with tarfile.open(archive, "r:*") as tar:
        members = tar.getmembers()
        for member in members:
            target = (dest / member.name).resolve()
            if not _within_root(dest_root, target):
                raise RuntimeError(f"archive member escapes destination root: {member.name!r}")
            if member.issym() or member.islnk():
                link_target = (target.parent / member.linkname).resolve()
                if not _within_root(dest_root, link_target):
                    raise RuntimeError(
                        f"archive link member points outside destination root: {member.name!r} -> {member.linkname!r}"
                    )
        try:
            tar.extractall(dest, filter="data")  # py3.12+ 安全过滤
        except TypeError:
            tar.extractall(dest)
    # 若解包后只有一个子目录且主 tex 在其中，则下钻一层
    entries = [item for item in dest.iterdir() if not item.name.startswith("__MACOSX")]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return dest


_ECCV_CLASS_GUARD = (
    "\\PackageError{eccv}{Package only meant to be used with document class `llncs'}"
    "{Change document class to `llncs'.}"
)
_ECCV_CLASS_GUARD_RELAXED = (
    "\\PackageWarningNoLine{eccv}"
    "{class check relaxed by PaperFit pack compat; document class kept as authors shipped}"
)


def apply_compat_fixes(work_dir: Path) -> List[str]:
    """打包期环境兼容：作者源码形态与编译环境冲突的最小修复，逐项返回名称。

    eccv_sty_class_check_relaxed：数据集里有 article+eccv.sty 的 arXiv 原始投稿形态，
    自带 sty 在 \\begin{document} 处硬性断言 llncs 类。只把错误降为警告——
    不改文档类、不改任何版式参数（改成 llncs 会整体重排，违背"冻结 B0"原则）。
    """
    fixes: List[str] = []
    sty = work_dir / "eccv.sty"
    if sty.is_file():
        try:
            text = sty.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if _ECCV_CLASS_GUARD in text:
            sty.write_text(text.replace(_ECCV_CLASS_GUARD, _ECCV_CLASS_GUARD_RELAXED), encoding="utf-8")
            fixes.append("eccv_sty_class_check_relaxed")
    return fixes


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
    status: str  # success / pack_copy_failed / injection_failed / compile_failed / render_failed / input_incomplete / builder_error
    injection_status: str
    reasons: List[str] = field(default_factory=list)
    case_dir: Optional[Path] = None
    stage_status: Dict[str, str] = field(default_factory=lambda: dict.fromkeys(STAGE_ORDER, "pending"))


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
    applied_perturbations: Optional[List[str]] = None,
    validated_perturbations: Optional[List[str]] = None,
    perturbation_source: Optional[str] = None,
) -> CaseBuildResult:
    "构建并冻结一个 B0 case。compile_fn(project_root) / render_fn(project_root, pages_dir)。"
    stage_status: Dict[str, str] = dict.fromkeys(STAGE_ORDER, "pending")
    result = CaseBuildResult(
        case_id=case_id,
        paper_id=paper_id,
        status="success",
        injection_status="success",
        stage_status=stage_status,
    )
    key = case_dir_key(case_id)
    case_dir = pack_root / paper_id / key
    work_dir = case_dir / "b0_project"
    pages_dir = case_dir / "pages"
    source_manifest: Dict[str, Any] = {
        "schema_version": PACK_SCHEMA_VERSION,
        "case_id": case_id,
        "paper_id": paper_id,
        "case_dir_key": key,
        "archive": str(archive),
        "archive_sha256": None,
        "injector_version": INJECTOR_VERSION,
        "seed": seed,
        "perturbation_ids": perturbation_ids,
        "generated_at": datetime.now().isoformat(),
    }

    def _fail(
        stage: str,
        status: str,
        injection_status: Optional[str] = None,
        reason: Optional[str] = None,
        error: Optional[BaseException] = None,
    ) -> CaseBuildResult:
        """阶段失败：标记 stage_status、写 source_manifest.json、返回失败终态。"""
        stage_status[stage] = "failed"
        for later_stage in STAGE_ORDER[STAGE_ORDER.index(stage) + 1:]:
            stage_status[later_stage] = "skipped"
        result.status = status
        if injection_status is not None:
            result.injection_status = injection_status
        if reason:
            result.reasons.append(reason)
        failure: Dict[str, Any] = {"stage": stage, "status": status, "reasons": list(result.reasons)}
        if error is not None:
            failure["error"] = f"{type(error).__name__}: {error}"
        source_manifest["stage_status"] = dict(stage_status)
        source_manifest["failure"] = failure
        try:
            _write_json(case_dir / "source_manifest.json", source_manifest)
        except OSError:
            # manifest 自身写不进（极端磁盘/权限问题）：终态仍在返回值与
            # build_pack 的 pack_manifest 里，不会静默丢 case
            pass
        result.case_dir = case_dir
        return result

    # ---- 阶段 copy：归档可读性 + 解包 + 冻结整包拷贝 ----
    try:
        source_manifest["archive_sha256"] = _sha256_file(archive)
    except OSError as exc:
        return _fail("copy", "input_incomplete", reason=f"archive unreadable: {archive}", error=exc)

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            project_root = extract_archive(Path(archive), Path(tmpdir) / "extracted")
        except (OSError, UnicodeError, RuntimeError) as exc:
            return _fail(
                "copy",
                "pack_copy_failed",
                reason=f"archive extraction failed: {archive}",
                error=exc,
            )

        # 冻结 B0 工程：整包拷贝（滤缓存），后续阶段都直接操作 work_dir
        try:
            if work_dir.exists():
                shutil.rmtree(work_dir)
            shutil.copytree(project_root, work_dir, ignore=_pack_copy_ignore)
        except (OSError, UnicodeError, RuntimeError) as exc:
            return _fail(
                "copy",
                "pack_copy_failed",
                reason="freezing B0 project copy failed (copytree)",
                error=exc,
            )
        stage_status["copy"] = "ok"

        # ---- 阶段 source_validation：定位主 tex + 扰动注入 + 环境兼容 ----
        try:
            main_tex = find_main_tex(work_dir)
            if main_tex is None:
                return _fail(
                    "source_validation",
                    "injection_failed",
                    injection_status="main_tex_not_found",
                    reason="no .tex with \\documentclass found in archive",
                )

            source_manifest["main_tex_relpath"] = str(main_tex.relative_to(work_dir))
            original_tex = main_tex.read_text(encoding="utf-8", errors="replace")
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
                return _fail(
                    "source_validation",
                    "injection_failed",
                    injection_status="perturbation_failed",
                    reason=f"failed perturbations: {', '.join(failed)}",
                )

            # 覆盖冻结工程里的主 tex 为扰动后版本
            main_tex.write_text(perturbed_tex, encoding="utf-8")
            source_manifest["perturbed_main_tex_sha256"] = _sha256_text(perturbed_tex)
        except (OSError, UnicodeError, RuntimeError) as exc:
            return _fail(
                "source_validation",
                "injection_failed",
                injection_status="injection_error",
                reason=f"injection stage failed: {type(exc).__name__}",
                error=exc,
            )
        stage_status["source_validation"] = "ok"

        # 环境兼容层：只处理"作者源码形态 vs 本机宏包"的冲突，不动任何版式参数，
        # 每项修复都记录进 manifest（可审计、可复现）
        try:
            compat_fixes = apply_compat_fixes(work_dir)
            if compat_fixes:
                source_manifest["compat_fixes"] = compat_fixes
        except (OSError, UnicodeError, RuntimeError) as exc:
            return _fail(
                "source_validation",
                "injection_failed",
                injection_status="injection_error",
                reason="compat fix stage failed",
                error=exc,
            )

        # ---- 阶段 compile：编译 B0 ----
        try:
            compile_report = compile_fn(work_dir)
            _write_json(case_dir / "compile.json", compile_report)
        except Exception as exc:  # compile_fn 是外部注入代码，按超集隔离
            return _fail(
                "compile",
                "compile_failed",
                reason=f"compile stage raised {type(exc).__name__}",
                error=exc,
            )
        if not compile_report.get("success"):
            return _fail("compile", "compile_failed", reason="B0 compile failed after perturbation")
        stage_status["compile"] = "ok"

        # ---- 阶段 render：渲染页图 ----
        try:
            if pages_dir.exists():
                shutil.rmtree(pages_dir)
            render_report = render_fn(work_dir, pages_dir)
            _write_json(case_dir / "render.json", render_report)
        except Exception as exc:  # render_fn 是外部注入代码，按超集隔离
            return _fail(
                "render",
                "render_failed",
                reason=f"render stage raised {type(exc).__name__}",
                error=exc,
            )
        if not render_report.get("success"):
            return _fail("render", "render_failed", reason="B0 render failed")
        stage_status["render"] = "ok"

        # ---- 阶段 ready：页图哈希 + 冻结 manifest + case 元数据 ----
        try:
            page_hashes = {
                page.name: _sha256_file(page) for page in sorted(pages_dir.glob("page_*.png"))
            }
            source_manifest["page_hashes"] = page_hashes
            source_manifest["page_count"] = len(page_hashes)
            
            
            if applied_perturbations is None:
                applied_perturbations = [
                    item.perturbation_id for item in injection_results if item.status == "success"
                ]
            if perturbation_source is None:
                perturbation_source = "dataset_b0" if skip_injection else "mvp_injector"
            source_manifest["perturbation_source"] = perturbation_source
            stage_status["ready"] = "ok"
            source_manifest["stage_status"] = dict(stage_status)
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
                        "applied_perturbations": list(applied_perturbations),
                        "validated_perturbations": list(validated_perturbations or []),
                        "selected_perturbation_source": perturbation_source,
                        "seed": seed,
                        "injector_version": INJECTOR_VERSION,
                        "frozen": True,
                    }
                },
            )
        except (OSError, UnicodeError, RuntimeError) as exc:
            return _fail("ready", "builder_error", reason="freezing case artifacts failed", error=exc)
        result.case_dir = case_dir
        return result


def _compose_pack_manifest(results: List[Dict[str, Any]], pack_root: Path) -> Dict[str, Any]:
    statuses = [item["status"] for item in results]
    summary: Dict[str, Any] = {
        "total": len(results),
        "success": statuses.count("success"),
        "pack_copy_failed": statuses.count("pack_copy_failed"),
        "injection_failed": statuses.count("injection_failed"),
        "compile_failed": statuses.count("compile_failed"),
        "render_failed": statuses.count("render_failed"),
        "input_incomplete": statuses.count("input_incomplete"),
        "builder_error": statuses.count("builder_error"),
    }
    return {
        "schema_version": PACK_SCHEMA_VERSION,
        "pack_root": str(pack_root),
        "injector_version": INJECTOR_VERSION,
        "generated_at": datetime.now().isoformat(),
        "case_results": results,
        "summary": summary,
        "frozen": True,
    }


def build_pack(
    *,
    entries: List[Dict[str, Any]],
    pack_root: Path,
    compile_fn: Callable[[Path], Dict[str, Any]],
    render_fn: Callable[[Path, Path], Dict[str, Any]],
    skip_injection: bool = False,
) -> Dict[str, Any]:
    """批量构建冻结 pack 并原子地持续更新 pack_manifest.json。

    entries: 见 build_case 参数（archive/paper_id/...）。
    单 case 的任何意外（含 build_case 自身 bug）都序列化进该 case 的
    终态（status=builder_error）后继续下一个 case，整批永不中止。
    """
    pack_root.mkdir(parents=True, exist_ok=True)
    results: List[Dict[str, Any]] = []

    def _flush() -> Dict[str, Any]:
        manifest = _compose_pack_manifest(results, pack_root)
        _atomic_write_json(pack_root / "pack_manifest.json", manifest)
        return manifest

    _flush()  # 空清单也先落盘：第 1 个 case 崩溃时 pack_manifest.json 已存在且合法
    for entry in entries:
        try:
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
                applied_perturbations=entry.get("applied_perturbations"),
                validated_perturbations=entry.get("validated_perturbations"),
                perturbation_source=entry.get("perturbation_source"),
            )
        except Exception as exc:  # 最后防线：单 case 的任何意外都不许终止整批
            case_id = str(entry.get("case_id") or "?")
            paper_id = str(entry.get("paper_id") or "?")
            build = CaseBuildResult(
                case_id=case_id,
                paper_id=paper_id,
                status="builder_error",
                injection_status="not_run",
                reasons=[f"{type(exc).__name__}: {exc}"],
                case_dir=pack_root / paper_id / case_dir_key(case_id),
            )
        results.append(
            {
                "case_id": build.case_id,
                "paper_id": build.paper_id,
                "case_dir_key": case_dir_key(build.case_id),
                "status": build.status,
                "injection_status": build.injection_status,
                "stage_status": dict(build.stage_status),
                "reasons": build.reasons,
            }
        )
        # 每完成一个 case 原子更新整批 manifest（临时文件 + os.replace）
        _flush()
    return _compose_pack_manifest(results, pack_root)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _atomic_write_json(path: Path, payload: Any) -> None:
    """临时文件 + os.replace 原子写：读者永远不会看到半个 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, str(path))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
