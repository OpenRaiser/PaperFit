from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path
from typing import Sequence


def run(module_name: str, argv: Sequence[str] | None = None) -> int:
    root = Path(__file__).resolve().parent
    lib_dir = root / "lib"
    sys.path.insert(0, str(lib_dir))
    module = importlib.import_module(module_name)
    args = list(argv) if argv is not None else sys.argv[1:]
    main = module.main
    if not inspect.signature(main).parameters:
        old_argv = sys.argv
        try:
            sys.argv = [old_argv[0], *args]
            return int(main())
        finally:
            sys.argv = old_argv
    return int(main(args))
