"""Map neutral TRACE events and trusted Office V2 facts into Oracle evidence."""

from __future__ import annotations

import json
from collections.abc import Iterable
from json import JSONDecodeError

from pydantic import ValidationError

from sandbox.protocol import TraceEvent
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.agent_api import project_office_v2_tool_result
from sandbox.scenarios.office_v2.attack_models import MaterializedScenarioCase
from sandbox.scenarios.office_v2.clean_cases import CleanCaseMaterialization
from sandbox.scenarios.office_v2.oracle import evaluate_scenario_oracle
from sandbox.scenarios.office_v2.oracle_evidence import (
    EpisodeTimelineEntry,
    InteractionEvidenceFact,
    InteractionEvidenceKind,
    OracleEvidenceBundle,
    OracleEvidenceIntegrityError,
    TerminationFact,
    TimelineEntryKind,
    build_clean_oracle_evidence_bundle,
    build_oracle_evidence_bundle,
)
from sandbox.scenarios.office_v2.oracle_models import (
    CompleteScenarioOracleResult,
    OracleFailureCode,
)
from sandbox.scenarios.office_v2.tools.contracts import (
    OfficeToolInvocation,
    OfficeToolResult,
)
from sandbox.scenarios.office_v2.world import StateTransitionRecord

BUSINESS_TOOL_TRACE_SOURCE = "controlled_tools"
INTERACTION_TRACE_SOURCE = "trace.office.interaction"
SUBMIT_TRACE_SOURCE = "trace.react"


def build_oracle_evidence_from_trace(
    *,
    scenario_case: MaterializedScenarioCase | CleanCaseMaterialization,
    initialization_transition: StateTransitionRecord | None,
    trace_events: Iterable[TraceEvent],
    invocations: tuple[OfficeToolInvocation, ...],
    results: tuple[OfficeToolResult, ...],
    interaction_facts: tuple[InteractionEvidenceFact, ...],
    termination: TerminationFact,
    final_state_digest: str,
    initial_state_digest: str | None = None,
    recording_digest: str | None = None,
    replay_digest: str | None = None,
) -> OracleEvidenceBundle:
    """Validate neutral recording events, then build the existing trusted bundle.

    TRACE proves ordering and the exact payload shown to the Agent. Complete policy
    decisions, state transitions, provenance, and interaction transitions must be
    supplied as trusted facts; this adapter never reconstructs missing facts.
    """

    events = _validated_events(trace_events)
    tool_pairs = _tool_pairs(events)
    if len(tool_pairs) != len(invocations) or len(tool_pairs) != len(results):
        raise OracleEvidenceIntegrityError(
            OracleFailureCode.DANGLING_EVIDENCE_REF,
            "TRACE business tool exchanges do not match trusted tool facts",
        )

    positions: list[tuple[int, TimelineEntryKind, int]] = []
    for item_sequence, ((call_event, result_event), invocation, result) in enumerate(
        zip(tool_pairs, invocations, results, strict=True)
    ):
        _validate_tool_exchange(call_event, result_event, invocation, result)
        positions.append((result_event.sequence, TimelineEntryKind.TOOL, item_sequence))

    interaction_events = tuple(
        event for event in events if event.source == INTERACTION_TRACE_SOURCE
    )
    if len(interaction_events) != len(interaction_facts):
        raise OracleEvidenceIntegrityError(
            OracleFailureCode.DANGLING_EVIDENCE_REF,
            "TRACE interaction events do not match trusted interaction facts",
        )
    for item_sequence, (event, fact) in enumerate(
        zip(interaction_events, interaction_facts, strict=True)
    ):
        _validate_interaction_event(event, fact)
        positions.append((event.sequence, TimelineEntryKind.INTERACTION, item_sequence))

    _validate_termination(events, termination)
    timeline = tuple(
        EpisodeTimelineEntry(
            episode_sequence=episode_sequence,
            entry_kind=entry_kind,
            item_sequence=item_sequence,
        )
        for episode_sequence, (_, entry_kind, item_sequence) in enumerate(
            sorted(positions, key=lambda item: item[0])
        )
    )
    common = {
        "invocations": invocations,
        "results": results,
        "interaction_facts": interaction_facts,
        "timeline": timeline,
        "termination": termination,
        "final_state_digest": final_state_digest,
        "recording_digest": recording_digest,
        "replay_digest": replay_digest,
    }
    if isinstance(scenario_case, CleanCaseMaterialization):
        if initialization_transition is not None:
            raise OracleEvidenceIntegrityError(
                OracleFailureCode.INVALID_CONTRACT,
                "clean TRACE cannot include an initialization transition",
            )
        if initial_state_digest is None:
            raise OracleEvidenceIntegrityError(
                OracleFailureCode.INVALID_CONTRACT,
                "clean TRACE requires its exact initial state digest",
            )
        return build_clean_oracle_evidence_bundle(
            clean_case=scenario_case,
            initial_state_digest=initial_state_digest,
            **common,
        )
    return build_oracle_evidence_bundle(
        scenario_case=scenario_case,
        initialization_transition=initialization_transition,
        **common,
    )


def rebuild_oracle_evidence_bundle(
    payload: str | bytes | bytearray,
    *,
    expected_bundle_digest: str,
) -> OracleEvidenceBundle:
    """Revalidate a persisted neutral fact bundle against an external digest lock."""

    try:
        decoded = json.loads(payload)
    except (JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
        raise OracleEvidenceIntegrityError(
            OracleFailureCode.INVALID_CONTRACT,
            "persisted Oracle evidence is not valid JSON",
        ) from exc
    if not isinstance(decoded, dict):
        raise OracleEvidenceIntegrityError(
            OracleFailureCode.INVALID_CONTRACT,
            "persisted Oracle evidence must be a JSON object",
        )
    if decoded.get("bundle_digest") != expected_bundle_digest:
        raise OracleEvidenceIntegrityError(
            OracleFailureCode.DIGEST_MISMATCH,
            "persisted Oracle evidence does not match its external digest lock",
        )
    try:
        bundle = OracleEvidenceBundle.model_validate(decoded)
    except ValidationError as exc:
        reasons = "; ".join(
            str(item["msg"]) for item in exc.errors(include_input=False)
        )
        raise OracleEvidenceIntegrityError(
            _validation_failure_code(reasons),
            reasons,
        ) from exc
    if bundle.bundle_digest != expected_bundle_digest:
        raise OracleEvidenceIntegrityError(
            OracleFailureCode.DIGEST_MISMATCH,
            "rebuilt Oracle evidence does not match its external digest lock",
        )
    return bundle


def rebuild_scenario_oracle_from_bundle(
    payload: str | bytes | bytearray,
    *,
    expected_bundle_digest: str,
    scenario_case: MaterializedScenarioCase,
) -> CompleteScenarioOracleResult:
    """Re-evaluate persisted facts without accepting a saved Oracle verdict."""

    bundle = rebuild_oracle_evidence_bundle(
        payload,
        expected_bundle_digest=expected_bundle_digest,
    )
    return evaluate_scenario_oracle(bundle=bundle, scenario_case=scenario_case)


def _validation_failure_code(message: str) -> OracleFailureCode:
    normalized = message.casefold()
    if "digest" in normalized:
        return OracleFailureCode.DIGEST_MISMATCH
    if "sequence" in normalized or "timeline" in normalized:
        return OracleFailureCode.INVALID_SEQUENCE
    if "state" in normalized:
        return OracleFailureCode.INVALID_STATE_CHAIN
    if "evidence" in normalized or "reference" in normalized:
        return OracleFailureCode.DANGLING_EVIDENCE_REF
    return OracleFailureCode.INVALID_CONTRACT


def _validated_events(trace_events: Iterable[TraceEvent]) -> tuple[TraceEvent, ...]:
    try:
        events = tuple(
            TraceEvent.model_validate(event.model_dump(mode="python", exclude_none=False))
            for event in trace_events
        )
    except (AttributeError, ValidationError) as exc:
        raise OracleEvidenceIntegrityError(
            OracleFailureCode.INVALID_CONTRACT,
            "TRACE contains an invalid event contract",
        ) from exc
    if tuple(event.sequence for event in events) != tuple(range(len(events))):
        raise OracleEvidenceIntegrityError(
            OracleFailureCode.INVALID_SEQUENCE,
            "TRACE event sequence must be contiguous from zero",
        )
    execution_ids = {event.execution_id for event in events}
    if len(execution_ids) > 1:
        raise OracleEvidenceIntegrityError(
            OracleFailureCode.IDENTITY_MISMATCH,
            "TRACE events must belong to one execution",
        )
    for event in events:
        if event.source == INTERACTION_TRACE_SOURCE:
            try:
                InteractionEvidenceKind(event.event_type)
            except ValueError as exc:
                raise OracleEvidenceIntegrityError(
                    OracleFailureCode.INVALID_CONTRACT,
                    "TRACE contains an unknown Office V2 interaction event",
                    affected_ids=(event.event_type,),
                ) from exc
        elif event.source == BUSINESS_TOOL_TRACE_SOURCE and event.event_type not in {
            "tool_call",
            "tool_result",
        }:
            raise OracleEvidenceIntegrityError(
                OracleFailureCode.INVALID_CONTRACT,
                "TRACE contains an unknown Office V2 business tool event",
                affected_ids=(event.event_type,),
            )
    return events


def _tool_pairs(
    events: tuple[TraceEvent, ...],
) -> tuple[tuple[TraceEvent, TraceEvent], ...]:
    pending: dict[str, TraceEvent] = {}
    pairs: list[tuple[TraceEvent, TraceEvent]] = []
    for event in events:
        if event.source != BUSINESS_TOOL_TRACE_SOURCE:
            continue
        call_id = event.data.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            raise OracleEvidenceIntegrityError(
                OracleFailureCode.INVALID_CONTRACT,
                "business tool TRACE event requires a non-empty call_id",
            )
        if event.event_type == "tool_call":
            if pending:
                raise OracleEvidenceIntegrityError(
                    OracleFailureCode.INVALID_SEQUENCE,
                    "business tool calls cannot overlap in one Agent execution",
                    affected_ids=tuple(sorted((*pending, call_id))),
                )
            if call_id in pending:
                raise OracleEvidenceIntegrityError(
                    OracleFailureCode.DUPLICATE_EVIDENCE,
                    "business tool TRACE repeats an unresolved call_id",
                    affected_ids=(call_id,),
                )
            pending[call_id] = event
            continue
        call_event = pending.pop(call_id, None)
        if call_event is None:
            raise OracleEvidenceIntegrityError(
                OracleFailureCode.DANGLING_EVIDENCE_REF,
                "business tool result has no preceding call",
                affected_ids=(call_id,),
            )
        pairs.append((call_event, event))
    if pending:
        raise OracleEvidenceIntegrityError(
            OracleFailureCode.DANGLING_EVIDENCE_REF,
            "business tool call has no result",
            affected_ids=tuple(sorted(pending)),
        )
    return tuple(pairs)


def _validate_tool_exchange(
    call_event: TraceEvent,
    result_event: TraceEvent,
    invocation: OfficeToolInvocation,
    result: OfficeToolResult,
) -> None:
    call_id = call_event.data["call_id"]
    call_index = call_event.data.get("call_index")
    if (
        result_event.data.get("call_id") != call_id
        or not isinstance(call_index, int)
        or call_index < 0
        or call_index != result_event.data.get("call_index")
        or call_event.data.get("name") != invocation.tool_name
        or result_event.data.get("name") != invocation.tool_name
        or result.invocation_id != invocation.invocation_id
    ):
        raise OracleEvidenceIntegrityError(
            OracleFailureCode.IDENTITY_MISMATCH,
            "TRACE and trusted tool exchange identities do not match",
            affected_ids=(str(call_id), invocation.invocation_id),
        )
    arguments = call_event.data.get("arguments")
    if (
        not isinstance(arguments, dict)
        or sha256_digest(arguments) != invocation.arguments_digest
        or call_event.input_digest != invocation.arguments_digest
    ):
        raise OracleEvidenceIntegrityError(
            OracleFailureCode.DIGEST_MISMATCH,
            "TRACE tool arguments do not match trusted invocation",
            affected_ids=(invocation.invocation_id,),
        )
    visible_payload = {
        key: value
        for key, value in result_event.data.items()
        if key not in {"call_id", "call_index", "name"}
    }
    expected_payload = project_office_v2_tool_result(result).model_visible_payload()
    if (
        visible_payload != expected_payload
        or result_event.output_digest != sha256_digest(expected_payload)
    ):
        raise OracleEvidenceIntegrityError(
            OracleFailureCode.DIGEST_MISMATCH,
            "TRACE tool result does not match trusted Agent-visible projection",
            affected_ids=(invocation.invocation_id,),
        )


def _validate_interaction_event(
    event: TraceEvent,
    fact: InteractionEvidenceFact,
) -> None:
    if (
        event.event_type != fact.event_kind.value
        or event.logical_time != fact.logical_time
        or event.input_digest != fact.input_digest
        or event.output_digest != fact.output_digest
        or event.state_digest != fact.state_digest
    ):
        raise OracleEvidenceIntegrityError(
            OracleFailureCode.IDENTITY_MISMATCH,
            "TRACE interaction event does not match trusted interaction fact",
            affected_ids=(fact.evidence_id,),
        )
    if sha256_digest(event.data) != fact.data_digest:
        raise OracleEvidenceIntegrityError(
            OracleFailureCode.DIGEST_MISMATCH,
            "TRACE interaction data does not match trusted interaction fact",
            affected_ids=(fact.evidence_id,),
        )


def _validate_termination(
    events: tuple[TraceEvent, ...], termination: TerminationFact
) -> None:
    submits = tuple(
        event
        for event in events
        if event.event_type == "agent_submit" and event.source == SUBMIT_TRACE_SOURCE
    )
    if termination.submitted:
        if len(submits) != 1 or submits[0].data.get("accepted") is not True:
            raise OracleEvidenceIntegrityError(
                OracleFailureCode.DANGLING_EVIDENCE_REF,
                "submitted termination requires exactly one accepted agent_submit event",
            )
        if submits[0].data.get("answer_digest") != termination.output_digest:
            raise OracleEvidenceIntegrityError(
                OracleFailureCode.DIGEST_MISMATCH,
                "agent_submit answer digest does not match termination fact",
                affected_ids=(termination.evidence_id,),
            )
    elif submits:
        raise OracleEvidenceIntegrityError(
            OracleFailureCode.IDENTITY_MISMATCH,
            "non-submitted termination cannot contain agent_submit",
            affected_ids=(termination.evidence_id,),
        )


__all__ = [
    "BUSINESS_TOOL_TRACE_SOURCE",
    "INTERACTION_TRACE_SOURCE",
    "SUBMIT_TRACE_SOURCE",
    "build_oracle_evidence_from_trace",
    "rebuild_oracle_evidence_bundle",
    "rebuild_scenario_oracle_from_bundle",
]
