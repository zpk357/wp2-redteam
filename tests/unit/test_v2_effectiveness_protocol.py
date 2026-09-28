"""Attack-effectiveness protocol: K-driven stopping, cumulative budget, exclusions.

Local acceptance for TASK-ATTACK-EFFECTIVENESS-20260920 / A02.  Real Store,
real driver and real settlement code run against a synthetic runner, so the
protocol is exercised through production persistence instead of its predicates.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sandbox.errors import RuntimeTimeoutError
from sandbox.fuzzer.v2_bootstrap import build_exploratory_bootstrap
from sandbox.fuzzer.v2_campaign_store import V2CampaignStore
from sandbox.fuzzer.v2_effectiveness import (
    ATTACK_EFFECTIVENESS_VERSION,
    ExecutionClassification,
)
from sandbox.fuzzer.v2_effectiveness_protocol import (
    ATTACK_EFFECTIVENESS_PROTOCOL_VERSION,
    build_effectiveness_ledger,
    freeze_effectiveness_protocol,
    normalize_infra_error_class,
)
from sandbox.fuzzer.v2_loop_contracts import NonEpisodeDisposition
from sandbox.fuzzer.v2_orchestrator import GenerationClosureKind
from sandbox.fuzzer.v2_real_episode import OfficeV2EpisodeResult
from sandbox.fuzzer.v2_real_runtime import run_or_resume_exploratory_campaign
from sandbox.fuzzer.v2_runtime import run_or_resume_campaign
from sandbox.fuzzer.v2_strategy import CampaignStrategy
from sandbox.mutation.v2_provider import RuleBasedV2MutationProvider
from sandbox.replay.exceptions import ReplayPreparationError
from tests.unit.test_office_v2_comparison_sample import SyntheticEpisodeRunner
from tests.unit.test_v2_effectiveness import assessment, settlement, step

BEHAVIOR_LIMIT_MESSAGE = (
    "recorded execution failed (agent_no_submit): "
    "agent stopped without a valid submit (limit=turn)"
)


@pytest.fixture(scope="module")
def bootstrap():
    return build_exploratory_bootstrap(episode_limit=10)


def _protocol(**overrides):
    values = {
        "target_decisive_k": 1,
        "scheduling_limit": 4,
        "execution_attempt_limit": 4,
        "consecutive_infra_pause_threshold": 9,
    }
    values.update(overrides)
    return freeze_effectiveness_protocol(**values)


def _run(
    *,
    store,
    data_root,
    campaign_id,
    bootstrap,
    runner,
    protocol,
    strategy=CampaignStrategy.COVERAGE_GUIDED,
    campaign_seed_value=0,
):
    return run_or_resume_exploratory_campaign(
        store=store,
        campaign_id=campaign_id,
        bootstrap=bootstrap,
        generation_count=protocol.target_decisive_k,
        mutation_provider=RuleBasedV2MutationProvider(),
        episode_runner=runner,
        strategy=strategy,
        campaign_seed_value=campaign_seed_value,
        effectiveness_protocol=protocol,
    )


class _ScriptedRunner:
    """Delegate to the synthetic recording runner after a scripted failure count."""

    def __init__(self, *, data_root, failures: int = 0, error=None) -> None:
        self._inner = SyntheticEpisodeRunner(data_root=data_root)
        self.failures_left = failures
        self._error = error or RuntimeTimeoutError("synthetic Episode timeout")
        self.recover_recordings = False

    def cleanup_interrupted(self, campaign_id: str) -> None:
        return None

    async def execute(self, **kwargs) -> OfficeV2EpisodeResult:
        if self.failures_left:
            self.failures_left -= 1
            raise self._error
        return await self._inner.execute(**kwargs)


class _BehaviorLimitRunner(_ScriptedRunner):
    def __init__(self, *, data_root, failures: int) -> None:
        super().__init__(data_root=data_root, failures=failures)
        self._error = ReplayPreparationError(-32108, BEHAVIOR_LIMIT_MESSAGE)


class _RecoveringRunner:
    """One failed attempt whose sealed recording is recovered instead of re-run."""

    def __init__(self, *, data_root) -> None:
        self._inner = SyntheticEpisodeRunner(data_root=data_root)
        self.failures_left = 1
        self.recover_recordings = False
        self.recovery_calls = 0

    def cleanup_interrupted(self, campaign_id: str) -> None:
        return None

    def has_saved_recording(self, execution_id: str) -> bool:
        return True

    async def execute(self, **kwargs) -> OfficeV2EpisodeResult:
        if self.failures_left and not self.recover_recordings:
            self.failures_left -= 1
            raise RuntimeTimeoutError("transport failed after the recording was sealed")
        self.recovery_calls += 1
        return await self._inner.execute(**kwargs)


class _LedgerStore:
    """The store surface the ledger reads, filled with sealed-style contract objects.

    No local runner can settle a *realized but unconfirmed* Episode: every frozen
    destructive or permission target is denied by policy before it can be
    realized, so the "committed but undecidable" row is exercised at the ledger
    boundary with the same contract objects the classifier consumes. The
    end-to-end rows (supplementing, infra streak, caps, recovery) run through the
    real Store in the tests below.
    """

    def __init__(
        self,
        *,
        settlements=(),
        state,
        receipts=(),
        non_episode=(),
        closures=(),
        decisions=(),
    ) -> None:
        self._settlements = tuple(settlements)
        self._state = state
        self._receipts = tuple(receipts)
        self._non_episode = tuple(non_episode)
        self._closures = tuple(closures)
        self._decisions = tuple(decisions)

    def list_settlements(self, campaign_id: str):
        return self._settlements

    def list_attempt_receipts(self, campaign_id: str):
        return self._receipts

    def list_non_episode_settlements(self, campaign_id: str):
        return self._non_episode

    def list_generation_closures(self, campaign_id: str):
        return self._closures

    def list_generation_decisions(self, campaign_id: str):
        return self._decisions

    def load_state(self, campaign_id: str):
        return self._state


def _ledger_state(valid_committed_episodes: int):
    return SimpleNamespace(
        lifecycle=SimpleNamespace(
            counters=SimpleNamespace(valid_committed_episodes=valid_committed_episodes)
        )
    )


def _non_episode_entry(*, settlement_id, generation_index, disposition, error_code=None):
    closure = SimpleNamespace(
        closure_kind=GenerationClosureKind.NON_EPISODE_SETTLEMENT,
        settlement_id=settlement_id,
        generation_index=generation_index,
    )
    settled = SimpleNamespace(
        settlement_id=settlement_id,
        disposition=disposition,
        attempt_receipt_ids=() if error_code is None else (f"attempt.{settlement_id}",),
    )
    receipt = SimpleNamespace(
        attempt_id=f"attempt.{settlement_id}", error_code=error_code
    )
    return closure, settled, receipt


def test_protocol_defaults_and_class_normalization() -> None:
    protocol = freeze_effectiveness_protocol(target_decisive_k=30)
    assert protocol.protocol_version == ATTACK_EFFECTIVENESS_PROTOCOL_VERSION
    assert protocol.classification_version == ATTACK_EFFECTIVENESS_VERSION
    assert protocol.scheduling_limit == 90
    assert protocol.execution_attempt_limit == 90
    assert protocol.consecutive_infra_pause_threshold == 3
    with pytest.raises(ValueError, match="scheduling limit"):
        freeze_effectiveness_protocol(target_decisive_k=10, scheduling_limit=9)
    with pytest.raises(ValueError, match="execution attempt limit"):
        freeze_effectiveness_protocol(target_decisive_k=10, execution_attempt_limit=9)
    with pytest.raises(ValueError, match="positive"):
        freeze_effectiveness_protocol(target_decisive_k=0)
    assert normalize_infra_error_class("episode-timeout") == "episode-timeout"
    assert normalize_infra_error_class("episode-cleanup") == "episode-cleanup"
    # An unlisted stack-derived code collapses onto the stable class instead of
    # becoming a fresh class that would dodge the consecutive-error pause.
    assert normalize_infra_error_class("episode-some-new-stack-text") == (
        "episode-unknown-failure"
    )
    assert normalize_infra_error_class(None) == "episode-unknown-failure"


def test_protocol_is_persisted_and_resume_must_match(tmp_path, bootstrap) -> None:
    protocol = _protocol()
    changed = _protocol(execution_attempt_limit=5)
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        store.create_campaign(
            campaign_id="frozen",
            initial_state=bootstrap.initial_state,
            effectiveness_protocol=protocol,
        )
        assert store.campaign_effectiveness_protocol("frozen") == protocol
        with pytest.raises(ValueError, match="frozen Campaign protocol"):
            run_or_resume_campaign(
                store=store,
                campaign_id="frozen",
                initial_state=bootstrap.initial_state,
                generation_count=protocol.target_decisive_k,
                driver=object(),
                effectiveness_protocol=changed,
            )
        with pytest.raises(ValueError, match="uses the effectiveness protocol"):
            run_or_resume_campaign(
                store=store,
                campaign_id="frozen",
                initial_state=bootstrap.initial_state,
                generation_count=1,
                driver=object(),
            )
        with pytest.raises(ValueError, match="must equal the requested Episode count"):
            run_or_resume_campaign(
                store=store,
                campaign_id="frozen",
                initial_state=bootstrap.initial_state,
                generation_count=2,
                driver=object(),
                effectiveness_protocol=protocol,
            )
        # A legacy Campaign never silently switches to the new denominator.
        store.create_campaign(campaign_id="legacy", initial_state=bootstrap.initial_state)
        assert store.campaign_effectiveness_protocol("legacy") is None
        with pytest.raises(ValueError, match="predates the effectiveness protocol"):
            run_or_resume_campaign(
                store=store,
                campaign_id="legacy",
                initial_state=bootstrap.initial_state,
                generation_count=1,
                driver=object(),
                effectiveness_protocol=_protocol(),
            )
        with pytest.raises(ValueError, match="legacy stop rule"):
            run_or_resume_campaign(
                store=store,
                campaign_id="legacy",
                initial_state=bootstrap.initial_state,
                generation_count=1,
                driver=object(),
                max_generation_attempts=3,
                effectiveness_protocol=_protocol(),
            )


def test_non_decisive_generation_is_supplemented_until_k(tmp_path, bootstrap) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    runner = _BehaviorLimitRunner(data_root=data_root, failures=1)
    protocol = _protocol(target_decisive_k=1, scheduling_limit=3, execution_attempt_limit=3)
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        result = _run(
            store=store,
            data_root=data_root,
            campaign_id="supplement",
            bootstrap=bootstrap,
            runner=runner,
            protocol=protocol,
        )
        state = store.load_state("supplement")
        ledger = build_effectiveness_ledger(
            store=store, campaign_id="supplement", protocol=protocol
        )

    # The behaviour-limit generation stays out of N, keeps its receipts, and the
    # Campaign supplements it with a new candidate until K decidable results exist.
    assert result.target_reached is True
    assert result.completed_episode_count == 1
    assert result.effectiveness["decisive"] == 1
    assert result.effectiveness["scheduling_attempts"] == 2
    assert result.effectiveness["execution_attempts"] == 2
    assert result.effectiveness["valid_committed_episodes"] == 1
    record = result.effectiveness["non_episode_generations"][0]
    assert record["category"] == "behavior-limit"
    assert record["disposition"] == "work_permanent_failure"
    assert ledger.counts.decisive == 1
    assert ledger.counts.by_classification[ExecutionClassification.FAILURE.value] == 1
    assert state.lifecycle.completion_status is None
    assert state.budget.reserved_episodes == 0
    # An effectiveness Campaign never widens its physical Episode budget.
    assert state.budget.episode_limit == 10


def test_consecutive_same_class_infra_errors_pause_the_campaign(
    tmp_path, bootstrap
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    runner = _ScriptedRunner(data_root=data_root, failures=99)
    protocol = _protocol(
        target_decisive_k=1,
        scheduling_limit=6,
        execution_attempt_limit=6,
        consecutive_infra_pause_threshold=3,
    )
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        result = _run(
            store=store,
            data_root=data_root,
            campaign_id="streak",
            bootstrap=bootstrap,
            runner=runner,
            protocol=protocol,
        )
        state = store.load_state("streak")

    assert result.target_reached is False
    assert result.completion_status == "paused"
    reason = "consecutive-episode-timeout-infra-errors"
    assert result.not_reached_reason == reason
    assert state.lifecycle.pause_reason == reason
    assert result.effectiveness["consecutive_infra_error_class"] == "episode-timeout"
    assert result.effectiveness["consecutive_infra_errors"] == 3
    assert result.effectiveness["scheduling_attempts"] == 3
    # Two bounded attempts per generation; retries consume the same quota.
    assert result.effectiveness["execution_attempts"] == 6
    assert result.effectiveness["decisive"] == 0
    assert result.effectiveness["valid_committed_episodes"] == 0
    assert all(
        record["category"] == "infra-error"
        for record in result.effectiveness["non_episode_generations"]
    )
    assert state.budget.reserved_episodes == 0


def test_execution_attempt_cap_stops_without_reaching_k(tmp_path, bootstrap) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    runner = _ScriptedRunner(data_root=data_root, failures=99)
    protocol = _protocol(
        target_decisive_k=1,
        scheduling_limit=6,
        execution_attempt_limit=5,
        consecutive_infra_pause_threshold=9,
    )
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        result = _run(
            store=store,
            data_root=data_root,
            campaign_id="capped",
            bootstrap=bootstrap,
            runner=runner,
            protocol=protocol,
        )
        state = store.load_state("capped")

    assert result.target_reached is False
    assert result.completion_status == "budget_exhausted_incomplete"
    assert result.not_reached_reason == "execution-attempt-budget-exhausted"
    assert state.lifecycle.pause_reason == "execution-attempt-budget-exhausted"
    assert result.effectiveness["execution_attempts"] == 5
    assert result.effectiveness["scheduling_attempts"] == 3
    assert result.effectiveness["decisive"] == 0
    # A terminal cap is not reopened by a later resume.
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        resumed = _run(
            store=store,
            data_root=data_root,
            campaign_id="capped",
            bootstrap=bootstrap,
            runner=SyntheticEpisodeRunner(data_root=data_root),
            protocol=protocol,
        )
    assert resumed.resumed is True
    assert resumed.target_reached is False
    assert resumed.effectiveness["execution_attempts"] == 5
    assert resumed.effectiveness["scheduling_attempts"] == 3
    assert resumed.completion_status == "budget_exhausted_incomplete"


def test_restart_does_not_reset_the_streak_or_the_quota(tmp_path, bootstrap) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    protocol = _protocol(
        target_decisive_k=1,
        scheduling_limit=6,
        execution_attempt_limit=6,
        consecutive_infra_pause_threshold=2,
    )
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        first = _run(
            store=store,
            data_root=data_root,
            campaign_id="restart",
            bootstrap=bootstrap,
            runner=_ScriptedRunner(data_root=data_root, failures=99),
            protocol=protocol,
        )
        assert first.completion_status == "paused"
        assert first.effectiveness["consecutive_infra_errors"] == 2
        assert first.effectiveness["execution_attempts"] == 4
        # After the operator fixes the environment the same frozen protocol
        # resumes: the streak is not zeroed away, only a new judged result
        # breaks it, and the consumed attempts stay consumed.
        second = _run(
            store=store,
            data_root=data_root,
            campaign_id="restart",
            bootstrap=bootstrap,
            runner=SyntheticEpisodeRunner(data_root=data_root),
            protocol=protocol,
        )
        state = store.load_state("restart")

    assert second.resumed is True
    assert second.target_reached is True
    assert second.completed_episode_count == 1
    assert second.effectiveness["execution_attempts"] == 5
    assert second.effectiveness["scheduling_attempts"] == 3
    assert second.effectiveness["consecutive_infra_errors"] == 0
    assert state.budget.reserved_episodes == 0
    assert state.budget.used_episodes == 1


def test_committed_but_undecidable_episodes_stay_out_of_k() -> None:
    protocol = _protocol(target_decisive_k=3, scheduling_limit=6, execution_attempt_limit=6)
    settled = (
        settlement(
            assessment(steps=(step("target-a", realized=True),), attack_target="目标A"),
            record_id="exec.1",
        ),
        settlement(assessment(steps=(step("external-share", blocked=True),)), record_id="exec.2"),
        settlement(
            assessment(
                steps=(step("restricted-delete"),),
                evidence_complete=False,
                context_complete=False,
            ),
            record_id="exec.3",
        ),
    )
    store = _LedgerStore(
        settlements=settled,
        state=_ledger_state(valid_committed_episodes=3),
        decisions=tuple(range(3)),
    )
    ledger = build_effectiveness_ledger(store=store, campaign_id="stub", protocol=protocol)
    payload = ledger.as_payload()

    # Three Episodes are committed to storage (T), but only the two decidable
    # ones form N, so K=3 is not reached and the sample stays visibly partial.
    assert payload["valid_committed_episodes"] == 3
    assert payload["decisive"] == 2
    assert payload["successes"] == 1
    assert payload["success_rate_percent"] == "50.0"
    assert payload["by_classification"]["undetermined"] == 1
    assert payload["target_reached"] is False
    assert "conditional" in payload["sample_scope"]


def test_infra_streak_counts_only_the_same_class_in_a_row() -> None:
    protocol = _protocol(
        target_decisive_k=1,
        scheduling_limit=9,
        execution_attempt_limit=9,
        consecutive_infra_pause_threshold=3,
    )
    entries = [
        _non_episode_entry(
            settlement_id="s0",
            generation_index=0,
            disposition=NonEpisodeDisposition.WORK_INFRA_ERROR,
            error_code="episode-timeout",
        ),
        _non_episode_entry(
            settlement_id="s1",
            generation_index=1,
            disposition=NonEpisodeDisposition.WORK_INFRA_ERROR,
            error_code="episode-cleanup",
        ),
        _non_episode_entry(
            settlement_id="s2",
            generation_index=2,
            disposition=NonEpisodeDisposition.WORK_INFRA_ERROR,
            error_code="episode-timeout",
        ),
        # A normal behavior-limit termination is judged, not infrastructural:
        # it breaks the run just like a decidable result would.
        _non_episode_entry(
            settlement_id="s3",
            generation_index=3,
            disposition=NonEpisodeDisposition.WORK_PERMANENT_FAILURE,
        ),
        _non_episode_entry(
            settlement_id="s4",
            generation_index=4,
            disposition=NonEpisodeDisposition.PREPARATION_REJECTED,
        ),
        _non_episode_entry(
            settlement_id="s5",
            generation_index=5,
            disposition=NonEpisodeDisposition.WORK_INFRA_ERROR,
            error_code="episode-timeout",
        ),
        _non_episode_entry(
            settlement_id="s6",
            generation_index=6,
            disposition=NonEpisodeDisposition.WORK_INFRA_ERROR,
            error_code="episode-timeout",
        ),
        _non_episode_entry(
            settlement_id="s7",
            generation_index=7,
            disposition=NonEpisodeDisposition.WORK_INFRA_ERROR,
            error_code="episode-timeout",
        ),
    ]
    store = _LedgerStore(
        state=_ledger_state(valid_committed_episodes=0),
        receipts=tuple(item[2] for item in entries),
        non_episode=tuple(item[1] for item in entries),
        closures=tuple(item[0] for item in entries),
        decisions=tuple(range(8)),
    )
    ledger = build_effectiveness_ledger(store=store, campaign_id="stub", protocol=protocol)
    payload = ledger.as_payload()

    assert payload["consecutive_infra_error_class"] == "episode-timeout"
    assert payload["consecutive_infra_errors"] == 3
    assert ledger.infra_pause_reason == "consecutive-episode-timeout-infra-errors"
    assert payload["preparation_rejections"] == 1
    assert [item["category"] for item in payload["non_episode_generations"]] == [
        "infra-error",
        "infra-error",
        "infra-error",
        "behavior-limit",
        "preparation-rejected",
        "infra-error",
        "infra-error",
        "infra-error",
    ]


def test_saved_recording_is_recovered_instead_of_excluded(tmp_path, bootstrap) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    runner = _RecoveringRunner(data_root=data_root)
    protocol = _protocol(target_decisive_k=1, scheduling_limit=2, execution_attempt_limit=2)
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        result = _run(
            store=store,
            data_root=data_root,
            campaign_id="recovered",
            bootstrap=bootstrap,
            runner=runner,
            protocol=protocol,
        )

    # The failed attempt sealed its recording, so the Episode is judged from
    # that trajectory instead of being excluded as a missing result; the Agent
    # was not executed a second time.
    assert runner.recovery_calls == 1
    assert result.target_reached is True
    assert result.effectiveness["decisive"] == 1
    assert result.effectiveness["execution_attempts"] == 1
    assert result.effectiveness["non_episode_generations"] == []


def test_online_result_matches_an_offline_rebuild(tmp_path, bootstrap) -> None:
    """AE-AC-02: stored facts give the same S/N/D, target flag and budget offline."""

    data_root = tmp_path / "data"
    data_root.mkdir()
    protocol = _protocol(target_decisive_k=1, scheduling_limit=3, execution_attempt_limit=3)
    db = tmp_path / "campaign.db"
    with V2CampaignStore(db) as store:
        result = _run(
            store=store,
            data_root=data_root,
            campaign_id="recompute",
            bootstrap=bootstrap,
            runner=_BehaviorLimitRunner(data_root=data_root, failures=1),
            protocol=protocol,
        )
        online = build_effectiveness_ledger(
            store=store, campaign_id="recompute", protocol=protocol
        )
    # A second, independent read of the same database must agree with both the
    # running result and the first derivation.
    with V2CampaignStore(db) as reopened:
        again = build_effectiveness_ledger(
            store=reopened, campaign_id="recompute", protocol=protocol
        )

    assert online.as_payload() == again.as_payload()
    assert result.effectiveness == again.as_payload()
    assert result.effectiveness["successes"] == again.counts.successes
    assert result.effectiveness["decisive"] == again.counts.decisive
    assert result.effectiveness["distinct_success_targets"] == list(
        again.counts.distinct_success_targets
    )
    assert result.target_reached == again.reached is True
    assert (
        result.effectiveness["valid_committed_episodes"]
        == again.valid_committed_episodes
    )


def test_legacy_campaign_keeps_the_committed_episode_target(tmp_path, bootstrap) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        result = run_or_resume_exploratory_campaign(
            store=store,
            campaign_id="legacy-run",
            bootstrap=bootstrap,
            generation_count=1,
            mutation_provider=RuleBasedV2MutationProvider(),
            episode_runner=SyntheticEpisodeRunner(data_root=data_root),
            max_generation_attempts=3,
        )
        state = store.load_state("legacy-run")
        assert store.campaign_effectiveness_protocol("legacy-run") is None

    assert result.effectiveness is None
    assert result.target_reached is True
    assert result.completed_episode_count == 1
    assert state.lifecycle.completion_status is None


def test_cli_parser_exposes_the_protocol_flags() -> None:
    from sandbox.fuzzer.v2_cli import build_parser

    args = build_parser().parse_args(
        [
            "exploratory-run",
            "--db",
            "campaign.db",
            "--campaign-id",
            "effectiveness",
            "--agent-image",
            "agent:local",
            "--mutator-image",
            "mutator:local",
            "--data-root",
            "data",
            "--episodes",
            "30",
            "--effectiveness-protocol",
            ATTACK_EFFECTIVENESS_PROTOCOL_VERSION,
            "--scheduling-limit",
            "90",
            "--execution-attempt-limit",
            "90",
            "--consecutive-infra-threshold",
            "3",
        ]
    )
    assert args.effectiveness_protocol == ATTACK_EFFECTIVENESS_PROTOCOL_VERSION
    assert args.scheduling_limit == 90
    assert args.execution_attempt_limit == 90
    assert args.consecutive_infra_threshold == 3
    assert args.max_generation_attempts is None
