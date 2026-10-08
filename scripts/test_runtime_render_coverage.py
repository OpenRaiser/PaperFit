from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

import fitz

from scripts.runtime_artifacts import collect_artifact_manifest

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
from orchestrator_runtime import OrchestratorRuntime


class RuntimeRenderCoverageTest(unittest.TestCase):
    def _manifest(self, root, names, *, readable=True):
        pages = root / "data/pages"
        pages.mkdir(parents=True)
        main = root / "main.tex"
        main.write_text("\\documentclass{article}\n\\begin{document}Paper\\end{document}", encoding="utf-8")
        pdf = root / "main.pdf"
        if readable:
            with fitz.open() as document:
                for index in range(3):
                    page = document.new_page()
                    page.insert_text((72, 72), f"Real PDF page {index + 1}")
                document.save(pdf)
                for name in names:
                    document[0].get_pixmap().save(pages / name)
        else:
            pdf.write_bytes(b"not a readable PDF")
            for name in names:
                (pages / name).write_bytes(b"png fixture")
        reports = {
            "visual_signal_report": {"summary": {"pages_analyzed": len(names)}, "vlm_diagnosis": {
                "configured": True, "status": "completed", "coverage": 1.0, "clean": True,
            }},
            "defect_report": {"defects": []},
            "gatekeeper_decision": {"decision": "DONE"},
        }
        artifacts = {"page_images_dir": "data/pages"}
        os.utime(main, (1700000000, 1700000000))
        os.utime(pdf, (1700000001, 1700000001))
        for name in names:
            os.utime(pages / name, (1700000002, 1700000002))
        for index, (key, payload) in enumerate(reports.items(), start=3):
            path = root / "data" / f"{key}.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            os.utime(path, (1700000000 + index, 1700000000 + index))
            artifacts[key] = path.relative_to(root).as_posix()
        return collect_artifact_manifest(root, "main.tex", artifacts)

    def test_three_page_pdf_requires_all_pages_even_if_vlm_claims_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self._manifest(Path(tmp), ["page_001.png"])
            failure = OrchestratorRuntime(state_path=str(Path(tmp) / "data/state.json"))._terminal_visual_evidence_failure(
                decision="DONE", artifact_manifest=manifest,
            )
        self.assertEqual(manifest["artifacts"]["pdf"]["page_count"], 3)
        self.assertEqual(manifest["vision_diagnosis"]["status"], "completed")
        self.assertEqual(manifest["freshness"]["status"], "stale_or_missing")
        self.assertIn("page_images_complete", manifest["freshness"]["blocking_checks"])
        self.assertEqual(failure["failure_type"], "terminal_success_without_fresh_visual_evidence")

    def test_only_exact_contiguous_numeric_page_sequence_passes(self):
        for names in (
            ["page_001.png", "page_002.png"],
            ["page_001.png", "page_003.png"],
            ["page_001.png", "page_002.png", "page_004.png"],
            ["page_001.png", "page_002.png", "page_003.png", "page_004.png"],
            ["page_001.png", "page_1.png", "page_002.png"],
            ["page_000.png", "page_001.png", "page_002.png"],
            ["page_001.png", "page_002.png", "page_final.png"],
        ):
            with self.subTest(names=names), tempfile.TemporaryDirectory() as tmp:
                manifest = self._manifest(Path(tmp), names)
                self.assertFalse(manifest["freshness"]["checks"]["page_images_complete"]["pass"])
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self._manifest(Path(tmp), ["page_003.png", "page_001.png", "page_002.png"])
            self.assertEqual(manifest["freshness"]["status"], "pass")
            self.assertEqual(manifest["artifacts"]["page_images"]["page_numbers"], [1, 2, 3])

    def test_unreadable_pdf_cannot_establish_complete_rendering(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self._manifest(Path(tmp), ["page_001.png"], readable=False)
        self.assertIsNone(manifest["artifacts"]["pdf"]["page_count"])
        self.assertIn("pdf_page_count_available", manifest["freshness"]["blocking_checks"])
        self.assertIn("page_images_complete", manifest["freshness"]["blocking_checks"])


if __name__ == "__main__":
    unittest.main()
