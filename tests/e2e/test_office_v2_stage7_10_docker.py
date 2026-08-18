from __future__ import annotations

import json
import os
from pathlib import Path

import docker
import pytest
from app.adapter.factory import STAGE7_CONTROL_MODEL_DIGEST, STAGE7_CONTROL_MODEL_NAME
from app.office_v2_session import OfficeV2LiveOracleArtifact

from sandbox.client.artifact_transfer import ArtifactTransfer
from sandbox.client.runtime_client import RuntimeClient
from sandbox.config import SandboxConfig, TraceConfig, WeekOneConfig
from sandbox.engine.case_source import TemplateCaseSource
from sandbox.protocol import OFFICE_V2_SCENARIO_ID, ExecutionRequest, ModelOptions
from sandbox.replay.artifact_store import ArtifactStore
from sandbox.replay.digests import sha256_digest
from sandbox.replay.manifest import ManifestStore
from sandbox.replay.replay_engine import ReplayEngine
from sandbox.scenarios.office_v2.attack_cases import (
    build_representative_scenario_fixtures,
)
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.execution_contracts import build_v2_execution_envelope
from sandbox.scheduler.docker_scheduler import DockerSandboxScheduler
from sandbox.scoring.rule_scorer import RuleBasedScorer

pytestmark = pytest.mark.skipif(
    os.environ.get("TRACE_G_RUN_DOCKER_E2E") != "1",
    reason="set TRACE_G_RUN_DOCKER_E2E=1 to run real Docker tests",
)


def _request(index: int, mode: str) -> ExecutionRequest:
    fixture = build_representative_scenario_fixtures()[index - 1]
    case = fixture.scenario_case
    model = ModelOptions(
        provider="fake",
        model_name=STAGE7_CONTROL_MODEL_NAME,
        model_digest=STAGE7_CONTROL_MODEL_DIGEST,
    )
    envelope = build_v2_execution_envelope(
        case,
        initial_state=fixture.materialization.initial_state,
        initialization_transition=fixture.materialization.initialization_transition,
        model_identity=model,
    )
    return ExecutionRequest(
        execution_id=f"episode.stage7.10.docker.{index:02d}.{mode}",
        case_id=case.case_id,
        scenario_id=OFFICE_V2_SCENARIO_ID,
        prompt=case.task.instruction,
        max_steps=60,
        timeout_seconds=180,
        metadata={"office_v2_stage7_control_mode": mode},
        seed=0,
        model=model,
        office_v2_execution=envelope,
    )


async def test_stage7_10_four_entry_docker_controls(tmp_path: Path) -> None:
    client = docker.from_env()
    client.ping()
    config = WeekOneConfig(
        sandbox=SandboxConfig(
            image=os.getenv("TRACE_G_STAGE7_10_E2E_IMAGE", "trace-g-office-v2:stage7-10-local"),
            workspace_storage="archive_volume",
            execution_timeout_seconds=240,
            startup_timeout_seconds=30,
        ),
        tracing=TraceConfig(output_dir=tmp_path / "trajectories", pull_interval_seconds=0.01),
    )
    artifacts = ArtifactStore(tmp_path / "artifacts")
    scheduler = DockerSandboxScheduler(config.sandbox, client=client)
    engine = ReplayEngine(
        config,
        scheduler,
        RuntimeClient(config.tracing, docker_client=client),
        RuleBasedScorer(),
        ManifestStore(tmp_path / "replays"),
        artifacts,
        ArtifactTransfer(client, artifacts),
        TemplateCaseSource(),
    )
    selected = (10, 7, 14, 9)
    facts = []
    for index in selected:
        for mode in ("safe", "full"):
            manifest = await engine.record_request(_request(index, mode))
            assert manifest.office_v2_oracle is not None
            artifact = OfficeV2LiveOracleArtifact.model_validate_json(
                artifacts.read_bytes(manifest.office_v2_oracle)
            )
            objective = artifact.oracle_result.security.planned_objectives[0]
            facts.append(
                {
                    "index": index,
                    "mode": mode,
                    "case_id": manifest.case_id,
                    "completion": objective.completion_kind.value,
                    "exposure": [item.value for item in objective.exposure_fact.stages],
                    "tool_calls": len(artifact.evidence_bundle.tool_exchanges),
                    "state_changed": artifact.oracle_result.final_state_digest
                    != artifact.oracle_result.initial_state_digest,
                    "oracle_digest": artifact.oracle_result.result_digest,
                }
            )
    for index in selected:
        safe = next(item for item in facts if item["index"] == index and item["mode"] == "safe")
        full = next(item for item in facts if item["index"] == index and item["mode"] == "full")
        assert safe["state_changed"] is False
        assert full["state_changed"] is True
        assert full["completion"] == "full"
    assert client.containers.list(
        all=True,
        filters={
            "label": [
                "trace-g.component=agent-sandbox",
                f"trace-g.owner-instance={scheduler.scheduler_instance_id}",
            ]
        },
    ) == []
    assert client.volumes.list(
        filters={
            "label": [
                "trace-g.component=workspace-volume",
                f"trace-g.owner-instance={scheduler.scheduler_instance_id}",
            ]
        },
    ) == []
    output = os.environ.get("TRACE_G_STAGE7_10_EVIDENCE_OUTPUT")
    if output:
        payload = {
            "schema_version": "office-v2-stage7-10-evidence-v1",
            "identity": {
                "image": config.sandbox.image,
                "image_id": client.images.get(config.sandbox.image).id,
                "provider": STAGE7_CONTROL_MODEL_NAME,
                "provider_digest": STAGE7_CONTROL_MODEL_DIGEST,
                "world_digest": load_canonical_world().world_digest,
            },
            "limitations": {"real_model_used": False, "coverage_used": False, "judge_used": False},
            "episodes": facts,
        }
        payload["evidence_digest"] = sha256_digest(payload)
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


async def test_stage7_10_compound_partial_full_docker(tmp_path: Path) -> None:
    client = docker.from_env()
    client.ping()
    config = WeekOneConfig(
        sandbox=SandboxConfig(
            image=os.getenv(
                "TRACE_G_STAGE7_10_E2E_IMAGE",
                "trace-g-office-v2:stage7-10-local",
            ),
            workspace_storage="archive_volume",
            execution_timeout_seconds=240,
            startup_timeout_seconds=30,
        ),
        tracing=TraceConfig(
            output_dir=tmp_path / "trajectories",
            pull_interval_seconds=0.01,
        ),
    )
    artifacts = ArtifactStore(tmp_path / "artifacts")
    scheduler = DockerSandboxScheduler(config.sandbox, client=client)
    engine = ReplayEngine(
        config,
        scheduler,
        RuntimeClient(config.tracing, docker_client=client),
        RuleBasedScorer(),
        ManifestStore(tmp_path / "replays"),
        artifacts,
        ArtifactTransfer(client, artifacts),
        TemplateCaseSource(),
    )
    facts = []
    for mode in ("partial", "full"):
        manifest = await engine.record_request(_request(8, mode))
        assert manifest.office_v2_oracle is not None
        artifact = OfficeV2LiveOracleArtifact.model_validate_json(
            artifacts.read_bytes(manifest.office_v2_oracle)
        )
        objective = artifact.oracle_result.security.planned_objectives[0]
        facts.append(
            {
                "mode": mode,
                "case_id": manifest.case_id,
                "completion": objective.completion_kind.value,
                "realized_milestone_ids": list(objective.realized_milestone_ids),
                "tool_calls": len(artifact.evidence_bundle.tool_exchanges),
                "oracle_digest": artifact.oracle_result.result_digest,
            }
        )
    assert facts[0]["completion"] == "partial"
    assert facts[1]["completion"] == "full"
    assert len(facts[0]["realized_milestone_ids"]) < len(
        facts[1]["realized_milestone_ids"]
    )
    assert client.containers.list(
        all=True,
        filters={
            "label": [
                "trace-g.component=agent-sandbox",
                f"trace-g.owner-instance={scheduler.scheduler_instance_id}",
            ]
        },
    ) == []
    assert client.volumes.list(
        filters={
            "label": [
                "trace-g.component=workspace-volume",
                f"trace-g.owner-instance={scheduler.scheduler_instance_id}",
            ]
        },
    ) == []
    output = os.environ.get("TRACE_G_STAGE7_10_COMPOUND_EVIDENCE_OUTPUT")
    if output:
        payload = {
            "schema_version": "office-v2-stage7-10-compound-evidence-v1",
            "identity": {
                "image": config.sandbox.image,
                "image_id": client.images.get(config.sandbox.image).id,
                "provider": STAGE7_CONTROL_MODEL_NAME,
                "provider_digest": STAGE7_CONTROL_MODEL_DIGEST,
                "world_digest": load_canonical_world().world_digest,
            },
            "episodes": facts,
        }
        payload["evidence_digest"] = sha256_digest(payload)
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


async def test_stage8_e3_single_parameter_propagation_docker(tmp_path: Path) -> None:
    client = docker.from_env()
    client.ping()
    config = WeekOneConfig(
        sandbox=SandboxConfig(
            image=os.getenv(
                "TRACE_G_STAGE7_10_E2E_IMAGE",
                "trace-g-office-v2:stage7-10-local",
            ),
            workspace_storage="archive_volume",
            execution_timeout_seconds=240,
            startup_timeout_seconds=30,
        ),
        tracing=TraceConfig(
            output_dir=tmp_path / "trajectories",
            pull_interval_seconds=0.01,
        ),
    )
    artifacts = ArtifactStore(tmp_path / "artifacts")
    scheduler = DockerSandboxScheduler(config.sandbox, client=client)
    engine = ReplayEngine(
        config,
        scheduler,
        RuntimeClient(config.tracing, docker_client=client),
        RuleBasedScorer(),
        ManifestStore(tmp_path / "replays"),
        artifacts,
        ArtifactTransfer(client, artifacts),
        TemplateCaseSource(),
    )
    facts = []
    for mode in ("safe", "full"):
        manifest = await engine.record_request(_request(23, mode))
        assert manifest.office_v2_oracle is not None
        artifact = OfficeV2LiveOracleArtifact.model_validate_json(
            artifacts.read_bytes(manifest.office_v2_oracle)
        )
        objective = artifact.oracle_result.security.planned_objectives[0]
        exchanges = artifact.evidence_bundle.tool_exchanges
        facts.append(
            {
                "mode": mode,
                "case_id": manifest.case_id,
                "completion": objective.completion_kind.value,
                "realized_milestone_ids": list(objective.realized_milestone_ids),
                "exposure": [item.value for item in objective.exposure_fact.stages],
                "state_changed": artifact.oracle_result.final_state_digest
                != artifact.oracle_result.initial_state_digest,
                "tools": [item.invocation_ref.tool_name for item in exchanges],
                "argument_sources": {
                    item.invocation_ref.tool_name: [
                        source.model_dump(mode="json") for source in item.argument_sources
                    ]
                    for item in exchanges
                    if item.argument_sources
                },
                "oracle_digest": artifact.oracle_result.result_digest,
            }
        )

    safe, full = facts
    assert safe["state_changed"] is False
    assert safe["completion"] == "none"
    assert full["state_changed"] is True
    assert full["completion"] == "full"
    assert set(full["realized_milestone_ids"]) == {
        "milestone.calendar",
        "milestone.notify",
        "milestone.record",
    }
    assert {"observed", "used"} <= set(full["exposure"])
    for tool in ("update_calendar_event", "write_file", "send_email"):
        assert tool in full["tools"]
        assert tool in full["argument_sources"]
    assert full["tools"].index("search_calendar_events") < full["tools"].index(
        "update_calendar_event"
    )
    assert client.containers.list(
        all=True,
        filters={
            "label": [
                "trace-g.component=agent-sandbox",
                f"trace-g.owner-instance={scheduler.scheduler_instance_id}",
            ]
        },
    ) == []
    assert client.volumes.list(
        filters={
            "label": [
                "trace-g.component=workspace-volume",
                f"trace-g.owner-instance={scheduler.scheduler_instance_id}",
            ]
        },
    ) == []
    output = os.environ.get("TRACE_G_STAGE8_E3_EVIDENCE_OUTPUT")
    if output:
        payload = {
            "schema_version": "office-v2-stage8-e3-evidence-v1",
            "identity": {
                "image": config.sandbox.image,
                "image_id": client.images.get(config.sandbox.image).id,
                "provider": STAGE7_CONTROL_MODEL_NAME,
                "provider_digest": STAGE7_CONTROL_MODEL_DIGEST,
                "world_digest": load_canonical_world().world_digest,
            },
            "limitations": {
                "real_model_used": False,
                "multi_parameter_contract_used": False,
                "coverage_used": False,
                "judge_used": False,
            },
            "episodes": facts,
        }
        payload["evidence_digest"] = sha256_digest(payload)
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
