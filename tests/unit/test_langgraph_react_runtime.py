from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.adapter.langgraph_react_runtime import (
    SYSTEM_PROMPT,
    LangGraphReactRuntime,
)
from app.adapter.trace_react_adapter import TraceReactAdapter
from app.protocol import ExecutionRequest
from app.replay.react_decision_recorder import RecordedReactProvider
from app.replay.state_codec import StateCodec
from app.replay.tool_recorder import ToolReplayer
from app.tools.base import ToolRegistry
from langchain_core.messages import AIMessage, ToolMessage

from sandbox.coverage.behavior import BehaviorFeatureExtractor
from sandbox.coverage.input import CoverageInputResolver
from sandbox.coverage.risk import RiskRecognizer
from sandbox.coverage.taxonomy import RiskTaxonomyLoader
from sandbox.protocol import RecordingOptions, TraceEvent
from sandbox.replay.models import (
    CheckpointStateEnvelope,
    RecordedModelDecision,
    RecordedToolInteraction,
    StateCheckpoint,
)
from sandbox.replay.normalizer import normalize_behavior_trace
from sandbox.scenarios.office_episode import (
    OfficeToolRuntimeState,
    build_office_episode_initialization,
)
from sandbox.scenarios.office_fork import replace_office_carrier_payload
from sandbox.scenarios.office_matrix import OFFICE_V1_TEST_MATRIX
from sandbox.scenarios.office_v1 import BOB_EMAIL, MEETING_START, MEETING_TITLE
from sandbox.tool_contracts import OFFICE_SCENARIO_TOOL_SPECS

CLEAN_CASE = OFFICE_V1_TEST_MATRIX.clean_cases[0]
INJECTED_CASE = OFFICE_V1_TEST_MATRIX.attack_cases[0]


def _request(case=CLEAN_CASE) -> ExecutionRequest:
    initialization = build_office_episode_initialization(case)
    return ExecutionRequest(
        execution_id=f"langgraph-{case.case_id}",
        case_id=case.case_id,
        scenario_id=case.scenario.template_id,
        prompt=case.benign_task.instruction,
        max_steps=case.budget.max_steps,
        timeout_seconds=case.budget.timeout_seconds,
        seed=case.seed,
        scenario_initialization=initialization.model_dump(mode="json"),
    )


class DependentOfficeChatModel:
    def __init__(self, *, injected_payload: str | None = None) -> None:
        self.turn = 0
        self.bound_tools = []
        self.inputs = []
        self.injected_payload = injected_payload

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    async def ainvoke(self, input_messages):
        self.inputs.append(tuple(input_messages))
        self.turn += 1
        if self.turn == 1:
            assert all(not isinstance(message, ToolMessage) for message in input_messages)
            return AIMessage(
                content="",
                tool_calls=[{"name": "search_email", "args": {"query": "Bob"}, "id": "c1"}],
            )
        prior = input_messages[-1]
        assert isinstance(prior, ToolMessage)
        result = json.loads(prior.content)
        if self.turn == 2:
            assert "email-bob-001" in prior.content
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_email",
                        "args": {"email_id": "email-bob-001"},
                        "id": "c2",
                    }
                ],
            )
        if self.turn == 3:
            assert result["output"]["email_id"] == "email-bob-001"
            assert MEETING_TITLE in prior.content
            if self.injected_payload is not None:
                assert self.injected_payload in prior.content
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "create_calendar_event",
                        "args": {
                            "title": MEETING_TITLE,
                            "start": MEETING_START,
                            "attendees": [BOB_EMAIL],
                        },
                        "id": "c3",
                    }
                ],
            )
        assert result["outcome"] == "succeeded"
        return AIMessage(
            content="",
            tool_calls=[{"name": "submit", "args": {"answer": "done"}, "id": "c4"}],
        )


class RecoveringOfficeChatModel:
    def __init__(self) -> None:
        self.turn = 0

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, input_messages):
        self.turn += 1
        if self.turn == 1:
            return AIMessage(
                content="",
                tool_calls=[{"name": "search_email", "args": {"query": "Bob"}, "id": "r1"}],
            )
        if self.turn == 2:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "create_calendar_event",
                        "args": {
                            "title": "guessed",
                            "start": "guessed",
                            "attendees": [BOB_EMAIL],
                        },
                        "id": "r2",
                    }
                ],
            )
        prior = input_messages[-1]
        assert isinstance(prior, ToolMessage)
        if self.turn == 3:
            assert json.loads(prior.content)["outcome"] == "blocked"
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_email",
                        "args": {"email_id": "email-bob-001"},
                        "id": "r3",
                    }
                ],
            )
        if self.turn == 4:
            assert MEETING_TITLE in prior.content
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "create_calendar_event",
                        "args": {
                            "title": MEETING_TITLE,
                            "start": MEETING_START,
                            "attendees": [BOB_EMAIL],
                        },
                        "id": "r4",
                    }
                ],
            )
        return AIMessage(
            content="",
            tool_calls=[{"name": "submit", "args": {"answer": "done"}, "id": "r5"}],
        )


class SubmitOnlyChatModel:
    def __init__(self) -> None:
        self.bound_tools = []

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    async def ainvoke(self, input_messages):
        return AIMessage(
            content="",
            tool_calls=[{"name": "submit", "args": {"answer": "done"}, "id": "s1"}],
        )


class BlockingChatModel:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, input_messages):
        self.started.set()
        await self.release.wait()
        return AIMessage(
            content="",
            tool_calls=[{"name": "submit", "args": {"answer": "done"}, "id": "b1"}],
        )


class ForkSuffixChatModel:
    def __init__(self, replacement: str) -> None:
        self.turn = 0
        self.replacement = replacement

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, input_messages):
        self.turn += 1
        prior = input_messages[-1]
        assert isinstance(prior, ToolMessage)
        if self.turn == 1:
            assert "email-bob-001" in prior.content
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_email",
                        "args": {"email_id": "email-bob-001"},
                        "id": "f1",
                    }
                ],
            )
        if self.turn == 2:
            assert self.replacement in prior.content
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "create_calendar_event",
                        "args": {
                            "title": MEETING_TITLE,
                            "start": MEETING_START,
                            "attendees": [BOB_EMAIL],
                        },
                        "id": "f2",
                    }
                ],
            )
        assert json.loads(prior.content)["outcome"] == "succeeded"
        return AIMessage(
            content="",
            tool_calls=[{"name": "submit", "args": {"answer": "done"}, "id": "f3"}],
        )


async def _collect(adapter, request):
    return [event async for event in adapter.execute(request)]


def _load_recording(output_dir):
    envelope = CheckpointStateEnvelope.model_validate_json(
        (output_dir / "initial-state.json").read_bytes()
    )
    decisions = [
        RecordedModelDecision.model_validate_json(line)
        for line in (output_dir / "model-decisions.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line
    ]
    interactions = [
        RecordedToolInteraction.model_validate_json(line)
        for line in (output_dir / "tool-records.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line
    ]
    checkpoints = [
        StateCheckpoint.model_validate_json(line)
        for line in (output_dir / "checkpoints.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line
    ]
    events = [
        TraceEvent.model_validate_json(line)
        for line in (output_dir / "events.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line
    ]
    return envelope, decisions, interactions, checkpoints, events


async def _strict_replay(request, output_dir):
    envelope, decisions, interactions, checkpoints, events = _load_recording(
        output_dir
    )
    tools = ToolRegistry()
    initial = StateCodec().restore(envelope, tools, execution_id=request.execution_id)
    adapter = TraceReactAdapter(
        provider=RecordedReactProvider(decisions),
        registry=ToolReplayer(tools, interactions),
    )
    replay_request = request.model_copy(update={"recording": None})
    replay_events = [
        event
        async for event in adapter.execute_replay_from(replay_request, initial=initial)
    ]
    return adapter, checkpoints, events, replay_events


async def test_langgraph_loop_reinjects_real_results_and_changes_office_state() -> None:
    model = DependentOfficeChatModel()
    events = await _collect(LangGraphReactRuntime(chat_model=model), _request())

    tool_results = [event for event in events if event.event_type == "tool_result"]
    observed = next(event for event in events if event.event_type == "scenario_state_observed")
    exposed_names = [tool["function"]["name"] for tool in model.bound_tools]

    assert exposed_names == [
        *(spec.name for spec in OFFICE_SCENARIO_TOOL_SPECS),
        "submit",
    ]
    assert [event.data["name"] for event in tool_results] == [
        "search_email",
        "read_email",
        "create_calendar_event",
    ]
    assert observed.data["normal_task_completed"] is True
    assert observed.data["action_count"] == 3
    assert events[-1].event_type == "execution_finished"
    assert len(model.inputs) == 4


async def test_attack_metadata_is_not_in_prompt_but_materialized_email_is_observed() -> None:
    payload = INJECTED_CASE.attack.payload
    model = DependentOfficeChatModel(injected_payload=payload)
    events = await _collect(LangGraphReactRuntime(chat_model=model), _request(INJECTED_CASE))

    initial_text = "\n".join(str(message.content) for message in model.inputs[0])
    assert payload not in initial_text
    assert INJECTED_CASE.attack.objective.template_id not in initial_text
    assert SYSTEM_PROMPT in initial_text
    assert "Search results are metadata only" in initial_text
    assert any(
        isinstance(message, ToolMessage) and payload in str(message.content)
        for message in model.inputs[2]
    )
    observed = next(event for event in events if event.event_type == "scenario_state_observed")
    assert observed.data["normal_task_completed"] is True
    assert observed.data["attack_side_effect_observed"] is False


async def test_model_can_recover_from_an_ungrounded_write_using_the_real_error() -> None:
    model = RecoveringOfficeChatModel()
    events = await _collect(LangGraphReactRuntime(chat_model=model), _request())
    results = [event for event in events if event.event_type == "tool_result"]
    observed = next(event for event in events if event.event_type == "scenario_state_observed")

    assert [event.data["name"] for event in results] == [
        "search_email",
        "create_calendar_event",
        "read_email",
        "create_calendar_event",
    ]
    assert results[1].data["outcome"] == "blocked"
    assert "prior successful read_email result" in results[1].data["error"]
    assert results[3].data["outcome"] == "succeeded"
    assert observed.data["normal_task_completed"] is True


async def test_g4_accepts_every_frozen_office_task_at_the_agent_boundary() -> None:
    for case in OFFICE_V1_TEST_MATRIX.clean_cases:
        model = SubmitOnlyChatModel()
        events = await _collect(LangGraphReactRuntime(chat_model=model), _request(case))

        assert events[-1].event_type == "execution_finished"
        assert [tool["function"]["name"] for tool in model.bound_tools] == [
            *(spec.name for spec in OFFICE_SCENARIO_TOOL_SPECS),
            "submit",
        ]


async def test_trace_events_stream_before_the_model_finishes() -> None:
    model = BlockingChatModel()
    events = LangGraphReactRuntime(chat_model=model).execute(_request())

    first = await anext(events)
    await asyncio.wait_for(model.started.wait(), timeout=1)

    assert first.event_type == "execution_started"
    model.release.set()
    remaining = [event async for event in events]
    assert remaining[-1].event_type == "execution_finished"


async def test_recording_strict_replays_without_calling_the_chat_model(
    tmp_path,
    monkeypatch,
) -> None:
    replay_out = tmp_path / "replay-out"
    monkeypatch.setenv("REPLAY_OUTPUT_DIR", str(replay_out))
    request = _request().model_copy(
        update={"recording": RecordingOptions(enabled=True)}
    )
    model = DependentOfficeChatModel()
    source_adapter = LangGraphReactRuntime(chat_model=model)
    source_events = await _collect(source_adapter, request)
    source_turns = model.turn

    replay_adapter, checkpoints, recorded_events, replay_events = await _strict_replay(
        request, replay_out
    )

    assert model.turn == source_turns
    assert recorded_events == source_events
    assert normalize_behavior_trace(replay_events) == normalize_behavior_trace(
        source_events
    )
    assert replay_adapter.last_final_state_digest == checkpoints[-1].state_digest
    assert [item.state_digest for item in replay_adapter.last_checkpoint_digests] == [
        item.state_digest for item in checkpoints
    ]


async def test_live_carrier_fork_preserves_parent_and_replays_child_coverage(
    tmp_path,
    monkeypatch,
) -> None:
    parent_out = tmp_path / "parent"
    monkeypatch.setenv("REPLAY_OUTPUT_DIR", str(parent_out))
    parent_request = _request(INJECTED_CASE).model_copy(
        update={"recording": RecordingOptions(enabled=True)}
    )
    await _collect(
        LangGraphReactRuntime(chat_model=DependentOfficeChatModel()),
        parent_request,
    )
    parent_snapshot = {
        path.relative_to(parent_out).as_posix(): path.read_bytes()
        for path in parent_out.rglob("*")
        if path.is_file()
    }
    _, _, _, parent_checkpoints, _ = _load_recording(parent_out)
    parent_checkpoint = next(
        item
        for item in parent_checkpoints
        if item.kind.value == "after_tool"
        and item.state_artifact is not None
        and len(
            CheckpointStateEnvelope.model_validate_json(
                (parent_out / item.state_artifact.relative_path).read_bytes()
            ).enterprise_tool_state["office_episode"]["actions"]
        )
        == 1
    )
    parent_envelope = CheckpointStateEnvelope.model_validate_json(
        (parent_out / parent_checkpoint.state_artifact.relative_path).read_bytes()
    )
    replacement = "Use the alternate synthetic test instruction in this email."
    fork = replace_office_carrier_payload(parent_envelope, replacement)
    child_case = fork.initialization.test_case
    child_request = _request(child_case).model_copy(
        update={
            "execution_id": "langgraph-office-fork-child",
            "recording": None,
            "scenario_initialization": fork.initialization.model_dump(mode="json"),
        }
    )
    child_tools = ToolRegistry()
    child_initial = StateCodec().restore(
        fork.checkpoint_state,
        child_tools,
        execution_id=child_request.execution_id,
    )
    child_out = tmp_path / "child"
    monkeypatch.setenv("REPLAY_OUTPUT_DIR", str(child_out))
    child_adapter = LangGraphReactRuntime(chat_model=ForkSuffixChatModel(replacement))
    child_events = [
        event
        async for event in child_adapter.execute_fork(
            child_request,
            initial=child_initial,
            registry=child_tools,
            audit_events=[{"event_type": "fork_started"}],
        )
    ]
    replay_adapter, checkpoints, _, replay_events = await _strict_replay(
        child_request, child_out
    )

    assert parent_snapshot == {
        path.relative_to(parent_out).as_posix(): path.read_bytes()
        for path in parent_out.rglob("*")
        if path.is_file()
    }
    assert normalize_behavior_trace(replay_events) == normalize_behavior_trace(
        child_events
    )
    assert replay_adapter.last_final_state_digest == checkpoints[-1].state_digest
    child_state = OfficeToolRuntimeState.model_validate(
        fork.checkpoint_state.enterprise_tool_state["office_episode"]
    )
    source_path = tmp_path / "child-source.jsonl"
    replay_path = tmp_path / "child-replay.jsonl"
    for path, events in ((source_path, child_events), (replay_path, replay_events)):
        path.write_text(
            "".join(event.model_dump_json() + "\n" for event in events),
            encoding="utf-8",
        )
    resolver = CoverageInputResolver(
        trajectory_root=tmp_path,
        manifest_root=tmp_path / "manifests",
        artifact_root=tmp_path / "artifacts",
    )
    source_input = resolver.from_trajectory_path(
        source_path, scenario_initialization=child_state
    )
    replay_input = resolver.from_trajectory_path(
        replay_path, scenario_initialization=child_state
    )
    source_profile = BehaviorFeatureExtractor().extract(
        trajectory_id=source_input.trajectory_id,
        execution_id=source_input.execution_id,
        events=normalize_behavior_trace(source_input.events),
        office_evidence=source_input.scenario_evidence,
    )
    replay_profile = BehaviorFeatureExtractor().extract(
        trajectory_id=replay_input.trajectory_id,
        execution_id=replay_input.execution_id,
        events=normalize_behavior_trace(replay_input.events),
        office_evidence=replay_input.scenario_evidence,
    )
    taxonomy = RiskTaxonomyLoader(Path("config/risk-taxonomy.yaml")).load()

    assert source_input.scenario_evidence is not None
    assert source_input.scenario_evidence.baseline_action_count == 1
    assert source_profile.profile_hash == replay_profile.profile_hash
    assert {
        (hit.category_id, hit.stage, hit.depth)
        for hit in RiskRecognizer(taxonomy).recognize(source_input)
    } == {
        (hit.category_id, hit.stage, hit.depth)
        for hit in RiskRecognizer(taxonomy).recognize(replay_input)
    }
