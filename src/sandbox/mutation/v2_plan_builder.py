"""Host-only bridge from a frozen Scheduler decision to one MutationPlan."""

from __future__ import annotations

from sandbox.fuzzer.v2_corpus import ExecutableSeedSupport, ExecutionRecord
from sandbox.fuzzer.v2_orchestrator import GenerationDecision
from sandbox.fuzzer.v2_scheduler import (
    ComparisonContext,
    MutationGenerationAllocation,
    OperatorAllocation,
)
from sandbox.fuzzer.v2_strategy import CampaignStrategy
from sandbox.replay.digests import sha256_digest

from .v2_brief import (
    V2_MUTATION_PROMPT_IDENTITY_DIGEST,
    V2_MUTATION_RESPONSE_SCHEMA_DIGEST,
)
from .v2_contracts import (
    MutationIntent,
    MutationPlan,
    MutationProviderBudget,
    PayloadSlot,
    build_v2_mutation_field_registry,
    seal_contract,
)


def build_semantic_mutation_plan(
    *,
    decision: GenerationDecision,
    parent_seed: ExecutableSeedSupport,
    supporting_execution: ExecutionRecord,
    operator_allocation: OperatorAllocation,
    provider_id: str,
    campaign_strategy: CampaignStrategy | str = CampaignStrategy.COVERAGE_GUIDED,
) -> MutationPlan:
    allocation = decision.allocation
    if parent_seed.seed_id != allocation.executable_seed_id:
        raise ValueError("executable parent seed differs from Scheduler allocation")
    if supporting_execution.execution_record_id != allocation.supporting_execution_record_id:
        raise ValueError("supporting execution differs from Scheduler allocation")
    if supporting_execution.seed_id != parent_seed.seed_id:
        raise ValueError("supporting execution belongs to another seed")
    if operator_allocation.risk_type is not allocation.risk_type:
        raise ValueError("operator allocation differs from Scheduler risk pool")
    if (
        operator_allocation.supporting_execution_record_id
        != supporting_execution.execution_record_id
    ):
        raise ValueError("operator allocation differs from supporting execution")

    authorization_branch = "unchanged"
    context = seal_contract(
        ComparisonContext,
        {
            "actor_id": supporting_execution.actor_id,
            "task_id": supporting_execution.task_id,
            "resource_binding_digest": supporting_execution.resource_binding_digest,
            "allocation_target_digest": allocation.allocation_target_digest,
            "authorization_branch": authorization_branch,
            "baseline_snapshot_digest": allocation.coverage_snapshot_digest,
        },
        "comparison_context_digest",
    )
    operator = operator_allocation
    mutation_allocation = seal_contract(
        MutationGenerationAllocation,
        {
            "mutation_generation_allocation_id": (
                "mutation-allocation."
                + allocation.allocation_digest.removeprefix("sha256:")[:24]
            ),
            "base_allocation": allocation,
            "initial_context": context,
            "operator_allocation": operator,
            "final_context": context,
        },
        "mutation_allocation_digest",
    )
    intent = seal_contract(
        MutationIntent,
        {
            "mutation_intent_id": (
                "intent."
                + mutation_allocation.mutation_allocation_digest.removeprefix("sha256:")[:24]
            ),
            "mutation_allocation_digest": mutation_allocation.mutation_allocation_digest,
            "risk_type": allocation.risk_type,
            "parent_seed_id": allocation.parent_seed_id,
            "executable_seed_id": parent_seed.seed_id,
            "supporting_execution_record_id": supporting_execution.execution_record_id,
            "binding_source_digest": allocation.binding_source_digest,
            "comparison_context_digest": context.comparison_context_digest,
            "baseline_snapshot_digest": allocation.coverage_snapshot_digest,
            "operator_allocation_digest": operator.operator_allocation_digest,
        },
        "intent_digest",
    )
    payload = parent_seed.payload_specs[0]
    slot = PayloadSlot(
        payload_slot_id=f"slot.{payload.payload_spec_id}",
        payload_spec_id=payload.payload_spec_id,
        field_path="condition.payload_slots.generated_content",
        min_length=1,
        max_length=2048,
        content_constraints=("plain-text", "single-candidate"),
    )
    registry = build_v2_mutation_field_registry()
    plan_payload = {
        "mutation_plan_id": (
            "plan."
            + sha256_digest(
                {
                    "allocation": mutation_allocation.mutation_allocation_digest,
                    "slot": slot.payload_slot_id,
                }
            ).removeprefix("sha256:")[:24]
        ),
        "intent": intent,
        "allocation": mutation_allocation,
        "campaign_strategy": CampaignStrategy(campaign_strategy),
        "payload_slots": (slot,),
        "field_registry_digest": registry.registry_digest,
        "changed_field_paths": (
            "condition.digests",
            "condition.payload_slots.generated_content",
        ),
        "preserved_field_paths": (
            "scenario.actor",
            "scenario.authorization_branch",
            "scenario.canonical_world",
            "scenario.objective",
            "scenario.parent_case",
            "scenario.resource_bindings",
            "scenario.task",
        ),
        "provider_id": provider_id,
        "prompt_identity_digest": V2_MUTATION_PROMPT_IDENTITY_DIGEST,
        "response_schema_digest": V2_MUTATION_RESPONSE_SCHEMA_DIGEST,
        "budget": MutationProviderBudget(
            plan_total_token_budget=4096,
            per_attempt_token_limit=2048,
            reserved_total_cost_microunits=0,
            max_attempts=2,
            timeout_ms=300_000,
        ),
        "mutation_identity_digest": None,
    }
    return seal_contract(MutationPlan, plan_payload, "plan_digest")


__all__ = ["build_semantic_mutation_plan"]
