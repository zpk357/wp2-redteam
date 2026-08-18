"""Digest-locked identity for the Office V2 feedback-loop boundary."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, ValidationError, field_validator, model_validator

from sandbox.protocol import OFFICE_V2_EXECUTION_ENVELOPE_VERSION
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import Identifier, OfficeV2Contract, Sha256Digest
from sandbox.versions import AGENT_VERSION, GRAPH_VERSION

from .v2_identity import build_v2_campaign_identity_lock
from .v2_mutation_identity import build_v2_mutation_identity_lock

V2_FEEDBACK_LOOP_IDENTITY_VERSION = "office-v2-feedback-loop-identity-v1"
V2_LOOP_RUNTIME_PROFILE_VERSION = "office-v2-loop-runtime-profile-v1"
V2_LOOP_TRACE_SCHEMA_VERSION = "1.2"
V2_LOOP_STATE_CODEC_VERSION = "office-v2-state-codec-v1"


class V2FeedbackLoopIdentityError(ValueError):
    """A value cannot safely enter the Office V2 feedback loop."""


class V2FeedbackLoopComponent(StrEnum):
    MUTATION_BUDGET = "mutation-budget"
    PREPARATION_SETTLEMENT = "preparation-settlement"
    EXECUTION_HANDOFF = "execution-handoff"
    EPISODE_WORK = "episode-work"
    EXECUTION_CLOSURE = "execution-closure"
    CANDIDATE_SETTLEMENT = "candidate-settlement"
    NON_EPISODE_SETTLEMENT = "non-episode-settlement"
    FEEDBACK_ORCHESTRATION = "feedback-orchestration"
    FINDING_VERIFICATION = "finding-verification"
    CAMPAIGN_LIFECYCLE = "campaign-lifecycle"


_COMPONENT_VERSIONS = {
    component: f"office-v2-{component.value}-v1"
    for component in V2FeedbackLoopComponent
}

_COMPONENT_SEMANTICS: dict[V2FeedbackLoopComponent, object] = {
    V2FeedbackLoopComponent.MUTATION_BUDGET: {
        "reservation": "plan-maximum-before-provider",
        "terminal-cost-settlement": "all-preparation-terminal-states",
        "insufficient-budget": "do-not-call-provider",
    },
    V2FeedbackLoopComponent.PREPARATION_SETTLEMENT: {
        "terminal-states": ["paused", "ready", "rejected"],
        "ready-route": "execution-handoff",
        "paused-or-rejected-route": "non-episode-settlement",
        "coverage-effect": "forbidden",
    },
    V2FeedbackLoopComponent.EXECUTION_HANDOFF: {
        "input": "ready-preparation-only",
        "lineage": [
            "allocation",
            "candidate",
            "parent-seed",
            "supporting-execution",
            "binding",
            "comparison-context",
            "coverage-baseline",
        ],
        "candidate-work-count": 1,
    },
    V2FeedbackLoopComponent.EPISODE_WORK: {
        "candidate-count": 1,
        "world": "fresh-isolated-episode",
        "attempt-receipts": "immutable",
        "ambiguous": "pause-no-automatic-retry",
    },
    V2FeedbackLoopComponent.EXECUTION_CLOSURE: {
        "evidence": [
            "invocation-result",
            "policy-decision",
            "state-transition",
            "oracle",
            "utility",
            "termination",
            "cleanup",
        ],
        "exposure-stages": ["planned", "delivered", "observed", "used"],
        "initialization-separate-from-agent-delta": True,
    },
    V2FeedbackLoopComponent.CANDIDATE_SETTLEMENT: {
        "requires": ["sealed-work", "execution-record", "coverage-delta"],
        "atomic-state": [
            "coverage",
            "corpus",
            "frontiers",
            "exposure",
            "budget",
            "lifecycle",
            "feedback",
        ],
    },
    V2FeedbackLoopComponent.NON_EPISODE_SETTLEMENT: {
        "dispositions": [
            "cancelled-before-execution",
            "preparation-paused",
            "preparation-rejected",
            "work-permanent-failure",
        ],
        "execution-record": "forbidden",
        "unchanged": ["coverage", "corpus", "exposure", "frontiers", "no-gain"],
    },
    V2FeedbackLoopComponent.FEEDBACK_ORCHESTRATION: {
        "input": "latest-feedback-digest",
        "decision": "recompute-change-or-keep-with-reason",
        "next-generation": "after-exactly-one-settlement",
    },
    V2FeedbackLoopComponent.FINDING_VERIFICATION: {
        "key": "stable-without-acquisition-metadata",
        "states": [
            "recorded",
            "replay-required",
            "replay-confirmed",
            "replay-failed",
        ],
        "strict-replay-new-coverage": False,
        "fork": "verification-only-no-campaign-write",
    },
    V2FeedbackLoopComponent.CAMPAIGN_LIFECYCLE: {
        "baseline-complete": "non-terminal-event-enter-adaptive",
        "terminal": [
            "budget-exhausted-incomplete",
            "cancelled",
            "paused",
            "saturated",
        ],
        "failed-catch-all": "forbidden",
    },
}

V2_LOOP_ASSET_DISPOSITION = {
    "reuse": [
        "attempt-receipt",
        "candidate-settlement",
        "candidate-work",
        "mutation-preparation",
        "strict-replay",
        "v2-campaign-store",
        "v2-coverage-input",
    ],
    "extend": ["campaign-budget", "campaign-lifecycle", "promotion"],
    "new": [
        "execution-closure",
        "execution-handoff",
        "finding-record",
        "mutation-budget-reservation",
        "next-generation-feedback",
        "non-episode-settlement",
        "preparation-cost-settlement",
    ],
    "forbidden": [
        "judge",
        "legacy-v1-campaign-input",
        "real-ollama-mutator",
        "real-qwen-agent",
        "second-campaign-database",
    ],
}
V2_LOOP_ASSET_DISPOSITION_DIGEST = sha256_digest(V2_LOOP_ASSET_DISPOSITION)


class V2FeedbackLoopComponentIdentity(OfficeV2Contract):
    component: V2FeedbackLoopComponent
    version: Identifier
    content_digest: Sha256Digest

    @model_validator(mode="after")
    def matches_frozen_semantics(self) -> Self:
        if self.version != _COMPONENT_VERSIONS[self.component]:
            raise ValueError("Office V2 feedback-loop component version does not match")
        if self.content_digest != sha256_digest(_COMPONENT_SEMANTICS[self.component]):
            raise ValueError("Office V2 feedback-loop component digest does not match")
        return self


def _component_identities() -> tuple[V2FeedbackLoopComponentIdentity, ...]:
    return tuple(
        V2FeedbackLoopComponentIdentity(
            component=component,
            version=_COMPONENT_VERSIONS[component],
            content_digest=sha256_digest(_COMPONENT_SEMANTICS[component]),
        )
        for component in sorted(V2FeedbackLoopComponent, key=lambda item: item.value)
    )


class V2LoopRuntimeProfile(OfficeV2Contract):
    profile_version: Literal["office-v2-loop-runtime-profile-v1"] = (
        V2_LOOP_RUNTIME_PROFILE_VERSION
    )
    agent_profile: Literal["scripted:office-v2"] = "scripted:office-v2"
    mutator_profile: Literal["rule-based:office-v2"] = "rule-based:office-v2"
    execution_mode: Literal["host-scripted", "docker-scripted"] = "host-scripted"
    candidate_count: Literal[1] = 1
    real_qwen_enabled: Literal[False] = False
    real_ollama_mutator_enabled: Literal[False] = False
    judge_enabled: Literal[False] = False
    profile_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"profile_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        if self.profile_digest != sha256_digest(self.digest_payload()):
            raise ValueError("Office V2 loop runtime profile digest does not match")
        return self


def build_v2_loop_runtime_profile(
    *, execution_mode: Literal["host-scripted", "docker-scripted"] = "host-scripted"
) -> V2LoopRuntimeProfile:
    payload = {"execution_mode": execution_mode}
    draft = V2LoopRuntimeProfile.model_construct(
        **payload, profile_digest="sha256:" + "0" * 64
    )
    return V2LoopRuntimeProfile(
        **payload, profile_digest=sha256_digest(draft.digest_payload())
    )


class V2FeedbackLoopIdentityLock(OfficeV2Contract):
    identity_version: Literal["office-v2-feedback-loop-identity-v1"] = (
        V2_FEEDBACK_LOOP_IDENTITY_VERSION
    )
    campaign_identity_digest: Sha256Digest
    mutation_identity_digest: Sha256Digest
    execution_envelope_version: Literal["office-v2-execution-envelope-v1"] = (
        OFFICE_V2_EXECUTION_ENVELOPE_VERSION
    )
    agent_version: Literal["trace-react-v2"] = AGENT_VERSION
    graph_version: Literal["trace-react-v2"] = GRAPH_VERSION
    trace_schema_version: Literal["1.2"] = V2_LOOP_TRACE_SCHEMA_VERSION
    state_codec_version: Literal["office-v2-state-codec-v1"] = (
        V2_LOOP_STATE_CODEC_VERSION
    )
    asset_disposition_digest: Sha256Digest
    runtime_profile: V2LoopRuntimeProfile
    components: tuple[V2FeedbackLoopComponentIdentity, ...] = Field(
        min_length=10, max_length=10
    )
    identity_digest: Sha256Digest

    @field_validator("components")
    @classmethod
    def components_are_complete_and_canonical(
        cls, value: tuple[V2FeedbackLoopComponentIdentity, ...]
    ) -> tuple[V2FeedbackLoopComponentIdentity, ...]:
        by_component = {item.component: item for item in value}
        if len(by_component) != len(value) or set(by_component) != set(
            V2FeedbackLoopComponent
        ):
            raise ValueError(
                "Office V2 feedback-loop identity requires every component once"
            )
        return tuple(sorted(value, key=lambda item: item.component.value))

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"identity_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def matches_upstream_and_digest(self) -> Self:
        expected = {
            "campaign_identity_digest": build_v2_campaign_identity_lock().identity_digest,
            "mutation_identity_digest": build_v2_mutation_identity_lock().identity_digest,
            "asset_disposition_digest": V2_LOOP_ASSET_DISPOSITION_DIGEST,
        }
        for field_name, expected_value in expected.items():
            if getattr(self, field_name) != expected_value:
                raise ValueError(
                    f"Office V2 feedback-loop identity {field_name} does not match"
                )
        if self.identity_digest != sha256_digest(self.digest_payload()):
            raise ValueError("Office V2 feedback-loop identity digest does not match")
        return self


def build_v2_feedback_loop_identity_lock(
    *, runtime_profile: V2LoopRuntimeProfile | None = None
) -> V2FeedbackLoopIdentityLock:
    profile = runtime_profile or build_v2_loop_runtime_profile()
    payload = {
        "campaign_identity_digest": build_v2_campaign_identity_lock().identity_digest,
        "mutation_identity_digest": build_v2_mutation_identity_lock().identity_digest,
        "asset_disposition_digest": V2_LOOP_ASSET_DISPOSITION_DIGEST,
        "runtime_profile": profile,
        "components": _component_identities(),
    }
    draft = V2FeedbackLoopIdentityLock.model_construct(
        **payload, identity_digest="sha256:" + "0" * 64
    )
    return V2FeedbackLoopIdentityLock(
        **payload, identity_digest=sha256_digest(draft.digest_payload())
    )


def require_v2_feedback_loop_identity_lock(
    value: object,
) -> V2FeedbackLoopIdentityLock:
    if isinstance(value, V2FeedbackLoopIdentityLock):
        payload = value.model_dump(mode="json", exclude_none=False)
    elif isinstance(value, Mapping):
        if value.get("identity_version") != V2_FEEDBACK_LOOP_IDENTITY_VERSION:
            raise V2FeedbackLoopIdentityError(
                "Office V2 feedback loop requires its V2 identity lock"
            )
        payload = dict(value)
    else:
        raise V2FeedbackLoopIdentityError(
            "Office V2 feedback loop requires its V2 identity lock"
        )
    try:
        validated = V2FeedbackLoopIdentityLock.model_validate(payload)
    except ValidationError as exc:
        raise V2FeedbackLoopIdentityError(
            "Office V2 feedback-loop identity validation failed"
        ) from exc
    expected = build_v2_feedback_loop_identity_lock(
        runtime_profile=validated.runtime_profile
    )
    if validated.identity_digest != expected.identity_digest:
        raise V2FeedbackLoopIdentityError(
            "Office V2 feedback-loop identity drifted from frozen inputs"
        )
    return validated


__all__ = [
    "V2_FEEDBACK_LOOP_IDENTITY_VERSION",
    "V2_LOOP_ASSET_DISPOSITION",
    "V2_LOOP_ASSET_DISPOSITION_DIGEST",
    "V2FeedbackLoopComponent",
    "V2FeedbackLoopComponentIdentity",
    "V2FeedbackLoopIdentityError",
    "V2FeedbackLoopIdentityLock",
    "V2LoopRuntimeProfile",
    "build_v2_feedback_loop_identity_lock",
    "build_v2_loop_runtime_profile",
    "require_v2_feedback_loop_identity_lock",
]
