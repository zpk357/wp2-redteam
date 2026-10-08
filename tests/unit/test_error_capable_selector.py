"""MW-AC-07/08: guided model selection and pure random selection boundaries."""

from __future__ import annotations

import asyncio
import json

import pytest
from app.agent.react_contract import ReactTurn

from sandbox.scenarios.error_capable import (
    AttackMode,
    CoverageFeedback,
    ErrorCapableMode,
    SelectorRequest,
    TaskFamily,
    attack_spec,
)
from sandbox.scenarios.error_capable_identity import ModelIdentity
from sandbox.scenarios.error_capable_selector import (
    IllegalChoice,
    LLMSelector,
    PureRandomSelector,
    SelectionRejected,
    coordinate_of,
    frozen_menu,
    validate_choice,
)

IDENTITY = ModelIdentity.capture(
    provider_id="local", raw_model_label="scripted-agent", provider_version="test"
)


def request(mode: ErrorCapableMode, *, seed: int = 9) -> SelectorRequest:
    guided = mode is ErrorCapableMode.GUIDED
    return SelectorRequest(
        mode=mode,
        seed=seed,
        agent_model_name=IDENTITY.normalized_model_id,
        selector_model_name=IDENTITY.normalized_model_id if guided else None,
        available_task_families=tuple(TaskFamily),
        available_attacks=tuple(AttackMode),
        feedback=CoverageFeedback() if guided else None,
    )


class ForbiddenHistory:
    def __getattr__(self, name: str):
        raise AssertionError(f"history must not be accessed: {name}")


class SelectionAdapter:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = []

    async def generate(self, messages, tools, *, seed):
        self.calls.append((messages, tools, seed))
        return ReactTurn(assistant_text=self.response, tool_calls=[], stop_reason="stop")


def test_random_replays_seed_and_records_no_provider_or_history_call() -> None:
    sampler = PureRandomSelector()
    sample_request = request(ErrorCapableMode.RANDOM)
    first, raw = sampler(sample_request, ForbiddenHistory(), episode_index=0)
    second, second_raw = PureRandomSelector()(sample_request, ForbiddenHistory(), episode_index=0)
    assert first == second and raw == second_raw
    assert sampler.last_attempt is not None
    assert sampler.last_attempt.provider_calls == 0
    assert sampler.last_attempt.token_usage == {"prompt_tokens": 0, "completion_tokens": 0}
    assert sampler.last_attempt.raw_request["seed"] == sample_request.seed
    assert sampler.last_attempt.raw_request["menu"] == frozen_menu(sample_request)
    assert sampler.last_attempt.raw_request["sampler"] == sampler.name


def test_random_samples_registered_combinations_without_a_repeat_filter() -> None:
    sampler = PureRandomSelector()
    selections = []
    for seed in range(24):
        sample_request = request(ErrorCapableMode.RANDOM, seed=seed)
        choice, _ = sampler(sample_request, ForbiddenHistory(), episode_index=seed)
        assert validate_choice(sample_request, choice) == choice
        selections.append(choice)
    assert len({choice.task_family for choice in selections}) == 3
    assert len({choice.attack_mode for choice in selections}) > 1
    # Repeating a seed after other selections still repeats its sample, regardless of history.
    assert (
        sampler(request(ErrorCapableMode.RANDOM), ForbiddenHistory(), episode_index=99)[0]
        == (selections[9])
    )


def test_guided_and_random_share_the_frozen_menu() -> None:
    assert frozen_menu(request(ErrorCapableMode.GUIDED)) == frozen_menu(
        request(ErrorCapableMode.RANDOM)
    )


def test_guided_model_call_receives_feedback_and_preserves_raw_response() -> None:
    choice, raw = PureRandomSelector()(
        request(ErrorCapableMode.RANDOM), ForbiddenHistory(), episode_index=0
    )
    adapter = SelectionAdapter(raw)
    selector = LLMSelector(adapter, IDENTITY)
    result, result_raw = asyncio.run(
        selector(request(ErrorCapableMode.GUIDED), ForbiddenHistory(), episode_index=0)
    )
    assert result == choice and result_raw == raw
    assert len(adapter.calls) == 1
    messages, tools, seed = adapter.calls[0]
    assert not tools and seed == 9
    assert "feedback" in json.loads(messages[1].content)
    assert selector.last_attempt.raw_response == raw
    assert selector.last_attempt.provider_calls == 1


def test_llm_selector_refuses_random_before_a_model_call() -> None:
    adapter = SelectionAdapter("{}")
    selector = LLMSelector(adapter, IDENTITY)
    with pytest.raises(ValueError, match="restricted to guided"):
        asyncio.run(selector(request(ErrorCapableMode.RANDOM), None, episode_index=0))
    assert not adapter.calls


@pytest.mark.parametrize("response", ["not JSON", "valid"])
def test_guided_records_adapter_tokens_even_for_a_rejected_choice(response: str) -> None:
    _, valid = PureRandomSelector()(request(ErrorCapableMode.RANDOM), None, episode_index=0)
    adapter = SelectionAdapter(valid if response == "valid" else response)
    adapter.last_token_usage = {"prompt_tokens": 81, "completion_tokens": 13}
    selector = LLMSelector(adapter, IDENTITY)
    if response == "valid":
        asyncio.run(selector(request(ErrorCapableMode.GUIDED), None, episode_index=0))
    else:
        with pytest.raises(SelectionRejected):
            asyncio.run(selector(request(ErrorCapableMode.GUIDED), None, episode_index=0))
    assert selector.last_attempt.token_usage == {"prompt_tokens": 81, "completion_tokens": 13}


def test_failed_provider_call_does_not_reuse_previous_token_usage() -> None:
    class FailedAdapter:
        last_token_usage = {"prompt_tokens": 81, "completion_tokens": 13}

        async def generate(self, messages, tools, *, seed):
            raise TimeoutError("provider timeout")

    selector = LLMSelector(FailedAdapter(), IDENTITY)
    with pytest.raises(SelectionRejected):
        asyncio.run(selector(request(ErrorCapableMode.GUIDED), None, episode_index=0))
    assert selector.last_attempt.token_usage is None


@pytest.mark.parametrize("response", ["not JSON", "{}", '{"rationale": ""}'])
def test_invalid_llm_choice_consumes_one_call_without_retry(response: str) -> None:
    adapter = SelectionAdapter(response)
    selector = LLMSelector(adapter, IDENTITY)
    with pytest.raises(SelectionRejected):
        asyncio.run(selector(request(ErrorCapableMode.GUIDED), None, episode_index=0))
    assert len(adapter.calls) == 1
    assert selector.last_attempt.raw_response == response
    assert selector.last_attempt.rejection
    assert selector.last_attempt.provider_calls == 1


def test_a_refusal_that_is_not_a_duplicate_carries_its_coordinate_too() -> None:
    """`AlreadyTaken` handed its coordinate back; every other refusal handed back nothing.

    The campaign puts a refusal into `rejected_menu_cells` only when the exception carries a
    coordinate, so an illegal choice -- a carrier the frozen menu does not offer -- was refused
    with nothing for the payload to report.  `NP`'s own comments say why that matters: at
    `temperature=0.0` an unchanged question gets an unchanged answer, so the arm proposes the same
    illegal combination until the budget is gone.  The formal experiment's second repetition spent
    two Opportunities on one illegal carrier that way, the second one after being refused for it
    once, with the rationale reworded so it read as adaptation while the coordinate stayed put.

    The illegal carrier used to be one that was in the menu but not among the chosen mechanism's
    two.  Carriers no longer belong to mechanisms, so the example is a carrier the menu does not
    have at all.  The refusal under test -- an illegal choice that hands its coordinate back --
    is the same one either way.
    """

    sample_request = request(ErrorCapableMode.GUIDED)
    good, _ = PureRandomSelector()(
        request(ErrorCapableMode.RANDOM), ForbiddenHistory(), episode_index=0
    )
    absent = "carrier-not-in-the-frozen-menu"
    assert absent not in sample_request.available_carriers
    bad = good.model_copy(update={"attack_carrier": absent})

    with pytest.raises(IllegalChoice) as refused:
        validate_choice(sample_request, bad)

    assert str(refused.value) == "unavailable carrier"
    assert refused.value.coordinate == coordinate_of(bad)
    assert refused.value.coordinate["attack_carrier"] == absent
    # Still a ValueError, so every existing caller keeps catching it unchanged.
    assert isinstance(refused.value, ValueError)


def test_the_duplicate_refusal_still_carries_the_same_coordinate_shape() -> None:
    """The two refusal kinds must hand back the same axes, or the set mixes two shapes."""

    sample_request = request(ErrorCapableMode.GUIDED)
    good, _ = PureRandomSelector()(
        request(ErrorCapableMode.RANDOM), ForbiddenHistory(), episode_index=0
    )
    taken = CoverageFeedback(chosen_menu_cells=(coordinate_of(good),))
    with pytest.raises(ValueError) as refused:
        validate_choice(
            sample_request.model_copy(update={"feedback": taken, "require_unobserved": True}), good
        )
    assert str(refused.value) == "guided choice is a combination the run has already taken"
    assert refused.value.coordinate == coordinate_of(good)


def test_the_guided_payload_carries_outcomes_and_no_candidate_list() -> None:
    """The selector is given what to choose by, and nothing whose order could choose for it."""

    feedback = CoverageFeedback(
        menu_gaps_total=2160,
        target_menu_cells=2160,
        family_outcomes=({"task_family": "summary_delivery", "opportunities": 3, "violations": 2},),
        mechanism_outcomes=({"attack_mode": "note_rewrite", "opportunities": 3, "violations": 2},),
        unobserved_by_group=(
            {"task_family": "summary_delivery", "attack_mode": "note_rewrite", "unobserved": 12},
        ),
    )
    payload = LLMSelector(SelectionAdapter("{}"), IDENTITY).payload(
        request(ErrorCapableMode.GUIDED).model_copy(update={"feedback": feedback})
    )
    sent = payload["feedback"]
    assert sent["family_outcomes"] == [dict(feedback.family_outcomes[0])]
    assert sent["mechanism_outcomes"] == [dict(feedback.mechanism_outcomes[0])]
    assert sent["unobserved_by_group"] == [dict(feedback.unobserved_by_group[0])]
    assert sent["menu_gaps"] == [] and sent["menu_gap_details"] == []


def test_the_prompt_changes_when_the_outcome_rows_change_which_is_only_necessary() -> None:
    """The offline half of the counterfactual.  The sufficient half is a provider call.

    `scripts/probe_selector_feedback_use.py` re-asks the model at one history point with the outcome
    rows removed and reports whether the choice moved.  This test only holds down that the two
    prompts really do differ: it cannot show that a model reads either one, and a selector that
    ignores its payload passes it.
    """

    rows = ({"task_family": "summary_delivery", "opportunities": 3, "violations": 2},)
    base = request(ErrorCapableMode.GUIDED).model_copy(
        update={"feedback": CoverageFeedback(menu_gaps_total=10, family_outcomes=rows)}
    )
    erased = base.model_copy(
        update={"feedback": base.feedback.model_copy(update={"family_outcomes": ()})}
    )
    selector = LLMSelector(SelectionAdapter("{}"), IDENTITY)
    assert selector.payload(base) != selector.payload(erased)


def test_a_guided_choice_the_run_has_already_taken_is_rejected() -> None:
    choice, _ = PureRandomSelector()(
        request(ErrorCapableMode.RANDOM), ForbiddenHistory(), episode_index=0
    )
    dumped = choice.model_dump(mode="json")
    taken = {
        key: dumped[key]
        for key in (
            "task_family",
            "path_id",
            "attack_mode",
            "attack_carrier",
            "layout_id",
        )
    }
    guarded = request(ErrorCapableMode.GUIDED).model_copy(
        update={
            "feedback": CoverageFeedback(chosen_menu_cells=(taken,), menu_gaps_total=1),
            "require_unobserved": True,
        }
    )
    with pytest.raises(ValueError, match="already taken"):
        validate_choice(guarded, choice)
    # The same combination is accepted while it is still unobserved, and a control that repeats one
    # fixed condition is not making this choice at all.
    unguarded = guarded.model_copy(update={"require_unobserved": False})
    assert validate_choice(unguarded, choice) == choice
