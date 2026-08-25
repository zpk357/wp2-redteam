#!/usr/bin/env python3
"""Build a read-only coverage UI snapshot from a verified Office V2 archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "src"))

from sandbox.coverage.v2_contracts import build_v2_candidate_batch_baseline  # noqa: E402
from sandbox.coverage.v2_episode_coverage import (  # noqa: E402
    V2CandidateEpisode,
    V2CoverageSnapshot,
    evaluate_v2_candidate_batch,
)
from sandbox.coverage.v2_input import v2_coverage_input_from_recording  # noqa: E402
from sandbox.coverage.v2_risk_catalog import V2_RISK_CATALOG  # noqa: E402
from sandbox.fuzzer.v2_campaign_loop import build_v2_coverage_artifact  # noqa: E402
from sandbox.replay.digests import sha256_digest  # noqa: E402
from sandbox.replay.manifest import verify_manifest  # noqa: E402
from sandbox.replay.models import ReplayManifest  # noqa: E402
from sandbox.scenarios.office_v2.attack_objectives import (  # noqa: E402
    ATTACK_OBJECTIVE_BY_ID,
)

SCHEMA_VERSION = "office-v2-coverage-visualization-v3"

_RISK_OBJECTIVE_BY_ID = {
    item.objective_id: item for item in V2_RISK_CATALOG.objectives
}


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_path(root: Path, relative: object) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError("archive metadata contains an invalid relative path")
    target = (root / relative).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("archive metadata path escapes the archive root") from exc
    return target


def _verify_core_files(root: Path, metadata: dict[str, Any]) -> None:
    pairs = (
        ("campaign_database", "campaign_database_sha256"),
        ("campaign_report", "campaign_report_sha256"),
        ("bootstrap", "bootstrap_sha256"),
        ("model_lock", "model_lock_sha256"),
    )
    for path_field, digest_field in pairs:
        path = _archive_path(root, metadata.get(path_field))
        expected = metadata.get(digest_field)
        if not path.is_file() or _sha256(path) != expected:
            raise ValueError(f"archive integrity mismatch: {path_field}")


def _connect_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{path.as_posix()}?mode=ro&immutable=1",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    return connection


def _rows(connection: sqlite3.Connection, table: str, field: str) -> list[dict[str, Any]]:
    result = []
    for row in connection.execute(f"SELECT {field} FROM {table} ORDER BY rowid"):
        value = json.loads(row[field])
        if not isinstance(value, dict):
            raise ValueError(f"{table}.{field} contains a non-object")
        result.append(value)
    return result


def _one_by(items: list[dict[str, Any]], field: str, value: object, label: str) -> dict[str, Any]:
    matches = [item for item in items if item.get(field) == value]
    if len(matches) != 1:
        raise ValueError(f"expected one {label} for {field}={value!r}, found {len(matches)}")
    return matches[0]


def _artifact(data_root: Path, reference: object) -> bytes:
    if not isinstance(reference, dict):
        raise ValueError("ReplayManifest artifact reference is missing")
    path = _archive_path(data_root / "artifacts", reference.get("relative_path"))
    payload = path.read_bytes()
    expected = reference.get("sha256")
    if len(payload) != reference.get("size_bytes") or "sha256:" + _sha256(path) != expected:
        raise ValueError(f"ReplayManifest artifact mismatch: {expected}")
    return payload


def _json_artifact(data_root: Path, reference: object) -> Any:
    return json.loads(_artifact(data_root, reference))


def _ndjson_artifact(data_root: Path, reference: object) -> list[dict[str, Any]]:
    rows = []
    for line in _artifact(data_root, reference).splitlines():
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("NDJSON artifact contains a non-object row")
        rows.append(value)
    return rows


def _load_manifests(data_root: Path) -> dict[str, tuple[ReplayManifest, dict[str, Any]]]:
    manifests: dict[str, tuple[ReplayManifest, dict[str, Any]]] = {}
    for path in sorted((data_root / "replays").glob("*/manifest.json")):
        raw = _json(path)
        manifest = ReplayManifest.model_validate(raw)
        verify_manifest(manifest)
        if manifest.manifest_digest in manifests:
            raise ValueError("archive repeats a ReplayManifest digest")
        manifests[manifest.manifest_digest] = (manifest, raw)
    if not manifests:
        raise ValueError("archive contains no ReplayManifest")
    return manifests


def _seed_content(seed: dict[str, Any]) -> str:
    payloads = seed.get("payload_specs") or []
    return "\n\n".join(str(item.get("content", "")) for item in payloads).strip()


def _seed_projection(seed: dict[str, Any]) -> dict[str, Any]:
    payloads = seed.get("payload_specs") or []
    first = payloads[0] if payloads else {}
    intent = seed.get("origin_intent") or {}
    objective_id = intent.get("objective_id")
    risk_objective = _RISK_OBJECTIVE_BY_ID.get(objective_id)
    return {
        "id": seed.get("seed_id"),
        "label": objective_id or seed.get("seed_id"),
        "objective_id": objective_id,
        "primary_risk_family": (
            risk_objective.classification.primary_scheduling_family.value
            if risk_objective is not None
            else None
        ),
        "risk_facets": (
            [item.value for item in risk_objective.classification.risk_facets]
            if risk_objective is not None
            else []
        ),
        "carrier": first.get("carrier_kind")
        or (seed.get("carrier_recipe") or {}).get("carrier_kind"),
        "field_path": first.get("field_path"),
        "content": _seed_content(seed),
        "source": "bootstrap" if not seed.get("parent_seed_id") else "campaign_promotion",
        "generation_depth": seed.get("generation_depth", 0),
        "operator_history": seed.get("operator_history") or [],
    }


def _risk_catalog_projection(seeds: list[dict[str, Any]]) -> dict[str, Any]:
    seed_ids_by_objective: dict[str, list[str]] = {}
    for seed in seeds:
        objective_id = (seed.get("origin_intent") or {}).get("objective_id")
        if objective_id not in _RISK_OBJECTIVE_BY_ID:
            raise ValueError(f"seed has no frozen Office V2 risk objective: {seed.get('seed_id')}")
        seed_ids_by_objective.setdefault(objective_id, []).append(str(seed["seed_id"]))

    objectives = []
    for risk_objective in V2_RISK_CATALOG.objectives:
        objective = ATTACK_OBJECTIVE_BY_ID[risk_objective.objective_id]
        objectives.append(
            {
                "id": objective.objective_id,
                "title": objective.title,
                "risk_category_ids": list(objective.risk_category_ids),
                "primary_risk_family": (
                    risk_objective.classification.primary_scheduling_family.value
                ),
                "risk_facets": [
                    item.value for item in risk_objective.classification.risk_facets
                ],
                "milestone_ids": [
                    item.milestone_id for item in risk_objective.milestones
                ],
                "seed_ids": sorted(seed_ids_by_objective.get(objective.objective_id, [])),
            }
        )

    families = []
    for family in V2_RISK_CATALOG.families:
        family_objectives = [
            item for item in objectives if item["primary_risk_family"] == family.value
        ]
        families.append(
            {
                "id": family.value,
                "objective_ids": [item["id"] for item in family_objectives],
                "seed_ids": sorted(
                    seed_id
                    for item in family_objectives
                    for seed_id in item["seed_ids"]
                ),
            }
        )
    return {"families": families, "objectives": objectives}


def _frontier_projection(
    *,
    state: dict[str, Any],
    allocation: dict[str, Any],
) -> dict[str, Any]:
    frontier_kind = allocation["frontier_kind"]
    frontier_field = (
        "risk_frontiers" if frontier_kind == "risk" else "behavior_frontiers"
    )
    frontier = _one_by(
        state["frontiers"][frontier_field],
        "frontier_id",
        allocation["frontier_id"],
        f"{frontier_kind} frontier",
    )
    candidate_seed_ids = sorted(
        {
            entry["seed_id"]
            for entry in state["corpus"]["entries"]
            if allocation["frontier_id"] in entry["frontier_ids"]
        }
    )
    if allocation["parent_seed_id"] not in candidate_seed_ids:
        raise ValueError("selected parent seed is not compatible with its frontier")
    state_seed_by_id = {
        seed["seed_id"]: seed for seed in state["corpus"]["seeds"]
    }

    projection = {
        "frontier_kind": frontier_kind,
        "frontier_id": frontier["frontier_id"],
        "candidate_seed_ids": candidate_seed_ids,
        "candidate_seeds": [
            _seed_projection(state_seed_by_id[seed_id])
            for seed_id in candidate_seed_ids
        ],
        "selected_parent_seed_id": allocation["parent_seed_id"],
    }
    if frontier_kind == "risk":
        objective_id = frontier["objective_id"]
        risk_objective = _RISK_OBJECTIVE_BY_ID[objective_id]
        family_id = risk_objective.classification.primary_scheduling_family.value
        family_objective_ids = [
            item.objective_id
            for item in V2_RISK_CATALOG.objectives
            if item.classification.primary_scheduling_family.value == family_id
        ]
        family_seed_ids = sorted(
            seed["seed_id"]
            for seed in state["corpus"]["seeds"]
            if (seed.get("origin_intent") or {}).get("objective_id")
            in family_objective_ids
        )
        projection.update(
            {
                "primary_risk_family": family_id,
                "risk_facets": [
                    item.value for item in risk_objective.classification.risk_facets
                ],
                "objective_id": objective_id,
                "target_milestone_id": frontier["target_milestone_id"],
                "family_objective_ids": family_objective_ids,
                "family_seed_ids": family_seed_ids,
                "family_seeds": [
                    _seed_projection(state_seed_by_id[seed_id])
                    for seed_id in family_seed_ids
                ],
            }
        )
    else:
        projection.update(
            {
                "behavior_gap_kind": frontier["behavior_gap_kind"],
                "feature_family": frontier["feature_family"],
                "related_objective_id": frontier.get("related_objective_id"),
            }
        )
    return projection


def _evidence_sequences(refs: object) -> list[int]:
    sequences = {
        item.sequence
        for item in refs or ()
        if getattr(item, "sequence", None) is not None
    }
    return sorted(sequences)


def _feature_projection(feature: Any) -> dict[str, Any]:
    dimensions = [f"{item.name}={item.value}" for item in feature.dimensions]
    return {
        "id": feature.feature_key_digest,
        "kind": feature.kind.value,
        "value": " · ".join(dimensions),
        "dimensions": [item.model_dump(mode="json") for item in feature.dimensions],
        "is_new": True,
        "evidence_sequences": _evidence_sequences(feature.evidence_refs),
    }


def _risk_projection(context: Any) -> dict[str, Any]:
    return {
        "id": context.context_key_digest,
        "entry_kind": context.entry_kind,
        "source_domain": context.source_domain,
        "sink_domain": context.sink_domain,
        "sink_action": context.sink_action,
        "carrier": context.carrier,
        "recipient_kind": context.recipient_kind,
        "authorization_branch": context.authorization_branch,
        "planned": context.planned_or_unexpected == "planned",
        "outcome": context.outcome.value,
        "objective_id": context.objective_id,
        "milestone_id": context.milestone_id,
        "leakage_proof_grade": context.leakage_proof_grade.value,
        "evidence_sequences": _evidence_sequences(context.evidence_refs),
    }


def _tool_path(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = [event for event in events if event.get("event_type") == "tool_result"]
    used: set[int] = set()
    path = []
    for event in events:
        if event.get("event_type") != "tool_call":
            continue
        data = event.get("data") or {}
        invocation_id = data.get("invocation_id") or data.get("call_id")
        matched_index = next(
            (
                index
                for index, result in enumerate(results)
                if index not in used
                and (
                    invocation_id is None
                    or (result.get("data") or {}).get("invocation_id") == invocation_id
                    or (result.get("data") or {}).get("call_id") == invocation_id
                )
            ),
            None,
        )
        result = results[matched_index] if matched_index is not None else None
        if matched_index is not None:
            used.add(matched_index)
        result_data = (result or {}).get("data") or {}
        path.append(
            {
                "step": len(path) + 1,
                "tool_name": data.get("tool_name")
                or data.get("name")
                or result_data.get("tool_name"),
                "outcome": result_data.get("status") or result_data.get("outcome") or "recorded",
                "evidence_sequences": [
                    value
                    for value in (event.get("sequence"), (result or {}).get("sequence"))
                    if isinstance(value, int)
                ],
            }
        )
    return path


def _final_answer(events: list[dict[str, Any]]) -> str:
    for event in reversed(events):
        data = event.get("data") or {}
        for key in ("final_answer", "final_response", "response", "content"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def _actual_prompt(data_root: Path, manifest_raw: dict[str, Any]) -> str:
    value = _json_artifact(data_root, manifest_raw.get("prompt"))
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for field in ("instruction", "prompt", "content"):
            if isinstance(value.get(field), str):
                return value[field]
    raise ValueError("recorded Agent prompt has an unsupported shape")


def _campaign_status(report: dict[str, Any], requested_generations: int) -> str:
    completion = report.get("completion_status")
    if completion:
        return str(completion)
    if report.get("generation_index") == requested_generations:
        return "completed"
    return "paused"


def build_snapshot(
    *,
    archive_root: Path,
    campaign_id: str | None = None,
    archive_sha256: str | None = None,
) -> dict[str, Any]:
    root = archive_root.resolve()
    metadata_paths = sorted(root.glob("reports/server-stage6/*/archive-metadata.json"))
    if campaign_id is not None:
        metadata_paths = [path for path in metadata_paths if path.parent.name == campaign_id]
    if len(metadata_paths) != 1:
        raise ValueError("archive must contain exactly one selected Campaign metadata file")
    metadata = _json(metadata_paths[0])
    campaign_id = str(metadata["campaign_id"])
    _verify_core_files(root, metadata)

    report_path = _archive_path(root, metadata["campaign_report"])
    report = _json(report_path)
    if report.get("campaign_id") != campaign_id:
        raise ValueError("Campaign report belongs to a different Campaign")
    expected_report_digest = report.get("report_digest")
    report_body = {key: value for key, value in report.items() if key != "report_digest"}
    if expected_report_digest != sha256_digest(report_body):
        raise ValueError("Campaign report digest does not match")

    database = _archive_path(root, metadata["campaign_database"])
    data_root = database.parent
    connection = _connect_read_only(database)
    try:
        campaign_row = connection.execute(
            "SELECT * FROM campaign WHERE campaign_id = ?", (campaign_id,)
        ).fetchone()
        if campaign_row is None:
            raise ValueError("Campaign database does not contain the selected Campaign")
        identity = json.loads(campaign_row["identity_json"])
        lifecycle = json.loads(campaign_row["lifecycle_json"])
        decisions = _rows(connection, "generation_decision", "decision_json")
        handoffs = _rows(connection, "execution_handoff", "handoff_json")
        preparations = _rows(connection, "mutation_preparation", "preparation_json")
        works = _rows(connection, "candidate_work", "work_json")
        receipts = _rows(connection, "attempt_receipt", "receipt_json")
        settlements = _rows(connection, "settlement", "settlement_json")
        feedback_rows = _rows(connection, "generation_feedback", "feedback_json")
        state_rows = _rows(connection, "campaign_state_snapshot", "state_json")
    finally:
        connection.close()

    states = {item["state_digest"]: item for item in state_rows}
    initial_state = states[decisions[0]["input_state_digest"]]
    initial_seeds = initial_state["corpus"]["seeds"]
    initial_seeds_by_id = {item["seed_id"]: item for item in initial_seeds}
    risk_catalog = _risk_catalog_projection(initial_seeds)
    manifests = _load_manifests(data_root)
    model_lock = _json(_archive_path(root, metadata["model_lock"]))
    roles = {item["role"]: item for item in model_lock["roles"]}
    findings_by_fact = {
        item["canonical_fact_digest"]: item for item in report.get("findings", [])
    }

    preparations_by_allocation = {
        item["plan"]["allocation"]["base_allocation"]["generation_allocation_id"]: item
        for item in preparations
    }
    handoffs_by_allocation = {item["generation_allocation_id"]: item for item in handoffs}
    works_by_allocation = {item["generation_allocation_id"]: item for item in works}
    settlements_by_work = {item["work_id"]: item for item in settlements}
    receipts_by_id = {item["attempt_id"]: item for item in receipts}
    feedback_by_generation = {item["generation_index"]: item for item in feedback_rows}

    generations = []
    runtime_identity: tuple[str, str, str] | None = None
    for decision in decisions:
        index = decision["generation_index"]
        number = index + 1
        allocation = decision["allocation"]
        decision_state = states[decision["input_state_digest"]]
        frontier_selection = _frontier_projection(
            state=decision_state,
            allocation=allocation,
        )
        allocation_id = allocation["generation_allocation_id"]
        preparation = preparations_by_allocation[allocation_id]
        handoff = handoffs_by_allocation[allocation_id]
        work = works_by_allocation[allocation_id]
        settlement = settlements_by_work[work["work_id"]]
        receipt = receipts_by_id[work["attempt_ids"][-1]]
        next_state = states[settlement["next_campaign_state_digest"]]
        execution = _one_by(
            next_state["corpus"]["execution_records"],
            "execution_record_id",
            settlement["execution_record_id"],
            "execution record",
        )
        manifest, manifest_raw = manifests[execution["manifest_digest"]]
        current_runtime = (
            str(manifest.metadata["producer_runtime_kind"]),
            str(manifest.metadata["producer_runtime_version"]),
            str(manifest.metadata["producer_runtime_composition_digest"]),
        )
        if runtime_identity is None:
            runtime_identity = current_runtime
        elif current_runtime != runtime_identity:
            raise ValueError("Campaign generations use different producer Runtime identities")

        oracle_payload = _artifact(data_root, manifest_raw["office_v2_oracle"])
        recording_payload = _artifact(data_root, manifest_raw["office_v2_recording_state"])
        coverage_input = v2_coverage_input_from_recording(
            manifest,
            oracle_artifact_payload=oracle_payload,
            recording_state_payload=recording_payload,
            container_removed=True,
        )
        coverage_artifact = build_v2_coverage_artifact(coverage_input)
        facts = coverage_artifact.episode_facts
        baseline = V2CoverageSnapshot.model_validate(
            states[decision["input_state_digest"]]["coverage"]
        )
        candidate_id = handoff["materialized_candidate_id"]
        batch = build_v2_candidate_batch_baseline(
            campaign_id=campaign_id,
            candidate_set_id=f"candidate-set.{candidate_id}",
            candidate_set_digest=sha256_digest({"candidate_id": candidate_id}),
            candidate_ids=(candidate_id,),
            baseline_snapshot_digest=baseline.snapshot_digest,
        )
        evaluated = evaluate_v2_candidate_batch(
            batch_baseline=batch,
            baseline_snapshot=baseline,
            candidates=(V2CandidateEpisode(candidate_id=candidate_id, episode_facts=facts),),
        )
        delta = evaluated.deltas[0]
        if (
            delta.delta_digest != settlement["coverage_delta_digest"]
            or evaluated.next_snapshot.snapshot_digest
            != settlement["next_coverage_snapshot_digest"]
        ):
            raise ValueError(f"generation {number} Coverage settlement does not reproduce")

        preparation_candidate = preparation["parsed_candidate"]
        candidate_content = preparation_candidate["slot_values"][0][1]
        prompt = _actual_prompt(data_root, manifest_raw)
        if prompt != candidate_content:
            raise ValueError(f"generation {number} recorded prompt differs from candidate")
        events = _ndjson_artifact(data_root, manifest_raw["events"])
        if not events or events[-1].get("event_type") != "execution_finished":
            raise ValueError(f"generation {number} does not contain a complete Episode")

        parent_seed = initial_seeds_by_id.get(allocation["parent_seed_id"])
        if parent_seed is None:
            parent_seed = _one_by(
                states[decision["input_state_digest"]]["corpus"]["seeds"],
                "seed_id",
                allocation["parent_seed_id"],
                "parent seed",
            )
        derived_seed_id = preparation["materialized_candidate"]["seed_id"]
        derived_seed = _one_by(
            next_state["corpus"]["seeds"], "seed_id", derived_seed_id, "derived seed"
        )
        corpus_entry = _one_by(
            next_state["corpus"]["entries"],
            "corpus_entry_id",
            settlement["corpus_entry_id"],
            "corpus entry",
        )
        finding = findings_by_fact.get(facts.canonical_fact_digest)
        new_behavior_keys = set(delta.new_primary_behavior_features)
        new_risk_keys = set(delta.new_risk_contexts)
        behavior_features = [
            _feature_projection(item)
            for item in facts.behavior.primary_features
            if item.feature_key_digest in new_behavior_keys
        ]
        risk_contexts = [
            _risk_projection(item)
            for item in facts.risk_context_cells
            if item.context_key_digest in new_risk_keys
        ]
        behavior_features.sort(key=lambda item: (item["kind"], item["value"], item["id"]))
        risk_contexts.sort(key=lambda item: item["id"])
        if len(behavior_features) != len(new_behavior_keys) or len(risk_contexts) != len(
            new_risk_keys
        ):
            raise ValueError(f"generation {number} Coverage detail is incomplete")

        feedback = feedback_by_generation[number]
        operator_allocation = preparation["plan"]["allocation"]["operator_allocation"]
        validation = preparation["validation"]
        failed_checks = [item["reason_code"] for item in validation["checks"] if not item["passed"]]
        validation_reasons = failed_checks or [
            f"{len(validation['checks'])}-layer-host-validation-passed"
        ]
        delivery = preparation["materialized_candidate"]["delivered_payloads"][0]
        agent_costs = receipt["costs"]
        mutator_tokens = (
            preparation["outcome"]["actual_input_tokens"]
            + preparation["outcome"]["actual_output_tokens"]
        )
        disposition = "risk_seed" if corpus_entry["seed_kind"] == "risk" else "exploration_seed"
        generations.append(
            {
                "number": number,
                "internal_decision_index": index,
                "settlement_kind": "candidate_settlement",
                "status": "committed",
                "decision": {
                    "digest": decision["decision_digest"],
                    "input_feedback_digest": decision["input_feedback_digest"],
                    "input_feedback_kind": (
                        "initial_baseline" if index == 0 else "previous_generation"
                    ),
                    "selected_parent_seed_id": allocation["parent_seed_id"],
                    "supporting_execution_id": allocation["supporting_execution_record_id"],
                    "frontier_kind": allocation["frontier_kind"],
                    "frontier_id": allocation["frontier_id"],
                    "target": allocation["frontier_id"],
                    "frontier_cells": [preparation["brief"]["frontier_description"]],
                    "frontier_selection": frontier_selection,
                    "reason_codes": [*decision["reason_codes"], *allocation["reason_codes"]],
                    "score_components": allocation["score_components"],
                },
                "mutation": {
                    "parent_seed_id": parent_seed["seed_id"],
                    "parent_seed": _seed_projection(parent_seed),
                    "parent_content": _seed_content(parent_seed),
                    "candidate_id": preparation_candidate["candidate_digest"],
                    "candidate_content": candidate_content,
                    "target": allocation["frontier_id"],
                    "carrier": derived_seed["carrier_recipe"]["carrier_kind"],
                    "field_path": derived_seed["payload_specs"][0]["field_path"],
                    "operator_families": operator_allocation["selected_operator_families"],
                    "operator_plan": {
                        "steps": [
                            {
                                "order": operator_index,
                                "family": family,
                                "reason": " · ".join(operator_allocation["reason_codes"]),
                                "changed_fields": preparation["plan"]["changed_field_paths"],
                                "status": "applied",
                            }
                            for operator_index, family in enumerate(
                                operator_allocation["selected_operator_families"], start=1
                            )
                        ]
                    },
                    "changed_fields": preparation["plan"]["changed_field_paths"],
                    "validation": {
                        "status": validation["disposition"],
                        "reason_codes": validation_reasons,
                        "checks": validation["checks"],
                    },
                    "provider_attempts": [
                        {
                            "attempt": item["attempt_index"],
                            "status": item["state"],
                            "response_digest": item["response_digest"],
                            "tokens": item["input_tokens"] + item["output_tokens"],
                        }
                        for item in preparation["provider_attempts"]
                    ],
                },
                "agent_input": {
                    "execution_id": events[0]["execution_id"],
                    "task_instruction": prompt,
                    "candidate_delivery": {
                        "resource_type": derived_seed["carrier_recipe"]["carrier_kind"],
                        "resource_id": delivery["resource_id"],
                        "field_path": delivery["field_path"],
                        "content": candidate_content,
                    },
                },
                "episode": {
                    "execution_id": events[0]["execution_id"],
                    "scenario_case_id": manifest.case_id,
                    "trajectory_id": manifest.trajectory_id,
                    "replay_id": manifest.replay_id,
                    "manifest_digest": manifest.manifest_digest,
                    "duration_ms": agent_costs["elapsed_ms"],
                    "tokens": {"total": agent_costs["agent_tokens"]},
                    "final_answer": _final_answer(events),
                    "events": events,
                },
                "coverage": {
                    "cumulative": {
                        "primary_behavior": len(
                            evaluated.next_snapshot.primary_behavior_feature_keys
                        ),
                        "risk_contexts": len(evaluated.next_snapshot.risk_context_keys),
                    },
                    "delta": {
                        "primary_behavior": len(behavior_features),
                        "risk_contexts": len(risk_contexts),
                    },
                    "delta_digest": delta.delta_digest,
                    "snapshot_digest": evaluated.next_snapshot.snapshot_digest,
                    "tool_path": _tool_path(events),
                    "behavior_features": behavior_features,
                    "risk_contexts": risk_contexts,
                },
                "seed_promotion": {
                    "disposition": disposition,
                    "parent_eligible": True,
                    "seed_id": derived_seed_id,
                    "corpus_entry_id": corpus_entry["corpus_entry_id"],
                    "finding_id": finding["finding_key"] if finding else None,
                    "reason_codes": corpus_entry["promotion_reasons"],
                },
                "feedback_output": {
                    "digest": feedback["feedback_digest"],
                    "gap_kind": feedback["gap_kind"],
                    "uncovered_targets": [],
                    "summary": (
                        f"本代 CoverageDelta {feedback['coverage_delta_digest']} 已封存；"
                        f"下一代调度输入类型为 {feedback['gap_kind']}。"
                    ),
                    "reason_codes": feedback["reason_codes"],
                },
                "costs": {
                    "agent_tokens": agent_costs["agent_tokens"],
                    "mutator_tokens": mutator_tokens,
                    "elapsed_ms": agent_costs["elapsed_ms"],
                },
            }
        )

    if len(generations) != metadata["completed_generations"]:
        raise ValueError("metadata generation count differs from Campaign database")
    if runtime_identity is None:
        raise ValueError("Campaign has no runtime identity")
    budget = report["budget"]["consumed"]
    first = generations[0]
    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "kind": "server_campaign_archive",
            "integrity_status": "verified_archive",
            "is_server_data": True,
            "notice": "真实服务器 Qwen Campaign 归档的只读覆盖率变异展示。",
            "generated_at": metadata["created_at"],
            "source_revision": metadata["source_revision"],
            "archive_sha256": archive_sha256,
            "report_digest": report["report_digest"],
            "model_lock_digest": model_lock["lock_digest"],
        },
        "selected_campaign_id": campaign_id,
        "campaigns": [
            {
                "id": campaign_id,
                "title": f"真实 Qwen Campaign / {len(generations)} 代",
                "identity_digest": identity["identity_digest"],
                "runtime": {
                    "kind": runtime_identity[0],
                    "label": "Office V2 LangGraph Agent",
                    "version": runtime_identity[1],
                    "composition_digest": runtime_identity[2],
                },
                "model": {
                    "name": model_lock["model_name"],
                    "identity_digest": model_lock["manifest_digest"],
                    "agent_role_digest": roles["agent"]["role_digest"],
                    "mutator_role_digest": roles["mutator"]["role_digest"],
                },
                "status": _campaign_status(report, metadata["completed_generations"]),
                "phase": report["phase"],
                "requested_generations": metadata["completed_generations"],
                "completed_generations": len(generations),
                "valid_committed_episodes": lifecycle["counters"]["valid_committed_episodes"],
                "invalid_or_failed_attempts": lifecycle["counters"]["invalid_or_failed_attempts"],
                "elapsed_ms": budget["elapsed_ms"],
                "tokens": {
                    "agent": budget["agent_tokens"],
                    "mutator": budget["mutator_tokens"],
                    "total": budget["agent_tokens"] + budget["mutator_tokens"],
                },
                "baseline": {
                    "scenario_case_id": first["episode"]["scenario_case_id"],
                    "initial_state_digest": first["decision"]["input_feedback_digest"]
                    or decisions[0]["input_state_digest"],
                    "task_instruction": (
                        "第一代从冻结种子池选择父种子，再由真实 Mutator "
                        "生成实际 Agent 输入。"
                    ),
                    "selection_method": "frozen_scheduler_allocation",
                    "seed_pool": [_seed_projection(item) for item in initial_seeds],
                    "risk_catalog": risk_catalog,
                    "g1_selection": {
                        "parent_seed_id": first["decision"]["selected_parent_seed_id"],
                        "supporting_execution_id": first["decision"]["supporting_execution_id"],
                        "frontier_id": first["decision"]["frontier_id"],
                        "frontier_selection": first["decision"]["frontier_selection"],
                        "allocation_digest": decisions[0]["allocation"]["allocation_digest"],
                        "reason_codes": first["decision"]["reason_codes"],
                    },
                },
                "generations": generations,
            }
        ],
    }
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--campaign-id")
    parser.add_argument("--archive-sha256")
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY / "coverage_ui" / "data" / "latest.json",
    )
    args = parser.parse_args()
    snapshot = build_snapshot(
        archive_root=args.archive_root,
        campaign_id=args.campaign_id,
        archive_sha256=args.archive_sha256,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"coverage-ui-snapshot={args.output.resolve()}")
    print(f"campaign={snapshot['selected_campaign_id']}")
    print(f"generations={snapshot['campaigns'][0]['completed_generations']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
