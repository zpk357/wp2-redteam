from __future__ import annotations

import pytest
from app.adapter.base import AdapterConfigurationError
from app.adapter.trace_react_adapter import TraceReactAdapter
from app.agent.office_control_provider import (
    OfficeControlProvider,
    OfficeControlProviderError,
)
from app.agent.react_contract import SUBMIT_TOOL_SPEC, ReactMessage
from app.protocol import ExecutionRequest

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_controls import OfficeSafeControl, OfficeVulnerableControl
from sandbox.scenarios.office_episode import build_office_episode_initialization
from sandbox.scenarios.office_matrix import OFFICE_V1_TEST_MATRIX
from sandbox.scenarios.office_runtime import CAPABILITY_TOOL_NAMES
from sandbox.tool_contracts import OFFICE_SCENARIO_TOOL_SPECS

REPRESENTATIVE_CASE_IDS = {
    "office-v1-attack-01",
    "office-v1-attack-10",
    "office-v1-attack-11",
}
REPRESENTATIVE_CASES = tuple(
    case
    for case in OFFICE_V1_TEST_MATRIX.attack_cases
    if case.case_id in REPRESENTATIVE_CASE_IDS
)


def _request(case, control: str) -> ExecutionRequest:
    initialization = build_office_episode_initialization(case)
    return ExecutionRequest(
        execution_id=f"office-control-{case.case_id}-{control}",
        case_id=case.case_id,
        scenario_id=case.scenario.template_id,
        prompt=case.benign_task.instruction,
        max_steps=case.budget.max_steps,
        timeout_seconds=case.budget.timeout_seconds,
        seed=case.seed,
        scenario_initialization=initialization.model_dump(mode="json"),
        metadata={"scenario_control": control},
    )


@pytest.mark.parametrize("case", REPRESENTATIVE_CASES, ids=lambda case: case.case_id)
@pytest.mark.parametrize("control", ("safe", "vulnerable"))
async def test_office_control_runs_full_trace_react_episode(case, control: str) -> None:
    reference = (
        OfficeSafeControl().run(case)
        if control == "safe"
        else OfficeVulnerableControl().run(case)
    )

    events = [event async for event in TraceReactAdapter().execute(_request(case, control))]

    tool_calls = [event for event in events if event.event_type == "tool_call"]
    tool_results = [event for event in events if event.event_type == "tool_result"]
    model_starts = [event for event in events if event.event_type == "model_start"]
    observation = next(
        event for event in events if event.event_type == "scenario_state_observed"
    )
    assert [event.data["name"] for event in tool_calls] == [
        CAPABILITY_TOOL_NAMES[action.capability_id] for action in reference.actions
    ]
    assert [event.data["authorized"] for event in tool_results] == [
        action.authorized for action in reference.actions
    ]
    for index, tool_result in enumerate(tool_results, start=1):
        assert model_starts[index].data["prior_tool_result_digest"] == (
            tool_result.output_digest
        )
    assert observation.data["normal_task_completed"] is True
    assert observation.data["attack_side_effect_observed"] is (control == "vulnerable")
    assert observation.data["final_state_digest"] == reference.final_state_digest
    assert sum(event.event_type == "agent_submit" for event in events) == 1
    assert events[-1].event_type == "execution_finished"


async def test_office_control_rejects_a_tool_result_that_did_not_really_match() -> None:
    case = REPRESENTATIVE_CASES[0]
    provider = OfficeControlProvider("safe", case)
    tools = (*OFFICE_SCENARIO_TOOL_SPECS, SUBMIT_TOOL_SPEC)
    first = await provider.generate((), tools, seed=case.seed)
    call = first.tool_calls[0]
    messages = (
        ReactMessage(role="assistant", tool_calls=[call]),
        ReactMessage(
            role="tool",
            call_id="call-1",
            name=call.name,
            content={"allowed": True, "outcome": "succeeded", "output": []},
        ),
    )

    with pytest.raises(OfficeControlProviderError, match="conflicts"):
        await provider.generate(messages, tools, seed=case.seed)


async def test_fake_office_episode_requires_an_explicit_control() -> None:
    case = REPRESENTATIVE_CASES[0]
    request = _request(case, "safe").model_copy(update={"metadata": {}})

    with pytest.raises(AdapterConfigurationError) as raised:
        _ = [event async for event in TraceReactAdapter().execute(request)]

    assert raised.value.error_code == "trace_office_configuration_error"


def test_representative_case_selection_is_complete_and_stable() -> None:
    assert {case.case_id for case in REPRESENTATIVE_CASES} == REPRESENTATIVE_CASE_IDS
    assert len({sha256_digest(case) for case in REPRESENTATIVE_CASES}) == 3
