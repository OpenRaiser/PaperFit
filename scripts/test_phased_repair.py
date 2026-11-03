"验收：分组批处理 + 原子接受。"

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

from phased_repair import (  # noqa: E402
    RoundEvidenceSnapshot,
    candidate_action_metadata,
    classify_action,
    execute_phased_repair,
    gate_decision,
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
        # Page reading is injected in this harness. An identifiable artifact lets
        # us exercise reporting without invoking a real compiler in unit tests.
        (root / "main.pdf").write_bytes(b"phased test PDF artifact")
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
    def test_unmeasured_scope_does_not_relax_content_or_reference_checks(self):
        for failure in ("content", "undefined_reference"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as td:
                h = _Harness(Path(td), [_candidate("wrap_url_breaking")])
                original_execute = h.execute_fn
                original_compile = h.compile_fn
                def execute_with_content_drop(*args, **kwargs):
                    report = original_execute(*args, **kwargs)
                    path = h.root / "main.tex"
                    path.write_text(path.read_text(encoding="utf-8").replace(r"\caption{x}", ""), encoding="utf-8")
                    return report
                def compile_with_undefined_reference(root, main_tex):
                    report = original_compile(root, main_tex)
                    (root / "main.log").write_text("LaTeX Warning: Reference `missing' undefined", encoding="utf-8")
                    return report
                if failure == "content":
                    h.execute_fn = execute_with_content_drop
                else:
                    h.compile_fn = compile_with_undefined_reference
                summary = h.run(target_pages=8, page_policy="at_most", page_budget_scope="main_body")
                self.assertEqual(summary["accepted_count"], 0)
                self.assertEqual(summary["rolled_back_count"], 1)
                self.assertEqual(h.tex(), MAIN_TEX)
                expected = "protected objects decreased" if failure == "content" else "undefined references increased"
                self.assertIn(expected, summary["candidate_outcomes"][0]["reason"])

    def test_at_most_reductions_are_accepted_in_every_phase(self):
        with tempfile.TemporaryDirectory() as td:
            h = _Harness(Path(td), [
                _candidate("wrap_url_breaking", line=3),
                _candidate("adjust_float_width", line=4),
                _candidate("balance_final_columns", line=5),
            ])
            h.pages_by_call = {0: 8, 1: 7, 2: 6, 3: 5}
            summary = h.run(target_pages=8, page_policy="at_most")
            self.assertEqual(summary["accepted_count"], 3)
            self.assertEqual(summary["rolled_back_count"], 0)
            self.assertEqual(summary["page_policy"], "at_most")
            self.assertEqual(summary["final_evidence"]["page_gap"], 0)
            # Injected count/compiler functions exercise policy, not native
            # source/PDF proof. Goal success stays unknown in this harness.
            self.assertIsNone(summary["page_objective"]["target_met"])
            for action in ("wrap_url_breaking", "adjust_float_width", "balance_final_columns"):
                self.assertIn("% APPLIED:" + action, h.tex())

    def test_policy_acceptance_and_rollback_for_each_phase(self):
        for action in ("wrap_url_breaking", "adjust_float_width", "balance_final_columns"):
            for policy, before, after, accepted in (
                ("at_most", 7, 8, True),
                ("at_most", 8, 9, False),
                ("exact", 8, 7, False),
            ):
                with self.subTest(action=action, policy=policy, before=before, after=after):
                    with tempfile.TemporaryDirectory() as td:
                        h = _Harness(Path(td), [_candidate(action)])
                        h.pages_by_call = {0: before, 1: after}
                        summary = h.run(target_pages=8, page_policy=policy)
                        outcome = summary["candidate_outcomes"][0]
                        self.assertEqual(outcome["accepted"], accepted)
                        self.assertEqual(outcome["rolled_back"], not accepted)
                        self.assertEqual("% APPLIED:" in h.tex(), accepted)

    def test_unmeasured_scope_keeps_candidate_without_claiming_page_hit(self):
        for scope in ("main_body", "with_refs"):
            for action in ("wrap_url_breaking", "adjust_float_width", "balance_final_columns"):
                with self.subTest(scope=scope, action=action):
                    with tempfile.TemporaryDirectory() as td:
                        h = _Harness(Path(td), [_candidate(action)])
                        h.pages_by_call = {0: 9, 1: 10}
                        summary = h.run(target_pages=8, page_policy="at_most", page_budget_scope=scope)
                        self.assertEqual(summary["accepted_count"], 1)
                        self.assertIn("scope_boundary_unverified", summary["candidate_outcomes"][0]["reason"])
                        objective = summary["page_objective"]
                        self.assertEqual(objective["status"], "unknown")
                        self.assertIsNone(objective["target_met"])
                        self.assertIsNone(objective["distance"])
                        self.assertEqual(objective["measured_scope"], "with_appendix")
                        self.assertEqual(objective["page_budget_scope"], scope)
                        self.assertIsNone(summary["final_evidence"]["page_gap"])

    def test_failed_compile_cannot_accept_existing_pdf_in_any_phase(self):
        reports = (
            {"success": False, "returncode": 1, "restored_baseline_pdf": True},
            {"success": True, "returncode": 124, "timeout": True},
            {"success": True, "returncode": 0, "pdf_fresh": False},
            {"success": True, "returncode": 0, "restored_baseline_pdf": True},
            {"success": True, "returncode": 0, "timed_out": True},
            {"success": True, "returncode": 0, "restored_good_pdf": True},
            {"success": True, "returncode": 0, "partial_pdf_available": True},
            None,
        )
        for action in ("wrap_url_breaking", "adjust_float_width", "balance_final_columns"):
            for compile_report in reports:
                with self.subTest(action=action, compile_report=compile_report):
                    with tempfile.TemporaryDirectory() as td:
                        h = _Harness(Path(td), [_candidate(action)])
                        h.compile_fn = lambda root, main_tex: compile_report
                        summary = h.run(target_pages=8, page_policy="at_most")
                        self.assertEqual(summary["accepted_count"], 0)
                        self.assertEqual(summary["rolled_back_count"], 1)
                        self.assertIn("compile_or_render_failed", summary["candidate_outcomes"][0]["reason"])
                        self.assertTrue((h.root / "main.pdf").is_file())
                        self.assertNotIn("% APPLIED:", h.tex())
                        self.assertIsNone(summary["page_objective"]["target_met"])

    def test_failed_later_preview_cannot_reuse_earlier_page_hit(self):
        with tempfile.TemporaryDirectory() as td:
            h = _Harness(Path(td), [
                _candidate("wrap_url_breaking", line=3),
                _candidate("balance_final_columns", line=4),
            ])
            original_compile = h.compile_fn
            def compile_with_failed_preview(root, main_tex):
                report = original_compile(root, main_tex)
                if h.compile_calls == 2:
                    (root / "main.pdf").write_bytes(b"different failed preview PDF")
                    return {"success": False, "returncode": 1}
                return report
            h.compile_fn = compile_with_failed_preview
            summary = h.run(target_pages=8, page_policy="at_most")
            self.assertEqual(summary["accepted_count"], 1)
            self.assertEqual(summary["rolled_back_count"], 1)
            self.assertIn("% APPLIED:wrap_url_breaking:3", h.tex())
            self.assertNotIn("% APPLIED:balance_final_columns:4", h.tex())
            self.assertEqual(summary["page_objective"]["status"], "unknown")
            self.assertIsNone(summary["page_objective"]["target_met"])

    def test_success_without_native_input_binding_keeps_candidate_pending(self):
        with tempfile.TemporaryDirectory() as td:
            h = _Harness(Path(td), [_candidate("wrap_url_breaking")])
            h.compile_fn = lambda root, main_tex: {
                "schema_version": "compile_result.v2", "success": True,
                "returncode": 0, "pdf_fresh": True,
                "current_source_compile_success": False, "source_pdf_binding": "unverified",
                "compile_inputs": {"bound": False, "unavailable": ["missing_recorder"]},
            }
            summary = h.run(target_pages=8, page_policy="at_most")
            self.assertEqual(summary["accepted_count"], 1)
            self.assertIn("% APPLIED:", h.tex())
            self.assertEqual(summary["page_objective"]["status"], "unknown")
            self.assertEqual(summary["page_objective"]["source_pdf_binding"], "unverified")
            self.assertIsNone(summary["page_objective"]["target_met"])

    def test_identical_restored_pdf_does_not_reuse_failed_latest_native_journal(self):
        with tempfile.TemporaryDirectory() as td:
            h = _Harness(Path(td), [
                _candidate("wrap_url_breaking", line=3),
                _candidate("balance_final_columns", line=4),
            ])
            original_compile = h.compile_fn
            def compile_with_later_failure(root, main_tex):
                report = original_compile(root, main_tex)
                if h.compile_calls == 2:
                    return {"success": False, "returncode": 1, "restored_baseline_pdf": True}
                return {**report, "schema_version": "compile_result.v2",
                        "current_source_compile_success": True, "source_pdf_binding": "verified_native_inputs"}
            h.compile_fn = compile_with_later_failure
            with mock.patch("phased_repair.verify_native_build", return_value={
                    "status": "unknown", "source_pdf_binding": "unverified", "reason": "latest_native_compile_failed"}) as binding:
                summary = h.run(target_pages=8, page_policy="at_most")
            binding.assert_called_once()
            self.assertEqual(summary["accepted_count"], 1)
            self.assertEqual(summary["rolled_back_count"], 1)
            self.assertEqual(summary["page_objective"]["status"], "unknown")
            self.assertIsNone(summary["page_objective"]["target_met"])

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
            compile_success = h.compile_fn
            def compile_with_overfull(root, main_tex):
                result = compile_success(root, main_tex)
                (root / "main.log").write_text(r"Overfull \hbox (738pt too wide)", encoding="utf-8")
                return result
            h.compile_fn = compile_with_overfull
            summary = h.run(target_pages=8)
            self.assertTrue(summary["candidate_outcomes"][0]["rolled_back"])
            self.assertIn("severe overfull increased", summary["candidate_outcomes"][0]["reason"])
            self.assertNotIn("% APPLIED:", h.tex())

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


class PagePolicyGateTest(unittest.TestCase):
    def evidence(self, pages):
        return RoundEvidenceSnapshot(page_count=pages, severe_overfull=0,
            total_overflow_pt=0.0, overfull_count=0, undefined_count=0,
            protected={"captions": 1}, compile_success=True)

    def test_direct_gate_retains_legacy_exact_default(self):
        accepted, reason = gate_decision(self.evidence(8), self.evidence(7), target_pages=8)
        self.assertFalse(accepted)
        self.assertIn("page_gap worsened", reason)
        accepted, _ = gate_decision(self.evidence(8), self.evidence(7), target_pages=8, page_policy="at_most")
        self.assertTrue(accepted)

    def test_invalid_policy_fails_explicitly_even_when_count_missing(self):
        with self.assertRaises(ValueError):
            gate_decision(self.evidence(8), self.evidence(None), target_pages=8, page_policy="close_enough")

    def test_invalid_page_measurements_cannot_be_accepted(self):
        for count in (None, 0, -1, True):
            with self.subTest(count=count):
                accepted, reason = gate_decision(self.evidence(8), self.evidence(count),
                    target_pages=8, page_policy="at_most", page_budget_scope="main_body")
                self.assertFalse(accepted)
                self.assertIn("compile_or_render_failed", reason)


if __name__ == "__main__":
    unittest.main()
