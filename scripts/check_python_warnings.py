#!/usr/bin/env python3
from __future__ import annotations

import py_compile
import sys
from pathlib import Path


CHECK_DIRS = ("scripts",)


def iter_python_files(repo_root: Path):
    for rel_dir in CHECK_DIRS:
        root = repo_root / rel_dir
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield path


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    failed: list[str] = []
    for path in iter_python_files(repo_root):
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            failed.append(f"{path}: {exc.msg}")

    if failed:
        print("Python compile/warning check failed:", file=sys.stderr)
        for item in failed:
            print(f"  - {item}", file=sys.stderr)
        return 1

    print("Python compile/warning check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
