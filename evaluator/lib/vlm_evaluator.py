from __future__ import annotations

import base64
import io
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Protocol, Sequence

from PIL import Image, ImageDraw


class VisionJudgeClient(Protocol):
    def analyze(
        self,
        *,
        images: Sequence[Image.Image],
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[str, Dict[str, Any]]:
        raise NotImplementedError


@dataclass
class VLMAbsoluteConfig:
    api_url: str
    api_key: str
    model_name: str
    timeout_sec: int = 240
    protocol_version: str = "v3"
    mode: str = "stitched"
    max_image_dim: int = 1800
    jpeg_quality: int = 78
    max_retries: int = 2
    verify_ssl: bool = True
    swap_pair_order: bool = False

    @classmethod
    def from_env(cls) -> "VLMAbsoluteConfig":
        api_url = os.getenv("BASELINE_VLM_API_URL") or os.getenv("BASE_URL")
        api_key = os.getenv("BASELINE_VLM_API_KEY") or os.getenv("API_KEY")
        model_name = os.getenv("BASELINE_VLM_MODEL_NAME") or os.getenv("MODEL")
        if not api_url or not api_key or not model_name:
            raise ValueError(
                "Missing VLM API configuration. Provide api_url/api_key/model_name "
                "or set BASELINE_VLM_API_URL, BASELINE_VLM_API_KEY, BASELINE_VLM_MODEL_NAME."
            )
        return cls(
            api_url=api_url,
            api_key=api_key,
            model_name=model_name,
            verify_ssl=_parse_bool(os.getenv("BASELINE_VLM_VERIFY_SSL") or os.getenv("VLM_VERIFY_SSL"), default=True),
        )

    def public_metadata(self) -> Dict[str, Any]:
        return {
            "provider": "openai_compatible",
            "api_url": self.api_url,
            "model_name": self.model_name,
            "timeout_sec": self.timeout_sec,
            "protocol_version": "v3",
            "mode": self.mode,
            "max_image_dim": self.max_image_dim,
            "jpeg_quality": self.jpeg_quality,
            "max_retries": self.max_retries,
            "verify_ssl": self.verify_ssl,
        }


class OpenAICompatibleVisionJudgeClient:
    def __init__(self, config: VLMAbsoluteConfig):
        self.config = config

    def analyze(
        self,
        *,
        images: Sequence[Image.Image],
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[str, Dict[str, Any]]:
        try:
            import httpx
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("openai and httpx are required for VLM evaluation") from exc

        base_url = self.config.api_url.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url = base_url + "/v1"
        client = OpenAI(
            api_key=self.config.api_key,
            base_url=base_url,
            http_client=httpx.Client(
                trust_env=False,
                timeout=float(self.config.timeout_sec),
                verify=bool(self.config.verify_ssl),
            ),
        )
        content: List[Dict[str, Any]] = [{"type": "text", "text": user_prompt}]
        for image in images:
            content.append({"type": "image_url", "image_url": {"url": _image_to_data_url(image, config=self.config)}})

        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                response = client.chat.completions.create(
                    model=self.config.model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": content},
                    ],
                    temperature=0.0,
                    timeout=float(self.config.timeout_sec),
                )
                return _normalize_chat_completion_response(response)
            except Exception as exc:  # pragma: no cover
                last_error = exc
                if attempt < self.config.max_retries:
                    time.sleep(2)
        assert last_error is not None
        raise last_error


def stitch_images(images: Sequence[Image.Image], *, max_dim: int = 1800, gap: int = 4) -> Image.Image:
    if len(images) == 1:
        return images[0].convert("RGB")
    rows, cols = _calc_grid(len(images))
    images_rgb = [image.convert("RGB") for image in images]
    cell_w = max(image.size[0] for image in images_rgb)
    cell_h = max(image.size[1] for image in images_rgb)
    total_w = cols * cell_w + (cols - 1) * gap
    total_h = rows * cell_h + (rows - 1) * gap
    if max(total_w, total_h) > max_dim:
        scale = max_dim / max(total_w, total_h)
        cell_w = max(int(cell_w * scale), 1)
        cell_h = max(int(cell_h * scale), 1)
        total_w = cols * cell_w + (cols - 1) * gap
        total_h = rows * cell_h + (rows - 1) * gap
    resized = [image.resize((cell_w, cell_h), Image.LANCZOS) for image in images_rgb]
    canvas = Image.new("RGB", (total_w, total_h), "white")
    draw = ImageDraw.Draw(canvas)
    for idx, image in enumerate(resized):
        row = idx // cols
        col = idx % cols
        canvas.paste(image, (col * (cell_w + gap), row * (cell_h + gap)))
    for row in range(rows + 1):
        y = row * (cell_h + gap) - gap // 2
        draw.line([(0, y), (total_w, y)], fill="gray", width=gap)
    for col in range(cols + 1):
        x = col * (cell_w + gap) - gap // 2
        draw.line([(x, 0), (x, total_h)], fill="gray", width=gap)
    return canvas


def _calc_grid(n: int) -> tuple[int, int]:
    import math

    cols = int(math.ceil(math.sqrt(n)))
    while cols > 0:
        rows = math.ceil(n / cols)
        if cols <= rows * 1.5 and rows <= cols * 1.5:
            break
        cols -= 1
    return rows, cols


def _extract_json_object(raw_text: str) -> Dict[str, Any]:
    text = (raw_text or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", text)
    if fenced:
        try:
            payload = json.loads(fenced.group(1))
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            payload = json.loads(text[start : end + 1])
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _image_to_data_url(image: Image.Image, *, config: VLMAbsoluteConfig) -> str:
    buffer = io.BytesIO()
    normalized = image.convert("RGB")
    width, height = normalized.size
    if max(width, height) > config.max_image_dim:
        scale = config.max_image_dim / max(width, height)
        normalized = normalized.resize((max(int(width * scale), 1), max(int(height * scale), 1)), Image.LANCZOS)
    normalized.save(buffer, format="JPEG", quality=config.jpeg_quality)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _normalize_chat_completion_response(response: Any) -> tuple[str, Dict[str, Any]]:
    choices = getattr(response, "choices", None)
    if isinstance(choices, list) and choices:
        first_choice = choices[0]
        message = getattr(first_choice, "message", None)
        raw_content = getattr(message, "content", None)
        return _message_content_to_text(raw_content), _usage_to_dict(getattr(response, "usage", None))
    if isinstance(response, dict):
        usage = _usage_to_dict(response.get("usage"))
        choices_obj = response.get("choices")
        if isinstance(choices_obj, list) and choices_obj:
            first_choice = choices_obj[0]
            if isinstance(first_choice, dict):
                message = first_choice.get("message")
                if isinstance(message, dict):
                    return _message_content_to_text(message.get("content")), usage
        return str(response.get("output_text") or response), usage
    return _message_content_to_text(response), _usage_to_dict(None)


def _message_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: List[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    chunks.append(text)
        return "\n".join(chunks)
    return str(content or "")


def _usage_to_dict(usage: Any) -> Dict[str, Any]:
    return {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
    }


def _parse_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"invalid boolean value: {value!r}")
