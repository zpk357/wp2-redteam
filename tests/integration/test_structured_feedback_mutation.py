"""FDM-T06: formal runtime/Oracle evidence for feedback-directed mutation.

The model decisions in this file are deterministic test inputs.  They drive the real structured
Office runtime and the real Oracle so the assertions cover state, evidence and B/R/J projections;
they are mechanism evidence and must not be read as a real model success rate or arm advantage.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
import test_structured_fixture_branching as branching  # noqa: E402

from sandbox.replay.digests import sha256_digest  # noqa: E402
from sandbox.scenarios.structured_v1.fixtures import load_fixture  # noqa: E402
from sandbox.scenarios.structured_v1.fixtures import summary_delivery_b as B  # noqa: E402
from sandbox.scenarios.structured_v1.fixtures import summary_delivery_e2 as E2  # noqa: E402
from sandbox.structured_v1.bundle import (  # noqa: E402
    EpisodeBundle,
    finalize_bundle,
    write_finalized_bundle,
)
from sandbox.structured_v1.coverage import (  # noqa: E402
    CoverageLedger,
    CoverageResult,
    bind_coverage_execution,
    extract_coverage,
)
from sandbox.structured_v1.drivers import (  # noqa: E402
    ScriptedModelPort,
    drive_structured_v1_episode,
)
from sandbox.structured_v1.feedback import (  # noqa: E402
    ParentEvidenceError,
    build_parent_feedback,
)
from sandbox.structured_v1.generation import GenerationBudget  # noqa: E402
from sandbox.structured_v1.host import (  # noqa: E402
    build_host_closure_receipt,
    build_runtime_receipt,
)
from sandbox.structured_v1.model import ModelDecision  # noqa: E402
from sandbox.structured_v1.models import OperationKind  # noqa: E402
from sandbox.structured_v1.projection import build_public_projection  # noqa: E402
from sandbox.structured_v1.provider import (  # noqa: E402
    ProviderBoundaryError,
    ProviderRequestKind,
    ProviderTextItem,
    ProviderTextRequest,
    ProviderTextResponse,
    verify_provider_request,
)
from sandbox.structured_v1.search import ArmKind, TwoArmSearch  # noqa: E402


def _stop_decisions(tool_name: str, arguments: dict[str, object]) -> list[ModelDecision]:
    return [
        ModelDecision(
            call_id="model.001",
            action_request_id="action.0001",
            tool_name=tool_name,
            arguments=arguments,
        ),
        ModelDecision(call_id="model.002"),
    ]


def _execute(
    fixture,
    case,
    decisions: list[ModelDecision],
    *,
    episode_id: str,
    closure_proven: bool = True,
):
    """Run one deterministic model port through the formal runtime and finalize its evidence."""

    from sandbox.structured_v1.submission import build_envelope

    prepared = build_envelope(
        case,
        manifest=fixture.manifest,
        base_world=fixture.base_world,
        overlay=fixture.overlay,
        budget=branching._budget(),
        episode_id=episode_id,
        arm_id="fdm-t06-offline",
    )
    assets, tooling = branching._store(fixture)
    bundle = drive_structured_v1_episode(
        prepared.envelope,
        assets=assets,
        tooling=tooling,
        model=ScriptedModelPort(decisions),
        clock=branching._QuietClock(),
    )
    assert isinstance(bundle, EpisodeBundle)
    runtime = build_runtime_receipt(
        episode_id=bundle.episode_id,
        container_id="fdm-t06-container",
        image_digest="sha256:" + "a" * 64,
        image_reference="structured-v1:test",
        container_bundle_digest=bundle.bundle_digest,
        source="test-substitute",
    )
    closure = build_host_closure_receipt(
        runtime_receipt=runtime,
        stop_requested=True,
        container_absent=closure_proven,
        no_post_bundle_activity=True,
        isolation_confirmed=True,
        source="test-substitute",
    )
    return finalize_bundle(bundle, runtime_receipt=runtime, host_closure_receipt=closure)


def _coverage(fixture, case, finalized) -> CoverageResult:
    raw = extract_coverage(
        finalized.container_bundle,
        manifest=fixture.manifest,
        artifacts=finalized.artifacts(),
    )
    return bind_coverage_execution(
        raw,
        bundle=finalized.container_bundle,
        case=case,
        manifest=fixture.manifest,
        execution_config_digest=sha256_digest({
            "task": "fdm-t06", "fixture": fixture.manifest.fixture_id,
        }),
    )


class RecordingProvider:
    provider_id = "fdm-t06-scripted-provider"
    provider_version = "structured-fdm-t06"

    def __init__(self) -> None:
        self.requests: list[ProviderTextRequest] = []

    def complete(self, request: ProviderTextRequest) -> ProviderTextResponse:
        self.requests.append(request)
        return ProviderTextResponse(
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            items=tuple(
                ProviderTextItem(node_id=node_id, text=f"T06 改写 {node_id}。")
                for node_id in request.node_ids
            ),
            input_tokens=17,
            output_tokens=9,
        )


def _search(manifest, case, provider: RecordingProvider, seed: str) -> TwoArmSearch:
    return TwoArmSearch(
        {case.mutation_lineage.generation_identity: case},
        manifest,
        seed=seed,
        provider=provider,
        generation_budget=GenerationBudget(
            requests_per_opportunity=2,
            max_input_tokens_per_request=4096,
            max_output_tokens_per_request=1024,
        ),
    )


def _seed_local_search(search: TwoArmSearch, case, coverage: CoverageResult) -> None:
    parent_id = case.mutation_lineage.generation_identity
    search.record(
        coverage,
        parent_id=parent_id,
        source_parent_id=None,
        evidence_complete=True,
    )
    # Opportunity one is a local opportunity for the selected deterministic seeds.  Keeping the
    # schedule explicit makes this a replayable mechanism check rather than a frequency claim.
    search.state = search.state.model_copy(update={
        "feedback_opportunity": 0,
        "direction_opportunities": {"data-release": 1},
    })


def test_formal_runtime_oracle_exposes_b_r_j_deltas_and_unknown(tmp_path):
    fixture = load_fixture(B.FIXTURE_ID)
    case = branching._normal_control_case(fixture)
    base = _execute(
        fixture,
        case,
        _stop_decisions("read_drive_file", {"file_id": B.OVERVIEW_FILE}),
        episode_id="fdm-t06-base",
    )
    behavior_only = _execute(
        fixture,
        case,
        [
            ModelDecision(
                call_id="model.001",
                action_request_id="action.0001",
                tool_name="search_files",
                arguments={"page_size": 20},
            ),
            ModelDecision(
                call_id="model.002",
                action_request_id="action.0002",
                tool_name="read_file",
                arguments={"path": B.NOTES_PATH},
            ),
            ModelDecision(call_id="model.003"),
        ],
        episode_id="fdm-t06-behavior-only",
    )
    risk_only = _execute(
        fixture,
        case,
        _stop_decisions("read_drive_file", {"file_id": B.BRIEF_FILE}),
        episode_id="fdm-t06-risk-only",
    )
    risk_and_joint = _execute(
        fixture,
        case,
        branching._read_brief_decisions(),
        episode_id="fdm-t06-parent-window",
    )
    duplicate = _execute(
        fixture,
        case,
        _stop_decisions("read_drive_file", {"file_id": B.OVERVIEW_FILE}),
        episode_id="fdm-t06-duplicate",
    )
    incomplete = _execute(
        fixture,
        case,
        branching._read_brief_decisions(),
        episode_id="fdm-t06-unknown",
        closure_proven=False,
    )

    ledger = CoverageLedger()
    base_coverage = _coverage(fixture, case, base)
    ledger, base_application = ledger.apply(base_coverage)
    assert base_application.local_total.behavior_count > 0
    assert base_application.local_total.risk_count == 0
    assert base_application.local_total.joint_count == 0

    behavior_coverage = _coverage(fixture, case, behavior_only)
    ledger, behavior_application = ledger.apply(behavior_coverage)
    assert behavior_application.local_total.behavior_count > 0
    assert behavior_application.local_total.risk_count == 0
    assert behavior_application.local_total.joint_count == 0

    risk_coverage = _coverage(fixture, case, risk_only)
    parent_window_coverage = _coverage(fixture, case, risk_and_joint)
    ledger, risk_application = ledger.apply(risk_coverage)
    # R-only means no new B; the new R fact necessarily creates a new witnessed J relation.
    assert risk_application.local_total.behavior_count == 0
    assert risk_application.local_total.risk_count > 0
    assert risk_application.local_total.joint_count > 0

    ledger, combined_application = ledger.apply(parent_window_coverage)
    assert combined_application.local_total.behavior_count > 0
    assert combined_application.local_total.risk_count > 0
    assert combined_application.local_total.joint_count > 0

    duplicate_coverage = _coverage(fixture, case, duplicate)
    ledger, duplicate_application = ledger.apply(duplicate_coverage)
    assert duplicate_application.local_total.behavior_count == 0
    assert duplicate_application.local_total.risk_count == 0
    assert duplicate_application.local_total.joint_count == 0
    assert duplicate_application.global_delta.behavior_count == 0
    assert duplicate_application.global_delta.risk_count == 0
    assert duplicate_application.global_delta.joint_count == 0

    unknown_coverage = _coverage(fixture, case, incomplete)
    assert incomplete.complete is False
    assert unknown_coverage.judgment_missing
    assert set(unknown_coverage.outcome_by_obligation.values()) == {"unknown"}
    assert unknown_coverage.findings == ()

    parent_dir = tmp_path / "parent-finalized"
    parent_dir.mkdir()
    write_finalized_bundle(risk_and_joint, parent_dir / "parent.json")
    evidence = build_parent_feedback(
        risk_and_joint,
        manifest=fixture.manifest,
        case=case,
        execution_identity=parent_window_coverage.execution,
        target_slot_id="s3",
    )
    assert evidence.public_feedback.observed_slot_ids == ("s3",)
    assert evidence.public_feedback.action_window == "observed"
    assert evidence.slot_proofs[0].later_action_request_ids


def test_feedback_changes_guided_next_draw_and_reaches_runtime(tmp_path):
    fixture = load_fixture(B.FIXTURE_ID)
    case = branching._normal_control_case(fixture)
    parent_final = _execute(
        fixture,
        case,
        branching._read_brief_decisions(),
        episode_id="fdm-t06-parent",
    )
    parent_coverage = _coverage(fixture, case, parent_final)
    parent_id = case.mutation_lineage.generation_identity
    parent_dir = tmp_path / "finalized"
    parent_dir.mkdir()
    write_finalized_bundle(parent_final, parent_dir / "parent.json")

    found = None
    for index in range(80):
        seed = f"fdm-t06-seed-{index}"
        plain_provider = RecordingProvider()
        guided_provider = RecordingProvider()
        plain = _search(fixture.manifest, case, plain_provider, seed)
        guided = _search(fixture.manifest, case, guided_provider, seed)
        _seed_local_search(plain, case, parent_coverage)
        _seed_local_search(guided, case, parent_coverage)
        evidence = guided.load_parent_feedback(
            parent_id,
            str(parent_dir),
            target_slot_id="s3",
        )
        plain_receipt = plain.select(ArmKind.COVERAGE_GUIDED)
        guided_receipt = guided.select(ArmKind.COVERAGE_GUIDED)
        if (
            not plain_receipt.root_restart
            and not guided_receipt.root_restart
            and guided_receipt.selection_branch == "priority"
            and guided_receipt.intent_id is not None
            and plain_receipt.intent_id is not None
            and guided_receipt.selected_position != plain_receipt.selected_position
        ):
            found = (plain, guided, plain_provider, guided_provider,
                     plain_receipt, guided_receipt, evidence)
            break
    assert found is not None, "frozen seeds did not exercise a guided priority fork"
    plain, guided, plain_provider, guided_provider, plain_receipt, guided_receipt, evidence = found

    assert guided_receipt.feedback_sources
    assert guided_receipt.public_feedback == evidence.public_feedback
    assert guided_receipt.intent_id is not None
    plain_case = plain.generate(plain_receipt)
    guided_case = guided.generate(guided_receipt)
    assert plain_provider.requests[-1].feedback is None
    assert guided_provider.requests[-1].feedback == guided_receipt.public_feedback
    assert guided_provider.requests[-1].intent_id == guided_receipt.intent_id
    assert guided_case.mutation_lineage.parent_candidate_id == parent_id
    assert plain_case.mutation_lineage.parent_candidate_id == parent_id

    plain_final = _execute(
        fixture,
        plain_case,
        _stop_decisions("read_drive_file", {"file_id": B.BRIEF_FILE}),
        episode_id="fdm-t06-plain-child",
    )
    guided_final = _execute(
        fixture,
        guided_case,
        _stop_decisions("read_drive_file", {"file_id": B.BRIEF_FILE}),
        episode_id="fdm-t06-guided-child",
    )
    plain_coverage = _coverage(fixture, plain_case, plain_final)
    guided_coverage = _coverage(fixture, guided_case, guided_final)
    assert guided_coverage.execution.material_digest != parent_coverage.execution.material_digest
    assert (
        {item.key for item in guided_coverage.risk}
        != {item.key for item in parent_coverage.risk}
        or {item.key for item in guided_coverage.joint}
        != {item.key for item in parent_coverage.joint}
    )
    assert plain_coverage.execution.material_digest != parent_coverage.execution.material_digest

    guided.record(
        guided_coverage,
        parent_id=guided_case.mutation_lineage.generation_identity,
        parent_baseline=parent_coverage,
        source_parent_id=parent_id,
        selected_unit=guided_receipt.selected_unit,
        evidence_complete=True,
        selection_cooldown=guided_receipt.cooldown,
    )
    retention = guided.state.retention[guided_case.mutation_lineage.generation_identity]
    assert retention.parent_risk_count == len(parent_coverage.risk)
    assert retention.child_risk_count == len(guided_coverage.risk)

    # The random-evolution arm uses the same fixed parent and stream but ignores the injected
    # parent evidence.  Keep this assertion separate from the guided fork above.
    random_plain = TwoArmSearch({parent_id: case}, fixture.manifest, seed="fdm-t06-random")
    random_poisoned = TwoArmSearch({parent_id: case}, fixture.manifest, seed="fdm-t06-random")
    for search in (random_plain, random_poisoned):
        search.state = search.state.model_copy(update={
            "evolution_pool": (parent_id,),
            "feedback_opportunity": 0,
            "direction_opportunities": {"data-release": 1},
        })
    random_poisoned.record_parent_feedback(parent_id, evidence)
    left = random_plain.select(ArmKind.RANDOM_EVOLUTION)
    right = random_poisoned.select(ArmKind.RANDOM_EVOLUTION)
    assert left.selected_position == right.selected_position
    assert left.intent_id == right.intent_id
    assert left.public_feedback is None and right.public_feedback is None
    assert left.feedback_sources == right.feedback_sources == ()


def test_provider_boundary_rejects_private_value_before_transport():
    fixture = load_fixture(E2.FIXTURE_ID)
    private_value = fixture.manifest.private_values[0]
    request = ProviderTextRequest(
        request_id="fdm-t06-private",
        kind=ProviderRequestKind.GENERATION,
        projection=build_public_projection(manifest=fixture.manifest, parent=None),
        node_ids=("n1",),
        obligation_direction="data-release",
        operation=OperationKind.EDIT_TEXT,
        position_description=private_value,
        intent_id="encounter",
        intent_version="structured-intent-v1",
    )
    with pytest.raises(ProviderBoundaryError, match="restricted value"):
        verify_provider_request(request, manifest=fixture.manifest)
    with pytest.raises(ParentEvidenceError):
        from sandbox.structured_v1.feedback import PublicFeedback, validate_public_feedback

        validate_public_feedback(
            PublicFeedback(observed_slot_ids=("not-a-slot",)), manifest=fixture.manifest
        )
