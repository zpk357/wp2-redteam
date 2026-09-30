# 真实 Agent 入口与执行证据桥实现

- 任务 ID：`TASK-REAL-AGENT-BRIDGE-20260930`
- 状态：`IN_PROGRESS`
- 对应 SPEC：[`SPEC-REAL-AGENT-BRIDGE-20260930`](../specs/20260930-real-agent-entry-and-evidence-bridge.md)（`APPROVED`）
- 上游 TASK：[`TASK-SCENARIO-ERROR-20260930`](20260930-error-capable-scenario-task.md)
- 日期：2026-09-30
- 触发依据：服务器 precheck 报告 `PASS 8 / PARTIAL 9 / BLOCKED 7 / FAIL 0`
- 目的：为 `error_capable` 场景建立真实 Agent 入口与可回放执行证据桥，使其从"契约与离线探针版"进入可做真实 Agent smoke 的状态。

## 1. 需求映射

| TASK 需求 | SPEC 需求 | 服务器 precheck 对应项 |
|---|---|---|
| 场景注册为正式 fixture | `RA-01` | R.1（BLOCKED） |
| Agent adapter 与 Controller 边界 | `RA-02` | 4.2（BLOCKED）、R.1 |
| 首轮输入审计 | `RA-03` | 3.2（BLOCKED） |
| 工具请求/结果/状态证据记录 | `RA-04` | 2.2、4.1 |
| 模型身份双表示 | `RA-05` | 1.5（PARTIAL） |
| 执行证据桥 | `RA-06` | 3.3、5.3 |
| 确定性事实只由 runtime 与 Oracle 产生 | `RA-07` | 5.1、7.1 |
| 四阶段证据区分 | `RA-08` | 5.1、5.2 |
| 两臂公平性 | `RA-09` | 6.1、7.1（BLOCKED） |
| 准入门槛 | `RA-10` | 报告第七节 |

## 2. 前置依赖与被依赖项

### 前置依赖

- 已批准的 `SPEC-REAL-AGENT-BRIDGE-20260930`；
- 现有的 `ReactModelProvider` 契约、`OllamaReactProvider` 与 `FakeReactProvider`（`agent_image/app/agent/`）；
- 现有的 `OfficeV2ToolRuntime`、`OfficeToolInvocation`、`OfficeToolResult` 与 `office_v2_tool_definitions()`；
- 现有的 `error_capable` 场景契约与 `assess_delivery` Oracle；
- 服务器 smoke 运行根 `/opt/trace-g-wp2-redteam-smoke-20260930`。

### 被依赖项

- `error_capable` 的正式服务器 Agent smoke；
- `formal_comparison_eligible` 的复审；
- 后续正式 guided/random Campaign TASK。

## 3. 范围

### 包含

1. 新增 `error_capable` 的独立 fixture 身份、版本、manifest 摘要与加载函数，并进入独立 registry；
2. 新增 Agent adapter 协议与 Controller 循环：Controller 只准备冻结计划、发送首轮输入、校验请求、调用 runtime、回传结果、保存证据；
3. 首轮输入 payload 的构造、摘要与禁止项审计，并在首次工具调用前绑定到 runtime；
4. 逐次记录 Agent 原始请求、参数摘要、`OfficeToolInvocation`、`OfficeToolResult`、前后状态摘要与 execution evidence，保持调用顺序；
5. 模型身份双表示（原始标签 + 规范化 ID + 版本 + 身份摘要）；
6. 执行证据桥：工具结果 → 交付观察 → 确定性 Oracle → 行为/风险/联合覆盖；
7. 四阶段（`read_only`/`blocked`/`attempted`/`committed`）由真实工具结果派生，缺证据时保持未定；
8. 本地 fake Agent 契约测试与旧 fixture 回归测试；
9. 服务器真实 Provider smoke 脚本与前置检查更新。

### 不包含

- 不修改旧 fixture、旧 Campaign、旧 replay 或历史结果；
- 不启动正式 guided/random Campaign；
- 不把 fake Agent 的成功当作正式实验结果；
- 不为了让 `attempted` 出现而放宽工具权限、修改 Oracle 或伪造状态；
- 不把选择器理由、Agent 自述或 Judge 结论当作确定性执行事实；
- 不在本任务内开放 `formal_comparison_eligible = true`。

## 4. 预计修改区域和数据流

### 新增

- `src/sandbox/scenarios/error_capable_registry.py`：fixture 身份、manifest、freeze digest、registry 与加载函数（`RA-01`）；
- `src/sandbox/scenarios/error_capable_identity.py`：模型身份双表示（`RA-05`）；
- `src/sandbox/scenarios/error_capable_agent.py`：adapter 协议、首轮输入、Controller 循环、轨迹记录（`RA-02`/`RA-03`/`RA-04`）；
- `src/sandbox/scenarios/error_capable_bridge.py`：交付观察桥与阶段派生（`RA-06`/`RA-07`/`RA-08`）；
- `scripts/probe_error_capable_agent.py`：本地 fake Agent 与服务器 Provider smoke 共用入口。

### 允许的修正（由服务器 smoke 证据触发）

- 允许修正 `error_capable.py` 的任务材料，使每个任务族声明的输入事实真实存在；不改变旧 fixture、旧 Campaign 或历史结果。
- 允许修正 Agent 轨迹的停止原因和结果字段，区分最终回合无工具调用、全程无动作、截断、预算耗尽和已发生动作后的自然停止。
- 允许修正执行证据桥的聚合语义，区分工作区写入、外部交付和任务完成，并保留每一次交付调用的 Oracle 结果。

### 不改动

- `src/sandbox/scenarios/structured_v1/fixtures/`（旧 fixture 顺序与 digest 不变）。
- `src/sandbox/scenarios/error_capable_local.py`（保留为离线探针；其 `run_local_path_probe` 明确不被当作 Agent 执行证据）；

### 数据流

```text
ERROR_CAPABLE_FIXTURES[fixture_id]
-> frozen EpisodeScenarioPlan
-> FirstInput payload + digest
-> runtime.bind_agent_visible_context(agent_context_digest, system_prompt_digest)
-> AgentAdapter.step(messages, tools, seed) -> ReactTurn
-> AgentToolRequest(episode, sequence, tool_name, arguments_digest, agent_output_digest)
-> OfficeV2ToolRuntime.invoke(request)
-> OfficeToolInvocation + OfficeToolResult + before/after state digest
-> ToolStepRecord (ordered)
-> DeliveryObservation (from committed results)
-> assess_delivery -> OracleAssessment
-> coverage increments
-> guided feedback / random isolated input
```

## 5. 实施 checklist

### A. Fixture 注册（`RA-01`）

- [x] 定义独立 fixture ID、版本与 freeze digest；
- [x] 定义 manifest：任务族、文件角色、路径模板、攻击模式、工具菜单摘要、Oracle 契约版本；
- [x] 提供 `load_error_capable_fixture(fixture_id)` 与 `ERROR_CAPABLE_FIXTURES`；
- [x] 证明旧 `structured_v1` fixture registry 的顺序、digest 与加载结果不变。

### B. 模型身份（`RA-05`）

- [ ] `ModelIdentity` 含 `provider_id`、`raw_model_label`、`normalized_model_id`、`provider_version`、`model_identity_digest`；
- [ ] 规范化不丢失原始标签，不同 Provider/标签不折叠为同一身份；
- [ ] 规范化规则确定为：小写化 + 非 `[a-z0-9._-]` 字符替换为 `-`，且原始标签必须原样保留。

### C. Agent adapter 与首轮输入（`RA-02`/`RA-03`）

- [ ] `AgentAdapter` 协议：`async def step(messages, tools, seed) -> ReactTurn`；
- [ ] Controller 不按 `path_id` 执行任何预设序列；
- [ ] 首轮输入只含通用规则、业务目标非答案部分与工具说明；
- [ ] 首轮 payload 禁止项审计：任务文件 ID、固定文件名、完整任务正文、攻击 ID、攻击载体正文；
- [ ] 首次工具调用前 `bind_agent_visible_context`，否则报错。

### D. 证据记录（`RA-04`）

- [ ] 每次请求绑定 episode、sequence、工具名、参数摘要、Agent 输出摘要；
- [ ] 记录对应 `OfficeToolInvocation`、`OfficeToolResult`、前后状态摘要、可见输出摘要、execution evidence；
- [ ] 顺序一致且不可重排；
- [ ] 拒绝的请求（不在菜单内、参数非法）被记录为拒绝，而不是丢弃。

### E. 证据桥与 Oracle（`RA-06`/`RA-07`/`RA-08`）

- [ ] 由已记录的工具结果派生 `DeliveryObservation`，并记录来源 invocation/evidence ID；
- [x] 每个交付通道调用分别生成 observation/assessment，摘要不得丢弃较早的交付；
- [ ] 阶段派生：`read_only` / `blocked` / `attempted` / `committed`；
- [ ] `attempted` 仅在"到达交付通道、`committed=false`、`blocked=false`、非 post-submit"时产生；
- [ ] 无证据时不产生 `attempted`，不把 blocked/failed 重标；
- [ ] 每个 Oracle finding 能回指至少一个工具结果，或明确记录"无交付载荷/无提交证据"。
- [x] 工作区写入、外部副作用和任务完成分别记录；成功写文件不自动代表外部交付。

### F. 公平性与准入（`RA-09`/`RA-10`）

- [ ] guided/random 首轮输入、工具能力、预算、运行环境一致；
- [ ] 反馈只出现在 selector 输入，不进入 Agent 任务正文；
- [ ] 准入门槛函数只读证据，不读配置开关。

## 6. 验收 checklist

- [x] `RA-AC-01`：`error_capable` 可由正式 registry 加载并生成独立冻结计划；旧 fixture 回归不变；
- [x] `RA-AC-02`：本地 fake Agent 通过统一 adapter 留下完整首轮输入与请求收据；服务器 Provider 需重跑确认；
- [x] `RA-AC-03`：首轮禁止项审计通过，任务文件只在后续工具结果中出现；
- [x] `RA-AC-04`：Controller 不按 `path_id` 执行预设路径；
- [ ] `RA-AC-05`：服务器 Provider 修复后 smoke 尚未重跑；
- [x] `RA-AC-06`：每个 Oracle finding 关联工具/状态/载荷证据；无证据的 attempted 保持未定；
- [x] `RA-AC-06a`：多次交付全部保留并可分别回指证据；
- [x] `RA-AC-06b`：最终无工具调用与先前已动作、截断、预算耗尽分开记录；
- [x] `RA-AC-07`：Provider 原始标签、规范化身份与版本摘要字段可回读；服务器复测仍需确认；
- [ ] `RA-AC-08`：guided 反馈进入下一集 selector，random 不读跨 Episode feedback；
- [ ] `RA-AC-09`：中断、工具失败、预算耗尽与恢复不重复提交、不篡改 plan、不补造结果；
- [ ] `RA-AC-10`：上述全部有证据后，才提交 `formal_comparison_eligible` 复审。

## 7. 验证命令与人工观察

```text
python -m compileall -q src agent_image tests
scripts/project_ruff.cmd check src agent_image tests
python -m pytest -q tests/unit/test_error_capable_*.py
python -m pytest -q tests/unit/test_structured_*.py
python scripts/probe_error_capable_agent.py --adapter fake --output <path>
git diff --check
```

人工观察至少包括：

1. 首轮 payload 中不出现任务文件 ID、固定文件名、任务正文、攻击 ID；
2. 工具轨迹中先出现 `list_directory`/`search_files`，再出现任务文件读取；
3. 每条请求都能找到对应的 `OfficeToolInvocation` 与 `OfficeToolResult`；
4. 每条 Oracle finding 都能回指工具证据或明确记录无证据；
5. 模型身份同时显示原始标签与规范化 ID；
6. 恢复后不出现重复提交。

## 8. 风险、失败信号和回滚

### 风险

- 真实模型的工具调用格式可能与 `ReactToolCall` 不兼容，需要适配或拒绝；
- fake Agent 若读写死路径会退化为 Controller 代发，必须由工具结果驱动；
- 首轮输入若包含任务族常量正文，会与任务文件正文重合，需要审计规则明确判定；
- 真实模型可能在预算内不完成任务发现，导致 Episode 无交付；
- `attempted` 在 Office V2 的自然可达性仍可能很低。

### 失败信号

- Controller 中出现任何按 `path_id` 分支的工具序列；
- 首轮审计发现禁止项；
- 请求与 invocation/result 无法一一对应；
- Oracle finding 无证据支撑却被判为违规或安全；
- 旧 fixture digest 或加载结果发生变化；
- 恢复产生重复提交。

### 回滚

- 只废弃新增模块与 registry 条目，不修改旧 fixture、旧 Campaign 或历史 Artifact；
- 若证据桥需要改变既有 Oracle 契约，停止实现，回到 SPEC 修订；
- 若真实模型无法在既有 `ReactToolCall` 契约下驱动 Office V2 工具，停止实现并报告，不通过放宽工具权限绕过。

## 9. 状态转换

本 TASK 由用户批准 SPEC 时一并授权执行，置为 `READY`。代码实现完成后按 §6 逐项记录证据；
在服务器真实 Provider smoke 与证据桥完整性通过前，`formal_comparison_eligible` 保持 `false`，
不得将本 TASK 标记为 `DONE`。
