from __future__ import annotations

import pytest

from sandbox.coverage.v2_input import v2_coverage_input_from_direct
from sandbox.coverage.v2_risk_coverage import (
    V2RiskCoverageExtractionError,
    extract_v2_planned_risk_coverage,
)
from tests.unit.test_office_v2_coverage_input import _artifact, _bundles


def _coverage_input():
    direct, _, _ = _bundles()
    return v2_coverage_input_from_direct(
        _artifact("execution.risk-coverage.001", direct),
        container_removed=True,
    )


def test_planned_risk_coverage_uses_fixed_denominator_and_utility_companion() -> None:
    coverage = extract_v2_planned_risk_coverage(_coverage_input())

    assert coverage.denominator.family_total == 4
    assert coverage.denominator.objective_total == 12
    assert coverage.denominator.milestone_total == 23
    assert coverage.denominator.applicable_objective_total == 12
    assert coverage.denominator.reachable_objective_total == 12
    assert coverage.eligibility.submitted
    assert coverage.eligibility.termination_reason == "submit"
    assert coverage.objectives


def test_planned_risk_coverage_is_acquisition_independent() -> None:
    direct, recording, replay = _bundles()
    inputs = tuple(
        v2_coverage_input_from_direct(
            _artifact(f"execution.risk-{index}.001", bundle),
            container_removed=True,
        )
        for index, bundle in enumerate((direct, recording, replay))
    )

    assert len(
        {
            extract_v2_planned_risk_coverage(item).planned_risk_digest
            for item in inputs
        }
    ) == 1


def test_unknown_objective_fails_closed() -> None:
    coverage_input = _coverage_input()
    objective = coverage_input.oracle_facts.security.planned_objectives[0]
    changed = objective.model_copy(update={"objective_id": "objective.unknown"})
    security = coverage_input.oracle_facts.security.model_copy(
        update={"planned_objectives": (changed,)}
    )
    untrusted = coverage_input.model_copy(
        update={
            "oracle_facts": coverage_input.oracle_facts.model_copy(
                update={"security": security}
            )
        }
    )

    with pytest.raises(V2RiskCoverageExtractionError, match="unknown V2 objective"):
        extract_v2_planned_risk_coverage(untrusted)
