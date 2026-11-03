"""Standalone subset of the evaluator's shared PDF and JSON helpers."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional


def pdf_page_count(pdf_path: Optional[Path]) -> Optional[int]:
    if not pdf_path or not pdf_path.exists():
        return None
    tool = shutil.which("pdfinfo")
    if not tool:
        return None
    # cwd 换到父目录后相对路径会失效，必须用绝对路径调用
    absolute_pdf = pdf_path.resolve()
    try:
        result = subprocess.run(
            [tool, str(absolute_pdf)], cwd=absolute_pdf.parent,
            capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("Pages:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
