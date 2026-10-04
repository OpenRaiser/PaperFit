from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from repair_plan_executor import execute_repair_plan  # noqa: E402
from repair_plan_generator import _build_consistency_candidates  # noqa: E402


TEX = r"""
\documentclass{article}
\begin{document}
\begin{table}
\resizebox{\linewidth}{!}{%
\begin{tabular}{ll}
a & b \\
\end{tabular}}
\end{table}
\end{document}
"""


class ConsistencyFixerTests(unittest.TestCase):
    def test_visual_c1_becomes_a_candidate(self) -> None:
        candidates = _build_consistency_candidates(
            {"findings": [{"taxonomy_defect_id": "C1"}]}
        )
        self.assertEqual(candidates[0]["defect_family"], "C1")
        self.assertEqual(_build_consistency_candidates({"findings": []}), [])

    def test_execute_removes_resizebox(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tex = root / "main.tex"
            tex.write_text(TEX, encoding="utf-8")
            plan = root / "plan.json"
            plan.write_text(
                json.dumps({"candidates": [{"defect_family": "C1"}]}),
                encoding="utf-8",
            )
            report = execute_repair_plan(str(plan), str(tex), str(root / "out.json"))
            updated = tex.read_text(encoding="utf-8")
            self.assertNotIn(r"\resizebox", updated)
            self.assertIn(r"\begin{tabular}", updated)
            self.assertGreaterEqual(report["applied_count"], 1)

    def test_execute_c4_removes_manual_caption_vspace(self) -> None:
        tex_c4 = (
            "\\documentclass{article}\n"
            "\\usepackage{caption}\n"
            "\\begin{document}\n"
            "\\begin{table}\n"
            "\\centering\n"
            "\\begin{tabular}{ll}\na & b \\\\\n\\end{tabular}\n"
            "\\vspace{12pt}\n"
            "\\caption{Manual gap}\n\\label{tab:c4}\n"
            "\\end{table}\n"
            "\\end{document}\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tex = root / "main.tex"
            tex.write_text(tex_c4, encoding="utf-8")
            plan = root / "plan.json"
            plan.write_text(
                json.dumps({"candidates": [{"defect_family": "C4"}]}),
                encoding="utf-8",
            )
            report = execute_repair_plan(str(plan), str(tex), str(root / "out.json"))
            updated = tex.read_text(encoding="utf-8")
            self.assertNotIn("\\vspace{12pt}", updated)
            self.assertIn("\\caption{Manual gap}", updated)
            c4_changes = [
                change
                for change in report["consistency_report"]["changes"]
                if change.get("defect_id") == "C4"
            ]
            self.assertEqual(len(c4_changes), 1)
            self.assertEqual(c4_changes[0]["applied_edits"], 1)

    def test_execute_c4_without_actions_reports_unresolved(self) -> None:
        tex_plain = (
            "\\documentclass{article}\n"
            "\\begin{document}\n"
            "No floats, no caption package.\n"
            "\\end{document}\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tex = root / "main.tex"
            tex.write_text(tex_plain, encoding="utf-8")
            plan = root / "plan.json"
            plan.write_text(
                json.dumps({"candidates": [{"defect_family": "C4"}]}),
                encoding="utf-8",
            )
            report = execute_repair_plan(str(plan), str(tex), str(root / "out.json"))
            self.assertEqual(
                report["consistency_report"]["unresolved"],
                [{"defect_id": "C4", "reason": "caption_spacing_not_normalizable"}],
            )
            self.assertEqual(tex.read_text(encoding="utf-8"), tex_plain)


if __name__ == "__main__":
    unittest.main()
