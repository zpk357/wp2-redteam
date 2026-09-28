"""The frozen assets the container owns (`SOC-ENV-12`, `SOC-ENV-27`; P2).

The envelope points at a base world by **locator**, and the container resolves that
locator **inside its own store**: it never follows a path, a parent reference, a URL or an
unregistered name, so neither a candidate nor a provider can redirect it at a different
world. A locator is ``frozen-world/<bare sha256>`` - the world's own digest - which means
the location and the world's identity are the same fact rather than two things that have
to agree.

The store also holds the frozen ``fixture → base/overlay`` mapping (`SOC-ENV-12`): which
base a fixture runs against, and the digest of the overlay it was frozen with. That is the
mapping a run may not deviate from, and the reason a fixture's policy is data rather than
a code path.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from sandbox.scenarios.office_v2.canonical_world import CanonicalOfficeWorld
from sandbox.structured_v1.envelope_codes import EnvelopeRefusal, FailureCode
from sandbox.structured_v1.models import (
    Identifier,
    Sha256Digest,
    StructuredContract,
)
from sandbox.structured_v1.world import FixtureWorldOverlay

LOCATOR_NAMESPACE = "frozen-world"
LOCATOR_PATTERN = re.compile(rf"^{LOCATOR_NAMESPACE}/([0-9a-f]{{64}})$")


class FixtureBaseMapping(StructuredContract):
    """One line of the frozen `fixture → base/overlay` mapping (`SOC-ENV-12`)."""

    fixture_id: Identifier
    fixture_version: Identifier
    manifest_digest: Sha256Digest
    base_locator: str
    base_world_digest: Sha256Digest
    overlay_digest: Sha256Digest


@dataclass(frozen=True)
class FrozenWorldAsset:
    """One world the image froze, reachable only by its own digest."""

    world: CanonicalOfficeWorld

    @property
    def locator(self) -> str:
        return f"{LOCATOR_NAMESPACE}/{self.world.world_digest.removeprefix('sha256:')}"

    @property
    def world_digest(self) -> str:
        return self.world.world_digest


class FrozenAssetStore:
    """The container's own assets, addressed by locator; nothing outside it is reachable."""

    def __init__(
        self,
        worlds: Iterable[FrozenWorldAsset],
        mappings: Iterable[FixtureBaseMapping] = (),
    ) -> None:
        self._worlds: Mapping[str, FrozenWorldAsset] = MappingProxyType(
            {asset.locator: asset for asset in worlds}
        )
        self._mappings: Mapping[str, FixtureBaseMapping] = MappingProxyType(
            {mapping.fixture_id: mapping for mapping in mappings}
        )

    def locators(self) -> tuple[str, ...]:
        return tuple(sorted(self._worlds))

    def mapping_for(self, fixture_id: str) -> FixtureBaseMapping | None:
        return self._mappings.get(fixture_id)

    def resolve(self, locator: str) -> CanonicalOfficeWorld:
        """Resolve a locator inside the store, or refuse with the reason.

        Anything that is not this namespace's own digest is ``envelope.locator_outside_store``;
        a well-formed locator with no asset behind it is ``envelope.payload_missing`` -
        a declared digest without a payload is not a world.
        """

        match = LOCATOR_PATTERN.fullmatch(locator)
        if match is None:
            raise EnvelopeRefusal(
                FailureCode.LOCATOR_OUTSIDE_STORE,
                f"{locator!r} is not a {LOCATOR_NAMESPACE}/<digest> locator",
            )
        asset = self._worlds.get(locator)
        if asset is None:
            raise EnvelopeRefusal(
                FailureCode.PAYLOAD_MISSING,
                f"no frozen world is stored at {locator!r}",
            )
        return asset.world


def asset_digest_of(world: CanonicalOfficeWorld) -> str:
    """The digest a locator for this world must carry."""

    return world.world_digest


def overlay_digest(overlay: FixtureWorldOverlay) -> str:
    """The digest of a fixture overlay, computed here so callers cannot supply one."""

    return overlay.canonical_digest()
