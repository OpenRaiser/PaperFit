from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from float_fixers import fix_float_defects, fix_float_reference_distance, fix_figure_width_mismatch  # noqa: E402


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

    def test_b2_overwide_figure_is_normalized_to_linewidth(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tex_path = root / "main.tex"
            tex_path.write_text(
                "\n".join(
                    [
                        r"\documentclass{article}",
                        r"\begin{document}",
                        r"\begin{figure}[ht]",
                        r"\centering",
                        r"\includegraphics[width=1.28\linewidth]{figs/edge.pdf}",
                        r"\caption{The schematic diagram of edges.}",
                        r"\label{fig:edge}",
                        r"\end{figure}",
                        r"\end{document}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with mock.patch("float_fixers._passes_hard_content_gate", return_value=(True, "pass")):
                report = fix_float_defects(
                    str(tex_path),
                    [{"defect_id": "B2", "object": "fig:edge", "page": 5}],
                )

            updated = tex_path.read_text(encoding="utf-8")
            self.assertEqual(report.status, "success")
            self.assertEqual(len(report.changes), 1)
            self.assertEqual(report.changes[0].defect_id, "B2")
            self.assertEqual(report.changes[0].object_name, "fig:edge")
            self.assertIn(r"\includegraphics[width=\linewidth]{figs/edge.pdf}", updated)
            self.assertNotIn(r"width=1.28\linewidth", updated)
            self.assertIn(r"\caption{The schematic diagram of edges.}", updated)
            self.assertIn(r"\label{fig:edge}", updated)

    def test_b2_object_kind_table_routes_table_label_even_when_label_contains_fig(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tex_path = root / "main.tex"
            tex_path.write_text(
                "\n".join(
                    [
                        r"\documentclass{article}",
                        r"\begin{document}",
                        r"\begin{table}[t]",
                        r"\centering",
                        r"\caption{Results}",
                        r"\label{tab:fig-results}",
                        r"\begin{tabular}{lll}",
                        r"A & B & C \\",
                        r"1 & 2 & 3 \\",
                        r"\end{tabular}",
                        r"\end{table}",
                        r"\end{document}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with mock.patch("float_fixers._passes_hard_content_gate", return_value=(True, "pass")):
                report = fix_float_defects(
                    str(tex_path),
                    [
                        {
                            "defect_id": "B2",
                            "object": "tab:fig-results",
                            "object_kind": "table_like",
                            "visual_width_subtype": "overflow_width",
                            "page": 3,
                        }
                    ],
                )

            updated = tex_path.read_text(encoding="utf-8")
            self.assertEqual(report.status, "success")
            self.assertEqual(report.changes[0].object_name, "tab:fig-results")
            self.assertIn(r"\begin{tabular*}{\linewidth}", updated)
            self.assertIn(r"\label{tab:fig-results}", updated)
            self.assertNotIn(r"\includegraphics", updated)

    def test_b2_object_kind_table_routes_table_label_without_tab_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tex_path = root / "main.tex"
            tex_path.write_text(
                "\n".join(
                    [
                        r"\documentclass{article}",
                        r"\begin{document}",
                        r"\begin{table}[t]",
                        r"\centering",
                        r"\caption{Results}",
                        r"\label{wide-results}",
                        r"\begin{tabular}{lll}",
                        r"A & B & C \\",
                        r"1 & 2 & 3 \\",
                        r"\end{tabular}",
                        r"\end{table}",
                        r"\end{document}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with mock.patch("float_fixers._passes_hard_content_gate", return_value=(True, "pass")):
                report = fix_float_defects(
                    str(tex_path),
                    [
                        {
                            "defect_id": "B2",
                            "object": "wide-results",
                            "object_kind": "table_like",
                            "visual_width_subtype": "overflow_width",
                            "page": 3,
                        }
                    ],
                )

            updated = tex_path.read_text(encoding="utf-8")
            self.assertEqual(report.status, "success")
            self.assertEqual(report.changes[0].object_name, "wide-results")
            self.assertIn(r"\begin{tabular*}{\linewidth}", updated)
            self.assertIn(r"\label{wide-results}", updated)

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


class FigureWidthEvidencePolicyTest(unittest.TestCase):
    def _figure(self, content, environment="figure"):
        return (f"\\begin{{{environment}}}[t]\n" + content
                + "\n" + r"\label{fig:target}" + f"\n\\end{{{environment}}}\n")

    def test_caption_overscale_is_clamped_without_enlarging_main_panel(self):
        source = self._figure(r"\includegraphics[width=.4\linewidth]{main.pdf}" + "\n"
                              + r"\caption{A trajectory \includegraphics[width=1.28\linewidth]{arrow.pdf} reaches the target.}")
        updated, result = fix_figure_width_mismatch(source, "fig:target")
        self.assertIsNotNone(result)
        self.assertEqual(updated, source.replace("1.28", ""))

    def test_all_provable_overscales_in_target_float_are_clamped(self):
        source = self._figure(r"\includegraphics[width=1.18\linewidth]{first.pdf}" + "\n"
                              + r"\includegraphics[trim={0 0 2 0},clip,width={1.25\columnwidth}]{second.pdf}" + "\n"
                              + r"\caption{Kept caption.}")
        outside = self._figure(r"\includegraphics[width=1.4\linewidth]{other.pdf}" + r"\caption{Other.}").replace("fig:target", "fig:other")
        updated, result = fix_figure_width_mismatch(source + outside, "fig:target")
        self.assertIsNotNone(result)
        self.assertEqual(updated, source.replace("1.18", "").replace(r"{1.25\columnwidth}", r"\columnwidth") + outside)

    def test_repeated_width_last_underfill_is_never_expanded(self):
        source = self._figure(r"\includegraphics[width=1.4\linewidth, width=.4\linewidth]{panel.pdf}" + r"\caption{Kept.}")
        updated, result = fix_figure_width_mismatch(source, "fig:target")
        self.assertIsNone(result)
        self.assertEqual(updated, source)

    def test_repeated_width_last_overscale_is_clamped_only_at_effective_key(self):
        source = self._figure(r"\includegraphics[width=.4\linewidth, width=1.2\linewidth]{panel.pdf}" + r"\caption{Kept.}")
        updated, result = fix_figure_width_mismatch(source, "fig:target")
        self.assertIsNotNone(result)
        self.assertEqual(updated, source.replace("1.2", ""))

    def test_multi_panel_without_provable_overscale_is_a_noop(self):
        source = self._figure(r"\includegraphics[width=.4\linewidth]{left.pdf}" + "\n"
                              + r"\includegraphics[width=.4\linewidth]{right.pdf}" + r"\caption{Kept.}")
        updated, result = fix_figure_width_mismatch(source, "fig:target")
        self.assertIsNone(result)
        self.assertEqual(updated, source)

    def test_typed_underfill_expands_sole_body_and_excludes_caption_graphic(self):
        source = self._figure(r"\includegraphics[width=.4\linewidth]{main.pdf}" + "\n"
                              + r"\caption{A trajectory \includegraphics[width=\linewidth]{arrow.pdf} reaches probability $0.95$.}")
        unchanged, result = fix_figure_width_mismatch(source, "fig:target")
        self.assertIsNone(result)
        self.assertEqual(unchanged, source)
        updated, result = fix_figure_width_mismatch(source, "fig:target", visual_width_subtype="underfilled_width")
        self.assertIsNotNone(result)
        self.assertEqual(updated, source.replace("width=.4\\linewidth", "width=\\linewidth"))

    def test_typed_underfill_multiple_body_panels_remains_a_noop(self):
        source = self._figure(r"\includegraphics[width=.4\linewidth]{left.pdf}" + "\n"
                              + r"\includegraphics[width=.4\linewidth]{right.pdf}"
                              + r"\caption{Key \includegraphics[width=.1\linewidth]{key.pdf}.}")
        updated, result = fix_figure_width_mismatch(source, "fig:target", visual_width_subtype="underfilled_width")
        self.assertIsNone(result)
        self.assertEqual(updated, source)

    def test_typed_underfill_excludes_graphics_in_short_and_full_caption(self):
        source = self._figure(r"\includegraphics[width=.4\linewidth]{main.pdf}" + "\n"
                              + r"\caption[Key \includegraphics[width=.1\linewidth]{key.pdf}]{Long key \includegraphics[width=.2\linewidth]{other.pdf}.}")
        updated, result = fix_figure_width_mismatch(source, "fig:target", visual_width_subtype="underfilled_width")
        self.assertIsNotNone(result)
        self.assertEqual(updated, source.replace("width=.4\\linewidth", "width=\\linewidth"))

    def test_typed_underfill_only_changes_last_effective_width_key(self):
        source = self._figure(r"\includegraphics[width=1.4\linewidth, width=.4\linewidth]{main.pdf}"
                              + r"\caption{Key \includegraphics[width=.1\linewidth]{key.pdf}.}")
        updated, result = fix_figure_width_mismatch(source, "fig:target", visual_width_subtype="underfilled_width")
        self.assertIsNotNone(result)
        self.assertEqual(updated, source.replace("width=.4\\linewidth", "width=\\linewidth"))

    def test_typed_underfill_unknown_width_is_not_guessed(self):
        source = self._figure(r"\includegraphics[width=\authorwidth]{main.pdf}"
                              + r"\caption{Key \includegraphics[width=.1\linewidth]{key.pdf}.}")
        updated, result = fix_figure_width_mismatch(source, "fig:target", visual_width_subtype="underfilled_width")
        self.assertIsNone(result)
        self.assertEqual(updated, source)

    def test_plan_underfill_evidence_reaches_body_graphic_fixer(self):
        from repair_plan_executor import _build_float_defects
        from fixer_registry import execute_float_candidates
        with tempfile.TemporaryDirectory() as directory:
            source = self._figure(r"\includegraphics[width=.4\linewidth]{main.pdf}"
                                  + r"\caption{Key \includegraphics[width=\linewidth]{key.pdf}.}")
            path = Path(directory) / "main.tex"
            path.write_text(source, encoding="utf-8")
            plan = {"candidates": [{"defect_family": "B2", "target": {"label": "fig:target", "object_kind": "figure_like"},
                                     "visual_width_subtype": "underfilled_width", "visual_object_width_ratio": .4}]}
            defects = _build_float_defects(plan, max_candidates=1)
            with mock.patch("float_fixers._passes_hard_content_gate", return_value=(True, "pass")):
                report = execute_float_candidates(str(path), defects, column_type="double")
            self.assertEqual(report["status"], "success")
            self.assertEqual(path.read_text(encoding="utf-8"), source.replace("width=.4\\linewidth", "width=\\linewidth"))

    def test_textwidth_clamp_requires_starred_float(self):
        source = self._figure(r"\includegraphics[width=1.2\textwidth]{wide.pdf}" + r"\caption{Kept.}")
        updated, result = fix_figure_width_mismatch(source, "fig:target", "double_column")
        self.assertIsNone(result)
        self.assertEqual(updated, source)
        source = source.replace("{figure}", "{figure*}")
        updated, result = fix_figure_width_mismatch(source, "fig:target", "double_column")
        self.assertIsNotNone(result)
        self.assertEqual(updated, source.replace("1.2", ""))

    def test_commented_graphics_and_captions_are_not_repair_targets(self):
        source = self._figure("% " + r"\includegraphics[width=1.5\linewidth]{example.pdf}" + "\n"
                              + r"\includegraphics[width=\linewidth]{main.pdf}" + r"\caption{Kept.}")
        updated, result = fix_figure_width_mismatch(source, "fig:target")
        self.assertIsNone(result)
        self.assertEqual(updated, source)


def _caption_test_engine():
    tectonic = os.environ.get("PAPERFIT_TEST_TECTONIC") or shutil.which("tectonic")
    if tectonic:
        return [tectonic, "--keep-logs", "--keep-intermediates", "--untrusted", "main.tex"]
    pdflatex = shutil.which("pdflatex")
    return [pdflatex, "-interaction=nonstopmode", "main.tex"] if pdflatex else None


class CaptionGraphicCompileTest(unittest.TestCase):
    @unittest.skipUnless(_caption_test_engine(), "set PAPERFIT_TEST_TECTONIC or install TeX")
    def test_oversized_caption_arrow_real_compile_preserves_payload_and_fonts(self):
        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import DecodedStreamObject, NameObject

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def make_asset(name, width, height):
                writer = PdfWriter()
                page = writer.add_blank_page(width=width, height=height)
                stream = DecodedStreamObject()
                stream.set_data(b"0.8 w 1 2.5 m 26 2.5 l 22 4.5 m 26 2.5 l 22 0.5 l S")
                page[NameObject("/Contents")] = writer._add_object(stream)
                with (root / name).open("wb") as handle:
                    writer.write(handle)
            make_asset("arrow.pdf", 28.75, 5.18)
            make_asset("diagram.pdf", 100, 50)
            caption = r"\caption{A sample trajectory \includegraphics[width=1.28\linewidth]{arrow.pdf} reaches the target with probability $0.95$.}"
            source = (r"\documentclass[twocolumn]{article}" + "\n" + r"\usepackage{graphicx}" + "\n"
                      + r"\begin{document}" + "\n" + r"\begin{figure}[t]" + "\n" + r"\centering" + "\n"
                      + r"\includegraphics[width=.4\linewidth]{diagram.pdf}" + "\n" + caption + "\n"
                      + r"\label{fig:target}" + "\n" + r"\end{figure}" + "\n"
                      + "We measure the success probability in Figure~" + r"\ref{fig:target}." + "\n"
                      + r"\end{document}" + "\n")
            def compile_source(content):
                (root / "main.tex").write_text(content, encoding="utf-8")
                # Native pdfLaTeX needs a second pass to resolve Figure references.
                # Comparing its first-pass bold ?? against a resolved digit would
                # mistake reference convergence for an unwanted font change.
                for _pass in range(2):
                    result = subprocess.run(_caption_test_engine(), cwd=root, capture_output=True, timeout=180,
                                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                    self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace")[-1000:])
                reader = PdfReader(str(root / "main.pdf"))
                font_names = set()
                for page in reader.pages:
                    for font in (page.get("/Resources", {}).get("/Font", {}) or {}).values():
                        font = font.get_object()
                        if font.get("/BaseFont"):
                            font_names.add(re.sub(r"^/[A-Z]{6}\+", "", str(font["/BaseFont"])))
                log = (root / "main.log").read_text(encoding="utf-8", errors="replace")
                self.assertNotIn("There were undefined references", log)
                return log, len(reader.pages), font_names
            before_log, before_pages, before_fonts = compile_source(source)
            self.assertIn(r"Overfull \hbox", before_log)
            updated, result = fix_figure_width_mismatch(source, "fig:target", "double_column")
            self.assertIsNotNone(result)
            self.assertEqual(updated, source.replace("1.28", ""))
            after_log, after_pages, after_fonts = compile_source(updated)
            self.assertNotIn(r"Overfull \hbox", after_log)
            self.assertEqual(before_pages, after_pages)
            self.assertEqual(before_fonts, after_fonts)
            self.assertIn(r"\includegraphics[width=.4\linewidth]{diagram.pdf}", updated)
            self.assertIn(r"probability $0.95$", updated)
            self.assertIn(r"\label{fig:target}", updated)
            body_updated, result = fix_figure_width_mismatch(
                updated, "fig:target", "double_column", visual_width_subtype="underfilled_width")
            self.assertIsNotNone(result)
            self.assertEqual(body_updated, updated.replace("width=.4\\linewidth", "width=\\linewidth"))
            body_log, body_pages, body_fonts = compile_source(body_updated)
            self.assertNotIn(r"Overfull \hbox", body_log)
            self.assertEqual(after_pages, body_pages)
            self.assertEqual(after_fonts, body_fonts)


if __name__ == "__main__":
    unittest.main()
