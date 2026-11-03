"""回归测试：Windows 深路径下 pre-repair 快照与被测论文目录解耦。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from runtime_snapshots import (  # noqa: E402
    ABSOLUTE_MAX_PATH_CHARS,
    SNAPSHOT_ENV_VAR,
    SnapshotPathLengthError,
    create_pre_repair_snapshot,
    restore_snapshot,
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _make_deep_dir(base: Path, target_chars: int) -> Path:
    """构造绝对路径长度 >= target_chars 的目录（每段目录名 <= 48 字符，NTFS 可创建）。"""
    path = base.resolve()
    while len(str(path)) < target_chars:
        remaining = target_chars - len(str(path))
        width = max(8, min(48, remaining))
        part = ("deep_case_dir_" + "x" * 40)[:width]
        path = path / part
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_paper(root: Path) -> None:
    (root / "sections").mkdir(exist_ok=True)
    (root / "main.tex").write_text(
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "\\input{sections/intro}\n"
        "\\bibliography{refs}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    (root / "sections" / "intro.tex").write_text("Intro text.\n", encoding="utf-8")
    (root / "refs.bib").write_text("@article{x, title={X}}\n", encoding="utf-8")


class LongPathSnapshotTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        # 隔离环境变量，保证测试分支确定（不继承外部 PAPERFIT_SNAPSHOT_ROOT）。
        env_patcher = mock.patch.dict(os.environ)
        env = env_patcher.start()
        env.pop(SNAPSHOT_ENV_VAR, None)
        self.addCleanup(env_patcher.stop)

    def test_deep_project_auto_switches_to_short_external_root_and_rollback_sha_matches(self) -> None:
        """200+ 字符源路径：默认根放不下 → 自动切短外部根，回滚后 SHA 与清单一致。"""
        if len(tempfile.gettempdir()) > 150:
            self.skipTest("system temp dir too long for deterministic auto-switch test")
        root = _make_deep_dir(self.tmp / "case", 208)
        _write_paper(root)
        # 源路径（绝对路径）确实超过 200 字符。
        self.assertGreaterEqual(len(str(root / "main.tex")), 200)
        # 默认根下目标路径必然超过 220（触发自动切换的先决条件）。
        default_planned = (
            len(str(root / "data" / "snapshots" / "pre_repair_0123456789" / "files" / "0123456789abcdef.tex"))
        )
        self.assertGreater(default_planned, 220)

        manifest = create_pre_repair_snapshot(project_root=root, main_tex="main.tex")

        manifest_path = Path(manifest["rollback_target"])
        self.assertTrue(manifest_path.is_absolute())
        self.assertTrue(manifest_path.is_file())
        # 快照与被测论文目录解耦：manifest 落在 project_root 之外。
        self.assertNotIn(root, manifest_path.resolve().parents)
        self.assertFalse((root / "data" / "snapshots").exists())

        persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["snapshot_id"], manifest["snapshot_id"])
        self.assertTrue(persisted["files"])
        for item in persisted["files"]:
            self.assertIn("mtime", item)
            self.assertIn("bytes", item)
            self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")
            # 清单只存相对路径，不把绝对路径假设塞进文件条目。
            self.assertFalse(Path(item["path"]).is_absolute())

        # 突变源文件后回滚。
        (root / "main.tex").write_text("mutated main\n", encoding="utf-8")
        (root / "sections" / "intro.tex").unlink()
        (root / "refs.bib").write_text("mutated bib\n", encoding="utf-8")

        report = restore_snapshot(project_root=root, rollback_target=manifest["rollback_target"])
        self.assertEqual(report["snapshot_id"], manifest["snapshot_id"])
        self.assertEqual(len(report["restored_files"]), len(persisted["files"]))
        # 验收：rollback 后每个文件 SHA 与清单一致。
        for item in persisted["files"]:
            restored = root / item["path"]
            self.assertTrue(restored.is_file(), item["path"])
            self.assertEqual(_sha256_file(restored), item["sha256"], item["path"])

    def test_env_snapshot_root_directs_snapshot_to_explicit_tmp_dir(self) -> None:
        """PAPERFIT_SNAPSHOT_ROOT / 显式 snapshot_root 参数 → 快照落在指定短外部根。"""
        external = self.tmp / "explicit_snapshot_root"
        root = _make_deep_dir(self.tmp / "case_env", 205)
        _write_paper(root)
        self.assertGreaterEqual(len(str(root / "main.tex")), 200)

        with mock.patch.dict(os.environ, {SNAPSHOT_ENV_VAR: str(external)}):
            manifest = create_pre_repair_snapshot(project_root=root, main_tex="main.tex")

        manifest_path = Path(manifest["rollback_target"])
        self.assertTrue(manifest_path.is_absolute())
        self.assertEqual(
            external.resolve(),
            manifest_path.parent.parent.resolve(),
            "manifest 应位于显式外部根/<snapshot_id>/ 下",
        )

        # snapshot_root 参数与环境变量等价。
        external_param = self.tmp / "explicit_snapshot_root_param"
        manifest2 = create_pre_repair_snapshot(
            project_root=root, main_tex="main.tex", snapshot_root=str(external_param)
        )
        self.assertEqual(
            external_param.resolve(),
            Path(manifest2["rollback_target"]).parent.parent.resolve(),
        )

        # 环境变量路径下的快照可正常回滚，SHA 与清单一致。
        (root / "main.tex").write_text("mutated main\n", encoding="utf-8")
        restore_snapshot(project_root=root, rollback_target=manifest["rollback_target"])
        for item in manifest["files"]:
            self.assertEqual(_sha256_file(root / item["path"]), item["sha256"], item["path"])

    def test_over_limit_paths_fail_cleanly_without_partial_snapshot(self) -> None:
        """目标路径超 240 字符：明确失败（path_length_blocked），不留半套快照。"""
        root = self.tmp / "shallow"
        root.mkdir()
        _write_paper(root)
        # 显式外部根长到任何目标路径都超过 240 字符（预检发生在写入前，目录无需真实存在）。
        long_root = self.tmp.joinpath(*["L" * 40 for _ in range(6)])

        with self.assertRaises(SnapshotPathLengthError) as ctx:
            create_pre_repair_snapshot(project_root=root, main_tex="main.tex", snapshot_root=long_root)

        self.assertIn("path_length_blocked", str(ctx.exception))
        details = ctx.exception.to_dict()
        self.assertTrue(details["path_length_blocked"])
        self.assertTrue(details["blocked_paths"])
        self.assertTrue(all(len(p) > ABSOLUTE_MAX_PATH_CHARS for p in details["blocked_paths"]))
        # 明确失败而非半套：外部根未被创建，项目内也没有 data/snapshots 残留。
        self.assertFalse(long_root.exists())
        self.assertFalse((root / "data" / "snapshots").exists())

    def test_failed_copy_leaves_no_partial_snapshot(self) -> None:
        """中途复制失败：整体清理本次快照目录，不留半套。"""
        root = self.tmp / "shallow2"
        root.mkdir()
        _write_paper(root)
        external = self.tmp / "explicit_root_cleanup"
        original_copy2 = shutil.copy2
        calls = {"count": 0}

        def flaky_copy2(src, dst, **kwargs):
            calls["count"] += 1
            if calls["count"] > 1:
                raise OSError("simulated copy failure")
            return original_copy2(src, dst, **kwargs)

        with mock.patch("runtime_snapshots.shutil.copy2", flaky_copy2):
            with self.assertRaises(OSError):
                create_pre_repair_snapshot(
                    project_root=root, main_tex="main.tex", snapshot_root=str(external)
                )

        self.assertGreater(calls["count"], 1)
        if external.exists():
            self.assertEqual(list(external.glob("*/snapshot_manifest.json")), [])
        self.assertFalse((root / "data" / "snapshots").exists())

    def test_default_root_behavior_unchanged_for_shallow_project(self) -> None:
        """默认行为兼容：浅路径 + 未设环境变量 → 项目内 data/snapshots，旧契约字段不变。"""
        root = self.tmp / "plain"
        root.mkdir()
        _write_paper(root)

        manifest = create_pre_repair_snapshot(
            project_root=root, main_tex="main.tex", snapshot_id="legacy_style_snapshot"
        )

        rollback_target = Path(manifest["rollback_target"])
        self.assertFalse(rollback_target.is_absolute())
        manifest_path = root / rollback_target
        self.assertTrue(manifest_path.is_file())
        self.assertEqual(
            manifest_path,
            root / "data" / "snapshots" / "legacy_style_snapshot" / "snapshot_manifest.json",
        )
        persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [item["path"] for item in persisted["files"]],
            ["main.tex", "refs.bib", "sections/intro.tex"],
        )
        for item in persisted["files"]:
            # 旧契约：backup_path 相对 project_root 且文件真实存在。
            self.assertTrue((root / item["backup_path"]).is_file(), item["backup_path"])
            self.assertIn("mtime", item)
            self.assertIn("bytes", item)

        (root / "main.tex").write_text("mutated main\n", encoding="utf-8")
        restore_snapshot(project_root=root, rollback_target=manifest["rollback_target"])
        self.assertIn("\\documentclass{article}", (root / "main.tex").read_text(encoding="utf-8"))
        for item in persisted["files"]:
            self.assertEqual(_sha256_file(root / item["path"]), item["sha256"], item["path"])


    def test_legacy_manifest_with_only_backup_path_still_restores(self) -> None:
        """旧格式清单（只有 backup_path、镜像相对路径布局）仍可回滚，不被新格式破坏。"""
        root = self.tmp / "legacy"
        root.mkdir()
        _write_paper(root)

        manifest = create_pre_repair_snapshot(
            project_root=root, main_tex="main.tex", snapshot_id="legacy_fmt"
        )
        manifest_path = Path(manifest["rollback_target"])
        manifest_path = manifest_path if manifest_path.is_absolute() else root / manifest_path
        persisted = json.loads(manifest_path.read_text(encoding="utf-8"))

        # 手工降级为旧格式：删掉 backup 字段，把备份挪回镜像相对路径布局。
        legacy_entries = []
        for item in persisted["files"]:
            legacy_backup = manifest_path.parent / "files_mirror" / item["path"]
            legacy_backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(manifest_path.parent / item["backup"], legacy_backup)
            legacy_entries.append(
                {
                    "path": item["path"],
                    "backup_path": (
                        manifest_path.parent.relative_to(root) / "files_mirror" / item["path"]
                    ).as_posix(),
                    "sha256": item["sha256"],
                }
            )
        persisted["files"] = legacy_entries
        persisted.pop("path_length_policy", None)
        manifest_path.write_text(json.dumps(persisted, indent=2, ensure_ascii=False), encoding="utf-8")

        (root / "main.tex").write_text("mutated main\n", encoding="utf-8")
        report = restore_snapshot(
            project_root=root,
            rollback_target=manifest["rollback_target"],
        )
        self.assertEqual(len(report["restored_files"]), len(legacy_entries))
        for item in legacy_entries:
            self.assertEqual(_sha256_file(root / item["path"]), item["sha256"], item["path"])


if __name__ == "__main__":
    unittest.main()
