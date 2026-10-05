from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from PIL import Image

LIB_DIR = Path(__file__).resolve().parent.parent / "lib"
EVAL_DIR = Path(__file__).resolve().parent.parent
for entry in (str(LIB_DIR), str(EVAL_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from revision_pair import (  # noqa: E402
    REVISION_SYSTEM_PROMPT,
    build_hard_constraints,
    build_revision_user_prompt,
    iter_common_cases,
    normalize_revision_result,
    to_v3_payload,
)


PROMPT_KWARGS = dict(
    reference_method="v1",
    candidate_method="v2",
    template_name="AAAI2026",
    profile="hard",
    target_pages=9,
    reference_page_count=9,
    candidate_page_count=8,
    candidate_compile_success=True,
    candidate_render_success=True,
    candidate_page_budget_hit=True,
    candidate_undefined_ref_count=0,
)


class RevisionPromptTest(unittest.TestCase):
    def test_prompt_uses_revision_semantics_not_b0(self) -> None:
        prompt = build_revision_user_prompt(**PROMPT_KWARGS)
        self.assertIn("reference version v1", prompt)
        self.assertIn("candidate version v2", prompt)
        self.assertNotIn("B0", prompt)
        self.assertNotIn("perturbed", prompt)
        self.assertIn("winner: one of reference, candidate, tie", prompt)
        self.assertIn("reference_defects", prompt)

    def test_swap_flips_layout_only(self) -> None:
        swapped = build_revision_user_prompt(**PROMPT_KWARGS, swap_order=True)
        self.assertIn("- LEFT montage: candidate version v2.", swapped)
        self.assertIn("- RIGHT montage: reference version v1.", swapped)
        self.assertIn("compile_success=True", swapped)  # 对称块的新键值格式
        self.assertIn("Reference: page_budget_hit=", swapped)  # 硬约束对称展示

    def test_system_prompt_is_not_the_b0_protocol(self) -> None:
        self.assertIn("two revisions", REVISION_SYSTEM_PROMPT)
        self.assertNotIn("perturbed input", REVISION_SYSTEM_PROMPT)


class WireMappingTest(unittest.TestCase):
    def test_reference_keys_map_to_v3_schema(self) -> None:
        payload = to_v3_payload(
            {
                "reference_defects": [{"defect_type": "overflow", "severity": "major"}],
                "candidate_defects": [],
                "winner": "reference",
            }
        )
        self.assertIn("b0_defects", payload)
        self.assertNotIn("reference_defects", payload)
        self.assertEqual(payload["winner"], "b0")

    def test_normalize_revision_result_reuses_v3_gates(self) -> None:
        revision_payload = {
            "reference_defects": [{"defect_type": "overflow", "severity": "major"}],
            "candidate_defects": [],
            "target_defect_assessment": {},
            "new_defects_introduced": [],
            "scores": {
                "defect_resolution": 4,
                "constraint_alignment": 5,
                "visual_quality": 4,
                "new_defect_avoidance": 4,
                "publication_readiness": 4,
            },
            "final_aesthetic_scores": {
                "overall_professionalism": 4,
                "space_utilization": 4,
                "float_placement_quality": 4,
                "typographic_consistency": 4,
                "visual_balance": 4,
            },
            "repair_outcome": "clearly_improved",
            "winner": "candidate",
            "confidence": 0.8,
            "summary": "candidate resolves overflow",
        }
        result = normalize_revision_result(
            revision_payload,
            hard_constraints={"compile_success": True, "render_success": True},
        )
        self.assertTrue(result.get("revision_pair"))
        self.assertEqual(result["winner"], "candidate")
        self.assertGreater(result["gated_v3_score"], 0)


class CommonCaseSelectionTest(unittest.TestCase):
    def test_common_cases_require_pages_on_both_sides(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for method in ("v1", "v2"):
                case = root / method / "AAAI" / "case-a"
                (case / "pages").mkdir(parents=True)
                Image.new("RGB", (40, 50), "white").save(case / "pages" / "page_001.png")
                (case / "reports").mkdir(parents=True)
                (case / "reports" / "evaluation_report.json").write_text("{}", encoding="utf-8")
            only_v2 = root / "v2" / "AAAI" / "case-b"
            (only_v2 / "pages").mkdir(parents=True)
            Image.new("RGB", (40, 50), "white").save(only_v2 / "pages" / "page_001.png")

            common = iter_common_cases(root / "v1", root / "v2")
            self.assertEqual(common, ["AAAI/case-a"])


class HardConstraintsTest(unittest.TestCase):
    def test_constraints_come_from_candidate_evaluation(self) -> None:
        constraints = build_hard_constraints(
            {"compile_success": True, "render_success": True, "page_budget_hit": False, "undefined_ref_count": 2}
        )
        self.assertEqual(constraints["compile_success"], True)
        self.assertEqual(constraints["page_budget_hit"], False)
        self.assertEqual(constraints["undefined_ref_count"], 2)


class RunnerEndToEndTest(unittest.TestCase):
    def test_run_revision_evaluation_with_fake_client(self) -> None:
        from evaluate_revision_pair import run_revision_evaluation

        canned_response = json.dumps(
            {
                "reference_defects": [{"defect_type": "overflow", "severity": "major"}],
                "candidate_defects": [],
                "target_defect_assessment": {},
                "new_defects_introduced": [],
                "scores": {
                    "defect_resolution": 4,
                    "constraint_alignment": 5,
                    "visual_quality": 4,
                    "new_defect_avoidance": 5,
                    "publication_readiness": 4,
                },
                "final_aesthetic_scores": {
                    "overall_professionalism": 4,
                    "space_utilization": 4,
                    "float_placement_quality": 4,
                    "typographic_consistency": 4,
                    "visual_balance": 4,
                },
                "repair_outcome": "clearly_improved",
                "winner": "candidate",
                "confidence": 0.9,
                "summary": "v2 fixes the overflow that v1 left.",
            }
        )

        class FakeClient:
            def analyze(self, *, images, system_prompt, user_prompt):
                assert "two revisions" in system_prompt
                assert "reference version v1" in user_prompt
                return canned_response, {"prompt_tokens": 1, "completion_tokens": 1}

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for method in ("v1", "v2"):
                case = root / method / "AAAI" / "case-a"
                (case / "pages").mkdir(parents=True)
                Image.new("RGB", (60, 80), "white").save(case / "pages" / "page_001.png")
                (case / "reports").mkdir(parents=True)
                (case / "reports" / "evaluation_report.json").write_text(
                    json.dumps(
                        {
                            "compile_success": True,
                            "render_success": True,
                            "page_budget_hit": True,
                            "undefined_ref_count": 0,
                            "page_count": 9,
                            "target_pages": 9,
                        }
                    ),
                    encoding="utf-8",
                )

            summary = run_revision_evaluation(
                client=FakeClient(),
                reference_root=root / "v1",
                candidate_root=root / "v2",
                reference_method="v1",
                candidate_method="v2",
                output_dir=root / "out",
                case_ids=["AAAI/case-a"],
                case_root=None,
                max_image_dim=1800,
                swap_order=False,
            )

            self.assertEqual(summary["case_count"], 1)
            self.assertEqual(summary["candidate_wins"], 1)
            self.assertGreater(summary["mean_gated_v3_score"], 0)
            report_files = list((root / "out" / "v2").rglob("vlm_report_normal.json"))
            self.assertEqual(len(report_files), 1)
            saved = json.loads(report_files[0].read_text(encoding="utf-8"))
            self.assertEqual(saved["comparison_type"], "revision_pair")
            self.assertEqual(saved["pair_order"], "normal")
            self.assertTrue(saved["vlm"]["revision_pair"])


if __name__ == "__main__":
    unittest.main()
