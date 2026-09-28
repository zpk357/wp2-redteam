"""Formal four-pool risk direction and seed selection.

This module is deliberately independent from the historical milestone frontier
snapshot. It chooses a risk type first, then a uniformly random eligible parent
from that risk type's pool. Lower risk-type progress is preferred; ties are
resolved by a replayable receipt.
"""

from __future__ import annotations

from typing import Self

from pydantic import Field, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import Identifier, OfficeV2Contract, Sha256Digest

from .v2_seed_pools import (
    FrozenSeed,
    FrozenSeedCatalog,
    RiskProgressLevel,
    RiskType,
    RiskTypeProgressState,
)
from .v2_selection import (
    SelectionKind,
    SelectionReceipt,
    independent_uniform_selection,
    priority_selection,
)


class RiskPoolSelection(OfficeV2Contract):
    risk_type: RiskType
    parent_seed_id: Identifier
    risk_type_progress_level: RiskProgressLevel
    direction_selection_receipt: SelectionReceipt
    selection_receipt: SelectionReceipt
    reasons: tuple[Identifier, ...] = Field(min_length=1)
    selection_digest: Sha256Digest

    @model_validator(mode="after")
    def receipt_and_digest_match(self) -> Self:
        if self.direction_selection_receipt.selection_kind is not SelectionKind.RISK_DIRECTION:
            raise ValueError("risk direction selection requires a risk-direction receipt")
        if self.direction_selection_receipt.selected_option_id != self.risk_type.value:
            raise ValueError("risk direction receipt does not close over risk type")
        if self.selection_receipt.selection_kind is not SelectionKind.PARENT:
            raise ValueError("risk pool selection requires a parent receipt")
        if self.selection_receipt.selected_option_id != self.parent_seed_id:
            raise ValueError("risk pool receipt does not identify parent seed")
        if self.selection_digest != sha256_digest(self.digest_payload()):
            raise ValueError("risk pool selection digest does not match")
        return self

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"selection_digest"}, exclude_none=False)


class FormalRiskAllocation(OfficeV2Contract):
    """Formal allocation result independent of historical frontier objects."""

    risk_type: RiskType
    parent_seed_id: Identifier
    risk_type_progress_level: RiskProgressLevel
    direction_selection_receipt: SelectionReceipt
    parent_selection_receipt: SelectionReceipt
    catalog_digest: Sha256Digest
    risk_progress_digest: Sha256Digest
    allocation_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"allocation_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def receipts_and_digest_match(self) -> Self:
        if self.direction_selection_receipt.selection_kind is not SelectionKind.RISK_DIRECTION:
            raise ValueError("formal allocation requires a risk-direction receipt")
        if self.direction_selection_receipt.selected_option_id != self.risk_type.value:
            raise ValueError("formal allocation risk direction does not match receipt")
        if self.parent_selection_receipt.selection_kind is not SelectionKind.PARENT:
            raise ValueError("formal allocation requires a parent receipt")
        if self.parent_selection_receipt.selected_option_id != self.parent_seed_id:
            raise ValueError("formal allocation parent does not match receipt")
        if self.allocation_digest != sha256_digest(self.digest_payload()):
            raise ValueError("formal allocation digest does not match")
        return self


def _progress_index(
    states: tuple[RiskTypeProgressState, ...],
) -> dict[RiskType, RiskProgressLevel]:
    result: dict[RiskType, RiskProgressLevel] = {}
    for state in states:
        if state.risk_type in result:
            raise ValueError("risk-type progress state ids must be unique")
        result[state.risk_type] = state.level
    return result


def select_risk_pool_seed(
    *,
    catalog: FrozenSeedCatalog,
    progress_states: tuple[RiskTypeProgressState, ...] = (),
    generation_index: int = 0,
    campaign_id: str = "campaign.risk-pool",
    campaign_seed_value: int = 0,
    include_derived: bool = True,
    selectable_seed_ids: frozenset[str] | None = None,
    prefer_lowest_progress: bool = True,
) -> RiskPoolSelection:
    """Choose a risk type, then a uniform parent in its pool.

    ``prefer_lowest_progress`` prefers the lowest-progress risk types and breaks
    ties by a replayable receipt.  ``False`` treats every non-empty pool as
    equally eligible, which is the independent random baseline's direction rule.
    """

    progress = _progress_index(progress_states)
    pool_candidates: list[tuple[RiskProgressLevel, RiskType, tuple[FrozenSeed, ...]]] = []
    for pool in catalog.pools:
        seeds = tuple(
            seed
            for seed in pool.selectable_seeds(include_derived=include_derived)
            if selectable_seed_ids is None or seed.seed_id in selectable_seed_ids
        )
        if not seeds:
            continue
        risk_level = progress.get(pool.risk_type, RiskProgressLevel.NOT_SELECTED)
        pool_candidates.append((risk_level, pool.risk_type, seeds))
    if not pool_candidates:
        raise ValueError("no selectable seed in any risk pool")
    if prefer_lowest_progress:
        lowest = min(item[0] for item in pool_candidates)
        directions = tuple(item for item in pool_candidates if item[0] is lowest)
        direction_reason = "lowest-risk-type-progress-level"
    else:
        directions = tuple(pool_candidates)
        direction_reason = "uniform-risk-direction"
    direction_receipt = independent_uniform_selection(
        selection_kind=SelectionKind.RISK_DIRECTION,
        campaign_id=campaign_id,
        generation_index=generation_index,
        option_ids=tuple(item[1].value for item in directions),
        campaign_seed_value=campaign_seed_value,
    )
    risk_level, risk_type, seeds = next(
        item for item in directions if item[1].value == direction_receipt.selected_option_id
    )
    parent_receipt = independent_uniform_selection(
        selection_kind=SelectionKind.PARENT,
        campaign_id=campaign_id,
        generation_index=generation_index,
        option_ids=tuple(seed.seed_id for seed in seeds),
        campaign_seed_value=campaign_seed_value,
    )
    parent_seed = next(seed for seed in seeds if seed.seed_id == parent_receipt.selected_option_id)
    payload = {
        "risk_type": risk_type,
        "parent_seed_id": parent_seed.seed_id,
        "risk_type_progress_level": risk_level,
        "direction_selection_receipt": direction_receipt,
        "selection_receipt": parent_receipt,
        "reasons": (
            direction_reason,
            "risk-direction-selected-first",
            "root-only" if not include_derived else "derived-eligible",
        ),
    }
    draft = RiskPoolSelection.model_construct(
        **payload, selection_digest="sha256:" + "0" * 64
    )
    return RiskPoolSelection(
        **payload,
        selection_digest=sha256_digest(draft.digest_payload()),
    )


def build_formal_risk_allocation(
    *,
    catalog: FrozenSeedCatalog,
    progress_states: tuple[RiskTypeProgressState, ...] = (),
    generation_index: int = 0,
    campaign_id: str = "campaign.risk-pool",
    campaign_seed_value: int = 0,
    include_derived: bool = True,
    selectable_seed_ids: frozenset[str] | None = None,
    prefer_lowest_progress: bool = True,
    budget_remaining: int = 1,
) -> FormalRiskAllocation:
    if budget_remaining <= 0:
        raise ValueError("formal risk allocation requires remaining Episode budget")
    selected = select_risk_pool_seed(
        catalog=catalog,
        progress_states=progress_states,
        generation_index=generation_index,
        campaign_id=campaign_id,
        campaign_seed_value=campaign_seed_value,
        include_derived=include_derived,
        selectable_seed_ids=selectable_seed_ids,
        prefer_lowest_progress=prefer_lowest_progress,
    )
    catalog_digest = sha256_digest(catalog.model_dump(mode="json", exclude_none=False))
    progress_digest = sha256_digest(
        tuple(
            state.model_dump(mode="json", exclude_none=False)
            for state in sorted(progress_states, key=lambda item: item.risk_type.value)
        )
    )
    payload = {
        "risk_type": selected.risk_type,
        "parent_seed_id": selected.parent_seed_id,
        "risk_type_progress_level": selected.risk_type_progress_level,
        "direction_selection_receipt": selected.direction_selection_receipt,
        "parent_selection_receipt": selected.selection_receipt,
        "catalog_digest": catalog_digest,
        "risk_progress_digest": progress_digest,
    }
    draft = FormalRiskAllocation.model_construct(
        **payload, allocation_digest="sha256:" + "0" * 64
    )
    return FormalRiskAllocation(
        **payload,
        allocation_digest=sha256_digest(draft.digest_payload()),
    )


def build_priority_risk_allocation(
    *,
    catalog: FrozenSeedCatalog,
    progress_states: tuple[RiskTypeProgressState, ...] = (),
    generation_index: int = 0,
    campaign_id: str = "campaign.risk-pool",
    campaign_seed_value: int = 0,
    seed_id: str,
) -> FormalRiskAllocation:
    """Close one allocation over a queued seed instead of a uniform draw.

    The promoted seed defines both the direction (its own risk pool) and the
    parent, because a generation allocation must close its direction receipt over
    the risk type. Both receipts use the new-seed-priority policy with a single
    option, so the forced choice stays replayable and distinguishable from an
    ordinary uniform draw.
    """

    pool = next(
        (item for item in catalog.pools if any(seed.seed_id == seed_id for seed in item.seeds)),
        None,
    )
    if pool is None:
        raise ValueError("priority seed is not part of the frozen catalog")
    progress = _progress_index(progress_states)
    risk_level = progress.get(pool.risk_type, RiskProgressLevel.NOT_SELECTED)
    direction_receipt = priority_selection(
        selection_kind=SelectionKind.RISK_DIRECTION,
        campaign_id=campaign_id,
        generation_index=generation_index,
        option_id=pool.risk_type.value,
        reason_codes=("new-seed-priority-queue-head", "direction-follows-promoted-seed"),
        campaign_seed_value=campaign_seed_value,
    )
    parent_receipt = priority_selection(
        selection_kind=SelectionKind.PARENT,
        campaign_id=campaign_id,
        generation_index=generation_index,
        option_id=seed_id,
        reason_codes=("new-seed-priority-queue-head", "one-time-exploration-opportunity"),
        campaign_seed_value=campaign_seed_value,
    )
    payload = {
        "risk_type": pool.risk_type,
        "parent_seed_id": seed_id,
        "risk_type_progress_level": risk_level,
        "direction_selection_receipt": direction_receipt,
        "parent_selection_receipt": parent_receipt,
        "catalog_digest": sha256_digest(catalog.model_dump(mode="json", exclude_none=False)),
        "risk_progress_digest": sha256_digest(
            tuple(
                state.model_dump(mode="json", exclude_none=False)
                for state in sorted(progress_states, key=lambda item: item.risk_type.value)
            )
        ),
    }
    draft = FormalRiskAllocation.model_construct(
        **payload, allocation_digest="sha256:" + "0" * 64
    )
    return FormalRiskAllocation(
        **payload,
        allocation_digest=sha256_digest(draft.digest_payload()),
    )


__all__ = [
    "FormalRiskAllocation",
    "RiskPoolSelection",
    "build_formal_risk_allocation",
    "build_priority_risk_allocation",
    "select_risk_pool_seed",
]
