from __future__ import annotations

from datetime import UTC, datetime
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
from sandbox.scenarios.models import TestCase as ScenarioTestCase
from sandbox.scenarios.office_campaign_completion import (
    OfficeCampaignCompletionPolicy,
    OfficeCampaignCompletionStatus,
    evaluate_office_campaign_completion,
)
from sandbox.scenarios.office_campaign_scheduler import (
    AdaptiveBatchDecision,
    AdaptiveBatchResult,
    AdaptiveDirectionOutcome,
    AdaptiveDirectionResult,
    AdaptiveFrontierInput,
    AdaptiveFrontierStats,
    OfficeAdaptiveSchedulerPolicy,
    schedule_adaptive_batch,
)
from sandbox.scenarios.office_campaign_state import (
    OfficeCampaignStateError,
    OfficeCampaignStateStore,
    RiskFrontierHints,
    RiskFrontierRecoveryStatus,
)
from sandbox.scenarios.office_episode import build_office_episode_initialization
from sandbox.scenarios.office_v1 import FAKE_AGENT


def _risk_scope() -> CampaignRiskScopeIndex:
    taxonomy = RiskTaxonomyLoader(Path("config/risk-taxonomy.yaml")).load()
    return CampaignRiskScopeIndex(
        CampaignRiskScope(
            scope_version="office-scheduler-risk-scope-v1",
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
    *,
    observations: int = 0,
    observed_behavior_paths: int | None = None,
    campaign_id: str = "office-adaptive-scheduler-test",
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
        observed_behavior_paths=(
            observations
            if observed_behavior_paths is None
            else observed_behavior_paths
        ),
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
            observations=observations,
            trailing_without_behavior_gain=observations,
            max_without_behavior_gain=observations,
            trailing_without_execution_risk_gain=observations,
            max_without_execution_risk_gain=observations,
            trailing_without_any_gain=observations,
            max_without_any_gain=observations,
        ),
    )


def _manifest(feedback: CampaignCoverageFeedback) -> ScenarioCampaignManifest:
    return ScenarioCampaignManifest(
        campaign_id=feedback.campaign_id,
        created_at=datetime(2026, 8, 3, tzinfo=UTC),
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
        energy_formula_version="office-adaptive-interleave-v1",
        corpus_policy_version="frontier-state-only-v1",
        scheduler_policy_version="office-adaptive-interleave-v1",
        random_seed=41,
        scenario_catalogs=OFFICE_V1_CANDIDATE_CATALOG.manifest(),
    )


def _store(
    root: Path,
    feedback: CampaignCoverageFeedback,
    *,
    policy: OfficeAdaptiveSchedulerPolicy | None = None,
    completion_policy: OfficeCampaignCompletionPolicy | None = None,
) -> OfficeCampaignStateStore:
    return OfficeCampaignStateStore(
        root / "state",
        _manifest(feedback),
        feedback,
        agent=FAKE_AGENT,
        risk_scope=_risk_scope(),
        scheduler_policy=policy,
        completion_policy=completion_policy,
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
    result = CoverageResult(
        trajectory_id=coverage_input.trajectory_id,
        execution_id=coverage_input.execution_id,
        input_digest=coverage_input.input_digest,
        behavior_profile_hash=sha256_digest({"execution_id": execution_id}),
    )
    return coverage_input, result


async def _complete_baseline(store: OfficeCampaignStateStore, root: Path) -> None:
    for ordinal in range(12):
        lease = store.lease_next_baseline_item("scheduler-baseline-worker")
        assert lease is not None
        coverage_input, result = await _submitted_input(
            root,
            lease.candidate,
            execution_id=f"scheduler-baseline-{ordinal}",
        )
        store.commit_baseline_episode(
            lease.lease.lease_token,
            coverage_input,
            result,
        )
    assert store.lease_next_baseline_item("scheduler-baseline-worker") is None


def _frontier(risk_category_id: str) -> AdaptiveFrontierInput:
    return AdaptiveFrontierInput(
        risk_category_id=risk_category_id,
        observed_execution_depth=0,
        max_reachable_depth=3,
        next_execution_target_depth=2,
        composition_ids=(f"composition-{risk_category_id}",),
        composition_objective_ids=(f"objective-{risk_category_id}",),
        behavior_gap_ids=(f"gap-{risk_category_id}",),
        total_path_risk_cells=4,
        recovery_status="ready",
    )


def test_hard_fairness_precedes_soft_score_and_is_explainable() -> None:
    policy = OfficeAdaptiveSchedulerPolicy(
        batch_size=2,
        max_consecutive_decisions=2,
        starvation_decisions=3,
    )
    frontiers = tuple(_frontier(item) for item in ("a", "b", "c", "d"))
    stats = (
        AdaptiveFrontierStats(
            risk_category_id="a",
            selection_count=5,
            last_selected_decision_index=4,
            consecutive_selected_decisions=2,
        ),
        AdaptiveFrontierStats(
            risk_category_id="b",
            selection_count=1,
            last_selected_decision_index=0,
            consecutive_selected_decisions=0,
        ),
        AdaptiveFrontierStats(
            risk_category_id="c",
            selection_count=1,
            last_selected_decision_index=3,
            consecutive_selected_decisions=0,
        ),
        AdaptiveFrontierStats(
            risk_category_id="d",
            selection_count=1,
            last_selected_decision_index=3,
            consecutive_selected_decisions=0,
        ),
    )

    decision = schedule_adaptive_batch(
        campaign_id="fairness-test",
        random_seed=41,
        policy=policy,
        decision_index=5,
        feedback_digest="sha256:" + "1" * 64,
        input_snapshot_digest="sha256:" + "2" * 64,
        observed_behavior_paths=0,
        frontiers=frontiers,
        stats=stats,
    )

    reasons = {item.risk_category_id: item.allocation_reason for item in decision.directions}
    evidence = {item.risk_category_id: item for item in decision.candidates}
    assert reasons["b"] == "starvation"
    assert "exploration" in reasons.values()
    assert "a" not in reasons
    assert "max_consecutive_share" in evidence["a"].constraint_hits
    assert evidence["b"].score.waiting_age == 1000
    assert evidence["c"].score.path_risk_novelty == 1000


def test_exploration_reserve_and_consecutive_constraint_remain_feasible() -> None:
    policy = OfficeAdaptiveSchedulerPolicy(
        batch_size=2,
        max_consecutive_decisions=1,
        starvation_decisions=1,
    )
    initial = schedule_adaptive_batch(
        campaign_id="reserve-test",
        random_seed=41,
        policy=policy,
        decision_index=0,
        feedback_digest="sha256:" + "1" * 64,
        input_snapshot_digest="sha256:" + "2" * 64,
        observed_behavior_paths=0,
        frontiers=tuple(_frontier(item) for item in ("a", "b", "c")),
        stats=tuple(AdaptiveFrontierStats(risk_category_id=item) for item in ("a", "b", "c")),
    )
    assert {item.allocation_reason for item in initial.directions} == {
        "starvation",
        "exploration",
    }

    capped_stats = tuple(
        AdaptiveFrontierStats(
            risk_category_id=item,
            selection_count=1,
            last_selected_decision_index=0,
            consecutive_selected_decisions=1,
        )
        for item in ("a", "b")
    )
    feasible = schedule_adaptive_batch(
        campaign_id="reserve-test",
        random_seed=41,
        policy=policy,
        decision_index=1,
        feedback_digest="sha256:" + "1" * 64,
        input_snapshot_digest="sha256:" + "3" * 64,
        observed_behavior_paths=0,
        frontiers=tuple(_frontier(item) for item in ("a", "b")),
        stats=capped_stats,
    )
    assert len(feasible.directions) == 2
    assert all(
        "max_consecutive_share_infeasible" in item.constraint_hits
        for item in feasible.candidates
    )


@pytest.mark.asyncio
async def test_persisted_batch_recovers_and_feedback_controls_cooldown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = _feedback()
    manifest = _manifest(initial)
    policy = OfficeAdaptiveSchedulerPolicy(
        policy_version=manifest.scheduler_policy_version,
        cooldown_after_no_gain=1,
    )
    root = tmp_path / "campaign"
    with _store(root, initial, policy=policy) as store:
        with pytest.raises(OfficeCampaignStateError, match="completed fair baseline"):
            store.schedule_next_adaptive_batch()
        await _complete_baseline(store, tmp_path)
        with pytest.raises(OfficeCampaignStateError, match="includes the completed baseline"):
            store.schedule_next_adaptive_batch()
        store.apply_feedback(_feedback(observations=12))
        before_schedule = store.snapshot()
        original_persist = store._persist_snapshot

        def fail_snapshot(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("injected adaptive snapshot failure")

        monkeypatch.setattr(store, "_persist_snapshot", fail_snapshot)
        with pytest.raises(RuntimeError, match="injected adaptive snapshot failure"):
            store.schedule_next_adaptive_batch()
        monkeypatch.setattr(store, "_persist_snapshot", original_persist)
        assert store.snapshot() == before_schedule
        decision = store.schedule_next_adaptive_batch()
        assert len(decision.directions) == 2
        assert store.schedule_next_adaptive_batch() == decision
        with pytest.raises(OfficeCampaignStateError, match="active adaptive decision"):
            store.apply_feedback(_feedback(observations=13))
        scheduled_snapshot = store.snapshot()

    with _store(root, initial, policy=policy) as reopened:
        assert reopened.snapshot() == scheduled_snapshot
        assert reopened.schedule_next_adaptive_batch() == decision
        submitted = decision.directions[0]
        rejected = decision.directions[1]
        result = AdaptiveBatchResult(
            decision_id=decision.decision_id,
            direction_results=(
                AdaptiveDirectionResult(
                    direction_id=submitted.direction_id,
                    outcome=AdaptiveDirectionOutcome.SUBMITTED,
                    token_cost=512,
                    evidence_digest="sha256:" + "6" * 64,
                    reason_code="valid_episode",
                ),
                AdaptiveDirectionResult(
                    direction_id=rejected.direction_id,
                    outcome=AdaptiveDirectionOutcome.CANDIDATE_REJECTED,
                    reason_code="invalid_composition",
                ),
            ),
        )
        incomplete_result = AdaptiveBatchResult(
            decision_id=decision.decision_id,
            direction_results=(result.direction_results[0],),
        )
        with pytest.raises(OfficeCampaignStateError, match="every selected direction"):
            reopened.complete_adaptive_batch(incomplete_result)
        assert reopened.snapshot().adaptive_scheduler.active_decision == decision
        completed = reopened.complete_adaptive_batch(result)
        assert reopened.complete_adaptive_batch(result) == completed
        with pytest.raises(OfficeCampaignStateError, match="fresh coverage feedback"):
            reopened.schedule_next_adaptive_batch()
        after_feedback = reopened.apply_feedback(_feedback(observations=13))
        cooled = next(
            item
            for item in after_feedback.risk_frontiers
            if item.risk_category_id == submitted.risk_category_id
        )
        assert cooled.recovery_status == RiskFrontierRecoveryStatus.COOLED
        stat_map = {
            item.risk_category_id: item
            for item in after_feedback.adaptive_scheduler.frontier_stats
        }
        assert stat_map[submitted.risk_category_id].consecutive_no_gain == 1
        assert stat_map[submitted.risk_category_id].tokens_consumed == 512
        assert stat_map[rejected.risk_category_id].invalid_candidates == 1
        reactivated = reopened.apply_feedback(
            _feedback(observations=14),
            hints=(
                RiskFrontierHints(
                    risk_category_id=submitted.risk_category_id,
                    parent_seed_ids=("sha256:" + "a" * 64,),
                    local_budget=cooled.local_budget,
                    virtual_runtime=cooled.virtual_runtime,
                ),
            ),
        )
        frontier = next(
            item
            for item in reactivated.risk_frontiers
            if item.risk_category_id == submitted.risk_category_id
        )
        assert frontier.recovery_status == RiskFrontierRecoveryStatus.READY
        assert frontier.cooldown_until_observation is None


def test_policy_drift_and_scheduler_row_tampering_are_rejected(tmp_path: Path) -> None:
    feedback = _feedback(campaign_id="office-scheduler-integrity-test")
    root = tmp_path / "campaign"
    store = _store(root, feedback)
    database_path = store.database_path
    snapshot = store.snapshot()
    store.close()

    changed_policy = OfficeAdaptiveSchedulerPolicy(
        policy_version=_manifest(feedback).scheduler_policy_version,
        risk_gap_weight=99,
    )
    with pytest.raises(OfficeCampaignStateError, match="metadata mismatch"):
        _store(root, feedback, policy=changed_policy)

    import sqlite3

    connection = sqlite3.connect(database_path)
    connection.execute(
        "UPDATE adaptive_scheduler SET state_digest = ? WHERE singleton = 1",
        ("sha256:" + "f" * 64,),
    )
    connection.commit()
    connection.close()
    with (
        pytest.raises(OfficeCampaignStateError, match="scheduler state digest"),
        _store(root, feedback) as reopened,
    ):
        reopened.snapshot()
    assert snapshot.adaptive_scheduler.next_decision_index == 0


def _batch_result(
    decision: AdaptiveBatchDecision,
    *,
    digest_character: str,
    token_cost: int = 0,
) -> AdaptiveBatchResult:
    return AdaptiveBatchResult(
        decision_id=decision.decision_id,
        direction_results=tuple(
            AdaptiveDirectionResult(
                direction_id=direction.direction_id,
                outcome=AdaptiveDirectionOutcome.SUBMITTED,
                token_cost=token_cost,
                evidence_digest=(
                    "sha256:"
                    + digest_character * 62
                    + f"{direction.ordinal:02x}"
                ),
                reason_code="valid_submitted_episode",
            )
            for direction in decision.directions
        ),
    )


def test_target_depths_still_require_behavior_no_gain_evidence() -> None:
    policy = OfficeCampaignCompletionPolicy(max_submitted_episodes=20)
    evaluation = evaluate_office_campaign_completion(
        policy=policy,
        baseline_complete=True,
        reachable_frontier_ids=("a", "b"),
        target_depth_reached_ids=("a", "b"),
        frontier_no_gain_counts={"a": 0, "b": 0},
        consecutive_submitted_without_any_gain=0,
        submitted_episode_count=12,
        tokens_consumed=0,
        cost_microunits_consumed=0,
        elapsed_milliseconds_consumed=0,
        has_pending_work=False,
    )

    assert evaluation.status == OfficeCampaignCompletionStatus.BASELINE_COMPLETE

    frontiers = tuple(
        AdaptiveFrontierInput(
            risk_category_id=risk_category_id,
            observed_execution_depth=3,
            max_reachable_depth=3,
            composition_ids=(f"composition-{risk_category_id}",),
            composition_objective_ids=(f"objective-{risk_category_id}",),
            total_path_risk_cells=4,
            recovery_status="target_depth_reached",
        )
        for risk_category_id in ("a", "b")
    )
    decision = schedule_adaptive_batch(
        campaign_id="target-depth-behavior-test",
        random_seed=41,
        policy=OfficeAdaptiveSchedulerPolicy(),
        decision_index=0,
        feedback_digest="sha256:" + "1" * 64,
        input_snapshot_digest="sha256:" + "2" * 64,
        observed_behavior_paths=0,
        frontiers=frontiers,
        stats=tuple(
            AdaptiveFrontierStats(risk_category_id=risk_category_id)
            for risk_category_id in ("a", "b")
        ),
    )

    assert len(decision.directions) == 2
    assert all(item.target_execution_depth == 3 for item in decision.directions)
    assert all(
        "risk_target_depth_reached" in item.constraint_hits
        for item in decision.candidates
    )


def test_completion_state_row_digest_tampering_is_rejected(tmp_path: Path) -> None:
    feedback = _feedback(campaign_id="office-completion-integrity-test")
    with _store(tmp_path / "campaign", feedback) as store:
        store._connection.execute(
            "UPDATE campaign_completion SET state_digest = ? WHERE singleton = 1",
            ("sha256:" + "f" * 64,),
        )
        with pytest.raises(OfficeCampaignStateError, match="completion state digest"):
            store.snapshot()


def test_pause_cancel_and_completion_policy_are_persisted(tmp_path: Path) -> None:
    feedback = _feedback(campaign_id="office-completion-control-test")
    root = tmp_path / "campaign"
    policy = OfficeCampaignCompletionPolicy(max_submitted_episodes=40)
    with _store(root, feedback, completion_policy=policy) as store:
        initial = store.snapshot()
        assert (
            initial.completion.status
            == OfficeCampaignCompletionStatus.BASELINE_INCOMPLETE
        )
        paused = store.pause_campaign("operator_requested")
        assert paused.completion.status == OfficeCampaignCompletionStatus.PAUSED
        assert store.pause_campaign("operator_requested") == paused
        with pytest.raises(OfficeCampaignStateError, match="paused"):
            store.lease_next_baseline_item("blocked-worker")

    with _store(root, feedback, completion_policy=policy) as reopened:
        assert reopened.snapshot() == paused
        cancelled = reopened.cancel_campaign("operator_cancelled")
        assert (
            cancelled.completion.status
            == OfficeCampaignCompletionStatus.CANCELLED
        )
        assert reopened.cancel_campaign("operator_cancelled") == cancelled
        with pytest.raises(OfficeCampaignStateError, match="cancelled"):
            reopened.lease_next_baseline_item("blocked-worker")

    changed_policy = OfficeCampaignCompletionPolicy(max_submitted_episodes=41)
    with pytest.raises(OfficeCampaignStateError, match="metadata mismatch"):
        _store(root, feedback, completion_policy=changed_policy)


@pytest.mark.asyncio
async def test_non_submitted_work_exhausts_budget_without_saturation_evidence(
    tmp_path: Path,
) -> None:
    initial = _feedback(campaign_id="office-completion-budget-test")
    policy = OfficeCampaignCompletionPolicy(
        max_submitted_episodes=500,
        max_tokens=10,
    )
    with _store(tmp_path / "campaign", initial, completion_policy=policy) as store:
        await _complete_baseline(store, tmp_path)
        store.apply_feedback(
            _feedback(
                campaign_id=initial.campaign_id,
                observations=12,
                observed_behavior_paths=12,
            )
        )
        decision = store.schedule_next_adaptive_batch()
        rejected, provider_error = decision.directions
        completed = store.complete_adaptive_batch(
            AdaptiveBatchResult(
                decision_id=decision.decision_id,
                direction_results=(
                    AdaptiveDirectionResult(
                        direction_id=rejected.direction_id,
                        outcome=AdaptiveDirectionOutcome.CANDIDATE_REJECTED,
                        token_cost=6,
                        cost_microunits=100,
                        elapsed_milliseconds=20,
                        reason_code="invalid_composition",
                    ),
                    AdaptiveDirectionResult(
                        direction_id=provider_error.direction_id,
                        outcome=AdaptiveDirectionOutcome.PROVIDER_ERROR,
                        token_cost=5,
                        cost_microunits=200,
                        elapsed_milliseconds=30,
                        reason_code="provider_timeout",
                    ),
                ),
            )
        )
        completion = completed.completion
        assert (
            completion.status
            == OfficeCampaignCompletionStatus.BUDGET_EXHAUSTED_INCOMPLETE
        )
        assert completion.tokens_consumed == 11
        assert completion.cost_microunits_consumed == 300
        assert completion.elapsed_milliseconds_consumed == 50
        assert completion.observations == ()
        assert completion.consecutive_submitted_without_any_gain == 0
        with pytest.raises(OfficeCampaignStateError, match="budget_exhausted"):
            store.schedule_next_adaptive_batch()


@pytest.mark.asyncio
async def test_global_saturation_wins_at_exact_episode_budget_edge(
    tmp_path: Path,
) -> None:
    initial = _feedback(campaign_id="office-completion-saturation-test")
    completion_policy = OfficeCampaignCompletionPolicy(
        minimum_global_no_gain_submitted_episodes=4,
        minimum_frontier_no_gain_submitted_episodes=1,
        max_submitted_episodes=16,
    )
    root = tmp_path / "campaign"
    with _store(
        root,
        initial,
        completion_policy=completion_policy,
    ) as store:
        await _complete_baseline(store, tmp_path)
        store.apply_feedback(
            _feedback(
                campaign_id=initial.campaign_id,
                observations=12,
                observed_behavior_paths=12,
            )
        )
        first = store.schedule_next_adaptive_batch()
        store.complete_adaptive_batch(
            _batch_result(first, digest_character="7")
        )
        after_first = store.apply_feedback(
            _feedback(
                campaign_id=initial.campaign_id,
                observations=14,
                observed_behavior_paths=12,
            )
        )
        assert (
            after_first.completion.consecutive_submitted_without_any_gain == 2
        )
        second = store.schedule_next_adaptive_batch()
        store.complete_adaptive_batch(
            _batch_result(second, digest_character="8")
        )
        saturated = store.apply_feedback(
            _feedback(
                campaign_id=initial.campaign_id,
                observations=16,
                observed_behavior_paths=12,
            )
        )
        selected_risks = {
            direction.risk_category_id
            for decision in (first, second)
            for direction in decision.directions
        }
        assert selected_risks == set(_risk_scope().category_ids)
        assert saturated.completion.submitted_episode_count == 16
        assert (
            "submitted_episode_budget_exhausted"
            in saturated.completion.budget_exhaustion_reason_codes
        )
        assert saturated.completion.status == OfficeCampaignCompletionStatus.SATURATED
        with pytest.raises(OfficeCampaignStateError, match="saturated"):
            store.schedule_next_adaptive_batch()

    with _store(
        root,
        initial,
        completion_policy=completion_policy,
    ) as reopened:
        assert reopened.snapshot() == saturated
