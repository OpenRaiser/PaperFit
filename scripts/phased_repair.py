#!/usr/bin/env python3
"候选分组批处理 + 原子接受。"

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from runtime_snapshots import create_pre_repair_snapshot, discover_source_files, restore_snapshot
except ModuleNotFoundError:  # package import
    from .runtime_snapshots import create_pre_repair_snapshot, discover_source_files, restore_snapshot

PHASED_REPAIR_SCHEMA_VERSION = "1.0"

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
        )

    def _trial_compile() -> RoundEvidenceSnapshot:
        compile_fn(root, main_tex)
        return collect_evidence(root, main_tex, page_count_fn)

    # —— Phase 1: local_safe 批量（一次试编译，批次级回滚）——
    local_batch = groups["local_safe"]
    if local_batch:
        snapshot = create_pre_repair_snapshot(
            project_root=root, main_tex=main_tex, snapshot_id=f"phased_local_{datetime.now().strftime('%H%M%S_%f')}"
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
            else:
                restore_snapshot(project_root=root, rollback_target=str(snapshot.get("rollback_target")))
                for oc in batch_outcomes:
                    oc.rolled_back = True
                    oc.reason = reason
                    rolled_back_count += 1
        outcomes.extend(batch_outcomes)

    # —— Phase 2: layout_sensitive 逐候选（每候选快照 + 试编译，失败只回滚该候选）——
    for idx, candidate in enumerate(groups["layout_sensitive"]):
        outcome = _candidate_outcome(candidate)
        snapshot = create_pre_repair_snapshot(
            project_root=root, main_tex=main_tex, snapshot_id=f"phased_layout_{idx}_{datetime.now().strftime('%H%M%S_%f')}"
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
        else:
            restore_snapshot(project_root=root, rollback_target=str(snapshot.get("rollback_target")))
            outcome.rolled_back = True
            outcome.reason = reason
            rolled_back_count += 1
        outcomes.append(outcome)

    # —— Phase 3: global_sensitive 单独试编译（严格 page_gap）——
    for idx, candidate in enumerate(groups["global_sensitive"]):
        outcome = _candidate_outcome(candidate)
        snapshot = create_pre_repair_snapshot(
            project_root=root, main_tex=main_tex, snapshot_id=f"phased_global_{idx}_{datetime.now().strftime('%H%M%S_%f')}"
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
        else:
            restore_snapshot(project_root=root, rollback_target=str(snapshot.get("rollback_target")))
            outcome.rolled_back = True
            outcome.reason = reason
            rolled_back_count += 1
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
