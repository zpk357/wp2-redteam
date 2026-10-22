from __future__ import annotations

from dataclasses import dataclass

import pytest
from app.adapter.base import AdapterExecutionError
from app.adapter.langgraph_react_runtime import LangGraphReactRuntime
from app.agent.react_contract import (
    REQUEST_CLARIFICATION_TOOL_SPEC,
    SUBMIT_TOOL_SPEC,
    ReactToolCall,
)

from sandbox.agent_prompts import (
    render_office_v2_agent_system_prompt,
)
from sandbox.scenarios.office_v2.agent_api import OfficeV2AgentSessionSurface
from sandbox.scenarios.office_v2.agent_context import (
    assemble_agent_workspace_context,
    derive_agent_identity_context,
    derive_agent_policy_capability_context,
)
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASE_BY_ID
from sandbox.scenarios.office_v2.tools import office_v2_tool_definitions
from sandbox.scenarios.office_v2.tools.runtime import OfficeV2ToolRuntime
from sandbox.scenarios.office_v2.world import EpisodeWorld
from sandbox.tool_contracts import OFFICE_V2_TOOL_SPECS

# LangChain is not a declared dependency: the LangGraph adapter imports it lazily at the model
# boundary.  This test does the same, so a checkout without LangChain still collects and runs.
_langchain_messages = pytest.importorskip(
    "langchain_core.messages",
    reason="the LangGraph adapter needs LangChain, which this checkout does not install",
)
AIMessage = _langchain_messages.AIMessage


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
