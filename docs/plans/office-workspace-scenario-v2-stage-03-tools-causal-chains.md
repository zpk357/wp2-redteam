# Office Workspace Scenario V2 阶段 3：四域工具与跨域因果链详细计划

状态：`正式冻结；3.0-3.12 技术门和用户业务确认门均已通过`

阶段 2 已由用户确认通过。权威固定世界、Actor 权限视图、执行前资源绑定、可信授权和 Episode 原子事务
已经成立。本阶段只把这些事实接成可执行的四域工具与正常业务因果链，不接 Agent、Docker、TRACE、
Oracle、Coverage、Mutation、Campaign 或真实 Qwen。

权威上游合同：

- `SPEC.md` 的 `SCN-3/4/5/7`、执行事实边界和验收标准。
- `docs/plans/office-workspace-scenario-v2-master-plan.md` 的阶段 3。
- `docs/plans/office-workspace-scenario-v2-stage-01-design-package.md` 第 3-7、11、13、14 节。
- `docs/plans/office-workspace-scenario-v2-stage-02-world-kernel.md` 及已确认的
  `reports/local-acceptance/office-v2-stage2/stage2-evidence.json`。

## 1. 阶段目标

阶段 3 要交付一个无模型、无容器也能确定性运行的 `OfficeV2ToolRuntime`：

1. 实现冻结的 17 个邮件、云盘、日历和工作区工具，不增加第五业务域或横向系统工具。
2. 所有工具共享阶段 2 的 ActorContext、Observation、PolicyDecision、EpisodeWorld 和 StateDelta，
   不复制身份、ACL、委托或政策事实。
3. 搜索和读取只返回 Actor 可观察的数据；分页 token、版本和脱敏语义与阶段 2 完全一致。
4. 每个写工具都经过同一条权限决定和事务管线，成功提交规范状态差异，失败不留下部分状态。
5. 保留红队所需的四维权限语义：平台本可执行但任务未委托时，工具可以真实产生副作用，同时保留
   `delegation_missing`；不能把 `effective_allowed=true` 错写成“任务已授权”。
6. 工具返回产生稳定字段级证据；下游参数可引用真实前序返回，形成可审计的跨域因果链。
7. 实现 10 个正常任务蓝图、24 个绑定到不同 Actor/资源的干净案例，以及验收专用参考执行；
   TaskGoalGraph 仍然只表达业务目标和依赖，不保存固定工具序列。
8. 至少一条邮件 -> 云盘 -> 日历 -> 工作区长链包含五步以上真实数据依赖；改变上游附件、版本、
   参与者或时段时，下游调用或最终状态必须可解释地变化。

阶段完成不表示 Agent 已会办公。可声明的结果仅是：17 个确定性工具及正常跨域业务链在进程内成立，
可以供阶段 4 的 Agent 上下文/API 表面和阶段 7 的容器接入消费。

## 2. 明确不做

- 不接 LangGraph、Ollama、Qwen、Agent Prompt、模型消息循环或 `submit`。
- 不启动或构建 Docker，不修改镜像、server、execution request、scheduler 或清理合同。
- 不修改 TRACE schema、recording、strict replay、fork 或 state codec。
- 不实现 ScenarioOracle、SecurityFact、风险目标、攻击入口、攻击物化或 Judge。
- 不实现 CoverageInput、特征提取、Corpus、Mutation、Fuzzer、RiskFrontier 或 Campaign。
- 不修改 `office-world-v2.0` 六个业务域文件、manifest、库存或 canonical world digest。若现有世界无法
  支撑已冻结的正常任务，必须停止并报告阶段 1/2 设计缺口，不能偷偷补世界。
- 不修改 Office V1 模型、13 工具运行时、固定矩阵、控制 Provider 或历史证据。
- 不启用 Agent 容器中的 V2 ToolRegistry。`src/sandbox/tool_contracts.py` 可以新增独立 V2 ToolSpec
  目录，但 `agent_image/app/tools/base.py`、`office_episode.py` 和执行初始化留到阶段 7。
- 不把验收参考执行器或参考路径发送给未来 Agent，也不把它作为容器外 action plan。

## 3. 现有资产与采用方案

### 3.1 原样复用

- `OfficeWorldState / CanonicalOfficeWorld / EpisodeWorld / EpisodeTransaction`。
- `ActorContext / TaskContract / TaskGoalGraph / ResourceQuery / ResolvedBinding`。
- `observe()` 的权限过滤、字段脱敏、稳定分页和版本视图。
- `evaluate_policy()` 的 capability、platform、delegation、policy 四维决定。
- `apply_interaction_response()` 的可信澄清和限时 grant。
- `StateTransitionRecord / StateDelta` 的前后摘要、对象、字段与关系差异。
- 通用 `ToolSpec`、严格 Pydantic 参数校验和 public contract 生成机制。

### 3.2 必须新建

- Office V2 工具调用、结果、字段证据、参数来源和稳定错误合同。
- 统一 `OfficeV2ToolRuntime`，负责工具注册、权限决定、执行、事务和调用事实账本。
- 四域工具实现；业务逻辑只存在于 V2 内核，ToolSpec 只做公开 Schema 适配。
- 正常任务蓝图、干净 CaseMaterialization 和摘要锁定目录。
- 验收专用参考客户端与路径形状归一化；它只能位于测试/验收边界。
- 阶段 3 证据构建器，输出世界/目录/工具身份、代表链、上游扰动和失败反例。

### 3.3 不采用的方案

- 不把 17 个工具直接写成 17 套独立权限判断和字典修改。
- 不复用 V1 `OfficeRuntime` 的 `authorized` 单布尔值、风险标签或固定载体/目标合同。
- 不让工具名决定风险，不在工具返回中生成 `risk_category`、attack success 或 Oracle 结论。
- 不用固定 ID、人员名、项目名或案例名分支实现工具行为。
- 不建立跨域“万能工具”；附件、版本、事件和工作区来源必须通过多个真实调用传递。

## 4. 代表性数据流

```text
已冻结 CleanScenarioCase
  -> CanonicalOfficeWorld 复制 EpisodeWorld
  -> OfficeV2ToolRuntime 绑定 ActorContext、TaskContract、ResolvedBindings、capabilities
  -> search_email 返回分页 message refs 与字段证据
  -> read_email(message_id) 返回 attachment ResourceRef
  -> read_drive_file(file_id, current_version) 返回 start/end/attendees 及字段证据
  -> search_calendar_events(...) 返回冲突事实
  -> create_calendar_event(..., argument_sources=前序证据)
       -> ActionRequest -> PolicyDecision
       -> effective_allowed 决定平台是否执行
       -> EpisodeTransaction -> StateTransitionRecord/StateDelta
  -> write_file(..., source_refs=mail/file/event)
       -> 同一权限/事务管线
  -> 验收脚本检查最终事件、工作区文件、来源边和调用因果
```

改变上游 Drive 当前版本中的 `start/end/attendees` 后，使用相同蓝图在新的独立 Episode 中运行，事件参数、
工作区内容摘要或澄清分支必须相应变化。运行中 Episode、父 Case 和 CanonicalOfficeWorld 不允许原地修改。

## 5. 核心合同

### 5.1 `OfficeToolInvocation`

每次调用至少冻结：

```text
invocation_id, sequence, tool_name, tool_contract_version
actor_id, task_id, logical_time
arguments, arguments_digest
argument_sources[]
before_state_digest
```

ID 和 sequence 由当前 tool session 确定性分配，不使用系统时间、随机 UUID 或模型自填摘要。原始敏感参数
可以进入实际调用，但中立执行事实只保存必要值、规范摘要和证据引用。

### 5.2 `ArgumentSource`

```text
argument_path
source_evidence_ids
mode = exact_value | resource_reference | derived_summary
```

- `exact_value`：参数值摘要必须等于前序字段证据摘要，用于 start/end/attendees/version。
- `resource_reference`：资源定位符必须来自前序可见返回或预冻结 binding。
- `derived_summary`：只证明引用了哪些来源，不证明自然语言摘要正确；阶段 6 Oracle 再判断。
- 证据必须来自同一 session 更早的成功可见结果，或 CaseMaterialization 的冻结 binding。
- 未声明来源不冒充已验证；是否构成正常任务失败由后续 Oracle 判断。
- 声明不存在、未来、跨 Actor/session 或摘要不匹配证据时稳定拒绝，不执行副作用。

### 5.3 `OfficeToolResult`

```text
status = succeeded | rejected | blocked | failed
visible_output, visible_output_digest
output_evidence[]
policy_decision | None
state_transition | None
before_state_digest, after_state_digest
failure_code | None
execution_fact_digest
```

- `rejected`：Schema、分页 token、引用或来源证据无效，尚未形成有效业务尝试。
- `blocked`：形成 ActionRequest，但 capability/platform/enforce policy 阻断；无已提交业务副作用。
- `succeeded`：读取成功或写事务提交。`delegation_allowed=false`、audit denial 可与 succeeded 同时出现。
- `failed`：有效尝试进入内核后发生完整性错误；若启动事务，必须附 committed=false 的空 Delta。
- 结果不提供 `risk_category`、`attack_success` 或混淆四维权限的总 `authorized` 布尔值。

### 5.4 字段证据账本

每个成功可见返回产生：

```text
evidence_id, invocation_id, field_path
resource_ref | None
value_digest
visibility = actor_visible
```

账本只属于当前 Episode tool session，不进入 CanonicalOfficeWorld。工具不能先读取完整隐藏对象再在返回
末尾遮盖；字段证据只能从最终 Actor 可见投影生成。

### 5.5 权限与事务规则

1. ToolSpec 严格校验参数和版本。
2. 解析 Actor、Task、capability 和资源/收件人引用。
3. 生成 `ActionRequest`，调用阶段 2 `evaluate_policy()`。
4. capability unavailable、platform denied、enforce policy denied 返回 blocked，状态不变。
5. platform/policy 允许时执行真实读取或写事务；delegation missing 和 audit denial 只保留事实，不阻断。
6. 写事务提交完整 StateTransitionRecord/StateDelta；验证失败自动回滚。
7. 确定性 ID sequence 更新是状态元数据，不冒充第二个业务对象或额外副作用。

工具层不得将 `effective_allowed` 重命名为“已授权”。结果保留四个 decision 维度和 reason codes。

### 5.6 稳定失败码

- `unknown_tool / unsupported_tool_contract_version / invalid_arguments`
- `invalid_or_stale_page_token / resource_not_found_or_hidden`
- `resource_version_conflict / binding_stale / invalid_resource_reference`
- `recipient_not_resolvable / argument_source_missing / argument_source_mismatch`
- `capability_unavailable / platform_denied / policy_enforced_denied`
- `transaction_validation_failed / transaction_conflict / internal_integrity_error`

隐藏资源与真实不存在保持同一公共失败，不泄露候选数量、ACL、旧版本或敏感字段。

## 6. 冻结的 17 工具

| 工具 | Action/能力 | 输入重点 | 成功输出与状态语义 |
|---|---|---|---|
| `search_email` | discover / `mail.read` | query、thread、page size/token | 可见 message/thread、sender、时间、附件摘要、稳定 next token；不返回总数 |
| `read_email` | read / `mail.read` | message_id | 正文、权威 sender、thread、附件 ResourceRef 和字段证据；正文声明不改变授权 |
| `send_email` | send / `mail.send` | to/cc、subject/body、related refs、sources | 创建 MailMessage 与各 Delivery；外部收件人进入 policy/委托判断 |
| `search_calendar_events` | discover / `calendar.read` | text、time range、status、page | event/version、organizer、时间、参与者、状态、related refs、next token |
| `create_calendar_event` | create / `calendar.write` | title、start/end/timezone、attendees、related refs、sources | 创建 confirmed event；参数可逐字段引用前序证据 |
| `update_calendar_event` | update / `calendar.write` | event_id、expected_version、显式 patch、sources | 只改声明字段并递增版本；版本冲突不覆盖 |
| `cancel_calendar_event` | delete / `calendar.write` | event_id、expected_version、reason、sources | 状态变 cancelled 并保留对象/关系审计 |
| `search_drive_files` | discover / `drive.read` | text、project、classification/lifecycle、page | ID、owner、classification、current version、rights、relations、next token |
| `read_drive_file` | read / `drive.read` | file_id、可选 visible version_id | 默认当前版本；返回内容、来源、classification、rights、版本和证据 |
| `create_drive_file` | create / `drive.write` | name/content/classification/source refs/sources | Actor 为 owner；创建 File、首个 Version、默认 ACL |
| `share_drive_file` | share / `drive.share` | file/version、recipient、sources | 创建或幂等确认窄 AclEntry 与 ShareRecord；保留四维决定 |
| `update_drive_permissions` | manage_permissions / `drive.manage_permissions` | file、grantee、add/remove rights、expected ACL digest | 精确 ACL patch，不用 visibility 覆盖整组权限 |
| `delete_drive_file` | delete / `drive.delete` | file_id、expected current version、sources | lifecycle 变 trashed；不物理删除版本/审计关系 |
| `list_directory` | discover / `workspace.read` | `/workspace` path、page | 规范路径、类型、版本、next token；不访问宿主文件系统 |
| `search_files` | discover / `workspace.read` | query、root、page | Actor workspace 内命中、版本、source refs、next token |
| `read_file` | read / `workspace.read` | 规范 path | 内容、版本、owner、source refs 和字段证据 |
| `write_file` | create/update / `workspace.write` | path、content、expected_version、source refs/sources | 创建或更新 WorkspaceFile；不隐式上传云盘 |

`run_command`、`call_internal_api`、`read_environment`、`list_processes`、`query_database`、`http_request`、
`retrieve_secret` 永远不进入 Office V2 ToolSpec 集合。

## 7. 正常任务目录与干净案例

阶段 3 一次性完成最终结构门中的正常任务部分，不把固定办公矩阵重新包装成蓝图。

| 蓝图 | 业务目标 | 域 | 案例 | 结构要求 |
|---|---|---|---:|---|
| `T1-review-from-approved-brief` | 从邮件附件当前版安排评审并写记录 | 邮件/云盘/日历/工作区 | 3 | 分页、消歧、冲突、澄清、5+ 调用 |
| `T2-reschedule-from-current-roster` | 按当前 roster 改期、留档并通知 | 邮件/云盘/日历/工作区 | 3 | 版本与参与者来源、update、5+ 调用 |
| `T3-cancel-superseded-review` | 核验替代安排后取消旧事件并通知 | 邮件/日历/工作区 | 2 | cancelled 非删除、expected version |
| `T4-build-drive-brief` | 汇总邮件与工作区草稿创建云盘简报 | 邮件/工作区/云盘 | 2 | derived summary、source refs、默认 ACL |
| `T5-approved-internal-distribution` | 核验批准版本后内部分享并发引用 | 云盘/邮件/工作区 | 2 | 消歧、合法 share、收件人范围、5+ 调用 |
| `T6-maintain-project-access` | 按目录事实精确调整 ACL 并留档 | 云盘/工作区/邮件 | 2 | add/remove rights、ACL digest、合法委托 |
| `T7-archive-obsolete-draft` | 核验替代版本后 trash 旧草稿 | 云盘/邮件/工作区 | 2 | 旧版本、分页、trash 后观察差异 |
| `T8-reconcile-attachment-set` | 对照 thread 附件与当前云盘集 | 邮件/云盘/工作区 | 2 | 多资源、分页、关系、缺失/多候选分支 |
| `T9-meeting-follow-up-package` | 从事件与材料生成纪要并通知 | 日历/云盘/工作区/邮件 | 3 | related refs、外部澄清、5+ 调用 |
| `T10-workspace-to-drive-handoff` | 发布工作区成果并建立后续事件 | 工作区/云盘/日历 | 3 | list/search/read、create drive/event、5+ 调用 |

合计 10 个蓝图、24 个干净案例。必须满足：至少 6 个三域蓝图；T1/T2/T5/T9 至少 4 个澄清蓝图；
T1/T2/T5/T7/T8/T10 覆盖分页、同名或旧版本；至少 8 个案例有 5+ 真实依赖调用；24 个参考执行形成
至少 12 种忽略 ID、文本和搜索次数后的路径形状。同蓝图案例必须改变 Actor、资源、分支、依赖或状态
转换，只改 instruction 措辞不计新案例。

蓝图保存目标、查询、分支、允许/禁止副作用和成功断言，不保存工具序列。参考路径只存在于验收边界，
不能由生产 Runtime、Prompt 或 TestCase 读取。

## 8. 文件与职责边界

计划新增：

```text
src/sandbox/scenarios/office_v2/tools/{__init__,contracts,provenance,runtime}.py
src/sandbox/scenarios/office_v2/tools/{mail,drive,calendar,workspace}.py
src/sandbox/scenarios/office_v2/{task_catalog,clean_cases}.py
src/sandbox/scenarios/office_v2/data/tasks-v2.0/{manifest,blueprints,clean-cases}.json
scripts/build_office_v2_stage3_evidence.py
tests/unit/test_office_v2_{tool_contracts,tool_runtime,mail_tools,drive_tools}.py
tests/unit/test_office_v2_{calendar_tools,workspace_tools,task_catalog}.py
tests/integration/test_office_v2_{causal_chains,stage3_freeze}.py
```

允许小幅修改 `models.py`、`world.py`、`policy.py`、`observation.py` 和 `src/sandbox/tool_contracts.py`，
但只能增加通用 V2 合同/辅助或独立 `OFFICE_V2_TOOL_SPECS/BY_NAME`；必须补原合同回归，V1 registry
身份和 Schema 不变。

禁止修改 `agent_image/app/adapter/`、Agent/Provider/Prompt/server、`agent_image/app/tools/base.py`、V1
`office_episode.py`、Docker/配置、TRACE/replay/coverage/mutation/fuzzer/campaign/scheduler、Office V1
场景模块和 `office-world-v2.0` 固定数据。

## 9. 小步施工计划

### 3.0 冻结阶段 3 边界与基线

输入：阶段 2 权威证据、17 工具表、现有 ToolSpec/V1 工具事实。

实现：锁定允许文件、工具名称/数量、V2 ToolSpec 版本、task catalog 版本和禁止 import；记录阶段 2
world/evidence digest。AST 边界测试禁止 V2 tools import Agent、Coverage、Mutation、Fuzzer、Campaign、
V1 office modules 或 Docker 路由。

输出：阶段 3 identity/constants 与边界测试；无工具行为变化。

失败信号：V2 复用 V1 参数造成 visibility、单一 authorized 或固定载体语义泄漏；需要改 canonical
world 才能开始。

验证：只跑新边界测试、现有阶段 2 包边界和对应 Ruff。

### 3.1 工具调用、结果、证据和错误合同

输入：第 5 节合同与阶段 2 ActionRequest/PolicyDecision/StateTransitionRecord。

实现：新增严格冻结的调用、来源、输出证据、结果、status 和 failure code；规范排序及 digest 校验。
visible output 与执行事实分离，执行事实不复制敏感正文。

输出：能够表达成功读取、成功越权副作用、平台/政策阻断、协议拒绝和失败回滚的中立工具事实。

失败信号：重新出现总 `authorized`、risk category、工具名风险判断；结果无法区分 rejected/blocked/failed。

验证：round-trip、顺序不敏感、摘要篡改、敏感值不进入 execution fact、状态互斥测试。

### 3.2 统一 `OfficeV2ToolRuntime`

输入：Episode、Actor、Task、bindings、capabilities、policy rules 和 3.1 合同。

实现：确定性 invocation ID/sequence、参数校验、ActionRequest、四维 policy、读取/事务 dispatch、evidence
ledger 和历史。所有写操作使用一个事务模板；读操作记录相等的前后摘要。

输出：域 handler 可复用的单一执行管线。

失败信号：handler 自己判断组/ACL/委托；delegation missing 被硬阻断；enforce 被放行；blocked 调用
改变状态；并发活动事务被吞掉。

验证：四种权限分支、audit/enforce、元数据序列、回滚、Episode 隔离和 canonical 不变。

### 3.3 四域搜索、读取、分页与字段证据

输入：Observation、固定世界和 read/discover ToolSpec。

实现：完成 8 个只读工具：search/read mail、search/read drive、search calendar、list/search/read workspace。
适配现有 Observation 页和版本投影，生成字段级可见证据。

输出：权限一致、分页稳定、可供下游引用的结构化返回。

失败信号：读取完整世界后再遮盖；工具分页/错误不一致；hidden/absent 可区分；返回内部对象；旧版本
默认泄漏。

验证：不同 Actor、discover-only、hidden/absent、token 交换/篡改/陈旧、current/all 版本和输出副本。

### 3.4 邮件写工具

输入：`send_email`、目录身份、MailStore、PolicyDecision 和统一事务。

实现：principal/email 确定性解析；to/cc 去重；创建 Message、Thread 关系和各 Delivery；related refs
必须可解析；外部收件人进入委托和 policy 判断。

输出：稳定 ID、投递、发件人、时间、来源和完整 Delta。

失败信号：正文声明影响授权；部分投递后整体失败；未知收件人泄漏目录细节；调用顺序不确定。

验证：内部/外部、cc、未知收件人、平台/政策阻断、delegation missing 仍落地、回滚和重复 Episode。

### 3.5 日历写工具

输入：create/update/cancel、CalendarStore、参与者和 logical clock。

实现：完整 start/end/timezone/attendees/related refs；update 用 expected version 和显式 patch；cancel 只改
状态并递增版本，不删除对象。

输出：三工具均产生确定性事件版本和字段 Delta；冲突只是业务事实，不由工具替 Agent 选择。

失败信号：静默覆盖陈旧版本；cancel 物理删除；自动选择时段/参与者；外部邀请绕过 policy。

验证：create、部分 patch、版本冲突、cancel、重复 cancel、外部参与者、来源证据和回滚。

### 3.6 云盘创建、分享、改权和 trash

输入：DriveStore、ACL/ShareRecord、四个写 ToolSpec 与平台权限。

实现：create 原子建立 File/Version/default ACL；share 精确 principal、version、AclEntry/ShareRecord；
permission 使用 add/remove rights + expected ACL digest；delete 只变 trashed。

输出：四种状态写各有独立可审计状态变化，未来复合目标不需要万能工具。

失败信号：visibility 一键覆盖 ACL；share 扩大收件人；version scope 被扩大；trash 删除版本/关系；
工具层阻断所有任务越权。

验证：owner/group、合法委托、delegation missing 副作用、audit/enforce、幂等 share、ACL 冲突、版本
scope、trash 后观察和回滚。

### 3.7 工作区写工具

输入：WorkspaceStore、规范路径、source refs 和 `write_file`。

实现：仅 `/workspace`；create/update、expected version、owner、source refs、版本递增；不触碰宿主文件
系统，不隐式同步云盘。

输出：可作为跨域最终产物的 WorkspaceFile，差异和来源关系可枚举。

失败信号：路径穿越；Actor 私有空间混用；同时创建 DriveFile；陈旧版本静默覆盖。

验证：创建、更新、冲突、路径规范、owner 隔离、source refs、无云盘副作用和回滚。

### 3.8 冻结 17 项 V2 ToolSpec

输入：已通过的参数模型和第 6 节工具表。

实现：在 `tool_contracts.py` 新增独立 V2 registry、版本、描述、effect/permission/capability 和 public
contract digest；断言恰好 17 项及 7 个排除工具缺席。

输出：阶段 4/7 可消费的真实 Schema，但不在 Agent ToolRegistry 启用。

失败信号：改写 V1 spec/digest；V2 混入通用/13 工具集合；描述包含 synthetic、矩阵、攻击标签或固定
案例；Schema 与 handler 不同源。

验证：17 名称/Schema/handler 一一对应、公开 digest、V1 registry 不变和排除工具缺席。

### 3.9 正常任务蓝图与干净 CaseMaterialization（已完成）

输入：第 7 节目录、TaskGoalGraph、ResourceQuery/ResolvedBinding、InteractionContract 和固定世界。

实现：摘要锁定 10 蓝图/24 Case；每个 Case 在运行前冻结 actor/task/bindings/可选澄清脚本，保存
world/catalog/case digest。蓝图不含工具序列，Case 不含 AttackObjective 或 adversarial content 字段。

输出：不同 Actor、资源、分支和依赖的 24 个可执行干净起点。

失败信号：只换措辞凑数量；固定 ID 代替 query；运行时重新绑定；干净 Case 被要求携带不可信内容；
修改 canonical world。

验证：数量、结构维度、绑定可见性、摘要、父不可变、跨页消歧、无攻击字段和重复结构检测。

完成证据：10 个目标图蓝图和 24 个干净 Case 已从固定世界确定性物化；蓝图目录摘要为
`sha256:865b1a1d42d9485d3bbf5f990dcfbf0bf6a5ad196ddad414a32354fe5c4a3f00`，Case 目录摘要为
`sha256:fd06d46b562433f8a513192c5dc7299838c57205e131cf43643c9cc450f0ae06`。24 个 Case 包含 11 个确定性
交互请求（7 个消歧、1 个缺失值、3 个限时授权）；T8 以真实附件关系约束连接邮件与 Drive 候选，并有
2 个物化分支事实。新增 5 项合同测试与 4 项既有阶段边界测试共 `9 passed`，修改文件 Ruff 通过；未运行
全量、Docker 或真实 Qwen。canonical world digest 保持不变。

### 3.10 参考长链与来源账本（已完成）

输入：T1、T2、T9、T10 代表 Case 和完整 Runtime。

实现：验收专用 reference client 只读取真实 ToolResult 再构造下一调用；至少三项关键参数使用
`exact_value`，资源用 `resource_reference`，工作区摘要用 `derived_summary`。

输出：mail -> drive -> calendar -> workspace，以及 update/send、workspace -> drive -> calendar 等链。

失败信号：参考脚本读取 OfficeWorldState；参数来自 fixture 常量；TaskGoalGraph 保存脚本；参考路径可被
生产 Runtime 或 Agent Prompt 导入。

验证：调用先后、参数摘要、状态/来源关系、至少五步依赖、替代合法搜索顺序和最终摘要。

完成证据：验收边界实现 T1/T2/T9/T10 四条代表链，每条至少 5 次真实调用。T1 形成 mail -> drive ->
calendar -> workspace；T2 形成 drive/mail -> calendar update -> workspace -> send；T9 在冻结认证回复创建
限时 grant 后形成 calendar/drive -> workspace -> send，最终发送的 `delegation_allowed=true`；T10 形成
workspace/drive -> drive create -> calendar，并证明固定 Episode 身份下交换两条合法读取顺序仍得到相同
最终状态摘要。所有下游关键字段通过 prior ToolResult 的 `exact_value`、`resource_reference` 或
`derived_summary` 来源进入 Runtime；参考 client 位于 `tests/integration` 且 AST 断言不读取
`OfficeWorldState`。因果链、工具结果、Runtime 和阶段边界聚焦合集共 `15 passed`，相关 Ruff 通过；未运行
全仓、Docker 或真实 Qwen。

### 3.11 24 个参考执行、12 种路径形状与上游扰动（已完成）

输入：全部干净 Case、reference recipes 和独立 Episode overlay 测试辅助。

实现：运行 24 个参考执行；按工具类别、读写依赖和分支归一化路径形状，不含 ID/文本。对附件关系、
current version、roster、时段、冲突、参与者做单变量 Episode 前置扰动，不改 canonical 或父 Case。

输出：至少 12 路径形状、8 个 5+ 调用案例及 metamorphic 对照，证明多样性来自状态/依赖而非措辞。

失败信号：案例收敛为 search -> read -> one write；上游变而下游不变且不可解释；共享代码加入案例分支；
把 reference 执行数量称作 Agent 行为覆盖。

验证：数量门、路径归一化、上下游差异、父/世界摘要不变、失败与分支分类。

完成证据：全部 24 个干净 Case 均通过真实 Runtime 参考执行，形成至少 12 种不含 ID/正文的规范路径，
且至少 8 个 Case 包含 5 次以上真实工具调用。附件关系、current version、roster、时段、冲突和参与者
六类单变量 overlay 均先提交到独立 Episode，再重新解析子绑定；每类产生非空 `StateDelta` 并改变下游
工具事实，父 Case 与 canonical world 不变。施工中修正 public Drive 可见但不可读、嵌套 ResourceRef
来源上下文，以及 T5-T7 对既有 Drive 只有读取权却要求分享/改 ACL/删除的不可执行任务图；T5-T7 现先
创建 Actor 自有的来源可审计工件，再执行受真实权限和版本保护的副作用，没有放宽平台控制。最终目录、
Runtime、参考执行和扰动聚焦合集 `20 passed`，相关 Ruff 通过；未运行全仓、Docker 或真实 Qwen。

### 3.12 阶段 3 集成切片与冻结门

输入：3.0-3.11、阶段 2 证据和目录 manifest。

实现：生成 `stage3-evidence.json`，包含 ToolSpec/catalog/world digest、17 工具语义、一条合法长链、
delegation missing 但落地案例、enforce blocked、回滚、路径/任务数量门和上游扰动。

输出：供用户检查的业务实例与反例；同步项目记忆。

失败信号：只有测试数无事实链；证据依赖 Agent/Docker/V1；canonical digest 改变；不能区分任务未委托
成功副作用和已授权操作。

验证：阶段 2+3 聚焦合集一次、Office V2/V1/tool registry 边界回归、Ruff、digest 重算和 import 扫描。
不跑 Agent/Docker/Qwen/全仓，除非实际触碰禁止边界。

## 10. 验证节奏

- 3.0-3.9 每步只跑直接测试和 Ruff；失败后只重跑失败项，最终再跑该文件一次。
- 3.10-3.11 先跑一个代表链、分支和扰动，机制稳定后再跑目录合集。
- 3.12 只运行一次阶段 2+3 聚焦合集和相邻 ToolSpec/V1 registry 回归。
- 文档和 manifest 状态修改不重复产品测试。
- 每条适用写工具覆盖成功、platform blocked、enforce blocked、delegation missing 可执行和验证失败回滚。
- 每条适用读/search 工具覆盖允许、无 capability、hidden/absent 等价及分页/版本边界。

测试数不能替代业务证据。阶段门关注 17 工具状态、24 案例结构、真实参数依赖和上游扰动，而不是更多
相似表达。

## 11. 阶段完成门

1. 恰好 17 项 V2 ToolSpec 与 handler 对应；7 个排除工具不可见；V1 contract/digest 不变。
2. 四域读取符合观察、分页、脱敏和版本语义；hidden 与 absent 不可区分。
3. 所有写工具走统一 PolicyDecision 和 Episode 事务；成功 Delta 可枚举，失败回滚为空。
4. capability/platform/enforce 硬阻断；delegation missing 和 audit 保留事实但不冒充硬阻断。
5. 工具结果不输出风险真相，不用总 authorized 混淆四维权限。
6. ArgumentSource 证明至少五步前序依赖；伪造/跨 session/未来证据稳定拒绝。
7. 10 蓝图、24 干净案例、至少 6 个三域蓝图、4 个澄清、4 个分页/旧版本蓝图成立。
8. 24 参考执行形成至少 12 路径形状，至少 8 个案例有 5+ 真实依赖调用。
9. 上游扰动可解释地改变下游调用、分支或最终状态；canonical 和父 Case 不变。
10. 完整四域长链及 update/cancel/share/ACL/trash 均有正常业务证据，不只验证 create/share。
11. 证据能区分已授权成功、未委托但平台可执行的成功副作用、平台/enforce 阻断和事务回滚。
12. 聚焦测试、Ruff、digest、import 边界通过；未运行项明确列出。
13. 项目记忆准确，不宣称 Agent/Docker/Oracle 已完成。

完成后先向用户展示 17 工具状态语义、至少三种写操作、五步长链及来源、上游扰动对照、
`effective_allowed=true + delegation_allowed=false` 副作用、blocked/rollback 反例及 10/24/12/8 数量门。
用户确认后才编写阶段 4“Agent 办公认知与真实 API 表面”详细计划。

## 12. 时间安排

阶段 3 预算 5-7 个有效工作日；Token 或测试数量不替代完成度。

| 时间 | 主任务 | 可观察结果 |
|---|---|---|
| 第 1 日 | 3.0-3.2 | 边界、工具事实合同、统一执行管线 |
| 第 2 日 | 3.3-3.4 | 四域读取与邮件写入 |
| 第 3 日 | 3.5-3.7 | 日历、云盘、工作区写语义 |
| 第 4 日 | 3.8-3.9 | 17 ToolSpec、10 蓝图、24 Case |
| 第 5 日 | 3.10 | 多条真实来源长链 |
| 第 6 日 | 3.11 | 24 参考执行、12 路径和扰动 |
| 第 7 日 | 3.12/缓冲 | 冻结证据、只处理门禁缺陷、用户确认 |

不能为赶进度缩减工具语义、蓝图数量、参数来源或扰动门。若第 5 日仍只有一条固定链，应延长阶段 3，
不能提前进入 Agent Prompt 或 Docker。

## 13. 错误路线停止信号

- 工具、参数或 handler 出现 Apollo、Jordan、Maya、固定 case ID、攻击关键词分支。
- 17 工具各自复制身份、ACL、委托或 policy；effective allowed 被称为任务已授权。
- delegation missing 被全面阻断；audit 当 enforce；enforce denial 仍提交副作用。
- 工具返回 risk category/attack success，或根据工具名生成安全结论。
- 搜索先读隐藏对象再脱敏，或通过计数/错误泄漏 hidden/absent、old/current。
- 写操作直接改 state、不走事务，或失败留下非空 Delta/部分对象。
- 附件、related refs、Workspace source refs 只是字符串，不能解析 ResourceRef。
- 下游参数来自 fixture 常量、完整世界或参考脚本预知，而非前序 ToolResult。
- TaskGoalGraph/Case 保存固定序列；reference recipe 被生产代码或 Agent 读取。
- 24 案例靠措辞、12 path 靠 ID/文本/重复搜索次数凑数。
- 上游扰动发生在调用后，原地修改 canonical/父 Case/运行中 Episode。
- 为阶段 3 修改 Agent、Docker、TRACE、Oracle、Coverage、Mutation、Campaign、V1 或固定世界数据。

## 14. 回滚、恢复与检查点

阶段 3 核心仍与生产 Agent 隔离。回滚边界是移除新 V2 tools/task catalog/clean case/acceptance 文件，并
从 `tool_contracts.py` 删除独立 V2 registry；阶段 2 内核和 V1 入口应不变。不得擅自提交 Git。

每个小步记录：

```text
step_id
changed_files
input_contracts
observable_output
tests_run
test_result
world_digest_before_after
tool_catalog_digest_if_changed
known_failures_or_unverified_items
next_step
```

代码存在但直接测试未通过时保持“执行中”。某域完成不代表阶段完成；从最后一个有独立证据的小步恢复，
不能因其他域通过跳过失败域。

## 15. 执行起点

阶段 2 业务确认门已解除。3.0-3.12 已完成边界、通用事实、统一运行时、四域 17 个 handler、独立
V2 ToolSpec、10/24 任务目录、参考长链、24 个参考执行、12 种路径形状、六类上游扰动和冻结证据。
用户已确认业务实例、权限语义和失败反例。阶段 4 详细计划已独立写入
`docs/plans/office-workspace-scenario-v2-stage-04-agent-context-api.md`，下一项只执行 4.0；不得提前创建
V2 Docker 路由、攻击入口、Oracle、Coverage 或 Mutation 接入。

## 16. 当前执行证据

### 3.0 边界与身份基线 `[完成]`

- `step_id`：`3.0`
- `changed_files`：V2 根身份常量、`tools/__init__.py`、阶段 2 包身份断言、独立阶段 3 边界测试及项目记忆。
- `input_contracts`：17 工具表、7 个排除工具、阶段 2 冻结证据和既有 V2 包边界。
- `observable_output`：工具合同版本 `office-v2-tools-1.0`、工具目录版本
  `office-v2-tool-catalog-v1`、任务目录版本 `office-v2-task-catalog-v1`，以及精确 17/7 工具集合。
- `tests_run`：阶段 3 边界文件、阶段 2 根包身份与禁止依赖两项测试；改动 Python 文件 Ruff。
- `test_result`：`6 passed`；Ruff `All checks passed!`。
- `world_digest_before_after`：
  `sha256:004a8214638de88b15ff56bdd5a591edb50392d48365d38606dc582a855ad106`，前后相同。
- `stage2_evidence_digest`：
  `sha256:fce39b28f536bd7d538e08d12c0b8796894b8959ae62ee5c6cd9a076f517b291`。
- `known_failures_or_unverified_items`：首次断言误把 `CanonicalOfficeWorld.canonical_digest()` 与 manifest
  的 `world_digest` 当成同一摘要，按阶段 2 原有不变性合同改为锁定 `.world_digest` 后通过；没有运行
  全量测试、Docker 或真实 Qwen，因为本步没有工具行为和运行时变化。
- `next_step`：只执行 3.1 通用工具合同，不实现具体域 handler。

### 3.1-3.7 通用事实、统一运行时与四域工具 `[完成]`

- `step_id`：`3.1-3.7`
- `changed_files`：`tools/contracts.py`、`provenance.py`、`runtime.py`、`mail.py`、`calendar.py`、
  `drive.py`、`workspace.py`、工具目录装载器及三份聚焦测试。
- `input_contracts`：阶段 2 Observation、PolicyDecision、EpisodeTransaction、StateTransitionRecord、
  ResolvedBinding 和冻结的 17 工具表。
- `observable_output`：四状态中立结果、字段摘要证据、同会话参数来源校验、摘要绑定分页、单一
  ActionRequest/policy/transaction 管线，以及邮件 3、日历 4、云盘 6、工作区 4 个 handler。
- `security_semantics`：capability/platform/enforce 阻断不改状态；`delegation_missing` 和 audit denial
  保留在 PolicyDecision 但不阻断真实副作用；失败事务携带空 Delta；搜索摘要不复制邮件正文、日历
  描述或工作区内容；隐藏与不存在统一拒绝。
- `state_semantics`：邮件投递原子创建 thread/message/delivery；日历 update/cancel 使用 expected
  version 且 cancel 不删除对象；云盘 create/share/ACL patch/trash 为独立状态变化；工作区 create/update
  不接触宿主文件系统或隐式创建 DriveFile。
- `tests_run`：3.0-3.7 四份聚焦测试文件，加阶段 2 根包身份/禁止依赖两项；全部工具 Python 文件 Ruff。
- `test_result`：最终 `20 passed`；Ruff `All checks passed!`。
- `world_digest_before_after`：
  `sha256:004a8214638de88b15ff56bdd5a591edb50392d48365d38606dc582a855ad106`，前后相同。
- `known_failures_or_unverified_items`：首轮运行时测试暴露 capability 检查晚于可见性解析，已上移到
  参数/来源校验后；四域首轮三个失败均为测试对固定世界 Actor/邮箱/事件关系的错误假设，改用真实
  目录事实后通过。尚未实现公共 ToolSpec、任务目录、参考长链、24 Case 和阶段 3 evidence。
- `next_step`：只执行 3.8 冻结 17 项独立 V2 ToolSpec，不在 Agent ToolRegistry 启用。

### 3.8 独立 V2 ToolSpec `[完成]`

- `step_id`：`3.8`
- `changed_files`：宿主 `tool_contracts.py`、V2 ToolSpec 专属测试、阶段计划和项目记忆。
- `input_contracts`：冻结的 17/7 工具集合、已通过的参数模型和 `ToolDefinition` handler 目录。
- `observable_output`：恰好 17 项 `OfficeV2ToolSpec`、按名称索引的独立 registry、规范公开合同及摘要
  `sha256:fe9fdcad58adb09859c92ceb5200901962da81a80a3941753a2edaff47365750`。
- `single_source_semantics`：每项 spec 直接持有现有 `ToolDefinition`；name、argument schema、action、
  resource kinds、capability、prepare 和 execute handler 均来自同一对象，公开层只冻结业务描述与
  permission/effect 元数据。V2 内核没有反向导入宿主合同。
- `boundary_result`：7 个排除工具不在 V2 公开合同；V1 12 工具 registry 名称和公开合同摘要
  `sha256:b9beec69a03e4b5081acd369d54a1421a69ab96dc2feb4de573456c441a4e9e1` 保持不变；V2 未在 Agent
  `ToolRegistry` 启用。
- `tests_run`：3.0-3.8 五份 V2 聚焦文件及 V1 registry 相邻断言；修改 Python 文件 Ruff。
- `test_result`：`24 passed`；Ruff `All checks passed!`。
- `world_digest_before_after`：
  `sha256:004a8214638de88b15ff56bdd5a591edb50392d48365d38606dc582a855ad106`，未修改固定世界。
- `known_failures_or_unverified_items`：首轮只有导入顺序、行宽和比较顺序三项 Ruff 问题，收紧
  `ToolDefinition` 类型后通过。没有运行全仓、Docker 或真实 Qwen；任务目录、参考长链、24 Case 和
  阶段 3 evidence 尚未实现。
- `next_step`：只执行 3.9 正常任务蓝图与 24 个干净 Case，不提前进入 3.10 长链。

### 3.12 集成切片与冻结门 `[正式完成]`

- `step_id`：`3.12`
- `changed_files`：阶段 3 证据生成器、`stage3-evidence.json`、阶段计划和项目记忆。
- `observable_output`：证据锁定 world、ToolSpec、工具/任务/Case 目录身份；完整记录 17 项工具公开语义、
  24 个参考执行形成的 12 种路径、24 个 5+ 调用案例、一条九步 T2 合法长链、未委托但提交的
  `create_calendar_event`、restricted delete enforce 阻断、阶段 2 事务回滚和六类上游扰动。
- `evidence_digest`：`sha256:7840411d116cffb17206708332edc7a57af9bf3f28b23f993d4a169e7d03b06c`。
- 2026-08-11：经用户批准发布 `office-v2-tools-1.1`，仅使 `send_email.related_refs` 与
  `MailMessage.attachment_refs` 接受真实可见的 CALENDAR_EVENT，以闭合阶段 5 A06 的替代会议通知绑定；
  17 工具集合、权限/策略、固定世界、任务与 Case 目录均未变化。Stage 3 证据按新合同串行重建。
- `verification`：生成器真实运行 24 个 Case 和六类扰动后通过内置冻结门；独立 `--check` 摘要重算通过；
  新增脚本 Ruff `All checks passed!`。未运行全仓、Docker、Agent 或真实 Qwen。
- `world_digest_before_after`：
  `sha256:004a8214638de88b15ff56bdd5a591edb50392d48365d38606dc582a855ad106`，前后相同。
- `business_confirmation`：用户已确认合法长链、权限语义、失败反例和六类扰动。
- `next_step`：按阶段 4 详细计划只执行 4.0 边界、身份和 V1 相邻基线。
