"""The common root distribution (SS-012) and its independent random stream.

All arms share this generator: the same manifest, seeds, input grammar, operator
catalogue and legal gate.  Structure is drawn first and text second (``SS-012`` step
4), so this module never touches a provider.

Guaranteed by construction, then re-checked by admission:

* the no-injection branch (10%) reproduces the fixture's own slot content, so its
  input digest equals the untouched fixture's;
* the injected branch draws the node count uniformly from ``1..min(8, 4 x slots)``,
  then each node's slot uniformly among the slots that still have room, and its role
  uniformly among that slot's permitted roles (which is the contract's uniform draw
  over the three roles whenever the fixture permits all three);
* the node sampling order is the topological order, so every reference points forward
  and a registered alias is a leaf;
* the edge count is drawn uniformly from ``0..min(12, possible)`` and each edge is
  drawn uniformly among the candidates that keep the graph duplicate-free, acyclic
  and within the depth bound;
* when the slot capacity or the legal candidate set runs out, the sampler keeps what
  it drew and records the actual counts rather than re-drawing the structure.

The fixture's original node ids belong to the fixture, so a no-injection root
re-declares that content under candidate ids; the material, and therefore the input
digest, is unchanged (SS-005, SS-018).
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from sandbox.structured_v1.fixture import PLACEHOLDER_DIGEST, StructuredFixtureManifest
from sandbox.structured_v1.models import (
    CaseNode,
    EdgeType,
    Identifier,
    MutationLineage,
    NodeRole,
    ReferenceEdge,
    ReferenceTarget,
    ReferenceTargetKind,
    SlotPatch,
    StructuredCase,
)
from sandbox.structured_v1.rendering import render_material
from sandbox.structured_v1.streams import build_random
from sandbox.structured_v1.validation import (
    DEFAULT_CASE_BOUNDS,
    CaseBounds,
    admit_case,
    check_reference_graph,
    slot_limits,
)

NO_INJECTION_PERCENT: Final[int] = 10


class RootSamplingError(ValueError):
    """A root that cannot be built from the given structure and text."""


@dataclass(frozen=True)
class NodePlacement:
    """A drawn node: where it goes, what it presents as, and (fixture branch) its text."""

    node_id: str
    slot_id: str
    role: NodeRole
    text: str | None = None


@dataclass(frozen=True)
class EdgeDraft:
    edge_id: str
    source: str
    edge_type: EdgeType
    target: ReferenceTarget


@dataclass(frozen=True)
class RootStructure:
    """A drawn structure; text is still missing for the injected branch."""

    node_order: tuple[str, ...]
    placements: tuple[NodePlacement, ...]
    edges: tuple[EdgeDraft, ...]
    no_injection: bool
    requested_node_count: int
    requested_edge_count: int
    possible_edge_count: int
    exhausted_slot_capacity: bool
    exhausted_edge_candidates: bool


def sample_root_structure(
    manifest: StructuredFixtureManifest,
    *,
    random_state: str,
    bounds: CaseBounds = DEFAULT_CASE_BOUNDS,
) -> RootStructure:
    """Draw one root structure from the common distribution."""

    rng = build_random(random_state)
    if rng.randrange(100) < NO_INJECTION_PERCENT:
        return _no_injection_structure(manifest)
    return _injected_structure(manifest, rng, bounds)


def build_root_case(
    manifest: StructuredFixtureManifest,
    structure: RootStructure,
    *,
    generation_identity: str,
    random_state: str,
    texts: Mapping[str, str] | None = None,
    bounds: CaseBounds = DEFAULT_CASE_BOUNDS,
) -> StructuredCase:
    """Materialise a drawn structure into an admitted candidate.

    A no-injection root carries the fixture's own text, which is the whole point of
    that branch; an injected root needs a text for every drawn node.
    """

    text_by_node = _resolve_texts(structure, texts)
    nodes = tuple(
        CaseNode(
            node_id=placement.node_id,
            role=placement.role,
            text=text_by_node[placement.node_id],
        )
        for placement in sorted(structure.placements, key=lambda item: item.node_id)
    )
    patches = tuple(
        SlotPatch(
            slot_id=profile.slot_id,
            node_ids=tuple(
                placement.node_id
                for placement in structure.placements
                if placement.slot_id == profile.slot_id
            ),
        )
        for profile in manifest.slots
    )
    edges = tuple(
        ReferenceEdge(
            edge_id=draft.edge_id,
            source=draft.source,
            target=draft.target,
            edge_type=draft.edge_type,
        )
        for draft in structure.edges
    )
    case = StructuredCase(
        fixture_id=manifest.fixture_id,
        manifest_digest=manifest.manifest_digest,
        slot_patches=patches,
        nodes=nodes,
        reference_graph=edges,
        mutation_lineage=MutationLineage(
            parent_candidate_id=None,
            operation=None,
            generation_identity=generation_identity,
            random_state=random_state,
        ),
        input_digest=PLACEHOLDER_DIGEST,
    )
    case = case.model_copy(
        update={"input_digest": render_material(case, manifest).material_digest}
    )
    admission = admit_case(case, manifest=manifest, bounds=bounds)
    if not admission.accepted:
        raise RootSamplingError(f"drawn root is not admissible: {admission.rejection}")
    return case


def _resolve_texts(
    structure: RootStructure,
    texts: Mapping[str, str] | None,
) -> dict[str, str]:
    if structure.no_injection:
        resolved = {
            placement.node_id: placement.text
            for placement in structure.placements
            if placement.text is not None
        }
        if len(resolved) != len(structure.placements):
            raise RootSamplingError("a no-injection root must carry the fixture text")
        return resolved
    if texts is None:
        raise RootSamplingError("an injected root needs a text for every drawn node")
    missing = [
        placement.node_id for placement in structure.placements if placement.node_id not in texts
    ]
    if missing:
        raise RootSamplingError(f"missing text for {len(missing)} node(s): {missing[0]}")
    return {placement.node_id: texts[placement.node_id] for placement in structure.placements}


def _no_injection_structure(manifest: StructuredFixtureManifest) -> RootStructure:
    """Re-declare the fixture's own slots under candidate ids, keeping the material."""

    placements: list[NodePlacement] = []
    edges: list[EdgeDraft] = []
    for profile in manifest.slots:
        content = manifest.original_content(profile.slot_id)
        if content is None:
            continue
        renamed: dict[str, str] = {}
        for index, node in enumerate(content.nodes, start=len(placements) + 1):
            node_id = _node_id(index)
            renamed[node.node_id] = node_id
            placements.append(
                NodePlacement(
                    node_id=node_id,
                    slot_id=profile.slot_id,
                    role=node.role,
                    text=node.text,
                )
            )
        for edge in content.edges:
            edges.append(
                EdgeDraft(
                    edge_id=_edge_id(len(edges) + 1),
                    source=renamed.get(edge.source, edge.source),
                    edge_type=edge.edge_type,
                    target=_rename_target(edge.target, renamed),
                )
            )
    structure = RootStructure(
        node_order=tuple(placement.node_id for placement in placements),
        placements=tuple(placements),
        edges=tuple(edges),
        no_injection=True,
        requested_node_count=len(placements),
        requested_edge_count=len(edges),
        possible_edge_count=0,
        exhausted_slot_capacity=False,
        exhausted_edge_candidates=False,
    )
    return structure


def _injected_structure(
    manifest: StructuredFixtureManifest,
    rng: random.Random,
    bounds: CaseBounds,
) -> RootStructure:
    profiles = {profile.slot_id: profile for profile in manifest.slots}
    slot_ids = [profile.slot_id for profile in manifest.slots]
    limits = {
        slot_id: slot_limits(profiles[slot_id], bounds)[0] for slot_id in slot_ids
    }
    max_nodes = min(bounds.max_nodes, sum(limits.values()))
    requested_nodes = rng.randint(1, max_nodes) if max_nodes else 0

    placements: list[NodePlacement] = []
    used = dict.fromkeys(slot_ids, 0)
    exhausted_slots = False
    for _ in range(requested_nodes):
        eligible = [slot_id for slot_id in slot_ids if used[slot_id] < limits[slot_id]]
        if not eligible:
            exhausted_slots = True
            break
        slot_id = eligible[rng.randrange(len(eligible))]
        roles = profiles[slot_id].allowed_roles
        role = roles[rng.randrange(len(roles))]
        used[slot_id] += 1
        placements.append(
            NodePlacement(node_id=_node_id(len(placements) + 1), slot_id=slot_id, role=role)
        )

    order = tuple(placement.node_id for placement in placements)
    candidates = _initial_candidates(order, manifest)
    possible = len(candidates)
    requested_edges = rng.randint(0, min(bounds.max_edges, possible))

    edges: list[EdgeDraft] = []
    exhausted_edges = False
    node_ids = set(order)
    for _ in range(requested_edges):
        legal = [
            candidate
            for candidate in candidates
            if _keeps_the_graph_legal(node_ids, edges, candidate, manifest, bounds)
        ]
        if not legal:
            exhausted_edges = True
            break
        pick = legal[rng.randrange(len(legal))]
        edges.append(
            EdgeDraft(
                edge_id=_edge_id(len(edges) + 1),
                source=pick[0],
                edge_type=pick[1],
                target=pick[2],
            )
        )
        candidates.remove(pick)
    return RootStructure(
        node_order=order,
        placements=tuple(placements),
        edges=tuple(edges),
        no_injection=False,
        requested_node_count=requested_nodes,
        requested_edge_count=requested_edges,
        possible_edge_count=possible,
        exhausted_slot_capacity=exhausted_slots,
        exhausted_edge_candidates=exhausted_edges,
    )


def _node_id(index: int) -> Identifier:
    return f"n{index}"


def _edge_id(index: int) -> Identifier:
    return f"e{index}"


def _rename_target(target: ReferenceTarget, renamed: dict[str, str]) -> ReferenceTarget:
    if target.kind is ReferenceTargetKind.NODE and target.ref in renamed:
        return ReferenceTarget(kind=ReferenceTargetKind.NODE, ref=renamed[target.ref])
    return target


def _initial_candidates(
    order: tuple[str, ...],
    manifest: StructuredFixtureManifest,
) -> list[tuple[str, EdgeType, ReferenceTarget]]:
    """Forward internal pairs with both types, plus leaf edges to registered aliases."""

    candidates: list[tuple[str, EdgeType, ReferenceTarget]] = []
    for index, source in enumerate(order):
        for target in order[index + 1 :]:
            reference = ReferenceTarget(kind=ReferenceTargetKind.NODE, ref=target)
            for edge_type in (EdgeType.POINTS_TO, EdgeType.CONTINUES_AT):
                candidates.append((source, edge_type, reference))
        for alias in manifest.aliases:
            alias_target = ReferenceTarget(kind=ReferenceTargetKind.ALIAS, ref=alias.alias)
            for edge_type in alias.allowed_edge_types:
                candidates.append((source, edge_type, alias_target))
    return candidates


def _keeps_the_graph_legal(
    node_ids: set[str],
    edges: list[EdgeDraft],
    candidate: tuple[str, EdgeType, ReferenceTarget],
    manifest: StructuredFixtureManifest,
    bounds: CaseBounds,
) -> bool:
    """Ask admission's own graph rule, so the sampler cannot propose an illegal edge."""

    source, edge_type, target = candidate
    probe = tuple(
        ReferenceEdge(
            edge_id=draft.edge_id,
            source=draft.source,
            target=draft.target,
            edge_type=draft.edge_type,
        )
        for draft in (*edges, EdgeDraft("probe", source, edge_type, target))
    )
    return check_reference_graph(node_ids, probe, manifest, bounds) is None
