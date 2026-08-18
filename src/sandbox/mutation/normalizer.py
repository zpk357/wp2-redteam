"""Versioned prompt normalization and content-addressed identifiers."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from sandbox import text_normalization
from sandbox.replay.digests import sha256_digest

NORMALIZATION_VERSION = "1.0"


def normalize_prompt(prompt: str) -> str:
    return text_normalization.normalize_prompt(prompt)


def normalized_prompt_digest(prompt: str) -> str:
    return text_normalization.normalized_prompt_digest(prompt)


def prompt_digest(prompt: str) -> str:
    return sha256_digest(prompt)


def prompt_dedupe_key(prompt: str) -> str:
    return sha256_digest(
        {
            "candidate_kind": "prompt",
            "normalized_prompt_sha256": normalized_prompt_digest(prompt),
        }
    )


def fork_dedupe_key(
    *,
    parent_replay_id: str,
    checkpoint_id: str,
    injection_type: str,
    content: str,
) -> str:
    return sha256_digest(
        {
            "candidate_kind": "fork",
            "parent_replay_id": parent_replay_id,
            "checkpoint_id": checkpoint_id,
            "injection_type": injection_type,
            "normalized_prompt_sha256": normalized_prompt_digest(content),
        }
    )


def _digest_projection(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _digest_projection(value.model_dump(mode="json", exclude_none=False))
    if isinstance(value, float):
        return format(value, ".17g")
    if isinstance(value, dict):
        return {
            str(key): _digest_projection(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, list | tuple):
        return [_digest_projection(item) for item in value]
    return value


def stable_digest(value: Any) -> str:
    return sha256_digest(_digest_projection(value))
