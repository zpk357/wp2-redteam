from __future__ import annotations

import pytest
from pydantic import ValidationError

from sandbox.fuzzer.v2_identity import build_v2_campaign_identity_lock
from sandbox.fuzzer.v2_loop_identity import (
    V2_LOOP_ASSET_DISPOSITION,
    V2_LOOP_ASSET_DISPOSITION_DIGEST,
    V2FeedbackLoopComponent,
    V2FeedbackLoopIdentityError,
    V2LoopRuntimeProfile,
    build_v2_feedback_loop_identity_lock,
    build_v2_loop_runtime_profile,
    require_v2_feedback_loop_identity_lock,
)
from sandbox.fuzzer.v2_mutation_identity import build_v2_mutation_identity_lock


def test_loop_identity_is_complete_deterministic_and_binds_upstream() -> None:
    first = build_v2_feedback_loop_identity_lock()
    second = build_v2_feedback_loop_identity_lock()

    assert first == second
    assert first.campaign_identity_digest == build_v2_campaign_identity_lock().identity_digest
    assert first.mutation_identity_digest == build_v2_mutation_identity_lock().identity_digest
    assert first.asset_disposition_digest == V2_LOOP_ASSET_DISPOSITION_DIGEST
    assert tuple(item.component for item in first.components) == tuple(
        sorted(V2FeedbackLoopComponent, key=lambda item: item.value)
    )
    assert require_v2_feedback_loop_identity_lock(first) == first
    assert require_v2_feedback_loop_identity_lock(first.model_dump(mode="json")) == first


def test_loop_identity_locks_assets_and_engineering_only_runtime() -> None:
    identity = build_v2_feedback_loop_identity_lock()

    assert V2_LOOP_ASSET_DISPOSITION["forbidden"] == [
        "judge",
        "legacy-v1-campaign-input",
        "real-ollama-mutator",
        "real-qwen-agent",
        "second-campaign-database",
    ]
    assert identity.runtime_profile.agent_profile == "scripted:office-v2"
    assert identity.runtime_profile.mutator_profile == "rule-based:office-v2"
    assert identity.runtime_profile.candidate_count == 1
    assert identity.runtime_profile.real_qwen_enabled is False
    assert identity.runtime_profile.real_ollama_mutator_enabled is False
    assert identity.runtime_profile.judge_enabled is False


def test_host_and_docker_scripted_profiles_are_distinct_and_valid() -> None:
    host = build_v2_feedback_loop_identity_lock()
    docker = build_v2_feedback_loop_identity_lock(
        runtime_profile=build_v2_loop_runtime_profile(
            execution_mode="docker-scripted"
        )
    )

    assert host.identity_digest != docker.identity_digest
    assert require_v2_feedback_loop_identity_lock(docker) == docker


@pytest.mark.parametrize(
    "field_name",
    (
        "campaign_identity_digest",
        "mutation_identity_digest",
        "asset_disposition_digest",
    ),
)
def test_loop_identity_rejects_upstream_or_asset_drift(field_name: str) -> None:
    identity = build_v2_feedback_loop_identity_lock()
    drifted = identity.model_copy(update={field_name: "sha256:" + "9" * 64})

    with pytest.raises(V2FeedbackLoopIdentityError, match="validation failed"):
        require_v2_feedback_loop_identity_lock(drifted)


def test_loop_identity_rejects_component_drift_and_legacy_input() -> None:
    identity = build_v2_feedback_loop_identity_lock()
    component = identity.components[0]
    drifted_component = component.model_copy(
        update={"content_digest": "sha256:" + "8" * 64}
    )
    payload = identity.model_dump(mode="python", exclude_none=False)
    payload["components"] = (drifted_component, *identity.components[1:])

    with pytest.raises(ValidationError, match="component digest does not match"):
        type(identity).model_validate(payload)
    with pytest.raises(V2FeedbackLoopIdentityError, match="requires its V2 identity"):
        require_v2_feedback_loop_identity_lock(build_v2_campaign_identity_lock())


def test_loop_identity_rejects_unversioned_mapping() -> None:
    payload = build_v2_feedback_loop_identity_lock().model_dump(mode="json")
    payload.pop("identity_version")

    with pytest.raises(V2FeedbackLoopIdentityError, match="requires its V2 identity"):
        require_v2_feedback_loop_identity_lock(payload)


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("agent_profile", "ollama:qwen"),
        ("mutator_profile", "ollama:qwen"),
        ("candidate_count", 2),
        ("real_qwen_enabled", True),
        ("real_ollama_mutator_enabled", True),
        ("judge_enabled", True),
    ),
)
def test_loop_runtime_rejects_qwen_judge_batch_and_real_mutator(
    field_name: str, value: object
) -> None:
    profile = build_v2_loop_runtime_profile().model_dump(
        mode="python", exclude_none=False
    )
    profile[field_name] = value

    with pytest.raises(ValidationError):
        V2LoopRuntimeProfile.model_validate(profile)
