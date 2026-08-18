from __future__ import annotations

import json
from dataclasses import dataclass

import pytest
from app.adapter.base import AdapterExecutionError
from app.adapter.langgraph_react_runtime import LangGraphReactRuntime
from app.agent.react_contract import (
    REQUEST_CLARIFICATION_TOOL_SPEC,
    SUBMIT_TOOL_SPEC,
    ReactToolCall,
)
from app.protocol import ExecutionRequest
from app.tools.base import ToolRegistry
from langchain_core.messages import AIMessage, SystemMessage

from sandbox.agent_prompts import (
    OFFICE_AGENT_SYSTEM_PROMPT,
    OFFICE_AGENT_SYSTEM_PROMPT_DIGEST,
    OFFICE_AGENT_SYSTEM_PROMPT_VERSION,
    render_office_v2_agent_system_prompt,
)
from sandbox.scenarios.office_episode import build_office_episode_initialization
from sandbox.scenarios.office_matrix import OFFICE_V1_TEST_MATRIX
from sandbox.scenarios.office_v2.agent_api import OfficeV2AgentSessionSurface
from sandbox.scenarios.office_v2.agent_context import (
    assemble_agent_workspace_context,
    derive_agent_identity_context,
    derive_agent_policy_capability_context,
)
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASE_BY_ID
from sandbox.scenarios.office_v2.interaction_session import (
    DeterministicInteractionSession,
    ScriptedResponseDirective,
)
from sandbox.scenarios.office_v2.tools import office_v2_tool_definitions
from sandbox.scenarios.office_v2.tools.runtime import OfficeV2ToolRuntime
from sandbox.scenarios.office_v2.world import EpisodeWorld
from sandbox.tool_contracts import OFFICE_SCENARIO_TOOL_SPECS, OFFICE_V2_TOOL_SPECS


def _v1_request() -> ExecutionRequest:
    case = OFFICE_V1_TEST_MATRIX.clean_cases[0]
    initialization = build_office_episode_initialization(case)
    return ExecutionRequest(
        execution_id="office-v2-session-injection",
        case_id=case.case_id,
        scenario_id=case.scenario.template_id,
        prompt=case.benign_task.instruction,
        max_steps=case.budget.max_steps,
        timeout_seconds=case.budget.timeout_seconds,
        seed=case.seed,
        scenario_initialization=initialization.model_dump(mode="json"),
    )


@dataclass(frozen=True)
class _TerminalControlExecution:
    final_answer: str
    follow_up_user_message: None = None

    def model_visible_payload(self):
        return None


def _v2_surface(
    *,
    control_handler=None,
    business_result_observer=None,
) -> OfficeV2AgentSessionSurface:
    canonical = load_canonical_world()
    case = CLEAN_CASE_BY_ID["clean.t2.evergreen"]
    identity = derive_agent_identity_context(canonical.state, case.actor, case.task)
    policy = derive_agent_policy_capability_context(
        canonical.state,
        case.task,
        office_v2_tool_definitions(),
    )
    context = assemble_agent_workspace_context(identity, policy)
    rendered = render_office_v2_agent_system_prompt(context)
    runtime = OfficeV2ToolRuntime(
        episode=EpisodeWorld(canonical, episode_id="agent-session-surface"),
        actor=case.actor,
        task=case.task,
        definitions=office_v2_tool_definitions(),
        bindings=case.resolved_bindings,
    )

    def handle_control(name: str, arguments: dict):
        assert name == SUBMIT_TOOL_SPEC.name
        return _TerminalControlExecution(
            SUBMIT_TOOL_SPEC.validate_arguments(arguments).answer
        )

    return OfficeV2AgentSessionSurface(
        rendered_prompt=rendered,
        runtime=runtime,
        control_tool_specs=(REQUEST_CLARIFICATION_TOOL_SPEC, SUBMIT_TOOL_SPEC),
        control_handler=control_handler or handle_control,
        business_result_observer=business_result_observer,
    )


class SubmitOnlyChatModel:
    def __init__(self) -> None:
        self.bound_tools = []
        self.inputs = []

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    async def ainvoke(self, messages):
        self.inputs.append(tuple(messages))
        return AIMessage(
            content="",
            tool_calls=[
                {"name": "submit", "args": {"answer": "done"}, "id": "v2-submit"}
            ],
        )


class ClarificationThenSubmitChatModel(SubmitOnlyChatModel):
    async def ainvoke(self, messages):
        self.inputs.append(tuple(messages))
        if len(self.inputs) == 1:
            case = CLEAN_CASE_BY_ID["clean.t2.evergreen"]
            request = case.task.user_response_script.requests[0]
            descriptions = {
                fact.fact_id: fact.description
                for fact in case.task.required_response_facts
            }
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "request_clarification",
                        "args": {
                            "question_kind": request.question_kind.value,
                            "missing_fact_descriptions": [
                                descriptions[item] for item in request.missing_fact_ids
                            ],
                        },
                        "id": "v2-clarification",
                    }
                ],
            )
        return AIMessage(
            content="",
            tool_calls=[
                {"name": "submit", "args": {"answer": "continued"}, "id": "v2-submit"}
            ],
        )


def test_default_v1_surface_preserves_prompt_specs_and_initial_messages() -> None:
    request = _v1_request()
    registry = ToolRegistry()
    registry.enable_office_episode(request.scenario_initialization)
    surface = LangGraphReactRuntime.v1_session_surface(registry)
    messages = LangGraphReactRuntime._initial_messages(request, surface)

    assert surface.system_message == OFFICE_AGENT_SYSTEM_PROMPT
    assert surface.prompt_version == OFFICE_AGENT_SYSTEM_PROMPT_VERSION
    assert surface.prompt_digest == OFFICE_AGENT_SYSTEM_PROMPT_DIGEST
    assert surface.business_tool_specs == OFFICE_SCENARIO_TOOL_SPECS
    assert surface.control_tool_specs == (SUBMIT_TOOL_SPEC,)
    assert [(message.role, message.content) for message in messages] == [
        ("system", OFFICE_AGENT_SYSTEM_PROMPT),
        ("user", request.prompt),
    ]


def test_v2_surface_binds_real_prompt_tools_runtime_and_visible_projection() -> None:
    surface = _v2_surface()
    projection = surface.execute_business_tool(
        "read_drive_file",
        {
            "file_id": "drive.evergreen.public-brief",
            "version_id": "version.evergreen.public-brief.1",
        },
    )

    assert surface.business_tool_specs is OFFICE_V2_TOOL_SPECS
    assert len(surface.business_tool_specs) == 17
    assert surface.prompt_digest == surface.rendered_prompt.envelope.system_message_digest
    assert projection.model_visible_payload()["status"] == "succeeded"
    assert projection.trusted_result.policy_decision is not None


async def test_langgraph_accepts_v2_surface_only_by_constructor_injection() -> None:
    model = SubmitOnlyChatModel()
    surface = _v2_surface()
    events = [
        event
        async for event in LangGraphReactRuntime(
            chat_model=model,
            session_surface=surface,
        ).execute(_v1_request())
    ]

    assert isinstance(model.inputs[0][0], SystemMessage)
    assert model.inputs[0][0].content == surface.system_message
    assert [item["function"]["name"] for item in model.bound_tools] == [
        *(spec.name for spec in OFFICE_V2_TOOL_SPECS),
        "request_clarification",
        "submit",
    ]
    assert events[-1].event_type == "execution_finished"
    assert events[-1].data["final_answer"] == "done"


async def test_langgraph_reinjects_deterministic_authenticated_reply() -> None:
    canonical = load_canonical_world()
    case = CLEAN_CASE_BY_ID["clean.t2.evergreen"]
    request = case.task.user_response_script.requests[0]
    rule = case.task.user_response_script.response_rules[0]
    interaction = DeterministicInteractionSession(
        episode=EpisodeWorld(canonical, episode_id="agent-session-interaction"),
        task=case.task,
        actor_id=case.actor.actor_id,
        response_directives=(
            ScriptedResponseDirective(
                request_id=request.request_id,
                rule_id=rule.rule_id,
                turn_id="turn.t2.evergreen.response",
                responder_id=rule.authenticated_responder_id,
                authenticated_principal_id=rule.authenticated_responder_id,
            ),
        ),
    )

    def handle_control(name: str, arguments: dict):
        if name == REQUEST_CLARIFICATION_TOOL_SPEC.name:
            return interaction.handle_request(arguments)
        return _TerminalControlExecution(
            SUBMIT_TOOL_SPEC.validate_arguments(arguments).answer
        )

    model = ClarificationThenSubmitChatModel()
    events = [
        event
        async for event in LangGraphReactRuntime(
            chat_model=model,
            session_surface=_v2_surface(
                control_handler=handle_control,
                business_result_observer=interaction.observe_result,
            ),
        ).execute(_v1_request())
    ]

    second_turn = model.inputs[1]
    assert second_turn[-2].name == REQUEST_CLARIFICATION_TOOL_SPEC.name
    assert json.loads(second_turn[-2].content) == {
        "outcome": "no_grant",
        "selected_refs": [],
        "status": "succeeded",
    }
    assert second_turn[-1].content.startswith("Authenticated task-session response")
    interaction_types = [
        event.event_type
        for event in events
        if event.source == "trace.office.interaction"
    ]
    assert interaction_types == [
        "agent_clarification_requested",
        "user_response_received",
        "interaction_result",
    ]
    control_types = [
        event.event_type
        for event in events
        if event.source in {"agent_control", "trace.office.interaction"}
    ]
    assert control_types == [
        "tool_call",
        *interaction_types,
        "tool_result",
    ]
    serialized_events = json.dumps(
        [event.model_dump(mode="json") for event in events],
        sort_keys=True,
        ensure_ascii=False,
    )
    for forbidden in (
        rule.response_text,
        rule.rule_id,
        "grant_id",
        "risk_category",
        "SecurityFact",
        "utility",
    ):
        assert forbidden not in serialized_events
    assert events[-1].data["final_answer"] == "continued"


def test_submit_exclusivity_is_unchanged_for_session_surfaces() -> None:
    calls = [
        ReactToolCall(call_id="business", name="search_email", arguments={}),
        ReactToolCall(
            call_id="submit",
            name="submit",
            arguments={"answer": "done"},
        ),
    ]

    with pytest.raises(AdapterExecutionError) as raised:
        LangGraphReactRuntime._validate_call_batch(calls)

    assert raised.value.error_code == "trace_mixed_submit_batch"
