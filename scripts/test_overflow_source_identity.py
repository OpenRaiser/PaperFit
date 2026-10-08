"""Regression: local TeX line numbers must retain their included source file."""
from __future__ import annotations

import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from overflow_fixers import fix_overflow_defects
from overflow_objects import build_overflow_objects, index_by_warning
from repair_plan_executor import _build_overflow_defects, execute_repair_plan


TABLE = r"""\begin{table}[ht]
\caption{Measured results.}\label{tab:results}
\begin{tabular}{lrrrrrrrr}
Model & A & B & C & D & E & F & G & H \\
Baseline & 10.21 & 4.8 & 5.0 & 1.7 & 31.3 & 99.4 & 3.8 & 1.2 \\
Ours & 9.74 & 4.1 & 4.7 & 1.5 & 33.5 & 99.7 & 3.7 & 1.1 \\
\end{tabular}
\end{table}
"""
EQUATION = r"""\begin{equation}
\label{eq:sum} L = a + b + c + d + e + f + g + h + i + j + k + l
\end{equation}
"""


class OverflowSourceIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "sections").mkdir()
        self.main = self.root / "paper.tex"
        self.main_source = (r"\documentclass{article}" + "\n" + r"\begin{document}" + "\n"
                            + r"\input{sections/first}" + "\n" + r"\input{sections/second}" + "\n"
                            + r"\end{document}" + "\n")
        self.main.write_text(self.main_source, encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def write_collision_log(self, widths=(18.0, 18.0)):
        log = self.root / "paper.log"
        log.write_text("".join(
            f"(sections/{name}.tex\nOverfull \\hbox ({width}pt too wide) in paragraph at lines 4--4\n []\n\n)\n"
            for name, width in zip(("first", "second"), widths)), encoding="utf-8")
        return log

    def test_identical_warning_occurrences_keep_both_include_files(self):
        for name in ("first", "second"):
            (self.root / f"sections/{name}.tex").write_text("one\ntwo\nthree\n" + name + "\n", encoding="utf-8")
        log = self.write_collision_log()
        warnings = [{"overflow_pt": 18.0, "lines": "4--4", "subtype": "paragraph"} for _ in range(2)]
        objects = index_by_warning(build_overflow_objects(
            {"log_file": str(log), "overfull_hbox": warnings}, self.root, "paper.tex"))
        self.assertEqual(objects[0].source_file, "sections/first.tex")
        self.assertEqual(objects[1].source_file, "sections/second.tex")
        self.assertEqual(objects[0].source_anchor["end_line"], 4)

    def test_executor_preserves_file_identity_and_does_not_dedupe_distinct_sources(self):
        # The same main-file line is display math, but the included source line
        # is a table: pre-routing must not inspect the unrelated main line.
        self.main.write_text(r"\begin{equation}" + "\nx=y\n" + r"\end{equation}" + "\n", encoding="utf-8")
        candidates = [{"defect_family": "D1", "line_number": 2, "description": "in alignment",
                       "overflow_amount": 18.0, "source_anchor": {"file": f"sections/{name}.tex", "start_line": 2},
                       "overflow_id": f"ovf-{index:03d}"}
                      for index, name in enumerate(("first", "second"))]
        defects = _build_overflow_defects({"candidates": candidates}, str(self.main), 5)
        self.assertEqual(len(defects), 2)
        self.assertTrue(all(item["defect_id"] == "D1" for item in defects))
        self.assertEqual([item["source_anchor"]["file"] for item in defects],
                         ["sections/first.tex", "sections/second.tex"])
        self.assertEqual(defects[0]["overflow_id"], "ovf-000")

    def test_anchored_table_repairs_correct_include_without_whole_file_sloppy(self):
        first = self.root / "sections/first.tex"
        second = self.root / "sections/second.tex"
        first.write_text(TABLE, encoding="utf-8")
        second.write_text(TABLE.replace("tab:results", "tab:other"), encoding="utf-8")
        original_second = second.read_text(encoding="utf-8")
        self.write_collision_log()
        report = fix_overflow_defects(str(self.main), [{"defect_id": "D1", "line_number": 4,
            "object": "tab:results", "overflow_amount": 18.0,
            "source_anchor": {"file": "sections/first.tex", "start_line": 4}}])
        self.assertEqual(report.status, "success", report.to_dict())
        updated = first.read_text(encoding="utf-8")
        self.assertIn(r"\setlength{\tabcolsep}{4.988pt}", updated)
        self.assertEqual(updated.replace(r"\setlength{\tabcolsep}{4.988pt}" + "\n", ""), TABLE)
        self.assertEqual(second.read_text(encoding="utf-8"), original_second)
        self.assertEqual(self.main.read_text(encoding="utf-8"), self.main_source)
        self.assertNotIn(r"\sloppy", updated)
        self.assertEqual(report.modified_files, [str(first)])

    def test_legacy_line_only_collision_requires_review_and_writes_nothing(self):
        for name in ("first", "second"):
            (self.root / f"sections/{name}.tex").write_text("one\ntwo\nthree\nSome ordinary prose.\n", encoding="utf-8")
        self.write_collision_log()
        before = {path: path.read_bytes() for path in self.root.rglob("*.tex")}
        report = fix_overflow_defects(str(self.main), [{"defect_id": "D1", "line_number": 4,
                                                      "overflow_amount": 18.0}])
        self.assertEqual(report.status, "failed")
        self.assertIn("ambiguous source file", " ".join(report.unresolved))
        self.assertTrue(all(path.read_bytes() == content for path, content in before.items()))

    def test_plan_executor_repairs_two_tables_with_the_same_local_line(self):
        for name in ("first", "second"):
            (self.root / f"sections/{name}.tex").write_text(TABLE.replace("tab:results", f"tab:{name}"), encoding="utf-8")
        log = self.write_collision_log()
        objects = index_by_warning(build_overflow_objects({"log_file": str(log), "overfull_hbox": [
            {"overflow_pt": 18.0, "lines": "4--4", "subtype": "alignment"} for _ in range(2)]},
            self.root, "paper.tex"))
        plan = {"candidates": [{"candidate_type": "log_warning", "defect_family": "D1",
            "target": {"scope": "table_overflow"}, "line_number": 4, "overflow_amount": 18.0,
            "description": "in alignment", "source_anchor": objects[index].source_anchor}
            for index in range(2)]}
        plan_file = self.root / "plan.json"
        plan_file.write_text(json.dumps(plan), encoding="utf-8")
        report = execute_repair_plan(str(plan_file), str(self.main), str(self.root / "execution.json"))
        self.assertEqual(report["status"], "success", report)
        self.assertEqual(report["applied_count"], 2)
        self.assertEqual(len(report["selected_candidates"]["overflow"]), 2)
        for name in ("first", "second"):
            updated = (self.root / f"sections/{name}.tex").read_text(encoding="utf-8")
            self.assertIn(r"\setlength{\tabcolsep}{4.988pt}", updated)
            self.assertNotIn(r"\sloppy", updated)
        self.assertEqual(self.main.read_text(encoding="utf-8"), self.main_source)

    def test_explicit_source_file_repairs_include_equation_and_root_dependency(self):
        first = self.root / "sections/first.tex"
        second = self.root / "sections/second.tex"
        first.write_text(EQUATION, encoding="utf-8")
        second.write_text("Unrelated text.\n", encoding="utf-8")
        report = fix_overflow_defects(str(self.main), [{"defect_id": "D1", "line_number": 2,
            "object": "overflow", "overflow_amount": 40.0, "source_file": "sections/first.tex"}])
        self.assertEqual(report.status, "success", report.to_dict())
        updated = first.read_text(encoding="utf-8")
        self.assertTrue(r"\begin{aligned}" in updated or r"\begin{multline}" in updated)
        self.assertNotIn(r"\sloppy", updated)
        self.assertNotIn(r"\usepackage", updated)
        self.assertEqual(second.read_text(encoding="utf-8"), "Unrelated text.\n")
        self.assertEqual(self.main.read_text(encoding="utf-8").count(r"\usepackage{amsmath}"), 1)

    def test_nested_main_anchors_resolve_from_compilation_project_root(self):
        nested = self.root / "source"
        nested.mkdir()
        nested_main = nested / "paper.tex"
        nested_main.write_text(self.main_source, encoding="utf-8")
        first = self.root / "sections/first.tex"
        first.write_text(TABLE, encoding="utf-8")
        with patch("pathlib.Path.cwd", return_value=self.root):
            report = fix_overflow_defects(str(nested_main), [{"defect_id": "D1", "line_number": 4,
                "object": "tab:results", "overflow_amount": 18.0,
                "source_anchor": {"file": "sections/first.tex", "start_line": 4}}])
        self.assertEqual(report.status, "success", report.to_dict())
        self.assertIn(r"\setlength{\tabcolsep}{4.988pt}", first.read_text(encoding="utf-8"))
        self.assertEqual(nested_main.read_text(encoding="utf-8"), self.main_source)


if __name__ == "__main__":
    unittest.main()
