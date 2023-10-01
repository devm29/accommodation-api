"""The provider used when nothing is configured.

Its whole job is to make "no API key" a clear, catchable condition instead of
a 500 from deep inside a vendor SDK. Every caller already handles
:class:`LLMUnavailable`, so the product boots and serves every other endpoint.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from .base import Completion, LLMUnavailable


class NullProvider:
    name = "null"

    def is_available(self) -> bool:
        return False

    def complete(
        self,
        messages: Sequence[Any],
        *,
        purpose: str = "",
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Completion:
        raise LLMUnavailable(
            "No language model is configured. Set OPENAI_API_KEY, or set "
            "LLM_PROVIDER=demo to run against the built-in canned responses."
        )
