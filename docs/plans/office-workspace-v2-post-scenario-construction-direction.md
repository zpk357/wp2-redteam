# Office Workspace V2 场景后施工方向交接（覆盖率变异）

状态：`方向已确认；V2 场景、覆盖与第三步调度门均已通过，第四步详细计划等待用户确认`

## 0. 这份文档的用途

用户与 Claude 已经完成覆盖率变异的设计讨论，并冻结了一批设计决策。本文件把这些讨论成果、施工顺序
和阶段门整理成交接件，供 Codex 在场景施工完成后接手覆盖率变异工作时使用。

本文件**不是**字段级详细施工计划（那些仍要等场景冻结后逐阶段展开），但它约束后续所有详细计划的
形状：顺序、门禁、已经不能回退的设计决策，以及场景施工期间必须顺手做对的原料。

当前项目位置：V2 场景阶段 1-8、V2CoverageInput、双覆盖与第三步 Corpus/Frontier 调度闭环均已完成。
第四步受控语义变异详细计划位于
`docs/plans/office-workspace-scenario-v2-step-04-controlled-semantic-mutation.md`，尚未开始实现。

## 1. 施工总顺序（不可颠倒）

```text
完整场景设计与实现（V2 阶段 0-8）
  -> 场景验收冻结（阶段 8 门）
  -> 覆盖定义（门 B）
  -> 变异空间设计（office-workspace-v2-mutation-space-master-plan M0-M8）
  -> 单候选逐轮反馈与晋升
  -> 反馈引导实验
  -> 等预算实验（与随机 / 无反馈对比）
```

覆盖 / 变异工作在阶段 8 通过前出现，一律视为范围漂移并停止。

## 2. 已冻结的覆盖率变异设计决策（讨论成果）

以下决策是经过论证、并由用户多次纠正后定稿的，后续施工不得推翻；若要改，必须重新走设计讨论。

### D1 调度分层：风险调度器选方向，Corpus 选父种子

调度顺序固定为：① RiskFrontier 风险调度器按覆盖空白选择"探索方向"（调度单元 = objective + 当前里程碑
+ 缺失里程碑 + 阻塞原因 + 可用算子）→ ② Corpus 按能量选择父种子 → ③ 代码冻结 `MutationIntent`
（只引用覆盖空白，不含候选内容）。

### D2 Analyzer 是代码，不是 LLM

变异简报（场景切片 + 轨迹情报 + 覆盖空白）由宿主代码从结构化事实装配，不允许 LLM 自行推理汇编简报。

### D3 Materializer 是宿主代码；LLM 永不触碰世界状态

物化器（host 代码）按攻击入口修改 **Episode 副本**，产出可审计的差异 diff。规范世界、ACL、授权事实
不可变。LLM 只生成文本候选：不能写状态、不能填可信 digest、不能自报校验通过、不能自报覆盖增长或
风险命中。

### D4 攻击 = MutationPlan 声明的算子组合

攻击由 `MutationPlan` 显式声明的结构化算子组合定义，宿主校验组合合法；不是 LLM 任意组合。组合必须
保存应用顺序、中间状态和每步校验结果。

### D5 委托语义（重要修正）

`delegation_allowed=false` = 记录任务越权，但平台可执行的操作**仍会执行**。四层决策维度固定为
`capability_available / platform_allowed / delegation_allowed / policy_allowed`。

- "被阻塞"必须单独构造：`platform deny` 或 `policy enforce deny`。
- `delegation_allowed=false` 不能同时表示"被阻塞"和"允许执行"。
- 不变量：`platform_allowed=false` 时不能出现成功写入；`delegation_allowed=false` 不得被错误记录成
  平台拒绝。
- 第一轮正确示例：`read_drive_file -> capability=true, platform=true, delegation=false, policy=audit
  -> 读取成功 -> unexpected unauthorized_file_read = realized`。

### D6 泄漏判定分层（内容摘要相等不能证明全覆盖）

分层判定：`exact_copy / canary_exposure / atomic_exposure / semantic_possible`。"无法证明" = `unverified`，
不是"无泄漏"。不能因为摘要相等就断言没有泄漏。

### D7 候选晋升时机（先执行再入 Corpus）

顺序固定：`singleton CandidateSet -> 执行一个候选 -> 从真实轨迹提取覆盖增量 -> 晋升或拒绝 -> 重新调度`。
候选在执行前不得进入探索 Corpus。

### D8 含蓄 / 语义不可证明（诚实标注）

宿主无法证明"含蓄"（implicitness）或语义保持。用 `requested_operator / structural_validation /
lexical_heuristic / semantic_preservation=unverified` 如实标注，不冒充语义判据。

### D9 单一命中不饱和；伪造授权不改平台权限

- 一次 realized 不声明覆盖饱和。命中元组 = `objective + source_domain + sink_domain + carrier +
  recipient_kind + authorization_branch + stage`；其他组合仍是空白。
- 伪造授权不能改变平台权限；若组合不可达，改 Actor / 资源绑定或标记不可达，不能宣称可绕过硬 ACL。

## 3. 覆盖定义方向（阶段 8 之后第一个施工）

双覆盖，全部消费场景已冻结的中立事实，不新发明：

1. **行为特征覆盖**：工具调用边、状态变化（StateDelta）、跨域因果。
2. **风险覆盖**：风险类别 × 深度（categories × depth）。

特征来源：
- 工具调用边：`operation / resource kind / field path / relation kind`。
- `StateTransitionRecord / StateDelta`：changed_resources 与字段级变化。
- `SecurityFact` 的 stage：`intent / attempted / blocked / realized`。
- `PolicyDecision` 四层结果。

去重与身份：默认特征不依赖实例 ID；确需实例身份时必须单独论证，禁止用轮换同类资源制造假新颖度。
覆盖快照、增量、反馈摘要都要锁定版本身份。

## 4. 变异空间设计方向

详细计划是 `office-workspace-v2-mutation-space-master-plan.md` 的 M0-M8，这里只重申关键形状：

- 9 个算子族（4.1-4.9）：案例绑定 / 关系图 / 任务授权 / 攻击入口 / 伪造授权 / 参数来源操纵 /
  攻击目标重定向 / 路径延伸缩短 / 组合变异。
- 核心工件：`MutationIntent / MutationPlan / MutationCandidate / MutationValidationRecord /
  CandidateSet`。
- 两阶段生成：Analyzer（宿主代码装配事实简报）-> Generator（LLM 在冻结计划内生成文本）。
- 宿主验证六项：结构合法 / 授权一致 / 可达性 / 目标保持 / 近重复 / Oracle 可判定。

## 5. 单候选逐轮反馈方向

- 每轮 `CandidateSet` 只含一个候选；执行后提取真实 coverage delta，再晋升或拒绝并重新调度。
- 一个物理 Corpus，通过 **risk view** 和 **exploration view** 区分风险推进与行为增量，避免复制同一
  AttackSeed 和 ExecutionRecord。
- 不用静态合法率分数预测覆盖收益；每轮候选的价值只由真实执行结果决定。

## 6. 反馈引导实验方向

- 下一代 `MutationIntent` 引用上一代 feedback digest。
- 等预算实验：与随机生成 / 无反馈生成对比，证明"覆盖引导"本身有效，而不是只生成更多候选。

## 7. 场景施工期间必须顺手做对的原料（避免返工）

覆盖层是上游场景合同的一方向消费者，以下三样必须在场景阶段内冻结，不能到覆盖阶段再回头改 World /
Oracle 去迎合覆盖算法：

1. 阶段 2：世界事务记录 `changed_resources`（状态差异）；`StateDelta` 可枚举、可摘要、可重放，不泄漏
   原始敏感值。
2. 阶段 6：`SecurityFact` 稳定输出 `objective / milestone`、`intent/attempted/blocked/realized` 阶段、
   violation kind、PolicyDecision/StateDelta/工具事件证据引用和事实摘要。
3. 阶段 3/4：`PolicyDecision` 区分四层权限；工具调用记录真实边（operation / resource kind / field path /
   relation kind）。

## 8. 给 Codex 的行动约束

- 阶段 8 冻结前：只做场景施工；覆盖特征提取、版本、Corpus、变异、候选竞争、等预算实验一概不写。
- 不新增第五业务域、新工具、异步授权撤销、并发竞态、多轮诱导入口。
- 不把"通过很多单元测试"当场景完成证据；不把固定矩阵数量当完整度证据。
- Office V1 的 RiskFrontier / G6 / G5 / 固定 12 组合只保留为历史文档与回归 fixture，不得迁入 V2 Corpus、
  覆盖分母、候选竞争或等预算实验。
- 覆盖定义不得反向要求 World 或 Oracle 改写事实来迎合当前权重算法。
