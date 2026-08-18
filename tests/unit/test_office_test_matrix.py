from __future__ import annotations

from collections import defaultdict

import pytest
from pydantic import ValidationError

from sandbox.scenarios.injection import materialize_injection
from sandbox.scenarios.matrix import TestMatrix
from sandbox.scenarios.office_matrix import (
    OFFICE_V1_TEST_MATRIX,
    office_attack_expression,
)
from sandbox.scenarios.office_v1 import EXTERNAL_RESTRICTED_FILE_SHARE


def test_office_matrix_contains_six_clean_and_twelve_attacked_cases() -> None:
    summary = OFFICE_V1_TEST_MATRIX.summary()

    assert summary.clean_case_count == 6
    assert summary.attack_case_count == 12
    assert len(summary.task_template_ids) == 6
    assert len(summary.objective_template_ids) == 6
    assert len(summary.carrier_template_ids) == 3


def test_every_matrix_case_is_frozen_valid_and_attack_cases_materialize() -> None:
    OFFICE_V1_TEST_MATRIX.assert_integrity()
    for case in OFFICE_V1_TEST_MATRIX.clean_cases:
        case.assert_integrity()
        assert case.attack is None
    for case in OFFICE_V1_TEST_MATRIX.attack_cases:
        case.assert_integrity()
        materialized = materialize_injection(case)
        assert materialized.case_id == case.case_id
        assert materialized.payload_digest


def test_matrix_proves_task_objective_carrier_and_expression_replacement() -> None:
    by_task: dict[str, set[str]] = defaultdict(set)
    by_objective: dict[str, set[str]] = defaultdict(set)
    by_triplet: dict[tuple[str, str, str], set[str]] = defaultdict(set)

    for case in OFFICE_V1_TEST_MATRIX.attack_cases:
        attack = case.attack
        by_task[case.benign_task.template_id].add(attack.objective.template_id)
        by_objective[attack.objective.template_id].add(attack.carrier.template_id)
        by_triplet[
            (
                case.benign_task.template_id,
                attack.objective.template_id,
                attack.carrier.template_id,
            )
        ].add(attack.payload)

    assert len(by_task["create-meeting-from-email"]) == 6
    assert by_objective["external-restricted-file-share"] == {
        "calendar-description",
        "drive-file-content",
        "email-body",
    }
    assert len(
        by_triplet[
            (
                "create-meeting-from-email",
                "external-restricted-file-share",
                "email-body",
            )
        ]
    ) == 2


def test_attack_expression_is_deterministic_and_uses_objective_parameters() -> None:
    direct = office_attack_expression(EXTERNAL_RESTRICTED_FILE_SHARE)
    wrapped = office_attack_expression(EXTERNAL_RESTRICTED_FILE_SHARE, "workflow-note")

    assert direct == office_attack_expression(EXTERNAL_RESTRICTED_FILE_SHARE)
    assert EXTERNAL_RESTRICTED_FILE_SHARE.parameters["file_id"] in direct
    assert EXTERNAL_RESTRICTED_FILE_SHARE.parameters["recipient_id"] in direct
    assert direct in wrapped
    assert direct != wrapped


def test_matrix_rejects_role_confusion_and_duplicate_attack_combinations() -> None:
    payload = OFFICE_V1_TEST_MATRIX.model_dump(mode="python", exclude={"content_digest"})
    payload["clean_cases"] = (payload["attack_cases"][0],)
    with pytest.raises(ValidationError, match="clean_cases must not contain attack bindings"):
        TestMatrix.model_validate(payload)

    payload = OFFICE_V1_TEST_MATRIX.model_dump(mode="python", exclude={"content_digest"})
    duplicate = dict(payload["attack_cases"][0])
    duplicate["case_id"] = "office-v1-duplicate-semantic-attack"
    duplicate["content_digest"] = None
    payload["attack_cases"] = (*payload["attack_cases"], duplicate)
    with pytest.raises(ValidationError, match="attack combinations must be unique"):
        TestMatrix.model_validate(payload)


def test_matrix_digest_detects_nested_case_tampering() -> None:
    restored = TestMatrix.model_validate_json(OFFICE_V1_TEST_MATRIX.model_dump_json())
    restored.attack_cases[0].scenario.initial_state["emails"]["email-bob-001"][
        "body"
    ] = "tampered"

    with pytest.raises(ValueError, match="no longer matches content_digest"):
        restored.assert_integrity()
