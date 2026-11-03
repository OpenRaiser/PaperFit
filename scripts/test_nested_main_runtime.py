from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import paperfit_command
import runtime_actions


class NestedMainRuntimeTest(unittest.TestCase):
    def test_compilers_use_main_directory_and_preserve_root_artifacts(self) -> None:
        for module, compile_name, runner_name, counter_name, bounded_name in (
            (runtime_actions, "compile_latex", "run_command", "pdf_page_count", "compile_latex_bounded"),
            (paperfit_command, "_compile", "_run", "_pdf_page_count", "_compile_bounded"),
        ):
            for bounded in (False, True):
                with self.subTest(module=module.__name__, bounded=bounded), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp).resolve()
                    build = root / "paper"
                    build.mkdir()
                    main = build / "main.tex"
                    main.write_text("\\documentclass{article}\n\\begin{document}Nested\\end{document}", encoding="utf-8")
                    (root / "main.aux").write_text("unrelated root aux", encoding="utf-8")
                    (root / "main.pdf").write_bytes(b"unrelated root pdf")
                    (build / "main.aux").write_text("old nested aux", encoding="utf-8")
                    calls = []

                    def fake_run(command, *, cwd, timeout=None):
                        calls.append((command, cwd))
                        self.assertEqual(cwd, build)
                        self.assertEqual(command[-1], "main.tex")
                        self.assertFalse((build / "main.aux").exists() and len(calls) == 1)
                        (build / "main.pdf").write_bytes(b"current nested pdf")
                        (build / "main.log").write_text("Output written on main.pdf (1 page).", encoding="utf-8")
                        (build / "main.aux").write_text("\\relax", encoding="utf-8")
                        return subprocess.CompletedProcess(command, 0, "Output written on main.pdf (1 page).", "")

                    with (
                        mock.patch.object(module, runner_name, side_effect=fake_run),
                        mock.patch.object(module, counter_name, return_value=1),
                        mock.patch.object(module.shutil, "which", side_effect=lambda name: name),
                        mock.patch.dict(os.environ, {"PAPERFIT_BOUNDED_COMPILE": "0"}),
                    ):
                        if bounded:
                            result = getattr(module, bounded_name)(root, main_tex=main, timeout_sec=60)
                        else:
                            result = getattr(module, compile_name)(root, main_tex=main)
                    self.assertTrue(result["success"])
                    self.assertEqual(Path(result["pdf_path"]), build / "main.pdf")
                    self.assertEqual(result["log_file"], "paper/main.log")
                    self.assertEqual((root / "main.pdf").read_bytes(), b"unrelated root pdf")
                    self.assertEqual((root / "main.aux").read_text(), "unrelated root aux")
                    self.assertEqual(len(calls), 3 if bounded else 1)

    def test_task_specs_preserve_nested_log_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            main = root / "paper" / "main.tex"
            main.parent.mkdir()
            main.write_text("\\documentclass{article}", encoding="utf-8")
            captured = []

            def fake_task(self, *, task_spec, output_path):
                captured.append(task_spec)
                return {"status": "continue", "gatekeeper_decision": "CONTINUE"}

            with mock.patch.object(paperfit_command.OrchestratorRuntime, "run_task", fake_task):
                paperfit_command._run_visual_only(root, main_tex=main, template=None, target_pages=None)
                paperfit_command._run_typed_source_changing(
                    root, task_type="full_vto", main_tex=main, template=None,
                    target_pages=None, max_rounds=1, user_request="fix layout",
                )
            self.assertEqual(len(captured), 2)
            for spec in captured:
                self.assertEqual(spec.log_file, "paper/main.log")
                self.assertEqual(Path(spec.main_tex), Path("paper/main.tex"))

    def test_page_count_uses_nested_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            main = root / "paper" / "main.tex"
            main.parent.mkdir()
            main.with_suffix(".pdf").write_bytes(b"pdf fixture")
            with mock.patch.object(paperfit_command, "_run", return_value=subprocess.CompletedProcess([], 0, "Pages: 7\n", "")) as runner:
                self.assertEqual(paperfit_command._infer_pdf_page_count(root, main), 7)
            self.assertEqual(runner.call_args.args[0][1], str(main.with_suffix(".pdf")))


if __name__ == "__main__":
    unittest.main()
