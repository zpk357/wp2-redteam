from __future__ import annotations

import pytest
from app.protocol import (
    RUNTIME_CHILD_CLEANUP_TIMEOUT_SECONDS as CONTAINER_CHILD_CLEANUP_TIMEOUT,
)
from app.protocol import ExecutionRequest as ContainerExecutionRequest
from app.protocol import TraceEvent as ContainerTraceEvent
from pydantic import ValidationError

from sandbox.protocol import (
    RUNTIME_CHILD_CLEANUP_TIMEOUT_SECONDS,
    RUNTIME_TERMINAL_GRACE_SECONDS,
    RUNTIME_TERMINAL_TRANSPORT_MARGIN_SECONDS,
    ExecutionBackend,
    ExecutionRequest,
    ModelOptions,
    RecordingOptions,
    TraceEvent,
    V2ExecutionEnvelope,
)


def test_container_reexports_the_canonical_protocol_models() -> None:
    assert ContainerExecutionRequest is ExecutionRequest
    assert ContainerTraceEvent is TraceEvent
    assert ContainerExecutionRequest.model_json_schema() == ExecutionRequest.model_json_schema()


def test_host_terminal_grace_exceeds_child_cleanup_deadline() -> None:
    assert CONTAINER_CHILD_CLEANUP_TIMEOUT == RUNTIME_CHILD_CLEANUP_TIMEOUT_SECONDS
    assert (
        RUNTIME_TERMINAL_GRACE_SECONDS - RUNTIME_CHILD_CLEANUP_TIMEOUT_SECONDS
        >= RUNTIME_TERMINAL_TRANSPORT_MARGIN_SECONDS
    )


def test_recording_extension_is_optional_and_strict() -> None:
    request = ExecutionRequest(
        execution_id="exec-1",
        case_id="case-1",
        prompt="hello",
        recording=RecordingOptions(enabled=True),
    )
    assert request.recording is not None and request.recording.enabled is True
    assert request.recording.default_tool_replay_mode == "execute_and_verify"


def test_execution_backend_defaults_to_trace_react() -> None:
    request = ExecutionRequest(
        execution_id="exec-trace",
        case_id="case-trace",
        prompt="hello",
    )

    assert request.execution_backend == ExecutionBackend.TRACE_REACT_V2


@pytest.mark.parametrize("retired_backend", ["langgraph_v1", "inspect_react_v2"])
def test_retired_execution_backends_are_rejected(retired_backend: str) -> None:
    with pytest.raises(ValidationError):
        ExecutionRequest(
            execution_id="exec-retired",
            case_id="case-retired",
            prompt="hello",
            execution_backend=retired_backend,
        )


def test_trace_react_recording_is_supported() -> None:
    request = ExecutionRequest(
        execution_id="exec-trace-recording",
        case_id="case-trace-recording",
        prompt="hello",
        recording=RecordingOptions(enabled=True),
    )
    assert request.execution_backend == ExecutionBackend.TRACE_REACT_V2
    assert request.recording is not None and request.recording.enabled is True


def test_execution_request_carries_optional_scenario_initialization() -> None:
    initialization = {"kind": "synthetic-scenario", "schema_version": "1.0"}
    request = ExecutionRequest(
        execution_id="exec-scenario",
        case_id="case-scenario",
        prompt="hello",
        scenario_initialization=initialization,
    )

    assert request.scenario_initialization == initialization
    assert ContainerExecutionRequest.model_validate(
        request.model_dump(mode="json")
    ).scenario_initialization == initialization


def test_v2_execution_envelope_is_part_of_the_shared_protocol_schema() -> None:
    schema = V2ExecutionEnvelope.model_json_schema()
    request_schema = ExecutionRequest.model_json_schema()

    assert schema["properties"]["schema_version"]["const"] == (
        "office-v2-execution-envelope-v1"
    )
    assert "office_v2_execution" in request_schema["properties"]


def test_trace_event_accepts_current_replay_fields() -> None:
    event = TraceEvent(
        execution_id="exec-1",
        sequence=0,
        event_type="model_decision_recorded",
        source="fake-model-v1",
        logical_time=1,
        input_digest="sha256:" + "a" * 64,
        output_digest="sha256:" + "b" * 64,
        checkpoint_id="checkpoint-1",
    )
    assert event.logical_time == 1


def test_trace_event_rejects_retired_schema() -> None:
    with pytest.raises(ValidationError):
        TraceEvent(
            schema_version="1.1",
            execution_id="exec-old",
            sequence=0,
            event_type="execution_started",
            source="runtime",
        )


def test_model_options_reserve_optional_model_digest() -> None:
    options = ModelOptions(
        model_name="local-model",
        model_digest="sha256:" + "a" * 64,
    )

    assert options.model_digest == "sha256:" + "a" * 64
    assert options.model_dump(mode="json")["model_digest"] == options.model_digest
