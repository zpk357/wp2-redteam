"""Real Mutator and Docker Agent driver for the shared Office V2 Campaign loop."""

from __future__ import annotations

import asyncio
from typing import Protocol

from sandbox.mutation.v2_brief import build_minimal_fact_brief
from sandbox.mutation.v2_contracts import build_v2_mutation_field_registry
from sandbox.mutation.v2_materializer import (
    SlotMaterializationTarget,
    TextMaterializationOperation,
)
from sandbox.mutation.v2_plan_builder import (
    build_expression_mutation_plan,
    initial_feedback_digest,
)
from sandbox.mutation.v2_preparation import (
    MutationPreparation,
    MutationPreparationState,
    prepare_candidate,
)
from sandbox.mutation.v2_provider import V2MutationProvider
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.fork import (
    infer_office_v2_compatibility_purpose,
    rematerialize_office_v2_scenario_text,
)
from sandbox.scenarios.office_v2.models import OfficeV2Contract

from .v2_campaign import record_non_episode_generation, record_valid_episode
from .v2_campaign_loop import (
    build_v2_coverage_artifact,
    promote_coverage_artifact,
    record_frontier_result,
)
from .v2_campaign_state import (
    V2CampaignStateSnapshot,
    build_campaign_state,
    reserve_campaign_budget,
    reserve_mutation_budget,
    settle_campaign_budget,
    settle_mutation_budget,
)
from .v2_campaign_store import V2CampaignStore
from .v2_corpus import ExecutionCosts
from .v2_feedback import (
    build_finding,
    build_next_generation_feedback,
    build_non_episode_feedback,
)
from .v2_loop_contracts import (
    NonEpisodeDisposition,
    build_candidate_work_from_handoff,
    build_execution_closure_from_oracle,
    build_execution_handoff,
    build_mutation_budget_reservation,
    build_non_episode_settlement,
    build_preparation_cost_settlement,
)
from .v2_orchestrator import (
    GenerationClosureKind,
    build_generation_closure_receipt,
)
from .v2_real_episode import (
    OfficeV2RealEpisodeResult,
    source_attack_case,
)
from .v2_runtime import (
    V2CampaignRunResult,
    V2GenerationAdvance,
    run_or_resume_campaign,
)
from .v2_scheduler import BaselineStatus, update_baseline_item
from .v2_work import (
    AttemptDisposition,
    AttemptReceipt,
    BudgetReservation,
    CandidateSettlement,
    CandidateWorkState,
    seal_work_contract,
)


class RealCampaignBootstrap(OfficeV2Contract):
    initial_state: V2CampaignStateSnapshot
    model_identity_digest: str


class OfficeV2EpisodeRunner(Protocol):
    async def execute(
        self,
        *,
        source_scenario_case_id: str,
        generated_content: str,
        execution_id: str,
        seed: int,
    ) -> OfficeV2RealEpisodeResult: ...


class _RealGenerationDriver:
    def __init__(
        self,
        *,
        store: V2CampaignStore,
        bootstrap: RealCampaignBootstrap,
        mutation_provider: V2MutationProvider,
        episode_runner: OfficeV2EpisodeRunner,
    ) -> None:
        self.store = store
        self.bootstrap = bootstrap
        self.mutation_provider = mutation_provider
        self.episode_runner = episode_runner

    def advance(self, *, campaign_id, decision, state, previous_feedback):
        seed = next(
            item for item in state.corpus.seeds
            if item.seed_id == decision.allocation.parent_seed_id
        )
        execution = next(
            item for item in state.corpus.execution_records
            if item.execution_record_id
            == decision.allocation.supporting_execution_record_id
        )
        parent_candidate = next(
            item for item in state.corpus.materialized_candidates
            if item.materialized_candidate_id == execution.materialized_candidate_id
        )
        feedback_digest = (
            previous_feedback.feedback_digest
            if previous_feedback is not None
            else initial_feedback_digest(
                campaign_id=campaign_id, state_digest=state.state_digest
            )
        )
        plan = build_expression_mutation_plan(
            decision=decision,
            parent_seed=seed,
            supporting_execution=execution,
            feedback_digest=feedback_digest,
            provider_id=self.mutation_provider.provider_id,
            model_identity_digest=self.bootstrap.model_identity_digest,
        )
        self.store.put_allocation(
            campaign_id=campaign_id, allocation=decision.allocation
        )
        mutation_reservation = build_mutation_budget_reservation(
            campaign_id=campaign_id,
            allocation=decision.allocation,
            mutation_plan_digest=plan.plan_digest,
            reserved_tokens=plan.budget.plan_total_token_budget,
            reserved_cost_microunits=plan.budget.reserved_total_cost_microunits,
        )
        mutation_reserved_state = _replace_budget(
            state,
            reserve_mutation_budget(
                state.budget,
                tokens=mutation_reservation.reserved_tokens,
                cost_microunits=mutation_reservation.reserved_cost_microunits,
            ),
        )
        self.store.reserve_mutation(
            reservation=mutation_reservation, next_state=mutation_reserved_state
        )

        source_case = source_attack_case(execution.scenario_case_id)
        canonical_world = load_canonical_world()
        purpose = infer_office_v2_compatibility_purpose(source_case, canonical_world)
        slot = plan.payload_slots[0]
        parent_payload = seed.payload_specs[0]
        delivered = next(
            item for item in parent_candidate.delivered_payloads
            if item.payload_spec_id == parent_payload.payload_spec_id
        )

        def resolve_case_id(parsed) -> str:
            generated = dict(parsed.slot_values)[slot.payload_slot_id]
            return rematerialize_office_v2_scenario_text(
                source_case=source_case,
                canonical_world=canonical_world,
                generated_content=generated,
                purpose=purpose,
                seed=decision.generation_index,
            ).scenario_case.case_id

        brief = build_minimal_fact_brief(
            plan=plan,
            frontier_description=(
                f"Explore frozen frontier {decision.allocation.frontier_id} "
                f"using feedback {feedback_digest}."
            ),
            operator_instructions=("Change only the frozen payload expression.",),
            scenario_facts=(),
            parent_payload_texts=(parent_payload.content,),
        )
        preparation, materialized = asyncio.run(
            prepare_candidate(
                campaign_id=campaign_id,
                plan=plan,
                brief=brief,
                registry=build_v2_mutation_field_registry(),
                provider=self.mutation_provider,
                parent_text_by_slot={slot.payload_slot_id: parent_payload.content},
                scenario_case_id=source_case.case_id,
                scenario_case_id_resolver=resolve_case_id,
                targets=(
                    SlotMaterializationTarget(
                        payload_slot_id=slot.payload_slot_id,
                        resource_id=delivered.resource_id,
                        resource_version=delivered.resource_version,
                        field_path=delivered.field_path,
                        original_content=parent_payload.content,
                        operation=TextMaterializationOperation.REPLACE,
                    ),
                ),
            )
        )
        self.store.put_mutation_preparation(preparation)
        preparation_settlement = build_preparation_cost_settlement(
            reservation=mutation_reservation, preparation=preparation
        )
        prepared_state = _replace_budget(
            mutation_reserved_state,
            settle_mutation_budget(
                mutation_reserved_state.budget,
                reserved_tokens=mutation_reservation.reserved_tokens,
                reserved_cost_microunits=mutation_reservation.reserved_cost_microunits,
                actual=preparation_settlement.actual_costs,
            ),
        )
        self.store.settle_preparation_cost(
            settlement=preparation_settlement, next_state=prepared_state
        )
        if preparation.state is not MutationPreparationState.READY or materialized is None:
            return self._close_non_episode(
                campaign_id=campaign_id,
                decision=decision,
                state=prepared_state,
                preparation=preparation,
                previous_feedback=previous_feedback,
            )

        handoff = build_execution_handoff(
            campaign_id=campaign_id,
            allocation=decision.allocation,
            preparation=preparation,
        )
        agent_reservation = BudgetReservation(
            agent_tokens=1_000_000, elapsed_ms=plan.budget.timeout_ms * 3
        )
        work = build_candidate_work_from_handoff(
            handoff=handoff, reservation=agent_reservation
        )
        episode_reserved_state = _replace_budget(
            prepared_state,
            reserve_campaign_budget(prepared_state.budget, agent_reservation),
        )
        self.store.put_execution_handoff(
            handoff=handoff, work=work, next_state=episode_reserved_state
        )
        self.store.transition_work(work.work_id, state=CandidateWorkState.EXECUTING)
        execution_id = f"v2-generation-{decision.generation_index}-{work.work_id[-12:]}"
        episode = asyncio.run(
            self.episode_runner.execute(
                source_scenario_case_id=execution.scenario_case_id,
                generated_content=materialized.slot_values[0].visible_content,
                execution_id=execution_id,
                seed=decision.generation_index,
            )
        )
        candidate = preparation.materialized_candidate
        assert candidate is not None
        if candidate.scenario_case_id != episode.scenario_case.case_id:
            raise ValueError("prepared candidate and executed scenario identity differ")
        receipt = _successful_receipt(
            work_id=work.work_id,
            manifest_digest=episode.manifest.manifest_digest,
            agent_tokens=episode.agent_tokens,
            elapsed_ms=episode.elapsed_ms,
        )
        self.store.seal_attempt(receipt)
        execution_closure = build_execution_closure_from_oracle(
            work=work,
            candidate=candidate,
            execution_id=execution_id,
            trace_digest=episode.oracle.trace_digest,
            manifest_digest=episode.manifest.manifest_digest,
            oracle_result=episode.oracle.oracle_result,
            submitted=True,
            termination_reason="submit",
            cleanup_confirmed=True,
        )
        artifact = build_v2_coverage_artifact(episode.coverage_input)
        promoted = promote_coverage_artifact(
            campaign_id=campaign_id,
            candidate_id=candidate.materialized_candidate_id,
            artifact=artifact,
            baseline=episode_reserved_state.coverage,
            seed=seed,
            candidate=candidate,
            attempt_receipt_ids=(receipt.attempt_id,),
            costs=receipt.costs,
            corpus_snapshot=episode_reserved_state.corpus,
            frontier_snapshot=episode_reserved_state.frontiers,
            execution_closure=execution_closure,
        )
        coverage_gain = _coverage_gain(promoted.delta)
        next_frontiers = record_frontier_result(
            promoted.frontiers,
            frontier_id=decision.allocation.frontier_id,
            coverage_gain=coverage_gain,
        )
        next_ledger = _advance_baseline(
            episode_reserved_state,
            promoted.execution,
            tuple(item.objective_id for item in promoted.facts.planned_risk.objectives),
        )
        next_state = build_campaign_state(
            coverage=promoted.next_coverage,
            corpus=promoted.corpus.snapshot(),
            frontiers=next_frontiers,
            exposure_ledger=next_ledger,
            budget=settle_campaign_budget(
                episode_reserved_state.budget,
                reservation=agent_reservation,
                actual=receipt.costs,
            ),
            lifecycle=record_valid_episode(
                episode_reserved_state.lifecycle,
                coverage_gain=coverage_gain,
            ),
        )
        finding = build_finding(
            campaign_id=campaign_id,
            facts=promoted.facts,
            delta=promoted.delta,
            execution=promoted.execution,
        )
        feedback = build_next_generation_feedback(
            campaign_id=campaign_id,
            generation_index=next_state.lifecycle.counters.generation_index,
            execution=promoted.execution,
            delta=promoted.delta,
            previous_feedback_digest=(
                previous_feedback.feedback_digest
                if previous_feedback is not None
                else None
            ),
            consecutive_no_gain=not coverage_gain,
        )
        settlement = seal_work_contract(
            CandidateSettlement,
            {
                "settlement_id": f"settlement.{work.work_id[5:]}",
                "work_id": work.work_id,
                "attempt_receipt_ids": (receipt.attempt_id,),
                "execution_record_id": promoted.execution.execution_record_id,
                "coverage_delta_digest": promoted.delta.delta_digest,
                "next_coverage_snapshot_digest": next_state.coverage.snapshot_digest,
                "promotion_decision_digest": sha256_digest(
                    promoted.decision.model_dump(mode="json", exclude_none=False)
                ),
                "corpus_entry_id": (
                    promoted.corpus_entry.corpus_entry_id
                    if promoted.corpus_entry is not None
                    else None
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
        closure = build_generation_closure_receipt(
            campaign_id=campaign_id,
            generation_index=decision.generation_index,
            closure_kind=GenerationClosureKind.CANDIDATE_SETTLEMENT,
            settlement_id=settlement.settlement_id,
            settlement_digest=settlement.settlement_digest,
            resulting_state_digest=next_state.state_digest,
        )
        self.store.transition_work(
            work.work_id,
            state=CandidateWorkState.SEALED,
            sealed_execution_record_id=promoted.execution.execution_record_id,
        )
        self.store.commit_settlement(
            campaign_id=campaign_id,
            settlement=settlement,
            next_state=next_state,
            feedback=feedback,
            finding=finding,
            closure=closure,
        )
        return V2GenerationAdvance(
            next_state=next_state,
            closure=closure,
            feedback=feedback,
            persisted=True,
        )

    def _close_non_episode(
        self,
        *,
        campaign_id,
        decision,
        state,
        preparation: MutationPreparation,
        previous_feedback,
    ) -> V2GenerationAdvance:
        next_state = build_campaign_state(
            coverage=state.coverage,
            corpus=state.corpus,
            frontiers=state.frontiers,
            exposure_ledger=state.exposure_ledger,
            budget=state.budget,
            lifecycle=record_non_episode_generation(state.lifecycle),
        )
        disposition = (
            NonEpisodeDisposition.PREPARATION_REJECTED
            if preparation.state is MutationPreparationState.REJECTED
            else NonEpisodeDisposition.PREPARATION_PAUSED
        )
        settlement = build_non_episode_settlement(
            campaign_id=campaign_id,
            generation_allocation_id=decision.allocation.generation_allocation_id,
            disposition=disposition,
            previous_state=state,
            next_state=next_state,
            actual_costs=ExecutionCosts(),
            preparation=preparation,
        )
        feedback = build_non_episode_feedback(
            campaign_id=campaign_id,
            generation_index=next_state.lifecycle.counters.generation_index,
            reason_code=f"preparation-{preparation.state.value}",
            previous_feedback=previous_feedback,
        )
        closure = build_generation_closure_receipt(
            campaign_id=campaign_id,
            generation_index=decision.generation_index,
            closure_kind=GenerationClosureKind.NON_EPISODE_SETTLEMENT,
            settlement_id=settlement.settlement_id,
            settlement_digest=settlement.settlement_digest,
            resulting_state_digest=next_state.state_digest,
        )
        self.store.commit_non_episode_settlement(
            settlement=settlement,
            next_state=next_state,
            feedback=feedback,
            closure=closure,
        )
        return V2GenerationAdvance(
            next_state=next_state,
            closure=closure,
            feedback=feedback,
            persisted=True,
        )


def _replace_budget(state, budget):
    return build_campaign_state(
        coverage=state.coverage,
        corpus=state.corpus,
        frontiers=state.frontiers,
        exposure_ledger=state.exposure_ledger,
        budget=budget,
        lifecycle=state.lifecycle,
    )


def _successful_receipt(
    *, work_id: str, manifest_digest: str, agent_tokens: int, elapsed_ms: int
):
    payload = {
        "attempt_id": f"attempt.{work_id[5:]}.1",
        "work_id": work_id,
        "attempt_number": 1,
        "disposition": AttemptDisposition.SUCCEEDED,
        "response_digest": manifest_digest,
        "response_byte_count": 0,
        "bounded_summary": "sealed Office V2 recording",
        "costs": ExecutionCosts(agent_tokens=agent_tokens, elapsed_ms=elapsed_ms),
    }
    return seal_work_contract(AttemptReceipt, payload, "receipt_digest")


def _coverage_gain(delta) -> bool:
    return bool(
        delta.new_primary_behavior_features
        or delta.new_primary_scheduling_families
        or delta.new_risk_facets
        or delta.new_risk_objectives
        or delta.new_exposure_stages
        or delta.new_milestone_outcome_bits
        or delta.new_unexpected_violations
        or delta.new_risk_contexts
        or delta.new_behavior_risk_links
    )


def _advance_baseline(state, execution, objective_ids):
    pending = {
        item.objective_id
        for item in state.exposure_ledger.items
        if item.status is BaselineStatus.PENDING
    }
    objective_id = next((item for item in objective_ids if item in pending), None)
    if objective_id is None:
        return state.exposure_ledger
    return update_baseline_item(
        state.exposure_ledger,
        objective_id=objective_id,
        execution_record_id=execution.execution_record_id,
    )


def run_or_resume_real_campaign(
    *,
    store: V2CampaignStore,
    campaign_id: str,
    bootstrap: RealCampaignBootstrap,
    generation_count: int,
    mutation_provider: V2MutationProvider,
    episode_runner: OfficeV2EpisodeRunner,
) -> V2CampaignRunResult:
    return run_or_resume_campaign(
        store=store,
        campaign_id=campaign_id,
        initial_state=bootstrap.initial_state,
        generation_count=generation_count,
        driver=_RealGenerationDriver(
            store=store,
            bootstrap=bootstrap,
            mutation_provider=mutation_provider,
            episode_runner=episode_runner,
        ),
    )


__all__ = ["RealCampaignBootstrap", "run_or_resume_real_campaign"]
