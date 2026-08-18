#!/usr/bin/env python3
"""Build portable tuning facts and unlabeled golden-set candidates from one Campaign."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--campaign-export", type=Path, required=True)
    parser.add_argument("--coverage-export", type=Path, required=True)
    parser.add_argument("--mutation-export", type=Path, required=True)
    parser.add_argument("--trajectory-dir", type=Path, default=Path("data/trajectories"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--taxonomy-path", type=Path, required=True)
    parser.add_argument("--risk-scope-path", type=Path, required=True)
    parser.add_argument("--operator-registry-path", type=Path, required=True)
    parser.add_argument("--fuzzer-config", type=Path, required=True)
    parser.add_argument("--target-profile-path", type=Path, required=True)
    return parser.parse_args()


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def value_digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + chr(10),
        encoding="utf-8",
    )


def write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    payload = chr(10).join(
        json.dumps(value, ensure_ascii=False, sort_keys=True) for value in values
    )
    path.write_text(payload + (chr(10) if payload else ""), encoding="utf-8")


def git_provenance() -> dict[str, Any]:
    def run(*arguments: str) -> bytes | None:
        try:
            return subprocess.check_output(
                ["git", *arguments],
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError):
            return None

    revision = run("rev-parse", "HEAD")
    status = run("status", "--porcelain=v1")
    diff = run("diff", "--binary", "HEAD")
    return {
        "revision": revision.decode().strip() if revision else None,
        "dirty": bool(status and status.strip()),
        "status_digest": (
            "sha256:" + hashlib.sha256(status).hexdigest() if status is not None else None
        ),
        "diff_digest": ("sha256:" + hashlib.sha256(diff).hexdigest() if diff is not None else None),
    }


def resolve_trajectory(
    raw_path: str | None,
    execution_id: str | None,
    trajectory_root: Path,
) -> Path | None:
    root = trajectory_root.resolve()
    candidates: list[Path] = []
    if raw_path:
        raw = Path(raw_path)
        candidates.append(raw if raw.is_absolute() else Path.cwd() / raw)
        candidates.append(root / raw.name)
    if execution_id:
        candidates.append(root / f"{execution_id}.jsonl")
        candidates.append(root / f"{execution_id}.jsonl.partial")
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved != root and root not in resolved.parents:
            continue
        if resolved.is_file():
            return resolved
    return None


def read_events(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    output = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            output.append(json.loads(line))
    return output


def root_seed(seed_id: str | None, seeds: dict[str, dict[str, Any]]) -> str | None:
    current = seed_id
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        seed = seeds.get(current)
        if not seed or not seed.get("parent_seed_id"):
            return current
        current = seed["parent_seed_id"]
    return current


def final_answer(events: list[dict[str, Any]]) -> str | None:
    for event in reversed(events):
        if event.get("event_type") == "execution_finished":
            value = event.get("data", {}).get("final_answer")
            return str(value) if value is not None else None
    return None


def tool_name(event: dict[str, Any]) -> str | None:
    data = event.get("data") or {}
    value = data.get("tool_name") or data.get("name")
    if value:
        return str(value)
    source = str(event.get("source") or "")
    if source.startswith("tool."):
        return source.removeprefix("tool.")
    return None


def evidence_projection(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    retained = []
    for event in events:
        event_type = str(event.get("event_type") or "")
        if (
            event_type.startswith("tool_")
            or event_type.startswith("security_")
            or event_type.startswith("execution_")
            or event_type in {"model_start", "model_end", "policy_decision"}
        ):
            retained.append(
                {
                    "sequence": event.get("sequence"),
                    "event_type": event_type,
                    "source": event.get("source"),
                    "data": event.get("data", {}),
                    "input_digest": event.get("input_digest"),
                    "output_digest": event.get("output_digest"),
                    "state_digest": event.get("state_digest"),
                }
            )
    return retained


def selection_reasons(record: dict[str, Any]) -> list[str]:
    coverage = record.get("coverage") or {}
    score = record.get("machine_signals", {}).get("rule_score") or {}
    reasons: list[str] = []
    if coverage.get("new_behavior_count", 0):
        reasons.append("new_behavior")
    if coverage.get("new_risk_categories"):
        reasons.append("new_risk_category")
    if coverage.get("risk_depth_changes"):
        reasons.append("risk_depth_increase")
    links = coverage.get("behavior_risk_links") or []
    if any(item.get("novelty_class") == "both_new" for item in links):
        reasons.append("new_behavior_risk_link")
    if record.get("mutation", {}).get("mutation_depth", 0) >= 2:
        reasons.append("second_generation_mutation")
    if record.get("behavior_summary", {}).get("tool_call_count", 0) >= 2:
        reasons.append("multi_tool")
    if record.get("behavior_summary", {}).get("model_step_count", 0) >= 2:
        reasons.append("multi_step")
    if score.get("verdict") == "safe":
        reasons.append("safe_control")
    if score.get("verdict") in {"blocked_attempt", "violation"}:
        reasons.append("rule_signaled_risk")
    if record.get("execution", {}).get("status") != "succeeded":
        reasons.append("incomplete_or_failed")
    targets = set(record.get("mutation", {}).get("target_risks") or [])
    actual = set(record.get("machine_signals", {}).get("risk_categories") or [])
    if targets and not targets.intersection(actual):
        reasons.append("target_not_observed")
    if coverage.get("risk_scope_exceeded"):
        reasons.append("risk_scope_exceeded")
    return sorted(set(reasons or ["baseline_or_no_gain"]))


def build_records(
    campaign: dict[str, Any],
    coverage: dict[str, Any],
    mutation: dict[str, Any],
    trajectory_root: Path,
) -> list[dict[str, Any]]:
    seeds = {item["seed_id"]: item for item in campaign.get("seeds", [])}
    candidates = {item["mutation_id"]: item for item in mutation.get("candidates", [])}
    batches = {item["batch_id"]: item for item in mutation.get("batches", [])}
    candidate_batch: dict[str, str] = {}
    for batch in batches.values():
        for candidate in batch.get("accepted", []):
            candidate_batch[candidate["mutation_id"]] = batch["batch_id"]
    provider_calls: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for call in mutation.get("provider_calls", []):
        provider_calls[call["batch_id"]].append(call)

    attempts_by_work: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for attempt in campaign.get("work_attempts", []):
        attempts_by_work[attempt["work_item_id"]].append(attempt)
    observations = {item["work_item_id"]: item for item in campaign.get("observations", [])}
    corpus = {item["work_item_id"]: item for item in campaign.get("corpus", [])}
    coverage_results = {item["trajectory_id"]: item for item in coverage.get("results", [])}
    energy_by_seed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for decision in campaign.get("energy_decisions", []):
        energy_by_seed[decision["seed_id"]].append(decision)

    initial_seed_by_template: dict[str, dict[str, Any]] = {}
    for seed in seeds.values():
        template_id = (seed.get("case", {}).get("metadata") or {}).get("template_id")
        if template_id:
            initial_seed_by_template[str(template_id)] = seed

    records: list[dict[str, Any]] = []
    ordered_work = sorted(
        campaign.get("work_items", []),
        key=lambda item: (
            item.get("dispatch_sequence") is None,
            item.get("dispatch_sequence") or 0,
            item["work_item_id"],
        ),
    )
    for work in ordered_work:
        attempts = sorted(
            attempts_by_work.get(work["work_item_id"], []),
            key=lambda item: item["attempt"],
        )
        latest_attempt = attempts[-1] if attempts else None
        outcome = latest_attempt.get("outcome") if latest_attempt else None
        execution_id = (outcome or {}).get("execution_id") or work.get("execution_id")
        trajectory_id = (
            work.get("trajectory_id") or (outcome or {}).get("trajectory_id") or execution_id
        )
        trajectory_path = resolve_trajectory(
            work.get("trajectory_path") or (outcome or {}).get("trajectory_path"),
            execution_id,
            trajectory_root,
        )
        events = read_events(trajectory_path)
        event_model_sources = sorted(
            {
                str(event.get("source"))
                for event in events
                if event.get("event_type") == "model_start"
            }
        )
        source = work.get("source") or {}
        candidate = candidates.get(source.get("candidate_id"))
        initial_seed = initial_seed_by_template.get(str(source.get("case_id")))
        parent_seed_id = work.get("parent_seed_id")
        lineage_seed = parent_seed_id or (initial_seed or {}).get("seed_id")
        lineage_root = root_seed(lineage_seed, seeds)
        prompt = None
        if candidate:
            prompt = candidate.get("prompt")
            if prompt is None and candidate.get("fork"):
                prompt = candidate["fork"].get("content")
        elif initial_seed:
            prompt = initial_seed.get("case", {}).get("prompt")
        batch_id = candidate_batch.get(candidate["mutation_id"]) if candidate else None
        result = coverage_results.get(trajectory_id)
        score = (outcome or {}).get("score")
        tool_events = [
            event for event in events if str(event.get("event_type", "")).startswith("tool_")
        ]
        tool_sequence = [
            name
            for event in tool_events
            if event.get("event_type") == "tool_call"
            if (name := tool_name(event))
        ]
        risk_hits = (result or {}).get("risk_hits") or []
        risk_categories = sorted({item["category_id"] for item in risk_hits})
        evidence_sequences = sorted(
            {
                evidence["event_sequence"]
                for hit in risk_hits
                for evidence in hit.get("evidence", [])
                if evidence.get("event_sequence") is not None
            }
        )
        execution_status = (outcome or {}).get("execution_status") or work.get("status")
        record = {
            "schema_version": SCHEMA_VERSION,
            "campaign_id": campaign["manifest"]["campaign_id"],
            "work_item_id": work["work_item_id"],
            "dispatch_sequence": work.get("dispatch_sequence"),
            "source_kind": source.get("kind"),
            "case_id": source.get("case_id") or (initial_seed or {}).get("case", {}).get("case_id"),
            "root_seed_id": lineage_root,
            "parent_seed_id": parent_seed_id,
            "prompt": prompt,
            "prompt_sha256": (
                candidate.get("prompt_sha256")
                if candidate
                else (initial_seed or {}).get("prompt_sha256")
            ),
            "mutation": {
                "mutation_id": candidate.get("mutation_id") if candidate else None,
                "batch_id": batch_id,
                "mutation_depth": candidate.get("mutation_depth", 0) if candidate else 0,
                "operator_id": candidate.get("operator_id") if candidate else None,
                "operator_version": candidate.get("operator_version") if candidate else None,
                "target_risks": candidate.get("target_risks", [])
                if candidate
                else ((initial_seed or {}).get("case", {}).get("target_risks", [])),
                "target_depths": candidate.get("target_depths", {}) if candidate else {},
                "priority": candidate.get("mutation_priority") if candidate else None,
                "priority_components": (
                    candidate.get("priority_components", {}) if candidate else {}
                ),
                "provider_calls": provider_calls.get(batch_id, []),
            },
            "execution": {
                "attempts": attempts,
                "execution_id": execution_id,
                "trajectory_id": trajectory_id,
                "trajectory_relative_path": (
                    str(trajectory_path.relative_to(Path.cwd()))
                    if trajectory_path and Path.cwd() in trajectory_path.parents
                    else str(trajectory_path)
                    if trajectory_path
                    else None
                ),
                "trajectory_digest": file_digest(trajectory_path) if trajectory_path else None,
                "trace_complete": bool(
                    events
                    and str(events[-1].get("event_type", "")).startswith("execution_")
                    and not str(trajectory_path).endswith(".partial")
                ),
                "status": execution_status,
                "duration_ms": (outcome or {}).get("duration_ms"),
                "container_removed": (outcome or {}).get("container_removed"),
                "model_sources": event_model_sources,
                "final_answer": final_answer(events),
            },
            "behavior_summary": {
                "model_step_count": sum(
                    event.get("event_type") == "model_start" for event in events
                ),
                "tool_call_count": sum(event.get("event_type") == "tool_call" for event in events),
                "tool_sequence": tool_sequence,
                "termination_event": events[-1].get("event_type") if events else None,
            },
            "coverage": result,
            "observation": observations.get(work["work_item_id"]),
            "corpus_entry": corpus.get(work["work_item_id"]),
            "energy_decisions": energy_by_seed.get(parent_seed_id or lineage_seed, []),
            "machine_signals": {
                "rule_score": score,
                "risk_categories": risk_categories,
                "risk_evidence_sequences": evidence_sequences,
            },
            "evidence_events": evidence_projection(events),
        }
        record["selection_reasons"] = selection_reasons(record)
        record["record_digest"] = value_digest(record)
        records.append(record)
    return records


def aggregate_metrics(
    records: list[dict[str, Any]],
    campaign: dict[str, Any],
    coverage: dict[str, Any],
    mutation: dict[str, Any],
) -> dict[str, Any]:
    operator: dict[str, Counter[str]] = defaultdict(Counter)
    target: dict[str, Counter[str]] = defaultdict(Counter)
    candidate_by_id = {item["mutation_id"]: item for item in mutation.get("candidates", [])}
    for candidate in candidate_by_id.values():
        operator[candidate["operator_id"]]["accepted_candidates"] += 1
        for category in candidate.get("target_risks", []):
            target[category]["accepted_candidates"] += 1
    for rejection in mutation.get("rejections", []):
        operator[rejection.get("operator_id") or "<unknown>"]["rejected_candidates"] += 1
    for record in records:
        mutation_info = record["mutation"]
        operator_id = mutation_info.get("operator_id")
        actual = set(record["machine_signals"]["risk_categories"])
        gain = bool(
            (record.get("coverage") or {}).get("new_behavior_count")
            or (record.get("coverage") or {}).get("new_risk_categories")
            or (record.get("coverage") or {}).get("risk_depth_changes")
        )
        if operator_id:
            metrics = operator[operator_id]
            metrics["executed"] += int(record["execution"]["execution_id"] is not None)
            metrics["succeeded"] += int(record["execution"]["status"] == "succeeded")
            metrics["coverage_gain"] += int(gain)
            metrics["corpus_promotions"] += int(record.get("corpus_entry") is not None)
            metrics["second_generation"] += int(mutation_info.get("mutation_depth", 0) >= 2)
        for category in mutation_info.get("target_risks", []):
            metrics = target[category]
            metrics["executed"] += int(record["execution"]["execution_id"] is not None)
            metrics["target_observed"] += int(category in actual)
            metrics["coverage_gain"] += int(gain)

    provider_calls = mutation.get("provider_calls", [])
    successful_calls = [item for item in provider_calls if item.get("status") == "succeeded"]
    coverage_progression = []
    for record in records:
        result = record.get("coverage")
        if not result:
            continue
        coverage_progression.append(
            {
                "dispatch_sequence": record.get("dispatch_sequence"),
                "trajectory_id": result.get("trajectory_id"),
                "new_behavior_count": result.get("new_behavior_count", 0),
                "new_risk_categories": result.get("new_risk_categories", []),
                "risk_depth_changes": result.get("risk_depth_changes", []),
                "combined_delta": result.get("combined_delta", 0),
                "cumulative_behavior_count": result.get("cumulative_behavior_count", 0),
                "cumulative_risk_count": result.get("cumulative_risk_count", 0),
                "intent_coverage": result.get("intent_coverage", 0),
                "behavior_coverage": result.get("behavior_coverage", 0),
                "impact_coverage": result.get("impact_coverage", 0),
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "campaign_id": campaign["manifest"]["campaign_id"],
        "campaign": campaign.get("campaign"),
        "coverage_snapshot": coverage.get("snapshot"),
        "mutation_snapshot": mutation.get("snapshot"),
        "operator_metrics": {
            key: dict(sorted(value.items())) for key, value in sorted(operator.items())
        },
        "target_risk_metrics": {
            key: dict(sorted(value.items())) for key, value in sorted(target.items())
        },
        "rejection_reasons": dict(
            sorted(Counter(item["reason"] for item in mutation.get("rejections", [])).items())
        ),
        "failure_codes": dict(
            sorted(
                Counter(
                    record["execution"]["attempts"][-1]["outcome"].get("error_code")
                    for record in records
                    if record["execution"]["attempts"]
                    and record["execution"]["attempts"][-1].get("outcome")
                    and record["execution"]["attempts"][-1]["outcome"].get("error_code")
                ).items()
            )
        ),
        "provider": {
            "call_count": len(provider_calls),
            "successful_calls": len(successful_calls),
            "failed_calls": len(provider_calls) - len(successful_calls),
            "prompt_tokens": sum(item.get("prompt_eval_count") or 0 for item in successful_calls),
            "completion_tokens": sum(item.get("eval_count") or 0 for item in successful_calls),
            "wall_latency_ms": sum(item.get("latency_ms") or 0 for item in provider_calls),
            "model_total_duration_ns": sum(
                item.get("total_duration_ns") or 0 for item in successful_calls
            ),
        },
        "coverage_progression": coverage_progression,
        "selection_reason_counts": dict(
            sorted(
                Counter(
                    reason for record in records for reason in record.get("selection_reasons", [])
                ).items()
            )
        ),
        "model_sources": sorted(
            {
                source
                for record in records
                for source in record["execution"].get("model_sources", [])
            }
        ),
    }


def golden_candidate(record: dict[str, Any]) -> dict[str, Any] | None:
    execution = record["execution"]
    if not record.get("prompt") or not execution.get("trajectory_digest"):
        return None
    root = record.get("root_seed_id") or record["work_item_id"]
    sample = {
        "schema_version": SCHEMA_VERSION,
        "sample_id": value_digest(
            {
                "trajectory_digest": execution["trajectory_digest"],
                "work_item_id": record["work_item_id"],
            }
        ),
        "group_id": value_digest({"root_seed_id": root}),
        "campaign_id": record["campaign_id"],
        "work_item_id": record["work_item_id"],
        "case_id": record.get("case_id"),
        "source_kind": record.get("source_kind"),
        "prompt": record["prompt"],
        "prompt_sha256": record.get("prompt_sha256"),
        "mutation": record["mutation"],
        "execution": {
            "execution_id": execution.get("execution_id"),
            "trajectory_id": execution.get("trajectory_id"),
            "trajectory_digest": execution.get("trajectory_digest"),
            "trace_complete": execution.get("trace_complete"),
            "status": execution.get("status"),
            "final_answer": execution.get("final_answer"),
        },
        "behavior_summary": record["behavior_summary"],
        "evidence_events": record["evidence_events"],
        "selection_reasons": record["selection_reasons"],
        "machine_signals": record["machine_signals"],
        "human_annotation": {
            "status": "unlabeled",
            "annotator_a": None,
            "annotator_b": None,
            "adjudication": None,
        },
    }
    sample["input_digest"] = value_digest(
        {
            key: value
            for key, value in sample.items()
            if key not in {"human_annotation", "input_digest"}
        }
    )
    return sample


def main() -> int:
    args = parse_args()
    campaign = load_json(args.campaign_export)
    coverage = load_json(args.coverage_export)
    mutation = load_json(args.mutation_export)
    manifest = campaign.get("manifest") or {}
    if manifest.get("campaign_id") != args.campaign_id:
        raise SystemExit("campaign export identity mismatch")
    if (coverage.get("snapshot") or {}).get("campaign_id") != args.campaign_id:
        raise SystemExit("coverage export identity mismatch")
    if (mutation.get("snapshot") or {}).get("campaign_id") != args.campaign_id:
        raise SystemExit("mutation export identity mismatch")
    if not manifest.get("agent_model_digest") or manifest.get("mutation_provider") != "ollama":
        raise SystemExit("learning export requires locked real Agent and Ollama mutator")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    records = build_records(campaign, coverage, mutation, args.trajectory_dir)
    expected_source = f"ollama:{manifest['agent_model_name']}@{manifest['agent_model_digest']}"
    unexpected_sources = sorted(
        {
            source
            for record in records
            for source in record["execution"].get("model_sources", [])
            if source != expected_source
        }
    )
    if unexpected_sources:
        raise SystemExit(f"unexpected trajectory model sources: {unexpected_sources}")

    metrics = aggregate_metrics(records, campaign, coverage, mutation)
    candidates = [
        candidate for record in records if (candidate := golden_candidate(record)) is not None
    ]
    outcomes_path = args.output_dir / "candidate-outcomes.jsonl"
    metrics_path = args.output_dir / "learning-metrics.json"
    golden_path = args.output_dir / "golden-set-candidates.jsonl"
    manifest_path = args.output_dir / "golden-set-candidate-manifest.json"
    write_jsonl(outcomes_path, records)
    write_json(metrics_path, metrics)
    write_jsonl(golden_path, candidates)

    source_files = [
        args.campaign_export,
        args.coverage_export,
        args.mutation_export,
        args.taxonomy_path,
        args.risk_scope_path,
        args.operator_registry_path,
        args.fuzzer_config,
        args.target_profile_path,
    ]
    candidate_manifest = {
        "schema_version": SCHEMA_VERSION,
        "dataset_kind": "unlabeled_golden_set_candidate_pool",
        "generated_at": datetime.now(UTC).isoformat(),
        "campaign_id": args.campaign_id,
        "candidate_count": len(candidates),
        "work_record_count": len(records),
        "is_golden_set": False,
        "labels_generated_by_model": False,
        "human_label_required": True,
        "recommended_minimum_real_trajectory_pool": 100,
        "recommended_final_human_labeled_size": {"minimum": 50, "maximum": 80},
        "candidate_pool_size_gate_passed": len(candidates) >= 100,
        "grouping_rule": "all descendants of one root seed share group_id",
        "model_lock": {
            "agent_model_name": manifest.get("agent_model_name"),
            "agent_model_digest": manifest.get("agent_model_digest"),
            "mutation_model_name": manifest.get("mutation_model_name"),
            "mutation_model_digest": manifest.get("mutation_model_digest"),
            "agent_image": manifest.get("agent_image"),
            "agent_image_digest": manifest.get("agent_image_digest"),
            "agent_model_runtime_image": manifest.get("agent_model_runtime_image"),
            "agent_model_runtime_digest": manifest.get("agent_model_runtime_digest"),
        },
        "configuration_locks": {
            "config_digest": manifest.get("config_digest"),
            "taxonomy_version": manifest.get("taxonomy_version"),
            "taxonomy_digest": manifest.get("taxonomy_digest"),
            "risk_scope_version": manifest.get("risk_scope_version"),
            "risk_scope_digest": manifest.get("risk_scope_digest"),
            "mutation_registry_version": manifest.get("mutation_registry_version"),
            "mutation_registry_digest": manifest.get("mutation_registry_digest"),
        },
        "git": git_provenance(),
        "input_files": {
            str(path): {"bytes": path.stat().st_size, "sha256": file_digest(path)}
            for path in source_files
        },
        "outputs": {
            path.name: {"bytes": path.stat().st_size, "sha256": file_digest(path)}
            for path in (outcomes_path, metrics_path, golden_path)
        },
        "selection_reason_counts": metrics["selection_reason_counts"],
        "labeling_warning": (
            "Coverage, RuleBasedScorer, and Corpus fields are machine signals only; "
            "they must not prefill human gold labels."
        ),
    }
    write_json(manifest_path, candidate_manifest)
    print(
        json.dumps(
            {
                "campaign_id": args.campaign_id,
                "records": len(records),
                "unlabeled_candidates": len(candidates),
                "candidate_pool_size_gate_passed": len(candidates) >= 100,
                "output_dir": str(args.output_dir),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
