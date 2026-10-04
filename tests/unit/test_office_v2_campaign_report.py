"""Metric-definition tests for the Campaign report.

The metric definitions are frozen before any comparison run.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sandbox.fuzzer.v2_bootstrap import build_exploratory_bootstrap
from sandbox.fuzzer.v2_campaign_store import V2CampaignStore, V2CampaignStoreError
from sandbox.fuzzer.v2_corpus import SeedKind
from sandbox.fuzzer.v2_report import (
    BOOTSTRAP_PROMOTION_REASON,
    _admitted_entries,
    _coverage_counts,
    _longest_no_gain_run,
    _promotion_distribution,
    build_v2_campaign_report,
)

PRE_EXISTING_KEYS = (
    "campaign_id",
    "strategy",
    "phase",
    "completion_status",
    "generation_index",
    "valid_committed_episodes",
    "invalid_or_failed_attempts",
    "coverage_snapshot_digest",
    "coverage_counts",
    "corpus",
    "seed_pools",
    "budget",
    "decisions",
    "feedback",
    "findings",
    "recovery",
    "report_digest",
)


def entry(entry_id: str, reasons: tuple[str, ...], kind: SeedKind = SeedKind.RISK):
    return SimpleNamespace(
        corpus_entry_id=entry_id,
        promotion_reasons=reasons,
        seed_kind=kind,
    )


def test_coverage_counts_are_reported_per_dimension() -> None:
    state = SimpleNamespace(
        coverage=SimpleNamespace(
            canonical_fact_digests=("a",),
            primary_behavior_feature_keys=("b", "c"),
            risk_context_keys=("d", "e", "f"),
            milestone_outcome_bit_keys=("g", "h", "i", "j"),
        )
    )

    assert _coverage_counts(state) == {
        "canonical_facts": 1,
        "primary_behavior_features": 2,
        "risk_contexts": 3,
        "oracle_outcome_bits": 4,
    }


def test_admitted_entries_exclude_bootstrap_entries() -> None:
    state = SimpleNamespace(
        corpus=SimpleNamespace(
            entries=(
                entry("bootstrap-1", (BOOTSTRAP_PROMOTION_REASON,)),
                entry("promoted-1", ("new-selected-target-behavior",)),
            )
        )
    )

    assert set(_admitted_entries(state)) == {"promoted-1"}


def test_promotion_distribution_keeps_reasons_and_seed_kinds_separate() -> None:
    reasons, kinds = _promotion_distribution(
        (
            entry(
                "a",
                ("new-selected-target-behavior", "risk-fact-advanced"),
                SeedKind.RISK,
            ),
            entry(
                "b",
                ("new-selected-target-behavior", "new-primary-behavior"),
                SeedKind.EXPLORATION,
            ),
        )
    )

    assert reasons == {
        "new-selected-target-behavior": 2,
        "risk-fact-advanced": 1,
        "new-primary-behavior": 1,
    }
    assert kinds == {"risk": 1, "exploration": 1}


def test_longest_no_gain_run_counts_consecutive_generations() -> None:
    series = [
        {"no_coverage_gain": False},
        {"no_coverage_gain": True},
        {"no_coverage_gain": True},
        {"no_coverage_gain": False},
        {"no_coverage_gain": True},
    ]

    assert _longest_no_gain_run(series) == 2


def test_longest_no_gain_run_does_not_bridge_unknown_rows() -> None:
    series = [
        {"no_coverage_gain": True},
        {"no_coverage_gain": None},
        {"no_coverage_gain": True},
    ]

    assert _longest_no_gain_run(series) == 1


def test_store_list_methods_return_empty_for_a_fresh_campaign(tmp_path) -> None:
    bootstrap = build_exploratory_bootstrap(episode_limit=4)
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        store.create_campaign(campaign_id="demo", initial_state=bootstrap.initial_state)

        assert store.list_generation_decisions("demo") == ()
        assert store.list_generation_feedback("demo") == ()
        assert store.list_findings("demo") == ()
        assert store.list_attempt_receipts("demo") == ()


def test_store_list_methods_reject_an_unknown_campaign(tmp_path) -> None:
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        with pytest.raises(V2CampaignStoreError, match="campaign does not exist"):
            store.list_generation_decisions("missing")
        with pytest.raises(V2CampaignStoreError, match="campaign does not exist"):
            store.list_attempt_receipts("missing")


def test_report_keeps_its_keys_and_adds_series_and_aggregates(tmp_path) -> None:
    bootstrap = build_exploratory_bootstrap(episode_limit=4)
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        store.create_campaign(campaign_id="demo", initial_state=bootstrap.initial_state)

        report = build_v2_campaign_report(store=store, campaign_id="demo")

    for key in PRE_EXISTING_KEYS:
        assert key in report
    assert report["decisions"] == []
    assert report["feedback"] == []
    assert report["findings"] == []
    assert report["generation_series"] == []
    assert report["aggregates"]["generations"] == 0
    assert report["aggregates"]["promotions"] == 0
    assert report["aggregates"]["promotions_by_seed_kind"] == {}
    assert report["aggregates"]["promotion_reasons"] == {}
    assert report["aggregates"]["longest_no_gain_run"] == 0
    assert report["aggregates"]["generated_candidates"] == 0
    assert report["aggregates"]["repeat_rate"] is None
    assert report["aggregates"]["failure_rate"] is None
    assert report["aggregates"]["timeout_rate"] is None
    assert report["aggregates"]["unique_findings"] == 0
