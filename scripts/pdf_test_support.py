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
