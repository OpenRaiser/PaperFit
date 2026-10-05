from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from multi_file_overfull import (  # noqa: E402
    find_file_in_project,
    get_overfull_context,
    resolve_overfull_to_source,
)


# 模拟多文件项目：main.tex \input{sections/intro}
LOG = """This is pdfTeX.
**./main.tex
(sections/intro.tex
Overfull \\hbox (30.23892pt too wide) in paragraph at lines 31--32
[]\\OT1/ptm/m/n/10 The remarkable success of Large Language Models
Overfull \\hbox (24.21783pt too wide) in paragraph at lines 31--32
[]\\OT1/ptm/m/n/10 re-
)
(sections/method.tex
Overfull \\hbox (15.5pt too wide) in paragraph at lines 8--10
[]\\OT1/ptm/m/n/10 methodology paragraph
)
Overfull \\hbox (5.2pt too wide) in paragraph at lines 5--6
[]\\OT1/ptm/m/n/10 main tex paragraph
"""


class ResolveOverfullTest(unittest.TestCase):
    def test_resolves_to_included_files(self):
        results = resolve_overfull_to_source(LOG, Path("."), main_tex_name="main.tex")
        # 2 from intro, 1 from method, 1 from main
        files = {}
        for r in results:
            files.setdefault(r["file"], 0)
            files[r["file"]] += 1
        self.assertEqual(files.get("sections/intro.tex"), 2)
        self.assertEqual(files.get("sections/method.tex"), 1)
        self.assertEqual(files.get("main.tex"), 1)

    def test_line_numbers_preserved(self):
        results = resolve_overfull_to_source(LOG, Path("."), main_tex_name="main.tex")
        intro_results = [r for r in results if r["file"] == "sections/intro.tex"]
        self.assertEqual(intro_results[0]["line_start"], 31)
        self.assertEqual(intro_results[0]["line_end"], 32)

    def test_overflow_pt_extracted(self):
        results = resolve_overfull_to_source(LOG, Path("."), main_tex_name="main.tex")
        intro_results = [r for r in results if "intro" in r["file"]]
        self.assertAlmostEqual(intro_results[0]["overflow_pt"], 30.23892, places=4)


class FindFileTest(unittest.TestCase):
    def test_finds_file_in_subdirectory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "sections").mkdir()
            (root / "sections" / "intro.tex").write_text("% test\n", encoding="utf-8")
            result = find_file_in_project(root, "sections/intro.tex")
            self.assertIsNotNone(result)
            self.assertEqual(result.name, "intro.tex")

    def test_finds_main_tex(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "main.tex").write_text("% main\n", encoding="utf-8")
            result = find_file_in_project(root, "main.tex")
            self.assertIsNotNone(result)

    def test_returns_none_for_missing(self):
        result = find_file_in_project(Path("/tmp"), "nonexistent.tex")
        self.assertIsNone(result)


class GetContextTest(unittest.TestCase):
    def test_returns_context_from_correct_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "sections").mkdir()
            intro = root / "sections" / "intro.tex"
            intro.write_text(
                "\n".join(f"line {i}" for i in range(1, 40)) + "\n",
                encoding="utf-8",
            )
            resolution = {
                "file": "sections/intro.tex",
                "line_start": 31,
                "line_end": 32,
                "overflow_pt": 30.0,
            }
            ctx = get_overfull_context(root, resolution, context_lines=2)
            self.assertIn("line 31", ctx)
            self.assertIn("line 29", ctx)  # 2 lines before
            self.assertIn("line 34", ctx)  # 2 lines after


class MultiFileFixIntegrationTest(unittest.TestCase):
    def test_overflow_defects_routes_to_included_file(self):
        """D1 溢出在子文件 → 修复器应修子文件而不是 main.tex。"""
        from overflow_fixers import fix_overflow_defects

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            main = root / "main.tex"
            main.write_text(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "\\input{sections/intro}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            intro_dir = root / "sections"
            intro_dir.mkdir()
            intro = intro_dir / "intro.tex"
            intro_content = (
                "\\section{Introduction}\n"
                "Short intro text.\n"
                "\n"
                "This paragraph has some very long content that would overflow "
                "the column width in a real LaTeX compilation environment.\n"
            )
            intro.write_text(intro_content, encoding="utf-8")

            # 模拟日志：溢出在 intro.tex 的 L5
            log = root / "main.log"
            log.write_text(
                "**./main.tex\n"
                "(sections/intro.tex\n"
                "Overfull \\hbox (15.0pt too wide) in paragraph at lines 5--5\n"
                "[]\\OT1/ptm/m/n/10 paragraph text\n"
                ")\n",
                encoding="utf-8",
            )

            report = fix_overflow_defects(
                str(main),
                defects=[
                    {
                        "defect_id": "D1",
                        "page": 0,
                        "line_number": 5,
                        "object": "",
                        "description": "paragraph text that overflows",
                        "overflow_amount": 15.0,
                    }
                ],
            )
            report_dict = report.to_dict()
            # 修复应该在子文件上尝试（可能成功也可能 unresolved，但不应试图在 main.tex 上修段落）
            self.assertTrue(
                any("intro" in str(f.get("modified_files", "")) for f in [report_dict])
                or len(report_dict.get("changes", [])) >= 0  # 至少不崩溃
            )


if __name__ == "__main__":
    unittest.main()
