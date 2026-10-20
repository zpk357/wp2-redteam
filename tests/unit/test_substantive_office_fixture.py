"""Contract checks for the substantive office fixture (`SOF-T01`/`T02`/`T03`/`T04`)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sandbox.scenarios.error_capable import (
    TASK_FAMILY_SPECS,
    AttackMode,
    CoverageFeedback,
    EpisodeKind,
    ErrorCapableMode,
    SelectorDecision,
    SelectorRequest,
    TaskFamily,
    build_plan,
    materialize_scenario,
)
from sandbox.scenarios.error_capable_registry import (
    ERROR_CAPABLE_FIXTURE_ID,
    load_error_capable_fixture,
)
from sandbox.scenarios.error_capable_world import LAYOUT_COUNTS, planned_file_ids
from sandbox.scenarios.substantive_office import RECIPE_PREFIX
from sandbox.scenarios.substantive_office.completion import business_completed
from sandbox.scenarios.substantive_office.materials import INSTANCES, instances_for

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "docs" / "reports" / "20261020-substantive-office-fixture-baseline.json"


def _request(seed: int = 11) -> SelectorRequest:
    return SelectorRequest(
        mode=ErrorCapableMode.GUIDED,
        seed=seed,
        agent_model_name="model.check",
        selector_model_name="model.check",
        available_task_families=tuple(TaskFamily),
        available_attacks=tuple(AttackMode),
        feedback=CoverageFeedback(),
    )


def _plan(
    instance_id: str,
    *,
    layout: str = "balanced-9",
    seed: int = 11,
    carrier: str = "message_body",
):
    instance = next(item for item in INSTANCES if item.instance_id == instance_id)
    spec = next(item for item in TASK_FAMILY_SPECS if item.task_family is instance.family)
    episode_id = f"check.{instance_id}"
    ids = planned_file_ids(episode_id, seed=seed, layout_id=layout)
    decision = SelectorDecision(
        task_family=instance.family,
        path_id=spec.path_ids[0],
        attack_mode=AttackMode.CROSS_FILE_SPLICE,
        attack_carrier=carrier,
        layout_id=layout,
        episode_kind=EpisodeKind.ATTACK,
        rationale="fixture contract check",
    )
    return build_plan(
        _request(seed),
        decision,
        episode_id=episode_id,
        task_file_id=ids[0],
        file_ids=ids,
        attack_carrier=carrier,
        model_name="model.check",
        layout_id=layout,
    ).model_copy(update={"material_recipe": f"{RECIPE_PREFIX}{instance_id}.a"})


def test_the_fixture_has_its_own_identity_and_menu() -> None:
    fixture = load_error_capable_fixture("substantive-office-01")

    assert fixture.fixture_id == "substantive-office-01"
    assert fixture.fixture_version == "1.0.0"
    assert fixture.manifest.material_instances == tuple(item.instance_id for item in INSTANCES)
    assert fixture.manifest.attack_phrasings == ("a", "b")
    historical = load_error_capable_fixture(ERROR_CAPABLE_FIXTURE_ID)
    assert fixture.freeze_digest != historical.freeze_digest


def test_each_family_has_two_business_instances() -> None:
    for family in TaskFamily:
        assert len(instances_for(family)) == 2
        assert len({item.instance_id for item in instances_for(family)}) == 2


@pytest.mark.parametrize("instance", INSTANCES, ids=lambda item: item.instance_id)
def test_material_is_deterministic_and_the_attack_replaces_one_sentence(instance) -> None:
    plan = _plan(instance.instance_id)
    first = materialize_scenario(plan)
    second = materialize_scenario(plan)

    assert first.materialization_digest == second.materialization_digest
    carrier = next(item for item in first.files if item.file_id == plan.attack_carrier_file_id)
    assert "导出对照" in carrier.body
    for other in first.files:
        if other.file_id != carrier.file_id:
            assert "导出对照" not in other.body


def test_the_restricted_value_stays_in_the_restricted_files() -> None:
    plan = _plan("summary.north-yard")
    material = materialize_scenario(plan)
    restricted = next(
        fact.canonical_value
        for fact in material.facts
        if fact.fact_id.endswith("restricted-target")
    )
    public = next(
        fact.canonical_value for fact in material.facts if fact.fact_id.endswith("public-reference")
    )

    bodies = {item.file_id: item.body for item in material.files}
    task = bodies[plan.file_ids[0]]
    overview = bodies[plan.file_ids[2]]
    assert restricted not in task and restricted not in overview
    assert public in overview
    assert restricted in bodies[plan.file_ids[5]]


def test_layouts_materialise_nine_eleven_and_thirteen_files() -> None:
    for layout, count in LAYOUT_COUNTS.items():
        plan = _plan("calendar.north-yard", layout=layout)
        material = materialize_scenario(plan)
        assert len(material.files) == count
        assert tuple(item.role for item in material.files) == plan.file_roles


def test_the_historical_fixture_digests_did_not_move() -> None:
    expected = json.loads(BASELINE.read_text(encoding="utf-8"))
    fixture = load_error_capable_fixture(ERROR_CAPABLE_FIXTURE_ID)

    assert fixture.manifest.manifest_digest == expected["manifest_digest"]
    assert fixture.freeze_digest == expected["freeze_digest"]
    assert fixture.manifest.world_digest == expected["world_digest"]


def test_business_completion_reads_the_declared_fields() -> None:
    plan = _plan("summary.north-yard")
    instance = next(item for item in INSTANCES if item.instance_id == "summary.north-yard")

    def step(payload: str, audience: tuple[str, ...] = ("partner.contact",)) -> SimpleNamespace:
        committed = SimpleNamespace(
            payload=payload,
            payload_established=True,
            audience=audience,
            is_delivery=lambda: bool(audience),
        )
        return SimpleNamespace(committed=committed, request=SimpleNamespace(arguments={}))

    good = SimpleNamespace(steps=[step(f"{instance.public_ref} | {instance.handover} | 交接完成")])
    missing_date = SimpleNamespace(steps=[step(instance.public_ref)])

    assert business_completed(plan, good) is True
    assert business_completed(plan, missing_date) is False


def test_a_recipe_with_an_unknown_instance_is_refused() -> None:
    plan = _plan("summary.north-yard")
    bad = plan.model_copy(update={"material_recipe": f"{RECIPE_PREFIX}summary.unknown.a"})

    with pytest.raises(ValueError, match="unregistered instance"):
        materialize_scenario(bad)
