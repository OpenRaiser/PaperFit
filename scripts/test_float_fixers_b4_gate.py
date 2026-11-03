from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from float_fixers import fix_float_defects  # noqa: E402


SPLIT_TABLE_TEX = """\\documentclass{article}
\\begin{document}
Text before the table.
\\begin{table}[h]
\\centering
\\caption{A long table that splits across pages}
\\label{tab:split}
\\begin{tabular}{lcc}
\\hline
System & Metric A & Metric B \\\\
\\hline
baseline & 1.0 & 2.0 \\\\
ours-small & 1.1 & 2.1 \\\\
ours-large & 1.2 & 2.2 \\\\
ours-extra & 1.3 & 2.3 \\\\
\\hline
\\end{tabular}
\\end{table}
Text after the table.
\\end{document}
"""


class B4SplitTableGateTest(unittest.TestCase):
    """The longtable rewrite must survive its own hard content gate."""

    def test_b4_split_table_lands_after_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tex = Path(tmpdir) / "main.tex"
            tex.write_text(SPLIT_TABLE_TEX, encoding="utf-8")

            report = fix_float_defects(
                tex_file_path=str(tex),
                defects=[
                    {
                        "defect_id": "B4",
                        "object": "tab:split",
                        "page": 2,
                        "line_number": None,
                    }
                ],
                template_type="double_column",
            )
            payload = report.to_dict()
            updated = tex.read_text(encoding="utf-8")

            self.assertIn("\\begin{longtable}", updated)
            self.assertIn("Continued on next page", updated)
            self.assertIn("\\caption{A long table that splits across pages}", updated)
            self.assertEqual(payload["status"], "success", payload)
            self.assertTrue(
                any(change.get("defect_id") == "B4" for change in payload.get("changes", [])),
                payload.get("changes"),
            )


if __name__ == "__main__":
    unittest.main()
