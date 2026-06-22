from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.runtime_snapshots import create_pre_repair_snapshot, discover_source_files, restore_snapshot


class RuntimeSnapshotsTest(unittest.TestCase):
    def test_pre_repair_snapshot_captures_main_inputs_and_bibliography(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "sections").mkdir()
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

            discovered = [path.relative_to(root).as_posix() for path in discover_source_files(root, "main.tex")]
            self.assertEqual(discovered, ["main.tex", "refs.bib", "sections/intro.tex"])

            manifest = create_pre_repair_snapshot(
                project_root=root,
                main_tex="main.tex",
                snapshot_id="test_snapshot",
            )

            manifest_path = root / manifest["rollback_target"]
            self.assertTrue(manifest_path.is_file())
            persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["snapshot_id"], "test_snapshot")
            self.assertEqual(
                [item["path"] for item in persisted["files"]],
                ["main.tex", "refs.bib", "sections/intro.tex"],
            )
            for item in persisted["files"]:
                self.assertTrue((root / item["backup_path"]).is_file())
                self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")

            (root / "main.tex").write_text("mutated main\n", encoding="utf-8")
            (root / "sections" / "intro.tex").unlink()
            (root / "refs.bib").write_text("mutated bib\n", encoding="utf-8")

            report = restore_snapshot(
                project_root=root,
                rollback_target=manifest["rollback_target"],
                output_path="data/rollback_report.json",
            )

            self.assertEqual(report["snapshot_id"], "test_snapshot")
            self.assertEqual(len(report["restored_files"]), 3)
            self.assertIn("\\documentclass{article}", (root / "main.tex").read_text(encoding="utf-8"))
            self.assertEqual((root / "sections" / "intro.tex").read_text(encoding="utf-8"), "Intro text.\n")
            self.assertEqual((root / "refs.bib").read_text(encoding="utf-8"), "@article{x, title={X}}\n")
            self.assertTrue((root / "data" / "rollback_report.json").is_file())


if __name__ == "__main__":
    unittest.main()
