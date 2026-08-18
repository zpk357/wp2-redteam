#!/usr/bin/env python3
"""Build a fail-closed Weeks 1-5 acceptance summary from server artifacts."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--result-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-golden-pool", action="store_true")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(f"required acceptance artifact is missing: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"expected a JSON object: {path}")
    return value


def all_true(checks: dict[str, bool]) -> bool:
    return bool(checks) and all(checks.values())


def main() -> int:
    args = parse_args()
    repository = Path.cwd().resolve()
    result_dir = args.result_dir.resolve()
    output = args.output.resolve()
    if repository not in result_dir.parents or repository not in output.parents:
        raise SystemExit("acceptance paths must remain inside the repository")

    agent = load_json(result_dir / "agent-run.json")
    replay = load_json(result_dir / "weeks-1-5" / "week2-validation.json")
    campaign = load_json(result_dir / "campaign-export.json")
    coverage = load_json(result_dir / "coverage-export.json")
    mutation = load_json(result_dir / "mutation-export.json")
    loop_validation = load_json(result_dir / "campaign-validation.json")
    learning = load_json(result_dir / "learning" / "learning-metrics.json")
    pool = load_json(result_dir / "learning" / "golden-set-candidate-manifest.json")

    manifest = campaign.get("manifest") or {}
    if manifest.get("campaign_id") != args.campaign_id:
        raise SystemExit("campaign export identity mismatch")
    model_name = manifest.get("agent_model_name")
    model_digest = manifest.get("agent_model_digest")
    expected_source = f"ollama:{model_name}@{model_digest}"

    execution = agent.get("execution") or {}
    week1 = {
        "real_agent_execution_succeeded": execution.get("status") == "succeeded",
        "sandbox_container_removed": agent.get("container_removed") is True,
        "agent_model_is_digest_locked": (
            isinstance(model_digest, str) and model_digest.startswith("sha256:")
        ),
        "campaign_agent_is_not_fake": (
            isinstance(model_name, str) and bool(model_name) and "fake" not in model_name.casefold()
        ),
    }

    week2 = {
        "record_strict_live_fork_child_strict_passed": replay.get("passed") is True,
        "replay_model_digest_matches_campaign": replay.get("model_digest") == model_digest,
    }

    coverage_snapshot = coverage.get("snapshot") or {}
    risk_depths = coverage_snapshot.get("risk_depths") or {}
    total_risks = coverage_snapshot.get("total_risk_categories")
    heatmap = coverage.get("pretty_heatmap") or {}
    week3 = {
        "coverage_campaign_matches": coverage_snapshot.get("campaign_id") == args.campaign_id,
        "coverage_contains_real_trajectories": (
            coverage_snapshot.get("total_trajectories", 0) > 0
            and len(coverage.get("results") or []) > 0
        ),
        "all_taxonomy_leaves_have_explicit_depth": (
            isinstance(total_risks, int)
            and total_risks > 0
            and len(risk_depths) == total_risks
            and all(isinstance(value, int) and 0 <= value <= 3 for value in risk_depths.values())
        ),
        "behavior_profiles_exported": len(coverage.get("profiles") or []) > 0,
        "human_readable_heatmap_exported": (
            isinstance(heatmap, dict) and heatmap.get("campaign_id") == args.campaign_id
        ),
    }

    batches = mutation.get("batches") or []
    candidates = mutation.get("candidates") or []
    calls = mutation.get("provider_calls") or []
    near_duplicates = [
        item for item in mutation.get("rejections") or [] if item.get("reason") == "near_duplicate"
    ]
    week4 = {
        "mutation_campaign_matches": (
            (mutation.get("snapshot") or {}).get("campaign_id") == args.campaign_id
        ),
        "feedback_and_plan_persisted": (
            bool(batches) and all(item.get("feedback") and item.get("plan") for item in batches)
        ),
        "ollama_provider_calls_persisted": (
            bool(calls)
            and all(
                item.get("provider") == "ollama"
                and item.get("model_name") == manifest.get("mutation_model_name")
                and item.get("model_digest") == manifest.get("mutation_model_digest")
                and item.get("request_digest")
                and item.get("latency_ms") is not None
                for item in calls
            )
        ),
        "accepted_candidates_use_locked_ollama": (
            bool(candidates)
            and all(
                item.get("provider") == "ollama"
                and item.get("model_digest") == manifest.get("mutation_model_digest")
                for item in candidates
            )
        ),
        "near_duplicate_evidence_persisted": all(
            item.get("normalized_prompt_sha256") and item.get("maximum_similarity") is not None
            for item in near_duplicates
        ),
    }

    corpus = campaign.get("corpus") or []
    work_items = campaign.get("work_items") or []
    seeds = campaign.get("seeds") or []
    sources = set(learning.get("model_sources") or [])
    week5 = {
        "automated_loop_validation_passed": loop_validation.get("passed") is True,
        "campaign_corpus_nonempty": bool(corpus),
        "mutation_work_committed": any(
            (item.get("source") or {}).get("kind") == "mutation"
            and item.get("status") == "committed"
            for item in work_items
        ),
        "second_generation_requirement_satisfied": (
            not args.require_golden_pool
            or any(
                item.get("origin") == "mutation" and item.get("mutation_depth", 0) >= 2
                for item in seeds
            )
        ),
        "learning_records_built": pool.get("work_record_count", 0) > 0,
        "all_observed_model_sources_are_locked_real_model": sources == {expected_source},
        "candidate_pool_is_unlabeled": (
            pool.get("is_golden_set") is False
            and pool.get("labels_generated_by_model") is False
            and pool.get("human_label_required") is True
        ),
    }
    if args.require_golden_pool:
        week5["real_trajectory_candidate_pool_at_least_100"] = (
            pool.get("candidate_pool_size_gate_passed") is True
            and pool.get("candidate_count", 0) >= 100
        )

    weeks = {
        "week1_isolated_real_model_execution": week1,
        "week2_record_replay_and_fork": week2,
        "week3_behavior_and_risk_coverage": week3,
        "week4_semantic_mutation": week4,
        "week5_automated_campaign_loop": week5,
    }
    failed = {
        week: sorted(name for name, passed in checks.items() if not passed)
        for week, checks in weeks.items()
        if not all_true(checks)
    }
    payload = {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "campaign_id": args.campaign_id,
        "model_lock": {
            "model_name": model_name,
            "model_digest": model_digest,
            "expected_trajectory_source": expected_source,
        },
        "mode": "data" if args.require_golden_pool else "smoke",
        "weeks": weeks,
        "passed": not failed,
        "failed_checks": failed,
        "golden_set_status": {
            "candidate_count": pool.get("candidate_count"),
            "candidate_pool_gate_passed": pool.get("candidate_pool_size_gate_passed"),
            "is_human_labeled_gold": False,
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + chr(10),
        encoding="utf-8",
    )
    print(json.dumps({"passed": not failed, "output": str(output)}))
    if failed:
        raise SystemExit(f"Weeks 1-5 acceptance failed: {failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
