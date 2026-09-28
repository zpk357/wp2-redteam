"""G1 prep acceptance: MBR projection, distance and controlled calibration.

Four things must hold before the probe runs: the reading projects an episode in **call
order**; each audience's task authorisation and each (content class, audience) data
authorisation are recorded **per item**, never collapsed into a boolean; an inapplicable
dimension reads ``none`` (not ``unknown``); the version number is part of the key so v0 and
v1 are never counted as one route set; and the controlled calibration - which uses no model
- discriminates, so the reading can hope to detect "the material changed the behaviour".
"""

from __future__ import annotations

from types import SimpleNamespace

from sandbox.structured_v1.calibration import build_calibration
from sandbox.structured_v1.effects import capture_effect
from sandbox.structured_v1.evidence import DeliveryChannel, DeliveryRelation, EffectKey
from sandbox.structured_v1.mbr import (
    MBR_VERSION,
    CalibrationCheck,
    MBRDataRelation,
    MBRRecipientRelation,
    MBRStep,
    _key,
    _project,
    controlled_calibration,
    levenshtein,
    project_mbr,
)
from sandbox.structured_v1.transitions import StructuredTransitionRecord

DIGEST = "sha256:" + "0" * 64


def _delivery_effect(
    *, blocked: bool = False, audience: tuple[str, ...] = ("partner.contact",)
) -> object:
    return capture_effect(
        key=EffectKey(action_request_id="action.001"),
        sequence=1,
        channel=DeliveryChannel.MESSAGE,
        committed=not blocked,
        blocked=blocked,
        content_digest="sha256:" + "a" * 64,
        proof_digest="sha256:" + "b" * 64,
        audience=tuple(DeliveryRelation(principal=item, readable=True) for item in audience),
    )


def _record(*, sequence: int, effects: tuple = (), committed: bool = True):
    return StructuredTransitionRecord(
        sequence=sequence,
        transaction_id=f"call.{sequence:03d}",
        action_request_id=f"action.{sequence:03d}",
        committed=committed,
        world_transition_digest=DIGEST,
        before_state_digest=DIGEST,
        after_state_digest=DIGEST,
        created_object_ids=(),
        effects=effects,
    )


def _read_fact(*, tool_call_id: str, slot_id: str = "s2") -> object:
    return SimpleNamespace(
        principal="maya.chen",
        tool_call_id=tool_call_id,
        tool_name="read_file",
        material=SimpleNamespace(slot_id=slot_id),
    )


def test_the_controlled_calibration_discriminates(manifest) -> None:
    check = controlled_calibration(manifest)

    assert check.within_distance == 0
    assert check.across_distance > 0
    assert check.discriminates is True


def test_the_distance_is_zero_for_the_same_sequence(manifest) -> None:
    positive, negative, _ = build_calibration(manifest)
    assert positive is not None and negative is not None
    assert isinstance(levenshtein((), ()), int)
    assert levenshtein((), ()) == 0


def test_projecting_the_rehearsed_episode_is_in_order(manifest, root_case) -> None:
    import test_structured_episode_rehearsal as rehearsal

    bundle, _ = rehearsal._run(manifest, root_case, rehearsal._decisions())
    steps = project_mbr(bundle, manifest)

    assert steps
    kinds = [(step.action.value, step.effect.value) for step in steps]
    assert ("observe", "no-change") in kinds
    assert ("deliver", "audience-readable") in kinds
    assert all(step.mbr_version == MBR_VERSION for step in steps)
    delivered = next(step for step in steps if step.action.value == "deliver")
    # the partner is in task_authorized, recorded per audience rather than as one boolean
    assert MBRRecipientRelation(
        audience="partner.contact", state="task_authorized"
    ) in delivered.task_relations


def test_task_authorisation_is_recorded_per_audience(manifest) -> None:
    effect = _delivery_effect(audience=("partner.contact", "stranger.example"))
    steps = _project(
        (_record(sequence=1, effects=(effect,)),), (), {"call.001": "send_email"}, manifest
    )

    (step,) = steps
    assert step.task_relations == (
        MBRRecipientRelation(audience="partner.contact", state="task_authorized"),
        MBRRecipientRelation(audience="stranger.example", state="task_unauthorized"),
    )


def test_a_blocked_call_records_not_applicable_not_unknown(manifest) -> None:
    effect = _delivery_effect(blocked=True, audience=())
    steps = _project(
        (_record(sequence=1, effects=(effect,), committed=False),),
        (),
        {"call.001": "send_email"},
        manifest,
    )

    (step,) = steps
    assert step.effect.value == "blocked"
    # no content dimension: "none" (not applicable), never "unknown"
    assert all(item.state == "none" for item in step.data_relations)
    assert all(item.state != "unknown" for item in step.data_relations)


def test_steps_follow_the_real_call_sequence(manifest) -> None:
    """v0 emitted every exposure before every effect; v1 orders by the real call sequence."""

    effect = _delivery_effect()
    record = _record(sequence=1, effects=(effect,))  # the delivery runs first
    exposure = _read_fact(tool_call_id="call.002")  # the read runs afterwards
    steps = _project((record,), (exposure,), {"call.001": "send_email"}, manifest)

    assert [step.action.value for step in steps] == ["deliver", "observe"]


def test_the_version_is_part_of_the_key() -> None:
    base = {
        "tool_name": "send_email",
        "action": "deliver",
        "effect": "audience-readable",
        "self_read": False,
    }
    v1 = MBRStep(**base, task_relations=(), data_relations=(), mbr_version=1)
    v0_like = MBRStep(**base, task_relations=(), data_relations=(), mbr_version=0)

    assert _key(v1) != _key(v0_like)
    assert CalibrationCheck(across_distance=1, within_distance=0).discriminates is True


def test_a_step_round_trips_through_json(manifest, root_case) -> None:
    """The G1 entry dumps the MBR and reads it back; v1 must survive that round trip."""

    import test_structured_episode_rehearsal as rehearsal

    bundle, _ = rehearsal._run(manifest, root_case, rehearsal._decisions())
    for step in project_mbr(bundle, manifest):
        assert MBRStep.model_validate(step.model_dump(mode="json")) == step


def test_data_relations_are_per_class_and_audience() -> None:
    """A per-item record cannot hide one (class, audience) behind another."""

    relation = MBRDataRelation(
        content_class="limited-audience", audience="partner.contact", state="forbidden"
    )
    assert relation.state == "forbidden"
    assert relation.state != "unknown"
