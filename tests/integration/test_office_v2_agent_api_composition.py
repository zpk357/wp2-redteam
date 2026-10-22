from __future__ import annotations

import json

import pytest
from app.agent.react_contract import REQUEST_CLARIFICATION_TOOL_SPEC

from sandbox.agent_prompts import render_office_v2_agent_system_prompt
from sandbox.scenarios.office_v2.agent_api import OfficeV2AgentSessionSurface
from sandbox.scenarios.office_v2.agent_context import (
    assemble_agent_workspace_context,
    derive_agent_identity_context,
    derive_agent_policy_capability_context,
)
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASE_BY_ID
from sandbox.scenarios.office_v2.interaction import InteractionStatus, ResponseChannel
from sandbox.scenarios.office_v2.interaction_session import (
    DeterministicInteractionSession,
    RequestClarificationArguments,
    ScriptedResponseDirective,
)
from sandbox.scenarios.office_v2.tools import office_v2_tool_definitions
from sandbox.scenarios.office_v2.tools.runtime import OfficeV2ToolRuntime
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


def _surface(
    case_id: str,
    *,
    channel: ResponseChannel = ResponseChannel.AUTHENTICATED_TASK_SESSION,
    responder_id: str | None = None,
):
    canonical = load_canonical_world()
    case = CLEAN_CASE_BY_ID[case_id]
    episode = EpisodeWorld(canonical, episode_id=f"stage4.10.{case_id}")
    definitions = office_v2_tool_definitions()
    identity = derive_agent_identity_context(canonical.state, case.actor, case.task)
    policy = derive_agent_policy_capability_context(
        canonical.state, case.task, definitions
    )
    context = assemble_agent_workspace_context(identity, policy)
    runtime = OfficeV2ToolRuntime(
        episode=episode,
        actor=case.actor,
        task=case.task,
        definitions=definitions,
        bindings=case.resolved_bindings,
    )
    request = case.task.user_response_script.requests[0]
    rule = case.task.user_response_script.response_rules[0]
    responder = responder_id or rule.authenticated_responder_id
    interaction = DeterministicInteractionSession(
        episode=episode,
        task=case.task,
        actor_id=case.actor.actor_id,
        response_directives=(
            ScriptedResponseDirective(
                request_id=request.request_id,
                rule_id=rule.rule_id,
                turn_id=f"turn.stage4.10.{case_id}",
                responder_id=responder,
                authenticated_principal_id=responder,
                channel=channel,
            ),
        ),
    )

    def handle_control(name: str, arguments: dict):
        assert name == REQUEST_CLARIFICATION_TOOL_SPEC.name
        return interaction.handle_request(arguments)

    surface = OfficeV2AgentSessionSurface(
        rendered_prompt=render_office_v2_agent_system_prompt(context),
        runtime=runtime,
        control_tool_specs=(REQUEST_CLARIFICATION_TOOL_SPEC,),
        control_handler=handle_control,
        business_result_observer=interaction.observe_result,
    )
    return canonical, case, episode, context, surface


def _observe_all(
    surface: OfficeV2AgentSessionSurface, tool_name: str
) -> list[dict[str, object]]:
    page_token = None
    observed: list[dict[str, object]] = []
    while True:
        arguments = {"page_size": 25}
        if page_token is not None:
            arguments["page_token"] = page_token
        payload = surface.execute_business_tool(tool_name, arguments).model_visible_payload()
        assert payload["status"] == "succeeded"
        data = payload["data"]
        assert isinstance(data, dict)
        items = data["items"]
        assert isinstance(items, list)
        assert all(isinstance(item, dict) for item in items)
        observed.extend(items)
        if data["has_more"] is not True:
            return observed
        page_token = data["next_page_token"]
        assert isinstance(page_token, str)


def _observe_clarification_sources(
    surface: OfficeV2AgentSessionSurface, case_id: str
) -> None:
    request = CLEAN_CASE_BY_ID[case_id].task.user_response_script.requests[0]
    if request.candidate_refs:
        kinds = {ref.kind.value for ref in request.candidate_refs}
        if "drive_file" in kinds:
            items = _observe_all(surface, "search_drive_files")
            by_file_id = {str(item["file_id"]): item for item in items}
            for ref in request.candidate_refs:
                item = by_file_id[ref.resource_id]
                version_id = ref.version_id or str(item["current_version_id"])
                result = surface.execute_business_tool(
                    "read_drive_file",
                    {"file_id": ref.resource_id, "version_id": version_id},
                ).model_visible_payload()
                assert result["status"] == "succeeded"
    if request.requested_recipient_ids:
        _observe_all(surface, "search_calendar_events")


@pytest.mark.parametrize(
    ("case_id", "expected"),
    (
        ("clean.t1.apollo", InteractionStatus.SELECTION_ACCEPTED),
        ("clean.t2.evergreen", InteractionStatus.NO_GRANT),
        ("clean.t9.apollo", InteractionStatus.GRANT_CREATED),
        ("clean.t9.borealis", InteractionStatus.GRANT_CREATED),
    ),
)
def test_real_surface_composes_four_multiturn_cases_from_actual_tool_evidence(
    case_id: str, expected: InteractionStatus
) -> None:
    canonical, case, episode, context, surface = _surface(case_id)
    before = episode.state_digest
    _observe_clarification_sources(surface, case_id)

    execution = surface.handle_control_call(
        REQUEST_CLARIFICATION_TOOL_SPEC.name,
        _proposal(case_id).model_dump(mode="json"),
    )

    assert execution.outcome is not None
    assert execution.outcome.status is expected
    assert execution.coordination.source_evidence_ids or not (
        case.task.user_response_script.requests[0].candidate_refs
        or case.task.user_response_script.requests[0].requested_recipient_ids
    )
    assert execution.follow_up_user_message is not None
    assert context.actor_display_name in surface.system_message
    assert case.case_id not in surface.system_message
    trusted_trace = execution.neutral_trace_events()
    assert trusted_trace[0].event_type == "agent_clarification_requested"
    assert trusted_trace[-1].event_type == (
        "delegation_grant_created"
        if expected is InteractionStatus.GRANT_CREATED
        else "interaction_result"
    )
    if expected is InteractionStatus.GRANT_CREATED:
        assert episode.state_digest != before
        assert len(episode.state.delegation_grants) == 1
    else:
        assert episode.state_digest == before
    assert load_canonical_world().world_digest == canonical.world_digest


def test_two_actor_sessions_pair_visible_context_with_hidden_sources() -> None:
    first = _surface("clean.t1.apollo")
    second = _surface("clean.t2.evergreen")
    first_context, first_surface = first[3], first[4]
    second_context, second_surface = second[3], second[4]

    assert first_context.actor_display_name != second_context.actor_display_name
    assert first_context.actor_email != second_context.actor_email
    assert first_context.context_digest != second_context.context_digest
    assert first_surface.business_tool_specs is second_surface.business_tool_specs
    assert first_surface.prompt_digest != second_surface.prompt_digest
    serialized = json.dumps(
        [
            first_context.model_visible_payload(),
            second_context.model_visible_payload(),
        ],
        sort_keys=True,
    )
    assert first[1].actor.actor_id not in serialized
    assert second[1].actor.actor_id not in serialized


@pytest.mark.parametrize(
    ("channel", "responder_id", "failure_code"),
    (
        (ResponseChannel.BUSINESS_CONTENT, None, "untrusted_channel"),
        (
            ResponseChannel.AUTHENTICATED_TASK_SESSION,
            "user.sofia.martinez",
            "responder_not_allowed",
        ),
    ),
)
def test_real_surface_rejections_pair_visible_failure_with_unchanged_state(
    channel: ResponseChannel,
    responder_id: str | None,
    failure_code: str,
) -> None:
    _, _, episode, _, surface = _surface(
        "clean.t9.apollo", channel=channel, responder_id=responder_id
    )
    before = episode.state_digest
    _observe_clarification_sources(surface, "clean.t9.apollo")

    execution = surface.handle_control_call(
        REQUEST_CLARIFICATION_TOOL_SPEC.name,
        _proposal("clean.t9.apollo").model_dump(mode="json"),
    )

    assert execution.model_visible_payload() == {
        "status": "rejected",
        "error": failure_code,
    }
    assert execution.follow_up_user_message is None
    assert episode.state_digest == before
    assert episode.state.delegation_grants == ()
    assert all(
        event.event_type != "delegation_grant_created"
        for event in execution.neutral_trace_events()
    )
