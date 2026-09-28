"""The common local edit kernel (SS-012 last paragraph, SS-010).

The kernel first chooses uniformly among the registered operations that have at least
one legal position on the parent, then uniformly among that operation's legal
positions.  A position is enumerated from the frozen manifest - a slot's
``allowed_operations`` bounds what may happen inside it - and every enumerated position
is checked against the same graph and capacity rules admission applies, so the kernel
cannot propose an edit the gate would refuse.

Text-bearing positions (``edit_text``, ``split_node``, ``merge_nodes`` and adding a
node) need provider-authored text; the deterministic position and edge operations
(``move_node``, ``edit_reference``, deleting a node) run without a provider call,
which is exactly the cost split of SS-012.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar, Final

from sandbox.structured_v1.fixture import StructuredFixtureManifest
from sandbox.structured_v1.models import (
    EdgeType,
    NodeRole,
    OperationKind,
    ReferenceEdge,
    ReferenceTarget,
    ReferenceTargetKind,
    StructuredCase,
)
from sandbox.structured_v1.operators import (
    ReferenceAction,
    ToggleAction,
    apply_edit_reference,
    apply_edit_text,
    apply_merge_nodes,
    apply_move_node,
    apply_split_node,
    apply_toggle_node,
    declared_positions,
    edge_map,
    merge_rejection,
    node_map,
    slot_order,
)
from sandbox.structured_v1.streams import build_random
from sandbox.structured_v1.validation import (
    DEFAULT_CASE_BOUNDS,
    CaseBounds,
    check_reference_graph,
    slot_limits,
)


class EditKernelError(ValueError):
    """An edit the kernel cannot plan or apply."""


@dataclass(frozen=True)
class _Position:
    OPERATION: ClassVar[OperationKind]

    @property
    def operation(self) -> OperationKind:
        return self.OPERATION

    def affected_slots(self) -> tuple[str, ...]:
        raise NotImplementedError

    def describe(self) -> str:
        raise NotImplementedError


@dataclass(frozen=True)
class EditTextPosition(_Position):
    OPERATION: ClassVar[OperationKind] = OperationKind.EDIT_TEXT

    node_id: str
    slot_id: str

    def affected_slots(self) -> tuple[str, ...]:
        return (self.slot_id,)

    def describe(self) -> str:
        return f"edit_text({self.node_id})"


@dataclass(frozen=True)
class MoveNodePosition(_Position):
    OPERATION: ClassVar[OperationKind] = OperationKind.MOVE_NODE

    node_id: str
    source_slot_id: str
    slot_id: str
    index: int

    def affected_slots(self) -> tuple[str, ...]:
        return (self.source_slot_id, self.slot_id)

    def describe(self) -> str:
        return f"move_node({self.node_id}->{self.slot_id}[{self.index}])"


@dataclass(frozen=True)
class EditReferencePosition(_Position):
    OPERATION: ClassVar[OperationKind] = OperationKind.EDIT_REFERENCE

    action: ReferenceAction
    edge_id: str
    touched_slots: tuple[str, ...]
    source: str | None = None
    target: ReferenceTarget | None = None
    edge_type: EdgeType | None = None

    def affected_slots(self) -> tuple[str, ...]:
        return self.touched_slots

    def describe(self) -> str:
        if self.action == "add":
            suffix = f"{self.source}-{self.edge_type}-{self.target.ref}" if self.target else "?"
        elif self.action == "remove":
            suffix = "removed"
        else:
            suffix = f"retargeted->{self.target.ref}" if self.target else "retargeted"
        return f"edit_reference({self.edge_id}:{suffix})"


@dataclass(frozen=True)
class SplitNodePosition(_Position):
    OPERATION: ClassVar[OperationKind] = OperationKind.SPLIT_NODE

    node_id: str
    first_node_id: str
    second_node_id: str
    slot_id: str

    def affected_slots(self) -> tuple[str, ...]:
        return (self.slot_id,)

    def describe(self) -> str:
        return f"split_node({self.node_id}->{self.first_node_id}|{self.second_node_id})"


@dataclass(frozen=True)
class MergeNodesPosition(_Position):
    OPERATION: ClassVar[OperationKind] = OperationKind.MERGE_NODES

    first_node_id: str
    second_node_id: str
    merged_node_id: str
    slot_id: str

    def affected_slots(self) -> tuple[str, ...]:
        return (self.slot_id,)

    def describe(self) -> str:
        return f"merge_nodes({self.first_node_id}+{self.second_node_id}->{self.merged_node_id})"


@dataclass(frozen=True)
class ToggleNodePosition(_Position):
    OPERATION: ClassVar[OperationKind] = OperationKind.TOGGLE_NODE

    action: ToggleAction
    node_id: str
    slot_id: str
    role: NodeRole | None = None
    index: int | None = None

    def affected_slots(self) -> tuple[str, ...]:
        return (self.slot_id,)

    def describe(self) -> str:
        if self.action == "add":
            return f"toggle_node(add {self.node_id}@{self.slot_id}[{self.index}])"
        return f"toggle_node(remove {self.node_id})"


EditPosition = (
    EditTextPosition
    | MoveNodePosition
    | EditReferencePosition
    | SplitNodePosition
    | MergeNodesPosition
    | ToggleNodePosition
)

_TEXT_NODE_IDS: Final[dict[OperationKind, bool]] = {
    OperationKind.EDIT_TEXT: True,
    OperationKind.MOVE_NODE: False,
    OperationKind.EDIT_REFERENCE: False,
    OperationKind.SPLIT_NODE: True,
    OperationKind.MERGE_NODES: True,
    OperationKind.TOGGLE_NODE: False,
}


def text_node_ids(position: EditPosition) -> tuple[str, ...]:
    """The nodes whose text the provider must write for this position."""

    match position:
        case EditTextPosition():
            return (position.node_id,)
        case SplitNodePosition():
            return (position.first_node_id, position.second_node_id)
        case MergeNodesPosition():
            return (position.merged_node_id,)
        case ToggleNodePosition(action="add"):
            return (position.node_id,)
        case _:
            return ()


def needs_text(position: EditPosition) -> bool:
    return bool(text_node_ids(position))


def position_to_data(position: EditPosition) -> dict[str, Any]:
    """Return the stable, host-side representation of one legal edit position.

    Selection receipts must survive a checkpoint without depending on a Python dataclass
    instance.  The representation contains only registered operation fields; it is not a
    provider-visible payload.
    """

    match position:
        case EditTextPosition():
            return {
                "operation": position.operation.value,
                "node_id": position.node_id,
                "slot_id": position.slot_id,
            }
        case MoveNodePosition():
            return {
                "operation": position.operation.value,
                "node_id": position.node_id,
                "source_slot_id": position.source_slot_id,
                "slot_id": position.slot_id,
                "index": position.index,
            }
        case EditReferencePosition():
            return {
                "operation": position.operation.value,
                "action": position.action,
                "edge_id": position.edge_id,
                "touched_slots": list(position.touched_slots),
                "source": position.source,
                "target": None if position.target is None else position.target.model_dump(
                    mode="json", exclude_none=False
                ),
                "edge_type": None if position.edge_type is None else position.edge_type.value,
            }
        case SplitNodePosition():
            return {
                "operation": position.operation.value,
                "node_id": position.node_id,
                "first_node_id": position.first_node_id,
                "second_node_id": position.second_node_id,
                "slot_id": position.slot_id,
            }
        case MergeNodesPosition():
            return {
                "operation": position.operation.value,
                "first_node_id": position.first_node_id,
                "second_node_id": position.second_node_id,
                "merged_node_id": position.merged_node_id,
                "slot_id": position.slot_id,
            }
        case ToggleNodePosition():
            return {
                "operation": position.operation.value,
                "action": position.action,
                "node_id": position.node_id,
                "slot_id": position.slot_id,
                "role": None if position.role is None else position.role.value,
                "index": position.index,
            }
    raise EditKernelError(f"unsupported edit position: {type(position).__name__}")


def position_from_data(data: Mapping[str, Any]) -> EditPosition:
    """Rebuild a position from a selection receipt after recovery."""

    try:
        operation = OperationKind(str(data["operation"]))
    except (KeyError, TypeError, ValueError) as error:
        raise EditKernelError("selection receipt has an invalid operation") from error
    try:
        if operation is OperationKind.EDIT_TEXT:
            return EditTextPosition(node_id=str(data["node_id"]), slot_id=str(data["slot_id"]))
        if operation is OperationKind.MOVE_NODE:
            return MoveNodePosition(
                node_id=str(data["node_id"]),
                source_slot_id=str(data["source_slot_id"]),
                slot_id=str(data["slot_id"]),
                index=int(data["index"]),
            )
        if operation is OperationKind.EDIT_REFERENCE:
            raw_target = data.get("target")
            return EditReferencePosition(
                action=str(data["action"]),  # type: ignore[arg-type]
                edge_id=str(data["edge_id"]),
                touched_slots=tuple(str(item) for item in data.get("touched_slots", ())),
                source=None if data.get("source") is None else str(data["source"]),
                target=(
                    None
                    if raw_target is None
                    else ReferenceTarget.model_validate(raw_target)
                ),
                edge_type=(
                    None
                    if data.get("edge_type") is None
                    else EdgeType(str(data["edge_type"]))
                ),
            )
        if operation is OperationKind.SPLIT_NODE:
            return SplitNodePosition(
                node_id=str(data["node_id"]),
                first_node_id=str(data["first_node_id"]),
                second_node_id=str(data["second_node_id"]),
                slot_id=str(data["slot_id"]),
            )
        if operation is OperationKind.MERGE_NODES:
            return MergeNodesPosition(
                first_node_id=str(data["first_node_id"]),
                second_node_id=str(data["second_node_id"]),
                merged_node_id=str(data["merged_node_id"]),
                slot_id=str(data["slot_id"]),
            )
        return ToggleNodePosition(
            action=str(data["action"]),  # type: ignore[arg-type]
            node_id=str(data["node_id"]),
            slot_id=str(data["slot_id"]),
            role=None if data.get("role") is None else NodeRole(str(data["role"])),
            index=None if data.get("index") is None else int(data["index"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise EditKernelError("selection receipt has an invalid position") from error


def target_slot_ids(parent: StructuredCase, position: EditPosition) -> tuple[str, ...]:
    """Return the slot(s) whose material the position is intended to change.

    A reference edit is attributed to its source node's slot even when the edge points into a
    different slot.  A move has two possible targets because either its source or destination
    slot may be the evidence-backed opportunity.  This mapping is deliberately separate from
    ``affected_slots()`` which is an admission/permission relation.
    """

    if isinstance(position, (EditTextPosition, SplitNodePosition, MergeNodesPosition,
                             ToggleNodePosition)):
        return (position.slot_id,)
    if isinstance(position, MoveNodePosition):
        return tuple(dict.fromkeys((position.source_slot_id, position.slot_id)))
    if isinstance(position, EditReferencePosition):
        source = position.source
        if source is None:
            edge = edge_map(parent).get(position.edge_id)
            source = None if edge is None else edge.source
        if source is None:
            return ()
        source_position = declared_positions(parent).get(source)
        return () if source_position is None else (source_position[0],)
    return ()


# Explicit aliases make the semantic distinction clear at call sites and in persisted reports.
feedback_target_slots = target_slot_ids


def position_sort_key(position: EditPosition) -> str:
    """Canonical ordering used for every position draw and its digest."""

    import json

    return json.dumps(position_to_data(position), ensure_ascii=False, sort_keys=True)


def legal_positions(
    parent: StructuredCase,
    *,
    manifest: StructuredFixtureManifest,
    bounds: CaseBounds = DEFAULT_CASE_BOUNDS,
) -> dict[OperationKind, tuple[EditPosition, ...]]:
    """Every legal position of every registered operation on this parent."""

    positions: dict[OperationKind, list[EditPosition]] = {kind: [] for kind in OperationKind}
    positions[OperationKind.EDIT_TEXT] = _text_positions(parent, manifest)
    positions[OperationKind.MOVE_NODE] = _move_positions(parent, manifest, bounds)
    positions[OperationKind.EDIT_REFERENCE] = _reference_positions(parent, manifest, bounds)
    positions[OperationKind.SPLIT_NODE] = _split_positions(parent, manifest, bounds)
    positions[OperationKind.MERGE_NODES] = _merge_positions(parent, manifest, bounds)
    positions[OperationKind.TOGGLE_NODE] = _toggle_positions(parent, manifest, bounds)
    return {kind: tuple(items) for kind, items in positions.items()}


def choose_edit(
    parent: StructuredCase,
    *,
    manifest: StructuredFixtureManifest,
    random_state: str,
    bounds: CaseBounds = DEFAULT_CASE_BOUNDS,
) -> EditPosition:
    """Uniformly pick an available operation, then uniformly pick its position."""

    positions = legal_positions(parent, manifest=manifest, bounds=bounds)
    return choose_edit_from_positions(positions, random_state=random_state)


def choose_edit_from_positions(
    positions: Mapping[OperationKind, tuple[EditPosition, ...]],
    *,
    random_state: str,
) -> EditPosition:
    """Apply the common uniform operation/position kernel to an existing legal set."""

    available = sorted(
        (kind for kind in OperationKind if positions.get(kind, ())), key=lambda kind: kind.value
    )
    if not available:
        raise EditKernelError("this parent has no legal edit position")
    rng = build_random(random_state)
    kind = available[rng.randrange(len(available))]
    candidates = tuple(sorted(positions.get(kind, ()), key=position_sort_key))
    return candidates[rng.randrange(len(candidates))]


def apply_edit(
    parent: StructuredCase,
    position: EditPosition,
    *,
    manifest: StructuredFixtureManifest,
    generation_identity: str,
    random_state: str,
    texts: Mapping[str, str] | None = None,
    bounds: CaseBounds = DEFAULT_CASE_BOUNDS,
) -> StructuredCase:
    """Apply a chosen position; text-bearing positions must carry their text."""

    required = text_node_ids(position)
    if required:
        if texts is None:
            raise EditKernelError(f"{position.describe()} needs provider text")
        missing = [node_id for node_id in required if node_id not in texts]
        if missing:
            raise EditKernelError(f"missing text for {missing[0]}")
    assert texts is not None or not required
    match position:
        case EditTextPosition():
            return apply_edit_text(
                parent,
                node_id=position.node_id,
                text=_text(texts, position.node_id),
                manifest=manifest,
                generation_identity=generation_identity,
                random_state=random_state,
            )
        case MoveNodePosition():
            return apply_move_node(
                parent,
                node_id=position.node_id,
                slot_id=position.slot_id,
                index=position.index,
                manifest=manifest,
                generation_identity=generation_identity,
                random_state=random_state,
            )
        case EditReferencePosition():
            return apply_edit_reference(
                parent,
                action=position.action,
                edge_id=position.edge_id,
                manifest=manifest,
                source=position.source,
                target=position.target,
                edge_type=position.edge_type,
                generation_identity=generation_identity,
                random_state=random_state,
            )
        case SplitNodePosition():
            return apply_split_node(
                parent,
                node_id=position.node_id,
                first_node_id=position.first_node_id,
                second_node_id=position.second_node_id,
                first_text=_text(texts, position.first_node_id),
                second_text=_text(texts, position.second_node_id),
                manifest=manifest,
                generation_identity=generation_identity,
                random_state=random_state,
            )
        case MergeNodesPosition():
            return apply_merge_nodes(
                parent,
                first_node_id=position.first_node_id,
                second_node_id=position.second_node_id,
                merged_node_id=position.merged_node_id,
                text=_text(texts, position.merged_node_id),
                manifest=manifest,
                generation_identity=generation_identity,
                random_state=random_state,
                bounds=bounds,
            )
        case _:
            return apply_toggle_node(
                parent,
                action=position.action,
                node_id=position.node_id,
                manifest=manifest,
                slot_id=position.slot_id,
                role=position.role,
                text=_text(texts, position.node_id) if position.action == "add" else None,
                index=position.index,
                generation_identity=generation_identity,
                random_state=random_state,
            )


def _text(texts: Mapping[str, str] | None, node_id: str) -> str:
    if texts is None or node_id not in texts:
        raise EditKernelError(f"missing text for {node_id}")
    return texts[node_id]


def _slots_allow(
    operation: OperationKind,
    slots: tuple[str, ...],
    manifest: StructuredFixtureManifest,
) -> bool:
    for slot_id in dict.fromkeys(slots):
        profile = manifest.slot_profile(slot_id)
        if profile is None or operation not in profile.allowed_operations:
            return False
    return True


def _fresh_node_id(parent: StructuredCase) -> str:
    used = set(node_map(parent))
    index = 1
    while f"n{index}" in used:
        index += 1
    return f"n{index}"


def _fresh_edge_id(parent: StructuredCase) -> str:
    used = set(edge_map(parent))
    index = 1
    while f"e{index}" in used:
        index += 1
    return f"e{index}"


def _text_positions(
    parent: StructuredCase,
    manifest: StructuredFixtureManifest,
) -> list[EditTextPosition]:
    positions_by_node = declared_positions(parent)
    return [
        EditTextPosition(node_id=node_id, slot_id=positions_by_node[node_id][0])
        for node_id in sorted(node_map(parent))
        if _slots_allow(
            OperationKind.EDIT_TEXT, (positions_by_node[node_id][0],), manifest
        )
    ]


def _move_positions(
    parent: StructuredCase,
    manifest: StructuredFixtureManifest,
    bounds: CaseBounds,
) -> list[MoveNodePosition]:
    positions: list[MoveNodePosition] = []
    current = slot_order(parent)
    positions_by_node = declared_positions(parent)
    limits = {
        profile.slot_id: slot_limits(profile, bounds)[0] for profile in manifest.slots
    }
    for node_id in sorted(node_map(parent)):
        source_slot = positions_by_node[node_id][0]
        role = node_map(parent)[node_id].role
        for slot_id in [profile.slot_id for profile in manifest.slots]:
            if not _slots_allow(OperationKind.MOVE_NODE, (source_slot, slot_id), manifest):
                continue
            profile = manifest.slot_profile(slot_id)
            if profile is None or role not in profile.allowed_roles:
                continue
            orders = {key: list(value) for key, value in current.items()}
            orders[source_slot].remove(node_id)
            if len(orders[slot_id]) >= limits[slot_id]:
                continue
            for index in range(len(orders[slot_id]) + 1):
                trial = {key: list(value) for key, value in orders.items()}
                trial[slot_id].insert(index, node_id)
                if {key: tuple(value) for key, value in trial.items()} == current:
                    continue
                positions.append(
                    MoveNodePosition(
                        node_id=node_id,
                        source_slot_id=source_slot,
                        slot_id=slot_id,
                        index=index,
                    )
                )
    return positions


def _reference_positions(
    parent: StructuredCase,
    manifest: StructuredFixtureManifest,
    bounds: CaseBounds,
) -> list[EditReferencePosition]:
    positions: list[EditReferencePosition] = []
    node_ids = set(node_map(parent))
    positions_by_node = declared_positions(parent)
    edges = tuple(edge_map(parent).values())

    def touched(source: str, target: ReferenceTarget) -> tuple[str, ...]:
        slots = [positions_by_node[source][0]]
        if target.kind is ReferenceTargetKind.NODE and target.ref in positions_by_node:
            slots.append(positions_by_node[target.ref][0])
        return tuple(dict.fromkeys(slots))

    for edge in edges:
        slots = touched(edge.source, edge.target)
        if _slots_allow(OperationKind.EDIT_REFERENCE, slots, manifest):
            positions.append(
                EditReferencePosition(
                    action="remove", edge_id=edge.edge_id, touched_slots=slots
                )
            )
        for target, edge_type in _target_options(edge.source, parent, manifest):
            if target == edge.target and edge_type is edge.edge_type:
                continue
            trial = tuple(
                candidate
                if candidate.edge_id != edge.edge_id
                else ReferenceEdge(
                    edge_id=edge.edge_id,
                    source=edge.source,
                    target=target,
                    edge_type=edge_type,
                )
                for candidate in edges
            )
            if check_reference_graph(node_ids, trial, manifest, bounds) is not None:
                continue
            retarget_slots = touched(edge.source, target)
            if not _slots_allow(OperationKind.EDIT_REFERENCE, retarget_slots, manifest):
                continue
            positions.append(
                EditReferencePosition(
                    action="retarget",
                    edge_id=edge.edge_id,
                    touched_slots=retarget_slots,
                    target=target,
                    edge_type=edge_type,
                )
            )
    fresh_id = _fresh_edge_id(parent)
    for source in sorted(node_map(parent)):
        for target, edge_type in _target_options(source, parent, manifest):
            trial = (
                *edges,
                ReferenceEdge(
                    edge_id=fresh_id, source=source, target=target, edge_type=edge_type
                ),
            )
            if check_reference_graph(node_ids, trial, manifest, bounds) is not None:
                continue
            add_slots = touched(source, target)
            if not _slots_allow(OperationKind.EDIT_REFERENCE, add_slots, manifest):
                continue
            positions.append(
                EditReferencePosition(
                    action="add",
                    edge_id=fresh_id,
                    touched_slots=add_slots,
                    source=source,
                    target=target,
                    edge_type=edge_type,
                )
            )
    return positions


def _target_options(
    source: str,
    parent: StructuredCase,
    manifest: StructuredFixtureManifest,
) -> list[tuple[ReferenceTarget, EdgeType]]:
    options: list[tuple[ReferenceTarget, EdgeType]] = []
    for node_id in sorted(node_map(parent)):
        if node_id == source:
            continue
        target = ReferenceTarget(kind=ReferenceTargetKind.NODE, ref=node_id)
        for edge_type in (EdgeType.POINTS_TO, EdgeType.CONTINUES_AT):
            options.append((target, edge_type))
    for alias in manifest.aliases:
        target = ReferenceTarget(kind=ReferenceTargetKind.ALIAS, ref=alias.alias)
        for edge_type in alias.allowed_edge_types:
            options.append((target, edge_type))
    return options


def _split_positions(
    parent: StructuredCase,
    manifest: StructuredFixtureManifest,
    bounds: CaseBounds,
) -> list[SplitNodePosition]:
    positions: list[SplitNodePosition] = []
    orders = slot_order(parent)
    positions_by_node = declared_positions(parent)
    limits = {profile.slot_id: slot_limits(profile, bounds)[0] for profile in manifest.slots}
    fresh = [_fresh_node_id(parent)]
    remaining = sorted(set(_all_node_ids(bounds)) - set(node_map(parent)) - {fresh[0]})
    if not remaining:
        return []
    second = remaining[0]
    if len(node_map(parent)) + 1 > bounds.max_nodes:
        return []
    for node_id in sorted(node_map(parent)):
        slot_id = positions_by_node[node_id][0]
        if len(orders[slot_id]) + 1 > limits[slot_id]:
            continue
        if not _slots_allow(OperationKind.SPLIT_NODE, (slot_id,), manifest):
            continue
        positions.append(
            SplitNodePosition(
                node_id=node_id,
                first_node_id=fresh[0],
                second_node_id=second,
                slot_id=slot_id,
            )
        )
    return positions


def _all_node_ids(bounds: CaseBounds) -> tuple[str, ...]:
    return tuple(f"n{index}" for index in range(1, bounds.max_nodes + 8))


def _merge_positions(
    parent: StructuredCase,
    manifest: StructuredFixtureManifest,
    bounds: CaseBounds,
) -> list[MergeNodesPosition]:
    positions: list[MergeNodesPosition] = []
    orders = slot_order(parent)
    fresh = _fresh_node_id(parent)
    for slot_id in sorted(orders):
        if not _slots_allow(OperationKind.MERGE_NODES, (slot_id,), manifest):
            continue
        order = orders[slot_id]
        for index in range(len(order) - 1):
            if (
                merge_rejection(
                    parent, order[index], order[index + 1], fresh, manifest, bounds
                )
                is not None
            ):
                continue
            positions.append(
                MergeNodesPosition(
                    first_node_id=order[index],
                    second_node_id=order[index + 1],
                    merged_node_id=fresh,
                    slot_id=slot_id,
                )
            )
    return positions


def _toggle_positions(
    parent: StructuredCase,
    manifest: StructuredFixtureManifest,
    bounds: CaseBounds,
) -> list[ToggleNodePosition]:
    positions: list[ToggleNodePosition] = []
    orders = slot_order(parent)
    limits = {profile.slot_id: slot_limits(profile, bounds)[0] for profile in manifest.slots}
    incoming = {
        edge.target.ref
        for edge in parent.reference_graph
        if edge.target.kind is ReferenceTargetKind.NODE
    }
    fresh = _fresh_node_id(parent)
    if len(node_map(parent)) + 1 <= bounds.max_nodes:
        for profile in manifest.slots:
            if not _slots_allow(OperationKind.TOGGLE_NODE, (profile.slot_id,), manifest):
                continue
            if len(orders[profile.slot_id]) >= limits[profile.slot_id]:
                continue
            for role in profile.allowed_roles:
                for index in range(len(orders[profile.slot_id]) + 1):
                    positions.append(
                        ToggleNodePosition(
                            action="add",
                            node_id=fresh,
                            slot_id=profile.slot_id,
                            role=role,
                            index=index,
                        )
                    )
    positions_by_node = declared_positions(parent)
    for node_id in sorted(node_map(parent)):
        if node_id in incoming:
            continue
        slot_id = positions_by_node[node_id][0]
        if not _slots_allow(OperationKind.TOGGLE_NODE, (slot_id,), manifest):
            continue
        positions.append(
            ToggleNodePosition(action="remove", node_id=node_id, slot_id=slot_id)
        )
    return positions
