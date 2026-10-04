"""Human-readable JSON projection of one Office V2 Campaign database.

The report exposes three layers:

- the original single-Campaign projection (unchanged keys);
- ``generation_series``: one row per recorded generation, so coverage, cost and
  admissions can be plotted against Episode budget;
- ``aggregates``: Campaign-level counts and rates over the same persisted
  evidence.

Every metric is reported on its own dimension.  Nothing is blended into a single
score, because a blended score would hide which dimension regressed.
"""

from __future__ import annotations

import json
from pathlib import Path

from sandbox.replay.digests import sha256_digest

from .v2_campaign_store import V2CampaignStore
from .v2_work import AttemptDisposition

BOOTSTRAP_PROMOTION_REASON = "exploratory-bootstrap-parent"
TIMEOUT_ERROR_CODE = "episode-timeout"


def _coverage_counts(state) -> dict[str, int]:
    return {
        "canonical_facts": len(state.coverage.canonical_fact_digests),
        "primary_behavior_features": len(state.coverage.primary_behavior_feature_keys),
        "risk_contexts": len(state.coverage.risk_context_keys),
        "oracle_outcome_bits": len(state.coverage.milestone_outcome_bit_keys),
    }


def _admitted_entries(state) -> dict[str, object]:
    """Corpus entries this Campaign admitted itself, keyed by entry id.

    Bootstrap entries are excluded: every Campaign starts with them, so counting
    them as promotions would overstate the mechanism under test.
    """
    return {
        item.corpus_entry_id: item
        for item in state.corpus.entries
        if BOOTSTRAP_PROMOTION_REASON not in item.promotion_reasons
    }


def build_v2_generation_series(
    *, store: V2CampaignStore, campaign_id: str
) -> list[dict[str, object]]:
    """Return one row per recorded generation, in generation order.

    Metric definitions (frozen before any comparison run):

    - ``coverage_counts``, ``corpus_entries``, ``used_episodes`` and ``consumed``
      describe the state the generation started from, so a row shows what the
      generation could already rely on.
    - Generation counters increment when a generation settles, so the feedback
      indexed ``g`` is produced by generation ``g - 1`` and consumed by generation
      ``g``.  A row therefore takes its increments from the feedback it
      **produced** (``index + 1``).  Reading them from ``index`` reported the
      previous generation's increments and dropped the last generation entirely.
    - What a generation **consumed** is taken from its own decision record
      (``input_feedback_digest``), never inferred from the index.  The independent
      baseline consumes no cross-Episode feedback, so it must not be reported as
      if it did.
    - ``gap_kind`` keeps its original meaning: the gap kind of the feedback the
      generation consumed, or ``None`` when it consumed none.
      ``produced_gap_kind`` describes the outcome the generation produced.
    - ``no_coverage_gain`` is the produced ``FeedbackBrief.consecutive_no_gain``
      flag.  Despite the field name it is a single-generation flag, not a run
      length; the run length is derived in the aggregates.
    - ``promoted_entries`` counts the Corpus entries this generation admitted,
      excluding bootstrap entries.
    """

    decisions = store.list_generation_decisions(campaign_id)
    records = store.list_generation_feedback(campaign_id)
    feedback_by_index = {item.generation_index: item for item in records}
    feedback_by_digest = {item.feedback_digest: item for item in records}
    final_state = store.load_state(campaign_id)
    series: list[dict[str, object]] = []
    for index, decision in enumerate(decisions):
        before = store.load_state_by_digest(decision.input_state_digest)
        after = (
            store.load_state_by_digest(decisions[index + 1].input_state_digest)
            if index + 1 < len(decisions)
            else final_state
        )
        admitted_before = _admitted_entries(before)
        admitted = [
            item
            for entry_id, item in _admitted_entries(after).items()
            if entry_id not in admitted_before
        ]
        consumed = (
            feedback_by_digest.get(decision.input_feedback_digest)
            if decision.input_feedback_digest is not None
            else None
        )
        produced = feedback_by_index.get(decision.generation_index + 1)
        produced_brief = produced.brief if produced is not None else None
        series.append(
            {
                "generation_index": decision.generation_index,
                "risk_type": decision.allocation.risk_type.value,
                "parent_seed_id": decision.allocation.parent_seed_id,
                "risk_type_progress_level": decision.allocation.risk_type_progress_level,
                "reason_codes": list(decision.reason_codes),
                "coverage_counts": _coverage_counts(before),
                "corpus_entries": len(before.corpus.entries),
                "used_episodes": before.budget.used_episodes,
                "consumed": before.budget.consumed.model_dump(mode="json", exclude_none=False),
                "gap_kind": consumed.gap_kind.value if consumed is not None else None,
                "input_feedback_digest": decision.input_feedback_digest,
                "produced_feedback_digest": (
                    produced.feedback_digest if produced is not None else None
                ),
                "produced_gap_kind": produced.gap_kind.value if produced is not None else None,
                "no_coverage_gain": (
                    produced_brief.consecutive_no_gain if produced_brief is not None else None
                ),
                "new_primary_behavior_count": (
                    produced_brief.new_primary_behavior_count
                    if produced_brief is not None
                    else None
                ),
                "new_risk_fact_count": (
                    produced_brief.new_risk_fact_count if produced_brief is not None else None
                ),
                "promoted_entries": len(admitted),
                "promotion_reasons": sorted(
                    {reason for item in admitted for reason in item.promotion_reasons}
                ),
                "promotion_seed_kinds": sorted(
                    {item.seed_kind.value for item in admitted}
                ),
            }
        )
    return series


def _promotion_distribution(entries) -> tuple[dict[str, int], dict[str, int]]:
    """Return (reason counts, seed-kind counts) for one set of Corpus entries."""
    promotion_reasons: dict[str, int] = {}
    promotion_seed_kinds: dict[str, int] = {}
    for item in entries:
        for reason in item.promotion_reasons:
            promotion_reasons[reason] = promotion_reasons.get(reason, 0) + 1
        key = item.seed_kind.value
        promotion_seed_kinds[key] = promotion_seed_kinds.get(key, 0) + 1
    return promotion_reasons, promotion_seed_kinds


def _longest_no_gain_run(series: list[dict[str, object]]) -> int:
    longest = 0
    current = 0
    for row in series:
        if row["no_coverage_gain"] is True:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def build_v2_campaign_aggregates(
    *, store: V2CampaignStore, campaign_id: str, series: list[dict[str, object]]
) -> dict[str, object]:
    """Return Campaign-level aggregates over the same persisted evidence."""

    state = store.load_state(campaign_id)
    counters = state.lifecycle.counters
    receipts = store.list_attempt_receipts(campaign_id)
    findings = store.list_findings(campaign_id)
    admitted = _admitted_entries(state)
    history = store.load_candidate_history(campaign_id)

    failed = [item for item in receipts if item.disposition is not AttemptDisposition.SUCCEEDED]
    retryable = [
        item for item in receipts if item.disposition is AttemptDisposition.RETRYABLE
    ]
    timeouts = [item for item in receipts if item.error_code == TIMEOUT_ERROR_CODE]
    promotion_reasons, promotion_seed_kinds = _promotion_distribution(admitted.values())

    attempts = len(receipts)
    generated = len(history)
    unique_generated = len({item[0] for item in history})
    return {
        "generations": len(series),
        "valid_committed_episodes": counters.valid_committed_episodes,
        "invalid_or_failed_attempts": counters.invalid_or_failed_attempts,
        "attempt_receipts": attempts,
        "failed_attempts": len(failed),
        "retryable_attempts": len(retryable),
        "timeouts": len(timeouts),
        # Canonical JSON rejects non-integer floats, so rates are explicit
        # decimal strings. A raw float here made `report` and `inspect` raise on
        # any Campaign whose failure rate was not a whole number.
        "failure_rate": f"{len(failed) / attempts:.6f}" if attempts else None,
        "timeout_rate": f"{len(timeouts) / attempts:.6f}" if attempts else None,
        "promotions": len(admitted),
        "promotions_by_seed_kind": promotion_seed_kinds,
        "promotion_reasons": promotion_reasons,
        "longest_no_gain_run": _longest_no_gain_run(series),
        "generated_candidates": generated,
        "unique_candidates": unique_generated,
        "repeat_rate": (
            (generated - unique_generated) / generated if generated else None
        ),
        "unique_findings": len(findings),
        "findings_awaiting_replay": len(
            [
                item
                for item in findings
                if item.replay_status.value in {"recorded", "replay_required"}
            ]
        ),
        "cost": state.budget.consumed.model_dump(mode="json", exclude_none=False),
        "budget": state.budget.model_dump(mode="json", exclude_none=False),
    }


def _last_selection_source(store: V2CampaignStore, campaign_id: str) -> str | None:
    """Whether the latest scheduling decision came from the queue or the pool."""

    decision = store.load_latest_generation_decision(campaign_id)
    if decision is None:
        return None
    policy = decision.allocation.parent_selection_receipt.selection_policy
    if policy.value == "new_seed_priority_v1":
        return "new_seed_priority"
    return "uniform_pool"


def build_v2_campaign_report(
    *, store: V2CampaignStore, campaign_id: str
) -> dict[str, object]:
    state = store.load_state(campaign_id)
    series = build_v2_generation_series(store=store, campaign_id=campaign_id)
    payload: dict[str, object] = {
        "campaign_id": campaign_id,
        "strategy": store.campaign_strategy(campaign_id).value,
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
        # Guided-only mechanism; the independent arm always reports an empty
        # queue and a uniform selection source, which is what makes the two
        # protocols distinguishable offline.
        "guided_seed_priority": {
            "mechanism": "guided-only",
            "queue": list(state.new_seed_priority_queue),
            "queue_length": len(state.new_seed_priority_queue),
            "last_selection_source": _last_selection_source(store, campaign_id),
        },
        "coverage_snapshot_digest": state.coverage.snapshot_digest,
        "coverage_counts": _coverage_counts(state),
        "corpus": {
            "execution_supports": len(state.corpus.execution_supports),
            "executions": len(state.corpus.execution_records),
            "entries": len(state.corpus.entries),
        },
        "seed_pools": {
            "sizes": state.seed_catalog.pool_sizes(),
            "progress": {
                item.risk_type.value: int(item.level) for item in state.risk_progress
            },
        },
        "budget": state.budget.model_dump(mode="json", exclude_none=False),
        "decisions": [
            json.loads(item.model_dump_json(exclude_none=False))
            for item in store.list_generation_decisions(campaign_id)
        ],
        "feedback": [
            json.loads(item.model_dump_json(exclude_none=False))
            for item in store.list_generation_feedback(campaign_id)
        ],
        "findings": [
            json.loads(item.model_dump_json(exclude_none=False))
            for item in store.list_findings(campaign_id)
        ],
        "recovery": {
            key: list(values)
            for key, values in store.inspect_recovery(campaign_id).items()
        },
        "generation_series": series,
        "aggregates": build_v2_campaign_aggregates(
            store=store, campaign_id=campaign_id, series=series
        ),
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


__all__ = [
    "build_v2_campaign_aggregates",
    "build_v2_campaign_report",
    "build_v2_generation_series",
    "write_v2_campaign_report",
]
