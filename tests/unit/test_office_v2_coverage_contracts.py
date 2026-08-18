from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from sandbox.coverage.v2_contracts import (
    V2_COVERAGE_CONTRACT_IDENTITY,
    V2CandidateBatchBaseline,
    V2CoverageContractError,
    V2ExposureProgress,
    V2MilestoneOutcomeBits,
    V2RiskFamily,
    build_v2_candidate_batch_baseline,
    build_v2_episode_eligibility_facts,
    build_v2_objective_risk_classification,
)
from sandbox.coverage.v2_input import v2_coverage_input_from_direct
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.attack_objectives import ATTACK_OBJECTIVE_CATALOG
from sandbox.scenarios.office_v2.oracle_models import (
    ExposureStage,
    MilestoneOutcome,
    UtilityDisposition,
)
from tests.unit.test_office_v2_coverage_input import _artifact, _bundles


def test_contract_identity_locks_components_and_objective_catalog() -> None:
    identity = V2_COVERAGE_CONTRACT_IDENTITY

    assert identity.objective_catalog_version == ATTACK_OBJECTIVE_CATALOG.catalog_version
    assert identity.objective_catalog_digest == ATTACK_OBJECTIVE_CATALOG.catalog_digest
    assert len(identity.components) == 6

    payload = identity.model_dump(mode="json", exclude_none=False)
    payload["components"][0]["content_digest"] = sha256_digest("tampered")
    payload["identity_digest"] = sha256_digest(
        {key: value for key, value in payload.items() if key != "identity_digest"}
    )
    with pytest.raises(ValidationError, match="component digest does not match"):
        type(identity).model_validate(payload)


def test_objective_classification_has_one_primary_and_multiple_fact_facets() -> None:
    classification = build_v2_objective_risk_classification(
        objective_id="objective.a01.external-acl-share-link",
        primary_scheduling_family=V2RiskFamily.SENSITIVE_INFORMATION_DISCLOSURE,
        risk_facets=(
            V2RiskFamily.UNAUTHORIZED_OPERATION_PERMISSION_EXPANSION,
            V2RiskFamily.SENSITIVE_INFORMATION_DISCLOSURE,
        ),
    )

    assert classification.primary_scheduling_family in classification.risk_facets
    assert classification.risk_facets == tuple(
        sorted(classification.risk_facets, key=lambda item: item.value)
    )

    with pytest.raises(ValueError, match="primary scheduling family"):
        build_v2_objective_risk_classification(
            objective_id="objective.a01.external-acl-share-link",
            primary_scheduling_family=V2RiskFamily.SENSITIVE_INFORMATION_DISCLOSURE,
            risk_facets=(V2RiskFamily.UNAUTHORIZED_OPERATION_PERMISSION_EXPANSION,),
        )


def test_campaign_milestone_algebra_preserves_blocked_and_realized_branches() -> None:
    blocked = V2MilestoneOutcomeBits.from_episode_outcome(MilestoneOutcome.BLOCKED)
    realized = V2MilestoneOutcomeBits.from_episode_outcome(MilestoneOutcome.REALIZED)

    merged = blocked.merged(realized)

    assert merged.attempted_seen
    assert merged.blocked_seen
    assert merged.realized_seen
    assert V2MilestoneOutcomeBits.from_episode_outcome(
        MilestoneOutcome.NOT_REACHED
    ) == V2MilestoneOutcomeBits()
    with pytest.raises(ValidationError, match="requires attempted coverage"):
        V2MilestoneOutcomeBits(blocked_seen=True)


def test_exposure_progress_is_ordered_independently_from_outcome_bits() -> None:
    observed = V2ExposureProgress(highest_stage=ExposureStage.OBSERVED)
    delivered = V2ExposureProgress(highest_stage=ExposureStage.DELIVERED)
    used = V2ExposureProgress(highest_stage=ExposureStage.USED)

    assert observed.merged(delivered).highest_stage is ExposureStage.OBSERVED
    assert observed.merged(used).highest_stage is ExposureStage.USED


def test_utility_facts_continue_with_coverage_without_becoming_coverage() -> None:
    bundle, _, _ = _bundles()
    coverage_input = v2_coverage_input_from_direct(
        _artifact("execution.coverage.contracts.001", bundle),
        container_removed=True,
    )

    eligibility = build_v2_episode_eligibility_facts(coverage_input)
    utility = coverage_input.oracle_facts.utility

    assert eligibility.canonical_fact_digest == coverage_input.canonical_fact_digest
    assert eligibility.utility_disposition is utility.disposition
    assert eligibility.required_goals_satisfied == (not utility.unmet_required_goal_ids)
    assert eligibility.normal_task_completed == (
        utility.disposition is UtilityDisposition.COMPLETED
    )
    assert eligibility.submitted is utility.submitted
    assert eligibility.termination_reason == utility.termination_reason

    with pytest.raises(V2CoverageContractError, match="require V2CoverageInput"):
        build_v2_episode_eligibility_facts(object())  # type: ignore[arg-type]


def test_candidate_batch_baseline_is_canonical_and_identity_locked() -> None:
    batch = build_v2_candidate_batch_baseline(
        campaign_id="campaign.office-v2.001",
        candidate_set_id="candidate-set.001",
        candidate_set_digest=sha256_digest("candidate-set"),
        candidate_ids=("candidate.002", "candidate.001"),
        baseline_snapshot_digest=sha256_digest("baseline"),
    )

    assert batch.candidate_ids == ("candidate.001", "candidate.002")
    assert batch.coverage_identity_digest == V2_COVERAGE_CONTRACT_IDENTITY.identity_digest

    payload = batch.model_dump(mode="json", exclude_none=False)
    payload["coverage_identity_digest"] = sha256_digest("v1-identity")
    payload["batch_baseline_digest"] = sha256_digest(
        {key: value for key, value in payload.items() if key != "batch_baseline_digest"}
    )
    with pytest.raises(ValidationError, match="wrong coverage identity"):
        V2CandidateBatchBaseline.model_validate(payload)


def test_v2_contract_module_does_not_import_v1_coverage_semantics() -> None:
    module_path = (
        Path(__file__).parents[2] / "src" / "sandbox" / "coverage" / "v2_contracts.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )

    forbidden = {
        "sandbox.coverage.feature_normalizer",
        "sandbox.coverage.input",
        "sandbox.coverage.models",
        "sandbox.coverage.office_risk",
        "sandbox.coverage.store",
    }
    assert imported.isdisjoint(forbidden)
