"""Digest-locked identity for the Office V2 mutation preparation boundary."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, ValidationError, field_validator, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import Identifier, OfficeV2Contract, Sha256Digest

from .v2_identity import build_v2_campaign_identity_lock

V2_MUTATION_IDENTITY_VERSION = "office-v2-mutation-identity-v1"


class V2MutationIdentityError(ValueError):
    """A value cannot safely enter Office V2 mutation preparation."""


class V2MutationComponent(StrEnum):
    CONTEXT_ALLOCATION = "context-allocation"
    FEEDBACK_OPERATOR_POLICY = "feedback-operator-policy"
    FIELD_REGISTRY = "field-registry"
    MUTATION_PREPARATION = "mutation-preparation"
    PROVIDER_ATTEMPT = "provider-attempt"
    PROVIDER_AUTHORITY = "provider-authority"


_COMPONENT_VERSIONS = {
    component: f"office-v2-{component.value}-v1" for component in V2MutationComponent
}

_COMPONENT_SEMANTICS: dict[V2MutationComponent, object] = {
    V2MutationComponent.CONTEXT_ALLOCATION: {
        "owner": "scheduler",
        "types": ["rebind", "retarget", "authorization-branch"],
        "ordering": ["rebind", "retarget", "authorization-branch"],
        "silent-change": "forbidden",
    },
    V2MutationComponent.FEEDBACK_OPERATOR_POLICY: {
        "owner": "scheduler",
        "input": ["coverage", "exposure", "supporting-execution", "frontier-gap"],
        "output": "operator-allocation-or-no-compatible-operator",
        "deterministic": True,
    },
    V2MutationComponent.FIELD_REGISTRY: {
        "classifications": ["conditionally-mutable", "derived", "frozen", "mutable"],
        "authorities": [
            "host-derived",
            "host-operator",
            "provider-text",
            "scheduler-allocation",
        ],
        "unknown-or-duplicate": "pause",
    },
    V2MutationComponent.MUTATION_PREPARATION: {
        "terminal": ["paused", "ready", "rejected"],
        "candidate-work-created-by": "step-5-only",
        "execution-record": "forbidden",
    },
    V2MutationComponent.PROVIDER_ATTEMPT: {
        "separate-from": "episode-attempt-receipt",
        "retry": "explicit-transient-bounded-by-plan-total",
        "ambiguous": "pause",
    },
    V2MutationComponent.PROVIDER_AUTHORITY: {
        "candidate-count": 1,
        "ordinary-slot-count": 1,
        "multiple-slots": "registered-composition-only",
        "provider-writes": ["payload-slot.generated-content"],
        "provider-cannot-write": [
            "authorization",
            "canonical-world",
            "operator-output",
            "placement",
            "resource-binding",
        ],
        "semantic-preservation": "unverified-until-judge-scope",
    },
}

V2_CONTEXT_ALLOCATION_CONTRACT_DIGEST = sha256_digest(
    _COMPONENT_SEMANTICS[V2MutationComponent.CONTEXT_ALLOCATION]
)
V2_FEEDBACK_TO_OPERATOR_POLICY_DIGEST = sha256_digest(
    _COMPONENT_SEMANTICS[V2MutationComponent.FEEDBACK_OPERATOR_POLICY]
)


class V2MutationComponentIdentity(OfficeV2Contract):
    component: V2MutationComponent
    version: Identifier
    content_digest: Sha256Digest

    @model_validator(mode="after")
    def matches_frozen_semantics(self) -> Self:
        if self.version != _COMPONENT_VERSIONS[self.component]:
            raise ValueError("Office V2 mutation component version does not match")
        if self.content_digest != sha256_digest(_COMPONENT_SEMANTICS[self.component]):
            raise ValueError("Office V2 mutation component digest does not match")
        return self


def _component_identities() -> tuple[V2MutationComponentIdentity, ...]:
    return tuple(
        V2MutationComponentIdentity(
            component=component,
            version=_COMPONENT_VERSIONS[component],
            content_digest=sha256_digest(_COMPONENT_SEMANTICS[component]),
        )
        for component in sorted(V2MutationComponent, key=lambda item: item.value)
    )


class V2MutationIdentityLock(OfficeV2Contract):
    identity_version: Literal["office-v2-mutation-identity-v1"] = (
        V2_MUTATION_IDENTITY_VERSION
    )
    campaign_identity_digest: Sha256Digest
    context_allocation_contract_digest: Sha256Digest
    feedback_operator_policy_digest: Sha256Digest
    components: tuple[V2MutationComponentIdentity, ...] = Field(
        min_length=6, max_length=6
    )
    identity_digest: Sha256Digest

    @field_validator("components")
    @classmethod
    def components_are_complete_and_canonical(
        cls, value: tuple[V2MutationComponentIdentity, ...]
    ) -> tuple[V2MutationComponentIdentity, ...]:
        by_component = {item.component: item for item in value}
        if len(by_component) != len(value) or set(by_component) != set(
            V2MutationComponent
        ):
            raise ValueError("Office V2 mutation identity requires every component once")
        return tuple(sorted(value, key=lambda item: item.component.value))

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"identity_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def matches_upstream_and_digest(self) -> Self:
        expected = {
            "campaign_identity_digest": build_v2_campaign_identity_lock().identity_digest,
            "context_allocation_contract_digest": V2_CONTEXT_ALLOCATION_CONTRACT_DIGEST,
            "feedback_operator_policy_digest": V2_FEEDBACK_TO_OPERATOR_POLICY_DIGEST,
        }
        for field_name, expected_value in expected.items():
            if getattr(self, field_name) != expected_value:
                raise ValueError(f"Office V2 mutation identity {field_name} does not match")
        if self.identity_digest != sha256_digest(self.digest_payload()):
            raise ValueError("Office V2 mutation identity digest does not match")
        return self


def build_v2_mutation_identity_lock() -> V2MutationIdentityLock:
    payload = {
        "campaign_identity_digest": build_v2_campaign_identity_lock().identity_digest,
        "context_allocation_contract_digest": V2_CONTEXT_ALLOCATION_CONTRACT_DIGEST,
        "feedback_operator_policy_digest": V2_FEEDBACK_TO_OPERATOR_POLICY_DIGEST,
        "components": _component_identities(),
    }
    draft = V2MutationIdentityLock.model_construct(
        **payload, identity_digest="sha256:" + "0" * 64
    )
    return V2MutationIdentityLock(
        **payload, identity_digest=sha256_digest(draft.digest_payload())
    )


def require_v2_mutation_identity_lock(value: object) -> V2MutationIdentityLock:
    if isinstance(value, V2MutationIdentityLock):
        payload = value.model_dump(mode="json", exclude_none=False)
    elif isinstance(value, Mapping):
        if value.get("identity_version") != V2_MUTATION_IDENTITY_VERSION:
            raise V2MutationIdentityError(
                "Office V2 mutation preparation requires a V2 mutation identity lock"
            )
        payload = dict(value)
    else:
        raise V2MutationIdentityError(
            "Office V2 mutation preparation requires a V2 mutation identity lock"
        )
    try:
        validated = V2MutationIdentityLock.model_validate(payload)
    except ValidationError as exc:
        raise V2MutationIdentityError(
            "Office V2 mutation identity validation failed"
        ) from exc
    expected = build_v2_mutation_identity_lock()
    if validated.identity_digest != expected.identity_digest:
        raise V2MutationIdentityError(
            "Office V2 mutation identity drifted from frozen inputs"
        )
    return validated


__all__ = [
    "V2_CONTEXT_ALLOCATION_CONTRACT_DIGEST",
    "V2_FEEDBACK_TO_OPERATOR_POLICY_DIGEST",
    "V2_MUTATION_IDENTITY_VERSION",
    "V2MutationComponent",
    "V2MutationComponentIdentity",
    "V2MutationIdentityError",
    "V2MutationIdentityLock",
    "build_v2_mutation_identity_lock",
    "require_v2_mutation_identity_lock",
]
