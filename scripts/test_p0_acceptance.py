"""P0/P2 验收补项：

- 3: page_gap 变差时自动拒绝
- 4: 已达目标页数时增加页数的动作被拒绝（含垂直代价方向规则）
- 8: 表格修复后行列数、caption、label 保持不变
- 11: 编译失败不删除 baseline PDF（反而恢复）
- 12: fix-layout 没有新 PDF 时返回非零
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from phased_repair import RoundEvidenceSnapshot, gate_decision  # noqa: E402


def _evidence(pages, severe=0, protected=None, undef=0):
    return RoundEvidenceSnapshot(
        page_count=pages,
        severe_overfull=severe,
        total_overflow_pt=0.0,
        overfull_count=0,
        undefined_count=undef,
        protected=protected or {"figures": 1, "tables": 1, "captions": 1, "labels": 1, "citations": 1},
    )


class PageGapGateTest(unittest.TestCase):
    def test_page_gap_worse_rejects(self):
        # 8 页目标 8（gap 0）→ 修复后 10 页（gap 2）→ 拒绝
        accepted, reason = gate_decision(_evidence(8), _evidence(10), target_pages=8)
        self.assertFalse(accepted)
        self.assertIn("page_gap worsened", reason)

    def test_page_gap_closer_accepts(self):
        # 10 页目标 8（gap 2）→ 修复后 8 页（gap 0）→ 接受
        accepted, _ = gate_decision(_evidence(10), _evidence(8), target_pages=8)
        self.assertTrue(accepted)

    def test_at_target_page_growth_rejected(self):
        # 已命中目标页数：任何页数增加都拒绝（page_gap 分支或页数分支）
        accepted, reason = gate_decision(_evidence(8), _evidence(9), target_pages=8)
        self.assertFalse(accepted)
        self.assertTrue("page_gap worsened" in reason or "page count grew" in reason)

    def test_over_budget_vertical_cost_must_not_grow_pages(self):
        # 超页预算（10 > 8）时垂直代价动作不许再增页，即使 gap 没变差（10→11 gap 2→3 其实变差；
        # 用 10→10+1 页场景验证方向规则本身）
        accepted, reason = gate_decision(
            _evidence(10), _evidence(11), target_pages=8, vertical_cost=True
        )
        self.assertFalse(accepted)

    def test_no_target_budget_page_growth_rejected(self):
        accepted, reason = gate_decision(_evidence(12), _evidence(13), target_pages=None)
        self.assertFalse(accepted)
        self.assertIn("page count grew", reason)

    def test_severe_overfull_increase_rejects(self):
        accepted, reason = gate_decision(_evidence(8, severe=0), _evidence(8, severe=1), target_pages=8)
        self.assertFalse(accepted)
        self.assertIn("severe overfull increased", reason)

    def test_missing_pdf_fail_closed(self):
        accepted, reason = gate_decision(_evidence(8), _evidence(None), target_pages=8)
        self.assertFalse(accepted)
        self.assertIn("fail-closed", reason)

    def test_protected_objects_decrease_rejects(self):
        after = _evidence(8, protected={"figures": 0, "tables": 1, "captions": 1, "labels": 1, "citations": 1})
        accepted, reason = gate_decision(_evidence(8), after, target_pages=8)
        self.assertFalse(accepted)
        self.assertIn("figures", reason)


TABLE_TEX = (
    "\\documentclass{article}\n\\begin{document}\n"
    "\\begin{table}[h]\n\\centering\n"
    "\\resizebox{0.7\\textwidth}{!}{%\n"
    "\\begin{tabular}{ccc}\n\\toprule\n"
    "A & B & C \\\\\n\\midrule\n"
    "1 & 2 & 3 \\\\\n4 & 5 & 6 \\\\\n7 & 8 & 9 \\\\\n"
    "\\bottomrule\n\\end{tabular}}\n"
    "\\caption{Kept caption}\\label{tab:keep}\n"
    "\\end{table}\n\\end{document}\n"
)


class TableStructurePreservationTest(unittest.TestCase):
    def test_table_fix_keeps_rows_cols_caption_label(self):
        from overflow_fixers import fix_table_overflow

        modified, fix = fix_table_overflow(TABLE_TEX, line_number=6)
        if fix is None:  # 无可修项也算通过（结构未动）
            self.assertEqual(modified, TABLE_TEX)
            return
        # 行数：数据行都以 \\ 结尾（3 行 × 2 个 &）
        self.assertEqual(TABLE_TEX.count(" & "), modified.count(" & "))
        self.assertEqual(TABLE_TEX.count("\\\\"), modified.count("\\\\"))
        self.assertIn("\\caption{Kept caption}", modified)
        self.assertIn("\\label{tab:keep}", modified)
        # resizebox 缩放系数被钳制或移除，而不是无脑改 linewidth 掩盖
        self.assertNotIn("1.5\\textwidth", modified)


class BaselinePdfProtectionTest(unittest.TestCase):
    def test_compile_failure_restores_baseline_pdf(self):
        import tempfile

        import runtime_actions

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            main = root / "main.tex"
            main.write_text("\\documentclass{article}\\begin{document}x\\end{document}\n", encoding="utf-8")
            pdf = root / "main.pdf"
            pdf.write_bytes(b"%PDF-baseline")

            fake_result = mock.Mock(returncode=12, stdout="", stderr="! LaTeX Error: boom\n==> Fatal error")
            with mock.patch.object(runtime_actions, "run_command", return_value=fake_result):
                report = runtime_actions.compile_latex(root, main_tex=main)

            self.assertFalse(report["success"])
            self.assertTrue(report["restored_baseline_pdf"])
            self.assertEqual(pdf.read_bytes(), b"%PDF-baseline")  # 修前 PDF 没被删


    def test_page_shrink_without_target_rejected_when_large(self):
        "ICLR 回归：无页数预算时，单候选造成 12→10 页的大幅缩水必须拒绝"
        accepted, reason = gate_decision(_evidence(12), _evidence(10), target_pages=None)
        self.assertFalse(accepted)
        self.assertIn("page swing too large", reason)
        accepted, _ = gate_decision(_evidence(12), _evidence(11), target_pages=None)
        self.assertTrue(accepted)


class FixLayoutExitCodeTest(unittest.TestCase):
    "退出码契约（ 重定义）：旧 PDF 不再给任何非 done 状态打 0。"

    def _report(self, status, pdf_path=None):
        runtime_actions = {}
        if pdf_path is not None:
            runtime_actions["compile"] = {"pdf_path": str(pdf_path)}
        return {"status": status, "runtime_actions": runtime_actions}

    def test_done_exits_zero(self):
        from paperfit_command import _source_changing_exit_code

        self.assertEqual(_source_changing_exit_code(self._report("done"), Path("."), Path("main.tex")), 0)

    def test_blocked_exits_two_even_with_pdf(self):
        import tempfile

        from paperfit_command import _source_changing_exit_code

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pdf = root / "main.pdf"
            pdf.write_bytes(b"%PDF")
            # 旧 PDF 只供诊断比较，不代表本次修复成功
            self.assertEqual(
                _source_changing_exit_code(self._report("blocked", pdf), root, root / "main.tex"), 2
            )

    def test_typed_nested_blocked_with_pdf_exits_nonzero(self):
        """typed 报告终态在 run_result.status：嵌套 blocked + 磁盘旧 PDF → 非零。"""
        import tempfile

        from paperfit_command import _source_changing_exit_code

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "main.pdf").write_bytes(b"%PDF")
            typed_report = {"mode": "typed_fix_layout", "run_result": {"status": "blocked"}}
            self.assertEqual(_source_changing_exit_code(typed_report, root, root / "main.tex"), 2)

    def test_typed_nested_done_exits_zero(self):
        from paperfit_command import _source_changing_exit_code

        typed_report = {"mode": "typed_fix_layout", "run_result": {"status": "done"}}
        self.assertEqual(_source_changing_exit_code(typed_report, Path("."), Path("main.tex")), 0)

    def test_typed_nested_continue_exits_one(self):
        from paperfit_command import _source_changing_exit_code

        typed_report = {"mode": "typed_fix_layout", "run_result": {"status": "continue"}}
        self.assertEqual(_source_changing_exit_code(typed_report, Path("."), Path("main.tex")), 1)

    def test_approval_required_exits_three(self):
        from paperfit_command import _source_changing_exit_code

        self.assertEqual(
            _source_changing_exit_code(self._report("approval_required"), Path("."), Path("main.tex")), 3
        )

    def test_missing_status_fail_closed(self):
        from paperfit_command import _source_changing_exit_code

        self.assertEqual(_source_changing_exit_code({}, Path("."), Path("main.tex")), 2)




class FixLayoutCliExitTest(unittest.TestCase):
    """CLI 集成：typed fix-layout 嵌套状态驱动真实退出码。"""

    def _run_cli(self, tmpdir: str, nested_status: str) -> int:
        import sys as _sys
        from unittest import mock

        import paperfit_command

        root = Path(tmpdir)
        (root / "data").mkdir(exist_ok=True)
        (root / "main.tex").write_text(r"\documentclass{article}x\end{document}", encoding="utf-8")
        (root / "main.pdf").write_bytes(b"%PDF-old")  # 磁盘旧 PDF

        argv = [
            "paperfit_command.py", "fix-layout",
            "--main", str(root / "main.tex"),
        ]
        with mock.patch.object(_sys, "argv", argv),                 mock.patch.object(paperfit_command.Path, "cwd", return_value=root.resolve()),                 mock.patch.object(paperfit_command, "_detect_main_tex", return_value=root / "main.tex"),                 mock.patch.object(paperfit_command, "_build_portrait", return_value={}),                 mock.patch.object(
                    paperfit_command,
                    "_run_typed_source_changing",
                    return_value={"mode": "typed_fix_layout", "run_result": {"status": nested_status}},
                ),                 mock.patch("builtins.print"):
            try:
                paperfit_command.main()
                return 0
            except SystemExit as exc:
                return int(exc.code or 0)

    def test_typed_blocked_with_old_pdf_nonzero(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(self._run_cli(td, "blocked"), 2)

    def test_typed_done_zero(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(self._run_cli(td, "done"), 0)

    def test_typed_continue_one(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(self._run_cli(td, "continue"), 1)


if __name__ == "__main__":
    unittest.main()
