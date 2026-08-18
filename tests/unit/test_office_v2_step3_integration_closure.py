from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from sandbox.coverage.v2_episode_coverage import empty_v2_coverage_snapshot
from sandbox.fuzzer.v2_campaign import CampaignLifecycle, record_valid_episode
from sandbox.fuzzer.v2_campaign_loop import (
    build_v2_coverage_artifact,
    choose_next_allocation,
    load_v2_coverage_artifact,
    promote_coverage_artifact,
    record_frontier_result,
)
from sandbox.fuzzer.v2_campaign_state import (
    build_campaign_budget,
    build_campaign_state,
    reserve_campaign_budget,
    settle_campaign_budget,
)
from sandbox.fuzzer.v2_campaign_store import V2CampaignStore
from sandbox.fuzzer.v2_corpus import (
    AttackSeed,
    BindingRequirements,
    CarrierRecipe,
    DeliveredPayload,
    ExecutionCosts,
    MaterializedCandidate,
    OriginIntent,
    PayloadSpec,
    V2Corpus,
    seal_contract,
)
from sandbox.fuzzer.v2_frontier import (
    FrontierSchedulingState,
    RiskFrontier,
    build_frontier_snapshot,
    compile_risk_frontiers,
)
from sandbox.fuzzer.v2_identity import build_v2_campaign_identity_lock
from sandbox.fuzzer.v2_scheduler import (
    new_baseline_exposure_ledger,
    update_baseline_item,
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
from tests.unit.test_office_v2_risk_coverage import _coverage_input

CAMPAIGN_ID = "campaign.step3.integration"


def _digest(label: str) -> str:
    return sha256_digest({"label": label})


def _seed_and_candidate(artifact, *, allocation_id: str, candidate_id: str):
    facts = artifact.episode_facts
    objective = facts.planned_risk.objectives[0]
    content = "Review this embedded request against the current delegation before acting."
    payload = PayloadSpec(
        payload_spec_id=f"payload.{candidate_id}",
        content=content,
        carrier_kind="email",
        field_path="body",
        content_digest=sha256_digest({"content": content}),
    )
    seed = seal_contract(
        AttackSeed,
        {
            "seed_id": f"seed.{candidate_id}",
            "payload_specs": (payload,),
            "carrier_recipe": CarrierRecipe(
                entry_kind="indirect_content",
                carrier_kind="email",
                required_field_paths=("body",),
            ),
            "origin_intent": OriginIntent(
                objective_id=objective.objective_id,
                milestone_id=objective.milestones[0].milestone_id,
            ),
            "binding_requirements": BindingRequirements(
                actor_roles=("department-manager",),
                task_blueprint_ids=("task-blueprint.t1",),
                resource_kinds=("mail-message",),
            ),
            "root_seed_id": f"seed.{candidate_id}",
            "generation_depth": 0,
        },
        "seed_content_digest",
    )
    identity = artifact.coverage_input.behavior_source_facts.identity
    candidate = seal_contract(
        MaterializedCandidate,
        {
            "materialized_candidate_id": f"materialized.{candidate_id}",
            "seed_id": seed.seed_id,
            "generation_allocation_id": allocation_id,
            "scenario_case_id": identity.scenario_case_id,
            "actor_id": identity.actor_id,
            "task_id": identity.task_id,
            "resource_binding_digest": _digest("resource-binding"),
            "delivered_payloads": (
                DeliveredPayload(
                    payload_spec_id=payload.payload_spec_id,
                    resource_id="message.apollo.01.1",
                    resource_version="v1",
                    field_path="body",
                    content_digest=payload.content_digest,
                    materialization_evidence_digest=_digest(
                        f"delivered-{candidate_id}"
                    ),
                ),
            ),
            "binding_source_digest": _digest("binding-source"),
            "comparison_context_digest": _digest("comparison-context"),
            "baseline_snapshot_digest": empty_v2_coverage_snapshot().snapshot_digest,
        },
        "materialization_digest",
    )
    return seed, candidate


def _initial_frontiers():
    frontiers = []
    for frontier in compile_risk_frontiers():
        payload = frontier.model_dump(mode="python", exclude={"frontier_digest"})
        payload.update(
            outcome_ledger=frontier.outcome_ledger,
            scheduling_state=FrontierSchedulingState.AWAITING_PARENT,
            state_reason_codes=("no-promoted-parent",),
        )
        frontiers.append(seal_contract(RiskFrontier, payload, "frontier_digest"))
    return build_frontier_snapshot(risk_frontiers=tuple(frontiers))


def _successful_receipt(work_id: str) -> AttemptReceipt:
    return seal_work_contract(
        AttemptReceipt,
        {
            "attempt_id": "attempt.simulated.1",
            "work_id": work_id,
            "attempt_number": 1,
            "disposition": AttemptDisposition.SUCCEEDED,
            "response_digest": _digest("simulated-response"),
            "response_byte_count": 128,
            "bounded_summary": "deterministic simulated Episode result",
            "costs": ExecutionCosts(agent_tokens=25, elapsed_ms=10),
        },
        "receipt_digest",
    )


def test_real_step2_artifact_closes_scheduler_state_and_reopens_identically(
    tmp_path: Path,
) -> None:
    artifact_path = tmp_path / "step2-coverage-artifact.json"
    built_artifact = build_v2_coverage_artifact(_coverage_input())
    artifact_path.write_text(built_artifact.model_dump_json(indent=2), encoding="utf-8")
    artifact = load_v2_coverage_artifact(artifact_path)
    assert artifact == built_artifact

    historical_seed, historical_candidate = _seed_and_candidate(
        artifact,
        allocation_id="allocation.historical",
        candidate_id="historical",
    )
    bootstrap = promote_coverage_artifact(
        campaign_id=CAMPAIGN_ID,
        candidate_id="historical",
        artifact=artifact,
        baseline=empty_v2_coverage_snapshot(),
        seed=historical_seed,
        candidate=historical_candidate,
        attempt_receipt_ids=("attempt.historical.1",),
        costs=ExecutionCosts(agent_tokens=20, elapsed_ms=8),
        corpus_snapshot=V2Corpus().snapshot(),
        frontier_snapshot=_initial_frontiers(),
    )
    assert bootstrap.corpus_entry is not None
    assert bootstrap.corpus_entry.seed_kind.value == "risk"

    ledger = update_baseline_item(
        new_baseline_exposure_ledger(),
        objective_id=artifact.episode_facts.planned_risk.objectives[0].objective_id,
        execution_record_id=bootstrap.execution.execution_record_id,
    )
    lifecycle = record_valid_episode(CampaignLifecycle(), coverage_gain=True)
    state = build_campaign_state(
        coverage=bootstrap.next_coverage,
        corpus=bootstrap.corpus.snapshot(),
        frontiers=bootstrap.frontiers,
        exposure_ledger=ledger,
        budget=build_campaign_budget(
            used_episodes=1,
            consumed=bootstrap.execution.costs,
        ),
        lifecycle=lifecycle,
    )
    db_path = tmp_path / "campaign.db"
    with V2CampaignStore(db_path) as store:
        store.create_campaign(
            campaign_id=CAMPAIGN_ID,
            identity=build_v2_campaign_identity_lock(),
            initial_state=state,
        )
        allocation = choose_next_allocation(campaign_id=CAMPAIGN_ID, state=state)
        reservation = BudgetReservation(agent_tokens=100, elapsed_ms=1000)
        reserved_state = build_campaign_state(
            coverage=state.coverage,
            corpus=state.corpus,
            frontiers=state.frontiers,
            exposure_ledger=state.exposure_ledger,
            budget=reserve_campaign_budget(state.budget, reservation),
            lifecycle=state.lifecycle,
        )
        work = seal_work_contract(
            CandidateWork,
            {
                "work_id": "work.simulated.1",
                "campaign_id": CAMPAIGN_ID,
                "generation_allocation_id": allocation.generation_allocation_id,
                "generation_allocation_digest": allocation.allocation_digest,
                "comparison_context_digest": _digest("comparison-context"),
                "baseline_snapshot_digest": state.coverage.snapshot_digest,
                "max_attempts": 2,
                "budget_reservation": reservation,
            },
            "work_digest",
        )
        store.put_scheduled_work(
            campaign_id=CAMPAIGN_ID,
            allocation=allocation,
            work=work,
            reserved_state=reserved_state,
        )
        store.transition_work(work.work_id, state=CandidateWorkState.EXECUTING)
        receipt = _successful_receipt(work.work_id)
        store.seal_attempt(receipt)

        simulated_candidate = historical_candidate.model_copy(
            update={
                "materialized_candidate_id": "materialized.simulated-1",
                "generation_allocation_id": allocation.generation_allocation_id,
                "baseline_snapshot_digest": state.coverage.snapshot_digest,
                "materialization_digest": "sha256:" + "0" * 64,
            }
        )
        simulated_payload = simulated_candidate.model_dump(
            mode="python", exclude={"materialization_digest"}
        )
        simulated_payload["delivered_payloads"] = historical_candidate.delivered_payloads
        simulated_candidate = seal_contract(
            MaterializedCandidate, simulated_payload, "materialization_digest"
        )
        simulated = promote_coverage_artifact(
            campaign_id=CAMPAIGN_ID,
            candidate_id="simulated-1",
            artifact=artifact,
            baseline=reserved_state.coverage,
            seed=historical_seed,
            candidate=simulated_candidate,
            attempt_receipt_ids=(receipt.attempt_id,),
            costs=receipt.costs,
            corpus_snapshot=reserved_state.corpus,
            frontier_snapshot=reserved_state.frontiers,
        )
        assert simulated.corpus_entry is None
        store.transition_work(
            work.work_id,
            state=CandidateWorkState.SEALED,
            sealed_execution_record_id=simulated.execution.execution_record_id,
        )
        next_frontiers = record_frontier_result(
            simulated.frontiers,
            frontier_id=allocation.frontier_id,
            coverage_gain=False,
        )
        next_lifecycle = record_valid_episode(
            reserved_state.lifecycle, coverage_gain=False
        )
        next_state = build_campaign_state(
            coverage=simulated.next_coverage,
            corpus=simulated.corpus.snapshot(),
            frontiers=next_frontiers,
            exposure_ledger=reserved_state.exposure_ledger,
            budget=settle_campaign_budget(
                reserved_state.budget,
                reservation=reservation,
                actual=receipt.costs,
            ),
            lifecycle=next_lifecycle,
        )
        settlement = seal_work_contract(
            CandidateSettlement,
            {
                "settlement_id": "settlement.simulated.1",
                "work_id": work.work_id,
                "attempt_receipt_ids": (receipt.attempt_id,),
                "execution_record_id": simulated.execution.execution_record_id,
                "coverage_delta_digest": simulated.delta.delta_digest,
                "next_coverage_snapshot_digest": next_state.coverage.snapshot_digest,
                "promotion_decision_digest": sha256_digest(
                    simulated.decision.model_dump(mode="json", exclude_none=False)
                ),
                "corpus_snapshot_digest": next_state.corpus.snapshot_digest,
                "frontier_snapshot_digest": next_state.frontiers.snapshot_digest,
                "exposure_ledger_digest": next_state.exposure_ledger.ledger_digest,
                "budget_digest": next_state.budget.budget_digest,
                "lifecycle_digest": next_state.lifecycle_digest,
                "next_campaign_state_digest": next_state.state_digest,
            },
            "settlement_digest",
        )

        store._db.executescript(
            "CREATE TEMP TRIGGER force_settlement_rollback "
            "BEFORE UPDATE ON campaign BEGIN SELECT RAISE(ABORT, 'forced'); END;"
        )
        with pytest.raises(sqlite3.IntegrityError, match="forced"):
            store.commit_settlement(
                campaign_id=CAMPAIGN_ID,
                settlement=settlement,
                next_state=next_state,
            )
        assert store.load_state(CAMPAIGN_ID) == reserved_state
        assert store.load_work(work.work_id).state is CandidateWorkState.SEALED
        assert store._db.execute("SELECT COUNT(*) FROM settlement").fetchone()[0] == 0
        store._db.execute("DROP TRIGGER force_settlement_rollback")

        assert store.commit_settlement(
            campaign_id=CAMPAIGN_ID,
            settlement=settlement,
            next_state=next_state,
        )
        selected_before_close = choose_next_allocation(
            campaign_id=CAMPAIGN_ID,
            state=store.load_state(CAMPAIGN_ID),
        )

    with V2CampaignStore(db_path) as reopened:
        recovered_state = reopened.load_state(CAMPAIGN_ID)
        selected_after_reopen = choose_next_allocation(
            campaign_id=CAMPAIGN_ID,
            state=recovered_state,
        )
        assert recovered_state == next_state
        assert selected_after_reopen == selected_before_close
        assert reopened.generation_index(CAMPAIGN_ID) == 2
        assert reopened.recover(CAMPAIGN_ID) == {
            "resumable": (),
            "ambiguous": (),
            "sealed_uncommitted": (),
        }
