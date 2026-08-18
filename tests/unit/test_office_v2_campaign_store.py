from __future__ import annotations

from pathlib import Path

import pytest

from sandbox.coverage.v2_episode_coverage import empty_v2_coverage_snapshot
from sandbox.fuzzer.v2_campaign import CampaignLifecycle, record_valid_episode
from sandbox.fuzzer.v2_campaign_state import (
    build_campaign_budget,
    build_campaign_state,
)
from sandbox.fuzzer.v2_campaign_store import V2CampaignStore, V2CampaignStoreError
from sandbox.fuzzer.v2_corpus import ExecutionCosts, V2Corpus, seal_contract
from sandbox.fuzzer.v2_frontier import (
    FrontierKind,
    build_frontier_snapshot,
    compile_risk_frontiers,
)
from sandbox.fuzzer.v2_identity import build_v2_campaign_identity_lock
from sandbox.fuzzer.v2_scheduler import (
    AllocationLane,
    GenerationAllocation,
    new_baseline_exposure_ledger,
)
from sandbox.fuzzer.v2_work import (
    AttemptDisposition,
    AttemptReceipt,
    BudgetReservation,
    CandidateSettlement,
    CandidateWork,
    CandidateWorkState,
    seal_work_contract,
)
from sandbox.replay.digests import sha256_digest


def digest(label: str) -> str:
    return sha256_digest({"label": label})


def build_work(work_id: str = "work-1") -> CandidateWork:
    allocation = build_allocation()
    return seal_work_contract(
        CandidateWork,
        {
            "work_id": work_id,
            "campaign_id": "campaign-1",
            "generation_allocation_id": "allocation-1",
            "generation_allocation_digest": allocation.allocation_digest,
            "comparison_context_digest": digest("comparison"),
            "baseline_snapshot_digest": empty_v2_coverage_snapshot().snapshot_digest,
            "max_attempts": 2,
            "budget_reservation": BudgetReservation(agent_tokens=500),
        },
        "work_digest",
    )


def build_allocation() -> GenerationAllocation:
    return seal_contract(
        GenerationAllocation,
        {
            "generation_allocation_id": "allocation-1",
            "generation_index": 0,
            "frontier_kind": FrontierKind.RISK,
            "frontier_id": "frontier-1",
            "allocation_target_digest": digest("target"),
            "parent_seed_id": "seed-1",
            "supporting_execution_record_id": "execution-parent",
            "binding_source_digest": digest("binding"),
            "allocation_lane": AllocationLane.BASELINE,
            "reason_codes": ("baseline-debt",),
            "coverage_snapshot_digest": empty_v2_coverage_snapshot().snapshot_digest,
            "corpus_digest": digest("corpus"),
            "frontier_digest": digest("frontier"),
        },
        "allocation_digest",
    )


def put_work(store: V2CampaignStore) -> None:
    allocation = build_allocation()
    store.put_allocation(campaign_id="campaign-1", allocation=allocation)
    assert store.load_allocation(allocation.generation_allocation_id) == allocation
    store.put_work(build_work())


def build_receipt(
    *,
    attempt_id: str = "attempt-1",
    disposition: AttemptDisposition = AttemptDisposition.RETRYABLE,
    error_code: str | None = "provider-503",
) -> AttemptReceipt:
    return seal_work_contract(
        AttemptReceipt,
        {
            "attempt_id": attempt_id,
            "work_id": "work-1",
            "attempt_number": 1,
            "disposition": disposition,
            "error_code": error_code,
            "response_digest": digest("response"),
            "response_byte_count": 64,
            "bounded_summary": "bounded",
            "costs": ExecutionCosts(agent_tokens=10, elapsed_ms=5),
        },
        "receipt_digest",
    )


def create_store(path: Path | str) -> V2CampaignStore:
    store = V2CampaignStore(path)
    store.create_campaign(
        campaign_id="campaign-1",
        identity=build_v2_campaign_identity_lock(),
        initial_state=initial_state(),
    )
    return store


def initial_state(*, lifecycle: CampaignLifecycle | None = None):
    return build_campaign_state(
        coverage=empty_v2_coverage_snapshot(),
        corpus=V2Corpus().snapshot(),
        frontiers=build_frontier_snapshot(risk_frontiers=compile_risk_frontiers()),
        exposure_ledger=new_baseline_exposure_ledger(),
        budget=build_campaign_budget(),
        lifecycle=lifecycle or CampaignLifecycle(),
    )


def test_store_uses_wal_and_reopens_with_digest_locked_identity(tmp_path: Path) -> None:
    path = tmp_path / "campaign.db"
    with create_store(path) as store:
        assert store.journal_mode() == "wal"
        expected = store.load_identity("campaign-1")
    with V2CampaignStore(path) as reopened:
        assert reopened.load_identity("campaign-1") == expected


def test_attempt_receipt_is_immutable_and_retryable_work_recovers(tmp_path: Path) -> None:
    with create_store(tmp_path / "campaign.db") as store:
        put_work(store)
        store.transition_work("work-1", state=CandidateWorkState.EXECUTING)
        receipt = build_receipt()
        store.seal_attempt(receipt)
        store.seal_attempt(receipt)
        recovery = store.recover("campaign-1")
        assert recovery["resumable"] == ("work-1",)

        changed = receipt.model_copy(update={"bounded_summary": "changed"})
        with pytest.raises(V2CampaignStoreError, match="immutable"):
            store.seal_attempt(changed)


def test_execution_window_without_receipt_becomes_ambiguous_and_never_resumes(
    tmp_path: Path,
) -> None:
    with create_store(tmp_path / "campaign.db") as store:
        put_work(store)
        store.transition_work("work-1", state=CandidateWorkState.EXECUTING)
        first = store.recover("campaign-1")
        second = store.recover("campaign-1")
        assert first["ambiguous"] == ("work-1",)
        assert second["ambiguous"] == ("work-1",)
        assert store.load_work("work-1").state is CandidateWorkState.AMBIGUOUS


def test_settlement_is_atomic_and_idempotent(tmp_path: Path) -> None:
    snapshot = empty_v2_coverage_snapshot()
    lifecycle = record_valid_episode(CampaignLifecycle(), coverage_gain=True)
    next_state = initial_state(lifecycle=lifecycle)
    with create_store(tmp_path / "campaign.db") as store:
        put_work(store)
        store.transition_work("work-1", state=CandidateWorkState.EXECUTING)
        store.seal_attempt(
            build_receipt(
                disposition=AttemptDisposition.SUCCEEDED,
                error_code=None,
            )
        )
        store.transition_work(
            "work-1",
            state=CandidateWorkState.SEALED,
            sealed_execution_record_id="execution-1",
        )
        settlement = seal_work_contract(
            CandidateSettlement,
            {
                "settlement_id": "settlement-1",
                "work_id": "work-1",
                "attempt_receipt_ids": ("attempt-1",),
                "execution_record_id": "execution-1",
                "coverage_delta_digest": digest("delta"),
                "next_coverage_snapshot_digest": snapshot.snapshot_digest,
                "promotion_decision_digest": digest("promotion"),
                "corpus_entry_id": "entry-1",
                "corpus_snapshot_digest": next_state.corpus.snapshot_digest,
                "frontier_snapshot_digest": next_state.frontiers.snapshot_digest,
                "exposure_ledger_digest": next_state.exposure_ledger.ledger_digest,
                "budget_digest": next_state.budget.budget_digest,
                "lifecycle_digest": next_state.lifecycle_digest,
                "next_campaign_state_digest": next_state.state_digest,
            },
            "settlement_digest",
        )
        assert store.commit_settlement(
            campaign_id="campaign-1",
            settlement=settlement,
            next_state=next_state,
        )
        assert not store.commit_settlement(
            campaign_id="campaign-1",
            settlement=settlement,
            next_state=next_state,
        )
        assert store.generation_index("campaign-1") == 1
        assert store.load_work("work-1").state is CandidateWorkState.COMMITTED


def test_settlement_rejects_receipt_or_execution_lineage_drift() -> None:
    snapshot = empty_v2_coverage_snapshot()
    with create_store(":memory:") as store:
        put_work(store)
        store.transition_work("work-1", state=CandidateWorkState.EXECUTING)
        store.seal_attempt(
            build_receipt(disposition=AttemptDisposition.SUCCEEDED, error_code=None)
        )
        store.transition_work(
            "work-1",
            state=CandidateWorkState.SEALED,
            sealed_execution_record_id="execution-1",
        )
        payload = {
            "settlement_id": "settlement-drift",
            "work_id": "work-1",
            "attempt_receipt_ids": ("other-attempt",),
            "execution_record_id": "execution-1",
            "coverage_delta_digest": digest("delta"),
            "next_coverage_snapshot_digest": snapshot.snapshot_digest,
            "promotion_decision_digest": digest("promotion"),
            "corpus_snapshot_digest": initial_state().corpus.snapshot_digest,
            "frontier_snapshot_digest": initial_state().frontiers.snapshot_digest,
            "exposure_ledger_digest": initial_state().exposure_ledger.ledger_digest,
            "budget_digest": initial_state().budget.budget_digest,
            "lifecycle_digest": initial_state().lifecycle_digest,
            "next_campaign_state_digest": initial_state().state_digest,
        }
        settlement = seal_work_contract(
            CandidateSettlement, payload, "settlement_digest"
        )
        with pytest.raises(V2CampaignStoreError, match="receipt lineage differs"):
            store.commit_settlement(
                campaign_id="campaign-1",
                settlement=settlement,
                next_state=initial_state(),
            )


def test_drifted_identity_cannot_reopen_campaign_state(tmp_path: Path) -> None:
    path = tmp_path / "campaign.db"
    with create_store(path) as store:
        store._db.execute(
            "UPDATE campaign SET identity_digest=? WHERE campaign_id=?",
            (digest("tampered"), "campaign-1"),
        )
        store._db.commit()
        # JSON still validates, but the stored digest column no longer agrees.
        with pytest.raises(V2CampaignStoreError, match="identity digest differs"):
            store.load_identity("campaign-1")
