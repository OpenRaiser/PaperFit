#!/usr/bin/env python3
"""每轮统一接受函数：页距离 + overfull + 保护对象 + 引用差值的硬门禁。

终版方案第四节/第六节：修复不能只看"改了什么"，必须看"改完后整体有没有变差"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class RoundEvidence:
    """一轮修复前后的证据快照。"""
    # 页数
    page_count_before: Optional[int] = None
    page_count_after: Optional[int] = None
    target_pages: Optional[int] = None

    # Overfull（归因后的可见计数，不是 raw）
    overfull_before: Optional[int] = None
    overfull_after: Optional[int] = None

    # 保护对象计数（不能减少）
    figures_before: int = 0
    figures_after: int = 0
    tables_before: int = 0
    tables_after: int = 0
    labels_before: int = 0
    labels_after: int = 0
    refs_before: int = 0
    refs_after: int = 0

    # 引用差值
    undefined_refs_before: int = 0
    undefined_refs_after: int = 0
    undefined_citations_before: int = 0
    undefined_citations_after: int = 0

    # 缺陷
    major_defects_before: int = 0
    major_defects_after: int = 0

    # 内容完整性
    content_integrity_pass: bool = True

    @property
    def page_distance_before(self) -> Optional[int]:
        if self.page_count_before is None or self.target_pages is None:
            return None
        return abs(self.page_count_before - self.target_pages)

    @property
    def page_distance_after(self) -> Optional[int]:
        if self.page_count_after is None or self.target_pages is None:
            return None
        return abs(self.page_count_after - self.target_pages)


@dataclass
class AcceptanceDecision:
    """接受/拒绝判定及原因。"""
    accepted: bool
    reasons: List[str] = field(default_factory=list)
    rollback_required: bool = False
    no_effect: bool = False
    no_effect_reason: Optional[str] = None


def evaluate_round_acceptance(evidence: RoundEvidence) -> AcceptanceDecision:
    """统一接受函数：所有条件都通过才接受。"""
    reasons: List[str] = []

    # 1. 页距离不能增加
    if evidence.page_distance_before is not None and evidence.page_distance_after is not None:
        if evidence.page_distance_after > evidence.page_distance_before:
            reasons.append(
                f"page_distance increased: {evidence.page_distance_before} -> {evidence.page_distance_after} "
                f"(pages {evidence.page_count_before} -> {evidence.page_count_after}, target {evidence.target_pages})"
            )

    # 2. Overfull 不能增加
    if evidence.overfull_before is not None and evidence.overfull_after is not None:
        if evidence.overfull_after > evidence.overfull_before:
            reasons.append(
                f"overfull increased: {evidence.overfull_before} -> {evidence.overfull_after}"
            )

    # 3. 保护对象不能减少
    if evidence.figures_after < evidence.figures_before:
        reasons.append(f"figures lost: {evidence.figures_before} -> {evidence.figures_after}")
    if evidence.tables_after < evidence.tables_before:
        reasons.append(f"tables lost: {evidence.tables_before} -> {evidence.tables_after}")
    if evidence.labels_after < evidence.labels_before:
        reasons.append(f"labels lost: {evidence.labels_before} -> {evidence.labels_after}")
    if evidence.refs_after < evidence.refs_before:
        reasons.append(f"refs lost: {evidence.refs_before} -> {evidence.refs_after}")

    # 4. 不能新增未解引用
    new_refs = evidence.undefined_refs_after - evidence.undefined_refs_before
    if new_refs > 0:
        reasons.append(f"new undefined refs: +{new_refs}")
    new_cites = evidence.undefined_citations_after - evidence.undefined_citations_before
    if new_cites > 0:
        reasons.append(f"new undefined citations: +{new_cites}")

    # 5. 不能新增 major 缺陷
    if evidence.major_defects_after > evidence.major_defects_before:
        reasons.append(
            f"new major defects: {evidence.major_defects_before} -> {evidence.major_defects_after}"
        )

    # 6. 内容完整性必须通过
    if not evidence.content_integrity_pass:
        reasons.append("content integrity failed")

    accepted = len(reasons) == 0
    rollback = not accepted and any(
        "increased" in r or "lost" in r or "new " in r or "failed" in r
        for r in reasons
    )

    return AcceptanceDecision(
        accepted=accepted,
        reasons=reasons if not accepted else ["all gates passed"],
        rollback_required=rollback,
    )


def detect_no_effect(
    *,
    source_changed: bool,
    applied_count: int,
    run_failed: bool,
    rejection_reasons: Optional[List[str]] = None,
) -> Optional[str]:
    """检测 no_effect 状态并返回具体原因。"""
    if run_failed:
        return None  # 运行失败不是 no_effect

    if source_changed or applied_count > 0:
        return None  # 确实改了源码

    # 源码未变，分类原因
    if rejection_reasons:
        for reason in rejection_reasons:
            if "anchor" in reason.lower():
                return "no_unique_source_anchor"
            if "approval" in reason.lower() or "blocked" in reason.lower():
                return "candidate_blocked_by_approval"
            if "page_budget" in reason.lower() or "page_distance" in reason.lower():
                return "candidate_rejected_by_page_budget"
            if "quality" in reason.lower() or "gate" in reason.lower():
                return "candidate_rejected_by_quality_gate"
        return "candidate_rejected"

    return "no_actionable_candidate"


def build_round_evidence_from_state(
    *,
    state_before: Dict[str, Any],
    state_after: Dict[str, Any],
    rule_report_before: Optional[Dict[str, Any]] = None,
    rule_report_after: Optional[Dict[str, Any]] = None,
) -> RoundEvidence:
    """从运行时 state 和 rule_report 构建证据。"""
    def _pages(state, rule_report):
        # 优先从 visual report 取页数，其次从 rule_report
        visual = (state.get("artifacts") or {}).get("visual_signal_report")
        if visual:
            summary = visual.get("summary") if isinstance(visual, dict) else {}
            pages = (summary or {}).get("pages_analyzed")
            if pages:
                return int(pages)
        if rule_report:
            for entry in (rule_report.get("warnings") or []):
                if "Output written" in str(entry.get("context") or ""):
                    import re
                    match = re.search(r"\((\d+) page", str(entry.get("context")))
                    if match:
                        return int(match.group(1))
        return None

    def _overfull(rule_report):
        if not rule_report:
            return None
        return int((rule_report.get("summary") or {}).get("overfull_hbox_total") or 0)

    def _undef(rule_report, key):
        if not rule_report:
            return 0
        summary = rule_report.get("summary") or {}
        return int(summary.get(key) or 0)

    def _protected(state, key):
        ci = state.get("content_integrity") or {}
        structure = ci.get("structure_changes") or {}
        if not structure:
            return 0
        before = structure.get("before") or {}
        return int(before.get(key) or 0)

    task = state_before.get("task") or {}
    target = task.get("target_pages")

    return RoundEvidence(
        page_count_before=_pages(state_before, rule_report_before),
        page_count_after=_pages(state_after, rule_report_after),
        target_pages=int(target) if target else None,
        overfull_before=_overfull(rule_report_before),
        overfull_after=_overfull(rule_report_after),
        figures_before=_protected(state_before, "figure_count"),
        figures_after=_protected(state_after, "figure_count"),
        tables_before=_protected(state_before, "table_count"),
        tables_after=_protected(state_after, "table_count"),
        labels_before=_protected(state_before, "table_labels") if False else 0,  # TODO
        labels_after=0,
        undefined_refs_before=_undef(rule_report_before, "undefined_refs_count"),
        undefined_refs_after=_undef(rule_report_after, "undefined_refs_count"),
        undefined_citations_before=_undef(rule_report_before, "undefined_citations_count"),
        undefined_citations_after=_undef(rule_report_after, "undefined_citations_count"),
    )
