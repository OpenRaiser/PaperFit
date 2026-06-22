#!/usr/bin/env python3
"""Runtime-owned executable actions for PaperFit task execution."""

from __future__ import annotations

import locale
import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


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
    return blob.decode("utf-8", errors="replace")


def run_command(
    cmd: List[str],
    *,
    cwd: Path,
    timeout: Optional[int] = None,
) -> subprocess.CompletedProcess[str]:
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
        stderr_text = (stderr_text + f"\nTIMEOUT after {timeout}s").strip()
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


def _mkdir_for_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def clean_main_build_artifacts(project_root: Path, *, main_tex: Path) -> None:
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


def pdf_page_count(pdf_path: Path) -> Optional[int]:
    if not pdf_path.exists():
        return None
    result = run_command(["pdfinfo", str(pdf_path)], cwd=pdf_path.parent, timeout=30)
    if result.returncode != 0:
        return None
    for line in (result.stdout or "").splitlines():
        if line.startswith("Pages:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def compile_latex(project_root: Path, *, main_tex: Path) -> Dict[str, Any]:
    timeout_sec = int(os.environ.get("PAPERFIT_COMPILE_TIMEOUT_SEC", "240"))
    if os.environ.get("PAPERFIT_BOUNDED_COMPILE") == "1":
        bounded = compile_latex_bounded(project_root, main_tex=main_tex, timeout_sec=timeout_sec)
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
    clean_main_build_artifacts(project_root, main_tex=main_tex)
    try:
        pdf_path.unlink()
    except FileNotFoundError:
        pass

    result = run_command(cmd, cwd=project_root, timeout=timeout_sec)
    compile_log = project_root / "data" / "logs" / "paperfit_compile.log"
    _mkdir_for_file(compile_log)
    compile_log.write_text(
        (result.stdout or "") + ("\n" + result.stderr if result.stderr else ""),
        encoding="utf-8",
    )
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


def compile_latex_bounded(project_root: Path, *, main_tex: Path, timeout_sec: int) -> Dict[str, Any]:
    pdflatex = shutil.which("pdflatex")
    if not pdflatex:
        return {"available": False}
    pdf_path = project_root / f"{main_tex.stem}.pdf"
    good_pdf_path = project_root / f"{main_tex.stem}.paperfit-good.pdf"
    clean_main_build_artifacts(project_root, main_tex=main_tex)
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
        if pdf_page_count(pdf_path) is None:
            return False
        shutil.copy2(pdf_path, good_pdf_path)
        return True

    def _run_pdflatex() -> subprocess.CompletedProcess[str]:
        return run_command(
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
            result = run_command([bibtex, main_tex.stem], cwd=project_root, timeout=max(60, min(timeout_sec, 180)))
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

    if have_good_pdf and pdf_page_count(pdf_path) is None:
        shutil.copy2(good_pdf_path, pdf_path)
        restored_good_pdf = True

    try:
        good_pdf_path.unlink()
    except FileNotFoundError:
        pass

    compile_log = project_root / "data" / "logs" / "paperfit_compile.log"
    _mkdir_for_file(compile_log)
    compile_log.write_text(
        "\n".join(
            (item.get("stdout_tail") or "")
            + ("\n" + str(item.get("stderr_tail") or "") if item.get("stderr_tail") else "")
            for item in passes
        ),
        encoding="utf-8",
    )
    page_count = pdf_page_count(pdf_path)
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


def render_pdf_pages(project_root: Path, *, pdf_path: Path, output_dir: str = "data/pages") -> Dict[str, Any]:
    cmd = [
        sys.executable,
        str(package_root() / "scripts" / "render_pages.py"),
        str(pdf_path),
        "--output",
        output_dir,
        "--dpi",
        "220",
    ]
    result = run_command(cmd, cwd=project_root)
    return {
        "success": result.returncode == 0,
        "command": cmd,
        "returncode": result.returncode,
        "stdout_tail": (result.stdout or "")[-4000:],
        "stderr_tail": (result.stderr or "")[-4000:],
        "page_dir": output_dir,
    }


def extract_pdf_pages_text(pdf_path: Path) -> List[str]:
    result = run_command(["pdftotext", "-layout", str(pdf_path), "-"], cwd=pdf_path.parent)
    if result.returncode != 0:
        return []
    pages = (result.stdout or "").split("\f")
    while pages and not pages[-1].strip():
        pages.pop()
    return pages


def inspect_endmatter_float_intrusion(pdf_path: Path) -> Dict[str, Any]:
    pages = extract_pdf_pages_text(pdf_path)
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
