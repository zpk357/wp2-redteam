from __future__ import annotations

import ast
from pathlib import Path

from agent_image.app.office_v2_session import OfficeV2LiveOracleArtifact
from sandbox.coverage.v2_behavior import V2BehaviorFeature, V2BehaviorFeatureKind
from sandbox.coverage.v2_episode_behavior import extract_v2_behavior_profile
from sandbox.coverage.v2_input import (
    v2_coverage_input_from_direct,
    v2_coverage_input_from_recording,
    v2_coverage_input_from_strict_replay,
)
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASE_BY_ID
from tests.integration.test_office_v2_stage6_acceptance import (
    _evaluate_clean_reference_case,
)
from tests.unit.test_office_v2_coverage_input import (
    _artifact,
    _bundles,
    _manifest,
    _matched_replay,
    _recording_state_payload,
)


def _dimensions(feature: V2BehaviorFeature) -> dict[str, str]:
    return {item.name: item.value for item in feature.dimensions}


def _clean_artifact(case_id: str) -> OfficeV2LiveOracleArtifact:
    bundle, oracle_result = _evaluate_clean_reference_case(CLEAN_CASE_BY_ID[case_id])
    payload = {
        "execution_id": f"execution.coverage.profile.{case_id}",
        "trace_digest": sha256_digest({"trace": case_id}),
        "trusted_facts_digest": sha256_digest({"facts": bundle.bundle_digest}),
        "evidence_bundle": bundle,
        "oracle_result": oracle_result,
    }
    draft = OfficeV2LiveOracleArtifact.model_construct(
        **payload,
        artifact_digest="sha256:" + "0" * 64,
    )
    return OfficeV2LiveOracleArtifact(
        **payload,
        artifact_digest=sha256_digest(draft.digest_payload()),
    )


def test_committed_state_and_submit_complete_the_behavior_profile() -> None:
    coverage = v2_coverage_input_from_direct(
        _clean_artifact("clean.t1.apollo"),
        container_removed=True,
    )
    profile = extract_v2_behavior_profile(coverage)

    state = [
        feature
        for feature in profile.primary_features
        if feature.kind is V2BehaviorFeatureKind.STATE_OBJECT_CHANGE
    ]
    assert any(
        _dimensions(feature)
        == {"count": "1", "object_kind": "calendar_event", "operation": "created"}
        for feature in state
    )
    assert any(
        _dimensions(feature)
        == {"count": "3+", "object_kind": "attendance", "operation": "created"}
        for feature in state
    )
    assert any(
        feature.kind is V2BehaviorFeatureKind.STATE_FIELD_CHANGE
        and _dimensions(feature).get("object_kind") == "workspace_file"
        and _dimensions(feature).get("field_path") == "content"
        for feature in profile.primary_features
    )
    termination = next(
        feature
        for feature in profile.primary_features
        if feature.kind is V2BehaviorFeatureKind.TERMINATION
    )
    assert _dimensions(termination) == {"reason": "submit", "submitted": "yes"}
    assert termination.evidence_refs[0].ref_kind.value == "termination"
    assert profile.normalized_path.segments[-1].atoms[-1].semantic_id == "termination.submit"


def test_trusted_authorization_flow_produces_interaction_behavior_and_edges() -> None:
    coverage = v2_coverage_input_from_direct(
        _clean_artifact("clean.t9.apollo"),
        container_removed=True,
    )
    profile = extract_v2_behavior_profile(coverage)

    interactions = [
        feature
        for feature in profile.primary_features
        if feature.kind is V2BehaviorFeatureKind.INTERACTION
    ]
    assert interactions
    assert any(
        _dimensions(feature).get("state_advanced") == "yes"
        and _dimensions(feature).get("status") == "grant_created"
        for feature in interactions
    )
    assert any(
        feature.kind is V2BehaviorFeatureKind.INTERACTION_EDGE
        for feature in profile.primary_features
    )
    assert any(
        atom.atom_kind.value == "interaction"
        for segment in profile.normalized_path.segments
        for atom in segment.atoms
    )


def test_initialization_overlay_does_not_become_agent_state_coverage() -> None:
    bundle, _, _ = _bundles()
    coverage = v2_coverage_input_from_direct(
        _artifact("execution.profile.overlay.001", bundle),
        container_removed=True,
    )
    profile = extract_v2_behavior_profile(coverage)

    state_kinds = {
        V2BehaviorFeatureKind.STATE_OBJECT_CHANGE,
        V2BehaviorFeatureKind.STATE_FIELD_CHANGE,
        V2BehaviorFeatureKind.STATE_RELATION_CHANGE,
        V2BehaviorFeatureKind.STATE_CROSS_DOMAIN,
    }
    assert not any(feature.kind in state_kinds for feature in profile.primary_features)


def test_acquisition_paths_produce_the_same_complete_behavior_profile() -> None:
    direct_bundle, recording_bundle, replay_bundle = _bundles()
    recording_execution_id = "execution.profile.recording.001"
    recording_state_payload = _recording_state_payload(
        recording_execution_id,
        recording_bundle,
    )
    manifest = _manifest(
        recording_bundle,
        recording_state_payload=recording_state_payload,
    )
    inputs = (
        v2_coverage_input_from_direct(
            _artifact("execution.profile.direct.001", direct_bundle),
            container_removed=True,
        ),
        v2_coverage_input_from_recording(
            manifest,
            _artifact(recording_execution_id, recording_bundle),
            recording_state_payload=recording_state_payload,
            container_removed=True,
        ),
        v2_coverage_input_from_strict_replay(
            manifest,
            _matched_replay(manifest, replay_bundle),
            _artifact("execution.profile.replay.001", replay_bundle),
            source_recording_state_payload=recording_state_payload,
        ),
    )

    profiles = tuple(extract_v2_behavior_profile(item) for item in inputs)
    assert len({item.profile_digest for item in profiles}) == 1


def test_v2_episode_extractor_does_not_import_v1_coverage_modules() -> None:
    source = Path("src/sandbox/coverage/v2_episode_behavior.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert "sandbox.coverage.behavior" not in imported
    assert "sandbox.coverage.models" not in imported
    assert "sandbox.coverage.office_risk" not in imported
    assert "sandbox.coverage.store" not in imported
