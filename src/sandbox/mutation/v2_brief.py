"""Minimal host-built facts and provider response schema for Office V2."""

from __future__ import annotations

from typing import Self

from pydantic import Field, field_validator, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import Identifier, OfficeV2Contract, Sha256Digest

from .v2_contracts import MutationPlan

V2_MUTATION_PROMPT_VERSION = "office-v2-mutation-prompt-v1"
V2_MUTATION_RESPONSE_SCHEMA_VERSION = "office-v2-mutation-response-v1"


class BriefFact(OfficeV2Contract):
    fact_kind: Identifier
    public_label: str = Field(min_length=1, max_length=256)
    public_value: str = Field(min_length=1, max_length=2048)


class BriefSlot(OfficeV2Contract):
    payload_slot_id: Identifier
    placement_description: str = Field(min_length=1, max_length=512)
    content_constraints: tuple[Identifier, ...] = Field(default_factory=tuple)
    max_length: int = Field(gt=0, le=8192)


class MinimalFactBrief(OfficeV2Contract):
    brief_id: Identifier
    mutation_plan_digest: Sha256Digest
    frontier_description: str = Field(min_length=1, max_length=512)
    operator_instructions: tuple[str, ...] = Field(min_length=1)
    scenario_facts: tuple[BriefFact, ...] = Field(default_factory=tuple)
    parent_payload_texts: tuple[str, ...] = Field(min_length=1)
    slots: tuple[BriefSlot, ...] = Field(min_length=1)
    forbidden_changes: tuple[Identifier, ...] = Field(min_length=1)
    prompt_version: Identifier = V2_MUTATION_PROMPT_VERSION
    response_schema_version: Identifier = V2_MUTATION_RESPONSE_SCHEMA_VERSION
    brief_digest: Sha256Digest

    @field_validator("slots")
    @classmethod
    def slots_are_canonical(cls, value: tuple[BriefSlot, ...]) -> tuple[BriefSlot, ...]:
        ids = tuple(item.payload_slot_id for item in value)
        if len(ids) != len(set(ids)):
            raise ValueError("brief payload slot ids must be unique")
        return tuple(sorted(value, key=lambda item: item.payload_slot_id))

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"brief_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        if self.brief_digest != sha256_digest(self.digest_payload()):
            raise ValueError("minimal fact brief digest does not match")
        return self


class ProviderSlotValue(OfficeV2Contract):
    payload_slot_id: Identifier
    generated_content: str = Field(min_length=1, max_length=8192)


class MutationCandidateResponse(OfficeV2Contract):
    slot_values: tuple[ProviderSlotValue, ...] = Field(min_length=1)
    expression_metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("slot_values")
    @classmethod
    def slot_values_are_unique(
        cls, value: tuple[ProviderSlotValue, ...]
    ) -> tuple[ProviderSlotValue, ...]:
        ids = tuple(item.payload_slot_id for item in value)
        if len(ids) != len(set(ids)):
            raise ValueError("provider returned a payload slot more than once")
        return tuple(sorted(value, key=lambda item: item.payload_slot_id))


V2_MUTATION_PROMPT_IDENTITY_DIGEST = sha256_digest(
    {
        "version": V2_MUTATION_PROMPT_VERSION,
        "authority": "fill-host-frozen-text-slots-only",
        "candidate_count": 1,
        "output": "strict-json",
    }
)
V2_MUTATION_RESPONSE_SCHEMA_DIGEST = sha256_digest(
    {
        "version": V2_MUTATION_RESPONSE_SCHEMA_VERSION,
        "schema": MutationCandidateResponse.model_json_schema(),
    }
)


def build_minimal_fact_brief(
    *,
    plan: MutationPlan,
    frontier_description: str,
    operator_instructions: tuple[str, ...],
    scenario_facts: tuple[BriefFact, ...],
    parent_payload_texts: tuple[str, ...],
) -> MinimalFactBrief:
    if plan.prompt_identity_digest != V2_MUTATION_PROMPT_IDENTITY_DIGEST:
        raise ValueError("mutation plan prompt identity does not match")
    if plan.response_schema_digest != V2_MUTATION_RESPONSE_SCHEMA_DIGEST:
        raise ValueError("mutation plan response schema identity does not match")
    slots = tuple(
        BriefSlot(
            payload_slot_id=item.payload_slot_id,
            placement_description=item.placement_ref,
            content_constraints=item.content_constraints,
            max_length=item.max_length,
        )
        for item in plan.payload_slots
    )
    payload = {
        "brief_id": f"brief.{plan.plan_digest.removeprefix('sha256:')[:24]}",
        "mutation_plan_digest": plan.plan_digest,
        "frontier_description": frontier_description,
        "operator_instructions": operator_instructions,
        "scenario_facts": scenario_facts,
        "parent_payload_texts": parent_payload_texts,
        "slots": slots,
        "forbidden_changes": (
            "actor",
            "authorization",
            "canonical_world",
            "frontier",
            "objective",
            "operator",
            "placement",
            "resource_binding",
            "task",
        ),
    }
    draft = MinimalFactBrief.model_construct(
        **payload, brief_digest="sha256:" + "0" * 64
    )
    return MinimalFactBrief(
        **payload, brief_digest=sha256_digest(draft.digest_payload())
    )


__all__ = [
    "BriefFact",
    "BriefSlot",
    "MinimalFactBrief",
    "MutationCandidateResponse",
    "ProviderSlotValue",
    "V2_MUTATION_PROMPT_IDENTITY_DIGEST",
    "V2_MUTATION_PROMPT_VERSION",
    "V2_MUTATION_RESPONSE_SCHEMA_DIGEST",
    "V2_MUTATION_RESPONSE_SCHEMA_VERSION",
    "build_minimal_fact_brief",
]
