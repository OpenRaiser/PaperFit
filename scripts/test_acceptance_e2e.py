"""验收断言补充：

- 真实编译的最小长 token 文档：overfull 1→0、页数不回退、结构对象不减少；
- 混合批次审批门：高风险 deferred、安全子集继续；全高风险 → approval_required。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(SCRIPTS_DIR.parent / "regression") not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR.parent / "regression"))

from overflow_fixers import fix_overflow_defects  # noqa: E402
from runtime_approval import build_candidate_approval_scope_gate  # noqa: E402

LONG_TOKEN = "PerturbationStressToken" + "X" * 50

DOC = (
    "\\documentclass[10pt]{article}\n"
    "\\usepackage[margin=1in]{geometry}\n"
    "\\begin{document}\n"
    "A paragraph with enough words to fill at least one full line of text before the "
    "unbreakable token appears in the middle of the flowing body text "
    + LONG_TOKEN
    + " and a few trailing words after it.\n"
    "\\includegraphics{none-needed.pdf}\n"
    "\\caption{cap}\\label{fig:x}\\\\cite{ref:x}\n"
    "\\end{document}\n"
)


def _pdflatex_available() -> bool:
    return shutil.which("pdflatex") is not None


def _compile(work: Path) -> tuple[int, int, str]:
    subprocess.run(
        ["pdflatex", "-interaction=nonstopmode", "main.tex"],
        cwd=work, capture_output=True, timeout=300,
    )
    log_path = work / "main.log"
    text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
    overfull = len(re.findall(r"Overfull \\hbox", text))
    pages = 0
    m = re.search(r"Output written on main\.pdf \((\d+) pages?", text)
    if m:
        pages = int(m.group(1))
    return overfull, pages, text


class LongTokenEndToEndTest(unittest.TestCase):
    @unittest.skipUnless(_pdflatex_available(), "pdflatex not installed")
    def test_long_token_real_compile_overfull_fixed(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            (work / "main.tex").write_text(DOC.replace("\\includegraphics{none-needed.pdf}\n", ""), encoding="utf-8")
            before_overfull, before_pages, before_text = _compile(work)
            self.assertEqual(before_overfull, 1)  # 缺陷可复现：长 token 造成 1 处 overfull
            self.assertGreaterEqual(before_pages, 1)

            # 从真实日志解析警告行号（段落起始行），不猜行号
            warn = re.search(r"Overfull \\hbox \(([\d.]+)pt too wide\) in paragraph at lines (\d+)", before_text)
            self.assertIsNotNone(warn, "前置条件：长 token 必须产生可复现 overfull")
            defects = [
                {
                    "defect_id": "D1",
                    "object": "段落文本",
                    "page": 1,
                    "line_number": int(warn.group(2)),
                    "description": "in paragraph",
                    "overflow_amount": float(warn.group(1)),
                }
            ]
            report = fix_overflow_defects(str(work / "main.tex"), defects)
            self.assertEqual(report.status, "success")
            after_overfull, after_pages, _ = _compile(work)
            self.assertEqual(after_overfull, 0)
            self.assertEqual(after_pages, before_pages)  # 页数不回退
            fixed = (work / "main.tex").read_text(encoding="utf-8")
            # token 字符内容不变（只插入断点）；label/caption/cite 命令保留
            self.assertIn("\\allowbreak", fixed)
            # token 字符内容不变：各词段保留，X 总数不变（只插入断点，不改字符）
            for part in ("Perturbation", "Stress", "Token"):
                self.assertIn(part, fixed)
            self.assertEqual(fixed.count("X"), LONG_TOKEN.count("X"))
            self.assertIn("\\caption{cap}", fixed)
            self.assertIn("\\label{fig:x}", fixed)
            self.assertIn("\\cite{ref:x}", fixed)


class MixedBatchApprovalGateTest(unittest.TestCase):
    def _task(self):
        return {"task_type": "full_vto", "max_rounds": 2}

    def _approval(self):
        return {
            "policy": {
                "mutation_surface": ["layout_macros", "float_placement", "spacing"],
                "high_risk_operations": ["semantic_text_edit"],
            }
        }

    def _candidate(self, action, op, surfaces):
        return {
            "defect_family": "D1",
            "proposed_action": action,
            "risk": {"operation": op, "risk_level": "medium", "mutation_surface": surfaces},
        }

    def test_high_risk_deferred_safe_subset_executes(self):
        plan = {
            "candidates": [
                self._candidate("review_paragraph_spacing_and_looseness", "semantic_text_edit", ["semantic_text"]),  # 高风险
                self._candidate("wrap_url_breaking", "line_breaking", ["layout_macros"]),  # 合法
                self._candidate("repair_overfull_boxes", "line_breaking", ["layout_macros"]),  # 合法
            ]
        }
        gate = build_candidate_approval_scope_gate(
            task=self._task(), approval=self._approval(), repair_plan=plan, candidate_limit=2,
        )
        self.assertEqual(gate["status"], "pass")  # 安全子集可执行
        allowed_actions = [e["proposed_action"] for e in gate["allowed_candidates"]]
        self.assertEqual(allowed_actions, ["wrap_url_breaking", "repair_overfull_boxes"])
        self.assertEqual(len(gate["deferred_candidates"]), 1)
        self.assertEqual(gate["deferred_candidates"][0]["deferred_reason"], "high_risk_operation_requires_fresh_approval")

    def test_no_starvation_beyond_deferred_prefix(self):
        # 前 2 个全是高风险，第 3 个合法：不许饿死
        plan = {
            "candidates": [
                self._candidate("a_high", "semantic_text_edit", ["semantic_text"]),
                self._candidate("b_high", "semantic_text_edit", ["semantic_text"]),
                self._candidate("wrap_url_breaking", "line_breaking", ["layout_macros"]),
            ]
        }
        gate = build_candidate_approval_scope_gate(
            task=self._task(), approval=self._approval(), repair_plan=plan, candidate_limit=1,
        )
        self.assertEqual(gate["status"], "pass")
        self.assertEqual(gate["allowed_candidates"][0]["proposed_action"], "wrap_url_breaking")
        self.assertEqual(len(gate["deferred_candidates"]), 2)

    def test_only_high_risk_returns_approval_required(self):
        plan = {
            "candidates": [self._candidate("x", "semantic_text_edit", ["semantic_text"])]
        }
        gate = build_candidate_approval_scope_gate(
            task=self._task(), approval=self._approval(), repair_plan=plan, candidate_limit=2,
        )
        self.assertEqual(gate["status"], "approval_required")
        self.assertEqual(gate["allowed_candidates"], [])
        self.assertEqual(len(gate["deferred_candidates"]), 1)

    def test_risk_classification_not_downgraded(self):
        # 审查点：semantic_text_edit 必须仍是高风险（不允许为过关而降级）
        plan = {"candidates": [self._candidate("x", "semantic_text_edit", ["semantic_text"])]}
        gate = build_candidate_approval_scope_gate(
            task=self._task(), approval=self._approval(), repair_plan=plan, candidate_limit=2,
        )
        self.assertTrue(gate["candidate_risks"][0]["checks"]["high_risk_operation_requires_fresh_approval"])


if __name__ == "__main__":
    unittest.main()
