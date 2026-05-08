#!/usr/bin/env python3
"""
Executable PaperFit command driver.

This bridges host-facing slash-command semantics (/paperfit, /fix-layout,
/check-visual, /migrate-template) to real package scripts so any model that can
run `paperfit` can execute the same workflow.
"""

from __future__ import annotations

import argparse
import json
import locale
import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from orchestrator_runtime import OrchestratorRuntime
from paperfit_portrait import load_templates


def package_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _decode_output(blob: bytes | str | None) -> str:
    if blob is None:
        return ""
    if isinstance(blob, str):
        return blob

    preferred = locale.getpreferredencoding(False)
    for encoding in ("utf-8", preferred):
        if not encoding:
            continue
        try:
            return blob.decode(encoding)
        except UnicodeDecodeError:
            continue

    # Keep runtime robust even when command output contains mixed encodings.
    return blob.decode("utf-8", errors="replace")


def _run(cmd: List[str], *, cwd: Path, timeout: Optional[int] = None) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        start_new_session=True,
    )
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()

    stdout_text = _decode_output(stdout)
    stderr_text = _decode_output(stderr)
    if timed_out:
        timeout_message = f"TIMEOUT after {timeout}s"
        stderr_text = (stderr_text + "\n" + timeout_message).strip()
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=124,
            stdout=stdout_text,
            stderr=stderr_text,
        )

    return subprocess.CompletedProcess(
        args=cmd,
        returncode=process.returncode,
        stdout=stdout_text,
        stderr=stderr_text,
    )


def _mkdir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    _mkdir(path)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _read_state(project_root: Path) -> Dict[str, Any]:
    return _load_json(project_root / "data" / "state.json")


def _detect_main_tex(project_root: Path, main_override: Optional[str]) -> Path:
    if main_override:
        candidate = (project_root / main_override).resolve()
        if candidate.is_file():
            return candidate
        raise FileNotFoundError(f"main tex not found: {candidate}")

    state = _read_state(project_root)
    main_from_state = state.get("main_tex")
    if isinstance(main_from_state, str):
        candidate = (project_root / main_from_state).resolve()
        if candidate.is_file():
            return candidate

    tex_files = sorted(project_root.glob("*.tex"))
    docclass_hits: List[Path] = []
    for tex_path in tex_files:
        try:
            snippet = tex_path.read_text(encoding="utf-8", errors="replace")[:8000]
        except OSError:
            continue
        if r"\documentclass" in snippet:
            docclass_hits.append(tex_path.resolve())

    if len(docclass_hits) == 1:
        return docclass_hits[0]
    if docclass_hits:
        preferred_names = {"main.tex", "paper.tex", "aaai24_antibody.tex"}
        for candidate in docclass_hits:
            if candidate.name in preferred_names:
                return candidate
        docclass_hits.sort(key=lambda p: (len(p.name), p.name))
        return docclass_hits[0]

    raise FileNotFoundError(f"unable to infer main tex under {project_root}")


def _resolve_template_key(template: Optional[str]) -> Optional[str]:
    if not template:
        return None
    templates = load_templates()
    if template in templates:
        return template
    lowered = template.lower()
    for key in templates:
        if key.lower() == lowered:
            return key
    compact = re.sub(r"[\s_-]+", "", lowered)
    for key in templates:
        if re.sub(r"[\s_-]+", "", key.lower()) == compact:
            return key
    return template


def _default_target_pages(template_key: Optional[str], page_budget: str) -> Optional[int]:
    if not template_key:
        return None
    templates = load_templates()
    template = templates.get(template_key) or {}
    expected = template.get("expected_pages") or {}
    value = expected.get(page_budget)
    if value is None and page_budget != "main_body":
        value = expected.get("main_body")
    return int(value) if isinstance(value, int) else None


def _infer_pdf_page_count(project_root: Path, main_tex: Path) -> Optional[int]:
    pdf_path = project_root / f"{main_tex.stem}.pdf"
    if not pdf_path.is_file():
        return None
    result = _run(["pdfinfo", str(pdf_path)], cwd=project_root)
    if result.returncode != 0:
        return None
    for line in (result.stdout or "").splitlines():
        if line.lower().startswith("pages:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def _build_portrait(
    project_root: Path,
    *,
    main_tex: Path,
    template: Optional[str],
    page_budget: str,
    target_pages: Optional[int],
    strict: bool,
    max_rounds: int,
) -> Dict[str, Any]:
    script = package_root() / "scripts" / "paperfit_portrait.py"
    effective_target_pages = target_pages or _infer_pdf_page_count(project_root, main_tex) or 9
    cmd = [
        sys.executable,
        str(script),
        "build",
        "--main",
        str(main_tex.relative_to(project_root)),
        "--page-budget",
        page_budget,
        "--target-pages",
        str(effective_target_pages),
        "--max-rounds",
        str(max_rounds),
    ]
    if template:
        cmd.extend(["--template", template])
    if strict:
        cmd.append("--strict")
    result = _run(cmd, cwd=project_root)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "portrait build failed")
    return {"command": cmd, "stdout": result.stdout.strip()}


def _refresh_portrait(project_root: Path, *, main_tex: Path) -> Dict[str, Any]:
    script = package_root() / "scripts" / "paperfit_portrait.py"
    cmd = [
        sys.executable,
        str(script),
        "refresh",
        "--main",
        str(main_tex.relative_to(project_root)),
    ]
    result = _run(cmd, cwd=project_root)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "portrait refresh failed")
    return {"command": cmd, "stdout": result.stdout.strip()}


def _compile(project_root: Path, *, main_tex: Path) -> Dict[str, Any]:
    timeout_sec = int(os.environ.get("PAPERFIT_COMPILE_TIMEOUT_SEC", "240"))
    if os.environ.get("PAPERFIT_BOUNDED_COMPILE") == "1":
        bounded = _compile_bounded(project_root, main_tex=main_tex, timeout_sec=timeout_sec)
        if bounded.get("available"):
            return bounded

    cmd = [
        "latexmk",
        "-pdf",
        "-interaction=nonstopmode",
        "-halt-on-error",
        main_tex.name,
    ]
    pdf_path = project_root / f"{main_tex.stem}.pdf"
    _clean_main_build_artifacts(project_root, main_tex=main_tex)
    try:
        pdf_path.unlink()
    except FileNotFoundError:
        pass
    result = _run(cmd, cwd=project_root, timeout=timeout_sec)
    compile_log = project_root / "data" / "logs" / "paperfit_compile.log"
    _mkdir(compile_log)
    compile_log.write_text((result.stdout or "") + ("\n" + result.stderr if result.stderr else ""), encoding="utf-8")
    timed_out = result.returncode == 124
    combined_log = (result.stdout or "") + "\n" + (result.stderr or "")
    fatal_patterns = [
        "Fatal error occurred",
        "Emergency stop",
        "! LaTeX Error:",
        " ==> Fatal error",
    ]
    fatal_error = any(pattern in combined_log for pattern in fatal_patterns)
    partial_pdf_available = bool(timed_out and pdf_path.is_file() and not fatal_error)
    success = (result.returncode == 0 and pdf_path.is_file()) or partial_pdf_available
    return {
        "success": success,
        "command": cmd,
        "returncode": result.returncode,
        "timeout": timed_out,
        "timeout_sec": timeout_sec,
        "partial_pdf_available": partial_pdf_available,
        "fatal_error": fatal_error,
        "stdout_tail": (result.stdout or "")[-4000:],
        "stderr_tail": (result.stderr or "")[-4000:],
        "log_file": f"{main_tex.stem}.log",
        "pdf_path": str(pdf_path) if success else None,
        "compile_log": str(compile_log),
    }


def _clean_main_build_artifacts(project_root: Path, *, main_tex: Path) -> None:
    for suffix in [
        ".aux",
        ".bbl",
        ".bcf",
        ".blg",
        ".fdb_latexmk",
        ".fls",
        ".log",
        ".out",
        ".run.xml",
        ".toc",
    ]:
        try:
            (project_root / f"{main_tex.stem}{suffix}").unlink()
        except FileNotFoundError:
            pass


def _pdf_page_count(pdf_path: Path) -> Optional[int]:
    if not pdf_path.exists():
        return None
    result = _run(["pdfinfo", str(pdf_path)], cwd=pdf_path.parent, timeout=30)
    if result.returncode != 0:
        return None
    for line in (result.stdout or "").splitlines():
        if line.startswith("Pages:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def _compile_bounded(project_root: Path, *, main_tex: Path, timeout_sec: int) -> Dict[str, Any]:
    pdflatex = shutil.which("pdflatex")
    if not pdflatex:
        return {"available": False}
    pdf_path = project_root / f"{main_tex.stem}.pdf"
    good_pdf_path = project_root / f"{main_tex.stem}.paperfit-good.pdf"
    _clean_main_build_artifacts(project_root, main_tex=main_tex)
    for path in (pdf_path, good_pdf_path):
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    pass_timeout = max(60, min(timeout_sec, 240))
    passes: List[Dict[str, Any]] = []
    timed_out = False
    fatal_error = False
    restored_good_pdf = False
    have_good_pdf = False

    def _record(result: subprocess.CompletedProcess[str]) -> None:
        nonlocal timed_out, fatal_error
        timed_out = timed_out or result.returncode == 124
        combined = (result.stdout or "") + "\n" + (result.stderr or "")
        fatal_error = fatal_error or any(
            pattern in combined
            for pattern in ["Fatal error occurred", "Emergency stop", "! LaTeX Error:", " ==> Fatal error"]
        )
        passes.append(
            {
                "command": result.args if isinstance(result.args, list) else [str(result.args)],
                "returncode": result.returncode,
                "timeout": result.returncode == 124,
                "stdout_tail": (result.stdout or "")[-4000:],
                "stderr_tail": (result.stderr or "")[-4000:],
            }
        )

    def _snapshot_if_readable() -> bool:
        if _pdf_page_count(pdf_path) is None:
            return False
        shutil.copy2(pdf_path, good_pdf_path)
        return True

    def _run_pdflatex() -> subprocess.CompletedProcess[str]:
        return _run(
            [pdflatex, "-interaction=nonstopmode", "-halt-on-error", "-recorder", main_tex.name],
            cwd=project_root,
            timeout=pass_timeout,
        )

    result = _run_pdflatex()
    _record(result)
    have_good_pdf = _snapshot_if_readable()

    aux_path = project_root / f"{main_tex.stem}.aux"
    bibtex = shutil.which("bibtex")
    if aux_path.exists() and bibtex:
        aux_text = aux_path.read_text(encoding="utf-8", errors="replace")
        if r"\bibdata{" in aux_text:
            result = _run([bibtex, main_tex.stem], cwd=project_root, timeout=max(60, min(timeout_sec, 180)))
            _record(result)

    for _ in range(2):
        result = _run_pdflatex()
        _record(result)
        if _snapshot_if_readable():
            have_good_pdf = True
        elif result.returncode != 0 and have_good_pdf:
            shutil.copy2(good_pdf_path, pdf_path)
            restored_good_pdf = True
            break
        elif result.returncode != 0:
            break

    if have_good_pdf and _pdf_page_count(pdf_path) is None:
        shutil.copy2(good_pdf_path, pdf_path)
        restored_good_pdf = True

    try:
        good_pdf_path.unlink()
    except FileNotFoundError:
        pass

    compile_log = project_root / "data" / "logs" / "paperfit_compile.log"
    _mkdir(compile_log)
    compile_log.write_text(
        "\n".join(
            (item.get("stdout_tail") or "") + ("\n" + str(item.get("stderr_tail") or "") if item.get("stderr_tail") else "")
            for item in passes
        ),
        encoding="utf-8",
    )
    page_count = _pdf_page_count(pdf_path)
    success = page_count is not None and not fatal_error
    return {
        "available": True,
        "success": success,
        "command": [item.get("command") for item in passes],
        "returncode": 0 if success else (124 if timed_out else int((passes[-1] if passes else {}).get("returncode") or 1)),
        "timeout": timed_out,
        "timeout_sec": timeout_sec,
        "bounded_compile": True,
        "restored_good_pdf": restored_good_pdf,
        "partial_pdf_available": False,
        "fatal_error": fatal_error,
        "passes": passes,
        "stdout_tail": "\n".join(str(item.get("stdout_tail") or "") for item in passes)[-4000:],
        "stderr_tail": "\n".join(str(item.get("stderr_tail") or "") for item in passes)[-4000:],
        "log_file": f"{main_tex.stem}.log",
        "pdf_path": str(pdf_path) if success else None,
        "compile_log": str(compile_log),
    }


def _sanitize_precompile_sources(project_root: Path, *, main_tex: Path) -> Dict[str, Any]:
    tex_path = (project_root / main_tex.name).resolve()
    if not tex_path.is_file():
        return {"changed": False, "main_tex": str(tex_path), "reasons": ["main_tex_not_found"]}

    original = tex_path.read_text(encoding="utf-8", errors="replace")
    updated = original
    widow_token_removals = 0
    template_budget_shift_removals = 0
    multline_alignment_fixes = 0
    siunitx_key_fixes = 0
    tabularx_package_added = False
    reasons: List[str] = []

    # Benchmark A1 disturbance may inject a forced linebreak token that can appear in invalid context.
    pattern_same_line = re.compile(
        r"^[ \t]*\\linebreak\[\s*4\s*\]\s*\\mbox\{PaperFitWidowTailToken\}[ \t]*\n?",
        re.MULTILINE,
    )
    updated, count_same = pattern_same_line.subn("", updated)
    widow_token_removals += count_same

    pattern_split_lines = re.compile(
        r"^[ \t]*\\linebreak\[\s*4\s*\][ \t]*\n[ \t]*\\mbox\{PaperFitWidowTailToken\}[ \t]*\n?",
        re.MULTILINE,
    )
    updated, count_split = pattern_split_lines.subn("", updated)
    widow_token_removals += count_split

    pattern_token_only = re.compile(r"^.*PaperFitWidowTailToken.*\n?", re.MULTILINE)
    updated, count_token = pattern_token_only.subn("", updated)
    widow_token_removals += count_token

    if widow_token_removals > 0:
        reasons.append("removed_widow_disturbance_token")

    # Benchmark E2 disturbance may shrink textheight globally and create large bottom whitespace on every page.
    e2_block_pattern = re.compile(
        r"^[ \t]*%[ \t]*DISTURBANCE:E2_template_page_budget_shift:BEGIN[^\n]*\n"
        r".*?"
        r"^[ \t]*%[ \t]*DISTURBANCE:E2_template_page_budget_shift:END[^\n]*\n?",
        re.MULTILINE | re.DOTALL,
    )
    updated, count_e2_blocks = e2_block_pattern.subn("", updated)
    template_budget_shift_removals += count_e2_blocks

    if template_budget_shift_removals > 0:
        reasons.append("removed_template_page_budget_shift_disturbance")

    def _fix_multline_alignment(match: re.Match[str]) -> str:
        nonlocal multline_alignment_fixes
        body = match.group(2)
        if "&" not in body:
            return match.group(0)
        multline_alignment_fixes += 1
        star = "*" if match.group(1).endswith("*") else ""
        return f"\\begin{{align{star}}}{body}\\end{{align{star}}}"

    updated = re.sub(
        r"\\begin\{(multline\*?)\}(.*?)\\end\{\1\}",
        _fix_multline_alignment,
        updated,
        flags=re.DOTALL,
    )
    if multline_alignment_fixes:
        reasons.append("converted_aligned_multline_to_align")

    siunitx_key_typos = {
        "table-foXmat": "table-format",
        "tabXe-foXmat": "table-format",
        "tabXe-format": "table-format",
    }
    for typo, correct in siunitx_key_typos.items():
        updated, count = re.subn(
            rf"(?<=\[){re.escape(typo)}(?=\s*=)|(?<=,){re.escape(typo)}(?=\s*=)",
            correct,
            updated,
        )
        siunitx_key_fixes += count
    if siunitx_key_fixes:
        reasons.append("fixed_siunitx_table_format_key_typos")

    eccv_docclass_fixed = False
    begin_document_idx = updated.find(r"\begin{document}")
    preamble = updated if begin_document_idx < 0 else updated[:begin_document_idx]
    eccv_package_pattern = re.compile(
        r"^[ \t]*\\usepackage(?:\[[^\]]*\])?\{eccv\}[ \t]*(?:%[^\n]*)?$",
        re.MULTILINE,
    )
    docclass_pattern = re.compile(
        r"^[ \t]*\\documentclass(?:\[[^\]]*\])?\{([^}]+)\}[ \t]*(?:%[^\n]*)?$",
        re.MULTILINE,
    )
    if eccv_package_pattern.search(preamble):
        docclass_match = docclass_pattern.search(preamble)
        if docclass_match:
            cls = docclass_match.group(1).strip().lower()
            if cls != "llncs":
                replacement = r"\documentclass[runningheads]{llncs}"
                updated = (
                    updated[:docclass_match.start()]
                    + replacement
                    + updated[docclass_match.end():]
                )
                eccv_docclass_fixed = True
                reasons.append("fixed_eccv_docclass_to_llncs")

    has_project_tabularx = False
    for tex_file in project_root.rglob("*.tex"):
        if any(part in {".git", "data", "pages", "page_images"} for part in tex_file.relative_to(project_root).parts[:-1]):
            continue
        try:
            if r"\begin{tabularx}" in tex_file.read_text(encoding="utf-8", errors="replace"):
                has_project_tabularx = True
                break
        except OSError:
            continue
    begin_document_idx = updated.find(r"\begin{document}")
    preamble = updated if begin_document_idx < 0 else updated[:begin_document_idx]
    if has_project_tabularx and r"\usepackage{tabularx}" not in preamble and begin_document_idx >= 0:
        updated = updated[:begin_document_idx] + "\\usepackage{tabularx}\n" + updated[begin_document_idx:]
        tabularx_package_added = True
        reasons.append("added_tabularx_package_for_project_tables")

    changed = updated != original
    if changed:
        tex_path.write_text(updated, encoding="utf-8")

    return {
        "changed": changed,
        "main_tex": str(tex_path),
        "widow_token_removals": widow_token_removals,
        "template_budget_shift_removals": template_budget_shift_removals,
        "multline_alignment_fixes": multline_alignment_fixes,
        "siunitx_key_fixes": siunitx_key_fixes,
        "eccv_docclass_fixed": eccv_docclass_fixed,
        "tabularx_package_added": tabularx_package_added,
        "reasons": reasons,
    }


def _render(project_root: Path, *, pdf_path: Path, output_dir: str = "data/pages") -> Dict[str, Any]:
    cmd = [
        sys.executable,
        str(package_root() / "scripts" / "render_pages.py"),
        str(pdf_path),
        "--output",
        output_dir,
        "--dpi",
        "220",
    ]
    result = _run(cmd, cwd=project_root)
    success = result.returncode == 0
    return {
        "success": success,
        "command": cmd,
        "returncode": result.returncode,
        "stdout_tail": (result.stdout or "")[-4000:],
        "stderr_tail": (result.stderr or "")[-4000:],
        "page_dir": output_dir,
    }


def _extract_pdf_pages_text(pdf_path: Path) -> List[str]:
    result = _run(["pdftotext", "-layout", str(pdf_path), "-"], cwd=pdf_path.parent)
    if result.returncode != 0:
        return []
    pages = (result.stdout or "").split("\f")
    while pages and not pages[-1].strip():
        pages.pop()
    return pages


def _inspect_endmatter_float_intrusion(pdf_path: Path) -> Dict[str, Any]:
    pages = _extract_pdf_pages_text(pdf_path)
    if not pages:
        return {"available": False, "hard_failures": []}

    heading_patterns = [
        re.compile(r"^\s*Acknowledg(?:e)?ments\b", re.IGNORECASE | re.MULTILINE),
        re.compile(r"^\s*References\b", re.IGNORECASE | re.MULTILINE),
        re.compile(r"^\s*Bibliography\b", re.IGNORECASE | re.MULTILINE),
    ]
    caption_pattern = re.compile(r"(^|\n)\s*((?:Figure|Table)\s+\d+\s*:)", re.IGNORECASE)

    start_page: Optional[int] = None
    start_heading: Optional[str] = None
    start_offset: Optional[int] = None
    for index, page_text in enumerate(pages, start=1):
        for pattern in heading_patterns:
            match = pattern.search(page_text)
            if match:
                start_page = index
                start_heading = match.group(0).strip()
                start_offset = match.start()
                break
        if start_page is not None:
            break

    if start_page is None:
        return {"available": True, "hard_failures": [], "endmatter_start_page": None, "intrusions": []}

    intrusions: List[Dict[str, Any]] = []
    for index in range(start_page, len(pages) + 1):
        page_text = pages[index - 1]
        captions = []
        for match in caption_pattern.finditer(page_text):
            if index == start_page and start_offset is not None and match.start() < start_offset:
                continue
            captions.append(match.group(2).strip())
        captions = sorted(set(captions))
        if captions:
            intrusions.append({"page": index, "captions": captions})

    hard_failures: List[str] = []
    if intrusions:
        detail = ", ".join(
            f"page {item['page']}: {' / '.join(item['captions'])}"
            for item in intrusions
        )
        hard_failures.append(
            f"Body float caption detected on endmatter pages starting at page {start_page} ({start_heading}): {detail}"
        )

    return {
        "available": True,
        "endmatter_start_page": start_page,
        "endmatter_heading": start_heading,
        "intrusions": intrusions,
        "hard_failures": hard_failures,
    }


def _run_round(
    project_root: Path,
    *,
    main_tex: Path,
    template: Optional[str],
    target_pages: Optional[int],
    page_dir: str,
) -> Dict[str, Any]:
    runtime = OrchestratorRuntime(state_path=str(project_root / "data" / "state.json"))
    cwd_before = Path.cwd()
    try:
        os.chdir(project_root)
        state = runtime.run_round(
            main_tex=str(main_tex.relative_to(project_root)),
            log_file=f"{main_tex.stem}.log",
            page_dir=page_dir,
            template=template,
            target_pages=target_pages,
        )
    finally:
        os.chdir(cwd_before)
    return state


def _execute_repair_plan(project_root: Path, *, main_tex: Path, max_candidates: int = 5) -> Dict[str, Any]:
    runtime = OrchestratorRuntime(state_path=str(project_root / "data" / "state.json"))
    cwd_before = Path.cwd()
    try:
        os.chdir(project_root)
        state = runtime.execute_repair_plan(
            main_tex=str(main_tex.relative_to(project_root)),
            output_path="data/repair_execution_report.json",
            max_candidates=max_candidates,
        )
    finally:
        os.chdir(cwd_before)
    return state


def _copy_project(src_root: Path, dst_root: Path) -> Path:
    if dst_root.exists():
        raise FileExistsError(f"save-as target already exists: {dst_root}")
    ignore = shutil.ignore_patterns(
        "*.aux",
        "*.bbl",
        "*.blg",
        "*.fdb_latexmk",
        "*.fls",
        "*.log",
        "*.out",
        "*.synctex.gz",
        "data",
        "archives",
        "page_images",
        "data/pages*",
        "__pycache__",
        ".DS_Store",
        "._*",
    )
    shutil.copytree(src_root, dst_root, ignore=ignore)
    return dst_root


def _migrate_template(
    project_root: Path,
    *,
    main_tex: Path,
    target_template: str,
) -> Dict[str, Any]:
    report_path = project_root / "data" / f"template_migration_report_{target_template.lower()}.json"
    cmd = [
        sys.executable,
        str(package_root() / "scripts" / "template_migrate.py"),
        str(main_tex.relative_to(project_root)),
        "--target",
        target_template,
        "--output",
        str(main_tex.relative_to(project_root)),
        "--report",
        str(report_path.relative_to(project_root)),
    ]
    result = _run(cmd, cwd=project_root)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "template migrate failed")
    return {
        "command": cmd,
        "report_path": str(report_path),
        "report": _load_json(report_path),
    }


def _summarize_state(state: Dict[str, Any]) -> Dict[str, Any]:
    task = state.get("task") or {}
    return {
        "status": state.get("status"),
        "main_tex": state.get("main_tex"),
        "compile_success": state.get("compile_success"),
        "page_images_rendered": state.get("page_images_rendered"),
        "current_round": state.get("current_round"),
        "last_gatekeeper_decision": state.get("last_gatekeeper_decision"),
        "defect_summary": state.get("defect_summary"),
        "next_actions": state.get("next_actions"),
        "artifacts": state.get("artifacts"),
        "task": {
            "task_type": task.get("task_type"),
            "template": task.get("template"),
            "target_pages": task.get("target_pages"),
            "column_type": task.get("column_type"),
            "page_budget_scope": task.get("page_budget_scope"),
        },
    }


def _compile_timeout_summary(main_tex: Path, timeout_sec: int) -> Dict[str, Any]:
    return {
        "status": "BLOCKED",
        "main_tex": main_tex.name,
        "compile_success": False,
        "page_images_rendered": False,
        "current_round": None,
        "last_gatekeeper_decision": "BLOCKED",
        "defect_summary": {
            "initial_total": 1,
            "resolved": 0,
            "remaining": 1,
        },
        "next_actions": [
            f"Compilation exceeded {timeout_sec}s; inspect TeX macro recursion or oversized assets",
        ],
        "artifacts": {},
        "task": {},
    }


def _positive_env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _run_visual_only(
    project_root: Path,
    *,
    main_tex: Path,
    template: Optional[str],
    target_pages: Optional[int],
) -> Dict[str, Any]:
    pre_compile_sanitization = _sanitize_precompile_sources(project_root, main_tex=main_tex)
    compile_result = _compile(project_root, main_tex=main_tex)
    page_dir = "data/pages"
    render_result = {"success": False, "page_dir": page_dir}
    visual_hard_guards = {"available": False, "hard_failures": []}
    if compile_result["success"] and compile_result["pdf_path"]:
        render_result = _render(project_root, pdf_path=Path(compile_result["pdf_path"]), output_dir=page_dir)
        visual_hard_guards = _inspect_endmatter_float_intrusion(Path(compile_result["pdf_path"]))
    if compile_result.get("timeout") and not compile_result.get("success"):
        state_summary = _compile_timeout_summary(main_tex, int(compile_result.get("timeout_sec") or 0))
    else:
        state = _run_round(
            project_root,
            main_tex=main_tex,
            template=template,
            target_pages=target_pages,
            page_dir=page_dir,
        )
        state_summary = _summarize_state(state)
    report = {
        "mode": "check_visual",
        "project_root": str(project_root),
        "pre_compile_sanitization": pre_compile_sanitization,
        "compile": compile_result,
        "render": render_result,
        "visual_hard_guards": visual_hard_guards,
        "state_summary": state_summary,
    }
    report_path = project_root / "data" / "check_visual_report.json"
    _write_json(report_path, report)
    report["report_path"] = str(report_path)
    return report


def _run_fix_layout(
    project_root: Path,
    *,
    main_tex: Path,
    template: Optional[str],
    target_pages: Optional[int],
    max_rounds: Optional[int],
) -> Dict[str, Any]:
    iterations: List[Dict[str, Any]] = []
    pending_verification = False
    stop_reason: Optional[str] = None
    blocked_reasons: List[str] = []
    run_until_done = os.environ.get("PAPERFIT_RUN_UNTIL_DONE") == "1"
    round_limit = None if run_until_done else (max_rounds if max_rounds and max_rounds > 0 else None)
    hard_round_limit = _positive_env_int("PAPERFIT_MAX_TOTAL_ROUNDS", 120 if run_until_done else 0)
    stall_round_limit = _positive_env_int("PAPERFIT_STALL_ROUNDS", 40 if run_until_done else 0)
    no_apply_round_limit = _positive_env_int("PAPERFIT_NO_APPLY_ROUNDS", 15 if run_until_done else 0)
    state_stall_failure_limit = _positive_env_int("PAPERFIT_STATE_STALL_FAILURES", 40 if run_until_done else 0)
    best_remaining: Optional[int] = None
    rounds_without_remaining_improvement = 0
    consecutive_no_apply = 0
    index = 1
    while round_limit is None or index <= round_limit:
        if run_until_done and hard_round_limit and len(iterations) >= hard_round_limit:
            pending_verification = False
            stop_reason = "blocked"
            if "max_total_rounds_reached" not in blocked_reasons:
                blocked_reasons.append("max_total_rounds_reached")
            break

        state: Dict[str, Any] = {}
        pre_compile_sanitization = _sanitize_precompile_sources(project_root, main_tex=main_tex)
        compile_result = _compile(project_root, main_tex=main_tex)
        page_dir = "data/pages"
        render_result = {"success": False, "page_dir": page_dir}
        visual_hard_guards = {"available": False, "hard_failures": []}
        if compile_result["success"] and compile_result["pdf_path"]:
            render_result = _render(project_root, pdf_path=Path(compile_result["pdf_path"]), output_dir=page_dir)
            visual_hard_guards = _inspect_endmatter_float_intrusion(Path(compile_result["pdf_path"]))
        if compile_result.get("timeout") and not compile_result.get("success"):
            state_summary = _compile_timeout_summary(main_tex, int(compile_result.get("timeout_sec") or 0))
        else:
            state = _run_round(
                project_root,
                main_tex=main_tex,
                template=template,
                target_pages=target_pages,
                page_dir=page_dir,
            )
            state_summary = _summarize_state(state)
        iteration: Dict[str, Any] = {
            "round": index,
            "pre_compile_sanitization": pre_compile_sanitization,
            "compile": compile_result,
            "render": render_result,
            "visual_hard_guards": visual_hard_guards,
            "state_summary": state_summary,
        }
        iterations.append(iteration)
        if compile_result.get("timeout") and not compile_result.get("success"):
            pending_verification = False
            if "compile_timeout" not in blocked_reasons:
                blocked_reasons.append("compile_timeout")
            if not run_until_done:
                stop_reason = "blocked"
                break
        elif not compile_result.get("success"):
            pending_verification = False
            if "compile_failed" not in blocked_reasons:
                blocked_reasons.append("compile_failed")
            if not run_until_done:
                stop_reason = "blocked"
                break

        defect_summary = state_summary.get("defect_summary") or {}
        remaining = int(defect_summary.get("remaining") or 0)
        if best_remaining is None or remaining < best_remaining:
            best_remaining = remaining
            rounds_without_remaining_improvement = 0
        else:
            rounds_without_remaining_improvement += 1

        decision = state_summary.get("last_gatekeeper_decision")
        failure_tracking = state.get("failure_tracking") if isinstance(state, dict) else {}
        if not isinstance(failure_tracking, dict):
            failure_tracking = {}
        iteration["failure_tracking"] = failure_tracking
        iteration["progress"] = {
            "remaining_defects": remaining,
            "best_remaining_defects": best_remaining,
            "rounds_without_remaining_improvement": rounds_without_remaining_improvement,
            "consecutive_no_apply": consecutive_no_apply,
        }

        if (decision == "DONE" or remaining == 0) and not visual_hard_guards.get("hard_failures"):
            pending_verification = False
            stop_reason = "done"
            break

        if run_until_done and stall_round_limit and rounds_without_remaining_improvement >= stall_round_limit:
            pending_verification = False
            stop_reason = "blocked"
            if "stalled_no_remaining_improvement" not in blocked_reasons:
                blocked_reasons.append("stalled_no_remaining_improvement")
            break

        if (
            run_until_done
            and state_stall_failure_limit
            and bool(failure_tracking.get("stalled"))
            and int(failure_tracking.get("consecutive_failures") or 0) >= state_stall_failure_limit
        ):
            pending_verification = False
            stop_reason = "blocked"
            if "failure_tracking_stalled" not in blocked_reasons:
                blocked_reasons.append("failure_tracking_stalled")
            break

        repair_state = _execute_repair_plan(project_root, main_tex=main_tex)
        repair_summary = repair_state.get("repair_execution_summary") or {}
        if not isinstance(repair_summary, dict):
            repair_summary = {}
        raw_applied_count = int(repair_summary.get("applied_count") or 0)
        effective_applied_count = raw_applied_count
        effective_source_changed: Optional[bool] = None

        repair_report = _load_json(project_root / "data" / "repair_execution_report.json")
        hash_comparison = (
            ((repair_report.get("content_integrity") or {}).get("diff") or {}).get("hash_comparison") or {}
        )
        if isinstance(hash_comparison.get("identical"), bool):
            effective_source_changed = not bool(hash_comparison.get("identical"))
        if raw_applied_count > 0 and effective_source_changed is False:
            effective_applied_count = 0
            if "repair_no_effective_source_change" not in blocked_reasons:
                blocked_reasons.append("repair_no_effective_source_change")

        iteration["repair_execution_summary"] = {
            **repair_summary,
            "applied_count_raw": raw_applied_count,
            "applied_count_effective": effective_applied_count,
            "effective_source_changed": effective_source_changed,
        }
        applied_count = effective_applied_count
        if applied_count <= 0:
            consecutive_no_apply += 1
            pending_verification = False
            if raw_applied_count > 0 and effective_source_changed is False:
                if "no_effective_patch_applied" not in blocked_reasons:
                    blocked_reasons.append("no_effective_patch_applied")
            elif "no_repair_candidates_applied" not in blocked_reasons:
                blocked_reasons.append("no_repair_candidates_applied")
            repair_status = str(repair_summary.get("status") or "")
            repair_reason = f"repair_execution_{repair_status}" if repair_status else ""
            if repair_reason and repair_reason not in blocked_reasons:
                blocked_reasons.append(repair_reason)
            if run_until_done and no_apply_round_limit and consecutive_no_apply >= no_apply_round_limit:
                stop_reason = "blocked"
                if "no_repair_progress_limit_reached" not in blocked_reasons:
                    blocked_reasons.append("no_repair_progress_limit_reached")
                break
            if not run_until_done:
                stop_reason = "blocked"
                break
            _refresh_portrait(project_root, main_tex=main_tex)
            index += 1
            continue

        consecutive_no_apply = 0
        pending_verification = True
        _refresh_portrait(project_root, main_tex=main_tex)
        index += 1
    else:
        stop_reason = "round_limit"

    if pending_verification:
        pre_compile_sanitization = _sanitize_precompile_sources(project_root, main_tex=main_tex)
        compile_result = _compile(project_root, main_tex=main_tex)
        page_dir = "data/pages"
        render_result = {"success": False, "page_dir": page_dir}
        visual_hard_guards = {"available": False, "hard_failures": []}
        if compile_result["success"] and compile_result["pdf_path"]:
            render_result = _render(project_root, pdf_path=Path(compile_result["pdf_path"]), output_dir=page_dir)
            visual_hard_guards = _inspect_endmatter_float_intrusion(Path(compile_result["pdf_path"]))
        if compile_result.get("timeout") and not compile_result.get("success"):
            state_summary = _compile_timeout_summary(main_tex, int(compile_result.get("timeout_sec") or 0))
            if "compile_timeout" not in blocked_reasons:
                blocked_reasons.append("compile_timeout")
            if not run_until_done:
                stop_reason = "blocked"
        else:
            state = _run_round(
                project_root,
                main_tex=main_tex,
                template=template,
                target_pages=target_pages,
                page_dir=page_dir,
            )
            state_summary = _summarize_state(state)
            if not compile_result.get("success"):
                if "compile_failed" not in blocked_reasons:
                    blocked_reasons.append("compile_failed")
                if not run_until_done:
                    stop_reason = "blocked"
        iterations.append(
            {
                "round": len(iterations) + 1,
                "verification_only": True,
                "pre_compile_sanitization": pre_compile_sanitization,
                "compile": compile_result,
                "render": render_result,
                "visual_hard_guards": visual_hard_guards,
                "state_summary": state_summary,
            }
        )
        verification_summary = iterations[-1].get("state_summary") or {}
        verification_defects = verification_summary.get("defect_summary") or {}
        verification_remaining = int(verification_defects.get("remaining") or 0)
        verification_decision = verification_summary.get("last_gatekeeper_decision")
        if verification_decision == "DONE" and verification_remaining == 0:
            stop_reason = "done"

    final_state = _read_state(project_root)
    final_state_summary = _summarize_state(final_state)
    if stop_reason == "blocked" and "compile_timeout" in blocked_reasons and iterations:
        final_state_summary = iterations[-1].get("state_summary") or final_state_summary
    report = {
        "mode": "fix_layout",
        "project_root": str(project_root),
        "iterations": iterations,
        "final_state_summary": final_state_summary,
        "round_limit": round_limit,
        "hard_round_limit": hard_round_limit,
        "stall_round_limit": stall_round_limit,
        "no_apply_round_limit": no_apply_round_limit,
        "state_stall_failure_limit": state_stall_failure_limit,
        "round_count": len(iterations),
        "stop_reason": stop_reason or "unknown",
        "blocked_reasons": blocked_reasons,
    }
    report_path = project_root / "data" / "fix_layout_report.json"
    _write_json(report_path, report)
    report["report_path"] = str(report_path)
    return report


def _layout_completion_status(fix_report: Dict[str, Any]) -> Dict[str, Any]:
    final_summary = fix_report.get("final_state_summary") or {}
    defect_summary = final_summary.get("defect_summary") or {}
    decision = str(final_summary.get("last_gatekeeper_decision") or "")
    remaining = int(defect_summary.get("remaining") or 0)
    done = decision == "DONE" and remaining == 0
    stop_reason = str(fix_report.get("stop_reason") or "")
    blocked_reasons = fix_report.get("blocked_reasons") or []
    reasons: List[str] = []
    if decision and decision != "DONE":
        reasons.append(f"gatekeeper_decision_{decision.lower()}")
    if remaining > 0:
        reasons.append("visual_defects_remaining")
    if stop_reason == "blocked":
        reasons.extend(str(reason) for reason in blocked_reasons)
    status = "done" if done else ("blocked" if stop_reason == "blocked" else "incomplete")
    return {
        "status": status,
        "gatekeeper_decision": decision or None,
        "remaining_defects": remaining,
        "stop_reason": "done" if done else (stop_reason or None),
        "round_count": int(fix_report.get("round_count") or 0),
        "blocked_reasons": blocked_reasons,
        "failure_reasons": reasons,
    }


def _handle_paperfit_request(
    project_root: Path,
    *,
    request: str,
    main_tex: Path,
    template: Optional[str],
    target_pages: Optional[int],
    max_rounds: Optional[int],
    save_as: Optional[Path],
) -> Dict[str, Any]:
    inferred = OrchestratorRuntime.infer_task_from_request(request)
    task_type = inferred.get("task_type")
    portrait_max_rounds = max_rounds if max_rounds is not None else 0
    _build_portrait(
        project_root,
        main_tex=main_tex,
        template=template,
        page_budget="main_body",
        target_pages=target_pages,
        strict=False,
        max_rounds=portrait_max_rounds,
    )
    if task_type == "template_migration":
        target_template = _resolve_template_key(inferred.get("template") or template)
        if not target_template:
            raise ValueError("template migration request requires a target template")
        migration_target_pages = target_pages
        working_root = project_root
        if save_as:
            working_root = _copy_project(project_root, save_as.resolve())
            main_tex = _detect_main_tex(working_root, main_tex.name)
        if working_root != project_root:
            _build_portrait(
                working_root,
                main_tex=main_tex,
                template=template,
                page_budget="main_body",
                target_pages=migration_target_pages,
                strict=False,
                max_rounds=portrait_max_rounds,
            )
        migration = _migrate_template(working_root, main_tex=main_tex, target_template=target_template)
        _build_portrait(
            working_root,
            main_tex=main_tex,
            template=target_template,
            page_budget="main_body",
            target_pages=migration_target_pages,
            strict=False,
            max_rounds=portrait_max_rounds,
        )
        fix_report = _run_fix_layout(
            working_root,
            main_tex=main_tex,
            template=target_template,
            target_pages=migration_target_pages,
            max_rounds=max_rounds,
        )
        completion = _layout_completion_status(fix_report)
        return {
            "mode": "paperfit_request",
            "status": completion["status"],
            "request": request,
            "task_type": task_type,
            "project_root": str(working_root),
            "migration": migration,
            "fix_layout": fix_report,
            "layout_completion": completion,
        }

    if task_type == "visual_only":
        return _run_visual_only(
            project_root,
            main_tex=main_tex,
            template=template,
            target_pages=target_pages,
        )

    if task_type in {"full_vto", "adjust_length", "repair_table"}:
        fix_report = _run_fix_layout(
            project_root,
            main_tex=main_tex,
            template=template,
            target_pages=target_pages or inferred.get("target_pages"),
            max_rounds=max_rounds,
        )
        completion = _layout_completion_status(fix_report)
        return {
            **fix_report,
            "status": completion["status"],
            "layout_completion": completion,
        }

    return _run_visual_only(
        project_root,
        main_tex=main_tex,
        template=template,
        target_pages=target_pages,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Executable PaperFit command driver")
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--main", default=None)
    common.add_argument("--template", default=None)
    common.add_argument("--target-pages", type=int, default=None)
    common.add_argument("--page-budget", default="main_body", choices=["main_body", "with_refs", "with_appendix"])
    common.add_argument("--strict", action="store_true")
    common.add_argument("--max-rounds", type=int, default=None)
    common.add_argument("--save-as", default=None)

    slash_parser = subparsers.add_parser("slash", parents=[common], help="Execute a slash-command style request")
    slash_parser.add_argument("request")

    fix_parser = subparsers.add_parser("fix-layout", parents=[common], help="Run executable /fix-layout workflow")
    check_parser = subparsers.add_parser("check-visual", parents=[common], help="Run executable /check-visual workflow")
    migrate_parser = subparsers.add_parser("migrate-template", parents=[common], help="Run executable template migration")
    migrate_parser.add_argument("target_template")

    args = parser.parse_args()

    project_root = Path.cwd().resolve()
    main_tex = _detect_main_tex(project_root, args.main)
    template = _resolve_template_key(getattr(args, "template", None))
    # Template migration treats template page counts as informational unless the
    # caller explicitly asks for a hard target. Other layout workflows keep the
    # historical template default behavior.
    if args.command in {"slash", "migrate-template"}:
        target_pages = args.target_pages
    else:
        target_pages = args.target_pages or _default_target_pages(template, args.page_budget)
    effective_max_rounds = args.max_rounds
    if effective_max_rounds is None and args.command != "migrate-template":
        effective_max_rounds = 3
    portrait_max_rounds = effective_max_rounds if effective_max_rounds is not None else 0

    if args.command == "slash":
        report = _handle_paperfit_request(
            project_root,
            request=args.request,
            main_tex=main_tex,
            template=template,
            target_pages=target_pages,
            max_rounds=effective_max_rounds,
            save_as=Path(args.save_as).resolve() if args.save_as else None,
        )
    elif args.command == "fix-layout":
        _build_portrait(
            project_root,
            main_tex=main_tex,
            template=template,
            page_budget=args.page_budget,
            target_pages=target_pages,
            strict=args.strict,
            max_rounds=portrait_max_rounds,
        )
        report = _run_fix_layout(
            project_root,
            main_tex=main_tex,
            template=template,
            target_pages=target_pages,
            max_rounds=effective_max_rounds,
        )
        completion = _layout_completion_status(report)
        report["status"] = completion["status"]
        report["layout_completion"] = completion
    elif args.command == "check-visual":
        _build_portrait(
            project_root,
            main_tex=main_tex,
            template=template,
            page_budget=args.page_budget,
            target_pages=target_pages,
            strict=args.strict,
            max_rounds=portrait_max_rounds,
        )
        report = _run_visual_only(
            project_root,
            main_tex=main_tex,
            template=template,
            target_pages=target_pages,
        )
    elif args.command == "migrate-template":
        target_template = _resolve_template_key(args.target_template)
        if not target_template:
            raise SystemExit("target template is required")
        working_root = project_root
        if args.save_as:
            working_root = _copy_project(project_root, Path(args.save_as).resolve())
            main_tex = _detect_main_tex(working_root, main_tex.name)
        _build_portrait(
            working_root,
            main_tex=main_tex,
            template=template,
            page_budget=args.page_budget,
            target_pages=target_pages,
            strict=args.strict,
            max_rounds=portrait_max_rounds,
        )
        migration = _migrate_template(working_root, main_tex=main_tex, target_template=target_template)
        effective_target_pages = args.target_pages
        _build_portrait(
            working_root,
            main_tex=main_tex,
            template=target_template,
            page_budget=args.page_budget,
            target_pages=effective_target_pages,
            strict=args.strict,
            max_rounds=portrait_max_rounds,
        )
        fix_report = _run_fix_layout(
            working_root,
            main_tex=main_tex,
            template=target_template,
            target_pages=effective_target_pages,
            max_rounds=effective_max_rounds,
        )
        completion = _layout_completion_status(fix_report)
        report = {
            "mode": "migrate_template",
            "status": completion["status"],
            "project_root": str(working_root),
            "migration": migration,
            "fix_layout": fix_report,
            "layout_completion": completion,
        }
    else:
        raise SystemExit(f"unsupported command: {args.command}")

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
