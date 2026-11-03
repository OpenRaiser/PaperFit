#!/usr/bin/env python3
"""Snapshot and rollback metadata helpers for source-changing PaperFit tasks."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set


SOURCE_EXTENSIONS = {".tex", ".bib", ".bst", ".cls", ".sty", ".def"}
TEX_DEFINITION_EXTENSIONS = {".tex", ".cls", ".sty", ".def"}

# 快照根默认值（project_root 内相对路径）：不传 snapshot_root 且环境变量未设置时
# 保持历史行为，快照仍写入 <project_root>/data/snapshots。
SNAPSHOT_ROOT_DEFAULT = "data/snapshots"
# 外部快照根环境变量：harness 传入 run 级短外部根，使快照目录与被测论文目录解耦，
# 规避 Windows MAX_PATH=260（深 regression_runs/... 下 WinError 3）。
SNAPSHOT_ENV_VAR = "PAPERFIT_SNAPSHOT_ROOT"

# Windows MAX_PATH=260。写入前先做目标路径长度预检：
# - 默认（项目内）根下任一目标路径超过 220 字符 → 自动切换到短外部根（tempfile.mkdtemp 风格）；
# - 任何根下任一目标路径超过 240 字符 → 记 path_length_blocked 并让本次快照整体失败（不留半套）。
DEFAULT_ROOT_MAX_PATH_CHARS = 220
ABSOLUTE_MAX_PATH_CHARS = 240
MANIFEST_FILENAME = "snapshot_manifest.json"


class SnapshotPathLengthError(RuntimeError):
    """快照目标路径超过绝对上限：快照被整体拒绝（path_length_blocked），不留半套。"""

    def __init__(
        self,
        message: str,
        *,
        blocked_paths: List[str],
        max_path_chars: int = ABSOLUTE_MAX_PATH_CHARS,
    ) -> None:
        super().__init__(message)
        self.blocked_paths = list(blocked_paths)
        self.max_path_chars = max_path_chars

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path_length_blocked": True,
            "max_path_chars": self.max_path_chars,
            "blocked_paths": list(self.blocked_paths),
        }


def env_snapshot_root() -> Optional[str]:
    """读取 PAPERFIT_SNAPSHOT_ROOT（外部快照根）。未设置或为空时返回 None。"""
    value = os.environ.get(SNAPSHOT_ENV_VAR)
    if value is not None and value.strip():
        return value.strip()
    return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _project_relative(project_root: Path, path: Path) -> str:
    # 契约路径统一用正斜杠，保证 state/manifest JSON 跨平台一致。
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _short_snapshot_id(prefix: str = "pre_repair") -> str:
    # 目录名用短 hash 而非长时戳+完整文件名，避免在深路径下继续放大目标路径。
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _short_backup_name(rel_path: str) -> str:
    # 备份文件以 相对路径哈希 命名（保留原扩展名），不再镜像完整相对路径树。
    digest = hashlib.sha256(rel_path.encode("utf-8")).hexdigest()[:16]
    suffix = Path(rel_path).suffix or ".bin"
    return f"{digest}{suffix}"


def resolve_manifest_path(project_root: Path, rollback_target: str | Path) -> Path:
    """把 rollback_target 解析为 manifest 绝对路径：绝对路径原样使用，相对路径锚定 project_root。"""
    root = project_root.resolve()
    path = Path(rollback_target)
    return path if path.is_absolute() else root / path


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

    # Capture literal project-local declaration dependencies as actual
    # baselines too. Installed TeX libraries and unrelated project files are
    # excluded; constructed names and macro expansion are never guessed.
    from source_map import group, masked_tex
    text = masked_tex(_read_text(tex_path))
    text = re.sub(r"\\begin\s*\{filecontents\*?\}.*?\\end\s*\{filecontents\*?\}", "", text, flags=re.S)
    dependency = re.compile(r"\\(input|include|documentclass|usepackage|RequirePackage|LoadClass|LoadClassWithOptions|RequirePackageWithOptions)(?![A-Za-z@])")
    for match in dependency.finditer(text):
        pos = match.end()
        optional = group(text, pos, "[", "]")
        if optional:
            pos = optional[1]
        argument = group(text, pos)
        if argument is None:
            continue
        command, names = match[1], argument[0]
        extension = ".cls" if command in {"documentclass", "LoadClass", "LoadClassWithOptions"} else ".sty"
        for name in (names.split(",") if command in {"usepackage", "RequirePackage", "RequirePackageWithOptions"} else [names]):
            name = name.strip()
            if not name or re.search(r"[\\{}%#^~\x00]", name):
                continue
            if command in {"input", "include"}:
                child = _candidate_tex_path(project_root, tex_path.parent, name)
            else:
                reference = Path(name)
                if not reference.suffix:
                    reference = reference.with_suffix(extension)
                child = (tex_path.parent / reference).resolve()
                if not child.is_file():
                    child = (project_root / reference).resolve()
            if (child.is_file() and child.suffix.lower() in TEX_DEFINITION_EXTENSIONS
                    and child.resolve().is_relative_to(project_root.resolve())):
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


def _planned_target_paths(files_dir: Path, root: Path, sources: List[Path]) -> List[Path]:
    """写入前的目标路径预演：所有备份文件 + manifest 本身。"""
    planned: List[Path] = [files_dir / _short_backup_name(_project_relative(root, source)) for source in sources]
    planned.append(files_dir.parent / MANIFEST_FILENAME)
    return planned


def create_pre_repair_snapshot(
    *,
    project_root: Path,
    main_tex: str,
    snapshot_root: str | Path | None = None,
    snapshot_id: str | None = None,
) -> Dict[str, Any]:
    """创建修复前快照。

    snapshot_root 选择优先级：显式参数 > PAPERFIT_SNAPSHOT_ROOT 环境变量 > 项目内默认 data/snapshots。
    - 相对路径（含默认值）：拼在 project_root 下，历史行为不变；
    - 绝对路径：视为外部根，快照与被测论文目录解耦（rollback_target 记绝对路径）。
    路径长度预检：默认（项目内）根下目标路径 > 220 字符自动切短外部根（tempfile.mkdtemp 风格，
    测试可用 snapshot_root 参数或环境变量显式指定落点）；任何根下 > 240 字符抛
    SnapshotPathLengthError（path_length_blocked），快照整体失败、不留半套。
    """
    root = project_root.resolve()
    snapshot_id = snapshot_id or _short_snapshot_id()

    sources = discover_source_files(root, main_tex)

    if snapshot_root is not None:
        requested_root: Optional[str | Path] = snapshot_root
    else:
        requested_root = env_snapshot_root()
    base_input = requested_root if requested_root is not None else SNAPSHOT_ROOT_DEFAULT
    base_path = Path(str(base_input))
    base_is_external = base_path.is_absolute()
    base_dir = base_path if base_is_external else root / base_path

    snapshot_dir = base_dir / snapshot_id
    files_dir = snapshot_dir / "files"
    planned = _planned_target_paths(files_dir, root, sources)
    longest = max(len(str(path)) for path in planned)

    auto_external_root: Optional[str] = None
    if not base_is_external and longest > DEFAULT_ROOT_MAX_PATH_CHARS:
        # 默认（项目内）根放不下：自动切换到系统短外部根。生产走 tempfile.mkdtemp；
        # 测试/需要固定落点时应通过 snapshot_root 参数或 PAPERFIT_SNAPSHOT_ROOT 显式指定。
        auto_dir = Path(tempfile.mkdtemp(prefix="paperfit_snapshots_"))
        auto_external_root = str(auto_dir)
        snapshot_dir = auto_dir / snapshot_id
        files_dir = snapshot_dir / "files"
        planned = _planned_target_paths(files_dir, root, sources)
        longest = max(len(str(path)) for path in planned)

    blocked = [str(path) for path in planned if len(str(path)) > ABSOLUTE_MAX_PATH_CHARS]
    if blocked:
        raise SnapshotPathLengthError(
            "path_length_blocked: snapshot target path(s) exceed "
            f"{ABSOLUTE_MAX_PATH_CHARS} chars (longest={longest}); "
            "refusing to create a partial snapshot",
            blocked_paths=blocked,
        )

    snapshot_dir_existed = snapshot_dir.exists()
    entries: List[Dict[str, Any]] = []
    try:
        files_dir.mkdir(parents=True, exist_ok=True)
        for source in sources:
            rel = _project_relative(root, source)
            backup_name = _short_backup_name(rel)
            backup_path = files_dir / backup_name
            shutil.copy2(source, backup_path)
            stat_result = source.stat()
            entry: Dict[str, Any] = {
                "path": rel,
                "sha256": _sha256_file(source),
                "bytes": stat_result.st_size,
                "mtime": stat_result.st_mtime,
                # backup 相对 snapshot_dir（manifest 所在目录）；restore 以 manifest 为锚点解析，不猜路径。
                "backup": f"files/{backup_name}",
            }
            snapshot_dir_resolved = snapshot_dir.resolve()
            if snapshot_dir_resolved.is_relative_to(root):
                # 兼容旧清单消费方：项目内快照额外提供相对 project_root 的 backup_path。
                entry["backup_path"] = (
                    snapshot_dir_resolved.relative_to(root).joinpath("files", backup_name).as_posix()
                )
            entries.append(entry)
    except Exception:
        # 不许半套快照：任何一步失败都整体清掉本次快照目录（只清理本次新建的）。
        if not snapshot_dir_existed:
            shutil.rmtree(snapshot_dir, ignore_errors=True)
        if auto_external_root:
            shutil.rmtree(auto_external_root, ignore_errors=True)
        raise

    snapshot_dir_resolved = snapshot_dir.resolve()
    in_project = snapshot_dir_resolved.is_relative_to(root)
    if in_project:
        snapshot_dir_ref = snapshot_dir_resolved.relative_to(root).as_posix()
        rollback_target: str = (snapshot_dir_resolved.relative_to(root) / MANIFEST_FILENAME).as_posix()
    else:
        snapshot_dir_ref = str(snapshot_dir_resolved)
        rollback_target = str(snapshot_dir_resolved / MANIFEST_FILENAME)

    manifest = {
        "schema_version": "1.1",
        "snapshot_id": snapshot_id,
        "created_at": datetime.now().isoformat(),
        "project_root": str(root),
        "main_tex": main_tex,
        "snapshot_dir": snapshot_dir_ref,
        "rollback_target": rollback_target,
        "path_length_policy": {
            "default_root_max_chars": DEFAULT_ROOT_MAX_PATH_CHARS,
            "absolute_max_chars": ABSOLUTE_MAX_PATH_CHARS,
            "longest_target_chars": longest,
            "auto_external_root": auto_external_root,
            "external_snapshot_root": None if in_project else str(base_dir),
        },
        "files": entries,
    }
    manifest_path = snapshot_dir / MANIFEST_FILENAME
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def load_snapshot_manifest(project_root: Path, rollback_target: str | Path) -> Dict[str, Any]:
    root = project_root.resolve()
    manifest_path = resolve_manifest_path(root, rollback_target)
    if not manifest_path.is_file():
        raise FileNotFoundError(f"snapshot manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("snapshot manifest must be a JSON object")
    return manifest


def _resolve_manifest_snapshot_dir(
    manifest: Dict[str, Any], snapshot_dir: Optional[Path]
) -> Optional[Path]:
    """确定 manifest 所在快照目录：显式参数 > manifest 内声明的绝对 snapshot_dir。"""
    if snapshot_dir is not None:
        return snapshot_dir
    declared = manifest.get("snapshot_dir")
    if isinstance(declared, str) and declared and Path(declared).is_absolute():
        return Path(declared)
    return None


def _resolve_backup_file(
    project_root: Path, snapshot_dir: Optional[Path], item: Dict[str, Any]
) -> Optional[Path]:
    """按清单解析单个备份文件的位置。

    优先 entry["backup"]（相对 snapshot_dir，即 manifest 所在目录，外部快照也成立）；
    兼容旧清单 entry["backup_path"]（相对 project_root）。不字符串拼接猜测路径。
    """
    backup_rel = item.get("backup")
    if isinstance(backup_rel, str) and backup_rel and snapshot_dir is not None:
        candidate = Path(backup_rel)
        if not candidate.is_absolute() and ".." not in candidate.parts:
            return snapshot_dir / candidate
    legacy_rel = item.get("backup_path")
    if isinstance(legacy_rel, str) and legacy_rel:
        candidate = Path(legacy_rel)
        if not candidate.is_absolute() and ".." not in candidate.parts:
            return project_root / candidate
    return None


def validate_snapshot_manifest(
    project_root: Path,
    manifest: Dict[str, Any],
    *,
    snapshot_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    root = project_root.resolve()
    manifest_snapshot_dir = _resolve_manifest_snapshot_dir(manifest, snapshot_dir)
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
        if not isinstance(rel_path, str) or not rel_path:
            errors.append("snapshot file entry missing path")
            continue
        if Path(rel_path).is_absolute() or ".." in Path(rel_path).parts:
            errors.append(f"unsafe snapshot source path: {rel_path}")
            continue
        expected_sha = item.get("sha256")
        backup_path = _resolve_backup_file(root, manifest_snapshot_dir, item)
        if backup_path is None:
            errors.append(f"snapshot file entry missing resolvable backup location for {rel_path}")
            continue
        if not backup_path.is_file():
            errors.append(f"snapshot backup missing: {backup_path}")
            continue
        actual_sha = _sha256_file(backup_path)
        if isinstance(expected_sha, str) and expected_sha and actual_sha != expected_sha:
            errors.append(f"snapshot backup hash mismatch: {backup_path}")
        checked_files.append(
            {
                "path": rel_path,
                "backup_path": str(backup_path),
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
    manifest_path = resolve_manifest_path(root, rollback_target)
    manifest = load_snapshot_manifest(root, rollback_target)
    validation = validate_snapshot_manifest(root, manifest, snapshot_dir=manifest_path.parent)
    if not validation.get("valid"):
        raise ValueError("Invalid snapshot manifest: " + "; ".join(validation.get("errors") or []))

    restored: List[Dict[str, Any]] = []
    for item in manifest.get("files") or []:
        rel_path = str(item["path"])
        destination = root / rel_path
        backup = _resolve_backup_file(root, manifest_path.parent, item)
        if backup is None:
            raise ValueError(f"snapshot file entry has no resolvable backup: {rel_path}")
        before_sha = _sha256_file(destination) if destination.is_file() else None
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup, destination)
        after_sha = _sha256_file(destination)
        restored.append(
            {
                "path": rel_path,
                "backup": item.get("backup"),
                "backup_path": str(backup),
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
