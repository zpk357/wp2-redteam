from __future__ import annotations

import pytest
from app.agent.react_contract import ReactMessage
from app.replay.state_codec import StateCodec
from app.tools.base import ToolRegistry

from sandbox.replay.models import CheckpointKind, ResumePhase
from sandbox.scenarios.models import resolve_state_value
from sandbox.scenarios.office_controls import OfficeVulnerableControl
from sandbox.scenarios.office_episode import (
    OfficeToolRuntimeState,
    build_office_episode_initialization,
)
from sandbox.scenarios.office_fork import (
    OfficeCarrierForkError,
    replace_office_carrier_payload,
)
from sandbox.scenarios.office_matrix import OFFICE_V1_TEST_MATRIX
from sandbox.scenarios.office_runtime import CAPABILITY_TOOL_NAMES


def _case(case_id: str):
    return next(item for item in OFFICE_V1_TEST_MATRIX.attack_cases if item.case_id == case_id)


def _checkpoint_after_actions(case_id: str, action_count: int):
    case = _case(case_id)
    initialization = build_office_episode_initialization(case)
    tools = ToolRegistry()
    tools.enable_office_episode(initialization.model_dump(mode="json"))
    control = OfficeVulnerableControl().run(case)
    assert control.passed
    messages = [ReactMessage(role="user", content=case.benign_task.instruction)]
    for index, action in enumerate(control.actions[:action_count], start=1):
        tool = CAPABILITY_TOOL_NAMES[action.capability_id]
        result = tools.execute({"name": tool, "arguments": action.arguments}).to_dict()
        messages.extend(
            (
                ReactMessage(role="assistant", content=f"Calling {tool}."),
                ReactMessage(
                    role="tool",
                    name=tool,
                    call_id=f"call-{index}",
                    content=result,
                ),
            )
        )
    state = {
        "prompt": case.benign_task.instruction,
        "step_count": action_count,
        "max_steps": case.budget.max_steps,
        "final_answer": None,
        "messages": [message.model_dump(mode="json") for message in messages],
        "seen_call_ids": [f"call-{index}" for index in range(1, action_count + 1)],
        "submitted": False,
        "turn": action_count,
    }
    return case, StateCodec().export(
        state,
        tools,
        checkpoint_kind=CheckpointKind.AFTER_TOOL,
        resume_phase=ResumePhase.APPLY_TOOL_RESULT,
        logical_time=action_count,
        next_model_decision_index=action_count,
        next_tool_interaction_index=action_count,
    )


@pytest.mark.parametrize(
    ("case_id", "prefix_action_count"),
    [
        ("office-v1-attack-01", 1),
        ("office-v1-attack-10", 1),
        ("office-v1-attack-11", 0),
    ],
)
def test_carrier_payload_replacement_preserves_case_and_prefix(
    case_id: str,
    prefix_action_count: int,
) -> None:
    parent, checkpoint = _checkpoint_after_actions(case_id, prefix_action_count)
    assert parent.attack is not None
    replacement = (
        f"Alternate synthetic instruction for objective "
        f"{parent.attack.objective.objective_id}."
    )

    result = replace_office_carrier_payload(checkpoint, replacement)
    child = result.initialization.test_case
    saved = OfficeToolRuntimeState.model_validate(
        result.checkpoint_state.enterprise_tool_state["office_episode"]
    )
    exists, target_value = resolve_state_value(
        child.scenario,
        result.initialization.initial_state,
        child.attack.carrier.target,
    )

    assert exists is True
    assert replacement in target_value
    assert parent.attack.payload not in target_value
    assert child.parent_case_id == parent.case_id
    assert child.benign_task == parent.benign_task
    assert child.attack.objective == parent.attack.objective
    assert child.attack.carrier == parent.attack.carrier
    assert child.agent == parent.agent
    assert child.budget == parent.budget
    assert child.seed == parent.seed
    assert len(saved.actions) == prefix_action_count
    assert result.parent_payload_digest != result.replacement_payload_digest


def test_carrier_payload_replacement_rejects_checkpoint_after_exposure() -> None:
    parent, checkpoint = _checkpoint_after_actions("office-v1-attack-01", 2)
    assert parent.attack is not None

    with pytest.raises(OfficeCarrierForkError, match="already exposed"):
        replace_office_carrier_payload(
            checkpoint,
            f"Alternate synthetic instruction for {parent.attack.objective.objective_id}.",
        )


def test_carrier_payload_replacement_rejects_noop() -> None:
    parent, checkpoint = _checkpoint_after_actions("office-v1-attack-01", 1)
    assert parent.attack is not None

    with pytest.raises(OfficeCarrierForkError, match="must change"):
        replace_office_carrier_payload(checkpoint, parent.attack.payload)
