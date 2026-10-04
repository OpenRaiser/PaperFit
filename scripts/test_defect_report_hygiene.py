from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.defect_report_builder import build_defect_report


class DefectReportHygieneTest(unittest.TestCase):
    def test_hygiene_findings_become_defects(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            hygiene = base / "hygiene.json"
            output = base / "defects.json"
            hygiene.write_text(
                json.dumps(
                    {
                        "source_file": "main.tex",
                        "findings": [
                            {
                                "family": "unresolved_marker",
                                "severity": "critical",
                                "line": 12,
                                "column": 5,
                                "snippet": "Table ??",
                                "description": "Source pollution token `??` remains",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = build_defect_report(None, None, str(hygiene), str(output))

        self.assertEqual(report["summary"]["total_defects"], 1)
        defect = report["defects"][0]
        self.assertEqual(defect["source"], "source_hygiene_report")
        self.assertEqual(defect["defect_family"], "unresolved_marker")
        self.assertEqual(defect["severity"], "critical")


if __name__ == "__main__":
    unittest.main()
