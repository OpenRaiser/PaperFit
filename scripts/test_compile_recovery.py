"""编译恢复子系统验收：确定性策略、签名去重、all-or-nothing 回滚、边界。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from compile_recovery import (  # noqa: E402
    apply_deterministic_recovery,
    fix_duplicate_environments,
    fix_escaped_identifiers,
    fix_junk_before_documentclass,
    fix_missing_end_document,
    fix_missing_end_env_at_error,
    fix_missing_package_for_command,
    fix_unicode,
    recover_compile,
)


def _write(root: Path, text: str) -> None:
    (root / "main.tex").write_text(text, encoding="utf-8")


def _read(root: Path) -> str:
    return (root / "main.tex").read_text(encoding="utf-8")


class StrategyTest(unittest.TestCase):
    def test_unicode_fix(self):
        tex, changes = fix_unicode("em\u2014dash and \u03b1 symbol")
        self.assertIn("---", tex)
        self.assertIn("$\\alpha$", tex)
        self.assertTrue(changes)

    def test_unicode_untouched_when_no_match(self):
        tex, changes = fix_unicode("plain ascii")
        self.assertEqual((tex, changes), ("plain ascii", []))

    def test_escaped_identifier_fix(self):
        tex, n = fix_escaped_identifiers("see \\ref{fig:framework\\_overview} and \\cite{smith2020}")
        self.assertIn("\\ref{fig:framework_overview}", tex)
        self.assertIn("\\cite{smith2020}", tex)
        self.assertEqual(n, 1)

    def test_duplicate_env_collapse(self):
        tex, changes = fix_duplicate_environments("\\begin{figure}\n\\begin{figure}x\\end{figure}\n\\end{figure}")
        self.assertEqual(tex.count("\\begin{figure}"), 1)
        self.assertTrue(changes)

    def test_missing_end_document(self):
        tex, changed = fix_missing_end_document("\\documentclass{article}\\begin{document}x")
        self.assertTrue(changed)
        self.assertIn("\\end{document}", tex)

    def test_junk_before_documentclass(self):
        tex, changed = fix_junk_before_documentclass("garbage line\n\\documentclass{article}")
        self.assertTrue(changed)
        self.assertTrue(tex.startswith("\\documentclass"))

    def test_junk_comment_prefix_kept(self):
        tex, changed = fix_junk_before_documentclass("% just a comment\n\\documentclass{article}")
        self.assertFalse(changed)

    def test_missing_package_for_command_ucs_only(self):
        base = "\\documentclass{article}\n\\begin{document}\n\\toprule\n\\end{document}\n"
        tex, inserted = fix_missing_package_for_command(base, "! Undefined control sequence.")
        self.assertEqual(inserted, ["\\usepackage{booktabs}"])
        self.assertIn("\\usepackage{booktabs}", tex)
        # 无 UCS 错误时不动
        tex2, inserted2 = fix_missing_package_for_command(base, "some other error")
        self.assertEqual(inserted2, [])

    def test_missing_end_env_at_error(self):
        base = "\\documentclass{article}\\begin{document}\n\\begin{figure}x\n\\end{document}\n"
        tex, changes = fix_missing_end_env_at_error(base, "\\begin{figure} at l.5")
        self.assertTrue(changes)
        self.assertIn("\\end{figure}", tex)


class ApplyRecoveryTest(unittest.TestCase):
    def test_recovery_applies_and_writes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "\\documentclass{article}\n\\begin{document}\n\\ref{fig:a\\_b}\n\\end{document}\n")
            actions = apply_deterministic_recovery(root, "main.tex", "! Undefined control sequence.")
            strategies = {a.strategy for a in actions}
            self.assertIn("escaped_identifiers", strategies)
            self.assertIn("\\ref{fig:a_b}", _read(root))




class OrphanBeginEnvTest(unittest.TestCase):
    """Regression checks."""

    TEX = (
        "\\documentclass{article}\n\\begin{document}\n"
        "text before\n"
        "%   \\resizebox{1.21\\textwidth}{!}{%\n"
        "\\begin{tabular}{lccccc}\n"
        "%     \\toprule\n"
        "%   \\end{tabular}\n"
        "}\n"
        "\\begin{figure}[tbp]\nx\\end{figure}\n"
        "\\end{document}\n"
    )
    LOG = "! LaTeX Error: \\begin{tabular} on input line 5 ended by \\end{document}."

    def test_orphan_commented_with_stray_brace(self):
        from compile_recovery import fix_orphan_begin_env

        fixed, changes = fix_orphan_begin_env(self.TEX, self.LOG)
        self.assertEqual(changes, ["orphan-begin-tabular@5"])
        self.assertIn("% [paperfit-recovery] orphan begin commented", fixed)
        self.assertIn("% [paperfit-recovery] stray brace commented", fixed)
        # 注释原文保留，figure 完整
        self.assertIn("%   \\end{tabular}", fixed)
        self.assertIn("\\begin{figure}[tbp]", fixed)

    def test_line_drift_tolerated(self):
        from compile_recovery import fix_orphan_begin_env

        # 日志行号 11（±10 漂移内）仍能命中第 5 行的孤儿
        log = "! LaTeX Error: \\begin{tabular} on input line 11 ended by \\end{document}."
        fixed, changes = fix_orphan_begin_env(self.TEX, log)
        self.assertEqual(changes, ["orphan-begin-tabular@5"])

    def test_active_content_blocks_fix(self):
        from compile_recovery import fix_orphan_begin_env

        tex = self.TEX.replace("%     \\toprule\n", "    Model & X active \\\\\n")
        fixed, changes = fix_orphan_begin_env(tex, self.LOG)
        self.assertEqual(changes, [])
        self.assertEqual(fixed, tex)

    def test_no_log_evidence_no_fix(self):
        from compile_recovery import fix_orphan_begin_env

        fixed, changes = fix_orphan_begin_env(self.TEX, "unrelated log")
        self.assertEqual(changes, [])


class _FakeCompile:
    """按脚本演进的假编译器：第 1 次失败，应用修复后第 2 次成功（或按剧本失败）。"""

    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.calls = 0

    def __call__(self, root, main_path):
        import json as _json

        idx = min(self.calls, len(self.outcomes) - 1)
        outcome = self.outcomes[idx]
        self.calls += 1
        result = {
            "schema_version": "compile_result.v2",
            "attempt_id": f"fake-{self.calls}",
            "success": outcome.get("success", False),
            "error_signature": outcome.get("sig"),
            "log_path": outcome.get("log_path"),
            "failure_class": None if outcome.get("success") else "source_error",
        }
        if outcome.get("write_log"):
            path = root / "data" / "compile" / "fake.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(outcome["write_log"], encoding="utf-8")
            result["log_path"] = str(path.relative_to(root))
        return result


class RecoverLoopTest(unittest.TestCase):
    def test_recover_on_second_attempt(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "\\ref{fig:a\\_b}\n\\documentclass{article}\n\\begin{document}x\\end{document}\n")
            fake = _FakeCompile([
                {"success": False, "sig": "s1", "write_log": "! Undefined control sequence.\nl.1 \\ref"},
                {"success": True},
            ])
            result = recover_compile(root, "main.tex", compile_fn=fake)
            self.assertTrue(result.success)
            self.assertEqual(result.attempts, 2)
            self.assertFalse(result.rolled_back)
            self.assertTrue(result.attempted)

    def test_same_signature_stops_loop(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "irrelevant but unfixable \\documentclass{article}\\begin{document}x\\end{document}\n")
            fake = _FakeCompile([
                {"success": False, "sig": "same", "write_log": "! Weird error"},
                {"success": False, "sig": "same", "write_log": "! Weird error"},
                {"success": True},  # 不应到达
            ])
            result = recover_compile(root, "main.tex", compile_fn=fake)
            self.assertFalse(result.success)
            self.assertEqual(result.stopped_reason, "same_error_signature_repeated")
            self.assertLessEqual(fake.calls, 3)

    def test_total_failure_rolls_back_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "\\ref{fig:a\\_b}\n\\documentclass{article}\n\\begin{document}x\\end{document}\n")
            original = _read(root)
            fake = _FakeCompile([
                {"success": False, "sig": "s1", "write_log": "! Undefined control sequence.\nl.1 \\ref"},
                {"success": False, "sig": "s2", "write_log": "! Another error"},
                {"success": False, "sig": "s3", "write_log": "! Third error"},
            ])
            restores = []
            captured = {"content": _read(root)}

            def _snap_create():
                captured["content"] = _read(root)
                return "snap-token"

            def _snap_restore(_token):
                restores.append(_token)
                _write(root, captured["content"])
            result = recover_compile(
                root, "main.tex", compile_fn=fake,
                snapshot_create=_snap_create,
                snapshot_restore=_snap_restore,
                max_attempts=3,
            )
            self.assertFalse(result.success)
            self.assertTrue(result.rolled_back)
            self.assertEqual(restores, ["snap-token"])
            self.assertEqual(_read(root), original)  # 源码已恢复

    def test_success_first_try_no_side_effects(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "\\documentclass{article}\\begin{document}ok\\end{document}\n")
            fake = _FakeCompile([{"success": True}])
            result = recover_compile(root, "main.tex", compile_fn=fake)
            self.assertTrue(result.success)
            self.assertEqual(result.attempts, 1)
            self.assertEqual(result.attempted, [])

    def test_no_fix_available_stops_cleanly(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "\\documentclass{article}\\begin{document}clean already\\end{document}\n")
            fake = _FakeCompile([
                {"success": False, "sig": "s1", "write_log": "! Something we cannot fix"},
            ])
            result = recover_compile(root, "main.tex", compile_fn=fake)
            self.assertFalse(result.success)
            self.assertEqual(result.stopped_reason, "no_deterministic_fix_available")
            self.assertFalse(result.rolled_back)  # 没改过源码就不需要回滚


if __name__ == "__main__":
    unittest.main()
