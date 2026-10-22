"""The host's container request: what travels, and what is refused before it travels.

These are the checks that must hold *before* a container is asked to do anything.  A
request that reaches an image with the wrong payload shape, the wrong frozen identity or
a plan that does not describe the Episode it claims would run something other than the
Opportunity it is recorded as running.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sandbox.protocol import ExecutionRequest, ModelOptions
from sandbox.scenarios.error_capable import (
    ATTACK_SPECS,
    TASK_FAMILY_SPECS,
    ErrorCapableMode,
    SelectorDecision,
    SelectorRequest,
    TaskFamily,
    build_plan,
    materialize_scenario,
)
from sandbox.scenarios.error_capable_agent import DISCOVERY_TASK
from sandbox.scenarios.error_capable_identity import ModelIdentity
from sandbox.scenarios.error_capable_registry import (
    ERROR_CAPABLE_FIXTURE_ID,
    load_error_capable_fixture,
)
from sandbox.scenarios.error_capable_submission import (
    ERROR_CAPABLE_EXECUTION_VERSION,
    build_error_capable_request,
)
from sandbox.scenarios.error_capable_world import carrier_ids, planned_file_ids

EPISODE_ID = "campaign.guided.007"
SEED = 4242
MODEL_NAME = "probe-model"
ADAPTER_VERSION = "probe-adapter-v1"

#: Building one cell needs no coverage snapshot, and `build_plan` applies the same menu
#: validation on either arm.  The mode only decides what the payload records.
MODE = ErrorCapableMode.RANDOM
FAMILIES = tuple(spec.task_family for spec in TASK_FAMILY_SPECS)
ATTACKS = tuple(spec.attack_mode for spec in ATTACK_SPECS)
PATHS = tuple(path for spec in TASK_FAMILY_SPECS for path in spec.path_ids)
LAYOUT = "balanced-9"


def _fixture():
    return load_error_capable_fixture(ERROR_CAPABLE_FIXTURE_ID)


def _identity() -> ModelIdentity:
    return ModelIdentity.capture(
        provider_id="local",
        raw_model_label=MODEL_NAME,
        provider_version=ADAPTER_VERSION,
    )


def _plan_and_material(*, layout_id: str = LAYOUT, episode_id: str = EPISODE_ID):
    request = SelectorRequest(
        mode=MODE,
        seed=SEED,
        agent_model_name=MODEL_NAME,
        available_task_families=FAMILIES,
        available_paths=PATHS,
        available_attacks=ATTACKS,
    )
    decision = SelectorDecision(
        task_family=TaskFamily.SUMMARY_DELIVERY,
        path_id="summary.public-index-email",
        attack_mode=ATTACKS[0],
        attack_carrier=carrier_ids()[0],
        layout_id=layout_id,
        rationale="submission test holds every axis still but the layout",
    )
    file_ids = planned_file_ids(episode_id, seed=SEED, layout_id=layout_id)
    plan = build_plan(
        request,
        decision,
        episode_id=episode_id,
        file_ids=file_ids,
        attack_carrier=decision.attack_carrier,
        model_name=MODEL_NAME,
        layout_id=layout_id,
        task_file_id=file_ids[0],
    )
    return plan, materialize_scenario(plan)


def _request(**overrides):
    plan, material = _plan_and_material()
    kwargs = {
        "execution_id": EPISODE_ID,
        "index": 7,
        "mode": MODE.value,
        "plan": plan,
        "material": material,
        "fixture": _fixture(),
        "model": ModelOptions(),
        "model_identity": _identity(),
        "adapter_version": ADAPTER_VERSION,
        "timeout_seconds": 600,
        "max_tool_requests": 24,
        # Required, not defaulted: a payload that does not say the budget is a payload the
        # container must refuse, because the alternative is running whatever number the image
        # happens to carry.
        "max_continuations": 3,
        "journal_root": "/opt/run/episodes",
        "resume": False,
    }
    kwargs.update(overrides)
    return build_error_capable_request(**kwargs), plan, material


def test_payload_carries_the_plan_and_the_digests_that_let_a_container_refuse_it() -> None:
    request, plan, material = _request()
    payload = request.error_capable_execution

    assert payload is not None
    assert payload["version"] == ERROR_CAPABLE_EXECUTION_VERSION
    assert payload["plan"] == plan.model_dump(mode="json")
    assert payload["materialization_digest"] == material.materialization_digest
    assert payload["fixture_freeze_digest"] == _fixture().freeze_digest
    assert payload["index"] == 7
    # The seed is carried once, inside the digested plan; a second copy could disagree.
    assert "seed" not in payload
    assert request.seed == plan.seed


def test_the_world_itself_does_not_travel() -> None:
    """The fixture and the material are recomputed in the container, not shipped."""

    request, _, material = _request()
    payload = request.error_capable_execution

    assert payload is not None
    assert "material" not in payload
    assert "fixture" not in payload
    assert "files" not in payload
    # The plan's file bodies are the material's; the plan only names them.
    assert material.files
    assert payload["plan"]["file_paths"]


def test_the_agent_still_has_to_find_the_work_request() -> None:
    """The prompt names no file, no recipient and no fact -- it travels unchanged."""

    request, _, _ = _request()

    assert request.prompt == DISCOVERY_TASK
    assert request.execution_id == EPISODE_ID
    assert request.max_tool_calls == 24


def test_a_payload_that_claims_another_episode_is_refused() -> None:
    plan, material = _plan_and_material()
    with pytest.raises(ValidationError, match="execution id does not match plan"):
        _request(plan=plan, material=material, execution_id="campaign.guided.000")


def test_a_missing_digest_is_refused() -> None:
    from sandbox.protocol import ExecutionRequest as Request

    plan, _ = _plan_and_material()
    with pytest.raises(ValidationError, match="materialization_digest"):
        Request(
            execution_id=EPISODE_ID,
            case_id=EPISODE_ID,
            prompt=DISCOVERY_TASK,
            model=ModelOptions(),
            error_capable_execution={
                "plan": plan.model_dump(mode="json"),
                "fixture_freeze_digest": "sha256:" + "a" * 64,
                "plan_digest": "sha256:" + "b" * 64,
            },
        )


def test_it_cannot_be_combined_with_another_envelope() -> None:
    request, _, _ = _request()
    payload = dict(request.error_capable_execution or {})

    with pytest.raises(ValidationError, match="cannot use another scenario envelope"):
        ExecutionRequest(
            execution_id=EPISODE_ID,
            case_id=EPISODE_ID,
            prompt=DISCOVERY_TASK,
            model=ModelOptions(),
            scenario_initialization={"anything": True},
            error_capable_execution=payload,
        )


def test_the_request_requires_model_options() -> None:
    plan, material = _plan_and_material()
    with pytest.raises(ValidationError, match="requires model options"):
        _request(plan=plan, material=material, model=None)


def test_a_historical_fixture_identity_cannot_carry_new_material() -> None:
    plan, material = _plan_and_material()
    legacy = load_error_capable_fixture("error-capable-multipath-01")

    with pytest.raises(ValueError, match="historical fixture identity"):
        _request(plan=plan, material=material, fixture=legacy)
