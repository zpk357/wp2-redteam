"""Attack-effectiveness main report (AE-01, AE-06).

Local acceptance for TASK-ATTACK-EFFECTIVENESS-20260920 / A03.  The report is
built from real persisted Campaigns (the same synthetic runner the comparison
sample uses), so the main table, the diagnostic demotion and the provenance
labels are checked against stored facts rather than hand-written payloads.
"""

from __future__ import annotations

import pytest

from sandbox.errors import RuntimeTimeoutError
from sandbox.fuzzer.v2_bootstrap import build_exploratory_bootstrap
from sandbox.fuzzer.v2_campaign_store import V2CampaignStore
from sandbox.fuzzer.v2_comparison_report import (
    build_comparison_arm,
    build_v2_comparison_report,
    render_v2_comparison_table,
)
from sandbox.fuzzer.v2_effectiveness_protocol import freeze_effectiveness_protocol
from sandbox.fuzzer.v2_effectiveness_report import EFFECTIVENESS_REPORT_VERSION
from sandbox.fuzzer.v2_real_episode import OfficeV2EpisodeResult
from sandbox.fuzzer.v2_real_runtime import run_or_resume_exploratory_campaign
from sandbox.fuzzer.v2_strategy import CampaignStrategy
from sandbox.mutation.v2_provider import RuleBasedV2MutationProvider
from tests.unit.test_office_v2_comparison_sample import (
    GENERATIONS,
    GUIDED,
    SyntheticEpisodeRunner,
    _run_campaign,
)

INDEPENDENT = "sample.independent"


class _TimeoutRunner:
    """Every execution attempt fails; no Episode result is ever produced."""

    def __init__(self) -> None:
        self.recover_recordings = False

    def cleanup_interrupted(self, campaign_id: str) -> None:
        return None

    async def execute(self, **kwargs) -> OfficeV2EpisodeResult:
        raise RuntimeTimeoutError("synthetic Episode timeout")


class _PartialTokenUsageRunner(SyntheticEpisodeRunner):
    """A real Episode whose recording carries usage for only some decisions."""

    async def execute(self, **kwargs) -> OfficeV2EpisodeResult:
        episode = await super().execute(**kwargs)
        return episode.model_copy(
            update={
                "agent_tokens_incomplete": True,
                "agent_token_decisions": 2,
                "agent_token_missing_decisions": 1,
            }
        )


class _FirstAttemptTimeoutRunner(SyntheticEpisodeRunner):
    """One timed-out attempt that the bounded retry replaces with a real Episode."""

    def __init__(self, *, data_root) -> None:
        super().__init__(data_root=data_root)
        self.failures_left = 1

    async def execute(self, **kwargs) -> OfficeV2EpisodeResult:
        if self.failures_left:
            self.failures_left -= 1
            raise RuntimeTimeoutError("first attempt timed out; the retry settles")
        return await super().execute(**kwargs)


def _protocol(**overrides):
    values = {
        "target_decisive_k": 1,
        "scheduling_limit": 2,
        "execution_attempt_limit": 2,
        "consecutive_infra_pause_threshold": 9,
    }
    values.update(overrides)
    return freeze_effectiveness_protocol(**values)


def _run_protocol_campaign(
    *, store, data_root, campaign_id, runner, protocol, strategy
) -> None:
    run_or_resume_exploratory_campaign(
        store=store,
        campaign_id=campaign_id,
        bootstrap=build_exploratory_bootstrap(episode_limit=protocol.scheduling_limit),
        generation_count=protocol.target_decisive_k,
        mutation_provider=RuleBasedV2MutationProvider(),
        episode_runner=runner,
        strategy=strategy,
        effectiveness_protocol=protocol,
    )


def _local_pair(tmp_path):
    """Two real Campaigns whose arms were never frozen under the protocol."""

    data_root = tmp_path / "data"
    data_root.mkdir()
    bootstrap = build_exploratory_bootstrap(episode_limit=GENERATIONS)
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        store.create_campaign(campaign_id=GUIDED, initial_state=bootstrap.initial_state)
        store.create_campaign(
            campaign_id=INDEPENDENT,
            initial_state=bootstrap.initial_state,
            strategy="random_independent",
        )
        _run_campaign(
            store=store,
            data_root=data_root,
            campaign_id=GUIDED,
            strategy=CampaignStrategy.COVERAGE_GUIDED,
            campaign_seed_value=7,
            bootstrap=bootstrap,
        )
        _run_campaign(
            store=store,
            data_root=data_root,
            campaign_id=INDEPENDENT,
            strategy=CampaignStrategy.RANDOM_INDEPENDENT,
            campaign_seed_value=7,
            bootstrap=bootstrap,
        )
        report = build_v2_comparison_report(
            store=store,
            guided_campaign_id=GUIDED,
            independent_campaign_id=INDEPENDENT,
            data_root=data_root,
        )
        arm = build_comparison_arm(
            store=store, campaign_id=GUIDED, data_root=data_root
        )
    return report, arm


def test_main_table_leads_with_attack_effectiveness(tmp_path) -> None:
    report, arm = _local_pair(tmp_path)
    main = report["effectiveness"]
    guided = main["arms"]["guided"]
    independent = main["arms"]["independent"]

    assert report["report_version"] == EFFECTIVENESS_REPORT_VERSION
    assert report["main_scoring"] == "attack-effectiveness"
    assert guided["scoring"] == "post-hoc-reclassification"
    assert guided["classification_version"] == "attack-effectiveness-v1"
    # Benign Episodes never attempt the frozen target, so the rate is a real 0.0
    # inside the decidable sample — not a withheld value and not a success.
    assert guided["successes"] == 0
    assert guided["decisive"] == GENERATIONS
    assert guided["success_rate_percent"] == "0.0"
    assert guided["distinct_success_targets"] == []
    assert guided["undetermined"] == 0
    assert guided["exclusion_rate_percent"] == "0.0"
    assert guided["valid_committed_episodes"] == GENERATIONS
    # An arm that predates the protocol carries no fabricated K or limits.
    assert guided["target_decisive_k"] is None
    assert guided["target_reached"] is None
    assert "事后重算" in guided["scoring_note"]
    assert guided == arm["effectiveness"]

    comparison = main["comparison"]
    assert comparison["conditions"]["both_scored_by_protocol"] is False
    # A re-scored arm never yields a rate difference that could read as a result.
    assert comparison["differences"]["success_rate_percent_points"] is None
    assert "事后重算" in comparison["differences"]["success_rate_withheld_reason"]
    assert comparison["claims"]["superiority"] == "not-established"

    table = render_v2_comparison_table(report)
    assert table.index("# 攻击效果主表") < table.index("# 诊断：覆盖率与失败分解")
    assert (
        f"| 攻击成功 S | {guided['successes']} | {independent['successes']} |" in table
    )
    assert (
        f"| 可判定 N | {guided['decisive']} | {independent['decisive']} |" in table
    )
    assert "成功率 S/N（可判定样本内，%） | 0.0 | 0.0 |" in table
    assert "优越性结论：not-established" in table
    assert "采样口径" in table


def test_protocol_arms_report_k_and_a_partial_arm(tmp_path) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    guided_protocol = _protocol(target_decisive_k=1)
    # K=2 with only two execution attempts: the first generation spends one
    # attempt on a timed-out try and one on its retry, so the cap stops the arm
    # with N=1 < K -> a real partial arm, not a fabricated shortfall.
    partial_protocol = _protocol(
        target_decisive_k=2, scheduling_limit=3, execution_attempt_limit=2
    )
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        _run_protocol_campaign(
            store=store,
            data_root=data_root,
            campaign_id="pair.guided",
            runner=SyntheticEpisodeRunner(data_root=data_root),
            protocol=guided_protocol,
            strategy=CampaignStrategy.COVERAGE_GUIDED,
        )
        _run_protocol_campaign(
            store=store,
            data_root=data_root,
            campaign_id="pair.partial",
            runner=_FirstAttemptTimeoutRunner(data_root=data_root),
            protocol=partial_protocol,
            strategy=CampaignStrategy.RANDOM_INDEPENDENT,
        )
        report = build_v2_comparison_report(
            store=store,
            guided_campaign_id="pair.guided",
            independent_campaign_id="pair.partial",
        )

    guided = report["effectiveness"]["arms"]["guided"]
    partial = report["effectiveness"]["arms"]["independent"]
    assert guided["scoring"] == "frozen-protocol"
    assert guided["target_decisive_k"] == 1
    assert guided["target_reached"] is True
    assert guided["not_reached_reason"] is None
    assert guided["scheduling_attempts"] == 1
    assert guided["cost"]["agent_tokens_complete"] is True
    assert partial["target_reached"] is False
    assert partial["not_reached_reason"] == "execution-attempt-budget-exhausted"
    assert partial["decisive"] == 1
    assert partial["target_decisive_k"] == 2

    comparison = report["effectiveness"]["comparison"]
    assert comparison["conditions"]["both_scored_by_protocol"] is True
    assert comparison["conditions"]["k_equal"] is False
    # One arm reached its K and the other did not, so no rate difference is given;
    # reaching K on both would still not be a superiority claim.
    assert comparison["differences"]["success_rate_percent_points"] is None
    assert "达标状态不同" in comparison["differences"]["success_rate_withheld_reason"]
    assert comparison["claims"]["superiority"] == "not-established"

    table = render_v2_comparison_table(report)
    assert "| 是否达到 K | 是 | 否 |" in table
    assert "| 未达标原因 | 未定义 | execution-attempt-budget-exhausted |" in table
    assert "| 有效目标 K | 1 | 2 |" in table


def test_all_exceptional_arm_has_no_rate_and_reports_the_stop(tmp_path) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    protocol = _protocol(
        target_decisive_k=1, scheduling_limit=4, execution_attempt_limit=4
    )
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        _run_protocol_campaign(
            store=store,
            data_root=data_root,
            campaign_id="all-exceptional",
            runner=_TimeoutRunner(),
            protocol=protocol,
            strategy=CampaignStrategy.COVERAGE_GUIDED,
        )
        arm = build_comparison_arm(store=store, campaign_id="all-exceptional")
        report = build_v2_comparison_report(
            store=store,
            guided_campaign_id="all-exceptional",
            independent_campaign_id="all-exceptional",
        )

    effectiveness = arm["effectiveness"]
    assert effectiveness["decisive"] == 0
    assert effectiveness["successes"] == 0
    # N = 0 means the rate is undefined, never zero.
    assert effectiveness["success_rate_percent"] is None
    assert effectiveness["settled_episodes"] == 0
    assert effectiveness["exclusion_rate_percent"] is None
    assert effectiveness["infra_error_generations"] == 2
    assert effectiveness["scheduling_attempts"] == 2
    assert effectiveness["execution_attempts"] == 4
    assert effectiveness["target_reached"] is False
    assert effectiveness["not_reached_reason"] == "execution-attempt-budget-exhausted"
    assert effectiveness["by_classification"]["infra-error"] == 0
    assert effectiveness["by_classification"]["undetermined"] == 0

    table = render_v2_comparison_table(report)
    assert "| 成功率 S/N（可判定样本内，%） | 未定义 | 未定义 |" in table
    assert "| INFRA_ERROR（代次） | 2 | 2 |" in table


def test_incomplete_token_usage_is_marked_not_zeroed(tmp_path) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    protocol = _protocol(target_decisive_k=1)
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        _run_protocol_campaign(
            store=store,
            data_root=data_root,
            campaign_id="partial-cost",
            runner=_PartialTokenUsageRunner(data_root=data_root),
            protocol=protocol,
            strategy=CampaignStrategy.COVERAGE_GUIDED,
        )
        arm = build_comparison_arm(store=store, campaign_id="partial-cost")

    cost = arm["effectiveness"]["cost"]
    assert cost["agent_tokens_complete"] is False
    assert cost["incomplete_cost_receipts"]
    # The recorded part is kept; the missing usage is not added as zero.
    assert cost["agent_tokens"] == 1_500
    assert "不补零" in cost["note"]
    assert arm["cost"]["agent_tokens_complete"] is False


def test_injected_arms_without_effectiveness_are_tolerated(monkeypatch) -> None:
    """Older or hand-built arm payloads must not crash the report."""

    import sandbox.fuzzer.v2_comparison_report as comparison_report
    from tests.unit.test_office_v2_comparison_report import (
        _GUIDED_VALUES,
        _INDEPENDENT_VALUES,
        _prepared_arm,
    )
    from tests.unit.test_office_v2_comparison_report import GUIDED as PAIRED_GUIDED
    from tests.unit.test_office_v2_comparison_report import (
        INDEPENDENT as PAIRED_INDEPENDENT,
    )

    prepared = {
        PAIRED_GUIDED: _prepared_arm(
            PAIRED_GUIDED, strategy="coverage_guided", values=_GUIDED_VALUES
        ),
        PAIRED_INDEPENDENT: _prepared_arm(
            PAIRED_INDEPENDENT,
            strategy="random_independent",
            values=_INDEPENDENT_VALUES,
        ),
    }
    monkeypatch.setattr(
        comparison_report,
        "build_comparison_arm",
        lambda *, store, campaign_id, data_root=None: prepared[campaign_id],
    )
    report = comparison_report.build_v2_comparison_report(
        store=object(),
        guided_campaign_id=PAIRED_GUIDED,
        independent_campaign_id=PAIRED_INDEPENDENT,
    )
    assert report["effectiveness"]["arms"]["guided"]["scoring"] == "unavailable"
    assert (
        report["effectiveness"]["comparison"]["differences"][
            "success_rate_percent_points"
        ]
        is None
    )
    table = render_v2_comparison_table(report)
    assert "不可用" in table
    assert "# 攻击效果主表" in table


def test_arm_payload_is_serialisable_and_repeatable(tmp_path) -> None:
    import json

    report, _arm = _local_pair(tmp_path)
    serialised = json.dumps(report, ensure_ascii=False)
    assert EFFECTIVENESS_REPORT_VERSION in serialised
    # Two builds of the same persisted pair agree on the main numbers.
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        again = build_v2_comparison_report(
            store=store,
            guided_campaign_id=GUIDED,
            independent_campaign_id=INDEPENDENT,
        )
    assert again["effectiveness"]["arms"] == report["effectiveness"]["arms"]
    assert pytest.approx(0.0) == float(
        report["effectiveness"]["arms"]["guided"]["success_rate_percent"]
    )
