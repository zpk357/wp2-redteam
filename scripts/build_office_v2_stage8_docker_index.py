from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sandbox.replay.digests import sha256_digest

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "clean_and_replay": ROOT
    / "reports/local-acceptance/office-v2-stage7-9/stage7-9-evidence.json",
    "four_entries": ROOT
    / "reports/local-acceptance/office-v2-stage7-10/stage7-10-evidence.json",
    "compound": ROOT
    / "reports/local-acceptance/office-v2-stage7-10/stage7-10-compound-evidence.json",
    "lifecycle": ROOT
    / "reports/local-acceptance/office-v2-stage7-11/stage7-11-evidence.json",
    "parameter_propagation": ROOT
    / "reports/local-acceptance/office-v2-stage8/stage8-e3-evidence.json",
}


def _load(path: Path) -> tuple[dict[str, Any], str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    digest = payload.pop("evidence_digest")
    if sha256_digest(payload) != digest:
        raise ValueError(f"Docker evidence digest is invalid: {path}")
    return payload, digest


def build_stage8_docker_index() -> dict[str, Any]:
    loaded = {name: _load(path) for name, path in SOURCES.items()}
    clean = loaded["clean_and_replay"][0]
    entries = loaded["four_entries"][0]
    compound = loaded["compound"][0]
    lifecycle = loaded["lifecycle"][0]
    parameter = loaded["parameter_propagation"][0]

    clean_by_case = {item["case_id"]: item for item in clean["episodes"]}
    entry_by_index_mode = {
        (item["index"], item["mode"]): item for item in entries["episodes"]
    }
    compound_by_mode = {item["mode"]: item for item in compound["episodes"]}
    lifecycle_by_mode = {item["mode"]: item for item in lifecycle["episodes"]}
    parameter_by_mode = {item["mode"]: item for item in parameter["episodes"]}

    world_digests = {
        clean["identity"]["world_digest"],
        entries["identity"]["world_digest"],
        parameter["identity"]["world_digest"],
    }
    if len(world_digests) != 1:
        raise ValueError("Docker evidence uses different Office V2 worlds")

    long_chain = clean_by_case["clean.t2.delta"]
    authorization = clean_by_case["clean.t9.apollo"]
    four_entry_pairs_pass = all(
        entry_by_index_mode[(index, "safe")]["state_changed"] is False
        and entry_by_index_mode[(index, "safe")]["completion"] == "none"
        and entry_by_index_mode[(index, "full")]["state_changed"] is True
        and entry_by_index_mode[(index, "full")]["completion"] == "full"
        for index in (7, 9, 10, 14)
    )
    compound_pass = (
        compound_by_mode["partial"]["completion"] == "partial"
        and compound_by_mode["full"]["completion"] == "full"
        and len(compound_by_mode["partial"]["realized_milestone_ids"])
        < len(compound_by_mode["full"]["realized_milestone_ids"])
    )
    parameter_pass = (
        parameter_by_mode["safe"]["state_changed"] is False
        and parameter_by_mode["full"]["completion"] == "full"
        and {"observed", "used"} <= set(parameter_by_mode["full"]["exposure"])
        and set(parameter_by_mode["full"]["realized_milestone_ids"])
        == {"milestone.calendar", "milestone.record", "milestone.notify"}
    )
    lifecycle_pass = (
        lifecycle_by_mode["timeout"]["error_code"] == "execution_timed_out"
        and lifecycle_by_mode["cancel"]["error_code"] == "execution_cancelled"
        and lifecycle["cleanup"]["remaining_owned_containers"] == 0
        and lifecycle["cleanup"]["remaining_owned_volumes"] == 0
    )
    isolation_pass = (
        clean["isolation"]["remaining_owned_containers"] == 0
        and clean["isolation"]["remaining_owned_volumes"] == 0
        and all(
            item == {
                "mount_types": ["volume"],
                "network_mode": "none",
                "privileged": False,
                "read_only": True,
                "user": "10001:10001",
            }
            for item in clean["isolation"]["profiles"]
        )
    )
    replay_pass = all(
        item["recording_complete"] is True
        and item["replay_status"] == "matched"
        and item["all_checkpoints_matched"] is True
        and item["source_behavior_digest"] == item["replay_behavior_digest"]
        and item["source_final_state_digest"] == item["replay_final_state_digest"]
        and item["replay_container_removed"] is True
        for item in clean["episodes"]
    )

    coverage = {
        "clean_cross_domain_chain": {
            "passed": long_chain["utility_disposition"] == "completed"
            and long_chain["tool_call_count"] >= 5
            and long_chain["explicit_submit_count"] == 1,
            "source": "clean_and_replay",
            "case_id": long_chain["case_id"],
        },
        "trusted_authorization_chain": {
            "passed": authorization["utility_disposition"] == "completed"
            and authorization["delegation_grant_count"] == 1,
            "source": "clean_and_replay",
            "case_id": authorization["case_id"],
        },
        "four_entry_safe_full_contrasts": {
            "passed": four_entry_pairs_pass,
            "source": "four_entries",
            "episode_count": len(entries["episodes"]),
        },
        "compound_partial_full": {
            "passed": compound_pass,
            "source": "compound",
            "case_id": compound_by_mode["full"]["case_id"],
        },
        "single_parameter_propagation": {
            "passed": parameter_pass,
            "source": "parameter_propagation",
            "case_id": parameter_by_mode["full"]["case_id"],
        },
        "strict_replay": {"passed": replay_pass, "source": "clean_and_replay"},
        "isolation_profile": {"passed": isolation_pass, "source": "clean_and_replay"},
        "timeout_cancel_cleanup": {"passed": lifecycle_pass, "source": "lifecycle"},
    }
    payload: dict[str, Any] = {
        "schema_version": "office-v2-stage8-docker-index-v1",
        "evidence_class": "existing_docker_evidence_index",
        "decision": {
            "episodes_rerun": False,
            "reason": (
                "Existing digest-verified artifacts already distinguish every "
                "Stage 8.4 requirement."
            ),
            "real_model_claimed": False,
        },
        "identity": {
            "world_digest": next(iter(world_digests)),
            "source_evidence_digests": {
                name: digest for name, (_, digest) in loaded.items()
            },
            "image_identities": sorted(
                {
                    clean["identity"]["image_id"],
                    entries["identity"]["image_id"],
                    lifecycle["identity"]["image_id"],
                    parameter["identity"]["image_id"],
                }
            ),
        },
        "coverage": coverage,
        "all_required_docker_evidence_present": all(
            item["passed"] for item in coverage.values()
        ),
        "limitations": {
            "deterministic_provider_only": True,
            "real_qwen_used": False,
            "coverage_or_mutation_used": False,
            "current_global_docker_inventory_claimed": False,
        },
    }
    validate_stage8_docker_index(payload)
    payload["evidence_digest"] = sha256_digest(payload)
    return payload


def validate_stage8_docker_index(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != "office-v2-stage8-docker-index-v1":
        raise ValueError("Stage 8 Docker index schema is invalid")
    if not payload.get("coverage") or any(
        item.get("passed") is not True for item in payload["coverage"].values()
    ):
        raise ValueError("Stage 8 Docker evidence requirement failed")
    if payload.get("all_required_docker_evidence_present") is not True:
        raise ValueError("Stage 8 Docker evidence index is incomplete")
    if payload.get("decision", {}).get("episodes_rerun") is not False:
        raise ValueError("Stage 8 Docker reuse decision is invalid")
    if payload.get("limitations") != {
        "deterministic_provider_only": True,
        "real_qwen_used": False,
        "coverage_or_mutation_used": False,
        "current_global_docker_inventory_claimed": False,
    }:
        raise ValueError("Stage 8 Docker limitations are invalid")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", type=Path)
    args = parser.parse_args()
    if args.check is not None:
        payload = json.loads(args.check.read_text(encoding="utf-8"))
        digest = payload.pop("evidence_digest")
        validate_stage8_docker_index(payload)
        if sha256_digest(payload) != digest:
            raise ValueError("Stage 8 Docker index digest is invalid")
        print(digest)
        return 0
    payload = build_stage8_docker_index()
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
