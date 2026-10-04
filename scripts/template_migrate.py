#!/usr/bin/env python3
"""
Minimal template migration helper for PaperFit.

Current scope:
- reliable single-column migration for AAAI-like sources
- compileable fallback target: PaperFitSingleColumn
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from transactional_patch import atomic_write_text
from template_registry import load_templates


REPO_ROOT = Path(__file__).resolve().parent.parent
CONFLICTING_STYLE_PACKAGES = [
    "acl",
    "acmart",
    "cvpr",
    "iccv",
    "eccv",
    "eccvabbrv",
    "aaai24",
    "aaai25",
    "aaai2026",
    "icml2024",
    "icml2025",
    "iclr2026_conference",
    "iclr2025_conference",
    "iclr2021_conference",
    "iclr2020_conference",
    "iclr2019_conference",
    "neurips_2024",
    "neurips_2023",
    "neurips_2025",
    "open_neurips",
]
CONFLICTING_INPUT_FILES = [
    "preamble",
]
SOURCE_FONT_PACKAGES = [
    "libertine",
    "newtxmath",
    "newtxtext",
    "txfonts",
    "pxfonts",
    "mathptmx",
    "mathpazo",
    "fourier",
    "inconsolata",
    "zi4",
]
PACKAGE_CONFLICTS_BY_TARGET = {
    "CVPR2026": ["eso-pic", "caption", "subcaption", "subfig", "subfigure", "lineno"],
    "ICCV2025": ["eso-pic", "caption", "subcaption", "subfig", "subfigure", "lineno"],
    "SIGIR2025": ["natbib"],
    "KDD2025": ["natbib"],
    "ICML2025": ["algorithm", "algorithm2e"],
}
CAMERA_READY_FORBIDDEN_STYLE_OPTIONS = {"review", "submission", "preprint", "anonymous", "pagenumbers"}


def detect_source_template(tex: str) -> str:
    packages = set(_iter_active_usepackages(tex))
    if {"aaai24", "aaai25", "aaai2026"} & packages:
        return "AAAI-like"
    if {"iclr2026_conference", "iclr2025_conference"} & packages:
        return "ICLR-like"
    if {"cvpr", "iccv"} & packages:
        return "CVF-like"
    if {"neurips_2025"} & packages:
        return "NeurIPS-like"
    if {"icml2024", "icml2025"} & packages:
        return "ICML-like"
    if {"acl"} & packages:
        return "ACL-like"
    docclass_match = re.search(r"(?m)^[ \t]*\\documentclass(?:\[[^\]]*\])?\{([^}]+)\}", tex)
    if docclass_match:
        return docclass_match.group(1)
    return "unknown"


def _iter_active_usepackages(tex: str) -> Iterable[str]:
    clean = re.sub(r"(?m)^[ \t]*%[^\n]*(?:\n|$)", "", tex)
    for match in re.finditer(r"\\usepackage(?:\[[^\]]*\])?\{([^}]+)\}", clean):
        for package_name in match.group(1).split(","):
            package_name = package_name.strip()
            if package_name:
                yield package_name


def replace_documentclass(tex: str, target_documentclass: str) -> str:
    pattern = re.compile(r"(?m)^[ \t]*\\documentclass(?:\[[^\]]*\])?\{[^}]+\}")
    return pattern.sub(lambda _match: target_documentclass, tex, count=1)


def remove_package(tex: str, package_name: str) -> str:
    pattern = re.compile(
        rf"^[ \t]*\\usepackage(?:\[[^\]]*\])?\{{[^}}]*\b{re.escape(package_name)}\b[^}}]*\}}[^\n]*\n?",
        re.MULTILINE,
    )
    return pattern.sub("", tex)


def remove_conflicting_template_style_lines(tex: str) -> str:
    updated = tex
    for package_name in CONFLICTING_STYLE_PACKAGES:
        updated = remove_package(updated, package_name)
    for input_name in CONFLICTING_INPUT_FILES:
        pattern = re.compile(
            rf"^[ \t]*\\input\{{{re.escape(input_name)}\}}[^\n]*\n?",
            re.MULTILINE,
        )
        updated = pattern.sub("", updated)
    updated = re.sub(
        r"^[ \t]*\\setlength\\titlebox\{[^}]+\}[^\n]*\n?",
        "",
        updated,
        flags=re.MULTILINE,
    )
    return updated


def remove_source_template_commands(tex: str) -> str:
    orphan_commands = [
        r"\\iclrfinalcopy",
        r"\\neuripsfinalcopy",
        r"\\aaai_finalcopy",
    ]
    updated = tex
    for command in orphan_commands:
        updated = re.sub(rf"^[ \t]*{command}[^\n]*\n?", "", updated, flags=re.MULTILINE)
    for command in [
        r"\titlerunning",
        r"\authorrunning",
        r"\icmltitlerunning",
        r"\runningtitle",
        r"\runningauthor",
    ]:
        updated = _remove_balanced_commands(updated, command, 1)
    return updated


def remove_source_font_packages(tex: str, target_name: str) -> str:
    """Drop source-template font packages so the target template owns fonts."""
    updated = tex
    for package_name in SOURCE_FONT_PACKAGES:
        updated = remove_package(updated, package_name)
    return updated


def remove_camera_ready_line_numbering(tex: str, target_name: str) -> str:
    updated = tex
    updated = remove_package(updated, "lineno")
    for command in [
        r"\linenumbers",
        r"\nolinenumbers",
        r"\pagewiselinenumbers",
        r"\runninglinenumbers",
        r"\switchlinenumbers",
    ]:
        updated = re.sub(rf"^[ \t]*{re.escape(command)}[^\n]*\n?", "", updated, flags=re.MULTILINE)
    return updated


def apply_removed_macro_policy(tex: str, target: Dict[str, Any]) -> str:
    macro_compatibility = target.get("macro_compatibility")
    if not isinstance(macro_compatibility, dict):
        return tex
    updated = tex
    for macro in macro_compatibility.get("removed_macros") or []:
        command = str(macro).strip()
        if not command.startswith("\\"):
            continue
        updated = _remove_balanced_commands(updated, command, 1)
        updated = re.sub(rf"^[ \t]*{re.escape(command)}[^\n]*\n?", "", updated, flags=re.MULTILINE)
    return updated


def remove_source_template_metadata(tex: str, target_name: str) -> str:
    if target_name.startswith("SIGIR") or target_name.startswith("KDD"):
        return tex
    updated = tex
    updated = re.sub(r"(?ms)^[ \t]*\\begin\{CCSXML\}.*?^[ \t]*\\end\{CCSXML\}[^\n]*\n?", "", updated)
    updated = re.sub(r"^[ \t]*\\ccsdesc(?:\[[^\]]*\])?\{[^}]*\}[^\n]*\n?", "", updated, flags=re.MULTILINE)
    updated = re.sub(r"^[ \t]*\\keywords\{[^}]*\}[^\n]*\n?", "", updated, flags=re.MULTILINE)
    updated = re.sub(r"^[ \t]*\\Description\{[^}]*\}[^\n]*\n?", "", updated, flags=re.MULTILINE)
    updated = re.sub(r"^[ \t]*\\printccsdesc[^\n]*\n?", "", updated, flags=re.MULTILINE)
    updated = re.sub(r"^[ \t]*\\renewcommand\{\\shortauthors\}\{[^}]*\}[^\n]*\n?", "", updated, flags=re.MULTILINE)
    updated = re.sub(r"^[ \t]*\\balance[^\n]*\n?", "", updated, flags=re.MULTILINE)
    updated = updated.replace(r"\begin{teaserfigure}", r"\begin{figure}")
    updated = updated.replace(r"\end{teaserfigure}", r"\end{figure}")
    updated = _convert_acm_author_metadata_for_article(updated)
    for command, arg_count in [
        (r"\copyrightyear", 1),
        (r"\acmYear", 1),
        (r"\setcopyright", 1),
        (r"\acmConference", 3),
        (r"\acmBooktitle", 1),
        (r"\acmDOI", 1),
        (r"\acmISBN", 1),
        (r"\settopmatter", 1),
        (r"\orcid", 1),
        (r"\authornote", 1),
        (r"\affiliation", 1),
        (r"\email", 1),
    ]:
        updated = _remove_balanced_commands(updated, command, arg_count)
    updated = re.sub(r"^[ \t]*\\authornotemark(?:\[[^\]]*\])?[^\n]*\n?", "", updated, flags=re.MULTILINE)
    return updated


def unwrap_texorpdfstring_commands(tex: str) -> str:
    cursor = 0
    pieces: List[str] = []
    changed = False
    command = r"\texorpdfstring"
    while True:
        idx = tex.find(command, cursor)
        if idx < 0:
            pieces.append(tex[cursor:])
            break
        if _is_commented_position(tex, idx):
            pieces.append(tex[cursor : idx + len(command)])
            cursor = idx + len(command)
            continue
        parsed = _parse_required_braced_args(tex, idx + len(command), 2)
        if parsed is None:
            pieces.append(tex[cursor : idx + len(command)])
            cursor = idx + len(command)
            continue
        end_idx, args = parsed
        pieces.append(tex[cursor:idx])
        pieces.append(args[0])
        cursor = end_idx
        changed = True
    return "".join(pieces) if changed else tex


def normalize_theorem_labels(tex: str) -> str:
    theorem_envs = "theorem|lemma|proposition|corollary|definition|remark"
    pattern = re.compile(
        rf"(?m)^[ \t]*\\begin\{{({theorem_envs})\}}(?:\[[^\]]*\])?.*?^[ \t]*\\end\{{\1\}}",
        re.DOTALL,
    )

    def _normalize_block(match: re.Match[str]) -> str:
        block = match.group(0)
        label_match = re.search(r"^[ \t]*\\label\{[^}]+\}[^\n]*\n?", block, flags=re.MULTILINE)
        if not label_match:
            return block
        begin_match = re.search(r"\\begin\{[^}]+\}(?:\[[^\]]*\])?", block)
        if not begin_match:
            return block
        label_line = label_match.group(0)
        without_label = block[: label_match.start()] + block[label_match.end():]
        return without_label[: begin_match.end()] + "\n" + label_line + without_label[begin_match.end():]

    return pattern.sub(_normalize_block, tex)


def normalize_article_maketitle_order(tex: str, target: Dict[str, Any]) -> str:
    documentclass = str(target.get("documentclass") or "")
    if "{article}" not in documentclass:
        return tex

    abstract_match = re.search(r"(?m)^[ \t]*\\begin\{abstract\}", tex)
    maketitle_match = re.search(r"(?m)^[ \t]*\\maketitle[ \t]*(?:%[^\n]*)?\n?", tex)
    if not abstract_match or not maketitle_match:
        return tex
    if maketitle_match.start() < abstract_match.start():
        return tex

    maketitle_line = maketitle_match.group(0)
    without_maketitle = tex[: maketitle_match.start()] + tex[maketitle_match.end():]
    abstract_match = re.search(r"(?m)^[ \t]*\\begin\{abstract\}", without_maketitle)
    if not abstract_match:
        return tex
    insertion = maketitle_line
    if not insertion.endswith("\n"):
        insertion += "\n"
    if abstract_match.start() > 0 and without_maketitle[abstract_match.start() - 1] != "\n":
        insertion = "\n" + insertion
    return without_maketitle[: abstract_match.start()] + insertion + without_maketitle[abstract_match.start():]


def _remove_balanced_commands(tex: str, command: str, arg_count: int) -> str:
    cursor = 0
    pieces: List[str] = []
    changed = False
    while True:
        idx = tex.find(command, cursor)
        if idx < 0:
            pieces.append(tex[cursor:])
            break
        if _is_commented_position(tex, idx):
            pieces.append(tex[cursor : idx + len(command)])
            cursor = idx + len(command)
            continue
        parsed = _parse_command_call(tex, idx + len(command), arg_count)
        if parsed is None:
            pieces.append(tex[cursor : idx + len(command)])
            cursor = idx + len(command)
            continue
        end_idx = parsed
        if end_idx < len(tex) and tex[end_idx : end_idx + 1] == "\n":
            end_idx += 1
        pieces.append(tex[cursor:idx])
        cursor = end_idx
        changed = True
    return "".join(pieces) if changed else tex


def _parse_command_call(tex: str, start_idx: int, required_arg_count: int) -> Optional[int]:
    idx = start_idx
    while True:
        while idx < len(tex) and tex[idx].isspace():
            idx += 1
        if idx >= len(tex) or tex[idx] != "[":
            break
        close_idx = tex.find("]", idx + 1)
        if close_idx < 0:
            return None
        idx = close_idx + 1
    parsed = _parse_required_braced_args(tex, idx, required_arg_count)
    if parsed is None:
        return None
    end_idx, _args = parsed
    return end_idx


def _find_all_command_blocks(tex: str, command: str) -> List[Tuple[int, int, str]]:
    blocks: List[Tuple[int, int, str]] = []
    cursor = 0
    pattern = re.compile(rf"\\{re.escape(command)}\s*\{{")
    while True:
        match = pattern.search(tex, cursor)
        if not match:
            break
        if _is_commented_position(tex, match.start()):
            cursor = match.end()
            continue
        brace_start = tex.find("{", match.start())
        close_idx = _find_matching_brace(tex, brace_start)
        if close_idx is None:
            cursor = match.end()
            continue
        blocks.append((match.start(), close_idx + 1, tex[brace_start + 1 : close_idx]))
        cursor = close_idx + 1
    return blocks


def _strip_author_name_for_article(content: str) -> str:
    text = " ".join(content.split())
    text = re.sub(r"\\thanks\{[^}]*\}", "", text)
    text = re.sub(r"\\authornote\{[^}]*\}", "", text)
    text = re.sub(r"\\authornotemark(?:\[[^\]]*\])?", "", text)
    text = re.sub(r"\\orcid\{[^}]*\}", "", text)
    return text.strip().strip(",")


def _convert_acm_author_metadata_for_article(tex: str) -> str:
    author_blocks = _find_all_command_blocks(tex, "author")
    if len(author_blocks) < 2:
        return tex
    names = [_strip_author_name_for_article(content) for _start, _end, content in author_blocks]
    names = [name for name in names if name]
    if len(names) < 2:
        return tex
    emails = [content.strip() for _start, _end, content in _find_all_command_blocks(tex, "email") if content.strip()]

    updated = tex
    for command, arg_count in [
        (r"\author", 1),
        (r"\orcid", 1),
        (r"\authornote", 1),
        (r"\affiliation", 1),
        (r"\email", 1),
    ]:
        updated = _remove_balanced_commands(updated, command, arg_count)
    updated = re.sub(r"^[ \t]*\\authornotemark(?:\[[^\]]*\])?[^\n]*\n?", "", updated, flags=re.MULTILINE)

    body_lines = [", ".join(names)]
    if emails:
        body_lines.append(r"\\")
        body_lines.append(r"\texttt{" + ", ".join(emails) + "}")
    author_block = "\\author{\n  " + "\n  ".join(body_lines) + "\n}\n"

    title_block = _find_command_block(updated, "title")
    if title_block:
        _start, end, _content = title_block
        insert_at = end + (1 if end < len(updated) and updated[end : end + 1] == "\n" else 0)
        return updated[:insert_at] + author_block + updated[insert_at:]
    begin_doc = re.search(r"^\\begin\{document\}", updated, re.MULTILINE)
    insert_at = begin_doc.end() if begin_doc else 0
    return updated[:insert_at] + "\n" + author_block + updated[insert_at:]


def ensure_target_header_shims(tex: str, target_name: str) -> str:
    if target_name not in {"CVPR2026", "ICCV2025"}:
        return tex
    if r"\confName" in tex and r"\def\confName" in tex:
        return tex
    match = re.search(r"(?m)(^[ \t]*\\documentclass(?:\[[^\]]*\])?\{[^}]*\}[^\n]*\n?)", tex)
    if not match:
        return tex
    conf_name = "ICCV" if target_name.startswith("ICCV") else "CVPR"
    year_match = re.search(r"(\d{4})", target_name)
    conf_year = year_match.group(1) if year_match else ""
    shim = (
        f"\\providecommand{{\\confName}}{{{conf_name}}}"
        f"\\providecommand{{\\confYear}}{{{conf_year}}}"
        "\\providecommand{\\paperID}{}\n"
    )
    return tex[: match.end()] + shim + tex[match.end():]


def ensure_package_line(tex: str, package_name: str) -> str:
    if _has_active_package(tex, package_name):
        return tex
    match = re.search(r"^\\documentclass[^\n]*\n?", tex, re.MULTILINE)
    insert_at = match.end() if match else 0
    return tex[:insert_at] + f"\\usepackage{{{package_name}}}\n" + tex[insert_at:]


def ensure_package_line_before_document(tex: str, package_name: str, options: Optional[str] = None) -> str:
    if _has_active_package(tex, package_name):
        return tex
    package_line = rf"\usepackage{f'[{options}]' if options else ''}{{{package_name}}}" + "\n"
    if package_name in {"amsmath", "amssymb"}:
        dependency_match = re.search(
            r"^[ \t]*\\usepackage(?:\[[^\]]*\])?\{[^}]*\b(?:cleveref|hyperref)\b[^}]*\}[^\n]*\n?",
            tex,
            flags=re.MULTILINE,
        )
        if dependency_match:
            return tex[: dependency_match.start()] + package_line + tex[dependency_match.start():]
    begin_doc = re.search(r"^\\begin\{document\}", tex, re.MULTILINE)
    insert_at = begin_doc.start() if begin_doc else len(tex)
    return tex[:insert_at] + package_line + tex[insert_at:]


def ensure_common_content_packages(tex: str, target_name: str) -> str:
    updated = tex
    if any(token in updated for token in [r"\begin{align", r"\begin{equation", r"\operatorname", r"\boldsymbol"]):
        updated = ensure_package_line_before_document(updated, "amsmath")
    if any(token in updated for token in [r"\mathbb", r"\mathfrak", r"\varnothing"]):
        updated = ensure_package_line_before_document(updated, "amssymb")
    if r"\includegraphics" in updated:
        updated = ensure_package_line_before_document(updated, "graphicx")
    if any(token in updated for token in [r"\toprule", r"\midrule", r"\bottomrule"]):
        updated = ensure_package_line_before_document(updated, "booktabs")
    if r"\multirow" in updated:
        updated = ensure_package_line_before_document(updated, "multirow")
    if any(token in updated for token in [r"\definecolor", r"\rowcolor", r"\cellcolor", r"\textcolor"]):
        updated = ensure_package_line_before_document(updated, "xcolor")
    if target_name in {"CVPR2026", "ICCV2025"}:
        updated = ensure_package_line_before_document(
            updated,
            "hyperref",
            options="breaklinks,colorlinks",
        )
    return updated


def remove_target_package_conflicts(tex: str, target_name: str) -> str:
    updated = tex
    for package_name in PACKAGE_CONFLICTS_BY_TARGET.get(target_name, []):
        updated = remove_package(updated, package_name)
    return updated


def adapt_project_floats_for_target(
    output_path: Path,
    *,
    target_column_type: str,
) -> List[str]:
    if target_column_type not in {"double", "single"}:
        return []
    changes: List[str] = []
    project_root = output_path.parent
    tex_paths = sorted(
        path for path in project_root.rglob("*.tex")
        if "data" not in path.relative_to(project_root).parts
        and not path.name.startswith("._")
    )
    for tex_path in tex_paths:
        try:
            before = tex_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if target_column_type == "double":
            after, file_changes = adapt_tex_floats_for_double_column(before)
        else:
            after, file_changes = adapt_tex_floats_for_single_column(before)
        if after != before:
            atomic_write_text(tex_path, after)
            changes.extend(f"{tex_path.relative_to(project_root).as_posix()}: {item}" for item in file_changes)
    return changes


def adapt_tex_floats_for_double_column(tex: str) -> Tuple[str, List[str]]:
    changes: List[str] = []
    updated = tex

    def _promote_wide_float(match: re.Match[str]) -> str:
        block = match.group(0)
        env = match.group(1)
        if env.endswith("*"):
            return block
        if not _float_block_needs_double_column_width(block, env):
            return block
        promoted_env = env + "*"
        new_block = re.sub(
            rf"\\begin\{{{env}\}}(?:\[[^\]]*\])?",
            rf"\\begin{{{promoted_env}}}[t]",
            block,
            count=1,
        )
        end_pattern = rf"\end{{{env}}}"
        end_idx = new_block.rfind(end_pattern)
        if end_idx >= 0:
            new_block = new_block[:end_idx] + rf"\end{{{promoted_env}}}" + new_block[end_idx + len(end_pattern):]
        new_block = _normalize_wide_float_widths(new_block)
        return new_block

    float_pattern = re.compile(r"(?m)^[ \t]*\\begin\{(figure|table)\}(?:\[[^\]]*\])?.*?^[ \t]*\\end\{\1\}", re.DOTALL)
    promoted = float_pattern.sub(_promote_wide_float, updated)
    if promoted != updated:
        updated = promoted
        changes.append("promoted textwidth floats to starred environments")

    resized = unwrap_forbidden_scaling_commands(updated)
    if resized != updated:
        updated = resized
        changes.append("unwrapped resizebox/scalebox table scaling")

    math_adapted = adapt_wide_display_math_for_double_column(updated)
    if math_adapted != updated:
        updated = math_adapted
        changes.append("promoted wide display math to two-column strip")

    tightened = tighten_wide_tables_for_double_column(updated)
    if tightened != updated:
        updated = tightened
        changes.append("tightened wide tables without forbidden scaling")

    return updated, changes


def adapt_tex_floats_for_single_column(tex: str) -> Tuple[str, List[str]]:
    changes: List[str] = []
    updated = normalize_star_floats_for_single_column(tex)
    if updated != tex:
        changes.append("normalized figure*/table* to figure/table")

    resized = unwrap_forbidden_scaling_commands(updated)
    if resized != updated:
        updated = resized
        changes.append("unwrapped resizebox/scalebox table scaling")

    tightened = tighten_wide_tables_for_single_column(updated)
    if tightened != updated:
        updated = tightened
        changes.append("tightened wide tables without forbidden scaling")

    return updated, changes


def _float_block_needs_double_column_width(block: str, env: str) -> bool:
    if r"\textwidth" in block:
        return True
    if env == "table":
        tabular_match = re.search(r"\\begin\{tabularx?\}(?:\{[^}]+\})?(\{[^}]+\})", block)
        if tabular_match:
            column_count = _count_top_level_columns(tabular_match.group(1))
            longest_line = max((len(line.strip()) for line in block.splitlines()), default=0)
            if column_count >= 5 or longest_line >= 110:
                return True
    return False


def _normalize_wide_float_widths(block: str) -> str:
    updated = block
    updated = updated.replace(r"\linewidth", r"\textwidth")
    updated = updated.replace(r"\columnwidth", r"\textwidth")
    updated = re.sub(
        r"(\\includegraphics(?:\[[^\]]*)?width=)\\(?:line|column)width",
        r"\1\\textwidth",
        updated,
    )
    updated = re.sub(
        r"(\\begin\{tabularx\})\{\\(?:line|column|text)width\}",
        r"\1{\\textwidth}",
        updated,
    )
    return updated


def unwrap_forbidden_scaling_commands(tex: str) -> str:
    r"""Remove resizebox/scalebox wrappers while preserving their braced body.

    The migration path must not rely on brute-force table scaling.  A plain
    regex is unsafe here because table bodies often contain nested braces,
    \multicolumn, colors, and math.  This parser only unwraps commands whose
    required braced arguments are balanced.
    """
    updated = tex
    for command, body_arg_index in ((r"\resizebox", 2), (r"\scalebox", 1)):
        updated = _unwrap_balanced_command(updated, command, body_arg_index)
    return updated


def _unwrap_balanced_command(tex: str, command: str, body_arg_index: int) -> str:
    cursor = 0
    pieces: List[str] = []
    changed = False
    while True:
        idx = tex.find(command, cursor)
        if idx < 0:
            pieces.append(tex[cursor:])
            break
        parsed = _parse_required_braced_args(tex, idx + len(command), body_arg_index + 1)
        if parsed is None:
            pieces.append(tex[cursor : idx + len(command)])
            cursor = idx + len(command)
            continue
        end_idx, args = parsed
        body = args[body_arg_index]
        pieces.append(tex[cursor:idx])
        pieces.append(body)
        cursor = end_idx
        changed = True
    return "".join(pieces) if changed else tex


def _parse_required_braced_args(tex: str, start_idx: int, count: int) -> Optional[Tuple[int, List[str]]]:
    args: List[str] = []
    idx = start_idx
    for _ in range(count):
        while idx < len(tex) and tex[idx].isspace():
            idx += 1
        if idx >= len(tex) or tex[idx] != "{":
            return None
        close_idx = _find_matching_brace(tex, idx)
        if close_idx is None:
            return None
        args.append(tex[idx + 1 : close_idx])
        idx = close_idx + 1
    return idx, args


def _find_matching_brace(tex: str, open_idx: int) -> Optional[int]:
    if open_idx >= len(tex) or tex[open_idx] != "{":
        return None
    depth = 0
    escaped = False
    for idx in range(open_idx, len(tex)):
        char = tex[idx]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return idx
    return None


def adapt_wide_display_math_for_double_column(tex: str) -> str:
    updated = tex
    pattern = re.compile(
        r"\\begin\{(equation\*?|empheq)\}(?:\[[^\]]*\])?(?:\{[^}]*\})?.*?\\end\{\1\}",
        re.DOTALL,
    )

    def _wrap_if_wide(match: re.Match[str]) -> str:
        block = match.group(0)
        if _is_commented_position(updated, match.start()):
            return block
        if _already_inside_strip(updated, match.start()):
            return block
        if not _display_math_needs_strip(block):
            return block
        return "\\begin{strip}\n" + block + "\n\\end{strip}"

    adapted = pattern.sub(_wrap_if_wide, updated)
    return adapted


def _is_commented_position(tex: str, idx: int) -> bool:
    line_start = tex.rfind("\n", 0, idx) + 1
    line_prefix = tex[line_start:idx]
    escaped = False
    for char in line_prefix:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "%":
            return True
    return False


def _already_inside_strip(tex: str, idx: int) -> bool:
    before = tex[:idx]
    return before.rfind(r"\begin{strip}") > before.rfind(r"\end{strip}")


def _display_math_needs_strip(block: str) -> bool:
    compact = re.sub(r"\s+", "", block)
    longest_line = max((len(line.strip()) for line in block.splitlines()), default=0)
    if longest_line >= 95 or len(compact) >= 190:
        return True
    wide_tokens = [
        r"\begin{bmatrix}",
        r"\bigoplus",
        r"\operatorname{Att}",
        r"\mathbb{E}_{x\sim",
        r"\tilde{\alpha}",
        r"\Omega_{\text{think}}",
        r"\operatorname{clip}",
    ]
    return any(token in block for token in wide_tokens)


def _extract_package_names(line: str) -> List[str]:
    match = re.search(r"\\usepackage(?:\[[^\]]*\])?\{([^}]+)\}", line)
    if not match:
        return []
    return [part.strip() for part in match.group(1).split(",") if part.strip()]


def _has_active_package(tex: str, package_name: str) -> bool:
    pattern = re.compile(
        rf"^[ \t]*\\usepackage(?:\[[^\]]*\])?\{{[^}}]*\b{re.escape(package_name)}\b[^}}]*\}}",
        re.MULTILINE,
    )
    return bool(pattern.search(tex))


def _has_active_input(tex: str, input_name: str) -> bool:
    pattern = re.compile(rf"^[ \t]*\\input\{{{re.escape(input_name)}\}}", re.MULTILINE)
    return bool(pattern.search(tex))


def ensure_required_preamble_lines(tex: str, required_lines: list[str]) -> str:
    if not required_lines:
        return tex
    missing: List[str] = []
    for line in required_lines:
        packages = _extract_package_names(line)
        if packages:
            if all(_has_active_package(tex, package_name) for package_name in packages):
                continue
            missing.append(line)
            continue

        input_match = re.search(r"\\input\{([^}]+)\}", line)
        if input_match:
            if _has_active_input(tex, input_match.group(1)):
                continue
            missing.append(line)
            continue

        pattern = re.compile(rf"^[ \t]*{re.escape(line)}[ \t]*$", re.MULTILINE)
        if pattern.search(tex):
            continue
        missing.append(line)
    if not missing:
        return tex

    docclass_match = re.search(r"^\\documentclass[^\n]*\n?", tex, re.MULTILINE)
    insert_at = docclass_match.end() if docclass_match else 0
    block = "".join(f"{line}\n" for line in missing)
    if insert_at and insert_at < len(tex) and tex[insert_at] != "\n":
        block = "\n" + block
    return tex[:insert_at] + block + tex[insert_at:]


def _target_style_package_options(target: Dict[str, Any]) -> Dict[str, Optional[List[str]]]:
    """Return camera-ready options for target style packages only."""
    style_packages = set(CONFLICTING_STYLE_PACKAGES)
    for filename in (target.get("official_assets") or {}).get("class_files", []) or []:
        style_packages.add(Path(str(filename)).stem)

    options_by_package: Dict[str, Optional[List[str]]] = {}
    for line in target.get("required_preamble_lines") or []:
        match = re.search(r"\\usepackage(?:\[([^\]]*)\])?\{([^}]+)\}", str(line))
        if not match:
            continue
        options = [
            part.strip()
            for part in _split_top_level_commas(match.group(1) or "")
            if part.strip()
        ]
        for package_name in _extract_package_names(str(line)):
            if package_name in style_packages:
                options_by_package[package_name] = options

    for package_name in target.get("required_packages") or []:
        package = str(package_name).strip()
        if package in style_packages and package not in options_by_package:
            options_by_package[package] = None
    return options_by_package


def normalize_camera_ready_template_options(tex: str, target: Dict[str, Any]) -> str:
    """Force target conference style packages out of review/submission modes."""
    options_by_package = _target_style_package_options(target)
    if not options_by_package:
        return tex

    def _replace(match: re.Match[str]) -> str:
        leading = match.group(1)
        raw_options = match.group(2)
        package_block = match.group(3)
        suffix = match.group(4) or ""
        packages = [part.strip() for part in package_block.split(",") if part.strip()]
        matched = [package for package in packages if package in options_by_package]
        if not matched or len(packages) != 1:
            return match.group(0)

        package = matched[0]
        desired_options = options_by_package.get(package)
        current_options = [
            part.strip()
            for part in _split_top_level_commas(raw_options or "")
            if part.strip()
        ]
        if desired_options is None:
            desired_options = [
                option
                for option in current_options
                if option.strip().split("=", 1)[0].lower() not in CAMERA_READY_FORBIDDEN_STYLE_OPTIONS
            ]
        option_part = f"[{','.join(desired_options)}]" if desired_options else ""
        return f"{leading}{option_part}{{{package_block}}}{suffix}"

    return re.sub(
        r"(?m)^([ \t]*\\usepackage)(?:\[([^\]]*)\])?\{([^}]+)\}([^\n]*)",
        _replace,
        tex,
    )


def ensure_iclr_camera_ready_copy(tex: str) -> str:
    if not _has_active_package(tex, "iclr2025_conference"):
        return tex
    if re.search(r"(?m)^[ \t]*\\iclrfinalcopy\b", tex):
        return tex
    package_match = re.search(
        r"(?m)^[ \t]*\\usepackage(?:\[[^\]]*\])?\{[^}]*\biclr2025_conference\b[^}]*\}[^\n]*\n?",
        tex,
    )
    insert_at = package_match.end() if package_match else 0
    return tex[:insert_at] + r"\iclrfinalcopy" + "\n" + tex[insert_at:]


def _json_load(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_prepared_manifest(target: Dict[str, Any]) -> Dict[str, Any]:
    official_assets = target.get("official_assets") or {}
    manifest_path = official_assets.get("prepared_manifest")
    if not manifest_path:
        return {}
    path = Path(str(manifest_path))
    if not path.exists():
        return {}
    return _json_load(path)


def _resolve_manifest_path(value: Any) -> Path:
    path = Path(str(value))
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def _normalize_line(line: str) -> str:
    return line.strip()


def _iter_preamble_lines(sample_tex: str) -> Iterable[str]:
    for raw_line in sample_tex.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("%"):
            continue
        if line.startswith(r"\begin{document}"):
            break
        yield line


def infer_required_preamble_lines(target: Dict[str, Any], prepared_manifest: Dict[str, Any]) -> List[str]:
    required = list(target.get("required_preamble_lines") or [])
    sample_tex = prepared_manifest.get("main_tex_sample")
    if not isinstance(sample_tex, str) or not sample_tex.strip():
        return required

    package_hints = {str(pkg) for pkg in (target.get("required_packages") or [])}
    class_file_hints = {
        Path(str(name)).stem
        for name in (target.get("official_assets") or {}).get("class_files", [])
    }
    input_hints: set[str] = set()

    explicit_packages = set()
    for line in required:
        explicit_packages.update(_extract_package_names(str(line)))
    allowed_template_packages = package_hints | class_file_hints | explicit_packages

    for line in _iter_preamble_lines(sample_tex):
        if line.startswith(r"\usepackage"):
            packages = set(_extract_package_names(line))
            if packages and packages.issubset(explicit_packages):
                continue
            foreign_template_packages = {
                package
                for package in packages
                if _looks_like_template_style_package(package)
                and package not in allowed_template_packages
            }
            if foreign_template_packages:
                continue
            if packages and packages & allowed_template_packages:
                required.append(line)
        elif line.startswith(r"\input"):
            if any(hint and hint in line for hint in input_hints):
                required.append(line)

    deduped: List[str] = []
    seen = set()
    seen_packages = set()
    for line in required:
        norm = _normalize_line(str(line))
        if not norm or norm in seen:
            continue
        packages = tuple(sorted(_extract_package_names(norm)))
        if packages and packages in seen_packages:
            continue
        seen.add(norm)
        if packages:
            seen_packages.add(packages)
        deduped.append(norm)
    return deduped


def _looks_like_template_style_package(package_name: str) -> bool:
    if package_name in CONFLICTING_STYLE_PACKAGES:
        return True
    return bool(re.match(r"iclr\d{4}_conference$", package_name))


def _extract_input_targets(required_lines: Iterable[str]) -> List[str]:
    names: List[str] = []
    for line in required_lines:
        match = re.search(r"\\input\{([^}]+)\}", line)
        if match:
            names.append(match.group(1))
    return names


def _copy_if_missing(src: Path, dst: Path) -> bool:
    if dst.exists() or not src.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def _find_by_name(root: Path, filename: str) -> Optional[Path]:
    matches = [path for path in root.rglob(filename) if path.is_file()]
    if not matches:
        return None
    matches.sort(key=lambda p: (len(p.parts), str(p)))
    return matches[0]


def _copy_required_asset(
    *,
    effective: Path,
    payload_root: Path,
    output_dir: Path,
    relative: Path,
) -> bool:
    if _copy_if_missing(effective / relative, output_dir / relative):
        return True
    if _copy_if_missing(payload_root / relative, output_dir / relative):
        return True
    found = _find_by_name(effective, relative.name) or _find_by_name(payload_root, relative.name)
    return bool(found and _copy_if_missing(found, output_dir / relative))


def ensure_supporting_asset_files(
    target: Dict[str, Any],
    prepared_manifest: Dict[str, Any],
    output_path: Path,
) -> List[str]:
    changes: List[str] = []
    payload_dir = prepared_manifest.get("payload_dir")
    effective_root = prepared_manifest.get("effective_root")
    if not payload_dir or not effective_root:
        return changes

    payload_root = _resolve_manifest_path(payload_dir)
    effective = _resolve_manifest_path(effective_root)
    output_dir = output_path.parent

    required_lines = infer_required_preamble_lines(target, prepared_manifest)
    for input_name in _extract_input_targets(required_lines):
        relative = Path(input_name)
        if not relative.suffix:
            relative = relative.with_suffix(".tex")
        copied = _copy_required_asset(
            effective=effective,
            payload_root=payload_root,
            output_dir=output_dir,
            relative=relative,
        )
        if copied:
            changes.append(f"copied supporting file {relative.as_posix()}")

    official_assets = target.get("official_assets") or {}
    for group_name in ("class_files", "bibliography_files"):
        for filename in official_assets.get(group_name, []) or []:
            rel = Path(str(filename))
            copied = _copy_required_asset(
                effective=effective,
                payload_root=payload_root,
                output_dir=output_dir,
                relative=rel,
            )
            if copied:
                changes.append(f"copied supporting file {rel.as_posix()}")
    return changes


def normalize_star_floats_for_single_column(tex: str) -> str:
    tex = tex.replace(r"\begin{figure*}", r"\begin{figure}")
    tex = tex.replace(r"\end{figure*}", r"\end{figure}")
    tex = tex.replace(r"\begin{table*}", r"\begin{table}")
    tex = tex.replace(r"\end{table*}", r"\end{table}")
    return tex


def ensure_article_compatibility_macros(tex: str) -> str:
    missing: List[str] = []
    if r"\providecommand{\equalcontrib}" not in tex:
        missing.append(r"\providecommand{\equalcontrib}{}")
    if r"\providecommand{\affiliations}" not in tex:
        missing.append(r"\providecommand{\affiliations}[1]{\date{#1}}")
    if r"\begin{acks}" in tex and r"\newenvironment{acks}" not in tex:
        missing.append(r"\newenvironment{acks}{\section*{Acknowledgments}}{}")

    if not missing:
        return tex

    block = "\n".join(
        [
            "% PaperFit single-column migration compatibility shims",
            r"\makeatletter",
            *missing,
            r"\makeatother",
            "",
        ]
    )

    title_match = re.search(r"^\\title\{", tex, re.MULTILINE)
    if not title_match:
        begin_doc = re.search(r"^\\begin\{document\}", tex, re.MULTILINE)
        insert_at = begin_doc.start() if begin_doc else len(tex)
        return tex[:insert_at] + block + tex[insert_at:]
    return tex[:title_match.start()] + block + tex[title_match.start():]


def _migration_hints(target: Dict[str, Any]) -> Dict[str, Any]:
    hints = target.get("migration_hints")
    return hints if isinstance(hints, dict) else {}


def ensure_required_shims(tex: str, target: Dict[str, Any]) -> str:
    macro_compatibility = target.get("macro_compatibility")
    if not isinstance(macro_compatibility, dict):
        return tex

    shims = macro_compatibility.get("required_shims")
    if not isinstance(shims, list):
        return tex

    missing = [str(shim).strip() for shim in shims if str(shim).strip() and str(shim).strip() not in tex]
    if not missing:
        return tex

    block = "\n".join(missing) + "\n"
    begin_doc = re.search(r"^\\begin\{document\}", tex, re.MULTILINE)
    insert_at = begin_doc.start() if begin_doc else len(tex)
    return tex[:insert_at] + block + tex[insert_at:]


def _find_command_block(tex: str, command: str) -> Optional[Tuple[int, int, str]]:
    match = re.search(rf"\\{re.escape(command)}\s*\{{", tex)
    if not match:
        return None
    brace_start = tex.find("{", match.start())
    if brace_start < 0:
        return None

    depth = 0
    for idx in range(brace_start, len(tex)):
        char = tex[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return match.start(), idx + 1, tex[brace_start + 1 : idx]
    return None


def _replace_command_block(tex: str, command: str, new_block: str) -> str:
    found = _find_command_block(tex, command)
    if not found:
        return tex
    start, end, _content = found
    return tex[:start] + new_block + tex[end:]


def _source_has_visible_authors(tex: str) -> bool:
    found = _find_command_block(tex, "author")
    if not found:
        return False
    _start, _end, content = found
    normalized = re.sub(r"\s+", " ", content).strip().lower()
    if not normalized:
        return False
    return "anonymous" not in normalized and "submission" not in normalized


def _split_top_level_commas(text: str) -> List[str]:
    parts: List[str] = []
    current: List[str] = []
    depth = 0
    for char in text:
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
            continue
        current.append(char)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _sanitize_acl_author_item(item: str) -> str:
    updated = " ".join(item.split())
    updated = re.sub(r"\$\^\{([^}]+)\}\$", r"\\textsuperscript{\1}", updated)
    updated = updated.replace(r"\equalcontrib", r"\textsuperscript{*}")
    updated = re.sub(
        r"\\textsuperscript\{([^}]+)\}\\thanks\{[^}]*Equal contribution\.?[^}]*\}",
        r"\\textsuperscript{\1,*}",
        updated,
        flags=re.IGNORECASE,
    )
    updated = re.sub(
        r"\\textsuperscript\{([^}]+)\}\\footnotemark(?:\[[^\]]+\])?",
        r"\\textsuperscript{\1,*}",
        updated,
    )
    updated = re.sub(
        r"\\textsuperscript\{([^}]+)\}\\thanks\{[^}]*Corresponding author\.?[^}]*\}",
        r"\\textsuperscript{\1,$\\dagger$}",
        updated,
        flags=re.IGNORECASE,
    )
    updated = re.sub(r"\\thanks\{[^}]*\}", "", updated)
    updated = re.sub(r"\\footnotemark(?:\[[^\]]+\])?", "", updated)
    updated = updated.strip().rstrip(",")
    if not updated:
        return updated
    if not updated.startswith(r"\textbf{"):
        updated = rf"\textbf{{{updated}}}"
    return updated


def _format_acl_author_block(author_content: str) -> Optional[str]:
    if r"\And" in author_content or r"\AND" in author_content:
        return None
    if r"\textsuperscript{" not in author_content:
        return None

    raw_lines = [line.strip() for line in author_content.splitlines() if line.strip()]
    if not raw_lines:
        return None

    author_lines: List[str] = []
    affiliation_lines: List[str] = []
    affiliation_started = False
    for line in raw_lines:
        cleaned = line.rstrip("\\").strip()
        if cleaned.startswith(r"\textsuperscript{") or cleaned.startswith(r"\texttt{") or cleaned.startswith(r"\small{"):
            affiliation_started = True
        if affiliation_started:
            affiliation_lines.append(cleaned)
        else:
            author_lines.append(cleaned)

    if not author_lines or not affiliation_lines:
        return None

    authors = [
        _sanitize_acl_author_item(part)
        for part in _split_top_level_commas(" ".join(author_lines))
    ]
    authors = [part for part in authors if part]
    if len(authors) < 4:
        return None

    split_idx = (len(authors) + 1) // 2
    author_rows = [", ".join(authors[:split_idx])]
    if split_idx < len(authors):
        author_rows.append(", ".join(authors[split_idx:]))

    email_value = ""
    aff_chunks: List[str] = []
    for line in affiliation_lines:
        if r"\texttt{" in line:
            email_match = re.search(r"\\texttt\{([^}]+)\}", line)
            if email_match:
                email_value = email_match.group(1).strip()
            continue
        aff_chunks.append(line)

    body_lines: List[str] = [rf"  {row}\\" for row in author_rows[:-1]]
    body_lines.append(f"  {author_rows[-1]}")
    if aff_chunks:
        body_lines.append(r"\\")
        body_lines.append(r"\\")
        if len(aff_chunks) >= 2:
            body_lines.append(f"  {', '.join(aff_chunks[:2])},\\")
            if len(aff_chunks) > 2:
                body_lines.append(f"  {', '.join(aff_chunks[2:])}")
        else:
            body_lines.append(f"  {aff_chunks[0]}")

    notes: List[str] = []
    if "equal contribution" in author_content.lower():
        notes.append(r"\textsuperscript{*}Equal contribution.")
    if "corresponding author" in author_content.lower() and email_value:
        notes.append(
            rf"\textsuperscript{{$\dagger$}}Correspondence: \href{{mailto:{email_value}}}{{{email_value}}}"
        )
    elif email_value:
        notes.append(rf"\href{{mailto:{email_value}}}{{{email_value}}}")

    if notes:
        body_lines.append(r"\\")
        body_lines.append(rf"  \small{{{' '.join(notes)}}}")

    return "\\author{\n" + "\n".join(body_lines) + "\n}"


def _format_acl_author_block_from_affiliations(author_content: str, affiliations_content: str) -> Optional[str]:
    author_text = " ".join(line.strip() for line in author_content.splitlines() if line.strip())
    authors = [_sanitize_acl_author_item(part) for part in _split_top_level_commas(author_text)]
    authors = [part for part in authors if part]
    if len(authors) < 2:
        return None

    affiliation_lines: List[str] = []
    email_value = ""
    for raw_line in affiliations_content.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("%"):
            continue
        stripped = re.sub(r"\\textsuperscript\{\\rm\s*([^}]+)\}", r"\\textsuperscript{\1}", stripped)
        stripped = stripped.rstrip("\\").strip()
        if "@" in stripped and r"\textsuperscript" not in stripped:
            email_value = stripped
            continue
        affiliation_lines.append(stripped)

    split_idx = (len(authors) + 1) // 2
    rows = [", ".join(authors[:split_idx])]
    if split_idx < len(authors):
        rows.append(", ".join(authors[split_idx:]))

    body_lines: List[str] = [rf"  {row}\\" for row in rows[:-1]]
    body_lines.append(f"  {rows[-1]}")
    if affiliation_lines:
        body_lines.append(r"\\")
        body_lines.append(r"\\")
        if len(affiliation_lines) >= 2:
            body_lines.append(f"  {', '.join(affiliation_lines[:2])},\\")
            if len(affiliation_lines) > 2:
                body_lines.append(f"  {', '.join(affiliation_lines[2:])}")
        else:
            body_lines.append(f"  {affiliation_lines[0]}")

    notes: List[str] = []
    if r"\equalcontrib" in author_content or "equal contribution" in author_content.lower():
        notes.append(r"\textsuperscript{*}Equal contribution.")
    if "corresponding author" in author_content.lower() and email_value:
        notes.append(
            rf"\textsuperscript{{$\dagger$}}Correspondence: \href{{mailto:{email_value}}}{{{email_value}}}"
        )
    elif email_value:
        notes.append(rf"\href{{mailto:{email_value}}}{{{email_value}}}")
    if notes:
        body_lines.append(r"\\")
        body_lines.append(rf"  \small{{{' '.join(notes)}}}")
    return "\\author{\n" + "\n".join(body_lines) + "\n}"


def _convert_affiliations_block_for_acl(tex: str) -> str:
    author_found = _find_command_block(tex, "author")
    affiliations_found = _find_command_block(tex, "affiliations")
    if not author_found or not affiliations_found:
        return tex
    _a_start, _a_end, author_content = author_found
    _aff_start, _aff_end, affiliations_content = affiliations_found
    replacement = _format_acl_author_block_from_affiliations(author_content, affiliations_content)
    if not replacement:
        return tex
    updated = _replace_command_block(tex, "author", replacement)
    refreshed_aff = _find_command_block(updated, "affiliations")
    if refreshed_aff:
        aff_start, aff_end, _content = refreshed_aff
        updated = updated[:aff_start] + updated[aff_end:]
    return updated


def _sanitize_article_author_item(item: str) -> str:
    updated = " ".join(item.split())
    updated = re.sub(r"\$\^\{([^}]+)\}\$", r"\\textsuperscript{\1}", updated)
    updated = re.sub(r"\\textsuperscript\{([^}]+)\}\\equalcontrib", r"\\textsuperscript{\1,*}", updated)
    updated = updated.replace(r"\equalcontrib", r"\textsuperscript{*}")
    updated = re.sub(
        r"\\textsuperscript\{([^}]+)\}\\thanks\{[^}]*Corresponding author\.?[^}]*\}",
        r"\\textsuperscript{\1,$\\dagger$}",
        updated,
        flags=re.IGNORECASE,
    )
    updated = re.sub(
        r"\\thanks\{[^}]*Equal contribution\.?[^}]*\}",
        r"\\textsuperscript{*}",
        updated,
        flags=re.IGNORECASE,
    )
    updated = re.sub(r"\\thanks\{[^}]*\}", "", updated)
    return updated.strip().rstrip(",")


def _format_centered_author_block_from_affiliations(author_content: str, affiliations_content: str) -> Optional[str]:
    author_text = " ".join(line.strip() for line in author_content.splitlines() if line.strip())
    authors = [_sanitize_article_author_item(part) for part in _split_top_level_commas(author_text)]
    authors = [part for part in authors if part]
    if len(authors) < 2:
        return None

    affiliation_lines: List[str] = []
    email_value = ""
    for raw_line in affiliations_content.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("%"):
            continue
        stripped = re.sub(r"\\textsuperscript\{\\rm\s*([^}]+)\}", r"\\textsuperscript{\1}", stripped)
        stripped = stripped.rstrip("\\").strip()
        if "@" in stripped and r"\textsuperscript" not in stripped:
            email_value = stripped
            continue
        affiliation_lines.append(stripped)

    split_idx = (len(authors) + 1) // 2
    rows = [", ".join(authors[:split_idx])]
    if split_idx < len(authors):
        rows.append(", ".join(authors[split_idx:]))

    body_lines: List[str] = [f"  {rows[0]}"]
    for row in rows[1:]:
        body_lines.append(r"  \\")
        body_lines.append(f"  {row}")

    if affiliation_lines:
        body_lines.append(r"  \\")
        body_lines.append(r"  \\")
        for idx, line in enumerate(affiliation_lines):
            suffix = r"\\" if idx < len(affiliation_lines) - 1 or email_value else ""
            body_lines.append(f"  {line}{suffix}")

    if email_value:
        body_lines.append(r"  \texttt{" + email_value + r"}")

    notes: List[str] = []
    if r"\equalcontrib" in author_content or "equal contribution" in author_content.lower():
        notes.append(r"\textsuperscript{*}Equal contribution.")
    if "corresponding author" in author_content.lower():
        notes.append(r"\textsuperscript{$\dagger$}Corresponding author.")
    if notes:
        body_lines.append(r"  \\")
        body_lines.append(r"  \small{" + " ".join(notes) + r"}")

    return "\\author{\n" + "\n".join(body_lines) + "\n}"


def _convert_affiliations_block_for_centered_article(tex: str) -> str:
    author_found = _find_command_block(tex, "author")
    affiliations_found = _find_command_block(tex, "affiliations")
    if not author_found or not affiliations_found:
        return tex
    _a_start, _a_end, author_content = author_found
    _aff_start, _aff_end, affiliations_content = affiliations_found
    replacement = _format_centered_author_block_from_affiliations(author_content, affiliations_content)
    if not replacement:
        return tex
    updated = _replace_command_block(tex, "author", replacement)
    refreshed_aff = _find_command_block(updated, "affiliations")
    if refreshed_aff:
        aff_start, aff_end, _content = refreshed_aff
        updated = updated[:aff_start] + updated[aff_end:]
    return updated


def _ensure_acl_package_mode(tex: str) -> str:
    if not _source_has_visible_authors(tex):
        return tex
    updated = re.sub(r"\\usepackage\[review\]\{acl\}", r"\\usepackage[final]{acl}", tex)
    updated = re.sub(r"\\usepackage\{acl\}", r"\\usepackage[final]{acl}", updated)
    return updated


def _ensure_neurips_package_mode(tex: str, target: Dict[str, Any]) -> str:
    if not _source_has_visible_authors(tex):
        return tex
    option = str(_migration_hints(target).get("package_options_for_visible_authors") or "").strip()
    if not option:
        return tex
    replacement = rf"\usepackage[{option}]{{neurips_2025}}"
    return re.sub(
        r"\\usepackage(?:\[[^\]]*\])?\{neurips_2025\}",
        lambda _match: replacement,
        tex,
        count=1,
    )


def _ensure_acl_titlebox(tex: str, minimum_size: str = "6.5cm") -> str:
    if r"\setlength\titlebox{" in tex:
        return tex
    author_block = _find_command_block(tex, "author")
    if not author_block:
        return tex
    _start, _end, author_content = author_block
    if author_content.count(r"\textsuperscript{") < 4 and len(author_content) < 180:
        return tex
    title_match = re.search(r"^\\title\{", tex, re.MULTILINE)
    if not title_match:
        return tex
    insertion = rf"\setlength\titlebox{{{minimum_size}}}" + "\n"
    return tex[:title_match.start()] + insertion + tex[title_match.start():]


def _source_uses_listings(tex: str) -> bool:
    clean = re.sub(r"(?m)^[ \t]*%[^\n]*(?:\n|$)", "", tex)
    return bool(
        re.search(
            r"\\begin\{lstlisting\}|\\lstinputlisting|\\begin\{listing\}",
            clean,
        )
    )


def _remove_template_example_blocks(tex: str) -> str:
    begin_doc = re.search(r"^\\begin\{document\}", tex, re.MULTILINE)
    if not begin_doc:
        return tex
    preamble = tex[: begin_doc.start()]
    body = tex[begin_doc.start() :]
    preamble = re.sub(r"(?ms)^\\iffalse\b.*?^\\fi\s*\n?", "", preamble)
    return preamble + body


def _remove_aaai_preamble_residue(tex: str) -> str:
    updated = tex
    for package_name in ["helvet", "courier", "natbib", "caption", "url"]:
        updated = remove_package(updated, package_name)
    updated = remove_package(updated, "bibentry")
    updated = re.sub(r"^[ \t]*\\urlstyle\{[^}]+\}[^\n]*\n?", "", updated, flags=re.MULTILINE)
    updated = re.sub(r"^[ \t]*\\def\\UrlFont\{[^}]+\}[^\n]*\n?", "", updated, flags=re.MULTILINE)
    updated = re.sub(r"^[ \t]*\\frenchspacing[^\n]*\n?", "", updated, flags=re.MULTILINE)
    updated = re.sub(r"^[ \t]*\\setlength\{\\pdfpagewidth\}\{[^}]+\}[^\n]*\n?", "", updated, flags=re.MULTILINE)
    updated = re.sub(r"^[ \t]*\\setlength\{\\pdfpageheight\}\{[^}]+\}[^\n]*\n?", "", updated, flags=re.MULTILINE)
    updated = re.sub(r"^[ \t]*%File:[^\n]*\n?", "", updated, flags=re.MULTILINE)
    updated = re.sub(r"^[ \t]*%release[^\n]*\n?", "", updated, flags=re.MULTILINE)
    updated = re.sub(r"^[ \t]*\\pdfinfo\{.*?^\}[ \t]*\n?", "", updated, flags=re.MULTILINE | re.DOTALL)
    updated = re.sub(r"^[ \t]*\\setcounter\{secnumdepth\}\{0\}[^\n]*\n?", "", updated, flags=re.MULTILINE)
    updated = _remove_template_example_blocks(updated)
    if not _source_uses_listings(updated):
        for package_name in ["newfloat", "listings"]:
            updated = remove_package(updated, package_name)
        updated = re.sub(r"^[ \t]*\\DeclareCaptionStyle\{ruled\}\{[^}]*\}[^\n]*\n?", "", updated, flags=re.MULTILINE)
        lstset_block = _find_command_block(updated, "lstset")
        if lstset_block:
            start, end, _content = lstset_block
            trailing_newline = 1 if end < len(updated) and updated[end : end + 1] == "\n" else 0
            updated = updated[:start] + updated[end + trailing_newline :]
        updated = re.sub(r"^[ \t]*\\floatstyle\{ruled\}[^\n]*\n?", "", updated, flags=re.MULTILINE)
        updated = re.sub(r"^[ \t]*\\newfloat\{listing\}\{[^}]+\}\{[^}]+\}\{[^}]*\}[^\n]*\n?", "", updated, flags=re.MULTILINE)
        updated = re.sub(r"^[ \t]*\\floatname\{listing\}\{[^}]+\}[^\n]*\n?", "", updated, flags=re.MULTILINE)
        updated = re.sub(r"^[ \t]*% REMOVE THIS: bibentry[^\n]*\n?", "", updated, flags=re.MULTILINE)
        updated = re.sub(r"^[ \t]*% END REMOVE bibentry[^\n]*\n?", "", updated, flags=re.MULTILINE)
        updated = re.sub(r"^[ \t]*% This is only needed to show inline citations.*\n?", "", updated, flags=re.MULTILINE)
    updated = re.sub(r"^[ \t]*%Example,[^\n]*\n?", "", updated, flags=re.MULTILINE)
    return updated


def _apply_acl_author_style(tex: str) -> str:
    found = _find_command_block(tex, "author")
    if not found:
        return tex
    _start, _end, author_content = found
    replacement = _format_acl_author_block(author_content)
    if not replacement:
        return tex
    return _replace_command_block(tex, "author", replacement)


def _count_top_level_columns(column_spec: str) -> int:
    spec = column_spec.strip("{}")
    depth = 0
    count = 0
    idx = 0
    while idx < len(spec):
        char = spec[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        elif depth == 0 and char in {"l", "r", "c", "X"}:
            count += 1
        elif depth == 0 and char in {"p", "m", "b"} and idx + 1 < len(spec) and spec[idx + 1] == "{":
            count += 1
        idx += 1
    return count


def _tighten_wide_table_block(block: str) -> str:
    tabular_match = re.search(r"\\begin\{tabularx?\}(?:\{[^}]+\})?(\{[^}]+\})", block)
    if not tabular_match:
        return block
    column_spec = tabular_match.group(1)
    column_count = _count_top_level_columns(column_spec)
    if column_count < 4:
        return block

    updated = block
    if r"\begin{table*}" not in updated:
        updated = re.sub(r"\\begin\{table\}(?:\[[^\]]*\])?", r"\\begin{table*}[t]", updated, count=1)
        updated = updated.replace(r"\end{table}", r"\end{table*}", 1)
        updated = updated.replace(r"\linewidth", r"\textwidth")
    size = r"\scriptsize" if column_count >= 8 else r"\footnotesize"
    tabcolsep = "0.8pt" if column_count >= 8 else "1.5pt"
    updated = _set_table_size_command(updated, size)
    updated = _set_local_tabcolsep_before_first_tabular(updated, tabcolsep)
    updated = _make_first_text_column_wrappable(updated)
    return updated


def tighten_wide_tables_for_double_column(tex: str) -> str:
    float_pattern = re.compile(r"(?m)^[ \t]*\\begin\{(?:table|figure)\*?\}(?:\[[^\]]*\])?.*?^[ \t]*\\end\{(?:table|figure)\*?\}", re.DOTALL)
    updated = float_pattern.sub(lambda match: _tighten_table_like_float_block(match.group(0)), tex)
    # Handle standalone tabular snippets as a fallback for included files.
    table_pattern = re.compile(r"(?m)^[ \t]*\\begin\{table\*?\}(?:\[[^\]]*\])?.*?^[ \t]*\\end\{table\*?\}", re.DOTALL)
    return table_pattern.sub(lambda match: _tighten_wide_table_block(match.group(0)), updated)


def _tighten_table_like_float_block(block: str) -> str:
    updated = _tighten_wide_table_block(block)
    # Composite floats often contain several minipage-local tabulars.  These
    # do not meet the "wide table" column-count heuristic, but they overflow
    # after resizebox is unwrapped because long first-column labels cannot wrap.
    minipage_pattern = re.compile(r"\\begin\{minipage\}(?:\[[^\]]*\])?\{[^}]+\}.*?\\end\{minipage\}", re.DOTALL)
    return minipage_pattern.sub(lambda match: _tighten_minipage_tables(match.group(0)), updated)


def _tighten_minipage_tables(block: str) -> str:
    if r"\begin{tabular" not in block:
        return block
    updated = block
    updated = _set_table_size_command(updated, r"\scriptsize")
    updated = _set_local_tabcolsep_before_first_tabular(updated, "1pt")
    updated = _make_first_text_column_wrappable(updated)
    return updated


def _set_table_size_command(block: str, size_command: str) -> str:
    updated = block
    for command in (r"\scriptsize", r"\footnotesize", r"\small"):
        if command in updated:
            return updated.replace(command, size_command, 1)
    centering_match = re.search(r"\\centering", updated)
    if centering_match:
        return updated[: centering_match.end()] + f"\n{size_command}" + updated[centering_match.end():]
    begin_match = re.search(r"\\begin\{(?:table|figure|table\*|figure\*|minipage)\}(?:\[[^\]]*\])?(?:\{[^}]+\})?", updated)
    if begin_match:
        return updated[: begin_match.end()] + f"\n{size_command}" + updated[begin_match.end():]
    return updated


def _set_local_tabcolsep_before_first_tabular(block: str, value: str) -> str:
    # Remove stale local table spacing commands in the float/minipage.  Keeping
    # duplicate commands is brittle because the later one silently wins.
    updated = re.sub(r"^[ \t]*\\setlength\{\\tabcolsep\}\{[^}]+\}[^\n]*\n?", "", block, flags=re.MULTILINE)
    updated = re.sub(r"^[ \t]*\\tabcolsep\s*=\s*[^ \n]+[^\n]*\n?", "", updated, flags=re.MULTILINE)
    tabular_match = _find_first_active_match(r"\\begin\{tabularx?\}", updated)
    if tabular_match:
        return updated[: tabular_match.start()] + rf"\setlength{{\tabcolsep}}{{{value}}}" + "\n" + updated[tabular_match.start():]
    return updated


def _make_first_text_column_wrappable(block: str) -> str:
    replaced = False

    def _replace(match: re.Match[str]) -> str:
        nonlocal replaced
        if replaced or _is_commented_position(block, match.start()):
            return match.group(0)
        prefix = match.group(1)
        spec = match.group(2)
        if "p{" in spec or "m{" in spec or "b{" in spec or "X" in spec:
            return match.group(0)
        stripped = spec.strip()
        if not stripped or stripped[0] != "l":
            return match.group(0)
        column_count = _count_top_level_columns("{" + spec + "}")
        if column_count < 3:
            return match.group(0)
        width = "0.34\\linewidth" if column_count <= 4 else "0.18\\textwidth"
        new_spec = "p{" + width + "}" + spec[1:]
        if not new_spec.startswith("@{}"):
            new_spec = "@{}" + new_spec + "@{}"
        replaced = True
        return f"{prefix}{{{new_spec}}}"

    return re.sub(
        r"(\\begin\{tabular\})\{([^}]+)\}",
        _replace,
        block,
    )


def _find_first_active_match(pattern: str, tex: str) -> Optional[re.Match[str]]:
    for match in re.finditer(pattern, tex):
        if not _is_commented_position(tex, match.start()):
            return match
    return None


def _tighten_single_column_table_block(block: str) -> str:
    tabular_match = re.search(r"\\begin\{tabularx?\}(?:\{[^}]+\})?(\{[^}]+\})", block)
    if not tabular_match:
        return block
    column_spec = tabular_match.group(1)
    column_count = _count_top_level_columns(column_spec)
    if column_count < 6:
        return block

    updated = block
    preferred_size = r"\scriptsize" if column_count >= 10 else (r"\footnotesize" if column_count >= 7 else r"\small")
    updated = _set_table_size_command(updated, preferred_size)
    target_tabcolsep = "0.6pt" if column_count >= 10 else ("1pt" if column_count >= 7 else "2pt")
    updated = _set_local_tabcolsep_before_first_tabular(updated, target_tabcolsep)
    updated = _make_first_text_column_wrappable(updated)

    updated = re.sub(
        r"(\\begin\{tabularx?\}(?:\{[^}]+\})?)\{([^@}][^}]*)\}",
        lambda match: f"{match.group(1)}{{@{{}}{match.group(2)}@{{}}}}",
        updated,
        count=1,
    )
    return updated


def tighten_wide_tables_for_single_column(tex: str) -> str:
    pattern = re.compile(r"\\begin\{table\*?\}(?:\[[^\]]*\])?.*?\\end\{table\*?\}", re.DOTALL)
    return pattern.sub(lambda match: _tighten_single_column_table_block(match.group(0)), tex)


def apply_target_style_postprocess(tex: str, target: Dict[str, Any]) -> str:
    hints = _migration_hints(target)
    style = str(hints.get("title_affiliation_style") or "").strip().lower()
    updated = tex
    if style == "acl":
        updated = _remove_aaai_preamble_residue(updated)
        updated = _convert_affiliations_block_for_acl(updated)
        updated = _ensure_acl_package_mode(updated)
        updated = _apply_acl_author_style(updated)
        updated = _ensure_acl_titlebox(updated)
    elif style == "iclr":
        updated = _remove_aaai_preamble_residue(updated)
        updated = _convert_affiliations_block_for_centered_article(updated)
        updated = ensure_iclr_camera_ready_copy(updated)
    elif style == "neurips":
        updated = _remove_aaai_preamble_residue(updated)
        updated = _convert_affiliations_block_for_centered_article(updated)
        updated = _ensure_neurips_package_mode(updated, target)
    column_type = str(target.get("column_type") or "").lower()
    if column_type == "double":
        updated = tighten_wide_tables_for_double_column(updated)
    elif column_type == "single":
        updated = tighten_wide_tables_for_single_column(updated)
    return updated


def ensure_bibliographystyle(tex: str, style_name: Optional[str] = "plainnat", target: Optional[Dict[str, Any]] = None) -> str:
    broken_double = r"\\bibliographystyle{"
    if broken_double in tex:
        tex = tex.replace(broken_double, r"\bibliographystyle{")

    hints = _migration_hints(target or {})
    style_family = str(hints.get("title_affiliation_style") or "").strip().lower()
    if style_family == "acl":
        return re.sub(r"^[ \t]*\\bibliographystyle\{[^}]+\}[^\n]*\n?", "", tex, flags=re.MULTILINE)

    if r"\begin{thebibliography}" in tex:
        return tex
    if r"\bibliographystyle{" in tex:
        if not style_name:
            return tex
        return re.sub(
            r"^[ \t]*\\bibliographystyle\{[^}]+\}[^\n]*",
            rf"\\bibliographystyle{{{style_name}}}",
            tex,
            count=1,
            flags=re.MULTILINE,
        )

    bibliography_match = re.search(r"^\\bibliography\{[^}]+\}", tex, re.MULTILINE)
    if not bibliography_match or not style_name:
        return tex

    insertion = f"\\bibliographystyle{{{style_name}}}\n"
    return tex[:bibliography_match.start()] + insertion + tex[bibliography_match.start():]


def migrate_to_template(
    tex: str,
    target_name: str,
    templates: Dict[str, Any],
    output_path: Optional[Path] = None,
    prepared_manifest: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if target_name not in templates:
        raise ValueError(f"Unknown target template: {target_name}")

    target = templates[target_name]
    migrated = tex
    changes: list[str] = []
    prepared_manifest = prepared_manifest if prepared_manifest is not None else load_prepared_manifest(target)

    migrated = replace_documentclass(migrated, str(target["documentclass"]))
    changes.append(f"documentclass -> {target_name}")

    updated = remove_conflicting_template_style_lines(migrated)
    if updated != migrated:
        migrated = updated
        changes.append("removed conflicting template style lines")

    updated = remove_source_template_commands(migrated)
    if updated != migrated:
        migrated = updated
        changes.append("removed source-template commands")

    updated = remove_source_font_packages(migrated, target_name)
    if updated != migrated:
        migrated = updated
        changes.append("removed source-template font packages")

    updated = remove_source_template_metadata(migrated, target_name)
    if updated != migrated:
        migrated = updated
        changes.append("removed source-template metadata")

    updated = unwrap_texorpdfstring_commands(migrated)
    if updated != migrated:
        migrated = updated
        changes.append("unwrapped texorpdfstring source macros")

    updated = normalize_theorem_labels(migrated)
    if updated != migrated:
        migrated = updated
        changes.append("normalized theorem labels")

    updated = normalize_article_maketitle_order(migrated, target)
    if updated != migrated:
        migrated = updated
        changes.append("normalized article maketitle order")

    updated = ensure_common_content_packages(migrated, target_name)
    if updated != migrated:
        migrated = updated
        changes.append("ensured common content packages")

    updated = remove_target_package_conflicts(migrated, target_name)
    if updated != migrated:
        migrated = updated
        changes.append(f"removed package conflicts for {target_name}")

    updated = remove_camera_ready_line_numbering(migrated, target_name)
    if updated != migrated:
        migrated = updated
        changes.append("removed camera-ready line numbering")

    for package_name in list(target.get("forbidden_packages") or []):
        updated = remove_package(migrated, package_name)
        if updated != migrated:
            migrated = updated
            changes.append(f"removed package {package_name}")

    updated = apply_removed_macro_policy(migrated, target)
    if updated != migrated:
        migrated = updated
        changes.append("removed target-incompatible macros")

    updated = ensure_required_preamble_lines(
        migrated,
        infer_required_preamble_lines(target, prepared_manifest),
    )
    if updated != migrated:
        migrated = updated
        changes.append("inserted required preamble lines")

    updated = normalize_camera_ready_template_options(migrated, target)
    if updated != migrated:
        migrated = updated
        changes.append("normalized camera-ready template options")

    if target.get("column_type") == "single":
        updated = normalize_star_floats_for_single_column(migrated)
        if updated != migrated:
            migrated = updated
            changes.append("normalized figure*/table* to figure/table")

    updated = ensure_article_compatibility_macros(migrated)
    if updated != migrated:
        migrated = updated
        changes.append("added compatibility shims for affiliations/equalcontrib")

    updated = apply_target_style_postprocess(migrated, target)
    if updated != migrated:
        migrated = updated
        changes.append("applied target style postprocess")

    updated = ensure_required_preamble_lines(
        migrated,
        infer_required_preamble_lines(target, prepared_manifest),
    )
    if updated != migrated:
        migrated = updated
        changes.append("reinserted required preamble lines after postprocess")

    updated = normalize_camera_ready_template_options(migrated, target)
    if updated != migrated:
        migrated = updated
        changes.append("renormalized camera-ready template options")

    bib_style = _migration_hints(target).get("add_bibliographystyle_if_missing") or "plainnat"
    updated = ensure_bibliographystyle(migrated, style_name=str(bib_style) if bib_style else None, target=target)
    if updated != migrated:
        migrated = updated
        changes.append(f"reconciled bibliographystyle for {target_name}")

    updated = ensure_required_shims(migrated, target)
    if updated != migrated:
        migrated = updated
        changes.append(f"inserted required compatibility shims for {target_name}")

    updated = ensure_target_header_shims(migrated, target_name)
    if updated != migrated:
        migrated = updated
        changes.append(f"inserted target header shims for {target_name}")

    if target.get("column_type") == "double":
        updated, float_changes = adapt_tex_floats_for_double_column(migrated)
        if updated != migrated:
            migrated = updated
            changes.extend(f"main: {item}" for item in float_changes)
        if r"\begin{strip}" in migrated:
            updated = ensure_package_line(migrated, "cuted")
            if updated != migrated:
                migrated = updated
                changes.append("added cuted package for wide display math")
    elif target.get("column_type") == "single":
        updated, float_changes = adapt_tex_floats_for_single_column(migrated)
        if updated != migrated:
            migrated = updated
            changes.extend(f"main: {item}" for item in float_changes)

    supporting_changes: List[str] = []
    if output_path is not None:
        supporting_changes = ensure_supporting_asset_files(target, prepared_manifest, output_path)
        changes.extend(supporting_changes)
        float_changes = adapt_project_floats_for_target(
            output_path,
            target_column_type=str(target.get("column_type") or ""),
        )
        changes.extend(float_changes)
        if any("promoted wide display math" in item for item in float_changes):
            updated = ensure_package_line(migrated, "cuted")
            if updated != migrated:
                migrated = updated
                changes.append("added cuted package for project wide display math")

    return {
        "content": migrated,
        "changes": changes,
        "target_template": target_name,
        "target_column_type": target.get("column_type"),
        "prepared_manifest": prepared_manifest,
        "supporting_changes": supporting_changes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate a TeX file to another template")
    parser.add_argument("main_tex")
    parser.add_argument("--target", required=True)
    parser.add_argument("--output", default=None)
    parser.add_argument("--report", default="data/template_migration_report.json")
    parser.add_argument("--backup-dir", default="data/backups")
    args = parser.parse_args()

    templates = load_templates()
    main_tex = Path(args.main_tex).resolve()
    output_path = Path(args.output).resolve() if args.output else main_tex
    report_path = Path(args.report).resolve() if Path(args.report).is_absolute() else Path.cwd() / args.report
    backup_dir = Path(args.backup_dir).resolve() if Path(args.backup_dir).is_absolute() else Path.cwd() / args.backup_dir

    original = main_tex.read_text(encoding="utf-8")
    source_template = detect_source_template(original)
    migration = migrate_to_template(original, args.target, templates, output_path)
    backup_path = atomic_write_text(output_path, migration["content"], backup_dir=backup_dir)

    report = {
        "status": "success",
        "source_template": source_template,
        "target_template": migration["target_template"],
        "target_column_type": migration["target_column_type"],
        "target_official_assets": (templates.get(args.target) or {}).get("official_assets"),
        "target_registry_asset": (templates.get(args.target) or {}).get("registry_asset"),
        "target_prepared_manifest": migration.get("prepared_manifest"),
        "input_file": str(main_tex),
        "output_file": str(output_path),
        "backup_path": backup_path,
        "changes": migration["changes"],
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
