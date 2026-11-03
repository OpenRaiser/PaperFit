"""Readable PDF/image fixtures for tests that exercise terminal artifact acceptance."""

from pathlib import Path

import fitz
from PIL import Image


def write_test_pdf(path: Path, page_count: int = 1) -> None:
    with fitz.open() as document:
        for index in range(page_count):
            page = document.new_page()
            page.insert_text((72, 72), f"Fixture page {index + 1}")
        document.save(path)


def write_test_png(path: Path) -> None:
    Image.new("RGB", (16, 16), "white").save(path, format="PNG")


def synthetic_native_compile(project_root: Path, *, main_tex: Path, **kwargs: object) -> dict:
    """Exercise real native receipt/binding code with explicit synthetic output.

    No TeX process runs and this fixture establishes no scientific or visual
    acceptance. The mocked process writes a readable PDF and a recorder of the
    actual source bytes; NativeCompileEvidence and verify_native_build run normally.
    """
    import os
    import subprocess
    from unittest import mock

    import runtime_actions
    from compile_inputs import verify_native_build

    root = project_root.resolve()
    main = (main_tex if main_tex.is_absolute() else root / main_tex).resolve()

    def compiler_output(command: list[str], **_: object) -> subprocess.CompletedProcess:
        write_test_pdf(main.with_suffix(".pdf"))
        main.with_suffix(".fls").write_text(
            f"PWD {main.parent}\nINPUT {main.name}\n", encoding="utf-8"
        )
        return subprocess.CompletedProcess(
            command, 0, f"Synthetic fixture: Output written on {main.stem}.pdf (1 page).", ""
        )

    with (
        mock.patch.dict(os.environ, {"PAPERFIT_BOUNDED_COMPILE": "0"}),
        mock.patch.object(runtime_actions, "run_command", side_effect=compiler_output),
        mock.patch.object(runtime_actions, "pdf_page_count", return_value=1),
    ):
        result = runtime_actions.compile_latex(
            root, main_tex=main, engine="pdflatex", tolerant=bool(kwargs.get("tolerant"))
        )
    binding = verify_native_build(main, root=root)
    if not result.get("current_source_compile_success") or binding.get("status") != "current":
        raise AssertionError({"synthetic_compile": result, "native_binding": binding})
    return result
