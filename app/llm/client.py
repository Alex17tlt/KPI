"""OpenRouter client: bounded timeout, model fallback, JSON mode with schema validation and repair."""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any, TypeVar

import requests
from pydantic import BaseModel, ValidationError

from app.config import settings

log = logging.getLogger("kpi.llm")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.OPENROUTER_API_KEY}", "Content-Type": "application/json",
            "HTTP-Referer": settings.BASE_URL, "X-Title": "Seven Group KPI"}


def _post_bounded(url: str, *, headers: dict, body: str, timeout: float) -> requests.Response:
    """requests' timeout does not cover DNS or a trickling response; run on a daemon thread with a hard deadline."""
    if timeout <= 0:
        raise requests.Timeout(f"OpenRouter deadline exhausted ({timeout}s)")
    box: dict = {}

    def run():
        try:
            box["resp"] = requests.post(url, headers=headers, data=body, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            box["err"] = e

    worker = threading.Thread(target=run, name="openrouter-post", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise requests.Timeout(f"OpenRouter did not respond within {timeout}s")
    if "err" in box:
        raise box["err"]
    if box.get("resp") is None:
        raise requests.Timeout("OpenRouter request ended without a response")
    return box["resp"]


def call(messages: list[dict], model: str | None = None, *, timeout: int | None = None, reasoning: bool = False,
         fallback_models: list[str] | None = None, temperature: float | None = 0.2,
         max_tokens: int | None = None) -> str:
    if not settings.OPENROUTER_API_KEY:
        raise LLMError("OPENROUTER_API_KEY не задан в .env")
    primary = model or settings.LLM_MODEL_TEXT
    fallbacks = settings.LLM_FALLBACK_MODELS if fallback_models is None else fallback_models
    models = [primary] + [m for m in fallbacks if m != primary]
    timeout = timeout or settings.LLM_TIMEOUT
    last: Exception | None = None
    for m in models:
        data: dict[str, Any] = {"model": m, "messages": messages}
        if temperature is not None:
            data["temperature"] = temperature
        if max_tokens:
            data["max_tokens"] = max_tokens
        if reasoning:
            data["reasoning"] = {"effort": "medium", "max_tokens": 2000}
        t0 = time.monotonic()
        try:
            resp = _post_bounded(OPENROUTER_URL, headers=_headers(), body=json.dumps(data), timeout=timeout)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"].get("content")
            if content and content.strip():
                log.info("llm ok model=%s %.1fs", m, time.monotonic() - t0)
                return content
            last = LLMError(f"модель {m} вернула пустой ответ")
            log.warning("llm empty model=%s", m)
        except Exception as e:  # noqa: BLE001
            last = e
            detail = (getattr(getattr(e, "response", None), "text", "") or "")[:300]
            log.warning("llm fail model=%s: %s %s", m, e, detail)
    raise LLMError(f"Все модели недоступны: {last}")


def ask(prompt: str, model: str | None = None, **kw) -> str:
    """Pass a prompt, get a string back."""
    return call([{"role": "user", "content": prompt}], model, **kw)


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_json_text(raw: str) -> Any:
    s = _FENCE.sub("", raw.strip())
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        a, b = s.find("{"), s.rfind("}")
        if a != -1 and b > a:
            return json.loads(s[a:b + 1])
        raise


def call_json(messages: list[dict], schema: type[T], model: str | None = None, *, retries: int = 2, **kw) -> T:
    """Validated JSON. On a bad answer the validation error is sent back to the model for repair."""
    msgs, last = list(messages), None
    for attempt in range(retries + 1):
        raw = call(msgs, model, **kw)
        try:
            return schema.model_validate(parse_json_text(raw))
        except (json.JSONDecodeError, ValidationError) as e:
            last = e
            log.warning("llm json invalid (attempt %s): %s", attempt, str(e)[:300])
            msgs = [*messages, {"role": "assistant", "content": raw[:6000]},
                    {"role": "user", "content": f"Ответ не прошёл проверку:\n{str(e)[:1500]}\n"
                                                "Верни исправленный JSON целиком, без пояснений и без ```."}]
    raise LLMError(f"Модель не вернула корректный JSON: {last}")
