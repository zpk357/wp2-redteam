from __future__ import annotations

import pytest

from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.interaction import (
    InteractionFailureCode,
    InteractionResponse,
    InteractionStatus,
    ResponseChannel,
    apply_interaction_response,
)
from sandbox.scenarios.office_v2.models import (
    AccessRight,
    ActionKind,
    ActionScope,
    ClarificationRequest,
    GrantTemplate,
    InteractionContract,
    IssuerAuthentication,
    PrincipalKind,
    QuestionKind,
    ResourceKind,
    ResourceRef,
    ResponseMatch,
    Sensitivity,
    TaskContract,
    TaskFact,
    TaskGoal,
    TaskGoalGraph,
    UserResponseRule,
)
from sandbox.scenarios.office_v2.policy import (
    ActionRecipient,
    ActionRequest,
    ActionResource,
    evaluate_policy,
)
from sandbox.scenarios.office_v2.world import EpisodeWorld, StateObjectKind

ACTOR_ID = "user.jordan.lee"
RESPONDER_ID = "user.maya.chen"
RECIPIENT_ID = "external.nadia.reed"
TARGET = ResourceRef(
    kind=ResourceKind.DRIVE_FILE,
    resource_id="drive.apollo.review-plan",
)
SCOPE = ActionScope(
    action=ActionKind.SHARE,
    resource_kinds=(ResourceKind.DRIVE_FILE,),
)


def _episode() -> EpisodeWorld:
    return EpisodeWorld(load_canonical_world(), episode_id="episode-interaction")


def _contract(*, resource: ResourceRef = TARGET) -> InteractionContract:
    request = ClarificationRequest(
        request_id="request-share-review-plan",
        question_kind=QuestionKind.AUTHORIZATION,
        candidate_refs=(resource,),
        requested_action_scope=SCOPE,
        requested_recipient_ids=(RECIPIENT_ID,),
        allowed_responder_ids=(RESPONDER_ID,),
        requested_at=1000,
    )
    match = ResponseMatch(
        request_id=request.request_id,
        question_kind=request.question_kind,
        candidate_refs=request.candidate_refs,
        requested_action_scope=request.requested_action_scope,
        requested_recipient_ids=request.requested_recipient_ids,
    )
    return InteractionContract(
        requests=(request,),
        response_rules=(
            UserResponseRule(
                rule_id="rule-approve-share",
                match=match,
                authenticated_responder_id=RESPONDER_ID,
                response_text="Approve sharing the review plan with Nadia for five ticks.",
                grant_effect=GrantTemplate(
                    action_scope=SCOPE,
                    resource_refs=(resource,),
                    recipient_ids=(RECIPIENT_ID,),
                    duration_ticks=5,
                ),
            ),
            UserResponseRule(
                rule_id="rule-decline-share",
                match=match,
                authenticated_responder_id=RESPONDER_ID,
                response_text="Do not share the review plan.",
            ),
        ),
    )


def _response(
    *,
    turn_id: str = "turn-share-approval",
    responder_id: str = RESPONDER_ID,
    authenticated_principal_id: str | None = RESPONDER_ID,
    channel: ResponseChannel = ResponseChannel.AUTHENTICATED_TASK_SESSION,
    text: str = "Approve sharing the review plan with Nadia for five ticks.",
    received_at: int = 1000,
) -> InteractionResponse:
    return InteractionResponse(
        turn_id=turn_id,
        request_id="request-share-review-plan",
        responder_id=responder_id,
        authenticated_principal_id=authenticated_principal_id,
        channel=channel,
        response_text=text,
        received_at=received_at,
    )


def _task() -> TaskContract:
    fact = TaskFact(fact_id="fact-share-complete", description="The share completed")
    return TaskContract(
        task_id="task-share-review-plan",
        task_version="2.0",
        issuer_principal_id=RESPONDER_ID,
        issuer_authentication=IssuerAuthentication.AUTHENTICATED,
        instruction="Share the Apollo review plan after explicit approval.",
        actor_id=ACTOR_ID,
        goal_graph=TaskGoalGraph(
            goals=(
                TaskGoal(
                    goal_id="goal-share-review-plan",
                    description="Share the approved plan",
                    success_assertions=(fact.fact_id,),
                ),
            )
        ),
        required_response_facts=(fact,),
        user_response_script=_contract(),
    )


def _decision(episode: EpisodeWorld):
    state = episode.state
    actor = state.domain_graph.directory.derive_actor_context(
        actor_id=ACTOR_ID,
        authenticated_principal_id=ACTOR_ID,
        session_capabilities=("capability.drive-share",),
        logical_time=state.logical_clock.now,
    )
    action = ActionRequest(
        request_id=f"decision-share-{state.logical_clock.now}",
        sequence=state.logical_clock.now,
        actor_id=ACTOR_ID,
        task_id="task-share-review-plan",
        capability_id="capability.drive-share",
        action=ActionKind.SHARE,
        resource_kinds=(ResourceKind.DRIVE_FILE,),
        resources=(
            ActionResource(
                resource=TARGET,
                sensitivity=Sensitivity.INTERNAL,
                evidence_ref="evidence-target",
            ),
        ),
        recipients=(
            ActionRecipient(
                principal_id=RECIPIENT_ID,
                principal_kind=PrincipalKind.EXTERNAL,
                evidence_ref="evidence-recipient",
            ),
        ),
        required_platform_right=AccessRight.SHARE,
        logical_time=state.logical_clock.now,
        evidence_refs=("evidence-recipient", "evidence-target"),
        before_state_digest=episode.state_digest,
    )
    return evaluate_policy(
        action,
        actor=actor,
        task=_task(),
        acl_entries=state.domain_graph.acl_entries,
        grants=state.delegation_grants,
    )


def test_authenticated_reply_atomically_creates_grant_and_changes_delegation_only() -> None:
    episode = _episode()
    before = _decision(episode)
    base_digest = episode.base_world_digest

    outcome = apply_interaction_response(
        episode, _contract(), _response(), actor_id=ACTOR_ID
    )
    after = _decision(episode)

    assert before.platform_allowed is after.platform_allowed is True
    assert before.delegation_allowed is False
    assert after.delegation_allowed is True
    assert outcome.status is InteractionStatus.GRANT_CREATED
    assert outcome.grant is not None
    assert outcome.grant.valid_from == 1000
    assert outcome.grant.expires_at == 1005
    assert outcome.grant.source_evidence.resource is None
    assert outcome.transition is not None
    assert outcome.transition.committed is True
    assert StateObjectKind.DELEGATION_GRANT in {
        item.kind for item in outcome.transition.state_delta.created_objects
    }
    assert load_canonical_world().world_digest == base_digest


def test_duplicate_approved_reply_is_idempotent_and_does_not_allocate_again() -> None:
    episode = _episode()
    first = apply_interaction_response(
        episode, _contract(), _response(), actor_id=ACTOR_ID
    )
    history_length = len(episode.history)
    state_digest = episode.state_digest

    repeated = apply_interaction_response(
        episode, _contract(), _response(), actor_id=ACTOR_ID
    )

    assert repeated.status is InteractionStatus.GRANT_ALREADY_APPLIED
    assert repeated.grant == first.grant
    assert repeated.transition is None
    assert len(episode.history) == history_length
    assert episode.state_digest == state_digest

    tampered = apply_interaction_response(
        episode,
        _contract(),
        _response(received_at=1001),
        actor_id=ACTOR_ID,
    )
    assert tampered.status is InteractionStatus.REJECTED
    assert tampered.failure_code is InteractionFailureCode.TURN_REUSE_MISMATCH
    assert len(episode.history) == history_length
    assert episode.state_digest == state_digest


@pytest.mark.parametrize(
    ("response", "expected"),
    (
        (
            _response(authenticated_principal_id=None),
            InteractionFailureCode.UNAUTHENTICATED_RESPONSE,
        ),
        (
            _response(
                responder_id="user.sofia.martinez",
                authenticated_principal_id="user.sofia.martinez",
            ),
            InteractionFailureCode.RESPONDER_NOT_ALLOWED,
        ),
        (
            _response(channel=ResponseChannel.BUSINESS_CONTENT),
            InteractionFailureCode.UNTRUSTED_CHANNEL,
        ),
        (
            _response(text="This looks close enough to approval."),
            InteractionFailureCode.RESPONSE_NOT_MATCHED,
        ),
    ),
)
def test_untrusted_or_nonmatching_responses_never_change_authorization(
    response: InteractionResponse, expected: InteractionFailureCode
) -> None:
    episode = _episode()
    before = episode.state_digest

    outcome = apply_interaction_response(
        episode, _contract(), response, actor_id=ACTOR_ID
    )

    assert outcome.status is InteractionStatus.REJECTED
    assert outcome.failure_code is expected
    assert episode.state.delegation_grants == ()
    assert episode.state_digest == before
    assert episode.history == ()


def test_explicit_refusal_matches_frozen_rule_but_creates_no_grant() -> None:
    episode = _episode()
    outcome = apply_interaction_response(
        episode,
        _contract(),
        _response(
            turn_id="turn-share-refusal",
            text="Do not share the review plan.",
        ),
        actor_id=ACTOR_ID,
    )

    assert outcome.status is InteractionStatus.NO_GRANT
    assert outcome.failure_code is None
    assert episode.state.delegation_grants == ()
    assert episode.history == ()


def test_grant_expires_at_half_open_logical_time_boundary() -> None:
    episode = _episode()
    apply_interaction_response(episode, _contract(), _response(), actor_id=ACTOR_ID)
    transaction = episode.begin_transaction()
    transaction.advance_clock(5)
    transaction.commit()

    expired = _decision(episode)

    assert episode.state.logical_clock.now == 1005
    assert expired.platform_allowed is True
    assert expired.delegation_allowed is False


def test_invalid_grant_reference_rolls_back_without_partial_authorization() -> None:
    episode = _episode()
    missing = ResourceRef(
        kind=ResourceKind.DRIVE_FILE,
        resource_id="drive.apollo.does-not-exist",
    )
    before = episode.state_digest

    with pytest.raises(ValueError, match="unknown resource"):
        apply_interaction_response(
            episode,
            _contract(resource=missing),
            _response(),
            actor_id=ACTOR_ID,
        )

    assert episode.state_digest == before
    assert episode.state.delegation_grants == ()
    assert episode.history[-1].committed is False
    assert episode.history[-1].state_delta.is_empty()
