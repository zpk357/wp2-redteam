from __future__ import annotations

import os

import docker
import pytest
from app.adapter.deepseek_harness_adapter import (
    HARNESS_MODEL_DIGEST,
    HARNESS_MODEL_NAME,
    DeepSeekHarnessAdapter,
)

from sandbox.client.artifact_transfer import ArtifactTransfer
from sandbox.client.runtime_client import RuntimeClient
from sandbox.config import SandboxConfig, TraceConfig, WeekOneConfig
from sandbox.fuzzer.v2_campaign_store import V2CampaignStore
from sandbox.fuzzer.v2_real_episode import DockerOfficeV2EpisodeRunner
from sandbox.fuzzer.v2_real_runtime import (
    RealCampaignBootstrap,
    run_or_resume_real_campaign,
)
from sandbox.fuzzer.v2_report import build_v2_campaign_report
from sandbox.mutation.v2_provider import RuleBasedV2MutationProvider
from sandbox.protocol import AgentRuntimeKind, ModelProvider
from sandbox.replay.artifact_store import ArtifactStore
from sandbox.replay.manifest import ManifestStore
from sandbox.replay.replay_engine import ReplayEngine
from sandbox.scheduler.docker_scheduler import DockerSandboxScheduler
from sandbox.scoring.rule_scorer import RuleBasedScorer
from tests.unit.test_office_v2_feedback_loop_batch_c import loop_fixture

pytestmark = pytest.mark.skipif(
    os.environ.get("TRACE_G_RUN_DOCKER_E2E") != "1",
    reason="set TRACE_G_RUN_DOCKER_E2E=1 to run the focused Docker acceptance",
)


def test_harness_docker_three_generation_campaign(tmp_path) -> None:
    client = docker.from_env()
    client.ping()
    image_ref = os.getenv(
        "TRACE_G_DEEPSEEK_HARNESS_IMAGE", "trace-g-deepseek-harness:h6-local"
    )
    producer = DeepSeekHarnessAdapter().producer_runtime_identity
    image = client.images.get(image_ref)
    labels = image.labels
    assert labels["org.trace-g.agent-runtime"] == producer["producer_runtime_kind"]
    assert labels["org.trace-g.runtime"] == producer["producer_runtime_version"]
    assert labels["org.trace-g.composition-sha256"] == producer[
        "producer_runtime_composition_digest"
    ].removeprefix("sha256:")

    data_root = tmp_path / "data"
    config = WeekOneConfig(
        sandbox=SandboxConfig(
            image=image_ref,
            workspace_storage="archive_volume",
            startup_timeout_seconds=30,
            execution_timeout_seconds=120,
        ),
        tracing=TraceConfig(
            output_dir=data_root / "trajectories",
            pull_interval_seconds=0.01,
        ),
    )
    artifacts = ArtifactStore(data_root / "artifacts")
    scheduler = DockerSandboxScheduler(config.sandbox, client=client)
    engine = ReplayEngine(
        config,
        scheduler,
        RuntimeClient(config.tracing, docker_client=client),
        RuleBasedScorer(),
        ManifestStore(data_root / "replays"),
        artifacts,
        ArtifactTransfer(client, artifacts),
        case_source=None,
    )
    runner = DockerOfficeV2EpisodeRunner(
        replay_engine=engine,
        artifact_store=artifacts,
        model_name=HARNESS_MODEL_NAME,
        model_digest=HARNESS_MODEL_DIGEST,
        producer_runtime_kind=AgentRuntimeKind.DEEPSEEK_HARNESS,
        producer_runtime_version=producer["producer_runtime_version"],
        producer_runtime_composition_digest=producer[
            "producer_runtime_composition_digest"
        ],
        model_provider=ModelProvider.FAKE,
        model_endpoint=None,
        max_steps=8,
        timeout_seconds=120,
    )
    _, initial_state = loop_fixture()
    campaign_id = "campaign.deepseek-harness.h6-docker"
    with V2CampaignStore(tmp_path / "campaign.sqlite3") as store:
        result = run_or_resume_real_campaign(
            store=store,
            campaign_id=campaign_id,
            bootstrap=RealCampaignBootstrap(
                initial_state=initial_state,
                model_identity_digest=HARNESS_MODEL_DIGEST,
            ),
            generation_count=3,
            mutation_provider=RuleBasedV2MutationProvider(),
            episode_runner=runner,
            producer_runtime_kind=AgentRuntimeKind.DEEPSEEK_HARNESS,
            producer_runtime_version=producer["producer_runtime_version"],
            producer_runtime_composition_digest=producer[
                "producer_runtime_composition_digest"
            ],
        )
        report = build_v2_campaign_report(store=store, campaign_id=campaign_id)

    assert result.completed_generation_count == 3
    assert report["valid_committed_episodes"] == 3
    assert len(report["decisions"]) == 3
    assert len(report["feedback"]) == 3
    assert report["decisions"][1]["input_feedback_digest"] == (
        report["feedback"][0]["feedback_digest"]
    )
    assert report["decisions"][2]["input_feedback_digest"] == (
        report["feedback"][1]["feedback_digest"]
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
