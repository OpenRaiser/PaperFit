#!/usr/bin/env python3
"""
PaperFit 状态管理器

管理 VTO 闭环中的状态持久化，包括读取、更新、备份和归档。
状态文件 state.json 记录了当前任务的所有关键信息，支持中断恢复和多轮迭代。

用法:
    paperfit run scripts/state_manager.py init <main_tex> [--task <type>] [--column-type double|single]
        [--page-budget main_body|with_refs|with_appendix] ...
    paperfit run scripts/state_manager.py column-void data/reports/column_void_r3.json
    paperfit run scripts/state_manager.py get <key>
    paperfit run scripts/state_manager.py set <key> <value>
    paperfit run scripts/state_manager.py update "<json_patch>"
    paperfit run scripts/state_manager.py next-round
    paperfit run scripts/state_manager.py archive
    paperfit run scripts/state_manager.py show
"""

import os
import re
import json
import shutil
import argparse
import sys
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional

from state_schema import STATE_VERSION, build_default_state, deep_update, validate_state


def _page_index_from_name(name: str) -> Optional[int]:
    m = re.search(r"page_(\d+)", name, re.IGNORECASE)
    if not m:
        return None
    return int(m.group(1), 10)


class StateManager:
    """状态管理器"""

    DEFAULT_STATE_PATH = "data/state.json"
    BACKUP_DIR = "data/backups"
    ARCHIVE_DIR = "data/archives"
    CASE_DIR = "data/benchmarks/case"  # Benchmark case directory

    def __init__(self, state_path: str = DEFAULT_STATE_PATH):
        self.state_path = Path(state_path)
        self.backup_dir = self.state_path.parent / "backups"
        self.archive_dir = self.state_path.parent / "archives"
        self.case_dir = self.state_path.parent / "benchmarks" / "case"
        self.state: Dict[str, Any] = {}

    def init_state(
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
        """初始化新任务的状态文件"""
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)

        self.state = build_default_state(
            main_tex=main_tex,
            task_type=task_type,
            target_pages=target_pages,
            template=template,
            strict_mode=strict_mode,
            max_rounds=max_rounds,
            column_type=column_type,
            page_budget_scope=page_budget_scope,
        )
        self.validate_state(self.state)
        self._save()
        return self.state

    def validate_state(self, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return validate_state(state if state is not None else self.state)

    def load(self) -> Dict[str, Any]:
        """加载当前状态"""
        if not self.state_path.exists():
            raise FileNotFoundError(f"State file not found: {self.state_path}")

        with open(self.state_path, 'r', encoding='utf-8') as f:
            loaded = json.load(f)

        self.state = self.validate_state(loaded)

        return self.state

    def save(self) -> None:
        """保存状态（自动备份旧版本）"""
        if self.state_path.exists():
            self._backup()
        self._save()

    def _save(self) -> None:
        """内部保存方法"""
        self.state = self.validate_state(self.state)
        self.state["updated_at"] = datetime.now().isoformat()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

        with open(self.state_path, 'w', encoding='utf-8') as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)

    def _backup(self) -> None:
        """备份当前状态文件和 case 目录"""
        if not self.state_path.exists():
            return

        self.backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 备份状态文件
        backup_name = f"state_{timestamp}.json"
        backup_path = self.backup_dir / backup_name
        shutil.copy2(self.state_path, backup_path)

        # 备份 case 目录（如果存在）
        if self.case_dir.exists():
            case_backup_name = f"case_{timestamp}"
            case_backup_path = self.backup_dir / case_backup_name
            self._backup_directory(self.case_dir, case_backup_path)

        # 保留最近 20 个备份
        self._cleanup_old_files(self.backup_dir, "state_*.json", keep=20)
        self._cleanup_old_files(self.backup_dir, "case_*", keep=20)

    def _backup_directory(self, src: Path, dst: Path) -> None:
        """递归备份目录，跳过大型文件和临时文件"""
        dst.mkdir(parents=True, exist_ok=True)
        skipped_extensions = {'.pdf', '.png', '.jpg', '.jpeg', '.log', '.aux', '.bbl', '.blg', '.out'}

        for item in src.rglob('*'):
            if item.is_file():
                # 跳过大型文件和临时文件
                if item.suffix in skipped_extensions:
                    continue
                if item.name.startswith('.'):
                    continue

                relative_path = item.relative_to(src)
                dst_path = dst / relative_path
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, dst_path)

    def get(self, key: str) -> Any:
        """获取状态中的指定键值（支持点号访问嵌套字段）"""
        if not self.state:
            self.load()

        keys = key.split('.')
        value = self.state
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return None
        return value

    def set(self, key: str, value: Any) -> None:
        """设置状态中的指定键值（支持点号访问嵌套字段）"""
        if not self.state:
            self.load()

        keys = key.split('.')
        target = self.state
        for k in keys[:-1]:
            if k not in target:
                target[k] = {}
            target = target[k]

        target[keys[-1]] = value
        self.save()

    def update(self, patch: Dict[str, Any]) -> None:
        """批量更新状态（深度合并）"""
        if not self.state:
            self.load()

        self._deep_update(self.state, patch)
        self.state = self.validate_state(self.state)
        self.save()

    def _deep_update(self, target: Dict, source: Dict) -> None:
        """递归深度合并字典"""
        deep_update(target, source)

    def _artifacts(self) -> Dict[str, Any]:
        if not self.state:
            self.load()
        artifacts = self.state.get("artifacts")
        if not isinstance(artifacts, dict):
            raise ValueError("state.artifacts missing or invalid")
        return artifacts

    def _content_integrity(self) -> Dict[str, Any]:
        if not self.state:
            self.load()
        content_integrity = self.state.get("content_integrity")
        if not isinstance(content_integrity, dict):
            raise ValueError("state.content_integrity missing or invalid")
        return content_integrity

    def next_round(self) -> Dict[str, Any]:
        """进入下一轮迭代"""
        if not self.state:
            self.load()

        self.state["current_round"] += 1
        self.state["status"] = "EVALUATING"
        self.state["agents_this_round"] = []
        self.state["compile_success"] = None
        self.state["page_images_rendered"] = False
        # 新一轮编译前清空上轮机检摘要，避免 orchestrator 误用陈旧 A5 信号
        artifacts = self._artifacts()
        artifacts["column_void_report"] = None
        artifacts["column_void_schema_version"] = None
        self.state["cv_signals_summary"] = {
            "schema_version": "1.0",
            "tool": "detect_column_void",
            "a5_candidate_pages": [],
            "a5_candidate_count": 0,
            "pages_flagged_count": 0,
            "by_page": [],
            "updated_at": None,
        }
        self.state["visual_signals_summary"] = {
            "schema_version": "1.0",
            "priority_pages": [],
            "priority_objects": [],
            "cross_page_hints": [],
            "crossref_hints": [],
            "consistency_summary": None,
            "updated_at": None,
        }
        self.state["repair_plan_summary"] = {
            "schema_version": "1.0",
            "total_candidates": 0,
            "top_candidates": [],
            "updated_at": None,
        }
        self.state["repair_execution_summary"] = {
            "schema_version": "1.0",
            "status": None,
            "applied_count": 0,
            "selected_candidates": [],
            "updated_at": None,
        }
        self.save()

        return self.state

    def archive(self) -> str:
        """归档当前状态（任务完成时调用）"""
        if not self.state:
            self.load()

        self.archive_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_name = f"state_final_{timestamp}.json"
        archive_path = self.archive_dir / archive_name

        # 更新状态标记
        self.state["status"] = "ARCHIVED"
        self.state["archived_at"] = datetime.now().isoformat()

        with open(archive_path, 'w', encoding='utf-8') as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)

        # 可选：删除当前状态文件表示任务完成
        # self.state_path.unlink(missing_ok=True)

        return str(archive_path)

    def _cleanup_old_files(self, directory: Path, pattern: str, keep: int) -> None:
        """清理旧文件，仅保留最近 keep 个"""
        files = sorted(directory.glob(pattern), key=os.path.getmtime, reverse=True)
        for old_file in files[keep:]:
            old_file.unlink()

    def add_history_entry(self, entry: Dict[str, Any]) -> None:
        """添加一轮历史记录"""
        if not self.state:
            self.load()

        if "history" not in self.state:
            self.state["history"] = []

        entry["timestamp"] = datetime.now().isoformat()
        self.state["history"].append(entry)
        self.save()

    def ingest_column_void_report(self, report_path: str) -> Dict[str, Any]:
        """
        读取 detect_column_void.py 输出的 JSON，写入 artifacts.column_void_report
        与 cv_signals_summary（轻量摘要，供 orchestrator / gatekeeper / layout-detective）。
        """
        rp = Path(report_path)
        if not rp.is_file():
            raise FileNotFoundError(f"列空洞报告不存在: {rp}")

        data = json.loads(rp.read_text(encoding="utf-8"))
        cwd = Path.cwd()
        try:
            rel_report = str(rp.resolve().relative_to(cwd.resolve()))
        except ValueError:
            rel_report = str(rp.resolve())

        pages = data.get("pages") or []
        by_page: List[Dict[str, Any]] = []
        pages_with: List[int] = []
        total_cand = 0

        for p in pages:
            if p.get("error"):
                continue
            cands = p.get("a5_candidates") or []
            idx = p.get("page_index")
            if idx is None and p.get("page_image"):
                idx = _page_index_from_name(Path(p["page_image"]).name)
            if idx is None and p.get("file"):
                idx = _page_index_from_name(Path(p["file"]).name)
            if not cands:
                continue
            if idx is not None:
                pages_with.append(int(idx))
            total_cand += len(cands)
            ratios = [float(c.get("void_ratio_of_column") or 0) for c in cands]
            by_page.append(
                {
                    "page_index": idx,
                    "page_image": p.get("page_image"),
                    "a5_candidate_count": len(cands),
                    "max_void_ratio": round(max(ratios), 4) if ratios else 0.0,
                }
            )

        pages_with = sorted(set(pages_with))
        schema_ver = data.get("schema_version") or "1.0"

        if not self.state:
            self.load()

        artifacts = self._artifacts()
        artifacts["column_void_report"] = rel_report
        artifacts["column_void_schema_version"] = schema_ver
        # 若报告内页图路径可推断目录，写入 page_images_dir
        first_img = None
        for p in pages:
            if p.get("page_image"):
                first_img = Path(p["page_image"]).parent
                break
        if first_img is not None:
            try:
                artifacts["page_images_dir"] = str(
                    first_img.resolve().relative_to(cwd.resolve())
                )
            except ValueError:
                artifacts["page_images_dir"] = str(first_img)

        self.state["cv_signals_summary"] = {
            "schema_version": "1.0",
            "tool": "detect_column_void",
            "a5_candidate_pages": pages_with,
            "a5_candidate_count": total_cand,
            "pages_flagged_count": len(pages_with),
            "by_page": by_page,
            "updated_at": datetime.now().isoformat(),
        }
        self.save()
        return {
            "artifacts": self.state["artifacts"],
            "cv_signals_summary": self.state["cv_signals_summary"],
        }

    def update_defect_summary(self, resolved: int, remaining: int, initial: Optional[int] = None) -> None:
        """更新缺陷摘要"""
        if not self.state:
            self.load()

        if initial is not None:
            self.state["defect_summary"]["initial_total"] = initial
        self.state["defect_summary"]["resolved"] = resolved
        self.state["defect_summary"]["remaining"] = remaining
        self.save()

    def ingest_defect_report(self, report_path: str) -> Dict[str, Any]:
        """
        读取 defect_report_builder 输出报告，写入 artifacts.defect_report
        与 defect_summary（统一缺陷口径，供 runtime / gatekeeper / status 消费）。
        """
        rp = Path(report_path)
        if not rp.is_file():
            raise FileNotFoundError(f"统一缺陷报告不存在: {rp}")

        data = json.loads(rp.read_text(encoding="utf-8"))
        cwd = Path.cwd()
        try:
            rel_report = str(rp.resolve().relative_to(cwd.resolve()))
        except ValueError:
            rel_report = str(rp.resolve())

        if not self.state:
            self.load()

        current_summary = self.state.get("defect_summary") or {}
        defects = data.get("defects") or []
        remaining = sum(
            1 for defect in defects if str(defect.get("status") or "open").lower() not in {"resolved", "closed"}
        )
        prior_resolved = int(current_summary.get("resolved") or 0)
        prior_initial = int(current_summary.get("initial_total") or 0)
        initial_total = max(prior_initial, prior_resolved + remaining)
        resolved = max(initial_total - remaining, 0)

        artifacts = self._artifacts()
        artifacts["defect_report"] = rel_report
        self.state["defect_summary"] = {
            "initial_total": initial_total,
            "resolved": resolved,
            "remaining": remaining,
        }
        self.save()
        return {
            "artifacts": self.state.get("artifacts"),
            "defect_summary": self.state.get("defect_summary"),
        }

    def ingest_visual_signal_report(self, report_path: str) -> Dict[str, Any]:
        """
        读取 visual_signal_aggregator 输出报告，写入 artifacts.visual_signal_report
        与 visual_signals_summary（轻量摘要，供 CLI / gatekeeper / agent 路由）。
        """
        rp = Path(report_path)
        if not rp.is_file():
            raise FileNotFoundError(f"视觉信号报告不存在: {rp}")

        data = json.loads(rp.read_text(encoding="utf-8"))
        cwd = Path.cwd()
        try:
            rel_report = str(rp.resolve().relative_to(cwd.resolve()))
        except ValueError:
            rel_report = str(rp.resolve())

        if not self.state:
            self.load()

        routing_hints = data.get("routing_hints") or {}
        priority_objects = []
        for item in (data.get("priority_objects") or [])[:5]:
            priority_objects.append(
                {
                    "page": int(item.get("page") or 0),
                    "object_kind": item.get("object_kind"),
                    "reason": item.get("reason"),
                    "priority_score": item.get("priority_score"),
                }
            )

        cross_page_hints = []
        for item in (data.get("cross_page_hints") or [])[:5]:
            cross_page_hints.append(
                {
                    "taxonomy_defect_id": item.get("taxonomy_defect_id"),
                    "pages": item.get("pages") or [],
                }
            )

        crossref_hints = []
        for item in (data.get("crossref_hints") or [])[:5]:
            crossref_hints.append(
                {
                    "label": item.get("label"),
                    "float_type": item.get("float_type"),
                    "severity": item.get("severity"),
                }
            )

        artifacts = self._artifacts()
        artifacts["visual_signal_report"] = rel_report
        self.state["visual_signals_summary"] = {
            "schema_version": str(data.get("schema_version") or "1.0"),
            "priority_pages": list(routing_hints.get("priority_pages") or []),
            "priority_objects": priority_objects,
            "cross_page_hints": cross_page_hints,
            "crossref_hints": crossref_hints,
            "consistency_summary": data.get("consistency_summary"),
            "updated_at": datetime.now().isoformat(),
        }
        self.save()
        return {
            "artifacts": self.state.get("artifacts"),
            "visual_signals_summary": self.state.get("visual_signals_summary"),
        }

    def ingest_repair_plan(self, report_path: str) -> Dict[str, Any]:
        """
        读取 repair_plan_generator 输出报告，写入 artifacts.repair_plan
        与 repair_plan_summary（供 CLI / 调度 / 证据链消费）。
        """
        rp = Path(report_path)
        if not rp.is_file():
            raise FileNotFoundError(f"修复计划不存在: {rp}")

        data = json.loads(rp.read_text(encoding="utf-8"))
        cwd = Path.cwd()
        try:
            rel_report = str(rp.resolve().relative_to(cwd.resolve()))
        except ValueError:
            rel_report = str(rp.resolve())

        if not self.state:
            self.load()

        top_candidates = []
        for item in (data.get("candidates") or [])[:5]:
            target = item.get("target") or {}
            top_candidates.append(
                {
                    "candidate_type": item.get("candidate_type"),
                    "page": item.get("page"),
                    "defect_family": item.get("defect_family"),
                    "proposed_action": item.get("proposed_action"),
                    "object_kind": target.get("object_kind"),
                    "label": target.get("label"),
                    "priority_score": item.get("priority_score"),
                }
            )

        artifacts = self._artifacts()
        artifacts["repair_plan"] = rel_report
        self.state["repair_plan_summary"] = {
            "schema_version": str(data.get("schema_version") or "1.0"),
            "total_candidates": int(((data.get("summary") or {}).get("total_candidates")) or 0),
            "top_candidates": top_candidates,
            "updated_at": datetime.now().isoformat(),
        }
        self.save()
        return {
            "artifacts": self.state.get("artifacts"),
            "repair_plan_summary": self.state.get("repair_plan_summary"),
        }

    def ingest_repair_execution_report(self, report_path: str) -> Dict[str, Any]:
        rp = Path(report_path)
        if not rp.is_file():
            raise FileNotFoundError(f"修复执行报告不存在: {rp}")

        data = json.loads(rp.read_text(encoding="utf-8"))
        cwd = Path.cwd()
        try:
            rel_report = str(rp.resolve().relative_to(cwd.resolve()))
        except ValueError:
            rel_report = str(rp.resolve())

        if not self.state:
            self.load()

        selected_payload = data.get("selected_candidates") or []
        selected_items: List[Dict[str, Any]] = []
        if isinstance(selected_payload, list):
            selected_items = [item for item in selected_payload if isinstance(item, dict)]
        elif isinstance(selected_payload, dict):
            for group_items in selected_payload.values():
                if not isinstance(group_items, list):
                    continue
                for item in group_items:
                    if isinstance(item, dict):
                        selected_items.append(item)

        selected = []
        for item in selected_items[:5]:
            selected.append(
                {
                    "defect_id": item.get("defect_id"),
                    "object": item.get("object"),
                    "page": item.get("page"),
                }
            )

        artifacts = self._artifacts()
        artifacts["repair_execution_report"] = rel_report
        self.state["repair_execution_summary"] = {
            "schema_version": str(data.get("schema_version") or "1.0"),
            "status": data.get("status"),
            "applied_count": int(data.get("applied_count") or 0),
            "selected_candidates": selected,
            "updated_at": datetime.now().isoformat(),
        }

        integrity = data.get("content_integrity") or {}
        if integrity:
            validation_status = "pending"
            integrity_status = str(integrity.get("status") or "").lower()
            if integrity_status == "pass":
                validation_status = "pass"
            elif integrity_status == "failed":
                validation_status = "fail"

            diff = integrity.get("diff") or {}
            violation = diff.get("violation") or {}
            violation_level = int(violation.get("level") or 0)
            action_taken = diff.get("action_required") or ("auto_rollback" if integrity.get("rollback_performed") else "none")
            rollback_target = data.get("main_tex") if integrity.get("rollback_performed") else None

            content_integrity = self._content_integrity()
            content_integrity["validation_status"] = validation_status
            content_integrity["violation_level"] = violation_level
            content_integrity["action_taken"] = action_taken
            content_integrity["rollback_target"] = rollback_target

        self.save()
        return {
            "artifacts": self.state.get("artifacts"),
            "repair_execution_summary": self.state.get("repair_execution_summary"),
            "content_integrity": self.state.get("content_integrity"),
        }

    def ingest_semantic_report(self, report_path: str) -> Dict[str, Any]:
        """
        读取 semantic_budgeter 输出报告，写入 artifacts.semantic_patch_report
        与 semantic_budget_summary，并同步内容完整性摘要字段。
        """
        rp = Path(report_path)
        if not rp.is_file():
            raise FileNotFoundError(f"语义补丁报告不存在: {rp}")

        data = json.loads(rp.read_text(encoding="utf-8"))
        cwd = Path.cwd()
        try:
            rel_report = str(rp.resolve().relative_to(cwd.resolve()))
        except ValueError:
            rel_report = str(rp.resolve())

        if not self.state:
            self.load()

        artifacts = self._artifacts()
        artifacts["semantic_patch_report"] = rel_report
        self.state["semantic_budget_summary"] = data.get("summary")

        integrity = data.get("integrity") or {}
        if integrity:
            violation_level = int(integrity.get("violation_level") or 0)
            action_required = integrity.get("action_required") or "none"
            validation_status = "pass" if violation_level == 0 else "fail"
            content_integrity = self._content_integrity()
            content_integrity["validation_status"] = validation_status
            content_integrity["violation_level"] = violation_level
            content_integrity["action_taken"] = action_required

        self.save()
        return {
            "artifacts": self.state.get("artifacts"),
            "semantic_budget_summary": self.state.get("semantic_budget_summary"),
            "content_integrity": self.state.get("content_integrity"),
        }

    def ingest_gatekeeper_decision(self, decision_path: str) -> Dict[str, Any]:
        """
        读取 gatekeeper_enforcer 决策报告，回写 last_gatekeeper_decision/status。
        """
        dp = Path(decision_path)
        if not dp.is_file():
            raise FileNotFoundError(f"门禁决策报告不存在: {dp}")
        data = json.loads(dp.read_text(encoding="utf-8"))
        decision = str(data.get("decision") or "CONTINUE")

        if not self.state:
            self.load()

        cwd = Path.cwd()
        try:
            rel_report = str(dp.resolve().relative_to(cwd.resolve()))
        except ValueError:
            rel_report = str(dp.resolve())

        artifacts = self._artifacts()
        artifacts["gatekeeper_decision"] = rel_report
        self.state["last_gatekeeper_decision"] = decision
        self.state["status"] = "DONE" if decision == "DONE" else ("BLOCKED" if decision == "BLOCKED" else "EVALUATING")
        self.save()
        return {
            "last_gatekeeper_decision": self.state["last_gatekeeper_decision"],
            "status": self.state["status"],
            "artifacts": self.state.get("artifacts"),
        }

    def update_failure_tracking(
        self,
        *,
        decision: str,
        failure_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.state:
            self.load()

        tracking = dict(self.state.get("failure_tracking") or {})
        normalized_decision = str(decision or "").upper()
        if normalized_decision == "DONE":
            tracking.update(
                {
                    "consecutive_failures": 0,
                    "stalled": False,
                    "conservative_mode": False,
                    "manual_review_required": False,
                    "last_failure_type": None,
                    "last_failure_round": None,
                    "last_failure_at": None,
                }
            )
        else:
            failures = int(tracking.get("consecutive_failures") or 0) + 1
            stalled = failures >= 3
            tracking.update(
                {
                    "consecutive_failures": failures,
                    "stalled": stalled,
                    "conservative_mode": stalled or normalized_decision == "BLOCKED",
                    "manual_review_required": stalled or normalized_decision == "BLOCKED",
                    "last_failure_type": failure_type or "round_not_done",
                    "last_failure_round": int(self.state.get("current_round") or 0),
                    "last_failure_at": datetime.now().isoformat(),
                }
            )

        self.state["failure_tracking"] = tracking
        self.save()
        return tracking

    def save_pre_repair_snapshot(self, snapshot_data: Dict[str, Any]) -> None:
        """
        保存修复前快照（语义哈希、内容分类、字数统计）

        Args:
            snapshot_data: 包含以下字段:
                - timestamp: ISO 时间戳
                - defect_id: 待修复缺陷 ID
                - target_file: 目标文件路径
                - target_line_range: [line_start, line_end]
                - content_type: C0-C7 分类
                - semantic_hash: SHA256 哈希
                - academic_word_count: 学术词汇数
                - sentence_count: 句子数
        """
        if not self.state:
            self.load()

        self.state["pre_repair_snapshot"] = snapshot_data
        self.save()

    def update_content_integrity_status(
        self,
        validation_status: str,
        violation_level: Optional[int] = None,
        action_taken: Optional[str] = None,
        rollback_target: Optional[str] = None
    ) -> None:
        """
        更新内容完整性验证状态

        Args:
            validation_status: 验证状态 ("pass", "fail", "pending")
            violation_level: 违规级别 (0-3，0 表示无违规)
            action_taken: 已执行操作 ("none", "log_only", "manual_review", "auto_rollback")
            rollback_target: 回滚目标文件路径（若触发自动回滚）
        """
        if not self.state:
            self.load()

        self.state["content_integrity"]["validation_status"] = validation_status
        self.state["content_integrity"]["violation_level"] = violation_level
        self.state["content_integrity"]["action_taken"] = action_taken
        self.state["content_integrity"]["rollback_target"] = rollback_target
        self.save()

    def get_content_integrity_status(self) -> Dict[str, Any]:
        """获取当前内容完整性状态"""
        if not self.state:
            self.load()

        return self.state.get("content_integrity", {
            "validation_status": None,
            "violation_level": None,
            "action_taken": None,
            "rollback_target": None
        })

    def clear_pre_repair_snapshot(self) -> None:
        """清除修复前快照（轮次切换时调用）"""
        if not self.state:
            self.load()

        self.state["pre_repair_snapshot"] = None
        self.save()


def main():
    parser = argparse.ArgumentParser(description="PaperFit State Manager")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # init 命令
    init_parser = subparsers.add_parser("init", help="Initialize new state")
    init_parser.add_argument("main_tex", help="Main .tex file path")
    init_parser.add_argument("--task", default="full_vto", help="Task type")
    init_parser.add_argument("--target-pages", type=int, help="Target page count")
    init_parser.add_argument("--template", help="Template name")
    init_parser.add_argument("--strict", action="store_true", help="Strict mode")
    init_parser.add_argument("--max-rounds", type=int, default=10, help="Max iterations")
    init_parser.add_argument(
        "--column-type",
        choices=["single", "double"],
        default=None,
        help="栏数：double 时在编译+页图后由 orchestrator 触发 OpenCV 列空洞检测",
    )
    init_parser.add_argument(
        "--page-budget",
        dest="page_budget_scope",
        choices=["main_body", "with_refs", "with_appendix"],
        default=None,
        help="页数口径：正文不含参考文献/附录 | 含参考文献的 PDF 总页 | 含附录整份 PDF",
    )

    # get 命令
    get_parser = subparsers.add_parser("get", help="Get value by key")
    get_parser.add_argument("key", help="Key path (e.g., 'task.target_pages')")

    # set 命令
    set_parser = subparsers.add_parser("set", help="Set value by key")
    set_parser.add_argument("key", help="Key path")
    set_parser.add_argument("value", help="Value (JSON string)")

    # update 命令
    update_parser = subparsers.add_parser("update", help="Batch update with JSON patch")
    update_parser.add_argument("patch", help="JSON patch string")

    # next-round 命令
    subparsers.add_parser("next-round", help="Increment round counter")

    # archive 命令
    subparsers.add_parser("archive", help="Archive current state")

    # show 命令
    subparsers.add_parser("show", help="Display current state")
    subparsers.add_parser("validate", help="Validate and normalize current state schema")

    cv_parser = subparsers.add_parser(
        "column-void",
        help="将 detect_column_void 报告合并进 data/state.json（artifacts + cv_signals_summary）",
    )
    cv_parser.add_argument("report_json", help="列空洞 JSON 报告路径")

    semantic_parser = subparsers.add_parser(
        "semantic-report",
        help="将 semantic_budgeter 报告合并进 data/state.json（artifacts + semantic_budget_summary）",
    )
    semantic_parser.add_argument("report_json", help="语义补丁报告 JSON 路径")

    gate_parser = subparsers.add_parser(
        "gatekeeper-decision",
        help="将 gatekeeper_enforcer 决策合并进 data/state.json（last_gatekeeper_decision + status）",
    )
    gate_parser.add_argument("decision_json", help="门禁决策 JSON 路径")

    args = parser.parse_args()
    manager = StateManager()

    try:
        if args.command == "init":
            state = manager.init_state(
                args.main_tex,
                task_type=args.task,
                target_pages=args.target_pages,
                template=args.template,
                strict_mode=args.strict,
                max_rounds=args.max_rounds,
                column_type=getattr(args, "column_type", None),
                page_budget_scope=getattr(args, "page_budget_scope", None),
            )
            print(f"State initialized: {manager.state_path}")
            print(json.dumps(state, indent=2))

        elif args.command == "get":
            value = manager.get(args.key)
            print(json.dumps(value, indent=2, ensure_ascii=False))

        elif args.command == "set":
            try:
                parsed_value = json.loads(args.value)
            except json.JSONDecodeError:
                parsed_value = args.value
            manager.set(args.key, parsed_value)
            print(f"Set {args.key} = {json.dumps(parsed_value, ensure_ascii=False)}")

        elif args.command == "update":
            patch = json.loads(args.patch)
            manager.update(patch)
            print("State updated")

        elif args.command == "next-round":
            state = manager.next_round()
            print(f"Advanced to round {state['current_round']}")

        elif args.command == "archive":
            archive_path = manager.archive()
            print(f"State archived to {archive_path}")

        elif args.command == "show":
            state = manager.load()
            print(json.dumps(state, indent=2, ensure_ascii=False))

        elif args.command == "validate":
            state = manager.load()
            manager.save()
            print("State schema valid")
            print(json.dumps(state, indent=2, ensure_ascii=False))

        elif args.command == "column-void":
            manager.load()
            merged = manager.ingest_column_void_report(args.report_json)
            print(json.dumps(merged, indent=2, ensure_ascii=False))

        elif args.command == "semantic-report":
            manager.load()
            merged = manager.ingest_semantic_report(args.report_json)
            print(json.dumps(merged, indent=2, ensure_ascii=False))

        elif args.command == "gatekeeper-decision":
            manager.load()
            merged = manager.ingest_gatekeeper_decision(args.decision_json)
            print(json.dumps(merged, indent=2, ensure_ascii=False))

        else:
            parser.print_help()
            sys.exit(1)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
