from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from scripts.runtime_artifacts import collect_artifact_manifest
from scripts.pdf_test_support import write_test_pdf, write_test_png


class RuntimeArtifactsTest(unittest.TestCase):
    def test_detects_page_images_older_than_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            main = root / "main.tex"
            pdf = root / "main.pdf"
            pages = root / "data" / "pages"
            pages.mkdir(parents=True)
            visual = root / "data" / "visual_signal_report.json"
            defects = root / "data" / "defect_report.json"
            gatekeeper = root / "data" / "gatekeeper_decision.json"

            main.write_text("tex", encoding="utf-8")
            write_test_pdf(pdf)
            page = pages / "page_001.png"
            write_test_png(page)
            visual.write_text("{}", encoding="utf-8")
            defects.write_text("{}", encoding="utf-8")
            gatekeeper.write_text("{}", encoding="utf-8")

            old = 1_700_000_000
            new = 1_700_000_100
            os.utime(page, (old, old))
            os.utime(pdf, (new, new))
            os.utime(visual, (new + 1, new + 1))
            os.utime(defects, (new + 2, new + 2))
            os.utime(gatekeeper, (new + 3, new + 3))

            manifest = collect_artifact_manifest(
                project_root=root,
                main_tex="main.tex",
                artifacts={
                    "page_images_dir": "data/pages",
                    "visual_signal_report": "data/visual_signal_report.json",
                    "defect_report": "data/defect_report.json",
                    "gatekeeper_decision": "data/gatekeeper_decision.json",
                },
            )

            self.assertEqual(manifest["freshness"]["status"], "stale_or_missing")
            self.assertIn(
                "page_images_not_older_than_pdf",
                manifest["freshness"]["blocking_checks"],
            )

    def test_pdf_older_than_source_set_blocks_freshness(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            main = root / "main.tex"
            pdf = root / "main.pdf"
            pages = root / "data" / "pages"
            pages.mkdir(parents=True)
            visual = root / "data" / "visual_signal_report.json"
            defects = root / "data" / "defect_report.json"
            gatekeeper = root / "data" / "gatekeeper_decision.json"

            main.write_text("tex", encoding="utf-8")
            write_test_pdf(pdf)
            page = pages / "page_001.png"
            write_test_png(page)
            visual.write_text("{}", encoding="utf-8")
            defects.write_text("{}", encoding="utf-8")
            gatekeeper.write_text("{}", encoding="utf-8")

            base = 1_700_000_000
            os.utime(pdf, (base, base))
            os.utime(page, (base + 1, base + 1))
            os.utime(visual, (base + 2, base + 2))
            os.utime(defects, (base + 3, base + 3))
            os.utime(gatekeeper, (base + 4, base + 4))
            # Source edited after the PDF was produced.
            os.utime(main, (base + 5, base + 5))

            manifest = collect_artifact_manifest(
                project_root=root,
                main_tex="main.tex",
                artifacts={
                    "page_images_dir": "data/pages",
                    "visual_signal_report": "data/visual_signal_report.json",
                    "defect_report": "data/defect_report.json",
                    "gatekeeper_decision": "data/gatekeeper_decision.json",
                },
            )

        self.assertEqual(manifest["freshness"]["status"], "stale_or_missing")
        self.assertIn(
            "pdf_not_older_than_source_set",
            manifest["freshness"]["blocking_checks"],
        )

    def test_fresh_chain_with_source_set_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            main = root / "main.tex"
            pdf = root / "main.pdf"
            pages = root / "data" / "pages"
            pages.mkdir(parents=True)
            visual = root / "data" / "visual_signal_report.json"
            defects = root / "data" / "defect_report.json"
            gatekeeper = root / "data" / "gatekeeper_decision.json"

            main.write_text("tex", encoding="utf-8")
            write_test_pdf(pdf)
            page = pages / "page_001.png"
            write_test_png(page)
            visual.write_text("{}", encoding="utf-8")
            defects.write_text("{}", encoding="utf-8")
            gatekeeper.write_text("{}", encoding="utf-8")

            base = 1_700_000_000
            os.utime(main, (base, base))
            os.utime(pdf, (base + 1, base + 1))
            os.utime(page, (base + 2, base + 2))
            os.utime(visual, (base + 3, base + 3))
            os.utime(defects, (base + 4, base + 4))
            os.utime(gatekeeper, (base + 5, base + 5))

            manifest = collect_artifact_manifest(
                project_root=root,
                main_tex="main.tex",
                artifacts={
                    "page_images_dir": "data/pages",
                    "visual_signal_report": "data/visual_signal_report.json",
                    "defect_report": "data/defect_report.json",
                    "gatekeeper_decision": "data/gatekeeper_decision.json",
                },
            )

        self.assertEqual(manifest["freshness"]["status"], "pass")
        self.assertEqual(manifest["freshness"]["blocking_checks"], [])
        self.assertGreaterEqual(manifest["artifacts"]["source_set"]["count"], 1)

    def test_edited_input_closure_file_invalidates_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sections = root / "sections"
            sections.mkdir()
            main = root / "main.tex"
            intro = sections / "intro.tex"
            pdf = root / "main.pdf"
            pages = root / "data" / "pages"
            pages.mkdir(parents=True)
            visual = root / "data" / "visual_signal_report.json"
            defects = root / "data" / "defect_report.json"
            gatekeeper = root / "data" / "gatekeeper_decision.json"

            main.write_text("\\input{sections/intro}\n", encoding="utf-8")
            intro.write_text("body", encoding="utf-8")
            write_test_pdf(pdf)
            page = pages / "page_001.png"
            write_test_png(page)
            visual.write_text("{}", encoding="utf-8")
            defects.write_text("{}", encoding="utf-8")
            gatekeeper.write_text("{}", encoding="utf-8")

            base = 1_700_000_000
            os.utime(main, (base, base))
            os.utime(intro, (base, base))
            os.utime(pdf, (base + 1, base + 1))
            os.utime(page, (base + 2, base + 2))
            os.utime(visual, (base + 3, base + 3))
            os.utime(defects, (base + 4, base + 4))
            os.utime(gatekeeper, (base + 5, base + 5))
            # An included file is edited after the PDF was produced.
            os.utime(intro, (base + 6, base + 6))

            manifest = collect_artifact_manifest(
                project_root=root,
                main_tex="main.tex",
                artifacts={
                    "page_images_dir": "data/pages",
                    "visual_signal_report": "data/visual_signal_report.json",
                    "defect_report": "data/defect_report.json",
                    "gatekeeper_decision": "data/gatekeeper_decision.json",
                },
            )

        self.assertEqual(manifest["freshness"]["status"], "stale_or_missing")
        self.assertIn(
            "pdf_not_older_than_source_set",
            manifest["freshness"]["blocking_checks"],
        )
        closure_paths = [
            item["path"].replace("\\", "/") for item in manifest["artifacts"]["source_set"]["files"]
        ]
        self.assertIn("sections/intro.tex", closure_paths)


if __name__ == "__main__":
    unittest.main()
