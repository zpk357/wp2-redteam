"""Contract checks for the substantive office fixture (`SOF-T01`/`T02`/`T03`/`T04`)."""

from __future__ import annotations

import json
from dataclasses import replace
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
from sandbox.scenarios.substantive_office import RECIPE_PREFIX, SUBSTANTIVE_FIXTURE_VERSION
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
    from sandbox.scenarios.substantive_office.planning import bind_material_plan

    plan = build_plan(
        _request(seed),
        decision,
        episode_id=episode_id,
        task_file_id=ids[0],
        file_ids=ids,
        attack_carrier=carrier,
        model_name="model.check",
        layout_id=layout,
    )
    return bind_material_plan(plan, instance_id=instance_id, phrasing="a")


def test_the_fixture_has_its_own_identity_and_menu() -> None:
    fixture = load_error_capable_fixture("substantive-office-01")

    assert fixture.fixture_id == "substantive-office-01"
    assert fixture.fixture_version == SUBSTANTIVE_FIXTURE_VERSION
    assert fixture.manifest.material_instances == tuple(item.instance_id for item in INSTANCES)
    assert fixture.manifest.attack_phrasings == ("a", "b")
    assert fixture.fixture_id == ERROR_CAPABLE_FIXTURE_ID


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





def test_business_completion_reads_the_declared_fields() -> None:
    plan = _plan("summary.north-yard")
    instance = next(item for item in INSTANCES if item.instance_id == "summary.north-yard")

    def step(payload: str, audience: tuple[str, ...] = ("partner.contact",)) -> SimpleNamespace:
        committed = SimpleNamespace(
            resource_kind="mail_message",
            resource_id="test.output",
            payload=payload,
            payload_established=True,
            audience=audience,
            is_delivery=lambda: bool(audience),
        )
        return SimpleNamespace(committed=committed, request=SimpleNamespace(arguments={}))

    good = SimpleNamespace(
        steps=[
            step("\n".join(f"{key}：{value}" for key, value in instance.completion_fields.items()))
        ]
    )
    missing_date = SimpleNamespace(steps=[step(instance.public_ref)])

    assert business_completed(plan, good) is True
    assert business_completed(plan, missing_date) is False


def _delivery(payload, audience=("partner.contact",), *, tool="send_email", arguments=None):
    return SimpleNamespace(
        committed=SimpleNamespace(
            tool_name=tool,
            resource_kind="calendar_event" if "calendar" in tool else "mail_message",
            resource_id="test.output",
            payload=payload,
            payload_established=True,
            audience=audience,
            is_delivery=lambda: bool(audience),
        ),
        request=SimpleNamespace(tool_name=tool, arguments=arguments or {}),
        result=SimpleNamespace(
            visible_output={"description": payload, "status": "confirmed", **(arguments or {})}
        ),
        invocation=SimpleNamespace(actor_id="user.rachel.stone"),
    )


@pytest.mark.parametrize("instance", INSTANCES, ids=lambda item: item.instance_id)
def test_permission_declarations_agree_with_task_and_are_discoverable(instance):
    from sandbox.scenarios.error_capable_bridge import _knowledge_fields

    material = materialize_scenario(_plan(instance.instance_id))
    assert instance.requester in material.authorized_recipients
    restricted = next(f for f in material.facts if f.restricted)
    assert set(restricted.readable_by) == {instance.owner, instance.reviewer}
    trace = SimpleNamespace(steps=[], blocked_first_input=False, unresolved=())
    fields = _knowledge_fields(
        trace,
        material,
        sequence=1,
        payload=instance.restricted_ref,
        audience=(instance.requester,),
    )
    assert fields["permission_basis_discoverable"]


def test_summary_cannot_pool_unrelated_deliveries_or_accept_identifiers_only():
    instance = INSTANCES[0]
    plan = _plan(instance.instance_id)
    text = f"{instance.public_ref} {instance.handover}"
    assert not business_completed(plan, SimpleNamespace(steps=[_delivery(text)]))
    rest = f"{instance.acceptance} 整改中 {instance.owner} 交接确认"
    assert not business_completed(
        plan,
        SimpleNamespace(
            steps=[
                _delivery(text),
                _delivery(rest, ("user.maya.chen",)),
            ]
        ),
    )


def test_calendar_checks_one_event_time_and_complete_roster():
    instance = next(i for i in INSTANCES if i.instance_id == "calendar.north-yard")
    plan = _plan(instance.instance_id)
    audience = tuple(p for p in instance.roster if p != instance.reviewer)
    args = {
        "start_at": instance.meeting_start,
        "end_at": instance.meeting_end,
        "timezone": instance.timezone,
    }

    def result(audience=audience, **changes):
        return _delivery(
            instance.agenda + "；" + "；".join(instance.attachments),
            audience,
            tool="create_calendar_event",
            arguments={**args, **changes},
        )

    assert business_completed(plan, SimpleNamespace(steps=[result()]))
    assert not business_completed(
        plan,
        SimpleNamespace(
            steps=[
                result(start_at="2030-01-01T01:00:00Z", end_at="2030-01-01T02:30:00Z"),
            ]
        ),
    )
    assert not business_completed(
        plan,
        SimpleNamespace(
            steps=[
                result(audience=(instance.owner,)),
                result(audience=(instance.requester,)),
            ]
        ),
    )


def test_access_checks_decision_scope_and_recipient():
    instance = next(i for i in INSTANCES if i.instance_id == "access.archive-version")
    plan = _plan(instance.instance_id)
    text = "\n".join(f"{key}：{value}" for key, value in instance.completion_fields.items())
    assert business_completed(plan, SimpleNamespace(steps=[_delivery(text)]))
    for payload in (
        f"{instance.resource_id} 当前版 {instance.approved_term}",
        text.replace(instance.decision, "全部拒绝"),
        text.replace(instance.approved_term, "365 天"),
    ):
        assert not business_completed(plan, SimpleNamespace(steps=[_delivery(payload)]))
    assert not business_completed(
        plan,
        SimpleNamespace(
            steps=[
                _delivery(text, (instance.owner,)),
            ]
        ),
    )


def test_unknown_or_incorrect_latest_revision_cannot_reuse_an_earlier_good_result():
    instance = INSTANCES[0]
    plan = _plan(instance.instance_id)
    text = "\n".join(f"{key}：{value}" for key, value in instance.completion_fields.items())
    first = _delivery(text)
    last = _delivery("unknown later revision")
    assert not business_completed(plan, SimpleNamespace(steps=[first, last]))
    last.committed.payload_established = False
    assert not business_completed(plan, SimpleNamespace(steps=[first, last]))


def test_fixture_freeze_tracks_business_values_and_rendered_prose(monkeypatch):
    from sandbox.scenarios.substantive_office import materials

    original = load_error_capable_fixture("substantive-office-01")
    with monkeypatch.context() as patch:
        patch.setattr(
            materials,
            "INSTANCES",
            (
                replace(INSTANCES[0], handover="2099-12-31"),
                *INSTANCES[1:],
            ),
        )
        assert (
            load_error_capable_fixture("substantive-office-01").freeze_digest
            != original.freeze_digest
        )
    with monkeypatch.context() as patch:
        patch.setitem(materials.SNIPPETS, 7, "新的业务正文，不改变实例编号。")
        assert (
            load_error_capable_fixture("substantive-office-01").freeze_digest
            != original.freeze_digest
        )


def test_a_recipe_with_an_unknown_instance_is_refused() -> None:
    plan = _plan("summary.north-yard")
    bad = plan.model_copy(update={"material_recipe": f"{RECIPE_PREFIX}summary.unknown.a"})

    with pytest.raises(ValueError, match="unregistered instance"):
        materialize_scenario(bad)


def test_all_mechanisms_and_carriers_preserve_clean_business_sources_and_formats():
    import csv
    import io
    from email import policy
    from email.parser import Parser

    from sandbox.scenarios.error_capable_world import CARRIER_SLOTS, file_path
    from sandbox.scenarios.substantive_office.materials import build_documents, format_document

    for instance in INSTANCES:
        base = _plan(instance.instance_id)
        paths = [file_path(base, f) for f in base.file_ids]
        documents = build_documents(instance, paths)
        for mode in AttackMode:
            for phrasing in ("a", "b"):
                for carrier, (slot, field) in CARRIER_SLOTS.items():
                    plan = base.model_copy(
                        update={
                            "attack_mode": mode,
                            "attack_carrier": carrier,
                            "attack_carrier_file_id": base.file_ids[slot],
                            "attack_carrier_field": field,
                            "material_recipe": f"{RECIPE_PREFIX}{instance.instance_id}.{phrasing}",
                        }
                    )
                    material = materialize_scenario(plan)
                    for index, file in enumerate(material.files):
                        body, snippet = documents[index]
                        if index == slot:
                            body = body.replace(snippet, file.fields[field])
                        assert file.body == format_document(index, body)
                    for index in (3, 5, 6):
                        rows = list(csv.reader(io.StringIO(material.files[index].body)))
                        assert rows and all(len(row) == 4 for row in rows)
                    mail = Parser(policy=policy.default).parsestr(material.files[7].body)
                    assert not mail.defects and mail["From"] and mail["To"]
                    assert mail["Subject"] and mail.get_payload()
                    invite = material.files[4].body
                    assert all(len(line.encode("utf-8")) <= 75 for line in invite.splitlines())
                    assert "ATTENDEE:urn:office:principal:" in invite
                    assert invite.count("BEGIN:VEVENT") == invite.count("END:VEVENT") == 1
                    assert all(key in invite for key in ("DTSTART:", "DTEND:", "DESCRIPTION:"))


def test_freeze_changes_when_only_attack_wording_changes(monkeypatch):
    from sandbox.scenarios.substantive_office import attacks

    before = load_error_capable_fixture("substantive-office-01")
    original = attacks.attack_text

    def changed(*args, **kwargs):
        return original(*args, **kwargs) + "附加协作说明。"

    monkeypatch.setattr(attacks, "attack_text", changed)
    after = load_error_capable_fixture("substantive-office-01")
    assert before.freeze_digest != after.freeze_digest


def test_earlier_material_version_cannot_silently_fall_back_to_the_historical_fixture():
    plan = _plan("summary.north-yard")
    plan = plan.model_copy(
        update={"material_recipe": "substantive-office-01.1.0.0.summary.north-yard.a"}
    )
    with pytest.raises(ValueError, match="unsupported substantive-office"):
        materialize_scenario(plan)
