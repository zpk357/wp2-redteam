# AI 工作约定

## 接手顺序

1. 先读 `HANDOFF.md`，确认已验证状态、失败边界和下一项任务。
2. 完整阅读 `SPEC.md`，确认本次工作没有偏离产品合同。
3. 再读 `LOG-INDEX.md`，只按记录标识读取 `LOG.md` 中相关条目。
4. 总施工顺序看 `docs/plans/project-roadmap.md`。
5. 修改前运行 `git status --short`。

每开始一个路线图编号任务、准备改变架构边界或宣布阶段通过时，都必须重新对照 `SPEC.md`。
SPEC 只在用户明确改变产品目标或支持边界时修改，不能为了迁就当前实现而反向改规格。

## 现场保护

- 当前工作区和本地检查点领先 GitHub，禁止 reset、checkout、rebase 或用远端文件覆盖本地内容。
- 保留用户已有改动；不得擅自提交、推送或清理未确认文件。
- `reports/server-downloads/trace-g-trace-react-qwen3-004-trace-workspace-results.tar.gz` 是当前真实
  Qwen 权威归档，不得删除或改写。
- 本机 RTX 3060 Laptop GPU 6GB 可用于开发期 Docker 同容器实证；最终服务器综合验收仍必须在服务器
  完成并锁定模型/镜像 digest，本机结果不得冒充服务器验收。
- 已确认损坏的 flowfix 离线包不得复用。

## 项目边界

- `src/sandbox/`：宿主机调度、重放、覆盖率、变异和 Fuzzer。
- `agent_image/`：一次性容器内的 TRACE-ReAct Runtime、Provider、工具、场景状态和录制。
- `controller_image/`、`deploy/` 和服务器脚本：离线 GPU 部署。
- 正式 Episode 的 Agent 镜像必须自包含锁定 Qwen 权重、回环 Ollama、LangGraph Agent、办公工具和
  场景状态；LLM Mutator 使用独立 Docker 角色，不能与被测 Agent 合并身份或共享上下文。
- 唯一执行后端是 `trace_react_v2`；不得恢复旧执行入口、依赖或旧格式只读解析。
- 录制格式固定为 `trace-react-v2`、TRACE schema 1.2；旧工具状态 codec 为 2.0，Office V2 必须使用
  `office-v2-state-codec-v1`，禁止静默互读。
- 企业工具是确定性模拟层，不得描述为已连接真实企业系统。
- 第 6-7 阶段裁判、黄金集、主动学习和漂移监控的设计见
  `docs/plans/judge-confidence-weeks-06-07-plan.md`；当前只完成计划，运行时代码仍冻结。J6-J7 只离线消费
  封存证据，不反馈 Fuzzing；所有任务、轨迹、攻击载荷和回答均是不可信 Judge 数据。前五周返工只能
  修改 Office V2 Evidence Adapter，不能把 Campaign、Coverage 或旧 ScoreResult 类型带入 Judge 核心。

## 实现原则

- 先复现并定位根因；共享问题进入共享机制，不针对单个样例反复打补丁。
- Python 正式支持 3.11；Runtime 必须保持非 root UID/GID `10001:10001`。
- 沙箱默认无公网；正式被测 Agent 只能访问同一 Episode 容器内 `127.0.0.1` 的锁定 Ollama。不得使用
  宿主/其他容器模型服务、挂载宿主模型目录，或由容器外预规划工具序列。
- 正式 Agent 循环使用当前锁定版本的 LangGraph；`trace_react_v2` 继续作为 TRACE 事件、检查点、
  recording/replay/fork 和 coverage 证据合同。不得恢复已删除的旧 LangGraph 适配器。
- 风险命中只相信工具轨迹和环境状态；模型自报 operator/risk 只是候选建议。
- 最终语义变异由锁定身份的 LLM Mutator 完成；RuleBased/Fake 只验证工程合同，不能冒充语义质量。
- MutationPlan 允许显式改变正常任务、攻击目标、载体、表达和路径，但必须记录改变/保持维度并重新
  校验；禁止静默目标漂移。
- 变异审计分三段：调用前由 Scheduler/宿主冻结 MutationPlan 的目标、位置、资源、结构算子和 payload
  slot；LLM 的 MutationCandidate 只能返回该 slot 的 generated content；宿主生成
  MutationValidationRecord 和结构物化结果。禁止让模型选择 placement/资源/算子结果或自填可信摘要、
  校验结论。
- 一个 Campaign 可以代表一次场景测试，但独立攻击组合必须使用新的 Episode。正式调度先保证所有
  场景兼容目标有提交 Episode 或不可达原因，再公平交错 RiskFrontier；禁止单一目标饥饿其他目标。
- `baseline_complete`、`saturated` 和 `budget_exhausted_incomplete` 必须分开；候选拒绝、Provider/
  基础设施错误、清理失败和 soak probe 不得推进暴露或无增益窗口。
- 最终复杂语义评分由通过黄金集门禁的 LLM-as-Judge 完成；Judge 只能解释、分级和排序执行证据，
  不能制造、删除或改写工具调用、授权、状态变化和事实覆盖。
- 确定性执行事实 oracle 是正式事实系统而不是 Fake Judge；Judge 与事实冲突时保留事实并转人工复核。
- 未分类异常暂停 Campaign，不能按临时错误吞掉。
- 不记录密码、SSH 私钥、云密钥、完整失败响应或真实凭据。

## 验证要求

- 日常修改只跑直接受影响的聚焦测试 `python -m pytest -q <test-files>` 和对应 Ruff
  `python -m ruff check <changed-files>`；文档状态调整不跑产品回归。
- 只有改动 Runtime、工具状态、TRACE、replay、coverage 事实合同或 Docker 调度时，才增加最小且有
  判定力的 Docker 代表路径。
- 完整测试 `python -m pytest` 只在路线图里程碑收尾、共享合同变化或最终封包前运行一次，不得在同一
  身份摘要下反复运行。
- 12 组合真实 Qwen、四容器 recording/replay/fork、8GB 镜像摘要和服务器包双层摘要属于昂贵门禁；
  首次冻结或相关镜像/模型/Prompt/目录/证据合同摘要变化时才重跑，其他修改复用已有权威工件。
- 无法证明改动不影响旧证据、证据损坏或相关 digest 改变时，必须升级验证，不能用“节省时间”跳过。
- Docker E2E 需显式设置 `TRACE_G_RUN_DOCKER_E2E=1`。
- 报告必须区分本机 Fake、Docker E2E、服务器真实模型和未执行检查。
- skip、代码阅读或测试数量不能冒充真实模型证据。

## 当前最高优先级

**2026-08-17 第六步计划修订并重新冻结：** 当前下一项是
`docs/plans/office-workspace-scenario-v2-step-06-real-model-server-validation.md` 的 `6.0`；计划 SHA-256 为
`sha256:9b47f4ce833ba7bc767500050861174aedf3c0962dda847d477c38feb99174a0`。正式 Agent 和
LLM Mutator 均锁定 `qwen3.5:27b-q4_K_M`，角色/镜像/Prompt/预算分离并串行使用单张 RTX 4090 24GB；
初始 context 为 8192。旧 `qwen3:8b` G5 包只作历史证据，不能改名复用。模型加载、未声明 CPU offload
或工具/结构化输出协议失败时必须阻塞，不得静默降级。48 个冻结案例只在本地做完整性门；服务器先做
最小预检和 2 代接通门，再让同一个 Campaign 按 10/20/30/50 代恢复续跑，覆盖饱和可提前停止，不单独
运行 24 clean + 24 representative 付费矩阵。第六步不修改冻结的场景、Coverage、Corpus、Scheduler、
Mutation 或 Oracle，也不进入 Judge。

**2026-08-17 第五步更新：** `docs/plans/office-workspace-scenario-v2-step-05-multigeneration-feedback-loop.md`
的 5.0-5.15 确定性工程施工和验收已完成。代表性 Docker 闭环、Office V2 recording/strict replay、
verification-only fork、正式 Campaign run/resume 和最终证据自检均已通过；证据摘要为
`sha256:2df3b5f23ecc33c14d116bd6d6efd1f9177fd5f5b0182465df8b32ee73bde5a1`。本结论只证明隔离模拟环境中的
确定性工程闭环成立；RuleBased/scripted 证据不得冒充真实 Qwen、真实语义变异质量或探索收益。

**2026-08-06 优先级更新：** 用户已暂停旧 G6/G5/5.4-5.6 路线。Office Workspace Scenario V2 阶段 1
设计已确认，阶段 2 技术门和用户业务确认门均已通过并正式冻结。阶段 3 详细计划为
`docs/plans/office-workspace-scenario-v2-stage-03-tools-causal-chains.md`；步骤 3.0-3.12 已完成边界、通用
事实、统一权限/事务运行时、四域 17 个 handler、独立 V2 ToolSpec、10 个任务蓝图、24 个干净
CaseMaterialization、全部参考执行、12 种路径形状、六类上游扰动和冻结证据；用户已确认业务实例与
失败语义，阶段 3 正式冻结。阶段 4 详细计划为
`docs/plans/office-workspace-scenario-v2-stage-04-agent-context-api.md`；4.0 已完成阶段身份、五个上游摘要、
V1 Prompt/13 工具、TRACE 1.2、允许文件和禁止依赖基线；4.1 已完成严格上下文与证据 sidecar 合同；
4.2 已完成权威身份、组织、角色/组、时间与发行者认证派生；4.3 已完成活动政策、任务委托、完整 17
工具能力片段和完整 context 组装，具体资源平台权限仍只在工具调用时计算；4.4 已完成无评测泄漏的 V2
基础规则、规范动态 renderer 和四摘要 Prompt envelope，V1 Prompt identity 不变；4.5 已完成同源 17
ToolSpec 的模型协议和封闭结果投影，模型只见 `status/data/error`，完整政策、状态转换和输出证据仍在可信
侧，联合聚焦回归 `43 passed`；4.6 已让同一 LangGraph 循环消费通用只读 session surface，默认 V1
Prompt/13 工具/recording identity 不变，V2 Prompt/17 工具/runtime 只能经构造器测试注入。首轮相邻
集合暴露并修复 surface 绕过 `ToolRecorder` 的真实回归，针对复测 `6 passed`，边界/Prompt identity
`11 passed`；4.7 已新增不含权威字段的 `request_clarification` control schema，以及按三类语义匹配
冻结 InteractionContract 的 coordinator。候选/接收方必须有 OutputEvidence，missing-value 必须来自
Task fact；零/多匹配、来源缺失、重复 pending 和 control 混批均封闭拒绝，联合聚焦 `24 passed`，来源
审计复测 `6 passed`。4.8 已完成冻结回复 rule 的确定性执行、selection/grant/no-grant/rejection、
认证 user message 回灌、同 turn 幂等和 5-tick 到期；四个 Clean Case、两个合法 grant 和两个状态不变
拒绝均有聚焦证据，核心 `17 passed`，唯一 JSON 入站修复项单独复测通过，Ruff 通过。4.9 已固定四类
中立交互 TRACE、前后摘要归属、事务后 grant 事件、无敏感回复/评测字段，以及 rejection/rollback 无
grant/transition；交互会话 `10 passed`，多轮顺序/泄漏和摘要归属单测通过，Ruff 通过。4.10 真实
surface 组合切片覆盖四个多轮 Case、两个 Actor、两条拒绝及 search→read 精确版本证据，7/7 通过；
分页、platform/enforce、未委托副作用、到期和 Prompt/V1/ToolSpec 八项矩阵回归通过。4.11 已生成
阶段 4 自校验证据，包含 17 工具、两 Actor、六交互、两 grant、两状态不变拒绝及权限/分页/版本/TRACE，
摘要已因 ToolSpec 1.1 身份传播重锁为 `sha256:022763e692d764ff0e9045da242bf1272074142e9f498afd5917e83a68788077`；一次性聚焦
冻结集 `91 passed`，相关 Ruff 与独立证据检查通过。用户已确认阶段 4 业务实例与边界，阶段 4 正式
冻结。阶段 5 详细计划为
`docs/plans/office-workspace-scenario-v2-stage-05-attack-entry-materialization.md`。5.0-5.12 已完成严格合同、
12 目标、四域可达表面、四入口、纯兼容性求解、原子 ScenarioCase 物化、24 个结构代表案例、四入口
真实可见正反事实，以及 12 个真实 ToolRuntime 完整 witness 和 6 个复合目标部分 witness。5.13 已生成
自校验阶段证据；目标目录经用户确认重冻结为 v1.1，摘要为
`sha256:b44931cdd4e7e2173771f2b356860dc937fb253ff4a0fce8cf9a2f77bb50fe04`；
技术门和用户业务确认门均已通过，阶段 5 正式冻结。阶段 6 详细计划为
`docs/plans/office-workspace-scenario-v2-stage-06-fact-oracle.md`。6.0 已新增 Oracle contract/evidence 版本身份，
重算并锁定 Stage 2-5 evidence/identity，建立六个批准模块名和禁止依赖边界；新旧边界 `5 passed`，Stage 5
evidence 独立检查、Ruff 和 diff check 通过。6.1 已新增封闭 EvidenceRef、独立 utility/security 事实、
里程碑、violation、complete/invalid-evidence 结果和自摘要合同；聚焦合同与边界 `9 passed`，Ruff 通过。
6.2-6.6 已完成 OracleEvidenceBundle、utility 目录/求值、六类 objective assertion 通用匹配器和复合
里程碑求值。A01 已证明 0/3-3/3，A05/A06/A07/A08/A12 已证明 partial/full；A06 合同冲突已按用户批准
发布 `office-v2-tools-1.1`，同步修正参数、ToolDefinition 与世界状态，并串行重建 Stage 3-5 证据。
6.7 已完成权限与独立违规事实：硬阻断、delegation+commit、audit+commit、合法委托、额外副作用及
planned/unexpected 关联聚焦 `8 passed`。6.8 已完成四入口 exposure：精确资源/版本/字段/摘要证明
observed，ArgumentSource 回指证明 used，聚焦 `13 passed`，6.4-6.8 相邻回归 `40 passed`。6.9 已补齐
原子目标通用汇总并新增 ScenarioOracle 纯组合层，聚焦 `6 passed`。6.10 已新增中立 TRACE/recording
纯映射：TRACE 只证明事件顺序和 Agent 可见输入/结果，完整 PolicyDecision、StateTransitionRecord 与
交互转换必须由可信事实提供；错序、篡改、缺项和未知 Office V2 事件封闭拒绝，模型生命周期事件可
忽略，direct 与 recording-shaped 输入生成相同 bundle。6.10 与边界聚焦 `11 passed`。6.11 已新增
外部 expected bundle digest 锁和离线重新求值入口；direct、recording、strict-replay-shaped 三路径独立
重建后 utility/security/evidence/result digest 一致，参数、decision、transition、初始/最终状态、
objective binding 和 interaction grant 篡改均稳定拒绝，错误分类不回显原始输入。6.12-6.13 已完成
Clean Case 正式入口、五故事、24 Clean Case、四入口、12 Objective、6 compound、四层权限、重放等价
和篡改拒绝的集成验收，并生成可独立 `--check` 的阶段证据，摘要为
`sha256:f6cf9bc01fe70ee2372ebae6435f9c4286fa4d456e43dc13c60e6644fff7a740`。Stage 6 聚焦集
`128 passed`，相邻边界 `17 passed`，Ruff/diff check 通过。用户已确认业务语义，阶段 6 正式冻结。
阶段 7 详细计划为 `docs/plans/office-workspace-scenario-v2-stage-07-docker-agent-integration.md`。7.0 已完成
Stage 2-6 身份重算、执行资产迁移表和边界测试；7.1 已在现有 ExecutionRequest/RPC 内冻结严格
V2ExecutionEnvelope，Case/状态/目录/Prompt/模型和旧初始化漂移均在容器创建前拒绝。7.2 已新增不经
V1 的 OfficeV2ContainerSession，唯一 EpisodeWorld/ToolRuntime、状态快照/恢复、事务链和 Episode 隔离
已通过。7.3 已复用阶段 4 Agent surface，把冻结 17 ToolSpec 接入该唯一 Runtime；模型只看稳定投影，
完整结果进入可信 sidecar。7.4 已让正式 V2 请求加载唯一 Session、渲染动态 Prompt 并运行 LangGraph
多轮工具循环；确定性 Provider 的 search/read/create/submit 与自动参数来源证据通过，非正式 runtime
不能回落旧适配器。7.5 已把冻结可信回复、资源消歧和限时授权接入同一正式 Episode；不可信内容和
无权回复保持状态不变，模型不能选择可信身份或授权时限。7.6 已把正式 TRACE 与可信工具/交互 sidecar
严格配对并导出自包含 Oracle 工件；篡改、缺项和初始化 overlay 冒充 Agent 行为均拒绝。7.7 已用显式
V2 codec 保存唯一 Episode 快照、可信工具/交互事实、边界 checkpoint、最终 Oracle 工件和成对 Manifest
引用；旧 codec、缺 grant 事件和摘要断裂均拒绝。7.8 已复用现有 ReplayAdapter 完成 V2 strict
replay：不调用模型，恢复初始快照后重新执行工具/可信交互并重建 sidecar/Oracle；Clean、Attack、
授权链等价与参数/结果/状态篡改拒绝均通过，联合回归 `138 passed`。7.9 已完成 Clean 长链、授权链、
recording/replay 与零当前 owner 残留，证据摘要为
`sha256:80bc9d9386d797328baef378e274e09f2847095ee86ca1f78f766bce7bdb45c7`。7.10 已用 10 个最小 Docker
Episode 完成四入口 safe/full 和一个复合目标 partial/full 校准；两份证据摘要为
`sha256:bce11816b6f4ea5df6312eabd8b782d048ce7c1745ad5720b6944fd1ed78701e` 与
`sha256:331e6eca1a61335a0737ff088a32e3cdf39246c2014fc26b25b0ed9255c1364d`。按用户要求未逐一重跑 12
目标和四层权限 Docker 矩阵，相关业务/Oracle 语义复用 Stage 6 冻结证据。7.11 已用两个当前 V2 Docker
Episode 验证超时、取消和 finally 零残留，并用聚焦合同门验证临时错误白名单、配置/模型漂移/协议/
完整性/未知错误暂停；证据摘要为
`sha256:339b48bfbc2ab2a29558c0afd0e92ebf595a14be74e41a0d2bd1c62ef46473b0`。阶段 7 本机工程门完成，
阶段 8 详细计划已经建立；8.0 验收入口审计和 8.1 五故事冻结已完成。验收映射摘要为
`sha256:3f2d6b706bbe5bb181b5bb79cb66e8251023ad1aedcd1a2d324ea51903c0fd6a`，故事目录摘要为
`sha256:7388af40c193fc5e478f904a222d9967e9b49384154b18fa5bc2117a69538062`。8.2 E1/E2/E3、8.3 结构门和
8.4 Docker 证据复核均已完成；结构门证据摘要为
`sha256:788019c90faacdb819f8356583bcf82c4ad56f243ae2c5320ed9c298c9b24d9e`，Docker 复核索引摘要为
`sha256:535b52f98baa22c71d96e67c1ee180e98209a40e83af7cc757175ebf10d459ab`。8.5 静态审计已完成，证据摘要为
`sha256:e98fa5900621796381060d5d76d24d8c7055d837a9e1333d9fb9594832454294`；V2 不反向 import V1，但公开
CLI、旧 CoverageInput/Campaign 和 Agent 镜像仍能到达 V1。Stage 1 的旧删除门要求先完成真实 Qwen 和
V2 CoverageInput，与用户后来明确的“场景冻结后再做真实 Qwen 和 Coverage/Mutation”顺序冲突，因此
审计状态是 blocked，未删除任何文件。当前必须由用户确认：是否先建立 V2 正式 scenario 入口，再移除
V1 的生产可达性，历史证据和通用回归继续保留。原 7.12-7.15 的 Qwen 打包、上传和服务器矩阵仍延后到
闭环完成后的一次最终综合验收。
当前不运行真实 Qwen，也不进入 Coverage、Mutation 或 LLM Judge。
不继续旧 G6/G5，不扩展 Office V1、RiskFrontier、
Campaign 恢复或覆盖率变异。下文保留旧状态作为资产与历史证据，不再代表施工顺序。

2026-08-12：用户确认解除 Stage 8.5 的旧顺序循环。公开 `trace-redteam` 现只保留
`scenario list/run`、`replay`、`checkpoints` 和 `fork`；场景目录固定为 24 个 V2 Clean Case 与 24 个
代表性 ScenarioCase。`scenario run` 只构建 `OfficeV2ExecutionEnvelope` 并进入现有 recording/replay
管线；正式实时 Agent 拒绝无 V2 信封请求。V1 的 run/record/coverage/mutation/campaign 正式入口已
禁用，但旧源代码和历史数据保留未删。V1 处置证据摘要为
`sha256:2bf4ee0f0ea8ef9b3a8789d7730d884e53822f04e4e2950b5b506bacf2fed309`；Stage 8 冻结总证据摘要为
`sha256:62dad7278ca755f800b825286bdd06d713ebd95aefd91ec4a6d9536853b2a139`。Office Workspace Scenario V2
正式冻结。Stage 9.1 的 V2CoverageInput 和覆盖第二步 `2.0-2.3` 已完成；一级/二级行为合同、证据血缘、
有界路径，以及真实工具一至三元路径、公共工具域跨域边、去值参数形状/来源链和四层权限/结果分支
均已实现；committed Agent StateDelta、可信交互和终止也已组装为完整 BehaviorProfile，初始化 overlay
不进入 Agent 状态覆盖。覆盖第二步 `2.4-2.8` 现也已完成：4 个 RiskFamily、12 个 Objective、23 个
Milestone、计划/意外风险、上下文、行为—风险关联、共同批基线和 CoverageDelta 均已通过统一聚焦
验收 `53 passed`；证据摘要为
`sha256:fa15cb1f4408de02dd8866f171def4c80597bd99c79a4d61c8f2ef60f57e3e0e`。第三步 Corpus、
RiskFrontier、种子晋升、父种子选择、公平预算和 Campaign 状态的详细设计草案已写入
`docs/plans/office-workspace-scenario-v2-step-03-corpus-risk-frontier.md`。2026-08-15 审查后又补齐独立
BehaviorFrontier、最小 MutationCapabilityManifest、单调风险事实/独立调度状态，以及 CandidateWork/
AttemptReceipt 两阶段恢复合同；完整并发租约压力验证移到第五步。2026-08-15 用户又明确：AttackSeed
只保存 planned `payload_specs`，MaterializedCandidate 保存 delivered 内容，ExecutionRecord 保存
observed/used 与具体运行，CorpusEntry 保存保留原因；父选择必须锁定 supporting ExecutionRecord 和
binding source。BehaviorFrontier 锁定具体行为锚点/缺口；`locally_saturated` 与
`local_budget_exhausted` 分离；只有明确临时错误可以有界新建 attempt，所有尝试成本累计。正式 V2
闭环每轮只生成、执行、结算一个候选，第二步 CandidateSet 接口仅以 singleton 形式复用；Actor/Task/
资源变化使用显式 RebindAllocation 和新的 comparison context。该单候选规则已同步取代 SPEC 中早期
2-4 候选子批合同。第三步 `3.0-3.12` 已完成：六组件身份锁、四类 Corpus 对象、晋升分类、双
Frontier、12 目标基线、父种子与 supporting ExecutionRecord 选择、单候选公平调度、Work/AttemptReceipt、
SQLite WAL 恢复、Campaign 状态和三轮无模型解释均已实现。Campaign 身份摘要为
`sha256:49a27697a3f6b2fb9bf6cd539871a6a29b4fbc0b2cc404d14102d3b2c8a7e06d`，阶段证据摘要为
`sha256:ad3938463941e9da402ede227a074f5714154c757c90d6e7bdba6968a150fd45`。3.12 已用完整第二步 Coverage
工件贯通风险种子、Corpus/Frontier、Scheduler、预算预留、原子 Settlement 和重开恢复；六类状态共用
一个 SQLite 事务，强制回滚无部分写入，重开后的 Allocation 相同。最新聚焦测试 `52 passed`，Ruff
通过。第三步调度闭环正式闭合。第四步受控语义变异详细计划已经写入
`docs/plans/office-workspace-scenario-v2-step-04-controlled-semantic-mutation.md`，已按七项合同审查修订并
等待用户确认；尚未开始 Mutation 运行时代码。修订后 Scheduler 独占重绑定/重定向/授权分支决定，字段
注册表封闭所有变化，MutationPreparation 与第五步 CandidateWork 分离，Plan 总预算约束全部 attempts，
拒绝只更新 preparation 统计。用户已确认并补充多 slot 组合、确定性 FeedbackToOperatorPolicy 和
`semantic_preservation=unverified` 合同。`4.0` 已完成独立 Mutation identity、Scheduler 所有的
Retarget/AuthorizationBranch/Operator allocation 与兼容 MutationGenerationAllocation 信封；历史
GenerationAllocation 和 Campaign identity 未改。第四步 `4.1-4.12` 也已完成：12 项字段规则、九类
OperatorFamily、五类 FeedbackGap、单候选 Plan/Brief、RuleBased 与 Fake HTTP Ollama 协议、独立
ProviderAttempt、14 层校验、Stage 5 ScenarioCase 复用物化、MutationPreparation 和同一
V2CampaignStore 恢复均已验收。联合聚焦集 `33 passed`，Ruff、证据自检和 diff check 通过；证据摘要为
`sha256:33ab906e51ae9e1061bf2b8550b54fa05bbbfaea90b690e123b289d12ccadc19`。第五步候选执行与反馈闭环详细
计划已写入 `docs/plans/office-workspace-scenario-v2-step-05-multigeneration-feedback-loop.md`，等待用户确认
后才可施工。计划继续使用单候选：ready preparation → 独立 Episode → ExecutionClosure → V2 Coverage
→ Finding/Seed 分流 → 全状态原子 Settlement → 下一代；Utility 完全失败的真实发现默认只保存 finding，
不自动成为父种子。第五步只用 RuleBased Mutator 与 scripted Agent 验证三代工程闭环、恢复、Docker、
strict replay/fork 和 CLI，不验证真实语义质量。真实 Qwen 验收和 Judge 仍未开始。
2026-08-17 第五步计划完成施工前合同审查修订：Provider 前预留 MutationPlan 最大预算，Preparation
终态先结算实际 Mutator 成本；rejected/paused/permanent work failure/execution 前取消使用
NonEpisodeSettlement，且不得修改 Coverage、Exposure、Corpus 或无增益窗口；`baseline_complete` 是
非终态事件并立即进入 adaptive；第五步 Fork 仅 verification-only；下一代引用最新 feedback 重新计算但
允许有理由保持；Finding 以稳定 finding_key 去重，strict replay 只更新验证状态。SPEC 已同步这些产品
合同。当前仍处于用户确认门，禁止提前开始 5.0 运行时代码。

以下为 2026-08-04 前的已验证资产和旧施工状态，仅用于迁移审计。

路线图大步骤 3 已完成：旧执行代码、依赖、镜像定义、旧轨迹和兼容解析已退役；完整非 Docker
回归 `649 passed / 34 skipped / 6 warnings`；当前源码最近一次全量 Docker E2E `34 passed`，replay Docker 文件
`6 passed`，本轮宿主办公变异子批合同改动未重跑 Docker。Ruff 通过。当时唯一
细化计划现已从工作树移除，历史内容只通过 Git 和 `LOG.md` 追溯。其授权边界、可组合合同、六类正常任务、
六类攻击目标、三类注入载体、有效组合规则、6+12 第一批矩阵和第 8 步确定性控制校准已经完成。
12 个攻击案例的安全控制结果为攻击证据假，脆弱控制共享相同正常前缀并形成攻击证据真，六类目标
均有正反例。第 9.1-9.3 步已完成初始化信封、13 项办公工具桥和三类载体的安全/脆弱 Docker 成对
Episode；第 10.1 已证明安全/脆弱办公 recording 与 strict replay 的行为、最终状态和检查点匹配；
第 10.2 已证明读取载体前可以只替换攻击表达，父 Manifest、Artifact 和前缀不变，子分支可独立录制
并 strict replay。路线图 `4.8a` / 办公计划 `11.1` 已把直接轨迹、recording、strict replay 和
carrier fork 冻结为现有 CoverageInput 的版本化办公执行证据信封；模型自报标签不进入事实摘要。
路线图 `4.8b` / 办公计划 `11.2` 已让既有行为特征提取器消费该信封，提取完整工具路径、参数/敏感
等级、结果、授权转换、状态差异和终止原因；fork 的父前缀进入跨断点二元组/三元组，非办公 profile
公式保持不变；无效参数名只能产生有限的 `<INVALID_ARGS>` 特征。相关 Docker 聚焦回归 `4 passed`
且资源零残留。路线图 `4.8c` / 办公计划 `11.3` 已完成 `office-risk-v1` 版本化风险映射：办公事实
只由冻结 TestCase、工具调用、授权和状态变化重建，显式区分 `intent`、`attempted`、`blocked` 和
`realized`；模型、工具和安全事件自报标签不能改变风险签名。风险树为 `enterprise-v2`，删除类目标
使用叶节点 `unauthorized_resource_deletion`。路线图 `4.9a` / 办公计划 `12.1` 已让现有 CoverageStore
锁定 taxonomy 与办公 mapping 的版本和内容摘要，累计快照携带相同身份；重复轨迹、事务回滚、提交后
快照中断与重启自愈均已验证。旧 schema `1.0` 因无法证明 taxonomy 内容摘要而明确拒绝，不静默迁移。
路线图 `4.9b` / 办公计划 `12.2` 已基于同一事务视图输出精确工具路径 × 执行风险热力图、风险深度
空白、逐轨迹覆盖增长和连续无增益区间；报告携带 taxonomy/mapping/scope 身份及内容摘要，并可用摘要
校验。行为侧只报告已观察路径、新增数和无增益轨迹数，不虚构未知分母百分比；当前尚无 Fuzzer 代际，
因此不得把轨迹写入顺序称作“代”。标签篡改、重启重建和持久结果不连续均有测试。历史独立 Ollama
真实 Qwen 校准已完成，但新的同容器 Qwen + LangGraph 正式验收尚未开始。路线图 `5.1a` / 办公计划 `13.1` 已完成合法办公
TestCase 候选生成：`ScenarioCampaignManifest` 分别锁定场景、任务、目标、载体和固定表达目录；生成器
复用既有组合与 TestCase 验证器，在 Docker 前返回确定性候选或稳定拒绝码，并在每次生成前复核目录
摘要。路线图 `5.1b-5.1c` / 办公计划 `13.2` 已完成：办公专用三段式变异工件在调用前
落盘并带摘要，宿主重新核对目录、父案例、组件快照与实际差异；只有表达改变且其他维度保持时才生成
目标保持子 TestCase；显式重定向必须改变目标并精确声明组件差异，合法任务/载体重组重新通过既有
组合与 TestCase 校验。实际额外风险单列 unexpected RiskHit，路径只按真实轨迹计入。RuleBased 仍
只是合同替身。路线图 `5.2a` / 办公计划 `13.3` 的状态基础已完成：`ObjectiveExposureLedger` 和
`RiskFrontier` 锁定 Campaign/目录/taxonomy/scope/mapping/Agent/预算身份，只有合法提交 Episode 可
幂等推进 executed，事务中断与重启索引/快照一致性已验证。`5.2b / 13.3` 公平基线也已完成：冻结的
12 个代表组合按目标轮转，每项使用新 Episode；持久单租约、失败重排、精确提交和重启恢复均已验证，
不扩张为目录内 36 个合法表达组合的笛卡尔穷举。`5.2c / 13.4` 自适应交错调度和 `5.2d / 14.4`
完成状态和 `5.3` MutationPlan 批预算、确定性持久子批、有界 Provider 重试、缩批降级均已完成。
`5.4a / 14.1` 已暂停；第 6-7 阶段仍冻结。
最终产品合同已澄清：第 5 阶段的 RuleBased/Fake 是测试替身，5.6 必须分别验收 LLM Mutator 和真实
被测 Agent；变异支持显式目标重定向但禁止静默漂移。第 6-7 阶段最终使用 LLM-as-Judge，当前冻结
只是施工顺序，不代表用确定性评分替代。RiskFrontier 状态、公平基线、可解释自适应交错和完成状态
以及持久变异子批恢复已经实现。用户已暂停 `5.4a / 14.1` 并修正正式部署合同。`5.G1` 已锁定
LangGraph/LangChain/`langchain-ollama` 依赖、许可证、版本、同容器进程拓扑和 TRACE 适配边界。
`5.G2` 已完成本机 Docker 同容器实证：锁定 Qwen3 8B 权重、回环 Ollama、非 root PID 1 监督、
warm-up/健康/信号清理、外部 endpoint 禁令和镜像内 hash 安装均通过；服务器验收仍属于 5.G5。
`5.G3` 已完成本机真实 Qwen Docker 纵向实证：模型自主选择工具、消费真实 ToolMessage、在状态内核
拒绝未取证参数后读取来源并纠正；合成注入样例真实产生未授权共享并由 TRACE/状态判为已实现风险。
`5.G4` 已完成本机真实 Qwen Docker 实证：正式 LangGraph Runtime 接入全部 13 项办公 ToolSpec，
覆盖冻结目录任务，逐步输出 TRACE 1.2；recording、无 Qwen strict replay、carrier fork 父不可变/
子独立，以及 CoverageInput 行为与风险一致性均已通过。权威通过证据为
`reports/local-acceptance/20260804-g4-rerun2/acceptance.json`，SHA-256
`e0157bb868575723768ad94f51b4018a7bc23547fcf86e37a569389fd69457ab`；两个更早目录是失败证据，
不得冒充通过。G5 上传前准备已经完成：独立离线包位于 `D:\hxjh\trace-g-server-kit-g5`，只包含自包含 Agent-Qwen
镜像、Controller 镜像、源码、锁和验收工具；本机最终 preflight 位于
`reports/local-acceptance/20260804-g5-preflight-final-rerun1`，live 有同容器 Ollama/GPU，strict 无
Ollama且零 GPU 请求。该证据不是服务器通过。用户已决定远程 G5 不再阻塞核心施工：当前先在本机
执行 G6 冻结 12 组合真实 Qwen 基线，随后恢复 5.4a-c、5.5，并完成 5.6a 与 5.6b 的本机小规模真实
多代闭环；之后一次上传执行远程 G5，再扩大到 5.6c。昂贵证据按 digest 复用，不重复跑。正式路径
不得调用 `OfficeControlProvider`、外部 Ollama 或容器外 action plan；第 6-7 阶段继续冻结。
