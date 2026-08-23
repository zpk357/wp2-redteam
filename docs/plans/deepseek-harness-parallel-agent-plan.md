# DeepSeek Harness 第二 Agent Runtime 精简施工总计划

状态：`H0-H6 已完成；下一步 H7 真实 Qwen 与服务器综合验收（需用户确认）`

编写日期：2026-08-22

授权边界：`AUTHORIZED-EVALUATION-SCOPE.md`

候选版本覆盖说明：`v0.2.0-rc.1` 采用 registry-first/server-built 部署。H7 中任何旧的“制作或上传
模型离线包”表述均由 `deepseek-harness-h7-real-qwen-server.md` 的在线获取合同取代；不得把模型层推送
到 GHCR。H8/Judge 实现及其详细计划不属于本候选提交，待真实模型服务器门完成后另行恢复。

## 1. 唯一目标

把 DeepSeek Harness 建成现有平台的第二个正式 Agent Runtime，并最终同步现有 Agent 已具备的全部平台能力：

```text
同一 Office V2 Case、17 个工具、可信多轮交互
-> 同一 Policy/事务事实和 Utility/Security Oracle
-> 同一 trace_react_v2 recording/replay/fork
-> 同一 CoverageInput、双覆盖、Corpus、RiskFrontier 和调度
-> 同一结构化变异、候选执行和多代反馈闭环
-> 独立 Harness-Qwen Docker/服务器证据
-> 同一 Judge 标准输入和最终报告
```

完成后，用户可在直接运行入口或 Campaign TargetProfile 显式选择 `langgraph` 或 `deepseek_harness`。默认仍为
`langgraph`；选择结果属于宿主启动身份，不写入 Case、Candidate 或 `ExecutionRequest`；两个 Runtime 的
Campaign、数据库、Corpus、覆盖累计和归档独立。当前不做性能优劣对比。

## 2. 明确不做

为防止再次把简单目标做成长期基础设施项目，以下内容不属于本计划：

- 不建设动态插件市场、运行时发现或通用第三方插件系统；当前只需一个两值 Runtime kind 和小型显式调度。
- 不建立重复包含模型、镜像、工具和 Case 摘要的新身份聚合模型；只新增 Runtime kind/version/composition，
  其余继续使用现有执行信封和 Manifest 字段。
- 不为空的 Harness 实现建立 skeleton、占位执行器或虚假成功路径。
- 不复制 Office World、ToolRuntime、Policy、Oracle、Replay、Coverage、Corpus、Scheduler、Mutation 或 Judge。
- 不修改冻结的 Office V2 业务语义，不增加 Harness 专用 Case、资源或规则。
- 不新增第二套 TRACE、recording、replay、coverage 或 Campaign 格式。
- 不重复运行 17 工具逐项矩阵、24/48 Case 矩阵、全仓测试或现有 LangGraph 的昂贵 Docker/真实模型门。
- 不因为以后可能出现第三种 Agent 而提前抽象；第三种 Runtime 真正进入范围时再提取注册机制。

## 3. 固定架构

### 3.1 公共路径

```text
Host launch selection / Campaign runtime lock
          |
          v
Runtime-specific image + trusted container runtime lock
   |                     |
   v                     v
现有 LangGraphAdapter    DeepSeekHarnessAdapter
                         |
                         v
                  Node JSON-RPC driver
                         |
                  官方最小 Harness composition
                         |
                  stdio Office V2 MCP bridge
                         |
                         v
                  现有 OfficeV2ContainerSession

两条路径最终都输出现有 TraceEvent、可信 sidecar、状态摘要和终态。
```

外层继续使用现有 Python `RuntimeState`、RPC、取消和结果合同。Harness 私有 Session、Cordis 对象和
durable event 不成为平台协议，只能作为诊断工件或由 Adapter 转换为现有中立事实。

### 3.2 Runtime 身份

只新增三个来源字段：

```text
producer_runtime_kind
producer_runtime_version
producer_runtime_composition_digest
```

模型、镜像、工具目录、Case、Prompt 和执行摘要继续使用现有字段。Manifest 把这些已有字段与三个 Runtime
字段绑定到同一 Episode/Campaign，不再新建一套重复身份对象。Runtime 字段只用于同源验证，不计行为新颖度。

### 3.3 失败语义

- 未指定 Runtime：使用 `langgraph`。
- 未知 Runtime：协议入口拒绝。
- 显式选择 Harness 但实现、身份或进程不可用：明确失败，不启动 LangGraph。
- Harness 初始化、Bridge、模型或协议失败：沿用现有配置/临时/未知错误分类和 Campaign 暂停规则。
- 工具拒绝：作为真实工具结果回灌，允许 Agent 继续决策。
- 没有有效 `submit`：沿用 `agent_no_submit`。
- 取消、超时、预算耗尽和清理失败：沿用现有终态，不新增近义状态。

## 4. 复用边界

| 能力 | 直接复用 | Harness 只新增 |
|---|---|---|
| RPC、取消、终态 | `RuntimeState` | 一个满足 `AgentAdapter` 的实现 |
| Office 世界与 17 工具 | `OfficeV2ContainerSession`、ToolSpec、ToolRuntime | ToolSpec 到 MCP 的机械映射 Bridge |
| 多轮可信回复 | 现有 InteractionCoordinator | driver 的 followup 转接 |
| Policy/事务/Oracle | 全部现有实现 | 无业务规则 |
| TRACE/sidecar | 现有 schema 与事实模型 | Harness event 到现有事实的 Adapter |
| Recording/Replay/Fork | 现有格式和状态机；现有 LangGraph replay/fork engine | producer 录制转换与身份分离 |
| Coverage | 现有完整性门和双覆盖 | 验证 Runtime 元数据不进入 novelty key |
| Campaign/Corpus/调度 | 现有数据库和算法 | Runtime 选择与独立目录身份锁 |
| Mutation/候选竞争 | 全部现有实现 | 使用 Harness 执行已有 Candidate |
| Judge | 现有 JudgmentCase/Rubric | Harness 事实进入同一 Evidence Adapter |

任何阶段一旦需要复制表中“直接复用”的模块，必须停止并审查架构，不得继续堆代码。

## 5. 七个施工阶段

H0-H1 已完成：产品支持边界、上游版本锁和最小官方 SDK/Cordis/MCP 可行性已经确认。后续只执行 H2-H8。

### H2：最小 Runtime 选择入口

目标：让请求能显式选择 Runtime，同时保持 LangGraph 默认行为不变。

施工：

1. 新增 `AgentRuntimeKind = langgraph | deepseek_harness`，但不嵌入现有已摘要模型。
2. H2 只让容器 `AdapterFactory` 读取镜像 ENV 启动身份；H3/H4 在真实 Harness 镜像存在后补镜像锁和宿主
   启动前交叉验证。不修改 `ExecutionRequest` 或 `TargetProfile` schema。
3. `AdapterFactory` 使用小型显式分支；不引入动态注册表。
4. Harness 尚未实现时返回 `agent_runtime_unavailable`，不能回退。
5. 不改 `LangGraphReactRuntime`、`RuntimeState`、TRACE 或历史证据。

最低验收：旧请求和 TargetProfile 序列化/摘要不变且仍选 LangGraph；显式 LangGraph 启动值等价；未知启动身份拒绝；显式
Harness 不可用且未调用 LangGraph。
只跑 4-6 个单元断言和一次 diff 审查。

详细计划：`docs/plans/deepseek-harness-h2-runtime-neutral-interface.md`。

### H3：Harness 真实最小垂直链

详细计划：`docs/plans/deepseek-harness-h3-minimal-vertical-slice.md`。

目标：先证明真实 Harness 能通过同一平台入口完成一个工具回合，而不是继续建设抽象。

施工：

1. 实现 `DeepSeekHarnessAdapter`，由现有 Python Runtime 启动 Node SDK driver，并回收它派生的官方 JSON-RPC
   Runtime 与 MCP Bridge 整个进程树。
2. driver 使用 H1 锁定的官方 SDK 与最小 composition，不加载终端、Web、浏览器、外部 MCP 或子 Agent。
3. 建立 stdio Office MCP Bridge，只映射一个无副作用读取工具到现有 `OfficeV2ContainerSession`；通过独立
   模型不可见 sidecar 输出可信 invocation/result/state 事实并与 Node 事件闭合关联。
4. 工具真实结果必须进入下一次模型决策，最后显式 `submit`。
5. 在最小一次性容器中证明正常退出和取消清理。

最低验收：一个确定性工具回合成功；一个取消路径无子进程/容器残留；初始化失败不回退。最多两次容器执行。

用户确认门 A：展示一条实际业务链的请求、模型选择、工具结果回灌、后续决策和 submit。用户确认这确实是
第二个 Agent Runtime 后，才进入完整 17 工具接入。

### H4：完整 Office V2 直执行能力

详细计划：`docs/plans/deepseek-harness-h4-office-v2-direct-parity.md`。

目标：让 Harness 具备与现有 Agent 相同的直接执行业务能力和中立事实。

施工：

1. 从冻结 ToolSpec 机械生成全部 17 个 MCP schema；Bridge 不复制权限或业务逻辑。
2. 先证明 `awaiting_followup -> whole-Agent idle -> same-session authenticated user followup`，再接通读写、可信
   澄清/授权、策略拒绝、事务失败、预算、显式 submit 和清理；若公开 API 无法保持 user-role 边界则停止。
3. 规范化模型调用、工具调用/结果、交互、状态摘要、终态和成本；可信 PolicyDecision、StateDelta 和 Oracle
   继续来自现有 Python 内核。
4. 让现有 Utility/Security Oracle 直接求值，不增加 Harness 分支。

最低验收：静态证明 17 个 MCP schema 与 ToolSpec 的规范字段一一对应，映射 Manifest 绑定冻结源目录摘要；
只执行四条确定性代表链：读取依赖链、已提交状态变化、可信授权多轮、策略拒绝/回滚。复合 partial/full
从四条中选择一条覆盖，不增加第五条。

### H5：Recording、Strict Replay 与 Verification-only Fork

详细计划：`docs/plans/deepseek-harness-h5-record-replay-fork.md`。

目标：同步可审计与可恢复能力，不复制重放器。

施工：

1. Harness direct execution 写入现有 recording 格式；私有 Session 只作非权威诊断。
2. 保留并明确现有 LangGraph strict replay/fork engine；只有证明确属无关的类型判断才移除。
3. strict replay 不启动 Harness、Node、Ollama 或 Qwen，ReplayResult 单独记录 replay engine 身份。
4. fork 沿用现有 LangGraph `live_and_record` verification-only 语义，不增加 live Harness Session 恢复；
   子记录分开标记 Harness 父前缀来源与 LangGraph 后缀 producer/fork engine。
5. recording 保存三个 producer Runtime 字段；replay engine 身份与 producer 分开。

最低验收：选 H4 的一条代表 Episode，完成 direct -> recording -> strict replay -> verification-only fork；
再做一个摘要篡改拒绝。只执行这一组，不跑案例矩阵。

### H6：Coverage、Campaign、Mutation 与多代闭环

详细计划：`docs/plans/deepseek-harness-h6-coverage-campaign-loop.md`。

目标：让 Harness 使用现有自动探索平台，而不是另建 Harness 专用算法。

施工：

1. CoverageInput 构建前的 acquisition 门验证 producer Runtime 来源字段；不修改 `V2CoverageInput` schema，
   Runtime 身份不进入行为新颖度键。
2. Campaign Manifest 增加三个 Runtime 字段，并继续绑定已有模型/镜像/工具/Case 摘要；Episode request
   不重复携带 Runtime 选择。
3. 显式选择 Harness 后使用独立 Campaign ID、数据库、Corpus、覆盖累计和报告目录。
4. Corpus、RiskFrontier、公平调度、MutationIntent、Materializer 和候选竞争不改算法。
5. 串联现有暂停、恢复、无新增切换和完成状态。

最低验收：一个 Harness 确定性三代闭环，同时覆盖一次暂停/恢复；证明下一代引用上一代 feedback digest、
Coverage 可累计、Corpus 可晋升、方向可切换。既有 LangGraph Campaign 不重跑 Docker，只跑共享合同聚焦回归。

### H7：真实 Qwen 与服务器综合验收

详细计划：`docs/plans/deepseek-harness-h7-real-qwen-server.md`。

目标：一次性证明 Harness 在正式自包含环境中能够真实控制 Qwen 和工具闭环。

施工：

1. 构建独立 Harness-Qwen 镜像和离线包，复用既有模型锁、Office 内核和服务器部署机制；现有 Mutator 保持
   独立角色/镜像/Prompt/身份，Controller 串行分配 GPU。
2. 先运行一个真实 Qwen 多步正常任务，必须包含工具调用、结果回灌、后续决策和 submit。
3. 再运行一个代表授权/策略分支，不展开完整 Case 矩阵。
4. 运行同一 Harness Campaign 的最小两代真实闭环和一条本地 strict replay。
5. 顶层 evaluation bundle 绑定 preflight 与 Campaign；Campaign 工件共享 campaign_id，并分别归档 Agent、
   Mutator 的模型/镜像/Runtime/Prompt 身份、GPU、progress、数据库、轨迹、Oracle、Coverage、Replay 和清理事实。

最低验收：一次 preflight、一次两代 Campaign、一次 replay；实际完成代数和 completion status 必须一致；
推理期 GPU/配置有证据，结束后零当前 Episode 残留。除非共享代码摘要改变，不重跑现有 LangGraph 服务器门。

服务器租用、上传或远程执行前保留一次外部成本确认门。

### H8：Judge 接入与最终能力冻结

详细计划保留在未提交的 Judge 工作区，不属于 `v0.2.0-rc.1`。

目标：在 Judge baseline 已先冻结的前提下，完成最后一项平台能力同步并给出可审计的完成矩阵。

施工：

1. Harness 事实通过现有 Evidence Adapter 生成同一 `JudgmentCase`，不新增 Rubric。
2. Judge 继续只离线消费封存事实，不反馈 Coverage、Mutation 或 Campaign。
3. 用一条代表 Case 验证事实优先级、缺失事实拒绝和 Harness 私有文本不能覆盖工具/状态事实。
4. 完成 H2-H8 能力矩阵、README、HANDOFF、LOG 和最终代码审查。

最低验收：一条 Harness JudgmentCase 和既有 Judge 聚焦合同；不重跑整个黄金集，除非 Judge 代码本身改变。

用户确认门 B：用户审查能力矩阵、代表业务事实、未运行项和服务器证据后，才宣布 Harness 与现有 Agent
同步到相同已验证平台层级。

## 6. 统一测试与代码审查规则

### 6.1 测试预算

| 阶段 | 最大常规验证预算 |
|---|---|
| H2 | 4-6 个单元断言，无 Docker |
| H3 | 1 条成功 + 1 条取消容器路径 |
| H4 | 1 个目录映射检查 + 4 条确定性代表链 |
| H5 | 1 条 direct/record/replay/fork + 1 个篡改 |
| H6 | 1 个确定性三代闭环，内含一次恢复 |
| H7 | 1 个真实 preflight + 1 个两代 Campaign + 1 个 replay |
| H8 | 1 条 Judge 输入 + 共享合同聚焦回归 |

以上是默认预算，不是禁止补充验证的绝对上限。只有代码审查发现某个被修改分支没有任何失败信号时，才允许
用一条最小聚焦断言补齐；必须同时说明现有代表链为何不能覆盖，禁止因此扩大为案例矩阵。

规则：

- 同一代码摘要下已经通过的昂贵门不重复运行。
- 17 工具用同源 schema 摘要证明完整性，只对代表类别执行，不逐工具重复。
- 冻结 Office V2、Oracle、Coverage、Corpus、Mutation 的既有业务测试不重跑；只运行被修改接口的相邻断言。
- 全仓测试默认不运行；只有最终合并前发现共享合同影响无法由聚焦集覆盖时，才由用户决定是否运行。
- 测试失败先定位根因；不通过扩大矩阵、增加案例特判或反复重跑碰运气。

### 6.2 每阶段一次代码审查

每个阶段实现完成后做一次只读 diff 审查，固定检查：

1. 是否复制了已有平台逻辑。
2. 是否存在 Harness 失败后回退 LangGraph。
3. 是否把 Runtime 私有对象泄漏为公共协议。
4. 是否漏记成本、终态、状态摘要或清理失败。
5. 是否把 Runtime 身份计入 Coverage 新颖度。
6. 是否增加案例 ID、资源 ID 或表达文本特判。
7. 是否修改了阶段允许范围之外的文件。

只有审查发现真实风险时才补测试；不能为了“看起来稳妥”自动扩大测试集合。

## 7. 停止信号

出现以下任一情况，AI 必须暂停并向用户报告，不能继续堆实现：

- 需要复制 Office World、Policy、Oracle、Coverage、Campaign、Mutation 或 Judge。
- 需要新增第二种 backend、TRACE 或 recording 格式。
- 必须修改 Harness 上游私有代码或使用未公开 Hook。
- 现有 LangGraph 默认行为、TRACE 或历史摘要发生变化。
- Harness 工具结果不能可靠进入下一次模型决策。
- MCP Bridge 无法复用唯一 OfficeV2ContainerSession，出现双状态源。
- 同一个缺陷连续两次靠新增特判修复。
- 聚焦测试无法区分成功和失败，或真实服务器证据无法绑定同一 Campaign。

## 8. 施工时间估算

| 阶段 | 预计有效工作时间 |
|---|---:|
| H2 | 0.5 天 |
| H3 | 1.5-2.5 天 |
| H4 | 2-3 天；可信 followup 门约 0.5 天后可提前停止 |
| H5 | 1.5-2 天 |
| H6 | 1-1.5 天 |
| H7 | 1-2 天，另计服务器等待 |
| H8 | 0.5 天 |
| 合计 | 8-12 个有效工作日 |

在可信 followup 门直接可行、上游预发布 API 没有新阻塞、Docker/模型资源就绪且 Stage 6/Judge 工作树已先
冻结的情况下，可以压缩到约 7-9 个连续工作日。最大不确定性是 Harness 的 user-role followup 边界、Node
MCP 跨进程事实与 recording 顺序的对齐，以及 Harness 与 Ollama/Qwen 的真实 tool calling。遇到阻塞应在
对应停止门报告，不增加通用平台层。

## 9. 给后续 Codex 的固定施工指令

```text
本任务只执行 DeepSeek Harness 精简总计划中的 Hx，目标是让 Harness 复用现有 Agent 的同一平台能力。
不得建设动态插件系统、重复身份模型或第二套平台算法，不得修改冻结 Office V2 业务语义。

先读取当阶段现有接口和上一阶段证据，说明本阶段输入、状态变化、输出和失败语义，然后直接施工。
只运行计划规定的最小聚焦测试；同一摘要下通过的 Docker、真实模型和案例矩阵不得重跑。

实现后只做一次 diff 代码审查，重点检查复制逻辑、静默回退、私有类型泄漏、终态/成本/清理遗漏和案例特判。
发现需要复制公共模块、新增协议或修改 LangGraph 既有行为时立即停止，不得自行扩大范围。

完成后报告修改文件、代表数据流、验证结果、未运行项和下一阶段。未经明确授权不提交、不推送、不远程执行。
```

## 10. 当前阶段门

H0-H6 已完成。H3 已用官方 SDK/JSON-RPC composition、stdio MCP Bridge 和唯一
`OfficeV2ContainerSession` 完成 `list_directory -> 结果回灌 -> 后续 submit`；无公网只读容器的成功与取消
两条验收均通过，取消后 Episode 残留为 0。权威机器证据位于
`agent_variants/deepseek_harness/h3-evidence.json`。用户已确认 H3；H4 已完成完整 Office V2 直执行、可信
多轮、Oracle、终态、成本和两条 Docker 代表验收，证据位于
`agent_variants/deepseek_harness/h4-evidence.json`。H5 已绑定 producer 身份并完成 Harness recording、既有
LangGraph strict replay verifier 和 verification-only fork；可信 followup 的 idle 边界被无损保留，普通
LangGraph 时序不变。机器证据位于 `agent_variants/deepseek_harness/h5-evidence.json`。H6 已完成 Coverage
前置来源核验、Campaign producer 三元组持久化、模型摘要兜底删除、现有 settlement/Corpus/feedback 接入、
提交后恢复和三代 Docker Campaign；最终证据位于 `agent_variants/deepseek_harness/h6-evidence.json`，摘要为
`sha256:b05555161735f91d0efe7317354893817fb1d450013558661605bbb8ce88a585`。确定性
Docker 样例产生行为覆盖与 Corpus entry，后两代进入无增益反馈，三代 recording/Oracle/CoverageInput 完整且
容器零残留。它没有产生风险里程碑，持久 Campaign 也仍是 baseline 非终态。尚未证明 Harness 的真实 Qwen、
GPU/服务器或 Judge 能力；H7 涉及外部成本，必须等待用户确认。
