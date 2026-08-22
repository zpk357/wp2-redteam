"""Stage 6 two-generation gate and complete Campaign evidence archive."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import sqlite3
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sandbox.replay.digests import sha256_digest
from sandbox.replay.models import ReplayResult

from .v2_campaign_store import V2CampaignStore
from .v2_feedback import NextGenerationFeedback
from .v2_orchestrator import GenerationClosureReceipt, GenerationDecision

STAGE6_GATE_SCHEMA = "office-v2-stage6-two-generation-gate-v1"
STAGE6_ARCHIVE_SCHEMA = "office-v2-stage6-evidence-archive-v2"
STAGE6_MILESTONE_SCHEMA = "office-v2-stage6-milestone-v1"


def _check(checks: list[dict[str, object]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def audit_two_generation_gate(
    *, db_path: Path, campaign_id: str
) -> dict[str, object]:
    """Verify that generation two consumed generation one's committed feedback."""

    checks: list[dict[str, object]] = []
    with V2CampaignStore(db_path) as store:
        state = store.load_state(campaign_id)
        decisions = {
            row["generation_index"]: GenerationDecision.model_validate_json(
                row["decision_json"]
            )
            for row in store._db.execute(
                "SELECT generation_index, decision_json FROM generation_decision "
                "WHERE campaign_id=? ORDER BY generation_index",
                (campaign_id,),
            )
        }
        feedback = {
            row["generation_index"]: NextGenerationFeedback.model_validate_json(
                row["feedback_json"]
            )
            for row in store._db.execute(
                "SELECT generation_index, feedback_json FROM generation_feedback "
                "WHERE campaign_id=? ORDER BY generation_index",
                (campaign_id,),
            )
        }
        closures = {
            row["generation_index"]: GenerationClosureReceipt.model_validate_json(
                row["closure_json"]
            )
            for row in store._db.execute(
                "SELECT generation_index, closure_json FROM generation_closure "
                "WHERE campaign_id=? ORDER BY generation_index",
                (campaign_id,),
            )
        }

        _check(
            checks,
            "two-generations-committed",
            state.lifecycle.counters.generation_index >= 2
            and {0, 1}.issubset(decisions)
            and {1, 2}.issubset(feedback)
            and {0, 1}.issubset(closures),
            f"generation_index={state.lifecycle.counters.generation_index}",
        )

        if 0 in closures and 1 in decisions:
            _check(
                checks,
                "generation-one-closed-before-generation-two",
                closures[0].resulting_state_digest == decisions[1].input_state_digest,
                "generation 2 input state must equal generation 1 closure state",
            )
        else:
            _check(
                checks,
                "generation-one-closed-before-generation-two",
                False,
                "required lineage records are missing",
            )

        if 1 in decisions and 1 in feedback and 2 in feedback:
            _check(
                checks,
                "generation-two-consumed-generation-one-feedback",
                decisions[1].input_feedback_digest == feedback[1].feedback_digest
                and feedback[2].previous_feedback_digest == feedback[1].feedback_digest,
                "decision[1] and feedback[2] must reference feedback[1]",
            )
        else:
            _check(
                checks,
                "generation-two-consumed-generation-one-feedback",
                False,
                "required feedback records are missing",
            )

        preparations: dict[int, dict[str, Any]] = {}
        attempt_counts: dict[str, int] = {}
        for row in store._db.execute(
            "SELECT preparation_id, preparation_json FROM mutation_preparation "
            "WHERE campaign_id=?",
            (campaign_id,),
        ):
            payload = json.loads(row["preparation_json"])
            generation = payload["plan"]["allocation"]["base_allocation"][
                "generation_index"
            ]
            preparations[generation] = payload
            attempt_counts[row["preparation_id"]] = store._db.execute(
                "SELECT COUNT(*) FROM mutation_provider_attempt WHERE preparation_id=?",
                (row["preparation_id"],),
            ).fetchone()[0]
        _check(
            checks,
            "both-generations-invoked-mutator",
            {0, 1}.issubset(preparations)
            and all(
                attempt_counts.get(preparations[index]["preparation_id"], 0) > 0
                for index in (0, 1)
            ),
            f"preparation_generations={sorted(preparations)}",
        )

        plan_feedback = None
        plan_reasons: list[str] = []
        if 1 in preparations:
            plan = preparations[1]["plan"]
            plan_feedback = plan["intent"]["feedback_digest"]
            plan_reasons = list(
                plan["allocation"]["operator_allocation"].get("reason_codes", [])
            )
        _check(
            checks,
            "generation-two-plan-bound-to-feedback",
            1 in feedback
            and plan_feedback == feedback[1].feedback_digest
            and bool(plan_reasons),
            f"feedback_digest={plan_feedback}; reason_codes={plan_reasons}",
        )

        candidate_closures = tuple(
            item for index, item in closures.items()
            if index in (0, 1) and item.closure_kind.value == "candidate_settlement"
        )
        matched_settlements = 0
        for closure in candidate_closures:
            row = store._db.execute(
                "SELECT settlement_digest FROM settlement WHERE settlement_id=?",
                (closure.settlement_id,),
            ).fetchone()
            if row is not None and row["settlement_digest"] == closure.settlement_digest:
                matched_settlements += 1
        _check(
            checks,
            "at-least-one-agent-settlement",
            state.lifecycle.counters.valid_committed_episodes >= 1
            and matched_settlements >= 1,
            "valid_committed_episodes="
            f"{state.lifecycle.counters.valid_committed_episodes}; "
            f"matched_settlements={matched_settlements}",
        )

        execution_by_id = {
            item.execution_record_id: item for item in state.corpus.execution_records
        }
        evidence_feedback = tuple(
            item for index, item in feedback.items()
            if index in (1, 2)
            and item.execution_record_id is not None
            and item.coverage_delta_digest is not None
        )
        execution_evidence_ok = any(
            item.execution_record_id in execution_by_id
            and not item.execution_record_id.startswith("execution.bootstrap.")
            and execution_by_id[item.execution_record_id].cleanup_confirmed
            and execution_by_id[item.execution_record_id].coverage_delta_digest
            == item.coverage_delta_digest
            for item in evidence_feedback
        )
        _check(
            checks,
            "agent-oracle-coverage-evidence-present",
            execution_evidence_ok,
            f"evidence_feedback_count={len(evidence_feedback)}",
        )

        recovery = store.recover(campaign_id)
        uncommitted_sealed = store._db.execute(
            "SELECT COUNT(*) FROM candidate_work w LEFT JOIN settlement s "
            "ON s.work_id=w.work_id WHERE w.campaign_id=? AND "
            "json_extract(w.work_json, '$.state')='sealed' AND s.work_id IS NULL",
            (campaign_id,),
        ).fetchone()[0]
        _check(
            checks,
            "generation-boundary-recovery-clean",
            not recovery["resumable"]
            and not recovery["ambiguous"]
            and uncommitted_sealed == 0,
            f"recovery={recovery}; uncommitted_sealed={uncommitted_sealed}",
        )

        payload: dict[str, object] = {
            "schema_version": STAGE6_GATE_SCHEMA,
            "campaign_id": campaign_id,
            "database": str(db_path),
            "generation_index": state.lifecycle.counters.generation_index,
            "checks": checks,
            "passed": all(bool(item["passed"]) for item in checks),
        }
    payload["audit_digest"] = sha256_digest(payload)
    return payload


def write_two_generation_gate(
    *, db_path: Path, campaign_id: str, output: Path
) -> dict[str, object]:
    report = audit_two_generation_gate(db_path=db_path, campaign_id=campaign_id)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def audit_stage6_milestone(
    *, db_path: Path, campaign_id: str, target_generation: int
) -> dict[str, object]:
    """Classify a paid milestone without treating every clean exit as success."""

    if target_generation not in {2, 10, 20, 30, 50}:
        raise ValueError("target generation is not a Stage 6 milestone")
    with V2CampaignStore(db_path) as store:
        state = store.load_state(campaign_id)
        generation = state.lifecycle.counters.generation_index
        completion = state.lifecycle.completion_status
        completion_value = completion.value if completion is not None else None
        if completion_value == "saturated":
            result_kind = "saturated"
            passed = True
        elif completion_value is None and generation >= target_generation:
            result_kind = "target_reached"
            passed = True
        else:
            result_kind = completion_value or "target_not_reached"
            passed = False
        runtime_row = store._db.execute(
            "SELECT runtime_identity_digest FROM campaign_runtime_identity WHERE campaign_id=?",
            (campaign_id,),
        ).fetchone()
        payload: dict[str, object] = {
            "schema_version": STAGE6_MILESTONE_SCHEMA,
            "campaign_id": campaign_id,
            "target_generation": target_generation,
            "actual_generation": generation,
            "completion_status": completion_value,
            "result_kind": result_kind,
            "runtime_identity_digest": (
                runtime_row["runtime_identity_digest"] if runtime_row is not None else None
            ),
            "passed": passed and runtime_row is not None,
        }
    payload["audit_digest"] = sha256_digest(payload)
    return payload


def write_stage6_milestone(
    *, db_path: Path, campaign_id: str, target_generation: int, output: Path
) -> dict[str, object]:
    report = audit_stage6_milestone(
        db_path=db_path,
        campaign_id=campaign_id,
        target_generation=target_generation,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


@dataclass(frozen=True)
class _ArchiveSource:
    path: Path
    member: str


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _validate_sealed_mapping(
    payload: dict[str, Any], *, digest_field: str, label: str
) -> None:
    expected = payload.get(digest_field)
    actual = sha256_digest(
        {key: value for key, value in payload.items() if key != digest_field}
    )
    if expected != actual:
        raise ValueError(f"archived {label} digest differs")


def _checkpoint_success_database(path: Path) -> None:
    database = sqlite3.connect(path)
    try:
        integrity = database.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise ValueError("Campaign database integrity check failed")
        database.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        database.commit()
    finally:
        database.close()
    wal = path.with_name(path.name + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ValueError("Campaign WAL remains non-empty after checkpoint")


def verify_stage6_evidence_archive(path: Path) -> dict[str, object]:
    """Recompute every archived member and the companion archive checksum."""

    sidecar = path.with_name(path.name + ".sha256")
    if not path.is_file() or not sidecar.is_file():
        raise ValueError("archive or companion checksum is missing")
    fields = sidecar.read_text(encoding="ascii").strip().split()
    if len(fields) != 2 or fields[1] != path.name:
        raise ValueError("archive companion checksum has invalid format")
    if "sha256:" + fields[0] != _file_digest(path):
        raise ValueError("archive checksum differs")

    with tarfile.open(path, "r:gz") as archive:
        names = archive.getnames()
        if len(names) != len(set(names)):
            raise ValueError("archive contains duplicate members")
        manifest_member = archive.extractfile("stage6-evidence-manifest.json")
        if manifest_member is None:
            raise ValueError("archive manifest is missing")
        manifest = json.loads(manifest_member.read())
        expected_digest = manifest.pop("manifest_digest", None)
        if expected_digest != sha256_digest(manifest):
            raise ValueError("archive manifest digest differs")
        manifest["manifest_digest"] = expected_digest
        expected_names = {
            item["path"] for item in manifest["members"]
        } | {"stage6-evidence-manifest.json"}
        if set(names) != expected_names:
            raise ValueError("archive members differ from manifest")
        for item in manifest["members"]:
            member = archive.getmember(item["path"])
            stream = archive.extractfile(member)
            if stream is None or not member.isfile() or member.size != item["size"]:
                raise ValueError(f"archive member metadata differs: {item['path']}")
            digest = hashlib.sha256()
            while block := stream.read(1024 * 1024):
                digest.update(block)
            if "sha256:" + digest.hexdigest() != item["sha256"]:
                raise ValueError(f"archive member checksum differs: {item['path']}")
        _verify_archived_stage6_closure(archive, manifest)
    return manifest


def _json_member(archive: tarfile.TarFile, name: str) -> dict[str, Any]:
    stream = archive.extractfile(name)
    if stream is None:
        raise ValueError(f"required closure member is missing: {name}")
    value = json.loads(stream.read())
    if not isinstance(value, dict):
        raise ValueError(f"closure member is not an object: {name}")
    return value


def _without_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_none(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [_without_none(item) for item in value]
    return value


def _json_lines_member(archive: tarfile.TarFile, name: str) -> list[dict[str, Any]]:
    stream = archive.extractfile(name)
    if stream is None:
        raise ValueError(f"required closure member is missing: {name}")
    values: list[dict[str, Any]] = []
    for raw_line in stream.read().splitlines():
        if not raw_line.strip():
            continue
        value = json.loads(raw_line)
        if not isinstance(value, dict):
            raise ValueError(f"closure JSONL row is not an object: {name}")
        values.append(value)
    if not values:
        raise ValueError(f"closure JSONL member is empty: {name}")
    return values


def _verify_archived_stage6_closure(
    archive: tarfile.TarFile, manifest: dict[str, object]
) -> None:
    """Bind identities, database state, reports, and host evidence to one Campaign."""

    required = {
        "identity/stage6-model-lock.json",
        "identity/stage6-bootstrap.json",
        "identity/stage6-repair-plan.json",
        "identity/stage6-repair-application.json",
        "identity/stage6-stage.json",
        "identity/stage6-source-tree.json",
        "preflight/stage6-preflight.json",
        "preflight/stage6-server-host.json",
        "preflight/stage6-gpu-residency.json",
    }
    if manifest.get("outcome") == "success":
        required.update(
            {
                "campaign/campaign.sqlite3",
                "results/campaign-report.json",
                "results/stage6-campaign-progress.jsonl",
                "results/stage6-replay-report.json",
            }
        )
    names = set(archive.getnames())
    missing = required - names
    if missing:
        raise ValueError(f"archive closure members are missing: {sorted(missing)}")
    campaign_id = str(manifest["campaign_id"])
    lock = _json_member(archive, "identity/stage6-model-lock.json")
    bootstrap = _json_member(archive, "identity/stage6-bootstrap.json")
    repair = _json_member(archive, "identity/stage6-repair-application.json")
    repair_plan = _json_member(archive, "identity/stage6-repair-plan.json")
    stage = _json_member(archive, "identity/stage6-stage.json")
    source_tree = _json_member(archive, "identity/stage6-source-tree.json")
    preflight = _json_member(archive, "preflight/stage6-preflight.json")
    host = _json_member(archive, "preflight/stage6-server-host.json")
    gpu = _json_member(archive, "preflight/stage6-gpu-residency.json")
    lock_digest = lock.get("lock_digest")
    lock_payload = _without_none(
        {key: value for key, value in lock.items() if key != "lock_digest"}
    )
    if lock_digest != sha256_digest(lock_payload):
        raise ValueError("archived model lock digest differs")
    _validate_sealed_mapping(
        repair_plan, digest_field="lock_digest", label="repair plan"
    )
    _validate_sealed_mapping(
        repair, digest_field="receipt_digest", label="repair receipt"
    )
    _validate_sealed_mapping(
        source_tree, digest_field="source_tree_digest", label="source tree"
    )
    plan_roles = {
        item.get("role"): item
        for item in repair_plan.get("roles", [])
        if isinstance(item, dict)
    }
    receipt_roles = {
        item.get("role"): item
        for item in repair.get("roles", [])
        if isinstance(item, dict)
    }
    lock_roles = {
        item.get("role"): item
        for item in lock.get("roles", [])
        if isinstance(item, dict)
    }
    role_chain_matches = set(plan_roles) == set(receipt_roles) == set(lock_roles) == {
        "agent",
        "mutator",
    } and all(
        plan_roles[role].get("final_image_reference")
        == receipt_roles[role].get("image_reference")
        == lock_roles[role].get("image_reference")
        and receipt_roles[role].get("image_id") == lock_roles[role].get("image_id")
        and receipt_roles[role].get("image_build_receipt_digest")
        == lock_roles[role].get("image_build_receipt_digest")
        for role in ("agent", "mutator")
    )
    full_residency = gpu.get("full_residency", {})
    if (
        bootstrap.get("model_identity_digest") != lock.get("manifest_digest")
        or preflight.get("passed") is not True
        or preflight.get("model_digest") != lock.get("manifest_digest")
        or preflight.get("model_lock_digest") != lock_digest
        or repair.get("active_model_lock_digest") != lock_digest
        or repair.get("repair_lock_digest") != repair_plan.get("lock_digest")
        or repair_plan.get("model_digest") != lock.get("manifest_digest")
        or repair_plan.get("controller_image_reference")
        != lock.get("controller_image_reference")
        or repair_plan.get("controller_image_id") != lock.get("controller_image_id")
        or not role_chain_matches
        or stage.get("status") != "ready"
        or stage.get("source_revision") != repair_plan.get("source_revision")
        or stage.get("repair_lock_digest") != repair_plan.get("lock_digest")
        or source_tree.get("source_revision") != repair_plan.get("source_revision")
        or host.get("captured") is not True
        or gpu.get("passed") is not True
        or gpu.get("campaign_id") != "stage6-preflight"
        or full_residency.get("agent") is not True
        or full_residency.get("mutator") is not True
        or gpu.get("residual_observed_model_process_pids")
        or preflight.get("mutator_completed_before_agent") is not True
        or preflight.get("agent_successful_tool_exchange_count", 0) < 1
        or preflight.get("agent_model_decision_count", 0) < 2
        or preflight.get("agent_post_tool_decision_proved") is not True
    ):
        raise ValueError("archived Stage 6 identities or preflight evidence differ")
    if "campaign/campaign.sqlite3" not in names:
        return
    wal_members = tuple(
        item for item in manifest["members"]
        if item["path"] == "campaign/campaign.sqlite3-wal"
    )
    if manifest.get("outcome") == "success" and any(
        int(item["size"]) > 0 for item in wal_members
    ):
        raise ValueError("successful archive contains an uncheckpointed Campaign WAL")
    db_stream = archive.extractfile("campaign/campaign.sqlite3")
    if db_stream is None:
        raise ValueError("archived Campaign database is missing")
    with tempfile.TemporaryDirectory() as temporary:
        database_path = Path(temporary) / "campaign.sqlite3"
        database_path.write_bytes(db_stream.read())
        database = sqlite3.connect(database_path)
        database.row_factory = sqlite3.Row
        try:
            campaign = database.execute(
                "SELECT identity_digest, generation_index FROM campaign WHERE campaign_id=?",
                (campaign_id,),
            ).fetchone()
            runtime = database.execute(
                "SELECT runtime_identity_digest FROM campaign_runtime_identity WHERE campaign_id=?",
                (campaign_id,),
            ).fetchone()
        finally:
            database.close()
    if campaign is None or runtime is None:
        raise ValueError("archive database does not contain the declared Campaign identity")
    if runtime["runtime_identity_digest"] != lock_digest:
        raise ValueError("archive Campaign database and model identity differ")
    if "results/campaign-report.json" in names:
        report = _json_member(archive, "results/campaign-report.json")
        report_matches = (
            report.get("campaign_id") == campaign_id
            and report.get("identity_digest") == campaign["identity_digest"]
            and report.get("generation_index") == campaign["generation_index"]
        )
    else:
        report_matches = manifest.get("outcome") != "success"
    if not report_matches:
        raise ValueError("archive Campaign database, report, and model identity differ")
    milestone_names = tuple(
        name
        for name in names
        if name.startswith("results/milestone-to-") and name.endswith(".json")
    )
    if manifest.get("outcome") == "success":
        if not milestone_names:
            raise ValueError("successful archive has no milestone decision")
        progress_rows = _json_lines_member(
            archive, "results/stage6-campaign-progress.jsonl"
        )
        progress = progress_rows[-1]
        boundary_run = next(
            (
                index
                for index, row in enumerate(progress_rows)
                if row.get("mode") == "run"
                and row.get("requested_target") == 1
                and row.get("observed_generation") == 1
            ),
            None,
        )
        boundary_resume = next(
            (
                index
                for index, row in enumerate(progress_rows)
                if row.get("mode") == "resume"
                and row.get("requested_target") == 2
                and row.get("observed_generation") >= 2
            ),
            None,
        )
        if (
            boundary_run is None
            or boundary_resume is None
            or boundary_resume <= boundary_run
        ):
            raise ValueError("successful archive lacks the active 1-to-2 resume boundary")
        requested_target = progress.get("requested_target")
        milestone_name = f"results/milestone-to-{requested_target}.json"
        if milestone_name not in milestone_names:
            raise ValueError("successful archive lacks this invocation's milestone")
        milestone = _json_member(archive, milestone_name)
        if (
            milestone.get("campaign_id") != campaign_id
            or milestone.get("actual_generation") != campaign["generation_index"]
            or milestone.get("runtime_identity_digest") != lock_digest
            or milestone.get("passed") is not True
        ):
            raise ValueError("successful archive milestone does not match Campaign state")
        if (
            progress.get("campaign_id") != campaign_id
            or progress.get("observed_generation") != campaign["generation_index"]
            or progress.get("report_digest") != report.get("report_digest")
            or progress.get("completion_status") != report.get("completion_status")
            or progress.get("requested_target") != milestone.get("target_generation")
        ):
            raise ValueError("successful archive progress does not match Campaign state")
        replay_payload = _json_member(archive, "results/stage6-replay-report.json")
        try:
            replay = ReplayResult.model_validate(replay_payload)
        except ValueError as exc:
            raise ValueError("successful archive replay report is invalid") from exc
        if replay.status.value != "matched" or replay.container_removed is not True:
            raise ValueError("successful archive replay gate did not match cleanly")
        replay_parts = (replay.source_replay_id, replay.replay_run_id)
        if any(
            not value or value in {".", ".."} or any(char in value for char in "/\\:")
            for value in replay_parts
        ):
            raise ValueError("successful archive replay identity is invalid")
        replay_root = f"campaign/replays/{replay.source_replay_id}"
        manifest_name = f"{replay_root}/manifest.json"
        result_name = f"{replay_root}/runs/{replay.replay_run_id}/result.json"
        if manifest_name not in names or result_name not in names:
            raise ValueError(
                "successful archive replay result is not bound to archived run artifacts"
            )
        replay_manifest = _json_member(archive, manifest_name)
        archived_result = _json_member(archive, result_name)
        if (
            replay_manifest.get("replay_id") != replay.source_replay_id
            or (
                replay.source_trajectory_id is not None
                and replay_manifest.get("trajectory_id")
                != replay.source_trajectory_id
            )
            or archived_result != replay.model_dump(mode="json")
        ):
            raise ValueError(
                "successful archive replay report differs from archived run artifacts"
            )


def _tree_sources(root: Path, prefix: str) -> list[_ArchiveSource]:
    if not root.is_dir():
        raise ValueError(f"required evidence directory is missing: {root}")
    sources: list[_ArchiveSource] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"evidence archive refuses symlink: {path}")
        if path.is_file() and path.name != "stage6-evidence-manifest.json":
            sources.append(_ArchiveSource(path, f"{prefix}/{path.relative_to(root).as_posix()}"))
    return sources


def build_stage6_evidence_archive(
    *,
    campaign_id: str,
    outcome: str,
    campaign_root: Path,
    result_root: Path,
    model_lock: Path,
    bootstrap: Path,
    preflight: Path,
    repair_plan: Path,
    repair_receipt: Path,
    stage_record: Path,
    source_tree_identity: Path,
    server_host: Path,
    gpu_residency: Path,
    output: Path,
) -> dict[str, object]:
    """Create a normalized archive and a complete content manifest."""

    if outcome not in {"success", "failure"}:
        raise ValueError("outcome must be success or failure")
    sidecar = output.with_name(output.name + ".sha256")
    if output.exists() or sidecar.exists():
        raise ValueError("evidence archive output already exists")
    required_files = {
        "identity/stage6-model-lock.json": model_lock,
        "identity/stage6-bootstrap.json": bootstrap,
        "preflight/stage6-preflight.json": preflight,
        "identity/stage6-repair-plan.json": repair_plan,
        "identity/stage6-repair-application.json": repair_receipt,
        "identity/stage6-stage.json": stage_record,
        "identity/stage6-source-tree.json": source_tree_identity,
        "preflight/stage6-server-host.json": server_host,
        "preflight/stage6-gpu-residency.json": gpu_residency,
    }
    for path in required_files.values():
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"required evidence file is missing or unsafe: {path}")
    if outcome == "success" and not (campaign_root / "campaign.sqlite3").is_file():
        raise ValueError("successful archive requires campaign.sqlite3")
    if outcome == "success":
        _checkpoint_success_database(campaign_root / "campaign.sqlite3")

    sources = _tree_sources(campaign_root, "campaign")
    sources.extend(_tree_sources(result_root, "results"))
    sources.extend(_ArchiveSource(path, member) for member, path in required_files.items())
    members = [
        {
            "path": source.member,
            "size": source.path.stat().st_size,
            "sha256": _file_digest(source.path),
        }
        for source in sorted(sources, key=lambda item: item.member)
    ]
    manifest: dict[str, object] = {
        "schema_version": STAGE6_ARCHIVE_SCHEMA,
        "campaign_id": campaign_id,
        "outcome": outcome,
        "members": members,
    }
    manifest["manifest_digest"] = sha256_digest(manifest)
    manifest_bytes = (json.dumps(manifest, indent=2) + "\n").encode()
    result_root.mkdir(parents=True, exist_ok=True)
    (result_root / "stage6-evidence-manifest.json").write_bytes(manifest_bytes)

    output.parent.mkdir(parents=True, exist_ok=True)
    with (
        output.open("wb") as raw,
        gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as archive,
    ):
        for source in sorted(sources, key=lambda item: item.member):
            info = tarfile.TarInfo(source.member)
            info.size = source.path.stat().st_size
            info.mtime = info.uid = info.gid = 0
            info.uname = info.gname = ""
            with source.path.open("rb") as stream:
                archive.addfile(info, stream)
        info = tarfile.TarInfo("stage6-evidence-manifest.json")
        info.size = len(manifest_bytes)
        info.mtime = info.uid = info.gid = 0
        info.uname = info.gname = ""
        archive.addfile(info, io.BytesIO(manifest_bytes))
    archive_digest = _file_digest(output)
    sidecar.write_text(
        f"{archive_digest.removeprefix('sha256:')}  {output.name}\n", encoding="ascii"
    )
    verify_stage6_evidence_archive(output)
    return {**manifest, "archive": str(output), "archive_sha256": archive_digest}


__all__ = [
    "STAGE6_ARCHIVE_SCHEMA",
    "STAGE6_GATE_SCHEMA",
    "STAGE6_MILESTONE_SCHEMA",
    "audit_stage6_milestone",
    "audit_two_generation_gate",
    "build_stage6_evidence_archive",
    "verify_stage6_evidence_archive",
    "write_two_generation_gate",
    "write_stage6_milestone",
]
