#!/usr/bin/env python3
"""
Minimal executable runtime helpers for orchestrator state transitions.

This does not replace the full Claude-driven workflow yet. It enforces the
state mutations that must stay consistent regardless of prompt text.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from state_manager import StateManager


class OrchestratorRuntime:
    def __init__(self, state_path: str = StateManager.DEFAULT_STATE_PATH):
        self.manager = StateManager(state_path=state_path)

    def _python_executable(self) -> str:
        return sys.executable or "python3"

    @staticmethod
    def infer_task_from_request(request: str) -> Dict[str, Any]:
        text = str(request or "").strip()
        lowered = text.lower()
        normalized = re.sub(r"\s+", " ", lowered)

        def _has_any(*needles: str) -> bool:
            return any(needle in normalized for needle in needles if needle)

        def _extract_target_pages() -> Optional[int]:
            patterns = [
                r"(?:压到|缩到|减到|控制到|调整到|正文压到)\s*(\d+)\s*页",
                r"(?:to|into|down to|fit in)\s*(\d+)\s*pages?",
                r"(?:=|target-pages?\s+)(\d+)",
            ]
            for pattern in patterns:
                match = re.search(pattern, normalized, re.IGNORECASE)
                if match:
                    return int(match.group(1))
            return None

        def _extract_table_target() -> Optional[str]:
            label_match = re.search(r"\b(tab:[\w:-]+)\b", text, re.IGNORECASE)
            if label_match:
                return label_match.group(1)
            number_match = re.search(r"\btable\s*(\d+)\b", normalized, re.IGNORECASE)
            if number_match:
                return f"Table {number_match.group(1)}"
            cn_match = re.search(r"表\s*(\d+)", text)
            if cn_match:
                return f"Table {cn_match.group(1)}"
            return None

        def _extract_template_name() -> Optional[str]:
            known_templates = [
                "cvpr", "iclr", "eccv", "neurips", "aaai", "icml", "ieee", "acm", "arxiv",
            ]
            for template in known_templates:
                match = re.search(rf"\b{template}[0-9]{{0,4}}\b", normalized, re.IGNORECASE)
                if match:
                    return match.group(0).upper()
            cn_match = re.search(r"迁移到\s*([A-Za-z]+[0-9]{0,4})", text)
            if cn_match:
                return cn_match.group(1)
            return None

        if _has_any("/paperfit-undo", "undo last change", "回滚最近", "撤销最近", "undo paperfit"):
            return {"task_type": "undo_last_change"}

        if _has_any("/show-status", "show status", "status", "当前状态", "查看状态", "最近结果", "进度"):
            return {"task_type": "status_query"}

        if _has_any("/repair-table", "repair table", "fix table", "修表", "修一下表格", "table ") and _extract_table_target():
            result: Dict[str, Any] = {"task_type": "repair_table"}
            target = _extract_table_target()
            if target:
                result["object_target"] = target
            return result

        if _has_any("/migrate-template", "migrate to", "migrate this paper to", "迁移到", "切到模板", "模板迁移"):
            result = {"task_type": "template_migration"}
            template_name = _extract_template_name()
            if template_name:
                result["template"] = template_name
            return result

        if _has_any("/adjust-length", "压到", "缩到", "减到", "页数", "target pages", "fit in", "reduce to", "shorten to") and _extract_target_pages() is not None:
            return {
                "task_type": "adjust_length",
                "target_pages": _extract_target_pages(),
            }

        if _has_any("/check-visual", "check visual", "visual only", "只做视觉", "只做诊断", "不要改代码", "不要改tex", "排版分析", "视觉诊断", "layout analysis", "analyze layout"):
            return {"task_type": "visual_only"}

        if _has_any(
            "/fix-layout",
            "修复排版",
            "修复当前论文",
            "修复当前项目",
            "完整修复",
            "repair layout",
            "fix layout",
            "排到投稿",
            "layout repair",
            "完整闭环",
        ):
            result = {"task_type": "full_vto"}
            target_pages = _extract_target_pages()
            if target_pages is not None:
                result["target_pages"] = target_pages
            return result

        result = {"task_type": "analyze_layout"}
        target_pages = _extract_target_pages()
        if target_pages is not None:
            result["target_pages"] = target_pages
        return result

    def init_task(
        self,
        main_tex: str,
        task_type: str = "full_vto",
        target_pages: Optional[int] = None,
        template: Optional[str] = None,
        strict_mode: bool = False,
        max_rounds: int = 10,
        column_type: Optional[str] = None,
        page_budget_scope: Optional[str] = None,
    ) -> Dict[str, Any]:
        state = self.manager.init_state(
            main_tex=main_tex,
            task_type=task_type,
            target_pages=target_pages,
            template=template,
            strict_mode=strict_mode,
            max_rounds=max_rounds,
            column_type=column_type,
            page_budget_scope=page_budget_scope,
        )
        self.manager.update({"status": "INITIALIZED", "agents_this_round": ["orchestrator-agent"]})
        return self.manager.load()

    def start_round(self) -> Dict[str, Any]:
        state = self.manager.next_round()
        self.manager.update({"agents_this_round": ["orchestrator-agent"]})
        return self.manager.load()

    def mark_compile(self, success: bool, report_path: Optional[str] = None) -> Dict[str, Any]:
        self.manager.load()
        patch: Dict[str, Any] = {
            "compile_success": success,
            "status": "EVALUATING" if success else "BLOCKED",
        }
        if report_path:
            patch["artifacts"] = {"rule_report": report_path}
        self.manager.update(patch)
        return self.manager.load()

    def mark_page_images_rendered(self, rendered: bool, page_dir: Optional[str] = None) -> Dict[str, Any]:
        self.manager.load()
        patch: Dict[str, Any] = {"page_images_rendered": rendered}
        if page_dir:
            patch["artifacts"] = {"page_images_dir": page_dir}
        self.manager.update(patch)
        return self.manager.load()

    def apply_gatekeeper_decision(self, decision: str, report_path: Optional[str] = None) -> Dict[str, Any]:
        self.manager.load()
        normalized = decision.upper()
        patch: Dict[str, Any] = {
            "last_gatekeeper_decision": normalized,
            "status": "DONE" if normalized == "DONE" else ("BLOCKED" if normalized == "BLOCKED" else "EVALUATING"),
        }
        if report_path:
            patch["artifacts"] = {"gatekeeper_decision": report_path}
        self.manager.update(patch)
        return self.manager.load()

    def ingest_column_void_report(self, report_path: str) -> Dict[str, Any]:
        self.manager.load()
        self.manager.ingest_column_void_report(report_path)
        return self.manager.load()

    def ingest_semantic_report(self, report_path: str) -> Dict[str, Any]:
        self.manager.load()
        self.manager.ingest_semantic_report(report_path)
        return self.manager.load()

    def ingest_defect_report(self, report_path: str) -> Dict[str, Any]:
        self.manager.load()
        self.manager.ingest_defect_report(report_path)
        return self.manager.load()

    def set_defect_summary(
        self, resolved: int, remaining: int, initial: Optional[int] = None
    ) -> Dict[str, Any]:
        self.manager.load()
        self.manager.update_defect_summary(resolved=resolved, remaining=remaining, initial=initial)
        return self.manager.load()

    def set_next_actions(self, actions: list[str]) -> Dict[str, Any]:
        self.manager.load()
        self.manager.update({"next_actions": actions})
        return self.manager.load()

    def set_agents_this_round(self, agents: list[str]) -> Dict[str, Any]:
        self.manager.load()
        self.manager.update({"agents_this_round": agents})
        return self.manager.load()

    def set_artifact(self, key: str, value: str) -> Dict[str, Any]:
        self.manager.load()
        artifacts = dict(self.manager.get("artifacts") or {})
        artifacts[key] = value
        self.manager.update({"artifacts": artifacts})
        return self.manager.load()

    def execute_repair_plan(
        self,
        main_tex: str,
        repair_plan_path: Optional[str] = None,
        output_path: Optional[str] = None,
        column_type: Optional[str] = None,
        max_candidates: int = 3,
    ) -> Dict[str, Any]:
        self.manager.load()
        repair_plan_path = repair_plan_path or (((self.manager.get("artifacts") or {}).get("repair_plan")) or "data/repair_plan.json")
        output_path = output_path or "data/repair_execution_report.json"
        task_config = self.manager.get("task") or {}
        report = self._run_repair_plan_executor(
            repair_plan_path=repair_plan_path,
            main_tex=main_tex,
            output_path=output_path,
            column_type=column_type or task_config.get("column_type"),
            target_pages=task_config.get("target_pages"),
            max_candidates=max_candidates,
        )
        if report:
            self.manager.ingest_repair_execution_report(output_path)
        return self.manager.load()

    def add_history_entry(
        self,
        decision: Optional[str] = None,
        defects_found: Optional[int] = None,
        defects_resolved: Optional[int] = None,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.manager.load()
        entry: Dict[str, Any] = {"round": self.manager.get("current_round")}
        if decision is not None:
            entry["decision"] = decision
        if defects_found is not None:
            entry["defects_found"] = defects_found
        if defects_resolved is not None:
            entry["defects_resolved"] = defects_resolved
        if note:
            entry["note"] = note
        self.manager.add_history_entry(entry)
        return self.manager.load()

    def archive_task(self) -> Dict[str, Any]:
        self.manager.load()
        archive_path = self.manager.archive()
        self.manager.update({"status": "ARCHIVED"})
        state = self.manager.load()
        state["archive_path"] = archive_path
        return state

    def inspect_project(
        self,
        main_tex: str,
        log_file: Optional[str] = None,
        page_dir: Optional[str] = None,
        template: Optional[str] = None,
        target_pages: Optional[int] = None,
        crossrefs_output: Optional[str] = None,
        rule_report_output: Optional[str] = None,
        visual_signal_output: Optional[str] = None,
        defect_report_output: Optional[str] = None,
        initialize_task: bool = True,
        record_history: bool = True,
    ) -> Dict[str, Any]:
        if initialize_task:
            self.init_task(
                main_tex=main_tex,
                template=template,
                target_pages=target_pages,
            )
        else:
            self.manager.load()

        cwd = Path.cwd()
        main_tex_path = cwd / main_tex
        if log_file is None:
            log_file = str(main_tex_path.with_suffix(".log").name)
        if page_dir is None:
            page_dir = "page_images" if (cwd / "page_images").is_dir() else "data/pages"
        if crossrefs_output is None:
            crossrefs_output = "data/crossrefs.json"
        if rule_report_output is None:
            rule_report_output = "data/rule_report.json"
        if visual_signal_output is None:
            visual_signal_output = "data/visual_signal_report.json"
        if defect_report_output is None:
            defect_report_output = "data/defect_report.json"
        hygiene_report_output = "data/source_hygiene_report.json"
        repair_plan_output = "data/repair_plan.json"

        rule_report = self._parse_log_to_report(log_file=log_file, output_path=rule_report_output)
        self.mark_compile(success=bool(rule_report.get("compile_success")), report_path=rule_report_output)

        page_dir_path = cwd / page_dir
        rendered = page_dir_path.is_dir() and any(page_dir_path.glob("page_*.png"))
        self.mark_page_images_rendered(rendered=rendered, page_dir=page_dir)
        self.manager.update(
            {
                "artifacts": {
                    "visual_signal_report": None,
                    "repair_plan": None,
                    "defect_report": None,
                }
            }
        )

        crossrefs_report = self._extract_crossrefs(main_tex=main_tex, output_path=crossrefs_output)
        self.set_artifact("crossrefs_report", crossrefs_output)

        hygiene_report = self._run_source_hygiene_check(
            main_tex=main_tex,
            output_path=hygiene_report_output,
        )
        self.set_artifact("source_hygiene_report", hygiene_report_output)

        if rendered:
            visual_report = self._run_visual_signal_aggregator(
                pages_dir=page_dir,
                output_path=visual_signal_output,
                rule_report_output=rule_report_output,
                crossrefs_output=crossrefs_output,
            )
            if visual_report:
                self.manager.ingest_visual_signal_report(visual_signal_output)
                repair_plan = self._run_repair_plan_generator(
                    visual_signal_report=visual_signal_output,
                    output_path=repair_plan_output,
                    crossrefs_output=crossrefs_output,
                    rule_report_output=rule_report_output,
                    target_pages=target_pages,
                )
                if repair_plan:
                    self.manager.ingest_repair_plan(repair_plan_output)

        defect_report = self._run_defect_report_builder(
            rule_report_output=rule_report_output,
            visual_signal_output=visual_signal_output,
            hygiene_report_output=hygiene_report_output,
            output_path=defect_report_output,
        )
        if defect_report:
            self.manager.ingest_defect_report(defect_report_output)

        agents = ["orchestrator-agent", "rule-engine-agent"]
        if rendered:
            agents.append("layout-detective-agent")
        self.set_agents_this_round(agents)
        defect_summary = (self.manager.load().get("defect_summary") or {})
        if record_history:
            self.add_history_entry(
                decision="CONTINUE" if rule_report.get("compile_success") else "BLOCKED",
                defects_found=int(defect_summary.get("remaining") or 0),
                defects_resolved=int(defect_summary.get("resolved") or 0),
                note=f"inspect-project:{Path(main_tex).name}",
            )

        state = self.manager.load()
        state["rule_report"] = rule_report
        state["crossrefs_report"] = crossrefs_report
        state["source_hygiene_report"] = hygiene_report
        return state

    def run_round(
        self,
        main_tex: str,
        log_file: Optional[str] = None,
        page_dir: Optional[str] = None,
        template: Optional[str] = None,
        target_pages: Optional[int] = None,
        crossrefs_output: Optional[str] = None,
        rule_report_output: Optional[str] = None,
        column_void_report: Optional[str] = None,
        semantic_report: Optional[str] = None,
        gatekeeper_report: Optional[str] = None,
        gatekeeper_output: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._ensure_initialized_state(main_tex=main_tex, template=template, target_pages=target_pages)
        self.start_round()

        state = self.inspect_project(
            main_tex=main_tex,
            log_file=log_file,
            page_dir=page_dir,
            template=template,
            target_pages=target_pages,
            crossrefs_output=crossrefs_output,
            rule_report_output=rule_report_output,
            visual_signal_output=None,
            initialize_task=False,
            record_history=False,
        )

        cwd = Path.cwd()
        if column_void_report:
            cv_path = Path(column_void_report)
            if not cv_path.is_absolute():
                cv_path = cwd / cv_path
            if cv_path.is_file():
                state = self.ingest_column_void_report(str(cv_path))
        if semantic_report:
            sem_path = Path(semantic_report)
            if not sem_path.is_absolute():
                sem_path = cwd / sem_path
            if sem_path.is_file():
                state = self.ingest_semantic_report(str(sem_path))
        if gatekeeper_report:
            gate_path = Path(gatekeeper_report)
            if not gate_path.is_absolute():
                gate_path = cwd / gate_path
            if gate_path.is_file():
                state = self.manager.ingest_gatekeeper_decision(str(gate_path))
                state = self.manager.load()

        rule_report = state.get("rule_report") or self._load_optional_json(
            (cwd / (rule_report_output or "data/rule_report.json"))
        )
        defect_summary = state.get("defect_summary") or {}
        visual_signal_report = self._load_optional_json(
            cwd / (((self.manager.load().get("artifacts") or {}).get("visual_signal_report")) or "data/visual_signal_report.json")
        )
        state = self.set_next_actions(
            self._derive_next_actions(rule_report=rule_report, visual_signal_report=visual_signal_report)
        )
        state = self.set_agents_this_round(
            self._derive_agents_this_round(state=state, visual_signal_report=visual_signal_report)
        )

        gatekeeper_output = gatekeeper_output or "data/gatekeeper_decision.json"
        if gatekeeper_report:
            gate_path = Path(gatekeeper_report)
            if not gate_path.is_absolute():
                gate_path = cwd / gate_path
            if gate_path.is_file():
                self.manager.ingest_gatekeeper_decision(str(gate_path))
        else:
            self._run_gatekeeper(output_path=gatekeeper_output)

        state = self.manager.load()
        decision = state.get("last_gatekeeper_decision")
        if decision is None:
            decision = "CONTINUE" if state.get("compile_success") else "BLOCKED"
        failure_type = None
        if decision != "DONE":
            compile_diagnostics = ((rule_report or {}).get("compile_diagnostics") or {})
            failure_type = (
                compile_diagnostics.get("primary_category")
                if not state.get("compile_success")
                else ("blocked_gate" if decision == "BLOCKED" else "remaining_defects")
            )
        self.manager.update_failure_tracking(decision=str(decision), failure_type=failure_type)

        state = self.add_history_entry(
            decision=decision,
            defects_found=defect_summary["remaining"],
            defects_resolved=defect_summary["resolved"],
            note=f"run-round:{Path(main_tex).name}",
        )

        state = self.manager.load()
        state["rule_report"] = rule_report
        return state

    def _ensure_initialized_state(
        self,
        main_tex: str,
        template: Optional[str] = None,
        target_pages: Optional[int] = None,
    ) -> Dict[str, Any]:
        try:
            state = self.manager.load()
            if not state.get("main_tex"):
                raise FileNotFoundError
            return state
        except FileNotFoundError:
            return self.init_task(main_tex=main_tex, template=template, target_pages=target_pages)

    def _load_optional_json(self, path: Path) -> Dict[str, Any]:
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _has_pending_float_priority_candidates(self, state: Optional[Dict[str, Any]] = None) -> bool:
        current_state = state or self.manager.load()
        repair_plan_summary = current_state.get("repair_plan_summary") or {}
        top_candidates = repair_plan_summary.get("top_candidates") or []
        if any(str(item.get("defect_family") or "") in {"B1", "B2"} for item in top_candidates):
            return True

        repair_plan_path = ((current_state.get("artifacts") or {}).get("repair_plan"))
        if not repair_plan_path:
            return False

        plan_path = Path(repair_plan_path)
        if not plan_path.is_absolute():
            plan_path = Path.cwd() / plan_path
        if not plan_path.is_file():
            return False

        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return False
        return any(str(item.get("defect_family") or "") in {"B1", "B2"} for item in plan.get("candidates") or [])

    def _derive_defect_summary(self, defect_report: Optional[Dict[str, Any]]) -> Dict[str, int]:
        state = self.manager.load()
        current = state.get("defect_summary") or {}
        defects = (defect_report or {}).get("defects") or []
        remaining = sum(
            1 for defect in defects if str(defect.get("status") or "open").lower() not in {"resolved", "closed"}
        )
        initial_total = max(int(current.get("initial_total") or 0), int(current.get("resolved") or 0) + remaining)
        resolved = max(initial_total - remaining, 0)
        return {
            "initial_total": initial_total,
            "resolved": resolved,
            "remaining": remaining,
        }

    def _derive_next_actions(
        self,
        rule_report: Optional[Dict[str, Any]],
        visual_signal_report: Optional[Dict[str, Any]] = None,
    ) -> list[str]:
        report = rule_report or {}
        summary = report.get("summary") or {}
        actions: list[str] = []
        float_priority_pending = self._has_pending_float_priority_candidates()
        if not report.get("compile_success", False):
            actions.append("Fix compilation blockers before visual loop continues")
        if int(summary.get("overfull_hbox_total") or 0) > 0:
            actions.append("Route D-class overflow defects to overflow-repair")
        if int(summary.get("underfull_hbox_total") or 0) > 0:
            if float_priority_pending:
                actions.append("Defer paragraph looseness/text edits until figure/table placement and sizing repairs are done")
            else:
                actions.append("Review spacing and paragraph looseness issues")
        routing_hints = (visual_signal_report or {}).get("routing_hints") or {}
        for action in routing_hints.get("next_actions") or []:
            if action not in actions:
                actions.append(action)
        priority_pages = routing_hints.get("priority_pages") or []
        if priority_pages:
            actions.append(f"Review priority pages from visual signals: {', '.join(str(p) for p in priority_pages[:5])}")
        cross_page_hints = (visual_signal_report or {}).get("cross_page_hints") or []
        if cross_page_hints:
            actions.append("Review recurring cross-page visual defects before local patching")
        crossref_hints = (visual_signal_report or {}).get("crossref_hints") or []
        if crossref_hints:
            actions.append("Review crossref distance hints for float-placement issues before moving figures")
        priority_objects = (visual_signal_report or {}).get("priority_objects") or []
        if priority_objects:
            top_objects = []
            for item in priority_objects[:3]:
                page = int(item.get("page") or 0)
                kind = str(item.get("object_kind") or "object").replace("_like", "")
                top_objects.append(f"p.{page} {kind}")
            actions.append(f"Inspect priority objects from visual signals: {', '.join(top_objects)}")
        repair_plan = (self.manager.load().get("repair_plan_summary") or {})
        if int(repair_plan.get("total_candidates") or 0) > 0:
            actions.append(
                f"Execute top repair-plan candidates first: {int(repair_plan.get('total_candidates') or 0)} queued"
            )
        if int(summary.get("warnings") or 0) == 0:
            actions.append("Proceed to quality gate evaluation")
        return actions

    def _derive_agents_this_round(
        self,
        state: Dict[str, Any],
        visual_signal_report: Optional[Dict[str, Any]] = None,
    ) -> list[str]:
        agents = ["orchestrator-agent", "rule-engine-agent"]
        if state.get("page_images_rendered"):
            agents.append("layout-detective-agent")

        findings = (visual_signal_report or {}).get("findings") or []
        taxonomy_ids = {str(f.get("taxonomy_defect_id") or "") for f in findings}
        float_priority_pending = self._has_pending_float_priority_candidates(state=state)

        if state.get("compile_success") and (
            (state.get("defect_summary") or {}).get("remaining", 0) > 0 or findings
        ):
            agents.append("code-surgeon-agent")

        if taxonomy_ids.intersection({"A1", "A2", "A3", "A6"}) and not float_priority_pending:
            agents.append("semantic-polish-agent")
        priority_objects = (visual_signal_report or {}).get("priority_objects") or []
        if any(str(item.get("object_kind") or "") in {"figure_like", "table_like"} for item in priority_objects):
            agents.append("layout-detective-agent")

        deduped: list[str] = []
        for agent in agents:
            if agent not in deduped:
                deduped.append(agent)
        return deduped

    def _parse_log_to_report(self, log_file: str, output_path: str) -> Dict[str, Any]:
        repo_scripts = Path(__file__).resolve().parent
        cmd = [
            self._python_executable(),
            str(repo_scripts / "parse_log.py"),
            log_file,
            "--output",
            output_path,
        ]
        completed = subprocess.run(cmd, check=False, cwd=Path.cwd(), capture_output=True, text=True)

        report_path = Path(output_path)
        if not report_path.is_file():
            raise subprocess.CalledProcessError(
                completed.returncode,
                cmd,
                output=completed.stdout,
                stderr=completed.stderr,
            )

        report = json.loads(report_path.read_text(encoding="utf-8"))
        if completed.returncode != 0:
            report["parse_log_runtime"] = {
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        return report

    def _run_gatekeeper(self, output_path: str) -> Dict[str, Any]:
        repo_scripts = Path(__file__).resolve().parent
        state = self.manager.load()
        cmd = [
            self._python_executable(),
            str(repo_scripts / "gatekeeper_enforcer.py"),
            "--state",
            str(self.manager.state_path),
            "--output",
            output_path,
        ]
        defect_report = ((state.get("artifacts") or {}).get("defect_report"))
        if defect_report:
            cmd.extend(["--defects", defect_report])
        semantic_report = ((state.get("artifacts") or {}).get("semantic_patch_report"))
        if semantic_report:
            cmd.extend(["--semantic-report", semantic_report])
        if bool(((state.get("task") or {}).get("strict_mode"))):
            cmd.append("--strict")
        subprocess.run(cmd, check=True, cwd=Path.cwd(), capture_output=True, text=True)
        self.manager.ingest_gatekeeper_decision(output_path)
        return self.manager.load()

    def _run_visual_signal_aggregator(
        self,
        pages_dir: str,
        output_path: str,
        rule_report_output: Optional[str] = None,
        crossrefs_output: Optional[str] = None,
    ) -> Dict[str, Any]:
        repo_scripts = Path(__file__).resolve().parent
        cmd = [
            self._python_executable(),
            str(repo_scripts / "visual_signal_aggregator.py"),
            pages_dir,
            "--output",
            output_path,
        ]
        state = self.manager.load()
        column_void_report = ((state.get("artifacts") or {}).get("column_void_report"))
        if column_void_report:
            cmd.extend(["--column-void-report", column_void_report])
        if rule_report_output:
            cmd.extend(["--log-report", rule_report_output])
        if crossrefs_output:
            cmd.extend(["--crossrefs-report", crossrefs_output])
        try:
            subprocess.run(cmd, check=True, cwd=Path.cwd(), capture_output=True, text=True)
        except subprocess.CalledProcessError:
            return {}
        return json.loads(Path(output_path).read_text(encoding="utf-8"))

    def _run_repair_plan_generator(
        self,
        visual_signal_report: str,
        output_path: str,
        crossrefs_output: Optional[str] = None,
        rule_report_output: Optional[str] = None,
        target_pages: Optional[int] = None,
    ) -> Dict[str, Any]:
        repo_scripts = Path(__file__).resolve().parent
        cmd = [
            self._python_executable(),
            str(repo_scripts / "repair_plan_generator.py"),
            visual_signal_report,
            "--output",
            output_path,
        ]
        if crossrefs_output:
            cmd.extend(["--crossrefs-report", crossrefs_output])
        if rule_report_output:
            cmd.extend(["--rule-report", rule_report_output])
        if target_pages is not None:
            cmd.extend(["--target-pages", str(target_pages)])
        try:
            subprocess.run(cmd, check=True, cwd=Path.cwd(), capture_output=True, text=True)
        except subprocess.CalledProcessError:
            return {}
        return json.loads(Path(output_path).read_text(encoding="utf-8"))

    def _run_defect_report_builder(
        self,
        rule_report_output: Optional[str],
        visual_signal_output: Optional[str],
        hygiene_report_output: Optional[str],
        output_path: str,
    ) -> Dict[str, Any]:
        repo_scripts = Path(__file__).resolve().parent
        cmd = [
            self._python_executable(),
            str(repo_scripts / "defect_report_builder.py"),
            "--output",
            output_path,
        ]
        if rule_report_output:
            cmd.extend(["--rule-report", rule_report_output])
        if visual_signal_output:
            cmd.extend(["--visual-signal-report", visual_signal_output])
        if hygiene_report_output:
            cmd.extend(["--hygiene-report", hygiene_report_output])
        subprocess.run(cmd, check=True, cwd=Path.cwd(), capture_output=True, text=True)
        return json.loads(Path(output_path).read_text(encoding="utf-8"))

    def _run_source_hygiene_check(self, main_tex: str, output_path: str) -> Dict[str, Any]:
        repo_scripts = Path(__file__).resolve().parent
        cmd = [
            self._python_executable(),
            str(repo_scripts / "source_hygiene_check.py"),
            main_tex,
            "--output",
            output_path,
        ]
        subprocess.run(cmd, check=True, cwd=Path.cwd(), capture_output=True, text=True)
        return json.loads(Path(output_path).read_text(encoding="utf-8"))

    def _run_repair_plan_executor(
        self,
        repair_plan_path: str,
        main_tex: str,
        output_path: str,
        column_type: Optional[str] = None,
        target_pages: Optional[int] = None,
        max_candidates: int = 3,
    ) -> Dict[str, Any]:
        repo_scripts = Path(__file__).resolve().parent
        cmd = [
            self._python_executable(),
            str(repo_scripts / "repair_plan_executor.py"),
            repair_plan_path,
            main_tex,
            "--output",
            output_path,
            "--max-candidates",
            str(max_candidates),
        ]
        if column_type:
            cmd.extend(["--column-type", column_type])
        if target_pages is not None:
            cmd.extend(["--target-pages", str(target_pages)])
        result = subprocess.run(cmd, check=False, cwd=Path.cwd(), capture_output=True, text=True)
        output_file = Path(output_path)
        if output_file.is_file():
            return json.loads(output_file.read_text(encoding="utf-8"))

        fallback = {
            "schema_version": "1.0",
            "status": "failed",
            "applied_count": 0,
            "selected_candidates": [],
            "error": {
                "returncode": result.returncode,
                "stdout_tail": (result.stdout or "")[-4000:],
                "stderr_tail": (result.stderr or "")[-4000:],
            },
        }
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(fallback, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return fallback

    def _extract_crossrefs(self, main_tex: str, output_path: str) -> Dict[str, Any]:
        repo_scripts = Path(__file__).resolve().parent
        cmd = [
            self._python_executable(),
            str(repo_scripts / "extract_crossrefs.py"),
            main_tex,
            "--output",
            output_path,
        ]
        subprocess.run(cmd, check=True, cwd=Path.cwd(), capture_output=True, text=True)
        return json.loads(Path(output_path).read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="PaperFit orchestrator runtime helpers")
    parser.add_argument("--state", default=StateManager.DEFAULT_STATE_PATH, help="Path to state.json")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    init_parser = subparsers.add_parser("init-task", help="Initialize task state")
    init_parser.add_argument("main_tex")
    init_parser.add_argument("--task", default="full_vto")
    init_parser.add_argument("--target-pages", type=int)
    init_parser.add_argument("--template")
    init_parser.add_argument("--strict", action="store_true")
    init_parser.add_argument("--max-rounds", type=int, default=10)
    init_parser.add_argument("--column-type", choices=["single", "double"], default=None)
    init_parser.add_argument("--page-budget", dest="page_budget_scope", default=None)

    subparsers.add_parser("start-round", help="Advance to the next round and reset round-scoped fields")

    compile_parser = subparsers.add_parser("mark-compile", help="Record compile result")
    compile_parser.add_argument("--success", action="store_true")
    compile_parser.add_argument("--failure", action="store_true")
    compile_parser.add_argument("--rule-report", dest="report_path", default=None)

    render_parser = subparsers.add_parser("mark-render", help="Record page-image render state")
    render_parser.add_argument("--rendered", action="store_true")
    render_parser.add_argument("--not-rendered", action="store_true")
    render_parser.add_argument("--page-dir", default=None)

    column_void_parser = subparsers.add_parser("ingest-column-void", help="Merge OpenCV column-void report into state")
    column_void_parser.add_argument("report_json")

    semantic_parser = subparsers.add_parser("ingest-semantic", help="Merge semantic budget report into state")
    semantic_parser.add_argument("report_json")

    defect_parser = subparsers.add_parser("set-defect-summary", help="Update defect summary fields")
    defect_parser.add_argument("--resolved", type=int, required=True)
    defect_parser.add_argument("--remaining", type=int, required=True)
    defect_parser.add_argument("--initial", type=int, default=None)

    actions_parser = subparsers.add_parser("set-next-actions", help="Replace next_actions list")
    actions_parser.add_argument("actions", nargs="+")

    agents_parser = subparsers.add_parser("set-agents", help="Replace agents_this_round list")
    agents_parser.add_argument("agents", nargs="+")

    artifact_parser = subparsers.add_parser("set-artifact", help="Set one artifacts.* field")
    artifact_parser.add_argument("key")
    artifact_parser.add_argument("value")

    history_parser = subparsers.add_parser("add-history", help="Append one history entry")
    history_parser.add_argument("--decision", default=None)
    history_parser.add_argument("--defects-found", type=int, default=None)
    history_parser.add_argument("--defects-resolved", type=int, default=None)
    history_parser.add_argument("--note", default=None)

    gate_parser = subparsers.add_parser("gatekeeper", help="Apply gatekeeper decision")
    gate_parser.add_argument("decision", choices=["DONE", "CONTINUE", "BLOCKED"])
    gate_parser.add_argument("--report", default=None)

    subparsers.add_parser("archive-task", help="Archive current state and return archive path")

    inspect_parser = subparsers.add_parser(
        "inspect-project",
        help="Parse existing project artifacts (.tex/.log/page images/crossrefs) into state",
    )
    inspect_parser.add_argument("main_tex")
    inspect_parser.add_argument("--log-file", default=None)
    inspect_parser.add_argument("--page-dir", default=None)
    inspect_parser.add_argument("--template", default=None)
    inspect_parser.add_argument("--target-pages", type=int, default=None)
    inspect_parser.add_argument("--crossrefs-output", default=None)
    inspect_parser.add_argument("--rule-report-output", default=None)

    infer_parser = subparsers.add_parser(
        "infer-task",
        help="Infer PaperFit task type from a natural-language request",
    )
    infer_parser.add_argument("request")

    run_round_parser = subparsers.add_parser(
        "run-round",
        help="Execute one round of project inspection and state aggregation",
    )
    run_round_parser.add_argument("main_tex")
    run_round_parser.add_argument("--log-file", default=None)
    run_round_parser.add_argument("--page-dir", default=None)
    run_round_parser.add_argument("--template", default=None)
    run_round_parser.add_argument("--target-pages", type=int, default=None)
    run_round_parser.add_argument("--crossrefs-output", default=None)
    run_round_parser.add_argument("--rule-report-output", default=None)
    run_round_parser.add_argument("--column-void-report", default=None)
    run_round_parser.add_argument("--semantic-report", default=None)
    run_round_parser.add_argument("--gatekeeper-report", default=None)
    run_round_parser.add_argument("--gatekeeper-output", default=None)

    repair_exec_parser = subparsers.add_parser(
        "execute-repair-plan",
        help="Execute top repair-plan candidates and write execution report",
    )
    repair_exec_parser.add_argument("main_tex")
    repair_exec_parser.add_argument("--repair-plan", dest="repair_plan_path", default=None)
    repair_exec_parser.add_argument("--output", dest="output_path", default="data/repair_execution_report.json")
    repair_exec_parser.add_argument("--column-type", default=None)
    repair_exec_parser.add_argument("--max-candidates", type=int, default=3)

    args = parser.parse_args()
    runtime = OrchestratorRuntime(state_path=args.state)

    if args.command == "init-task":
        out = runtime.init_task(
            main_tex=args.main_tex,
            task_type=args.task,
            target_pages=args.target_pages,
            template=args.template,
            strict_mode=args.strict,
            max_rounds=args.max_rounds,
            column_type=args.column_type,
            page_budget_scope=args.page_budget_scope,
        )
    elif args.command == "start-round":
        out = runtime.start_round()
    elif args.command == "mark-compile":
        success = True if args.success else False if args.failure else None
        if success is None:
            raise SystemExit("mark-compile requires --success or --failure")
        out = runtime.mark_compile(success=success, report_path=args.report_path)
    elif args.command == "mark-render":
        rendered = True if args.rendered else False if args.not_rendered else None
        if rendered is None:
            raise SystemExit("mark-render requires --rendered or --not-rendered")
        out = runtime.mark_page_images_rendered(rendered=rendered, page_dir=args.page_dir)
    elif args.command == "ingest-column-void":
        out = runtime.ingest_column_void_report(report_path=args.report_json)
    elif args.command == "ingest-semantic":
        out = runtime.ingest_semantic_report(report_path=args.report_json)
    elif args.command == "set-defect-summary":
        out = runtime.set_defect_summary(
            resolved=args.resolved,
            remaining=args.remaining,
            initial=args.initial,
        )
    elif args.command == "set-next-actions":
        out = runtime.set_next_actions(actions=args.actions)
    elif args.command == "set-agents":
        out = runtime.set_agents_this_round(agents=args.agents)
    elif args.command == "set-artifact":
        out = runtime.set_artifact(key=args.key, value=args.value)
    elif args.command == "add-history":
        out = runtime.add_history_entry(
            decision=args.decision,
            defects_found=args.defects_found,
            defects_resolved=args.defects_resolved,
            note=args.note,
        )
    elif args.command == "gatekeeper":
        out = runtime.apply_gatekeeper_decision(decision=args.decision, report_path=args.report)
    elif args.command == "archive-task":
        out = runtime.archive_task()
    elif args.command == "inspect-project":
        out = runtime.inspect_project(
            main_tex=args.main_tex,
            log_file=args.log_file,
            page_dir=args.page_dir,
            template=args.template,
            target_pages=args.target_pages,
            crossrefs_output=args.crossrefs_output,
            rule_report_output=args.rule_report_output,
        )
    elif args.command == "infer-task":
        out = runtime.infer_task_from_request(args.request)
    elif args.command == "run-round":
        out = runtime.run_round(
            main_tex=args.main_tex,
            log_file=args.log_file,
            page_dir=args.page_dir,
            template=args.template,
            target_pages=args.target_pages,
            crossrefs_output=args.crossrefs_output,
            rule_report_output=args.rule_report_output,
            column_void_report=args.column_void_report,
            semantic_report=args.semantic_report,
            gatekeeper_report=args.gatekeeper_report,
            gatekeeper_output=args.gatekeeper_output,
        )
    elif args.command == "execute-repair-plan":
        out = runtime.execute_repair_plan(
            main_tex=args.main_tex,
            repair_plan_path=args.repair_plan_path,
            output_path=args.output_path,
            column_type=args.column_type,
            max_candidates=args.max_candidates,
        )
    else:
        parser.print_help()
        raise SystemExit(1)

    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
