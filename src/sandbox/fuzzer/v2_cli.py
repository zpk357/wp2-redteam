"""CLI for inspectable exploratory Office V2 Campaigns."""

from __future__ import annotations

import argparse
import json
import os
import traceback
from pathlib import Path

from sandbox.config import SandboxConfig, SandboxLimits, TraceConfig, WeekOneConfig
from sandbox.protocol import (
    AgentRuntimeKind,
    ModelProvider,
    seal_model_inference_options,
)
from sandbox.scenarios.office_v2.execution_request import IN_CONTAINER_OLLAMA_ENDPOINT

from .v2_bootstrap import build_exploratory_bootstrap
from .v2_campaign_store import V2CampaignStore
from .v2_comparison_report import write_v2_comparison_report
from .v2_effectiveness_protocol import (
    ATTACK_EFFECTIVENESS_PROTOCOL_VERSION,
    DEFAULT_CONSECUTIVE_INFRA_PAUSE_THRESHOLD,
    freeze_effectiveness_protocol,
)
from .v2_orchestrator import decide_next_generation
from .v2_preflight import run_preflight
from .v2_real_runtime import run_or_resume_exploratory_campaign
from .v2_report import build_v2_campaign_report, write_v2_campaign_report
from .v2_strategy import CampaignStrategy
from .v2_target_judge import OllamaTargetPreservationJudge


def _exploratory_inference(*, num_ctx: int = 12_288):
    return seal_model_inference_options(
        num_ctx=num_ctx,
        num_predict=1024,
        temperature="0.2",
        top_p="0.9",
        top_k=40,
        thinking=False,
        source_config_digest="sha256:" + "0" * 64,
    )


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
    for name in ("exploratory-run", "exploratory-resume", "exploratory-smoke"):
        command = commands.add_parser(name)
        command.add_argument("--db", type=Path, required=True)
        command.add_argument("--campaign-id", required=True)
        command.add_argument("--agent-image", required=True)
        command.add_argument("--mutator-image", required=True)
        command.add_argument(
            "--agent-runtime",
            choices=[kind.value for kind in AgentRuntimeKind],
            default=AgentRuntimeKind.LANGGRAPH.value,
        )
        command.add_argument("--model-name", default="qwen3.5:27b-q4_K_M")
        command.add_argument("--ollama-mode", choices=("host", "embedded"), default="host")
        command.add_argument("--ollama-endpoint", default="http://127.0.0.1:11434")
        command.add_argument(
            "--episodes",
            "--generations",
            dest="episodes",
            type=int,
            default=10,
            help="number of Episodes to attempt (10 is only the default)",
        )
        command.add_argument("--episode-timeout", type=int, default=600)
        command.add_argument(
            "--agent-context-tokens",
            type=int,
            default=12_288,
            help="Agent model context window; 12288 is the single-GPU exploratory default",
        )
        command.add_argument("--max-tool-calls", type=int, default=24)
        command.add_argument("--max-steps", type=int, default=40)
        command.add_argument("--gpu-device", default="0")
        command.add_argument("--data-root", type=Path, required=True)
        command.add_argument("--progress-dir", type=Path)
        command.add_argument(
            "--strategy",
            choices=[strategy.value for strategy in CampaignStrategy],
            default=CampaignStrategy.COVERAGE_GUIDED.value,
        )
        command.add_argument("--campaign-seed", type=int, default=0)
        command.add_argument("--max-generation-attempts", type=int)
        command.add_argument(
            "--effectiveness-protocol",
            choices=(ATTACK_EFFECTIVENESS_PROTOCOL_VERSION,),
            default=None,
            help=(
                "explicitly enable the attack-effectiveness protocol: stop on K "
                "decidable results (SUCCESS+FAILURE) and supplement exclusions "
                "inside a frozen cumulative budget"
            ),
        )
        command.add_argument(
            "--scheduling-limit",
            type=int,
            default=None,
            help="cumulative scheduling decisions across resumes; defaults to 3*K",
        )
        command.add_argument(
            "--execution-attempt-limit",
            type=int,
            default=None,
            help=(
                "cumulative Episode execution attempts including retries; "
                "defaults to 3*K"
            ),
        )
        command.add_argument(
            "--consecutive-infra-threshold",
            type=int,
            default=DEFAULT_CONSECUTIVE_INFRA_PAUSE_THRESHOLD,
            help="pause after this many consecutive same-class infrastructure faults",
        )
    compare = commands.add_parser("compare")
    compare.add_argument("--db", type=Path, required=True)
    compare.add_argument("--guided-campaign-id", required=True)
    compare.add_argument("--independent-campaign-id", required=True)
    compare.add_argument("--output", type=Path, required=True)
    compare.add_argument("--table-output", type=Path)
    compare.add_argument(
        "--data-root",
        type=Path,
        help=(
            "run data root holding replays/ and artifacts/; "
            "without it the path metric is unscorable"
        ),
    )
    preflight = commands.add_parser("preflight")
    preflight.add_argument("--db", type=Path, required=True)
    preflight.add_argument("--data-root", type=Path, required=True)
    preflight.add_argument("--agent-image", required=True)
    preflight.add_argument("--mutator-image", required=True)
    preflight.add_argument("--model-name", default="qwen3.5:27b-q4_K_M")
    preflight.add_argument("--ollama-mode", choices=("host", "embedded"), default="host")
    preflight.add_argument("--ollama-endpoint", default="http://127.0.0.1:11434")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    with V2CampaignStore(args.db) as store:
        if args.command == "preflight":
            payload = run_preflight(
                agent_image=args.agent_image,
                mutator_image=args.mutator_image,
                model_name=args.model_name,
                ollama_mode=args.ollama_mode,
                ollama_endpoint=args.ollama_endpoint,
                db=args.db,
                data_root=args.data_root,
            )
        elif args.command in {"exploratory-run", "exploratory-resume", "exploratory-smoke"}:
            if args.command == "exploratory-resume" and not store.campaign_exists(args.campaign_id):
                raise SystemExit("exploratory-resume requires an existing Campaign")
            try:
                payload = _run_exploratory(args, store)
            except Exception:
                failure_dir = args.data_root / "failures"
                failure_dir.mkdir(parents=True, exist_ok=True)
                failure_path = failure_dir / "controller-error.txt"
                failure_path.write_text(traceback.format_exc(), encoding="utf-8")
                raise
        elif args.command == "inspect":
            payload = build_v2_campaign_report(store=store, campaign_id=args.campaign_id)
        elif args.command == "report":
            payload = write_v2_campaign_report(
                store=store,
                campaign_id=args.campaign_id,
                output=args.output,
            )
        elif args.command == "compare":
            payload = write_v2_comparison_report(
                store=store,
                guided_campaign_id=args.guided_campaign_id,
                independent_campaign_id=args.independent_campaign_id,
                output=args.output,
                data_root=args.data_root,
                table_output=args.table_output,
            )
        else:
            state = store.load_state(args.campaign_id)
            previous = store.load_latest_generation_decision(args.campaign_id)
            if (
                previous is not None
                and previous.generation_index == state.lifecycle.counters.generation_index
                and previous.input_state_digest == state.state_digest
            ):
                decision = previous
            else:
                strategy = store.campaign_strategy(args.campaign_id)
                decision = decide_next_generation(
                    campaign_id=args.campaign_id,
                    state=state,
                    latest_feedback=store.load_latest_feedback(args.campaign_id),
                    previous_decision=previous,
                    previous_closure=store.load_latest_generation_closure(args.campaign_id),
                    strategy=strategy,
                )
                store.put_generation_decision(decision)
            payload = decision.model_dump(mode="json", exclude_none=False)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.command == "preflight":
        return 0 if payload.get("ready") else 2
    if args.command in {"exploratory-run", "exploratory-resume", "exploratory-smoke"}:
        return (
            0 if payload.get("target_reached") and payload.get("completion_status") is None else 2
        )
    return 0


def _run_exploratory(args, store) -> dict[str, object]:
    try:
        import docker

        from sandbox.client.artifact_transfer import ArtifactTransfer
        from sandbox.client.runtime_client import RuntimeClient
        from sandbox.mutation.v2_docker import DockerOllamaV2MutationProvider
        from sandbox.replay.artifact_store import ArtifactStore
        from sandbox.replay.manifest import ManifestStore
        from sandbox.replay.replay_engine import ReplayEngine
        from sandbox.scheduler.docker_scheduler import DockerSandboxScheduler
        from sandbox.scoring.rule_scorer import RuleBasedScorer

        from .v2_real_episode import DockerOfficeV2EpisodeRunner
    except ImportError as exc:
        raise SystemExit(
            "exploratory-run requires the Docker runtime dependencies from pyproject.toml"
        ) from exc

    if args.episodes < 1:
        raise SystemExit("--episodes must be positive")
    runtime_kind = AgentRuntimeKind(args.agent_runtime)
    if args.agent_context_tokens < 8192:
        raise SystemExit("--agent-context-tokens must be at least 8192")
    inference = _exploratory_inference(num_ctx=args.agent_context_tokens)
    target_episodes = 1 if args.command == "exploratory-smoke" else args.episodes
    protocol = None
    if args.effectiveness_protocol is not None:
        if args.max_generation_attempts is not None:
            raise SystemExit(
                "--max-generation-attempts belongs to the legacy stop rule; "
                "the effectiveness protocol is bounded by --scheduling-limit "
                "and --execution-attempt-limit"
            )
        protocol = freeze_effectiveness_protocol(
            target_decisive_k=target_episodes,
            scheduling_limit=args.scheduling_limit,
            execution_attempt_limit=args.execution_attempt_limit,
            consecutive_infra_pause_threshold=args.consecutive_infra_threshold,
        )
    # The physical storage budget only exists for an effectiveness Campaign so
    # that exclusions never widen it; K stays the decidable-sample target.
    bootstrap = build_exploratory_bootstrap(
        model_name=args.model_name,
        episode_limit=(
            target_episodes if protocol is None else protocol.scheduling_limit
        ),
    )
    client = docker.from_env()
    artifacts = ArtifactStore(args.data_root / "artifacts")
    config = WeekOneConfig(
        max_steps=args.max_steps,
        sandbox=SandboxConfig(
            image=args.agent_image,
            network_mode="host" if args.ollama_mode == "host" else "none",
            gpu_device=args.gpu_device,
            workspace_storage="archive_volume",
            startup_timeout_seconds=600,
            execution_timeout_seconds=args.episode_timeout,
            runtime_environment={
                "TRACE_G_EXPLORATORY_RUN": "1",
                "TRACE_G_AGENT_RUNTIME": runtime_kind.value,
                "TRACE_G_MODEL_NAME": args.model_name,
                "TRACE_G_EXTERNAL_OLLAMA": "1" if args.ollama_mode == "host" else "0",
                "TRACE_G_OLLAMA_ENDPOINT": (
                    args.ollama_endpoint
                    if args.ollama_mode == "host"
                    else IN_CONTAINER_OLLAMA_ENDPOINT
                ),
            },
            limits=SandboxLimits(
                memory_limit="22g",
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
        model_name=args.model_name,
        inference=inference,
        gpu_device=args.gpu_device,
        campaign_id=args.campaign_id,
        client=client,
        network_mode="host" if args.ollama_mode == "host" else "none",
        ollama_endpoint=(
            args.ollama_endpoint if args.ollama_mode == "host" else IN_CONTAINER_OLLAMA_ENDPOINT
        ),
        external_ollama=args.ollama_mode == "host",
    )
    episode_runner = DockerOfficeV2EpisodeRunner(
        replay_engine=engine,
        artifact_store=artifacts,
        model_name=args.model_name,
        model_provider=ModelProvider.OLLAMA,
        model_endpoint=(
            args.ollama_endpoint if args.ollama_mode == "host" else IN_CONTAINER_OLLAMA_ENDPOINT
        ),
        model_inference=inference,
        max_steps=args.max_steps,
        max_tool_calls=args.max_tool_calls,
        timeout_seconds=args.episode_timeout,
        diagnostic_dir=args.data_root / "failures",
    )
    progress_callback = None
    if args.progress_dir is not None:
        args.progress_dir.mkdir(parents=True, exist_ok=True)

        def write_progress(result) -> None:
            destination = args.progress_dir / (f"episode-{result.completed_episode_count:06d}.json")
            temporary = destination.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(result.model_dump(mode="json", exclude_none=False), indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, destination)

        progress_callback = write_progress
    result = run_or_resume_exploratory_campaign(
        store=store,
        campaign_id=args.campaign_id,
        bootstrap=bootstrap,
        generation_count=target_episodes,
        mutation_provider=provider,
        episode_runner=episode_runner,
        target_judge=OllamaTargetPreservationJudge(
            endpoint=args.ollama_endpoint,
            model_name=args.model_name,
            evidence_dir=args.data_root / "target-reviews",
        ) if args.ollama_mode == "host" else None,
        progress_callback=progress_callback,
        strategy=CampaignStrategy(args.strategy),
        campaign_seed_value=args.campaign_seed,
        exploratory=True,
        max_generation_attempts=(
            None
            if protocol is not None
            else (
                args.max_generation_attempts
                if args.max_generation_attempts is not None
                else target_episodes * 3
            )
        ),
        effectiveness_protocol=protocol,
    ).model_dump(mode="json", exclude_none=False)
    args.data_root.mkdir(parents=True, exist_ok=True)
    destination = args.data_root / "last-run.json"
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, destination)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
