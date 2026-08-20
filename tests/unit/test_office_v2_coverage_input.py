from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agent_image.app.office_v2_session import OfficeV2LiveOracleArtifact
from sandbox.coverage.v2_contracts import build_v2_candidate_batch_baseline
from sandbox.coverage.v2_episode_coverage import (
    V2CandidateEpisode,
    build_v2_episode_coverage_facts,
    empty_v2_coverage_snapshot,
    evaluate_v2_candidate_batch,
)
from sandbox.coverage.v2_input import (
    V2AcquisitionKind,
    V2CoverageInputError,
    v2_coverage_input_from_direct,
    v2_coverage_input_from_recording,
    v2_coverage_input_from_strict_replay,
)
from sandbox.protocol import ToolReplayMode
from sandbox.replay.digests import sha256_digest
from sandbox.replay.manifest import seal_manifest
from sandbox.replay.models import ArtifactRef, ReplayManifest, ReplayResult, ReplayStatus
from sandbox.scenarios.office_v2.oracle import evaluate_scenario_oracle
from sandbox.scenarios.office_v2.oracle_evidence import build_oracle_evidence_bundle
from sandbox.scenarios.office_v2.oracle_trace import build_oracle_evidence_from_trace
from tests.unit.test_office_v2_oracle_trace import _recording


def _artifact(
    execution_id: str,
    bundle,
) -> OfficeV2LiveOracleArtifact:
    result = evaluate_scenario_oracle(
        bundle=bundle,
        scenario_case=_recording()[0].scenario_case,
    )
    payload = {
        "execution_id": execution_id,
        "trace_digest": sha256_digest({"trace": execution_id}),
        "trusted_facts_digest": sha256_digest({"facts": bundle.bundle_digest}),
        "evidence_bundle": bundle,
        "oracle_result": result,
    }
    draft = OfficeV2LiveOracleArtifact.model_construct(
        **payload,
        artifact_digest="sha256:" + "0" * 64,
    )
    return OfficeV2LiveOracleArtifact(
        **payload,
        artifact_digest=sha256_digest(draft.digest_payload()),
    )


def _bundles():
    materialization, invocation, result, events, termination = _recording()
    common = {
        "scenario_case": materialization.scenario_case,
        "initialization_transition": materialization.initialization_transition,
        "invocations": (invocation,),
        "results": (result,),
        "interaction_facts": (),
        "termination": termination,
        "final_state_digest": result.after_state_digest,
    }
    direct = build_oracle_evidence_bundle(timeline=None, **common)
    recording = build_oracle_evidence_from_trace(trace_events=events, **common)
    replay_events = tuple(
        event.model_copy(update={"execution_id": "execution.coverage-replay.001"})
        for event in events
    )
    replay = build_oracle_evidence_from_trace(trace_events=replay_events, **common)
    return direct, recording, replay


def _artifact_ref(label: str) -> ArtifactRef:
    return ArtifactRef(
        media_type="application/json",
        sha256=sha256_digest(label),
        size_bytes=2,
        relative_path=f"{label}.json",
    )


def _manifest(bundle, *, recording_complete: bool = True) -> ReplayManifest:
    generic = _artifact_ref("generic")
    manifest = ReplayManifest(
        replay_id="replay.coverage.001",
        trajectory_id="trajectory.coverage.001",
        created_at=datetime(2026, 8, 14, tzinfo=UTC),
        case_id=bundle.identity.scenario_case_id,
        scenario_id="office-workspace-v2",
        seed=42,
        image_ref="trace-g-agent:test",
        image_digest="sha256:" + "1" * 64,
        image_digest_kind="image_id",
        runtime_version="test",
        agent_version="trace-react-v2",
        state_codec_version="office-v2-state-codec-v1",
        default_tool_replay_mode=ToolReplayMode.STUB_RESPONSE,
        recording_complete=recording_complete,
        incomplete_reason=None if recording_complete else "test-incomplete",
        prompt_digest=sha256_digest("prompt"),
        initial_state_digest=bundle.identity.initial_state_digest,
        normalized_behavior_trace_digest=sha256_digest("normalized-behavior"),
        determinism_config_digest=sha256_digest("determinism"),
        prompt=generic,
        events=generic,
        initial_state=generic,
        determinism_config=generic,
        model_decisions=generic,
        tool_records=generic,
        checkpoints=generic,
        office_v2_recording_state=_artifact_ref("recording-state"),
        office_v2_oracle=_artifact_ref("oracle"),
    )
    return seal_manifest(manifest)


def _matched_replay(manifest: ReplayManifest, bundle) -> ReplayResult:
    return ReplayResult(
        replay_run_id="run.coverage.001",
        source_replay_id=manifest.replay_id,
        source_trajectory_id=manifest.trajectory_id,
        replay_trajectory_id="trajectory.coverage.replay.001",
        status=ReplayStatus.MATCHED,
        source_behavior_digest=manifest.normalized_behavior_trace_digest,
        replay_behavior_digest=manifest.normalized_behavior_trace_digest,
        source_final_state_digest=bundle.identity.final_state_digest,
        replay_final_state_digest=bundle.identity.final_state_digest,
        container_removed=True,
    )


def test_direct_recording_and_strict_replay_share_canonical_facts() -> None:
    direct_bundle, recording_bundle, replay_bundle = _bundles()
    direct_artifact = _artifact("execution.coverage.direct.001", direct_bundle)
    recording_artifact = _artifact("execution.coverage.recording.001", recording_bundle)
    replay_artifact = _artifact("execution.coverage.replay.001", replay_bundle)
    manifest = _manifest(recording_bundle)

    direct = v2_coverage_input_from_direct(direct_artifact, container_removed=True)
    recording = v2_coverage_input_from_recording(
        manifest,
        recording_artifact,
        container_removed=True,
    )
    replay = v2_coverage_input_from_strict_replay(
        manifest,
        _matched_replay(manifest, replay_bundle),
        replay_artifact,
    )

    assert direct.canonical_fact_digest == recording.canonical_fact_digest
    assert recording.canonical_fact_digest == replay.canonical_fact_digest
    assert direct.behavior_source_facts == recording.behavior_source_facts
    assert recording.behavior_source_facts == replay.behavior_source_facts
    assert direct.oracle_facts.oracle_fact_digest == recording.oracle_facts.oracle_fact_digest
    assert recording.oracle_facts.oracle_fact_digest == replay.oracle_facts.oracle_fact_digest
    assert {
        direct.acquisition.source_kind,
        recording.acquisition.source_kind,
        replay.acquisition.source_kind,
    } == {
        V2AcquisitionKind.DIRECT,
        V2AcquisitionKind.RECORDING,
        V2AcquisitionKind.STRICT_REPLAY,
    }
    assert len(
        {
            direct.acquisition.metadata_digest,
            recording.acquisition.metadata_digest,
            replay.acquisition.metadata_digest,
        }
    ) == 3

    snapshots = []
    for index, item in enumerate((direct, recording, replay), start=1):
        baseline = empty_v2_coverage_snapshot()
        candidate_id = f"candidate-{index}"
        batch = build_v2_candidate_batch_baseline(
            campaign_id="campaign-coverage-equivalence",
            candidate_set_id=f"candidate-set-{index}",
            candidate_set_digest=sha256_digest("same-candidate-set"),
            baseline_snapshot_digest=baseline.snapshot_digest,
            candidate_ids=(candidate_id,),
        )
        result = evaluate_v2_candidate_batch(
            batch_baseline=batch,
            baseline_snapshot=baseline,
            candidates=(
                V2CandidateEpisode(
                    candidate_id=candidate_id,
                    episode_facts=build_v2_episode_coverage_facts(item),
                ),
            ),
        )
        snapshots.append(result.next_snapshot)
    assert snapshots[0] == snapshots[1] == snapshots[2]


def test_initialization_is_separate_from_agent_state_transitions() -> None:
    bundle, _, _ = _bundles()
    coverage = v2_coverage_input_from_direct(
        _artifact("execution.coverage.separation.001", bundle),
        container_removed=True,
    )

    facts = coverage.behavior_source_facts
    assert facts.initialization_materialization_digest == bundle.materialization_ref.evidence_digest
    assert facts.initialization_materialization_digest not in facts.agent_transition_digests
    assert facts.agent_transition_digests == tuple(
        exchange.state_transition.transition_digest
        for exchange in bundle.tool_exchanges
        if exchange.state_transition is not None
    )
    assert coverage.oracle_facts.planned_objective_ids
    assert all(
        violation.objective_id is None
        for violation in coverage.oracle_facts.security.violations
        if not violation.planned
    )


def test_incomplete_recording_diverged_replay_and_missing_cleanup_are_rejected() -> None:
    bundle, _, _ = _bundles()
    artifact = _artifact("execution.coverage.reject.001", bundle)

    with pytest.raises(V2CoverageInputError, match="acquisition metadata"):
        v2_coverage_input_from_direct(artifact, container_removed=False)

    incomplete = _manifest(bundle, recording_complete=False)
    with pytest.raises(V2CoverageInputError, match="incomplete recording"):
        v2_coverage_input_from_recording(
            incomplete,
            artifact,
            container_removed=True,
        )

    manifest = _manifest(bundle)
    replay = _matched_replay(manifest, bundle).model_copy(
        update={"status": ReplayStatus.DIVERGED}
    )
    with pytest.raises(V2CoverageInputError, match="must be matched"):
        v2_coverage_input_from_strict_replay(manifest, replay, artifact)


def test_oracle_result_must_close_over_the_exact_bundle() -> None:
    bundle, other_bundle, _ = _bundles()
    artifact = _artifact("execution.coverage.mismatch.001", bundle)
    changed_bundle = other_bundle.model_copy(
        update={"recording_digest": sha256_digest("other")}
    )
    mismatched = artifact.model_copy(
        update={"evidence_bundle": changed_bundle}
    )

    with pytest.raises(V2CoverageInputError, match="invalid V2 execution closure"):
        v2_coverage_input_from_direct(mismatched, container_removed=True)


def test_coverage_input_digest_rejects_acquisition_tampering() -> None:
    bundle, _, _ = _bundles()
    coverage = v2_coverage_input_from_direct(
        _artifact("execution.coverage.input-digest.001", bundle),
        container_removed=True,
    )
    payload = coverage.model_dump(mode="json", exclude_none=False)
    payload["evidence_bundle_digest"] = sha256_digest("changed")

    with pytest.raises(ValidationError, match="input digest does not match"):
        type(coverage).model_validate(payload)
