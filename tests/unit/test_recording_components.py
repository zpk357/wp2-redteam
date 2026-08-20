from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.adapter.trace_react_adapter import TraceReactAdapter
from app.agent.fake_react_provider import FakeReactProvider
from app.replay.checkpoint import RecordingSession
from app.replay.replay_adapter import LoadedFork, ReplayAdapter
from app.replay.state_codec import StateCodec
from app.replay.tool_recorder import ToolRecorder, ToolReplayer
from app.tools.base import ToolRegistry

from sandbox.models import ExecutionRequest, ModelOptions, RecordingOptions
from sandbox.protocol import ModelProvider, ToolReplayMode
from sandbox.replay.exceptions import ArtifactIntegrityError, ReplayDivergenceError
from sandbox.replay.models import CheckpointKind, ResumePhase


@pytest.mark.parametrize("determinism", [{}, {"execution_backend": "retired"}])
def test_replay_rejects_recording_without_trace_backend(determinism: dict) -> None:
    with pytest.raises(ArtifactIntegrityError, match="trace_react_v2"):
        ReplayAdapter._require_trace_backend(determinism)


def test_recording_locks_system_prompt_identity(tmp_path: Path) -> None:
    request = ExecutionRequest(
        execution_id="prompt-lock-test",
        case_id="prompt-lock-case",
        prompt="Inspect the workspace",
        seed=7,
        recording=RecordingOptions(enabled=True),
    )
    session = RecordingSession(
        request,
        FakeReactProvider(),
        ToolRegistry(),
        output_dir=tmp_path,
        system_prompt_version="office-agent-system-prompt-v1",
        system_prompt_digest="sha256:" + "a" * 64,
    )
    session.start({"messages": [], "turn": 0})
    session.finalize_incomplete([], reason="unit_test")

    determinism = json.loads((tmp_path / "determinism-config.json").read_text())
    assert determinism["system_prompt_version"] == "office-agent-system-prompt-v1"
    assert determinism["system_prompt_digest"] == "sha256:" + "a" * 64


def test_recording_rejects_partial_system_prompt_identity(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be provided together"):
        RecordingSession(
            ExecutionRequest(
                execution_id="partial-prompt-lock-test",
                case_id="partial-prompt-lock-case",
                prompt="Inspect the workspace",
                seed=7,
                recording=RecordingOptions(enabled=True),
            ),
            FakeReactProvider(),
            ToolRegistry(),
            output_dir=tmp_path,
            system_prompt_version="office-agent-system-prompt-v1",
        )


def test_tool_recorder_execute_and_verify_round_trip() -> None:
    recorder = ToolRecorder(
        ToolRegistry(),
        replay_mode=ToolReplayMode.EXECUTE_AND_VERIFY,
    )
    action = {"name": "write_file", "arguments": {"path": "/workspace/a.txt", "content": "x"}}
    recorder.set_context(sequence=4, before_checkpoint_id="before-tool")
    expected = recorder.execute(action)
    recorder.attach_after_checkpoint("after-tool")

    replayer = ToolReplayer(ToolRegistry(), recorder.interactions)
    actual = replayer.execute(action)
    assert actual == expected
    replayer.assert_consumed()


def test_state_codec_restores_agent_and_all_controlled_tool_state() -> None:
    tools = ToolRegistry()
    tools.execute(
        {"name": "write_file", "arguments": {"path": "/workspace/new.txt", "content": "saved"}}
    )
    codec = StateCodec()
    envelope = codec.export(
        {"prompt": "hello", "execution_id": "old", "step_count": 2, "unknown": "drop"},
        tools,
        checkpoint_kind=CheckpointKind.AFTER_TOOL,
        resume_phase=ResumePhase.APPLY_TOOL_RESULT,
        logical_time=3,
        next_model_decision_index=1,
        next_tool_interaction_index=1,
    )
    restored_tools = ToolRegistry()
    state = codec.restore(envelope, restored_tools, execution_id="new")
    assert state["execution_id"] == "new"
    assert "unknown" not in state
    assert restored_tools.filesystem.read_file("/workspace/new.txt").output == "saved"
    assert restored_tools.state_digest() == tools.state_digest()


async def test_recording_request_writes_replay_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "replay-out"
    monkeypatch.setenv("REPLAY_OUTPUT_DIR", str(output_dir))
    request = ExecutionRequest(
        execution_id="record-1",
        case_id="case-1",
        prompt="读取 /workspace/public.txt",
        max_steps=5,
        timeout_seconds=5,
        recording=RecordingOptions(enabled=True),
    )
    events = [event async for event in TraceReactAdapter().execute(request)]
    assert events[-1].event_type == "execution_finished"
    assert all(event.schema_version == "1.2" for event in events)
    expected_files = {
        "prompt.json",
        "initial-state.json",
        "determinism-config.json",
        "events.jsonl",
        "model-decisions.jsonl",
        "tool-records.jsonl",
        "checkpoints.jsonl",
        "recording-audit.jsonl",
    }
    assert expected_files <= {path.name for path in output_dir.iterdir()}
    assert (output_dir / "model-decisions.jsonl").stat().st_size > 0
    assert (output_dir / "tool-records.jsonl").stat().st_size > 0
    assert list((output_dir / "states").glob("*.json"))


async def test_formal_live_fork_cannot_fall_back_to_calibration_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CalibrationFactory:
        def create(self, request):
            return TraceReactAdapter()

    request = ExecutionRequest(
        execution_id="formal-fork",
        case_id="case",
        prompt="task",
        model=ModelOptions(
            provider=ModelProvider.OLLAMA,
            model_name="qwen3:8b",
            model_digest="sha256:" + "1" * 64,
            endpoint="http://127.0.0.1:11434",
        ),
    )
    adapter = ReplayAdapter(adapter_factory=CalibrationFactory())
    monkeypatch.setattr(
        adapter,
        "load_fork",
        lambda fork_request: LoadedFork(
            request=request,
            model=None,
            tools=ToolRegistry(),
            initial={},
            recording=None,
            start_node="agent",
            audit_events=[],
            v2_recording_state=None,
        ),
    )

    with pytest.raises(ReplayDivergenceError, match="cannot fall back"):
        _ = [event async for event in adapter.execute_fork(object())]
