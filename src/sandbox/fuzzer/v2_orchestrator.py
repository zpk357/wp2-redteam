"""Single-candidate generation ordering for the Office V2 feedback loop."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import Identifier, OfficeV2Contract, Sha256Digest

from .v2_campaign import CampaignCompletionStatus
from .v2_campaign_loop import choose_next_allocation
from .v2_campaign_state import V2CampaignStateSnapshot
from .v2_feedback import NextGenerationFeedback
from .v2_scheduler import GenerationAllocation

TERMINAL_CAMPAIGN_STATUSES = frozenset(
    {
        CampaignCompletionStatus.SATURATED,
        CampaignCompletionStatus.BUDGET_EXHAUSTED_INCOMPLETE,
        CampaignCompletionStatus.PAUSED,
        CampaignCompletionStatus.CANCELLED,
    }
)


class GenerationClosureKind(StrEnum):
    CANDIDATE_SETTLEMENT = "candidate_settlement"
    NON_EPISODE_SETTLEMENT = "non_episode_settlement"


class GenerationClosureReceipt(OfficeV2Contract):
    campaign_id: Identifier
    generation_index: int = Field(ge=0)
    closure_kind: GenerationClosureKind
    settlement_id: Identifier
    settlement_digest: Sha256Digest
    resulting_state_digest: Sha256Digest
    closure_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"closure_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        if self.closure_digest != sha256_digest(self.digest_payload()):
            raise ValueError("generation closure receipt digest does not match")
        return self


class GenerationDecision(OfficeV2Contract):
    campaign_id: Identifier
    generation_index: int = Field(ge=0)
    input_state_digest: Sha256Digest
    input_feedback_digest: Sha256Digest | None = None
    allocation: GenerationAllocation
    previous_allocation_digest: Sha256Digest | None = None
    decision_changed: bool
    reason_codes: tuple[Identifier, ...] = Field(min_length=1)
    decision_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"decision_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def identity_and_digest_match(self) -> Self:
        if self.generation_index != self.allocation.generation_index:
            raise ValueError("decision and allocation generation differ")
        if self.decision_changed != (
            self.previous_allocation_digest != self.allocation.allocation_digest
        ):
            raise ValueError("decision change flag does not match allocation")
        if self.decision_digest != sha256_digest(self.digest_payload()):
            raise ValueError("generation decision digest does not match")
        return self


def _seal(model_type, payload: dict[str, object], digest_field: str):
    draft = model_type.model_construct(
        **payload, **{digest_field: "sha256:" + "0" * 64}
    )
    return model_type(
        **payload, **{digest_field: sha256_digest(draft.digest_payload())}
    )


def decide_next_generation(
    *,
    campaign_id: str,
    state: V2CampaignStateSnapshot,
    latest_feedback: NextGenerationFeedback | None,
    previous_decision: GenerationDecision | None = None,
    previous_closure: GenerationClosureReceipt | None = None,
) -> GenerationDecision:
    if state.lifecycle.completion_status in TERMINAL_CAMPAIGN_STATUSES:
        raise ValueError("terminal Campaign cannot create another generation")
    if state.lifecycle.counters.generation_index > 0:
        if previous_closure is None:
            raise ValueError("next generation requires prior atomic settlement")
        if previous_closure.resulting_state_digest != state.state_digest:
            raise ValueError("prior settlement does not produce current state")
        if latest_feedback is None:
            raise ValueError("next generation requires latest feedback")
        if latest_feedback.generation_index != state.lifecycle.counters.generation_index:
            raise ValueError("latest feedback generation differs from current state")
    allocation = choose_next_allocation(campaign_id=campaign_id, state=state)
    previous_digest = (
        previous_decision.allocation.allocation_digest
        if previous_decision is not None
        else None
    )
    changed = previous_digest != allocation.allocation_digest
    reasons = (
        "recomputed-from-latest-feedback",
        "allocation-changed" if changed else "allocation-retained-after-recompute",
    )
    return _seal(
        GenerationDecision,
        {
            "campaign_id": campaign_id,
            "generation_index": state.lifecycle.counters.generation_index,
            "input_state_digest": state.state_digest,
            "input_feedback_digest": (
                latest_feedback.feedback_digest if latest_feedback is not None else None
            ),
            "allocation": allocation,
            "previous_allocation_digest": previous_digest,
            "decision_changed": changed,
            "reason_codes": reasons,
        },
        "decision_digest",
    )


def build_generation_closure_receipt(
    *,
    campaign_id: str,
    generation_index: int,
    closure_kind: GenerationClosureKind,
    settlement_id: str,
    settlement_digest: str,
    resulting_state_digest: str,
) -> GenerationClosureReceipt:
    return _seal(
        GenerationClosureReceipt,
        {
            "campaign_id": campaign_id,
            "generation_index": generation_index,
            "closure_kind": closure_kind,
            "settlement_id": settlement_id,
            "settlement_digest": settlement_digest,
            "resulting_state_digest": resulting_state_digest,
        },
        "closure_digest",
    )


__all__ = [
    "GenerationClosureKind",
    "GenerationClosureReceipt",
    "GenerationDecision",
    "TERMINAL_CAMPAIGN_STATUSES",
    "build_generation_closure_receipt",
    "decide_next_generation",
]
