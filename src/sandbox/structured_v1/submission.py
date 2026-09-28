"""Host-side assembly of one structured submission (R2; search contract `SS-002`).

The host owns two things the container must not take on faith: the **envelope** (the case as
the host orders it, with the declared initial state the container re-derives) and the
**request** that carries it.  Both are built here from the fixture asset and the candidate, so
the same construction serves the offline rehearsal, the container run and the two-arm loop.

Nothing here decides anything: the material is rendered and materialised exactly as the
episode will re-derive it, and every identity the envelope declares is computed, not copied
from a caller's claim.
"""

from __future__ import annotations

from dataclasses import dataclass

from sandbox.protocol import ExecutionRequest, ModelOptions
from sandbox.scenarios.office_v2.canonical_world import CanonicalOfficeWorld
from sandbox.structured_v1.budget import RunBudgetEnvelope
from sandbox.structured_v1.container import StructuredCaseRequest
from sandbox.structured_v1.envelope import (
    ActorIdentity,
    AuthorisationContext,
    OracleScope,
    ScheduleIdentity,
    StructuredEnvelope,
    implied_obligations,
)
from sandbox.structured_v1.fixture import StructuredFixtureManifest
from sandbox.structured_v1.models import Identifier, StructuredCase
from sandbox.structured_v1.rendering import REFERENCE_SYNTAX_VERSION, render_material
from sandbox.structured_v1.tool_catalogue import (
    ToolingIdentity,
    build_office_v2_catalogue,
)
from sandbox.structured_v1.world import (
    FixtureWorldOverlay,
    MaterializedWorld,
    materialize_world,
)


@dataclass(frozen=True)
class PreparedSubmission:
    """One assembled episode: the envelope it will run under and the state it declares."""

    envelope: StructuredEnvelope
    materialized: MaterializedWorld


def build_envelope(
    case: StructuredCase,
    *,
    manifest: StructuredFixtureManifest,
    base_world: CanonicalOfficeWorld,
    overlay: FixtureWorldOverlay,
    budget: RunBudgetEnvelope,
    episode_id: str,
    arm_id: str,
    generation: int = 0,
    case_id: str | None = None,
    attempt: int = 0,
    tooling: ToolingIdentity | None = None,
) -> PreparedSubmission:
    """Render and materialise the case, then declare exactly what was produced.

    The declared initial state is the state this call derived, so a container that re-derives
    a different one refuses the envelope instead of running a world the host never saw.
    """

    if case.fixture_id != manifest.fixture_id:
        raise ValueError("the case belongs to a different fixture")
    if case.manifest_digest != manifest.manifest_digest:
        raise ValueError("the case was built for a different manifest")

    material = render_material(case, manifest)
    materialized = materialize_world(
        manifest,
        material,
        base_world=base_world,
        overlay=overlay,
        episode_id=episode_id,
    )
    organization = base_world.state.domain_graph.directory.organization
    actor_id = manifest.actor_id or organization.organization_id
    envelope = StructuredEnvelope(
        syntax_version=REFERENCE_SYNTAX_VERSION,
        manifest=manifest,
        manifest_digest=manifest.manifest_digest,
        task_text=manifest.public_task_text,
        actor=ActorIdentity(
            principal_id=actor_id,
            kind="user",
            organization_id=organization.organization_id,
        ),
        authorisation=AuthorisationContext(
            task_authorized=manifest.task_authorized,
            data_audience=manifest.data_audience,
            required_delivery=manifest.required_delivery,
        ),
        oracle_scope=OracleScope(
            obligations=tuple(sorted(implied_obligations(manifest), key=lambda item: item.value))
        ),
        material=material,
        overlay=overlay,
        base_locator=_base_locator(base_world),
        base_world_digest=base_world.world_digest,
        declared_initial_state_digest=materialized.initial_state_digest,
        declared_initialization_transition_digest=(
            materialized.initialization_transition_digest
        ),
        tooling=tooling
        or ToolingIdentity(
            tool_catalogue_digest=build_office_v2_catalogue().catalogue_digest
        ),
        schedule=ScheduleIdentity(
            episode_id=episode_id,
            arm_id=arm_id,
            generation=generation,
            case_id=case_id or _case_id(case),
        ),
        budget=budget,
        attempt=attempt,
    )
    return PreparedSubmission(envelope=envelope, materialized=materialized)


def build_execution_request(
    envelope: StructuredEnvelope,
    *,
    execution_id: str,
    model: ModelOptions,
    timeout_seconds: int,
    request_id: str = "request-0001",
    seed: int | None = None,
    max_steps: int = 20,
    max_tool_calls: int = 24,
) -> ExecutionRequest:
    """The host's request: the new protocol kind plus the model identity that will drive it."""

    structured = StructuredCaseRequest(request_id=request_id, envelope=envelope)
    return ExecutionRequest(
        execution_id=execution_id,
        case_id=envelope.schedule.case_id,
        prompt=envelope.task_text,
        max_steps=max_steps,
        timeout_seconds=timeout_seconds,
        max_tool_calls=max_tool_calls,
        seed=seed,
        model=model,
        structured_case_execution=structured.model_dump(mode="json"),
    )


def _case_id(case: StructuredCase) -> Identifier:
    return case.mutation_lineage.generation_identity


def _base_locator(base_world: CanonicalOfficeWorld) -> str:
    from sandbox.structured_v1.assets import FrozenWorldAsset

    return FrozenWorldAsset(world=base_world).locator


__all__ = ["PreparedSubmission", "build_envelope", "build_execution_request"]
