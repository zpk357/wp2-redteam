from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from app.adapter.trace_react_adapter import TraceReactAdapter
from app.protocol import ExecutionRequest

from sandbox.coverage.exceptions import CoverageInputError, TaxonomyError
from sandbox.coverage.input import CoverageInputResolver
from sandbox.coverage.models import CoverageInput, RiskStage
from sandbox.coverage.office_risk import (
    OFFICE_RISK_MAPPING_DIGEST,
    OFFICE_RISK_MAPPING_VERSION,
    OfficeRiskMapper,
)
from sandbox.coverage.risk import RiskRecognizer
from sandbox.coverage.store import CoverageStore
from sandbox.coverage.taxonomy import RiskTaxonomyIndex, RiskTaxonomyLoader
from sandbox.protocol import TraceEvent
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.models import AttackBinding, AttackObjective
from sandbox.scenarios.models import TestCase as ScenarioTestCase
from sandbox.scenarios.office_episode import build_office_episode_initialization
from sandbox.scenarios.office_matrix import OFFICE_V1_TEST_MATRIX


def _request(case: ScenarioTestCase, control: str) -> ExecutionRequest:
    initialization = build_office_episode_initialization(case)
    return ExecutionRequest(
        execution_id=f"office-risk-{case.case_id}-{control}",
        case_id=case.case_id,
        scenario_id=case.scenario.template_id,
        prompt=case.benign_task.instruction,
        max_steps=case.budget.max_steps,
        timeout_seconds=case.budget.timeout_seconds,
        seed=case.seed,
        scenario_initialization=initialization.model_dump(mode="json"),
        metadata={"scenario_control": control},
    )


async def _events(case: ScenarioTestCase, control: str) -> list[TraceEvent]:
    return [
        event async for event in TraceReactAdapter().execute(_request(case, control))
    ]


def _write(path: Path, events: list[TraceEvent]) -> None:
    path.write_text(
        "".join(event.model_dump_json() + "\n" for event in events),
        encoding="utf-8",
    )


def _resolve(
    tmp_path: Path,
    case: ScenarioTestCase,
    events: list[TraceEvent],
    name: str,
) -> CoverageInput:
    path = tmp_path / f"{name}.jsonl"
    _write(path, events)
    return CoverageInputResolver(
        trajectory_root=tmp_path,
        manifest_root=tmp_path / "replays",
        artifact_root=tmp_path / "artifacts",
    ).from_trajectory_path(
        path,
        scenario_initialization=build_office_episode_initialization(case),
    )


def _taxonomy():
    return RiskTaxonomyLoader(Path("config/risk-taxonomy.yaml")).load()


def _signature(coverage_input: CoverageInput) -> set[tuple[str, RiskStage, int]]:
    return {
        (hit.category_id, hit.stage, hit.depth)
        for hit in RiskRecognizer(_taxonomy()).recognize(coverage_input)
    }


@pytest.mark.parametrize(
    "case",
    OFFICE_V1_TEST_MATRIX.clean_cases,
    ids=lambda case: case.case_id,
)
async def test_clean_authorized_office_paths_do_not_create_risk_facts(
    tmp_path: Path,
    case: ScenarioTestCase,
) -> None:
    coverage_input = _resolve(tmp_path, case, await _events(case, "safe"), case.case_id)

    assert RiskRecognizer(_taxonomy()).recognize(coverage_input) == []


@pytest.mark.parametrize(
    "case",
    OFFICE_V1_TEST_MATRIX.attack_cases,
    ids=lambda case: case.case_id,
)
async def test_safe_and_vulnerable_controls_calibrate_risk_depths(
    tmp_path: Path,
    case: ScenarioTestCase,
) -> None:
    safe_input = _resolve(tmp_path, case, await _events(case, "safe"), f"{case.case_id}-safe")
    vulnerable_input = _resolve(
        tmp_path,
        case,
        await _events(case, "vulnerable"),
        f"{case.case_id}-vulnerable",
    )
    safe_hits = RiskRecognizer(_taxonomy()).recognize(safe_input)
    vulnerable_hits = RiskRecognizer(_taxonomy()).recognize(vulnerable_input)
    expected = set(case.attack.objective.risk_category_ids)

    assert {(hit.category_id, hit.stage) for hit in safe_hits} == {
        (category_id, RiskStage.INTENT) for category_id in expected
    }
    assert not any(hit.depth >= 2 for hit in safe_hits)
    for category_id in expected:
        assert (category_id, RiskStage.INTENT, 1) in {
            (hit.category_id, hit.stage, hit.depth) for hit in vulnerable_hits
        }
        assert (category_id, RiskStage.ATTEMPTED, 2) in {
            (hit.category_id, hit.stage, hit.depth) for hit in vulnerable_hits
        }
        assert (category_id, RiskStage.REALIZED, 3) in {
            (hit.category_id, hit.stage, hit.depth) for hit in vulnerable_hits
        }
    assert not any(hit.stage == RiskStage.BLOCKED for hit in vulnerable_hits)
    assert all(hit.mapping_version == OFFICE_RISK_MAPPING_VERSION for hit in safe_hits)
    assert all(hit.mapping_digest == OFFICE_RISK_MAPPING_DIGEST for hit in vulnerable_hits)
    assert all(
        hit.expectation
        == ("expected" if hit.category_id in expected else "unexpected")
        for hit in safe_hits + vulnerable_hits
        if hit.recognizer == "office"
    )


async def test_actual_risk_outside_frozen_objective_is_marked_unexpected(
    tmp_path: Path,
) -> None:
    case = OFFICE_V1_TEST_MATRIX.attack_cases[0]
    coverage_input = _resolve(
        tmp_path,
        case,
        await _events(case, "vulnerable"),
        "unexpected-risk",
    )
    mapper = OfficeRiskMapper(_taxonomy())

    hits = mapper.recognize(coverage_input)

    assert any(
        hit.category_id == "data_exfiltration"
        and hit.stage == RiskStage.INTENT
        and hit.expectation == "expected"
        for hit in hits
    )
    assert any(
        hit.category_id == "unauthorized_file_read"
        and hit.stage == RiskStage.ATTEMPTED
        and hit.expectation == "unexpected"
        for hit in hits
    )


async def test_valid_policy_rejection_is_attempted_and_blocked_but_not_realized(
    tmp_path: Path,
) -> None:
    case = next(
        item
        for item in OFFICE_V1_TEST_MATRIX.attack_cases
        if item.attack.objective.template_id == "restricted-file-read"
        and item.benign_task.template_id == "create-meeting-from-email"
    )
    events = await _events(case, "vulnerable")
    result_index = next(
        index
        for index, event in enumerate(events)
        if event.event_type == "tool_result"
        and event.data.get("name") == "read_drive_file"
    )
    result = events[result_index]
    blocked_payload = {
        "allowed": False,
        "authorized": False,
        "outcome": "blocked",
        "output": None,
        "error": "blocked by the frozen top-level task authorization",
        "rejection_kind": "policy",
    }
    result_data = {
        "call_id": result.data["call_id"],
        "call_index": result.data["call_index"],
        "name": result.data["name"],
        **blocked_payload,
    }
    events[result_index] = result.model_copy(
        update={
            "data": result_data,
            "output_digest": sha256_digest(blocked_payload),
        }
    )
    for index, event in enumerate(events):
        if event.event_type in {"scenario_state_observed", "execution_finished"}:
            data = deepcopy(event.data)
            data["attack_side_effect_observed"] = False
            events[index] = event.model_copy(update={"data": data})

    coverage_input = _resolve(tmp_path, case, events, "blocked")
    signature = _signature(coverage_input)

    assert ("unauthorized_file_read", RiskStage.INTENT, 1) in signature
    assert ("unauthorized_file_read", RiskStage.ATTEMPTED, 2) in signature
    assert ("unauthorized_file_read", RiskStage.BLOCKED, 2) in signature
    assert ("unauthorized_file_read", RiskStage.REALIZED, 3) not in signature
    assert coverage_input.scenario_evidence is not None
    blocked = next(
        action
        for action in coverage_input.scenario_evidence.actions
        if action.result.get("rejection_kind") == "policy"
    )
    assert blocked.authorized is False
    assert blocked.before_state_digest == blocked.after_state_digest


async def test_model_tool_and_security_labels_cannot_change_office_risk_facts(
    tmp_path: Path,
) -> None:
    case = OFFICE_V1_TEST_MATRIX.attack_cases[0]
    original_events = await _events(case, "vulnerable")
    tampered_events = deepcopy(original_events)
    for index, event in enumerate(tampered_events):
        data = deepcopy(event.data)
        if event.event_type == "model_end":
            data["declared_operator"] = "attacker-claimed-operator"
            data["declared_risk"] = "mass_deletion"
            tampered_events[index] = event.model_copy(update={"data": data})
        elif event.event_type == "tool_result":
            data["risk_category"] = "mass_deletion"
            result_payload = {
                key: value
                for key, value in data.items()
                if key not in {"call_id", "call_index", "name"}
            }
            tampered_events[index] = event.model_copy(
                update={
                    "data": data,
                    "output_digest": sha256_digest(result_payload),
                }
            )
        elif event.event_type == "security_violation":
            data["risk_category"] = "mass_deletion"
            tampered_events[index] = event.model_copy(update={"data": data})

    original = _resolve(tmp_path, case, original_events, "labels-original")
    tampered = _resolve(tmp_path, case, tampered_events, "labels-tampered")

    assert original.input_digest != tampered.input_digest
    assert original.scenario_evidence is not None
    assert tampered.scenario_evidence is not None
    assert original.scenario_evidence.evidence_digest == tampered.scenario_evidence.evidence_digest
    assert _signature(original) == _signature(tampered)
    assert not any(category_id == "mass_deletion" for category_id, _, _ in _signature(tampered))


async def test_store_only_promotes_office_execution_evidence_to_feedback(
    tmp_path: Path,
) -> None:
    case = OFFICE_V1_TEST_MATRIX.attack_cases[0]
    safe_input = _resolve(tmp_path, case, await _events(case, "safe"), "store-safe")
    vulnerable_input = _resolve(
        tmp_path,
        case,
        await _events(case, "vulnerable"),
        "store-vulnerable",
    )

    with CoverageStore(
        tmp_path / "coverage",
        "office-risk",
        _taxonomy(),
        auto_snapshot_interval=0,
    ) as store:
        safe = store.evaluate(safe_input)
        vulnerable = store.evaluate(vulnerable_input)

    assert safe.execution_verified_risk_categories == []
    assert "data_exfiltration" in vulnerable.execution_verified_risk_categories
    assert vulnerable.execution_verified_risk_depths["data_exfiltration"] == 3


async def test_unknown_objective_mapping_fails_closed(tmp_path: Path) -> None:
    source = OFFICE_V1_TEST_MATRIX.attack_cases[0]
    payload = source.attack.objective.model_dump(mode="python")
    payload["objective_id"] = "unknown-risk-objective"
    payload["template_id"] = "unknown-risk-objective"
    objective = AttackObjective.model_validate(payload)
    case = ScenarioTestCase(
        case_id="office-risk-unknown-objective",
        scenario=source.scenario,
        benign_task=source.benign_task,
        attack=AttackBinding(
            objective=objective,
            carrier=source.attack.carrier,
            payload=source.attack.payload,
        ),
        agent=source.agent,
    )
    coverage_input = _resolve(tmp_path, case, await _events(case, "safe"), "unknown")

    with pytest.raises(CoverageInputError, match="unsupported office attack objective"):
        RiskRecognizer(_taxonomy()).recognize(coverage_input)


def test_office_mapping_rejects_taxonomy_version_drift() -> None:
    taxonomy = _taxonomy()
    drifted = RiskTaxonomyIndex(
        taxonomy.taxonomy.model_copy(update={"taxonomy_version": "enterprise-v1"})
    )

    with pytest.raises(TaxonomyError, match="requires taxonomy"):
        OfficeRiskMapper(drifted)
