"""Office V2 campaign lifecycle and human-readable scheduling decisions."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from sandbox.scenarios.office_v2.models import Identifier, OfficeV2Contract, Sha256Digest

from .v2_scheduler import GenerationAllocation


class CampaignPhase(StrEnum):
    ACTIVE = "active"


class CampaignCompletionStatus(StrEnum):
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
    phase: CampaignPhase = CampaignPhase.ACTIVE
    completion_status: CampaignCompletionStatus | None = None
    counters: CampaignCounters = Field(default_factory=CampaignCounters)
    pause_reason: Identifier | None = None


def evaluate_campaign_lifecycle(
    *,
    current: CampaignLifecycle,
    budget_exhausted: bool = False,
    pause_reason: str | None = None,
    cancelled: bool = False,
    global_no_gain_threshold: int = 5,
    allow_coverage_saturation: bool = True,
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
    if budget_exhausted:
        completion = CampaignCompletionStatus.BUDGET_EXHAUSTED_INCOMPLETE
    elif (
        allow_coverage_saturation
        and current.counters.valid_committed_episodes > 0
        and current.counters.global_consecutive_no_gain >= global_no_gain_threshold
    ):
        completion = CampaignCompletionStatus.SATURATED
    else:
        completion = None
    return current.model_copy(
        update={
            "completion_status": completion,
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
    risk_type: Identifier
    parent_seed_id: Identifier
    executable_seed_id: Identifier
    supporting_execution_record_id: Identifier
    binding_source_digest: Sha256Digest
    reason_codes: tuple[Identifier, ...]
    score_components: tuple[tuple[Identifier, int], ...]
    input_snapshot_digests: tuple[Sha256Digest, ...]
    statement: str


def explain_allocation(allocation: GenerationAllocation) -> SchedulingExplanation:
    statement = (
        f"generation {allocation.generation_index} selected risk pool "
        f"{allocation.risk_type.value}; semantic parent {allocation.parent_seed_id} "
        f"uses executable seed {allocation.executable_seed_id} supported by "
        f"{allocation.supporting_execution_record_id}."
    )
    return SchedulingExplanation(
        generation_index=allocation.generation_index,
        risk_type=allocation.risk_type.value,
        parent_seed_id=allocation.parent_seed_id,
        executable_seed_id=allocation.executable_seed_id,
        supporting_execution_record_id=allocation.supporting_execution_record_id,
        binding_source_digest=allocation.binding_source_digest,
        reason_codes=allocation.reason_codes,
        score_components=allocation.score_components,
        input_snapshot_digests=(
            allocation.coverage_snapshot_digest,
            allocation.corpus_digest,
            allocation.seed_catalog_digest,
            allocation.risk_progress_digest,
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
