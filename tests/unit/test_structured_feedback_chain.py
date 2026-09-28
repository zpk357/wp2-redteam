"""SOC-FIX-23: real Office tools, scripted model, no server or model requests."""
import pytest
import test_structured_episode_rehearsal as rehearsal
from structured_coverage_helpers import TEST_CONFIG, record_coverage
from test_structured_two_arm_search import _at_local_behavior_slot, _result

from sandbox.structured_v1.campaign import (
    CampaignCheckpoint,
    CampaignLimits,
    CampaignUsage,
    load_checkpoint,
    run_opportunity,
)
from sandbox.structured_v1.coverage import (
    JointRelation,
    bind_coverage_execution,
    extract_coverage,
    parent_child_retention,
)
from sandbox.structured_v1.drivers import ScriptedModelPort, drive_structured_v1_episode
from sandbox.structured_v1.experiment_inputs import prepare_inputs
from sandbox.structured_v1.model import ModelDecision
from sandbox.structured_v1.models import sha256_digest
from sandbox.structured_v1.search import ArmKind, TwoArmSearch
from sandbox.structured_v1.submission import build_envelope


def _execute(manifest, case, *, reverse=False, named=True, changed=False):
    envelope, store, body, _ = rehearsal._prepared(manifest, case)
    envelope = build_envelope(
        case, manifest=manifest, base_world=rehearsal._base_world(),
        overlay=envelope.overlay, budget=envelope.budget, tooling=envelope.tooling,
        episode_id=f"episode-{case.mutation_lineage.generation_identity}",
        arm_id="offline-chain",
    ).envelope
    read = ("read_drive_file", {"file_id": "f-overview"})
    args = {"to": ["partner.contact"], "subject": "Public summary",
            "body": body + (" changed" if changed else "")}
    if named:
        args["related_refs"] = [{"kind": "drive_file", "resource_id": "f-overview"}]
    send = ("send_email", args)
    actions = [send, read] if reverse else [read, send]
    decisions = [ModelDecision(call_id=f"m-{i}", action_request_id=f"a-{i}",
                               tool_name=name, arguments=arguments)
                 for i, (name, arguments) in enumerate(actions)]
    decisions.append(ModelDecision(call_id="m-stop"))
    return drive_structured_v1_episode(
        envelope, assets=store, tooling=rehearsal._tooling_facts(),
        model=ScriptedModelPort(decisions), clock=rehearsal.FakeClock([0, 1, 2, 60, 120]),
    )


@pytest.mark.parametrize("reverse,named,changed,expected", [
    (False, True, False, True), (True, True, False, False),
    (False, False, False, False), (False, True, True, False),
])
def test_real_source_witness_requires_prior_read_reference_and_exact_body(
    manifest, root_case, reverse, named, changed, expected,
):
    bundle = _execute(manifest, root_case, reverse=reverse, named=named, changed=changed)
    assert bundle.exposures, "must exercise a real bound read"
    assert any(e.committed and e.audience for r in bundle.records for e in r.effects)
    coverage = extract_coverage(bundle, manifest=manifest)
    edges = [j for j in coverage.joint if j.relation == JointRelation.EXPLICIT_SOURCE_EDGE]
    assert bool(edges) is expected
    assert coverage.behavior and coverage.risk, "missing linkage must not erase other facts"


def _search(manifest):
    return TwoArmSearch(
        {c.candidate_id: c.case for c in prepare_inputs(manifest, count=4).candidates},
        manifest, seed="chain",
    )


@pytest.mark.parametrize("earliest_state", ["cool", "unbound", "more-opportunities"])
def test_recent_eligible_representative_wins(manifest, earliest_state):
    search = _search(manifest)
    first, recent = sorted(search.parents)[:2]
    for candidate in (first, recent):
        record_coverage(search, _result(candidate, manifest.fixture_id, "shared"),
                        parent_id=candidate)
    if earliest_state == "cool":
        search.state = search.state.model_copy(update={"cooldown": {first: 10}})
    elif earliest_state == "unbound":
        coverage = dict(search.state.parent_coverage)
        coverage.pop(first)
        search.state = search.state.model_copy(update={"parent_coverage": coverage})
    else:
        search.state = search.state.model_copy(update={"occurrence": {first: 8, recent: 1}})
    _at_local_behavior_slot(search, "chain")
    receipt = search.select(ArmKind.COVERAGE_GUIDED)
    assert not receipt.root_restart
    assert receipt.parent_id == recent
    assert receipt.cooldown == 0


@pytest.mark.parametrize("field,value", [
    ("candidate_id", "wrong-candidate"), ("input_digest", sha256_digest("wrong-input")),
    ("manifest_digest", sha256_digest("wrong-manifest")),
    ("material_digest", sha256_digest("wrong-material")),
    ("execution_config_digest", sha256_digest("wrong-config")),
    ("coverage_version", "wrong-version"),
])
def test_identity_mismatch_never_awards_coverage(manifest, field, value):
    search = _search(manifest)
    candidate = sorted(search.parents)[0]
    bound = record_coverage(search, _result("first", manifest.fixture_id, "old"),
                            parent_id=candidate)
    bad = bound.model_copy(update={"execution": bound.execution.model_copy(update={field: value})})
    before = search.state
    with pytest.raises(ValueError, match="identity mismatch"):
        search.record(bad, parent_id=candidate)
    assert search.state == before
    _at_local_behavior_slot(search, "chain")
    selection = search.select(ArmKind.COVERAGE_GUIDED)
    with pytest.raises(ValueError, match="identity mismatch"):
        search.generate(selection.model_copy(update={"parent_baseline": bad}))


def test_unbound_legacy_coverage_does_not_qualify_as_parent(manifest):
    search = _search(manifest)
    candidate = sorted(search.parents)[0]
    search.record(_result("legacy", manifest.fixture_id, "old"), parent_id=candidate)
    _at_local_behavior_slot(search, "chain")
    assert search.select(ArmKind.COVERAGE_GUIDED).reason == "no-eligible-parent"


def test_actual_parent_to_child_chain_and_checkpoint(manifest, root_case, tmp_path):
    parent_id = root_case.mutation_lineage.generation_identity
    search = TwoArmSearch({parent_id: root_case}, manifest, seed="chain")
    bundle = _execute(manifest, root_case)
    baseline = bind_coverage_execution(
        extract_coverage(bundle, manifest=manifest), bundle=bundle, case=root_case,
        manifest=manifest, execution_config_digest=TEST_CONFIG,
    )
    search.record(baseline, parent_id=parent_id)
    _at_local_behavior_slot(search, "chain")
    checkpoint = CampaignCheckpoint(limits=CampaignLimits(
        opportunities=2, model_calls=20, tool_calls=20, input_tokens=10000,
        output_tokens=10000, wall_clock_seconds=1000, expense_units=1000,
    ), search=search.state)
    path = tmp_path / "chain.json"
    state, child_bundle = run_opportunity(
        path, checkpoint, search, arm=ArmKind.COVERAGE_GUIDED, manifest=manifest,
        execute=lambda case: _execute(manifest, case),
        execution_config=lambda bundle: TEST_CONFIG,
        planned=CampaignUsage(model_calls=3, tool_calls=2),
    )
    receipt = state.selections[0]
    assert not receipt.root_restart and receipt.parent_baseline == baseline
    child_id = next(iter(state.lineage))
    child_coverage = state.search.parent_coverage[child_id]
    assert child_bundle.episode_id != bundle.episode_id
    assert child_id != parent_id
    assert child_coverage.execution.candidate_id == child_id
    assert child_coverage.execution.bundle_digest == child_bundle.bundle_digest
    assert state.search.retention[child_id] == parent_child_retention(baseline, child_coverage)
    assert state.search.parent_coverage[parent_id] == baseline
    assert state.usage.model_calls == child_bundle.usage.model_calls
    assert load_checkpoint(path) == state

    # A different execution configuration cannot earn coverage, but the run is still billed.
    _at_local_behavior_slot(search, "chain")
    before = search.state.ledger
    with pytest.raises(ValueError, match="identity mismatch"):
        run_opportunity(
            path, state, search, arm=ArmKind.RANDOM_INDEPENDENT, manifest=manifest,
            execute=lambda case: _execute(manifest, case),
            execution_config=lambda bundle: sha256_digest("different-model"),
            planned=CampaignUsage(model_calls=3, tool_calls=2),
        )
    failed = load_checkpoint(path)
    assert failed.stopped and failed.stop_reason == "execution-identity-mismatch"
    assert failed.usage.opportunities == 2
    assert failed.usage.model_calls == state.usage.model_calls + child_bundle.usage.model_calls
    assert search.state.ledger == before


def test_tied_representative_selection_replays_from_frozen_state(manifest):
    search = _search(manifest)
    for candidate in sorted(search.parents)[:2]:
        record_coverage(search, _result(candidate, manifest.fixture_id, "shared"),
                        parent_id=candidate)
    _at_local_behavior_slot(search, "chain")
    before = search.state
    first = search.select(ArmKind.COVERAGE_GUIDED)
    search.state = before
    assert search.select(ArmKind.COVERAGE_GUIDED) == first


def test_parent_lineage_mismatch_rejected_before_settlement(manifest):
    search = _search(manifest)
    first, unrelated = sorted(search.parents)[:2]
    baseline = record_coverage(search, _result("first", manifest.fixture_id, "shared"),
                               parent_id=first)
    other = record_coverage(search, _result("other", manifest.fixture_id, "other"),
                           parent_id=unrelated)
    before = search.state
    with pytest.raises(ValueError, match="parent-child execution identity mismatch"):
        search.record(other, parent_id=unrelated, parent_baseline=baseline)
    assert search.state == before


async def test_host_config_binds_actual_request_and_finalized_image_offline(monkeypatch):
    """Substitute scheduling only: this does not claim a container was executed."""
    from types import SimpleNamespace

    import test_structured_host_runner_probes as probes

    from sandbox.protocol import ModelOptions, ModelProvider
    from sandbox.structured_v1.envelope import StructuredEnvelope
    from sandbox.structured_v1.host_runner import HostProbes, StructuredHostRunner

    fixture, _ = probes._normal_control_envelope()
    case = prepare_inputs(fixture.manifest, count=4).candidates[0].case

    class Scheduler(probes._FakeScheduler):
        async def create(self, episode_id, image, limits):
            return probes._handle().model_copy(update={"execution_id": episode_id})

        async def wait_until_ready(self, handle):
            pass

    async def drive(self, handle, request):
        envelope = StructuredEnvelope.model_validate(request.structured_case_execution["envelope"])
        return probes._offline_bundle(SimpleNamespace(envelope=envelope)), 7

    monkeypatch.setattr(StructuredHostRunner, "_drive", drive)
    scheduler = Scheduler()
    runner = StructuredHostRunner(
        scheduler=scheduler, runtime=probes._UnusedRuntime(), manifest=fixture.manifest,
        base_world=fixture.base_world, overlay=fixture.overlay, image="structured-v1:test",
        model=ModelOptions(provider=ModelProvider.FAKE, model_name="scripted"),
        default_budget=probes._budget(), probes=HostProbes(
            container_absent=lambda _: scheduler.destroyed,
            no_post_bundle_activity=lambda *args: True,
            isolation_confirmed=lambda _: scheduler.destroyed,
            inspect=probes._fake_inspect, source="test-substitute",
        ),
    )
    async def run(episode_id):
        return await runner._run_case(case, episode_id=episode_id, budget=probes._budget(),
                                      generation=0, attempt=0, seed=1)

    first = await run("episode-1")
    second = await run("episode-2")
    digest = runner.execution_config_digest(first.container_bundle)
    assert runner.execution_config_digest(second.container_bundle) == digest
    assert first.runtime_receipt.image_digest == probes.IMAGE_DIGEST
    runner.model = runner.model.model_copy(update={"model_name": "other-model"})
    third = await run("episode-3")
    assert runner.execution_config_digest(third.container_bundle) != digest
    wrong = first.container_bundle.model_copy(update={"bundle_digest": sha256_digest("wrong")})
    with pytest.raises(ValueError, match="different bundle"):
        runner.execution_config_digest(wrong)


def test_two_direction_roots_have_distinct_execution_identities(manifest):
    search = _search(manifest)
    first = search.generate(search.select(ArmKind.COVERAGE_GUIDED))
    second = search.generate(search.select(ArmKind.COVERAGE_GUIDED))
    assert first.mutation_lineage.generation_identity != second.mutation_lineage.generation_identity
    assert search.parents[first.mutation_lineage.generation_identity] == first


def test_root_schedule_still_applies_after_first_block(manifest):
    search = _search(manifest)
    candidate = sorted(search.parents)[0]
    record_coverage(search, _result("first", manifest.fixture_id, "shared"), parent_id=candidate)
    index = next(i for i in range(5, 100) if i % 4 == 1
                 and i % 5 in search._root_slots("chain:data-release", i // 5))
    search.state = search.state.model_copy(
        update={"direction_opportunities": {"data-release": index}}
    )
    assert search.select(ArmKind.COVERAGE_GUIDED).reason == "scheduled-root-restart"
