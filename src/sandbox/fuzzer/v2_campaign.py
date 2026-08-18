"""Office V2 campaign lifecycle and human-readable scheduling decisions."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from sandbox.scenarios.office_v2.models import Identifier, OfficeV2Contract, Sha256Digest

from .v2_frontier import FrontierSchedulingState
from .v2_scheduler import BaselineExposureLedger, GenerationAllocation


class CampaignPhase(StrEnum):
    BASELINE = "baseline"
    ADAPTIVE = "adaptive"


class CampaignCompletionStatus(StrEnum):
    BASELINE_COMPLETE = "baseline_complete"
    SATURATED = "saturated"
    BUDGET_EXHAUSTED_INCOMPLETE = "budget_exhausted_incomplete"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class CampaignCounters(OfficeV2Contract):
    valid_committed_episodes: int = Field(default=0, ge=0)
    invalid_or_failed_attempts: int = Field(default=0, ge=0)
    global_consecutive_no_gain: int = Field(default=0, ge=0)
    generation_index: int = Field(default=0, ge=0)


class CampaignLifecycle(OfficeV2Contract):
    phase: CampaignPhase = CampaignPhase.BASELINE
    completion_status: CampaignCompletionStatus | None = None
    baseline_complete_event_emitted: bool = False
    counters: CampaignCounters = Field(default_factory=CampaignCounters)
    pause_reason: Identifier | None = None


def evaluate_campaign_lifecycle(
    *,
    current: CampaignLifecycle,
    baseline_ledger: BaselineExposureLedger,
    risk_frontier_states: tuple[FrontierSchedulingState, ...],
    behavior_frontier_states: tuple[FrontierSchedulingState, ...],
    budget_exhausted: bool = False,
    pause_reason: str | None = None,
    cancelled: bool = False,
    global_no_gain_threshold: int = 5,
) -> CampaignLifecycle:
    if cancelled:
        return current.model_copy(
            update={"completion_status": CampaignCompletionStatus.CANCELLED}
        )
    if pause_reason is not None:
        return current.model_copy(
            update={
                "completion_status": CampaignCompletionStatus.PAUSED,
                "pause_reason": pause_reason,
            }
        )
    baseline_complete = baseline_ledger.baseline_complete
    phase = CampaignPhase.ADAPTIVE if baseline_complete else CampaignPhase.BASELINE
    open_states = {
        FrontierSchedulingState.READY,
        FrontierSchedulingState.ACTIVE,
        FrontierSchedulingState.COOLING,
        FrontierSchedulingState.AWAITING_PARENT,
        FrontierSchedulingState.AWAITING_OPERATOR,
        FrontierSchedulingState.LOCAL_BUDGET_EXHAUSTED,
    }
    all_states = (*risk_frontier_states, *behavior_frontier_states)
    still_open = any(item in open_states for item in all_states)
    if budget_exhausted and (not baseline_complete or still_open):
        completion = CampaignCompletionStatus.BUDGET_EXHAUSTED_INCOMPLETE
    elif (
        baseline_complete
        and not still_open
        and current.counters.global_consecutive_no_gain >= global_no_gain_threshold
    ):
        completion = CampaignCompletionStatus.SATURATED
    else:
        completion = None
    return current.model_copy(
        update={
            "phase": phase,
            "completion_status": completion,
            "baseline_complete_event_emitted": (
                current.baseline_complete_event_emitted or baseline_complete
            ),
        }
    )


def record_valid_episode(
    lifecycle: CampaignLifecycle, *, coverage_gain: bool
) -> CampaignLifecycle:
    counters = lifecycle.counters
    return lifecycle.model_copy(
        update={
            "counters": counters.model_copy(
                update={
                    "valid_committed_episodes": counters.valid_committed_episodes + 1,
                    "generation_index": counters.generation_index + 1,
                    "global_consecutive_no_gain": (
                        0
                        if coverage_gain
                        else counters.global_consecutive_no_gain + 1
                    ),
                }
            )
        }
    )


def record_failed_attempt(lifecycle: CampaignLifecycle) -> CampaignLifecycle:
    counters = lifecycle.counters
    return lifecycle.model_copy(
        update={
            "counters": counters.model_copy(
                update={
                    "invalid_or_failed_attempts": counters.invalid_or_failed_attempts + 1
                }
            )
        }
    )


def record_non_episode_generation(lifecycle: CampaignLifecycle) -> CampaignLifecycle:
    """Close one scheduling generation without claiming a valid Episode."""
    counters = lifecycle.counters
    return lifecycle.model_copy(
        update={
            "counters": counters.model_copy(
                update={
                    "invalid_or_failed_attempts": counters.invalid_or_failed_attempts + 1,
                    "generation_index": counters.generation_index + 1,
                }
            )
        }
    )


class SchedulingExplanation(OfficeV2Contract):
    generation_index: int
    frontier_kind: Identifier
    frontier_id: Identifier
    allocation_lane: Identifier
    parent_seed_id: Identifier
    supporting_execution_record_id: Identifier
    binding_source_digest: Sha256Digest
    reason_codes: tuple[Identifier, ...]
    score_components: tuple[tuple[Identifier, int], ...]
    input_snapshot_digests: tuple[Sha256Digest, ...]
    statement: str


def explain_allocation(allocation: GenerationAllocation) -> SchedulingExplanation:
    statement = (
        f"generation {allocation.generation_index} selected {allocation.frontier_kind.value} "
        f"frontier {allocation.frontier_id} through {allocation.allocation_lane.value}; "
        f"parent {allocation.parent_seed_id} is supported by "
        f"{allocation.supporting_execution_record_id}."
    )
    return SchedulingExplanation(
        generation_index=allocation.generation_index,
        frontier_kind=allocation.frontier_kind.value,
        frontier_id=allocation.frontier_id,
        allocation_lane=allocation.allocation_lane.value,
        parent_seed_id=allocation.parent_seed_id,
        supporting_execution_record_id=allocation.supporting_execution_record_id,
        binding_source_digest=allocation.binding_source_digest,
        reason_codes=allocation.reason_codes,
        score_components=allocation.score_components,
        input_snapshot_digests=(
            allocation.coverage_snapshot_digest,
            allocation.corpus_digest,
            allocation.frontier_digest,
            allocation.policy_digest,
        ),
        statement=statement,
    )


__all__ = [
    "CampaignCompletionStatus",
    "CampaignCounters",
    "CampaignLifecycle",
    "CampaignPhase",
    "SchedulingExplanation",
    "evaluate_campaign_lifecycle",
    "explain_allocation",
    "record_failed_attempt",
    "record_non_episode_generation",
    "record_valid_episode",
]
