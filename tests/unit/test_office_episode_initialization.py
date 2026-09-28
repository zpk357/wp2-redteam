from __future__ import annotations

from copy import deepcopy

import pytest

import sandbox.scenarios.office_episode as office_episode
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.injection import materialize_injection
from sandbox.scenarios.office_episode import (
    MAX_OFFICE_EPISODE_INITIALIZATION_BYTES,
    OfficeInitializationConfigurationError,
    OfficeInitializationIntegrityError,
    build_office_episode_initialization,
    dump_office_episode_initialization,
    load_office_episode_initialization,
)
from sandbox.scenarios.office_matrix import OFFICE_V1_TEST_MATRIX
from sandbox.scenarios.office_runtime import OfficeRuntime
from sandbox.scenarios.office_v1 import SECRET_FILE_ID

ALL_OFFICE_CASES = (
    *OFFICE_V1_TEST_MATRIX.clean_cases,
    *OFFICE_V1_TEST_MATRIX.attack_cases,
)


@pytest.mark.parametrize("case", ALL_OFFICE_CASES, ids=lambda case: case.case_id)
def test_all_office_cases_round_trip_to_the_exact_runtime_state(case) -> None:
    first = build_office_episode_initialization(case)
    second = build_office_episode_initialization(case)
    payload = dump_office_episode_initialization(first)
    restored = load_office_episode_initialization(
        payload,
        expected_digest=first.envelope_digest,
    )
    runtime = OfficeRuntime(case)

    assert first == second == restored
    assert payload == dump_office_episode_initialization(second)
    assert restored.initial_state == runtime.initial_state
    assert restored.initial_state_digest == runtime.state_digest()
    if case.attack is None:
        assert restored.materialized_injection is None
    else:
        assert restored.materialized_injection == materialize_injection(case)


@pytest.mark.parametrize(
    "field",
    (
        "kind",
        "schema_version",
        "execution_backend",
        "trace_schema_version",
        "state_codec_version",
    ),
)
def test_missing_contract_discriminator_is_an_integrity_error(field: str) -> None:
    raw = _raw_initialization()
    del raw[field]

    with pytest.raises(OfficeInitializationIntegrityError) as caught:
        load_office_episode_initialization(raw)

    assert caught.value.error_code == "data_integrity_error"


@pytest.mark.parametrize(
    ("field", "unsupported"),
    (
        ("kind", "other-initialization"),
        ("schema_version", "9.0"),
        ("execution_backend", "other_backend"),
        ("trace_schema_version", "9.0"),
        ("state_codec_version", "9.0"),
    ),
)
def test_unknown_contract_version_is_a_configuration_error(
    field: str, unsupported: str
) -> None:
    raw = _raw_initialization()
    raw[field] = unsupported

    with pytest.raises(OfficeInitializationConfigurationError) as caught:
        load_office_episode_initialization(raw)

    assert caught.value.error_code == "configuration_error"


@pytest.mark.parametrize("payload", (b"not-json", b"\xff", "[]", 42))
def test_malformed_payload_is_an_integrity_error(payload) -> None:
    with pytest.raises(OfficeInitializationIntegrityError) as caught:
        load_office_episode_initialization(payload)

    assert caught.value.error_code == "data_integrity_error"


def test_extra_field_is_rejected_as_an_integrity_error() -> None:
    raw = _raw_initialization()
    raw["unexpected"] = True

    with pytest.raises(OfficeInitializationIntegrityError):
        load_office_episode_initialization(raw)


def test_oversized_payload_is_rejected_before_json_parsing() -> None:
    payload = b"{" + b" " * MAX_OFFICE_EPISODE_INITIALIZATION_BYTES

    with pytest.raises(OfficeInitializationIntegrityError, match="maximum serialized size"):
        load_office_episode_initialization(payload)


def test_state_tampering_is_rejected_even_if_the_envelope_is_resealed() -> None:
    raw = _raw_initialization()
    raw["initial_state"]["drive_files"][SECRET_FILE_ID]["content"] = "tampered"
    raw["initial_state_digest"] = sha256_digest(raw["initial_state"])
    raw["envelope_digest"] = sha256_digest(
        {key: value for key, value in raw.items() if key != "envelope_digest"}
    )

    with pytest.raises(OfficeInitializationIntegrityError):
        load_office_episode_initialization(raw)


def test_nested_test_case_tampering_is_rejected() -> None:
    raw = _raw_initialization()
    raw["test_case"]["benign_task"]["instruction"] = "tampered instruction"

    with pytest.raises(OfficeInitializationIntegrityError):
        load_office_episode_initialization(raw)


def test_materialized_injection_tampering_is_rejected() -> None:
    raw = _raw_initialization()
    raw["materialized_injection"]["carrier_id"] = "different-carrier"

    with pytest.raises(OfficeInitializationIntegrityError):
        load_office_episode_initialization(raw)


def test_wrong_detached_digest_is_rejected() -> None:
    payload = dump_office_episode_initialization(
        build_office_episode_initialization(ALL_OFFICE_CASES[0])
    )

    with pytest.raises(OfficeInitializationIntegrityError, match="detached expected digest"):
        load_office_episode_initialization(payload, expected_digest="0" * 64)


def test_mutation_after_construction_is_detected() -> None:
    initialization = build_office_episode_initialization(ALL_OFFICE_CASES[0])
    initialization.initial_state["drive_files"][SECRET_FILE_ID]["content"] = "tampered"

    with pytest.raises(ValueError, match="frozen TestCase materialization"):
        initialization.assert_integrity()


def test_unclassified_materialization_failure_is_not_swallowed(monkeypatch) -> None:
    raw = _raw_initialization()

    def fail_unexpectedly(_case):
        raise RuntimeError("unexpected materializer failure")

    monkeypatch.setattr(office_episode, "materialize_injection", fail_unexpectedly)

    with pytest.raises(RuntimeError, match="unexpected materializer failure"):
        load_office_episode_initialization(raw)


def _raw_initialization() -> dict:
    attacked_case = OFFICE_V1_TEST_MATRIX.attack_cases[0]
    initialization = build_office_episode_initialization(attacked_case)
    return deepcopy(initialization.model_dump(mode="json"))
