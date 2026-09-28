"""The production campaign boundary reloads selected parent evidence from disk."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
import test_structured_feedback_mutation as window_helpers  # noqa: E402
from test_structured_feedback_mutation import _complete_parent  # noqa: E402
from test_structured_two_arm_search import _at_local_behavior_slot  # noqa: E402

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.structured_v1.fixtures import load_fixture
from sandbox.scenarios.structured_v1.fixtures import summary_delivery_b as B
from sandbox.structured_v1.bundle import finalize_bundle, write_finalized_bundle
from sandbox.structured_v1.campaign import (
    CampaignCheckpoint,
    CampaignLimits,
    CampaignUsage,
    load_checkpoint,
    run_opportunity,
)
from sandbox.structured_v1.campaign_report import checkpoint_report
from sandbox.structured_v1.coverage import bind_coverage_execution, extract_coverage
from sandbox.structured_v1.feedback import ParentEvidenceError
from sandbox.structured_v1.host import build_host_closure_receipt, build_runtime_receipt
from sandbox.structured_v1.model import ModelDecision
from sandbox.structured_v1.search import ArmKind, TwoArmSearch


def _ready_search():
    fixture, case, finalized, identity = _complete_parent()
    parent_id = case.mutation_lineage.generation_identity
    search = TwoArmSearch({parent_id: case}, fixture.manifest, seed="disk-entry")
    coverage = bind_coverage_execution(
        extract_coverage(finalized.container_bundle, manifest=fixture.manifest),
        bundle=finalized.container_bundle,
        case=case,
        manifest=fixture.manifest,
        execution_config_digest=identity.execution_config_digest,
    )
    search.record(coverage, parent_id=parent_id)
    _at_local_behavior_slot(search, "disk-entry")
    return fixture, finalized, search


def _checkpoint(search):
    return CampaignCheckpoint(
        limits=CampaignLimits(
            opportunities=2, model_calls=20, tool_calls=20,
            input_tokens=10000, output_tokens=10000,
            wall_clock_seconds=1000, expense_units=1000,
        ),
        search=search.state,
    )


@pytest.mark.parametrize("evidence", ["missing", "incomplete"])
def test_missing_or_incomplete_selected_parent_spends_one_opportunity_without_request(
    tmp_path, evidence,
):
    fixture, finalized, search = _ready_search()
    directory = tmp_path / "finalized"
    directory.mkdir()
    if evidence == "incomplete":
        incomplete = finalize_bundle(
            finalized.container_bundle,
            runtime_receipt=finalized.runtime_receipt,
            host_closure_receipt=build_host_closure_receipt(
                runtime_receipt=finalized.runtime_receipt,
                stop_requested=True,
                container_absent=False,
                no_post_bundle_activity=True,
                isolation_confirmed=True,
                source="test-substitute",
            ),
        )
        write_finalized_bundle(incomplete, directory / "parent.json")
    path = tmp_path / "checkpoint.json"
    state, bundle = run_opportunity(
        path, _checkpoint(search), search,
        arm=ArmKind.COVERAGE_GUIDED, manifest=fixture.manifest,
        execute=lambda case: pytest.fail("child must not execute"),
        planned=CampaignUsage(model_calls=3, tool_calls=3),
        parent_bundle_directory=directory,
    )
    assert bundle is None
    assert state.usage.opportunities == 1
    assert state.usage.model_calls == state.usage.mutator_calls == 0
    assert state.failed_parent_evidence[0]["reason"] == (
        "ParentBundleNotFound" if evidence == "missing" else "ParentBundleIncomplete"
    )
    assert not state.generations and not state.selections
    assert load_checkpoint(path) == state


def test_complete_parent_is_loaded_before_local_position_selection(tmp_path):
    fixture, finalized, search = _ready_search()
    directory = tmp_path / "finalized"
    directory.mkdir()
    write_finalized_bundle(finalized, directory / "parent.json")
    path = tmp_path / "checkpoint.json"
    with pytest.raises(RuntimeError, match="fault after selection"):
        run_opportunity(
            path, _checkpoint(search), search,
            arm=ArmKind.COVERAGE_GUIDED, manifest=fixture.manifest,
            execute=lambda case: pytest.fail("child must not execute"),
            planned=CampaignUsage(model_calls=3, tool_calls=3),
            parent_bundle_directory=directory,
            fault_after="selection",
        )
    persisted = load_checkpoint(path)
    receipt = persisted.pending_selection
    assert receipt is not None and not receipt.root_restart
    assert receipt.feedback_source_digest == finalized.final_bundle_digest
    assert receipt.selection_branch == "common"  # complete parent has no slot exposure
    assert receipt.public_feedback is not None
    assert persisted.search.parent_feedback[receipt.parent_id].source.final_bundle_digest == (
        finalized.final_bundle_digest
    )


def test_resume_rechecks_pending_parent_before_generation(tmp_path):
    fixture, finalized, search = _ready_search()
    directory = tmp_path / "finalized"
    directory.mkdir()
    parent_path = write_finalized_bundle(finalized, directory / "parent.json")
    path = tmp_path / "checkpoint.json"
    with pytest.raises(RuntimeError, match="fault after selection"):
        run_opportunity(
            path, _checkpoint(search), search,
            arm=ArmKind.COVERAGE_GUIDED, manifest=fixture.manifest,
            execute=lambda child: pytest.fail("child must not execute"),
            planned=CampaignUsage(model_calls=3, tool_calls=3),
            parent_bundle_directory=directory, fault_after="selection",
        )
    pending = load_checkpoint(path)
    parent_path.unlink()
    resumed = TwoArmSearch(search.parents, fixture.manifest, seed="disk-entry")
    resumed.state = pending.search
    state, bundle = run_opportunity(
        path, pending, resumed,
        arm=ArmKind.COVERAGE_GUIDED, manifest=fixture.manifest,
        execute=lambda child: pytest.fail("child must not execute"),
        planned=CampaignUsage(model_calls=3, tool_calls=3),
        parent_bundle_directory=directory,
    )
    assert bundle is None and state.usage.opportunities == 1
    assert state.failed_parent_evidence[0]["reason"] == "ParentBundleNotFound"
    assert state.pending_selection is None
    assert state.usage.model_calls == 0


def test_verified_slot_window_from_disk_reaches_selection_receipt(tmp_path):
    fixture = load_fixture(B.FIXTURE_ID)
    case = window_helpers.helpers._normal_control_case(fixture)
    decisions = [
        ModelDecision(
            call_id="model.001", action_request_id="action.0001",
            tool_name="read_drive_file", arguments={"file_id": B.BRIEF_FILE},
        ),
        ModelDecision(
            call_id="model.002", action_request_id="action.0002",
            tool_name="send_email",
            arguments={"to": [B.PARTNER_ID], "subject": "summary", "body": "public"},
        ),
        ModelDecision(call_id="model.003"),
    ]
    bundle = window_helpers.helpers._run(fixture, decisions, case=case)
    runtime = build_runtime_receipt(
        episode_id=bundle.episode_id, container_id="offline-container",
        image_digest="sha256:" + "a" * 64,
        container_bundle_digest=bundle.bundle_digest, source="test-substitute",
    )
    finalized = finalize_bundle(
        bundle, runtime_receipt=runtime,
        host_closure_receipt=build_host_closure_receipt(
            runtime_receipt=runtime, stop_requested=True, container_absent=True,
            no_post_bundle_activity=True, isolation_confirmed=True,
            source="test-substitute",
        ),
    )
    directory = tmp_path / "finalized"
    directory.mkdir()
    write_finalized_bundle(finalized, directory / "parent.json")
    search = TwoArmSearch(
        {case.mutation_lineage.generation_identity: case}, fixture.manifest,
        seed="disk-entry",
    )
    coverage = bind_coverage_execution(
        extract_coverage(bundle, manifest=fixture.manifest), bundle=bundle,
        case=case, manifest=fixture.manifest,
        execution_config_digest=sha256_digest({"offline": "fdm-t02-window"}),
    )
    search.record(coverage, parent_id=case.mutation_lineage.generation_identity)
    _at_local_behavior_slot(search, "disk-entry")
    path = tmp_path / "checkpoint.json"
    with pytest.raises(RuntimeError, match="fault after selection"):
        run_opportunity(
            path, _checkpoint(search), search,
            arm=ArmKind.COVERAGE_GUIDED, manifest=fixture.manifest,
            execute=lambda child: pytest.fail("child must not execute"),
            planned=CampaignUsage(model_calls=3, tool_calls=3),
            parent_bundle_directory=directory, fault_after="selection",
        )
    receipt = load_checkpoint(path).pending_selection
    assert receipt is not None and receipt.feedback_source_digest == finalized.final_bundle_digest
    assert receipt.priority_position_count > 0
    assert receipt.selection_branch in {"priority", "common"}
    assert receipt.public_feedback is not None


def test_bad_parent_identity_stops_before_provider_or_child(tmp_path):
    fixture, finalized, search = _ready_search()
    directory = tmp_path / "finalized"
    directory.mkdir()
    parent_path = write_finalized_bundle(finalized, directory / "parent.json")
    raw = parent_path.read_text(encoding="utf-8")
    parent_path.write_text(
        raw.replace(finalized.final_bundle_digest, "sha256:" + "0" * 64),
        encoding="utf-8",
    )
    path = tmp_path / "checkpoint.json"
    with pytest.raises(ParentEvidenceError):
        run_opportunity(
            path, _checkpoint(search), search,
            arm=ArmKind.COVERAGE_GUIDED, manifest=fixture.manifest,
            execute=lambda case: pytest.fail("child must not execute"),
            planned=CampaignUsage(model_calls=3, tool_calls=3),
            parent_bundle_directory=directory,
        )
    persisted = load_checkpoint(path)
    assert persisted.stopped and persisted.isolated
    assert persisted.stop_reason == "parent-evidence-identity-invalid"


def test_parent_evidence_failure_reconciles_as_spent_without_episode():
    _, _, search = _ready_search()
    state = CampaignCheckpoint(limits=_checkpoint(search).limits)
    state = state.reserve("opportunity-0")
    state = state.record_receipt("opportunity-0", CampaignUsage())
    state = state.settle("opportunity-0", CampaignUsage()).model_copy(update={
        "failed_parent_evidence": ({
            "opportunity": 0, "parent_id": "parent-0",
            "reason": "ParentBundleNotFound",
        },),
    })
    report = checkpoint_report(state)
    assert report["episodes"] == 0
    assert report["generation_failures"] == 0
    assert report["parent_evidence_failures"] == 1
    assert report["missing"] == []
    assert report["curves"][0]["failure_class"] == "ParentBundleNotFound"


def test_cli_two_arm_passes_finalized_directory_to_production_loop(monkeypatch, tmp_path):
    entry_path = Path(__file__).resolve().parents[2] / "scripts" / "run_structured_v1_episode.py"
    spec = importlib.util.spec_from_file_location("structured_fdm_entry", entry_path)
    assert spec is not None and spec.loader is not None
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    root = tmp_path / "campaign"
    args = entry.build_parser().parse_args([
        "two-arm", "--fixture", "summary-delivery-e2-completion",
        "--image", "offline:test", "--data-root", str(root),
        "--arm", "coverage_guided", "--opportunities", "1",
        "--allow-deterministic-text", "--max-model-calls", "16",
        "--max-tool-calls", "16",
    ])
    runner = SimpleNamespace(
        arm_id=None, episode_counter=None,
        execute=lambda case: pytest.fail("CLI wiring test must not execute"),
        artifacts=lambda bundle: pytest.fail("CLI wiring test must not finalize"),
        execution_config_digest=lambda bundle: "offline",
    )
    monkeypatch.setattr(entry, "_runner", lambda options: runner)
    captured = []

    def observe(*positional, **keyword):
        captured.append(keyword["parent_bundle_directory"])
        return positional[1], None

    monkeypatch.setattr(entry, "run_opportunity", observe)
    assert entry._stage_two_arm(args) == 0
    assert captured == [root / "finalized"]
