from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from scripts.runtime_artifacts import collect_artifact_manifest


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
            pdf.write_bytes(b"pdf")
            page = pages / "page_001.png"
            page.write_bytes(b"png")
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


if __name__ == "__main__":
    unittest.main()
