"""Inspection and deterministic scheduling CLI for Office V2 Campaigns."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import docker

from sandbox.client.artifact_transfer import ArtifactTransfer
from sandbox.client.runtime_client import RuntimeClient
from sandbox.config import SandboxConfig, SandboxLimits, TraceConfig, WeekOneConfig
from sandbox.mutation.v2_docker import DockerOllamaV2MutationProvider
from sandbox.replay.artifact_store import ArtifactStore
from sandbox.replay.manifest import ManifestStore
from sandbox.replay.replay_engine import ReplayEngine
from sandbox.scheduler.docker_scheduler import DockerSandboxScheduler
from sandbox.scoring.rule_scorer import RuleBasedScorer

from .v2_campaign_store import V2CampaignStore
from .v2_orchestrator import decide_next_generation
from .v2_real_episode import DockerOfficeV2EpisodeRunner
from .v2_real_runtime import RealCampaignBootstrap, run_or_resume_real_campaign
from .v2_report import build_v2_campaign_report, write_v2_campaign_report
from .v2_scripted_runtime import (
    ScriptedCampaignBootstrap,
    run_or_resume_scripted_campaign,
)
from .v2_stage6_identity import Stage6ModelLock, Stage6Role


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trace-redteam-v2-campaign")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "plan-next"):
        command = commands.add_parser(name)
        command.add_argument("--db", type=Path, required=True)
        command.add_argument("--campaign-id", required=True)
    report = commands.add_parser("report")
    report.add_argument("--db", type=Path, required=True)
    report.add_argument("--campaign-id", required=True)
    report.add_argument("--output", type=Path, required=True)
    for name in ("run", "resume"):
        command = commands.add_parser(name)
        command.add_argument("--db", type=Path, required=True)
        command.add_argument("--campaign-id", required=True)
        command.add_argument("--bootstrap", type=Path, required=True)
        command.add_argument("--generations", type=int, default=3)
    for name in ("real-run", "real-resume"):
        command = commands.add_parser(name)
        command.add_argument("--db", type=Path, required=True)
        command.add_argument("--campaign-id", required=True)
        command.add_argument("--bootstrap", type=Path, required=True)
        command.add_argument("--model-lock", type=Path, required=True)
        command.add_argument("--agent-image", required=True)
        command.add_argument("--mutator-image", required=True)
        command.add_argument("--data-root", type=Path, required=True)
        command.add_argument("--generations", type=int, required=True)
        command.add_argument("--gpu-device", default="0")
        command.add_argument("--progress-dir", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    with V2CampaignStore(args.db) as store:
        if args.command in {"real-run", "real-resume"}:
            if args.command == "real-resume" and not store.campaign_exists(
                args.campaign_id
            ):
                raise SystemExit("real-resume requires an existing Campaign")
            bootstrap = RealCampaignBootstrap.model_validate_json(
                args.bootstrap.read_bytes()
            )
            lock = Stage6ModelLock.model_validate_json(args.model_lock.read_bytes())
            payload = _run_real(args, store, bootstrap, lock)
        elif args.command in {"run", "resume"}:
            bootstrap = ScriptedCampaignBootstrap.model_validate_json(
                args.bootstrap.read_bytes()
            )
            if args.command == "resume" and not store.campaign_exists(args.campaign_id):
                raise SystemExit("resume requires an existing Campaign")
            payload = run_or_resume_scripted_campaign(
                store=store,
                campaign_id=args.campaign_id,
                bootstrap=bootstrap,
                generation_count=args.generations,
            ).model_dump(mode="json", exclude_none=False)
        elif args.command == "inspect":
            payload = build_v2_campaign_report(
                store=store, campaign_id=args.campaign_id
            )
        elif args.command == "report":
            payload = write_v2_campaign_report(
                store=store,
                campaign_id=args.campaign_id,
                output=args.output,
            )
        else:
            state = store.load_state(args.campaign_id)
            previous = store.load_latest_generation_decision(args.campaign_id)
            if (
                previous is not None
                and previous.generation_index
                == state.lifecycle.counters.generation_index
                and previous.input_state_digest == state.state_digest
            ):
                decision = previous
            else:
                decision = decide_next_generation(
                    campaign_id=args.campaign_id,
                    state=state,
                    latest_feedback=store.load_latest_feedback(args.campaign_id),
                    previous_decision=previous,
                    previous_closure=store.load_latest_generation_closure(
                        args.campaign_id
                    ),
                )
                store.put_generation_decision(decision)
            payload = decision.model_dump(mode="json", exclude_none=False)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _run_real(args, store, bootstrap, lock: Stage6ModelLock) -> dict[str, object]:
    roles = {item.role: item for item in lock.roles}
    agent_role = roles[Stage6Role.AGENT]
    mutator_role = roles[Stage6Role.MUTATOR]
    if (
        agent_role.image_reference != args.agent_image
        or mutator_role.image_reference != args.mutator_image
        or bootstrap.model_identity_digest != lock.manifest_digest
    ):
        raise SystemExit("real Campaign arguments differ from Stage6ModelLock")
    client = docker.from_env()
    if (
        client.images.get(lock.controller_image_reference).id.lower()
        != lock.controller_image_id.lower()
    ):
        raise SystemExit("Controller image ID differs from Stage6ModelLock")
    if client.images.get(args.agent_image).id.lower() != agent_role.image_id.lower():
        raise SystemExit("Agent image ID differs from Stage6ModelLock")
    if (
        client.images.get(args.mutator_image).id.lower()
        != mutator_role.image_id.lower()
    ):
        raise SystemExit("Mutator image ID differs from Stage6ModelLock")
    artifacts = ArtifactStore(args.data_root / "artifacts")
    config = WeekOneConfig(
        sandbox=SandboxConfig(
            image=args.agent_image,
            gpu_device=args.gpu_device,
            workspace_storage="archive_volume",
            startup_timeout_seconds=600,
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
    scheduler = DockerSandboxScheduler(config.sandbox, client=client)
    engine = ReplayEngine(
        config,
        scheduler,
        RuntimeClient(config.tracing, docker_client=client),
        RuleBasedScorer(),
        ManifestStore(args.data_root / "replays"),
        artifacts,
        ArtifactTransfer(client, artifacts),
        case_source=None,
    )
    provider = DockerOllamaV2MutationProvider(
        image_ref=args.mutator_image,
        image_id=mutator_role.image_id,
        model_name=lock.model_name,
        model_identity_digest=lock.manifest_digest,
        gpu_device=args.gpu_device,
        campaign_id=args.campaign_id,
        client=client,
    )
    episode_runner = DockerOfficeV2EpisodeRunner(
        replay_engine=engine,
        artifact_store=artifacts,
        model_name=lock.model_name,
        model_digest=lock.manifest_digest,
    )
    progress_callback = None
    if args.progress_dir is not None:
        args.progress_dir.mkdir(parents=True, exist_ok=True)

        def write_progress(result) -> None:
            generation = result.completed_generation_count
            destination = args.progress_dir / f"generation-{generation:06d}.json"
            temporary = destination.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(
                    result.model_dump(mode="json", exclude_none=False), indent=2
                )
                + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, destination)

        progress_callback = write_progress
    return run_or_resume_real_campaign(
        store=store,
        campaign_id=args.campaign_id,
        bootstrap=bootstrap,
        generation_count=args.generations,
        mutation_provider=provider,
        episode_runner=episode_runner,
        runtime_identity_digest=lock.lock_digest,
        progress_callback=progress_callback,
    ).model_dump(mode="json", exclude_none=False)


if __name__ == "__main__":
    raise SystemExit(main())
