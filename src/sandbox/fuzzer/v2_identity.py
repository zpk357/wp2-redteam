"""Digest-locked identities for the Office V2 corpus and scheduler boundary."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, ValidationError, field_validator, model_validator

from sandbox.coverage.v2_contracts import (
    V2_COVERAGE_CONTRACT_IDENTITY,
    V2_COVERAGE_CONTRACT_VERSION,
)
from sandbox.coverage.v2_risk_catalog import V2_RISK_CATALOG
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2 import (
    OFFICE_V2_ATTACK_OBJECTIVE_CATALOG_VERSION,
    OFFICE_V2_CANONICAL_WORLD_ID,
    OFFICE_V2_CONTRACT_SCHEMA_VERSION,
    OFFICE_V2_TASK_CATALOG_VERSION,
)
from sandbox.scenarios.office_v2.attack_objectives import ATTACK_OBJECTIVE_CATALOG
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASE_CATALOG
from sandbox.scenarios.office_v2.models import (
    Identifier,
    OfficeV2Contract,
    Sha256Digest,
    WorldVersion,
)
from sandbox.scenarios.office_v2.task_catalog import TASK_BLUEPRINT_CATALOG_DIGEST

V2_FUZZER_IDENTITY_VERSION = "office-v2-fuzzer-identity-v1"
V2_SCHEDULER_POLICY_VERSION = "office-v2-scheduler-policy-v1"


class V2FuzzerIdentityError(ValueError):
    """A value cannot safely create Office V2 campaign state."""


class V2FuzzerComponent(StrEnum):
    CORPUS = "corpus"
    RISK_FRONTIER = "risk-frontier"
    BEHAVIOR_FRONTIER = "behavior-frontier"
    SCHEDULER = "scheduler"
    CAMPAIGN_STORE = "campaign-store"
    MUTATION_CAPABILITY = "mutation-capability"


_COMPONENT_VERSIONS = {
    component: f"office-v2-{component.value}-v1" for component in V2FuzzerComponent
}

_COMPONENT_SEMANTICS: dict[V2FuzzerComponent, object] = {
    V2FuzzerComponent.CORPUS: {
        "physical_store": "single",
        "views": ["behavior", "carrier", "compatibility", "lineage", "risk"],
        "seed_execution_separation": "required",
        "v1_inputs": "forbidden",
    },
    V2FuzzerComponent.RISK_FRONTIER: {
        "key": ["scenario", "scheduling-family", "objective", "milestone"],
        "facts": "monotonic",
        "outcomes": ["attempted-seen", "blocked-seen", "realized-seen"],
    },
    V2FuzzerComponent.BEHAVIOR_FRONTIER: {
        "key": [
            "scenario",
            "gap-kind",
            "feature-family",
            "behavior-anchor-digest",
            "gap-descriptor-digest",
        ],
        "instance_ids_and_text": "excluded-from-anchor",
    },
    V2FuzzerComponent.SCHEDULER: {
        "hard_order": [
            "baseline-debt",
            "starvation",
            "exploration-reserve",
            "max-consecutive-share",
            "soft-ranking",
        ],
        "candidate_count": 1,
        "parent_selection": "corpus-entry+seed+supporting-execution",
    },
    V2FuzzerComponent.CAMPAIGN_STORE: {
        "attempt_receipts": "immutable",
        "coverage_commit": "idempotent-after-valid-execution",
        "ambiguous_attempt": "pause-no-automatic-retry",
        "unknown_error": "pause",
    },
    V2FuzzerComponent.MUTATION_CAPABILITY: {
        "scope": "schema-only-until-step-4",
        "missing_operator": "awaiting-operator",
        "unreachable_requires": "world-tool-permission-or-compatibility-proof",
    },
}

_SCHEDULER_POLICY_SEMANTICS = {
    "candidate_count": 1,
    "feedback_visibility": "next-generation-after-commit",
    "frontier_states": [
        "active",
        "awaiting-operator",
        "awaiting-parent",
        "cooling",
        "local-budget-exhausted",
        "locally-saturated",
        "ready",
        "unreachable",
    ],
    "local_budget_exhausted_counts_as_saturated": False,
    "retry": "explicit-transient-bounded-only",
}
V2_SCHEDULER_POLICY_DIGEST = sha256_digest(_SCHEDULER_POLICY_SEMANTICS)


class V2FuzzerComponentIdentity(OfficeV2Contract):
    component: V2FuzzerComponent
    version: Identifier
    content_digest: Sha256Digest

    @model_validator(mode="after")
    def matches_frozen_semantics(self) -> Self:
        if self.version != _COMPONENT_VERSIONS[self.component]:
            raise ValueError("Office V2 fuzzer component version does not match")
        if self.content_digest != sha256_digest(_COMPONENT_SEMANTICS[self.component]):
            raise ValueError("Office V2 fuzzer component digest does not match")
        return self


def _component_identities() -> tuple[V2FuzzerComponentIdentity, ...]:
    return tuple(
        V2FuzzerComponentIdentity(
            component=component,
            version=_COMPONENT_VERSIONS[component],
            content_digest=sha256_digest(_COMPONENT_SEMANTICS[component]),
        )
        for component in sorted(V2FuzzerComponent, key=lambda item: item.value)
    )


class V2CampaignIdentityLock(OfficeV2Contract):
    identity_version: Literal["office-v2-fuzzer-identity-v1"] = V2_FUZZER_IDENTITY_VERSION
    scenario_schema_version: Literal["office-v2.0"] = OFFICE_V2_CONTRACT_SCHEMA_VERSION
    world_id: Literal["office-world-v2.0"] = OFFICE_V2_CANONICAL_WORLD_ID
    world_version: WorldVersion
    world_digest: Sha256Digest
    task_catalog_version: Literal["office-v2-task-catalog-v1"] = (
        OFFICE_V2_TASK_CATALOG_VERSION
    )
    task_catalog_digest: Sha256Digest
    clean_case_catalog_digest: Sha256Digest
    objective_catalog_version: Literal["office-v2-attack-objectives-v1.1"] = (
        OFFICE_V2_ATTACK_OBJECTIVE_CATALOG_VERSION
    )
    objective_catalog_digest: Sha256Digest
    risk_catalog_digest: Sha256Digest
    coverage_contract_version: Literal["office-v2-coverage-contract-v1"] = (
        V2_COVERAGE_CONTRACT_VERSION
    )
    coverage_identity_digest: Sha256Digest
    scheduler_policy_version: Literal["office-v2-scheduler-policy-v1"] = (
        V2_SCHEDULER_POLICY_VERSION
    )
    scheduler_policy_digest: Sha256Digest
    components: tuple[V2FuzzerComponentIdentity, ...] = Field(min_length=6, max_length=6)
    identity_digest: Sha256Digest

    @field_validator("components")
    @classmethod
    def components_are_complete_and_canonical(
        cls, value: tuple[V2FuzzerComponentIdentity, ...]
    ) -> tuple[V2FuzzerComponentIdentity, ...]:
        by_component = {item.component: item for item in value}
        if len(by_component) != len(value) or set(by_component) != set(V2FuzzerComponent):
            raise ValueError("Office V2 campaign identity requires every component exactly once")
        return tuple(sorted(value, key=lambda item: item.component.value))

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"identity_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def matches_frozen_upstream_and_digest(self) -> Self:
        world = load_canonical_world()
        expected = {
            "world_version": world.world_version,
            "world_digest": world.world_digest,
            "task_catalog_digest": TASK_BLUEPRINT_CATALOG_DIGEST,
            "clean_case_catalog_digest": CLEAN_CASE_CATALOG.catalog_digest,
            "objective_catalog_digest": ATTACK_OBJECTIVE_CATALOG.catalog_digest,
            "risk_catalog_digest": V2_RISK_CATALOG.catalog_digest,
            "coverage_identity_digest": V2_COVERAGE_CONTRACT_IDENTITY.identity_digest,
            "scheduler_policy_digest": V2_SCHEDULER_POLICY_DIGEST,
        }
        for field_name, expected_value in expected.items():
            if getattr(self, field_name) != expected_value:
                raise ValueError(f"Office V2 campaign identity {field_name} does not match")
        if self.identity_digest != sha256_digest(self.digest_payload()):
            raise ValueError("Office V2 campaign identity digest does not match")
        return self


def build_v2_campaign_identity_lock() -> V2CampaignIdentityLock:
    """Build the only identity accepted before Office V2 campaign state exists."""

    world = load_canonical_world()
    payload = {
        "world_version": world.world_version,
        "world_digest": world.world_digest,
        "task_catalog_digest": TASK_BLUEPRINT_CATALOG_DIGEST,
        "clean_case_catalog_digest": CLEAN_CASE_CATALOG.catalog_digest,
        "objective_catalog_digest": ATTACK_OBJECTIVE_CATALOG.catalog_digest,
        "risk_catalog_digest": V2_RISK_CATALOG.catalog_digest,
        "coverage_identity_digest": V2_COVERAGE_CONTRACT_IDENTITY.identity_digest,
        "scheduler_policy_digest": V2_SCHEDULER_POLICY_DIGEST,
        "components": _component_identities(),
    }
    draft = V2CampaignIdentityLock.model_construct(
        **payload,
        identity_digest="sha256:" + "0" * 64,
    )
    return V2CampaignIdentityLock(
        **payload,
        identity_digest=sha256_digest(draft.digest_payload()),
    )


def require_v2_campaign_identity_lock(value: object) -> V2CampaignIdentityLock:
    """Reject legacy, incomplete, or drifted inputs before campaign state creation."""

    if isinstance(value, V2CampaignIdentityLock):
        payload = value.model_dump(mode="json", exclude_none=False)
    elif isinstance(value, Mapping):
        if value.get("identity_version") != V2_FUZZER_IDENTITY_VERSION:
            raise V2FuzzerIdentityError("Office V2 campaign creation requires a V2 identity lock")
        payload = dict(value)
    else:
        raise V2FuzzerIdentityError("Office V2 campaign creation requires a V2 identity lock")
    try:
        validated = V2CampaignIdentityLock.model_validate(payload)
    except ValidationError as exc:
        raise V2FuzzerIdentityError("Office V2 campaign identity validation failed") from exc
    expected = build_v2_campaign_identity_lock()
    if validated.identity_digest != expected.identity_digest:
        raise V2FuzzerIdentityError("Office V2 campaign identity drifted from frozen inputs")
    return validated


__all__ = [
    "V2_FUZZER_IDENTITY_VERSION",
    "V2_SCHEDULER_POLICY_VERSION",
    "V2_SCHEDULER_POLICY_DIGEST",
    "V2CampaignIdentityLock",
    "V2FuzzerComponent",
    "V2FuzzerComponentIdentity",
    "V2FuzzerIdentityError",
    "build_v2_campaign_identity_lock",
    "require_v2_campaign_identity_lock",
]
