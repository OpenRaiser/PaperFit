"""Select a manuscript compiler without changing its template or host model."""
from __future__ import annotations
import os
import re
from pathlib import Path
ENGINES = {"pdflatex": "-pdf", "xelatex": "-xelatex", "lualatex": "-lualatex"}

def resolve_tex_engine(main_tex: Path, explicit: str | None = None) -> str:
    """Explicit setting, process setting, root magic comment, then pdfLaTeX."""
    value = explicit or os.environ.get("PAPERFIT_TEX_ENGINE")
    if not value:
        for line in main_tex.read_text(encoding="utf-8", errors="replace").splitlines()[:20]:
            match = re.fullmatch(r"\s*%\s*!\s*TeX\s+(?:program|TS-program)\s*=\s*([\w-]+)\s*", line, re.I)
            if match:
                value = match[1]
                break
    engine = (value or "pdflatex").strip().lower()
    if engine not in ENGINES:
        raise ValueError(f"Unsupported TeX engine: {engine!r}; use pdflatex, xelatex or lualatex")
    return engine
