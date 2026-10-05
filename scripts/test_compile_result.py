"""compile_result.v2 验收：结构化字段、失败分类、签名稳定、journal、旧键兼容。"""

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

from compile_result import (  # noqa: E402
    CompileAttempt,
    classify_failure,
    diff_source_fingerprints,
    error_signature,
    extract_first_error,
    source_fingerprint,
)

LOGS = {
    "missing_sty": "! LaTeX Error: File `tensorboard.sty' not found.\n",
    "missing_cls": "! LaTeX Error: File `iclr2027_conference.cls' not found.\n",
    "missing_font": "! Font \\MUNGUI=umung at 10.95pt not loadable: Metric (TFM) file not found.\n",
    "missing_image": "! LaTeX Error: File `figs/architecture.pdf' not found.\n",
    "missing_input": "! LaTeX Error: File `sections/method.tex' not found.\n",
    "ucs": "! Undefined control sequence.\nl.42 \\makeatletter\n",
    "template": "! Package eccv Error: Package only meant to be used with document class `llncs'\n",
    "source": "! LaTeX Error: There's no line here to end.\n",
    "engine": "This is pdfTeX\n! Emergency stop.\n",
    "timeout": "",
    "unknown": "! Weird new failure mode.\n",
}


def _make_root(td):
    root = Path(td)
    content = "\\documentclass{article}\\begin{document}x\\end{document}\n"
    (root / "main.tex").write_text(content, encoding="utf-8")
    return root


class ClassifyTest(unittest.TestCase):
    def test_success_returns_none(self):
        self.assertIsNone(classify_failure("", success=True, timed_out=False, returncode=0))

    def test_timeout_beats_log_patterns(self):
        self.assertEqual(
            classify_failure(LOGS["ucs"], success=False, timed_out=True, returncode=124), "timed_out"
        )

    def test_missing_package(self):
        self.assertEqual(
            classify_failure(LOGS["missing_sty"], success=False, timed_out=False, returncode=12),
            "missing_package",
        )

    def test_missing_class(self):
        self.assertEqual(
            classify_failure(LOGS["missing_cls"], success=False, timed_out=False, returncode=12),
            "missing_class",
        )

    def test_missing_font(self):
        self.assertEqual(
            classify_failure(LOGS["missing_font"], success=False, timed_out=False, returncode=12),
            "missing_font",
        )

    def test_missing_image(self):
        self.assertEqual(
            classify_failure(LOGS["missing_image"], success=False, timed_out=False, returncode=12),
            "missing_image",
        )

    def test_missing_input(self):
        self.assertEqual(
            classify_failure(LOGS["missing_input"], success=False, timed_out=False, returncode=12),
            "missing_input",
        )

    def test_undefined_control_sequence(self):
        self.assertEqual(
            classify_failure(LOGS["ucs"], success=False, timed_out=False, returncode=12),
            "undefined_control_sequence",
        )

    def test_template_mismatch(self):
        self.assertEqual(
            classify_failure(LOGS["template"], success=False, timed_out=False, returncode=12),
            "template_mismatch",
        )

    def test_generic_source_error(self):
        self.assertEqual(
            classify_failure(LOGS["source"], success=False, timed_out=False, returncode=12),
            "source_error",
        )

    def test_engine_error(self):
        self.assertEqual(
            classify_failure(LOGS["engine"], success=False, timed_out=False, returncode=1),
            "engine_error",
        )

    def test_unrecognized_error_line_is_source_error(self):
        self.assertEqual(
            classify_failure(LOGS["unknown"], success=False, timed_out=False, returncode=1),
            "source_error",
        )

    def test_no_error_line_is_unknown_failure(self):
        self.assertEqual(
            classify_failure("rc=1 with no latex error line at all", success=False, timed_out=False, returncode=1),
            "unknown_failure",
        )


class SignatureTest(unittest.TestCase):
    def test_signature_stable_across_line_numbers(self):
        a = error_signature("! Undefined control sequence.\nl.42 \\foo\n")
        b = error_signature("! Undefined control sequence.\nl.99 \\foo\n")
        self.assertEqual(a, b)
        self.assertIsNotNone(a)
        self.assertEqual(len(a), 12)

    def test_signature_stable_across_paths(self):
        a = error_signature("! LaTeX Error: File `C:\\work\\paper\\tensorboard.sty' not found.")
        b = error_signature("! LaTeX Error: File `D:\\other\\tensorboard.sty' not found.")
        self.assertEqual(a, b)

    def test_different_errors_different_signature(self):
        self.assertNotEqual(error_signature(LOGS["ucs"]), error_signature(LOGS["source"]))

    def test_success_no_signature(self):
        self.assertIsNone(error_signature(""))


class AttemptTest(unittest.TestCase):
    def _root(self, td: str) -> Path:
        root = Path(td)
        (root / "main.tex").write_text("\\documentclass{article}\\begin{document}x\\end{document}\n", encoding="utf-8")
        return root

    def test_success_attempt_full_schema(self):
        with tempfile.TemporaryDirectory() as td:
            root = _make_root(td)
            pdf = root / "main.pdf"
            pdf.write_bytes(b"%PDF-test")
            attempt = CompileAttempt(root, main_tex="main.tex")
            result = attempt.finalize(
                command=["latexmk", "-pdf", "main.tex"],
                cwd=str(root),
                returncode=0,
                timed_out=False,
                stdout="ok",
                stderr="",
                log_text="",
                pdf_path=pdf,
                success=True,
                page_count=3,
                repair_action=None,
            )
            self.assertEqual(result["schema_version"], "compile_result.v2")
            self.assertEqual(result["attempt_id"], "attempt-1")
            self.assertTrue(result["pdf_exists"])
            self.assertTrue(result["pdf_fresh"])
            self.assertEqual(result["page_count"], 3)
            self.assertIsNone(result["failure_class"])
            self.assertIsNone(result["error_signature"])
            self.assertTrue(result["pdf_sha256"])
            # 产物归档
            self.assertTrue((root / "data/compile/attempt-1/stdout.txt").is_file())
            self.assertTrue((root / "data/compile/attempt-1/compile.log").is_file())
            # journal 追加
            journal = (root / "data/compile/journal.jsonl").read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(journal), 1)
            self.assertEqual(json.loads(journal[0])["attempt_id"], "attempt-1")

    def test_attempt_ids_increment(self):
        with tempfile.TemporaryDirectory() as td:
            root = _make_root(td)
            for expected in ("attempt-1", "attempt-2"):
                attempt = CompileAttempt(root, main_tex="main.tex")
                self.assertEqual(attempt.attempt_id, expected)
                attempt.finalize(
                    command=[], cwd=str(root), returncode=0, timed_out=False,
                    pdf_path=None, success=False,
                )

    def test_failure_attempt_carries_class_and_signature(self):
        with tempfile.TemporaryDirectory() as td:
            root = _make_root(td)
            attempt = CompileAttempt(root, main_tex="main.tex")
            result = attempt.finalize(
                command=[], cwd=str(root), returncode=12, timed_out=False,
                log_text=LOGS["missing_sty"], pdf_path=None, success=False,
                repair_action=None,
            )
            self.assertEqual(result["failure_class"], "missing_package")
            self.assertIsNotNone(result["error_signature"])
            self.assertFalse(result["pdf_exists"])

    def test_explicit_attempt_id_round_and_round(self):
        with tempfile.TemporaryDirectory() as td:
            root = _make_root(td)
            attempt = CompileAttempt(root, main_tex="main.tex", attempt_id="round-1-attempt-2")
            result = attempt.finalize(
                command=[], cwd=str(root), returncode=0, timed_out=False,
                pdf_path=None, success=True,
            )
            self.assertEqual(result["attempt_id"], "round-1-attempt-2")


class FingerprintTest(unittest.TestCase):
    def test_changed_files_detected(self):
        with tempfile.TemporaryDirectory() as td:
            root = _make_root(td)
            before = source_fingerprint(root, "main.tex")
            (root / "main.tex").write_text("\\documentclass{article}\\begin{document}y\\end{document}\n", encoding="utf-8")
            after = source_fingerprint(root, "main.tex")
            self.assertEqual(diff_source_fingerprints(before, after), ["main.tex"])

    def test_unchanged_no_diff(self):
        with tempfile.TemporaryDirectory() as td:
            root = _make_root(td)
            before = source_fingerprint(root, "main.tex")
            after = source_fingerprint(root, "main.tex")
            self.assertEqual(diff_source_fingerprints(before, after), [])


class CompileLatexIntegrationTest(unittest.TestCase):
    def test_compile_latex_returns_v2_plus_legacy_keys(self):
        import runtime_actions

        with tempfile.TemporaryDirectory() as td:
            root = _make_root(td)
            main = root / "main.tex"
            fake_result = mock.Mock(returncode=0, stdout="Output written", stderr="")
            with mock.patch.object(runtime_actions, "run_command", return_value=fake_result), \
                 mock.patch.object(runtime_actions, "pdf_page_count", return_value=2):
                (root / "main.pdf").write_bytes(b"%PDF")  # 预置，防 unlink 后缺失
                # 重新让编译"产出" pdf：mock run_command 不写文件，先放 baseline
                report = runtime_actions.compile_latex(root, main_tex=main)
            self.assertIn("schema_version", report)
            self.assertEqual(report["schema_version"], "compile_result.v2")
            # 旧键全保留
            for legacy in ("success", "command", "returncode", "case_status", "missing_sty", "compile_log"):
                self.assertIn(legacy, report)
            self.assertTrue((root / "data/compile/journal.jsonl").is_file())


if __name__ == "__main__":
    unittest.main()
