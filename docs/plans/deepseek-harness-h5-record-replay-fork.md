# DeepSeek Harness H5：Recording、Strict Replay 与 Fork 详细计划

状态：`H5.0-H5.5 已完成；本机技术门通过`

上游：`docs/plans/deepseek-harness-parallel-agent-plan.md`

## 1. 目标

让 Harness 的正式 Episode 使用现有 recording、strict replay 和 verification-only fork。现有 Office V2
strict replay 明确由 `LangGraphReactRuntime.execute_strict_replay()` 充当验证引擎，fork 后缀明确是
`live_and_record`；本阶段不把它们错误描述为已经 Runtime-neutral，也不新建 Harness replay engine。

## 2. 固定语义

```text
Harness live execution
-> 现有 trace-react-v2 recording + OfficeV2RecordingState
-> producer_runtime_kind/version/composition_digest
-> 现有 LangGraph replay verifier 使用录制决定和可信工具事实重新验证
-> 不启动 Node/Harness/Ollama/Qwen
-> 现有 LangGraph fork engine 从规范 checkpoint 建立不可变 live_and_record 子分支
```

这里的 verification-only 表示子分支不进入 Campaign/Coverage/Finding，不表示 fork 后缀是纯离线执行。
Harness 私有 Session 可以封存为诊断附件，但不参与 strict replay、fork、Coverage 或 Oracle 的权威判断。
不实现 live Harness Session fork，也不把 LangGraph fork engine 冒充 Harness producer。

## 3. 身份最小扩展

新 recording 在现有 Manifest 的摘要保护 metadata 中增加：

```text
producer_runtime_kind
producer_runtime_version
producer_runtime_composition_digest
```

`ReplayManifest.runtime_version` 当前是 replay/recording 包版本（构造位置固定写 `0.2.0`），不能静默改成
producer Runtime version，也不能与新字段混为同一语义。`replay_engine_identity` 在 replay 实际发生后优先
写入现有 `ReplayAuditEvent.data`；只有现有审计无法表达时才最小扩展 ReplayResult。它不写进源 recording
Manifest，因为录制时尚不存在 replay engine。

模型、镜像、工具目录、Case、Prompt 和 execution_id 继续使用现有字段。不得再建立包含这些字段的新聚合模型。
兼容策略必须同时满足：

- 新 Harness 和新 LangGraph 正式录制都写三个 producer 字段；Harness 缺失即明确拒绝。
- 已冻结的现有 LangGraph Manifest 继续按原协议可重放，但结果明确标记 `legacy_unbound_producer_identity`，
  不得进入新的跨 Runtime Campaign/服务器同源结论。
- 不重写历史文件、不从模型摘要猜 Runtime，也不把旧记录冒充新 Harness recording。

## 4. 预计修改区域

```text
src/sandbox/replay/models.py
src/sandbox/replay/recording.py 或现有 Manifest 构造位置
agent_image/app/adapter/deepseek_harness_adapter.py
agent_image/app/replay/replay_adapter.py
agent_image/app/replay/checkpoint.py              # 仅能力字段确实需要时
agent_image/app/office_v2_session.py              # 仅导出 producer 身份引用
tests/unit/test_deepseek_harness_recording.py
tests/unit/test_replay_core.py                    # 只加共享类型解耦断言
tests/integration/test_deepseek_harness_replay.py
```

禁止复制 ReplayEngine、Office codec、Oracle、Coverage 或 fork 业务逻辑；禁止修改 Case/World/Policy。

## 5. 施工步骤

### H5.0 现有引擎语义复核

冻结三项现状：strict replay 显式构造 LangGraph replay verifier；Office fork 通过 AdapterFactory 构造
LangGraph fork engine；fork suffix 是 `LIVE_AND_RECORD`。区分 producer Runtime、replay engine 和 fork
engine，只有确有无关的类型判断才允许删除，不能把删除 `isinstance` 当作阶段目标。

### H5.1 Producer 身份写入

Harness Adapter 把三个 Runtime 来源字段写入现有 determinism/Manifest metadata。字段来自 H3 已交叉验证的
构建锁和实际 Adapter 常量，不能由模型、Case 文本或 `ExecutionRequest` 提供。

### H5.2 Harness recording

把 H4 已有 TraceEvent、模型决定、工具/交互事实、checkpoint、Session state 和 Oracle 工件写入现有格式。
必须先证明 Harness 的多 activity/user-role followup（包括 awaiting_followup 期间的 idle assistant message）
能无损转换为当前 `RecordedModelDecision`、ReactMessage 和 checkpoint 顺序；不能只做到工具 JSON 看起来
相似，也不能为了通过现有 verifier 丢弃活动边界。发生取消、超时或异常时沿用 incomplete recording 语义
并保存已发生成本。

### H5.3 Producer-neutral recording、既有 strict replay engine

重放器根据证据协议与 codec 读取录制，不根据 producer Runtime 构造 live Agent；继续调用现有 LangGraph
replay verifier。重放后保留原 producer 身份，并在 ReplayAuditEvent 中另记 replay engine 身份。若 Harness
录制不能无损转换为该引擎消费的规范决定序列，立即停止并评估提取最小
`OfficeV2RecordedExecutionVerifier`，不得复制整套状态机或伪造通过。

### H5.4 Verification-only fork

显式使用现有 LangGraph fork engine 执行 `live_and_record` 验证后缀。父 Manifest 保留 Harness producer
身份；子 Manifest 通过现有 parent/prefix 引用继承父前缀来源，并把新后缀 producer/fork engine 明确标为
LangGraph。不得把混合前缀/后缀的子记录整体标成 Harness live execution。父记录不可变；子分支不写入
Campaign/Coverage/Finding。不得由父 producer Runtime 选择 fork engine，也不得宣称这是 Harness live
Session fork。

### H5.5 聚焦验收与审查

优先选择 H4 的可信多轮授权链（它同时包含真实状态变化），完成 direct -> recording -> strict replay ->
fork；再篡改一项摘要验证拒绝。若现有冻结 Case 无法让一条 Episode 同时覆盖 user-role followup 和状态变化，
才拆成两条聚焦 Episode，不扩展为矩阵。随后做一次 diff 审查。

## 6. 验收标准

- direct 与 recording 导出的 Oracle、最终状态和去除 execution/acquisition 元数据后的规范事实摘要一致。
- strict replay 不启动 Harness、Node、模型或网络服务。
- replay 结果保留 producer Runtime，且 replay engine 不冒充 producer。
- fork 父记录不变，子 checkpoint/状态/Oracle 可验证；报告明确 Harness 父前缀、LangGraph 后缀 producer/
  fork engine 与 `live_and_record` 语义。
- 篡改 Runtime、模型决定、工具结果或状态摘要中的一个时明确失败。
- 现有 LangGraph 的一条聚焦 replay/fork 合同不回归；不重跑其 Docker 矩阵。

## 7. 代码审查与停止条件

检查是否把 replay/fork engine 误当 producer、是否把私有 Session 当权威、是否为 Harness 复制重放状态机、
是否允许缺 producer 身份进入新 Harness 正式路径、是否在 strict replay 中错误启动模型、是否修改父记录、
是否错误宣称 fork 为纯离线或 Harness live fork。

若 strict replay 必须恢复 Harness 私有 Session、或 verification-only fork 无法从现有 checkpoint 完成，立即
停止并说明真实合同冲突，不降级为 live replay。

## 8. H5.0 复核结论与确认门（2026-08-22）

现有实现存在一个必须先解决的真实顺序冲突：

```text
LangGraph 当前顺序：clarification tool result -> authenticated user message -> next model call
Harness H4 真实顺序：clarification tool result -> assistant idle -> authenticated user message -> next activity
```

`LangGraphReactRuntime` 当前在 control handler 返回后立即把 `follow_up_user_message` 追加到 React messages。
因此若直接把 H4 driver decisions 转成 `RecordedModelDecision`，idle 决策的 `input_digest` 和 checkpoint 消息顺序
必然与现有 strict replay verifier 不同。删除 idle assistant、把回复塞回 MCP result，或忽略 input digest 都违反
H4/H5 已冻结合同。

建议的最小修法是扩展现有 LangGraph replay verifier，而不是新建 Harness replay engine：

1. 新 Harness recording 显式记录 activity index 与 `awaiting_followup -> idle` 边界。
2. 仅当 producer identity 为锁定的 `deepseek_harness` 时，replay verifier 延迟追加 control 返回的可信 user
   message；先消费已记录 idle assistant 决策，再追加该 user message并进入下一 activity。
3. 普通 LangGraph live/recording/replay 顺序保持不变；该模式不能由 ExecutionRequest、模型或 Case 文本开启。
4. Harness recording 构建时用同一窄模式生成现有 checkpoint/decision/tool-record 格式，并逐项对照 H4 Bridge
   事实、最终状态和 Oracle；任何差异失败关闭。

用户已确认该架构决策。实现只为锁定的 Harness producer/recording verifier 开启延迟 followup 窄模式；普通
LangGraph live/recording/replay 顺序保持不变，没有丢失 activity 边界或放宽摘要校验。

## 9. H5 实施结果（2026-08-22）

- 新 Harness 与新 LangGraph recording 都绑定 producer kind/version/composition digest；字段缺失、部分缺失、
  不匹配或被篡改时失败关闭。历史 LangGraph recording 只按
  `legacy_unbound_producer_identity` 兼容，不进入新跨 Runtime 同源结论。
- Harness 真实执行先形成 Bridge 权威事实，再以同一确定性决定序列生成规范 recording；最终状态、Oracle、
  EvidenceBundle 和结果摘要不一致时拒绝录制。
- 可信多轮保持 `clarification result -> assistant idle -> authenticated user message -> next activity`。普通
  LangGraph 时序未改变。
- strict replay 直接使用 LangGraph recorded verifier，不启动 Harness 或模型；replay audit 分开记录源 producer
  与验证引擎身份。
- verification-only fork 保留不可变 Harness 父前缀，子后缀明确记录 LangGraph producer、fork engine 和
  `live_and_record`，不把混合分支冒充 Harness live execution。
- 取消或失败会保存 incomplete recording、已完成 activity/decision 数与 token 用量，不以成功 Episode 结算。

机器证据位于 `agent_variants/deepseek_harness/h5-evidence.json`，文件摘要为
`sha256:fd956f7f158060aa928fe9621df139761e2ed8ed76eec400479c8a4b1d201c70`。本阶段只运行三条 Harness 聚焦测试、五个相邻
LangGraph 选择案例、语法/Ruff/diff 检查；未运行全仓、Docker、Ollama、真实 Qwen、Coverage 或 Campaign。
