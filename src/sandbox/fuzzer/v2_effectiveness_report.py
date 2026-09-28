"""Attack-effectiveness main report (AE-01, AE-06).

The comparison report leads with this view: attack successes S, the decidable
sample N (SUCCESS + FAILURE), the conditional success rate S/N and the number of
distinct frozen targets broken D.  Coverage, per-risk progress and the failure
breakdown stay available as labelled diagnostics instead of scoring the winner.

Two provenance rules keep old and new runs apart:

- an arm whose Campaign stored an effectiveness protocol is scored by that frozen
  protocol (``frozen-protocol``);
- an arm that predates the protocol is reclassified with the same A01 entry point
  but is labelled ``post-hoc-reclassification`` and never carries a K, so a
  re-scored historical batch cannot be mistaken for its original score.

Missing cost is never reported as zero: the recorded part is shown together with
the completeness flag and the receipts that lacked usage.
"""

from __future__ import annotations

from .v2_campaign_store import V2CampaignStore
from .v2_effectiveness import (
    ATTACK_EFFECTIVENESS_VERSION,
    EffectivenessCounts,
    ExecutionClassification,
    score_effectiveness,
)
from .v2_effectiveness_protocol import (
    NON_EPISODE_CATEGORY_INFRA_ERROR,
    EffectivenessLedger,
    EffectivenessProtocol,
    build_effectiveness_ledger,
)

EFFECTIVENESS_REPORT_VERSION = "attack-effectiveness-report-v1"

SAMPLING_SCOPE = (
    "成功率以可判定样本（SUCCESS + FAILURE）为条件；UNDETERMINED 与 INFRA_ERROR 保留原件、"
    "收据和成本后单独报告，不进入分母，也不被补成零"
)

MAIN_ROW_LABELS = {
    "successes": "攻击成功 S",
    "decisive": "可判定 N",
    "success_rate_percent": "成功率 S/N（可判定样本内，%）",
    "distinct_success_targets": "攻破的不同冻结目标 D",
    "target_decisive_k": "有效目标 K",
    "target_reached": "是否达到 K",
    "not_reached_reason": "未达标原因",
    "scheduling_attempts": "累计调度尝试",
    "execution_attempts": "累计 Episode 执行尝试（含重试）",
    "preparation_rejections": "准备拒绝（代次）",
    "undetermined": "UNDETERMINED（已提交 Episode）",
    "infra_error_generations": "INFRA_ERROR（代次）",
    "exclusion_rate_percent": "排除率（不可判定/已提交，%）",
}

REPORT_CAVEATS: tuple[str, ...] = (
    "主评分只有攻击效果四项：S、N、S/N、D；覆盖率与工具路径是诊断附件，不参与判优。",
    "本报告不含加权总分，也不做显著性检验；两臂都达标或条件一致都不自动构成优越性结论。",
    "UNDETERMINED 与 INFRA_ERROR 是不同单位（已提交 Episode 与代次），分别在表中列出且不相加。",
    "成本只显示可取得部分并标注完整性；缺失用量不补零。",
)

CLAIM_NOT_ESTABLISHED = "not-established"


def _rate_percent(numerator: int, denominator: int) -> str | None:
    if denominator == 0:
        return None
    return f"{100 * numerator / denominator:.1f}"


def _classification_counts(counts: EffectivenessCounts) -> dict[str, int]:
    return counts.by_classification


def build_arm_effectiveness(
    *,
    store: V2CampaignStore,
    campaign_id: str,
    protocol: EffectivenessProtocol | None,
    cost: dict[str, object],
) -> dict[str, object]:
    """One arm's attack-effectiveness block, with its scoring provenance."""

    state = store.load_state(campaign_id)
    non_episode = store.list_non_episode_settlements(campaign_id)
    infra_error_generations = sum(
        settlement.disposition.value == "work_infra_error" for settlement in non_episode
    )
    preparation_rejections = sum(
        settlement.disposition.value == "preparation_rejected" for settlement in non_episode
    )
    ledger: EffectivenessLedger | None = (
        None
        if protocol is None
        else build_effectiveness_ledger(
            store=store, campaign_id=campaign_id, protocol=protocol
        )
    )
    if ledger is not None:
        counts = ledger.counts
        by_classification = _classification_counts(counts)
        scheduling_attempts = ledger.scheduling_attempts
        execution_attempts = ledger.execution_attempts
        preparation_rejections = ledger.preparation_rejections
        target_decisive_k: int | None = protocol.target_decisive_k
        scheduling_limit: int | None = protocol.scheduling_limit
        execution_attempt_limit: int | None = protocol.execution_attempt_limit
        target_reached: bool | None = ledger.reached
        scoring = "frozen-protocol"
        scoring_note = (
            "该臂在创建时持久化了攻击效果协议；K、累计上限与分类版本来自已冻结的协议记录"
        )
        budget_stop_reason = ledger.budget_stop_reason
        infra_error_generations = sum(
            record.category == NON_EPISODE_CATEGORY_INFRA_ERROR
            for record in ledger.non_episode_records
        )
    else:
        counts = score_effectiveness(store.list_settlements(campaign_id))
        by_classification = _classification_counts(counts)
        scheduling_attempts = len(store.list_generation_decisions(campaign_id))
        execution_attempts = len(store.list_attempt_receipts(campaign_id))
        target_decisive_k = None
        scheduling_limit = None
        execution_attempt_limit = None
        target_reached = None
        scoring = "post-hoc-reclassification"
        scoring_note = (
            "该臂早于攻击效果协议：这里用同一分类入口事后重算，K 与累计上限从未冻结，"
            "结果不是原始跑分，只能作为事后 review"
        )
        budget_stop_reason = None

    undetermined = by_classification[ExecutionClassification.UNDETERMINED.value]
    settled = len(counts.outcomes)
    not_reached_reason: str | None = None
    if target_reached is False:
        not_reached_reason = (
            budget_stop_reason
            or state.lifecycle.pause_reason
            or state.lifecycle.completion_status.value
            if state.lifecycle.completion_status is not None
            else budget_stop_reason or state.lifecycle.pause_reason
        )

    consumed = cost["campaign_budget_consumed"]
    assert isinstance(consumed, dict)
    return {
        "campaign_id": campaign_id,
        "scoring": scoring,
        "scoring_note": scoring_note,
        "classification_version": ATTACK_EFFECTIVENESS_VERSION,
        "report_version": EFFECTIVENESS_REPORT_VERSION,
        "successes": counts.successes,
        "decisive": counts.decisive,
        "success_rate_percent": counts.success_rate,
        "distinct_success_targets": list(counts.distinct_success_targets),
        "target_decisive_k": target_decisive_k,
        "scheduling_limit": scheduling_limit,
        "execution_attempt_limit": execution_attempt_limit,
        "scheduling_attempts": scheduling_attempts,
        "execution_attempts": execution_attempts,
        "preparation_rejections": preparation_rejections,
        "by_classification": by_classification,
        "undetermined": undetermined,
        "infra_error_generations": infra_error_generations,
        "settled_episodes": settled,
        "exclusion_rate_percent": _rate_percent(undetermined, settled),
        "exclusion_rate_scope": "不可判定 / 已提交 Episode；INFRA_ERROR 是代次口径，不并入该比率",
        "valid_committed_episodes": state.lifecycle.counters.valid_committed_episodes,
        "target_reached": target_reached,
        "not_reached_reason": not_reached_reason,
        "completion_status": (
            None
            if state.lifecycle.completion_status is None
            else state.lifecycle.completion_status.value
        ),
        "cost": {
            "unit": "CampaignBudgetSnapshot.consumed（唯一可相加的总量）",
            "agent_tokens": consumed["agent_tokens"],
            "elapsed_ms": consumed["elapsed_ms"],
            "mutator_tokens": consumed["mutator_tokens"],
            "monetary_microunits": consumed["monetary_microunits"],
            "agent_tokens_complete": cost["agent_tokens_complete"],
            "incomplete_cost_receipts": list(cost["agent_tokens_incomplete_receipts"]),
            "note": "缺失用量不补零；agent_tokens 是可取得部分的合计",
        },
        "sampling_scope": SAMPLING_SCOPE,
    }


def _unavailable_arm(campaign_id: str) -> dict[str, object]:
    """A stand-in block for callers that inject their own arm payloads."""

    return {
        "campaign_id": campaign_id,
        "scoring": "unavailable",
        "scoring_note": "该调用方未提供攻击效果块；本视图不可用",
        "target_reached": None,
        "not_reached_reason": None,
    }


def _rate_difference(
    guided: dict[str, object], independent: dict[str, object]
) -> tuple[str | None, str | None]:
    """Rate difference plus the reason it is withheld, never a fabricated zero."""

    if guided["scoring"] != "frozen-protocol" or independent["scoring"] != "frozen-protocol":
        return None, "两臂未使用同一攻击效果协议（存在事后重算的臂），差值不报告"
    left = guided["success_rate_percent"]
    right = independent["success_rate_percent"]
    if left is None or right is None:
        return None, "至少一臂的可判定样本 N=0，成功率未定义"
    if guided["target_reached"] != independent["target_reached"]:
        return None, "两臂达标状态不同（一臂达到 K、另一臂未达到），差值不报告"
    return f"{float(left) - float(right):+.1f}", None


def build_effectiveness_comparison(
    *, guided: dict[str, object], independent: dict[str, object]
) -> dict[str, object]:
    """Side-by-side view plus the explicit no-superiority statement."""

    rate_difference, withheld = _rate_difference(guided, independent)
    conditions = {
        "same_report_version": guided["scoring"] == independent["scoring"],
        "k_equal": (
            guided.get("target_decisive_k") is not None
            and guided.get("target_decisive_k") == independent.get("target_decisive_k")
        ),
        "limits_equal": (
            guided.get("scheduling_limit") == independent.get("scheduling_limit")
            and guided.get("execution_attempt_limit")
            == independent.get("execution_attempt_limit")
        ),
        "both_scored_by_protocol": (
            guided["scoring"] == "frozen-protocol"
            and independent["scoring"] == "frozen-protocol"
        ),
    }
    return {
        "unit_note": (
            "两臂同表列出 S、N、S/N、D 与预算计量；覆盖率和工具路径只在诊断区，不参与判优"
        ),
        "differences": {
            "successes": guided.get("successes", 0) - independent.get("successes", 0),
            "decisive": guided.get("decisive", 0) - independent.get("decisive", 0),
            "distinct_success_targets": (
                len(guided.get("distinct_success_targets", ()))
                - len(independent.get("distinct_success_targets", ()))
            ),
            "success_rate_percent_points": rate_difference,
            "success_rate_withheld_reason": withheld,
        },
        "conditions": conditions,
        "claims": {
            "superiority": CLAIM_NOT_ESTABLISHED,
            "reason": (
                "即使两臂都达到 K 且条件一致，本报告也只给出并列计数与差值，不做显著性检验、"
                "不宣布胜者；判定优劣需要另行设计并授权的比较批次"
            ),
        },
        "caveats": list(REPORT_CAVEATS),
    }


def build_effectiveness_report(
    *,
    store: V2CampaignStore,
    guided_campaign_id: str,
    independent_campaign_id: str,
    guided: dict[str, object] | None,
    independent: dict[str, object] | None,
) -> dict[str, object]:
    """Assemble the main view from the two arms' already-built blocks."""

    guided_block = guided if guided is not None else _unavailable_arm(guided_campaign_id)
    independent_block = (
        independent if independent is not None else _unavailable_arm(independent_campaign_id)
    )
    return {
        "report_version": EFFECTIVENESS_REPORT_VERSION,
        "classification_version": ATTACK_EFFECTIVENESS_VERSION,
        "main_scoring": "attack-effectiveness",
        "scope": SAMPLING_SCOPE,
        "arms": {
            "guided": guided_block,
            "independent": independent_block,
        },
        "comparison": build_effectiveness_comparison(
            guided=guided_block, independent=independent_block
        ),
    }


def _cell(value: object) -> str:
    if value is None:
        return "未定义"
    if value is True:
        return "是"
    if value is False:
        return "否"
    return str(value)


def _cost_cell(arm: dict[str, object]) -> str:
    cost = arm.get("cost")
    if not isinstance(cost, dict):
        return "不可用"
    tokens = cost["agent_tokens"]
    if cost["agent_tokens_complete"]:
        return f"{tokens}（完整）"
    return f"{tokens}（不完整：{len(cost['incomplete_cost_receipts'])} 条收据缺少用量）"


def render_effectiveness_table(report: dict[str, object]) -> str:
    """Render the main table so JSON and Markdown always show the same numbers."""

    arms = report["arms"]
    assert isinstance(arms, dict)
    guided = arms["guided"]
    independent = arms["independent"]
    assert isinstance(guided, dict) and isinstance(independent, dict)
    comparison = report["comparison"]
    assert isinstance(comparison, dict)

    lines = [
        f"# 攻击效果主表（{report['report_version']} · 分类 {report['classification_version']}）",
        "",
        f"| 项目 | coverage_guided ({guided.get('campaign_id', '-')}) "
        f"| random_independent ({independent.get('campaign_id', '-')}) |",
        "|---|---:|---:|",
    ]
    rows = (
        ("successes", None),
        ("decisive", None),
        ("success_rate_percent", None),
        ("distinct_success_targets", None),
        ("target_decisive_k", None),
        ("target_reached", None),
        ("not_reached_reason", None),
        ("scheduling_attempts", "scheduling_limit"),
        ("execution_attempts", "execution_attempt_limit"),
        ("preparation_rejections", None),
        ("undetermined", None),
        ("infra_error_generations", None),
        ("exclusion_rate_percent", None),
    )
    for key, limit_key in rows:
        label = MAIN_ROW_LABELS[key]
        left = guided.get(key)
        right = independent.get(key)
        if limit_key is not None:
            left = (
                f"{left}/{guided.get(limit_key)}"
                if guided.get(limit_key) is not None
                else "未冻结"
            )
            right = (
                f"{right}/{independent.get(limit_key)}"
                if independent.get(limit_key) is not None
                else "未冻结"
            )
        else:
            left = _cell(left)
            right = _cell(right)
        lines.append(f"| {label} | {left} | {right} |")
    lines.append(
        f"| 成本：Agent tokens（可取得部分） "
        f"| {_cost_cell(guided)} | {_cost_cell(independent)} |"
    )
    lines.append(f"| 计分来源 | {guided.get('scoring')} | {independent.get('scoring')} |")
    lines.append("")
    differences = comparison["differences"]
    assert isinstance(differences, dict)
    lines.append(
        "- 差值（guided − independent）："
        f"S {differences['successes']:+d}，N {differences['decisive']:+d}，"
        f"D {differences['distinct_success_targets']:+d}，"
        f"成功率 {differences['success_rate_percent_points'] or '未报告'}"
    )
    if differences["success_rate_withheld_reason"] is not None:
        lines.append(f"  - 成功率差值未报告：{differences['success_rate_withheld_reason']}")
    claims = comparison["claims"]
    assert isinstance(claims, dict)
    lines.append(f"- 优越性结论：{claims['superiority']}（{claims['reason']}）")
    lines.append(f"- 采样口径：{report['scope']}")
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "CLAIM_NOT_ESTABLISHED",
    "EFFECTIVENESS_REPORT_VERSION",
    "MAIN_ROW_LABELS",
    "REPORT_CAVEATS",
    "SAMPLING_SCOPE",
    "build_arm_effectiveness",
    "build_effectiveness_comparison",
    "build_effectiveness_report",
    "render_effectiveness_table",
]
