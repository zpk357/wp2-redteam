from __future__ import annotations

from sandbox.coverage.v2_contracts import V2RiskFamily
from sandbox.coverage.v2_risk_catalog import (
    V2_RISK_CATALOG,
    compile_v2_risk_catalog,
)
from sandbox.scenarios.office_v2.attack_objectives import ATTACK_OBJECTIVE_CATALOG


def test_catalog_compiles_four_families_twelve_objectives_and_23_milestones() -> None:
    catalog = V2_RISK_CATALOG
    assert len(catalog.families) == 4
    assert len(catalog.objectives) == 12
    assert catalog.milestone_count == 23
    assert catalog.source_objective_catalog_digest == ATTACK_OBJECTIVE_CATALOG.catalog_digest
    assert compile_v2_risk_catalog() == catalog


def test_classification_keeps_one_primary_family_and_multiple_true_facets() -> None:
    a01 = next(
        item
        for item in V2_RISK_CATALOG.objectives
        if item.objective_id == "objective.a01.external-acl-share-link"
    )
    assert a01.classification.primary_scheduling_family is (
        V2RiskFamily.SENSITIVE_INFORMATION_DISCLOSURE
    )
    assert set(a01.classification.risk_facets) == {
        V2RiskFamily.SENSITIVE_INFORMATION_DISCLOSURE,
        V2RiskFamily.UNAUTHORIZED_OPERATION_PERMISSION_EXPANSION,
    }
    assert [item.milestone_id for item in a01.milestones] == [
        "milestone.acl",
        "milestone.notify",
        "milestone.share",
    ]


def test_atomic_milestone_id_matches_the_frozen_oracle_rule() -> None:
    a03 = next(
        item
        for item in V2_RISK_CATALOG.objectives
        if item.objective_id == "objective.a03.sensitive-mail-exfiltration"
    )
    assert a03.milestones[0].milestone_id == (
        "milestone.atomic.objective.a03.sensitive-mail-exfiltration"
    )
    assert a03.compatible_entry_kinds
    assert a03.applicable and a03.reachable
