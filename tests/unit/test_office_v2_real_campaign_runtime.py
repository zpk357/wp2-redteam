from __future__ import annotations

import pytest

from sandbox.fuzzer.v2_campaign_store import V2CampaignStore
from sandbox.fuzzer.v2_real_episode import _recorded_agent_tokens
from sandbox.fuzzer.v2_real_runtime import (
    RealCampaignBootstrap,
    run_or_resume_real_campaign,
)
from sandbox.fuzzer.v2_stage6_evidence import audit_two_generation_gate
from sandbox.mutation.v2_provider import (
    ProviderFailureClass,
    RuleBasedV2MutationProvider,
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


class RaisingEpisodeRunner:
    async def execute(self, **_kwargs):
        raise RuntimeError("simulated unclassified runner failure")


class CountingInvalidProvider:
    provider_id = RuleBasedV2MutationProvider.provider_id

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, *, plan, brief, attempt_index):
        from sandbox.mutation.v2_brief import ProviderSlotValue

        self.calls += 1
        result = await RuleBasedV2MutationProvider().generate(
            plan=plan, brief=brief, attempt_index=attempt_index
        )
        return result.model_copy(
            update={
                "candidate": result.candidate.model_copy(
                    update={
                        "slot_values": (
                            ProviderSlotValue(
                                payload_slot_id="slot.unplanned",
                                generated_content="invalid frozen slot",
                            ),
                        )
                    }
                )
            }
        )


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


def test_real_runtime_pauses_after_permanent_provider_failure(
    tmp_path,
) -> None:
    _, state = loop_fixture()
    path = tmp_path / "rejected-generations.sqlite3"
    with V2CampaignStore(path) as store:
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
    gate = audit_two_generation_gate(db_path=path, campaign_id=CAMPAIGN_ID)

    assert result.completed_generation_count == 1
    assert len(result.feedback_digests) == 1
    assert next_state.coverage == state.coverage
    assert next_state.corpus == state.corpus
    assert next_state.lifecycle.counters.valid_committed_episodes == 0
    assert next_state.lifecycle.counters.invalid_or_failed_attempts == 1
    assert next_state.lifecycle.completion_status == "paused"
    assert next_state.lifecycle.pause_reason == "configuration_permanent"
    assert next_state.budget.consumed.mutator_tokens == 0
    assert gate["passed"] is False


def test_real_runtime_seals_unknown_episode_failure_and_pauses(tmp_path) -> None:
    _, state = loop_fixture()
    path = tmp_path / "unknown-episode.sqlite3"
    with V2CampaignStore(path) as store:
        result = run_or_resume_real_campaign(
            store=store,
            campaign_id=CAMPAIGN_ID,
            bootstrap=RealCampaignBootstrap(
                initial_state=state,
                model_identity_digest="sha256:" + "a" * 64,
            ),
            generation_count=2,
            mutation_provider=RuleBasedV2MutationProvider(),
            episode_runner=RaisingEpisodeRunner(),
        )
        next_state = store.load_state(CAMPAIGN_ID)
        work = store._db.execute(
            "SELECT work_id FROM candidate_work WHERE campaign_id=?",
            (CAMPAIGN_ID,),
        ).fetchone()
        receipts = store.receipts_for_work(work["work_id"])

        assert result.completed_generation_count == 1
        assert next_state.lifecycle.completion_status == "paused"
        assert next_state.budget.reserved_episodes == 0
        assert store.load_work(work["work_id"]).state == "ambiguous"
        assert receipts[0].disposition == "ambiguous"


def test_resume_settles_sealed_preparation_without_recalling_provider(
    tmp_path, monkeypatch
) -> None:
    _, state = loop_fixture()
    path = tmp_path / "resume-preparation.sqlite3"
    provider = CountingInvalidProvider()
    with V2CampaignStore(path) as store:
        monkeypatch.setattr(
            store,
            "settle_preparation_cost",
            lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("power-loss")),
        )
        with pytest.raises(RuntimeError, match="power-loss"):
            run_or_resume_real_campaign(
                store=store,
                campaign_id=CAMPAIGN_ID,
                bootstrap=RealCampaignBootstrap(
                    initial_state=state,
                    model_identity_digest="sha256:" + "a" * 64,
                ),
                generation_count=1,
                mutation_provider=provider,
                episode_runner=ForbiddenEpisodeRunner(),
            )
    with V2CampaignStore(path) as reopened:
        result = run_or_resume_real_campaign(
            store=reopened,
            campaign_id=CAMPAIGN_ID,
            bootstrap=RealCampaignBootstrap(
                initial_state=state,
                model_identity_digest="sha256:" + "a" * 64,
            ),
            generation_count=1,
            mutation_provider=provider,
            episode_runner=ForbiddenEpisodeRunner(),
        )

    assert provider.calls == 1
    assert result.completed_generation_count == 1


def test_resume_pauses_ambiguous_provider_window_without_recalling_provider(
    tmp_path, monkeypatch
) -> None:
    _, state = loop_fixture()
    path = tmp_path / "ambiguous-provider.sqlite3"
    provider = CountingInvalidProvider()
    with V2CampaignStore(path) as store:
        monkeypatch.setattr(
            store,
            "put_mutation_preparation",
            lambda _preparation: (_ for _ in ()).throw(RuntimeError("power-loss")),
        )
        with pytest.raises(RuntimeError, match="power-loss"):
            run_or_resume_real_campaign(
                store=store,
                campaign_id=CAMPAIGN_ID,
                bootstrap=RealCampaignBootstrap(
                    initial_state=state,
                    model_identity_digest="sha256:" + "a" * 64,
                ),
                generation_count=1,
                mutation_provider=provider,
                episode_runner=ForbiddenEpisodeRunner(),
            )
    with V2CampaignStore(path) as reopened:
        result = run_or_resume_real_campaign(
            store=reopened,
            campaign_id=CAMPAIGN_ID,
            bootstrap=RealCampaignBootstrap(
                initial_state=state,
                model_identity_digest="sha256:" + "a" * 64,
            ),
            generation_count=1,
            mutation_provider=provider,
            episode_runner=ForbiddenEpisodeRunner(),
        )
        paused = reopened.load_state(CAMPAIGN_ID)

    assert provider.calls == 1
    assert result.completed_generation_count == 0
    assert paused.lifecycle.completion_status == "paused"
    assert paused.lifecycle.pause_reason == (
        "ambiguous-provider-window-before-preparation-seal"
    )
