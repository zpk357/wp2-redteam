from __future__ import annotations

import pytest
from app.adapter.base import AdapterConfigurationError
from app.office_v2_runtime_surface import build_office_v2_runtime_surface

from sandbox.fuzzer.v2_target_oracle import build_formal_target_scenario_supports
from sandbox.scenarios.office_v2.agent_context import (
    TASK_EXECUTION_SUPPORT_METADATA_KEY,
)
from sandbox.scenarios.office_v2.execution_request import (
    build_office_v2_execution_request,
)


def _request(seed_id: str):
    support = next(
        item
        for item in build_formal_target_scenario_supports()
        if item.seed.seed_id == seed_id
    )
    task_support = support.agent_task_execution_support()
    request = build_office_v2_execution_request(
        support.scenario_case,
        initial_state=support.materialization.initial_state,
        initialization_transition=support.materialization.initialization_transition,
        execution_id=f"task-support-{seed_id}",
        model_name="test",
        seed=1,
        max_steps=4,
        timeout_seconds=30,
        metadata={
            TASK_EXECUTION_SUPPORT_METADATA_KEY: task_support.model_dump(
                mode="json", exclude_none=False
            )
        },
    )
    return support, task_support, request


def test_runtime_renders_support_without_changing_the_mutated_task() -> None:
    support, task_support, request = _request("modification.workspace-record")

    _, surface = build_office_v2_runtime_surface(request)

    assert request.prompt == support.seed.base_text
    assert support.scenario_case.task.instruction == support.seed.base_text
    assert "Concrete task references and values:" in surface.system_message
    assert all(item in surface.system_message for item in task_support.summaries)
    assert support.seed.attack_target not in surface.system_message
    assert support.target_oracle.scenario_source_objective_id not in surface.system_message


def test_runtime_rejects_tampered_task_execution_support() -> None:
    _, _, request = _request("destructive.restricted-drive-delete")
    metadata = dict(request.metadata)
    payload = dict(metadata[TASK_EXECUTION_SUPPORT_METADATA_KEY])
    payload["summaries"] = ["target file: drive_file; id=forged.file."]
    metadata[TASK_EXECUTION_SUPPORT_METADATA_KEY] = payload

    with pytest.raises(
        AdapterConfigurationError,
        match="task execution support is invalid",
    ):
        build_office_v2_runtime_surface(request.model_copy(update={"metadata": metadata}))
