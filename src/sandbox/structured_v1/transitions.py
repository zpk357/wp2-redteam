"""One committed action and the effects it produced, written as a single artifact (P3).

``SOC-ENV-51`` says the effect record is part of the transaction payload rather than a
parallel log, and the ledger is a projection of committed payloads. This module is that
payload: the world transition's digest, the state digests it moved between, the objects it
created, and the effects it produced - persisted together, so "committed but unrecorded"
cannot happen and the ledger can always be re-derived from what was committed
(``SOC-ENV-53``).

The chain rule of ``SOC-ENV-04`` is checked here too: the episode's records must be
contiguous and must start where the initialization transaction left the world.
"""

from __future__ import annotations

from pydantic import Field, model_validator

from sandbox.scenarios.office_v2.world import StateTransitionRecord
from sandbox.structured_v1.envelope_codes import EnvelopeRefusal, FailureCode
from sandbox.structured_v1.evidence import DeliveryEffect
from sandbox.structured_v1.models import (
    Identifier,
    Sha256Digest,
    StructuredContract,
)


class StructuredTransitionRecord(StructuredContract):
    """What one committed episode action changed and delivered."""

    sequence: int = Field(ge=0)
    transaction_id: Identifier
    action_request_id: Identifier | None = None
    committed: bool
    world_transition_digest: Sha256Digest
    before_state_digest: Sha256Digest
    after_state_digest: Sha256Digest
    created_object_ids: tuple[str, ...] = ()
    effects: tuple[DeliveryEffect, ...] = ()

    @model_validator(mode="after")
    def effects_and_outcome_agree(self) -> StructuredTransitionRecord:
        effect_ids = [effect.effect_id for effect in self.effects]
        if len(effect_ids) != len(set(effect_ids)):
            raise ValueError("a transition must not record the same effect twice")
        if not self.committed:
            # A refused call still happened, and `SOC-SAF-13` keeps it as a fact. An
            # external call that reached the delivery channel but neither committed nor
            # received a platform block is an attempted effect, never a delivery.
            for effect in self.effects:
                if effect.committed:
                    raise ValueError("a transition that did not commit cannot deliver anything")
                if not effect.blocked and (
                    effect.channel.value == "actor-private"
                    or effect.created_objects
                ):
                    raise ValueError(
                        "a noncommitted effect must be an external attempt without created objects"
                    )
            if self.before_state_digest != self.after_state_digest:
                raise ValueError("a failed transition cannot change the state digest")
        return self

    def delivery_effects(self) -> tuple[DeliveryEffect, ...]:
        return tuple(effect for effect in self.effects if effect.is_delivery())


def record_transition(
    transition: StateTransitionRecord,
    *,
    sequence: int,
    action_request_id: str | None = None,
    effects: tuple[DeliveryEffect, ...] = (),
) -> StructuredTransitionRecord:
    """Turn a world transition into its episode record, refusing impossible combinations."""

    if not transition.committed:
        for effect in effects:
            if effect.committed or (not effect.blocked and (
                effect.channel.value == "actor-private" or effect.created_objects
            )):
                raise EnvelopeRefusal(
                    FailureCode.UNBOUND,
                    "a noncommitted effect must be an external attempt without created objects",
                )
    created = tuple(
        sorted({item.object_id for item in transition.state_delta.created_objects})
    )
    return StructuredTransitionRecord(
        sequence=sequence,
        transaction_id=transition.transaction_id,
        action_request_id=action_request_id,
        committed=transition.committed,
        world_transition_digest=transition.transition_digest,
        before_state_digest=transition.before_state_digest,
        after_state_digest=transition.after_state_digest,
        created_object_ids=created,
        effects=effects,
    )


def initialized_transition_record(
    materialized: object,
    *,
    sequence: int = 0,
    action_request_id: str | None = None,
) -> StructuredTransitionRecord:
    """The episode's initialization transition as a record.

    It is kept in the bundle for reconstruction and audit (`SOC-ENV-89`) and carries **no
    effects**: materialisation is not a delivery (`SOC-ENV-76`).
    """

    transition = materialized.initialization_transition
    return record_transition(
        transition, sequence=sequence, action_request_id=action_request_id, effects=()
    )


def verify_resume(
    records: tuple[StructuredTransitionRecord, ...],
    *,
    expected_state_digest: str,
) -> None:
    """A resumed episode continues from where the **committed chain** ended (`SOC-ENV-56`).

    The checkpoint is the last committed transition, not the last ledger entry: an effect
    record can be appended or retried, while the chain is what the world actually did.
    """

    if not records:
        return
    last = max(records, key=lambda item: item.sequence)
    if last.after_state_digest != expected_state_digest:
        raise EnvelopeRefusal(
            FailureCode.RESUME_INCONSISTENT,
            "the resumed state is not where the committed chain ended",
        )


def verify_chain(
    records: tuple[StructuredTransitionRecord, ...],
    *,
    expected_start_state_digest: str,
    expected_start_sequence: int = 0,
) -> None:
    """The records must be contiguous from the initialization's end state (`SOC-ENV-04`).

    The initialization transition occupies sequence 0 and episode actions follow it, so a
    bundle passes ``expected_start_sequence=1``; a standalone chain starts at 0.
    """

    if not records:
        return
    ordered = tuple(sorted(records, key=lambda item: item.sequence))
    for position, record in enumerate(ordered):
        if position == 0:
            if record.sequence != expected_start_sequence:
                raise EnvelopeRefusal(
                    FailureCode.CHAIN_BROKEN,
                    f"the record chain starts at sequence {record.sequence}, "
                    f"not {expected_start_sequence}",
                )
        elif record.sequence != ordered[position - 1].sequence + 1:
            raise EnvelopeRefusal(
                FailureCode.CHAIN_BROKEN,
                f"sequence {record.sequence} does not follow "
                f"{ordered[position - 1].sequence}",
            )
    first = ordered[0]
    if first.before_state_digest != expected_start_state_digest:
        raise EnvelopeRefusal(
            FailureCode.CHAIN_BROKEN,
            "the first record does not start at the initialization's end state",
        )
    for previous, record in zip(ordered, ordered[1:], strict=False):
        if record.before_state_digest != previous.after_state_digest:
            raise EnvelopeRefusal(
                FailureCode.CHAIN_BROKEN,
                f"record {record.sequence} does not continue record {previous.sequence}",
            )
