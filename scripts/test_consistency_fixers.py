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


if __name__ == "__main__":
    unittest.main()
