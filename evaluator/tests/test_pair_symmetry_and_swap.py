from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent.parent / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from pair_symmetry import reconcile_pair_runs  # noqa: E402
from vlm_protocol import build_v3_user_prompt  # noqa: E402


PROMPT_KWARGS = dict(
    method_id="OURS",
    template_name="AAAI2026",
    profile="hard",
    target_pages=9,
    requested_perturbations=["D1_long_token_overflow"],
    b0_page_count=9,
    candidate_page_count=9,
    candidate_compile_success=True,
    candidate_render_success=True,
    candidate_page_budget_hit=True,
    candidate_undefined_ref_count=0,
)


class SwapPromptTest(unittest.TestCase):
    def test_default_order_matches_official_protocol(self) -> None:
        prompt = build_v3_user_prompt(**PROMPT_KWARGS)
        self.assertIn("- LEFT montage: B0 perturbed input.", prompt)
        self.assertIn("- RIGHT montage: candidate output from method OURS.", prompt)

    def test_swapped_order_flips_layout_lines_only(self) -> None:
        prompt = build_v3_user_prompt(**PROMPT_KWARGS, swap_order=True)
        self.assertIn("- LEFT montage: candidate output from method OURS.", prompt)
        self.assertIn("- RIGHT montage: B0 perturbed input.", prompt)
        # 角色元数据不受换位影响
        self.assertIn("compile_success: True", prompt)


class SwapImageTest(unittest.TestCase):
    def test_swapped_image_moves_the_divider(self) -> None:
        from PIL import Image

        from run_vlm_evaluation import _labeled_comparison_image

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            b0_dir = root / "b0" / "pages"
            cand_dir = root / "cand" / "pages"
            b0_dir.mkdir(parents=True)
            cand_dir.mkdir(parents=True)
            Image.new("RGB", (100, 140), "white").save(b0_dir / "page_001.png")
            Image.new("RGB", (220, 140), "white").save(cand_dir / "page_001.png")

            def divider_x(img) -> int:
                # 在正文行扫描宽度>=3 的暗色竖线（分隔线 width=3）
                row = img.convert("L").crop((0, 100, img.width, 101))
                dark_runs = []
                run = 0
                for x, value in enumerate(row.getdata()):
                    if value < 100:
                        run += 1
                    else:
                        if run >= 3:
                            dark_runs.append(x - run)
                        run = 0
                if run >= 3:
                    dark_runs.append(img.width - run)
                self.assertEqual(len(dark_runs), 1, dark_runs)
                return dark_runs[0]

            normal = _labeled_comparison_image(
                b0_pages_dir=b0_dir,
                candidate_pages_dir=cand_dir,
                candidate_method="OURS",
                max_dim=1800,
            )
            swapped = _labeled_comparison_image(
                b0_pages_dir=b0_dir,
                candidate_pages_dir=cand_dir,
                candidate_method="OURS",
                max_dim=1800,
                swap_order=True,
            )

            # normal: 左侧是 100px 宽的 B0 → 分界线 ≈ 100+8
            self.assertAlmostEqual(divider_x(normal), 108, delta=2)
            # swapped: 左侧是 220px 宽的候选 → 分界线 ≈ 220+8
            self.assertAlmostEqual(divider_x(swapped), 228, delta=2)


class ReconcilePairRunsTest(unittest.TestCase):
    def test_agree_on_candidate(self) -> None:
        result = reconcile_pair_runs(
            {"vlm": {"winner": "candidate", "repair_score": 2}},
            {"vlm": {"winner": "candidate", "repair_score": 2}},
        )
        self.assertEqual(result["agreement"], "agree")
        self.assertEqual(result["winner"], "candidate")
        self.assertTrue(result["score_sign_agreement"])

    def test_conflict_marks_uncertain(self) -> None:
        result = reconcile_pair_runs(
            {"vlm": {"winner": "candidate", "repair_score": 2}},
            {"vlm": {"winner": "b0", "repair_score": -1}},
        )
        self.assertEqual(result["agreement"], "conflict")
        self.assertEqual(result["winner"], "uncertain")
        self.assertIn("position bias", result["note"])

    def test_tie_vs_win_is_conflict(self) -> None:
        result = reconcile_pair_runs(
            {"vlm": {"winner": "tie", "repair_score": 0}},
            {"vlm": {"winner": "candidate", "repair_score": 1}},
        )
        self.assertEqual(result["agreement"], "conflict")
        self.assertEqual(result["winner"], "uncertain")

    def test_both_tie_agrees(self) -> None:
        result = reconcile_pair_runs(
            {"vlm": {"winner": "tie", "repair_score": 0}},
            {"winner": "tie"},
        )
        self.assertEqual(result["agreement"], "agree")
        self.assertEqual(result["winner"], "tie")
        self.assertIsNone(result["score_sign_agreement"])

    def test_accepts_flat_and_nested_reports(self) -> None:
        result = reconcile_pair_runs(
            {"winner": "candidate"},
            {"vlm": {"winner": "candidate"}},
        )
        self.assertEqual(result["agreement"], "agree")


if __name__ == "__main__":
    unittest.main()
