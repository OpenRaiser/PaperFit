from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from float_fixers import fix_float_defects, fix_float_reference_distance  # noqa: E402


class FloatFixerPolicyTest(unittest.TestCase):
    def test_b1_placement_relaxation_only_changes_restrictive_params(self) -> None:
        table_with_top = "\n".join(
            [
                r"\begin{table}[t]",
                r"\caption{Target}",
                r"\label{tab:target}",
                r"\end{table}",
            ]
        )
        unchanged, result = fix_float_reference_distance(
            table_with_top,
            float_label="tab:target",
            ref_page=1,
            float_page=5,
        )
        self.assertEqual(unchanged, table_with_top)
        self.assertIsNone(result)

        table_with_page = table_with_top.replace("[t]", "[p]")
        changed, result = fix_float_reference_distance(
            table_with_page,
            float_label="tab:target",
            ref_page=1,
            float_page=5,
        )
        self.assertIn(r"\begin{table}[ht]", changed)
        self.assertIsNotNone(result)

    def test_float_fix_does_not_apply_global_placeins_or_position_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tex_path = root / "main.tex"
            tex_path.write_text(
                "\n".join(
                    [
                        r"\documentclass{article}",
                        r"\begin{document}",
                        "Body text.",
                        r"\begin{figure}[p]",
                        r"\includegraphics[width=0.8\linewidth]{other.pdf}",
                        r"\caption{Other}",
                        r"\label{fig:other}",
                        r"\end{figure}",
                        r"\begin{figure}[t]",
                        r"\includegraphics[width=0.5\linewidth]{target.pdf}",
                        r"\caption{Target}",
                        r"\label{fig:target}",
                        r"\end{figure}",
                        r"\bibliography{refs}",
                        r"\end{document}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with mock.patch("float_fixers._passes_hard_content_gate", return_value=(True, "pass")):
                report = fix_float_defects(
                    str(tex_path),
                    [{"defect_id": "B2", "object": "fig:target", "page": 2}],
                )

            updated = tex_path.read_text(encoding="utf-8")
            self.assertEqual(report.status, "success")
            self.assertNotIn(r"\usepackage{placeins}", updated)
            self.assertNotIn(r"\FloatBarrier", updated)
            self.assertIn(r"\begin{figure}[p]", updated)
            self.assertIn(r"\includegraphics[width=\linewidth]{target.pdf}", updated)

    def test_floatbarrier_requires_explicit_hard_guard_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            base_tex = "\n".join(
                [
                    r"\documentclass{article}",
                    r"\begin{document}",
                    r"See Figure~\ref{fig:late}.",
                    r"\begin{figure}[t]",
                    r"\includegraphics[width=0.8\linewidth]{late.pdf}",
                    r"\caption{Late}",
                    r"\label{fig:late}",
                    r"\end{figure}",
                    r"\bibliography{refs}",
                    r"\end{document}",
                ]
            ) + "\n"

            without_guard = root / "without_guard.tex"
            without_guard.write_text(base_tex, encoding="utf-8")
            with mock.patch("float_fixers._passes_hard_content_gate", return_value=(True, "pass")):
                fix_float_defects(
                    str(without_guard),
                    [{"defect_id": "B1", "object": "fig:late", "page": 5, "ref_page": 1}],
                )
            self.assertNotIn(r"\FloatBarrier", without_guard.read_text(encoding="utf-8"))

            with_guard = root / "with_guard.tex"
            with_guard.write_text(base_tex, encoding="utf-8")
            with mock.patch("float_fixers._passes_hard_content_gate", return_value=(True, "pass")):
                fix_float_defects(
                    str(with_guard),
                    [
                        {
                            "defect_id": "B1",
                            "object": "fig:late",
                            "page": 5,
                            "ref_page": 1,
                            "endmatter_float_intrusion": True,
                        }
                    ],
                )
            guarded = with_guard.read_text(encoding="utf-8")
            self.assertIn(r"\usepackage{placeins}", guarded)
            self.assertIn(r"\FloatBarrier", guarded)


if __name__ == "__main__":
    unittest.main()
