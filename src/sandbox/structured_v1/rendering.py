"""Deterministic materialisation of a structured candidate (SS-005, SS-006, SS-007).

The renderer is the only place that turns a case into the material a world will
hold.  Four properties are load bearing:

* **one interpretation** - a slot is materialised either entirely from the
  candidate patch or entirely from the fixture's original content, and a node is
  placed exactly once, by its slot list (SS-005);
* **deterministic order** - slots follow the manifest, nodes follow the slot list,
  and a node's reference clauses are sorted by edge type and resolved target label;
* **escaped text** - candidate free text cannot forge a reference marker (SS-006);
* **identity free** - node, edge and lineage ids never appear in the material, so
  renaming a node keeps the input digest (SS-007, SS-018).

Node targets render as a slot-local ordinal rather than an id, which is what makes
the material stable under renaming while still changing when a node moves.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Literal

from sandbox.replay.canonical import canonical_json_bytes
from sandbox.replay.digests import sha256_digest
from sandbox.structured_v1.fixture import StructuredFixtureManifest
from sandbox.structured_v1.models import (
    STRUCTURED_SCHEMA_VERSION,
    CaseNode,
    EdgeType,
    Identifier,
    NodeRole,
    ReferenceEdge,
    ReferenceTargetKind,
    Sha256Digest,
    StructuredCase,
    StructuredContract,
)

REFERENCE_SYNTAX_VERSION: Final[str] = "structured-ref-v1"
MARKER_OPEN: Final[str] = "〔"
MARKER_CLOSE: Final[str] = "〕"
ESCAPE_CHARACTER: Final[str] = "\\"
CLAUSE_SEPARATOR: Final[str] = "\n"
REFERENCE_LABEL_PREFIXES: Final[dict[EdgeType, str]] = {
    EdgeType.POINTS_TO: "相关材料",
    EdgeType.CONTINUES_AT: "接续",
}
NODE_ROLE_LABELS: Final[dict[NodeRole, str]] = {
    NodeRole.CONTEXT: "上下文片段",
    NodeRole.REFERENCE: "引用说明",
    NodeRole.DELIVERY_NOTE: "交付说明",
}
_ORDINAL_TEMPLATE: Final[str] = "{slot_id} 第 {ordinal} 条"


class SlotOwner(StrEnum):
    """Who authored the content materialised into a slot."""

    CANDIDATE = "candidate"
    FIXTURE = "fixture"


@dataclass(frozen=True)
class EffectiveSlot:
    """The content that will actually be materialised into one slot."""

    slot_id: str
    owner: SlotOwner
    nodes: tuple[CaseNode, ...] = ()
    edges: tuple[ReferenceEdge, ...] = ()


@dataclass(frozen=True)
class EffectiveCase:
    """Slot-ordered view of everything the case materialises."""

    fixture_id: str
    manifest_digest: str
    slots: tuple[EffectiveSlot, ...]

    def nodes(self) -> tuple[CaseNode, ...]:
        return tuple(node for slot in self.slots for node in slot.nodes)

    def edges(self) -> tuple[ReferenceEdge, ...]:
        return tuple(edge for slot in self.slots for edge in slot.edges)


class RenderedSlotContent(StructuredContract):
    slot_id: Identifier
    contents: tuple[str, ...] = ()


class RenderedMaterial(StructuredContract):
    """The materialised input of one candidate, with its identity digest."""

    schema_version: Literal["structured-v1.0"] = STRUCTURED_SCHEMA_VERSION
    fixture_id: Identifier
    manifest_digest: Sha256Digest
    syntax_version: Identifier
    slots: tuple[RenderedSlotContent, ...]
    material_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"material_digest"}, exclude_none=False)


def escaped_material_text(text: str) -> str:
    """Escape the marker characters so free text cannot forge a reference clause."""

    escaped = text.replace(ESCAPE_CHARACTER, ESCAPE_CHARACTER * 2)
    escaped = escaped.replace(MARKER_OPEN, ESCAPE_CHARACTER + MARKER_OPEN)
    return escaped.replace(MARKER_CLOSE, ESCAPE_CHARACTER + MARKER_CLOSE)


def role_label(role: NodeRole) -> str:
    """The registered presentation label of a node role (SS-006).

    A role is input structure, so it must reach the material: otherwise changing it
    would only move a digest and stay invisible to the agent.
    """

    return f"{MARKER_OPEN}{NODE_ROLE_LABELS[role]}{MARKER_CLOSE}"


def rendered_node_text(node: CaseNode, clauses: tuple[str, ...] = ()) -> str:
    """Role label, escaped text, then typed reference clauses, in that order."""

    parts = [role_label(node.role) + escaped_material_text(node.text)]
    parts.extend(clauses)
    return CLAUSE_SEPARATOR.join(parts)


@dataclass(frozen=True)
class ResolvedReference:
    """One outgoing edge plus its identity-free target label."""

    edge: ReferenceEdge
    target_label: str

    @property
    def label(self) -> str:
        return reference_label(self.edge, self.target_label)

    @property
    def clause(self) -> str:
        return reference_clause(self.edge, self.target_label)


def reference_label(edge: ReferenceEdge, target_label: str) -> str:
    """The registered label of one reference, without the material markers."""

    prefix = REFERENCE_LABEL_PREFIXES[edge.edge_type]
    return f"{prefix}：{target_label}"


def reference_clause(edge: ReferenceEdge, target_label: str) -> str:
    """Render one typed reference using the registered syntax."""

    return f"{MARKER_OPEN}{reference_label(edge, target_label)}{MARKER_CLOSE}"


def build_effective_case(
    case: StructuredCase | None,
    manifest: StructuredFixtureManifest,
) -> EffectiveCase:
    """Resolve which slot content is materialised; candidate patch wins per slot.

    ``case=None`` yields the fixture's own material, which is the no-injection root
    of SS-012 and the subject of fixture self-checks.
    """

    declared: dict[str, CaseNode] = {}
    patch_order: dict[str, tuple[str, ...]] = {}
    case_edges: tuple[ReferenceEdge, ...] = ()
    if case is not None:
        declared = {node.node_id: node for node in case.nodes}
        patch_order = {patch.slot_id: patch.node_ids for patch in case.slot_patches}
        case_edges = case.reference_graph

    slots: list[EffectiveSlot] = []
    for profile in manifest.slots:
        if profile.slot_id in patch_order:
            node_ids = patch_order[profile.slot_id]
            owned = set(node_ids)
            slots.append(
                EffectiveSlot(
                    slot_id=profile.slot_id,
                    owner=SlotOwner.CANDIDATE,
                    nodes=tuple(declared[node_id] for node_id in node_ids),
                    edges=tuple(edge for edge in case_edges if edge.source in owned),
                )
            )
            continue
        original = manifest.original_content(profile.slot_id)
        slots.append(
            EffectiveSlot(
                slot_id=profile.slot_id,
                owner=SlotOwner.FIXTURE,
                nodes=tuple(original.nodes) if original is not None else (),
                edges=tuple(original.edges) if original is not None else (),
            )
        )
    return EffectiveCase(
        fixture_id=manifest.fixture_id,
        manifest_digest=manifest.manifest_digest,
        slots=tuple(slots),
    )


def node_positions(effective: EffectiveCase) -> dict[str, tuple[str, int]]:
    """Map each node id to its slot and 1-based ordinal within that slot."""

    positions: dict[str, tuple[str, int]] = {}
    for slot in effective.slots:
        for index, node in enumerate(slot.nodes, start=1):
            positions[node.node_id] = (slot.slot_id, index)
    return positions


def resolve_references(effective: EffectiveCase) -> dict[str, tuple[ResolvedReference, ...]]:
    """Resolve every node's outgoing references, sorted by edge type then target label."""

    positions = node_positions(effective)
    resolved: dict[str, list[ResolvedReference]] = {
        node.node_id: [] for node in effective.nodes()
    }
    for edge in effective.edges():
        if edge.target.kind is ReferenceTargetKind.ALIAS:
            target_label = edge.target.ref
        else:
            slot_id, ordinal = positions[edge.target.ref]
            target_label = _ORDINAL_TEMPLATE.format(slot_id=slot_id, ordinal=ordinal)
        resolved[edge.source].append(ResolvedReference(edge=edge, target_label=target_label))
    return {
        node_id: tuple(sorted(items, key=lambda item: (item.edge.edge_type.value, item.label)))
        for node_id, items in resolved.items()
    }


def render_effective_case(
    effective: EffectiveCase,
    manifest: StructuredFixtureManifest,
) -> RenderedMaterial:
    references = resolve_references(effective)
    slots: list[RenderedSlotContent] = []
    for slot in effective.slots:
        contents: list[str] = []
        for node in slot.nodes:
            clauses = tuple(
                reference.clause for reference in references.get(node.node_id, ())
            )
            contents.append(rendered_node_text(node, clauses))
        slots.append(RenderedSlotContent(slot_id=slot.slot_id, contents=tuple(contents)))
    material = RenderedMaterial(
        fixture_id=effective.fixture_id,
        manifest_digest=effective.manifest_digest,
        syntax_version=REFERENCE_SYNTAX_VERSION,
        slots=tuple(slots),
        material_digest="sha256:" + "0" * 64,
    )
    digest = sha256_digest(material.digest_payload())
    return material.model_copy(update={"material_digest": digest})


def render_material(
    case: StructuredCase,
    manifest: StructuredFixtureManifest,
) -> RenderedMaterial:
    """Render a candidate; the case must already be structurally resolvable."""

    return render_effective_case(build_effective_case(case, manifest), manifest)


def material_body(material: RenderedMaterial) -> dict[str, object]:
    """The materialised input body whose byte length SS-011 bounds."""

    return {
        "slots": [
            {"slot_id": slot.slot_id, "contents": list(slot.contents)} for slot in material.slots
        ]
    }


def material_body_bytes(material: RenderedMaterial) -> bytes:
    return canonical_json_bytes(material_body(material))


def material_texts(material: RenderedMaterial) -> tuple[str, ...]:
    return tuple(content for slot in material.slots for content in slot.contents)
