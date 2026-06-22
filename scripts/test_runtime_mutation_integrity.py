from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from runtime_mutation_integrity import build_source_mutation_report  # noqa: E402
from runtime_snapshots import create_pre_repair_snapshot  # noqa: E402


class RuntimeMutationIntegrityTest(unittest.TestCase):
    def test_source_mutation_report_detects_changed_and_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "sections").mkdir()
            (root / "main.tex").write_text(
                "\\documentclass{article}\n\\begin{document}\n\\input{sections/intro}\n\\end{document}\n",
                encoding="utf-8",
            )
            (root / "sections" / "intro.tex").write_text("Original intro.\n", encoding="utf-8")
            snapshot = create_pre_repair_snapshot(
                project_root=root,
                main_tex="main.tex",
                snapshot_id="mutation_report_snapshot",
            )

            (root / "main.tex").write_text("Mutated main.\n", encoding="utf-8")
            (root / "sections" / "intro.tex").unlink()
            report = build_source_mutation_report(
                project_root=root,
                rollback_target=snapshot["rollback_target"],
                output_path="data/source_mutation_report.json",
            )

            self.assertEqual(report["summary"]["tracked_files"], 2)
            self.assertEqual(report["summary"]["changed_files"], 2)
            self.assertEqual(report["summary"]["missing_files"], 1)
            by_path = {item["path"]: item for item in report["files"]}
            self.assertTrue(by_path["main.tex"]["changed"])
            self.assertFalse(by_path["sections/intro.tex"]["exists"])
            persisted = json.loads((root / "data" / "source_mutation_report.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["snapshot_id"], "mutation_report_snapshot")


if __name__ == "__main__":
    unittest.main()
