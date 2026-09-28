"""`random_evolution`: the fair main control - same roots, same edit kernel, no feedback reads.

`SOC-FBK-09` fixes what this arm may read when it picks a parent: the candidates that passed the
shared admission gate, the shared input-digest dedup, and its own frozen random stream. Every
other arm reads some cross-episode result to choose; this one must not, and the tests below prove
that four ways: the identity is its own, the pool is filled by admission alone, the draw does not
move when every source it may not read is poisoned with foreign values, and both the root draw and
the local edit are the very same kernels the guided arm uses.
"""

from structured_coverage_helpers import record_coverage

from sandbox.replay.digests import sha256_digest
from sandbox.structured_v1.coverage import BehaviorAtom, CoverageResult
from sandbox.structured_v1.experiment_inputs import prepare_inputs
from sandbox.structured_v1.search import (
    GUIDED_FEEDBACK_SOURCES,
    ArmKind,
    CandidateRefused,
    SearchState,
    SelectionReceipt,
    TwoArmSearch,
)


def _result(episode_id: str, fixture_id: str, key: str) -> CoverageResult:
    return CoverageResult(
        episode_id=episode_id, fixture_id=fixture_id, behavior=(BehaviorAtom(key=(key,)),)
    )


def _parents(manifest, *, count: int = 4) -> dict:
    inputs = prepare_inputs(manifest, count=count)
    return {item.candidate_id: item.case for item in inputs.candidates}


def _search(manifest, seed: str, *, count: int = 4) -> TwoArmSearch:
    return TwoArmSearch(_parents(manifest, count=count), manifest, seed=seed)


def _pooled_search(manifest, seed: str, *, count: int = 4) -> TwoArmSearch:
    """An arm whose pool is already filled, admitted through the same gate generation uses."""

    search = _search(manifest, seed, count=count)
    for candidate_id in sorted(search.parents):
        search._pool_admit(ArmKind.RANDOM_EVOLUTION, search.parents[candidate_id])
    return search


def _draws(search: TwoArmSearch, *, steps: int) -> list[SelectionReceipt]:
    """Draw `steps` selections without executing them, advancing only the shared schedule clock.

    Only the choice is under test here. Driving real edits offline would collide with the
    placeholder wording, which is a pure function of the node id (and names the arm), so a
    second edit of the same node would materialise an identical child and be refused. The clock
    advance below is exactly what a successful generation does; it carries no feedback at all.
    """

    receipts: list[SelectionReceipt] = []
    for _ in range(steps):
        receipt = search.select(ArmKind.RANDOM_EVOLUTION)
        receipts.append(receipt)
        search.state = search.state.model_copy(
            update={
                "opportunity": search.state.opportunity + 1,
                "feedback_opportunity": search.state.feedback_opportunity + 1,
                "direction_opportunities": search._bump_direction(receipt),
            }
        )
    return receipts


def _first_local_index(seed: str, direction: str = "data-release") -> int:
    """The first per-direction index that is a local opportunity and not a root slot."""

    index = 0
    while index % 5 in TwoArmSearch._root_slots(f"{seed}:{direction}", index // 5):
        index += 1
    return index


def _poisoned(state: SearchState) -> SearchState:
    """Fill every source this arm is forbidden to read with foreign values."""

    return state.model_copy(
        update={
            "cooldown": {"poison-parent": 9},
            "occurrence": {"poison-parent": 7},
            "unit_cooldown": {"poison-unit": 9},
            "unit_occurrence": {"poison-unit": 7},
            "unit_opportunities": {"poison-unit": 5},
            "unit_index": {"poison-unit": ("poison-parent",)},
            "unit_dimension": {"poison-unit": "behavior"},
            "unit_no_new": {"poison-unit": 3},
            "parent_no_new": {"poison-parent": 3},
            "archive": {"behavior": ("poison-parent",)},
        }
    )


def _shape(case) -> tuple:
    """The drawn structure, with the wording taken out: the offline placeholder names the arm."""

    return (
        tuple(sorted(node.node_id for node in case.nodes)),
        tuple(sorted(node.role.value for node in case.nodes)),
        tuple(sorted((edge.source, edge.edge_type.value) for edge in case.reference_graph)),
        tuple(sorted(patch.slot_id for patch in case.slot_patches)),
    )


def test_random_evolution_is_its_own_arm_identity() -> None:
    assert ArmKind.RANDOM_EVOLUTION.value == "random_evolution"
    assert ArmKind.RANDOM_EVOLUTION is not ArmKind.RANDOM_INDEPENDENT
    assert ArmKind.RANDOM_EVOLUTION is not ArmKind.COVERAGE_GUIDED


def test_the_first_evolution_opportunity_is_a_root_and_joins_the_pool(manifest) -> None:
    search = _search(manifest, "evolution-start")

    receipt = search.select(ArmKind.RANDOM_EVOLUTION)

    assert receipt.root_restart is True
    assert receipt.reason == "scheduled-root-restart"
    assert receipt.selected_direction == "data-release"
    assert receipt.parent_pool == (), "the pool starts empty; nothing has been admitted yet"

    root = search.generate(receipt)
    identity = root.mutation_lineage.generation_identity
    assert root.mutation_lineage.operation is None, "a scheduled root is a real root"
    assert search.state.evolution_pool == (identity,), "admission alone puts it in the pool"


def test_a_local_evolution_opportunity_draws_a_pooled_parent(manifest) -> None:
    search = _pooled_search(manifest, "evolution-local")
    search.state = search.state.model_copy(
        update={"direction_opportunities": {"data-release": _first_local_index(search.seed)}}
    )

    receipt = search.select(ArmKind.RANDOM_EVOLUTION)

    assert receipt.root_restart is False
    assert receipt.reason == "uniform-parent-pool-draw"
    assert receipt.parent_id in search.state.evolution_pool
    assert receipt.parent_baseline is None, "it edits a parent it never executed itself"


def test_an_empty_pool_falls_back_to_a_root_restart(manifest) -> None:
    search = _search(manifest, "evolution-empty")
    search.state = search.state.model_copy(
        update={"direction_opportunities": {"data-release": _first_local_index(search.seed)}}
    )

    receipt = search.select(ArmKind.RANDOM_EVOLUTION)

    assert receipt.root_restart is True
    assert receipt.reason == "empty-parent-pool"


def test_every_evolution_receipt_reads_no_feedback(manifest) -> None:
    receipts = _draws(_pooled_search(manifest, "evolution-none"), steps=10)

    assert any(not item.root_restart for item in receipts), "the schedule must reach a local slot"
    for receipt in receipts:
        assert receipt.feedback_sources == (), "random_evolution reads no cross-episode feedback"
        assert receipt.parent_baseline is None, "it holds no coverage baseline at all"
        assert receipt.cooldown == 0


def test_poisoning_every_feedback_source_does_not_move_the_draw(manifest) -> None:
    """The strongest form of the `SOC-FBK-09` proof: fill every source it may not read."""

    clean = _draws(_pooled_search(manifest, "evolution-poison"), steps=10)
    search = _pooled_search(manifest, "evolution-poison")
    search.state = _poisoned(search.state)
    poisoned = _draws(search, steps=10)

    assert [item.parent_id for item in poisoned] == [item.parent_id for item in clean]
    assert [item.selected_direction for item in poisoned] == [
        item.selected_direction for item in clean
    ]
    assert [item.root_restart for item in poisoned] == [item.root_restart for item in clean]


def test_the_identical_run_repeats_its_parent_sequence(manifest) -> None:
    """Only the frozen stream decides the draw, so an identical run repeats it exactly."""

    first = _draws(_pooled_search(manifest, "evolution-repeat"), steps=10)
    second = _draws(_pooled_search(manifest, "evolution-repeat"), steps=10)

    assert [item.parent_id for item in first] == [item.parent_id for item in second]


def test_the_pool_snapshot_verifies_against_its_receipt_digest(manifest) -> None:
    """The receipt carries the pool it chose from, so the draw is checkable offline."""

    receipts = _draws(_pooled_search(manifest, "evolution-pool"), steps=10)
    local = [item for item in receipts if not item.root_restart]

    assert local, "the schedule must reach a local slot"
    for receipt in local:
        assert receipt.pool_digest == sha256_digest({"parent_pool": list(receipt.parent_pool)})
        assert receipt.parent_id in receipt.parent_pool
        assert len(receipt.parent_pool) == len(set(receipt.parent_pool))


def test_a_repeated_input_is_not_pooled_twice(manifest) -> None:
    search = _search(manifest, "evolution-dedup")
    root = search.generate(search.select(ArmKind.RANDOM_EVOLUTION))
    identity = root.mutation_lineage.generation_identity

    search._pool_admit(ArmKind.RANDOM_EVOLUTION, root)

    assert search.state.evolution_pool == (identity,), "a seen input digest is not re-pooled"
    assert search.state.input_digests == (root.input_digest,)


def test_the_evolution_arm_still_settles_full_coverage(manifest) -> None:
    """Its results are recorded in full - they are simply not allowed back into its own choosing."""

    search = _search(manifest, "evolution-settle")
    root = search.generate(search.select(ArmKind.RANDOM_EVOLUTION))
    identity = root.mutation_lineage.generation_identity

    record_coverage(
        search,
        _result("episode-1", manifest.fixture_id, "behavior-evolution"),
        parent_id=identity,
    )

    assert search.state.ledger.global_seen.behavior, "coverage is still recorded"
    assert identity in search.state.parent_coverage, "its own evidence is still kept"

    follow_up = search.select(ArmKind.RANDOM_EVOLUTION)
    assert follow_up.feedback_sources == ()
    assert follow_up.parent_baseline is None


def test_both_schedule_arms_draw_the_same_root_structure_from_the_same_stream(manifest) -> None:
    """Same point of the shared distribution, same structure - whichever arm asks for it."""

    guided = _search(manifest, "shared-root")
    evolution = _search(manifest, "shared-root")
    guided_receipt = guided.select(ArmKind.COVERAGE_GUIDED)
    evolution_receipt = evolution.select(ArmKind.RANDOM_EVOLUTION)

    assert guided_receipt.selected_direction == evolution_receipt.selected_direction
    assert guided_receipt.root_restart and evolution_receipt.root_restart

    evolution_receipt = evolution_receipt.model_copy(
        update={"random_state": guided_receipt.random_state}
    )
    guided_root = guided.generate(guided_receipt)
    evolution_root = evolution.generate(evolution_receipt)

    assert _shape(guided_root) == _shape(evolution_root)


def test_both_schedule_arms_choose_the_same_edit_from_the_same_stream(manifest) -> None:
    """Same parent and stream: both arms take the same operation at the same position."""

    parents = _parents(manifest)
    parent_id = sorted(parents)[0]
    stream = "shared-edit"

    guided = TwoArmSearch(dict(parents), manifest, seed="g")
    record_coverage(
        guided, _result("ep-1", manifest.fixture_id, "behavior-a"), parent_id=parent_id
    )
    guided.generate(
        SelectionReceipt(
            opportunity=0,
            arm=ArmKind.COVERAGE_GUIDED,
            parent_id=parent_id,
            selected_direction="data-release",
            reason="test",
            random_state=stream,
            root_restart=False,
            parent_baseline=guided.state.parent_coverage[parent_id],
        )
    )

    evolution = TwoArmSearch(dict(parents), manifest, seed="e")
    evolution.generate(
        SelectionReceipt(
            opportunity=0,
            arm=ArmKind.RANDOM_EVOLUTION,
            parent_id=parent_id,
            selected_direction="data-release",
            reason="test",
            random_state=stream,
            root_restart=False,
        )
    )

    assert guided.last_plan is not None and evolution.last_plan is not None
    assert guided.last_plan.operation is not None
    assert evolution.last_plan.operation is guided.last_plan.operation
    assert evolution.last_plan.position == guided.last_plan.position
    assert evolution.last_plan.editable_nodes == guided.last_plan.editable_nodes


def test_the_guided_arm_declares_the_feedback_it_read(manifest) -> None:
    """The contrast that gives `feedback_sources` its meaning."""

    search = _search(manifest, "guided-declares")

    receipt = search.select(ArmKind.COVERAGE_GUIDED)

    assert receipt.feedback_sources, "the guided arm really reads feedback"
    assert set(receipt.feedback_sources) <= set(GUIDED_FEEDBACK_SOURCES)


def test_a_refused_child_spends_the_opportunity_without_fabricating_a_candidate(manifest) -> None:
    """A legal position whose child the gate refuses fails exactly like a wording failure.

    Offline the placeholder wording is a function of the node id, so editing one node twice is a
    no-op and the shared admission gate refuses that child (`no-registered-change`).  The
    opportunity is still spent once, the shared direction clock still advances, the plan is kept
    for diagnosis, and the arm keeps no candidate for the failed opportunity.
    """

    search = _search(manifest, "refused")
    refusal: CandidateRefused | None = None
    advance: tuple[int, int] | None = None

    for _ in range(80):
        receipt = search.select(ArmKind.RANDOM_EVOLUTION)
        before = (search.state.opportunity, search.state.feedback_opportunity)
        try:
            search.generate(receipt)
        except CandidateRefused as refused:
            refusal = refused
            advance = (
                search.state.opportunity - before[0],
                search.state.feedback_opportunity - before[1],
            )
            break

    assert refusal is not None, "the placeholder wording must eventually repeat itself"
    assert advance == (1, 1), "the opportunity is spent once and the shared clock advances once"
    assert refusal.plan is not None, "the refused opportunity keeps its plan for diagnosis"
    assert not any(
        case.mutation_lineage.generation_identity == f"opportunity-{refusal.plan.opportunity}"
        for case in search.parents.values()
    ), "no candidate is invented for a generation that never produced one"
