"""Run the error-capable scenario through the real Agent entry.

Both adapters go through the same `AgentAdapter` boundary, so a local run and a server run differ
only in who answers.  The fake adapter's runs are contract tests and are labelled as such in the
output; they are never reported as model results (`RA-AC-02`).

Usage:
  probe_error_capable_agent.py --adapter fake --output <path>
  probe_error_capable_agent.py --adapter ollama --endpoint http://127.0.0.1:11434 \
      --model qwen3.5:27b-q4_K_M --output <path>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "src"), str(REPO / "agent_image")]

from sandbox.scenarios.error_capable import (  # noqa: E402
    ATTACK_SPECS,
    TASK_FAMILY_SPECS,
    CoverageFeedback,
    EpisodeKind,
    ErrorCapableMode,
    SelectorDecision,
    SelectorRequest,
    build_plan,
    materialize_scenario,
)
from sandbox.scenarios.error_capable_agent import (  # noqa: E402
    DiscoveryScriptedAgent,
    run_agent_episode,
)
from sandbox.scenarios.error_capable_bridge import bridge_trace, summarise_bridge  # noqa: E402
from sandbox.scenarios.error_capable_identity import ModelIdentity  # noqa: E402
from sandbox.scenarios.error_capable_registry import load_error_capable_fixture  # noqa: E402

FAKE_ROW = "contract-test-not-a-model-result"


def _plan_for(family_index: int, path_index: int, episode_id: str):
    family = TASK_FAMILY_SPECS[family_index]
    attack = ATTACK_SPECS[(path_index + family_index) % len(ATTACK_SPECS)]
    request = SelectorRequest(
        mode=ErrorCapableMode.GUIDED,
        seed=20260930,
        agent_model_name="probe.agent",
        selector_model_name="probe.agent",
        available_task_families=tuple(item.task_family for item in TASK_FAMILY_SPECS),
        available_attacks=tuple(item.attack_mode for item in ATTACK_SPECS),
        feedback=CoverageFeedback(menu_gaps=("discovery",), target_menu_cells=1),
    )
    decision = SelectorDecision(
        task_family=family.task_family,
        task_variant=family.variants[0].variant_id,
        path_id=family.path_ids[path_index],
        attack_mode=attack.attack_mode,
        attack_carrier=attack.carriers[0],
        layout_id="balanced-8",
        episode_kind=EpisodeKind.ATTACK,
        rationale="probe",
    )
    plan = build_plan(
        request,
        decision,
        episode_id=episode_id,
        task_file_id=f"{episode_id}-task",
        file_ids=(
            f"{episode_id}-task",
            f"{episode_id}-public",
            f"{episode_id}-restricted",
            f"{episode_id}-history",
        ),
        attack_carrier=attack.carriers[0],
        model_name="probe.agent",
    )
    return plan, materialize_scenario(plan)


def _ollama_adapter(args: argparse.Namespace):
    from app.agent.ollama_react_provider import OllamaReactProvider

    from sandbox.protocol import ModelOptions, ModelProvider, seal_model_inference_options

    options = ModelOptions(
        provider=ModelProvider.OLLAMA,
        model_name=args.model,
        endpoint=args.endpoint,
        timeout_seconds=args.timeout,
        inference=seal_model_inference_options(
            num_ctx=args.num_ctx,
            num_predict=args.num_predict,
            temperature="0.0",
            top_p="0.9",
            top_k=40,
            thinking=False,
            # The same placeholder the episode runner uses; it names the config the options were
            # derived from, which for a probe is the defaults above.
            source_config_digest="sha256:" + "0" * 64,
        ),
    )
    return OllamaReactProvider(options)


async def _run(args: argparse.Namespace) -> dict[str, object]:
    from sandbox.scenarios.error_capable_registry import ERROR_CAPABLE_FIXTURE_ID

    fixture = load_error_capable_fixture(ERROR_CAPABLE_FIXTURE_ID)

    if args.adapter == "fake":
        adapter = DiscoveryScriptedAgent()
        identity = ModelIdentity.capture(
            provider_id="local", raw_model_label="scripted-agent", provider_version=adapter.version
        )
    else:
        adapter = _ollama_adapter(args)
        identity = ModelIdentity.capture(
            provider_id="ollama",
            raw_model_label=args.model,
            provider_version=getattr(adapter, "version", None),
        )

    schedule = [
        (
            family_index,
            family.task_family.value,
            f"probe.{family.task_family.value}.{args.path_index}",
            (),
        )
        for family_index, family in enumerate(TASK_FAMILY_SPECS)
    ]
    # One extra Episode with a capability the actor does not hold, so the blocked stage is reached
    # by a real policy decision rather than relabelled from a refusal (`RA-08`).
    for capability in args.blocked_capability:
        schedule.append(
            (
                0,
                f"blocked.{capability}",
                f"probe.blocked.{capability.replace('.', '-')}",
                (capability,),
            )
        )

    episodes = []
    for family_index, label, episode_id, drop in schedule:
        plan, material = _plan_for(family_index, args.path_index, episode_id)
        store = None
        if args.journal_root is not None:
            from sandbox.scenarios.error_capable_journal import JournalStore

            store = JournalStore(args.journal_root, plan.episode_id)
            store.claim()
        try:
            trace = await run_agent_episode(
                fixture=fixture,
                plan=plan,
                material=material,
                adapter=adapter,
                model_identity=identity,
                seed=args.seed + family_index,
                max_tool_requests=args.max_tool_requests,
                drop_capabilities=drop,
                journal=store,
                resume=args.resume,
            )
        finally:
            if store is not None:
                store.release()
        bridged = bridge_trace(trace, material=material, plan=plan)
        episodes.append(
            {
                "task_family": plan.task_family.value,
                "label": label,
                "path_id": plan.path_id,
                "attack_mode": plan.attack_mode.value,
                "dropped_capabilities": list(drop),
                "trace": trace.model_dump(mode="json"),
                "bridge": bridged.model_dump(mode="json"),
                "bridge_summary": summarise_bridge(bridged),
            }
        )

    return {
        "probe": "error-capable-real-agent-entry",
        "adapter": args.adapter,
        "adapter_version": adapter.version,
        "model_identity": identity.model_dump(mode="json"),
        "fixture_id": fixture.fixture_id,
        "fixture_freeze_digest": fixture.freeze_digest,
        "tool_menu_digest": trace.first_input.tool_menu_digest,
        "evidence_kind": FAKE_ROW if args.adapter == "fake" else "provider-backed-agent-run",
        "episodes": episodes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", choices=("fake", "ollama"), default="fake")
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="qwen3.5:27b-q4_K_M")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--num-ctx", type=int, default=12288)
    parser.add_argument("--num-predict", type=int, default=1024)
    parser.add_argument("--seed", type=int, default=20260930)
    parser.add_argument("--path-index", type=int, default=0)
    parser.add_argument("--max-tool-requests", type=int, default=24)
    parser.add_argument(
        "--journal-root",
        type=Path,
        default=None,
        help="write a running checkpoint per Episode here, so a hard kill costs at most one call",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="continue each Episode from its checkpoint instead of starting it over",
    )
    parser.add_argument(
        "--blocked-capability",
        action="append",
        default=["mail.send"],
        help="run one extra Episode per named capability the actor is denied",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    payload = asyncio.run(_run(args))
    encoded = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")

    print(f"adapter={payload['adapter']} evidence_kind={payload['evidence_kind']}")
    print(f"model_identity={payload['model_identity']['raw_model_label']}")
    for episode in payload["episodes"]:
        summary = episode["bridge_summary"]
        stages = ",".join(
            f"{key}:{value}" for key, value in sorted(summary["stage_counts"].items())
        )
        print(
            f"  {episode['label']:<34} stop={summary['stop_reason']:<18}"
            f" [{stages:<28}] findings={summary['findings']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
