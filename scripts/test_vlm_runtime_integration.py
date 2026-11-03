"""Offline regression of the real inspect-project VLM writeback and DONE guard.

The runtime, StateManager, diagnosis merge, action plan and artifact manifest
remain real. Only external inspectors and the VLM service are replaced.
Native input verification uses an explicit source/PDF-byte-bound synthetic
inspector double; these fixtures do not create or claim compiler receipts.
"""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest import mock

import fitz

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import vlm_diagnosis
from orchestrator_runtime import OrchestratorRuntime, _resolve_vlm_column_type
from runtime_artifacts import collect_artifact_manifest
from state_manager import StateManager
from visual_action_plan import build_visual_action_plan, write_visual_action_plan
from repair_plan_generator import generate_repair_plan
from test_host_context import mocked_native_verifier_for_fixture


def _write_result(payload):
    def write(*, output_path, **_):
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        result = copy.deepcopy(payload)
        path.write_text(json.dumps(result), encoding="utf-8")
        return result
    return write


def _diagnosis(status="completed", findings=()):
    complete = status == "completed"
    return {
        "schema_version": "1.1", "configured": True, "status": status,
        "success": complete, "clean": not findings if complete else None,
        "coverage": 1.0 if complete else 0.0, "pages_analyzed": 1 if complete else 0,
        "missing_page_numbers": [] if complete else [1], "findings_count": len(findings),
        "error": None if complete else "Offline simulated incomplete inspection",
        "batches": [{"page_numbers": [1], "status": status, "response_model": "fake-vision"}],
    }


class VLMColumnTypeTest(unittest.TestCase):
    def test_task_override_and_registered_aaai_layout_resolve(self):
        self.assertEqual(_resolve_vlm_column_type({"task": {"column_type": "single"}}, "AAAI"), "single")
        self.assertEqual(_resolve_vlm_column_type({"task": {}}, "AAAI"), "double")
        self.assertEqual(_resolve_vlm_column_type({"task": {}}, "AAAI2025"), "double")
        self.assertIsNone(_resolve_vlm_column_type({"task": {}}, "unknown-layout"))

    def test_ambiguous_template_family_is_not_guessed(self):
        registry = {"v1": {"family": "mixed", "column_type": "single"},
                    "v2": {"family": "mixed", "column_type": "double"}}
        with mock.patch("template_registry.load_templates", return_value=registry):
            self.assertIsNone(_resolve_vlm_column_type({"task": {}}, "mixed"))


class VLMWidthRepairRoutingTest(unittest.TestCase):
    def test_caption_anchored_model_width_diagnosis_reaches_typed_b2_candidate(self):
        for subtype, source_width in (("overflow_width", "1.25\\columnwidth"),
                                      ("underfilled_width", "0.45\\columnwidth")):
            with self.subTest(subtype=subtype), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                data = root / "data"
                data.mkdir()
                (root / "main.tex").write_text(
                    "\\documentclass[twocolumn]{article}\n\\begin{document}\n"
                    "\\begin{figure}\n\\includegraphics[width=" + source_width + "]{sample}\n"
                    "\\caption{Fixture figure}\\label{fig:fixture}\n\\end{figure}\n"
                    "\\end{document}\n", encoding="utf-8")
                findings = vlm_diagnosis.parse_vlm_defects(json.dumps({"defects": [{
                    "code": "B2", "page": 1, "severity": "degrading", "caption_number": 1,
                    "width_subtype": subtype, "confidence": 0.95,
                    "evidence": "Figure 1 has visibly inappropriate width for its column."}]}),
                    strict=True, page_numbers=[1])
                visual = {"summary": {"pages_analyzed": 1}, "findings": findings, "hints": []}
                crossrefs = {"source_file": "main.tex", "floats": [{"float_type": "figure",
                    "label": "fig:fixture", "number": 1, "line_number": 3, "width_spec": source_width}]}
                visual_path = data / "visual_signal_report.json"
                crossrefs_path = data / "crossrefs.json"
                visual_path.write_text(json.dumps(visual), encoding="utf-8")
                crossrefs_path.write_text(json.dumps(crossrefs), encoding="utf-8")
                action_plan = build_visual_action_plan(visual_report=visual, crossrefs_report=crossrefs)
                entry = action_plan["entries"][0]
                self.assertEqual(entry["label"], "fig:fixture")
                self.assertEqual(entry["caption_number"], 1)
                self.assertEqual(entry["anchor_status"], "resolved")
                self.assertEqual(entry["defect_type"], subtype)
                self.assertEqual(entry["status"], "executable")
                write_visual_action_plan(action_plan, data / "visual_action_plan.json")
                repair = generate_repair_plan(str(visual_path), str(data / "repair_plan.json"),
                                              crossrefs_report=str(crossrefs_path))
                typed = [candidate for candidate in repair["candidates"]
                         if candidate.get("defect_family") == "B2"
                         and candidate.get("proposed_action") == "adjust_float_width"
                         and (candidate.get("target") or {}).get("label") == "fig:fixture"
                         and candidate.get("visual_width_subtype") == subtype]
                self.assertTrue(typed, repair)
                promoted = [candidate for candidate in typed if candidate.get("candidate_type") == "visual_plan"]
                self.assertTrue(promoted, repair)
                for candidate in promoted:
                    self.assertEqual(candidate["description"],
                                     "Figure 1 has visibly inappropriate width for its column.")


class InspectProjectVLMIntegrationTest(unittest.TestCase):
    @contextmanager
    def _inspect(self, *, status="completed", findings=(), merge_error=False,
                 task_column_type="double", template="AAAI2025"):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory).resolve()
            (root / "data" / "pages").mkdir(parents=True)
            (root / "main.tex").write_text(
                "\\documentclass[twocolumn]{article}\n\\begin{document}\n"
                "Offline vision integration fixture.\n\\end{document}\n", encoding="utf-8")
            with fitz.open() as pdf:
                page = pdf.new_page()
                page.insert_text((40, 40), "Offline vision integration fixture")
                pdf.save(root / "main.pdf")
                page.get_pixmap().save(root / "data" / "pages" / "page_001.png")
            stack.enter_context(mock.patch('runtime_artifacts.verify_native_build',
                side_effect=mocked_native_verifier_for_fixture(root)))
            previous_cwd = Path.cwd()
            os.chdir(root)
            stack.callback(os.chdir, previous_cwd)
            runtime = OrchestratorRuntime(state_path=str(root / "data" / "state.json"))
            runtime.init_task("main.tex", task_type="visual_only", template=template,
                              column_type=task_column_type)
            self.assertIsInstance(runtime.manager, StateManager)
            cv_report = {
                "schema_version": "1.0", "findings": [], "hints": [],
                "summary": {"pages_analyzed": 1, "total_findings": 0, "category_breakdown": {}},
                "machine_sources": {"cv_detector": {"enabled": True, "page_results_count": 1}},
                "routing_hints": {"priority_pages": []},
            }
            responses = {
                "_parse_log_to_report": {"compile_success": True, "defects": []},
                "_extract_crossrefs": {"source_file": "main.tex", "floats": [
                    {"float_type": "figure", "label": "fig:fixture", "number": 1, "line_number": 3}]},
                "_run_source_hygiene_check": {"findings": []},
                "_run_visual_signal_aggregator": cv_report,
                "_run_defect_report_builder": {"schema_version": "1.0", "defects": []},
            }
            for name, response in responses.items():
                stack.enter_context(mock.patch.object(runtime, name, side_effect=_write_result(response)))
            stack.enter_context(mock.patch.object(runtime, "_run_repair_plan_generator", return_value={}))
            stack.enter_context(mock.patch("orchestrator_runtime.subprocess.run",
                                           side_effect=AssertionError("external command must be mocked")))
            stack.enter_context(mock.patch("vlm_diagnosis.urlopen",
                                           side_effect=AssertionError("integration tests must not call an API")))
            stack.enter_context(mock.patch("vlm_diagnosis.vlm_diagnosis_configured", return_value=True))
            service = stack.enter_context(mock.patch("vlm_diagnosis.run_vlm_diagnosis", return_value=(
                copy.deepcopy(list(findings)), _diagnosis(status, findings))))
            if merge_error:
                stack.enter_context(mock.patch("vlm_diagnosis.merge_vlm_findings",
                                               side_effect=AttributeError("simulated real-shape merge failure")))
            actions = {}
            events = []
            runtime.inspect_project(
                main_tex="main.tex", page_dir="data/pages", template=template,
                initialize_task=False, record_history=False, runtime_actions=actions,
                emit_event=lambda event_type, **payload: events.append({"type": event_type, **payload}),
            )
            persisted = json.loads((root / "data" / "visual_signal_report.json").read_text(encoding="utf-8"))
            gatekeeper = root / "data" / "gatekeeper_decision.json"
            gatekeeper.write_text('{"decision": "DONE"}', encoding="utf-8")
            runtime.set_artifact("gatekeeper_decision", "data/gatekeeper_decision.json")
            state = runtime.manager.load()
            manifest = collect_artifact_manifest(root, "main.tex", state["artifacts"])
            yield {"runtime": runtime, "root": root, "service": service,
                   "report": persisted, "actions": actions, "events": events,
                   "state": state, "manifest": manifest}

    def test_completed_zero_findings_calls_vision_and_persists_success(self):
        with self._inspect() as result:
            service = result["service"]
            service.assert_called_once_with(Path("data/pages"), template_name="AAAI2025", column_type="double")
            self.assertEqual(result["report"]["findings"], [])
            self.assertEqual(result["report"]["vlm_diagnosis"]["status"], "completed")
            self.assertTrue(result["report"]["vlm_diagnosis"]["clean"])
            self.assertTrue(result["actions"]["vlm_diagnosis"]["success"])
            self.assertEqual(result["actions"]["vlm_diagnosis"]["findings_added"], 0)
            self.assertEqual(result["manifest"]["freshness"]["status"], "pass")
            self.assertTrue(result["manifest"]["vision_diagnosis"]["configured"])
            self.assertIsNone(result["runtime"]._terminal_visual_evidence_failure(
                decision="DONE", artifact_manifest=result["manifest"]))

    def test_aaai_family_resolves_double_when_task_override_is_absent(self):
        with self._inspect(task_column_type=None, template="AAAI") as result:
            self.assertEqual(result["service"].call_args.kwargs["column_type"], "double")
            self.assertTrue(result["actions"]["vlm_diagnosis"]["success"])

    def test_nonzero_findings_merge_with_real_dictionary_provenance(self):
        findings = vlm_diagnosis.parse_vlm_defects(json.dumps({"defects": [{
            "code": "B2", "page": 1, "severity": "degrading", "caption_number": 1,
            "confidence": 0.9, "evidence": "Figure 1 extends beyond its column."}]}))
        with self._inspect(findings=findings) as result:
            self.assertEqual(result["report"]["findings"], findings)
            self.assertEqual(result["report"]["machine_sources"]["cv_detector"],
                             {"enabled": True, "page_results_count": 1})
            self.assertEqual(result["report"]["machine_sources"]["vlm_detector"]["findings_added"], 1)
            self.assertEqual(result["actions"]["vlm_diagnosis"]["findings_added"], 1)
            self.assertTrue(result["actions"]["vlm_diagnosis"]["success"])
            self.assertFalse(result["report"]["vlm_diagnosis"]["clean"])

    def test_incomplete_diagnosis_is_persisted_and_cannot_pass_done_guard(self):
        for status in ("partial", "failed"):
            with self.subTest(status=status), self._inspect(status=status) as result:
                self.assertEqual(result["report"]["vlm_diagnosis"]["status"], status)
                self.assertFalse(result["actions"]["vlm_diagnosis"]["success"])
                self.assertIsNone(result["report"]["vlm_diagnosis"]["clean"])
                self.assertEqual(result["manifest"]["freshness"]["status"], "pass")
                self.assertEqual(result["manifest"]["vision_diagnosis"]["status"], status)
                failure = result["runtime"]._terminal_visual_evidence_failure(
                    decision="DONE", artifact_manifest=result["manifest"])
                self.assertEqual(failure["failure_type"], "terminal_success_without_complete_vlm_evidence")
                blocked = result["runtime"]._record_terminal_visual_guard_failure(failure)
                self.assertEqual(blocked["status"], "BLOCKED")

    def test_merge_exception_preserves_configured_failure_after_successful_api_result(self):
        with self._inspect(merge_error=True) as result:
            diagnosis = result["report"]["vlm_diagnosis"]
            self.assertTrue(diagnosis["configured"])
            self.assertEqual(diagnosis["status"], "failed")
            self.assertIsNone(diagnosis["clean"])
            self.assertIn("AttributeError", diagnosis["error"])
            self.assertFalse(result["actions"]["vlm_diagnosis"]["success"])
            failure = result["runtime"]._terminal_visual_evidence_failure(
                decision="DONE", artifact_manifest=result["manifest"])
            self.assertEqual(failure["failure_type"], "terminal_success_without_complete_vlm_evidence")
            self.assertEqual(result["runtime"]._record_terminal_visual_guard_failure(failure)["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
