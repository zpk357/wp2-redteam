"""Run both arms of the coverage-feedback campaign and compare them field by field.

This is the smoke that `RA-CLOSE-02` asks for: guided receives a coverage snapshot derived from the
previous Episode's execution evidence, random samples uniformly without a selection model or
history, and shared Agent conditions and menus are checked explicitly.

Run: python scripts/probe_error_capable_campaign.py --adapter fake
     python scripts/probe_error_capable_campaign.py --adapter ollama --episodes 3
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

from sandbox.scenarios.error_capable import (  # noqa: E402
    ATTACK_SPECS,
    TASK_FAMILY_SPECS,
    ErrorCapableMode,
)
from sandbox.scenarios.error_capable_agent import DiscoveryScriptedAgent  # noqa: E402
from sandbox.scenarios.error_capable_campaign import (  # noqa: E402
    ScriptedSelector,
    compare_arms,
    oracle_contract_version,
    run_campaign,
    tool_catalogue_size,
)
from sandbox.scenarios.error_capable_identity import ModelIdentity  # noqa: E402
from sandbox.scenarios.error_capable_registry import (  # noqa: E402
    ERROR_CAPABLE_FIXTURE_ID,
    load_error_capable_fixture,
)
from sandbox.scenarios.error_capable_selector import LLMSelector  # noqa: E402

FAKE_ROW = "contract-test-not-a-model-result"


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
        evidence_kind = "provider-backed-agent-run"

    selector = (
        ScriptedSelector(
            path_ids={spec.task_family.value: spec.path_ids for spec in TASK_FAMILY_SPECS},
            attacks=tuple(spec.attack_mode.value for spec in ATTACK_SPECS),
        )
        if args.adapter == "fake"
        else LLMSelector(adapter, identity)
    )

    reports = {}
    for mode in (ErrorCapableMode.GUIDED, ErrorCapableMode.RANDOM):
        reports[mode.value] = await run_campaign(
            fixture=fixture,
            mode=mode,
            episodes=args.episodes,
            adapter=adapter,
            selector=selector,
            model_identity=identity,
            seed=args.seed,
            max_tool_requests=args.max_tool_requests,
            journal_root=args.journal_root,
        )

    guided = reports[ErrorCapableMode.GUIDED.value]
    random = reports[ErrorCapableMode.RANDOM.value]
    alignment = compare_arms(guided, random)

    return {
        "probe": "error-capable-coverage-feedback",
        "adapter": args.adapter,
        "adapter_version": adapter.version,
        "evidence_kind": evidence_kind,
        "model_identity": identity.model_dump(mode="json"),
        "fixture_id": fixture.fixture_id,
        "fixture_freeze_digest": fixture.freeze_digest,
        "episodes_per_arm": args.episodes,
        "oracle_contract_version": oracle_contract_version(),
        "tool_catalogue_size": tool_catalogue_size(),
        "alignment": alignment.model_dump(mode="json"),
        "aligned": alignment.aligned,
        "arms": {
            mode: {
                "targets": report.targets.model_dump(mode="json"),
                "episodes": [
                    {
                        "index": record.index,
                        "episode_id": record.episode_id,
                        "family": record.selector.decision.task_family.value,
                        "attack": record.selector.decision.attack_mode.value,
                        "rationale": record.selector.decision.rationale,
                        "first_input_digest": record.first_input_digest,
                        "blind_request_digest": record.selector.blind_request_digest,
                        "feedback_digest": record.selector.feedback_digest,
                        "history_reads": list(record.selector.history_reads),
                        "stop_reason": record.stop_reason,
                        "settled_coverage": record.settled_coverage,
                        "observed": None
                        if record.observed is None
                        else record.observed.model_dump(mode="json"),
                    }
                    for record in report.episodes
                ],
                "ledger": {
                    "settled": list(report.ledger.settled),
                    "observed_count": len(report.ledger.observed),
                },
                "sentinel_reads": list(report.sentinel_reads),
                "selection_attempts": [
                    a.model_dump(mode="json") for a in report.selection_attempts
                ],
                "selection_provider_calls": sum(
                    a.provider_calls for a in report.selection_attempts
                ),
                "selection_elapsed_ms": sum(
                    a.elapsed_ms for a in report.selection_attempts
                ),
                "selection_tokens": report.selection_cost()["tokens"],
                "selection_tokens_status": report.selection_cost()["tokens_status"],
                "selection_cost": report.selection_cost(),
                "feedback_used": [list(item) for item in report.feedback_used()],
                "final_coverage": report.ledger.feedback(report.targets, limit=1_000).model_dump(
                    mode="json"
                ),
            }
            for mode, report in reports.items()
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", choices=("fake", "ollama"), default="fake")
    parser.add_argument("--episodes", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260930)
    parser.add_argument("--max-tool-requests", type=int, default=24)
    parser.add_argument("--journal-root", type=Path, default=None)
    parser.add_argument("--output", type=Path)
    # Provider options, shared with the Agent probe.
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

    alignment = payload["alignment"]
    print(f"adapter={payload['adapter']} evidence_kind={payload['evidence_kind']}")
    print(f"episodes_per_arm={payload['episodes_per_arm']} aligned={payload['aligned']}")
    for key in (
        "shared_model_identity",
        "shared_tool_menu",
        "shared_budget",
        "shared_targets",
        "agent_inputs_identical",
        "blind_requests_identical",
        "guided_received_feedback",
        "random_received_feedback",
        "random_read_history",
    ):
        print(f"  {key:<28} {alignment[key]}")
    for mode in ("coverage_guided", "random_independent"):
        arm = payload["arms"][mode]
        print(f"\n[{mode}] settled={len(arm['ledger']['settled'])} reads={arm['sentinel_reads']}")
        for episode in arm["episodes"]:
            observed = episode["observed"] or {}
            print(
                f"  {episode['index']} {episode['family']:<22} {episode['attack']:<24}"
                f" stage={observed.get('stage')} risk={observed.get('risk_class')}"
                f" settled={episode['settled_coverage']}"
            )
            print(f"      {episode['rationale']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
