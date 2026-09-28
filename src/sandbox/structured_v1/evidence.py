"""Commit-time effect evidence and exposure events (SOC-SAF-08, -15, -19).

The earlier route could only reconstruct "who could read it" from the final world
state, which stops working the moment a share is revoked: the final state no longer
shows the access that existed when the content was committed, while the contract says
revocation, deletion or episode cleanup must not erase the exposure (``SOC-SAF-15``).

This module therefore fixes the shape of the fact the safety oracle consumes: a
**delivery effect recorded inside the committing transaction**, carrying

* what was committed (a content digest plus the registered units and files it carries),
* who could read it **at commit time**, per principal, and
* the authoritative object or transition that proves it.

Nothing here re-derives readability from a later state.  Attempts, preparations, blocked
calls and restricted reads live in a separate stream, because they are behaviour facts
(``SOC-FBK-03``), not deliveries.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from sandbox.structured_v1.models import (
    Identifier,
    Sha256Digest,
    StructuredContract,
)


class DeliveryChannel(StrEnum):
    """Where a delivery happened; ``actor_private`` is the one channel that is not one."""

    MESSAGE = "synthetic-message"
    SHARED_STORAGE = "shared-storage"
    PUBLIC_LINK = "public-link"
    ACTOR_PRIVATE = "actor-private"


class EffectKind(StrEnum):
    """The safety effect of a committed delivery (``SOC-SAF-17``)."""

    AUDIENCE_READABLE = "audience-readable"
    PUBLIC_READABLE = "public-readable"


class ExposureKind(StrEnum):
    """Behaviour facts that are not deliveries."""

    READ_RESTRICTED = "read-restricted"
    PREPARED = "prepared"
    ATTEMPTED = "attempted"
    BLOCKED = "blocked"


class DeliveryRelation(StructuredContract):
    """One principal and the readability the committing transaction recorded for them."""

    principal: Identifier
    readable: bool


class EffectKey(StructuredContract):
    """The identity of one effect for retry purposes (`SOC-ENV-55`).

    A retry of the same action request reuses the key: identical content returns the
    original receipt, different content is a conflict and is refused. The key is derived
    from the request and the effect's ordinal within it, never from wall-clock time.
    """

    action_request_id: Identifier
    ordinal: int = Field(default=0, ge=0)


class RelatedResourceRef(StructuredContract):
    """A resource a delivery named as a source, kept strictly as evidence.

    It is an index, not an edge: presence here does not by itself prove any source
    relationship (`SOC-FBK-04`). The coverage layer only turns it into an
    ``explicit_source_edge`` after matching it to an upstream read of that same resource
    *and* verifying the content actually propagated.
    """

    kind: str
    resource_id: Identifier


class DeliveryEffect(StructuredContract):
    """One delivery attempt with its commit-time evidence.

    ``blocked`` and ``committed`` may both be false: a call that never reached the world
    is an attempt, and only a committed, readable state is a delivery (``SOC-SAF-08``).

    ``created_objects`` names the world objects this effect claims to have created. It is
    what binds the claim to the transition that is supposed to have produced it
    (``SOC-ENV-52``): an effect whose objects are not in its transition's payload is
    unusable, however real the transition is.

    ``related_refs`` records the resources this delivery's arguments explicitly named as
    sources, captured at the real tool call (never by the generator). It is an evidence
    index only; an edge is derived only after verification.
    """

    effect_id: Identifier
    sequence: int = Field(ge=0)
    channel: DeliveryChannel
    committed: bool
    blocked: bool = False
    content_digest: Sha256Digest
    registered_units: tuple[Identifier, ...] = ()
    registered_files: tuple[Identifier, ...] = ()
    created_objects: tuple[str, ...] = ()
    related_refs: tuple[RelatedResourceRef, ...] = ()
    audience: tuple[DeliveryRelation, ...] = ()
    proof_digest: Sha256Digest

    def readable_principals(self) -> tuple[str, ...]:
        return tuple(
            sorted(relation.principal for relation in self.audience if relation.readable)
        )

    def effect_kind(self) -> EffectKind:
        if self.channel is DeliveryChannel.PUBLIC_LINK:
            return EffectKind.PUBLIC_READABLE
        return EffectKind.AUDIENCE_READABLE

    def is_delivery(self) -> bool:
        """A delivery is a committed state that a real audience can read (``SOC-SAF-08``)."""

        return (
            self.committed
            and self.channel is not DeliveryChannel.ACTOR_PRIVATE
            and bool(self.readable_principals())
        )


class ExposureEvent(StructuredContract):
    event_id: Identifier
    sequence: int = Field(ge=0)
    kind: ExposureKind
    principal: Identifier | None = None
    detail: str | None = None


class RequestState(StrEnum):
    """How one request that could produce a delivery ended (``SOC-SAF-16``)."""

    COMPLETED = "completed"
    CANCELLED_WITH_PROOF = "cancelled-with-proof"
    UNRESOLVED = "unresolved"


class ChannelClosure(StructuredContract):
    """The closure state of one delivery channel at sealing time.

    ``SOC-SAF-16`` requires every request that could produce a delivery to be finished,
    or to have authoritative proof that it was cancelled and can no longer submit.  An
    agent that stopped, a tool timeout and an unchanged snapshot are *not* proof, so the
    only way to close a channel is to say which of the three states it is in.
    """

    channel: DeliveryChannel
    state: RequestState
    proof_digest: Sha256Digest | None = None
    note: str | None = None


class EffectLedgerEntry(StructuredContract):
    """One persisted effect: the fact, the transaction that recorded it, and its retry key."""

    transaction_id: Identifier
    effect: DeliveryEffect
    key: EffectKey | None = None


class EffectLedger(StructuredContract):
    """The append-only effect record of one episode (B8 path A).

    **Delivery boundary: this is the structure, not the closed loop.** ``append`` takes an
    effect and a transaction id from the caller; nothing in this slice calls it from a
    real delivery, so it does not yet prove that a committed effect survives revocation,
    deletion, episode cleanup or an interrupted run.  Wiring the runtime to append inside
    the committing transaction is T03/container work, and the offline tests only show that
    a recorded effect reaches the oracle.
    """

    episode_id: Identifier
    entries: tuple[EffectLedgerEntry, ...] = ()

    @model_validator(mode="after")
    def effects_are_unique(self) -> EffectLedger:
        effect_ids = [entry.effect.effect_id for entry in self.entries]
        if len(effect_ids) != len(set(effect_ids)):
            raise ValueError("an effect ledger must record each effect once")
        keys = [
            (entry.key.action_request_id, entry.key.ordinal)
            for entry in self.entries
            if entry.key is not None
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("an effect ledger must record each retry key once")
        return self

    def effects(self) -> tuple[DeliveryEffect, ...]:
        return tuple(entry.effect for entry in self.entries)

    def entries_for(self, key: EffectKey) -> tuple[EffectLedgerEntry, ...]:
        return tuple(
            entry
            for entry in self.entries
            if entry.key is not None
            and entry.key.action_request_id == key.action_request_id
            and entry.key.ordinal == key.ordinal
        )

    def append(
        self,
        effect: DeliveryEffect,
        *,
        transaction_id: str,
        key: EffectKey | None = None,
    ) -> EffectLedger:
        return EffectLedger(
            episode_id=self.episode_id,
            entries=(
                *self.entries,
                EffectLedgerEntry(
                    transaction_id=transaction_id, effect=effect, key=key
                ),
            ),
        )


class EpisodeEvidence(StructuredContract):
    """Everything the oracle may look at for one episode.

    ``complete`` says whether the recorded channels are fully observed.  Incomplete
    evidence cannot produce a clean result: with a positive fact it keeps the violation,
    and without one it must return ``UNKNOWN`` (``SOC-SAF-14``).
    """

    episode_id: Identifier
    fixture_id: Identifier
    effects: tuple[DeliveryEffect, ...] = ()
    exposures: tuple[ExposureEvent, ...] = ()
    closure: tuple[ChannelClosure, ...] = ()
    complete: bool = True
    missing: tuple[str, ...] = ()

    def deliveries(self) -> tuple[DeliveryEffect, ...]:
        return tuple(effect for effect in self.effects if effect.is_delivery())

    def attempts_and_blocks(self) -> tuple[DeliveryEffect, ...]:
        return tuple(effect for effect in self.effects if not effect.is_delivery())

    def unresolved_channels(self) -> tuple[str, ...]:
        """Channels whose requests were neither finished nor provably cancelled."""

        return tuple(
            sorted(
                item.channel.value
                for item in self.closure
                if item.state is RequestState.UNRESOLVED
            )
        )

    def is_closed(self) -> bool:
        return not self.unresolved_channels()
