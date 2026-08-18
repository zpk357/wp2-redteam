from __future__ import annotations

import pytest

from sandbox.fuzzer.v2_identity import build_v2_campaign_identity_lock
from sandbox.fuzzer.v2_mutation_identity import (
    V2MutationComponent,
    V2MutationIdentityError,
    build_v2_mutation_identity_lock,
    require_v2_mutation_identity_lock,
)


def test_mutation_identity_is_complete_deterministic_and_binds_campaign() -> None:
    first = build_v2_mutation_identity_lock()
    second = build_v2_mutation_identity_lock()

    assert first == second
    assert first.campaign_identity_digest == build_v2_campaign_identity_lock().identity_digest
    assert tuple(item.component for item in first.components) == tuple(
        sorted(V2MutationComponent, key=lambda item: item.value)
    )
    assert require_v2_mutation_identity_lock(first) == first
    assert require_v2_mutation_identity_lock(first.model_dump(mode="json")) == first


@pytest.mark.parametrize(
    "field_name",
    (
        "campaign_identity_digest",
        "context_allocation_contract_digest",
        "feedback_operator_policy_digest",
    ),
)
def test_mutation_identity_rejects_upstream_or_policy_drift(field_name: str) -> None:
    identity = build_v2_mutation_identity_lock()
    drifted = identity.model_copy(update={field_name: "sha256:" + "9" * 64})

    with pytest.raises(V2MutationIdentityError, match="validation failed"):
        require_v2_mutation_identity_lock(drifted)


def test_mutation_identity_rejects_missing_component_and_legacy_input() -> None:
    identity = build_v2_mutation_identity_lock()
    payload = identity.model_dump(mode="python", exclude_none=False)
    payload["components"] = payload["components"][:-1]
    with pytest.raises(ValueError, match="at least 6 items"):
        type(identity).model_validate(payload)

    with pytest.raises(V2MutationIdentityError, match="requires a V2 mutation identity"):
        require_v2_mutation_identity_lock(build_v2_campaign_identity_lock())


def test_mutation_identity_rejects_unversioned_mapping() -> None:
    payload = build_v2_mutation_identity_lock().model_dump(mode="json")
    payload.pop("identity_version")

    with pytest.raises(V2MutationIdentityError, match="requires a V2 mutation identity"):
        require_v2_mutation_identity_lock(payload)
