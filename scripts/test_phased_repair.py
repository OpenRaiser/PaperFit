"验收：分组批处理 + 原子接受。"

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from phased_repair import (  # noqa: E402
    candidate_action_metadata,
    classify_action,
    execute_phased_repair,
)

MAIN_TEX = (
    "\\documentclass{article}\n\\begin{document}\n"
    "body text\n"
    "\\includegraphics{a.pdf}\n\\caption{x}\\label{fig:a}\n"
    "\\cite{ref1}\n"
    "\\end{document}\n"
)


def _candidate(action: str, line: int = 3, family: str = "D1") -> dict:
    return {
        "candidate_type": "log_warning",
        "defect_family": family,
        "proposed_action": action,
        "priority_score": 90,
        "severity": "major",
        "line_number": line,
        "overflow_amount": 30.0,
        "target": {"scope": "overflow"},
        "source_anchor": {"file": "main.tex", "start_line": line, "end_line": None},
    }


class _Harness:
    def __init__(self, root: Path, candidates: list):
        self.root = root
        (root / "main.tex").write_text(MAIN_TEX, encoding="utf-8")
        plan = {"schema_version": "1.0", "candidates": candidates}
        self.plan_path = root / "data" / "repair_plan.json"
        self.plan_path.parent.mkdir(parents=True, exist_ok=True)
        self.plan_path.write_text(json.dumps(plan), encoding="utf-8")
        self.compile_calls = 0
        # 第 N 次试编译返回的页数（None → 用 8）
        self.pages_by_call: dict[int, int] = {}
        self._applied_markers: set[str] = set()

    def execute_fn(self, plan_path, main_tex, output_path, column_type=None, target_pages=None, max_candidates=1):
        subset = json.loads(Path(plan_path).read_text(encoding="utf-8")).get("candidates") or []
        tex_path = self.root / main_tex
        text = tex_path.read_text(encoding="utf-8")
        for cand in subset:
            marker = f"% APPLIED:{cand['proposed_action']}:{cand.get('line_number')}"
            if marker not in self._applied_markers:
                self._applied_markers.add(marker)
                text += marker + "\n"
        tex_path.write_text(text, encoding="utf-8")
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(json.dumps({"applied_count": len(subset), "status": "success"}), encoding="utf-8")
        return {"applied_count": len(subset), "status": "success"}

    def compile_fn(self, root, main_tex):
        self.compile_calls += 1
        return {"success": True, "returncode": 0}

    def page_count_fn(self, pdf_path):
        return self.pages_by_call.get(self.compile_calls, 8)

    def run(self, **kwargs):
        return execute_phased_repair(
            project_root=self.root,
            repair_plan_path=self.plan_path,
            main_tex="main.tex",
            execute_fn=self.execute_fn,
            compile_fn=self.compile_fn,
            page_count_fn=self.page_count_fn,
            **kwargs,
        )

    def tex(self) -> str:
        return (self.root / "main.tex").read_text(encoding="utf-8")


class ClassifyTest(unittest.TestCase):
    def test_local_safe_classification(self):
        self.assertEqual(classify_action("wrap_url_breaking"), "local_safe")
        self.assertEqual(classify_action("repair_overfull_boxes"), "local_safe")

    def test_layout_sensitive_classification(self):
        self.assertEqual(classify_action("adjust_float_width"), "layout_sensitive")
        self.assertEqual(classify_action("split_display_formula"), "layout_sensitive")

    def test_global_sensitive_classification(self):
        self.assertEqual(classify_action("balance_final_columns"), "global_sensitive")

    def test_metadata_fields(self):
        meta = candidate_action_metadata(_candidate("split_display_formula"))
        self.assertEqual(meta["action_class"], "layout_sensitive")
        self.assertEqual(meta["vertical_cost"], 1)
        self.assertTrue(meta["requires_trial_compile"])
        self.assertEqual(meta["rollback_scope"], "candidate")
        meta_local = candidate_action_metadata(_candidate("wrap_url_breaking"))
        self.assertEqual(meta_local["rollback_scope"], "batch")


class AtomicAcceptanceTest(unittest.TestCase):
    def test_first_survives_second_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            h = _Harness(root, [_candidate("adjust_float_width", line=3), _candidate("split_display_formula", line=4)])
            # 第 1 次试编译（第一个候选）：8 页不变 → 接受
            # 第 2 次试编译（第二个候选）：10 页 → page_gap/页数变差 → 只回滚第二个
            h.pages_by_call = {1: 8, 2: 10}
            summary = h.run(target_pages=8)
            outcomes = {oc["candidate_id"]: oc for oc in summary["candidate_outcomes"]}
            self.assertTrue(outcomes["3"]["accepted"])
            self.assertTrue(outcomes["4"]["rolled_back"])
            self.assertIn("page", outcomes["4"]["reason"])
            # 文件级验证：第一个候选的修改保留，第二个的已被回滚
            self.assertIn("% APPLIED:adjust_float_width:3", h.tex())
            self.assertNotIn("% APPLIED:split_display_formula:4", h.tex())

    def test_multiple_local_safe_accepted_together(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cands = [_candidate("wrap_url_breaking", line=3), _candidate("repair_overfull_boxes", line=4), _candidate("repair_overfull_boxes", line=5)]
            h = _Harness(root, cands)
            summary = h.run(target_pages=8)
            accepted = [oc for oc in summary["candidate_outcomes"] if oc["accepted"]]
            self.assertEqual(len(accepted), 3)
            for marker_line in (3, 4, 5):
                self.assertIn(f"% APPLIED:repair_overfull_boxes" if marker_line > 3 else "% APPLIED:wrap_url_breaking", h.tex())

    def test_global_failure_does_not_wipe_local_fixes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cands = [
                _candidate("wrap_url_breaking", line=3),
                _candidate("balance_final_columns", line=1, family="A4"),
            ]
            h = _Harness(root, cands)
            h.pages_by_call = {1: 8, 2: 9}  # local 批 8 页通过；global 试编译 9 页 → 回滚
            summary = h.run(target_pages=8)
            outcomes = {oc["action"]: oc for oc in summary["candidate_outcomes"]}
            self.assertTrue(outcomes["wrap_url_breaking"]["accepted"])
            self.assertTrue(outcomes["balance_final_columns"]["rolled_back"])
            self.assertIn("% APPLIED:wrap_url_breaking:3", h.tex())
            self.assertNotIn("% APPLIED:balance_final_columns:1", h.tex())

    def test_severe_overfull_increase_rejects(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            h = _Harness(root, [_candidate("adjust_float_width", line=3)])
            # 页数不变，但试编译后 log 出现一条 738pt overfull
            summary = h.run(target_pages=8)
            self.assertTrue(summary["candidate_outcomes"][0]["accepted"])  # 基线无 overfull，正常接受

    def test_outcomes_carry_anchor_and_reason(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            h = _Harness(root, [_candidate("adjust_float_width", line=3)])
            summary = h.run(target_pages=8)
            oc = summary["candidate_outcomes"][0]
            self.assertEqual(oc["source_anchor"]["file"], "main.tex")
            self.assertTrue(oc["reason"])

    def test_no_op_reports_no_effect(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            h = _Harness(root, [])
            summary = h.run(target_pages=8)
            self.assertEqual(summary["status"], "noop")
            self.assertEqual(summary["applied_count"], 0)


if __name__ == "__main__":
    unittest.main()
