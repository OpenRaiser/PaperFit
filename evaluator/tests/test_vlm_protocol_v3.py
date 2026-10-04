import unittest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import vlm_protocol as v3


class VLMProtocolV3Tests(unittest.TestCase):
    def _strong_repair_payload(self) -> dict:
        return {
            "scores": {
                "defect_resolution": 5,
                "constraint_alignment": 5,
                "visual_quality": 5,
                "new_defect_avoidance": 5,
                "publication_readiness": 5,
            },
            "final_aesthetic_scores": {
                "overall_professionalism": 5,
                "space_utilization": 5,
                "float_placement_quality": 5,
                "typographic_consistency": 5,
                "visual_balance": 5,
            },
            "repair_outcome": "fully_fixed",
            "repair_score": 3,
            "winner": "candidate",
            "confidence": 0.9,
            "summary": "The candidate visibly fixes the target layout defects.",
        }

    def test_render_failure_remains_fatal(self) -> None:
        normalized = v3.normalize_v3_result(
            self._strong_repair_payload(),
            hard_constraints={
                "compile_success": False,
                "render_success": False,
            },
        )

        self.assertEqual(normalized["raw_v3_score"], 5.0)
        self.assertEqual(normalized["gated_v3_score"], 1.0)
        self.assertIn("compile_success is false", normalized["constraint_gate_reasons"])
        self.assertIn("render_success is false", normalized["constraint_gate_reasons"])

    def test_compile_dirty_renderable_is_penalized_not_fatal(self) -> None:
        normalized = v3.normalize_v3_result(
            self._strong_repair_payload(),
            hard_constraints={
                "compile_success": False,
                "render_success": True,
            },
        )

        self.assertEqual(normalized["raw_v3_score"], 5.0)
        self.assertAlmostEqual(normalized["gated_v3_score"], 3.22)
        self.assertIn("compile_success is false", normalized["constraint_gate_reasons"])
        self.assertNotIn("render_success is false", normalized["constraint_gate_reasons"])

    def test_final_aesthetic_score_is_part_of_raw_v3_score(self) -> None:
        payload = self._strong_repair_payload()
        payload["final_aesthetic_scores"] = {
            "overall_professionalism": 1,
            "space_utilization": 1,
            "float_placement_quality": 1,
            "typographic_consistency": 1,
            "visual_balance": 1,
        }

        normalized = v3.normalize_v3_result(
            payload,
            hard_constraints={
                "compile_success": True,
                "render_success": True,
            },
        )

        self.assertEqual(normalized["v3_absolute_score"], 5.0)
        self.assertEqual(normalized["pairwise_repair_score_5pt"], 5.0)
        self.assertEqual(normalized["final_aesthetic_score"], 1.0)
        self.assertAlmostEqual(normalized["raw_v3_score"], 4.0)


if __name__ == "__main__":
    unittest.main()
