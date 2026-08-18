from __future__ import annotations

import json
import os
from pathlib import Path

import docker
import pytest
from app.adapter.factory import STAGE7_CONTROL_MODEL_DIGEST, STAGE7_CONTROL_MODEL_NAME

from sandbox.client.artifact_transfer import ArtifactTransfer
from sandbox.client.runtime_client import RuntimeClient
from sandbox.config import SandboxConfig, TraceConfig, WeekOneConfig
from sandbox.engine.case_source import TemplateCaseSource
from sandbox.protocol import OFFICE_V2_SCENARIO_ID, ExecutionRequest, ModelOptions
from sandbox.replay.artifact_store import ArtifactStore
from sandbox.replay.digests import sha256_digest
from sandbox.replay.manifest import ManifestStore
from sandbox.replay.models import CheckpointKind, ForkInjection
from sandbox.replay.replay_engine import ReplayEngine
from sandbox.scenarios.office_v2.attack_cases import build_representative_scenario_fixtures
from sandbox.scenarios.office_v2.execution_contracts import build_v2_execution_envelope
from sandbox.scheduler.docker_scheduler import DockerSandboxScheduler
from sandbox.scoring.rule_scorer import RuleBasedScorer

pytestmark = pytest.mark.skipif(
    os.environ.get("TRACE_G_RUN_DOCKER_E2E") != "1",
    reason="set TRACE_G_RUN_DOCKER_E2E=1 to run real Docker tests",
)


async def test_step5_v2_verification_only_fork_strict_replay(
    tmp_path: Path,
) -> None:
    client = docker.from_env()
    client.ping()
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
    manifests = ManifestStore(tmp_path / "replays")
    scheduler = DockerSandboxScheduler(config.sandbox, client=client)
    engine = ReplayEngine(
        config,
        scheduler,
        RuntimeClient(config.tracing, docker_client=client),
        RuleBasedScorer(),
        manifests,
        artifacts,
        ArtifactTransfer(client, artifacts),
        TemplateCaseSource(),
    )

    fixture = build_representative_scenario_fixtures()[7]
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
    parent_request = ExecutionRequest(
        execution_id="episode.step5.v2.fork.parent",
        case_id=case.case_id,
        scenario_id=OFFICE_V2_SCENARIO_ID,
        prompt=case.task.instruction,
        max_steps=60,
        timeout_seconds=180,
        metadata={"office_v2_stage7_control_mode": "partial"},
        seed=case.seed,
        model=model,
        office_v2_execution=envelope,
    )
    parent = await engine.record_request(parent_request)
    parent_digest = parent.manifest_digest
    checkpoint = next(
        item
        for item in engine.checkpoints(parent.replay_id)
        if item.recoverable
        and item.kind is CheckpointKind.BEFORE_MODEL
        and "carrier_payload_replace" in item.allowed_injection_types
    )
    child = await engine.fork(
        parent.replay_id,
        checkpoint.checkpoint_id,
        ForkInjection(
            type="carrier_payload_replace",
            content="Controlled verification-only replacement instruction.",
        ),
        operator="step5-local-acceptance",
    )
    replay = await engine.replay(
        child.replay_id,
        replay_run_id="step5-v2-verification-only-fork-replay",
    )

    assert manifests.load(parent.replay_id).manifest_digest == parent_digest
    assert child.parent_replay_id == parent.replay_id
    assert child.case_id != parent.case_id
    assert child.fork_checkpoint_id == checkpoint.checkpoint_id
    assert child.parent_prefix is not None
    assert child.parent_prefix_digest is not None
    assert replay.status.value == "matched"
    assert replay.source_behavior_digest == replay.replay_behavior_digest
    assert replay.source_final_state_digest == replay.replay_final_state_digest
    assert all(item.matched for item in replay.checkpoint_comparisons)
    assert replay.container_removed is True

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
    remaining_containers = client.containers.list(all=True, filters=owner_labels)
    remaining_volumes = client.volumes.list(filters=volume_labels)
    assert remaining_containers == []
    assert remaining_volumes == []

    output = os.environ.get("TRACE_G_STEP5_FORK_EVIDENCE_OUTPUT")
    if output:
        payload = {
            "schema_version": "office-v2-step5-fork-evidence-v1",
            "verification_only": True,
            "parent_replay_id": parent.replay_id,
            "parent_manifest_digest": parent.manifest_digest,
            "child_replay_id": child.replay_id,
            "child_manifest_digest": child.manifest_digest,
            "checkpoint_id": checkpoint.checkpoint_id,
            "parent_prefix_digest": child.parent_prefix_digest,
            "strict_replay_status": replay.status.value,
            "source_behavior_digest": replay.source_behavior_digest,
            "replay_behavior_digest": replay.replay_behavior_digest,
            "source_final_state_digest": replay.source_final_state_digest,
            "replay_final_state_digest": replay.replay_final_state_digest,
            "remaining_owned_containers": len(remaining_containers),
            "remaining_owned_volumes": len(remaining_volumes),
            "campaign_state_modified": False,
            "coverage_modified": False,
            "finding_modified": False,
            "budget_modified": False,
        }
        payload["evidence_digest"] = sha256_digest(payload)
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
