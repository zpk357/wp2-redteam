"""Formal, independent registry entry for the error-capable scenario (`RA-01`).

The scenario needs a fixture identity of its own: an id, a version, a manifest over everything the
Episodes are drawn from, and a freeze digest that a run can be pinned to.  It deliberately does not
join the retired structured-scenario fixture registry -- that registry held a different object
type and a different scenario family, and merging the two would put old fixtures in the blast radius
of a new one for no gain.

Everything here is a pure function of already-frozen inputs: the registered task families, the
registered attack modes, the Office V2 tool catalogue and the canonical world digest.  Two calls in
the same revision produce the same manifest, and `verify` recomputes rather than trusts.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.error_capable import ATTACK_SPECS, TASK_FAMILY_SPECS, FileRole
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.models import Identifier, OfficeV2Contract, Sha256Digest
from sandbox.scenarios.office_v2.tools import (
    OFFICE_V2_TOOL_NAMES,
    office_v2_tool_definitions,
)

ERROR_CAPABLE_FIXTURE_ID = "substantive-office-01"
#: The manifest schema and the freeze rule belong to the fixture that ships, not to a
#: lineage: one fixture, one identity, and a version bump whenever its material changes.
ERROR_CAPABLE_MANIFEST_VERSION = "error-capable-manifest-v3"
#: v3 because what `task_completed` means changed, not how a breach is judged: completion is
#: now a property of the task family rather than of a per-family variant, so a recorded
#: `task_completed` from v2 and one from v3 are answers to different questions.
ERROR_CAPABLE_ORACLE_CONTRACT_VERSION = "error-capable-oracle-v3"
ERROR_CAPABLE_FREEZE_VERSION = "error-capable-freeze-v2"

#: The action channels the scenario registers, taken from the families themselves rather than
#: restated here, so a family that gains a channel cannot leave the manifest behind.
_ACTION_CHANNELS = tuple(
    sorted({channel.value for spec in TASK_FAMILY_SPECS for channel in spec.action_channels})
)


class ErrorCapableFixtureManifest(OfficeV2Contract):
    """What the Episodes of this fixture may be drawn from."""

    manifest_version: str = ERROR_CAPABLE_MANIFEST_VERSION
    fixture_id: Identifier
    fixture_version: str
    task_families: tuple[Identifier, ...]
    path_ids: tuple[Identifier, ...]
    attack_modes: tuple[Identifier, ...]
    file_roles: tuple[Identifier, ...]
    action_channels: tuple[Identifier, ...]
    tool_names: tuple[Identifier, ...]
    tool_catalogue_digest: Sha256Digest
    oracle_contract_version: str
    world_id: Identifier
    world_version: str
    world_digest: Sha256Digest
    scenario_registry_digest: Sha256Digest | None = None
    manifest_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"manifest_digest"}, exclude_none=True)

    @model_validator(mode="after")
    def digest_matches(self) -> ErrorCapableFixtureManifest:
        if self.manifest_digest != sha256_digest(self.digest_payload()):
            raise ValueError("manifest_digest does not match the manifest")
        return self

    def covers(self, family: str, path_id: str, attack_mode: str) -> bool:
        if family not in self.task_families or attack_mode not in self.attack_modes:
            return False
        spec = next((item for item in TASK_FAMILY_SPECS if item.task_family.value == family), None)
        return spec is not None and path_id in spec.path_ids


class ErrorCapableFixture(OfficeV2Contract):
    """A manifest plus the digest a run may be pinned to."""

    freeze_version: str = ERROR_CAPABLE_FREEZE_VERSION
    manifest: ErrorCapableFixtureManifest
    freeze_digest: Sha256Digest

    @model_validator(mode="after")
    def freeze_matches_manifest(self) -> ErrorCapableFixture:
        expected = sha256_digest(
            {
                "freeze_version": self.freeze_version,
                "manifest_digest": self.manifest.manifest_digest,
                "world_digest": self.manifest.world_digest,
                "tool_catalogue_digest": self.manifest.tool_catalogue_digest,
                "oracle_contract_version": self.manifest.oracle_contract_version,
            }
        )
        if self.freeze_digest != expected:
            raise ValueError("freeze_digest does not match the manifest")
        return self

    @property
    def fixture_id(self) -> str:
        return self.manifest.fixture_id

    @property
    def fixture_version(self) -> str:
        return self.manifest.fixture_version


SUBSTANTIVE_FIXTURE_ORACLE_VERSION = "substantive-office-oracle-v3"


class SubstantiveFixtureManifest(ErrorCapableFixtureManifest):
    """The historical manifest plus the material menu this fixture is drawn from."""

    material_instances: tuple[Identifier, ...]
    attack_phrasings: tuple[Identifier, ...]


def build_substantive_office_fixture() -> ErrorCapableFixture:
    """Assemble the second fixture from its own material and the frozen Office contract."""

    from sandbox.scenarios.error_capable_world import CARRIER_SLOTS, LAYOUT_COUNTS, PATH_TEMPLATES
    from sandbox.scenarios.substantive_office import (
        SUBSTANTIVE_FIXTURE_ID,
        SUBSTANTIVE_FIXTURE_VERSION,
    )
    from sandbox.scenarios.substantive_office.attacks import PHRASING_IDS
    from sandbox.scenarios.substantive_office.manifest import content_manifest
    from sandbox.scenarios.substantive_office.materials import INSTANCES

    definitions = office_v2_tool_definitions()
    if tuple(definitions) != OFFICE_V2_TOOL_NAMES:
        raise RuntimeError("live Office V2 tool catalogue differs from the frozen name list")
    world = load_canonical_world()

    fields = {
        "fixture_id": SUBSTANTIVE_FIXTURE_ID,
        "fixture_version": SUBSTANTIVE_FIXTURE_VERSION,
        "task_families": tuple(spec.task_family.value for spec in TASK_FAMILY_SPECS),
        "path_ids": tuple(path for spec in TASK_FAMILY_SPECS for path in spec.path_ids),
        "attack_modes": tuple(spec.attack_mode.value for spec in ATTACK_SPECS),
        "file_roles": tuple(role.value for role in FileRole),
        "action_channels": _ACTION_CHANNELS,
        "tool_names": tuple(definitions),
        "tool_catalogue_digest": sha256_digest(list(definitions)),
        "oracle_contract_version": SUBSTANTIVE_FIXTURE_ORACLE_VERSION,
        "world_id": world.world_id,
        "world_version": str(world.world_version),
        "world_digest": world.world_digest,
        "material_instances": tuple(item.instance_id for item in INSTANCES),
        "attack_phrasings": tuple(PHRASING_IDS),
        "scenario_registry_digest": sha256_digest(
            {
                "families": [item.model_dump(mode="json") for item in TASK_FAMILY_SPECS],
                "attacks": [item.model_dump(mode="json") for item in ATTACK_SPECS],
                "paths": [item.model_dump(mode="json") for item in PATH_TEMPLATES],
                "carriers": CARRIER_SLOTS,
                "layouts": LAYOUT_COUNTS,
                "instances": [item.instance_id for item in INSTANCES],
                "phrasings": list(PHRASING_IDS),
                "content": content_manifest(),
            }
        ),
    }
    placeholder = SubstantiveFixtureManifest.model_construct(
        **fields, manifest_digest="sha256:" + "0" * 64
    )
    manifest = SubstantiveFixtureManifest(
        **fields, manifest_digest=sha256_digest(placeholder.digest_payload())
    )
    # Constructed rather than validated: the fixture's `manifest` field is typed as the historical
    # manifest, and a normal constructor would coerce this subclass back to it and drop the material
    # menu.  The manifest itself was validated above, digest included, so nothing is unchecked here
    # that the historical fixture does not also check.
    return ErrorCapableFixture.model_construct(
        manifest=manifest,
        freeze_digest=sha256_digest(
            {
                "freeze_version": ERROR_CAPABLE_FREEZE_VERSION,
                "manifest_digest": manifest.manifest_digest,
                "world_digest": manifest.world_digest,
                "tool_catalogue_digest": manifest.tool_catalogue_digest,
                "oracle_contract_version": manifest.oracle_contract_version,
            }
        ),
    )


#: The scenario's own registry.  Kept separate from the retired structured-scenario one on purpose:
#: the two hold different contract types, and RA-01 requires old fixtures to stay untouched.
ERROR_CAPABLE_FIXTURES: dict[str, Callable[[], ErrorCapableFixture]] = {
    ERROR_CAPABLE_FIXTURE_ID: build_substantive_office_fixture,
}


def load_error_capable_fixture(fixture_id: str) -> ErrorCapableFixture:
    builder = ERROR_CAPABLE_FIXTURES.get(fixture_id)
    if builder is None:
        known = sorted(ERROR_CAPABLE_FIXTURES)
        raise KeyError(f"unknown error-capable fixture {fixture_id!r}; registered: {known}")
    return builder()


def verify_error_capable_fixture(fixture: ErrorCapableFixture) -> bool:
    """Rebuild from the frozen inputs and compare.  A drifting manifest must not pass."""

    rebuilt = load_error_capable_fixture(fixture.fixture_id)
    return (
        fixture.manifest.manifest_digest == rebuilt.manifest.manifest_digest
        and fixture.freeze_digest == rebuilt.freeze_digest
    )


__all__ = [
    "ERROR_CAPABLE_FIXTURE_ID",
    "ERROR_CAPABLE_FIXTURE_VERSION",
    "ERROR_CAPABLE_FIXTURES",
    "ERROR_CAPABLE_ORACLE_CONTRACT_VERSION",
    "ErrorCapableFixture",
    "ErrorCapableFixtureManifest",
    "build_error_capable_fixture",
    "load_error_capable_fixture",
    "verify_error_capable_fixture",
]
