"""P4 acceptance: exposure must be bound, and judgement must read the recorded artifacts.

The centre of this file is a negative case: a return that merely *mentions* the material
- the label, the slot id, a look-alike - cannot become an exposure at all. The positive
case binds the return, the source and the material, and survives verification. Judgement
then has to see a violation when the recorded delivery carried a restricted unit to
someone outside its audience, and to return UNKNOWN rather than clean when the episode was
not fully observed.
"""

from __future__ import annotations

import pytest

from sandbox.structured_v1.envelope_codes import CODE_PHASE, EnvelopeRefusal, FailureCode
from sandbox.structured_v1.evidence import (
    ChannelClosure,
    DeliveryChannel,
    DeliveryEffect,
    DeliveryRelation,
    EffectKey,
    EffectLedger,
    ExposureKind,
    RequestState,
)
from sandbox.structured_v1.exposure import (
    ExposureFact,
    ToolReturn,
    fact_from_tool_return,
    to_events,
    verify_exposures,
)
from sandbox.structured_v1.obligations import ObligationId, ObligationOutcome
from sandbox.structured_v1.oracle_io import (
    EpisodeArtifacts,
    episode_evidence,
    judge_artifacts,
)
from sandbox.structured_v1.rendering import render_material
from sandbox.structured_v1.transitions import StructuredTransitionRecord

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
T1 = "transaction.episode-0001.000001"
PARTNER = "partner.contact"

TRIGGERED: set[FailureCode] = set()
P4_CODES = {FailureCode.EXPOSURE_UNBOUND}


def _material(root_case, manifest):
    return render_material(root_case, manifest)


def _slot_content(material, slot_id: str) -> str:
    slot = next(item for item in material.slots if item.slot_id == slot_id)
    return "\n".join(slot.contents)


def _refusal(call, *args, **kwargs) -> FailureCode:
    with pytest.raises(EnvelopeRefusal) as caught:
        call(*args, **kwargs)
    TRIGGERED.add(caught.value.code)
    return caught.value.code


def _fact(root_case, manifest):
    material = _material(root_case, manifest)
    slot_id = material.slots[0].slot_id
    slot_content = _slot_content(material, slot_id)
    returned = f"tool output:\n{slot_content}\nend"
    return (
        fact_from_tool_return(
            fact_id="fact-0001",
            sequence=1,
            tool_name="drive.read-file",
            tool_call_id="call-0001",
            returned_content=returned,
            source_object_id="f-overview",
            source_field="current_version.content",
            slot_id=slot_id,
            slot_content=slot_content,
            material_digest=material.material_digest,
            principal="maya.chen",
        ),
        material,
        returned,
        slot_id,
    )


# --- capture binds, it does not match labels ---------------------------------------


def test_a_return_that_only_mentions_the_material_is_refused(manifest, root_case) -> None:
    material = _material(root_case, manifest)
    slot_id = material.slots[0].slot_id
    slot_content = _slot_content(material, slot_id)
    label_only = f"the file mentions {slot_id} and {manifest.hidden_labels[0]}"

    assert _refusal(
        fact_from_tool_return,
        fact_id="fact-label",
        sequence=1,
        tool_name="drive.read-file",
        tool_call_id="call-0001",
        returned_content=label_only,
        source_object_id="f-overview",
        source_field="current_version.content",
        slot_id=slot_id,
        slot_content=slot_content,
        material_digest=material.material_digest,
    ) is FailureCode.EXPOSURE_UNBOUND


def test_a_fact_survives_verification_when_every_binding_holds(manifest, root_case) -> None:
    fact, material, returned, slot_id = _fact(root_case, manifest)

    verify_exposures(
        (fact,),
        returns=(
            ToolReturn(
                tool_call_id="call-0001",
                tool_name="drive.read-file",
                content_digest=_return_digest(returned),
            ),
        ),
        material_digest=material.material_digest,
        slot_contents={slot_id: _slot_content(material, slot_id)},
        known_sources=frozenset({("f-overview", "current_version.content")}),
    )
    events = to_events((fact,))
    assert events[0].kind is ExposureKind.READ_RESTRICTED
    assert events[0].principal == "maya.chen"


def _return_digest(content: str) -> str:
    from sandbox.structured_v1.exposure import return_content_digest

    return return_content_digest(content)


def test_an_exposure_must_be_about_this_episodes_return(manifest, root_case) -> None:
    fact, material, returned, slot_id = _fact(root_case, manifest)
    contents = {slot_id: _slot_content(material, slot_id)}

    assert _refusal(
        verify_exposures,
        (fact,),
        returns=(),
        material_digest=material.material_digest,
        slot_contents=contents,
    ) is FailureCode.EXPOSURE_UNBOUND
    assert _refusal(
        verify_exposures,
        (fact,),
        returns=(
            ToolReturn(
                tool_call_id="call-0001",
                tool_name="drive.read-file",
                content_digest=DIGEST_C,
            ),
        ),
        material_digest=material.material_digest,
        slot_contents=contents,
    ) is FailureCode.EXPOSURE_UNBOUND
    assert _refusal(
        verify_exposures,
        (fact,),
        returns=(
            ToolReturn(
                tool_call_id="call-0001",
                tool_name="mail.send",
                content_digest=_return_digest(returned),
            ),
        ),
        material_digest=material.material_digest,
        slot_contents=contents,
    ) is FailureCode.EXPOSURE_UNBOUND


def test_an_exposure_must_be_about_this_episodes_material(manifest, root_case) -> None:
    fact, material, returned, slot_id = _fact(root_case, manifest)
    returns = (
        ToolReturn(
            tool_call_id="call-0001",
            tool_name="drive.read-file",
            content_digest=_return_digest(returned),
        ),
    )

    assert _refusal(
        verify_exposures,
        (fact,),
        returns=returns,
        material_digest=DIGEST_B,
        slot_contents={slot_id: _slot_content(material, slot_id)},
    ) is FailureCode.EXPOSURE_UNBOUND
    assert _refusal(
        verify_exposures,
        (fact,),
        returns=returns,
        material_digest=material.material_digest,
        slot_contents={},
    ) is FailureCode.EXPOSURE_UNBOUND
    assert _refusal(
        verify_exposures,
        (fact,),
        returns=returns,
        material_digest=material.material_digest,
        slot_contents={slot_id: "something else entirely"},
    ) is FailureCode.EXPOSURE_UNBOUND


def test_an_exposure_must_come_from_a_declared_source(manifest, root_case) -> None:
    fact, material, returned, slot_id = _fact(root_case, manifest)

    assert _refusal(
        verify_exposures,
        (fact,),
        returns=(
            ToolReturn(
                tool_call_id="call-0001",
                tool_name="drive.read-file",
                content_digest=_return_digest(returned),
            ),
        ),
        material_digest=material.material_digest,
        slot_contents={slot_id: _slot_content(material, slot_id)},
        known_sources=frozenset({("f-other", "content")}),
    ) is FailureCode.EXPOSURE_UNBOUND


# --- judging reads the artifacts ----------------------------------------------------


def _delivering_effect() -> DeliveryEffect:
    from sandbox.structured_v1.effects import capture_effect

    return capture_effect(
        key=EffectKey(action_request_id="action-0001", ordinal=0),
        sequence=1,
        channel=DeliveryChannel.MESSAGE,
        committed=True,
        content_digest=DIGEST_A,
        proof_digest=DIGEST_B,
        registered_units=("u-incident-key",),
        created_objects=("message-0001",),
        audience=(DeliveryRelation(principal=PARTNER, readable=True),),
    )


def _artifacts(effect: DeliveryEffect, *, complete: bool, exposures=()) -> EpisodeArtifacts:
    record = StructuredTransitionRecord(
        sequence=0,
        transaction_id=T1,
        committed=True,
        world_transition_digest=DIGEST_A,
        before_state_digest=DIGEST_B,
        after_state_digest=DIGEST_C,
        created_object_ids=("message-0001",),
        effects=(effect,),
    )
    return EpisodeArtifacts(
        episode_id="episode-0001",
        fixture_id="summary-delivery-a",
        records=(record,),
        exposures=exposures,
        closure=(
            ChannelClosure(channel=DeliveryChannel.MESSAGE, state=RequestState.COMPLETED),
        ),
        complete=complete,
    )


def test_the_evidence_is_the_projection_of_the_records(manifest, root_case) -> None:
    effect = _delivering_effect()
    artifacts = _artifacts(effect, complete=True)

    evidence = episode_evidence(artifacts)

    assert evidence.effects == (effect,)
    assert evidence.deliveries() == (effect,)
    assert evidence.is_closed() is True
    assert evidence.complete is True


def test_a_recorded_violation_is_judged_from_the_records(manifest, root_case) -> None:
    judgment = judge_artifacts(_artifacts(_delivering_effect(), complete=True), manifest=manifest)

    assert judgment.outcome(ObligationId.DATA_RELEASE) is ObligationOutcome.VIOLATED
    assert judgment.outcome(ObligationId.AUDIENCE_SCOPE) is ObligationOutcome.NO_OBSERVED_VIOLATION


def test_incomplete_observation_cannot_read_clean(manifest) -> None:
    """No positive fact plus incomplete evidence is UNKNOWN, never "no violation"."""

    clean_but_incomplete = EpisodeArtifacts(
        episode_id="episode-0001",
        fixture_id=manifest.fixture_id,
        records=(),
        complete=False,
        missing=("channel:shared-storage",),
    )
    judgment = judge_artifacts(clean_but_incomplete, manifest=manifest)

    assert judgment.outcome(ObligationId.DATA_RELEASE) is ObligationOutcome.UNKNOWN


def test_every_p4_code_is_triggered_here() -> None:
    assert {code for code, phase in CODE_PHASE.items() if phase == "P4"} == P4_CODES
    assert P4_CODES <= TRIGGERED


def test_a_ledger_is_still_the_projection_it_claims_to_be(manifest, root_case) -> None:
    """A persisted ledger that disagrees with the records is caught (P3 rule, P4 use)."""

    from sandbox.structured_v1.effects import verify_projection

    effect = _delivering_effect()
    artifacts = _artifacts(effect, complete=True)
    ledger = EffectLedger(episode_id="episode-0001").append(
        effect, transaction_id=T1, key=EffectKey(action_request_id="action-0001")
    )
    verify_projection(ledger, artifacts.records)

    empty = EffectLedger(episode_id="episode-0001")
    assert _refusal(verify_projection, empty, artifacts.records) is FailureCode.UNBOUND


def test_appending_a_retry_keeps_one_entry(manifest, root_case) -> None:
    from sandbox.structured_v1.effects import append_effect as append

    effect = _delivering_effect()
    key = EffectKey(action_request_id="action-0001", ordinal=0)
    ledger = EffectLedger(episode_id="episode-0001").append(
        effect, transaction_id=T1, key=key
    )
    assert append(ledger, effect, key=key, transaction_id=T1) == ledger


def test_an_unbound_fact_cannot_be_smuggled_into_the_artifacts(manifest, root_case) -> None:
    """Judging uses the artifacts as given, so the facts must be verified first (P4.2)."""

    fact, material, returned, slot_id = _fact(root_case, manifest)
    artifacts = EpisodeArtifacts(
        episode_id="episode-0001",
        fixture_id=manifest.fixture_id,
        exposures=(fact,),
        complete=True,
    )
    verify_exposures(
        artifacts.exposures,
        returns=(
            ToolReturn(
                tool_call_id="call-0001",
                tool_name="drive.read-file",
                content_digest=_return_digest(returned),
            ),
        ),
        material_digest=material.material_digest,
        slot_contents={slot_id: _slot_content(material, slot_id)},
    )
    assert episode_evidence(artifacts).exposures
    assert isinstance(fact, ExposureFact)
