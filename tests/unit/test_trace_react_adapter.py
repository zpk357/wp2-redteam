from __future__ import annotations

import json

import pytest
from app.adapter.base import AdapterExecutionError, AgentNoSubmitError
from app.adapter.trace_react_adapter import TraceReactAdapter
from app.agent.fake_react_provider import FakeReactProvider
from app.agent.react_contract import ReactMessage, ReactToolCall, ReactTurn
from app.protocol import ExecutionBackend, ExecutionRequest, RecordingOptions

from sandbox.tool_contracts import ToolSpec


def request(*, max_steps: int = 4) -> ExecutionRequest:
    return ExecutionRequest(
        execution_id="trace-react-1",
        case_id="file-chain",
        prompt="List the workspace, read the first file, and submit its contents.",
        max_steps=max_steps,
        execution_backend=ExecutionBackend.TRACE_REACT_V2,
    )


async def collect(adapter: TraceReactAdapter, execution: ExecutionRequest):
    return [event async for event in adapter.execute(execution)]


async def test_fake_provider_completes_list_read_submit_chain() -> None:
    provider = FakeReactProvider()
    adapter = TraceReactAdapter(provider=provider)

    events = await collect(adapter, request())

    assert [
        event.data["name"] for event in events if event.event_type == "tool_call"
    ] == ["list_directory", "read_file"]
    assert [event.event_type for event in events].count("agent_submit") == 1
    assert events[-1].event_type == "execution_finished"
    assert events[-1].data["final_answer"].startswith("Read result:")
    assert provider.inputs[1][-1].role == "tool"
    assert provider.inputs[1][-1].name == "list_directory"
    assert provider.inputs[2][-1].name == "read_file"


async def test_trace_react_recording_persists_existing_replay_artifacts(
    tmp_path, monkeypatch
) -> None:
    output_dir = tmp_path / "replay-out"
    monkeypatch.setenv("REPLAY_OUTPUT_DIR", str(output_dir))
    execution = request().model_copy(
        update={"recording": RecordingOptions(enabled=True)}
    )

    events = await collect(
        TraceReactAdapter(provider=FakeReactProvider()),
        execution,
    )

    assert events[-1].event_type == "execution_finished"
    determinism = json.loads((output_dir / "determinism-config.json").read_text())
    assert determinism["execution_backend"] == ExecutionBackend.TRACE_REACT_V2.value
    assert determinism["recording_complete"] is True
    assert (output_dir / "prompt.json").is_file()
    assert (output_dir / "initial-state.json").is_file()
    assert len((output_dir / "model-decisions.jsonl").read_text().splitlines()) == 3
    assert len((output_dir / "tool-records.jsonl").read_text().splitlines()) == 2
    checkpoints = [
        json.loads(line)
        for line in (output_dir / "checkpoints.jsonl").read_text().splitlines()
    ]
    assert len(checkpoints) == 12
    assert {checkpoint["kind"] for checkpoint in checkpoints} == {
        "node_commit",
        "before_model",
        "after_model",
        "before_tool",
        "after_tool",
    }
    assert any((output_dir / "states").iterdir())


class TextOnlyProvider:
    version = "text-only-provider"

    async def generate(
        self,
        messages: tuple[ReactMessage, ...],
        tools: tuple[ToolSpec, ...],
        *,
        seed: int | None,
    ) -> ReactTurn:
        del messages, tools, seed
        return ReactTurn(assistant_text="The task is complete.", stop_reason="stop")


async def test_plain_completion_text_cannot_finish_without_submit() -> None:
    adapter = TraceReactAdapter(provider=TextOnlyProvider())

    with pytest.raises(AgentNoSubmitError) as error:
        await collect(adapter, request(max_steps=2))

    assert error.value.error_code == "agent_no_submit"
    assert adapter.last_final_state_digest is not None


class SequenceProvider:
    version = "sequence-provider"

    def __init__(self, turns: list[ReactTurn]) -> None:
        self.turns = list(turns)
        self.inputs: list[tuple[ReactMessage, ...]] = []

    async def generate(
        self,
        messages: tuple[ReactMessage, ...],
        tools: tuple[ToolSpec, ...],
        *,
        seed: int | None,
    ) -> ReactTurn:
        del tools, seed
        self.inputs.append(messages)
        return self.turns.pop(0)


async def test_multiple_tool_results_keep_declaration_order_and_identity() -> None:
    provider = SequenceProvider(
        [
            ReactTurn(
                tool_calls=[
                    ReactToolCall(
                        call_id="declared-a",
                        name="read_file",
                        arguments={"path": "/workspace/public.txt"},
                    ),
                    ReactToolCall(
                        call_id="declared-b",
                        name="read_file",
                        arguments={"path": "/workspace/notes/readme.txt"},
                    ),
                ]
            ),
            ReactTurn(
                tool_calls=[
                    ReactToolCall(name="submit", arguments={"answer": "both read"})
                ]
            ),
        ]
    )

    events = await collect(TraceReactAdapter(provider=provider), request())

    result_events = [event for event in events if event.event_type == "tool_result"]
    assert [event.data["call_id"] for event in result_events] == [
        "declared-a",
        "declared-b",
    ]
    next_turn_tools = [message for message in provider.inputs[1] if message.role == "tool"]
    assert [message.call_id for message in next_turn_tools] == [
        "declared-a",
        "declared-b",
    ]


async def test_duplicate_call_id_fails_as_integrity_error() -> None:
    provider = SequenceProvider(
        [
            ReactTurn(
                tool_calls=[
                    ReactToolCall(
                        call_id="duplicate",
                        name="list_directory",
                        arguments={"path": "/workspace"},
                    )
                ]
            ),
            ReactTurn(
                tool_calls=[
                    ReactToolCall(
                        call_id="duplicate",
                        name="read_file",
                        arguments={"path": "/workspace/public.txt"},
                    )
                ]
            ),
        ]
    )

    with pytest.raises(AdapterExecutionError) as error:
        await collect(TraceReactAdapter(provider=provider), request())

    assert error.value.error_code == "trace_duplicate_tool_call_id"


async def test_submit_cannot_hide_a_business_tool_in_the_same_turn() -> None:
    provider = SequenceProvider(
        [
            ReactTurn(
                tool_calls=[
                    ReactToolCall(name="submit", arguments={"answer": "done"}),
                    ReactToolCall(
                        name="write_file",
                        arguments={"path": "/workspace/late.txt", "content": "late"},
                    ),
                ]
            )
        ]
    )

    with pytest.raises(AdapterExecutionError) as error:
        await collect(TraceReactAdapter(provider=provider), request())

    assert error.value.error_code == "trace_mixed_submit_batch"


async def test_invalid_submit_arguments_are_classified() -> None:
    provider = SequenceProvider(
        [ReactTurn(tool_calls=[ReactToolCall(name="submit", arguments={"answer": ""})])]
    )

    with pytest.raises(AdapterExecutionError) as error:
        await collect(TraceReactAdapter(provider=provider), request())

    assert error.value.error_code == "agent_invalid_submit"
