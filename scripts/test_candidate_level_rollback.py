"""验收：候选级事务回滚（轮级失败归因）。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import orchestrator_runtime  # noqa: E402
from pdf_test_support import write_test_pdf  # noqa: E402
from orchestrator_runtime import OrchestratorRuntime  # noqa: E402
from phased_repair import (  # noqa: E402
    attribute_and_rollback_round_failure,
    collect_unit_rollback_edits,
    diff_line_hunks,
    revert_line_hunks,
)
from runtime_types import TaskSpec  # noqa: E402

MAIN_TEX = (
    "\\documentclass{article}\n"
    "\\begin{document}\n"
    "\\includegraphics{a.pdf}\n"
    "\\caption{x}\\label{fig:a}\n"
    "long token overfull line\n"
    "\\cite{ref1}\n"
    "\\end{document}\n"
)

D1_MARKER = "% APPLIED:repair_overfull_boxes:D1"
B2_WIDE_MARKER = "% APPLIED:adjust_float_width:fig:wide"
B2_EARLY_MARKER = "% APPLIED:adjust_float_width:fig:early"

REPAIR_PLAN = {
    "schema_version": "1.0",
    "summary": {"total_candidates": 3},
    "candidates": [
        {
            "candidate_type": "log_warning",
            "defect_family": "D1",
            "proposed_action": "repair_overfull_boxes",
            "priority_score": 90,
            "line_number": 5,
            "overflow_amount": 30.0,
            "page": 1,
            "target": {"scope": "overflow"},
            "source_anchor": {"file": "main.tex", "start_line": 5, "end_line": None},
        },
        {
            "candidate_type": "object",
            "defect_family": "B2",
            "proposed_action": "adjust_float_width",
            "priority_score": 192,
            "page": 2,
            "target": {"label": "fig:early", "float_type": "figure"},
            "visual_width_subtype": "overflow_width",
        },
        {
            "candidate_type": "object",
            "defect_family": "B2",
            "proposed_action": "adjust_float_width",
            "priority_score": 192,
            "page": 7,
            "target": {"label": "fig:wide", "float_type": "figure"},
            "visual_width_subtype": "overflow_width",
        },
    ],
}

ENDMATTER_INTRUSION = {
    "available": True,
    "endmatter_start_page": 8,
    "endmatter_heading": "Acknowledgements",
    "intrusions": [{"page": 8, "captions": ["Figure 7:"]}],
    "hard_failures": [
        "Body float caption detected on endmatter pages starting at page 8 (Acknowledgements): page 8: Figure 7:"
    ],
}


def _write_phased_outcome(
    outcomes: list,
    *,
    candidate_id: str,
    action: str,
    action_class: str,
    unit_index: int,
    page: int,
    marker_line: str,
    root: Path,
    main_tex: str,
    snapshot_root: Path,
) -> None:
    """真实建快照 → 真实改源码 → 记录该单元源码编辑范围（与 phased_repair 同机制）。"""
    from runtime_snapshots import create_pre_repair_snapshot

    snapshot = create_pre_repair_snapshot(
        project_root=root,
        main_tex=main_tex,
        snapshot_root=str(snapshot_root),
        snapshot_id=f"unit_{unit_index}",
    )
    tex_path = root / main_tex
    with tex_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(marker_line + "\n")
    edits = collect_unit_rollback_edits(root, str(snapshot["rollback_target"]))
    outcomes.append(
        {
            "candidate_id": candidate_id,
            "action": action,
            "action_class": action_class,
            "applied": True,
            "accepted": True,
            "rejected": False,
            "rolled_back": False,
            "reason": "all gates passed",
            "source_anchor": {"file": main_tex, "start_line": None, "end_line": None},
            "unit_index": unit_index,
            "page": page,
            "rollback_edits": edits,
        }
    )


class _CandidateRollbackHarness:
    """full_vto run 骨架：真实快照 + 真实源码标记，编译/渲染/硬门禁可注入行为。"""

    def __init__(self, root: Path, *, executor_mode: str = "phased_units") -> None:
        self.root = root
        self.main_tex_name = "main.tex"
        (root / "main.tex").write_text(MAIN_TEX, encoding="utf-8")
        write_test_pdf(root / "main.pdf")
        self.core_calls = {"count": 0}
        self.compile_calls = {"count": 0}
        self.mode = executor_mode
        self.overfull_by_wide_marker = False  # 验收路径：fig:wide 存在 → overfull 增加
        self.last_outcomes: list = []
        self.runtime: OrchestratorRuntime | None = None

    # ---- fake phased executor：按单元建快照 + 改源码 + 报 candidate_outcomes ----
    def fake_execute(self, *args: object, **_: object) -> dict:
        runtime_self = self.runtime
        if self.mode == "legacy_no_outcomes":
            (self.root / self.main_tex_name).write_text(
                MAIN_TEX + D1_MARKER + "\n" + B2_WIDE_MARKER + "\n", encoding="utf-8"
            )
            runtime_self.manager.update(
                {
                    "repair_execution_summary": {
                        "schema_version": "1.0",
                        "status": "success",
                        "applied_count": 3,
                        "selected_candidates": [],
                        "updated_at": None,
                    }
                }
            )
            return runtime_self.manager.load()

        outcomes: list = []
        snapshot_root = self.root / "data" / "unit_snapshots"
        _write_phased_outcome(
            outcomes,
            candidate_id="D1",
            action="repair_overfull_boxes",
            action_class="local_safe",
            unit_index=0,
            page=1,
            marker_line=D1_MARKER,
            root=self.root,
            main_tex=self.main_tex_name,
            snapshot_root=snapshot_root,
        )
        _write_phased_outcome(
            outcomes,
            candidate_id="fig:early",
            action="adjust_float_width",
            action_class="layout_sensitive",
            unit_index=1,
            page=2,
            marker_line=B2_EARLY_MARKER,
            root=self.root,
            main_tex=self.main_tex_name,
            snapshot_root=snapshot_root,
        )
        _write_phased_outcome(
            outcomes,
            candidate_id="fig:wide",
            action="adjust_float_width",
            action_class="layout_sensitive",
            unit_index=2,
            page=7,
            marker_line=B2_WIDE_MARKER,
            root=self.root,
            main_tex=self.main_tex_name,
            snapshot_root=snapshot_root,
        )
        runtime_self.manager.update(
            {
                "repair_execution_summary": {
                    "schema_version": "1.1",
                    "status": "success",
                    "applied_count": 3,
                    "accepted_count": 3,
                    "rejected_count": 0,
                    "rolled_back_count": 0,
                    "candidate_outcomes": outcomes,
                    "selected_candidates": [],
                    "updated_at": None,
                }
            }
        )
        self.last_outcomes = outcomes
        return runtime_self.manager.load()

    # ---- fake round core：写诊断工件 + 修复计划 + 决策 ----
    def fake_core(
        self,
        *,
        emit_event: object | None = None,
        runtime_actions: dict | None = None,
        **_: object,
    ) -> dict:
        runtime_self = self.runtime
        self.core_calls["count"] += 1
        page_dir = self.root / "data" / "pages"
        page_dir.mkdir(parents=True, exist_ok=True)
        page = page_dir / "page_001.png"
        page.write_bytes(b"png")
        visual_report = self.root / "data" / "visual_signal_report.json"
        defect_report = self.root / "data" / "defect_report.json"
        gatekeeper_report = self.root / "data" / "gatekeeper_decision.json"
        repair_plan = self.root / "data" / "repair_plan.json"
        visual_report.write_text(
            json.dumps({"summary": {"pages_analyzed": 8}, "findings": []}), encoding="utf-8"
        )
        defect_report.write_text("{}", encoding="utf-8")
        gatekeeper_report.write_text(json.dumps({"decision": "CONTINUE"}), encoding="utf-8")
        repair_plan.write_text(json.dumps(REPAIR_PLAN), encoding="utf-8")
        tex_text = (self.root / self.main_tex_name).read_text(encoding="utf-8", errors="replace")
        overfull_total = 2
        if self.overfull_by_wide_marker and B2_WIDE_MARKER in tex_text:
            overfull_total = 4  # fig:wide 放宽后在轮级证据里 overfull 变多
        base_mtime = 1_800_050_000 + self.core_calls["count"] * 100
        for offset, path in enumerate([page, visual_report, defect_report, gatekeeper_report]):
            os.utime(path, (base_mtime + offset, base_mtime + offset))
        runtime_self.manager.update(
            {
                "compile_success": True,
                "page_images_rendered": True,
                "last_gatekeeper_decision": "CONTINUE",
                "defect_summary": {"initial_total": 3, "resolved": 1, "remaining": 2},
                "repair_plan_summary": {
                    "schema_version": "1.0",
                    "total_candidates": 3,
                    "top_candidates": [],
                    "updated_at": None,
                },
                "artifacts": {
                    "page_images_dir": "data/pages",
                    "visual_signal_report": "data/visual_signal_report.json",
                    "defect_report": "data/defect_report.json",
                    "repair_plan": "data/repair_plan.json",
                    "gatekeeper_decision": "data/gatekeeper_decision.json",
                },
            }
        )
        runtime_self._record_runtime_action(
            "gatekeeper_enforcer",
            {
                "success": True,
                "source": "generated",
                "output_path": "data/gatekeeper_decision.json",
                "decision": "CONTINUE",
            },
            phase="verify",
            state="VERIFYING",
            emit_event=emit_event if callable(emit_event) else None,
            runtime_actions=runtime_actions,
        )
        # rule_report 与真实 _run_round_core 一样：附在返回的 state 上（不进 state schema）
        state = runtime_self.manager.load()
        state["rule_report"] = {
            "summary": {
                "overfull_hbox_total": overfull_total,
                "total_overflow_pt": float(overfull_total) * 10.0,
            }
        }
        return state

    def fake_compile(self, project_root: Path, main_tex=None, tolerant=True):
        self.compile_calls["count"] += 1
        return {
            "success": True,
            "pdf_path": str(self.root / "main.pdf"),
            "timeout": False,
            "log_file": "main.log",
        }

    def hard_guard_by_tex(self, pdf_path) -> dict:
        tex_text = (self.root / self.main_tex_name).read_text(encoding="utf-8", errors="replace")
        if B2_WIDE_MARKER in tex_text:
            return dict(ENDMATTER_INTRUSION)
        return {"available": True, "hard_failures": []}

    def run_task(self, *, hard_guard_fn=None, overfull_by_wide_marker: bool = False) -> dict:
        self.overfull_by_wide_marker = overfull_by_wide_marker
        guard_fn = hard_guard_fn or self.hard_guard_by_tex
        self.runtime = OrchestratorRuntime(state_path=str(self.root / "data" / "state.json"))
        spec = TaskSpec.from_dict(
            {
                "task_type": "full_vto",
                "project_root": str(self.root),
                "main_tex": self.main_tex_name,
                "allow_source_mutation": True,
                "pre_repair_snapshot_required": True,
                "rollback_policy": "required",
                "max_rounds": 1,
            }
        )
        with (
            mock.patch.object(orchestrator_runtime, "compile_latex", side_effect=self.fake_compile),
            mock.patch.object(
                orchestrator_runtime,
                "render_pdf_pages",
                return_value={"success": True, "page_dir": "data/pages"},
            ),
            mock.patch.object(orchestrator_runtime, "inspect_endmatter_float_intrusion", side_effect=guard_fn),
            mock.patch.object(OrchestratorRuntime, "_run_round_core", self.fake_core),
            mock.patch.object(OrchestratorRuntime, "execute_repair_plan_phased", self.fake_execute),
        ):
            return self.runtime.run_task(
                task_spec=spec,
                output_path="data/run_result_candidate_rollback.json",
            )

    def tex(self) -> str:
        return (self.root / self.main_tex_name).read_text(encoding="utf-8", errors="replace")


class HardGuardAttributionTest(unittest.TestCase):
    def test_single_b2_rolled_back_d1_retained(self) -> None:
        """AAAI 形态：D1 accepted + B2(fig:wide) 触发 endmatter 侵入 → 只回滚该 B2。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            harness = _CandidateRollbackHarness(root)
            result = harness.run_task()

            tex = harness.tex()
            # D1 真实收益保留（源码 diff 可见）；无关且已过局部门禁的 fig:early 保留
            self.assertIn(D1_MARKER, tex)
            self.assertIn(B2_EARLY_MARKER, tex)
            # 致害 B2 被单独回滚
            self.assertNotIn(B2_WIDE_MARKER, tex)

            records = result["round_execution_records"]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["rollback_scope"], "candidate")
            self.assertEqual(records[0]["attributed_candidate"], "fig:wide")
            self.assertEqual(records[0]["rolled_back_candidate_ids"], ["fig:wide"])
            # 轮记录计数随候选级回滚收敛（accepted 3→2，rolled_back 0→1）
            self.assertEqual(records[0]["accepted"], 2)
            self.assertEqual(records[0]["rolled_back"], 1)

            action = result["runtime_actions"]["candidate_level_rollback"]
            self.assertTrue(action["success"])
            self.assertEqual(action["attributed_candidate"], "fig:wide")
            self.assertEqual(action["attempts_used"], 1)
            self.assertEqual(action["verify_kind"], "hard_guard")

            # 硬门禁已在候选级回滚后清零：不再是整轮 guard_rolled_back / BLOCKED
            self.assertEqual(result["status"], "continue")
            self.assertNotIn("rollback_to_snapshot", result["runtime_actions"])
            state = json.loads((root / "data" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["content_integrity"]["validation_status"], "candidate_level_rollback")
            summary = state["repair_execution_summary"]
            self.assertEqual(summary["accepted_count"], 2)
            self.assertEqual(summary["rolled_back_count"], 1)
            rolled = {oc["candidate_id"]: oc for oc in summary["candidate_outcomes"]}
            self.assertTrue(rolled["D1"]["accepted"])
            self.assertTrue(rolled["fig:early"]["accepted"])
            self.assertTrue(rolled["fig:wide"]["rolled_back"])
            # 非阻断学习证据保留：下一 run 可对致害 B2 走 FloatBarrier 保护路径
            self.assertEqual(
                state["post_repair_hard_guard"]["status"], "attributed_candidate_rolled_back"
            )
            self.assertTrue(state["post_repair_hard_guard"]["visual_hard_guards"]["hard_failures"])

    def test_all_candidates_harmful_full_round_rollback(self) -> None:
        """全候选都致害：逆序逐个回滚重验证耗尽后才整轮回滚（candidates_exhausted）。"""

        def always_failing_guard(pdf_path) -> dict:
            return dict(ENDMATTER_INTRUSION)

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            harness = _CandidateRollbackHarness(root)
            result = harness.run_task(hard_guard_fn=always_failing_guard)

            tex = harness.tex()
            self.assertNotIn(D1_MARKER, tex)
            self.assertNotIn(B2_WIDE_MARKER, tex)
            self.assertNotIn(B2_EARLY_MARKER, tex)
            self.assertEqual(tex, MAIN_TEX, "整轮回滚必须恢复轮前快照源码")

            records = result["round_execution_records"]
            self.assertEqual(records[0]["rollback_scope"], "candidates_exhausted")
            self.assertIsNone(records[0]["attributed_candidate"])

            action = result["runtime_actions"]["candidate_level_rollback"]
            self.assertFalse(action["success"])
            self.assertEqual(action["status"], "candidates_exhausted")
            # 有界：尝试次数 = 接受单元数（3），每个候选至多试一次
            self.assertEqual(action["attempts_used"], 3)
            self.assertEqual(len(action["attempts"]), 3)
            self.assertEqual(action["attempts"][-1]["rolled_back_units"], [2, 1, 0])

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["failure"]["failure_type"], "post_repair_hard_guard_failed")
            self.assertTrue(result["runtime_actions"]["rollback_to_snapshot"]["success"])
            state = json.loads((root / "data" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["content_integrity"]["validation_status"], "rolled_back")
            self.assertTrue((root / "data" / "rollback_report_post_repair_hard_guard.json").is_file())

    def test_legacy_executor_without_outcomes_keeps_full_round_rollback(self) -> None:
        """无候选编辑范围（旧执行器/回退路径）：保持既有整轮回滚，不静默放行。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            harness = _CandidateRollbackHarness(root, executor_mode="legacy_no_outcomes")
            result = harness.run_task()

            self.assertNotIn(B2_WIDE_MARKER, harness.tex())
            records = result["round_execution_records"]
            self.assertEqual(records[0]["rollback_scope"], "full_round")
            self.assertIsNone(records[0]["attributed_candidate"])
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["failure"]["failure_type"], "post_repair_hard_guard_failed")


class RoundAcceptanceAttributionTest(unittest.TestCase):
    def test_acceptance_failure_attributed_to_b2_d1_retained(self) -> None:
        """轮级验收失败（overfull 增加）也先候选归因：回滚致害 B2 后复验通过。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            harness = _CandidateRollbackHarness(root)
            # 硬门禁保持清零：让失败只来自轮级验收（overfull 增加）
            result = harness.run_task(
                hard_guard_fn=lambda pdf_path: {"available": True, "hard_failures": []},
                overfull_by_wide_marker=True,
            )

            tex = harness.tex()
            self.assertIn(D1_MARKER, tex, "D1 收益必须保留")
            self.assertIn(B2_EARLY_MARKER, tex)
            self.assertNotIn(B2_WIDE_MARKER, tex)

            records = result["round_execution_records"]
            self.assertEqual(records[0]["rollback_scope"], "candidate")
            self.assertEqual(records[0]["attributed_candidate"], "fig:wide")

            action = result["runtime_actions"]["candidate_level_rollback"]
            self.assertTrue(action["success"])
            self.assertEqual(action["verify_kind"], "round_acceptance")
            # 归因复验重跑了轮核心诊断（初始 + 修复后 + 复验）
            self.assertEqual(harness.core_calls["count"], 3)

            state = json.loads((root / "data" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["content_integrity"]["validation_status"], "candidate_level_rollback")
            self.assertEqual(result["status"], "continue")


class HunkMechanicsTest(unittest.TestCase):
    """编辑范围记录与手术式撤销的单元机制。"""

    def test_diff_and_revert_roundtrip(self) -> None:
        before = "a\nb\nc\n"
        after = "a\nB2\nc\nd\n"
        hunks = diff_line_hunks(before, after)
        self.assertEqual(revert_line_hunks(after, hunks), before)

    def test_revert_rejects_drifted_or_ambiguous_hunks(self) -> None:
        hunks = [{"before": "x\n", "after": "dup\n", "after_start_line": 1}]
        # 预期行已漂移，且 after 片段在当前文本中出现两次 → 拒绝（fail-closed）
        self.assertIsNone(revert_line_hunks("z\ndup\ndup\n", hunks))

    def test_multi_unit_revert_keeps_unrelated_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "data").mkdir()
            harness = _CandidateRollbackHarness(root)
            runtime = OrchestratorRuntime(state_path=str(root / "data" / "state.json"))
            runtime.init_task(main_tex="main.tex")
            harness.runtime = runtime
            harness.fake_execute()
            tex_path = root / "main.tex"
            with tex_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write("% unrelated later edit\n")

            # 直接用 phased_repair 的归因驱动：回滚 fig:wide 单元，其余保留
            summary_outcomes = harness.last_outcomes
            self.assertEqual(len(summary_outcomes), 3)
            self.assertTrue(all(oc["rollback_edits"] for oc in summary_outcomes))
            report = attribute_and_rollback_round_failure(
                project_root=root,
                main_tex="main.tex",
                candidate_outcomes=summary_outcomes,
                failure_evidence=ENDMATTER_INTRUSION,
                verify_fn=lambda rolled: {
                    "cleared": B2_WIDE_MARKER not in tex_path.read_text(encoding="utf-8", errors="replace")
                },
            )
            self.assertEqual(report["status"], "candidate")
            self.assertEqual(report["attributed_candidate"], "fig:wide")
            tex = tex_path.read_text(encoding="utf-8", errors="replace")
            self.assertIn(D1_MARKER, tex)
            self.assertIn(B2_EARLY_MARKER, tex)
            self.assertNotIn(B2_WIDE_MARKER, tex)
            self.assertIn("% unrelated later edit", tex)


if __name__ == "__main__":
    unittest.main()
