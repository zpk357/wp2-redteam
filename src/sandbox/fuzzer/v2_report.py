"""Human-readable JSON projection of one Office V2 Campaign database."""

from __future__ import annotations

import json
from pathlib import Path

from sandbox.replay.digests import sha256_digest

from .v2_campaign_store import V2CampaignStore


def build_v2_campaign_report(
    *, store: V2CampaignStore, campaign_id: str
) -> dict[str, object]:
    identity = store.load_identity(campaign_id)
    state = store.load_state(campaign_id)
    decisions = store._db.execute(
        "SELECT decision_json FROM generation_decision WHERE campaign_id=? "
        "ORDER BY generation_index",
        (campaign_id,),
    ).fetchall()
    feedback = store._db.execute(
        "SELECT feedback_json FROM generation_feedback WHERE campaign_id=? "
        "ORDER BY generation_index",
        (campaign_id,),
    ).fetchall()
    findings = store._db.execute(
        "SELECT finding_json FROM finding WHERE campaign_id=? ORDER BY finding_key",
        (campaign_id,),
    ).fetchall()
    payload: dict[str, object] = {
        "campaign_id": campaign_id,
        "identity_digest": identity.identity_digest,
        "phase": state.lifecycle.phase.value,
        "completion_status": (
            state.lifecycle.completion_status.value
            if state.lifecycle.completion_status is not None
            else None
        ),
        "generation_index": state.lifecycle.counters.generation_index,
        "valid_committed_episodes": (
            state.lifecycle.counters.valid_committed_episodes
        ),
        "invalid_or_failed_attempts": (
            state.lifecycle.counters.invalid_or_failed_attempts
        ),
        "coverage_snapshot_digest": state.coverage.snapshot_digest,
        "coverage_counts": {
            "canonical_facts": len(state.coverage.canonical_fact_digests),
            "primary_behavior_features": len(
                state.coverage.primary_behavior_feature_keys
            ),
            "risk_contexts": len(state.coverage.risk_context_keys),
            "risk_milestone_outcomes": len(
                state.coverage.milestone_outcome_bit_keys
            ),
        },
        "corpus": {
            "seeds": len(state.corpus.seeds),
            "executions": len(state.corpus.execution_records),
            "entries": len(state.corpus.entries),
        },
        "frontiers": {
            "risk": len(state.frontiers.risk_frontiers),
            "behavior": len(state.frontiers.behavior_frontiers),
        },
        "budget": state.budget.model_dump(mode="json", exclude_none=False),
        "decisions": [json.loads(item["decision_json"]) for item in decisions],
        "feedback": [json.loads(item["feedback_json"]) for item in feedback],
        "findings": [json.loads(item["finding_json"]) for item in findings],
        "recovery": {
            key: list(values)
            for key, values in store.inspect_recovery(campaign_id).items()
        },
    }
    payload["report_digest"] = sha256_digest(payload)
    return payload


def write_v2_campaign_report(
    *, store: V2CampaignStore, campaign_id: str, output: Path
) -> dict[str, object]:
    report = build_v2_campaign_report(store=store, campaign_id=campaign_id)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


__all__ = ["build_v2_campaign_report", "write_v2_campaign_report"]
