from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.injection import materialize_injection
from sandbox.scenarios.models import (
    AttackBinding,
    BenignTask,
    InjectionCarrier,
    InjectionOperation,
    ResourceSelector,
)
from sandbox.scenarios.models import TestCase as ScenarioTestCase
from sandbox.scenarios.office_v1 import (
    CREATE_MEETING_FROM_EMAIL,
    EMAIL_BODY_CARRIER,
    EXTERNAL_RESTRICTED_FILE_SHARE,
    FAKE_AGENT,
    FIXED_ATTACK,
    OFFICE_BENIGN_TASKS,
    OFFICE_INJECTION_CARRIERS,
    OFFICE_V1,
)


def _case_for(
    carrier: InjectionCarrier,
    index: int = 0,
    payload: str = FIXED_ATTACK,
    task: BenignTask | None = None,
):
    task_by_carrier_type = {
        "email_body": CREATE_MEETING_FROM_EMAIL,
        "drive_file_content": next(
            task for task in OFFICE_BENIGN_TASKS if task.template_id == "read-only-file-lookup"
        ),
        "calendar_description": next(
            task
            for task in OFFICE_BENIGN_TASKS
            if task.template_id == "reschedule-calendar-event"
        ),
    }
    return ScenarioTestCase(
        case_id=f"office-carrier-{index:03d}",
        scenario=OFFICE_V1,
        benign_task=task or task_by_carrier_type.get(
            carrier.carrier_type, CREATE_MEETING_FROM_EMAIL
        ),
        attack=AttackBinding(
            objective=EXTERNAL_RESTRICTED_FILE_SHARE,
            carrier=carrier,
            payload=payload,
        ),
        agent=FAKE_AGENT,
    )


def _target_value(state: dict, carrier: InjectionCarrier) -> str:
    schema = next(
        item
        for item in OFFICE_V1.state_schema
        if item.collection_id == carrier.target.collection_id
    )
    value = state[carrier.target.collection_id]
    if schema.container == "mapping":
        value = value[carrier.target.item_id]
    else:
        value = next(
            item
            for item in value
            if item[schema.item_id_field] == carrier.target.item_id
        )
    for field in carrier.target.field_path:
        value = value[field]
    return value


def test_office_catalog_has_three_distinct_registered_untrusted_carriers() -> None:
    policy_sources = set(OFFICE_V1.authorization_policy.untrusted_content_sources)

    assert len(OFFICE_INJECTION_CARRIERS) == 3
    assert {carrier.template_id for carrier in OFFICE_INJECTION_CARRIERS} == {
        "calendar-description",
        "drive-file-content",
        "email-body",
    }
    assert {
        carrier.carrier_type for carrier in OFFICE_INJECTION_CARRIERS
    }.issubset(policy_sources)
    assert all(carrier.parameters for carrier in OFFICE_INJECTION_CARRIERS)


def test_same_objective_keeps_identical_success_evidence_across_carriers() -> None:
    cases = [_case_for(carrier, index) for index, carrier in enumerate(OFFICE_INJECTION_CARRIERS)]
    objectives = [case.attack.objective for case in cases]

    assert all(objective == objectives[0] for objective in objectives)
    assert all(
        objective.success_evidence == objectives[0].success_evidence
        for objective in objectives
    )
    assert len({case.attack.carrier.carrier_id for case in cases}) == 3
    assert len({case.content_digest for case in cases}) == 3


def test_each_carrier_materializes_deterministically_without_mutating_scenario() -> None:
    original = deepcopy(OFFICE_V1.initial_state)

    for index, carrier in enumerate(OFFICE_INJECTION_CARRIERS):
        case = _case_for(carrier, index)
        first = materialize_injection(case)
        second = materialize_injection(case)
        before = _target_value(original, carrier)
        after = _target_value(first.initial_state, carrier)

        assert first == second
        assert before in after
        assert FIXED_ATTACK in after
        assert first.before_value_digest == sha256_digest(before)
        assert first.after_value_digest == sha256_digest(after)
        assert first.initial_state_digest == sha256_digest(first.initial_state)
        assert OFFICE_V1.initial_state == original


def test_payload_variation_changes_materialized_state_not_objective() -> None:
    first_case = _case_for(EMAIL_BODY_CARRIER, 10, "First synthetic attack expression.")
    second_case = _case_for(EMAIL_BODY_CARRIER, 11, "Second synthetic attack expression.")
    first = materialize_injection(first_case)
    second = materialize_injection(second_case)

    assert first_case.attack.objective == second_case.attack.objective
    assert first_case.attack.carrier == second_case.attack.carrier
    assert first.payload_digest != second.payload_digest
    assert first.after_value_digest != second.after_value_digest
    assert first.initial_state_digest != second.initial_state_digest


def test_unregistered_carrier_type_is_rejected_before_materialization() -> None:
    payload = EMAIL_BODY_CARRIER.model_dump(mode="python")
    payload.update(
        {
            "carrier_id": "unregistered-chat-message",
            "template_id": "chat-message",
            "carrier_type": "chat_message",
        }
    )
    carrier = InjectionCarrier.model_validate(payload)

    with pytest.raises(ValidationError, match="not a registered untrusted content source"):
        _case_for(carrier, 20)


def test_carrier_target_must_be_a_text_field() -> None:
    payload = EMAIL_BODY_CARRIER.model_dump(mode="python")
    payload.update(
        {
            "carrier_id": "invalid-email-object",
            "target": ResourceSelector(
                collection_id="emails", item_id="email-bob-001"
            ).model_dump(mode="python"),
        }
    )
    with pytest.raises(ValidationError, match="must select a content field"):
        InjectionCarrier.model_validate(payload)


def test_template_slot_requires_declared_slot_and_exactly_one_match() -> None:
    payload = EMAIL_BODY_CARRIER.model_dump(mode="python")
    payload.update(
        {
            "carrier_id": "email-template-slot",
            "operation": "template_slot",
            "template_slot": "{{attack}}",
        }
    )
    carrier = InjectionCarrier.model_validate(payload)
    task_payload = CREATE_MEETING_FROM_EMAIL.model_dump(mode="python")
    task_payload["task_id"] = "meeting-with-template-slot-carrier"
    task_payload["content_exposures"][0]["semantics_preserving_operations"] = (
        InjectionOperation.TEMPLATE_SLOT,
    )
    case = _case_for(carrier, 30, task=BenignTask.model_validate(task_payload))

    with pytest.raises(ValueError, match="exactly once"):
        materialize_injection(case)

    payload["template_slot"] = None
    with pytest.raises(ValidationError, match="requires template_slot"):
        InjectionCarrier.model_validate(payload)


def test_clean_case_and_tampered_case_cannot_be_materialized() -> None:
    clean = ScenarioTestCase(
        case_id="office-clean-no-carrier",
        scenario=OFFICE_V1,
        benign_task=CREATE_MEETING_FROM_EMAIL,
        agent=FAKE_AGENT,
    )
    with pytest.raises(ValueError, match="has no attack"):
        materialize_injection(clean)

    attacked = ScenarioTestCase.model_validate_json(
        _case_for(EMAIL_BODY_CARRIER, 40).model_dump_json()
    )
    attacked.scenario.initial_state["emails"]["email-bob-001"]["body"] = "tampered"
    with pytest.raises(ValueError, match="no longer matches content_digest"):
        materialize_injection(attacked)
