from __future__ import annotations

from types import SimpleNamespace

import pytest

from sandbox.coverage.v2_contracts import V2RiskFamily
from sandbox.coverage.v2_input import v2_coverage_input_from_direct
from sandbox.coverage.v2_unexpected_risk import (
    V2UnexpectedRiskMappingError,
    map_v2_unexpected_risks,
)
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.oracle import evaluate_scenario_oracle
from sandbox.scenarios.office_v2.oracle_models import MilestoneOutcome
from tests.unit.test_office_v2_utility_evaluator import _t10_bundle


def _unexpected_input():
    bundle, materialization = _t10_bundle()
    result = evaluate_scenario_oracle(
        bundle=bundle,
        scenario_case=materialization.scenario_case,
    )
    artifact = SimpleNamespace(
        execution_id="execution.unexpected-risk.001",
        artifact_digest=sha256_digest("unexpected-risk-artifact"),
        evidence_bundle=bundle,
        oracle_result=result,
    )
    return v2_coverage_input_from_direct(artifact, container_removed=True)


def test_unexpected_committed_action_maps_facets_without_inventing_intent() -> None:
    mapped = map_v2_unexpected_risks(_unexpected_input())

    assert mapped.violations
    fact = mapped.violations[0]
    assert fact.planned is False
    assert fact.matched_objective_id is None
    assert fact.side_effect_committed
    assert fact.outcome is MilestoneOutcome.REALIZED
    assert V2RiskFamily.UNAUTHORIZED_OPERATION_PERMISSION_EXPANSION in fact.risk_facets
    assert fact.exchange_sequences
    assert fact.evidence_refs


def test_planned_violations_are_not_duplicated_as_unexpected() -> None:
    coverage_input = _unexpected_input()
    unexpected = coverage_input.oracle_facts.security.violations[0]
    planned = unexpected.model_copy(
        update={"planned": True, "objective_id": "objective.a10.unauthorized-external-event"}
    )
    security = coverage_input.oracle_facts.security.model_copy(
        update={"violations": (planned,)}
    )
    changed = coverage_input.model_copy(
        update={
            "oracle_facts": coverage_input.oracle_facts.model_copy(
                update={"security": security}
            )
        }
    )

    assert map_v2_unexpected_risks(changed).violations == ()


def test_unresolved_unexpected_evidence_fails_closed() -> None:
    coverage_input = _unexpected_input()
    violation = coverage_input.oracle_facts.security.violations[0]
    dangling = violation.model_copy(
        update={
            "evidence_refs": tuple(
                ref.model_copy(update={"evidence_id": f"dangling.{index}"})
                for index, ref in enumerate(violation.evidence_refs)
            )
        }
    )
    security = coverage_input.oracle_facts.security.model_copy(
        update={"violations": (dangling,)}
    )
    changed = coverage_input.model_copy(
        update={
            "oracle_facts": coverage_input.oracle_facts.model_copy(
                update={"security": security}
            )
        }
    )

    with pytest.raises(V2UnexpectedRiskMappingError, match="does not resolve"):
        map_v2_unexpected_risks(changed)
