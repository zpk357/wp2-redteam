from __future__ import annotations

import pytest

from sandbox.coverage.v2_input import v2_coverage_input_from_recording
from sandbox.fuzzer.v2_campaign_store import V2CampaignStore
from sandbox.fuzzer.v2_cli import build_parser
from sandbox.fuzzer.v2_real_episode import (
    OfficeV2RealEpisodeResult,
    OfficeV2RecordedOracleArtifact,
    _recorded_agent_tokens,
    source_attack_case,
)
from sandbox.fuzzer.v2_real_runtime import (
    RealCampaignBootstrap,
    run_or_resume_real_campaign,
)
from sandbox.fuzzer.v2_report import build_v2_campaign_report
from sandbox.fuzzer.v2_stage6_evidence import audit_two_generation_gate
from sandbox.mutation.v2_provider import (
    ProviderFailureClass,
    RuleBasedV2MutationProvider,
    V2ProviderFailure,
    seal_failed_provider_attempt,
)
from sandbox.protocol import AgentRuntimeKind
from sandbox.replay.canonical import canonical_json_bytes
from sandbox.replay.digests import sha256_digest
from sandbox.replay.manifest import seal_manifest
from sandbox.replay.models import (
    RECORDED_MODEL_TOKEN_USAGE_KEY,
    RecordedModelDecision,
)
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.fork import (
    infer_office_v2_compatibility_purpose,
    rematerialize_office_v2_scenario_text,
)
from sandbox.scenarios.office_v2.oracle import evaluate_scenario_oracle
from sandbox.scenarios.office_v2.oracle_evidence import (
    build_oracle_evidence_bundle,
    build_termination_fact,
)
from tests.unit.test_office_v2_coverage_input import _manifest
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


class MismatchedRuntimeRunner:
    producer_runtime_identity = {
        "producer_runtime_kind": AgentRuntimeKind.LANGGRAPH.value,
        "producer_runtime_version": "wrong",
        "producer_runtime_composition_digest": "sha256:" + "b" * 64,
    }

    async def execute(self, **_kwargs):
        raise AssertionError("identity mismatch must fail before Episode execution")


class MatchingRuntimeRunner:
    producer_runtime_identity = {
        "producer_runtime_kind": AgentRuntimeKind.DEEPSEEK_HARNESS.value,
        "producer_runtime_version": "deepseek-harness-h4-v1",
        "producer_runtime_composition_digest": "sha256:" + "c" * 64,
    }

    def __init__(self) -> None:
        self.called = False

    async def execute(self, **_kwargs):
        self.called = True
        raise RuntimeError("matching runner reached")


class DeterministicHarnessEpisodeRunner:
    producer_runtime_identity = {
        "producer_runtime_kind": AgentRuntimeKind.DEEPSEEK_HARNESS.value,
        "producer_runtime_version": "deepseek-harness-h4-v1",
        "producer_runtime_composition_digest": "sha256:" + "c" * 64,
    }

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(
        self,
        *,
        source_scenario_case_id,
        generated_content,
        execution_id,
        seed,
        run_context,
    ):
        del run_context
        source = source_attack_case(source_scenario_case_id)
        world = load_canonical_world()
        materialized = rematerialize_office_v2_scenario_text(
            source_case=source,
            canonical_world=world,
            generated_content=generated_content,
            purpose=infer_office_v2_compatibility_purpose(source, world),
            seed=seed,
        )
        state = materialized.initial_state
        state_digest = state.canonical_digest()
        answer_digest = sha256_digest({"execution": execution_id, "answer": "done"})
        termination = build_termination_fact(
            evidence_id=f"termination.{execution_id}",
            sequence=0,
            reason="submit",
            submitted=True,
            output_digest=answer_digest,
        )
        bundle = build_oracle_evidence_bundle(
            scenario_case=materialized.scenario_case,
            initialization_transition=materialized.initialization_transition,
            invocations=(),
            results=(),
            interaction_facts=(),
            timeline=(),
            termination=termination,
            final_state_digest=state_digest,
            recording_digest=sha256_digest({"recording": execution_id}),
        )
        oracle_result = evaluate_scenario_oracle(
            bundle=bundle,
            scenario_case=materialized.scenario_case,
        )
        oracle_payload = {
            "artifact_version": "office-v2-live-oracle-artifact-v1",
            "execution_id": execution_id,
            "trace_digest": sha256_digest({"trace": execution_id}),
            "trusted_facts_digest": sha256_digest({"facts": bundle.bundle_digest}),
            "evidence_bundle": bundle,
            "oracle_result": oracle_result,
        }
        draft_oracle = OfficeV2RecordedOracleArtifact.model_construct(
            **oracle_payload,
            artifact_digest="sha256:" + "0" * 64,
        )
        oracle = OfficeV2RecordedOracleArtifact(
            **oracle_payload,
            artifact_digest=sha256_digest(draft_oracle.digest_payload()),
        )
        session = {
            "schema_version": "office-v2.0",
            "snapshot_version": "office-v2-session-snapshot-v1",
            "execution_envelope_digest": sha256_digest(
                {"execution-envelope": execution_id}
            ),
            "episode_id": execution_id,
            "base_world_digest": materialized.scenario_case.base_world_digest,
            "initial_state_digest": state_digest,
            "state": state.model_dump(mode="json", exclude_none=False),
            "history": [],
            "state_digest": state_digest,
        }
        session["snapshot_digest"] = sha256_digest(session)
        recording_state = {
            "schema_version": "office-v2.0",
            "recording_state_version": "office-v2-recording-state-v1",
            "session": session,
            "tool_invocations": [],
            "tool_results": [],
            "interaction_events": [],
            "pending_clarification_request_ids": [],
        }
        recording_state["recording_state_digest"] = sha256_digest(recording_state)
        recording_state_payload = canonical_json_bytes(recording_state)
        oracle_artifact_payload = canonical_json_bytes(oracle)
        manifest = _manifest(
            bundle,
            recording_state_payload=recording_state_payload,
            oracle_artifact_payload=oracle_artifact_payload,
        )
        manifest = seal_manifest(
            manifest.model_copy(
                update={
                    "replay_id": f"replay.{execution_id}",
                    "trajectory_id": f"trajectory.{execution_id}",
                    "metadata": dict(self.producer_runtime_identity),
                    "manifest_digest": None,
                }
            )
        )
        coverage_input = v2_coverage_input_from_recording(
            manifest,
            oracle_artifact_payload=oracle_artifact_payload,
            recording_state_payload=recording_state_payload,
            container_removed=True,
        )
        self.calls.append(execution_id)
        return OfficeV2RealEpisodeResult(
            scenario_case=materialized.scenario_case,
            manifest=manifest,
            oracle=oracle,
            coverage_input=coverage_input,
            agent_tokens=17,
            elapsed_ms=5,
        )


class SimulatedProcessInterruption(BaseException):
    pass


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


def test_real_campaign_runtime_selection_defaults_and_is_explicit() -> None:
    parser = build_parser()
    common = [
        "--db",
        "campaign.db",
        "--campaign-id",
        "campaign-runtime-selection",
        "--bootstrap",
        "bootstrap.json",
        "--model-lock",
        "model-lock.json",
        "--agent-image",
        "agent:test",
        "--mutator-image",
        "mutator:test",
        "--data-root",
        "data",
        "--generations",
        "3",
    ]
    default = parser.parse_args(["real-run", *common])
    harness = parser.parse_args(
        ["real-run", *common, "--agent-runtime", "deepseek_harness"]
    )

    assert default.agent_runtime == AgentRuntimeKind.LANGGRAPH.value
    assert harness.agent_runtime == AgentRuntimeKind.DEEPSEEK_HARNESS.value


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
            runtime_identity_digest="sha256:" + "a" * 64,
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


def test_producer_bound_campaign_rejects_mismatched_episode_runner(tmp_path) -> None:
    _, state = loop_fixture()
    with V2CampaignStore(tmp_path / "runtime-mismatch.sqlite3") as store:
        with pytest.raises(RuntimeError, match="differs from Campaign"):
            run_or_resume_real_campaign(
                store=store,
                campaign_id=CAMPAIGN_ID,
                bootstrap=RealCampaignBootstrap(
                    initial_state=state,
                    model_identity_digest="sha256:" + "a" * 64,
                ),
                generation_count=1,
                mutation_provider=RuleBasedV2MutationProvider(),
                episode_runner=MismatchedRuntimeRunner(),
                producer_runtime_kind=AgentRuntimeKind.DEEPSEEK_HARNESS,
                producer_runtime_version="deepseek-harness-h4-v1",
                producer_runtime_composition_digest="sha256:" + "c" * 64,
            )
        paused = store.load_state(CAMPAIGN_ID)
        assert paused.lifecycle.completion_status == "paused"
        assert paused.lifecycle.pause_reason == "producer-runtime-identity-mismatch"

    matching = MatchingRuntimeRunner()
    with V2CampaignStore(tmp_path / "runtime-match.sqlite3") as store:
        with pytest.raises(RuntimeError, match="matching runner reached"):
            run_or_resume_real_campaign(
                store=store,
                campaign_id=f"{CAMPAIGN_ID}.matching",
                bootstrap=RealCampaignBootstrap(
                    initial_state=state,
                    model_identity_digest="sha256:" + "a" * 64,
                ),
                generation_count=1,
                mutation_provider=RuleBasedV2MutationProvider(),
                episode_runner=matching,
                producer_runtime_kind=AgentRuntimeKind.DEEPSEEK_HARNESS,
                producer_runtime_version="deepseek-harness-h4-v1",
                producer_runtime_composition_digest="sha256:" + "c" * 64,
            )
        assert matching.called is True
        paused = store.load_state(f"{CAMPAIGN_ID}.matching")
        assert paused.lifecycle.pause_reason == "episode-runner-unknown-failure"


def test_harness_three_generation_loop_resumes_without_duplicate_settlement(
    tmp_path, monkeypatch
) -> None:
    _, state = loop_fixture()
    campaign_id = f"{CAMPAIGN_ID}.harness-loop"
    runner = DeterministicHarnessEpisodeRunner()
    path = tmp_path / "harness-loop.sqlite3"
    with V2CampaignStore(path) as store:
        original_commit = store.commit_settlement
        interrupted = False

        def commit_then_interrupt(**kwargs):
            nonlocal interrupted
            committed = original_commit(**kwargs)
            if not interrupted:
                interrupted = True
                raise SimulatedProcessInterruption("post-commit process exit")
            return committed

        monkeypatch.setattr(store, "commit_settlement", commit_then_interrupt)
        with pytest.raises(SimulatedProcessInterruption):
            run_or_resume_real_campaign(
                store=store,
                campaign_id=campaign_id,
                bootstrap=RealCampaignBootstrap(
                    initial_state=state,
                    model_identity_digest="sha256:" + "a" * 64,
                ),
                generation_count=3,
                mutation_provider=RuleBasedV2MutationProvider(),
                episode_runner=runner,
                producer_runtime_kind=AgentRuntimeKind.DEEPSEEK_HARNESS,
                producer_runtime_version="deepseek-harness-h4-v1",
                producer_runtime_composition_digest="sha256:" + "c" * 64,
            )
        interrupted_state = store.load_state(campaign_id)

    assert interrupted_state.lifecycle.counters.generation_index == 1
    assert interrupted_state.lifecycle.counters.valid_committed_episodes == 1
    assert len(runner.calls) == 1

    with V2CampaignStore(path) as store:
        result = run_or_resume_real_campaign(
            store=store,
            campaign_id=campaign_id,
            bootstrap=RealCampaignBootstrap(
                initial_state=state,
                model_identity_digest="sha256:" + "a" * 64,
            ),
            generation_count=3,
            mutation_provider=RuleBasedV2MutationProvider(),
            episode_runner=runner,
            producer_runtime_kind=AgentRuntimeKind.DEEPSEEK_HARNESS,
            producer_runtime_version="deepseek-harness-h4-v1",
            producer_runtime_composition_digest="sha256:" + "c" * 64,
        )
        next_state = store.load_state(campaign_id)
        report = build_v2_campaign_report(store=store, campaign_id=campaign_id)
        settlement_count = store._db.execute(
            "SELECT COUNT(*) FROM settlement"
        ).fetchone()[0]
        receipt_count = store._db.execute(
            "SELECT COUNT(*) FROM attempt_receipt"
        ).fetchone()[0]

    assert result.resumed is True
    assert result.completed_generation_count == 3
    assert len(runner.calls) == 3
    assert next_state.lifecycle.counters.valid_committed_episodes == 3
    assert len(next_state.corpus.execution_records) == (
        len(state.corpus.execution_records) + 3
    )
    assert next_state.budget.consumed.agent_tokens == (
        state.budget.consumed.agent_tokens + 51
    )
    assert settlement_count == 3
    assert receipt_count == 3
    assert len(report["decisions"]) == 3
    assert len(report["feedback"]) == 3
    assert report["decisions"][1]["input_feedback_digest"] == (
        report["feedback"][0]["feedback_digest"]
    )
    assert report["feedback"][1]["previous_feedback_digest"] == (
        report["feedback"][0]["feedback_digest"]
    )
    assert report["decisions"][2]["input_feedback_digest"] == (
        report["feedback"][1]["feedback_digest"]
    )


def test_real_runtime_seals_unknown_episode_failure_and_pauses(tmp_path) -> None:
    _, state = loop_fixture()
    path = tmp_path / "unknown-episode.sqlite3"
    with V2CampaignStore(path) as store:
        with pytest.raises(RuntimeError, match="simulated unclassified runner failure"):
            run_or_resume_real_campaign(
                store=store,
                campaign_id=CAMPAIGN_ID,
                bootstrap=RealCampaignBootstrap(
                    initial_state=state,
                    model_identity_digest="sha256:" + "a" * 64,
                ),
                generation_count=2,
                mutation_provider=RuleBasedV2MutationProvider(),
                episode_runner=RaisingEpisodeRunner(),
                runtime_identity_digest="sha256:" + "a" * 64,
            )
        next_state = store.load_state(CAMPAIGN_ID)
        work = store._db.execute(
            "SELECT work_id FROM candidate_work WHERE campaign_id=?",
            (CAMPAIGN_ID,),
        ).fetchone()
        receipts = store.receipts_for_work(work["work_id"])

        assert next_state.lifecycle.counters.generation_index == 0
        assert next_state.lifecycle.completion_status == "paused"
        assert next_state.budget.reserved_episodes == 1
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
                runtime_identity_digest="sha256:" + "a" * 64,
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
            runtime_identity_digest="sha256:" + "a" * 64,
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
                runtime_identity_digest="sha256:" + "a" * 64,
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
            runtime_identity_digest="sha256:" + "a" * 64,
        )
        paused = reopened.load_state(CAMPAIGN_ID)

    assert provider.calls == 1
    assert result.completed_generation_count == 0
    assert paused.lifecycle.completion_status == "paused"
    assert paused.lifecycle.pause_reason == (
        "ambiguous-provider-window-before-preparation-seal"
    )
