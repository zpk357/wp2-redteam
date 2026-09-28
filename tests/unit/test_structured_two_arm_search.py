from structured_coverage_helpers import record_coverage

from sandbox.structured_v1.campaign import (
    CampaignCheckpoint,
    CampaignLimits,
    CampaignUsage,
    load_checkpoint,
    run_opportunity,
    save_checkpoint,
)
from sandbox.structured_v1.coverage import (
    BehaviorAtom,
    CoverageResult,
    JointAtom,
    JointRelation,
    RiskAtom,
    RiskEventKind,
    unit_key_string,
)
from sandbox.structured_v1.drivers import ScriptedModelPort, drive_structured_v1_episode
from sandbox.structured_v1.experiment_inputs import prepare_inputs
from sandbox.structured_v1.model import ModelDecision
from sandbox.structured_v1.rendering import render_material
from sandbox.structured_v1.search import ArmKind, TwoArmSearch


def _result(episode_id: str, fixture_id: str, key: str) -> CoverageResult:
    return CoverageResult(
        episode_id=episode_id,
        fixture_id=fixture_id,
        behavior=(BehaviorAtom(key=(key,)),),
    )


def _local_behavior_slot(seed: str, direction: str = "data-release") -> int:
    """First per-direction index whose rotation is ``behavior`` and is not a root slot."""

    index = 0
    while True:
        if index % 4 == 1 and index % 5 not in TwoArmSearch._root_slots(
            f"{seed}:{direction}", index // 5
        ):
            return index
        index += 1


def _at_local_behavior_slot(search: TwoArmSearch, seed: str) -> None:
    """Put the arm at a non-root ``behavior`` slot of the first obligation direction."""

    slot = _local_behavior_slot(seed)
    search.state = search.state.model_copy(
        update={
            "feedback_opportunity": 0,
            "direction_opportunities": {"data-release": slot},
        }
    )


def test_independent_arm_draws_a_fresh_root_each_time(manifest) -> None:
    inputs = prepare_inputs(manifest, count=4)
    parents = {item.candidate_id: item.case for item in inputs.candidates}
    search = TwoArmSearch(parents, manifest, seed="arm")
    first = search.select(ArmKind.RANDOM_INDEPENDENT)
    assert first.reason == "independent-root-draw"
    assert first.root_restart is False  # its normal mode is a fresh draw, not a restart
    root = search.generate(first)
    assert root.mutation_lineage.operation is None, "an independent draw is a root"
    second = search.select(ArmKind.RANDOM_INDEPENDENT)
    assert second.parent_id != first.parent_id, "each opportunity draws a fresh root"


def test_first_feedback_opportunity_is_a_scheduled_root(manifest) -> None:
    inputs = prepare_inputs(manifest, count=4)
    parents = {item.candidate_id: item.case for item in inputs.candidates}
    search = TwoArmSearch(parents, manifest, seed="schedule")

    receipt = search.select(ArmKind.COVERAGE_GUIDED)

    assert receipt.root_restart is True
    assert receipt.reason == "scheduled-root-restart"
    assert receipt.selected_direction == "data-release"
    child = search.generate(receipt)
    assert child.mutation_lineage.operation is None, "a scheduled root is a real root"


def test_guided_arm_picks_a_representative_on_a_local_slot(manifest) -> None:
    inputs = prepare_inputs(manifest, count=4)
    parents = {item.candidate_id: item.case for item in inputs.candidates}
    search = TwoArmSearch(parents, manifest, seed="guided")
    chosen = sorted(parents)[-1]
    record_coverage(
        search, _result("episode-a", manifest.fixture_id, "behavior-a"), parent_id=chosen
    )
    _at_local_behavior_slot(search, "guided")

    receipt = search.select(ArmKind.COVERAGE_GUIDED)

    assert receipt.root_restart is False
    assert receipt.parent_id == chosen
    assert receipt.reason == "feedback-ranked-unit"
    assert receipt.selected_key == ("behavior-a",)
    assert receipt.selected_direction == "data-release"


def test_guided_empty_planned_dimension_falls_back_to_behavior(manifest) -> None:
    parents = {
        item.candidate_id: item.case for item in prepare_inputs(manifest, count=4).candidates
    }
    search = TwoArmSearch(parents, manifest, seed="fallback")
    chosen = sorted(parents)[0]
    record_coverage(
        search, _result("episode-a", manifest.fixture_id, "behavior-a"), parent_id=chosen
    )
    index = next(
        index for index in range(1, 20)
        if index % 4 == 2
        and index % 5 not in TwoArmSearch._root_slots("fallback:data-release", index // 5)
    )
    search.state = search.state.model_copy(update={
        "feedback_opportunity": 0,
        "direction_opportunities": {"data-release": index},
    })

    receipt = search.select(ArmKind.COVERAGE_GUIDED)

    assert not receipt.root_restart
    assert receipt.planned_dimension == "risk"
    assert receipt.selected_dimension == "behavior"
    assert receipt.reason == "empty-planned-dimension-fallback"
    assert receipt.parent_id == chosen


def test_guided_empty_behavior_falls_back_to_joint(manifest) -> None:
    parents = {
        item.candidate_id: item.case for item in prepare_inputs(manifest, count=4).candidates
    }
    search = TwoArmSearch(parents, manifest, seed="fallback-joint")
    chosen = sorted(parents)[0]
    risk_key = ("data-release", "restricted", "none", "none", "prepared", "none")
    behavior_key = ("prepared",)
    joint_key = (risk_key, behavior_key, "same_exchange")
    result = CoverageResult(
        episode_id="joint-only",
        fixture_id=manifest.fixture_id,
        joint=(JointAtom(
            key=joint_key,
            relation=JointRelation.SAME_EXCHANGE,
            behavior_key=behavior_key,
            risk_key=risk_key,
        ),),
    )
    record_coverage(search, result, parent_id=chosen)
    index = next(
        index for index in range(1, 20)
        if index % 4 == 1
        and index % 5 not in TwoArmSearch._root_slots("fallback-joint:data-release", index // 5)
    )
    search.state = search.state.model_copy(update={
        "feedback_opportunity": 0,
        "direction_opportunities": {"data-release": index},
    })

    receipt = search.select(ArmKind.COVERAGE_GUIDED)

    assert receipt.planned_dimension == "behavior"
    assert receipt.selected_dimension == "joint"
    assert receipt.selected_key == joint_key


def test_guided_empty_joint_falls_back_to_risk(manifest) -> None:
    parents = {
        item.candidate_id: item.case for item in prepare_inputs(manifest, count=4).candidates
    }
    search = TwoArmSearch(parents, manifest, seed="fallback-risk")
    chosen = sorted(parents)[0]
    risk_key = ("data-release", "restricted", "none", "none", "prepared", "none")
    record_coverage(search, CoverageResult(
        episode_id="risk-only",
        fixture_id=manifest.fixture_id,
        risk=(RiskAtom(key=risk_key, event_kind=RiskEventKind.PREPARED),),
    ), parent_id=chosen)
    index = next(
        index for index in range(1, 30)
        if index % 4 == 3
        and index % 5 not in TwoArmSearch._root_slots("fallback-risk:data-release", index // 5)
    )
    search.state = search.state.model_copy(update={
        "feedback_opportunity": 0,
        "direction_opportunities": {"data-release": index},
    })

    receipt = search.select(ArmKind.COVERAGE_GUIDED)

    assert receipt.planned_dimension == "joint"
    assert receipt.selected_dimension == "risk"
    assert receipt.selected_key == risk_key


def test_three_consecutive_no_new_pauses_the_parent_and_expiry_restores_it(manifest) -> None:
    inputs = prepare_inputs(manifest, count=4)
    parents = {item.candidate_id: item.case for item in inputs.candidates}
    search = TwoArmSearch(parents, manifest, seed="cool")
    chosen = sorted(parents)[-1]
    result = _result("episode-a", manifest.fixture_id, "behavior-a")
    record_coverage(search, result, parent_id=chosen)  # new -> no_new = 0
    for index in range(3):
        record_coverage(
            search,
            result.model_copy(update={"episode_id": f"repeat-{index}"}), parent_id=chosen
        )
    assert search.state.cooldown[chosen] == 10, "three consecutive no-new pauses the parent"
    assert search.state.parent_no_new[chosen] == 0

    search.state = search.state.model_copy(
        update={"cooldown": {chosen: 10}}
    )
    _at_local_behavior_slot(search, "cool")
    receipt = search.select(ArmKind.COVERAGE_GUIDED)
    assert receipt.parent_id != chosen, "a paused parent is not eligible"

    # The pause expires one opportunity at a time, and expiry never clears seen coverage.
    search.state = search.state.model_copy(update={"cooldown": {chosen: 2}})
    search.state = search._tick()
    assert search.state.cooldown[chosen] == 1
    search.state = search._tick()
    assert chosen not in search.state.cooldown, "expiry restores eligibility"
    assert search.state.ledger.global_seen.behavior, "expiry must not clear seen coverage"


def test_units_are_ranked_by_allocated_opportunities_then_occurrences(manifest) -> None:
    """SOC-FBK-09: the unit with fewer local opportunities and occurrences is drawn first."""

    search = TwoArmSearch(
        {item.candidate_id: item.case for item in prepare_inputs(manifest, count=4).candidates},
        manifest,
        seed="unit-rank",
    )
    parent = sorted(search.parents)[0]
    for key in ("unit-a", "unit-b"):
        record_coverage(
            search,
            CoverageResult(
                episode_id=f"episode-{key}",
                fixture_id=manifest.fixture_id,
                behavior=(BehaviorAtom(key=(key,)),),
            ),
            parent_id=parent,
        )
    search.state = search.state.model_copy(
        update={"unit_opportunities": {unit_key_string(("unit-a",)): 3}}
    )
    _at_local_behavior_slot(search, "unit-rank")

    receipt = search.select(ArmKind.COVERAGE_GUIDED)

    assert receipt.selected_key == ("unit-b",), "the less-allocated unit is drawn first"
    assert receipt.parent_id == parent


def test_unit_counters_are_kept_beside_parent_counters(manifest) -> None:
    search = TwoArmSearch(
        {item.candidate_id: item.case for item in prepare_inputs(manifest, count=4).candidates},
        manifest,
        seed="units",
    )
    chosen = sorted(search.parents)[0]
    result = _result("episode-a", manifest.fixture_id, "behavior-a")
    record_coverage(search, result, parent_id=chosen)
    assert search.state.unit_occurrence
    assert all(value == 0 for value in search.state.unit_no_new.values())


def test_multi_generation_feedback_and_selection_are_checkpointed(manifest, tmp_path) -> None:
    inputs = prepare_inputs(manifest, count=4)
    parents = {item.candidate_id: item.case for item in inputs.candidates}
    search = TwoArmSearch(parents, manifest, seed="campaign")
    limits = CampaignLimits(opportunities=2, model_calls=4, tool_calls=4,
                            input_tokens=100, output_tokens=100,
                            wall_clock_seconds=60, expense_units=20)
    checkpoint = CampaignCheckpoint(limits=limits)
    first = search.select(ArmKind.COVERAGE_GUIDED)
    child = search.generate(first)
    result = _result("episode-1", manifest.fixture_id, "new-behavior")
    # The executed case's coverage belongs to the executed case, not the selected parent.
    record_coverage(search, result, parent_id=child.mutation_lineage.generation_identity)
    checkpoint = checkpoint.record_selection(
        first, child_id=child.mutation_lineage.generation_identity
    ).with_search(search.state)
    second = search.select(ArmKind.COVERAGE_GUIDED)
    assert checkpoint.search.ledger.global_seen.behavior
    # The shared schedule moved to the second obligation direction; its first opportunity is
    # also a root restart (`SOC-FBK-08`), so the second draw lands on the risk dimension.
    assert second.selected_direction == "audience-scope"
    assert second.selected_dimension == "risk"
    assert second.root_restart is True
    path = tmp_path / "multi-generation.json"
    save_checkpoint(path, checkpoint)
    assert load_checkpoint(path) == checkpoint


def test_formal_episode_execution_settles_and_feeds_the_next_selection(
    manifest, tmp_path
):
    import test_structured_episode_rehearsal as rehearsal

    inputs = prepare_inputs(manifest, count=4)
    parents = {item.candidate_id: item.case for item in inputs.candidates}
    search = TwoArmSearch(parents, manifest, seed="formal-loop")
    limits = CampaignLimits(opportunities=2, model_calls=8, tool_calls=8,
                            input_tokens=1000, output_tokens=1000,
                            wall_clock_seconds=600, expense_units=100)

    def execute(case):
        envelope, store, _, _ = rehearsal._prepared(manifest, case)
        rendered = render_material(case, manifest)
        s1 = next(slot for slot in rendered.slots if slot.slot_id == "s1")
        tool_name = "read_drive_file" if s1.contents else "read_file"
        arguments = (
            {"file_id": "f-overview"}
            if s1.contents
            else {"path": "/workspace/notes/delivery-notes.md"}
        )
        model = ScriptedModelPort([
            ModelDecision(call_id="model.001", action_request_id="action-0001",
                          tool_name=tool_name, arguments=arguments),
            ModelDecision(call_id="model.002"),
        ])
        return drive_structured_v1_episode(
            envelope, assets=store, tooling=rehearsal._tooling_facts(), model=model,
            clock=rehearsal.FakeClock([0, 1, 60]),
        )

    path = tmp_path / "formal-loop.json"
    state, bundle = run_opportunity(
        path, CampaignCheckpoint(limits=limits), search,
        arm=ArmKind.COVERAGE_GUIDED, manifest=manifest, execute=execute,
        planned=CampaignUsage(model_calls=2, tool_calls=1),
    )
    assert bundle.exposures
    assert state.usage.opportunities == 1
    assert state.search.ledger.global_seen.behavior
    assert state.selections
    assert load_checkpoint(path) == state


def test_receipt_recovery_settles_before_deciding_replay(tmp_path):
    state = CampaignCheckpoint(limits=CampaignLimits(
        opportunities=2, model_calls=3, tool_calls=3, input_tokens=10,
        output_tokens=10, wall_clock_seconds=30, expense_units=10,
    )).reserve("opportunity-0").mark_submitted("opportunity-0")
    state = state.record_receipt("opportunity-0", CampaignUsage(model_calls=1))
    path = tmp_path / "receipt.json"
    save_checkpoint(path, state)
    recovered = load_checkpoint(path).recover(closure_proven=True)
    assert recovered.usage.opportunities == 1
    assert recovered.reservations["opportunity-0"].settled is True


def test_a_seed_without_execution_evidence_has_no_parent_qualification(manifest) -> None:
    """A unit whose earliest witness never executed cannot be edited (`SOC-FBK-13`)."""

    search = TwoArmSearch(
        {item.candidate_id: item.case for item in prepare_inputs(manifest, count=4).candidates},
        manifest,
        seed="no-evidence",
    )
    key = unit_key_string(("behavior-a",))
    witness = sorted(search.parents)[0]
    # Manually register a unit whose witness is a seed with no execution coverage.
    search.state = search.state.model_copy(
        update={
            "unit_dimension": {key: "behavior"},
            "unit_index": {key: (witness,)},
        }
    )
    _at_local_behavior_slot(search, "no-evidence")

    receipt = search.select(ArmKind.COVERAGE_GUIDED)

    assert receipt.root_restart is True
    assert receipt.reason == "no-eligible-parent"


def test_the_frozen_parent_baseline_does_not_drift(manifest) -> None:
    """A later execution of the same parent must not rewrite the selected baseline."""

    search = TwoArmSearch(
        {item.candidate_id: item.case for item in prepare_inputs(manifest, count=4).candidates},
        manifest,
        seed="frozen",
    )
    parent = sorted(search.parents)[0]
    baseline = _result("episode-1", manifest.fixture_id, "behavior-a")
    baseline = record_coverage(search, baseline, parent_id=parent)
    _at_local_behavior_slot(search, "frozen")

    receipt = search.select(ArmKind.COVERAGE_GUIDED)

    assert receipt.root_restart is False
    assert receipt.parent_baseline == baseline

    # A later execution of the same parent updates the archive, but not the frozen snapshot.
    later = _result("episode-2", manifest.fixture_id, "behavior-b")
    later = record_coverage(search, later, parent_id=parent)

    assert search.state.parent_coverage[parent] == later
    assert receipt.parent_baseline == baseline, "the selected baseline must stay frozen"


def test_a_childs_new_coverage_belongs_to_the_child(manifest) -> None:
    """The child's evidence is attributed to the child, and its retention is kept apart."""

    search = TwoArmSearch(
        {item.candidate_id: item.case for item in prepare_inputs(manifest, count=4).candidates},
        manifest,
        seed="belongs",
    )
    parent = sorted(search.parents)[0]
    record_coverage(
        search, _result("episode-1", manifest.fixture_id, "behavior-a"), parent_id=parent
    )
    _at_local_behavior_slot(search, "belongs")

    receipt = search.select(ArmKind.COVERAGE_GUIDED)
    child = search.generate(receipt)
    child_id = child.mutation_lineage.generation_identity
    child_result = _result("episode-child", manifest.fixture_id, "behavior-child")
    child_result = record_coverage(
        search, child_result, parent_id=child_id, parent_baseline=receipt.parent_baseline
    )

    assert search.state.parent_coverage[child_id] == child_result
    assert child_id in search.state.retention
    assert search.state.unit_index[unit_key_string(("behavior-child",))][0] == child_id
