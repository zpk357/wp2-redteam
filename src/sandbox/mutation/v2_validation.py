"""Fourteen host-owned validation layers for Office V2 candidates."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from sandbox.scenarios.office_v2.models import Identifier, OfficeV2Contract

from .v2_candidate import ParsedMutationCandidate
from .v2_contracts import MutationFieldAuthority, MutationFieldRegistry, MutationPlan


class CandidateValidationDisposition(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PAUSED = "paused"


class ValidationCheck(OfficeV2Contract):
    layer: int = Field(ge=1, le=14)
    check_id: Identifier
    passed: bool
    reason_code: Identifier


class CandidateValidationResult(OfficeV2Contract):
    disposition: CandidateValidationDisposition
    checks: tuple[ValidationCheck, ...] = Field(min_length=14, max_length=14)
    exact_duplicate: bool = False
    lineage_duplicate: bool = False
    repeated_structure: bool = False
    near_duplicate_score: float = Field(default=0.0, ge=0.0, le=1.0)


def validate_candidate(
    *,
    plan: MutationPlan,
    registry: MutationFieldRegistry,
    candidate: ParsedMutationCandidate,
    known_candidate_digests: frozenset[str] = frozenset(),
    known_content_digests: frozenset[str] = frozenset(),
    recent_lineage_content_digests: frozenset[str] = frozenset(),
    recent_structure_signature_digests: frozenset[str] = frozenset(),
    cumulative_output_tokens: int = 0,
) -> CandidateValidationResult:
    expected_slots = {item.payload_slot_id: item for item in plan.payload_slots}
    actual_slots = dict(candidate.slot_values)
    rules = {item.field_path: item for item in registry.rules}
    provider_rule = rules.get("condition.payload_slots.generated_content")
    identity_matches = True
    provider_authority_matches = (
        provider_rule is not None
        and provider_rule.authority is MutationFieldAuthority.PROVIDER_TEXT
    )
    lengths_valid = all(
        slot.min_length <= len(actual_slots.get(slot_id, "")) <= slot.max_length
        for slot_id, slot in expected_slots.items()
    )
    authorization_preserved = (
        plan.allocation.final_context.authorization_branch
        == plan.allocation.initial_context.authorization_branch
    )
    text_changed = bool(candidate.text_diffs) and all(
        item.changed for item in candidate.text_diffs
    )
    exact_duplicate = (
        not text_changed
        or candidate.candidate_digest in known_candidate_digests
        or candidate.normalized_content_digest in known_content_digests
    )
    lineage_duplicate = (
        candidate.normalized_content_digest in recent_lineage_content_digests
    )
    checks = (
        ("mutation-identity", identity_matches, "identity-drift"),
        (
            "allocation-lineage",
            candidate.mutation_plan_digest == plan.plan_digest,
            "allocation-drift",
        ),
        (
            "field-registry",
            registry.registry_digest == plan.field_registry_digest,
            "registry-drift",
        ),
        ("provider-authority", provider_authority_matches, "provider-authority-drift"),
        ("candidate-count", True, "candidate-count-invalid"),
        ("slot-cardinality", set(actual_slots) == set(expected_slots), "slot-set-mismatch"),
        ("strict-schema", bool(actual_slots), "schema-invalid"),
        ("text-length", lengths_valid, "slot-length-invalid"),
        (
            "semantic-text-slot",
            all(
                slot.field_path == "condition.payload_slots.generated_content"
                for slot in expected_slots.values()
            ),
            "non-semantic-text-slot",
        ),
        (
            "objective-structural",
            candidate.structural_objective_preserved,
            "objective-drift",
        ),
        (
            "world-frozen",
            "scenario.canonical_world" in plan.preserved_field_paths,
            "world-not-preserved",
        ),
        ("authorization-frozen", authorization_preserved, "authorization-drift"),
        (
            "exact-duplicate",
            not exact_duplicate and not lineage_duplicate,
            (
                "candidate-noop"
                if not text_changed
                else "lineage-exact-duplicate"
                if lineage_duplicate
                else "exact-duplicate"
            ),
        ),
        (
            "budget-and-lineage",
            cumulative_output_tokens <= plan.budget.plan_total_token_budget,
            "provider-budget-exceeded",
        ),
    )
    results = tuple(
        ValidationCheck(
            layer=index,
            check_id=check_id,
            passed=passed,
            reason_code="ok" if passed else reason,
        )
        for index, (check_id, passed, reason) in enumerate(checks, start=1)
    )
    failed = tuple(item for item in results if not item.passed)
    pause_reasons = {"identity-drift", "registry-drift", "provider-authority-drift"}
    disposition = CandidateValidationDisposition.ACCEPTED
    if failed:
        disposition = (
            CandidateValidationDisposition.PAUSED
            if any(item.reason_code in pause_reasons for item in failed)
            else CandidateValidationDisposition.REJECTED
        )
    return CandidateValidationResult(
        disposition=disposition,
        checks=results,
        exact_duplicate=exact_duplicate,
        lineage_duplicate=lineage_duplicate,
        repeated_structure=(
            candidate.structure_signature_digest
            in recent_structure_signature_digests
        ),
    )


__all__ = [
    "CandidateValidationDisposition",
    "CandidateValidationResult",
    "ValidationCheck",
    "validate_candidate",
]
