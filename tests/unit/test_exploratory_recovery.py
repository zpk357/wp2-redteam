from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from sandbox.errors import InfrastructureError, RuntimeTimeoutError
from sandbox.fuzzer.v2_bootstrap import build_exploratory_bootstrap
from sandbox.fuzzer.v2_campaign_store import V2CampaignStore
from sandbox.fuzzer.v2_orchestrator import decide_next_generation
from sandbox.fuzzer.v2_real_runtime import _episode_failure_receipt, _RealGenerationDriver
from sandbox.fuzzer.v2_runtime import run_or_resume_campaign
from sandbox.fuzzer.v2_work import (
    BudgetReservation,
    CandidateWork,
    CandidateWorkState,
    seal_work_contract,
)
from sandbox.mutation.v2_candidate import is_mutator_refusal
from sandbox.mutation.v2_provider import RuleBasedV2MutationProvider
from sandbox.replay.exceptions import ReplayPreparationError
from sandbox.scheduler.docker_scheduler import DockerSandboxScheduler


@pytest.fixture(scope="module")
def bootstrap():
    return build_exploratory_bootstrap(episode_limit=10)


@pytest.mark.parametrize(
    "text,refusal",
    [
        ("无法生成包含错误测试事实或用于传播的文件内容。", True),
        ("I cannot generate that content.", True),
        ("Please delete the specified restricted test file.", False),
        ("请删除指定的受限测试文件。", False),
    ],
)
def test_mutator_refusal_is_not_a_task(text, refusal):
    assert is_mutator_refusal(text) is refusal


@pytest.mark.parametrize("failure", [
    "recorded execution failed (langgraph_context_exhausted): context full",
    "recorded execution failed (langgraph_empty_model_response): no output after recovery",
    "recorded execution failed (tool_call_budget_exceeded): tool call budget exceeded: 24",
    "recorded execution failed (agent_no_submit): "
    "agent stopped without a valid submit (limit=repeated_rejected_control_call)",
    "recorded execution failed (agent_no_submit): "
    "agent stopped without a valid submit (limit=rejected_control_recovery)",
])
def test_behavior_limit_closes_failed_work_and_continues(tmp_path, bootstrap, failure):
    runner = SimpleNamespace(
        execute=AsyncMock(
            side_effect=ReplayPreparationError(
                -32108,
                failure,
            )
        )
    )
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        driver = _RealGenerationDriver(
            store=store,
            bootstrap=bootstrap,
            mutation_provider=RuleBasedV2MutationProvider(),
            episode_runner=runner,
        )
        result = run_or_resume_campaign(
            store=store,
            campaign_id="limited",
            initial_state=bootstrap.initial_state,
            generation_count=1,
            driver=driver,
            exploratory=True,
            max_generation_attempts=2,
        )
        state = store.load_state("limited")
        assert runner.execute.await_count == 2
        assert result.completed_episode_count == 0
        assert result.attempted_generation_count == 2
        assert state.budget.reserved_episodes == 0
        assert state.budget.used_episodes == 0
        assert state.seed_catalog == bootstrap.initial_state.seed_catalog
        rows = store._db.execute("SELECT work_id FROM candidate_work").fetchall()
        assert len(rows) == 2
        assert all(store.load_work(row[0]).state is CandidateWorkState.FAILED for row in rows)


def test_exited_container_fails_without_sleep(monkeypatch):
    scheduler = object.__new__(DockerSandboxScheduler)
    scheduler.config = SimpleNamespace(startup_timeout_seconds=600)
    scheduler._container_health_status = Mock(side_effect=InfrastructureError("exited: bad config"))
    sleep = Mock(side_effect=AssertionError("must not wait for a dead container"))
    monkeypatch.setattr(asyncio, "sleep", sleep)
    with pytest.raises(InfrastructureError, match="bad config"):
        asyncio.run(scheduler.wait_until_ready(SimpleNamespace(container_id="dead")))
        sleep.assert_not_called()


def test_pause_preserves_seed_catalog_and_risk_progress(tmp_path, bootstrap):
    from sandbox.fuzzer.v2_campaign_state import add_promoted_seed_to_catalog, settle_risk_progress
    from sandbox.fuzzer.v2_seed_pools import derive_promoted_seed

    parent = bootstrap.initial_state.seed_catalog.pools[0].root_seeds[0]
    child = derive_promoted_seed(
        parent=parent, rewritten_text="Please proceed: " + parent.base_text,
        operator_receipt_ids=("operator-receipt.pause",),
    )
    initial = add_promoted_seed_to_catalog(bootstrap.initial_state, seed=child)
    initial = settle_risk_progress(
        initial, risk_type=parent.risk_type, attempted=True, realized=True
    )
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        store.create_campaign(campaign_id="pause", initial_state=initial)
        before = store.load_state("pause")
        paused = store.pause_campaign("pause", reason="temporary")
        assert paused.seed_catalog == before.seed_catalog
        assert paused.risk_progress == before.risk_progress
        resumed = store.resume_paused_campaign("pause", reason="fixed")
        assert resumed.seed_catalog == before.seed_catalog
        assert resumed.risk_progress == before.risk_progress


def test_resume_renews_exhausted_attempts_without_losing_receipts(tmp_path, bootstrap):
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        store.create_campaign(campaign_id="recovery", initial_state=bootstrap.initial_state)
        decision = decide_next_generation(
            campaign_id="recovery", state=bootstrap.initial_state, latest_feedback=None
        )
        allocation = decision.allocation
        store.put_allocation(campaign_id="recovery", allocation=allocation)
        work = seal_work_contract(
            CandidateWork,
            {
                "work_id": "work.recovery",
                "campaign_id": "recovery",
                "generation_allocation_id": allocation.generation_allocation_id,
                "generation_allocation_digest": allocation.allocation_digest,
                "comparison_context_digest": "sha256:" + "0" * 64,
                "baseline_snapshot_digest": "sha256:" + "0" * 64,
                "budget_reservation": BudgetReservation(),
            },
            "work_digest",
        )
        store.put_work(work)
        for attempt in range(1, 7):
            store.transition_work(work.work_id, state=CandidateWorkState.EXECUTING)
            store.seal_attempt(
                _episode_failure_receipt(
                    work_id=work.work_id,
                    attempt_number=attempt,
                    error=RuntimeTimeoutError("failed"),
                    elapsed_ms=5,
                    retryable=True,
                )
            )
            store.transition_work(work.work_id, state=CandidateWorkState.AMBIGUOUS)
            before = store.receipts_for_work(work.work_id)
            store.resume_ambiguous_work(
                "recovery",
                work_id=work.work_id,
                reason="fixed-code",
                isolation_confirmed=True,
                additional_attempts=2 if attempt % 2 == 0 else 0,
            )
            assert store.receipts_for_work(work.work_id) == before
            assert store.load_work(work.work_id).state is CandidateWorkState.ALLOCATED
        assert store.load_work(work.work_id).max_attempts == 8
        executing = store.transition_work(work.work_id, state=CandidateWorkState.EXECUTING)
        driver = _RealGenerationDriver(
            store=store, bootstrap=bootstrap, mutation_provider=object(), episode_runner=object()
        )
        recovered = driver._recover_interrupted_work(executing)
        assert recovered.state is CandidateWorkState.AMBIGUOUS
        assert len(store.receipts_for_work(work.work_id)) == 7
        assert "Controller stopped" in store.receipts_for_work(work.work_id)[-1].bounded_summary
        store.resume_ambiguous_work(
            "recovery", work_id=work.work_id, reason="restart", isolation_confirmed=True
        )
        executing = store.transition_work(work.work_id, state=CandidateWorkState.EXECUTING)
        store.seal_attempt(
            _episode_failure_receipt(
                work_id=work.work_id,
                attempt_number=8,
                error=RuntimeTimeoutError("saved before process exit"),
                elapsed_ms=5,
                retryable=True,
            )
        )
        driver._recover_interrupted_work(executing)
        assert len(store.receipts_for_work(work.work_id)) == 8


def test_interrupted_mutation_is_retried_with_same_reservation(tmp_path, bootstrap, monkeypatch):
    provider = SimpleNamespace(provider_id="provider-docker-ollama-v2", cleanup_interrupted=Mock())
    runner = SimpleNamespace(cleanup_interrupted=Mock())
    prepare = Mock(side_effect=RuntimeError("mutation test failure"))
    monkeypatch.setattr("sandbox.fuzzer.v2_real_runtime.prepare_candidate", prepare)
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        store.create_campaign(campaign_id="recovery", initial_state=bootstrap.initial_state)
        decision = decide_next_generation(
            campaign_id="recovery", state=bootstrap.initial_state, latest_feedback=None
        )
        store.put_generation_decision(decision)
        driver = _RealGenerationDriver(
            store=store, bootstrap=bootstrap, mutation_provider=provider, episode_runner=runner
        )
        with pytest.raises(RuntimeError, match="mutation test failure"):
            driver.advance(
                campaign_id="recovery",
                decision=decision,
                state=bootstrap.initial_state,
                previous_feedback=None,
            )
        reservation = store.load_mutation_reservation_for_allocation(
            "recovery", decision.allocation.generation_allocation_id
        )
        state = store.resume_paused_campaign("recovery", reason="fixed-code")
        with pytest.raises(RuntimeError, match="mutation test failure"):
            driver.resume_incomplete(
                campaign_id="recovery", decision=decision, state=state, previous_feedback=None
            )
        assert prepare.call_count == 2
        assert (
            store.load_mutation_reservation_for_allocation(
                "recovery", decision.allocation.generation_allocation_id
            )
            == reservation
        )
        provider.cleanup_interrupted.assert_called_once_with("recovery")
        runner.cleanup_interrupted.assert_called_once_with("recovery")


def test_driver_exception_is_in_result_and_log(tmp_path, bootstrap, caplog):
    driver = SimpleNamespace(advance=Mock(side_effect=ValueError("root cause example")))
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        result = run_or_resume_campaign(
            store=store,
            campaign_id="diagnostic",
            initial_state=bootstrap.initial_state,
            generation_count=10,
            driver=driver,
        )
    assert result.completion_status == "paused"
    assert "ValueError: root cause example" in result.error_traceback
    assert "root cause example" in caplog.text


def test_cli_saves_unhandled_controller_error(tmp_path, monkeypatch):
    from sandbox.fuzzer import v2_cli

    monkeypatch.setattr(v2_cli, "_run_exploratory", Mock(side_effect=ValueError("save failure")))
    with pytest.raises(ValueError, match="save failure"):
        v2_cli.main(
            [
                "exploratory-run",
                "--db",
                str(tmp_path / "campaign.db"),
                "--campaign-id",
                "diagnostic",
                "--data-root",
                str(tmp_path),
                "--agent-image",
                "agent:debug",
                "--mutator-image",
                "mutator:debug",
            ]
        )
    diagnostic = (tmp_path / "failures" / "controller-error.txt").read_text(encoding="utf-8")
    assert "Traceback" in diagnostic
    assert "ValueError: save failure" in diagnostic


@pytest.mark.parametrize("sealed", [False, True])
def test_settlement_retry_does_not_seal_success_twice(sealed, monkeypatch):
    from sandbox.fuzzer.v2_real_runtime import _successful_receipt

    receipt = _successful_receipt(
        work_id="work.pending",
        attempt_number=1,
        manifest_digest="sha256:" + "0" * 64,
        agent_tokens=7,
        elapsed_ms=10,
    )
    store = Mock()
    store.receipts_for_work.return_value = (receipt,)
    driver = _RealGenerationDriver(
        store=store, bootstrap=object(), mutation_provider=object(), episode_runner=Mock()
    )
    episode = SimpleNamespace(
        scenario_case=SimpleNamespace(case_id="case"),
        manifest=SimpleNamespace(manifest_digest=receipt.response_digest),
        agent_tokens=7,
        elapsed_ms=10,
        oracle=SimpleNamespace(trace_digest="trace", oracle_result=object()),
    )
    monkeypatch.setattr(
        "sandbox.fuzzer.v2_real_runtime.build_execution_closure_from_oracle",
        Mock(side_effect=ValueError("settlement fault injection")),
    )
    with pytest.raises(ValueError, match="settlement fault injection"):
        driver._settle_episode(
            work=SimpleNamespace(
                work_id="work.pending",
                state=CandidateWorkState.SEALED if sealed else CandidateWorkState.EXECUTING,
            ),
            attempt_number=1,
            episode=episode,
            execution_id="exec",
            campaign_id="demo",
            decision=None,
            preparation=SimpleNamespace(
                materialized_candidate=SimpleNamespace(scenario_case_id="case"),
                parsed_candidate=object(),
            ),
            seed=None,
            formal_parent_seed=None,
            plan=None,
            brief=None,
            episode_reserved_state=None,
            execution=None,
            agent_reservation=None,
            previous_feedback=None,
        )
    store.seal_attempt.assert_not_called()
    driver.episode_runner.execute.assert_not_called()


def test_saved_replay_is_located_by_oracle_execution(tmp_path):
    import json

    from sandbox.fuzzer.v2_real_episode import DockerOfficeV2EpisodeRunner

    directory = tmp_path / "replay-example"
    directory.mkdir()
    (directory / "manifest.json").write_text("{}")
    manifest = SimpleNamespace(office_v2_oracle=object())
    manifests = SimpleNamespace(root=tmp_path, load=Mock(return_value=manifest))
    artifacts = Mock()
    artifacts.read_bytes.return_value = json.dumps({"execution_id": "saved-exec"}).encode()
    runner = DockerOfficeV2EpisodeRunner(
        replay_engine=SimpleNamespace(manifest_store=manifests),
        artifact_store=artifacts,
        model_name="qwen",
    )
    assert runner._saved_recording("saved-exec") is manifest
    assert runner._saved_recording("another-exec") is None
