"""Адаптеры к LLM. Облако (Anthropic Claude) и локальная модель (Ollama) за одним интерфейсом."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from .config import PRICES_PER_MTOK, settings


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResult:
    text: str
    model: str
    provider: str
    usage: dict[str, int] = field(default_factory=dict)
    stop_reason: str | None = None
    duration_sec: float = 0.0
    cost_usd: float | None = None

    def parsed(self) -> dict[str, Any]:
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as e:
            raise LLMError(f"Модель вернула невалидный JSON: {e}") from e


class Provider(Protocol):
    name: str
    model: str

    async def complete(self, system: str, user: str, schema: dict[str, Any]) -> LLMResult: ...


def estimate_cost(model: str, usage: dict[str, int]) -> float | None:
    price = PRICES_PER_MTOK.get(model)
    if not price:
        return None
    pin, pout = price
    cost = (
        usage.get("input_tokens", 0) * pin
        + usage.get("cache_creation_input_tokens", 0) * pin * 1.25
        + usage.get("cache_read_input_tokens", 0) * pin * 0.1
        + usage.get("output_tokens", 0) * pout
    ) / 1_000_000
    return round(cost, 5)


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str | None = None, effort: str | None = None):
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.AsyncAnthropic(max_retries=5)
        self.model = model or settings.model
        self.effort = effort or settings.effort

    async def complete(self, system: str, user: str, schema: dict[str, Any]) -> LLMResult:
        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=32000,
            # Системный промпт одинаков для всех диалогов одного чек-листа → кэшируется.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
        )
        started = time.monotonic()
        try:
            if settings.fallbacks:
                stream_cm = self.client.beta.messages.stream(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
                )
            else:
                stream_cm = self.client.messages.stream(**kwargs)
            async with stream_cm as stream:
                msg = await stream.get_final_message()
        except self._anthropic.AuthenticationError as e:
            raise LLMError("Ошибка авторизации Anthropic: проверьте ANTHROPIC_API_KEY") from e
        except self._anthropic.BadRequestError as e:
            raise LLMError(f"Некорректный запрос к модели: {e}") from e
        except self._anthropic.RateLimitError as e:
            raise LLMError("Превышен лимит запросов Anthropic (после повторов)") from e
        except self._anthropic.APIStatusError as e:
            raise LLMError(f"Ошибка API Anthropic {e.status_code}: {e}") from e
        except self._anthropic.APIConnectionError as e:
            raise LLMError(f"Нет соединения с API Anthropic: {e}") from e

        if msg.stop_reason == "refusal":
            raise LLMError("Модель отказалась оценивать диалог (refusal)")
        if msg.stop_reason == "max_tokens":
            raise LLMError("Ответ модели обрезан по max_tokens")
        text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        u = msg.usage
        usage = {
            "input_tokens": getattr(u, "input_tokens", 0) or 0,
            "output_tokens": getattr(u, "output_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
            "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
        }
        served_by = getattr(msg, "model", None) or self.model
        return LLMResult(text=text, model=served_by, provider=self.name, usage=usage,
                         stop_reason=msg.stop_reason, duration_sec=round(time.monotonic() - started, 2),
                         cost_usd=estimate_cost(self.model, usage))


class OllamaProvider:
    """Локальная модель через нативный API Ollama (данные не покидают контур)."""
    name = "ollama"

    def __init__(self, model: str | None = None, url: str | None = None):
        self.model = model or settings.ollama_model
        self.url = (url or settings.ollama_url).rstrip("/")

    async def complete(self, system: str, user: str, schema: dict[str, Any]) -> LLMResult:
        started = time.monotonic()
        payload = {
            "model": self.model,
            "stream": False,
            "format": schema,
            "think": settings.ollama_think,
            "options": {"temperature": 0, "num_ctx": settings.ollama_num_ctx},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        try:
            async with httpx.AsyncClient(timeout=600) as client:
                r = await client.post(f"{self.url}/api/chat", json=payload)
                r.raise_for_status()
                data = r.json()
        except httpx.HTTPError as e:
            raise LLMError(f"Ошибка Ollama: {e}") from e
        usage = {"input_tokens": data.get("prompt_eval_count", 0), "output_tokens": data.get("eval_count", 0)}
        return LLMResult(text=data["message"]["content"], model=self.model, provider=self.name, usage=usage,
                         stop_reason=data.get("done_reason"), duration_sec=round(time.monotonic() - started, 2),
                         cost_usd=0.0)


def get_provider(name: str | None = None, model: str | None = None) -> Provider:
    name = name or settings.provider
    if name == "anthropic":
        return AnthropicProvider(model=model)
    if name == "ollama":
        return OllamaProvider(model=model)
    raise LLMError(f"Неизвестный провайдер: {name}")
