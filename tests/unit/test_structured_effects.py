"""P3 acceptance: binding, retry and projection of commit-time effect evidence.

The rules under test are the ones that keep an effect from being believed on its own
word: it must be carried by a committed transition that also created the objects it
names, the initialization transaction can never be a delivery, the ledger is exactly the
projection of the records, and a retry returns the receipt instead of adding an entry or
being refused.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sandbox.scenarios.office_v2.canonical_world import (
    CanonicalOfficeWorld,
    OfficeWorldState,
    build_canonical_world,
)
from sandbox.scenarios.office_v2.models import (
    IdentityDirectory,
    OfficeDomainGraph,
    Organization,
    Principal,
    PrincipalKind,
    Sensitivity,
)
from sandbox.structured_v1.effects import (
    RetryDecision,
    append_effect,
    capture_effect,
    retry_decision,
    same_content,
    verify_ledger,
    verify_projection,
)
from sandbox.structured_v1.envelope_codes import CODE_PHASE, EnvelopeRefusal, FailureCode
from sandbox.structured_v1.evidence import (
    DeliveryChannel,
    DeliveryEffect,
    DeliveryRelation,
    EffectKey,
    EffectLedger,
    RelatedResourceRef,
)
from sandbox.structured_v1.rendering import render_material
from sandbox.structured_v1.transitions import (
    StructuredTransitionRecord,
    initialized_transition_record,
    record_transition,
    verify_chain,
)
from sandbox.structured_v1.world import (
    FixtureWorldOverlay,
    OverlayDriveFile,
    OverlayWorkspaceFile,
    SlotFieldKind,
    SlotWorldTarget,
    materialize_world,
)

NOW = datetime(2026, 9, 21, 8, tzinfo=UTC)
S1_FILE = "f-overview"
S2_PATH = "/workspace/notes/delivery-notes.md"
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
DIGEST_D = "sha256:" + "d" * 64
INITIALIZATION = "transaction.episode-0001.000000"
T1 = "transaction.episode-0001.000001"
T2 = "transaction.episode-0001.000002"
PARTNER = "partner.contact"

TRIGGERED: set[FailureCode] = set()

P3_CODES = {
    FailureCode.UNBOUND,
    FailureCode.KEY_CONFLICT,
    FailureCode.INITIALIZATION_CITED_AS_DELIVERY,
    FailureCode.CHAIN_BROKEN,
}


def _effect(
    *,
    action_request_id: str = "action-0001",
    ordinal: int = 0,
    sequence: int = 1,
    committed: bool = True,
    blocked: bool = False,
    channel: DeliveryChannel = DeliveryChannel.MESSAGE,
    content_digest: str = DIGEST_A,
    created_objects: tuple[str, ...] = ("message-0001",),
    audience_readable: bool = True,
) -> DeliveryEffect:
    return capture_effect(
        key=EffectKey(action_request_id=action_request_id, ordinal=ordinal),
        sequence=sequence,
        channel=channel,
        committed=committed,
        blocked=blocked,
        content_digest=content_digest,
        proof_digest=DIGEST_B,
        registered_units=("u-incident-key",),
        created_objects=created_objects,
        audience=(DeliveryRelation(principal=PARTNER, readable=audience_readable),),
    )


def test_capture_effect_records_the_named_sources() -> None:
    """The sources a delivery named are captured as evidence, never as an edge by themselves."""

    ref = RelatedResourceRef(kind="drive_file", resource_id="f-1")
    effect = capture_effect(
        key=EffectKey(action_request_id="action-0001"),
        sequence=1,
        channel=DeliveryChannel.MESSAGE,
        committed=True,
        content_digest=DIGEST_A,
        proof_digest=DIGEST_B,
        related_refs=(ref,),
        audience=(DeliveryRelation(principal=PARTNER, readable=True),),
    )

    assert effect.related_refs == (ref,)


def _record(
    transaction_id: str,
    *,
    sequence: int,
    committed: bool = True,
    created: tuple[str, ...] = ("message-0001",),
    effects: tuple[DeliveryEffect, ...] = (),
    before: str = DIGEST_C,
    after: str = DIGEST_D,
) -> StructuredTransitionRecord:
    return StructuredTransitionRecord(
        sequence=sequence,
        transaction_id=transaction_id,
        committed=committed,
        world_transition_digest=DIGEST_A,
        before_state_digest=before,
        after_state_digest=after,
        created_object_ids=created,
        effects=effects,
    )


def _ledger(entries: tuple[tuple[str, DeliveryEffect, EffectKey], ...] = ()) -> EffectLedger:
    ledger = EffectLedger(episode_id="episode-0001")
    for transaction_id, effect, key in entries:
        ledger = ledger.append(effect, transaction_id=transaction_id, key=key)
    return ledger


def _refusal(call, *args, **kwargs) -> FailureCode:
    with pytest.raises(EnvelopeRefusal) as caught:
        call(*args, **kwargs)
    TRIGGERED.add(caught.value.code)
    return caught.value.code


# --- capture -----------------------------------------------------------------------


def test_capture_is_content_addressed_and_stable() -> None:
    first = _effect()
    assert first is not None
    assert first.effect_id == _effect().effect_id
    assert first.effect_id != _effect(content_digest=DIGEST_D).effect_id
    assert first.is_delivery() is True
    assert same_content(first, _effect(sequence=9)) is True


def test_a_committed_delivery_needs_a_reader() -> None:
    assert _refusal(_effect, audience_readable=False) is FailureCode.UNBOUND
    private = _effect(
        channel=DeliveryChannel.ACTOR_PRIVATE, audience_readable=False, created_objects=()
    )
    assert private.is_delivery() is False


def test_contradictory_outcomes_are_refused() -> None:
    assert _refusal(_effect, committed=True, blocked=True) is FailureCode.UNBOUND
    assert _refusal(_effect, committed=False) is FailureCode.UNBOUND
    uncommitted = _effect(committed=False, created_objects=())
    assert uncommitted.is_delivery() is False


# --- retry -------------------------------------------------------------------------


def test_a_same_key_same_content_retry_returns_the_receipt() -> None:
    key = EffectKey(action_request_id="action-0001", ordinal=0)
    effect = _effect()
    ledger = _ledger(((T1, effect, key),))

    assert retry_decision(ledger, key, _effect(sequence=7)) is RetryDecision.RETURN_RECEIPT
    assert append_effect(ledger, _effect(sequence=7), key=key, transaction_id=T2) == ledger
    assert len(ledger.entries) == 1


def test_a_same_key_different_content_retry_is_refused() -> None:
    key = EffectKey(action_request_id="action-0001", ordinal=0)
    ledger = _ledger(((T1, _effect(), key),))

    assert _refusal(retry_decision, ledger, key, _effect(content_digest=DIGEST_D)) is (
        FailureCode.KEY_CONFLICT
    )


def test_a_new_key_appends() -> None:
    key = EffectKey(action_request_id="action-0001", ordinal=0)
    ledger = _ledger(((T1, _effect(), key),))
    other = EffectKey(action_request_id="action-0002", ordinal=0)

    assert retry_decision(ledger, other, _effect(action_request_id="action-0002")) is (
        RetryDecision.APPEND
    )
    grown = append_effect(
        ledger, _effect(action_request_id="action-0002"), key=other, transaction_id=T2
    )
    assert len(grown.entries) == 2
    assert grown.entries_for(other) != ()


# --- binding and projection --------------------------------------------------------


def test_the_ledger_must_be_the_projection_of_the_records() -> None:
    effect = _effect()
    records = (_record(T1, sequence=0, effects=(effect,)),)
    ledger = _ledger(((T1, effect, EffectKey(action_request_id="action-0001")),))

    verify_projection(ledger, records)
    extra = _ledger(
        (
            (T1, effect, EffectKey(action_request_id="action-0001")),
            (
                T2,
                _effect(action_request_id="action-0002"),
                EffectKey(action_request_id="action-0002"),
            ),
        )
    )
    assert _refusal(verify_projection, extra, records) is FailureCode.UNBOUND


def test_an_entry_must_cite_a_recorded_committed_transition() -> None:
    effect = _effect()
    key = EffectKey(action_request_id="action-0001")
    ledger = _ledger(((T1, effect, key),))
    records = (_record(T1, sequence=0, effects=(effect,)),)

    verify_ledger(ledger, records, initialization_transaction_id=INITIALIZATION)
    assert _refusal(
        verify_ledger, ledger, (), initialization_transaction_id=INITIALIZATION
    ) is FailureCode.UNBOUND

    failed = (
        _record(T1, sequence=0, committed=False, created=(), effects=(), after=DIGEST_C),
    )
    assert _refusal(
        verify_ledger, ledger, failed, initialization_transaction_id=INITIALIZATION
    ) is FailureCode.UNBOUND


def test_the_initialization_transaction_is_never_a_delivery() -> None:
    effect = _effect()
    key = EffectKey(action_request_id="action-0001")
    ledger = _ledger(((INITIALIZATION, effect, key),))
    records = (_record(INITIALIZATION, sequence=0, effects=(effect,)),)

    assert _refusal(
        verify_ledger, ledger, records, initialization_transaction_id=INITIALIZATION
    ) is FailureCode.INITIALIZATION_CITED_AS_DELIVERY


def test_an_effect_must_be_carried_by_its_transition() -> None:
    effect = _effect()
    key = EffectKey(action_request_id="action-0001")
    ledger = _ledger(((T1, effect, key),))
    records = (_record(T1, sequence=0, effects=()),)

    assert _refusal(
        verify_ledger, ledger, records, initialization_transaction_id=INITIALIZATION
    ) is FailureCode.UNBOUND


def test_created_objects_must_be_in_the_transition() -> None:
    effect = _effect(created_objects=("message-0001", "message-0002"))
    key = EffectKey(action_request_id="action-0001")
    ledger = _ledger(((T1, effect, key),))
    records = (_record(T1, sequence=0, created=("message-0001",), effects=(effect,)),)

    assert _refusal(
        verify_ledger, ledger, records, initialization_transaction_id=INITIALIZATION
    ) is FailureCode.UNBOUND


# --- the chain ---------------------------------------------------------------------


def test_the_chain_must_be_contiguous_from_the_initialization() -> None:
    first = _record(T1, sequence=0, before=DIGEST_C, after=DIGEST_D)
    second = _record(T2, sequence=1, before=DIGEST_D, after=DIGEST_A)

    verify_chain((first, second), expected_start_state_digest=DIGEST_C)
    assert _refusal(
        verify_chain, (first, second), expected_start_state_digest=DIGEST_B
    ) is FailureCode.CHAIN_BROKEN
    assert _refusal(
        verify_chain,
        (first, _record(T2, sequence=2, before=DIGEST_D, after=DIGEST_A)),
        expected_start_state_digest=DIGEST_C,
    ) is FailureCode.CHAIN_BROKEN
    assert _refusal(
        verify_chain,
        (first, _record(T2, sequence=1, before=DIGEST_A, after=DIGEST_A)),
        expected_start_state_digest=DIGEST_C,
    ) is FailureCode.CHAIN_BROKEN
    assert _refusal(
        verify_chain,
        (_record(T1, sequence=1, before=DIGEST_C, after=DIGEST_D),),
        expected_start_state_digest=DIGEST_C,
    ) is FailureCode.CHAIN_BROKEN


# --- the adapter to the world's own transitions ------------------------------------


def _base_world() -> CanonicalOfficeWorld:
    organization = Organization(
        organization_id="org.example",
        name="Example Corporation",
        primary_domain="example.test",
        external_domains=("partner.test",),
    )
    graph = OfficeDomainGraph(
        directory=IdentityDirectory(
            organization=organization,
            principals=(
                Principal(
                    principal_id="maya.chen",
                    kind=PrincipalKind.USER,
                    display_name="Maya Chen",
                    email="maya@example.test",
                    organization_id="org.example",
                ),
            ),
        )
    )
    return build_canonical_world(OfficeWorldState(domain_graph=graph))


def _overlay() -> FixtureWorldOverlay:
    return FixtureWorldOverlay(
        drive_files=(
            OverlayDriveFile(
                file_id=S1_FILE,
                version_id="f-overview-v1",
                name="overview.md",
                mime_type="text/markdown",
                owner_id="maya.chen",
                classification=Sensitivity.PUBLIC,
                created_at=NOW,
            ),
        ),
        workspace_files=(
            OverlayWorkspaceFile(
                path=S2_PATH,
                owner_id="maya.chen",
                media_type="text/markdown",
                created_at=NOW,
            ),
        ),
        slot_targets=(
            SlotWorldTarget(
                slot_id="s1",
                field_kind=SlotFieldKind.DRIVE_FILE_CONTENT,
                object_id=S1_FILE,
            ),
            SlotWorldTarget(
                slot_id="s2",
                field_kind=SlotFieldKind.WORKSPACE_FILE_CONTENT,
                object_id=S2_PATH,
            ),
        ),
    )


def test_a_real_transition_becomes_a_record_without_effects(manifest, root_case) -> None:
    """The initialization writes the world; it never delivers (`SOC-ENV-76`)."""

    base = _base_world()
    materialized = materialize_world(
        manifest,
        render_material(root_case, manifest),
        base_world=base,
        overlay=_overlay(),
        episode_id="episode-0001",
    )
    record = initialized_transition_record(materialized)

    assert record.committed is True
    assert record.effects == ()
    assert record.created_object_ids
    assert record.transaction_id == materialized.initialization_transition.transaction_id
    verify_chain((record,), expected_start_state_digest=record.before_state_digest)

    failed = materialized.initialization_transition.model_copy(update={"committed": False})
    assert _refusal(record_transition, failed, sequence=0, effects=(_effect(),)) is (
        FailureCode.UNBOUND
    )


def test_a_failed_transition_cannot_carry_effects() -> None:
    with pytest.raises(ValueError, match="cannot deliver"):
        StructuredTransitionRecord(
            sequence=0,
            transaction_id=T1,
            committed=False,
            world_transition_digest=DIGEST_A,
            before_state_digest=DIGEST_C,
            after_state_digest=DIGEST_C,
            effects=(_effect(),),
        )


def test_a_blocked_attempt_may_cite_a_transition_that_did_not_commit() -> None:
    """`SOC-SAF-13`: a refused call is a fact to keep, and it is not a delivery.

    The refusal above is about *deliveries*; an attempt that the platform blocked has no
    readable audience, so it is recorded against the transition that refused it and stays
    visible to the judgement as an attempt/block.
    """

    key = EffectKey(action_request_id="action-0001")
    blocked = _effect(committed=False, blocked=True, created_objects=())
    record = _record(
        T1, sequence=1, committed=False, created=(), effects=(blocked,),
        before=DIGEST_C, after=DIGEST_C,
    )
    ledger = _ledger(((T1, blocked, key),))

    assert blocked.is_delivery() is False
    verify_ledger(ledger, (record,), initialization_transaction_id=INITIALIZATION)
    assert record.delivery_effects() == ()


def test_every_p3_code_is_triggered_here() -> None:
    assert {code for code, phase in CODE_PHASE.items() if phase == "P3"} == P3_CODES
    assert P3_CODES <= TRIGGERED
