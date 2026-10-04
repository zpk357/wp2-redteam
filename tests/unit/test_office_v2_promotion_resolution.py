"""Admission-decision tests for the dual coverage feedback design.

Each test pins one sufficient or blocking fact so that a behavioural change
cannot pass silently.
"""

from __future__ import annotations

from sandbox.fuzzer.v2_agent_behavior import TargetMatch
from sandbox.fuzzer.v2_promotion import (
    PromotionDecision,
    PromotionDisposition,
    resolve_promotion_decision,
)
from sandbox.fuzzer.v2_strategy import CampaignStrategy
from sandbox.fuzzer.v2_target_preservation import TargetPreservationStatus
from sandbox.replay.digests import sha256_digest

SIGNATURE = sha256_digest({"label": "target-behavior"})
ORACLE_DIGEST = sha256_digest({"label": "target-oracle"})


def classified(disposition: PromotionDisposition, *reason_codes: str) -> PromotionDecision:
    return PromotionDecision(
        disposition=disposition,
        reason_codes=reason_codes or ("classified",),
        risk_contribution_keys=(sha256_digest({"label": "risk"}),)
        if disposition is PromotionDisposition.RISK
        else (),
        behavior_contribution_keys=(sha256_digest({"label": "primary"}),)
        if disposition in {PromotionDisposition.RISK, PromotionDisposition.EXPLORATION}
        else (),
    )


def resolve(**overrides) -> PromotionDecision:
    arguments: dict[str, object] = {
        "strategy": CampaignStrategy.COVERAGE_GUIDED,
        "classified": classified(PromotionDisposition.RISK, "risk-fact-advanced"),
        "target_preservation_status": TargetPreservationStatus.PRESERVED,
        "target_match": TargetMatch.MATCHED,
        "target_oracle_digest": ORACLE_DIGEST,
        "context_complete": True,
        "evidence_complete": True,
        "attempted": True,
        "target_behavior_signature": SIGNATURE,
        "existing_behavior_keys": frozenset(),
    }
    arguments.update(overrides)
    return resolve_promotion_decision(**arguments)  # type: ignore[arg-type]


def test_new_risk_contribution_promotes_without_an_attempt() -> None:
    decision = resolve(
        classified=classified(PromotionDisposition.RISK, "risk-fact-advanced"),
        attempted=False,
    )

    assert decision.disposition is PromotionDisposition.RISK
    assert decision.risk_contribution_keys
    assert decision.behavior_contribution_keys == (SIGNATURE,)


def test_new_primary_behavior_with_completed_task_promotes_exploration() -> None:
    decision = resolve(
        classified=classified(PromotionDisposition.EXPLORATION, "new-primary-behavior"),
        attempted=False,
    )

    assert decision.disposition is PromotionDisposition.EXPLORATION
    assert decision.behavior_contribution_keys == (SIGNATURE,)


def test_new_primary_behavior_without_completed_task_is_not_promoted() -> None:
    decision = resolve(
        classified=classified(
            PromotionDisposition.NO_PROMOTION, "primary-behavior-without-normal-task"
        ),
        attempted=False,
    )

    assert decision.disposition is PromotionDisposition.NO_PROMOTION
    assert "selected-target-not-attempted" in decision.reason_codes


def test_finding_only_classification_survives_without_an_attempt() -> None:
    decision = resolve(
        classified=classified(PromotionDisposition.FINDING_ONLY, "risk-fact-advanced"),
        attempted=False,
    )

    assert decision.disposition is PromotionDisposition.FINDING_ONLY


def test_attempted_new_target_behavior_keeps_its_promotion() -> None:
    decision = resolve(
        classified=classified(
            PromotionDisposition.NO_PROMOTION, "no-new-canonical-coverage"
        ),
        attempted=True,
    )

    assert decision.disposition is PromotionDisposition.RISK
    assert "new-selected-target-behavior" in decision.reason_codes


def test_drifted_target_is_not_promoted() -> None:
    decision = resolve(target_preservation_status=TargetPreservationStatus.DRIFTED)

    assert decision.disposition is PromotionDisposition.NO_PROMOTION
    assert decision.reason_codes == ("selected-target-drifted",)


def test_unverified_target_preservation_is_not_promoted() -> None:
    decision = resolve(target_preservation_status=TargetPreservationStatus.UNVERIFIED)

    assert decision.disposition is PromotionDisposition.NO_PROMOTION
    assert decision.reason_codes == ("selected-target-preservation-unverified",)


def test_unmatched_target_is_not_promoted() -> None:
    decision = resolve(
        classified=classified(PromotionDisposition.EXPLORATION, "new-primary-behavior"),
        target_match=TargetMatch.UNMATCHED,
    )

    assert decision.disposition is PromotionDisposition.NO_PROMOTION
    assert decision.reason_codes == ("selected-target-unmatched",)


def test_missing_target_oracle_is_not_promoted() -> None:
    decision = resolve(target_oracle_digest=None)

    assert decision.disposition is PromotionDisposition.NO_PROMOTION
    assert decision.reason_codes == ("selected-target-unmatched",)


def test_incomplete_context_is_not_promoted() -> None:
    decision = resolve(context_complete=False)

    assert decision.disposition is PromotionDisposition.NO_PROMOTION
    assert decision.reason_codes == ("selected-target-context-incomplete",)


def test_seen_behavior_signature_is_not_promoted() -> None:
    decision = resolve(
        classified=classified(PromotionDisposition.EXPLORATION, "new-primary-behavior"),
        existing_behavior_keys=frozenset({SIGNATURE}),
    )

    assert decision.disposition is PromotionDisposition.NO_PROMOTION
    assert decision.reason_codes == ("duplicate-selected-target-behavior",)


def test_not_attempted_without_new_coverage_is_not_promoted() -> None:
    decision = resolve(
        classified=classified(
            PromotionDisposition.NO_PROMOTION, "no-new-canonical-coverage"
        ),
        attempted=False,
    )

    assert decision.disposition is PromotionDisposition.NO_PROMOTION
    assert decision.reason_codes == (
        "selected-target-not-attempted",
        "no-new-canonical-coverage",
    )


def test_random_independent_never_promotes() -> None:
    decision = resolve(strategy=CampaignStrategy.RANDOM_INDEPENDENT)

    assert decision.disposition is PromotionDisposition.NO_PROMOTION
    assert decision.reason_codes == ("independent-random-baseline-no-promotion",)


def test_random_independent_never_promotes_even_with_a_new_primary_behavior() -> None:
    decision = resolve(
        strategy=CampaignStrategy.RANDOM_INDEPENDENT,
        classified=classified(PromotionDisposition.EXPLORATION, "new-primary-behavior"),
        attempted=False,
    )

    assert decision.disposition is PromotionDisposition.NO_PROMOTION
    assert decision.reason_codes == ("independent-random-baseline-no-promotion",)


def test_quarantined_classification_is_returned_unchanged() -> None:
    quarantined = classified(
        PromotionDisposition.QUARANTINED, "hard-gate-cleanup_confirmed"
    )
    decision = resolve(classified=quarantined)

    assert decision == quarantined


def test_random_independent_still_reports_classified_contribution_keys() -> None:
    decision = resolve(strategy=CampaignStrategy.RANDOM_INDEPENDENT)

    assert decision.behavior_contribution_keys == (sha256_digest({"label": "primary"}),)
