"""Formal, independent registry entry for the error-capable scenario (`RA-01`).

The scenario needs a fixture identity of its own: an id, a version, a manifest over everything the
Episodes are drawn from, and a freeze digest that a run can be pinned to.  It deliberately does not
join `sandbox.scenarios.structured_v1.fixtures.FIXTURES` -- that registry holds a different object
type and a different scenario family, and merging the two would put old fixtures in the blast radius
of a new one for no gain.

Everything here is a pure function of already-frozen inputs: the registered task families, the
registered attack modes, the Office V2 tool catalogue and the canonical world digest.  Two calls in
the same revision produce the same manifest, and `verify` recomputes rather than trusts.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pydantic import model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.error_capable import ATTACK_SPECS, TASK_FAMILY_SPECS, FileRole
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.models import Identifier, OfficeV2Contract, Sha256Digest
from sandbox.scenarios.office_v2.tools import (
    OFFICE_V2_TOOL_NAMES,
    office_v2_tool_definitions,
)

ERROR_CAPABLE_FIXTURE_ID = "error-capable-multipath-02"
#: v4 because the routes changed.  Each family offered four paths, one of which finished inside the
#: actor's own workspace -- a route its own family's completion rule rejected, so the index called it
#: a way to finish the work and the scorer called it unfinished.  Those three paths are gone and each
#: family now offers three, all of which can complete it.  The coordinate space is 9 paths rather
#: than 12, so a recorded path set from v3 and one from v4 describe different spaces and cannot be
#: compared cell for cell.  The freeze digest moves with it without anyone having to remember, since
#: it is computed from the families themselves.
#:
#: v4.0.1 because what finishes a family changed without the space changing: `update_calendar_event`
#: was dropped from the calendar family and from its three routes, since neither offered nor named it
#: and nothing in the world was there to update.  The coordinate space is still 9 paths.  It is still
#: a version change rather than an edit in place, because a run that took the update route could
#: otherwise be compared with one that could not.
#:
#: v5.0.0 because one of the five coordinates changed.  The mechanism axis went from ten entries to
#: eight, two of them new, and each now names the violation type it is written to provoke.  A recorded
#: `attack_mode` from v4 and one from v5 describe different menus -- and a run recorded under v4 cannot
#: even be read back through `target_of`, which returns nothing for a mechanism that has left the set
#: rather than guessing at which new one might have taken its place.  The path axis is unchanged at
#: nine; the carrier and layout axes are unchanged; it is the mechanism axis alone that moved, and that
#: is still a coordinate change rather than an edit in place.
ERROR_CAPABLE_FIXTURE_VERSION = "5.0.0"
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


def build_error_capable_fixture() -> ErrorCapableFixture:
    """Assemble the fixture from frozen inputs.

    Pure and deterministic: the only external call is the canonical world load.
    """

    definitions = office_v2_tool_definitions()
    if tuple(definitions) != OFFICE_V2_TOOL_NAMES:
        raise RuntimeError("live Office V2 tool catalogue differs from the frozen name list")

    world = load_canonical_world()
    from sandbox.scenarios.error_capable_world import CARRIER_SLOTS, LAYOUT_COUNTS, PATH_TEMPLATES

    fields = {
        "fixture_id": ERROR_CAPABLE_FIXTURE_ID,
        "fixture_version": ERROR_CAPABLE_FIXTURE_VERSION,
        "task_families": tuple(spec.task_family.value for spec in TASK_FAMILY_SPECS),
        "path_ids": tuple(path for spec in TASK_FAMILY_SPECS for path in spec.path_ids),
        "attack_modes": tuple(spec.attack_mode.value for spec in ATTACK_SPECS),
        "file_roles": tuple(role.value for role in FileRole),
        "action_channels": _ACTION_CHANNELS,
        "tool_names": tuple(definitions),
        "tool_catalogue_digest": sha256_digest(list(definitions)),
        "oracle_contract_version": ERROR_CAPABLE_ORACLE_CONTRACT_VERSION,
        "world_id": world.world_id,
        "world_version": str(world.world_version),
        "world_digest": world.world_digest,
        "scenario_registry_digest": sha256_digest(
            {
                "families": [item.model_dump(mode="json") for item in TASK_FAMILY_SPECS],
                "attacks": [item.model_dump(mode="json") for item in ATTACK_SPECS],
                "paths": [item.model_dump(mode="json") for item in PATH_TEMPLATES],
                "carriers": CARRIER_SLOTS,
                "layouts": LAYOUT_COUNTS,
            }
        ),
    }
    # The digest is self-validating, so it is sealed from a placed digest rather than assigned and
    # then re-checked -- the same two-step `seal_model_inference_options` uses.
    placeholder = ErrorCapableFixtureManifest.model_construct(
        **fields, manifest_digest="sha256:" + "0" * 64
    )
    manifest = ErrorCapableFixtureManifest(
        **fields, manifest_digest=sha256_digest(placeholder.digest_payload())
    )
    return ErrorCapableFixture(
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


#: The scenario's own registry.  Separate from `structured_v1.fixtures.FIXTURES` on purpose: the two
#: hold different contract types, and RA-01 requires old fixtures to stay untouched.
ERROR_CAPABLE_FIXTURES: dict[str, Callable[[], ErrorCapableFixture]] = {
    ERROR_CAPABLE_FIXTURE_ID: build_error_capable_fixture,
    "error-capable-multipath-01": lambda: ErrorCapableFixture.model_validate_json(
        Path(__file__).with_name("error_capable_legacy_fixture.json").read_text(encoding="utf-8")
    ),
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
