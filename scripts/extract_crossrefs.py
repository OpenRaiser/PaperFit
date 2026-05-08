#!/usr/bin/env python3
"""
Extract cross-reference relationships from LaTeX .tex files.

This script parses .tex source files to:
1. Extract all \ref{fig:*}/\ref{tab:*} occurrences with their line numbers
2. Extract all figure/table environments with their labels
3. Compute "source order distance" between first reference and float definition
4. Output a JSON report listing floats with significant reference-definition distances

Usage:
    paperfit run scripts/extract_crossrefs.py main.tex
    paperfit run scripts/extract_crossrefs.py main.tex --output data/crossrefs.json

This complements visual detection by providing early detection at source level.
"""

import argparse
import json
import re
import sys
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Tuple


@dataclass
class Reference:
    """A \ref{fig:*} occurrence in the source."""
    line_number: int
    label: str
    source_type: str  # latex_ref | plain_text_number
    context: str  # Surrounding text for debugging
    char_offset: int  # Character offset in file
    matched_text: Optional[str] = None


@dataclass
class FloatDefinition:
    """A float environment with its label."""
    line_number: int
    label: str
    float_type: str  # figure | table
    float_position: str  # e.g., [ht], [htbp]
    width_spec: str  # e.g., 0.98\linewidth
    image_file: str  # extracted from \includegraphics
    char_offset: int
    section: Optional[str] = None  # Which section this figure appears in
    table_env: Optional[str] = None
    tabcolsep: Optional[str] = None


@dataclass
class CrossRefDistance:
    """Distance between first reference and float definition."""
    label: str
    ref_line: int
    float_line: int
    figure_line: int  # legacy alias for compatibility
    float_type: str
    line_distance: int  # Absolute line difference
    ref_before_figure: bool  # legacy alias for compatibility
    ref_before_float: bool
    section_distance: int  # Number of sections between them
    severity: str  # "none", "minor", "major"
    reference_source: str
    reference_text: Optional[str] = None


@dataclass
class CrossRefReport:
    """Complete cross-reference analysis report."""
    source_file: str
    total_refs: int
    total_figures: int
    total_tables: int
    total_floats: int
    figures_with_labels: int
    orphan_refs: List[str]  # \ref without corresponding \label
    orphan_labels: List[str]  # \label without any \ref
    distances: List[CrossRefDistance]
    major_issues: List[str]  # Labels with major severity


def extract_section_structure(tex_content: str) -> List[Tuple[int, str]]:
    """
    Extract section structure from .tex content.
    Returns list of (line_number, section_name) tuples.
    """
    sections = []
    section_pattern = re.compile(
        r'\\(section|subsection|subsubsection)\*?\{([^}]+)\}',
        re.MULTILINE
    )

    for match in section_pattern.finditer(tex_content):
        line_no = tex_content[:match.start()].count('\n') + 1
        sections.append((line_no, match.group(2).strip()))

    return sections


def get_section_at_line(sections: List[Tuple[int, str]], line_no: int) -> Optional[str]:
    """Get the section name that contains the given line number."""
    current_section = None
    for sec_line, sec_name in sections:
        if line_no >= sec_line:
            current_section = sec_name
        else:
            break
    return current_section


def compute_section_distance(
    sections: List[Tuple[int, str]],
    line1: int,
    line2: int
) -> int:
    """
    Compute number of section boundaries between two lines.
    """
    sec1 = get_section_at_line(sections, line1)
    sec2 = get_section_at_line(sections, line2)

    if sec1 == sec2:
        return 0

    # Count section boundaries between them
    count = 0
    for sec_line, _ in sections:
        if min(line1, line2) < sec_line <= max(line1, line2):
            count += 1
    return count


def _position_inside_latex_environment(tex_content: str, position: int, env_names: set[str]) -> bool:
    stack: List[str] = []
    pattern = re.compile(r'\\(begin|end)\{([^}]+)\}')
    for match in pattern.finditer(tex_content[:position]):
        if is_commented_at(tex_content, match.start()):
            continue
        env_name = match.group(2)
        if env_name not in env_names:
            continue
        if match.group(1) == "begin":
            stack.append(env_name)
        elif env_name in stack:
            for idx in range(len(stack) - 1, -1, -1):
                if stack[idx] == env_name:
                    del stack[idx]
                    break
    return bool(stack)


def _find_paragraph_start(tex_content: str, position: int) -> int:
    paragraph_break = tex_content.rfind("\n\n", 0, position)
    command_breaks = [
        match.start() + 1
        for match in re.finditer(r'\n\\(?:begin|end|section|subsection|subsubsection)\b', tex_content[:position])
    ]
    candidates = [0]
    if paragraph_break != -1:
        candidates.append(paragraph_break + 2)
    candidates.extend(command_breaks)
    return max(candidates)


def _is_index_like_reference(tex_content: str, ref: Reference) -> bool:
    if _position_inside_latex_environment(
        tex_content,
        ref.char_offset,
        {"itemize", "enumerate", "description"},
    ):
        return True
    para_start = _find_paragraph_start(tex_content, ref.char_offset)
    prefix = tex_content[para_start:ref.char_offset]
    return bool(re.search(r'(^|\n)\s*\\item\b', prefix))


def choose_semantic_reference(tex_content: str, label_refs: List[Reference]) -> Reference:
    ordered_refs = sorted(label_refs, key=lambda r: (r.line_number, r.char_offset))
    for ref in ordered_refs:
        if not _is_index_like_reference(tex_content, ref):
            return ref
    return ordered_refs[0]


def build_reference(
    tex_content: str,
    label: str,
    char_offset: int,
    source_type: str,
    matched_text: Optional[str] = None,
) -> Reference:
    """Build a reference object with derived line number and context."""
    line_no = tex_content[:char_offset].count('\n') + 1
    start_ctx = max(0, char_offset - 50)
    end_ctx = min(len(tex_content), char_offset + 80)
    context = tex_content[start_ctx:end_ctx].replace('\n', ' ').strip()
    return Reference(
        line_number=line_no,
        label=label,
        source_type=source_type,
        context=context,
        char_offset=char_offset,
        matched_text=matched_text,
    )


def is_commented_at(text: str, offset: int) -> bool:
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


def extract_aux_numbered_labels(aux_content: str) -> Dict[Tuple[str, int], str]:
    r"""
    Extract stable numeric label mappings from .aux content when available.

    Only mappings with explicit numeric counters are accepted. This works
    reliably for labels written after \caption; labels written before \caption
    often expand to \caption@xref placeholders and are ignored here.
    """
    numbered_labels: Dict[Tuple[str, int], str] = {}
    pattern = re.compile(r'\\newlabel\{((?:fig|tab):[^}]+)\}\{\{([^}]*)\}')

    for match in pattern.finditer(aux_content):
        label = match.group(1)
        raw_number = match.group(2).strip()
        if not raw_number.isdigit():
            continue
        float_type = 'figure' if label.startswith('fig:') else 'table'
        numbered_labels[(float_type, int(raw_number))] = label

    return numbered_labels


def build_numbered_label_map(
    floats: List[FloatDefinition],
    aux_content: Optional[str] = None,
) -> Dict[Tuple[str, int], str]:
    r"""
    Build a numeric reference map such as ("table", 4) -> "tab:aff_opt".

    Source-order numbering is the fallback because it matches LaTeX counter
    order for most papers, including cases where `.aux` cannot recover a table
    number due to `\label` preceding `\caption`.
    """
    numbered_labels: Dict[Tuple[str, int], str] = {}
    counters = {'figure': 0, 'table': 0}

    for flt in sorted(floats, key=lambda item: item.line_number):
        counters[flt.float_type] += 1
        numbered_labels[(flt.float_type, counters[flt.float_type])] = flt.label

    if aux_content:
        numbered_labels.update(extract_aux_numbered_labels(aux_content))

    return numbered_labels


def extract_refs(
    tex_content: str,
    numbered_label_map: Optional[Dict[Tuple[str, int], str]] = None,
) -> List[Reference]:
    """
    Extract all \ref{fig:*}/\ref{tab:*} occurrences and, when a number map is
    provided, plain-text references like "Table 4" or "Figure 3".
    """
    refs = []
    ref_pattern = re.compile(r'\\ref\{([^}]+)\}')

    for match in ref_pattern.finditer(tex_content):
        if is_commented_at(tex_content, match.start()):
            continue
        label = match.group(1)
        if label.startswith(('fig:', 'tab:')):
            refs.append(build_reference(
                tex_content=tex_content,
                label=label,
                char_offset=match.start(),
                source_type='latex_ref',
                matched_text=match.group(0),
            ))

    if numbered_label_map:
        plain_text_pattern = re.compile(r'\b(Figure|Fig\.|Table)\s*~?\s*(\d+)\b')
        for match in plain_text_pattern.finditer(tex_content):
            if is_commented_at(tex_content, match.start()):
                continue
            kind = match.group(1)
            number = int(match.group(2))
            float_type = 'table' if kind == 'Table' else 'figure'
            label = numbered_label_map.get((float_type, number))
            if not label:
                continue
            refs.append(build_reference(
                tex_content=tex_content,
                label=label,
                char_offset=match.start(),
                source_type='plain_text_number',
                matched_text=match.group(0),
            ))

    refs.sort(key=lambda ref: (ref.line_number, ref.char_offset, ref.label))
    return refs


def extract_float_definitions(
    tex_content: str,
    sections: Optional[List[Tuple[int, str]]] = None,
) -> List[FloatDefinition]:
    """
    Extract all figure/table environments with their labels.
    """
    floats = []

    float_start_pattern = re.compile(r'\\begin\{(figure\*?|table\*?)\}(\[([^\]]*)\])?')

    for match in float_start_pattern.finditer(tex_content):
        if is_commented_at(tex_content, match.start()):
            continue
        line_no = tex_content[:match.start()].count('\n') + 1
        char_offset = match.start()
        env_name = match.group(1)
        float_pos = match.group(3) if match.group(3) else 'default'
        base_type = 'figure' if env_name.startswith('figure') else 'table'

        float_end_match = re.search(
            r'\\end\{' + re.escape(env_name) + r'\}',
            tex_content[match.start():],
            re.DOTALL
        )

        if not float_end_match:
            continue

        float_content = tex_content[match.start():match.start() + float_end_match.end()]

        label_pattern = re.compile(r'\\label\{([^}]+)\}')
        label_match = None
        for candidate_label in label_pattern.finditer(float_content):
            if is_commented_at(tex_content, match.start() + candidate_label.start()):
                continue
            label_match = candidate_label
            break

        if label_match:
            label = label_match.group(1)
        else:
            continue
        if not label.startswith(('fig:', 'tab:')):
            continue

        width_match = re.search(r'width=([^\],]+)', float_content)
        width_spec = width_match.group(1) if width_match else 'none'
        table_env = None
        tabcolsep = None

        if base_type == 'table':
            tabularx_match = re.search(r'\\begin\{tabularx\}\{([^}]+)\}', float_content)
            if tabularx_match:
                width_spec = tabularx_match.group(1).strip()
                table_env = 'tabularx'
            else:
                resizebox_match = re.search(r'\\resizebox\{([^}]+)\}\{!\}\{\\begin\{tabular', float_content)
                if resizebox_match:
                    width_spec = resizebox_match.group(1).strip()
                    table_env = 'resizebox-tabular'
                elif re.search(r'\\begin\{tabular\}', float_content):
                    table_env = 'tabular'

            tabcolsep_match = re.search(r'\\setlength\{\\tabcolsep\}\{([^}]+)\}', float_content)
            if tabcolsep_match:
                tabcolsep = tabcolsep_match.group(1).strip()

        img_pattern = re.compile(r'\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}')
        img_match = img_pattern.search(float_content)
        image_file = img_match.group(1) if img_match else 'unknown'

        floats.append(FloatDefinition(
            line_number=line_no,
            label=label,
            float_type=base_type,
            float_position=float_pos,
            width_spec=width_spec,
            image_file=image_file,
            char_offset=char_offset,
            section=get_section_at_line(sections, line_no) if sections else None,
            table_env=table_env,
            tabcolsep=tabcolsep,
        ))

    return floats


def analyze_cross_references(
    refs: List[Reference],
    floats: List[FloatDefinition],
    sections: List[Tuple[int, str]],
    tex_content: str = "",
) -> Tuple[List[CrossRefDistance], List[str], List[str]]:
    """
    Analyze cross-reference distances.
    Returns (distances, orphan_refs, orphan_labels).
    """
    distances = []
    orphan_refs = []

    float_map = {flt.label: flt for flt in floats}

    # Track which figures have been referenced
    referenced_labels = set()

    # Group refs by label and find first occurrence
    ref_by_label: Dict[str, List[Reference]] = {}
    for ref in refs:
        if ref.label not in ref_by_label:
            ref_by_label[ref.label] = []
        ref_by_label[ref.label].append(ref)

    for label, label_refs in ref_by_label.items():
        if label not in float_map:
            orphan_refs.append(label)
            continue

        referenced_labels.add(label)
        float_def = float_map[label]

        # Find the semantic reference used for B1 distance. RQ/overview list
        # items often enumerate future figures and tables; they remain valid
        # references, but they are weak anchors for float migration.
        first_ref = (
            choose_semantic_reference(tex_content, label_refs)
            if tex_content
            else min(label_refs, key=lambda r: r.line_number)
        )

        # Compute line distance
        line_distance = abs(first_ref.line_number - float_def.line_number)
        ref_before_float = first_ref.line_number < float_def.line_number

        # Compute section distance
        sec_distance = compute_section_distance(
            sections, first_ref.line_number, float_def.line_number
        )

        # Determine severity
        severity = "none"
        if line_distance > 100 or sec_distance >= 2:
            severity = "major"
        elif line_distance > 50 or sec_distance == 1:
            severity = "minor"

        # Treat float-page-only placement as a baseline violation. Even when
        # source distance is short, `[p]` often pushes floats away from the
        # paragraph that semantically owns them.
        if float_def.float_position in {"p", "!p"}:
            severity = "major"

        distances.append(CrossRefDistance(
            label=label,
            ref_line=first_ref.line_number,
            float_line=float_def.line_number,
            figure_line=float_def.line_number,
            float_type=float_def.float_type,
            line_distance=line_distance,
            ref_before_figure=ref_before_float,
            ref_before_float=ref_before_float,
            section_distance=sec_distance,
            severity=severity,
            reference_source=first_ref.source_type,
            reference_text=first_ref.matched_text,
        ))

    # Find orphan labels (floats that are never referenced)
    orphan_labels = [
        flt.label for flt in floats
        if flt.label not in referenced_labels
    ]

    return distances, orphan_refs, orphan_labels


def _resolve_input_path(base_dir: Path, root_dir: Path, raw_path: str) -> Optional[Path]:
    candidate = raw_path.strip()
    if not candidate:
        return None
    path = Path(candidate)
    if not path.suffix:
        path = path.with_suffix(".tex")
    if not path.is_absolute():
        for parent in (base_dir, root_dir):
            resolved = (parent / path).resolve()
            if resolved.is_file():
                return resolved
        return None
    path = path.resolve()
    return path if path.is_file() else None


def _expand_tex_inputs(
    tex_path: Path,
    seen: Optional[set[Path]] = None,
    root_dir: Optional[Path] = None,
) -> str:
    r"""Read a TeX file and inline direct \input/\include children for analysis."""
    seen = seen or set()
    resolved = tex_path.resolve()
    root_dir = root_dir.resolve() if root_dir else resolved.parent
    if resolved in seen:
        return f"\n% PaperFit skipped recursive input: {resolved}\n"
    seen.add(resolved)

    content = resolved.read_text(encoding='utf-8')
    include_re = re.compile(r'(?<!%)\\(?:input|include)\s*\{([^}]+)\}')

    def replace_include(match: re.Match[str]) -> str:
        child = _resolve_input_path(resolved.parent, root_dir, match.group(1))
        if child is None:
            return match.group(0)
        child_content = _expand_tex_inputs(child, seen, root_dir=root_dir)
        rel = child.relative_to(resolved.parent) if child.is_relative_to(resolved.parent) else child
        return (
            f"\n% PaperFit BEGIN input {rel}\n"
            f"{child_content}\n"
            f"% PaperFit END input {rel}\n"
        )

    return include_re.sub(replace_include, content)


def extract_tex_file(tex_path: str) -> str:
    r"""Read .tex content, expanding project-local \input/\include files."""
    return _expand_tex_inputs(Path(tex_path))


def extract_aux_file(aux_path: Path) -> Optional[str]:
    """Read adjacent .aux content when available."""
    if not aux_path.exists():
        return None
    return aux_path.read_text(encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(
        description='Extract cross-reference relationships from LaTeX .tex files'
    )
    parser.add_argument(
        'tex_file',
        help='Path to .tex file'
    )
    parser.add_argument(
        '--output', '-o',
        help='Output JSON file path'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Print verbose output'
    )

    args = parser.parse_args()

    # Extract .tex content
    tex_path = Path(args.tex_file)
    if not tex_path.exists():
        print(f"Error: File not found: {tex_path}", file=sys.stderr)
        sys.exit(1)

    tex_content = extract_tex_file(str(tex_path))

    # Extract components
    sections = extract_section_structure(tex_content)
    floats = extract_float_definitions(tex_content, sections=sections)
    aux_content = extract_aux_file(tex_path.with_suffix('.aux'))
    numbered_label_map = build_numbered_label_map(floats, aux_content=aux_content)
    refs = extract_refs(tex_content, numbered_label_map=numbered_label_map)

    # Analyze cross-references
    distances, orphan_refs, orphan_labels = analyze_cross_references(
        refs, floats, sections, tex_content=tex_content
    )

    # Build report
    major_issues = [d.label for d in distances if d.severity == "major"]

    figure_count = sum(1 for flt in floats if flt.float_type == "figure")
    table_count = sum(1 for flt in floats if flt.float_type == "table")

    report = CrossRefReport(
        source_file=str(tex_path),
        total_refs=len(refs),
        total_figures=figure_count,
        total_tables=table_count,
        total_floats=len(floats),
        figures_with_labels=figure_count,
        orphan_refs=orphan_refs,
        orphan_labels=orphan_labels,
        distances=distances,
        major_issues=major_issues
    )

    # Output
    output_data = {
        'source_file': report.source_file,
        'summary': {
            'total_refs': report.total_refs,
            'total_figures': report.total_figures,
            'total_tables': report.total_tables,
            'total_floats': report.total_floats,
            'figures_with_labels': report.figures_with_labels,
            'orphan_refs_count': len(report.orphan_refs),
            'orphan_labels_count': len(report.orphan_labels),
            'major_issues_count': len(report.major_issues)
        },
        'orphan_refs': report.orphan_refs,
        'orphan_labels': report.orphan_labels,
        'major_issues': report.major_issues,
        'distances': [asdict(d) for d in report.distances],
        'floats': [asdict(flt) for flt in floats],
    }

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=2)
        print(f"Report written to: {output_path}")
    else:
        print(json.dumps(output_data, indent=2))

    # Verbose summary
    if args.verbose:
        print(f"\n=== Summary ===", file=sys.stderr)
        print(f"Source: {report.source_file}", file=sys.stderr)
        print(f"Total \\ref{{fig:*}}: {report.total_refs}", file=sys.stderr)
        print(f"Total float environments: {report.total_floats}", file=sys.stderr)
        print(f"Major B1 candidates: {len(report.major_issues)}", file=sys.stderr)
        if report.major_issues:
            print(f"  Issues: {', '.join(report.major_issues)}", file=sys.stderr)


if __name__ == '__main__':
    main()
