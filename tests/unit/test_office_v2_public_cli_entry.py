from __future__ import annotations

import pytest

from sandbox.scenarios.office_v2.cli_entry import (
    IN_CONTAINER_OLLAMA_ENDPOINT,
    OfficeV2PublicEntryError,
    build_office_v2_public_request,
    office_v2_public_case,
    office_v2_public_cases,
)

DIGEST = "sha256:" + "a" * 64


def test_public_catalog_exposes_clean_and_representative_cases() -> None:
    cases = office_v2_public_cases()

    assert len(cases) == 48
    assert sum(item.kind == "clean" for item in cases) == 24
    assert sum(item.kind == "attack" for item in cases) == 24
    assert len({item.public_id for item in cases}) == 48


def test_public_request_uses_v2_envelope_and_in_container_model() -> None:
    selected = office_v2_public_case("clean.t2.delta")
    request = build_office_v2_public_request(
        selected,
        execution_id="scenario-office-v2-clean-t2-delta",
        model_name="qwen3:8b",
        model_digest=DIGEST,
        seed=7,
        max_steps=40,
        timeout_seconds=180,
    )

    assert request.office_v2_execution is not None
    assert request.scenario_initialization is None
    assert request.model is not None
    assert request.model.endpoint == IN_CONTAINER_OLLAMA_ENDPOINT
    assert request.office_v2_execution.model_identity == request.model
    assert request.office_v2_execution.interaction_response_directives == ()


def test_frozen_response_is_explicit_and_comes_from_case_contract() -> None:
    selected = office_v2_public_case("clean.t9.apollo")
    request = build_office_v2_public_request(
        selected,
        execution_id="scenario-office-v2-clean-t9-apollo",
        model_name="qwen3:8b",
        model_digest=DIGEST,
        seed=0,
        max_steps=40,
        timeout_seconds=180,
        use_frozen_response=True,
    )

    directives = request.office_v2_execution.interaction_response_directives
    assert len(directives) == 1
    assert directives[0].authenticated_principal_id == directives[0].responder_id


def test_public_catalog_rejects_unknown_case() -> None:
    with pytest.raises(OfficeV2PublicEntryError, match="unknown Office V2 case"):
        office_v2_public_case("office-v1-attack-001")
