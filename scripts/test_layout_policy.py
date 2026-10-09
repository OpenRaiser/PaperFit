"""Mechanical policy checks at native diff, executor and live acceptance boundaries."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from content_integrity_check import compute_content_diff
from gatekeeper_enforcer import make_decision
from layout_policy import compare_layout_policy, snapshot_layout_policy
from phased_repair import collect_evidence
from repair_plan_executor import _enforce_structure_integrity, execute_repair_plan
from runtime_snapshots import create_pre_repair_snapshot
from orchestrator_runtime import OrchestratorRuntime
from runtime_types import TaskSpec

TABLE = r"\begin{table}\caption{Results}\label{tab:r}\begin{tabular}{lr}Method&Score\\A&91.2\\\end{tabular}\end{table}"
TABULAR = r"\begin{tabular}{lr}Method&Score\\A&91.2\\\end{tabular}"


def manuscript(body: str) -> str:
    return "\\documentclass{article}\n\\begin{document}\n" + body + "\n\\end{document}\n"


class LayoutPolicyTest(unittest.TestCase):
    def test_new_table_scaling_requires_review_while_scientific_payload_stays_pass(self) -> None:
        before = manuscript(TABLE)
        for wrapper in [r"\resizebox{0.1\linewidth}{!}{" + TABULAR + "}", r"\scalebox{0.1}{" + TABULAR + "}"]:
            with self.subTest(wrapper=wrapper):
                diff = compute_content_diff(before, before.replace(TABULAR, wrapper))
                self.assertEqual(diff["scientific_payload"]["status"], "pass")
                self.assertEqual(diff["layout_policy"]["status"], "manual_review")
                # A pending presentation policy never overrides other content/structure gates.
                self.assertEqual(diff["action_required"], "auto_rollback")

    def test_graphic_scale_arguments_are_compared_but_native_width_is_allowed(self) -> None:
        image = r"\includegraphics[width=0.8\linewidth]{plot.pdf}"
        before = manuscript(image)
        after = manuscript(r"\scalebox{0.1}[0.2]{" + image + "}")
        self.assertEqual(compare_layout_policy(before, after)["status"], "manual_review")
        self.assertEqual(compare_layout_policy(after, after.replace("{0.1}", "{0.2}"))["status"], "manual_review")
        self.assertEqual(compare_layout_policy(before, before.replace("0.8", "0.7"))["status"], "pass")

    def test_existing_violations_and_their_removal_are_allowed(self) -> None:
        scaled = manuscript(TABLE.replace(TABULAR, r"\resizebox{0.2\linewidth}{!}{" + TABULAR + "}"))
        self.assertEqual(compare_layout_policy(scaled, scaled + "% a comment\n")["status"], "pass")
        self.assertEqual(compare_layout_policy(scaled, manuscript(TABLE))["status"], "pass")
        self.assertEqual(compare_layout_policy(scaled, scaled.replace("0.2", "0.1"))["status"], "manual_review")

    def test_comments_literals_definitions_and_control_symbols_preserve_offsets(self) -> None:
        inactive = (
            "% \\newpage\n"
            "\\verb|\\newpage|\n"
            "\\begin{verbatim*}\\newpage\\end{verbatim*}\n"
            "\\begin{minted}{tex}\\newpage\\end{minted}\n"
            "\\detokenize{\\newpage}\n"
            "\\newcommand{\\templatebreak}{\\newpage}\n"
            "\\begin{filecontents}{example.tex}\\newpage\\end{filecontents}\n"
            "\\\\newpage\n"
        )
        self.assertEqual(compare_layout_policy("", inactive)["status"], "pass")
        report = compare_layout_policy("", inactive + "\\newpage\n")
        self.assertEqual(len(report["findings"]), 1)
        finding = report["findings"][0]
        self.assertEqual(finding["line"], 9)
        self.assertEqual(finding["start_offset"], len(inactive))

    def test_bounded_pagination_and_spacing_require_policy_evidence(self) -> None:
        for command in [r"\newpage", r"\pagebreak", r"\pagebreak[4]"]:
            self.assertEqual(compare_layout_policy("", command)["status"], "manual_review")
            self.assertEqual(compare_layout_policy("", command, bounded_layout=False)["status"], "pass")
        self.assertEqual(compare_layout_policy("", r"\pagebreak[2]")["status"], "pass")
        for command in [r"\clearpage", r"\vspace{-0.3em}", r"\vspace{1cm}", r"\vspace{\unknownskip}"]:
            diff = compute_content_diff("", command)
            self.assertEqual(diff["layout_policy"]["status"], "manual_review")
            self.assertEqual(diff["action_required"], "manual_review")
        for command in [r"\scalebox{0.1}\mytable", r"\resizebox{\linewidth}{!}{\mytable}", r"\vspace\myskip"]:
            self.assertEqual(compare_layout_policy("", command)["status"], "manual_review")

    def test_exact_independent_approval_does_not_create_a_broad_bypass(self) -> None:
        source = "\\vspace{-0.3em}\n"
        approval = {
            "approved": True, "file": "sections/results.tex", "line": 1,
            "command": "vspace", "arguments": ["-0.3em"],
            "evidence": {"source": "host_review", "reason": "checked a local section gap", "scope": "local", "visual_inspected": True, "page_images": ["page_003.png"]},
        }
        self.assertEqual(compare_layout_policy("", source, approvals=[approval], file="sections/results.tex")["status"], "manual_review")
        for changed in [source.replace("0.3", "0.6"), "\n" + source, source + r"\newpage"]:
            self.assertNotEqual(compare_layout_policy("", changed, approvals=[approval], file="sections/results.tex")["status"], "pass")
        spoof = {**approval, "evidence": {**approval["evidence"], "source": "vlm"}}
        self.assertEqual(compare_layout_policy("", source, approvals=[spoof], file="sections/results.tex")["status"], "manual_review")

    def test_endmatter_flush_can_use_separate_scoped_host_evidence(self) -> None:
        approval = {
            "approved": True, "file": "main.tex", "line": 1,
            "command": "clearpage", "arguments": [],
            "evidence": {"source": "host_review", "reason": "confirmed body float in references", "scope": "endmatter", "endmatter_intrusion": True, "visual_inspected": True, "page_images": ["page_008.png"]},
        }
        self.assertEqual(compare_layout_policy("", r"\clearpage", file="main.tex", approvals=[approval])["status"], "manual_review")
        del approval["evidence"]["endmatter_intrusion"]
        self.assertEqual(compare_layout_policy("", r"\clearpage", file="main.tex", approvals=[approval])["status"], "manual_review")

    def test_c1_unwrap_and_d2_native_reflow_pass(self) -> None:
        scaled = manuscript(TABLE.replace(TABULAR, r"\resizebox{\linewidth}{!}{" + TABULAR + "}"))
        diff = compute_content_diff(scaled, manuscript(TABLE))
        self.assertEqual(diff["scientific_payload"]["status"], "pass")
        self.assertEqual(diff["layout_policy"]["status"], "pass")
        equation = r"\begin{equation}x+y=1\label{eq:a}\end{equation}"
        broken = equation.replace("x+y=1", r"\begin{aligned}x+y&={}\\1\end{aligned}")
        diff = compute_content_diff(manuscript(equation), manuscript(broken))
        self.assertEqual(diff["scientific_payload"]["status"], "pass")
        self.assertEqual(diff["layout_policy"]["status"], "pass")

    def test_executor_retains_positive_scaling_for_explicit_review_without_scientific_damage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tex = root / "main.tex"
            original = manuscript(TABLE).replace("\n", "\r\n").encode("utf-8")
            tex.write_bytes(original)
            plan = root / "plan.json"
            plan.write_text(json.dumps({"candidates": [{"candidate_type": "log_warning", "defect_family": "D2", "line_number": 3, "overflow_amount": 12.0, "target": {"scope": "overflow"}}]}), encoding="utf-8")
            def pseudo_fix(**kwargs):
                tex.write_text(manuscript(TABLE).replace(TABULAR, r"\resizebox{0.1\linewidth}{!}{" + TABULAR + "}"), encoding="utf-8")
                return {"status": "success", "changes": [{"success": True, "defect_id": "D2"}], "unresolved": []}
            with mock.patch("repair_plan_executor.execute_overflow_candidates", side_effect=pseudo_fix):
                report = execute_repair_plan(str(plan), str(tex), str(root / "report.json"))
            self.assertEqual(report["status"], "manual_review")
            self.assertFalse(report["success"])
            self.assertNotIn("rollback_reason", report)
            self.assertEqual(report["content_integrity"]["diff"]["scientific_payload"]["status"], "pass")
            self.assertFalse(report["layout_policy"]["rollback_performed"])
            self.assertIn(r"\resizebox{0.1\linewidth}", tex.read_text(encoding="utf-8"))
            self.assertEqual(json.loads((root / "report.json").read_text(encoding="utf-8"))["status"], "manual_review")

    def test_executor_policy_keeps_changed_include_pending_for_explicit_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            main = root / "main.tex"
            include = root / "table.tex"
            main.write_text(manuscript(r"\input{table}"), encoding="utf-8")
            original = TABLE.encode("utf-8")
            include.write_bytes(original)
            originals = {main: main.read_bytes(), include: original}
            include.write_text(TABLE.replace(TABULAR, r"\scalebox{0.1}{" + TABULAR + "}"), encoding="utf-8")
            report = _enforce_structure_integrity(main_tex=str(main), original_tex=main.read_text(encoding="utf-8"), report={"status": "success", "applied_count": 1}, original_sources=originals)
            self.assertEqual(report["status"], "manual_review")
            self.assertFalse(report["success"])
            self.assertEqual(report["layout_policy"]["findings"][0]["file"], "table.tex")
            self.assertFalse(report["layout_policy"]["rollback_performed"])
            self.assertIn(r"\scalebox{0.1}", include.read_text(encoding="utf-8"))

    def test_layout_executor_rejects_small_ordinary_prose_claim_flip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tex = root / "main.tex"
            before = manuscript("The method does not improve accuracy.")
            original = before.replace("\n", "\r\n").encode("utf-8")
            tex.write_text(before.replace("does not improve", "does improve"), encoding="utf-8")
            report = _enforce_structure_integrity(main_tex=str(tex), original_tex=before, original_bytes=original, report={"status": "success", "applied_count": 1})
            self.assertEqual(report["content_integrity"]["diff"]["scientific_payload"]["status"], "pass")
            self.assertEqual(report["layout_prose"]["status"], "blocked")
            self.assertTrue(report["layout_prose"]["rollback_performed"])
            self.assertEqual(report["rollback_reason"], "layout_prose_changed")
            self.assertEqual(tex.read_bytes(), original)

    def test_nested_scientific_mutation_rolls_back_before_candidate_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            main = root / "main.tex"
            include = root / "equation.tex"
            main.write_text(manuscript(r"\input{equation}"), encoding="utf-8")
            original = b"\\begin{equation}x+y=1\\label{eq:a}\\end{equation}\r\n"
            include.write_bytes(original.replace(b"=1", b"=2"))
            report = _enforce_structure_integrity(main_tex=str(main), original_tex=main.read_text(encoding="utf-8"), report={"status": "success", "applied_count": 1}, original_sources={main: main.read_bytes(), include: original})
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["rollback_reason"], "scientific_integrity_violation")
            self.assertTrue(report["scientific_payload"]["rollback_performed"])
            self.assertEqual(report["scientific_payload"]["findings"][0]["file"], "equation.tex")
            self.assertEqual(include.read_bytes(), original)

    def test_deleted_tracked_include_is_blocked_and_restored_from_cached_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            main = root / "main.tex"
            include = root / "sections" / "equation.tex"
            include.parent.mkdir()
            main.write_text(manuscript(r"\input{sections/equation}"), encoding="utf-8")
            original = b"\\begin{equation}x+y=1\\label{eq:a}\\end{equation}\r\n"
            include.write_bytes(original)
            originals = {main: main.read_bytes(), include: original}
            include.unlink()
            include.parent.rmdir()
            report = _enforce_structure_integrity(main_tex=str(main), original_tex=main.read_text(encoding="utf-8"), report={"status": "success", "applied_count": 1}, original_sources=originals)
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["applied_count"], 0)
            self.assertEqual(report["scientific_payload"]["status"], "manual_review")
            self.assertEqual(report["layout_policy"]["findings"][0]["rule"], "tracked_source_unavailable")
            self.assertEqual(include.read_bytes(), original)


class LiveLayoutPolicyTest(unittest.TestCase):
    def test_live_nested_formula_mutation_cannot_trust_fake_executor_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "paper").mkdir()
            (root / "paper" / "main.tex").write_text(manuscript(r"\input{equation}"), encoding="utf-8")
            equation = root / "paper" / "equation.tex"
            before = r"\begin{equation}x+y=1\label{eq:a}\end{equation}"
            equation.write_text(before, encoding="utf-8")
            snapshot = create_pre_repair_snapshot(project_root=root, main_tex="paper/main.tex", snapshot_id="formula")
            (root / "visual.json").write_text(json.dumps({"summary": {"pages_analyzed": 1}, "findings": []}), encoding="utf-8")
            state = {"compile_success": True, "page_images_rendered": True, "task": {"main_tex": "paper/main.tex"}, "artifacts": {"visual_signal_report": "visual.json"}, "visual_signals_summary": {"updated_at": "now"}, "content_integrity": {"validation_status": "mutation_reported", "violation_level": 0, "rollback_target": snapshot["rollback_target"]}, "repair_execution_summary": {"status": "success", "applied_count": 1}}
            equation.write_text(before.replace("=1", "=2"), encoding="utf-8")
            cwd = Path.cwd()
            try:
                os.chdir(root)
                decision = make_decision(state, {"defects": []}, False, None)
                self.assertEqual(decision["decision"], "BLOCKED")
                self.assertTrue(decision["checks"]["layout_policy"]["pass"])
                self.assertTrue(decision["checks"]["layout_prose"]["pass"])
                self.assertFalse(decision["checks"]["scientific_payload"]["pass"])
                self.assertEqual(decision["checks"]["scientific_payload"]["findings"][0]["file"], "paper/equation.tex")
                spec = TaskSpec.from_dict({"task_type": "full_vto", "project_root": str(root), "main_tex": "paper/main.tex", "allow_source_mutation": True, "pre_repair_snapshot_required": True, "rollback_policy": "required"})
                runtime = OrchestratorRuntime(state_path=str(root / "data" / "state.json"))
                self.assertFalse(runtime._refresh_round_metrics_after(state=state, task_spec=spec, latest={}).content_integrity_pass)
                equation.write_text(before, encoding="utf-8")
                self.assertTrue(runtime._refresh_round_metrics_after(state=state, task_spec=spec, latest={}).content_integrity_pass)
            finally:
                os.chdir(cwd)

    def test_round_content_status_cannot_override_changed_live_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            main = root / "main.tex"
            before = manuscript("The method does not improve accuracy.")
            main.write_text(before, encoding="utf-8")
            snapshot = create_pre_repair_snapshot(project_root=root, main_tex="main.tex", snapshot_id="round_policy")
            (root / "visual.json").write_text(json.dumps({"summary": {"pages_analyzed": 1}, "findings": []}), encoding="utf-8")
            state = {"task": {"main_tex": "main.tex"}, "artifacts": {"visual_signal_report": "visual.json"}, "content_integrity": {"validation_status": "mutation_reported", "rollback_target": snapshot["rollback_target"]}}
            spec = TaskSpec.from_dict({"task_type": "full_vto", "project_root": str(root), "main_tex": "main.tex", "allow_source_mutation": True, "target_pages": 1, "pre_repair_snapshot_required": True, "rollback_policy": "required"})
            runtime = OrchestratorRuntime(state_path=str(root / "data" / "state.json"))
            cwd = Path.cwd()
            try:
                os.chdir(root)
                self.assertTrue(runtime._refresh_round_metrics_after(state=state, task_spec=spec, latest={}).content_integrity_pass)
                main.write_text(before.replace("does not improve", "does improve"), encoding="utf-8")
                evidence = runtime._refresh_round_metrics_after(state=state, task_spec=spec, latest={})
                self.assertFalse(evidence.content_integrity_pass)
                main.write_text(before, encoding="utf-8")
                self.assertTrue(runtime._refresh_round_metrics_after(state=state, task_spec=spec, latest={}).content_integrity_pass)
                state["content_integrity"]["rollback_target"] = "absent.json"
                self.assertFalse(runtime._refresh_round_metrics_after(state=state, task_spec=spec, latest={}).content_integrity_pass)
            finally:
                os.chdir(cwd)

    def test_live_gate_recomputes_nested_include_and_ignores_clean_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "paper").mkdir()
            (root / "paper" / "main.tex").write_text(manuscript(r"\input{table}"), encoding="utf-8")
            include = root / "paper" / "table.tex"
            include.write_text(TABLE, encoding="utf-8")
            snapshot = create_pre_repair_snapshot(project_root=root, main_tex="paper/main.tex", snapshot_id="policy")
            state = {"compile_success": True, "page_images_rendered": True, "artifacts": {"visual_signal_report": "visual.json"}, "visual_signals_summary": {"updated_at": "now"}, "content_integrity": {"validation_status": "mutation_reported", "violation_level": 0, "rollback_target": snapshot["rollback_target"]}, "repair_execution_summary": {"status": "success", "applied_count": 1}}
            (root / "visual.json").write_text(json.dumps({"summary": {"pages_analyzed": 1}, "findings": []}), encoding="utf-8")
            cwd = Path.cwd()
            try:
                os.chdir(root)
                self.assertEqual(make_decision(state, {"defects": []}, False, None)["decision"], "DONE")
                include.write_text(TABLE.replace(TABULAR, r"\resizebox{0.1\linewidth}{!}{" + TABULAR + "}"), encoding="utf-8")
                decision = make_decision(state, {"defects": []}, False, None)
                self.assertEqual(decision["decision"], "CONTINUE")
                self.assertEqual(decision["checks"]["layout_policy"]["status"], "manual_review")
                self.assertFalse(decision["gates_passed"]["layout_policy"])
                self.assertEqual(decision["checks"]["layout_policy"]["findings"][0]["file"], "paper/table.tex")
                include.unlink()
                self.assertEqual(make_decision(state, {"defects": []}, False, None)["decision"], "CONTINUE")
            finally:
                os.chdir(cwd)

    def test_missing_or_invalid_baseline_cannot_accept_a_source_changing_run(self) -> None:
        state = {"compile_success": True, "page_images_rendered": True, "artifacts": {"visual_signal_report": "visual.json"}, "visual_signals_summary": {"updated_at": "now"}, "repair_execution_summary": {"status": "success", "applied_count": 1}}
        decision = make_decision(state, {"defects": []}, False, None)
        self.assertEqual(decision["decision"], "CONTINUE")
        self.assertFalse(decision["checks"]["layout_policy"]["checked"])
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(snapshot_layout_policy(Path(directory), "absent.json")["status"], "manual_review")

    def test_nested_phased_metrics_use_adjacent_pdf_and_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "paper").mkdir()
            (root / "paper" / "main.tex").write_text(manuscript(TABLE), encoding="utf-8")
            (root / "main.log").write_text("", encoding="utf-8")
            (root / "paper" / "main.log").write_text("Overfull \\hbox (123.0pt too wide) in paragraph at lines 3--3", encoding="utf-8")
            requested = []
            def count(pdf):
                requested.append(pdf)
                return 2 if pdf == root / "paper" / "main.pdf" else 99
            evidence = collect_evidence(root, "paper/main.tex", count)
            self.assertEqual(requested, [root / "paper" / "main.pdf"])
            self.assertEqual(evidence.page_count, 2)
            self.assertEqual(evidence.severe_overfull, 1)


if __name__ == "__main__":
    unittest.main()
