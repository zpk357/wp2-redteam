from __future__ import annotations

import pytest

from sandbox.coverage.models import CoverageSnapshot
from sandbox.fuzzer.v2_identity import (
    V2CampaignIdentityLock,
    V2FuzzerComponent,
    V2FuzzerIdentityError,
    build_v2_campaign_identity_lock,
    require_v2_campaign_identity_lock,
)


def test_v2_campaign_identity_lock_is_complete_and_deterministic() -> None:
    first = build_v2_campaign_identity_lock()
    second = build_v2_campaign_identity_lock()

    assert first == second
    assert first.identity_digest == second.identity_digest
    assert tuple(item.component for item in first.components) == tuple(
        sorted(V2FuzzerComponent, key=lambda item: item.value)
    )
    assert first.scheduler_policy_version == "office-v2-scheduler-policy-v1"
    assert require_v2_campaign_identity_lock(first) == first
    assert require_v2_campaign_identity_lock(first.model_dump(mode="json")) == first


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    (
        ("world_digest", "sha256:" + "1" * 64),
        ("task_catalog_digest", "sha256:" + "2" * 64),
        ("objective_catalog_digest", "sha256:" + "3" * 64),
        ("risk_catalog_digest", "sha256:" + "4" * 64),
        ("coverage_identity_digest", "sha256:" + "5" * 64),
        ("scheduler_policy_digest", "sha256:" + "6" * 64),
    ),
)
def test_v2_campaign_identity_lock_rejects_upstream_drift(
    field_name: str,
    replacement: str,
) -> None:
    identity = build_v2_campaign_identity_lock()
    drifted = identity.model_copy(update={field_name: replacement})

    with pytest.raises(V2FuzzerIdentityError, match="validation failed"):
        require_v2_campaign_identity_lock(drifted)


def test_v2_campaign_identity_lock_rejects_missing_component() -> None:
    identity = build_v2_campaign_identity_lock()
    payload = identity.model_dump(mode="python", exclude_none=False)
    payload["components"] = payload["components"][:-1]

    with pytest.raises(ValueError, match="at least 6 items"):
        V2CampaignIdentityLock.model_validate(payload)


def test_v2_campaign_identity_lock_rejects_component_drift() -> None:
    identity = build_v2_campaign_identity_lock()
    components = list(identity.components)
    components[0] = components[0].model_copy(
        update={"content_digest": "sha256:" + "7" * 64}
    )
    drifted = identity.model_copy(update={"components": tuple(components)})

    with pytest.raises(V2FuzzerIdentityError, match="validation failed"):
        require_v2_campaign_identity_lock(drifted)


def test_v2_campaign_identity_lock_rejects_legacy_coverage_object() -> None:
    legacy = CoverageSnapshot(
        campaign_id="legacy-campaign",
        taxonomy_version="legacy-taxonomy",
        taxonomy_digest="sha256:" + "8" * 64,
        risk_scope_version="legacy-scope",
    )

    with pytest.raises(V2FuzzerIdentityError, match="requires a V2 identity lock"):
        require_v2_campaign_identity_lock(legacy)


def test_v2_campaign_identity_lock_rejects_unversioned_mapping() -> None:
    identity = build_v2_campaign_identity_lock().model_dump(mode="json")
    identity.pop("identity_version")

    with pytest.raises(V2FuzzerIdentityError, match="requires a V2 identity lock"):
        require_v2_campaign_identity_lock(identity)
