"""Report-layer tests for the two-arm comparison export.

Local Coverage limit: no test in this repository can yet persist a real Episode
settlement without Docker and a real model, so the metric payloads here are
checked for their reporting semantics. The end-to-end sample that must drive real
persistence requires a real run.
"""

from __future__ import annotations

import json

from sandbox.fuzzer import v2_comparison_report
from sandbox.fuzzer.v2_bootstrap import build_exploratory_bootstrap
from sandbox.fuzzer.v2_campaign_store import V2CampaignStore
from sandbox.fuzzer.v2_cli import main
from sandbox.fuzzer.v2_comparison_report import (
    METRIC_LABELS,
    SCORING_VERSION,
    build_v2_comparison_report,
    render_v2_comparison_table,
)
from sandbox.fuzzer.v2_scoring import COMPLETE, METRIC_KEYS, PARTIAL, UNSCORABLE

GUIDED = "paired.guided"
INDEPENDENT = "paired.independent"


def _create_local_pair(tmp_path):
    """Create two empty local Campaigns that share one frozen initial state."""

    db = tmp_path / "campaign.db"
    bootstrap = build_exploratory_bootstrap(episode_limit=2)
    with V2CampaignStore(db) as store:
        store.create_campaign(
            campaign_id=GUIDED, initial_state=bootstrap.initial_state
        )
        store.create_campaign(
            campaign_id=INDEPENDENT,
            initial_state=bootstrap.initial_state,
            strategy="random_independent",
        )
    return db


def _metric(values: dict[str, int | None], availability: str, member: str) -> dict:
    return {
        key: {
            "value": values[key],
            "availability": availability,
            "scorable_episodes": 0 if availability == UNSCORABLE else 1,
            "total_episodes": 1,
            "members": (
                [
                    {
                        "member": f"{member}.{key}",
                        "episodes": ["record.1"],
                        "evidence_ids": ["evidence.1"],
                    }
                ]
                if availability != UNSCORABLE
                else []
            ),
        }
        for key in METRIC_KEYS
    }


def _prepared_arm(
    campaign_id: str,
    *,
    strategy: str,
    values: dict[str, int | None],
    availability: str = COMPLETE,
    episode_limit: int = 30,
    committed: int = 30,
    completion_status: str | None = "budget_exhausted_incomplete",
    mutator_tokens: int = 400,
    successes: int = 1,
    scorable: int | None = None,
) -> dict[str, object]:
    scorable_episodes = committed if scorable is None else scorable
    if scorable_episodes == 0:
        success_availability = "unscorable"
    elif scorable_episodes < committed:
        success_availability = "partial"
    else:
        success_availability = "complete"
    return {
        "campaign_id": campaign_id,
        "strategy": strategy,
        "phase": "completed",
        "completion_status": completion_status,
        "metrics": _metric(values, availability, campaign_id),
        "success": {
            "scope": "risk-scorable committed Episodes of this arm",
            "successes": successes,
            "scorable_episodes": scorable_episodes,
            "committed_episodes": committed,
            "excluded_episodes": committed - scorable_episodes,
            "success_rate_percent": (
                None
                if scorable_episodes == 0
                else f"{100 * successes / scorable_episodes:.1f}"
            ),
            "rate_scope": "rate-within-scorable-episodes",
            "availability": success_availability,
            "success_execution_record_ids": [
                f"record.{index}" for index in range(successes)
            ],
        },
        "failures": {
            "unit_note": "two separate views with different units",
            "non_episode_generations": {
                "unit": "generation closed without a committed Episode",
                "total": 2,
                "by_disposition": {"preparation_rejected": 2},
                "reasons": [
                    {
                        "settlement_id": "non-episode-settlement.test",
                        "disposition": "preparation_rejected",
                        "work_id": None,
                        "attempt_receipt_ids": [],
                        "reason_status": "found",
                        "reasons": ["preparation-state:rejected"],
                        "reason_sources": ["mutation_preparation.state"],
                        "settlement_reason_code_present": True,
                    }
                ],
            },
            "episode_execution_attempts": {
                "unit": "one AttemptReceipt per Episode execution attempt",
                "total": committed + 2,
                "by_disposition": {"succeeded": committed, "retryable": 2},
                "by_error_code": {"episode-timeout": 1},
            },
            "invalid_or_failed_attempts": {
                "field": "CampaignCounters.invalid_or_failed_attempts",
                "display_name": "未形成有效 Episode 的代次",
                "value": 2,
                "incremented_by": "record_non_episode_generation",
                "note": "counts generations, not attempts",
            },
        },
        "budget": {
            "source": "CampaignBudgetSnapshot",
            "episode_limit": episode_limit,
            "used_episodes": committed,
            "reserved_episodes": 0,
            "valid_committed_episodes": committed,
            "invalid_or_failed_attempts": 2,
            "episode_target_reached": committed >= episode_limit,
        },
        "episode_execution_attempts": {
            "scope": "one AttemptReceipt per Episode execution attempt; not a Mutator call",
            "receipts": committed + 2,
            "by_disposition": {"succeeded": committed, "retryable": 2},
            "by_error_code": {"episode-timeout": 1},
            "timeouts": 1,
            "agent_tokens": 3000,
            "elapsed_ms": 90_000,
        },
        "cost": {
            "campaign_budget_consumed": {
                "source": "CampaignBudgetSnapshot.consumed",
                "additive_total": True,
                "agent_tokens": 3000,
                "elapsed_ms": 90_000,
                "mutator_tokens": mutator_tokens,
                "monetary_microunits": 0,
            },
            "episode_execution_attempts": {
                "source": "AttemptReceipt.costs",
                "additive_total": False,
                "overlaps": "campaign_budget_consumed.agent_tokens",
                "agent_tokens": 3000,
                "elapsed_ms": 90_000,
                "mutator_tokens": None,
            },
            "committed_execution_records": {
                "source": "ExecutionRecord.costs",
                "additive_total": False,
                "overlaps": "subset of episode_execution_attempts",
                "agent_tokens": 2900,
                "elapsed_ms": 88_000,
                "mutator_tokens": None,
            },
            "budget_reconciles_with_attempt_receipts": True,
            "budget_reconciles_with_attempt_receipts_note": (
                "compares consumed against the receipt sums; see receipts_not_settled"
            ),
            "agent_tokens_complete": True,
            "agent_tokens_incomplete_receipts": [],
            "receipts_not_settled": [],
            "receipts_not_settled_elapsed_ms": 0,
            "mutator_tokens_available_from": "campaign_budget_consumed only",
        },
        "evidence_limits": {"risk_excluded": [], "path_unscorable": []},
        "evidence_index": {
            "record.1": {"manifest_digest": "sha256:" + "a" * 64, "seed_id": "seed.1"}
        },
        "notes": [],
        "campaign_report": {"campaign_id": campaign_id},
    }


_GUIDED_VALUES = {
    "risk_types_attempted": 3,
    "risk_types_realized": 1,
    "risk_targets_attempted": 4,
    "risk_targets_realized": 1,
    "tool_path_unigram": 12,
    "tool_path_bigram": 7,
    "tool_path_trigram": 2,
}
_INDEPENDENT_VALUES = {
    "risk_types_attempted": 2,
    "risk_types_realized": 1,
    "risk_targets_attempted": 4,
    "risk_targets_realized": 2,
    "tool_path_unigram": 15,
    "tool_path_bigram": 6,
    "tool_path_trigram": 1,
}


def _patched(monkeypatch, *, guided: dict, independent: dict) -> None:
    def prepared(*, store, campaign_id, data_root=None):
        return guided if campaign_id == GUIDED else independent

    monkeypatch.setattr(v2_comparison_report, "build_comparison_arm", prepared)


def test_a_local_pair_withholds_every_metric_instead_of_reporting_zero(tmp_path) -> None:
    db = _create_local_pair(tmp_path)
    with V2CampaignStore(db) as store:
        report = build_v2_comparison_report(
            store=store,
            guided_campaign_id=GUIDED,
            independent_campaign_id=INDEPENDENT,
        )

    assert report["scoring_version"] == SCORING_VERSION
    assert report["weighted_total"] is None
    assert [item["key"] for item in report["metric_catalogue"]] == list(METRIC_KEYS)

    for role in ("guided", "independent"):
        arm = report["arms"][role]
        for key in METRIC_KEYS:
            metric = arm["metrics"][key]
            assert metric["availability"] == UNSCORABLE
            assert metric["value"] is None

    # A withheld value must never produce a difference.
    for key in METRIC_KEYS:
        delta = report["deltas"][key]
        assert delta["difference"] is None
        assert delta["guided"] is None and delta["independent"] is None
        assert delta["value_withheld"].startswith("difference-withheld:")

    table = render_v2_comparison_table(report)
    assert "不可计分" in table
    assert "不可比（见下）" in table

    # The two real campaigns must be visible as different strategies.
    assert report["comparability"]["verified_from_store"]["guided_strategy"] == (
        "coverage_guided"
    )
    assert report["comparability"]["verified_from_store"]["independent_strategy"] == (
        "random_independent"
    )
    assert report["comparability"]["usable_for_superiority_claim"] is False
    assert report["comparability"]["unverified"]


def test_partial_evidence_is_marked_incomplete_and_withholds_the_difference(
    monkeypatch,
) -> None:
    _patched(
        monkeypatch,
        guided=_prepared_arm(
            GUIDED, strategy="coverage_guided", values=_GUIDED_VALUES, availability=PARTIAL
        ),
        independent=_prepared_arm(
            INDEPENDENT,
            strategy="random_independent",
            values=_INDEPENDENT_VALUES,
            availability=COMPLETE,
        ),
    )
    report = v2_comparison_report.build_v2_comparison_report(
        store=object(), guided_campaign_id=GUIDED, independent_campaign_id=INDEPENDENT
    )

    for key in METRIC_KEYS:
        assert report["arms"]["guided"]["metrics"][key]["availability"] == PARTIAL
        delta = report["deltas"][key]
        assert delta["difference"] is None
        assert delta["value_withheld"] == "difference-withheld:guided=不完整"
    assert report["comparability"]["usable_for_superiority_claim"] is False
    assert "guided-tool_path_trigram" in {
        item["check"] for item in report["comparability"]["checks"]
    }


def test_complete_arms_export_hand_computed_differences_and_dedup_members(
    monkeypatch,
) -> None:
    _patched(
        monkeypatch,
        guided=_prepared_arm(GUIDED, strategy="coverage_guided", values=_GUIDED_VALUES),
        independent=_prepared_arm(
            INDEPENDENT, strategy="random_independent", values=_INDEPENDENT_VALUES
        ),
    )
    report = v2_comparison_report.build_v2_comparison_report(
        store=object(), guided_campaign_id=GUIDED, independent_campaign_id=INDEPENDENT
    )

    deltas = report["deltas"]
    assert deltas["risk_types_attempted"]["difference"] == 1
    assert deltas["risk_targets_realized"]["difference"] == -1
    assert deltas["tool_path_unigram"]["difference"] == -3
    assert deltas["tool_path_bigram"]["difference"] == 1
    assert deltas["tool_path_trigram"]["value_withheld"] is None

    # Every member is exported with the Episode and evidence behind it.
    member = report["arms"]["guided"]["metrics"]["risk_types_attempted"]["members"][0]
    assert member["member"] == "paired.guided.risk_types_attempted"
    assert member["episodes"] == ["record.1"]
    assert member["evidence_ids"] == ["evidence.1"]

    table = render_v2_comparison_table(report)
    assert f"| {METRIC_LABELS['risk_types_attempted']} | 3 | 2 | +1 |" in table
    assert f"| {METRIC_LABELS['risk_targets_realized']} | 1 | 2 | -1 |" in table
    assert "不加权总分：无" in table


def test_cost_scopes_declare_overlap_and_breakdowns_keep_mutator_tokens_unknown(
    monkeypatch,
) -> None:
    _patched(
        monkeypatch,
        guided=_prepared_arm(
            GUIDED,
            strategy="coverage_guided",
            values=_GUIDED_VALUES,
            mutator_tokens=400,
        ),
        independent=_prepared_arm(
            INDEPENDENT,
            strategy="random_independent",
            values=_INDEPENDENT_VALUES,
            mutator_tokens=250,
        ),
    )
    report = v2_comparison_report.build_v2_comparison_report(
        store=object(), guided_campaign_id=GUIDED, independent_campaign_id=INDEPENDENT
    )

    cost = report["arms"]["guided"]["cost"]
    assert cost["campaign_budget_consumed"]["additive_total"] is True
    assert cost["episode_execution_attempts"]["additive_total"] is False
    assert cost["committed_execution_records"]["additive_total"] is False
    # Only the authoritative total carries Mutator tokens; a breakdown reports
    # "unknown" rather than 0.
    assert cost["campaign_budget_consumed"]["mutator_tokens"] == 400
    assert cost["episode_execution_attempts"]["mutator_tokens"] is None
    assert cost["committed_execution_records"]["mutator_tokens"] is None
    # Cost completeness travels beside the integer contract, and an unsettled
    # receipt is reported by name instead of being folded into the total.
    assert cost["agent_tokens_complete"] is True
    assert cost["agent_tokens_incomplete_receipts"] == []
    assert cost["receipts_not_settled"] == []
    assert "receipts_not_settled" in cost["budget_reconciles_with_attempt_receipts_note"]

    table = render_v2_comparison_table(report)
    assert "Mutator token 只在" in table
    assert "| campaign_budget_consumed.mutator_tokens | 400 | 250 | 是 |" in table
    assert "| episode_execution_attempts.agent_tokens | 3000 | 3000 | 否（与总量重叠） |" in table
    assert "| campaign_budget_consumed.elapsed_ms | 90000 | 90000 | 是 |" in table


def test_same_strategy_or_missed_budget_blocks_the_superiority_claim(monkeypatch) -> None:
    _patched(
        monkeypatch,
        guided=_prepared_arm(
            GUIDED,
            strategy="coverage_guided",
            values=_GUIDED_VALUES,
            committed=12,
        ),
        independent=_prepared_arm(
            INDEPENDENT,
            strategy="coverage_guided",
            values=_INDEPENDENT_VALUES,
            committed=12,
        ),
    )
    report = v2_comparison_report.build_v2_comparison_report(
        store=object(), guided_campaign_id=GUIDED, independent_campaign_id=INDEPENDENT
    )

    comparability = report["comparability"]
    assert comparability["usable_for_superiority_claim"] is False
    failed = {
        item["check"] for item in comparability["checks"] if not item["ok"]
    }
    assert "arms-differ-in-strategy" in failed
    assert "independent-arm-strategy" in failed
    assert "guided-episode-target-reached" in failed
    assert "independent-episode-target-reached" in failed
    # Strategies and budget are compared from the store, not asserted by the caller.
    assert comparability["verified_from_store"]["guided_episode_limit"] == 30


def test_met_target_budget_passes_the_checks_without_asserting_superiority(
    monkeypatch,
) -> None:
    _patched(
        monkeypatch,
        guided=_prepared_arm(GUIDED, strategy="coverage_guided", values=_GUIDED_VALUES),
        independent=_prepared_arm(
            INDEPENDENT, strategy="random_independent", values=_INDEPENDENT_VALUES
        ),
    )
    report = v2_comparison_report.build_v2_comparison_report(
        store=object(), guided_campaign_id=GUIDED, independent_campaign_id=INDEPENDENT
    )

    comparability = report["comparability"]
    # Every check the database can make passes, but the run conditions are never
    # established from the database, so the report still does not assert
    # superiority on its own.
    assert comparability["checks_passed"] is True
    assert all(item["ok"] for item in comparability["checks"])
    assert comparability["usable_for_superiority_claim"] is False
    assert comparability["blocked_by"] == ["run-conditions-unverified"]
    # Conditions the database cannot establish stay listed.
    assert len(comparability["unverified"]) == 3


def test_repeated_export_is_identical_and_leaves_the_campaign_unchanged(tmp_path) -> None:
    db = _create_local_pair(tmp_path)
    with V2CampaignStore(db) as store:
        state_before = store.load_state(GUIDED).state_digest

        first = build_v2_comparison_report(
            store=store, guided_campaign_id=GUIDED, independent_campaign_id=INDEPENDENT
        )
        second = build_v2_comparison_report(
            store=store, guided_campaign_id=GUIDED, independent_campaign_id=INDEPENDENT
        )

        assert first == second
        assert store.load_state(GUIDED).state_digest == state_before
        assert store.list_settlements(GUIDED) == ()
        assert store.list_attempt_receipts(GUIDED) == ()


def test_the_compare_command_writes_json_and_a_markdown_table(tmp_path) -> None:
    db = _create_local_pair(tmp_path)
    output = tmp_path / "exports" / "comparison.json"
    table = tmp_path / "exports" / "comparison.md"

    exit_code = main(
        [
            "compare",
            "--db",
            str(db),
            "--guided-campaign-id",
            GUIDED,
            "--independent-campaign-id",
            INDEPENDENT,
            "--output",
            str(output),
            "--table-output",
            str(table),
        ]
    )

    assert exit_code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["scoring_version"] == SCORING_VERSION
    assert payload["arms"]["guided"]["campaign_id"] == GUIDED
    assert payload["arms"]["independent"]["strategy"] == "random_independent"
    # The main view is the attack-effectiveness table; the coverage comparison
    # stays as a labelled diagnostic below it.
    assert payload["main_scoring"] == "attack-effectiveness"
    assert payload["report_version"] == payload["effectiveness"]["report_version"]
    assert payload["effectiveness"]["arms"]["guided"]["scoring"] == (
        "post-hoc-reclassification"
    )
    rendered = table.read_text(encoding="utf-8")
    assert "# 攻击效果主表" in rendered
    assert rendered.index("# 攻击效果主表") < rendered.index("诊断：覆盖率与失败分解")
    assert GUIDED in rendered and INDEPENDENT in rendered


def test_success_and_failure_views_stay_separate_and_never_summed(tmp_path) -> None:
    db = _create_local_pair(tmp_path)
    output = tmp_path / "success.json"
    table = tmp_path / "success.md"

    exit_code = main(
        [
            "compare",
            "--db",
            str(db),
            "--guided-campaign-id",
            GUIDED,
            "--independent-campaign-id",
            INDEPENDENT,
            "--output",
            str(output),
            "--table-output",
            str(table),
        ]
    )

    assert exit_code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["scoring_version"] == "comparison-scoring-3"
    assert "post-hoc" in payload["report_scope"]

    success = payload["arms"]["guided"]["success"]
    assert set(success) == {
        "scope",
        "successes",
        "scorable_episodes",
        "committed_episodes",
        "excluded_episodes",
        "success_rate_percent",
        "rate_scope",
        "availability",
        "success_execution_record_ids",
    }
    # An empty arm withholds its rate instead of reporting a completed zero.
    assert success["committed_episodes"] == 0
    assert success["success_rate_percent"] is None
    assert success["availability"] == "unscorable"

    failures = payload["arms"]["independent"]["failures"]
    assert failures["non_episode_generations"]["total"] == 0
    assert failures["episode_execution_attempts"]["total"] == 0
    assert "must not be added" in failures["unit_note"]
    assert (
        failures["invalid_or_failed_attempts"]["display_name"]
        == "未形成有效 Episode 的代次"
    )

    rendered = table.read_text(encoding="utf-8")
    assert "## 成功 Episode（可计分样本内，不比较两臂）" in rendered
    assert "## 失败分解（两个口径，单位不同，不可相加）" in rendered
    assert "未形成有效 Episode 的代次" in rendered
    assert "不给两臂成功率差值" in rendered


def test_non_episode_reason_is_traced_or_marked_unknown() -> None:
    class Preparation:
        class state:  # noqa: N801 - mirrors the pydantic model attribute layout
            value = "rejected"

        class outcome:  # noqa: N801 - mirrors the pydantic model attribute layout
            reason_codes = ("operator-rejected",)

    class Store:
        def __init__(self, preparation) -> None:
            self._preparation = preparation

        def load_preparation_for_allocation(self, campaign_id, allocation_id):
            return self._preparation

    settlement = {
        "settlement_id": "non-episode-settlement.one",
        "generation_allocation_id": "allocation.one",
        "disposition": "preparation_rejected",
        "work_id": None,
        "attempt_receipt_ids": [],
    }

    found = v2_comparison_report._non_episode_reason(
        store=Store(Preparation()), campaign_id="campaign", settlement=settlement
    )
    assert found["reason_status"] == "found"
    assert "operator-rejected" in found["reasons"]
    assert "mutation_preparation.outcome.reason_codes" in found["reason_sources"]
    # The settlement table carries no top-level reason code, and that absence is
    # reported as absent rather than as a recorded null.
    assert found["settlement_reason_code_present"] is False

    unknown = v2_comparison_report._non_episode_reason(
        store=Store(None), campaign_id="campaign", settlement=settlement
    )
    assert unknown["reason_status"] == "unknown"
    assert unknown["reasons"] == []
