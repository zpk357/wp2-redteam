# 真实 Agent 入口与执行证据桥

- 规格 ID：`SPEC-REAL-AGENT-BRIDGE-20260930`
- 状态：`APPROVED`
- 日期：2026-09-30
- 批准日期：2026-09-30
- 适用范围：`error_capable` 场景进入服务器 smoke 和正式比较前
- 上游规格：[`SPEC-SCENARIO-ERROR-20260930`](20260930-error-capable-scenario-and-oracle.md)
- 触发依据：服务器 precheck 报告 `PASS 8 / PARTIAL 9 / BLOCKED 7 / FAIL 0`

## 1. 背景与阻塞

服务器 precheck 已证明材料物化和 Office V2 工具链可以运行，但当前实现仍不能证明真实 Agent 能执行场景。阻塞点如下：

1. `error_capable` 未注册到正式 fixture 入口，无法从现有 Campaign 入口创建真实 Agent Episode；
2. 选择器/计划只保存抽象模型名，不能证明与 Provider 实际服务标签一致；
3. `EpisodeScenarioPlan` 没有可审计的首轮 Agent 输入，无法验证任务文件没有被提前暴露；
4. `run_local_path_probe` 由 Controller 直接调用 `runtime.invoke()`，只能证明工具链可达，不能作为 Agent 执行证据；
5. 工具轨迹、状态快照、交付载荷和 Oracle 结果尚未形成一条可回放的证据链。

没有解决这些问题时，路径探针不得被称为 Agent 成功，`formal_comparison_eligible` 必须保持 `false`。

## 2. 目标

- `RA-01`：将 `error_capable` 注册为正式、独立、可冻结的场景入口，不改变旧 fixture、旧 Campaign 或历史 digest。
- `RA-02`：建立真实 Agent Episode 入口。Controller 只负责准备冻结计划、启动 Agent、转发 Agent 请求并记录结果，不替 Agent 选择工具或填写工具参数。
- `RA-03`：为每个 Episode 固定并保存首轮 Agent 输入的完整 payload digest 和可审计摘要。首轮输入不得包含任务文件 ID、固定文件名、完整任务正文、攻击 ID 或攻击载体正文；允许包含通用安全规则和工具能力说明。
- `RA-04`：保存 Agent 每次工具请求的原始结构化调用、对应 Office V2 `OfficeToolInvocation`、工具结果、前后状态 digest、可见输出和 execution evidence，并保持调用顺序一致。
- `RA-05`：模型身份必须同时记录 Provider、服务标签、版本/摘要和请求模型名。契约必须接受真实 Provider 标签，或保存规范化身份与原始标签的双表示；不能用会拒绝真实服务标签的字段伪造一致性。
- `RA-06`：建立执行证据桥：`plan -> first_input -> Agent request -> OfficeToolInvocation -> OfficeToolResult -> state transition -> delivery observation -> OracleAssessment -> coverage`。每一段通过 episode ID、invocation ID、resource/evidence ID 和 digest 关联。
- `RA-07`：确定性事实仍由工具运行时和 Oracle 产生。Agent、路径选择器和 LLM Judge 不能伪造工具调用、提交、权限、状态、内容事实或覆盖新增。
- `RA-08`：真实 Agent 轨迹支持 `read_only`、`blocked`、`attempted`、`committed` 的证据区分；没有真实 attempted 证据时保持缺失，不把 blocked 或 failed 重标为 attempted。
- `RA-09`：guided/random 的 Agent 执行条件公平：相同模型身份、Provider、工具菜单、场景菜单、预算、提示版本、Oracle 和记录口径；唯一原则差异仍是 guided 消费跨 Episode coverage feedback。
- `RA-10`：只有真实 Agent smoke、证据桥完整性和恢复测试全部通过，才允许把场景提交给正式比较准入。

## 3. 术语和边界

### 3.1 真实 Agent Episode

真实 Agent Episode 是由被测模型自主决定下一步工具调用的执行。Controller 可以发送首轮输入、返回工具结果、实施墙钟/步数预算和保存证据，但不得预先执行路径或替 Agent 生成调用。

### 3.2 首轮输入

首轮输入是 Agent 第一次收到的 system/developer/task/tool-context payload 的完整规范化表示。目录、搜索和读取工具可以暴露任务文件；首轮输入本身不能暴露任务文件答案。

### 3.3 执行事实

执行事实必须来自 Agent 请求、Office V2 runtime 结果、状态差异、交付载荷和 Oracle。选择器理由、Agent 自述和 Judge 结论只能作为辅助说明。

### 3.4 模型身份

模型身份至少包含：`provider_id`、`raw_model_label`、`normalized_model_id`、`provider_version`（如可得）、`model_identity_digest`。规范化不能丢失原始标签，也不能把不同 Provider/标签折叠为同一身份。

## 4. 必须实现的入口和数据流

```text
registered fixture
  -> frozen EpisodeScenarioPlan
  -> first-input payload + digest
  -> real Agent loop
  -> Agent tool request
  -> OfficeV2ToolRuntime.invoke(request)
  -> OfficeToolResult + state/evidence
  -> delivery observation
  -> deterministic Oracle
  -> behavior/risk/joint coverage
  -> guided feedback or random isolated input
```

### 4.1 Fixture 注册

- `error_capable` 必须出现在正式 fixture registry 中，并具有独立 fixture ID、版本、manifest/freeze digest 和加载函数；
- registry 加载不能修改既有 fixture 的顺序、digest、权限或历史结果；
- 旧 fixture 回归测试必须在新入口加入后仍通过。

### 4.2 Agent 适配器

- 新增明确的 Agent adapter/protocol，输入为首轮 payload，输出为结构化工具请求或终止决定；
- adapter 可以调用真实 Provider，也可以在本地使用确定性 fake Agent 做契约测试；fake Agent 的结果不得冒充正式实验结果；
- Controller 只能校验请求、调用 runtime 并把结果返回 Agent；禁止存在按 `path_id` 写死的工具序列作为真实 Agent 执行入口；
- 每次请求必须绑定当前 episode、调用序号、工具名、参数 digest 和 Agent 输出 digest。

### 4.3 首轮输入审计

- 生成首轮输入时只放置通用规则、业务目标的非答案部分和工具说明；
- 任务文件 ID、固定文件名、攻击模式/载体 ID、受限事实正文不得出现在首轮 payload；
- 通过字符串、结构化字段和 digest 对照测试验证禁止项；
- Agent 通过 `list_directory`、`search_files` 或公开索引追踪发现任务文件的证据必须出现在后续工具轨迹中。

### 4.4 工具与 Oracle 桥接

- 真实 Agent 的每个工具请求必须经过 Office V2 runtime，不能绕过 policy、provenance 或状态事务；
- 轨迹结束时根据真实工具结果生成 `DeliveryObservation`，记录来源 invocation/evidence ID；
- Oracle 只使用已保存的工具结果、状态、受众和内容事实判断阶段与 FindingKind；
- Oracle 结果必须能回指至少一个相关工具结果或明确记录“无交付载荷/无提交证据”。

### 4.5 模型身份和公平性

- Agent 与 selector 使用同一个模型身份结构，而不是只比较一个自由字符串；
- smoke 中必须保存 Provider 返回的实际身份摘要；
- guided/random 的首轮 Agent 输入、工具能力、预算和运行环境相同；反馈只允许出现在 selector 输入，不得进入 Agent 任务正文形成额外提示差异。

## 5. 验收标准

- `RA-AC-01`：`error_capable` 可由正式 registry 加载，并能生成独立冻结计划；旧 fixture 回归不变。
- `RA-AC-02`：至少一个本地 fake Agent 和一个服务器 Provider smoke 通过同一 Agent adapter；两者都留下完整首轮输入和工具请求收据。
- `RA-AC-03`：首轮输入禁止项检查通过，且任务文件只能在后续真实工具结果中出现。
- `RA-AC-04`：Controller 不再通过 `path_id` 直接执行预设路径；真实 Agent 请求决定实际工具调用。
- `RA-AC-05`：至少一条真实 Agent Episode 完成任务发现、读取和合法工具动作；至少一条 Episode 产生确定性 blocked 或 committed 结果，均可回放。
- `RA-AC-06`：每个 Oracle finding 都能关联工具/状态/载荷证据；没有证据的 attempted 保持未定。
- `RA-AC-07`：Provider 原始模型标签、规范化身份和版本摘要可回读，Agent/selector 身份一致性检查通过。
- `RA-AC-08`：guided 的反馈进入下一集 selector，random 不读取跨 Episode feedback；两臂其余执行条件完全一致。
- `RA-AC-09`：中断、工具失败、预算耗尽和恢复不会重复提交、篡改 plan 或补造成功结果。
- `RA-AC-10`：上述验收全部有证据后，才可将 `formal_comparison_eligible` 从 `false` 提交复审；本 SPEC 本身不自动批准正式实验。

## 6. 非目标

- 不修改旧 fixture、旧 Campaign、旧 replay 或历史结果；
- 不把本地 fake Agent 当成真实模型结果；
- 不为了制造 attempted 或违规而放宽工具权限、改变 Oracle 或伪造状态；
- 不在本规格批准和对应 TASK 完成前启动正式 guided/random Campaign；
- 不把 LLM 自述、选择器理由或 Judge 判断当作确定性执行事实。

## 7. 失败处置

以下任一项失败时，保持 `formal_comparison_eligible=false`，并在 precheck 中标为 BLOCKED/FAIL，不能折算为安全结果：

- fixture 未注册或 digest 不稳定；
- 首轮输入泄露任务答案；
- Controller 代替 Agent 发起工具调用；
- Provider 身份无法与 Agent/selector 对齐；
- 工具结果无法关联到状态、交付载荷或 Oracle；
- 恢复造成重复提交或证据覆盖；
- guided/random 执行条件不公平。

## 8. 用户确认

- [x] 接受将本规格作为 `SPEC-SCENARIO-ERROR-20260930` 的补充规格；
- [x] 接受本地先实现 fake Agent 契约测试，再使用真实 Provider 做服务器 smoke；
- [x] 接受 Provider 原始标签与规范化身份双表示，而不是继续限制为单一 `Identifier` 字符串；
- [x] 接受在真实 Agent 证据桥完成前不进入正式 Campaign。

批准依据：用户于 2026-09-30 对 `SPEC-REAL-AGENT-BRIDGE-20260930` 作出批准并指示按
`SPEC -> TASK -> CODE` 规则执行。四项确认按提议方案全部接受。

批准不改变 §6 非目标：本规格不自动批准正式实验；`formal_comparison_eligible` 仍为 `false`，
只有在 §5 全部验收项取得证据后才提交复审。
