"""The minimal real Coverage -> Corpus -> Frontier -> Scheduler integration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import model_validator

from sandbox.coverage.v2_contracts import build_v2_candidate_batch_baseline
from sandbox.coverage.v2_episode_coverage import (
    V2CandidateEpisode,
    V2CoverageDelta,
    V2CoverageSnapshot,
    V2EpisodeCoverageFacts,
    build_v2_episode_coverage_facts,
    evaluate_v2_candidate_batch,
)
from sandbox.coverage.v2_input import V2CoverageInput
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import OfficeV2Contract, Sha256Digest

from .v2_campaign_state import V2CampaignStateSnapshot
from .v2_corpus import (
    AttackSeed,
    CorpusEntry,
    ExecutionCosts,
    ExecutionRecord,
    MaterializedCandidate,
    SeedKind,
    V2Corpus,
    seal_contract,
)
from .v2_frontier import (
    BehaviorFrontier,
    FrontierKind,
    FrontierSchedulingState,
    MilestoneOutcomeLedger,
    RiskFrontier,
    V2FrontierSnapshot,
    build_behavior_frontier,
    build_frontier_snapshot,
)
from .v2_loop_contracts import ExecutionClosure
from .v2_promotion import (
    PromotionDecision,
    PromotionDisposition,
    PromotionGateFacts,
    classify_v2_promotion,
)
from .v2_scheduler import (
    AllocationLane,
    FrontierOption,
    GenerationAllocation,
    ParentSelectionCandidate,
    SchedulerPolicy,
    choose_frontier,
    select_parent,
)


class V2CoverageArtifact(OfficeV2Contract):
    coverage_input: V2CoverageInput
    episode_facts: V2EpisodeCoverageFacts
    artifact_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json", exclude={"artifact_digest"}, exclude_none=False
        )

    @model_validator(mode="after")
    def facts_and_digest_match(self):
        if self.episode_facts.input_digest != self.coverage_input.input_digest:
            raise ValueError("coverage artifact facts refer to a different input")
        if self.artifact_digest != sha256_digest(self.digest_payload()):
            raise ValueError("coverage artifact digest does not match")
        return self


def build_v2_coverage_artifact(coverage_input: V2CoverageInput) -> V2CoverageArtifact:
    facts = build_v2_episode_coverage_facts(coverage_input)
    payload = {"coverage_input": coverage_input, "episode_facts": facts}
    draft = V2CoverageArtifact.model_construct(
        **payload, artifact_digest="sha256:" + "0" * 64
    )
    return V2CoverageArtifact(
        **payload, artifact_digest=sha256_digest(draft.digest_payload())
    )


def load_v2_coverage_artifact(path: Path | str) -> V2CoverageArtifact:
    return V2CoverageArtifact.model_validate_json(Path(path).read_text(encoding="utf-8"))


@dataclass(frozen=True)
class CoveragePromotionResult:
    facts: V2EpisodeCoverageFacts
    delta: V2CoverageDelta
    next_coverage: V2CoverageSnapshot
    decision: PromotionDecision
    execution: ExecutionRecord
    corpus: V2Corpus
    frontiers: V2FrontierSnapshot
    corpus_entry: CorpusEntry | None


def _merge_risk_frontiers(
    snapshot: V2FrontierSnapshot, facts: V2EpisodeCoverageFacts
) -> tuple[RiskFrontier, ...]:
    objectives = {item.objective_id: item for item in facts.planned_risk.objectives}
    updated: list[RiskFrontier] = []
    for frontier in snapshot.risk_frontiers:
        objective = objectives.get(frontier.objective_id)
        if objective is None:
            updated.append(frontier)
            continue
        milestone_by_id = {item.milestone_id: item for item in objective.milestones}
        milestone = milestone_by_id[frontier.target_milestone_id]
        observed = MilestoneOutcomeLedger(
            attempted_seen=milestone.outcome_bits.attempted_seen,
            blocked_seen=milestone.outcome_bits.blocked_seen,
            realized_seen=milestone.outcome_bits.realized_seen,
        )
        ledger = frontier.outcome_ledger.merge(observed)
        first_open = next(
            (
                item.milestone_id
                for item in objective.milestones
                if not item.outcome_bits.realized_seen
            ),
            None,
        )
        if ledger.realized_seen:
            state = FrontierSchedulingState.COOLING
            reasons = ("milestone-realized",)
        elif frontier.target_milestone_id == first_open:
            state = FrontierSchedulingState.READY
            reasons = ("compatible-promoted-parent",)
        else:
            state = FrontierSchedulingState.AWAITING_PARENT
            reasons = ("milestone-dependency-pending",)
        payload = frontier.model_dump(mode="python", exclude={"frontier_digest"})
        payload.update(
            outcome_ledger=ledger,
            scheduling_state=state,
            state_reason_codes=reasons,
        )
        updated.append(seal_contract(RiskFrontier, payload, "frontier_digest"))
    return tuple(updated)


def _exploration_frontier(
    *, facts: V2EpisodeCoverageFacts, delta: V2CoverageDelta
) -> BehaviorFrontier | None:
    new_keys = set(delta.new_primary_behavior_features)
    feature = next(
        (item for item in facts.behavior.primary_features if item.feature_key_digest in new_keys),
        None,
    )
    if feature is None:
        return None
    return build_behavior_frontier(
        scenario_id="office-workspace-v2",
        behavior_gap_kind="extend-observed-path",
        feature_family=feature.kind,
        behavior_anchor_digest=feature.feature_key_digest,
        gap_descriptor_digest=sha256_digest(
            {"gap": "successor", "anchor": feature.feature_key_digest}
        ),
    )


def record_frontier_result(
    snapshot: V2FrontierSnapshot,
    *,
    frontier_id: str,
    coverage_gain: bool,
) -> V2FrontierSnapshot:
    risk_frontiers = []
    behavior_frontiers = []
    found = False
    for frontier in snapshot.risk_frontiers:
        if frontier.frontier_id != frontier_id:
            risk_frontiers.append(frontier)
            continue
        found = True
        payload = frontier.model_dump(mode="python", exclude={"frontier_digest"})
        payload.update(
            outcome_ledger=frontier.outcome_ledger,
            locally_committed_episodes=frontier.locally_committed_episodes + 1,
            consecutive_no_gain=0 if coverage_gain else frontier.consecutive_no_gain + 1,
            local_budget_used=frontier.local_budget_used + 1,
        )
        risk_frontiers.append(seal_contract(RiskFrontier, payload, "frontier_digest"))
    for frontier in snapshot.behavior_frontiers:
        if frontier.frontier_id != frontier_id:
            behavior_frontiers.append(frontier)
            continue
        found = True
        payload = frontier.model_dump(mode="python", exclude={"frontier_digest"})
        payload.update(
            locally_committed_episodes=frontier.locally_committed_episodes + 1,
            consecutive_no_gain=0 if coverage_gain else frontier.consecutive_no_gain + 1,
            local_budget_used=frontier.local_budget_used + 1,
        )
        behavior_frontiers.append(
            seal_contract(BehaviorFrontier, payload, "frontier_digest")
        )
    if not found:
        raise ValueError("settled frontier is missing from current snapshot")
    return build_frontier_snapshot(
        risk_frontiers=tuple(risk_frontiers),
        behavior_frontiers=tuple(behavior_frontiers),
    )


def promote_coverage_artifact(
    *,
    campaign_id: str,
    candidate_id: str,
    artifact: V2CoverageArtifact,
    baseline: V2CoverageSnapshot,
    seed: AttackSeed,
    candidate: MaterializedCandidate,
    attempt_receipt_ids: tuple[str, ...],
    costs: ExecutionCosts,
    corpus_snapshot,
    frontier_snapshot: V2FrontierSnapshot,
    execution_closure: ExecutionClosure | None = None,
) -> CoveragePromotionResult:
    identity = artifact.coverage_input.behavior_source_facts.identity
    if candidate.seed_id != seed.seed_id:
        raise ValueError("materialized candidate refers to a different seed")
    if candidate.baseline_snapshot_digest != baseline.snapshot_digest:
        raise ValueError("materialized candidate uses a different coverage baseline")
    if execution_closure is not None:
        if (
            execution_closure.materialized_candidate_id
            != candidate.materialized_candidate_id
        ):
            raise ValueError("execution closure refers to a different candidate")
        if not execution_closure.cleanup_confirmed:
            raise ValueError("execution closure has not confirmed cleanup")
    if (
        candidate.scenario_case_id != identity.scenario_case_id
        or candidate.actor_id != identity.actor_id
        or candidate.task_id != identity.task_id
    ):
        raise ValueError("materialized candidate differs from Coverage execution identity")
    batch = build_v2_candidate_batch_baseline(
        campaign_id=campaign_id,
        candidate_set_id=f"candidate-set.{candidate_id}",
        candidate_set_digest=sha256_digest({"candidate_id": candidate_id}),
        candidate_ids=(candidate_id,),
        baseline_snapshot_digest=baseline.snapshot_digest,
    )
    coverage = evaluate_v2_candidate_batch(
        batch_baseline=batch,
        baseline_snapshot=baseline,
        candidates=(
            V2CandidateEpisode(candidate_id=candidate_id, episode_facts=artifact.episode_facts),
        ),
    )
    delta = coverage.deltas[0]
    decision = classify_v2_promotion(
        facts=artifact.episode_facts,
        delta=delta,
        gates=PromotionGateFacts(
            v2_identity_valid=True,
            execution_complete=True,
            oracle_complete=True,
            cleanup_confirmed=True,
            canonical_fact_is_new=(
                artifact.episode_facts.canonical_fact_digest
                not in baseline.canonical_fact_digests
            ),
            baseline_matches=True,
            initialization_overlay_separate=True,
            integrity_valid=True,
        ),
    )
    exposure_stages = ["planned", "delivered"]
    if execution_closure is not None and execution_closure.observed_payload_refs:
        exposure_stages.append("observed")
    if execution_closure is not None and execution_closure.used_payload_refs:
        exposure_stages.append("used")
    execution = seal_contract(
        ExecutionRecord,
        {
            "execution_record_id": f"execution.{candidate_id}",
            "seed_id": seed.seed_id,
            "materialized_candidate_id": candidate.materialized_candidate_id,
            "scenario_case_id": candidate.scenario_case_id,
            "actor_id": candidate.actor_id,
            "task_id": candidate.task_id,
            "resource_binding_digest": candidate.resource_binding_digest,
            "binding_source_digest": candidate.binding_source_digest,
            "comparison_context_digest": candidate.comparison_context_digest,
            "episode_digest": artifact.episode_facts.canonical_fact_digest,
            "manifest_digest": artifact.coverage_input.acquisition.source_digest,
            "oracle_fact_digest": artifact.coverage_input.oracle_facts.oracle_fact_digest,
            "coverage_facts_digest": artifact.episode_facts.episode_coverage_digest,
            "coverage_delta_digest": delta.delta_digest,
            "observed_contribution_keys": tuple(
                sorted(
                    {
                        *delta.new_primary_behavior_features,
                        *decision.risk_contribution_keys,
                    }
                )
            ),
            "observed_payload_refs": (
                execution_closure.observed_payload_refs
                if execution_closure is not None
                else ()
            ),
            "used_payload_refs": (
                execution_closure.used_payload_refs
                if execution_closure is not None
                else ()
            ),
            "exposure_stages": tuple(exposure_stages),
            "utility_disposition": artifact.episode_facts.eligibility.utility_disposition,
            "normal_task_completed": artifact.episode_facts.eligibility.normal_task_completed,
            "submitted": (
                execution_closure.submitted
                if execution_closure is not None
                else artifact.episode_facts.eligibility.submitted
            ),
            "termination_reason": (
                execution_closure.termination_reason
                if execution_closure is not None
                else artifact.episode_facts.eligibility.termination_reason
            ),
            "cleanup_confirmed": (
                execution_closure.cleanup_confirmed
                if execution_closure is not None
                else True
            ),
            "attempt_receipt_ids": attempt_receipt_ids,
            "costs": costs,
        },
        "record_digest",
    )
    corpus = V2Corpus.from_snapshot(corpus_snapshot)
    corpus.add_seed(seed)
    corpus.add_candidate(candidate)
    corpus.add_execution(execution)
    risk_frontiers = _merge_risk_frontiers(frontier_snapshot, artifact.episode_facts)
    behavior_frontiers = list(frontier_snapshot.behavior_frontiers)
    if decision.disposition is PromotionDisposition.EXPLORATION:
        behavior = _exploration_frontier(facts=artifact.episode_facts, delta=delta)
        if behavior is not None:
            behavior_frontiers.append(behavior)
    next_frontiers = build_frontier_snapshot(
        risk_frontiers=risk_frontiers,
        behavior_frontiers=tuple(behavior_frontiers),
    )
    entry = None
    if decision.disposition in {PromotionDisposition.RISK, PromotionDisposition.EXPLORATION}:
        objective_ids = {
            item.objective_id for item in artifact.episode_facts.planned_risk.objectives
        }
        compatible_frontiers = tuple(
            item.frontier_id
            for item in (*next_frontiers.risk_frontiers, *next_frontiers.behavior_frontiers)
            if (
                isinstance(item, RiskFrontier) and item.objective_id in objective_ids
            )
            or isinstance(item, BehaviorFrontier)
        )
        entry = seal_contract(
            CorpusEntry,
            {
                "corpus_entry_id": f"corpus-entry.{candidate_id}",
                "seed_id": seed.seed_id,
                "seed_kind": (
                    SeedKind.RISK
                    if decision.disposition is PromotionDisposition.RISK
                    else SeedKind.EXPLORATION
                ),
                "promotion_reasons": decision.reason_codes,
                "execution_record_ids": (execution.execution_record_id,),
                "risk_contribution_keys": decision.risk_contribution_keys,
                "behavior_contribution_keys": decision.behavior_contribution_keys,
                "frontier_ids": compatible_frontiers,
                "carrier_kinds": (seed.carrier_recipe.carrier_kind,),
                "compatibility_digests": (candidate.binding_source_digest,),
            },
            "entry_digest",
        )
        corpus.add_entry(entry)
    return CoveragePromotionResult(
        facts=artifact.episode_facts,
        delta=delta,
        next_coverage=coverage.next_snapshot,
        decision=decision,
        execution=execution,
        corpus=corpus,
        frontiers=next_frontiers,
        corpus_entry=entry,
    )


def choose_next_allocation(
    *,
    campaign_id: str,
    state: V2CampaignStateSnapshot,
    policy: SchedulerPolicy | None = None,
) -> GenerationAllocation:
    policy = policy or SchedulerPolicy()
    corpus = V2Corpus.from_snapshot(state.corpus)
    ledger_by_objective = {item.objective_id: item for item in state.exposure_ledger.items}
    remaining = state.budget.episode_limit - (
        state.budget.used_episodes + state.budget.reserved_episodes
    )
    options: list[FrontierOption] = []
    frontier_by_id = {}
    for frontier in state.frontiers.risk_frontiers:
        has_parent = any(
            frontier.frontier_id in entry.frontier_ids for entry in state.corpus.entries
        )
        if not has_parent:
            continue
        frontier_by_id[frontier.frontier_id] = frontier
        exposure = ledger_by_objective[frontier.objective_id]
        options.append(
            FrontierOption(
                frontier_kind=FrontierKind.RISK,
                frontier_id=frontier.frontier_id,
                objective_id=frontier.objective_id,
                target_milestone_id=frontier.target_milestone_id,
                scheduling_state=frontier.scheduling_state,
                baseline_pending=exposure.status.value == "pending",
                wait_decisions=frontier.consecutive_no_gain,
                local_budget_remaining=remaining,
                risk_gap_score=3 if not frontier.outcome_ledger.realized_seen else 0,
            )
        )
    for frontier in state.frontiers.behavior_frontiers:
        has_parent = any(
            frontier.frontier_id in entry.frontier_ids for entry in state.corpus.entries
        )
        if not has_parent:
            continue
        frontier_by_id[frontier.frontier_id] = frontier
        options.append(
            FrontierOption(
                frontier_kind=FrontierKind.BEHAVIOR,
                frontier_id=frontier.frontier_id,
                behavior_gap_kind=frontier.behavior_gap_kind,
                feature_family=frontier.feature_family,
                scheduling_state=frontier.scheduling_state,
                wait_decisions=frontier.consecutive_no_gain,
                local_budget_remaining=remaining,
                behavior_rarity_score=1,
            )
        )
    chosen, lane, reasons = choose_frontier(
        options=tuple(options),
        generation_index=state.lifecycle.counters.generation_index,
        policy=policy,
    )
    parent_candidates = []
    seed_by_id = {item.seed_id: item for item in state.corpus.seeds}
    for entry in state.corpus.entries:
        if chosen.frontier_id not in entry.frontier_ids:
            continue
        for execution in corpus.supporting_executions(entry.corpus_entry_id):
            parent_candidates.append(
                ParentSelectionCandidate(
                    corpus_entry=entry,
                    seed=seed_by_id[entry.seed_id],
                    supporting_execution=execution,
                    compatible_frontier_ids=entry.frontier_ids,
                    risk_proximity=3 if entry.seed_kind is SeedKind.RISK else 0,
                    primary_novelty=len(entry.behavior_contribution_keys),
                )
            )
    parent = select_parent(
        frontier_id=chosen.frontier_id, candidates=tuple(parent_candidates)
    )
    if parent is None:
        raise ValueError("selected frontier has no compatible parent")
    frontier = frontier_by_id[chosen.frontier_id]
    allocation_key = {
        "campaign_id": campaign_id,
        "generation": state.lifecycle.counters.generation_index,
        "state": state.state_digest,
        "frontier": chosen.frontier_id,
        "parent": parent.selection_digest,
    }
    return seal_contract(
        GenerationAllocation,
        {
            "generation_allocation_id": (
                "allocation." + sha256_digest(allocation_key).split(":", 1)[1][:24]
            ),
            "generation_index": state.lifecycle.counters.generation_index,
            "frontier_kind": chosen.frontier_kind,
            "frontier_id": chosen.frontier_id,
            "allocation_target_digest": frontier.frontier_digest,
            "parent_seed_id": parent.parent_seed_id,
            "supporting_execution_record_id": parent.supporting_execution_record_id,
            "binding_source_digest": parent.binding_source_digest,
            "allocation_lane": lane if isinstance(lane, AllocationLane) else AllocationLane(lane),
            "reason_codes": reasons,
            "score_components": parent.soft_score_components,
            "coverage_snapshot_digest": state.coverage.snapshot_digest,
            "corpus_digest": state.corpus.snapshot_digest,
            "frontier_digest": state.frontiers.snapshot_digest,
        },
        "allocation_digest",
    )


__all__ = [
    "CoveragePromotionResult",
    "V2CoverageArtifact",
    "build_v2_coverage_artifact",
    "choose_next_allocation",
    "load_v2_coverage_artifact",
    "promote_coverage_artifact",
    "record_frontier_result",
]
