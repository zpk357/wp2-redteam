"""ANCHOR-07: actual Office delivery enters the existing local campaign feedback chain.

Only model decisions, text generation and time are scripted. This checks execution identity,
selection and settlement, not real-model branching or a coverage advantage.
"""

from __future__ import annotations

import json

from sandbox.scenarios.structured_v1.fixtures import load_fixture
from sandbox.scenarios.structured_v1.fixtures.summary_delivery_c import (
    FIXTURE_ID,
    PUBLIC_ANCHOR_BODY,
    PUBLIC_ANCHOR_FILE,
)
from sandbox.structured_v1.budget import RunBudgetEnvelope
from sandbox.structured_v1.campaign import (
    CampaignCheckpoint,
    CampaignLimits,
    CampaignUsage,
    load_checkpoint,
    run_opportunity,
)
from sandbox.structured_v1.coverage import parent_child_retention
from sandbox.structured_v1.drivers import ScriptedModelPort, drive_structured_v1_episode
from sandbox.structured_v1.experiment_inputs import prepare_inputs
from sandbox.structured_v1.freeze import build_freeze_manifest, load_assets
from sandbox.structured_v1.generation import GenerationBudget
from sandbox.structured_v1.model import ModelDecision
from sandbox.structured_v1.models import sha256_digest
from sandbox.structured_v1.public_delivery import judge_public_delivery
from sandbox.structured_v1.search import ArmKind, TwoArmSearch
from sandbox.structured_v1.submission import build_envelope
from sandbox.structured_v1.text_provider import HttpJsonTextProvider, TextProviderOptions
from sandbox.structured_v1.tool_catalogue import ImageManifest, build_office_v2_catalogue


class _FreshTextTransport:
    """Supply fresh node text through the real provider parser without making requests."""

    def __init__(self):
        self.requests = 0

    def __call__(self, *, url, payload, timeout_seconds):
        self.requests += 1
        instruction = json.loads(payload["messages"][1]["content"])
        items = [
            {"node_id": node, "text": f"Material note {node}, revision {self.requests}."}
            for node in instruction["node_ids"]
        ]
        return {
            "message": {"role": "assistant", "content": json.dumps({"items": items})},
            "prompt_eval_count": 21,
            "eval_count": 9,
        }


class _QuietClock:
    source = "test-substitute"

    def __init__(self):
        self.waited = False

    def now(self):
        return 200 if self.waited else 100

    def wait_until(self, target):
        self.waited = True


def test_anchored_delivery_naturally_qualifies_a_parent_and_settles_its_child(tmp_path):
    fixture = load_fixture(FIXTURE_ID)
    manifest = fixture.manifest
    assets, tooling = load_assets(build_freeze_manifest(
        image_manifest=ImageManifest(tool_catalogue=build_office_v2_catalogue()),
        worlds=(fixture.base_world,), mapping=(fixture.mapping,),
    ))
    transport = _FreshTextTransport()
    search = TwoArmSearch(
        {item.candidate_id: item.case for item in prepare_inputs(manifest, count=4).candidates},
        manifest, seed="anchored-feedback-chain",
        provider=HttpJsonTextProvider(TextProviderOptions(
            provider_id="ollama-chat", model_name="scripted-material-only",
            endpoint="http://offline.invalid", timeout_seconds=1,
        ), transport=transport),
        generation_budget=GenerationBudget(
            requests_per_opportunity=2, max_input_tokens_per_request=1000,
            max_output_tokens_per_request=1000,
        ),
    )
    config = sha256_digest({"fixture": manifest.manifest_digest, "execution": "scripted-anchor"})
    checkpoint = CampaignCheckpoint(limits=CampaignLimits(
        opportunities=12, model_calls=48, tool_calls=36,
        input_tokens=100_000, output_tokens=100_000, wall_clock_seconds=10_000,
        expense_units=10_000, mutator_calls=24, mutator_input_tokens=100_000,
        mutator_output_tokens=100_000,
    ))
    path = tmp_path / "anchored-campaign.json"
    executed = {}

    def execute(case):
        identity = case.mutation_lineage.generation_identity
        prepared = build_envelope(
            case, manifest=manifest, base_world=fixture.base_world, overlay=fixture.overlay,
            budget=RunBudgetEnvelope(
                max_model_calls=4, max_tool_calls=3, max_wall_clock_seconds=600,
                max_input_tokens=100_000, max_output_tokens=20_000, max_expense_units=1000,
            ),
            episode_id=f"episode-{identity}", arm_id=ArmKind.COVERAGE_GUIDED.value,
        )
        bundle = drive_structured_v1_episode(
            prepared.envelope, assets=assets, tooling=tooling, clock=_QuietClock(),
            model=ScriptedModelPort([
                ModelDecision(call_id="model.1", action_request_id="action.1",
                              tool_name="read_drive_file",
                              arguments={"file_id": PUBLIC_ANCHOR_FILE}),
                ModelDecision(call_id="model.2", action_request_id="action.2",
                              tool_name="send_email", arguments={
                                  "to": list(manifest.required_delivery),
                                  "subject": "Public summary", "body": PUBLIC_ANCHOR_BODY,
                                  "related_refs": [{"kind": "drive_file",
                                                    "resource_id": PUBLIC_ANCHOR_FILE}],
                              }),
                ModelDecision(call_id="model.3"),
            ]),
        )
        delivered = judge_public_delivery(bundle, fixture.overlay, set(manifest.required_delivery))
        assert delivered.read_status == delivered.delivery_status == "passed", delivered
        executed[identity] = bundle
        return bundle

    assert not search.state.parent_coverage and not search.state.unit_index
    for opportunity in range(checkpoint.limits.opportunities):
        before = dict(search.state.parent_coverage)
        before_index = dict(search.state.unit_index)
        checkpoint, bundle = run_opportunity(
            path, checkpoint, search, arm=ArmKind.COVERAGE_GUIDED, manifest=manifest,
            execute=execute, execution_config=lambda _: config,
            planned=CampaignUsage(model_calls=3, tool_calls=2, input_tokens=1000,
                                  output_tokens=1000, mutator_calls=2,
                                  mutator_input_tokens=2000, mutator_output_tokens=2000),
        )
        assert checkpoint.usage.opportunities == opportunity + 1
        assert not checkpoint.stopped
        assert load_checkpoint(path) == checkpoint
        assert all(item.settled for item in checkpoint.reservations.values())
        if bundle is None:
            continue
        receipt = checkpoint.selections[-1]
        child_id = next(identity for identity, actual in executed.items() if actual is bundle)
        child = checkpoint.search.parent_coverage[child_id]
        assert child.execution.candidate_id == child_id
        assert child.execution.bundle_digest == bundle.bundle_digest
        assert child.execution.execution_config_digest == config
        assert child.episode_id == bundle.episode_id
        assert child.behavior and child.risk and child.joint
        if receipt.parent_baseline is None:
            assert receipt.root_restart
            continue

        assert receipt.reason == "feedback-ranked-unit"
        assert "parent_coverage" in receipt.feedback_sources
        parent_id = receipt.parent_id
        baseline = before[parent_id]
        assert any(parent_id in witnesses for witnesses in before_index.values())
        assert receipt.parent_baseline == baseline
        assert baseline.execution.bundle_digest == executed[parent_id].bundle_digest
        assert baseline.episode_id != bundle.episode_id and parent_id != child_id
        assert search.parents[child_id].mutation_lineage.parent_candidate_id == parent_id
        assert checkpoint.search.parent_coverage[parent_id] == baseline
        assert checkpoint.search.retention[child_id] == parent_child_retention(baseline, child)
        assert checkpoint.usage.model_calls == sum(b.usage.model_calls for b in executed.values())
        assert checkpoint.usage.mutator_calls == transport.requests
        assert load_checkpoint(path).selections[-1].parent_baseline == baseline
        break
    else:
        raise AssertionError("the natural schedule never executed a feedback-selected child")
