from __future__ import annotations

from copy import deepcopy

import pytest
from app.office_v2_session import OfficeV2SessionSnapshot, load_office_v2_session
from pydantic import ValidationError

from sandbox.protocol import ModelOptions, V2ExecutionEnvelope
from sandbox.scenarios.office_v2.attack_cases import build_representative_scenario_fixtures
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASE_BY_ID
from sandbox.scenarios.office_v2.execution_contracts import build_v2_execution_envelope


def _model() -> ModelOptions:
    return ModelOptions(provider="fake", model_name="stage7-scripted")


def _clean_envelope() -> V2ExecutionEnvelope:
    canonical = load_canonical_world()
    return build_v2_execution_envelope(
        CLEAN_CASE_BY_ID["clean.t1.apollo"],
        initial_state=canonical.state,
        model_identity=_model(),
    )


def test_session_has_one_episode_state_owner_and_frozen_case() -> None:
    envelope = _clean_envelope()
    session = load_office_v2_session(envelope, episode_id="episode.stage7.clean")

    assert session.runtime.episode is session.episode
    assert session.runtime.state is session.episode.state
    assert session.scenario_case.case_id == envelope.scenario_case_id
    assert session.episode.state_digest == envelope.initial_state_digest
    assert len(session.runtime.definitions) == 17


def test_two_sessions_are_isolated_and_canonical_world_is_unchanged() -> None:
    canonical_digest = load_canonical_world().world_digest
    envelope = _clean_envelope()
    left = load_office_v2_session(envelope, episode_id="episode.stage7.left")
    right = load_office_v2_session(envelope, episode_id="episode.stage7.right")

    transaction = left.episode.begin_transaction()
    transaction.allocate_id("stage7.probe")
    transaction.commit()

    assert left.episode is not right.episode
    assert left.episode.state is not right.episode.state
    assert left.episode.state_digest != right.episode.state_digest
    assert right.episode.state_digest == envelope.initial_state_digest
    assert load_canonical_world().world_digest == canonical_digest


def test_export_and_restore_preserve_state_and_transaction_chain() -> None:
    envelope = _clean_envelope()
    session = load_office_v2_session(envelope, episode_id="episode.stage7.restore")
    transaction = session.episode.begin_transaction()
    transaction.allocate_id("stage7.restore-probe")
    transaction.commit()
    snapshot = session.export_state()

    restored = load_office_v2_session(
        envelope,
        episode_id="episode.stage7.restore",
        snapshot=snapshot,
    )

    assert restored.episode.state_digest == session.episode.state_digest
    assert restored.episode.history == session.episode.history
    assert restored.export_state().snapshot_digest == snapshot.snapshot_digest


def test_attack_session_keeps_initialization_transition_outside_agent_history() -> None:
    fixture = next(
        item
        for item in build_representative_scenario_fixtures()
        if item.materialization.initialization_transition is not None
    )
    envelope = build_v2_execution_envelope(
        fixture.scenario_case,
        initial_state=fixture.materialization.initial_state,
        initialization_transition=fixture.materialization.initialization_transition,
        model_identity=_model(),
    )

    session = load_office_v2_session(envelope, episode_id="episode.stage7.attack")

    assert session.initialization_transition == fixture.materialization.initialization_transition
    assert session.episode.history == ()
    assert session.episode.state_digest == fixture.scenario_case.initial_world_digest


def test_snapshot_tampering_and_cross_envelope_restore_are_rejected() -> None:
    envelope = _clean_envelope()
    session = load_office_v2_session(envelope, episode_id="episode.stage7.tamper")
    payload = session.export_state().model_dump(mode="json")
    payload["state"]["next_id_sequence"] += 1
    with pytest.raises(ValidationError, match="state digest does not match state"):
        OfficeV2SessionSnapshot.model_validate(payload)

    changed = deepcopy(envelope.model_dump(mode="json"))
    changed["model_identity"]["model_name"] = "other-model"
    other = V2ExecutionEnvelope.model_validate(changed)
    with pytest.raises(ValueError, match="snapshot envelope mismatch"):
        load_office_v2_session(
            other,
            episode_id="episode.stage7.tamper",
            snapshot=session.export_state(),
        )
