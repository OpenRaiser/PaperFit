"B2 超宽图钳制修复器的真实回归测试。"

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from float_fixers import fix_figure_width_mismatch, fix_float_defects  # noqa: E402

BS = "\\"


def _real_doc() -> str:
    # 结构取自真实论文 L129-137（wrapfigure 0.44\textwidth + 超宽图），文字简化
    return "\n".join([
        BS + "documentclass[10pt]{article}",
        BS + "usepackage[margin=1in]{geometry}",
        BS + "usepackage{graphicx,wrapfig,amsmath}",
        BS + "begin{document}",
        *(("Filler paragraph text number %d with several words to wrap around the figure. " % i) * 3 for i in range(1, 12)),
        "",
        BS + "begin{wrapfigure}[12]{r}{0.44" + BS + "textwidth}",
        "  " + BS + "centering",
        BS + "includegraphics[width=1.38" + BS + "linewidth]{figures/teaser.pdf}",
        "  " + BS + "caption{Kept caption.}",
        "  " + BS + "label{fig:teaser}",
        BS + "end{wrapfigure}",
        "",
        *(("More filler text after the wrap figure with enough words %d. " % i) * 3 for i in range(1, 10)),
        BS + "end{document}",
        "",
    ])


def _make_image(root: Path) -> None:
    from PIL import Image, ImageDraw

    figs = root / "figures"
    figs.mkdir(exist_ok=True)
    img = Image.new("RGB", (400, 260), (240, 240, 245))
    d = ImageDraw.Draw(img)
    d.rectangle([20, 20, 380, 240], outline=(60, 60, 80), width=3)
    d.line([20, 130, 380, 130], fill=(200, 60, 60), width=4)
    img.save(figs / "teaser.pdf")


def _compile(work: Path):
    subprocess.run(
        ["pdflatex", "-interaction=nonstopmode", "main.tex"],
        cwd=work, capture_output=True, timeout=300,
    )
    log = work / "main.log"
    text = log.read_text(errors="replace") if log.exists() else ""
    overfull = [float(m) for m in re.findall(r"Overfull \\hbox \(([\d.]+)pt", text)]
    m = re.search(r"Output written on main\.pdf\s*\((\d+) pages?", text)
    return text, overfull, (int(m.group(1)) if m else None)


class ClampUnitTest(unittest.TestCase):
    def test_oversized_factor_clamped_only(self):
        doc = (
            BS + "begin{figure}[h]" + BS + "centering\n"
            + BS + "includegraphics[width=1.38" + BS + "linewidth]{figs/a.pdf}\n"
            + BS + "caption{c}" + BS + "label{fig:a}" + BS + "end{figure}\n"
        )
        modified, fix = fix_figure_width_mismatch(doc, "fig:a")
        self.assertIsNotNone(fix)
        self.assertIn("钳制", fix.action)
        self.assertIn("width=" + BS + "linewidth", modified)
        self.assertNotIn("1.38", modified)
        # 文件路径与其他内容逐字保留
        self.assertIn("{figs/a.pdf}", modified)
        self.assertIn(BS + "caption{c}", modified)
        self.assertIn(BS + "label{fig:a}", modified)

    def test_multi_option_graphics_options_preserved(self):
        doc = (
            BS + "begin{wrapfigure}{r}{0.44" + BS + "textwidth}" + BS + "centering\n"
            + BS + "includegraphics[trim={0.38cm 1.07cm 0.35cm 0.36cm}, clip, width=1.28" + BS + "linewidth]{figs/b.pdf}\n"
            + BS + "caption{c}" + BS + "label{fig:b}" + BS + "end{wrapfigure}\n"
        )
        modified, fix = fix_figure_width_mismatch(doc, "fig:b")
        self.assertIsNotNone(fix)
        self.assertIn("trim={0.38cm 1.07cm 0.35cm 0.36cm}, clip, width=" + BS + "linewidth", modified)
        self.assertIn("{figs/b.pdf}", modified)

    def test_undersized_factor_not_clamped(self):
        doc = (
            BS + "begin{figure}[h]" + BS + "centering\n"
            + BS + "includegraphics[width=0.78" + BS + "linewidth]{figs/c.pdf}\n"
            + BS + "caption{c}" + BS + "label{fig:c}" + BS + "end{figure}\n"
        )
        modified, fix = fix_figure_width_mismatch(doc, "fig:c")
        if fix is not None:
            self.assertNotIn("钳制", fix.action)  # K≤1 不走钳制策略
        self.assertIn("{figs/c.pdf}", modified)

    def test_wrapfigure_block_located_by_label(self):
        # 真实案例形态：超宽图在 wrapfigure 里，也必须能按 label 唯一定位
        doc = (
            BS + "begin{wrapfigure}[15]{r}{0.44" + BS + "textwidth}\n"
            + BS + "includegraphics[width=1.38" + BS + "linewidth]{figs/d.pdf}\n"
            + BS + "caption{c}" + BS + "label{fig:d}\n"
            + BS + "end{wrapfigure}\n"
        )
        modified, fix = fix_figure_width_mismatch(doc, "fig:d")
        self.assertIsNotNone(fix)
        self.assertIn("width=" + BS + "linewidth", modified)


class RealCompileTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("pdflatex"), "pdflatex not installed")
    def test_real_paper_fixture_overfull_reduced(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            (work / "main.tex").write_text(_real_doc(), encoding="utf-8")
            _make_image(work)
            before_text, before_overfull, before_pages = _compile(work)
            self.assertGreaterEqual(len(before_overfull), 1, "前置：超宽图必须产生可复现 overfull")
            self.assertGreater(max(before_overfull), 10.0)

            report = fix_float_defects(
                str(work / "main.tex"),
                [{"defect_id": "B2", "object": "fig:teaser", "page": 1, "description": "oversized graphics"}],
                template_type="single_column",
            )
            self.assertEqual(report.status, "success")
            self.assertEqual(len(report.changes), 1)
            self.assertIn("钳制", report.changes[0].action)

            after_text, after_overfull, after_pages = _compile(work)
            after_src = (work / "main.tex").read_text(encoding="utf-8")
            # 指标断言：overfull 数或总量下降；页数不退化；结构对象不减少；无新增缺图/未定义引用
            self.assertLess(len(after_overfull), len(before_overfull))
            self.assertLess(sum(after_overfull), sum(before_overfull))
            self.assertLessEqual(after_pages, before_pages)
            before_src_lines = _real_doc()
            self.assertEqual(
                after_src.count(BS + "includegraphics"),
                before_src_lines.count(BS + "includegraphics"),
            )
            self.assertIn(BS + "caption{Kept caption.}", after_src)
            self.assertIn(BS + "label{fig:teaser}", after_src)
            self.assertIn("{figures/teaser.pdf}", after_src)  # 图片文件参数未动
            self.assertEqual(
                after_src.count("File `figures/teaser.pdf' not found"), 0
            )


if __name__ == "__main__":
    unittest.main()
