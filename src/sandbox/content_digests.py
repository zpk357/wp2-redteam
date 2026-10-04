"""Dependency-neutral canonical digests for models containing finite floats."""

from __future__ import annotations

import math

from pydantic import BaseModel

from sandbox.replay.digests import sha256_digest


def decimalized_sha256_digest(value: object, *, label: str = "digest") -> str:
    """Hash canonical content after representing finite fractional floats as strings."""

    return sha256_digest(_decimalize_floats(value, label=label))


def decimalize_floats(value: object, *, label: str = "value") -> object:
    """The same conversion `decimalized_sha256_digest` applies, returned as data.

    Needed when the converted object is *stored* rather than only hashed: canonical JSON v1 refuses
    a fractional float outright, so writing raw floats next to a digest computed over the converted
    form would produce a file whose own digest cannot be recomputed from its contents.
    """

    return _decimalize_floats(value, label=label)


def _decimalize_floats(value: object, *, label: str) -> object:
    if isinstance(value, BaseModel):
        return _decimalize_floats(
            value.model_dump(mode="python", exclude_none=False),
            label=label,
        )
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{label} cannot contain NaN or Infinity")
        if value.is_integer():
            return int(value)
        return str(value)
    if isinstance(value, dict):
        return {
            key: _decimalize_floats(item, label=label)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_decimalize_floats(item, label=label) for item in value]
    return value
