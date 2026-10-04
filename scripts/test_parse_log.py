from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from parse_log import LogParser


class ParseLogTest(unittest.TestCase):
    def test_overfull_detected_at_line_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "main.log"
            log_path.write_text(
                "\n".join(
                    [
                        "This is pdfTeX",
                        "Overfull \\hbox (738.21164pt too wide) detected at line 221",
                        "Overfull \\hbox (13.21004pt too wide) in paragraph at lines 315--315",
                        "Output written on main.pdf (1 page).",
                    ]
                ),
                encoding="utf-8",
            )

            report = LogParser(str(log_path)).parse()

        overfull = report["overfull_hbox"]
        self.assertEqual(len(overfull), 2)
        self.assertEqual(overfull[0]["lines"], "221")
        self.assertEqual(overfull[0]["context"], "")
        self.assertEqual(overfull[1]["lines"], "315--315")
        self.assertEqual(overfull[1]["context"], "in paragraph")


if __name__ == "__main__":
    unittest.main()
