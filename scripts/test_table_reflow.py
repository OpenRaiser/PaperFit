"""Measured table repairs preserve scientific content, numeric alignment and fonts."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from overflow_fixers import fix_overflow_defects, fix_table_overflow
from skills.latex_fixers.shared_table_helpers import (
    convert_last_text_column_to_x,
    convert_preserve_first_column_to_x,
    count_table_columns,
    find_first_tabular_span,
    rewrite_first_tabular_to_tabularx,
)


NUMERIC_TABLE = r"""\begin{table}[ht]
\centering
\caption{Validation results.}\label{tab:results}
\begin{tabular}{lrrrrrrrr}
Model & A & B & C & D & E & F & G & H \\
Baseline & 10.21 & 4.8 & 5.0 & 1.7 & 31.3 & 99.4 & 3.8 & 1.2 \\
Ours & 9.74 & 4.1 & 4.7 & 1.5 & 33.5 & 99.7 & 3.7 & 1.1 \\
\end{tabular}
\end{table}
"""


class TableColumnParserTests(unittest.TestCase):
    def test_decorator_macros_and_nested_widths_are_opaque(self):
        spec = r"{>{\centering\arraybackslash}p{\dimexpr .2\linewidth-2\tabcolsep\relax}rr}"
        self.assertEqual(count_table_columns(spec), 3)
        self.assertEqual(convert_last_text_column_to_x(spec), spec[:-2] + "X}")
        self.assertEqual(convert_preserve_first_column_to_x(spec),
                         r"{>{\centering\arraybackslash}p{\dimexpr .2\linewidth-2\tabcolsep\relax}XX}")

    def test_repeated_columns_have_real_count_and_single_last_conversion(self):
        spec = r"{l*{2}{*{3}{r}}}"
        self.assertEqual(count_table_columns(spec), 7)
        self.assertEqual(convert_last_text_column_to_x(spec), "{lrrrrrX}")

    def test_unknown_custom_column_fails_closed(self):
        spec = r"{lS[table-format=2.2]r}"
        self.assertEqual(count_table_columns(spec), 0)
        self.assertEqual(convert_last_text_column_to_x(spec), spec)

    def test_optional_position_whitespace_and_commented_example(self):
        source = "% \\begin{tabular}{ll} ignored\n" + r"\begin{tabular} [t] {lrr}" + "\na & 1 & 2 \\\\\n" + r"\end{tabular}"
        span = find_first_tabular_span(source)
        self.assertEqual(span["column_spec"], "{lrr}")
        updated, info = rewrite_first_tabular_to_tabularx(source, r"\linewidth", convert_last_text_column_to_x)
        self.assertIsNotNone(info)
        self.assertIn(r"\begin{tabularx}{\linewidth}[t]{lrX}", updated)
        self.assertIn("% \\begin{tabular}{ll} ignored", updated)


class MeasuredTableRepairTests(unittest.TestCase):
    def test_measured_padding_keeps_numeric_alignment_and_all_payload(self):
        updated, result = fix_table_overflow(NUMERIC_TABLE, "tab:results", overflow_amount=18)
        self.assertIsNotNone(result)
        self.assertIn(r"\setlength{\tabcolsep}{4.988pt}", updated)
        self.assertEqual(updated.replace(r"\setlength{\tabcolsep}{4.988pt}" + "\n", ""), NUMERIC_TABLE)
        self.assertNotIn(r"\small", updated)
        self.assertNotIn(r"\begin{table*}", updated)

    def test_padding_floor_keeps_readable_separation(self):
        updated, result = fix_table_overflow(NUMERIC_TABLE, "tab:results", overflow_amount=100)
        self.assertIsNotNone(result)
        self.assertIn(r"\setlength{\tabcolsep}{3pt}", updated)
        second, result = fix_table_overflow(updated, "tab:results", overflow_amount=46)
        self.assertIsNone(result)
        self.assertEqual(second, updated)

    def test_known_inherited_padding_and_unknown_macro(self):
        source = r"\setlength{\tabcolsep}{4pt}" + "\n" + NUMERIC_TABLE
        updated, result = fix_table_overflow(source, "tab:results", overflow_amount=3.4)
        self.assertIsNotNone(result)
        self.assertIn(r"\setlength{\tabcolsep}{3.8pt}", updated)
        for inherited in (r"\setlength{\tabcolsep}{2pt}", r"\setlength{\tabcolsep}{\tablepadding}"):
            source = inherited + "\n" + NUMERIC_TABLE
            updated, result = fix_table_overflow(source, "tab:results", overflow_amount=18)
            self.assertIsNone(result)
            self.assertEqual(updated, source)

    def test_previous_table_local_padding_does_not_leak(self):
        first = NUMERIC_TABLE.replace("tab:results", "tab:first").replace(r"\centering", r"\centering\setlength{\tabcolsep}{2pt}")
        source = first + NUMERIC_TABLE
        updated, result = fix_table_overflow(source, "tab:results", overflow_amount=18)
        self.assertIsNotNone(result)
        self.assertTrue(updated.startswith(first))
        self.assertIn(r"\setlength{\tabcolsep}{4.988pt}", updated)

    def test_invalid_outer_scale_is_one_isolated_correction(self):
        source = NUMERIC_TABLE.replace(r"\begin{tabular}", r"\resizebox{1.27\linewidth}{!}{\begin{tabular}").replace(r"\end{tabular}", r"\end{tabular}}")
        updated, result = fix_table_overflow(source, "tab:results", overflow_amount=107.3)
        self.assertIsNotNone(result)
        self.assertEqual(updated, source.replace("1.27", ""))
        self.assertNotIn(r"\small", updated)
        self.assertNotIn(r"\begin{table*}", updated)

    def test_unknown_label_never_modifies_a_nearby_table(self):
        updated, result = fix_table_overflow(NUMERIC_TABLE, "tab:missing", overflow_amount=18)
        self.assertIsNone(result)
        self.assertEqual(updated, NUMERIC_TABLE)

    def test_executor_passes_measured_overflow_to_table_strategy(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "main.tex"
            source.write_text(NUMERIC_TABLE, encoding="utf-8")
            report = fix_overflow_defects(str(source), [{"defect_id": "D1", "object": "tab:results", "overflow_amount": 18}])
            self.assertEqual(report.status, "success")
            self.assertIn("measured table padding", report.changes[0].action)
            self.assertIn(r"\begin{tabular}{lrrrrrrrr}", source.read_text(encoding="utf-8"))


def _tex_engine():
    explicit = os.environ.get("PAPERFIT_TEST_TECTONIC")
    tectonic = explicit or shutil.which("tectonic")
    if tectonic:
        return [tectonic, "--keep-logs", "--keep-intermediates", "--untrusted", "main.tex"]
    pdflatex = shutil.which("pdflatex")
    return [pdflatex, "-interaction=nonstopmode", "main.tex"] if pdflatex else None


class TableCompileRegressionTests(unittest.TestCase):
    @unittest.skipUnless(_tex_engine(), "set PAPERFIT_TEST_TECTONIC or install a TeX engine")
    def test_numeric_table_real_compile_removes_measured_overflow(self):
        # The width is measured by TeX itself, then the fixture requests exactly
        # 18pt less. This exercises geometry without relying on font metrics.
        tabular = NUMERIC_TABLE[NUMERIC_TABLE.index(r"\begin{tabular}"):NUMERIC_TABLE.index(r"\end{tabular}") + len(r"\end{tabular}")]
        source = (r"\documentclass{article}" + "\n" + r"\newsavebox{\tableprobe}" + "\n"
                  + r"\begin{document}" + "\n" + r"\sbox{\tableprobe}{" + tabular + "}\n"
                  + r"\setlength{\columnwidth}{\dimexpr\wd\tableprobe-18pt\relax}" + "\n"
                  + NUMERIC_TABLE + r"\end{document}")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tex = root / "main.tex"
            def compile_tex(content):
                tex.write_text(content, encoding="utf-8")
                result = subprocess.run(_tex_engine(), cwd=root, capture_output=True, timeout=180,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace")[-1000:])
                self.assertTrue((root / "main.pdf").is_file())
                return (root / "main.log").read_text(encoding="utf-8", errors="replace")
            before_log = compile_tex(source)
            widths = [float(value) for value in re.findall(r"Overfull \\hbox \(([0-9.]+)pt", before_log)]
            self.assertTrue(widths)
            updated, result = fix_table_overflow(source, "tab:results", overflow_amount=max(widths))
            self.assertIsNotNone(result)
            after_log = compile_tex(updated)
            self.assertNotIn(r"Overfull \hbox", after_log)
            self.assertIn(r"\begin{tabular}{lrrrrrrrr}", updated)
            self.assertNotIn(r"\small", updated)


if __name__ == "__main__":
    unittest.main()
