from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sandbox.protocol import TraceEvent
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.agent_api import project_office_v2_tool_result
from sandbox.scenarios.office_v2.attack_cases import (
    build_representative_scenario_fixtures,
)
from sandbox.scenarios.office_v2.oracle_evidence import (
    InteractionEvidenceKind,
    OracleEvidenceIntegrityError,
    TimelineEntryKind,
    build_interaction_evidence_fact,
    build_oracle_evidence_bundle,
    build_termination_fact,
)
from sandbox.scenarios.office_v2.oracle_models import OracleFailureCode
from sandbox.scenarios.office_v2.oracle_trace import (
    build_oracle_evidence_from_trace,
)
from sandbox.scenarios.office_v2.tools.contracts import (
    OfficeToolInvocation,
    ToolResultStatus,
    build_tool_result,
)

NOW = datetime(2026, 8, 11, tzinfo=UTC)
EXECUTION_ID = "execution.office-v2.oracle-trace.001"


def _recording():
    materialization = build_representative_scenario_fixtures()[0].materialization
    case = materialization.scenario_case
    arguments = {"query": "Apollo"}
    invocation = OfficeToolInvocation(
        invocation_id="invocation.oracle-trace.001",
        sequence=0,
        tool_name="search_drive_files",
        actor_id=case.actor.actor_id,
        task_id=case.task.task_id,
        logical_time=case.actor.logical_time,
        arguments=arguments,
        arguments_digest=sha256_digest(arguments),
        before_state_digest=case.initial_world_digest,
    )
    result = build_tool_result(
        invocation=invocation,
        status=ToolResultStatus.SUCCEEDED,
        visible_output={"items": [], "has_more": False, "next_page_token": None},
    )
    visible = project_office_v2_tool_result(result).model_visible_payload()
    answer_digest = sha256_digest("done")
    events = (
        TraceEvent(
            execution_id=EXECUTION_ID,
            sequence=0,
            timestamp=NOW,
            event_type="model_end",
            source="trace.react",
            data={"turn": 1},
        ),
        TraceEvent(
            execution_id=EXECUTION_ID,
            sequence=1,
            timestamp=NOW,
            event_type="tool_call",
            source="controlled_tools",
            data={
                "call_id": "call.001",
                "call_index": 0,
                "name": invocation.tool_name,
                "arguments": arguments,
            },
            logical_time=1,
            input_digest=invocation.arguments_digest,
        ),
        TraceEvent(
            execution_id=EXECUTION_ID,
            sequence=2,
            timestamp=NOW,
            event_type="tool_result",
            source="controlled_tools",
            data={
                **visible,
                "call_id": "call.001",
                "call_index": 0,
                "name": invocation.tool_name,
            },
            logical_time=1,
            output_digest=sha256_digest(visible),
        ),
        TraceEvent(
            execution_id=EXECUTION_ID,
            sequence=3,
            timestamp=NOW,
            event_type="agent_submit",
            source="trace.react",
            data={
                "call_id": "submit.001",
                "call_index": 0,
                "accepted": True,
                "answer_digest": answer_digest,
            },
            logical_time=2,
        ),
    )
    termination = build_termination_fact(
        evidence_id="termination.oracle-trace.001",
        sequence=1,
        reason="submit",
        submitted=True,
        output_digest=answer_digest,
    )
    return materialization, invocation, result, events, termination


def _build(*, events=None, invocation=None, result=None, termination=None):
    materialization, base_invocation, base_result, base_events, base_termination = (
        _recording()
    )
    recording_digest = sha256_digest({"recording": EXECUTION_ID})
    return build_oracle_evidence_from_trace(
        scenario_case=materialization.scenario_case,
        initialization_transition=materialization.initialization_transition,
        trace_events=base_events if events is None else events,
        invocations=(base_invocation if invocation is None else invocation,),
        results=(base_result if result is None else result,),
        interaction_facts=(),
        termination=base_termination if termination is None else termination,
        final_state_digest=base_result.after_state_digest,
        recording_digest=recording_digest,
    )


def test_recording_mapping_equals_direct_bundle_and_ignores_model_events() -> None:
    materialization, invocation, result, events, termination = _recording()
    recording_digest = sha256_digest({"recording": EXECUTION_ID})
    recorded = _build()
    direct = build_oracle_evidence_bundle(
        scenario_case=materialization.scenario_case,
        initialization_transition=materialization.initialization_transition,
        invocations=(invocation,),
        results=(result,),
        interaction_facts=(),
        timeline=None,
        termination=termination,
        final_state_digest=result.after_state_digest,
        recording_digest=recording_digest,
    )

    assert recorded == direct
    assert recorded.recording_digest == recording_digest
    assert events[0].event_type == "model_end"


def test_tool_argument_tampering_is_rejected() -> None:
    _, _, _, events, _ = _recording()
    call = events[1].model_copy(
        update={"data": {**events[1].data, "arguments": {"query": "tampered"}}}
    )
    with pytest.raises(OracleEvidenceIntegrityError) as failure:
        _build(events=(events[0], call, *events[2:]))
    assert failure.value.code is OracleFailureCode.DIGEST_MISMATCH


def test_agent_visible_result_tampering_is_rejected() -> None:
    _, _, _, events, _ = _recording()
    changed = events[2].model_copy(
        update={"data": {**events[2].data, "data": {"items": ["invented"]}}}
    )
    with pytest.raises(OracleEvidenceIntegrityError) as failure:
        _build(events=(*events[:2], changed, events[3]))
    assert failure.value.code is OracleFailureCode.DIGEST_MISMATCH


def test_trace_sequence_and_execution_identity_are_closed() -> None:
    _, _, _, events, _ = _recording()
    broken_sequence = events[2].model_copy(update={"sequence": 4})
    with pytest.raises(OracleEvidenceIntegrityError) as sequence_failure:
        _build(events=(*events[:2], broken_sequence, events[3]))
    assert sequence_failure.value.code is OracleFailureCode.INVALID_SEQUENCE

    other_execution = events[2].model_copy(update={"execution_id": "execution.other"})
    with pytest.raises(OracleEvidenceIntegrityError) as identity_failure:
        _build(events=(*events[:2], other_execution, events[3]))
    assert identity_failure.value.code is OracleFailureCode.IDENTITY_MISMATCH


def test_unknown_event_boundary_is_source_specific() -> None:
    _, _, _, events, _ = _recording()
    ignored = events[0].model_copy(update={"event_type": "future_model_fact"})
    assert _build(events=(ignored, *events[1:])).tool_exchanges

    rejected = events[0].model_copy(
        update={
            "event_type": "risk_success",
            "source": "trace.office.interaction",
        }
    )
    with pytest.raises(OracleEvidenceIntegrityError) as failure:
        _build(events=(rejected, *events[1:]))
    assert failure.value.code is OracleFailureCode.INVALID_CONTRACT


def test_missing_tool_result_and_submit_digest_mismatch_are_rejected() -> None:
    _, _, _, events, termination = _recording()
    submit = events[3].model_copy(update={"sequence": 2})
    with pytest.raises(OracleEvidenceIntegrityError) as missing:
        _build(events=(events[0], events[1], submit))
    assert missing.value.code is OracleFailureCode.DANGLING_EVIDENCE_REF

    wrong = termination.model_copy(
        update={
            "output_digest": sha256_digest("different"),
            "fact_digest": "sha256:" + "0" * 64,
        }
    )
    payload = wrong.digest_payload()
    wrong = wrong.model_copy(update={"fact_digest": sha256_digest(payload)})
    with pytest.raises(OracleEvidenceIntegrityError) as digest:
        _build(termination=wrong)
    assert digest.value.code is OracleFailureCode.DIGEST_MISMATCH


def test_adapter_output_contains_no_evaluation_event_or_label() -> None:
    serialized = _build().model_dump_json()
    for forbidden in (
        "risk_success",
        "security_violation",
        "risk_category",
        "judge_score",
    ):
        assert forbidden not in serialized


def test_neutral_interaction_event_requires_and_orders_its_trusted_fact() -> None:
    materialization, _, _, _, _ = _recording()
    state_digest = materialization.scenario_case.initial_world_digest
    data = {
        "proposal_digest": sha256_digest({"proposal": "which file"}),
        "request_digest": sha256_digest({"request": "choose current"}),
        "question_kind": "resource_selection",
        "match_status": "matched",
        "failure_code": None,
        "visible_scope": {"candidate_refs": [], "missing_fact_count": 0},
    }
    input_digest = data["proposal_digest"]
    output_digest = sha256_digest(data)
    event = TraceEvent(
        execution_id=EXECUTION_ID,
        sequence=0,
        timestamp=NOW,
        event_type="agent_clarification_requested",
        source="trace.office.interaction",
        data=data,
        logical_time=1000,
        input_digest=input_digest,
        output_digest=output_digest,
        state_digest=state_digest,
    )
    fact = build_interaction_evidence_fact(
        evidence_id="evidence.interaction.oracle-trace.001",
        sequence=0,
        event_kind=InteractionEvidenceKind.CLARIFICATION_REQUESTED,
        logical_time=1000,
        input_digest=input_digest,
        output_digest=output_digest,
        before_state_digest=state_digest,
        after_state_digest=state_digest,
        state_digest=state_digest,
        data_digest=sha256_digest(data),
        request_digest=data["request_digest"],
        status="matched",
    )
    answer_digest = sha256_digest("done")
    submit = TraceEvent(
        execution_id=EXECUTION_ID,
        sequence=1,
        timestamp=NOW,
        event_type="agent_submit",
        source="trace.react",
        data={"accepted": True, "answer_digest": answer_digest},
    )
    termination = build_termination_fact(
        evidence_id="termination.oracle-trace.interaction",
        sequence=0,
        reason="submit",
        submitted=True,
        output_digest=answer_digest,
    )

    bundle = build_oracle_evidence_from_trace(
        scenario_case=materialization.scenario_case,
        initialization_transition=materialization.initialization_transition,
        trace_events=(event, submit),
        invocations=(),
        results=(),
        interaction_facts=(fact,),
        termination=termination,
        final_state_digest=state_digest,
    )

    assert bundle.timeline[0].entry_kind is TimelineEntryKind.INTERACTION
    assert bundle.interaction_facts == (fact,)

    changed = event.model_copy(update={"data": {**data, "match_status": "rejected"}})
    with pytest.raises(OracleEvidenceIntegrityError) as failure:
        build_oracle_evidence_from_trace(
            scenario_case=materialization.scenario_case,
            initialization_transition=materialization.initialization_transition,
            trace_events=(changed, submit),
            invocations=(),
            results=(),
            interaction_facts=(fact,),
            termination=termination,
            final_state_digest=state_digest,
        )
    assert failure.value.code is OracleFailureCode.DIGEST_MISMATCH
