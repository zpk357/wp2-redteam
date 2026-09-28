"""Shared run/resume loop for deterministic and real-model Office V2 Campaigns."""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable
from typing import Protocol

from pydantic import Field

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import OfficeV2Contract

from .v2_campaign import CampaignCompletionStatus
from .v2_campaign_state import V2CampaignStateSnapshot
from .v2_campaign_store import V2CampaignStore
from .v2_effectiveness_protocol import (
    EffectivenessLedger,
    EffectivenessProtocol,
    build_effectiveness_ledger,
    describe_protocol_difference,
)
from .v2_feedback import NextGenerationFeedback
from .v2_orchestrator import (
    GenerationClosureReceipt,
    GenerationDecision,
    decide_next_generation,
)
from .v2_report import build_v2_campaign_report
from .v2_strategy import CampaignStrategy


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
    requested_generation_count: int = Field(ge=1)
    completed_generation_count: int = Field(ge=0)
    requested_episode_count: int = Field(ge=1)
    completed_episode_count: int = Field(ge=0)
    attempted_generation_count: int = Field(ge=0)
    final_state_digest: str
    decision_digests: tuple[str, ...]
    feedback_digests: tuple[str, ...]
    resumed: bool
    target_reached: bool
    completion_status: str | None = None
    error_traceback: str | None = None
    #: Attack-effectiveness S / N / D, cumulative budget use and exclusions.
    #: ``None`` for a Campaign that runs under the legacy stop rule.
    effectiveness: dict[str, object] | None = None
    #: Why K was not reached when ``target_reached`` is false.
    not_reached_reason: str | None = None
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
    strategy: CampaignStrategy | str = CampaignStrategy.COVERAGE_GUIDED,
    campaign_seed_value: int = 0,
    exploratory: bool = True,
    max_generation_attempts: int | None = None,
    effectiveness_protocol: EffectivenessProtocol | None = None,
) -> V2CampaignRunResult:
    if generation_count < 1:
        raise ValueError("generation_count must be positive")
    if effectiveness_protocol is not None and (
        effectiveness_protocol.target_decisive_k != generation_count
    ):
        raise ValueError("the effectiveness protocol K must equal the requested Episode count")
    if effectiveness_protocol is not None and max_generation_attempts is not None:
        raise ValueError(
            "max_generation_attempts belongs to the legacy stop rule; "
            "an effectiveness Campaign is bounded by its frozen protocol limits"
        )
    resumed = store.campaign_exists(campaign_id)
    if resumed:
        stored_protocol = store.campaign_effectiveness_protocol(campaign_id)
        if stored_protocol != effectiveness_protocol:
            raise ValueError(
                "resume parameters differ from the frozen Campaign protocol: "
                + describe_protocol_difference(stored_protocol, effectiveness_protocol)
            )
    store.create_campaign(
        campaign_id=campaign_id,
        initial_state=initial_state,
        strategy=strategy,
        campaign_seed_value=campaign_seed_value,
        effectiveness_protocol=effectiveness_protocol,
    )
    # A resumed Campaign keeps the strategy it was created with.  The CLI
    # remains flexible without mixing strategies inside one persisted run.
    strategy = store.campaign_strategy(campaign_id)
    state = store.load_state(campaign_id)
    if (
        exploratory
        and effectiveness_protocol is None
        and generation_count > state.budget.episode_limit
    ):
        # Legacy target extension only. An effectiveness Campaign never widens
        # its physical Episode budget to keep supplementing: the storage budget
        # stays frozen and a shortfall is reported instead of extended away.
        state = store.extend_episode_limit(
            campaign_id,
            episode_limit=generation_count,
        )
    if exploratory and resumed and state.lifecycle.completion_status == "paused":
        state = store.resume_paused_campaign(
            campaign_id,
            reason="exploratory-resume",
        )
    if not exploratory and state.lifecycle.counters.generation_index > generation_count:
        raise ValueError("stored Campaign is beyond requested generation count")
    if max_generation_attempts is not None and max_generation_attempts < generation_count:
        raise ValueError("max_generation_attempts must be at least the requested count")

    error_traceback = None
    not_reached_reason = None
    starting_generation = state.lifecycle.counters.generation_index
    while True:
        ledger = _effectiveness_ledger(store, campaign_id, effectiveness_protocol)
        if _campaign_target_reached(
            state,
            generation_count=generation_count,
            exploratory=exploratory,
            decisive_count=None if ledger is None else ledger.counts.decisive,
        ):
            break
        if state.lifecycle.completion_status is not None:
            break
        if ledger is not None:
            stop_reason = ledger.budget_stop_reason
            if stop_reason is not None:
                state = store.close_campaign(
                    campaign_id,
                    status=CampaignCompletionStatus.BUDGET_EXHAUSTED_INCOMPLETE,
                    reason=stop_reason,
                )
                not_reached_reason = stop_reason
                break
        elif (
            max_generation_attempts is not None
            and state.lifecycle.counters.generation_index - starting_generation
            >= max_generation_attempts
        ):
            not_reached_reason = "generation-attempt-budget-exhausted"
            state = store.pause_campaign(
                campaign_id,
                reason=not_reached_reason,
            )
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
                try:
                    advance = resume(
                        campaign_id=campaign_id,
                        decision=decision,
                        state=state,
                        previous_feedback=previous_feedback,
                    )
                except Exception:
                    error_traceback = traceback.format_exc()
                    logging.getLogger(__name__).exception("Campaign recovery failed")
                    if not exploratory:
                        raise
                    state = store.load_state(campaign_id)
                    if state.lifecycle.completion_status is None:
                        state = store.pause_campaign(
                            campaign_id,
                            reason="campaign-recovery-failure",
                        )
                    break
                if advance is None:
                    state = store.load_state(campaign_id)
                    break
                state = _accept_advance(
                    store=store,
                    campaign_id=campaign_id,
                    advance=advance,
                    decision=decision,
                )
                reason = _effectiveness_infra_pause_reason(
                    store, campaign_id, effectiveness_protocol
                )
                if reason is not None:
                    state = store.pause_campaign(campaign_id, reason=reason)
                    not_reached_reason = reason
                    break
                if state.lifecycle.completion_status is not None or _campaign_target_reached(
                    state,
                    generation_count=generation_count,
                    exploratory=exploratory,
                    decisive_count=_effectiveness_decisive_count(
                        store, campaign_id, effectiveness_protocol
                    ),
                ):
                    break
                continue
        else:
            decision = decide_next_generation(
                campaign_id=campaign_id,
                state=state,
                latest_feedback=previous_feedback,
                previous_decision=previous_decision,
                previous_closure=previous_closure,
                strategy=strategy,
                campaign_seed_value=campaign_seed_value,
            )
            store.put_generation_decision(decision)

        try:
            advance = driver.advance(
                campaign_id=campaign_id,
                decision=decision,
                state=state,
                previous_feedback=previous_feedback,
            )
        except Exception:
            error_traceback = traceback.format_exc()
            logging.getLogger(__name__).exception("Campaign execution failed")
            if not exploratory:
                raise
            state = store.load_state(campaign_id)
            if state.lifecycle.completion_status is None:
                state = store.pause_campaign(
                    campaign_id,
                    reason="campaign-driver-failure",
                )
            break
        if advance is None:
            state = store.load_state(campaign_id)
            break
        state = _accept_advance(
            store=store,
            campaign_id=campaign_id,
            advance=advance,
            decision=decision,
        )
        reason = _effectiveness_infra_pause_reason(
            store, campaign_id, effectiveness_protocol
        )
        if reason is not None:
            state = store.pause_campaign(campaign_id, reason=reason)
            not_reached_reason = reason
            break
        if state.lifecycle.completion_status is not None or _campaign_target_reached(
            state,
            generation_count=generation_count,
            exploratory=exploratory,
            decisive_count=_effectiveness_decisive_count(
                store, campaign_id, effectiveness_protocol
            ),
        ):
            break
        if progress_callback is not None and state.lifecycle.counters.generation_index % 5 == 0:
            progress_callback(
                _build_result(
                    store,
                    campaign_id,
                    generation_count,
                    resumed,
                    exploratory=exploratory,
                    effectiveness_protocol=effectiveness_protocol,
                )
            )

    return _build_result(
        store,
        campaign_id,
        generation_count,
        resumed,
        exploratory=exploratory,
        error_traceback=error_traceback,
        effectiveness_protocol=effectiveness_protocol,
        not_reached_reason=not_reached_reason,
    )


def _campaign_target_reached(
    state: V2CampaignStateSnapshot,
    *,
    generation_count: int,
    exploratory: bool,
    decisive_count: int | None = None,
) -> bool:
    """Whether the target is reached.

    ``decisive_count`` is the attack-effectiveness N (SUCCESS + FAILURE).  When
    it is given the target is the decidable-sample count K; otherwise the legacy
    stopping rule counts committed Episodes.
    """

    if exploratory:
        if decisive_count is not None:
            return decisive_count >= generation_count
        return state.lifecycle.counters.valid_committed_episodes >= generation_count
    return state.lifecycle.counters.generation_index >= generation_count


def _effectiveness_ledger(
    store: V2CampaignStore,
    campaign_id: str,
    protocol: EffectivenessProtocol | None,
) -> EffectivenessLedger | None:
    if protocol is None:
        return None
    return build_effectiveness_ledger(
        store=store, campaign_id=campaign_id, protocol=protocol
    )


def _effectiveness_decisive_count(
    store: V2CampaignStore,
    campaign_id: str,
    protocol: EffectivenessProtocol | None,
) -> int | None:
    ledger = _effectiveness_ledger(store, campaign_id, protocol)
    return None if ledger is None else ledger.counts.decisive


def _effectiveness_infra_pause_reason(
    store: V2CampaignStore,
    campaign_id: str,
    protocol: EffectivenessProtocol | None,
) -> str | None:
    ledger = _effectiveness_ledger(store, campaign_id, protocol)
    return None if ledger is None else ledger.infra_pause_reason


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
    *,
    exploratory: bool,
    error_traceback: str | None = None,
    effectiveness_protocol: EffectivenessProtocol | None = None,
    not_reached_reason: str | None = None,
) -> V2CampaignRunResult:
    state = store.load_state(campaign_id)
    report = build_v2_campaign_report(store=store, campaign_id=campaign_id)
    ledger = _effectiveness_ledger(store, campaign_id, effectiveness_protocol)
    target_reached = _campaign_target_reached(
        state,
        generation_count=generation_count,
        exploratory=exploratory,
        decisive_count=None if ledger is None else ledger.counts.decisive,
    )
    if not target_reached and not_reached_reason is None:
        not_reached_reason = state.lifecycle.pause_reason or (
            state.lifecycle.completion_status.value
            if state.lifecycle.completion_status is not None
            else None
        )
    payload = {
        "campaign_id": campaign_id,
        "error_traceback": error_traceback,
        "requested_generation_count": generation_count,
        "completed_generation_count": state.lifecycle.counters.generation_index,
        "requested_episode_count": generation_count,
        "completed_episode_count": (
            state.lifecycle.counters.valid_committed_episodes
            if ledger is None
            else ledger.counts.decisive
        ),
        "attempted_generation_count": state.lifecycle.counters.generation_index,
        "final_state_digest": state.state_digest,
        "decision_digests": tuple(item["decision_digest"] for item in report["decisions"]),
        "feedback_digests": tuple(item["feedback_digest"] for item in report["feedback"]),
        "resumed": resumed,
        "target_reached": target_reached,
        "completion_status": (
            state.lifecycle.completion_status.value
            if state.lifecycle.completion_status is not None
            else None
        ),
        "effectiveness": None if ledger is None else ledger.as_payload(),
        "not_reached_reason": not_reached_reason,
    }
    draft = V2CampaignRunResult.model_construct(**payload, result_digest="sha256:" + "0" * 64)
    return V2CampaignRunResult(**payload, result_digest=sha256_digest(draft.digest_payload()))


__all__ = [
    "V2CampaignRunResult",
    "V2GenerationAdvance",
    "V2GenerationDriver",
    "run_or_resume_campaign",
]
