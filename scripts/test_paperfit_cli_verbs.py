from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
CLI = REPO_ROOT / "bin" / "paperfit.js"


def _run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["node", str(CLI), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=120,
    )


def _write_snapshot(project: Path, main_content: str) -> str:
    """Create a minimal pre-repair snapshot and return its manifest path."""
    snapshot_dir = project / "data" / "snapshots" / "pre_repair_test"
    files_dir = snapshot_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    backup = files_dir / "main.tex"
    backup.write_text(main_content, encoding="utf-8", newline="\n")
    # Hash the bytes actually on disk so platform newline handling cannot
    # invalidate the manifest.
    sha = hashlib.sha256(backup.read_bytes()).hexdigest()
    manifest = {
        "schema_version": "1.0",
        "snapshot_id": "pre_repair_test",
        "project_root": str(project.resolve()),
        "files": [
            {
                "path": "main.tex",
                "backup_path": "data/snapshots/pre_repair_test/files/main.tex",
                "bytes": backup.stat().st_size,
                "sha256": sha,
            }
        ],
    }
    manifest_path = snapshot_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8", newline="\n")
    return "data/snapshots/pre_repair_test/manifest.json"


class CliRollbackVerbTest(unittest.TestCase):
    def test_rollback_restores_file_from_state_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project = Path(tmpdir)
            (project / "data").mkdir()
            original = "\\documentclass{article}\noriginal\n"
            target = _write_snapshot(project, original)
            (project / "main.tex").write_text("\\documentclass{article}\nmutated\n", encoding="utf-8")
            (project / "data" / "state.json").write_text(
                json.dumps({"content_integrity": {"rollback_target": target}}),
                encoding="utf-8",
            )

            result = _run_cli(["rollback"], project)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("original", (project / "main.tex").read_text(encoding="utf-8"))
            report = json.loads((project / "data" / "rollback_report.json").read_text(encoding="utf-8"))
            self.assertTrue(report.get("restored_files"))

    def test_rollback_without_target_fails_with_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project = Path(tmpdir)
            (project / "data").mkdir()
            result = _run_cli(["rollback"], project)
            self.assertEqual(result.returncode, 1)
            self.assertIn("回滚目标", result.stderr)


class CliResumeVerbTest(unittest.TestCase):
    def test_resume_without_task_file_fails_with_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project = Path(tmpdir)
            (project / "data").mkdir()
            result = _run_cli(["resume"], project)
            self.assertEqual(result.returncode, 1)
            self.assertIn("task.json", result.stderr)


if __name__ == "__main__":
    unittest.main()
