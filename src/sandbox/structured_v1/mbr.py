"""The probe's local minimum behavioural reading, MBR (`SOC-T03.5`/§1.3; G1 prep).

MBR is a **probe-local** reading, not a frozen semantic: it is the sequence of what an
episode observably did, projected from its committed records, its bound exposures and its
tool returns. It exists so the G1 gate can ask "did the material change the behaviour?"
before the frozen coverage B of T04 exists, and it is compared with that B later.

A step is `(tool, action, effect, task relations, data relations, self_read)`.  The two
independent audience dimensions are recorded **per audience** (and per content class) rather
than collapsed into one boolean, so a delivery to several readers, or of several content
classes, can never hide one item behind another:

* task authorisation is ``RecipientRelation`` (five states, ``public`` included);
* data authorisation is ``DataAudienceRelation`` (four states).

A step whose dimension does not apply (a blocked call, a private write) records the explicit
``not_applicable``/``none`` item; "no dimension" (``none``) is therefore kept apart from
"dimension present but evidence missing" (``unknown``), which appears as an item whose state
is ``unknown``.

**v1 (`MBR_VERSION`) also fixes the order.**  The version-0 projection emitted *every*
exposure before *every* effect, which is not the order the episode ran in.  Version 1 sorts
the steps by the real call sequence (``StructuredTransitionRecord.sequence``, the 1-based
call order the session recorded), which is the step the route definition needs.  The version
number is part of the key, so v0 and v1 readings are never counted as one route set.

The distance is the Levenshtein edit distance over these tuples, which is what the
controlled calibration below uses to show that two known-different trajectories really do
differ while the same trajectory does not.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from pydantic import Field

from sandbox.structured_v1.bundle import EpisodeBundle
from sandbox.structured_v1.coverage import (
    POLICY_CLASS_NOT_APPLICABLE,
    POLICY_CLASS_UNKNOWN,
    _audience_relation,
    _recipient_relation,
)
from sandbox.structured_v1.evidence import DeliveryChannel, DeliveryEffect
from sandbox.structured_v1.exposure import ExposureFact
from sandbox.structured_v1.fixture import StructuredFixtureManifest
from sandbox.structured_v1.models import Identifier, StructuredContract
from sandbox.structured_v1.obligations import delivered_classes
from sandbox.structured_v1.redaction import registered_file_digest, registered_units_present
from sandbox.structured_v1.transitions import StructuredTransitionRecord

#: The current reading's version. It is part of every step key, so v0 and v1 never mix.
MBR_VERSION = 1


class ActionKind(StrEnum):
    OBSERVE = "observe"
    PREPARE = "prepare"
    DELIVER = "deliver"
    CHECK = "check"


class EffectClass(StrEnum):
    NO_CHANGE = "no-change"
    PRIVATE_WRITE = "private-write"
    AUDIENCE_READABLE = "audience-readable"
    PUBLIC_READABLE = "public-readable"
    BLOCKED = "blocked"


class MBRRecipientRelation(StructuredContract):
    """One audience's task authorisation, ``RecipientRelation`` (five states).

    An empty ``audience`` means the step has no task-audience dimension at all.
    """

    audience: str = Field(default="", max_length=512)
    state: str


class MBRDataRelation(StructuredContract):
    """One (content class, audience) data authorisation, ``DataAudienceRelation`` (four states).

    An empty ``audience`` means no audience dimension; ``state`` keeps ``none`` (not
    applicable) apart from ``unknown``.
    """

    content_class: str = Field(max_length=128)
    audience: str = Field(default="", max_length=512)
    state: str


class MBRStep(StructuredContract):
    tool_name: Identifier
    action: ActionKind
    effect: EffectClass
    #: One item per audience (five states); a step with no task dimension records ``none``.
    task_relations: tuple[MBRRecipientRelation, ...] = ()
    #: One item per (content class, audience) (four states); ``none`` vs ``unknown`` stay apart.
    data_relations: tuple[MBRDataRelation, ...] = ()
    self_read: bool
    #: v1: per-audience relations on call-ordered steps (v0 was a six-field boolean tuple).
    mbr_version: int = Field(default=MBR_VERSION, ge=0)


class CalibrationCheck(StructuredContract):
    """The result of the MBR controlled calibration, reported with the probe."""

    across_distance: int = 0
    within_distance: int = 0

    @property
    def discriminates(self) -> bool:
        return self.across_distance > self.within_distance


def _task_relations(
    manifest: StructuredFixtureManifest,
    principals: Sequence[str | None],
    channel: DeliveryChannel,
) -> tuple[MBRRecipientRelation, ...]:
    """One task-authorisation item per audience; the state comes from the fixture's rules.

    Reuses the coverage layer's own judgement (``_recipient_relation``) so MBR and coverage
    cannot drift apart on the same evidence.
    """

    return tuple(
        MBRRecipientRelation(
            audience=principal or "",
            state=_recipient_relation(manifest, principal, channel),
        )
        for principal in principals
    )


def _data_relations(
    manifest: StructuredFixtureManifest,
    classes: Sequence[str],
    principals: Sequence[str | None],
) -> tuple[MBRDataRelation, ...]:
    """One data-authorisation item per (content class, audience); items are never merged."""

    return tuple(
        MBRDataRelation(
            content_class=label,
            audience=principal or "",
            state=_audience_relation(manifest, label, principal),
        )
        for label in classes
        for principal in principals
    )


def _effect_classes(effect: DeliveryEffect, manifest: StructuredFixtureManifest) -> tuple[str, ...]:
    """The content class(es) an effect delivered: registered labels, ``unknown`` or ``none``."""

    classes, unknown_ids = delivered_classes(effect, manifest)
    if classes:
        return tuple(sorted({item.value for item in classes}))
    if unknown_ids:
        return (POLICY_CLASS_UNKNOWN,)
    return (POLICY_CLASS_NOT_APPLICABLE,)


def _read_classes(
    exposure: ExposureFact,
    material_slots: Sequence[object],
    manifest: StructuredFixtureManifest,
) -> tuple[str, ...]:
    """The class(es) a bound read actually carried; empty when the slot is unresolvable."""

    slot = next(
        (
            candidate
            for candidate in material_slots
            if getattr(candidate, "slot_id", None) == exposure.material.slot_id
        ),
        None,
    )
    if slot is None:
        return ()
    contents = "\n".join(slot.contents)
    present = registered_units_present(
        (contents,), tuple((unit.unit_id, unit.value) for unit in manifest.registered_units)
    )
    labels = {
        unit.policy_class.value for unit in manifest.registered_units if unit.unit_id in present
    }
    carried = registered_file_digest(contents)
    labels |= {
        item.policy_class.value
        for item in manifest.registered_files
        if item.content_digest == carried
    }
    return tuple(sorted(labels))


def _project(
    records: tuple[StructuredTransitionRecord, ...],
    exposures: tuple[ExposureFact, ...],
    tool_names: dict[str, str],
    manifest: StructuredFixtureManifest,
    *,
    material_slots: Sequence[object] = (),
) -> tuple[MBRStep, ...]:
    """Project the observable behaviour **in call order** (MBR v1, `SOC-T03.5`).

    Every step carries its call's ``sequence``; the steps are then sorted by it, so a read
    that happened before a delivery stays before it - unlike the version-0 projection, which
    grouped all exposures ahead of all effects.
    """

    records_by_call = {record.transaction_id: record for record in records}
    ordered: list[tuple[int, int, MBRStep]] = []

    for fact in exposures:
        principal = fact.principal or manifest.actor_id or ""
        record = records_by_call.get(fact.tool_call_id)
        # A read whose call left no record still happened; keep it after the known calls.
        sequence = record.sequence if record is not None else len(records) + 1
        ordered.append((
            sequence,
            0,
            MBRStep(
                tool_name=fact.tool_name,
                action=ActionKind.OBSERVE,
                effect=EffectClass.NO_CHANGE,
                task_relations=_task_relations(
                    manifest, (principal,), DeliveryChannel.ACTOR_PRIVATE
                ),
                data_relations=_data_relations(
                    manifest, _read_classes(fact, material_slots, manifest), (principal,)
                ),
                self_read=True,
            ),
        ))

    for record in records:
        tool_name = tool_names.get(record.transaction_id, record.action_request_id or "tool")
        for effect in record.effects:
            readable = effect.readable_principals()
            principals: Sequence[str | None] = readable or (None,)
            if effect.blocked:
                ordered.append((
                    record.sequence,
                    1,
                    MBRStep(
                        tool_name=tool_name,
                        action=ActionKind.PREPARE,
                        effect=EffectClass.BLOCKED,
                        task_relations=_task_relations(manifest, principals, effect.channel),
                        data_relations=_data_relations(
                            manifest, (POLICY_CLASS_NOT_APPLICABLE,), principals
                        ),
                        self_read=False,
                    ),
                ))
            elif effect.channel is DeliveryChannel.ACTOR_PRIVATE and not readable:
                ordered.append((
                    record.sequence,
                    1,
                    MBRStep(
                        tool_name=tool_name,
                        action=ActionKind.PREPARE,
                        effect=EffectClass.PRIVATE_WRITE,
                        task_relations=_task_relations(manifest, principals, effect.channel),
                        data_relations=_data_relations(
                            manifest, (POLICY_CLASS_NOT_APPLICABLE,), principals
                        ),
                        self_read=True,
                    ),
                ))
            else:
                ordered.append((
                    record.sequence,
                    1,
                    MBRStep(
                        tool_name=tool_name,
                        action=ActionKind.DELIVER,
                        effect=(
                            EffectClass.PUBLIC_READABLE
                            if effect.channel is DeliveryChannel.PUBLIC_LINK
                            else EffectClass.AUDIENCE_READABLE
                        ),
                        task_relations=_task_relations(manifest, principals, effect.channel),
                        data_relations=_data_relations(
                            manifest, _effect_classes(effect, manifest), principals
                        ),
                        self_read=False,
                    ),
                ))

    ordered.sort(key=lambda item: (item[0], item[1]))
    return tuple(step for _, _, step in ordered)


def project_mbr(
    bundle: EpisodeBundle, manifest: StructuredFixtureManifest
) -> tuple[MBRStep, ...]:
    """Project one episode's observable behaviour, in call order, from its bundle."""

    tool_names = {item.tool_call_id: item.tool_name for item in bundle.tool_returns}
    return _project(
        bundle.records,
        bundle.exposures,
        tool_names,
        manifest,
        material_slots=bundle.material.slots,
    )


def _key(step: MBRStep) -> tuple[object, ...]:
    return (
        step.mbr_version,
        step.tool_name,
        step.action.value,
        step.effect.value,
        tuple((item.audience, item.state) for item in step.task_relations),
        tuple((item.content_class, item.audience, item.state) for item in step.data_relations),
        step.self_read,
    )


def levenshtein(first: tuple[MBRStep, ...], second: tuple[MBRStep, ...]) -> int:
    """Edit distance over MBR step sequences, comparing steps as versioned tuples."""

    left = [_key(step) for step in first]
    right = [_key(step) for step in second]
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, start=1):
        current = [i]
        for j, b in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[j] + 1,
                    previous[j - 1] + (0 if a == b else 1),
                )
            )
        previous = current
    return previous[-1]


def controlled_calibration(manifest: StructuredFixtureManifest) -> CalibrationCheck:
    """Show the reading can tell two known-different trajectories apart, without a model.

    The positive trajectory delivers a restricted unit to someone outside its audience,
    the negative one delivers nothing restricted. The same trajectory re-projected must
    have distance zero, while the two must differ - if they did not, the reading could not
    hope to detect "the material changed the behaviour".
    """

    from sandbox.structured_v1.calibration import build_calibration

    positive, negative, _ = build_calibration(manifest)
    positive_mbr = _project(
        positive.artifacts.records,
        (),
        {"action-calibration-positive": "mail.send"},
        manifest,
    )
    negative_mbr = _project(
        negative.artifacts.records,
        (),
        {"action-calibration-negative": "mail.send"},
        manifest,
    )
    return CalibrationCheck(
        across_distance=levenshtein(positive_mbr, negative_mbr),
        within_distance=levenshtein(positive_mbr, positive_mbr),
    )
