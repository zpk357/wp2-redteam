"""Command-line entry point for isolated execution, recording, and strict replay."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

import docker

from sandbox.client.artifact_transfer import ArtifactTransfer
from sandbox.client.runtime_client import RuntimeClient
from sandbox.config import (
    ReplayConfig,
    SandboxConfig,
    SandboxLimits,
    TraceConfig,
    WeekOneConfig,
)
from sandbox.fuzzer.models import SandboxRunContext
from sandbox.protocol import ModelOptions, ModelProvider
from sandbox.replay.artifact_store import ArtifactStore
from sandbox.replay.manifest import ManifestStore
from sandbox.replay.models import ForkInjection, ForkSuffixMode, ReplayMode
from sandbox.replay.replay_engine import ReplayEngine
from sandbox.scenarios.office_v2.cli_entry import (
    build_office_v2_public_request,
    office_v2_public_case,
    office_v2_public_cases,
)
from sandbox.scheduler.docker_scheduler import DockerSandboxScheduler
from sandbox.scoring.rule_scorer import RuleBasedScorer


def _storage_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output-dir", type=Path, default=Path("data/trajectories"))
    parser.add_argument("--artifact-dir", type=Path, default=Path("data/artifacts"))
    parser.add_argument("--manifest-dir", type=Path, default=Path("data/replays"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trace-redteam")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scenario = subparsers.add_parser("scenario", help="run the frozen Office V2 scenario")
    scenario_commands = scenario.add_subparsers(dest="scenario_command", required=True)
    scenario_commands.add_parser("list", help="list frozen Office V2 cases")
    scenario_run = scenario_commands.add_parser(
        "run", help="execute and record one Office V2 case"
    )
    scenario_run.add_argument("--case", required=True, dest="case_id")
    scenario_run.add_argument("--image", required=True)
    scenario_run.add_argument("--model-name", required=True)
    scenario_run.add_argument("--model-digest", required=True)
    scenario_run.add_argument("--seed", type=int, default=0)
    scenario_run.add_argument("--max-steps", type=int, default=60)
    scenario_run.add_argument("--timeout-seconds", type=int, default=600)
    scenario_run.add_argument("--gpu-device", default="0")
    scenario_run.add_argument("--memory-limit", default="8g")
    scenario_run.add_argument("--nano-cpus", type=int, default=4_000_000_000)
    scenario_run.add_argument("--pids-limit", type=int, default=512)
    scenario_run.add_argument("--tmpfs-size", default="1g")
    scenario_run.add_argument("--use-frozen-response", action="store_true")
    _storage_arguments(scenario_run)

    replay = subparsers.add_parser("replay", help="strictly replay a sealed package")
    replay.add_argument("--replay-id", required=True)
    replay.add_argument("--run-id")
    replay.add_argument("--campaign-id")
    replay.add_argument("--work-item-id")
    replay.add_argument("--attempt", type=int, default=1)
    replay.add_argument(
        "--mode",
        choices=[mode.value for mode in ReplayMode],
        default=ReplayMode.STRICT.value,
    )
    _storage_arguments(replay)

    checkpoints = subparsers.add_parser(
        "checkpoints",
        help="list recoverable checkpoints in a sealed replay",
    )
    checkpoints.add_argument("--replay-id", required=True)
    _storage_arguments(checkpoints)

    fork = subparsers.add_parser("fork", help="fork execution from a recoverable checkpoint")
    fork.add_argument("--parent-replay-id", required=True)
    fork.add_argument("--checkpoint-id", required=True)
    fork.add_argument(
        "--injection-type",
        required=True,
        choices=[
            "prompt_replace",
            "prompt_append",
            "carrier_payload_replace",
            "model_decision_replace",
            "tool_result_replace",
        ],
    )
    fork.add_argument("--content", required=True)
    fork.add_argument(
        "--suffix-mode",
        choices=[mode.value for mode in ForkSuffixMode],
        default=ForkSuffixMode.LIVE_AND_RECORD.value,
    )
    fork.add_argument("--operator", default="local-cli")
    _storage_arguments(fork)
    return parser


def _config(args) -> WeekOneConfig:
    return WeekOneConfig(
        seed=getattr(args, "seed", 42),
        sandbox=SandboxConfig(
            image=getattr(args, "image", "trace-redteam-agent:server"),
            workspace_storage=(
                "archive_volume"
                if args.command in {"scenario", "replay", "fork"}
                else "tmpfs"
            ),
            execution_timeout_seconds=getattr(args, "timeout_seconds", 120),
            gpu_device=getattr(args, "gpu_device", None),
            limits=SandboxLimits(
                memory_limit=getattr(args, "memory_limit", "512m"),
                nano_cpus=getattr(args, "nano_cpus", 1_000_000_000),
                pids_limit=getattr(args, "pids_limit", 128),
                tmpfs_size=getattr(args, "tmpfs_size", "64m"),
            ),
        ),
        tracing=TraceConfig(output_dir=args.output_dir),
        replay=ReplayConfig(
            artifact_dir=getattr(args, "artifact_dir", Path("data/artifacts")),
            manifest_dir=getattr(args, "manifest_dir", Path("data/replays")),
        ),
        model=ModelOptions(
            provider=ModelProvider.FAKE,
            model_name="unused-host-placeholder",
        ),
    )


def _replay_engine(config: WeekOneConfig) -> ReplayEngine:
    docker_client = docker.from_env()
    artifact_store = ArtifactStore(config.replay.artifact_dir)
    scheduler = DockerSandboxScheduler(config.sandbox, client=docker_client)
    runtime = RuntimeClient(config.tracing, docker_client=docker_client)
    return ReplayEngine(
        config,
        scheduler,
        runtime,
        RuleBasedScorer(),
        ManifestStore(config.replay.manifest_dir),
        artifact_store,
        ArtifactTransfer(docker_client, artifact_store),
        case_source=None,
    )


def _fork_injection_content(args):
    if args.injection_type in {
        "prompt_replace",
        "prompt_append",
        "carrier_payload_replace",
    }:
        return args.content
    return json.loads(args.content)


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "scenario" and args.scenario_command == "list":
        payload = [
            {"case_id": item.public_id, "kind": item.kind}
            for item in office_v2_public_cases()
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    config = _config(args)
    if args.command == "scenario":
        selected = office_v2_public_case(args.case_id)
        execution_id = f"scenario-{uuid4().hex}"
        request = build_office_v2_public_request(
            selected,
            execution_id=execution_id,
            model_name=args.model_name,
            model_digest=args.model_digest,
            seed=args.seed,
            max_steps=args.max_steps,
            timeout_seconds=args.timeout_seconds,
            use_frozen_response=args.use_frozen_response,
        )
        manifest = asyncio.run(_replay_engine(config).record_request(request))
        print(json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 0

    if args.command == "checkpoints":
        artifacts = ArtifactStore(config.replay.artifact_dir)
        engine = ReplayEngine(
            config,
            scheduler=None,
            runtime=None,
            scorer=RuleBasedScorer(),
            manifest_store=ManifestStore(config.replay.manifest_dir),
            artifact_store=artifacts,
            artifact_transfer=None,
            case_source=None,
        )
        checkpoints = engine.checkpoints(args.replay_id)
        print(
            json.dumps(
                [checkpoint.model_dump(mode="json") for checkpoint in checkpoints],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    engine = _replay_engine(config)

    if args.command == "fork":
        content = _fork_injection_content(args)
        manifest = asyncio.run(
            engine.fork(
                args.parent_replay_id,
                args.checkpoint_id,
                ForkInjection(type=args.injection_type, content=content),
                suffix_mode=ForkSuffixMode(args.suffix_mode),
                operator=args.operator,
            )
        )
        print(json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 0

    run_context = None
    if args.command == "replay" and (
        args.campaign_id is not None or args.work_item_id is not None
    ):
        if args.campaign_id is None or args.work_item_id is None:
            raise SystemExit("replay Campaign labels require both IDs")
        run_context = SandboxRunContext(
            campaign_id=args.campaign_id,
            work_item_id=args.work_item_id,
            attempt=args.attempt,
        )
    result = asyncio.run(
        engine.replay(
            args.replay_id,
            mode=ReplayMode(args.mode),
            replay_run_id=args.run_id,
            run_context=run_context,
        )
    )
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return int(result.status.value != "matched" or not result.container_removed)


if __name__ == "__main__":
    raise SystemExit(main())
