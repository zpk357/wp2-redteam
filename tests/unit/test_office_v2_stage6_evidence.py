from __future__ import annotations

import hashlib
import json
import tarfile
from pathlib import Path

import pytest

from sandbox.fuzzer.v2_campaign_store import V2CampaignStore
from sandbox.fuzzer.v2_real_runtime import run_or_resume_real_campaign
from sandbox.fuzzer.v2_scripted_runtime import (
    ScriptedCampaignBootstrap,
    run_or_resume_scripted_campaign,
)
from sandbox.fuzzer.v2_stage6_evidence import (
    audit_two_generation_gate,
    build_stage6_evidence_archive,
    verify_stage6_evidence_archive,
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
    (campaign / "campaign.sqlite3").write_bytes(b"sqlite-evidence")
    (campaign / "recording.json").write_text("{}\n", encoding="utf-8")
    (results / "campaign-report.json").write_text("{}\n", encoding="utf-8")
    paths = {"campaign": campaign, "results": results}
    for name in ("model-lock", "bootstrap", "preflight"):
        path = tmp_path / f"{name}.json"
        path.write_text(f'{{"kind":"{name}"}}\n', encoding="utf-8")
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
        "stage6-evidence-manifest.json",
    }.issubset(names)
    assert output.with_name(output.name + ".sha256").is_file()
    assert result["archive_sha256"] == "sha256:" + hashlib.sha256(
        output.read_bytes()
    ).hexdigest()
    assert verify_stage6_evidence_archive(output)["campaign_id"] == CAMPAIGN_ID


def test_archive_failure_does_not_remove_original_evidence(tmp_path: Path) -> None:
    paths = _evidence_files(tmp_path)
    missing_lock = tmp_path / "missing-lock.json"
    with pytest.raises(ValueError, match="required evidence file"):
        build_stage6_evidence_archive(
            campaign_id=CAMPAIGN_ID,
            outcome="failure",
            campaign_root=paths["campaign"],
            result_root=paths["results"],
            model_lock=missing_lock,
            bootstrap=paths["bootstrap"],
            preflight=paths["preflight"],
            output=tmp_path / "failed.tar.gz",
        )
    assert (paths["campaign"] / "campaign.sqlite3").read_bytes() == b"sqlite-evidence"


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
        output=output,
    )
    payload = bytearray(output.read_bytes())
    payload[len(payload) // 2] ^= 1
    output.write_bytes(payload)
    with pytest.raises(ValueError, match="archive checksum differs"):
        verify_stage6_evidence_archive(output)
