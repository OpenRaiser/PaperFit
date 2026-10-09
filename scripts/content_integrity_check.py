#!/usr/bin/env python3
"""
content_integrity_check.py — Content Integrity Validation for PaperFit VTO

This script implements the Content Integrity Protection Protocol:
- Extract academic content from LaTeX (filter out commands)
- Compute semantic hashes before/after repairs
- Detect deleted/added sentences
- Validate critical sections presence
- Generate violation reports

Usage:
    python content_integrity_check.py pre <tex_file> <line_range> --output pre_hash.json
    python content_integrity_check.py post <tex_file> <line_range> --baseline pre_hash.json --output result.json
    python content_integrity_check.py diff <original.tex> <repaired.tex> --output diff_report.json
    python content_integrity_check.py critical <tex_file> --config content_boundaries.yaml --output sections.json
"""

import argparse
import hashlib
import difflib
import json
import re
import sys
import tarfile
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Tuple, Set
from datetime import datetime

from scientific_integrity import TEXT_WRAPPERS, FLOAT_ENVS, _command, _group, _optional, _strip_layout, _envs, _literal_dimension, _macro_definitions, _shadowed_presentation_commands, active_source, minipage_layout_end, scientific_payload_diff
from layout_policy import compare_layout_policy
from layout_prose_integrity import compare_layout_prose


def _unwrap_supported_text_wrappers(text: str, *, opaque_commands: frozenset = frozenset()) -> str:
    """Keep supported wrapper text before the legacy command remover runs."""
    output = []
    i = 0
    while i < len(text):
        if text[i] != "\\":
            output.append(text[i])
            i += 1
            continue
        name, command_end = _command(text, i)
        star = command_end < len(text) and text[command_end] == "*"
        argument = command_end + int(star)
        argument = _optional(text, argument)
        supported = name in TEXT_WRAPPERS | {"shortstack"}
        if name == "parbox" and not star and name not in opaque_commands:
            header_end = minipage_layout_end(text, command_end)
            if header_end is not None:
                argument = header_end
                supported = True
        while argument < len(text) and text[argument].isspace():
            argument += 1
        if supported and argument < len(text) and text[argument] == "{":
            _, end = _group(text, argument)
            if end > argument:
                wrapper = text[i:end]
                normalized = _strip_layout(wrapper, text_wrappers=True, opaque_commands=opaque_commands)
                if normalized != wrapper:
                    # A TeX cell line break separates words in plain text. The
                    # scientific normalizer ignores whitespace, so preserve
                    # this boundary before reusing its wrapper handling.
                    parts = []
                    cursor = 0
                    while cursor < len(wrapper):
                        if wrapper[cursor] == "\\":
                            control, stop = _command(wrapper, cursor)
                            if control == "\\":
                                parts.append(" ")
                                cursor = _optional(wrapper, stop)
                                continue
                            parts.append(wrapper[cursor:stop])
                            cursor = stop
                        else:
                            parts.append(wrapper[cursor])
                            cursor += 1
                    normalized = _strip_layout("".join(parts), text_wrappers=True, opaque_commands=opaque_commands)
                output.append(normalized)
                i = end
                continue
        output.append(text[i:command_end])
        i = command_end
    return "".join(output)


def extract_academic_content(tex_content: str) -> str:
    """
    Extract pure academic text from LaTeX content by filtering out commands.

    Args:
        tex_content: Raw LaTeX content

    Returns:
        Plain text with LaTeX commands removed
    """
    text = tex_content

    # Remove comments
    text = re.sub(r'%.*$', '', text, flags=re.MULTILINE)

    # Consume only known literal minipage headers before stripping markers.
    # Their optional alignment and width must not leak into academic words.
    spans = []
    for match in re.finditer(r'\\begin\s*\{minipage\}', text):
        end = minipage_layout_end(text, match.end())
        if end is not None:
            spans.append((match.start(), end))
    for start, end in reversed(spans):
        text = text[:start] + ' ' + text[end:]

    # Remove math environments (including content) - MUST run before generic LaTeX command removal
    text = re.sub(r'\\begin\{(equation|align|align\*|gather|gather\*|multline|multline\*|displaymath)\}.*?\\end\{\1\}', ' ', text, flags=re.DOTALL)
    text = re.sub(r'\$\$.*?\$\$', ' ', text, flags=re.DOTALL)
    text = re.sub(r'\$[^$]*?\$', ' ', text)
    text = re.sub(r'\\\[.*?\\\]', ' ', text, flags=re.DOTALL)
    text = re.sub(r'\\\((?:[^()]|\\\(|\\\))*?\\\)', ' ', text, flags=re.DOTALL)

    # Remove remaining \begin{} and \end{} markers not caught by math environment pattern
    text = re.sub(r'\\begin\{[^}]+\}', ' ', text)
    text = re.sub(r'\\end\{[^}]+\}', ' ', text)

    # Known text wrappers retain their grouped words. Unsupported makecell
    # forms remain opaque under the same bounded scientific normalization.
    text = _unwrap_supported_text_wrappers(text, opaque_commands=_shadowed_presentation_commands(active_source(tex_content)))

    # Remove LaTeX commands with optional arguments
    # Handles: \command, \command[opt]{req}, \command{req}
    text = re.sub(r'\\[a-zA-Z]+(?:\[[^\]]*\])?(?:\{[^}]*\})*', ' ', text)

    # Remove special characters and extra whitespace
    text = re.sub(r'[{}\\&%#$_^~]', ' ', text)
    text = re.sub(r'\s+', ' ', text)

    return text.strip()


def extract_sentences(text: str) -> List[str]:
    """
    Split text into sentences.

    Args:
        text: Plain text content

    Returns:
        List of sentences
    """
    # Simple sentence splitting on . ! ? followed by space or end
    sentences = re.split(r'(?<=[.!?])\s+', text)
    # Filter out empty or very short sentences
    return [s.strip() for s in sentences if len(s.strip()) > 10]


def semantic_hash(tex_content: str) -> str:
    """
    Compute SHA256 hash of academic content.

    Args:
        tex_content: Raw LaTeX content

    Returns:
        Hex digest of SHA256 hash
    """
    academic = extract_academic_content(tex_content)
    # Normalize whitespace for consistent hashing
    normalized = ' '.join(academic.split())
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()


def _extract_float_blocks(tex_content: str, env_name: str) -> List[str]:
    # Share the scientific parser's explicit standard-environment inventory;
    # rotated/starred containers must retain their counts and label family.
    # Unknown environment names are not promoted by a substring match.
    families = {
        family: {name for name in FLOAT_ENVS if name.removesuffix('*').endswith(family)}
        for family in ('figure', 'table')
    }
    source = active_source(tex_content)
    return [source[env['start']:env['end']] for env in _envs(source)
            if env['name'] in families.get(env_name, {env_name})]


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


def _extract_float_labels_after_offset(tex_content: str, start_offset: Optional[int]) -> List[str]:
    if start_offset is None:
        return []

    labels: List[str] = []
    tail = active_source(tex_content[start_offset:])
    for env in _envs(tail):
        if env['name'] not in FLOAT_ENVS:
            continue
        block = tail[env['start']:env['end']]
        label_match = re.search(r'\\label\{([^}]+)\}', block)
        family = 'figure' if 'figure' in env['name'] else 'table'
        labels.append(label_match.group(1) if label_match else family)
    return sorted(set(labels))


def _extract_bibliography_structure(tex_content: str) -> Dict[str, object]:
    bibliography_start = _find_bibliography_start(tex_content)
    kind = "none"
    if re.search(r'\\begin\{thebibliography\}', tex_content):
        kind = "thebibliography"
    elif re.search(r'\\printbibliography\b', tex_content):
        kind = "printbibliography"
    elif re.search(r'\\bibliography\{', tex_content):
        kind = "bibliography_command"

    return {
        "present": bibliography_start is not None,
        "kind": kind,
        "bibitem_count": len(re.findall(r'\\bibitem\b', tex_content)),
        "float_labels_after_bibliography": _extract_float_labels_after_offset(tex_content, bibliography_start),
    }


def _extract_layout_structure(tex_content: str) -> Dict[str, object]:
    tex_content = active_source(tex_content)
    figure_blocks = _extract_float_blocks(tex_content, "figure")
    table_blocks = _extract_float_blocks(tex_content, "table")
    includegraphics = re.findall(r'\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}', tex_content)

    figure_labels: List[str] = []
    table_labels: List[str] = []
    for block in figure_blocks:
        figure_labels.extend(re.findall(r'\\label\{([^}]+)\}', block))
    for block in table_blocks:
        table_labels.extend(re.findall(r'\\label\{([^}]+)\}', block))

    return {
        "figure_count": len(figure_blocks),
        "table_count": len(table_blocks),
        "includegraphics_count": len(includegraphics),
        "figure_labels": sorted(set(figure_labels)),
        "table_labels": sorted(set(table_labels)),
        "graphics_files": sorted(set(includegraphics)),
        "bibliography": _extract_bibliography_structure(tex_content),
    }


def structure_regression_reasons(diff: Dict) -> List[str]:
    """
    Extract structural regression reasons from a computed content diff.

    This focuses on figure/table/includegraphics counts plus label/file loss.
    """
    structure = diff.get("structure_changes") or {}
    before = structure.get("before") or {}
    after = structure.get("after") or {}
    restoration = diff.get("source_restoration") or {}
    restored_asset = (restoration.get("asset") if restoration.get("checked") is True
                      and restoration.get("status") == "verified" else None)
    reasons: List[str] = []

    if before.get("figure_count") != after.get("figure_count"):
        reasons.append(
            f"Figure environment count changed: {before.get('figure_count')} -> {after.get('figure_count')}"
        )
    if before.get("table_count") != after.get("table_count"):
        reasons.append(
            f"Table environment count changed: {before.get('table_count')} -> {after.get('table_count')}"
        )
    checked_graphics_count = before.get("includegraphics_count")
    if restored_asset and isinstance(checked_graphics_count, int):
        checked_graphics_count -= 1
    if checked_graphics_count != after.get("includegraphics_count"):
        reasons.append(
            "includegraphics count changed: "
            f"{checked_graphics_count} -> {after.get('includegraphics_count')}"
        )

    removed_figure_labels = structure.get("removed_figure_labels") or []
    removed_table_labels = structure.get("removed_table_labels") or []
    removed_graphics_files = [asset for asset in structure.get("removed_graphics_files") or []
                             if asset != restored_asset]
    before_bibliography = before.get("bibliography") or {}
    after_bibliography = after.get("bibliography") or {}
    if removed_figure_labels:
        reasons.append(f"Removed figure labels: {', '.join(removed_figure_labels)}")
    if removed_table_labels:
        reasons.append(f"Removed table labels: {', '.join(removed_table_labels)}")
    if removed_graphics_files:
        reasons.append(f"Removed graphics files: {', '.join(removed_graphics_files)}")
    if before_bibliography.get("present") != after_bibliography.get("present"):
        reasons.append("Bibliography presence changed")
    if before_bibliography.get("kind") != after_bibliography.get("kind"):
        reasons.append(
            "Bibliography anchor changed: "
            f"{before_bibliography.get('kind')} -> {after_bibliography.get('kind')}"
        )
    if before_bibliography.get("bibitem_count") != after_bibliography.get("bibitem_count"):
        reasons.append(
            "Bibliography entry count changed: "
            f"{before_bibliography.get('bibitem_count')} -> {after_bibliography.get('bibitem_count')}"
        )
    before_trailing_floats = set(before_bibliography.get("float_labels_after_bibliography") or [])
    after_trailing_floats = set(after_bibliography.get("float_labels_after_bibliography") or [])
    trailing_floats = sorted(after_trailing_floats - before_trailing_floats)
    if trailing_floats:
        reasons.append(
            "New float blocks found after bibliography start: " + ", ".join(trailing_floats)
        )

    return reasons


def scientific_regression_reasons(diff: Dict) -> List[str]:
    """Independent scientific-payload drift; prose hashes are insufficient."""
    payload = diff.get("scientific_payload") or {}
    if not payload.get("checked"):
        return ["Scientific payload validation unavailable"]
    return list(payload.get("regression_reasons") or [])


def has_scientific_regression(diff: Dict) -> bool:
    return bool(scientific_regression_reasons(diff))


def has_structure_regression(diff: Dict) -> bool:
    """Return True when the diff shows any figure/table/graphics regression."""
    return bool(structure_regression_reasons(diff))


def count_words(text: str) -> int:
    """
    Count words in text.

    Args:
        text: Plain text content

    Returns:
        Word count
    """
    words = re.findall(r'\b[a-zA-Z]+\b', text)
    return len(words)


def get_line_range_content(file_path: str, line_start: int, line_end: int) -> str:
    """
    Extract content from specific line range.

    Args:
        file_path: Path to .tex file
        line_start: Starting line number (1-indexed)
        line_end: Ending line number (1-indexed)

    Returns:
        Content in the specified line range
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # Convert to 0-indexed
    start_idx = max(0, line_start - 1)
    end_idx = min(len(lines), line_end)

    return ''.join(lines[start_idx:end_idx])


def classify_content_type(tex_content: str) -> Dict:
    """
    Classify the content type of a LaTeX snippet.

    Args:
        tex_content: Raw LaTeX content

    Returns:
        Classification result with content type and safety flags
    """
    academic = extract_academic_content(tex_content)
    sentences = extract_sentences(academic)

    result = {
        'primary_type': 'C0',
        'type_name': '排版控制',
        'contains_academic_content': False,
        'safe_to_modify': True,
        'boundary_notes': '',
        'sentence_count': len(sentences),
        'word_count': count_words(academic)
    }

    # Check for different content types
    has_math = bool(re.search(r'\$.*?\$|\\\[.*?\\\]|\\begin\{equation\}|\\begin\{align\}', tex_content))
    # Match numerical data: numbers followed by keywords/units OR keywords followed by numbers
    has_numbers = bool(re.search(r'(\d+\.?\d*\s*(%|accuracy|precision|error)|(%|accuracy|precision|error)\s*\d+\.?\d*)', academic, re.IGNORECASE))
    has_full_sentences = len(sentences) >= 1

    # Classification priority: C4 > C5 > C3 > C2 > C1 > C0
    # C4 (numerical data) takes priority over C5 (full sentences) when both are present
    if has_numbers:
        result['primary_type'] = 'C4'
        result['type_name'] = '数据与结果'
        result['contains_academic_content'] = True
        result['safe_to_modify'] = False
        result['boundary_notes'] = 'Contains numerical data or results'
    elif has_full_sentences:
        result['primary_type'] = 'C5'
        result['type_name'] = '学术论述'
        result['contains_academic_content'] = True
        result['safe_to_modify'] = False
        result['boundary_notes'] = f'Contains {len(sentences)} complete sentence(s)'
    elif has_math:
        result['primary_type'] = 'C3'
        result['type_name'] = '公式与符号'
        result['contains_academic_content'] = True
        result['safe_to_modify'] = False
        result['boundary_notes'] = 'Contains mathematical expressions'
    elif re.search(r'\\cite\{|\\label\{|\\ref\{', tex_content):
        result['primary_type'] = 'C2'
        result['type_name'] = '引用键'
        result['contains_academic_content'] = False
        result['safe_to_modify'] = False
        result['boundary_notes'] = 'Contains citation or cross-reference keys'
    elif re.search(r'\\(title|author|affiliation|keywords)\{', tex_content):
        result['primary_type'] = 'C1'
        result['type_name'] = '元数据'
        result['contains_academic_content'] = True
        result['safe_to_modify'] = 'format_only'
        result['boundary_notes'] = 'Contains metadata - format can change, content cannot'

    return result


def _literal_graphic_line(line: str) -> Optional[str]:
    """One standard static invocation; macros and graphics search rules stay opaque."""
    match = re.fullmatch(r'\s*\\includegraphics\s*(?:\[([^\[\]]*)\])?\s*\{([^{}]+)\}\s*', line)
    if not match:
        return None
    asset = match.group(2)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./ -]*\.(?:pdf|png|jpg|jpeg|eps)', asset):
        return None
    for option in (match.group(1) or '').split(','):
        if not option.strip():
            continue
        if '=' not in option:
            return None
        key, value = (part.strip() for part in option.split('=', 1))
        if key in {'width', 'height', 'totalheight'} and _literal_dimension(value):
            continue
        if key == 'keepaspectratio' and value in {'true', 'false'}:
            continue
        return None
    return asset


def _inactive_figure_context(source: str, line_number: int, *, active_line: bool) -> str:
    lines = source.splitlines()
    index = line_number - 1
    if type(line_number) is not int or not 0 <= index < len(lines):
        raise ValueError('source_line_out_of_range')
    start = next((i for i in range(index - 1, max(-1, index - 61), -1)
                  if re.fullmatch(r'\s*%\s*\\begin\{figure\*?\}(?:\[[^\]]*\])?\s*', lines[i])), None)
    end = next((i for i in range(index + 1, min(len(lines), index + 61))
                if re.fullmatch(r'\s*%\s*\\end\{figure\*?\}\s*', lines[i])), None)
    if start is None or end is None:
        raise ValueError('commented_figure_context_missing')
    body = []
    for i in range(start, end + 1):
        line = lines[i].lstrip()
        if i == index and active_line:
            if _literal_graphic_line(line) is None:
                raise ValueError('graphic_invocation_not_static')
            body.append(line)
        elif line and not line.startswith('%'):
            raise ValueError('figure_context_contains_active_content')
        else:
            body.append(line[1:] if line.startswith('%') else '')
    return '\n'.join(body)


def _commented_figure_signature(context: str) -> Tuple:
    """Compare the actual commented figure/caption/label, ignoring prose comments."""
    records = []
    i = 0
    while i < len(context):
        if context[i] != '\\':
            i += 1
            continue
        name, end = _command(context, i)
        if name in {'begin', 'end', 'caption', 'label'}:
            argument, stop = _group(context, _optional(context, end))
            if stop <= end:
                raise ValueError('malformed_comment_context')
            records.append((name, re.sub(r'\s+', ' ', argument).strip()))
            i = stop
        else:
            i = end
    if ([name for name, _ in records].count('caption') != 1 or
            [name for name, _ in records].count('label') != 1 or
            records[0][0] != 'begin' or records[-1][0] != 'end' or
            records[0][1] not in {'figure', 'figure*'} or records[-1][1] != records[0][1]):
        raise ValueError('complete_comment_figure_caption_label_required')
    return tuple(records)


def verify_missing_graphic_restoration(original: str, repaired: str, evidence_path: str,
                                       *, scientific_payload: Dict, layout_prose: Dict,
                                       layout_policy: Dict) -> Dict:
    """Verify a local archived original, never a caller-supplied approval boolean.

    URL metadata records provenance, not network authenticity. Other changes
    still undergo the full independent content/policy and structure checks.
    """
    result = {'checked': False, 'status': 'manual_review', 'evidence_path': str(evidence_path),
              'limits': 'Local byte/hash consistency and exact inactive-source restoration; no network-origin authentication or rendered-quality proof.'}
    try:
        evidence_file = Path(evidence_path).resolve()
        evidence = json.loads(evidence_file.read_text(encoding='utf-8'))
        if not isinstance(evidence, dict) or evidence.get('schema') != 'paperfit_source_restoration_evidence.v1':
            raise ValueError('unsupported_evidence_schema')
        result['evidence_sha256'] = hashlib.sha256(evidence_file.read_bytes()).hexdigest()
        project = Path(evidence['project_root']).resolve(strict=True)
        if not project.is_dir():
            raise ValueError('project_root_not_directory')
        loaded = {}
        for role in ('before', 'after', 'reference'):
            path = Path(evidence[role + '_source_path']).resolve(strict=True)
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if path.suffix.lower() not in {'.tex', '.ltx'} or digest != evidence[role + '_source_sha256']:
                raise ValueError(role + '_source_hash_mismatch')
            loaded[role] = data.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')
            result[role + '_source_sha256'] = digest
            if role == 'after' and not path.is_relative_to(project):
                raise ValueError('after_source_outside_project')
            if role == 'after':
                after_directory = path.parent
        if loaded['before'] != original or loaded['after'] != repaired:
            raise ValueError('evidence_does_not_bind_current_source_pair')
        if not all(report.get('checked') is True and report.get('status') == 'pass'
                   for report in (scientific_payload, layout_prose, layout_policy)):
            raise ValueError('independent_protection_did_not_pass')
        probe_path = Path(evidence['provenance_probe_path']).resolve(strict=True)
        probe_data = probe_path.read_bytes()
        if hashlib.sha256(probe_data).hexdigest() != evidence['provenance_probe_sha256']:
            raise ValueError('provenance_record_hash_mismatch')
        probe = json.loads(probe_data)
        if not isinstance(probe, dict):
            raise ValueError('provenance_record_not_object')
        archive = Path(probe['download']).resolve(strict=True)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != probe['sha256']:
            raise ValueError('reference_archive_hash_mismatch')
        member = evidence['reference_archive_member']
        if PurePosixPath(member).is_absolute() or '..' in PurePosixPath(member).parts:
            raise ValueError('unsafe_archive_member')
        with tarfile.open(archive, 'r:*') as bundle:
            entry = bundle.getmember(member)
            if not entry.isfile() or entry.size > 5_000_000:
                raise ValueError('reference_member_not_bounded_regular_source')
            reference_bytes = bundle.extractfile(entry).read()
            if hashlib.sha256(reference_bytes).hexdigest() != evidence['reference_source_sha256']:
                raise ValueError('reference_source_not_archive_member_bytes')
            archive_names = [entry.name for entry in bundle.getmembers()]
        before_line, after_line, reference_line = (evidence[role + '_line'] for role in ('before', 'after', 'reference'))
        contexts = [_inactive_figure_context(loaded[role], number, active_line=role == 'before')
                    for role, number in zip(('before', 'after', 'reference'), (before_line, after_line, reference_line))]
        signatures = [_commented_figure_signature(context) for context in contexts]
        if signatures[0] != signatures[1] or signatures[1] != signatures[2]:
            raise ValueError('original_comment_context_mismatch')
        lines = {role: loaded[role].splitlines()[number - 1]
                 for role, number in zip(('before', 'after', 'reference'), (before_line, after_line, reference_line))}
        if lines['after'] != lines['reference'] or not lines['reference'].lstrip().startswith('%'):
            raise ValueError('after_line_not_exact_original_comment')
        # Bind the active invocation to this changed line, rather than finding
        # an identical inactive block elsewhere after deleting the original.
        changes = difflib.SequenceMatcher(None, original.splitlines(), repaired.splitlines(), autojunk=False).get_opcodes()
        if not any(tag == 'replace' and old_start <= before_line - 1 < old_end
                   and new_start <= after_line - 1 < new_end
                   for tag, old_start, old_end, new_start, new_end in changes):
            raise ValueError('restoration_line_not_matched_change')
        asset = _literal_graphic_line(lines['before'])
        if asset is None or _literal_graphic_line(lines['reference'].lstrip()[1:]) != asset:
            raise ValueError('static_asset_reference_mismatch')
        if any(name == asset or name.endswith('/' + asset) for name in archive_names):
            raise ValueError('asset_present_in_reference_archive')
        asset_path = (after_directory / asset).resolve()
        if not asset_path.is_relative_to(project) or asset_path.exists() or asset_path.is_symlink():
            raise ValueError('asset_not_confirmed_absent_inside_project')
        if re.search(r'\\(?:graphicspath|DeclareGraphicsExtensions)\b', active_source(original) + active_source(repaired)):
            raise ValueError('graphics_search_configuration_requires_review')
        for record in _macro_definitions(active_source(original)):
            target = record[1] if record[0] in {'def', 'gdef', 'edef', 'xdef', 'let'} else record[2]
            if target.lstrip('\\') == 'includegraphics':
                raise ValueError('includegraphics_redefinition_requires_review')
        if probe.get('schema') != 'paperfit_asset_recovery_probe.v1' or probe.get('asset') != asset:
            raise ValueError('provenance_record_asset_mismatch')
        if not any(isinstance(row, dict) and row.get('line') == reference_line and row.get('text') == lines['reference']
                   for row in probe.get('main_tex_asset_reference', [])):
            raise ValueError('provenance_reference_line_mismatch')
        result.update(checked=True, status='verified', asset=asset,
                      before_line=before_line, after_line=after_line, reference_line=reference_line,
                      provenance={'requested_url': probe.get('requested_url'), 'final_url': probe.get('final_url'),
                                  'archive_sha256': probe['sha256'], 'probe_sha256': evidence['provenance_probe_sha256']})
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, IndexError, AttributeError, tarfile.TarError) as exc:
        result['reason'] = str(exc) if isinstance(exc, ValueError) else 'restoration_evidence_unavailable:' + type(exc).__name__
    return result


def compute_content_diff(original_tex: str, repaired_tex: str, *, layout_policy_approvals=(), file: str = "", layout_only: bool = False, source_restoration_evidence_path: Optional[str] = None) -> Dict:
    """
    Compute the difference between original and repaired content.

    Args:
        original_tex: Original LaTeX content
        repaired_tex: Repaired LaTeX content

    Returns:
        Diff report with deleted/added content
    """
    original_academic = extract_academic_content(original_tex)
    repaired_academic = extract_academic_content(repaired_tex)

    original_sentences = set(extract_sentences(original_academic))
    repaired_sentences = set(extract_sentences(repaired_academic))

    deleted_sentences = original_sentences - repaired_sentences
    added_sentences = repaired_sentences - original_sentences

    original_word_count = count_words(original_academic)
    repaired_word_count = count_words(repaired_academic)
    original_layout = _extract_layout_structure(original_tex)
    repaired_layout = _extract_layout_structure(repaired_tex)

    word_count_change = repaired_word_count - original_word_count
    word_count_change_pct = (word_count_change / original_word_count * 100) if original_word_count > 0 else 0

    pre_hash = semantic_hash(original_tex)
    post_hash = semantic_hash(repaired_tex)

    scientific_payload = scientific_payload_diff(original_tex, repaired_tex)
    layout_policy = compare_layout_policy(original_tex, repaired_tex, approvals=layout_policy_approvals, file=file)
    layout_prose = compare_layout_prose(original_tex, repaired_tex)
    source_restoration = (verify_missing_graphic_restoration(original_tex, repaired_tex, source_restoration_evidence_path,
                          scientific_payload=scientific_payload, layout_prose=layout_prose, layout_policy=layout_policy)
                          if source_restoration_evidence_path else {'checked': False, 'status': 'not_used'})

    # Determine violation level and required action
    violation_level = 0
    violation_reasons = []
    action_required = 'none'
    removed_figure_labels = sorted(set(original_layout["figure_labels"]) - set(repaired_layout["figure_labels"]))
    removed_table_labels = sorted(set(original_layout["table_labels"]) - set(repaired_layout["table_labels"]))
    removed_graphics_files = sorted(set(original_layout["graphics_files"]) - set(repaired_layout["graphics_files"]))
    structure_changes = {
        'before': original_layout,
        'after': repaired_layout,
        'removed_figure_labels': removed_figure_labels,
        'removed_table_labels': removed_table_labels,
        'removed_graphics_files': removed_graphics_files,
    }
    structure_reasons = structure_regression_reasons({"structure_changes": structure_changes, "source_restoration": source_restoration})
    layout_structure_changed = bool(structure_reasons)

    if scientific_payload["regression_reasons"]:
        violation_level = 3
        action_required = "auto_rollback"
        violation_reasons.extend(scientific_payload["regression_reasons"])
    if layout_structure_changed:
        violation_level = 3
        action_required = 'auto_rollback'
        violation_reasons.extend(structure_reasons)
    elif pre_hash != post_hash and not scientific_payload["regression_reasons"]:
        if deleted_sentences:
            violation_reasons.append(f"Deleted {len(deleted_sentences)} sentence(s)")
        if abs(word_count_change_pct) > 15:
            violation_level = 3
            violation_reasons.append(f"Word count changed by {word_count_change_pct:.1f}% (>15%)")
            action_required = 'auto_rollback'
        elif abs(word_count_change_pct) > 5:
            violation_level = 2
            violation_reasons.append(f"Word count changed by {word_count_change_pct:.1f}% (5-15%)")
            action_required = 'manual_review'
        else:
            violation_level = 1
            violation_reasons.append(f"Word count changed by {word_count_change_pct:.1f}% (<5%)")
            action_required = 'log_only'

    # A layout shortcut can preserve all scientific content and still violate
    # repair policy. Keep its evidence separate from content violation levels.
    if layout_policy["status"] == "blocked" or (layout_only and layout_prose.get("checked") is True and layout_prose["status"] != "pass"):
        action_required = "auto_rollback"
    elif (layout_policy["status"] == "manual_review" or (layout_only and layout_prose.get("checked") is not True)) and action_required != "auto_rollback":
        action_required = "manual_review"
    if source_restoration_evidence_path and source_restoration['status'] != 'verified' and action_required != 'auto_rollback':
        action_required = 'manual_review'

    return {
        'timestamp': datetime.now().isoformat(),
        'scientific_payload': scientific_payload,
        'layout_policy': layout_policy,
        'layout_prose': layout_prose,
        'source_restoration': source_restoration,
        'hash_comparison': {
            'pre_hash': pre_hash,
            'post_hash': post_hash,
            'identical': pre_hash == post_hash
        },
        'word_count': {
            'original': original_word_count,
            'repaired': repaired_word_count,
            'change': word_count_change,
            'change_percentage': round(word_count_change_pct, 2)
        },
        'sentence_changes': {
            'deleted_count': len(deleted_sentences),
            'deleted_sentences': list(deleted_sentences),
            'added_count': len(added_sentences),
            'added_sentences': list(added_sentences)
        },
        'structure_changes': {
            'before': original_layout,
            'after': repaired_layout,
            'removed_figure_labels': removed_figure_labels,
            'removed_table_labels': removed_table_labels,
            'removed_graphics_files': removed_graphics_files,
        },
        'violation': {
            'level': violation_level,
            'reasons': violation_reasons,
            'requires_action': violation_level > 0
        },
        'action_required': action_required
    }


def check_critical_sections(tex_file: str, config_path: str) -> Dict:
    """
    Check presence and completeness of critical sections.

    Args:
        tex_file: Path to LaTeX file
        config_path: Path to content_boundaries.yaml

    Returns:
        Section check results
    """
    try:
        import yaml
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
    except ImportError:
        # Fallback if PyYAML not installed
        config = {
            'critical_sections': [
                {
                    'label': 'Abstract',
                    'start_pattern': '\\begin{abstract}',
                    'end_pattern': '\\end{abstract}',
                    'min_sentences': 3,
                    'min_words': 100
                },
                {
                    'label': 'Conclusions',
                    'start_pattern': '\\section{Conclusion}',
                    'end_pattern': None,  # Until next section or end
                    'min_sentences': 2,
                    'min_words': 50
                }
            ]
        }
    except FileNotFoundError:
        return {
            'status': 'error',
            'message': f'Config file not found: {config_path}'
        }

    with open(tex_file, 'r', encoding='utf-8') as f:
        content = f.read()

    results = {
        'timestamp': datetime.now().isoformat(),
        'file': tex_file,
        'sections': []
    }

    for section_config in config.get('critical_sections', []):
        label = section_config['label']
        start_pattern = section_config['start_pattern']
        end_pattern = section_config.get('end_pattern')
        min_sentences = section_config.get('min_sentences', 1)
        min_words = section_config.get('min_words', 20)

        # Find section start
        start_match = re.search(re.escape(start_pattern), content)
        if not start_match:
            results['sections'].append({
                'label': label,
                'status': 'not_found',
                'message': f'Pattern "{start_pattern}" not found'
            })
            continue

        start_idx = start_match.end()

        # Find section end
        if end_pattern:
            end_match = re.search(re.escape(end_pattern), content[start_idx:])
            if end_match:
                end_idx = start_idx + end_match.start()
            else:
                end_idx = len(content)
        else:
            # Until next \section or end of document
            next_section = re.search(r'\\section\{', content[start_idx:])
            if next_section:
                end_idx = start_idx + next_section.start()
            else:
                end_match = re.search(r'\\end\{document\}', content[start_idx:])
                if end_match:
                    end_idx = start_idx + end_match.start()
                else:
                    end_idx = len(content)

        section_content = content[start_idx:end_idx]
        academic_content = extract_academic_content(section_content)
        sentences = extract_sentences(academic_content)
        word_count = count_words(academic_content)

        status = 'pass'
        issues = []

        if len(sentences) < min_sentences:
            status = 'fail'
            issues.append(f'Only {len(sentences)} sentences (minimum: {min_sentences})')

        if word_count < min_words:
            status = 'fail'
            issues.append(f'Only {word_count} words (minimum: {min_words})')

        results['sections'].append({
            'label': label,
            'status': status,
            'start_position': start_idx,
            'end_position': end_idx,
            'content_length': end_idx - start_idx,
            'sentence_count': len(sentences),
            'word_count': word_count,
            'min_sentences_required': min_sentences,
            'min_words_required': min_words,
            'issues': issues
        })

    # Overall status
    all_pass = all(s['status'] == 'pass' for s in results['sections'])
    results['overall_status'] = 'pass' if all_pass else 'fail'

    return results


def cmd_pre(args):
    """Handle 'pre' command - compute pre-repair snapshot"""
    file_path = args.tex_file
    line_range = args.line_range.split(',')
    line_start = int(line_range[0])
    line_end = int(line_range[1]) if len(line_range) > 1 else line_start + 50

    content = get_line_range_content(file_path, line_start, line_end)
    classification = classify_content_type(content)
    academic = extract_academic_content(content)

    result = {
        'timestamp': datetime.now().isoformat(),
        'file': file_path,
        'line_range': [line_start, line_end],
        'classification': classification,
        'semantic_hash': semantic_hash(content),
        'academic_word_count': count_words(academic),
        'sentence_count': classification['sentence_count'],
        'content': content[:500],  # First 500 chars for human reference only
        # Full baseline content: 'post' must diff against the complete range,
        # never against the truncated reference field.
        'full_content': content,
        'content_length': len(content)
    }

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"Pre-repair snapshot saved to {args.output}")
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))

    return result


def cmd_post(args):
    """Handle 'post' command - compare post-repair with baseline"""
    file_path = args.tex_file
    line_range = args.line_range.split(',')
    line_start = int(line_range[0])
    line_end = int(line_range[1]) if len(line_range) > 1 else line_start + 50

    with open(args.baseline, 'r', encoding='utf-8') as f:
        baseline = json.load(f)

    full_content = baseline.get('full_content')
    if full_content is None:
        full_content = baseline.get('content', '')
    # A legacy baseline only carries the truncated 500-char reference field.
    # Word-drift levels computed against it are unreliable, so such
    # comparisons must never trigger auto_rollback on their own.
    baseline_truncated = 'full_content' not in baseline and len(full_content) >= 500
    original_content = full_content
    repaired_content = get_line_range_content(file_path, line_start, line_end)

    diff_result = compute_content_diff(original_content, repaired_content, layout_only=True)
    diff_result['file'] = file_path
    diff_result['line_range'] = [line_start, line_end]
    diff_result['baseline_file'] = args.baseline
    diff_result['baseline_truncated'] = baseline_truncated

    # Determine action required
    violation_level = diff_result['violation']['level']
    if (diff_result.get('layout_policy') or {}).get('status') == 'blocked':
        diff_result['action_required'] = 'auto_rollback'
    elif baseline_truncated:
        diff_result['action_required'] = 'manual_review'
        diff_result['action_note'] = (
            'baseline lacks full_content; word-drift levels are computed against a '
            'truncated reference and cannot support auto_rollback'
        )
    elif violation_level >= 3 or ((diff_result.get('layout_prose') or {}).get('checked') is True and (diff_result.get('layout_prose') or {}).get('status') != 'pass'):
        diff_result['action_required'] = 'auto_rollback'
    elif violation_level >= 2 or (diff_result.get('layout_policy') or {}).get('status') == 'manual_review' or (diff_result.get('layout_prose') or {}).get('checked') is not True:
        diff_result['action_required'] = 'manual_review'
    elif violation_level >= 1:
        diff_result['action_required'] = 'log_only'
    else:
        diff_result['action_required'] = 'none'

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(diff_result, f, indent=2, ensure_ascii=False)
        print(f"Post-repair validation saved to {args.output}")
    else:
        print(json.dumps(diff_result, indent=2, ensure_ascii=False))

    return diff_result


def cmd_diff(args):
    """Handle 'diff' command - compare two files"""
    with open(args.original, 'r', encoding='utf-8') as f:
        original = f.read()

    with open(args.repaired, 'r', encoding='utf-8') as f:
        repaired = f.read()

    result = compute_content_diff(original, repaired, layout_only=True,
                                  source_restoration_evidence_path=getattr(args, 'source_restoration_evidence', None))
    result['original_file'] = args.original
    result['repaired_file'] = args.repaired

    if args.output:
        with open(args.output, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"Content diff report saved to {args.output}")
    else:
        print(json.dumps(result, indent=2))

    return result


def cmd_critical(args):
    """Handle 'critical' command - check critical sections"""
    result = check_critical_sections(args.tex_file, args.config)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"Critical sections check saved to {args.output}")
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))

    return result


def main():
    parser = argparse.ArgumentParser(
        description='Content Integrity Validation for PaperFit VTO'
    )
    subparsers = parser.add_subparsers(dest='command', help='Command type')

    # 'pre' command
    pre_parser = subparsers.add_parser('pre', help='Compute pre-repair snapshot')
    pre_parser.add_argument('tex_file', help='Path to .tex file')
    pre_parser.add_argument('line_range', help='Line range (e.g., "100,150" or "100")')
    pre_parser.add_argument('--output', '-o', help='Output JSON file')
    pre_parser.set_defaults(func=cmd_pre)

    # 'post' command
    post_parser = subparsers.add_parser('post', help='Post-repair validation')
    post_parser.add_argument('tex_file', help='Path to repaired .tex file')
    post_parser.add_argument('line_range', help='Line range (e.g., "100,150" or "100")')
    post_parser.add_argument('--baseline', '-b', required=True, help='Baseline JSON file')
    post_parser.add_argument('--output', '-o', help='Output JSON file')
    post_parser.set_defaults(func=cmd_post)

    # 'diff' command
    diff_parser = subparsers.add_parser('diff', help='Compare two files')
    diff_parser.add_argument('original', help='Original .tex file')
    diff_parser.add_argument('repaired', help='Repaired .tex file')
    diff_parser.add_argument('--output', '-o', help='Output JSON file')
    diff_parser.add_argument('--source-restoration-evidence', help='Hash-bound local original-source/archive evidence for one inactive missing graphic restoration')
    diff_parser.set_defaults(func=cmd_diff)

    # 'critical' command
    critical_parser = subparsers.add_parser('critical', help='Check critical sections')
    critical_parser.add_argument('tex_file', help='Path to .tex file')
    critical_parser.add_argument('--config', '-c', default='config/content_boundaries.yaml', help='Config file')
    critical_parser.add_argument('--output', '-o', help='Output JSON file')
    critical_parser.set_defaults(func=cmd_critical)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    result = args.func(args)
    if args.command in {'diff', 'post'}:
        action = result.get('action_required') or (result.get('violation') or {}).get('action')
        return 3 if action == 'auto_rollback' else 2 if action == 'manual_review' else 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
