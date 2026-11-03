from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from inject_defects import SampleGenerator  # noqa: E402


ALL_DEFECT_TYPES = [
    "A1-widow-orphan",
    "A2-trailing-whitespace",
    "B1-float-placement",
    "B2-float-width",
    "B3-float-clustering",
    "D1-overfull-hbox",
    "D2-long-formula",
    "D3-url-overflow",
]


class InjectedSampleSyntaxTest(unittest.TestCase):
    """Injected benchmark samples must contain valid single-backslash LaTeX."""

    def test_every_injection_produces_single_backslash_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            generator = SampleGenerator(Path(tmpdir))
            output_path, defects = generator.generate_defective_sample(
                "all_defects",
                ALL_DEFECT_TYPES,
            )
            content = output_path.read_text(encoding="utf-8")

            self.assertEqual(len(defects), len(ALL_DEFECT_TYPES))
            # A literal double backslash before a command would be a line
            # break followed by the word 'begin', i.e. broken LaTeX.
            self.assertNotIn("\\\\\\begin", content)
            self.assertNotIn("\\\\\\includegraphics", content)
            self.assertNotIn("\\\\\\label", content)
            self.assertNotIn("\\\\\\caption", content)
            self.assertNotIn("\\\\\\end", content)
            self.assertIn("\\begin{document}", content)
            self.assertIn("\\end{document}", content)

    def test_b3_and_d2_injections_are_valid_latex_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            generator = SampleGenerator(Path(tmpdir))
            output_path, _ = generator.generate_defective_sample(
                "b3_d2",
                ["B3-float-clustering", "D2-long-formula"],
            )
            content = output_path.read_text(encoding="utf-8")
            self.assertIn("\\begin{figure}[h]", content)
            self.assertIn("\\includegraphics[width=0.5\\linewidth]", content)
            self.assertIn("\\caption{Injected figure causing clustering.}", content)
            self.assertIn("\\end{figure}", content)
            self.assertIn("f(x) = a_0 + a_1 x", content)
            self.assertIn("\\end{equation}", content)


if __name__ == "__main__":
    unittest.main()
