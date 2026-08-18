"""Derive office coverage facts from a committed TRACE-G episode."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from sandbox.coverage.events import terminal_kind
from sandbox.coverage.exceptions import CoverageInputError
from sandbox.coverage.models import (
    OfficeBaselineActionEvidence,
    OfficeExecutionEvidence,
    OfficeToolExecutionEvidence,
)
from sandbox.protocol import TraceEvent
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_episode import (
    OfficeEpisodeInitialization,
    OfficeInitializationError,
    OfficeToolRuntimeState,
    load_office_episode_initialization,
)
from sandbox.scenarios.office_runtime import (
    OfficeActionRecord,
    OfficeRuntime,
    OfficeRuntimeError,
)
from sandbox.tool_contracts import OFFICE_SCENARIO_TOOL_SPEC_BY_NAME

_TERMINAL_EVENTS = {
    "execution_finished",
    "execution_timed_out",
    "execution_cancelled",
    "execution_error",
}
_RESULT_FACT_FIELDS = (
    "allowed",
    "authorized",
    "outcome",
    "output",
    "error",
    "rejection_kind",
)


def has_office_events(events: list[TraceEvent]) -> bool:
    return any(
        event.event_type in {"scenario_initialized", "scenario_state_observed"}
        and event.source == "trace.office"
        for event in events
    )


def build_office_execution_evidence(
    events: list[TraceEvent],
    scenario_state: OfficeEpisodeInitialization | OfficeToolRuntimeState | dict[str, Any],
) -> OfficeExecutionEvidence:
    if not events:
        raise CoverageInputError("office coverage input contains no events")
    initialization, runtime = _restore_runtime(scenario_state)
    case = initialization.test_case
    episode_initial_state = runtime.export_state()
    episode_initial_state_digest = runtime.state_digest()
    baseline_action_count = len(runtime.records)
    baseline_actions = [_baseline_action(record) for record in runtime.records]

    started = [event for event in events if event.event_type == "execution_started"]
    initialized = [
        event
        for event in events
        if event.event_type == "scenario_initialized" and event.source == "trace.office"
    ]
    observations = [
        event
        for event in events
        if event.event_type == "scenario_state_observed" and event.source == "trace.office"
    ]
    if len(started) != 1:
        raise CoverageInputError("office trajectory requires exactly one execution_started event")
    if len(initialized) != 1:
        raise CoverageInputError(
            "office trajectory requires exactly one office initialization event"
        )
    if len(observations) > 1:
        raise CoverageInputError("office trajectory contains multiple final state observations")
    _validate_started(started[0], case.case_id, case.scenario.template_id)
    _validate_initialized(initialized[0], initialization)

    pending: dict[str, TraceEvent] = {}
    seen_call_ids: set[str] = set()
    actions: list[OfficeToolExecutionEvidence] = []
    for event in events:
        if event.event_type == "tool_call":
            call_id = _required_text(event.data, "call_id", "office tool call")
            if call_id in seen_call_ids or call_id in pending:
                raise CoverageInputError(f"duplicate office tool call_id: {call_id}")
            if event.source != "controlled_tools":
                raise CoverageInputError("office tool call has an unexpected event source")
            pending[call_id] = event
            seen_call_ids.add(call_id)
        elif event.event_type == "tool_result":
            call_id = _required_text(event.data, "call_id", "office tool result")
            call = pending.pop(call_id, None)
            if call is None:
                raise CoverageInputError(
                    f"office tool result has no matching call: {call_id}"
                )
            actions.append(_verify_tool_window(runtime, call, event))
    if pending:
        raise CoverageInputError(
            f"office tool calls are missing results: {sorted(pending)}"
        )

    termination = terminal_kind(events)
    terminal = events[-1]
    if terminal.event_type not in _TERMINAL_EVENTS:
        raise CoverageInputError("office trajectory does not end in a terminal event")
    normal_task_completed = runtime.benign_success().passed
    attack_evaluation = runtime.attack_success()
    attack_side_effect_observed = (
        attack_evaluation.passed if attack_evaluation is not None else None
    )
    unauthorized_action_count = sum(not record.authorized for record in runtime.records)

    if observations:
        _validate_observation(
            observations[0],
            case_id=case.case_id,
            scenario_id=case.scenario.template_id,
            normal_task_completed=normal_task_completed,
            attack_side_effect_observed=attack_side_effect_observed,
            action_count=len(runtime.records),
            unauthorized_action_count=unauthorized_action_count,
            initial_state_digest=initialization.initial_state_digest,
            final_state_digest=runtime.state_digest(),
        )
    if terminal.event_type == "execution_finished":
        if not observations:
            raise CoverageInputError("successful office trajectory lacks final state observation")
        submits = [
            event
            for event in events
            if event.event_type == "agent_submit" and event.data.get("accepted") is True
        ]
        if len(submits) != 1:
            raise CoverageInputError("successful office trajectory requires one accepted submit")
        if terminal.data.get("attack_side_effect_observed") is not bool(
            attack_side_effect_observed
        ):
            raise CoverageInputError(
                "office terminal attack fact conflicts with reconstructed state"
            )

    return OfficeExecutionEvidence(
        case_id=case.case_id,
        scenario_id=case.scenario.template_id,
        test_case=case,
        test_case_digest=case.content_digest,
        initialization_digest=initialization.envelope_digest,
        scenario_initial_state=deepcopy(initialization.initial_state),
        scenario_initial_state_digest=initialization.initial_state_digest,
        episode_initial_state=episode_initial_state,
        episode_initial_state_digest=episode_initial_state_digest,
        final_state=runtime.export_state(),
        final_state_digest=runtime.state_digest(),
        baseline_actions=baseline_actions,
        actions=actions,
        baseline_action_count=baseline_action_count,
        total_action_count=len(runtime.records),
        unauthorized_action_count=unauthorized_action_count,
        normal_task_completed=normal_task_completed,
        attack_side_effect_observed=attack_side_effect_observed,
        termination=termination,
    )


def _baseline_action(record: OfficeActionRecord) -> OfficeBaselineActionEvidence:
    return OfficeBaselineActionEvidence(
        action_sequence=record.sequence,
        tool_name=record.tool_name,
        capability_id=record.capability_id,
        arguments=deepcopy(record.arguments),
        authorized=record.authorized,
        outcome=record.outcome,
        before_state_digest=record.before_state_digest,
        after_state_digest=record.after_state_digest,
    )


def _restore_runtime(
    scenario_state: OfficeEpisodeInitialization | OfficeToolRuntimeState | dict[str, Any],
) -> tuple[OfficeEpisodeInitialization, OfficeRuntime]:
    try:
        if isinstance(scenario_state, OfficeToolRuntimeState):
            saved = scenario_state
        elif isinstance(scenario_state, OfficeEpisodeInitialization):
            initialization = load_office_episode_initialization(
                scenario_state.model_dump(mode="json")
            )
            saved = None
        elif isinstance(scenario_state, dict) and "initialization" in scenario_state:
            saved = OfficeToolRuntimeState.model_validate(scenario_state)
        else:
            initialization = load_office_episode_initialization(scenario_state)
            saved = None
    except (OfficeInitializationError, ValidationError, ValueError, TypeError) as exc:
        raise CoverageInputError("office scenario state failed integrity validation") from exc

    if saved is not None:
        try:
            initialization = load_office_episode_initialization(
                saved.initialization.model_dump(mode="json")
            )
        except OfficeInitializationError as exc:
            raise CoverageInputError("office initialization failed integrity validation") from exc
    runtime = OfficeRuntime(initialization.test_case)
    if runtime.export_state() != initialization.initial_state:
        raise CoverageInputError("office runtime initial state conflicts with initialization")
    if saved is None:
        return initialization, runtime

    try:
        for action in saved.actions:
            runtime.execute(action.capability_id, action.arguments)
    except OfficeRuntimeError as exc:
        raise CoverageInputError("office baseline contains an invalid action") from exc
    records_digest = sha256_digest(
        [record.model_dump(mode="json") for record in runtime.records]
    )
    if records_digest != saved.records_digest:
        raise CoverageInputError("office baseline records_digest does not match actions")
    if runtime.state_digest() != saved.final_state_digest:
        raise CoverageInputError("office baseline final_state_digest does not match actions")
    return initialization, runtime


def _verify_tool_window(
    runtime: OfficeRuntime,
    call: TraceEvent,
    result: TraceEvent,
) -> OfficeToolExecutionEvidence:
    if result.source != "controlled_tools":
        raise CoverageInputError("office tool result has an unexpected event source")
    call_id = _required_text(call.data, "call_id", "office tool call")
    tool_name = _required_text(call.data, "name", "office tool call")
    if result.data.get("call_id") != call_id or result.data.get("name") != tool_name:
        raise CoverageInputError("office tool result identity does not match its call")
    if result.data.get("call_index") != call.data.get("call_index"):
        raise CoverageInputError("office tool result call_index does not match its call")
    arguments = call.data.get("arguments")
    if not isinstance(arguments, dict):
        raise CoverageInputError("office tool call arguments must be an object")
    if call.input_digest != sha256_digest(arguments):
        raise CoverageInputError("office tool call input_digest does not match arguments")
    spec = OFFICE_SCENARIO_TOOL_SPEC_BY_NAME.get(tool_name)
    if spec is None:
        raise CoverageInputError(f"office trajectory called an unknown tool: {tool_name}")

    result_payload = {
        key: value
        for key, value in result.data.items()
        if key not in {"call_id", "call_index", "name"}
    }
    if result.output_digest != sha256_digest(result_payload):
        raise CoverageInputError("office tool result output_digest does not match payload")
    rejection_kind = result_payload.get("rejection_kind")
    if rejection_kind not in {None, "policy", "provenance"}:
        raise CoverageInputError("office tool result uses an unknown rejection kind")
    before_state_digest = runtime.state_digest()
    arguments_valid = True
    try:
        parsed = spec.validate_arguments(arguments)
    except ValidationError as exc:
        if rejection_kind is not None:
            raise CoverageInputError(
                "invalid office arguments cannot be classified as a policy block"
            ) from exc
        arguments_valid = False
        arguments = {}
        expected = {
            "allowed": False,
            "outcome": "rejected",
            "output": None,
            "error": f"invalid tool arguments: {exc.errors()[0]['msg']}",
        }
    else:
        clean_arguments = parsed.model_dump(mode="python")
        policy_rejection = rejection_kind == "policy"
        try:
            record = runtime.execute(
                spec.required_capability,
                clean_arguments,
                enforce_authorization=policy_rejection,
                enforce_parameter_provenance=True,
            )
        except OfficeRuntimeError as exc:
            if policy_rejection:
                raise CoverageInputError(
                    "office policy block could not form a valid action scope"
                ) from exc
            expected = {
                "allowed": False,
                "outcome": "rejected",
                "output": None,
                "error": str(exc),
            }
        else:
            blocked = record.outcome == "blocked"
            expected = {
                "allowed": not blocked,
                "authorized": record.authorized,
                "outcome": record.outcome,
                "output": record.output,
                "error": record.error,
            }
            if blocked:
                expected["rejection_kind"] = record.rejection_kind
            arguments = clean_arguments
    actual = {
        key: result_payload.get(key)
        for key in _RESULT_FACT_FIELDS
        if key != "authorized" and key in expected
    }
    if "authorized" in expected:
        actual["authorized"] = result_payload.get("authorized")
    if actual != expected:
        raise CoverageInputError(
            f"office tool result conflicts with deterministic execution: {call_id}"
        )
    after_state_digest = runtime.state_digest()
    return OfficeToolExecutionEvidence(
        call_id=call_id,
        tool_name=tool_name,
        capability_id=spec.required_capability,
        arguments=deepcopy(arguments),
        arguments_valid=arguments_valid,
        allowed=bool(expected["allowed"]),
        authorized=expected.get("authorized"),
        outcome=str(expected["outcome"]),
        result=deepcopy(expected),
        result_digest=sha256_digest(expected),
        before_state_digest=before_state_digest,
        after_state_digest=after_state_digest,
        call_sequence=call.sequence,
        result_sequence=result.sequence,
    )


def _validate_started(event: TraceEvent, case_id: str, scenario_id: str) -> None:
    if event.data.get("case_id") != case_id:
        raise CoverageInputError("office execution_started case_id does not match TestCase")
    if event.data.get("scenario_id") != scenario_id:
        raise CoverageInputError("office execution_started scenario_id does not match TestCase")
    if event.data.get("execution_backend") != "trace_react_v2":
        raise CoverageInputError("office trajectory uses an unsupported execution backend")


def _validate_initialized(
    event: TraceEvent,
    initialization: OfficeEpisodeInitialization,
) -> None:
    case = initialization.test_case
    attack_location = (
        case.attack.carrier.target.model_dump(mode="json")
        if case.attack is not None
        else None
    )
    expected = {
        "scenario_id": case.scenario.template_id,
        "case_id": case.case_id,
        "initialization_digest": initialization.envelope_digest,
        "office_state_digest": initialization.initial_state_digest,
        "attack_location": attack_location,
    }
    actual = {key: event.data.get(key) for key in expected}
    if actual != expected:
        raise CoverageInputError("office initialization event conflicts with frozen state")


def _validate_observation(
    event: TraceEvent,
    *,
    case_id: str,
    scenario_id: str,
    normal_task_completed: bool,
    attack_side_effect_observed: bool | None,
    action_count: int,
    unauthorized_action_count: int,
    initial_state_digest: str,
    final_state_digest: str,
) -> None:
    expected = {
        "case_id": case_id,
        "scenario_id": scenario_id,
        "normal_task_completed": normal_task_completed,
        "attack_side_effect_observed": attack_side_effect_observed,
        "action_count": action_count,
        "unauthorized_action_count": unauthorized_action_count,
        "initial_state_digest": initial_state_digest,
        "final_state_digest": final_state_digest,
    }
    actual = {key: event.data.get(key) for key in expected}
    if actual != expected:
        raise CoverageInputError("office final observation conflicts with reconstructed state")


def _required_text(data: dict[str, Any], key: str, label: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise CoverageInputError(f"{label} requires non-empty {key}")
    return value
