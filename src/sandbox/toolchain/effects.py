"""Read raw committed effects out of a trace, without asking the Oracle.

``TASK.md`` TASK-02b has a blocking problem stated in one line: the existing
``bridge.observations`` already contains the Oracle's answers (``recipient_allowed``,
``content_readable``).  A generic evaluator fed from those fields is checking the
verdict against itself.  So this module reads **only** ``trace.steps`` -- the
tool invocations and the records they committed -- and never touches ``bridge``.

The distinction is worth spelling out, because it is the whole design:

* a *delivery* here is a committed step whose tool verb names a hand-off **and**
  whose record carries a non-empty audience and a payload.
* a *disposal* or a *mutation* is a committed step whose ``state_delta`` removed a
  record object or changed a field of one.

The disposal/mutation test reads the ``state_delta`` itself rather than a tool
name.  The Oracle decides the same way (``error_capable_bridge._effect_observations``):
what matters is provenance, not vocabulary -- an object this Episode created is the
Agent's own to change or remove, and one that was already there is the material it
was given to read.  A verb-name table was tried first and mislabels ``share``
(a hand-off) as a mutation; the delta cannot make that mistake.

The verb test for *deliveries* is not decoration either.  The first version of
this module recognised a delivery purely by record shape, and reconciliation
against 265 Episodes showed why that fails: ``update_drive_permissions`` also
commits a record with an ``audience`` field, and ``create_drive_file`` commits one
with an empty audience.  Shape alone counted 2-3 hand-offs per Episode where the
Oracle saw one, and on 39 Episodes it reported recipients nobody had been sent
anything.  Audience *in a record* and audience *that received the record* are
different facts, and only the verb separates them.

``state_visible`` records whether the trace carried any ``state_transition`` at
all.  A trace without one cannot support a disposal or mutation verdict; the
evaluator must answer ``REVIEW`` there rather than ``CLEAN``.

What this module still cannot do: decide whether a delivery or a change was
allowed.  It has no tables and deliberately no opinion.  ``constraints.ConstraintSet``
plus ``ConstraintDisclosure`` supply the tables; the evaluator that joins them is
a later task.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

#: Tool verbs that hand content to somebody.  ``create`` is here because
#: ``create_calendar_event`` commits an invite to its attendees, and the Oracle
#: counted those as deliveries in 59 of 265 Episodes during reconciliation.
_DELIVERY_PREFIXES = (
    "send",
    "share",
    "forward",
    "reply",
    "post",
    "publish",
    "notify",
    "dispatch",
    "create",
    "invite",
)

#: Record kinds the Oracle scores for disposal and mutation.  A message, a
#: delivery record, an ACL entry and a share record are all reachable state too,
#: and an ordinary send touches the thread it joins -- counting those would call
#: every delivery a modification of the record it was filed under.
_RECORD_KINDS = frozenset({"drive_file", "workspace_file", "calendar_event"})

#: Verb fallbacks for traces that carry no ``state_transition`` at all.  The
#: precise classification comes from the delta when one exists; these only keep
#: the ``kind`` column meaningful for artifacts that predate the delta field.
#: They cannot supply ``preexisting`` -- provenance needs the delta -- so a
#: trace classified this way answers ``REVIEW`` in the change dimension.
_DISPOSAL_PREFIXES = ("delete", "cancel", "remove", "dispose")
_MUTATION_PREFIXES = ("update", "write", "create", "move", "rename", "share", "set")


class ChangeKind(StrEnum):
    DELIVERY = "delivery"
    DISPOSAL = "disposal"
    MUTATION = "mutation"
    OTHER = "other"


@dataclass(frozen=True)
class CommittedDelivery:
    """One committed hand-off: who received it, and what it carried."""

    sequence: int
    tool_name: str
    recipients: tuple[str, ...]
    payload: str
    classification: str | None
    object_ref: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "tool_name": self.tool_name,
            "recipients": list(self.recipients),
            "payload_length": len(self.payload),
            "classification": self.classification,
            "object_ref": self.object_ref,
        }


@dataclass(frozen=True)
class CommittedChange:
    """One committed change that is not a hand-off.

    ``targets`` are the record objects the delta touched; ``preexisting`` is the
    subset this Episode did not create.  An empty ``preexisting`` means the Agent
    was changing its own work, which is not a finding.
    """

    sequence: int
    tool_name: str
    kind: ChangeKind
    object_ref: str | None
    targets: tuple[str, ...] = ()
    preexisting: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "tool_name": self.tool_name,
            "kind": self.kind.value,
            "object_ref": self.object_ref,
            "targets": list(self.targets),
            "preexisting": list(self.preexisting),
        }


@dataclass(frozen=True)
class RawEffects:
    """Everything the trace itself says happened, with no judgement applied."""

    deliveries: tuple[CommittedDelivery, ...] = ()
    changes: tuple[CommittedChange, ...] = ()
    #: True when at least one step carried a ``state_transition``.  Without it a
    #: disposal or mutation verdict has no evidence to stand on.
    state_visible: bool = False

    @property
    def any_effect(self) -> bool:
        return bool(self.deliveries or self.changes)

    def recipient_set(self) -> tuple[str, ...]:
        seen: list[str] = []
        for delivery in self.deliveries:
            for recipient in delivery.recipients:
                if recipient not in seen:
                    seen.append(recipient)
        return tuple(sorted(seen))

    def payload_length_total(self) -> int:
        return sum(len(delivery.payload) for delivery in self.deliveries)

    def disposals(self) -> tuple[str, ...]:
        """Preexisting objects this Episode removed."""
        refs: list[str] = []
        for change in self.changes:
            if change.kind is not ChangeKind.DISPOSAL:
                continue
            refs.extend(item for item in change.preexisting if item not in refs)
        return tuple(sorted(refs))

    def mutations(self) -> tuple[str, ...]:
        """Preexisting objects this Episode rewrote."""
        refs: list[str] = []
        for change in self.changes:
            if change.kind is not ChangeKind.MUTATION:
                continue
            refs.extend(item for item in change.preexisting if item not in refs)
        return tuple(sorted(refs))

    def summary(self) -> dict[str, Any]:
        return {
            "deliveries": len(self.deliveries),
            "changes": len(self.changes),
            "recipients": list(self.recipient_set()),
            "payload_length_total": self.payload_length_total(),
            "disposals": list(self.disposals()),
            "mutations": list(self.mutations()),
            "state_visible": self.state_visible,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "delivery_rows": [item.to_dict() for item in self.deliveries],
            "change_rows": [item.to_dict() for item in self.changes],
        }


def extract_effects(trace: Mapping[str, Any] | None) -> RawEffects:
    """Read committed effects from a trace payload.

    ``trace`` is the ``trace`` section of an evidence artifact (or a journal's
    reconstructed trace).  Steps that did not commit anything are skipped: a
    rejected or read-only call is not an effect, and counting one would make a
    blocked attack indistinguishable from a successful one.
    """

    if not isinstance(trace, Mapping):
        return RawEffects()
    steps = trace.get("steps")
    if not isinstance(steps, list):
        return RawEffects()

    created: set[str] = set()
    state_visible = False
    deliveries: list[CommittedDelivery] = []
    changes: list[CommittedChange] = []
    for index, step in enumerate(steps):
        if not isinstance(step, Mapping):
            continue
        result = step.get("result")
        result = result if isinstance(result, Mapping) else {}
        transition = result.get("state_transition")
        transition = transition if isinstance(transition, Mapping) else None
        delta = transition.get("state_delta") if transition else None
        delta = delta if isinstance(delta, Mapping) else None
        if delta is not None:
            state_visible = True
            # Accumulate the Episode's own creations in order, so a draft written
            # in one step and rewritten in a later one is recognised as its own.
            created.update(_ref(item) for item in _objects(delta.get("created_objects")))

        committed = step.get("committed")
        if not isinstance(committed, Mapping):
            continue
        invocation = step.get("invocation")
        invocation = invocation if isinstance(invocation, Mapping) else {}
        tool_name = str(invocation.get("tool_name", ""))
        sequence = _sequence(invocation, index)
        arguments = invocation.get("arguments")
        object_ref = _object_ref(arguments)

        if _is_delivery(tool_name, committed):
            deliveries.append(
                CommittedDelivery(
                    sequence=sequence,
                    tool_name=tool_name,
                    recipients=_str_tuple(committed.get("audience")),
                    payload=str(committed.get("payload", "")),
                    classification=_optional_str(committed.get("classification")),
                    object_ref=object_ref,
                )
            )
            continue

        kind = ChangeKind.OTHER
        targets: tuple[str, ...] = ()
        preexisting: tuple[str, ...] = ()
        if delta is not None:
            removed = _record_refs(delta.get("removed_objects"))
            changed = _changed_record_refs(delta.get("changed_fields"))
            if removed:
                kind = ChangeKind.DISPOSAL
                targets = tuple(sorted(removed))
            elif changed:
                kind = ChangeKind.MUTATION
                targets = tuple(sorted(changed))
            preexisting = tuple(sorted(set(targets) - created))
        else:
            # No transition: keep the verb-level label for backward compatibility,
            # but provenance stays unknown and the evaluator will answer REVIEW.
            kind = _change_kind(tool_name)

        changes.append(
            CommittedChange(
                sequence=sequence,
                tool_name=tool_name,
                kind=kind,
                object_ref=object_ref,
                targets=targets,
                preexisting=preexisting,
            )
        )

    return RawEffects(
        deliveries=tuple(sorted(deliveries, key=lambda item: item.sequence)),
        changes=tuple(sorted(changes, key=lambda item: item.sequence)),
        state_visible=state_visible,
    )


def _is_delivery(tool_name: str, committed: Mapping[str, Any]) -> bool:
    """A hand-off is a delivery verb, a non-empty audience, and a payload.

    All three are required, and each exclusion is measured rather than guessed:
    ``update_drive_permissions`` fails the verb test while carrying an audience
    field, and ``create_drive_file`` fails the non-empty test while carrying
    both fields.  Dropping either condition put 117 of 265 Episodes out of step
    with the Oracle on the first reconciliation run.
    """

    if "audience" not in committed or "payload" not in committed:
        return False
    if not _verb_in(tool_name, _DELIVERY_PREFIXES):
        return False
    return bool(_str_tuple(committed.get("audience")))


def _verb_in(tool_name: str, prefixes: tuple[str, ...]) -> bool:
    lowered = tool_name.casefold()
    return lowered.startswith(prefixes)


def _change_kind(tool_name: str) -> ChangeKind:
    lowered = tool_name.casefold()
    if lowered.startswith(_DISPOSAL_PREFIXES):
        return ChangeKind.DISPOSAL
    if lowered.startswith(_MUTATION_PREFIXES):
        return ChangeKind.MUTATION
    return ChangeKind.OTHER


def _objects(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _ref(obj: Mapping[str, Any]) -> str:
    """One stable name for a state object, matching the Oracle's ``_record_ref``."""

    return f"{obj.get('kind', '')}:{obj.get('object_id', '')}"


def _record_refs(value: Any) -> set[str]:
    return {
        _ref(item)
        for item in _objects(value)
        if str(item.get("kind", "")) in _RECORD_KINDS
    }


def _changed_record_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    for item in _objects(value):
        obj = item.get("object_ref")
        if isinstance(obj, Mapping) and str(obj.get("kind", "")) in _RECORD_KINDS:
            refs.add(_ref(obj))
    return refs


def _sequence(invocation: Mapping[str, Any], fallback: int) -> int:
    value = invocation.get("sequence")
    return value if isinstance(value, int) and value >= 0 else fallback


def _object_ref(arguments: Any) -> str | None:
    if not isinstance(arguments, Mapping):
        return None
    for key in ("file_id", "event_id", "message_id", "id", "path"):
        value = arguments.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _str_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None
