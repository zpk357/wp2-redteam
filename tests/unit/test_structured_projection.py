"""Public projection boundary (SS-004, SOC-SAF-12).

The projection is what a provider request may carry.  These tests check both
directions: the parent's own low-trust material and the registered aliases are
visible, while registered private values and hidden labels are refused no matter
which field would carry them.
"""

from __future__ import annotations

import pytest

from sandbox.structured_v1.models import CaseNode, NodeRole, SlotPatch
from sandbox.structured_v1.projection import (
    ProjectionLeakError,
    build_public_projection,
    projection_leaks,
    verify_public_projection,
)


def _leaky_case(factory, kit, text: str):
    return factory(
        nodes=(CaseNode(node_id="k1", role=NodeRole.CONTEXT, text=text),),
        reference_graph=(),
        slot_patches=(SlotPatch(slot_id="s1", node_ids=("k1",)),),
    )


def test_projection_is_clean_for_the_sample(manifest, root_case) -> None:
    projection = build_public_projection(manifest=manifest, parent=root_case)

    assert projection_leaks(projection, manifest=manifest) == ()
    verify_public_projection(projection, manifest=manifest)


def test_projection_exposes_parent_material_and_registrations(manifest, root_case, soc_kit) -> None:
    projection = build_public_projection(manifest=manifest, parent=root_case)

    node_texts = {node.text for node in projection.parent_nodes}
    assert soc_kit.default_nodes[0].text in node_texts
    assert [slot.slot_id for slot in projection.slots] == ["s1", "s2"]
    assert projection.slots[0].max_nodes == 4
    assert tuple(alias for alias in projection.aliases) == (
        "overview",
        "working-appendix",
        "partner-contact",
    )
    labels = [label for node in projection.parent_nodes for label in node.references]
    assert "相关材料：overview" in labels
    assert "接续：s1 第 2 条" in labels


def test_root_projection_uses_the_fixture_content(manifest, case_factory, soc_kit) -> None:
    root = case_factory(nodes=(), reference_graph=(), slot_patches=())
    projection = build_public_projection(manifest=manifest, parent=root)

    assert tuple(node.text for node in projection.parent_nodes) == (
        soc_kit.original_s1_text,
        soc_kit.original_s2_text,
    )


def test_projection_digest_is_stable_and_parent_sensitive(
    manifest, root_case, case_factory, soc_kit
) -> None:
    first = build_public_projection(manifest=manifest, parent=root_case)
    second = build_public_projection(manifest=manifest, parent=root_case)
    edited = case_factory(
        nodes=tuple(
            node.model_copy(update={"text": node.text + "又一句。"})
            if node.node_id == "n1"
            else node
            for node in soc_kit.default_nodes
        )
    )
    third = build_public_projection(manifest=manifest, parent=edited)

    assert first.projection_digest == second.projection_digest
    assert first.projection_digest != third.projection_digest


def test_projection_refuses_a_restricted_value(manifest, case_factory, soc_kit) -> None:
    parent = _leaky_case(
        case_factory, soc_kit, f"补充说明里出现了 {soc_kit.restricted_value} 一词。"
    )
    projection = build_public_projection(manifest=manifest, parent=parent)

    assert len(projection_leaks(projection, manifest=manifest)) == 1
    with pytest.raises(ProjectionLeakError):
        verify_public_projection(projection, manifest=manifest)


def test_projection_refuses_a_hidden_label(manifest, case_factory, soc_kit) -> None:
    parent = _leaky_case(
        case_factory, soc_kit, f"内部备注写着 {soc_kit.hidden_label} 字样。"
    )
    projection = build_public_projection(manifest=manifest, parent=parent)

    assert len(projection_leaks(projection, manifest=manifest)) == 1
    with pytest.raises(ProjectionLeakError):
        verify_public_projection(projection, manifest=manifest)


def test_leak_check_covers_fields_beyond_the_material(soc_kit) -> None:
    """The walk is not field-specific: a restricted value in any string is caught."""

    broken_manifest = soc_kit.build_manifest(
        public_task_text=f"任务说明里带上了 {soc_kit.restricted_value}",
    )
    projection = build_public_projection(manifest=broken_manifest, parent=None)

    assert projection_leaks(projection, manifest=broken_manifest) != ()


def test_projection_does_not_expose_unregistered_aliases(manifest, root_case) -> None:
    projection = build_public_projection(manifest=manifest, parent=root_case)

    assert "private-match-table" not in projection.aliases
    assert all(
        alias in {"overview", "working-appendix", "partner-contact"}
        for alias in projection.aliases
    )
