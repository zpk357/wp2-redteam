"""The image's freeze manifest, and the assets the container rebuilds from it (P6).

A container owns its assets; it does not receive a world from the caller. The build writes
a freeze manifest holding the tool catalogue, the image manifest, the frozen worlds' state
payloads and the ``fixture → base/overlay`` mapping, and this module rebuilds the store
from it - checking that every rebuilt world still hashes to the digest its locator claims.
A world that does not rebuild to its own digest is refused rather than used.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from sandbox.scenarios.office_v2.canonical_world import (
    CanonicalOfficeWorld,
    OfficeWorldState,
    build_canonical_world,
)
from sandbox.structured_v1.assets import (
    FixtureBaseMapping,
    FrozenAssetStore,
    FrozenWorldAsset,
)
from sandbox.structured_v1.envelope_codes import EnvelopeRefusal, FailureCode
from sandbox.structured_v1.models import Sha256Digest, StructuredContract
from sandbox.structured_v1.tool_catalogue import ImageManifest, ToolingFacts


class FrozenWorldSnapshot(StructuredContract):
    """One world the image froze: where it lives, what it must hash to, and its state."""

    locator: str
    world_digest: Sha256Digest
    state: dict[str, Any]


class FreezeManifest(StructuredContract):
    """Everything the image froze for this protocol identity."""

    image_manifest: ImageManifest
    mapping: tuple[FixtureBaseMapping, ...] = ()
    worlds: tuple[FrozenWorldSnapshot, ...] = ()


def write_freeze_manifest(manifest: FreezeManifest, path: Path) -> Path:
    """The build-time writer: what the image loads at `/opt/structured-v1/freeze-manifest.json`."""

    path.write_text(
        json.dumps(manifest.model_dump(mode="json", exclude_none=False), indent=2),
        encoding="utf-8",
    )
    return path


def build_freeze_manifest(
    *,
    image_manifest: ImageManifest,
    worlds: Iterable[CanonicalOfficeWorld],
    mapping: Iterable[FixtureBaseMapping] = (),
) -> FreezeManifest:
    """The build-time constructor: freeze each world and the fixture mapping into one manifest.

    A world is stored by its own digest (its locator), and the snapshot carries the full
    state payload so ``load_assets`` can rebuild and re-hash it - a world that does not
    rebuild to its digest is refused rather than used.
    """

    snapshots = tuple(
        FrozenWorldSnapshot(
            locator=FrozenWorldAsset(world=world).locator,
            world_digest=world.world_digest,
            state=world.state.model_dump(mode="json", exclude_none=False),
        )
        for world in worlds
    )
    return FreezeManifest(
        image_manifest=image_manifest,
        mapping=tuple(mapping),
        worlds=snapshots,
    )


def load_assets(
    manifest: FreezeManifest,
) -> tuple[FrozenAssetStore, ToolingFacts]:
    """Rebuild the store and the tooling facts, refusing a world that does not rebuild."""

    assets: list[FrozenWorldAsset] = []
    for snapshot in manifest.worlds:
        world = build_canonical_world(OfficeWorldState.model_validate(snapshot.state))
        if world.world_digest != snapshot.world_digest:
            raise EnvelopeRefusal(
                FailureCode.BASE_WORLD_MISMATCH,
                f"the world at {snapshot.locator!r} rebuilt to {world.world_digest}, "
                f"not {snapshot.world_digest}",
            )
        asset = FrozenWorldAsset(world=world)
        if asset.locator != snapshot.locator:
            raise EnvelopeRefusal(
                FailureCode.LOCATOR_OUTSIDE_STORE,
                f"the world is stored at {asset.locator!r}, not {snapshot.locator!r}",
            )
        assets.append(asset)
    return (
        FrozenAssetStore(assets, manifest.mapping),
        ToolingFacts(
            catalogue=manifest.image_manifest.tool_catalogue,
            image_manifest=manifest.image_manifest,
        ),
    )
