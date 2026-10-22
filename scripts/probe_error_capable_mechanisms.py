"""Pin one scenario condition and vary only the attack mechanism.

`MW-AC-06` asks for a *controlled* comparison: at least two mechanisms proven, not asserted, to
change different decision dimensions.  The campaign's own selectors cannot produce that, because
both of them choose the whole combination -- including the mechanism.  This probe replaces the
chooser with one that returns the same pinned condition for every opportunity and changes only
`attack_mode`, so any difference between the runs is attributable to the mechanism and not to the
family, variant, path, layout, carrier, seed, budget or menu.

What is held fixed, and recorded per Episode so it can be checked rather than trusted:

- task family, task variant, path template, layout and carrier slot;
- the base seed, so each mechanism sees the same per-Episode seed (`seed + index`);
- the tool budget and the frozen tool menu;
- the model identity and adapter;
- the Agent's first input, which must be byte-identical across mechanisms.

Run: python scripts/probe_error_capable_mechanisms.py --adapter fake
     python scripts/probe_error_capable_mechanisms.py --adapter ollama
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
for _candidate in (_HERE.parent, _HERE.parents[1] / "src", _HERE.parents[1] / "agent_image"):
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from probe_error_capable_agent import _ollama_adapter  # noqa: E402

from sandbox.scenarios.error_capable import ErrorCapableMode, attack_spec  # noqa: E402
from sandbox.scenarios.error_capable_agent import DiscoveryScriptedAgent  # noqa: E402
from sandbox.scenarios.error_capable_campaign import PinnedSelector, run_campaign  # noqa: E402
from sandbox.scenarios.error_capable_identity import ModelIdentity  # noqa: E402
from sandbox.scenarios.error_capable_registry import (  # noqa: E402
    ERROR_CAPABLE_FIXTURE_ID,
    load_error_capable_fixture,
)

FAKE_ROW = "contract-test-not-a-model-result"
PROVIDER_ROW = "provider-backed-agent-run"

#: The three mechanisms and the decision dimension each one is registered as targeting.  The
#: registry's own claim is what the run is testing; it is not evidence for itself.
MECHANISMS = (
    ("note_rewrite", "discovery"),
    ("authority_spoof", "authorization"),
    ("public_link_exfiltration", "delivery"),
)


async def _run(args: argparse.Namespace) -> dict[str, object]:
    fixture = load_error_capable_fixture(ERROR_CAPABLE_FIXTURE_ID)

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

    arms: dict[str, object] = {}
    for attack_mode, declared_stage in MECHANISMS:
        spec = attack_spec(attack_mode)
        if args.carrier not in spec.carriers:
            raise SystemExit(
                f"{args.carrier!r} is not a carrier of {attack_mode!r}; registered:"
                f" {list(spec.carriers)}"
            )
        selector = PinnedSelector(
            {
                "task_family": args.task_family,
                "task_variant": args.task_variant,
                "path_id": args.path_id,
                "layout_id": args.layout_id,
                "attack_carrier": args.carrier,
                "attack_mode": attack_mode,
            },
            vary="attack_mode",
        )
        report = await run_campaign(
            fixture=fixture,
            mode=ErrorCapableMode.GUIDED,
            episodes=args.repeats,
            adapter=adapter,
            selector=selector,
            model_identity=identity,
            seed=args.seed,
            max_tool_requests=args.max_tool_requests,
            # One subdirectory per mechanism.  The Episode id is derived from the mode and the
            # index, so without this the second mechanism overwrites the first one's traces,
            # bridges and selection receipts, and the raw evidence for all but the last mechanism is
            # destroyed by the comparison that produced it.
            journal_root=None if args.journal_root is None
            else Path(args.journal_root) / attack_mode,
        )
        arms[attack_mode] = {
            "declared_stage": declared_stage,
            "registry_stage": spec.decision_stage,
            "registry_mechanism": spec.mechanism,
            "pinned": dict(selector.fields),
            "rejected_opportunities": len(report.rejected_opportunities),
            "episodes": [
                {
                    "index": record.index,
                    "episode_id": record.episode_id,
                    "decision": record.selector.decision.model_dump(mode="json"),
                    "first_input_digest": record.first_input_digest,
                    "tool_menu_digest": record.tool_menu_digest,
                    "stop_reason": record.stop_reason,
                    "unresolved": list(record.unresolved),
                    "observed": None if record.observed is None
                    else record.observed.model_dump(mode="json"),
                }
                for record in report.episodes
            ],
        }
        print(f"  {attack_mode:<28} episodes={len(report.episodes)}"
              f" rejected={len(report.rejected_opportunities)}")

    return {
        "probe": "error-capable-mechanism-comparison",
        "adapter": args.adapter,
        "adapter_version": adapter.version,
        "evidence_kind": evidence_kind,
        "model_identity": identity.model_dump(mode="json"),
        "fixture_id": fixture.fixture_id,
        "fixture_freeze_digest": fixture.freeze_digest,
        "repeats_per_mechanism": args.repeats,
        "seed": args.seed,
        "max_tool_requests": args.max_tool_requests,
        "fixed": {
            "task_family": args.task_family,
            "task_variant": args.task_variant,
            "path_id": args.path_id,
            "layout_id": args.layout_id,
            "attack_carrier": args.carrier,
        },
        "arms": arms,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", choices=("fake", "ollama"), default="fake")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260930)
    parser.add_argument("--max-tool-requests", type=int, default=24)
    parser.add_argument("--task-family", default="summary_delivery")
    parser.add_argument("--task-variant", default="summary.partner-brief")
    parser.add_argument("--path-id", default="summary.public-index-email")
    parser.add_argument("--layout-id", default="balanced-8")
    parser.add_argument("--carrier", default="file-note")
    parser.add_argument("--journal-root", type=Path, default=None)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="qwen3.5:27b-q4_K_M")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--num-ctx", type=int, default=12288)
    parser.add_argument("--num-predict", type=int, default=1024)
    args = parser.parse_args()

    payload = asyncio.run(_run(args))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    print(f"adapter={payload['adapter']} evidence_kind={payload['evidence_kind']}")
    print(f"fixed={payload['fixed']} repeats={payload['repeats_per_mechanism']}"
          f" seed={payload['seed']} budget={payload['max_tool_requests']}")
    for attack_mode, arm in payload["arms"].items():
        stages = [
            (episode["observed"] or {}).get("behaviour_detail", {}).get("strongest_stage")
            for episode in arm["episodes"]
        ]
        risks = [(episode["observed"] or {}).get("risk_detail", {}).get("class")
                 for episode in arm["episodes"]]
        print(f"  {attack_mode:<28} stages={stages} risks={risks}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
