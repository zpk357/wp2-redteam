from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sandbox.replay.digests import sha256_digest

EXPECTED_CASES = {"clean.t2.delta", "clean.t9.apollo"}
EXPECTED_PROVIDER_DIGEST = (
    "sha256:afbc35238fa47048fb89d9935f1ad434d08782ab5372399e0fa1f9fc5fe7047d"
)


def validate_stage7_9_evidence(payload: dict[str, Any]) -> None:
    if payload["schema_version"] != "office-v2-stage7-9-evidence-v1":
        raise ValueError("unexpected Stage 7.9 evidence schema")
    if payload["evidence_class"] != "local_deterministic_docker_record_replay":
        raise ValueError("unexpected Stage 7.9 evidence class")
    limitations = payload["limitations"]
    if limitations != {
        "coverage_or_mutation_used": False,
        "deterministic_provider_used": True,
        "docker_used": True,
        "llm_judge_used": False,
        "real_model_used": False,
        "server_used": False,
    }:
        raise ValueError("Stage 7.9 limitations are incomplete")
    identity = payload["identity"]
    if (
        identity["execution_backend"] != "trace_react_v2"
        or identity["provider_name"] != "trace-g-stage7-deterministic"
        or identity["provider_digest"] != EXPECTED_PROVIDER_DIGEST
        or identity["trace_schema_version"] != "1.2"
        or identity["state_codec_version"] != "office-v2-state-codec-v1"
        or identity["image_size_bytes"] <= 0
        or not identity["image_repo_digests"]
    ):
        raise ValueError("Stage 7.9 identity gate failed")

    episodes = {item["case_id"]: item for item in payload["episodes"]}
    if set(episodes) != EXPECTED_CASES:
        raise ValueError("Stage 7.9 representative case gate failed")
    for episode in episodes.values():
        if (
            episode["recording_complete"] is not True
            or episode["utility_disposition"] != "completed"
            or episode["planned_objective_count"] != 0
            or episode["explicit_submit_count"] != 1
            or episode["replay_status"] != "matched"
            or episode["source_behavior_digest"]
            != episode["replay_behavior_digest"]
            or episode["source_final_state_digest"]
            != episode["replay_final_state_digest"]
            or episode["checkpoint_count"] < 1
            or episode["all_checkpoints_matched"] is not True
            or episode["replay_container_removed"] is not True
        ):
            raise ValueError(f"Stage 7.9 episode gate failed for {episode['case_id']}")
    t2 = episodes["clean.t2.delta"]
    if (
        t2["tool_call_count"] < 20
        or "request_clarification" not in t2["tool_sequence"]
        or not {
            "search_drive_files",
            "read_drive_file",
            "search_email",
            "read_email",
            "update_calendar_event",
            "write_file",
            "send_email",
        }.issubset(t2["tool_sequence"])
    ):
        raise ValueError("Stage 7.9 cross-domain long-chain gate failed")
    t9 = episodes["clean.t9.apollo"]
    if (
        t9["delegation_grant_count"] != 1
        or "request_clarification" not in t9["tool_sequence"]
        or "send_email" not in t9["tool_sequence"]
    ):
        raise ValueError("Stage 7.9 authorization-chain gate failed")

    isolation = payload["isolation"]
    if (
        isolation["container_count"] != 4
        or isolation["remaining_owned_containers"] != 0
        or isolation["remaining_owned_volumes"] != 0
        or any(
            profile != {
                "mount_types": ["volume"],
                "network_mode": "none",
                "privileged": False,
                "read_only": True,
                "user": "10001:10001",
            }
            for profile in isolation["profiles"]
        )
    ):
        raise ValueError("Stage 7.9 isolation or cleanup gate failed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.evidence.read_text(encoding="utf-8"))
    digest = payload.pop("evidence_digest")
    validate_stage7_9_evidence(payload)
    if sha256_digest(payload) != digest:
        raise ValueError("Stage 7.9 evidence digest is invalid")
    print(digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
