#!/usr/bin/env python3
"""VLM 在环诊断（PaperFit 论文核心设计）：渲染页图 → VLM → 13 类缺陷记录。

论文 Sense 阶段的 page-image 证据层由 VLM 承担：CV 启发式（pymupdf）只能测
几何量（空白比、图宽比），看不出"浮动体堆叠观感""表格被压扁""跨页失衡"这类
二维版式缺陷。本模块把渲染页拼图发给 VLM，按论文 13 类扰动对应的缺陷词表
分类，输出与 CV 检测器同 schema 的 findings（source=vlm_detector），下游
visual_action_plan → repair_plan 提升 → executor 的既有机制自动消费。

对象级缺陷（B2/C1/C2）要求 VLM 报 caption_number（"Figure 3"/"Table 2"），
锚点解析用它映射 crossrefs 的 label → 源码行——页面层发现、源码层定位。

未配置 BASELINE_VLM_* 时安静跳过（返回空列表），CV 检测不受影响。
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
for _entry in (str(REPO_ROOT / "evaluator"), str(REPO_ROOT / "evaluator" / "lib")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

VLM_DIAGNOSIS_SCHEMA_VERSION = "1.0"

# 论文 13 类扰动对应的可见缺陷词表（视觉可判定子集；E 类迁移缺陷同样可见）
DEFECT_VOCABULARY: Dict[str, str] = {
    "A1": "widow/orphan lines: a paragraph's first or last line stranded alone at a page/column boundary",
    "A2": "excessive trailing whitespace: large empty area at the end of the document before the bibliography",
    "A4": "column imbalance: on double-column final pages, one column ends far earlier than the other",
    "A5": "column void: a large vertical gap in the middle of a body-text column",
    "B1": "float drift/stacking: figures or tables clustered on dedicated float-only pages, or landing far from their first mention",
    "B2": "graphics width anomaly: a figure visibly overflowing the column/text boundary, or shrunk noticeably smaller than its column",
    "C1": "undersized table: table text visibly shrunken/condensed relative to body text (over-scaled down)",
    "C2": "oversized table: table extends beyond the column or text width",
    "D1": "text overflow: a text line protruding past the right margin",
    "D2": "formula overflow: a display equation extending past the margin or clipped",
    "E1": "post-migration width mismatch: images with widths inconsistent with the current template's geometry",
    "E2": "post-migration height squeeze: abnormal text height / cramped pages from reduced textheight",
}

# 视觉词表 → action plan 家族（A3/A6/B3/B4/B5/C3/C4/D3 无独立视觉判定形态，不请求）
SEVERITY_MAP = {"blocking": "critical", "degrading": "major", "cosmetic": "minor"}

SYSTEM_PROMPT = (
    "You are a meticulous LaTeX typesetting inspector. You examine rendered pages of a scientific "
    "paper and report VISIBLE layout defects only. You must not judge scientific content, writing "
    "quality, or aesthetics of ideas. Respond with a single JSON object and nothing else."
)


def build_user_prompt(
    *,
    template_name: str,
    column_type: Optional[str],
    page_count: int,
) -> str:
    vocab_lines = "\n".join(f'  "{code}": {desc}' for code, desc in DEFECT_VOCABULARY.items())
    return f"""Inspect the montage of all {page_count} rendered pages (ordered left-to-right, top-to-bottom).

Context: template={template_name}, column_type={column_type or 'unknown'}.

Report every VISIBLE defect matching this vocabulary:
{vocab_lines}

Rules:
- Only report defects you can SEE in the pages. Do not guess from context.
- severity: "blocking" (makes the document unusable/unpublishable), "degrading" (clearly wrong but readable), "cosmetic" (minor polish).
- For B2/C1/C2 defects you MUST include the object's caption number exactly as printed (e.g. "Figure 3" -> caption_number 3, "Table 2" -> caption_number 2). Omit caption_number only if no number is printed.
- confidence: 0.0-1.0 how sure you are the defect exists.
- evidence: one short sentence quoting/describing what you see.

Respond with exactly this JSON shape:
{{"defects": [{{"code": "B2", "page": 5, "severity": "degrading", "caption_number": 3, "confidence": 0.85, "evidence": "Figure 3 extends past the right column boundary by roughly 15 percent."}}]}}
If no defects are visible, respond {{"defects": []}}."""


def vlm_diagnosis_configured() -> bool:
    return bool(
        os.environ.get("BASELINE_VLM_API_KEY")
        and os.environ.get("BASELINE_VLM_API_URL")
        and os.environ.get("BASELINE_VLM_MODEL_NAME")
    )


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        brace = re.search(r"\{.*\}", candidate, re.DOTALL)
        if brace:
            try:
                parsed = json.loads(brace.group(0))
                return parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                return None
    return None


def _to_finding(index: int, defect: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    code = str(defect.get("code") or "").strip().upper()
    if code not in DEFECT_VOCABULARY:
        return None
    try:
        page = int(defect.get("page") or 0)
    except (TypeError, ValueError):
        return None
    if page <= 0:
        return None
    severity = SEVERITY_MAP.get(str(defect.get("severity") or "").strip().lower(), "minor")
    try:
        confidence = max(0.0, min(1.0, float(defect.get("confidence") or 0.5)))
    except (TypeError, ValueError):
        confidence = 0.5
    metrics: Dict[str, Any] = {"defect_type": code.lower()}
    caption_number = defect.get("caption_number")
    if caption_number is not None:
        try:
            metrics["caption_number"] = int(caption_number)
        except (TypeError, ValueError):
            pass
    return {
        "defect_id": f"vf-vlm-{index:03d}",
        "taxonomy_defect_id": code,
        "severity": severity,
        "page": page,
        "confidence": round(confidence, 3),
        "description": str(defect.get("evidence") or DEFECT_VOCABULARY[code])[:300],
        "source": "vlm_detector",
        "metrics": metrics,
    }


def parse_vlm_defects(text: str) -> List[Dict[str, Any]]:
    parsed = _extract_json_object(text)
    if not parsed:
        return []
    findings: List[Dict[str, Any]] = []
    for i, defect in enumerate(parsed.get("defects") or [], start=1):
        if isinstance(defect, dict):
            finding = _to_finding(i, defect)
            if finding is not None:
                findings.append(finding)
    return findings


def _default_client():
    from vlm_evaluator import OpenAICompatibleVisionJudgeClient, VLMAbsoluteConfig  # noqa: PLC0415

    config = VLMAbsoluteConfig(
        api_url=os.environ["BASELINE_VLM_API_URL"],
        api_key=os.environ["BASELINE_VLM_API_KEY"],
        model_name=os.environ["BASELINE_VLM_MODEL_NAME"],
        timeout_sec=240,
        protocol_version="v3",
        mode="stitched",
    )
    return OpenAICompatibleVisionJudgeClient(config)


def _load_pages(pages_dir: Path) -> List[Any]:
    from PIL import Image  # noqa: PLC0415

    pages = sorted(pages_dir.glob("page_*.png"))
    return [Image.open(p).convert("RGB") for p in pages]


def run_vlm_diagnosis(
    pages_dir: Path,
    *,
    template_name: str = "unknown",
    column_type: Optional[str] = None,
    client: Optional[Any] = None,
    max_image_dim: int = 1800,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """返回 (findings, 诊断报告)。未配置 VLM 时返回空列表。"""
    report: Dict[str, Any] = {
        "schema_version": VLM_DIAGNOSIS_SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(),
        "pages_dir": str(pages_dir),
        "configured": vlm_diagnosis_configured() or client is not None,
        "findings_count": 0,
        "error": None,
    }
    if client is None and not vlm_diagnosis_configured():
        report["configured"] = False
        return [], report

    try:
        from vlm_evaluator import stitch_images  # noqa: PLC0415

        pages = _load_pages(Path(pages_dir))
        if not pages:
            report["error"] = "no rendered pages found"
            return [], report
        montage = stitch_images(pages, max_dim=max_image_dim)
        active_client = client or _default_client()
        user_prompt = build_user_prompt(
            template_name=template_name,
            column_type=column_type,
            page_count=len(pages),
        )
        text, meta = active_client.analyze(
            images=[montage],
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )
        findings = parse_vlm_defects(text)
        report.update(
            {
                "pages_analyzed": len(pages),
                "raw_response_chars": len(text or ""),
                "response_model": (meta or {}).get("model"),
                "findings_count": len(findings),
            }
        )
        
        
        
        
        
        if _detail_pass_enabled():
            detail_findings, detail_report = _run_detail_pass(
                pages,
                active_client,
                template_name=template_name,
                column_type=column_type,
            )
            findings = _merge_stage_findings(findings, detail_findings)
            report["detail_pass"] = detail_report
            report["findings_count"] = len(findings)
        return findings, report
    except Exception as exc:  # VLM 失败不能阻断诊断管线：CV findings 照常工作
        report["error"] = f"{type(exc).__name__}: {exc}"
        return [], report


_DETAIL_OBJECT_CODES = ("B2", "C1", "C2", "D1", "D2")


def _detail_pass_enabled() -> bool:
    return os.environ.get("PAPERFIT_VLM_DETAIL", "1").strip().lower() not in {"0", "false", "off", "no"}


def _detail_max_chunks() -> int:
    try:
        return max(1, int(os.environ.get("PAPERFIT_VLM_DETAIL_MAX_CHUNKS", "8")))
    except ValueError:
        return 8


def _build_detail_user_prompt(page_numbers: List[int]) -> str:
    vocab_lines = "\n".join(
        f'  "{code}": {DEFECT_VOCABULARY[code]}' for code in _DETAIL_OBJECT_CODES
    )
    pages_desc = ", ".join(str(n) for n in page_numbers)
    return f"""Examine these {len(page_numbers)} consecutive rendered pages at full resolution.
They are pages {pages_desc} of the paper (left to right).

Report every VISIBLE object-level defect matching this vocabulary:
{vocab_lines}

Rules:
- Only report defects you can SEE. Judge object geometry relative to the column/text margins on the same page.
- For B2/C1/C2 you MUST include the caption number exactly as printed ("Table 2" -> caption_number 2). Omit caption_number only if none is printed.
- "page" MUST be the global page number ({pages_desc}), not the position in this image.
- severity: "blocking" | "degrading" | "cosmetic". confidence: 0.0-1.0. evidence: one short sentence.

Respond with exactly this JSON shape:
{{"defects": [{{"code": "C2", "page": 5, "severity": "degrading", "caption_number": 2, "confidence": 0.85, "evidence": "Table 2 extends past the right column boundary."}}]}}
If none are visible, respond {{"defects": []}}."""


def _run_detail_pass(
    pages: List[Any],
    client: Any,
    *,
    template_name: str,
    column_type: Optional[str],
    chunk_size: int = 3,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    from vlm_evaluator import stitch_images  # noqa: PLC0415

    detail_report: Dict[str, Any] = {
        "enabled": True,
        "chunk_size": chunk_size,
        "chunks_requested": 0,
        "chunks_failed": 0,
        "findings": 0,
        "errors": [],
    }
    findings: List[Dict[str, Any]] = []
    chunks = [
        (start, pages[start : start + chunk_size])
        for start in range(0, len(pages), chunk_size)
    ][:_detail_max_chunks()]
    for start, chunk in chunks:
        page_numbers = [start + offset + 1 for offset in range(len(chunk))]
        try:
            montage = stitch_images(chunk, max_dim=2400)
            text, _meta = client.analyze(
                images=[montage],
                system_prompt=SYSTEM_PROMPT,
                user_prompt=_build_detail_user_prompt(page_numbers),
            )
            detail_report["chunks_requested"] += 1
            chunk_findings = parse_vlm_defects(text)
            # 细看组内页码校准：模型偶尔回报组内序号，映射回全局页码
            valid = set(page_numbers)
            for f in chunk_findings:
                if int(f.get("page") or 0) not in valid:
                    f["page"] = page_numbers[0]
            findings.extend(chunk_findings)
        except Exception as exc:  # 单组失败不中断其余组
            detail_report["chunks_failed"] += 1
            detail_report["errors"].append(f"{type(exc).__name__}: {exc}")
    detail_report["findings"] = len(findings)
    return findings, detail_report


def _merge_stage_findings(
    montage_findings: List[Dict[str, Any]], detail_findings: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """细看结果优先（分辨率高、证据准），拼图结果在同 code+page 上让位。"""
    detail_keys = set()
    for f in detail_findings:
        metrics = f.get("metrics") or {}
        detail_keys.add(
            (
                str(f.get("taxonomy_defect_id") or ""),
                int(f.get("page") or 0),
                str(metrics.get("caption_number") or ""),
            )
        )
    merged = list(detail_findings)
    for f in montage_findings:
        metrics = f.get("metrics") or {}
        key = (
            str(f.get("taxonomy_defect_id") or ""),
            int(f.get("page") or 0),
            str(metrics.get("caption_number") or ""),
        )
        if key not in detail_keys:
            merged.append(f)
    return merged


def merge_vlm_findings(visual_report_path: Path, findings: List[Dict[str, Any]], diag_report: Optional[Dict[str, Any]] = None) -> int:
    """把 VLM findings 并入 visual_signal_report.json（去重后追加），返回并入数量。"""
    path = Path(visual_report_path)
    if not findings or not path.is_file():
        return 0
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0

    existing = report.get("findings") or []
    seen = set()
    for f in existing:
        metrics = f.get("metrics") or {}
        seen.add(
            (
                str(f.get("taxonomy_defect_id") or ""),
                int(f.get("page") or 0),
                str(metrics.get("caption_number") or ""),
            )
        )
    added = 0
    added_findings: List[Dict[str, Any]] = []
    for f in findings:
        metrics = f.get("metrics") or {}
        key = (
            str(f.get("taxonomy_defect_id") or ""),
            int(f.get("page") or 0),
            str(metrics.get("caption_number") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        added_findings.append(f)
        existing.append(f)
        added += 1

    if added:
        report["findings"] = existing
        summary = report.get("summary") or {}
        summary["total_findings"] = int(summary.get("total_findings") or 0) + added
        breakdown = summary.setdefault("category_breakdown", {})
        for f in added_findings:
            letter = str(f.get("taxonomy_defect_id") or "")[:1]
            if letter:
                breakdown[letter] = int(breakdown.get(letter) or 0) + 1
        sources = report.setdefault("machine_sources", [])
        # aggregator 输出的 machine_sources 是 {source: metadata} dict（而非 list），
        # 此前无条件 .append 会抛 AttributeError——VLM 回写在真实管线上从未成功。
        if isinstance(sources, dict):
            sources.setdefault(
                "vlm_detector",
                {"findings_added": added, "detail_pass": bool((diag_report or {}).get("detail_pass"))},
            )
        elif "vlm_detector" not in sources:
            sources.append("vlm_detector")
        report["vlm_diagnosis"] = diag_report or {"findings_count": added}
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return added
