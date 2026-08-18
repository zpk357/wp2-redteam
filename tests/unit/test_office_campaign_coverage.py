from __future__ import annotations

import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest
from app.adapter.trace_react_adapter import TraceReactAdapter
from app.protocol import ExecutionRequest

from sandbox.coverage.exceptions import CoverageIntegrityError
from sandbox.coverage.input import CoverageInputResolver
from sandbox.coverage.models import CoverageInput, CoverageSnapshot
from sandbox.coverage.office_risk import (
    OFFICE_RISK_MAPPING_DIGEST,
    OFFICE_RISK_MAPPING_VERSION,
)
from sandbox.coverage.store import CoverageStore
from sandbox.coverage.taxonomy import RiskTaxonomyIndex, RiskTaxonomyLoader
from sandbox.fuzzer.models import fuzzer_digest
from sandbox.protocol import TraceEvent
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.models import TestCase as ScenarioTestCase
from sandbox.scenarios.office_episode import build_office_episode_initialization
from sandbox.scenarios.office_matrix import OFFICE_V1_TEST_MATRIX


def _request(case: ScenarioTestCase, control: str) -> ExecutionRequest:
    initialization = build_office_episode_initialization(case)
    return ExecutionRequest(
        execution_id=f"office-campaign-{case.case_id}-{control}",
        case_id=case.case_id,
        scenario_id=case.scenario.template_id,
        prompt=case.benign_task.instruction,
        max_steps=case.budget.max_steps,
        timeout_seconds=case.budget.timeout_seconds,
        seed=case.seed,
        scenario_initialization=initialization.model_dump(mode="json"),
        metadata={"scenario_control": control},
    )


async def _coverage_input(
    tmp_path: Path,
    case: ScenarioTestCase,
    control: str,
    name: str,
) -> CoverageInput:
    events: list[TraceEvent] = [
        event async for event in TraceReactAdapter().execute(_request(case, control))
    ]
    trajectory_root = tmp_path / "trajectories"
    trajectory_root.mkdir(parents=True, exist_ok=True)
    path = trajectory_root / f"{name}.jsonl"
    path.write_text(
        "".join(event.model_dump_json() + "\n" for event in events),
        encoding="utf-8",
    )
    return CoverageInputResolver(
        trajectory_root=trajectory_root,
        manifest_root=tmp_path / "replays",
        artifact_root=tmp_path / "artifacts",
    ).from_trajectory_path(
        path,
        scenario_initialization=build_office_episode_initialization(case),
    )


def _taxonomy() -> RiskTaxonomyIndex:
    return RiskTaxonomyLoader(Path("config/risk-taxonomy.yaml")).load()


def _attack_case(template_id: str) -> ScenarioTestCase:
    return next(
        case
        for case in OFFICE_V1_TEST_MATRIX.attack_cases
        if case.attack is not None and case.attack.objective.template_id == template_id
    )


def test_coverage_and_campaign_manifest_share_taxonomy_digest() -> None:
    taxonomy = _taxonomy()

    assert taxonomy.digest == fuzzer_digest(taxonomy.taxonomy)


async def test_office_campaign_is_cumulative_idempotent_and_restartable(
    tmp_path: Path,
) -> None:
    taxonomy = _taxonomy()
    share_case = _attack_case("external-restricted-file-share")
    delete_case = _attack_case("delete-drive-file")
    safe = await _coverage_input(tmp_path, share_case, "safe", "share-safe")
    vulnerable = await _coverage_input(
        tmp_path,
        share_case,
        "vulnerable",
        "share-vulnerable",
    )
    deletion = await _coverage_input(
        tmp_path,
        delete_case,
        "vulnerable",
        "delete-vulnerable",
    )
    coverage_root = tmp_path / "coverage"

    with CoverageStore(
        coverage_root,
        "office-campaign",
        taxonomy,
        auto_snapshot_interval=0,
    ) as store:
        first = store.evaluate(safe)
        duplicate = store.evaluate(safe)
        second = store.evaluate(vulnerable)
        third = store.evaluate(deletion)
        expected = store.snapshot(include_heatmap=False)

        assert duplicate.already_evaluated is True
        assert duplicate.cumulative_behavior_count == first.cumulative_behavior_count
        assert first.cumulative_behavior_count <= second.cumulative_behavior_count
        assert second.cumulative_behavior_count <= third.cumulative_behavior_count
        assert expected.total_trajectories == 3
        assert expected.taxonomy_digest == taxonomy.digest
        assert expected.risk_mapping_version == OFFICE_RISK_MAPPING_VERSION
        assert expected.risk_mapping_digest == OFFICE_RISK_MAPPING_DIGEST
        assert expected.risk_depths["data_exfiltration"] == 3
        assert expected.risk_depths["unauthorized_resource_deletion"] == 3

    with CoverageStore(
        coverage_root,
        "office-campaign",
        taxonomy,
        auto_snapshot_interval=0,
    ) as restored:
        assert restored.snapshot(include_heatmap=False) == expected
        assert restored.evaluate(vulnerable).already_evaluated is True
        assert restored.total_trajectories() == 3


def test_store_rejects_same_version_taxonomy_content_drift(tmp_path: Path) -> None:
    taxonomy = _taxonomy()
    with CoverageStore(tmp_path, "taxonomy-lock", taxonomy, auto_snapshot_interval=0):
        pass

    payload = deepcopy(taxonomy.taxonomy.model_dump(mode="python"))
    payload["categories"][0]["label"] = "drifted taxonomy content"
    drifted = RiskTaxonomyIndex(type(taxonomy.taxonomy).model_validate(payload))
    assert drifted.taxonomy_version == taxonomy.taxonomy_version
    assert drifted.digest != taxonomy.digest

    with pytest.raises(CoverageIntegrityError, match="taxonomy_digest mismatch"):
        CoverageStore(tmp_path, "taxonomy-lock", drifted, auto_snapshot_interval=0)


def test_legacy_store_is_not_silently_reidentified(tmp_path: Path) -> None:
    taxonomy = _taxonomy()
    with CoverageStore(tmp_path, "legacy-lock", taxonomy, auto_snapshot_interval=0) as store:
        database_path = store.database_path

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE metadata SET value = '1.0' WHERE key = 'schema_version'"
        )
        connection.execute("DELETE FROM metadata WHERE key = 'taxonomy_digest'")

    with pytest.raises(CoverageIntegrityError, match="missing immutable metadata"):
        CoverageStore(tmp_path, "legacy-lock", taxonomy, auto_snapshot_interval=0)


async def test_store_rejects_locked_office_mapping_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    taxonomy = _taxonomy()
    case = _attack_case("external-restricted-file-share")
    coverage_input = await _coverage_input(tmp_path, case, "safe", "mapping-lock")
    with CoverageStore(tmp_path / "coverage", "mapping-lock", taxonomy) as store:
        store.evaluate(coverage_input)

    monkeypatch.setattr(
        "sandbox.coverage.office_risk.OFFICE_RISK_MAPPING_DIGEST",
        "sha256:" + "f" * 64,
    )
    with pytest.raises(CoverageIntegrityError, match="risk mapping identity"):
        CoverageStore(tmp_path / "coverage", "mapping-lock", taxonomy)


async def test_office_mapped_campaign_rejects_unmapped_input(tmp_path: Path) -> None:
    taxonomy = _taxonomy()
    case = _attack_case("external-restricted-file-share")
    mapped = await _coverage_input(tmp_path, case, "safe", "mapped")
    unmapped = mapped.model_copy(
        update={
            "trajectory_id": "unmapped-office-trajectory",
            "input_digest": sha256_digest(
                {"source": mapped.input_digest, "scenario_evidence": None}
            ),
            "scenario_evidence": None,
        }
    )

    with CoverageStore(tmp_path / "coverage", "mapping-boundary", taxonomy) as store:
        store.evaluate(mapped)
        with pytest.raises(CoverageIntegrityError, match="cannot ingest an unmapped"):
            store.evaluate(unmapped)
        assert store.total_trajectories() == 1


async def test_office_ingest_failure_rolls_back_lock_and_partial_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    taxonomy = _taxonomy()
    case = _attack_case("restricted-file-read")
    coverage_input = await _coverage_input(tmp_path, case, "vulnerable", "rollback")

    with CoverageStore(
        tmp_path / "baseline",
        "office-rollback",
        taxonomy,
        auto_snapshot_interval=0,
    ) as baseline_store:
        baseline_store.evaluate(coverage_input)
        baseline = baseline_store.snapshot(include_heatmap=False)

    with CoverageStore(
        tmp_path / "recovery",
        "office-rollback",
        taxonomy,
        auto_snapshot_interval=0,
    ) as store:
        original_ingest = store._ingest

        def fail_ingest(connection, *_args) -> None:
            connection.execute(
                "INSERT INTO features(kind, value, first_trajectory_id) VALUES (?, ?, ?)",
                ("test", "partial", coverage_input.trajectory_id),
            )
            raise RuntimeError("injected office ingest interruption")

        monkeypatch.setattr(store, "_ingest", fail_ingest)
        with pytest.raises(RuntimeError, match="injected office ingest interruption"):
            store.evaluate(coverage_input)

        empty = store.snapshot(include_heatmap=False)
        assert empty.total_trajectories == 0
        assert empty.total_features == 0
        assert empty.risk_mapping_version is None

        monkeypatch.setattr(store, "_ingest", original_ingest)
        store.evaluate(coverage_input)
        assert store.snapshot(include_heatmap=False) == baseline


async def test_restart_repairs_snapshot_after_post_commit_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    taxonomy = _taxonomy()
    case = _attack_case("restricted-file-read")
    coverage_input = await _coverage_input(tmp_path, case, "vulnerable", "snapshot")
    coverage_root = tmp_path / "coverage"

    with CoverageStore(
        coverage_root,
        "office-snapshot",
        taxonomy,
        auto_snapshot_interval=1,
    ) as store:
        def fail_snapshot(_snapshot: CoverageSnapshot) -> Path:
            raise OSError("injected snapshot interruption")

        monkeypatch.setattr(store, "write_snapshot", fail_snapshot)
        with pytest.raises(OSError, match="injected snapshot interruption"):
            store.evaluate(coverage_input)
        assert store.total_trajectories() == 1

    with CoverageStore(
        coverage_root,
        "office-snapshot",
        taxonomy,
        auto_snapshot_interval=1,
    ) as restored:
        expected = restored.snapshot()
        snapshot_path = restored.snapshot_root / "snapshot-000001.json"
        persisted = CoverageSnapshot.model_validate_json(snapshot_path.read_text(encoding="utf-8"))

        assert persisted == expected
        assert restored.evaluate(coverage_input).already_evaluated is True
        assert restored.total_trajectories() == 1
