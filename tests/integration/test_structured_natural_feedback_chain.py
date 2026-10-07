"""SOC-TASK §25: natural local search, scripted transport/Agent, real Office tools.

This checks engineering flow, not attack quality or superiority. The Office world is the
existing rehearsal world; there is no container, network, model, or research execution.
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
from sandbox.structured_v1.search import OBLIGATION_DIRECTIONS, ArmKind, TwoArmSearch
from sandbox.structured_v1.text_provider import HttpJsonTextProvider, TextProviderOptions


class _MaterialTransport:
    """The identical bounded response rule for every arm, through the actual HTTP adapter.

    Each request returns fresh wording, the way a real provider does. A provider that echoed the
    same text back for the same node would make a second edit of that node a no-op, and the shared
    edit kernel refuses a child that materialises exactly its parent's units.
    """

    def __init__(self):
        self.payloads = []
        self.revision = 0

    def __call__(self, *, url, payload, timeout_seconds):
        self.payloads.append(payload)
        self.revision += 1
        instruction = json.loads(payload["messages"][1]["content"])
        items = [{"node_id": node, "text": f"Public material note {node} r{self.revision}."}
                 for node in instruction["node_ids"]]
        return {
            "message": {"role": "assistant", "content": json.dumps({"items": items})},
            "prompt_eval_count": 21,
            "eval_count": 9,
        }


def test_empty_archive_naturally_executes_a_parent_and_settles_a_child(tmp_path, monkeypatch):
    # Reuse the established real-Office rehearsal without changing suite import configuration.
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "unit"))
    chain = importlib.import_module("test_structured_feedback_chain")
    manifest = build_manifest()
    inputs = prepare_inputs(manifest, count=4)
    parents = {item.candidate_id: item.case for item in inputs.candidates}
    opportunities = 12
    limits = CampaignLimits(
        opportunities=opportunities, model_calls=100, tool_calls=100,
        input_tokens=100000, output_tokens=100000, wall_clock_seconds=10000,
        expense_units=10000, mutator_calls=24, mutator_input_tokens=100000,
        mutator_output_tokens=100000,
    )
    generation_budget = GenerationBudget(
        requests_per_opportunity=2, max_input_tokens_per_request=1000,
        max_output_tokens_per_request=1000,
    )
    planned = CampaignUsage(
        model_calls=3, tool_calls=2, input_tokens=1000, output_tokens=1000,
        mutator_calls=2, mutator_input_tokens=2000, mutator_output_tokens=2000,
    )
    options = TextProviderOptions(
        provider_id="ollama-chat", model_name="offline-transport-only",
        endpoint="http://offline.invalid", timeout_seconds=1,
    )
    results = {}
    transports = {}
    for arm in ArmKind:
        transport = _MaterialTransport()
        transports[arm] = transport
        search = TwoArmSearch(
            parents, manifest, seed="natural-material-chain",
            provider=HttpJsonTextProvider(options, transport=transport),
            generation_budget=generation_budget,
        )
        assert not search.state.parent_coverage and not search.state.unit_index
        checkpoint = CampaignCheckpoint(limits=limits)
        path = tmp_path / f"{arm.value}.json"
        actual_bundles = {}
        for opportunity in range(opportunities):
            before = dict(search.state.parent_coverage)

            def execute(case, executed=actual_bundles):
                bundle = chain._execute(manifest, case)
                executed[case.mutation_lineage.generation_identity] = bundle
                return bundle

            checkpoint, bundle = run_opportunity(
                path, checkpoint, search, arm=arm, manifest=manifest,
                execute=execute, planned=planned,
                execution_config=lambda _: chain.TEST_CONFIG,
            )
            assert bundle is not None and not checkpoint.stopped
            receipt = checkpoint.selections[-1]
            assert receipt.opportunity == opportunity
            assert receipt.selected_direction == OBLIGATION_DIRECTIONS[opportunity % 2]
            child_id = next(
                candidate for candidate, actual in actual_bundles.items()
                if actual is bundle
            )
            own = search.state.parent_coverage[child_id]
            assert own.execution.candidate_id == child_id
            assert own.execution.bundle_digest == bundle.bundle_digest
            assert own.episode_id == bundle.episode_id
            if receipt.parent_baseline is not None:
                # `search.py` records one of three reasons, and a receipt with a baseline can carry
                # any of them: the ranking applies only when the planned dimension already has an
                # observed unit.  On the first Opportunity the archive is empty, so the documented
                # fallback is what is recorded, and asserting one string for every baseline asserted
                # that the fallback branch does not exist.
                if not before:
                    assert receipt.reason == "empty-planned-dimension-fallback"
                else:
                    assert receipt.reason in {
                        "feedback-ranked-unit",
                        "empty-planned-dimension-fallback",
                        "directed-near-violation",
                    }
                assert receipt.parent_id in before
                assert receipt.parent_baseline == before[receipt.parent_id]
                assert receipt.parent_baseline.execution.bundle_digest == (
                    actual_bundles[receipt.parent_id].bundle_digest
                )
                assert child_id != receipt.parent_id
                assert search.parents[child_id].mutation_lineage.parent_candidate_id == (
                    receipt.parent_id
                )
                assert child_id in search.state.retention
            elif receipt.arm is ArmKind.RANDOM_EVOLUTION and not receipt.root_restart:
                # The evolution arm edits a pooled parent it never executed itself, so it holds
                # no coverage baseline and earns no retention record (`SOC-FBK-09`).
                assert search.parents[child_id].mutation_lineage.parent_candidate_id == (
                    receipt.parent_id
                )
                assert child_id not in search.state.retention
            else:
                # A root restart, or an independent draw: the case has no parent at all.
                assert search.parents[child_id].mutation_lineage.parent_candidate_id is None
            assert checkpoint.usage.opportunities == opportunity + 1
            assert checkpoint.usage.model_calls == sum(
                actual.usage.model_calls for actual in actual_bundles.values()
            )
            assert checkpoint.usage.mutator_calls == len(transport.payloads)
            assert checkpoint.usage.mutator_input_tokens == 21 * len(transport.payloads)
            assert checkpoint.usage.mutator_output_tokens == 9 * len(transport.payloads)
            assert load_checkpoint(path) == checkpoint
        assert transport.payloads, "must exercise the real provider request/response path"
        results[arm] = checkpoint

    guided = results[ArmKind.COVERAGE_GUIDED]
    random = results[ArmKind.RANDOM_INDEPENDENT]
    assert guided.selections[0].root_restart
    assert any(item.parent_baseline is not None for item in guided.selections), (
        "natural schedule must reach actual feedback-selected local execution"
    )
    assert any(
        item.parent_baseline is not None
        and search.parents[item.parent_id].mutation_lineage.parent_candidate_id is not None
        for item in guided.selections
    ), "a real executed child must itself become a parent through feedback"
    assert all(item.parent_baseline is None and item.reason == "independent-root-draw"
               for item in random.selections)
    evolution = results[ArmKind.RANDOM_EVOLUTION]
    assert evolution.selections[0].root_restart
    assert all(item.feedback_sources == () for item in evolution.selections), (
        "the evolution arm never reads cross-episode feedback when it picks a parent"
    )
    assert any(
        not item.root_restart and item.parent_baseline is None
        for item in evolution.selections
    ), "the evolution arm must reach a local edit without holding any coverage baseline"
    assert [item.selected_direction for item in evolution.selections] == [
        item.selected_direction for item in guided.selections
    ], "the three arms ride the same direction schedule (`SS-010`)"
    assert [item.selected_direction for item in guided.selections] == [
        item.selected_direction for item in random.selections
    ]
    assert guided.limits == random.limits
    systems = {
        payload["messages"][0]["content"]
        for transport in transports.values() for payload in transport.payloads
    }
    assert len(systems) == 1, "both arms must receive the same material-generation capability"
    assert all(
        plan.obligation_direction == OBLIGATION_DIRECTIONS[plan.opportunity % 2]
        for checkpoint in results.values() for plan in checkpoint.generations
    )
