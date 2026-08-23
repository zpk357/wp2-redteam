# DeepSeek Harness H2：最小 Runtime 选择入口

状态：`已完成；21 项聚焦测试和 Ruff 通过，进入 H3`

上游计划：`docs/plans/deepseek-harness-parallel-agent-plan.md`

## 1. 目标

只增加选择第二 Runtime 所必需的最小公共入口，不实现 Harness 本体，不建设通用插件框架。

```text
Runtime-specific image ENV + trusted container lock
  -> AdapterFactory 显式分支
     -> langgraph：完全沿用现有构造
     -> deepseek_harness：H3 实现前明确 agent_runtime_unavailable
```

Runtime 是启动目标身份，不是业务 Episode 输入。不得把它加入 `ExecutionRequest`、V2 Case、Candidate 或
MutationPlan；这些对象参与规范摘要，加入默认字段也会改变现有 LangGraph 证据。

## 2. 修改范围

允许修改：

```text
src/sandbox/protocol.py                     # 仅新增独立枚举，不给 ExecutionRequest 加字段
agent_image/app/adapter/factory.py
tests/unit/test_shared_protocol.py
tests/unit/test_formal_agent_model_gate.py  # 或一个新的小型 Factory 测试文件
AGENTS.md
LOG.md
LOG-INDEX.md
docs/plans/deepseek-harness-parallel-agent-plan.md
docs/plans/deepseek-harness-h2-runtime-neutral-interface.md
```

禁止修改：

```text
agent_image/app/adapter/langgraph_react_runtime.py
agent_image/app/runtime.py
agent_image/app/replay/**
agent_image/app/office_v2/**
src/sandbox/coverage/**
src/sandbox/fuzzer/**
src/sandbox/judge/**
Dockerfile*
scripts/server_*
```

## 3. 实现

1. 新增两值 `AgentRuntimeKind`：`langgraph`、`deepseek_harness`，但不嵌入任何现有已摘要模型。
2. H2 不修改 `TargetProfile`、`SandboxRunContext` 或 Campaign identity；宿主选择与镜像交叉验证留给有真实
   Harness 镜像的 H3/H4。
3. 容器 Factory 读取镜像 ENV 启动值 `TRACE_G_AGENT_RUNTIME`；缺失时仅为兼容现有 LangGraph 镜像取默认
   `langgraph`，未知值明确拒绝。
4. `ExecutionBackend` 继续只有 `trace_react_v2`。
5. Factory 保留现有 LangGraph 构造函数原样，只在外层增加 Runtime 分支。
6. Harness 分支在 H3 前抛出稳定 `agent_runtime_unavailable`；任何 Harness 错误不得进入 LangGraph 分支。

不增加 `AgentRuntimeIdentity` 聚合模型、动态 Registry、Harness skeleton、JS 身份生成器或新证据格式。
Runtime version/composition 在 H3 有真实实现后作为 Adapter 常量加入，H5/H6 再进入 recording/Manifest。

## 4. 最低验收

只运行一次聚焦集，包含：

1. 修改前后旧请求的 JSON 和规范摘要完全相同。
2. 缺少启动值与显式 `langgraph` 的 Factory 路径等价。
3. `deepseek_harness` 是合法目标/启动值，但 H3 前返回 `agent_runtime_unavailable`。
4. Harness 不可用时 LangGraph 构造函数没有被调用。
5. 未知启动值被明确拒绝。
6. `ExecutionRequest`、`TargetProfile` 字段集合与 `ExecutionBackend` 均没有新增值。

随后只运行修改文件 Ruff 和 `git diff --check`，并做一次 diff 代码审查。不运行 Docker、Ollama、Qwen、
Office 案例、Replay、Coverage、Campaign、Mutation、Judge 或全仓测试。

## 5. 完成条件

完成后只能声明：宿主目标配置和容器 Factory 具备第二 Runtime 的显式选择入口，默认 LangGraph 请求与摘要
未改变，Harness 尚未实现时失败关闭。
不能声明 Harness 已能执行 Office V2。下一步直接进入 H3 真实最小垂直链。
