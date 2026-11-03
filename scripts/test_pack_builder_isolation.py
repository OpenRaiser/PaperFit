"回归：pack_builder 整批隔离——单 case 失败不许中止 10-case/200-case 批量。"

from __future__ import annotations

import io
import json
import shutil
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from regression.pack_builder import (  # noqa: E402
    STAGE_ORDER,
    build_pack,
    case_dir_key,
    extract_archive,
    resolve_case_dir,
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

CASE_IDS = [f"arXiv-2601.0000{index}v1" for index in range(10)]


def _make_archive(root: Path, name: str, with_cache_junk: bool = False) -> Path:
    source_dir = root / "src" / name
    (source_dir / "figs").mkdir(parents=True)
    (source_dir / "figs" / "fig.pdf").write_bytes(b"%PDF")
    (source_dir / "paper.tex").write_text(PAPER_MAIN, encoding="utf-8")
    if with_cache_junk:
        for junk in ("paper.aux", "paper.log", "paper.fls", "paper.fdb_latexmk", "refs.bbl", "refs.blg", "paper.out", "paper.synctex.gz"):
            (source_dir / junk).write_text("junk\n", encoding="utf-8")
        (source_dir / "out").mkdir()
        (source_dir / "out" / "paper.pdf").write_bytes(b"%PDF junk")
        (source_dir / "validation_artifacts").mkdir()
        (source_dir / "validation_artifacts" / "art.json").write_text("{}", encoding="utf-8")
        (source_dir / "data" / "pages").mkdir(parents=True)
        (source_dir / "data" / "pages" / "page_001.png").write_bytes(b"png-stale")
    archive = root / f"{name}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source_dir, arcname=name)
    return archive


def _fake_compile_ok(project_root: Path) -> dict:
    (project_root / "paper.pdf").write_bytes(b"%PDF fake")
    return {"success": True}


def _fake_render_ok(project_root: Path, pages_dir: Path) -> dict:
    pages_dir.mkdir(parents=True, exist_ok=True)
    (pages_dir / "page_001.png").write_bytes(b"png1")
    (pages_dir / "page_002.png").write_bytes(b"png2")
    return {"success": True, "page_count": 2}


def _entries(root: Path, case_ids=CASE_IDS, with_cache_junk: bool = False) -> list:
    return [
        {
            "archive": str(_make_archive(root, case_id, with_cache_junk=with_cache_junk)),
            "paper_id": "CVPRICCV",
            "case_id": case_id,
            "venue_id": "CVPRICCV",
            "perturbation_ids": ["D1_long_token_overflow"],
            "seed": index,
            "target_pages": 9,
            "profile": "hard",
        }
        for index, case_id in enumerate(case_ids)
    ]


class PackBatchIsolationTest(unittest.TestCase):
    """单 case 失败只落该 case 终态，整批继续并留完整 pack_manifest.json。"""

    def test_third_case_copy_failure_does_not_abort_batch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pack_root = root / "pack"
            failing_key = case_dir_key(CASE_IDS[2])  # 人为让第 3 个 case 的 copytree 复现 WinError 3
            real_copytree = shutil.copytree

            def flaky_copytree(src, dst, *args, **kwargs):
                if Path(dst).parent.name == failing_key:
                    raise OSError(
                        3,
                        "The system cannot find the path specified",
                        "images\\s16_1p5b_dataset_breakdown_tipadapter_cleanest.pdf",
                    )
                return real_copytree(src, dst, *args, **kwargs)

            with mock.patch("regression.pack_builder.shutil.copytree", new=flaky_copytree):
                manifest = build_pack(
                    entries=_entries(root),
                    pack_root=pack_root,
                    compile_fn=_fake_compile_ok,
                    render_fn=_fake_render_ok,
                )

            # 10 个 case 全部有终态；失败不被静默丢弃
            self.assertEqual(len(manifest["case_results"]), 10)
            by_case = {item["case_id"]: item for item in manifest["case_results"]}
            self.assertEqual(set(by_case), set(CASE_IDS))
            for case_id in CASE_IDS:
                expected = "pack_copy_failed" if case_id == CASE_IDS[2] else "success"
                self.assertEqual(by_case[case_id]["status"], expected, case_id)
            self.assertEqual(
                manifest["summary"],
                {
                    "total": 10,
                    "success": 9,
                    "pack_copy_failed": 1,
                    "injection_failed": 0,
                    "compile_failed": 0,
                    "render_failed": 0,
                    "input_incomplete": 0,
                    "builder_error": 0,
                },
            )

            # 失败 case 的 stage_status：copy=failed，其后全部 skipped
            failed_stage = by_case[CASE_IDS[2]]["stage_status"]
            self.assertEqual(failed_stage["copy"], "failed")
            for stage in ("source_validation", "compile", "render", "ready"):
                self.assertEqual(failed_stage[stage], "skipped")
            self.assertIn("copytree", " ".join(by_case[CASE_IDS[2]]["reasons"]))

            # 成功 case 的 stage_status 全 ok；失败 case 的 source_manifest.json 已写
            for case_id in (CASE_IDS[0], CASE_IDS[1], *CASE_IDS[3:]):
                self.assertEqual(
                    by_case[case_id]["stage_status"],
                    dict.fromkeys(STAGE_ORDER, "ok"),
                    case_id,
                )
                case_dir = resolve_case_dir(pack_root, "CVPRICCV", case_id)
                self.assertTrue((case_dir / "pages" / "page_001.png").is_file(), case_id)
                benchmark = json.loads((case_dir / "benchmark_case.json").read_text(encoding="utf-8"))
                self.assertEqual(benchmark["benchmark_case"]["case_id"], case_id)  # 人类可读 id 保留
            failed_manifest = json.loads(
                (resolve_case_dir(pack_root, "CVPRICCV", CASE_IDS[2]) / "source_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(failed_manifest["failure"]["status"], "pack_copy_failed")
            self.assertEqual(failed_manifest["stage_status"]["copy"], "failed")
            
            self.assertIn("cannot find the path specified", failed_manifest["failure"]["error"])
            self.assertIn("s16_1p5b_dataset_breakdown", failed_manifest["failure"]["error"])
            self.assertEqual(failed_manifest["case_id"], CASE_IDS[2])

            # 磁盘上的 pack_manifest.json 与返回值一致、含全部 10 个终态、无 .tmp 残留
            on_disk = json.loads((pack_root / "pack_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual([item["case_id"] for item in on_disk["case_results"]], CASE_IDS)
            self.assertEqual(on_disk["summary"]["success"], 9)
            self.assertEqual(on_disk["summary"]["pack_copy_failed"], 1)
            self.assertEqual(list(pack_root.glob("pack_manifest.json.*.tmp")), [])

            # 物理目录是 12 位短 key，不是人类可读 case_id
            self.assertTrue((pack_root / "CVPRICCV" / failing_key).is_dir())
            self.assertFalse((pack_root / "CVPRICCV" / CASE_IDS[2]).exists())
            for case_id in CASE_IDS:
                self.assertTrue((pack_root / "CVPRICCV" / case_dir_key(case_id)).is_dir(), case_id)

    def test_compile_crash_of_one_case_is_isolated(self):
        def compile_crashes_on_case2(project_root: Path) -> dict:
            if project_root.parent.name == case_dir_key(CASE_IDS[1]):
                raise RuntimeError("latexmk exploded")
            return _fake_compile_ok(project_root)

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pack_root = root / "pack"
            manifest = build_pack(
                entries=_entries(root),
                pack_root=pack_root,
                compile_fn=compile_crashes_on_case2,
                render_fn=_fake_render_ok,
            )
            statuses = {item["case_id"]: item["status"] for item in manifest["case_results"]}
            self.assertEqual(statuses[CASE_IDS[1]], "compile_failed")
            self.assertEqual(sum(1 for status in statuses.values() if status == "success"), 9)
            failed = next(item for item in manifest["case_results"] if item["case_id"] == CASE_IDS[1])
            self.assertEqual(failed["stage_status"]["compile"], "failed")
            self.assertEqual(failed["stage_status"]["render"], "skipped")
            self.assertEqual(failed["stage_status"]["ready"], "skipped")

    def test_missing_archive_is_input_incomplete_not_batch_abort(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            entries = _entries(root)
            entries[4]["archive"] = str(root / "nope" / "missing.tar.gz")  # 第 5 个 case 归档缺失
            manifest = build_pack(
                entries=entries,
                pack_root=root / "pack",
                compile_fn=_fake_compile_ok,
                render_fn=_fake_render_ok,
            )
            statuses = {item["case_id"]: item["status"] for item in manifest["case_results"]}
            self.assertEqual(statuses[CASE_IDS[4]], "input_incomplete")
            self.assertEqual(manifest["summary"]["success"], 9)
            self.assertEqual(manifest["summary"]["input_incomplete"], 1)
            failure_manifest = json.loads(
                (
                    resolve_case_dir(root / "pack", "CVPRICCV", CASE_IDS[4]) / "source_manifest.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(failure_manifest["failure"]["status"], "input_incomplete")


class PackHygieneTest(unittest.TestCase):
    """copytree ignore 过滤与解包防穿越。"""

    def test_copytree_ignores_latex_cache_and_runtime_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            manifest = build_pack(
                entries=_entries(root, [CASE_IDS[0]], with_cache_junk=True),
                pack_root=root / "pack",
                compile_fn=_fake_compile_ok,
                render_fn=_fake_render_ok,
            )
            self.assertEqual(manifest["summary"]["success"], 1)
            b0 = resolve_case_dir(root / "pack", "CVPRICCV", CASE_IDS[0]) / "b0_project"
            for junk in (
                "paper.aux",
                "paper.log",
                "paper.fls",
                "paper.fdb_latexmk",
                "refs.bbl",
                "refs.blg",
                "paper.out",
                "paper.synctex.gz",
                "out",
                "validation_artifacts",
                "data/pages",  # data 目录本身保留，只滤其中的运行页图
            ):
                self.assertFalse((b0 / junk).exists(), junk)
            # 作者源码与图片保留，扰动仍生效
            self.assertTrue((b0 / "figs" / "fig.pdf").is_file())
            self.assertIn("PerturbationStressToken", (b0 / "paper.tex").read_text(encoding="utf-8"))

    def test_extract_archive_rejects_path_traversal_member(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            archive = root / "evil.tar.gz"
            payload = b"escaped\n"
            with tarfile.open(archive, "w:gz") as tar:
                info = tarfile.TarInfo(name="../evil_escaped.txt")
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
            dest = root / "pack" / "CVPRICCV" / case_dir_key(CASE_IDS[0]) / "b0_project"
            with self.assertRaises(RuntimeError):
                extract_archive(archive, dest)
            self.assertFalse((root / "pack" / "evil_escaped.txt").exists())

    def test_traversal_archive_is_recorded_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            archive = root / "evil.tar.gz"
            payload = b"escaped\n"
            with tarfile.open(archive, "w:gz") as tar:
                info = tarfile.TarInfo(name="../evil_escaped.txt")
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
            good = _make_archive(root, CASE_IDS[0])
            manifest = build_pack(
                entries=[
                    {"archive": str(good), "paper_id": "CVPRICCV", "case_id": CASE_IDS[0], "venue_id": "CVPRICCV",
                     "perturbation_ids": ["D1_long_token_overflow"], "seed": 1},
                    {"archive": str(archive), "paper_id": "CVPRICCV", "case_id": "evil-case", "venue_id": "CVPRICCV",
                     "perturbation_ids": ["D1_long_token_overflow"], "seed": 2},
                ],
                pack_root=root / "pack",
                compile_fn=_fake_compile_ok,
                render_fn=_fake_render_ok,
            )
            statuses = {item["case_id"]: item["status"] for item in manifest["case_results"]}
            self.assertEqual(statuses[CASE_IDS[0]], "success")
            self.assertEqual(statuses["evil-case"], "pack_copy_failed")
            self.assertFalse((root / "pack" / "evil_escaped.txt").exists())


class ResolveCaseDirTest(unittest.TestCase):
    def test_short_key_layout_and_legacy_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pack_root = Path(tmpdir)
            short = pack_root / "CVPRICCV" / case_dir_key(CASE_IDS[0])
            short.mkdir(parents=True)
            self.assertEqual(resolve_case_dir(pack_root, "CVPRICCV", CASE_IDS[0]), short)
            # 旧版 pack（目录名=人类可读 case_id）仍可定位
            legacy = pack_root / "ICLR" / CASE_IDS[1]
            legacy.mkdir(parents=True)
            self.assertEqual(resolve_case_dir(pack_root, "ICLR", CASE_IDS[1]), legacy)
            # 都不存在时返回短 key 路径（调用方写终态用）
            fresh = pack_root / "AAAI" / case_dir_key("brand-new")
            self.assertEqual(resolve_case_dir(pack_root, "AAAI", "brand-new"), fresh)


if __name__ == "__main__":
    unittest.main()
