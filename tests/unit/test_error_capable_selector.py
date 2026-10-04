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
)
from sandbox.scenarios.error_capable_identity import ModelIdentity
from sandbox.scenarios.error_capable_selector import (
    LLMSelector,
    PureRandomSelector,
    SelectionRejected,
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
