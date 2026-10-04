#!/usr/bin/env python3
"真实论文扰动注入器（回归 benchmark pack 的 B0 生成器组件）。"

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

INJECTOR_VERSION = "real-paper-injector-v1"

MVP_PERTURBATIONS = ("D1_long_token_overflow", "D2_long_formula", "B2_shrink_graphics", "C1_table_resizebox")

_LONG_TOKEN = "PerturbationStressToken" + "X" * 48  # 不可断行的 68 字符长词

_LONG_FORMULA = (
    "% PERTURBED: D2_long_formula\n"
    "\\begin{equation}\n"
    "f_{\\mathrm{perturb}}(x) = a_0 + a_1 x + a_2 x^2 + a_3 x^3 + a_4 x^4"
    " + a_5 x^5 + a_6 x^6 + a_7 x^7 + a_8 x^8 + a_9 x^9\n"
    "    + a_{10} x^{10} + a_{11} x^{11} + a_{12} x^{12} + a_{13} x^{13}\n"
    "\\end{equation}\n"
)


@dataclass
class InjectionResult:
    status: str  # success / target_not_found / already_perturbed
    perturbation_id: str
    edits: List[Dict[str, Any]] = field(default_factory=list)
    reason: Optional[str] = None


def _body_span(tex: str) -> Tuple[int, int]:
    begin = tex.find("\\begin{document}")
    if begin < 0:
        return (-1, -1)
    begin += len("\\begin{document}")
    end = tex.find("\\end{document}", begin)
    if end < 0:
        end = len(tex)
    return (begin, end)


def _first_paragraph_end(tex: str, body_start: int, body_end: int) -> Optional[int]:
    """返回正文里第一个'真段落'之后的偏移（跳过命令/环境行）。"""
    body = tex[body_start:body_end]
    environment_depth = 0
    for raw_line in body.splitlines(keepends=True):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("%"):
            continue
        if re.match(r"\\begin\{", stripped):
            environment_depth += 1
            continue
        if re.match(r"\\end\{", stripped):
            environment_depth = max(0, environment_depth - 1)
            continue
        if environment_depth > 0 or stripped.startswith("\\"):
            continue
        if len(stripped.split()) >= 6:
            return body_start + body.find(raw_line) + len(raw_line)
    return None


def inject_long_token(tex: str) -> Tuple[str, List[Dict[str, Any]], Optional[str]]:
    body_start, body_end = _body_span(tex)
    if body_start < 0:
        return (tex, [], "no \\begin{document}")
    insert_at = _first_paragraph_end(tex, body_start, body_end)
    if insert_at is None:
        return (tex, [], "no plain paragraph found in body")
    marker = f" % PERTURBED: D1_long_token_overflow\n"
    injected = f"{marker}{_LONG_TOKEN} "
    return (
        tex[:insert_at] + injected + tex[insert_at:],
        [{"perturbation_id": "D1_long_token_overflow", "kind": "long_token", "token_length": len(_LONG_TOKEN)}],
        None,
    )


def inject_long_formula(tex: str) -> Tuple[str, List[Dict[str, Any]], Optional[str]]:
    body_start, body_end = _body_span(tex)
    if body_start < 0:
        return (tex, [], "no \\begin{document}")
    insert_at = _first_paragraph_end(tex, body_start, body_end)
    if insert_at is None:
        insert_at = body_end
    return (
        tex[:insert_at] + "\n" + _LONG_FORMULA + tex[insert_at:],
        [{"perturbation_id": "D2_long_formula", "kind": "long_formula", "terms": 14}],
        None,
    )


_WIDTH_RE = re.compile(r"(\\includegraphics\s*\[[^\]]*?width\s*=\s*)([0-9]*\.?[0-9]+)(\\(?:line|column|text)width)")


def shrink_graphics_width(tex: str, *, factor: float = 0.5, max_images: int = 3) -> Tuple[str, List[Dict[str, Any]], Optional[str]]:
    edits: List[Dict[str, Any]] = []
    replaced = {"count": 0}

    def _shrink(match: "re.Match[str]") -> str:
        if replaced["count"] >= max_images:
            return match.group(0)
        replaced["count"] += 1
        original = float(match.group(2))
        shrunk = max(0.05, round(original * factor, 3))
        edits.append(
            {
                "perturbation_id": "B2_shrink_graphics",
                "kind": "width_shrink",
                "before": f"{original} {match.group(3)}",
                "after": f"{shrunk} {match.group(3)}",
            }
        )
        return f"{match.group(1)}{shrunk}{match.group(3)}"

    if "\\includegraphics" not in tex:
        return (tex, [], "no \\includegraphics found")
    updated = _WIDTH_RE.sub(_shrink, tex)
    if not edits:
        return (tex, [], "no width=<factor>\\...width includegraphics found")
    # 注入标记跟在第一处改动前不易定位，统一在文档头之后记录于 manifest
    return (updated, edits, None)


_TABULAR_RE = re.compile(r"(\\begin\{tabular\}\{[^}]+\}.*?\\end\{tabular\})", re.DOTALL)


def wrap_first_tabular_with_resizebox(tex: str) -> Tuple[str, List[Dict[str, Any]], Optional[str]]:
    if "\\resizebox" in tex:
        return (tex, [], "resizebox already present (refusing to double-wrap)")
    match = _TABULAR_RE.search(tex)
    if not match:
        return (tex, [], "no tabular environment found")
    tabular = match.group(1)
    if tabular.count("\\begin{tabular}") > 1:
        return (tex, [], "nested/greedy tabular match; refusing")
    wrapped = "\\resizebox{\\linewidth}{!}{%\n" + tabular + "}\n"
    updated = tex[: match.start()] + wrapped + tex[match.end():]
    return (
        updated,
        [{"perturbation_id": "C1_table_resizebox", "kind": "resizebox_wrap", "tabular_spec_head": tabular[:40]}],
        None,
    )


_HANDLERS = {
    "D1_long_token_overflow": inject_long_token,
    "D2_long_formula": inject_long_formula,
    "B2_shrink_graphics": shrink_graphics_width,
    "C1_table_resizebox": wrap_first_tabular_with_resizebox,
}


def apply_perturbations(tex: str, perturbation_ids: List[str]) -> Tuple[str, List[InjectionResult]]:
    """按给定顺序叠加扰动；任何一步失败即停止并保留已成功的部分。"""
    current = tex
    results: List[InjectionResult] = []
    for perturbation_id in perturbation_ids:
        handler = _HANDLERS.get(perturbation_id)
        if handler is None:
            results.append(
                InjectionResult(status="unsupported", perturbation_id=perturbation_id, reason="no handler")
            )
            continue
        updated, edits, reason = handler(current)
        if reason is not None:
            results.append(
                InjectionResult(
                    status="target_not_found",
                    perturbation_id=perturbation_id,
                    reason=reason,
                )
            )
            continue
        current = updated
        results.append(
            InjectionResult(status="success", perturbation_id=perturbation_id, edits=edits)
        )
    return current, results


def perturb_project_files(tex_files: Dict[str, str], perturbation_ids: List[str]) -> Tuple[Dict[str, str], List[InjectionResult]]:
    """多文件工程：扰动只落在主 tex（避免把 B0 的可追溯性打散到子文件）。"""
    main_name = max(tex_files, key=lambda name: len(tex_files[name])) if tex_files else None
    if main_name is None:
        return (tex_files, [InjectionResult(status="target_not_found", perturbation_id="all", reason="empty project")])
    perturbed, results = apply_perturbations(tex_files[main_name], perturbation_ids)
    updated = dict(tex_files)
    updated[main_name] = perturbed
    return updated, results
