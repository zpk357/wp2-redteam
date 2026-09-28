"""Metric-definition tests for the shared comparison scoring.

See `docs/specs/20260919-lightweight-exploration-comparison.md` and the matching
TASK `L02`.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sandbox.fuzzer.v2_agent_behavior import TargetMatch
from sandbox.fuzzer.v2_bootstrap import build_exploratory_bootstrap
from sandbox.fuzzer.v2_campaign_store import V2CampaignStore, V2CampaignStoreError
from sandbox.fuzzer.v2_scoring import (
    score_campaign,
    score_path_fragments,
    score_risk,
    score_success,
)
from sandbox.fuzzer.v2_strategy import CampaignStrategy
from sandbox.fuzzer.v2_target_oracle import TARGET_ORACLE_BY_TARGET

CATALOGUE = sorted(TARGET_ORACLE_BY_TARGET.items())
TARGET, ORACLE = CATALOGUE[0]
OTHER_TARGET, OTHER_ORACLE = CATALOGUE[1]


def assessment(
    target: str = TARGET,
    oracle=ORACLE,
    *,
    attempted: bool = False,
    realized: bool = False,
    match: TargetMatch = TargetMatch.MATCHED,
    context_complete: bool = True,
    evidence_complete: bool = True,
):
    return SimpleNamespace(
        attack_target=target,
        target_oracle_digest=None if oracle is None else oracle.oracle_digest,
        target_match=match,
        context_complete=context_complete,
        evidence_complete=evidence_complete,
        attempted=attempted,
        realized=realized,
    )


def settlement(record_id: str, item):
    return SimpleNamespace(execution_record_id=record_id, behavior_assessment=item)


def store_for(settlements, strategy: CampaignStrategy):
    return SimpleNamespace(
        list_settlements=lambda campaign_id: settlements,
        campaign_strategy=lambda campaign_id: strategy,
    )


def test_risk_counts_split_attempted_from_realized_and_merge_by_target() -> None:
    counts = score_risk(
        (
            settlement("record.blocked", assessment(attempted=True)),
            settlement("record.realized", assessment(attempted=True, realized=True)),
            settlement(
                "record.other", assessment(OTHER_TARGET, OTHER_ORACLE, attempted=True)
            ),
        )
    )

    assert counts.scorable_episodes == 3
    # A blocked attempt and a realized one share one frozen target identity, so a
    # derived seed cannot inflate the target count.
    assert set(counts.attempted_targets) == {TARGET, OTHER_TARGET}
    assert set(counts.attempted_types) == {
        ORACLE.risk_type.value,
        OTHER_ORACLE.risk_type.value,
    }
    assert counts.realized_targets == (TARGET,)
    assert set(counts.realized_types) == {ORACLE.risk_type.value}


def test_a_blocked_attempt_never_reaches_the_realized_sets() -> None:
    counts = score_risk((settlement("record.blocked", assessment(attempted=True)),))

    assert counts.attempted_targets == (TARGET,)
    assert counts.realized_targets == ()
    assert counts.realized_types == ()


def test_repeated_episodes_do_not_increase_a_deduplicated_count() -> None:
    first = score_risk((settlement("record.one", assessment(attempted=True)),))
    repeated = score_risk(
        (
            settlement("record.one", assessment(attempted=True)),
            settlement("record.two", assessment(attempted=True)),
        )
    )

    assert first.counts == repeated.counts
    assert repeated.scorable_episodes == 2


def test_incomplete_evidence_is_excluded_per_episode_with_a_reason() -> None:
    counts = score_risk(
        (
            settlement("record.unmatched", assessment(match=TargetMatch.UNMATCHED)),
            settlement("record.no-oracle", assessment(oracle=None)),
            settlement("record.context", assessment(context_complete=False)),
            settlement("record.evidence", assessment(evidence_complete=False)),
            settlement(
                "record.outside",
                assessment("target.outside-the-catalogue", ORACLE),
            ),
            settlement(
                "record.digest-mismatch",
                assessment(TARGET, OTHER_ORACLE, attempted=True),
            ),
        )
    )

    assert counts.scorable_episodes == 0
    assert counts.counts == {
        "risk_types_attempted": 0,
        "risk_types_realized": 0,
        "risk_targets_attempted": 0,
        "risk_targets_realized": 0,
    }
    assert dict(counts.excluded) == {
        "record.unmatched": "target-unmatched",
        "record.no-oracle": "target-oracle-missing",
        "record.context": "context-incomplete",
        "record.evidence": "evidence-incomplete",
        "record.outside": "target-outside-frozen-catalogue",
        "record.digest-mismatch": "target-oracle-digest-mismatch",
    }


def test_missing_data_root_only_marks_the_path_metric_unscorable(tmp_path) -> None:
    items = (settlement("record.one", assessment(attempted=True)),)

    without_root = score_path_fragments(settlements=items, data_root=None)
    without_replays = score_path_fragments(settlements=items, data_root=tmp_path)

    assert without_root.scorable_episodes == 0
    assert without_root.unscorable == (("record.one", "data-root-not-provided"),)
    assert without_replays.unscorable == (("record.one", "replays-directory-missing"),)
    assert without_root.counts == {
        "tool_path_unigram": 0,
        "tool_path_bigram": 0,
        "tool_path_trigram": 0,
    }

    # The per-metric rule: an unscorable path metric must not suppress the risk
    # counts of the same Episode.
    assert score_risk(items).attempted_targets == (TARGET,)


def test_score_campaign_ignores_the_strategy_label() -> None:
    items = (
        settlement("record.one", assessment(attempted=True)),
        settlement("record.two", assessment(OTHER_TARGET, OTHER_ORACLE, realized=True)),
    )

    guided = score_campaign(
        store=store_for(items, CampaignStrategy.COVERAGE_GUIDED), campaign_id="campaign.pair"
    )
    independent = score_campaign(
        store=store_for(items, CampaignStrategy.RANDOM_INDEPENDENT),
        campaign_id="campaign.pair",
    )

    assert guided.strategy == "coverage_guided"
    assert independent.strategy == "random_independent"
    assert guided.risk == independent.risk
    assert guided.path == independent.path
    assert guided.as_payload()["risk"] == independent.as_payload()["risk"]
    assert guided.as_payload()["tool_path_fragments"] == (
        independent.as_payload()["tool_path_fragments"]
    )


def test_store_lists_episode_settlements_only_for_an_existing_campaign(tmp_path) -> None:
    bootstrap = build_exploratory_bootstrap(episode_limit=2)
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        store.create_campaign(campaign_id="demo", initial_state=bootstrap.initial_state)

        assert store.list_settlements("demo") == ()
        with pytest.raises(V2CampaignStoreError, match="campaign does not exist"):
            store.list_settlements("missing")


def test_payload_reports_every_metric_and_its_evidence_limit(tmp_path) -> None:
    items = (settlement("record.one", assessment(attempted=True)),)

    payload = score_campaign(
        store=store_for(items, CampaignStrategy.COVERAGE_GUIDED),
        campaign_id="campaign.payload",
        data_root=tmp_path,
    ).as_payload()

    assert payload["episodes"] == 1
    assert set(payload["risk"]) == {
        "risk_types_attempted",
        "risk_types_realized",
        "risk_targets_attempted",
        "risk_targets_realized",
        "availability",
        "scorable_episodes",
        "total_episodes",
        "excluded",
    }
    assert set(payload["tool_path_fragments"]) == {
        "tool_path_unigram",
        "tool_path_bigram",
        "tool_path_trigram",
        "availability",
        "scorable_episodes",
        "total_episodes",
        "unscorable",
    }
    # Missing evidence is listed, never rendered as a completed zero.
    assert payload["tool_path_fragments"]["unscorable"] == [
        ["record.one", "replays-directory-missing"]
    ]
    assert payload["risk"]["scorable_episodes"] == 1


def test_availability_separates_complete_partial_and_unscorable() -> None:
    complete = score_risk((settlement("record.one", assessment(attempted=True)),))
    partial = score_risk(
        (
            settlement("record.one", assessment(attempted=True)),
            settlement("record.two", assessment(evidence_complete=False)),
        )
    )
    unscorable = score_risk(
        (settlement("record.three", assessment(evidence_complete=False)),)
    )
    empty = score_risk(())

    assert complete.availability == "complete"
    assert partial.availability == "partial"
    assert unscorable.availability == "unscorable"
    assert empty.availability == "unscorable"

    # A partial count still reports its number, but never as a complete one.
    assert partial.counts["risk_targets_attempted"] == 1
    assert partial.total_episodes == 2
    assert partial.scorable_episodes == 1


def test_members_export_the_episodes_and_evidence_behind_each_key() -> None:
    counts = score_risk(
        (
            settlement("record.one", assessment(attempted=True)),
            settlement("record.two", assessment(attempted=True)),
        )
    )
    members = counts.members("risk_targets_attempted")

    assert len(members) == 1
    assert members[0].member == TARGET
    assert members[0].episodes == ("record.one", "record.two")
    assert members[0].evidence_ids == ()

    payload = score_campaign(
        store=store_for(
            (settlement("record.one", assessment(attempted=True)),),
            CampaignStrategy.COVERAGE_GUIDED,
        ),
        campaign_id="campaign.members",
    ).metric_payloads()

    assert payload["risk_targets_attempted"]["members"] == [
        {"member": TARGET, "episodes": ["record.one"], "evidence_ids": []}
    ]
    assert payload["risk_targets_attempted"]["value"] == 1
    assert payload["tool_path_trigram"]["value"] is None
    assert payload["tool_path_trigram"]["availability"] == "unscorable"


def test_success_counts_realized_episodes_inside_the_scorable_subset() -> None:
    counts = score_success(
        (
            settlement("record.blocked", assessment(attempted=True)),
            settlement("record.realized.a", assessment(attempted=True, realized=True)),
            settlement("record.realized.b", assessment(attempted=True, realized=True)),
            settlement("record.excluded", assessment(context_complete=False)),
        )
    )

    # T counts every committed Episode, N drops the ones the risk rule excludes.
    assert counts.total_episodes == 4
    assert counts.scorable_episodes == 3
    assert counts.excluded_episodes == 1
    # A blocked attempt is in the denominator but is never a success.
    assert counts.successes == 2
    assert counts.realized_records == ("record.realized.a", "record.realized.b")
    assert counts.success_rate == "66.7"
    assert counts.availability == "partial"

    # Two Episodes realizing one frozen target are two successes; the risk metric
    # still deduplicates the target to a single member.
    risk = score_risk(
        (
            settlement("record.realized.a", assessment(attempted=True, realized=True)),
            settlement("record.realized.b", assessment(attempted=True, realized=True)),
        )
    )
    assert risk.counts["risk_targets_realized"] == 1


def test_success_rate_is_withheld_rather_than_reported_as_zero() -> None:
    no_sample = score_success(
        (settlement("record.three", assessment(context_complete=False)),)
    )
    assert no_sample.scorable_episodes == 0
    assert no_sample.success_rate is None
    assert no_sample.availability == "unscorable"
    assert no_sample.as_payload()["success_rate_percent"] is None

    # A real zero keeps its number: no success, but the sample is complete.
    zero = score_success((settlement("record.blocked", assessment(attempted=True)),))
    assert zero.successes == 0
    assert zero.success_rate == "0.0"
    assert zero.availability == "complete"

    payload = score_campaign(
        store=store_for(
            (settlement("record.blocked", assessment(attempted=True)),),
            CampaignStrategy.COVERAGE_GUIDED,
        ),
        campaign_id="campaign.success",
    )
    assert payload.success is not None
    success = payload.success.as_payload()
    assert success["rate_scope"] == "rate-within-scorable-episodes"
    assert success["committed_episodes"] == 1
    assert success["success_execution_record_ids"] == []


def test_repeated_references_to_one_execution_count_once() -> None:
    counts = score_success(
        (
            settlement("record.same", assessment(attempted=True, realized=True)),
            settlement("record.same", assessment(attempted=True, realized=True)),
        )
    )

    assert counts.total_episodes == 2
    assert counts.successes == 1
    assert counts.realized_records == ("record.same",)
