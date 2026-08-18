"""Deterministic Office V2 corpus promotion classification."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from sandbox.coverage.v2_episode_coverage import (
    V2CoverageDelta,
    V2EpisodeCoverageFacts,
)
from sandbox.scenarios.office_v2.models import Identifier, OfficeV2Contract


class PromotionDisposition(StrEnum):
    RISK = "risk"
    EXPLORATION = "exploration"
    OBSERVATION_ONLY = "observation-only"
    REJECTED = "rejected"


class PromotionGateFacts(OfficeV2Contract):
    v2_identity_valid: bool
    execution_complete: bool
    oracle_complete: bool
    cleanup_confirmed: bool
    canonical_fact_is_new: bool
    baseline_matches: bool
    initialization_overlay_separate: bool
    integrity_valid: bool


class PromotionDecision(OfficeV2Contract):
    disposition: PromotionDisposition
    reason_codes: tuple[Identifier, ...] = Field(min_length=1)
    risk_contribution_keys: tuple[str, ...] = Field(default_factory=tuple)
    behavior_contribution_keys: tuple[str, ...] = Field(default_factory=tuple)


def classify_v2_promotion(
    *,
    facts: V2EpisodeCoverageFacts,
    delta: V2CoverageDelta,
    gates: PromotionGateFacts,
) -> PromotionDecision:
    if facts.canonical_fact_digest != delta.canonical_fact_digest:
        raise ValueError("promotion facts and delta refer to different execution")
    hard_gates = gates.model_dump(mode="python")
    failed = tuple(sorted(name for name, passed in hard_gates.items() if not passed))
    if not facts.eligibility.submitted:
        failed += ("submitted",)
    if failed:
        return PromotionDecision(
            disposition=PromotionDisposition.REJECTED,
            reason_codes=tuple(f"hard-gate-{item}" for item in sorted(set(failed))),
        )

    risk = tuple(
        sorted(
            {
                *delta.new_exposure_stages,
                *delta.new_milestone_outcome_bits,
                *delta.new_unexpected_violations,
                *delta.new_risk_contexts,
                *delta.new_behavior_risk_links,
            }
        )
    )
    primary = tuple(sorted(delta.new_primary_behavior_features))
    if risk:
        reasons = ["risk-fact-advanced"]
        if facts.eligibility.normal_task_completed:
            reasons.append("normal-task-completed")
        return PromotionDecision(
            disposition=PromotionDisposition.RISK,
            reason_codes=tuple(reasons),
            risk_contribution_keys=risk,
            behavior_contribution_keys=primary,
        )
    if primary and facts.eligibility.normal_task_completed:
        return PromotionDecision(
            disposition=PromotionDisposition.EXPLORATION,
            reason_codes=("new-primary-behavior",),
            behavior_contribution_keys=primary,
        )
    if primary:
        return PromotionDecision(
            disposition=PromotionDisposition.OBSERVATION_ONLY,
            reason_codes=("primary-behavior-without-normal-task",),
            behavior_contribution_keys=primary,
        )
    if delta.new_secondary_diversity_features or delta.new_behavior_profile:
        return PromotionDecision(
            disposition=PromotionDisposition.OBSERVATION_ONLY,
            reason_codes=("secondary-diversity-only",),
        )
    return PromotionDecision(
        disposition=PromotionDisposition.REJECTED,
        reason_codes=("no-new-canonical-coverage",),
    )


__all__ = [
    "PromotionDecision",
    "PromotionDisposition",
    "PromotionGateFacts",
    "classify_v2_promotion",
]
