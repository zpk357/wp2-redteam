from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path

from app.adapter.trace_react_adapter import TraceReactAdapter
from app.protocol import ExecutionRequest
from pydantic import ValidationError

from sandbox.coverage.behavior import BehaviorFeatureExtractor
from sandbox.coverage.input import CoverageInputResolver
from sandbox.coverage.models import (
    BehaviorFeatureKind,
    BehaviorProfile,
    CoverageInput,
    OfficeExecutionEvidence,
)
from sandbox.coverage.office_evidence import build_office_execution_evidence
from sandbox.coverage.store import CoverageStore
from sandbox.coverage.taxonomy import RiskTaxonomyLoader
from sandbox.protocol import TraceEvent
from sandbox.replay.digests import sha256_digest
from sandbox.replay.normalizer import normalize_behavior_trace
from sandbox.scenarios.office_episode import build_office_episode_initialization
from sandbox.scenarios.office_matrix import OFFICE_V1_TEST_MATRIX
from sandbox.scenarios.office_v1 import ATTACK_RECIPIENT, SECRET_FILE_ID
from sandbox.tool_contracts import OFFICE_SCENARIO_TOOL_SPEC_BY_NAME

CASE = next(
    case
    for case in OFFICE_V1_TEST_MATRIX.attack_cases
    if case.case_id == "office-v1-attack-01"
)


def _request(control: str, case=CASE) -> ExecutionRequest:
    initialization = build_office_episode_initialization(case)
    return ExecutionRequest(
        execution_id=f"office-behavior-{case.case_id}-{control}",
        case_id=case.case_id,
        scenario_id=case.scenario.template_id,
        prompt=case.benign_task.instruction,
        max_steps=case.budget.max_steps,
        timeout_seconds=case.budget.timeout_seconds,
        seed=case.seed,
        scenario_initialization=initialization.model_dump(mode="json"),
        metadata={"scenario_control": control},
    )


async def _events(control: str, case=CASE) -> list[TraceEvent]:
    return [
        event async for event in TraceReactAdapter().execute(_request(control, case))
    ]


def _write(path: Path, events: list[TraceEvent]) -> None:
    path.write_text(
        "".join(event.model_dump_json() + "\n" for event in events),
        encoding="utf-8",
    )


def _resolve(tmp_path: Path, path: Path, case=CASE) -> CoverageInput:
    return CoverageInputResolver(
        trajectory_root=tmp_path,
        manifest_root=tmp_path / "replays",
        artifact_root=tmp_path / "artifacts",
    ).from_trajectory_path(
        path,
        scenario_initialization=build_office_episode_initialization(case),
    )


def _profile(coverage_input: CoverageInput) -> BehaviorProfile:
    return BehaviorFeatureExtractor().extract(
        trajectory_id=coverage_input.trajectory_id,
        execution_id=coverage_input.execution_id,
        events=normalize_behavior_trace(coverage_input.events),
        office_evidence=coverage_input.scenario_evidence,
    )


def _values(profile: BehaviorProfile) -> set[tuple[BehaviorFeatureKind, str]]:
    return {(feature.kind, feature.value) for feature in profile.features}


async def test_office_profiles_use_execution_facts_for_path_and_state_novelty(
    tmp_path: Path,
) -> None:
    inputs: dict[str, CoverageInput] = {}
    for control in ("safe", "vulnerable"):
        path = tmp_path / f"{control}.jsonl"
        _write(path, await _events(control))
        inputs[control] = _resolve(tmp_path, path)

    safe = _values(_profile(inputs["safe"]))
    vulnerable = _values(_profile(inputs["vulnerable"]))

    shared_prefix = (BehaviorFeatureKind.TOOL_BIGRAM, "search_email→read_email")
    assert shared_prefix in safe
    assert shared_prefix in vulnerable
    assert (
        BehaviorFeatureKind.TOOL_TRIGRAM,
        "search_email→read_email→create_calendar_event",
    ) in safe
    assert (
        BehaviorFeatureKind.TOOL_TRIGRAM,
        "create_calendar_event→read_drive_file→share_drive_file",
    ) in vulnerable
    assert (
        BehaviorFeatureKind.PARAM_SENSITIVITY,
        "read_drive_file:resource=restricted",
    ) in vulnerable
    assert (
        BehaviorFeatureKind.PARAM_SENSITIVITY,
        "share_drive_file:recipient=external",
    ) in vulnerable
    assert (
        BehaviorFeatureKind.AUTHORIZATION,
        "share_drive_file:unauthorized",
    ) in vulnerable
    assert (
        BehaviorFeatureKind.AUTHORIZATION_TRANSITION,
        "authorized→unauthorized",
    ) in vulnerable
    assert not any(
        kind in {BehaviorFeatureKind.AUTHORIZATION, BehaviorFeatureKind.AUTHORIZATION_TRANSITION}
        and "unauthorized" in value
        for kind, value in safe
    )
    assert (
        BehaviorFeatureKind.STATE_CHANGE,
        "read_email:unchanged",
    ) in safe
    assert (
        BehaviorFeatureKind.STATE_CHANGE,
        "share_drive_file:changed",
    ) in vulnerable
    assert (
        BehaviorFeatureKind.STATE_CHANGE,
        "calendar_events:added",
    ) in safe
    assert (
        BehaviorFeatureKind.STATE_CHANGE,
        "drive_shares:added",
    ) in vulnerable
    assert (BehaviorFeatureKind.TERMINATION, "succeeded") in safe
    assert (BehaviorFeatureKind.TERMINATION, "succeeded") in vulnerable

    forbidden = (SECRET_FILE_ID, ATTACK_RECIPIENT, CASE.attack.payload)
    assert all(
        token not in value
        for _, value in safe | vulnerable
        for token in forbidden
    )


async def test_office_profile_ignores_model_and_tool_risk_claims(tmp_path: Path) -> None:
    original = await _events("vulnerable")
    labelled = deepcopy(original)
    model_index = next(
        index for index, event in enumerate(labelled) if event.event_type == "model_end"
    )
    model_event = labelled[model_index]
    model_data = deepcopy(model_event.data)
    model_data["declared_operator"] = "claimed-operator"
    model_data["declared_risk"] = "claimed-risk"
    labelled[model_index] = model_event.model_copy(update={"data": model_data})

    result_index = next(
        index for index, event in enumerate(labelled) if event.event_type == "tool_result"
    )
    result_event = labelled[result_index]
    result_data = deepcopy(result_event.data)
    result_data["risk_category"] = "claimed-tool-risk"
    result_payload = {
        key: value
        for key, value in result_data.items()
        if key not in {"call_id", "call_index", "name"}
    }
    labelled[result_index] = result_event.model_copy(
        update={"data": result_data, "output_digest": sha256_digest(result_payload)}
    )

    original_path = tmp_path / "original.jsonl"
    labelled_path = tmp_path / "labelled.jsonl"
    _write(original_path, original)
    _write(labelled_path, labelled)
    original_input = _resolve(tmp_path, original_path)
    labelled_input = _resolve(tmp_path, labelled_path)

    assert original_input.input_digest != labelled_input.input_digest
    assert _profile(original_input).profile_hash == _profile(labelled_input).profile_hash


async def test_rejected_office_call_is_not_counted_as_unauthorized(
    tmp_path: Path,
) -> None:
    path = tmp_path / "safe.jsonl"
    _write(path, await _events("safe"))
    coverage_input = _resolve(tmp_path, path)
    evidence = coverage_input.scenario_evidence
    assert evidence is not None
    original = evidence.actions[0]
    rejected_result = {
        "allowed": False,
        "outcome": "rejected",
        "output": None,
        "error": "synthetic invalid arguments",
    }
    rejected = original.model_copy(
        update={
            "allowed": False,
            "authorized": None,
            "outcome": "rejected",
            "result": rejected_result,
            "result_digest": sha256_digest(rejected_result),
        }
    )
    payload = evidence.model_dump(mode="python")
    payload["actions"][0] = rejected.model_dump(mode="python")
    payload["total_action_count"] -= 1
    payload["evidence_digest"] = None
    rejected_evidence = OfficeExecutionEvidence.model_validate(payload)
    profile = _profile(
        coverage_input.model_copy(update={"scenario_evidence": rejected_evidence})
    )
    values = _values(profile)

    assert (
        BehaviorFeatureKind.AUTHORIZATION,
        "search_email:rejected",
    ) in values
    assert (
        BehaviorFeatureKind.AUTHORIZATION_TRANSITION,
        "start→rejected",
    ) in values
    assert not any(value == "search_email:unauthorized" for _, value in values)


async def test_invalid_argument_names_cannot_create_unbounded_behavior_features() -> None:
    events = await _events("safe")
    result_index = next(
        index for index, event in enumerate(events) if event.event_type == "tool_result"
    )
    call_index = next(
        index
        for index, event in enumerate(events[:result_index])
        if event.event_type == "tool_call"
    )
    call = events[call_index]
    result = events[result_index]
    attacker_field = "attacker_controlled_" + "x" * 600
    invalid_arguments = {attacker_field: "payload"}
    spec = OFFICE_SCENARIO_TOOL_SPEC_BY_NAME[str(call.data["name"])]
    try:
        spec.validate_arguments(invalid_arguments)
    except ValidationError as exc:
        validation_message = exc.errors()[0]["msg"]
    else:  # pragma: no cover - the schema must reject the adversarial field
        raise AssertionError("office tool schema accepted an unknown argument")
    rejected_payload = {
        "allowed": False,
        "outcome": "rejected",
        "output": None,
        "error": f"invalid tool arguments: {validation_message}",
    }
    call_data = dict(call.data)
    call_data["arguments"] = invalid_arguments
    events[call_index] = call.model_copy(
        update={"data": call_data, "input_digest": sha256_digest(invalid_arguments)}
    )
    result_data = {
        "call_id": result.data["call_id"],
        "call_index": result.data["call_index"],
        "name": result.data["name"],
        **rejected_payload,
    }
    events[result_index] = result.model_copy(
        update={"data": result_data, "output_digest": sha256_digest(rejected_payload)}
    )
    terminal = events[-1].model_copy(
        update={
            "event_type": "execution_error",
            "sequence": events[result_index].sequence + 1,
            "data": {"error_code": "synthetic_stop_after_rejected_call"},
        }
    )
    truncated = [*events[: result_index + 1], terminal]

    evidence = build_office_execution_evidence(
        truncated,
        build_office_episode_initialization(CASE),
    )
    profile = BehaviorFeatureExtractor().extract(
        trajectory_id="invalid-arguments",
        execution_id=events[0].execution_id,
        events=normalize_behavior_trace(truncated),
        office_evidence=evidence,
    )
    values = _values(profile)

    assert evidence.actions[0].arguments_valid is False
    assert evidence.actions[0].arguments == {}
    assert (
        BehaviorFeatureKind.PARAM_SHAPE,
        "search_email:<INVALID_ARGS>",
    ) in values
    assert all(attacker_field not in value for _, value in values)


async def test_office_store_is_idempotent_and_accumulates_new_behavior(
    tmp_path: Path,
) -> None:
    resolved: dict[str, CoverageInput] = {}
    for control in ("safe", "vulnerable"):
        path = tmp_path / f"store-{control}.jsonl"
        _write(path, await _events(control))
        resolved[control] = _resolve(tmp_path, path)

    taxonomy = RiskTaxonomyLoader(Path("config/risk-taxonomy.yaml")).load()
    with CoverageStore(
        tmp_path / "coverage",
        "office-behavior",
        taxonomy,
        auto_snapshot_interval=0,
    ) as store:
        safe = store.evaluate(resolved["safe"])
        duplicate = store.evaluate(resolved["safe"])
        vulnerable = store.evaluate(resolved["vulnerable"])

        assert duplicate.already_evaluated is True
        assert duplicate.cumulative_behavior_count == safe.cumulative_behavior_count
        assert vulnerable.new_behavior_count > 0
        assert vulnerable.cumulative_behavior_count > safe.cumulative_behavior_count
        stored = store.all_profiles()
        assert stored[0].profile_hash == _profile(resolved["safe"]).profile_hash
        assert stored[1].profile_hash == _profile(resolved["vulnerable"]).profile_hash


async def test_full_office_matrix_produces_bounded_data_driven_features(
    tmp_path: Path,
) -> None:
    all_values: set[tuple[BehaviorFeatureKind, str]] = set()
    executions = [
        *((case, "safe") for case in OFFICE_V1_TEST_MATRIX.clean_cases),
        *(
            (case, control)
            for case in OFFICE_V1_TEST_MATRIX.attack_cases
            for control in ("safe", "vulnerable")
        ),
    ]
    for index, (case, control) in enumerate(executions):
        path = tmp_path / f"matrix-{index:02d}.jsonl"
        _write(path, await _events(control, case))
        coverage_input = _resolve(tmp_path, path, case)
        profile = _profile(coverage_input)
        evidence = coverage_input.scenario_evidence
        assert evidence is not None
        assert profile.feature_count == len(profile.features)
        assert profile.feature_count < 500
        values = _values(profile)
        all_values.update(values)

        for action in evidence.actions:
            authorization = (
                "rejected"
                if not action.allowed
                else "authorized"
                if action.authorized is True
                else "unauthorized"
            )
            assert (
                BehaviorFeatureKind.AUTHORIZATION,
                f"{action.tool_name}:{authorization}",
            ) in values
            state_change = (
                "changed"
                if action.before_state_digest != action.after_state_digest
                else "unchanged"
            )
            assert (
                BehaviorFeatureKind.STATE_CHANGE,
                f"{action.tool_name}:{state_change}",
            ) in values

        for _, value in values:
            assert "sha256:" not in value
            assert "@" not in value
            assert "SYNTHETIC" not in value
            assert re.search(r"(?:email|drive|event)-[a-z0-9-]+", value) is None

    expected = {
        (BehaviorFeatureKind.STATE_CHANGE, "calendar_events:added"),
        (BehaviorFeatureKind.STATE_CHANGE, "calendar_events:updated"),
        (BehaviorFeatureKind.STATE_CHANGE, "calendar_events:removed"),
        (BehaviorFeatureKind.STATE_CHANGE, "drive_files:added"),
        (BehaviorFeatureKind.STATE_CHANGE, "drive_files:updated"),
        (BehaviorFeatureKind.STATE_CHANGE, "drive_files:removed"),
        (BehaviorFeatureKind.STATE_CHANGE, "drive_shares:added"),
        (BehaviorFeatureKind.STATE_CHANGE, "sent_emails:added"),
        (BehaviorFeatureKind.PARAM_SENSITIVITY, "create_drive_file:declared=internal"),
        (BehaviorFeatureKind.PARAM_SENSITIVITY, "read_drive_file:resource=restricted"),
        (BehaviorFeatureKind.PARAM_SENSITIVITY, "share_drive_file:recipient=external"),
        (BehaviorFeatureKind.PARAM_SENSITIVITY, "share_drive_file:recipient=internal"),
        (BehaviorFeatureKind.PARAM_SENSITIVITY, "update_drive_permissions:visibility=public"),
        (
            BehaviorFeatureKind.AUTHORIZATION_TRANSITION,
            "authorized→unauthorized",
        ),
    }
    assert expected.issubset(all_values)
