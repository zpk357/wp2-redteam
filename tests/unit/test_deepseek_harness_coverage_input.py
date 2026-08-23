from __future__ import annotations

import pytest

from sandbox.coverage.v2_input import (
    V2CoverageInputError,
    v2_coverage_input_from_recording,
    verify_v2_recording_runtime_identity,
)
from sandbox.protocol import AgentRuntimeKind
from sandbox.replay.canonical import canonical_json_bytes
from sandbox.replay.digests import sha256_bytes, sha256_digest
from sandbox.replay.manifest import seal_manifest
from sandbox.replay.models import ArtifactRef
from tests.unit.test_office_v2_coverage_input import (
    _artifact,
    _bundles,
    _manifest,
    _oracle_artifact_payload,
    _recording_state_payload,
)


def _runtime_manifest(base, *, kind: AgentRuntimeKind, version: str, composition: str):
    determinism = {
        "producer_runtime_kind": kind.value,
        "producer_runtime_version": version,
        "producer_runtime_composition_digest": composition,
    }
    payload = canonical_json_bytes(determinism)
    reference = ArtifactRef(
        media_type="application/json",
        sha256=sha256_bytes(payload),
        size_bytes=len(payload),
        relative_path=f"objects/{sha256_bytes(payload).removeprefix('sha256:')}",
    )
    manifest = seal_manifest(
        base.model_copy(
            update={
                "manifest_digest": None,
                "determinism_config": reference,
                "determinism_config_digest": sha256_digest(determinism),
                "metadata": {
                    "producer_runtime_kind": kind.value,
                    "producer_runtime_version": version,
                    "producer_runtime_composition_digest": composition,
                },
            }
        )
    )
    return manifest, payload


def test_runtime_acquisition_identity_is_verified_but_not_coverage_novelty() -> None:
    _, recording_bundle, _ = _bundles()
    execution_id = "execution.coverage.runtime-neutral.001"
    artifact = _artifact(execution_id, recording_bundle)
    oracle_payload = _oracle_artifact_payload(artifact)
    recording_state = _recording_state_payload(execution_id, recording_bundle)
    base = _manifest(
        recording_bundle,
        recording_state_payload=recording_state,
        oracle_artifact_payload=oracle_payload,
    )
    identities = (
        (AgentRuntimeKind.LANGGRAPH, "langgraph-test-v1", sha256_digest("langgraph")),
        (
            AgentRuntimeKind.DEEPSEEK_HARNESS,
            "deepseek-harness-h4-v1",
            sha256_digest("deepseek-harness"),
        ),
    )

    coverage_inputs = []
    for kind, version, composition in identities:
        manifest, determinism = _runtime_manifest(
            base,
            kind=kind,
            version=version,
            composition=composition,
        )
        assert verify_v2_recording_runtime_identity(
            manifest,
            determinism_config_payload=determinism,
            expected_runtime_kind=kind,
            expected_runtime_version=version,
            expected_runtime_composition_digest=composition,
        )["producer_runtime_kind"] == kind.value
        coverage_inputs.append(
            v2_coverage_input_from_recording(
                manifest,
                oracle_artifact_payload=oracle_payload,
                recording_state_payload=recording_state,
                container_removed=True,
            )
        )

    first, second = coverage_inputs
    assert first.canonical_fact_digest == second.canonical_fact_digest
    assert first.behavior_source_facts == second.behavior_source_facts
    assert first.oracle_facts == second.oracle_facts
    assert first.acquisition.metadata_digest != second.acquisition.metadata_digest

    harness_manifest, harness_determinism = _runtime_manifest(
        base,
        kind=identities[1][0],
        version=identities[1][1],
        composition=identities[1][2],
    )
    with pytest.raises(V2CoverageInputError, match="differs from Campaign"):
        verify_v2_recording_runtime_identity(
            harness_manifest,
            determinism_config_payload=harness_determinism,
            expected_runtime_kind=AgentRuntimeKind.LANGGRAPH,
            expected_runtime_version=identities[1][1],
            expected_runtime_composition_digest=identities[1][2],
        )
