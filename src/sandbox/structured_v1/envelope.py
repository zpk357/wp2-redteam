"""The structured-scenario execution envelope (draft §4, §7; `SOC-ENV-01`–`SOC-ENV-33`).

This module is the only way an episode gets to run. ``verify_envelope`` either returns a
:class:`VerifiedEnvelope` or raises :class:`EnvelopeRefusal` carrying a
:class:`~sandbox.structured_v1.envelope_codes.FailureCode`; there is no third outcome and
no partial path a caller could proceed on. A verified envelope that has **not** been
checked against its frozen assets reports ``runnable is False``, and
:func:`require_runnable` refuses it, so a checked identity alone cannot start a real run.

The envelope carries **objects, not only digests** (§4.1): the trusted task text, the
execution actor, the authorisation context, the fixture manifest, the overlay and the
material by value; the base world by locator into the container's own frozen store; and
the initial state as a *declared* digest the container re-derives (§4.2). Digests are
recomputed here rather than trusted, and every refusal names the check that failed.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.canonical_world import CanonicalOfficeWorld
from sandbox.structured_v1.assets import FrozenAssetStore, overlay_digest
from sandbox.structured_v1.budget import RunBudgetEnvelope
from sandbox.structured_v1.envelope_codes import EnvelopeRefusal, FailureCode
from sandbox.structured_v1.fixture import (
    DataAudienceRule,
    StructuredFixtureManifest,
    verify_fixture_manifest,
)
from sandbox.structured_v1.models import (
    STRUCTURED_SCHEMA_VERSION,
    Identifier,
    Sha256Digest,
    StructuredContract,
)
from sandbox.structured_v1.obligations import ObligationId
from sandbox.structured_v1.redaction import registered_file_digest
from sandbox.structured_v1.rendering import REFERENCE_SYNTAX_VERSION, RenderedMaterial
from sandbox.structured_v1.tool_catalogue import (
    ToolingFacts,
    ToolingIdentity,
    verify_tooling,
)
from sandbox.structured_v1.world import (
    CANONICAL_FIELD_PATHS,
    FixtureWorldOverlay,
    MaterializationError,
    SlotFieldKind,
    materialize_world,
)

PROTOCOL_ID = "structured-scenario"
ENVELOPE_VERSION = "envelope-v1"

#: Names of the check groups a verification performed; recorded on the proof so a caller
#: can see what was actually established instead of assuming it.
CHECK_IDENTITY = "identity"
CHECK_FIXTURE = "fixture"
CHECK_CONTEXT = "context"
CHECK_SCOPE = "scope"
CHECK_MATERIAL = "material"
CHECK_OVERLAY = "overlay"
CHECK_BUDGET = "budget"
CHECK_BASE_WORLD = "base-world"
CHECK_MATERIALISATION = "materialisation"
CHECK_CHAIN = "chain"


class ActorIdentity(StructuredContract):
    """Who executes the episode (`SOC-ENV-22`)."""

    principal_id: Identifier
    kind: str = Field(min_length=1, max_length=32)
    organization_id: Identifier | None = None


class AuthorisationContext(StructuredContract):
    """The audience sets that drive `AUDIENCE_SCOPE` and `DATA_RELEASE` (`SOC-ENV-23`)."""

    task_authorized: tuple[Identifier, ...] = Field(min_length=1)
    data_audience: tuple[DataAudienceRule, ...] = ()
    required_delivery: tuple[Identifier, ...] = Field(min_length=1)


class OracleScope(StructuredContract):
    """Which obligations this episode is judged on: the fixture's, never a catalogue.

    The tuple is stored in canonical order, so comparing a scope with the fixture's own
    set never depends on the order it was written in.
    """

    obligations: tuple[ObligationId, ...] = Field(min_length=1)

    @field_validator("obligations")
    @classmethod
    def obligations_are_canonical(
        cls, value: tuple[ObligationId, ...]
    ) -> tuple[ObligationId, ...]:
        ordered = tuple(sorted(value, key=lambda item: item.value))
        if len(set(ordered)) != len(ordered):
            raise ValueError("an oracle scope must not repeat an obligation")
        return ordered


class ScheduleIdentity(StructuredContract):
    """Which slot of the experiment this episode occupies."""

    episode_id: Identifier
    arm_id: Identifier
    generation: int = Field(ge=0)
    case_id: Identifier


class StructuredEnvelope(StructuredContract):
    """Everything one episode needs, plus the identities that make it checkable."""

    protocol_id: Literal["structured-scenario"] = PROTOCOL_ID
    envelope_version: Literal["envelope-v1"] = ENVELOPE_VERSION
    schema_version: Literal["structured-v1.0"] = STRUCTURED_SCHEMA_VERSION
    syntax_version: Identifier

    manifest: StructuredFixtureManifest
    manifest_digest: Sha256Digest

    task_text: str = Field(min_length=1, max_length=4000)
    actor: ActorIdentity
    authorisation: AuthorisationContext
    oracle_scope: OracleScope

    material: RenderedMaterial
    overlay: FixtureWorldOverlay
    base_locator: str = Field(min_length=1, max_length=256)
    base_world_digest: Sha256Digest
    declared_initial_state_digest: Sha256Digest
    declared_initialization_transition_digest: Sha256Digest

    tooling: ToolingIdentity
    schedule: ScheduleIdentity
    budget: RunBudgetEnvelope
    attempt: int = Field(default=0, ge=0)


class VerifiedEnvelope(StructuredContract):
    """Proof that an envelope passed its checks; a session may not run without one."""

    envelope_digest: Sha256Digest
    checks: tuple[str, ...]
    materialization_verified: bool
    tooling_verified: bool

    @property
    def runnable(self) -> bool:
        """True only when the frozen assets were checked, not just the identities."""

        return self.materialization_verified and self.tooling_verified

    @property
    def unverified(self) -> tuple[str, ...]:
        missing: list[str] = []
        if not self.materialization_verified:
            missing.append(CHECK_MATERIALISATION)
        if not self.tooling_verified:
            missing.append("tooling")
        return tuple(missing)


def require_runnable(verified: VerifiedEnvelope) -> None:
    """Refuse to start a run on an envelope whose assets were never checked."""

    if not verified.runnable:
        raise EnvelopeRefusal(
            FailureCode.PAYLOAD_MISSING,
            f"the envelope is not runnable; unverified: {', '.join(verified.unverified)}",
        )


def implied_obligations(manifest: StructuredFixtureManifest) -> frozenset[ObligationId]:
    """The obligations this fixture can actually be judged on.

    Derived from the fixture's own declarations: registered content classes make
    `DATA_RELEASE` judgeable, declared delivery targets make `AUDIENCE_SCOPE` judgeable.
    A scope that disagrees with this is not the fixture's - which is what
    ``envelope.objective_catalogue_present`` exists to catch.
    """

    implied: set[ObligationId] = set()
    if manifest.registered_units or manifest.registered_files:
        implied.add(ObligationId.DATA_RELEASE)
    if manifest.required_delivery:
        implied.add(ObligationId.AUDIENCE_SCOPE)
    return frozenset(implied)


def verify_envelope(
    envelope: StructuredEnvelope,
    *,
    assets: FrozenAssetStore | None = None,
    tooling: ToolingFacts | None = None,
    base_world: CanonicalOfficeWorld | None = None,
) -> VerifiedEnvelope:
    """Check the envelope and return the proof, or refuse with a code.

    ``assets`` is the container's own store: the base world is resolved from the
    envelope's locator **inside it** and the frozen ``fixture → base/overlay`` mapping is
    checked. ``tooling`` carries what the container can observe about the tools it runs.
    Without them the identity-level checks still run, but the proof is not ``runnable``
    and :func:`require_runnable` refuses to start.
    """

    checks: list[str] = []

    _require_identity(envelope)
    checks.append(CHECK_IDENTITY)
    _require_fixture(envelope)
    checks.append(CHECK_FIXTURE)
    _require_context(envelope)
    checks.append(CHECK_CONTEXT)
    _require_scope(envelope)
    checks.append(CHECK_SCOPE)
    _require_material(envelope)
    checks.append(CHECK_MATERIAL)
    _require_overlay(envelope)
    checks.append(CHECK_OVERLAY)
    if envelope.manifest.session_protocol is not None:
        try:
            envelope.manifest.session_protocol.budgets(envelope.budget)
        except ValueError as error:
            _refuse(FailureCode.CONTEXT_MISMATCH, str(error))
    checks.append(CHECK_BUDGET)

    if assets is not None:
        base_world = assets.resolve(envelope.base_locator)
        checks.append("assets")
        _require_mapping(envelope, assets)
        checks.append("mapping")

    materialization_verified = False
    if base_world is not None:
        _require_base_world(envelope, base_world)
        checks.append(CHECK_BASE_WORLD)
        _require_reproducible_initial_state(envelope, base_world)
        checks.append(CHECK_MATERIALISATION)
        checks.append(CHECK_CHAIN)
        materialization_verified = True

    tooling_verified = False
    if tooling is not None:
        verify_tooling(envelope.tooling, tooling)
        checks.append("tooling")
        tooling_verified = True

    return VerifiedEnvelope(
        envelope_digest=envelope.canonical_digest(),
        checks=tuple(checks),
        materialization_verified=materialization_verified,
        tooling_verified=tooling_verified,
    )


def _require_mapping(envelope: StructuredEnvelope, assets: FrozenAssetStore) -> None:
    """The fixture must run against the base and overlay it was frozen with."""

    mapping = assets.mapping_for(envelope.manifest.fixture_id)
    if mapping is None:
        _refuse(
            FailureCode.PAYLOAD_MISSING,
            f"no frozen mapping for fixture {envelope.manifest.fixture_id}",
        )
    if mapping.fixture_version != envelope.manifest.fixture_version:
        _refuse(FailureCode.FIXTURE_MISMATCH, "the frozen mapping pins another fixture version")
    if mapping.manifest_digest != envelope.manifest.manifest_digest:
        _refuse(FailureCode.FIXTURE_MISMATCH, "the frozen mapping pins another manifest")
    if mapping.base_locator != envelope.base_locator:
        _refuse(FailureCode.FIXTURE_MISMATCH, "the frozen mapping pins another base locator")
    if mapping.base_world_digest != envelope.base_world_digest:
        _refuse(FailureCode.FIXTURE_MISMATCH, "the frozen mapping pins another base world")
    if mapping.overlay_digest != overlay_digest(envelope.overlay):
        _refuse(FailureCode.FIXTURE_MISMATCH, "the overlay is not the frozen overlay")


def _refuse(code: FailureCode, detail: str) -> None:
    raise EnvelopeRefusal(code, detail)


def _require_identity(envelope: StructuredEnvelope) -> None:
    if envelope.protocol_id != PROTOCOL_ID:
        _refuse(FailureCode.IDENTITY_MISMATCH, f"protocol_id={envelope.protocol_id}")
    if envelope.envelope_version != ENVELOPE_VERSION:
        _refuse(FailureCode.IDENTITY_MISMATCH, f"envelope_version={envelope.envelope_version}")
    if envelope.schema_version != STRUCTURED_SCHEMA_VERSION:
        _refuse(FailureCode.IDENTITY_MISMATCH, f"schema_version={envelope.schema_version}")
    if envelope.syntax_version != REFERENCE_SYNTAX_VERSION:
        _refuse(FailureCode.IDENTITY_MISMATCH, f"syntax_version={envelope.syntax_version}")


def _require_fixture(envelope: StructuredEnvelope) -> None:
    manifest = envelope.manifest
    if not verify_fixture_manifest(manifest):
        _refuse(FailureCode.FIXTURE_MISMATCH, "the manifest does not match its own digest")
    if envelope.manifest_digest != manifest.manifest_digest:
        _refuse(FailureCode.FIXTURE_MISMATCH, "manifest_digest does not match the manifest")


def _require_context(envelope: StructuredEnvelope) -> None:
    manifest = envelope.manifest
    if envelope.task_text != manifest.public_task_text:
        _refuse(FailureCode.CONTEXT_MISMATCH, "the task text is not the manifest's")
    if manifest.actor_id is not None and envelope.actor.principal_id != manifest.actor_id:
        _refuse(FailureCode.CONTEXT_MISMATCH, "the actor is not the fixture's actor")
    if envelope.authorisation.task_authorized != manifest.task_authorized:
        _refuse(FailureCode.CONTEXT_MISMATCH, "task_authorized differs from the manifest")
    if envelope.authorisation.data_audience != manifest.data_audience:
        _refuse(FailureCode.CONTEXT_MISMATCH, "data_audience differs from the manifest")
    if envelope.authorisation.required_delivery != manifest.required_delivery:
        _refuse(FailureCode.CONTEXT_MISMATCH, "required_delivery differs from the manifest")


def _require_scope(envelope: StructuredEnvelope) -> None:
    declared = frozenset(envelope.oracle_scope.obligations)
    implied = implied_obligations(envelope.manifest)
    if declared != implied:
        _refuse(
            FailureCode.OBJECTIVE_CATALOGUE_PRESENT,
            "the oracle scope is not the fixture's own obligation set: "
            f"declared={sorted(item.value for item in declared)}, "
            f"implied={sorted(item.value for item in implied)}",
        )


def _require_material(envelope: StructuredEnvelope) -> None:
    manifest = envelope.manifest
    material = envelope.material
    if material.fixture_id != manifest.fixture_id:
        _refuse(FailureCode.FIXTURE_MISMATCH, "the material belongs to another fixture")
    if material.manifest_digest != manifest.manifest_digest:
        _refuse(FailureCode.FIXTURE_MISMATCH, "the material came from another manifest")
    if material.syntax_version != REFERENCE_SYNTAX_VERSION:
        _refuse(
            FailureCode.COVERAGE_MISMATCH,
            f"unregistered reference syntax {material.syntax_version}",
        )
    declared = [slot.slot_id for slot in material.slots]
    if len(declared) != len(set(declared)):
        _refuse(FailureCode.COVERAGE_MISMATCH, "the material repeats a slot")
    if set(declared) != {profile.slot_id for profile in manifest.slots}:
        _refuse(FailureCode.COVERAGE_MISMATCH, "the material does not cover the fixture slots")
    if sha256_digest(material.digest_payload()) != material.material_digest:
        _refuse(FailureCode.COVERAGE_MISMATCH, "the material digest does not match its content")


def _require_overlay(envelope: StructuredEnvelope) -> None:
    overlay = envelope.overlay
    if set(overlay.fixed_registered_reads) != set(envelope.manifest.fixed_registered_files):
        _refuse(FailureCode.FIXTURE_MISMATCH, "the fixed registered file set differs")
    registered = {profile.slot_id for profile in envelope.manifest.slots}
    targeted = {target.slot_id for target in overlay.slot_targets}
    if targeted != registered:
        _refuse(FailureCode.COVERAGE_MISMATCH, "the overlay does not target exactly the slots")
    locations = [target.write_location for target in overlay.slot_targets]
    if len(locations) != len(set(locations)):
        _refuse(FailureCode.WRITE_CONFLICT, "two slots would write to the same world field")
    for target in overlay.slot_targets:
        canonical = CANONICAL_FIELD_PATHS[target.field_kind]
        if target.field_path is not None and target.field_path != canonical:
            _refuse(
                FailureCode.FIELD_PATH_MISMATCH,
                f"slot {target.slot_id} declares {target.field_path!r} for "
                f"{target.field_kind.value}, which is written at {canonical!r}",
            )
    files = {item.file_id for item in overlay.drive_files}
    paths = {item.path for item in overlay.workspace_files}
    for target in overlay.slot_targets:
        declared = (
            paths
            if target.field_kind is SlotFieldKind.WORKSPACE_FILE_CONTENT
            else files
        )
        if target.object_id not in declared:
            _refuse(
                FailureCode.COVERAGE_MISMATCH,
                f"slot {target.slot_id} targets an object the overlay does not create",
            )
    for file_id in overlay.fixed_registered_reads:
        file = next((item for item in overlay.drive_files if item.file_id == file_id), None)
        registered = next(
            (item for item in envelope.manifest.registered_files if item.file_id == file_id),
            None,
        )
        if (file is None or registered is None
                or registered.content_digest != registered_file_digest(file.content)):
            _refuse(
                FailureCode.FIXTURE_MISMATCH,
                f"fixed registered file {file_id} does not match its frozen content",
            )


def _require_base_world(envelope: StructuredEnvelope, base_world: CanonicalOfficeWorld) -> None:
    if base_world.world_digest != envelope.base_world_digest:
        _refuse(
            FailureCode.BASE_WORLD_MISMATCH,
            f"the world handed over is {base_world.world_digest}, not {envelope.base_world_digest}",
        )


def _require_reproducible_initial_state(
    envelope: StructuredEnvelope,
    base_world: CanonicalOfficeWorld,
) -> None:
    """Re-derive the initial state: a digest set that does not reproduce is refused.

    Everything that could make the materialisation itself fail was already checked above,
    so a failure here means the declared state is not the state the rules produce - which
    is exactly what ``envelope.initial_state_not_reproducible`` is for.
    """

    try:
        materialized: Any = materialize_world(
            envelope.manifest,
            envelope.material,
            base_world=base_world,
            overlay=envelope.overlay,
            episode_id=envelope.schedule.episode_id,
            expected_base_world_digest=envelope.base_world_digest,
        )
    except MaterializationError as error:
        _refuse(FailureCode.INITIAL_STATE_NOT_REPRODUCIBLE, str(error))

    if materialized.initial_state_digest != envelope.declared_initial_state_digest:
        _refuse(
            FailureCode.INITIAL_STATE_NOT_REPRODUCIBLE,
            "the derived initial state is not the declared one",
        )
    if materialized.initialization_transition_digest != (
        envelope.declared_initialization_transition_digest
    ):
        _refuse(
            FailureCode.INITIAL_STATE_NOT_REPRODUCIBLE,
            "the derived initialization transition is not the declared one",
        )
    transition = materialized.initialization_transition
    if transition.before_state_digest != base_world.state.canonical_digest():
        _refuse(
            FailureCode.CHAIN_BROKEN,
            "the initialization transition does not start at the base",
        )
    if transition.after_state_digest != materialized.initial_state_digest:
        _refuse(
            FailureCode.CHAIN_BROKEN,
            "the initialization transition does not end at the state",
        )
