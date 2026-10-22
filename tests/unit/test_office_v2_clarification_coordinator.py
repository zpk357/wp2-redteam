from __future__ import annotations

import json

import pytest
from app.adapter.base import AdapterExecutionError
from app.adapter.langgraph_react_runtime import LangGraphReactRuntime
from app.agent.react_contract import (
    REQUEST_CLARIFICATION_TOOL_SPEC,
    SUBMIT_TOOL_SPEC,
    ReactToolCall,
)

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASE_BY_ID
from sandbox.scenarios.office_v2.interaction_session import (
    ClarificationCoordinator,
    ClarificationMatchFailure,
    ClarificationMatchStatus,
    RequestClarificationArguments,
)
from sandbox.scenarios.office_v2.models import InteractionContract, QuestionKind
from sandbox.scenarios.office_v2.tools.contracts import (
    OfficeToolInvocation,
    OutputEvidence,
    ToolResultStatus,
    build_tool_result,
)


def _proposal(case_id: str) -> RequestClarificationArguments:
    task = CLEAN_CASE_BY_ID[case_id].task
    request = task.user_response_script.requests[0]
    descriptions = {
        fact.fact_id: fact.description for fact in task.required_response_facts
    }
    return RequestClarificationArguments(
        question_kind=request.question_kind,
        candidate_refs=request.candidate_refs,
        missing_fact_descriptions=tuple(
            descriptions[fact_id] for fact_id in request.missing_fact_ids
        ),
        requested_action=(
            None
            if request.requested_action_scope is None
            else request.requested_action_scope.action
        ),
        requested_resource_kinds=(
            ()
            if request.requested_action_scope is None
            else request.requested_action_scope.resource_kinds
        ),
        requested_recipient_ids=request.requested_recipient_ids,
    )


def _visible_result(case_id: str):
    request = CLEAN_CASE_BY_ID[case_id].task.user_response_script.requests[0]
    invocation = OfficeToolInvocation(
        invocation_id=f"invocation.clarification.{case_id}",
        sequence=1,
        tool_name="search_visible_resources",
        actor_id="user.clarification",
        task_id=f"task.{case_id}",
        logical_time=1000,
        arguments={"query": case_id},
        arguments_digest=sha256_digest({"query": case_id}),
        before_state_digest="1" * 64,
    )
    evidence = []
    for index, ref in enumerate(request.candidate_refs):
        evidence.append(
            OutputEvidence(
                evidence_id=f"evidence.clarification.{case_id}.resource.{index}",
                invocation_id=invocation.invocation_id,
                invocation_sequence=invocation.sequence,
                field_path=("items", str(index), "resource"),
                resource_ref=ref,
                value_digest=sha256_digest(ref.model_dump(mode="json")),
            )
        )
    for index, recipient_id in enumerate(request.requested_recipient_ids):
        evidence.append(
            OutputEvidence(
                evidence_id=f"evidence.clarification.{case_id}.recipient.{index}",
                invocation_id=invocation.invocation_id,
                invocation_sequence=invocation.sequence,
                field_path=("items", str(index), "principal_id"),
                value_digest=sha256_digest(recipient_id),
            )
        )
    return build_tool_result(
        invocation=invocation,
        status=ToolResultStatus.SUCCEEDED,
        visible_output={"source": "actor_visible"},
        output_evidence=tuple(evidence),
    )


def _coordinator(case_id: str, *, with_sources: bool = True):
    task = CLEAN_CASE_BY_ID[case_id].task
    return ClarificationCoordinator(
        contract=task.user_response_script,
        task=task,
        visible_results=((_visible_result(case_id),) if with_sources else ()),
    )


@pytest.mark.parametrize(
    ("case_id", "kind"),
    (
        ("clean.t1.apollo", QuestionKind.DISAMBIGUATION),
        ("clean.t2.evergreen", QuestionKind.MISSING_VALUE),
        ("clean.t9.apollo", QuestionKind.AUTHORIZATION),
    ),
)
def test_three_question_kinds_match_one_frozen_request_without_exposing_authority(
    case_id: str,
    kind: QuestionKind,
) -> None:
    result = _coordinator(case_id).match(_proposal(case_id))

    assert result.status is ClarificationMatchStatus.MATCHED
    assert result.matched_request is not None
    assert result.matched_request.question_kind is kind
    if kind is QuestionKind.MISSING_VALUE:
        assert result.source_task_fact_ids == result.matched_request.missing_fact_ids
    visible = result.model_visible_payload()
    assert visible == {"status": "matched", "error": None}
    serialized = json.dumps(visible)
    for hidden in (
        "request_id",
        "allowed_responder_ids",
        "requested_at",
        "duration_ticks",
        "rule_id",
    ):
        assert hidden not in serialized


def test_zero_and_multiple_semantic_matches_are_closed_failures() -> None:
    case = CLEAN_CASE_BY_ID["clean.t1.apollo"]
    proposal = _proposal(case.case_id)
    no_match = proposal.model_copy(
        update={"candidate_refs": tuple(reversed(proposal.candidate_refs))}
    )
    zero = _coordinator(case.case_id).match(no_match)

    request = case.task.user_response_script.requests[0]
    duplicate = request.model_copy(update={"request_id": "request.duplicate.semantic"})
    ambiguous_contract = InteractionContract(
        requests=(request, duplicate),
        response_rules=case.task.user_response_script.response_rules,
    )
    ambiguous = ClarificationCoordinator(
        contract=ambiguous_contract,
        task=case.task,
        visible_results=(_visible_result(case.case_id),),
    ).match(proposal)

    assert zero.failure_code is ClarificationMatchFailure.NO_FROZEN_MATCH
    assert ambiguous.failure_code is ClarificationMatchFailure.AMBIGUOUS_FROZEN_MATCH
    assert zero.matched_request is ambiguous.matched_request is None


def test_visible_source_is_required_and_duplicate_request_is_rejected() -> None:
    case_id = "clean.t9.apollo"
    proposal = _proposal(case_id)
    missing_source = _coordinator(case_id, with_sources=False).match(proposal)
    coordinator = _coordinator(case_id)
    first = coordinator.match(proposal)
    repeated = coordinator.match(proposal)

    assert missing_source.failure_code is ClarificationMatchFailure.VISIBLE_SOURCE_MISSING
    assert first.status is ClarificationMatchStatus.MATCHED
    assert first.source_evidence_ids
    assert repeated.failure_code is ClarificationMatchFailure.REQUEST_ALREADY_PENDING


def test_control_schema_omits_authority_fields_and_control_calls_are_exclusive() -> None:
    schema = REQUEST_CLARIFICATION_TOOL_SPEC.arguments_model.model_json_schema()
    serialized = json.dumps(schema, sort_keys=True)
    for forbidden in (
        "request_id",
        "allowed_responder_ids",
        "requested_at",
        "duration_ticks",
        "rule_id",
    ):
        assert forbidden not in serialized

    clarification = ReactToolCall(
        call_id="clarify",
        name=REQUEST_CLARIFICATION_TOOL_SPEC.name,
        arguments=_proposal("clean.t1.apollo").model_dump(mode="json"),
    )
    business = ReactToolCall(call_id="business", name="search_email", arguments={})
    submit = ReactToolCall(
        call_id="submit",
        name=SUBMIT_TOOL_SPEC.name,
        arguments={"answer": "done"},
    )
    controls = {
        REQUEST_CLARIFICATION_TOOL_SPEC.name,
        SUBMIT_TOOL_SPEC.name,
    }

    for calls, expected in (
        ([clarification, business], "trace_mixed_control_batch"),
        ([clarification, submit], "trace_mixed_submit_batch"),
        ([clarification, clarification], "trace_duplicate_control_call"),
    ):
        with pytest.raises(AdapterExecutionError) as raised:
            LangGraphReactRuntime._validate_call_batch(
                calls,
                control_tool_names=controls,
            )
        assert raised.value.error_code == expected
