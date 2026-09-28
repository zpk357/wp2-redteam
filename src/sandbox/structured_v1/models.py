"""Host-owned contracts for one structured candidate (search contract SS-005..SS-009).

The schema is deliberately structural.  Numeric capacities, fixture permissions
and the operator catalogue live in the admission layer, so a frozen profile can be
revised by an independent development review without changing the wire schema
(SS-011).

Two properties are load bearing and are enforced here rather than in admission:

* every unordered collection has exactly one canonical order, so two equal
  candidates serialise to the same bytes; and
* candidate identity (node, edge, lineage ids) never enters the materialised
  input, so the input digest is computed from the rendered material alone
  (SS-007, SS-018).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from sandbox.replay.digests import sha256_digest

STRUCTURED_SCHEMA_VERSION = "structured-v1.0"

IDENTIFIER_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,127}$"
SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"
UNIT_KEY_PATTERN = r"^[a-z0-9][a-z0-9._:/-]{0,255}$"

Identifier = Annotated[str, Field(pattern=IDENTIFIER_PATTERN)]
Sha256Digest = Annotated[str, Field(pattern=SHA256_PATTERN)]
UnitKey = Annotated[str, Field(pattern=UNIT_KEY_PATTERN)]


class StructuredContract(BaseModel):
    """Immutable, strict base for persisted structured-scenario contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    def canonical_digest(self) -> str:
        return sha256_digest(self.model_dump(mode="json", exclude_none=False))


class NodeRole(StrEnum):
    """Presentation role of a node (SS-006).  A role is input structure, not authority."""

    CONTEXT = "context"
    REFERENCE = "reference"
    DELIVERY_NOTE = "delivery_note"


class EdgeType(StrEnum):
    """Typed reference edges (SS-006).  Both mean "the material refers to"."""

    POINTS_TO = "points_to"
    CONTINUES_AT = "continues_at"


class ReferenceTargetKind(StrEnum):
    NODE = "node"
    ALIAS = "alias"


class OperationKind(StrEnum):
    """The six registered local operations (SS-009)."""

    EDIT_TEXT = "edit_text"
    MOVE_NODE = "move_node"
    EDIT_REFERENCE = "edit_reference"
    SPLIT_NODE = "split_node"
    MERGE_NODES = "merge_nodes"
    TOGGLE_NODE = "toggle_node"


class ReferenceTarget(StructuredContract):
    """A candidate node or a registered public alias; never a free-form locator.

    ``SS-006`` forbids arbitrary URLs, host paths, real mailboxes, executable code
    and dynamic templates, so the target is a kind plus a registered name and
    nothing else.
    """

    kind: ReferenceTargetKind
    ref: Identifier


class CaseNode(StructuredContract):
    """One material fragment; its text is materialised once, through its slot list."""

    node_id: Identifier
    role: NodeRole
    text: str


class SlotPatch(StructuredContract):
    """Complete replacement of one registered low-trust slot (SS-005).

    Slot contents are an ordered node list, which is the only place a node is
    placed.  Omitting a slot keeps the fixture's original content, so an empty
    candidate patch set is exactly the no-injection root (SS-012).
    """

    slot_id: Identifier
    node_ids: tuple[Identifier, ...] = ()


class ReferenceEdge(StructuredContract):
    edge_id: Identifier
    source: Identifier
    target: ReferenceTarget
    edge_type: EdgeType


class MutationLineage(StructuredContract):
    """Generation identity; excluded from the input digest by construction (SS-007)."""

    parent_candidate_id: Identifier | None = None
    operation: OperationKind | None = None
    generation_identity: Identifier
    random_state: Annotated[str, Field(min_length=1, max_length=512)]
    edited_units: tuple[UnitKey, ...] = ()
    preserved_units: tuple[UnitKey, ...] = ()


class StructuredCase(StructuredContract):
    """One candidate of the bounded structured space (SS-005)."""

    schema_version: Literal["structured-v1.0"] = STRUCTURED_SCHEMA_VERSION
    fixture_id: Identifier
    manifest_digest: Sha256Digest
    slot_patches: tuple[SlotPatch, ...] = ()
    nodes: tuple[CaseNode, ...] = ()
    reference_graph: tuple[ReferenceEdge, ...] = ()
    mutation_lineage: MutationLineage
    input_digest: Sha256Digest

    @field_validator("slot_patches")
    @classmethod
    def patches_are_unique(cls, value: tuple[SlotPatch, ...]) -> tuple[SlotPatch, ...]:
        slot_ids = [patch.slot_id for patch in value]
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError("slot_patches must name each slot at most once")
        return value

    @field_validator("nodes")
    @classmethod
    def nodes_are_unique(cls, value: tuple[CaseNode, ...]) -> tuple[CaseNode, ...]:
        node_ids = [node.node_id for node in value]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("nodes must use unique node ids")
        return value

    @field_validator("reference_graph")
    @classmethod
    def edges_are_unique(cls, value: tuple[ReferenceEdge, ...]) -> tuple[ReferenceEdge, ...]:
        edge_ids = [edge.edge_id for edge in value]
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("reference_graph must use unique edge ids")
        return value
