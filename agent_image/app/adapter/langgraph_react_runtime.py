"""Formal LangGraph loop for the self-contained office Agent image."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TypedDict

from pydantic import ValidationError

from app.adapter.base import (
    AdapterConfigurationError,
    AdapterExecutionError,
    AgentAdapter,
    AgentNoSubmitError,
)
from app.agent.react_contract import (
    CONTINUE_PROMPT,
    REQUEST_CLARIFICATION_TOOL_SPEC,
    SUBMIT_TOOL_SPEC,
    ReactMessage,
    ReactToolCall,
    ReactTurn,
)
from app.office_v2_session import (
    OfficeV2ContainerSession,
    OfficeV2LiveOracleArtifact,
    OfficeV2RecordingState,
    OfficeV2SessionSnapshot,
    load_office_v2_session,
)
from app.protocol import ExecutionRequest, TraceEvent
from app.replay.checkpoint import RecordingSession
from app.replay.checkpoint_observer import ReplayCheckpointObserver
from app.replay.state_codec import OfficeV2StateCodec
from app.replay.tool_recorder import ToolReplayer
from app.tools.base import ToolRegistry
from app.tracing.collector import TraceCollector
from sandbox.agent_prompts import (
    OFFICE_AGENT_SYSTEM_PROMPT as SYSTEM_PROMPT,
)
from sandbox.agent_prompts import (
    OFFICE_AGENT_SYSTEM_PROMPT_DIGEST as SYSTEM_PROMPT_DIGEST,
)
from sandbox.agent_prompts import (
    OFFICE_AGENT_SYSTEM_PROMPT_VERSION as SYSTEM_PROMPT_VERSION,
)
from sandbox.agent_prompts import render_office_v2_agent_system_prompt
from sandbox.replay.digests import sha256_digest
from sandbox.replay.exceptions import ReplayDivergenceError
from sandbox.replay.models import CheckpointKind, ResumePhase
from sandbox.scenarios.office_v2.agent_context import (
    assemble_agent_workspace_context,
    derive_agent_identity_context,
    derive_agent_policy_capability_context,
)
from sandbox.scenarios.office_v2.interaction_session import (
    DeterministicInteractionSession,
    ScriptedResponseDirective,
)
from sandbox.tool_contracts import OFFICE_SCENARIO_TOOL_SPECS, ToolSpec


class ModelToolSpec(Protocol):
    name: str
    description: str
    arguments_model: Any


class ModelVisibleToolExecution(Protocol):
    def model_visible_payload(self) -> dict[str, Any]: ...


class ModelVisibleControlExecution(Protocol):
    final_answer: str | None
    follow_up_user_message: str | None

    def model_visible_payload(self) -> dict[str, Any] | None: ...

    def neutral_trace_events(self) -> tuple[Any, ...]: ...


class AgentSessionSurface(Protocol):
    system_message: str
    prompt_version: str
    prompt_digest: str
    business_tool_specs: tuple[ModelToolSpec, ...]
    control_tool_specs: tuple[ModelToolSpec, ...]

    def execute_business_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> ModelVisibleToolExecution: ...

    def handle_control_call(
        self, name: str, arguments: dict[str, Any]
    ) -> ModelVisibleControlExecution: ...


@dataclass(frozen=True, slots=True)
class _V1ModelToolExecution:
    payload: dict[str, Any]

    def model_visible_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True, slots=True)
class _V1ControlExecution:
    final_answer: str
    follow_up_user_message: None = None

    def model_visible_payload(self) -> None:
        return None

    def neutral_trace_events(self) -> tuple[()]:
        return ()


@dataclass(frozen=True, slots=True)
class _V1AgentSessionSurface:
    registry: ToolRegistry
    business_tool_specs: tuple[ModelToolSpec, ...]
    control_tool_specs: tuple[ModelToolSpec, ...]
    system_message: str = SYSTEM_PROMPT
    prompt_version: str = SYSTEM_PROMPT_VERSION
    prompt_digest: str = SYSTEM_PROMPT_DIGEST

    def execute_business_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> _V1ModelToolExecution:
        return _V1ModelToolExecution(
            self.registry.execute({"name": name, "arguments": arguments}).to_dict()
        )

    def handle_control_call(
        self, name: str, arguments: dict[str, Any]
    ) -> _V1ControlExecution:
        if name != SUBMIT_TOOL_SPEC.name:
            raise AdapterExecutionError(
                "langgraph_unknown_control_tool",
                f"unsupported Agent control tool: {name}",
            )
        try:
            return _V1ControlExecution(
                final_answer=SUBMIT_TOOL_SPEC.validate_arguments(arguments).answer
            )
        except ValidationError as exc:
            raise AdapterExecutionError(
                "agent_invalid_submit",
                f"submit arguments are invalid: {exc.errors()[0]['msg']}",
            ) from exc


class _GraphState(TypedDict, total=False):
    messages: list[ReactMessage]
    turn: int
    has_tool_calls: bool
    submitted: bool
    final_answer: str | None


class _LangGraphChatProvider:
    """Translate the stable React contract to LangChain messages at the model boundary."""

    version = "langgraph-chat-ollama-v1"

    def __init__(self, chat_model: Any) -> None:
        self._chat_model = chat_model

    async def generate(
        self,
        messages: tuple[ReactMessage, ...],
        tools: tuple[ModelToolSpec, ...],
        *,
        seed: int | None,
    ) -> ReactTurn:
        del seed
        from langchain_core.messages import (
            AIMessage,
            HumanMessage,
            SystemMessage,
            ToolMessage,
        )

        converted = []
        for message in messages:
            if message.role == "system":
                converted.append(SystemMessage(content=message.content))
            elif message.role == "user":
                converted.append(HumanMessage(content=message.content))
            elif message.role == "assistant":
                converted.append(
                    AIMessage(
                        content=message.content or "",
                        tool_calls=[
                            {
                                "name": call.name,
                                "args": call.arguments,
                                "id": call.call_id,
                                "type": "tool_call",
                            }
                            for call in message.tool_calls
                        ],
                    )
                )
            elif message.role == "tool":
                converted.append(
                    ToolMessage(
                        content=json.dumps(
                            message.content,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        tool_call_id=message.call_id,
                        name=message.name,
                    )
                )
        bound_model = self._chat_model.bind_tools(
            [LangGraphReactRuntime.tool_schema(spec) for spec in tools]
        )
        response = await bound_model.ainvoke(converted)
        if not isinstance(response, AIMessage):
            raise AdapterExecutionError(
                "langgraph_invalid_model_response",
                "the bound chat model did not return an AIMessage",
            )
        return ReactTurn(
            assistant_text=response.content or None,
            tool_calls=[
                ReactToolCall(
                    call_id=raw.get("id"),
                    name=str(raw.get("name", "")),
                    arguments=raw.get("args", {}),
                )
                for raw in response.tool_calls
            ],
            stop_reason=response.response_metadata.get("done_reason"),
        )


class LangGraphReactRuntime(AgentAdapter):
    """Run the formal office Agent through StateGraph and the TRACE 1.2 contract."""

    version = "trace-langgraph-office-g4"

    def __init__(
        self,
        *,
        chat_model: Any | None = None,
        provider_factory: Callable[[ExecutionRequest], Any] | None = None,
        registry_factory: Callable[[], ToolRegistry] = ToolRegistry,
        session_surface: AgentSessionSurface | None = None,
    ) -> None:
        self._chat_model = chat_model
        self._provider_factory = provider_factory
        self._registry_factory = registry_factory
        self._session_surface = session_surface
        self.last_checkpoint_digests = []
        self.last_final_state_digest: str | None = None
        self.last_v2_session: OfficeV2ContainerSession | None = None
        self.last_v2_oracle_artifact: OfficeV2LiveOracleArtifact | None = None
        self._seen_call_ids: set[str] = set()

    async def execute(self, request: ExecutionRequest) -> AsyncIterator[TraceEvent]:
        async for event in self._execute(request):
            yield event

    async def execute_fork(
        self,
        request: ExecutionRequest,
        *,
        initial: dict[str, Any],
        registry: ToolRegistry,
        audit_events: Sequence[dict[str, Any]],
    ) -> AsyncIterator[TraceEvent]:
        async for event in self._execute(
            request,
            initial=initial,
            registry_override=registry,
            force_recording=True,
            recording_audit_events=audit_events,
        ):
            yield event

    async def execute_v2_fork(
        self,
        request: ExecutionRequest,
        *,
        initial: dict[str, Any],
        initial_recording_state: OfficeV2RecordingState,
        audit_events: Sequence[dict[str, Any]],
    ) -> AsyncIterator[TraceEvent]:
        if request.office_v2_execution is None:
            raise AdapterConfigurationError(
                "v2_configuration_error",
                "Office V2 fork requires its frozen execution envelope",
            )
        async for event in self._execute(
            request,
            initial=initial,
            force_recording=True,
            recording_audit_events=audit_events,
            v2_initial_snapshot=initial_recording_state.session,
        ):
            yield event

    async def execute_strict_replay(
        self,
        request: ExecutionRequest,
        *,
        initial: dict[str, Any],
        initial_recording_state: OfficeV2RecordingState,
        expected_recording_state: OfficeV2RecordingState,
        expected_oracle: OfficeV2LiveOracleArtifact,
        provider: Any,
        tool_replayer: ToolReplayer,
    ) -> AsyncIterator[TraceEvent]:
        async for event in self._execute(
            request,
            initial=initial,
            provider_override=provider,
            replay_tools=tool_replayer,
            v2_initial_snapshot=initial_recording_state.session,
        ):
            yield event
        actual_state = self.last_v2_session
        actual_oracle = self.last_v2_oracle_artifact
        if actual_state is None or actual_oracle is None:
            raise ReplayDivergenceError(-32108, "Office V2 replay produced no trusted facts")
        replay_state = actual_state.export_recording_state()
        if replay_state.recording_state_digest != expected_recording_state.recording_state_digest:
            raise ReplayDivergenceError(-32107, "Office V2 trusted recording state diverged")
        if (
            actual_oracle.trusted_facts_digest != expected_oracle.trusted_facts_digest
            or actual_oracle.evidence_bundle.bundle_digest
            != expected_oracle.evidence_bundle.bundle_digest
            or actual_oracle.oracle_result.result_digest
            != expected_oracle.oracle_result.result_digest
        ):
            raise ReplayDivergenceError(-32107, "Office V2 Oracle facts diverged")

    async def _execute(
        self,
        request: ExecutionRequest,
        *,
        initial: dict[str, Any] | None = None,
        registry_override: ToolRegistry | None = None,
        force_recording: bool = False,
        recording_audit_events: Sequence[dict[str, Any]] = (),
        provider_override: Any | None = None,
        replay_tools: ToolReplayer | None = None,
        v2_initial_snapshot: OfficeV2SessionSnapshot | None = None,
    ) -> AsyncIterator[TraceEvent]:
        v2_session: OfficeV2ContainerSession | None = None
        self.last_v2_session = None
        self.last_v2_oracle_artifact = None
        is_v2 = request.office_v2_execution is not None
        replaying_v2 = provider_override is not None or replay_tools is not None
        if (provider_override is None) != (replay_tools is None):
            raise AdapterConfigurationError(
                "v2_configuration_error",
                "Office V2 replay provider and tool verifier must appear together",
            )
        if is_v2:
            if self._session_surface is not None:
                raise AdapterConfigurationError(
                    "v2_configuration_error",
                    "Office V2 requests cannot replace their frozen session surface",
                )
            if registry_override is not None:
                raise AdapterConfigurationError(
                    "v2_configuration_error",
                    "Office V2 cannot restore a legacy ToolRegistry",
                )
            has_checkpoint = initial is not None and v2_initial_snapshot is not None
            if replaying_v2 and not has_checkpoint:
                raise AdapterConfigurationError(
                    "v2_configuration_error",
                    "Office V2 replay requires paired Agent and scenario checkpoint state",
                )
            if not replaying_v2 and (initial is None) != (v2_initial_snapshot is None):
                raise AdapterConfigurationError(
                    "v2_configuration_error",
                    "Office V2 fork requires paired Agent and scenario checkpoint state",
                )
            if has_checkpoint and not replaying_v2 and not force_recording:
                raise AdapterConfigurationError(
                    "v2_configuration_error",
                    "Office V2 live checkpoint continuation must be recorded",
                )
            v2_session, surface = self._v2_session_surface(
                request,
                snapshot=v2_initial_snapshot,
            )
            self.last_v2_session = v2_session
            initialization = None
            base_registry = None
        else:
            if request.scenario_initialization is None:
                raise AdapterConfigurationError(
                    "langgraph_office_initialization_required",
                    "the formal Agent requires a validated office initialization",
                )
            base_registry = registry_override or self._registry_factory()
            initialization = base_registry.enable_office_episode(
                request.scenario_initialization
            )
            base_registry.office.validate_request(request)
            surface = self._session_surface or self.v1_session_surface(base_registry)
            if self._session_surface is not None and (
                force_recording
                or (request.recording is not None and request.recording.enabled)
            ):
                raise AdapterConfigurationError(
                    "langgraph_injected_surface_recording_unsupported",
                    "an injected Agent session surface cannot use the V1 recording route",
                )
        specs = (*surface.business_tool_specs, *surface.control_tool_specs)
        if provider_override is not None:
            base_provider = provider_override
        elif self._provider_factory is not None:
            base_provider = self._provider_factory(request)
        else:
            base_provider = _LangGraphChatProvider(
                self._chat_model or self._create_chat_model(request)
            )
        recording = None
        if force_recording or (
            request.recording is not None and request.recording.enabled
        ):
            recording_registry = base_registry or ToolRegistry()
            recording = RecordingSession(
                request,
                base_provider,
                recording_registry,
                runtime_id=(
                    "trace-react-v2-office-v2" if is_v2 else "trace-react-v2"
                ),
                system_prompt_version=surface.prompt_version,
                system_prompt_digest=surface.prompt_digest,
                state_codec=(
                    OfficeV2StateCodec(v2_session.export_recording_state)
                    if v2_session is not None
                    else None
                ),
            )
            recording.audit_events.extend(recording_audit_events)
            provider = recording.model
            registry = recording.tools
        else:
            provider = base_provider
            registry = base_registry
        if not is_v2 and self._session_surface is None:
            assert registry is not None
            surface = self.v1_session_surface(registry)
            specs = (*surface.business_tool_specs, *surface.control_tool_specs)
        office = registry.office if registry is not None else None
        observer = (
            ReplayCheckpointObserver(
                base_provider,
                replay_tools,
                runtime_id="trace-react-v2-office-v2",
                state_codec=OfficeV2StateCodec(v2_session.export_recording_state),
            )
            if replaying_v2 and v2_session is not None
            else None
        )

        def current_state_digest() -> str:
            if v2_session is not None:
                return v2_session.episode.state_digest
            assert registry is not None
            return registry.state_digest()

        collector = TraceCollector(request.execution_id, schema_version="1.2")
        events: list[TraceEvent] = []
        event_queue: asyncio.Queue[TraceEvent | object] = asyncio.Queue()
        completed = object()
        self._seen_call_ids = set()
        self.last_final_state_digest = current_state_digest()

        if initial is None:
            messages = self._initial_messages(request, surface)
            initial_turn = 0
        else:
            try:
                messages = [
                    ReactMessage.model_validate(message)
                    for message in initial.get("messages", [])
                ]
            except ValidationError as exc:
                raise AdapterExecutionError(
                    "trace_checkpoint_state_invalid",
                    "checkpoint messages are invalid",
                ) from exc
            if not messages or bool(initial.get("submitted")):
                raise AdapterExecutionError(
                    "trace_checkpoint_state_invalid",
                    "cannot resume an empty or already submitted checkpoint",
                )
            initial_turn = int(initial.get("turn", initial.get("step_count", 0)))
            self._seen_call_ids = {
                str(call_id) for call_id in initial.get("seen_call_ids", [])
            }

        def state_snapshot(
            current_messages: list[ReactMessage],
            *,
            turn: int,
            final_answer: str | None,
        ) -> dict[str, Any]:
            return {
                "prompt": request.prompt,
                "max_steps": request.max_steps,
                "turn": turn,
                "step_count": turn,
                "messages": [message.model_dump(mode="json") for message in current_messages],
                "seen_call_ids": sorted(self._seen_call_ids),
                "submitted": final_answer is not None,
                "final_answer": final_answer,
            }

        def emit(event_type: str, source: str, data=None, **fields) -> TraceEvent:
            event = collector.emit(event_type, source, data, **fields)
            events.append(event)
            event_queue.put_nowait(event)
            return event

        initial_state = state_snapshot(messages, turn=initial_turn, final_answer=None)
        if recording:
            recording.start(initial_state)
        if observer:
            observer.capture(
                initial_state,
                kind=CheckpointKind.NODE_COMMIT,
                resume_phase=ResumePhase.ENTER_NEXT_NODE,
            )

        emit(
            "execution_started",
            "runtime",
            {
                **request.metadata,
                "case_id": request.case_id,
                "scenario_id": request.scenario_id,
                "execution_backend": "trace_react_v2",
            },
        )
        if v2_session is not None:
            emit(
                "scenario_initialized",
                "trace.office.v2",
                {
                    "scenario_id": request.scenario_id,
                    "case_id": request.case_id,
                    "execution_envelope_digest": (
                        v2_session.envelope.canonical_digest()
                    ),
                    "office_state_digest": v2_session.envelope.initial_state_digest,
                    "scenario_case_kind": (
                        v2_session.envelope.scenario_case_kind.value
                    ),
                    "prompt_version": surface.prompt_version,
                    "prompt_digest": surface.prompt_digest,
                },
                state_digest=current_state_digest(),
            )
        else:
            assert initialization is not None
            case = initialization.test_case
            attack = case.attack
            emit(
                "scenario_initialized",
                "trace.office",
                {
                    "scenario_id": case.scenario.template_id,
                    "case_id": case.case_id,
                    "initialization_digest": initialization.envelope_digest,
                    "office_state_digest": initialization.initial_state_digest,
                    "attack_location": (
                        attack.carrier.target.model_dump(mode="json")
                        if attack is not None
                        else None
                    ),
                },
                state_digest=current_state_digest(),
            )

        from langgraph.graph import END, START, StateGraph

        async def model_node(state: _GraphState) -> dict[str, Any]:
            turn = int(state.get("turn", 0)) + 1
            if turn > request.max_steps:
                raise AgentNoSubmitError(limit_type="turn")
            current_messages = list(state["messages"])
            snapshot = state_snapshot(current_messages, turn=turn - 1, final_answer=None)
            before_model = recording.before_model(snapshot, turn) if recording else None
            replay_before_model_id = (
                base_provider.next_before_checkpoint_id if observer else None
            )
            if observer:
                observer.capture(
                    snapshot,
                    kind=CheckpointKind.BEFORE_MODEL,
                    resume_phase=ResumePhase.CALL_MODEL,
                )
            input_payload = [message.model_dump(mode="json") for message in current_messages]
            model_start = {
                "turn": turn,
                "input_message_count": len(current_messages),
                "available_tools": [spec.name for spec in specs],
            }
            prior_tool = next(
                (message for message in reversed(current_messages) if message.role == "tool"),
                None,
            )
            if prior_tool is not None:
                model_start.update(
                    {
                        "prior_tool": prior_tool.name,
                        "prior_tool_call_id": prior_tool.call_id,
                        "prior_tool_result_digest": sha256_digest(prior_tool.content),
                    }
                )
            emit(
                "model_start",
                provider.version,
                model_start,
                logical_time=turn,
                input_digest=sha256_digest(input_payload),
                checkpoint_id=(
                    before_model.checkpoint_id
                    if before_model
                    else replay_before_model_id
                ),
            )
            decision = await provider.generate(
                tuple(current_messages), specs, seed=request.seed
            )
            calls = self._assign_call_ids(decision.tool_calls, turn)
            self._validate_call_batch(
                calls,
                control_tool_names={spec.name for spec in surface.control_tool_specs},
            )
            current_messages.append(
                ReactMessage(
                    role="assistant",
                    content=decision.assistant_text,
                    tool_calls=calls,
                )
            )
            snapshot = state_snapshot(current_messages, turn=turn, final_answer=None)
            after_model = recording.after_model(snapshot, turn) if recording else None
            if observer:
                observer.capture(
                    snapshot,
                    kind=CheckpointKind.AFTER_MODEL,
                    resume_phase=ResumePhase.APPLY_MODEL_DECISION,
                )
            decision_payload = {
                "assistant_text": decision.assistant_text,
                "stop_reason": decision.stop_reason,
                "tool_calls": [call.model_dump(mode="json") for call in calls],
            }
            emit(
                "model_end",
                provider.version,
                {"turn": turn, "decision": decision_payload},
                logical_time=turn,
                input_digest=sha256_digest(input_payload),
                output_digest=sha256_digest(decision_payload),
                checkpoint_id=(
                    after_model.checkpoint_id
                    if after_model
                    else (
                        base_provider.last_decision.after_checkpoint_id
                        if observer and base_provider.last_decision is not None
                        else None
                    )
                ),
            )
            if not calls:
                current_messages.append(ReactMessage(role="user", content=CONTINUE_PROMPT))
            return {
                "messages": current_messages,
                "turn": turn,
                "has_tool_calls": bool(calls),
            }

        async def tool_node(state: _GraphState) -> dict[str, Any]:
            current_messages = list(state["messages"])
            message = next(
                (
                    candidate
                    for candidate in reversed(current_messages)
                    if candidate.role == "assistant"
                ),
                None,
            )
            if message is None or not message.tool_calls:
                raise AdapterExecutionError(
                    "langgraph_missing_tool_decision",
                    "tool node was entered without an Agent tool decision",
                )
            final_answer = None
            turn = int(state["turn"])
            for index, call in enumerate(message.tool_calls):
                if call.name in {spec.name for spec in surface.control_tool_specs}:
                    interaction_call = (
                        call.name == REQUEST_CLARIFICATION_TOOL_SPEC.name
                    )
                    before_state_digest = current_state_digest()
                    before_control = None
                    replay_before_control_id = (
                        replay_tools.next_before_checkpoint_id
                        if observer and interaction_call
                        else None
                    )
                    if recording and interaction_call:
                        snapshot = state_snapshot(
                            current_messages, turn=turn, final_answer=None
                        )
                        before_control = recording.before_tool(snapshot, turn)
                        emit(
                            "tool_call",
                            "agent_control",
                            {
                                "call_id": call.call_id,
                                "call_index": index,
                                "name": call.name,
                                "arguments": call.arguments,
                            },
                            logical_time=turn,
                            input_digest=sha256_digest(call.arguments),
                            checkpoint_id=before_control.checkpoint_id,
                        )
                    if observer and interaction_call:
                        snapshot = state_snapshot(
                            current_messages, turn=turn, final_answer=None
                        )
                        observer.capture(
                            snapshot,
                            kind=CheckpointKind.BEFORE_TOOL,
                            resume_phase=ResumePhase.CALL_TOOL,
                        )
                    execution = surface.handle_control_call(
                        call.name, call.arguments
                    )
                    if execution.final_answer is not None:
                        final_answer = execution.final_answer
                        emit(
                            "agent_submit",
                            "trace.react",
                            {
                                "call_id": call.call_id,
                                "call_index": index,
                                "accepted": True,
                                "answer_digest": sha256_digest(final_answer),
                            },
                            logical_time=turn,
                        )
                        continue
                    result = execution.model_visible_payload()
                    if result is None:
                        raise AdapterExecutionError(
                            "langgraph_invalid_control_result",
                            "a non-terminal control call requires a visible result",
                        )
                    if not recording:
                        emit(
                            "tool_call",
                            "agent_control",
                            {
                                "call_id": call.call_id,
                                "call_index": index,
                                "name": call.name,
                                "arguments": call.arguments,
                            },
                            logical_time=turn,
                            input_digest=sha256_digest(call.arguments),
                            checkpoint_id=replay_before_control_id,
                        )
                    for trace_fact in execution.neutral_trace_events():
                        emit(
                            trace_fact.event_type,
                            "trace.office.interaction",
                            trace_fact.data,
                            logical_time=trace_fact.logical_time,
                            input_digest=trace_fact.input_digest,
                            output_digest=trace_fact.output_digest,
                            state_digest=trace_fact.state_digest,
                        )
                    current_messages.append(
                        ReactMessage(
                            role="tool",
                            call_id=call.call_id,
                            name=call.name,
                            content=result,
                        )
                    )
                    if execution.follow_up_user_message is not None:
                        current_messages.append(
                            ReactMessage(
                                role="user",
                                content=execution.follow_up_user_message,
                            )
                        )
                    after_control = None
                    if recording:
                        after_state_digest = current_state_digest()
                        recording.record_external_tool(
                            name=call.name,
                            arguments=call.arguments,
                            result=result,
                            side_effect_digest_before=before_state_digest,
                            side_effect_digest_after=after_state_digest,
                            policy_decision=(
                                "allowed"
                                if getattr(execution, "outcome", None) is not None
                                and execution.outcome.failure_code is None
                                else "blocked"
                            ),
                        )
                        snapshot = state_snapshot(
                            current_messages, turn=turn, final_answer=None
                        )
                        after_control = recording.after_tool(snapshot, turn)
                    if observer:
                        policy_decision = (
                            "allowed"
                            if getattr(execution, "outcome", None) is not None
                            and execution.outcome.failure_code is None
                            else "blocked"
                        )
                        assert replay_tools is not None
                        replay_tools.verify_external(
                            {"name": call.name, "arguments": call.arguments},
                            result,
                            side_effect_digest_before=before_state_digest,
                            side_effect_digest_after=current_state_digest(),
                            policy_decision=policy_decision,
                        )
                        snapshot = state_snapshot(
                            current_messages, turn=turn, final_answer=None
                        )
                        observer.capture(
                            snapshot,
                            kind=CheckpointKind.AFTER_TOOL,
                            resume_phase=ResumePhase.APPLY_TOOL_RESULT,
                        )
                    emit(
                        "tool_result",
                        "agent_control",
                        {
                            **result,
                            "call_id": call.call_id,
                            "call_index": index,
                            "name": call.name,
                        },
                        logical_time=turn,
                        output_digest=sha256_digest(result),
                        checkpoint_id=(
                            after_control.checkpoint_id
                            if after_control
                            else (
                                replay_tools.last_record.after_checkpoint_id
                                if observer and replay_tools.last_record is not None
                                else None
                            )
                        ),
                    )
                    continue
                snapshot = state_snapshot(
                    current_messages, turn=turn, final_answer=None
                )
                before_state_digest = current_state_digest()
                before_tool = recording.before_tool(snapshot, turn) if recording else None
                replay_before_tool_id = (
                    replay_tools.next_before_checkpoint_id if observer else None
                )
                if observer:
                    observer.capture(
                        snapshot,
                        kind=CheckpointKind.BEFORE_TOOL,
                        resume_phase=ResumePhase.CALL_TOOL,
                    )
                emit(
                    "tool_call",
                    "controlled_tools",
                    {
                        "call_id": call.call_id,
                        "call_index": index,
                        "name": call.name,
                        "arguments": call.arguments,
                    },
                    logical_time=turn,
                    input_digest=sha256_digest(call.arguments),
                    checkpoint_id=(
                        before_tool.checkpoint_id
                        if before_tool
                        else replay_before_tool_id
                    ),
                )
                result = surface.execute_business_tool(
                    call.name, call.arguments
                ).model_visible_payload()
                self.last_final_state_digest = current_state_digest()
                current_messages.append(
                    ReactMessage(
                        role="tool",
                        call_id=call.call_id,
                        name=call.name,
                        content=result,
                    )
                )
                snapshot = state_snapshot(
                    current_messages, turn=turn, final_answer=None
                )
                if recording and v2_session is not None:
                    trusted_result = v2_session.trusted_tool_results[-1]
                    decision = trusted_result.policy_decision
                    recording.record_external_tool(
                        name=call.name,
                        arguments=call.arguments,
                        result=result,
                        side_effect_digest_before=before_state_digest,
                        side_effect_digest_after=current_state_digest(),
                        policy_decision=(
                            "allowed"
                            if decision is None or decision.effective_allowed
                            else "blocked"
                        ),
                    )
                if observer and v2_session is not None:
                    trusted_result = v2_session.trusted_tool_results[-1]
                    decision = trusted_result.policy_decision
                    assert replay_tools is not None
                    replay_tools.verify_external(
                        {"name": call.name, "arguments": call.arguments},
                        result,
                        side_effect_digest_before=before_state_digest,
                        side_effect_digest_after=current_state_digest(),
                        policy_decision=(
                            "allowed"
                            if decision is None or decision.effective_allowed
                            else "blocked"
                        ),
                    )
                after_tool = recording.after_tool(snapshot, turn) if recording else None
                if observer:
                    observer.capture(
                        snapshot,
                        kind=CheckpointKind.AFTER_TOOL,
                        resume_phase=ResumePhase.APPLY_TOOL_RESULT,
                    )
                if result.get("risk_category"):
                    emit(
                        "security_violation",
                        "tool_policy",
                        {
                            "call_id": call.call_id,
                            "call_index": index,
                            "tool": call.name,
                            "risk_category": result["risk_category"],
                            "reason": result.get("error"),
                            "allowed": result.get("allowed"),
                            "outcome": result.get("outcome"),
                        },
                        logical_time=turn,
                    )
                emit(
                    "tool_result",
                    "controlled_tools",
                    {**result, "call_id": call.call_id, "call_index": index, "name": call.name},
                    logical_time=turn,
                    output_digest=sha256_digest(result),
                    checkpoint_id=(
                        after_tool.checkpoint_id
                        if after_tool
                        else (
                            replay_tools.last_record.after_checkpoint_id
                            if observer and replay_tools.last_record is not None
                            else None
                        )
                    ),
                )
            return {
                "messages": current_messages,
                "submitted": final_answer is not None,
                "final_answer": final_answer,
            }

        def after_model(state: _GraphState) -> str:
            return "tools" if state.get("has_tool_calls") else "model"

        def after_tools(state: _GraphState) -> str:
            return "finish" if state.get("submitted") else "model"

        def finish_node(state: _GraphState) -> dict[str, Any]:
            final_answer = state.get("final_answer")
            if not final_answer:
                raise AgentNoSubmitError(limit_type="turn")
            current_messages = list(state["messages"])
            final_state = state_snapshot(
                current_messages,
                turn=int(state["turn"]),
                final_answer=final_answer,
            )
            self.last_final_state_digest = current_state_digest()
            if v2_session is not None:
                observation = {
                    "transaction_count": len(v2_session.episode.history),
                    "tool_invocation_count": len(v2_session.runtime.invocations),
                }
                emit(
                    "scenario_state_observed",
                    "trace.office.v2",
                    observation,
                    state_digest=self.last_final_state_digest,
                )
            else:
                assert office is not None
                observation = office.observe()
                emit(
                    "scenario_state_observed",
                    "trace.office",
                    observation,
                    state_digest=self.last_final_state_digest,
                )
            finished_data = {
                "final_answer": final_answer,
                "restricted_data_exposed": False,
            }
            if v2_session is not None:
                finished_data["scenario_case_kind"] = (
                    v2_session.envelope.scenario_case_kind.value
                )
            else:
                finished_data["attack_side_effect_observed"] = bool(
                    observation["attack_side_effect_observed"]
                )
            emit(
                "execution_finished",
                "runtime",
                finished_data,
                state_digest=self.last_final_state_digest,
            )
            if v2_session is not None:
                self.last_v2_oracle_artifact = v2_session.build_live_oracle_artifact(
                    trace_events=tuple(events),
                    final_answer=final_answer,
                )
            if observer:
                observer.capture(
                    final_state,
                    kind=CheckpointKind.NODE_COMMIT,
                    resume_phase=ResumePhase.ENTER_NEXT_NODE,
                )
                base_provider.assert_consumed()
                assert replay_tools is not None
                replay_tools.assert_consumed()
                self.last_checkpoint_digests = list(observer.records)
                self.last_final_state_digest = observer.final_state_digest
            if recording:
                extra_artifacts = None
                if v2_session is not None:
                    assert self.last_v2_oracle_artifact is not None
                    extra_artifacts = {
                        "office-v2-recording-state.json": (
                            v2_session.export_recording_state()
                        ),
                        "office-v2-oracle.json": self.last_v2_oracle_artifact,
                    }
                recording.finalize(
                    final_state,
                    events,
                    extra_artifacts=extra_artifacts,
                )
            return {}

        graph = StateGraph(_GraphState)
        graph.add_node("model", model_node)
        graph.add_node("tools", tool_node)
        graph.add_node("finish", finish_node)
        graph.add_edge(START, "model")
        graph.add_conditional_edges("model", after_model, {"model": "model", "tools": "tools"})
        graph.add_conditional_edges("tools", after_tools, {"model": "model", "finish": "finish"})
        graph.add_edge("finish", END)
        compiled = graph.compile()

        async def run_graph() -> None:
            try:
                await compiled.ainvoke(
                    {
                        "messages": messages,
                        "turn": initial_turn,
                        "has_tool_calls": False,
                        "submitted": False,
                        "final_answer": None,
                    },
                    config={"recursion_limit": request.max_steps * 3 + 5},
                )
            except BaseException as exc:
                if recording:
                    recording.finalize_incomplete(events, reason=type(exc).__name__)
                raise
            finally:
                event_queue.put_nowait(completed)

        graph_task = asyncio.create_task(run_graph(), name=f"langgraph-{request.execution_id}")
        try:
            while True:
                event = await event_queue.get()
                if event is completed:
                    break
                assert isinstance(event, TraceEvent)
                yield event
            await graph_task
        finally:
            if not graph_task.done():
                graph_task.cancel()
            await asyncio.gather(graph_task, return_exceptions=True)

    @staticmethod
    def _v2_session_surface(
        request: ExecutionRequest,
        *,
        snapshot: OfficeV2SessionSnapshot | None = None,
    ) -> tuple[OfficeV2ContainerSession, AgentSessionSurface]:
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
                    "langgraph_unknown_control_tool",
                    f"unsupported Agent control tool: {name}",
                )
            try:
                answer = SUBMIT_TOOL_SPEC.validate_arguments(arguments).answer
            except ValidationError as exc:
                raise AdapterExecutionError(
                    "agent_invalid_submit",
                    f"submit arguments are invalid: {exc.errors()[0]['msg']}",
                ) from exc
            return _V1ControlExecution(final_answer=answer)

        surface = session.build_agent_surface(
            rendered_prompt=render_office_v2_agent_system_prompt(context),
            control_tool_specs=(REQUEST_CLARIFICATION_TOOL_SPEC, SUBMIT_TOOL_SPEC),
            control_handler=handle_control,
            business_result_observer=interaction.observe_result,
        )
        return session, surface

    @staticmethod
    def select_specs(specs: Sequence[ToolSpec]) -> tuple[ToolSpec, ...]:
        by_name = {spec.name: spec for spec in specs}
        expected_names = tuple(spec.name for spec in OFFICE_SCENARIO_TOOL_SPECS)
        if len(by_name) != len(specs) or set(by_name) != set(expected_names):
            raise AdapterConfigurationError(
                "langgraph_office_tools_incomplete",
                "the formal office Agent requires the exact 13-tool office contract",
            )
        return (*(by_name[name] for name in expected_names), SUBMIT_TOOL_SPEC)

    @classmethod
    def v1_session_surface(cls, registry: ToolRegistry) -> AgentSessionSurface:
        specs = cls.select_specs(registry.specs)
        return _V1AgentSessionSurface(
            registry=registry,
            business_tool_specs=specs[:-1],
            control_tool_specs=(specs[-1],),
        )

    @staticmethod
    def tool_schema(spec: ModelToolSpec) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.arguments_model.model_json_schema(),
            },
        }

    def _assign_call_ids(
        self,
        calls: list[ReactToolCall],
        turn: int,
    ) -> list[ReactToolCall]:
        assigned = []
        for index, call in enumerate(calls):
            generated = sha256_digest(
                {
                    "turn": turn,
                    "call_index": index,
                    "name": call.name,
                    "arguments": call.arguments,
                }
            )[7:19]
            call_id = call.call_id or f"call-{turn}-{index}-{generated}"
            if call_id in self._seen_call_ids:
                raise AdapterExecutionError(
                    "trace_duplicate_tool_call_id",
                    f"model reused tool call id: {call_id}",
                )
            self._seen_call_ids.add(call_id)
            assigned.append(call.model_copy(update={"call_id": call_id}))
        return assigned

    @staticmethod
    def _validate_call_batch(
        calls: Sequence[ReactToolCall],
        *,
        control_tool_names: set[str] | None = None,
    ) -> None:
        submit_count = sum(call.name == SUBMIT_TOOL_SPEC.name for call in calls)
        if submit_count > 1:
            raise AdapterExecutionError(
                "trace_duplicate_submit",
                "a model turn cannot contain more than one submit call",
            )
        if submit_count == 1 and len(calls) != 1:
            raise AdapterExecutionError(
                "trace_mixed_submit_batch",
                "submit must be the only tool call in its model turn",
            )
        names = control_tool_names or {SUBMIT_TOOL_SPEC.name}
        non_submit_controls = [
            call for call in calls if call.name in names and call.name != SUBMIT_TOOL_SPEC.name
        ]
        if len(non_submit_controls) > 1:
            raise AdapterExecutionError(
                "trace_duplicate_control_call",
                "a model turn cannot contain more than one non-submit control call",
            )
        if non_submit_controls and len(calls) != 1:
            raise AdapterExecutionError(
                "trace_mixed_control_batch",
                "an Agent control call must be the only tool call in its model turn",
            )

    @staticmethod
    def _initial_messages(
        request: ExecutionRequest,
        surface: AgentSessionSurface,
    ) -> list[ReactMessage]:
        return [
            ReactMessage(role="system", content=surface.system_message),
            ReactMessage(role="user", content=request.prompt),
        ]

    @staticmethod
    def _create_chat_model(request: ExecutionRequest) -> Any:
        if request.model is None or request.model.endpoint is None:
            raise AdapterConfigurationError(
                "langgraph_model_configuration_missing",
                "formal LangGraph execution requires locked Ollama model options",
            )
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=request.model.model_name,
            base_url=request.model.endpoint,
            temperature=0,
            seed=request.seed,
            reasoning=False,
            client_kwargs={"timeout": request.model.timeout_seconds},
        )
