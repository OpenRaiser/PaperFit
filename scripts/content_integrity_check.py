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
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set
from datetime import datetime


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

    # Remove math environments (including content) - MUST run before generic LaTeX command removal
    text = re.sub(r'\\begin\{(equation|align|align\*|gather|gather\*|multline|multline\*|displaymath)\}.*?\\end\{\1\}', ' ', text, flags=re.DOTALL)
    text = re.sub(r'\$\$.*?\$\$', ' ', text, flags=re.DOTALL)
    text = re.sub(r'\$[^$]*?\$', ' ', text)
    text = re.sub(r'\\\[.*?\\\]', ' ', text, flags=re.DOTALL)
    text = re.sub(r'\\\((?:[^()]|\\\(|\\\))*?\\\)', ' ', text, flags=re.DOTALL)

    # Remove remaining \begin{} and \end{} markers not caught by math environment pattern
    text = re.sub(r'\\begin\{[^}]+\}', ' ', text)
    text = re.sub(r'\\end\{[^}]+\}', ' ', text)

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
    pattern = rf'\\begin\{{{env_name}\*?\}}.*?\\end\{{{env_name}\*?\}}'
    return re.findall(pattern, tex_content, flags=re.DOTALL)


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
    tail = tex_content[start_offset:]
    pattern = re.compile(r'\\begin\{(figure\*?|table\*?)\}(?:\[[^\]]*\])?')
    for match in pattern.finditer(tail):
        env_name = match.group(1)
        end_match = re.search(
            r'\\end\{' + re.escape(env_name) + r'\}',
            tail[match.end():],
            re.DOTALL,
        )
        if not end_match:
            continue
        block_end = match.end() + end_match.end()
        block = tail[match.start():block_end]
        label_match = re.search(r'\\label\{([^}]+)\}', block)
        labels.append(label_match.group(1) if label_match else env_name)
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
    reasons: List[str] = []

    if before.get("figure_count") != after.get("figure_count"):
        reasons.append(
            f"Figure environment count changed: {before.get('figure_count')} -> {after.get('figure_count')}"
        )
    if before.get("table_count") != after.get("table_count"):
        reasons.append(
            f"Table environment count changed: {before.get('table_count')} -> {after.get('table_count')}"
        )
    if before.get("includegraphics_count") != after.get("includegraphics_count"):
        reasons.append(
            "includegraphics count changed: "
            f"{before.get('includegraphics_count')} -> {after.get('includegraphics_count')}"
        )

    removed_figure_labels = structure.get("removed_figure_labels") or []
    removed_table_labels = structure.get("removed_table_labels") or []
    removed_graphics_files = structure.get("removed_graphics_files") or []
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


def compute_content_diff(original_tex: str, repaired_tex: str) -> Dict:
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
    structure_reasons = structure_regression_reasons({"structure_changes": structure_changes})
    layout_structure_changed = bool(structure_reasons)

    if layout_structure_changed:
        violation_level = 3
        action_required = 'auto_rollback'
        violation_reasons.extend(structure_reasons)
    elif pre_hash != post_hash:
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

    return {
        'timestamp': datetime.now().isoformat(),
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
        'content': content[:500]  # First 500 chars for reference
    }

    if args.output:
        with open(args.output, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"Pre-repair snapshot saved to {args.output}")
    else:
        print(json.dumps(result, indent=2))

    return result


def cmd_post(args):
    """Handle 'post' command - compare post-repair with baseline"""
    file_path = args.tex_file
    line_range = args.line_range.split(',')
    line_start = int(line_range[0])
    line_end = int(line_range[1]) if len(line_range) > 1 else line_start + 50

    with open(args.baseline, 'r') as f:
        baseline = json.load(f)

    original_content = baseline.get('content', '')
    repaired_content = get_line_range_content(file_path, line_start, line_end)

    diff_result = compute_content_diff(original_content, repaired_content)
    diff_result['file'] = file_path
    diff_result['line_range'] = [line_start, line_end]
    diff_result['baseline_file'] = args.baseline

    # Determine action required
    violation_level = diff_result['violation']['level']
    if violation_level >= 3:
        diff_result['action_required'] = 'auto_rollback'
    elif violation_level >= 2:
        diff_result['action_required'] = 'manual_review'
    elif violation_level >= 1:
        diff_result['action_required'] = 'log_only'
    else:
        diff_result['action_required'] = 'none'

    if args.output:
        with open(args.output, 'w') as f:
            json.dump(diff_result, f, indent=2)
        print(f"Post-repair validation saved to {args.output}")
    else:
        print(json.dumps(diff_result, indent=2))

    return diff_result


def cmd_diff(args):
    """Handle 'diff' command - compare two files"""
    with open(args.original, 'r', encoding='utf-8') as f:
        original = f.read()

    with open(args.repaired, 'r', encoding='utf-8') as f:
        repaired = f.read()

    result = compute_content_diff(original, repaired)
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
        with open(args.output, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"Critical sections check saved to {args.output}")
    else:
        print(json.dumps(result, indent=2))

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

    args.func(args)


if __name__ == '__main__':
    main()
