"""Recommendations remain reviewable leads, even with confident model claims."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from host_context import build_host_context
from page_fit_toolbox import build_toolbox, source_inventory, planning_review_candidates
from visual_action_plan import build_visual_action_plan
from repair_plan_generator import generate_repair_plan
from repair_plan_executor import execute_repair_plan
from runtime_approval import build_approval_object, build_candidate_approval_scope_gate
from paperfit_command import _derive_priority_items


class HostAdviceTest(unittest.TestCase):
    def fixture(self, files):
        temporary = tempfile.TemporaryDirectory(prefix="paperfit-host-advice-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for name, text in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return root

    def test_confident_model_cannot_confirm_its_own_suggested_repair(self):
        finding = {
            "defect_id": "reported-width", "taxonomy_defect_id": "B2",
            "source": "vlm_detector", "confidence": 1.0, "page": 1,
            "description": "The image seems too wide.",
            "metrics": {"label": "fig:one", "object_kind": "figure_like"},
            "host_review": {"verification": "accepted", "evidence_status": "current"},
        }
        plan = build_visual_action_plan(visual_report={"findings": [finding]},
            crossrefs_report={"source_file": "main.tex", "floats": [
                {"label": "fig:one", "float_type": "figure", "line_number": 4}]})
        entry = plan["entries"][0]
        self.assertEqual(entry["status"], "executable")  # Existing optional batch route.
        self.assertEqual(entry["host_review"]["verification"], "pending_host")
        self.assertEqual(entry["host_review"]["evidence_status"], "unverified")
        self.assertFalse(plan["host_guidance"]["machine_status_is_visual_verification"])
        self.assertFalse(plan["host_guidance"]["recommendation_is_authorization"])
        self.assertEqual(set(plan["host_guidance"]["host_choices"]), {"accept", "adapt", "reject"})

    def test_advice_does_not_approve_unanchored_document_mutation(self):
        plan = build_visual_action_plan(visual_report={"findings": [{
            "defect_id": "placement", "taxonomy_defect_id": "B1", "confidence": .99,
            "metrics": {"document_scope_approved": True,
                        "hard_guard_evidence": {"failure_type": "float_stacking"}},
            "document_scope_approved": True,
        }]}, crossrefs_report={})
        entry = plan["entries"][0]
        self.assertEqual(entry["status"], "human_review")
        self.assertNotIn("normalize_float_placements_document_level", entry["recommended_actions"])
        self.assertEqual(entry["host_review"]["verification"], "pending_host")

    def test_live_ambiguous_target_does_not_inherit_executable_or_host_confirmation(self):
        source = (r"\documentclass{article}\begin{document}"
                  r"\begin{table}\label{tab:dup}A\end{table}"
                  r"\begin{table}\label{tab:dup}B\end{table}\end{document}")
        root = self.fixture({"main.tex": source})
        data = root / "data"; data.mkdir()
        (data / "visual_action_plan.json").write_text(json.dumps({"entries": [{
            "finding_id": "dup", "label": "tab:dup", "object_type": "table",
            "status": "executable", "confidence": 1, "page": 1,
            "host_review": {"verification": "accepted"},
        }]}), encoding="utf-8")
        before = (root / "main.tex").read_bytes()
        result = build_host_context(root / "main.tex", root=root, target_pages=1)
        entry = result["findings"][0]
        self.assertEqual(entry["status"], "human_review")
        self.assertEqual(entry["source_anchor_status"], "ambiguous")
        self.assertEqual(entry["host_review"]["target_status"], "ambiguous")
        self.assertEqual(entry["host_review"]["verification"], "pending_host")
        self.assertEqual(entry["host_review"]["evidence_status"], "unknown")
        self.assertEqual(entry["host_review"]["reviewer"], "editing_host")
        authority = result["host_guidance"]["editing_authority"]
        self.assertTrue(authority["native_editing_supported"])
        self.assertFalse(authority["tool_routes_limit_host_edits"])
        self.assertFalse(authority["per_finding_acknowledgement_required"])
        self.assertTrue(result["layout_plan"]["available_before_defects_stable"])
        self.assertIsNone(result["page_objective"]["target_met"])
        self.assertEqual(before, (root / "main.tex").read_bytes())

    def test_generated_plan_and_noop_executor_preserve_native_host_alternative(self):
        root = self.fixture({"main.tex": r"\documentclass{article}\begin{document}Fixed scientific words.\end{document}",
                             "visual.json": json.dumps({"findings": []})})
        before = (root / "main.tex").read_bytes()
        plan = generate_repair_plan(str(root / "visual.json"), str(root / "plan.json"))
        report = execute_repair_plan(str(root / "plan.json"), "main.tex", "execution.json",
                                     project_root=root)
        self.assertEqual(plan["candidates"], [])
        self.assertEqual(report["status"], "noop")
        for payload in (plan, report):
            authority = payload["host_guidance"]["editing_authority"]
            self.assertTrue(authority["native_editing_supported"])
            self.assertFalse(authority["strategy_menu_is_exhaustive"])
            self.assertFalse(authority["ordinary_layout_reapproval_required"])
            self.assertFalse(payload["host_guidance"]["recommendation_is_authorization"])
        self.assertEqual(report["execution_scope"], "optional_batch_executor")
        self.assertEqual(before, (root / "main.tex").read_bytes())

    def test_high_risk_batch_deferral_does_not_revoke_existing_native_host_scope(self):
        task = {"task_type": "full_vto", "dry_run_source_mutation": True}
        approval = build_approval_object(task=task,
            state={"repair_plan_summary": {"total_candidates": 1}},
            runtime_actions={"repair_plan_executor": {
                "skipped": True, "reason": "dry_run_source_mutation", "planned_candidates": 1}})
        candidate = {"proposed_action": "convert_to_longtable", "risk": {
            "operation": "table_reconstruction", "risk_level": "high",
            "mutation_surface": ["table_environment"]}}
        gate = build_candidate_approval_scope_gate(task=task, approval=approval,
            repair_plan={"candidates": [candidate]})
        self.assertEqual(gate["status"], "approval_required")
        self.assertEqual(gate["selected_candidate_count"], 0)
        for payload in (approval, gate):
            self.assertEqual(payload["authority_scope"], "optional_batch_executor")
            self.assertTrue(payload["host_guidance"]["editing_authority"]["native_editing_supported"])
            self.assertFalse(payload["host_guidance"]["editing_authority"]["tool_routes_limit_host_edits"])
        self.assertEqual(approval["policy"]["applies_to"], "optional_batch_executor")
        item = next(item for item in _derive_priority_items({"approval": approval})
                    if item["priority"] == "review_repair_plan_approval")
        self.assertEqual(item["evidence"]["authority_scope"], "optional_batch_executor")
        self.assertIn("native editing", item["evidence"]["native_host_alternative"])

    def test_analysis_report_does_not_grant_source_mutation_authority(self):
        approval = build_approval_object(task={"task_type": "analysis"}, state={}, runtime_actions={})
        self.assertEqual(approval["status"], "not_applicable")
        self.assertFalse(approval["approval_granted"])
        self.assertFalse(approval["host_guidance"]["recommendation_is_authorization"])
        self.assertIn("Analysis-only requests do not authorize mutation",
                      approval["host_guidance"]["editing_authority"]["scope_note"])

    def test_page_plan_exposes_template_and_large_gap_leads_before_compile(self):
        root = self.fixture({"main.tex": "\n".join([
            r"\documentclass{article}", r"\addtolength{\textheight}{-8\baselineskip}",
            r"\begin{document}\input{body}\end{document}"]),
            "body.tex": "\n".join([r"Fixed scientific words.",
                                     r"\vspace*{0.18\textheight}", r"References."])})
        before = {p.name: p.read_bytes() for p in root.glob("*.tex")}
        result = build_toolbox(root / "main.tex", root=root, target_pages=2)
        self.assertEqual(result["phase"], "needs_current_measurement")
        self.assertTrue(result["layout_plan"]["available_before_defects_stable"])
        self.assertFalse(result["tier_order_required"])
        candidates = result["planning_review_candidates"]
        self.assertEqual([c["kind"] for c in candidates], ["explicit_template_dimension", "large_explicit_gap"])
        self.assertEqual([(c["source_anchor"]["file"], c["source_anchor"]["line"]) for c in candidates],
                         [("main.tex", 2), ("body.tex", 2)])
        for candidate in candidates:
            anchor = candidate["source_anchor"]
            self.assertEqual(anchor["source_sha256"], hashlib.sha256(before[anchor["file"]]).hexdigest())
            self.assertFalse(candidate["defect_confirmed"])
            self.assertIsNone(candidate["predicted_pages_saved"])
            self.assertEqual(candidate["host_review"]["verification"], "pending_host")
        self.assertIsNone(result["page_objective"]["target_met"])
        self.assertFalse(result["automatic_acceptance"])
        self.assertEqual(before, {p.name: p.read_bytes() for p in root.glob("*.tex")})

    def test_inactive_prototype_and_literal_examples_do_not_become_priorities(self):
        root = self.fixture({"main.tex": "\n".join([
            r"\documentclass{article}",
            r"% \addtolength{\textheight}{-8\baselineskip}",
            r"\newcommand{\unused}{\vspace{0.5\textheight}}",
            r"\begin{document}", r"\verb|\vspace{50pt}|",
            r"\begin{verbatim}\vspace{90pt}\end{verbatim}",
            r"\vspace{1pt}Fixed words.", r"\end{document}",
            r"\vspace{80pt}"])})
        inventory = source_inventory(root / "main.tex", root)
        self.assertEqual(planning_review_candidates(inventory), [])

    def test_conditional_gap_is_navigation_and_never_executed_proof(self):
        root = self.fixture({"main.tex": r"\iffalse\input{inactive}\fi",
                             "inactive.tex": r"\vspace{0.18\textheight}"})
        candidate = planning_review_candidates(source_inventory(root / "main.tex", root))[0]
        self.assertEqual(candidate["host_review"]["target_status"], "requires_host_review")
        self.assertFalse(candidate["defect_confirmed"])

    def test_unparsed_custom_length_is_not_a_quantified_large_gap(self):
        root = self.fixture({"main.tex": r"\begin{document}\vspace{\customgap}Words.\end{document}"})
        inventory = source_inventory(root / "main.tex", root)
        self.assertEqual(inventory["entries"][0]["value"], r"\customgap")
        self.assertEqual(planning_review_candidates(inventory), [])

    def test_localized_advice_stdout_is_utf8_even_with_gbk_console_default(self):
        root = self.fixture({"main.tex": r"\documentclass{article}\begin{document}Words.\end{document}"})
        scripts = Path(__file__).resolve().parent
        for script, extra in [("host_context.py", []), ("page_fit_toolbox.py", ["--target-pages", "1"])]:
            with self.subTest(script=script):
                result = subprocess.run([sys.executable, "-B", str(scripts / script),
                    "--main", str(root / "main.tex"), "--root", str(root), *extra],
                    env={**os.environ, "PYTHONIOENCODING": "gbk"}, capture_output=True)
                payload = json.loads(result.stdout.decode("utf-8"))
                self.assertIn("请你看看这是否是对的", payload["host_guidance"]["verification_question"])
                self.assertFalse(payload["host_guidance"]["recommendation_is_authorization"])


if __name__ == "__main__":
    unittest.main()
