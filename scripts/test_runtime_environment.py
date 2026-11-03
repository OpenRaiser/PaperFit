from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import orchestrator_runtime  # noqa: E402
import paperfit_command  # noqa: E402
from runtime_actions import collect_runtime_environment  # noqa: E402


class CollectRuntimeEnvironmentTest(unittest.TestCase):
    def test_snapshot_contains_versions_and_toolchain(self) -> None:
        snapshot = collect_runtime_environment()
        self.assertEqual(snapshot["schema_version"], "1.0")
        self.assertTrue(snapshot["python_version"])
        self.assertTrue(snapshot["platform"])
        for tool in ("latexmk", "pdflatex", "bibtex", "pdfinfo", "pdftotext"):
            self.assertIn(tool, snapshot["toolchain"])
            self.assertIsInstance(snapshot["toolchain"][tool], bool)


class RunResultEnvironmentTest(unittest.TestCase):
    def test_visual_only_run_result_records_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "data").mkdir()
            main_tex = root / "main.tex"
            main_tex.write_text(
                "\\documentclass{article}\n\\begin{document}\nEnv fixture.\n\\end{document}\n",
                encoding="utf-8",
            )

            def fake_core(*args, **kwargs):
                from orchestrator_runtime import OrchestratorRuntime

                runtime = OrchestratorRuntime(state_path=str(root / "data" / "state.json"))
                runtime.manager.update(
                    {"status": "BLOCKED", "last_gatekeeper_decision": "BLOCKED"}
                )
                return runtime.manager.load()

            with (
                mock.patch.object(
                    orchestrator_runtime,
                    "compile_latex",
                    return_value={"success": False, "timeout": False, "case_status": "source_compile_error"},
                ),
                mock.patch.object(orchestrator_runtime.OrchestratorRuntime, "_run_round_core", fake_core),
            ):
                report = paperfit_command._run_visual_only(
                    root,
                    main_tex=main_tex,
                    template=None,
                    target_pages=None,
                )

            environment = report["run_result"]["environment"]
            self.assertEqual(environment["python_version"], sys.version.split()[0])
            self.assertIn("toolchain", environment)
            on_disk = json.loads((root / "data" / "run_result_check_visual.json").read_text(encoding="utf-8"))
            self.assertEqual(on_disk["environment"]["python_version"], environment["python_version"])


if __name__ == "__main__":
    unittest.main()
