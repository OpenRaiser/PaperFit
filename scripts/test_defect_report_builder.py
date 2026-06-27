from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from defect_report_builder import _normalize_visual_report  # noqa: E402


class DefectReportBuilderTest(unittest.TestCase):
    def test_priority_object_b2_is_not_duplicated_when_finding_has_same_bbox(self) -> None:
        defects = _normalize_visual_report(
            {
                "findings": [
                    {
                        "source": "pymupdf_native",
                        "page": 2,
                        "taxonomy_defect_id": "B2",
                        "defect_id": "B2-native-underfilled-width",
                        "category": "B",
                        "severity": "major",
                        "confidence": 0.88,
                        "description": "native figure_like uses 0.350 of inferred column width",
                        "bbox": [402, 245, 657, 409],
                        "metrics": {"subtype": "underfilled_width"},
                    }
                ],
                "priority_objects": [
                    {
                        "page": 2,
                        "object_kind": "figure_like",
                        "bbox": [402, 245, 657, 409],
                        "reason": "low_width_ratio:0.350",
                        "object_width_ratio": 0.35,
                    }
                ],
            }
        )

        self.assertEqual(len([d for d in defects if d["defect_family"] == "B2"]), 1)
        self.assertEqual(defects[0]["source"], "pymupdf_native")


if __name__ == "__main__":
    unittest.main()
