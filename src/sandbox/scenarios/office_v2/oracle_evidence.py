"""Integrity-checked, content-redacted evidence input for the Office V2 oracle."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Self

from pydantic import Field, ValidationError, field_validator, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2 import OFFICE_V2_ORACLE_EVIDENCE_VERSION
from sandbox.scenarios.office_v2.attack_models import (
    MaterializedScenarioCase,
    ObjectiveResolvedBinding,
)
from sandbox.scenarios.office_v2.attack_objectives import ATTACK_OBJECTIVE_CATALOG_DIGEST
from sandbox.scenarios.office_v2.clean_cases import CleanCaseMaterialization
from sandbox.scenarios.office_v2.models import (
    ActionKind,
    Identifier,
    LogicalTime,
    OfficeV2Contract,
    ResourceKind,
    Sha256Digest,
    canonicalize_identifiers,
)
from sandbox.scenarios.office_v2.oracle_models import (
    EvidenceRef,
    InteractionEventEvidenceRef,
    MaterializationEvidenceRef,
    OracleFailureCode,
    OutputEvidenceRef,
    PolicyDecisionEvidenceRef,
    StateAssertionEvidenceRef,
    StateRole,
    StateTransitionEvidenceRef,
    TaskInputEvidenceRef,
    TerminationEvidenceRef,
    ToolInvocationEvidenceRef,
    ToolResultEvidenceRef,
)
from sandbox.scenarios.office_v2.policy import PolicyDecision
from sandbox.scenarios.office_v2.tools.contracts import (
    ArgumentSource,
    OfficeToolInvocation,
    OfficeToolResult,
    ToolFailureCode,
    ToolResultStatus,
)
from sandbox.scenarios.office_v2.world import StateTransitionRecord
from sandbox.tool_contracts import (
    OFFICE_V2_PUBLIC_TOOL_CONTRACT_DIGEST,
    OFFICE_V2_TOOL_SPEC_BY_NAME,
)


class OracleEvidenceIntegrityError(ValueError):
    """A stable, classified integrity failure; unknown exceptions are not converted."""

    def __init__(
        self,
        code: OracleFailureCode,
        detail: str,
        *,
        affected_ids: tuple[str, ...] = (),
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.affected_ids = affected_ids


class InteractionEvidenceKind(StrEnum):
    CLARIFICATION_REQUESTED = "agent_clarification_requested"
    USER_RESPONSE_RECEIVED = "user_response_received"
    INTERACTION_RESULT = "interaction_result"
    DELEGATION_GRANT_CREATED = "delegation_grant_created"


class OracleInputIdentity(OfficeV2Contract):
    oracle_evidence_version: Identifier = OFFICE_V2_ORACLE_EVIDENCE_VERSION
    scenario_case_id: Identifier
    scenario_case_digest: Sha256Digest
    actor_id: Identifier
    task_id: Identifier
    task_digest: Sha256Digest
    world_digest: Sha256Digest
    tool_catalog_digest: Sha256Digest
    objective_catalog_digest: Sha256Digest
    interaction_catalog_digest: Sha256Digest
    materialization_digest: Sha256Digest
    initial_state_digest: Sha256Digest
    final_state_digest: Sha256Digest


class ArgumentFieldShape(OfficeV2Contract):
    argument_name: Identifier
    value_shape: str = Field(min_length=1, max_length=128, pattern=r"^\S+$")


def _bounded_count(value: int) -> str:
    if value == 0:
        return "0"
    if value == 1:
        return "1"
    if value == 2:
        return "2"
    return "3+"


def _argument_value_shape(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return f"object:{_bounded_count(len(value))}"
    if isinstance(value, list | tuple):
        item_shapes = sorted({_argument_value_shape(item).split(":", 1)[0] for item in value})
        item_shape = "empty" if not item_shapes else "+".join(item_shapes)
        return f"list:{_bounded_count(len(value))}:{item_shape}"
    raise ValueError("tool argument contains an unsupported value shape")


def _argument_shape(arguments: dict[str, Any]) -> tuple[ArgumentFieldShape, ...]:
    return tuple(
        ArgumentFieldShape(
            argument_name=name,
            value_shape=_argument_value_shape(value),
        )
        for name, value in sorted(arguments.items())
    )


class ToolEvidenceExchange(OfficeV2Contract):
    sequence: int = Field(ge=0)
    invocation_ref: ToolInvocationEvidenceRef
    result_ref: ToolResultEvidenceRef
    decision_ref: PolicyDecisionEvidenceRef | None = None
    transition_ref: StateTransitionEvidenceRef | None = None
    output_refs: tuple[OutputEvidenceRef, ...] = Field(default_factory=tuple)
    argument_sources: tuple[ArgumentSource, ...] = Field(default_factory=tuple)
    argument_shape: tuple[ArgumentFieldShape, ...] = Field(
        default_factory=tuple,
        exclude_if=lambda value: not value,
    )
    argument_shape_complete: bool = Field(
        default=False,
        exclude_if=lambda value: not value,
    )
    action: ActionKind | None = None
    resource_kinds: tuple[ResourceKind, ...] = Field(default_factory=tuple)
    policy_decision: PolicyDecision | None = None
    state_transition: StateTransitionRecord | None = None
    status: ToolResultStatus
    failure_code: ToolFailureCode | None = None
    actor_id: Identifier
    task_id: Identifier
    logical_time: LogicalTime
    arguments_digest: Sha256Digest
    visible_output_digest: Sha256Digest
    before_state_digest: Sha256Digest
    after_state_digest: Sha256Digest

    @field_validator("output_refs")
    @classmethod
    def outputs_are_canonical(
        cls, value: tuple[OutputEvidenceRef, ...]
    ) -> tuple[OutputEvidenceRef, ...]:
        ids = tuple(item.evidence_id for item in value)
        canonicalize_identifiers(ids, field_name="tool exchange output evidence")
        return tuple(sorted(value, key=lambda item: item.sort_key()))

    @field_validator("resource_kinds")
    @classmethod
    def resource_kinds_are_canonical(
        cls, value: tuple[ResourceKind, ...]
    ) -> tuple[ResourceKind, ...]:
        if len(value) != len(set(value)):
            raise ValueError("tool exchange resource kinds must be unique")
        return tuple(sorted(value, key=lambda item: item.value))

    @field_validator("argument_shape")
    @classmethod
    def argument_shape_is_canonical(
        cls, value: tuple[ArgumentFieldShape, ...]
    ) -> tuple[ArgumentFieldShape, ...]:
        names = tuple(item.argument_name for item in value)
        canonicalize_identifiers(names, field_name="tool argument shape")
        return tuple(sorted(value, key=lambda item: item.argument_name))

    @model_validator(mode="after")
    def references_match_exchange(self) -> Self:
        if self.argument_shape and not self.argument_shape_complete:
            raise ValueError("incomplete tool argument shape cannot contain fields")
        if (
            self.invocation_ref.sequence != self.sequence
            or self.result_ref.sequence != self.sequence
            or self.invocation_ref.invocation_id != self.result_ref.invocation_id
            or self.invocation_ref.tool_name != self.result_ref.tool_name
        ):
            raise ValueError("tool exchange invocation and result references do not match")
        if self.decision_ref is not None and self.decision_ref.sequence != self.sequence:
            raise ValueError("tool exchange decision sequence does not match")
        if self.transition_ref is not None and self.transition_ref.sequence != self.sequence:
            raise ValueError("tool exchange transition sequence does not match")
        if any(
            item.sequence != self.sequence
            or item.invocation_id != self.invocation_ref.invocation_id
            for item in self.output_refs
        ):
            raise ValueError("tool exchange output evidence does not match invocation")
        if (self.decision_ref is None) != (self.policy_decision is None):
            raise ValueError("tool exchange decision fact and reference must coexist")
        if self.policy_decision is not None and (
            self.decision_ref is None
            or self.policy_decision.decision_digest != self.decision_ref.evidence_digest
            or self.policy_decision.decision_id != self.decision_ref.decision_id
        ):
            raise ValueError("tool exchange decision fact does not match reference")
        if self.policy_decision is not None and (
            self.action is not self.policy_decision.action
            or any(
                item.kind not in self.resource_kinds for item in self.policy_decision.resource_refs
            )
        ):
            raise ValueError("tool exchange action does not match policy fact")
        if (self.transition_ref is None) != (self.state_transition is None):
            raise ValueError("tool exchange transition fact and reference must coexist")
        if self.state_transition is not None and (
            self.transition_ref is None
            or self.state_transition.transition_digest != self.transition_ref.evidence_digest
            or self.state_transition.transaction_id != self.transition_ref.transaction_id
            or self.state_transition.committed != self.transition_ref.committed
        ):
            raise ValueError("tool exchange transition fact does not match reference")
        if self.status is ToolResultStatus.SUCCEEDED and self.failure_code is not None:
            raise ValueError("successful tool exchange cannot define failure_code")
        if self.status is not ToolResultStatus.SUCCEEDED and self.failure_code is None:
            raise ValueError("non-success tool exchange requires failure_code")
        if self.status in {ToolResultStatus.BLOCKED, ToolResultStatus.REJECTED} and (
            self.transition_ref is not None or self.before_state_digest != self.after_state_digest
        ):
            raise ValueError("blocked or rejected exchange cannot change state")
        if self.status is ToolResultStatus.FAILED and (
            self.transition_ref is not None and self.transition_ref.committed
        ):
            raise ValueError("failed exchange cannot contain a committed transition")
        return self

    def evidence_refs(self) -> tuple[EvidenceRef, ...]:
        return (
            self.invocation_ref,
            self.result_ref,
            *((self.decision_ref,) if self.decision_ref is not None else ()),
            *((self.transition_ref,) if self.transition_ref is not None else ()),
            *self.output_refs,
        )


class InteractionEvidenceFact(OfficeV2Contract):
    evidence_id: Identifier
    sequence: int = Field(ge=0)
    event_kind: InteractionEvidenceKind
    logical_time: LogicalTime
    input_digest: Sha256Digest
    output_digest: Sha256Digest
    before_state_digest: Sha256Digest
    after_state_digest: Sha256Digest
    state_digest: Sha256Digest
    data_digest: Sha256Digest
    request_digest: Sha256Digest | None = None
    status: Identifier | None = None
    failure_code: Identifier | None = None
    authenticated: bool | None = None
    transition_ref: StateTransitionEvidenceRef | None = None
    advances_state: bool = False
    fact_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"fact_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def shape_and_digest_match(self) -> Self:
        if self.state_digest != self.after_state_digest:
            raise ValueError("interaction state_digest must equal after_state_digest")
        if self.advances_state:
            if (
                self.event_kind is not InteractionEvidenceKind.INTERACTION_RESULT
                or self.transition_ref is None
                or not self.transition_ref.committed
                or self.before_state_digest == self.after_state_digest
            ):
                raise ValueError(
                    "state-advancing interaction requires a committed result transition"
                )
        elif self.before_state_digest != self.after_state_digest:
            raise ValueError("non-advancing interaction cannot change state")
        if self.event_kind is InteractionEvidenceKind.USER_RESPONSE_RECEIVED:
            if self.authenticated is None:
                raise ValueError("user response evidence requires authenticated fact")
        elif self.authenticated is not None:
            raise ValueError("only user response evidence may define authenticated")
        if self.event_kind in {
            InteractionEvidenceKind.CLARIFICATION_REQUESTED,
            InteractionEvidenceKind.INTERACTION_RESULT,
        }:
            if self.status is None:
                raise ValueError("interaction decision evidence requires status")
        elif self.event_kind is not InteractionEvidenceKind.DELEGATION_GRANT_CREATED and (
            self.status is not None or self.failure_code is not None
        ):
            raise ValueError("response evidence cannot define decision status")
        if self.event_kind is InteractionEvidenceKind.DELEGATION_GRANT_CREATED and (
            self.transition_ref is None or not self.transition_ref.committed
        ):
            raise ValueError("grant evidence requires committed transition reference")
        if self.fact_digest != sha256_digest(self.digest_payload()):
            raise ValueError("interaction evidence fact_digest does not match")
        return self

    def evidence_ref(self) -> InteractionEventEvidenceRef:
        return InteractionEventEvidenceRef(
            evidence_id=self.evidence_id,
            evidence_digest=self.fact_digest,
            sequence=self.sequence,
            event_type=self.event_kind.value,
            logical_time=self.logical_time,
        )


class TimelineEntryKind(StrEnum):
    TOOL = "tool"
    INTERACTION = "interaction"


class EpisodeTimelineEntry(OfficeV2Contract):
    episode_sequence: int = Field(ge=0)
    entry_kind: TimelineEntryKind
    item_sequence: int = Field(ge=0)


class TerminationFact(OfficeV2Contract):
    evidence_id: Identifier
    sequence: int = Field(ge=0)
    reason: Identifier
    submitted: bool
    output_digest: Sha256Digest
    fact_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"fact_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        if self.fact_digest != sha256_digest(self.digest_payload()):
            raise ValueError("termination fact_digest does not match")
        return self

    def evidence_ref(self) -> TerminationEvidenceRef:
        return TerminationEvidenceRef(
            evidence_id=self.evidence_id,
            evidence_digest=self.fact_digest,
            sequence=self.sequence,
            termination_reason=self.reason,
        )


class OracleEvidenceBundle(OfficeV2Contract):
    identity: OracleInputIdentity
    task_ref: TaskInputEvidenceRef
    materialization_ref: MaterializationEvidenceRef
    initial_state_ref: StateAssertionEvidenceRef
    final_state_ref: StateAssertionEvidenceRef
    objective_bindings: tuple[ObjectiveResolvedBinding, ...] = Field(default_factory=tuple)
    frozen_binding_evidence_ids: tuple[Identifier, ...] = Field(default_factory=tuple)
    tool_exchanges: tuple[ToolEvidenceExchange, ...] = Field(default_factory=tuple)
    interaction_facts: tuple[InteractionEvidenceFact, ...] = Field(default_factory=tuple)
    timeline: tuple[EpisodeTimelineEntry, ...] = Field(default_factory=tuple)
    termination: TerminationFact
    recording_digest: Sha256Digest | None = None
    replay_digest: Sha256Digest | None = None
    bundle_digest: Sha256Digest

    @field_validator("tool_exchanges")
    @classmethod
    def exchanges_are_ordered(
        cls, value: tuple[ToolEvidenceExchange, ...]
    ) -> tuple[ToolEvidenceExchange, ...]:
        if tuple(item.sequence for item in value) != tuple(range(len(value))):
            raise ValueError("tool exchange sequence must be contiguous from zero")
        return value

    @field_validator("interaction_facts")
    @classmethod
    def interactions_are_ordered(
        cls, value: tuple[InteractionEvidenceFact, ...]
    ) -> tuple[InteractionEvidenceFact, ...]:
        if tuple(item.sequence for item in value) != tuple(range(len(value))):
            raise ValueError("interaction sequence must be contiguous from zero")
        if tuple(item.logical_time for item in value) != tuple(
            sorted(item.logical_time for item in value)
        ):
            raise ValueError("interaction logical_time must be nondecreasing")
        return value

    @field_validator("timeline")
    @classmethod
    def timeline_is_ordered(
        cls, value: tuple[EpisodeTimelineEntry, ...]
    ) -> tuple[EpisodeTimelineEntry, ...]:
        if tuple(item.episode_sequence for item in value) != tuple(range(len(value))):
            raise ValueError("episode timeline sequence must be contiguous from zero")
        return value

    @field_validator("frozen_binding_evidence_ids")
    @classmethod
    def binding_evidence_is_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return canonicalize_identifiers(value, field_name="frozen binding evidence")

    @field_validator("objective_bindings")
    @classmethod
    def objective_bindings_are_canonical(
        cls, value: tuple[ObjectiveResolvedBinding, ...]
    ) -> tuple[ObjectiveResolvedBinding, ...]:
        slot_ids = tuple(item.slot_id for item in value)
        canonicalize_identifiers(slot_ids, field_name="objective bindings")
        return tuple(sorted(value, key=lambda item: item.slot_id))

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"bundle_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def identities_chain_and_digest_match(self) -> Self:
        if (
            self.task_ref.task_id != self.identity.task_id
            or self.task_ref.task_digest != self.identity.task_digest
            or self.task_ref.evidence_digest != self.identity.task_digest
        ):
            raise ValueError("task evidence does not match input identity")
        if self.materialization_ref.evidence_digest != self.identity.materialization_digest:
            raise ValueError("materialization evidence does not match input identity")
        if (
            self.initial_state_ref.state_role is not StateRole.INITIAL
            or self.initial_state_ref.state_digest != self.identity.initial_state_digest
            or self.final_state_ref.state_role is not StateRole.FINAL
            or self.final_state_ref.state_digest != self.identity.final_state_digest
        ):
            raise ValueError("state evidence does not match input identity")

        if any(
            exchange.task_id != self.identity.task_id or exchange.actor_id != self.identity.actor_id
            for exchange in self.tool_exchanges
        ):
            raise ValueError("tool exchange actor or task does not match input identity")
        expected_items = {
            *((TimelineEntryKind.TOOL, item.sequence) for item in self.tool_exchanges),
            *((TimelineEntryKind.INTERACTION, item.sequence) for item in self.interaction_facts),
        }
        actual_items = {(item.entry_kind, item.item_sequence) for item in self.timeline}
        if len(actual_items) != len(self.timeline) or actual_items != expected_items:
            raise ValueError("episode timeline must reference every evidence item exactly once")

        current = self.identity.initial_state_digest
        available_source_ids = set(self.frozen_binding_evidence_ids)
        for entry in self.timeline:
            if entry.entry_kind is TimelineEntryKind.TOOL:
                item = self.tool_exchanges[entry.item_sequence]
                referenced_source_ids = {
                    evidence_id
                    for source in item.argument_sources
                    for evidence_id in source.source_evidence_ids
                }
                if not referenced_source_ids.issubset(available_source_ids):
                    raise ValueError("tool argument source references unavailable evidence")
                before = item.before_state_digest
                after = item.after_state_digest
                available_source_ids.update(evidence.evidence_id for evidence in item.output_refs)
            else:
                item = self.interaction_facts[entry.item_sequence]
                before = item.before_state_digest
                after = item.after_state_digest
            if before != current:
                raise ValueError("episode evidence state chain is discontinuous")
            current = after
        if current != self.identity.final_state_digest:
            raise ValueError("final state digest does not close episode evidence chain")
        if self.termination.sequence < len(self.tool_exchanges):
            raise ValueError("termination sequence precedes completed tool exchanges")

        refs = [
            self.task_ref,
            self.materialization_ref,
            self.initial_state_ref,
            self.final_state_ref,
            *(ref for item in self.tool_exchanges for ref in item.evidence_refs()),
            *(item.evidence_ref() for item in self.interaction_facts),
            self.termination.evidence_ref(),
        ]
        refs.extend(
            item.transition_ref
            for item in self.interaction_facts
            if item.transition_ref is not None
        )
        evidence_by_id: dict[str, EvidenceRef] = {}
        for ref in refs:
            existing = evidence_by_id.setdefault(ref.evidence_id, ref)
            if existing != ref:
                raise ValueError("oracle evidence bundle contains conflicting evidence_id")
        if self.bundle_digest != sha256_digest(self.digest_payload()):
            raise ValueError("bundle_digest does not match evidence bundle")
        return self


def build_interaction_evidence_fact(
    *,
    evidence_id: str,
    sequence: int,
    event_kind: InteractionEvidenceKind,
    logical_time: int,
    input_digest: str,
    output_digest: str,
    before_state_digest: str,
    after_state_digest: str,
    state_digest: str,
    data_digest: str,
    request_digest: str | None = None,
    status: str | None = None,
    failure_code: str | None = None,
    authenticated: bool | None = None,
    transition_ref: StateTransitionEvidenceRef | None = None,
    advances_state: bool = False,
) -> InteractionEvidenceFact:
    payload = {
        "evidence_id": evidence_id,
        "sequence": sequence,
        "event_kind": event_kind,
        "logical_time": logical_time,
        "input_digest": input_digest,
        "output_digest": output_digest,
        "before_state_digest": before_state_digest,
        "after_state_digest": after_state_digest,
        "state_digest": state_digest,
        "data_digest": data_digest,
        "request_digest": request_digest,
        "status": status,
        "failure_code": failure_code,
        "authenticated": authenticated,
        "transition_ref": transition_ref,
        "advances_state": advances_state,
    }
    draft = InteractionEvidenceFact.model_construct(**payload, fact_digest="sha256:" + "0" * 64)
    return InteractionEvidenceFact(
        **payload,
        fact_digest=sha256_digest(draft.digest_payload()),
    )


def build_termination_fact(
    *, evidence_id: str, sequence: int, reason: str, submitted: bool, output_digest: str
) -> TerminationFact:
    payload = {
        "evidence_id": evidence_id,
        "sequence": sequence,
        "reason": reason,
        "submitted": submitted,
        "output_digest": output_digest,
    }
    draft = TerminationFact.model_construct(**payload, fact_digest="sha256:" + "0" * 64)
    return TerminationFact(**payload, fact_digest=sha256_digest(draft.digest_payload()))


def _exchange(
    invocation: OfficeToolInvocation,
    result: OfficeToolResult,
) -> ToolEvidenceExchange:
    if (
        invocation.invocation_id != result.invocation_id
        or invocation.sequence != result.sequence
        or invocation.tool_name != result.tool_name
        or invocation.before_state_digest != result.before_state_digest
    ):
        raise OracleEvidenceIntegrityError(
            OracleFailureCode.IDENTITY_MISMATCH,
            "tool invocation and result do not match",
            affected_ids=(invocation.invocation_id, result.invocation_id),
        )
    decision = result.policy_decision
    transition = result.state_transition
    tool_spec = OFFICE_V2_TOOL_SPEC_BY_NAME.get(invocation.tool_name)
    if decision is not None and (
        decision.sequence != invocation.sequence
        or decision.actor_id != invocation.actor_id
        or decision.task_id != invocation.task_id
        or decision.before_state_digest != invocation.before_state_digest
    ):
        raise OracleEvidenceIntegrityError(
            OracleFailureCode.IDENTITY_MISMATCH,
            "policy decision does not match invocation",
            affected_ids=(invocation.invocation_id, decision.decision_id),
        )
    if transition is not None and (
        transition.policy_decision_id != (None if decision is None else decision.decision_id)
    ):
        raise OracleEvidenceIntegrityError(
            OracleFailureCode.DANGLING_EVIDENCE_REF,
            "transition does not reference the paired policy decision",
            affected_ids=(invocation.invocation_id, transition.transaction_id),
        )
    return ToolEvidenceExchange(
        sequence=invocation.sequence,
        invocation_ref=ToolInvocationEvidenceRef(
            evidence_id=_evidence_id("invocation", invocation.invocation_id),
            evidence_digest=invocation.canonical_digest(),
            sequence=invocation.sequence,
            invocation_id=invocation.invocation_id,
            tool_name=invocation.tool_name,
        ),
        result_ref=ToolResultEvidenceRef(
            evidence_id=_evidence_id("result", result.invocation_id),
            evidence_digest=result.execution_fact_digest,
            sequence=result.sequence,
            invocation_id=result.invocation_id,
            tool_name=result.tool_name,
        ),
        decision_ref=(
            None
            if decision is None
            else PolicyDecisionEvidenceRef(
                evidence_id=_evidence_id("decision", decision.decision_id),
                evidence_digest=decision.decision_digest,
                sequence=decision.sequence,
                decision_id=decision.decision_id,
            )
        ),
        transition_ref=(
            None
            if transition is None
            else StateTransitionEvidenceRef(
                evidence_id=_evidence_id("transition", transition.transaction_id),
                evidence_digest=transition.transition_digest,
                sequence=invocation.sequence,
                transaction_id=transition.transaction_id,
                committed=transition.committed,
            )
        ),
        output_refs=tuple(
            OutputEvidenceRef(
                evidence_id=item.evidence_id,
                evidence_digest=item.canonical_digest(),
                sequence=result.sequence,
                invocation_id=result.invocation_id,
                field_path=item.field_path,
                resource_ref=item.resource_ref,
                value_digest=item.value_digest,
            )
            for item in result.output_evidence
        ),
        argument_sources=invocation.argument_sources,
        argument_shape=_argument_shape(invocation.arguments),
        argument_shape_complete=True,
        action=(
            decision.action
            if decision is not None
            else None
            if tool_spec is None
            else tool_spec.definition.action
        ),
        resource_kinds=tuple(
            sorted(
                {
                    *(() if tool_spec is None else tool_spec.definition.resource_kinds),
                    *(
                        item.resource_ref.kind
                        for item in result.output_evidence
                        if item.resource_ref is not None
                    ),
                },
                key=lambda item: item.value,
            )
        ),
        policy_decision=decision,
        state_transition=transition,
        status=result.status,
        failure_code=result.failure_code,
        actor_id=invocation.actor_id,
        task_id=invocation.task_id,
        logical_time=invocation.logical_time,
        arguments_digest=invocation.arguments_digest,
        visible_output_digest=result.visible_output_digest,
        before_state_digest=result.before_state_digest,
        after_state_digest=result.after_state_digest,
    )


def _build_oracle_evidence_bundle(
    *,
    scenario_case: MaterializedScenarioCase | CleanCaseMaterialization,
    initialization_transition: StateTransitionRecord | None,
    invocations: tuple[OfficeToolInvocation, ...],
    results: tuple[OfficeToolResult, ...],
    interaction_facts: tuple[InteractionEvidenceFact, ...],
    timeline: tuple[EpisodeTimelineEntry, ...] | None,
    termination: TerminationFact,
    final_state_digest: str,
    clean_initial_state_digest: str | None = None,
    recording_digest: str | None = None,
    replay_digest: str | None = None,
) -> OracleEvidenceBundle:
    try:
        if isinstance(scenario_case, MaterializedScenarioCase):
            scenario_case = MaterializedScenarioCase.model_validate(
                scenario_case.model_dump(mode="python", exclude_none=False)
            )
        else:
            scenario_case = CleanCaseMaterialization.model_validate(
                scenario_case.model_dump(mode="python", exclude_none=False)
            )
        initialization_transition = (
            None
            if initialization_transition is None
            else StateTransitionRecord.model_validate(
                initialization_transition.model_dump(mode="python", exclude_none=False)
            )
        )
        invocations = tuple(
            OfficeToolInvocation.model_validate(item.model_dump(mode="python", exclude_none=False))
            for item in invocations
        )
        results = tuple(
            OfficeToolResult.model_validate(item.model_dump(mode="python", exclude_none=False))
            for item in results
        )
        interaction_facts = tuple(
            InteractionEvidenceFact.model_validate(
                item.model_dump(mode="python", exclude_none=False)
            )
            for item in interaction_facts
        )
        termination = TerminationFact.model_validate(
            termination.model_dump(mode="python", exclude_none=False)
        )
    except ValidationError as exc:
        message = str(exc)
        code = (
            OracleFailureCode.DIGEST_MISMATCH
            if "digest" in message
            else OracleFailureCode.INVALID_CONTRACT
        )
        raise OracleEvidenceIntegrityError(code, message) from exc

    if len(invocations) != len(results):
        raise OracleEvidenceIntegrityError(
            OracleFailureCode.DANGLING_EVIDENCE_REF,
            "every tool invocation requires exactly one result",
        )
    if tuple(item.sequence for item in invocations) != tuple(range(len(invocations))):
        raise OracleEvidenceIntegrityError(
            OracleFailureCode.INVALID_SEQUENCE,
            "tool invocation sequence must be contiguous from zero",
        )
    by_invocation_id = {item.invocation_id: item for item in results}
    if len(by_invocation_id) != len(results):
        raise OracleEvidenceIntegrityError(
            OracleFailureCode.DUPLICATE_EVIDENCE,
            "tool results repeat invocation ids",
        )
    if set(by_invocation_id) != {item.invocation_id for item in invocations}:
        raise OracleEvidenceIntegrityError(
            OracleFailureCode.DANGLING_EVIDENCE_REF,
            "tool results contain unmatched invocation ids",
        )
    ordered_results = tuple(by_invocation_id[item.invocation_id] for item in invocations)
    exchanges = tuple(
        _exchange(invocation, result)
        for invocation, result in zip(invocations, ordered_results, strict=True)
    )

    is_attack_case = isinstance(scenario_case, MaterializedScenarioCase)
    expected_transition_digest = (
        scenario_case.materialization_record.initialization_transition_digest
        if is_attack_case
        else None
    )
    actual_transition_digest = (
        None if initialization_transition is None else initialization_transition.transition_digest
    )
    if actual_transition_digest != expected_transition_digest:
        raise OracleEvidenceIntegrityError(
            OracleFailureCode.DIGEST_MISMATCH,
            "initialization transition does not match materialization record",
        )
    if initialization_transition is not None and (
        not initialization_transition.committed
        or initialization_transition.after_state_digest
        != (
            scenario_case.initial_world_digest
            if is_attack_case
            else scenario_case.base_world_digest
        )
    ):
        raise OracleEvidenceIntegrityError(
            OracleFailureCode.INVALID_STATE_CHAIN,
            "initialization transition does not produce scenario initial state",
        )

    task_digest = scenario_case.task.canonical_digest()
    scenario_case_digest = (
        scenario_case.content_digest if is_attack_case else scenario_case.case_digest
    )
    initial_state_digest = (
        scenario_case.initial_world_digest if is_attack_case else clean_initial_state_digest
    )
    if initial_state_digest is None:
        raise OracleEvidenceIntegrityError(
            OracleFailureCode.INVALID_CONTRACT,
            "clean evidence requires an explicit runtime initial state digest",
        )
    interaction_contract = (
        scenario_case.interaction_contract
        if is_attack_case
        else scenario_case.task.user_response_script
    )
    materialization_digest = (
        scenario_case.materialization_record.materialization_digest
        if is_attack_case
        else scenario_case.case_digest
    )
    objective_bindings = scenario_case.objective_bindings if is_attack_case else ()
    task_bindings = (
        scenario_case.task_bindings if is_attack_case else scenario_case.resolved_bindings
    )
    identity = OracleInputIdentity(
        scenario_case_id=scenario_case.case_id,
        scenario_case_digest=scenario_case_digest,
        actor_id=scenario_case.actor.actor_id,
        task_id=scenario_case.task.task_id,
        task_digest=task_digest,
        world_digest=scenario_case.base_world_digest,
        tool_catalog_digest=OFFICE_V2_PUBLIC_TOOL_CONTRACT_DIGEST,
        objective_catalog_digest=ATTACK_OBJECTIVE_CATALOG_DIGEST,
        interaction_catalog_digest=interaction_contract.canonical_digest(),
        materialization_digest=materialization_digest,
        initial_state_digest=initial_state_digest,
        final_state_digest=final_state_digest,
    )
    task_ref = TaskInputEvidenceRef(
        evidence_id=_evidence_id("task", scenario_case.task.task_id),
        evidence_digest=task_digest,
        task_id=scenario_case.task.task_id,
        task_digest=task_digest,
    )
    materialization_ref = MaterializationEvidenceRef(
        evidence_id=_evidence_id("materialization", scenario_case.case_id),
        evidence_digest=materialization_digest,
        materialization_id=scenario_case.case_id,
        transition_digest=actual_transition_digest,
    )
    initial_state_ref = StateAssertionEvidenceRef(
        evidence_id=_evidence_id("state-initial", scenario_case.case_id),
        evidence_digest=sha256_digest({"role": "initial", "state_digest": initial_state_digest}),
        assertion_id=f"state.initial.{scenario_case.case_id}",
        state_role=StateRole.INITIAL,
        state_digest=initial_state_digest,
    )
    final_state_ref = StateAssertionEvidenceRef(
        evidence_id=_evidence_id("state-final", scenario_case.case_id),
        evidence_digest=sha256_digest({"role": "final", "state_digest": final_state_digest}),
        assertion_id=f"state.final.{scenario_case.case_id}",
        state_role=StateRole.FINAL,
        state_digest=final_state_digest,
    )
    if timeline is None:
        if interaction_facts:
            raise OracleEvidenceIntegrityError(
                OracleFailureCode.INVALID_SEQUENCE,
                "interaction evidence requires an explicit episode timeline",
            )
        timeline = tuple(
            EpisodeTimelineEntry(
                episode_sequence=index,
                entry_kind=TimelineEntryKind.TOOL,
                item_sequence=exchange.sequence,
            )
            for index, exchange in enumerate(exchanges)
        )
    payload = {
        "identity": identity,
        "task_ref": task_ref,
        "materialization_ref": materialization_ref,
        "initial_state_ref": initial_state_ref,
        "final_state_ref": final_state_ref,
        "objective_bindings": objective_bindings,
        "frozen_binding_evidence_ids": tuple(
            sorted(
                {
                    evidence_id
                    for binding in task_bindings
                    for evidence_id in (
                        *binding.matched_fact_refs,
                        *binding.candidate_evidence_refs,
                    )
                }
            )
        ),
        "tool_exchanges": exchanges,
        "interaction_facts": interaction_facts,
        "timeline": timeline,
        "termination": termination,
        "recording_digest": recording_digest,
        "replay_digest": replay_digest,
    }
    draft = OracleEvidenceBundle.model_construct(
        **payload,
        bundle_digest="sha256:" + "0" * 64,
    )
    try:
        return OracleEvidenceBundle(
            **payload,
            bundle_digest=sha256_digest(draft.digest_payload()),
        )
    except ValidationError as exc:
        message = str(exc)
        code = (
            OracleFailureCode.INVALID_SEQUENCE
            if "sequence" in message
            else OracleFailureCode.INVALID_STATE_CHAIN
            if "state" in message
            else OracleFailureCode.DUPLICATE_EVIDENCE
            if "duplicate" in message
            else OracleFailureCode.INVALID_CONTRACT
        )
        raise OracleEvidenceIntegrityError(code, message) from exc


def build_oracle_evidence_bundle(
    *,
    scenario_case: MaterializedScenarioCase,
    initialization_transition: StateTransitionRecord | None,
    invocations: tuple[OfficeToolInvocation, ...],
    results: tuple[OfficeToolResult, ...],
    interaction_facts: tuple[InteractionEvidenceFact, ...],
    timeline: tuple[EpisodeTimelineEntry, ...] | None,
    termination: TerminationFact,
    final_state_digest: str,
    recording_digest: str | None = None,
    replay_digest: str | None = None,
) -> OracleEvidenceBundle:
    return _build_oracle_evidence_bundle(
        scenario_case=scenario_case,
        initialization_transition=initialization_transition,
        invocations=invocations,
        results=results,
        interaction_facts=interaction_facts,
        timeline=timeline,
        termination=termination,
        final_state_digest=final_state_digest,
        recording_digest=recording_digest,
        replay_digest=replay_digest,
    )


def build_clean_oracle_evidence_bundle(
    *,
    clean_case: CleanCaseMaterialization,
    invocations: tuple[OfficeToolInvocation, ...],
    results: tuple[OfficeToolResult, ...],
    interaction_facts: tuple[InteractionEvidenceFact, ...],
    timeline: tuple[EpisodeTimelineEntry, ...] | None,
    termination: TerminationFact,
    initial_state_digest: str,
    final_state_digest: str,
    recording_digest: str | None = None,
    replay_digest: str | None = None,
) -> OracleEvidenceBundle:
    """Build evidence for a clean case without inventing an attack objective."""

    return _build_oracle_evidence_bundle(
        scenario_case=clean_case,
        initialization_transition=None,
        invocations=invocations,
        results=results,
        interaction_facts=interaction_facts,
        timeline=timeline,
        termination=termination,
        final_state_digest=final_state_digest,
        clean_initial_state_digest=initial_state_digest,
        recording_digest=recording_digest,
        replay_digest=replay_digest,
    )


def _evidence_id(kind: str, source_id: str) -> str:
    suffix = sha256_digest({"kind": kind, "source_id": source_id}).removeprefix("sha256:")[:24]
    return f"evidence.{kind}.{suffix}"


__all__ = [
    "ArgumentFieldShape",
    "InteractionEvidenceFact",
    "InteractionEvidenceKind",
    "EpisodeTimelineEntry",
    "OracleEvidenceBundle",
    "OracleEvidenceIntegrityError",
    "OracleInputIdentity",
    "TerminationFact",
    "TimelineEntryKind",
    "ToolEvidenceExchange",
    "build_interaction_evidence_fact",
    "build_clean_oracle_evidence_bundle",
    "build_oracle_evidence_bundle",
    "build_termination_fact",
]
