"""Deterministic host materialization for accepted Office V2 text slots."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from sandbox.fuzzer.v2_corpus import DeliveredPayload, MaterializedCandidate
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.fork import (
    infer_office_v2_compatibility_purpose,
    rematerialize_office_v2_scenario_text,
)
from sandbox.scenarios.office_v2.models import Identifier, OfficeV2Contract, Sha256Digest

from .v2_candidate import ParsedMutationCandidate
from .v2_contracts import MutationPlan, seal_contract
from .v2_validation import CandidateValidationDisposition, CandidateValidationResult


class TextMaterializationOperation(StrEnum):
    REPLACE = "replace"
    APPEND = "append"
    PREPEND = "prepend"


class SlotMaterializationTarget(OfficeV2Contract):
    payload_slot_id: Identifier
    resource_id: Identifier
    resource_version: Identifier
    field_path: str = Field(min_length=1, max_length=512)
    original_content: str
    operation: TextMaterializationOperation


class MaterializedSlotValue(OfficeV2Contract):
    payload_slot_id: Identifier
    visible_content: str
    original_content_digest: Sha256Digest
    visible_content_digest: Sha256Digest


class DeterministicMaterialization(OfficeV2Contract):
    candidate: MaterializedCandidate
    slot_values: tuple[MaterializedSlotValue, ...] = Field(min_length=1)


def _apply_text(before: str, generated: str, operation: TextMaterializationOperation) -> str:
    if operation is TextMaterializationOperation.REPLACE:
        return generated
    if operation is TextMaterializationOperation.APPEND:
        return f"{before}\n{generated}"
    return f"{generated}\n{before}"


def materialize_candidate(
    *,
    plan: MutationPlan,
    parsed: ParsedMutationCandidate,
    validation: CandidateValidationResult,
    scenario_case_id: str,
    targets: tuple[SlotMaterializationTarget, ...],
) -> DeterministicMaterialization:
    if validation.disposition is not CandidateValidationDisposition.ACCEPTED:
        raise ValueError("only accepted candidate can be materialized")
    generated_by_slot = dict(parsed.slot_values)
    target_by_slot = {item.payload_slot_id: item for item in targets}
    if len(target_by_slot) != len(targets) or set(target_by_slot) != set(generated_by_slot):
        raise ValueError("materialization targets do not match candidate slots")
    values = tuple(
        MaterializedSlotValue(
            payload_slot_id=slot_id,
            visible_content=_apply_text(
                target_by_slot[slot_id].original_content,
                generated,
                target_by_slot[slot_id].operation,
            ),
            original_content_digest=sha256_digest(
                {"content": target_by_slot[slot_id].original_content}
            ),
            visible_content_digest=sha256_digest(
                {
                    "content": _apply_text(
                        target_by_slot[slot_id].original_content,
                        generated,
                        target_by_slot[slot_id].operation,
                    )
                }
            ),
        )
        for slot_id, generated in sorted(generated_by_slot.items())
    )
    delivered = tuple(
        DeliveredPayload(
            payload_spec_id=next(
                item.payload_spec_id
                for item in plan.payload_slots
                if item.payload_slot_id == value.payload_slot_id
            ),
            resource_id=target_by_slot[value.payload_slot_id].resource_id,
            resource_version=target_by_slot[value.payload_slot_id].resource_version,
            field_path=target_by_slot[value.payload_slot_id].field_path,
            content_digest=value.visible_content_digest,
            materialization_evidence_digest=sha256_digest(value),
        )
        for value in values
    )
    base = plan.allocation.base_allocation
    context = plan.allocation.final_context
    payload = {
        "materialized_candidate_id": (
            "materialized."
            + sha256_digest(
                {"plan": plan.plan_digest, "candidate": parsed.candidate_digest}
            ).removeprefix("sha256:")[:24]
        ),
        "seed_id": base.parent_seed_id,
        "generation_allocation_id": base.generation_allocation_id,
        "scenario_case_id": scenario_case_id,
        "actor_id": context.actor_id,
        "task_id": context.task_id,
        "resource_binding_digest": context.resource_binding_digest,
        "delivered_payloads": delivered,
        "binding_source_digest": base.binding_source_digest,
        "comparison_context_digest": context.comparison_context_digest,
        "baseline_snapshot_digest": base.coverage_snapshot_digest,
    }
    candidate = seal_contract(
        MaterializedCandidate, payload, "materialization_digest"
    )
    return DeterministicMaterialization(candidate=candidate, slot_values=values)


__all__ = [
    "DeterministicMaterialization",
    "MaterializedSlotValue",
    "SlotMaterializationTarget",
    "TextMaterializationOperation",
    "infer_office_v2_compatibility_purpose",
    "materialize_candidate",
    "rematerialize_office_v2_scenario_text",
]
