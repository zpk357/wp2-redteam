from __future__ import annotations

import hashlib
import json
import tarfile
from pathlib import Path

import pytest

from sandbox.fuzzer.v2_campaign_store import V2CampaignStore
from sandbox.fuzzer.v2_real_runtime import run_or_resume_real_campaign
from sandbox.fuzzer.v2_report import write_v2_campaign_report
from sandbox.fuzzer.v2_scripted_runtime import (
    ScriptedCampaignBootstrap,
    run_or_resume_scripted_campaign,
)
from sandbox.fuzzer.v2_stage6_evidence import (
    audit_stage6_milestone,
    audit_two_generation_gate,
    build_stage6_evidence_archive,
    verify_stage6_evidence_archive,
    write_stage6_milestone,
)
from sandbox.fuzzer.v2_stage6_source_identity import (
    verify_source_tree_identity,
    write_source_tree_identity,
)
from sandbox.fuzzer.v2_work import (
    BudgetReservation,
    CandidateWork,
    CandidateWorkState,
    seal_work_contract,
)
from sandbox.mutation.v2_brief import ProviderSlotValue
from sandbox.mutation.v2_provider import RuleBasedV2MutationProvider
from sandbox.replay.digests import sha256_digest
from sandbox.replay.models import ReplayResult
from scripts.build_office_v2_stage6_bootstrap import build_stage6_bootstrap
from tests.unit.test_office_v2_feedback_loop_batch_c import loop_fixture

CAMPAIGN_ID = "campaign.stage6.fake-gate"


def _add_real_runtime_audit_rows(store: V2CampaignStore) -> None:
    decision_rows = store._db.execute(
        "SELECT generation_index, decision_json FROM generation_decision "
        "WHERE campaign_id=? ORDER BY generation_index",
        (CAMPAIGN_ID,),
    ).fetchall()
    feedback_one = json.loads(
        store._db.execute(
            "SELECT feedback_json FROM generation_feedback WHERE campaign_id=? "
            "AND generation_index=1",
            (CAMPAIGN_ID,),
        ).fetchone()[0]
    )
    closure_zero = json.loads(
        store._db.execute(
            "SELECT closure_json FROM generation_closure WHERE campaign_id=? "
            "AND generation_index=0",
            (CAMPAIGN_ID,),
        ).fetchone()[0]
    )
    with store._db:
        for row in decision_rows:
            generation = row["generation_index"]
            preparation_id = f"preparation.fake.{generation}"
            feedback_digest = (
                feedback_one["feedback_digest"]
                if generation == 1
                else sha256_digest("initial-feedback")
            )
            preparation = {
                "preparation_id": preparation_id,
                "plan": {
                    "intent": {"feedback_digest": feedback_digest},
                    "allocation": {
                        "base_allocation": {"generation_index": generation},
                        "operator_allocation": {
                            "reason_codes": ["fake-feedback-gap"]
                        },
                    },
                },
            }
            store._db.execute(
                "INSERT INTO mutation_preparation VALUES (?, ?, ?, ?)",
                (
                    preparation_id,
                    CAMPAIGN_ID,
                    sha256_digest(preparation),
                    json.dumps(preparation),
                ),
            )
            store._db.execute(
                "INSERT INTO mutation_provider_attempt VALUES (?, ?, ?, ?)",
                (
                    f"provider-attempt.fake.{generation}",
                    preparation_id,
                    sha256_digest({"attempt": generation}),
                    json.dumps({"state": "succeeded"}),
                ),
            )
        decision_zero = json.loads(decision_rows[0]["decision_json"])
        allocation = decision_zero["allocation"]
        store._db.execute(
            "INSERT INTO generation_allocation VALUES (?, ?, ?, ?)",
            (
                allocation["generation_allocation_id"],
                CAMPAIGN_ID,
                allocation["allocation_digest"],
                json.dumps(allocation),
            ),
        )
        work = seal_work_contract(
            CandidateWork,
            {
                "work_id": "work.fake.0",
                "campaign_id": CAMPAIGN_ID,
                "generation_allocation_id": allocation["generation_allocation_id"],
                "generation_allocation_digest": allocation["allocation_digest"],
                "comparison_context_digest": sha256_digest("comparison"),
                "baseline_snapshot_digest": allocation["coverage_snapshot_digest"],
                "budget_reservation": BudgetReservation(),
                "state": CandidateWorkState.SEALED,
                "sealed_execution_record_id": feedback_one["execution_record_id"],
            },
            "work_digest",
        )
        work_json = work.model_dump_json()
        store._db.execute(
            "INSERT INTO candidate_work VALUES (?, ?, ?, ?)",
            (work.work_id, CAMPAIGN_ID, work.work_digest, work_json),
        )
        store._db.execute(
            "INSERT INTO settlement VALUES (?, ?, ?, ?)",
            (
                closure_zero["settlement_id"],
                work.work_id,
                closure_zero["settlement_digest"],
                json.dumps({"execution": "fake-agent-episode"}),
            ),
        )


def test_two_generation_gate_accepts_boundary_resume_without_duplicates(
    tmp_path: Path,
) -> None:
    promoted, state = loop_fixture()
    path = tmp_path / "campaign.sqlite3"
    bootstrap = ScriptedCampaignBootstrap(
        initial_state=state, execution=promoted.execution, delta=promoted.delta
    )
    with V2CampaignStore(path) as store:
        first = run_or_resume_scripted_campaign(
            store=store,
            campaign_id=CAMPAIGN_ID,
            bootstrap=bootstrap,
            generation_count=1,
        )
        first_decision = first.decision_digests
    with V2CampaignStore(path) as store:
        second = run_or_resume_scripted_campaign(
            store=store,
            campaign_id=CAMPAIGN_ID,
            bootstrap=bootstrap,
            generation_count=2,
        )
        _add_real_runtime_audit_rows(store)

    report = audit_two_generation_gate(db_path=path, campaign_id=CAMPAIGN_ID)
    assert first_decision == second.decision_digests[:1]
    assert len(second.decision_digests) == len(set(second.decision_digests)) == 2
    assert report["passed"] is True


def test_milestone_gate_rejects_early_nonterminal_campaign(tmp_path: Path) -> None:
    promoted, state = loop_fixture()
    path = tmp_path / "campaign.sqlite3"
    bootstrap = ScriptedCampaignBootstrap(
        initial_state=state, execution=promoted.execution, delta=promoted.delta
    )
    with V2CampaignStore(path) as store:
        run_or_resume_scripted_campaign(
            store=store,
            campaign_id=CAMPAIGN_ID,
            bootstrap=bootstrap,
            generation_count=2,
        )
        store.bind_runtime_identity(CAMPAIGN_ID, identity_digest=sha256_digest("runtime"))

    report = audit_stage6_milestone(
        db_path=path, campaign_id=CAMPAIGN_ID, target_generation=10
    )
    assert report["passed"] is False
    assert report["result_kind"] == "target_not_reached"


class InvalidSlotMutationProvider:
    provider_id = RuleBasedV2MutationProvider.provider_id

    async def generate(self, *, plan, brief, attempt_index):
        result = await RuleBasedV2MutationProvider().generate(
            plan=plan, brief=brief, attempt_index=attempt_index
        )
        candidate = result.candidate.model_copy(
            update={
                "slot_values": (
                    ProviderSlotValue(
                        payload_slot_id="slot.unplanned",
                        generated_content="invalid unplanned slot",
                    ),
                )
            }
        )
        return result.model_copy(update={"candidate": candidate})


class ForbiddenEpisodeRunner:
    async def execute(self, **_kwargs):
        raise AssertionError("invalid candidate must not launch Agent")


class FailingEpisodeRunner:
    async def execute(self, **_kwargs):
        raise RuntimeError("injected Agent failure")


def test_invalid_candidate_is_settled_without_agent(tmp_path: Path) -> None:
    bootstrap = build_stage6_bootstrap(model_identity_digest="sha256:" + "a" * 64)
    with V2CampaignStore(tmp_path / "invalid.sqlite3") as store:
        result = run_or_resume_real_campaign(
            store=store,
            campaign_id="campaign.stage6.invalid",
            bootstrap=bootstrap,
            generation_count=1,
            mutation_provider=InvalidSlotMutationProvider(),
            episode_runner=ForbiddenEpisodeRunner(),
        )
        state = store.load_state("campaign.stage6.invalid")
    assert result.completed_generation_count == 1
    assert state.lifecycle.counters.valid_committed_episodes == 0
    assert state.lifecycle.counters.invalid_or_failed_attempts == 1


def test_agent_failure_is_visible_and_preserves_campaign_db(tmp_path: Path) -> None:
    bootstrap = build_stage6_bootstrap(model_identity_digest="sha256:" + "a" * 64)
    path = tmp_path / "agent-failure.sqlite3"
    with V2CampaignStore(path) as store:
        with pytest.raises(RuntimeError, match="injected Agent failure"):
            run_or_resume_real_campaign(
                store=store,
                campaign_id="campaign.stage6.agent-failure",
                bootstrap=bootstrap,
                generation_count=1,
                mutation_provider=RuleBasedV2MutationProvider(),
                episode_runner=FailingEpisodeRunner(),
            )
        assert store.generation_index("campaign.stage6.agent-failure") == 0
        assert store.load_latest_generation_decision(
            "campaign.stage6.agent-failure"
        ) is not None
    assert path.is_file()


def _evidence_files(tmp_path: Path) -> dict[str, Path]:
    campaign = tmp_path / "campaign"
    results = tmp_path / "results"
    campaign.mkdir()
    results.mkdir()
    promoted, state = loop_fixture()
    role_receipt_digest = sha256_digest("role-build")
    lock_payload = {
        "manifest_digest": sha256_digest("model"),
        "controller_image_reference": "controller:test",
        "controller_image_id": sha256_digest("controller-image"),
        "roles": [
            {
                "role": role,
                "image_reference": f"{role}:test",
                "image_id": sha256_digest(f"{role}-image"),
                "image_build_receipt_digest": role_receipt_digest,
            }
            for role in ("agent", "mutator")
        ],
    }
    lock_payload["lock_digest"] = sha256_digest(lock_payload)
    lock_payload["roles"][0]["image_archive_sha256"] = None
    runtime_digest = lock_payload["lock_digest"]
    bootstrap_value = ScriptedCampaignBootstrap(
        initial_state=state,
        execution=promoted.execution,
        delta=promoted.delta,
    )
    with V2CampaignStore(campaign / "campaign.sqlite3") as store:
        run_or_resume_scripted_campaign(
            store=store,
            campaign_id=CAMPAIGN_ID,
            bootstrap=bootstrap_value,
            generation_count=2,
        )
        store.bind_runtime_identity(CAMPAIGN_ID, identity_digest=runtime_digest)
        write_v2_campaign_report(
            store=store,
            campaign_id=CAMPAIGN_ID,
            output=results / "campaign-report.json",
        )
    write_stage6_milestone(
        db_path=campaign / "campaign.sqlite3",
        campaign_id=CAMPAIGN_ID,
        target_generation=2,
        output=results / "milestone-to-2.json",
    )
    (campaign / "recording.json").write_text("{}\n", encoding="utf-8")
    replay_id = "replay.stage6.fixture"
    replay_run_id = "run.stage6.fixture"
    source_trajectory_id = "trajectory.stage6.fixture"
    replay_root = campaign / "replays" / replay_id
    replay_root.mkdir(parents=True)
    (campaign / "replays" / "replay.stage6.fixture" / "manifest.json").write_text(
        json.dumps(
            {
                "replay_id": replay_id,
                "trajectory_id": source_trajectory_id,
                "recording_complete": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    report = json.loads((results / "campaign-report.json").read_text(encoding="utf-8"))
    (results / "stage6-campaign-progress.jsonl").write_text(
        json.dumps(
            {
                "schema_version": "office-v2-stage6-progress-v1",
                "campaign_id": CAMPAIGN_ID,
                "mode": "run",
                "requested_target": 1,
                "observed_generation": 1,
                "completion_status": None,
                "report_digest": sha256_digest("generation-one-report"),
            }
        )
        + "\n"
        + json.dumps(
            {
                "schema_version": "office-v2-stage6-progress-v1",
                "campaign_id": CAMPAIGN_ID,
                "mode": "resume",
                "requested_target": 2,
                "observed_generation": 2,
                "completion_status": report["completion_status"],
                "report_digest": report["report_digest"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    replay_result = ReplayResult.model_validate({
        "replay_run_id": replay_run_id,
        "source_replay_id": replay_id,
        "source_trajectory_id": source_trajectory_id,
        "replay_trajectory_id": "trajectory.stage6.fixture.strict",
        "status": "matched",
        "source_behavior_digest": sha256_digest("behavior"),
        "replay_behavior_digest": sha256_digest("behavior"),
        "source_final_state_digest": sha256_digest("state"),
        "replay_final_state_digest": sha256_digest("state"),
        "checkpoint_comparisons": [],
        "first_divergence_behavior_index": None,
        "source_divergence_sequence": None,
        "replay_divergence_sequence": None,
        "divergence_reason": None,
        "error_code": None,
        "missing_artifacts": [],
        "container_removed": True,
    }).model_dump(mode="json")
    (results / "stage6-replay-report.json").write_text(
        json.dumps(replay_result) + "\n", encoding="utf-8"
    )
    replay_run = replay_root / "runs" / replay_run_id
    replay_run.mkdir(parents=True)
    (replay_run / "result.json").write_text(
        json.dumps(replay_result) + "\n", encoding="utf-8"
    )
    paths = {"campaign": campaign, "results": results}
    payloads = {
        "model-lock": lock_payload,
        "bootstrap": {"model_identity_digest": lock_payload["manifest_digest"]},
        "preflight": {
            "passed": True,
            "model_digest": lock_payload["manifest_digest"],
            "model_lock_digest": lock_payload["lock_digest"],
            "mutator_completed_before_agent": True,
            "agent_successful_tool_exchange_count": 1,
            "agent_model_decision_count": 2,
            "agent_post_tool_decision_proved": True,
        },
        "repair-plan": {
            "source_revision": "376f413",
            "model_digest": lock_payload["manifest_digest"],
            "controller_image_reference": lock_payload["controller_image_reference"],
            "controller_image_id": lock_payload["controller_image_id"],
            "roles": [
                {
                    "role": item["role"],
                    "final_image_reference": item["image_reference"],
                }
                for item in lock_payload["roles"]
            ],
            "lock_digest": "pending",
        },
        "repair-receipt": {
            "repair_lock_digest": "pending",
            "active_model_lock_digest": lock_payload["lock_digest"],
            "roles": [
                {
                    "role": item["role"],
                    "image_reference": item["image_reference"],
                    "image_id": item["image_id"],
                    "image_build_receipt_digest": item[
                        "image_build_receipt_digest"
                    ],
                }
                for item in lock_payload["roles"]
            ],
            "receipt_digest": "pending",
        },
        "stage-record": {
            "status": "ready",
            "source_revision": "376f413",
            "repair_lock_digest": "pending",
        },
        "source-tree": {
            "schema_version": "office-v2-stage6-source-tree-v1",
            "source_revision": "376f413",
            "files": [{"path": "src/example.py", "size": 1, "sha256": sha256_digest("x")}],
            "source_tree_digest": "pending",
        },
        "server-host": {"captured": True},
        "gpu-residency": {
            "passed": True,
            "campaign_id": "stage6-preflight",
            "full_residency": {"agent": True, "mutator": True},
            "residual_observed_model_process_pids": [],
        },
    }
    payloads["repair-plan"]["lock_digest"] = sha256_digest(
        {
            key: value
            for key, value in payloads["repair-plan"].items()
            if key != "lock_digest"
        }
    )
    payloads["repair-receipt"]["repair_lock_digest"] = payloads["repair-plan"][
        "lock_digest"
    ]
    payloads["stage-record"]["repair_lock_digest"] = payloads["repair-plan"][
        "lock_digest"
    ]
    payloads["repair-receipt"]["receipt_digest"] = sha256_digest(
        {
            key: value
            for key, value in payloads["repair-receipt"].items()
            if key != "receipt_digest"
        }
    )
    payloads["source-tree"]["source_tree_digest"] = sha256_digest(
        {
            "schema_version": "office-v2-stage6-source-tree-v1",
            "source_revision": "376f413",
            "files": payloads["source-tree"]["files"],
        }
    )
    for name, payload in payloads.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        paths[name] = path
    return paths


def test_complete_archive_contains_campaign_results_and_identity(tmp_path: Path) -> None:
    paths = _evidence_files(tmp_path)
    output = tmp_path / "archive" / "complete.tar.gz"
    result = build_stage6_evidence_archive(
        campaign_id=CAMPAIGN_ID,
        outcome="success",
        campaign_root=paths["campaign"],
        result_root=paths["results"],
        model_lock=paths["model-lock"],
        bootstrap=paths["bootstrap"],
        preflight=paths["preflight"],
        repair_plan=paths["repair-plan"],
        repair_receipt=paths["repair-receipt"],
        stage_record=paths["stage-record"],
        source_tree_identity=paths["source-tree"],
        server_host=paths["server-host"],
        gpu_residency=paths["gpu-residency"],
        output=output,
    )
    with tarfile.open(output, "r:gz") as archive:
        names = set(archive.getnames())
    assert {
        "campaign/campaign.sqlite3",
        "campaign/recording.json",
        "results/campaign-report.json",
        "identity/stage6-model-lock.json",
        "identity/stage6-bootstrap.json",
        "preflight/stage6-preflight.json",
        "identity/stage6-repair-application.json",
        "preflight/stage6-server-host.json",
        "preflight/stage6-gpu-residency.json",
        "stage6-evidence-manifest.json",
    }.issubset(names)
    assert output.with_name(output.name + ".sha256").is_file()
    assert result["archive_sha256"] == "sha256:" + hashlib.sha256(
        output.read_bytes()
    ).hexdigest()
    assert verify_stage6_evidence_archive(output)["campaign_id"] == CAMPAIGN_ID


def test_archive_verifier_rejects_mixed_campaign_report(tmp_path: Path) -> None:
    paths = _evidence_files(tmp_path)
    report_path = paths["results"] / "campaign-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["campaign_id"] = "campaign.stage6.other"
    report_path.write_text(json.dumps(report) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="database, report, and model identity differ"):
        build_stage6_evidence_archive(
            campaign_id=CAMPAIGN_ID,
            outcome="success",
            campaign_root=paths["campaign"],
            result_root=paths["results"],
            model_lock=paths["model-lock"],
            bootstrap=paths["bootstrap"],
            preflight=paths["preflight"],
            repair_plan=paths["repair-plan"],
            repair_receipt=paths["repair-receipt"],
            stage_record=paths["stage-record"],
            source_tree_identity=paths["source-tree"],
            server_host=paths["server-host"],
            gpu_residency=paths["gpu-residency"],
            output=tmp_path / "mixed.tar.gz",
        )


def test_archive_verifier_rejects_replay_report_from_another_run(
    tmp_path: Path,
) -> None:
    paths = _evidence_files(tmp_path)
    replay_report = paths["results"] / "stage6-replay-report.json"
    payload = json.loads(replay_report.read_text(encoding="utf-8"))
    payload["replay_run_id"] = "run.stage6.other"
    replay_report.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="not bound to archived run artifacts"):
        build_stage6_evidence_archive(
            campaign_id=CAMPAIGN_ID,
            outcome="success",
            campaign_root=paths["campaign"],
            result_root=paths["results"],
            model_lock=paths["model-lock"],
            bootstrap=paths["bootstrap"],
            preflight=paths["preflight"],
            repair_plan=paths["repair-plan"],
            repair_receipt=paths["repair-receipt"],
            stage_record=paths["stage-record"],
            source_tree_identity=paths["source-tree"],
            server_host=paths["server-host"],
            gpu_residency=paths["gpu-residency"],
            output=tmp_path / "mixed-replay.tar.gz",
        )


def test_archive_failure_does_not_remove_original_evidence(tmp_path: Path) -> None:
    paths = _evidence_files(tmp_path)
    missing_lock = tmp_path / "missing-lock.json"
    original_database = (paths["campaign"] / "campaign.sqlite3").read_bytes()
    with pytest.raises(ValueError, match="required evidence file"):
        build_stage6_evidence_archive(
            campaign_id=CAMPAIGN_ID,
            outcome="failure",
            campaign_root=paths["campaign"],
            result_root=paths["results"],
            model_lock=missing_lock,
            bootstrap=paths["bootstrap"],
            preflight=paths["preflight"],
            repair_plan=paths["repair-plan"],
            repair_receipt=paths["repair-receipt"],
            stage_record=paths["stage-record"],
            source_tree_identity=paths["source-tree"],
            server_host=paths["server-host"],
            gpu_residency=paths["gpu-residency"],
            output=tmp_path / "failed.tar.gz",
        )
    assert (paths["campaign"] / "campaign.sqlite3").read_bytes() == original_database


def test_archive_verifier_rejects_corruption(tmp_path: Path) -> None:
    paths = _evidence_files(tmp_path)
    output = tmp_path / "complete.tar.gz"
    build_stage6_evidence_archive(
        campaign_id=CAMPAIGN_ID,
        outcome="success",
        campaign_root=paths["campaign"],
        result_root=paths["results"],
        model_lock=paths["model-lock"],
        bootstrap=paths["bootstrap"],
        preflight=paths["preflight"],
        repair_plan=paths["repair-plan"],
        repair_receipt=paths["repair-receipt"],
        stage_record=paths["stage-record"],
        source_tree_identity=paths["source-tree"],
        server_host=paths["server-host"],
        gpu_residency=paths["gpu-residency"],
        output=output,
    )
    payload = bytearray(output.read_bytes())
    payload[len(payload) // 2] ^= 1
    output.write_bytes(payload)
    with pytest.raises(ValueError, match="archive checksum differs"):
        verify_stage6_evidence_archive(output)


def test_source_tree_identity_rejects_post_install_tamper(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    source = root / "controller.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    identity = root / ".trace-g" / "stage6-source-tree.json"
    write_source_tree_identity(
        root=root, source_revision="376f413", output=identity
    )
    verify_source_tree_identity(root=root, identity=identity)

    source.write_text("VALUE = 2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="differs from frozen identity"):
        verify_source_tree_identity(root=root, identity=identity)


def test_complete_archive_refuses_to_overwrite_prior_attempt(tmp_path: Path) -> None:
    paths = _evidence_files(tmp_path)
    output = tmp_path / "complete.tar.gz"
    arguments = {
        "campaign_id": CAMPAIGN_ID,
        "outcome": "success",
        "campaign_root": paths["campaign"],
        "result_root": paths["results"],
        "model_lock": paths["model-lock"],
        "bootstrap": paths["bootstrap"],
        "preflight": paths["preflight"],
        "repair_plan": paths["repair-plan"],
        "repair_receipt": paths["repair-receipt"],
        "stage_record": paths["stage-record"],
        "source_tree_identity": paths["source-tree"],
        "server_host": paths["server-host"],
        "gpu_residency": paths["gpu-residency"],
        "output": output,
    }
    build_stage6_evidence_archive(**arguments)

    with pytest.raises(ValueError, match="already exists"):
        build_stage6_evidence_archive(**arguments)
