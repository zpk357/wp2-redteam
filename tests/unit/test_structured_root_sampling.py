"""The common root distribution and its independent streams (SS-012).

The sampler must be replayable from its stream, keep every draw inside the frozen
bounds, point every internal reference forward, and reproduce the fixture's own
material on the no-injection branch.  Each of those is asserted over many draws
rather than one, because the distribution - not a single sample - is the contract.
"""

from __future__ import annotations

from collections import Counter

import pytest

from sandbox.structured_v1.models import ReferenceTargetKind
from sandbox.structured_v1.rendering import render_material
from sandbox.structured_v1.sampling import (
    RootStructure,
    build_root_case,
    sample_root_structure,
)
from sandbox.structured_v1.streams import (
    edit_random_state,
    root_random_state,
    stream_random_state,
)
from sandbox.structured_v1.validation import CaseBounds, admit_case

DRAW_LIMIT = 200


def _texts(structure: RootStructure) -> dict[str, str]:
    return {
        placement.node_id: f"合成低信任文字 {placement.node_id}。"
        for placement in structure.placements
    }


def _build(manifest, structure: RootStructure, index: int = 0):
    return build_root_case(
        manifest,
        structure,
        generation_identity=f"root-{index:04d}",
        random_state=root_random_state("campaign-seed", index),
        texts=_texts(structure),
    )


def _draws(manifest, prefix: str, count: int = DRAW_LIMIT) -> list[RootStructure]:
    return [
        sample_root_structure(manifest, random_state=root_random_state(prefix, index))
        for index in range(count)
    ]


def _first_injected(manifest, prefix: str = "injected") -> RootStructure:
    for structure in _draws(manifest, prefix, 50):
        if not structure.no_injection:
            return structure
    raise AssertionError("no injected draw in fifty attempts")


def test_the_same_stream_replays_the_same_structure(manifest) -> None:
    first = sample_root_structure(manifest, random_state=root_random_state("replay", 7))
    second = sample_root_structure(manifest, random_state=root_random_state("replay", 7))

    assert first == second

def test_streams_are_independent_and_stable() -> None:
    assert root_random_state("campaign", 7) == root_random_state("campaign", 7)
    assert root_random_state("campaign", 7) != root_random_state("campaign", 8)
    assert root_random_state("campaign", 7) != edit_random_state("campaign", 7)
    assert stream_random_state("root-distribution", "campaign", 7) == root_random_state(
        "campaign", 7
    )


def test_no_injection_share_is_about_one_in_ten(manifest) -> None:
    draws = _draws(manifest, "share", 1000)
    share = sum(1 for structure in draws if structure.no_injection)

    assert 60 <= share <= 140


def test_no_injection_root_reproduces_the_fixture_material(manifest, case_factory) -> None:
    untouched = case_factory(nodes=(), reference_graph=(), slot_patches=())
    structure = next(
        draw for draw in _draws(manifest, "no-injection") if draw.no_injection
    )
    case = _build(manifest, structure)

    assert render_material(case, manifest).slots == render_material(untouched, manifest).slots
    assert case.input_digest == untouched.input_digest


def test_every_drawn_root_is_admissible_and_points_forward(manifest) -> None:
    for index, structure in enumerate(_draws(manifest, "bulk")):
        case = _build(manifest, structure, index)
        admission = admit_case(case, manifest=manifest)

        assert admission.accepted is True, admission.rejection
        order = {node_id: position for position, node_id in enumerate(structure.node_order)}
        for edge in structure.edges:
            if edge.target.kind is ReferenceTargetKind.NODE:
                assert order[edge.source] < order[edge.target.ref]


def test_draws_stay_inside_the_frozen_bounds(manifest) -> None:
    slots = len(manifest.slots)
    for structure in _draws(manifest, "bounds", 300):
        if structure.no_injection:
            continue
        assert 1 <= len(structure.placements) <= min(8, 4 * slots)
        assert structure.requested_edge_count <= min(12, structure.possible_edge_count)
        assert len(structure.edges) <= structure.requested_edge_count
        per_slot = Counter(placement.slot_id for placement in structure.placements)
        assert set(per_slot) <= {slot.slot_id for slot in manifest.slots}
        assert all(count <= 4 for count in per_slot.values())


def test_injected_roots_need_a_text_for_every_node(manifest) -> None:
    structure = _first_injected(manifest)

    with pytest.raises(ValueError, match="needs a text"):
        build_root_case(
            manifest,
            structure,
            generation_identity="root-0001",
            random_state="state",
        )
    partial = {structure.placements[0].node_id: "只有一条文字。"}
    with pytest.raises(ValueError, match="missing text"):
        build_root_case(
            manifest,
            structure,
            generation_identity="root-0001",
            random_state="state",
            texts=partial,
        )


def test_a_narrowed_profile_narrows_the_draw(manifest) -> None:
    bounds = CaseBounds(max_nodes=2, max_edges=1)
    for index in range(50):
        structure = sample_root_structure(
            manifest,
            random_state=root_random_state("narrow", index),
            bounds=bounds,
        )
        if structure.no_injection:
            continue
        assert len(structure.placements) <= 2
        assert structure.requested_edge_count <= 1


def test_root_lineage_declares_no_parent(manifest) -> None:
    structure = _first_injected(manifest)
    case = _build(manifest, structure)

    assert case.mutation_lineage.parent_candidate_id is None
    assert case.mutation_lineage.operation is None
    assert case.mutation_lineage.random_state == root_random_state("campaign-seed", 0)
    assert case.mutation_lineage.edited_units == ()
    assert case.mutation_lineage.preserved_units == ()


def test_different_streams_do_not_all_draw_the_same_structure(manifest) -> None:
    drawn = {
        str(
            (
                structure.node_order,
                structure.placements,
                structure.edges,
                structure.no_injection,
            )
        )
        for structure in _draws(manifest, "variety", 40)
    }

    assert len(drawn) > 1
