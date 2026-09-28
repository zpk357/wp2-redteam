"""Small, explicit input preparation for the offline structured experiment."""

from __future__ import annotations

from pydantic import Field

from sandbox.structured_v1.candidates import freeze_g1_candidates
from sandbox.structured_v1.edit_kernel import apply_edit, choose_edit, text_node_ids
from sandbox.structured_v1.fixture import StructuredFixtureManifest
from sandbox.structured_v1.models import (
    Identifier,
    OperationKind,
    StructuredCase,
    StructuredContract,
)
from sandbox.structured_v1.operators import diff_cases, preserved_units
from sandbox.structured_v1.rendering import RenderedMaterial, render_material
from sandbox.structured_v1.sampling import build_root_case, sample_root_structure


class PreparedCandidate(StructuredContract):
    candidate_id: Identifier
    parent_id: Identifier | None = None
    case: StructuredCase
    rendered: RenderedMaterial
    operation: OperationKind | None = None
    preserved: tuple[str, ...] = ()
    rendered_difference: tuple[str, ...] = ()
    readable_slots: tuple[Identifier, ...] = ()


class ExperimentInputs(StructuredContract):
    manifest_digest: str
    normal_control: PreparedCandidate
    candidates: tuple[PreparedCandidate, ...] = Field(default_factory=tuple)


def prepare_inputs(
    manifest: StructuredFixtureManifest,
    *,
    seed: str = "offline-inputs-v1",
    count: int = 10,
) -> ExperimentInputs:
    """Build normal control and material-bearing children through the real kernel."""
    for attempt in range(200):
        normal_state = f"{seed}-normal-{attempt}"
        normal_structure = sample_root_structure(manifest, random_state=normal_state)
        if normal_structure.no_injection:
            break
    else:
        raise RuntimeError("normal-control draw did not produce the no-injection branch")
    normal = build_root_case(
        manifest, normal_structure, generation_identity="normal-control",
        random_state=normal_state,
    )
    normal_rendered = render_material(normal, manifest)
    control = PreparedCandidate(
        candidate_id="normal-control", case=normal, rendered=normal_rendered,
        readable_slots=tuple(slot.slot_id for slot in normal_rendered.slots),
    )
    frozen = freeze_g1_candidates(manifest, seed=seed)
    # Rebuild the selected parent cases deterministically, then apply one shared edit.
    parents: dict[str, StructuredCase] = {}
    for item in frozen.roots():
        structure = sample_root_structure(manifest, random_state=f"{seed}-root-{len(parents)}")
        texts = {p.node_id: f"Offline material {p.node_id}." for p in structure.placements}
        parents[item.candidate_id] = build_root_case(
            manifest,
            structure,
            generation_identity=item.candidate_id,
            random_state=f"{seed}-root-{len(parents)}",
            texts=None if structure.no_injection else texts,
        )
    prepared: list[PreparedCandidate] = []
    for index, item in enumerate(frozen.edited()[:count]):
        parent = parents[item.parent_id or next(iter(parents))]
        state = f"{seed}-edit-{index}"
        position = choose_edit(parent, manifest=manifest, random_state=state)
        texts = {node: f"Offline edit {node}." for node in text_node_ids(position)}
        child = apply_edit(
            parent,
            position,
            manifest=manifest,
            generation_identity=item.candidate_id,
            random_state=state,
            texts=texts or None,
        )
        parent_rendered = render_material(parent, manifest)
        rendered = render_material(child, manifest)
        change = diff_cases(parent, child)
        parent_slots = {slot.slot_id: slot.contents for slot in parent_rendered.slots}
        prepared.append(PreparedCandidate(
            candidate_id=item.candidate_id,
            parent_id=item.parent_id,
            case=child,
            rendered=rendered,
            operation=position.operation,
            preserved=preserved_units(parent, child, manifest=manifest),
            rendered_difference=tuple(
                slot.slot_id
                for slot in rendered.slots
                if parent_slots.get(slot.slot_id) != slot.contents
            ),
            readable_slots=tuple(slot.slot_id for slot in rendered.slots),
        ))
        assert not change.is_empty()
    return ExperimentInputs(manifest_digest=manifest.manifest_digest, normal_control=control,
                            candidates=tuple(prepared))


__all__ = ["ExperimentInputs", "PreparedCandidate", "prepare_inputs"]
