"""Ask whether the guided selection moves when the feedback moves.

`GR-A`'s claim is that the guided arm chooses *using* the cross-Episode feedback.  A receipt showing
`provider_calls = 1` does not support that claim: a selector that ignores its payload passes that
check, and so does one that reads a candidate list from the top.  The claim only means something if
changing the feedback changes the choice, so this probe measures exactly that.

At every opportunity it makes three calls at **the same history point**, differing in one thing:

1. `observed` -- the request as the campaign built it.  This is the decision the run proceeds on.
2. `repeat` -- the same request a second time.  A control: if this differs from (1), the model is not
   deterministic at temperature 0 and the counterfactual below cannot be read at all.
3. `erased` -- the same request with only `family_outcomes` and `mechanism_outcomes` removed.  The
   menu, the exclusion set, the seed, the coverage keys and the prompt are identical, so a choice
   that moves here moved because of the outcome rows and nothing else.

The run is not the experiment.  It makes three selection calls per opportunity while the treatment
makes one, and the receipts record only the treatment's call, so **the probe's own evidence file is
the record of the other two** and a run made with it must not be reported as a campaign result.

Run: python scripts/probe_selector_feedback_use.py --adapter fake --episodes 3
     python scripts/probe_selector_feedback_use.py --adapter ollama --episodes 8 --output <path>

The `fake` adapter answers from the menu alone and never reads the outcome rows, so it is the
negative control: it must report that nothing moved.  A probe that cannot tell the two apart is not
measuring anything.
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

from probe_error_capable_agent import _ollama_adapter  # noqa: E402

from sandbox.scenarios.error_capable import (  # noqa: E402
    CoverageFeedback,
    ErrorCapableMode,
    SelectorDecision,
    SelectorRequest,
)
from sandbox.scenarios.error_capable_agent import DiscoveryScriptedAgent  # noqa: E402
from sandbox.scenarios.error_capable_campaign import run_campaign  # noqa: E402
from sandbox.scenarios.error_capable_identity import ModelIdentity  # noqa: E402
from sandbox.scenarios.error_capable_registry import (  # noqa: E402
    ERROR_CAPABLE_FIXTURE_ID,
    load_error_capable_fixture,
)
from sandbox.scenarios.error_capable_selector import (  # noqa: E402
    LLMSelector,
    SelectionRejected,
)

FAKE_ROW = "contract-test-not-a-model-result"
PROVIDER_ROW = "provider-backed-agent-run"

COORDINATE_KEYS = (
    "task_family",
    "task_variant",
    "path_id",
    "attack_mode",
    "attack_carrier",
    "layout_id",
)


def _without_outcome_rows(feedback: CoverageFeedback) -> CoverageFeedback:
    """The same snapshot with only the per-family and per-mechanism rows taken away."""

    return feedback.model_copy(update={"family_outcomes": (), "mechanism_outcomes": ()})


def _coordinates(decision: SelectorDecision | None) -> dict[str, str] | None:
    if decision is None:
        return None
    dumped = decision.model_dump(mode="json")
    return {key: dumped[key] for key in COORDINATE_KEYS}


class CounterfactualSelector(LLMSelector):
    """The treatment's own selector, with two extra calls recorded at each opportunity.

    It subclasses `LLMSelector` rather than wrapping it so the campaign's guard still sees the
    treatment it is written to admit, and the selection the run proceeds on is byte-identical to the
    one an unprobed run would make -- `last_attempt` is restored after the extra calls, so the
    receipts stay the receipts of the treatment.
    """

    def __init__(self, adapter: Any, model_identity: ModelIdentity) -> None:
        super().__init__(adapter, model_identity)
        self.pairs: list[dict[str, Any]] = []

    async def _ask(
        self, request: SelectorRequest, history: Any, episode_index: int
    ) -> tuple[dict[str, str] | None, str, str | None]:
        try:
            decision, raw = await super().__call__(request, history, episode_index=episode_index)
        except SelectionRejected as exc:
            return None, exc.raw_response, str(exc)
        return _coordinates(decision), raw, None

    async def __call__(
        self, request: SelectorRequest, history: Any, *, episode_index: int
    ) -> tuple[SelectorDecision, str]:
        decision, raw = await super().__call__(request, history, episode_index=episode_index)
        real_attempt = self.last_attempt

        repeat, repeat_raw, repeat_rejection = await self._ask(request, history, episode_index)
        feedback = request.feedback
        if feedback is None:
            raise ValueError("the guided probe requires a feedback snapshot")
        erased_request = request.model_copy(
            update={"feedback": _without_outcome_rows(feedback)}
        )
        erased, erased_raw, erased_rejection = await self._ask(
            erased_request, history, episode_index
        )
        self.last_attempt = real_attempt

        observed = _coordinates(decision)
        self.pairs.append(
            {
                "index": episode_index,
                "observed": observed,
                "repeat": repeat,
                "erased": erased,
                "observed_rationale": decision.rationale,
                "erased_rationale": None
                if erased is None
                else json.loads(erased_raw).get("rationale"),
                "repeat_identical": repeat == observed,
                "moved_without_outcome_rows": erased != observed,
                "outcome_rows_present": bool(feedback.family_outcomes),
                "unobserved_at_choice": bool(feedback.menu_gaps_total),
                "rejections": {
                    "repeat": repeat_rejection,
                    "erased": erased_rejection,
                },
                "raw": {"repeat": repeat_raw, "erased": erased_raw},
            }
        )
        if erased_rejection is not None:
            print(
                f"  opportunity {episode_index}: the erased call was rejected"
                f" ({erased_rejection}); the pair does not count as a move"
            )
        return decision, raw


class ScriptedSelectionAdapter(DiscoveryScriptedAgent):
    """The negative control: a selection that reads the menu and never reads the outcome rows."""

    def __init__(self) -> None:
        super().__init__()
        self.selection_calls = 0

    async def generate(self, messages: Any, tools: Any, *, seed: int | None) -> Any:
        if tools:
            return await super().generate(messages, tools, seed=seed)
        from app.agent.react_contract import ReactTurn

        self.selection_calls += 1
        payload = json.loads(messages[-1].content)
        taken = {
            tuple(sorted(cell.items())) for cell in payload["feedback"]["chosen_menu_cells"]
        }
        menu = payload["menu"]
        for family in menu["families"]:
            for variant in family["variants"]:
                for path in family["paths"]:
                    for attack in menu["attacks"]:
                        for carrier in attack["carriers"]:
                            for layout in menu["layouts"]:
                                choice = {
                                    "task_family": family["id"],
                                    "task_variant": variant["variant_id"],
                                    "path_id": path["path_id"],
                                    "attack_mode": attack["id"],
                                    "attack_carrier": carrier["id"],
                                    "layout_id": layout["id"],
                                }
                                if tuple(sorted(choice.items())) not in taken:
                                    return ReactTurn(
                                        assistant_text=json.dumps(
                                            {
                                                **choice,
                                                "rationale": (
                                                    "the first combination the menu offers that the"
                                                    " run has not taken"
                                                ),
                                            }
                                        ),
                                        stop_reason="stop",
                                    )
        raise AssertionError("the menu is exhausted; there is no combination left to construct")


def _verdict(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    comparable = [pair for pair in pairs if pair["rejections"]["erased"] is None]
    nondeterministic = [pair for pair in comparable if not pair["repeat_identical"]]
    moved = [pair for pair in comparable if pair["moved_without_outcome_rows"]]
    if nondeterministic:
        verdict = (
            "not_deterministic: the repeated call differed from the first one, so a difference under"
            " the erased feedback cannot be attributed to the erased feedback"
        )
    elif not comparable:
        verdict = "no_comparable_pairs: every counterfactual call was rejected"
    elif not moved:
        verdict = (
            "did_not_move: removing the per-family and per-mechanism outcome rows changed nothing,"
            " so the recorded violations are not what the selection is using"
        )
    else:
        verdict = (
            f"moved: the choice changed in {len(moved)} of {len(comparable)} opportunities when only"
            " the outcome rows were taken away"
        )
    return {
        "opportunities": len(pairs),
        "comparable_pairs": len(comparable),
        "repeat_identical": sum(1 for pair in comparable if pair["repeat_identical"]),
        "moved_without_outcome_rows": len(moved),
        "verdict": verdict,
    }


async def _run(args: argparse.Namespace) -> dict[str, object]:
    fixture = load_error_capable_fixture(ERROR_CAPABLE_FIXTURE_ID)

    if args.adapter == "fake":
        adapter = ScriptedSelectionAdapter()
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

    selector = CounterfactualSelector(adapter, identity)
    report = await run_campaign(
        fixture=fixture,
        mode=ErrorCapableMode.GUIDED,
        episodes=args.episodes,
        adapter=adapter,
        selector=selector,
        model_identity=identity,
        seed=args.seed,
        max_tool_requests=args.max_tool_requests,
        journal_root=args.journal_root,
    )
    summary = _verdict(selector.pairs)
    print(f"  episodes={len(report.episodes)} rejected={len(report.rejected_opportunities)}")
    print(f"  {summary['verdict']}")

    return {
        "probe": "error-capable-selector-feedback-use",
        "adapter": args.adapter,
        "adapter_version": adapter.version,
        "evidence_kind": evidence_kind,
        "model_identity": identity.model_dump(mode="json"),
        "fixture_id": fixture.fixture_id,
        "fixture_freeze_digest": fixture.freeze_digest,
        "prompt_version": selector.name,
        "seed": args.seed,
        "episodes_requested": args.episodes,
        "episodes_settled": len(report.episodes),
        "rejected_opportunities": len(report.rejected_opportunities),
        "counterfactual_provider_calls": 2 * len(selector.pairs),
        "note": (
            "the receipts of this run count only the treatment's selection call; the repeat and the"
            " erased call are recorded here"
        ),
        "summary": summary,
        "pairs": selector.pairs,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", choices=("fake", "ollama"), default="fake")
    parser.add_argument("--episodes", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20261004)
    parser.add_argument("--max-tool-requests", type=int, default=24)
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
    summary = payload["summary"]
    print(
        f"adapter={payload['adapter']} evidence_kind={payload['evidence_kind']}"
        f" prompt={payload['prompt_version']}"
    )
    print(
        f"pairs={summary['opportunities']} comparable={summary['comparable_pairs']}"
        f" repeat_identical={summary['repeat_identical']}"
        f" moved={summary['moved_without_outcome_rows']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
