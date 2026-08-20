"""Validate the Ollama-facing Office V2 mutation schema inside an image."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from app.office_v2_mutator_worker import _response_schema


def _max_lengths(value: Any) -> Iterator[int]:
    if isinstance(value, dict):
        limit = value.get("maxLength")
        if isinstance(limit, int):
            yield limit
        for child in value.values():
            yield from _max_lengths(child)
    elif isinstance(value, list):
        for child in value:
            yield from _max_lengths(child)


def main() -> int:
    schema = _response_schema()
    limits = list(_max_lengths(schema))
    if any(limit > 1024 for limit in limits):
        raise SystemExit(f"Ollama schema contains unsupported maxLength: {limits}")
    print(
        json.dumps(
            {
                "schema_check": "passed",
                "max_lengths": limits,
                "top_level_keys": sorted(schema),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
