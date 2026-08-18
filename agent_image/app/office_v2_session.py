"""Container-side owner of one Office V2 Episode state and tool runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from sandbox.protocol import TraceEvent, V2ExecutionEnvelope, V2ScenarioCaseKind
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.agent_api import OfficeV2AgentSessionSurface
from sandbox.scenarios.office_v2.agent_context import AgentRenderedSystemPrompt
from sandbox.scenarios.office_v2.attack_models import MaterializedScenarioCase
from sandbox.scenarios.office_v2.canonical_world import OfficeWorldState
from sandbox.scenarios.office_v2.clean_cases import CleanCaseMaterialization
from sandbox.scenarios.office_v2.interaction_session import (
    InteractionControlExecution,
)
from sandbox.scenarios.office_v2.models import Identifier, OfficeV2Contract, Sha256Digest
from sandbox.scenarios.office_v2.oracle import (
    evaluate_clean_scenario_oracle,
    evaluate_scenario_oracle,
)
from sandbox.scenarios.office_v2.oracle_evidence import (
    InteractionEvidenceFact,
    InteractionEvidenceKind,
    OracleEvidenceBundle,
    build_interaction_evidence_fact,
    build_termination_fact,
)
from sandbox.scenarios.office_v2.oracle_models import (
    CompleteScenarioOracleResult,
    StateTransitionEvidenceRef,
)
from sandbox.scenarios.office_v2.oracle_trace import build_oracle_evidence_from_trace
from sandbox.scenarios.office_v2.tools import office_v2_tool_definitions
from sandbox.scenarios.office_v2.tools.contracts import (
    ArgumentSource,
    OfficeToolInvocation,
    OfficeToolResult,
)
from sandbox.scenarios.office_v2.tools.provenance import infer_exact_argument_sources
from sandbox.scenarios.office_v2.tools.runtime import OfficeV2ToolRuntime
from sandbox.scenarios.office_v2.world import EpisodeWorld, StateObjectKind, StateTransitionRecord

OFFICE_V2_SESSION_SNAPSHOT_VERSION = "office-v2-session-snapshot-v1"
OFFICE_V2_LIVE_ORACLE_ARTIFACT_VERSION = "office-v2-live-oracle-artifact-v1"
OFFICE_V2_RECORDING_STATE_VERSION = "office-v2-recording-state-v1"


class OfficeV2LiveOracleArtifact(OfficeV2Contract):
    artifact_version: Literal["office-v2-live-oracle-artifact-v1"] = (
        OFFICE_V2_LIVE_ORACLE_ARTIFACT_VERSION
    )
    execution_id: Identifier
    trace_digest: Sha256Digest
    trusted_facts_digest: Sha256Digest
    evidence_bundle: OracleEvidenceBundle
    oracle_result: CompleteScenarioOracleResult
    artifact_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={"artifact_digest"},
            exclude_none=False,
        )

    @model_validator(mode="after")
    def identities_and_digest_match(self) -> Self:
        if self.oracle_result.input_bundle_digest != self.evidence_bundle.bundle_digest:
            raise ValueError("live Oracle result does not match evidence bundle")
        if self.artifact_digest != sha256_digest(self.digest_payload()):
            raise ValueError("live Oracle artifact digest does not match payload")
        return self


class OfficeV2SessionSnapshot(OfficeV2Contract):
    snapshot_version: Literal["office-v2-session-snapshot-v1"] = (
        OFFICE_V2_SESSION_SNAPSHOT_VERSION
    )
    execution_envelope_digest: Sha256Digest
    episode_id: Identifier
    base_world_digest: Sha256Digest
    initial_state_digest: Sha256Digest
    state: OfficeWorldState
    history: tuple[StateTransitionRecord, ...] = Field(default_factory=tuple)
    state_digest: Sha256Digest
    snapshot_digest: Sha256Digest

    @field_validator("history")
    @classmethod
    def history_has_unique_transaction_ids(
        cls, value: tuple[StateTransitionRecord, ...]
    ) -> tuple[StateTransitionRecord, ...]:
        ids = tuple(item.transaction_id for item in value)
        if len(ids) != len(set(ids)):
            raise ValueError("session history contains duplicate transaction ids")
        return value

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={"snapshot_digest"},
            exclude_none=False,
        )

    @model_validator(mode="after")
    def state_history_and_digest_match(self) -> Self:
        if self.state.canonical_digest() != self.state_digest:
            raise ValueError("session state digest does not match state")
        current = self.initial_state_digest
        for record in self.history:
            if record.before_state_digest != current:
                raise ValueError("session history is not contiguous")
            current = record.after_state_digest
        if current != self.state_digest:
            raise ValueError("session history final digest does not match state")
        if self.snapshot_digest != sha256_digest(self.digest_payload()):
            raise ValueError("session snapshot digest does not match payload")
        return self


class OfficeV2RecordedInteractionEvent(OfficeV2Contract):
    event_type: Literal[
        "agent_clarification_requested",
        "user_response_received",
        "interaction_result",
        "delegation_grant_created",
    ]
    data: dict[str, Any]
    logical_time: int = Field(ge=0)
    input_digest: Sha256Digest
    output_digest: Sha256Digest
    state_digest: Sha256Digest


class OfficeV2RecordingState(OfficeV2Contract):
    recording_state_version: Literal["office-v2-recording-state-v1"] = (
        OFFICE_V2_RECORDING_STATE_VERSION
    )
    session: OfficeV2SessionSnapshot
    tool_invocations: tuple[OfficeToolInvocation, ...] = Field(default_factory=tuple)
    tool_results: tuple[OfficeToolResult, ...] = Field(default_factory=tuple)
    interaction_events: tuple[OfficeV2RecordedInteractionEvent, ...] = Field(
        default_factory=tuple
    )
    pending_clarification_request_ids: tuple[Identifier, ...] = Field(default_factory=tuple)
    recording_state_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={"recording_state_digest"},
            exclude_none=False,
        )

    @model_validator(mode="after")
    def trusted_facts_and_digest_match(self) -> Self:
        if len(self.tool_invocations) != len(self.tool_results):
            raise ValueError("recording tool invocation/result counts differ")
        for invocation, result in zip(self.tool_invocations, self.tool_results, strict=True):
            if (
                invocation.invocation_id != result.invocation_id
                or invocation.sequence != result.sequence
                or invocation.tool_name != result.tool_name
                or invocation.before_state_digest != result.before_state_digest
            ):
                raise ValueError("recording tool invocation/result identities differ")
        if len(self.pending_clarification_request_ids) != len(
            set(self.pending_clarification_request_ids)
        ):
            raise ValueError("pending clarification request ids must be unique")
        event_types = tuple(item.event_type for item in self.interaction_events)
        if event_types.count("agent_clarification_requested") != event_types.count(
            "interaction_result"
        ):
            raise ValueError("recording interaction request/result counts differ")
        created_grants = sum(
            item.kind is StateObjectKind.DELEGATION_GRANT
            for transition in self.session.history
            for item in transition.state_delta.created_objects
        )
        if event_types.count("delegation_grant_created") != created_grants:
            raise ValueError("recording grant events do not match state transitions")
        if self.recording_state_digest != sha256_digest(self.digest_payload()):
            raise ValueError("recording state digest does not match payload")
        return self


@dataclass(frozen=True, slots=True)
class OfficeV2ContainerSession:
    envelope: V2ExecutionEnvelope
    scenario_case: MaterializedScenarioCase | CleanCaseMaterialization
    episode: EpisodeWorld
    runtime: OfficeV2ToolRuntime
    initialization_transition: StateTransitionRecord | None
    _trusted_tool_results: list[OfficeToolResult] = field(
        default_factory=list,
        repr=False,
        compare=False,
    )
    _trusted_interactions: list[InteractionControlExecution] = field(
        default_factory=list,
        repr=False,
        compare=False,
    )

    @property
    def trusted_tool_results(self) -> tuple[OfficeToolResult, ...]:
        """Return trusted execution facts without exposing a mutable sidecar."""

        return tuple(self._trusted_tool_results)

    @property
    def trusted_interactions(self) -> tuple[InteractionControlExecution, ...]:
        return tuple(self._trusted_interactions)

    def record_trusted_interaction(self, execution: InteractionControlExecution) -> None:
        if self._trusted_interactions and execution is self._trusted_interactions[-1]:
            raise ValueError(
                "v2_data_integrity_error: trusted interaction was observed twice"
            )
        self._trusted_interactions.append(execution)

    def build_live_oracle_artifact(
        self,
        *,
        trace_events: tuple[TraceEvent, ...],
        final_answer: str,
    ) -> OfficeV2LiveOracleArtifact:
        if len(self.runtime.invocations) != len(self._trusted_tool_results):
            raise ValueError(
                "v2_data_integrity_error: trusted tool sidecar is incomplete"
            )
        interaction_facts = _interaction_evidence_facts(self._trusted_interactions)
        termination = build_termination_fact(
            evidence_id=_live_evidence_id("termination", self.episode.episode_id),
            sequence=len(self.runtime.invocations) + len(interaction_facts),
            reason="submit",
            submitted=True,
            output_digest=sha256_digest(final_answer),
        )
        bundle = build_oracle_evidence_from_trace(
            scenario_case=self.scenario_case,
            initialization_transition=self.initialization_transition,
            trace_events=trace_events,
            invocations=tuple(self.runtime.invocations),
            results=tuple(self._trusted_tool_results),
            interaction_facts=interaction_facts,
            termination=termination,
            initial_state_digest=self.envelope.initial_state_digest,
            final_state_digest=self.episode.state_digest,
        )
        if isinstance(self.scenario_case, MaterializedScenarioCase):
            result = evaluate_scenario_oracle(
                bundle=bundle,
                scenario_case=self.scenario_case,
            )
        else:
            result = evaluate_clean_scenario_oracle(
                bundle=bundle,
                clean_case=self.scenario_case,
            )
        payload = {
            "execution_id": self.episode.episode_id,
            "trace_digest": sha256_digest(
                tuple(
                    event.model_dump(mode="json", exclude_none=False)
                    for event in trace_events
                )
            ),
            "trusted_facts_digest": sha256_digest(
                {
                    "invocations": self.runtime.invocations,
                    "results": self._trusted_tool_results,
                    "interactions": interaction_facts,
                }
            ),
            "evidence_bundle": bundle,
            "oracle_result": result,
        }
        draft = OfficeV2LiveOracleArtifact.model_construct(
            **payload,
            artifact_digest="sha256:" + "0" * 64,
        )
        return OfficeV2LiveOracleArtifact(
            **payload,
            artifact_digest=sha256_digest(draft.digest_payload()),
        )

    def build_agent_surface(
        self,
        *,
        rendered_prompt: AgentRenderedSystemPrompt,
        control_tool_specs: tuple[Any, ...],
        control_handler: Callable[[str, dict[str, Any]], Any],
        business_result_observer: Callable[[OfficeToolResult], None] | None = None,
    ) -> OfficeV2AgentSessionSurface:
        """Bind the frozen V2 Agent API to this session's only tool runtime."""

        def observe(result: OfficeToolResult) -> None:
            self._record_trusted_tool_result(result)
            if business_result_observer is not None:
                business_result_observer(result)

        return OfficeV2AgentSessionSurface(
            rendered_prompt=rendered_prompt,
            runtime=self.runtime,
            control_tool_specs=control_tool_specs,
            control_handler=control_handler,
            business_result_observer=observe,
            argument_source_resolver=self._resolve_argument_sources,
        )

    def _resolve_argument_sources(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> tuple[ArgumentSource, ...]:
        del tool_name
        return infer_exact_argument_sources(arguments, self._trusted_tool_results)

    def _record_trusted_tool_result(self, result: OfficeToolResult) -> None:
        if not self.runtime.results or result is not self.runtime.results[-1]:
            raise ValueError(
                "v2_data_integrity_error: observed tool result is not the latest "
                "session runtime result"
            )
        if self._trusted_tool_results and result is self._trusted_tool_results[-1]:
            raise ValueError(
                "v2_data_integrity_error: trusted tool result was observed twice"
            )
        self._trusted_tool_results.append(result)

    def export_state(self) -> OfficeV2SessionSnapshot:
        payload = {
            "execution_envelope_digest": self.envelope.canonical_digest(),
            "episode_id": self.episode.episode_id,
            "base_world_digest": self.episode.base_world_digest,
            "initial_state_digest": self.envelope.initial_state_digest,
            "state": self.episode.state,
            "history": self.episode.history,
            "state_digest": self.episode.state_digest,
        }
        return OfficeV2SessionSnapshot(
            **payload,
            snapshot_digest=sha256_digest(
                {
                    "schema_version": "office-v2.0",
                    "snapshot_version": OFFICE_V2_SESSION_SNAPSHOT_VERSION,
                    **payload,
                }
            ),
        )

    def export_recording_state(
        self,
        *,
        pending_clarification_request_ids: tuple[str, ...] = (),
    ) -> OfficeV2RecordingState:
        interaction_events = tuple(
            OfficeV2RecordedInteractionEvent(
                event_type=event.event_type,
                data=event.data,
                logical_time=event.logical_time,
                input_digest=event.input_digest,
                output_digest=event.output_digest,
                state_digest=event.state_digest,
            )
            for execution in self._trusted_interactions
            for event in execution.neutral_trace_events()
        )
        payload = {
            "session": self.export_state(),
            "tool_invocations": tuple(self.runtime.invocations),
            "tool_results": tuple(self._trusted_tool_results),
            "interaction_events": interaction_events,
            "pending_clarification_request_ids": tuple(
                sorted(pending_clarification_request_ids)
            ),
        }
        draft = OfficeV2RecordingState.model_construct(
            **payload,
            recording_state_digest="sha256:" + "0" * 64,
        )
        return OfficeV2RecordingState(
            **payload,
            recording_state_digest=sha256_digest(draft.digest_payload()),
        )


def _interaction_evidence_facts(
    executions: list[InteractionControlExecution],
) -> tuple[InteractionEvidenceFact, ...]:
    facts: list[InteractionEvidenceFact] = []
    for execution in executions:
        neutral_events = execution.neutral_trace_events()
        transition = (
            execution.outcome.transition
            if execution.outcome is not None
            and execution.outcome.transition is not None
            and execution.outcome.transition.committed
            else None
        )
        transition_sequence = next(
            (
                len(facts) + index
                for index, event in enumerate(neutral_events)
                if event.event_type == InteractionEvidenceKind.INTERACTION_RESULT.value
            ),
            None,
        )
        for event in neutral_events:
            kind = InteractionEvidenceKind(event.event_type)
            sequence = len(facts)
            transition_ref = None
            if transition is not None and kind in {
                InteractionEvidenceKind.INTERACTION_RESULT,
                InteractionEvidenceKind.DELEGATION_GRANT_CREATED,
            }:
                transition_ref = StateTransitionEvidenceRef(
                    evidence_id=_live_evidence_id(
                        "interaction-transition",
                        transition.transaction_id,
                    ),
                    evidence_digest=transition.transition_digest,
                    sequence=transition_sequence,
                    transaction_id=transition.transaction_id,
                    committed=True,
                )
            advances_state = (
                kind is InteractionEvidenceKind.INTERACTION_RESULT
                and transition_ref is not None
                and execution.before_state_digest != execution.after_state_digest
            )
            before_state_digest = (
                execution.before_state_digest if advances_state else event.state_digest
            )
            after_state_digest = (
                execution.after_state_digest if advances_state else event.state_digest
            )
            facts.append(
                build_interaction_evidence_fact(
                    evidence_id=_live_evidence_id(
                        "interaction",
                        f"{sequence}:{event.event_type}:{event.input_digest}",
                    ),
                    sequence=sequence,
                    event_kind=kind,
                    logical_time=event.logical_time,
                    input_digest=event.input_digest,
                    output_digest=event.output_digest,
                    before_state_digest=before_state_digest,
                    after_state_digest=after_state_digest,
                    state_digest=after_state_digest,
                    data_digest=sha256_digest(event.data),
                    request_digest=event.data.get("request_digest"),
                    status=(
                        event.data.get("match_status")
                        if kind is InteractionEvidenceKind.CLARIFICATION_REQUESTED
                        else event.data.get("status")
                        if kind is InteractionEvidenceKind.INTERACTION_RESULT
                        else None
                    ),
                    failure_code=(
                        event.data.get("failure_code")
                        if kind
                        in {
                            InteractionEvidenceKind.CLARIFICATION_REQUESTED,
                            InteractionEvidenceKind.INTERACTION_RESULT,
                        }
                        else None
                    ),
                    authenticated=(
                        event.data.get("authenticated")
                        if kind is InteractionEvidenceKind.USER_RESPONSE_RECEIVED
                        else None
                    ),
                    transition_ref=transition_ref,
                    advances_state=advances_state,
                )
            )
    return tuple(facts)


def _live_evidence_id(kind: str, source: str) -> str:
    suffix = sha256_digest({"kind": kind, "source": source}).removeprefix("sha256:")[:24]
    return f"evidence.live.{kind}.{suffix}"


def load_office_v2_session(
    envelope: V2ExecutionEnvelope,
    *,
    episode_id: str,
    snapshot: OfficeV2SessionSnapshot | None = None,
) -> OfficeV2ContainerSession:
    """Create one V2 state owner directly from a validated execution envelope."""

    envelope = V2ExecutionEnvelope.model_validate(envelope.model_dump(mode="json"))
    if envelope.scenario_case_kind is V2ScenarioCaseKind.ATTACK:
        scenario_case: MaterializedScenarioCase | CleanCaseMaterialization = (
            MaterializedScenarioCase.model_validate(envelope.scenario_case_payload)
        )
    else:
        scenario_case = CleanCaseMaterialization.model_validate(
            envelope.scenario_case_payload
        )
    initialization_transition = (
        StateTransitionRecord.model_validate(envelope.initialization_transition_payload)
        if envelope.initialization_transition_payload is not None
        else None
    )

    if snapshot is None:
        state = OfficeWorldState.model_validate(envelope.initial_state_payload)
        history: tuple[StateTransitionRecord, ...] = ()
    else:
        snapshot = OfficeV2SessionSnapshot.model_validate(
            snapshot.model_dump(mode="json", exclude_none=False)
        )
        if snapshot.execution_envelope_digest != envelope.canonical_digest():
            raise ValueError("v2_data_integrity_error: snapshot envelope mismatch")
        if snapshot.episode_id != episode_id:
            raise ValueError("v2_configuration_error: snapshot episode id mismatch")
        if snapshot.base_world_digest != envelope.base_world_digest:
            raise ValueError("v2_data_integrity_error: snapshot base world mismatch")
        if snapshot.initial_state_digest != envelope.initial_state_digest:
            raise ValueError("v2_data_integrity_error: snapshot initial state mismatch")
        state = snapshot.state
        history = snapshot.history

    episode = EpisodeWorld.restore(
        episode_id=episode_id,
        base_world_digest=envelope.base_world_digest,
        state=state,
        history=history,
        initial_state_digest=envelope.initial_state_digest,
    )
    actor = scenario_case.actor
    if actor.logical_time != state.logical_clock.now:
        actor = state.domain_graph.directory.derive_actor_context(
            actor_id=actor.actor_id,
            authenticated_principal_id=actor.authenticated_principal_id,
            session_capabilities=actor.session_capabilities,
            logical_time=state.logical_clock.now,
        )
    bindings = (
        scenario_case.task_bindings
        if isinstance(scenario_case, MaterializedScenarioCase)
        else scenario_case.resolved_bindings
    )
    runtime = OfficeV2ToolRuntime(
        episode=episode,
        actor=actor,
        task=scenario_case.task,
        definitions=office_v2_tool_definitions(),
        bindings=bindings,
        binding_world_digest=envelope.initial_state_digest,
    )
    return OfficeV2ContainerSession(
        envelope=envelope,
        scenario_case=scenario_case,
        episode=episode,
        runtime=runtime,
        initialization_transition=initialization_transition,
    )


__all__ = [
    "OFFICE_V2_LIVE_ORACLE_ARTIFACT_VERSION",
    "OFFICE_V2_SESSION_SNAPSHOT_VERSION",
    "OFFICE_V2_RECORDING_STATE_VERSION",
    "OfficeV2ContainerSession",
    "OfficeV2LiveOracleArtifact",
    "OfficeV2SessionSnapshot",
    "OfficeV2RecordingState",
    "OfficeV2RecordedInteractionEvent",
    "load_office_v2_session",
]
