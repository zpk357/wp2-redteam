from __future__ import annotations

import pytest
from app.adapter.base import AdapterConfigurationError
from app.adapter.factory import (
    STAGE7_CONTROL_MODEL_DIGEST,
    STAGE7_CONTROL_MODEL_NAME,
    AdapterFactory,
)
from app.adapter.langgraph_react_runtime import LangGraphReactRuntime
from app.agent.office_v2_stage7_control_provider import OfficeV2Stage7ControlProvider

from sandbox.protocol import OFFICE_V2_SCENARIO_ID, ExecutionRequest, ModelOptions
from sandbox.scenarios.office_v2.attack_cases import (
    RepresentativeScenarioFixture,
    build_representative_scenario_fixtures,
)
from sandbox.scenarios.office_v2.attack_models import MaterializedScenarioCase
from sandbox.scenarios.office_v2.execution_contracts import build_v2_execution_envelope


def _fixture(index: int) -> RepresentativeScenarioFixture:
    return build_representative_scenario_fixtures()[index - 1]


def _request(index: int, mode: str) -> ExecutionRequest:
    fixture = _fixture(index)
    case = fixture.scenario_case
    model = ModelOptions(
        provider="fake",
        model_name=STAGE7_CONTROL_MODEL_NAME,
        model_digest=STAGE7_CONTROL_MODEL_DIGEST,
    )
    envelope = build_v2_execution_envelope(
        case,
        initial_state=fixture.materialization.initial_state,
        initialization_transition=fixture.materialization.initialization_transition,
        model_identity=model,
    )
    return ExecutionRequest(
        execution_id=f"episode.stage7.10.{index:02d}.{mode}",
        case_id=case.case_id,
        scenario_id=OFFICE_V2_SCENARIO_ID,
        prompt=case.task.instruction,
        max_steps=50,
        timeout_seconds=30,
        metadata={"office_v2_stage7_control_mode": mode},
        seed=0,
        model=model,
        office_v2_execution=envelope,
    )


async def _execute(index: int, mode: str):
    runtime = LangGraphReactRuntime(
        provider_factory=OfficeV2Stage7ControlProvider.from_request
    )
    events = [event async for event in runtime.execute(_request(index, mode))]
    assert runtime.last_v2_oracle_artifact is not None
    assert runtime.last_v2_session is not None
    return runtime, events


@pytest.mark.parametrize("index", [10, 7, 14, 9])
async def test_safe_controls_observe_or_receive_input_without_committed_side_effects(
    index: int,
) -> None:
    runtime, _ = await _execute(index, "safe")
    oracle = runtime.last_v2_oracle_artifact.oracle_result
    objective = oracle.security.planned_objectives[0]

    assert objective.completion_kind.value == "none"
    assert runtime.last_v2_session.episode.history == ()
    assert "delivered" in {stage.value for stage in objective.exposure_fact.stages}
    assert len(runtime.last_v2_session.episode.state.delegation_grants) == 0


@pytest.mark.parametrize("index", [10, 7, 14, 9])
async def test_full_controls_use_toolruntime_and_oracle_detects_real_side_effects(
    index: int,
) -> None:
    runtime, _ = await _execute(index, "full")
    oracle = runtime.last_v2_oracle_artifact.oracle_result
    objective = oracle.security.planned_objectives[0]

    assert objective.completion_kind.value == "full"
    assert runtime.last_v2_session.episode.history
    assert len(runtime.last_v2_session.episode.state.delegation_grants) == 0
    assert any(
        result.state_transition is not None and result.state_transition.committed
        for result in runtime.last_v2_session.trusted_tool_results
    )


async def test_compound_partial_and_full_are_distinct_oracle_results() -> None:
    partial, _ = await _execute(8, "partial")
    full, _ = await _execute(8, "full")

    partial_objective = (
        partial.last_v2_oracle_artifact.oracle_result.security.planned_objectives[0]
    )
    full_objective = full.last_v2_oracle_artifact.oracle_result.security.planned_objectives[0]
    assert partial_objective.completion_kind.value == "partial"
    assert full_objective.completion_kind.value == "full"
    assert len(partial_objective.realized_milestone_ids) < len(
        full_objective.realized_milestone_ids
    )


def test_control_mode_and_model_identity_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(AdapterConfigurationError, match="safe, full, or partial"):
        OfficeV2Stage7ControlProvider.from_request(_request(7, "safe").model_copy(
            update={"metadata": {}}
        ))

    monkeypatch.setenv("TRACE_G_FORMAL_AGENT", "1")
    monkeypatch.setenv("TRACE_G_STAGE7_DETERMINISTIC_PROVIDER", "1")
    assert isinstance(AdapterFactory().create(_request(7, "safe")), LangGraphReactRuntime)
    wrong = _request(7, "safe").model_copy(
        update={
            "model": _request(7, "safe").model.model_copy(
                update={"model_digest": "sha256:" + "b" * 64}
            )
        }
    )
    with pytest.raises(AdapterConfigurationError, match="Stage 7.10 control identity"):
        AdapterFactory().create(wrong)


@pytest.mark.parametrize("index", [23, 24])
def test_temporal_parameter_cases_survive_the_host_container_json_boundary(
    index: int,
) -> None:
    case = _fixture(index).scenario_case
    restored = MaterializedScenarioCase.model_validate(
        case.model_dump(mode="json", exclude_none=False)
    )

    assert restored == case
    assert restored.content_digest == case.content_digest
