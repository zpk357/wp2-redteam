"""Capture, retry and verification of commit-time effect evidence (P3).

Three rules live here, all of them from the draft's §6:

* **Binding** (`SOC-ENV-52`). An effect is usable only if it is part of the payload of the
  transition it cites: the transition must exist in the episode, must have committed, must
  not be the initialization transaction, and must name the objects the effect claims to
  have created. Citing a real committed transaction proves nothing by itself.
* **Retry** (`SOC-ENV-55`). The same key with identical content returns the original
  receipt - no second entry, no refusal. The same key with different content is a conflict
  and is refused. A duplicate is therefore not automatically a refusal.
* **Projection** (`SOC-ENV-51`). The ledger is the committed payloads, projected: whatever
  a record carries must appear in the ledger, and nothing else may.

Capture happens **inside** the committing transaction, so an effect that cannot be written
means the delivery cannot be reported as committed (`SOC-ENV-53`).
"""

from __future__ import annotations

from enum import StrEnum

from sandbox.replay.digests import sha256_digest
from sandbox.structured_v1.envelope_codes import EnvelopeRefusal, FailureCode
from sandbox.structured_v1.evidence import (
    DeliveryChannel,
    DeliveryEffect,
    DeliveryRelation,
    EffectKey,
    EffectLedger,
    RelatedResourceRef,
)
from sandbox.structured_v1.transitions import StructuredTransitionRecord

#: Fields that are allowed to differ between two attempts at the same effect.
_VOLATILE_FIELDS = frozenset({"effect_id", "sequence"})


class RetryDecision(StrEnum):
    """What a retry of an already-seen key should do."""

    APPEND = "append"
    RETURN_RECEIPT = "return-receipt"


def capture_effect(
    *,
    key: EffectKey,
    sequence: int,
    channel: DeliveryChannel,
    committed: bool,
    content_digest: str,
    proof_digest: str,
    blocked: bool = False,
    registered_units: tuple[str, ...] = (),
    registered_files: tuple[str, ...] = (),
    created_objects: tuple[str, ...] = (),
    related_refs: tuple[RelatedResourceRef, ...] = (),
    audience: tuple[DeliveryRelation, ...] = (),
) -> DeliveryEffect:
    """Build one effect from what the committing call can actually observe.

    The id is derived from the content, so an identical retry produces the identical
    effect rather than a second one that merely looks similar.
    """

    if blocked and committed:
        raise EnvelopeRefusal(
            FailureCode.UNBOUND, "an effect cannot be blocked and committed at once"
        )
    if not committed and created_objects:
        raise EnvelopeRefusal(
            FailureCode.UNBOUND, "an effect that did not commit cannot have created objects"
        )
    if (
        committed
        and channel is not DeliveryChannel.ACTOR_PRIVATE
        and not any(relation.readable for relation in audience)
    ):
        raise EnvelopeRefusal(
            FailureCode.UNBOUND,
            "a committed delivery must record at least one reader",
        )
    payload = {
        "key": {"action_request_id": key.action_request_id, "ordinal": key.ordinal},
        "channel": channel.value,
        "committed": committed,
        "blocked": blocked,
        "content_digest": content_digest,
        "registered_units": sorted(registered_units),
        "registered_files": sorted(registered_files),
        "created_objects": sorted(created_objects),
        "related_refs": [
            {"kind": item.kind, "resource_id": item.resource_id}
            for item in sorted(related_refs, key=lambda item: (item.kind, item.resource_id))
        ],
        "audience": [
            {"principal": item.principal, "readable": item.readable}
            for item in sorted(audience, key=lambda item: item.principal)
        ],
        "proof_digest": proof_digest,
    }
    return DeliveryEffect(
        effect_id=f"effect.{sha256_digest(payload)[7:23]}",
        sequence=sequence,
        channel=channel,
        committed=committed,
        blocked=blocked,
        content_digest=content_digest,
        registered_units=tuple(sorted(registered_units)),
        registered_files=tuple(sorted(registered_files)),
        created_objects=tuple(sorted(created_objects)),
        related_refs=tuple(sorted(related_refs, key=lambda item: (item.kind, item.resource_id))),
        audience=tuple(sorted(audience, key=lambda item: item.principal)),
        proof_digest=proof_digest,
    )


def same_content(first: DeliveryEffect, second: DeliveryEffect) -> bool:
    """True when two effects describe the same fact, ignoring where they were recorded."""

    return first.model_dump(exclude=set(_VOLATILE_FIELDS)) == second.model_dump(
        exclude=set(_VOLATILE_FIELDS)
    )


def retry_decision(
    ledger: EffectLedger,
    key: EffectKey,
    effect: DeliveryEffect,
) -> RetryDecision:
    """Decide what a retry means, refusing only a genuine conflict (`SOC-ENV-55`)."""

    entries = ledger.entries_for(key)
    if not entries:
        return RetryDecision.APPEND
    if all(same_content(entry.effect, effect) for entry in entries):
        return RetryDecision.RETURN_RECEIPT
    raise EnvelopeRefusal(
        FailureCode.KEY_CONFLICT,
        f"key {key.action_request_id}#{key.ordinal} was already recorded with other content",
    )


def append_effect(
    ledger: EffectLedger,
    effect: DeliveryEffect,
    *,
    key: EffectKey,
    transaction_id: str,
) -> EffectLedger:
    """Apply the retry rule: append a new effect, or return the receipt unchanged."""

    if retry_decision(ledger, key, effect) is RetryDecision.RETURN_RECEIPT:
        return ledger
    return ledger.append(effect, transaction_id=transaction_id, key=key)


def expected_effects(
    records: tuple[StructuredTransitionRecord, ...],
) -> tuple[DeliveryEffect, ...]:
    """The ledger's content, as the committed records define it (`SOC-ENV-51`)."""

    return tuple(effect for record in records for effect in record.effects)


def verify_projection(
    ledger: EffectLedger,
    records: tuple[StructuredTransitionRecord, ...],
) -> None:
    """The ledger must be exactly the projection of the committed records."""

    committed = expected_effects(records)
    if ledger.effects() != committed:
        raise EnvelopeRefusal(
            FailureCode.UNBOUND,
            "the ledger is not the projection of the committed records",
        )


def verify_ledger(
    ledger: EffectLedger,
    records: tuple[StructuredTransitionRecord, ...],
    *,
    initialization_transaction_id: str,
) -> None:
    """Every entry must be bound to a committed transition that carries it (`SOC-ENV-52`)."""

    by_transaction = {record.transaction_id: record for record in records}
    for entry in ledger.entries:
        if entry.transaction_id == initialization_transaction_id:
            raise EnvelopeRefusal(
                FailureCode.INITIALIZATION_CITED_AS_DELIVERY,
                f"effect {entry.effect.effect_id} cites the initialization transaction",
            )
        record = by_transaction.get(entry.transaction_id)
        if record is None:
            raise EnvelopeRefusal(
                FailureCode.UNBOUND,
                f"effect {entry.effect.effect_id} cites an unrecorded transaction",
            )
        if (
            not record.committed
            and (
                entry.effect.committed
                or (not entry.effect.blocked and (
                    entry.effect.channel.value == "actor-private"
                    or entry.effect.created_objects
                ))
            )
        ):
            raise EnvelopeRefusal(
                FailureCode.UNBOUND,
                f"effect {entry.effect.effect_id} is not a valid external attempt",
            )
        carried = {effect.effect_id for effect in record.effects}
        if entry.effect.effect_id not in carried:
            raise EnvelopeRefusal(
                FailureCode.UNBOUND,
                f"effect {entry.effect.effect_id} is not part of its transaction's payload",
            )
        missing = sorted(set(entry.effect.created_objects) - set(record.created_object_ids))
        if missing:
            raise EnvelopeRefusal(
                FailureCode.UNBOUND,
                f"effect {entry.effect.effect_id} claims objects its transition did not "
                f"create: {missing}",
            )
