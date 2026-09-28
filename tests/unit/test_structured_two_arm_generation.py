"""Both arms generate through the same provider, and the Mutator's cost is settled (`SS-011`,
`SS-012` step 4, `SS-014`, `SOC-FBK-15`).

The provider here is a scripted double: it exercises the contract (one generation, at most one
repair, receipts for every request) without a model.  The point of these tests is that the
*search* - not a test - decides which nodes need text, that a failure ends the opportunity
instead of producing a candidate, and that unreported usage stays unknown.
"""

from __future__ import annotations

import pytest
from structured_coverage_helpers import record_coverage

from sandbox.structured_v1.campaign import (
    CampaignCheckpoint,
    CampaignLimits,
    CampaignUsage,
    load_checkpoint,
    run_opportunity,
)
from sandbox.structured_v1.coverage import BehaviorAtom, CoverageResult
from sandbox.structured_v1.drivers import ScriptedModelPort, drive_structured_v1_episode
from sandbox.structured_v1.experiment_inputs import prepare_inputs
from sandbox.structured_v1.generation import (
    GenerationBudget,
    TextGenerationFailed,
    generate_texts,
)
from sandbox.structured_v1.model import ModelDecision
from sandbox.structured_v1.provider import (
    ProviderFailureClass,
    ProviderTextItem,
    ProviderTextRequest,
    ProviderTextResponse,
)
from sandbox.structured_v1.rendering import render_material
from sandbox.structured_v1.search import ArmKind, TwoArmSearch

BUDGET = GenerationBudget(
    requests_per_opportunity=2,
    max_input_tokens_per_request=8192,
    max_output_tokens_per_request=4096,
)


@pytest.mark.parametrize(
    ("mode", "wanted", "accepted", "requests"),
    [
        ("valid", ("n1", "n2"), True, 1),
        ("repair-once", ("n1", "n2"), True, 2),
        ("always-invalid", ("n1",), False, 2),
        ("transport-error", ("n1",), False, 2),
        ("valid", (), True, 0),
        (None, (), True, 0),
        (None, ("n1",), False, 0),
    ],
)
def test_every_generation_outcome_preserves_the_scheduled_direction(
    manifest, mode, wanted, accepted, requests
) -> None:
    provider = None if mode is None else ScriptedTextProvider(mode=mode)
    try:
        result = generate_texts(
            provider=provider,
            manifest=manifest,
            node_ids=wanted,
            budget=BUDGET,
            opportunity=1,
            arm=ArmKind.RANDOM_INDEPENDENT.value,
            parent_id="root",
            obligation_direction="audience-scope",
        )
        plan = result.plan
    except TextGenerationFailed as error:
        plan = error.plan
    assert plan.obligation_direction == "audience-scope"
    assert plan.accepted is accepted
    assert plan.requests == requests
    if provider is not None:
        assert all(r.obligation_direction == "audience-scope" for r in provider.requests)


class ScriptedTextProvider:
    """A provider double: valid text, one repair, or a failure that never repairs."""

    provider_id = "scripted-text"
    provider_version = "scripted-text-1"

    def __init__(
        self,
        *,
        mode: str = "valid",
        report_usage: bool = True,
        input_tokens: int = 7,
        output_tokens: int = 3,
    ) -> None:
        self.mode = mode
        self.report_usage = report_usage
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.requests: list[ProviderTextRequest] = []

    def complete(self, request: ProviderTextRequest) -> ProviderTextResponse:
        self.requests.append(request)
        usage = {
            "input_tokens": self.input_tokens if self.report_usage else 0,
            "output_tokens": self.output_tokens if self.report_usage else 0,
            "usage_reported": self.report_usage,
        }
        if self.mode == "transport-error":
            raise RuntimeError("scripted transport failure")
        if self.mode == "always-invalid":
            return ProviderTextResponse(
                provider_id=self.provider_id,
                provider_version=self.provider_version,
                items=(),
                **usage,
            )
        if self.mode == "repair-once" and len(self.requests) == 1:
            # The first answer omits the last node, so the host asks exactly once more.
            return ProviderTextResponse(
                provider_id=self.provider_id,
                provider_version=self.provider_version,
                items=tuple(
                    ProviderTextItem(node_id=node_id, text=f"合成低信任文字 {node_id}。")
                    for node_id in request.node_ids[:-1]
                ),
                **usage,
            )
        return ProviderTextResponse(
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            items=tuple(
                ProviderTextItem(node_id=node_id, text=f"合成低信任文字 {node_id}。")
                for node_id in request.node_ids
            ),
            **usage,
        )


def _parents(manifest, count: int = 4) -> dict[str, object]:
    inputs = prepare_inputs(manifest, count=count)
    return {item.candidate_id: item.case for item in inputs.candidates}


def _search(manifest, provider, *, seed: str = "two-arm-gen") -> TwoArmSearch:
    return TwoArmSearch(
        _parents(manifest), manifest, seed=seed, provider=provider, generation_budget=BUDGET
    )


def _seed_archive(manifest, search: TwoArmSearch) -> None:
    """Give the guided arm a behaviour representative so a local edit can be drawn."""

    record_coverage(search,
        CoverageResult(
            episode_id="seed",
            fixture_id=manifest.fixture_id,
            behavior=(BehaviorAtom(key=("seed-behavior",)),),
        ),
        parent_id=sorted(search.parents)[-1],
    )


def _draw_guided_edit(manifest, search: TwoArmSearch):
    """Seed the archive, then keep drawing local behaviour edits until one carries text."""

    _seed_archive(manifest, search)
    direction = "data-release"
    slot = 0
    while slot <= 80:
        if slot % 4 == 1 and slot % 5 not in TwoArmSearch._root_slots(
            f"{search.seed}:{direction}", slot // 5
        ):
            search.state = search.state.model_copy(
                update={
                    "feedback_opportunity": 0,
                    "direction_opportunities": {direction: slot},
                }
            )
            receipt = search.select(ArmKind.COVERAGE_GUIDED)
            child = search.generate(receipt)
            plan = search.state.plans[-1]
            if plan.editable_nodes and plan.operation is not None:
                return child, plan
        slot += 1
    raise AssertionError("no text-bearing local edit was drawn")


def test_both_arms_ask_the_same_generator_and_record_their_plans(manifest) -> None:
    provider = ScriptedTextProvider()
    search = _search(manifest, provider)

    child, text_plan = _draw_guided_edit(manifest, search)
    random_receipt = search.select(ArmKind.RANDOM_INDEPENDENT)
    root = search.generate(random_receipt)
    root_plan = search.state.plans[-1]

    # Both arms paid the same generator, and every plan names it and the budget it ran under.
    assert {plan.provider_id for plan in search.state.plans} == {"scripted-text"}
    assert all(plan.budget == BUDGET for plan in search.state.plans)
    assert len(provider.requests) == sum(1 for plan in search.state.plans if plan.requests)
    assert text_plan.operation is not None
    assert text_plan.preserved_units
    assert text_plan.accepted is True
    assert text_plan.requests == 1
    assert child.mutation_lineage.operation is not None
    assert root.mutation_lineage.operation is None
    assert root_plan.arm == ArmKind.RANDOM_INDEPENDENT.value
    assert root_plan.operation is None


def test_a_generation_that_needs_one_repair_is_billed_as_two_requests(manifest) -> None:
    provider = ScriptedTextProvider(mode="repair-once")
    search = _search(manifest, provider)

    case, plan = _draw_guided_edit(manifest, search)

    assert plan.requests == 2
    assert plan.accepted is True
    assert case.mutation_lineage.operation is not None


def test_a_generation_that_never_succeeds_ends_the_opportunity(manifest) -> None:
    provider = ScriptedTextProvider(mode="always-invalid")
    search = _search(manifest, provider)

    for _ in range(12):
        receipt = search.select(ArmKind.COVERAGE_GUIDED)
        try:
            search.generate(receipt)
        except TextGenerationFailed as failed:
            assert failed.attempts == 2
            assert failed.plan.accepted is False
            assert failed.plan.failure_class == ProviderFailureClass.MISSING_NODE.value
            assert search.state.plans[-1].accepted is False
            return
    raise AssertionError("the invalid provider was never a failure")


def test_a_provider_that_exceeds_its_declared_budget_is_refused(manifest) -> None:
    provider = ScriptedTextProvider(mode="repair-once")
    search = TwoArmSearch(
        _parents(manifest),
        manifest,
        seed="budget",
        provider=provider,
        generation_budget=BUDGET.model_copy(update={"requests_per_opportunity": 1}),
    )

    for _ in range(12):
        try:
            search.generate(search.select(ArmKind.COVERAGE_GUIDED))
        except ValueError as error:
            assert "declared budget" in str(error)
            return
    raise AssertionError("no repair path was drawn in twelve opportunities")


def _execute(manifest):
    import test_structured_episode_rehearsal as rehearsal

    def execute(case):
        envelope, store, _, _ = rehearsal._prepared(manifest, case)
        rendered = render_material(case, manifest)
        s1 = next(slot for slot in rendered.slots if slot.slot_id == "s1")
        tool_name = "read_drive_file" if s1.contents else "read_file"
        arguments = (
            {"file_id": "f-overview"}
            if s1.contents
            else {"path": "/workspace/notes/delivery-notes.md"}
        )
        model = ScriptedModelPort(
            [
                ModelDecision(
                    call_id="model.001",
                    action_request_id="action-0001",
                    tool_name=tool_name,
                    arguments=arguments,
                ),
                ModelDecision(call_id="model.002"),
            ]
        )
        return drive_structured_v1_episode(
            envelope,
            assets=store,
            tooling=rehearsal._tooling_facts(),
            model=model,
            clock=rehearsal.FakeClock([0, 1, 60]),
        )

    return execute


def _limits() -> CampaignLimits:
    return CampaignLimits(
        opportunities=6,
        model_calls=24,
        tool_calls=24,
        input_tokens=6000,
        output_tokens=6000,
        wall_clock_seconds=3600,
        expense_units=600,
        mutator_calls=12,
        mutator_input_tokens=BUDGET.max_input_tokens_per_request * 6,
        mutator_output_tokens=BUDGET.max_output_tokens_per_request * 6,
    )


def _planned() -> CampaignUsage:
    return CampaignUsage(
        model_calls=2,
        tool_calls=1,
        mutator_calls=BUDGET.requests_per_opportunity,
        mutator_input_tokens=BUDGET.max_input_tokens_per_request,
        mutator_output_tokens=BUDGET.max_output_tokens_per_request,
    )


def test_an_opportunity_settles_the_mutator_cost_and_persists_its_plan(manifest, tmp_path) -> None:
    provider = ScriptedTextProvider()
    search = _search(manifest, provider, seed="settle")
    path = tmp_path / "campaign.json"

    state = CampaignCheckpoint(limits=_limits())
    for _ in range(_limits().opportunities):
        state, bundle = run_opportunity(
            path,
            state,
            search,
            arm=ArmKind.COVERAGE_GUIDED,
            manifest=manifest,
            execute=_execute(manifest),
            planned=_planned(),
        )
        assert bundle is not None
        if state.usage.mutator_calls:
            break

    assert state.usage.mutator_calls == 1
    assert state.usage.mutator_input_tokens == 7
    assert state.usage.mutator_output_tokens == 3
    assert state.usage.complete is True
    assert any(plan.accepted and plan.requests for plan in state.generations)
    assert {plan.provider_id for plan in state.generations} == {provider.provider_id}
    assert load_checkpoint(path) == state


def test_a_failed_generation_is_billed_and_produces_no_episode(manifest, tmp_path) -> None:
    provider = ScriptedTextProvider(mode="transport-error", report_usage=False)
    search = _search(manifest, provider, seed="failed-generation")
    path = tmp_path / "campaign.json"

    state = CampaignCheckpoint(limits=_limits())
    for _ in range(_limits().opportunities):
        state, bundle = run_opportunity(
            path,
            state,
            search,
            arm=ArmKind.COVERAGE_GUIDED,
            manifest=manifest,
            execute=_execute(manifest),
            planned=_planned(),
        )
        if bundle is None:
            break

    assert bundle is None
    assert state.usage.mutator_calls == 2
    # Unreported usage stays unknown: the entry must not read as a free call.
    assert state.usage.mutator_input_tokens is None
    assert state.usage.complete is False
    assert [plan.accepted for plan in state.generations][-1] is False
    assert [plan.requests for plan in state.generations][-1] == 2
    assert state.generations[-1].failure_class == ProviderFailureClass.TRANSPORT.value
    assert state.search.plans[-1].accepted is False
    assert load_checkpoint(path) == state


@pytest.mark.parametrize("arm", tuple(ArmKind))
def test_failed_generations_consume_the_common_direction_schedule(manifest, tmp_path, arm) -> None:
    provider = ScriptedTextProvider(mode="always-invalid")
    search = _search(manifest, provider, seed="failed-generation")
    state = CampaignCheckpoint(limits=_limits())
    path = tmp_path / "campaign.json"
    failures = 0
    for index in range(_limits().opportunities):
        state, bundle = run_opportunity(
            path,
            state,
            search,
            arm=arm,
            manifest=manifest,
            execute=_execute(manifest),
            planned=_planned(),
        )
        plan = state.generations[-1]
        assert plan.opportunity == index
        assert plan.obligation_direction == ("data-release", "audience-scope")[index % 2]
        assert search.state.opportunity == state.usage.opportunities == index + 1
        if bundle is None:
            failures += 1
            assert plan.accepted is False and plan.requests == 2
    assert failures > 1
    assert state.usage.mutator_calls == 2 * failures
    assert load_checkpoint(path) == state
