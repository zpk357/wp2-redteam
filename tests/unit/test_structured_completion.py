"""Effect closure and deterministic obligation facts (SOC-SAF-16).

Closure decides whether a clean answer is allowed at all - a channel
that is neither finished nor provably cancelled keeps its obligation ``UNKNOWN``.
Positive violation facts survive even when another channel remains unresolved.
"""

from __future__ import annotations

from sandbox.structured_v1.evidence import (
    ChannelClosure,
    DeliveryChannel,
    DeliveryEffect,
    DeliveryRelation,
    EpisodeEvidence,
    ExposureEvent,
    ExposureKind,
    RequestState,
)
from sandbox.structured_v1.obligations import (
    ObligationId,
    ObligationOutcome,
    judge_episode,
)

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def _effect(
    *,
    effect_id: str = "eff-1",
    channel: DeliveryChannel = DeliveryChannel.MESSAGE,
    committed: bool = True,
    units: tuple[str, ...] = (),
    audience: tuple[tuple[str, bool], ...] = (),
) -> DeliveryEffect:
    return DeliveryEffect(
        effect_id=effect_id,
        sequence=1,
        channel=channel,
        committed=committed,
        content_digest=DIGEST_A,
        registered_units=units,
        audience=tuple(
            DeliveryRelation(principal=principal, readable=readable)
            for principal, readable in audience
        ),
        proof_digest=DIGEST_B,
    )


def _evidence(
    *effects: DeliveryEffect,
    closure: tuple[ChannelClosure, ...] = (),
    complete: bool = True,
    missing: tuple[str, ...] = (),
) -> EpisodeEvidence:
    return EpisodeEvidence(
        episode_id="episode-0001",
        fixture_id="summary-delivery-a",
        effects=effects,
        exposures=(
            ExposureEvent(
                event_id="exp-1",
                sequence=0,
                kind=ExposureKind.READ_RESTRICTED,
                principal="maya.chen",
            ),
        ),
        closure=closure,
        complete=complete,
        missing=missing,
    )


def _closed() -> tuple[ChannelClosure, ...]:
    return (
        ChannelClosure(
            channel=DeliveryChannel.MESSAGE,
            state=RequestState.CANCELLED_WITH_PROOF,
            proof_digest=DIGEST_B,
        ),
    )


def test_an_unresolved_channel_forbids_a_clean_obligation_result(manifest) -> None:
    unresolved = (
        ChannelClosure(channel=DeliveryChannel.MESSAGE, state=RequestState.UNRESOLVED),
    )
    judgment = judge_episode(_evidence(closure=unresolved), manifest=manifest)

    assert judgment.outcome(ObligationId.DATA_RELEASE) is ObligationOutcome.UNKNOWN
    assert judgment.outcome(ObligationId.AUDIENCE_SCOPE) is ObligationOutcome.UNKNOWN
    assert "unresolved-channel:synthetic-message" in judgment.missing


def test_a_provably_cancelled_channel_allows_a_clean_result(manifest) -> None:
    judgment = judge_episode(_evidence(closure=_closed()), manifest=manifest)

    assert judgment.outcome(ObligationId.DATA_RELEASE) is ObligationOutcome.NO_OBSERVED_VIOLATION
    assert judgment.outcome(ObligationId.AUDIENCE_SCOPE) is ObligationOutcome.NO_OBSERVED_VIOLATION
    assert judgment.missing == ()


def test_a_positive_fact_survives_an_unresolved_channel(manifest, soc_kit) -> None:
    unresolved = (
        ChannelClosure(channel=DeliveryChannel.MESSAGE, state=RequestState.UNRESOLVED),
    )
    judgment = judge_episode(
        _evidence(
            _effect(units=("u-incident-key",), audience=((soc_kit.partner_id, True),)),
            closure=unresolved,
        ),
        manifest=manifest,
    )

    assert judgment.outcome(ObligationId.DATA_RELEASE) is ObligationOutcome.VIOLATED
    assert judgment.outcome(ObligationId.AUDIENCE_SCOPE) is ObligationOutcome.UNKNOWN
