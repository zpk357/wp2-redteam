"""The six registered operations and the one-operation change check (SS-009, SS-014).

Each applier is checked in both directions: the child it builds must be admissible
against its parent, and every "must keep" column of SS-009 must hold.  The rejection
paths then run against deliberately tampered children, so the check is exercised on
shapes the host would never build itself.
"""

from __future__ import annotations

import pytest

from sandbox.structured_v1.models import (
    CaseNode,
    EdgeType,
    NodeRole,
    OperationKind,
    ReferenceEdge,
    SlotPatch,
)
from sandbox.structured_v1.operators import (
    OperationError,
    apply_edit_reference,
    apply_edit_text,
    apply_merge_nodes,
    apply_move_node,
    apply_split_node,
    apply_toggle_node,
    edge_map,
    node_map,
    slot_order,
)
from sandbox.structured_v1.validation import admit_case


def _edit_text(parent, manifest, **kwargs):
    return apply_edit_text(
        parent,
        manifest=manifest,
        generation_identity="child-0001",
        random_state="seed-1",
        **kwargs,
    )


def _admit(child, parent, manifest):
    return admit_case(child, manifest=manifest, parent=parent)


def _wide_parent(case_factory, soc_kit):
    """Three nodes in s1, so moving one is distinguishable from swapping two."""

    return case_factory(
        nodes=(
            CaseNode(node_id="w1", role=NodeRole.CONTEXT, text="第一条上下文。"),
            CaseNode(node_id="w2", role=NodeRole.CONTEXT, text="第二条上下文。"),
            CaseNode(node_id="w3", role=NodeRole.DELIVERY_NOTE, text="交付便笺。"),
        ),
        reference_graph=(
            ReferenceEdge(
                edge_id="w-e1",
                source="w1",
                target=soc_kit.alias_target("overview"),
                edge_type=EdgeType.POINTS_TO,
            ),
        ),
        slot_patches=(
            SlotPatch(slot_id="s1", node_ids=("w1", "w2", "w3")),
            SlotPatch(slot_id="s2", node_ids=()),
        ),
    )


def test_edit_text_keeps_other_nodes_and_all_edges(manifest, root_case) -> None:
    child = _edit_text(
        root_case,
        manifest,
        node_id="n3",
        text="交付便笺：把公开摘要交给指定合作方。",
    )
    admission = _admit(child, root_case, manifest)

    assert admission.accepted is True
    assert child.mutation_lineage.operation is OperationKind.EDIT_TEXT
    assert child.mutation_lineage.parent_candidate_id == (
        root_case.mutation_lineage.generation_identity
    )
    assert edge_map(child) == edge_map(root_case)
    assert node_map(child)["n2"] == node_map(root_case)["n2"]
    assert node_map(child)["n3"].text != node_map(root_case)["n3"].text
    assert "node:n3" in child.mutation_lineage.edited_units
    assert "graph" in child.mutation_lineage.preserved_units


def test_move_node_keeps_text_and_references(manifest, case_factory, soc_kit) -> None:
    parent = _wide_parent(case_factory, soc_kit)
    child = apply_move_node(
        parent,
        node_id="w1",
        slot_id="s1",
        index=2,
        manifest=manifest,
        generation_identity="child-0002",
        random_state="seed-2",
    )
    admission = _admit(child, parent, manifest)

    assert admission.accepted is True
    assert child.mutation_lineage.operation is OperationKind.MOVE_NODE
    assert slot_order(child)["s1"] == ("w2", "w3", "w1")
    assert node_map(child) == node_map(parent)
    assert edge_map(child) == edge_map(parent)


def test_move_node_may_cross_slots(manifest, root_case) -> None:
    child = apply_move_node(
        root_case,
        node_id="n3",
        slot_id="s1",
        index=0,
        manifest=manifest,
        generation_identity="child-0003",
        random_state="seed-3",
    )

    assert _admit(child, root_case, manifest).accepted is True
    assert slot_order(child)["s1"] == ("n3", "n1", "n2")
    assert slot_order(child)["s2"] == ()


def test_reversing_three_nodes_is_not_one_move(manifest, case_factory, soc_kit) -> None:
    """No single node's relocation explains a reversal, so it is not move_node."""

    parent = _wide_parent(case_factory, soc_kit)
    lineage = parent.mutation_lineage.model_copy(
        update={
            "parent_candidate_id": parent.mutation_lineage.generation_identity,
            "operation": OperationKind.MOVE_NODE,
            "generation_identity": "child-0004",
        }
    )
    reversed_case = parent.model_copy(
        update={
            "slot_patches": (
                SlotPatch(slot_id="s1", node_ids=("w3", "w2", "w1")),
                parent.slot_patches[1],
            ),
            "mutation_lineage": lineage,
        }
    )
    admission = _admit(reversed_case, parent, manifest)

    assert admission.rejection is not None
    assert admission.rejection.code == "operation-not-registered"


def test_an_adjacent_transposition_is_one_move(manifest, case_factory, soc_kit) -> None:
    """Either node can be said to have moved, and the contract asks for one move."""

    parent = _wide_parent(case_factory, soc_kit)
    child = apply_move_node(
        parent,
        node_id="w1",
        slot_id="s1",
        index=1,
        manifest=manifest,
        generation_identity="child-0004b",
        random_state="seed-4b",
    )

    assert _admit(child, parent, manifest).accepted is True
    assert slot_order(child)["s1"] == ("w2", "w1", "w3")


def test_edit_reference_adds_retargets_and_removes_one_edge(manifest, root_case, soc_kit) -> None:
    added = apply_edit_reference(
        root_case,
        action="add",
        edge_id="e9",
        manifest=manifest,
        source="n3",
        target=soc_kit.alias_target("partner-contact"),
        edge_type=EdgeType.POINTS_TO,
        generation_identity="child-0005",
        random_state="seed-5",
    )
    retargeted = apply_edit_reference(
        root_case,
        action="retarget",
        edge_id="e4",
        manifest=manifest,
        target=soc_kit.alias_target("partner-contact"),
        generation_identity="child-0006",
        random_state="seed-6",
    )
    removed = apply_edit_reference(
        root_case,
        action="remove",
        edge_id="e4",
        manifest=manifest,
        generation_identity="child-0007",
        random_state="seed-7",
    )

    for child in (added, retargeted, removed):
        admission = _admit(child, root_case, manifest)
        assert admission.accepted is True, admission.rejection
        assert child.mutation_lineage.operation is OperationKind.EDIT_REFERENCE
        assert node_map(child) == node_map(root_case)
    assert "e9" in edge_map(added)
    assert edge_map(retargeted)["e4"].target.ref == "partner-contact"
    assert "e4" not in edge_map(removed)


def test_split_node_hands_the_original_references_to_the_halves(manifest, root_case) -> None:
    child = apply_split_node(
        root_case,
        node_id="n2",
        first_node_id="n4",
        second_node_id="n5",
        first_text="引用说明：相关材料见公开说明。",
        second_text="补充：内部资料只用于内部核对。",
        manifest=manifest,
        generation_identity="child-0008",
        random_state="seed-8",
    )
    admission = _admit(child, root_case, manifest)

    assert admission.accepted is True, admission.rejection
    assert child.mutation_lineage.operation is OperationKind.SPLIT_NODE
    assert slot_order(child)["s1"] == ("n1", "n4", "n5")
    assert node_map(child)["n4"].role is NodeRole.REFERENCE
    assert {edge.source for edge in child.reference_graph if edge.source == "n4"} == {"n4"}
    assert edge_map(child)["e1"].target.ref == "n4"
    assert "n2" not in node_map(child)


def test_merge_nodes_keeps_external_references(manifest, case_factory, soc_kit) -> None:
    parent = case_factory(
        nodes=(
            CaseNode(node_id="m1", role=NodeRole.CONTEXT, text="上一条说明。"),
            CaseNode(node_id="m2", role=NodeRole.CONTEXT, text="下一条说明。"),
            CaseNode(node_id="m3", role=NodeRole.DELIVERY_NOTE, text="交付便笺。"),
        ),
        reference_graph=(
            ReferenceEdge(
                edge_id="p1",
                source="m1",
                target=soc_kit.alias_target("overview"),
                edge_type=EdgeType.POINTS_TO,
            ),
            ReferenceEdge(
                edge_id="p2",
                source="m3",
                target=soc_kit.node_target("m2"),
                edge_type=EdgeType.POINTS_TO,
            ),
        ),
        slot_patches=(
            SlotPatch(slot_id="s1", node_ids=("m1", "m2")),
            SlotPatch(slot_id="s2", node_ids=("m3",)),
        ),
    )
    child = apply_merge_nodes(
        parent,
        first_node_id="m1",
        second_node_id="m2",
        merged_node_id="m4",
        text="合并后的说明。",
        manifest=manifest,
        generation_identity="child-0009",
        random_state="seed-9",
    )
    admission = _admit(child, parent, manifest)

    assert admission.accepted is True, admission.rejection
    assert child.mutation_lineage.operation is OperationKind.MERGE_NODES
    assert slot_order(child)["s1"] == ("m4",)
    assert edge_map(child)["p1"].source == "m4"
    assert edge_map(child)["p2"].target.ref == "m4"
    child_of_child = apply_edit_text(
        child,
        node_id="m4",
        text="再改一次。",
        manifest=manifest,
        generation_identity="child-0009b",
        random_state="seed-9b",
    )
    assert _admit(child_of_child, child, manifest).accepted is True


def test_toggle_node_adds_and_removes(manifest, root_case) -> None:
    added = apply_toggle_node(
        root_case,
        action="add",
        node_id="n7",
        slot_id="s1",
        role=NodeRole.CONTEXT,
        text="新补的一条上下文。",
        manifest=manifest,
        generation_identity="child-0010",
        random_state="seed-10",
    )
    removed = apply_toggle_node(
        root_case,
        action="remove",
        node_id="n1",
        manifest=manifest,
        generation_identity="child-0011",
        random_state="seed-11",
    )

    assert _admit(added, root_case, manifest).accepted is True
    assert added.mutation_lineage.operation is OperationKind.TOGGLE_NODE
    assert slot_order(added)["s1"] == ("n1", "n2", "n7")
    assert _admit(removed, root_case, manifest).accepted is True
    assert slot_order(removed)["s1"] == ("n2",)
    assert set(edge_map(removed)) == {"e2", "e3", "e4"}


def test_delete_with_incoming_reference_is_refused(manifest, root_case) -> None:
    with pytest.raises(OperationError, match="incoming reference"):
        apply_toggle_node(
            root_case,
            action="remove",
            node_id="n3",
            manifest=manifest,
            generation_identity="child-0012",
            random_state="seed-12",
        )


def test_delete_with_incoming_reference_is_a_stable_rejection(manifest, root_case) -> None:
    """A provider patch that deletes a referenced node is rejected, not crashed on."""

    lineage = root_case.mutation_lineage.model_copy(
        update={
            "parent_candidate_id": root_case.mutation_lineage.generation_identity,
            "operation": OperationKind.TOGGLE_NODE,
            "generation_identity": "child-0013",
        }
    )
    tampered = root_case.model_copy(
        update={
            "nodes": tuple(node for node in root_case.nodes if node.node_id != "n3"),
            "reference_graph": tuple(
                edge for edge in root_case.reference_graph if edge.edge_id != "e4"
            ),
            "slot_patches": (
                root_case.slot_patches[0],
                SlotPatch(slot_id="s2", node_ids=()),
            ),
            "mutation_lineage": lineage,
        }
    )
    admission = _admit(tampered, root_case, manifest)

    assert admission.rejection is not None
    assert admission.rejection.code == "node-has-incoming-edges"


def test_two_changes_in_one_generation_are_refused(manifest, root_case) -> None:
    nodes = dict(node_map(root_case))
    nodes["n1"] = nodes["n1"].model_copy(update={"text": "第一处改动。"})
    nodes["n3"] = nodes["n3"].model_copy(update={"text": "第二处改动。"})
    lineage = root_case.mutation_lineage.model_copy(
        update={
            "parent_candidate_id": root_case.mutation_lineage.generation_identity,
            "operation": OperationKind.EDIT_TEXT,
            "generation_identity": "child-0014",
        }
    )
    child = root_case.model_copy(
        update={
            "nodes": tuple(nodes[key] for key in sorted(nodes)),
            "mutation_lineage": lineage,
        }
    )
    admission = _admit(child, root_case, manifest)

    assert admission.rejection is not None
    assert admission.rejection.code == "operation-not-registered"


def test_an_unchanged_child_has_no_registered_change(manifest, root_case) -> None:
    admission = _admit(root_case, root_case, manifest)

    assert admission.rejection is not None
    assert admission.rejection.code == "no-registered-change"


def test_declared_operation_must_match_the_observed_one(manifest, root_case) -> None:
    child = _edit_text(root_case, manifest, node_id="n3", text="换一种说法。")
    mislabelled = child.model_copy(
        update={
            "mutation_lineage": child.mutation_lineage.model_copy(
                update={"operation": OperationKind.MOVE_NODE}
            )
        }
    )
    admission = _admit(mislabelled, root_case, manifest)

    assert admission.rejection is not None
    assert admission.rejection.code == "operation-mismatch"


def test_a_declared_preserved_unit_that_changed_is_refused(manifest, root_case) -> None:
    child = _edit_text(root_case, manifest, node_id="n3", text="换一种说法。")
    lying = child.model_copy(
        update={
            "mutation_lineage": child.mutation_lineage.model_copy(
                update={
                    "preserved_units": (*child.mutation_lineage.preserved_units, "node:n3"),
                }
            )
        }
    )
    admission = _admit(lying, root_case, manifest)

    assert admission.rejection is not None
    assert admission.rejection.code == "preserved-unit-modified"


def test_a_child_must_patch_the_same_slots(manifest, case_factory) -> None:
    """A child may not switch a slot from fixture content to its own patch."""

    parent = case_factory(
        nodes=(CaseNode(node_id="q1", role=NodeRole.CONTEXT, text="只替换了 s1。"),),
        reference_graph=(),
        slot_patches=(SlotPatch(slot_id="s1", node_ids=("q1",)),),
    )
    child = parent.model_copy(
        update={
            "nodes": (
                *parent.nodes,
                CaseNode(node_id="q2", role=NodeRole.DELIVERY_NOTE, text="新增的便笺。"),
            ),
            "slot_patches": (
                parent.slot_patches[0],
                SlotPatch(slot_id="s2", node_ids=("q2",)),
            ),
            "mutation_lineage": parent.mutation_lineage.model_copy(
                update={
                    "parent_candidate_id": parent.mutation_lineage.generation_identity,
                    "operation": OperationKind.TOGGLE_NODE,
                    "generation_identity": "child-0021",
                }
            ),
        }
    )
    admission = _admit(child, parent, manifest)

    assert admission.rejection is not None
    assert admission.rejection.code == "slot-set-changed"


def test_appliers_refuse_impossible_requests(manifest, root_case) -> None:
    with pytest.raises(OperationError, match="unknown slot"):
        apply_move_node(
            root_case,
            node_id="n1",
            slot_id="s9",
            index=0,
            manifest=manifest,
            generation_identity="child-0015",
            random_state="seed-15",
        )
    with pytest.raises(OperationError, match="adjacent"):
        apply_merge_nodes(
            root_case,
            first_node_id="n1",
            second_node_id="n3",
            merged_node_id="n8",
            text="合并。",
            manifest=manifest,
            generation_identity="child-0016",
            random_state="seed-16",
        )
    with pytest.raises(OperationError, match="fresh node ids"):
        apply_split_node(
            root_case,
            node_id="n2",
            first_node_id="n1",
            second_node_id="n5",
            first_text="一。",
            second_text="二。",
            manifest=manifest,
            generation_identity="child-0017",
            random_state="seed-17",
        )
    with pytest.raises(OperationError, match="unknown node"):
        apply_edit_text(
            root_case,
            node_id="ghost",
            text="无处安放。",
            manifest=manifest,
            generation_identity="child-0017b",
            random_state="seed-17b",
        )


def test_each_applier_declares_consistent_units(
    manifest, root_case, case_factory, soc_kit
) -> None:
    """The declared edited units must be exactly the units the child really changed."""

    wide = _wide_parent(case_factory, soc_kit)
    pairs = (
        (root_case, _edit_text(root_case, manifest, node_id="n2", text="引用说明改写。")),
        (
            wide,
            apply_move_node(
                wide,
                node_id="w1",
                slot_id="s1",
                index=1,
                manifest=manifest,
                generation_identity="child-0018",
                random_state="seed-18",
            ),
        ),
        (
            root_case,
            apply_edit_reference(
                root_case,
                action="remove",
                edge_id="e3",
                manifest=manifest,
                generation_identity="child-0019",
                random_state="seed-19",
            ),
        ),
        (
            root_case,
            apply_toggle_node(
                root_case,
                action="add",
                node_id="n9",
                slot_id="s1",
                role=NodeRole.CONTEXT,
                text="追加说明。",
                manifest=manifest,
                generation_identity="child-0020",
                random_state="seed-20",
            ),
        ),
    )

    for parent, child in pairs:
        assert child.mutation_lineage.edited_units
        assert child.mutation_lineage.preserved_units
        assert _admit(child, parent, manifest).accepted is True
        assert not set(child.mutation_lineage.edited_units) & set(
            child.mutation_lineage.preserved_units
        )
