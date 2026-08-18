from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from sandbox.protocol import (
    OFFICE_V2_SCENARIO_ID,
    ExecutionRequest,
    ModelOptions,
    V2ExecutionEnvelope,
)
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.attack_cases import build_representative_scenario_fixtures
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASE_BY_ID
from sandbox.scenarios.office_v2.execution_contracts import build_v2_execution_envelope
from sandbox.scenarios.office_v2.interaction_session import ScriptedResponseDirective


def _model() -> ModelOptions:
    return ModelOptions(provider="fake", model_name="stage7-scripted")


def _clean_envelope() -> V2ExecutionEnvelope:
    world = load_canonical_world()
    return build_v2_execution_envelope(
        CLEAN_CASE_BY_ID["clean.t1.apollo"],
        initial_state=world.state,
        model_identity=_model(),
    )


def _request(envelope: V2ExecutionEnvelope) -> ExecutionRequest:
    return ExecutionRequest(
        execution_id="exec-office-v2-contract",
        case_id=envelope.scenario_case_id,
        prompt=envelope.scenario_case_payload["task"]["instruction"],
        scenario_id=OFFICE_V2_SCENARIO_ID,
        model=envelope.model_identity,
        office_v2_execution=envelope,
    )


def test_clean_envelope_and_request_round_trip_without_legacy_initialization() -> None:
    envelope = _clean_envelope()
    request = _request(envelope)

    restored = ExecutionRequest.model_validate(request.model_dump(mode="json"))

    assert restored == request
    assert restored.scenario_initialization is None
    assert restored.office_v2_execution is not None
    assert restored.office_v2_execution.canonical_digest() == envelope.canonical_digest()


def test_attack_envelope_freezes_materialization_transition_and_initial_state() -> None:
    fixture = next(
        item
        for item in build_representative_scenario_fixtures()
        if item.materialization.initialization_transition is not None
    )
    envelope = build_v2_execution_envelope(
        fixture.scenario_case,
        initial_state=fixture.materialization.initial_state,
        initialization_transition=fixture.materialization.initialization_transition,
        model_identity=_model(),
    )

    restored = V2ExecutionEnvelope.model_validate(envelope.model_dump(mode="json"))

    assert restored.scenario_case_kind == "attack"
    assert restored.initial_state_digest == fixture.scenario_case.initial_world_digest
    assert restored.initialization_transition_payload is not None


@pytest.mark.parametrize(
    ("field", "replacement", "error_code"),
    [
        ("base_world_digest", "sha256:" + "1" * 64, "v2_data_integrity_error"),
        ("tool_catalog_digest", "sha256:" + "2" * 64, "v2_data_integrity_error"),
        ("objective_catalog_digest", "sha256:" + "3" * 64, "v2_data_integrity_error"),
    ],
)
def test_frozen_catalog_identity_drift_is_rejected(
    field: str, replacement: str, error_code: str
) -> None:
    payload = _clean_envelope().model_dump(mode="json")
    payload[field] = replacement

    with pytest.raises(ValidationError, match=error_code):
        V2ExecutionEnvelope.model_validate(payload)


def test_case_and_initial_state_tampering_are_rejected() -> None:
    case_payload = _clean_envelope().model_dump(mode="json")
    case_payload["scenario_case_payload"]["task"]["instruction"] += " changed"
    with pytest.raises(ValidationError, match="task digest mismatch"):
        V2ExecutionEnvelope.model_validate(case_payload)

    state_payload = _clean_envelope().model_dump(mode="json")
    state_payload["initial_state_payload"]["world_version"] = "tampered-world"
    with pytest.raises(ValidationError, match="initial state digest mismatch"):
        V2ExecutionEnvelope.model_validate(state_payload)


def test_request_rejects_prompt_model_and_dual_initialization_drift() -> None:
    envelope = _clean_envelope()
    base = _request(envelope).model_dump(mode="json")

    prompt_drift = deepcopy(base)
    prompt_drift["prompt"] += " changed"
    with pytest.raises(ValidationError, match="prompt does not match frozen task"):
        ExecutionRequest.model_validate(prompt_drift)

    model_drift = deepcopy(base)
    model_drift["model"]["model_name"] = "different"
    with pytest.raises(ValidationError, match="request model options differ"):
        ExecutionRequest.model_validate(model_drift)

    dual_state = deepcopy(base)
    dual_state["scenario_initialization"] = {"kind": "legacy"}
    with pytest.raises(ValidationError, match="cannot use legacy scenario initialization"):
        ExecutionRequest.model_validate(dual_state)


def test_unknown_envelope_fields_and_missing_envelope_are_rejected() -> None:
    payload = _clean_envelope().model_dump(mode="json")
    payload["verdict"] = "safe"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        V2ExecutionEnvelope.model_validate(payload)

    with pytest.raises(ValidationError, match="requires an execution envelope"):
        ExecutionRequest(
            execution_id="exec-office-v2-missing",
            case_id="clean.t1.apollo",
            prompt="task",
            scenario_id=OFFICE_V2_SCENARIO_ID,
        )


def _directive(*, rule_id: str | None = None, turn_id: str = "turn.stage7.5"):
    contract = CLEAN_CASE_BY_ID["clean.t1.apollo"].task.user_response_script
    request = contract.requests[0]
    rule = contract.response_rules[0]
    return ScriptedResponseDirective(
        request_id=request.request_id,
        rule_id=rule_id or rule.rule_id,
        turn_id=turn_id,
        responder_id=rule.authenticated_responder_id,
        authenticated_principal_id=rule.authenticated_responder_id,
    )


def test_response_directives_round_trip_and_tampering_is_rejected() -> None:
    world = load_canonical_world()
    envelope = build_v2_execution_envelope(
        CLEAN_CASE_BY_ID["clean.t1.apollo"],
        initial_state=world.state,
        model_identity=_model(),
        response_directives=(_directive(),),
    )
    assert V2ExecutionEnvelope.model_validate(envelope.model_dump(mode="json")) == envelope

    payload = envelope.model_dump(mode="json")
    payload["interaction_response_directives"][0]["responder_id"] = "user.hana.sato"
    with pytest.raises(ValidationError, match="response directive digest mismatch"):
        V2ExecutionEnvelope.model_validate(payload)


def test_response_directives_cannot_escape_or_repeat_frozen_request() -> None:
    world = load_canonical_world()
    with pytest.raises(ValidationError, match="outside frozen contract"):
        build_v2_execution_envelope(
            CLEAN_CASE_BY_ID["clean.t1.apollo"],
            initial_state=world.state,
            model_identity=_model(),
            response_directives=(_directive(rule_id="rule.outside"),),
        )

    first = _directive(turn_id="turn.stage7.5.first")
    second = _directive(turn_id="turn.stage7.5.second")
    payload = build_v2_execution_envelope(
        CLEAN_CASE_BY_ID["clean.t1.apollo"],
        initial_state=world.state,
        model_identity=_model(),
    ).model_dump(mode="json")
    directives = tuple(
        item.model_dump(mode="json", exclude_none=False) for item in (first, second)
    )
    payload["interaction_response_directives"] = directives
    payload["interaction_response_directives_digest"] = sha256_digest(directives)
    with pytest.raises(ValidationError, match="repeat a request"):
        V2ExecutionEnvelope.model_validate(payload)
