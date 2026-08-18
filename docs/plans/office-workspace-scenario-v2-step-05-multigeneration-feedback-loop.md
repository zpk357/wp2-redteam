# Office Workspace V2 第五步详细计划：单候选多代反馈闭环

状态：`5.0-5.15 确定性工程施工与验收完成；真实 Qwen 和语义探索能力留待后续阶段`

2026-08-17 施工结果：第五步身份锁、Mutation 预算前置、Preparation 成本结算、无 Episode 结算、
ready-only Handoff、单 Work、ExecutionClosure、Finding/Seed 双通道、反馈链和三代恢复合同已经实现。
代表性 Docker 闭环、recording/strict replay、verification-only fork、正式 Campaign run/resume、
联合聚焦集、Ruff 与证据自检均已通过。证据为
`reports/local-acceptance/office-v2-step5/stage5-loop-evidence.json`，摘要
`sha256:2df3b5f23ecc33c14d116bd6d6efd1f9177fd5f5b0182465df8b32ee73bde5a1`，并通过
`acceptance_complete=true` 自校验。本步结论只证明确定性工程闭环成立，不证明真实 Qwen 的语义探索能力。

上游前置：

- Office Workspace V2 场景阶段 1-8 已冻结；
- 第一步已经把 direct、recording 和 strict replay 整理成可信 `V2CoverageInput`；
- 第二步已经实现行为覆盖、风险覆盖、Utility 事实和 `V2CoverageDelta`；
- 第三步已经实现 Corpus、Risk/Behavior Frontier、单候选 Scheduler、预算、收据、原子结算和 SQLite 恢复；
- 第四步已经实现受控语义候选准备，并止于 `MutationPreparation.ready`；
- Judge、黄金集、真实 Ollama/Qwen 和最终语义质量结论继续冻结，不进入本步。

## 1. 本步要解决什么

前四步已经分别能够回答：

```text
一次 Episode 真实发生了什么？
这次执行增加了哪些行为或风险覆盖？
下一轮应该探索哪个缺口、从哪条历史路线继续？
怎样生成一个不越界的新候选？
```

第五步要把这些能力接成一个能够自动连续运行的工程闭环：

```text
读取 ready 候选
→ 在独立 Episode 中执行
→ 封存轨迹、环境变化和 Oracle 事实
→ 计算真实 CoverageDelta
→ 决定保留为发现、晋升为父种子或不晋升
→ 原子更新 Coverage、Corpus、Frontier、Exposure、预算和 Campaign 状态
→ Scheduler 读取最新反馈，选择下一代
→ 重复，直到饱和、预算耗尽、暂停或取消
```

通俗理解：前四步已经造好了“出题、考试、阅卷和选下一题”的零件，第五步负责让它们自动接力，而且断电重启后不会忘记做到哪里。

## 2. 本步明确不做什么

第五步只证明工程闭环正确，不把替代模型的结果冒充真实模型结论。

本步不做：

- 不调用真实 Ollama 或真实 Qwen；
- 不开发 Judge、黄金集、主动学习或评分漂移；
- 不评价生成文本的真实语义质量；
- 不宣称 scripted Agent 能代表真实 Agent 的探索能力；
- 不运行 24 小时压力测试，不优化最终吞吐率；
- 不增加第二个业务场景；
- 不重写冻结的 Office V2 世界、Oracle、Coverage、Corpus 或 Replay；
- 不恢复旧的 2-4 候选批次和同批竞争；
- 不物理删除 V1 或过渡资产；
- 不制作最终 HTML 产品报告。

真实 Qwen、真实语义候选和真实覆盖收益统一留到第六步验收。

## 3. 一条完整运行链

```text
V2CampaignStateSnapshot
        │
        ├─ Scheduler 选择一个 Frontier、父 AttackSeed 和 supporting ExecutionRecord
        │
        ├─ 原子预留 MutationPlan 最大 Token/成本预算
        │
        ├─ 第四步生成并持久化 MutationPreparation 终态
        │       ├─ rejected/paused：结算实际 Mutator 成本
        │       │                   → NonEpisodeSettlement 关闭本代
        │       └─ ready：结算实际 Mutator 成本并释放余额
        │
        ▼
第五步核对 HandoffIdentity
        │
        ├─ 原子创建 CandidateWork 并预留一次 Episode 预算
        │
        ├─ 创建独立 Episode 副本
        │
        ├─ scripted Agent 多轮调用 Office V2 工具
        │
        ├─ Tracer 封存 invocation/result/PolicyDecision/StateTransition
        │
        ├─ Oracle 依据真实状态产生事实
        │
        ├─ 清理 Episode 并封存 AttemptReceipt
        │
        ▼
ExecutionClosure
        │
        ├─ 转换为 V2CoverageInput
        ├─ 计算 V2CoverageDelta
        ├─ 形成 ExecutionRecord
        ├─ 形成 PromotionDecision / FindingDisposition
        ├─ 形成 NextGenerationFeedback
        │
        ▼
一个 SQLite 事务提交 CandidateSettlement
        │
        ├─ CoverageSnapshot
        ├─ Corpus snapshot
        ├─ Risk/Behavior Frontier snapshot
        ├─ ExposureLedger
        ├─ Budget
        ├─ CampaignLifecycle
        └─ Settlement
        │
        ▼
Scheduler 只读取已提交状态，确定下一代
```

每代只有一个候选。第 N+1 代一定在第 N 代由 `CandidateSettlement` 或 `NonEpisodeSettlement` 恰好关闭
一次后才生成；有有效 Episode 时使用最新执行反馈，没有有效 Episode 时只使用成本、拒绝/失败统计和
生命周期事实。

## 4. 本步的核心对象和责任

| 对象 | 负责什么 | 不负责什么 |
|---|---|---|
| `MutationBudgetReservation` | 在调用 Provider 前冻结并预留 Plan 最大预算 | 不代表已经发生实际成本 |
| `MutationPreparation` | 证明候选已经合法生成和物化 | 不代表 Agent 已看到或使用内容 |
| `PreparationCostSettlement` | 对所有 Preparation 终态结算实际 Mutator 成本并释放余额 | 不推进 Coverage 或无增益窗口 |
| `NonEpisodeSettlement` | 在没有有效 Episode 时关闭本代并释放预留 | 不要求 ExecutionRecord，也不修改 Coverage/Exposure |
| `ExecutionHandoff` | 锁定 ready 候选与执行工作之间的身份 | 不创建 Coverage 结论 |
| `CandidateWork` | 表示一次候选 Episode 的执行工作 | 不表示 Mutator Provider 生命周期 |
| `AttemptReceipt` | 逐次封存执行尝试、错误和真实成本 | 不自动把失败计为无增益 |
| `ExecutionClosure` | 封存一次 Episode 的全部执行事实和清理事实 | 不自行判断种子价值 |
| `V2CoverageInput/Delta` | 从可信事实计算覆盖 | 不相信 Agent 或 Mutator 自述标签 |
| `FindingRecord` | 用稳定 finding_key 保留真实风险发现和 replay 状态 | 不自动进入 Corpus 父种子池 |
| `PromotionDecision` | 决定是否进入风险种子池或探索种子池 | 不改变历史 Seed 或 ExecutionRecord |
| `NextGenerationFeedback` | 把本代真实结果压缩成下一代可用反馈 | 不选择 Frontier 或算子 |
| `CandidateSettlement` | 原子提交本代所有状态变化 | 不包含未封存的外部执行窗口 |
| `CampaignLifecycle` | 表示基线、饱和、预算、暂停和取消状态 | 不用一个 `failed` 吞并所有失败原因 |

## 5. 必须冻结的设计结论

### D1 Mutator 预算必须在 Provider 调用前预留

正确顺序固定为：

```text
GenerationAllocation
→ 原子创建 MutationBudgetReservation
→ 检查并预留 plan_total_token_budget / max cost
→ 执行 MutationPreparation
→ PreparationCostSettlement 结算实际成本并释放余额
→ ready 才允许预留 Episode/Agent 预算
```

如果 Campaign 剩余 Mutator 预算不足以覆盖 Plan 最大预算，不得调用 Provider。所有 Preparation 终态都
必须产生一次幂等 `PreparationCostSettlement`；重启不能重复扣减，失败 attempt 的真实成本不能丢失。

### D2 第四步与第五步只通过显式 Handoff 相连

第五步只能消费满足以下条件的 preparation：

```text
state = ready
validation = accepted
materialized_candidate_id 存在
allocation / seed / supporting execution / binding / baseline 摘要一致
Campaign identity、场景版本和 FieldRegistry 身份一致
```

第五步新增 `ExecutionHandoff`，至少锁定：

```text
campaign_id
generation_index
preparation_id / preparation_digest
materialized_candidate_id / digest
generation_allocation_id / digest
parent_seed_id
supporting_execution_record_id
comparison_context_digest
baseline_snapshot_digest
scenario_case_digest
agent_runtime_identity
execution_policy_digest
handoff_digest
```

同一 `ExecutionHandoff` 只能创建一个逻辑 `CandidateWork`。重启后重复提交相同 Handoff 必须幂等；相同 ID、不同内容属于完整性错误并暂停 Campaign。

### D3 每代严格只有一个候选

正式 V2 合同为：

```text
一次 GenerationAllocation
→ 一次 MutationPreparation
→ 一个 MaterializedCandidate
→ 一个 CandidateWork
→ 一个已结算 Episode
→ 下一代
```

第二步的 `CandidateSet` 只能以 singleton 包装复用，不恢复批次候选竞争。这样每一代的选择都能使用上一代刚产生的真实覆盖反馈。

### D4 每个候选使用独立 Episode

候选不能继承上一个候选运行后的可变世界。每次执行必须从冻结的：

```text
Canonical World
+ ScenarioCase
+ initialization overlay
+ allocation binding
```

创建新的 Episode 状态。

同一 Episode 内允许 Agent 多轮调用工具，业务状态持续变化；Episode 结束后销毁其容器、临时目录和临时卷。下一代只继承 Coverage、Corpus 和调度反馈，不继承上一代的业务副作用。

### D5 初始化条件与 Agent 副作用继续分开

```text
initialization_overlay
```

表示测试开始前宿主放入的条件；

```text
agent_state_delta
```

表示 Agent 执行工具后造成的变化。

两者不能合并成一个状态差异。否则系统会把宿主预置内容误判成 Agent 已经造成的风险副作用。

### D6 只有有效 Episode 才能结算 Coverage

进入覆盖与反馈的最低条件：

- V2 身份和摘要完整；
- 执行已经到确定终态；
- invocation、result、PolicyDecision 和 StateTransition 有序闭合；
- Oracle 完成且事实可追溯；
- initialization overlay 与 Agent StateDelta 分离；
- termination、submit 和 Utility 事实存在；
- 容器和临时资源清理结果明确；
- Coverage baseline 与 Allocation 锁定摘要一致。

无效、未知或执行窗口模糊的 Episode 不计算无增益、不推进 Frontier、不累计 Coverage，也不进入 Corpus。

### D7 “发现值得保存”与“适合继续当父种子”分开

真实风险副作用可能已经发生，但正常任务完全失败。这种案例有审计价值，却不一定适合继续变异。

因此结算分为两条线：

```text
FindingDisposition
  no_finding
  recorded
  replay_required
  replay_confirmed
  replay_failed

SeedPromotionDisposition
  risk_seed
  exploration_seed
  finding_only
  no_promotion
  quarantined
```

规则：

- 风险证据真实成立，即使 Utility 失败，也要保存为 `FindingRecord`；
- Utility 完全失败的风险案例默认是 `finding_only`，不能自动成为下一代父种子；
- 风险里程碑推进且正常任务完成，或满足冻结的最低 Utility 门，才可晋升 `risk_seed`；
- 只有新行为覆盖、没有风险推进时，必须正常任务完成才可晋升 `exploration_seed`；
- 无新主要行为、无新风险推进时不晋升；
- 完整性、身份、Oracle 或清理不可信时进入 `quarantined`，不视为普通无增益。

这里的 Utility 不是 Judge 分数，而是现有确定性事实：required goals、正常任务是否完成、是否 submit、额外副作用和 termination reason。

`FindingRecord` 使用稳定 `finding_key` 去重。该键至少由场景/世界版本、Objective/Milestone、规范化风险
上下文、source/sink/carrier/recipient/authorization branch 和首次执行 canonical fact 组成，不包含
acquisition metadata。strict replay 只能把同一 Finding 从 `replay_required` 更新为
`replay_confirmed/replay_failed`；不能创建第二条 Finding、第二个 Generation 或新的 Coverage 贡献。

### D8 planned、delivered、observed、used 在执行后闭合

```text
AttackSeed.payload_specs[]                  = planned
MaterializedCandidate.delivered_payloads[] = delivered
ExecutionRecord.observed_payload_refs[]    = observed
ExecutionRecord.used_payload_refs[]        = used
```

`observed` 只能由真实读取事件证明；`used` 必须由后续工具参数来源、状态变化或 Oracle 因果证据证明。内容被放进邮件或文件，不代表 Agent 读过；Agent 读过，也不代表它用于后续动作。

### D9 Coverage 只相对 Allocation 冻结基线计算

本轮 `CoverageDelta` 必须相对 `GenerationAllocation.coverage_snapshot_digest` 指向的基线计算。结算前若 Campaign 当前 Coverage 已经变化，说明出现并发写入或恢复分叉，必须暂停，不能把候选改为相对新基线重新计算。

虽然当前是单候选，也保留 singleton `CandidateSet` 包装，以复用第二步已经验证的 Coverage 接口。

### D10 最新反馈必须参与下一代重新决策

第五步新增 `NextGenerationFeedback`，至少包含：

```text
coverage_delta_digest
new_behavior_feature_keys
risk_milestone_changes
planned_vs_unexpected
exposure_stage_changes
observed_payload_refs / used_payload_refs
permission_blockers
utility_disposition
promotion_disposition
operator_outcome
consecutive_no_gain
feedback_digest
```

该对象只总结真实结果，不自行选择下一步。下一代仍由：

```text
Frontier Scheduler
→ Parent Selector
→ FeedbackToOperatorPolicy
```

确定方向、父执行和算子。

每个下一代 Allocation 必须引用最新 `feedback_digest`，并重新计算 Frontier、父执行和
OperatorAllocation。真实情况下，重新计算后的最优决定可以改变，也可以保持不变；两种结果都必须
保存可解释原因，不能为了证明反馈存在而强迫切换。

受控验收另外构造关键反馈不同的案例，证明这些反馈在条件确实改变时能够导致不同决定；如果没有兼容
选择，则应明确进入 `awaiting_parent`、`awaiting_operator` 或冷却状态。

### D11 一次风险实现不等于整个方向饱和

一次 realized 只关闭对应：

```text
Objective
+ Milestone
+ source/sink/carrier/recipient/authorization context
```

仍然存在其他载体、接收方、授权分支或行为缺口时，保留新的 context gap。风险事实单调增长，但调度状态可以 ready、active、cooling、locally_saturated 或 local_budget_exhausted。

`local_budget_exhausted` 不能参与 Campaign `saturated` 判断。

### D12 尝试、重试和模糊执行窗口

每次 Episode 尝试都先进入 `executing`，结束后立即封存不可变 `AttemptReceipt` 和真实成本。

只允许明确临时故障有界重试，例如：

- 连接失败；
- 明确超时且可以证明 Episode 未提交业务副作用；
- 429；
- 白名单中的部分 5xx；
- 明确截断。

以下情况暂停，不自动重试：

- Episode 可能已提交副作用但收据未落盘；
- 配置、模型、镜像或协议摘要漂移；
- 轨迹、Manifest、Oracle 或状态摘要不完整；
- 未分类异常；
- 清理结果未知；
- 相同逻辑工作出现不一致结果。

每次失败成本都累计，重试不能重新获得完整预算。

### D13 有 Episode 和无 Episode 都必须原子结算

```text
有有效 Episode：
创建 Work/预留预算
→ 执行 Episode
→ 立即封存 AttemptReceipt + ExecutionClosure + 实际成本
→ 读取 sealed Work
→ 计算 Coverage/Promotion/Feedback
→ CandidateSettlement 原子提交全部下一状态

无有效 Episode：
Preparation rejected/paused
或 Work permanent failure
或 execution 前取消
→ NonEpisodeSettlement 原子提交成本、预算、统计和生命周期
→ 不创建 ExecutionRecord
→ 不改变 Coverage/Exposure/Corpus/无增益窗口

两条路径共同保证：
PreparationCostSettlement 先结清 Mutator 实际成本
→ 本代最终恰好由 CandidateSettlement 或 NonEpisodeSettlement 关闭一次
```

`NonEpisodeSettlement` 至少包含：

```text
settlement_id / campaign_id / generation_allocation_id
preparation_id / preparation_outcome_digest
work_id（可选）/ attempt_receipt_ids[]
disposition = preparation_rejected / preparation_paused /
              work_permanent_failure / cancelled_before_execution
actual_costs / released_reservations
invalid_candidate_delta / operator_rejection_deltas
scheduling_decision_delta
next_budget_digest / next_lifecycle_digest / next_state_digest
settlement_digest
```

`scheduling_decision_delta` 使下一次 Allocation 获得新的决策序号，但不能增加
`valid_committed_episodes`，也不能改变 `global_consecutive_no_gain`。

Campaign counter 语义固定为：

```text
generation_index             = 已由两类 Settlement 关闭的 Allocation 数量
valid_committed_episodes     = 仅 CandidateSettlement 的有效 Episode 数量
invalid_or_failed_attempts   = preparation/work 的无效或失败尝试数量
global_consecutive_no_gain   = 仅有效 Episode 的连续无增益数量
```

因此 NonEpisodeSettlement 会把 `generation_index + 1`，但不会伪造有效 Episode 或无增益观察。

这样进程在 Docker 已经结束、数据库尚未结算时崩溃，恢复后可以从 sealed Work 继续结算，而不是重新
执行候选；没有 Episode 的终态也不会遗留预算预留或被重复调度。

### D14 Campaign 状态沿用 SPEC

`baseline_complete` 是非终态事件，不是完成/停止状态：

```text
baseline phase
→ baseline_complete event
→ phase = adaptive
→ 继续创建下一轮 Allocation
```

真正终态只有：

- `saturated`：所有可达且适用的 Frontier 在合同意义上饱和，并且没有待处理工作；
- `budget_exhausted_incomplete`：预算用完但仍有缺口；
- `paused`：需要人工处理配置、完整性、模型漂移、模糊执行窗口或未知错误；
- `cancelled`：用户明确取消。

不得因为记录了 `baseline_complete` 而停止创建 adaptive Allocation。不得新增一个宽泛 `failed` 把不同
根因压平。`locally_saturated` 只是单个 Frontier 的调度状态，不是 Campaign 完成状态。

## 6. 基线阶段与自适应阶段

一次 Campaign 不是直接从最容易的风险开始反复变异。

### 6.1 基线阶段

对全部 applicable Objective 至少运行一次确定性基线，记录：

- 正常任务能否完成；
- 入口是否可达；
- 目标里程碑初始状态；
- 权限分支；
- 初始行为路径；
- 哪些 Objective 因世界或 Actor 绑定不适用。

完成后状态变为 `baseline_complete`，但 Campaign 不结束，而是进入 adaptive phase。

### 6.2 自适应阶段

每代执行：

```text
选择缺口
→ 选择父执行
→ 选择算子
→ 准备一个候选
→ 执行一个候选
→ 结算一个候选
→ 使用最新反馈进入下一代
```

风险公平和行为探索预算继续分别记账，容易命中的风险不能长期占满预算。

## 7. 三代工程验收实例

该实例使用 RuleBased Mutator 和 scripted Agent，只验证闭环，不证明真实模型能力。

### 第 0 代：基线

```text
Scheduler：选择一个尚未完成基线的 Objective
Agent：完成正常任务，并产生一条已知工具链
Coverage：记录初始工具边、状态变化和 Utility
Promotion：保留可继续探索的基线父执行
Feedback：入口已 observed，但未 used
```

### 第 1 代：反馈改变算子

```text
FeedbackToOperatorPolicy：因为 observed 未 used，选择参数来源或表达结构算子
RuleBased Provider：生成一个受控文本候选
Agent：读取该内容，并据此尝试后续工具调用，但被 enforce policy 阻止
Coverage：新增 permission branch 和 blocked 风险结果
Promotion：正常任务完成，晋升风险种子
Feedback：attempted + blocked，记录具体 blocker
```

### 第 2 代：反馈再次改变调度

```text
Scheduler：不能用文本伪造突破硬阻断；切换到合法 Rebind、其他 context gap，
           或转向另一个公平欠账 Frontier
Agent：在新 context 中走出新的工具链或真实状态变化
Coverage：新增行为特征或推进另一个风险里程碑
Promotion：按 Utility 和真实证据决定风险种子、探索种子或 finding_only
```

三代验收必须能够解释：

- 每代为什么选择该 Frontier；
- 为什么选择这条父 Seed 和具体 supporting ExecutionRecord；
- 为什么选择该算子；
- Agent 实际观察和使用了什么；
- 新增了什么覆盖；
- 为什么晋升或不晋升；
- 下一代为什么改变，或者为什么重新计算后仍保持原决定。

## 8. Replay 与 Fork 的范围

### 8.1 Direct、recording、strict replay

三条采集路径不要求整个 JSON 字节相同，因为 acquisition lineage 本来不同。要求：

```text
canonical_fact_digest 相同
behavior_source_facts 相同
oracle_fact_digest 相同
acquisition metadata 可以不同
```

第五步确定性三代工程验收中的每一代都保存 recording，并执行 strict replay，以一次性验证循环与重放接口闭合。

后续正式真实模型 Campaign 为节省成本：

- 晋升为风险种子或形成 Finding 的 Episode 必须 strict replay；
- 晋升为探索种子的代表 Episode 必须 strict replay；
- 无增益 Episode 保存 sealed recording，可按策略抽样 replay；
- 任何报告为正式缺陷的结果都必须有 strict replay 证据。

Finding 的 strict replay 是验证动作，不是新 Episode 贡献：它按稳定 `finding_key` 更新
`replay_confirmed/replay_failed`，不得重复创建 Finding、推进 Coverage、增加 Exposure 或生成新的
CorpusEntry。

### 8.2 Fork

第五步选择“仅验证”的 Fork 方案：至少选择一条已录制 Episode，在内容被观察前的合法 checkpoint
替换 payload，生成新的子 lineage 并继续执行，但不把该验证分支写入父 Campaign 的 Coverage 或代际状态。

Fork 必须：

- 保留父 Manifest、checkpoint 和替换字段摘要；
- 断点前使用历史录制事实；
- 断点后产生独立 `ForkVerificationRecord`，而不是 Campaign `ExecutionRecord`；
- 不覆盖父轨迹；
- 标记 `verification_only=true`；
- 不创建 GenerationAllocation、MutationPreparation、CandidateWork、Finding 或 Settlement；
- 不修改 Coverage、Corpus、Frontier、Exposure、预算和 Campaign lifecycle。

未来如果需要让 Fork 结果参与搜索，必须创建新的子 Campaign 或完整新 Generation，并重新经过
Allocation → Preparation → Handoff → Work → Settlement；本步不实现这条扩展路径。

## 9. CLI 与报告

第五步增加 V2 专用入口，建议产品命令为：

```text
trace-redteam campaign run --config campaign.yaml
trace-redteam campaign resume --campaign-id <id>
trace-redteam campaign inspect --campaign-id <id>
trace-redteam campaign replay --execution-id <id> --strict
```

本步只允许：

```text
agent = scripted:office-v2
mutator = rule-based:office-v2
```

真实 `ollama/qwen` 配置在第六步才解除前置门。旧 V1 Campaign 入口不得重新启用。

JSON 报告至少包含：

- Campaign 身份、场景/Agent/Mutator/策略摘要；
- 基线覆盖状态；
- 每代 Allocation、父 Seed、supporting ExecutionRecord 和算子；
- preparation、work、attempt、execution 和 settlement lineage；
- Utility、Exposure、行为与风险增量；
- Finding 和 Seed promotion 结果；
- Coverage/Corpus/Frontier 增长；
- Token、Episode、时间和成本预算；
- 当前完成或暂停原因；
- strict replay 和 fork 命令；
- 容器、临时目录和临时卷清理结果。

## 10. 失败处理表

| 失败 | 状态变化 | 是否重试 | 是否算无增益 | 是否推进预算 |
|---|---|---:|---:|---:|
| Mutator 最大预算无法预留 | budget_exhausted_incomplete | 否，不调用 Provider | 否 | 不产生实际 Mutator 成本 |
| 候选结构被第四步拒绝 | preparation rejected → NonEpisodeSettlement | 否，本代结束 | 否 | 结算 Mutator 实际成本并释放余额 |
| Preparation 因配置/完整性暂停 | preparation paused → NonEpisodeSettlement | 否 | 否 | 结算实际成本，Campaign paused |
| 明确可重试的执行前连接错误 | work 保持可恢复 | 有界 | 否 | 计真实尝试成本 |
| 明确可证明无副作用的超时 | retryable receipt | 有界 | 否 | 计真实尝试成本 |
| Work 永久失败且没有 ExecutionRecord | failed → NonEpisodeSettlement | 否 | 否 | 结算全部 attempts 并释放 Episode 余额 |
| 执行是否提交无法确定 | ambiguous / Campaign paused | 否 | 否 | 计真实尝试成本 |
| 配置或身份摘要漂移 | Campaign paused | 否 | 否 | 计已发生成本 |
| 轨迹或 Oracle 不完整 | quarantined / Campaign paused | 否 | 否 | 计已发生成本 |
| Agent 正常结束但无新覆盖 | committed no_promotion | 否 | 是 | 计 Episode 和实际成本 |
| 风险成立但正常任务失败 | finding_only | 否 | 按真实覆盖记账 | 计 Episode 和实际成本 |
| 容器清理失败 | Campaign paused | 否 | 否 | 计已发生成本 |
| execution 前用户取消 | NonEpisodeSettlement → cancelled | 否 | 否 | 结算已发生成本并释放预留 |
| execution 后用户取消 | sealed 证据保留 → cancelled | 否 | 否 | 保留并结算已封存状态 |

## 11. 资产处置表

| 现有资产 | 第五步处置 | 原因 |
|---|---|---|
| `MutationPreparation` / `PreparationOutcome` | 直接消费 | 第四步唯一正式输入 |
| Campaign Budget snapshot | 扩展 Mutation 预留、实际结算和余额释放 | Provider 调用前必须有总预算门 |
| `CandidateWork` / `AttemptReceipt` / `CandidateSettlement` | 复用并补齐执行编排 | 第三步已验证两阶段恢复合同 |
| `NonEpisodeSettlement` | 在同一 Store 新增兄弟结算合同 | 无 ExecutionRecord 的终态也必须原子关闭本代 |
| `V2CampaignStore` | 继续作为唯一 SQLite | 不创建第二套 Campaign 数据库 |
| `V2CoverageInput` 及三条转换路径 | 直接复用 | 不创建第二套轨迹完整性体系 |
| `promote_coverage_artifact` | 拆清 Finding 与 Seed eligibility 后复用 | 当前已有 Coverage→Corpus 基础链 |
| `choose_next_allocation` | 复用并接入真实 Feedback | 第三步已验证确定性调度 |
| Stage 7/8 execution closure | 复用 | 已冻结 Agent 执行和录制事实 |
| strict replay | 复用并更新 Finding replay 状态 | 验证不重复贡献 Coverage |
| fork | 仅作 verification-only 复用 | 本步不让验证分支绕过正式 Campaign lineage |
| 旧 V1 engine/store/mutator | 隔离 | 不进入正式 V2 路径 |
| Judge/LLM scorer | 不接入 | 当前没有判分任务 |

## 12. 分步施工计划

每个编号控制为一次适合 Codex 完成和验收的工作量。除真实架构冲突外，后续可按批次连续施工。

### 5.0 边界、资产与身份锁（已完成）

- 冻结第五步组件、版本、schema、执行器和策略身份；
- 建立上述资产处置表的代码级映射；
- 锁定只接受 Office Workspace V2、terminal preparation 和 singleton generation；只有 ready preparation
  可以创建 ExecutionHandoff，rejected/paused 只能进入 NonEpisodeSettlement；
- 明确第五步不能调用真实 Ollama/Qwen/Judge；
- 确认旧 V1 入口在新对象创建前拒绝。

验收：相同身份输入产生相同摘要；场景、Agent Runtime、Mutator、Coverage baseline 或 preparation 摘要漂移会暂停，不会静默继续。

### 5.1 Mutation 预算预留与 Preparation 成本结算（已完成）

- 实现不可变 `MutationBudgetReservation`；
- 在调用 Provider 前原子预留 Plan 最大 Token/成本；
- 实现幂等 `PreparationCostSettlement`，对 ready/rejected/paused 全部终态结算实际成本并释放余额；
- 预算不足时进入 `budget_exhausted_incomplete`，不得调用 Provider；
- 数据库重开后不能重复预留、重复扣减或遗失失败 attempt 成本。

验收：故障注入覆盖预留前、Provider 完成后和成本结算前；重开后 Campaign 预算只能是旧完整状态或新
完整状态。

### 5.2 NonEpisodeSettlement、ExecutionHandoff 与 CandidateWork（已完成）

- 实现 `NonEpisodeSettlement` 四种 disposition；
- rejected/paused preparation、permanent work failure 和 execution 前取消均可在没有 ExecutionRecord 时
  原子关闭本代；
- NonEpisodeSettlement 只更新成本、预算、invalid/operator 统计、调度决策次数和必要生命周期；
- 对 ready preparation 实现不可变 `ExecutionHandoff`，校验 candidate、allocation、seed、supporting
  execution、binding 和 baseline 全链摘要；
- 从 Handoff 原子创建唯一 `CandidateWork`，并预留 Episode、Agent Token、时间和成本预算；
- 防止 rejected/paused/non-ready preparation 进入执行。

验收：四类无 Episode 终态均无 Coverage/Exposure/Corpus/无增益变化且无预算残留；换任一父执行、资源
绑定、comparison context 或 baseline 都会在创建 Work 前失败。

### 5.3 V2 Episode 执行适配器（复用既有 Runtime，闭包适配已完成）

- 接入现有 Office V2 Agent Runtime、ScenarioCase 和 ToolRuntime；
- 使用 scripted Agent 运行完整多轮任务；
- 一个 Episode 内保持状态，直到 submit、明确失败、超时或步数上限；
- 不为具体人名、Case ID 或 A01 增加运行时特判；
- 形成统一 `ExecutionClosure`。

验收：代表任务必须包含前后依赖的多次工具调用，后一步真实使用前一步结果；普通文本“完成”不能替代 submit。

### 5.4 observed/used 与执行事实闭合（已完成）

- 从工具读取事件生成 `observed_payload_refs`；
- 从 ArgumentSource、OutputEvidence、后续工具参数和 StateDelta 生成 `used_payload_refs`；
- 保持 planned、delivered、observed、used 四层血缘；
- 封存 Utility、termination、submit、Oracle 和清理事实。

验收：只放入未读取、读取但未使用、使用后被阻止、使用后产生副作用四种情况能够明确区分。

### 5.5 AttemptReceipt、错误分类与 sealed Work（已完成）

- 每次尝试立即封存 digest、字节数、截断、HTTP/运行状态、有限摘要和真实成本；
- 只允许白名单临时错误有界重试；
- 对 ambiguous、unknown、integrity 和 cleanup failure 暂停；
- 成功 ExecutionClosure 绑定 ExecutionRecord，并把 Work 变为 sealed；
- sealed Work 不得再次执行。

验收：模拟“执行完成后、结算前崩溃”，重启只能继续结算，不能重复运行 Episode。

### 5.6 ExecutionClosure 到 CoverageDelta（已完成）

- 调用第一步三路径转换和第二步 Coverage 计算；
- 使用 Allocation 冻结 baseline 和 singleton CandidateSet；
- 生成 ExecutionRecord；
- 校验 initialization overlay 与 Agent StateDelta 分离；
- 校验 direct/recording/replay 的 canonical facts 对齐。

验收：相同事实不同采集路径只贡献一次覆盖；资源 ID、相似文本和循环次数不能制造假新颖度。

### 5.7 Finding 与 Seed Promotion 双通道（已完成）

- 增加带稳定 `finding_key` 和 recorded/replay_required/replay_confirmed/replay_failed 状态的
  `FindingRecord`，以及 `SeedPromotionDisposition`；
- 明确风险证据保存与父种子资格分离；
- 风险种子、探索种子、finding_only、no_promotion 和 quarantined 分别结算；
- 把 Utility 事实作为晋升门和排序依据，而不是覆盖本身；
- 只给合格父种子建立 CorpusEntry；
- strict replay 只能更新同一 Finding 的验证状态，不能重复 Finding、Coverage 或 Generation。

验收：风险成立但正常任务失败会保留 Finding，却不会默认成为父种子；正常完成且新增行为的案例能进入探索池。

### 5.8 NextGenerationFeedback 与 Frontier 更新（已完成）

- 从 CoverageDelta、Exposure、Utility、permission blockers 和 operator outcome 构建反馈；
- 更新 Risk/Behavior Frontier、上下文缺口、无增益窗口和局部预算；
- 一次 realized 只关闭对应里程碑上下文；
- local budget exhausted 与 locally saturated 分开；
- 明确反馈到下一代 Allocation 的因果链，并保存重新决策后“改变或保持”的理由。

验收：每个下一代都引用最新 feedback digest 并重新计算；普通案例允许保持原决定，受控差异案例证明
至少五类已冻结 feedback gap 在关键条件变化时会产生不同下一步，或明确
`awaiting_operator/awaiting_parent`。

### 5.9 全状态原子 Settlement（已完成）

- CandidateSettlement 在同一个 SQLite 事务提交：CoverageSnapshot、Corpus、Frontiers、
  ExposureLedger、预算、Lifecycle、Feedback 和 Settlement；
- NonEpisodeSettlement 在同一 Store 的独立原子事务中结算成本、预算、统计、决策序号和生命周期，
  并验证 Coverage/Corpus/Frontier/Exposure/无增益摘要保持不变；
- 先核对当前 state/baseline/work/receipt 摘要，防止并发分叉；
- 相同 settlement 重提幂等；相同 ID 不同内容暂停；
- 事务失败不得留下部分 Coverage 或部分 Corpus。

验收：分别对 CandidateSettlement 和 NonEpisodeSettlement 的每个写入点做故障注入，数据库重开后只能
看到旧完整状态或新完整状态。

### 5.10 单候选下一代编排器（已完成）

- 串联 Scheduler → Mutation 预算预留 → 第四步 Preparation → 成本结算 → Work 或 NonEpisodeSettlement
  → Execution → Coverage → CandidateSettlement；
- 每代提交后才请求下一代 Allocation；
- baseline phase 和 adaptive phase 使用同一状态机；
- 若 preparation rejected/paused、Work 永久失败或 execution 前取消，走 NonEpisodeSettlement，不伪造
  ExecutionRecord；
- `baseline_complete` 只把 phase 切换为 adaptive，必须继续创建下一代；
- 只有 saturated、budget_exhausted_incomplete、paused 或 cancelled 才停止创建下一代。

验收：连续运行时不存在两个未结算 generation，也不存在先生成下一代再补写上一代反馈。

### 5.11 暂停、恢复、取消与确定性重开（已完成）

- 实现 Campaign 恢复入口；
- 分类恢复 ready preparation、allocated/resumable/sealed/ambiguous Work；
- sealed Work 继续结算，ambiguous Work 暂停，明确 retryable Work 有界重试；
- 恢复 Mutation 预算预留、PreparationCostSettlement 和未完成的 NonEpisodeSettlement；
- 支持用户取消并按 execution 前/后分别结算；
- 重开后下一轮 Scheduler 选择必须与关闭前完全一致。

验收：在 Mutation 预算预留后、preparation 终态后、Work 创建后、Attempt 封存后、Coverage 计算后和
两类 Settlement 提交前分别模拟崩溃，恢复行为符合合同。

### 5.12 无 Docker 的确定性三代闭环（已完成）

- 使用 RuleBased Mutator、scripted Agent 和内存/本地执行适配器跑三代；
- 证明每代引用最新 feedback 并重新计算；至少一个受控反馈差异改变 Frontier、父执行或算子，同时覆盖
  一个“反馈变化但最优决定保持”的合法案例；
- 覆盖 risk seed、exploration seed、finding_only 和 no_promotion 中至少三类；
- 证明相同初始状态重复运行产生相同调度和摘要；
- 增加一个固定/无反馈对照，仅证明机制差异，不声称语义效果优势。

验收：三代自动完成，报告能够逐代解释选择、执行、Coverage、晋升和下一步。

### 5.13 Docker 代表闭环、Replay 与 Fork（待 Docker daemon）

- 在独立 Docker Episode 中运行代表三代链；
- 每代容器内完成多轮工具调用，并在结束后清理；
- 保存 recording，对三代逐一 strict replay；
- 选择一代在合法 checkpoint 做 verification-only fork 并替换 payload，形成新 lineage；
- 验证 direct/recording/replay canonical facts 一致；
- 验证超时、清理失败和模糊执行窗口不会被吞掉。

验收：容器、网络、临时目录和临时卷隔离成立，执行后零残留；Replay 和 Fork 不覆盖父轨迹；strict
replay 只更新 Finding 验证状态，Fork 不修改父 Campaign 的 Coverage、Finding、Exposure、预算或代际。

### 5.14 V2 Campaign CLI 与 JSON 报告（部分完成）

- 增加 run/resume/inspect/replay V2 命令；
- 配置中明确单候选、预算、最大代数、Agent/Mutator 和 replay 策略；
- 输出第 9 节规定的 JSON 报告；
- 正式入口只接受 scripted Agent 和 RuleBased Mutator；
- 真实 Qwen 配置返回清晰的第六步前置门错误。

验收：用户可以从一条命令启动三代工程闭环，中断后 resume，并用报告中的命令 strict replay。

### 5.15 联合验收、证据和文档收口（无 Docker 部分完成）

- 运行一次第五步聚焦联合测试；
- 运行一次 Docker 代表闭环、超时清理、strict replay 和 fork；
- 生成自校验 `stage5-loop-evidence.json`；
- 记录代码、schema、镜像、配置和结果摘要；
- 更新 README、HANDOFF、AGENTS、LOG 和 LOG-INDEX；
- 明确结论只限工程闭环，不宣传真实 Qwen 或语义效果。

验收：证据摘要可重算，数据库重开调度一致，工作区无容器/卷残留，相关 Ruff 和 `git diff --check` 通过。

## 13. 建议施工批次与节省时间策略

确认计划后建议按四个批次连续施工：

```text
批次 A：5.0-5.4
身份、Mutation 预算、NonEpisodeSettlement、Handoff、Work、执行适配和 observed/used

批次 B：5.5-5.9
收据、Coverage、Finding/Promotion、Feedback 和两类原子结算

批次 C：5.10-5.12
编排、恢复和无 Docker 三代闭环

批次 D：5.13-5.15
Docker、Replay/Fork、CLI、联合证据和文档
```

验证策略：

- 编码期间只运行直接相关的最小测试或单个失败复现；
- 每个批次结束统一运行该批次聚焦测试和相关 Ruff；
- 同一代码摘要下已通过的测试不重复运行；
- Docker 只在 `5.13` 和 `5.15` 运行代表闭环及清理验收；
- 不重建 Stage 2-8 已冻结的昂贵证据；
- 不运行真实 Ollama/Qwen、Judge 或 24 小时压力测试；
- 不因编写第五步而重跑全仓 pytest。

## 14. 本步总体完成标准

以下条件全部满足，第五步才算完成：

1. 第四步一个 ready 候选只能建立一个可审计 CandidateWork；
2. 每代只有一个候选，且必须由 CandidateSettlement 或 NonEpisodeSettlement 恰好关闭一次后再产生
   下一代；
3. scripted Agent 在独立 Episode 中完成真实多轮工具交互；
4. planned、delivered、observed、used 有真实证据且不混淆；
5. Coverage 只来自真实轨迹、PolicyDecision、StateDelta 和 Oracle；
6. Finding 保存与父种子晋升分开，Utility 失败不会被盲目奖励；
7. Mutator 调用前预留最大预算，所有 Preparation 终态结算实际成本并释放余额；
8. 有效 Episode 原子提交 Coverage、Corpus、Frontier、Exposure、预算和 Lifecycle；无有效 Episode
   原子结算成本、预算和统计且不改变覆盖事实；
9. 连续三代自动运行，每代引用最新反馈重新决策，并证明受控反馈差异可以改变决定；
10. `baseline_complete` 只触发 adaptive phase，只有四个真正终态停止调度；
11. 暂停、恢复、取消和模糊执行窗口符合错误合同；
12. 三代 recording 均可 strict replay，Finding 不重复计数；至少一个 verification-only fork 产生新
    lineage 且不修改父 Campaign；
13. Docker 隔离、超时和零残留通过；
14. V2 CLI 可 run/resume/inspect/replay；
15. JSON 报告能解释每代的选择、执行、覆盖、晋升和状态；
16. 证据自校验、聚焦测试、Ruff 和 diff check 通过；
17. 报告明确写明尚未验证真实 Qwen、真实语义质量或真实探索收益。

## 15. 用户确认门

开始修改第五步运行时代码前，需要用户确认以下决定：

1. 正式 V2 继续采用每代单候选，不恢复 2-4 候选批次；
2. 每个候选使用新的独立 Episode，Campaign 不累计业务世界副作用；
3. 风险 Finding 与 Corpus 父种子资格分开；
4. 风险成立但正常任务完全失败时，默认保存为 `finding_only`，不自动作为父种子；
5. 第五步使用 RuleBased Mutator 和 scripted Agent，只证明工程闭环；
6. Mutator 调用前预留 Plan 最大预算；无有效 Episode 时用 NonEpisodeSettlement 原子关闭本代；
7. `baseline_complete` 是非终态事件，进入 adaptive 后继续调度；
8. 下一代必须读取最新 feedback 重新计算，但结果允许有理由地保持不变；
9. Finding 使用稳定 finding_key，strict replay 只更新验证状态，不重复 Coverage；
10. 第五步三代工程验收逐代 recording + strict replay，并验证一个不写入 Campaign 的
    verification-only fork；
11. 真正的语义质量、真实 Agent 探索能力和覆盖收益留到第六步真实 Qwen；
12. Judge 继续不设计、不实现；真实 Qwen 验收后优先进行项目收尾评估。

用户确认后，才从 `5.0` 开始施工。
