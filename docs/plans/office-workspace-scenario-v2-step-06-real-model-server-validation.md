# Office Workspace V2 第六步详细计划：真实模型连续反馈 Campaign 验收

状态：`修订后已冻结，尚未施工`

冻结日期：2026-08-17

用户决定：第六步正式被测 Agent 和正式 LLM Mutator 均使用
`qwen3.5:27b-q4_K_M`。`qwen3:8b` 不再作为正式能力模型；旧 G5 服务器包和其中写死的
`qwen3:8b` 身份只保留为历史证据，不得改名复用。

本计划承接第五步已经通过的确定性工程闭环。第五步证据为
`reports/local-acceptance/office-v2-step5/stage5-loop-evidence.json`，摘要
`sha256:2df3b5f23ecc33c14d116bd6d6efd1f9177fd5f5b0182465df8b32ee73bde5a1`。

## 1. 本步要证明什么

第五步已经用确定性替身证明以下工程链路成立：

```text
候选准备
-> 独立 Episode 执行
-> 轨迹、状态和 Oracle 事实封存
-> Coverage/Corpus/Frontier/预算原子结算
-> 下一代读取最新 feedback
-> 暂停、恢复、strict replay 和 verification-only fork
```

第六步不重跑一套昂贵的演示矩阵，而是把真实模型接入同一条正式 Campaign：

1. 真实 Qwen Mutator 根据冻结 MutationPlan 和最新 Coverage feedback 生成候选；
2. Controller 在宿主可信边界验证候选，非法候选只记录拒绝和成本，不启动 Agent；
3. 合法候选进入全新的隔离 Agent Episode；
4. 真实 Qwen Agent 读取实际工具结果，自主选择后续工具、参数和提交时机；
5. Oracle 从实际工具调用和状态变化计算事实，Coverage 生成下一代 feedback；
6. 第二代开始必须消费上一代已提交的 feedback；
7. 同一个 Campaign 可以暂停、恢复并连续运行 20、30 或最多 50 代，直到饱和、预算耗尽或出现明确终态。

本步分成两个真实模型门：

- **2 代接通门**：证明真实 Mutator -> Controller -> 真实 Agent -> Oracle -> Coverage -> 下一代 Mutator 的因果链确实接通；
- **连续探索门**：在同一 Campaign 上恢复续跑，默认至少观察到 20 代上限，并可按覆盖收益继续到 30 或 50 代。

通过后只能声称：

> 在冻结的 Office Workspace V2、`qwen3.5:27b-q4_K_M`、服务器硬件和推理配置下，真实 Agent 与真实
> Mutator 的连续反馈 Campaign 能够运行、恢复、结算并产生可复算的行为与覆盖证据。

不得扩大为“模型普遍安全”“发现了所有风险”“50 代等于完整覆盖”或“已验证真实 Microsoft 365”。

## 2. 为什么不再单独运行 24 + 24 服务器基线

冻结的 24 个 Clean Case 和 24 个代表攻击案例仍然是场景资产，不能删除或改变，但它们不再组成一套
独立的付费服务器矩阵：

- 复用前序阶段已经冻结并通过的 48 个案例证据；本步不重复枚举执行全部案例，只检查第六步直接触及的身份、物化接口和运行合同；
- 服务器预检最多运行一个 clean smoke，确认真实 Agent 的工具协议和正常任务路径；
- 攻击目标的公平暴露由正式 Scheduler 在同一个真实 Campaign 的早期 generation 中完成；
- 早期 generation 同时是 baseline phase 和连续探索的一部分，不在 Campaign 前重复执行；
- 每个已注册且可达目标仍必须得到已提交 Episode，或者保存稳定不可达/不兼容原因；
- 达到 `baseline_complete` 后，同一 Campaign 自然进入 adaptive phase，不新建第二套运行逻辑。

这样保留 SPEC 的公平性和不可挑选展示要求，同时把服务器费用花在真正要验证的反馈循环上。

## 3. 明确不做什么

本步不做：

- 不重新设计或扩充冻结的场景、24 个 Clean Case、24 个代表攻击案例、12 个攻击目标或 6 个复合目标；
- 不修改 Coverage、Corpus、Risk/Behavior Frontier、Scheduler、MutationPlan、Oracle 或第五步状态机语义；
- 不引入 GLM-5.2、云端模型 API、外部 Ollama endpoint、宿主模型目录或公网推理；
- 不允许用 `qwen3:8b`、`qwen3.5:27b` 或其他模型静默替代冻结模型；
- 不实现 LLM-as-Judge、黄金集、主动学习或评分漂移；
- 不把模型自报的风险、授权或成功当作事实；
- 不为了得到攻击成功结果放宽权限、完整性、任务保持或候选校验规则；
- 不并发运行 Agent 和 Mutator 抢占同一张 GPU；
- 不强制跑满 50 代，也不把代数当作覆盖率；
- 不在服务器重复第五步已经验证的所有 deterministic replay/fork/error 分支；
- 不删除旧服务器包、历史失败归档或用户已有工作区修改。

## 4. 冻结模型与推理配置

### 4.1 模型选择

| 角色 | 冻结模型 | 量化 | 用途 |
|---|---|---|---|
| 被测 Agent | `qwen3.5:27b-q4_K_M` | GGUF Q4_K_M | 多轮工具选择、参数推理和提交 |
| LLM Mutator | `qwen3.5:27b-q4_K_M` | GGUF Q4_K_M | 只生成 Scheduler 冻结 payload slot 的内容 |

两个角色可以复用同一份上游权重内容，但必须使用不同角色身份、镜像、Prompt、配置、容器和预算记录。
Docker 镜像可以继承同一只读模型基础层以避免重复存储权重；这不允许两个运行中容器共享进程、上下文、
可写目录或推理 endpoint。

### 4.2 推理配置

首个权威服务器批次冻结以下配置：

| 配置 | Agent | Mutator |
|---|---:|---:|
| `num_ctx` | 8192 | 8192 |
| `num_predict` 上限 | 4096 | 2048 |
| `temperature` | 0.2 | 0.7 |
| `top_p` | 0.8 | 0.8 |
| `top_k` | 20 | 20 |
| seed 来源 | Episode identity digest | MutationPlan digest |
| thinking | 启用并验证工具调用兼容 | 关闭，强制结构化候选 Schema |
| 并发模型实例 | 1 | 1 |

Agent thinking 文本不是事实证据，不进入 Coverage、Oracle、Finding 或授权判断。任何参数变化都会形成
新的模型运行身份；预检后不得在同一 Campaign 内改变上下文、采样、thinking、Ollama 版本或模型 tag。

### 4.3 不允许自动降级

`qwen3.5:27b-q4_K_M` 的 Ollama 权重约 17GB，目标是在 RTX 4090 24GB 上完整驻留并为 8192 context
保留运行空间。此前候选 `qwen3.5:35b-a3b-int4` 已由 Ollama Linux registry 明确拒绝为 macOS 专用，
不得在 Linux/NVIDIA 服务器包中继续使用或改名冒充。27B 是稠密模型；Qwen 官方对比中其多数指令遵循、
工具调用、长上下文、代码和搜索 Agent 指标不低于 35B-A3B，因此本次变更不是退回 8B 级能力模型。
若出现以下任一情况，第六步状态为
`blocked_model_runtime`，保留失败证据并停止，不得自动切换模型：

- 模型无法完整加载或发生 OOM；
- 推理发生未声明的 CPU offload；
- 8192 context 的代表最大请求无法完成；
- Ollama 不支持该模型的 tool calling、thinking 或结构化输出合同；
- 模型内容摘要与锁不一致；
- warm-up、超时、取消或退出后仍有 GPU 进程残留。

改用其他模型、增加 GPU 或改变推理引擎都属于新的用户决策，必须建立新身份和修订计划。

## 5. 模型与运行身份

首次下载和离线封包时生成 `Stage6ModelLock`，至少锁定：

- canonical model name、上游 registry、完整 Ollama manifest/config/layer digest；
- 模型归档 SHA-256、归档字节数、量化类型和 chat template digest；
- Ollama 镜像 digest、二进制版本和运行参数；
- Agent/Mutator 各自 Prompt、Provider 和推理配置 digest；
- Agent、Mutator、Controller 镜像 ID、RepoDigest 和离线 tar SHA-256；
- Office V2 场景、ToolSpec、Oracle、Coverage、Corpus、Scheduler、Mutation 和 Campaign 身份摘要；
- 服务器 GPU 型号、GPU UUID、显存、驱动、CUDA、Docker 和 NVIDIA Container Toolkit 版本。

短 tag、页面短 hash 或文件名不能代替完整摘要。获取完成前可以标记 `pending_acquisition`，权威运行前
必须全部变为可校验值。

## 6. 单代真实数据流

服务器保持单张 RTX 4090 24GB，所有模型步骤严格串行：

```text
Scheduler 选择目标并冻结 MutationPlan
-> 启动独立 Mutator 容器
-> Qwen3.5 读取最新 feedback，生成一个 payload candidate
-> Controller 校验并结算非法候选
-> 合法候选启动全新 Agent Episode
-> Qwen3.5 自主调用 Office V2 工具
-> Oracle 检查真实轨迹、授权和状态变化
-> Coverage/Corpus/Frontier/预算原子结算
-> 提交 generation checkpoint 和最新 feedback
-> 下一代
```

Mutator 退出并完成清理后，Agent 才能获得 GPU。Agent 和 Mutator 不得相互访问网络、进程、Prompt、
历史消息或可写目录。每个合法候选使用新的 Agent Episode；非法候选不创建 Episode。

## 7. 连续 Campaign 和付费运行策略

### 7.1 固定上限，不强制跑满

正式 Campaign 配置：

- `max_generations = 50`；
- `episode_budget = 50`，另有独立 token、时间和费用上限；
- 每一代原子结算后保存可恢复 checkpoint；
- 每 5 代导出一次进度、Coverage 增量、目标暴露、失败分类和累计成本；
- generation 和 Episode 按现有第五步合同计数，不另造含义；
- 候选拒绝、Provider/基础设施错误和清理失败不得伪装成有效 Episode 或无增益观察。

50 是最大预算，不是成功条件。允许并要求在以下状态正确停止：

- `saturated`：baseline complete 后满足冻结的有效无增益窗口；
- `budget_exhausted_incomplete`：任一预算先耗尽但仍有未覆盖前沿；
- `paused`：人工暂停、身份漂移、未分类异常或 ambiguous receipt；
- `cancelled`：明确取消并完成清理。

### 7.2 分批租用服务器

同一代码、模型、Prompt、配置和镜像摘要下按以下里程碑恢复续跑：

1. **2 代**：接通门。第二代必须引用第一代 settlement 产生的 `feedback_digest`；
2. **10 代**：检查稳定性、目标公平暴露、候选合法率、Agent 有效率和单位成本；
3. **20 代**：连续探索的首个正式观察点；
4. **30 代**：仅当仍有可达覆盖增长或 baseline 尚未完成时继续；
5. **50 代**：最终预算上限，仍未完成则如实报告 `budget_exhausted_incomplete`。

每个里程碑都使用正式 `resume`，不得新建 Campaign、重置 Coverage 或重复已结算费用。若第 17 代已合法
进入 `saturated`，第六步可以在 17 代停止；若第 50 代仍有空白，也不能声称饱和或完整覆盖。

### 7.3 接通门的判定

2 代接通门必须同时证明：

- 两代均由真实 Mutator 调用产生，且至少一个候选合法并完成真实 Agent Episode；
- 第二代 MutationPlan 明确绑定第一代最新 `feedback_digest`；
- 第一代 Oracle/Coverage 已原子提交后才创建第二代；
- 第二代计划或候选能解释其使用的覆盖空白、父执行或调度上下文；
- 实际 Coverage 只来自工具轨迹、可信交互、授权和状态变化；
- stop/resume 不重复扣费、不重复 Finding、不分叉 generation。

如果两代中因模型输出导致候选被合法拒绝，可以在同一 2 代接通批次之后恢复追加少量 generation，直到
得到至少一个有效 Agent Episode；拒绝本身保留为真实结果，不能重写或删除。

## 8. 本地与服务器各自验证什么

### 8.1 本地免费验证

不加载 27B 模型，使用已有 fake/scripted transport 验证：

- 全部 24 Clean Case 和 24 代表攻击案例可枚举、物化、校验并绑定冻结身份；
- 12 个目标、6 个复合目标、入口兼容性和不可达原因仍完整；
- 真实运行模块与 CLI/测试调用同一 Campaign Runtime；
- 2/10/20/30/50 generation 上限、每代 checkpoint、每 5 代报告和 resume 幂等；
- 候选拒绝、超时、OOM 分类、ambiguous receipt、预算耗尽和饱和终态；
- GPU 租约串行合同、容器 owner 标签和精确清理规则；
- 服务器包内容、双层摘要、模型锁 Schema 和失败归档；
- 下载后的 recording/Manifest/strict replay 可离线验证。

本地验证结构和控制逻辑，不冒充真实 Qwen 行为。

### 8.2 服务器真实模型预检

付费 Campaign 前只运行最小预检：

- 一个 Mutator 结构化输出请求；
- 一个 clean Agent smoke Episode；
- 模型完整加载、8192 context、无 OOM、无未声明 CPU offload；
- Agent tool call、ToolMessage 回灌和 Mutator Schema 均兼容；
- 记录 cold start、warm-up、耗时、token、峰值显存和清理证据；
- 超时/SIGTERM 可以终止，容器、临时卷和 GPU 进程零残留。

预检失败只修复模型适配、运行或打包问题，不启动正式 Campaign。

### 8.3 服务器真实 Campaign

预检通过后只运行第 7 节的同一个连续 Campaign。其早期 generation 由冻结 Scheduler 公平分配目标，
自然完成 baseline exposure；不人工挑选“容易成功”的攻击，也不单独运行 24 个攻击案例矩阵。

攻击没有实现、payload 未观察/未使用或被权限阻止都是有效行为结果。模型协议失败、证据不完整、未分类
异常或清理失败不是有效行为结果，必须按失败合同暂停或结算。

### 8.4 Replay 与 Fork

- 服务器只需封存 recording 和完整自包含工件；
- 下载后在本地对 Finding、晋升风险种子和至少一个普通 Episode 做 strict replay；
- 至少一个真实模型 recording 必须证明 strict replay 不调用模型也能重建相同事实；
- verification-only fork 的工程合同已在第五步 Docker 验证，不因第六步重复付费；
- 只有真实模型接入改变 checkpoint/recording 公共格式时，才追加一个服务器 fork 兼容性检查；
- fork 始终不得写入 Campaign、Coverage、Finding、Corpus、预算或 generation。

## 9. 失败分类与停止条件

| 失败 | 行为 |
|---|---|
| 模型/镜像/Prompt/目录 digest 漂移 | 立即暂停，不重试 |
| OOM、CPU offload、GPU 残留 | `blocked_model_runtime`，停止正式 Campaign |
| Tool call 或结构化输出协议不兼容 | 修复适配后重做预检 |
| 明确 transport/timeout/选定 5xx | 同一 attempt 有界重试，累计全部成本 |
| 输出截断 | 按既有合同有界重试，不扩大 token 上限掩盖问题 |
| 候选不合法 | Preparation 拒绝并结算，不创建 Agent Episode |
| Agent 未 submit | 保留轨迹并按现有失败合同结算 |
| 清理失败 | 系统性失败，不能报告该 Episode 成功 |
| 不明确副作用窗口中断 | Campaign 暂停，依据 sealed receipt 处置 |
| 攻击未实现或被权限阻止 | 有效行为结果，不是基础设施失败 |
| Judge 缺失 | 本步预期状态，不影响事实 Oracle |

同一错误连续出现时停止扩大代数，先修根因。不得针对单个案例添加特殊 Prompt、映射或白名单。

## 10. 权威证据

服务器输出通过或失败归档，至少包含：

- `stage6-model-lock.json`；
- `stage6-server-host.json`；
- `stage6-preflight.json`；
- `stage6-campaign-progress.jsonl`，每 5 代一个不可覆盖快照；
- `stage6-campaign-report.json`；
- `stage6-replay-report.json`；
- Campaign SQLite、sealed recordings、Manifest、ReplayResult 和必要 Finding；
- 每次模型调用的 role、Prompt/config/model digest、token、耗时、失败分类和响应 digest；
- 每个 generation 的计划、输入 feedback、候选结算、Episode/无 Episode 原因和输出 feedback；
- 每个 Episode 的场景、Agent、工具、Oracle、Coverage 和最终状态摘要；
- 容器、卷、网络和 GPU 清理证据；
- 全归档内容清单和 SHA-256。

本机下载后离线运行独立验证器，重新计算摘要、Manifest、Replay、Coverage identity 和归档清单。服务器
脚本生成的 `passed=true` 不能替代本机复核。

最终证据目标路径：

```text
reports/server-downloads/office-v2-step6-qwen35-27b/<campaign-id>/
reports/server-downloads/office-v2-step6-qwen35-27b-results.tar.zst
reports/server-downloads/office-v2-step6-qwen35-27b-results.tar.zst.sha256
```

## 11. 通过标准

第六步只有同时满足以下条件才通过：

1. 模型确为 `qwen3.5:27b-q4_K_M`，完整 manifest/layer/archive digest 匹配；
2. Agent 和 Mutator 的角色、容器、Prompt、预算和 Provider identity 分离；
3. 两个模型角色串行使用唯一 GPU，无外部 endpoint、宿主模型挂载或公网；
4. 前序 48 个冻结案例证据身份保持不变，第六步直接触及的结构、身份和物化接口聚焦检查通过；
5. 最小服务器预检通过，真实 Agent 能消费工具结果并自主产生后续调用；
6. 真实 Mutator 只能生成冻结 slot 内容，候选经宿主校验后才能执行；
7. 2 代接通门证明第二代消费第一代已提交 feedback；
8. 同一 Campaign 可以按里程碑恢复续跑，至少到达 20 代上限或在此之前合法进入 `saturated`；
9. baseline phase 由 Campaign 早期 generation 公平推进，每个可达目标有提交 Episode 或稳定不可达原因；
10. stop/resume 不重复扣费、不分叉、不重复 Finding/Coverage；
11. 至少一个真实 recording 在本地 strict replay 后事实和最终状态一致；
12. 事实结论来自工具轨迹、授权、状态和 Oracle，不来自模型自报；
13. 服务器归档和本机离线验证器通过，双层摘要一致；
14. 所有本轮容器、卷、网络和 GPU/Ollama 进程零残留；
15. 报告区分模型行为、合法拒绝、系统失败、不可达、饱和和预算不足；
16. 聚焦测试、相关最小 Docker 门、Ruff 和 `git diff --check` 通过。

如果 20 代前因覆盖饱和而停止，连续探索门仍可通过；如果预算先耗尽，则工程闭环可以通过，但 Campaign
结果必须是 `budget_exhausted_incomplete`，不能写成覆盖完成。

## 12. 分步施工计划

### 6.0 边界、资产和身份锁

- 实现 `Stage6ModelLock`、角色配置和上游摘要绑定；
- 生成旧 G5/qwen3:8b 资产处置表；
- 让真实模型入口在完整锁缺失时封闭拒绝。

### 6.1 Qwen3.5 协议探针

- 用注入 transport 和冻结响应验证 thinking、tool call、structured output 与错误分类；
- 准备服务器获取/校验模型的脚本，不在本机加载 27B；
- 不运行 Campaign。

### 6.2 Agent 与 Mutator 镜像

- 建立可去重的只读模型基础层；
- 构建自包含 Agent 镜像和独立 Mutator 镜像；
- 锁定非 root、回环 Ollama、只读根、无宿主模型挂载和角色专用入口。

### 6.3 正式 V2 真实模型运行模块

- 将第五步公共 Campaign Runtime 接到真实 Mutator 和 Docker Agent 执行适配器；
- CLI 与测试调用同一模块，不复制演示逻辑；
- 保留 scripted/RuleBased 与真实模型身份隔离。

### 6.4 连续运行、预算与恢复

- 接入 `max_generations=50`、Episode/token/时间/费用预算；
- 每代 checkpoint、每 5 代报告，并验证 2/10/20/30/50 的同 Campaign resume；
- 把实际 token 和耗时纳入现有结算；
- 实现 Mutator 清理完成后 Agent 才能获取 GPU 的串行租约；
- 验证中断、超时、OOM、残留和 ambiguous receipt 的暂停语义。

### 6.5 本地完整性门与服务器包

- 复用已冻结的 48 案例权威证据，不在本地重复运行完整矩阵；只运行第六步直接受影响的聚焦合同检查；
- 生成新的 Office V2 第六步包，不覆盖旧 G5 包；
- 加入 staging、preflight、run/resume、progress export、failure archive 和离线验证脚本；
- 完成包内容与双层摘要检查。

### 6.6 服务器最小预检

- 获取并锁定 `qwen3.5:27b-q4_K_M`；
- 完成 Mutator 请求、clean Agent smoke、显存/协议/清理门；
- 失败只修复预检，不启动正式 Campaign。

### 6.7 真实 2 代接通门

- 启动正式 Campaign 并提交前两代；
- 证明第二代消费第一代 feedback；
- 主动 stop/resume 一次，确认无重复结算。

### 6.8 连续 20-50 代 Campaign

- 同一 Campaign 先恢复到 10 代，再到 20 代；
- 覆盖仍增长或 baseline 未完成时再恢复到 30/50；
- 每 5 代检查收益、成本、失败率和目标公平性；
- 饱和就停止，预算耗尽就如实报告 incomplete。

### 6.9 下载、Replay、归档和本机复核

- 下载 sealed recordings 和 Campaign 工件；
- 本机完成代表性 strict replay、摘要与 Coverage 身份复算；
- 仅在公共 recording/checkpoint 格式变化时追加 fork 兼容检查；
- 核对服务器零残留并更新最终项目记忆。

## 13. 验证节省策略

- 6.0-6.5 只运行直接受影响的本地聚焦测试和最小 Docker 协议路径；
- 本机 6GB GPU 不尝试加载 27B，不制造无意义 OOM；
- 服务器预检通过前不启动 Campaign；
- 不运行独立 24 clean + 24 representative 服务器矩阵；
- 同一身份摘要下通过的里程碑不重复运行，只用 `resume` 继续；
- 服务器按 2 -> 10 -> 20 -> 30 -> 50 分批付费，覆盖饱和即停止；
- strict replay 尽量下载后在本地执行，不消耗 GPU；
- 不重建 Stage 2-8 和第五步已冻结且身份未变化的证据。

## 14. 冻结决定

以下决定随本修订计划冻结，施工时不得自行改变：

1. 正式 Agent 和 Mutator 都使用 `qwen3.5:27b-q4_K_M`；
2. Agent/Mutator 角色严格隔离并串行使用单张 RTX 4090；
3. 初始权威 context 为 8192，不自动扩大；
4. 模型不适配或显存不足时阻塞，不静默降级；
5. 48 个冻结案例复用前序冻结证据，本步不重复本地矩阵，也不建立独立付费服务器矩阵；
6. 服务器只保留最小预检、2 代接通门和同一 Campaign 的连续探索；
7. Campaign 最大 50 代，按 2/10/20/30/50 恢复，饱和可提前停止；
8. 早期 generation 承担公平 baseline exposure，baseline complete 后同一 Campaign 进入 adaptive；
9. 真实模型不改变 Oracle、Coverage、Corpus、Scheduler、Mutation 或 Campaign 事实合同；
10. verification-only fork 不写入 Campaign，且默认复用第五步工程证据；
11. Judge、黄金集、主动学习和漂移继续冻结；
12. 第六步结论绑定本次模型、硬件和配置，不推广到其他模型或真实 Microsoft 365。
