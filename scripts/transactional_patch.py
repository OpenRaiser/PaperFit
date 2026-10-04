#!/usr/bin/env python3
"""
Atomic text write helpers for PaperFit fixers.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional


def atomic_write_text(
    path: str | Path,
    content: str,
    *,
    encoding: str = "utf-8",
    backup_dir: Optional[str | Path] = None,
) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    backup_path = ""
    if backup_dir is not None and target.exists():
        backup_root = Path(backup_dir)
        backup_root.mkdir(parents=True, exist_ok=True)
        backup_name = f"{target.name}.{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.bak"
        backup = backup_root / backup_name
        shutil.copy2(target, backup)
        backup_path = str(backup)

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
        try:
            dir_fd = os.open(str(target.parent), os.O_DIRECTORY)
        except (AttributeError, OSError):
            dir_fd = None
        if dir_fd is not None:
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    return backup_path
