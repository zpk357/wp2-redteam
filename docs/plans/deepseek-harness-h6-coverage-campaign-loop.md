# DeepSeek Harness H6：Coverage、Campaign 与多代闭环详细计划

状态：`H6.0-H6.5 已完成；本地确定性闭环冻结，下一步 H7 真实模型/服务器确认门`

上游：`docs/plans/deepseek-harness-parallel-agent-plan.md`

## 1. 目标

让 Harness Episode 进入现有 `V2CoverageInput -> Coverage -> Corpus/RiskFrontier -> Scheduler -> Mutation ->
下一代 Episode` 闭环。H6 只接 Runtime 选择和来源身份，不修改任何搜索算法。

## 2. 固定数据流

```text
Scheduler/Mutation 生成既有 Candidate
-> Campaign 锁定 Harness TargetProfile/image/runtime identity
-> DockerOfficeV2EpisodeRunner 启动对应容器（request 不含 Runtime 选择）
-> H5 recording + Oracle
-> 现有 V2CoverageInput 完整性门
-> 现有行为/风险 CoverageDelta
-> 现有晋升、Frontier、反馈和下一代调度
-> 同一 Campaign 原子结算与恢复
```

Runtime 不参与候选内容生成，不影响风险方向选择，也不计行为新颖度。

## 3. Campaign 身份和隔离

现有 Campaign Manifest/identity 增加三个字段：

```text
producer_runtime_kind
producer_runtime_version
producer_runtime_composition_digest
```

并继续绑定已有模型、镜像、工具目录、Case、协议和算法摘要。必须删除当前
`runtime_identity_digest or model_identity_digest` 一类模型摘要兜底：模型身份不能替代 Runtime 身份。

规则：

- Campaign 创建后 Runtime 不可切换。
- Harness 与 LangGraph 使用不同 campaign_id、数据库、Corpus、Coverage store 和报告目录。
- 恢复时逐项校验 Runtime 来源字段；缺失或不符则暂停。
- 不合并两个 Runtime 的累计 Coverage；需要比较时以后读取各自报告，不改本阶段状态。

## 4. Coverage 规则

- 在调用现有 CoverageInput builder 前，Episode settlement/acquisition 门验证 recording 的 Runtime 来源字段与
  Campaign 相同；`V2CoverageInput`、`V2AcquisitionMetadata`、规范业务事实和 `canonical_fact_digest` 均不
  增加 Runtime 字段。
- Runtime kind/version/composition 和私有 Session ID 只保留在 Campaign/Episode acquisition audit。
- 行为特征只来自规范工具路径、参数形状/来源、结果、权限、状态、交互和终止。
- 风险覆盖只来自现有 Oracle/Exposure 事实。
- 同一路径只更换 Runtime 身份不得产生 CoverageDelta。

## 5. 预计修改区域

```text
src/sandbox/coverage/office_evidence.py
src/sandbox/coverage/v2_input.py                  # 原则上不改；仅复用现有 builder
src/sandbox/fuzzer/v2_identity.py
src/sandbox/fuzzer/v2_campaign_store.py
src/sandbox/fuzzer/v2_real_episode.py
src/sandbox/fuzzer/v2_real_runtime.py
src/sandbox/fuzzer/v2_cli.py                     # 仅显式 Runtime 选项
src/sandbox/fuzzer/v2_report.py                  # 仅报告 Runtime 来源字段时
tests/unit/test_deepseek_harness_coverage_input.py
tests/unit/test_office_v2_real_campaign_runtime.py
tests/integration/test_deepseek_harness_three_generation.py
```

原则上禁止修改行为/风险特征提取器、Corpus 晋升、RiskFrontier、公平调度、MutationPlan、Materializer、Judge。
如果只是为了传递 Runtime 字段而必须改这些算法模块，说明边界设计错误，应停止。

## 6. 施工步骤

### H6.0 CoverageInput 来源完整性

在 CoverageInput 构建前把 H5 的 producer Runtime 字段与 Manifest/recording/Campaign 交叉验证；不改
CoverageInput schema。添加一条“规范事实相同、只换 Runtime acquisition 身份不产生新行为特征”的断言。

### H6.1 Campaign Manifest 与恢复

扩展现有身份模型和 SQLite 持久化；迁移只针对尚未正式发布的 Harness Campaign。历史 LangGraph Campaign
不得自动冒充带新字段的 Harness Campaign。

### H6.2 Episode runner 选择

`DockerOfficeV2EpisodeRunner` 从 Campaign 锁定值选择 TargetProfile、镜像和容器启动身份；构造的
`ExecutionRequest` 不含 Runtime 字段，不能由每代 Candidate 改 Runtime。默认 CLI 仍为 LangGraph，Harness
需显式选择。

### H6.3 现有闭环串联

不改调度/变异算法，把 Harness Episode 结果交给现有 settlement、Coverage、Corpus、Finding 和 feedback。
下一代 MutationIntent 必须引用上一代 feedback digest。

### H6.4 暂停恢复

在同一三代实验中制造一次可恢复的本地 Provider/进程中断，重开后从持久状态继续；永久身份或协议错误仍暂停。

### H6.5 聚焦验收与审查

运行一个确定性三代 Harness Campaign，包含一次恢复和至少一次真实 Coverage/风险进展或方向切换。只运行相关
共享合同测试，不重跑完整 LangGraph Campaign 或 Docker 矩阵。

## 7. 验收标准

- 三代的父种子、Candidate、Episode、CoverageDelta、feedback 和下一代决定可追溯。
- Runtime 身份匹配但不进入 novelty key。
- Corpus 晋升、Frontier、公平性和完成状态使用现有算法。
- 一次中断后恢复不重复结算、不丢成本、不重复 Coverage/Finding。
- Harness 失败不会回退 LangGraph；两个 Runtime 的数据库/目录不混用。
- `runtime_identity_digest` 不再由模型 digest 兜底。

## 8. 代码审查与停止条件

检查是否出现 Harness 专用调度分支、Runtime 元数据进入特征键、Campaign 中途切换、目录或数据库混用、模型
摘要兜底、执行前晋升、暂停后重复外部副作用和重复结算。

若接入需要改变 Coverage/Corpus/Scheduler/Mutation 的决策结果，或无法用现有 EpisodeResult 完成 settlement，
立即停止并报告缺失的公共合同，而不是添加 Harness 特判。

## 9. H6.0-H6.2 实施结果（2026-08-22）

- 新增 Coverage 前置来源门：交叉校验密封 Manifest、`determinism-config.json` artifact 与 runner 接收的宿主
  预期 producer kind/version/composition；`V2CoverageInput` 和 acquisition schema 均未增加 Runtime 字段。
  H6.2 已在每次 Episode 前把该值与同一 Campaign store 的 producer 三元组及 runner 身份逐项比较。
- 同一规范事实分别绑定 LangGraph 与 Harness acquisition 身份时，canonical fact、行为事实和 Oracle 事实相同，
  只有 acquisition metadata 不同，因此 Runtime 不会制造行为新颖度。
- 现有 `campaign_runtime_identity` 表原地增加 producer 三字段；新 Campaign 原子写入完整三元组并在恢复时逐项
  校验，旧单摘要行明确为 legacy，不能自动升级成 producer-bound Campaign。
- `run_or_resume_campaign` 可绑定完整 producer 三元组；`run_or_resume_real_campaign` 删除
  `runtime_identity_digest or model_identity_digest` 隐式兜底。旧 Stage 6 路径仍可显式传 legacy 摘要，避免
  未经计划迁移历史服务器证据。
- `DockerOfficeV2EpisodeRunner` 在 CoverageInput 构建前执行来源门，Runtime 仍由宿主构造 runner 和已锁镜像，
  不进入 `ExecutionRequest`、Case、Candidate 或 MutationPlan。
- Campaign CLI 默认 LangGraph，只有显式选择才进入 Harness；Harness source lock、镜像标签、Campaign、runner
  和 recording 统一使用聚合 composition digest。

`h6-foundation-evidence.json` 只保留为 H6.0-H6.2 的历史局部证据；最终 H6 证据见
`agent_variants/deepseek_harness/h6-evidence.json`，SHA-256 为
`sha256:b05555161735f91d0efe7317354893817fb1d450013558661605bbb8ce88a585`。

## 10. H6.3-H6.5 实施结果（2026-08-22）

- Harness Episode 已复用现有单候选 settlement、Coverage、Corpus、RiskFrontier、Mutation feedback 和下一代
  决策，没有增加 Harness 专用算法分支。第二、三代决定分别绑定上一代 feedback digest。
- 聚焦恢复用例在第一代 settlement 已提交但调用方尚未确认时模拟进程退出；重开同一 SQLite 后达到三代，
  runner 只调用 3 次，settlement/receipt 各 3 条，新增执行记录 3 条，Agent token 增量精确为 51，没有重复
  结算或成本丢失。
- 最终镜像 `trace-g-deepseek-harness:h6-local` 使用聚合 composition
  `sha256:d330f6e8c3f173332e7f6267d8c2a2a0bb4d6c10cc7df9502ed91b224858bfad`。一次聚焦 Docker
  Campaign 真实创建并清理 3 个 Harness Episode 容器，得到 3 个完整 recording/Oracle/CoverageInput 和连续
  feedback；最终残留容器为 0。
- Docker 样例累计 4 个 canonical facts、8 个一级行为特征和 1 个 Corpus entry；后两代被正确标记为连续无
  增益。该确定性样例没有风险里程碑或 Finding，不能据此宣称风险覆盖推进。
- 三代达到本阶段验收预算，但持久状态仍为 `phase=baseline`、`completion_status=null`；这不是 Campaign
  生命周期终态，文档和证据不得写成 `completed` 或 `success`。
- 为让 recording 的成本合同成立，Harness 的已验证 Episode token 总量被确定性分摊到规范 actionable
  decisions，分摊总和严格等于来源总量；缺失 token 继续失败关闭。公开请求只新增宿主选择的模型 backend
  参数，LangGraph 默认仍为 Ollama，Runtime 选择仍不进入 request。

最小验证为一条三代提交后恢复测试、一条三代 Docker Campaign、相关 Ruff/Python/Node 语法和
`git diff --check`。未运行全仓、真实 Ollama/Qwen、GPU、服务器或 Judge。H7 涉及真实模型和外部成本，必须
在用户确认后开始。
