"""The minimal real Coverage -> Corpus -> formal risk-pool Scheduler integration."""

from __future__ import annotations

from collections.abc import Callable
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

from .v2_agent_behavior import AgentBehaviorAssessment
from .v2_campaign_state import V2CampaignStateSnapshot
from .v2_corpus import (
    CorpusEntry,
    ExecutableSeedSupport,
    ExecutionCosts,
    ExecutionRecord,
    MaterializedCandidate,
    SeedKind,
    V2Corpus,
    record_parent_result,
    seal_contract,
)
from .v2_loop_contracts import ExecutionClosure
from .v2_promotion import (
    PromotionDecision,
    PromotionDisposition,
    PromotionGateFacts,
    classify_v2_promotion,
    resolve_promotion_decision,
)
from .v2_risk_pool_scheduler import (
    build_formal_risk_allocation,
    build_priority_risk_allocation,
)
from .v2_scheduler import GenerationAllocation
from .v2_seed_pools import FrozenSeed, build_seed_support_catalog
from .v2_strategy import CampaignStrategy
from .v2_target_preservation import TargetPreservationAssessment


class V2CoverageArtifact(OfficeV2Contract):
    coverage_input: V2CoverageInput
    episode_facts: V2EpisodeCoverageFacts
    artifact_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"artifact_digest"}, exclude_none=False)

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
    draft = V2CoverageArtifact.model_construct(**payload, artifact_digest="sha256:" + "0" * 64)
    return V2CoverageArtifact(**payload, artifact_digest=sha256_digest(draft.digest_payload()))


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
    corpus_entry: CorpusEntry | None
    promoted_seed: FrozenSeed | None
    promoted_support: ExecutableSeedSupport | None


def promote_coverage_artifact(
    *,
    campaign_id: str,
    candidate_id: str,
    artifact: V2CoverageArtifact,
    baseline: V2CoverageSnapshot,
    seed: ExecutableSeedSupport | None,
    formal_parent_seed: FrozenSeed,
    candidate: MaterializedCandidate,
    attempt_receipt_ids: tuple[str, ...],
    costs: ExecutionCosts,
    corpus_snapshot,
    execution_closure: ExecutionClosure | None = None,
    behavior_assessment: AgentBehaviorAssessment,
    target_preservation: TargetPreservationAssessment,
    selected_parent_seed_id: str | None = None,
    selected_parent_execution_record_id: str | None = None,
    campaign_strategy: CampaignStrategy | str = CampaignStrategy.COVERAGE_GUIDED,
    promotion_seed_factory: Callable[[], FrozenSeed] | None = None,
    promotion_support_factory: Callable[[], ExecutableSeedSupport] | None = None,
) -> CoveragePromotionResult:
    identity = artifact.coverage_input.behavior_source_facts.identity
    if seed is not None and candidate.seed_id != seed.seed_id:
        raise ValueError("materialized candidate refers to a different seed")
    expected_behavior_seed_id = selected_parent_seed_id or (
        seed.seed_id if seed is not None else None
    )
    if expected_behavior_seed_id is None:
        raise ValueError("promotion requires selected parent seed identity")
    if behavior_assessment.seed_id != expected_behavior_seed_id:
        raise ValueError("behavior assessment refers to a different formal seed")
    if target_preservation.seed_id != expected_behavior_seed_id:
        raise ValueError("target preservation refers to a different formal seed")
    if candidate.baseline_snapshot_digest != baseline.snapshot_digest:
        raise ValueError("materialized candidate uses a different coverage baseline")
    if execution_closure is not None:
        if execution_closure.materialized_candidate_id != candidate.materialized_candidate_id:
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
    strategy = CampaignStrategy(campaign_strategy)
    if strategy is CampaignStrategy.RANDOM_INDEPENDENT and (
        seed is None or seed.generation_depth != 0
    ):
        raise ValueError("random independent Episode seed must be a frozen root seed")
    classified = classify_v2_promotion(
        facts=artifact.episode_facts,
        delta=delta,
        gates=PromotionGateFacts(
            v2_identity_valid=True,
            execution_complete=True,
            oracle_complete=True,
            cleanup_confirmed=True,
            canonical_fact_is_new=(
                artifact.episode_facts.canonical_fact_digest not in baseline.canonical_fact_digests
            ),
            baseline_matches=True,
            initialization_overlay_separate=True,
            integrity_valid=True,
        ),
    )
    decision = resolve_promotion_decision(
        strategy=strategy,
        classified=classified,
        target_preservation_status=target_preservation.status,
        target_match=behavior_assessment.target_match,
        target_oracle_digest=behavior_assessment.target_oracle_digest,
        context_complete=behavior_assessment.context_complete,
        evidence_complete=behavior_assessment.evidence_complete,
        attempted=behavior_assessment.attempted,
        target_behavior_signature=behavior_assessment.target_behavior_signature,
        existing_behavior_keys=frozenset(
            key for item in corpus_snapshot.entries for key in item.behavior_contribution_keys
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
            "seed_id": candidate.seed_id,
            "materialized_candidate_id": candidate.materialized_candidate_id,
            "scenario_case_id": candidate.scenario_case_id,
            "actor_id": candidate.actor_id,
            "task_id": candidate.task_id,
            "resource_binding_digest": candidate.resource_binding_digest,
            "binding_source_digest": candidate.binding_source_digest,
            "comparison_context_digest": candidate.comparison_context_digest,
            "initial_state_digest": behavior_assessment.initial_state_digest,
            "final_state_digest": behavior_assessment.final_state_digest,
            "evidence_bundle_digest": behavior_assessment.evidence_bundle_digest,
            "behavior_source_digest": behavior_assessment.behavior_source_digest,
            "episode_digest": artifact.episode_facts.canonical_fact_digest,
            "manifest_digest": artifact.coverage_input.acquisition.source_digest,
            "oracle_fact_digest": artifact.coverage_input.oracle_facts.oracle_fact_digest,
            "oracle_result_digest": behavior_assessment.oracle_result_digest,
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
                execution_closure.observed_payload_refs if execution_closure is not None else ()
            ),
            "used_payload_refs": (
                execution_closure.used_payload_refs if execution_closure is not None else ()
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
                execution_closure.cleanup_confirmed if execution_closure is not None else True
            ),
            "attempt_receipt_ids": attempt_receipt_ids,
            "costs": costs,
        },
        "record_digest",
    )
    corpus = V2Corpus.from_snapshot(corpus_snapshot)
    entry = None
    promoted_seed = None
    promoted_support = None
    if decision.disposition in {PromotionDisposition.RISK, PromotionDisposition.EXPLORATION}:
        promoted_seed = (
            promotion_seed_factory() if promotion_seed_factory is not None else None
        )
        if promoted_seed is None:
            raise ValueError("promoting an Episode requires a derived FrozenSeed")
        if candidate.seed_id != promoted_seed.seed_id:
            raise ValueError("promoted seed identity differs from Episode candidate")
        if promoted_seed.risk_type is not formal_parent_seed.risk_type:
            raise ValueError("promoted seed changed risk type")
        execution_support = (
            promotion_support_factory() if promotion_support_factory is not None else seed
        )
        if execution_support is None:
            raise ValueError("promoting an Episode requires executable support")
        if execution_support.seed_id != promoted_seed.seed_id:
            raise ValueError("promoted execution support identity differs from FrozenSeed")
        promoted_support = execution_support
        corpus.add_execution_support(execution_support)
        corpus.add_candidate(candidate)
        corpus.add_execution(execution)
        entry = seal_contract(
            CorpusEntry,
            {
                "corpus_entry_id": f"corpus-entry.{candidate_id}",
                "seed_id": promoted_seed.seed_id,
                "seed_kind": (
                    SeedKind.RISK
                    if decision.disposition is PromotionDisposition.RISK
                    else SeedKind.EXPLORATION
                ),
                "promotion_reasons": decision.reason_codes,
                "execution_record_ids": (execution.execution_record_id,),
                "risk_contribution_keys": decision.risk_contribution_keys,
                "behavior_contribution_keys": decision.behavior_contribution_keys,
                "compatibility_digests": (candidate.binding_source_digest,),
            },
            "entry_digest",
        )
        corpus.add_entry(entry)
    if (selected_parent_seed_id is None) != (selected_parent_execution_record_id is None):
        raise ValueError("parent result accounting requires complete selection lineage")
    if selected_parent_seed_id is not None and strategy is CampaignStrategy.COVERAGE_GUIDED:
        record_parent_result(
            corpus,
            parent_seed_id=selected_parent_seed_id,
            supporting_execution_record_id=selected_parent_execution_record_id,
            child_created=entry is not None,
            productive=entry is not None,
            cost_microunits=costs.monetary_microunits,
        )
    return CoveragePromotionResult(
        facts=artifact.episode_facts,
        delta=delta,
        next_coverage=coverage.next_snapshot,
        decision=decision,
        execution=execution,
        corpus=corpus,
        corpus_entry=entry,
        promoted_seed=promoted_seed,
        promoted_support=promoted_support,
    )


def choose_next_allocation(
    *,
    campaign_id: str,
    state: V2CampaignStateSnapshot,
    strategy: CampaignStrategy | str = CampaignStrategy.COVERAGE_GUIDED,
    campaign_seed_value: int = 0,
) -> GenerationAllocation:
    independent = CampaignStrategy(strategy) is CampaignStrategy.RANDOM_INDEPENDENT
    support_catalog = build_seed_support_catalog(
        catalog=state.seed_catalog,
        corpus_snapshot=state.corpus,
    )
    supported_ids = frozenset(item.seed_id for item in support_catalog.supports)
    # Only the guided arm reads the queue; the independent baseline keeps its
    # uniform draw and never observes cross-Episode promotion feedback.
    priority_seed_id = (
        None
        if independent or not state.new_seed_priority_queue
        else state.new_seed_priority_queue[0]
    )
    if priority_seed_id is not None:
        if priority_seed_id not in supported_ids:
            raise ValueError("priority seed has no executable support")
        formal = build_priority_risk_allocation(
            catalog=state.seed_catalog,
            progress_states=state.risk_progress,
            generation_index=state.lifecycle.counters.generation_index,
            campaign_id=campaign_id,
            campaign_seed_value=campaign_seed_value,
            seed_id=priority_seed_id,
        )
    else:
        try:
            formal = build_formal_risk_allocation(
                catalog=state.seed_catalog,
                progress_states=() if independent else state.risk_progress,
                generation_index=state.lifecycle.counters.generation_index,
                campaign_id=campaign_id,
                campaign_seed_value=campaign_seed_value,
                include_derived=not independent,
                prefer_lowest_progress=not independent,
                selectable_seed_ids=supported_ids,
                budget_remaining=state.budget.episode_limit
                - state.budget.used_episodes
                - state.budget.reserved_episodes,
            )
        except ValueError as exc:
            if "no selectable seed" in str(exc):
                raise ValueError("awaiting_parent-no-compatible-parent") from exc
            raise
    support = support_catalog.support_for(formal.parent_seed_id)
    if support is None:
        raise ValueError("formal risk seed has no executable support")
    reasons = (
        ("independent-random-baseline",)
        if independent
        else ("new-seed-priority-queue-head", "one-time-exploration-opportunity")
        if priority_seed_id is not None
        else ("formal-risk-pool-selection", "lowest-risk-type-progress-level")
    )
    return seal_contract(
        GenerationAllocation,
        {
            "generation_allocation_id": (
                "allocation." + formal.allocation_digest.split(":", 1)[1][:24]
            ),
            "generation_index": state.lifecycle.counters.generation_index,
            "risk_type": formal.risk_type,
            "allocation_target_digest": formal.allocation_digest,
            "parent_seed_id": formal.parent_seed_id,
            "executable_seed_id": support.corpus_seed_id,
            "supporting_execution_record_id": support.supporting_record_id,
            "binding_source_digest": support.binding_source_digest,
            "reason_codes": reasons,
            "score_components": (
                ("risk-type-progress-level", int(formal.risk_type_progress_level)),
            ),
            "direction_selection_receipt": formal.direction_selection_receipt,
            "parent_selection_receipt": formal.parent_selection_receipt,
            "coverage_snapshot_digest": state.coverage.snapshot_digest,
            "corpus_digest": state.corpus.snapshot_digest,
            "seed_catalog_digest": formal.catalog_digest,
            "risk_progress_digest": formal.risk_progress_digest,
            "risk_type_progress_level": int(formal.risk_type_progress_level),
            "risk_pool_selection_digest": formal.allocation_digest,
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
]
