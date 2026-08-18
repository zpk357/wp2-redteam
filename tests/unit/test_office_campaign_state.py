from __future__ import annotations

from pathlib import Path

import pytest
from app.adapter.trace_react_adapter import TraceReactAdapter
from app.protocol import ExecutionRequest

from sandbox.content_digests import decimalized_sha256_digest
from sandbox.coverage.input import CoverageInputResolver
from sandbox.coverage.models import (
    CampaignCoverageFeedback,
    CampaignRiskScope,
    CoverageResult,
    CoverageSaturationSummary,
    RiskCoverageGap,
    RiskReachability,
    RiskStage,
)
from sandbox.coverage.office_risk import (
    OFFICE_RISK_MAPPING_DIGEST,
    OFFICE_RISK_MAPPING_VERSION,
)
from sandbox.coverage.risk_scope import CampaignRiskScopeIndex
from sandbox.coverage.taxonomy import RiskTaxonomyLoader
from sandbox.fuzzer.models import ScenarioCampaignManifest
from sandbox.scenarios.candidate_generation import (
    OFFICE_V1_CANDIDATE_CATALOG,
    OfficeCandidateCatalog,
)
from sandbox.scenarios.office_campaign_state import (
    FrontierLocalBudget,
    ObjectiveExposureStatus,
    OfficeCampaignStateError,
    OfficeCampaignStateStore,
    RiskFrontierHints,
    RiskFrontierRecoveryStatus,
)
from sandbox.scenarios.office_episode import build_office_episode_initialization
from sandbox.scenarios.office_matrix import OFFICE_V1_TEST_MATRIX
from sandbox.scenarios.office_v1 import (
    CREATE_MEETING_FROM_EMAIL,
    EXTERNAL_RESTRICTED_FILE_SHARE,
    FAKE_AGENT,
    OFFICE_INJECTION_CARRIERS,
    OFFICE_V1,
)


def _risk_scope(
    category_ids: tuple[str, ...] = (
        "data_exfiltration",
        "unauthorized_file_read",
        "unauthorized_resource_deletion",
    ),
) -> CampaignRiskScopeIndex:
    taxonomy = RiskTaxonomyLoader(Path("config/risk-taxonomy.yaml")).load()
    return CampaignRiskScopeIndex(
        CampaignRiskScope(
            scope_version="office-risk-scope-v1",
            taxonomy_version=taxonomy.taxonomy_version,
            categories={
                category_id: RiskReachability(max_reachable_depth=3)
                for category_id in category_ids
            },
        ),
        taxonomy,
    )


def _feedback(
    campaign_id: str = "office-frontier-test",
    *,
    observed_data_exfiltration_depth: int = 0,
    observations: int = 0,
    risk_scope: CampaignRiskScopeIndex | None = None,
) -> CampaignCoverageFeedback:
    locked_scope = risk_scope or _risk_scope()
    gaps = []
    for category_id in locked_scope.category_ids:
        observed = (
            observed_data_exfiltration_depth
            if category_id == "data_exfiltration"
            else 0
        )
        stages = [RiskStage.ATTEMPTED] if observed >= 2 else []
        gaps.append(
            RiskCoverageGap(
                risk_category_id=category_id,
                risk_category_label=locked_scope.taxonomy.get(category_id).label,
                observed_depth=observed,
                observed_execution_depth=observed,
                max_reachable_depth=locked_scope.max_reachable_depth(category_id),
                next_execution_target_depth=(
                    None if observed == 3 else max(2, observed + 1)
                ),
                observed_stages=stages,
                execution_stages=stages,
            )
        )
    return CampaignCoverageFeedback(
        campaign_id=campaign_id,
        taxonomy_version=locked_scope.taxonomy.taxonomy_version,
        taxonomy_digest=locked_scope.taxonomy.digest,
        risk_mapping_version=OFFICE_RISK_MAPPING_VERSION,
        risk_mapping_digest=OFFICE_RISK_MAPPING_DIGEST,
        risk_scope_version=locked_scope.scope_version,
        risk_scope_digest=locked_scope.digest,
        include_empty=True,
        observed_behavior_paths=observations,
        risk_gaps=gaps,
        saturation=CoverageSaturationSummary(
            observations=observations,
            trailing_without_behavior_gain=0,
            max_without_behavior_gain=0,
            trailing_without_execution_risk_gain=0,
            max_without_execution_risk_gain=0,
            trailing_without_any_gain=0,
            max_without_any_gain=0,
        ),
    )


def _manifest(
    feedback: CampaignCoverageFeedback,
    *,
    catalog: OfficeCandidateCatalog = OFFICE_V1_CANDIDATE_CATALOG,
    scheduler_policy_version: str = "frontier-state-v1",
) -> ScenarioCampaignManifest:
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
        scheduler_policy_version=scheduler_policy_version,
        random_seed=41,
        scenario_catalogs=catalog.manifest(),
    )


def _store(
    tmp_path: Path,
    feedback: CampaignCoverageFeedback | None = None,
    *,
    catalog: OfficeCandidateCatalog = OFFICE_V1_CANDIDATE_CATALOG,
    risk_scope: CampaignRiskScopeIndex | None = None,
) -> OfficeCampaignStateStore:
    locked_feedback = feedback or _feedback()
    locked_scope = risk_scope or _risk_scope(
        tuple(gap.risk_category_id for gap in locked_feedback.risk_gaps)
    )
    return OfficeCampaignStateStore(
        tmp_path / "state",
        _manifest(locked_feedback, catalog=catalog),
        locked_feedback,
        agent=FAKE_AGENT,
        risk_scope=locked_scope,
        catalog=catalog,
    )


async def _submitted_office_input(tmp_path: Path):
    case = OFFICE_V1_TEST_MATRIX.attack_cases[0]
    initialization = build_office_episode_initialization(case)
    request = ExecutionRequest(
        execution_id="office-frontier-execution",
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
    path = trajectory_root / "submitted.jsonl"
    path.write_text(
        "".join(event.model_dump_json() + "\n" for event in events),
        encoding="utf-8",
    )
    coverage_input = CoverageInputResolver(
        trajectory_root=trajectory_root,
        manifest_root=tmp_path / "replays",
        artifact_root=tmp_path / "artifacts",
    ).from_trajectory_path(path, scenario_initialization=initialization)
    coverage_result = CoverageResult(
        trajectory_id=coverage_input.trajectory_id,
        execution_id=coverage_input.execution_id,
        input_digest=coverage_input.input_digest,
        behavior_profile_hash="sha256:" + "6" * 64,
    )
    return coverage_input, coverage_result, case


def test_initial_state_covers_locked_objectives_and_risk_frontiers(
    tmp_path: Path,
) -> None:
    feedback = _feedback()
    manifest = _manifest(feedback)
    risk_scope = _risk_scope()
    with OfficeCampaignStateStore(
        tmp_path / "state",
        manifest,
        feedback,
        agent=FAKE_AGENT,
        risk_scope=risk_scope,
    ) as store:
        snapshot = store.snapshot()

    assert snapshot.revision == 0
    assert snapshot.current_feedback_digest == feedback.report_digest
    assert len(snapshot.objective_exposures) == 6
    assert {entry.status for entry in snapshot.objective_exposures} == {
        ObjectiveExposureStatus.UNSEEN
    }
    assert len(snapshot.objective_ledger.unseen_objective_ids) == 6
    assert snapshot.objective_ledger.executed_objective_ids == ()
    assert snapshot.objective_ledger.unreachable_objective_ids == ()
    assert all(entry.compatible_compositions for entry in snapshot.objective_exposures)
    assert {entry.risk_category_id for entry in snapshot.risk_frontiers} == {
        "data_exfiltration",
        "unauthorized_file_read",
        "unauthorized_resource_deletion",
    }
    assert all(
        entry.recovery_status == RiskFrontierRecoveryStatus.READY
        for entry in snapshot.risk_frontiers
    )
    assert snapshot.content_digest is not None


def test_static_incompatibility_is_persisted_as_stable_unreachable_state(
    tmp_path: Path,
) -> None:
    incompatible_catalog = OfficeCandidateCatalog(
        scenario=OFFICE_V1,
        benign_tasks=(CREATE_MEETING_FROM_EMAIL,),
        attack_objectives=(EXTERNAL_RESTRICTED_FILE_SHARE,),
        injection_carriers=(OFFICE_INJECTION_CARRIERS[1],),
        expression_ids=("direct",),
    )
    risk_scope = _risk_scope(("data_exfiltration",))
    feedback = _feedback(
        campaign_id="office-unreachable-test",
        risk_scope=risk_scope,
    )
    with _store(
        tmp_path,
        feedback,
        catalog=incompatible_catalog,
        risk_scope=risk_scope,
    ) as store:
        snapshot = store.snapshot()
        advanced = _feedback(
            campaign_id=feedback.campaign_id,
            observations=1,
            risk_scope=risk_scope,
        )
        with pytest.raises(OfficeCampaignStateError, match="unreachable frontier"):
            store.apply_feedback(
                advanced,
                hints=(RiskFrontierHints(risk_category_id="data_exfiltration"),),
            )
        assert store.snapshot() == snapshot

    exposure = snapshot.objective_exposures[0]
    assert exposure.status == ObjectiveExposureStatus.UNREACHABLE_OR_INCOMPATIBLE
    assert exposure.unreachable_reason_codes
    assert not exposure.compatible_compositions
    frontier = snapshot.risk_frontiers[0]
    assert (
        frontier.recovery_status
        == RiskFrontierRecoveryStatus.UNREACHABLE_OR_INCOMPATIBLE
    )
    assert not frontier.objective_ids
    assert "no_compatible_registered_composition" in frontier.unreachable_reason_codes


@pytest.mark.asyncio
async def test_only_submitted_office_episode_advances_exposure_idempotently(
    tmp_path: Path,
) -> None:
    coverage_input, coverage_result, case = await _submitted_office_input(tmp_path)
    assert case.attack is not None
    with _store(tmp_path) as store:
        before = store.snapshot()
        committed = store.commit_episode(coverage_input, coverage_result)
        repeated = store.commit_episode(coverage_input, coverage_result)

    target = next(
        entry
        for entry in committed.objective_exposures
        if entry.objective_id == case.attack.objective.objective_id
    )
    assert before.revision == 0
    assert committed.revision == 1
    assert repeated == committed
    assert target.status == ObjectiveExposureStatus.EXECUTED
    assert len(target.committed_episodes) == 1
    assert target.objective_id in committed.objective_ledger.executed_objective_ids


@pytest.mark.asyncio
async def test_replay_or_conflicting_duplicate_cannot_advance_exposure(
    tmp_path: Path,
) -> None:
    coverage_input, coverage_result, _case = await _submitted_office_input(tmp_path)
    with _store(tmp_path) as store:
        before = store.snapshot()
        with pytest.raises(OfficeCampaignStateError, match="live office episode"):
            store.commit_episode(
                coverage_input.model_copy(update={"source_kind": "strict_replay"}),
                coverage_result,
            )
        assert store.snapshot() == before
        committed = store.commit_episode(coverage_input, coverage_result)
        conflicting_result = coverage_result.model_copy(update={"combined_delta": 0.5})
        with pytest.raises(OfficeCampaignStateError, match="conflicting episode"):
            store.commit_episode(coverage_input, conflicting_result)
        assert store.snapshot() == committed


@pytest.mark.asyncio
async def test_episode_agent_drift_cannot_advance_exposure(tmp_path: Path) -> None:
    coverage_input, coverage_result, _case = await _submitted_office_input(tmp_path)
    evidence = coverage_input.scenario_evidence
    assert evidence is not None
    changed_case_payload = evidence.test_case.model_dump(
        mode="python", exclude={"content_digest"}
    )
    changed_case_payload["agent"] = evidence.test_case.agent.model_copy(
        update={"model_name": "different-office-agent"}
    )
    changed_case = type(evidence.test_case).model_validate(changed_case_payload)
    changed_evidence_payload = evidence.model_dump(
        mode="python", exclude={"evidence_digest"}
    )
    changed_evidence_payload.update(
        {
            "test_case": changed_case,
            "test_case_digest": changed_case.content_digest,
        }
    )
    changed_evidence = type(evidence).model_validate(changed_evidence_payload)
    changed_input = coverage_input.model_copy(
        update={"scenario_evidence": changed_evidence}
    )

    with _store(tmp_path) as store:
        before = store.snapshot()
        with pytest.raises(OfficeCampaignStateError, match="agent or budget"):
            store.commit_episode(changed_input, coverage_result)
        assert store.snapshot() == before


@pytest.mark.asyncio
async def test_commit_transaction_rolls_back_if_snapshot_persistence_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    coverage_input, coverage_result, _case = await _submitted_office_input(tmp_path)
    with _store(tmp_path) as store:
        original = store._persist_snapshot

        def fail_snapshot(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("injected snapshot failure")

        monkeypatch.setattr(store, "_persist_snapshot", fail_snapshot)
        with pytest.raises(RuntimeError, match="injected snapshot failure"):
            store.commit_episode(coverage_input, coverage_result)
        assert store.snapshot().revision == 0
        monkeypatch.setattr(store, "_persist_snapshot", original)
        assert store.commit_episode(coverage_input, coverage_result).revision == 1


def test_feedback_updates_frontier_state_and_recovers_exactly(tmp_path: Path) -> None:
    initial = _feedback()
    manifest = _manifest(initial)
    risk_scope = _risk_scope()
    updated = _feedback(
        observed_data_exfiltration_depth=2,
        observations=1,
        risk_scope=risk_scope,
    )
    hint = RiskFrontierHints(
        risk_category_id="data_exfiltration",
        parent_seed_ids=("sha256:" + "a" * 64,),
        behavior_gap_ids=("path-gap:mail-read-to-drive-share",),
        local_budget=FrontierLocalBudget(
            episode_limit=4,
            token_limit=8_192,
            episodes_consumed=1,
            tokens_consumed=1_024,
        ),
        cooldown_until_observation=3,
        virtual_runtime=1.5,
    )
    secondary_hint = RiskFrontierHints(
        risk_category_id="unauthorized_file_read",
        parent_seed_ids=("sha256:" + "b" * 64,),
    )
    root = tmp_path / "state"
    with OfficeCampaignStateStore(
        root,
        manifest,
        initial,
        agent=FAKE_AGENT,
        risk_scope=risk_scope,
    ) as store:
        changed = store.apply_feedback(updated, hints=(hint, secondary_hint))
        repeated = store.apply_feedback(updated, hints=(secondary_hint, hint))
        with pytest.raises(OfficeCampaignStateError, match="different frontier hints"):
            store.apply_feedback(
                updated,
                hints=(
                    hint.model_copy(update={"behavior_gap_ids": ("different-gap",)}),
                    secondary_hint,
                ),
            )

    frontier = next(
        entry
        for entry in changed.risk_frontiers
        if entry.risk_category_id == "data_exfiltration"
    )
    assert changed.revision == 1
    assert repeated == changed
    assert frontier.observed_execution_depth == 2
    assert frontier.next_execution_target_depth == 3
    assert frontier.recovery_status == RiskFrontierRecoveryStatus.COOLED
    assert frontier.parent_seed_ids == hint.parent_seed_ids
    assert frontier.behavior_gap_ids == hint.behavior_gap_ids
    assert frontier.local_budget == hint.local_budget

    with OfficeCampaignStateStore(
        root,
        manifest,
        initial,
        agent=FAKE_AGENT,
        risk_scope=risk_scope,
    ) as reopened:
        assert reopened.snapshot() == changed


def test_feedback_identity_or_category_drift_fails_without_state_change(
    tmp_path: Path,
) -> None:
    initial = _feedback()
    with _store(tmp_path, initial) as store:
        before = store.snapshot()
        drifted_payload = initial.model_dump(mode="python", exclude={"report_digest"})
        drifted_payload["taxonomy_digest"] = "sha256:" + "f" * 64
        drifted = CampaignCoverageFeedback.model_validate(drifted_payload)
        with pytest.raises(OfficeCampaignStateError, match="identity mismatch"):
            store.apply_feedback(drifted)
        assert store.snapshot() == before


def test_feedback_depth_or_taxonomy_label_cannot_rewrite_frontier_history(
    tmp_path: Path,
) -> None:
    risk_scope = _risk_scope()
    initial = _feedback(risk_scope=risk_scope)
    advanced = _feedback(
        observed_data_exfiltration_depth=2,
        observations=1,
        risk_scope=risk_scope,
    )
    with _store(tmp_path, initial, risk_scope=risk_scope) as store:
        changed = store.apply_feedback(advanced)
        regressed = _feedback(observations=2, risk_scope=risk_scope)
        with pytest.raises(OfficeCampaignStateError, match="cannot move backwards"):
            store.apply_feedback(regressed)
        assert store.snapshot() == changed

        label_payload = advanced.model_dump(mode="python", exclude={"report_digest"})
        label_payload["observed_behavior_paths"] = 2
        label_payload["risk_gaps"][0]["risk_category_label"] = "tampered label"
        label_drift = CampaignCoverageFeedback.model_validate(label_payload)
        with pytest.raises(OfficeCampaignStateError, match="locked scope"):
            store.apply_feedback(label_drift)
        assert store.snapshot() == changed


def test_reopen_rejects_campaign_manifest_drift(tmp_path: Path) -> None:
    feedback = _feedback()
    risk_scope = _risk_scope()
    root = tmp_path / "state"
    manifest = _manifest(feedback)
    with OfficeCampaignStateStore(
        root,
        manifest,
        feedback,
        agent=FAKE_AGENT,
        risk_scope=risk_scope,
    ):
        pass

    changed = _manifest(feedback, scheduler_policy_version="changed-policy")
    with pytest.raises(OfficeCampaignStateError, match="manifest_digest"):
        OfficeCampaignStateStore(
            root,
            changed,
            feedback,
            agent=FAKE_AGENT,
            risk_scope=risk_scope,
        )


def test_snapshot_detects_persisted_row_tampering(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        row = store._connection.execute(
            "SELECT objective_id, entry_json FROM objective_exposures ORDER BY objective_id LIMIT 1"
        ).fetchone()
        assert row is not None
        payload = row["entry_json"].replace('"revision":0', '"revision":9')
        store._connection.execute(
            "UPDATE objective_exposures SET entry_json = ? WHERE objective_id = ?",
            (payload, row["objective_id"]),
        )
        with pytest.raises(ValueError, match="digest does not match"):
            store.snapshot()


@pytest.mark.parametrize(
    "table,column",
    (
        ("objective_exposures", "entry_digest"),
        ("feedback_applications", "application_digest"),
    ),
)
def test_snapshot_detects_persisted_index_tampering(
    tmp_path: Path, table: str, column: str
) -> None:
    with _store(tmp_path / table) as store:
        store._connection.execute(
            f"UPDATE {table} SET {column} = ?",
            ("sha256:" + "f" * 64,),
        )
        with pytest.raises(OfficeCampaignStateError, match="integrity mismatch|persisted snapshot"):
            store.snapshot()


@pytest.mark.asyncio
async def test_snapshot_detects_committed_episode_index_tampering(
    tmp_path: Path,
) -> None:
    coverage_input, coverage_result, _case = await _submitted_office_input(
        tmp_path / "episode"
    )
    with _store(tmp_path / "store") as store:
        store.commit_episode(coverage_input, coverage_result)
        store._connection.execute(
            "UPDATE committed_episodes SET reference_digest = ?",
            ("sha256:" + "f" * 64,),
        )
        with pytest.raises(OfficeCampaignStateError, match="episode row integrity"):
            store.snapshot()


def test_snapshot_digest_is_content_addressed(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        snapshot = store.snapshot()

    assert snapshot.content_digest == decimalized_sha256_digest(
        snapshot.model_dump(mode="json", exclude={"content_digest"}),
        label="office campaign state digest",
    )
