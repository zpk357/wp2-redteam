from __future__ import annotations

import pytest

from sandbox.coverage.v2_behavior import V2BehaviorFeatureKind
from sandbox.fuzzer.v2_corpus import seal_contract
from sandbox.fuzzer.v2_frontier import (
    FrontierKind,
    FrontierSchedulingState,
    MilestoneOutcomeLedger,
    MilestoneState,
    MutationCapabilityManifest,
    build_behavior_frontier,
    compile_risk_frontiers,
    resolve_frontier_readiness,
)
from sandbox.replay.digests import sha256_digest


def digest(label: str) -> str:
    return sha256_digest({"label": label})


def test_risk_frontiers_compile_exactly_four_families_twelve_objectives_23_milestones() -> None:
    frontiers = compile_risk_frontiers()
    assert len(frontiers) == 23
    assert len({item.primary_scheduling_family for item in frontiers}) == 4
    assert len({item.objective_id for item in frontiers}) == 12
    assert len({item.frontier_id for item in frontiers}) == 23


def test_milestone_ledger_is_monotonic_and_preserves_blocked_and_realized() -> None:
    blocked = MilestoneOutcomeLedger(attempted_seen=True, blocked_seen=True)
    realized = MilestoneOutcomeLedger(attempted_seen=True, realized_seen=True)
    merged = blocked.merge(realized)
    assert merged.milestone_state is MilestoneState.REALIZED
    assert merged.blocked_seen is True
    assert merged.realized_seen is True


def test_blocked_or_realized_without_attempt_is_rejected() -> None:
    with pytest.raises(ValueError, match="requires attempted"):
        MilestoneOutcomeLedger(blocked_seen=True)


def test_behavior_frontiers_do_not_collapse_different_anchors_or_gaps() -> None:
    one = build_behavior_frontier(
        scenario_id="office-workspace-v2",
        behavior_gap_kind="missing-successor",
        feature_family=V2BehaviorFeatureKind.TOOL_BIGRAM,
        behavior_anchor_digest=digest("calendar-workspace-email"),
        gap_descriptor_digest=digest("email-successor"),
    )
    two = build_behavior_frontier(
        scenario_id="office-workspace-v2",
        behavior_gap_kind="missing-successor",
        feature_family=V2BehaviorFeatureKind.TOOL_BIGRAM,
        behavior_anchor_digest=digest("drive-calendar"),
        gap_descriptor_digest=digest("calendar-successor"),
    )
    assert one.frontier_id != two.frontier_id


def test_secondary_diversity_cannot_create_behavior_frontier() -> None:
    with pytest.raises(ValueError, match="secondary diversity"):
        build_behavior_frontier(
            scenario_id="office-workspace-v2",
            behavior_gap_kind="more-repetition",
            feature_family=V2BehaviorFeatureKind.INVOCATION_COUNT,
            behavior_anchor_digest=digest("anchor"),
            gap_descriptor_digest=digest("gap"),
        )


def test_missing_operator_parent_and_unreachable_are_distinct() -> None:
    assert (
        resolve_frontier_readiness(
            has_compatible_parent=True, has_compatible_operator=False
        )
        is FrontierSchedulingState.AWAITING_OPERATOR
    )
    assert (
        resolve_frontier_readiness(
            has_compatible_parent=False, has_compatible_operator=True
        )
        is FrontierSchedulingState.AWAITING_PARENT
    )
    assert (
        resolve_frontier_readiness(
            has_compatible_parent=False,
            has_compatible_operator=False,
            stable_unreachable_reason_codes=("platform-denied",),
        )
        is FrontierSchedulingState.UNREACHABLE
    )


def test_mutation_capability_manifest_separates_changed_and_preserved_dimensions() -> None:
    manifest = seal_contract(
        MutationCapabilityManifest,
        {
            "manifest_id": "capability-1",
            "operator_family": "semantic-indirection",
            "required_seed_properties": ("has-agent-facing-text",),
            "allowed_changed_dimensions": ("expression",),
            "preserved_dimensions": ("objective", "world"),
            "supported_frontier_kinds": (FrontierKind.RISK, FrontierKind.BEHAVIOR),
            "supported_objective_ids": ("a03",),
            "supported_behavior_kinds": (V2BehaviorFeatureKind.TOOL_BIGRAM,),
        },
        "manifest_digest",
    )
    assert manifest.allowed_changed_dimensions == ("expression",)

    payload = manifest.model_dump(mode="python", exclude={"manifest_digest"})
    payload["preserved_dimensions"] = ("expression",)
    with pytest.raises(ValueError, match="must not overlap"):
        seal_contract(MutationCapabilityManifest, payload, "manifest_digest")
