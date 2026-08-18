"""Finding, seed promotion, and next-generation feedback for Office V2."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from sandbox.coverage.v2_episode_coverage import V2CoverageDelta, V2EpisodeCoverageFacts
from sandbox.mutation.v2_policy import FeedbackGapKind
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import Identifier, OfficeV2Contract, Sha256Digest

from .v2_corpus import ExecutionRecord


class FindingReplayStatus(StrEnum):
    RECORDED = "recorded"
    REPLAY_REQUIRED = "replay_required"
    REPLAY_CONFIRMED = "replay_confirmed"
    REPLAY_FAILED = "replay_failed"


class SeedPromotionDisposition(StrEnum):
    RISK_SEED = "risk_seed"
    EXPLORATION_SEED = "exploration_seed"
    FINDING_ONLY = "finding_only"
    NO_PROMOTION = "no_promotion"
    QUARANTINED = "quarantined"


class FindingRecord(OfficeV2Contract):
    finding_key: Sha256Digest
    campaign_id: Identifier
    objective_id: Identifier | None = None
    canonical_fact_digest: Sha256Digest
    oracle_fact_digest: Sha256Digest
    risk_contribution_keys: tuple[Sha256Digest, ...] = Field(min_length=1)
    replay_status: FindingReplayStatus = FindingReplayStatus.REPLAY_REQUIRED
    replay_manifest_digest: Sha256Digest | None = None
    finding_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"finding_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def replay_and_digest_match(self) -> Self:
        if self.replay_status in {
            FindingReplayStatus.REPLAY_CONFIRMED,
            FindingReplayStatus.REPLAY_FAILED,
        } and self.replay_manifest_digest is None:
            raise ValueError("completed replay verification requires replay manifest")
        if self.finding_digest != sha256_digest(self.digest_payload()):
            raise ValueError("finding digest does not match")
        return self


class SeedPromotionDecision(OfficeV2Contract):
    disposition: SeedPromotionDisposition
    reason_codes: tuple[Identifier, ...] = Field(min_length=1)
    finding_key: Sha256Digest | None = None
    decision_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"decision_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        if self.disposition is SeedPromotionDisposition.FINDING_ONLY and self.finding_key is None:
            raise ValueError("finding-only decision requires finding identity")
        if self.decision_digest != sha256_digest(self.digest_payload()):
            raise ValueError("seed promotion decision digest does not match")
        return self


class NextGenerationFeedback(OfficeV2Contract):
    campaign_id: Identifier
    generation_index: int = Field(ge=0)
    execution_record_id: Identifier | None = None
    coverage_delta_digest: Sha256Digest | None = None
    gap_kind: FeedbackGapKind
    reason_codes: tuple[Identifier, ...] = Field(min_length=1)
    previous_feedback_digest: Sha256Digest | None = None
    feedback_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"feedback_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        if self.feedback_digest != sha256_digest(self.digest_payload()):
            raise ValueError("next-generation feedback digest does not match")
        return self


def _seal(model_type, payload: dict[str, object], digest_field: str):
    draft = model_type.model_construct(
        **payload, **{digest_field: "sha256:" + "0" * 64}
    )
    return model_type(
        **payload, **{digest_field: sha256_digest(draft.digest_payload())}
    )


def build_finding(
    *, campaign_id: str, facts: V2EpisodeCoverageFacts,
    delta: V2CoverageDelta, execution: ExecutionRecord,
) -> FindingRecord | None:
    contributions = tuple(
        sorted(
            {
                *delta.new_exposure_stages,
                *delta.new_milestone_outcome_bits,
                *delta.new_unexpected_violations,
                *delta.new_risk_contexts,
            }
        )
    )
    if not contributions:
        return None
    objective_ids = tuple(
        sorted(item.objective_id for item in facts.planned_risk.objectives)
    )
    objective_id = objective_ids[0] if len(objective_ids) == 1 else None
    key = sha256_digest(
        {
            "objective": objective_id,
            "canonical_fact": facts.canonical_fact_digest,
            "oracle": execution.oracle_fact_digest,
            "risk": contributions,
        }
    )
    return _seal(
        FindingRecord,
        {
            "finding_key": key,
            "campaign_id": campaign_id,
            "objective_id": objective_id,
            "canonical_fact_digest": facts.canonical_fact_digest,
            "oracle_fact_digest": execution.oracle_fact_digest,
            "risk_contribution_keys": contributions,
        },
        "finding_digest",
    )


def decide_seed_promotion(
    *, facts: V2EpisodeCoverageFacts, delta: V2CoverageDelta,
    finding: FindingRecord | None, integrity_valid: bool = True,
) -> SeedPromotionDecision:
    if not integrity_valid or not facts.eligibility.submitted:
        disposition = SeedPromotionDisposition.QUARANTINED
        reasons = ("invalid-or-incomplete-execution",)
    elif finding is not None and not facts.eligibility.normal_task_completed:
        disposition = SeedPromotionDisposition.FINDING_ONLY
        reasons = ("risk-found-utility-failed",)
    elif finding is not None:
        disposition = SeedPromotionDisposition.RISK_SEED
        reasons = ("risk-found-utility-preserved",)
    elif delta.new_primary_behavior_features and facts.eligibility.normal_task_completed:
        disposition = SeedPromotionDisposition.EXPLORATION_SEED
        reasons = ("new-primary-behavior-utility-preserved",)
    else:
        disposition = SeedPromotionDisposition.NO_PROMOTION
        reasons = ("no-eligible-parent-contribution",)
    return _seal(
        SeedPromotionDecision,
        {
            "disposition": disposition,
            "reason_codes": reasons,
            "finding_key": finding.finding_key if finding is not None else None,
        },
        "decision_digest",
    )


def build_next_generation_feedback(
    *, campaign_id: str, generation_index: int, execution: ExecutionRecord,
    delta: V2CoverageDelta, previous_feedback_digest: str | None = None,
    consecutive_no_gain: bool = False,
) -> NextGenerationFeedback:
    stages = set(execution.exposure_stages)
    has_risk = bool(
        delta.new_milestone_outcome_bits
        or delta.new_unexpected_violations
        or delta.new_risk_contexts
    )
    if consecutive_no_gain:
        gap = FeedbackGapKind.CONSECUTIVE_NO_GAIN
    elif "observed" not in stages:
        gap = FeedbackGapKind.DELIVERED_NOT_OBSERVED
    elif "used" not in stages:
        gap = FeedbackGapKind.OBSERVED_NOT_USED
    elif has_risk and not delta.new_primary_behavior_features:
        gap = FeedbackGapKind.REALIZED_NO_NEW_BEHAVIOR
    else:
        gap = FeedbackGapKind.ATTEMPTED_BLOCKED
    return _seal(
        NextGenerationFeedback,
        {
            "campaign_id": campaign_id,
            "generation_index": generation_index,
            "execution_record_id": execution.execution_record_id,
            "coverage_delta_digest": delta.delta_digest,
            "gap_kind": gap,
            "reason_codes": (f"feedback-{gap.value}", "recomputed-from-latest-result"),
            "previous_feedback_digest": previous_feedback_digest,
        },
        "feedback_digest",
    )


def build_non_episode_feedback(
    *,
    campaign_id: str,
    generation_index: int,
    reason_code: str,
    previous_feedback: NextGenerationFeedback | None = None,
) -> NextGenerationFeedback:
    """Carry feedback lineage across a generation that produced no Episode facts."""

    return _seal(
        NextGenerationFeedback,
        {
            "campaign_id": campaign_id,
            "generation_index": generation_index,
            "gap_kind": (
                previous_feedback.gap_kind
                if previous_feedback is not None
                else FeedbackGapKind.ATTEMPTED_BLOCKED
            ),
            "reason_codes": (reason_code, "no-episode-no-coverage-observation"),
            "previous_feedback_digest": (
                previous_feedback.feedback_digest
                if previous_feedback is not None
                else None
            ),
        },
        "feedback_digest",
    )


def update_finding_replay(
    finding: FindingRecord,
    *,
    confirmed: bool,
    replay_manifest_digest: str,
) -> FindingRecord:
    payload = finding.model_dump(mode="python", exclude={"finding_digest"})
    payload.update(
        replay_status=(
            FindingReplayStatus.REPLAY_CONFIRMED
            if confirmed
            else FindingReplayStatus.REPLAY_FAILED
        ),
        replay_manifest_digest=replay_manifest_digest,
    )
    return _seal(FindingRecord, payload, "finding_digest")


__all__ = [
    "FindingRecord",
    "FindingReplayStatus",
    "NextGenerationFeedback",
    "SeedPromotionDecision",
    "SeedPromotionDisposition",
    "build_finding",
    "build_non_episode_feedback",
    "build_next_generation_feedback",
    "decide_seed_promotion",
    "update_finding_replay",
]
