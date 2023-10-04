"""OpenAI-backed provider.

This is the only module in the project that knows the OpenAI SDK exists. The
pinned SDK is 0.28, whose surface is ``openai.ChatCompletion.create``; moving
to the 1.x client means rewriting this file and nothing else.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

import openai

from .base import Completion, LLMRateLimited, LLMUnavailable, to_messages

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-4"
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF_SECONDS = 30.0


class OpenAIProvider:
    """Chat completions via OpenAI, with a bounded retry on rate limits."""

    name = "openai"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        retries: int = DEFAULT_RETRIES,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
        model_overrides: Optional[dict[str, str]] = None,
    ) -> None:
        self._api_key = api_key
        self.model = model or DEFAULT_MODEL
        self.retries = max(1, retries)
        self.backoff_seconds = backoff_seconds
        # purpose -> model, so a cheap model can serve chat while extraction
        # keeps the strong one, without touching a single call site.
        self.model_overrides = dict(model_overrides or {})

    def is_available(self) -> bool:
        return bool(self._api_key)

    def complete(
        self,
        messages: Sequence[Any],
        *,
        purpose: str = "",
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Completion:
        if not self.is_available():
            raise LLMUnavailable(
                "OPENAI_API_KEY is not set; set it or choose another LLM_PROVIDER."
            )

        payload: dict[str, Any] = {
            "model": model or self.model_overrides.get(purpose) or self.model,
            "messages": [message.as_dict() for message in to_messages(messages)],
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        openai.api_key = self._api_key

        last_error: Optional[Exception] = None
        for attempt in range(1, self.retries + 1):
            try:
                response = openai.ChatCompletion.create(**payload)
            except openai.error.RateLimitError as exc:
                last_error = exc
                logger.warning(
                    "OpenAI rate limit (attempt %d of %d): %s", attempt, self.retries, exc
                )
                if attempt < self.retries:
                    time.sleep(self.backoff_seconds)
                continue

            return _to_completion(response, payload["model"])

        raise LLMRateLimited(
            f"OpenAI refused {self.retries} attempts for model {payload['model']}"
        ) from last_error


def _to_completion(response: Any, model: str) -> Completion:
    text = response["choices"][0]["message"]["content"]
    created = response.get("created") if hasattr(response, "get") else None
    if isinstance(created, (int, float)):
        created_at = datetime.fromtimestamp(created, tz=timezone.utc)
    else:
        created_at = datetime.now(timezone.utc)
    return Completion(
        text=(text or "").strip(),
        model=model,
        created_at=created_at,
        provider="openai",
        raw={"id": response.get("id")} if hasattr(response, "get") else {},
    )
