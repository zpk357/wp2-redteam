"""The local edit kernel: uniform operation choice, legal positions (SS-012, SS-010).

The kernel's job is to propose an edit that the gate will accept, from the positions
the frozen manifest permits.  The tests therefore check both halves: every chosen
position applies and is admitted against its parent, and positions the manifest does
not permit are never enumerated.
"""

from __future__ import annotations

import pytest

from sandbox.structured_v1.edit_kernel import (
    EditKernelError,
    apply_edit,
    choose_edit,
    legal_positions,
    needs_text,
    text_node_ids,
)
from sandbox.structured_v1.fixture import SlotProfile
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
    apply_merge_nodes,
    merge_rejection,
)
from sandbox.structured_v1.streams import edit_random_state
from sandbox.structured_v1.validation import admit_case


def _rich_parent(case_factory, soc_kit):
    return case_factory(
        nodes=(
            CaseNode(node_id="r1", role=NodeRole.CONTEXT, text="第一条上下文。"),
            CaseNode(node_id="r2", role=NodeRole.REFERENCE, text="引用说明。"),
            CaseNode(node_id="r3", role=NodeRole.DELIVERY_NOTE, text="交付便笺。"),
            CaseNode(node_id="r4", role=NodeRole.DELIVERY_NOTE, text="第二条交付便笺。"),
        ),
        reference_graph=(
            ReferenceEdge(
                edge_id="r-e1",
                source="r1",
                target=soc_kit.alias_target("overview"),
                edge_type=EdgeType.POINTS_TO,
            ),
            ReferenceEdge(
                edge_id="r-e2",
                source="r2",
                target=soc_kit.node_target("r3"),
                edge_type=EdgeType.POINTS_TO,
            ),
        ),
        slot_patches=(
            SlotPatch(slot_id="s1", node_ids=("r1", "r2")),
            SlotPatch(slot_id="s2", node_ids=("r3", "r4")),
        ),
    )


def _texts(position) -> dict[str, str]:
    return {node_id: f"合成改写文字 {node_id}。" for node_id in text_node_ids(position)}


def test_a_rich_parent_has_all_six_operations(manifest, case_factory, soc_kit) -> None:
    positions = legal_positions(_rich_parent(case_factory, soc_kit), manifest=manifest)

    assert set(positions) == set(OperationKind)
    assert all(positions[kind] for kind in OperationKind)
    assert positions[OperationKind.EDIT_TEXT]


def test_every_chosen_position_applies_and_is_admitted(manifest, case_factory, soc_kit) -> None:
    parent = _rich_parent(case_factory, soc_kit)
    seen: set[OperationKind] = set()
    for index in range(60):
        random_state = edit_random_state("kernel", index)
        position = choose_edit(parent, manifest=manifest, random_state=random_state)
        seen.add(position.operation)
        child = apply_edit(
            parent,
            position,
            manifest=manifest,
            generation_identity=f"child-{index:04d}",
            random_state=random_state,
            texts=_texts(position),
        )
        admission = admit_case(child, manifest=manifest, parent=parent)

        assert admission.accepted is True, (position.describe(), admission.rejection)
        assert child.mutation_lineage.operation is position.operation

    assert seen == set(OperationKind)


def test_the_same_stream_chooses_the_same_position(manifest, case_factory, soc_kit) -> None:
    parent = _rich_parent(case_factory, soc_kit)
    stream = edit_random_state("kernel", 3)

    first = choose_edit(parent, manifest=manifest, random_state=stream)
    second = choose_edit(parent, manifest=manifest, random_state=stream)

    assert first == second


def test_deterministic_positions_need_no_provider_text(manifest, case_factory, soc_kit) -> None:
    parent = _rich_parent(case_factory, soc_kit)
    positions = legal_positions(parent, manifest=manifest)

    for kind in (
        OperationKind.MOVE_NODE,
        OperationKind.EDIT_REFERENCE,
    ):
        for position in positions[kind]:
            assert needs_text(position) is False
            child = apply_edit(
                parent,
                position,
                manifest=manifest,
                generation_identity="child-3000",
                random_state="state",
            )
            assert admit_case(child, manifest=manifest, parent=parent).accepted is True
    removals = [
        position
        for position in positions[OperationKind.TOGGLE_NODE]
        if position.action == "remove"
    ]
    assert removals
    for position in removals:
        assert needs_text(position) is False


def test_text_positions_require_text(manifest, case_factory, soc_kit) -> None:
    parent = _rich_parent(case_factory, soc_kit)
    positions = legal_positions(parent, manifest=manifest)
    text_position = positions[OperationKind.EDIT_TEXT][0]

    assert text_node_ids(text_position) == (text_position.node_id,)
    with pytest.raises(EditKernelError, match="needs provider text"):
        apply_edit(
            parent,
            text_position,
            manifest=manifest,
            generation_identity="child-3001",
            random_state="state",
        )
    with pytest.raises(EditKernelError, match="missing text"):
        apply_edit(
            parent,
            text_position,
            manifest=manifest,
            generation_identity="child-3001",
            random_state="state",
            texts={"other-node": "写了别的节点。"},
        )


def test_a_slot_that_forbids_an_operation_has_no_such_position(
    manifest, case_factory, soc_kit
) -> None:
    restricted = soc_kit.build_manifest(
        slots=(
            SlotProfile(
                slot_id="s1",
                max_nodes=4,
                max_code_points=1200,
                allowed_operations=(OperationKind.MOVE_NODE,),
            ),
            SlotProfile(
                slot_id="s2",
                max_nodes=3,
                max_code_points=800,
                allowed_roles=(NodeRole.DELIVERY_NOTE,),
            ),
        )
    )
    positions = legal_positions(_rich_parent(case_factory, soc_kit), manifest=restricted)

    for kind, kind_positions in positions.items():
        for position in kind_positions:
            for slot_id in dict.fromkeys(position.affected_slots()):
                profile = restricted.slot_profile(slot_id)
                assert profile is not None
                assert kind in profile.allowed_operations
    assert positions[OperationKind.MOVE_NODE]
    assert positions[OperationKind.EDIT_TEXT]
    assert all(position.slot_id == "s2" for position in positions[OperationKind.EDIT_TEXT])
    assert all(position.slot_id == "s2" for position in positions[OperationKind.SPLIT_NODE])
    assert all(position.slot_id == "s2" for position in positions[OperationKind.MERGE_NODES])
    assert all(position.slot_id == "s2" for position in positions[OperationKind.TOGGLE_NODE])


def test_merging_linked_nodes_is_not_a_legal_position(manifest, case_factory, soc_kit) -> None:
    """An edge between a mergeable pair would become a self-loop, so the pair is excluded."""

    parent = case_factory(
        nodes=(
            CaseNode(node_id="p1", role=NodeRole.CONTEXT, text="上一条。"),
            CaseNode(node_id="p2", role=NodeRole.CONTEXT, text="下一条。"),
            CaseNode(node_id="p3", role=NodeRole.DELIVERY_NOTE, text="交付便笺。"),
        ),
        reference_graph=(
            ReferenceEdge(
                edge_id="p-e1",
                source="p1",
                target=soc_kit.node_target("p2"),
                edge_type=EdgeType.POINTS_TO,
            ),
        ),
        slot_patches=(
            SlotPatch(slot_id="s1", node_ids=("p1", "p2")),
            SlotPatch(slot_id="s2", node_ids=("p3",)),
        ),
    )
    positions = legal_positions(parent, manifest=manifest)

    assert positions[OperationKind.MERGE_NODES] == ()
    assert merge_rejection(parent, "p1", "p2", "p4", manifest) is not None
    with pytest.raises(OperationError, match="illegal graph"):
        apply_merge_nodes(
            parent,
            first_node_id="p1",
            second_node_id="p2",
            merged_node_id="p4",
            text="合并后的说明。",
            manifest=manifest,
            generation_identity="child-4000",
            random_state="state",
        )


def test_merging_nodes_that_share_a_reference_is_refused(
    manifest, case_factory, soc_kit
) -> None:
    """Two references that differ only in which of the pair they touch would collide."""

    parent = case_factory(
        nodes=(
            CaseNode(node_id="q1", role=NodeRole.CONTEXT, text="第一条。"),
            CaseNode(node_id="q2", role=NodeRole.CONTEXT, text="第二条。"),
            CaseNode(node_id="q3", role=NodeRole.DELIVERY_NOTE, text="交付便笺。"),
        ),
        reference_graph=(
            ReferenceEdge(
                edge_id="q-e1",
                source="q3",
                target=soc_kit.node_target("q1"),
                edge_type=EdgeType.POINTS_TO,
            ),
            ReferenceEdge(
                edge_id="q-e2",
                source="q3",
                target=soc_kit.node_target("q2"),
                edge_type=EdgeType.POINTS_TO,
            ),
        ),
        slot_patches=(
            SlotPatch(slot_id="s1", node_ids=("q1", "q2")),
            SlotPatch(slot_id="s2", node_ids=("q3",)),
        ),
    )
    positions = legal_positions(parent, manifest=manifest)
    rejection = merge_rejection(parent, "q1", "q2", "q4", manifest)

    assert positions[OperationKind.MERGE_NODES] == ()
    assert rejection is not None
    assert rejection.code == "duplicate-edge"


def test_a_parent_without_any_legal_position_is_refused(case_factory, soc_kit) -> None:
    """A fixture that permits no operation has an empty candidate set, not a crash."""

    locked = soc_kit.build_manifest(
        slots=(
            SlotProfile(slot_id="s1", allowed_operations=()),
            SlotProfile(
                slot_id="s2",
                allowed_operations=(),
                allowed_roles=(NodeRole.DELIVERY_NOTE,),
            ),
        )
    )

    with pytest.raises(EditKernelError, match="no legal edit position"):
        choose_edit(case_factory(), manifest=locked, random_state="state")
