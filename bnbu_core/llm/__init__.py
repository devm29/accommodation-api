"""
The language-model seam.

Every GPT call in this project goes through :class:`LLMProvider`. Swapping
vendors, pinning a different SDK, or running the whole product with no
credentials at all is a registry entry rather than an edit to four views.
"""

from .base import (
    Completion,
    LLMError,
    LLMProvider,
    LLMRateLimited,
    LLMUnavailable,
    Message,
    Purpose,
)
from .registry import (
    available_providers,
    get_llm_provider,
    register_provider,
    reset_provider_cache,
)

__all__ = [
    "Completion",
    "LLMError",
    "LLMProvider",
    "LLMRateLimited",
    "LLMUnavailable",
    "Message",
    "Purpose",
    "available_providers",
    "get_llm_provider",
    "register_provider",
    "reset_provider_cache",
]
