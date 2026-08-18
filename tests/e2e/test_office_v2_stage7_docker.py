from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import docker
import pytest
from app.adapter.factory import (
    STAGE7_DETERMINISTIC_MODEL_DIGEST,
    STAGE7_DETERMINISTIC_MODEL_NAME,
)
from app.office_v2_session import OfficeV2LiveOracleArtifact, OfficeV2RecordingState

from sandbox.client.artifact_transfer import ArtifactTransfer
from sandbox.client.runtime_client import RuntimeClient
from sandbox.config import SandboxConfig, TraceConfig, WeekOneConfig
from sandbox.engine.case_source import TemplateCaseSource
from sandbox.protocol import (
    OFFICE_V2_SCENARIO_ID,
    ExecutionRequest,
    ModelOptions,
)
from sandbox.replay.artifact_store import ArtifactStore
from sandbox.replay.digests import sha256_digest
from sandbox.replay.manifest import ManifestStore
from sandbox.replay.replay_engine import ReplayEngine
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASE_BY_ID
from sandbox.scenarios.office_v2.execution_contracts import build_v2_execution_envelope
from sandbox.scenarios.office_v2.interaction_session import ScriptedResponseDirective
from sandbox.scheduler.docker_scheduler import DockerSandboxScheduler
from sandbox.scoring.rule_scorer import RuleBasedScorer

pytestmark = pytest.mark.skipif(
    os.environ.get("TRACE_G_RUN_DOCKER_E2E") != "1",
    reason="set TRACE_G_RUN_DOCKER_E2E=1 to run real Docker tests",
)


class TrackingScheduler(DockerSandboxScheduler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.container_snapshots: list[dict[str, Any]] = []

    async def create(self, *args: Any, **kwargs: Any):
        handle = await super().create(*args, **kwargs)
        container = self.client.containers.get(handle.container_id)
        container.reload()
        self.container_snapshots.append(
            {
                "container_id": handle.container_id,
                "user": container.attrs["Config"]["User"],
                "read_only": container.attrs["HostConfig"]["ReadonlyRootfs"],
                "network_mode": container.attrs["HostConfig"]["NetworkMode"],
                "privileged": container.attrs["HostConfig"]["Privileged"],
                "mounts": container.attrs.get("Mounts", []),
            }
        )
        return handle


def _artifact_events(artifacts: ArtifactStore, reference: Any) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in artifacts.read_bytes(reference).decode("utf-8").splitlines()
        if line
    ]


def _write_evidence(output: Path, payload: dict[str, Any]) -> None:
    payload["evidence_digest"] = sha256_digest(payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _request(case_id: str, *, max_steps: int) -> ExecutionRequest:
    case = CLEAN_CASE_BY_ID[case_id]
    contract = case.task.user_response_script
    request_contract = contract.requests[0]
    rule = contract.response_rules[0]
    directive = ScriptedResponseDirective(
        request_id=request_contract.request_id,
        rule_id=rule.rule_id,
        turn_id=f"turn.stage7.9.docker.{case_id}",
        responder_id=rule.authenticated_responder_id,
        authenticated_principal_id=rule.authenticated_responder_id,
    )
    model = ModelOptions(
        provider="fake",
        model_name=STAGE7_DETERMINISTIC_MODEL_NAME,
        model_digest=STAGE7_DETERMINISTIC_MODEL_DIGEST,
    )
    envelope = build_v2_execution_envelope(
        case,
        initial_state=load_canonical_world().state,
        model_identity=model,
        response_directives=(directive,),
    )
    return ExecutionRequest(
        execution_id=f"episode-stage7-9-docker-{case_id.replace('.', '-')}",
        case_id=case_id,
        scenario_id=OFFICE_V2_SCENARIO_ID,
        prompt=case.task.instruction,
        max_steps=max_steps,
        timeout_seconds=120,
        seed=0,
        model=model,
        office_v2_execution=envelope,
    )


async def test_stage7_9_record_replay_isolated_episodes_and_zero_residue(
    tmp_path: Path,
) -> None:
    docker_client = docker.from_env()
    docker_client.ping()
    config = WeekOneConfig(
        sandbox=SandboxConfig(
            image=os.getenv("TRACE_G_STAGE7_E2E_IMAGE", "trace-g-office-v2:stage7-local"),
            workspace_storage="archive_volume",
            execution_timeout_seconds=180,
            startup_timeout_seconds=30,
        ),
        tracing=TraceConfig(
            output_dir=tmp_path / "trajectories",
            pull_interval_seconds=0.01,
        ),
    )
    artifacts = ArtifactStore(tmp_path / "artifacts")
    scheduler = TrackingScheduler(config.sandbox, client=docker_client)
    engine = ReplayEngine(
        config,
        scheduler,
        RuntimeClient(config.tracing, docker_client=docker_client),
        RuleBasedScorer(),
        ManifestStore(tmp_path / "replays"),
        artifacts,
        ArtifactTransfer(docker_client, artifacts),
        TemplateCaseSource(),
    )

    evidence = {}
    for case_id, max_steps in (("clean.t2.delta", 40), ("clean.t9.apollo", 30)):
        before = len(scheduler.container_snapshots)
        manifest = await engine.record_request(_request(case_id, max_steps=max_steps))
        assert len(scheduler.container_snapshots) == before + 1
        assert manifest.office_v2_oracle is not None
        oracle = OfficeV2LiveOracleArtifact.model_validate_json(
            artifacts.read_bytes(manifest.office_v2_oracle)
        )
        assert oracle.oracle_result.utility.disposition.value == "completed"
        assert oracle.oracle_result.security.planned_objectives == ()

        replay = await engine.replay(
            manifest.replay_id,
            replay_run_id=f"stage7-9-{case_id.replace('.', '-')}-strict",
        )
        assert len(scheduler.container_snapshots) == before + 2
        assert replay.status.value == "matched"
        assert replay.source_behavior_digest == replay.replay_behavior_digest
        assert replay.source_final_state_digest == replay.replay_final_state_digest
        assert replay.checkpoint_comparisons
        assert all(item.matched for item in replay.checkpoint_comparisons)
        assert replay.container_removed is True
        evidence[case_id] = (manifest, oracle, replay)

    t9_manifest = evidence["clean.t9.apollo"][0]
    assert t9_manifest.office_v2_recording_state is not None
    t9_state = OfficeV2RecordingState.model_validate_json(
        artifacts.read_bytes(t9_manifest.office_v2_recording_state)
    ).session.state
    assert len(t9_state.delegation_grants) == 1

    assert len(scheduler.container_snapshots) == 4
    for snapshot in scheduler.container_snapshots:
        assert snapshot["user"] == "10001:10001"
        assert snapshot["read_only"] is True
        assert snapshot["network_mode"] == "none"
        assert snapshot["privileged"] is False
        assert all(mount["Type"] == "volume" for mount in snapshot["mounts"])

    assert docker_client.containers.list(
        all=True,
        filters={
            "label": [
                "trace-g.component=agent-sandbox",
                f"trace-g.owner-instance={scheduler.scheduler_instance_id}",
            ]
        },
    ) == []
    assert docker_client.volumes.list(
        filters={
            "label": [
                "trace-g.component=workspace-volume",
                f"trace-g.owner-instance={scheduler.scheduler_instance_id}",
            ]
        },
    ) == []

    evidence_output = os.environ.get("TRACE_G_STAGE7_EVIDENCE_OUTPUT")
    if evidence_output:
        image = docker_client.images.get(config.sandbox.image)
        episode_facts = []
        for case_id in ("clean.t2.delta", "clean.t9.apollo"):
            manifest, oracle, replay = evidence[case_id]
            events = _artifact_events(artifacts, manifest.events)
            decisions = _artifact_events(artifacts, manifest.model_decisions)
            tool_calls = [
                item["data"]["name"]
                for item in events
                if item["event_type"] == "tool_call"
            ]
            submits = [item for item in events if item["event_type"] == "agent_submit"]
            state = OfficeV2RecordingState.model_validate_json(
                artifacts.read_bytes(manifest.office_v2_recording_state)
            ).session.state
            episode_facts.append(
                {
                    "case_id": case_id,
                    "case_digest": CLEAN_CASE_BY_ID[case_id].case_digest,
                    "manifest_digest": manifest.manifest_digest,
                    "recording_complete": manifest.recording_complete,
                    "model_decision_count": len(decisions),
                    "tool_call_count": len(tool_calls),
                    "tool_sequence": tool_calls,
                    "explicit_submit_count": len(submits),
                    "delegation_grant_count": len(state.delegation_grants),
                    "utility_disposition": oracle.oracle_result.utility.disposition.value,
                    "planned_objective_count": len(
                        oracle.oracle_result.security.planned_objectives
                    ),
                    "unexpected_violation_count": sum(
                        not item.planned
                        for item in oracle.oracle_result.security.violations
                    ),
                    "oracle_result_digest": oracle.oracle_result.result_digest,
                    "source_behavior_digest": replay.source_behavior_digest,
                    "replay_behavior_digest": replay.replay_behavior_digest,
                    "source_final_state_digest": replay.source_final_state_digest,
                    "replay_final_state_digest": replay.replay_final_state_digest,
                    "replay_status": replay.status.value,
                    "checkpoint_count": len(replay.checkpoint_comparisons),
                    "all_checkpoints_matched": all(
                        item.matched for item in replay.checkpoint_comparisons
                    ),
                    "replay_container_removed": replay.container_removed,
                }
            )
        owner_labels = {
            "label": [
                "trace-g.component=agent-sandbox",
                f"trace-g.owner-instance={scheduler.scheduler_instance_id}",
            ]
        }
        volume_labels = {
            "label": [
                "trace-g.component=workspace-volume",
                f"trace-g.owner-instance={scheduler.scheduler_instance_id}",
            ]
        }
        payload = {
            "schema_version": "office-v2-stage7-9-evidence-v1",
            "evidence_class": "local_deterministic_docker_record_replay",
            "limitations": {
                "docker_used": True,
                "deterministic_provider_used": True,
                "real_model_used": False,
                "server_used": False,
                "coverage_or_mutation_used": False,
                "llm_judge_used": False,
            },
            "identity": {
                "image_ref": config.sandbox.image,
                "image_id": image.id,
                "image_repo_digests": sorted(image.attrs.get("RepoDigests", [])),
                "image_size_bytes": image.attrs["Size"],
                "provider_name": STAGE7_DETERMINISTIC_MODEL_NAME,
                "provider_digest": STAGE7_DETERMINISTIC_MODEL_DIGEST,
                "world_digest": load_canonical_world().world_digest,
                "execution_backend": "trace_react_v2",
                "trace_schema_version": "1.2",
                "state_codec_version": "office-v2-state-codec-v1",
            },
            "episodes": episode_facts,
            "isolation": {
                "container_count": len(scheduler.container_snapshots),
                "profiles": [
                    {
                        "user": item["user"],
                        "read_only": item["read_only"],
                        "network_mode": item["network_mode"],
                        "privileged": item["privileged"],
                        "mount_types": sorted(
                            mount["Type"] for mount in item["mounts"]
                        ),
                    }
                    for item in scheduler.container_snapshots
                ],
                "remaining_owned_containers": len(
                    docker_client.containers.list(all=True, filters=owner_labels)
                ),
                "remaining_owned_volumes": len(
                    docker_client.volumes.list(filters=volume_labels)
                ),
            },
        }
        _write_evidence(Path(evidence_output), payload)
