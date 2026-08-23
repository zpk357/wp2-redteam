"""Runtime-neutral construction of the formal Office V2 Agent surface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.adapter.base import AdapterConfigurationError, AdapterExecutionError
from app.agent.react_contract import (
    REQUEST_CLARIFICATION_TOOL_SPEC,
    SUBMIT_TOOL_SPEC,
)
from app.office_v2_session import (
    OfficeV2ContainerSession,
    OfficeV2SessionSnapshot,
    load_office_v2_session,
)
from app.protocol import ExecutionRequest
from sandbox.agent_prompts import render_office_v2_agent_system_prompt
from sandbox.scenarios.office_v2.agent_api import OfficeV2AgentSessionSurface
from sandbox.scenarios.office_v2.agent_context import (
    assemble_agent_workspace_context,
    derive_agent_identity_context,
    derive_agent_policy_capability_context,
)
from sandbox.scenarios.office_v2.interaction_session import (
    DeterministicInteractionSession,
    ScriptedResponseDirective,
)


@dataclass(frozen=True, slots=True)
class OfficeV2SubmitExecution:
    final_answer: str
    follow_up_user_message: None = None

    def model_visible_payload(self) -> None:
        return None

    def neutral_trace_events(self) -> tuple[()]:
        return ()


def build_office_v2_runtime_surface(
    request: ExecutionRequest,
    *,
    snapshot: OfficeV2SessionSnapshot | None = None,
) -> tuple[OfficeV2ContainerSession, OfficeV2AgentSessionSurface]:
    """Bind one frozen request to the shared state, interaction, and tool runtime."""

    envelope = request.office_v2_execution
    if envelope is None:
        raise AdapterConfigurationError(
            "v2_configuration_error",
            "Office V2 execution requires its frozen envelope",
        )
    session = load_office_v2_session(
        envelope,
        episode_id=(snapshot.episode_id if snapshot is not None else request.execution_id),
        snapshot=snapshot,
    )
    context = assemble_agent_workspace_context(
        derive_agent_identity_context(
            session.episode.state,
            session.runtime.actor,
            session.runtime.task,
        ),
        derive_agent_policy_capability_context(
            session.episode.state,
            session.runtime.task,
            session.runtime.definitions,
        ),
    )
    interaction = DeterministicInteractionSession(
        episode=session.episode,
        task=session.runtime.task,
        actor_id=session.runtime.actor.actor_id,
        response_directives=tuple(
            ScriptedResponseDirective.model_validate(
                item.model_dump(mode="json", exclude_none=False),
                strict=False,
            )
            for item in envelope.interaction_response_directives
        ),
    )

    def handle_control(name: str, arguments: dict[str, Any]) -> Any:
        if name == REQUEST_CLARIFICATION_TOOL_SPEC.name:
            execution = interaction.handle_request(arguments)
            session.record_trusted_interaction(execution)
            return execution
        if name != SUBMIT_TOOL_SPEC.name:
            raise AdapterExecutionError(
                "agent_unknown_control_tool",
                f"unsupported Agent control tool: {name}",
            )
        try:
            answer = SUBMIT_TOOL_SPEC.validate_arguments(arguments).answer
        except ValidationError as exc:
            raise AdapterExecutionError(
                "agent_invalid_submit",
                f"submit arguments are invalid: {exc.errors()[0]['msg']}",
            ) from exc
        return OfficeV2SubmitExecution(final_answer=answer)

    surface = session.build_agent_surface(
        rendered_prompt=render_office_v2_agent_system_prompt(context),
        control_tool_specs=(REQUEST_CLARIFICATION_TOOL_SPEC, SUBMIT_TOOL_SPEC),
        control_handler=handle_control,
        business_result_observer=interaction.observe_result,
    )
    return session, surface


__all__ = [
    "OfficeV2SubmitExecution",
    "build_office_v2_runtime_surface",
]
