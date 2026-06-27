from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from overflow_fixers import fix_equation_overflow  # noqa: E402
from repair_plan_executor import execute_repair_plan  # noqa: E402


LONG_EQUATION = (
    r"\mathcal{L}_{\mathrm{disturb}} = "
    r"x_{\mathrm{BenchmarkEquationOverflowSequenceAlphaBetaGammaDeltaEpsilonZetaEtaThetaIotaKappa}} "
    r"+ y_{\mathrm{BenchmarkEquationOverflowSequenceLambdaMuNuXiOmicronPiRhoSigmaTauUpsilon}} "
    r"+ z_{\mathrm{BenchmarkEquationOverflowSequencePhiChiPsiOmegaAuxiliarySuffixChain}}"
)

CONCAT_FRACTION_EQUATION = (
    r"m_{ij}'^{(l)} = \mathrm{Concat}(\boldsymbol{h}_i^{(l)}, "
    r"\boldsymbol{h}_j^{(l)}, "
    r"\frac{(\boldsymbol{Z}_{i}^{(l)} - \boldsymbol{Z}_{j}^{(l)})^T "
    r"(\boldsymbol{Z}_{i}^{(l)} - \boldsymbol{Z}_{j}^{(l)})}"
    r"{\|(\boldsymbol{Z}_{i}^{(l)} - \boldsymbol{Z}_{j}^{(l)})^T "
    r"(\boldsymbol{Z}_{i}^{(l)} - \boldsymbol{Z}_{j}^{(l)})\|_F}),"
)


class OverflowFixersTest(unittest.TestCase):
    def test_equation_overflow_rewrites_display_math_to_aligned_terms(self) -> None:
        tex = "\n".join(
            [
                r"\documentclass{article}",
                r"\begin{document}",
                r"\begin{equation}",
                LONG_EQUATION,
                r"\end{equation}",
                r"\end{document}",
            ]
        )

        updated, result = fix_equation_overflow(tex, line_number=3)

        self.assertIsNotNone(result)
        self.assertIn(r"\begin{equation}", updated)
        self.assertIn(r"\begin{aligned}", updated)
        self.assertIn(r"\\", updated)
        self.assertIn(r"&\quad + y_", updated)
        self.assertIn(r"&\quad + z_", updated)
        self.assertEqual(updated.count(r"\substack"), 3)
        self.assertIn(r"\mathrm{BenchmarkEquationOverflow}", updated)
        self.assertNotIn(r"\begin{equation}" + "\n" + LONG_EQUATION, updated)
        self.assertEqual(result.defect_id, "D2")

    def test_equation_overflow_breaks_concat_fraction_argument(self) -> None:
        tex = "\n".join(
            [
                r"\documentclass{article}",
                r"\begin{document}",
                r"\begin{equation}",
                CONCAT_FRACTION_EQUATION,
                r"\end{equation}",
                r"\end{document}",
            ]
        )

        updated, result = fix_equation_overflow(tex, line_number=3)

        self.assertIsNotNone(result)
        self.assertIn(r"\begin{equation}", updated)
        self.assertIn(r"\begin{aligned}", updated)
        self.assertIn(
            r"\mathrm{Concat}(\boldsymbol{h}_i^{(l)}, \boldsymbol{h}_j^{(l)}, \\",
            updated,
        )
        self.assertIn(r"&\frac{(\boldsymbol{Z}_{i}^{(l)}", updated)
        self.assertIn(r"\|_F}),", updated)
        self.assertEqual(updated.count(r"\begin{equation}"), 1)
        self.assertNotIn(r"\begin{multline}", updated)
        self.assertEqual(result.defect_id, "D2")

    def test_executor_routes_display_math_d1_to_d2_and_defers_lower_priority_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\n".join(
                    [
                        r"\documentclass{article}",
                        r"\begin{document}",
                        "Intro paragraph.",
                        r"\begin{equation}",
                        LONG_EQUATION,
                        r"\end{equation}",
                        "A short final paragraph.",
                        r"\end{document}",
                    ]
                ),
                encoding="utf-8",
            )
            plan_path = root / "repair_plan.json"
            output_path = root / "repair_execution_report.json"
            plan_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "candidates": [
                            {
                                "candidate_type": "log_warning",
                                "defect_family": "D1",
                                "priority_score": 95,
                                "line_number": 5,
                                "target": {"scope": "overflow"},
                                "description": LONG_EQUATION,
                                "overflow_amount": 738.2,
                            },
                            {
                                "candidate_type": "visual_tail",
                                "defect_family": "A2",
                                "priority_score": 80,
                                "page": 2,
                                "target": {"scope": "page_whitespace"},
                                "description": "bottom whitespace",
                            },
                            {
                                "candidate_type": "global",
                                "defect_family": "A/C",
                                "priority_score": 40,
                                "target": {"scope": "paragraph_spacing"},
                                "proposed_action": "review_paragraph_spacing_and_looseness",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = execute_repair_plan(
                repair_plan_path=str(plan_path),
                main_tex=str(main_tex),
                output_path=str(output_path),
                max_candidates=1,
            )

            updated = main_tex.read_text(encoding="utf-8")
            self.assertEqual(report["status"], "success")
            self.assertEqual(report["selected_candidates"]["overflow"][0]["defect_id"], "D2")
            self.assertEqual(report["selected_candidates"]["space_util"], [])
            self.assertEqual(report["space_report"]["status"], "noop")
            self.assertEqual(report["global_report"]["status"], "noop")
            self.assertEqual(report["applied_count"], 1)
            self.assertIn(r"\begin{aligned}", updated)
            self.assertIn(r"\\", updated)
            self.assertEqual(updated.count(r"\substack"), 3)


if __name__ == "__main__":
    unittest.main()
