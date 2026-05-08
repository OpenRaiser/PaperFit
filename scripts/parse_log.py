#!/usr/bin/env python3
"""
LaTeX 编译日志解析器

解析 LaTeX 编译生成的 .log 文件，提取错误、警告、Overfull/Underfull hbox
等信息，并输出结构化的 JSON 报告，供 rule-engine-agent 使用。

用法:
    python parse_log.py <log_file> [--output <json_file>] [--verbose]

示例:
    python parse_log.py compile.log --output log_report.json
"""

import re
import json
import argparse
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional


class LogParser:
    """LaTeX 日志解析器"""

    def __init__(self, log_path: str):
        self.log_path = Path(log_path)
        self.content = ""
        self.errors: List[Dict] = []
        self.warnings: List[Dict] = []
        self.overfull_hbox: List[Dict] = []
        self.underfull_hbox: List[Dict] = []
        self.undefined_refs: List[str] = []
        self.citation_issues: List[str] = []
        self.float_warnings: List[Dict] = []
        self.package_warnings: List[Dict] = []
        self.suppressed_warnings: List[Dict] = []
        self.compile_success = True
        self.compile_diagnostics: Dict[str, Any] = {
            "primary_category": None,
            "missing_packages": [],
            "missing_files": [],
            "undefined_macros": [],
            "bibliography_signals": [],
            "suggested_action": None,
        }

    def parse(self) -> Dict[str, Any]:
        """执行完整解析，返回结构化报告"""
        if not self.log_path.exists():
            return {"error": f"Log file not found: {self.log_path}"}

        with open(self.log_path, 'r', encoding='utf-8', errors='ignore') as f:
            self.content = f.read()

        self._check_compile_success()
        self._extract_errors()
        self._extract_overfull()
        self._extract_underfull()
        self._extract_undefined_references()
        self._extract_citation_warnings()
        self._extract_float_warnings()
        self._extract_package_warnings()
        self._classify_compile_failure()

        return self._build_report()

    def _check_compile_success(self) -> None:
        """检查编译是否成功（日志中是否有 Fatal error）"""
        # 常见成功标志
        if "Output written on" in self.content:
            self.compile_success = True
        elif "Fatal error" in self.content or "Emergency stop" in self.content:
            self.compile_success = False
        else:
            # 默认视为成功（可能日志不完整）
            self.compile_success = True

    def _extract_errors(self) -> None:
        """提取 ! 开头的错误行"""
        lines = self.content.split('\n')
        for i, line in enumerate(lines):
            if line.startswith('!'):
                error = {
                    "type": "LaTeX Error",
                    "message": line[2:].strip(),
                    "line": self._find_line_number(lines, i),
                    "context": self._extract_context(lines, i)
                }
                self.errors.append(error)

    def _extract_overfull(self) -> None:
        r"""提取 Overfull \hbox 警告"""
        pattern = r'Overfull \\hbox \(([0-9.]+)pt too wide\) (.*?)(?: at lines ([0-9]+(?:--[0-9]+)?))?'
        matches = re.finditer(pattern, self.content, re.MULTILINE)

        for match in matches:
            overflow_pt = float(match.group(1))
            context = match.group(2).strip()
            lines_range = match.group(3) if match.group(3) else None

            # 判断是否在表格对齐环境中
            is_alignment = 'in alignment' in context

            entry = {
                "type": "Overfull hbox",
                "subtype": "alignment" if is_alignment else "paragraph",
                "overflow_pt": overflow_pt,
                "context": context,
                "lines": lines_range,
                "severity": "major" if overflow_pt >= 5.0 else "minor"
            }
            self.overfull_hbox.append(entry)
            self.warnings.append(entry)

    def _extract_underfull(self) -> None:
        r"""提取 Underfull \hbox 警告"""
        source_file: Optional[str] = None
        lines = self.content.split('\n')
        pattern = re.compile(
            r'Underfull \\hbox \(badness [0-9]+\)\s*(.*?)\s*(?:at lines ([0-9]+(?:--[0-9]+)?))?$'
        )

        for line in lines:
            for source_match in re.finditer(r'\((?:\./)?([^()\s]+)', line):
                token = source_match.group(1)
                if token.endswith(('.bbl', '.aux', '.tex', '.sty', '.cls')):
                    source_file = token

            stripped = line.strip()
            if not stripped.startswith('Underfull \\hbox'):
                if stripped in {')', ']'}:
                    source_file = None
                continue

            match = pattern.match(stripped)
            if not match:
                continue

            context = match.group(1).strip()
            lines_range = match.group(2) if match.group(2) else None
            entry = {
                "type": "Underfull hbox",
                "context": context,
                "lines": lines_range,
                "severity": "minor",
                "source_file": source_file,
            }
            if str(source_file or "").endswith(".bbl"):
                entry["suppressed_reason"] = "bibliography_generated_file"
                self.suppressed_warnings.append(entry)
                continue

            self.underfull_hbox.append(entry)
            self.warnings.append(entry)

    def _extract_undefined_references(self) -> None:
        """提取未定义的引用警告"""
        pattern = r'LaTeX Warning: Reference `([^`]+)\' undefined'
        matches = re.finditer(pattern, self.content)

        for match in matches:
            ref = match.group(1)
            self.undefined_refs.append(ref)
            self.warnings.append({
                "type": "Undefined reference",
                "reference": ref,
                "severity": "major"
            })

    def _extract_citation_warnings(self) -> None:
        """提取未定义的引用警告"""
        pattern = r'LaTeX Warning: Citation `([^`]+)\' .*undefined'
        matches = re.finditer(pattern, self.content)

        for match in matches:
            cite = match.group(1)
            self.citation_issues.append(cite)
            self.warnings.append({
                "type": "Undefined citation",
                "citation": cite,
                "severity": "major"
            })

    def _extract_float_warnings(self) -> None:
        """提取浮动体相关警告"""
        pattern = r'LaTeX Warning: Float too large for page by ([0-9.]+)pt'
        matches = re.finditer(pattern, self.content)

        for match in matches:
            overflow_pt = float(match.group(1))
            entry = {
                "type": "Float too large",
                "overflow_pt": overflow_pt,
                "severity": "major"
            }
            self.float_warnings.append(entry)
            self.warnings.append(entry)

    def _extract_package_warnings(self) -> None:
        """提取宏包警告（如 hyperref、caption 等）"""
        pattern = r'Package (\w+) Warning: (.*?)(?: on input line ([0-9]+))?'
        matches = re.finditer(pattern, self.content)

        for match in matches:
            package = match.group(1)
            message = match.group(2).strip()
            line = match.group(3) if match.group(3) else None

            entry = {
                "type": "Package warning",
                "package": package,
                "message": message,
                "line": line,
                "severity": "minor"
            }
            self.package_warnings.append(entry)
            self.warnings.append(entry)

    def _classify_compile_failure(self) -> None:
        missing_packages: List[str] = []
        missing_files: List[str] = []
        undefined_macros: List[str] = []
        bibliography_signals: List[str] = []

        for match in re.finditer(r"(?:LaTeX Error:\s*)?File [`']([^`']+)[`'] not found", self.content):
            file_name = match.group(1)
            suffix = Path(file_name).suffix.lower()
            if suffix in {".sty", ".cls", ".bst", ".bbx", ".cbx", ".cfg"}:
                missing_packages.append(Path(file_name).stem)
            else:
                missing_files.append(file_name)

        if "Undefined control sequence" in self.content:
            for error in self.errors:
                if "Undefined control sequence" not in str(error.get("message") or ""):
                    continue
                context = str(error.get("context") or "")
                macro_match = re.search(r"(\\[A-Za-z@]+)", context)
                if macro_match:
                    undefined_macros.append(macro_match.group(1))
            if not undefined_macros:
                undefined_macros.append("unknown_macro")

        if re.search(r"No file .*?\.bbl", self.content):
            bibliography_signals.append("missing_bbl")
        if re.search(r"I couldn't open database file", self.content):
            bibliography_signals.append("missing_bib_database")
        if self.citation_issues:
            bibliography_signals.append("undefined_citations")

        primary_category = None
        suggested_action = None
        if missing_packages:
            primary_category = "missing_package"
            suggested_action = "suggest_install_missing_package"
        elif undefined_macros:
            primary_category = "undefined_macro"
            suggested_action = "inspect_preamble_or_macro_definition"
        elif bibliography_signals:
            primary_category = "bibliography"
            suggested_action = "rerun_bibtex_or_check_bibliography_inputs"
        elif missing_files:
            primary_category = "missing_file"
            suggested_action = "restore_or_fix_missing_input_file"
        elif not self.compile_success and self.errors:
            primary_category = "latex_error"
            suggested_action = "inspect_error_context"

        self.compile_diagnostics = {
            "primary_category": primary_category,
            "missing_packages": sorted(set(missing_packages)),
            "missing_files": sorted(set(missing_files)),
            "undefined_macros": sorted(set(undefined_macros)),
            "bibliography_signals": sorted(set(bibliography_signals)),
            "suggested_action": suggested_action,
        }

    def _find_line_number(self, lines: List[str], current_idx: int) -> Optional[int]:
        """尝试从上下文中提取行号"""
        for offset in range(-3, 4):
            idx = current_idx + offset
            if 0 <= idx < len(lines):
                match = re.search(r'l\.([0-9]+)', lines[idx])
                if match:
                    return int(match.group(1))
        return None

    def _extract_context(self, lines: List[str], error_idx: int) -> str:
        """提取错误附近的上下文行"""
        context_lines = []
        for offset in range(1, 5):
            idx = error_idx + offset
            if idx < len(lines) and lines[idx].strip():
                context_lines.append(lines[idx].strip())
        return ' '.join(context_lines[:2])

    def _build_report(self) -> Dict[str, Any]:
        """构建最终 JSON 报告"""
        summary = {
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "overfull_hbox_total": len(self.overfull_hbox),
            "underfull_hbox_total": len(self.underfull_hbox),
            "undefined_references": len(self.undefined_refs),
            "citation_issues": len(self.citation_issues),
            "float_warnings": len(self.float_warnings),
            "package_warnings": len(self.package_warnings)
        }

        return {
            "parse_version": "1.0",
            "log_file": str(self.log_path),
            "compile_success": self.compile_success,
            "compile_diagnostics": self.compile_diagnostics,
            "summary": summary,
            "errors": self.errors,
            "warnings": self.warnings,
            "overfull_hbox": self.overfull_hbox,
            "underfull_hbox": self.underfull_hbox,
            "undefined_references": self.undefined_refs,
            "citation_issues": self.citation_issues,
            "float_warnings": self.float_warnings,
            "package_warnings": self.package_warnings,
            "suppressed_warnings": self.suppressed_warnings,
            "compilation_blockers": self.errors  # 任何错误都阻塞编译
        }


def main():
    parser = argparse.ArgumentParser(description="解析 LaTeX 编译日志")
    parser.add_argument("log_file", help="LaTeX .log 文件路径")
    parser.add_argument("--output", "-o", help="输出 JSON 文件路径")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")

    args = parser.parse_args()

    log_parser = LogParser(args.log_file)
    report = log_parser.parse()

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"Report saved to {args.output}")

    if args.verbose or not args.output:
        print(json.dumps(report, indent=2, ensure_ascii=False))

    # 根据编译状态返回退出码
    sys.exit(0 if report.get("compile_success", False) else 1)


if __name__ == "__main__":
    main()
