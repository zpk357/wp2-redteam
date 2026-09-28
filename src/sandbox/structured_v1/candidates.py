"""The frozen G1 candidate set and its lineage (run-package §7).

Twenty candidates, built deterministically: ten roots, at least two of them no-injection,
and ten local edits with at least two parents each carrying at least two children. That
parent/child invariant is the precondition the G1 criterion needs, because "the material
changed the behaviour" compares a parent's children against each other.

The content is deterministic seed material - it is the probe's **fixed input**, not
evidence. Evidence integrity (effect capture, closure, host image) is a run-time concern
and lives elsewhere; here a candidate is only a digest plus its lineage.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sandbox.structured_v1.edit_kernel import apply_edit, choose_edit, text_node_ids
from sandbox.structured_v1.fixture import StructuredFixtureManifest
from sandbox.structured_v1.models import (
    Identifier,
    Sha256Digest,
    StructuredCase,
    StructuredContract,
)
from sandbox.structured_v1.sampling import RootStructure, build_root_case, sample_root_structure
from sandbox.structured_v1.validation import admit_case

DEFAULT_SEED = "g1-candidates-v1"
ROOT_COUNT = 10
EDITED_COUNT = 10


class CandidateKind(StrEnum):
    ROOT = "root"
    EDITED = "edited"


class FrozenCandidate(StructuredContract):
    candidate_id: Identifier
    kind: CandidateKind
    parent_id: Identifier | None = None
    no_injection: bool = False
    material_digest: Sha256Digest


@dataclass(frozen=True)
class G1Candidates:
    """The frozen set plus the cases it was drawn from, for a run that must execute them."""

    frozen: G1CandidateSet
    cases: dict[str, StructuredCase]


class G1CandidateSet(StructuredContract):
    """The frozen input to the probe: candidates, their digests, and their lineage."""

    seed: str
    manifest_digest: Sha256Digest
    candidates: tuple[FrozenCandidate, ...] = ()

    def roots(self) -> tuple[FrozenCandidate, ...]:
        return tuple(item for item in self.candidates if item.kind is CandidateKind.ROOT)

    def edited(self) -> tuple[FrozenCandidate, ...]:
        return tuple(item for item in self.candidates if item.kind is CandidateKind.EDITED)

    def no_injection_roots(self) -> tuple[FrozenCandidate, ...]:
        return tuple(item for item in self.roots() if item.no_injection)

    def parents(self) -> dict[str, tuple[str, ...]]:
        by_parent: dict[str, list[str]] = {}
        for item in self.edited():
            if item.parent_id is not None:
                by_parent.setdefault(item.parent_id, []).append(item.candidate_id)
        return {parent: tuple(sorted(children)) for parent, children in by_parent.items()}

    def verify_invariants(self) -> tuple[str, ...]:
        """The G1 preconditions, as failures: empty means the set can run the criterion."""

        failures: list[str] = []
        if len(self.roots()) != ROOT_COUNT or len(self.edited()) != EDITED_COUNT:
            failures.append(
                f"counts are {len(self.roots())} roots / {len(self.edited())} edited"
            )
        if len(self.no_injection_roots()) < 2:
            failures.append("fewer than two no-injection roots")
        parents_with_two = [
            parent for parent, children in self.parents().items() if len(children) >= 2
        ]
        if len(parents_with_two) < 2:
            failures.append("fewer than two parents with at least two children")
        # Each parent's children must differ from one another, or "the material changed
        # the behaviour" is trivially unmeasurable for that parent. No-injection roots may
        # share the fixture digest; that is the point of that branch, not a defect.
        by_digest = {item.candidate_id: item.material_digest for item in self.candidates}
        for parent, children in self.parents().items():
            child_digests = [by_digest[child] for child in children]
            if len(child_digests) != len(set(child_digests)):
                failures.append(f"parent {parent} has two children with the same digest")
        return tuple(failures)


def _texts_for(structure: RootStructure, root_index: int) -> dict[str, str]:
    return {
        placement.node_id: f"Probe material {root_index} - {placement.node_id}."
        for placement in structure.placements
    }


def freeze_g1_candidates(
    manifest: StructuredFixtureManifest,
    *,
    seed: str = DEFAULT_SEED,
) -> G1CandidateSet:
    """Build and freeze the twenty candidates, checking the invariant holds."""

    return build_g1_candidates(manifest, seed=seed).frozen


def build_g1_candidates(
    manifest: StructuredFixtureManifest,
    *,
    seed: str = DEFAULT_SEED,
) -> G1Candidates:
    """The frozen set **and** the cases it was built from.

    The probe needs both: the set is what a run-package freezes and rebuilds, while running the
    probe needs the case behind each id.  Both come from the same single deterministic draw, so
    the cases cannot drift from the digests that were frozen.
    """

    roots: list[FrozenCandidate] = []
    root_cases: dict[str, StructuredCase] = {}
    root_index = 0
    drawn: list[tuple[bool, StructuredCase]] = []
    seen_no_injection = 0

    while seen_no_injection < 2 or len(drawn) < ROOT_COUNT:
        random_state = f"{seed}-root-{root_index}"
        root_index += 1
        structure = sample_root_structure(manifest, random_state=random_state)
        texts = None if structure.no_injection else _texts_for(structure, root_index)
        case = build_root_case(
            manifest,
            structure,
            generation_identity="g1-root",
            random_state=random_state,
            texts=texts,
        )
        drawn.append((structure.no_injection, case))
        if structure.no_injection:
            seen_no_injection += 1

    no_injection_draws = [item for item in drawn if item[0]]
    injected_draws = [item for item in drawn if not item[0]]
    chosen = no_injection_draws + injected_draws[: ROOT_COUNT - len(no_injection_draws)]
    for no_injection, case in chosen:
        material = admit_case(case, manifest=manifest).material
        assert material is not None
        candidate_id = f"root-{len(roots) + 1:02d}"
        roots.append(
            FrozenCandidate(
                candidate_id=candidate_id,
                kind=CandidateKind.ROOT,
                no_injection=no_injection,
                material_digest=material.material_digest,
            )
        )
        root_cases[candidate_id] = case

    # Two children for each of the first two injected roots, then the rest spread out.
    injected_roots = [item for item in roots if not item.no_injection]
    child_plan: list[tuple[str, int]] = [
        (parent.candidate_id, 2) for parent in injected_roots[:2]
    ]
    offset = 0
    while sum(count for _, count in child_plan) < EDITED_COUNT:
        parent = roots[offset % len(roots)]
        child_plan.append((parent.candidate_id, 1))
        offset += 1

    edited: list[FrozenCandidate] = []
    edited_cases: dict[str, StructuredCase] = {}
    for parent_id, count in child_plan:
        parent_case = root_cases[parent_id]
        for _ in range(count):
            random_state = f"{seed}-edit-{len(edited)}"
            position = choose_edit(parent_case, manifest=manifest, random_state=random_state)
            child = apply_edit(
                parent_case,
                position,
                manifest=manifest,
                generation_identity="g1-edit",
                random_state=random_state,
                texts={
                    node_id: f"Probe edit {len(edited)} - {node_id}."
                    for node_id in text_node_ids(position)
                },
            )
            material = admit_case(child, manifest=manifest, parent=parent_case).material
            assert material is not None
            candidate_id = f"edited-{len(edited) + 1:02d}"
            edited.append(
                FrozenCandidate(
                    candidate_id=candidate_id,
                    kind=CandidateKind.EDITED,
                    parent_id=parent_id,
                    material_digest=material.material_digest,
                )
            )
            edited_cases[candidate_id] = child

    result = G1CandidateSet(
        seed=seed,
        manifest_digest=manifest.manifest_digest,
        candidates=(*roots, *edited),
    )
    failures = result.verify_invariants()
    if failures:
        raise RuntimeError(f"the G1 candidate set is not usable: {failures}")
    return G1Candidates(frozen=result, cases={**root_cases, **edited_cases})
