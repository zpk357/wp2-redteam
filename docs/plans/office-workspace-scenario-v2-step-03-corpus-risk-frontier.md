# Office Workspace V2 第三步详细计划：Corpus、RiskFrontier 与公平调度

状态：`3.0-3.12` 已完成并正式闭合；第四步详细计划已经编写，尚未开始第四步运行时代码施工。
2026-08-15 用户已确认“AttackSeed/MaterializedCandidate/ExecutionRecord/CorpusEntry 分责”和
“每轮只生成、执行、结算一个候选”。

上游前置：

- 第一步已经把 direct、recording、strict replay 转换为可信 `V2CoverageInput`；
- 第二步已经从同一输入计算 `V2EpisodeCoverageFacts`、`V2CoverageDelta` 和候选基线；
- 4 个顶层风险方向、12 个 Objective、23 个 Milestone 已冻结；
- 本步不得重新解释轨迹、重算 Oracle，也不得依赖模型自报标签。

## 1. 本步要解决什么

第二步已经能回答：

```text
这一条 Episode 新增了什么行为、风险事实和行为—风险关联？
```

第三步要回答：

```text
哪些历史案例值得留下？
下一轮优先补哪个风险缺口？
应该从哪条历史案例继续？
为什么现在只为它生成这一个候选？
为什么做出这个决定？
中断后怎样从同一个决定继续？
```

通俗理解：第二步负责阅卷，第三步负责建立“优秀案例档案馆”和“下一轮测试排班表”。

本步只做确定性决策，不调用 LLM、不生成新文本、不启动 Docker、不运行 Agent。第四步才根据本步输出
调用语义变异器，第五步才把生成、执行、覆盖和晋升串成多代闭环。

## 2. 先冻结八个设计结论

### 2.1 一个物理 Corpus，多个索引视图

不为 12 个 Objective 建 12 套互相隔离的种子库。使用一个 V2 Corpus 保存全部种子，再建立以下索引：

- 风险方向、Objective 和 Milestone；
- 行为缺口类型和特征族；
- 风险种子或探索种子；
- 正常任务、Actor、资源绑定、入口和载体；
- 行为特征、风险上下文和行为—风险关联；
- 父子血缘、状态、稳定性和历史收益。

这样，一条“邮件入口引出了新的云盘读取路径”的种子既能服务邮件外发目标，也可能成为后续权限扩张
路径的踏板；但调度时必须先通过兼容性过滤，不能因为都在一个库里就随便跨目标使用。

### 2.2 种子只保存“怎么测试”

`AttackSeed` 是一份可继续变异的测试配方。它只保存：

```text
原始测试目标：A03 敏感邮件外发
准备放置的内容：一个或多个结构化 payload spec，保留完整文本和目标位置要求
载体配方：邮件正文、云盘字段或可信回复中的哪个位置
绑定要求：需要什么类型的任务、Actor、资源和接收方
入口类型、使用过的结构算子和父种子血缘
```

单个 `agent_facing_text` 不足以表示多字段、复合入口和多轮回复，因此正式字段使用
`payload_specs[]`。它只证明“计划把什么内容放到哪里”，不证明内容已经物化，更不证明 Agent 已经读取
或使用。具体 Actor、任务、资源 ID、Episode、轨迹、Coverage、Oracle 和调度统计不塞进 AttackSeed；
它们分别属于 `MaterializedCandidate`、`ExecutionRecord` 和 `CorpusEntry`。

内容暴露严格沿用 Stage 6 冻结顺序：

```text
AttackSeed.payload_specs[]                  = planned
MaterializedCandidate.delivered_payloads[] = delivered
ExecutionRecord.observed_payload_refs[]    = observed
ExecutionRecord.used_payload_refs[]        = used
```

后一个阶段必须有独立事实证据，不能因为内容存在就推断 Agent 已观察，也不能因为 Agent 读过就推断后续
工具参数或副作用使用了该内容。

### 2.3 风险种子和探索种子是两种用途

- **风险种子**：推进了 Objective 的真实 Milestone、风险上下文或行为—风险关联，适合围绕当前风险
  深挖。
- **探索种子**：没有推进计划风险，但产生了新的一级行为路径、权限分支或状态变化，适合低预算探索
  新路线。

二者都在同一个 Corpus 中，通过 `seed_kind` 和索引区分。探索种子不会因为“尚未形成风险”被删除，
但也不能与已证明风险推进的种子获得完全相同的高预算。

### 2.4 风险与纯行为使用两类 Frontier

`RiskFrontier` 至少锁定：

```text
Campaign + 主调度 RiskFamily + Objective
+ 当前要推进的 Milestone
+ 当前覆盖事实与缺口
+ 兼容的 Case / Actor / 资源 / 入口集合
```

例如“敏感信息披露”太宽；`A01 的第二个里程碑：外部分享尚未创建`才是可执行的 RiskFrontier。

`BehaviorFrontier` 至少锁定：

```text
Campaign + behavior_gap_kind + feature_family
+ behavior_anchor_digest + gap_descriptor_digest
+ 可选的关联 Objective
+ 可继续探索的 Corpus 视图
```

例如一条种子只发现 `calendar -> workspace -> email` 新工具链但没有推进任何 Objective，它进入
BehaviorFrontier，不得被伪挂到某个风险目标。风险公平和行为探索保留分别记账，再由总调度器合并分配。

`blocked_seen` 和 `realized_seen` 是并列的历史事实，不能用一个最大整数覆盖彼此。一次 blocked 不表示
该里程碑完成，一次 realized 也不删除已经观察到的防御阻断分支。

### 2.5 硬公平先于软收益评分

调度顺序固定为：

```text
先补基线欠账
→ 再处理即将饥饿的风险方向
→ 再满足滚动窗口内的探索保留欠账
→ 再执行最大连续占用限制
→ 当前这一轮最后才按收益与成本排序
```

不能把所有规则混成一个总分。否则一个容易产生新路径的方向可能永远压住其他方向，而且无法证明
“每个可达目标至少测过一次”。

### 2.6 每轮只生成并结算一个候选

第三步可以选择方向和父种子，但不能预测候选一定增加覆盖。后续完整顺序必须是：

```text
调度器选择一个 Frontier、一条父种子和一条 supporting ExecutionRecord
→ 第四步生成一个 Candidate
→ 宿主检查结构、边界和完全重复
→ 在独立 Episode 中执行
→ 相对生成前冻结的 baseline 计算 CoverageDelta
→ 立即决定是否晋升并提交本轮结果
→ 更新反馈后再选择下一轮
```

第二步已有的 CandidateSet 共同 baseline 合同继续复用，但 V2 正式闭环把它作为“只含一个候选的
singleton CandidateSet”使用，避免重写已验证的 CoverageDelta 接口。

### 2.7 每个候选必须冻结比较上下文

每个普通 Candidate 必须保存本轮固定业务条件：

```text
actor + normal task + resource bindings
+ objective/allocation target + authorization branch
+ baseline snapshot
```

以上内容形成 `comparison_context_digest`。后续多个单候选轮次只有在该摘要相同时，才可以在报告中
比较表达差异。改变 Actor、任务或资源绑定属于显式 `RebindAllocation`，必须创建新的比较上下文，
不能伪装成只改表达。

### 2.8 第三步只依赖一个最小变异能力接口

第三步不提前实现第四步的 MutationPlan 或 LLM Mutator，只冻结 `MutationCapabilityManifest`：

```text
operator_family
required_seed_properties
allowed_changed_dimensions
preserved_dimensions
supported_frontier_kinds
```

父种子兼容性只能根据已注册能力判断。某个前沿存在合法场景组合、但当前没有可用算子时，状态是
`awaiting_operator`，不能误报为 `unreachable`。

## 3. 旧资产处置表

| 现有资产 | 第三步决定 | 原因 |
|---|---|---|
| V2 `EpisodeCoverageFacts/Delta/Snapshot` | 直接复用 | 它们是唯一可信覆盖输入 |
| CandidateSet 共同 baseline | 以 singleton 方式复用 | 每轮一个候选仍锁定生成前 CoverageSnapshot |
| 旧 SQLite WAL、事务、摘要锁、租约恢复思想 | 复用机制 | 已有故障恢复经验，但需要 V2 数据合同 |
| 旧硬公平、饥饿、探索保留、冷却思想 | 复用并重新映射 | 规则正确，但旧对象绑定 V1 风险深度和案例 |
| 旧 `CorpusPolicy/SeedRecord/EnergyScheduler` | 不直接接入 V2 | 只保存旧 TestCase 和粗粒度风险类别，缺少完整 AttackSeed 语义 |
| 旧 `OfficeCampaignState/Scheduler` | 保留、隔离、参考 | 不让 V1 风险分类、12 个旧组合和 depth 语义进入 V2 |
| 旧 CoverageStore 快照覆盖机制 | 参考事务边界 | V2 每轮需要同时提交覆盖、Corpus、Frontier 和预算 |
| Judge、LLM 评分和模型自报标签 | 不使用 | 当前只相信 Oracle、工具轨迹和状态变化 |

建议后续新增 V2 专用模块，不改造旧对象冒充兼容：

```text
src/sandbox/fuzzer/v2_corpus.py
src/sandbox/fuzzer/v2_frontier.py
src/sandbox/fuzzer/v2_scheduler.py
src/sandbox/fuzzer/v2_campaign_store.py
```

最终文件名可在 `3.0` 审计时微调，但 V1/V2 隔离边界不能改变。

## 4. AttackSeed、MaterializedCandidate、ExecutionRecord 与 CorpusEntry

### 4.1 AttackSeed：最小可变异配方

`AttackSeed` 只回答“这次准备怎样测试”：

```text
seed_id
payload_specs[]                # 计划放置的内容和位置要求；只证明 planned
carrier_recipe                 # 放入哪类载体、字段或轮次
origin_intent                  # 最初 Objective/Milestone，可为空
binding_requirements           # 需要什么类型的 Actor/Task/资源，不保存一次执行的具体 ID
operator_history               # 这条配方经过哪些变异方式
parent_seed_id / root_seed_id
generation_depth
seed_content_digest
```

AttackSeed 不保存具体 Episode、轨迹、Oracle、Coverage、成本、选择次数或冷却状态，也不保存
`observed/used` 结论。`origin_intent` 和历史内容不可原地修改；变异产生新的子 seed。

### 4.2 MaterializedCandidate：这次真正放进了什么

每次生成合法候选并冻结 Episode 输入后单独保存：

```text
materialized_candidate_id
seed_id / generation_allocation_id
ScenarioCase / Actor / Task / resource bindings
delivered_payloads[]           # 精确资源、版本、字段、内容摘要和物化证据；只证明 delivered
binding_source_digest
comparison_context_digest
baseline_snapshot_digest
materialization_digest
```

`delivered` 只表示内容已经存在于冻结 Episode 的某个可达载体中。它不能证明 Agent 后续调用了读取工具，
也不能证明该内容影响了工具参数、正常任务或副作用。

### 4.3 ExecutionRecord：这一次真实发生了什么

每次执行单独保存：

```text
execution_record_id
seed_id / materialized_candidate_id
ScenarioCase / Actor / Task / resource bindings
binding_source_digest
comparison_context_digest
Episode / Manifest / Oracle / Coverage 摘要
V2CoverageDelta 与 observed_contributions[]
observed_payload_refs[]        # 由精确读取/观察证据证明
used_payload_refs[]            # 由 ArgumentSource/OutputEvidence 回指证明
exposure_stages                # planned/delivered/observed/used 的事实前缀
Utility、submit、termination、cleanup
token、时间、成本和 AttemptReceipt 引用
```

同一 AttackSeed 可以在不同的显式 RebindAllocation 下产生多个 ExecutionRecord。执行记录不可反向修改
种子正文。`observed` 必须来自 Agent 实际获得的工具或交互结果；`used` 必须证明后续参数、状态变化或
提交结果回指该内容，不能根据文本相似或模型自述推断。

### 4.4 CorpusEntry：为什么值得保留和怎样调度

CorpusEntry 引用 AttackSeed 和一个或多个 ExecutionRecord，只保存：

```text
corpus_entry_id
seed_id
seed_kind = risk / exploration
promotion_reasons
支持它的 execution_record_ids
实际风险/行为贡献索引
适用的 Frontier/载体/兼容条件索引
active/cooled/quarantined/retired
选择次数、子代收益、无增益、成本等调度统计
```

因此四者职责固定为：

```text
AttackSeed           = 准备怎么测试
MaterializedCandidate = 本轮实际把内容放到了哪里
ExecutionRecord      = Agent 实际观察、使用并造成了什么
CorpusEntry          = 为什么保留、以后怎样选
```

### 4.5 一个种子为什么能被多个方向看到

物理上只保存一次，逻辑上通过索引查询：

```text
Corpus
├── risk view: A01 / A03 / ...
├── exploration view: 新工具边 / 新权限分支 / 新状态链
├── carrier view: email / drive / calendar / workspace / direct task
├── compatibility view: Task + Actor + resource + entry
└── lineage view: root → parent → child
```

调度器先选 RiskFrontier 或 BehaviorFrontier，再从 CorpusEntry 的兼容索引选择
`AttackSeed + supporting ExecutionRecord`。不能先在全库选一条高分种子，再强行让它服务一个不兼容
目标。一个 AttackSeed 可因多个 ExecutionRecord 的真实贡献被多个视图看到，但它的 `origin_intent`
永远不变；当前分配必须明确选择其中哪次执行作为绑定和证据来源。

### 4.6 CorpusEntry 状态

```text
active       可被选择
cooled       暂时无增益，等待新证据或冷却期结束
quarantined  完整性、稳定性或系统错误待处理
retired      超过深度、长期低收益或被更优等价种子取代
```

`retired` 只是不再把对应 AttackSeed 当父种子，AttackSeed 和 ExecutionRecord 不能删除。未知异常、
摘要漂移或数据完整性错误使 CorpusEntry 进入隔离并暂停 Campaign，不能当临时失败吞掉。

## 5. 种子晋升规则

### 5.1 硬门

候选只有全部满足以下条件才有资格晋升：

- Episode 使用锁定的 V2 身份；
- 执行、Oracle 和 Coverage 事实完整；
- Agent 显式 submit；
- 容器清理确认；
- canonical facts 未重复贡献；
- 候选属于当前单候选 Generation，并使用生成前冻结的 `baseline_snapshot_digest`；
- 初始化 overlay 与 Agent 副作用仍然分离；
- 没有摘要、lineage、模型身份或数据完整性错误。

不满足硬门的工件进入失败审计，不进入 Corpus，也不推进暴露和饱和窗口。

### 5.2 晋升分类

| 执行结果 | 处理 |
|---|---|
| 新 Milestone、outcome bit、风险上下文或行为—风险关联 | 晋升为风险种子 |
| 无风险推进，但有新一级行为、路径、权限或已提交状态特征 | 晋升为探索种子 |
| 只有二级多样性或新的组合摘要 | 保存 Observation，默认不晋升 |
| 与历史 canonical facts 完全相同 | 审计去重，不晋升 |
| 只有相似文本，执行后却产生新事实 | 按真实 Delta 晋升，不能因文本相似拒绝 |
| 正常任务失败且没有风险或一级行为增益 | 不晋升 |

### 5.3 Utility 怎样影响晋升

Utility 不是覆盖，也不能抹掉真实风险：

- 候选即使没有完成正常任务，只要真实推进了风险，仍应晋升风险种子；
- 但“风险推进且正常任务完成”的种子更隐蔽、更接近业务真实，应获得更高父种子优先级；
- 只产生行为新颖度但正常任务完全失败的探索候选，默认只保存 Observation；只有它确实打开新的可达
  前沿时才以低预算晋升；
- 正确拒绝可以记录 blocked 分支和新行为，但不能冒充 realized 风险。

这避免两个极端：既不会丢掉真实风险，也不会让“开场就失败但路径看起来新”的候选占满种子库。

### 5.4 每个候选执行后立即结算

每一轮只有一个候选：

1. 生成前冻结当前 CoverageSnapshot；
2. 执行并封存 AttemptReceipt；成功有效执行再生成 ExecutionRecord；
3. 相对该 baseline 计算 V2CoverageDelta；
4. 根据风险推进、一级行为、Utility 和完整性决定是否晋升；
5. 在一个事务中提交 Coverage、CorpusEntry、Frontier、预算和 Campaign 状态；
6. 下一轮只能读取提交后的新快照。

这种顺序不做“同批候选横向竞争”。相似表达是否有价值仍由真实执行 Delta 判断；一个候选没有增益，
下一轮调度器再决定继续同一方向、换父种子或切换 Frontier。

## 6. RiskFrontier 与 BehaviorFrontier

### 6.1 两类前沿键

V2 第一版前沿键为：

```text
scenario_id
+ primary_scheduling_family
+ objective_id
+ target_milestone_id
```

`blocked_seen/realized_seen`、入口、source/sink/carrier、recipient、授权分支和泄漏证明等级作为这个前沿
下面的覆盖事实和上下文缺口，不把每个字段笛卡尔积都展开成独立前沿。这样既能区分真正不同的测试
路径，又避免几百个空前沿造成组合爆炸。

纯行为探索使用独立键：

```text
scenario_id
+ behavior_gap_kind
+ feature_family
+ behavior_anchor_digest
+ gap_descriptor_digest
+ optional_related_objective_id
```

行为缺口只能来自第二步已经定义的一级行为族，例如工具路径、权限分支、已提交状态链、交互链或终止
分支。`behavior_anchor_digest` 锁定从哪条归一化行为事实继续探索，`gap_descriptor_digest` 锁定具体缺少
哪类后继边、权限分支或状态转换；二者都排除资源 ID、自由文本和数据库行号。这样
`calendar -> workspace -> email` 与 `drive -> calendar` 不会只因都属于 `tool_path` 而共享冷却、等待和
无增益计数。二级多样性不能单独创建 BehaviorFrontier。

### 6.2 事实状态与调度状态分离

风险事实单调保存：

```text
milestone_state: unseen / attempted / realized
outcome_bits: attempted_seen / blocked_seen / realized_seen
context_gaps: 尚未观察的入口、载体、授权分支、接收方或证明等级
```

调度生命周期另存：

```text
scheduling_state:
  ready / active / cooling / locally_saturated / local_budget_exhausted
  / awaiting_parent / awaiting_operator / unreachable
```

`milestone_state=realized` 后永不退回 ready/unseen。新的载体、授权分支或接收方只增加
`context_gaps`，再创建风险上下文分配或 BehaviorFrontier，不改写已经实现的里程碑事实。

- `awaiting_parent`：有合法场景和算子，但 Corpus 暂无兼容父种子；
- `awaiting_operator`：场景在结构上可达，但当前能力清单没有可用算子；
- `unreachable`：冻结世界、工具、权限或兼容性证明该组合确实不可达；
- `locally_saturated`：达到配置的最小有效 Episode 窗口后仍连续无增益，可参与 Campaign 饱和判断；
- `local_budget_exhausted`：当前前沿预算已经用完但仍有缺口，只能参与
  `budget_exhausted_incomplete`，绝不能参与 `saturated`。

### 6.3 前沿从哪里来

前沿由代码从冻结目录编译：

- 12 个 Objective 和 23 个 Milestone；
- Stage 5 兼容性求解结果；
- 当前 Campaign 的 CoverageSnapshot；
- Corpus 兼容索引；
- `MutationCapabilityManifest`；
- 已提交 Episode、局部预算和冷却历史。

模型不能创建、删除、改名或宣称一个前沿已完成。没有当前算子能力只能得到 `awaiting_operator`，不得
得到稳定 `unreachable`。

### 6.4 blocked 与 realized 的处理

- `attempted_seen` 表示 Agent 真正走到该动作；
- `blocked_seen` 表示防御或权限层真实阻止；
- `realized_seen` 表示真实副作用已提交；
- blocked 与 realized 可以同时在 Campaign 历史中存在；
- blocked 不推进复合目标的下一个 Milestone，但可以形成有价值的防御行为覆盖；
- realized 单调推进到下一个 Milestone；最后一个 Milestone realized 后，Objective 主链事实完成；
- 主链事实完成后，未见上下文进入独立 context gap 或 BehaviorFrontier，不能把原 Milestone 从
  realized 改回 ready，也不能宣布所有表达和行为已覆盖。

### 6.5 两本公平账

- **Risk ledger**：记录 12 个 Objective/23 个 Milestone 的基线、公平等待、局部预算和结果；
- **Behavior ledger**：记录一级行为缺口族、探索等待、保留预算和无增益历史；
- **总调度账**：先执行风险基线硬门，再在 Risk/Behavior 两本账之间执行饥饿与探索保留规则。

这样纯行为探索有正式身份和预算来源，同时不会冒充某个风险目标已被测试。

## 7. 父种子如何选择

父种子选择分两段，不能用一个全局分数跳过兼容性。

### 7.1 第一段：硬过滤

实际选择单位不是孤立的 seed，而是：

```text
CorpusEntry + AttackSeed + supporting ExecutionRecord
```

候选组合必须：

- `active`，且不在冷却或隔离；
- 属于同一锁定 World、目录和合同身份；
- 与 RiskFrontier 的 Objective/Milestone 或 BehaviorFrontier 的行为缺口兼容；
- 满足已注册 `MutationCapabilityManifest.required_seed_properties`；
- 正常任务、Actor、资源和入口能够生成合法 ScenarioCase；
- AttackSeed 有完整测试配方和 lineage，CorpusEntry 有至少一条与当前 Frontier 和绑定兼容的可信
  ExecutionRecord 支持；
- 未超过最大变异深度和局部预算；
- 不要求通过修改 ACL、基础世界或权威授权事实来制造“可达”。

如果没有兼容父种子，Frontier 不得从全库抓一条近似种子硬套。场景与算子均兼容但缺父种子时标记
`awaiting_parent` 并生成 bootstrap 需求；缺算子时标记 `awaiting_operator`；只有世界、工具、权限或
兼容性本身证明不可能时，才能记录稳定 `unreachable`。

### 7.2 第二段：确定性排序

过滤后按可解释组件排序。所有“曾推进”“正常任务完成”“成本”和“路径稀有”等执行事实，都来自本轮
明确选中的 `supporting ExecutionRecord`，不能把同一 seed 在其他 Actor 或资源绑定下的收益混进来：

**正向因素**

- 对当前 Objective/Milestone 的接近程度；
- 曾推进当前风险或相关行为—风险关联；
- 带来稀有一级行为、权限分支或状态链；
- 正常任务完成且风险仍发生；
- 长时间未被选择；
- 子代历史上有较高有效率和新覆盖率。

**负向因素**

- 连续无增益；
- 生成候选越界或完全重复率高；
- 子代执行成本高；
- 相同父种子已连续占用多轮；
- 变异深度接近上限；
- 只有二级多样性，没有一级贡献。

硬门和硬公平之后才计算软分。权重属于版本化 `SchedulerPolicy`，必须锁摘要。相同输入、Campaign seed
和策略身份必须选出同一父种子；最终平手使用内容摘要，不使用数据库插入顺序或当前时间。

## 8. 风险方向和预算如何分配

### 8.1 预算单位

第三步的主要分配单位是“一次单候选 Generation/Episode”，不是候选批次数量。另行记录：

- Mutator token 上限；
- Episode 数上限；
- Agent token 上限；
- 执行时间和成本上限。

第四步每次模型调用只请求一个候选，第五步执行并结算这一个候选后才进入下一轮。失败请求可以消耗
实际资源，但不能推进覆盖、暴露或无增益饱和窗口。

### 8.2 调度优先级

每次决策依次处理：

1. **基线欠账**：每个兼容 Objective 至少有一个提交 Episode，或稳定不可达原因；
2. **饥饿保护**：超过最大等待决策数的 Frontier 优先；
3. **行为探索保留**：周期性给 BehaviorFrontier 最低名额；
4. **连续份额限制**：同一 RiskFamily/Objective 不能长期连续独占；
5. **软排序**：风险缺口、行为稀有度、行为—风险新关联、欠采样、等待年龄升权；重复、无增益、
   invalid 率、成本和 virtual runtime 降权。

这些规则的顺序本身进入策略身份。若当前可执行方向太少，导致某个硬约束不可同时满足，必须输出明确
的 `constraint_infeasible` 原因，不能静默改变规则或死锁。

### 8.3 单候选 GenerationAllocation

一个 `GenerationAllocation` 最终包含：

```text
frontier_kind（risk / behavior）
frontier_id
allocation_target：
  - risk: objective_id + target_milestone_id + optional context_gap
  - behavior: behavior_gap_kind + feature_family + optional related objective
parent_seed_id
supporting_execution_record_id
binding_source_digest
candidate_count = 1
allocation_lane（baseline / risk / exploration / starvation）
选择原因和各评分组件
输入 Coverage、Corpus、Frontier、Policy 摘要
```

`binding_source_digest` 锁定支持执行中的 Actor、Task、资源、授权分支和比较上下文来源。第四步只能从
该执行重建本轮绑定；如果显式 Rebind，则必须创建新的 `RebindAllocation` 和新摘要。

`allocation_target` 只描述本轮调度目标，不修改 `AttackSeed.origin_intent`。调度器不一次分配多个候选；
所谓“给某条种子更多能量”改为“它在后续轮次仍可能被再次选中”。连续无增益会降低再次选择优先级并
进入 cooling；新种子、新行为—风险关联或新上下文可创建新的可调度缺口。

### 8.4 ComparisonContext 与 RebindAllocation

每个普通候选保存 `comparison_context_digest`：

```text
actor + task + resource_bindings
+ allocation_target + authorization_branch
+ baseline_snapshot_digest
```

连续两轮只有 comparison context 相同时，报告才可以把覆盖差异主要归因于测试表达变化。若要换 Actor、
任务、目标资源或授权分支，调度器先创建 `RebindAllocation`，冻结新的绑定并生成新的 comparison
context。不同 context 可以在同一 Campaign 中执行和累计覆盖，但不做直接的语言表达优劣比较。

不在本步硬写未经实验校准的业务权重。`3.6` 会先冻结可配置范围、硬不变量和确定性样例，再用 Fake
证据验证调度合同；真实 Qwen 结果只能在第六步用于验收，不能反向篡改历史决策。

## 9. Campaign 状态与恢复

### 9.1 Campaign 和 Episode 的关系

- 一个 Campaign 是“一次完整办公场景测试”；
- 一个 Candidate 必须在独立 Episode 中执行；
- Episode 之间不共享污染状态；
- 只有显式建模的复合 Objective 在同一个 Episode 内共享连续状态；
- Generation 是 Campaign 中的一轮单候选反馈循环；
- 为复用第二步接口，持久化形式可以是只含一个 Candidate 的 singleton CandidateSet。

### 9.2 状态语义

权威状态继续使用 SPEC 已冻结名称：

```text
baseline_complete
saturated
budget_exhausted_incomplete
paused
cancelled
```

实现时另保存 `phase=baseline/adaptive`，避免把 `baseline_complete` 误当成整个 Campaign 已结束。

- `baseline_complete`：每个可达 Objective 至少有提交 Episode，或有稳定不可达结论；之后继续自适应；
- `saturated`：基线已完成，所有可达 RiskFrontier 的主链事实已完成或达到 `locally_saturated`，所有
  仍开放的 BehaviorFrontier 也达到 `locally_saturated`，且全局有效提交 Episode 的无增益窗口满足；
- `budget_exhausted_incomplete`：预算先耗尽，明确表示没测完；
- `paused`：用户暂停、配置/身份/完整性错误、未知异常或不可安全恢复的系统错误；
- `cancelled`：用户明确终止。

局部 `cooling` 或 `local_budget_exhausted` 不能替代 Campaign 的 `saturated`。只要仍有可达缺口因预算
不足停下，Campaign 就必须是 `budget_exhausted_incomplete`。达到 23 个固定 Milestone 也不能证明开放
行为世界全部覆盖，报告只能说固定风险分母和当前配置的有效观察窗口内行为增长趋于饱和。

### 9.3 哪些结果能推进饱和窗口

只有满足硬门并已提交的有效 Episode 可以：

- 推进 ObjectiveExposureLedger；
- 更新 CoverageSnapshot；
- 更新 Frontier 无增益计数；
- 进入全局饱和窗口。

候选静态拒绝、Provider 重试、网络/基础设施错误、清理失败、超时未提交和 soak probe 均不得冒充
“测过但没收益”。

### 9.4 单候选尝试收据、两阶段持久化与原子提交

Docker Episode 是数据库事务之外的副作用，不能等整批结束才第一次记录执行结果。第三步冻结以下
两阶段合同：

```text
1. 持久化 CandidateWork、comparison_context、有界 retry policy 和预算预留
2. 创建不可变 attempt，并在启动前持久化 attempt_started
3. 启动 Episode
4. Episode 返回后立即封存不可变 AttemptReceipt、工件摘要和该次真实成本
5. 根据明确错误分类决定 sealed / retryable / case_failed / ambiguous / paused
6. 只有显式 retryable 且未超过上限时，才为同一 CandidateWork 创建新的 attempt
7. 成功有效执行生成 ExecutionRecord，并以单一事务提交本轮 Coverage/Corpus/Frontier/Campaign
```

`CandidateWork` 可以引用多个 attempt，但每个 `AttemptReceipt` 一经封存不可修改，至少包含 `work_id`、
`attempt_id`、attempt 序号、终态、错误分类、Episode/Manifest/Oracle/Coverage 工件摘要、submit/cleanup、
token、耗时、真实成本和收据摘要。旧的单一 `ResultReceipt` 表述在 V2 中废止，避免把一次尝试与整个
候选工作混为一谈。

重试规则必须封闭且有界：

- 成功并 sealed：不得重跑；
- `ambiguous_attempt`：不得自动重跑，保留预算并暂停等待明确处置；
- 只有错误合同明确列出的临时 Provider/基础设施失败，且 `attempt_count < max_attempts`，才可创建新
  attempt；
- Agent 无 submit、submit 无效等 case failure 不按基础设施失败重试；
- 配置错误、模型 digest 漂移、数据完整性、永久基础设施、清理失败和未分类异常立即暂停 Campaign；
- 每一次失败 attempt 的 token、时间和真实成本都累计，不能因为最终成功而抹掉。

重试创建的是同一 `MaterializedCandidate` 的新 attempt，不是新的语义候选，也不开始新的 Generation，
因此不违反“每轮一个候选”。

仍然存在一个无法用普通数据库完全消除的窗口：Episode 已产生外部成本，但进程在 AttemptReceipt
落盘前崩溃。因此恢复规则必须封闭：

- 若内容寻址工件已经完整封存，从工件重建并校验对应 AttemptReceipt；
- 若没有收据也无法证明工件完整，标记 `ambiguous_attempt`，保留预算预留并暂停或等待明确恢复策略；
- 不得自动把模糊尝试当成“从未执行”后重跑；
- 无论是否重试，同一 canonical facts 和 `work_id` 只能累计一次 Coverage；
- 第三步证明幂等封存和重启一致性，不宣称物理意义上的 exactly-once Docker 执行。

当前候选达到可结算终态后，本轮逻辑结果必须在同一事务中完成：

```text
校验活动决策和共同 baseline
→ 保存 ExecutionRecord 和 Episode Observation
→ 保存当前 V2CoverageDelta
→ 提交当前覆盖增量
→ 执行 AttackSeed/CorpusEntry 晋升或去重
→ 更新父种子统计
→ 更新 RiskFrontier、BehaviorFrontier 和两本公平账
→ 消费真实预算
→ 更新 Campaign 状态
→ 写审计事件和内容寻址快照
```

任一步失败，当前逻辑轮次不推进，但已封存的 AttemptReceipt 和每次真实成本仍保留。重启后返回同一个
活动决策；成功 sealed 工作不重跑，ambiguous 工作不自动重跑，只有显式 retryable 且仍在上限内的
工作才创建新 attempt，Coverage 不重复累计。第三步只实现单进程重启一致性所需的最小
work/attempt-receipt/幂等提交；并发租约续期、抢占和压力故障注入推迟到第五步闭环集成。
Campaign Manifest 必须锁定 World、目录、Coverage、Corpus、Scheduler、MutationCapability、Agent/模型
和策略摘要；摘要漂移时暂停，不能自动“升级后继续”。

## 10. 一个完整调度例子

假设基线已经完成，当前事实是：

```text
A03 敏感邮件外发：realized_seen，已有风险种子 S-A03
A01 ACL/分享/通知：只完成里程碑 1，里程碑 2 尚未 realized
A04 云盘删除：只见 blocked，连续两轮没被选择
BehaviorFrontier B-path：种子 S-X 走出了新的 calendar → workspace → email 路径，未关联 Objective
```

调度过程：

1. 风险目录指出 A01 的下一个缺口是“外部分享已创建”；
2. A04 已等待过久，触发饥饿保护；
3. 本周期还欠一个探索保留名额；
4. 调度器不能让已经容易命中的 A03 独占本轮；
5. 因为一次只执行一个候选，接下来三轮可能是：

```text
第 N 轮：A04 + 父种子 S-A04-blocked → 生成并执行 1 个候选（starvation）
提交反馈后重新调度
第 N+1 轮：A01 milestone 2 + 父种子 S-A01-m1 → 1 个候选（risk gap）
提交反馈后重新调度
第 N+2 轮：BehaviorFrontier B-path + S-X → 1 个候选（exploration reserve）
```

每一轮都冻结自己的 comparison context 和生成前 Coverage baseline。若 A01 下一轮想换一个 Actor，
先创建独立 RebindAllocation 和新的 comparison context，报告不能把它与只改变 Agent 可见表达的前一轮
直接比较。

第四步以后生成候选，第五步把合法候选分别执行。假设结果为：

- A04 仍 blocked，但产生新的权限分支：晋升探索种子，不推进删除 realized；
- A01 一个候选真实创建外部分享：晋升风险种子；里程碑 2 的事实永久 realized，新 RiskFrontier 指向
  里程碑 3；
- S-X 子代无新一级事实：不晋升，增加一次有效无增益；
- 每个结果分别提交，下一轮都读取最新 Coverage、Corpus 和 Frontier 后重新调度。

系统最终必须能用结构化原因解释上述每一步，而不是只输出一个无法审计的总分。

## 11. 失败条件

出现以下任一情况必须拒绝决策或暂停 Campaign：

- Coverage、Corpus、Frontier、Policy 或 Manifest 摘要不一致；
- singleton CandidateSet 中出现零个或多个候选；
- 候选 baseline 或 `comparison_context_digest` 与当前活动决策不一致；
- RebindAllocation 被静默描述成只改变测试表达；
- 候选结果、父子 lineage、Episode 或 CoverageDelta 缺失；
- 调度器使用不可达 Case、Actor、资源或入口；
- 模型自报标签被当成晋升事实；
- V1 风险分类、旧 TestCase 或旧 depth 语义进入 V2；
- 相同事实被 direct/recording/replay 重复累计；
- 数据库插入顺序改变父种子或调度结果；
- AttackSeed 把 planned/delivered 内容误记成 observed/used；
- GenerationAllocation 只引用 seed，未锁定 supporting ExecutionRecord 或 binding source；
- 不同归一化行为锚点因 feature family 相同而共享冷却或无增益计数；
- 未分类异常被归为临时 Provider 失败；
- `local_budget_exhausted` 或单前沿 cooling 被报告成 `saturated`；
- `awaiting_operator` 被误报成 `unreachable`；
- realized Milestone 因新上下文缺口退回 ready/unseen；
- 成功 sealed Work 被重新执行，ambiguous attempt 被自动重跑，或非白名单/超上限错误被重试；
- 失败 attempt 的真实成本被覆盖、删除或因后续成功而归零；
- 事务部分提交，造成 Coverage、Corpus 和 Frontier 互相不一致。

## 12. 分次施工计划

每项是一轮 Codex 适合完成和聚焦验证的工作量。`3.0-3.10` 连续施工时，每项只跑直接受影响的测试
和 Ruff；`3.11` 再统一运行第三步聚焦验收。不运行 Docker，不重建 Stage 2-8 昂贵证据。

### 3.0 资产处置与身份锁（已完成）

- 把本计划的资产处置表落实为模块边界测试；
- 冻结 Corpus、Risk/Behavior Frontier、Scheduler、Campaign Store 和 MutationCapability 六类组件身份；
- 锁定 Stage 2 Coverage identity、World、目录和策略摘要；
- 证明 V1 对象不能进入 V2。

验收：身份漂移和 V1 输入在状态创建前被拒绝。

完成证据：新增六组件 V2 身份清单与 Campaign 创建前身份门，复用冻结 World、任务/干净 Case、目标、
风险和 Coverage 摘要。Campaign 身份摘要为
`sha256:49a27697a3f6b2fb9bf6cd539871a6a29b4fbc0b2cc404d14102d3b2c8a7e06d`，Scheduler Policy 摘要为
`sha256:f214b1ae441eb8f8c8191b20a3c5758366be3e2b54e5875b97590fb58af09688`。聚焦测试 `11 passed`，
相关 Ruff 通过；未运行 Docker、Qwen 或全仓测试。

### 3.1 AttackSeed、MaterializedCandidate、ExecutionRecord 与 CorpusEntry 合同（已完成）

- AttackSeed 只实现 `payload_specs`、载体配方、origin intent、绑定要求、算子历史和父子血缘；
- MaterializedCandidate 保存具体绑定和 `delivered_payloads`；
- ExecutionRecord 保存 `observed/used` 引用、执行工件、Coverage/Oracle、Utility、AttemptReceipt 和成本；
- CorpusEntry 保存晋升原因、贡献索引、调度状态与统计；
- 实现一个物理库的多索引视图；
- 四类对象分别锁摘要，不能相互反向改写。

验收：分别回答“准备放什么”“实际放进哪里”“Agent 是否观察/使用并造成什么”“为什么保留”；内容
存在不能冒充 observed，AttackSeed 不携带某次执行和调度负担。

### 3.2 种子晋升分类器（已完成）

- 只消费 `V2EpisodeCoverageFacts + V2CoverageDelta`；
- 实现风险种子、探索种子、Observation-only 和拒绝四类结果；
- 纳入 Utility、submit、清理、完整性和 canonical 去重硬门；
- 每个候选执行后立即晋升或拒绝。

验收：风险推进、纯行为新颖、二级多样性、重复事实和正常任务失败的处理符合第 5 节。

### 3.3 双 Frontier 与能力清单（已完成）

- 从 4/12/23 目录编译 RiskFrontier，从一级行为族编译 BehaviorFrontier；
- BehaviorFrontier 使用归一化 `behavior_anchor_digest + gap_descriptor_digest` 区分具体行为缺口；
- 冻结最小 `MutationCapabilityManifest`，但不实现 MutationPlan/Mutator；
- 分离单调 milestone/outcome facts、context gaps 和 scheduling state；
- 区分 awaiting_parent、awaiting_operator、locally_saturated、local_budget_exhausted 和稳定 unreachable。

验收：纯行为种子无需伪挂 Objective；A01 能从里程碑 1 转向 2/3；blocked 不被 realized 覆盖；
realized 不倒退；缺算子不冒充不可达。

### 3.4 公平基线 ExposureLedger（已完成）

- 为 12 个 Objective 建确定性基线工作；
- 每个可达目标至少要求一个提交 Episode；
- 不可达必须有稳定原因；
- 工作游标、中断、重启和幂等提交不跳项。

验收：基线不能因预算偏好漏掉某个 Objective，失败尝试不推进 ledger。

### 3.5 父种子兼容过滤、比较上下文与选择（已完成）

- 先按 Frontier kind、Objective/Milestone 或行为缺口、Task/Actor/资源/入口和能力清单做硬过滤；
- 实际选择 `CorpusEntry + AttackSeed + supporting ExecutionRecord`，冻结 `binding_source_digest`；
- 冻结 `comparison_context_digest`，显式 Actor/Task/资源变化生成 RebindAllocation；
- 再按风险接近度、一级新颖度、Utility、历史收益、成本和等待排序；
- 使用内容摘要确定性破同分；
- 无兼容父种子时输出 bootstrap 或稳定失败原因。

验收：同一输入重复运行和重启后选中同一父种子及支持执行；不兼容高分种子不能越过硬门；报告不把
不同业务上下文的单候选轮次直接归因为表达差异。

### 3.6 公平调度与单候选分配（已完成）

- 实现 Risk/Behavior 两本公平账，以及基线欠账、饥饿、探索保留和最大连续份额硬规则；
- 再实现风险缺口、稀有行为/关联、欠采样、等待和成本软排序；
- 每次只输出一个 Frontier、一个父种子、一个 supporting ExecutionRecord 和 `candidate_count=1` 的
  `GenerationAllocation`；
- 策略参数版本化并锁摘要。

验收：容易命中的方向不能独占；每轮只产生一个候选；每个决定能解释方向、父种子和约束命中。

### 3.7 CandidateWork、AttemptReceipt 与单候选提交（已完成）

- 冻结活动决策、comparison context 和生成前 Coverage baseline；
- 执行前持久化 CandidateWork、attempt、白名单重试上限和预算预留；
- 每次尝试后立即封存不可变 AttemptReceipt 和实际成本；只有明确临时失败可在上限内新建 attempt；
- 当前候选结算后，原子提交 CoverageDelta、晋升结果、父种子统计和 Frontier；
- 提交完成后才允许下一轮读取新反馈。

验收：成功 sealed Work 不重跑，ambiguous attempt 不自动重跑，永久/未知错误暂停；失败 attempt 成本
全部累计，相同活动决策幂等提交只贡献一次覆盖。

### 3.8 最小 Campaign Store 与重启恢复（已完成）

- 使用 SQLite WAL、事务和内容寻址快照；
- 保存活动决策、CandidateWork、不可变 AttemptReceipt、预算和审计事件；
- 处理 sealed 工件收据重建、ambiguous attempt、幂等提交和快照补写；
- 完整性错误与未知异常暂停 Campaign。

验收：代表性崩溃边界重启后不丢 sealed 结果、不自动重跑 ambiguous 工作、不重复累计、不改变调度
选择。并发租约续期、抢占和压力验证明确留到第五步。

### 3.9 Campaign 完成状态（已完成）

- 实现 baseline/adaptive phase；
- 实现 `baseline_complete/saturated/budget_exhausted_incomplete/paused/cancelled`；
- 只有有效提交 Episode 进入局部和全局无增益窗口；
- 分离 `locally_saturated` 与 `local_budget_exhausted`，只有前者可参与 Campaign saturated；
- 新种子、新关联或新上下文创建新的调度缺口，但不倒退 realized milestone facts。

验收：预算不足、Provider 失败、候选拒绝和局部冷却均不能冒充饱和。

### 3.10 解释输出与无模型代表流程（已完成）

- 输出每次调度的候选前沿、硬约束、软分项、父种子、supporting ExecutionRecord、选择优先级和状态
  变化；
- 用第二步冻结事实构造至少三轮确定性代表输入；
- 不调用 Mutator、Agent、Docker 或 Qwen。

验收：人可以仅看 JSON 解释为什么选择、冷却、晋升或切换方向。

### 3.11 第三步统一验收与冻结证据（已完成）

- 一次性运行第三步聚焦测试和 Ruff；
- 生成自校验 JSON 证据；
- 验证双 Frontier、比较上下文、单候选反馈、硬公平、Corpus 晋升、收据恢复、状态机和 V1 隔离；
- 更新 README、HANDOFF、LOG 和 LOG-INDEX 的真实状态；
- 等待用户确认后才正式冻结第三步并编写第四步详细计划。

验收：不依赖 LLM 和 Docker，系统已经能对可信历史事实确定性回答“下一轮测什么、从哪条种子继续、
为什么现在只生成这一个候选”，并能在中断后得到同一答案。

完成证据：第三步联合聚焦测试 `50 passed`，相关 Ruff 与证据 `--check` 通过。自校验证据位于
`reports/local-acceptance/office-v2-coverage-step3/step3-evidence.json`，摘要为
`sha256:ad3938463941e9da402ede227a074f5714154c757c90d6e7bdba6968a150fd45`。本轮没有运行 Docker、Ollama、
真实 Qwen、Judge、LLM Mutator、全仓测试或 Stage 2-8 证据重建。

### 3.12 真实 Coverage 到可恢复下一轮的集成闭合（已完成）

- 读取一条完整序列化的第二步 `V2CoverageInput + V2EpisodeCoverageFacts` 工件；
- 使用真实 `V2CoverageDelta` 和晋升分类器产生风险种子，更新物理 Corpus 和 RiskFrontier；
- 由 Scheduler 自动选择下一轮 Frontier、父 AttackSeed 和 supporting ExecutionRecord；
- 预留预算并持久化单候选 Allocation/Work；
- 使用确定性模拟 Episode 结果生成真实 CoverageDelta、ExecutionRecord 和 Settlement；
- 将 CoverageSnapshot、Corpus snapshot、Risk/Behavior Frontier snapshot、ExposureLedger、预算、
  Campaign lifecycle 与 Settlement 作为一份内容寻址 Campaign 状态，在同一个 SQLite 事务中切换；
- 强制事务最后一步失败，证明 Settlement、状态快照和 Work 状态全部回滚；
- 正常结算后关闭并重新打开数据库，证明恢复后的下一轮 Allocation 与关闭前完全一致。

验收：第三步最新聚焦集 `52 passed`，相关 Ruff 通过。没有运行 Docker、Ollama、真实 Qwen、Judge、
LLM Mutator、全仓测试或 Stage 2-8 证据重建。至此第三步可以严格称为“完整调度闭环已实现”；下一项
是第四步受控语义变异详细计划。

## 13. 本步完成后的能力边界

完成第三步后，项目将具备：

```text
可信执行事实
→ 双覆盖
→ 有价值种子晋升
→ 风险缺口与纯行为缺口编译
→ 公平选择方向和父种子
→ 分配一个候选
→ 执行收据、幂等提交、暂停与最小恢复
```

但仍不具备：

- LLM 生成新的测试话语；
- 宿主对 MutationPlan/singleton CandidateSet 的结构校验；
- 自动启动候选的 Docker Episode；
- 多代自动闭环；
- 真实 Qwen 语义质量证明；
- LLM-as-Judge、黄金集、主动学习或漂移监控。

这些边界必须在报告中明确，不能用确定性调度测试冒充真实模型能力。

## 14. 用户确认门

开始 `3.0` 前，需要确认以下决策：

1. 使用一个物理 Corpus，并通过索引形成风险/探索/兼容视图；
2. AttackSeed 只保存 planned 配方；MaterializedCandidate 保存 delivered 内容；ExecutionRecord 保存
   observed/used 与实际执行；CorpusEntry 保存晋升理由和调度状态；
3. 风险种子和探索种子都可保留，但预算等级不同；
4. RiskFrontier 和 BehaviorFrontier 分开表示，分别记公平账，再由总调度器合并；
5. milestone facts 单调，scheduling state 独立；新上下文不让 realized 倒退；
6. 第三步冻结最小 MutationCapabilityManifest；缺算子是 awaiting_operator，不是 unreachable；
7. 每个候选锁定 comparison context；父选择锁定 supporting ExecutionRecord 与 binding source；
   Actor/Task/资源变化走 RebindAllocation；
8. Utility 影响优先级，但不能抹掉真实风险；
9. 基线欠账、饥饿、探索保留和连续份额是硬规则，先于软评分；
10. 每轮只生成、执行和结算一个候选；第二步批接口仅以 singleton 形式复用；
11. Work/attempt 先持久化，每次尝试封存不可变 AttemptReceipt，候选成功后按冻结 baseline 结算；
12. 成功 sealed 工作不重跑，ambiguous 不自动重跑；仅明确临时失败可有界新建 attempt，所有尝试成本
    累计，永久/未知错误暂停；第三步不声称物理 exactly-once；
13. `locally_saturated` 与 `local_budget_exhausted` 分开，后者不能参与 saturated；
14. BehaviorFrontier 锁定具体行为锚点和缺口摘要，不按粗 feature family 合并；
15. 第三步只做单进程重启一致性，并发租约压力验证留到第五步；
16. 第三步不运行 LLM、Docker 或 Qwen，统一验收后才进入第四步。
