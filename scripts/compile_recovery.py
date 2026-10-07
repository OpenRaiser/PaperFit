#!/usr/bin/env python3
"""编译恢复子系统：确定性、受控、可整体回滚的编译错误修复。

参考 NanoResearch latex/fixer.py 的两级策略思想，但按 PaperFit 边界收窄：
- 只做 Level 1（确定性修复）：Unicode 归一、命令→宏包注入（白名单）、
  重复环境折叠、\\ref/\\label/\\cite 参数内转义下划线还原、缺失
  \\end{document}、documentclass 前垃圾清理、错误行证实的缺失 \\end{env}。
- 不做 Level 2（LLM search-replace 写源码）——PaperFit 禁止模型直接写 LaTeX。
- 不做联网补 bib / 伪造参考文献。
- 恢复循环用 error_signature 去重（同一签名复现立即停，不无限重试）；
  全部尝试失败时恢复恢复前快照（all-or-nothing）。

每一步通过 compile_result.v2 的 repair_action 字段留痕。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from runtime_snapshots import discover_source_files
except ModuleNotFoundError:  # package import
    from .runtime_snapshots import discover_source_files

COMPILE_RECOVERY_SCHEMA = "compile_recovery.v1"

# 命令 → (宏包, usepackage 行)：只在系统已有该宏包时才有效（缺包属环境问题，
# 由 failure_class=missing_package/environment_ineligible 上报，不自动下载）
PACKAGE_FIXES: Dict[str, Tuple[str, str]] = {
    "\\multirow": ("multirow", "\\usepackage{multirow}"),
    "\\toprule": ("booktabs", "\\usepackage{booktabs}"),
    "\\midrule": ("booktabs", "\\usepackage{booktabs}"),
    "\\bottomrule": ("booktabs", "\\usepackage{booktabs}"),
    "\\FloatBarrier": ("placeins", "\\usepackage{placeins}"),
    "\\url{": ("url", "\\usepackage{url}"),
    "\\href{": ("hyperref", "\\usepackage{hyperref}"),
    "\\textcolor": ("xcolor", "\\usepackage{xcolor}"),
    "\\cellcolor": ("xcolor", "\\usepackage[table]{xcolor}"),
    "\\bm": ("bm", "\\usepackage{bm}"),
    "\\SI": ("siunitx", "\\usepackage{siunitx}"),
}

UNICODE_REPLACEMENTS: Dict[str, str] = {
    "\u2018": "`", "\u2019": "'", "\u201c": "``", "\u201d": "''",
    "\u2014": "---", "\u2013": "--", "\u2026": "\\ldots{}",
    "\u00d7": "$\\times$", "\u2264": "$\\leq$", "\u2265": "$\\geq$",
    "\u2260": "$\\neq$", "\u221e": "$\\infty$",
    "\u03b1": "$\\alpha$", "\u03b2": "$\\beta$", "\u03b3": "$\\gamma$",
    "\u03b4": "$\\delta$", "\u03bb": "$\\lambda$", "\u03c0": "$\\pi$",
    "\u03c3": "$\\sigma$", "\u2192": "$\\rightarrow$", "\u2190": "$\\leftarrow$",
    "\u00b1": "$\\pm$", "\u2248": "$\\approx$",
}

_ENV_NAMES = ("figure", "figure*", "table", "table*", "align", "align*",
              "equation", "equation*", "tabular", "tabular*", "abstract",
              "document", "itemize", "enumerate")

_ID_CMD_RE = re.compile(
    r"(\\(?:ref|eqref|autoref|nameref|pageref|label|cite[tp]?|citealp|citeauthor|citeyear))\{([^}]*)\}"
)


@dataclass
class RecoveryAction:
    strategy: str
    file: str
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"strategy": self.strategy, "file": self.file, "detail": self.detail}


@dataclass
class RecoveryResult:
    attempted: List[RecoveryAction] = field(default_factory=list)
    success: bool = False
    attempts: int = 0
    stopped_reason: str = ""
    rolled_back: bool = False
    final_compile: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": COMPILE_RECOVERY_SCHEMA,
            "success": self.success,
            "attempts": self.attempts,
            "stopped_reason": self.stopped_reason,
            "rolled_back": self.rolled_back,
            "actions": [a.to_dict() for a in self.attempted],
        }


def _write_atomic(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".recovery.tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def fix_unicode(tex: str) -> Tuple[str, List[str]]:
    changes: List[str] = []
    for char, repl in UNICODE_REPLACEMENTS.items():
        if char in tex:
            tex = tex.replace(char, repl)
            changes.append(f"U+{ord(char):04X}->{repl}")
    return tex, changes


def fix_escaped_identifiers(tex: str) -> Tuple[str, int]:
    count = 0

    def _fix(match: re.Match) -> str:
        nonlocal count
        arg = match.group(2)
        if "\\_" in arg:
            count += arg.count("\\_")
            repaired_arg = arg.replace("\\_", "_")
            return f"{match.group(1)}{{{repaired_arg}}}"
        return match.group(0)

    return _ID_CMD_RE.sub(_fix, tex), count


def fix_duplicate_environments(tex: str) -> Tuple[str, List[str]]:
    changes: List[str] = []
    for env in _ENV_NAMES:
        dup_begin = f"\\begin{{{env}}}\n\\begin{{{env}}}"
        while dup_begin in tex:
            tex = tex.replace(dup_begin, f"\\begin{{{env}}}", 1)
            changes.append(f"dup-begin-{env}")
        dup_end = f"\\end{{{env}}}\n\\end{{{env}}}"
        while dup_end in tex:
            tex = tex.replace(dup_end, f"\\end{{{env}}}", 1)
            changes.append(f"dup-end-{env}")
    return tex, changes


def fix_missing_end_document(tex: str) -> Tuple[str, bool]:
    if "\\begin{document}" in tex and "\\end{document}" not in tex:
        return tex + "\n\\end{document}\n", True
    return tex, False


def fix_junk_before_documentclass(tex: str) -> Tuple[str, bool]:
    match = re.search(r"\\documentclass[\[{]", tex)
    if match and match.start() > 0:
        prefix = tex[: match.start()]
        # 只看非注释行：整段都是 % 注释的前缀不算垃圾（NanoResearch 版会误删注释头）
        non_comment = "\n".join(
            line for line in prefix.splitlines() if not line.lstrip().startswith("%")
        )
        if re.search(r"[a-zA-Z]", non_comment):
            return tex[match.start():], True
    return tex, False


def fix_missing_package_for_command(tex: str, log_text: str) -> Tuple[str, List[str]]:
    """UCS 错误 + 命令白名单 → 导言区补 \\usepackage（仅当宏包未被加载）。"""
    if "undefined control sequence" not in (log_text or "").lower():
        return tex, []
    inserted: List[str] = []
    preamble_end = tex.find("\\begin{document}")
    if preamble_end <= 0:
        return tex, []
    preamble = tex[:preamble_end]
    for cmd, (pkg, use_line) in PACKAGE_FIXES.items():
        if cmd in tex and pkg not in preamble:
            tex = tex[:preamble_end] + use_line + "\n" + tex[preamble_end:]
            preamble_end += len(use_line) + 1
            preamble = tex[:preamble_end]
            inserted.append(use_line)
    return tex, inserted


def fix_missing_end_env_at_error(tex: str, log_text: str) -> Tuple[str, List[str]]:
    """错误行证实的缺失 \\end{env}：在 \\end{document} 前补齐（只补 begin 多于 end 的）。"""
    changes: List[str] = []
    lines = log_text or ""
    for env in _ENV_NAMES:
        begin_tag = f"\\begin{{{env}}}"
        end_tag = f"\\end{{{env}}}"
        if begin_tag in lines or end_tag in lines:
            if tex.count(begin_tag) > tex.count(end_tag):
                end_doc = tex.rfind("\\end{document}")
                if end_doc > 0:
                    tex = tex[:end_doc] + end_tag + "\n\n" + tex[end_doc:]
                    changes.append(f"add-end-{env}")
    return tex, changes


_ORPHAN_BEGIN_LOG_RE = re.compile(
    r"\\begin\{([a-zA-Z]+\*?)\} on input line (\d+) ended by \\end\{document\}"
)


def _active_part(line: str) -> str:
    """去掉注释部分后的活代码（不处理 % 转义 \\% 的边角，恢复场景足够）。"""
    stripped = line.lstrip()
    if stripped.startswith("%"):
        return ""
    return line.split("%", 1)[0].strip()


def fix_orphan_begin_env(tex: str, log_text: str) -> Tuple[str, List[str]]:
    "孤儿 \\begin{env}：配对的 \\end 被注释、区域无活内容 → 注释掉孤儿行和孤立 }。"
    changes: List[str] = []
    log = log_text or ""
    if not _ORPHAN_BEGIN_LOG_RE.search(log):
        return tex, changes
    lines = tex.splitlines(keepends=True)
    for match in _ORPHAN_BEGIN_LOG_RE.finditer(log):
        env, reported = match.group(1), int(match.group(2))
        begin_tag = f"\\begin{{{env}}}"
        # 行号可能有漂移（前序修复移动了行）：在报告行 ±10 内找实际含活 begin 的行
        idx = None
        for offset in range(-10, 11):
            probe = reported - 1 + offset
            if 0 <= probe < len(lines) and begin_tag in _active_part(lines[probe]):
                idx = probe
                break
        if idx is None:
            continue
        # 扫描到下一个活跃的环境边界（任何 \begin{...}/\end{...}）为止：
        # 孤儿领地在下一个环境开始处结束，边界本身不算"活内容"
        stray_brace_lines: List[int] = []
        has_active_content = False
        j = idx + 1
        while j < len(lines):
            active = _active_part(lines[j])
            if "\\begin{" in active or "\\end{" in active:
                break
            if active and active not in ("}", "{"):
                has_active_content = True
                break
            if active == "}":
                stray_brace_lines.append(j)
            j += 1
        if has_active_content:
            continue  # 区域有活内容：不敢动，交给 human_review
        lines[idx] = "% [paperfit-recovery] orphan begin commented: " + lines[idx]
        for brace_line in stray_brace_lines:
            lines[brace_line] = "% [paperfit-recovery] stray brace commented: " + lines[brace_line]
        changes.append(f"orphan-begin-{env}@{idx + 1}")
    return "".join(lines), changes


STRATEGY_ORDER = (
    "junk_before_documentclass",
    "missing_end_document",
    "duplicate_environments",
    "escaped_identifiers",
    "unicode",
    "missing_package_for_command",
    "missing_end_env_at_error",
)


def apply_deterministic_recovery(
    project_root: Path,
    main_tex: str,
    log_text: str,
    *,
    only_files: Optional[List[str]] = None,
) -> List[RecoveryAction]:
    """对项目源码应用一次确定性恢复扫描，返回实际执行的动作。"""
    root = Path(project_root)
    actions: List[RecoveryAction] = []
    sources = discover_source_files(root, main_tex)
    if only_files is not None:
        wanted = {str(root / name) for name in only_files}
        sources = [p for p in sources if str(p) in wanted]
    for source in sources:
        rel = str(source.relative_to(root))
        try:
            tex = source.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        original = tex
        if "junk_before_documentclass" in STRATEGY_ORDER:
            tex, changed = fix_junk_before_documentclass(tex)
            if changed:
                actions.append(RecoveryAction("junk_before_documentclass", rel))
        tex, changed = fix_missing_end_document(tex)
        if changed:
            actions.append(RecoveryAction("missing_end_document", rel))
        tex, dup = fix_duplicate_environments(tex)
        if dup:
            actions.append(RecoveryAction("duplicate_environments", rel, ",".join(dup[:5])))
        tex, n_ids = fix_escaped_identifiers(tex)
        if n_ids:
            actions.append(RecoveryAction("escaped_identifiers", rel, f"{n_ids} occurrences"))
        # Unicode 只在日志给出编码错误信号时动文本
        if re.search(r"inputenc|invalid character|unicode", (log_text or ""), re.IGNORECASE):
            tex, uni = fix_unicode(tex)
            if uni:
                actions.append(RecoveryAction("unicode", rel, ",".join(uni[:5])))
        tex, inserted = fix_missing_package_for_command(tex, log_text)
        if inserted:
            actions.append(RecoveryAction("missing_package_for_command", rel, ";".join(inserted)))
        tex, orphans = fix_orphan_begin_env(tex, log_text)
        if orphans:
            actions.append(RecoveryAction("orphan_begin_env", rel, ",".join(orphans)))
        tex, ends = fix_missing_end_env_at_error(tex, log_text)
        if ends:
            actions.append(RecoveryAction("missing_end_env_at_error", rel, ",".join(ends)))
        if tex != original:
            _write_atomic(source, tex)
    return actions


def recover_compile(
    project_root: Path,
    main_tex: str,
    *,
    compile_fn,
    snapshot_create=None,
    snapshot_restore=None,
    max_attempts: int = 3,
) -> RecoveryResult:
    """编译恢复循环：失败 → 确定性修复 → 重编译，签名去重，全败整体回滚。

    compile_fn(project_root, main_tex_path) -> compile_result.v2 dict
    """
    root = Path(project_root)
    result = RecoveryResult()
    main_path = root / main_tex

    first = compile_fn(root, main_path)
    result.attempts = 1
    result.final_compile = first
    if first.get("success"):
        result.success = True
        return result

    snapshot = None
    if snapshot_create is not None:
        try:
            snapshot = snapshot_create()
        except Exception:
            snapshot = None

    seen_signatures = {first.get("error_signature")}
    log_source = first.get("log_path")

    for attempt in range(2, max_attempts + 1):
        log_text = ""
        if log_source:
            log_path = root / str(log_source) if not Path(str(log_source)).is_absolute() else Path(str(log_source))
            if log_path.is_file():
                try:
                    log_text = log_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    log_text = ""
        actions = apply_deterministic_recovery(root, main_tex, log_text)
        if not actions:
            result.stopped_reason = "no_deterministic_fix_available"
            break
        result.attempted.extend(actions)

        nxt = compile_fn(root, main_path)
        result.attempts = attempt
        result.final_compile = nxt
        if nxt.get("success"):
            result.success = True
            result.stopped_reason = f"recovered_after_{attempt - 1}_fix_rounds"
            return result
        sig = nxt.get("error_signature")
        if sig in seen_signatures:
            result.stopped_reason = "same_error_signature_repeated"
            break
        seen_signatures.add(sig)
        log_source = nxt.get("log_path")

    if not result.success and snapshot is not None and snapshot_restore is not None and result.attempted:
        try:
            snapshot_restore(snapshot)
            result.rolled_back = True
        except Exception:
            result.rolled_back = False
    if not result.stopped_reason:
        result.stopped_reason = "max_attempts_exhausted" if result.attempted else "no_fix_applied"
    return result
