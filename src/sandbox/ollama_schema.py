"""Shared Ollama JSON-schema compatibility helpers."""

from __future__ import annotations

OLLAMA_GRAMMAR_MAX_FINITE_REPEAT = 1024


def ollama_compatible_schema(schema: dict) -> dict:
    """Drop finite string bounds that Ollama's grammar compiler rejects."""
    if not isinstance(schema, dict):
        raise TypeError("Ollama schema must be a JSON-schema object")
    stack: list[object] = [schema]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            max_length = current.get("maxLength")
            if (
                isinstance(max_length, int)
                and max_length > OLLAMA_GRAMMAR_MAX_FINITE_REPEAT
            ):
                current.pop("maxLength")
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return schema
