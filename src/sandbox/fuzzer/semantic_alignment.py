"""Compare mutation claims with independent static and execution evidence."""

from __future__ import annotations

from sandbox.coverage.models import CoverageResult
from sandbox.fuzzer.models import (
    SemanticAlignmentStatus,
    SemanticExecutionAlignment,
)
from sandbox.mutation.models import MutationCandidate


def build_execution_alignment(
    candidate: MutationCandidate,
    coverage: CoverageResult,
) -> SemanticExecutionAlignment:
    claimed_targets = sorted(
        set(candidate.provider_claimed_target_risks or candidate.target_risks)
    )
    observed = set(coverage.execution_verified_risk_categories)
    verified_claims = sorted(observed.intersection(claimed_targets))
    observed_other = sorted(observed.difference(claimed_targets))
    static_operator = bool(
        candidate.static_alignment and candidate.static_alignment.operator_evidenced
    )
    static_targets = (
        candidate.static_alignment.supported_target_risks
        if candidate.static_alignment
        else []
    )
    operator_effect = bool(coverage.new_behavior_count)
    if claimed_targets and set(claimed_targets).issubset(observed) and static_operator:
        status = SemanticAlignmentStatus.CONFIRMED
    elif observed_other and not verified_claims:
        status = SemanticAlignmentStatus.CONTRADICTED
    elif verified_claims or static_operator or operator_effect:
        status = SemanticAlignmentStatus.PARTIAL
    else:
        status = SemanticAlignmentStatus.NOT_EVIDENCED
    return SemanticExecutionAlignment(
        candidate_id=candidate.mutation_id,
        claimed_operator_id=(
            candidate.provider_claimed_operator_id or candidate.operator_id
        ),
        claimed_target_risks=claimed_targets,
        static_operator_evidenced=static_operator,
        static_supported_target_risks=static_targets,
        execution_verified_target_risks=verified_claims,
        execution_observed_other_risks=observed_other,
        operator_effect_observed=operator_effect,
        status=status,
    )
