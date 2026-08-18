# Office Workspace V2 项目剩余六步计划

状态：第一步和第二步已经完成；当前下一项是第三步 Corpus 与 RiskFrontier 详细设计；第三至第六步
继续逐步确认，不提前写死后续实现。

这份文件是完成当前项目的主施工计划，不是单独的 Stage 9 技术设计。施工必须按顺序进行，每次只细化
当前要做的一步。后续步骤可能根据前一步证据调整，不能提前写死。

Judge、黄金集、主动学习和漂移监控当前全部延后，不属于这六步。完成真实 Qwen 验收后，优先进行
项目收尾评估，而不是自动进入 Judge 开发。

## 总流程

```text
1. 把执行记录整理成覆盖率输入
→ 2. 计算行为覆盖和风险覆盖
→ 3. 建立种子库和风险调度器
→ 4. 实现受控的语义变异
→ 5. 跑通自动化多代闭环
→ 6. 使用真实 Qwen 验收
→ 项目收尾评估
```

## 第一步：把执行记录整理成覆盖率输入

状态：已完成。

### 做什么

把一次测试产生的轨迹、工具结果、权限判断、环境变化和 Oracle 结果整理成统一的
`V2CoverageInput`。

通俗理解：先把一次测试发生的全部事实整理成一张可信的“测试成绩单”。第二步只能读取这张成绩单，
不能重新从模型文本中猜测发生了什么。

### 必须区分的事实

1. 测试开始前放进去的测试条件。
2. Agent 自己执行后造成的状态变化。
3. 本次计划测试的风险目标。
4. Agent 意外产生的其他风险。
5. direct、recording、strict replay 的不同来源信息。

其中，测试初始化产生的 overlay/materialization 不能算成 Agent 的行为；模型自报的风险标签不能当成
真实风险证据。

### 数据来源

直接复用现有冻结资产：

- Stage 7/8 V2 执行闭包；
- `OracleEvidenceBundle`；
- `ScenarioOracleResult`；
- Replay Manifest、Checkpoint 和 ReplayResult；
- 现有规范摘要函数。

不建立第二套轨迹、Oracle、重放、摘要或数据库系统。

### 三条转换路径

1. `direct`：直接执行产生的可信 V2 Oracle 工件，加上容器清理结果。
2. `recording`：完整且已封印的 Manifest，加上经过摘要锁校验的 Oracle 工件。
3. `strict replay`：matched 的 ReplayResult、源 Manifest 和重建后的可信事实。

三条路径最终都转换成同一种 `V2CoverageInput`。

### 统一成绩单包含什么

- Case、Actor、Task、World、工具目录、目标目录和合同版本摘要；
- 有序工具调用、工具结果和交互事件；
- `PolicyDecision`、`StateTransition`、`ArgumentSource` 和 `OutputEvidence`；
- 初始化后的状态、Agent 状态转换和最终状态；
- submit、终止原因、录制完整性、清理结果和 Episode 有效性；
- 计划目标与意外违规；
- Oracle 事实及证据闭包。

### 摘要规则

```text
behavior_source_digest  = 行为覆盖要读取的可信执行事实
oracle_fact_digest      = 正常任务和安全事实
canonical_fact_digest   = 固定身份 + 上述两组事实
input_digest            = 包含采集来源在内的完整 V2CoverageInput
```

采集来源和 replay lineage 不进入 `canonical_fact_digest`，但必须保存在输入合同中，并受
`input_digest` 保护。

### 必须拒绝的情况

- OracleResult 不属于当前 EvidenceBundle；
- Case、Task、World、目录或版本身份不一致；
- 工具顺序、证据顺序或状态链断裂；
- Recording 不完整；
- Strict replay 不是 matched；
- 重放行为摘要或最终状态摘要不一致；
- Manifest 与重放事实的 Case、初始状态或 lineage 不一致；
- 容器清理没有确认；
- Agent 没有显式 submit；
- 持久化摘要或完整输入被篡改。

失败时不得生成部分 CoverageInput。未知异常不能被吞成临时失败或有效 Episode。

### 现有 coverage 资产处置

| 资产 | 决定 |
|---|---|
| 通用摘要、存储和反馈概念 | 后续复用 |
| V2 Oracle、执行和 replay 事实 | 直接复用 |
| 旧 `CoverageInputResolver` | 与 V2 隔离 |
| `OfficeExecutionEvidence` 和 V1 风险映射 | 不进入 V2 |
| V1 和过渡执行入口 | 保持禁用，不再扩展 |
| V1 文件、测试、镜像和历史数据 | 真实 Qwen 验收前不物理删除 |

### 产出

- `V2CoverageInput` 数据合同；
- direct/recording/strict replay 三条转换路径；
- 完整性、lineage 和篡改检查；
- coverage 资产处置表；
- 聚焦合同测试。

### 完成标准与真实结果

要求：三条路径的采集信息可以不同，但行为事实、Oracle 事实和 canonical fact digest 必须相同。

实际结果：已经完成。初始化条件与 Agent 状态变化已分离；不完整录制、重放偏离、清理失败、非 submit、
lineage 错误和证据闭包错误会被拒绝。新合同与相邻 Oracle rebuild 聚焦测试 `10 passed`，Ruff 通过。
没有运行 Docker、Ollama、真实 Qwen 或全仓测试。

## 第二步：计算行为覆盖和风险覆盖

状态：`2.0-2.8` 已完成；统一聚焦回归 `53 passed`，十项自校验证据全部通过。

详细设计见：`docs/plans/office-workspace-scenario-v2-step-02-behavior-risk-coverage.md`。

### 做什么

根据 `V2CoverageInput` 判断本次执行发现了哪些新行为、推进了哪些风险。

行为覆盖至少考虑：新工具、新工具顺序、新参数结构、新权限分支、新状态变化和新终止方式。

风险覆盖至少考虑：风险方向、intent/attempted/blocked/realized 阶段、复合目标里程碑，以及计划目标与
意外违规的区别。

### 产出

- 行为特征提取器；
- V2 风险映射；
- 覆盖增量和增长记录；
- 行为与风险关联数据。

### 完成标准

只换资源 ID 或替换一句近似文本不能制造假覆盖；真实的新路径、新权限分支和新状态必须被识别。

实际结果见第二步详细计划和
`reports/local-acceptance/office-v2-coverage-step2/step2-evidence.json`。行为归一化、4/12/23 风险目录、
计划/意外风险、共同批基线、CoverageDelta 与行为—风险关联均已冻结；尚未实现 Corpus 或调度。

## 第三步：建立种子库和风险调度器

状态：详细设计草案已写，等待用户确认后从 `3.0` 开始实现。

详细设计见：`docs/plans/office-workspace-scenario-v2-step-03-corpus-risk-frontier.md`。

### 做什么

保存有价值的历史案例，并决定下一轮优先测试哪个风险缺口、选择哪条父种子及其哪次支持执行。本阶段
每轮候选数量固定为一个，只分配单轮 token、Episode、时间和成本预算。

种子分为风险种子和探索种子。风险调度需要保证可达风险都有机会，不能让容易命中的方向长期占用全部
预算。

### 产出

- Corpus；
- RiskFrontier；
- 种子晋升规则；
- 公平调度和能量分配；
- Campaign 完成、暂停与恢复状态。

### 完成标准

系统能够解释为什么选择这个风险方向、这条父种子、这次支持执行和这个单候选预算。

## 第四步：实现受控的语义变异

状态：方向冻结，细节待第三步完成后确认。

### 做什么

代码先决定“测什么”，LLM 只负责在允许范围内生成不同表达。

调用 LLM 前必须冻结风险方向、父种子及其支持执行、允许和禁止改变的维度、变异算子、单候选预算。
每轮只生成一个候选。生成后，
宿主只检查场景边界、目标保持、可达性、数据结构和完全重复。语义近重复只能降权，不能直接断定无价值。

### 产出

- MutationIntent/MutationPlan；
- 结构化算子；
- 最小事实简报；
- singleton CandidateSet；
- 宿主校验；
- Ollama LLM Mutator。

### 完成标准

LLM 不能修改固定世界，不能静默更换目标；每个候选都有父种子、算子、简报、模型身份和校验结果等
完整血缘。

## 第五步：跑通自动化多代闭环

状态：详细计划已写入
`docs/plans/office-workspace-scenario-v2-step-05-multigeneration-feedback-loop.md`；六项施工前合同审查已
修订，等待用户确认后施工。

### 做什么

串联前四步，并先使用 Fake/RuleBased Mutator 和 scripted Agent 至少运行三代：

```text
选择风险方向
→ 选择父种子
→ 生成合法候选
→ 单个合法候选在独立 Episode 中执行
→ 计算真实覆盖增量
→ 晋升有价值种子
→ 原子结算后进入下一代
```

### 这一阶段能证明什么

- feedback 确实传到下一代；
- 调度方向能够变化；
- Corpus 能正确晋升；
- 暂停、恢复和重启不丢状态；
- strict replay 和 fork 成立。

它不能证明真实语言质量，也不能证明真实 Agent 探索能力。

### 产出与完成标准

产出确定性三代闭环、Campaign 状态机、暂停/恢复、闭环 CLI 和 JSON 覆盖报告。

连续三代必须能够自动运行，并能解释和复现每一代为什么换方向、执行了哪些候选、保留了哪些种子。

## 第六步：使用真实 Qwen 验收

状态：方向冻结，服务器施工细节在第五步通过后再写。

### 做什么

闭环机制稳定后再租 GPU 服务器：

1. 构建自包含 Agent-Qwen 镜像和离线包；
2. 本地核对镜像、模型和摘要；
3. 服务器先运行一个正常多步任务；
4. 运行正常、阻止、风险实现和复合目标等代表案例；
5. 运行至少两代真实覆盖反馈闭环；
6. 下载轨迹、Coverage、Corpus、Manifest 和重放证据。

### 这一步验证什么

- Qwen 是否真正自主调用工具；
- LLM Mutator 是否产生有意义的新表达；
- 覆盖反馈是否让真实 Agent 走出新路径；
- 多代探索是否优于重复运行固定案例。

### 完成标准

至少出现一次由新候选带来的真实行为覆盖或风险里程碑推进，并且能够 strict replay。

## 真实 Qwen 之后：项目收尾评估

真实 Qwen 验收通过后，优先完成：

- 最终 CLI 和 JSON/HTML 报告样例；
- README、HANDOFF、LOG 和 LOG-INDEX 收口；
- 一键运行和离线包说明；
- V1 和过渡资产依赖审计；
- 物理删除确认不再依赖的 V1 代码、测试、依赖和镜像；
- 必要的聚焦回归与最终完整性检查。

完成上述内容后判断项目是否可以收尾。Judge 不会因为第六步完成而自动进入开发。
