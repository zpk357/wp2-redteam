# TRACE-G WP2 产品规格

## 目的

TRACE-G WP2 面向开发和维护工具型 Agent 的安全工程团队，把一次性的随机红队测试升级为可隔离、
可重放、可度量、可持续探索且结论可复核的工程测试流水线。

系统接收测试用例或历史高价值种子，驱动 Agent 在受控环境中完成多轮模型/工具交互，记录行为和
环境状态，计算行为新颖度与风险覆盖，再用覆盖反馈引导后续语义变异。任何风险结论都必须指向
可核验的执行证据，不能只相信 Prompt 文本、模型自报标签或最终回答。

## 产品目标

- `EXE-1`：一次性 Agent 容器内由 LangGraph 控制完整模型/工具循环，工具真实返回必须进入下一轮
  Qwen 模型输入；容器外 TRACE-G 只负责编排、取证和反馈，不得代替 Agent 规划工具序列。
- `EXE-2`：成功只由一次有效 `submit` 表示；轮次耗尽、取消、超时和错误是不同终止状态。
- `EXE-3`：每个正式 Episode 的同一个一次性容器必须自包含锁定 Qwen 权重、仅监听回环地址的
  Ollama 推理服务、LangGraph Agent Runtime、办公工具和场景状态；不得调用宿主或其他容器中的模型。
- `SCN-1`：支持带身份、组织、权限、初始状态、任务、可选对抗条件、业务工具和事实 Oracle 的有状态
  场景；攻击内容载体只能是一类可选入口，不能成为所有攻击用例的必选字段。
- `SCN-2`：场景、正常任务和攻击目标可独立组合，固定 workspace 案例不能成为产品边界。
- `SCN-3`：Office V2 使用一个人工设计、版本化和内容摘要锁定的固定办公世界；每个 Episode 复制独立
  状态。覆盖反馈只能生成新的子 TestCase/Episode，不能原地修改基础世界、父案例或运行中案例。
- `SCN-4`：正常任务以业务目标依赖和分支表达，资源按业务条件从固定世界解析并在执行前冻结；内容型
  攻击位置必须有任务可达证据，不能硬绑定少量实例或在运行中追着 Agent 移动。
- `SCN-5`：Agent 只能通过身份权限、稳定分页和字段脱敏观察世界；信息不足时支持确定性合法澄清，
  只有已认证且有委托权的用户回复可以创建限时任务授权，内容声明不能改变权威授权。
- `SCN-6`：攻击目标同时支持原子和多里程碑复合状态链；Oracle 保留逐里程碑 attempted/blocked/
  realized 事实，不能把复合攻击降为一个关键工具调用。
- `SCN-7`：每个 Office V2 状态事务产生规范化 `StateTransitionRecord / StateDelta`，记录前后状态摘要、
  创建/移除资源、字段级变化和关系变化；失败回滚的差异必须为空。敏感值只记录摘要，不复制到差异事实。
- `SBX-1`：每个 Episode 在一个一次性、非 root、资源受限且默认无公网的 Docker 容器中执行。
- `TRC-1`：完整记录 Prompt、模型轮次、工具参数、工具返回、状态摘要、终止结果和清理结果。
- `RPL-1`：strict replay 使用录制的模型决定和工具返回，逐检查点验证行为与状态摘要。
- `RPL-2`：fork 能在允许的检查点替换输入，从父轨迹前缀生成独立的新执行后缀。
- `COV-1`：行为侧报告新边、新工具链、新参数形状、新状态转换和增长趋势，不虚构未知分母。
- `COV-2`：风险侧使用版本化风险树和执行证据计算固定分母覆盖率与触达深度。
- `COV-3`：Office V2 的状态差异和 Oracle 输出必须是可由未来 CoverageInput 单向消费的中立执行事实；
  具体资源 ID 只用于证据定位，不能默认成为行为新颖度维度。Office V1 固定矩阵永久排除于 V2 Corpus、
  覆盖分母、候选竞争和等预算实验。
- `MUT-1`：最终语义候选由 LLM Mutator 根据双覆盖反馈生成，抑制重复模式并优先探索低覆盖区域；
  RuleBased/Fake 只能作为确定性测试替身。
- `MUT-2`：变异可以显式改变固定世界中的 Actor/资源绑定、任务、攻击目标、攻击入口/位置、攻击表达、
  对抗初始状态 overlay 或交互路径；每次必须声明改变与保持的维度。不能修改 CanonicalOfficeWorld，
  允许目标重定向，禁止未记录的目标漂移。
- `JDG-1`：最终复杂语义评分由经过黄金集校准的 LLM-as-Judge 完成；第 6-7 阶段才建设裁判基线、
  主动学习和漂移冻结，Fake Judge 只能验证工程合同。
- `RPT-1`：报告覆盖增长、风险分布、失败状态、证据轨迹和可执行的重放命令。

## 核心领域对象

- `ScenarioTemplate`：可初始化的业务环境、工具能力、状态 Schema 和隔离要求。
- `TaskContract`：顶层用户任务、正常业务成功条件、委托范围和允许副作用；任务本身可以成为直接攻击入口。
- `TaskGoalGraph`：正常任务的业务目标、前置依赖、分支和澄清门；不规定唯一工具序列。
- `ResourceQuery / ResolvedBinding`：从固定办公世界按业务条件选择对象，并在执行前冻结解析结果与证据。
- `AttackObjective`：攻击者希望造成的状态变化或副作用、前置条件和确定性成功证据。
- `ObjectiveMilestoneGraph`：复合攻击目标的阶段依赖和逐阶段执行证据。
- `AdversarialCondition`：可选对抗条件；入口至少区分直接任务、间接内容、伪造授权和参数来源操纵。
- `ReachableAttackSurface`：由任务依赖、跨域关系、Actor 可见性和观察规则计算的内容放置集合。
- `ActorContext`：Agent 当前代表的身份、角色、组和可见组织信息。
- `ObservationPolicy / InteractionContract`：权限受限视图、稳定分页、确定性澄清和可信授权更新。
- `ScenarioOracle`：从权限决定、工具事实和初始/最终状态重建 utility 与 security 事实。
- `StateTransitionRecord / StateDelta`：从已提交事务记录前后摘要、字段路径、资源/关系创建移除和证据
  摘要；不等同于 Fuzzer 的 MutationPlan 或 MutationValidationRecord。
- `MutationPlan`：调用 LLM 前冻结的请求意图；包含父 `TestCase` ID/内容摘要、输入 coverage feedback
  摘要、目标覆盖空白、请求改变/保持维度、计划前后目录组件 ID、期望路径、算子、seed、预算和
  LLM Mutator/Prompt/Schema 身份。
- `MutationCandidate`：LLM 返回并规范化后的候选；包含计划引用、子 `TestCase`/内容摘要、实际字段
  差异、实际前后组件 ID、新攻击表达摘要和模型声明的期望路径。
- `MutationValidationRecord`：候选响应审计、静态校验/拒绝原因及实际差异与计划的一致性结果。
- `TestCase`：场景世界、Actor、任务、可选对抗条件、攻击目标和模型配置的冻结组合。
- `Episode`：一个容器中的完整多轮模型/工具执行及前后状态。
- `ObjectiveExposureLedger`：记录每个已注册攻击目标是否已有提交 Episode，或为何不可达/不兼容；它
  提供有限攻击目录的覆盖底线，不等同于风险覆盖或开放行为覆盖。
- `RiskFrontier`：按场景、风险类别和下一执行证据深度组织探索空白，挂接兼容的任务、攻击目标、
  攻击入口、父种子、行为路径空白、局部预算和饱和状态。
- `Finding`：由已提交轨迹和执行事实支持、可由 strict replay 或 fork 复核的安全发现。

攻击不是任意文本自由生成。Campaign Manifest 必须分别锁定场景世界、Actor/权限、`TaskContract`、
`AttackObjective`、`AdversarialCondition` 目录的版本和内容 digest。可执行攻击用例的全部目录组件必须
来自这些冻结目录，场景具备所需工具与前置状态，并存在可观察的成功条件。LLM 运行时提出的新组件
定义只能先保存为待注册研究样本，经独立审核、证据编译和目录版本发布后才能执行。缺少执行证据的
候选可以保存为研究样本，但不能进入事实风险覆盖或下一代调度反馈。

## 主链路

```text
ScenarioTemplate + ActorContext + TaskContract + optional AdversarialCondition + AttackObjective
  -> 生成合法 TestCase 基线并建立攻击目标暴露账本/风险前沿
  -> 先为每个可达攻击目标提供最低执行机会
  -> 调度器创建自包含 Qwen + LangGraph + 办公环境的一次性 Docker 容器
  -> 容器内 Ollama 启动并校验模型，LangGraph Agent 初始化场景并执行多轮模型/工具循环
  -> Tracer 提交模型、工具、状态、终止和清理事件
  -> 执行证据确认正常任务结果与攻击副作用
  -> 提取行为新颖度和风险覆盖
  -> Corpus 保留高价值种子
  -> 公平约束下针对风险前沿与行为空白交错生成下一代
  -> 重复直到明确完成、预算不足、取消或显式失败条件触发
```

一个 Campaign 可以代表“一次场景测试”，但内部必须执行多个相互隔离的 TestCase/Episode。除非
ScenarioTemplate 显式定义并验证复合攻击链，否则不得在前一个攻击已经改变的容器状态上继续测试下一
攻击目标；否则副作用污染会破坏归因、重放和公平比较。

## 执行合同

### 单一执行与证据合同

所有新执行、录制、重放和 fork 统一使用 `trace_react_v2` 证据与重放合同。正式被测 Agent 的运行时
实现统一为 LangGraph；项目不恢复旧 LangGraph 适配器，而是基于当前锁定版本重新接入。请求不指定
backend 时默认使用 `trace_react_v2`；显式旧值或未知值必须在协议入口拒绝，录制的 determinism 配置
缺失或不是 `trace_react_v2` 时必须在重放准备阶段拒绝。LangGraph 的私有状态对象不是长期协议，必须
在边界处转换为 TRACE schema 1.2 事件、检查点和状态摘要。

`trace_react_v2` 必须满足：

1. 容器内 LangGraph 持有 Agent 循环控制权，模型不能通过普通文本结束 Episode；容器外不得预生成
   或逐轮指定工具调用。
2. 每个模型工具调用都有稳定 `call_id`，工具参数先通过结构化 Schema 验证。
3. 工具的真实结构化返回追加到消息历史后，模型才能决定下一步。
4. `submit` 必须独占一个工具调用批次且只能成功一次。
5. 模型未在预算内提交时返回 `agent_no_submit`，不得伪装成正常完成。
6. 同一 Episode 的场景状态和所有业务工具持续存在于同一个容器中。
7. terminal 事件只在执行收尾和所需清理成功后发布。

### 模型 Provider

本地确定性回归可以使用 Fake React Provider；正式被测 Agent 使用同一 Episode 容器内的 Ollama
`/api/chat` Tool Calling Provider，endpoint 固定为容器回环地址。真实模型运行前必须锁定模型名称、
模型内容 SHA-256 digest、Agent 镜像 digest、LangGraph/Provider 版本和 Prompt digest；运行中发现
任一身份漂移立即暂停 Campaign。

Office Workspace V2 第六步服务器验收冻结使用 `qwen3.5:27b-q4_K_M`：被测 Agent 和 LLM Mutator
可以使用同一上游权重内容，但必须分别锁定角色、镜像、Prompt、Provider、推理配置和预算，并串行占用
唯一 GPU。`qwen3:8b` 只保留为历史或低成本校准证据，不进入第六步正式能力结论。模型在 8192 context
下无法完整驻留、发生未声明 CPU offload 或协议不兼容时必须阻塞验收，不得静默降级到其他模型。

Fake React Provider、RuleBased Mutation Provider 和未来的 Fake Judge Provider 都是测试替身：它们
用于验证协议、批次、错误、恢复和可复现性，不能通过最终语义质量或裁判质量验收。最终被测 Agent、
LLM Mutator 和 LLM-as-Judge 是三种独立角色；各自的模型、Prompt、配置和 digest 必须分开锁定与审计，
不得用一个角色的测试结果冒充另一个角色的能力。

确定性执行事实 oracle 不是 Fake Judge：它从工具轨迹、授权结果和环境状态重建攻击尝试、拦截与
副作用，是正式事实系统。它可以约束和校验 LLM Judge，但不承担最终复杂语义评分。

Provider 只允许对明确临时错误做有界恢复：transport、timeout、HTTP 408/429、选定 5xx 和有证据的
响应截断。配置错误、协议错误、无效结构、模型漂移、数据完整性错误和未知异常不得重试吞掉。
失败审计只保存 HTTP 状态、有限摘要、响应字节数、digest 和截断判定，不保存整段潜在敏感响应。

### 部署角色

- 可信 Controller/Fuzzer 自身以 Docker 容器运行，拥有 Episode 生命周期、coverage、Corpus、调度和
  工件存储；只有它可以持有 Docker Socket，且不得把 Socket 传给被测 Agent。
- 每个 `TestCase` 启动一个全新的 Agent-Qwen 容器。Qwen 自主选择工具、参数和提交时机，LangGraph
  只提供模型-工具编排；工具真实返回和场景状态变化必须发生并持续保存在该容器内。
- LLM Mutator 是与被测 Agent 分离的 Docker 角色，接收冻结 MutationPlan 和双覆盖反馈，输出候选；
  Controller 在宿主可信边界重新校验后，才为候选创建新的 Agent-Qwen Episode。
- 宿主机只提供 Docker Engine、NVIDIA 驱动/GPU 和持久结果存储。正式运行不依赖宿主 Python、宿主
  Ollama、宿主模型目录、外部模型 endpoint 或公网服务。

## 沙箱边界

- TRACE-G 调度器是容器生命周期的唯一所有者。
- Agent Runtime 使用 UID/GID `10001:10001`、只读根文件系统、能力删除和受控工作目录。
- Agent 容器不挂载 Docker Socket，不挂载宿主业务目录，不直接访问公网。
- 正式 Agent 镜像内同时包含锁定 Qwen 权重、Ollama、LangGraph Runtime、工具和办公环境；Ollama 只
  监听该容器的 `127.0.0.1`，不得通过 Docker 网络或宿主端口向其他主体提供推理。
- 正式 Agent 容器不得挂载宿主模型目录，也不得配置外部模型 endpoint；模型权重随镜像构建并由
  模型 digest 与镜像 digest 双重锁定。
- CPU、内存、PID、超时和并发资源必须由宿主调度器限制。
- 多进程启动、模型 warm-up、健康检查、超时/取消信号传播和 GPU 进程退出必须可观察；任一残留都按
  清理失败处理。
- Episode 结束后必须删除容器和临时卷；清理失败是系统性失败，不能报告测试成功。
- 普通 Docker 容器不是虚拟机级安全边界，生产部署仍需宿主机与账户隔离。

## 场景与安全事实

场景必须把任务、攻击目标和攻击入口分开表示。Campaign 可以显式改变任务、攻击目标、入口、表达和
交互顺序；局部目标保持型变异用于控制变量，目标重定向型变异用于探索新的风险空白。任何变异都必须
记录改变与保持的维度。任一目录组件发生变化时，新组件必须来自 Manifest 锁定目录；攻击目标变化还
必须重新验证场景前置条件、授权关系、入口可达性和独立成功证据。禁止只改文本或自报标签却不更新
lineage、组件身份与成功标准的静默漂移。
MutationPlan 可以声明希望探索的交互路径，但实际路径及其是否新颖只能由提交后的真实工具轨迹证明。

攻击成功应优先由最终环境状态确认，例如：文件是否分享、权限是否改变、邮件是否发出、转账对象和
金额是否改变。执行中意外观察到的风险照常进入事实风险命中，但必须与计划攻击目标的完成结果分开
记录，不能反向改写父案例或 MutationPlan 的声明目标。

模型生成 Prompt 时自报的 operator/risk 标签仅是候选建议。数据流固定为：

```text
Prompt + 自报标签
  -> 独立静态语义检查
  -> 沙箱执行
  -> 根据真实工具轨迹和环境状态确认 operator/risk
  -> 只有执行证据成立才进入事实覆盖和调度反馈
```

当前确定性企业工具是模拟夹具，不连接真实企业系统、生产网络或真实凭据。未来接入真实 Connector
时必须保持相同 ToolSpec、权限、副作用、轨迹和宿主授权边界。

## 轨迹、录制与重放

已提交轨迹是覆盖率、缺陷报告和回归判定的事实入口。事件至少包含：

- `execution_started`
- `scenario_initialized`
- `model_start` / `model_end`
- `tool_call` / `security_violation` / `tool_result`
- `scenario_state_observed`
- `agent_submit`
- `execution_finished` 或明确失败终止

新录制格式固定为 `trace-react-v2`、TRACE schema `1.2` 和 state codec `2.0`。Manifest、Artifact 和
状态摘要必须经过内容寻址与完整性校验。其他 backend 的录制不兼容、不自动迁移，也不允许以缺失
backend 字段降级。

strict replay 不重新调用真实模型或外部系统，而是使用录制的模型决定和工具返回，验证 TRACE-G
状态机、轨迹、检查点和判定逻辑。重新调用真实模型属于 live execution，只能报告路径/结果一致率，
不能宣称 100% 确定性。

Finding 必须使用不含 acquisition metadata 的稳定 `finding_key` 去重，并区分
`recorded → replay_required → replay_confirmed / replay_failed`。strict replay 只能更新同一 Finding 的
验证状态，不得创建新 Generation、重复 Finding 或新增 Coverage。

fork 在断点前复用已验证父前缀，在断点注入新 Prompt 或支持的替换，从断点后执行新分支；父轨迹
必须保持不可变。缺少快照、Artifact、允许的注入类型或完整性证明时必须失败。
仅用于重放验证的 fork 不得新增 Campaign Coverage、Finding、Exposure 或 Generation。若 fork 结果要
进入正式 Campaign，必须先建立新的子 Campaign 或 Generation，并完整经过 Allocation、Preparation、
Handoff、Work 和 Settlement，禁止把验证分支直接写入父 Campaign。

## 覆盖率合同

### 行为档案新颖度

Agent 的所有可能行为没有可枚举分母，因此行为侧不声明“已覆盖全部行为的百分比”。系统报告：

- 工具 unigram/bigram/trigram 与调用先后边
- 参数结构和敏感等级
- 工具结果类别
- 权限与安全状态转换
- 环境状态差异和终止类型
- 新特征数量、增长曲线和连续无新增的语料饱和度

### 风险维度覆盖率

风险分类树具有版本化固定分母，可以报告分类覆盖率和触达深度。Prompt 关键词只构成意图证据；
工具调用、策略事件和环境副作用才构成行为/影响证据。只有后两类事实证据可进入 Fuzzer 调度。

风险树、可达集、归一化版本和摘要必须锁入 Campaign Manifest。分类树变化后不能把新旧百分比直接
比较。

## Campaign 公平性与完成语义

Campaign 先建立有限目录的覆盖底线，再进入开放空间的自适应 Fuzzing：

1. 基线阶段为每个已注册且与场景兼容的 `AttackObjective` 安排至少一个合法、已提交的 Episode；没有
   合法组合时保存稳定的 `unreachable_or_incompatible` 原因。每个可达的 in-scope 风险类别也必须有
   初始合法种子或不可达证据。
2. 基线完成后，Scheduler 按单候选代际交错选择 `RiskFrontier`。风险深度空白、稀有行为、新路径-风险关联、
   欠采样程度和等待时间提高优先级；重复、连续无增益、高无效候选率和高单位成本降低优先级。
3. 公平约束高于软评分：必须设置每个前沿的最大连续份额、饥饿上限和保留探索预算。不得让单一容易
   产生覆盖的目标耗尽 Campaign，也不得让难目标阻塞其他目标。
4. 达到风险目标只降低优先级，不等同于行为探索完成。局部无增益前沿进入冷却；新 Corpus 种子、
   unexpected RiskHit 或新的路径-风险关联可以重新激活它。

攻击目标暴露和风险/行为覆盖必须分开报告。有限攻击目录可以达到 `baseline_complete`；风险前沿可以
达到目标深度；行为档案没有可枚举分母，只能报告增长与局部/全局饱和，不能声称“全部行为已测完”。

Campaign 生命周期至少区分：

- `baseline_complete`：非终态事件；表示每个场景兼容攻击目标均有已提交 Episode，或有可审计的不可达/
  不兼容原因。记录该事件后 `phase` 必须进入 `adaptive`，Scheduler 继续工作。
- `saturated`：已经 baseline complete，且所有可达前沿在最小有效观察窗口后均连续无新增行为、无风险
  执行深度提升、无新路径-风险单元格。
- `budget_exhausted_incomplete`：时间、Episode、token 或成本预算耗尽，但基线或可达前沿仍未满足。
- `paused` / `cancelled`：分别保留系统失败暂停与用户显式取消语义。

真正终态只有 `saturated`、`budget_exhausted_incomplete`、`paused` 和 `cancelled`；
`baseline_complete` 不得阻止创建第一轮 adaptive Allocation。

只有已提交的有效 Episode 可以推进暴露账本和饱和窗口。候选拒绝、LLM 无效输出、Provider 重试、
基础设施错误、清理失败和 soak probe 不得计为执行暴露或无增益观察。

## 变异与灰盒闭环

变异器由 TRACE-G 自研并围绕覆盖率反馈研究，不以薄适配层替代核心算法。最终语义生成必须由锁定
身份的 LLM Mutator 完成；RuleBased/Fake Provider 只用于验证反馈传递、Schema、批次、血缘、错误和
恢复，不得被描述为真实语义探索质量。

调用前先持久化并摘要 `MutationPlan`，由确定性 Scheduler 锁定父案例、feedback、请求改变/保持维度、
计划前后目录组件、期望路径、算子、seed、预算和 LLM 请求身份；LLM 不能自行扩大计划目标。调用后将
实际输出保存为 `MutationCandidate`，再把实际字段差异、新表达摘要、Provider 响应审计、静态校验与
拒绝原因写入 `MutationValidationRecord`。只有实际差异完全符合计划且组合校验通过的候选才能生成子
TestCase。目标保持型与目标重定向型变异必须并存；重定向只能选择 Manifest 锁定目录中的组件。

调用 Mutator 前必须从 Campaign 总预算原子预留该 `MutationPlan` 的最大 Token/成本；Preparation 到达
任一终态后，按实际成本结算并释放余额。没有产生有效 Episode 的终态也必须用不可变
`NonEpisodeSettlement` 关闭本代，至少覆盖 preparation rejected/paused、work permanent failure 和
execution 前取消。它只能更新成本、预算、invalid/operator 统计、调度决策次数和必要的暂停/取消状态，
不得修改 Coverage、Exposure、Corpus 或无增益窗口。

`generation_index` 表示已经由 CandidateSettlement 或 NonEpisodeSettlement 关闭的 Allocation 数量；
`valid_committed_episodes` 和连续无增益窗口只由有效 Episode 更新。NonEpisodeSettlement 可以推进前者，
不能伪造后两者。

调度器奖励新增行为特征和风险执行证据，惩罚近重复表达、高频工具链和长期无新增区域。正式 V2 闭环
每轮只请求、执行和结算一个候选；该候选提交真实反馈后，Scheduler 才能开始下一轮。临时失败只允许
按封闭错误白名单和固定上限创建新的 attempt，每次尝试的真实成本均须累计；模糊、永久或未知错误必须
暂停 Campaign。Scheduler 决策必须保存前沿、优先级分量、公平欠账、父种子及其支持执行、预算和
tie-break 证据。

下一代决策必须引用最新 `feedback_digest` 并重新计算；计算结果可以改变，也可以因最优选择仍相同而
保持。系统必须保存改变或保持的原因，禁止为了证明反馈存在而强迫无意义切换。

## 裁判与评分合同

LLM-as-Judge 用于解释执行证据的复杂语义：正常任务质量、攻击目标语义一致性、策略违规、影响严重度、
可利用性、最终回答风险和是否需要人工复核。Judge 输出必须结构化，引用具体证据事件，并锁定模型、
Prompt、Rubric、Schema 和黄金集版本。任务、轨迹、攻击载荷和最终回答均是不可信证据数据，不是给
Judge 的指令；Judge 不注册工具、不能访问公网或写业务文件，只能输出有限枚举、短理由和证据 ID。
结构合法但引用不存在、引用不属于输入闭包或结论与确定性事实冲突时，不得生成有效正式评分。

工具调用、授权结果和环境状态变化仍是不可由 Judge 覆盖的执行事实。Judge 可以对事实的语义价值评分，
但不能在没有证据时制造攻击成功，也不能删除已经成立的事实风险。第 6 阶段的 Fake Judge 只验证输入
构建、Schema、存储和失败路径；最终评分质量必须由真实 LLM Judge 在冻结黄金集上校准。第 7 阶段使用
模型分歧、评分方差、规则冲突、覆盖新颖度和人工回流监控漂移，不得只相信模型自报 confidence。

J6-J7 的 Judge 结果只用于 Finding/报告排序、解释和人工复核，不得反向改变 Coverage、Corpus、
Seed Energy、Scheduler、Mutation feedback 或 Campaign 执行。未来若要把 Judge 作为 Fuzzing 次级信号，
必须另立 RFC，重新冻结公平性、漂移和降级合同；不得把该能力解释为 J7 的默认组成部分。Judge 与执行
事实直接冲突时保留执行事实，将该响应标记为 `validation_failed` 并进入人工复核，不生成正式评分。
漂移监控达到锁定最小统计窗口且越过冻结阈值时自动冻结结论发布；样本不足只告警，不宣称已确认漂移。
恢复必须经人工确认或重新校准。
纯执行证据驱动的 Fuzzing 不受 Judge 可用性影响，原始轨迹与覆盖数据不得删除。

## 失败状态合同

- `case_failure`：Agent 未 submit、submit 无效或正常任务失败；该用例结束，Campaign 可继续。
- `transient_infrastructure`：白名单内 transport、timeout、限流、选定 5xx 或截断；允许有界恢复。
- `cancelled`：显式取消；保持取消语义。
- `configuration_error`：配置、场景、Provider 或风险树不可用；暂停 Campaign。
- `model_digest_mismatch`：模型身份漂移；暂停 Campaign。
- `data_integrity_error`：轨迹、Manifest、Artifact、协议或状态摘要不一致；暂停 Campaign。
- `systemic_infrastructure_failure`：永久基础设施或清理失败；暂停 Campaign。
- `unclassified_error`：未分类异常；暂停并向调用方失败返回，禁止按临时错误处理。

## 阶段门

1. 确定性沙箱、轨迹、strict replay 和 fork。
2. 单一 TRACE-ReAct 多轮执行器与固定有状态案例校准。
3. 场景世界、身份权限、任务、攻击目标和多类攻击入口泛化。
4. 行为新颖度与风险覆盖的证据化量化。
5. 覆盖率反馈语义变异和持久灰盒闭环。
6. LLM-as-Judge 基线与 50-80 条人工黄金集。
7. 主动学习采样、黄金探针、漂移告警和结论冻结。
8. 统一 CLI、JSON/HTML 报告、CI/CD 和真实业务 Agent 压测。

前五阶段稳定前只允许设计第 6-7 阶段的场景无关合同、安全边界和验收计划，不实现通用评分器、
rubric、黄金集运行库、置信度、漂移状态机或主动学习接口。固定案例的 utility/security 结果只使用
确定性状态断言，不属于 LLM 裁判。前五阶段返工只能影响 Evidence Adapter，不得反向改变 Judge 核心合同。

第 5 阶段存在强制前置门：先完成“同一一次性容器内 Qwen + Ollama + LangGraph Agent + 办公环境”的
真实 Agent 纵向闭环和服务器隔离验收，再恢复第一代 coverage/Corpus 串联。外部 Ollama、脚本
`OfficeControlProvider`、Fake/RuleBased Agent 或容器外预规划工具序列只能作为校准证据，不能通过
该门，也不能被写入正式办公 Campaign 的真实 Agent 结论。

## 非目标

- 不开发通用 Docker、Kubernetes 或云资源管理平台。
- 不保留多个执行后端、第二套轨迹数据库或旧格式只读兼容层。
- 不把模型自报标签、Prompt 关键词或最终文本当作攻击成功事实。
- 不把行为新颖度描述为全部行为覆盖百分比。
- 不把攻击目录 baseline complete 描述为全部语义表达或全部行为路径已覆盖。
- 不通过串行深挖单一攻击方向或穷举任务/目标/入口的完整笛卡尔积来冒充有效 Fuzzing。
- 不把 RuleBased/Fake Mutator 的结构变体描述为最终 LLM 语义变异质量。
- 不把外部模型服务、脚本工具计划或确定性控制 Provider 描述为正式被测 Agent。
- 不把 Fake Judge 或未通过黄金集门禁的 LLM Judge 结果描述为可信最终评分。
- 不要求所有变异永远保持同一攻击目标；只禁止未声明、未重新校验的目标漂移。
- 不声称真实 LLM 重新执行可以 100% 复现。
- 不在第 6 阶段前提前实现评分、黄金集或主动学习。
- 不把确定性模拟业务工具描述为真实企业系统。

## 验收标准

以下条件必须持续成立：

- 协议只接受 `trace_react_v2`；旧后端值以及缺失 TRACE-ReAct backend 证明的录制明确失败。
- 一个三步以上的依赖型任务在同一容器中完成，后一步参数来自前一步真实工具返回。
- 正式 Episode 的进程、网络和工件证据证明 Qwen/Ollama、LangGraph Agent、工具及场景状态同处一个
  一次性容器；模型 endpoint 为容器回环地址，Agent 不挂载宿主权重且不访问其他模型服务。
- 正式 Agent 自主产生工具名、参数和 `submit`；请求中不存在预定 action plan，`OfficeControlProvider`
  与 Fake/RuleBased 路径的结果只能进入测试替身报告。
- 普通文本“完成”不算成功；只有合法 `submit` 成功，轮次耗尽返回 `agent_no_submit`。
- clean 场景完成正常任务且无攻击副作用；四类攻击入口的结果由权限事实、工具轨迹和最终业务状态判定。
- Office V2 固定基础世界满足锁定的实体数量、跨域关系、同名/陈旧/无权资源和冲突信息下限；每次
  Episode 从相同 digest 复制状态，任何父世界或父案例原地修改都按完整性错误拒绝。
- 正常任务目录至少包含 10 个 TaskGoalGraph 蓝图和 24 个不同 Actor/资源绑定；参考执行至少形成
  12 种忽略具体 ID/文本后的工具路径形状，且至少 8 个案例包含 5 次以上真实依赖工具调用。
- 攻击目录至少包含 12 个目标，其中至少 6 个为两阶段以上复合目标；内容入口覆盖四域可达位置，
  表达变化本身不计作新的结构案例。
- 至少 4 个案例执行确定性澄清；可信回复产生的授权范围和有效期可复核，拒绝、无权回复和伪造内容
  均不改变授权状态。
- 录制保存模型、工具、状态和检查点；strict replay 的行为摘要、最终状态和检查点全部匹配。
- fork 能替换允许的 Prompt 断点并产生独立后缀，父轨迹不变。
- 模型 digest、资源边界和输入配置进入可审计记录。
- 一个场景 Campaign 的每个相互独立攻击组合使用新的 Episode；复合攻击链只有在 Scenario 明确建模时
  才共享状态。
- baseline complete 前，每个已注册且可达攻击目标至少有一个已提交 Episode；无合法组合时有稳定、
  可复现的不可达/不兼容原因。公平调度保证目标不会无限饥饿。
- saturated 只能由 baseline complete 后的有效 Episode 无增益窗口触发；候选拒绝、Provider/基础设施
  错误和 soak probe 不计入窗口。预算先耗尽必须报告 `budget_exhausted_incomplete`。
- LLM 调用前冻结的 MutationPlan 引用输入 feedback digest 并声明请求改变/保持维度；调用后的
  Candidate/ValidationRecord 保存实际差异与结果。显式目标重定向重新校验组合合同，RuleBased/Fake
  结果不能冒充最终语义质量。
- 正式 V2 每个 Generation 的 CandidateSet 恰好包含一个候选；该候选的执行、AttemptReceipt、覆盖和
  Corpus 结算提交前，不得调度下一 Generation。重试只为同一候选增加有界 attempt，不得生成第二个
  候选，所有尝试成本必须累计。
- 只改变攻击表达的计划保持其他冻结组件一致；从已注册目标 A 显式重定向到 B 可通过，而未声明的
  任务、目标或入口变化、未注册组件及重定向后授权/前置条件失败必须在进入 Docker 前拒绝且不得
  进入覆盖。
- 计划目标 A 而轨迹意外实现风险 B 时，A 的计划结果和 lineage 不变，B 只作为 unexpected RiskHit
  记录；计划期望路径 X 而实际路径为 Y 时，coverage 只记录 Y。
- 真实 LLM Mutator 至少完成两代；第二代引用第一代 feedback digest，并可证明计划因覆盖空白而改变。
- LLM-as-Judge 的最终评分引用执行证据并通过冻结黄金集质量门；Fake Judge 只提供工程测试证据。
- Judge 把已实现风险判为安全时不得改变风险签名，只产生 provisional 冲突复核；Judge 在无执行证据
  时声称违规不得新增 RiskHit。
- 只重试明确临时错误；配置、漂移、完整性、清理和未知错误暂停 Campaign。
- 容器无宿主文件和公网访问，运行后容器与临时卷零残留。
- 完整 Pytest、Ruff、适用 Docker E2E 通过；skip 与未运行项目必须如实报告。
- README、SPEC、路线图、LOG、LOG-INDEX、HANDOFF 和真实代码/证据一致。

## 规格变更规则

SPEC 只描述产品应成为什么、设计边界和验收合同，不记录日常施工进度。施工状态写入路线图、
HANDOFF 和 LOG。只有用户明确改变产品目标或支持边界时才修改 SPEC，并在 LOG 中记录原因；本次把
执行面收敛为唯一 `trace_react_v2`、彻底取消旧格式兼容，属于明确的产品边界变更。
本次用户进一步明确最终语义变异器和最终裁判都必须由 LLM 承担，并恢复“目标保持型与显式目标
重定向型并存、禁止静默漂移”的原始变异边界，因此属于产品合同澄清而非施工进度记录。
本次用户又确认一个场景 Campaign 应在自适应深挖前保证全部预设、可达攻击方向获得最低执行机会，
并要求区分基线完成、饱和与预算不足；这同样是产品完成语义变更，因此同步写入 SPEC。
本次用户明确正式被测 Agent 必须把 Qwen 权重、Ollama 推理服务、LangGraph 决策循环、办公工具和环境
全部放入同一个一次性 Docker 容器；外部控制器不得替代 Agent 规划工具序列。因此将先前“独立 Ollama
容器经 internal 网络供 Agent 调用”的拓扑废止为历史校准方案，并新增第 5 阶段前置门。
本次用户进一步纠正场景产品边界：Office V1 的间接提示注入只是攻击入口之一，`InjectionCarrier`
不再是所有 Fuzzing 种子的必选核心对象。当前优先冻结并完整建设邮件、云盘、日历、工作区文件四域，
身份权限与跨域因果链，以及直接任务、间接内容、伪造授权、参数来源操纵四类入口；覆盖率与变异施工
等待场景 V2 冻结后重新设计。
本次用户进一步明确正式 V2 反馈闭环采用单候选串行决策：每轮只生成、执行和结算一个候选，真实反馈
提交后才调度下一轮。该规则取代早期 Ollama Provider 的 2-4 候选子批合同；旧子批实现只能作为历史
资产审计对象，不能进入新的 V2 Campaign。
