from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from app.adapter.base import AgentNoSubmitError
from app.adapter.factory import AdapterFactory
from app.adapter.trace_react_adapter import TraceReactAdapter
from app.protocol import (
    ExecutionBackend,
    ExecutionRequest,
    ExecutionStatus,
    RecordingOptions,
    TraceEvent,
)
from app.runtime import RuntimeRpcError, RuntimeState

from sandbox.replay.models import ForkInjection, ReplayForkRequest


def request(execution_id: str = "exec-1", prompt: str = "读取 /etc/passwd") -> ExecutionRequest:
    return ExecutionRequest(
        execution_id=execution_id,
        case_id="case-1",
        prompt=prompt,
        scenario_id="test",
        agent_version="trace-react-v2",
    )


async def wait_for_terminal(runtime: RuntimeState, execution_id: str):
    for _ in range(100):
        result = await runtime.get(execution_id)
        if result.status in {
            ExecutionStatus.SUCCEEDED,
            ExecutionStatus.FAILED,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.TIMED_OUT,
        }:
            return result
        await asyncio.sleep(0.01)
    raise AssertionError("runtime did not reach terminal state")


async def test_runtime_executes_one_request_and_returns_trace() -> None:
    runtime = RuntimeState(expected_execution_id="exec-1")
    acknowledgement = await runtime.submit(request())
    assert acknowledgement["execution_id"] == "exec-1"
    result = await wait_for_terminal(runtime, "exec-1")
    page = await runtime.events("exec-1", -1, 100)
    assert result.status == ExecutionStatus.SUCCEEDED
    assert result.final_sequence == result.trace_count - 1
    assert page["terminal"] is True
    assert page["events"][-1]["event_type"] == "execution_finished"


async def test_runtime_submit_is_idempotent_for_same_digest() -> None:
    runtime = RuntimeState(expected_execution_id="exec-1")
    first = request()
    await runtime.submit(first)
    acknowledgement = await runtime.submit(first)
    assert acknowledgement["execution_id"] == "exec-1"
    await wait_for_terminal(runtime, "exec-1")


async def test_runtime_rejects_other_execution_id() -> None:
    runtime = RuntimeState(expected_execution_id="exec-1")
    with pytest.raises(RuntimeRpcError) as error:
        await runtime.submit(request("exec-2"))
    assert error.value.code == -32002


async def test_terminal_trace_requires_all_pages_to_be_consumed() -> None:
    runtime = RuntimeState(expected_execution_id="exec-1")
    await runtime.submit(request())
    await wait_for_terminal(runtime, "exec-1")
    first = await runtime.events("exec-1", -1, 2)
    assert first["final_sequence"] is not None
    assert first["terminal"] is False
    last = await runtime.events("exec-1", first["next_after_sequence"], 100)
    assert last["terminal"] is True


class StubTraceAdapter:
    last_checkpoint_digests = []
    last_final_state_digest = None

    async def execute(self, request: ExecutionRequest):
        yield TraceEvent(
            schema_version="1.2",
            execution_id=request.execution_id,
            sequence=0,
            event_type="execution_started",
            source="runtime",
        )
        yield TraceEvent(
            schema_version="1.2",
            execution_id=request.execution_id,
            sequence=1,
            event_type="execution_finished",
            source="runtime",
            data={"final_answer": "submitted"},
        )


class NoSubmitTraceAdapter:
    last_checkpoint_digests = []
    last_final_state_digest = None

    async def execute(self, request: ExecutionRequest):
        yield TraceEvent(
            schema_version="1.2",
            execution_id=request.execution_id,
            sequence=0,
            event_type="execution_started",
            source="runtime",
        )
        raise AgentNoSubmitError(limit_type="turn")


class AuditedFailureAdapter:
    last_checkpoint_digests = []
    last_final_state_digest = None

    async def execute(self, request: ExecutionRequest):
        del request
        error = RuntimeError("provider failed")
        error.error_code = "RuntimeTransportError"
        error.audit = {
            "http_status": 503,
            "response_bytes": 17,
            "response_truncated": False,
        }
        raise error
        yield


class SlowReactProvider:
    version = "slow-react-provider-v1"

    async def generate(self, messages, tools, *, seed):
        del messages, tools, seed
        await asyncio.sleep(10)


async def test_runtime_preserves_limited_provider_failure_audit() -> None:
    adapter = AuditedFailureAdapter()
    runtime = RuntimeState(
        expected_execution_id="exec-1",
        adapter_factory=AdapterFactory(trace_adapter_factory=lambda: adapter),
    )
    selected = request().model_copy(
        update={"execution_backend": ExecutionBackend.TRACE_REACT_V2}
    )

    await runtime.submit(selected)
    result = await wait_for_terminal(runtime, "exec-1")
    page = await runtime.events("exec-1", -1, 100)

    assert result.status == ExecutionStatus.FAILED
    assert result.error_code == "RuntimeTransportError"
    assert page["events"][-1]["data"]["provider_audit"] == {
        "http_status": 503,
        "response_bytes": 17,
        "response_truncated": False,
    }


class StartsOnExecuteAdapter:
    last_checkpoint_digests = []
    last_final_state_digest = None

    def __init__(self) -> None:
        self.started = False

    async def execute(self, request: ExecutionRequest):
        self.started = True
        yield TraceEvent(
            execution_id=request.execution_id,
            sequence=0,
            event_type="execution_started",
            source="runtime",
        )


class RaisesAfterFinishedAdapter:
    last_checkpoint_digests = []
    last_final_state_digest = None

    async def execute(self, request: ExecutionRequest):
        yield TraceEvent(
            execution_id=request.execution_id,
            sequence=0,
            event_type="execution_started",
            source="runtime",
        )
        yield TraceEvent(
            execution_id=request.execution_id,
            sequence=1,
            event_type="execution_finished",
            source="runtime",
            data={"final_answer": "must not escape"},
        )
        raise RuntimeError("post-submit cleanup failed")


class CleansUpAfterAppendFailureAdapter:
    last_checkpoint_digests = []
    last_final_state_digest = None

    def __init__(self) -> None:
        self.cleaned_up = False

    async def execute(self, request: ExecutionRequest):
        try:
            yield TraceEvent(
                execution_id=request.execution_id,
                sequence=0,
                event_type="execution_started",
                source="runtime",
            )
            yield TraceEvent(
                execution_id=request.execution_id,
                sequence=2,
                event_type="model_start",
                source="model",
            )
        finally:
            self.cleaned_up = True


async def test_runtime_selects_trace_react() -> None:
    adapter = StubTraceAdapter()
    factory = AdapterFactory(trace_adapter_factory=lambda: adapter)
    runtime = RuntimeState(expected_execution_id="exec-1", adapter_factory=factory)
    selected = request().model_copy(
        update={"execution_backend": ExecutionBackend.TRACE_REACT_V2}
    )

    await runtime.submit(selected)
    result = await wait_for_terminal(runtime, "exec-1")

    assert result.status == ExecutionStatus.SUCCEEDED
    assert result.final_answer == "submitted"


def test_runtime_reuses_adapter_factory_for_live_forks() -> None:
    factory = AdapterFactory(trace_adapter_factory=StubTraceAdapter)

    runtime = RuntimeState(expected_execution_id="exec-1", adapter_factory=factory)

    assert runtime.replay_adapter.adapter_factory is factory


class RejectingForkAdapter:
    last_checkpoint_digests = []
    last_final_state_digest = None

    async def execute_fork(self, request: ReplayForkRequest):
        del request
        raise ValueError("Office V2 fork cannot change the frozen task")
        yield


async def test_rejected_fork_publishes_current_terminal_schema() -> None:
    runtime = RuntimeState(expected_execution_id="exec-1")
    runtime.replay_adapter = RejectingForkAdapter()
    fork = ReplayForkRequest(
        execution_id="exec-1",
        child_replay_id="replay-child",
        manifest_relative_path="manifest.json",
        checkpoint_id="checkpoint-1",
        injection=ForkInjection(type="prompt_append", content="not allowed"),
    )

    await runtime.submit_fork(fork)
    result = await wait_for_terminal(runtime, "exec-1")
    page = await runtime.events("exec-1", -1, 100)

    assert result.status is ExecutionStatus.FAILED
    assert page["terminal"] is True
    assert page["events"][-1]["event_type"] == "execution_error"
    assert page["events"][-1]["schema_version"] == "1.2"
    assert "cannot change the frozen task" in page["events"][-1]["data"]["message"]


async def test_strict_replay_runtime_rejects_live_execution() -> None:
    runtime = RuntimeState(expected_execution_id="exec-1", runtime_mode="strict_replay")

    with pytest.raises(RuntimeRpcError, match="rejects live execution"):
        await runtime.submit(request())


async def test_runtime_selects_trace_react_only_when_request_is_explicit() -> None:
    adapter = StubTraceAdapter()
    factory = AdapterFactory(trace_adapter_factory=lambda: adapter)
    runtime = RuntimeState(expected_execution_id="exec-1", adapter_factory=factory)
    selected = request().model_copy(
        update={"execution_backend": ExecutionBackend.TRACE_REACT_V2}
    )

    await runtime.submit(selected)
    result = await wait_for_terminal(runtime, "exec-1")

    assert result.status == ExecutionStatus.SUCCEEDED
    assert result.final_answer == "submitted"


async def test_runtime_preserves_agent_no_submit_failure_code() -> None:
    factory = AdapterFactory(trace_adapter_factory=NoSubmitTraceAdapter)
    runtime = RuntimeState(expected_execution_id="exec-1", adapter_factory=factory)
    selected = request().model_copy(
        update={"execution_backend": ExecutionBackend.TRACE_REACT_V2}
    )

    await runtime.submit(selected)
    result = await wait_for_terminal(runtime, "exec-1")
    page = await runtime.events("exec-1", -1, 100)

    assert result.status == ExecutionStatus.FAILED
    assert result.error_code == "agent_no_submit"
    assert "limit=turn" in (result.error_message or "")
    assert page["events"][-1]["event_type"] == "execution_error"
    assert page["events"][-1]["data"]["error_code"] == "agent_no_submit"
    assert {event["schema_version"] for event in page["events"]} == {"1.2"}


async def test_immediate_cancel_is_terminal_before_adapter_starts() -> None:
    adapter = StartsOnExecuteAdapter()
    factory = AdapterFactory(trace_adapter_factory=lambda: adapter)
    runtime = RuntimeState(expected_execution_id="exec-1", adapter_factory=factory)

    acknowledgement = await runtime.submit(request())
    cancelled = await runtime.cancel("exec-1")
    result = await runtime.get("exec-1")
    page = await runtime.events("exec-1", -1, 100)

    assert acknowledgement["status"] == ExecutionStatus.PENDING.value
    assert cancelled["status"] == ExecutionStatus.CANCELLED.value
    assert adapter.started is False
    assert result.status == ExecutionStatus.CANCELLED
    assert result.error_code == "execution_cancelled"
    assert result.final_answer is None
    assert page["terminal"] is True
    assert [event["event_type"] for event in page["events"]] == [
        "execution_cancelled"
    ]


async def test_failure_after_finished_does_not_publish_success_terminal() -> None:
    factory = AdapterFactory(trace_adapter_factory=RaisesAfterFinishedAdapter)
    runtime = RuntimeState(expected_execution_id="exec-1", adapter_factory=factory)

    await runtime.submit(request())
    result = await wait_for_terminal(runtime, "exec-1")
    page = await runtime.events("exec-1", -1, 100)

    assert result.status == ExecutionStatus.FAILED
    assert result.error_code == "unknown_error"
    assert result.error_message == "post-submit cleanup failed"
    assert result.final_answer is None
    assert [event["event_type"] for event in page["events"]] == [
        "execution_started",
        "execution_error",
    ]
    assert page["terminal"] is True


async def test_append_failure_closes_adapter_before_result_is_published() -> None:
    adapter = CleansUpAfterAppendFailureAdapter()
    factory = AdapterFactory(trace_adapter_factory=lambda: adapter)
    runtime = RuntimeState(expected_execution_id="exec-1", adapter_factory=factory)

    await runtime.submit(request())
    result = await wait_for_terminal(runtime, "exec-1")

    assert result.status == ExecutionStatus.FAILED
    assert adapter.cleaned_up is True


async def test_immediate_v2_cancel_uses_v2_trace_schema() -> None:
    adapter = StartsOnExecuteAdapter()
    factory = AdapterFactory(trace_adapter_factory=lambda: adapter)
    runtime = RuntimeState(expected_execution_id="exec-1", adapter_factory=factory)
    selected = request().model_copy(
        update={"execution_backend": ExecutionBackend.TRACE_REACT_V2}
    )

    await runtime.submit(selected)
    await runtime.cancel("exec-1")
    page = await runtime.events("exec-1", -1, 100)

    assert adapter.started is False
    assert page["events"][-1]["event_type"] == "execution_cancelled"
    assert page["events"][-1]["schema_version"] == "1.2"


async def test_cancelled_recording_keeps_diagnostic_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "replay-out"
    monkeypatch.setenv("REPLAY_OUTPUT_DIR", str(output_dir))
    runtime = RuntimeState(
        expected_execution_id="exec-1",
        adapter_factory=AdapterFactory(
            trace_adapter_factory=lambda: TraceReactAdapter(
                provider=SlowReactProvider()
            )
        ),
    )
    recorded = request(prompt="loop forever: echo loop").model_copy(
        update={
            "max_steps": 100,
            "timeout_seconds": 10,
            "recording": RecordingOptions(enabled=True),
        }
    )
    await runtime.submit(recorded)
    await asyncio.sleep(0.05)
    await runtime.cancel("exec-1")
    await asyncio.sleep(0.1)
    result = await runtime.get("exec-1")

    assert result.status == ExecutionStatus.CANCELLED
    determinism = json.loads((output_dir / "determinism-config.json").read_bytes())
    assert determinism["recording_complete"] is False
    assert determinism["incomplete_reason"] == "CancelledError"
    checkpoints = [
        json.loads(line)
        for line in (output_dir / "checkpoints.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert checkpoints
    assert all(checkpoint["recoverable"] is False for checkpoint in checkpoints)
    audit = (output_dir / "recording-audit.jsonl").read_text(encoding="utf-8")
    assert "recording_incomplete" in audit
