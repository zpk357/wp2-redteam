from __future__ import annotations

import json

import pytest

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.oracle import evaluate_scenario_oracle
from sandbox.scenarios.office_v2.oracle_evidence import (
    OracleEvidenceIntegrityError,
    build_oracle_evidence_bundle,
)
from sandbox.scenarios.office_v2.oracle_models import OracleFailureCode
from sandbox.scenarios.office_v2.oracle_trace import (
    build_oracle_evidence_from_trace,
    rebuild_oracle_evidence_bundle,
    rebuild_scenario_oracle_from_bundle,
)
from tests.unit.test_office_v2_oracle_trace import _recording
from tests.unit.test_office_v2_utility_evaluator import _t9_bundle, _t10_bundle


def test_direct_recording_and_replay_are_independently_rebuilt_and_equal() -> None:
    materialization, invocation, result, events, termination = _recording()
    case = materialization.scenario_case
    recording_digest = sha256_digest({"recording": "source.001"})
    replay_digest = sha256_digest({"normalized_replay": "matched.001"})
    common = {
        "scenario_case": case,
        "initialization_transition": materialization.initialization_transition,
        "invocations": (invocation,),
        "results": (result,),
        "interaction_facts": (),
        "termination": termination,
        "final_state_digest": result.after_state_digest,
        "recording_digest": recording_digest,
        "replay_digest": replay_digest,
    }
    direct = build_oracle_evidence_bundle(timeline=None, **common)
    recorded = build_oracle_evidence_from_trace(trace_events=events, **common)
    replay_events = tuple(
        event.model_copy(update={"execution_id": "execution.strict-replay.001"})
        for event in events
    )
    replayed = build_oracle_evidence_from_trace(
        trace_events=replay_events,
        **common,
    )

    rebuilt = tuple(
        rebuild_oracle_evidence_bundle(
            bundle.model_dump_json(),
            expected_bundle_digest=bundle.bundle_digest,
        )
        for bundle in (direct, recorded, replayed)
    )
    results = tuple(
        rebuild_scenario_oracle_from_bundle(
            bundle.model_dump_json(),
            expected_bundle_digest=bundle.bundle_digest,
            scenario_case=case,
        )
        for bundle in rebuilt
    )

    assert direct == recorded == replayed
    assert rebuilt[0] == rebuilt[1] == rebuilt[2]
    assert results[0].utility == results[1].utility == results[2].utility
    assert results[0].security == results[1].security == results[2].security
    assert (
        results[0].evidence_closure
        == results[1].evidence_closure
        == results[2].evidence_closure
    )
    assert results[0].result_digest == results[1].result_digest == results[2].result_digest
    assert results[0] == evaluate_scenario_oracle(bundle=direct, scenario_case=case)


def _assert_rebuild_rejects(
    payload: dict[str, object],
    expected_digest: str,
    expected_code: OracleFailureCode,
) -> None:
    with pytest.raises(OracleEvidenceIntegrityError) as failure:
        rebuild_oracle_evidence_bundle(
            json.dumps(payload, sort_keys=True),
            expected_bundle_digest=expected_digest,
        )
    assert failure.value.code is expected_code


def test_arguments_decision_transition_and_state_tampering_are_rejected() -> None:
    bundle, _ = _t10_bundle()
    source = bundle.model_dump(mode="json", exclude_none=False)
    expected = bundle.bundle_digest

    arguments = json.loads(json.dumps(source))
    arguments["tool_exchanges"][0]["arguments_digest"] = sha256_digest("changed")
    _assert_rebuild_rejects(arguments, expected, OracleFailureCode.DIGEST_MISMATCH)

    decision = json.loads(json.dumps(source))
    decision_exchange = next(
        item for item in decision["tool_exchanges"] if item["policy_decision"] is not None
    )
    decision_exchange["policy_decision"]["decision_digest"] = sha256_digest("changed")
    _assert_rebuild_rejects(decision, expected, OracleFailureCode.DIGEST_MISMATCH)

    transition = json.loads(json.dumps(source))
    transition_exchange = next(
        item for item in transition["tool_exchanges"] if item["state_transition"] is not None
    )
    transition_exchange["state_transition"]["transition_digest"] = sha256_digest(
        "changed"
    )
    _assert_rebuild_rejects(transition, expected, OracleFailureCode.DIGEST_MISMATCH)

    initial = json.loads(json.dumps(source))
    initial["identity"]["initial_state_digest"] = sha256_digest("changed")
    _assert_rebuild_rejects(initial, expected, OracleFailureCode.INVALID_STATE_CHAIN)

    final = json.loads(json.dumps(source))
    final["identity"]["final_state_digest"] = sha256_digest("changed")
    _assert_rebuild_rejects(final, expected, OracleFailureCode.INVALID_STATE_CHAIN)


def test_rehashed_objective_binding_tamper_fails_external_digest_lock() -> None:
    bundle, _ = _t10_bundle()
    payload = bundle.model_dump(mode="json", exclude_none=False)
    payload["objective_bindings"] = []
    without_digest = {key: value for key, value in payload.items() if key != "bundle_digest"}
    payload["bundle_digest"] = sha256_digest(without_digest)

    with pytest.raises(OracleEvidenceIntegrityError) as failure:
        rebuild_oracle_evidence_bundle(
            json.dumps(payload, sort_keys=True),
            expected_bundle_digest=bundle.bundle_digest,
        )
    assert failure.value.code is OracleFailureCode.DIGEST_MISMATCH


def test_interaction_grant_tampering_is_rejected() -> None:
    bundle, _ = _t9_bundle(authorized=True)
    payload = bundle.model_dump(mode="json", exclude_none=False)
    interaction = payload["interaction_facts"][0]
    assert interaction["transition_ref"] is not None
    interaction["transition_ref"]["evidence_digest"] = sha256_digest("changed")

    _assert_rebuild_rejects(
        payload,
        bundle.bundle_digest,
        OracleFailureCode.DIGEST_MISMATCH,
    )


def test_malformed_or_wrongly_locked_artifact_never_produces_a_partial_result() -> None:
    bundle, materialization = _t10_bundle()
    with pytest.raises(OracleEvidenceIntegrityError) as malformed:
        rebuild_scenario_oracle_from_bundle(
            "{not-json",
            expected_bundle_digest=bundle.bundle_digest,
            scenario_case=materialization.scenario_case,
        )
    assert malformed.value.code is OracleFailureCode.INVALID_CONTRACT

    with pytest.raises(OracleEvidenceIntegrityError) as wrong_lock:
        rebuild_scenario_oracle_from_bundle(
            bundle.model_dump_json(),
            expected_bundle_digest=sha256_digest("wrong-lock"),
            scenario_case=materialization.scenario_case,
        )
    assert wrong_lock.value.code is OracleFailureCode.DIGEST_MISMATCH

    sensitive = "SENSITIVE-VALIDATION-INPUT-DO-NOT-LOG"
    payload = bundle.model_dump(mode="json", exclude_none=False)
    payload["unexpected_sensitive_field"] = sensitive
    with pytest.raises(OracleEvidenceIntegrityError) as extra_field:
        rebuild_oracle_evidence_bundle(
            json.dumps(payload),
            expected_bundle_digest=bundle.bundle_digest,
        )
    assert extra_field.value.code is OracleFailureCode.INVALID_CONTRACT
    assert sensitive not in extra_field.value.detail
