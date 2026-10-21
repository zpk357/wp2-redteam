"""Replayable fixed-weight selection receipts for Office V2 scheduling."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import Identifier, OfficeV2Contract, Sha256Digest


class SelectionKind(StrEnum):
    RISK_DIRECTION = "risk_direction"
    PARENT = "parent"
    OPERATOR = "operator"


class SelectionPolicy(StrEnum):
    RANDOM_UNIFORM = "random_uniform_v1"
    #: One-time exploration opportunity for a newly promoted seed. The receipt
    #: carries exactly one option, so the forced choice is replayable instead of
    #: silent: draw=0 over a singleton set.
    NEW_SEED_PRIORITY = "new_seed_priority_v1"


class WeightedSelectionOption(OfficeV2Contract):
    option_id: Identifier
    weight: int = Field(gt=0, le=1_000_000_000)
    reason_codes: tuple[Identifier, ...] = Field(min_length=1)
    option_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"option_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        if self.option_digest != sha256_digest(self.digest_payload()):
            raise ValueError("selection option digest does not match")
        return self


class SelectionReceipt(OfficeV2Contract):
    receipt_version: Literal["office-v2-selection-receipt-v1"] = (
        "office-v2-selection-receipt-v1"
    )
    selection_kind: SelectionKind
    campaign_id: Identifier
    generation_index: int = Field(ge=0)
    campaign_seed: int = Field(ge=0, le=2**63 - 1)
    generation_seed: Sha256Digest
    selection_policy: SelectionPolicy = SelectionPolicy.RANDOM_UNIFORM
    rng_algorithm: Literal["sha256-modulo-v1"] = "sha256-modulo-v1"
    options: tuple[WeightedSelectionOption, ...] = Field(min_length=1)
    total_weight: int = Field(gt=0)
    draw: int = Field(ge=0)
    selected_option_id: Identifier
    receipt_digest: Sha256Digest

    @field_validator("options")
    @classmethod
    def options_are_canonical(
        cls, value: tuple[WeightedSelectionOption, ...]
    ) -> tuple[WeightedSelectionOption, ...]:
        ids = tuple(item.option_id for item in value)
        if len(ids) != len(set(ids)):
            raise ValueError("selection options must be unique")
        return tuple(sorted(value, key=lambda item: item.option_id))

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"receipt_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def selection_and_digest_match(self) -> Self:
        if self.selection_policy is SelectionPolicy.RANDOM_UNIFORM and any(
            item.weight != 1 for item in self.options
        ):
            raise ValueError("random uniform selection requires unit weights")
        if self.selection_policy is SelectionPolicy.NEW_SEED_PRIORITY:
            if len(self.options) != 1 or self.options[0].weight != 1:
                raise ValueError("new-seed priority selection has exactly one option")
            if self.total_weight != 1 or self.draw != 0:
                raise ValueError("new-seed priority selection is drawn from one option")
            if self.selected_option_id != self.options[0].option_id:
                raise ValueError("new-seed priority selection must take its only option")
        if self.total_weight != sum(item.weight for item in self.options):
            raise ValueError("selection total weight does not match options")
        if self.draw >= self.total_weight:
            raise ValueError("selection draw is outside total weight")
        cursor = 0
        selected = None
        for item in self.options:
            cursor += item.weight
            if self.draw < cursor:
                selected = item.option_id
                break
        if selected != self.selected_option_id:
            raise ValueError("selection result does not match weighted draw")
        if self.receipt_digest != sha256_digest(self.digest_payload()):
            raise ValueError("selection receipt digest does not match")
        return self


def campaign_seed(campaign_id: str) -> int:
    return int(sha256_digest({"campaign_id": campaign_id})[7:23], 16) % (2**63)


def independent_uniform_selection(
    *,
    selection_kind: SelectionKind,
    campaign_id: str,
    generation_index: int,
    option_ids: tuple[str, ...],
    campaign_seed_value: int | None = None,
) -> SelectionReceipt:
    """Select uniformly without including prior Campaign state in the RNG input."""

    if not option_ids or len(option_ids) != len(set(option_ids)):
        raise ValueError("independent uniform selection requires unique options")
    options = []
    for option_id in option_ids:
        payload = {
            "option_id": option_id,
            "weight": 1,
            "reason_codes": ("static-compatible", "independent-uniform"),
        }
        draft = WeightedSelectionOption.model_construct(
            **payload, option_digest="sha256:" + "0" * 64
        )
        options.append(
            WeightedSelectionOption(
                **payload, option_digest=sha256_digest(draft.digest_payload())
            )
        )
    options = tuple(options)
    canonical = tuple(sorted(options, key=lambda item: item.option_id))
    seed = campaign_seed(campaign_id) if campaign_seed_value is None else campaign_seed_value
    generation_seed = sha256_digest(
        {
            "algorithm": "sha256-modulo-v1",
            "selection_policy": SelectionPolicy.RANDOM_UNIFORM,
            "selection_kind": selection_kind,
            "campaign_seed": seed,
            "generation_index": generation_index,
            "options": tuple(item.option_id for item in canonical),
        }
    )
    draw = int(generation_seed[7:23], 16) % len(canonical)
    payload = {
        "selection_kind": selection_kind,
        "campaign_id": campaign_id,
        "generation_index": generation_index,
        "campaign_seed": seed,
        "generation_seed": generation_seed,
        "selection_policy": SelectionPolicy.RANDOM_UNIFORM,
        "options": canonical,
        "total_weight": len(canonical),
        "draw": draw,
        "selected_option_id": canonical[draw].option_id,
    }
    draft = SelectionReceipt.model_construct(
        **payload, receipt_digest="sha256:" + "0" * 64
    )
    return SelectionReceipt(
        **payload, receipt_digest=sha256_digest(draft.digest_payload())
    )


def priority_selection(
    *,
    selection_kind: SelectionKind,
    campaign_id: str,
    generation_index: int,
    option_id: str,
    reason_codes: tuple[str, ...],
    campaign_seed_value: int | None = None,
) -> SelectionReceipt:
    """Record a forced one-time choice with a replayable single-option receipt."""

    if not reason_codes:
        raise ValueError("new-seed priority selection requires a reason code")
    payload = {
        "option_id": option_id,
        "weight": 1,
        "reason_codes": tuple(reason_codes),
    }
    draft = WeightedSelectionOption.model_construct(
        **payload, option_digest="sha256:" + "0" * 64
    )
    option = WeightedSelectionOption(
        **payload, option_digest=sha256_digest(draft.digest_payload())
    )
    seed = campaign_seed(campaign_id) if campaign_seed_value is None else campaign_seed_value
    generation_seed = sha256_digest(
        {
            "algorithm": "sha256-modulo-v1",
            "selection_policy": SelectionPolicy.NEW_SEED_PRIORITY,
            "selection_kind": selection_kind,
            "campaign_seed": seed,
            "generation_index": generation_index,
            "options": (option.option_id,),
        }
    )
    receipt_payload = {
        "selection_kind": selection_kind,
        "campaign_id": campaign_id,
        "generation_index": generation_index,
        "campaign_seed": seed,
        "generation_seed": generation_seed,
        "selection_policy": SelectionPolicy.NEW_SEED_PRIORITY,
        "options": (option,),
        "total_weight": 1,
        "draw": 0,
        "selected_option_id": option.option_id,
    }
    receipt_draft = SelectionReceipt.model_construct(
        **receipt_payload, receipt_digest="sha256:" + "0" * 64
    )
    return SelectionReceipt(
        **receipt_payload,
        receipt_digest=sha256_digest(receipt_draft.digest_payload()),
    )


__all__ = [
    "SelectionKind",
    "SelectionPolicy",
    "SelectionReceipt",
    "WeightedSelectionOption",
    "campaign_seed",
    "independent_uniform_selection",
    "priority_selection",
]
