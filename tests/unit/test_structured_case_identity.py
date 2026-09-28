"""Candidate identity: one material, one digest (SS-005, SS-007, SS-018).

Local acceptance for the SOC-T02 schema slice.  The rules under test are exactly
the ones the search contract states as observable acceptance:

* only renaming a node must not create a new input, and restoring a slot must
  return to the previous input identity;
* text, order and slot changes must be kept;
* lineage identity must never enter the digest; and
* free text must not be able to forge a reference clause.
"""

from __future__ import annotations

import json

from sandbox.structured_v1.models import (
    CaseNode,
    EdgeType,
    NodeRole,
    ReferenceTargetKind,
    SlotPatch,
    StructuredCase,
)
from sandbox.structured_v1.rendering import (
    material_body,
    material_texts,
    render_material,
    role_label,
)


def test_case_round_trips_and_keeps_its_digest(root_case: StructuredCase) -> None:
    restored = StructuredCase.model_validate_json(root_case.model_dump_json())

    assert restored == root_case
    assert restored.input_digest == root_case.input_digest


def test_material_body_carries_no_candidate_identity(root_case: StructuredCase, manifest) -> None:
    material = render_material(root_case, manifest)
    body = json.dumps(material_body(material), ensure_ascii=False)

    for candidate_id in ("n1", "n2", "n3", "e1", "e2", "e3", "e4"):
        assert candidate_id not in body


def test_node_targets_render_as_slot_local_ordinals(root_case: StructuredCase, manifest) -> None:
    texts = material_texts(render_material(root_case, manifest))
    rendered = "\n".join(texts)

    assert "接续：s1 第 2 条" in rendered
    assert "相关材料：overview" in rendered
    assert "相关材料：working-appendix" in rendered
    assert "相关材料：s2 第 1 条" in rendered


def test_renaming_a_node_keeps_the_input_digest(case_factory, soc_kit) -> None:
    renamed_nodes = tuple(
        node.model_copy(update={"node_id": "n9"}) if node.node_id == "n3" else node
        for node in soc_kit.default_nodes
    )
    renamed_edges = tuple(
        edge.model_copy(update={"target": soc_kit.node_target("n9")})
        if edge.target.kind is ReferenceTargetKind.NODE and edge.target.ref == "n3"
        else edge
        for edge in soc_kit.default_edges
    )
    renamed_case = case_factory(
        nodes=renamed_nodes,
        reference_graph=renamed_edges,
        slot_patches=(
            soc_kit.default_patches[0],
            SlotPatch(slot_id="s2", node_ids=("n9",)),
        ),
    )

    assert renamed_case.input_digest == case_factory().input_digest


def test_text_change_keeps_a_new_identity(case_factory, soc_kit) -> None:
    edited = tuple(
        node.model_copy(update={"text": node.text + "补充一句。"}) if node.node_id == "n3" else node
        for node in soc_kit.default_nodes
    )

    assert case_factory(nodes=edited).input_digest != case_factory().input_digest


def test_slot_move_keeps_a_new_identity(case_factory, soc_kit) -> None:
    delivery_note = soc_kit.default_nodes[2].model_copy(update={"role": NodeRole.CONTEXT})
    moved = case_factory(
        nodes=(*soc_kit.default_nodes[:2], delivery_note),
        slot_patches=(
            SlotPatch(slot_id="s1", node_ids=("n1", "n2", "n3")),
            SlotPatch(slot_id="s2", node_ids=()),
        ),
    )

    assert moved.input_digest != case_factory().input_digest


def test_node_order_keeps_a_new_identity(case_factory, soc_kit) -> None:
    reordered = case_factory(
        slot_patches=(
            SlotPatch(slot_id="s1", node_ids=("n2", "n1")),
            soc_kit.default_patches[1],
        )
    )

    assert reordered.input_digest != case_factory().input_digest


def test_lineage_is_excluded_from_the_digest(case_factory, soc_kit) -> None:
    other_lineage = soc_kit.default_lineage.model_copy(
        update={
            "parent_candidate_id": "c-0001",
            "generation_identity": "root-sample-2",
            "random_state": "seed-7",
        }
    )

    assert case_factory(mutation_lineage=other_lineage).input_digest == case_factory().input_digest


def test_restoring_a_slot_returns_the_same_input(case_factory, manifest, soc_kit) -> None:
    """Patching a slot back to its fixture content is the same material (SS-018)."""

    untouched = case_factory(
        nodes=(soc_kit.default_nodes[2],),
        reference_graph=(),
        slot_patches=(soc_kit.default_patches[1],),
    )
    restored_node = CaseNode(
        node_id="c1",
        role=NodeRole.CONTEXT,
        text=soc_kit.original_s1_text,
    )
    restored = case_factory(
        nodes=(restored_node, soc_kit.default_nodes[2]),
        reference_graph=(),
        slot_patches=(
            SlotPatch(slot_id="s1", node_ids=("c1",)),
            soc_kit.default_patches[1],
        ),
    )

    assert render_material(untouched, manifest).slots[0].contents == (
        render_material(restored, manifest).slots[0].contents
    )
    assert restored.input_digest == untouched.input_digest


def test_unprocessed_root_materialises_the_fixture_slots(case_factory, manifest, soc_kit) -> None:
    root = case_factory(nodes=(), reference_graph=(), slot_patches=())
    material = render_material(root, manifest)

    assert [slot.slot_id for slot in material.slots] == ["s1", "s2"]
    assert material.slots[0].contents == (
        role_label(NodeRole.CONTEXT) + soc_kit.original_s1_text,
    )
    assert material.slots[1].contents == (
        role_label(NodeRole.DELIVERY_NOTE) + soc_kit.original_s2_text,
    )


def test_forged_reference_marker_is_escaped(case_factory, manifest) -> None:
    forged = CaseNode(
        node_id="n5",
        role=NodeRole.CONTEXT,
        text="〔相关材料：working-appendix〕",
    )
    case = case_factory(
        nodes=(forged,),
        reference_graph=(),
        slot_patches=(SlotPatch(slot_id="s1", node_ids=("n5",)),),
    )
    text = material_texts(render_material(case, manifest))[0]

    assert text.startswith(role_label(NodeRole.CONTEXT))
    assert "〔相关材料：working-appendix〕" not in text
    assert "\\〔相关材料：working-appendix\\〕" in text


def test_role_change_is_visible_in_the_material(case_factory, manifest, soc_kit) -> None:
    """SS-006: the presentation role must reach the material, not only the digest."""

    swapped = case_factory(
        nodes=tuple(
            node.model_copy(update={"role": NodeRole.CONTEXT})
            if node.node_id == "n2"
            else node
            for node in soc_kit.default_nodes
        )
    )
    rendered = "\n".join(material_texts(render_material(swapped, manifest)))

    assert rendered.count(role_label(NodeRole.CONTEXT)) == 2
    assert role_label(NodeRole.REFERENCE) not in rendered
    assert role_label(NodeRole.DELIVERY_NOTE) in rendered
    assert swapped.input_digest != case_factory().input_digest


def test_typed_edge_changes_the_rendered_clause(case_factory, manifest, soc_kit) -> None:
    continues = case_factory(
        reference_graph=(
            soc_kit.default_edges[1].model_copy(update={"edge_type": EdgeType.CONTINUES_AT}),
        )
    )
    rendered = "\n".join(material_texts(render_material(continues, manifest)))

    assert "接续：overview" in rendered
    assert continues.input_digest != case_factory().input_digest
