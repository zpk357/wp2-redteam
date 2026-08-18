from __future__ import annotations

import pytest

from sandbox.agent_prompts import OFFICE_AGENT_BASE_RULES_V2_DIGEST
from sandbox.fuzzer.v2_stage6_identity import (
    Stage6Role,
    seal_inference_config,
    seal_role_identity,
    seal_stage6_model_lock,
)
from sandbox.mutation.v2_brief import V2_MUTATION_PROMPT_IDENTITY_DIGEST


def digest(value: str) -> str:
    return "sha256:" + value * 64


def role(role_kind: Stage6Role, image: str):
    is_agent = role_kind is Stage6Role.AGENT
    return seal_role_identity(
        role=role_kind,
        image_reference=f"trace-g-office-v2-{role_kind.value}:step6-local",
        image_id=digest(image),
        image_archive_sha256=digest("c" if is_agent else "d"),
        prompt_identity_digest=(
            OFFICE_AGENT_BASE_RULES_V2_DIGEST
            if is_agent
            else V2_MUTATION_PROMPT_IDENTITY_DIGEST
        ),
        provider_identity=(
            "ollama-react-stage6" if is_agent else "provider-docker-ollama-v2"
        ),
        inference=seal_inference_config(
            num_predict=4096 if is_agent else 2048,
            temperature="0.2" if is_agent else "0.7",
            thinking=is_agent,
        ),
    )


def test_stage6_lock_canonicalizes_two_distinct_runtime_roles() -> None:
    lock = seal_stage6_model_lock(
        manifest_digest=digest("1"),
        config_digest=digest("2"),
        chat_protocol_digest=digest("6"),
        layer_digests=(digest("3"),),
        archive_sha256=digest("4"),
        archive_bytes=17_000_000_000,
        ollama_image_reference="ollama/ollama:0.32.1",
        ollama_image_id=digest("5"),
        ollama_version="0.32.1",
        controller_image_reference="trace-redteam-controller:server",
        controller_image_id=digest("7"),
        controller_archive_sha256=digest("8"),
        roles=(role(Stage6Role.MUTATOR, "b"), role(Stage6Role.AGENT, "a")),
    )
    assert tuple(item.role for item in lock.roles) == (
        Stage6Role.AGENT,
        Stage6Role.MUTATOR,
    )


def test_stage6_lock_rejects_one_image_identity_for_both_roles() -> None:
    with pytest.raises(ValueError, match="image identities must differ"):
        seal_stage6_model_lock(
            manifest_digest=digest("1"),
            config_digest=digest("2"),
            chat_protocol_digest=digest("6"),
            layer_digests=(digest("3"),),
            archive_sha256=digest("4"),
            archive_bytes=17_000_000_000,
            ollama_image_reference="ollama/ollama:0.32.1",
            ollama_image_id=digest("5"),
            ollama_version="0.32.1",
            controller_image_reference="trace-redteam-controller:server",
            controller_image_id=digest("7"),
            controller_archive_sha256=digest("8"),
            roles=(role(Stage6Role.AGENT, "a"), role(Stage6Role.MUTATOR, "a")),
        )
