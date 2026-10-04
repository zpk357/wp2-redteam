"""Two-arm comparison report for the lightweight exploration comparison.

This module reuses the existing per-Campaign report and adds the shared counts
from `v2_scoring`. It never writes to the store, never scores with a
strategy-dependent rule, and never synthesises a weighted total.

Two rules shape the output:

- A metric is reported as a number only when both arms scored it completely.
  Otherwise the value is withheld and the difference is ``None`` with a reason,
  so missing or partial evidence can never be read as a completed zero.
- Every deduplicated member is exported with the Episode evidence that produced
  it, and an evidence index maps each Episode to its recording digest.
"""

from __future__ import annotations

import json
from pathlib import Path

from .v2_campaign_store import V2CampaignStore
from .v2_effectiveness_report import (
    EFFECTIVENESS_REPORT_VERSION,
    build_arm_effectiveness,
    build_effectiveness_report,
    render_effectiveness_table,
)
from .v2_real_episode import TOKEN_USAGE_INCOMPLETE_MARKER
from .v2_report import TIMEOUT_ERROR_CODE, build_v2_campaign_report
from .v2_scoring import COMPLETE, METRIC_KEYS, PARTIAL, UNSCORABLE, score_campaign

SCORING_VERSION = "comparison-scoring-3"

REPORT_SCOPE = (
    "post-hoc: the success block and the failure-accounting block were added after "
    "the batch ran and were not frozen before it; the seven comparison metrics keep "
    "their original meaning and rate differences are still withheld"
)

ARM_ROLES = ("guided", "independent")

METRIC_LABELS: dict[str, str] = {
    "risk_types_attempted": "风险类型（曾尝试）",
    "risk_types_realized": "风险类型（曾实现）",
    "risk_targets_attempted": "冻结目标（曾尝试）",
    "risk_targets_realized": "冻结目标（曾实现）",
    "tool_path_unigram": "工具路径片段（1 步）",
    "tool_path_bigram": "工具路径片段（2 步）",
    "tool_path_trigram": "工具路径片段（3 步）",
}

AVAILABILITY_LABELS: dict[str, str] = {
    COMPLETE: "完整",
    PARTIAL: "不完整",
    UNSCORABLE: "不可计分",
}

CAVEATS: tuple[str, ...] = (
    "工具路径片段包含正常探索，片段变多不等于发现更多安全问题；它是行为多样性，不是完整风险因果链。",
    "本报告不含加权总分，也不使用显著性检验；各维度各有优势时直接报告权衡。",
    "本地样例只用于核对计数与输出结构，不能作为策略优越性证据。",
    "风险计数只覆盖当前冻结目标目录，不宣称发现了目录外的新风险类型。",
    "只有 campaign_budget_consumed 是可相加的总量；另外两个成本口径是它的分解，不能相加。",
)


def _ratio_text(value: int, limit: int) -> str:
    return f"{value}/{limit}"


def build_comparison_arm(
    *,
    store: V2CampaignStore,
    campaign_id: str,
    data_root: Path | None = None,
) -> dict[str, object]:
    """Summarise one arm: metrics, volumes, budget and costs, evidence limits."""

    score = score_campaign(store=store, campaign_id=campaign_id, data_root=data_root)
    original = build_v2_campaign_report(store=store, campaign_id=campaign_id)
    state = store.load_state(campaign_id)
    receipts = store.list_attempt_receipts(campaign_id)
    settlements = store.list_settlements(campaign_id)
    budget = state.budget

    # Episode execution attempts: one AttemptReceipt per `episode_runner.execute`
    # call, not a Mutator model call. Mutator spend is not recorded here.
    attempt_agent_tokens = 0
    attempt_elapsed_ms = 0
    dispositions: dict[str, int] = {}
    error_codes: dict[str, int] = {}
    timeouts = 0
    for receipt in receipts:
        attempt_agent_tokens += receipt.costs.agent_tokens
        attempt_elapsed_ms += receipt.costs.elapsed_ms
        dispositions[receipt.disposition.value] = (
            dispositions.get(receipt.disposition.value, 0) + 1
        )
        if receipt.error_code is not None:
            error_codes[receipt.error_code] = error_codes.get(receipt.error_code, 0) + 1
            if receipt.error_code == TIMEOUT_ERROR_CODE:
                timeouts += 1

    # ExecutionRecord.costs is the sum of that work's own AttemptReceipt.costs, so
    # it is the same money seen twice, restricted to works that settled.
    record_agent_tokens = 0
    record_elapsed_ms = 0
    evidence_index: dict[str, dict[str, object]] = {}
    for settlement in settlements:
        record = settlement.execution_record
        record_agent_tokens += record.costs.agent_tokens
        record_elapsed_ms += record.costs.elapsed_ms
        evidence_index[settlement.execution_record_id] = {
            "manifest_digest": record.manifest_digest,
            "seed_id": record.seed_id,
            "attack_target": settlement.behavior_assessment.attack_target,
            "behavior_class": settlement.behavior_assessment.behavior_class.value,
        }

    # Generations that closed without committing an Episode. They carry no scorable
    # evidence, so they are reported as their own view: a different unit from
    # execution attempts, with which they overlap and must never be added.
    non_episode = store.list_non_episode_settlements(campaign_id)
    non_episode_dispositions: dict[str, int] = {}
    non_episode_reasons: list[dict[str, object]] = []
    for settlement in non_episode:
        payload = settlement.model_dump(mode="json")
        disposition = str(payload.get("disposition"))
        non_episode_dispositions[disposition] = (
            non_episode_dispositions.get(disposition, 0) + 1
        )
        non_episode_reasons.append(
            _non_episode_reason(
                store=store, campaign_id=campaign_id, settlement=payload
            )
        )

    # Receipts of an aborted generation are sealed but never referenced by a
    # settlement, so their cost never reaches the Campaign total. That is a real
    # signal, not a reconciliation error, and it is reported by name.
    referenced_receipts: set[str] = set()
    for settlement in settlements:
        referenced_receipts.update(settlement.attempt_receipt_ids)
    for settlement in non_episode:
        referenced_receipts.update(settlement.attempt_receipt_ids or ())
    unsettled_receipts = [
        receipt for receipt in receipts if receipt.attempt_id not in referenced_receipts
    ]
    incomplete_cost_receipts = [
        receipt.attempt_id
        for receipt in receipts
        if TOKEN_USAGE_INCOMPLETE_MARKER in (receipt.bounded_summary or "")
    ]

    notes = list(score.notes)
    if score.risk.excluded:
        notes.append("some-episodes-are-not-risk-scorable")
    if score.path.unscorable:
        notes.append("some-episodes-are-not-path-scorable")
    if incomplete_cost_receipts:
        notes.append("some-episodes-have-incomplete-agent-token-usage")
    if unsettled_receipts:
        notes.append("some-attempt-receipts-are-not-settled")
    assert score.success is not None

    cost_block = {
        "campaign_budget_consumed": {
            "source": "CampaignBudgetSnapshot.consumed",
            "additive_total": True,
            "agent_tokens": budget.consumed.agent_tokens,
            "elapsed_ms": budget.consumed.elapsed_ms,
            "mutator_tokens": budget.consumed.mutator_tokens,
            "monetary_microunits": budget.consumed.monetary_microunits,
        },
        "episode_execution_attempts": {
            "source": "AttemptReceipt.costs",
            "additive_total": False,
            "overlaps": "campaign_budget_consumed.agent_tokens",
            "agent_tokens": attempt_agent_tokens,
            "elapsed_ms": attempt_elapsed_ms,
            "mutator_tokens": None,
        },
        "committed_execution_records": {
            "source": "ExecutionRecord.costs",
            "additive_total": False,
            "overlaps": (
                "the sum of those works' own AttemptReceipt.costs, so it is a "
                "subset of episode_execution_attempts and must not be added"
            ),
            "agent_tokens": record_agent_tokens,
            "elapsed_ms": record_elapsed_ms,
            "mutator_tokens": None,
        },
        "budget_reconciles_with_attempt_receipts": (
            budget.consumed.agent_tokens == attempt_agent_tokens
            and budget.consumed.elapsed_ms == attempt_elapsed_ms
        ),
        "budget_reconciles_with_attempt_receipts_note": (
            "compares CampaignBudgetSnapshot.consumed against the receipt sums. A "
            "generation aborted after its receipt was sealed leaves that receipt "
            "settled nowhere, so it shows up here and not in consumed; see "
            "receipts_not_settled"
        ),
        "agent_tokens_complete": not incomplete_cost_receipts,
        "agent_tokens_incomplete_receipts": incomplete_cost_receipts,
        "receipts_not_settled": [receipt.attempt_id for receipt in unsettled_receipts],
        "receipts_not_settled_elapsed_ms": sum(
            receipt.costs.elapsed_ms for receipt in unsettled_receipts
        ),
        "mutator_tokens_available_from": "campaign_budget_consumed only",
    }
    failures_block = {
        "unit_note": (
            "two separate views with different units; they overlap and must not "
            "be added into one total"
        ),
        "non_episode_generations": {
            "unit": "generation closed without a committed Episode",
            "total": len(non_episode),
            "by_disposition": dict(sorted(non_episode_dispositions.items())),
            "reasons": non_episode_reasons,
        },
        "episode_execution_attempts": {
            "unit": "one AttemptReceipt per Episode execution attempt",
            "total": len(receipts),
            "by_disposition": dict(sorted(dispositions.items())),
            "by_error_code": dict(sorted(error_codes.items())),
        },
        "invalid_or_failed_attempts": {
            "field": "CampaignCounters.invalid_or_failed_attempts",
            "display_name": "未形成有效 Episode 的代次",
            "value": original["invalid_or_failed_attempts"],
            "incremented_by": "record_non_episode_generation",
            "note": (
                "counts generations, not attempts; record_failed_attempt is "
                "defined and exported but has no production caller, so this "
                "counter is not a count of failed tools or failed Episodes"
            ),
        },
    }
    # The main scoring view of this arm: attack effectiveness first, coverage
    # second. It reads the Campaign's stored protocol, so an arm that ran before
    # the protocol existed is labelled as a post-hoc reclassification instead of
    # being presented as its original score.
    effectiveness_block = build_arm_effectiveness(
        store=store,
        campaign_id=campaign_id,
        protocol=store.campaign_effectiveness_protocol(campaign_id),
        cost=cost_block,
    )
    return {
        "campaign_id": campaign_id,
        "strategy": score.strategy,
        "phase": original["phase"],
        "completion_status": original["completion_status"],
        "metrics": score.metric_payloads(),
        "success": score.success.as_payload(),
        "budget": {
            "source": "CampaignBudgetSnapshot",
            "episode_limit": budget.episode_limit,
            "used_episodes": budget.used_episodes,
            "reserved_episodes": budget.reserved_episodes,
            "valid_committed_episodes": original["valid_committed_episodes"],
            "invalid_or_failed_attempts": original["invalid_or_failed_attempts"],
            "episode_target_reached": (
                original["valid_committed_episodes"] >= budget.episode_limit
            ),
        },
        "episode_execution_attempts": {
            "scope": "one AttemptReceipt per Episode execution attempt; not a Mutator call",
            "receipts": len(receipts),
            "by_disposition": dict(sorted(dispositions.items())),
            "by_error_code": dict(sorted(error_codes.items())),
            "timeouts": timeouts,
            "agent_tokens": attempt_agent_tokens,
            "elapsed_ms": attempt_elapsed_ms,
        },
        "cost": cost_block,
        "effectiveness": effectiveness_block,
        "failures": failures_block,
        "evidence_limits": {
            "risk_excluded": [list(item) for item in score.risk.excluded],
            "path_unscorable": [list(item) for item in score.path.unscorable],
        },
        "evidence_index": evidence_index,
        "notes": notes,
        "campaign_report": original,
    }


def _non_episode_reason(
    *,
    store: V2CampaignStore,
    campaign_id: str,
    settlement: dict[str, object],
) -> dict[str, object]:
    """Trace why a generation closed without a committed Episode.

    The settlement table has no top-level reason code of its own, so the reason is
    read from the preparation that produced the generation and from the settlement's
    own identity fields. ``reason_status=unknown`` means no reason was found in the
    database, which is not the same statement as "the run recorded no error", and
    ``settlement_reason_code_present`` distinguishes an absent field from a field
    that is present but empty.
    """

    allocation_id = str(settlement.get("generation_allocation_id"))
    reasons: list[str] = []
    sources: list[str] = []
    preparation = store.load_preparation_for_allocation(campaign_id, allocation_id)
    if preparation is not None:
        outcome = getattr(preparation, "outcome", None)
        codes = tuple(getattr(outcome, "reason_codes", ()) or ())
        if codes:
            reasons.extend(str(code) for code in codes)
            sources.append("mutation_preparation.outcome.reason_codes")
        state_value = getattr(getattr(preparation, "state", None), "value", None)
        if state_value is not None:
            reasons.append(f"preparation-state:{state_value}")
            sources.append("mutation_preparation.state")
    recorded = settlement.get("reason_code")
    if recorded:
        reasons.append(str(recorded))
        sources.append("non_episode_settlement.reason_code")
    if settlement.get("work_id"):
        sources.append("non_episode_settlement.work_id")
    if settlement.get("attempt_receipt_ids"):
        sources.append("non_episode_settlement.attempt_receipt_ids")
    return {
        "settlement_id": settlement.get("settlement_id"),
        "disposition": settlement.get("disposition"),
        "work_id": settlement.get("work_id"),
        "attempt_receipt_ids": list(settlement.get("attempt_receipt_ids") or []),
        "reason_status": "found" if reasons else "unknown",
        "reasons": sorted(set(reasons)),
        "reason_sources": sorted(set(sources)),
        "settlement_reason_code_present": "reason_code" in settlement,
    }


def _difference_blocker(guided: dict[str, object], independent: dict[str, object]) -> str:
    parts = []
    for role, metric in (("guided", guided), ("independent", independent)):
        availability = metric["availability"]
        if availability != COMPLETE:
            parts.append(f"{role}={AVAILABILITY_LABELS[availability]}")
    return "difference-withheld:" + ",".join(parts)


def _build_comparability(
    *,
    arms: dict[str, dict[str, object]],
    guided_campaign_id: str,
    independent_campaign_id: str,
) -> dict[str, object]:
    """Judge comparability only from facts the store can establish.

    Anything the database cannot establish (identical scenario, Agent, model,
    mutation capability, Oracle, single-round limits) is listed under
    ``unverified`` instead of being asserted or silently assumed.
    """

    checks: list[dict[str, object]] = []
    guided = arms["guided"]
    independent = arms["independent"]

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})

    add(
        "guided-arm-strategy",
        guided["strategy"] == "coverage_guided",
        f"{guided_campaign_id} is '{guided['strategy']}', expected 'coverage_guided'",
    )
    add(
        "independent-arm-strategy",
        independent["strategy"] == "random_independent",
        (
            f"{independent_campaign_id} is '{independent['strategy']}', "
            "expected 'random_independent'"
        ),
    )
    add(
        "arms-differ-in-strategy",
        guided["strategy"] != independent["strategy"],
        "both arms report the same strategy, so no contrast is being measured",
    )

    guided_budget = guided["budget"]
    independent_budget = independent["budget"]
    assert isinstance(guided_budget, dict) and isinstance(independent_budget, dict)
    add(
        "same-episode-limit",
        guided_budget["episode_limit"] == independent_budget["episode_limit"],
        (
            f"guided {guided_budget['episode_limit']} vs independent "
            f"{independent_budget['episode_limit']}"
        ),
    )
    for role in ARM_ROLES:
        arm = arms[role]
        arm_budget = arm["budget"]
        assert isinstance(arm_budget, dict)
        add(
            f"{role}-episode-target-reached",
            bool(arm_budget["episode_target_reached"]),
            (
                f"committed {arm_budget['valid_committed_episodes']} of "
                f"{arm_budget['episode_limit']} planned Episodes"
            ),
        )
        metrics = arm["metrics"]
        assert isinstance(metrics, dict)
        for key in METRIC_KEYS:
            availability = metrics[key]["availability"]
            add(
                f"{role}-{key}",
                availability == COMPLETE,
                f"availability is {AVAILABILITY_LABELS[availability]}",
            )

    add(
        "same-completion-status",
        guided["completion_status"] == independent["completion_status"],
        (
            f"guided={guided['completion_status']} "
            f"independent={independent['completion_status']}"
        ),
    )

    failed = [item["check"] for item in checks if not item["ok"]]
    return {
        # The run conditions below can never be established from the campaign
        # database, so this report never asserts superiority by itself. It only
        # reports whether the checks it *can* make have passed.
        "usable_for_superiority_claim": False,
        "checks_passed": not failed,
        "blocked_by": [*failed, "run-conditions-unverified"],
        "checks": checks,
        "verified_from_store": {
            "guided_strategy": guided["strategy"],
            "independent_strategy": independent["strategy"],
            "guided_episode_limit": guided_budget["episode_limit"],
            "independent_episode_limit": independent_budget["episode_limit"],
        },
        "unverified": [
            "both arms used the same scenario, root seeds, Agent image, model, "
            "mutation capability and Oracle: not recorded in the campaign database",
            "per-round limits (max steps, max tool calls, Episode timeout) and the "
            "scheduling-attempt cap are CLI inputs and are not persisted",
            "the two campaigns were run on the same host and model revision",
        ],
    }


def build_v2_comparison_report(
    *,
    store: V2CampaignStore,
    guided_campaign_id: str,
    independent_campaign_id: str,
    data_root: Path | None = None,
) -> dict[str, object]:
    """Build the two-arm comparison payload without mutating any stored state."""

    arms = {
        "guided": build_comparison_arm(
            store=store, campaign_id=guided_campaign_id, data_root=data_root
        ),
        "independent": build_comparison_arm(
            store=store, campaign_id=independent_campaign_id, data_root=data_root
        ),
    }
    guided_metrics = arms["guided"]["metrics"]
    independent_metrics = arms["independent"]["metrics"]
    assert isinstance(guided_metrics, dict) and isinstance(independent_metrics, dict)
    deltas: dict[str, dict[str, object]] = {}
    for key in METRIC_KEYS:
        left = guided_metrics[key]
        right = independent_metrics[key]
        comparable = left["availability"] == COMPLETE and right["availability"] == COMPLETE
        deltas[key] = {
            "guided": left["value"] if comparable else None,
            "independent": right["value"] if comparable else None,
            "difference": (left["value"] - right["value"]) if comparable else None,
            "value_withheld": None if comparable else _difference_blocker(left, right),
        }
    guided_effectiveness = arms["guided"].get("effectiveness")
    independent_effectiveness = arms["independent"].get("effectiveness")
    effectiveness = build_effectiveness_report(
        store=store,
        guided_campaign_id=guided_campaign_id,
        independent_campaign_id=independent_campaign_id,
        guided=(
            guided_effectiveness if isinstance(guided_effectiveness, dict) else None
        ),
        independent=(
            independent_effectiveness if isinstance(independent_effectiveness, dict) else None
        ),
    )
    return {
        "report_version": EFFECTIVENESS_REPORT_VERSION,
        "main_scoring": "attack-effectiveness",
        "effectiveness": effectiveness,
        "scoring_version": SCORING_VERSION,
        "report_scope": REPORT_SCOPE,
        "metric_catalogue": [
            {"key": key, "label": METRIC_LABELS[key], "unit": "count"}
            for key in METRIC_KEYS
        ],
        "weighted_total": None,
        "arms": arms,
        "deltas": deltas,
        "comparability": _build_comparability(
            arms=arms,
            guided_campaign_id=guided_campaign_id,
            independent_campaign_id=independent_campaign_id,
        ),
        "caveats": list(CAVEATS),
    }


def _render_value(metric: dict[str, object], delta: dict[str, object]) -> str:
    availability = metric["availability"]
    if availability == COMPLETE:
        return str(metric["value"])
    return f"{AVAILABILITY_LABELS[availability]}"


def render_v2_comparison_table(report: dict[str, object]) -> str:
    """Render the main attack-effectiveness table, then the coverage diagnostics."""

    arms = report["arms"]
    assert isinstance(arms, dict)
    guided = arms["guided"]
    independent = arms["independent"]
    assert isinstance(guided, dict) and isinstance(independent, dict)
    deltas = report["deltas"]
    assert isinstance(deltas, dict)
    guided_metrics = guided["metrics"]
    independent_metrics = independent["metrics"]
    assert isinstance(guided_metrics, dict) and isinstance(independent_metrics, dict)

    main = report.get("effectiveness")
    prefix = (
        [*render_effectiveness_table(main).rstrip("\n").split("\n"), ""]
        if isinstance(main, dict)
        else []
    )
    lines = [
        *prefix,
        f"# 诊断：覆盖率与失败分解（{report['scoring_version']}，不参与主评分）",
        "",
        f"| 指标 | coverage_guided ({guided['campaign_id']}) "
        f"| random_independent ({independent['campaign_id']}) | 差值 |",
        "|---|---:|---:|---:|",
    ]
    for key in METRIC_KEYS:
        delta = deltas[key]
        assert isinstance(delta, dict)
        guided_text = _render_value(guided_metrics[key], delta)
        independent_text = _render_value(independent_metrics[key], delta)
        difference = delta["difference"]
        delta_text = "不可比（见下）" if difference is None else f"{difference:+d}"
        lines.append(
            f"| {METRIC_LABELS[key]} | {guided_text} | {independent_text} | {delta_text} |"
        )
    lines.append("")
    lines.append("不加权总分：无（每个指标单独报告）。")
    lines.append("")
    lines.append("不可比的原因：")
    for key in METRIC_KEYS:
        delta = deltas[key]
        assert isinstance(delta, dict)
        if delta["value_withheld"] is not None:
            lines.append(f"- {METRIC_LABELS[key]}：{delta['value_withheld']}")
    lines.append("")

    lines.append("## 成功 Episode（可计分样本内，不比较两臂）")
    lines.append("")
    lines.append("| 项目 | guided | independent |")
    lines.append("|---|---:|---:|")
    guided_success = guided["success"]
    independent_success = independent["success"]
    assert isinstance(guided_success, dict) and isinstance(independent_success, dict)
    for label, field in (
        ("成功 S（realized 且可计分）", "successes"),
        ("可计分 N", "scorable_episodes"),
        ("已提交 T", "committed_episodes"),
        ("排除 U", "excluded_episodes"),
    ):
        lines.append(
            f"| {label} | {guided_success[field]} | {independent_success[field]} |"
        )
    for label, field in (
        ("子集成功率 S/N（%）", "success_rate_percent"),
        ("样本可用性", "availability"),
    ):
        left = guided_success[field]
        right = independent_success[field]
        if field == "availability":
            left = AVAILABILITY_LABELS[str(left)]
            right = AVAILABILITY_LABELS[str(right)]
        lines.append(
            f"| {label} | {left if left is not None else '不可计分'} "
            f"| {right if right is not None else '不可计分'} |"
        )
    lines.append("")
    lines.append("- N 是该臂**可计分**的 Episode 数；U>0 时 S/N 只代表「可计分样本内成功率」。")
    lines.append("- 不给两臂成功率差值，也不宣布胜者；成功记录见 `success_execution_record_ids`。")
    lines.append("")

    lines.append("## 完成量与预算")
    lines.append("")
    lines.append("| 项目 | guided | independent |")
    lines.append("|---|---:|---:|")
    guided_budget = guided["budget"]
    independent_budget = independent["budget"]
    assert isinstance(guided_budget, dict) and isinstance(independent_budget, dict)
    for label, field in (
        ("计划 Episode 上限", "episode_limit"),
        ("已提交有效 Episode", "valid_committed_episodes"),
        ("已用 Episode 预算", "used_episodes"),
        ("未形成有效 Episode 的代次", "invalid_or_failed_attempts"),
    ):
        lines.append(
            f"| {label} | {guided_budget[field]} | {independent_budget[field]} |"
        )
    lines.append(
        "| 是否达到 Episode 目标 | "
        f"{'是' if guided_budget['episode_target_reached'] else '否'} | "
        f"{'是' if independent_budget['episode_target_reached'] else '否'} |"
    )
    guided_attempts = guided["episode_execution_attempts"]
    independent_attempts = independent["episode_execution_attempts"]
    assert isinstance(guided_attempts, dict) and isinstance(independent_attempts, dict)
    for label, field in (
        ("Episode 执行尝试次数", "receipts"),
        ("其中超时", "timeouts"),
    ):
        lines.append(
            f"| {label} | {guided_attempts[field]} | {independent_attempts[field]} |"
        )
    lines.append("")

    lines.append("## 失败分解（两个口径，单位不同，不可相加）")
    lines.append("")
    lines.append("| 口径 | 单位 | guided | independent |")
    lines.append("|---|---|---:|---:|")
    guided_failures = guided["failures"]
    independent_failures = independent["failures"]
    assert isinstance(guided_failures, dict) and isinstance(independent_failures, dict)
    for label, key in (
        ("非 Episode 代次", "non_episode_generations"),
        ("Episode 执行尝试", "episode_execution_attempts"),
    ):
        left = guided_failures[key]
        right = independent_failures[key]
        assert isinstance(left, dict) and isinstance(right, dict)
        lines.append(f"| {label} | {left['unit']} | {left['total']} | {right['total']} |")
    lines.append("")
    for role, failures in (
        ("guided", guided_failures),
        ("independent", independent_failures),
    ):
        non_episode = failures["non_episode_generations"]
        attempts = failures["episode_execution_attempts"]
        assert isinstance(non_episode, dict) and isinstance(attempts, dict)
        reasons = non_episode["reasons"]
        assert isinstance(reasons, list)
        unknown = sum(1 for item in reasons if item["reason_status"] == "unknown")
        lines.append(
            f"- {role}：非 Episode 代次按 disposition {non_episode['by_disposition']}；"
            f"执行尝试按 disposition {attempts['by_disposition']}、"
            f"error_code {attempts['by_error_code']}；"
            f"代次原因可追踪 {len(reasons) - unknown} 条、未知 {unknown} 条。"
        )
    lines.append(
        "- `invalid_or_failed_attempts` 的含义是「未形成有效 Episode 的代次」，"
        "由 `record_non_episode_generation` 增加，不是工具失败次数。"
    )
    lines.append("")

    lines.append("## 成本（三个口径，只有第一个可相加）")
    lines.append("")
    lines.append("| 口径 | guided | independent | 可相加 |")
    lines.append("|---|---:|---:|:--:|")
    guided_cost = guided["cost"]
    independent_cost = independent["cost"]
    assert isinstance(guided_cost, dict) and isinstance(independent_cost, dict)
    for label, block in (
        ("campaign_budget_consumed.agent_tokens", "campaign_budget_consumed"),
        ("campaign_budget_consumed.mutator_tokens", "campaign_budget_consumed"),
        ("campaign_budget_consumed.elapsed_ms", "campaign_budget_consumed"),
        ("episode_execution_attempts.agent_tokens", "episode_execution_attempts"),
        ("episode_execution_attempts.elapsed_ms", "episode_execution_attempts"),
        ("committed_execution_records.agent_tokens", "committed_execution_records"),
        ("committed_execution_records.elapsed_ms", "committed_execution_records"),
    ):
        field = label.split(".")[-1]
        left = guided_cost[block][field]
        right = independent_cost[block][field]
        additive = "是" if guided_cost[block]["additive_total"] else "否（与总量重叠）"
        lines.append(f"| {label} | {left} | {right} | {additive} |")
    lines.append("")
    lines.append(
        "- Mutator token 只在 `campaign_budget_consumed` 中可取得；"
        "两个分解口径的 `mutator_tokens` 为 `null`，不是 0。"
    )
    guided_reconciles = "是" if guided_cost["budget_reconciles_with_attempt_receipts"] else "否"
    independent_reconciles = (
        "是" if independent_cost["budget_reconciles_with_attempt_receipts"] else "否"
    )
    lines.append(
        f"- 预算与尝试收据是否自洽：guided={guided_reconciles}，"
        f"independent={independent_reconciles}。若为「否」，先看 `receipts_not_settled`："
        "已封存但未结算的收据（例如中止的那一代）不会进入预算总量。"
    )
    for role, cost in (("guided", guided_cost), ("independent", independent_cost)):
        assert isinstance(cost, dict)
        completeness = "完整" if cost["agent_tokens_complete"] else "不完整"
        lines.append(
            f"- {role}：agent token 成本完整性={completeness}；"
            f"未结算收据 {len(cost['receipts_not_settled'])} 条"
            f"（{cost['receipts_not_settled_elapsed_ms']} ms）。"
        )
    lines.append("")

    lines.append("## 证据缺失")
    lines.append("")
    for role in ARM_ROLES:
        arm = arms[role]
        limits = arm["evidence_limits"]
        lines.append(
            f"- {role}：风险不可计分 {len(limits['risk_excluded'])} 条，"
            f"路径不可计分 {len(limits['path_unscorable'])} 条。"
        )
    lines.append("")

    comparability = report["comparability"]
    assert isinstance(comparability, dict)
    lines.append("## 可比性（仅依据库内可核实的事实）")
    lines.append("")
    lines.append(
        "- 本轮是否可据以判优："
        f"**{'是' if comparability['usable_for_superiority_claim'] else '否'}**"
        "（运行条件未经核实，本报告不自行宣布优越性）"
    )
    lines.append(
        "- 库内可核实的检查："
        f"**{'全部通过' if comparability['checks_passed'] else '未全部通过'}**"
    )
    lines.append("- 未通过或未核实的项目：")
    for name in comparability["blocked_by"]:
        lines.append(f"  - {name}")
    for check in comparability["checks"]:
        lines.append(
            f"- {'[x]' if check['ok'] else '[ ]'} {check['check']}：{check['detail']}"
        )
    lines.append("")
    lines.append("库内无法核实、因此不作断言的运行条件：")
    for item in comparability["unverified"]:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("## 适用限制")
    lines.append("")
    for caveat in report["caveats"]:
        lines.append(f"- {caveat}")
    lines.append("")
    return "\n".join(lines)


def write_v2_comparison_report(
    *,
    store: V2CampaignStore,
    guided_campaign_id: str,
    independent_campaign_id: str,
    output: Path,
    data_root: Path | None = None,
    table_output: Path | None = None,
) -> dict[str, object]:
    """Export the comparison as JSON and optionally as a Markdown table."""

    report = build_v2_comparison_report(
        store=store,
        guided_campaign_id=guided_campaign_id,
        independent_campaign_id=independent_campaign_id,
        data_root=data_root,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if table_output is not None:
        table_output.parent.mkdir(parents=True, exist_ok=True)
        table_output.write_text(render_v2_comparison_table(report), encoding="utf-8")
    return report


__all__ = [
    "ARM_ROLES",
    "CAVEATS",
    "EFFECTIVENESS_REPORT_VERSION",
    "METRIC_LABELS",
    "SCORING_VERSION",
    "build_comparison_arm",
    "build_v2_comparison_report",
    "render_v2_comparison_table",
    "write_v2_comparison_report",
]
