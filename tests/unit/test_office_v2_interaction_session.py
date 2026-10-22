from __future__ import annotations

import json

import pytest

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASE_BY_ID
from sandbox.scenarios.office_v2.interaction import (
    InteractionFailureCode,
    InteractionStatus,
    ResponseChannel,
)
from sandbox.scenarios.office_v2.interaction_session import (
    DeterministicInteractionSession,
    RequestClarificationArguments,
    ScriptedResponseDirective,
)
from sandbox.scenarios.office_v2.models import ResourceKind, ResourceRef
from sandbox.scenarios.office_v2.tools.contracts import (
    OfficeToolInvocation,
    OutputEvidence,
    ToolResultStatus,
    build_tool_result,
)
from sandbox.scenarios.office_v2.world import EpisodeWorld


def _proposal(case_id: str) -> RequestClarificationArguments:
    task = CLEAN_CASE_BY_ID[case_id].task
    request = task.user_response_script.requests[0]
    descriptions = {fact.fact_id: fact.description for fact in task.required_response_facts}
    return RequestClarificationArguments(
        question_kind=request.question_kind,
        candidate_refs=request.candidate_refs,
        missing_fact_descriptions=tuple(
            descriptions[fact_id] for fact_id in request.missing_fact_ids
        ),
        requested_action=(
            request.requested_action_scope.action
            if request.requested_action_scope is not None
            else None
        ),
        requested_resource_kinds=(
            request.requested_action_scope.resource_kinds
            if request.requested_action_scope is not None
            else ()
        ),
        requested_recipient_ids=request.requested_recipient_ids,
    )


def _visible_result(case_id: str):
    request = CLEAN_CASE_BY_ID[case_id].task.user_response_script.requests[0]
    arguments = {"case_id": case_id}
    invocation = OfficeToolInvocation(
        invocation_id=f"invocation.stage4.8.{case_id}",
        sequence=1,
        tool_name="search_visible_resources",
        actor_id="user.stage4.8",
        task_id=f"task.{case_id}",
        logical_time=1000,
        arguments=arguments,
        arguments_digest=sha256_digest(arguments),
        before_state_digest="1" * 64,
    )
    evidence = tuple(
        OutputEvidence(
            evidence_id=f"evidence.stage4.8.{case_id}.resource.{index}",
            invocation_id=invocation.invocation_id,
            invocation_sequence=invocation.sequence,
            field_path=("items", str(index), "resource"),
            resource_ref=ref,
            value_digest=sha256_digest(ref.model_dump(mode="json")),
        )
        for index, ref in enumerate(request.candidate_refs)
    ) + tuple(
        OutputEvidence(
            evidence_id=f"evidence.stage4.8.{case_id}.recipient.{index}",
            invocation_id=invocation.invocation_id,
            invocation_sequence=invocation.sequence,
            field_path=("items", str(index), "principal_id"),
            value_digest=sha256_digest(recipient_id),
        )
        for index, recipient_id in enumerate(request.requested_recipient_ids)
    )
    return build_tool_result(
        invocation=invocation,
        status=ToolResultStatus.SUCCEEDED,
        visible_output={"source": "actor_visible"},
        output_evidence=evidence,
    )


def _session(
    case_id: str,
    *,
    channel: ResponseChannel = ResponseChannel.AUTHENTICATED_TASK_SESSION,
    responder_id: str | None = None,
):
    case = CLEAN_CASE_BY_ID[case_id]
    request = case.task.user_response_script.requests[0]
    rule = case.task.user_response_script.response_rules[0]
    responder = responder_id or rule.authenticated_responder_id
    episode = EpisodeWorld(load_canonical_world(), episode_id=f"stage4.8.{case_id}")
    session = DeterministicInteractionSession(
        episode=episode,
        task=case.task,
        actor_id=case.actor.actor_id,
        response_directives=(
            ScriptedResponseDirective(
                request_id=request.request_id,
                rule_id=rule.rule_id,
                turn_id=f"turn.stage4.8.{case_id}",
                responder_id=responder,
                authenticated_principal_id=responder,
                channel=channel,
            ),
        ),
        visible_results=(_visible_result(case_id),),
    )
    return episode, session


@pytest.mark.parametrize(
    ("case_id", "expected_status"),
    (
        ("clean.t1.apollo", InteractionStatus.SELECTION_ACCEPTED),
        ("clean.t2.evergreen", InteractionStatus.NO_GRANT),
        ("clean.t9.apollo", InteractionStatus.GRANT_CREATED),
        ("clean.t9.borealis", InteractionStatus.GRANT_CREATED),
    ),
)
def test_four_clean_cases_apply_frozen_replies(
    case_id: str, expected_status: InteractionStatus
) -> None:
    episode, session = _session(case_id)
    before = episode.state_digest

    execution = session.handle_request(_proposal(case_id))

    assert execution.outcome is not None
    assert execution.outcome.status is expected_status
    assert execution.follow_up_user_message is not None
    assert execution.model_visible_payload()["status"] == "succeeded"
    if expected_status is InteractionStatus.GRANT_CREATED:
        assert episode.state_digest != before
        assert execution.outcome.grant is not None
        assert execution.outcome.grant.is_active(episode.state.logical_clock.now)
    else:
        assert episode.state_digest == before


@pytest.mark.parametrize(
    ("channel", "responder_id", "failure"),
    (
        (
            ResponseChannel.BUSINESS_CONTENT,
            None,
            InteractionFailureCode.UNTRUSTED_CHANNEL,
        ),
        (
            ResponseChannel.AUTHENTICATED_TASK_SESSION,
            "user.sofia.martinez",
            InteractionFailureCode.RESPONDER_NOT_ALLOWED,
        ),
    ),
)
def test_rejected_replies_do_not_change_state_or_become_user_messages(
    channel: ResponseChannel,
    responder_id: str | None,
    failure: InteractionFailureCode,
) -> None:
    episode, session = _session(
        "clean.t9.apollo", channel=channel, responder_id=responder_id
    )
    before = episode.state_digest

    execution = session.handle_request(_proposal("clean.t9.apollo"))

    assert execution.outcome is not None
    assert execution.outcome.status is InteractionStatus.REJECTED
    assert execution.outcome.failure_code is failure
    assert execution.follow_up_user_message is None
    assert execution.model_visible_payload()["status"] == "rejected"
    assert episode.state_digest == before
    assert episode.state.delegation_grants == ()


def test_same_turn_is_idempotent_and_grant_expires_at_half_open_boundary() -> None:
    episode, session = _session("clean.t9.apollo")
    first = session.handle_request(_proposal("clean.t9.apollo"))
    history_length = len(episode.history)
    state_digest = episode.state_digest

    repeated = session.handle_request(_proposal("clean.t9.apollo"))

    assert first.outcome is not None and first.outcome.grant is not None
    assert repeated.outcome is not None
    assert repeated.outcome.status is InteractionStatus.GRANT_ALREADY_APPLIED
    assert len(episode.history) == history_length
    assert episode.state_digest == state_digest

    transaction = episode.begin_transaction()
    transaction.advance_clock(5)
    transaction.commit()
    assert episode.state.logical_clock.now == first.outcome.grant.expires_at
    assert not first.outcome.grant.is_active(episode.state.logical_clock.now)


def test_grant_trace_is_neutral_ordered_digest_locked_and_deterministic() -> None:
    episode, session = _session("clean.t9.apollo")
    execution = session.handle_request(_proposal("clean.t9.apollo"))
    events = execution.neutral_trace_events()

    assert [event.event_type for event in events] == [
        "agent_clarification_requested",
        "user_response_received",
        "interaction_result",
        "delegation_grant_created",
    ]
    assert execution.outcome is not None
    assert execution.outcome.transition is not None
    grant_event = events[-1]
    assert grant_event.output_digest == execution.outcome.transition.transition_digest
    assert grant_event.data["before_state_digest"] != grant_event.data["after_state_digest"]
    assert grant_event.state_digest == episode.state_digest
    assert [event.state_digest for event in events[:2]] == [
        execution.before_state_digest,
        execution.before_state_digest,
    ]
    assert [event.state_digest for event in events[2:]] == [
        execution.after_state_digest,
        execution.after_state_digest,
    ]
    serialized = json.dumps(
        [event.data for event in events], sort_keys=True, ensure_ascii=False
    )
    response_text = execution.response.response_text if execution.response else ""
    for forbidden in (
        response_text,
        "rule.t9.apollo.review-event",
        "grant.",
        "risk_category",
        "SecurityFact",
        "utility",
    ):
        assert forbidden not in serialized

    _, repeated_session = _session("clean.t9.apollo")
    repeated = repeated_session.handle_request(
        _proposal("clean.t9.apollo")
    ).neutral_trace_events()
    assert events == repeated


def test_rejected_trace_has_no_grant_or_transition_and_preserves_digest() -> None:
    episode, session = _session(
        "clean.t9.apollo", channel=ResponseChannel.BUSINESS_CONTENT
    )
    before = episode.state_digest

    execution = session.handle_request(_proposal("clean.t9.apollo"))
    events = execution.neutral_trace_events()

    assert [event.event_type for event in events] == [
        "agent_clarification_requested",
        "user_response_received",
        "interaction_result",
    ]
    result = events[-1].data
    assert result["status"] == "rejected"
    assert result["failure_code"] == "untrusted_channel"
    assert result["transition_digest"] is None
    assert result["before_state_digest"] == result["after_state_digest"] == before
    assert all(event.state_digest == before for event in events)


def test_transaction_rollback_is_a_neutral_rejection_without_grant_event() -> None:
    case = CLEAN_CASE_BY_ID["clean.t9.apollo"]
    request = case.task.user_response_script.requests[0]
    rule = case.task.user_response_script.response_rules[0]
    assert rule.grant_effect is not None
    missing_ref = ResourceRef(
        kind=ResourceKind.DRIVE_FILE,
        resource_id="drive.apollo.missing-stage4-9",
    )
    invalid_rule = rule.model_copy(
        update={
            "grant_effect": rule.grant_effect.model_copy(
                update={"resource_refs": (missing_ref,)}
            )
        }
    )
    invalid_contract = case.task.user_response_script.model_copy(
        update={"response_rules": (invalid_rule,)}
    )
    task = case.task.model_copy(update={"user_response_script": invalid_contract})
    episode = EpisodeWorld(load_canonical_world(), episode_id="stage4.9.rollback")
    before = episode.state_digest
    session = DeterministicInteractionSession(
        episode=episode,
        task=task,
        actor_id=case.actor.actor_id,
        response_directives=(
            ScriptedResponseDirective(
                request_id=request.request_id,
                rule_id=rule.rule_id,
                turn_id="turn.stage4.9.rollback",
                responder_id=rule.authenticated_responder_id,
                authenticated_principal_id=rule.authenticated_responder_id,
            ),
        ),
        visible_results=(_visible_result(case.case_id),),
    )

    execution = session.handle_request(_proposal(case.case_id))
    events = execution.neutral_trace_events()

    assert execution.model_visible_payload() == {
        "status": "rejected",
        "error": "transaction_validation_failed",
    }
    assert [event.event_type for event in events] == [
        "agent_clarification_requested",
        "user_response_received",
        "interaction_result",
    ]
    assert events[-1].data["failure_code"] == "transaction_validation_failed"
    assert events[-1].data["transition_digest"] is None
    assert episode.state_digest == before
    assert episode.history[-1].committed is False
    assert episode.history[-1].state_delta.is_empty()
