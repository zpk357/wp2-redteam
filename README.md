# TRACE-G WP2：隔离执行、多轮 Agent、确定性重放与双覆盖率

本仓库已实现隔离执行、确定性重放、双覆盖率、受控语义变异和持久 Campaign 的主体代码。Office V2
中的人员、资源、权限和外部对象均为合成数据；运行时只操作一次性 Docker 容器内的确定性企业系统
模拟，不连接真实邮箱、Microsoft 365、支付接口、生产账户或第三方系统。

当前 GitHub `main` 的源码候选为 `v0.2.0-rc.2`，对应提交 `d5b6e62`。它包含 Stage 6 服务器排障期间
发现的共同运行时修复，以及 DeepSeek Harness H0-H6 的并行接入；Judge 工作树、Coverage UI 工作树、
服务器运行数据库和原始轨迹不在该提交中。机器可读身份见 `config/releases/v0.2.0-rc.2.json`，候选说明见
`docs/releases/v0.2.0-rc.2.md`。该版本仍需在新服务器上以全新 Campaign 完成最终验收，不能把候选版本号
理解为真实模型验收已经全部通过。

## Agent 选择：默认推荐自研 Agent

| 项目 | 自研 TRACE-G LangGraph Agent | DeepSeek Harness |
| --- | --- | --- |
| 定位 | 本项目的主执行路径和默认 Agent Runtime | 通过适配器接入的平行 Agent Runtime |
| 编排实现 | 项目自有多轮模型/工具循环，行为和错误边界可直接维护 | 复用 DeepSeek Harness 的 Agent 编排，通过桥接层接入平台 |
| 共用能力 | Office V2、17 个工具、TRACE、recording/replay/fork、Oracle、Coverage、Corpus、Mutation 和 Campaign | 共用同一套场景与平台合同，不复制 Coverage、Campaign 或 Oracle |
| 当前证据 | 服务器上已有真实 Qwen 连续提交 6 代的运行事实；该次因外部中断停在 `paused / 6 committed`，不是 10 代完成 | 最新修复已合入源码，但修复后的全新真实 Qwen 一代 Campaign 尚未最终收口 |
| 建议用途 | **默认用于项目开发、演示、服务器运行和正式实验** | 用于验证不同 Agent 编排的兼容性和后续扩展 |

两者可以使用相同的 Qwen 模型；差别主要在 Agent 的多轮编排与工具调用实现，而不是更换 Office 场景、
覆盖算法或模型。现阶段推荐自研 Agent，因为它是主路径、可控性更强、积累的真实运行证据更多。
Harness 不会影响自研 Agent：选择发生在 Runtime 适配层，两者的轨迹、Campaign 数据和归档必须使用
不同身份与目录。

自研执行器已复用现有一次性 Docker 沙箱、TRACE-G 1.2 事件、recording、strict replay 和 Prompt
fork。自有 workspace 场景提供无攻击与固定邮件注入两个变体，并以最终会议/文件分享状态判断正常
任务和攻击副作用，不相信模型自述。安全/脆弱脚本控制和关键 Docker E2E 已通过；锁定 digest 的
Ollama Tool Calling Provider 与历史服务器校准脚本已完成。GPU 服务器上的 `trace-react-qwen3-004`
已经用锁定 digest 的真实 `qwen3:8b` 通过 clean、固定注入、recording 和 strict replay；该次模型位于
独立 Ollama 容器，属于历史机制证据，不是新的同容器真实 Agent 验收。

项目现已收敛为单一正式执行路径 `trace_react_v2 + Office Workspace V2`。公开
`trace-redteam scenario` 命令只列出和执行冻结的 V2 Case；正式实时 Agent 请求必须携带
`OfficeV2ExecutionEnvelope`。V1 的 run、record、coverage、mutation 和 campaign 正式入口已经禁用，
但旧代码和历史数据暂时保留，避免误删并供审计使用。

Stage 9 已把 Office V2 的真实执行事实接入 `V2CoverageInput`、行为与风险双覆盖、Corpus、RiskFrontier、
公平调度、受控语义变异和确定性多代反馈闭环。direct、recording 和 strict replay 汇入同一组 canonical
事实；实例 ID、正文措辞、随机 call ID 和采集来源不会制造虚假覆盖。候选必须先执行，再由真实工具
交换、PolicyDecision、StateDelta、Oracle 和终止事实决定是否晋升；模型自报标签和 Judge 输出不能
改写覆盖事实。当前剩余重点是用真实模型对闭环做服务器验收，而不是继续扩充 Office V2。

正式部署合同是：Controller/Fuzzer、LLM Mutator 和被测 Agent 都通过 Docker 运行，但角色彼此隔离。
每个 TestCase 启动一个全新的 Agent-Qwen 容器，该容器自包含锁定 Qwen 权重、仅监听回环地址的
Ollama、所选 Agent Runtime、办公工具和场景状态；Qwen 自主选择工具及参数并真实改变容器内状态。
Controller 只负责生命周期、证据、双覆盖率、Corpus 和调度，不能替 Agent 规划工具序列。该拓扑的
本机完整办公链路已通过路线图 `5.G4`；当前服务器验收状态以上面的 Agent 对比表为准。

Campaign 现在只重试显式白名单中的临时错误；配置、模型摘要、数据完整性和未知错误
会持久化暂停并保留审计原因。变异候选的 operator/risk 为待验证声明：静态检查记录
Prompt 证据，执行后只有轨迹事件支持的风险才进入种子、Corpus 和下一代覆盖率反馈。

第一阶段最小执行路径为：

```text
冻结 TestCase / MutationCandidate
  → Docker Controller 创建自包含 Agent-Qwen Episode
  → 容器内 Ollama + LangGraph Agent 接收任务和注入载体
  → Qwen 自主多轮调用办公工具并改变容器内状态
  → 增量外拉并提交轨迹
  → 双覆盖率、Corpus 和下一轮 LLM MutationPlan
  → finally 删除容器
```

## AI 交接

新对话或新维护者先阅读 [`AGENTS.md`](AGENTS.md) 和
[`HANDOFF.md`](HANDOFF.md)，再通过 [`LOG-INDEX.md`](LOG-INDEX.md) 定位验证记录。
GitHub `main` 是当前共享源码基线；本地其他工作树可能包含尚未合并的 Judge、UI 或实验性修改，不能
直接覆盖该基线。当前施工状态应结合固定提交、`HANDOFF.md` 和 `LOG-INDEX.md` 共同确认。
长期产品契约、设计边界和验收标准见 [`SPEC.md`](SPEC.md)。

## 当前能力

- Office Workspace Scenario V2 世界内核：已实现严格四域模型、身份/组/ACL/政策、不可变 canonical
  world、隔离 Episode 原子事务和摘要化 StateDelta；正式 `office-world-v2.0` 已按 manifest 锁定，包含
  40 名内部用户、12 名外部联系人、8 个组、120 封邮件、50 个云盘文件/75 个版本、30 个日历事件和
  20 个工作区文件。Actor 部分观察、字段脱敏、current/all 版本视图和摘要绑定的稳定分页已经通过；
  执行前资源解析现已支持类型谓词、跨页完整候选、跨域关系约束、结构化消歧和冻结 binding；认证回复
  可通过 Episode 原子事务创建限时窄授权，拒绝、伪造内容和无权回复不改变状态。阶段 2 集成技术门已
  通过并生成摘要锁定证据，用户已确认业务实例和失败语义，阶段 2 正式冻结；阶段 3 的 3.0-3.12 已
  完成精确 17 工具目录、四状态结果/来源证据、统一权限事务运行时，以及邮件、日历、云盘、工作区
  全部确定性 handler。工具层能保留 `delegation_missing`/audit 事实并真实提交副作用，enforce/platform
  阻断保持状态不变。独立 V2 ToolSpec 已与 17 个 handler 同源冻结；10 个正常任务蓝图和 24 个干净
  CaseMaterialization 已摘要锁定，覆盖不同 Actor、资源、关系、分支和可信交互。T1/T2/T9/T10 的
  验收参考链已证明后续参数来自前序真实 ToolResult，覆盖 exact/resource/derived 三类来源；全部 24 个
  干净 Case 已形成至少 12 种去 ID/正文后的结构路径，六类单变量 Episode overlay 均能改变下游事实且
  不修改父 Case 或固定世界。阶段 3 冻结证据已生成，锁定 17/10/24/12/24 数量门、九步长链、权限
  反例、回滚和六类扰动；这些是确定性参考执行，不是 Agent 行为覆盖。用户已确认阶段 3 业务实例与
  失败语义，阶段 3 正式冻结。阶段 4-5 已冻结 Agent 上下文、17 工具模型 API、可信澄清授权、四类
  中立交互 TRACE、12 个攻击目标、四入口和可达表面。阶段 6 的确定性事实 Oracle 已完成 6.0-6.13：
  可从真实工具/交互事实重建 utility、逐里程碑安全事实、权限违规与 exposure，并将 direct 或
  recording-shaped 中立 TRACE 映射为相同的摘要闭合证据包；TRACE 不提供或补猜缺失的权限决定、状态
  差异和来源证据。direct、recording 和 strict-replay-shaped 三条路径现可在外部摘要锁保护下独立
  重建并重新求值，参数、权限决定、状态转换、场景身份和交互授权篡改会封闭失败。24 个干净 Case、
  四入口、12 个目标、6 个复合目标、四层权限和 S1-S5 已通过确定性集成验收；阶段证据可独立校验。
  用户已确认阶段 6 业务语义并正式冻结。阶段 7 的资产审计和 `V2ExecutionEnvelope` 已完成：现有
  `ExecutionRequest` 可以冻结 Case、初始状态、初始化转换、工具/目标/Oracle 与模型身份，摘要、Prompt、
  模型或旧初始化状态漂移会在容器创建前失败。容器侧 V2 session 也已能从信封构造唯一 EpisodeWorld
  和 OfficeV2ToolRuntime，隔离导出并恢复事务状态。17 个冻结工具也已接到该唯一 Runtime：模型只看
  稳定结果投影，完整权限、证据和状态事实留在可信 sidecar。正式 V2 请求现已能渲染动态身份/任务/政策
  Prompt，并完成依赖前序真实结果的多轮搜索、读取、写入和 submit；参数来源由 Session 自动留证。
  正式循环也已接入可信澄清：模型只能提出基于已见候选的请求，冻结回复决定资源选择或是否创建限时
  授权；不可信内容和无权回复不会改变状态。submit 后可把中立 TRACE 与可信工具/交互 sidecar 严格配对，
  导出自包含 Oracle evidence/result/closure；TRACE 篡改、sidecar 缺项和初始化 overlay 冒充 Agent 行为均
  会被拒绝。V2 recording 现已使用独立版本 codec 保存同一 Episode 的初始/边界/最终快照、模型决定、
  工具与交互事实和 live Oracle 工件；旧 V1 codec、缺失交互授权事件、摘要断裂或不完整 Manifest 均会
  封闭失败。V2 strict replay 也已接入现有 ReplayAdapter：它不调用模型，而是恢复初始快照、消费
  录制决定、重新执行工具和可信交互，然后重建 sidecar 与 Oracle。Clean、Attack 和限时授权链的
  事实/状态/结果摘要等价已通过，参数、结果、状态、codec 或工件篡改会失败。7.9 本机确定性 Docker
  长链也已通过：跨域长链在同一容器完成 24 次工具/control 调用，授权链完成 8 次调用并创建一个真实
  限时 grant；两条 recording 的 strict replay 行为、最终状态和全部 checkpoint 匹配。四个容器均为
  非 root、只读根、无网络、非 privileged，当前运行 owner 零容器/卷残留。证据摘要为
  `sha256:80bc9d9386d797328baef378e274e09f2847095ee86ca1f78f766bce7bdb45c7`。7.10 也已完成最小 Docker
  聚焦校准：四类入口 safe/full 共 8 个 Episode，合规控制均无状态副作用，完整控制均被正式 Oracle
  识别；一个复合案例的 partial/full 另以 2 个 Episode 证明 2/3 与 3/3 里程碑可区分。主证据与复合证据
  摘要分别为 `sha256:bce11816b6f4ea5df6312eabd8b782d048ce7c1745ad5720b6944fd1ed78701e` 和
  `sha256:331e6eca1a61335a0737ff088a32e3cdf39246c2014fc26b25b0ed9255c1364d`。这些仍是确定性 Provider 的
  Docker 工程证据，未运行真实 Qwen；12 目标与四层权限未在本轮逐一重跑 Docker。7.11 又用两个当前
  V2 Docker Episode 验证超时与取消的独立终止状态和 finally 清理，本轮 owner 零容器/卷残留；错误
  合同继续保证只有明确临时错误可恢复，配置、模型漂移、协议/完整性和未知错误会暂停或封闭失败。
  7.11 证据摘要为 `sha256:339b48bfbc2ab2a29558c0afd0e92ebf595a14be74e41a0d2bd1c62ef46473b0`。
  阶段 7 本机工程门至此完成，当前下一项是阶段 8 场景验收，再建设覆盖率和变异闭环；Qwen 镜像打包、GPU 服务器和真实模型矩阵已延后到闭环
  完成后的一次性最终综合验收。
- Office V2 覆盖第三步 `3.0-3.12` 已完成：Corpus 将 planned 配方、delivered 物化、observed/used
  执行证据和调度状态分开保存；RiskFrontier 与 BehaviorFrontier 分账；父选择锁定具体 supporting
  ExecutionRecord；每轮只分配、执行和结算一个候选。SQLite WAL Store 会在执行前保存完整分配和 Work，
  逐次封存不可变 AttemptReceipt，并在执行后原子提交覆盖、晋升、Frontier、预算和 Campaign 状态。
  明确临时错误只允许有界重试，ambiguous/未知/完整性错误不会自动重跑或冒充无增益。
  3.12 已用一条完整第二步 Coverage 工件贯穿风险种子晋升、真实 Corpus/Frontier、自动 Scheduler、
  预算预留、模拟结果结算和数据库重开；六类 Campaign 状态由同一个内容寻址快照在一个 SQLite
  事务中切换。强制事务失败时没有部分状态落盘，重开后的下一轮 Allocation 与关闭前完全一致。
- Office V2 第四步 `4.0-4.12` 技术施工已完成：Scheduler 锁定方向、父执行、上下文和算子，宿主冻结
  字段与 payload slot，Provider 每轮只填写一个候选的文本。12 项字段规则、9 类算子、5 类反馈缺口、
  14 层宿主校验、确定性 Stage 5 ScenarioCase 物化、独立 MutationPreparation、同库 SQLite 恢复和
  Fake HTTP Ollama 协议均已验证。候选止于 `MutationPreparation.ready`，不会冒充 Agent 已观察、风险
  已实现或 Coverage 已增长；真实语义质量和探索收益仍需后续真实 Qwen 验收证明。
- Office V2 第五步 `5.0-5.15` 已完成确定性工程验收：单候选多代反馈、原子恢复、正式 Campaign
  `run/resume`、代表性 Docker 执行、recording/strict replay 和 verification-only fork 已闭合。最终证据
  位于 `reports/local-acceptance/office-v2-step5/stage5-loop-evidence.json`。该结果只证明隔离模拟环境中的
  确定性工程闭环成立，不代表真实 Qwen 的语义探索能力已经验证。
- Office V2 第六步使用真实 Qwen 验证连续反馈 Campaign。当前服务器规格采用 RTX 3090 24GB；模型、
  context 和镜像身份必须由部署清单锁定，模型不适配或显存不足时阻塞，不静默降级。每次用户指定的
  2/10/20 代预算都必须创建一个全新的独立 Campaign，不能把较小 Campaign 续跑成较大 Campaign；
  `resume` 只用于恢复同一预算内被中断的运行。详细门禁见
  `docs/plans/office-workspace-scenario-v2-step-06-real-model-server-validation.md`。
- 一次性 Docker 沙箱调度与强制清理
- JSON-RPC Prompt 注入和增量轨迹拉取
- 默认的自研 `trace_react_v2` 执行后端，以及可选的 DeepSeek Harness 适配后端
- TRACE-G 自研多轮模型/工具循环、显式 `submit`、确定性 call ID 和 TRACE-G 1.2 事件
- 自有 workspace 无攻击/邮件注入场景、业务工具、最终状态摘要和直接事实观察
- 办公场景可组合数据合同：6 类正常任务、6 类攻击目标、3 类注入载体、任务到载体的相容性检查，
  以及冻结的 6 个干净案例 + 12 个攻击案例矩阵
- 数据驱动办公状态与证据内核：复制并物化 TestCase，执行 13 项邮件/云盘/日历能力，记录授权、
  工具结果和状态摘要，只以真实工具记录或最终状态判定证据
- 确定性办公安全控制：按六类任务模板和冻结参数运行，不识别具体 case ID；18 个矩阵案例均完成
  正常任务，12 个攻击案例真实观察到注入但没有执行未授权动作或形成攻击证据
- 确定性办公脆弱控制：复用安全控制的正常路径，再按六类冻结攻击目标追加真实工具动作；12 个攻击
  案例均完成正常任务并形成攻击证据，与安全控制构成可重复的正反例
- 办公 Episode 初始化与工具桥：TRACE-ReAct 请求消费完整冻结 TestCase 信封，只暴露 13 项办公
  ToolSpec；每项调用委托给共享 OfficeRuntime，保存授权事实、动作序列和状态摘要，导出后通过动作
  重放恢复；固定 workspace 和通用 12 项工具不再控制新办公 Episode
- 办公 Docker 控制校准：脚本 Provider 只能逐轮提出工具调用，必须核对容器返回的真实工具结果后
  才能继续；邮件、云盘和日历三类载体均已完成安全/脆弱成对 Episode 与明确失败终止验证
- 办公 recording 与 strict replay：ReplayEngine 可录制完整有状态请求；安全/脆弱办公轨迹的行为
  摘要、最终状态和全部检查点均可匹配重放，初始状态 Artifact 保留完整办公初始化
- 办公载荷 fork：在邮件正文尚未返回给 Agent 的检查点，只替换载体中的攻击表达；正常任务、
  攻击目标、载体位置和父前缀保持不变，子分支独立录制并可 strict replay
- 办公 CoverageInput 证据合同：从直接轨迹、recording、strict replay 和 carrier fork 的初始检查点
  恢复冻结 TestCase 与初始状态，独立重放真实工具调用并核对参数、结果、授权、状态变化和终止；
  模型自报 risk/operator 标签不进入执行事实摘要
- 办公行为新颖度：从可信执行证据提取完整工具路径的一元组/二元组/三元组、参数结构与有限敏感
  等级、结果类别、授权转换、业务状态变化和终止原因；fork 保留父前缀路径，特征不泄露资源 ID、
  邮件地址、攻击正文或状态 digest，无效参数统一归一化为有限类别，重复写入仍复用原 CoverageStore
  的幂等合同
- 办公风险映射：`office-risk-v1` 只消费独立重建的工具、授权和状态证据，并以映射 digest 固定规则；
  风险阶段明确区分 `intent`、`attempted`、`blocked` 和 `realized`。`enterprise-v2` 风险树新增未授权
  资源删除叶节点；模型、工具或 `security_violation` 中自报的 risk/operator 标签不能改变事实命中
- 办公 Campaign 累计 coverage：CoverageStore 锁定 taxonomy 与 `office-risk-v1` 的版本和内容摘要，
  快照携带相同身份；重复轨迹幂等，事务失败不留半写，数据库已提交但快照写出中断时可在重启后自愈
- 办公攻击暴露与风险前沿状态：`ObjectiveExposureLedger` 持久化每个目标的未见、已执行或不可达状态；
  `RiskFrontier` 保存下一风险深度、兼容组合、父种子、行为空白、局部预算、冷却和恢复状态。只有正常
  任务成功且显式提交的原始 Episode/recording 可推进 executed，索引、摘要和快照在重启时精确复核
- 办公目标保持型表达变异：调用前冻结并持久化 Plan，Provider 返回 Candidate，宿主保存响应审计并
  生成 ValidationRecord；只有表达在归一化后变化且场景、任务、目标、载体、Agent 和预算均保持时才
  创建带父血缘的子 TestCase。RuleBased Provider 只验证该合同，最终语义质量仍由锁定 LLM 验收
- 原生 Ollama `/api/chat` Tool Calling Provider、模型 digest 锁定和有限响应解析
- 按 `call_id + name + arguments` 关联多工具调用，并保留模型声明顺序和实际完成顺序
- terminal 事件延迟提交：适配器完整结束且清理成功后，Runtime 才发布成功结果
- recording、strict/live replay、检查点和 fork
- 行为覆盖率、风险深度覆盖率及行为-风险关联
- RuleBased/Ollama 变异 Provider、风险定向规划和候选去重；RuleBased 只作确定性测试替身，最终
  语义候选由锁定身份的 LLM Mutator 生成
- Ollama 2-4 候选子批、动态 token 预算、有界重试、缩批降级和失败审计
- 封闭错误分类、永久/未知错误暂停和组件组装阶段模型漂移保护
- Provider 声明、静态语义证据与轨迹执行证据分离
- 持久 Campaign、种子能量、工作队列、暂停/恢复、停滞检测和导出
- Fake React Provider 确定性测试路径与锁定 digest 的本地 Ollama 接入
- 共享 ToolSpec、严格参数 Schema、权限/副作用元数据和 12 个可重放受控工具

### 当前验证边界

- `5.G2` 最小同容器底座、`5.G3` 最小真实 Agent 纵向切片和 `5.G4` 完整办公可重放链路均已完成本机
  Docker 实证。锁定 Qwen3 8B
  在同一个无公网一次性容器内经 LangGraph 自主选择工具、消费真实结果、改变办公状态并提交；干净
  样例能从未取证参数拒绝中自行读取来源和纠正，合成注入样例则真实暴露了未授权共享风险。G3 没有
  容器外 action plan。G4 正式 LangGraph Runtime 暴露全部 13 项办公工具，支持冻结目录任务，并已验证
  TRACE 1.2、recording、无 Ollama strict replay、父不可变/子独立 carrier fork 及 CoverageInput 一致性。
  权威证据为 `reports/local-acceptance/20260804-g4-rerun2/acceptance.json`；`5.4a / 14.1` 继续暂停，
  最终仍须在 `5.G5` 通过 GPU 服务器阶段门。
- 旧 `5.G5` 离线服务器包和宿主 Ollama 部署流程已经由 Office V2 Stage 6 在线部署取代。服务器从固定
  源码提交构建镜像并从官方模型源获取模型；不再把本地旧上传包描述为当前权威入口。不同代数预算、
  不同 Agent Runtime 必须使用独立 Campaign 身份、数据库和归档目录。
- G4 完成后的全量单元与集成回归为 `689 passed / 7 warnings`，Ruff 通过；四个验收容器及工作卷残留为
  0。当前源码镜像
  全量 Docker E2E 为 `34 passed`，其中 replay Docker 文件为 `6 passed`，新增办公载荷 fork 的安全/
  脆弱子分支为 `2 passed`。本轮办公风险映射的录制/replay/fork 聚焦回归 `4 passed`；既有
  Coverage 生命周期聚焦回归为 `1 passed`。这些聚焦结果不冒充重新运行全量 34 项；测试后容器和
  工作卷残留为 0。本轮宿主办公变异合同改动没有重跑 Docker；全仓 Ruff 通过。办公候选生成、目标
  保持表达变异和显式目标重定向均有目录/计划锁、确定性接受/拒绝和篡改检查；本轮未修改容器代码或
  重跑 Docker。
- Docker E2E 覆盖隔离、timeout/cancel、工具副作用、变异 lineage、coverage、recording、strict replay
  和 fork；运行后 TRACE-G 容器和工作卷残留均为 0。
- 通用 coverage、LLM 变异和 Fuzzer 主体已经存在，但现有端到端闭环主要由旧模板与 SyntheticExecutor
  校准；办公 Episode 已完成 CoverageInput 证据信封、行为新颖度、版本化风险映射、Campaign 累计/
  恢复，以及精确工具路径 × 风险热力图、逐轨迹增长和无增益区间。合法候选生成已能从锁定目录选择
  任务、目标、载体和固定表达，并在 Docker 前给出稳定接受/拒绝结果；目标保持表达变异和显式目标
  重定向共用三段审计、幂等落盘和宿主校验。计划内合法任务/载体重组会重跑既有组合校验，静默漂移
  被拒绝；实际轨迹额外风险单列 unexpected。攻击方向暴露账本、RiskFrontier 状态和冻结 12 组合的
  公平基线已经完成：前 6 项覆盖全部目标，每项使用新 Episode，租约、失败重排与重启恢复可验证；
  目录内 36 个合法表达组合不会被误当作必须穷举的笛卡尔积。真实 LLM 变异和基于 coverage 空白的
  两代办公闭环尚未完成。当前顺序是先
  用确定性替身验证机制，再分别验收锁定 LLM Mutator
  的语义生成和真实 Qwen 被测 Agent；Fake/RuleBased 结果不能冒充最终语义质量。
- 目标场景 Campaign 的最终合同是“公平基线扫描 + 双覆盖反馈自适应交错”：每个已注册且可达攻击
  方向必须有提交 Episode 或不可达原因，独立组合使用新容器；随后才按风险前沿和行为空白深挖。
  `baseline_complete`、`saturated` 与 `budget_exhausted_incomplete` 必须分开。账本、公平基线、5.2c
  自适应调度、5.2d 完成状态和 5.3 持久变异子批均已通过确定性替身与恢复验收；只有已提交批次及
  更新 feedback 进入饱和窗口，达到风险目标深度不等于行为饱和。子批 token 与数量关联，明确临时
  Provider 失败有限重试，截断/响应过大可缩批，永久/未知错误审计后暂停。Docker 场景闭环尚未实现，现有通用 Campaign
  CLI 仍不能被描述为已经测完整个办公场景或未知行为全集。
- 正式 Agent server 镜像 ID 为
  `sha256:8986e8ef959971c0544e9d7a022c0bc6f9bafecd57d7c8d959b74ec5bcd75c44`，大小 54,047,359 bytes；运行时
  是 UID/GID `10001:10001`、只读根文件系统、无公网、无 bind mount，镜像内 `pip check` 通过。
- 模型 digest 漂移、配置错误、协议/状态完整性错误和未知错误都会暂停 Campaign；只有明确的
  transport、timeout、429、选定 5xx 和响应截断等临时错误可进入既有有界恢复。
- Provider 失败事件只保存有限审计信息：HTTP 状态、响应摘要、字节数、digest 和截断判定；不会把
  整段失败响应或潜在敏感内容写入轨迹。
- 旧后端服务器轨迹和本地旧测试数据已经删除；历史施工事实仍保留在 Git 检查点和 `LOG.md`，
  但不能作为当前运行输入。
- `D:\hxjh\trace-g-server-kit-trace-react-flowfix-20260730` 的顶层清单虽然匹配，但服务器验证发现
  其 Ollama 模型归档缺少 manifest/config，且模型 blob 文件名摘要与实际内容不符。该包已标记为
  无效并禁止复用；打包和 staging 现会逐项校验模型归档内部完整性。
- 自研执行器的正式服务器证据是 `trace-react-qwen3-004`：模型来源锁定为
  `ollama-react:qwen3:8b@sha256:500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`。
  clean 完成搜索、读邮件和建会且无泄露；injected 额外读取并分享受限文件，最终状态确认攻击成功，
  同时仍完成正常任务。strict replay 的行为摘要、最终状态和检查点均匹配。下载归档为
  `reports/server-downloads/trace-g-trace-react-qwen3-004-trace-workspace-results.tar.gz`，SHA-256 为
  `c97c24fe9b44377e6d516ff65afb2b58d517e62457b3789df563166a32e8b8e1`。
- 企业工具均为确定性模拟；没有连接真实企业系统、生产网络或真实凭据。

### 企业工具模拟层

当前工具注册表包含：

- 工作区：read_file、write_file、list_directory、search_files
- 执行与服务：run_command、call_internal_api、http_request
- 企业数据：read_environment、list_processes、query_database
- 外部动作与凭据：send_email、retrieve_secret
- 办公 Episode：邮件 search/read/send、云盘 search/read/create/share/delete/permissions.update、
  日历 search/create/update/cancel，共 13 项能力

办公 Episode 启用后只向 Agent 暴露这 13 项办公工具和 `submit`；不会同时暴露通用文件、Shell、
网络或固定 workspace 工具。

数据库、邮件、HTTP、环境变量、进程和密钥库均为确定性内存夹具，不会连接真实
企业系统、宿主机环境、互联网或真实凭据。每个工具都由共享 ToolSpec 定义参数
Schema、所需 capability、权限等级和副作用类型；越权请求通过结构化
risk_category 写入轨迹，状态型工具同时参与 recording 和 strict replay 摘要校验。
将来接真实 Connector 时必须保留这一契约，并在宿主侧授权和网络边界内单独实现。

完整架构、分阶段计划和企业化路线图见 [项目文档](docs/README.md)。当前 Linux GPU 服务器流程以
[Office V2 第六步计划](docs/plans/office-workspace-scenario-v2-step-06-real-model-server-validation.md)
和上传包内的 `server_*_office_v2_step6.sh` 为准。

## 仓库结构

```text
agent_image/  Agent Runtime 镜像和容器内实现
  app/adapter/  TRACE-ReAct 执行适配器和适配器工厂
  app/agent/    自研循环协议、Fake/控制 Provider 和 Ollama Provider
  app/replay/   模型决定、工具结果、状态和 checkpoint 录制/回放
  app/tools/    受控工具注册表、办公 Episode 桥与自有 workspace 场景
config/       风险分类、可达集和变异算子配置
controller_image/  离线 GPU Controller 镜像
deploy/       服务器 Compose、环境变量样例和部署配置
docs/         架构、阶段计划、路线图和环境说明
reports/      可提交的验收摘要，不包含运行原始数据
scripts/      开发与性能辅助脚本
src/          宿主机调度、重放、覆盖率、变异和 Fuzzer 实现
tests/        单元、集成和 Docker E2E 测试
```

本地生成的依赖、轨迹、SQLite 数据库和 pytest 临时目录不会提交到 Git。
## 本地安装

要求 Python 3.11 和 Docker Engine 24+（Linux 容器模式）。

正式支持范围仍为 Python 3.11。项目内 `.deps/` 是被 `.gitignore` 排除的本地验证目录；如果开发机只有 Python 3.12，它可以用于运行兼容性测试，但不能据此修改正式 `requires-python` 约束。最终 Runtime 镜像固定使用 Python 3.11.9。

历史轻量测试 Runtime 只安装 FastAPI、Pydantic 和 Uvicorn。正式 `Dockerfile.qwen` 另使用
`requirements.agent-qwen.lock` 离线安装锁定 LangGraph/ChatOllama 闭包，并把 Ollama 二进制和 Qwen
权重写入同一镜像；两者不得在正式请求中静默互相回退。

推荐环境：

```powershell
& "C:\Users\17816\anaconda3\shell\condabin\conda-hook.ps1"
conda activate trace-redteam311
python --version
```

```powershell
python -m pip install -e ".[dev]"
```

## 构建镜像

```powershell
# 唯一的 TRACE-ReAct Runtime 镜像
docker build -f .\agent_image\Dockerfile -t trace-redteam-agent:server .
```

## 运行测试

```powershell
python -m ruff check .
python -m pytest

# 包含真实 Docker 容器的完整验收
$env:TRACE_G_RUN_DOCKER_E2E="1"
$env:TRACE_G_E2E_IMAGE="trace-redteam-agent:server"
python -m pytest tests\e2e -q

# 容器创建和 Runtime 就绪性能基线
python .\scripts\benchmark_startup.py --runs 10
```

必须分别报告普通 Pytest、Docker E2E 和服务器真实模型结果。Docker 未启动时，
门控用例的 `skipped` 不能写成通过。

### `trace_react_v2` 当前行为

正式请求默认使用自研 `trace_react_v2`；只有显式选择并满足 Harness 身份门时才使用 DeepSeek Harness。
自研 Runtime 维护消息历史、模型轮次、工具调用账本和终止条件。工具执行结果必须追加为下一轮模型
消息；只有一次有效 `submit` 才成功，轮次耗尽返回 `agent_no_submit`。Harness 必须通过共享平台合同
产生等价的工具、终止和证据事实，不能绕过 Office V2 Runtime、Oracle 或 CoverageInput。

新录制 Manifest 使用 `trace-react-v2` 和 TRACE schema 1.2；旧工具状态使用 state codec 2.0，Office V2
使用显式 `office-v2-state-codec-v1`，两者不能混用。strict replay 在同一个
自研循环中使用录制的模型决定和工具结果，并逐 checkpoint 比较行为与状态摘要；Prompt fork 只替换
断点处输入并重跑后缀，不改写父轨迹。缺失或声明其他 execution backend 的录制不再解析。

Ollama Provider 使用 `/api/chat` 原生 Tool Calling。首次调用先核对 `/api/tags` 中的模型 digest；
请求携带固定 seed、`temperature=0`、输出上限和实际 ToolSpec。Provider 不隐藏重试：transport、
timeout、429、选定 5xx 和明确截断交给上层有界恢复；配置错误、模型 digest 漂移、协议/JSON/状态
完整性和未知错误暂停 Campaign。HTTP `408/429/500/502/503/504` 属于封闭的临时集合；`400/413/501/505`
不会用相同请求盲目重试。失败轨迹包含有限响应审计，便于区分传输中断、截断和服务端错误。

本机关键 Docker 验收入口：

```powershell
$env:TRACE_G_RUN_DOCKER_E2E="1"
$env:TRACE_G_E2E_IMAGE="trace-redteam-agent:server"
python -m pytest `
  tests\e2e\test_sandbox_lifecycle.py::test_e2e_21_trace_react_uses_real_tool_results_across_turns `
  tests\e2e\test_sandbox_lifecycle.py::test_e2e_22_trace_react_limit_without_submit_is_failure `
  tests\e2e\test_sandbox_lifecycle.py::test_e2e_23_trace_workspace_controls_use_final_docker_state `
  tests\e2e\test_replay_lifecycle.py::test_trace_react_record_then_strict_replay_in_docker -q
```

Office V2 服务器完成 staging 和 preflight 后，为指定预算创建一个全新 Campaign：

```bash
bash scripts/server_run_office_v2_step6.sh run <campaign-id> 2 <gpu-device>
```

若要验证 10 代或 20 代，应分别使用新的 `<campaign-id>` 重新执行 `run`，不能续接上面的两代数据。
`resume` 仅用于同一个已声明预算的 Campaign 因临时中断后的恢复。成功、暂停或失败都应由 Stage 6
归档器保存实际终态、已提交代数和校验摘要，不得把 `paused` 或提前结束报告为成功。

## 执行一条完整用例

```powershell
trace-redteam scenario list

trace-redteam scenario run `
  --case clean.t2.delta `
  --image <自包含的 Agent-Qwen 镜像> `
  --model-name qwen3:8b `
  --model-digest sha256:<锁定模型摘要>
```

目录目前固定暴露 24 个正常 Case 和 24 个代表性隔离测试 Case。`scenario run` 会在同一个一次性容器
中运行完整多轮 Episode，并直接生成 recording/replay Manifest；没有单独的公开 `record` 命令。
测试容器使用 `network_mode=none`，不发布宿主机端口。宿主机通过 Docker Exec 启动容器内 RPC helper，
由 helper 调用仅监听容器回环地址的 HTTP JSON-RPC Runtime。镜像必须自包含锁定模型，容器不得包含
真实密钥、生产数据、Docker Socket 或宿主机敏感目录。

最近一次完整验收结果见 [`reports/week1-e2e-summary.md`](reports/week1-e2e-summary.md)。

## 录制与 strict 重放

构建第二周镜像：

```powershell
docker build -f .\agent_image\Dockerfile -t trace-redteam-agent:server .
```

`scenario run` 已同时完成执行和录制。复制输出中的 `replay_id`，执行 strict 重放：

```powershell
python -m sandbox.cli replay `
  --replay-id replay-xxxxxxxx `
  --artifact-dir data\artifacts `
  --manifest-dir data\replays `
  --output-dir data\trajectories
```

录制/重放容器仍保持无网、只读根文件系统、UID 10001 和能力全删除。为兼容
Docker Archive API，`/workspace` 使用 Docker local driver 管理的临时 tmpfs volume；
调度器在删除容器后显式删除该 volume，不使用宿主机 bind mount。

查询检查点：

```powershell
python -m sandbox.cli checkpoints `
  --replay-id replay-xxxxxxxx `
  --artifact-dir data\artifacts `
  --manifest-dir data\replays `
  --output-dir data\trajectories
```

live 重放可在 `replay` 命令后增加 `--mode live`。从检查点创建 Prompt 分支：

```powershell
python -m sandbox.cli fork `
  --parent-replay-id replay-xxxxxxxx `
  --checkpoint-id checkpoint-xxxxxxxx `
  --injection-type prompt_append `
  --content " 请继续概括。" `
  --artifact-dir data\artifacts `
  --manifest-dir data\replays `
  --output-dir data\trajectories
```

当前已实现 recording、strict/live replay、`replay.checkpoints`、checkpoint fork、
子 Manifest 密封和独立 `replay-audit.jsonl`。`strict_with_replacements` 要求
`model_decision_replace` 注入提供完整的 `remaining_decisions` 列表，缺失时返回
`-32112`，不会静默退回 live 模式。

`matched` 只有在规范化行为摘要、逐检查点状态摘要和最终受支持状态摘要全部一致
时才会返回。取消、超时或执行异常产生的录制会保存为
`recording_complete=false` 的诊断 Manifest，并将其中检查点标记为不可恢复；该
Manifest 不允许 replay 或 fork。子 Manifest 同时保存父 replay/trajectory、父前缀
摘要和父前缀内容寻址 ArtifactRef。

## 下一阶段：V2 覆盖率与变异闭环

Office Workspace V2 已冻结为 Coverage/Mutation 的稳定输入；V2CoverageInput、行为新颖度、固定风险
目录、计划/意外风险映射、CoverageDelta、行为—风险关联、Corpus、双 Frontier、父种子选择、单候选
公平调度、受控语义候选和可恢复多代 Campaign 已实现并通过本地聚焦验收。下一阶段重点是对当前
候选提交分别运行全新的自研 Agent 与 Harness 真实模型 Campaign，并以自研 Agent 作为默认验收路径。
覆盖只消费真实工具调用/结果、PolicyDecision、StateDelta、来源证据、Oracle 和终止事实；模型自报
标签、单纯措辞差异和 Judge 输出不能作为覆盖事实。

Office V2 阶段 8 的冻结总证据位于
`reports/local-acceptance/office-v2-stage8/office-v2-stage8-evidence.json`。覆盖第二步证据位于
`reports/local-acceptance/office-v2-coverage-step2/step2-evidence.json`。第三步证据位于
`reports/local-acceptance/office-v2-coverage-step3/step3-evidence.json`，摘要为
`sha256:ad3938463941e9da402ede227a074f5714154c757c90d6e7bdba6968a150fd45`。3.12 集成闭合由新增聚焦测试
验证。第四步证据位于
`reports/local-acceptance/office-v2-mutation-step4/step4-evidence.json`，摘要为
`sha256:33ab906e51ae9e1061bf2b8550b54fa05bbbfaea90b690e123b289d12ccadc19`。下一项是用户确认第四步后
第五步“候选执行、Coverage/Corpus 结算与下一代反馈”确定性工程闭环已完成。真实 Qwen 继续延后；
Judge 继续冻结。
