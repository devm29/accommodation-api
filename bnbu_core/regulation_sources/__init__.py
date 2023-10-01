"""
The regulation-source seam.

``RegulationSourceChain`` answers "can I run a short-term rental here?" by
asking each configured source in turn. ``settings.REGULATION_SOURCES`` decides
the order; ``register_source`` adds a new one.
"""

from .base import (
    KNOWN_STATUSES,
    STATUS_ALLOWED,
    STATUS_NOT_ALLOWED,
    STATUS_PENDING,
    STATUS_RESTRICTED,
    RegulationFinding,
    RegulationSource,
    RegulationSourceError,
)
from .registry import (
    RegulationSourceChain,
    available_sources,
    configured_chain,
    get_source,
    iter_sources,
    register_source,
    reset_source_cache,
)

__all__ = [
    "KNOWN_STATUSES",
    "STATUS_ALLOWED",
    "STATUS_NOT_ALLOWED",
    "STATUS_PENDING",
    "STATUS_RESTRICTED",
    "RegulationFinding",
    "RegulationSource",
    "RegulationSourceChain",
    "RegulationSourceError",
    "available_sources",
    "configured_chain",
    "get_source",
    "iter_sources",
    "register_source",
    "reset_source_cache",
]
