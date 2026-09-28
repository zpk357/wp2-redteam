from __future__ import annotations

from sandbox.fuzzer.v2_seed_pools import RiskType
from sandbox.mutation.v2_policy import (
    FORMAL_OPERATOR_CATALOG_DIGEST,
    FORMAL_OPERATOR_DEFINITIONS,
    FormalOperatorFamily,
    OperatorSelectionStatus,
    select_formal_operator,
)


def test_formal_operator_directory_has_twelve_families_and_concrete_constraints() -> None:
    assert len(FORMAL_OPERATOR_DEFINITIONS) == 12
    assert all(len(item.variants) >= 4 for item in FORMAL_OPERATOR_DEFINITIONS)
    assert FORMAL_OPERATOR_CATALOG_DIGEST.startswith("sha256:")
    assert all(
        variant.constraints
        and variant.forbidden_transformations
        and variant.risk_applicability
        for item in FORMAL_OPERATOR_DEFINITIONS
        for variant in item.variants
    )
    assert all(
        {"preserve-attack-target", "preserve-frozen-facts"}.issubset(
            variant.constraints
        )
        and {"change-requested-action", "change-actual-authorization"}.issubset(
            variant.forbidden_transformations
        )
        for item in FORMAL_OPERATOR_DEFINITIONS
        for variant in item.variants
    )
    assert any(
        set(variant.risk_applicability) != set(RiskType)
        for item in FORMAL_OPERATOR_DEFINITIONS
        for variant in item.variants
    )


def test_formal_operator_selection_filters_variants_by_selected_risk_pool() -> None:
    decision = select_formal_operator(
        campaign_id="campaign-risk-filter",
        generation_index=2,
        risk_type=RiskType.SENSITIVE_INFORMATION_DISCLOSURE,
        supporting_record_id="record-risk-filter",
    )
    assert decision.status is OperatorSelectionStatus.SELECTED
    assert decision.allocation is not None
    allocation = decision.allocation
    selected_variants = []
    for family_id, variant_id in zip(
        allocation.selected_operator_families,
        allocation.selected_operator_variants,
        strict=True,
    ):
        family = FormalOperatorFamily(family_id)
        definition = next(item for item in FORMAL_OPERATOR_DEFINITIONS if item.family_id is family)
        selected = next(item for item in definition.variants if item.variant_id == variant_id)
        assert RiskType.SENSITIVE_INFORMATION_DISCLOSURE in selected.risk_applicability
        selected_variants.append(selected)
    assert allocation.operator_constraint_digests == tuple(
        item.variant_digest for item in selected_variants
    )


def test_each_risk_pool_has_multiple_applicable_formal_variants() -> None:
    for risk_type in RiskType:
        applicable = [
            variant
            for definition in FORMAL_OPERATOR_DEFINITIONS
            for variant in definition.variants
            if risk_type in variant.risk_applicability
        ]
        assert len(applicable) >= 12


def test_operator_selection_uniformly_draws_one_to_three_distinct_families() -> None:
    counts = set()
    for index in range(90):
        decision = select_formal_operator(
            campaign_id=f"operator-count-{index}",
            generation_index=1,
            risk_type=RiskType.DESTRUCTIVE_OPERATION,
            supporting_record_id="record-count",
        )
        assert decision.allocation is not None
        allocation = decision.allocation
        counts.add(len(allocation.selected_operator_families))
        assert 1 <= len(allocation.selected_operator_families) <= 3
        assert len(set(allocation.selected_operator_families)) == len(
            allocation.selected_operator_families
        )
        assert allocation.operator_count_selection_receipt.selected_option_id == str(
            len(allocation.selected_operator_families)
        )
        assert tuple(
            receipt.selected_option_id for receipt in allocation.family_selection_receipts
        ) == allocation.selected_operator_families
        assert tuple(
            receipt.selected_option_id for receipt in allocation.variant_selection_receipts
        ) == allocation.selected_operator_variants
        for position, receipt in enumerate(allocation.family_selection_receipts):
            assert not set(allocation.selected_operator_families[:position]).intersection(
                option.option_id for option in receipt.options
            )
        assert all(
            variant_id.startswith(f"{family_id}.")
            for family_id, variant_id in zip(
                allocation.selected_operator_families,
                allocation.selected_operator_variants,
                strict=True,
            )
        )
    assert counts == {1, 2, 3}


def test_source_reference_is_expression_only_and_has_no_carrier_variants() -> None:
    definition = next(
        item
        for item in FORMAL_OPERATOR_DEFINITIONS
        if item.family_id is FormalOperatorFamily.SOURCE_REFERENCE
    )
    assert {item.variant_id for item in definition.variants} == {
        "source_reference.explicit_target",
        "source_reference.target_name",
        "source_reference.context_reference",
        "source_reference.fact_restating",
    }
    text = " ".join(
        f"{item.instruction} {' '.join(item.constraints)}"
        for item in definition.variants
    ).lower()
    assert not any(word in text for word in ("mail", "document", "task field", "carrier"))


def test_authority_claim_changes_textual_claim_not_actual_authorization() -> None:
    definition = next(
        item
        for item in FORMAL_OPERATOR_DEFINITIONS
        if item.family_id is FormalOperatorFamily.AUTHORITY_CLAIM
    )
    assert {
        "authority_claim.manager_approval",
        "authority_claim.delegated_approval",
        "authority_claim.policy_exception",
        "authority_claim.team_mandate",
    } == {item.variant_id for item in definition.variants}
    assert all(
        "authorization-claim-is-untrusted-text" in item.constraints
        and "preserve-actual-delegation" in item.constraints
        and "change-actual-authorization" in item.forbidden_transformations
        for item in definition.variants
    )
