"""Content-addressed state committed by one Office V2 settlement transaction."""

from __future__ import annotations

from typing import Self

from pydantic import Field, model_validator

from sandbox.coverage.v2_episode_coverage import V2CoverageSnapshot
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import OfficeV2Contract, Sha256Digest

from .v2_campaign import CampaignLifecycle
from .v2_corpus import ExecutionCosts, V2CorpusSnapshot
from .v2_frontier import V2FrontierSnapshot
from .v2_scheduler import BaselineExposureLedger
from .v2_work import BudgetReservation


class CampaignBudgetSnapshot(OfficeV2Contract):
    episode_limit: int = Field(default=100, ge=1)
    mutator_token_limit: int = Field(default=1_000_000, ge=1)
    monetary_microunit_limit: int = Field(default=1_000_000_000, ge=0)
    reserved_episodes: int = Field(default=0, ge=0)
    used_episodes: int = Field(default=0, ge=0)
    reserved: BudgetReservation = Field(default_factory=BudgetReservation)
    consumed: ExecutionCosts = Field(default_factory=ExecutionCosts)
    budget_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"budget_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def limits_and_digest_match(self) -> Self:
        if self.reserved_episodes + self.used_episodes > self.episode_limit:
            raise ValueError("campaign episode budget exceeded")
        if (
            self.reserved.mutator_tokens + self.consumed.mutator_tokens
            > self.mutator_token_limit
        ):
            raise ValueError("campaign Mutator token budget exceeded")
        if (
            self.reserved.monetary_microunits + self.consumed.monetary_microunits
            > self.monetary_microunit_limit
        ):
            raise ValueError("campaign monetary budget exceeded")
        if self.budget_digest != sha256_digest(self.digest_payload()):
            raise ValueError("campaign budget digest does not match")
        return self


def build_campaign_budget(
    *,
    episode_limit: int = 100,
    mutator_token_limit: int = 1_000_000,
    monetary_microunit_limit: int = 1_000_000_000,
    reserved_episodes: int = 0,
    used_episodes: int = 0,
    reserved: BudgetReservation | None = None,
    consumed: ExecutionCosts | None = None,
) -> CampaignBudgetSnapshot:
    payload = {
        "episode_limit": episode_limit,
        "mutator_token_limit": mutator_token_limit,
        "monetary_microunit_limit": monetary_microunit_limit,
        "reserved_episodes": reserved_episodes,
        "used_episodes": used_episodes,
        "reserved": reserved or BudgetReservation(),
        "consumed": consumed or ExecutionCosts(),
    }
    draft = CampaignBudgetSnapshot.model_construct(
        **payload, budget_digest="sha256:" + "0" * 64
    )
    return CampaignBudgetSnapshot(
        **payload, budget_digest=sha256_digest(draft.digest_payload())
    )


def reserve_campaign_budget(
    current: CampaignBudgetSnapshot, reservation: BudgetReservation
) -> CampaignBudgetSnapshot:
    return build_campaign_budget(
        episode_limit=current.episode_limit,
        mutator_token_limit=current.mutator_token_limit,
        monetary_microunit_limit=current.monetary_microunit_limit,
        reserved_episodes=current.reserved_episodes + 1,
        used_episodes=current.used_episodes,
        reserved=BudgetReservation(
            mutator_tokens=current.reserved.mutator_tokens + reservation.mutator_tokens,
            agent_tokens=current.reserved.agent_tokens + reservation.agent_tokens,
            elapsed_ms=current.reserved.elapsed_ms + reservation.elapsed_ms,
            monetary_microunits=(
                current.reserved.monetary_microunits
                + reservation.monetary_microunits
            ),
        ),
        consumed=current.consumed,
    )


def settle_campaign_budget(
    current: CampaignBudgetSnapshot,
    *,
    reservation: BudgetReservation,
    actual: ExecutionCosts,
) -> CampaignBudgetSnapshot:
    if current.reserved_episodes < 1:
        raise ValueError("campaign has no reserved Episode to settle")
    remaining = {
        "mutator_tokens": current.reserved.mutator_tokens - reservation.mutator_tokens,
        "agent_tokens": current.reserved.agent_tokens - reservation.agent_tokens,
        "elapsed_ms": current.reserved.elapsed_ms - reservation.elapsed_ms,
        "monetary_microunits": (
            current.reserved.monetary_microunits
            - reservation.monetary_microunits
        ),
    }
    if any(value < 0 for value in remaining.values()):
        raise ValueError("settled reservation exceeds persisted campaign reservation")
    return build_campaign_budget(
        episode_limit=current.episode_limit,
        mutator_token_limit=current.mutator_token_limit,
        monetary_microunit_limit=current.monetary_microunit_limit,
        reserved_episodes=current.reserved_episodes - 1,
        used_episodes=current.used_episodes + 1,
        reserved=BudgetReservation(**remaining),
        consumed=ExecutionCosts(
            mutator_tokens=current.consumed.mutator_tokens + actual.mutator_tokens,
            agent_tokens=current.consumed.agent_tokens + actual.agent_tokens,
            elapsed_ms=current.consumed.elapsed_ms + actual.elapsed_ms,
            monetary_microunits=(
                current.consumed.monetary_microunits
                + actual.monetary_microunits
            ),
        ),
    )


def reserve_mutation_budget(
    current: CampaignBudgetSnapshot,
    *,
    tokens: int,
    cost_microunits: int,
) -> CampaignBudgetSnapshot:
    """Reserve a MutationPlan before any Provider request is sent."""
    return build_campaign_budget(
        episode_limit=current.episode_limit,
        mutator_token_limit=current.mutator_token_limit,
        monetary_microunit_limit=current.monetary_microunit_limit,
        reserved_episodes=current.reserved_episodes,
        used_episodes=current.used_episodes,
        reserved=BudgetReservation(
            mutator_tokens=current.reserved.mutator_tokens + tokens,
            agent_tokens=current.reserved.agent_tokens,
            elapsed_ms=current.reserved.elapsed_ms,
            monetary_microunits=(
                current.reserved.monetary_microunits + cost_microunits
            ),
        ),
        consumed=current.consumed,
    )


def settle_mutation_budget(
    current: CampaignBudgetSnapshot,
    *,
    reserved_tokens: int,
    reserved_cost_microunits: int,
    actual: ExecutionCosts,
) -> CampaignBudgetSnapshot:
    if actual.agent_tokens or actual.elapsed_ms:
        raise ValueError("preparation settlement can only contain Mutator costs")
    if actual.mutator_tokens > reserved_tokens:
        raise ValueError("actual Mutator tokens exceed reservation")
    if actual.monetary_microunits > reserved_cost_microunits:
        raise ValueError("actual Mutator cost exceeds reservation")
    remaining_tokens = current.reserved.mutator_tokens - reserved_tokens
    remaining_cost = (
        current.reserved.monetary_microunits - reserved_cost_microunits
    )
    if remaining_tokens < 0 or remaining_cost < 0:
        raise ValueError("mutation settlement exceeds persisted reservation")
    return build_campaign_budget(
        episode_limit=current.episode_limit,
        mutator_token_limit=current.mutator_token_limit,
        monetary_microunit_limit=current.monetary_microunit_limit,
        reserved_episodes=current.reserved_episodes,
        used_episodes=current.used_episodes,
        reserved=BudgetReservation(
            mutator_tokens=remaining_tokens,
            agent_tokens=current.reserved.agent_tokens,
            elapsed_ms=current.reserved.elapsed_ms,
            monetary_microunits=remaining_cost,
        ),
        consumed=ExecutionCosts(
            mutator_tokens=current.consumed.mutator_tokens + actual.mutator_tokens,
            agent_tokens=current.consumed.agent_tokens,
            elapsed_ms=current.consumed.elapsed_ms,
            monetary_microunits=(
                current.consumed.monetary_microunits
                + actual.monetary_microunits
            ),
        ),
    )


class V2CampaignStateSnapshot(OfficeV2Contract):
    coverage: V2CoverageSnapshot
    corpus: V2CorpusSnapshot
    frontiers: V2FrontierSnapshot
    exposure_ledger: BaselineExposureLedger
    budget: CampaignBudgetSnapshot
    lifecycle: CampaignLifecycle
    state_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"state_digest"}, exclude_none=False)

    @property
    def lifecycle_digest(self) -> str:
        return sha256_digest(self.lifecycle.model_dump(mode="json", exclude_none=False))

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        if self.state_digest != sha256_digest(self.digest_payload()):
            raise ValueError("campaign state digest does not match")
        return self


def build_campaign_state(
    *,
    coverage: V2CoverageSnapshot,
    corpus: V2CorpusSnapshot,
    frontiers: V2FrontierSnapshot,
    exposure_ledger: BaselineExposureLedger,
    budget: CampaignBudgetSnapshot,
    lifecycle: CampaignLifecycle,
) -> V2CampaignStateSnapshot:
    payload = {
        "coverage": coverage,
        "corpus": corpus,
        "frontiers": frontiers,
        "exposure_ledger": exposure_ledger,
        "budget": budget,
        "lifecycle": lifecycle,
    }
    draft = V2CampaignStateSnapshot.model_construct(
        **payload, state_digest="sha256:" + "0" * 64
    )
    return V2CampaignStateSnapshot(
        **payload, state_digest=sha256_digest(draft.digest_payload())
    )


__all__ = [
    "CampaignBudgetSnapshot",
    "V2CampaignStateSnapshot",
    "build_campaign_budget",
    "build_campaign_state",
    "reserve_campaign_budget",
    "reserve_mutation_budget",
    "settle_campaign_budget",
    "settle_mutation_budget",
]
