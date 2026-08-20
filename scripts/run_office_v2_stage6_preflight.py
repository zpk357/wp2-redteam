#!/usr/bin/env python3
"""Run the paid-server Stage 6 Mutator and clean Agent smoke sequentially."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

import docker

from sandbox.client.artifact_transfer import ArtifactTransfer
from sandbox.client.runtime_client import RuntimeClient
from sandbox.config import SandboxConfig, SandboxLimits, TraceConfig, WeekOneConfig
from sandbox.fuzzer.v2_orchestrator import decide_next_generation
from sandbox.fuzzer.v2_real_episode import OfficeV2RecordedOracleArtifact, _recorded_agent_tokens
from sandbox.fuzzer.v2_real_runtime import RealCampaignBootstrap
from sandbox.fuzzer.v2_stage6_identity import Stage6ModelLock, Stage6Role
from sandbox.mutation.v2_brief import build_minimal_fact_brief
from sandbox.mutation.v2_docker import DockerOllamaV2MutationProvider
from sandbox.mutation.v2_plan_builder import (
    build_expression_mutation_plan,
    initial_feedback_digest,
)
from sandbox.replay.artifact_store import ArtifactStore
from sandbox.replay.manifest import ManifestStore
from sandbox.replay.models import RecordedModelDecision, RecordedToolInteraction
from sandbox.replay.replay_engine import ReplayEngine
from sandbox.scenarios.office_v2.cli_entry import (
    build_office_v2_public_request,
    office_v2_public_case,
)
from sandbox.scheduler.docker_scheduler import DockerSandboxScheduler
from sandbox.scoring.rule_scorer import RuleBasedScorer


async def run_preflight(args) -> dict[str, object]:
    lock = Stage6ModelLock.model_validate_json(args.model_lock.read_bytes())
    bootstrap = RealCampaignBootstrap.model_validate_json(args.bootstrap.read_bytes())
    if bootstrap.model_identity_digest != lock.manifest_digest:
        raise ValueError("bootstrap and Stage6 model lock differ")
    roles = {item.role: item for item in lock.roles}
    agent = roles[Stage6Role.AGENT]
    mutator = roles[Stage6Role.MUTATOR]
    if agent.image_reference != args.agent_image or mutator.image_reference != args.mutator_image:
        raise ValueError("preflight image references differ from Stage6 model lock")

    client = docker.from_env()
    controller_image = client.images.get(lock.controller_image_reference)
    if controller_image.id.lower() != lock.controller_image_id.lower():
        raise ValueError("controller image ID differs from Stage6 model lock")
    for role, reference in ((agent, args.agent_image), (mutator, args.mutator_image)):
        if client.images.get(reference).id.lower() != role.image_id.lower():
            raise ValueError(f"{role.role.value} image ID differs from Stage6 model lock")

    state = bootstrap.initial_state
    decision = decide_next_generation(
        campaign_id="stage6-preflight", state=state, latest_feedback=None
    )
    allocation = decision.allocation
    seed = next(item for item in state.corpus.seeds if item.seed_id == allocation.parent_seed_id)
    execution = next(
        item
        for item in state.corpus.execution_records
        if item.execution_record_id == allocation.supporting_execution_record_id
    )
    plan = build_expression_mutation_plan(
        decision=decision,
        parent_seed=seed,
        supporting_execution=execution,
        feedback_digest=initial_feedback_digest(
            campaign_id="stage6-preflight", state_digest=state.state_digest
        ),
        provider_id="provider-docker-ollama-v2",
        model_identity_digest=lock.manifest_digest,
    )
    brief = build_minimal_fact_brief(
        plan=plan,
        frontier_description="Stage 6 paid-server structured-output preflight.",
        operator_instructions=("Change only the frozen payload expression.",),
        scenario_facts=(),
        parent_payload_texts=(seed.payload_specs[0].content,),
    )
    provider = DockerOllamaV2MutationProvider(
        image_ref=args.mutator_image,
        image_id=mutator.image_id,
        model_name=lock.model_name,
        model_identity_digest=lock.manifest_digest,
        gpu_device=args.gpu_device,
        campaign_id="stage6-preflight",
        client=client,
    )
    mutation = await provider.generate(plan=plan, brief=brief, attempt_index=1)

    artifacts = ArtifactStore(args.data_root / "artifacts")
    config = WeekOneConfig(
        sandbox=SandboxConfig(
            image=args.agent_image,
            gpu_device=args.gpu_device,
            workspace_storage="archive_volume",
            execution_timeout_seconds=900,
            limits=SandboxLimits(
                memory_limit="14g",
                nano_cpus=8_000_000_000,
                pids_limit=512,
                tmpfs_size="2g",
            ),
        ),
        tracing=TraceConfig(output_dir=args.data_root / "trajectories"),
    )
    engine = ReplayEngine(
        config,
        DockerSandboxScheduler(config.sandbox, client=client),
        RuntimeClient(config.tracing, docker_client=client),
        RuleBasedScorer(),
        ManifestStore(args.data_root / "replays"),
        artifacts,
        ArtifactTransfer(client, artifacts),
        case_source=None,
    )
    selected = office_v2_public_case("clean.t1.apollo")
    execution_id = f"office-v2-stage6-clean-preflight-{uuid4().hex}"
    request = build_office_v2_public_request(
        selected,
        execution_id=execution_id,
        model_name=lock.model_name,
        model_digest=lock.manifest_digest,
        seed=0,
        max_steps=40,
        timeout_seconds=900,
    )
    manifest = await engine.record_request(request)
    if manifest.office_v2_oracle is None:
        raise ValueError("clean Agent preflight did not produce an Office V2 Oracle artifact")
    decision_bytes = artifacts.read_bytes(manifest.model_decisions)
    agent_tokens = _recorded_agent_tokens(decision_bytes)
    decisions = tuple(
        RecordedModelDecision.model_validate_json(line)
        for line in decision_bytes.splitlines()
        if line.strip()
    )
    tool_records = tuple(
        RecordedToolInteraction.model_validate_json(line)
        for line in artifacts.read_bytes(manifest.tool_records).splitlines()
        if line.strip()
    )
    oracle = OfficeV2RecordedOracleArtifact.model_validate_json(
        artifacts.read_bytes(manifest.office_v2_oracle)
    )
    successful_tools = tuple(
        item for item in oracle.evidence_bundle.tool_exchanges if item.status.value == "succeeded"
    )
    if not successful_tools or not tool_records:
        raise ValueError("clean Agent preflight did not complete an Office tool exchange")
    if not any(
        decision.sequence > interaction.sequence
        for interaction in tool_records
        for decision in decisions
    ):
        raise ValueError("clean Agent preflight did not decide again after a tool result")

    residue = client.containers.list(
        all=True, filters={"label": ["trace-g.component=office-v2-llm-mutator"]}
    )
    if residue:
        raise ValueError("Mutator preflight left a labeled container")
    agent_residue = client.containers.list(
        all=True, filters={"label": ["trace-g.component=agent-sandbox"]}
    )
    if agent_residue:
        raise ValueError("Agent preflight left a labeled container")
    volume_residue = client.volumes.list(
        filters={"label": [f"trace-g.execution-id={execution_id}"]}
    )
    if volume_residue:
        raise ValueError("Agent preflight left a labeled workspace volume")
    return {
        "schema_version": "office-v2-stage6-preflight-v1",
        "passed": True,
        "model_name": lock.model_name,
        "model_digest": lock.manifest_digest,
        "model_lock_digest": lock.lock_digest,
        "controller_image_reference": lock.controller_image_reference,
        "controller_image_id": lock.controller_image_id,
        "agent_image_reference": agent.image_reference,
        "agent_image_id": agent.image_id,
        "mutator_image_reference": mutator.image_reference,
        "mutator_image_id": mutator.image_id,
        "mutator_attempt_digest": mutation.attempt.attempt_digest,
        "mutator_input_tokens": mutation.attempt.input_tokens,
        "mutator_output_tokens": mutation.attempt.output_tokens,
        "agent_manifest_digest": manifest.manifest_digest,
        "agent_execution_id": execution_id,
        "agent_tokens": agent_tokens,
        "agent_model_decision_count": len(decisions),
        "agent_tool_exchange_count": len(oracle.evidence_bundle.tool_exchanges),
        "agent_successful_tool_exchange_count": len(successful_tools),
        "agent_post_tool_decision_proved": True,
        "mutator_completed_before_agent": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-lock", type=Path, required=True)
    parser.add_argument("--bootstrap", type=Path, required=True)
    parser.add_argument("--agent-image", required=True)
    parser.add_argument("--mutator-image", required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--gpu-device", default="0")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(run_preflight(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
