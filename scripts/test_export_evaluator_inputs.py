from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from export_evaluator_inputs import export_case_inputs  # noqa: E402


SOURCE_TEX = (
    "\\documentclass{article}\n"
    "\\begin{document}\n"
    "Perturbed source with defect markers.\n"
    "\\end{document}\n"
)

FINAL_TEX = (
    "\\documentclass{article}\n"
    "\\begin{document}\n"
    "Perturbed source with defect markers repaired a bit.\n"
    "\\end{document}\n"
)


def _build_project(root: Path, *, with_pages: bool = True) -> Path:
    project = root / "project"
    (project / "data" / "pages").mkdir(parents=True)
    (project / "main.tex").write_text(FINAL_TEX, encoding="utf-8")
    log = (
        "Output written on main.pdf (9 pages).\n"
        "Overfull \\hbox (12.3pt too wide) at lines 8--12\n"
        "LaTeX Warning: Reference `fig:missing' undefined on input line 20.\n"
    )
    (project / "main.log").write_text(log, encoding="utf-8")
    if with_pages:
        for index in (1, 2, 3):
            (project / "data" / "pages" / f"page_{index:03d}.png").write_bytes(b"png")
    return project


def _build_case_dir(root: Path) -> Path:
    case_dir = root / "case"
    case_dir.mkdir()
    (case_dir / "benchmark_case.json").write_text(
        json.dumps(
            {
                "benchmark_case": {
                    "case_id": "example-paper-01",
                    "venue_id": "AAAI",
                    "template_name": "AAAI2026",
                    "profile": "hard",
                    "target_pages": 9,
                    "requested_perturbations": ["D1_long_token_overflow"],
                }
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "main.tex").write_text(SOURCE_TEX, encoding="utf-8")
    (case_dir / "main.log").write_text(
        "LaTeX Warning: Reference `fig:missing' undefined on input line 20.\n",
        encoding="utf-8",
    )
    return case_dir


class ExportEvaluatorInputsTest(unittest.TestCase):
    def test_exports_method_root_layout_with_program_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            project = _build_project(root)
            case_dir = _build_case_dir(root)

            summary = export_case_inputs(
                project_root=project,
                output_root=root / "runs" / "OURS",
                paper_id="AAAI",
                case_id="example-paper-01",
                case_dir=case_dir,
            )

            case_out = root / "runs" / "OURS" / "AAAI" / "example-paper-01"
            self.assertTrue((case_out / "pages" / "page_001.png").is_file())
            self.assertEqual(summary["pages_copied"], 3)
            report = json.loads(
                (case_out / "reports" / "evaluation_report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(report["method_id"], "OURS")
            self.assertEqual(report["target_pages"], 9)
            self.assertEqual(report["overfull_count"], 1)
            self.assertGreaterEqual(report["undefined_ref_count"], 1)
            self.assertTrue(report["tex_changed"])
            self.assertEqual(report["case_status"], "layout_evaluable")
            self.assertEqual(
                report["program_score_dimensions"]["embedding_similarity"],
                "not_available_in_official_snapshot",
            )
            self.assertIn("content_integrity", report)

    def test_missing_pages_marks_case_ineligible(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            project = _build_project(root, with_pages=False)

            summary = export_case_inputs(
                project_root=project,
                output_root=root / "runs" / "OURS",
                paper_id="AAAI",
                case_id="case-broken",
            )

            case_out = root / "runs" / "OURS" / "AAAI" / "case-broken"
            self.assertFalse((case_out / "pages" / "page_001.png").exists())
            report = json.loads(
                (case_out / "reports" / "evaluation_report.json").read_text(encoding="utf-8")
            )
            self.assertFalse(report["render_success"])
            self.assertEqual(report["case_status"], "input_incomplete")
            self.assertEqual(summary["case_status"], "input_incomplete")

    def test_explicit_case_status_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            project = _build_project(root)

            export_case_inputs(
                project_root=project,
                output_root=root / "runs" / "OURS",
                paper_id="AAAI",
                case_id="case-env",
                case_status="environment_ineligible",
            )
            report = json.loads(
                (root / "runs" / "OURS" / "AAAI" / "case-env" / "reports" / "evaluation_report.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(report["case_status"], "environment_ineligible")

    def test_rerun_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            project = _build_project(root)
            kwargs = dict(
                project_root=project,
                output_root=root / "runs" / "OURS",
                paper_id="AAAI",
                case_id="case-x",
            )
            export_case_inputs(**kwargs)
            (root / "runs" / "OURS" / "AAAI" / "case-x" / "pages" / "page_009.png").write_bytes(b"junk")
            export_case_inputs(**kwargs)
            pages = sorted(
                (root / "runs" / "OURS" / "AAAI" / "case-x" / "pages").glob("page_*.png")
            )
            self.assertEqual([page.name for page in pages], ["page_001.png", "page_002.png", "page_003.png"])


if __name__ == "__main__":
    unittest.main()
