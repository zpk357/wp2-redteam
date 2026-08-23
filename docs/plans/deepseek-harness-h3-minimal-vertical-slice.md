# DeepSeek Harness H3：真实最小垂直链详细计划

状态：`H3 技术施工与聚焦验收已完成；等待用户确认门 A`

上游：`docs/plans/deepseek-harness-parallel-agent-plan.md`

## 1. 目标与完成边界

H3 只证明一件事：显式选择 `deepseek_harness` 后，真实 Harness composition 能在一次性容器内自主调用一个
Office V2 读取工具，消费真实工具结果，继续一次模型决策并有效 `submit`。

H3 不接全部工具、不做 Oracle 完整矩阵、不做 recording/replay、Coverage、Campaign、真实 Qwen 或 Judge。

## 2. 固定数据流

```text
Harness TargetProfile + trusted container runtime lock
-> 现有 RuntimeState
-> DeepSeekHarnessAdapter
-> Node SDK client/driver 子进程
-> 官方 JSON-RPC Runtime 子进程
-> H1 锁定的最小 Harness composition + 确定性模型替身
-> stdio OfficeV2 MCP Bridge 子进程
-> 唯一 OfficeV2ContainerSession
-> 一个只读工具结果
-> Harness 后续模型请求
-> submit
-> 现有 TraceEvent/终态
-> finally 关闭并回收整个 driver/Runtime/MCP 进程树和容器
```

Python Runtime 仍是 RPC、取消和终态的所有者；Harness 控制模型/工具循环；MCP Bridge 是唯一 Office 状态
所有者。禁止 Python 外层预先决定工具名、参数或调用顺序。

## 3. 进程与协议合同

### 3.1 Python Adapter

- 满足现有 `AgentAdapter.execute()`。
- Adapter 公开固定 Runtime kind/version；composition digest 来自 H1 package lock、上游 commit 和正式
  composition 内容。Factory 启动前交叉验证 `TRACE_G_AGENT_RUNTIME`、镜像内只读 lock 与 Adapter 常量，
  任一不符失败关闭。模型、Case 和请求不能提供或覆盖这些值。
- 每个 Episode 只启动一个 Node SDK client/driver；它会按 H1 的官方路径再启动 JSON-RPC Runtime，后者再
  启动 MCP Bridge。Adapter 必须把三者视为一个进程树，而不是误认为只有一个 Node 进程。
- 请求通过长度有界的 stdin JSON/临时只读文件传入，不放入命令行参数。
- stdout 只承载有版本的 NDJSON driver event；诊断进入 stderr，大小有界。
- 取消时先请求正常 shutdown，超过现有 cleanup deadline 后终止整个进程树；必须记录每个已知 PID 的
  退出结果，不能只等待 driver 父进程。
- 初始化失败、协议错序、未知事件和子进程非零退出使用稳定错误分类，不回显原始请求。

### 3.2 Node Driver

- 复用 H1 的官方 SDK JSON-RPC 和最小 Cordis composition。
- 只加载 Agent spine、锁定模型 adapter、Session、token meter 和一个 Office stdio MCP client。
- 不加载终端、Web、浏览器、外部 MCP、插件发现、工作流或子 Agent。
- 正式 composition 固定公开配置 `maxParallelToolCalls: 1`；模型可以提出多个调用，但上游 Runtime 必须串行
  交付 MCP，Bridge 不自造并发队列。
- durable event 只用于驱动本次活动；平台不保存 Cordis/Harness 私有对象。
- H3 使用确定性回环模型，输出必须真实依赖 MCP 工具结果。fixture 不得包含宿主 action plan；在无 Docker
  的 driver 聚焦断言中改变一次工具结果，后续模型决定也必须随之改变。

### 3.3 MCP Bridge

- 从冻结 V2ExecutionEnvelope 创建唯一 `OfficeV2ContainerSession`。
- H3 只暴露一个无副作用读取工具和现有 `submit` control。
- 业务调用必须经过现有 Agent surface/ToolRuntime；Bridge 不判断权限、不直接改状态。
- 模型只看到现有稳定工具结果投影；完整可信结果留在 Python sidecar。
- Bridge 通过 Episode 私有、模型不可见的 append-only NDJSON sidecar 写入可信事实，并在结束时原子写入
  final summary。每条记录至少包含 schema version、execution_id、随机 session nonce、递增 sequence、
  invocation ordinal、规范工具名、参数摘要、结果摘要和状态摘要；final summary 绑定记录文件摘要、最终
  状态与 complete/incomplete。
- Adapter 只能在 Node 事件与已经持久化的 Bridge 记录完成关联后发布规范 tool result/可信 TRACE；缺序号、
  重复、摘要不符、Bridge 提前退出或 final summary 缺失均失败关闭。Node/Harness 自报事件只作待关联材料。
- H3 禁止并行执行工具调用。关联键固定为 invocation ordinal + 规范工具名 + 参数摘要，再核对结果摘要；未知
  Harness call id 只保存为 acquisition metadata，不能成为可信事实主键。

## 4. 预计修改区域

允许区域：

```text
agent_image/app/adapter/factory.py
agent_image/app/adapter/deepseek_harness_adapter.py       # 新建
agent_variants/deepseek_harness/runtime/**                # 正式 driver/composition/bridge
agent_variants/deepseek_harness/Dockerfile.dev            # 最小无权重开发镜像
agent_variants/deepseek_harness/locks/runtime-source.json  # kind/version/composition 来源锁
agent_variants/deepseek_harness/package*.json              # 继续使用 H1 lock
tests/unit/test_deepseek_harness_adapter.py                 # 新建
tests/integration/test_deepseek_harness_vertical_slice.py   # 新建或现有对应目录
```

只读复用：

```text
agent_image/app/runtime.py
agent_image/app/office_v2_session.py
src/sandbox/scenarios/office_v2/**
```

禁止修改 Replay、Coverage、Fuzzer/Campaign、Mutation、Judge、服务器脚本和冻结 Office V2 业务合同。

## 5. 施工步骤

### H3.0 协议与进程基线

锁定 Runtime kind/version/composition 来源，随后锁定 driver 请求、event、shutdown 和错误四类最小消息，
以及 Bridge sidecar/final summary；每条消息有
schema version、execution_id 和递增 sequence。确认 stderr 不进入协议，stdout 非 JSON 立即失败。锁定完整
进程树与 PID 回收责任。

### H3.1 Driver 生命周期

把 H1 probe 中已证明的 SDK 启动、followup、idle、shutdown 和 process-close cancellation 提取到正式
driver；probe 保留为历史证据，不由正式代码 import。

### H3.2 单工具 Bridge

使用一个现有只读 ToolSpec 建立 MCP schema 与 handler，调用现有 Session surface，并通过专用 sidecar 输出
invocation、result 和前后状态摘要。前后摘要必须相同；Adapter 按 ordinal/name/argument digest 配对。

### H3.3 Adapter 与 TRACE 边界

Adapter 把 driver 的模型/工具/submit 生命周期映射为现有 TraceEvent；可信 sidecar 由 Bridge 产生，不能由
Node 自报。只实现 H3 所需事件，不提前实现 recording。未完成关联的 Node 事件不得发布为可信工具事实。

### H3.4 最小容器

构建不含 Qwen 权重的开发镜像，写入匹配的 runtime label、`TRACE_G_AGENT_RUNTIME` ENV 和只读来源锁；使用
非 root、只读 rootfs、无公网和临时目录。镜像只为 H3 代表链服务，不得修改当前 LangGraph 镜像。

### H3.5 聚焦验收与代码审查

先运行协议/Adapter 小单测，再运行最多两次容器：一次成功链、一次取消。随后做一次 diff 审查并生成简短
H3 证据摘要。

## 6. 最低验收

成功链必须同时证明：

1. 请求明确选择 Harness。
2. Harness 自主产生工具名和参数。
3. 一个真实 Office 工具 invocation/result 成对出现。
4. 后续模型请求包含该工具结果的摘要或结构化内容。
5. 有效 submit 后状态为 completed。
6. 只读调用前后状态摘要相同。

取消链必须证明：Adapter、Node driver、JSON-RPC Runtime、MCP Bridge 和容器全部结束；sidecar 标记
incomplete，没有成功终态或当前 Episode 残留。

## 7. 代码审查重点与停止条件

审查：工具顺序是否被宿主预置、是否存在 Harness -> LangGraph 回退、是否有第二状态源、stdout 是否混入日志、
取消是否只杀父进程、工具结果是否真的进入后续模型请求。

以下情况立即停止：必须修改上游私有代码；MCP 无法调用现有 Session；需要复制 ToolRuntime；无法把工具结果
回灌给模型；两次修复仍依赖消息顺序特判。

## 8. 用户确认门 A

展示一条业务实例的请求、模型决定、工具调用、真实结果、后续决定、submit、状态摘要和清理结果。用户确认
这是真实第二 Runtime 后，才进入 H4。H3 通过不代表 17 工具、真实 Qwen 或平台闭环已同步。
