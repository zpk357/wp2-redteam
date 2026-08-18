"""Run the bounded 5.G4 self-contained Agent-Qwen Docker acceptance gate."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

import docker
from docker.errors import NotFound

from sandbox.client.artifact_transfer import ArtifactTransfer
from sandbox.client.runtime_client import RuntimeClient
from sandbox.config import ReplayConfig, SandboxConfig, SandboxLimits, TraceConfig, WeekOneConfig
from sandbox.coverage.behavior import BehaviorFeatureExtractor
from sandbox.coverage.input import CoverageInputResolver
from sandbox.coverage.risk import RiskRecognizer
from sandbox.coverage.taxonomy import RiskTaxonomyLoader
from sandbox.engine.case_source import TemplateCaseSource
from sandbox.protocol import ExecutionBackend, ExecutionRequest, ModelOptions, ModelProvider
from sandbox.replay.artifact_store import ArtifactStore
from sandbox.replay.canonical import canonical_json_bytes
from sandbox.replay.manifest import ManifestStore
from sandbox.replay.models import (
    ArtifactRef,
    CheckpointStateEnvelope,
    ForkInjection,
    ReplayManifest,
)
from sandbox.replay.normalizer import normalize_behavior_trace
from sandbox.replay.replay_engine import ReplayEngine
from sandbox.scenarios.models import AgentConfig, ExecutionBudget, TestCase
from sandbox.scenarios.office_episode import build_office_episode_initialization
from sandbox.scenarios.office_fork import OfficeCarrierForkError, replace_office_carrier_payload
from sandbox.scenarios.office_matrix import OFFICE_V1_TEST_MATRIX
from sandbox.scheduler.docker_scheduler import DockerSandboxScheduler
from sandbox.scoring.rule_scorer import RuleBasedScorer

MODEL_NAME = "qwen3:8b"
MODEL_DIGEST = "sha256:500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41"
MODEL_ENDPOINT = "http://127.0.0.1:11434"
RUN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class EvidenceScheduler(DockerSandboxScheduler):
    """Record launch modes and prove whether Ollama exists in each ready container."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.launches: list[dict[str, Any]] = []

    async def create(self, execution_id, image_ref, limits, **kwargs):
        handle = await super().create(execution_id, image_ref, limits, **kwargs)
        container = self.client.containers.get(handle.container_id)
        container.image.reload()
        self.launches.append(
            {
                "execution_id": execution_id,
                "container_id": handle.container_id,
                "mode": kwargs.get("execution_mode", "live"),
                "image_digest": handle.image_digest,
                "image_id": container.image.id.lower(),
            }
        )
        return handle

    async def wait_until_ready(self, handle) -> None:
        await super().wait_until_ready(handle)
        container = self.client.containers.get(handle.container_id)
        container.reload()
        process_table = container.top()
        flattened = "\n".join(
            " ".join(str(field) for field in row)
            for row in process_table.get("Processes", [])
        )
        launch = next(item for item in self.launches if item["container_id"] == handle.container_id)
        launch["ollama_process_present"] = "/usr/bin/ollama serve" in flattened
        host_config = container.attrs.get("HostConfig") or {}
        mounts = container.attrs.get("Mounts") or []
        launch["isolation"] = {
            "network_mode": host_config.get("NetworkMode"),
            "read_only_rootfs": host_config.get("ReadonlyRootfs"),
            "privileged": host_config.get("Privileged"),
            "cap_drop": sorted(host_config.get("CapDrop") or []),
            "security_opt": sorted(host_config.get("SecurityOpt") or []),
            "bind_mount_count": sum(
                1 for mount in mounts if mount.get("Type") == "bind"
            ),
            "docker_socket_mounted": any(
                mount.get("Destination") == "/var/run/docker.sock" for mount in mounts
            ),
            "gpu_device_requests": len(host_config.get("DeviceRequests") or []),
        }
        if launch["mode"] == "strict_replay" and launch["ollama_process_present"]:
            raise RuntimeError("strict replay container started Ollama")
        if launch["mode"] == "live" and not launch["ollama_process_present"]:
            raise RuntimeError("live Agent container has no Ollama process")

    async def destroy(self, handle) -> None:
        await super().destroy(handle)
        try:
            self.client.containers.get(handle.container_id)
        except NotFound:
            launch = next(
                item for item in self.launches if item["container_id"] == handle.container_id
            )
            launch["removed"] = True
            return
        raise RuntimeError("acceptance container still exists after destroy")


def formal_attack_case() -> TestCase:
    source = OFFICE_V1_TEST_MATRIX.attack_cases[0]
    payload = source.model_dump(mode="json")
    payload["agent"] = AgentConfig(
        provider="ollama",
        model_name=MODEL_NAME,
        model_digest=MODEL_DIGEST,
        endpoint=MODEL_ENDPOINT,
    ).model_dump(mode="json")
    payload["budget"] = ExecutionBudget(
        max_steps=source.budget.max_steps,
        timeout_seconds=600,
        max_output_tokens=source.budget.max_output_tokens,
    ).model_dump(mode="json")
    payload["content_digest"] = None
    return TestCase.model_validate(payload)


def formal_request(
    case: TestCase,
    *,
    execution_id: str = "g4-parent-local",
    gate: str = "5.G4",
) -> ExecutionRequest:
    initialization = build_office_episode_initialization(case)
    return ExecutionRequest(
        execution_id=execution_id,
        case_id=case.case_id,
        scenario_id=case.scenario.template_id,
        prompt=case.benign_task.instruction,
        max_steps=case.budget.max_steps,
        timeout_seconds=case.budget.timeout_seconds,
        seed=case.seed,
        execution_backend=ExecutionBackend.TRACE_REACT_V2,
        model=ModelOptions(
            provider=ModelProvider.OLLAMA,
            model_name=MODEL_NAME,
            model_digest=MODEL_DIGEST,
            endpoint=MODEL_ENDPOINT,
            timeout_seconds=600,
        ),
        scenario_initialization=initialization.model_dump(mode="json"),
        metadata={"acceptance_gate": gate},
    )


def reachable_artifacts(manifest: ReplayManifest, store: ArtifactStore) -> dict[str, bytes]:
    references: list[ArtifactRef] = [
        manifest.prompt,
        manifest.events,
        manifest.initial_state,
        manifest.determinism_config,
        manifest.model_decisions,
        manifest.tool_records,
        manifest.checkpoints,
    ]
    references.extend(
        item
        for item in (manifest.recording_audit, manifest.parent_prefix)
        if item is not None
    )
    for line in store.read_bytes(manifest.checkpoints).splitlines():
        if not line.strip():
            continue
        state = json.loads(line).get("state_artifact")
        if state is not None:
            references.append(ArtifactRef.model_validate(state))
    return {item.sha256: store.read_bytes(item) for item in references}


def select_carrier_checkpoint(
    engine: ReplayEngine,
    manifest: ReplayManifest,
    replacement: str,
):
    fallback = None
    for checkpoint in engine.checkpoints(manifest.replay_id):
        if "carrier_payload_replace" not in checkpoint.allowed_injection_types:
            continue
        if checkpoint.state_artifact is None:
            continue
        envelope = CheckpointStateEnvelope.model_validate_json(
            engine.artifact_store.read_bytes(checkpoint.state_artifact)
        )
        try:
            replace_office_carrier_payload(envelope, replacement)
        except OfficeCarrierForkError:
            continue
        fallback = fallback or checkpoint
        if checkpoint.kind == "after_tool":
            return checkpoint
    if fallback is None:
        raise RuntimeError("recording contains no unexposed carrier fork checkpoint")
    return fallback


def coverage_signature(resolver: CoverageInputResolver, target) -> dict[str, Any]:
    coverage = (
        resolver.from_manifest(target)
        if isinstance(target, ReplayManifest)
        else resolver.resolve(trajectory_id=target)
    )
    if coverage.scenario_evidence is None:
        raise RuntimeError("formal office trajectory has no scenario evidence")
    profile = BehaviorFeatureExtractor().extract(
        trajectory_id=coverage.trajectory_id,
        execution_id=coverage.execution_id,
        events=normalize_behavior_trace(coverage.events),
        office_evidence=coverage.scenario_evidence,
    )
    taxonomy = RiskTaxonomyLoader(Path("config/risk-taxonomy.yaml")).load()
    risks = sorted(
        (hit.category_id, hit.stage, hit.depth)
        for hit in RiskRecognizer(taxonomy).recognize(coverage)
    )
    return {"behavior_profile_hash": profile.profile_hash, "risk_signature": risks}


async def run(args) -> dict[str, Any]:
    gate = getattr(args, "gate", "5.G4")
    run_id = getattr(args, "run_id", "g4-local")
    if gate not in {"5.G4", "5.G5"}:
        raise ValueError("gate must be 5.G4 or 5.G5")
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("run_id must contain only lowercase letters, digits, and hyphens")
    root = args.output_root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    trajectories = root / "trajectories"
    artifacts_path = root / "artifacts"
    manifests_path = root / "replays"
    client = docker.from_env()
    image = client.images.get(args.image)
    image_id = image.id.lower()
    expected_image_id = getattr(args, "expected_image_id", None)
    if expected_image_id is not None and image_id != expected_image_id.lower():
        raise RuntimeError(
            f"Agent-Qwen image identity mismatch: expected {expected_image_id}, observed {image_id}"
        )
    labels = image.attrs.get("Config", {}).get("Labels") or {}
    expected_model_digest = getattr(args, "expected_model_digest", MODEL_DIGEST)
    expected_labels = {
        "org.trace-g.runtime": "self-contained-agent-qwen",
        "org.trace-g.agent-framework": "langgraph",
        "org.trace-g.model.name": MODEL_NAME,
        "org.trace-g.model.digest": expected_model_digest,
    }
    if any(labels.get(key) != value for key, value in expected_labels.items()):
        raise RuntimeError("Agent-Qwen image labels do not match the acceptance contract")
    config = WeekOneConfig(
        sandbox=SandboxConfig(
            image=args.image,
            network_mode="none",
            startup_timeout_seconds=300,
            execution_timeout_seconds=600,
            workspace_storage="archive_volume",
            gpu_device=args.gpu_device,
            limits=SandboxLimits(
                memory_limit="8g",
                nano_cpus=4_000_000_000,
                pids_limit=512,
                tmpfs_size="1g",
            ),
        ),
        tracing=TraceConfig(
            output_dir=trajectories,
            pull_interval_seconds=0.5,
            request_timeout_seconds=120,
        ),
        replay=ReplayConfig(artifact_dir=artifacts_path, manifest_dir=manifests_path),
    )
    scheduler = EvidenceScheduler(config.sandbox, client=client)
    runtime = RuntimeClient(config.tracing, docker_client=client)
    artifacts = ArtifactStore(artifacts_path)
    manifests = ManifestStore(manifests_path)
    engine = ReplayEngine(
        config,
        scheduler,
        runtime,
        RuleBasedScorer(),
        manifests,
        artifacts,
        ArtifactTransfer(client, artifacts),
        TemplateCaseSource(),
    )
    case = formal_attack_case()
    parent = await engine.record_request(
        formal_request(case, execution_id=f"{run_id}-parent", gate=gate)
    )
    parent_manifest_bytes = canonical_json_bytes(parent)
    parent_artifacts = reachable_artifacts(parent, artifacts)

    parent_replay = await engine.replay(
        parent.replay_id,
        replay_run_id=f"{run_id}-parent-strict",
    )
    if parent_replay.status != "matched" or not parent_replay.container_removed:
        raise RuntimeError("parent strict replay did not match")

    replacement = case.attack.payload + "\nSynthetic G4 carrier variant B."
    checkpoint = select_carrier_checkpoint(engine, parent, replacement)
    child = await engine.fork(
        parent.replay_id,
        checkpoint.checkpoint_id,
        ForkInjection(type="carrier_payload_replace", content=replacement),
        execution_id=f"{run_id}-child",
        child_replay_id=f"{run_id}-child-replay",
        operator=f"{gate}-acceptance",
    )
    if canonical_json_bytes(manifests.load(parent.replay_id)) != parent_manifest_bytes:
        raise RuntimeError("carrier fork mutated the parent manifest")
    if reachable_artifacts(parent, artifacts) != parent_artifacts:
        raise RuntimeError("carrier fork mutated a parent artifact")

    child_replay = await engine.replay(
        child.replay_id,
        replay_run_id=f"{run_id}-child-strict",
    )
    if child_replay.status != "matched" or not child_replay.container_removed:
        raise RuntimeError("child strict replay did not match")

    resolver = CoverageInputResolver(
        trajectory_root=trajectories,
        manifest_root=manifests_path,
        artifact_root=artifacts_path,
    )
    parent_source_signature = coverage_signature(resolver, parent)
    parent_replay_signature = coverage_signature(
        resolver, parent_replay.replay_trajectory_id
    )
    child_source_signature = coverage_signature(resolver, child)
    child_replay_signature = coverage_signature(
        resolver, child_replay.replay_trajectory_id
    )
    if parent_source_signature != parent_replay_signature:
        raise RuntimeError("parent replay changed behavior or risk coverage")
    if child_source_signature != child_replay_signature:
        raise RuntimeError("child replay changed behavior or risk coverage")
    if len(scheduler.launches) != 4 or any(
        launch.get("removed") is not True for launch in scheduler.launches
    ):
        raise RuntimeError("acceptance did not clean up every disposable container")
    for launch in scheduler.launches:
        if launch["image_id"] != image_id:
            raise RuntimeError("acceptance launch used an unexpected image identity")
        isolation = launch.get("isolation") or {}
        if (
            isolation.get("network_mode") != "none"
            or isolation.get("read_only_rootfs") is not True
            or isolation.get("privileged") is not False
            or isolation.get("cap_drop") != ["ALL"]
            or "no-new-privileges:true" not in isolation.get("security_opt", [])
            or isolation.get("bind_mount_count") != 0
            or isolation.get("docker_socket_mounted") is not False
            or isolation.get("gpu_device_requests")
            != (1 if launch["mode"] == "live" else 0)
        ):
            raise RuntimeError("acceptance launch violated the container isolation contract")

    result = {
        "schema_version": "1.0",
        "gate": gate,
        "run_id": run_id,
        "image": args.image,
        "image_id": image_id,
        "model_name": MODEL_NAME,
        "model_digest": expected_model_digest,
        "image_labels": {key: labels[key] for key in sorted(expected_labels)},
        "parent_replay_id": parent.replay_id,
        "parent_strict_status": parent_replay.status,
        "fork_checkpoint_id": checkpoint.checkpoint_id,
        "child_replay_id": child.replay_id,
        "child_strict_status": child_replay.status,
        "parent_immutable": True,
        "parent_coverage": parent_source_signature,
        "child_coverage": child_source_signature,
        "launches": scheduler.launches,
    }
    (root / "acceptance.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--image", default="trace-redteam-agent-qwen:server")
    parser.add_argument("--gpu-device", default="0")
    parser.add_argument("--gate", choices=("5.G4", "5.G5"), default="5.G4")
    parser.add_argument("--run-id", default="g4-local")
    parser.add_argument("--expected-image-id")
    parser.add_argument("--expected-model-digest", default=MODEL_DIGEST)
    args = parser.parse_args()
    result = asyncio.run(run(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
