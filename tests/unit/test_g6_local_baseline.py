from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sandbox.agent_prompts import (
    OFFICE_AGENT_SYSTEM_PROMPT_DIGEST,
    OFFICE_AGENT_SYSTEM_PROMPT_VERSION,
)
from sandbox.coverage.office_risk import (
    OFFICE_RISK_MAPPING_DIGEST,
    OFFICE_RISK_MAPPING_VERSION,
)
from sandbox.coverage.taxonomy import RiskTaxonomyLoader
from sandbox.replay.artifact_store import ArtifactStore
from sandbox.replay.digests import sha256_digest
from sandbox.replay.manifest import ManifestStore, seal_manifest
from sandbox.replay.models import ReplayManifest
from sandbox.scenarios.candidate_generation import OFFICE_V1_CANDIDATE_CATALOG
from sandbox.scenarios.models import ExecutionBudget
from sandbox.scenarios.office_campaign_baseline import (
    OfficeBaselineLease,
    OfficeBaselinePlanner,
    OfficeBaselineWorkLease,
)
from scripts.run_g6_local_baseline import (
    MODEL_DIGEST,
    MODEL_NAME,
    G6BaselineLock,
    G6RunStatus,
    baseline_execution_id,
    campaign_manifest,
    determine_run_status,
    formal_agent,
    formal_request,
    g6_coverage_result_digest,
    g6_summary_digest,
    load_recording_for_lease,
    lock_file,
    locked_campaign_manifest,
    office_risk_scope,
    validate_recording,
)

IMAGE_ID = "sha256:" + "a" * 64
IMAGE_REF = "trace-redteam-agent-qwen:g6-test"


def _lease_and_lock():
    taxonomy = RiskTaxonomyLoader(Path("config/risk-taxonomy.yaml")).load()
    risk_scope = office_risk_scope(taxonomy)
    manifest = campaign_manifest(
        campaign_id="g6-unit-test",
        image_ref=IMAGE_REF,
        image_id=IMAGE_ID,
        risk_scope=risk_scope,
        random_seed=41,
    )
    plan = OfficeBaselinePlanner(
        campaign_id=manifest.campaign_id,
        manifest=manifest.scenario_catalogs,
        random_seed=manifest.random_seed,
        agent=formal_agent(),
        budget=ExecutionBudget(
            max_steps=20,
            timeout_seconds=600,
            max_output_tokens=4096,
        ),
        catalog=OFFICE_V1_CANDIDATE_CATALOG,
    ).plan()
    item = plan.items[0]
    work_lease = OfficeBaselineWorkLease(
        baseline_item_id=item.baseline_item_id,
        ordinal=item.ordinal,
        lease=OfficeBaselineLease(
            lease_token=sha256_digest("g6-unit-lease"),
            baseline_item_id=item.baseline_item_id,
            worker_id="g6-unit-worker",
            attempt_number=1,
        ),
        selection=item.selection,
        candidate=item.candidate,
    )
    run_lock = G6BaselineLock(
        campaign_id=manifest.campaign_id,
        image_ref=IMAGE_REF,
        image_id=IMAGE_ID,
        model_name=MODEL_NAME,
        model_digest=MODEL_DIGEST,
        system_prompt_version=OFFICE_AGENT_SYSTEM_PROMPT_VERSION,
        system_prompt_digest=OFFICE_AGENT_SYSTEM_PROMPT_DIGEST,
        matrix_digest=plan.source_matrix_digest,
        catalog_manifest_digest=manifest.scenario_catalogs.content_digest,
        taxonomy_version=taxonomy.taxonomy_version,
        taxonomy_digest=taxonomy.digest,
        risk_mapping_version=OFFICE_RISK_MAPPING_VERSION,
        risk_mapping_digest=OFFICE_RISK_MAPPING_DIGEST,
        risk_scope_version=risk_scope.scope_version,
        risk_scope_digest=risk_scope.digest,
        baseline_policy_version=plan.policy_version,
        baseline_plan_digest=plan.content_digest,
        item_count=len(plan.items),
    )
    return work_lease, run_lock


def _recording(
    root: Path,
    *,
    prompt_digest: str = OFFICE_AGENT_SYSTEM_PROMPT_DIGEST,
) -> tuple[ReplayManifest, ManifestStore, ArtifactStore]:
    lease, run_lock = _lease_and_lock()
    request = formal_request(
        lease,
        execution_id=baseline_execution_id(lease),
        campaign_id=run_lock.campaign_id,
        plan_digest=run_lock.baseline_plan_digest,
    )
    artifacts = ArtifactStore(root / "artifacts")
    determinism = {
        "execution_backend": "trace_react_v2",
        "metadata": request.metadata,
        "model": request.model.model_dump(mode="json"),
        "recording_complete": True,
        "system_prompt_version": OFFICE_AGENT_SYSTEM_PROMPT_VERSION,
        "system_prompt_digest": prompt_digest,
    }
    determinism_ref = artifacts.put_bytes(
        json.dumps(determinism, sort_keys=True).encode(), media_type="application/json"
    )
    generic = artifacts.put_bytes(b"{}", media_type="application/json")
    stream = artifacts.put_bytes(b"{}\n", media_type="application/x-ndjson")
    manifest = seal_manifest(
        ReplayManifest(
            replay_id="replay-g6-unit",
            trajectory_id="trajectory-g6-unit",
            created_at=datetime.now(UTC),
            case_id=lease.candidate.case_id,
            scenario_id=lease.candidate.scenario.template_id,
            seed=lease.candidate.seed,
            image_ref=IMAGE_REF,
            image_digest=f"{IMAGE_REF}@{IMAGE_ID}",
            image_digest_kind="repo_digest",
            runtime_version="0.2.0",
            agent_version="trace-react-v2",
            graph_version="trace-react-v2",
            system_prompt_version=OFFICE_AGENT_SYSTEM_PROMPT_VERSION,
            system_prompt_digest=prompt_digest,
            tool_registry_version="controlled-tools-v2",
            policy_version="enterprise-policy-v1",
            default_tool_replay_mode="execute_and_verify",
            prompt_digest=sha256_digest(lease.candidate.benign_task.instruction),
            initial_state_digest=generic.sha256,
            normalized_behavior_trace_digest=stream.sha256,
            determinism_config_digest=sha256_digest(determinism),
            prompt=generic,
            events=stream,
            initial_state=generic,
            determinism_config=determinism_ref,
            model_decisions=stream,
            tool_records=stream,
            checkpoints=stream,
        )
    )
    manifests = ManifestStore(root / "replays")
    manifests.save(manifest)
    return manifest, manifests, artifacts


def test_g6_lock_is_idempotent_and_rejects_identity_drift(tmp_path: Path) -> None:
    _, run_lock = _lease_and_lock()
    path = tmp_path / "baseline-lock.json"
    lock_file(path, run_lock)
    lock_file(path, run_lock)

    drifted = run_lock.model_copy(update={"image_id": "sha256:" + "b" * 64})
    with pytest.raises(RuntimeError, match="identity drift"):
        lock_file(path, drifted)


def test_g6_recovery_finds_one_recording_without_new_model_call(tmp_path: Path) -> None:
    lease, run_lock = _lease_and_lock()
    manifest, manifests, artifacts = _recording(tmp_path)

    recovered = load_recording_for_lease(manifests, artifacts, lease)
    assert recovered == manifest
    validate_recording(
        recovered,
        lease=lease,
        run_lock=run_lock,
        artifacts=artifacts,
    )


def test_g6_recovery_rejects_prompt_drift(tmp_path: Path) -> None:
    lease, run_lock = _lease_and_lock()
    manifest, _, artifacts = _recording(
        tmp_path, prompt_digest="sha256:" + "c" * 64
    )

    with pytest.raises(RuntimeError, match="system prompt identity drift"):
        validate_recording(
            manifest,
            lease=lease,
            run_lock=run_lock,
            artifacts=artifacts,
        )


def test_g6_execution_id_is_stable_for_same_lease() -> None:
    lease, _ = _lease_and_lock()
    assert baseline_execution_id(lease) == baseline_execution_id(lease)
    assert baseline_execution_id(lease).startswith("g6-01-")


def test_g6_campaign_manifest_is_stable_across_restart(tmp_path: Path) -> None:
    taxonomy = RiskTaxonomyLoader(Path("config/risk-taxonomy.yaml")).load()
    risk_scope = office_risk_scope(taxonomy)
    path = tmp_path / "campaign-manifest.json"
    first = locked_campaign_manifest(
        path,
        campaign_id="g6-manifest-test",
        image_ref=IMAGE_REF,
        image_id=IMAGE_ID,
        risk_scope=risk_scope,
        random_seed=41,
    )
    second = locked_campaign_manifest(
        path,
        campaign_id="g6-manifest-test",
        image_ref=IMAGE_REF,
        image_id=IMAGE_ID,
        risk_scope=risk_scope,
        random_seed=41,
    )

    assert second == first
    assert sha256_digest(second) == sha256_digest(first)


def test_g6_summary_digest_accepts_fractional_coverage() -> None:
    assert g6_summary_digest({"behavior_coverage": 0.5}).startswith("sha256:")


def test_g6_coverage_result_digest_accepts_fractional_coverage() -> None:
    assert g6_coverage_result_digest({"behavior_coverage": 0.5}).startswith(
        "sha256:"
    )


@pytest.mark.parametrize(
    ("committed", "attempted", "failed", "blocked", "expected"),
    [
        (12, 12, False, False, G6RunStatus.BASELINE_COMPLETE),
        (0, 1, True, True, G6RunStatus.PAUSED_ON_FAILURE),
        (9, 12, True, False, G6RunStatus.BASELINE_INCOMPLETE_WITH_FAILURES),
        (1, 2, True, False, G6RunStatus.RUNNING),
    ],
)
def test_g6_run_status_distinguishes_scan_outcomes(
    committed: int,
    attempted: int,
    failed: bool,
    blocked: bool,
    expected: G6RunStatus,
) -> None:
    assert determine_run_status(
        item_count=12,
        committed=committed,
        attempted=attempted,
        has_failed_items=failed,
        has_blocking_failure=blocked,
    ) == expected
