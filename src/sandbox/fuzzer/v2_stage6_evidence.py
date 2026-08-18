"""Stage 6 two-generation gate and complete Campaign evidence archive."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sandbox.replay.digests import sha256_digest

from .v2_campaign_store import V2CampaignStore
from .v2_feedback import NextGenerationFeedback
from .v2_orchestrator import GenerationClosureReceipt, GenerationDecision

STAGE6_GATE_SCHEMA = "office-v2-stage6-two-generation-gate-v1"
STAGE6_ARCHIVE_SCHEMA = "office-v2-stage6-evidence-archive-v1"


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
    return manifest


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
    output: Path,
) -> dict[str, object]:
    """Create a normalized archive and a complete content manifest."""

    if outcome not in {"success", "failure"}:
        raise ValueError("outcome must be success or failure")
    required_files = {
        "identity/stage6-model-lock.json": model_lock,
        "identity/stage6-bootstrap.json": bootstrap,
        "preflight/stage6-preflight.json": preflight,
    }
    for path in required_files.values():
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"required evidence file is missing or unsafe: {path}")
    if outcome == "success" and not (campaign_root / "campaign.sqlite3").is_file():
        raise ValueError("successful archive requires campaign.sqlite3")

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
    output.with_name(output.name + ".sha256").write_text(
        f"{archive_digest.removeprefix('sha256:')}  {output.name}\n", encoding="ascii"
    )
    verify_stage6_evidence_archive(output)
    return {**manifest, "archive": str(output), "archive_sha256": archive_digest}


__all__ = [
    "STAGE6_ARCHIVE_SCHEMA",
    "STAGE6_GATE_SCHEMA",
    "audit_two_generation_gate",
    "build_stage6_evidence_archive",
    "verify_stage6_evidence_archive",
    "write_two_generation_gate",
]
