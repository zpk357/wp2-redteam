from __future__ import annotations

from sandbox.errors import RuntimeTimeoutError, SandboxConfigurationError
from sandbox.fuzzer.v2_bootstrap import build_exploratory_bootstrap
from sandbox.fuzzer.v2_campaign import record_non_episode_generation
from sandbox.fuzzer.v2_campaign_store import V2CampaignStore
from sandbox.fuzzer.v2_cli import build_parser
from sandbox.fuzzer.v2_real_episode import DockerOfficeV2EpisodeRunner
from sandbox.fuzzer.v2_real_runtime import (
    _episode_failure_is_retryable,
    _episode_failure_receipt,
)
from sandbox.fuzzer.v2_runtime import _campaign_target_reached
from sandbox.fuzzer.v2_strategy import CampaignStrategy
from sandbox.fuzzer.v2_work import AttemptDisposition
from sandbox.protocol import ModelOptions, ModelProvider
from sandbox.replay.exceptions import ReplayPreparationError


def test_exploratory_parser_is_parameterized_and_has_no_release_inputs() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "exploratory-run",
            "--db",
            "campaign.db",
            "--campaign-id",
            "demo",
            "--agent-image",
            "agent:local",
            "--mutator-image",
            "mutator:local",
            "--data-root",
            "data",
            "--episodes",
            "50",
            "--agent-runtime",
            "deepseek_harness",
            "--strategy",
            "random_independent",
        ]
    )
    assert args.episodes == 50
    assert args.agent_runtime == "deepseek_harness"
    assert args.strategy == CampaignStrategy.RANDOM_INDEPENDENT.value


def test_exploratory_campaign_persists_strategy(tmp_path) -> None:
    bootstrap = build_exploratory_bootstrap(episode_limit=30)
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        store.create_campaign(
            campaign_id="demo",
            initial_state=bootstrap.initial_state,
            strategy=CampaignStrategy.RANDOM_INDEPENDENT,
            campaign_seed_value=7,
        )
        assert store.campaign_strategy("demo") is CampaignStrategy.RANDOM_INDEPENDENT


def test_exploratory_resume_reopens_a_paused_campaign(tmp_path) -> None:
    bootstrap = build_exploratory_bootstrap(episode_limit=2)
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        store.create_campaign(campaign_id="demo", initial_state=bootstrap.initial_state)
        store.pause_campaign("demo", reason="temporary-agent-failure")
        resumed = store.resume_paused_campaign("demo", reason="exploratory-resume")
        assert resumed.lifecycle.completion_status is None
        assert resumed.lifecycle.pause_reason is None


def test_exploratory_resume_can_extend_episode_target(tmp_path) -> None:
    bootstrap = build_exploratory_bootstrap(episode_limit=2)
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        store.create_campaign(campaign_id="demo", initial_state=bootstrap.initial_state)
        extended = store.extend_episode_limit("demo", episode_limit=20)
        assert extended.budget.episode_limit == 20
        assert extended.budget.used_episodes == 0


def test_ollama_model_requires_name_and_endpoint_only() -> None:
    assert ModelOptions(
        provider=ModelProvider.OLLAMA,
        model_name="qwen3:8b",
        endpoint="http://127.0.0.1:11434",
    )


def test_exploratory_episode_runner_preserves_tool_call_limit() -> None:
    runner = DockerOfficeV2EpisodeRunner(
        replay_engine=object(),
        artifact_store=object(),
        model_name="qwen3:8b",
        max_tool_calls=37,
    )
    assert runner.max_tool_calls == 37


def test_exploratory_parser_exposes_host_topology_and_smoke() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "exploratory-smoke",
            "--db", "campaign.db",
            "--campaign-id", "smoke",
            "--agent-image", "agent:local",
            "--mutator-image", "mutator:local",
            "--data-root", "data",
            "--ollama-mode", "host",
            "--max-generation-attempts", "4",
        ]
    )
    assert args.ollama_mode == "host"
    assert args.max_generation_attempts == 4
    assert args.agent_context_tokens == 12_288


def test_episode_failures_are_classified_for_bounded_retry() -> None:
    assert _episode_failure_is_retryable(RuntimeTimeoutError("slow"))
    assert _episode_failure_is_retryable(ReplayPreparationError(-32108, "transport"))
    assert not _episode_failure_is_retryable(SandboxConfigurationError("bad image"))
    receipt = _episode_failure_receipt(
        work_id="work.demo",
        attempt_number=1,
        error=RuntimeTimeoutError("slow"),
        elapsed_ms=100,
        retryable=True,
    )
    assert receipt.disposition is AttemptDisposition.RETRYABLE
    assert receipt.error_code == "episode-timeout"


def test_non_episode_generation_does_not_consume_episode_target() -> None:
    bootstrap = build_exploratory_bootstrap(episode_limit=2)
    state = bootstrap.initial_state.model_copy(
        update={"lifecycle": record_non_episode_generation(bootstrap.initial_state.lifecycle)}
    )
    assert state.lifecycle.counters.generation_index == 1
    assert state.lifecycle.counters.valid_committed_episodes == 0
    assert not _campaign_target_reached(state, generation_count=2, exploratory=True)
