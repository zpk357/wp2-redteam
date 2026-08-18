from __future__ import annotations

from sandbox.fuzzer.v2_campaign_store import V2CampaignStore
from sandbox.fuzzer.v2_real_runtime import (
    RealCampaignBootstrap,
    run_or_resume_real_campaign,
)
from sandbox.fuzzer.v2_real_episode import _recorded_agent_tokens
from sandbox.mutation.v2_provider import (
    ProviderFailureClass,
    V2ProviderFailure,
    seal_failed_provider_attempt,
)
from sandbox.replay.digests import sha256_digest
from sandbox.replay.models import (
    RECORDED_MODEL_TOKEN_USAGE_KEY,
    RecordedModelDecision,
)
from tests.unit.test_office_v2_feedback_loop_batch_c import CAMPAIGN_ID, loop_fixture


class PermanentlyFailingProvider:
    provider_id = "provider-docker-ollama-v2"

    async def generate(self, *, plan, brief, attempt_index):
        del brief
        attempt = seal_failed_provider_attempt(
            plan=plan,
            attempt_index=attempt_index,
            request_digest=sha256_digest(
                {"plan": plan.plan_digest, "attempt": attempt_index}
            ),
            failure_class=ProviderFailureClass.CONFIGURATION_PERMANENT,
        )
        raise V2ProviderFailure("expected test failure", attempt=attempt)


class ForbiddenEpisodeRunner:
    async def execute(self, **_kwargs):
        raise AssertionError("rejected candidate must not launch an Agent Episode")


def test_recorded_agent_tokens_sum_prompt_and_completion_usage() -> None:
    action = {
        "assistant_text": "done",
        "tool_calls": [],
        "stop_reason": "stop",
        RECORDED_MODEL_TOKEN_USAGE_KEY: {
            "prompt_tokens": 21,
            "completion_tokens": 8,
        },
    }
    decision = RecordedModelDecision(
        decision_id="decision-token-usage",
        sequence=1,
        decision_index=0,
        before_checkpoint_id="checkpoint-before",
        input_digest=sha256_digest("input"),
        output_digest=sha256_digest(action),
        action=action,
        model_name="OllamaReactProvider",
        model_version="ollama-react:test",
    )

    assert _recorded_agent_tokens((decision.model_dump_json() + "\n").encode()) == 29


def test_real_runtime_chains_rejected_generations_without_agent_or_coverage() -> None:
    _, state = loop_fixture()
    with V2CampaignStore(":memory:") as store:
        result = run_or_resume_real_campaign(
            store=store,
            campaign_id=CAMPAIGN_ID,
            bootstrap=RealCampaignBootstrap(
                initial_state=state,
                model_identity_digest="sha256:" + "a" * 64,
            ),
            generation_count=2,
            mutation_provider=PermanentlyFailingProvider(),
            episode_runner=ForbiddenEpisodeRunner(),
        )
        next_state = store.load_state(CAMPAIGN_ID)

    assert result.completed_generation_count == 2
    assert len(result.feedback_digests) == 2
    assert next_state.coverage == state.coverage
    assert next_state.corpus == state.corpus
    assert next_state.lifecycle.counters.valid_committed_episodes == 0
    assert next_state.lifecycle.counters.invalid_or_failed_attempts == 2
    assert next_state.budget.consumed.mutator_tokens == 0
