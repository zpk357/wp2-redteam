"""The six registered local operations (SS-009) and the change check (SS-014).

One operation runs per generation: the provider returns a patch, the host applies it,
and then the host verifies that the child differs from its parent by exactly one
registered operation with the declared preserved units really untouched.

Two generator policies make that check exact.  Both *use* the schema rather than
narrow it:

* **complete patches** - a generated candidate patches every registered slot, so a
  slot never changes owner between a parent and its child.  The schema still allows
  omitting a slot (it materialises the fixture content), and an explicit patch that
  reproduces that content has the same input digest, so the policy costs nothing.
* **stable node ids** - an edit keeps node ids, so every change is attributable to a
  unit.  Renaming a node is not a registered operation; it is also invisible to the
  material and to the input digest.

Units are the comparison vocabulary of the preserved set: ``slot:<id>`` (the ordered
node ids), ``node:<id>`` (role and text), ``edge:<id>`` (source, type, target),
``graph``, ``slots`` and ``content`` (the materialised digest).

Order comparisons always drop the nodes an operation replaces, because removing one
node and inserting two (or the reverse) shifts the ordinals of everything after it.
An applier raises :class:`OperationError` for a request that cannot be legal at all;
a provider patch instead reaches :func:`check_operation_change`, which returns a
stable rejection so the candidate opportunity can settle normally.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from sandbox.structured_v1.fixture import PLACEHOLDER_DIGEST, StructuredFixtureManifest
from sandbox.structured_v1.models import (
    CaseNode,
    EdgeType,
    MutationLineage,
    NodeRole,
    OperationKind,
    ReferenceEdge,
    ReferenceTarget,
    ReferenceTargetKind,
    SlotPatch,
    StructuredCase,
)
from sandbox.structured_v1.rendering import render_material
from sandbox.structured_v1.validation import (
    DEFAULT_CASE_BOUNDS,
    CaseBounds,
    CaseRejection,
    admit_case,
    check_reference_graph,
)

ReferenceAction = Literal["add", "remove", "retarget"]
ToggleAction = Literal["add", "remove"]

GRAPH_UNIT: Final[str] = "graph"
SLOTS_UNIT: Final[str] = "slots"
CONTENT_UNIT: Final[str] = "content"


class OperationError(ValueError):
    """A host-side operation request that cannot be applied at all."""


def node_unit(node_id: str) -> str:
    return f"node:{node_id}"


def edge_unit(edge_id: str) -> str:
    return f"edge:{edge_id}"


def slot_unit(slot_id: str) -> str:
    return f"slot:{slot_id}"


@dataclass(frozen=True)
class SlotChange:
    slot_id: str
    order_changed: bool
    added_nodes: tuple[str, ...]
    removed_nodes: tuple[str, ...]
    text_changes: tuple[tuple[str, str, str], ...]
    role_changes: tuple[tuple[str, str, str], ...]

    def is_empty(self) -> bool:
        return not (
            self.order_changed
            or self.added_nodes
            or self.removed_nodes
            or self.text_changes
            or self.role_changes
        )


@dataclass(frozen=True)
class CaseChange:
    """What actually differs between a parent and its child."""

    slots: tuple[SlotChange, ...]
    added_edges: tuple[ReferenceEdge, ...]
    removed_edges: tuple[ReferenceEdge, ...]
    retargeted_edges: tuple[tuple[ReferenceEdge, ReferenceEdge], ...]
    added_node_ids: tuple[str, ...]
    removed_node_ids: tuple[str, ...]
    text_changed_node_ids: tuple[str, ...]
    role_changed_node_ids: tuple[str, ...]
    moved_node_ids: tuple[str, ...]

    def is_empty(self) -> bool:
        return not (
            any(not slot.is_empty() for slot in self.slots)
            or self.has_edge_change()
            or self.has_node_change()
        )

    def has_node_change(self) -> bool:
        return bool(
            self.added_node_ids
            or self.removed_node_ids
            or self.text_changed_node_ids
            or self.role_changed_node_ids
            or self.moved_node_ids
        )

    def has_edge_change(self) -> bool:
        return bool(self.added_edges or self.removed_edges or self.retargeted_edges)


def node_map(case: StructuredCase) -> dict[str, CaseNode]:
    return {node.node_id: node for node in case.nodes}


def edge_map(case: StructuredCase) -> dict[str, ReferenceEdge]:
    return {edge.edge_id: edge for edge in case.reference_graph}


def slot_order(case: StructuredCase) -> dict[str, tuple[str, ...]]:
    return {patch.slot_id: patch.node_ids for patch in case.slot_patches}


def declared_positions(case: StructuredCase) -> dict[str, tuple[str, int]]:
    positions: dict[str, tuple[str, int]] = {}
    for slot_id, order in slot_order(case).items():
        for index, node_id in enumerate(order):
            positions[node_id] = (slot_id, index)
    return positions


def all_units(case: StructuredCase) -> frozenset[str]:
    units = {GRAPH_UNIT, SLOTS_UNIT, CONTENT_UNIT}
    for patch in case.slot_patches:
        units.add(slot_unit(patch.slot_id))
    for node in case.nodes:
        units.add(node_unit(node.node_id))
    for edge in case.reference_graph:
        units.add(edge_unit(edge.edge_id))
    return frozenset(units)


def diff_cases(parent: StructuredCase, child: StructuredCase) -> CaseChange:
    """Compare two candidates; they must patch the same slots (complete-patch policy)."""

    parent_orders = slot_order(parent)
    child_orders = slot_order(child)
    if set(parent_orders) != set(child_orders):
        raise OperationError("parent and child must patch the same slots")

    parent_nodes = node_map(parent)
    child_nodes = node_map(child)
    shared = sorted(set(parent_nodes) & set(child_nodes))
    added_nodes = tuple(sorted(set(child_nodes) - set(parent_nodes)))
    removed_nodes = tuple(sorted(set(parent_nodes) - set(child_nodes)))
    text_changes = tuple(
        node_id for node_id in shared if parent_nodes[node_id].text != child_nodes[node_id].text
    )
    role_changes = tuple(
        node_id for node_id in shared if parent_nodes[node_id].role is not child_nodes[node_id].role
    )
    parent_positions = declared_positions(parent)
    child_positions = declared_positions(child)
    moved = tuple(
        sorted(
            node_id
            for node_id in shared
            if child_positions.get(node_id) != parent_positions.get(node_id)
        )
    )

    slots: list[SlotChange] = []
    for slot_id in sorted(parent_orders):
        parent_order = parent_orders[slot_id]
        child_order = child_orders[slot_id]
        slots.append(
            SlotChange(
                slot_id=slot_id,
                order_changed=parent_order != child_order,
                added_nodes=tuple(
                    node_id for node_id in added_nodes if node_id in child_order
                ),
                removed_nodes=tuple(
                    node_id for node_id in removed_nodes if node_id in parent_order
                ),
                text_changes=tuple(
                    (node_id, parent_nodes[node_id].text, child_nodes[node_id].text)
                    for node_id in text_changes
                    if node_id in parent_order or node_id in child_order
                ),
                role_changes=tuple(
                    (node_id, parent_nodes[node_id].role.value, child_nodes[node_id].role.value)
                    for node_id in role_changes
                    if node_id in parent_order or node_id in child_order
                ),
            )
        )

    parent_edges = edge_map(parent)
    child_edges = edge_map(child)
    added_edges = tuple(child_edges[key] for key in sorted(set(child_edges) - set(parent_edges)))
    removed_edges = tuple(parent_edges[key] for key in sorted(set(parent_edges) - set(child_edges)))
    retargeted = tuple(
        (parent_edges[key], child_edges[key])
        for key in sorted(set(parent_edges) & set(child_edges))
        if parent_edges[key] != child_edges[key]
    )
    return CaseChange(
        slots=tuple(slots),
        added_edges=added_edges,
        removed_edges=removed_edges,
        retargeted_edges=retargeted,
        added_node_ids=added_nodes,
        removed_node_ids=removed_nodes,
        text_changed_node_ids=text_changes,
        role_changed_node_ids=role_changes,
        moved_node_ids=moved,
    )


def changed_units(
    parent: StructuredCase,
    child: StructuredCase,
    *,
    manifest: StructuredFixtureManifest,
) -> frozenset[str]:
    change = diff_cases(parent, child)
    units: set[str] = set()
    for slot in change.slots:
        if slot.order_changed or slot.added_nodes or slot.removed_nodes:
            units.add(slot_unit(slot.slot_id))
            units.add(SLOTS_UNIT)
    for node_id in (
        *change.added_node_ids,
        *change.removed_node_ids,
        *change.text_changed_node_ids,
        *change.role_changed_node_ids,
    ):
        units.add(node_unit(node_id))
    if change.has_edge_change():
        units.add(GRAPH_UNIT)
    for edge in (*change.added_edges, *change.removed_edges):
        units.add(edge_unit(edge.edge_id))
    for old, _ in change.retargeted_edges:
        units.add(edge_unit(old.edge_id))
    parent_digest = render_material(parent, manifest).material_digest
    child_digest = render_material(child, manifest).material_digest
    if parent_digest != child_digest:
        units.add(CONTENT_UNIT)
    return frozenset(units)


def preserved_units(
    parent: StructuredCase,
    child: StructuredCase,
    *,
    manifest: StructuredFixtureManifest,
) -> tuple[str, ...]:
    """Everything the child really left untouched; the lineage declares this set."""

    return tuple(sorted(all_units(parent) - changed_units(parent, child, manifest=manifest)))


def check_operation_change(
    parent: StructuredCase,
    child: StructuredCase,
    *,
    manifest: StructuredFixtureManifest,
) -> CaseRejection | None:
    """SS-014 step 5: exactly one registered operation, and the declared units hold."""

    try:
        change = diff_cases(parent, child)
    except OperationError as exc:
        return CaseRejection("slot-set-changed", str(exc))
    if change.is_empty():
        return CaseRejection(
            "no-registered-change",
            "child materialises the same units as its parent",
        )
    observed = _classify(parent, child, change)
    if observed is None:
        if _removes_node_with_incoming_edges(parent, change):
            return CaseRejection(
                "node-has-incoming-edges",
                "deleting a node requires that it has no incoming reference (SS-009)",
            )
        return CaseRejection(
            "operation-not-registered",
            "the change is not one of the six registered operations",
        )
    declared = child.mutation_lineage.operation
    if declared is None:
        return CaseRejection("operation-mismatch", "child does not declare an operation")
    if declared is not observed:
        return CaseRejection(
            "operation-mismatch",
            f"declared {declared.value}, observed {observed.value}",
        )
    changed = changed_units(parent, child, manifest=manifest)
    known = all_units(parent)
    for unit in child.mutation_lineage.preserved_units:
        if unit not in known:
            return CaseRejection(
                "preserved-unit-modified",
                f"{unit} is not a unit of the parent",
                unit=unit,
            )
        if unit in changed:
            return CaseRejection("preserved-unit-modified", f"{unit} actually changed", unit=unit)
    for unit in child.mutation_lineage.edited_units:
        if unit not in changed:
            return CaseRejection(
                "operation-mismatch",
                f"declared edit {unit} did not change",
                unit=unit,
            )
    return None


def _classify(
    parent: StructuredCase,
    child: StructuredCase,
    change: CaseChange,
) -> OperationKind | None:
    for kind, predicate in (
        (OperationKind.EDIT_TEXT, _is_edit_text),
        (OperationKind.EDIT_REFERENCE, _is_edit_reference),
    ):
        if predicate(parent, child, change):
            return kind
    if _is_move_node(parent, child, change):
        return OperationKind.MOVE_NODE
    if _is_split_node(parent, child, change):
        return OperationKind.SPLIT_NODE
    if _is_merge_nodes(parent, child, change):
        return OperationKind.MERGE_NODES
    if _is_toggle_node(parent, child, change):
        return OperationKind.TOGGLE_NODE
    return None


def _orders_match_without(
    parent: StructuredCase,
    child: StructuredCase,
    *,
    parent_drop: frozenset[str],
    child_drop: frozenset[str],
) -> bool:
    for slot_id, order in slot_order(parent).items():
        remaining_parent = tuple(node_id for node_id in order if node_id not in parent_drop)
        remaining_child = tuple(
            node_id for node_id in slot_order(child)[slot_id] if node_id not in child_drop
        )
        if remaining_parent != remaining_child:
            return False
    return True


def _orders_unchanged(parent: StructuredCase, child: StructuredCase) -> bool:
    return slot_order(parent) == slot_order(child)


def _is_edit_text(parent: StructuredCase, child: StructuredCase, change: CaseChange) -> bool:
    return (
        not change.has_edge_change()
        and not change.added_node_ids
        and not change.removed_node_ids
        and not change.role_changed_node_ids
        and not change.moved_node_ids
        and len(change.text_changed_node_ids) == 1
        and _orders_unchanged(parent, child)
    )


def _is_move_node(parent: StructuredCase, child: StructuredCase, change: CaseChange) -> bool:
    if change.has_edge_change() or change.added_node_ids or change.removed_node_ids:
        return False
    if change.text_changed_node_ids or change.role_changed_node_ids:
        return False
    return _has_moved_exactly_one_node(parent, child, change)


def _has_moved_exactly_one_node(
    parent: StructuredCase,
    child: StructuredCase,
    change: CaseChange,
) -> bool:
    """True when some node's relocation alone explains the child's order.

    Sufficient as well as necessary: removing that node from both orders leaves the
    same sequence exactly when the child is the parent with that one node moved.
    An adjacent transposition has two such explanations (either node "moved") and is
    still a single move, which is what ``SS-009`` asks for - one node changed
    position, while texts, other nodes' texts and legal references are kept.
    """

    return any(
        _orders_match_without(
            parent,
            child,
            parent_drop=frozenset({node_id}),
            child_drop=frozenset({node_id}),
        )
        for node_id in change.moved_node_ids
    )


def _is_edit_reference(parent: StructuredCase, child: StructuredCase, change: CaseChange) -> bool:
    if change.has_node_change() or not _orders_unchanged(parent, child):
        return False
    touched = {edge.edge_id for edge in change.added_edges}
    touched |= {edge.edge_id for edge in change.removed_edges}
    touched |= {old.edge_id for old, _ in change.retargeted_edges}
    if len(touched) == 1:
        return True
    if (
        len(change.added_edges) == 1
        and len(change.removed_edges) == 1
        and not change.retargeted_edges
    ):
        added = change.added_edges[0]
        removed = change.removed_edges[0]
        return added.source == removed.source and added.edge_type is removed.edge_type
    return False


def _is_split_node(parent: StructuredCase, child: StructuredCase, change: CaseChange) -> bool:
    if len(change.removed_node_ids) != 1 or len(change.added_node_ids) != 2:
        return False
    if change.text_changed_node_ids or change.role_changed_node_ids:
        return False
    removed = change.removed_node_ids[0]
    added = change.added_node_ids
    if not _orders_match_without(
        parent,
        child,
        parent_drop=frozenset({removed}),
        child_drop=frozenset(added),
    ):
        return False
    slot_id, index = declared_positions(parent)[removed]
    order = slot_order(child)[slot_id]
    if tuple(sorted(order[index : index + 2])) != tuple(sorted(added)):
        return False
    return _incident_edges_reassign(parent, child, old_nodes=(removed,), new_nodes=added)


def _is_merge_nodes(parent: StructuredCase, child: StructuredCase, change: CaseChange) -> bool:
    if len(change.removed_node_ids) != 2 or len(change.added_node_ids) != 1:
        return False
    if change.text_changed_node_ids or change.role_changed_node_ids:
        return False
    first, second = change.removed_node_ids
    merged = change.added_node_ids[0]
    positions = declared_positions(parent)
    first_slot, first_index = positions[first]
    second_slot, second_index = positions[second]
    if first_slot != second_slot or abs(first_index - second_index) != 1:
        return False
    if not _orders_match_without(
        parent,
        child,
        parent_drop=frozenset({first, second}),
        child_drop=frozenset({merged}),
    ):
        return False
    if declared_positions(child).get(merged) != (first_slot, min(first_index, second_index)):
        return False
    return _incident_edges_reassign(
        parent, child, old_nodes=(first, second), new_nodes=(merged,)
    )


def _is_toggle_node(parent: StructuredCase, child: StructuredCase, change: CaseChange) -> bool:
    if change.text_changed_node_ids or change.role_changed_node_ids:
        return False
    if bool(change.added_node_ids) == bool(change.removed_node_ids):
        return False
    if len(change.added_node_ids) == 1:
        new_node = change.added_node_ids[0]
        if change.removed_edges or change.retargeted_edges:
            return False
        if not _orders_match_without(
            parent,
            child,
            parent_drop=frozenset(),
            child_drop=frozenset({new_node}),
        ):
            return False
        return all(
            new_node in (edge.source, edge.target.ref) for edge in change.added_edges
        )
    removed = change.removed_node_ids[0]
    if change.added_edges or change.retargeted_edges:
        return False
    if _removes_node_with_incoming_edges(parent, change):
        return False
    if not _orders_match_without(
        parent,
        child,
        parent_drop=frozenset({removed}),
        child_drop=frozenset(),
    ):
        return False
    return all(
        removed in (edge.source, edge.target.ref) for edge in change.removed_edges
    )


def _removes_node_with_incoming_edges(parent: StructuredCase, change: CaseChange) -> bool:
    if len(change.removed_node_ids) != 1:
        return False
    removed = change.removed_node_ids[0]
    return any(
        edge.target.kind is ReferenceTargetKind.NODE and edge.target.ref == removed
        for edge in parent.reference_graph
    )


def _incident_edges_reassign(
    parent: StructuredCase,
    child: StructuredCase,
    *,
    old_nodes: tuple[str, ...],
    new_nodes: tuple[str, ...],
) -> bool:
    """Every reference touching an old node must now touch one of the new ones."""

    parent_edges = edge_map(parent)
    child_edges = edge_map(child)
    for edge_id, edge in parent_edges.items():
        if _touches(edge, old_nodes):
            continue
        if child_edges.get(edge_id) != edge:
            return False
    for edge in parent_edges.values():
        if not _touches(edge, old_nodes):
            continue
        if not any(
            _matches_reassignment(edge, candidate, old_nodes, new_nodes)
            for candidate in child_edges.values()
        ):
            return False
    for edge in child_edges.values():
        if _touches(edge, new_nodes):
            continue
        if parent_edges.get(edge.edge_id) != edge:
            return False
    return True


def _touches(edge: ReferenceEdge, node_ids: tuple[str, ...]) -> bool:
    if edge.source in node_ids:
        return True
    return edge.target.kind is ReferenceTargetKind.NODE and edge.target.ref in node_ids


def _matches_reassignment(
    old_edge: ReferenceEdge,
    new_edge: ReferenceEdge,
    old_nodes: tuple[str, ...],
    new_nodes: tuple[str, ...],
) -> bool:
    if old_edge.edge_type is not new_edge.edge_type:
        return False
    if old_edge.source in old_nodes:
        if new_edge.source not in new_nodes:
            return False
    elif new_edge.source != old_edge.source:
        return False
    if old_edge.target.kind is not new_edge.target.kind:
        return False
    if old_edge.target.kind is ReferenceTargetKind.ALIAS:
        return new_edge.target.ref == old_edge.target.ref
    if old_edge.target.ref in old_nodes:
        return new_edge.target.ref in new_nodes
    return new_edge.target.ref == old_edge.target.ref


def merge_rejection(
    parent: StructuredCase,
    first_node_id: str,
    second_node_id: str,
    merged_node_id: str,
    manifest: StructuredFixtureManifest,
    bounds: CaseBounds = DEFAULT_CASE_BOUNDS,
) -> CaseRejection | None:
    """Simulate the merge and ask the shared graph rule (SS-009's merge column).

    A merge has to keep every *external* reference, so a pair whose references would
    collide is not mergeable in the first place: an edge between the two would become a
    self-loop, and two references that differ only in which of the two they touch would
    become a duplicate edge.  Simulating the merged graph catches every such case
    without enumerating them by hand.
    """

    nodes = (set(node_map(parent)) - {first_node_id, second_node_id}) | {merged_node_id}
    edges = tuple(
        _repoint(edge, (first_node_id, second_node_id), (merged_node_id,))
        for edge in parent.reference_graph
    )
    return check_reference_graph(nodes, edges, manifest, bounds)


def _repoint(
    edge: ReferenceEdge,
    old_nodes: tuple[str, ...],
    new_nodes: tuple[str, ...],
) -> ReferenceEdge:
    source = new_nodes[0] if edge.source in old_nodes else edge.source
    target = edge.target
    if edge.target.kind is ReferenceTargetKind.NODE and edge.target.ref in old_nodes:
        target = ReferenceTarget(kind=ReferenceTargetKind.NODE, ref=new_nodes[0])
    return edge.model_copy(update={"source": source, "target": target})


# --------------------------------------------------------------------------------------
# Host-side appliers: each one constructs a child that is legal by construction.
# --------------------------------------------------------------------------------------


def _build_child(
    parent: StructuredCase,
    *,
    manifest: StructuredFixtureManifest,
    nodes: dict[str, CaseNode],
    edges: dict[str, ReferenceEdge],
    patches: tuple[SlotPatch, ...],
    operation: OperationKind,
    generation_identity: str,
    random_state: str,
) -> StructuredCase:
    """Assemble the child, then derive its lineage units from the real difference."""

    def assemble(lineage: MutationLineage) -> StructuredCase:
        return StructuredCase(
            fixture_id=parent.fixture_id,
            manifest_digest=parent.manifest_digest,
            slot_patches=patches,
            nodes=tuple(nodes[key] for key in sorted(nodes)),
            reference_graph=tuple(edges[key] for key in sorted(edges)),
            mutation_lineage=lineage,
            input_digest=PLACEHOLDER_DIGEST,
        )

    lineage = MutationLineage(
        parent_candidate_id=parent.mutation_lineage.generation_identity,
        operation=operation,
        generation_identity=generation_identity,
        random_state=random_state,
    )
    probe = assemble(lineage)
    probe = probe.model_copy(
        update={"input_digest": render_material(probe, manifest).material_digest}
    )
    return probe.model_copy(
        update={
            "mutation_lineage": lineage.model_copy(
                update={
                    "edited_units": tuple(sorted(changed_units(parent, probe, manifest=manifest))),
                    "preserved_units": preserved_units(parent, probe, manifest=manifest),
                }
            )
        }
    )


def _admit_child(
    parent: StructuredCase,
    child: StructuredCase,
    *,
    manifest: StructuredFixtureManifest,
) -> StructuredCase:
    """An applier only ever returns a child the contract already accepts."""

    admission = admit_case(child, manifest=manifest, parent=parent)
    if not admission.accepted:
        raise OperationError(f"operation produced a rejected child: {admission.rejection}")
    return child


def _require_parent_node(parent: StructuredCase, node_id: str) -> CaseNode:
    node = node_map(parent).get(node_id)
    if node is None:
        raise OperationError(f"unknown node: {node_id}")
    return node


def apply_edit_text(
    parent: StructuredCase,
    *,
    node_id: str,
    text: str,
    manifest: StructuredFixtureManifest,
    generation_identity: str,
    random_state: str,
) -> StructuredCase:
    """One node's text; every other node, and all edges, are kept (SS-009)."""

    _require_parent_node(parent, node_id)
    nodes = dict(node_map(parent))
    nodes[node_id] = nodes[node_id].model_copy(update={"text": text})
    child = _build_child(
        parent,
        manifest=manifest,
        nodes=nodes,
        edges=edge_map(parent),
        patches=parent.slot_patches,
        operation=OperationKind.EDIT_TEXT,
        generation_identity=generation_identity,
        random_state=random_state,
    )
    return _admit_child(parent, child, manifest=manifest)


def apply_move_node(
    parent: StructuredCase,
    *,
    node_id: str,
    slot_id: str,
    index: int,
    manifest: StructuredFixtureManifest,
    generation_identity: str,
    random_state: str,
) -> StructuredCase:
    """Move one node within or across permitted slots; its text and all references stay."""

    _require_parent_node(parent, node_id)
    if manifest.slot_profile(slot_id) is None:
        raise OperationError(f"unknown slot: {slot_id}")
    orders = {key: list(value) for key, value in slot_order(parent).items()}
    for order in orders.values():
        if node_id in order:
            order.remove(node_id)
    if index < 0 or index > len(orders[slot_id]):
        raise OperationError(f"index {index} is outside slot {slot_id}")
    orders[slot_id].insert(index, node_id)
    patches = tuple(SlotPatch(slot_id=key, node_ids=tuple(orders[key])) for key in sorted(orders))
    child = _build_child(
        parent,
        manifest=manifest,
        nodes=node_map(parent),
        edges=edge_map(parent),
        patches=patches,
        operation=OperationKind.MOVE_NODE,
        generation_identity=generation_identity,
        random_state=random_state,
    )
    return _admit_child(parent, child, manifest=manifest)


def apply_edit_reference(
    parent: StructuredCase,
    *,
    action: ReferenceAction,
    edge_id: str,
    manifest: StructuredFixtureManifest,
    source: str | None = None,
    target: ReferenceTarget | None = None,
    edge_type: EdgeType | None = None,
    generation_identity: str,
    random_state: str,
) -> StructuredCase:
    """Add, remove or retarget exactly one edge; node texts and other edges are kept."""

    edges = dict(edge_map(parent))
    if action == "add":
        if edge_id in edges:
            raise OperationError(f"edge already exists: {edge_id}")
        if source is None or target is None or edge_type is None:
            raise OperationError("adding an edge needs source, target and edge type")
        edges[edge_id] = ReferenceEdge(
            edge_id=edge_id, source=source, target=target, edge_type=edge_type
        )
    elif action == "remove":
        if edge_id not in edges:
            raise OperationError(f"unknown edge: {edge_id}")
        del edges[edge_id]
    else:
        existing = edges.get(edge_id)
        if existing is None:
            raise OperationError(f"unknown edge: {edge_id}")
        edges[edge_id] = existing.model_copy(
            update={
                "target": target if target is not None else existing.target,
                "edge_type": edge_type if edge_type is not None else existing.edge_type,
            }
        )
    child = _build_child(
        parent,
        manifest=manifest,
        nodes=node_map(parent),
        edges=edges,
        patches=parent.slot_patches,
        operation=OperationKind.EDIT_REFERENCE,
        generation_identity=generation_identity,
        random_state=random_state,
    )
    return _admit_child(parent, child, manifest=manifest)


def apply_split_node(
    parent: StructuredCase,
    *,
    node_id: str,
    first_node_id: str,
    second_node_id: str,
    first_text: str,
    second_text: str,
    manifest: StructuredFixtureManifest,
    generation_identity: str,
    random_state: str,
) -> StructuredCase:
    """Split one node in two and hand the original references to the first half."""

    original = _require_parent_node(parent, node_id)
    if first_node_id in node_map(parent) or second_node_id in node_map(parent):
        raise OperationError("split needs two fresh node ids")
    nodes = dict(node_map(parent))
    del nodes[node_id]
    nodes[first_node_id] = CaseNode(node_id=first_node_id, role=original.role, text=first_text)
    nodes[second_node_id] = CaseNode(node_id=second_node_id, role=original.role, text=second_text)
    orders = {key: list(value) for key, value in slot_order(parent).items()}
    for order in orders.values():
        if node_id in order:
            position = order.index(node_id)
            order[position] = first_node_id
            order.insert(position + 1, second_node_id)
    patches = tuple(SlotPatch(slot_id=key, node_ids=tuple(orders[key])) for key in sorted(orders))
    edges = {
        key: _repoint(edge, (node_id,), (first_node_id,))
        for key, edge in edge_map(parent).items()
    }
    child = _build_child(
        parent,
        manifest=manifest,
        nodes=nodes,
        edges=edges,
        patches=patches,
        operation=OperationKind.SPLIT_NODE,
        generation_identity=generation_identity,
        random_state=random_state,
    )
    return _admit_child(parent, child, manifest=manifest)


def apply_merge_nodes(
    parent: StructuredCase,
    *,
    first_node_id: str,
    second_node_id: str,
    merged_node_id: str,
    text: str,
    manifest: StructuredFixtureManifest,
    generation_identity: str,
    random_state: str,
    bounds: CaseBounds = DEFAULT_CASE_BOUNDS,
) -> StructuredCase:
    """Merge two adjacent nodes of one slot; every external reference survives."""

    first = _require_parent_node(parent, first_node_id)
    _require_parent_node(parent, second_node_id)
    if merged_node_id in node_map(parent):
        raise OperationError("merge needs a fresh node id")
    positions = declared_positions(parent)
    first_slot, first_index = positions[first_node_id]
    second_slot, second_index = positions[second_node_id]
    if first_slot != second_slot or abs(first_index - second_index) != 1:
        raise OperationError("merge requires two adjacent nodes of the same slot")
    rejection = merge_rejection(
        parent,
        first_node_id,
        second_node_id,
        merged_node_id,
        manifest,
        bounds,
    )
    if rejection is not None:
        raise OperationError(f"merge would produce an illegal graph: {rejection.code}")
    nodes = dict(node_map(parent))
    del nodes[first_node_id]
    del nodes[second_node_id]
    nodes[merged_node_id] = CaseNode(node_id=merged_node_id, role=first.role, text=text)
    orders = {key: list(value) for key, value in slot_order(parent).items()}
    order = orders[first_slot]
    order.remove(first_node_id)
    order.remove(second_node_id)
    order.insert(min(first_index, second_index), merged_node_id)
    patches = tuple(SlotPatch(slot_id=key, node_ids=tuple(orders[key])) for key in sorted(orders))
    edges = {
        key: _repoint(edge, (first_node_id, second_node_id), (merged_node_id,))
        for key, edge in edge_map(parent).items()
    }
    child = _build_child(
        parent,
        manifest=manifest,
        nodes=nodes,
        edges=edges,
        patches=patches,
        operation=OperationKind.MERGE_NODES,
        generation_identity=generation_identity,
        random_state=random_state,
    )
    return _admit_child(parent, child, manifest=manifest)


def apply_toggle_node(
    parent: StructuredCase,
    *,
    action: ToggleAction,
    node_id: str,
    manifest: StructuredFixtureManifest,
    slot_id: str | None = None,
    role: NodeRole | None = None,
    text: str | None = None,
    index: int | None = None,
    generation_identity: str,
    random_state: str,
) -> StructuredCase:
    """Add one node to a permitted slot, or delete a node that has no incoming edge."""

    nodes = dict(node_map(parent))
    edges = dict(edge_map(parent))
    orders = {key: list(value) for key, value in slot_order(parent).items()}
    if action == "add":
        if slot_id is None or role is None or text is None:
            raise OperationError("adding a node needs slot, role and text")
        if node_id in nodes:
            raise OperationError(f"node already exists: {node_id}")
        if manifest.slot_profile(slot_id) is None:
            raise OperationError(f"unknown slot: {slot_id}")
        nodes[node_id] = CaseNode(node_id=node_id, role=role, text=text)
        position = len(orders[slot_id]) if index is None else index
        orders[slot_id].insert(position, node_id)
    else:
        _require_parent_node(parent, node_id)
        if any(
            edge.target.kind is ReferenceTargetKind.NODE and edge.target.ref == node_id
            for edge in edges.values()
        ):
            raise OperationError("deleting a node requires that it has no incoming reference")
        del nodes[node_id]
        for order in orders.values():
            if node_id in order:
                order.remove(node_id)
        edges = {key: edge for key, edge in edges.items() if edge.source != node_id}
    patches = tuple(SlotPatch(slot_id=key, node_ids=tuple(orders[key])) for key in sorted(orders))
    child = _build_child(
        parent,
        manifest=manifest,
        nodes=nodes,
        edges=edges,
        patches=patches,
        operation=OperationKind.TOGGLE_NODE,
        generation_identity=generation_identity,
        random_state=random_state,
    )
    return _admit_child(parent, child, manifest=manifest)
