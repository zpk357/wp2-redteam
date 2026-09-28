"""Offline proof: a session that dies before its bundle still leaves locatable evidence.

No model and no container.  The Runtime state machine is driven with an adapter that raises
before it yields anything; the host's retrieval path is driven with a stub runtime whose stream
ends without the structured terminal event; and the tool-result record is built from a refused
official result.  The fault is injected, the evidence is real.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from app.protocol import ExecutionRequest, ExecutionStatus
from app.runtime import RuntimeState

from sandbox.structured_v1 import diagnostics
from sandbox.structured_v1.diagnostics import SENSITIVE_KEYS, tool_result_record
from sandbox.structured_v1.evidence import DeliveryChannel
from sandbox.structured_v1.host_runner import HostRunnerError, StructuredHostRunner

NOT_A_REAL_CREDENTIAL = "sk-live-not-a-real-credential"


def _request(execution_id: str = "episode-0001") -> ExecutionRequest:
    return ExecutionRequest(
        execution_id=execution_id,
        case_id="case-1",
        prompt="task",
        scenario_id="structured-v1",
    )


class _SessionDiesBeforeBundle:
    """The fault: the session raises while the bundle is still unwritten."""

    last_final_state_digest = None
    last_checkpoint_digests = []

    async def execute(self, request: Any):  # noqa: ANN201 - mirrors the adapter protocol
        error = RuntimeError("session died before the bundle")
        error.audit = {"api_key": NOT_A_REAL_CREDENTIAL, "provider": "ollama"}
        raise error
        yield  # pragma: no cover - an async generator that never yields


class _Factory:
    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter

    def create(self, request: Any) -> Any:
        return self._adapter


async def _wait_for_terminal(runtime: RuntimeState, execution_id: str):
    for _ in range(200):
        result = await runtime.get(execution_id)
        if result.status in {
            ExecutionStatus.SUCCEEDED,
            ExecutionStatus.FAILED,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.TIMED_OUT,
        }:
            return result
        await asyncio.sleep(0.01)
    raise AssertionError("runtime did not reach a terminal state")


async def test_a_session_that_dies_before_its_bundle_leaves_a_failure_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(diagnostics.DIAGNOSTICS_DIR_ENV, str(tmp_path))
    runtime = RuntimeState(
        expected_execution_id="episode-0001",
        adapter_factory=_Factory(_SessionDiesBeforeBundle()),
    )

    await runtime.submit(_request())
    result = await _wait_for_terminal(runtime, "episode-0001")
    await runtime._task  # surface adapter cleanup failures rather than leaking a task exception
    page = await runtime.events("episode-0001", -1, 100)

    assert result.status is ExecutionStatus.FAILED
    terminal = page["events"][-1]
    assert terminal["event_type"] == "execution_error"
    assert terminal["source"] == "runtime"
    record_path = Path(terminal["data"]["diagnostics_record"])
    assert record_path.exists(), "the failure must not live only in memory"

    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["event_type"] == "execution_error"
    assert record["execution_id"] == "episode-0001"
    assert record["data"]["error_type"] == "RuntimeError"
    assert "session died before the bundle" in record["data"]["message"]
    assert record["data"]["provider_audit"]["provider"] == "ollama"
    assert record["data"]["provider_audit"]["api_key"] == "[REDACTED]"


class _Page:
    def __init__(self, events: list[Any]) -> None:
        self.events = events
        self.terminal = True


class _StubRuntime:
    """Streams a terminal failure and never a structured bundle event."""

    def __init__(self, events: list[Any]) -> None:
        self._events = events

    async def submit(self, handle: Any, request: ExecutionRequest) -> dict[str, str]:
        return {"execution_id": request.execution_id}

    async def poll_and_stream_events(self, handle: Any, request: ExecutionRequest):
        yield _Page(self._events)


async def test_the_host_records_the_events_it_received_when_no_bundle_came(
    tmp_path: Path,
) -> None:
    events = [
        SimpleNamespace(sequence=0, event_type="execution_started", source="runtime", data={}),
        SimpleNamespace(
            sequence=1,
            event_type="execution_error",
            source="runtime",
            data={
                "error_type": "AdapterConfigurationError",
                "error_code": "coverage-mismatch",
                "message": "the session refused before writing a bundle",
                "api_key": NOT_A_REAL_CREDENTIAL,
            },
        ),
    ]
    runner = object.__new__(StructuredHostRunner)
    runner.runtime = _StubRuntime(events)
    runner.failure_directory = tmp_path

    with pytest.raises(HostRunnerError) as error:
        await runner._drive(cast(Any, object()), _request())

    message = str(error.value)
    assert "no structured bundle event" in message
    assert "coverage-mismatch" in message
    assert "the session refused before writing a bundle" in message

    records = list(tmp_path.glob("*.host-failure.*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text(encoding="utf-8"))
    assert record["execution_id"] == "episode-0001"
    assert record["terminal_sequence"] == 1
    assert record["received_event_count"] == 2
    assert record["container_failure"]["data"]["error_code"] == "coverage-mismatch"
    assert record["container_failure"]["data"]["api_key"] == "[REDACTED]"
    assert [item["event_type"] for item in record["received_events"]] == [
        "execution_started",
        "execution_error",
    ]


def test_a_refused_tool_result_is_recorded_with_status_refusal_and_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(diagnostics.DIAGNOSTICS_DIR_ENV, str(tmp_path))
    report = SimpleNamespace(
        tool_call_id="call.004",
        tool_name="list_directory",
        action_request_id="action.004",
        ordinal=0,
        channel=DeliveryChannel.ACTOR_PRIVATE,
        committed=False,
        blocked=True,
        post_submit=False,
        content_digest="sha256:" + "a" * 64,
        source_object_id=None,
        source_field=None,
        slot_id=None,
        registered_units=(),
        registered_files=(),
        model_visible_result={
            "allowed": False,
            "outcome": "denied",
            "error": "the path is outside the workspace",
            "token": NOT_A_REAL_CREDENTIAL,
        },
    )

    path = diagnostics.append_tool_result("episode-0001", tool_result_record(report))

    assert path is not None
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["status"] == "blocked"
    assert record["blocked"] is True
    assert record["committed"] is False
    assert record["tool_call_id"] == "call.004"
    assert record["action_request_id"] == "action.004"
    assert record["source"] == {"object_id": None, "field": None, "slot_id": None}
    assert record["refusal"]["outcome"] == "denied"
    assert record["refusal"]["error"] == "the path is outside the workspace"
    assert record["model_visible_result"]["token"] == "[REDACTED]"


def test_the_local_redaction_list_matches_the_trace_rule() -> None:
    from app.tracing.sanitizer import SENSITIVE_KEYS as TRACE_KEYS

    assert SENSITIVE_KEYS == TRACE_KEYS
