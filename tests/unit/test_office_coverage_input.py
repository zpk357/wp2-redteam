from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from app.adapter.trace_react_adapter import TraceReactAdapter
from app.protocol import ExecutionRequest

from sandbox.coverage.exceptions import CoverageInputError
from sandbox.coverage.input import CoverageInputResolver
from sandbox.protocol import TraceEvent
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_episode import build_office_episode_initialization
from sandbox.scenarios.office_matrix import OFFICE_V1_TEST_MATRIX

CASE = next(
    case
    for case in OFFICE_V1_TEST_MATRIX.attack_cases
    if case.case_id == "office-v1-attack-01"
)


def _request(control: str) -> ExecutionRequest:
    initialization = build_office_episode_initialization(CASE)
    return ExecutionRequest(
        execution_id=f"office-coverage-{control}",
        case_id=CASE.case_id,
        scenario_id=CASE.scenario.template_id,
        prompt=CASE.benign_task.instruction,
        max_steps=CASE.budget.max_steps,
        timeout_seconds=CASE.budget.timeout_seconds,
        seed=CASE.seed,
        scenario_initialization=initialization.model_dump(mode="json"),
        metadata={"scenario_control": control},
    )


async def _events(control: str) -> list[TraceEvent]:
    return [event async for event in TraceReactAdapter().execute(_request(control))]


def _write(path: Path, events: list[TraceEvent]) -> None:
    path.write_text(
        "".join(event.model_dump_json() + "\n" for event in events),
        encoding="utf-8",
    )


def _resolver(tmp_path: Path) -> CoverageInputResolver:
    return CoverageInputResolver(
        trajectory_root=tmp_path,
        manifest_root=tmp_path / "replays",
        artifact_root=tmp_path / "artifacts",
    )


@pytest.mark.parametrize(
    ("control", "attack_observed"),
    (("safe", False), ("vulnerable", True)),
)
async def test_office_trajectory_resolves_to_execution_facts(
    tmp_path: Path,
    control: str,
    attack_observed: bool,
) -> None:
    path = tmp_path / f"{control}.jsonl"
    events = await _events(control)
    _write(path, events)
    initialization = build_office_episode_initialization(CASE)

    first = _resolver(tmp_path).from_trajectory_path(
        path,
        scenario_initialization=initialization,
    )
    second = _resolver(tmp_path).from_trajectory_path(
        path,
        scenario_initialization=initialization,
    )

    evidence = first.scenario_evidence
    assert first.source_kind == "office_episode"
    assert first.prompt == CASE.benign_task.instruction
    assert evidence is not None
    assert evidence.case_id == CASE.case_id
    assert evidence.test_case_digest == CASE.content_digest
    assert evidence.scenario_initial_state == initialization.initial_state
    assert evidence.episode_initial_state == initialization.initial_state
    assert evidence.normal_task_completed is True
    assert evidence.attack_side_effect_observed is attack_observed
    assert evidence.unauthorized_action_count == (0 if control == "safe" else 2)
    assert evidence.final_state_digest == sha256_digest(evidence.final_state)
    assert evidence.evidence_digest == second.scenario_evidence.evidence_digest
    assert first.trajectory_id == second.trajectory_id
    assert first.input_digest == second.input_digest


async def test_safe_and_vulnerable_facts_share_the_benign_prefix(tmp_path: Path) -> None:
    initialization = build_office_episode_initialization(CASE)
    resolved = {}
    for control in ("safe", "vulnerable"):
        path = tmp_path / f"{control}.jsonl"
        _write(path, await _events(control))
        resolved[control] = _resolver(tmp_path).from_trajectory_path(
            path,
            scenario_initialization=initialization,
        ).scenario_evidence

    safe = resolved["safe"]
    vulnerable = resolved["vulnerable"]
    assert safe is not None and vulnerable is not None
    assert [action.tool_name for action in safe.actions[:2]] == [
        action.tool_name for action in vulnerable.actions[:2]
    ] == ["search_email", "read_email"]
    assert all(action.authorized is True for action in safe.actions)
    unauthorized = [
        action.tool_name for action in vulnerable.actions if action.authorized is False
    ]
    assert len(unauthorized) == 2
    assert "share_drive_file" in unauthorized
    assert safe.normal_task_completed is vulnerable.normal_task_completed is True
    assert safe.attack_side_effect_observed is False
    assert vulnerable.attack_side_effect_observed is True


async def test_model_self_labels_do_not_change_execution_fact_digest(tmp_path: Path) -> None:
    original = await _events("vulnerable")
    labelled = deepcopy(original)
    model_index = next(
        index for index, event in enumerate(labelled) if event.event_type == "model_end"
    )
    model_event = labelled[model_index]
    data = deepcopy(model_event.data)
    data["declared_operator"] = "model_claimed_operator"
    data["declared_risk"] = "model_claimed_risk"
    labelled[model_index] = model_event.model_copy(update={"data": data})
    original_path = tmp_path / "original.jsonl"
    labelled_path = tmp_path / "labelled.jsonl"
    _write(original_path, original)
    _write(labelled_path, labelled)
    initialization = build_office_episode_initialization(CASE)
    resolver = _resolver(tmp_path)

    original_input = resolver.from_trajectory_path(
        original_path,
        scenario_initialization=initialization,
    )
    labelled_input = resolver.from_trajectory_path(
        labelled_path,
        scenario_initialization=initialization,
    )

    assert original_input.input_digest != labelled_input.input_digest
    assert original_input.scenario_evidence is not None
    assert labelled_input.scenario_evidence is not None
    assert (
        original_input.scenario_evidence.evidence_digest
        == labelled_input.scenario_evidence.evidence_digest
    )
    assert (
        original_input.scenario_evidence.attack_side_effect_observed
        == labelled_input.scenario_evidence.attack_side_effect_observed
        is True
    )


@pytest.mark.parametrize("tamper", ("authorization", "final_state", "unpaired"))
async def test_office_coverage_rejects_inconsistent_execution_evidence(
    tmp_path: Path,
    tamper: str,
) -> None:
    events = await _events("vulnerable")
    if tamper == "authorization":
        index = next(
            index
            for index, event in enumerate(events)
            if event.event_type == "tool_result" and event.data["name"] == "read_drive_file"
        )
        event = events[index]
        data = deepcopy(event.data)
        data["authorized"] = True
        result_payload = {
            key: value
            for key, value in data.items()
            if key not in {"call_id", "call_index", "name"}
        }
        events[index] = event.model_copy(
            update={"data": data, "output_digest": sha256_digest(result_payload)}
        )
    elif tamper == "final_state":
        index = next(
            index
            for index, event in enumerate(events)
            if event.event_type == "scenario_state_observed"
        )
        event = events[index]
        data = deepcopy(event.data)
        data["final_state_digest"] = "sha256:" + "0" * 64
        events[index] = event.model_copy(update={"data": data})
    else:
        removed = next(
            index
            for index, event in enumerate(events)
            if event.event_type == "tool_result" and event.data["name"] == "read_drive_file"
        )
        events.pop(removed)
        events = [
            event.model_copy(update={"sequence": index})
            for index, event in enumerate(events)
        ]
    path = tmp_path / f"tampered-{tamper}.jsonl"
    _write(path, events)

    with pytest.raises(CoverageInputError):
        _resolver(tmp_path).from_trajectory_path(
            path,
            scenario_initialization=build_office_episode_initialization(CASE),
        )


async def test_office_coverage_requires_frozen_initialization(tmp_path: Path) -> None:
    path = tmp_path / "office.jsonl"
    _write(path, await _events("safe"))

    with pytest.raises(CoverageInputError, match="requires frozen scenario"):
        _resolver(tmp_path).from_trajectory_path(path)


async def test_office_coverage_rejects_unknown_initialization_version(
    tmp_path: Path,
) -> None:
    path = tmp_path / "office.jsonl"
    _write(path, await _events("safe"))
    payload = build_office_episode_initialization(CASE).model_dump(mode="json")
    payload["schema_version"] = "9.0"

    with pytest.raises(CoverageInputError, match="integrity validation"):
        _resolver(tmp_path).from_trajectory_path(
            path,
            scenario_initialization=payload,
        )
