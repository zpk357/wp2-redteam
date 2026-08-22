from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agent_image.app.office_v2_session import OfficeV2LiveOracleArtifact
from sandbox.coverage.v2_behavior import V2BehaviorFeature, V2BehaviorFeatureKind
from sandbox.coverage.v2_input import (
    v2_coverage_input_from_direct,
    v2_coverage_input_from_recording,
    v2_coverage_input_from_strict_replay,
)
from sandbox.coverage.v2_tool_behavior import (
    V2ToolBehaviorExtractionError,
    extract_v2_tool_behavior,
)
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASE_BY_ID
from sandbox.scenarios.office_v2.oracle_evidence import OracleEvidenceBundle
from tests.integration.test_office_v2_stage6_acceptance import (
    _evaluate_clean_reference_case,
)
from tests.unit.test_office_v2_coverage_input import (
    _artifact,
    _bundles,
    _manifest,
    _matched_replay,
    _oracle_artifact_payload,
    _recording_state_payload,
)


def _dimensions(feature: V2BehaviorFeature) -> dict[str, str]:
    return {item.name: item.value for item in feature.dimensions}


def _find(
    features: tuple[V2BehaviorFeature, ...],
    kind: V2BehaviorFeatureKind,
    **dimensions: str,
) -> V2BehaviorFeature:
    return next(
        feature
        for feature in features
        if feature.kind is kind
        and all(_dimensions(feature).get(name) == value for name, value in dimensions.items())
    )


def _clean_artifact() -> OfficeV2LiveOracleArtifact:
    bundle, oracle_result = _evaluate_clean_reference_case(
        CLEAN_CASE_BY_ID["clean.t1.apollo"]
    )
    payload = {
        "execution_id": "execution.coverage.tool-behavior.001",
        "trace_digest": sha256_digest("tool-behavior-trace"),
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


def test_real_long_chain_extracts_order_domain_shape_source_permission_and_result() -> None:
    coverage_input = v2_coverage_input_from_direct(
        _clean_artifact(),
        container_removed=True,
    )
    extraction = extract_v2_tool_behavior(coverage_input)

    _find(
        extraction.primary_features,
        V2BehaviorFeatureKind.TOOL_BIGRAM,
        tools="read_email>read_drive_file",
    )
    trigram = _find(
        extraction.primary_features,
        V2BehaviorFeatureKind.TOOL_TRIGRAM,
        tools="search_email>read_email>read_drive_file",
    )
    assert len(trigram.evidence_refs) == 3

    cross_domain = _find(
        extraction.primary_features,
        V2BehaviorFeatureKind.CROSS_DOMAIN_EDGE,
        source_domain="mail",
        sink_domain="drive",
        source_tool="read_email",
        sink_tool="read_drive_file",
    )
    assert len(cross_domain.evidence_refs) == 2

    shape = _find(
        extraction.primary_features,
        V2BehaviorFeatureKind.PARAMETER_SHAPE,
        tool="create_calendar_event",
    )
    assert "attendees:list:" in _dimensions(shape)["shape"]
    assert "description:string" in _dimensions(shape)["shape"]

    source = _find(
        extraction.primary_features,
        V2BehaviorFeatureKind.ARGUMENT_SOURCE_CHAIN,
        tool="read_drive_file",
        argument_path="file_id",
        mode="exact_value",
        origin="tool-output",
        cross_tool="yes",
        cross_domain="yes",
    )
    assert any(ref.ref_kind.value == "output_evidence" for ref in source.evidence_refs)

    permission = _find(
        extraction.primary_features,
        V2BehaviorFeatureKind.PERMISSION_BRANCH,
        tool="create_calendar_event",
    )
    permission_dimensions = _dimensions(permission)
    assert {
        "capability",
        "platform",
        "delegation",
        "policy",
        "effective",
        "policy_mode",
        "enforcement_layer",
        "outcome",
        "reasons",
    }.issubset(permission_dimensions)
    assert any(ref.ref_kind.value == "policy_decision" for ref in permission.evidence_refs)

    result = _find(
        extraction.primary_features,
        V2BehaviorFeatureKind.RESULT_BRANCH,
        tool="write_file",
        status="succeeded",
        failure_code="none",
        transaction="committed",
    )
    assert any(ref.ref_kind.value == "state_transition" for ref in result.evidence_refs)


def test_acquisition_paths_produce_the_same_tool_behavior() -> None:
    direct_bundle, recording_bundle, replay_bundle = _bundles()
    recording_execution_id = "execution.tool.recording.001"
    recording_state_payload = _recording_state_payload(
        recording_execution_id,
        recording_bundle,
    )
    recording_artifact = _artifact(recording_execution_id, recording_bundle)
    oracle_artifact_payload = _oracle_artifact_payload(recording_artifact)
    manifest = _manifest(
        recording_bundle,
        recording_state_payload=recording_state_payload,
        oracle_artifact_payload=oracle_artifact_payload,
    )
    direct = v2_coverage_input_from_direct(
        _artifact("execution.tool.direct.001", direct_bundle),
        container_removed=True,
    )
    recording = v2_coverage_input_from_recording(
        manifest,
        oracle_artifact_payload=oracle_artifact_payload,
        recording_state_payload=recording_state_payload,
        container_removed=True,
    )
    replay = v2_coverage_input_from_strict_replay(
        manifest,
        _matched_replay(manifest, replay_bundle),
        source_oracle_artifact_payload=oracle_artifact_payload,
        source_recording_state_payload=recording_state_payload,
    )

    extractions = tuple(extract_v2_tool_behavior(item) for item in (direct, recording, replay))
    assert len({item.extraction_digest for item in extractions}) == 1
    assert len({item.primary_features for item in extractions}) == 1


def test_incomplete_redacted_argument_shape_is_not_guessed() -> None:
    bundle, _, _ = _bundles()
    coverage = v2_coverage_input_from_direct(
        _artifact("execution.tool.old-evidence.001", bundle),
        container_removed=True,
    )
    exchange = coverage.behavior_source_facts.tool_exchanges[0].model_copy(
        update={"argument_shape": (), "argument_shape_complete": False}
    )
    behavior = coverage.behavior_source_facts.model_copy(
        update={"tool_exchanges": (exchange,)}
    )
    old_evidence = coverage.model_copy(update={"behavior_source_facts": behavior})

    with pytest.raises(V2ToolBehaviorExtractionError, match="predates"):
        extract_v2_tool_behavior(old_evidence)


def test_legacy_oracle_exchange_without_argument_shape_still_round_trips() -> None:
    bundle, _, _ = _bundles()
    legacy_exchanges = tuple(
        exchange.model_copy(
            update={"argument_shape": (), "argument_shape_complete": False}
        )
        for exchange in bundle.tool_exchanges
    )
    draft = bundle.model_copy(
        update={
            "tool_exchanges": legacy_exchanges,
            "bundle_digest": "sha256:" + "0" * 64,
        }
    )
    payload = draft.model_dump(mode="json", exclude_none=False)
    payload["bundle_digest"] = sha256_digest(draft.digest_payload())

    legacy = OracleEvidenceBundle.model_validate(payload)
    serialized = legacy.model_dump(mode="json", exclude_none=False)
    exchange_payload = serialized["tool_exchanges"][0]
    assert "argument_shape" not in exchange_payload
    assert "argument_shape_complete" not in exchange_payload
    assert OracleEvidenceBundle.model_validate(serialized) == legacy


def test_v2_tool_extractor_does_not_import_v1_coverage_modules() -> None:
    source = Path("src/sandbox/coverage/v2_tool_behavior.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert "sandbox.coverage.behavior" not in imported
    assert "sandbox.coverage.feature_normalizer" not in imported
    assert "sandbox.coverage.models" not in imported
    assert "sandbox.coverage.office_risk" not in imported
    assert "sandbox.coverage.store" not in imported
