from __future__ import annotations

import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from regression.pack_builder import build_pack, find_main_tex, resolve_case_dir  # noqa: E402


PAPER_MAIN = (
    "\\documentclass{article}\n"
    "\\usepackage{graphicx}\n"
    "\\begin{document}\n"
    "This paper studies layout repair on real documents with enough plain words.\n"
    "\\begin{figure}[t]\n\\includegraphics[width=0.9\\linewidth]{fig.pdf}\n\\end{figure}\n"
    "\\begin{table}[h]\\begin{tabular}{ll}\na & b \\\\\n\\end{tabular}\\end{table}\n"
    "Second paragraph with sufficient words for the injection point to exist.\n"
    "\\end{document}\n"
)


def _make_archive(root: Path, name: str = "example-paper-05") -> Path:
    source_dir = root / "src" / name
    (source_dir / "figs").mkdir(parents=True)
    (source_dir / "figs" / "fig.pdf").write_bytes(b"%PDF")
    (source_dir / "paper.tex").write_text(PAPER_MAIN, encoding="utf-8")
    (source_dir / "extra.tex").write_text("% fragment, no documentclass\nfragment\n", encoding="utf-8")
    archive = root / f"{name}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source_dir, arcname=name)
    return archive


def _fake_compile_ok(project_root: Path) -> dict:
    (project_root / "main.pdf").write_bytes(b"%PDF fake")
    return {"success": True}


def _fake_render_ok(project_root: Path, pages_dir: Path) -> dict:
    pages_dir.mkdir(parents=True, exist_ok=True)
    (pages_dir / "page_001.png").write_bytes(b"png1")
    (pages_dir / "page_002.png").write_bytes(b"png2")
    return {"success": True, "page_count": 2}


class FindMainTexTest(unittest.TestCase):
    def test_prefers_documentclass_paper_over_fragment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            archive = _make_archive(root)
            from regression.pack_builder import extract_archive

            project = extract_archive(archive, root / "extracted")
            main = find_main_tex(project)
            self.assertIsNotNone(main)
            self.assertEqual(main.name, "paper.tex")


class BuildPackTest(unittest.TestCase):
    def test_successful_case_freezes_everything(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            archive = _make_archive(root)
            pack_root = root / "pack"
            manifest = build_pack(
                entries=[
                    {
                        "archive": str(archive),
                        "paper_id": "AAAI",
                        "case_id": "example-paper-05",
                        "venue_id": "AAAI",
                        "perturbation_ids": ["D1_long_token_overflow", "B2_shrink_graphics"],
                        "seed": 42,
                        "target_pages": 9,
                        "profile": "hard",
                    }
                ],
                pack_root=pack_root,
                compile_fn=_fake_compile_ok,
                render_fn=_fake_render_ok,
            )
            self.assertEqual(manifest["summary"]["success"], 1)
            case_dir = resolve_case_dir(pack_root, "AAAI", "example-paper-05")
            # 冻结工件齐全
            source_manifest = json.loads((case_dir / "source_manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(source_manifest["page_hashes"])
            self.assertIn("perturbed_main_tex_sha256", source_manifest)
            self.assertEqual(len(source_manifest["page_hashes"]), 2)
            benchmark = json.loads((case_dir / "benchmark_case.json").read_text(encoding="utf-8"))
            self.assertEqual(benchmark["benchmark_case"]["requested_perturbations"], ["D1_long_token_overflow", "B2_shrink_graphics"])
            self.assertTrue(benchmark["benchmark_case"]["frozen"])
            # 扰动确实落在冻结工程里
            b0_main = (case_dir / "b0_project" / "paper.tex").read_text(encoding="utf-8")
            self.assertIn("PerturbationStressToken", b0_main)
            self.assertIn("width=0.45\\linewidth", b0_main)

    def test_injection_failure_recorded_not_silent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # 构造一个没有 includegraphics 的包 → B2 注入失败
            source_dir = root / "src" / "bare"
            source_dir.mkdir(parents=True)
            (source_dir / "paper.tex").write_text(
                "\\documentclass{article}\n\\begin{document}\nplain text only no targets for images\n\\end{document}\n",
                encoding="utf-8",
            )
            archive = root / "bare.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(source_dir, arcname="bare")

            manifest = build_pack(
                entries=[
                    {
                        "archive": str(archive),
                        "paper_id": "ICLR",
                        "case_id": "bare-case",
                        "venue_id": "ICLR",
                        "perturbation_ids": ["B2_shrink_graphics"],
                        "seed": 1,
                    }
                ],
                pack_root=root / "pack",
                compile_fn=_fake_compile_ok,
                render_fn=_fake_render_ok,
            )
            self.assertEqual(manifest["summary"]["injection_failed"], 1)
            self.assertEqual(manifest["case_results"][0]["status"], "injection_failed")
            failure_manifest = json.loads(
                (resolve_case_dir(root / "pack", "ICLR", "bare-case") / "source_manifest.json").read_text(encoding="utf-8")
            )
            self.assertIn("failed perturbations: B2_shrink_graphics", failure_manifest["failure"]["reasons"])

    def test_compile_failure_recorded(self):
        def compile_fails(project_root: Path) -> dict:
            return {"success": False, "reason": "missing package"}

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            archive = _make_archive(root)
            manifest = build_pack(
                entries=[
                    {
                        "archive": str(archive),
                        "paper_id": "AAAI",
                        "case_id": "case-c",
                        "venue_id": "AAAI",
                        "perturbation_ids": ["D1_long_token_overflow"],
                        "seed": 7,
                    }
                ],
                pack_root=root / "pack",
                compile_fn=compile_fails,
                render_fn=_fake_render_ok,
            )
            self.assertEqual(manifest["summary"]["compile_failed"], 1)
            self.assertEqual(manifest["case_results"][0]["status"], "compile_failed")
            self.assertEqual(manifest["case_results"][0]["injection_status"], "success")


if __name__ == "__main__":
    unittest.main()
