"""`L03-1`: a locally persisted, multi-generation, failure-containing sample.

This drives the real `_RealGenerationDriver` with a synthetic episode runner, so
reservation, mutation preparation, execution handoff, settlement and corpus
updates all run through production code, and `commit_settlement` really writes
`CandidateSettlement` rows into SQLite. Scoring then reads those rows back and
rebuilds each Episode's coverage input from the recording on disk.

The Agent behaviour is synthetic on purpose (no Docker, no model): this verifies
the measurement chain, not Agent behaviour.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agent_image.app.office_v2_session import OfficeV2LiveOracleArtifact
from sandbox.coverage.v2_input import v2_coverage_input_from_recording
from sandbox.fuzzer.v2_bootstrap import build_exploratory_bootstrap
from sandbox.fuzzer.v2_campaign_store import V2CampaignStore
from sandbox.fuzzer.v2_comparison_report import build_v2_comparison_report
from sandbox.fuzzer.v2_orchestrator import decide_next_generation
from sandbox.fuzzer.v2_real_episode import (
    OfficeV2EpisodeResult,
    OfficeV2RecordedOracleArtifact,
    source_attack_materialization_context,
)
from sandbox.fuzzer.v2_real_runtime import _RealGenerationDriver
from sandbox.fuzzer.v2_scoring import COMPLETE, score_campaign
from sandbox.fuzzer.v2_strategy import CampaignStrategy
from sandbox.mutation.v2_provider import RuleBasedV2MutationProvider
from sandbox.protocol import ToolReplayMode, TraceEvent
from sandbox.replay.artifact_store import ArtifactStore
from sandbox.replay.canonical import canonical_json_bytes
from sandbox.replay.digests import sha256_digest
from sandbox.replay.manifest import ManifestStore, seal_manifest
from sandbox.replay.models import ReplayManifest
from sandbox.scenarios.office_v2.agent_api import project_office_v2_tool_result
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.fork import rematerialize_office_v2_direct_task_text
from sandbox.scenarios.office_v2.oracle import evaluate_scenario_oracle
from sandbox.scenarios.office_v2.oracle_evidence import build_termination_fact
from sandbox.scenarios.office_v2.oracle_trace import build_oracle_evidence_from_trace
from sandbox.scenarios.office_v2.tools.contracts import (
    OfficeToolInvocation,
    ToolResultStatus,
    build_tool_result,
)

NOW = datetime(2026, 9, 19, tzinfo=UTC)
GUIDED = "sample.guided"
GENERATIONS = 4

# One proven read-only tool, called a varying number of times per generation, so
# each window length grows exactly once and then repeats:
#   generation 0 -> one call   : +1 unigram
#   generation 1 -> two calls  : +1 bigram
#   generation 2 -> two calls  : repeat, nothing new
#   generation 3 -> three calls: +1 trigram
CALL_PLANS = (1, 2, 2, 3)
TOOL_NAME = "search_drive_files"


def _short(value: str) -> str:
    return sha256_digest({"short": value})[7:23]


def _benign_exchanges(case, initial_state, tools: int, execution_id: str):
    """One read-only call repeated `tools` times, leaving the state unchanged."""

    state_digest = initial_state.canonical_digest()
    invocations = []
    results = []
    for index in range(tools):
        arguments = {"query": f"synthetic-{index}"}
        invocation = OfficeToolInvocation(
            invocation_id=f"invocation.{_short(execution_id)}.{index}",
            sequence=index,
            tool_name=TOOL_NAME,
            actor_id=case.actor.actor_id,
            task_id=case.task.task_id,
            logical_time=case.actor.logical_time,
            arguments=arguments,
            arguments_digest=sha256_digest(arguments),
            before_state_digest=state_digest,
        )
        results.append(
            build_tool_result(
                invocation=invocation,
                status=ToolResultStatus.SUCCEEDED,
                visible_output={"items": [], "has_more": False, "next_page_token": None},
            )
        )
        invocations.append(invocation)
    return tuple(invocations), tuple(results)


def _seal_recording(
    *,
    data_root: Path,
    case,
    initial_state,
    initial_transition,
    execution_id: str,
    tools: int | None = None,
    invocations=None,
    results=None,
    final_state=None,
) -> tuple[ReplayManifest, object, OfficeV2RecordedOracleArtifact]:
    """Build the trace, seal the recording, and rebuild its coverage input.

    Real exchanges from the tool runtime may be supplied; otherwise one read-only
    call is repeated `tools` times.
    """

    answer_digest = sha256_digest("synthetic-submit")
    if invocations is None:
        invocations, results = _benign_exchanges(
            case, initial_state, tools, execution_id
        )
    events = [
        TraceEvent(
            execution_id=execution_id,
            sequence=0,
            timestamp=NOW,
            event_type="model_end",
            source="trace.react",
            data={"turn": 1},
        )
    ]
    sequence = 1
    for index, (invocation, result) in enumerate(zip(invocations, results, strict=True)):
        visible = project_office_v2_tool_result(result).model_visible_payload()
        call_id = f"call.{index:03d}"
        events.append(
            TraceEvent(
                execution_id=execution_id,
                sequence=sequence,
                timestamp=NOW,
                event_type="tool_call",
                source="controlled_tools",
                data={
                    "call_id": call_id,
                    "call_index": index,
                    "name": invocation.tool_name,
                    "arguments": dict(invocation.arguments),
                },
                logical_time=index + 1,
                input_digest=invocation.arguments_digest,
            )
        )
        events.append(
            TraceEvent(
                execution_id=execution_id,
                sequence=sequence + 1,
                timestamp=NOW,
                event_type="tool_result",
                source="controlled_tools",
                data={
                    **visible,
                    "call_id": call_id,
                    "call_index": index,
                    "name": invocation.tool_name,
                },
                logical_time=index + 1,
                output_digest=sha256_digest(visible),
            )
        )
        sequence += 2
    events.append(
        TraceEvent(
            execution_id=execution_id,
            sequence=sequence,
            timestamp=NOW,
            event_type="agent_submit",
            source="trace.react",
            data={
                "call_id": "submit.001",
                "call_index": 0,
                "accepted": True,
                "answer_digest": answer_digest,
            },
            logical_time=len(invocations) + 1,
        )
    )
    termination = build_termination_fact(
        evidence_id=f"termination.{_short(execution_id)}",
        # The termination fact must follow every completed tool exchange.
        sequence=len(invocations),
        reason="submit",
        submitted=True,
        output_digest=answer_digest,
    )
    bundle = build_oracle_evidence_from_trace(
        scenario_case=case,
        initialization_transition=initial_transition,
        trace_events=tuple(events),
        invocations=tuple(invocations),
        results=tuple(results),
        interaction_facts=(),
        termination=termination,
        final_state_digest=results[-1].after_state_digest,
        recording_digest=sha256_digest({"recording": execution_id}),
    )
    oracle_result = evaluate_scenario_oracle(bundle=bundle, scenario_case=case)

    live_payload = {
        "artifact_version": "office-v2-live-oracle-artifact-v1",
        "execution_id": execution_id,
        "trace_digest": sha256_digest({"trace": execution_id}),
        "trusted_facts_digest": sha256_digest({"facts": bundle.bundle_digest}),
        "evidence_bundle": bundle,
        "oracle_result": oracle_result,
    }
    live_draft = OfficeV2LiveOracleArtifact.model_construct(
        **live_payload, artifact_digest="sha256:" + "0" * 64
    )
    live = OfficeV2LiveOracleArtifact(
        **live_payload, artifact_digest=sha256_digest(live_draft.digest_payload())
    )
    oracle_payload = canonical_json_bytes(live)
    recording_state_payload = _recording_state_payload(
        case=case,
        initial_state=initial_state,
        execution_id=execution_id,
        bundle=bundle,
        invocations=invocations,
        results=results,
        final_state=final_state,
    )

    artifacts = ArtifactStore(data_root / "artifacts")
    oracle_ref = artifacts.put_bytes(oracle_payload, media_type="application/json")
    recording_ref = artifacts.put_bytes(
        recording_state_payload, media_type="application/json"
    )
    manifest = seal_manifest(
        ReplayManifest(
            replay_id=f"replay.{_short(execution_id)}",
            trajectory_id=f"trajectory.{_short(execution_id)}",
            created_at=NOW,
            case_id=case.case_id,
            scenario_id="office-workspace-v2",
            seed=0,
            image_ref="trace-g-agent:synthetic",
            runtime_version="synthetic",
            agent_version="trace-react-v2",
            state_codec_version="office-v2-state-codec-v1",
            default_tool_replay_mode=ToolReplayMode.STUB_RESPONSE,
            recording_complete=True,
            incomplete_reason=None,
            prompt_digest=sha256_digest("prompt"),
            initial_state_digest=sha256_digest(
                {"generic_recording_envelope": bundle.identity.initial_state_digest}
            ),
            normalized_behavior_trace_digest=sha256_digest("normalized-behavior"),
            determinism_config_digest=sha256_digest("determinism"),
            prompt=oracle_ref,
            events=oracle_ref,
            initial_state=oracle_ref,
            determinism_config=oracle_ref,
            model_decisions=oracle_ref,
            tool_records=oracle_ref,
            checkpoints=oracle_ref,
            office_v2_recording_state=recording_ref,
            office_v2_oracle=oracle_ref,
        )
    )
    ManifestStore(data_root / "replays").save(manifest)
    coverage_input = v2_coverage_input_from_recording(
        manifest,
        oracle_artifact_payload=oracle_payload,
        recording_state_payload=recording_state_payload,
        container_removed=True,
    )
    recorded_payload = {
        "artifact_version": "office-v2-live-oracle-artifact-v1",
        "execution_id": execution_id,
        "trace_digest": live.trace_digest,
        "trusted_facts_digest": live.trusted_facts_digest,
        "evidence_bundle": bundle,
        "oracle_result": oracle_result,
    }
    recorded_draft = OfficeV2RecordedOracleArtifact.model_construct(
        **recorded_payload, artifact_digest="sha256:" + "0" * 64
    )
    recorded = OfficeV2RecordedOracleArtifact(
        **recorded_payload, artifact_digest=sha256_digest(recorded_draft.digest_payload())
    )
    return manifest, coverage_input, recorded


def _recording_state_payload(
    *, case, initial_state, execution_id, bundle, invocations, results, final_state=None
) -> bytes:
    state = initial_state if final_state is None else final_state
    assert initial_state.canonical_digest() == bundle.identity.initial_state_digest
    assert state.canonical_digest() == bundle.identity.final_state_digest
    # A state-changing Episode carries its committed transitions; a benign one
    # has none and keeps the empty history the earlier samples assert.
    history = [
        result.state_transition.model_dump(mode="json", exclude_none=False)
        for result in results
        if result.state_transition is not None
    ]
    session = {
        "schema_version": "office-v2.0",
        "snapshot_version": "office-v2-session-snapshot-v1",
        "execution_envelope_digest": sha256_digest("synthetic-execution-envelope"),
        "episode_id": execution_id,
        "base_world_digest": case.base_world_digest,
        "initial_state_digest": bundle.identity.initial_state_digest,
        "state": state.model_dump(mode="json", exclude_none=False),
        "history": history,
        "state_digest": bundle.identity.final_state_digest,
    }
    session["snapshot_digest"] = sha256_digest(session)
    recording_state = {
        "schema_version": "office-v2.0",
        "recording_state_version": "office-v2-recording-state-v1",
        "session": session,
        "tool_invocations": [
            item.model_dump(mode="json", exclude_none=False) for item in invocations
        ],
        "tool_results": [
            item.model_dump(mode="json", exclude_none=False) for item in results
        ],
        "interaction_events": [],
        "pending_clarification_request_ids": [],
    }
    recording_state["recording_state_digest"] = sha256_digest(recording_state)
    return canonical_json_bytes(recording_state)


class SyntheticEpisodeRunner:
    """Rematerialize the real case and seal a synthetic but valid recording."""

    def __init__(self, *, data_root: Path, fail_first_attempt: bool = False) -> None:
        self.data_root = data_root
        self.failures_left = 1 if fail_first_attempt else 0

    def cleanup_interrupted(self, campaign_id: str) -> None:
        return None

    async def execute(
        self,
        *,
        source_scenario_case_id: str,
        generated_content: str,
        execution_id: str,
        seed: int,
        run_context,
    ) -> OfficeV2EpisodeResult:
        if self.failures_left:
            self.failures_left -= 1
            raise TimeoutError("synthetic Episode timed out before the recording was sealed")
        canonical_world = load_canonical_world()
        source_case, purpose = source_attack_materialization_context(
            source_scenario_case_id, canonical_world
        )
        materialized = rematerialize_office_v2_direct_task_text(
            source_case=source_case,
            canonical_world=canonical_world,
            generated_content=generated_content,
            purpose=purpose,
            seed=seed,
        )
        manifest, coverage_input, recorded = _seal_recording(
            data_root=self.data_root,
            case=materialized.scenario_case,
            initial_state=materialized.initial_state,
            initial_transition=materialized.initialization_transition,
            tools=CALL_PLANS[seed % len(CALL_PLANS)],
            execution_id=execution_id,
        )
        return OfficeV2EpisodeResult(
            scenario_case=materialized.scenario_case,
            manifest=manifest,
            oracle=recorded,
            coverage_input=coverage_input,
            agent_tokens=1_500,
            elapsed_ms=45_000,
        )


def _run_campaign(*, store, data_root, campaign_id, strategy, campaign_seed_value, bootstrap):
    runner = SyntheticEpisodeRunner(data_root=data_root, fail_first_attempt=True)
    driver = _RealGenerationDriver(
        store=store,
        bootstrap=bootstrap,
        mutation_provider=RuleBasedV2MutationProvider(),
        episode_runner=runner,
        strategy=strategy,
        campaign_seed_value=campaign_seed_value,
    )
    state = bootstrap.initial_state
    feedback = None
    observed = []
    for generation in range(GENERATIONS):
        decision = decide_next_generation(
            campaign_id=campaign_id,
            state=state,
            latest_feedback=feedback,
            previous_closure=store.load_latest_generation_closure(campaign_id),
            strategy=strategy,
        )
        store.put_generation_decision(decision)
        advance = driver.advance(
            campaign_id=campaign_id,
            decision=decision,
            state=state,
            previous_feedback=feedback,
        )
        assert advance is not None, f"generation {generation} did not settle"
        state = store.load_state(campaign_id)
        feedback = store.load_latest_feedback(campaign_id)
        score = score_campaign(store=store, campaign_id=campaign_id, data_root=data_root)
        observed.append(
            (len(score.path.unigram), len(score.path.bigram), len(score.path.trigram))
        )
    return observed


def test_a_local_campaign_persists_real_episodes_with_a_failure(tmp_path) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    bootstrap = build_exploratory_bootstrap(episode_limit=GENERATIONS)
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        store.create_campaign(campaign_id=GUIDED, initial_state=bootstrap.initial_state)
        observed = _run_campaign(
            store=store,
            data_root=data_root,
            campaign_id=GUIDED,
            strategy=CampaignStrategy.COVERAGE_GUIDED,
            campaign_seed_value=7,
            bootstrap=bootstrap,
        )
        settlements = store.list_settlements(GUIDED)
        receipts = store.list_attempt_receipts(GUIDED)
        final = score_campaign(store=store, campaign_id=GUIDED, data_root=data_root)

    # Hand-computed growth: each window length grows once, then a repeat adds nothing.
    assert observed == [(1, 0, 0), (1, 1, 0), (1, 1, 0), (1, 1, 1)]
    # Real persistence through production code.
    assert len(settlements) == GENERATIONS
    assert final.path.availability == COMPLETE
    assert final.path.scorable_episodes == GENERATIONS
    # Benign reads never attempt the frozen target, so the risk counts are a real zero.
    assert final.risk.counts == {
        "risk_types_attempted": 0,
        "risk_types_realized": 0,
        "risk_targets_attempted": 0,
        "risk_targets_realized": 0,
    }
    # The failure is real and belongs to the first Episode's own attempt lineage.
    assert len(receipts) == GENERATIONS + 1
    failed = [item for item in receipts if item.error_code is not None]
    assert len(failed) == 1
    assert failed[0].error_code == "episode-timeout"
    assert set(settlements[0].attempt_receipt_ids) == {
        receipts[0].attempt_id,
        receipts[1].attempt_id,
    }
    # Every member points back to a recorded Episode, and the recording digest is
    # the one the settlement carries.
    manifest = {
        item.execution_record_id: item.execution_record.manifest_digest
        for item in settlements
    }
    for member in final.path.unigram_members:
        assert member.episodes
        assert all(episode in manifest for episode in member.episodes)
    assert final.path.unigram_members[0].evidence_ids


def test_the_persisted_pair_reports_both_arms(tmp_path) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    bootstrap = build_exploratory_bootstrap(episode_limit=GENERATIONS)
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        store.create_campaign(campaign_id=GUIDED, initial_state=bootstrap.initial_state)
        store.create_campaign(
            campaign_id="sample.independent",
            initial_state=bootstrap.initial_state,
            strategy="random_independent",
        )
        _run_campaign(
            store=store,
            data_root=data_root,
            campaign_id=GUIDED,
            strategy=CampaignStrategy.COVERAGE_GUIDED,
            campaign_seed_value=7,
            bootstrap=bootstrap,
        )
        _run_campaign(
            store=store,
            data_root=data_root,
            campaign_id="sample.independent",
            strategy=CampaignStrategy.RANDOM_INDEPENDENT,
            campaign_seed_value=7,
            bootstrap=bootstrap,
        )
        report = build_v2_comparison_report(
            store=store,
            guided_campaign_id=GUIDED,
            independent_campaign_id="sample.independent",
            data_root=data_root,
        )

    assert report["arms"]["guided"]["metrics"]["tool_path_unigram"]["value"] == 1
    assert report["arms"]["guided"]["metrics"]["tool_path_bigram"]["value"] == 1
    assert report["arms"]["guided"]["metrics"]["tool_path_trigram"]["value"] == 1
    # Both arms score their path metric completely, so a difference is given.
    delta = report["deltas"]["tool_path_unigram"]
    assert delta["difference"] is not None
    # Risk is a real zero on both arms, not a withheld value.
    assert report["arms"]["guided"]["metrics"]["risk_types_attempted"]["value"] == 0
    assert report["deltas"]["risk_types_attempted"]["difference"] == 0
    # The budgets actually came from the persisted snapshot.
    assert report["comparability"]["verified_from_store"]["guided_episode_limit"] == (
        GENERATIONS
    )
    assert report["deltas"]["risk_types_attempted"]["value_withheld"] is None
