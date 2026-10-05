#!/usr/bin/env python3
"""统一结构化编译结果（compile_result.v2）。

编译不再是 success=True/False 一个布尔：每次尝试（attempt）都有完整结构化
记录——命令、产物路径、PDF 新鲜度与哈希、失败类别（failure_class）、稳定
错误签名（error_signature）、源码变更集（changed_files）和修复动作
（repair_action）——并追加到 per-project 的尝试日志
data/compile/journal.jsonl，供恢复子系统做"同一错误不重复修"的判断。

失败类别词表（一、节）：
  timed_out / missing_package / missing_class / missing_font /
  missing_image / missing_input / undefined_control_sequence /
  template_mismatch / source_error / engine_error / unknown_failure

error_signature：首个错误的归一化指纹（去数字/路径/行号后 sha1 前 12 位），
同一根因跨尝试稳定，可用于去重与日志关联。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

COMPILE_RESULT_SCHEMA = "compile_result.v2"
JOURNAL_REL = "data/compile/journal.jsonl"
_ATTEMPT_DIR_RE = re.compile(r"^attempt-(\d+)$")

_ERROR_LINE_RE = re.compile(r"^! (.+)$", re.MULTILINE)

# 失败类别 → 判定正则（按优先级从上到下，首个命中即定类）
_FAILURE_PATTERNS: List[tuple[str, re.Pattern[str]]] = [
    ("template_mismatch", re.compile(r"only meant to be used with document class|option clash for document class|Class .* Error:")),
    ("missing_class", re.compile(r"Document class `[^']+' not found|LaTeX Error: File `[^']+\.cls' not found")),
    ("missing_package", re.compile(r"LaTeX Error: File `[^']+\.sty' not found|Package `[^']+' not found|`[^']+\.sty' not found")),
    ("missing_font", re.compile(r"The font `[^']+` cannot be found|Font \\[^=]+=.+not loadable|Font \\[^ ]+ at [\d.]+pt not found")),
    ("missing_image", re.compile(r"File `[^']+\.(?:pdf|png|jpg|jpeg|eps)' not found|Could not find figure|Error including.*file not found")),
    ("missing_input", re.compile(r"File `[^']+\.tex' not found|File `[^']+\.bib' not found|not found on input line")),
    ("undefined_control_sequence", re.compile(r"Undefined control sequence")),
]

_NORMALIZE_RE = re.compile(r"[\d.]+|[A-Za-z]:\\[^\s`']*|[~/][^\s`']*")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify_failure(log_text: str, *, success: bool, timed_out: bool, returncode: int) -> Optional[str]:
    """从编译日志/进程结果推断失败类别；成功返回 None。"""
    if success:
        return None
    if timed_out:
        return "timed_out"
    log = log_text or ""
    for failure_class, pattern in _FAILURE_PATTERNS:
        if pattern.search(log):
            return failure_class
    if "Emergency stop" in log or "==> Fatal error" in log:
        return "engine_error"
    if _ERROR_LINE_RE.search(log):
        return "source_error"
    return "unknown_failure"


def extract_first_error(log_text: str) -> Optional[str]:
    match = _ERROR_LINE_RE.search(log_text or "")
    return match.group(1).strip() if match else None


def error_signature(log_text: str) -> Optional[str]:
    """首个错误行的稳定指纹：去数字/绝对路径后 sha1[:12]。"""
    first_error = extract_first_error(log_text)
    if not first_error:
        return None
    normalized = _NORMALIZE_RE.sub("#", first_error)
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]


def _sha256_file(path: Path) -> Optional[str]:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _next_attempt_id(compile_root: Path) -> str:
    existing = [p.name for p in compile_root.glob("attempt-*") if p.is_dir()]
    highest = 0
    for name in existing:
        match = _ATTEMPT_DIR_RE.match(name)
        if match:
            highest = max(highest, int(match.group(1)))
    return f"attempt-{highest + 1}"


class CompileAttempt:
    """一次编译尝试的上下文：开始时创建，结束时 finalize 成 v2 结果。"""

    def __init__(self, project_root: Path, *, main_tex: str, compiler: str = "latexmk", attempt_id: Optional[str] = None):
        self.project_root = Path(project_root)
        self.main_tex = str(main_tex)
        self.compiler = compiler
        self.compile_root = self.project_root / "data" / "compile"
        self.compile_root.mkdir(parents=True, exist_ok=True)
        self.attempt_id = attempt_id or _next_attempt_id(self.compile_root)
        self.attempt_dir = self.compile_root / self.attempt_id
        self.attempt_dir.mkdir(parents=True, exist_ok=True)
        self.started_at = now_iso()

    def finalize(
        self,
        *,
        command: List[str],
        cwd: str,
        returncode: int,
        timed_out: bool,
        stdout: str = "",
        stderr: str = "",
        log_text: str = "",
        log_source: Optional[Path] = None,
        pdf_path: Optional[Path],
        success: bool,
        page_count: Optional[int] = None,
        changed_files: Optional[List[str]] = None,
        repair_action: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        finished_at = now_iso()

        # 产物归档：stdout/stderr/log 存进 attempt 目录（原始文件不动）
        stdout_path = self.attempt_dir / "stdout.txt"
        stderr_path = self.attempt_dir / "stderr.txt"
        log_path = self.attempt_dir / "compile.log"
        stdout_path.write_text(stdout or "", encoding="utf-8", errors="replace")
        stderr_path.write_text(stderr or "", encoding="utf-8", errors="replace")
        if log_source is not None and log_source.is_file():
            shutil.copy2(log_source, log_path)
        else:
            log_path.write_text(log_text or "", encoding="utf-8", errors="replace")

        combined_log = log_text or (log_source.read_text(encoding="utf-8", errors="replace") if log_source and log_source.is_file() else "")
        pdf = Path(pdf_path) if pdf_path else None
        pdf_exists = bool(pdf and pdf.is_file())
        pdf_fresh = False
        pdf_sha: Optional[str] = None
        if pdf_exists:
            pdf_sha = _sha256_file(pdf)
            try:
                pdf_fresh = pdf.stat().st_mtime >= datetime.now(timezone.utc).timestamp() - 86400
            except OSError:
                pdf_fresh = False
        if page_count is None and pdf_exists:
            try:
                from runtime_actions import pdf_page_count

                page_count = pdf_page_count(pdf)
            except Exception:
                page_count = None

        failure_class = classify_failure(
            combined_log, success=success, timed_out=timed_out, returncode=returncode
        )

        result: Dict[str, Any] = {
            "schema_version": COMPILE_RESULT_SCHEMA,
            "attempt_id": self.attempt_id,
            "main_tex": self.main_tex,
            "compiler": self.compiler,
            "command": list(command),
            "cwd": str(cwd),
            "started_at": self.started_at,
            "finished_at": finished_at,
            "returncode": int(returncode),
            "timed_out": bool(timed_out),
            "log_path": str(log_path.relative_to(self.project_root)) if log_path.is_relative_to(self.project_root) else str(log_path),
            "stdout_path": str(stdout_path.relative_to(self.project_root)),
            "stderr_path": str(stderr_path.relative_to(self.project_root)),
            "pdf_path": str(pdf) if pdf else None,
            "pdf_exists": pdf_exists,
            "pdf_fresh": pdf_fresh,
            "pdf_sha256": pdf_sha,
            "page_count": page_count,
            "failure_class": failure_class,
            "error_signature": error_signature(combined_log) if not success else None,
            "changed_files": list(changed_files or []),
            "repair_action": repair_action,
        }
        if extra:
            result.update(extra)

        self._append_journal(result)
        return result

    def _append_journal(self, result: Dict[str, Any]) -> None:
        journal = self.project_root / JOURNAL_REL
        try:
            with journal.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(result, ensure_ascii=False) + "\n")
        except OSError:
            pass


def source_fingerprint(project_root: Path, main_tex: str) -> Dict[str, str]:
    """当前源码指纹（file → sha256），用于计算 changed_files。"""
    try:
        from runtime_snapshots import discover_source_files

        root = Path(project_root)
        return {
            str(p.relative_to(root)): _sha256_file(p) or ""
            for p in discover_source_files(root, main_tex)
        }
    except Exception:
        return {}


def diff_source_fingerprints(before: Dict[str, str], after: Dict[str, str]) -> List[str]:
    """两次指纹间的变更文件列表（新增/删除/修改）。"""
    changed: List[str] = []
    for name, digest in after.items():
        if before.get(name) != digest:
            changed.append(name)
    for name in before:
        if name not in after:
            changed.append(name)
    return sorted(changed)
