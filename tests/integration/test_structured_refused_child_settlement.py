"""A refused child settles like a wording failure: same cost, no invented Episode, recoverable.

`SS-011`/`SS-014` count both failures as opportunities and forbid fabricating an Episode for
either.  These tests drive the real `run_opportunity` loop with a provider whose wording repeats
for the same node, so a repeat edit materialises exactly its parent and the shared admission gate
refuses it.  The books must stay straight for every arm, and the checkpoint must stay readable.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

from sandbox.scenarios.structured_v1.fixtures.summary_delivery_a import build_manifest
from sandbox.structured_v1.campaign import (
    CampaignCheckpoint,
    CampaignLimits,
    CampaignUsage,
    load_checkpoint,
    run_opportunity,
)
from sandbox.structured_v1.experiment_inputs import prepare_inputs
from sandbox.structured_v1.generation import GenerationBudget
from sandbox.structured_v1.search import ArmKind, TwoArmSearch
from sandbox.structured_v1.text_provider import HttpJsonTextProvider, TextProviderOptions

OPPORTUNITIES = 8


class _FrozenWordingTransport:
    """The identical wording for the same node every time, so a repeated edit is a no-op."""

    def __init__(self) -> None:
        self.payloads = []

    def __call__(self, *, url, payload, timeout_seconds):
        self.payloads.append(payload)
        instruction = json.loads(payload["messages"][1]["content"])
        items = [
            {"node_id": node, "text": f"Frozen material {node}."}
            for node in instruction["node_ids"]
        ]
        return {
            "message": {"role": "assistant", "content": json.dumps({"items": items})},
            "prompt_eval_count": 21,
            "eval_count": 9,
        }


def _limits() -> CampaignLimits:
    return CampaignLimits(
        opportunities=OPPORTUNITIES,
        model_calls=100,
        tool_calls=100,
        input_tokens=100000,
        output_tokens=100000,
        wall_clock_seconds=10000,
        expense_units=10000,
        mutator_calls=24,
        mutator_input_tokens=100000,
        mutator_output_tokens=100000,
    )


def _options() -> TextProviderOptions:
    return TextProviderOptions(
        provider_id="ollama-chat",
        model_name="offline-transport-only",
        endpoint="http://offline.invalid",
        timeout_seconds=1,
    )


def test_a_refused_child_costs_one_opportunity_and_invents_no_episode(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "unit"))
    chain = importlib.import_module("test_structured_feedback_chain")
    manifest = build_manifest()
    inputs = prepare_inputs(manifest, count=4)
    parents = {item.candidate_id: item.case for item in inputs.candidates}
    planned = CampaignUsage(
        model_calls=3,
        tool_calls=2,
        input_tokens=1000,
        output_tokens=1000,
        mutator_calls=2,
        mutator_input_tokens=2000,
        mutator_output_tokens=2000,
    )

    for arm in ArmKind:
        transport = _FrozenWordingTransport()
        search = TwoArmSearch(
            parents,
            manifest,
            seed=f"refused-child:{arm.value}",
            provider=HttpJsonTextProvider(_options(), transport=transport),
            generation_budget=GenerationBudget(
                requests_per_opportunity=2,
                max_input_tokens_per_request=1000,
                max_output_tokens_per_request=1000,
            ),
        )
        checkpoint = CampaignCheckpoint(limits=_limits())
        path = tmp_path / f"{arm.value}.json"
        episodes: list[str] = []
        failures = 0

        for _ in range(OPPORTUNITIES):
            checkpoint, bundle = run_opportunity(
                path,
                checkpoint,
                search,
                arm=arm,
                manifest=manifest,
                execute=lambda case: chain._execute(manifest, case),
                planned=planned,
            )

            assert checkpoint.stopped is False, checkpoint.stop_reason
            if bundle is None:
                failures += 1
            else:
                episodes.append(bundle.episode_id)

        # Every attempt is accounted for exactly once: an Episode or a consumed opportunity.
        assert checkpoint.usage.opportunities == len(episodes) + failures == OPPORTUNITIES
        assert failures + len(episodes) == OPPORTUNITIES
        assert len(episodes) == len(set(episodes)), "no Episode id is reused"
        assert load_checkpoint(path) == checkpoint, "the failure path stays recoverable"

        if arm is ArmKind.RANDOM_EVOLUTION:
            # The pooling arm edits parents it never executed, so repeating wording must reach the
            # refusal; the other arms ride the same settlement whenever it reaches them too.
            assert failures, "the pooling arm must meet a refused child with repeating wording"
