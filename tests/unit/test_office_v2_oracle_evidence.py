from __future__ import annotations

import pytest

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.attack_cases import (
    build_representative_scenario_fixtures,
)
from sandbox.scenarios.office_v2.oracle_evidence import (
    EpisodeTimelineEntry,
    InteractionEvidenceKind,
    OracleEvidenceBundle,
    OracleEvidenceIntegrityError,
    TimelineEntryKind,
    build_interaction_evidence_fact,
    build_oracle_evidence_bundle,
    build_termination_fact,
)
from sandbox.scenarios.office_v2.oracle_models import (
    OracleFailureCode,
    StateTransitionEvidenceRef,
)
from sandbox.scenarios.office_v2.tools.contracts import (
    OfficeToolInvocation,
    OutputEvidence,
    ToolFailureCode,
    ToolResultStatus,
    build_tool_result,
)
from sandbox.scenarios.office_v2.world import StateDelta, StateTransitionRecord


def _materialization():
    return build_representative_scenario_fixtures()[0].materialization


def _termination(sequence: int = 1):
    return build_termination_fact(
        evidence_id="termination.submit.001",
        sequence=sequence,
        reason="submit",
        submitted=True,
        output_digest=sha256_digest({"status": "done"}),
    )


def _read_exchange(secret: str = "SENSITIVE-CONTENT-DO-NOT-PERSIST"):
    materialization = _materialization()
    case = materialization.scenario_case
    invocation = OfficeToolInvocation(
        invocation_id="invocation.read.001",
        sequence=0,
        tool_name="read_drive_file",
        actor_id=case.actor.actor_id,
        task_id=case.task.task_id,
        logical_time=1,
        arguments={"file_id": "drive.apollo.meeting-pack", "marker": secret},
        arguments_digest=sha256_digest(
            {"file_id": "drive.apollo.meeting-pack", "marker": secret}
        ),
        before_state_digest=case.initial_world_digest,
    )
    output = {"content": secret, "file_id": "drive.apollo.meeting-pack"}
    result = build_tool_result(
        invocation=invocation,
        status=ToolResultStatus.SUCCEEDED,
        visible_output=output,
        output_evidence=(
            OutputEvidence(
                evidence_id="evidence.output.read.001",
                invocation_id=invocation.invocation_id,
                invocation_sequence=0,
                field_path=("content",),
                value_digest=sha256_digest(secret),
            ),
        ),
    )
    return materialization, invocation, result


def test_successful_read_bundle_is_redacted_round_trippable_and_digest_closed() -> None:
    secret = "SENSITIVE-CONTENT-DO-NOT-PERSIST"
    materialization, invocation, result = _read_exchange(secret)
    bundle = build_oracle_evidence_bundle(
        scenario_case=materialization.scenario_case,
        initialization_transition=materialization.initialization_transition,
        invocations=(invocation,),
        results=(result,),
        interaction_facts=(),
        timeline=None,
        termination=_termination(),
        final_state_digest=result.after_state_digest,
    )

    serialized = bundle.model_dump_json()
    assert secret not in serialized
    assert bundle.tool_exchanges[0].output_refs[0].value_digest == sha256_digest(secret)
    assert OracleEvidenceBundle.model_validate_json(serialized) == bundle


@pytest.mark.parametrize(
    ("status", "failure_code"),
    (
        (ToolResultStatus.BLOCKED, ToolFailureCode.PLATFORM_DENIED),
        (ToolResultStatus.REJECTED, ToolFailureCode.INVALID_ARGUMENTS),
    ),
)
def test_blocked_and_rejected_exchanges_preserve_the_state(
    status: ToolResultStatus,
    failure_code: ToolFailureCode,
) -> None:
    materialization, invocation, _ = _read_exchange()
    result = build_tool_result(
        invocation=invocation,
        status=status,
        failure_code=failure_code,
    )
    bundle = build_oracle_evidence_bundle(
        scenario_case=materialization.scenario_case,
        initialization_transition=materialization.initialization_transition,
        invocations=(invocation,),
        results=(result,),
        interaction_facts=(),
        timeline=None,
        termination=_termination(),
        final_state_digest=result.after_state_digest,
    )
    assert bundle.tool_exchanges[0].before_state_digest == bundle.identity.final_state_digest
    assert bundle.tool_exchanges[0].transition_ref is None


def test_state_advancing_interaction_requires_a_committed_transition_reference() -> None:
    materialization = _materialization()
    case = materialization.scenario_case
    after = sha256_digest({"state": "after-grant"})
    transition_ref = StateTransitionEvidenceRef(
        evidence_id="evidence.transition.interaction.001",
        evidence_digest=sha256_digest({"transaction": "grant"}),
        sequence=0,
        transaction_id="transaction.interaction.001",
        committed=True,
    )
    fact = build_interaction_evidence_fact(
        evidence_id="evidence.interaction.result.001",
        sequence=0,
        event_kind=InteractionEvidenceKind.INTERACTION_RESULT,
        logical_time=1,
        input_digest=sha256_digest({"request": 1}),
        output_digest=sha256_digest({"outcome": "grant_created"}),
        before_state_digest=case.initial_world_digest,
        after_state_digest=after,
        state_digest=after,
        data_digest=sha256_digest({"event": "interaction_result"}),
        status="grant_created",
        transition_ref=transition_ref,
        advances_state=True,
    )
    bundle = build_oracle_evidence_bundle(
        scenario_case=case,
        initialization_transition=materialization.initialization_transition,
        invocations=(),
        results=(),
        interaction_facts=(fact,),
        timeline=(
            EpisodeTimelineEntry(
                episode_sequence=0,
                entry_kind=TimelineEntryKind.INTERACTION,
                item_sequence=0,
            ),
        ),
        termination=_termination(sequence=0),
        final_state_digest=after,
    )
    assert bundle.identity.final_state_digest == after
    assert OracleEvidenceBundle.model_validate_json(bundle.model_dump_json()) == bundle


def test_failed_transaction_is_kept_as_an_uncommitted_empty_rollback() -> None:
    materialization, invocation, _ = _read_exchange()
    transition_payload = {
        "transaction_id": "transaction.failed.001",
        "action_request_id": "action.failed.001",
        "policy_decision_id": None,
        "before_state_digest": invocation.before_state_digest,
        "after_state_digest": invocation.before_state_digest,
        "committed": False,
        "failure_code": "transaction_validation_failed",
        "state_delta": StateDelta(),
    }
    draft = StateTransitionRecord.model_construct(
        **transition_payload,
        transition_digest="sha256:" + "0" * 64,
    )
    transition = StateTransitionRecord(
        **transition_payload,
        transition_digest=sha256_digest(draft.digest_payload()),
    )
    result = build_tool_result(
        invocation=invocation,
        status=ToolResultStatus.FAILED,
        state_transition=transition,
        failure_code=ToolFailureCode.TRANSACTION_VALIDATION_FAILED,
    )
    bundle = build_oracle_evidence_bundle(
        scenario_case=materialization.scenario_case,
        initialization_transition=materialization.initialization_transition,
        invocations=(invocation,),
        results=(result,),
        interaction_facts=(),
        timeline=None,
        termination=_termination(),
        final_state_digest=result.after_state_digest,
    )
    assert bundle.tool_exchanges[0].transition_ref is not None
    assert bundle.tool_exchanges[0].transition_ref.committed is False


def test_missing_result_and_wrong_final_digest_are_classified() -> None:
    materialization, invocation, result = _read_exchange()
    with pytest.raises(OracleEvidenceIntegrityError) as missing:
        build_oracle_evidence_bundle(
            scenario_case=materialization.scenario_case,
            initialization_transition=materialization.initialization_transition,
            invocations=(invocation,),
            results=(),
            interaction_facts=(),
            timeline=None,
            termination=_termination(),
            final_state_digest=result.after_state_digest,
        )
    assert missing.value.code is OracleFailureCode.DANGLING_EVIDENCE_REF

    with pytest.raises(OracleEvidenceIntegrityError) as final:
        build_oracle_evidence_bundle(
            scenario_case=materialization.scenario_case,
            initialization_transition=materialization.initialization_transition,
            invocations=(invocation,),
            results=(result,),
            interaction_facts=(),
            timeline=None,
            termination=_termination(),
            final_state_digest=sha256_digest({"wrong": "final"}),
        )
    assert final.value.code is OracleFailureCode.INVALID_STATE_CHAIN


def test_tampered_nested_result_digest_is_revalidated_and_rejected() -> None:
    materialization, invocation, result = _read_exchange()
    tampered = result.model_copy(
        update={"visible_output_digest": sha256_digest({"tampered": True})}
    )
    with pytest.raises(OracleEvidenceIntegrityError) as failure:
        build_oracle_evidence_bundle(
            scenario_case=materialization.scenario_case,
            initialization_transition=materialization.initialization_transition,
            invocations=(invocation,),
            results=(tampered,),
            interaction_facts=(),
            timeline=None,
            termination=_termination(),
            final_state_digest=result.after_state_digest,
        )
    assert failure.value.code is OracleFailureCode.DIGEST_MISMATCH


def test_duplicate_results_and_broken_sequence_are_classified() -> None:
    materialization, invocation, result = _read_exchange()
    duplicate = result.model_copy()
    with pytest.raises(OracleEvidenceIntegrityError) as repeated:
        build_oracle_evidence_bundle(
            scenario_case=materialization.scenario_case,
            initialization_transition=materialization.initialization_transition,
            invocations=(invocation, invocation.model_copy(update={"sequence": 1})),
            results=(result, duplicate),
            interaction_facts=(),
            timeline=None,
            termination=_termination(sequence=2),
            final_state_digest=result.after_state_digest,
        )
    assert repeated.value.code is OracleFailureCode.DUPLICATE_EVIDENCE

    broken = invocation.model_copy(update={"sequence": 2})
    with pytest.raises(OracleEvidenceIntegrityError) as sequence:
        build_oracle_evidence_bundle(
            scenario_case=materialization.scenario_case,
            initialization_transition=materialization.initialization_transition,
            invocations=(broken,),
            results=(result,),
            interaction_facts=(),
            timeline=None,
            termination=_termination(),
            final_state_digest=result.after_state_digest,
        )
    assert sequence.value.code is OracleFailureCode.INVALID_SEQUENCE
