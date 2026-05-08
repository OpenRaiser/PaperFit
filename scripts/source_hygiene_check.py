#!/usr/bin/env python3
"""
Source-level hygiene scanner for PaperFit repair rounds.

This catches non-visual pollution that routinely becomes low-score visual
output: unresolved ``??`` markers, placeholder/debug tokens, malformed math
payloads, and stray title-block text.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


POLLUTION_PATTERNS = [
    ("placeholder_token", re.compile(r"\b(?:PLACEHOLDER|REPLACE[_ -]?ME|DUMMY|TBD|TODO|FIXME|XXX|LOREM\s+IPSUM)\b", re.IGNORECASE), "major"),
    ("debug_token", re.compile(r"\b(?:DEBUG|TEST[_ -]?TOKEN|STRAY[_ -]?TEXT|PAPERFIT[_ -]?DEBUG)\b", re.IGNORECASE), "major"),
    ("unresolved_marker", re.compile(r"(?<!\?)\?\?(?!\?)"), "critical"),
    ("replacement_character", re.compile("�"), "critical"),
]

MATH_ENV_PATTERN = re.compile(
    r"(\$\$.*?\$\$|\$[^$]*?\$|\\\[.*?\\\]|"
    r"\\begin\{(?:equation\*?|align\*?|gather\*?|multline\*?|displaymath)\}.*?"
    r"\\end\{(?:equation\*?|align\*?|gather\*?|multline\*?|displaymath)\})",
    re.DOTALL,
)

TITLE_ZONE_END_PATTERN = re.compile(r"\\begin\{abstract\}|\\maketitle|\\section\*?\{", re.IGNORECASE)
LATEX_COMMAND_LINE_PATTERN = re.compile(r"^\s*(?:%|\\|\{|\}|$)")


@dataclass
class HygieneFinding:
    family: str
    severity: str
    line: int
    column: int
    snippet: str
    description: str


def _line_col(text: str, offset: int) -> tuple[int, int]:
    prefix = text[:offset]
    line = prefix.count("\n") + 1
    last_newline = prefix.rfind("\n")
    column = offset + 1 if last_newline < 0 else offset - last_newline
    return line, column


def _snippet(text: str, start: int, end: int, radius: int = 80) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    return re.sub(r"\s+", " ", text[left:right]).strip()


def _is_in_comment(text: str, offset: int) -> bool:
    line_start = text.rfind("\n", 0, offset) + 1
    line_prefix = text[line_start:offset]
    for match in re.finditer(r"(?<!\\)%", line_prefix):
        return True
    return False


def _find_group_end(text: str, open_brace_offset: int) -> Optional[int]:
    depth = 0
    index = open_brace_offset
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return None


def _scan_patterns(tex_content: str) -> Iterable[HygieneFinding]:
    for family, pattern, severity in POLLUTION_PATTERNS:
        for match in pattern.finditer(tex_content):
            if _is_in_comment(tex_content, match.start()):
                continue
            line, column = _line_col(tex_content, match.start())
            yield HygieneFinding(
                family=family,
                severity=severity,
                line=line,
                column=column,
                snippet=_snippet(tex_content, match.start(), match.end()),
                description=f"Source pollution token `{match.group(0)}` remains in TeX source",
            )


def _scan_math_payloads(tex_content: str) -> Iterable[HygieneFinding]:
    suspicious = re.compile(
        r"(?:/Volumes/|/Users/|\\Volumes\\|\\Users\\|PLACEHOLDER|DEBUG|TODO|FIXME|\?\?|�|NaN|NULL)",
        re.IGNORECASE,
    )
    for env_match in MATH_ENV_PATTERN.finditer(tex_content):
        body = env_match.group(0)
        for match in suspicious.finditer(body):
            offset = env_match.start() + match.start()
            if _is_in_comment(tex_content, offset):
                continue
            line, column = _line_col(tex_content, offset)
            yield HygieneFinding(
                family="suspicious_math_payload",
                severity="critical" if match.group(0) in {"??", "�"} else "major",
                line=line,
                column=column,
                snippet=_snippet(tex_content, offset, offset + len(match.group(0))),
                description=f"Suspicious non-math token `{match.group(0)}` appears inside a math environment",
            )


def _scan_title_zone(tex_content: str) -> Iterable[HygieneFinding]:
    begin_match = re.search(r"\\title(?:\[[^\]]*\])?\{", tex_content)
    if not begin_match:
        return
    title_end = _find_group_end(tex_content, begin_match.end() - 1)
    if title_end is None:
        title_end = begin_match.end()
    end_match = TITLE_ZONE_END_PATTERN.search(tex_content, title_end)
    zone_end = end_match.start() if end_match else min(len(tex_content), title_end + 2500)
    zone = tex_content[title_end:zone_end]
    zone_start = title_end
    offset = 0
    for raw_line in zone.splitlines(keepends=True):
        line_start = zone_start + offset
        offset += len(raw_line)
        stripped = raw_line.strip()
        if not stripped or LATEX_COMMAND_LINE_PATTERN.match(stripped):
            continue
        if re.fullmatch(r"[A-Za-z0-9 _./:-]{8,}", stripped):
            line, column = _line_col(tex_content, line_start + raw_line.find(stripped))
            yield HygieneFinding(
                family="title_stray_text",
                severity="major",
                line=line,
                column=column,
                snippet=stripped[:160],
                description="Bare stray text appears in the title/preamble zone before abstract/body",
            )


def scan_source(tex_path: str) -> Dict[str, Any]:
    path = Path(tex_path)
    tex_content = path.read_text(encoding="utf-8", errors="ignore")
    findings = list(_scan_patterns(tex_content))
    findings.extend(_scan_math_payloads(tex_content))
    findings.extend(_scan_title_zone(tex_content))

    severity_rank = {"critical": 3, "major": 2, "minor": 1}
    findings.sort(key=lambda item: (-severity_rank.get(item.severity, 0), item.line, item.column, item.family))
    by_family: Dict[str, int] = {}
    by_severity: Dict[str, int] = {}
    for finding in findings:
        by_family[finding.family] = by_family.get(finding.family, 0) + 1
        by_severity[finding.severity] = by_severity.get(finding.severity, 0) + 1

    return {
        "schema_version": "1.0",
        "generated_at": datetime.now().isoformat(),
        "source_file": str(path),
        "summary": {
            "finding_count": len(findings),
            "by_family": by_family,
            "by_severity": by_severity,
            "highest_severity": next((sev for sev in ("critical", "major", "minor") if by_severity.get(sev)), "clean"),
        },
        "findings": [asdict(finding) for finding in findings],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan TeX source for PaperFit hygiene regressions")
    parser.add_argument("tex_file")
    parser.add_argument("--output", "-o", default=None)
    args = parser.parse_args()

    report = scan_source(args.tex_file)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
