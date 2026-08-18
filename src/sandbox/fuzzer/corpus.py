"""Coverage-backed corpus retention policy and evidence construction."""

from __future__ import annotations

from dataclasses import dataclass

from sandbox.coverage.evidence import execution_evidenced_hits
from sandbox.coverage.models import CoverageResult
from sandbox.fuzzer.models import (
    CorpusEntry,
    CorpusReason,
    Observation,
    SeedRecord,
    SemanticExecutionAlignment,
    WorkItem,
    fuzzer_digest,
)
from sandbox.models import ScoreResult


@dataclass(frozen=True)
class CorpusDecision:
    retain: bool
    reasons: tuple[CorpusReason, ...]
    evidence_event_sequences: tuple[int, ...]


class CorpusPolicy:
    version = "coverage-corpus-v1"

    def evaluate(
        self,
        coverage: CoverageResult,
        score: ScoreResult | None,
        *,
        previous_verdicts: set[str] | None = None,
    ) -> CorpusDecision:
        reasons: list[CorpusReason] = []
        if coverage.new_behavior_count:
            reasons.append(CorpusReason.NEW_BEHAVIOR)
        if coverage.execution_new_risk_count:
            reasons.append(CorpusReason.NEW_RISK_CATEGORY)
        if coverage.execution_risk_depth_changes:
            reasons.append(CorpusReason.RISK_DEPTH_INCREASE)
        verified_categories = set(coverage.execution_verified_risk_categories)
        if any(
            link.novelty_class != "known_pair"
            and link.risk_category_id in verified_categories
            for link in coverage.behavior_risk_links
        ):
            reasons.append(CorpusReason.NEW_BEHAVIOR_RISK_LINK)
        if score and score.verdict == "violation":
            reasons.append(CorpusReason.POLICY_VIOLATION)
        known = previous_verdicts or set()
        if score and known and score.verdict not in known:
            reasons.append(CorpusReason.DIVERGENT_SECURITY_OUTCOME)
        evidence = set(score.evidence_event_sequences if score else [])
        for hit in execution_evidenced_hits(coverage.risk_hits):
            evidence.update(
                reference.event_sequence
                for reference in hit.evidence
                if reference.event_sequence is not None
            )
        return CorpusDecision(
            retain=bool(reasons),
            reasons=tuple(dict.fromkeys(reasons)),
            evidence_event_sequences=tuple(sorted(evidence)),
        )

    def build_entry(
        self,
        *,
        campaign_id: str,
        iteration: int,
        seed: SeedRecord,
        work: WorkItem,
        coverage: CoverageResult,
        score: ScoreResult | None,
        decision: CorpusDecision,
        semantic_alignment: SemanticExecutionAlignment | None = None,
    ) -> CorpusEntry:
        if not decision.retain or not work.trajectory_id:
            raise ValueError("retained corpus entry requires an interesting terminal trajectory")
        candidate_id = work.source.candidate_id
        identity = {
            "campaign_id": campaign_id,
            "work_item_id": work.work_item_id,
            "coverage_result_digest": fuzzer_digest(coverage),
            "policy_version": self.version,
        }
        return CorpusEntry(
            corpus_entry_id=fuzzer_digest(identity),
            campaign_id=campaign_id,
            seed_id=seed.seed_id,
            candidate_id=candidate_id,
            work_item_id=work.work_item_id,
            trajectory_id=work.trajectory_id,
            coverage_result_digest=fuzzer_digest(coverage),
            reasons=list(decision.reasons),
            evidence_event_sequences=list(decision.evidence_event_sequences),
            behavior_profile_hash=coverage.behavior_profile_hash,
            risk_categories=coverage.execution_verified_risk_categories,
            max_risk_depth=max(
                coverage.execution_verified_risk_depths.values(),
                default=0,
            ),
            score_verdict=score.verdict if score else None,
            replay_id=work.replay_id,
            created_iteration=iteration,
            semantic_alignment=semantic_alignment,
        )

    @staticmethod
    def build_observation(
        *,
        campaign_id: str,
        iteration: int,
        seed_id: str | None,
        work: WorkItem,
        coverage: CoverageResult,
        score: ScoreResult | None,
        semantic_alignment: SemanticExecutionAlignment | None = None,
    ) -> Observation:
        if not work.trajectory_id:
            raise ValueError("observation requires a trajectory")
        digest = fuzzer_digest(coverage)
        return Observation(
            observation_id=fuzzer_digest(
                {"campaign_id": campaign_id, "work_item_id": work.work_item_id, "coverage": digest}
            ),
            campaign_id=campaign_id,
            work_item_id=work.work_item_id,
            seed_id=seed_id,
            trajectory_id=work.trajectory_id,
            coverage_result_digest=digest,
            behavior_profile_hash=coverage.behavior_profile_hash,
            risk_categories=coverage.execution_verified_risk_categories,
            max_risk_depth=max(
                coverage.execution_verified_risk_depths.values(),
                default=0,
            ),
            score_verdict=score.verdict if score else None,
            behavior_delta=coverage.behavior_delta,
            risk_delta=coverage.execution_risk_seed_delta,
            combined_delta=coverage.execution_combined_delta,
            created_iteration=iteration,
            semantic_alignment=semantic_alignment,
        )
