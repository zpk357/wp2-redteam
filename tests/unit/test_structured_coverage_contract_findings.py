"""Counterexamples for the coverage contract findings C1-C3 (TASK §20).

Each test here fails against the code as it stood before the fix: a read of unrestricted content
used to be recorded as ``read_restricted``, two authorization relations used to collapse into
one risk key, and duplicate units used to lose their evidence references.
"""

from __future__ import annotations

from types import SimpleNamespace

from structured_coverage_helpers import record_coverage

from sandbox.structured_v1.campaign import CampaignCheckpoint, CampaignLimits
from sandbox.structured_v1.coverage import (
    POLICY_CLASS_UNKNOWN,
    BehaviorAtom,
    CoverageResult,
    CoverageSeen,
    JointRelation,
    RiskAtom,
    RiskEventKind,
    _effect_risk_facts,
    _exposure_risk_facts,
    _unique_atoms,
    extract_coverage,
    parent_child_retention,
    unit_key_string,
)
from sandbox.structured_v1.evidence import (
    DeliveryChannel,
    DeliveryEffect,
    DeliveryRelation,
)
from sandbox.structured_v1.experiment_inputs import prepare_inputs
from sandbox.structured_v1.fixture import DataPolicyClass, RegisteredFile, RegisteredUnit
from sandbox.structured_v1.redaction import registered_file_digest
from sandbox.structured_v1.search import ArmKind, TwoArmSearch


def _bundle(manifest, root_case):
    import test_structured_episode_rehearsal as rehearsal

    bundle, _ = rehearsal._run(manifest, root_case, rehearsal._decisions())
    return bundle


def _effect(**overrides) -> DeliveryEffect:
    fields: dict[str, object] = {
        "effect_id": "effect.000001",
        "sequence": 1,
        "channel": DeliveryChannel.MESSAGE,
        "committed": True,
        "blocked": False,
        "content_digest": "sha256:" + "a" * 64,
        "registered_units": (),
        "registered_files": (),
        "created_objects": (),
        "audience": (),
        "proof_digest": "sha256:" + "b" * 64,
    }
    fields.update(overrides)
    return DeliveryEffect(**fields)


def test_a_read_without_registered_content_is_not_a_restricted_read(manifest) -> None:
    """C1: the old code emitted READ_RESTRICTED for every exposure, restricted or not."""

    bundle = SimpleNamespace(
        material=SimpleNamespace(
            slots=[SimpleNamespace(slot_id="s1", contents=["public summary text"])]
        )
    )
    exposure = SimpleNamespace(
        fact_id="fact.000001", material=SimpleNamespace(slot_id="s1"), principal=None
    )

    facts, unknown = _exposure_risk_facts(exposure, bundle, manifest)

    assert facts == []
    assert unknown == []


def test_a_read_of_registered_content_carries_its_actual_class(manifest) -> None:
    unit = manifest.registered_units[0]
    bundle = SimpleNamespace(
        material=SimpleNamespace(
            slots=[SimpleNamespace(slot_id="s1", contents=[f"note: {unit.value}"])]
        )
    )
    exposure = SimpleNamespace(
        fact_id="fact.000001", material=SimpleNamespace(slot_id="s1"), principal=None
    )

    facts, unknown = _exposure_risk_facts(exposure, bundle, manifest)

    assert unknown == []
    assert [item.event_kind for item in facts] == [RiskEventKind.READ_RESTRICTED]
    assert facts[0].key[1] == unit.policy_class.value
    assert len(facts[0].key) == 6


def test_a_registered_public_unit_does_not_count_as_a_restricted_read(manifest) -> None:
    public = RegisteredUnit(
        unit_id="public-summary", value="public summary text", policy_class=DataPolicyClass.PUBLIC
    )
    mixed_manifest = manifest.model_copy(update={
        "registered_units": (*manifest.registered_units, public)
    })
    bundle = SimpleNamespace(material=SimpleNamespace(slots=[
        SimpleNamespace(slot_id="s1", contents=[public.value])
    ]))
    exposure = SimpleNamespace(
        fact_id="fact.public", material=SimpleNamespace(slot_id="s1"), principal=None
    )

    facts, unknown = _exposure_risk_facts(exposure, bundle, mixed_manifest)

    assert facts == []
    assert unknown == []


def test_a_registered_public_file_does_not_count_as_a_restricted_read(manifest) -> None:
    content = "public summary text"
    public = RegisteredFile(
        file_id="public-file", content_digest=registered_file_digest(content),
        policy_class=DataPolicyClass.PUBLIC,
    )
    mixed_manifest = manifest.model_copy(update={
        "registered_files": (*manifest.registered_files, public)
    })
    bundle = SimpleNamespace(material=SimpleNamespace(slots=[
        SimpleNamespace(slot_id="s1", contents=[content])
    ]))
    exposure = SimpleNamespace(
        fact_id="fact.public-file", material=SimpleNamespace(slot_id="s1"), principal=None
    )

    facts, unknown = _exposure_risk_facts(exposure, bundle, mixed_manifest)

    assert facts == []
    assert unknown == []


def test_a_public_and_restricted_mix_retains_only_the_restricted_read(manifest) -> None:
    restricted = manifest.registered_units[0]
    public = RegisteredUnit(
        unit_id="public-summary", value="public summary text", policy_class=DataPolicyClass.PUBLIC
    )
    mixed_manifest = manifest.model_copy(update={
        "registered_units": (*manifest.registered_units, public)
    })
    bundle = SimpleNamespace(material=SimpleNamespace(slots=[
        SimpleNamespace(slot_id="s1", contents=[public.value, restricted.value])
    ]))
    exposure = SimpleNamespace(
        fact_id="fact.mixed", material=SimpleNamespace(slot_id="s1"), principal=None
    )

    facts, unknown = _exposure_risk_facts(exposure, bundle, mixed_manifest)

    assert unknown == []
    assert [item.key[1] for item in facts] == [restricted.policy_class.value]


def test_every_restricted_read_is_backed_by_the_material_actually_read(
    manifest, root_case
) -> None:
    bundle = _bundle(manifest, root_case)
    coverage = extract_coverage(bundle, manifest=manifest)
    slots = "\n".join("\n".join(slot.contents) for slot in bundle.material.slots)
    present = {
        unit.policy_class.value for unit in manifest.registered_units if unit.value in slots
    }

    for atom in coverage.risk:
        if atom.event_kind is RiskEventKind.READ_RESTRICTED:
            assert atom.key[1] in present, "a restricted read must name the class it read"


def test_multi_principal_effects_keep_the_authorization_dimensions_separate(manifest) -> None:
    """C2: same channel, same class, different authorization must not collapse into one key."""

    unit = manifest.registered_units[0]
    actor = manifest.actor_id or manifest.task_authorized[0]
    outside = next(
        principal
        for principal in ("partner.contact", "stranger.example")
        if principal not in manifest.task_authorized
    )
    effect = _effect(
        registered_units=(unit.unit_id,),
        audience=(
            DeliveryRelation(principal=actor, readable=True),
            DeliveryRelation(principal=outside, readable=True),
        ),
    )

    facts, unknown = _effect_risk_facts(effect, manifest)

    assert unknown == []
    keys = {item.key for item in facts}
    assert len(keys) == len(facts), "each (class, principal, obligation) is its own unit"
    assert all(len(key) == 6 for key in keys)
    recipients = {key[2] for key in keys}
    audiences = {key[3] for key in keys}
    assert "task_authorized" in recipients and "task_unauthorized" in recipients
    assert "allowed" in audiences and "forbidden" in audiences
    assert {key[4] for key in keys} == {RiskEventKind.COMMITTED.value}


def test_an_unknown_content_class_is_recorded_but_not_admitted(manifest) -> None:
    effect = _effect(registered_units=("u-not-registered",))

    facts, unknown = _effect_risk_facts(effect, manifest)

    assert facts == [], "an unknown class must not become a new state"
    assert any(POLICY_CLASS_UNKNOWN in item for item in unknown)


def test_duplicate_units_keep_every_evidence_reference() -> None:
    """C3: the old dedupe kept the first atom and dropped every other reference."""

    first = BehaviorAtom(key=("a", "b", "c", "d"), evidence_refs=("witness.one",))
    second = BehaviorAtom(key=("a", "b", "c", "d"), evidence_refs=("witness.two",))

    merged = _unique_atoms([first, second])

    assert len(merged) == 1
    assert merged[0].evidence_refs == ("witness.one", "witness.two")


def test_seen_keeps_evidence_beside_the_novelty_keys() -> None:
    atom = BehaviorAtom(key=("a",), evidence_refs=("witness.one",))
    result = CoverageResult(episode_id="episode-1", fixture_id="fixture-1", behavior=(atom,))

    seen = CoverageSeen().add(result)

    assert atom.key in seen.behavior
    assert seen.evidence[unit_key_string(atom.key)] == ("witness.one",)


def test_parent_child_retention_is_not_100_when_the_parent_has_no_risk_events() -> None:
    """SOC-FBK-13: an empty parent risk set is not reported as a 100% retention."""

    parent = CoverageResult(episode_id="parent", fixture_id="fixture-1")
    child = CoverageResult(episode_id="child", fixture_id="fixture-1")

    report = parent_child_retention(parent, child)

    assert report.risk_retention is None
    assert report.behavior_retention is None


def test_parent_child_retention_counts_kept_and_new_events() -> None:
    kept = (
        "data-release", "limited-audience", "task_unauthorized",
        "forbidden", "committed", "not-enforced",
    )
    dropped = (
        "data-release", "actor-private", "task_authorized", "allowed",
        "committed", "none",
    )
    parent = CoverageResult(
        episode_id="parent",
        fixture_id="fixture-1",
        risk=(
            RiskAtom(key=kept, event_kind=RiskEventKind.COMMITTED),
            RiskAtom(key=dropped, event_kind=RiskEventKind.COMMITTED),
        ),
        behavior=(BehaviorAtom(key=("read", "observe", "read", "material"),),),
    )
    child = CoverageResult(
        episode_id="child",
        fixture_id="fixture-1",
        risk=(RiskAtom(key=kept, event_kind=RiskEventKind.COMMITTED),),
        behavior=(BehaviorAtom(key=("read", "observe", "read", "material"),),),
    )

    report = parent_child_retention(parent, child)

    assert report.parent_risk_count == 2
    assert report.risk_retention == 0.5
    assert report.behavior_retention == 1.0
    assert report.new_risk == 0
    assert report.new_behavior == 0


def test_explicit_source_edge_atoms_are_well_formed(manifest, root_case) -> None:
    bundle = _bundle(manifest, root_case)
    coverage = extract_coverage(bundle, manifest=manifest)
    risk_keys = {atom.key for atom in coverage.risk}
    behavior_keys = {atom.key for atom in coverage.behavior}

    for atom in coverage.joint:
        if atom.relation is JointRelation.EXPLICIT_SOURCE_EDGE:
            assert atom.risk_key in risk_keys
            assert atom.behavior_key in behavior_keys


def test_representative_archive_keeps_the_earliest_and_the_latest_witness(manifest) -> None:
    """C3: the old archive kept the last two, dropping the earliest witness (`SOC-FBK-10`)."""

    inputs = prepare_inputs(manifest, count=4)
    parents = {item.candidate_id: item.case for item in inputs.candidates}
    search = TwoArmSearch(parents, manifest, seed="archive-witnesses")
    names = sorted(parents)[:3]

    for index, name in enumerate(names):
        record_coverage(search,
            CoverageResult(
                episode_id=f"episode-{index}",
                fixture_id=manifest.fixture_id,
                behavior=(BehaviorAtom(key=(f"behavior-{index}",)),),
            ),
            parent_id=name,
        )

    assert search.state.archive["behavior"] == (names[0], names[-1])


def test_guided_selection_still_runs_after_a_recorded_opportunity(manifest) -> None:
    """The multi-generation path keeps working with the scoped ledger and the new archive."""

    inputs = prepare_inputs(manifest, count=4)
    parents = {item.candidate_id: item.case for item in inputs.candidates}
    search = TwoArmSearch(parents, manifest, seed="scoped")
    chosen = sorted(parents)[-1]
    record_coverage(search,
        CoverageResult(
            episode_id="episode-1",
            fixture_id=manifest.fixture_id,
            behavior=(BehaviorAtom(key=("new-behavior",)),),
        ),
        parent_id=chosen,
    )

    receipt = search.select(ArmKind.COVERAGE_GUIDED)

    assert receipt.parent_id in parents
    assert search.state.last_application is not None
    assert search.state.last_application.local
    assert CampaignCheckpoint(limits=CampaignLimits(
        opportunities=1, model_calls=1, tool_calls=1, input_tokens=1,
        output_tokens=1, wall_clock_seconds=1, expense_units=1,
    )).with_search(search.state).search.ledger == search.state.ledger
