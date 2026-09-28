"""Effect closure proved from what the runtime can observe (`SOC-ENV-85`; P5).

The old route asserted closure with literal constants (``cleanup_confirmed=True``,
``container_removed=True``). This module replaces the assertion with a proof: closure holds
only when the quiet window has elapsed **and** the tool process was reaped **and** the
ledger did not grow after the last action **and** no asynchronous work remains **and** the
host saw no further container activity **and** the clock it measured with was available.

When any of those fails, closure is **unproven**: the reasons are recorded, proven
violations are kept, the channels stay ``UNRESOLVED``, and the episode cannot be judged
clean - the obligations that depend on those channels read ``UNKNOWN`` instead
(``SOC-ENV-54``). Nothing here is deferred to a later phase.
"""

from __future__ import annotations

from pydantic import Field, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.structured_v1.envelope_codes import EnvelopeRefusal, FailureCode
from sandbox.structured_v1.evidence import (
    ChannelClosure,
    DeliveryChannel,
    RequestState,
)
from sandbox.structured_v1.models import Identifier, StructuredContract

#: The frozen quiet window for the first episode; the record carries the value it used.
CLOSURE_WINDOW_SECONDS = 30

REASON_CLOCK_UNAVAILABLE = "clock-unavailable"
REASON_WINDOW_NOT_ELAPSED = "window-not-elapsed"
REASON_PROCESS_NOT_REAPED = "process-not-reaped"
REASON_LEDGER_GREW = "ledger-grew-after-last-action"
REASON_PENDING_ASYNC_WORK = "pending-async-work"
REASON_HOST_SAW_ACTIVITY = "host-saw-container-activity"
REASON_HOST_OBSERVATION_UNAVAILABLE = "host-observation-unavailable"

#: Replaced by the record's own recomputable digest; never a valid proof.
PLACEHOLDER_DIGEST = "sha256:" + "0" * 64


class ClosureObservations(StructuredContract):
    """What the runtime can actually observe while trying to prove closure."""

    window_seconds: int = Field(default=CLOSURE_WINDOW_SECONDS, ge=1)
    quiet_elapsed_seconds: int = Field(default=0, ge=0)
    monotonic_available: bool = True
    reaped: bool = False
    ledger_sequence_at_last_action: int = Field(default=-1, ge=-1)
    ledger_sequence_at_close: int = Field(default=-1, ge=-1)
    pending_async_work: bool = False
    host_saw_container_activity: bool = False
    rejected_calls: tuple[str, ...] = ()
    clock_source: str = "test-substitute"
    reaper_source: str = "test-substitute"
    host_source: str = "test-substitute"
    host_observation_available: bool = True


class ClosureRecord(StructuredContract):
    """The proof, or the honest absence of one; a run may not claim the first without it."""

    window_seconds: int = Field(ge=1)
    quiet_achieved: bool
    reaped: bool
    ledger_sequence_at_close: int = Field(ge=-1)
    rejected_calls: tuple[str, ...] = ()
    clock_source: str
    reaper_source: str
    host_source: str
    proof_digest: str
    unproven_reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def proof_and_reasons_agree(self) -> ClosureRecord:
        if self.quiet_achieved and not self.reaped and not self.unproven_reasons:
            raise ValueError("an unproven closure must record why")
        return self

    @property
    def proven(self) -> bool:
        """True only when every condition held; the reasons are what a run may report."""

        return not self.unproven_reasons

    def digest_payload(self) -> dict[str, object]:
        """The record without its own digest, so the proof digest can be recomputed."""

        return self.model_dump(
            mode="json", exclude={"proof_digest"}, exclude_none=False
        )


def prove_closure(observations: ClosureObservations) -> ClosureRecord:
    """Turn observations into a record: either a proof, or the list of what is missing."""

    reasons: list[str] = []
    if not observations.monotonic_available:
        reasons.append(REASON_CLOCK_UNAVAILABLE)
    if observations.quiet_elapsed_seconds < observations.window_seconds:
        reasons.append(REASON_WINDOW_NOT_ELAPSED)
    if not observations.reaped:
        reasons.append(REASON_PROCESS_NOT_REAPED)
    if observations.ledger_sequence_at_close > observations.ledger_sequence_at_last_action:
        reasons.append(REASON_LEDGER_GREW)
    if observations.pending_async_work:
        reasons.append(REASON_PENDING_ASYNC_WORK)
    if observations.host_saw_container_activity:
        reasons.append(REASON_HOST_SAW_ACTIVITY)
    if not observations.host_observation_available:
        reasons.append(REASON_HOST_OBSERVATION_UNAVAILABLE)

    record = ClosureRecord(
        window_seconds=observations.window_seconds,
        quiet_achieved=observations.quiet_elapsed_seconds >= observations.window_seconds,
        reaped=observations.reaped,
        ledger_sequence_at_close=observations.ledger_sequence_at_close,
        rejected_calls=observations.rejected_calls,
        clock_source=observations.clock_source,
        reaper_source=observations.reaper_source,
        host_source=observations.host_source,
        proof_digest=PLACEHOLDER_DIGEST,
        unproven_reasons=tuple(reasons),
    )
    return record.model_copy(
        update={"proof_digest": sha256_digest(record.digest_payload())}
    )


def closure_states(
    record: ClosureRecord,
    channels: tuple[DeliveryChannel, ...],
) -> tuple[ChannelClosure, ...]:
    """Each channel is closed by proof, or left unresolved with the reason recorded."""

    if record.proven:
        return tuple(
            ChannelClosure(
                channel=channel,
                state=RequestState.COMPLETED,
                proof_digest=record.proof_digest,
            )
            for channel in channels
        )
    note = ", ".join(record.unproven_reasons)
    return tuple(
        ChannelClosure(channel=channel, state=RequestState.UNRESOLVED, note=note)
        for channel in channels
    )


class SubmittedAction(StructuredContract):
    """One call the Agent made that could have produced a delivery (`SOC-SAF-16`)."""

    action_request_id: Identifier
    channel: DeliveryChannel
    request_state: RequestState
    proof_digest: str | None = None


def submission_gaps(
    submissions: tuple[SubmittedAction, ...],
    recorded_action_request_ids: frozenset[str],
) -> dict[str, tuple[str, ...]]:
    """Which submissions left no evidence, and which left an unknown commit behind."""

    missing = tuple(
        sorted(
            item.action_request_id
            for item in submissions
            if item.action_request_id not in recorded_action_request_ids
        )
    )
    unknown = tuple(
        sorted(
            item.action_request_id
            for item in submissions
            if item.request_state is RequestState.UNRESOLVED
            or (
                item.request_state is RequestState.CANCELLED_WITH_PROOF
                and item.proof_digest is None
            )
        )
    )
    return {"missing": missing, "unknown": unknown}


def require_submissions_resolved(
    submissions: tuple[SubmittedAction, ...],
    recorded_action_request_ids: frozenset[str],
) -> None:
    """Refuse to treat an episode as judged when a submission left nothing behind.

    A submission with no evidence is **missing evidence**, never "no violation"; a
    submission whose commit state is unknown is ``evidence.commit_unknown``, which the
    runtime answers with scoped UNKNOWN, isolation and a stop (`SOC-ENV-54`).
    """

    gaps = submission_gaps(submissions, recorded_action_request_ids)
    if gaps["missing"]:
        raise EnvelopeRefusal(
            FailureCode.MISSING,
            f"submissions with no recorded effect: {list(gaps['missing'])}",
        )
    if gaps["unknown"]:
        raise EnvelopeRefusal(
            FailureCode.COMMIT_UNKNOWN,
            f"submissions whose commit state is unknown: {list(gaps['unknown'])}",
        )
