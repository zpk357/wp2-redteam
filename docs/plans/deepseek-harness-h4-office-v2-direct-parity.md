# DeepSeek Harness H4：完整 Office V2 直执行详细计划

状态：`已完成（2026-08-22）；下一步进入 H5，不新增业务确认门`

上游：`docs/plans/deepseek-harness-parallel-agent-plan.md`

## 1. 目标与不做事项

把 H3 的一个读取工具扩展为完整 Office V2 直执行能力：17 个业务工具、control 工具、可信多轮回复、事务、
Oracle、预算、终态和清理均与现有 Agent 使用同一事实系统。

H4 不做 recording/replay、Coverage、Campaign、Mutation、真实 Qwen、服务器或 Judge。

## 2. 输入、状态、输出

- 输入：冻结 V2ExecutionEnvelope、Runtime kind、预算和确定性模型身份。
- 状态：唯一 `OfficeV2ContainerSession` 持有 EpisodeWorld、ToolRuntime 和可信交互；Node/Harness 不持有
  第二份业务状态。
- 输出：现有 TraceEvent、可信 tool/interaction sidecar、最终状态摘要、Utility/Security Oracle、成本和终态。
- 失败：参数、权限、策略和事务失败作为工具结果回灌；协议/身份/完整性和未知错误失败关闭。

## 3. 复用机制

1. 从 `office_v2_tool_definitions()`/现有 Agent surface 机械生成 MCP schema，不手写 17 份定义；映射 Manifest
   绑定冻结源目录摘要。
2. 每次 MCP call 仍由 `OfficeV2AgentSessionSurface` 进入唯一 `OfficeV2ToolRuntime`。
3. 可信结果观察继续由 `OfficeV2ContainerSession.build_agent_surface()` 完成。
4. Oracle 只调用现有 `build_live_oracle_artifact()`，不增加 Harness 目标判断。
5. 澄清、可信回复和限时授权复用现有 InteractionContract/Coordinator；不能把可信 user-role followup
   降级为普通 MCP tool result。

如果 control handler 的构造仍只存在于 `LangGraphReactRuntime` 内，允许提取一个最小 Runtime-neutral helper，
让 LangGraph 和 Harness 同时调用；禁止复制。提取前后必须用一个现有 LangGraph 聚焦断言证明行为不变。

## 4. 预计修改区域

```text
agent_variants/deepseek_harness/runtime/**
agent_image/app/adapter/deepseek_harness_adapter.py
agent_image/app/office_v2_session.py                 # 仅缺少中立导出接缝时
agent_image/app/office_v2_runtime_surface.py         # 仅确需提取共享 control helper 时新建
agent_image/app/adapter/langgraph_react_runtime.py   # 仅改为调用上述 helper，禁止行为变化
src/sandbox/cli.py                                    # 仅顶层启动选择与镜像身份预检
tests/unit/test_deepseek_harness_tool_catalog.py
tests/integration/test_deepseek_harness_office_v2.py
```

禁止修改冻结 ToolSpec、World、Policy、Oracle 规则、Case 目录、Replay、Coverage、Campaign、Mutation 和 Judge。

## 5. 施工步骤

### H4.0 工具目录同源

生成 17 个业务工具和既有 control schema，逐项锁定规范名称、参数类型、required 字段和结果投影；映射
Manifest 保存冻结 `source_tool_catalog_digest` 及自身摘要。MCP 名称前缀只在 transport 层存在，进入可信
事实前恢复规范工具名。由于 MCP 与 ToolSpec 外层 JSON 结构不同，不要求两个整体摘要直接相等。

### H4.1 全部工具通用调用路径

用一个 handler 覆盖所有 ToolSpec：schema 校验 -> Agent surface -> ToolRuntime -> 稳定投影 -> Harness。
不得出现按案例或资源 ID 分支。

### H4.2 Control 与多轮

先做独立可行性门，再扩展全部工具：

1. `request_clarification` 必须是该模型回合唯一 control call；Bridge 执行现有 InteractionContract并返回
   原有 `model_visible_payload()`，Adapter 同时进入内部 `awaiting_trusted_followup` 活动状态。不得新增 pending
   业务结果，也不得把预冻结回复文本伪装成工具输出。
2. 当前 Harness activity 必须进入 idle，期间不得再发业务工具、第二个 control 或 `submit`；否则以
   `interaction_protocol_violation` 结束 Episode。
3. Adapter 从可信 directive 取得 responder/channel/grant 事实，命令 driver 用同一 session 的第二次
   `harness.run()` 注入真实 user-role followup；followup 文本、身份和 directive digest 必须闭合关联。
4. Adapter 必须保留 activity idle 边界和期间的 assistant message，不能从 TRACE/recording 输入中悄悄删除；
   后续模型活动才能继续工具调用和 `submit`。Harness 只能提出请求，不能自选可信 responder、grant
   范围或期限。

H1 只证明 whole-Agent idle 后可执行同 Session followup，没有证明运行中注入。若锁定的官方公开接口无法
稳定形成上述 awaiting_followup -> idle -> trusted followup 边界，H4 在此停止，不得把回复塞入 MCP 结果、修改冻结
InteractionContract 或靠提示词宣称已经等价。

### H4.3 事实、Oracle 与终态

使用 H3 已冻结的 Bridge sidecar 关联合同配对模型调用、工具 invocation/result、交互事件、StateDelta、
最终状态和 submit；由现有 Session 构建 Oracle。缺任一可信事实、ordinal/name/argument digest 不一致或
跨 activity 的 followup 引用不闭合时失败，不输出部分成功。

### H4.4 预算与失败

把模型轮次、工具次数、Token、时间和取消映射到现有终态；未分类异常外抛并保留已发生成本。清理失败不能
发布成功。

### H4.5 直接运行入口

现有顶层 Office V2 CLI 增加 `--agent-runtime`，默认仍为 LangGraph；`--image` 继续指定实际镜像。启动前读取
镜像 label/只读 lock，要求 kind/version/composition 与选项匹配；容器内 Factory 再交叉验证 ENV 与同一 lock。
该选项不传入 ExecutionRequest，也不改变 Case、模型、工具或任务。未知/不匹配值在创建容器前拒绝。

### H4.6 代表验收与审查

只运行目录映射检查和四条确定性代表链，完成后做一次 diff 审查；不跑 17 工具逐项或 24/48 Case 矩阵。
目录映射先把 17 工具按 schema/result/事务 handler 等价类分类，四条链必须至少命中每个真实实现类别；若
出现未命中的新 handler 类，只补一条无 Docker handler 断言，不增加业务 Case 矩阵。

## 6. 四条代表链

1. 读取依赖链：搜索 -> 精确读取 -> 后续参数使用 -> submit，状态不变。
2. 已提交变化：读取前置事实 -> 一个写操作 -> 后续读取验证 -> submit，StateDelta 连续。
3. 可信多轮：请求澄清 -> activity idle -> 认证 user-role 回复 -> 必要授权转换 -> 工具继续 -> 一个已提交
   状态变化 -> submit。
4. 拒绝与复合进度：策略拒绝或事务回滚，同时覆盖一个复合目标的 partial/full 区分。

每条链只选现有冻结 Case，不新建 Harness 专用业务样例。

## 7. 验收标准

- 17 个 schema 与冻结 ToolSpec 规范字段一一对应，映射 Manifest 的源目录摘要正确。
- 四条链均由 Harness 循环选择工具，不含宿主 action plan。
- 工具结果进入后续决策；调用/结果/状态/Oracle 引用闭合。
- 合法授权、无授权、平台拒绝和回滚不混淆。
- 可信回复保持 user role；进入 awaiting_followup 后到 followup 前无业务工具或 submit，活动边界未被丢弃。
- `submit` 是唯一成功出口；清理后无当前 Episode 残留。
- 现有 LangGraph 代表路径行为不变；若未提取共享 helper，则不运行该回归。

## 8. 代码审查与停止条件

重点检查手写 schema 漂移、Bridge 复制权限逻辑、Node 自报可信事实、双 Session、control 身份可伪造、失败后
继续发布成功、案例特判和 LangGraph 行为变化。

若必须复制 control/Oracle、无法让 17 工具走同一 handler、或四条链暴露不同的状态所有者，立即停止并报告。
H4 完成后直接进入 H5，不新增业务确认门。
