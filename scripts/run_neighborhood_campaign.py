"""One paired repeat of the neighborhood-priority experiment: both arms, one base seed.

`TASK-NEIGHBORHOOD-PRIORITY-20261006` §3 fixes the arms, the seeds, the order and the layout of the
run root. This entry point takes all four explicitly and refuses to guess any of them, because a run
whose seed or arm order was chosen by a default is a run nobody can re-check.

It runs **one repeat**, not the whole experiment: the specification requires the pairs to be run in
order and looked at between pairs without the settings changing, and a wrapper that can run all of
them is also a wrapper that can be restarted with a different seed by accident.

The default adapter is `fake`.  A real run needs `--adapter ollama` *and* a `--model` and an
`--endpoint` that answer, so nothing starts a paid or model-backed run by being invoked without
arguments.

Usage:
  python scripts/run_neighborhood_campaign.py --stage pilot --repeat 1 --root <dir>
  python scripts/run_neighborhood_campaign.py --stage main --repeat 1 --adapter ollama --root <dir>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import suppress
from pathlib import Path

_HERE = Path(__file__).resolve()
for _candidate in (_HERE.parent, _HERE.parents[1] / "src", _HERE.parents[1] / "agent_image"):
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from probe_error_capable_agent import _ollama_adapter, ollama_model_options  # noqa: E402

from sandbox.scenarios.error_capable import (  # noqa: E402
    ATTACK_SPECS,
    TASK_FAMILY_SPECS,
    ErrorCapableMode,
)
from sandbox.scenarios.error_capable_agent import DiscoveryScriptedAgent  # noqa: E402
from sandbox.scenarios.error_capable_campaign import (  # noqa: E402
    CampaignReport,
    ScriptedSelector,
    compare_arms,
    run_campaign,
)
from sandbox.scenarios.error_capable_executor import InProcessExecutor  # noqa: E402
from sandbox.scenarios.error_capable_identity import ModelIdentity  # noqa: E402
from sandbox.scenarios.error_capable_registry import (  # noqa: E402
    ERROR_CAPABLE_FIXTURE_ID,
    load_error_capable_fixture,
)
from sandbox.scenarios.error_capable_selector import LLMSelector  # noqa: E402

#: `SPEC-NEIGHBORHOOD-PRIORITY-20261006` §8 and TASK §3.  The pilot and the formal run use disjoint
#: ranges so a pilot artifact can never be mistaken for a formal one by its seed.
PILOT_SEEDS = (610610000, 610611000)
MAIN_SEEDS = (610620000, 610621000, 610622000, 610623000, 610624000, 610625000)
PILOT_EPISODES = 8
MAIN_EPISODES = 24

FAKE_ROW = "contract-test-not-a-model-result"
PROVIDER_ROW = "provider-backed-agent-run"


def arm_order(repeat: int) -> tuple[str, str]:
    """Odd repeats run random first, even repeats guided first (`NP-14`).

    The order alternates so that a service that gets slower, or a cache that fills, does not line up
    with one arm across all six pairs.
    """

    return ("random", "guided") if repeat % 2 == 1 else ("guided", "random")


def base_seed(stage: str, repeat: int) -> int:
    table = PILOT_SEEDS if stage == "pilot" else MAIN_SEEDS
    if not 1 <= repeat <= len(table):
        raise SystemExit(f"--repeat must be 1..{len(table)} for stage {stage}")
    return table[repeat - 1]


#: The mount every sandbox container already has: a writable tmpfs owned by the container's own
#: uid.  Naming it is the whole of what putting the workspace on a disk requires -- no extra
#: mount, and no privilege the sandbox does not already grant.
CONTAINER_WORKSPACE_ROOT = "/workspace"


def _build_executor(args: argparse.Namespace, *, adapter: object, identity: ModelIdentity) -> object:
    """Where the Episodes run, as a decision the invocation has to state.

    Defaulted to the in-process, in-memory path so that nothing changes by being invoked without
    arguments.  A container run has to name an image, because the image is now part of the
    campaign identity -- a defaulted image would be a run whose environment nobody chose.
    """

    if args.executor == "in-process":
        return InProcessExecutor(
            adapter=adapter,
            model_identity=identity,
            write_workspace=args.workspace == "directory",
        )

    from sandbox.client.runtime_client import RuntimeClient
    from sandbox.config import SandboxConfig, SandboxLimits, TraceConfig
    from sandbox.protocol import ModelOptions, ModelProvider
    from sandbox.scheduler.docker_scheduler import DockerSandboxScheduler
    from sandbox.scenarios.error_capable_runner import ErrorCapableContainerRunner

    model = (
        ModelOptions(provider=ModelProvider.FAKE, model_name=args.model)
        if args.adapter == "fake"
        else ollama_model_options(args)
    )
    # How the container reaches the model server.  Two shapes, and they are not interchangeable.
    #
    # `--model-network-name` is the stricter one.  It makes `SandboxConfig` require an internal
    # bridge network carrying the `ollama-only` policy label and an endpoint of
    # `http://ollama:11434`, which is the form that class was written to enforce: the container
    # is on a network with no egress except the model server, and the model server is attached to
    # the same network.  It also needs the *host* to resolve `ollama`, because the selector runs
    # here and the Agent runs there and both have to name the same model.
    #
    # Without it, the container shares the host's network namespace, which is how the
    # structured-scenario line ran its real-model episodes: the same `http://127.0.0.1:11434`
    # works from both sides and nothing has to resolve anything.  The container can then reach
    # whatever the host can -- a real weakening of the sandbox, and one this experiment accepts
    # because the Agent has no tool that could use it: the frozen menu is office tools, with no
    # shell and no HTTP client.  What the network is there for is the Agent's own model calls.
    model_network = args.model_network_name
    config = SandboxConfig(
        image=args.image,
        # `none` for the fake provider: a contract run has no reason to reach a network, and a
        # sandbox that does not open one is a sandbox with less to explain.
        network_mode="none" if args.adapter == "fake" else args.network_mode,
        startup_timeout_seconds=args.startup_timeout_seconds,
        execution_timeout_seconds=args.timeout,
        limits=SandboxLimits(
            memory_limit=args.memory_limit,
            nano_cpus=int(float(args.cpus) * 1_000_000_000),
            pids_limit=args.pids_limit,
            tmpfs_size=args.tmpfs_size,
        ),
        gpu_device=args.gpu_device,
        ollama_endpoint=MODEL_NETWORK_ENDPOINT if model_network else None,
        model_network_name=model_network,
    )
    return ErrorCapableContainerRunner(
        scheduler=DockerSandboxScheduler(config),
        runtime=RuntimeClient(TraceConfig(output_dir=Path(args.root) / "container-traces")),
        image=args.image,
        model=model,
        model_identity=identity,
        adapter_version=getattr(adapter, "version", None) or "unknown",
        timeout_seconds=args.timeout,
        max_continuations=args.max_continuations,
        limits=config.limits,
        container_workspace_root=CONTAINER_WORKSPACE_ROOT,
        run_root=_container_run_root(args),
    )


def _container_run_root(args: argparse.Namespace) -> tuple[Path, str] | None:
    """The host directory to bind into each container, and where it appears inside.

    `--container-journal none` runs without one.  A journal is what a hard kill is recovered
    from, and a container's own tmpfs cannot serve: the kill takes the mount with it.  A bind
    mount of a directory that outlives the container is the whole mechanism, which is why the
    two are configured together rather than apart.

    The directory has to be writable by uid 10001, which is what the container runs as and is
    not this process.  The mode is widened here rather than assumed, and the container will
    still fail loudly on the first checkpoint if the filesystem does not honour it -- a journal
    that silently never gets written is the failure this whole arrangement exists to avoid.
    """

    if args.container_journal == "none":
        return None
    host_root = Path(args.root) / "container-run-root"
    host_root.mkdir(parents=True, exist_ok=True)
    with suppress(OSError):
        host_root.chmod(0o777)
    return host_root, CONTAINER_RUN_ROOT


#: Where the bound run root appears inside the container.
CONTAINER_RUN_ROOT = "/run-root"

#: The only endpoint `SandboxConfig` accepts when a model network is named.  Spelled once because
#: it is a value that class validates against, not a default anybody may vary.
MODEL_NETWORK_ENDPOINT = "http://ollama:11434"


async def _run(args: argparse.Namespace) -> dict[str, object]:
    fixture = load_error_capable_fixture(ERROR_CAPABLE_FIXTURE_ID)
    seed = base_seed(args.stage, args.repeat)
    episodes = args.episodes or (PILOT_EPISODES if args.stage == "pilot" else MAIN_EPISODES)

    if args.adapter == "fake":
        adapter = DiscoveryScriptedAgent()
        identity = ModelIdentity.capture(
            provider_id="local", raw_model_label="scripted-agent", provider_version=adapter.version
        )
        evidence_kind = FAKE_ROW
        # The contract double, not a model: it exercises the guided path end to end so the loop's
        # guarantees can be checked without a Provider, and every artifact it produces says so.
        selector = ScriptedSelector(
            path_ids={spec.task_family.value: spec.path_ids for spec in TASK_FAMILY_SPECS},
            attacks=[spec.attack_mode.value for spec in ATTACK_SPECS],
        )
    else:
        adapter = _ollama_adapter(args)
        identity = ModelIdentity.capture(
            provider_id="ollama",
            raw_model_label=args.model,
            provider_version=getattr(adapter, "version", None),
        )
        evidence_kind = PROVIDER_ROW
        # The specification requires the selector to be the same model and the same full identity as
        # the Agent under test; the campaign checks it again and refuses a mismatch.
        selector = LLMSelector(adapter, identity)

    stage_root = Path(args.root) / args.stage / f"rep-{args.repeat:02d}"
    stage_root.mkdir(parents=True, exist_ok=True)
    order = arm_order(args.repeat)
    executor = _build_executor(args, adapter=adapter, identity=identity)

    #: Which arm this invocation runs.  `both` is the specification's unit of a paired repeat and
    #: is the default, so nothing changes by being invoked without the flag.
    #:
    #: Splitting the pair across two invocations is for a run that has to give the machine back in
    #: between -- not a way to save work.  The Campaign has no path that skips an Episode already
    #: settled, so a second invocation of an arm re-executes it from the first Episode; what
    #: splitting buys is that the arm not yet run can be run on its own, into this same root,
    #: instead of repeating the arm that has already finished.
    wanted = order if args.arm == "both" else (args.arm,)
    unplaced = [arm for arm in wanted if arm not in order]
    if unplaced:
        raise SystemExit(f"--arm {args.arm} is not in this repetition's order: {list(order)}")

    reports: dict[str, object] = {}
    for position, arm in enumerate(order):
        if arm not in wanted:
            # Read the arm this invocation is not running, so the pair summary can still be
            # drawn from the two reports rather than from the two in memory.
            stored_path = stage_root / f"{arm}-campaign.json"
            if stored_path.is_file():
                reports[arm] = CampaignReport.model_validate_json(
                    stored_path.read_text(encoding="utf-8")
                )
                print(f"  {arm:<7} carried forward from {stored_path.name}")
            continue
        mode = ErrorCapableMode.GUIDED if arm == "guided" else ErrorCapableMode.RANDOM
        arm_root = stage_root / arm
        arm_root.mkdir(parents=True, exist_ok=True)
        (stage_root / "running.json").write_text(
            json.dumps(
                {
                    "stage": args.stage,
                    "repeat": args.repeat,
                    "base_seed": seed,
                    "order": list(order),
                    "position": position,
                    "arm": arm,
                    "episodes": episodes,
                    "adapter": args.adapter,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        report = await run_campaign(
            fixture=fixture,
            mode=mode,
            episodes=episodes,
            adapter=adapter,
            selector=selector,
            model_identity=identity,
            seed=seed,
            max_tool_requests=args.max_tool_requests,
            max_continuations=args.max_continuations,
            journal_root=arm_root,
            executor=executor,
        )
        reports[arm] = report
        # **The two readouts this run exists to produce were not reaching the artifact.**  The report
        # is serialised by field, and both `target_reach` -- whether each mechanism produced the
        # violation type it is written to provoke -- and `risk_dimension_curve` -- where each of the
        # four types finished -- are methods, so `model_dump` left them out.  Neither had a consumer
        # anywhere in the run path: `selection_cost` is read by this script and by the probe, and
        # these two were only ever called from a test.  A run would therefore have finished, written
        # its artifacts, and shown nobody the numbers the whole change was for.
        #
        # Written beside the report rather than turned into fields on it.  Both are derived from
        # `episodes`, and a stored copy is a second place for the same quantity to live -- which is
        # the shape of the defect already fixed twice in this area.
        (stage_root / f"{arm}-campaign.json").write_text(
            json.dumps(
                {
                    **report.model_dump(mode="json"),
                    "readouts": {
                        "target_reach": report.target_reach(),
                        "risk_dimension_curve": report.risk_dimension_curve(),
                    },
                },
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(
            f"  {arm:<7} episodes={len(report.episodes)} opportunities={report.opportunities}"
            f" rejected={len(report.rejected_opportunities)}"
        )
        # Printed as well as stored: a run is watched as it goes, and a fact that is only in a file
        # is one nobody reads until the run is over.
        reached = report.target_reach()
        for name, row in reached.items():
            print(
                f"      {name:<26}-> {row['target_type']:<26}"
                f"reached={row['reached']} other={row['reached_other']}"
                f" nothing={row['reached_nothing']}"
            )
        if not reached:
            print("      target reach: no Episode's mechanism resolved to a target type")
        levels = {
            name: points[-1]["level"] if points else None
            for name, points in report.risk_dimension_curve().items()
        }
        print(f"      risk levels {levels}")

    if set(reports) != {"guided", "random"}:
        # The pair summary is a statement about two arms, so it is not written until there are
        # two.  Reporting one arm's numbers as if they were the pair's would be the same mistake
        # the arms are compared to avoid -- a rate over a denominator that is not what it claims.
        (stage_root / "running.json").unlink(missing_ok=True)
        print(f"  pair summary not written: this root holds {sorted(reports)}")
        return {"stage": args.stage, "repeat": args.repeat, "arms": sorted(reports)}

    alignment = compare_arms(reports["guided"], reports["random"])
    payload = {
        "experiment": "neighborhood-priority-paired-repeat",
        "stage": args.stage,
        "repeat": args.repeat,
        "base_seed": seed,
        "episodes_per_arm": episodes,
        "arm_order": list(order),
        "adapter": args.adapter,
        "adapter_version": adapter.version,
        "evidence_kind": evidence_kind,
        # Where these Episodes ran, in the pair summary rather than only inside each report:
        # a reader comparing two pairs has to be able to see that both were run the same way.
        "execution_environment": executor.environment.identity_payload(),
        "model_identity": identity.model_dump(mode="json"),
        "fixture_id": fixture.fixture_id,
        "fixture_freeze_digest": fixture.freeze_digest,
        "max_tool_requests": args.max_tool_requests,
        "aligned": alignment.model_dump(mode="json"),
        "arms": {
            arm: {
                "opportunities": report.opportunities,
                "episodes": len(report.episodes),
                "rejected": len(report.rejected_opportunities),
                "sentinel_reads": list(report.sentinel_reads),
                "selection_provider_calls": report.selection_cost()["provider_calls"],
                "priority_digest": None if report.priority is None else report.priority.digest(),
            }
            for arm, report in reports.items()
        },
    }
    (stage_root / "pair.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (stage_root / "running.json").unlink(missing_ok=True)
    print(f"  aligned={alignment.aligned}  written {stage_root / 'pair.json'}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("pilot", "main"), required=True)
    parser.add_argument("--repeat", type=int, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--adapter", choices=("fake", "ollama"), default="fake")
    parser.add_argument("--episodes", type=int, default=0)
    parser.add_argument("--max-tool-requests", type=int, default=24)
    #: How many silent turns the Agent gets before the loop stops believing it has finished.  A
    #: flag rather than an inherited default: the first preflight showed every Episode using all
    #: three, which is a number the run should state rather than absorb.
    parser.add_argument("--max-continuations", type=int, default=3)
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="qwen3.5:27b-q4_K_M")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--num-ctx", type=int, default=12288)
    #: Raised from 1024 after the first preflight.  The provider reported `length` on the turns
    #: where this model wrote its closing summary, so 1024 was cutting turns off mid-sentence --
    #: and a turn cut off is a turn whose ending, tool call included, was never seen.
    parser.add_argument("--num-predict", type=int, default=2048)
    #: `both` is the specification's unit of a paired repeat.  `guided` or `random` runs one arm of
    #: the pair on its own, so a run that has to hand the machine back after the first arm can
    #: finish the second later without repeating the first.
    parser.add_argument("--arm", choices=("both", "guided", "random"), default="both")
    parser.add_argument("--executor", choices=("in-process", "container"), default="in-process")
    #: `memory` is what every result so far was produced by; `directory` writes the workspace out
    #: and reads it back, which is how a host run is put on the same terms as a container one.
    parser.add_argument("--workspace", choices=("memory", "directory"), default="memory")
    parser.add_argument("--image", default="error-capable:local")
    #: `none` by default: a container that reaches nothing is the shape a contract run wants and
    #: the shape that needs no explanation.  A model-backed run needs `host` (so the container
    #: sees the host's `127.0.0.1:11434`) or a `--model-network-name`.
    parser.add_argument("--network-mode", default="none")
    #: Name of an internal bridge network labelled `trace-g.network-policy=ollama-only` with the
    #: model server attached.  When given, the endpoint is forced to `http://ollama:11434` on both
    #: sides and the container has no egress except the model server.
    parser.add_argument("--model-network-name", default=None)
    parser.add_argument("--gpu-device", default=None)
    parser.add_argument("--startup-timeout-seconds", type=float, default=60.0)
    parser.add_argument("--memory-limit", default="512m")
    parser.add_argument("--cpus", default="1.0")
    parser.add_argument("--pids-limit", type=int, default=128)
    parser.add_argument("--tmpfs-size", default="64m")
    #: `mount` binds a run root into every container so an Episode's journal survives the
    #: container; `none` runs without one, which is cheaper and is what a contract run wants.
    parser.add_argument("--container-journal", choices=("mount", "none"), default="mount")
    args = parser.parse_args()

    payload = asyncio.run(_run(args))
    print(
        f"stage={payload['stage']} repeat={payload['repeat']} seed={payload['base_seed']}"
        f" order={payload['arm_order']} kind={payload['evidence_kind']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
