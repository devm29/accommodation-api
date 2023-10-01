"""A curated table of rules, consulted before anything is paid for.

Most searches in this product are repeats of a short list of cities. Answering
those from a reviewed file is faster than a model, free, and - for the handful
of places it covers - more trustworthy, because a person wrote it.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .base import KNOWN_STATUSES, RegulationFinding

logger = logging.getLogger(__name__)

DATA_FILE = Path(__file__).resolve().parent / "data" / "str_rules.json"


def normalise(query: str) -> str:
    """Fold case, punctuation and whitespace so 'Kirkland, WA' == 'kirkland wa'."""
    return " ".join(re.sub(r"[^a-z0-9\s]", " ", (query or "").lower()).split())


@lru_cache(maxsize=1)
def _load(path: str = str(DATA_FILE)) -> dict[str, dict]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.exception("Could not read the curated STR rules at %s", path)
        return {}

    table: dict[str, dict] = {}
    for entry in raw.get("locations", []):
        if entry.get("status") not in KNOWN_STATUSES:
            logger.warning("Skipping curated entry with unknown status: %r", entry.get("status"))
            continue
        for alias in [entry.get("location", "")] + list(entry.get("aliases", [])):
            key = normalise(alias)
            if key:
                table[key] = entry
    return table


class StaticRegulationSource:
    """Exact-or-contained match against the curated table."""

    name = "curated"
    #: Answers without a network call, so it may run inside a request.
    is_offline = True

    def is_available(self) -> bool:
        return bool(_load())

    def lookup(self, query: str) -> Optional[RegulationFinding]:
        table = _load()
        key = normalise(query)
        if not key:
            return None

        entry = table.get(key)
        if entry is None:
            # Fall back to the longest alias contained in the query, so
            # "123 Main St, Kirkland WA 98033" still resolves to Kirkland.
            candidates = [alias for alias in table if alias and alias in key]
            if not candidates:
                return None
            entry = table[max(candidates, key=len)]

        return RegulationFinding(
            status=entry["status"],
            summary=entry["summary"],
            source=f"{self.name}:{entry['location']}",
            confidence=float(entry.get("confidence", 0.9)),
            citations=tuple(entry.get("citations", ())),
        )
