"""Versioned catalog identities locked into scenario campaigns."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import Field, field_validator, model_validator

from sandbox.protocol import normalize_sha256_digest
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.models import FrozenContract, Identifier


class CatalogLock(FrozenContract):
    """Identity of one ordered-independent catalog and all of its frozen items."""

    catalog_id: Identifier
    catalog_version: str = Field(min_length=1, max_length=128)
    item_ids: tuple[Identifier, ...] = Field(min_length=1)
    content_digest: str

    @field_validator("item_ids")
    @classmethod
    def item_ids_are_unique_and_canonical(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("catalog item_ids must not contain duplicates")
        return tuple(sorted(value))

    @field_validator("content_digest")
    @classmethod
    def digest_is_canonical(cls, value: str) -> str:
        return normalize_sha256_digest(value)


class ScenarioCatalogManifest(FrozenContract):
    """Separate catalog locks required by composable scenario generation."""

    scenario: CatalogLock
    benign_tasks: CatalogLock
    attack_objectives: CatalogLock
    injection_carriers: CatalogLock
    attack_expressions: CatalogLock
    content_digest: str | None = None

    @model_validator(mode="after")
    def validate_digest(self) -> ScenarioCatalogManifest:
        locks = (
            self.scenario,
            self.benign_tasks,
            self.attack_objectives,
            self.injection_carriers,
            self.attack_expressions,
        )
        catalog_ids = [lock.catalog_id for lock in locks]
        if len(catalog_ids) != len(set(catalog_ids)):
            raise ValueError("scenario catalog manifest catalog_id values must be unique")
        calculated = sha256_digest(
            self.model_dump(mode="json", exclude={"content_digest"})
        )
        if self.content_digest is not None:
            supplied = normalize_sha256_digest(self.content_digest)
            if supplied != calculated:
                raise ValueError("scenario catalog manifest content_digest does not match")
        object.__setattr__(self, "content_digest", calculated)
        return self

    def assert_integrity(self) -> None:
        current = sha256_digest(
            self.model_dump(mode="json", exclude={"content_digest"})
        )
        if current != self.content_digest:
            raise ValueError("scenario catalog manifest no longer matches content_digest")


def build_catalog_lock(
    *,
    catalog_id: str,
    catalog_version: str,
    items: Sequence[tuple[str, Any]],
) -> CatalogLock:
    """Build a canonical lock from item identities and their complete frozen content."""
    canonical_items = sorted(items, key=lambda item: item[0])
    item_ids = tuple(item_id for item_id, _content in canonical_items)
    if not item_ids:
        raise ValueError("catalog lock requires at least one item")
    if len(item_ids) != len(set(item_ids)):
        raise ValueError("catalog lock item identities must be unique")
    payload = {
        "catalog_id": catalog_id,
        "catalog_version": catalog_version,
        "items": [
            {"item_id": item_id, "content": content}
            for item_id, content in canonical_items
        ],
    }
    return CatalogLock(
        catalog_id=catalog_id,
        catalog_version=catalog_version,
        item_ids=item_ids,
        content_digest=sha256_digest(payload),
    )
