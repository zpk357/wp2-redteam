from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sandbox.replay.digests import sha256_bytes, sha256_digest
from sandbox.scenarios.office_v2.cli_entry import office_v2_public_cases

ROOT = Path(__file__).resolve().parents[1]
STAGE8 = ROOT / "reports/local-acceptance/office-v2-stage8"
SOURCES = {
    "acceptance_map": STAGE8 / "stage8-acceptance-map.json",
    "story_freeze": STAGE8 / "stage8-story-freeze.json",
    "example_binding_audit": STAGE8 / "stage8-example-binding-audit.json",
    "example_definitions": STAGE8 / "stage8-example-definitions.json",
    "e3_evidence": STAGE8 / "stage8-e3-evidence.json",
    "structure_evidence": STAGE8 / "office-v2-stage8-structure-evidence.json",
    "docker_index": STAGE8 / "office-v2-stage8-docker-index.json",
    "v1_disposition": STAGE8 / "office-v2-stage8-v1-disposition.json",
}


def _load(path: Path) -> tuple[dict[str, Any], str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    digest = payload.pop("evidence_digest", None)
    if digest is None:
        digest = sha256_bytes(path.read_bytes())
    elif sha256_digest(payload) != digest:
        raise ValueError(f"Stage 8 source digest is invalid: {path}")
    return payload, digest


def build_stage8_evidence() -> dict[str, Any]:
    loaded = {name: _load(path) for name, path in SOURCES.items()}
    structure = loaded["structure_evidence"][0]
    docker_index = loaded["docker_index"][0]
    disposition = loaded["v1_disposition"][0]
    cases = office_v2_public_cases()
    clean_count = sum(item.kind == "clean" for item in cases)
    attack_count = sum(item.kind == "attack" for item in cases)

    gates = {
        "structural_contracts": structure["all_required_gates_passed"] is True,
        "docker_execution_contracts": docker_index[
            "all_required_docker_evidence_present"
        ]
        is True,
        "formal_v1_entry_disabled": disposition["formal_entry_disabled"] is True
        and disposition["legacy_production_entry_active"] is False,
        "public_v2_catalog": clean_count == 24
        and attack_count == 24
        and len({item.public_id for item in cases}) == 48,
        "world_identity_consistent": structure["identity"]["world_digest"]
        == docker_index["identity"]["world_digest"],
    }
    payload: dict[str, Any] = {
        "schema_version": "office-v2-stage8-evidence-v1",
        "scenario_id": "office-world-v2.0",
        "status": "frozen" if all(gates.values()) else "blocked",
        "gates": gates,
        "identity": {
            **structure["identity"],
            "story_freeze_digest": loaded["story_freeze"][1],
            "docker_index_digest": loaded["docker_index"][1],
            "formal_entry_disposition_digest": loaded["v1_disposition"][1],
        },
        "public_entry": {
            "command": "trace-redteam scenario run",
            "clean_case_count": clean_count,
            "representative_case_count": attack_count,
            "total_case_count": len(cases),
            "execution_contract": "OfficeV2ExecutionEnvelope",
            "records_replay_manifest": True,
            "legacy_live_entry_enabled": False,
            "legacy_source_and_history_deleted": False,
        },
        "coverage_mutation_handoff": {
            "allowed_facts": [
                "resolved case and resource bindings",
                "model events without self-reported risk truth",
                "tool calls and tool results",
                "policy decisions",
                "state deltas and final state",
                "observation and parameter provenance",
                "Oracle utility, milestone and violation evidence",
                "termination and cleanup facts",
            ],
            "forbidden_as_coverage_truth": [
                "model self-reported risk or operator labels",
                "prompt wording novelty by itself",
                "a fixed expected tool sequence",
                "LLM Judge output",
                "legacy V1 matrix results",
            ],
        },
        "source_evidence_digests": {
            name: digest for name, (_, digest) in loaded.items()
        },
        "limitations": {
            "deterministic_provider_evidence_reused": True,
            "real_qwen_run_performed": False,
            "coverage_or_mutation_implemented": False,
            "judge_or_active_learning_implemented": False,
            "docker_rerun_performed_for_entry_wiring": False,
        },
    }
    validate_stage8_evidence(payload)
    payload["evidence_digest"] = sha256_digest(payload)
    return payload


def validate_stage8_evidence(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != "office-v2-stage8-evidence-v1":
        raise ValueError("Stage 8 evidence schema is invalid")
    gates = payload.get("gates")
    if not isinstance(gates, dict) or not gates or not all(gates.values()):
        raise ValueError("Stage 8 freeze gate failed")
    if payload.get("status") != "frozen":
        raise ValueError("Office V2 scenario is not frozen")
    entry = payload.get("public_entry", {})
    if entry.get("total_case_count") != 48:
        raise ValueError("Office V2 public catalog is incomplete")
    if entry.get("legacy_live_entry_enabled") is not False:
        raise ValueError("Legacy live entry remains enabled")
    handoff = payload.get("coverage_mutation_handoff", {})
    if not handoff.get("allowed_facts") or not handoff.get(
        "forbidden_as_coverage_truth"
    ):
        raise ValueError("Coverage and mutation handoff is incomplete")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", type=Path)
    args = parser.parse_args()
    if args.check is not None:
        payload = json.loads(args.check.read_text(encoding="utf-8"))
        digest = payload.pop("evidence_digest")
        validate_stage8_evidence(payload)
        if sha256_digest(payload) != digest:
            raise ValueError("Stage 8 evidence digest is invalid")
        print(digest)
        return 0
    payload = build_stage8_evidence()
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
