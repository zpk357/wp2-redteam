"""One arm of the TASK-05 coverage experiment, run against the real campaign.

The four arms differ only in the scheduler rule (``no_coverage`` / ``behaviour_only``
/ ``risk_only`` / ``dual``); the campaign, the fixture and the guidance setting are
identical across them, because an arm that also changed the harness would measure
the harness.

One invocation = one arm of one repeat.  Splitting arms across invocations is how
the run gives the machine back between arms; it is not a way to skip work, and an
arm always starts from its first Episode.

Usage:
  python scripts/run_evolution_campaign.py --stage pilot --repeat 1 --arm dual \
      --episodes 8 --adapter ollama --root <dir> --model qwen3.5:27b-q4_K_M
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve()
for _candidate in (_HERE.parent, _HERE.parents[1] / "src", _HERE.parents[1] / "agent_image"):
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from probe_error_capable_agent import _ollama_adapter, ollama_model_options  # noqa: E402

from sandbox.scenarios.error_capable import ErrorCapableMode  # noqa: E402
from sandbox.scenarios.error_capable_agent import DiscoveryScriptedAgent  # noqa: E402
from sandbox.scenarios.error_capable_campaign import run_campaign  # noqa: E402
from sandbox.scenarios.error_capable_executor import InProcessExecutor  # noqa: E402
from sandbox.scenarios.error_capable_identity import ModelIdentity  # noqa: E402
from sandbox.scenarios.error_capable_registry import (  # noqa: E402
    ERROR_CAPABLE_FIXTURE_ID,
    load_error_capable_fixture,
)
from sandbox.toolchain.evolution import CoverageSchedulerSelector  # noqa: E402
from sandbox.toolchain.scheduler import Arm  # noqa: E402

#: Disjoint from the neighborhood experiment's ranges so no artifact can be
#: mistaken for one of another experiment by its seed.
PILOT_SEEDS = (610700000, 610701000)
MAIN_SEEDS = (610710000, 610711000, 610712000)

PILOT_EPISODES = 8
MAIN_EPISODES = 24

FAKE_ROW = "contract-test-not-a-model-result"
PROVIDER_ROW = "provider-backed-agent-run"


def base_seed(stage: str, repeat: int) -> int:
    table = PILOT_SEEDS if stage == "pilot" else MAIN_SEEDS
    if not 1 <= repeat <= len(table):
        raise SystemExit(f"--repeat must be 1..{len(table)} for stage {stage}")
    return table[repeat - 1]


async def _run(args: argparse.Namespace) -> int:
    fixture = load_error_capable_fixture(ERROR_CAPABLE_FIXTURE_ID)
    seed = base_seed(args.stage, args.repeat)
    episodes = args.episodes or (PILOT_EPISODES if args.stage == "pilot" else MAIN_EPISODES)

    if args.adapter == "fake":
        adapter = DiscoveryScriptedAgent()
        identity = ModelIdentity.capture(
            provider_id="local", raw_model_label="scripted-agent", provider_version=adapter.version
        )
        evidence_kind = FAKE_ROW
    else:
        adapter = _ollama_adapter(args)
        identity = ModelIdentity.capture(
            provider_id="ollama",
            raw_model_label=args.model,
            provider_version=getattr(adapter, "version", None),
        )
        evidence_kind = PROVIDER_ROW

    # Baseline selectors share the harness with the scheduler arms; only the ordering
    # rule differs.  `llm_select` is the model's own menu choice (the pre-TASK-05
    # guided selector); `pure_random` is the harness's uniform sampler, which by
    # contract only runs in random mode.
    mode = (
        ErrorCapableMode.RANDOM
        if args.mode == "random" or args.arm == "pure_random"
        else ErrorCapableMode.GUIDED
    )
    if args.arm == "llm_select":
        from sandbox.scenarios.error_capable_selector import LLMSelector

        selector = LLMSelector(adapter=adapter, model_identity=identity)
    elif args.arm == "pure_random":
        from sandbox.scenarios.error_capable_selector import PureRandomSelector

        selector = PureRandomSelector()
        mode = ErrorCapableMode.RANDOM
    else:
        selector = CoverageSchedulerSelector(Arm(args.arm), seed=seed)
    arm_root = Path(args.root) / args.stage / f"rep-{args.repeat:02d}" / args.arm
    arm_root.mkdir(parents=True, exist_ok=True)

    def _progress(record: Any) -> None:
        # The verdict lives inside `observed`, a pydantic model rather than a dict.
        observed = getattr(record, "observed", None)
        print(
            f"  ep {record.episode_id} "
            f"risk={getattr(observed, 'risk_class', None)} "
            f"attack={getattr(observed, 'attack', None)} "
            f"reached={getattr(observed, 'inducement_reached', None)}",
            flush=True,
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
        guidance="full",
        neutral_materials=args.neutral_materials,
        journal_root=arm_root,
        executor=InProcessExecutor(
            adapter=adapter, model_identity=identity, write_workspace=args.workspace == "directory"
        ),
        on_episode=_progress,
    )

    # COST-03: the Agent-side usage each settled episode's trace reported, totalled per arm.
    # Selector-side spend stays in selection_cost; adapters that report nothing leave rows
    # out, and the episodes_with_usage count says so instead of implying a zero.
    usage_rows = [
        record.agent_token_usage for record in report.episodes if record.agent_token_usage
    ]
    agent_cost = {
        "episodes_with_usage": len(usage_rows),
        "provider_calls": sum(row.provider_calls for row in usage_rows),
        "input_tokens": sum(row.input_tokens for row in usage_rows),
        "output_tokens": sum(row.output_tokens for row in usage_rows),
    }

    out = {
        "arm": args.arm,
        "evidence_kind": evidence_kind,
        "seed": seed,
        "stage": args.stage,
        "repeat": args.repeat,
        "episodes": len(report.episodes),
        "opportunities": report.opportunities,
        "scheduler": selector.history_summary(),
        # Selector-side token usage: the Agent's own usage is not on the trace, so
        # the report's cost ledger is the only figure the run can actually show.
        "selection_cost": report.selection_cost(),
        "agent_cost": agent_cost,
        "readouts": {
            "target_reach": report.target_reach(),
            "risk_dimension_curve": report.risk_dimension_curve(),
        },
        "report": report.model_dump(mode="json"),
    }
    path = arm_root / "evolution-campaign.json"
    path.write_text(
        json.dumps(out, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    summary = selector.history_summary()
    agent_total = agent_cost["input_tokens"] + agent_cost["output_tokens"]
    print(
        f"  arm={args.arm} episodes={len(report.episodes)} "
        f"violations={summary['violations']} tokens={summary['tokens']} "
        f"agent_tokens={agent_total} "
        f"(calls={agent_cost['provider_calls']}, episodes={agent_cost['episodes_with_usage']}) "
        f"repeat={summary['repeat_exploration']}"
    )
    print(f"  written {path}")
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("pilot", "main"), required=True)
    parser.add_argument("--repeat", type=int, required=True)
    parser.add_argument(
        "--arm",
        choices=[item.value for item in Arm] + ["llm_select", "pure_random"],
        required=True,
    )
    parser.add_argument("--mode", choices=("guided", "random"), default="guided")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--adapter", choices=("fake", "ollama"), default="fake")
    parser.add_argument("--episodes", type=int, default=0)
    parser.add_argument("--max-tool-requests", type=int, default=24)
    parser.add_argument("--max-continuations", type=int, default=3)
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="qwen3.5:27b-q4_K_M")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--num-ctx", type=int, default=12288)
    parser.add_argument("--num-predict", type=int, default=2048)
    parser.add_argument("--workspace", choices=("memory", "directory"), default="memory")
    parser.add_argument(
        "--neutral-materials",
        action="store_true",
        help="EV-03 matched control: replace the carrier's injection payload with the"
        " harness's benign note; resources, layout, facts and task stay identical.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run(_parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
