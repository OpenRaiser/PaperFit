from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.runtime_status import build_runtime_status


class RuntimeStatusTest(unittest.TestCase):
    def test_status_view_summarizes_state_event_projection_and_repair_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "EVALUATING",
                "last_gatekeeper_decision": "CONTINUE",
                "task": {"type": "visual_only"},
                "defect_summary": {"initial_total": 3, "resolved": 1, "remaining": 2},
                "runtime_event_summary": {
                    "run_id": "run1",
                    "event_log": "data/events/run1.ndjson",
                    "event_count": 7,
                    "last_event_type": "task_completed",
                    "last_phase": None,
                    "last_runtime_state": "CONTINUE",
                    "actions": {"compile": {"success": True}},
                },
                "artifacts": {
                    "task_spec": "data/task.json",
                    "page_images_dir": "data/pages",
                    "visual_signal_report": "data/visual_signal_report.json",
                    "defect_report": "data/defect_report.json",
                    "repair_plan": "data/repair_plan.json",
                    "repair_execution_report": None,
                    "rollback_report": None,
                },
                "repair_plan_summary": {
                    "total_candidates": 4,
                    "immutability_policy": "invalidate_on_source_change",
                    "source_fingerprint_sha256": "abc123",
                },
                "repair_execution_summary": {"status": None, "applied_count": 0},
                "content_integrity": {
                    "validation_status": "snapshot_created",
                    "rollback_target": "data/snapshots/s1/snapshot_manifest.json",
                },
                "next_actions": ["Review defects"],
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            run_result = {
                "run_id": "run1",
                "gatekeeper_decision": "CONTINUE",
                "event_log": "data/events/run1.ndjson",
                "artifact_manifest": {
                    "freshness": {
                        "status": "pass",
                        "blocking_checks": [],
                    }
                },
            }
            (root / "data" / "run_result_check_visual.json").write_text(json.dumps(run_result), encoding="utf-8")

            status = build_runtime_status(project_root=root)

            self.assertEqual(status["main_tex"], "main.tex")
            self.assertEqual(status["runtime"]["event_count"], 7)
            self.assertTrue(status["runtime"]["actions"]["compile"]["success"])
            self.assertEqual(status["repair"]["plan_candidates"], 4)
            self.assertEqual(status["repair"]["plan_immutability_policy"], "invalidate_on_source_change")
            self.assertEqual(status["artifact_freshness"]["status"], "pass")
            self.assertEqual(status["next_actions"], ["Review defects"])

    def test_status_view_discovers_full_vto_dry_run_result_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "EVALUATING",
                "task": {"type": "full_vto"},
                "runtime_event_summary": {
                    "run_id": "run-dry",
                    "event_log": "data/events/run-dry.ndjson",
                    "event_count": 5,
                },
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            run_result = {
                "run_id": "run-dry",
                "gatekeeper_decision": "CONTINUE",
                "event_log": "data/events/run-dry.ndjson",
                "artifact_manifest": {
                    "freshness": {
                        "status": "pass",
                        "blocking_checks": [],
                    }
                },
            }
            (root / "data" / "run_result_full_vto_dry_run.json").write_text(json.dumps(run_result), encoding="utf-8")

            status = build_runtime_status(project_root=root)

            self.assertEqual(status["run_result_path"], "data/run_result_full_vto_dry_run.json")
            self.assertEqual(status["runtime"]["run_id"], "run-dry")
            self.assertEqual(status["gatekeeper_decision"], "CONTINUE")
            self.assertEqual(status["artifact_freshness"]["status"], "pass")

    def test_status_view_prefers_full_vto_result_over_check_visual_for_source_changing_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "EVALUATING",
                "task": {"type": "full_vto"},
                "runtime_event_summary": {"run_id": "run-dry", "event_count": 5},
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            check_visual_result = {
                "run_id": "run-check",
                "artifact_manifest": {"freshness": {"status": "unknown"}},
            }
            dry_run_result = {
                "run_id": "run-dry",
                "artifact_manifest": {"freshness": {"status": "pass", "blocking_checks": []}},
            }
            (root / "data" / "run_result_check_visual.json").write_text(
                json.dumps(check_visual_result),
                encoding="utf-8",
            )
            (root / "data" / "run_result_full_vto_dry_run.json").write_text(
                json.dumps(dry_run_result),
                encoding="utf-8",
            )

            status = build_runtime_status(project_root=root)

            self.assertEqual(status["run_result_path"], "data/run_result_full_vto_dry_run.json")
            self.assertEqual(status["runtime"]["run_id"], "run-dry")
            self.assertEqual(status["artifact_freshness"]["status"], "pass")

    def test_status_view_discovers_agent_result_and_reports_repair_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "EVALUATING",
                "task": {"type": "full_vto"},
                "runtime_event_summary": {"run_id": "run-agent", "event_count": 8},
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            agent_result = {
                "run_id": "run-agent",
                "runtime_actions": {
                    "repair_plan_executor": {
                        "success": True,
                        "skipped": True,
                        "reason": "dry_run_source_mutation",
                        "risk_level": "high",
                        "requires_approval": True,
                    }
                },
                "artifact_manifest": {"freshness": {"status": "pass", "blocking_checks": []}},
            }
            (root / "data" / "run_result_agent.json").write_text(
                json.dumps(agent_result),
                encoding="utf-8",
            )

            status = build_runtime_status(project_root=root)

            self.assertEqual(status["run_result_path"], "data/run_result_agent.json")
            self.assertEqual(status["runtime"]["run_id"], "run-agent")
            self.assertTrue(status["repair"]["skipped"])
            self.assertEqual(status["repair"]["skip_reason"], "dry_run_source_mutation")
            self.assertEqual(status["repair"]["risk_level"], "high")
            self.assertTrue(status["repair"]["requires_approval"])
            self.assertEqual(status["approval"]["status"], "approval_required")
            self.assertTrue(status["approval"]["requires_approval"])
            self.assertFalse(status["approval"]["approval_granted"])
            self.assertEqual(status["approval"]["reason"], "dry_run_source_mutation")
            self.assertIn("--apply", status["approval"]["approval_mechanisms"])

    def test_status_view_surfaces_repair_loop_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "EVALUATING",
                "task": {"type": "full_vto"},
                "runtime_event_summary": {"run_id": "run-agent", "event_count": 8},
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            agent_result = {
                "run_id": "run-agent",
                "repair_loop_policy": {
                    "schema_version": "1.0",
                    "execution_mode": "report_only",
                    "round_limit": 1,
                    "current_round": 1,
                    "candidate_batch_limit": 0,
                    "stop_condition": "approval_required",
                    "next_round_allowed": False,
                    "next_round_reason": "dry_run_source_mutation",
                },
                "round_artifact_lineage": [
                    {
                        "schema_version": "1.0",
                        "round": 1,
                        "actions": {
                            "repair_plan_executor": {
                                "phase": "repair",
                                "input_artifacts": {"repair_plan": "data/repair_plan.json"},
                                "output_artifacts": {},
                            }
                        },
                    }
                ],
                "artifact_manifest": {"freshness": {"status": "pass", "blocking_checks": []}},
            }
            (root / "data" / "run_result_agent.json").write_text(
                json.dumps(agent_result),
                encoding="utf-8",
            )

            status = build_runtime_status(project_root=root)

            policy = status["repair_loop_policy"]
            self.assertEqual(policy["execution_mode"], "report_only")
            self.assertEqual(policy["round_limit"], 1)
            self.assertFalse(policy["next_round_allowed"])
            self.assertEqual(policy["next_round_reason"], "dry_run_source_mutation")
            self.assertEqual(status["round_artifact_lineage"][0]["round"], 1)
            self.assertIn("repair_plan_executor", status["round_artifact_lineage"][0]["actions"])

    def test_status_view_surfaces_repair_loop_freshness_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "BLOCKED",
                "last_gatekeeper_decision": "DONE",
                "task": {"type": "full_vto"},
                "runtime_event_summary": {"run_id": "run-agent", "event_count": 9},
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            agent_result = {
                "run_id": "run-agent",
                "status": "blocked",
                "gatekeeper_decision": "DONE",
                "repair_loop_policy": {
                    "schema_version": "1.0",
                    "execution_mode": "report_only",
                    "round_limit": 2,
                    "current_round": 1,
                    "stop_condition": "artifact_freshness_not_pass",
                    "next_round_allowed": False,
                    "next_round_reason": "artifact_freshness_not_pass",
                    "second_round_apply_readiness": {
                        "schema_version": "1.0",
                        "status": "blocked",
                        "reason": "artifact_freshness_not_pass",
                        "checks": {
                            "artifact_freshness_pass": False,
                            "runtime_execution_mode_can_auto_apply": False,
                        },
                    },
                },
                "artifact_manifest": {
                    "freshness": {
                        "status": "stale_or_missing",
                        "blocking_checks": ["pdf_exists"],
                    }
                },
                "failure": {
                    "failure_type": "terminal_success_without_fresh_visual_evidence",
                    "reason": "gatekeeper_done_but_artifact_freshness_failed",
                    "artifact_freshness": {
                        "status": "stale_or_missing",
                        "blocking_checks": ["pdf_exists"],
                    },
                },
            }
            (root / "data" / "run_result_agent.json").write_text(
                json.dumps(agent_result),
                encoding="utf-8",
            )

            status = build_runtime_status(project_root=root)

            policy = status["repair_loop_policy"]
            self.assertEqual(status["run_result_path"], "data/run_result_agent.json")
            self.assertEqual(status["artifact_freshness"]["status"], "stale_or_missing")
            self.assertEqual(status["artifact_freshness"]["blocking_checks"], ["pdf_exists"])
            self.assertEqual(policy["stop_condition"], "artifact_freshness_not_pass")
            self.assertEqual(policy["next_round_reason"], "artifact_freshness_not_pass")
            self.assertFalse(policy["next_round_allowed"])
            self.assertFalse(
                policy["second_round_apply_readiness"]["checks"]["artifact_freshness_pass"]
            )
            self.assertEqual(
                status["terminal_success_guard"]["failure_type"],
                "terminal_success_without_fresh_visual_evidence",
            )

    def test_top_level_status_view_cli_uses_runtime_status_contract(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        cli = repo_root / "bin" / "paperfit.js"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "EVALUATING",
                "task": {"type": "full_vto"},
                "runtime_event_summary": {"run_id": "run-agent", "event_count": 8},
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            agent_result = {
                "run_id": "run-agent",
                "runtime_actions": {
                    "repair_plan_executor": {
                        "success": True,
                        "skipped": True,
                        "reason": "dry_run_source_mutation",
                        "risk_level": "high",
                        "requires_approval": True,
                    }
                },
                "artifact_manifest": {"freshness": {"status": "pass", "blocking_checks": []}},
            }
            (root / "data" / "run_result_agent.json").write_text(
                json.dumps(agent_result),
                encoding="utf-8",
            )

            result = subprocess.run(
                ["node", str(cli), "status-view"],
                cwd=root,
                check=True,
                text=True,
                capture_output=True,
            )
            status = json.loads(result.stdout)

            self.assertEqual(status["run_result_path"], "data/run_result_agent.json")
            self.assertEqual(status["runtime"]["run_id"], "run-agent")
            self.assertEqual(status["approval"]["status"], "approval_required")
            self.assertTrue(status["repair"]["requires_approval"])

    def test_top_level_status_view_cli_surfaces_freshness_block(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        cli = repo_root / "bin" / "paperfit.js"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "BLOCKED",
                "last_gatekeeper_decision": "DONE",
                "task": {"type": "full_vto"},
                "runtime_event_summary": {"run_id": "run-agent", "event_count": 9},
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            agent_result = {
                "run_id": "run-agent",
                "status": "blocked",
                "gatekeeper_decision": "DONE",
                "repair_loop_policy": {
                    "schema_version": "1.0",
                    "execution_mode": "report_only",
                    "stop_condition": "artifact_freshness_not_pass",
                    "next_round_allowed": False,
                    "next_round_reason": "artifact_freshness_not_pass",
                    "second_round_apply_readiness": {
                        "schema_version": "1.0",
                        "status": "blocked",
                        "reason": "artifact_freshness_not_pass",
                        "checks": {"artifact_freshness_pass": False},
                    },
                },
                "artifact_manifest": {
                    "freshness": {
                        "status": "stale_or_missing",
                        "blocking_checks": ["pdf_exists"],
                    }
                },
                "failure": {
                    "failure_type": "terminal_success_without_fresh_visual_evidence",
                    "reason": "gatekeeper_done_but_artifact_freshness_failed",
                    "artifact_freshness": {
                        "status": "stale_or_missing",
                        "blocking_checks": ["pdf_exists"],
                    },
                },
            }
            (root / "data" / "run_result_agent.json").write_text(
                json.dumps(agent_result),
                encoding="utf-8",
            )

            result = subprocess.run(
                ["node", str(cli), "status-view"],
                cwd=root,
                check=True,
                text=True,
                capture_output=True,
            )
            status = json.loads(result.stdout)

            policy = status["repair_loop_policy"]
            self.assertEqual(status["run_result_path"], "data/run_result_agent.json")
            self.assertEqual(status["artifact_freshness"]["status"], "stale_or_missing")
            self.assertEqual(status["artifact_freshness"]["blocking_checks"], ["pdf_exists"])
            self.assertEqual(policy["stop_condition"], "artifact_freshness_not_pass")
            self.assertEqual(policy["next_round_reason"], "artifact_freshness_not_pass")
            self.assertFalse(policy["next_round_allowed"])
            self.assertFalse(
                policy["second_round_apply_readiness"]["checks"]["artifact_freshness_pass"]
            )
            self.assertEqual(
                status["terminal_success_guard"]["failure_type"],
                "terminal_success_without_fresh_visual_evidence",
            )

    def test_top_level_status_cli_renders_runtime_status_contract(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        cli = repo_root / "bin" / "paperfit.js"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "EVALUATING",
                "task": {"type": "full_vto"},
                "runtime_event_summary": {"run_id": "run-agent", "event_count": 8},
                "defect_summary": {"initial_total": 4, "resolved": 1, "remaining": 3},
                "artifacts": {
                    "task_spec": "data/task.json",
                    "visual_signal_report": "data/visual_signal_report.json",
                    "defect_report": "data/defect_report.json",
                    "repair_plan": "data/repair_plan.json",
                },
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            agent_result = {
                "run_id": "run-agent",
                "runtime_actions": {
                    "repair_plan_executor": {
                        "success": True,
                        "skipped": True,
                        "reason": "dry_run_source_mutation",
                        "risk_level": "high",
                        "requires_approval": True,
                    }
                },
                "artifact_manifest": {"freshness": {"status": "pass", "blocking_checks": []}},
            }
            (root / "data" / "run_result_agent.json").write_text(
                json.dumps(agent_result),
                encoding="utf-8",
            )

            result = subprocess.run(
                ["node", str(cli), "status"],
                cwd=root,
                check=True,
                text=True,
                capture_output=True,
            )

            self.assertIn("RunResult: data/run_result_agent.json", result.stdout)
            self.assertIn("Artifact Freshness: pass", result.stdout)
            self.assertIn("Requires Approval: true", result.stdout)
            self.assertIn("Status: approval_required", result.stdout)

    def test_top_level_status_cli_renders_freshness_block(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        cli = repo_root / "bin" / "paperfit.js"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "BLOCKED",
                "last_gatekeeper_decision": "DONE",
                "task": {"type": "full_vto"},
                "runtime_event_summary": {"run_id": "run-agent", "event_count": 9},
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            agent_result = {
                "run_id": "run-agent",
                "status": "blocked",
                "gatekeeper_decision": "DONE",
                "repair_loop_policy": {
                    "schema_version": "1.0",
                    "execution_mode": "report_only",
                    "stop_condition": "artifact_freshness_not_pass",
                    "next_round_allowed": False,
                    "next_round_reason": "artifact_freshness_not_pass",
                    "second_round_apply_readiness": {
                        "schema_version": "1.0",
                        "status": "blocked",
                        "reason": "artifact_freshness_not_pass",
                        "checks": {
                            "artifact_freshness_pass": False,
                            "source_mutation_executed": False,
                            "runtime_execution_mode_can_auto_apply": False,
                        },
                    },
                },
                "artifact_manifest": {
                    "freshness": {
                        "status": "stale_or_missing",
                        "blocking_checks": ["pdf_exists"],
                    }
                },
                "failure": {
                    "failure_type": "terminal_success_without_fresh_visual_evidence",
                    "reason": "gatekeeper_done_but_artifact_freshness_failed",
                    "artifact_freshness": {
                        "status": "stale_or_missing",
                        "blocking_checks": ["pdf_exists"],
                    },
                },
            }
            (root / "data" / "run_result_agent.json").write_text(
                json.dumps(agent_result),
                encoding="utf-8",
            )

            result = subprocess.run(
                ["node", str(cli), "status"],
                cwd=root,
                check=True,
                text=True,
                capture_output=True,
            )

            self.assertIn("Status: BLOCKED", result.stdout)
            self.assertIn("Gatekeeper: DONE", result.stdout)
            self.assertIn("Artifact Freshness: stale_or_missing", result.stdout)
            self.assertIn("Stop: artifact_freshness_not_pass", result.stdout)
            self.assertIn("Reason: artifact_freshness_not_pass", result.stdout)
            self.assertIn("Second Round Readiness: blocked", result.stdout)
            self.assertIn("Second Round Reason: artifact_freshness_not_pass", result.stdout)
            self.assertIn(
                "Second Round Failed Checks: artifact_freshness_pass, source_mutation_executed, runtime_execution_mode_can_auto_apply",
                result.stdout,
            )
            self.assertIn(
                "Failure: terminal_success_without_fresh_visual_evidence",
                result.stdout,
            )

    def test_top_level_status_cli_renders_candidate_gate_block(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        cli = repo_root / "bin" / "paperfit.js"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "BLOCKED",
                "last_gatekeeper_decision": "BLOCKED",
                "task": {"type": "full_vto"},
                "runtime_event_summary": {"run_id": "run-agent", "event_count": 6},
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            agent_result = {
                "run_id": "run-agent",
                "status": "blocked",
                "gatekeeper_decision": "BLOCKED",
                "repair_loop_policy": {
                    "schema_version": "1.0",
                    "execution_mode": "report_only",
                    "stop_condition": "approval_scope_blocked",
                    "next_round_allowed": False,
                    "next_round_reason": "approval_scope_blocked",
                    "candidate_approval_scope_gate": {
                        "schema_version": "1.0",
                        "status": "blocked",
                        "reason": "selected_candidate_exceeds_approval_scope",
                        "blocked_candidates": [
                            {
                                "index": 1,
                                "defect_family": "B1",
                                "risk": {
                                    "risk_level": "high",
                                    "operation": "float_movement_across_section_boundary",
                                },
                            }
                        ],
                    },
                    "second_round_apply_readiness": {
                        "schema_version": "1.0",
                        "status": "blocked",
                        "reason": "approval_scope_blocked",
                        "checks": {
                            "candidate_approval_scope_gate_pass": False,
                            "runtime_execution_mode_can_auto_apply": False,
                        },
                    },
                },
                "artifact_manifest": {
                    "freshness": {
                        "status": "pass",
                        "blocking_checks": [],
                    }
                },
            }
            (root / "data" / "run_result_agent.json").write_text(
                json.dumps(agent_result),
                encoding="utf-8",
            )

            result = subprocess.run(
                ["node", str(cli), "status"],
                cwd=root,
                check=True,
                text=True,
                capture_output=True,
            )

            self.assertIn("Stop: approval_scope_blocked", result.stdout)
            self.assertIn("Reason: approval_scope_blocked", result.stdout)
            self.assertIn("Candidate Gate: blocked", result.stdout)
            self.assertIn(
                "Candidate Gate Reason: selected_candidate_exceeds_approval_scope",
                result.stdout,
            )
            self.assertIn("Blocked Candidates: 1", result.stdout)
            self.assertIn("Second Round Readiness: blocked", result.stdout)
            self.assertIn("Second Round Reason: approval_scope_blocked", result.stdout)
            self.assertIn(
                "Second Round Failed Checks: candidate_approval_scope_gate_pass, runtime_execution_mode_can_auto_apply",
                result.stdout,
            )

    def test_top_level_status_cli_renders_carry_forward_block(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        cli = repo_root / "bin" / "paperfit.js"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "EVALUATING",
                "task": {"type": "full_vto"},
                "runtime_event_summary": {"run_id": "run-agent", "event_count": 5},
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            agent_result = {
                "run_id": "run-agent",
                "status": "continue",
                "gatekeeper_decision": "CONTINUE",
                "repair_loop_policy": {
                    "schema_version": "1.0",
                    "execution_mode": "report_only",
                    "stop_condition": "continue",
                    "next_round_allowed": False,
                    "next_round_reason": "multi_round_apply_not_enabled_in_current_runtime",
                    "approval_scope_carry_forward": {
                        "schema_version": "1.0",
                        "status": "blocked",
                        "reason": "approval_scope_contract_mismatch",
                        "checks": {
                            "approval_scope_matches": False,
                            "mutation_surface_within_scope": False,
                            "high_risk_operations_declared": True,
                            "fresh_approval_required_for_high_risk_operations": True,
                        },
                    },
                },
                "artifact_manifest": {
                    "freshness": {
                        "status": "pass",
                        "blocking_checks": [],
                    }
                },
            }
            (root / "data" / "run_result_agent.json").write_text(
                json.dumps(agent_result),
                encoding="utf-8",
            )

            result = subprocess.run(
                ["node", str(cli), "status"],
                cwd=root,
                check=True,
                text=True,
                capture_output=True,
            )

            self.assertIn("Approval Carry-forward: blocked", result.stdout)
            self.assertIn(
                "Approval Carry-forward Reason: approval_scope_contract_mismatch",
                result.stdout,
            )
            self.assertIn(
                "Approval Carry-forward Failed Checks: approval_scope_matches, mutation_surface_within_scope",
                result.stdout,
            )

    def test_status_view_prefers_nondry_result_and_reports_mutation_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            state = {
                "main_tex": "main.tex",
                "status": "EVALUATING",
                "task": {"type": "full_vto"},
                "runtime_event_summary": {"run_id": "run-nondry", "event_count": 12},
                "artifacts": {
                    "repair_execution_report": "data/repair_execution_report.json",
                    "source_mutation_report": "data/source_mutation_report.json",
                },
                "repair_execution_summary": {"status": None, "applied_count": 0},
            }
            (root / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            check_visual_result = {
                "run_id": "run-check",
                "artifact_manifest": {"freshness": {"status": "unknown"}},
            }
            nondry_result = {
                "run_id": "run-nondry",
                "runtime_actions": {
                    "repair_plan_executor": {
                        "status": "success",
                        "applied_count": 4,
                    }
                },
                "artifact_manifest": {"freshness": {"status": "pass", "blocking_checks": []}},
            }
            (root / "data" / "run_result_check_visual.json").write_text(
                json.dumps(check_visual_result),
                encoding="utf-8",
            )
            (root / "data" / "run_result_full_vto_nondry.json").write_text(
                json.dumps(nondry_result),
                encoding="utf-8",
            )

            status = build_runtime_status(project_root=root)

            self.assertEqual(status["run_result_path"], "data/run_result_full_vto_nondry.json")
            self.assertEqual(status["repair"]["execution_status"], "success")
            self.assertEqual(status["repair"]["applied_count"], 4)
            self.assertEqual(status["artifacts"]["source_mutation_report"], "data/source_mutation_report.json")
            self.assertEqual(status["approval"]["status"], "approved_and_executed")
            self.assertTrue(status["approval"]["approval_granted"])
            self.assertEqual(status["approval"]["execution"]["applied_count"], 4)


if __name__ == "__main__":
    unittest.main()
