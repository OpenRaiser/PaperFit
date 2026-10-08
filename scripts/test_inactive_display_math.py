"""Only executed display math may be selected for an overflow rewrite."""
from __future__ import annotations

import sys
import unittest
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from overflow_fixers import _find_display_math_match, fix_equation_overflow
from repair_plan_executor import _display_math_anchor_line


class InactiveDisplayMathTests(unittest.TestCase):
    def test_comments_and_literal_commands_are_not_equations(self):
        for source in (r"% \[x+y\]", r"% \begin{equation}x+y\end{equation}",
                       r"\verb|a\[i\]|", r"\detokenize{\[x+y\]}",
                       r"\newcommand{\example}{\[x+y\]}"):
            with self.subTest(source=source):
                updated, fix = fix_equation_overflow(source, line_number=1)
                self.assertEqual(updated, source)
                self.assertIsNone(fix)

    def test_literal_environments_are_not_equations(self):
        for environment in ("verbatim", "lstlisting", "minted", "comment"):
            source = f"\\begin{{{environment}}}\na\\[i\\]\n\\end{{{environment}}}\n"
            with self.subTest(environment=environment):
                updated, fix = fix_equation_overflow(source, line_number=2)
                self.assertEqual(updated, source)
                self.assertIsNone(fix)

    def test_real_display_after_literal_example_uses_original_offsets(self):
        prefix = r"\newcommand{\example}{\[x+y\]}" + "\n% \\[x+y\\]\n"
        equation = r"\[L = a + b + c + d + e + f\]"
        source = prefix + equation + "\n"
        match = _find_display_math_match(source, line_number=3)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(0), equation)
        self.assertEqual(match.start(), len(prefix))
        updated, fix = fix_equation_overflow(source, line_number=3)
        self.assertIsNotNone(fix)
        self.assertTrue(updated.startswith(prefix))
        self.assertIn(r"\begin{equation*}", updated)
        self.assertIn(r"\begin{aligned}", updated)

    def test_masked_matching_retains_comments_in_real_equation_body(self):
        source = (r"\begin{equation}" + "\n" + r"a+b % \end{equation} example" + "\n"
                  + r"+c+d\label{eq:sum}" + "\n" + r"\end{equation}")
        match = _find_display_math_match(source, equation_label="eq:sum")
        self.assertIsNotNone(match)
        self.assertEqual(match.group(0), source)
        self.assertIn(r"% \end{equation} example", match.group(2))

    def test_executor_ignores_inactive_display_markers(self):
        source = (r"% \[x+y\]" + "\n" + r"\begin{verbatim}" + "\n"
                  + r"a\[i\]" + "\n" + r"\end{verbatim}" + "\n"
                  + r"\newcommand{\example}{\begin{equation}x+y\end{equation}}")
        with tempfile.TemporaryDirectory() as directory:
            tex = Path(directory) / "paper.tex"
            tex.write_text(source, encoding="utf-8")
            for line in range(1, 6):
                self.assertIsNone(_display_math_anchor_line(str(tex), line))

    def test_existing_aligned_equation_without_safe_split_is_not_wrapped_in_multline(self):
        source = (r"\begin{equation}\label{eq:composition}" + "\n"
                  + r"\begin{aligned}" + "\n"
                  + r"&x_0 = \phi_{1:T}(x_T,z_{1:T};\theta) = \phi_1\circ\phi_2\circ\phi_T(x_T,z_{1:T};\theta)=\\" + "\n"
                  + r"&\phi_1(\phi_{2:T}(x_T,z_{2:T};\theta),z_1;\theta),\\" + "\n"
                  + r"\end{aligned}" + "\n" + r"\end{equation}")
        updated, fix = fix_equation_overflow(source, equation_label="eq:composition")
        self.assertEqual(updated, source)
        self.assertIsNone(fix)


if __name__ == "__main__":
    unittest.main()
