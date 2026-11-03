from __future__ import annotations

import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from PIL import Image


SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
for entry in (str(SCRIPTS_DIR), str(REPO_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from regression.mvp_runner import (  # noqa: E402
    choose_perturbation,
    export_b0_side,
    run_mvp,
    select_cases,
)


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


def _make_paperfit_data(root: Path, venues=("AAAI", "ICLR", "ECCV"), per_venue=2) -> Path:
    data_root = root / "paperfit-data"
    cases = []
    index = 0
    for venue in venues:
        raw_dir = data_root / "raw" / venue
        raw_dir.mkdir(parents=True)
        for sub in range(per_venue):
            paper_id = f"arXiv-2601.0000{index}v1"
            src = data_root / "_src" / paper_id
            (src / "figs").mkdir(parents=True)
            (src / "figs" / "fig.pdf").write_bytes(b"%PDF tiny")
            (src / "paper.tex").write_text(PAPER_MAIN, encoding="utf-8")
            archive = raw_dir / f"{paper_id}.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(src, arcname=paper_id)
            cases.append(
                {
                    "case_id": f"{venue}/{paper_id}",
                    "venue_id": venue,
                    "paper_id": paper_id,
                    "target_pages": 8,
                    "profile": "hard" if index % 2 else "normal",
                    "column_type": "double",
                    "seed": 1000 + index,
                    "asset_bytes": 1000 + index * 10,
                    "requested_perturbations": ["D1_long_token_overflow", "B2_shrink_graphics"],
                }
            )
            index += 1
    (data_root / "metadata").mkdir(parents=True)
    (data_root / "metadata" / "cases.jsonl").write_text(
        "\n".join(json.dumps(case) for case in cases) + "\n", encoding="utf-8"
    )
    return data_root


def _fake_compile(project: Path) -> dict:
    main = sorted(project.glob("*.tex"))
    stem = main[0].stem if main else "paper"
    (project / f"{stem}.pdf").write_bytes(b"%PDF fake")
    (project / f"{stem}.log").write_text("Output written on main.pdf (8 pages).\n", encoding="utf-8")
    return {"success": True}


def _fake_render(project: Path, pages_dir: Path) -> dict:
    pages_dir.mkdir(parents=True, exist_ok=True)
    for page in range(1, 3):
        Image.new("RGB", (40, 50), "white").save(pages_dir / f"page_{page:03d}.png")
    return {"success": True, "page_count": 2}


def _fake_paperfit_runner(project: Path, task_path: Path, output_path: Path) -> dict:
    # 模拟一次成功的修复运行：改一下主 tex、渲染页、写 run_result
    main = sorted(project.glob("*.tex"))[0]
    main.write_text(main.read_text(encoding="utf-8") + "% repaired\n", encoding="utf-8")
    (project / f"{main.stem}.log").write_text("Output written on main.pdf (8 pages).\n", encoding="utf-8")
    pages = project / "data" / "pages"
    pages.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 50), "white").save(pages / "page_001.png")
    payload = {
        "status": "done",
        "runtime_actions": {
            "compile": {"success": True, "case_status": "layout_evaluable"},
            "render": {"success": True},
        },
        "failure": None,
    }
    output_path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


class SelectionTest(unittest.TestCase):
    def test_select_cases_stratifies_venues(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_root = _make_paperfit_data(Path(tmpdir), venues=("AAAI", "ICLR", "ECCV"), per_venue=2)
            cases = json.loads(
                (data_root / "metadata" / "cases.jsonl").read_text(encoding="utf-8").splitlines()[0]
            ) and None
            from regression.mvp_runner import load_cases_metadata

            metadata = load_cases_metadata(data_root)
            selected = select_cases(metadata, count=6)
            self.assertEqual(len(selected), 6)
            venues = {case["venue_id"] for case in selected}
            self.assertEqual(venues, {"AAAI", "ICLR", "ECCV"})

    def test_choose_perturbation_balances_types(self):
        case = {"requested_perturbations": ["B2_shrink_graphics", "D1_long_token_overflow"]}
        used = {"D1_long_token_overflow": 3, "B2_shrink_graphics": 0}
        self.assertEqual(choose_perturbation(case, used_counts=used), "B2_shrink_graphics")


class RunMvpEndToEndTest(unittest.TestCase):
    def test_full_pipeline_with_fakes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            data_root = _make_paperfit_data(root, venues=("AAAI", "ICLR"), per_venue=2)
            result = run_mvp(
                paperfit_data=data_root,
                out_root=root / "regression_runs",
                case_count=4,
                max_rounds=1,
                apply=True,
                paperfit_runner=_fake_paperfit_runner,
                compile_fn=_fake_compile,
                render_fn=_fake_render,
            )
            run_dir = Path(result["run_dir"])
            self.assertEqual(result["pack_summary"]["total"], 4)
            self.assertEqual(result["pack_summary"]["success"], 4)
            self.assertEqual(result["decision"], "INCONCLUSIVE")  # VLM 未配置
            report_md = (run_dir / "reports" / "regression_report.md").read_text(encoding="utf-8")
            self.assertIn("裁判未配置", report_md)
            self.assertIn("assigned 4 → injected 4", report_md)
            records = [
                json.loads(line)
                for line in (run_dir / "case_records.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(records), 4)
            self.assertTrue(all(record["candidate_status"] == "layout_evaluable" for record in records))
            # 双侧 method-root 都有导出
            self.assertTrue((run_dir / "methods" / "B0" / "AAAI").is_dir())
            self.assertTrue((run_dir / "methods" / "OURS" / "AAAI").is_dir())
            b0_report = list((run_dir / "methods" / "B0").glob("*/*/reports/evaluation_report.json"))
            ours_report = list((run_dir / "methods" / "OURS").glob("*/*/reports/evaluation_report.json"))
            self.assertEqual(len(b0_report), 4)
            self.assertEqual(len(ours_report), 4)
            manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["applied_cases"], 4)


if __name__ == "__main__":
    unittest.main()
