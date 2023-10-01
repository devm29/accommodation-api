"""Small helpers for the JSONField columns this project stores answers in."""

from __future__ import annotations

import json
from typing import Any, Optional


def as_mapping(value: Any) -> dict:
    """
    Return ``value`` as a dict.

    ``Document.gpt_response`` and ``Regulations.gpt_response`` are JSONFields,
    but analysis results were once written to them with ``json.dumps()``, so
    rows in an existing database hold a JSON *string*. Every reader called
    ``.get()`` on it, which is an ``AttributeError`` on a str.
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def first_json_object(text: str) -> dict:
    """
    Pull the first balanced JSON object out of ``text``.

    Models wrap JSON in prose or a ``` fence however firmly you ask them not
    to. Returns ``{}`` when there is nothing parseable.
    """
    if not isinstance(text, str):
        return {}

    start = text.find("{")
    while start != -1:
        end = _end_of_object(text, start)
        if end is not None:
            try:
                decoded = json.loads(text[start : end + 1])
            except ValueError:
                decoded = None
            if isinstance(decoded, dict):
                return decoded
        start = text.find("{", start + 1)
    return {}


def _end_of_object(text: str, start: int) -> Optional[int]:
    """Index of the ``}`` closing the object that opens at ``start``."""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None
