from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.adapter.base import AdapterConfigurationError, AgentNoSubmitError
from app.adapter.factory import AdapterFactory
from app.adapter.langgraph_react_runtime import LangGraphReactRuntime
from app.office_v2_session import (
    OfficeV2LiveOracleArtifact,
    OfficeV2RecordingState,
)
from app.replay.react_decision_recorder import RecordedReactProvider
from app.replay.replay_adapter import ReplayAdapter
from app.replay.state_codec import StateCodec
from app.replay.tool_recorder import ToolReplayer
from app.tools.base import ToolRegistry
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from sandbox.models import TestCase as SandboxTestCase
from sandbox.protocol import (
    OFFICE_V2_SCENARIO_ID,
    ExecutionRequest,
    ModelOptions,
    TraceEvent,
)
from sandbox.replay.artifact_store import ArtifactStore
from sandbox.replay.canonical import canonical_json_bytes
from sandbox.replay.digests import sha256_digest
from sandbox.replay.exceptions import ReplayDivergenceError
from sandbox.replay.models import (
    CheckpointStateEnvelope,
    RecordedModelDecision,
    RecordedToolInteraction,
    ReplayMode,
    ReplayRequest,
    StateCheckpoint,
)
from sandbox.replay.replay_engine import ReplayEngine
from sandbox.scenarios.office_v2.agent_context import derive_agent_identity_context
from sandbox.scenarios.office_v2.attack_cases import (
    build_representative_scenario_fixtures,
)
from sandbox.scenarios.office_v2.attack_models import AttackEntryKind
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASE_BY_ID
from sandbox.scenarios.office_v2.execution_contracts import build_v2_execution_envelope
from sandbox.scenarios.office_v2.interaction import ResponseChannel
from sandbox.scenarios.office_v2.interaction_session import ScriptedResponseDirective
from sandbox.scenarios.office_v2.oracle_evidence import OracleEvidenceIntegrityError
from sandbox.scenarios.office_v2.oracle_models import ObjectiveCompletionKind
from sandbox.tool_contracts import OFFICE_V2_TOOL_SPECS


def _request(
    *,
    recording: bool = False,
    max_steps: int = 8,
    model: ModelOptions | None = None,
    case_id: str = "clean.t10.borealis",
    response_directives: tuple[ScriptedResponseDirective, ...] = (),
) -> ExecutionRequest:
    canonical = load_canonical_world()
    case = CLEAN_CASE_BY_ID[case_id]
    model = model or ModelOptions(provider="fake", model_name="stage7-scripted")
    envelope = build_v2_execution_envelope(
        case,
        initial_state=canonical.state,
        model_identity=model,
        response_directives=response_directives,
    )
    payload = {
        "execution_id": "episode.stage7.langgraph-v2",
        "case_id": case.case_id,
        "scenario_id": OFFICE_V2_SCENARIO_ID,
        "prompt": case.task.instruction,
        "max_steps": max_steps,
        "timeout_seconds": 30,
        "model": model,
        "office_v2_execution": envelope,
    }
    if recording:
        payload["recording"] = {"enabled": True}
    return ExecutionRequest.model_validate(payload)


class DependentV2ChatModel:
    def __init__(self) -> None:
        self.turn = 0
        self.bound_tools = []
        self.inputs = []

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    async def ainvoke(self, messages):
        self.inputs.append(tuple(messages))
        self.turn += 1
        if self.turn == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_files",
                        "args": {"query": "decision-summary", "page_size": 25},
                        "id": "search-workspace",
                    }
                ],
            )
        previous = next(
            message for message in reversed(messages) if isinstance(message, ToolMessage)
        )
        result = json.loads(previous.content)
        if self.turn == 2:
            hit = next(
                item
                for item in result["data"]["items"]
                if item["path"] == "/workspace/borealis/decision-summary.md"
            )
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"path": hit["path"]},
                        "id": "read-workspace",
                    }
                ],
            )
        if self.turn == 3:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "create_drive_file",
                        "args": {
                            "name": "decision-summary handoff.md",
                            "content": result["data"]["content"],
                            "mime_type": "text/markdown",
                            "classification": "internal",
                            "source_refs": [result["data"]["resource"]],
                        },
                        "id": "create-handoff",
                    }
                ],
            )
        return AIMessage(
            content="",
            tool_calls=[
                {"name": "submit", "args": {"answer": "Published."}, "id": "submit"}
            ],
        )


class TextOnlyChatModel:
    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        return AIMessage(content="The task is complete.")


class SubmitOnlyChatModel:
    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        return AIMessage(
            content="",
            tool_calls=[
                {"name": "submit", "args": {"answer": "Observed."}, "id": "submit"}
            ],
        )


class InvalidClarificationRecoveryChatModel:
    def __init__(self, *, repeat: bool = False) -> None:
        self.turn = 0
        self.repeat = repeat
        self.invalid_call = {
            "name": "request_clarification",
            "args": {
                "question_kind": "disambiguation",
                "candidate_refs": [
                    {"kind": "drive_file", "resource_id": "drive.delta.one"},
                    {"kind": "drive_file", "resource_id": "drive.delta.two"},
                ],
                "missing_fact_descriptions": ["Which file should be removed?"],
                "requested_action": "delete",
                "requested_resource_kinds": ["drive_file"],
            },
        }

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.turn += 1
        if self.turn == 1 or self.repeat:
            return AIMessage(
                content="",
                tool_calls=[
                    {**self.invalid_call, "id": f"invalid-{self.turn}"},
                ],
            )
        prior = next(
            message for message in reversed(messages) if isinstance(message, ToolMessage)
        )
        result = json.loads(prior.content)
        assert result["error"] == "invalid_arguments"
        assert result["retry_allowed"] is True
        assert any(
            "disambiguation accepts only candidate_refs" in detail
            for detail in result["validation_errors"]
        )
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "submit",
                    "args": {"answer": "Stopped safely."},
                    "id": "submit",
                }
            ],
        )


class RejectedClarificationRecoveryChatModel:
    def __init__(self, *, repeat: bool = False) -> None:
        self.turn = 0
        self.repeat = repeat
        self.call = {
            "name": "request_clarification",
            "args": {
                "question_kind": "disambiguation",
                "candidate_refs": [
                    {"kind": "drive_file", "resource_id": "drive.delta.one"},
                    {"kind": "drive_file", "resource_id": "drive.delta.two"},
                ],
            },
        }

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.turn += 1
        if self.turn == 1 or self.repeat:
            return AIMessage(
                content="",
                tool_calls=[{**self.call, "id": f"rejected-{self.turn}"}],
            )
        prior = next(
            message for message in reversed(messages) if isinstance(message, ToolMessage)
        )
        result = json.loads(prior.content)
        assert result["error"] == "no_frozen_match"
        assert result["retry_allowed"] is True
        assert "submit a bounded answer" in result["correction"]
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "submit",
                    "args": {"answer": "The required scope remains unresolved."},
                    "id": "submit",
                }
            ],
        )


class AttackShareChatModel:
    def __init__(self, *, file_id: str, recipient: str) -> None:
        self.file_id = file_id
        self.recipient = recipient
        self.turn = 0

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.turn += 1
        if self.turn == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "share_drive_file",
                        "args": {
                            "file_id": self.file_id,
                            "recipient": self.recipient,
                        },
                        "id": "attack-share",
                    }
                ],
            )
        return AIMessage(
            content="",
            tool_calls=[
                {"name": "submit", "args": {"answer": "Observed."}, "id": "submit"}
            ],
        )


class AuthorizationV2ChatModel:
    def __init__(self, case_id: str) -> None:
        self.turn = 0
        self.inputs = []
        self.bound_tools = []
        self.request = CLEAN_CASE_BY_ID[case_id].task.user_response_script.requests[0]
        self.observed_ref = None

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    async def ainvoke(self, messages):
        self.inputs.append(tuple(messages))
        self.turn += 1
        if self.turn == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_drive_files",
                        "args": {"page_size": 25},
                        "id": "search-pack",
                    }
                ],
            )
        previous = next(
            message for message in reversed(messages) if isinstance(message, ToolMessage)
        )
        result = json.loads(previous.content)
        if self.turn == 2:
            expected = self.request.candidate_refs[0]
            hit = next(
                item
                for item in result["data"]["items"]
                if item["file_id"] == expected.resource_id
            )
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_drive_file",
                        "args": {
                            "file_id": hit["file_id"],
                            "version_id": hit["current_version_id"],
                        },
                        "id": "read-pack",
                    }
                ],
            )
        if self.turn == 3:
            self.observed_ref = result["data"]["resource"]
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_calendar_events",
                        "args": {"page_size": 25},
                        "id": "search-participant",
                    }
                ],
            )
        if self.turn == 4:
            recipient = self.request.requested_recipient_ids[0]
            assert recipient in json.dumps(result, sort_keys=True)
            scope = self.request.requested_action_scope
            assert scope is not None and self.observed_ref is not None
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "request_clarification",
                        "args": {
                            "question_kind": "authorization",
                            "candidate_refs": [self.observed_ref],
                            "requested_action": scope.action.value,
                            "requested_resource_kinds": [
                                item.value for item in scope.resource_kinds
                            ],
                            "requested_recipient_ids": [recipient],
                        },
                        "id": "request-authorization",
                    }
                ],
            )
        return AIMessage(
            content="",
            tool_calls=[
                {"name": "submit", "args": {"answer": "Handled."}, "id": "submit"}
            ],
        )


class DisambiguationV2ChatModel:
    def __init__(self) -> None:
        self.turn = 0
        self.inputs = []
        self.candidates = CLEAN_CASE_BY_ID[
            "clean.t1.apollo"
        ].task.user_response_script.requests[0].candidate_refs
        self.hits = {}
        self.observed_refs = []

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.inputs.append(tuple(messages))
        self.turn += 1
        if self.turn == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_drive_files",
                        "args": {"page_size": 25},
                        "id": "search-candidates",
                    }
                ],
            )
        previous = next(
            message for message in reversed(messages) if isinstance(message, ToolMessage)
        )
        result = json.loads(previous.content)
        if self.turn == 2:
            self.hits = {item["file_id"]: item for item in result["data"]["items"]}
            candidate = self.candidates[0]
            hit = self.hits[candidate.resource_id]
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_drive_file",
                        "args": {
                            "file_id": hit["file_id"],
                            "version_id": candidate.version_id,
                        },
                        "id": "read-candidate-1",
                    }
                ],
            )
        if self.turn == 3:
            self.observed_refs.append(result["data"]["resource"])
            candidate = self.candidates[1]
            hit = self.hits[candidate.resource_id]
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_drive_file",
                        "args": {
                            "file_id": hit["file_id"],
                            "version_id": candidate.version_id,
                        },
                        "id": "read-candidate-2",
                    }
                ],
            )
        if self.turn == 4:
            self.observed_refs.append(result["data"]["resource"])
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "request_clarification",
                        "args": {
                            "question_kind": "disambiguation",
                            "candidate_refs": self.observed_refs,
                        },
                        "id": "request-selection",
                    }
                ],
            )
        return AIMessage(
            content="",
            tool_calls=[
                {"name": "submit", "args": {"answer": "Selected."}, "id": "submit"}
            ],
        )


async def _collect(runtime: LangGraphReactRuntime, request: ExecutionRequest):
    return [event async for event in runtime.execute(request)]


async def test_formal_v2_request_runs_dynamic_multiturn_data_dependent_chain() -> None:
    model = DependentV2ChatModel()
    runtime = LangGraphReactRuntime(chat_model=model)
    request = _request()
    initial_digest = request.office_v2_execution.initial_state_digest

    events = await _collect(runtime, request)

    session = runtime.last_v2_session
    assert session is not None
    assert isinstance(model.inputs[0][0], SystemMessage)
    system_message = model.inputs[0][0].content
    assert "Current workspace context:" in system_message
    identity = derive_agent_identity_context(
        session.episode.state,
        session.runtime.actor,
        session.runtime.task,
    )
    assert identity.actor_display_name in system_message
    assert request.case_id not in system_message
    assert [item["function"]["name"] for item in model.bound_tools] == [
        *(spec.name for spec in OFFICE_V2_TOOL_SPECS),
        "request_clarification",
        "submit",
    ]
    assert [item.tool_name for item in session.runtime.invocations] == [
        "search_files",
        "read_file",
        "create_drive_file",
    ]
    create_sources = {
        source.argument_path: source for source in session.runtime.invocations[-1].argument_sources
    }
    assert ("content",) in create_sources
    assert ("source_refs", "0") in create_sources
    assert session.episode.state_digest != initial_digest
    assert runtime.last_final_state_digest == session.episode.state_digest
    assert events[-1].event_type == "execution_finished"
    assert events[-1].data["final_answer"] == "Published."
    assert next(
        item for item in events if item.event_type == "scenario_initialized"
    ).source == "trace.office.v2"
    serialized_results = json.dumps(
        [item.data for item in events if item.event_type == "tool_result"],
        sort_keys=True,
    )
    assert "policy_decision" not in serialized_results
    assert "source_evidence_ids" not in serialized_results

    artifact = runtime.last_v2_oracle_artifact
    assert artifact is not None
    assert OfficeV2LiveOracleArtifact.model_validate(
        artifact.model_dump(mode="json", exclude_none=False)
    ) == artifact
    assert artifact.oracle_result.input_bundle_digest == (
        artifact.evidence_bundle.bundle_digest
    )
    assert len(artifact.evidence_bundle.tool_exchanges) == 3
    assert artifact.oracle_result.evidence_closure

    tool_result_index = next(
        index
        for index, event in enumerate(events)
        if event.event_type == "tool_result" and event.source == "controlled_tools"
    )
    tampered = events[tool_result_index].model_copy(
        update={"data": {**events[tool_result_index].data, "status": "rejected"}}
    )
    changed_events = (*events[:tool_result_index], tampered, *events[tool_result_index + 1 :])
    with pytest.raises(OracleEvidenceIntegrityError):
        session.build_live_oracle_artifact(
            trace_events=changed_events,
            final_answer="Published.",
        )

    removed = session._trusted_tool_results.pop()
    try:
        with pytest.raises(ValueError, match="trusted tool sidecar is incomplete"):
            session.build_live_oracle_artifact(
                trace_events=tuple(events),
                final_answer="Published.",
            )
    finally:
        session._trusted_tool_results.append(removed)


async def test_attack_initialization_overlay_is_not_agent_realization() -> None:
    fixture = next(
        item
        for item in build_representative_scenario_fixtures()
        if item.materialization.initialization_transition is not None
    )
    model = ModelOptions(provider="fake", model_name="stage7-scripted")
    envelope = build_v2_execution_envelope(
        fixture.scenario_case,
        initial_state=fixture.materialization.initial_state,
        initialization_transition=fixture.materialization.initialization_transition,
        model_identity=model,
    )
    request = ExecutionRequest(
        execution_id="episode.stage7.attack-live-oracle",
        case_id=fixture.scenario_case.case_id,
        scenario_id=OFFICE_V2_SCENARIO_ID,
        prompt=fixture.scenario_case.task.instruction,
        max_steps=2,
        timeout_seconds=30,
        model=model,
        office_v2_execution=envelope,
    )
    runtime = LangGraphReactRuntime(chat_model=SubmitOnlyChatModel())

    events = await _collect(runtime, request)

    artifact = runtime.last_v2_oracle_artifact
    assert artifact is not None
    assert artifact.evidence_bundle.tool_exchanges == ()
    assert artifact.evidence_bundle.interaction_facts == ()
    assert artifact.evidence_bundle.timeline == ()
    assert artifact.evidence_bundle.identity.initial_state_digest == (
        fixture.scenario_case.initial_world_digest
    )
    assert artifact.evidence_bundle.identity.final_state_digest == (
        fixture.scenario_case.initial_world_digest
    )
    planned = artifact.oracle_result.security.planned_objectives[0]
    assert planned.completion_kind is ObjectiveCompletionKind.NONE
    assert planned.realized_milestone_ids == ()
    assert all(event.source != "controlled_tools" for event in events)


async def test_plain_completion_text_does_not_end_v2_episode() -> None:
    with pytest.raises(AgentNoSubmitError):
        await _collect(
            LangGraphReactRuntime(chat_model=TextOnlyChatModel()),
            _request(max_steps=2),
        )


async def test_invalid_control_arguments_explain_how_the_model_can_recover() -> None:
    model = InvalidClarificationRecoveryChatModel()

    events = await _collect(
        LangGraphReactRuntime(chat_model=model),
        _request(max_steps=3),
    )

    rejected = next(
        event
        for event in events
        if event.event_type == "tool_result"
        and event.data["name"] == "request_clarification"
    )
    assert rejected.data["retry_allowed"] is True
    assert rejected.data["validation_errors"]
    assert events[-1].event_type == "execution_finished"
    assert model.turn == 2


async def test_identical_invalid_control_arguments_stop_after_one_retry() -> None:
    model = InvalidClarificationRecoveryChatModel(repeat=True)
    events = []

    with pytest.raises(
        AgentNoSubmitError,
        match="limit=repeated_rejected_control_call",
    ):
        async for event in LangGraphReactRuntime(chat_model=model).execute(
            _request(max_steps=8)
        ):
            events.append(event)

    results = [
        event
        for event in events
        if event.event_type == "tool_result"
        and event.data["name"] == "request_clarification"
    ]
    assert [event.data["retry_allowed"] for event in results] == [True, False]
    assert model.turn == 2


async def test_identical_business_rejected_control_call_stops_after_one_retry() -> None:
    model = RejectedClarificationRecoveryChatModel(repeat=True)
    events = []

    with pytest.raises(
        AgentNoSubmitError,
        match="limit=repeated_rejected_control_call",
    ):
        async for event in LangGraphReactRuntime(chat_model=model).execute(
            _request(max_steps=8)
        ):
            events.append(event)

    results = [
        event
        for event in events
        if event.event_type == "tool_result"
        and event.data["name"] == "request_clarification"
    ]
    assert [event.data["status"] for event in results] == ["rejected", "rejected"]
    assert [event.data["error"] for event in results] == [
        "no_frozen_match",
        "no_frozen_match",
    ]
    assert [event.data["retry_allowed"] for event in results] == [True, False]
    assert model.turn == 2


async def test_business_rejected_control_call_explains_safe_recovery() -> None:
    model = RejectedClarificationRecoveryChatModel()

    events = await _collect(
        LangGraphReactRuntime(chat_model=model),
        _request(max_steps=3),
    )

    result = next(
        event
        for event in events
        if event.event_type == "tool_result"
        and event.data["name"] == "request_clarification"
    )
    assert result.data["status"] == "rejected"
    assert result.data["error"] == "no_frozen_match"
    assert result.data["retry_allowed"] is True
    assert "submit a bounded answer" in result.data["correction"]
    assert events[-1].event_type == "execution_finished"
    assert model.turn == 2


async def test_v2_recording_captures_versioned_state_and_tool_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "v2-recording"
    monkeypatch.setenv("REPLAY_OUTPUT_DIR", str(output_dir))

    await _collect(
        LangGraphReactRuntime(chat_model=DependentV2ChatModel()),
        _request(recording=True),
    )

    initial = CheckpointStateEnvelope.model_validate_json(
        (output_dir / "initial-state.json").read_bytes()
    )
    assert initial.state_codec_version == "office-v2-state-codec-v1"
    assert initial.scenario_state_codec == initial.state_codec_version
    with pytest.raises(ValueError, match="legacy state codec"):
        StateCodec().restore(initial, ToolRegistry(), execution_id="rejected")

    checkpoints = {
        item.checkpoint_id: item
        for item in (
            StateCheckpoint.model_validate_json(line)
            for line in (output_dir / "checkpoints.jsonl").read_text().splitlines()
        )
    }
    checkpoint_states = {
        checkpoint_id: CheckpointStateEnvelope.model_validate_json(
            (output_dir / checkpoint.state_artifact.relative_path).read_bytes()
        )
        for checkpoint_id, checkpoint in checkpoints.items()
    }
    records = tuple(
        RecordedToolInteraction.model_validate_json(line)
        for line in (output_dir / "tool-records.jsonl").read_text().splitlines()
    )
    assert len(records) == 3
    for record in records:
        before = OfficeV2RecordingState.model_validate(
            checkpoint_states[record.before_checkpoint_id].scenario_state
        )
        after = OfficeV2RecordingState.model_validate(
            checkpoint_states[record.after_checkpoint_id].scenario_state
        )
        assert before.session.state_digest == record.side_effect_digest_before
        assert after.session.state_digest == record.side_effect_digest_after

    final_state = OfficeV2RecordingState.model_validate_json(
        (output_dir / "office-v2-recording-state.json").read_bytes()
    )
    oracle = OfficeV2LiveOracleArtifact.model_validate_json(
        (output_dir / "office-v2-oracle.json").read_bytes()
    )
    assert len(final_state.tool_invocations) == len(final_state.tool_results) == 3
    assert final_state.pending_clarification_request_ids == ()
    assert oracle.evidence_bundle.identity.final_state_digest == (
        final_state.session.state_digest
    )


async def test_v2_strict_replay_reexecutes_tools_without_model_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "v2-strict-source"
    monkeypatch.setenv("REPLAY_OUTPUT_DIR", str(output_dir))
    source_request = _request(recording=True)
    await _collect(
        LangGraphReactRuntime(chat_model=DependentV2ChatModel()),
        source_request,
    )
    initial = CheckpointStateEnvelope.model_validate_json(
        (output_dir / "initial-state.json").read_bytes()
    )
    initial_recording_state = OfficeV2RecordingState.model_validate(
        initial.scenario_state
    )
    expected_state = OfficeV2RecordingState.model_validate_json(
        (output_dir / "office-v2-recording-state.json").read_bytes()
    )
    expected_oracle = OfficeV2LiveOracleArtifact.model_validate_json(
        (output_dir / "office-v2-oracle.json").read_bytes()
    )
    decisions = [
        RecordedModelDecision.model_validate_json(line)
        for line in (output_dir / "model-decisions.jsonl").read_bytes().splitlines()
        if line.strip()
    ]
    tools = [
        RecordedToolInteraction.model_validate_json(line)
        for line in (output_dir / "tool-records.jsonl").read_bytes().splitlines()
        if line.strip()
    ]
    replay_request = source_request.model_copy(
        update={
            "execution_id": "episode.stage7.strict-replay",
            "recording": None,
        }
    )
    runtime = LangGraphReactRuntime()
    events = []
    async for event in runtime.execute_strict_replay(
        replay_request,
        initial={
            **initial.agent_state,
            "execution_id": replay_request.execution_id,
        },
        initial_recording_state=initial_recording_state,
        expected_recording_state=expected_state,
        expected_oracle=expected_oracle,
        provider=RecordedReactProvider(decisions),
        tool_replayer=ToolReplayer(ToolRegistry(), tools),
    ):
        events.append(event)

    assert any(event.event_type == "execution_finished" for event in events)
    assert runtime.last_v2_session is not None
    assert runtime.last_v2_oracle_artifact is not None
    assert runtime.last_v2_session.export_recording_state() == expected_state
    assert (
        runtime.last_v2_oracle_artifact.oracle_result.result_digest
        == expected_oracle.oracle_result.result_digest
    )
    assert runtime.last_checkpoint_digests


async def test_v2_manifest_strict_replay_matches_all_source_checkpoints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "v2-manifest-source"
    input_dir = tmp_path / "replay-in"
    monkeypatch.setenv("REPLAY_OUTPUT_DIR", str(output_dir))
    source_request = _request(recording=True)
    await _collect(
        LangGraphReactRuntime(chat_model=DependentV2ChatModel()),
        source_request,
    )
    downloaded = {
        path.relative_to(output_dir).as_posix(): path.read_bytes()
        for path in output_dir.rglob("*")
        if path.is_file()
    }
    source_events = [
        TraceEvent.model_validate_json(line)
        for line in downloaded["events.jsonl"].splitlines()
        if line.strip()
    ]
    artifact_store = ArtifactStore(input_dir / "artifacts")
    engine = ReplayEngine(
        None,
        None,
        None,
        None,
        None,
        artifact_store,
        None,
    )
    manifest = engine._build_manifest(
        replay_id="replay.office-v2.stage7.8",
        case=SandboxTestCase(
            case_id=source_request.case_id,
            prompt=source_request.prompt,
            scenario_id=source_request.scenario_id,
            seed=1,
        ),
        image_ref="trace-redteam-agent:test",
        image_digest="sha256:" + "1" * 64,
        events=source_events,
        downloaded=downloaded,
    )
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "manifest.json").write_bytes(canonical_json_bytes(manifest))
    adapter = ReplayAdapter(input_dir=input_dir)
    replay_events = [
        event
        async for event in adapter.execute(
            ReplayRequest(
                execution_id="episode.stage7.manifest-replay",
                replay_run_id="run.stage7.manifest-replay",
                source_replay_id=manifest.replay_id,
                mode=ReplayMode.STRICT,
                manifest_relative_path="manifest.json",
            )
        )
    ]
    source_checkpoints = [
        StateCheckpoint.model_validate_json(line)
        for line in downloaded["checkpoints.jsonl"].splitlines()
        if line.strip()
    ]

    assert replay_events[-1].event_type == "execution_finished"
    assert [item.kind for item in adapter.last_checkpoint_digests] == [
        item.kind.value for item in source_checkpoints
    ]
    assert [item.state_digest for item in adapter.last_checkpoint_digests] == [
        item.state_digest for item in source_checkpoints
    ]
    assert adapter.last_final_state_digest == source_checkpoints[-1].state_digest


async def test_v2_attack_recording_strict_replay_preserves_security_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = next(
        item
        for item in build_representative_scenario_fixtures()
        if item.scenario_case.attack_objective.objective_id.startswith("objective.a01.")
        and item.scenario_case.adversarial_condition.entry_kind
        is AttackEntryKind.DIRECT_TASK
    )
    source_binding = next(
        item for item in fixture.scenario_case.objective_bindings if item.slot_id == "source_file"
    )
    recipient_binding = next(
        item for item in fixture.scenario_case.objective_bindings if item.slot_id == "recipient"
    )
    source_ref = source_binding.resource_refs[0]
    recipient = recipient_binding.principal_ids[0]
    model = ModelOptions(provider="fake", model_name="stage7-attack-scripted")
    envelope = build_v2_execution_envelope(
        fixture.scenario_case,
        initial_state=fixture.materialization.initial_state,
        initialization_transition=fixture.materialization.initialization_transition,
        model_identity=model,
    )
    source_request = ExecutionRequest(
        execution_id="episode.stage7.attack-recording",
        case_id=fixture.scenario_case.case_id,
        scenario_id=OFFICE_V2_SCENARIO_ID,
        prompt=fixture.scenario_case.task.instruction,
        max_steps=3,
        timeout_seconds=30,
        model=model,
        office_v2_execution=envelope,
        recording={"enabled": True},
    )
    output_dir = tmp_path / "v2-attack-source"
    monkeypatch.setenv("REPLAY_OUTPUT_DIR", str(output_dir))
    await _collect(
        LangGraphReactRuntime(
            chat_model=AttackShareChatModel(
                file_id=source_ref.resource_id,
                recipient=recipient,
            )
        ),
        source_request,
    )
    initial = CheckpointStateEnvelope.model_validate_json(
        (output_dir / "initial-state.json").read_bytes()
    )
    expected_state = OfficeV2RecordingState.model_validate_json(
        (output_dir / "office-v2-recording-state.json").read_bytes()
    )
    expected_oracle = OfficeV2LiveOracleArtifact.model_validate_json(
        (output_dir / "office-v2-oracle.json").read_bytes()
    )
    decisions = [
        RecordedModelDecision.model_validate_json(line)
        for line in (output_dir / "model-decisions.jsonl").read_bytes().splitlines()
        if line.strip()
    ]
    tool_records = [
        RecordedToolInteraction.model_validate_json(line)
        for line in (output_dir / "tool-records.jsonl").read_bytes().splitlines()
        if line.strip()
    ]
    replay = LangGraphReactRuntime()
    replay_request = source_request.model_copy(
        update={"execution_id": "episode.stage7.attack-replay", "recording": None}
    )
    replay_events = [
        event
        async for event in replay.execute_strict_replay(
            replay_request,
            initial={**initial.agent_state, "execution_id": replay_request.execution_id},
            initial_recording_state=OfficeV2RecordingState.model_validate(
                initial.scenario_state
            ),
            expected_recording_state=expected_state,
            expected_oracle=expected_oracle,
            provider=RecordedReactProvider(decisions),
            tool_replayer=ToolReplayer(ToolRegistry(), tool_records),
        )
    ]

    assert any(event.event_type == "tool_result" for event in replay_events)
    assert replay.last_v2_oracle_artifact is not None
    assert (
        replay.last_v2_oracle_artifact.oracle_result.security
        == expected_oracle.oracle_result.security
    )
    assert (
        replay.last_v2_oracle_artifact.oracle_result.result_digest
        == expected_oracle.oracle_result.result_digest
    )


@pytest.mark.parametrize("tamper", ["arguments", "result", "state"])
async def test_v2_strict_replay_rejects_resealed_tool_record_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    output_dir = tmp_path / f"v2-tamper-{tamper}"
    monkeypatch.setenv("REPLAY_OUTPUT_DIR", str(output_dir))
    source_request = _request(recording=True)
    await _collect(
        LangGraphReactRuntime(chat_model=DependentV2ChatModel()),
        source_request,
    )
    initial = CheckpointStateEnvelope.model_validate_json(
        (output_dir / "initial-state.json").read_bytes()
    )
    decisions = [
        RecordedModelDecision.model_validate_json(line)
        for line in (output_dir / "model-decisions.jsonl").read_bytes().splitlines()
        if line.strip()
    ]
    records = [
        RecordedToolInteraction.model_validate_json(line)
        for line in (output_dir / "tool-records.jsonl").read_bytes().splitlines()
        if line.strip()
    ]
    first = records[0]
    initial_recording_state = OfficeV2RecordingState.model_validate(
        initial.scenario_state
    )
    expected_recording_state = OfficeV2RecordingState.model_validate_json(
        (output_dir / "office-v2-recording-state.json").read_bytes()
    )
    if tamper == "arguments":
        changed = {**first.arguments, "query": "tampered-query"}
        records[0] = first.model_copy(
            update={"arguments": changed, "arguments_digest": sha256_digest(changed)}
        )
    elif tamper == "result":
        changed = {"status": "ok", "data": {"tampered": True}, "error": None}
        records[0] = first.model_copy(
            update={"result": changed, "result_digest": sha256_digest(changed)}
        )
    else:
        expected_recording_state = initial_recording_state
    replay_request = source_request.model_copy(
        update={"execution_id": f"episode.stage7.tamper-{tamper}", "recording": None}
    )
    runtime = LangGraphReactRuntime()
    with pytest.raises(
        ReplayDivergenceError,
        match="tool call diverged|tool result|trusted recording state",
    ):
        async for _ in runtime.execute_strict_replay(
            replay_request,
            initial={**initial.agent_state, "execution_id": replay_request.execution_id},
            initial_recording_state=initial_recording_state,
            expected_recording_state=expected_recording_state,
            expected_oracle=OfficeV2LiveOracleArtifact.model_validate_json(
                (output_dir / "office-v2-oracle.json").read_bytes()
            ),
            provider=RecordedReactProvider(decisions),
            tool_replayer=ToolReplayer(ToolRegistry(), records),
        ):
            pass


async def test_v2_recording_captures_grant_interaction_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "v2-interaction-recording"
    monkeypatch.setenv("REPLAY_OUTPUT_DIR", str(output_dir))
    case_id = "clean.t9.apollo"

    source_request = _request(
        case_id=case_id,
        response_directives=(_authorization_directive(),),
        recording=True,
    )
    await _collect(
        LangGraphReactRuntime(chat_model=AuthorizationV2ChatModel(case_id)),
        source_request,
    )

    state = OfficeV2RecordingState.model_validate_json(
        (output_dir / "office-v2-recording-state.json").read_bytes()
    )
    assert state.pending_clarification_request_ids == ()
    assert state.session.state.delegation_grants
    assert {item.event_type for item in state.interaction_events} >= {
        "agent_clarification_requested",
        "user_response_received",
        "interaction_result",
        "delegation_grant_created",
    }
    records = tuple(
        RecordedToolInteraction.model_validate_json(line)
        for line in (output_dir / "tool-records.jsonl").read_text().splitlines()
    )
    interaction = next(
        item for item in records if item.tool_name == "request_clarification"
    )
    assert interaction.side_effect_digest_before != interaction.side_effect_digest_after
    assert interaction.after_checkpoint_id is not None

    tampered = state.model_dump(mode="json", exclude_none=False)
    tampered["interaction_events"] = [
        item
        for item in tampered["interaction_events"]
        if item["event_type"] != "delegation_grant_created"
    ]
    tampered["recording_state_digest"] = sha256_digest(
        {
            key: value
            for key, value in tampered.items()
            if key != "recording_state_digest"
        }
    )
    with pytest.raises(ValueError, match="grant events"):
        OfficeV2RecordingState.model_validate(tampered)

    initial = CheckpointStateEnvelope.model_validate_json(
        (output_dir / "initial-state.json").read_bytes()
    )
    decisions = [
        RecordedModelDecision.model_validate_json(line)
        for line in (output_dir / "model-decisions.jsonl").read_bytes().splitlines()
        if line.strip()
    ]
    replay_request = source_request.model_copy(
        update={"execution_id": "episode.stage7.interaction-replay", "recording": None}
    )
    replay = LangGraphReactRuntime()
    async for _ in replay.execute_strict_replay(
        replay_request,
        initial={**initial.agent_state, "execution_id": replay_request.execution_id},
        initial_recording_state=OfficeV2RecordingState.model_validate(
            initial.scenario_state
        ),
        expected_recording_state=state,
        expected_oracle=OfficeV2LiveOracleArtifact.model_validate_json(
            (output_dir / "office-v2-oracle.json").read_bytes()
        ),
        provider=RecordedReactProvider(decisions),
        tool_replayer=ToolReplayer(ToolRegistry(), list(records)),
    ):
        pass
    assert replay.last_v2_session is not None
    assert replay.last_v2_session.export_recording_state() == state


def test_factory_routes_v2_only_through_locked_formal_runtime(monkeypatch) -> None:
    with pytest.raises(AdapterConfigurationError) as raised:
        AdapterFactory().create(_request())
    assert raised.value.error_code == "v2_requires_formal_agent_runtime"

    digest = "sha256:" + "a" * 64
    model = ModelOptions(
        provider="ollama",
        model_name="qwen3:8b",
        model_digest=digest,
        endpoint="http://127.0.0.1:11434",
    )
    monkeypatch.setenv("TRACE_G_FORMAL_AGENT", "1")
    monkeypatch.setenv("TRACE_G_MODEL_NAME", "qwen3:8b")
    monkeypatch.setenv("TRACE_G_MODEL_DIGEST", digest)
    monkeypatch.setenv("TRACE_G_OLLAMA_ENDPOINT", "http://127.0.0.1:11434")

    assert isinstance(AdapterFactory().create(_request(model=model)), LangGraphReactRuntime)


def _authorization_directive(
    *,
    channel: ResponseChannel = ResponseChannel.AUTHENTICATED_TASK_SESSION,
    responder_id: str = "user.maya.chen",
) -> ScriptedResponseDirective:
    contract = CLEAN_CASE_BY_ID["clean.t9.apollo"].task.user_response_script
    request = contract.requests[0]
    rule = contract.response_rules[0]
    return ScriptedResponseDirective(
        request_id=request.request_id,
        rule_id=rule.rule_id,
        turn_id=f"turn.stage7.5.{channel.value}.{responder_id}",
        responder_id=responder_id,
        authenticated_principal_id=responder_id,
        channel=channel,
    )


@pytest.mark.parametrize(
    ("directive", "expected_failure", "expect_grant"),
    (
        (_authorization_directive(), None, True),
        (
            _authorization_directive(channel=ResponseChannel.BUSINESS_CONTENT),
            "untrusted_channel",
            False,
        ),
        (
            _authorization_directive(responder_id="user.hana.sato"),
            "responder_not_allowed",
            False,
        ),
    ),
)
async def test_formal_v2_trusted_interaction_controls_authority_state(
    directive: ScriptedResponseDirective,
    expected_failure: str | None,
    expect_grant: bool,
) -> None:
    case_id = "clean.t9.apollo"
    model = AuthorizationV2ChatModel(case_id)
    runtime = LangGraphReactRuntime(chat_model=model)
    request = _request(
        case_id=case_id,
        response_directives=(directive,),
        max_steps=8,
    )
    initial_digest = request.office_v2_execution.initial_state_digest

    events = await _collect(runtime, request)

    session = runtime.last_v2_session
    assert session is not None
    grants = session.episode.state.delegation_grants
    assert bool(grants) is expect_grant
    if expect_grant:
        grant = grants[0]
        assert grant.valid_from == 1000
        assert grant.expires_at == 1005
        assert session.episode.state_digest != initial_digest
        assert any(item.event_type == "delegation_grant_created" for item in events)
        assert any(
            "Authenticated task-session response" in str(message.content)
            for message in model.inputs[-1]
        )
    else:
        assert session.episode.state_digest == initial_digest
        result = next(
            item
            for item in events
            if item.event_type == "interaction_result"
        )
        assert result.data["failure_code"] == expected_failure
        assert all(item.event_type != "delegation_grant_created" for item in events)

    rule = CLEAN_CASE_BY_ID[case_id].task.user_response_script.response_rules[0]
    serialized = json.dumps(
        [item.model_dump(mode="json") for item in events],
        sort_keys=True,
    )
    assert rule.rule_id not in serialized
    assert rule.response_text not in serialized
    assert "grant_id" not in serialized


async def test_formal_v2_disambiguation_selects_observed_resource_without_state_change() -> None:
    case_id = "clean.t1.apollo"
    contract = CLEAN_CASE_BY_ID[case_id].task.user_response_script
    request_contract = contract.requests[0]
    rule = contract.response_rules[0]
    directive = ScriptedResponseDirective(
        request_id=request_contract.request_id,
        rule_id=rule.rule_id,
        turn_id="turn.stage7.5.disambiguation",
        responder_id=rule.authenticated_responder_id,
        authenticated_principal_id=rule.authenticated_responder_id,
    )
    model = DisambiguationV2ChatModel()
    runtime = LangGraphReactRuntime(chat_model=model)
    request = _request(
        case_id=case_id,
        response_directives=(directive,),
        max_steps=8,
    )
    initial_digest = request.office_v2_execution.initial_state_digest

    events = await _collect(runtime, request)

    session = runtime.last_v2_session
    assert session is not None
    assert session.episode.state_digest == initial_digest
    interaction = next(item for item in events if item.event_type == "interaction_result")
    assert interaction.data["status"] == "selection_accepted"
    assert interaction.data["selected_refs"] == [
        rule.selected_refs[0].model_dump(mode="json")
    ]
    assert any(
        "Authenticated task-session response" in str(message.content)
        for message in model.inputs[-1]
    )
