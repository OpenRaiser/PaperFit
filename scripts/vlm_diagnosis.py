#!/usr/bin/env python3
"""VLM 在环诊断（PaperFit 论文核心设计）：渲染页图 → VLM → 13 类缺陷记录。

论文 Sense 阶段的 page-image 证据层由 VLM 承担：CV 启发式（pymupdf）只能测
几何量（空白比、图宽比），看不出"浮动体堆叠观感""表格被压扁""跨页失衡"这类
二维版式缺陷。本模块把高分辨率页面分批发给 VLM，按论文 13 类扰动对应的缺陷词表
分类，输出与 CV 检测器同 schema 的 findings（source=vlm_detector），下游
visual_action_plan → repair_plan 提升 → executor 的既有机制自动消费。

对象级缺陷（B2/C1/C2）要求 VLM 报 caption_number（"Figure 3"/"Table 2"），
锚点解析用它映射 crossrefs 的 label → 源码行——页面层发现、源码层定位。

接受 PAPERFIT_VLM_*、已有 BASELINE_VLM_* 或 DEEPSEEK_API_KEY。
未配置时跳过；失败或覆盖不完整时绝不设置 clean=True，不能视为无缺陷。
"""

from __future__ import annotations

import base64
import io
import json
import math
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener, urlopen

REPO_ROOT = Path(__file__).resolve().parent.parent
for _entry in (str(REPO_ROOT / "evaluator"), str(REPO_ROOT / "evaluator" / "lib")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

VLM_DIAGNOSIS_SCHEMA_VERSION = "1.1"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_VISION_MODEL = "deepseek-flash"

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
    page_numbers: Optional[Sequence[int]] = None,
    image_views: Optional[Sequence[Dict[str, Any]]] = None,
    total_page_count: Optional[int] = None,
) -> str:
    vocab_lines = "\n".join(f'  "{code}": {desc}' for code, desc in DEFECT_VOCABULARY.items())
    numbers = list(page_numbers) if page_numbers is not None else list(range(1, page_count + 1))
    mapping = "\n".join(
        f"- Image {i}: physical PDF page {view['page']}, view={view['view']}"
        for i, view in enumerate(image_views or [{"page": p, "view": "full page"} for p in numbers], 1)
    )
    return f"""Inspect these {len(numbers)} rendered pages from a document with {total_page_count or page_count} available pages.
These are separate images, not a montage. Image order and physical PDF page mapping:
{mapping}
Allowed page values for this batch: {json.dumps(numbers)}.

Context: template={template_name}, column_type={column_type or 'unknown'}.

Report every VISIBLE defect matching this vocabulary:
{vocab_lines}

Rules:
- Only report defects you can SEE in the pages. Do not guess from context.
- page MUST be the physical PDF page in the mapping, never an image index or the printed footer number. Crops are views of the same page, not extra pages.
- Use full-page images to judge margins, column balance and whitespace. Crops help read captions and small text; a crop edge is NOT a document margin or evidence of clipping.
- Do not report ordinary title/abstract spacing, intentional final-page whitespace after references, naturally narrow figures, or a short table as defects without clear visible damage.
- Unusual variable names and multiline equation labels are not layout defects by themselves. Never assess whether a formula is valid science. For D1/D2, identify a visibly crossed document margin or clipped glyphs; equation text that wraps inside the available width is not overflow.
- For cross-page defects, only judge boundaries between consecutive physical pages present in this batch. Do not infer float drift without visible contextual evidence.
- severity: "blocking" (makes the document unusable/unpublishable), "degrading" (clearly wrong but readable), "cosmetic" (minor polish).
- For B2/C1/C2 defects you MUST include the object's caption number exactly as printed (e.g. "Figure 3" -> caption_number 3, "Table 2" -> caption_number 2). Omit caption_number only if no number is printed.
- For B2 also include width_subtype: "overflow_width" if visibly crossing the available margin, or "underfilled_width" if clearly too small for its intended column. Omit this field when the kind of width damage is uncertain; do not infer it from a crop boundary.
- confidence: 0.0-1.0 how sure you are the defect exists.
- evidence: one short sentence quoting/describing what you see.

Respond with exactly this JSON shape:
{{"defects": [{{"code": "B2", "page": 5, "severity": "degrading", "caption_number": 3, "confidence": 0.85, "evidence": "Figure 3 extends past the right column boundary by roughly 15 percent."}}]}}
If no defects are visible, respond {{"defects": []}}."""


def vlm_diagnosis_configured() -> bool:
    return _resolve_vlm_config() is not None


def _resolve_vlm_config() -> Optional[Dict[str, Any]]:
    """Keep legacy credentials paired with their endpoint; never log keys."""
    paperfit_key = os.environ.get("PAPERFIT_VLM_API_KEY", "").strip()
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    paperfit_url = (os.environ.get("PAPERFIT_VLM_API_URL") or os.environ.get("PAPERFIT_VLM_BASE_URL") or "").strip()
    paperfit_model = (os.environ.get("PAPERFIT_VLM_MODEL") or os.environ.get("PAPERFIT_VLM_MODEL_NAME") or "").strip()
    legacy = [os.environ.get(name, "").strip() for name in (
        "BASELINE_VLM_API_KEY", "BASELINE_VLM_API_URL", "BASELINE_VLM_MODEL_NAME"
    )]
    if paperfit_key or ((paperfit_url or paperfit_model) and (deepseek_key or all(legacy))):
        # An explicitly overridden endpoint/model can reuse the configured key.
        key = paperfit_key or (legacy[0] if all(legacy) else deepseek_key)
        base_url = paperfit_url or (legacy[1] if all(legacy) and not paperfit_key else DEEPSEEK_BASE_URL)
        model = paperfit_model or (legacy[2] if all(legacy) and not paperfit_key else DEEPSEEK_VISION_MODEL)
    elif all(legacy):
        key, base_url, model = legacy
    elif deepseek_key:
        key, base_url, model = deepseek_key, DEEPSEEK_BASE_URL, DEEPSEEK_VISION_MODEL
    else:
        return None
    return {"api_key": key, "api_url": base_url, "model_name": model,
            "legacy_endpoint": bool(all(legacy) and not paperfit_key and not paperfit_url)}


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: int = 8192) -> int:
    raw = os.environ.get(name)
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


class VLMResponseError(ValueError):
    """A response is unusable as layout evidence, rather than defect-free."""


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
        raw_confidence = defect.get("confidence")
        confidence = max(0.0, min(1.0, float(raw_confidence if raw_confidence is not None else 0.5)))
    except (TypeError, ValueError):
        confidence = 0.5
    metrics: Dict[str, Any] = {"defect_type": code.lower()}
    if code in {"B2", "E1"}:
        metrics["object_kind"] = "figure"
    elif code in {"C1", "C2"}:
        metrics["object_kind"] = "table"
    width_subtype = defect.get("width_subtype")
    if code == "B2" and width_subtype in {"overflow_width", "underfilled_width"}:
        metrics["subtype"] = width_subtype
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


def parse_vlm_defects(
    text: str, *, strict: bool = False, page_numbers: Optional[Sequence[int]] = None
) -> List[Dict[str, Any]]:
    """The runtime uses strict validation; permissive parsing keeps the old API."""
    if strict:
        candidate = text.strip() if isinstance(text, str) else ""
        fenced = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", candidate, re.DOTALL)
        if fenced:
            candidate = fenced[1]
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise VLMResponseError("response contains duplicate JSON fields")
                result[key] = value
            return result

        try:
            parsed = json.loads(candidate, object_pairs_hook=unique_object)
        except (json.JSONDecodeError, TypeError):
            raise VLMResponseError("response must contain only a JSON object") from None
        if not isinstance(parsed, dict):
            raise VLMResponseError("response must be a JSON object")
        if set(parsed) != {"defects"}:
            raise VLMResponseError("response must contain only the defects array, without error or status fields")
    else:
        parsed = _extract_json_object(text)
    if not parsed or "defects" not in parsed or not isinstance(parsed["defects"], list):
        if strict:
            raise VLMResponseError("response must be a JSON object with a defects array")
        return []
    findings: List[Dict[str, Any]] = []
    allowed_pages = set(page_numbers) if page_numbers is not None else None
    for i, defect in enumerate(parsed["defects"], start=1):
        if strict:
            if not isinstance(defect, dict):
                raise VLMResponseError(f"defect {i} must be an object")
            code = defect.get("code")
            page = defect.get("page")
            confidence = defect.get("confidence")
            evidence = defect.get("evidence")
            if not isinstance(code, str) or code.strip().upper() not in DEFECT_VOCABULARY:
                raise VLMResponseError(f"defect {i} has an unsupported code")
            if type(page) is not int or page <= 0 or (allowed_pages is not None and page not in allowed_pages):
                raise VLMResponseError(f"defect {i} refers to a page outside this batch")
            severity = defect.get("severity")
            if not isinstance(severity, str) or severity.strip().lower() not in SEVERITY_MAP:
                raise VLMResponseError(f"defect {i} has an invalid severity")
            if type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
                raise VLMResponseError(f"defect {i} has an invalid confidence")
            if not isinstance(evidence, str) or not evidence.strip():
                raise VLMResponseError(f"defect {i} has no visible evidence")
            caption_number = defect.get("caption_number")
            if caption_number is not None and (type(caption_number) is not int or caption_number <= 0):
                raise VLMResponseError(f"defect {i} has an invalid caption number")
            if "width_subtype" in defect and (
                not isinstance(defect["width_subtype"], str)
                or defect["width_subtype"] not in {"overflow_width", "underfilled_width"}
            ):
                raise VLMResponseError(f"defect {i} has an invalid width subtype")
        if isinstance(defect, dict):
            finding = _to_finding(i, defect)
            if finding is not None:
                findings.append(finding)
    return findings


class _OpenAICompatibleDiagnosisClient:
    """Small standard-library transport; no OpenAI SDK installation required."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.timeout = _env_int("PAPERFIT_VLM_TIMEOUT_SEC", 240, maximum=1800)

    def analyze(self, *, images, system_prompt, user_prompt):
        base_url = self.config["api_url"].rstrip("/")
        # The previous evaluator added /v1 to legacy BASELINE base URLs.
        # Explicit PAPERFIT URLs and the official DeepSeek default are used as-is.
        if self.config.get("legacy_endpoint") and not base_url.endswith(("/v1", "/chat/completions")):
            base_url += "/v1"
        parsed_url = urlsplit(base_url)
        if parsed_url.scheme not in {"https", "http"} or not parsed_url.netloc or parsed_url.username or parsed_url.password or parsed_url.query or parsed_url.fragment:
            raise ValueError("VLM API URL must be an HTTP(S) endpoint without credentials or query parameters")
        endpoint = base_url if base_url.endswith("/chat/completions") else base_url + "/chat/completions"
        content: List[Dict[str, Any]] = [{"type": "text", "text": user_prompt}]
        for image in images:
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="PNG")
            data = buffer.getvalue()
            if len(data) > 32 * 1024 * 1024:
                raise ValueError("VLM image exceeds the 32 MiB inline image limit")
            content.append({"type": "image_url", "image_url": {
                "url": "data:image/png;base64," + base64.b64encode(data).decode("ascii"),
                "detail": "high",
            }})
        payload: Dict[str, Any] = {
            "model": self.config["model_name"],
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": content}],
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "max_tokens": _env_int("PAPERFIT_VLM_MAX_TOKENS", 4096, maximum=393216),
        }
        if self.config["model_name"].startswith("deepseek-"):
            payload["thinking"] = {"type": "disabled"}
        body = json.dumps(payload).encode("utf-8")
        if len(body) > 48 * 1024 * 1024:
            raise ValueError("VLM request exceeds the 48 MiB inline body limit; reduce pages per batch")
        def _build_request() -> Request:
            return Request(endpoint, data=body, headers={
                "Authorization": "Bearer " + self.config["api_key"], "Content-Type": "application/json",
            }, method="POST")

        try:
            try:
                with urlopen(_build_request(), timeout=self.timeout) as response:
                    result = json.load(response)
            except URLError as url_exc:
                # HTTPError 是 URLError 的子类：HTTP 层错误（4xx/5xx）说明请求
                # 已到达服务端，不属于"连不上"，直连重试没有意义，直接上抛。
                if isinstance(url_exc, HTTPError):
                    raise
                # Windows 上 urlopen 会读注册表里的 WinINET 系统代理；代理已停用
                # 时表现为本地 10061 拒绝连接。退回无代理直连重试一次——工作代理
                # 仍被优先尊重。注意：代理尝试会 set_proxy 原地改写 Request（host
                # 被指向代理），直连必须重建请求对象，否则仍会连到本地代理端口。
                direct_opener = build_opener(ProxyHandler({}))
                with direct_opener.open(_build_request(), timeout=self.timeout) as response:
                    result = json.load(response)
        except HTTPError as exc:
            # Never include remote bodies, headers, request images or keys in logs.
            raise RuntimeError(f"VLM API returned HTTP {exc.code}") from None
        except URLError:
            raise RuntimeError("VLM API network connection failed") from None
        if not isinstance(result, dict) or not isinstance(result.get("choices"), list) or not result["choices"]:
            raise VLMResponseError("VLM API response has no completion choices")
        choice = result["choices"][0]
        if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
            raise VLMResponseError("VLM API response has no message")
        if choice.get("finish_reason") != "stop":
            raise VLMResponseError("VLM API completion is missing a normal stop reason")
        message = choice["message"]
        if message.get("refusal"):
            raise VLMResponseError("VLM API refused the diagnosis request")
        text = message.get("content")
        if not isinstance(text, str) or not text.strip():
            raise VLMResponseError("VLM API returned empty diagnosis content")
        return text, {"model": result.get("model"), "finish_reason": choice.get("finish_reason"), "usage": result.get("usage")}


def _default_client():
    config = _resolve_vlm_config()
    if config is None:
        raise ValueError("VLM API is not configured")
    return _OpenAICompatibleDiagnosisClient(config)


def _load_page_records(pages_dir: Path) -> List[Tuple[int, Path]]:
    records: Dict[int, Path] = {}
    for path in pages_dir.glob("page_*.png"):
        match = re.fullmatch(r"page_(\d+)", path.stem)
        if not match or int(match[1]) <= 0:
            raise ValueError("rendered page filenames must use positive physical page numbers: page_001.png")
        number = int(match[1])
        if number in records:
            raise ValueError(f"duplicate rendered page number {number}")
        records[number] = path
    return sorted(records.items())


def _batch_images(records: Sequence[Tuple[int, Path]], *, max_image_dim: int, detail_crops: bool):
    from PIL import Image  # noqa: PLC0415

    images, views = [], []
    for number, path in records:
        with Image.open(path) as opened:
            image = opened.convert("RGB")
        page_views = [("full page", image)]
        if detail_crops:
            # Overlap keeps central equations and captions legible. The full page
            # remains the authority for document margins and width anomalies.
            width, height = image.size
            page_views.extend([
                ("left-column detail crop", image.crop((0, 0, int(width * 0.56), height))),
                ("right-column detail crop", image.crop((int(width * 0.44), 0, width, height))),
            ])
        for view, candidate in page_views:
            candidate.thumbnail((max_image_dim, max_image_dim), getattr(Image, "Resampling", Image).LANCZOS)
            images.append(candidate)
            views.append({"page": number, "view": view, "width": candidate.width, "height": candidate.height})
    return images, views


def _finding_key(finding: Dict[str, Any]) -> Tuple[str, int, str, str, str]:
    metrics = finding.get("metrics") or {}
    return (str(finding.get("taxonomy_defect_id") or ""), int(finding.get("page") or 0),
            str(metrics.get("caption_number") or ""),
            str(metrics.get("object_kind") or ""),
            str(metrics.get("subtype") or ""))


def _safe_error(exc: Exception) -> str:
    message = str(exc)
    for name in ("PAPERFIT_VLM_API_KEY", "BASELINE_VLM_API_KEY", "DEEPSEEK_API_KEY"):
        value = os.environ.get(name)
        if value:
            message = message.replace(value, "<redacted>")
    message = re.sub(r"data:image/[^;]+;base64,[A-Za-z0-9+/=]+", "<redacted image>", message)
    message = re.sub(r"(?:sk-|github_pat_)[A-Za-z0-9_-]+", "<redacted>", message)
    return f"{type(exc).__name__}: {message[:400]}"


def run_vlm_diagnosis(
    pages_dir: Path,
    *,
    template_name: str = "unknown",
    column_type: Optional[str] = None,
    client: Optional[Any] = None,
    max_image_dim: Optional[int] = None,
    pages_per_batch: Optional[int] = None,
    batch_overlap: Optional[int] = None,
    max_batches: Optional[int] = None,
    page_numbers: Optional[Sequence[int]] = None,
    detail_crops: Optional[bool] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Return findings and explicit completion/coverage state, preserving tuple API.

    A budget or page selection may leave coverage incomplete. Only a fully
    completed, validated inspection can set clean=True. Batches overlap by one
    page by default so boundary defects retain adjacent-page context.
    """
    report: Dict[str, Any] = {
        "schema_version": VLM_DIAGNOSIS_SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(),
        "pages_dir": str(pages_dir),
        "configured": vlm_diagnosis_configured() or client is not None,
        "findings_count": 0,
        "status": "skipped",
        "success": None,
        "clean": None,
        "pages_analyzed": 0,
        "coverage": 0.0,
        "batches": [],
        "error": None,
    }
    if client is None and not vlm_diagnosis_configured():
        report["configured"] = False
        return [], report

    try:
        image_dim = max_image_dim if max_image_dim is not None else _env_int("PAPERFIT_VLM_MAX_IMAGE_DIM", 2400)
        batch_size = pages_per_batch if pages_per_batch is not None else _env_int("PAPERFIT_VLM_PAGES_PER_BATCH", 2, maximum=32)
        overlap = batch_overlap if batch_overlap is not None else _env_int("PAPERFIT_VLM_BATCH_OVERLAP", min(1, batch_size - 1), minimum=0, maximum=31)
        batch_limit = max_batches if max_batches is not None else _env_int("PAPERFIT_VLM_MAX_BATCHES", 0, minimum=0, maximum=10000)
        if type(image_dim) is not int or not 1 <= image_dim <= 8192:
            raise ValueError("max_image_dim must be an integer between 1 and 8192")
        if type(batch_size) is not int or not 1 <= batch_size <= 32:
            raise ValueError("pages_per_batch must be an integer between 1 and 32")
        if type(overlap) is not int or not 0 <= overlap < batch_size:
            raise ValueError("batch_overlap must be nonnegative and smaller than pages_per_batch")
        if type(batch_limit) is not int or batch_limit < 0:
            raise ValueError("max_batches must be a nonnegative integer (0 means unlimited)")
        crops = detail_crops
        if crops is None:
            raw_crops = os.environ.get("PAPERFIT_VLM_DETAIL_CROPS")
            if raw_crops is not None and raw_crops.lower() not in {"true", "false", "1", "0", "yes", "no"}:
                raise ValueError("PAPERFIT_VLM_DETAIL_CROPS must be true or false")
            crops = raw_crops.lower() in {"true", "1", "yes"} if raw_crops is not None else column_type == "double"
        records = _load_page_records(Path(pages_dir))
        if not records:
            raise ValueError("no rendered pages found")
        available = [number for number, _ in records]
        # Gaps in a render directory also prevent an all-document clean claim.
        expected = set(range(1, max(available) + 1))
        if page_numbers is not None:
            selected = set(page_numbers)
            if any(type(p) is not int or p <= 0 for p in page_numbers) or not selected or not selected <= set(available):
                raise ValueError("page_numbers must select existing positive physical page numbers")
            records = [(p, path) for p, path in records if p in selected]
        report.update({"page_count": len(expected), "available_page_numbers": available,
                       "max_image_dim": image_dim, "pages_per_batch": batch_size,
                       "batch_overlap": overlap, "detail_crops": crops})
        active_client = client or _default_client()
        findings, seen, analyzed, errors = [], set(), set(), []
        starts = range(0, len(records), batch_size - overlap)
        for start in starts:
            if start and start + overlap >= len(records):
                break  # avoid a redundant final batch containing only overlap
            if batch_limit and len(report["batches"]) >= batch_limit:
                break
            batch = records[start:start + batch_size]
            numbers = [number for number, _ in batch]
            batch_report: Dict[str, Any] = {"page_numbers": numbers, "status": "failed", "error": None}
            report["batches"].append(batch_report)
            images = []
            try:
                images, views = _batch_images(batch, max_image_dim=image_dim, detail_crops=bool(crops))
                batch_report["image_views"] = views
                text, meta = active_client.analyze(images=images, system_prompt=SYSTEM_PROMPT,
                    user_prompt=build_user_prompt(template_name=template_name, column_type=column_type,
                        page_count=len(batch), page_numbers=numbers, image_views=views, total_page_count=len(expected)))
                meta = meta if isinstance(meta, dict) else {}
                if meta.get("finish_reason") not in (None, "stop"):
                    raise VLMResponseError("completion did not finish normally: " + str(meta["finish_reason"]))
                batch_findings = parse_vlm_defects(text, strict=True, page_numbers=numbers)
                batch_report.update({"status": "completed", "raw_response_chars": len(text),
                                     "response_model": meta.get("model"), "findings_count": len(batch_findings)})
                usage = meta.get("usage")
                if isinstance(usage, dict):
                    batch_report["usage"] = {
                        key: value for key, value in usage.items()
                        if key in {"prompt_tokens", "completion_tokens", "total_tokens",
                                   "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"}
                        and type(value) is int and value >= 0
                    }
                report["response_model"] = meta.get("model")
                report["raw_response_chars"] = report.get("raw_response_chars", 0) + len(text)
                analyzed.update(numbers)
                for finding in batch_findings:
                    key = _finding_key(finding)
                    if key not in seen:
                        seen.add(key)
                        finding["defect_id"] = f"vf-vlm-{len(findings) + 1:03d}"
                        findings.append(finding)
            except Exception as exc:
                batch_report["error"] = _safe_error(exc)
                errors.append(batch_report["error"])
            finally:
                for image in images:
                    image.close()
        missing = sorted(expected - analyzed)
        complete = not errors and not missing
        report.update({"pages_analyzed": len(analyzed), "analyzed_page_numbers": sorted(analyzed),
                       "missing_page_numbers": missing, "coverage": round(len(analyzed) / len(expected), 4),
                       "findings_count": len(findings), "success": complete,
                       "status": "completed" if complete else "partial" if analyzed else "failed",
                       "clean": not findings if complete else False if findings else None,
                       "error": "; ".join(errors)[:1000] if errors else None})
        return findings, report
    except Exception as exc:  # VLM 失败不能阻断诊断管线：CV findings 照常工作
        report.update({"status": "failed", "success": False, "error": _safe_error(exc)})
        return [], report


def merge_vlm_findings(visual_report_path: Path, findings: List[Dict[str, Any]], diag_report: Optional[Dict[str, Any]] = None) -> int:
    """把 VLM findings 并入 visual_signal_report.json（去重后追加），返回并入数量。"""
    path = Path(visual_report_path)
    if (not findings and diag_report is None) or not path.is_file():
        return 0
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0

    existing = report.get("findings") or []
    seen = {_finding_key(f) for f in existing}
    added = 0
    added_findings: List[Dict[str, Any]] = []
    for f in findings:
        key = _finding_key(f)
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
    if added:
        sources = report.setdefault("machine_sources", {})
        if isinstance(sources, dict):
            sources["vlm_detector"] = {
                "findings_added": added,
                "status": (diag_report or {}).get("status"),
                "coverage": (diag_report or {}).get("coverage"),
            }
        elif isinstance(sources, list):
            if "vlm_detector" not in sources:
                sources.append("vlm_detector")
        else:
            raise ValueError("machine_sources must be a mapping or legacy list")
    if diag_report is not None or added:
        report["vlm_diagnosis"] = diag_report if diag_report is not None else {"findings_count": added}
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return added
