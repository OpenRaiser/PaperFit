#!/usr/bin/env python3
"候选分组批处理 + 原子接受。"

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from runtime_snapshots import (
        _resolve_backup_file,
        create_pre_repair_snapshot,
        discover_source_files,
        env_snapshot_root,
        load_snapshot_manifest,
        resolve_manifest_path,
        restore_snapshot,
    )
except ModuleNotFoundError:  # package import
    from .runtime_snapshots import (
        _resolve_backup_file,
        create_pre_repair_snapshot,
        discover_source_files,
        env_snapshot_root,
        load_snapshot_manifest,
        resolve_manifest_path,
        restore_snapshot,
    )

PHASED_REPAIR_SCHEMA_VERSION = "1.1"

LOCAL_SAFE_ACTIONS = {
    "repair_overfull_boxes",  # D1 段落级：长 token/局部 stretch（灾难级仍会被抢占逻辑排序）
    "wrap_url_breaking",
    "normalize_caption_spacing",
    "normalize_caption_setup",
    "apply_looseness",
    "compress_trailing_whitespace",
}

LAYOUT_SENSITIVE_ACTIONS = {
    "adjust_float_width",
    "split_display_formula",
    "remove_resizebox_around_tabular",
    "remove_preamble_textheight_shrink",
    "normalize_float_position_near_reference",
    "move_float_closer_to_first_reference",
    "convert_to_longtable",
    "force_here_placement",
}

GLOBAL_SENSITIVE_ACTIONS = {
    "review_paragraph_spacing_and_looseness",
    "balance_final_columns",
    "reduce_page_count",
    "expand_page_count",
    "decluster_float_sequence",
    "pack_late_floats_before_endmatter",
    "reflow_column_void",
}


VERTICAL_COST_ACTIONS = {
    "split_display_formula",      # 单行公式拆多行会增高
    "expand_page_count",
    "adjust_float_width",         # 放宽图会增高（缩小则相反，试编译裁决）
    "convert_to_longtable",
    "move_float_closer_to_first_reference",
}

# 明确增加页数风险的全局动作
PAGE_RISK_ACTIONS = GLOBAL_SENSITIVE_ACTIONS | {"split_display_formula"}



ACTION_CLASS_SUSPICION_RANK = {"global_sensitive": 0, "layout_sensitive": 1, "local_safe": 2}


def classify_action(proposed_action: str) -> str:
    action = str(proposed_action or "")
    if action in GLOBAL_SENSITIVE_ACTIONS:
        return "global_sensitive"
    if action in LAYOUT_SENSITIVE_ACTIONS:
        return "layout_sensitive"
    return "local_safe"


def candidate_action_metadata(candidate: Dict[str, Any]) -> Dict[str, Any]:
    "规格的候选元数据。"
    action = str(candidate.get("proposed_action") or "")
    action_class = classify_action(action)
    return {
        "action_class": action_class,
        "vertical_cost": 1 if action in VERTICAL_COST_ACTIONS else 0,
        "page_risk": "high" if action in PAGE_RISK_ACTIONS else ("medium" if action_class == "layout_sensitive" else "low"),
        "requires_trial_compile": action_class != "local_safe",
        "expected_scope": {
            "local_safe": "line",
            "layout_sensitive": "object",
            "global_sensitive": "document",
        }[action_class],
        "rollback_scope": "batch" if action_class == "local_safe" else "candidate",
    }


_OVERFULL_RE = re.compile(r"Overfull \\hbox \(([\d.]+)pt")
_UNDEF_RE = re.compile(r"LaTeX Warning: (?:Reference|Citation).*undefined")
_PROTECTED_PATTERNS = {
    "figures": re.compile(r"\\includegraphics"),
    "tables": re.compile(r"\\begin\{table\*?\}"),
    "captions": re.compile(r"\\caption"),
    "labels": re.compile(r"\\label\{"),
    "citations": re.compile(r"\\cite[pt]?\{"),
}


@dataclass
class CandidateOutcome:
    candidate_id: str
    action: str
    action_class: str
    applied: bool = False
    accepted: bool = False
    rejected: bool = False
    rolled_back: bool = False
    reason: str = ""
    source_anchor: Optional[Dict[str, Any]] = None
    
    
    unit_index: Optional[int] = None
    page: Optional[int] = None
    rollback_edits: Optional[List[Dict[str, Any]]] = field(default=None)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "action": self.action,
            "action_class": self.action_class,
            "applied": self.applied,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "rolled_back": self.rolled_back,
            "reason": self.reason,
            "source_anchor": self.source_anchor,
            "unit_index": self.unit_index,
            "page": self.page,
            "rollback_edits": self.rollback_edits,
        }


@dataclass
class RoundEvidenceSnapshot:
    page_count: Optional[int]
    severe_overfull: int
    total_overflow_pt: float
    overfull_count: int
    undefined_count: int
    protected: Dict[str, int]
    page_gap: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "page_count": self.page_count,
            "page_gap": self.page_gap,
            "severe_overfull": self.severe_overfull,
            "total_overflow_pt": self.total_overflow_pt,
            "overfull_count": self.overfull_count,
            "undefined_count": self.undefined_count,
            "protected": self.protected,
        }


def collect_evidence(
    project_root: Path,
    main_tex: str,
    page_count_fn: Callable[[Path], Optional[int]],
) -> RoundEvidenceSnapshot:
    """从最新编译产物收集门禁证据（页数/严重溢出/保护对象计数）。"""
    root = Path(project_root)
    log_path = root / Path(main_tex).with_suffix(".log").name
    log_text = ""
    if log_path.is_file():
        try:
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            log_text = ""
    widths = [float(m) for m in _OVERFULL_RE.findall(log_text)]
    protected: Dict[str, int] = {name: 0 for name in _PROTECTED_PATTERNS}
    try:
        for source in discover_source_files(root, main_tex):
            text = source.read_text(encoding="utf-8", errors="replace")
            for name, pattern in _PROTECTED_PATTERNS.items():
                protected[name] += len(pattern.findall(text))
    except Exception:
        pass
    page_count = page_count_fn(root / Path(main_tex).with_suffix(".pdf").name)
    return RoundEvidenceSnapshot(
        page_count=page_count,
        severe_overfull=sum(1 for w in widths if w >= 100.0),
        total_overflow_pt=round(sum(widths), 2),
        overfull_count=len(widths),
        undefined_count=len(_UNDEF_RE.findall(log_text)),
        protected=protected,
    )


def gate_decision(
    before: RoundEvidenceSnapshot,
    after: RoundEvidenceSnapshot,
    *,
    target_pages: Optional[int],
    vertical_cost: bool = False,
    strict_page_gap: bool = False,
) -> Tuple[bool, str]:
    "硬门：全部通过才接受。返回 (accepted, reason)。"
    if after.page_count is None:
        return False, "compile_or_render_failed: no readable PDF (fail-closed)"
    if before.page_count is not None:
        page_delta = after.page_count - before.page_count
        if target_pages is not None:
            gap_before = abs(before.page_count - target_pages)
            gap_after = abs(after.page_count - target_pages)
            if gap_after > gap_before:
                return False, f"page_gap worsened: {gap_before} -> {gap_after} (pages {before.page_count} -> {after.page_count}, target {target_pages})"
            # 超页预算时，带垂直代价的动作必须证明页数没有增加
            if vertical_cost and before.page_count > target_pages and after.page_count > before.page_count:
                return False, f"vertical-cost action grew pages while over budget: {before.page_count} -> {after.page_count}"
        elif strict_page_gap and page_delta > 0:
            return False, f"page count grew without a target budget: {before.page_count} -> {after.page_count}"
        elif page_delta > 0:
            return False, f"page count grew: {before.page_count} -> {after.page_count}"
        elif abs(page_delta) >= 2:
            
            
            return False, (
                f"page swing too large without a target budget: {before.page_count} -> {after.page_count} "
                f"(delta {page_delta:+d}); needs target_pages to adjudicate"
            )
    if after.severe_overfull > before.severe_overfull:
        return False, f"severe overfull increased: {before.severe_overfull} -> {after.severe_overfull}"
    for name, before_count in before.protected.items():
        if after.protected.get(name, 0) < before_count:
            return False, f"protected objects decreased: {name} {before_count} -> {after.protected.get(name, 0)}"
    if after.undefined_count > before.undefined_count:
        return False, f"undefined references increased: {before.undefined_count} -> {after.undefined_count}"
    return True, "all gates passed"







def _read_source_text(path: Path) -> str:
    """按字节读源文件并 surrogateescape 解码：回滚时能逐字节还原（含非 UTF-8 序列与行尾风格）。"""
    return path.read_bytes().decode("utf-8", errors="surrogateescape")


def _write_source_text(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8", errors="surrogateescape"))


def _count_fragment_lines(fragment: str) -> int:
    if not fragment:
        return 0
    return fragment.count("\n") + (0 if fragment.endswith("\n") else 1)


def diff_line_hunks(before_text: str, after_text: str) -> List[Dict[str, Any]]:
    """行级 diff：把一次编辑表达成 (before, after, after_start_line) hunk 列表。

    hunk 的 after_start_line 是该 hunk 在编辑后文件中的 1-based 起始行，
    撤销时优先按此定位，定位失败再全文唯一匹配，仍不唯一则拒绝（fail-closed）。
    """
    before_lines = before_text.splitlines(keepends=True)
    after_lines = after_text.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(a=before_lines, b=after_lines, autojunk=False)
    hunks: List[Dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        hunks.append(
            {
                "before": "".join(before_lines[i1:i2]),
                "after": "".join(after_lines[j1:j2]),
                "after_start_line": j1 + 1,
            }
        )
    return hunks


def revert_line_hunks(file_text: str, hunks: List[Dict[str, Any]]) -> Optional[str]:
    """在当前文件文本上撤销一组 hunk（after → before）。定位失败/歧义返回 None。"""
    lines = file_text.splitlines(keepends=True)
    for hunk in sorted(hunks, key=lambda h: -int(h.get("after_start_line") or 1)):
        after_frag = str(hunk.get("after") or "")
        before_frag = str(hunk.get("before") or "")
        n_after = _count_fragment_lines(after_frag)
        expected = int(hunk.get("after_start_line") or 1) - 1
        if n_after == 0:
            # 纯插入型 hunk 的撤销 = 在原位置插回 before 行；位置必须仍有效
            if not (0 <= expected <= len(lines)):
                return None
            chosen = expected
        else:
            def _match_at(idx: int) -> bool:
                return 0 <= idx and idx + n_after <= len(lines) and "".join(lines[idx : idx + n_after]) == after_frag

            if _match_at(expected):
                chosen = expected
            else:
                matches = [i for i in range(len(lines) - n_after + 1) if _match_at(i)]
                if len(matches) != 1:
                    return None
                chosen = matches[0]
        lines[chosen : chosen + n_after] = before_frag.splitlines(keepends=True)
    return "".join(lines)


def collect_unit_rollback_edits(
    project_root: Path,
    rollback_target: str,
) -> List[Dict[str, Any]]:
    """对比单元执行前快照与当前源码，记录该执行单元的源码编辑范围。

    返回 [{"file": 相对路径, "hunks": diff_line_hunks(...)}]；单元未改任何文件时为空列表。
    """
    root = Path(project_root)
    manifest = load_snapshot_manifest(root, rollback_target)
    manifest_dir = resolve_manifest_path(root, rollback_target).parent
    edits: List[Dict[str, Any]] = []
    for item in manifest.get("files") or []:
        if not isinstance(item, dict):
            continue
        rel = str(item.get("path") or "")
        if not rel:
            continue
        backup = _resolve_backup_file(root, manifest_dir, item)
        if backup is None or not backup.is_file():
            continue
        try:
            pre_text = _read_source_text(backup)
        except OSError:
            continue
        current = root / rel
        cur_text = _read_source_text(current) if current.is_file() else ""
        if pre_text == cur_text:
            continue
        hunks = diff_line_hunks(pre_text, cur_text)
        if hunks:
            edits.append({"file": rel, "hunks": hunks})
    return edits


def rollback_units_by_edits(
    project_root: Path,
    unit_edits: List[List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """手术式撤销若干执行单元的编辑：只动这些单元改过的 hunk，其余源码原样保留。

    两阶段：先把所有文件的新文本计算出来（任一 hunk 定位失败则整体放弃、不写盘），
    再统一写回——失败时源码保持尝试前状态。
    """
    root = Path(project_root).resolve()
    staged_text: Dict[str, str] = {}
    staged_dirty: Dict[str, bool] = {}
    for unit in reversed(unit_edits):  # 后执行的单元先撤销，减少行号漂移
        for file_entry in unit or []:
            rel = str(file_entry.get("file") or "")
            if not rel:
                continue
            path = root / rel
            if rel in staged_text:
                text = staged_text[rel]
            else:
                text = _read_source_text(path) if path.is_file() else ""
            hunks = file_entry.get("hunks") or []
            if not isinstance(hunks, list) or not hunks:
                continue
            new_text = revert_line_hunks(text, hunks)
            if new_text is None:
                return {
                    "success": False,
                    "reason": "hunk_not_locatable",
                    "file": rel,
                    "restored_files": [],
                }
            staged_text[rel] = new_text
            staged_dirty[rel] = True
    restored: List[str] = []
    for rel, new_text in staged_text.items():
        if not staged_dirty.get(rel):
            continue
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_source_text(path, new_text)
        restored.append(rel)
    return {"success": True, "reason": "units_reverted", "restored_files": restored}


def _intrusion_page(failure_evidence: Dict[str, Any]) -> Optional[int]:
    if not isinstance(failure_evidence, dict):
        return None
    pages: List[int] = []
    endmatter = failure_evidence.get("endmatter_start_page")
    if isinstance(endmatter, (int, float)):
        pages.append(int(endmatter))
    for intrusion in failure_evidence.get("intrusions") or []:
        if isinstance(intrusion, dict) and isinstance(intrusion.get("page"), (int, float)):
            pages.append(int(intrusion["page"]))
    return max(pages) if pages else None


def _unit_suspicion_key(unit: Dict[str, Any], intrusion_page: Optional[int]) -> Tuple[int, int, int]:
    """致害嫌疑排序：副作用范围大的优先（global > layout > local），

    距离失败证据页近的候选优先，同嫌疑时后接受的优先。
    """
    classes = unit.get("action_classes") or ["local_safe"]
    rank = min((ACTION_CLASS_SUSPICION_RANK.get(cls, 2) for cls in classes), default=2)
    near = 1
    if intrusion_page is not None:
        pages = [int(p) for p in (unit.get("pages") or []) if isinstance(p, (int, float))]
        near = 0 if any(p >= intrusion_page - 1 for p in pages) else 1
    return (rank, near, -int(unit.get("unit_index") or 0))


def attribute_and_rollback_round_failure(
    *,
    project_root: Path,
    main_tex: str,
    candidate_outcomes: List[Dict[str, Any]],
    failure_evidence: Dict[str, Any],
    verify_fn: Callable[[List[str]], Dict[str, Any]],
) -> Dict[str, Any]:
    "轮级失败时的候选归因回滚。"
    root = Path(project_root)
    report: Dict[str, Any] = {
        "schema_version": "1.0",
        "status": "not_supported",
        "attributed_candidate": None,
        "rolled_back_candidate_ids": [],
        "attempts": [],
        "attempts_used": 0,
        "candidate_units_considered": 0,
        "reason": "",
    }

    units: Dict[int, Dict[str, Any]] = {}
    for outcome in candidate_outcomes or []:
        if not isinstance(outcome, dict) or not outcome.get("accepted"):
            continue
        unit_index = outcome.get("unit_index")
        if not isinstance(unit_index, int):
            continue
        edits = outcome.get("rollback_edits")
        if not isinstance(edits, list) or not edits:
            # 无编辑范围的接受候选无法手术式撤销（保持保留，不阻塞其余归因）
            continue
        unit = units.setdefault(
            unit_index,
            {
                "unit_index": unit_index,
                "candidate_ids": [],
                "action_classes": [],
                "pages": [],
                "edits": edits,
            },
        )
        unit["candidate_ids"].append(str(outcome.get("candidate_id")))
        unit["action_classes"].append(str(outcome.get("action_class") or "local_safe"))
        if isinstance(outcome.get("page"), (int, float)):
            unit["pages"].append(int(outcome["page"]))
    if not units:
        report["reason"] = "no_accepted_candidate_with_rollback_edits"
        return report
    report["candidate_units_considered"] = len(units)

    intrusion_page = _intrusion_page(failure_evidence)
    suspicion_order = sorted(units.values(), key=lambda u: _unit_suspicion_key(u, intrusion_page))
    reverse_acceptance_order = sorted(units.values(), key=lambda u: -int(u["unit_index"]))

    # 尝试序列：第 1 个 = 嫌疑最大的单元；其后按"最后接受的候选逆序"逐个累计
    sequence: List[Dict[str, Any]] = [suspicion_order[0]]
    for unit in reverse_acceptance_order:
        if unit["unit_index"] != sequence[0]["unit_index"]:
            sequence.append(unit)

    rolled_units: List[Dict[str, Any]] = []
    attributed_unit: Optional[Dict[str, Any]] = None
    for unit in sequence:
        rolled_units.append(unit)
        report["attempts_used"] += 1
        rollback_report = rollback_units_by_edits(root, [u["edits"] for u in rolled_units])
        if not rollback_report.get("success"):
            report["attempts"].append(
                {
                    "rolled_back_units": [u["unit_index"] for u in rolled_units],
                    "cleared": False,
                    "reason": f"surgical_revert_failed:{rollback_report.get('reason')}",
                }
            )
            continue
        rolled_ids = [cid for u in rolled_units for cid in u["candidate_ids"]]
        verification = verify_fn(rolled_ids)
        attempt_entry = {
            "rolled_back_units": [u["unit_index"] for u in rolled_units],
            "rolled_back_candidate_ids": rolled_ids,
            "cleared": bool(verification.get("cleared")),
            "reason": str(verification.get("reason") or ""),
        }
        report["attempts"].append(attempt_entry)
        if verification.get("cleared"):
            attributed_unit = unit
            break

    if attributed_unit is not None:
        rolled_ids = [cid for u in rolled_units for cid in u["candidate_ids"]]
        report["status"] = "candidate" if len(rolled_units) == 1 else "candidates_partial"
        # 归因候选 = 定位到致害的那次回滚中新加入的单元；单候选单元才有唯一 id
        last_ids = attributed_unit["candidate_ids"]
        report["attributed_candidate"] = last_ids[0] if len(last_ids) == 1 else None
        report["rolled_back_candidate_ids"] = rolled_ids
        report["reason"] = "attributed_and_rolled_back"
        return report

    report["status"] = "candidates_exhausted"
    report["rolled_back_candidate_ids"] = [cid for u in rolled_units for cid in u["candidate_ids"]]
    report["reason"] = "all_accepted_candidates_rolled_back_without_clearing_gates"
    return report


def _subset_plan(plan: Dict[str, Any], subset: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {**{k: v for k, v in plan.items() if k != "candidates"}, "candidates": subset}


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def execute_phased_repair(
    *,
    project_root: Path,
    repair_plan_path: Path,
    main_tex: str,
    column_type: Optional[str] = None,
    target_pages: Optional[int] = None,
    max_local_batch: int = 6,
    max_layout_candidates: int = 3,
    max_global_candidates: int = 1,
    execute_fn: Optional[Callable[..., Dict[str, Any]]] = None,
    compile_fn: Optional[Callable[..., Dict[str, Any]]] = None,
    page_count_fn: Optional[Callable[[Path], Optional[int]]] = None,
    report_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """分组执行修复计划，返回与 repair_execution_summary 兼容的汇总。"""

    root = Path(project_root)
    plan = json.loads(Path(repair_plan_path).read_text(encoding="utf-8"))
    all_candidates = [c for c in plan.get("candidates") or [] if isinstance(c, dict) and not c.get("requires_human_review")]

    if execute_fn is None:
        from repair_plan_executor import execute_repair_plan as execute_fn  # type: ignore[no-redef]
    if compile_fn is None:
        from runtime_actions import compile_latex as _compile  # type: ignore[no-redef]

        def compile_fn(subset_root: Path, subset_main: str):  # type: ignore[misc]
            return _compile(subset_root, main_tex=Path(subset_main))
    if page_count_fn is None:
        from runtime_actions import pdf_page_count as page_count_fn  # type: ignore[no-redef]

    groups: Dict[str, List[Dict[str, Any]]] = {"local_safe": [], "layout_sensitive": [], "global_sensitive": []}
    for candidate in all_candidates:
        meta = candidate_action_metadata(candidate)
        candidate.update(meta)
        groups[meta["action_class"]].append(candidate)
    groups["local_safe"] = groups["local_safe"][:max_local_batch]
    groups["layout_sensitive"] = groups["layout_sensitive"][:max_layout_candidates]
    groups["global_sensitive"] = groups["global_sensitive"][:max_global_candidates]

    outcomes: List[CandidateOutcome] = []
    accepted_count = 0
    rolled_back_count = 0
    rejected_count = 0

    # 基线证据：轮开始时的编译产物由调用方保证存在（repair 前 observe 已编译）
    baseline = collect_evidence(root, main_tex, page_count_fn)
    current = baseline

    def _candidate_outcome(candidate: Dict[str, Any]) -> CandidateOutcome:
        page_value = candidate.get("page")
        return CandidateOutcome(
            candidate_id=str(
                candidate.get("overflow_id")
                or candidate.get("finding_id")
                or candidate.get("line_number")
                or candidate.get("proposed_action")
            ),
            action=str(candidate.get("proposed_action") or ""),
            action_class=candidate.get("action_class") or classify_action(candidate.get("proposed_action")),
            source_anchor=candidate.get("source_anchor"),
            page=int(page_value) if isinstance(page_value, (int, float)) else None,
        )

    def _trial_compile() -> RoundEvidenceSnapshot:
        compile_fn(root, main_tex)
        return collect_evidence(root, main_tex, page_count_fn)

    def _record_unit_edits(unit_outcomes: List[CandidateOutcome], unit_index: int, rollback_target: str) -> None:
        "接受后记录该单元的源码编辑范围（供轮级失败归因时单独撤销）。"
        try:
            edits = collect_unit_rollback_edits(root, rollback_target)
        except Exception:
            edits = None
        for oc in unit_outcomes:
            oc.unit_index = unit_index
            oc.rollback_edits = edits

    unit_counter = 0

    # —— Phase 1: local_safe 批量（一次试编译，批次级回滚）——
    local_batch = groups["local_safe"]
    if local_batch:
        snapshot = create_pre_repair_snapshot(
            project_root=root,
            main_tex=main_tex,
            snapshot_id=f"phased_local_{datetime.now().strftime('%H%M%S_%f')}",
            snapshot_root=env_snapshot_root(),
        )
        local_plan_path = root / "data" / "phased_local_plan.json"
        _write_json(local_plan_path, _subset_plan(plan, local_batch))
        subset_report = execute_fn(
            str(local_plan_path),
            main_tex,
            str(root / "data" / "phased_local_report.json"),
            column_type=column_type,
            target_pages=target_pages,
            max_candidates=max_local_batch,
        )
        applied_here = int(subset_report.get("applied_count") or 0)
        batch_outcomes = [_candidate_outcome(c) for c in local_batch]
        for oc in batch_outcomes:
            oc.applied = applied_here > 0
        if applied_here == 0:
            for oc in batch_outcomes:
                oc.rejected = True
                oc.reason = "batch produced no source change"
                rejected_count += 1
        else:
            after = _trial_compile()
            accepted, reason = gate_decision(current, after, target_pages=target_pages)
            if accepted:
                for oc in batch_outcomes:
                    oc.accepted = True
                    oc.reason = reason
                    accepted_count += 1
                current = after
                _record_unit_edits(batch_outcomes, unit_counter, str(snapshot.get("rollback_target")))
            else:
                restore_snapshot(project_root=root, rollback_target=str(snapshot.get("rollback_target")))
                for oc in batch_outcomes:
                    oc.rolled_back = True
                    oc.reason = reason
                    rolled_back_count += 1
        unit_counter += 1
        outcomes.extend(batch_outcomes)

    # —— Phase 2: layout_sensitive 逐候选（每候选快照 + 试编译，失败只回滚该候选）——
    for idx, candidate in enumerate(groups["layout_sensitive"]):
        outcome = _candidate_outcome(candidate)
        snapshot = create_pre_repair_snapshot(
            project_root=root,
            main_tex=main_tex,
            snapshot_id=f"phased_layout_{idx}_{datetime.now().strftime('%H%M%S_%f')}",
            snapshot_root=env_snapshot_root(),
        )
        subset_plan_path = root / "data" / f"phased_layout_plan_{idx}.json"
        _write_json(subset_plan_path, _subset_plan(plan, [candidate]))
        subset_report = execute_fn(
            str(subset_plan_path),
            main_tex,
            str(root / "data" / f"phased_layout_report_{idx}.json"),
            column_type=column_type,
            target_pages=target_pages,
            max_candidates=1,
        )
        applied_here = int(subset_report.get("applied_count") or 0)
        outcome.applied = applied_here > 0
        if applied_here == 0:
            outcome.rejected = True
            outcome.reason = "fixer produced no source change"
            rejected_count += 1
            outcomes.append(outcome)
            continue
        after = _trial_compile()
        accepted, reason = gate_decision(
            current,
            after,
            target_pages=target_pages,
            vertical_cost=bool(candidate.get("vertical_cost")),
        )
        if accepted:
            outcome.accepted = True
            outcome.reason = reason
            accepted_count += 1
            current = after
            _record_unit_edits([outcome], unit_counter, str(snapshot.get("rollback_target")))
        else:
            restore_snapshot(project_root=root, rollback_target=str(snapshot.get("rollback_target")))
            outcome.rolled_back = True
            outcome.reason = reason
            rolled_back_count += 1
        unit_counter += 1
        outcomes.append(outcome)

    # —— Phase 3: global_sensitive 单独试编译（严格 page_gap）——
    for idx, candidate in enumerate(groups["global_sensitive"]):
        outcome = _candidate_outcome(candidate)
        snapshot = create_pre_repair_snapshot(
            project_root=root,
            main_tex=main_tex,
            snapshot_id=f"phased_global_{idx}_{datetime.now().strftime('%H%M%S_%f')}",
            snapshot_root=env_snapshot_root(),
        )
        subset_plan_path = root / "data" / f"phased_global_plan_{idx}.json"
        _write_json(subset_plan_path, _subset_plan(plan, [candidate]))
        subset_report = execute_fn(
            str(subset_plan_path),
            main_tex,
            str(root / "data" / f"phased_global_report_{idx}.json"),
            column_type=column_type,
            target_pages=target_pages,
            max_candidates=1,
        )
        applied_here = int(subset_report.get("applied_count") or 0)
        outcome.applied = applied_here > 0
        if applied_here == 0:
            outcome.rejected = True
            outcome.reason = "fixer produced no source change"
            rejected_count += 1
            outcomes.append(outcome)
            continue
        after = _trial_compile()
        accepted, reason = gate_decision(current, after, target_pages=target_pages, strict_page_gap=True)
        if accepted:
            outcome.accepted = True
            outcome.reason = reason
            accepted_count += 1
            current = after
            _record_unit_edits([outcome], unit_counter, str(snapshot.get("rollback_target")))
        else:
            restore_snapshot(project_root=root, rollback_target=str(snapshot.get("rollback_target")))
            outcome.rolled_back = True
            outcome.reason = reason
            rolled_back_count += 1
        unit_counter += 1
        outcomes.append(outcome)

    summary = {
        "schema_version": PHASED_REPAIR_SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(),
        "status": "success" if accepted_count else ("partial" if applied_any(outcomes) else "noop"),
        "execution_mode": "phased_atomic",
        "applied_count": accepted_count,
        "accepted_count": accepted_count,
        "rejected_count": rejected_count,
        "rolled_back_count": rolled_back_count,
        "selected_candidates": [
            {
                "defect_id": c.get("defect_family"),
                "object": (c.get("target") or {}).get("label") or "overflow",
                "page": c.get("page") or 0,
                "action": c.get("proposed_action"),
                "action_class": c.get("action_class"),
                "finding_id": c.get("finding_id"),
                "line_number": c.get("line_number"),
            }
            for group in groups.values()
            for c in group
        ],
        "candidate_outcomes": [oc.to_dict() for oc in outcomes],
        "baseline_evidence": baseline.to_dict(),
        "final_evidence": current.to_dict(),
    }
    if report_path is not None:
        _write_json(Path(report_path), summary)
    return summary


def applied_any(outcomes: List[CandidateOutcome]) -> bool:
    return any(oc.applied for oc in outcomes)
