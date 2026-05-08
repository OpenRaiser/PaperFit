from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.source_hygiene_check import scan_source


class SourceHygieneCheckTest(unittest.TestCase):
    def test_detects_pollution_and_math_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tex_path = Path(tmpdir) / "main.tex"
            tex_path.write_text(
                r"""
\documentclass{article}
\title{A Paper}
STRAY DEBUG TOKEN
\begin{document}
\maketitle
The result is ?? and TODO.
\[
x + /Volumes/PAPERFIT_TEST/debug-token
\]
\end{document}
""",
                encoding="utf-8",
            )

            report = scan_source(str(tex_path))

        families = {finding["family"] for finding in report["findings"]}
        self.assertIn("unresolved_marker", families)
        self.assertIn("placeholder_token", families)
        self.assertIn("debug_token", families)
        self.assertIn("suspicious_math_payload", families)
        self.assertIn("title_stray_text", families)
        self.assertEqual(report["summary"]["highest_severity"], "critical")


if __name__ == "__main__":
    unittest.main()
