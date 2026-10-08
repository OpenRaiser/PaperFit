"""Readable PDF fixtures for tests that exercise terminal artifact acceptance."""

from pathlib import Path

import fitz


def write_test_pdf(path: Path, page_count: int = 1) -> None:
    with fitz.open() as document:
        for index in range(page_count):
            page = document.new_page()
            page.insert_text((72, 72), f"Fixture page {index + 1}")
        document.save(path)
