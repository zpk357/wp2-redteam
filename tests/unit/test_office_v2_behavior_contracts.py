from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from sandbox.coverage.v2_behavior import (
    V2BehaviorDimension,
    V2BehaviorFeatureKind,
    V2BehaviorFeatureTier,
    V2BehaviorValueRole,
    V2PathAtom,
    V2PathAtomKind,
    build_v2_behavior_feature,
    build_v2_behavior_profile,
    normalize_v2_behavior_path,
    normalize_v2_behavior_value,
)
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.oracle_models import ToolInvocationEvidenceRef


def _evidence(label: str) -> ToolInvocationEvidenceRef:
    return ToolInvocationEvidenceRef(
        evidence_id=f"evidence.{label}",
        evidence_digest=sha256_digest(label),
        sequence=1,
        invocation_id=f"invocation.{label}",
        tool_name="read_drive_file",
    )


def _feature(
    *,
    evidence_label: str = "one",
    tier: V2BehaviorFeatureTier = V2BehaviorFeatureTier.PRIMARY,
    kind: V2BehaviorFeatureKind = V2BehaviorFeatureKind.PERMISSION_BRANCH,
    value: str = "delegation-missing",
):
    return build_v2_behavior_feature(
        tier=tier,
        kind=kind,
        dimensions=(V2BehaviorDimension(name="branch", value=value),),
        evidence_refs=(_evidence(evidence_label),),
    )


def _atom(name: str) -> V2PathAtom:
    return V2PathAtom(atom_kind=V2PathAtomKind.TOOL, semantic_id=f"tool.{name}")


def test_instance_values_and_acquisition_metadata_do_not_create_behavior() -> None:
    assert normalize_v2_behavior_value(
        "drive.apollo.plan",
        role=V2BehaviorValueRole.INSTANCE_ID,
    ) == normalize_v2_behavior_value(
        "drive.borealis.pack",
        role=V2BehaviorValueRole.INSTANCE_ID,
    )
    assert normalize_v2_behavior_value(
        "first body",
        role=V2BehaviorValueRole.CONTENT,
    ) == normalize_v2_behavior_value(
        "unrelated body",
        role=V2BehaviorValueRole.CONTENT,
    )
    assert normalize_v2_behavior_value(
        "2026-08-14T10:00:00Z",
        role=V2BehaviorValueRole.TIMESTAMP,
    ) == "timestamp"
    assert normalize_v2_behavior_value(
        "cursor-one",
        role=V2BehaviorValueRole.CURSOR,
    ) == normalize_v2_behavior_value(
        "cursor-two",
        role=V2BehaviorValueRole.CURSOR,
    )
    assert (
        normalize_v2_behavior_value(
            "strict-replay",
            role=V2BehaviorValueRole.ACQUISITION,
        )
        is None
    )


def test_structural_shape_and_trusted_categories_preserve_only_semantics() -> None:
    first = {"recipient_id": "contact.one", "content": "alpha"}
    second = {"recipient_id": "contact.two", "content": "beta"}
    assert normalize_v2_behavior_value(
        first,
        role=V2BehaviorValueRole.STRUCTURAL,
    ) == normalize_v2_behavior_value(
        second,
        role=V2BehaviorValueRole.STRUCTURAL,
    )
    assert normalize_v2_behavior_value(
        "platform-denied",
        role=V2BehaviorValueRole.CATEGORICAL,
        allowed_categories=frozenset({"platform-denied", "allowed"}),
    ) == "platform-denied"
    with pytest.raises(ValueError, match="trusted vocabulary"):
        normalize_v2_behavior_value(
            "model-invented-branch",
            role=V2BehaviorValueRole.CATEGORICAL,
            allowed_categories=frozenset({"platform-denied", "allowed"}),
        )


def test_feature_semantics_are_stable_while_evidence_lineage_remains_auditable() -> None:
    first = _feature(evidence_label="first")
    second = _feature(evidence_label="second")

    assert first.feature_key_digest == second.feature_key_digest
    assert first.feature_fact_digest != second.feature_fact_digest


def test_feature_tier_rejects_primary_secondary_confusion() -> None:
    with pytest.raises(ValidationError, match="does not match its tier"):
        _feature(
            tier=V2BehaviorFeatureTier.PRIMARY,
            kind=V2BehaviorFeatureKind.INVOCATION_COUNT,
        )


def test_path_keeps_real_order_but_bounds_repeated_calls() -> None:
    read = _atom("read")
    send = _atom("send")

    assert normalize_v2_behavior_path((read, send)).path_digest != (
        normalize_v2_behavior_path((send, read)).path_digest
    )
    three = normalize_v2_behavior_path((read,) * 3 + (send,))
    four = normalize_v2_behavior_path((read,) * 4 + (send,))
    six = normalize_v2_behavior_path((read,) * 6 + (send,))
    two = normalize_v2_behavior_path((read,) * 2 + (send,))
    assert three.path_digest == four.path_digest == six.path_digest
    assert two.path_digest != three.path_digest


def test_path_folds_repeated_multi_tool_loops() -> None:
    loop = (_atom("search"), _atom("read"))
    three = normalize_v2_behavior_path(loop * 3 + (_atom("submit"),))
    five = normalize_v2_behavior_path(loop * 5 + (_atom("submit"),))

    assert three.path_digest == five.path_digest
    assert three.segments[0].atoms == loop
    assert three.segments[0].repeat_bucket.value == "3+"


def test_secondary_diversity_does_not_manufacture_a_new_primary_profile() -> None:
    path = normalize_v2_behavior_path((_atom("read"), _atom("send")))
    primary = _feature()
    first_secondary = _feature(
        evidence_label="secondary-one",
        tier=V2BehaviorFeatureTier.SECONDARY,
        kind=V2BehaviorFeatureKind.EQUIVALENT_RESOURCE,
        value="class-one",
    )
    second_secondary = _feature(
        evidence_label="secondary-two",
        tier=V2BehaviorFeatureTier.SECONDARY,
        kind=V2BehaviorFeatureKind.EXPRESSION_VARIATION,
        value="class-two",
    )
    first = build_v2_behavior_profile(
        canonical_fact_digest=sha256_digest("facts-one"),
        primary_features=(primary,),
        secondary_diversity=(first_secondary,),
        normalized_path=path,
    )
    second = build_v2_behavior_profile(
        canonical_fact_digest=sha256_digest("facts-two"),
        primary_features=(primary,),
        secondary_diversity=(second_secondary,),
        normalized_path=path,
    )

    assert first.profile_digest == second.profile_digest
    assert first.profile_fact_digest != second.profile_fact_digest


def test_v2_behavior_contract_does_not_import_v1_extraction_modules() -> None:
    source = Path("src/sandbox/coverage/v2_behavior.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert "sandbox.coverage.behavior" not in imported
    assert "sandbox.coverage.feature_normalizer" not in imported
    assert "sandbox.coverage.models" not in imported
    assert "sandbox.coverage.office_risk" not in imported
    assert "sandbox.coverage.store" not in imported
