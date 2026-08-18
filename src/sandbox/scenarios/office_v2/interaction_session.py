"""Model-visible clarification proposals matched to frozen trusted requests."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self

from pydantic import Field, ValidationInfo, field_validator, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.interaction import (
    InteractionOutcome,
    InteractionResponse,
    InteractionStatus,
    ResponseChannel,
    apply_interaction_response,
)
from sandbox.scenarios.office_v2.models import (
    ActionKind,
    ActionScope,
    ClarificationRequest,
    InteractionContract,
    PrincipalId,
    QuestionKind,
    ResourceKind,
    ResourceRef,
    TaskContract,
    UserResponseRule,
    canonicalize_identifiers,
    canonicalize_resource_refs,
)
from sandbox.scenarios.office_v2.tools.contracts import OfficeToolResult
from sandbox.scenarios.office_v2.world import EpisodeWorld
from sandbox.tool_contracts import ToolArguments


class RequestClarificationArguments(ToolArguments):
    question_kind: QuestionKind
    candidate_refs: tuple[ResourceRef, ...] = Field(default_factory=tuple)
    missing_fact_descriptions: tuple[str, ...] = Field(default_factory=tuple)
    requested_action: ActionKind | None = None
    requested_resource_kinds: tuple[ResourceKind, ...] = Field(default_factory=tuple)
    requested_recipient_ids: tuple[PrincipalId, ...] = Field(default_factory=tuple)

    @field_validator("candidate_refs")
    @classmethod
    def candidates_are_canonical(
        cls, value: tuple[ResourceRef, ...]
    ) -> tuple[ResourceRef, ...]:
        return canonicalize_resource_refs(value)

    @field_validator("missing_fact_descriptions")
    @classmethod
    def descriptions_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted(item.strip() for item in value))
        if any(not item for item in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("missing fact descriptions must be nonempty and unique")
        return normalized

    @field_validator("requested_resource_kinds")
    @classmethod
    def resource_kinds_are_canonical(
        cls, value: tuple[ResourceKind, ...]
    ) -> tuple[ResourceKind, ...]:
        return tuple(sorted(set(value), key=lambda item: item.value))

    @field_validator("requested_recipient_ids")
    @classmethod
    def recipients_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return canonicalize_identifiers(value, field_name="requested_recipient_ids")

    @model_validator(mode="after")
    def payload_matches_question_kind(self) -> Self:
        has_action = self.requested_action is not None or bool(
            self.requested_resource_kinds
        )
        if self.question_kind is QuestionKind.DISAMBIGUATION:
            if len(self.candidate_refs) < 2:
                raise ValueError("disambiguation requires at least two visible candidates")
            if self.missing_fact_descriptions or has_action or self.requested_recipient_ids:
                raise ValueError("disambiguation accepts only candidate_refs")
        elif self.question_kind is QuestionKind.MISSING_VALUE:
            if not self.missing_fact_descriptions:
                raise ValueError("missing_value requires visible fact descriptions")
            if self.candidate_refs or has_action or self.requested_recipient_ids:
                raise ValueError("missing_value accepts only fact descriptions")
        else:
            if self.requested_action is None or not self.requested_resource_kinds:
                raise ValueError("authorization requires an action and resource kinds")
            if not self.candidate_refs and not self.requested_recipient_ids:
                raise ValueError("authorization requires a resource or recipient scope")
            if self.missing_fact_descriptions:
                raise ValueError("authorization does not accept missing fact descriptions")
        return self


class ClarificationMatchStatus(StrEnum):
    MATCHED = "matched"
    REJECTED = "rejected"


class ClarificationMatchFailure(StrEnum):
    NO_FROZEN_MATCH = "no_frozen_match"
    AMBIGUOUS_FROZEN_MATCH = "ambiguous_frozen_match"
    VISIBLE_SOURCE_MISSING = "visible_source_missing"
    REQUEST_ALREADY_PENDING = "request_already_pending"


class ClarificationMatchResult(ToolArguments):
    status: ClarificationMatchStatus
    failure_code: ClarificationMatchFailure | None = None
    matched_request: ClarificationRequest | None = None
    source_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    source_task_fact_ids: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("source_evidence_ids", "source_task_fact_ids")
    @classmethod
    def source_ids_are_canonical(
        cls, value: tuple[str, ...], info: ValidationInfo
    ) -> tuple[str, ...]:
        return canonicalize_identifiers(value, field_name=info.field_name)

    @model_validator(mode="after")
    def result_is_consistent(self) -> Self:
        if self.status is ClarificationMatchStatus.MATCHED:
            if self.matched_request is None or self.failure_code is not None:
                raise ValueError("matched clarification requires exactly one trusted request")
        elif (
            self.matched_request is not None
            or self.source_evidence_ids
            or self.source_task_fact_ids
        ):
            raise ValueError("rejected clarification cannot expose trusted request facts")
        elif self.failure_code is None:
            raise ValueError("rejected clarification requires a failure code")
        return self

    def model_visible_payload(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "error": self.failure_code.value if self.failure_code is not None else None,
        }


class ClarificationCoordinator:
    """Resolve model proposals against immutable requests without creating authority."""

    def __init__(
        self,
        *,
        contract: InteractionContract,
        task: TaskContract,
        visible_results: tuple[OfficeToolResult, ...],
    ) -> None:
        self._contract = contract
        self._task = task
        self._visible_results = list(visible_results)
        self._pending_request_ids: set[str] = set()

    def observe_result(self, result: OfficeToolResult) -> None:
        self._visible_results.append(result)

    def complete(self, request_id: str) -> None:
        self._pending_request_ids.discard(request_id)

    @property
    def pending_request_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._pending_request_ids))

    def match(self, proposal: RequestClarificationArguments) -> ClarificationMatchResult:
        matches = tuple(
            request
            for request in self._contract.requests
            if self._semantic_match(request, proposal)
        )
        if not matches:
            return _rejected(ClarificationMatchFailure.NO_FROZEN_MATCH)
        if len(matches) > 1:
            return _rejected(ClarificationMatchFailure.AMBIGUOUS_FROZEN_MATCH)
        request = matches[0]
        if request.request_id in self._pending_request_ids:
            return _rejected(ClarificationMatchFailure.REQUEST_ALREADY_PENDING)
        source_ids = self._visible_source_ids(proposal)
        if source_ids is None:
            return _rejected(ClarificationMatchFailure.VISIBLE_SOURCE_MISSING)
        self._pending_request_ids.add(request.request_id)
        return ClarificationMatchResult(
            status=ClarificationMatchStatus.MATCHED,
            matched_request=request,
            source_evidence_ids=source_ids,
            source_task_fact_ids=self._fact_ids(proposal),
        )

    def _semantic_match(
        self,
        request: ClarificationRequest,
        proposal: RequestClarificationArguments,
    ) -> bool:
        if request.question_kind is not proposal.question_kind:
            return False
        if proposal.question_kind is QuestionKind.DISAMBIGUATION:
            return request.candidate_refs == proposal.candidate_refs
        if proposal.question_kind is QuestionKind.MISSING_VALUE:
            return request.missing_fact_ids == self._fact_ids(proposal)
        return (
            request.candidate_refs == proposal.candidate_refs
            and request.requested_action_scope
            == ActionScope(
                action=proposal.requested_action,
                resource_kinds=proposal.requested_resource_kinds,
            )
            and request.requested_recipient_ids == proposal.requested_recipient_ids
        )

    def _fact_ids(self, proposal: RequestClarificationArguments) -> tuple[str, ...]:
        return tuple(
            sorted(
                fact.fact_id
                for fact in self._task.required_response_facts
                if fact.description in proposal.missing_fact_descriptions
            )
        )

    def _visible_source_ids(
        self, proposal: RequestClarificationArguments
    ) -> tuple[str, ...] | None:
        evidence = tuple(
            item
            for result in self._visible_results
            for item in result.output_evidence
        )
        selected: set[str] = set()
        for ref in proposal.candidate_refs:
            matches = tuple(item for item in evidence if item.resource_ref == ref)
            if not matches:
                return None
            selected.update(item.evidence_id for item in matches)
        for recipient_id in proposal.requested_recipient_ids:
            digest = sha256_digest(recipient_id)
            matches = tuple(item for item in evidence if item.value_digest == digest)
            if not matches:
                return None
            selected.update(item.evidence_id for item in matches)
        return tuple(sorted(selected))


def _rejected(failure: ClarificationMatchFailure) -> ClarificationMatchResult:
    return ClarificationMatchResult(
        status=ClarificationMatchStatus.REJECTED,
        failure_code=failure,
    )


class ScriptedResponseDirective(ToolArguments):
    """Trusted episode input selecting one frozen response branch."""

    request_id: str
    rule_id: str
    turn_id: str
    responder_id: PrincipalId
    authenticated_principal_id: PrincipalId | None
    channel: ResponseChannel = ResponseChannel.AUTHENTICATED_TASK_SESSION


@dataclass(frozen=True, slots=True)
class NeutralInteractionTraceEvent:
    event_type: str
    data: dict[str, Any]
    logical_time: int
    input_digest: str
    output_digest: str
    state_digest: str


@dataclass(frozen=True, slots=True)
class InteractionControlExecution:
    coordination: ClarificationMatchResult
    proposal: RequestClarificationArguments
    logical_time: int
    before_state_digest: str
    after_state_digest: str
    response: InteractionResponse | None = None
    outcome: InteractionOutcome | None = None
    application_failure_code: str | None = None
    final_answer: None = None
    follow_up_user_message: str | None = None

    def model_visible_payload(self) -> dict[str, Any]:
        if self.coordination.status is ClarificationMatchStatus.REJECTED:
            return self.coordination.model_visible_payload()
        if self.outcome is None:
            return {
                "status": "rejected",
                "error": self.application_failure_code or "response_script_missing",
            }
        if self.outcome.status is InteractionStatus.REJECTED:
            return {
                "status": "rejected",
                "error": (
                    self.outcome.failure_code.value
                    if self.outcome.failure_code is not None
                    else "interaction_rejected"
                ),
            }
        return {
            "status": "succeeded",
            "outcome": self.outcome.status.value,
            "selected_refs": [
                ref.model_dump(mode="json") for ref in self.outcome.selected_refs
            ],
        }

    def neutral_trace_events(self) -> tuple[NeutralInteractionTraceEvent, ...]:
        request = self.coordination.matched_request
        request_digest = sha256_digest(request) if request is not None else None
        proposal_digest = sha256_digest(self.proposal)
        request_data = {
            "proposal_digest": proposal_digest,
            "request_digest": request_digest,
            "question_kind": self.proposal.question_kind.value,
            "match_status": self.coordination.status.value,
            "failure_code": (
                self.coordination.failure_code.value
                if self.coordination.failure_code is not None
                else None
            ),
            "visible_scope": self._visible_scope(),
        }
        events = [
            self._trace_event(
                "agent_clarification_requested",
                request_data,
                input_digest=proposal_digest,
                state_digest=self.before_state_digest,
            )
        ]
        if self.response is not None:
            response_digest = sha256_digest(self.response)
            response_data = {
                "request_digest": request_digest,
                "response_digest": response_digest,
                "channel": self.response.channel.value,
                "authenticated": (
                    self.response.authenticated_principal_id is not None
                    and self.response.authenticated_principal_id
                    == self.response.responder_id
                ),
                "responder_digest": sha256_digest(self.response.responder_id),
                "received_at": self.response.received_at,
            }
            events.append(
                self._trace_event(
                    "user_response_received",
                    response_data,
                    input_digest=response_digest,
                    state_digest=self.before_state_digest,
                )
            )

        transition = self.outcome.transition if self.outcome is not None else None
        failure_code = (
            (
                self.outcome.failure_code.value
                if self.outcome.failure_code is not None
                else None
            )
            if self.outcome is not None
            else (
                self.coordination.failure_code.value
                if self.coordination.failure_code is not None
                else self.application_failure_code or "response_script_missing"
            )
        )
        outcome_data = {
            "request_digest": request_digest,
            "outcome_digest": (
                self.outcome.outcome_digest if self.outcome is not None else None
            ),
            "status": (
                self.outcome.status.value
                if self.outcome is not None
                else InteractionStatus.REJECTED.value
            ),
            "failure_code": failure_code,
            "selected_refs": (
                [ref.model_dump(mode="json") for ref in self.outcome.selected_refs]
                if self.outcome is not None
                else []
            ),
            "transition_digest": (
                transition.transition_digest if transition is not None else None
            ),
            "before_state_digest": self.before_state_digest,
            "after_state_digest": self.after_state_digest,
        }
        outcome_digest = (
            self.outcome.outcome_digest
            if self.outcome is not None
            else sha256_digest(outcome_data)
        )
        events.append(
            self._trace_event(
                "interaction_result",
                outcome_data,
                input_digest=(
                    sha256_digest(self.response)
                    if self.response is not None
                    else proposal_digest
                ),
                output_digest=outcome_digest,
            )
        )

        if (
            self.outcome is not None
            and self.outcome.status is InteractionStatus.GRANT_CREATED
            and self.outcome.grant is not None
            and transition is not None
            and transition.committed
        ):
            grant = self.outcome.grant
            grant_data = {
                "request_digest": request_digest,
                "outcome_digest": self.outcome.outcome_digest,
                "action_scope": grant.action_scope.model_dump(mode="json"),
                "resource_refs": [
                    ref.model_dump(mode="json") for ref in grant.resource_refs
                ],
                "recipient_ids": list(grant.recipient_ids),
                "valid_from": grant.valid_from,
                "expires_at": grant.expires_at,
                "transition_digest": transition.transition_digest,
                "before_state_digest": transition.before_state_digest,
                "after_state_digest": transition.after_state_digest,
            }
            events.append(
                self._trace_event(
                    "delegation_grant_created",
                    grant_data,
                    input_digest=self.outcome.outcome_digest,
                    output_digest=transition.transition_digest,
                )
            )
        return tuple(events)

    def _trace_event(
        self,
        event_type: str,
        data: dict[str, Any],
        *,
        input_digest: str,
        output_digest: str | None = None,
        state_digest: str | None = None,
    ) -> NeutralInteractionTraceEvent:
        return NeutralInteractionTraceEvent(
            event_type=event_type,
            data=data,
            logical_time=self.logical_time,
            input_digest=input_digest,
            output_digest=output_digest or sha256_digest(data),
            state_digest=state_digest or self.after_state_digest,
        )

    def _visible_scope(self) -> dict[str, Any]:
        return {
            "candidate_refs": [
                ref.model_dump(mode="json") for ref in self.proposal.candidate_refs
            ],
            "missing_fact_count": len(self.proposal.missing_fact_descriptions),
            "requested_action": (
                self.proposal.requested_action.value
                if self.proposal.requested_action is not None
                else None
            ),
            "requested_resource_kinds": [
                item.value for item in self.proposal.requested_resource_kinds
            ],
            "requested_recipient_ids": list(self.proposal.requested_recipient_ids),
        }


class DeterministicInteractionSession:
    """Apply scenario-owned replies after a model proposal matches frozen facts."""

    def __init__(
        self,
        *,
        episode: EpisodeWorld,
        task: TaskContract,
        actor_id: PrincipalId,
        response_directives: tuple[ScriptedResponseDirective, ...],
        visible_results: tuple[OfficeToolResult, ...] = (),
    ) -> None:
        self._episode = episode
        self._task = task
        self._contract = task.user_response_script
        self._actor_id = actor_id
        self._coordinator = ClarificationCoordinator(
            contract=self._contract,
            task=task,
            visible_results=visible_results,
        )
        self._directives = self._index_directives(response_directives)

    def observe_result(self, result: OfficeToolResult) -> None:
        self._coordinator.observe_result(result)

    @property
    def pending_request_ids(self) -> tuple[str, ...]:
        return self._coordinator.pending_request_ids

    def handle_request(
        self, arguments: RequestClarificationArguments | dict[str, Any]
    ) -> InteractionControlExecution:
        proposal = (
            arguments
            if isinstance(arguments, RequestClarificationArguments)
            else RequestClarificationArguments.model_validate(arguments, strict=False)
        )
        logical_time = self._episode.state.logical_clock.now
        before_state_digest = self._episode.state_digest
        coordination = self._coordinator.match(proposal)
        request = coordination.matched_request
        if request is None:
            return InteractionControlExecution(
                coordination=coordination,
                proposal=proposal,
                logical_time=logical_time,
                before_state_digest=before_state_digest,
                after_state_digest=self._episode.state_digest,
            )

        directive = self._directives.get(request.request_id)
        if directive is None:
            self._coordinator.complete(request.request_id)
            return InteractionControlExecution(
                coordination=coordination,
                proposal=proposal,
                logical_time=logical_time,
                before_state_digest=before_state_digest,
                after_state_digest=self._episode.state_digest,
            )
        rule = self._rule(directive)
        response = InteractionResponse(
            turn_id=directive.turn_id,
            request_id=request.request_id,
            responder_id=directive.responder_id,
            authenticated_principal_id=directive.authenticated_principal_id,
            channel=directive.channel,
            response_text=rule.response_text,
            received_at=self._episode.state.logical_clock.now,
        )
        history_length = len(self._episode.history)
        try:
            outcome = apply_interaction_response(
                self._episode,
                self._contract,
                response,
                actor_id=self._actor_id,
            )
        except ValueError:
            failed_transition = (
                self._episode.history[-1]
                if len(self._episode.history) == history_length + 1
                else None
            )
            if failed_transition is None or failed_transition.committed:
                raise
            return InteractionControlExecution(
                coordination=coordination,
                proposal=proposal,
                logical_time=logical_time,
                before_state_digest=before_state_digest,
                after_state_digest=self._episode.state_digest,
                response=response,
                application_failure_code=(
                    failed_transition.failure_code
                    or "transaction_validation_failed"
                ),
            )
        finally:
            self._coordinator.complete(request.request_id)
        follow_up = None
        if outcome.status is not InteractionStatus.REJECTED:
            follow_up = (
                "Authenticated task-session response from "
                f"{response.authenticated_principal_id}: {response.response_text}"
            )
        return InteractionControlExecution(
            coordination=coordination,
            proposal=proposal,
            logical_time=logical_time,
            before_state_digest=before_state_digest,
            after_state_digest=self._episode.state_digest,
            response=response,
            outcome=outcome,
            follow_up_user_message=follow_up,
        )

    def _index_directives(
        self, directives: tuple[ScriptedResponseDirective, ...]
    ) -> dict[str, ScriptedResponseDirective]:
        indexed: dict[str, ScriptedResponseDirective] = {}
        for directive in directives:
            if directive.request_id in indexed:
                raise ValueError("response script has multiple branches for one request")
            request = next(
                (
                    item
                    for item in self._contract.requests
                    if item.request_id == directive.request_id
                ),
                None,
            )
            if request is None:
                raise ValueError("response directive references an unknown request")
            self._rule(directive)
            indexed[directive.request_id] = directive
        return indexed

    def _rule(self, directive: ScriptedResponseDirective) -> UserResponseRule:
        rule = next(
            (
                item
                for item in self._contract.response_rules
                if item.rule_id == directive.rule_id
            ),
            None,
        )
        if rule is None or rule.match.request_id != directive.request_id:
            raise ValueError("response directive does not select a frozen request rule")
        return rule


__all__ = [
    "ClarificationCoordinator",
    "ClarificationMatchFailure",
    "ClarificationMatchResult",
    "ClarificationMatchStatus",
    "DeterministicInteractionSession",
    "InteractionControlExecution",
    "NeutralInteractionTraceEvent",
    "RequestClarificationArguments",
    "ScriptedResponseDirective",
]
