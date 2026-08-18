from __future__ import annotations

from pathlib import Path

import pytest
from app.adapter.trace_react_adapter import TraceReactAdapter
from app.protocol import ExecutionRequest

from sandbox.coverage.input import CoverageInputResolver
from sandbox.coverage.models import (
    CampaignCoverageFeedback,
    CampaignRiskScope,
    CoverageInput,
    CoverageResult,
    CoverageSaturationSummary,
    RiskCoverageGap,
    RiskReachability,
)
from sandbox.coverage.office_risk import (
    OFFICE_RISK_MAPPING_DIGEST,
    OFFICE_RISK_MAPPING_VERSION,
)
from sandbox.coverage.risk_scope import CampaignRiskScopeIndex
from sandbox.coverage.taxonomy import RiskTaxonomyLoader
from sandbox.fuzzer.models import ScenarioCampaignManifest
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.candidate_generation import OFFICE_V1_CANDIDATE_CATALOG
from sandbox.scenarios.models import ExecutionBudget
from sandbox.scenarios.models import TestCase as ScenarioTestCase
from sandbox.scenarios.office_campaign_baseline import (
    OfficeBaselineAttemptOutcome,
    OfficeBaselinePlanner,
    OfficeBaselineStatus,
)
from sandbox.scenarios.office_campaign_state import (
    OfficeCampaignStateError,
    OfficeCampaignStateStore,
)
from sandbox.scenarios.office_episode import build_office_episode_initialization
from sandbox.scenarios.office_v1 import FAKE_AGENT


def _risk_scope() -> CampaignRiskScopeIndex:
    taxonomy = RiskTaxonomyLoader(Path("config/risk-taxonomy.yaml")).load()
    return CampaignRiskScopeIndex(
        CampaignRiskScope(
            scope_version="office-baseline-risk-scope-v1",
            taxonomy_version=taxonomy.taxonomy_version,
            categories={
                category_id: RiskReachability(max_reachable_depth=3)
                for category_id in (
                    "data_exfiltration",
                    "unauthorized_file_read",
                    "unauthorized_resource_deletion",
                )
            },
        ),
        taxonomy,
    )


def _feedback(
    campaign_id: str = "office-baseline-test",
) -> CampaignCoverageFeedback:
    risk_scope = _risk_scope()
    return CampaignCoverageFeedback(
        campaign_id=campaign_id,
        taxonomy_version=risk_scope.taxonomy.taxonomy_version,
        taxonomy_digest=risk_scope.taxonomy.digest,
        risk_mapping_version=OFFICE_RISK_MAPPING_VERSION,
        risk_mapping_digest=OFFICE_RISK_MAPPING_DIGEST,
        risk_scope_version=risk_scope.scope_version,
        risk_scope_digest=risk_scope.digest,
        include_empty=True,
        observed_behavior_paths=0,
        risk_gaps=tuple(
            RiskCoverageGap(
                risk_category_id=category_id,
                risk_category_label=risk_scope.taxonomy.get(category_id).label,
                observed_depth=0,
                observed_execution_depth=0,
                max_reachable_depth=3,
                next_execution_target_depth=2,
            )
            for category_id in risk_scope.category_ids
        ),
        saturation=CoverageSaturationSummary(
            observations=0,
            trailing_without_behavior_gain=0,
            max_without_behavior_gain=0,
            trailing_without_execution_risk_gain=0,
            max_without_execution_risk_gain=0,
            trailing_without_any_gain=0,
            max_without_any_gain=0,
        ),
    )


def _manifest(feedback: CampaignCoverageFeedback) -> ScenarioCampaignManifest:
    return ScenarioCampaignManifest(
        campaign_id=feedback.campaign_id,
        config_digest="sha256:" + "4" * 64,
        taxonomy_version=feedback.taxonomy_version,
        taxonomy_digest=feedback.taxonomy_digest,
        risk_scope_version=feedback.risk_scope_version,
        risk_scope_digest=feedback.risk_scope_digest,
        mutation_registry_version="office-mutation-v1",
        mutation_registry_digest="sha256:" + "5" * 64,
        mutation_provider="rule_based",
        mutation_provider_version="contract-test-double-v1",
        agent_model_name="office-safe-control",
        agent_image="trace-redteam-agent:test",
        target_profile_id="office-v1",
        energy_formula_version="frontier-state-only-v1",
        corpus_policy_version="frontier-state-only-v1",
        scheduler_policy_version="office-baseline-matrix-v1",
        random_seed=41,
        scenario_catalogs=OFFICE_V1_CANDIDATE_CATALOG.manifest(),
    )


def _store(
    tmp_path: Path,
    *,
    feedback: CampaignCoverageFeedback | None = None,
    manifest: ScenarioCampaignManifest | None = None,
) -> OfficeCampaignStateStore:
    locked_feedback = feedback or _feedback()
    return OfficeCampaignStateStore(
        tmp_path / "state",
        manifest or _manifest(locked_feedback),
        locked_feedback,
        agent=FAKE_AGENT,
        risk_scope=_risk_scope(),
    )


async def _submitted_input(
    tmp_path: Path,
    case: ScenarioTestCase,
    *,
    execution_id: str,
) -> tuple[CoverageInput, CoverageResult]:
    initialization = build_office_episode_initialization(case)
    request = ExecutionRequest(
        execution_id=execution_id,
        case_id=case.case_id,
        scenario_id=case.scenario.template_id,
        prompt=case.benign_task.instruction,
        max_steps=case.budget.max_steps,
        timeout_seconds=case.budget.timeout_seconds,
        seed=case.seed,
        scenario_initialization=initialization.model_dump(mode="json"),
        metadata={"scenario_control": "safe"},
    )
    events = [event async for event in TraceReactAdapter().execute(request)]
    trajectory_root = tmp_path / "trajectories"
    trajectory_root.mkdir(parents=True, exist_ok=True)
    path = trajectory_root / f"{execution_id}.jsonl"
    path.write_text(
        "".join(event.model_dump_json() + "\n" for event in events),
        encoding="utf-8",
    )
    coverage_input = CoverageInputResolver(
        trajectory_root=trajectory_root,
        manifest_root=tmp_path / "replays",
        artifact_root=tmp_path / "artifacts",
    ).from_trajectory_path(path, scenario_initialization=initialization)
    assert coverage_input.scenario_evidence is not None
    assert coverage_input.scenario_evidence.normal_task_completed
    result = CoverageResult(
        trajectory_id=coverage_input.trajectory_id,
        execution_id=coverage_input.execution_id,
        input_digest=coverage_input.input_digest,
        behavior_profile_hash=sha256_digest({"execution_id": execution_id}),
    )
    return coverage_input, result


def test_planner_samples_twelve_and_rotates_all_six_objectives_first() -> None:
    plan = OfficeBaselinePlanner(
        campaign_id="office-baseline-plan-test",
        manifest=OFFICE_V1_CANDIDATE_CATALOG.manifest(),
        random_seed=41,
        agent=FAKE_AGENT,
        budget=ExecutionBudget(),
        catalog=OFFICE_V1_CANDIDATE_CATALOG,
    ).plan()

    assert len(plan.items) == 12
    assert len({item.selection.objective_id for item in plan.items[:6]}) == 6
    assert {item.status for item in plan.items} == {OfficeBaselineStatus.QUEUED}
    assert len({item.candidate.content_digest for item in plan.items}) == 12


def test_initial_baseline_snapshot_has_deterministic_next_item(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        snapshot = store.snapshot()

    baseline = snapshot.baseline_scan
    assert len(baseline.items) == 12
    assert baseline.queued_item_ids == tuple(
        item.baseline_item_id for item in baseline.items
    )
    assert baseline.committed_item_ids == ()
    assert baseline.active_item_id is None
    assert baseline.next_item_id == baseline.items[0].baseline_item_id


def test_lease_is_idempotent_owned_and_exactly_recovered(tmp_path: Path) -> None:
    root = tmp_path / "campaign"
    feedback = _feedback()
    manifest = _manifest(feedback)
    with _store(root, feedback=feedback, manifest=manifest) as store:
        leased = store.lease_next_baseline_item("baseline-worker-a")
        assert leased is not None
        repeated = store.lease_next_baseline_item("baseline-worker-a")
        assert repeated == leased
        leased_snapshot = store.snapshot()
        with pytest.raises(OfficeCampaignStateError, match="another worker"):
            store.lease_next_baseline_item("baseline-worker-b")
        assert store.snapshot() == leased_snapshot

    with _store(root, feedback=feedback, manifest=manifest) as reopened:
        assert reopened.snapshot() == leased_snapshot
        assert reopened.lease_next_baseline_item("baseline-worker-a") == leased


def test_failed_attempt_requeues_after_untried_items_without_progress(
    tmp_path: Path,
) -> None:
    with _store(tmp_path) as store:
        lease = store.lease_next_baseline_item("baseline-worker-a")
        assert lease is not None
        released = store.release_baseline_item(
            lease.lease.lease_token,
            outcome=OfficeBaselineAttemptOutcome.INFRASTRUCTURE_ERROR,
            reason_code="transient_container_start",
        )
        repeated = store.release_baseline_item(
            lease.lease.lease_token,
            outcome=OfficeBaselineAttemptOutcome.INFRASTRUCTURE_ERROR,
            reason_code="transient_container_start",
        )

        assert repeated == released
        assert released.objective_ledger.executed_objective_ids == ()
        failed = released.baseline_scan.items[0]
        assert failed.status == OfficeBaselineStatus.QUEUED
        assert failed.attempt_count == 1
        assert (
            released.baseline_scan.next_item_id
            == released.baseline_scan.items[1].baseline_item_id
        )
        with pytest.raises(OfficeCampaignStateError, match="different evidence"):
            store.release_baseline_item(
                lease.lease.lease_token,
                outcome=OfficeBaselineAttemptOutcome.CASE_FAILURE,
                reason_code="agent_no_submit",
            )
        assert store.snapshot() == released


@pytest.mark.asyncio
async def test_wrong_candidate_cannot_satisfy_active_baseline_lease(
    tmp_path: Path,
) -> None:
    with _store(tmp_path) as store:
        lease = store.lease_next_baseline_item("baseline-worker-a")
        assert lease is not None
        wrong = store.snapshot().baseline_scan.items[1].candidate
        coverage_input, result = await _submitted_input(
            tmp_path / "wrong",
            wrong,
            execution_id="office-baseline-wrong-01",
        )
        before = store.snapshot()
        with pytest.raises(OfficeCampaignStateError, match="leased baseline candidate"):
            store.commit_baseline_episode(
                lease.lease.lease_token, coverage_input, result
            )
        assert store.snapshot() == before


@pytest.mark.asyncio
async def test_baseline_commit_rolls_back_with_snapshot_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _store(tmp_path) as store:
        lease = store.lease_next_baseline_item("baseline-worker-a")
        assert lease is not None
        coverage_input, result = await _submitted_input(
            tmp_path / "episode",
            lease.candidate,
            execution_id="office-baseline-rollback-01",
        )
        leased_snapshot = store.snapshot()
        original = store._persist_snapshot

        def fail_snapshot(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("injected baseline snapshot failure")

        monkeypatch.setattr(store, "_persist_snapshot", fail_snapshot)
        with pytest.raises(RuntimeError, match="injected baseline snapshot failure"):
            store.commit_baseline_episode(
                lease.lease.lease_token, coverage_input, result
            )
        assert store.snapshot() == leased_snapshot
        monkeypatch.setattr(store, "_persist_snapshot", original)
        committed = store.commit_baseline_episode(
            lease.lease.lease_token, coverage_input, result
        )
        assert len(committed.baseline_scan.committed_item_ids) == 1


@pytest.mark.asyncio
async def test_all_twelve_baseline_combinations_commit_in_new_episodes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "campaign"
    feedback = _feedback()
    manifest = _manifest(feedback)
    execution_ids: set[str] = set()
    with _store(root, feedback=feedback, manifest=manifest) as store:
        for index in range(1, 13):
            lease = store.lease_next_baseline_item("baseline-worker-a")
            assert lease is not None
            execution_id = f"office-baseline-complete-{index:02d}"
            coverage_input, result = await _submitted_input(
                tmp_path / f"episode-{index:02d}",
                lease.candidate,
                execution_id=execution_id,
            )
            committed = store.commit_baseline_episode(
                lease.lease.lease_token, coverage_input, result
            )
            repeated = store.commit_baseline_episode(
                lease.lease.lease_token, coverage_input, result
            )
            assert repeated == committed
            execution_ids.add(execution_id)

        assert store.lease_next_baseline_item("baseline-worker-a") is None
        final = store.snapshot()

    assert len(execution_ids) == 12
    assert final.baseline_scan.queued_item_ids == ()
    assert len(final.baseline_scan.committed_item_ids) == 12
    assert final.baseline_scan.active_item_id is None
    assert final.baseline_scan.next_item_id is None
    assert len(final.objective_ledger.executed_objective_ids) == 6
    assert all(
        item.status == OfficeBaselineStatus.COMMITTED
        and item.attempt_count == 1
        for item in final.baseline_scan.items
    )
    committed_objectives = set(final.objective_ledger.executed_objective_ids)
    assert all(
        not frontier.objective_ids
        or committed_objectives.intersection(frontier.objective_ids)
        for frontier in final.risk_frontiers
    )

    with _store(root, feedback=feedback, manifest=manifest) as reopened:
        assert reopened.snapshot() == final
        assert reopened.lease_next_baseline_item("baseline-worker-a") is None


def test_snapshot_detects_baseline_row_tampering(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        store._connection.execute(
            "UPDATE baseline_items SET item_digest = ? WHERE ordinal = 0",
            ("sha256:" + "f" * 64,),
        )
        with pytest.raises(OfficeCampaignStateError, match="baseline item row integrity"):
            store.snapshot()
