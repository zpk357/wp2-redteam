"""Shared run/resume loop for deterministic and real-model Office V2 Campaigns."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from pydantic import Field

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import OfficeV2Contract

from .v2_campaign_state import V2CampaignStateSnapshot
from .v2_campaign_store import V2CampaignStore
from .v2_feedback import NextGenerationFeedback
from .v2_identity import build_v2_campaign_identity_lock
from .v2_orchestrator import (
    GenerationClosureReceipt,
    GenerationDecision,
    decide_next_generation,
)
from .v2_report import build_v2_campaign_report

V2_CAMPAIGN_MAX_GENERATIONS = 50
V2_CAMPAIGN_RESUME_MILESTONES = (2, 10, 20, 30, 50)


class V2GenerationAdvance(OfficeV2Contract):
    next_state: V2CampaignStateSnapshot
    closure: GenerationClosureReceipt
    feedback: NextGenerationFeedback
    persisted: bool = False


class V2GenerationDriver(Protocol):
    def advance(
        self,
        *,
        campaign_id: str,
        decision: GenerationDecision,
        state: V2CampaignStateSnapshot,
        previous_feedback: NextGenerationFeedback | None,
    ) -> V2GenerationAdvance | None: ...


class V2CampaignRunResult(OfficeV2Contract):
    campaign_id: str
    requested_generation_count: int = Field(ge=1, le=V2_CAMPAIGN_MAX_GENERATIONS)
    completed_generation_count: int = Field(ge=0, le=V2_CAMPAIGN_MAX_GENERATIONS)
    final_state_digest: str
    decision_digests: tuple[str, ...]
    feedback_digests: tuple[str, ...]
    resumed: bool
    result_digest: str

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"result_digest"}, exclude_none=False)


def run_or_resume_campaign(
    *,
    store: V2CampaignStore,
    campaign_id: str,
    initial_state: V2CampaignStateSnapshot,
    generation_count: int,
    driver: V2GenerationDriver,
    progress_callback: Callable[[V2CampaignRunResult], None] | None = None,
    runtime_identity_digest: str | None = None,
) -> V2CampaignRunResult:
    if not 1 <= generation_count <= V2_CAMPAIGN_MAX_GENERATIONS:
        raise ValueError("generation_count must be between 1 and 50")
    resumed = store.campaign_exists(campaign_id)
    store.create_campaign(
        campaign_id=campaign_id,
        identity=build_v2_campaign_identity_lock(),
        initial_state=initial_state,
    )
    if runtime_identity_digest is not None:
        store.bind_runtime_identity(
            campaign_id, identity_digest=runtime_identity_digest
        )
    state = store.load_state(campaign_id)
    if state.lifecycle.counters.generation_index > generation_count:
        raise ValueError("stored Campaign is beyond requested generation count")

    while state.lifecycle.counters.generation_index < generation_count:
        if state.lifecycle.completion_status is not None:
            break
        generation = state.lifecycle.counters.generation_index
        previous_decision = store.load_latest_generation_decision(campaign_id)
        previous_closure = store.load_latest_generation_closure(campaign_id)
        previous_feedback = store.load_latest_feedback(campaign_id)
        if previous_decision is not None and previous_decision.generation_index == generation:
            decision = previous_decision
            if decision.input_state_digest != state.state_digest:
                resume = getattr(driver, "resume_incomplete", None)
                if resume is None:
                    state = store.pause_campaign(
                        campaign_id,
                        reason="incomplete-generation-recovery-required",
                    )
                    break
                advance = resume(
                    campaign_id=campaign_id,
                    decision=decision,
                    state=state,
                    previous_feedback=previous_feedback,
                )
                if advance is None:
                    state = store.load_state(campaign_id)
                    break
                state = _accept_advance(
                    store=store,
                    campaign_id=campaign_id,
                    advance=advance,
                    decision=decision,
                )
                if state.lifecycle.completion_status is not None:
                    break
                continue
        else:
            decision = decide_next_generation(
                campaign_id=campaign_id,
                state=state,
                latest_feedback=previous_feedback,
                previous_decision=previous_decision,
                previous_closure=previous_closure,
            )
            store.put_generation_decision(decision)

        advance = driver.advance(
            campaign_id=campaign_id,
            decision=decision,
            state=state,
            previous_feedback=previous_feedback,
        )
        state = _accept_advance(
            store=store,
            campaign_id=campaign_id,
            advance=advance,
            decision=decision,
        )
        if state.lifecycle.completion_status is not None:
            break
        if progress_callback is not None and state.lifecycle.counters.generation_index % 5 == 0:
            progress_callback(_build_result(store, campaign_id, generation_count, resumed))

    return _build_result(store, campaign_id, generation_count, resumed)


def _accept_advance(*, store, campaign_id, advance, decision):
    if advance.persisted:
        if store.load_state(campaign_id) != advance.next_state:
            raise ValueError("generation driver persisted a different state")
        if store.load_latest_generation_closure(campaign_id) != advance.closure:
            raise ValueError("generation driver did not persist its closure")
        if store.load_latest_feedback(campaign_id) != advance.feedback:
            raise ValueError("generation driver did not persist its feedback")
    else:
        store.commit_generation(
            decision=decision,
            next_state=advance.next_state,
            closure=advance.closure,
            feedback=advance.feedback,
        )
    return advance.next_state


def _build_result(
    store: V2CampaignStore,
    campaign_id: str,
    generation_count: int,
    resumed: bool,
) -> V2CampaignRunResult:
    state = store.load_state(campaign_id)
    report = build_v2_campaign_report(store=store, campaign_id=campaign_id)
    payload = {
        "campaign_id": campaign_id,
        "requested_generation_count": generation_count,
        "completed_generation_count": state.lifecycle.counters.generation_index,
        "final_state_digest": state.state_digest,
        "decision_digests": tuple(item["decision_digest"] for item in report["decisions"]),
        "feedback_digests": tuple(item["feedback_digest"] for item in report["feedback"]),
        "resumed": resumed,
    }
    draft = V2CampaignRunResult.model_construct(
        **payload, result_digest="sha256:" + "0" * 64
    )
    return V2CampaignRunResult(
        **payload, result_digest=sha256_digest(draft.digest_payload())
    )


__all__ = [
    "V2_CAMPAIGN_MAX_GENERATIONS",
    "V2_CAMPAIGN_RESUME_MILESTONES",
    "V2CampaignRunResult",
    "V2GenerationAdvance",
    "V2GenerationDriver",
    "run_or_resume_campaign",
]
