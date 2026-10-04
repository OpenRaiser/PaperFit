from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from regression.perturb import (  # noqa: E402
    INJECTOR_VERSION,
    apply_perturbations,
    perturb_project_files,
    wrap_first_tabular_with_resizebox,
)


PAPER = (
    "\\documentclass{article}\n"
    "\\usepackage{graphicx}\n"
    "\\begin{document}\n"
    "\\section{Intro}\n"
    "This paper studies the behaviour of layout repair systems on real documents.\n"
    "We evaluate our method on public benchmarks and report consistent gains.\n"
    "\n"
    "\\begin{figure}[t]\n"
    "\\centering\n"
    "\\includegraphics[width=0.9\\linewidth]{figs/overview.pdf}\n"
    "\\caption{Overview.}\n\\label{fig:overview}\n"
    "\\end{figure}\n"
    "\n"
    "\\begin{table}[h]\n"
    "\\centering\n"
    "\\begin{tabular}{lcc}\n"
    "\\hline\n"
    "System & Acc & F1 \\\\\n"
    "\\hline\n"
    "base & 1.0 & 2.0 \\\\\n"
    "ours & 1.1 & 2.1 \\\\\n"
    "\\hline\n"
    "\\end{tabular}\n"
    "\\caption{Results.}\n\\label{tab:results}\n"
    "\\end{table}\n"
    "\n"
    "Conclusion text with enough words to be a plain paragraph for injection.\n"
    "\\end{document}\n"
)


class LongTokenInjectionTest(unittest.TestCase):
    def test_d1_appends_token_after_first_paragraph(self):
        perturbed, results = apply_perturbations(PAPER, ["D1_long_token_overflow"])
        self.assertEqual(results[0].status, "success")
        self.assertIn("PERTURBED: D1_long_token_overflow", perturbed)
        self.assertIn("PerturbationStressToken", perturbed)
        # 注入点在第一个真段落之后、figure 环境之前
        token_pos = perturbed.find("PerturbationStressToken")
        figure_pos = perturbed.find("\\begin{figure}")
        self.assertLess(token_pos, figure_pos)

    def test_d1_falls_back_to_body_end_without_paragraph(self):
        hostile = "\\documentclass{article}\\begin{document}\\includegraphics{x}\\end{document}"
        perturbed, results = apply_perturbations(hostile, ["D1_long_token_overflow"])
        self.assertEqual(results[0].status, "success")
        # 兜底注入自成一段，位于 \end{document} 之前（正文分文件/无纯文本段的论文）
        self.assertLess(perturbed.find("PerturbationStressToken"), perturbed.find("\\end{document}"))


class LongFormulaInjectionTest(unittest.TestCase):
    def test_d2_inserts_long_equation(self):
        perturbed, results = apply_perturbations(PAPER, ["D2_long_formula"])
        self.assertEqual(results[0].status, "success")
        self.assertIn("PERTURBED: D2_long_formula", perturbed)
        self.assertIn("a_{13} x^{13}", perturbed)


class ShrinkGraphicsTest(unittest.TestCase):
    def test_b2_halves_widths_up_to_limit(self):
        two_images = PAPER.replace(
            "\\end{figure}",
            "\\includegraphics[width=0.8\\linewidth]{figs/second.pdf}\n\\end{figure}",
        )
        perturbed, results = apply_perturbations(two_images, ["B2_shrink_graphics"])
        self.assertEqual(results[0].status, "success")
        self.assertIn("width=0.45\\linewidth", perturbed)  # 0.9 -> 0.45
        self.assertIn("width=0.4\\linewidth", perturbed)   # 0.8 -> 0.4

    def test_b2_fails_without_includegraphics(self):
        _, results = apply_perturbations(
            "\\documentclass{article}\\begin{document}text only\\end{document}",
            ["B2_shrink_graphics"],
        )
        self.assertEqual(results[0].status, "target_not_found")


class ResizeboxWrapTest(unittest.TestCase):
    def test_c1_wraps_first_tabular(self):
        perturbed, results = apply_perturbations(PAPER, ["C1_table_resizebox"])
        self.assertEqual(results[0].status, "success")
        self.assertIn("\\resizebox{\\linewidth}{!}{%", perturbed)
        # 表格内容仍完整
        self.assertIn("System & Acc & F1", perturbed)
        self.assertIn("ours & 1.1 & 2.1", perturbed)

    def test_c1_refuses_when_resizebox_exists(self):
        already = PAPER.replace("\\begin{tabular}", "\\resizebox{0.9\\linewidth}{!}{\\begin{tabular}}", 1)
        perturbed, results = apply_perturbations(already, ["C1_table_resizebox"])
        self.assertEqual(results[0].status, "target_not_found")
        self.assertIn("already present", results[0].reason)

    def test_c1_no_tabular(self):
        result = wrap_first_tabular_with_resizebox(
            "\\documentclass{article}\\begin{document}no tables\\end{document}"
        )
        self.assertIsNotNone(result[2])


class StackedAndProjectTest(unittest.TestCase):
    def test_stack_four_perturbations_in_order(self):
        perturbed, results = apply_perturbations(
            PAPER,
            ["D1_long_token_overflow", "D2_long_formula", "B2_shrink_graphics", "C1_table_resizebox"],
        )
        self.assertEqual([r.status for r in results], ["success"] * 4)
        for marker in ("D1_long_token_overflow", "D2_long_formula"):
            self.assertIn(f"PERTURBED: {marker}", perturbed)

    def test_failure_stops_stack_and_reports(self):
        no_images = PAPER.replace("\\includegraphics[width=0.9\\linewidth]{figs/overview.pdf}\n", "")
        _, results = apply_perturbations(no_images, ["B2_shrink_graphics", "C1_table_resizebox"])
        self.assertEqual(results[0].status, "target_not_found")
        self.assertEqual(results[1].status, "success")

    def test_perturb_project_files_targets_largest_tex(self):
        files = {
            "main.tex": PAPER,
            "sections/intro.tex": "short",
        }
        updated, results = perturb_project_files(files, ["D1_long_token_overflow"])
        self.assertEqual(results[0].status, "success")
        self.assertIn("PerturbationStressToken", updated["main.tex"])
        self.assertEqual(updated["sections/intro.tex"], "short")

    def test_injector_version_recorded(self):
        self.assertTrue(INJECTOR_VERSION.startswith("real-paper-injector"))


if __name__ == "__main__":
    unittest.main()
