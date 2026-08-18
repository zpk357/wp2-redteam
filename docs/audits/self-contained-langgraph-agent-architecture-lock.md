# 同容器 Qwen + LangGraph Agent 依赖与架构锁

审计日期：2026-08-04

路线图：`5.G1`

本记录是工程依赖和架构审计，不是法律意见。正式镜像构建、Ollama 系统文件和 Qwen 权重许可证仍需
在 `5.G2` 生成最终 SBOM、NOTICE 和完整镜像 hash lock。

## 1. 结论

`5.G1` 选择“LangGraph `StateGraph` + `langchain-ollama` `ChatOllama` + TRACE-G 自有工具/证据
适配层”。不安装顶层 `langchain`，不使用 `create_agent`，也不恢复仓库已删除的旧
`langgraph_adapter.py`。原因是当前项目需要在模型前后、每次工具调用、授权判断、状态变化和终止处
保留精确 TRACE 事件；低层 `StateGraph` 能复用成熟循环和状态编排，同时不隐藏这些证据边界。

正式 Agent 栈锁定为：

| 直接包 | 锁定版本 | Python | 许可证 | 用途 |
|---|---:|---|---|---|
| `langgraph` | `1.2.10` | `>=3.10` | MIT | `StateGraph`、节点、条件边和运行时状态 |
| `langchain-core` | `1.5.3` | `>=3.10,<4` | MIT | 标准消息、工具和模型接口 |
| `langchain-ollama` | `1.1.0` | `>=3.10,<4` | MIT | `ChatOllama` 工具调用与异步模型接口 |

三者支持项目锁定的 Python 3.11。Linux x86_64 / CPython 3.11 的完整 37-wheel 闭包已由 pip 解析和
下载；版本与 wheel SHA-256 位于 `agent_image/requirements.langgraph.lock`。顶层 `langchain` 不是
所选实现的运行依赖，避免引入当前不需要的预制 Agent 和中间件表面。

官方依据：

- LangGraph 把自身定位为低层编排运行时，并明确可以不依赖顶层 LangChain 使用：
  <https://docs.langchain.com/oss/python/langgraph/overview>
- `StateGraph` 以 State、Node、Edge 建模并在编译时接入运行配置：
  <https://docs.langchain.com/oss/python/langgraph/graph-api>
- `ChatOllama` 位于独立 `langchain-ollama` 包，官方列出 tool calling、async 和 token usage 支持：
  <https://docs.langchain.com/oss/python/integrations/chat/ollama>
- LangGraph checkpoint 是框架状态快照；本项目只把它用于 Episode 内恢复辅助，不把
  `StateSnapshot` 当作 TRACE 长期协议：<https://docs.langchain.com/oss/python/langgraph/persistence>

## 2. 供应链与许可证边界

完整闭包的许可证元数据均非空，涉及 MIT、Apache-2.0、BSD-2/3-Clause、MPL-2.0、PSF-2.0 及明确的
双许可证组合，没有未知许可证。37 个 wheel 中 35 个内嵌至少一个 LICENSE/COPYING/NOTICE 文件；
`langchain-core==1.5.3` 和传递依赖 `langsmith==0.10.15` 的 wheel 只有 MIT 元数据、未内嵌许可文本。
因此允许内部施工，但 `5.G2` 的离线 wheelhouse/镜像必须额外携带对应上游 MIT 文本并生成最终 SBOM。

`langchain-core` 会强制安装 `langsmith` 客户端，但 TRACE-G 不使用 LangSmith 服务。正式容器必须显式
关闭 LangSmith tracing，不设置 API key，并通过无公网网络证明运行不依赖该服务。若后续包版本允许
合法移除该传递依赖，可在新的版本审计后缩减；当前不得手工删包破坏依赖闭包。

当前仓库根目录没有项目 LICENSE。内部研究施工可继续，但任何镜像、代码或结果工具的对外分发都被
此问题阻塞，直到项目所有者选择许可证并完成 Ollama、Qwen 权重、基础镜像、系统包和 Python wheel
的总 SBOM/NOTICE。

## 3. 正式进程与网络拓扑

```text
Docker host: Engine + NVIDIA driver/GPU + result storage only
  |
  +-- Controller/Fuzzer container (trusted, owns Docker Socket)
  |     |
  |     +-- per-Episode internal RPC network, no public egress
  |            |
  |            +-- Agent-Qwen container
  |                  +-- PID 1 supervisor / signal reaper
  |                  +-- Ollama, 127.0.0.1:11434 only
  |                  +-- LangGraph Agent Runtime
  |                  +-- Office ToolRegistry/OfficeRuntime/state
  |
  +-- LLM Mutator container (separate identity and state)
```

正式 Agent-Qwen 容器不挂载 Docker Socket、宿主模型目录或宿主业务目录。Controller 与 Agent 的 RPC
使用每个 Episode 独立的 Docker internal 网络，不发布宿主端口；这是当前 HTTP Runtime 的“无公网
等价隔离”，而不是无法通信的裸 `--network none`。Ollama 只绑定容器回环地址，Controller 和其他
Episode 均不能调用它。以后若 RPC 改为 Docker exec/Unix socket，可再把 Episode 收紧为
`--network none`，但不得为追求字面配置破坏可观察的执行控制。

镜像构建时把 Qwen 模型 manifest/blobs 放入只读镜像层，运行时不得 pull。模型 digest、模型目录闭包、
Ollama 版本/二进制、Python lock、Agent Prompt 和最终镜像 digest 都进入 Profile/启动证据。容器以
非 root UID/GID `10001:10001` 运行 Agent 和 Ollama；PID 1 负责启动 Ollama、等待 `/api/tags`、校验
digest、执行受限 warm-up、启动 Runtime，并在取消/超时/退出时终止全部子进程。若 Ollama 不能在该
权限和只读根文件系统下工作，`5.G2` 必须停止并修正镜像目录权限，不能把正式 Agent 改成 root。

## 4. Agent 数据流与 TRACE 适配

代表性执行流：

```text
ExecutionRequest/TestCase
  -> ToolRegistry.enable_office_episode() 冻结初始状态
  -> LangGraph State(messages, turn, submitted, final_answer)
  -> model node: ChatOllama.bind_tools(13 Office ToolSpec + submit)
  -> host model identity gate 校验本容器 /api/tags digest
  -> Qwen AIMessage(tool_calls)
  -> TRACE model/tool-call 事件和调用账本
  -> custom tool node: 既有 ToolSpec 参数校验 + ToolRegistry.execute()
  -> OfficeRuntime 真实改变容器内状态
  -> ToolMessage(真实结构化结果) 回到 messages
  -> conditional edge 回 model，或独占 submit 后 END
  -> 既有 terminal、state digest、recording/replay/fork/coverage 合同
```

不直接采用 LangGraph `ToolNode`：TRACE-G 已有稳定 ToolSpec、授权记录、call ID、结果顺序和状态 digest
合同，自定义 graph tool node 只是把成熟框架调用接到这些既有边界，不重写办公工具。禁止把工具结果
改成自然语言摘要后再回注；Qwen 必须收到与 TRACE 事件对应的结构化结果。

LangGraph state 只在本次 Episode 内控制循环。长期权威工件仍是 TRACE schema 1.2、state codec 2.0、
Replay Manifest、Artifact 和 coverage 输入。LangGraph checkpoint ID 可作为关联字段，但不能替代
TRACE checkpoint digest。LangSmith tracing 必须关闭，所有关键事件由 TRACE-G Collector 产生。

## 5. 保留、替换与测试替身

保留：

- `ExecutionRequest`、`AgentAdapter` 和 `trace_react_v2` backend 标识；
- `ToolSpec`、`ToolRegistry`、`OfficeEpisodeToolRuntime` 和 13 项办公工具；
- TRACE Collector、call ID 账本、状态 digest、terminal 延迟提交；
- recording、strict replay、carrier fork、CoverageInput、风险映射、CoverageStore；
- Campaign、RiskFrontier、公平调度、MutationPlan 子批和恢复。

替换或收窄：

- 正式 Agent 的手写 `for turn` 循环替换为新的 LangGraph `StateGraph` 适配器；
- 正式模型调用改为容器内 `ChatOllama(base_url="http://127.0.0.1:11434")`；现有
  `OllamaReactProvider` 的 digest 校验、有限错误审计等能力应抽成共享边界，不复制两套事实逻辑；
- `OfficeControlProvider`、Fake React 和 workspace control 只保留测试/历史校准入口。正式办公请求
  缺少锁定 Qwen 配置时必须 fail closed，不能再默认回退脚本 Provider；
- Controller 中错误放置的 `langgraph==0.6.11` 在 5.G2 移除，LangGraph 只属于 Agent 镜像。

## 6. 失败传播与停止信号

- Ollama 未启动、模型缺失/digest 漂移、外部 endpoint、Agent 镜像 digest 漂移：
  `configuration_error` 或 `model_digest_mismatch`，暂停 Campaign。
- Qwen 输出无效工具结构、call ID 冲突、工具结果无法配对、LangGraph/TRACE 状态不一致：
  `data_integrity_error`，不得重试吞掉。
- 模型/工具轮次耗尽且无合法独占 submit：`case_failure/agent_no_submit`，该 Episode 结束。
- 白名单 transport/timeout/408/429/选定 5xx：沿既有有界恢复合同；未知异常暂停。
- 取消/超时必须传播到 LangGraph、Runtime 和 Ollama；容器、卷或 GPU 进程残留：
  `systemic_infrastructure_failure`，不能报告测试成功。

以下任一现象要求停止 5.G2/5.G3 并回到架构审查：正式请求仍能触发脚本 action plan；真实工具结果
没有进入下一轮 Qwen；LangGraph 私有对象侵入 TRACE/coverage 数据库；同一 Agent 容器无法以非 root
运行 Ollama 和 Runtime；离线依赖或 Qwen 权重许可证无法闭合；无公网条件下发生运行时下载。

## 7. 退出方案

LangGraph 被限制在新的 `AgentAdapter` 实现内部。ToolRegistry、TRACE、Replay、Coverage 和 Campaign
均不依赖 LangGraph 私有类型；如果锁定版本存在无法修复的正确性、资源或许可证问题，可以替换该
Adapter，而不迁移 TestCase、轨迹或 coverage 数据。退出不允许恢复容器外 action plan，也不允许
把模型移出 Episode 容器；那是产品合同，不是框架选择。

## 8. 5.G1 验收证据

- 官方/PyPI 元数据确认三个直接包均为稳定发行、支持 Python 3.11 且为 MIT。
- pip 针对 CPython 3.11 + Linux x86_64 `manylinux_2_17` 成功解析并下载 37 个 wheel。
- 37 个 wheel 的名称、版本、许可证元数据、内嵌许可文件数量和 SHA-256 已逐项读取；无未知许可证。
- Python 3.11.9 临时隔离环境安装同一精确版本闭包成功；`pip check` 返回
  `No broken requirements found.`。
- 最小动态检查导入三包，构造并编译 `StateGraph`，执行 `1 -> 2` 状态转换，并构造
  `ChatOllama(model="qwen3:8b", base_url="http://127.0.0.1:11434")`；输出
  `1.2.10 1.5.3 1.1.0 graph-ok http://127.0.0.1:11434`。该检查没有调用 Ollama 或真实模型。
- Docker Desktop daemon 当时未运行，因此没有在本机 Linux 容器内安装 lock。Linux CPython 3.11
  wheel 的解析、下载和 hash 已完成；`5.G2` 构建镜像时仍必须以 `--require-hashes` 安装并在镜像内
  再运行 `pip check`/import，不能用本次 Windows venv 代替最终镜像证据。

结论：`5.G1` 通过，可以进入 `5.G2`。通过只代表依赖和架构边界可施工，不代表自包含 Agent-Qwen
镜像、Ollama 进程、GPU、真实 Qwen 或办公 Agent 已经工作。
