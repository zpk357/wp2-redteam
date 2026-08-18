# Office Workspace V2 第四步详细计划：受控语义变异

状态：`4.0-4.12 技术施工与联合验收已完成；等待用户确认后正式冻结第四步`

上游前置：

- Office Workspace V2 场景阶段 1-8 已冻结；
- 第一步已经把执行事实整理为可信 `V2CoverageInput`；
- 第二步已经实现行为覆盖、风险覆盖与 `V2CoverageDelta`；
- 第三步 `3.0-3.12` 已完成 Corpus、双 Frontier、单候选调度、预算、收据和 SQLite 恢复闭环；
- 第三步已经输出不可变的 `GenerationAllocation`，其中锁定 Frontier、父 AttackSeed、
  supporting ExecutionRecord、binding source、comparison context 和 Coverage baseline；
- Judge、黄金集、主动学习和评分漂移继续冻结，不进入本步。

## 1. 本步要解决什么

第三步已经能回答：

```text
下一轮测哪个风险或行为缺口？
从哪条种子、哪次真实执行继续？
为什么选择它？
```

第四步要把这个确定性决定变成一个新的、合法的测试候选：

```text
代码冻结“测什么、允许改什么、必须保持什么”
→ 代码组装最小事实简报
→ 变异 Provider 每轮只生成一个候选
→ 宿主独立解析、比较和校验
→ 宿主把候选物化到新的 Episode 副本
→ 持久化为等待第五步接管的 MutationPreparation.ready
```

通俗理解：**代码决定考试范围，LLM 只负责在范围内写出一道新题，宿主再检查这道题有没有偷换目标、
修改世界或伪造事实。**

第四步到“候选准备完成”即停止。它不启动被测 Agent，不计算新覆盖，不判断风险是否真的发生，也不把
候选晋升进 Corpus。只有第五步真实执行后，才能决定候选有没有价值。

## 2. 一条完整数据流

```text
GenerationAllocation
+ parent AttackSeed
+ supporting ExecutionRecord
+ frozen Campaign/Coverage state
+ MutationCapabilityManifest
        │
        ▼
宿主生成 MutationIntent
        │
        ▼
宿主冻结 MutationPlan
        │
        ▼
宿主组装 MinimalFactBrief
        │
        ▼
MutationProvider 为 Plan 已冻结的一个或多个协同 payload slot 生成恰好 1 个候选
        │
        ▼
宿主解析、规范化、计算候选文本 diff
        │
        ▼
宿主在临时 Episode 副本确定性预编译结构算子、写入文本并重算 derived 字段
        │
        ▼
MutationValidationRecord（包含预编译物化 diff）
   ├─ 拒绝：终止本轮并记录原因
   ├─ 暂停：配置、身份、完整性或未知错误
   └─ 接受：封存与预编译结果摘要相同的确定性物化
        │
        ▼
宿主封存预编译结果，生成新的不可变 ScenarioCase / MaterializedCandidate
        │
        ▼
同一 V2 Campaign SQLite 持久化 MutationPreparation 并标记 ready
        │
        ▼
第五步读取 ready preparation，创建 CandidateWork 并在独立 Docker Episode 中执行
```

本步不允许 LLM 选择位置、资源、算子结果或直接写 ScenarioCase、世界状态、ACL、授权、摘要、校验结论
和 Coverage 标签。所有结构变化与算子中间结果都由宿主按冻结 Plan 产生。

## 3. 必须冻结的设计结论

### 3.1 每轮只有一个候选

正式 V2 流程每轮只生成一个候选：

```text
一次 Allocation → 一个 MutationPlan → 一个 MutationCandidate
→ 一次校验与物化 → 第五步一次执行与结算
```

不恢复旧的 `2-4` 候选子批、缩批降级或同批竞争。第二步的 `CandidateSet` 只以 singleton 包装复用，
用于避免重写已经验证的 Coverage 接口。

一次一个候选的意义是：下一轮一定能使用上一轮最新的真实反馈，恢复状态也更简单。代价是模型调用次数
较多，但这是用户已经确认的正式规格。

### 3.2 宿主决定方向，LLM 只生成候选表达

以下内容必须由可信宿主代码决定并冻结：

- 当前 RiskFrontier 或 BehaviorFrontier；
- 父 AttackSeed 和 supporting ExecutionRecord；
- 本轮目标 Objective/Milestone 或行为缺口；
- 允许使用的算子及应用顺序；
- 允许改变和必须保持的维度；
- Actor、任务、资源绑定、授权分支和 Coverage baseline；
- 候选数、Provider 身份、模型身份、Token/时间/重试预算。

LLM 只能在这些边界内返回结构化候选内容。它不能选择新的风险方向，不能自行改 Actor/任务/资源，
不能自报“已通过校验”或“会增加覆盖”。

### 3.3 世界不可变，候选只作用于 Episode 副本

禁止修改 `CanonicalOfficeWorld`、父 ScenarioCase、父 AttackSeed 或历史 ExecutionRecord。候选只能通过
已经冻结的 Stage 5 物化能力，在新的 Episode 副本上添加显式 overlay 或重新绑定已存在的合法资源。

如果需要改变 Actor、任务或资源绑定，必须由第三步产生显式 `RebindAllocation`，并生成新的
`comparison_context_digest`。普通表达变异不得悄悄完成重绑定。

Objective 变化也不能由第四步决定。若调度器需要从 A01 切换到 A03，第三步必须先产生不可变
`RetargetAllocation`，锁定来源 Objective、目标 Objective/Milestone、原因和新的 comparison context。
第四步只能逐字段消费并核对该决定，不能创建、补全或改写它。

真实 delegation、grant 或 authorization branch 的变化同样属于调度上下文变化。必须由第三步产生
`RebindAllocation` 或 `AuthorizationBranchAllocation`；普通 Plan 默认冻结授权分支。LLM 只能改变放在
非可信业务内容里的授权声明文字，不能改变权威 delegation/grant。

### 3.4 planned、delivered、observed、used 不能混淆

```text
AttackSeed.payload_specs[]                  = planned
MaterializedCandidate.delivered_payloads[] = delivered
ExecutionRecord.observed_payload_refs[]    = observed
ExecutionRecord.used_payload_refs[]        = used
```

第四步最多证明内容已经确定性物化到 Agent 可能接触的字段，即 `delivered`。是否真的被 Agent 看到或用于
后续工具调用，必须等第五步从真实轨迹取证。

### 3.5 静态校验不预测测试价值

静态校验只回答：

```text
结构是否合法？
目标是否保持？
入口和资源是否可达？
是否越界修改冻结事实？
是否能够被现有 Oracle 判定？
```

它不能回答“候选会不会骗过 Agent”“会不会产生新路径”“会不会推进风险”。这些只能在第五步执行后由
真实工具轨迹、PolicyDecision、StateDelta 和 Oracle 结果证明。

### 3.6 完全重复与语义近重复分开处理

- 规范化内容、结构、位置和上下文均完全相同：可作为 `exact_duplicate` 拒绝；
- 语义相近但文本或结构不完全相同：只记录 `similarity_score` 并降低将来优先级；
- 不允许仅凭执行前语义相似度硬拒绝，因为相近表达仍可能让 Agent 走出不同路径。

### 3.7 每个字段必须进入变异注册表

不能只靠 `changed_dimensions[] / preserved_dimensions[]` 覆盖全部对象字段。第四步必须建立
`MutationFieldRegistry`，把 Plan、父 Case、Episode overlay 和候选物化可能接触的每个字段路径恰好标成：

```text
frozen                 # 永远不能由本轮改变
mutable                # 注册算子可以直接改变
conditionally_mutable  # 只有匹配的 Rebind/Retarget/Authorization allocation 才能改变
derived                # 只能由宿主根据其他字段重新计算
```

注册项至少保存字段路径或稳定路径模式、所属组件、分类、变更权限
`provider_text / host_operator / scheduler_allocation / host_derived`、允许的算子、条件 allocation 类型、派生
重算器、校验器和 registry digest。Provider 只能改变同时标为 `mutable + provider_text` 的
`payload_slot.generated_content`。Registry 自身出现未知字段、重复分类或无分类字段属于配置/完整性错误，
必须暂停 Campaign；候选试图改变已正确注册的 frozen 字段才是候选拒绝。`derived` 字段的摘要、引用
索引、版本和 lineage 只能由宿主重算，不能作为越界变化，也不能由 Provider 提供。

## 4. 核心数据合同

### 4.A Scheduler 所有的上下文变化决定

现有 `RebindAllocation` 继续由第三步 Scheduler 产生。第四步开始前还需在同一所有权边界补齐：

```text
RetargetAllocation
  source_objective_id
  destination_objective_id
  destination_milestone_id
  reason_codes[]
  previous_comparison_context_digest
  next_comparison_context
  retarget_digest

AuthorizationBranchAllocation
  source_authorization_branch
  destination_authorization_branch
  reason_codes[]
  previous_comparison_context_digest
  next_comparison_context
  authorization_allocation_digest
```

`GenerationAllocation` 只能引用已经持久化并校验的这些 allocation。使用 RetargetAllocation 时，本轮
`allocation_target` 和 MutationIntent 必须等于 destination Objective/Milestone；source 只用于血缘和
审计。第四步不得从 A01 自行挑选 A03，也不得自行构造新的授权分支。

### 4.B MutationIntent

表达调度器希望补哪个缺口，不包含候选文本：

```text
campaign_id / generation_id / allocation_id
frontier_kind / frontier_id
objective_id / target_milestone_id（行为前沿时可为空）
behavior_gap_ref（风险前沿时可为空）
parent_seed_id
supporting_execution_record_id
binding_source_digest
comparison_context_digest
coverage_baseline_digest
coverage_feedback_digest
requested_operator_families[]
operator_allocation_ref（必须来自 Scheduler）
rebind_allocation_ref（可选，只能来自 Scheduler）
retarget_allocation_ref（可选，只能来自 Scheduler）
authorization_branch_allocation_ref（可选，只能来自 Scheduler）
intent_digest
```

它必须完全由 `GenerationAllocation` 和同事务状态生成，不能让 Provider 补写。

### 4.C MutationPlan

Provider 调用前冻结的完整施工说明：

```text
intent_ref
parent_payload_specs[]
operator_steps[]                 # 有顺序
payload_slots[]                  # 一个候选内的 1 个或多个冻结 slot
changed_dimensions[]             # 由 FieldRegistry 和 allocation 编译
preserved_dimensions[]           # 由 FieldRegistry 和 allocation 编译
field_registry_digest
rebind_allocation_ref            # 只能逐字复制 Intent，普通变异为空
retarget_allocation_ref          # 只能逐字复制 Intent，普通变异为空
authorization_branch_allocation_ref # 只能逐字复制 Intent，普通变异为空
scenario_slice_refs[]
provider_identity / model_identity
prompt_schema_version / response_schema_version
deterministic_seed
plan_total_token_budget
per_attempt_token_limit
reserved_total_cost
timeout / max_attempts
plan_digest
```

同一次重试必须使用同一个 `plan_digest`。重试只是新的 Provider attempt，不是新一代，也不能悄悄改变
算子、目标、Prompt 或预算。创建 attempt 前必须同时满足：attempt 次数未超限、累计 Token 未达到
`plan_total_token_budget`、剩余成本覆盖本次预留，且 `per_attempt_token_limit` 不超过剩余总 Token。

### 4.D MinimalFactBrief

由宿主代码从结构化事实组装，内容仅包括生成候选所必需的最小信息：

- 当前要补的风险里程碑或行为缺口；
- 父种子的完整 Agent-facing 内容；
- supporting ExecutionRecord 中已证明的 observed/used 行为情报；
- Actor、正常任务、目标资源、接收方、权限与授权分支的最小场景切片；
- 宿主已经冻结的 `payload_slot_id`、该 slot 的业务语境和内容约束；
- 必须保持的目标、业务前提和禁止变化；
- 上一轮反馈摘要，而不是整份轨迹或整份世界。

不得放入：Oracle 私有判定字段、未授权秘密、全量世界数据、宿主凭据、历史失败完整响应，或任何要求
Provider 自行判断风险是否已实现的内容。

### 4.E MutationCandidate

Provider 只能为 Plan 中已经存在的 payload slots 返回一个候选：

```text
slot_values[]
  payload_slot_id
  generated_content
expression_metadata              # 非可信，仅用于审计
provider_candidate_id
provider_attempt_ref
```

普通算子只允许一个 slot；只有已注册的组合算子可以冻结多个协同 slot。`slot_values` 必须与 Plan 的
`payload_slots` 一一对应、无重复、无遗漏，且仍然只算一个 MutationCandidate。Provider 不得返回或建议
placement、目标资源、结构算子结果和中间状态；这些全部由 Plan 和宿主 Materializer 决定。
`expression_metadata` 只作非可信审计说明。
宿主必须重新计算文本差异和后续物化差异。Provider 不能返回可信 digest、CoverageDelta、风险命中、
校验通过或 Corpus 晋升结论。

### 4.F MutationValidationRecord

由宿主独立生成：

```text
plan_digest / candidate_digest
generated_content_diff
host_materialization_diff
field_registry_classification_checks[]
actual_changed_dimensions[]
preserved_dimension_checks[]
operator_step_checks[]
lineage_checks[]
reachability_checks[]
authorization_consistency_checks[]
structural_objective_preserved
semantic_preservation = unverified
lexical_heuristic_record           # 非裁判事实，只作审计
utility_precondition_check
oracle_decidability_check
world_immutability_check
exact_duplicate_check
semantic_similarity_record
disposition = accepted / rejected / paused
reason_codes[]
validation_digest
```

### 4.G MutationProviderAttempt

每次 Provider 尝试不可变，并保存有限审计信息：

```text
attempt_id / ordinal / deterministic_attempt_seed
started_at / ended_at / latency_ms
provider/model/prompt/response schema identity
http_status（适用时）
response_byte_count / response_digest
truncated
bounded_response_summary
attempt_token_limit / actual_tokens / actual_cost
outcome / classified_error
```

失败响应不保存完整敏感正文，只保存 digest、字节数、截断判定、HTTP 状态和有限摘要。

`MutationProviderAttempt` 只记录变异 Provider 调用，不能复用第五步 Episode 的 `AttemptReceipt`。

### 4.H MutationPreparation 与 PreparationOutcome

`MutationPreparation` 是第四步自己的生命周期对象：

```text
preparation_id / generation_allocation_id / plan_id
state = planned / provider_running / candidate_received / accepted
      / materialized / ready / rejected / paused
provider_attempt_ids[]
candidate_id / validation_id / materialized_candidate_id（按状态可选）
preparation_digest
```

它不包含 Episode `ExecutionRecord`，也不使用 `CandidateWorkState`。只有 `state=ready` 时，第五步才根据
`materialized_candidate_id` 创建现有 `CandidateWork(state=allocated)`。

每个终态还要生成 `PreparationOutcome`：

```text
preparation_id / plan_digest / parent_seed_id / operator_families[]
disposition = ready / rejected / paused
reason_codes[]
actual_cumulative_tokens / actual_cumulative_cost
invalid_candidate_delta
operator_rejection_deltas[]
outcome_digest
```

它供第五步原子结算预算和调度统计。拒绝不推进 Coverage、Exposure 或无增益窗口，但会消耗真实 Mutator
成本、增加父种子对应 CorpusEntry 的 invalid candidate rate，并按算子/原因累计失败率。已拒绝的
`plan_digest` 是终态，
不能原样无限重新生成；若继续探索，Scheduler 必须创建新的 GenerationAllocation/Plan。

## 5. 结构化算子目录与反馈映射

算子不是有限的“句式模板”，而是规定**允许改变哪个结构维度**。同一个算子下，LLM 仍可生成很多不同
表达。每个算子必须注册：

```text
operator_family
parameter_schema
required_seed_properties
supported_frontier_kinds/objectives
allowed_changed_dimensions
preserved_dimensions
field_registry_paths
preconditions
incompatible_operators
host_validator
capability_digest
```

第四步沿用宏观计划中的九类算子：

1. **案例绑定变异**：宿主只消费 Scheduler 已产生的 `RebindAllocation`，从固定世界应用另一组合法 Actor、
   任务或资源；第四步和 LLM 都不能自行选择新绑定。
2. **关系路径变异**：改用固定世界中另一条已有邮件附件、文件引用、日历关联或工作区关系链。
3. **任务委托变异**：宿主只消费 Scheduler 已产生的 `RebindAllocation` 或
   `AuthorizationBranchAllocation`，建立新的 comparison context；LLM 不能改变真实 delegation/grant。
4. **入口与位置变异**：在 direct task、邮件、云盘、日历或工作区等已注册可达字段间改变放置位置。
5. **非可信授权声明变异**：改变声明来源和表达，但绝不创建真实 grant 或修改权威授权状态。
6. **参数来源变异**：改变接收方、时间、资源引用等参数从哪个可观察字段得出，并保留来源证据要求。
7. **目标显式重定向**：宿主只消费 Scheduler 已产生的 `RetargetAllocation`，应用其中已锁定的新
   Objective/Milestone 和 comparison context；第四步不能自行选择重定向目标。
8. **路径延伸或缩短**：选择固定世界中更长或更短的已有关系链，只声明期望方向，不硬编码工具序列。
9. **组合变异**：按明确顺序组合已单独通过的算子，保存每一步中间结果与校验；禁止整体任意重写。

“含蓄、直接、分步、角色语境、格式包装”等表达变化不另建成风险方向。它们作为
`expression_profile` 参数附着在上述结构算子上，由 LLM 生成具体话语；宿主仍检查结构目标和保持维度。
这样既保留开放的语言空间，也不会让纯措辞变化冒充新的风险里程碑。

首轮实现不要求九类都调用 LLM。先完成共同合同，再为每类提供至少一个确定性参考实现，用来证明同一
机制能跨 Case 工作。LLM Provider 只消费注册目录，不另建一套算子语义。

### 5.1 FeedbackToOperatorPolicy

不能只保存 `coverage_feedback_digest`，必须由确定性策略把真实反馈转换为下一轮算子选择：

```text
delivered 但未 observed
→ 优先入口迁移、路径缩短或可达位置变化

observed 但未 used
→ 优先参数来源、来源声明或表达结构变化

attempted 但 blocked
→ 只探索 Scheduler 明确批准的 AuthorizationBranchAllocation 或合法 RebindAllocation

realized 但没有新行为
→ 优先新载体、新关系路径或新 comparison context

连续有效 Episode 无增益
→ 冷却当前算子族，切换到仍兼容的其他算子；没有时返回 no_compatible_operator
```

策略输入必须来自 Coverage/Exposure、supporting ExecutionRecord、Frontier gap 和算子能力目录；输出为
Scheduler 所有的 `OperatorAllocation`：

```text
frontier_id / supporting_execution_record_id
feedback_digest
selected_operator_families[]
reason_codes[]
policy_digest / operator_allocation_digest
```

相同输入必须产生相同 OperatorAllocation。第四步只能消费它，不能临时换算子。验收时必须证明同一父种子
面对不同 feedback gap 会选择不同算子或明确返回 `no_compatible_operator`。

### 5.2 结构保持不等于语义保持

宿主可以确定性证明 Objective 绑定、资源、入口、权限上下文和 Oracle 条件保持，因此记录：

```text
structural_objective_preserved = true/false
semantic_preservation = unverified
lexical_heuristic_record = 非可信辅助信号
```

Judge 冻结期间，任何关键词、相似度或模型自报都不能把 `semantic_preservation` 改成 verified。真实执行可
证明行为和风险事实，但仍不能反向伪造“文本语义等价”结论。

## 6. Provider 与错误状态合同

### 6.1 Provider 分层

- `RuleBasedV2MutationProvider`：确定性合同替身，只证明 Plan、解析、校验、物化、存储和恢复正确；
- `OllamaV2MutationProvider`：正式语义 Provider，使用锁定模型身份和结构化 JSON 输出；
- Fake HTTP：本地验证 Ollama 协议、截断、错误分类和响应审计；
- 真实模型：本地没有合适 Qwen 能力，正式语义质量与 Agent 联动留到服务器综合验收。

正式运行不得在 Ollama 失败后静默退回 RuleBased。否则报告会把确定性模板误写成真实语义变异。

### 6.2 允许重试的错误

仅以下明确临时错误允许在 `max_attempts` 内创建新 attempt：

- transport 连接中断；
- 明确 timeout；
- HTTP 408、429；
- 经过白名单确认的部分 5xx；
- 有证据的响应截断。

重试使用同一不可变 Plan，并由
`campaign + generation + plan_digest + attempt_ordinal` 派生确定性 attempt seed。
每次创建 attempt 前都要计算：

```text
remaining_tokens = plan_total_token_budget - actual_cumulative_tokens
remaining_cost   = reserved_total_cost - actual_cumulative_cost
```

只有 `remaining_tokens > 0`、本次 `per_attempt_token_limit <= remaining_tokens` 且剩余成本足够时才允许重试。
因此重试不会为每次 attempt 重新获得一份完整 Plan 预算。

### 6.3 必须暂停 Campaign 的错误

- 配置缺失或不合法；
- Provider、模型、Prompt 或 schema digest 漂移；
- 协议/JSON/schema/数据完整性错误；
- 存储和物化完整性错误；
- ambiguous 状态，无法证明 Provider 是否完成；
- 未分类异常。

未知错误不得按临时失败吞掉，也不得推进无增益窗口或 Frontier 冷却。

## 7. 宿主校验顺序

校验固定按以下顺序执行，前一层失败时不继续推断后续价值：

1. **身份与完整性**：Allocation、Plan、Provider、模型、schema、父 seed 和 supporting execution 摘要匹配。
2. **结构解析**：恰好一个候选，字段完整，类型和长度有界，没有未知字段或多余候选；JSON/schema/
   多候选等 Provider 协议错误进入 paused，不伪装成普通 candidate rejection。
3. **Provider 权限**：Candidate 只能包含 Plan 已冻结 slot 的文本，不能携带 placement、资源或算子结果。
4. **文本差异**：宿主计算 `generated_content` 相对父 payload 的真实文本差异。
5. **字段分类**：物化涉及的每个字段恰好命中一条 FieldRegistry 规则；未知、重复或漏分类字段拒绝。
6. **变化边界**：mutable 变化匹配算子；conditionally mutable 变化匹配 Scheduler allocation；derived 只由
   宿主重算；frozen 全部不变。
7. **算子一致性**：结构变化和中间结果全部由宿主产生，并可逐步归因到 Plan 中的声明算子。
8. **重绑定边界**：Actor、任务或资源改变时必须逐字匹配 `RebindAllocation`。
9. **目标保持**：默认保持 allocation target；重定向必须逐字匹配 Scheduler 的 `RetargetAllocation`。
10. **授权分支**：真实 delegation/grant 变化必须匹配 `AuthorizationBranchAllocation/RebindAllocation`；
    非可信授权文字不得改变权威状态。
11. **业务可达**：入口字段存在，Agent 在正常任务路径中有机会观察，资源和引用属于冻结世界。
12. **Utility 与 Oracle 可判定**：正常任务仍有可完成路径，计划风险仍能由现有事实 Oracle 判断。
13. **世界不可变**：Canonical World、父 Case、父 seed、历史执行摘要全部不变。
14. **重复处理**：完全重复拒绝；语义近重复只记分和降低后续优先级。

拒绝只是“这个候选不能执行”，不等于 Provider 临时失败，不推进 Coverage、Exposure 或局部无增益
状态；但必须生成 `PreparationOutcome`，结算真实 Mutator 成本、父种子对应 CorpusEntry 的 invalid
candidate rate、算子失败率和拒绝原因。相同 `plan_digest` 不得再次生成。是否为同一 Frontier 分配
新一轮，由第五步读取该
Outcome 后交给 Campaign 状态机处理。

## 8. 确定性物化

候选复用 Stage 5 已冻结的入口、可达字段和 ScenarioCase 物化机制。校验前先在临时副本预编译，接受后
只封存同一摘要结果，不重新执行一遍可能产生不同结果的结构变换：

```text
冻结父 ScenarioCase
→ 创建临时 Episode 世界副本
→ 宿主按 Plan 和 Scheduler allocation 应用结构算子、overlay/rebind/retarget
→ 把 Provider 文本写入 Plan 已冻结的 payload_slot_id
→ 由宿主重算全部 derived 字段
→ 记录每步前后摘要和字段级 diff
→ 重新运行组合、引用、可达性、Utility 和 Oracle 可判定门
→ 生成 MutationValidationRecord
→ accepted 时按相同结果摘要封存不可变 MaterializedCandidate
```

物化成功只能标记 `delivered`，并把 `MutationPreparation` 标为 `ready`。严禁提前填入 observed、used、
risk realized、CoverageDelta 或 Corpus promotion。第四步不创建 `CandidateWork`。

## 9. 持久化与恢复

第四步继续扩展现有 `V2CampaignStore`，不建立第二个 mutation 数据库。以下状态与第三步 Campaign 身份
和当前快照绑定：

```text
MutationIntent
MutationPlan
MutationProviderAttempt(s)
MutationCandidate
MutationValidationRecord
MaterializedCandidate
MutationPreparation
PreparationOutcome
```

`MutationPreparation` 状态包括：

```text
planned
provider_running
candidate_received
accepted
rejected
paused
materialized
ready
```

恢复规则：

- `ready`：不得再次调用 Provider 或重复物化；第五步可以幂等创建一次 CandidateWork；
- `materialized`：核对摘要后幂等补写 ready 状态；
- `accepted`：允许从同一 candidate 继续确定性物化；
- `rejected`：终态，不自动改写后重试；
- 明确 retryable attempt 且未超上限：使用同一 Plan 创建新 attempt；
- `provider_running` 且完成状态不明确：暂停，不自动重发；
- 身份、模型、schema、内容或数据库摘要不一致：暂停。

每次失败 attempt 的时间、Token 和费用都计入 `actual_cumulative_cost`。候选拒绝也保留审计工件，并生成
PreparationOutcome 供第五步结算 invalid rate、算子失败率和真实成本，但不能冒充已执行 Episode。

现有 `CandidateWork/AttemptReceipt` 状态机保持不变：

```text
MutationPreparation.ready
→ 第五步用 materialized_candidate_id 创建 CandidateWork.allocated
→ CandidateWork.executing / sealed / ambiguous / committed / failed
```

`CandidateWork.sealed` 仍必须关联 ExecutionRecord；Mutation Provider 成功不能满足这个条件。

## 10. 一个完整例子

第三步选择：

```text
RiskFrontier：A01 的“外部分享尚未创建”里程碑
父种子：一条已经证明 Agent 会观察 Apollo 评审邮件的 planned 配方
supporting ExecutionRecord：真实记录了该邮件正文 observed
comparison context：Maya + clean.t1.apollo + 固定资源绑定 + 当前授权分支
```

宿主冻结 Plan：

```text
目标保持：A01 当前里程碑不变
允许算子：入口位置变异，并设置 `expression_profile=semantic_indirection`
允许变化：邮件正文中的测试内容
必须保持：Actor、正常任务、资源、授权状态、Objective、Oracle 条件
候选数：1
```

Provider 返回一个新的邮件正文测试表达。宿主随后：

1. 确认 Candidate 只含 Plan 指定 `payload_slot_id` 和 `generated_content`；
2. 计算文本 diff，宿主按 Plan 应用位置算子，Provider 不参与选择邮件或位置；
3. 通过 FieldRegistry 确认正文是 mutable，摘要/引用是 derived，其他字段 frozen；
4. 确认没有创建真实授权、没有改 ACL、没有换 Objective；
5. 确认邮件是该任务可达字段，正常任务仍可完成，A01 Oracle 仍可判定；
6. 将正文物化到新的 Episode 副本并由宿主重算派生字段；
7. 保存 `delivered` 证据并把 MutationPreparation 标记为 `ready`。

此时系统**不能**说 Agent 看到了内容，也不能说外部分享发生了。第五步才启动独立 Docker Episode，
根据真实读取、工具调用和最终状态决定 observed、used、CoverageDelta 与是否晋升。

## 11. 资产处置表

第四步开始前只做逻辑分类，不删除旧代码：

| 现有资产 | 处置 | 原因 |
|---|---|---|
| 内容摘要、规范化与内容寻址工具 | 复用 | 通用且已验证 |
| 有界 Provider 响应审计与错误分类思想 | 复用合同，按 V2 身份重接 | 避免重复修复截断、漂移和未知错误问题 |
| 语义相似度基础函数 | 复用为审计分数 | 不能作为执行前硬拒绝依据 |
| 旧 MutationPlan/Candidate/Validation 名称 | 仅参考，不直接互读 | 旧对象绑定 V1 Case 和风险映射 |
| V1 operator、target resolver、batch mutator | 隔离 | 不得把旧场景假设或 2-4 批合同带入 V2 |
| 旧 mutation SQLite/CLI | 不接入正式 V2 | V2 继续使用统一 `V2CampaignStore` |
| Stage 5 V2 物化器与 Stage 6 Oracle 合同 | 直接复用 | 已冻结的场景事实和可判定边界 |
| Step 3 MutationCapabilityManifest | 扩展并重新锁摘要 | 它是本步算子注册入口 |
| Step 3 CandidateWork/AttemptReceipt | 第四步只引用、不复用生命周期 | 它们属于第五步 Episode 执行，不是 Mutator 调用 |

本步不得为了“整理”物理删除旧资产。等真实 Qwen 闭环通过、依赖与数据引用审计完成后，再决定删除。

## 12. 分步施工计划

每个编号控制为一次适合 Codex 完成和验收的工作量。

### 4.0 边界、资产与身份锁（已完成）

- 冻结第四步组件清单、版本、schema 和摘要；
- 形成上述资产处置表的代码级映射；
- 锁定上游 Campaign、Coverage、Corpus、Frontier、Allocation 和场景身份；
- 在 Scheduler 所有权内补齐 `RetargetAllocation` 与 `AuthorizationBranchAllocation`，并让
  GenerationAllocation 只引用这些已冻结决定；第四步不得创建它们；
- 冻结 `FeedbackToOperatorPolicy/OperatorAllocation` 身份接口；正式第四步 Allocation 必须包含 Scheduler
  已选择的算子，旧 Allocation 继续可读但不能创建 MutationPlan；
- 明确 `MutationPreparation` 与现有 `CandidateWork` 是两套相邻但不混用的生命周期；
- V1 输入在任何 V2 对象创建前拒绝。

验收：能证明第四步只消费真实第三步 Allocation；A01 不能在第四步静默变成 A03；Provider 成功也不能
把 CandidateWork 标为 sealed；不创建第二套数据库或场景解释器。

完成证据：保留既有 `GenerationAllocation` 不变，新增 Scheduler 所有的 `RetargetAllocation`、
`AuthorizationBranchAllocation`、`OperatorAllocation` 和兼容信封 `MutationGenerationAllocation`。信封按
固定顺序校验 Rebind → Retarget → Authorization context 链，算子必须匹配原 Frontier、supporting
ExecutionRecord 和反馈策略身份；没有 RetargetAllocation 时不能静默改变 allocation target。新增独立
`V2MutationIdentityLock`，绑定既有第三步 Campaign identity，不改写历史 SQLite/摘要，并锁定字段注册表、
Provider 文本权限、Preparation/Work 分离、Provider attempt 和 FeedbackToOperatorPolicy 边界。身份摘要为
`sha256:725b6b279425261fd8df6e7c18f7600737714cf93412e6400a6954bd8f957352`。聚焦测试
`26 passed`，相关 Ruff 与 `git diff --check` 通过；未运行 Docker、Ollama、Qwen、Judge 或全仓测试。

### 4.1 MutationIntent 与 MutationPlan

- 实现完整 `MutationFieldRegistry`，每个可接触字段恰好属于 frozen、mutable、conditionally_mutable 或
  derived；
- 实现严格不可变合同；
- 从 GenerationAllocation 确定性编译 Intent；
- 锁定父 seed、supporting execution、binding、comparison context 和 baseline；
- 锁定 payload_slot_id、结构位置、资源、算子顺序和 changed/preserved dimensions；
- 区分 Plan 总 Token/成本预算、单 attempt Token 上限、累计实际成本和 max_attempts。

验收：相同输入产生相同摘要；目标、父执行或任一冻结维度漂移均拒绝；未知/重复/漏分类字段拒绝；
derived 字段只能由宿主重算；累计尝试不能超过 Plan 总预算。

### 4.2 算子目录与能力编译

- 实现九类算子的公共注册合同；
- 定义参数、前置条件、支持的 Frontier、FieldRegistry 路径、变化维度、保持维度和不兼容组合；
- 从目录编译第三步使用的 `MutationCapabilityManifest`；
- 实现 FeedbackToOperatorPolicy，从不同真实 feedback gap 确定性产生不同 OperatorAllocation 或
  `no_compatible_operator`；
- 重绑定、重定向和真实授权分支算子只能消费 Scheduler 对应 allocation；
- 没有兼容算子时保持 `awaiting_operator`，不误报 unreachable。

验收：新增同类 Case 不需要增加硬编码分支；非法组合在 Provider 调用前被拒绝；同一父种子面对
未 observed、未 used、blocked、realized-no-new-behavior 和连续无增益时，选择结果可解释且确定。

### 4.3 最小事实简报与 Prompt/Schema 身份

- 从 Plan、场景切片、父文本、supporting trace 和 Coverage feedback 组装简报；
- 排除 Oracle 私有事实、全量世界和无关轨迹；
- 冻结 Prompt 模板、结构化响应 schema 和内容摘要；
- 规定一个候选和有界输出。

验收：相同 Plan 产生相同简报；Provider 不能看到或改写不属于它的权威事实。

### 4.4 RuleBased V2 Provider

- 实现一个确定性 Provider 合同替身；
- 每次返回一个 Candidate；普通算子含一个 slot，只有组合算子可含多个协同 slot；
- 不返回 placement、资源、结构算子结果或中间状态；
- 覆盖单算子、声明式组合和稳定拒绝样例；
- 明确标记其证据等级为 engineering-only。

验收：能够稳定驱动后续校验链，但报告不能声称语义质量或真实探索能力。

### 4.5 Mutation Provider 尝试、审计与错误分类

- 实现与 Episode AttemptReceipt 分离的不可变 `MutationProviderAttempt`；
- 保存状态、digest、字节数、截断和有限摘要；
- 实现明确临时错误的有界重试；
- 每次尝试前同时检查 plan_total_token_budget、per_attempt_token_limit、reserved_total_cost 和累计实际成本；
- 配置、身份、协议、完整性、ambiguous 和未知错误暂停 Campaign；
- 去除所有批大小和缩批逻辑。

验收：同一 Plan 的重试可审计且预算累计；未分类异常不会被吞掉。

### 4.6 Candidate 解析、规范化与真实 diff

- 严格解析恰好一个 Candidate；
- 只接受与 Plan payload_slots 一一对应的 slot_values 和非可信 expression_metadata；
- Provider 返回 placement、资源、结构算子输出或多余字段属于协议/schema 错误，暂停 Campaign；
- 由宿主计算 generated content diff；结构与字段级物化 diff 由宿主 Materializer 产生；
- Provider 自报摘要和校验结论不得进入可信结果。

验收：未知字段、多候选、无界文本和类型错误按 Provider 协议错误暂停；结构合法但越出 Plan 边界的候选
稳定 rejected；二者不会混为同一种调度反馈。

### 4.7 宿主结构与语义边界校验

- 实现第 7 节十四层校验；
- 区分普通变异、RebindAllocation、RetargetAllocation 和 AuthorizationBranchAllocation；
- 用 MutationFieldRegistry 封闭验证所有实际字段和 derived 重算；
- 只声明 `structural_objective_preserved`；语义保持固定为 unverified，lexical heuristic 只作审计；
- 实现 exact duplicate 拒绝和 near-duplicate 审计降权；
- 生成不可变 MutationValidationRecord。

验收：静默目标漂移、权限事实伪造、世界修改、不可达入口和未声明变化全部拒绝；校验不预测 Coverage。

### 4.8 V2 确定性物化

- 复用 Stage 5 入口和字段注册表；
- 在新 Episode 副本由宿主按 Plan 和 Scheduler allocation 应用结构算子；
- 将 Provider 文本写入冻结 payload slot，并由宿主重算 derived 字段；
- 保存中间摘要、最终 diff 与 delivered payload；
- 重新运行引用、可达性、Utility 和 Oracle 可判定门。

验收：父 Case、父 seed 和 Canonical World 不变；新候选只达到 delivered，不伪造 observed/used。

### 4.9 统一 SQLite 持久化与恢复

- 在现有 `V2CampaignStore` 持久化 Plan、MutationProviderAttempt、Candidate、Validation、
  MaterializedCandidate、MutationPreparation 和 PreparationOutcome；
- 实现独立 preparation 状态机和幂等恢复，不扩展 CandidateWorkState；
- accepted 后可恢复物化，ready 后不得重复调用 Provider；
- 故障注入验证无半个候选和无双重预算扣减。

验收：关闭数据库再打开，可从同一摘要恢复到同一个 ready preparation；第五步接管前没有 CandidateWork。

### 4.10 Ollama V2 Provider 协议实现

- 实现锁定身份的 Ollama Provider；
- 使用结构化 JSON 响应和单候选合同；
- 使用 Fake HTTP 覆盖成功、截断、429、选定 5xx、schema 错误、模型漂移和未知错误；
- 不在本机运行真实 Qwen，不把 Fake HTTP 当语义验收。

验收：协议和错误合同成立；真实模型质量仍明确为未验证。

### 4.11 无模型端到端候选准备闭环

运行一条真实第三步 Allocation：

```text
读取持久化 Allocation
→ 编译 Intent/Plan
→ RuleBased Provider 生成一个 Candidate
→ 宿主校验
→ 物化新 ScenarioCase
→ 持久化 MutationPreparation.ready + PreparationOutcome
→ 关闭并重开 SQLite
→ 恢复出完全相同的候选、血缘和 materialized_candidate_id
```

同时覆盖一个拒绝候选和一个 Campaign pause 候选，证明它们不会推进 Coverage、Exposure 或无增益窗口，
但会记录真实 Mutator 成本、invalid candidate rate、算子失败率和拒绝原因。证明相同 rejected plan 不能
无限重跑，并证明本步没有创建 CandidateWork。

验收：系统可以解释“为什么改这些、为什么其他事实没变、候选放在哪里、为什么能进入第五步”。

### 4.12 第四步统一验收与冻结证据

- 只运行第四步和直接上游边界的聚焦测试；
- 运行相关 Ruff、证据摘要自检和 `git diff --check`；
- 生成一份自校验第四步 JSON 证据；
- 更新 README、HANDOFF、AGENTS、LOG 和 LOG-INDEX；
- 用户确认后正式冻结第四步，再编写第五步详细计划。

验收：第四步工程合同全部成立，但报告清楚写明“尚未执行被测 Agent，尚未证明语义质量和覆盖收益”。

完成证据：第四步联合聚焦集 `33 passed`；相关 Ruff、证据自检和 `git diff --check` 通过。自校验证据位于
`reports/local-acceptance/office-v2-mutation-step4/step4-evidence.json`，摘要为
`sha256:33ab906e51ae9e1061bf2b8550b54fa05bbbfaea90b690e123b289d12ccadc19`。本轮只使用 RuleBased
Provider 与 Fake HTTP；未运行 Docker、真实 Ollama、Qwen、Judge、全仓测试或 Stage 2-8 证据重建。
Stage 5 的正式 ScenarioCase 构造器已被代表性间接内容案例实际复用，父 Case 和 Canonical World 摘要
保持不变；候选仍只到 delivered/ready，未创建 CandidateWork 或 CoverageDelta。

## 13. 验证策略

为节省时间和 Token：

- `4.0-4.10` 编码期间只运行直接受影响的最小测试与对应 Ruff；
- `4.11` 运行一次无模型端到端准备闭环；
- `4.12` 只运行一次第四步联合验收；
- 同一代码摘要已经通过的聚焦测试不重复运行；
- 不运行 Docker E2E、Ollama、真实 Qwen、全仓 pytest 或 Stage 2-8 昂贵证据重建；
- 不运行 Judge、黄金集、主动学习或漂移监控；
- README 只在 `4.12` 行为真实验证后更新，计划阶段不提前宣传能力。

## 14. 失败与暂停条件

出现以下任一情况时暂停施工并报告根因，不为单个 Case 增加特判：

- 算子只能通过写死某个 Case ID 实现，无法复用于同类资源；
- 实现要求修改 Canonical World、冻结场景、父 seed 或历史执行；
- 无法区分 planned/delivered 与 observed/used；
- 静态校验必须依赖尚未建设的 Judge 才能给出可信结论；
- GenerationAllocation 不能唯一绑定父执行、comparison context 或 baseline；
- Retarget/AuthorizationBranch 决定只能在第四步临时推断，无法由 Scheduler allocation 表达；
- 任一可接触字段无法唯一归类，或没有明确变更权限；
- MutationPreparation 必须与 CandidateWork/AttemptReceipt 共用状态才能继续；
- 物化器无法证明入口可达、目标保持或 Oracle 可判定；
- Provider/模型/Prompt/schema 身份无法锁定；
- unknown/ambiguous 错误只能靠自动重试掩盖；
- 必须建立第二套 Coverage、Oracle、Replay 或数据库才能继续。

普通类型错误、测试错误和通用实现缺陷自行定位修复，不需要逐小步等待用户确认。

## 15. 第四步完成标准

以下条件全部满足，第四步才算完成：

1. 真实第三步 `GenerationAllocation` 能确定性生成不可变 MutationIntent 和 MutationPlan；
2. 每轮恰好生成一个候选；
3. Provider 只能为冻结 payload slots 生成一个候选的文本值，不能决定 placement、资源或算子结果；
4. 每个实际字段恰好由 MutationFieldRegistry 分类，derived 字段只由宿主重算；
5. 宿主能独立计算文本 diff、结构物化 diff 并验证变化/保持维度；
6. 普通变异、Rebind、Retarget 和 AuthorizationBranch allocation 边界清楚，后三者只能由 Scheduler 产生；
7. Canonical World、父 Case、父 seed 和历史执行不变；
8. 完全重复拒绝，语义近重复只记录和降权；
9. 接受候选能确定性物化为新的不可变 ScenarioCase，并只标记 delivered；
10. Plan、MutationProviderAttempt、Candidate、Validation、materialization、Preparation 和 Outcome 全部进入
    同一 V2 Campaign SQLite；
11. 关闭重开后恢复出相同 ready preparation，不重复调用 Provider 或扣减预算；
12. MutationPreparation 与 CandidateWork 生命周期分离，第五步前不存在 Episode work；
13. Plan 总 Token/成本预算约束所有 attempts，重试不会重新获得完整额度；
14. rejected outcome 结算成本和 invalid/operator 统计，但不推进 Coverage/Exposure/无增益窗口；
15. 错误分类满足“明确临时错误有界重试，配置/漂移/完整性/未知错误暂停”；
16. RuleBased 证据只证明工程合同，Fake HTTP 只证明协议；
17. 没有把未执行候选写成 Coverage 增长、风险命中或 Corpus 晋升。
18. 不同 feedback gap 能确定性改变 OperatorAllocation 或返回 no_compatible_operator；
19. 报告只声称结构目标保持，semantic preservation 在 Judge 冻结期间始终为 unverified。

## 16. 本步完成后的能力边界

完成第四步后，项目将具备：

```text
可信 Coverage/Corpus/Frontier 状态
→ 自动选择一个方向、父种子和 supporting execution
→ 冻结一个受控 MutationPlan
→ Provider 只为冻结 slot 生成一段候选文本
→ 宿主校验并应用结构算子
→ 物化为新的不可变 Office V2 Episode
→ MutationPreparation.ready，可恢复地等待第五步创建 CandidateWork
```

仍不具备：

- 自动启动候选 Docker Episode；
- 从新 Episode 计算真实 CoverageDelta；
- 根据真实结果晋升或淘汰种子；
- 多代自动反馈循环；
- 真实 Qwen Agent 探索能力证明；
- 真实 LLM Mutator 语义质量证明；
- Judge、黄金集、主动学习或漂移监控。

第五步才把“候选准备 → Docker 执行 → Coverage → Corpus/Frontier 结算 → 下一代”串成闭环。

## 17. 用户确认结果

用户已确认总体架构可以施工，并补充“组合算子可多 slot、反馈必须确定性选择算子、结构保持不能冒充语义
保持”三项合同。当前确认结果为：

1. 每轮只生成一个候选，不恢复 2-4 子批；
2. Scheduler 决定 Frontier、父种子、绑定和 OperatorAllocation；宿主冻结位置、资源和变化边界；LLM
   只为冻结 payload slots 生成一个候选的文本值；
3. 第四步止于 `MutationPreparation.ready`，第五步才创建 CandidateWork、执行 Agent 和计算 Coverage；
4. Canonical World 永不修改，Actor/任务/资源变化只能消费 Scheduler 的 RebindAllocation；
5. Objective 默认保持；重定向只能消费 Scheduler 的 RetargetAllocation，第四步不能自行决定；
6. 真实 delegation/grant/授权分支变化只能消费 Scheduler 的 AuthorizationBranchAllocation/RebindAllocation；
7. 所有字段必须进入 frozen/mutable/conditionally_mutable/derived 注册表；
8. planned/delivered/observed/used 继续严格分层；
9. 完全重复可拒绝，语义近重复只降权；
10. Plan 总预算约束所有重试；拒绝结算真实成本和 invalid/operator 统计但不推进 Coverage；
11. RuleBased 只做工程验收，真实语义质量留到服务器综合验收；
12. 所有状态进入现有 `V2CampaignStore`，但 Preparation 与 CandidateWork 使用不同合同；
13. Judge 与评分系统继续冻结。
14. 普通算子一个 slot，只有组合算子可包含多个协同 slot；仍然是一轮一个 Candidate；
15. FeedbackToOperatorPolicy 必须真正改变算子选择，不能只保存 feedback digest；
16. 只证明 structural objective preserved，semantic preservation 保持 unverified。
