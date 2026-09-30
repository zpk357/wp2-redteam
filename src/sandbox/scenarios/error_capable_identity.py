"""Provider-visible model identity, kept in both raw and normalized form (`RA-05`).

The scenario contracts use ``Identifier``, which is ``^[a-z0-9][a-z0-9._-]{0,127}$``.  The tag a
real provider actually serves does not satisfy that pattern -- ``qwen3.5:27b-q4_K_M`` carries a
colon, underscores and upper case -- so a contract that records only an ``Identifier`` silently
replaces the truth with a value that can never be compared back to the provider.

Both forms are therefore kept.  ``raw_model_label`` is what the provider reports; the digest covers
it, so two different tags cannot collapse into one identity, and ``provider_version`` stays beside
them rather than being folded into either.
"""

from __future__ import annotations

import re

from pydantic import Field, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import Identifier, OfficeV2Contract, Sha256Digest

IDENTITY_VERSION = "model-identity-v1"

#: Everything outside the ``Identifier`` alphabet, collapsed to a single separator.
_OUTSIDE_ALPHABET = re.compile(r"[^a-z0-9._-]+")
#: An ``Identifier`` has to start with an alphanumeric character.
_LEADING_SEPARATORS = re.compile(r"^[^a-z0-9]+")
_MAX_IDENTIFIER = 128


def normalize_model_label(raw: str) -> str:
    """Project a provider label onto the ``Identifier`` alphabet without inventing characters.

    The mapping is total and deterministic: case-fold, replace every character outside the alphabet
    with ``-``, drop leading separators, and cut to the identifier length.  It is not injective, so
    the raw label is what identity is actually keyed on.
    """

    folded = _OUTSIDE_ALPHABET.sub("-", raw.strip().casefold())
    trimmed = _LEADING_SEPARATORS.sub("", folded)[:_MAX_IDENTIFIER].rstrip("-")
    if not trimmed:
        raise ValueError("model label normalises to an empty identifier")
    return trimmed


class ModelIdentity(OfficeV2Contract):
    """One provider-visible model, in both the served and the recordable form."""

    version: str = IDENTITY_VERSION
    provider_id: Identifier
    raw_model_label: str = Field(min_length=1, max_length=256)
    normalized_model_id: Identifier
    provider_version: str | None = Field(default=None, max_length=256)
    identity_digest: Sha256Digest

    @classmethod
    def capture(
        cls,
        *,
        provider_id: str,
        raw_model_label: str,
        provider_version: str | None = None,
    ) -> ModelIdentity:
        payload = {
            "version": IDENTITY_VERSION,
            "provider_id": provider_id,
            "raw_model_label": raw_model_label,
            "normalized_model_id": normalize_model_label(raw_model_label),
            "provider_version": provider_version,
        }
        return cls(**payload, identity_digest=sha256_digest(payload))

    @model_validator(mode="after")
    def normalization_follows_from_the_raw_label(self) -> ModelIdentity:
        if normalize_model_label(self.raw_model_label) != self.normalized_model_id:
            raise ValueError("normalized_model_id does not follow from raw_model_label")
        return self

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"identity_digest"})

    def is_same_identity(self, other: ModelIdentity) -> bool:
        """Identity is the digest, so the raw label participates and cannot be dropped."""

        return self.identity_digest == other.identity_digest

    def describe(self) -> str:
        version = self.provider_version or "unknown"
        return f"{self.provider_id}:{self.raw_model_label}#{version}"


__all__ = [
    "IDENTITY_VERSION",
    "ModelIdentity",
    "normalize_model_label",
]
