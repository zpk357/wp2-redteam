"""Admission of a structured candidate (SS-011 bounds, SS-014 order).

Each row below is one contract rule with its stable rejection code.  The tail of
the file checks the opposite direction: the same rules must not over-reject a
candidate that references a fixture-authored node in an unpatched slot, replace
every slot with its own content, or keep the fixture slots untouched.
"""

from __future__ import annotations

import pytest

from sandbox.structured_v1.fixture import PLACEHOLDER_DIGEST
from sandbox.structured_v1.models import (
    CaseNode,
    EdgeType,
    NodeRole,
    ReferenceEdge,
    ReferenceTarget,
    SlotPatch,
)
from sandbox.structured_v1.rendering import material_texts, role_label
from sandbox.structured_v1.validation import (
    DEFAULT_CASE_BOUNDS,
    REJECTION_CODES,
    CaseBounds,
    admit_case,
    verify_fixture_admissible,
)


def _context(node_id: str, text: str = "合成低信任上下文说明。") -> CaseNode:
    return CaseNode(node_id=node_id, role=NodeRole.CONTEXT, text=text)


def _delivery(node_id: str, text: str = "合成低信任交付便笺。") -> CaseNode:
    return CaseNode(node_id=node_id, role=NodeRole.DELIVERY_NOTE, text=text)


def _edge(
    edge_id: str,
    source: str,
    target: ReferenceTarget,
    edge_type: EdgeType = EdgeType.POINTS_TO,
) -> ReferenceEdge:
    return ReferenceEdge(
        edge_id=edge_id,
        source=source,
        target=target,
        edge_type=edge_type,
    )


def _leaky_case(factory, kit):
    return factory(
        nodes=(_context("k1", f"补充说明里出现了 {kit.restricted_value} 一词。"),),
        reference_graph=(),
        slot_patches=(SlotPatch(slot_id="s1", node_ids=("k1",)),),
    )


def _chain_case(factory, kit):
    """Five nodes joined by a four-edge chain, one edge past the depth bound."""

    nodes = (
        _context("a1"),
        _context("a2"),
        _context("a3"),
        _delivery("a4"),
        _delivery("a5"),
    )
    edges = (
        _edge("c1", "a1", kit.node_target("a2"), EdgeType.CONTINUES_AT),
        _edge("c2", "a2", kit.node_target("a3"), EdgeType.CONTINUES_AT),
        _edge("c3", "a3", kit.node_target("a4"), EdgeType.CONTINUES_AT),
        _edge("c4", "a4", kit.node_target("a5"), EdgeType.CONTINUES_AT),
    )
    return factory(
        nodes=nodes,
        reference_graph=edges,
        slot_patches=(
            SlotPatch(slot_id="s1", node_ids=("a1", "a2", "a3")),
            SlotPatch(slot_id="s2", node_ids=("a4", "a5")),
        ),
    )


def _alias_tail_case(factory, kit):
    """Three node edges plus one alias edge: four edges, one past the depth bound."""

    nodes = (_context("a1"), _context("a2"), _context("a3"), _delivery("a4"))
    edges = (
        _edge("c1", "a1", kit.node_target("a2"), EdgeType.CONTINUES_AT),
        _edge("c2", "a2", kit.node_target("a3"), EdgeType.CONTINUES_AT),
        _edge("c3", "a3", kit.node_target("a4"), EdgeType.CONTINUES_AT),
        _edge("c4", "a4", kit.alias_target("overview")),
    )
    return factory(
        nodes=nodes,
        reference_graph=edges,
        slot_patches=(
            SlotPatch(slot_id="s1", node_ids=("a1", "a2", "a3")),
            SlotPatch(slot_id="s2", node_ids=("a4",)),
        ),
    )


REJECTIONS: list[tuple[str, object, CaseBounds]] = [
    (
        "fixture-unknown",
        lambda f, k: f(fixture_id="other-fixture"),
        DEFAULT_CASE_BOUNDS,
    ),
    (
        "manifest-digest-mismatch",
        lambda f, k: f(manifest_digest=PLACEHOLDER_DIGEST),
        DEFAULT_CASE_BOUNDS,
    ),
    (
        "schema-version-unsupported",
        lambda f, k: k.tamper_case(f(), schema_version="structured-v9.9"),
        DEFAULT_CASE_BOUNDS,
    ),
    (
        "slot-unregistered",
        lambda f, k: k.tamper_case(
            f(), slot_patches=(SlotPatch(slot_id="s9", node_ids=("n1", "n2")),)
        ),
        DEFAULT_CASE_BOUNDS,
    ),
    (
        "unknown-node-reference",
        lambda f, k: k.tamper_case(
            f(), slot_patches=(SlotPatch(slot_id="s1", node_ids=("ghost",)),)
        ),
        DEFAULT_CASE_BOUNDS,
    ),
    (
        "node-not-in-any-slot",
        lambda f, k: k.tamper_case(f(), slot_patches=(k.default_patches[0],)),
        DEFAULT_CASE_BOUNDS,
    ),
    (
        "node-listed-twice",
        lambda f, k: k.tamper_case(
            f(),
            slot_patches=(
                k.default_patches[0],
                SlotPatch(slot_id="s2", node_ids=("n2",)),
            ),
        ),
        DEFAULT_CASE_BOUNDS,
    ),
    (
        "node-id-collides-with-fixture",
        lambda f, k: f(
            nodes=(_context("o1"),),
            reference_graph=(),
            slot_patches=(SlotPatch(slot_id="s1", node_ids=("o1",)),),
        ),
        DEFAULT_CASE_BOUNDS,
    ),
    (
        "edge-source-not-a-node",
        lambda f, k: f(reference_graph=(_edge("e9", "ghost", k.node_target("n1")),)),
        DEFAULT_CASE_BOUNDS,
    ),
    (
        "role-not-allowed-in-slot",
        lambda f, k: f(
            nodes=(_context("n1"), _delivery("n3"), _context("n5")),
            reference_graph=(),
            slot_patches=(
                SlotPatch(slot_id="s1", node_ids=("n1",)),
                SlotPatch(slot_id="s2", node_ids=("n3", "n5")),
            ),
        ),
        DEFAULT_CASE_BOUNDS,
    ),
    (
        "slot-capacity-exceeded",
        lambda f, k: f(
            nodes=(
                _context("n1"),
                _delivery("n3"),
                _delivery("n5"),
                _delivery("n6"),
                _delivery("n7"),
            ),
            reference_graph=(),
            slot_patches=(
                SlotPatch(slot_id="s1", node_ids=("n1",)),
                SlotPatch(slot_id="s2", node_ids=("n3", "n5", "n6", "n7")),
            ),
        ),
        DEFAULT_CASE_BOUNDS,
    ),
    (
        "node-capacity-exceeded",
        lambda f, k: f(),
        CaseBounds(max_nodes=1),
    ),
    (
        "edge-capacity-exceeded",
        lambda f, k: f(),
        CaseBounds(max_edges=1),
    ),
    (
        "node-text-empty",
        lambda f, k: f(
            nodes=(_context("k1", ""),),
            reference_graph=(),
            slot_patches=(SlotPatch(slot_id="s1", node_ids=("k1",)),),
        ),
        DEFAULT_CASE_BOUNDS,
    ),
    (
        "node-text-capacity-exceeded",
        lambda f, k: f(
            nodes=(_context("k1", "网" * 1501),),
            reference_graph=(),
            slot_patches=(SlotPatch(slot_id="s1", node_ids=("k1",)),),
        ),
        DEFAULT_CASE_BOUNDS,
    ),
    (
        "slot-text-capacity-exceeded",
        lambda f, k: f(
            nodes=(_delivery("k1", "网" * 900),),
            reference_graph=(),
            slot_patches=(SlotPatch(slot_id="s2", node_ids=("k1",)),),
        ),
        DEFAULT_CASE_BOUNDS,
    ),
    (
        "total-text-capacity-exceeded",
        lambda f, k: f(),
        CaseBounds(max_total_code_points=10),
    ),
    (
        "serialized-size-exceeded",
        lambda f, k: f(),
        CaseBounds(max_serialized_bytes=200),
    ),
    (
        "alias-unregistered",
        lambda f, k: f(
            reference_graph=(
                k.default_edges[0],
                _edge("e9", "n2", k.alias_target("unknown-material")),
            )
        ),
        DEFAULT_CASE_BOUNDS,
    ),
    (
        "edge-type-not-allowed-for-alias",
        lambda f, k: f(
            reference_graph=(
                k.default_edges[0],
                _edge(
                    "e9",
                    "n2",
                    k.alias_target("working-appendix"),
                    EdgeType.CONTINUES_AT,
                ),
            )
        ),
        DEFAULT_CASE_BOUNDS,
    ),
    (
        "self-loop",
        lambda f, k: f(
            reference_graph=(_edge("e9", "n1", k.node_target("n1"), EdgeType.CONTINUES_AT),)
        ),
        DEFAULT_CASE_BOUNDS,
    ),
    (
        "duplicate-edge",
        lambda f, k: f(
            reference_graph=(
                k.default_edges[3],
                _edge("e9", "n2", k.node_target("n3")),
            )
        ),
        DEFAULT_CASE_BOUNDS,
    ),
    (
        "graph-cycle",
        lambda f, k: f(
            reference_graph=(
                _edge("e9", "n1", k.node_target("n2"), EdgeType.CONTINUES_AT),
                _edge("e10", "n2", k.node_target("n1"), EdgeType.CONTINUES_AT),
            )
        ),
        DEFAULT_CASE_BOUNDS,
    ),
    (
        "depth-exceeded",
        lambda f, k: _chain_case(f, k),
        DEFAULT_CASE_BOUNDS,
    ),
    (
        "depth-exceeded#alias-tail",
        lambda f, k: _alias_tail_case(f, k),
        DEFAULT_CASE_BOUNDS,
    ),
    (
        "encoding-invalid",
        lambda f, k: k.tamper_case(
            f(),
            nodes=(_context("n1", "坏字\ud800"),),
            reference_graph=(),
            slot_patches=(SlotPatch(slot_id="s1", node_ids=("n1",)),),
        ),
        DEFAULT_CASE_BOUNDS,
    ),
    (
        "private-value-leak",
        lambda f, k: _leaky_case(f, k),
        DEFAULT_CASE_BOUNDS,
    ),
    (
        "input-digest-mismatch",
        lambda f, k: k.tamper_case(f(), input_digest=PLACEHOLDER_DIGEST),
        DEFAULT_CASE_BOUNDS,
    ),
]


def _expected_code(label: str) -> str:
    """A label may add a ``#detail`` suffix so two rows can share one rejection code."""

    return label.split("#", 1)[0]


@pytest.mark.parametrize(
    ("label", "builder", "bounds"),
    REJECTIONS,
    ids=[entry[0] for entry in REJECTIONS],
)
def test_rejections_use_stable_codes(
    label: str,
    builder,
    bounds: CaseBounds,
    manifest,
    case_factory,
    soc_kit,
) -> None:
    case = builder(case_factory, soc_kit)
    admission = admit_case(case, manifest=manifest, bounds=bounds)

    assert admission.accepted is False
    assert admission.material is None
    assert admission.rejection is not None
    assert admission.rejection.code == _expected_code(label)


def test_every_tested_code_is_part_of_the_taxonomy() -> None:
    assert {_expected_code(entry[0]) for entry in REJECTIONS} <= set(REJECTION_CODES)


def test_root_case_is_admissible(manifest, root_case) -> None:
    admission = admit_case(root_case, manifest=manifest)

    assert admission.accepted is True
    assert admission.rejection is None
    assert admission.material is not None
    assert admission.material.material_digest == root_case.input_digest


def test_untouched_root_keeps_the_fixture_slots(manifest, case_factory, soc_kit) -> None:
    root = case_factory(nodes=(), reference_graph=(), slot_patches=())
    admission = admit_case(root, manifest=manifest)

    assert admission.accepted is True
    assert admission.material is not None
    assert admission.material.slots[0].contents == (
        role_label(NodeRole.CONTEXT) + soc_kit.original_s1_text,
    )
    assert admission.material.slots[1].contents == (
        role_label(NodeRole.DELIVERY_NOTE) + soc_kit.original_s2_text,
    )


def test_candidate_may_reference_a_fixture_node(manifest, case_factory, soc_kit) -> None:
    case = case_factory(
        nodes=(_context("m1"),),
        reference_graph=(_edge("m1-e", "m1", soc_kit.node_target("o2")),),
        slot_patches=(SlotPatch(slot_id="s1", node_ids=("m1",)),),
    )
    admission = admit_case(case, manifest=manifest)

    assert admission.accepted is True
    assert admission.material is not None
    rendered = "\n".join(material_texts(admission.material))
    assert "相关材料：s2 第 1 条" in rendered


def test_three_edge_path_ending_at_an_alias_is_admissible(
    manifest, case_factory, soc_kit
) -> None:
    """The depth bound counts alias edges, but a path of exactly three still passes."""

    nodes = (_context("a1"), _context("a2"), _delivery("a3"))
    edges = (
        _edge("c1", "a1", soc_kit.node_target("a2"), EdgeType.CONTINUES_AT),
        _edge("c2", "a2", soc_kit.node_target("a3"), EdgeType.CONTINUES_AT),
        _edge("c3", "a3", soc_kit.alias_target("overview")),
    )
    case = case_factory(
        nodes=nodes,
        reference_graph=edges,
        slot_patches=(
            SlotPatch(slot_id="s1", node_ids=("a1", "a2")),
            SlotPatch(slot_id="s2", node_ids=("a3",)),
        ),
    )
    admission = admit_case(case, manifest=manifest)

    assert admission.accepted is True


def test_fixture_material_passes_the_same_rules(manifest) -> None:
    assert verify_fixture_admissible(manifest) is None


def test_private_value_rejection_does_not_restate_the_value(
    manifest, case_factory, soc_kit
) -> None:
    admission = admit_case(_leaky_case(case_factory, soc_kit), manifest=manifest)

    assert admission.rejection is not None
    assert admission.rejection.code == "private-value-leak"
    assert soc_kit.restricted_value not in admission.rejection.detail
