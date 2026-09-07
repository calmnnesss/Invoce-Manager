"""LLM-based invoice parsing service with multi-provider support."""

import base64
import json
import logging
import re
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

from app.config import get_settings

# Thread lock for singleton initialization
_llm_lock = threading.Lock()
from app.services.prompts import (
    INVOICE_VISION_PROMPT,
    INVOICE_VISION_SYSTEM_PROMPT,
    REQUIRED_FIELDS,
)

logger = logging.getLogger(__name__)
settings = get_settings()


def _model_matches_vision_pattern(model_name: str, vision_patterns: list[str]) -> bool:
    """Check if a model name matches any vision pattern.

    Uses exact match or prefix match with separator to avoid false positives.
    For example, "gpt-4o" matches "gpt-4o" and "gpt-4o-mini" but not "gpt-4o-audio".

    Args:
        model_name: The configured model name
        vision_patterns: List of known vision model patterns

    Returns:
        True if the model supports vision
    """
    for pattern in vision_patterns:
        # Exact match
        if model_name == pattern:
            return True
        # Prefix match: pattern must be followed by a separator (-) or end
        if model_name.startswith(pattern + "-") or model_name.startswith(pattern + "."):
            return True
    return False


def _model_uses_new_token_param(model_name: str) -> bool:
    """Check if a model uses max_completion_tokens instead of max_tokens.

    Newer OpenAI models (o1, o3, gpt-5.x) require max_completion_tokens parameter.

    Args:
        model_name: The model name

    Returns:
        True if the model uses max_completion_tokens
    """
    new_format_prefixes = ["o1", "o3", "gpt-5"]
    return any(model_name.startswith(prefix) for prefix in new_format_prefixes)


def _get_max_tokens_param(model_name: str, value: int) -> dict:
    """Get the correct max tokens parameter for a model.

    Args:
        model_name: The model name
        value: The max tokens value

    Returns:
        Dict with the appropriate parameter name and value
    """
    if _model_uses_new_token_param(model_name):
        return {"max_completion_tokens": value}
    return {"max_tokens": value}


# backend/ directory, used to resolve a relative llm_dump_dir
_BACKEND_ROOT = Path(__file__).resolve().parents[2]

_SEP = "=" * 78
_SUB = "-" * 78


@dataclass
class LLMCallContext:
    """Identifies the invoice being parsed, for log lines and dump files.

    Add fields here rather than threading more scalars through the call chain.
    """

    invoice_id: Optional[int] = None
    file_name: Optional[str] = None
    file_type: Optional[str] = None

    @property
    def label(self) -> str:
        """Compact one-line label for console log records."""
        invoice = self.invoice_id if self.invoice_id is not None else "?"
        return f"invoice={invoice} file={self.file_name or '?'}"

    @property
    def slug(self) -> str:
        """Filename-safe fragment identifying this call."""
        invoice = self.invoice_id if self.invoice_id is not None else "na"
        return f"invoice-{invoice}_{_safe_slug(self.file_name or 'unnamed')}"


def _safe_slug(name: str, limit: int = 40) -> str:
    """Reduce a user-supplied filename to something safe for a filename.

    CJK characters are alphanumeric to str.isalnum(), so Chinese names survive.
    """
    slug = "".join(c if (c.isalnum() or c in "-_.") else "-" for c in name)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-.")[:limit] or "unnamed"


def _resolve_dump_dir() -> Optional[Path]:
    """Resolve the configured dump directory, or None when disabled."""
    configured = (get_settings().llm_dump_dir or "").strip()
    if not configured:
        return None
    path = Path(configured)
    return path if path.is_absolute() else _BACKEND_ROOT / path


def _dump_llm_exchange(
    provider_name: str,
    ctx: Optional[LLMCallContext],
    raw_content: Optional[str],
    raw_fields: Optional[Dict[str, Any]] = None,
    cleaned_fields: Optional[Dict[str, Any]] = None,
    changes: Optional[list[str]] = None,
    extra_keys: Optional[list[str]] = None,
    error: Optional[str] = None,
) -> Optional[Path]:
    """Write one file per LLM call showing the response before/after cleaning.

    Never raises: dumping is a debugging aid and must not break parsing.
    """
    try:
        dump_dir = _resolve_dump_dir()
        if dump_dir is None:
            return None
        dump_dir.mkdir(parents=True, exist_ok=True)

        ctx = ctx or LLMCallContext()
        now = datetime.now()
        # Microseconds keep names unique across the LLM thread pool
        stamp = now.strftime("%Y%m%d-%H%M%S-%f")
        status = "ERROR" if error else "OK"
        file_path = dump_dir / f"{stamp}_{ctx.slug}_{provider_name}_{status}.log"

        model = getattr(get_settings(), f"{provider_name}_model", "") or "(unknown)"

        lines: list[str] = [
            _SEP,
            "LLM 响应清洗记录 (LLM response normalization record)",
            _SEP,
            f"时间       : {now.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}",
            f"发票 ID    : {ctx.invoice_id if ctx.invoice_id is not None else '(unknown)'}",
            f"文件名     : {ctx.file_name or '(unknown)'}",
            f"文件类型   : {ctx.file_type or '(unknown)'}",
            f"Provider   : {provider_name}",
            f"Model      : {model}",
        ]
        if error:
            lines.append(f"结果       : FAILED - {error}")
        else:
            populated = sum(1 for v in (cleaned_fields or {}).values() if v is not None)
            total = len(cleaned_fields or {})
            lines.append(
                f"结果       : OK  populated={populated}/{total}  "
                f"changed={len(changes or [])}"
            )
        lines.append("")

        lines += [_SUB, "[1] 原始响应 — provider 返回的原文，未做任何处理", _SUB]
        lines.append(raw_content if raw_content else "(no content)")
        lines.append("")

        if raw_fields is not None:
            lines += [_SUB, "[2] 结构清洗后 — 剥 markdown 围栏 + json.loads，值仍是原始值", _SUB]
            lines.append(json.dumps(raw_fields, ensure_ascii=False, indent=2))
            lines.append("")

        if cleaned_fields is not None:
            lines += [_SUB, "[3] 值清洗后 — _normalize_field_value 的输出，交给 OCR/LLM 仲裁", _SUB]
            lines.append(json.dumps(cleaned_fields, ensure_ascii=False, indent=2))
            lines.append("")

            lines += [_SUB, "[4] 逐字段差异 — 仅列出被清洗器改动过的字段", _SUB]
            if changes:
                lines += [f"  {c}" for c in changes]
            else:
                lines.append("  (无改动，模型输出已完全符合规范)")
            lines.append("")
            lines.append(
                f"被丢弃的多余键: {extra_keys}" if extra_keys else "被丢弃的多余键: (无)"
            )

        lines.append("")
        file_path.write_text("\n".join(lines), encoding="utf-8")
        return file_path
    except Exception as exc:  # pragma: no cover - never break parsing on a dump failure
        logger.warning(f"Failed to write LLM dump file: {exc}")
        return None


class BaseLLMProvider(ABC):
    """Base class for LLM providers."""

    @abstractmethod
    def is_configured(self) -> bool:
        """Check if provider is configured."""
        pass

    @abstractmethod
    def get_provider_name(self) -> str:
        """Get provider name."""
        pass

    @abstractmethod
    def chat_completion(self, system_prompt: str, user_prompt: str) -> str:
        """Send chat completion request and return response content."""
        pass

    def supports_vision(self) -> bool:
        """Check if provider supports vision/image input."""
        return False

    def vision_completion(self, system_prompt: str, user_prompt: str, image_data: bytes, mime_type: str = "image/png") -> str:
        """Send vision completion request with image and return response content.

        Args:
            system_prompt: System prompt for the model
            user_prompt: User prompt/instructions
            image_data: Raw image bytes
            mime_type: MIME type of the image (image/png, image/jpeg, etc.)

        Returns:
            Response content from the model
        """
        raise NotImplementedError("Vision not supported by this provider")


class OpenAIProvider(BaseLLMProvider):
    """OpenAI/OpenAI-compatible provider."""

    # Models that support vision
    # Note: gpt-5 and o-series models also support vision/multimodal input
    VISION_MODELS = ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4-vision-preview", "gpt-5", "o1", "o3"]

    def __init__(self):
        self._client = None
        self._lock = threading.Lock()

    @property
    def client(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    from openai import OpenAI
                    kwargs = {"api_key": settings.openai_api_key}
                    if settings.openai_base_url:
                        kwargs["base_url"] = settings.openai_base_url
                        # Override User-Agent for custom base URLs to avoid
                        # Cloudflare WAF blocking "OpenAI/Python" user agent
                        kwargs["default_headers"] = {"User-Agent": "python-httpx/0.27.0"}
                    self._client = OpenAI(**kwargs)
        return self._client

    def is_configured(self) -> bool:
        return bool(settings.openai_api_key)

    def get_provider_name(self) -> str:
        return "openai"

    def supports_vision(self) -> bool:
        return _model_matches_vision_pattern(settings.openai_model, self.VISION_MODELS)

    def chat_completion(self, system_prompt: str, user_prompt: str) -> str:
        model = settings.openai_model
        response = self.client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1,
            **_get_max_tokens_param(model, 1000),
        )
        return response.choices[0].message.content.strip()

    def vision_completion(self, system_prompt: str, user_prompt: str, image_data: bytes, mime_type: str = "image/png") -> str:
        model = settings.openai_model
        base64_image = base64.b64encode(image_data).decode("utf-8")
        response = self.client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}
                        }
                    ]
                }
            ],
            temperature=0.1,
            **_get_max_tokens_param(model, 1500),
        )
        return response.choices[0].message.content.strip()


class AnthropicProvider(BaseLLMProvider):
    """Anthropic Claude provider."""

    # All Claude 3 models support vision
    VISION_MODELS = ["claude-3", "claude-3.5"]

    def __init__(self):
        self._client = None
        self._lock = threading.Lock()

    @property
    def client(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    import anthropic
                    kwargs = {"api_key": settings.anthropic_api_key}
                    if settings.anthropic_base_url:
                        kwargs["base_url"] = settings.anthropic_base_url
                    self._client = anthropic.Anthropic(**kwargs)
        return self._client

    def is_configured(self) -> bool:
        return bool(settings.anthropic_api_key)

    def get_provider_name(self) -> str:
        return "anthropic"

    def supports_vision(self) -> bool:
        return _model_matches_vision_pattern(settings.anthropic_model, self.VISION_MODELS)

    def chat_completion(self, system_prompt: str, user_prompt: str) -> str:
        response = self.client.messages.create(
            model=settings.anthropic_model,
            max_tokens=1000,
            system=system_prompt,
            messages=[
                {"role": "user", "content": user_prompt}
            ],
        )
        return response.content[0].text.strip()

    def vision_completion(self, system_prompt: str, user_prompt: str, image_data: bytes, mime_type: str = "image/png") -> str:
        base64_image = base64.b64encode(image_data).decode("utf-8")
        response = self.client.messages.create(
            model=settings.anthropic_model,
            max_tokens=1500,
            system=system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime_type,
                                "data": base64_image
                            }
                        },
                        {"type": "text", "text": user_prompt}
                    ]
                }
            ],
        )
        return response.content[0].text.strip()


class GoogleProvider(BaseLLMProvider):
    """Google Gemini provider."""

    # Gemini models with vision support
    VISION_MODELS = ["gemini-1.5", "gemini-2", "gemini-pro-vision"]

    def __init__(self):
        self._model = None
        self._genai = None
        self._lock = threading.Lock()

    @property
    def genai(self):
        if self._genai is None:
            with self._lock:
                if self._genai is None:
                    import google.generativeai as genai
                    configure_kwargs = {"api_key": settings.google_api_key}
                    if settings.google_base_url:
                        configure_kwargs["client_options"] = {
                            "api_endpoint": settings.google_base_url
                        }
                    genai.configure(**configure_kwargs)
                    self._genai = genai
        return self._genai

    @property
    def model(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    self._model = self.genai.GenerativeModel(settings.google_model)
        return self._model

    def is_configured(self) -> bool:
        return bool(settings.google_api_key)

    def get_provider_name(self) -> str:
        return "google"

    def supports_vision(self) -> bool:
        return _model_matches_vision_pattern(settings.google_model, self.VISION_MODELS)

    def chat_completion(self, system_prompt: str, user_prompt: str) -> str:
        # Gemini combines system and user prompts
        full_prompt = f"{system_prompt}\n\n{user_prompt}"
        response = self.model.generate_content(full_prompt)
        return response.text.strip()

    def vision_completion(self, system_prompt: str, user_prompt: str, image_data: bytes, mime_type: str = "image/png") -> str:
        from PIL import Image
        from io import BytesIO

        # Convert bytes to PIL Image for Gemini
        image = Image.open(BytesIO(image_data))

        # Combine system and user prompts
        full_prompt = f"{system_prompt}\n\n{user_prompt}"

        response = self.model.generate_content([full_prompt, image])
        return response.text.strip()


class QwenProvider(BaseLLMProvider):
    """Alibaba Qwen provider (OpenAI-compatible)."""

    # Qwen-VL models support vision
    VISION_MODELS = ["qwen-vl", "qwen2-vl"]

    def __init__(self):
        self._client = None
        self._lock = threading.Lock()

    @property
    def client(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    from openai import OpenAI
                    self._client = OpenAI(
                        api_key=settings.qwen_api_key,
                        base_url=settings.qwen_base_url,
                    )
        return self._client

    def is_configured(self) -> bool:
        return bool(settings.qwen_api_key)

    def get_provider_name(self) -> str:
        return "qwen"

    def supports_vision(self) -> bool:
        return _model_matches_vision_pattern(settings.qwen_model, self.VISION_MODELS)

    def chat_completion(self, system_prompt: str, user_prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=settings.qwen_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1,
            max_tokens=1000,
        )
        return response.choices[0].message.content.strip()

    def vision_completion(self, system_prompt: str, user_prompt: str, image_data: bytes, mime_type: str = "image/png") -> str:
        base64_image = base64.b64encode(image_data).decode("utf-8")
        response = self.client.chat.completions.create(
            model=settings.qwen_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}
                        }
                    ]
                }
            ],
            temperature=0.1,
            max_tokens=1500,
        )
        return response.choices[0].message.content.strip()


class DeepSeekProvider(BaseLLMProvider):
    """DeepSeek provider (OpenAI-compatible)."""

    def __init__(self):
        self._client = None
        self._lock = threading.Lock()

    @property
    def client(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    from openai import OpenAI
                    self._client = OpenAI(
                        api_key=settings.deepseek_api_key,
                        base_url=settings.deepseek_base_url,
                    )
        return self._client

    def is_configured(self) -> bool:
        return bool(settings.deepseek_api_key)

    def get_provider_name(self) -> str:
        return "deepseek"

    def chat_completion(self, system_prompt: str, user_prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1,
            max_tokens=1000,
        )
        return response.choices[0].message.content.strip()


class ZhipuProvider(BaseLLMProvider):
    """Zhipu GLM provider."""

    # GLM-4V models support vision
    VISION_MODELS = ["glm-4v", "glm-4v-plus"]

    def __init__(self):
        self._client = None
        self._lock = threading.Lock()

    @property
    def client(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    from zhipuai import ZhipuAI
                    kwargs = {"api_key": settings.zhipu_api_key}
                    if settings.zhipu_base_url:
                        kwargs["base_url"] = settings.zhipu_base_url
                    self._client = ZhipuAI(**kwargs)
        return self._client

    def is_configured(self) -> bool:
        return bool(settings.zhipu_api_key)

    def get_provider_name(self) -> str:
        return "zhipu"

    def supports_vision(self) -> bool:
        return _model_matches_vision_pattern(settings.zhipu_model, self.VISION_MODELS)

    def chat_completion(self, system_prompt: str, user_prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=settings.zhipu_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1,
            max_tokens=1000,
        )
        return response.choices[0].message.content.strip()

    def vision_completion(self, system_prompt: str, user_prompt: str, image_data: bytes, mime_type: str = "image/png") -> str:
        base64_image = base64.b64encode(image_data).decode("utf-8")
        response = self.client.chat.completions.create(
            model=settings.zhipu_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"{system_prompt}\n\n{user_prompt}"},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}
                        }
                    ]
                }
            ],
            temperature=0.1,
            max_tokens=1500,
        )
        return response.choices[0].message.content.strip()


# Provider registry
PROVIDERS: Dict[str, type] = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "google": GoogleProvider,
    "qwen": QwenProvider,
    "deepseek": DeepSeekProvider,
    "zhipu": ZhipuProvider,
}


class LLMService:
    """Handles LLM-based invoice parsing with multi-provider support."""

    def __init__(self):
        self._providers: Dict[str, BaseLLMProvider] = {}
        self._active_provider: Optional[BaseLLMProvider] = None

    def _get_provider(self, provider_name: str) -> Optional[BaseLLMProvider]:
        """Get or create a provider instance."""
        if provider_name not in self._providers:
            if provider_name in PROVIDERS:
                self._providers[provider_name] = PROVIDERS[provider_name]()
        return self._providers.get(provider_name)

    @property
    def active_provider(self) -> Optional[BaseLLMProvider]:
        """Get the active LLM provider.

        Always fetch fresh settings to ensure config changes are reflected.
        """
        if self._active_provider is None:
            provider_name = get_settings().get_active_llm_provider()
            if provider_name:
                self._active_provider = self._get_provider(provider_name)
        return self._active_provider

    @property
    def is_available(self) -> bool:
        """Check if any LLM provider is available.

        Always fetch fresh settings to ensure config changes are reflected.
        """
        return get_settings().is_llm_configured()

    def get_configured_providers(self) -> list[str]:
        """Get list of configured provider names."""
        configured = []
        for name, provider_class in PROVIDERS.items():
            provider = self._get_provider(name)
            if provider and provider.is_configured():
                configured.append(name)
        return configured

    def get_active_provider_name(self) -> Optional[str]:
        """Get the name of the active provider."""
        return get_settings().get_active_llm_provider()

    def supports_vision(self) -> bool:
        """Check if the active provider supports vision/image input."""
        provider = self.active_provider
        return provider.supports_vision() if provider else False

    def parse_invoice_from_image(
        self,
        image_data: bytes,
        mime_type: str = "image/png",
        ctx: Optional["LLMCallContext"] = None,
    ) -> Dict[str, Any]:
        """Parse invoice directly from image using vision capabilities.

        Args:
            image_data: Raw image bytes
            mime_type: MIME type of the image
            ctx: Invoice identity (id, file name) used only for logs and dumps

        Returns:
            Dictionary of extracted fields
        """
        if not self.is_available:
            logger.warning("No LLM provider configured, skipping vision parsing")
            return {}

        provider = self.active_provider
        if not provider:
            logger.warning("Failed to get active LLM provider")
            return {}

        if not provider.supports_vision():
            logger.warning(f"Provider {provider.get_provider_name()} does not support vision")
            return {}

        label = ctx.label if ctx else "invoice=? file=?"
        content: Optional[str] = None
        try:
            content = provider.vision_completion(
                INVOICE_VISION_SYSTEM_PROMPT,
                INVOICE_VISION_PROMPT,
                image_data,
                mime_type
            )

            return self._parse_json_response(
                content, provider.get_provider_name(), ctx
            )

        except json.JSONDecodeError as e:
            snippet = content[:500] if content else "<no content>"
            logger.error(
                f"LLM vision JSON parse failed [{label}]: {e} | raw content: {snippet!r}"
            )
            _dump_llm_exchange(
                provider.get_provider_name(),
                ctx,
                content,
                error=f"JSONDecodeError: {e}",
            )
            return {}
        except Exception as e:
            logger.error(
                f"LLM vision parsing failed ({provider.get_provider_name()}) "
                f"[{label}]: {type(e).__name__}: {e}"
            )
            _dump_llm_exchange(
                provider.get_provider_name(),
                ctx,
                content,
                error=f"{type(e).__name__}: {e}",
            )
            return {}

    def _normalize_field_value(self, field_name: str, value: Any) -> Optional[str]:
        """Normalize and validate a single field value."""
        if value is None:
            return None

        if isinstance(value, (int, float)):
            value = str(value)
        if not isinstance(value, str):
            return None

        cleaned = value.strip()
        if not cleaned:
            return None

        if field_name in ['total_with_tax', 'amount', 'tax_amount']:
            cleaned = re.sub(r'[¥￥$€,，\s]', '', cleaned)#re.sub替换，把¥￥$€去除了
            match = re.search(r'(\d+(?:\.\d{1,2})?)', cleaned)#提取金额的内容
            return match.group(1) if match else None

        if field_name == 'issue_date':
            match = re.search(r'(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})', cleaned)#提取包含年/月/- 分隔符的内容
            if match:
                year, month, day = match.groups()
                return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
            if re.fullmatch(r'\d{4}-\d{2}-\d{2}', cleaned):#兼容不含文字的格式
                return cleaned
            return None

        if field_name == 'invoice_number':
            cleaned = re.sub(r'[\s-]+', '', cleaned)#移除空格
            return cleaned if re.fullmatch(r'\d{8,20}', cleaned) else None

        if field_name == 'invoice_code':
            cleaned = re.sub(r'[\s-]+', '', cleaned)
            return cleaned if re.fullmatch(r'\d{10,12}', cleaned) else None

        if field_name in ['buyer_tax_id', 'seller_tax_id']:
            cleaned = re.sub(r'\s+', '', cleaned.upper())
            return cleaned if re.fullmatch(r'[A-Z0-9]{15,20}', cleaned) else None

        if field_name == 'tax_rate':
            if cleaned == '免税':
                return cleaned
            return cleaned if re.fullmatch(r'\d+%', cleaned) else None

        return cleaned

    def _parse_json_response(
        self,
        content: str,
        provider_name: str,
        ctx: Optional["LLMCallContext"] = None,
    ) -> Dict[str, Any]:
        """Parse JSON from LLM response with schema validation and data cleaning.

        Args:
            content: Raw response content from LLM
            provider_name: Name of the provider for logging
            ctx: Invoice identity (id, file name) used only for logs and dumps

        Returns:
            Parsed and validated dictionary of fields
        """
        raw_content = content
        # Try to extract JSON from response
        if content.startswith("```"):
            # Remove markdown code blocks
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        raw_fields = json.loads(content)
        if not isinstance(raw_fields, dict):
            raise json.JSONDecodeError("LLM response is not a JSON object", content, 0)

        fields: Dict[str, Optional[str]] = {}
        changes: list[str] = []
        for field in REQUIRED_FIELDS:
            raw_value = raw_fields.get(field)
            cleaned = self._normalize_field_value(field, raw_value)
            fields[field] = cleaned
            if raw_value != cleaned:
                changes.append(f"{field}: {raw_value!r} -> {cleaned!r}")

        populated = [k for k, v in fields.items() if v is not None]
        extra_keys = [k for k in raw_fields if k not in REQUIRED_FIELDS]

        # Emitted at WARNING on purpose: the app never configures the root logger,
        # and uvicorn's default logging config leaves it without a handler, so INFO
        # records are dropped by logging.lastResort before they reach stderr.
        label = ctx.label if ctx else "invoice=? file=?"
        summary = (
            f"LLM ({provider_name}) normalize [{label}]: "
            f"populated={len(populated)}/{len(REQUIRED_FIELDS)} "
            f"changed={len(changes)}"
        )
        if extra_keys:
            summary += f" dropped_extra_keys={extra_keys}"
        logger.warning(summary)

        if changes:
            logger.warning(
                f"LLM ({provider_name}) normalize diff [{label}]: " + " | ".join(changes)
            )

        dump_path = _dump_llm_exchange(
            provider_name,
            ctx,
            raw_content,
            raw_fields=raw_fields,
            cleaned_fields=fields,
            changes=changes,
            extra_keys=extra_keys,
        )
        if dump_path:
            logger.warning(f"LLM ({provider_name}) dump written: {dump_path}")
        return fields


# Singleton instance
_llm_service: Optional[LLMService] = None


def get_llm_service() -> LLMService:
    """Get thread-safe LLMService singleton."""
    global _llm_service
    if _llm_service is None:
        with _llm_lock:
            # Double-check after acquiring lock
            if _llm_service is None:
                _llm_service = LLMService()
    return _llm_service


def reset_llm_service():
    """Reset the LLM service singleton (useful after config changes).

    Also refreshes the module-level settings to ensure providers
    see the updated configuration.
    """
    global _llm_service, settings
    with _llm_lock:
        _llm_service = None
        # Refresh module-level settings so providers see new config
        settings = get_settings()
