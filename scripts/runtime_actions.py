#!/usr/bin/env python3
"""Runtime-owned executable actions for PaperFit task execution."""

from __future__ import annotations

import json
import locale
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from tex_engine import ENGINES, resolve_tex_engine
    from diagnosis_ingest import classify_compile_case
except ModuleNotFoundError:  # package import during unit tests
    from .tex_engine import ENGINES, resolve_tex_engine
    from .diagnosis_ingest import classify_compile_case


def collect_runtime_environment() -> Dict[str, Any]:
    """Cheap, deterministic environment snapshot for run-result reproducibility.

    Only PATH probing (shutil.which) — no tool execution — so it is safe to
    attach to every run result.
    """
    return {
        "schema_version": "1.0",
        "captured_at": datetime.now().isoformat(),
        "python_version": sys.version.split()[0],
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "toolchain": {
            name: bool(shutil.which(name))
            for name in ("latexmk", "pdflatex", "bibtex", "pdfinfo", "pdftotext")
        },
    }


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


def terminate_process_tree(process: subprocess.Popen) -> None:
    """Cross-platform termination for a timed-out subprocess and its children."""
    if os.name == "nt":
        # os.killpg does not exist on Windows; taskkill /T walks the child tree.
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            capture_output=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, AttributeError):
        process.terminate()


def kill_process_tree(process: subprocess.Popen) -> None:
    """Force-kill variant of terminate_process_tree."""
    if os.name == "nt":
        terminate_process_tree(process)
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, AttributeError):
        process.kill()


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
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        terminate_process_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            kill_process_tree(process)
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
    main_path = main_tex if main_tex.is_absolute() else project_root / main_tex
    for suffix in [
        ".aux",
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
            main_path.with_suffix(suffix).unlink()
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


def compile_latex(project_root: Path, *, main_tex: Path, tolerant: bool = False, engine: Optional[str] = None) -> Dict[str, Any]:
    """Compile without changing manuscript source.

    Nonstop mode can produce a diagnostic preview after an error. That preview
    does not establish successful compilation of the current source.
    """
    project_root = project_root.resolve()
    main_tex = (main_tex if main_tex.is_absolute() else project_root / main_tex).resolve()
    build_root = main_tex.parent
    engine = resolve_tex_engine(main_tex, engine)
    main_relative = main_tex.relative_to(project_root).as_posix()
    timeout_sec = int(os.environ.get("PAPERFIT_COMPILE_TIMEOUT_SEC", "240"))
    if os.environ.get("PAPERFIT_BOUNDED_COMPILE") == "1":
        bounded = compile_latex_bounded(project_root, main_tex=main_tex, timeout_sec=timeout_sec, engine=engine)
        if bounded.get("available"):
            return bounded

    try:
        from compile_inputs import NativeCompileEvidence
    except ModuleNotFoundError:
        from .compile_inputs import NativeCompileEvidence
    evidence = NativeCompileEvidence(project_root, main_tex, compiler="latexmk")
    cmd = ["latexmk", ENGINES[engine], "-interaction=nonstopmode", "-recorder"]
    if not tolerant:
        cmd.append("-halt-on-error")
    cmd.append(main_tex.name)
    pdf_path = main_tex.with_suffix(".pdf")
    # P2：编译前保存 baseline PDF。编译失败时恢复——绝不删除唯一修前 PDF，
    # 评估器要能读到修前状态
    baseline_pdf = project_root / "data" / f"baseline_{main_tex.stem}.pdf"
    had_baseline = pdf_path.is_file()
    if had_baseline:
        baseline_pdf.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pdf_path, baseline_pdf)
    clean_main_build_artifacts(project_root, main_tex=main_tex)
    try:
        pdf_path.unlink()
    except FileNotFoundError:
        pass

    result = run_command(cmd, cwd=build_root, timeout=timeout_sec)
    restored_baseline = False
    if not pdf_path.is_file() and had_baseline and baseline_pdf.is_file():
        shutil.copy2(baseline_pdf, pdf_path)
        restored_baseline = True
    compile_log = project_root / "data" / "logs" / "paperfit_compile.log"
    _mkdir_for_file(compile_log)
    compile_log.write_text(
        (result.stdout or "") + ("\n" + result.stderr if result.stderr else ""),
        encoding="utf-8",
    )
    timed_out = result.returncode == 124
    combined_log = (result.stdout or "") + "\n" + (result.stderr or "")
    fatal_patterns = [
        "Fatal error occurred", "Emergency stop", "! LaTeX Error:",
        " ==> Fatal error", "Undefined control sequence",
    ]
    fatal_error = any(pattern in combined_log for pattern in fatal_patterns) or bool(
        re.search(r"^! ", combined_log, re.MULTILINE)
    )
    pdf_fresh = pdf_path.is_file() and not restored_baseline
    partial_pdf_available = bool(timed_out and pdf_fresh and not fatal_error)
    success = bool(result.returncode == 0 and pdf_fresh and not fatal_error)
    if tolerant and success:
        success = (pdf_page_count(pdf_path) or 0) > 0
    preview_pdf_path = str(pdf_path) if pdf_path.is_file() and not success else None

    case = classify_compile_case(combined_log, success=success)

    report = {
        "success": success,
        "engine": engine,
        "tolerant": tolerant,
        "restored_baseline_pdf": restored_baseline,
        "baseline_pdf": str(baseline_pdf) if had_baseline else None,
        "command": cmd,
        "cwd": str(build_root),
        "returncode": result.returncode,
        "timeout": timed_out,
        "timeout_sec": timeout_sec,
        "partial_pdf_available": partial_pdf_available,
        "pdf_fresh": pdf_fresh,
        "preview_pdf_path": preview_pdf_path,
        "fatal_error": fatal_error,
        "case_status": case["case_status"],
        "missing_sty": case["missing_sty"],
        "missing_images": case["missing_images"],
        "stdout_tail": (result.stdout or "")[-4000:],
        "stderr_tail": (result.stderr or "")[-4000:],
        "log_file": main_tex.with_suffix(".log").relative_to(project_root).as_posix(),
        "pdf_path": str(pdf_path) if success else None,
        "compile_log": str(compile_log),
    }
    return evidence.finalize(report, page_count=pdf_page_count(pdf_path) if pdf_path.is_file() else None,
                             stdout=result.stdout or '', stderr=result.stderr or '')


def compile_latex_bounded(project_root: Path, *, main_tex: Path, timeout_sec: int, engine: Optional[str] = None) -> Dict[str, Any]:
    project_root = project_root.resolve()
    main_tex = (main_tex if main_tex.is_absolute() else project_root / main_tex).resolve()
    build_root = main_tex.parent
    engine = resolve_tex_engine(main_tex, engine)
    pdflatex = shutil.which(engine)
    if not pdflatex:
        return {"available": False}
    try:
        from compile_inputs import NativeCompileEvidence
    except ModuleNotFoundError:
        from .compile_inputs import NativeCompileEvidence
    evidence = NativeCompileEvidence(project_root, main_tex, compiler=engine)
    pdf_path = main_tex.with_suffix(".pdf")
    good_pdf_path = main_tex.with_suffix(".paperfit-good.pdf")
    baseline_pdf = evidence.attempt.attempt_dir / "baseline.pdf"
    had_baseline = pdf_path.is_file()
    if had_baseline:
        shutil.copy2(pdf_path, baseline_pdf)
    clean_main_build_artifacts(project_root, main_tex=main_tex)
    for path in (pdf_path, good_pdf_path):
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    pass_timeout = max(60, min(timeout_sec, 240))
    passes: List[Dict[str, Any]] = []
    full_stdout: List[str] = []
    full_stderr: List[str] = []
    timed_out = False
    fatal_error = False
    restored_good_pdf = False
    have_good_pdf = False

    def _record(result: subprocess.CompletedProcess[str]) -> None:
        nonlocal timed_out, fatal_error
        full_stdout.append(result.stdout or '')
        full_stderr.append(result.stderr or '')
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
            cwd=build_root,
            timeout=pass_timeout,
        )

    result = _run_pdflatex()
    _record(result)
    have_good_pdf = _snapshot_if_readable()

    aux_path = main_tex.with_suffix(".aux")
    bibtex = shutil.which("bibtex")
    if aux_path.exists() and bibtex:
        aux_text = aux_path.read_text(encoding="utf-8", errors="replace")
        if r"\bibdata{" in aux_text:
            result = run_command([bibtex, main_tex.stem], cwd=build_root, timeout=max(60, min(timeout_sec, 180)))
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

    # Preserve a readable failed preview; restore the pre-attempt PDF only
    # when this attempt left no readable artifact. Its archive survives.
    restored_baseline_pdf = False
    if pdf_page_count(pdf_path) is None and had_baseline:
        shutil.copy2(baseline_pdf, pdf_path)
        restored_baseline_pdf = True

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
    success = bool(page_count is not None and page_count > 0 and not fatal_error
                   and not timed_out and not restored_good_pdf and not restored_baseline_pdf
                   and passes and all(item.get("returncode") == 0 for item in passes))
    combined_log = "\n".join(
        (item.get("stdout_tail") or "")
        + ("\n" + str(item.get("stderr_tail") or "") if item.get("stderr_tail") else "")
        for item in passes
    )
    case = classify_compile_case(combined_log, success=success)
    report = {
        "available": True,
        "success": success,
        "engine": engine,
        "command": [item.get("command") for item in passes],
        "cwd": str(build_root),
        "returncode": 0 if success else (124 if timed_out else int((passes[-1] if passes else {}).get("returncode") or 1)),
        "timeout": timed_out,
        "timeout_sec": timeout_sec,
        "bounded_compile": True,
        "restored_good_pdf": restored_good_pdf,
        "restored_baseline_pdf": restored_baseline_pdf,
        "baseline_pdf": str(baseline_pdf) if had_baseline else None,
        "partial_pdf_available": bool(timed_out and page_count and not fatal_error and not restored_good_pdf and not restored_baseline_pdf),
        "pdf_fresh": bool(page_count and not restored_good_pdf and not restored_baseline_pdf),
        "preview_pdf_path": str(pdf_path) if page_count and not success else None,
        "fatal_error": fatal_error,
        "case_status": case["case_status"],
        "missing_sty": case["missing_sty"],
        "missing_images": case["missing_images"],
        "passes": passes,
        "stdout_tail": "\n".join(str(item.get("stdout_tail") or "") for item in passes)[-4000:],
        "stderr_tail": "\n".join(str(item.get("stderr_tail") or "") for item in passes)[-4000:],
        "log_file": main_tex.with_suffix(".log").relative_to(project_root).as_posix(),
        "pdf_path": str(pdf_path) if success else None,
        "compile_log": str(compile_log),
    }

    return evidence.finalize(report, page_count=page_count,
                             stdout='\n'.join(full_stdout), stderr='\n'.join(full_stderr))


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
