#!/usr/bin/env python3
"""
Float Fixers Module

处理 Category B：浮动体缺陷
- B1: 浮动体远离首次引用
- B2: 浮动体大小不适配栏宽
- B3: 浮动体连续堆叠
- B4: 浮动体跨页分裂

该模块被 code-surgeon-agent 调用，执行对 .tex 源码的精确修改。
所有修复遵循最小修改原则，不改变学术内容。
"""

import re
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Any

from transactional_patch import atomic_write_text

try:
    from content_integrity_check import compute_content_diff, structure_regression_reasons
except ImportError:
    compute_content_diff = None
    structure_regression_reasons = None

try:
    from skills.latex_fixers.shared_table_helpers import (
        convert_plain_alignment_to_stretched_spec,
        convert_last_text_column_to_x,
        convert_preserve_first_column_to_x,
        ensure_tabularx_package,
        find_first_tabular_span,
        is_plain_alignment_column_spec,
        read_braced_group,
        remove_resizebox_around_first_tabular,
        rewrite_first_tabular_to_tabular_star,
        rewrite_first_tabular_to_tabularx,
    )
except ModuleNotFoundError:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from skills.latex_fixers.shared_table_helpers import (
        convert_plain_alignment_to_stretched_spec,
        convert_last_text_column_to_x,
        convert_preserve_first_column_to_x,
        ensure_tabularx_package,
        find_first_tabular_span,
        is_plain_alignment_column_spec,
        read_braced_group,
        remove_resizebox_around_first_tabular,
        rewrite_first_tabular_to_tabular_star,
        rewrite_first_tabular_to_tabularx,
    )


FLOAT_ASSET_DIRS = {"_figs", "_figures", "figures", "_tables", "tables"}


# ============================================================
# 数据结构定义
# ============================================================

@dataclass
class FixResult:
    """修复结果"""
    defect_id: str
    object_name: str
    action: str
    before: str
    after: str
    page: int = 0
    line_number: Optional[int] = None
    success: bool = False


@dataclass
class FloatFixReport:
    """修复报告"""
    status: str  # success | partial | failed
    modified_files: List[str] = field(default_factory=list)
    changes: List[FixResult] = field(default_factory=list)
    unresolved: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill": "float-optimizer",
            "status": self.status,
            "modified_files": self.modified_files,
            "changes": [
                {
                    "defect_id": c.defect_id,
                    "object": c.object_name,
                    "action": c.action,
                    "before": c.before,
                    "after": c.after,
                    "page": c.page,
                    "line_number": c.line_number,
                    "success": c.success,
                }
                for c in self.changes
            ],
            "unresolved": self.unresolved,
        }


# ============================================================
# B1：浮动体远离首次引用
# ============================================================

def fix_float_reference_distance(
    tex_content: str,
    float_label: str,
    ref_page: int,
    float_page: int,
    ref_line: Optional[int] = None,
    float_line: Optional[int] = None,
    reference_text: Optional[str] = None,
    reference_source: Optional[str] = None,
    force_intervention: bool = False,
    allow_cross_same_type: bool = False,
) -> Tuple[str, Optional[FixResult]]:
    r"""
    修复浮动体远离首次引用问题

    策略优先级:
    1. 调整位置参数为 [ht]
    2. 在引用点后添加 \FloatBarrier
    3. 移动浮动体源码位置
    4. 拆分大型浮动体

    Args:
        tex_content: .tex 文件内容
        float_label: 浮动体标签 (如 "fig:result" 或 "tab:results")
        ref_page: 首次引用所在页码
        float_page: 浮动体实际所在页码

    Returns:
        (修改后的内容，修复结果)
    """
    float_type = "figure" if "fig" in float_label.lower() else "table"
    target_match, env_name, block_start, block_end = _find_labeled_float_block(
        tex_content,
        float_label,
        float_type,
    )
    ref_match = _find_first_reference(tex_content, float_label)
    ref_anchor = _find_reference_anchor(
        tex_content,
        float_label=float_label,
        ref_match=ref_match,
        ref_line=ref_line,
        reference_text=reference_text,
    )
    source_line_distance = None
    if target_match and ref_anchor and block_start is not None:
        float_source_line = tex_content[:block_start].count('\n') + 1
        ref_source_line = tex_content[:ref_anchor["start"]].count('\n') + 1
        source_line_distance = abs(float_source_line - ref_source_line)
    elif ref_line is not None and float_line is not None:
        source_line_distance = abs(int(float_line) - int(ref_line))
    elif ref_page or float_page:
        source_line_distance = abs(int(float_page) - int(ref_page))

    if (not force_intervention) and (source_line_distance is not None and source_line_distance <= 12) and target_match:
        pos_param = target_match.group(1) if target_match.group(1) else ""
        if pos_param not in {'[p]', '[!p]', '[b]', '[!b]'}:
            return tex_content, None

    if target_match and block_start is not None and block_end is not None:
        normalized_content = _normalize_float_block_for_semantic_fix(
            tex_content,
            float_label=float_label,
            env_name=env_name,
            block_start=block_start,
            block_end=block_end,
        )
        if normalized_content != tex_content:
            tex_content = normalized_content
            target_match, env_name, block_start, block_end = _find_labeled_float_block(
                tex_content,
                float_label,
                float_type,
            )
            ref_match = _find_first_reference(tex_content, float_label)
            ref_anchor = _find_reference_anchor(
                tex_content,
                float_label=float_label,
                ref_match=ref_match,
                ref_line=ref_line,
                reference_text=reference_text,
            )

    # 策略 1: 只要源码中的浮动体没有贴近首次引用段落，就优先按引用段落锚定重排源码
    if target_match and ref_anchor and block_start is not None and block_end is not None:
        if env_name.endswith("*"):
            insert_pos = _find_star_float_anchor_insert_pos(tex_content, int(ref_anchor["start"]))
        else:
            insert_pos = _find_paragraph_end(tex_content, int(ref_anchor["end"]))
        insert_pos = _clamp_insert_pos_before_bibliography(tex_content, insert_pos)
        if insert_pos is not None:
            should_reanchor = block_start < int(ref_anchor["start"])
            if not should_reanchor and source_line_distance is not None:
                should_reanchor = source_line_distance > 12
            if not should_reanchor:
                should_reanchor = block_start > insert_pos + 120
            if should_reanchor and _would_cross_same_type_float(
                    tex_content,
                    float_type=float_type,
                    block_start=block_start,
                    block_end=block_end,
                    insert_pos=insert_pos,
                ):
                    should_reanchor = allow_cross_same_type
            if should_reanchor:
                moved_content = _move_float_to_insert_pos(
                    tex_content,
                    block_start,
                    block_end,
                    insert_pos,
                )
                if moved_content != tex_content:
                    anchor_desc = str(ref_anchor.get("preview") or reference_text or reference_source or float_label)
                    if env_name.endswith("*"):
                        action = f"将跨栏浮动体前移到语义锚点所属小节之前（{anchor_desc}）"
                    else:
                        action = f"将浮动体移动到语义锚点段落之后（{anchor_desc}）"
                    return moved_content, FixResult(
                        defect_id="B1",
                        object_name=float_label,
                        action=action,
                        before=f"\\begin{{{env_name}}}",
                        after=f"anchored after first reference paragraph for {float_label}",
                        success=True,
                    )

    # 策略 2: 若暂不移动源码，再调整位置参数，避免 [p]/[!b] 等参数继续把浮动体推出当前上下文
    if target_match:
        pos_param = target_match.group(1) if target_match.group(1) else ""

        if pos_param in ['[t]', '[b]', '[h]', '[p]', '[!t]', '[!b]', '[!h]', '[!p]']:
            new_param = "[ht]"
            modified_content = tex_content[:target_match.start(1)] + new_param + tex_content[target_match.end(1):]
            return modified_content, FixResult(
                defect_id="B1",
                object_name=float_label,
                action=f"将浮动体位置参数从 {pos_param} 改为 {new_param}",
                before=f"\\begin{{{float_type}}}{pos_param}",
                after=f"\\begin{{{float_type}}}{new_param}",
                success=True,
            )
        elif not pos_param:
            new_param = "[ht]"
            insert_pos = target_match.end()
            modified_content = tex_content[:insert_pos] + new_param + tex_content[insert_pos:]
            return modified_content, FixResult(
                defect_id="B1",
                object_name=float_label,
                action=f"添加浮动体位置参数 {new_param}",
                before=f"\\begin{{{float_type}}}",
                after=f"\\begin{{{float_type}}}{new_param}",
                success=True,
            )

    # 策略 3: 在引用点后添加 \FloatBarrier
    if ref_anchor:
        anchor_end = int(ref_anchor["end"])
        bibliography_start = _find_bibliography_start(tex_content)
        if bibliography_start is not None and anchor_end >= bibliography_start:
            return tex_content, None
        insert_pos = _find_paragraph_end(tex_content, anchor_end) or anchor_end
        insert_pos = _clamp_insert_pos_before_bibliography(tex_content, insert_pos)
        # 检查是否已有 \FloatBarrier
        after_ref = tex_content[insert_pos:insert_pos + 100]
        if '\\FloatBarrier' not in after_ref:
            modified_content = tex_content[:insert_pos] + "\n\\FloatBarrier" + tex_content[insert_pos:]
            anchor_preview = str(ref_anchor.get("preview") or reference_text or reference_source or float_label)
            return modified_content, FixResult(
                defect_id="B1",
                object_name=float_label,
                action=f"在语义锚点后添加 \\FloatBarrier 以阻止浮动体继续漂后（{anchor_preview}）",
                before=anchor_preview[:30] + "...",
                after=anchor_preview + "\n\\FloatBarrier",
                success=True,
            )

    return tex_content, None


def _find_first_reference(tex_content: str, float_label: str) -> Optional[re.Match[str]]:
    ref_pattern = r'\\(?:ref|autoref|cref|Cref)\{' + re.escape(float_label) + r'\}'
    return re.search(ref_pattern, tex_content)


def _line_span_for_line_number(tex_content: str, line_number: int) -> Optional[Tuple[int, int]]:
    if line_number <= 0:
        return None
    current_line = 1
    start = 0
    for index, ch in enumerate(tex_content):
        if current_line == line_number:
            line_end = tex_content.find('\n', start)
            if line_end == -1:
                line_end = len(tex_content)
            return start, line_end
        if ch == '\n':
            current_line += 1
            start = index + 1
    if current_line == line_number:
        return start, len(tex_content)
    return None


def _line_number_for_offset(tex_content: str, offset: int) -> int:
    return tex_content[:max(0, offset)].count('\n') + 1


def _rewrite_plain_text_reference_to_label_ref(
    tex_content: str,
    *,
    float_label: str,
    float_type: str,
    ref_line: Optional[int],
    reference_text: Optional[str],
) -> Tuple[str, bool]:
    if not reference_text or ref_line is None:
        return tex_content, False
    if "\\ref{" in reference_text:
        return tex_content, False

    ref_anchor = _find_reference_anchor(
        tex_content,
        float_label=float_label,
        ref_match=None,
        ref_line=ref_line,
        reference_text=reference_text,
    )
    if not ref_anchor:
        return tex_content, False

    prefix = "Figure" if float_type == "figure" else "Table"
    replacement = f"{prefix}~\\ref{{{float_label}}}"
    updated = tex_content[:int(ref_anchor["start"])] + replacement + tex_content[int(ref_anchor["end"]):]
    return updated, updated != tex_content


def _find_reference_anchor(
    tex_content: str,
    *,
    float_label: str,
    ref_match: Optional[re.Match[str]],
    ref_line: Optional[int],
    reference_text: Optional[str],
) -> Optional[Dict[str, Any]]:
    if ref_match:
        return {
            "start": ref_match.start(),
            "end": ref_match.end(),
            "preview": ref_match.group(0),
            "strategy": "latex_ref_match",
        }

    if ref_line is None:
        if reference_text:
            first_match = tex_content.find(reference_text)
            if first_match != -1:
                return {
                    "start": first_match,
                    "end": first_match + len(reference_text),
                    "preview": reference_text,
                    "strategy": "global_reference_text_search",
                }
        return None

    search_lines = [ref_line, ref_line - 1, ref_line + 1]
    fallback_anchor: Optional[Dict[str, Any]] = None
    for line_number in search_lines:
        if line_number is None or line_number <= 0:
            continue
        span = _line_span_for_line_number(tex_content, int(line_number))
        if not span:
            continue
        start, end = span
        line_text = tex_content[start:end]
        if reference_text:
            line_offset = line_text.find(reference_text)
            if line_offset != -1:
                match_start = start + line_offset
                match_end = match_start + len(reference_text)
                return {
                    "start": match_start,
                    "end": match_end,
                    "preview": reference_text,
                    "strategy": "semantic_home_reference_text",
                }
        stripped = line_text.strip()
        if stripped and fallback_anchor is None:
            fallback_anchor = {
                "start": start,
                "end": end,
                "preview": stripped,
                "strategy": "semantic_home_line_fallback",
            }

    if reference_text:
        matches: List[Tuple[int, int]] = []
        search_start = 0
        while True:
            idx = tex_content.find(reference_text, search_start)
            if idx == -1:
                break
            matches.append((idx, idx + len(reference_text)))
            search_start = idx + len(reference_text)
        if matches:
            target_match = min(
                matches,
                key=lambda span: abs(_line_number_for_offset(tex_content, span[0]) - int(ref_line)),
            )
            return {
                "start": target_match[0],
                "end": target_match[1],
                "preview": reference_text,
                "strategy": "global_reference_text_nearest_line",
            }

    return fallback_anchor


def _find_labeled_float_block(
    tex_content: str,
    float_label: str,
    float_type: str,
) -> Tuple[Optional[re.Match[str]], str, Optional[int], Optional[int]]:
    fallback_type = "table" if float_type == "figure" else "figure"
    env_names = []
    for env_name in (float_type, f"{float_type}*", fallback_type, f"{fallback_type}*"):
        if env_name not in env_names:
            env_names.append(env_name)
    for env_name in env_names:
        pattern = r'\\begin\{' + re.escape(env_name) + r'\}(\[[^\]]*\])?'
        for match in re.finditer(pattern, tex_content):
            if _is_commented_at(tex_content, match.start()):
                continue
            end_match = re.search(
                r'\\end\{' + re.escape(env_name) + r'\}',
                tex_content[match.end():],
                re.DOTALL,
            )
            if not end_match:
                continue
            block_end = match.end() + end_match.end()
            block = tex_content[match.start():block_end]
            label_pattern = r'\\label\{' + re.escape(float_label) + r'\}'
            for label_match in re.finditer(label_pattern, block):
                if _is_commented_at(tex_content, match.start() + label_match.start()):
                    continue
                return match, env_name, match.start(), block_end
    return None, float_type, None, None


def _is_commented_at(text: str, offset: int) -> bool:
    line_start = text.rfind("\n", 0, offset) + 1
    prefix = text[line_start:offset]
    idx = 0
    while True:
        percent = prefix.find("%", idx)
        if percent == -1:
            return False
        backslashes = 0
        cursor = percent - 1
        while cursor >= 0 and prefix[cursor] == "\\":
            backslashes += 1
            cursor -= 1
        if backslashes % 2 == 0:
            return True
        idx = percent + 1


def _label_float_type(label: str) -> str:
    return "figure" if "fig" in str(label).lower() else "table"


def _defect_labels(defect: Dict[str, Any]) -> List[str]:
    labels = [str(item) for item in (defect.get("labels") or []) if str(item)]
    object_name = str(defect.get("object") or "")
    if object_name and object_name not in labels:
        labels.insert(0, object_name)
    return labels


def _contains_any_labeled_float(tex_content: str, labels: List[str]) -> bool:
    for label in labels:
        match, _, _, _ = _find_labeled_float_block(tex_content, label, _label_float_type(label))
        if match:
            return True
    return False


def _iter_project_tex_files(project_root: Path) -> List[Path]:
    ignored_dirs = {".git", "__pycache__", "data", "pages", "page_images", "archives"}
    files: List[Path] = []
    for path in sorted(project_root.rglob("*.tex")):
        try:
            rel_parts = path.relative_to(project_root).parts
        except ValueError:
            rel_parts = path.parts
        if any(part in ignored_dirs for part in rel_parts[:-1]):
            continue
        files.append(path)
    return files


def _is_float_asset_file(project_root: Path, tex_file: Path) -> bool:
    try:
        rel_parts = tex_file.relative_to(project_root).parts
    except ValueError:
        rel_parts = tex_file.parts
    return any(part in FLOAT_ASSET_DIRS for part in rel_parts[:-1])


def _read_cached_tex_file(tex_file: Path, tex_contents: Optional[Dict[Path, str]] = None) -> Optional[str]:
    if tex_contents is not None and tex_file in tex_contents:
        return tex_contents[tex_file]
    try:
        return tex_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _find_tex_file_for_defect(project_root: Path, labels: List[str], preferred: Path) -> Optional[Path]:
    if not labels:
        return None
    for path in [preferred] + [p for p in _iter_project_tex_files(project_root) if p != preferred]:
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _contains_any_labeled_float(content, labels):
            return path
    return None


def _input_targets_path(raw_target: str, *, source_file: Path, project_root: Path, target_file: Path) -> bool:
    target = raw_target.strip()
    if not target:
        return False
    candidate = Path(target)
    if not candidate.suffix:
        candidate = candidate.with_suffix(".tex")
    candidates = [candidate.resolve()] if candidate.is_absolute() else [
        (source_file.parent / candidate).resolve(),
        (project_root / candidate).resolve(),
    ]
    return target_file.resolve() in candidates


def _find_input_command_for_file(
    project_root: Path,
    target_file: Path,
    tex_contents: Optional[Dict[Path, str]] = None,
) -> Optional[tuple[Path, int, int, str]]:
    input_pattern = re.compile(r'\\(?:input|include)\s*\{([^}]+)\}')
    for tex_file in _iter_project_tex_files(project_root):
        if tex_file == target_file:
            continue
        content = _read_cached_tex_file(tex_file, tex_contents)
        if content is None:
            continue
        for match in input_pattern.finditer(content):
            if _is_commented_at(content, match.start()):
                continue
            if _input_targets_path(match.group(1), source_file=tex_file, project_root=project_root, target_file=target_file):
                line_start = content.rfind("\n", 0, match.start()) + 1
                line_end = content.find("\n", match.end())
                line_end = len(content) if line_end == -1 else line_end + 1
                return tex_file, line_start, line_end, content[line_start:line_end].strip()
    return None


def _find_reference_anchor_file(
    project_root: Path,
    float_label: str,
    reference_text: Optional[str],
    tex_contents: Optional[Dict[Path, str]] = None,
    *,
    exclude_float_asset_files: bool = False,
) -> Optional[tuple[Path, int, int]]:
    patterns = []
    if reference_text:
        patterns.append(re.escape(reference_text))
    patterns.append(r'\\(?:ref|autoref|cref|Cref)\{' + re.escape(float_label) + r'\}')
    for tex_file in _iter_project_tex_files(project_root):
        if exclude_float_asset_files and _is_float_asset_file(project_root, tex_file):
            continue
        content = _read_cached_tex_file(tex_file, tex_contents)
        if content is None:
            continue
        for pattern in patterns:
            for match in re.finditer(pattern, content):
                if _is_commented_at(content, match.start()):
                    continue
                return tex_file, match.start(), match.end()
    return None


def _find_reference_anchor_file_candidates(
    project_root: Path,
    float_label: str,
    reference_text: Optional[str],
    tex_contents: Optional[Dict[Path, str]] = None,
    *,
    exclude_float_asset_files: bool = False,
) -> List[tuple[Path, int, int]]:
    patterns = []
    if reference_text:
        patterns.append(re.escape(reference_text))
    patterns.append(r'\\(?:ref|autoref|cref|Cref)\{' + re.escape(float_label) + r'\}')

    candidates: List[tuple[Path, int, int]] = []
    seen: set[tuple[Path, int, int]] = set()
    for tex_file in _iter_project_tex_files(project_root):
        if exclude_float_asset_files and _is_float_asset_file(project_root, tex_file):
            continue
        content = _read_cached_tex_file(tex_file, tex_contents)
        if content is None:
            continue
        for pattern in patterns:
            for match in re.finditer(pattern, content):
                if _is_commented_at(content, match.start()):
                    continue
                key = (tex_file, match.start(), match.end())
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(key)
    return candidates


def _input_command_for_target(*, from_file: Path, target_file: Path) -> str:
    rel = os.path.relpath(target_file, start=from_file.parent)
    rel_path = Path(rel)
    rel_text = rel_path.as_posix()
    if rel_path.suffix == ".tex":
        rel_text = rel_text[:-4]
    return f"\\input{{{rel_text}}}"


def _anchor_paragraph_float_ref_count(tex_content: str, ref_start: int, ref_end: int) -> int:
    para_start = _find_paragraph_start(tex_content, ref_start) or 0
    para_end = _find_paragraph_end(tex_content, ref_end) or len(tex_content)
    paragraph = tex_content[para_start:para_end]
    count = 0
    for match in re.finditer(r'\\(?:ref|autoref|cref|Cref)\{((?:fig|tab):[^}]+)\}', paragraph):
        if _is_commented_at(tex_content, para_start + match.start()):
            continue
        count += 1
    return count


def _find_labeled_float_file(
    project_root: Path,
    float_label: str,
    tex_contents: Optional[Dict[Path, str]] = None,
) -> Optional[Path]:
    float_type = _label_float_type(float_label)
    for tex_file in _iter_project_tex_files(project_root):
        content = _read_cached_tex_file(tex_file, tex_contents)
        if content is None:
            continue
        match, _, _, _ = _find_labeled_float_block(content, float_label, float_type)
        if match:
            return tex_file
    return None


def _find_labeled_float_env_name(
    tex_content: str,
    float_label: str,
) -> Optional[str]:
    _, env_name, block_start, block_end = _find_labeled_float_block(
        tex_content,
        float_label,
        _label_float_type(float_label),
    )
    if block_start is None or block_end is None:
        return None
    return env_name


def _anchor_paragraph_key(tex_content: str, ref_start: int, ref_end: int) -> Optional[tuple[int, int]]:
    para_start = _find_paragraph_start(tex_content, ref_start)
    para_end = _find_paragraph_end(tex_content, ref_end)
    if para_start is None or para_end is None:
        return None
    return para_start, para_end


def _position_inside_latex_environment(tex_content: str, position: int, env_names: set[str]) -> bool:
    prefix = tex_content[:position]
    events: List[tuple[int, str, str]] = []
    pattern = re.compile(r'\\(begin|end)\{([^}]+)\}')
    for match in pattern.finditer(prefix):
        if _is_commented_at(prefix, match.start()):
            continue
        env_name = match.group(2)
        if env_name in env_names:
            events.append((match.start(), match.group(1), env_name))

    stack: List[str] = []
    for _, event, env_name in events:
        if event == "begin":
            stack.append(env_name)
        elif env_name in stack:
            stack.reverse()
            stack.remove(env_name)
            stack.reverse()
    return bool(stack)


def _paragraph_starts_with_item(tex_content: str, para_start: int, ref_start: int) -> bool:
    prefix = tex_content[para_start:ref_start]
    return bool(re.search(r'(^|\n)\s*\\item\b', prefix))


def _choose_distribution_reference_anchor(
    *,
    project_root: Path,
    float_label: str,
    tex_contents: Dict[Path, str],
    used_anchor_keys: set[tuple[Path, int, int]],
) -> Optional[tuple[Path, int, int, tuple[int, int]]]:
    candidates = _find_reference_anchor_file_candidates(
        project_root,
        float_label,
        None,
        tex_contents,
        exclude_float_asset_files=True,
    )
    fallback: Optional[tuple[Path, int, int, tuple[int, int]]] = None
    for ref_file, ref_start, ref_end in candidates:
        ref_content = _read_cached_tex_file(ref_file, tex_contents)
        if ref_content is None:
            continue
        anchor_key = _anchor_paragraph_key(ref_content, ref_start, ref_end)
        if anchor_key is None:
            continue
        paragraph_key = (ref_file, anchor_key[0], anchor_key[1])
        if paragraph_key in used_anchor_keys:
            continue
        if _anchor_paragraph_float_ref_count(ref_content, ref_start, ref_end) > 2:
            continue

        candidate = (ref_file, ref_start, ref_end, anchor_key)
        in_list = _position_inside_latex_environment(
            ref_content,
            ref_start,
            {"itemize", "enumerate", "description"},
        ) or _paragraph_starts_with_item(ref_content, anchor_key[0], ref_start)
        if in_list:
            if fallback is None:
                fallback = candidate
            continue
        return candidate
    return fallback


def _distribute_included_float_inputs_for_cluster(
    *,
    project_root: Path,
    float_labels: List[str],
    tex_contents: Dict[Path, str],
) -> Tuple[List[tuple[Path, str, str]], Optional[FixResult]]:
    """
    Higher-level B3 strategy: when a visual cluster is caused by a block of
    included composite float files, move at most one input to each semantic
    first-reference paragraph instead of only changing float placement flags.
    """
    if len(float_labels) < 2:
        return [], None

    working_contents: Dict[Path, str] = dict(tex_contents)
    original_contents: Dict[Path, str] = {}
    moved_labels: List[str] = []
    used_targets: set[Path] = set()
    used_anchor_keys: set[tuple[Path, int, int]] = set()

    for label in float_labels:
        target_file = _find_labeled_float_file(project_root, label, working_contents)
        if target_file is None or target_file in used_targets:
            continue
        include_info = _find_input_command_for_file(project_root, target_file, working_contents)
        ref_info_with_anchor = _choose_distribution_reference_anchor(
            project_root=project_root,
            float_label=label,
            tex_contents=working_contents,
            used_anchor_keys=used_anchor_keys,
        )
        if include_info is None or ref_info_with_anchor is None:
            continue

        include_file, include_start, include_end, include_command = include_info
        ref_file, ref_start, ref_end, anchor_key = ref_info_with_anchor
        if ref_file == target_file or _is_float_asset_file(project_root, ref_file):
            continue

        ref_content = _read_cached_tex_file(ref_file, working_contents)
        include_content = _read_cached_tex_file(include_file, working_contents)
        if ref_content is None or include_content is None:
            continue

        paragraph_key = (ref_file, anchor_key[0], anchor_key[1])
        target_content = _read_cached_tex_file(target_file, working_contents)
        env_name = _find_labeled_float_env_name(target_content or "", label)
        insert_pos = anchor_key[0] if env_name and env_name.endswith("*") else anchor_key[1]
        insert_command = include_command if include_file == ref_file else _input_command_for_target(
            from_file=ref_file,
            target_file=target_file,
        )

        if include_file == ref_file:
            if include_start <= insert_pos <= include_end:
                continue
            without_include = include_content[:include_start] + include_content[include_end:]
            adjusted_insert = insert_pos
            if include_start < insert_pos:
                adjusted_insert -= include_end - include_start
            existing_window = without_include[max(0, adjusted_insert - 80):adjusted_insert + 80]
            if insert_command in existing_window:
                continue
            updated = without_include[:adjusted_insert] + "\n" + insert_command + "\n" + without_include[adjusted_insert:]
            original_contents.setdefault(include_file, include_content)
            working_contents[include_file] = updated
        else:
            updated_include = include_content[:include_start] + include_content[include_end:]
            existing_window = ref_content[max(0, insert_pos - 80):insert_pos + 80]
            if insert_command in existing_window:
                continue
            updated_ref = ref_content[:insert_pos] + "\n" + insert_command + "\n" + ref_content[insert_pos:]
            original_contents.setdefault(include_file, include_content)
            original_contents.setdefault(ref_file, ref_content)
            working_contents[include_file] = updated_include
            working_contents[ref_file] = updated_ref

        used_targets.add(target_file)
        used_anchor_keys.add(paragraph_key)
        moved_labels.append(label)

    if not moved_labels:
        return [], None

    updates = [
        (path, original_contents[path], working_contents[path])
        for path in original_contents
        if working_contents.get(path) != original_contents[path]
    ]
    if not updates:
        return [], None

    return updates, FixResult(
        defect_id="B3",
        object_name=", ".join(moved_labels),
        action="按首次引用段落分散浮动体 input，避免多个 composite floats 聚集在同一源码位置",
        before="clustered float input block",
        after="semantic-anchor distributed float inputs",
        success=True,
    )


def _move_included_float_input_near_reference(
    *,
    project_root: Path,
    target_file: Path,
    float_label: str,
    reference_text: Optional[str],
) -> Tuple[List[tuple[Path, str, str]], Optional[FixResult]]:
    include_info = _find_input_command_for_file(project_root, target_file)
    ref_info = _find_reference_anchor_file(project_root, float_label, reference_text)
    if include_info is None or ref_info is None:
        return [], None

    include_file, include_start, include_end, include_command = include_info
    ref_file, ref_start, ref_end = ref_info
    if ref_file == target_file:
        return [], None
    try:
        ref_parts = ref_file.relative_to(project_root).parts
    except ValueError:
        ref_parts = ref_file.parts
    if any(part in FLOAT_ASSET_DIRS for part in ref_parts[:-1]):
        return [], None

    try:
        include_content = include_file.read_text(encoding="utf-8")
        ref_content = ref_file.read_text(encoding="utf-8")
        target_content = target_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [], None

    insert_command = _input_command_for_target(from_file=ref_file, target_file=target_file)
    anchor_key = _anchor_paragraph_key(ref_content, ref_start, ref_end)
    if anchor_key is None:
        return [], None
    if _anchor_paragraph_float_ref_count(ref_content, ref_start, ref_end) > 2:
        return [], None
    env_name = _find_labeled_float_env_name(target_content, float_label)
    insert_pos = anchor_key[0] if env_name and env_name.endswith("*") else anchor_key[1]

    if include_file == ref_file:
        if include_start <= insert_pos <= include_end:
            return [], None
        without_include = include_content[:include_start] + include_content[include_end:]
        adjusted_insert = insert_pos
        if include_start < insert_pos:
            adjusted_insert -= include_end - include_start
        insertion = "\n" + include_command + "\n"
        updated = without_include[:adjusted_insert] + insertion + without_include[adjusted_insert:]
        return [(include_file, include_content, updated)], FixResult(
            defect_id="B1",
            object_name=float_label,
            action="将浮动体 input 移到首次引用段落附近",
            before=include_command,
            after=include_command,
            success=True,
        )

    updated_include = include_content[:include_start] + include_content[include_end:]
    insertion = "\n" + insert_command + "\n"
    updated_ref = ref_content[:insert_pos] + insertion + ref_content[insert_pos:]
    return [
        (include_file, include_content, updated_include),
        (ref_file, ref_content, updated_ref),
    ], FixResult(
        defect_id="B1",
        object_name=float_label,
        action="将跨文件浮动体 input 迁移到首次引用段落附近",
        before=f"{include_file.name}: {include_command}",
        after=f"{ref_file.name}: {insert_command}",
        success=True,
    )


def _move_float_after_reference_paragraph(
    tex_content: str,
    block_start: int,
    block_end: int,
    ref_end: int,
) -> str:
    insert_pos = _find_paragraph_end(tex_content, ref_end)
    if insert_pos is None:
        return tex_content

    return _move_float_to_insert_pos(tex_content, block_start, block_end, insert_pos)


def _move_float_to_insert_pos(
    tex_content: str,
    block_start: int,
    block_end: int,
    insert_pos: int,
) -> str:
    if insert_pos is None:
        return tex_content

    insert_pos = _clamp_insert_pos_before_bibliography(tex_content, insert_pos)
    if insert_pos is None:
        return tex_content

    block = tex_content[block_start:block_end]
    without_block = tex_content[:block_start] + tex_content[block_end:]

    adjusted_insert_pos = insert_pos
    if insert_pos > block_start:
        adjusted_insert_pos -= (block_end - block_start)

    if adjusted_insert_pos < 0 or adjusted_insert_pos > len(without_block):
        return tex_content

    insertion = "\n\n" + block.strip() + "\n\n"
    return without_block[:adjusted_insert_pos] + insertion + without_block[adjusted_insert_pos:]


def _find_bibliography_start(tex_content: str) -> Optional[int]:
    candidates: List[int] = []
    for pattern in (
        r'\\bibliography\{',
        r'\\printbibliography\b',
        r'\\begin\{thebibliography\}',
    ):
        match = re.search(pattern, tex_content)
        if match:
            candidates.append(match.start())
    if not candidates:
        return None
    return min(candidates)


def _clamp_insert_pos_before_bibliography(tex_content: str, insert_pos: Optional[int]) -> Optional[int]:
    if insert_pos is None:
        return None
    bibliography_start = _find_bibliography_start(tex_content)
    if bibliography_start is None:
        return insert_pos
    return min(insert_pos, bibliography_start)


def _would_cross_same_type_float(
    tex_content: str,
    *,
    float_type: str,
    block_start: int,
    block_end: int,
    insert_pos: int,
) -> bool:
    env_names = [float_type, f"{float_type}*"]
    search_start = min(block_end, insert_pos)
    search_end = max(block_start, insert_pos)

    if search_end <= search_start:
        return False

    for env_name in env_names:
        pattern = r'\\begin\{' + re.escape(env_name) + r'\}(?:\[[^\]]*\])?'
        for match in re.finditer(pattern, tex_content):
            other_start = match.start()
            if other_start == block_start:
                continue
            end_match = re.search(
                r'\\end\{' + re.escape(env_name) + r'\}',
                tex_content[match.end():],
                re.DOTALL,
            )
            if not end_match:
                continue
            other_end = match.end() + end_match.end()
            if other_end <= search_start or other_start >= search_end:
                continue
            block = tex_content[other_start:other_end]
            if re.search(r'\\label\{[^}]+\}', block):
                return True

    return False


def _find_paragraph_end(tex_content: str, position: int) -> Optional[int]:
    paragraph_break = re.search(r'\n\s*\n', tex_content[position:])
    section_break = re.search(r'\n\\(?:sub)*section\*?\{', tex_content[position:])
    command_break = re.search(r'\n\\(?:begin|end|caption|label|FloatBarrier|paragraph|subparagraph)\b', tex_content[position:])

    candidates = []
    if paragraph_break:
        candidates.append(position + paragraph_break.start())
    if section_break:
        candidates.append(position + section_break.start())
    if command_break:
        candidates.append(position + command_break.start())
    if candidates:
        return min(candidates)

    sentence_break = re.search(r'[.!?](?:["\']?)(?=\s|$)', tex_content[position:])
    if sentence_break:
        return position + sentence_break.end()

    return len(tex_content)


def _find_paragraph_start(tex_content: str, position: int) -> Optional[int]:
    if position <= 0:
        return 0

    prefix = tex_content[:position]
    paragraph_breaks = list(re.finditer(r'\n\s*\n', prefix))
    section_breaks = list(re.finditer(r'\n\\(?:sub)*section\*?\{', prefix))
    command_breaks = list(re.finditer(r'\n\\(?:begin|end|caption|label|FloatBarrier|paragraph|subparagraph)\b', prefix))

    candidates = [0]
    if paragraph_breaks:
        candidates.append(paragraph_breaks[-1].end())
    if section_breaks:
        candidates.append(section_breaks[-1].start() + 1)
    if command_breaks:
        candidates.append(command_breaks[-1].start() + 1)
    return max(candidates)


def _find_star_float_anchor_insert_pos(tex_content: str, ref_start: int) -> Optional[int]:
    subsection_starts = list(re.finditer(r'\n\\(?:sub)*section\*?\{', tex_content[:ref_start]))
    if subsection_starts:
        return subsection_starts[-1].start() + 1
    return _find_paragraph_start(tex_content, ref_start)


def _normalize_float_block_for_semantic_fix(
    tex_content: str,
    *,
    float_label: str,
    env_name: str,
    block_start: int,
    block_end: int,
) -> str:
    block = tex_content[block_start:block_end]
    updated_block = _ensure_label_after_caption_in_block(block, float_label=float_label)
    updated_block = _normalize_position_spec_for_semantic_fix(updated_block, env_name=env_name)
    updated_block = _normalize_figure_width_for_semantic_fix(updated_block, env_name=env_name)
    if updated_block == block:
        return tex_content
    return tex_content[:block_start] + updated_block + tex_content[block_end:]


def _ensure_label_after_caption_in_block(block: str, *, float_label: str) -> str:
    label_token = f"\\label{{{float_label}}}"
    label_idx = block.find(label_token)
    caption_idx = block.find("\\caption{")
    if label_idx == -1 or caption_idx == -1 or label_idx > caption_idx:
        return block

    label_line_start = block.rfind('\n', 0, label_idx)
    label_line_start = 0 if label_line_start == -1 else label_line_start + 1
    label_line_end = block.find('\n', label_idx)
    label_line_end = len(block) if label_line_end == -1 else label_line_end + 1
    label_line = block[label_line_start:label_line_end]
    stripped_label = label_line.strip()
    if stripped_label != label_token:
        return block

    without_label = block[:label_line_start] + block[label_line_end:]
    caption_idx = without_label.find("\\caption{")
    if caption_idx == -1:
        return block
    caption_line_end = without_label.find('\n', caption_idx)
    if caption_line_end == -1:
        caption_line_end = len(without_label)
        insertion = "\n" + stripped_label
    else:
        insertion = "\n" + stripped_label
    return without_label[:caption_line_end] + insertion + without_label[caption_line_end:]


def _normalize_position_spec_for_semantic_fix(block: str, *, env_name: str) -> str:
    pattern = r'(\\begin\{' + re.escape(env_name) + r'\})(\[[^\]]*\])?'
    match = re.search(pattern, block)
    if not match:
        return block

    current_param = match.group(2) or ""
    if current_param in {"[p]", "[!p]", "[b]", "[!b]"}:
        new_param = "[ht]"
    elif not current_param:
        new_param = "[ht]"
    else:
        return block

    if current_param:
        return block[:match.start(2)] + new_param + block[match.end(2):]
    insert_at = match.end(1)
    return block[:insert_at] + new_param + block[insert_at:]


def _apply_global_restrictive_position_normalization(
    tex_content: str,
) -> Tuple[str, List[FixResult]]:
    pattern = re.compile(r'\\begin\{(figure\*?|table\*?)\}(\[[^\]]*\])?')
    restrictive_params = {"[p]", "[!p]", "[b]", "[!b]"}
    changes: List[FixResult] = []
    cursor = 0
    parts: List[str] = []

    for match in pattern.finditer(tex_content):
        parts.append(tex_content[cursor:match.start()])
        env_name = match.group(1)
        current_param = match.group(2) or ""
        replacement = match.group(0)
        if current_param in restrictive_params:
            replacement = f"\\begin{{{env_name}}}[ht]"
            tail = tex_content[match.end():]
            end_match = re.search(r'\\end\{' + re.escape(env_name) + r'\}', tail)
            block = tail[: end_match.end()] if end_match else tail[:400]
            label_match = re.search(r'\\label\{([^}]+)\}', block)
            object_name = label_match.group(1) if label_match else env_name
            changes.append(
                FixResult(
                    defect_id="B1",
                    object_name=object_name,
                    action=f"全局基线：将浮动体位置参数从 {current_param} 归一化为 [ht]",
                    before=f"\\begin{{{env_name}}}{current_param}",
                    after=f"\\begin{{{env_name}}}[ht]",
                    success=True,
                )
            )
        parts.append(replacement)
        cursor = match.end()

    parts.append(tex_content[cursor:])
    return "".join(parts), changes


def _find_endmatter_start(tex_content: str) -> Optional[int]:
    patterns = [
        r'^\s*\\section\*?\{Acknowledg(?:e)?ments\}',
        r'^\s*\\section\*?\{References\}',
        r'\\bibliography\{',
        r'\\printbibliography\b',
        r'\\begin\{thebibliography\}',
    ]
    candidates: List[int] = []
    for pattern in patterns:
        match = re.search(pattern, tex_content, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            candidates.append(match.start())
    return min(candidates) if candidates else None


def _enforce_endmatter_float_barrier(
    tex_content: str,
) -> Tuple[str, Optional[FixResult]]:
    endmatter_start = _find_endmatter_start(tex_content)
    if endmatter_start is None:
        return tex_content, None

    if not re.search(r'\\begin\{(figure\*?|table\*?)\}', tex_content[:endmatter_start]):
        return tex_content, None

    lookback_start = max(0, endmatter_start - 240)
    if re.search(r'\\FloatBarrier\b', tex_content[lookback_start:endmatter_start]):
        return tex_content, None

    prefix = tex_content[:endmatter_start].rstrip()
    suffix = tex_content[endmatter_start:].lstrip()
    modified_content = prefix + "\n\n\\FloatBarrier\n\n" + suffix
    return modified_content, FixResult(
        defect_id="B1",
        object_name="endmatter-boundary",
        action="在致谢/参考文献前插入 \\FloatBarrier，禁止正文浮动体漂入 endmatter",
        before="[no FloatBarrier before endmatter]",
        after="\\FloatBarrier before acknowledgements/references",
        success=True,
    )


def _normalize_figure_width_for_semantic_fix(block: str, *, env_name: str) -> str:
    if not env_name.startswith("figure") or env_name.endswith("*"):
        return block

    include_match = re.search(r'\\includegraphics(?:\[[^\]]*\])?\{[^}]+\}', block)
    if not include_match:
        return block

    include_graphic = include_match.group(0)
    width_match = re.search(r'width=([0-9]*\.?[0-9]+)\\(linewidth|columnwidth|textwidth)', include_graphic)
    if not width_match:
        return block

    try:
        factor = float(width_match.group(1))
    except ValueError:
        return block
    if factor >= 0.75:
        return block

    new_graphic = re.sub(
        r'width=[0-9]*\.?[0-9]+\\(?:linewidth|columnwidth|textwidth)',
        r'width=0.98\\linewidth',
        include_graphic,
        count=1,
    )
    return block.replace(include_graphic, new_graphic, 1)


def add_floatbarrier_to_preamble(
    tex_content: str,
) -> Tuple[str, Optional[FixResult]]:
    r"""
    在导言区添加 placeins 宏包以支持 \FloatBarrier
    """
    if '\\usepackage{placeins}' in tex_content:
        return tex_content, None

    # 在 \begin{document} 前添加
    match = re.search(r'\\begin\{document\}', tex_content)
    if match:
        insert_pos = match.start()
        modified_content = tex_content[:insert_pos] + "\\usepackage{placeins}\n" + tex_content[insert_pos:]
        return modified_content, FixResult(
            defect_id="B1",
            object_name="导言区",
            action="添加 placeins 宏包以支持 \\FloatBarrier",
            before="\\begin{document}",
            after="\\usepackage{placeins}\n\\begin{document}",
            success=True,
        )

    return tex_content, None


# ============================================================
# B2：浮动体大小不适配栏宽
# ============================================================

def fix_figure_width_mismatch(
    tex_content: str,
    figure_label: str,
    template_type: str = "single_column",
) -> Tuple[str, Optional[FixResult]]:
    r"""
    修复图片宽度不适配栏宽问题

    策略优先级:
    1. 设置宽度为 \linewidth
    2. 区分单栏/跨栏 (双栏模板)
    3. 设置高度 + keepaspectratio

    Args:
        tex_content: .tex 文件内容
        figure_label: 图片标签
        template_type: 模板类型 ("single_column" | "double_column")

    Returns:
        (修改后的内容，修复结果)
    """
    _, env_name, block_start, block_end = _find_labeled_float_block(
        tex_content,
        figure_label,
        "figure",
    )
    if block_start is None or block_end is None:
        return tex_content, None
    block = tex_content[block_start:block_end]
    include_match = re.search(r'\\includegraphics(?:\[[^\]]*\])?\{[^}]+\}', block)
    if not include_match:
        return tex_content, None
    include_graphic = include_match.group(0)

    target_width = r"\textwidth" if template_type == "double_column" and env_name.endswith("*") else r"\linewidth"
    width_match = re.search(r'width=([^\s,\]]+)', include_graphic)
    if width_match:
        current_width = width_match.group(1).strip()
        if current_width == target_width:
            return tex_content, None

    if width_match:
        # 替换现有宽度为目标栏宽；跨栏图必须显式使用 \textwidth。
        new_graphic = re.sub(
            r'width=[^\s,\]]+',
            lambda _match: f"width={target_width}",
            include_graphic
        )
    else:
        # 没有 width 参数，添加
        # 检查是否有可选参数
        if include_graphic.startswith('\\includegraphics['):
            # 有可选参数，在 ] 前插入
            bracket_pos = include_graphic.find(']')
            new_graphic = include_graphic[:bracket_pos] + f',width={target_width}' + include_graphic[bracket_pos:]
        else:
            # 没有可选参数，添加
            new_graphic = include_graphic.replace(
                '\\includegraphics',
                f'\\includegraphics[width={target_width}]'
            )

    updated_block = block.replace(include_graphic, new_graphic, 1)
    modified_content = tex_content[:block_start] + updated_block + tex_content[block_end:]

    return modified_content, FixResult(
        defect_id="B2",
        object_name=figure_label,
        action=f"将图片宽度设为 {target_width}",
        before=include_graphic[:50] + "...",
        after=new_graphic[:50] + "...",
        success=True,
    )


def fix_table_width_mismatch(
    tex_content: str,
    table_label: str,
) -> Tuple[str, Optional[FixResult]]:
    r"""
    修复表格宽度不适配栏宽问题

    策略优先级:
    1. 将 tabular 改为 tabularx 并设宽度为 \linewidth
    2. 调整列规格
    3. 使用 sidewaystable 旋转超宽表格

    Args:
        tex_content: .tex 文件内容
        table_label: 表格标签

    Returns:
        (修改后的内容，修复结果)
    """
    _, env_name, block_start, block_end = _find_labeled_float_block(
        tex_content,
        table_label,
        "table",
    )
    if block_start is None or block_end is None:
        return tex_content, None
    block = tex_content[block_start:block_end]
    width_spec = r"\textwidth" if env_name.endswith("*") else r"\linewidth"
    tabular_span = find_first_tabular_span(block)
    column_spec = tabular_span.get("column_spec") if tabular_span else None

    if column_spec and is_plain_alignment_column_spec(column_spec):
        updated_block, rewrite_info = rewrite_first_tabular_to_tabular_star(
            block,
            width_spec=width_spec,
            tighten_tabcolsep=True,
        )
        if rewrite_info is None:
            updated_block, rewrite_info = remove_resizebox_around_first_tabular(
                block,
                tighten_tabcolsep=True,
            )
        if rewrite_info is not None:
            actions: List[str] = []
            before_fragments: List[str] = []
            after_fragments: List[str] = []
            if rewrite_info.get("removed_resizebox"):
                actions.append("移除 resizebox，恢复原始表格字号")
                before_fragments.append(
                    f"\\resizebox{{...}}{{!}}{{\\begin{{tabular}}{rewrite_info['column_spec_before']}}}"
                )
            else:
                before_fragments.append(
                    f"\\begin{{tabular}}{rewrite_info['column_spec_before']}"
                )
            if rewrite_info.get("stretched_tabular"):
                actions.append(f"将简单对齐表改为 tabular* 并铺满 {width_spec}")
                after_fragments.append(
                    f"\\begin{{tabular*}}{{{width_spec}}}{convert_plain_alignment_to_stretched_spec(column_spec)}"
                )
            else:
                after_fragments.append(
                    f"\\begin{{tabular}}{rewrite_info['column_spec_after']}"
                )
            if rewrite_info.get("tightened_tabcolsep"):
                actions.append("收紧局部 \\tabcolsep 以避免表格仍然溢出")
                before_fragments.append(str(rewrite_info.get("tabcolsep_before")))
                after_fragments.append(str(rewrite_info.get("tabcolsep_after")))

            modified_content = tex_content[:block_start] + updated_block + tex_content[block_end:]
            return modified_content, FixResult(
                defect_id="B2",
                object_name=table_label,
                action="；".join(actions),
                before=" | ".join(before_fragments),
                after=" | ".join(after_fragments),
                success=True,
            )

        return tex_content, None

    updated_block, rewrite_info = rewrite_first_tabular_to_tabularx(
        block,
        width_spec=width_spec,
        spec_converter=convert_preserve_first_column_to_x,
        tighten_tabcolsep=True,
    )
    if rewrite_info is None:
        return tex_content, None

    actions: List[str] = []
    before_fragments: List[str] = []
    after_fragments: List[str] = []
    if rewrite_info.get("column_spec_before") is not None:
        if rewrite_info.get("removed_resizebox"):
            actions.append("移除 resizebox 并将 tabular 改为 tabularx")
            before_fragments.append(
                f"\\resizebox{{...}}{{!}}{{\\begin{{tabular}}{rewrite_info['column_spec_before']}}}"
            )
        else:
            actions.append("将 tabular 改为 tabularx")
            before_fragments.append(
                f"\\begin{{tabular}}{rewrite_info['column_spec_before']}"
            )
        after_fragments.append(
            f"\\begin{{tabularx}}{{{width_spec}}}{rewrite_info['column_spec_after']}"
        )
    if rewrite_info.get("tightened_tabcolsep"):
        actions.append("收紧局部 \\tabcolsep 以压缩表格总宽度")
        before_fragments.append(str(rewrite_info.get("tabcolsep_before")))
        after_fragments.append(str(rewrite_info.get("tabcolsep_after")))

    modified_content = tex_content[:block_start] + updated_block + tex_content[block_end:]
    modified_content = ensure_tabularx_package(modified_content)

    return modified_content, FixResult(
        defect_id="B2",
        object_name=table_label,
        action="；".join(actions),
        before=" | ".join(before_fragments),
        after=" | ".join(after_fragments),
        success=True,
    )


def fix_wide_float_in_double_column(
    tex_content: str,
    float_label: str,
) -> Tuple[str, Optional[FixResult]]:
    """
    在双栏模板中修复宽浮动体

    策略:
    1. 将 figure 改为 figure* (跨栏)
    2. 将 table 改为 table*
    3. 宽度设为 \\textwidth

    Args:
        tex_content: .tex 文件内容
        float_label: 浮动体标签

    Returns:
        (修改后的内容，修复结果)
    """
    # 确定浮动体类型
    is_figure = "fig" in float_label.lower()
    float_type = "figure" if is_figure else "table"

    # 查找浮动体环境
    pattern = r'\\begin\{' + float_type + r'\}(\[[htbp]+\])?'
    matches = list(re.finditer(pattern, tex_content))

    target_match = None
    for match in matches:
        label_pattern = r'\\label\{' + re.escape(float_label) + r'\}'
        after_start = match.end()
        label_match = re.search(label_pattern, tex_content[after_start:after_start + 500])
        if label_match:
            target_match = match
            break

    if not target_match:
        return tex_content, None

    # 改为跨栏环境
    old_env = f"\\begin{{{float_type}}}"
    new_env = f"\\begin{{{float_type}*}}"

    modified_content = tex_content.replace(old_env, new_env, 1)
    modified_content = modified_content.replace(
        f"\\end{{{float_type}}}",
        f"\\end{{{float_type}*}}",
        1
    )

    return modified_content, FixResult(
        defect_id="B2",
        object_name=float_label,
        action=f"将 {float_type} 改为 {float_type}* 以跨栏显示",
        before=old_env,
        after=new_env,
        success=True,
    )


# ============================================================
# B3：浮动体连续堆叠
# ============================================================

def fix_float_clustering(
    tex_content: str,
    float_labels: List[str],
) -> Tuple[str, Optional[FixResult]]:
    r"""
    修复浮动体连续堆叠问题

    策略优先级:
    1. 分散浮动体位置参数
    2. 在浮动体之间插入正文
    3. 使用 \FloatBarrier 控制

    Args:
        tex_content: .tex 文件内容
        float_labels: 堆叠的浮动体标签列表

    Returns:
        (修改后的内容，修复结果)
    """
    if len(float_labels) < 2:
        return tex_content, None

    # 策略 1: 为每个浮动体分配不同的位置偏好
    position_prefs = ["[t]", "[ht]", "[b]", "[htbp]"]
    changes_made = []

    for i, label in enumerate(float_labels[:len(position_prefs)]):
        float_type = "figure" if "fig" in label.lower() else "table"
        match, env_name, _, _ = _find_labeled_float_block(tex_content, label, float_type)
        if not match:
            continue

        current_param = match.group(1) if match.group(1) else ""
        new_param = position_prefs[i]

        if _b3_position_already_dispersive(current_param):
            continue

        if current_param != new_param:
            if current_param:
                tex_content = tex_content[:match.start(1)] + new_param + tex_content[match.end(1):]
            else:
                insert_pos = match.end()
                tex_content = tex_content[:insert_pos] + new_param + tex_content[insert_pos:]

            changes_made.append({
                "label": label,
                "before": current_param or f"\\begin{{{env_name}}}",
                "after": f"\\begin{{{env_name}}}{new_param}",
            })

    if changes_made:
        return tex_content, FixResult(
            defect_id="B3",
            object_name=", ".join([c["label"] for c in changes_made]),
            action="分散浮动体位置参数以避免堆叠",
            before="; ".join([c["before"] for c in changes_made]),
            after="; ".join([c["after"] for c in changes_made]),
            success=True,
        )

    return tex_content, None


def _b3_position_already_dispersive(position_param: str) -> bool:
    if not position_param:
        return False
    if position_param in {"[t]", "[!t]"}:
        return True
    flags = set(position_param.strip("[]!"))
    if "p" in flags:
        return False
    return "h" in flags and "t" in flags


def _block_is_table_composite(block: str, labels: List[str]) -> bool:
    if re.search(r'\\captionof\{table\}', block):
        return True
    if any(str(label).lower().startswith("tab:") for label in labels):
        return True
    return bool(re.search(r'\\label\{tab:[^}]+\}', block))


def _rewrite_float_block_for_tail_packing(
    block: str,
    *,
    env_name: str,
    labels: List[str],
) -> Optional[tuple[str, str, str]]:
    begin_pattern = re.compile(r'\\begin\{' + re.escape(env_name) + r'\}(\[[^\]]*\])?')
    begin_match = begin_pattern.match(block)
    if not begin_match:
        return None

    current_param = begin_match.group(1) or ""
    new_env = env_name
    # Preserve the outer float family so the hard content gate can verify that
    # no float was removed. Composite table wrappers using captionof{table}
    # still get repaired by changing the placement from bottom/page to top.

    if new_env.endswith("*"):
        new_param = "[!t]"
    else:
        new_param = "[ht]"

    if new_env == env_name and current_param == new_param:
        return None

    new_begin = f"\\begin{{{new_env}}}{new_param}"
    new_block = new_begin + block[begin_match.end():]
    if new_env != env_name:
        new_block = re.sub(
            r'\\end\{' + re.escape(env_name) + r'\}\s*$',
            lambda _match: f"\\end{{{new_env}}}",
            new_block,
            count=1,
            flags=re.DOTALL,
        )

    before = f"\\begin{{{env_name}}}{current_param}"
    after = f"\\begin{{{new_env}}}{new_param}"
    return new_block, before, after


def _pack_late_float_blocks_for_labels(
    *,
    project_root: Path,
    float_labels: List[str],
    tex_contents: Dict[Path, str],
) -> Tuple[List[tuple[Path, str, str]], Optional[FixResult]]:
    """
    Compact late-page float debt without moving floats after references.

    CVPR-style two-column documents can push bottom or page-only star floats to
    float pages after the bibliography. For tail clusters, normalize the
    affected star floats to top placement and repair composite table floats that
    were encoded as figure* wrappers with captionof{table}.
    """
    if not float_labels:
        return [], None

    working_contents: Dict[Path, str] = dict(tex_contents)
    original_contents: Dict[Path, str] = {}
    changed_labels: List[str] = []
    before_after: List[tuple[str, str]] = []
    seen_blocks: set[tuple[Path, int, int]] = set()

    for label in float_labels:
        target_file = _find_labeled_float_file(project_root, label, working_contents)
        if target_file is None:
            continue
        content = _read_cached_tex_file(target_file, working_contents)
        if content is None:
            continue
        match, env_name, block_start, block_end = _find_labeled_float_block(
            content,
            label,
            _label_float_type(label),
        )
        if match is None or block_start is None or block_end is None:
            continue
        block_key = (target_file, block_start, block_end)
        if block_key in seen_blocks:
            continue
        seen_blocks.add(block_key)

        block = content[block_start:block_end]
        block_labels = list(dict.fromkeys(float_labels + re.findall(r'\\label\{([^}]+)\}', block)))
        rewrite = _rewrite_float_block_for_tail_packing(
            block,
            env_name=env_name,
            labels=block_labels,
        )
        if rewrite is None:
            continue

        new_block, before, after = rewrite
        if new_block == block:
            continue
        original_contents.setdefault(target_file, content)
        updated = content[:block_start] + new_block + content[block_end:]
        working_contents[target_file] = updated
        changed_labels.extend([item for item in block_labels if item in float_labels])
        before_after.append((before, after))

    updates = [
        (path, original_contents[path], working_contents[path])
        for path in original_contents
        if working_contents.get(path) != original_contents[path]
    ]
    if not updates:
        return [], None

    unique_changed_labels = list(dict.fromkeys(changed_labels))
    return updates, FixResult(
        defect_id="B3",
        object_name=", ".join(unique_changed_labels or float_labels),
        action="tail-float packing：将尾页 composite floats 规范为正文前置页顶浮动，避免漂入参考文献后",
        before="; ".join(item[0] for item in before_after),
        after="; ".join(item[1] for item in before_after),
        success=True,
    )


# ============================================================
# B4：浮动体跨页分裂
# ============================================================

def fix_split_table(
    tex_content: str,
    table_label: str,
) -> Tuple[str, Optional[FixResult]]:
    """
    修复长表格跨页分裂问题

    策略优先级:
    1. 将 table + tabular 改为 longtable
    2. 强制表格不跨页 [!h]
    3. 拆分过大的表格

    Args:
        tex_content: .tex 文件内容
        table_label: 表格标签

    Returns:
        (修改后的内容，修复结果)
    """
    # 定位 table 环境
    pattern = r'(\\begin\{table\}(?:\[[^\]]*\])?.*?)(\\begin\{tabular\})(\{[^}]+\})(.*?)(\\end\{tabular\})(.*?\\end\{table\})'
    matches = list(re.finditer(pattern, tex_content, re.DOTALL))

    target_match = None
    for match in matches:
        label_pattern = r'\\label\{' + re.escape(table_label) + r'\}'
        label_match = re.search(label_pattern, tex_content[match.start():match.end()])
        if label_match:
            target_match = match
            break

    if not target_match:
        return tex_content, None

    full_table = target_match.group(0)
    table_start = target_match.group(1)
    tabular_start = target_match.group(2)
    column_spec = target_match.group(3)
    table_body = target_match.group(4)
    tabular_end = target_match.group(5)
    table_end = target_match.group(6)

    # 检查是否已有 caption 和 label
    caption_match = re.search(r'\\caption\{([^}]+)\}', table_start + table_body)
    label_match = re.search(r'\\label\{([^}]+)\}', table_start + table_body)

    caption_text = caption_match.group(1) if caption_match else "Long Table"
    label_text = label_match.group(1) if label_match else table_label

    # 策略 1: 改为 longtable
    # 提取表头 (第一行)
    header_match = re.search(r'([^\\]*?)(?:\\hline)?\s*([^\\]+?)\s*\\\\', table_body)
    if header_match:
        header_row = header_match.group(2).strip()

        longtable_content = f"""\\begin{{longtable}}{column_spec}
\\caption{{{caption_text}}} \\label{{{label_text}}} \\\\
\\hline
{header_row} \\\\
\\hline
\\endfirsthead
\\hline
{header_row} \\\\
\\hline
\\endhead
\\hline \\multicolumn{{{len(column_spec.strip("{}"))}}}{{r}}{{Continued on next page}} \\\\
\\endfoot
\\hline
\\endlastfoot
"""
        # 添加表体 (去除第一行)
        body_lines = table_body.split('\\\\')
        if len(body_lines) > 1:
            longtable_content += '\n'.join(body_lines[1:])

        longtable_content += "\n\\end{longtable}"

        modified_content = tex_content.replace(full_table, longtable_content, 1)

        return modified_content, FixResult(
            defect_id="B4",
            object_name=table_label,
            action="将 table+tabular 改为 longtable 以支持跨页",
            before=f"\\begin{{table}}...\\end{{tabular}}...\\end{{table}}",
            after=f"\\begin{{longtable}}{column_spec}...\\end{{longtable}}",
            success=True,
        )

    return tex_content, None


def fix_split_figure(
    tex_content: str,
    figure_label: str,
) -> Tuple[str, Optional[FixResult]]:
    """
    修复图片组跨页分裂问题

    策略:
    1. 强制图片不跨页 [!h]
    2. 拆分过大的图片组为多个独立 figure

    Args:
        tex_content: .tex 文件内容
        figure_label: 图片标签

    Returns:
        (修改后的内容，修复结果)
    """
    # 定位 figure 环境
    pattern = r'\\begin\{figure\}(\[[^\]]*\])?'
    matches = list(re.finditer(pattern, tex_content))

    target_match = None
    for match in matches:
        label_pattern = r'\\label\{' + re.escape(figure_label) + r'\}'
        after_start = match.end()
        label_match = re.search(label_pattern, tex_content[after_start:after_start + 500])
        if label_match:
            target_match = match
            break

    if not target_match:
        return tex_content, None

    current_param = target_match.group(1) if target_match.group(1) else ""

    # 策略：添加 [!h] 强制位置
    new_param = "[!h]"
    if current_param != new_param:
        if current_param:
            modified_content = tex_content[:target_match.start(1)] + new_param + tex_content[target_match.end(1):]
        else:
            insert_pos = target_match.end()
            modified_content = tex_content[:insert_pos] + new_param + tex_content[insert_pos:]

        return modified_content, FixResult(
            defect_id="B4",
            object_name=figure_label,
            action=f"添加 [!h] 强制图片放置在此处以避免分裂",
            before=f"\\begin{{figure}}{current_param}",
            after=f"\\begin{{figure}}{new_param}",
            success=True,
        )

    return tex_content, None


# ============================================================
# 主修复函数
# ============================================================

def fix_float_defects(
    tex_file_path: str,
    defects: List[Dict[str, Any]],
    template_type: str = "single_column",
) -> FloatFixReport:
    """
    修复所有 Category B 缺陷

    Args:
        tex_file_path: .tex 文件路径
        defects: 缺陷列表，每个缺陷包含:
            - defect_id: B1, B2, B3, B4
            - page: 页码
            - object: 对象名称 (图表标签)
            - description: 描述
            - ref_page: 引用页码 (B1 需要)
        template_type: 模板类型 ("single_column" | "double_column")

    Returns:
        FloatFixReport: 修复报告
    """
    tex_path = Path(tex_file_path)
    if not tex_path.exists():
        return FloatFixReport(
            status="failed",
            unresolved=[f"文件不存在：{tex_file_path}"]
        )

    try:
        tex_content = tex_path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError) as e:
        return FloatFixReport(
            status="failed",
            unresolved=[f"无法读取文件 {tex_file_path}: {e}"]
        )
    original_tex_content = tex_content
    tex_contents: Dict[Path, str] = {tex_path: tex_content}
    original_tex_contents: Dict[Path, str] = {tex_path: tex_content}

    modified_files = set()
    changes = []
    unresolved = []

    # 检查是否需要添加 placeins 宏包
    needs_placeins = bool(defects)
    if needs_placeins and '\\usepackage{placeins}' not in tex_content:
        new_content, fix_result = add_floatbarrier_to_preamble(tex_content)
        if fix_result and new_content != tex_content:
            tex_content = new_content
            tex_contents[tex_path] = tex_content
            changes.append(fix_result)
            modified_files.add(str(tex_path))

    ordered_defects = sorted(
        defects,
        key=lambda defect: (
            0 if str(defect.get("defect_id") or "") == "B1" else 1,
            int(defect.get("ref_line") or 10**9),
            int(defect.get("page") or 0),
            str(defect.get("object") or ""),
        ),
    )

    # 先把纯文本编号引用标准化为 \ref，给后续同类浮动体重排留出编号更新空间
    for defect in ordered_defects:
        if str(defect.get("defect_id") or "") != "B1":
            continue
        if str(defect.get("reference_source") or "") != "plain_text_number":
            continue
        object_name = str(defect.get("object") or "")
        if not object_name:
            continue
        float_type = "figure" if "fig" in object_name.lower() else "table"
        normalized_content, changed = _rewrite_plain_text_reference_to_label_ref(
            tex_content,
            float_label=object_name,
            float_type=float_type,
            ref_line=defect.get("ref_line"),
            reference_text=defect.get("reference_text"),
        )
        if changed:
            tex_content = normalized_content
            defect["reference_source"] = "latex_ref_normalized"
            defect["reference_text"] = f"\\ref{{{object_name}}}"

    tex_content, baseline_position_changes = _apply_global_restrictive_position_normalization(tex_content)
    if baseline_position_changes:
        tex_contents[tex_path] = tex_content
        changes.extend(baseline_position_changes)
        modified_files.add(str(tex_path))

    tex_content, endmatter_barrier_change = _enforce_endmatter_float_barrier(tex_content)
    if endmatter_barrier_change:
        tex_contents[tex_path] = tex_content
        changes.append(endmatter_barrier_change)
        modified_files.add(str(tex_path))

    for defect in ordered_defects:
        defect_id = defect.get("defect_id", "")
        page = defect.get("page", 0)
        object_name = defect.get("object", "")
        ref_page = defect.get("ref_page", 0)
        labels = _defect_labels(defect)

        if defect_id == "B3" and labels:
            distributed_files: List[tuple[Path, str, str]] = []
            distribution_result = None
            packing_applied = False
            if bool(defect.get("tail_float_packing")):
                packed_files, packing_result = _pack_late_float_blocks_for_labels(
                    project_root=tex_path.parent,
                    float_labels=labels,
                    tex_contents=tex_contents,
                )
                if packed_files and packing_result:
                    for packed_path, original_content, packed_content in packed_files:
                        tex_contents[packed_path] = packed_content
                        original_tex_contents.setdefault(packed_path, original_content)
                        if packed_path == tex_path:
                            tex_content = packed_content
                        modified_files.add(str(packed_path))
                    packing_result.page = page
                    packing_result.line_number = defect.get("line_number")
                    changes.append(packing_result)
                    packing_applied = True
            if not bool(defect.get("avoid_input_migration")):
                distributed_files, distribution_result = _distribute_included_float_inputs_for_cluster(
                    project_root=tex_path.parent,
                    float_labels=labels,
                    tex_contents=tex_contents,
                )
            if distributed_files and distribution_result:
                for moved_path, original_content, moved_content in distributed_files:
                    tex_contents[moved_path] = moved_content
                    original_tex_contents.setdefault(moved_path, original_content)
                    if moved_path == tex_path:
                        tex_content = moved_content
                    modified_files.add(str(moved_path))
                distribution_result.page = page
                distribution_result.line_number = defect.get("line_number")
                changes.append(distribution_result)

            cluster_paths = [
                path
                for path in [tex_path] + [p for p in _iter_project_tex_files(tex_path.parent) if p != tex_path]
                if _contains_any_labeled_float(
                    tex_contents.get(path) if path in tex_contents else path.read_text(encoding="utf-8", errors="replace"),
                    labels,
                )
            ]
            cluster_changed = bool(distributed_files) or packing_applied
            for cluster_path in cluster_paths:
                if cluster_path not in tex_contents:
                    try:
                        cluster_content = cluster_path.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError) as exc:
                        unresolved.append(f"B3 ({object_name or '未知对象'}): 无法读取包含浮动体的文件 {cluster_path}: {exc}")
                        continue
                    tex_contents[cluster_path] = cluster_content
                    original_tex_contents[cluster_path] = cluster_content
                cluster_content = tex_contents[cluster_path]
                new_content, fix_result = fix_float_clustering(cluster_content, float_labels=labels)
                if fix_result and new_content != cluster_content:
                    tex_contents[cluster_path] = new_content
                    if cluster_path == tex_path:
                        tex_content = new_content
                    fix_result.page = page
                    fix_result.line_number = defect.get("line_number")
                    changes.append(fix_result)
                    modified_files.add(str(cluster_path))
                    cluster_changed = True
            if not cluster_changed:
                unresolved.append(f"B3 ({object_name or '未知对象'}): 无法自动修复，可能需要人工调整")
            continue

        active_path = tex_path
        if labels and not _contains_any_labeled_float(tex_contents.get(tex_path, tex_content), labels):
            found_path = _find_tex_file_for_defect(tex_path.parent, labels, tex_path)
            if found_path is not None:
                active_path = found_path
                if active_path not in tex_contents:
                    try:
                        found_content = active_path.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError) as exc:
                        unresolved.append(f"{defect_id} ({object_name or '未知对象'}): 无法读取包含浮动体的文件 {active_path}: {exc}")
                        continue
                    tex_contents[active_path] = found_content
                    original_tex_contents[active_path] = found_content

        active_content = tex_contents.get(active_path, tex_content)

        new_content = active_content
        fix_result = None

        if defect_id == "B1":
            # 浮动体远离首次引用
            reference_source = str(defect.get("reference_source") or "")
            allow_cross_same_type = reference_source.startswith("latex_ref_normalized")
            new_content, fix_result = fix_float_reference_distance(
                active_content,
                float_label=object_name,
                ref_page=ref_page,
                float_page=page,
                ref_line=defect.get("ref_line"),
                float_line=defect.get("float_line"),
                reference_text=defect.get("reference_text"),
                reference_source=reference_source,
                force_intervention=bool(defect.get("semantic_band")),
                allow_cross_same_type=allow_cross_same_type,
            )

        elif defect_id == "B2":
            # 浮动体大小不适配
            if "fig" in object_name.lower():
                new_content, fix_result = fix_figure_width_mismatch(
                    active_content,
                    figure_label=object_name,
                    template_type=template_type,
                )
            elif "tab" in object_name.lower():
                new_content, fix_result = fix_table_width_mismatch(
                    active_content,
                    table_label=object_name,
                )

        elif defect_id == "B3":
            # 浮动体连续堆叠 - 需要收集所有堆叠的标签
            new_content, fix_result = fix_float_clustering(
                active_content,
                float_labels=[str(item) for item in (defect.get("labels") or [object_name]) if str(item)],
            )

        elif defect_id == "B4":
            # 浮动体跨页分裂
            if "fig" in object_name.lower():
                new_content, fix_result = fix_split_figure(
                    active_content,
                    figure_label=object_name,
                )
            elif "tab" in object_name.lower():
                new_content, fix_result = fix_split_table(
                    active_content,
                    table_label=object_name,
                )

        # 检查修复是否成功
        if fix_result and new_content != active_content:
            tex_contents[active_path] = new_content
            if active_path == tex_path:
                tex_content = new_content
            fix_result.page = page
            fix_result.line_number = defect.get("line_number")
            changes.append(fix_result)
            modified_files.add(str(active_path))
        else:
            include_move_applied = False
            if defect_id == "B1" and active_path != tex_path and object_name:
                moved_files, move_result = _move_included_float_input_near_reference(
                    project_root=tex_path.parent,
                    target_file=active_path,
                    float_label=str(object_name),
                    reference_text=defect.get("reference_text"),
                )
                if moved_files and move_result:
                    for moved_path, original_content, moved_content in moved_files:
                        tex_contents[moved_path] = moved_content
                        original_tex_contents.setdefault(moved_path, original_content)
                        if moved_path == tex_path:
                            tex_content = moved_content
                        modified_files.add(str(moved_path))
                    move_result.page = page
                    move_result.line_number = defect.get("line_number")
                    changes.append(move_result)
                    include_move_applied = True
            if not include_move_applied:
                unresolved.append(
                    f"{defect_id} ({object_name or '未知对象'}): 无法自动修复，可能需要人工调整"
                )

    # 写入前硬门禁：内容完整性检查（fail-closed）
    if modified_files:
        for modified_file in sorted(Path(path) for path in modified_files):
            gate_passed, gate_reason = _passes_hard_content_gate(
                original_tex_contents.get(modified_file, original_tex_content),
                tex_contents.get(modified_file, tex_content),
            )
            if not gate_passed:
                unresolved.append(f"内容完整性硬门禁拦截：{modified_file}: {gate_reason}")
                return FloatFixReport(
                    status="failed",
                    modified_files=[],
                    changes=[],
                    unresolved=unresolved,
                )

    # 写入修改后的内容
    if modified_files:
        for modified_file in sorted(Path(path) for path in modified_files):
            try:
                atomic_write_text(
                    modified_file,
                    tex_contents.get(modified_file, tex_content),
                    backup_dir=tex_path.parent / "data" / "backups",
                )
            except OSError as e:
                unresolved.append(f"无法写入文件 {modified_file}: {e}")
                return FloatFixReport(
                    status="failed",
                    modified_files=list(modified_files),
                    changes=changes,
                    unresolved=unresolved,
                )

    status = "success" if not unresolved else ("partial" if changes else "failed")

    return FloatFixReport(
        status=status,
        modified_files=list(modified_files),
        changes=changes,
        unresolved=unresolved,
    )


def _passes_hard_content_gate(
    original_tex: str,
    repaired_tex: str,
) -> Tuple[bool, str]:
    """
    Hard content integrity gate for float repairs.

    For float-only fixes, we require no academic sentence addition/deletion
    and no word-count drift. This allows pure reordering but blocks
    accidental content rewrite/deletion.
    """
    if compute_content_diff is None:
        return False, "缺少 content_integrity_check 依赖，按 fail-closed 策略阻断写入"

    normalized_original = _normalize_tex_for_integrity_gate(original_tex)
    normalized_repaired = _normalize_tex_for_integrity_gate(repaired_tex)
    normalized_diff = compute_content_diff(normalized_original, normalized_repaired)
    deleted_count = normalized_diff["sentence_changes"]["deleted_count"]
    added_count = normalized_diff["sentence_changes"]["added_count"]
    word_delta = normalized_diff["word_count"]["change"]
    if deleted_count != 0:
        return False, f"检测到删除句子 {deleted_count} 条"
    if added_count != 0:
        return False, f"检测到新增句子 {added_count} 条"
    if word_delta != 0:
        return False, f"检测到学术词数变化 {word_delta} 个"
    if structure_regression_reasons is None:
        return False, "缺少结构回退检测依赖，按 fail-closed 策略阻断写入"

    structure_reasons = structure_regression_reasons(compute_content_diff(original_tex, repaired_tex))
    if structure_reasons:
        return False, "；".join(structure_reasons)

    return True, "pass"


def _normalize_tex_for_integrity_gate(tex_content: str) -> str:
    """
    Normalize known float-only control syntax so integrity gate
    compares academic content instead of placement tokens.
    """
    normalized = re.sub(r'^[ \t]*%[^\n]*(?:\n|$)', '', tex_content, flags=re.MULTILINE)
    normalized = _canonicalize_float_blocks_for_integrity_gate(normalized)
    normalized = _normalize_table_begins_for_integrity_gate(normalized)
    normalized = re.sub(
        r'\\begin\{tabularx\}\{[^}]*\}\{[^}]*\}',
        r'\\begin{tabularx}',
        normalized,
    )
    normalized = re.sub(
        r'\\begin\{tabular\}\{[^}]*\}',
        r'\\begin{tabular}',
        normalized,
    )
    normalized = re.sub(
        r'\\begin\{tabular\*\}\{[^}]*\}\{',
        r'\\begin{tabular}{',
        normalized,
    )
    normalized = re.sub(
        r'\\resizebox\{[^}]*\}\{[^}]*\}\{(\\begin\{tabular\}.*?\\end\{tabular\})\}',
        r'\1',
        normalized,
        flags=re.DOTALL,
    )
    normalized = re.sub(
        r'\\resizebox\{[^}]*\}\{[^}]*\}\{(\\begin\{tabularx\}.*?\\end\{tabularx\})\}',
        r'\1',
        normalized,
        flags=re.DOTALL,
    )
    normalized = re.sub(
        r'\\resizebox\{[^}]*\}\{[^}]*\}\{(\\begin\{tabular\*\}.*?\\end\{tabular\*\})\}',
        r'\1',
        normalized,
        flags=re.DOTALL,
    )
    normalized = normalized.replace(r'\end{tabular*}', r'\end{tabular}')
    normalized = normalized.replace(r'@{\extracolsep{\fill}}', '')
    normalized = re.sub(
        r'\\setlength\{\\tabcolsep\}\{[^}]*\}',
        r'\\setlength{\\tabcolsep}',
        normalized,
    )
    normalized = re.sub(
        r'^\s*\\newcolumntype\{.*$',
        '',
        normalized,
        flags=re.MULTILINE,
    )
    normalized = re.sub(
        r'\\usepackage\{tabularx\}',
        '',
        normalized,
    )
    normalized = re.sub(
        r'^[ \t]*\\(?:input|include)\{[^}]+\}[ \t]*\n?',
        '',
        normalized,
        flags=re.MULTILINE,
    )
    normalized = re.sub(
        r'\\begin\{(figure\*?|table\*?)\}\[[^\]]*\]',
        r'\\begin{\1}',
        normalized,
    )
    normalized = re.sub(
        r'\\begin\{(figure\*?|table\*?)\}',
        r'\\begin{\1}',
        normalized,
    )
    normalized = re.sub(
        r'\b(Table|Figure)\s*~?\\ref\{[^}]+\}',
        r'\1 REF',
        normalized,
    )
    normalized = re.sub(
        r'\b(Table|Figure)\s+\d+\b',
        r'\1 REF',
        normalized,
    )
    normalized = re.sub(r'\\FloatBarrier\b', '', normalized)
    return normalized


def _normalize_table_begins_for_integrity_gate(tex_content: str) -> str:
    normalized = tex_content
    for env_name, group_count, replacement in (
        ("tabularx", 2, r"\begin{tabularx}"),
        ("tabular*", 2, r"\begin{tabular}"),
        ("tabular", 1, r"\begin{tabular}"),
    ):
        normalized = _replace_table_begin_groups(
            normalized,
            env_name=env_name,
            group_count=group_count,
            replacement=replacement,
        )
    return normalized


def _replace_table_begin_groups(
    tex_content: str,
    *,
    env_name: str,
    group_count: int,
    replacement: str,
) -> str:
    pattern = re.compile(r'\\begin\{' + re.escape(env_name) + r'\}')
    cursor = 0
    parts: List[str] = []
    for match in pattern.finditer(tex_content):
        group_end = match.end()
        valid = True
        for _ in range(group_count):
            while group_end < len(tex_content) and tex_content[group_end].isspace():
                group_end += 1
            span = read_braced_group(tex_content, group_end)
            if not span:
                valid = False
                break
            group_end = span[1]
        if not valid:
            continue
        parts.append(tex_content[cursor:match.start()])
        parts.append(replacement)
        cursor = group_end
    if not parts:
        return tex_content
    parts.append(tex_content[cursor:])
    return "".join(parts)


def _canonicalize_float_blocks_for_integrity_gate(tex_content: str) -> str:
    """
    Reorder float blocks into a stable tail region before diffing.

    This neutralizes pure placement changes while still preserving the
    textual payload of captions and table cells for integrity comparison.
    """
    pattern = re.compile(r'\\begin\{(figure\*?|table\*?)\}.*?\\end\{\1\}', re.DOTALL)
    blocks = []
    body_parts = []
    cursor = 0

    for index, match in enumerate(pattern.finditer(tex_content)):
        body_parts.append(tex_content[cursor:match.start()])
        body_parts.append("\n")
        block = match.group(0)
        label_match = re.search(r'\\label\{([^}]+)\}', block)
        label = label_match.group(1).strip() if label_match else ""
        stable_block = re.sub(r'\s+', ' ', block).strip()
        blocks.append(
            (
                label or f"__unlabeled_{index:04d}_{stable_block[:80]}",
                block.strip(),
            )
        )
        cursor = match.end()

    if not blocks:
        return tex_content

    body_parts.append(tex_content[cursor:])
    float_tail = "\n\n".join(block for _, block in sorted(blocks, key=lambda item: item[0]))
    body = "".join(body_parts).strip()
    return body + "\n\n" + float_tail + "\n"


# ============================================================
# CLI 入口
# ============================================================

def main():
    """命令行接口"""
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Fix Category B float defects in LaTeX documents"
    )
    parser.add_argument(
        "tex_file",
        help="Path to .tex file"
    )
    parser.add_argument(
        "--defects",
        type=str,
        help="JSON string or file path containing defect list"
    )
    parser.add_argument(
        "--template",
        type=str,
        default="single_column",
        choices=["single_column", "double_column"],
        help="Template type"
    )
    parser.add_argument(
        "--json",
        "-j",
        action="store_true",
        help="Output JSON report"
    )

    args = parser.parse_args()

    # 解析缺陷列表
    defects = []
    if args.defects:
        if Path(args.defects).exists():
            with open(args.defects, 'r', encoding='utf-8') as f:
                defects = json.load(f)
        else:
            defects = json.loads(args.defects)

    # 执行修复
    report = fix_float_defects(args.tex_file, defects, template_type=args.template)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(f"\nFloat Fix Report")
        print("=" * 50)
        print(f"Status: {report.status}")
        print(f"Modified files: {report.modified_files}")
        print(f"Changes: {len(report.changes)}")
        for change in report.changes:
            print(f"  - [{change.defect_id}] {change.object_name}: {change.action}")
        if report.unresolved:
            print(f"\nUnresolved: {len(report.unresolved)}")
            for u in report.unresolved:
                print(f"  - {u}")


if __name__ == "__main__":
    main()
