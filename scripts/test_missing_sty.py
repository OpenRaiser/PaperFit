from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from paperfit_command import missing_public_sty_names  # noqa: E402


class MissingStyTests(unittest.TestCase):
    def test_extracts_unique_public_package_names(self) -> None:
        log = "\n".join([
            "! LaTeX Error: File `algpseudocode.sty' not found.",
            "! LaTeX Error: File `forloop.sty' not found.",
            "! LaTeX Error: File `algpseudocode.sty' not found.",
            "LaTeX Warning: File `figs/motivation.pdf' not found on input line 171.",
        ])
        self.assertEqual(missing_public_sty_names(log), ["algpseudocode", "forloop"])


if __name__ == "__main__":
    unittest.main()
