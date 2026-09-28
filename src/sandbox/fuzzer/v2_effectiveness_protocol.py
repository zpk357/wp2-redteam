"""Persisted attack-effectiveness protocol and its derived budget ledger (AE-04 - AE-06).

A Campaign that enables this protocol stops on the number of *decisive* results
(SUCCESS + FAILURE) instead of the number of committed Episodes, and supplements
UNDETERMINED / INFRA_ERROR outcomes with new candidates inside a frozen cumulative
budget.  Every counter is derived from persisted facts, so a Controller restart
cannot reset it or hand out fresh quota:

* the scheduling limit counts persisted generation decisions;
* the execution limit counts sealed attempt receipts, retries included;
* the consecutive infrastructure-error streak is read from the generation
  closures in generation order, with the error class normalized from the sealed
  receipt error code rather than from a stack trace.

The old Campaign semantics stay untouched: a Campaign without a stored protocol
keeps ``valid_committed_episodes`` as its stopping target and never extends any
limit on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

from pydantic import Field, model_validator

from sandbox.scenarios.office_v2.models import OfficeV2Contract

from .v2_effectiveness import (
    ATTACK_EFFECTIVENESS_VERSION,
    EffectivenessCounts,
    score_effectiveness,
)
from .v2_loop_contracts import NonEpisodeDisposition
from .v2_orchestrator import GenerationClosureKind

if TYPE_CHECKING:
    from .v2_campaign_store import V2CampaignStore

ATTACK_EFFECTIVENESS_PROTOCOL_VERSION = "attack-effectiveness-protocol-v1"

#: Draft caps from the SPEC, fixed together with K before any real run starts.
DEFAULT_CONSECUTIVE_INFRA_PAUSE_THRESHOLD = 3
DEFAULT_LIMIT_MULTIPLIER = 3

#: Stable infrastructure error classes. Anything else collapses into the
#: unknown-failure class so different stack texts cannot masquerade as
#: different classes and dodge the consecutive-error pause.
INFRA_ERROR_CLASSES = frozenset(
    {
        "episode-timeout",
        "episode-runtime-transport",
        "episode-cleanup",
        "episode-runtime-infrastructure",
        "episode-execution-failed",
        "episode-unknown-failure",
    }
)
UNKNOWN_INFRA_ERROR_CLASS = "episode-unknown-failure"

NON_EPISODE_CATEGORY_INFRA_ERROR = "infra-error"
NON_EPISODE_CATEGORY_BEHAVIOR_LIMIT = "behavior-limit"
NON_EPISODE_CATEGORY_PREPARATION_REJECTED = "preparation-rejected"
NON_EPISODE_CATEGORY_PREPARATION_PAUSED = "preparation-paused"
NON_EPISODE_CATEGORY_OTHER = "other"


class EffectivenessProtocol(OfficeV2Contract):
    """The frozen run contract of one attack-effectiveness Campaign.

    ``target_decisive_k`` is K: the number of SUCCESS + FAILURE Episodes the run
    must reach.  The two cumulative limits bound scheduling decisions and
    Episode execution attempts (retries included) across every resume; the
    threshold pauses a Campaign whose generation closures keep failing with the
    same infrastructure class.
    """

    protocol_version: str = ATTACK_EFFECTIVENESS_PROTOCOL_VERSION
    classification_version: str = ATTACK_EFFECTIVENESS_VERSION
    target_decisive_k: int = Field(gt=0)
    scheduling_limit: int = Field(gt=0)
    execution_attempt_limit: int = Field(gt=0)
    consecutive_infra_pause_threshold: int = Field(
        default=DEFAULT_CONSECUTIVE_INFRA_PAUSE_THRESHOLD, gt=0
    )

    @model_validator(mode="after")
    def versions_and_budget_match(self) -> Self:
        if self.protocol_version != ATTACK_EFFECTIVENESS_PROTOCOL_VERSION:
            raise ValueError("unsupported effectiveness protocol version")
        if self.classification_version != ATTACK_EFFECTIVENESS_VERSION:
            raise ValueError("unsupported attack-effectiveness classification version")
        if self.scheduling_limit < self.target_decisive_k:
            raise ValueError("scheduling limit cannot be below the decisive target K")
        if self.execution_attempt_limit < self.target_decisive_k:
            raise ValueError("execution attempt limit cannot be below the decisive target K")
        return self


def freeze_effectiveness_protocol(
    *,
    target_decisive_k: int,
    scheduling_limit: int | None = None,
    execution_attempt_limit: int | None = None,
    consecutive_infra_pause_threshold: int = DEFAULT_CONSECUTIVE_INFRA_PAUSE_THRESHOLD,
) -> EffectivenessProtocol:
    """Build one protocol; omitted limits default to ``K * DEFAULT_LIMIT_MULTIPLIER``."""

    if target_decisive_k < 1:
        raise ValueError("target_decisive_k must be positive")
    return EffectivenessProtocol(
        target_decisive_k=target_decisive_k,
        scheduling_limit=(
            scheduling_limit
            if scheduling_limit is not None
            else target_decisive_k * DEFAULT_LIMIT_MULTIPLIER
        ),
        execution_attempt_limit=(
            execution_attempt_limit
            if execution_attempt_limit is not None
            else target_decisive_k * DEFAULT_LIMIT_MULTIPLIER
        ),
        consecutive_infra_pause_threshold=consecutive_infra_pause_threshold,
    )


def normalize_infra_error_class(error_code: str | None) -> str:
    """Map a sealed receipt error code onto the stable class set."""

    if error_code in INFRA_ERROR_CLASSES:
        return str(error_code)
    return UNKNOWN_INFRA_ERROR_CLASS


def describe_protocol_difference(
    stored: EffectivenessProtocol | None, requested: EffectivenessProtocol | None
) -> str:
    """Explain a resume whose frozen protocol does not match; empty when equal."""

    if stored == requested:
        return ""
    if stored is None:
        return "the stored Campaign predates the effectiveness protocol; start a new batch"
    if requested is None:
        return "the stored Campaign uses the effectiveness protocol; pass the same protocol"
    fields = (
        "protocol_version",
        "classification_version",
        "target_decisive_k",
        "scheduling_limit",
        "execution_attempt_limit",
        "consecutive_infra_pause_threshold",
    )
    changed = [
        f"{name}: stored={getattr(stored, name)!r} requested={getattr(requested, name)!r}"
        for name in fields
        if getattr(stored, name) != getattr(requested, name)
    ]
    return "; ".join(changed) or "unknown protocol difference"


@dataclass(frozen=True, slots=True)
class NonEpisodeRecord:
    """One generation that closed without a decidable Episode result."""

    settlement_id: str
    generation_index: int
    disposition: NonEpisodeDisposition
    category: str
    infra_error_class: str | None
    attempt_receipt_count: int

    def as_payload(self) -> dict[str, object]:
        return {
            "settlement_id": self.settlement_id,
            "generation_index": self.generation_index,
            "disposition": self.disposition.value,
            "category": self.category,
            "infra_error_class": self.infra_error_class,
            "attempt_receipt_count": self.attempt_receipt_count,
        }


def _non_episode_category(disposition: NonEpisodeDisposition) -> str:
    if disposition is NonEpisodeDisposition.WORK_INFRA_ERROR:
        return NON_EPISODE_CATEGORY_INFRA_ERROR
    if disposition is NonEpisodeDisposition.WORK_PERMANENT_FAILURE:
        # A normal time / step / tool budget termination; it stays out of N but
        # is a judged behavior limit rather than an infrastructure fault.
        return NON_EPISODE_CATEGORY_BEHAVIOR_LIMIT
    if disposition is NonEpisodeDisposition.PREPARATION_REJECTED:
        return NON_EPISODE_CATEGORY_PREPARATION_REJECTED
    if disposition is NonEpisodeDisposition.PREPARATION_PAUSED:
        return NON_EPISODE_CATEGORY_PREPARATION_PAUSED
    return NON_EPISODE_CATEGORY_OTHER


@dataclass(frozen=True, slots=True)
class EffectivenessLedger:
    """Derived S / N / D, cumulative budget use and the infra-error streak.

    The ledger is a pure function of persisted facts plus the frozen protocol,
    so online stopping, offline scoring and a later resume all read the same
    numbers and a restart cannot reset them.
    """

    protocol: EffectivenessProtocol
    counts: EffectivenessCounts
    non_episode_records: tuple[NonEpisodeRecord, ...]
    scheduling_attempts: int
    execution_attempts: int
    valid_committed_episodes: int
    preparation_rejections: int
    infra_error_streak: int
    infra_error_class: str | None

    @property
    def reached(self) -> bool:
        return self.counts.decisive >= self.protocol.target_decisive_k

    @property
    def budget_stop_reason(self) -> str | None:
        if self.scheduling_attempts >= self.protocol.scheduling_limit:
            return "scheduling-attempt-budget-exhausted"
        if self.execution_attempts >= self.protocol.execution_attempt_limit:
            return "execution-attempt-budget-exhausted"
        return None

    @property
    def infra_pause_reason(self) -> str | None:
        if self.infra_error_streak < self.protocol.consecutive_infra_pause_threshold:
            return None
        return f"consecutive-{normalize_infra_error_class(self.infra_error_class)}-infra-errors"

    def as_payload(self) -> dict[str, object]:
        return {
            "protocol_version": self.protocol.protocol_version,
            "classification_version": self.protocol.classification_version,
            "target_decisive_k": self.protocol.target_decisive_k,
            "scheduling_limit": self.protocol.scheduling_limit,
            "execution_attempt_limit": self.protocol.execution_attempt_limit,
            "consecutive_infra_pause_threshold": (
                self.protocol.consecutive_infra_pause_threshold
            ),
            "successes": self.counts.successes,
            "decisive": self.counts.decisive,
            "success_rate_percent": self.counts.success_rate,
            "distinct_success_targets": list(self.counts.distinct_success_targets),
            "by_classification": self.counts.by_classification,
            "valid_committed_episodes": self.valid_committed_episodes,
            "preparation_rejections": self.preparation_rejections,
            "scheduling_attempts": self.scheduling_attempts,
            "execution_attempts": self.execution_attempts,
            "consecutive_infra_error_class": self.infra_error_class,
            "consecutive_infra_errors": self.infra_error_streak,
            "non_episode_generations": [
                record.as_payload() for record in self.non_episode_records
            ],
            "target_reached": self.reached,
            "budget_stop_reason": self.budget_stop_reason,
            "sample_scope": (
                "success rate is conditional on decidable results; excluded results "
                "keep their recordings, receipts and costs and are reported separately"
            ),
        }


def build_effectiveness_ledger(
    *,
    store: V2CampaignStore,
    campaign_id: str,
    protocol: EffectivenessProtocol,
) -> EffectivenessLedger:
    """Derive the ledger from the Campaign's own persisted facts."""

    settlements = store.list_settlements(campaign_id)
    counts = score_effectiveness(settlements)
    receipts = store.list_attempt_receipts(campaign_id)
    receipts_by_id = {item.attempt_id: item for item in receipts}
    non_episode_by_id = {
        item.settlement_id: item
        for item in store.list_non_episode_settlements(campaign_id)
    }
    records: list[NonEpisodeRecord] = []
    streak = 0
    streak_class: str | None = None
    for closure in store.list_generation_closures(campaign_id):
        if closure.closure_kind is GenerationClosureKind.CANDIDATE_SETTLEMENT:
            # A committed Episode was judged: decisive or undetermined, either
            # way the infrastructure streak is broken.
            streak = 0
            streak_class = None
            continue
        settlement = non_episode_by_id.get(closure.settlement_id)
        if settlement is None:
            continue
        category = _non_episode_category(settlement.disposition)
        infra_class = None
        if category == NON_EPISODE_CATEGORY_INFRA_ERROR:
            terminal = next(
                (
                    receipts_by_id[attempt_id]
                    for attempt_id in reversed(settlement.attempt_receipt_ids)
                    if attempt_id in receipts_by_id
                ),
                None,
            )
            infra_class = normalize_infra_error_class(
                None if terminal is None else terminal.error_code
            )
        records.append(
            NonEpisodeRecord(
                settlement_id=settlement.settlement_id,
                generation_index=closure.generation_index,
                disposition=settlement.disposition,
                category=category,
                infra_error_class=infra_class,
                attempt_receipt_count=len(settlement.attempt_receipt_ids),
            )
        )
        if infra_class is None:
            streak = 0
            streak_class = None
        elif infra_class == streak_class:
            streak += 1
        else:
            streak = 1
            streak_class = infra_class
    state = store.load_state(campaign_id)
    return EffectivenessLedger(
        protocol=protocol,
        counts=counts,
        non_episode_records=tuple(records),
        scheduling_attempts=len(store.list_generation_decisions(campaign_id)),
        execution_attempts=len(receipts),
        valid_committed_episodes=state.lifecycle.counters.valid_committed_episodes,
        preparation_rejections=sum(
            record.category == NON_EPISODE_CATEGORY_PREPARATION_REJECTED
            for record in records
        ),
        infra_error_streak=streak,
        infra_error_class=streak_class,
    )


__all__ = [
    "ATTACK_EFFECTIVENESS_PROTOCOL_VERSION",
    "DEFAULT_CONSECUTIVE_INFRA_PAUSE_THRESHOLD",
    "DEFAULT_LIMIT_MULTIPLIER",
    "EffectivenessLedger",
    "EffectivenessProtocol",
    "INFRA_ERROR_CLASSES",
    "NON_EPISODE_CATEGORY_BEHAVIOR_LIMIT",
    "NON_EPISODE_CATEGORY_INFRA_ERROR",
    "NON_EPISODE_CATEGORY_OTHER",
    "NON_EPISODE_CATEGORY_PREPARATION_PAUSED",
    "NON_EPISODE_CATEGORY_PREPARATION_REJECTED",
    "NonEpisodeRecord",
    "UNKNOWN_INFRA_ERROR_CLASS",
    "build_effectiveness_ledger",
    "describe_protocol_difference",
    "freeze_effectiveness_protocol",
    "normalize_infra_error_class",
]
