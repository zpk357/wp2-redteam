"""Trusted, deterministic response handling for Office V2 interactions."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, field_validator, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import (
    DelegationGrant,
    EvidenceSourceKind,
    Identifier,
    InteractionContract,
    LogicalTime,
    OfficeV2Contract,
    PrincipalId,
    ResourceRef,
    SourceEvidence,
    UserResponseRule,
    canonicalize_resource_refs,
)
from sandbox.scenarios.office_v2.world import EpisodeWorld, StateTransitionRecord


class ResponseChannel(StrEnum):
    AUTHENTICATED_TASK_SESSION = "authenticated_task_session"
    BUSINESS_CONTENT = "business_content"
    UNAUTHENTICATED_SESSION = "unauthenticated_session"


class InteractionStatus(StrEnum):
    GRANT_CREATED = "grant_created"
    GRANT_ALREADY_APPLIED = "grant_already_applied"
    SELECTION_ACCEPTED = "selection_accepted"
    NO_GRANT = "no_grant"
    REJECTED = "rejected"


class InteractionFailureCode(StrEnum):
    REQUEST_NOT_FOUND = "request_not_found"
    LOGICAL_TIME_MISMATCH = "logical_time_mismatch"
    UNTRUSTED_CHANNEL = "untrusted_channel"
    UNAUTHENTICATED_RESPONSE = "unauthenticated_response"
    AUTHENTICATION_MISMATCH = "authentication_mismatch"
    RESPONDER_NOT_ALLOWED = "responder_not_allowed"
    RESPONSE_NOT_MATCHED = "response_not_matched"
    TURN_REUSE_MISMATCH = "turn_reuse_mismatch"


class InteractionResponse(OfficeV2Contract):
    turn_id: Identifier
    request_id: Identifier
    responder_id: PrincipalId
    authenticated_principal_id: PrincipalId | None = None
    channel: ResponseChannel
    response_text: str = Field(min_length=1, max_length=2048)
    received_at: LogicalTime


class InteractionOutcome(OfficeV2Contract):
    turn_id: Identifier
    request_id: Identifier
    status: InteractionStatus
    failure_code: InteractionFailureCode | None = None
    selected_refs: tuple[ResourceRef, ...] = Field(default_factory=tuple)
    grant: DelegationGrant | None = None
    transition: StateTransitionRecord | None = None
    outcome_digest: str = Field(pattern=r"^(?:sha256:)?[0-9a-f]{64}$")

    @field_validator("selected_refs")
    @classmethod
    def refs_are_canonical(
        cls, value: tuple[ResourceRef, ...]
    ) -> tuple[ResourceRef, ...]:
        return canonicalize_resource_refs(value)

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json", exclude={"outcome_digest"}, exclude_none=False
        )

    @model_validator(mode="after")
    def result_is_consistent_and_locked(self) -> Self:
        if self.status is InteractionStatus.REJECTED:
            if self.failure_code is None or self.grant is not None or self.transition is not None:
                raise ValueError("rejected interaction must only contain a failure code")
        elif self.failure_code is not None:
            raise ValueError("accepted interaction must not contain a failure code")
        if self.status is InteractionStatus.GRANT_CREATED:
            if self.grant is None or self.transition is None or not self.transition.committed:
                raise ValueError("created grant requires a committed transition")
        elif self.status is InteractionStatus.GRANT_ALREADY_APPLIED:
            if self.grant is None or self.transition is not None:
                raise ValueError("idempotent grant result must not create another transition")
        elif self.grant is not None or self.transition is not None:
            raise ValueError("non-grant result must not change state")
        if self.outcome_digest != sha256_digest(self.digest_payload()):
            raise ValueError("outcome_digest does not match interaction outcome")
        return self


def apply_interaction_response(
    episode: EpisodeWorld,
    contract: InteractionContract,
    response: InteractionResponse,
    *,
    actor_id: PrincipalId,
) -> InteractionOutcome:
    """Apply one exact frozen response rule at the Episode transaction boundary."""

    request = next(
        (item for item in contract.requests if item.request_id == response.request_id),
        None,
    )
    if request is None:
        return _rejected(response, InteractionFailureCode.REQUEST_NOT_FOUND)

    existing = next(
        (
            grant
            for grant in episode.state.delegation_grants
            if grant.source_turn_id == response.turn_id
        ),
        None,
    )
    rule = _matching_rule(contract, response)
    if existing is not None:
        if (
            rule is not None
            and existing.source_request_id == request.request_id
            and existing.source_rule_id == rule.rule_id
            and existing.issuer_id == response.authenticated_principal_id
            and existing.actor_id == actor_id
            and existing.source_evidence.content_digest == sha256_digest(response)
        ):
            return _build_outcome(
                response,
                InteractionStatus.GRANT_ALREADY_APPLIED,
                selected_refs=rule.selected_refs,
                grant=existing,
            )
        return _rejected(response, InteractionFailureCode.TURN_REUSE_MISMATCH)

    if response.received_at != episode.state.logical_clock.now:
        return _rejected(response, InteractionFailureCode.LOGICAL_TIME_MISMATCH)
    if response.channel is not ResponseChannel.AUTHENTICATED_TASK_SESSION:
        return _rejected(response, InteractionFailureCode.UNTRUSTED_CHANNEL)
    if response.authenticated_principal_id is None:
        return _rejected(response, InteractionFailureCode.UNAUTHENTICATED_RESPONSE)
    if response.authenticated_principal_id != response.responder_id:
        return _rejected(response, InteractionFailureCode.AUTHENTICATION_MISMATCH)
    if response.authenticated_principal_id not in request.allowed_responder_ids:
        return _rejected(response, InteractionFailureCode.RESPONDER_NOT_ALLOWED)
    if rule is None:
        return _rejected(response, InteractionFailureCode.RESPONSE_NOT_MATCHED)
    if rule.grant_effect is None:
        status = (
            InteractionStatus.SELECTION_ACCEPTED
            if rule.selected_refs
            else InteractionStatus.NO_GRANT
        )
        return _build_outcome(
            response, status, selected_refs=rule.selected_refs
        )

    transaction = episode.begin_transaction()
    grant_id = transaction.allocate_id("grant")
    evidence = _interaction_evidence(response)
    template = rule.grant_effect
    grant = DelegationGrant(
        grant_id=grant_id,
        issuer_id=response.authenticated_principal_id,
        actor_id=actor_id,
        action_scope=template.action_scope,
        resource_refs=template.resource_refs,
        recipient_ids=template.recipient_ids,
        valid_from=response.received_at,
        expires_at=response.received_at + template.duration_ticks,
        source_turn_id=response.turn_id,
        source_request_id=request.request_id,
        source_rule_id=rule.rule_id,
        source_evidence=evidence,
    )
    transaction.replace_delegation_grants(
        (*transaction.staged_state.delegation_grants, grant)
    )
    transition = transaction.commit()
    return _build_outcome(
        response,
        InteractionStatus.GRANT_CREATED,
        selected_refs=rule.selected_refs,
        grant=grant,
        transition=transition,
    )


def _matching_rule(
    contract: InteractionContract, response: InteractionResponse
) -> UserResponseRule | None:
    return next(
        (
            rule
            for rule in contract.response_rules
            if rule.match.request_id == response.request_id
            and rule.authenticated_responder_id == response.authenticated_principal_id
            and rule.response_channel == response.channel.value
            and rule.response_text == response.response_text
        ),
        None,
    )


def _interaction_evidence(response: InteractionResponse) -> SourceEvidence:
    digest = sha256_digest(response)
    return SourceEvidence(
        evidence_id=f"evidence.interaction.{digest.removeprefix('sha256:')[:24]}",
        source_kind=EvidenceSourceKind.INTERACTION,
        source_id=response.turn_id,
        observed_at=response.received_at,
        content_digest=digest,
    )


def _rejected(
    response: InteractionResponse, code: InteractionFailureCode
) -> InteractionOutcome:
    return _build_outcome(
        response, InteractionStatus.REJECTED, failure_code=code
    )


def _build_outcome(
    response: InteractionResponse,
    status: InteractionStatus,
    *,
    failure_code: InteractionFailureCode | None = None,
    selected_refs: tuple[ResourceRef, ...] = (),
    grant: DelegationGrant | None = None,
    transition: StateTransitionRecord | None = None,
) -> InteractionOutcome:
    payload = {
        "schema_version": response.schema_version,
        "turn_id": response.turn_id,
        "request_id": response.request_id,
        "status": status,
        "failure_code": failure_code,
        "selected_refs": selected_refs,
        "grant": grant,
        "transition": transition,
    }
    digest_payload = InteractionOutcome.model_construct(
        **payload, outcome_digest="0" * 64
    ).model_dump(mode="json", exclude={"outcome_digest"}, exclude_none=False)
    return InteractionOutcome(**payload, outcome_digest=sha256_digest(digest_payload))


__all__ = [
    "InteractionFailureCode",
    "InteractionOutcome",
    "InteractionResponse",
    "InteractionStatus",
    "ResponseChannel",
    "apply_interaction_response",
]
