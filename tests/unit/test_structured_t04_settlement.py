"""T04 source-parent settlement, semantic repetition and independent cooldowns."""

from __future__ import annotations

from itertools import count

from sandbox.replay.digests import sha256_digest
from sandbox.structured_v1.coverage import (
    COVERAGE_VERSION,
    BehaviorAtom,
    CoverageExecutionIdentity,
    CoverageResult,
    unit_key_string,
)
from sandbox.structured_v1.experiment_inputs import prepare_inputs
from sandbox.structured_v1.rendering import render_material
from sandbox.structured_v1.search import (
    ArmKind,
    CandidateRefused,
    SelectionReceipt,
    TwoArmSearch,
    _semantic_outcome_signature,
)

TEST_CONFIG = sha256_digest({"t04": "offline-execution"})


def _search(manifest, *, seed: str = "t04") -> TwoArmSearch:
    inputs = prepare_inputs(manifest, count=4)
    return TwoArmSearch(
        {item.candidate_id: item.case for item in inputs.candidates},
        manifest,
        seed=seed,
    )


def _bound_result(
    search: TwoArmSearch,
    candidate_id: str,
    episode_id: str,
    key: str,
    *,
    judgment_missing: tuple[str, ...] = (),
    not_admitted: tuple[str, ...] = (),
    evidence_ref: str | None = None,
) -> CoverageResult:
    case = search.parents[candidate_id]
    material = render_material(case, search.manifest)
    return CoverageResult(
        episode_id=episode_id,
        fixture_id=search.manifest.fixture_id,
        execution=CoverageExecutionIdentity(
            candidate_id=candidate_id,
            input_digest=case.input_digest,
            material_digest=material.material_digest,
            manifest_digest=search.manifest.manifest_digest,
            execution_config_digest=TEST_CONFIG,
            coverage_version=COVERAGE_VERSION,
            bundle_digest=sha256_digest({"bundle": episode_id}),
            envelope_digest=sha256_digest({"envelope": episode_id}),
        ),
        behavior=(BehaviorAtom(
            key=(key,),
            evidence_refs=() if evidence_ref is None else (evidence_ref,),
        ),),
        judgment_missing=judgment_missing,
        not_admitted=not_admitted,
    )


def _child(search: TwoArmSearch, parent_id: str, label: str):
    """Generate an admissible child with a distinct generation identity."""

    for attempt in count():
        receipt = SelectionReceipt(
            opportunity=search.state.opportunity,
            arm=ArmKind.RANDOM_EVOLUTION,
            parent_id=parent_id,
            selected_direction="data-release",
            reason="t04-test-child",
            random_state=f"{label}-{attempt}",
            root_restart=False,
        )
        try:
            return search.generate(receipt)
        except CandidateRefused:
            if attempt >= 80:
                raise


def _local_behavior_slot(seed: str) -> int:
    for index in range(1, 100):
        if index % 4 == 1 and index % 5 not in TwoArmSearch._root_slots(
            f"{seed}:data-release", index // 5
        ):
            return index
    raise AssertionError("no local behavior slot")


def test_settlement_charges_source_parent_once_per_child_and_replays_idempotently(manifest):
    search = _search(manifest, seed="source-parent")
    source = sorted(search.parents)[0]
    search.record(
        _bound_result(search, source, "source", "shared"),
        parent_id=source,
        source_parent_id=None,
        selected_unit=None,
        evidence_complete=True,
    )

    children = [_child(search, source, f"child-{index}") for index in range(3)]
    for index, child in enumerate(children):
        search.record(
            _bound_result(search, child.mutation_lineage.generation_identity,
                          f"child-{index}", "shared"),
            parent_id=child.mutation_lineage.generation_identity,
            source_parent_id=source,
            selected_unit=unit_key_string(("selected",)),
            evidence_complete=True,
        )

    assert search.state.parent_no_new[source] == 0
    assert search.state.cooldown[source] == 10
    assert search.state.parent_no_new.get(children[-1].mutation_lineage.generation_identity) is None
    assert search.state.unit_no_new[unit_key_string(("selected",))] == 0
    assert search.state.unit_cooldown[unit_key_string(("selected",))] == 10

    before = search.state
    replay = _bound_result(
        search, children[-1].mutation_lineage.generation_identity, "child-2", "shared"
    )
    search.record(
        replay,
        parent_id=children[-1].mutation_lineage.generation_identity,
        source_parent_id=source,
        selected_unit=unit_key_string(("selected",)),
        evidence_complete=True,
    )
    assert search.state == before, "the exact child settlement is idempotent"


def test_incomplete_no_new_is_not_counted_but_independent_new_coverage_resets(manifest):
    search = _search(manifest, seed="quality-gate")
    source = sorted(search.parents)[0]
    search.record(
        _bound_result(search, source, "source", "shared"),
        parent_id=source,
        source_parent_id=None,
        selected_unit=None,
        evidence_complete=True,
    )
    unit = unit_key_string(("selected",))
    search.state = search.state.model_copy(update={
        "parent_no_new": {source: 2},
        "unit_no_new": {unit: 2},
    })

    incomplete = _child(search, source, "incomplete")
    incomplete_id = incomplete.mutation_lineage.generation_identity
    search.record(
        _bound_result(search, incomplete_id, "incomplete", "shared"),
        parent_id=incomplete_id,
        source_parent_id=source,
        selected_unit=unit,
        evidence_complete=False,
    )
    assert search.state.parent_no_new[source] == 2
    assert search.state.unit_no_new[unit] == 2
    assert source not in search.state.cooldown
    assert unit not in search.state.unit_cooldown
    assert incomplete_id not in search.state.parent_coverage

    new_child = _child(search, source, "partial-new")
    new_id = new_child.mutation_lineage.generation_identity
    search.record(
        _bound_result(search, new_id, "partial-new", "new-unit"),
        parent_id=new_id,
        source_parent_id=source,
        selected_unit=unit,
        evidence_complete=False,
    )
    assert search.state.parent_no_new[source] == 0
    assert search.state.unit_no_new[unit] == 0

    missing_child = _child(search, source, "judgment-missing")
    missing_id = missing_child.mutation_lineage.generation_identity
    search.record(
        _bound_result(
            search, missing_id, "judgment-missing", "new-unit", judgment_missing=("judge",)
        ),
        parent_id=missing_id,
        source_parent_id=source,
        selected_unit=unit,
        evidence_complete=True,
    )
    assert search.state.parent_no_new[source] == 0
    assert search.state.unit_no_new[unit] == 0

    termination_child = _child(search, source, "termination")
    termination_id = termination_child.mutation_lineage.generation_identity
    search.record(
        _bound_result(
            search, termination_id, "termination", "new-unit",
            not_admitted=("termination:model-stopped",),
        ),
        parent_id=termination_id,
        source_parent_id=source,
        selected_unit=unit,
        evidence_complete=True,
    )
    assert search.state.parent_no_new[source] == 1
    assert search.state.unit_no_new[unit] == 1


def test_parent_and_unit_cooldowns_do_not_pause_each_other(manifest):
    search = _search(manifest, seed="independent-pauses")
    source = sorted(search.parents)[0]
    search.record(
        _bound_result(search, source, "source", "shared"),
        parent_id=source,
        source_parent_id=None,
        selected_unit=None,
        evidence_complete=True,
    )
    unit = unit_key_string(("selected",))
    for index in range(3):
        child = _child(search, source, f"parent-only-{index}")
        child_id = child.mutation_lineage.generation_identity
        search.record(
            _bound_result(search, child_id, f"parent-only-{index}", "shared"),
            parent_id=child_id,
            source_parent_id=source,
            selected_unit=None,
            evidence_complete=True,
        )
    assert search.state.cooldown[source] == 10
    assert unit not in search.state.unit_cooldown

    # A distinct selected unit can reach its own threshold while the first parent's pause stays
    # untouched.  The source label is deliberately different to keep the two counters separate.
    for index in range(3):
        child = _child(search, source, f"unit-only-{index}")
        child_id = child.mutation_lineage.generation_identity
        search.record(
            _bound_result(search, child_id, f"unit-only-{index}", "shared"),
            parent_id=child_id,
            source_parent_id="other-source",
            selected_unit=unit,
            evidence_complete=True,
        )
    assert search.state.cooldown[source] == 10
    assert search.state.unit_cooldown[unit] == 10


def test_semantic_repeat_signature_excludes_identity_references_and_order(manifest):
    search = _search(manifest, seed="semantic-repeat")
    source = sorted(search.parents)[0]
    first = _bound_result(search, source, "first", "shared", evidence_ref="ref-a")
    second = _bound_result(search, source, "second", "shared", evidence_ref="ref-b")
    assert _semantic_outcome_signature(first) == _semantic_outcome_signature(second)

    search.record(first, parent_id=source, source_parent_id=None,
                  selected_unit=None, evidence_complete=True)
    child = _child(search, source, "repeat")
    child_id = child.mutation_lineage.generation_identity
    search.record(
        _bound_result(search, child_id, "repeat", "shared", evidence_ref="ref-child"),
        parent_id=child_id,
        source_parent_id=source,
        selected_unit=None,
        evidence_complete=True,
    )
    assert search.state.diagnostics.repeated_outcomes == 0
    child2 = _child(search, source, "repeat-2")
    child2_id = child2.mutation_lineage.generation_identity
    search.record(
        _bound_result(search, child2_id, "repeat-2", "shared", evidence_ref="different"),
        parent_id=child2_id,
        source_parent_id=source,
        selected_unit=None,
        evidence_complete=True,
    )
    assert search.state.diagnostics.repeated_outcomes == 1


def test_cooldown_is_checked_before_tick_and_random_evolution_does_not_tick_it(manifest):
    search = _search(manifest, seed="cooldown-boundary")
    source = sorted(search.parents)[0]
    search.record(
        _bound_result(search, source, "source", "shared"),
        parent_id=source,
        source_parent_id=None,
        selected_unit=None,
        evidence_complete=True,
    )
    unit = unit_key_string(("shared",))
    slot = _local_behavior_slot("cooldown-boundary")
    search.state = search.state.model_copy(update={
        "feedback_opportunity": 0,
        "direction_opportunities": {"data-release": slot},
        "cooldown": dict.fromkeys(search.parents, 10),
    })
    for remaining in range(10, 0, -1):
        receipt = search.select(ArmKind.COVERAGE_GUIDED)
        assert receipt.root_restart
        assert receipt.source_parent_id is None
        assert receipt.cooldown == remaining
        assert all(
            search.state.cooldown.get(candidate, 0) == remaining - 1
            for candidate in search.parents
        )
    restored = search.select(ArmKind.COVERAGE_GUIDED)
    assert not restored.root_restart
    assert restored.parent_id == source
    assert restored.source_parent_id == source
    assert restored.selected_unit == unit

    evolution = _search(manifest, seed="no-cooldown-read")
    for candidate in evolution.parents.values():
        evolution._pool_admit(ArmKind.RANDOM_EVOLUTION, candidate)
    evolution.state = evolution.state.model_copy(update={
        "cooldown": {"poison-parent": 10},
        "unit_cooldown": {"poison-unit": 10},
    })
    evolution.select(ArmKind.RANDOM_EVOLUTION)
    assert evolution.state.cooldown == {"poison-parent": 10}
    assert evolution.state.unit_cooldown == {"poison-unit": 10}
