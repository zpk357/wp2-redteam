"""Acceptance for the container step: the same Opportunity, run on a host and in a container.

The claim is narrow and testable: moving the Episode loop into a container does not change what
the Episode does.  So the probe runs the same plan and the same material twice -- once through
`run_agent_episode` on the host, once through a container -- and compares the two traces field
by field.

Two things are checked, and both are needed:

* **the traces agree, field by field.**  The container is an execution detail, and a trace that
  changed when the loop moved would mean it is not.
* **both runs read a real workspace, and the same one.**  Two runs that both kept the workspace
  in memory would agree with each other and prove nothing, so the tree digests are compared
  separately: the host writes its material to `<run root>/host-workspace/<episode>`, the
  container writes its own into the `/workspace` mount it was given, and the two trees have to
  hash the same.

Nothing here needs a model.  The scripted discovery agent is deterministic, and a fake provider
means the container needs no network at all (`network_mode="none"`), which is also the cheapest
way to see that the transport works before a provider is involved.

Usage::

    python scripts/probe_error_capable_container_roundtrip.py --image error-capable:local
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for _candidate in (REPO, REPO / "src", REPO / "agent_image"):
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from sandbox.client.runtime_client import RuntimeClient  # noqa: E402
from sandbox.config import SandboxConfig, SandboxLimits, TraceConfig  # noqa: E402
from sandbox.protocol import ModelOptions, ModelProvider  # noqa: E402
from sandbox.scenarios.error_capable import (  # noqa: E402
    ATTACK_SPECS,
    TASK_FAMILY_SPECS,
    ErrorCapableMode,
    SelectorDecision,
    SelectorRequest,
    TaskFamily,
    build_plan,
    materialize_scenario,
)
from sandbox.scenarios.error_capable_agent import (  # noqa: E402
    DiscoveryScriptedAgent,
    run_agent_episode,
)
from sandbox.scenarios.error_capable_identity import ModelIdentity  # noqa: E402
from sandbox.scenarios.error_capable_registry import (  # noqa: E402
    ERROR_CAPABLE_FIXTURE_ID,
    load_error_capable_fixture,
)
from sandbox.scenarios.error_capable_runner import (  # noqa: E402
    ErrorCapableContainerRunner,
)
from sandbox.scenarios.error_capable_submission import (  # noqa: E402
    build_error_capable_request,
)
from sandbox.scenarios.error_capable_world import carrier_ids, planned_file_ids  # noqa: E402
from sandbox.scheduler.docker_scheduler import DockerSandboxScheduler  # noqa: E402

EPISODE_ID = "probe.roundtrip.000"
SEED = 4242
MODEL_NAME = "scripted-discovery"

#: The mount every container already has, and the only thing the container side needs told.
CONTAINER_WORKSPACE_ROOT = "/workspace"

FAMILIES = tuple(spec.task_family for spec in TASK_FAMILY_SPECS)
ATTACKS = tuple(spec.attack_mode for spec in ATTACK_SPECS)
PATHS = tuple(path for spec in TASK_FAMILY_SPECS for path in spec.path_ids)
LAYOUT = "balanced-9"


def _plan_and_material():
    request = SelectorRequest(
        mode=ErrorCapableMode.RANDOM,
        seed=SEED,
        agent_model_name=MODEL_NAME,
        available_task_families=FAMILIES,
        available_paths=PATHS,
        available_attacks=ATTACKS,
    )
    decision = SelectorDecision(
        task_family=TaskFamily.SUMMARY_DELIVERY,
        path_id="summary.public-index-email",
        attack_mode=ATTACKS[0],
        attack_carrier=carrier_ids()[0],
        layout_id=LAYOUT,
        rationale="roundtrip probe holds the cell still so only the process can vary",
    )
    file_ids = planned_file_ids(EPISODE_ID, seed=SEED, layout_id=LAYOUT)
    plan = build_plan(
        request,
        decision,
        episode_id=EPISODE_ID,
        file_ids=file_ids,
        attack_carrier=decision.attack_carrier,
        model_name=MODEL_NAME,
        layout_id=LAYOUT,
        task_file_id=file_ids[0],
    )
    return plan, materialize_scenario(plan)


async def _in_process(plan, material, identity, workspace_root):
    """The same Episode the container runs, on the host, against a real directory.

    The directory is not a convenience: it is what makes the comparison mean anything.  Both
    sides now read their material through `WorkspaceFileSystem`, so what is being compared is
    the container boundary rather than the disk -- and if the host side kept the workspace in
    memory the two traces would differ in exactly the field that records which one happened.
    """

    adapter = DiscoveryScriptedAgent()
    return await run_agent_episode(
        fixture=load_error_capable_fixture(ERROR_CAPABLE_FIXTURE_ID),
        plan=plan,
        material=material,
        adapter=adapter,
        model_identity=identity,
        seed=plan.seed,
        max_tool_requests=24,
        workspace_root=workspace_root,
    )


async def _in_container(plan, material, identity, args, run_root):
    fixture = load_error_capable_fixture(ERROR_CAPABLE_FIXTURE_ID)
    config = SandboxConfig(
        image=args.image,
        network_mode="none",
        startup_timeout_seconds=args.startup_timeout_seconds,
        limits=SandboxLimits(),
    )
    # The workspace root is the mount the scheduler already gives every container: a writable
    # tmpfs owned by uid 10001.  The container writes this Opportunity's material into it
    # itself, from the plan, so nothing about the world crosses the boundary.
    #
    # `run_root` stays None, so this probe runs without a container journal.  A journal is what
    # a hard kill is recovered from, and recovering one needs a bind mount that outlives the
    # container plus a second container to read it.  That is a separate test with its own
    # subject; here the only things compared are the trace and the workspace tree.
    runner = ErrorCapableContainerRunner(
        scheduler=DockerSandboxScheduler(config),
        runtime=RuntimeClient(TraceConfig(output_dir=run_root / "traces")),
        image=args.image,
        model=ModelOptions(provider=ModelProvider.FAKE, model_name=MODEL_NAME),
        model_identity=identity,
        adapter_version=getattr(DiscoveryScriptedAgent(), "version", None) or "unknown",
        timeout_seconds=args.execution_timeout_seconds,
        container_workspace_root=CONTAINER_WORKSPACE_ROOT,
    )
    return await runner.run_opportunity(
        build_error_capable_request(
            execution_id=EPISODE_ID,
            index=0,
            mode=ErrorCapableMode.RANDOM.value,
            plan=plan,
            material=material,
            fixture=fixture,
            model=ModelOptions(provider=ModelProvider.FAKE, model_name=MODEL_NAME),
            model_identity=identity,
            adapter_version=getattr(DiscoveryScriptedAgent(), "version", None) or "unknown",
            timeout_seconds=args.execution_timeout_seconds,
            max_tool_requests=24,
            journal_root=None,
            workspace_root=CONTAINER_WORKSPACE_ROOT,
        )
    )


def _differences(left: dict, right: dict, path: str = "") -> list[str]:
    found: list[str] = []
    for key in sorted(set(left) | set(right)):
        here = f"{path}.{key}" if path else key
        if key not in left:
            found.append(f"{here}: only in container")
        elif key not in right:
            found.append(f"{here}: only in host")
        elif isinstance(left[key], dict) and isinstance(right[key], dict):
            found.extend(_differences(left[key], right[key], here))
        elif left[key] != right[key]:
            found.append(f"{here}: host={left[key]!r} container={right[key]!r}")
    return found


def _workspace_notes(host_trace, container_trace) -> list[str]:
    """What the two runs say about the workspace they actually read.

    A trace comparison on its own would pass if both sides had quietly kept the workspace in
    memory: they would agree with each other, and agree about nothing.  The tree digests are
    therefore checked separately -- present on both sides, and the same value, which is the
    only way to state that two different machines read the same real files.
    """

    host = host_trace.workspace_tree_digest
    container = container_trace.workspace_tree_digest
    if host is None or container is None:
        side = "host" if host is None else "container"
        return [f"WORKSPACE FAIL: {side} ran with no workspace on a disk"]
    if host != container:
        return [
            "WORKSPACE FAIL: the two runs read different trees",
            f"  host      {host}",
            f"  container {container}",
        ]
    return [f"WORKSPACE ok: both runs read the same tree, {host}"]


async def _main(args: argparse.Namespace) -> int:
    plan, material = _plan_and_material()
    identity = ModelIdentity.capture(
        provider_id="local", raw_model_label=MODEL_NAME, provider_version="scripted"
    )

    args.run_root.mkdir(parents=True, exist_ok=True)
    host_root = args.run_root / "host-workspace" / EPISODE_ID
    host_trace = await _in_process(plan, material, identity, host_root)
    print("HOST      stop_reason=%s steps=%d digest=%s" % (
        host_trace.stop_reason, len(host_trace.steps), host_trace.trace_digest[:26]
    ))
    print("          workspace=%s files=%d" % (
        (host_trace.workspace_tree_digest or "none")[:26],
        sum(1 for _ in host_root.rglob("*") if _.is_file()),
    ))

    container_root = args.run_root / "episodes"
    container_root.mkdir(parents=True, exist_ok=True)
    container_trace = await _in_container(plan, material, identity, args, container_root)
    print("CONTAINER stop_reason=%s steps=%d digest=%s" % (
        container_trace.stop_reason, len(container_trace.steps), container_trace.trace_digest[:26]
    ))
    print("          workspace=%s" % (container_trace.workspace_tree_digest or "none")[:26])

    differences = _differences(
        host_trace.model_dump(mode="json"), container_trace.model_dump(mode="json")
    )
    workspace_notes = _workspace_notes(host_trace, container_trace)
    workspace_failed = any(note.startswith("WORKSPACE FAIL") for note in workspace_notes)
    print()
    for note in workspace_notes:
        print(note)
    if not differences:
        print("RESULT identical: the container changed nothing about this Episode")
    else:
        print(f"RESULT {len(differences)} difference(s):")
        for item in differences[:40]:
            print(f"  {item}")
    (args.run_root / "roundtrip.json").write_text(
        json.dumps(
            {
                "identical": not differences,
                "workspace_failed": workspace_failed,
                "workspace_notes": workspace_notes,
                "differences": differences,
                "host_workspace_root": str(host_root),
                "host": host_trace.model_dump(mode="json"),
                "container": container_trace.model_dump(mode="json"),
            },
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0 if not differences and not workspace_failed else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--run-root", type=Path, default=Path("/tmp/error-capable-roundtrip"))
    parser.add_argument("--startup-timeout-seconds", type=float, default=60.0)
    parser.add_argument("--execution-timeout-seconds", type=int, default=300)
    return asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
