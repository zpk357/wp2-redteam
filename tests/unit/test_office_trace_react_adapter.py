from __future__ import annotations

from copy import deepcopy

import pytest
from app.adapter.trace_react_adapter import TraceReactAdapter
from app.agent.react_contract import ReactMessage, ReactToolCall, ReactTurn
from app.protocol import ExecutionRequest, ModelOptions
from app.tools.office_episode import OfficeEpisodeRequestError

from sandbox.scenarios.office_episode import (
    OfficeInitializationConfigurationError,
    build_office_episode_initialization,
)
from sandbox.scenarios.office_matrix import OFFICE_V1_TEST_MATRIX
from sandbox.scenarios.office_v1 import BOB_EMAIL, MEETING_START, MEETING_TITLE
from sandbox.tool_contracts import ToolSpec

CLEAN_CASE = OFFICE_V1_TEST_MATRIX.clean_cases[0]


class OfficeSequenceProvider:
    version = "office-sequence-provider"

    def __init__(self) -> None:
        self.turns = [
            ReactTurn(
                tool_calls=[
                    ReactToolCall(name="search_email", arguments={"query": "Bob"})
                ]
            ),
            ReactTurn(
                tool_calls=[
                    ReactToolCall(
                        name="read_email",
                        arguments={"email_id": "email-bob-001"},
                    )
                ]
            ),
            ReactTurn(
                tool_calls=[
                    ReactToolCall(
                        name="create_calendar_event",
                        arguments={
                            "title": MEETING_TITLE,
                            "start": MEETING_START,
                            "attendees": [BOB_EMAIL],
                        },
                    )
                ]
            ),
            ReactTurn(
                tool_calls=[ReactToolCall(name="submit", arguments={"answer": "done"})]
            ),
        ]
        self.inputs: list[tuple[ReactMessage, ...]] = []
        self.tool_names: list[list[str]] = []

    async def generate(
        self,
        messages: tuple[ReactMessage, ...],
        tools: tuple[ToolSpec, ...],
        *,
        seed: int | None,
    ) -> ReactTurn:
        del seed
        self.inputs.append(messages)
        self.tool_names.append([tool.name for tool in tools])
        return self.turns.pop(0)


def _request(**updates) -> ExecutionRequest:
    initialization = build_office_episode_initialization(CLEAN_CASE)
    request = ExecutionRequest(
        execution_id="office-trace-001",
        case_id=CLEAN_CASE.case_id,
        scenario_id=CLEAN_CASE.scenario.template_id,
        prompt=CLEAN_CASE.benign_task.instruction,
        max_steps=CLEAN_CASE.budget.max_steps,
        timeout_seconds=CLEAN_CASE.budget.timeout_seconds,
        seed=CLEAN_CASE.seed,
        scenario_initialization=initialization.model_dump(mode="json"),
    )
    return request.model_copy(update=updates)


async def _collect(adapter: TraceReactAdapter, request: ExecutionRequest):
    return [event async for event in adapter.execute(request)]


async def test_trace_react_consumes_office_initialization_and_13_tools() -> None:
    provider = OfficeSequenceProvider()
    events = await _collect(TraceReactAdapter(provider=provider), _request())

    initialized = next(event for event in events if event.event_type == "scenario_initialized")
    observed = next(
        event for event in events if event.event_type == "scenario_state_observed"
    )
    tool_results = [event for event in events if event.event_type == "tool_result"]

    assert initialized.source == "trace.office"
    assert initialized.data["case_id"] == CLEAN_CASE.case_id
    assert [event.data["name"] for event in tool_results] == [
        "search_email",
        "read_email",
        "create_calendar_event",
    ]
    assert all(result.data["authorized"] is True for result in tool_results)
    assert observed.data["normal_task_completed"] is True
    assert observed.data["attack_side_effect_observed"] is None
    assert observed.data["action_count"] == 3
    assert events[-1].event_type == "execution_finished"
    assert events[-1].data["restricted_data_exposed"] is False
    assert events[-1].data["attack_side_effect_observed"] is False
    assert all(len(names) == 14 and "submit" in names for names in provider.tool_names)
    assert "read_file" not in provider.tool_names[0]
    assert provider.inputs[1][-1].name == "search_email"
    assert provider.inputs[2][-1].name == "read_email"
    assert provider.inputs[3][-1].name == "create_calendar_event"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("case_id", "different-case"),
        ("scenario_id", "different-scenario"),
        ("prompt", "different prompt"),
        ("max_steps", CLEAN_CASE.budget.max_steps - 1),
        ("timeout_seconds", CLEAN_CASE.budget.timeout_seconds - 1),
        ("seed", CLEAN_CASE.seed + 1),
        ("model", ModelOptions(model_name="different-fake-model")),
    ),
)
async def test_office_request_must_match_the_frozen_test_case(field: str, value) -> None:
    with pytest.raises(OfficeEpisodeRequestError, match=field):
        await _collect(
            TraceReactAdapter(provider=OfficeSequenceProvider()),
            _request(**{field: value}),
        )


async def test_unknown_office_initialization_version_is_not_executed() -> None:
    request = _request()
    initialization = deepcopy(request.scenario_initialization)
    initialization["schema_version"] = "9.0"

    with pytest.raises(OfficeInitializationConfigurationError):
        await _collect(
            TraceReactAdapter(provider=OfficeSequenceProvider()),
            request.model_copy(update={"scenario_initialization": initialization}),
        )
