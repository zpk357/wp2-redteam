# Office Workspace Scenario V2 阶段 2：固定世界、身份权限与观察内核详细计划

状态：`完成；步骤 2.0-2.11 技术门与用户业务确认门均已通过`

阶段 1 的业务与架构设计已由用户确认。这个确认只表示可以进入阶段 2 施工，不表示任何 V2 场景行为
已经实现或通过测试。本阶段第一次编写 V2 运行时代码，但只建设无 Agent、无 Docker 的确定性世界内核。

权威上游合同：

- `docs/plans/office-workspace-scenario-v2-master-plan.md`
- `docs/plans/office-workspace-scenario-v2-stage-01-design-package.md`
- `SPEC.md` 中 `SCN-3` 至 `SCN-6`

## 1. 阶段目标

阶段 2 要把阶段 1 冻结的世界基础变成一个可执行、可复核、不可被案例逻辑偷偷改写的单一事实源：

1. 加载并校验唯一固定的 `office-world-v2.0`。
2. 从基础世界创建相互隔离的 Episode 副本，保证基础世界和父案例不变。
3. 实现内部用户、外部联系人、组、角色、所有权、ACL、任务委托和企业政策的权威事实。
4. 对动作产生结构化 `PolicyDecision`，明确区分账户能力、平台 ACL、任务委托和企业政策。
5. 只向 Actor 暴露其有权观察的分页、脱敏、版本化视图。
6. 在执行前按业务条件解析资源并冻结 `ResolvedBinding`，运行和 replay 不重新选择资源。
7. 支持确定性的可信澄清回复和限时 `DelegationGrant` 状态转换，但不接 Agent 对话循环。

阶段完成后的可观察结果不是“Agent 会办公”，而是以下纯内核闭环成立：

```text
加载固定世界 -> 复制 Episode -> 建立 ActorContext
-> 查询权限受限视图 -> 解析并冻结资源
-> 评估动作权限 -> 处理一条可信回复并创建限时委托
-> 重新评估动作 -> 原子修改 Episode -> 证明基础世界摘要不变
```

## 2. 明确不做

本阶段不实现或修改：

- 17 个工具的 ToolSpec、dispatch、容器工具桥和真实工具调用。
- LangGraph、Agent Prompt、动态系统上下文、Ollama 或真实 Qwen。
- 四类攻击入口的物化、`ReachableAttackSurface` 计算和攻击目标执行。
- `ScenarioOracle`、TRACE 事件接入、recording、replay 或 coverage 映射。
- 完整的 10 个 TaskGoalGraph 蓝图、24 个干净案例、12 个攻击目标和候选竞争。
- Mutation、Fuzzer、Campaign、RiskFrontier、G5、G6 或 Docker 镜像。
- Office V1 的兼容改造、删除或生产入口迁移。
- 异步授权撤销、并发竞态、真实墙钟和随机世界生成。

`TaskGoalGraph`、`ResourceQuery`、`ResolvedBinding`、`InteractionContract` 和 `DelegationGrant` 的基础模型及
验证规则属于阶段 2；完整任务目录属于阶段 3，Agent 多轮接入属于阶段 4，攻击放置属于阶段 5，Oracle
事实重建属于阶段 6。

## 3. 复用与自研边界

### 3.1 原样复用的项目机制

- Pydantic v2 和 `src/sandbox/protocol.py` 的严格合同风格：未知字段拒绝、字段级验证、版本字段明确。
- `src/sandbox/content_digests.py` 和现有规范 JSON/SHA-256 规则；不得新增不兼容的摘要算法。
- 现有 recording/state codec 中已经证明有效的确定性序列化原则。
- Office V1 状态内核的原子提交、回滚和稳定错误码经验，仅作为实现参考。

### 3.2 必须自研的 V2 边界

- 四域统一关系模型、固定世界 manifest 和跨域引用完整性。
- 身份、组闭包、ACL、委托与政策四层决策。
- Actor 部分可观察视图、稳定分页和字段脱敏。
- 约束式资源解析和执行前绑定冻结。
- Episode 逻辑时钟、确定性 ID 分配和不可变基础世界。

V2 模块不得 import `office_v1.py`、`office_runtime.py`、`office_matrix.py`、`injection.py` 或其他 Office V1
场景类。共享能力只允许依赖更底层的通用合同/摘要模块；V1 与 V2 由未来上层适配器分别调用。

### 3.3 不新增依赖

阶段 2 使用当前 Python 3.11、Pydantic 和标准库完成，不引入数据库、ORM、图数据库、随机数据生成器或
策略引擎。固定规模的世界以审计友好的版本化 JSON 保存；若纯内存实现无法满足既定库存的确定性和
测试速度，再提出独立架构决策，不能施工中静默换存储方案。

## 4. 代表性数据流

```text
data/office-world-v2.0/*.json
  -> CanonicalWorldLoader 校验 schema、文件摘要、组合摘要和跨域引用
  -> CanonicalOfficeWorld（冻结）
  -> create_episode(case_id) 深复制为 OfficeWorldState + logical_clock=0
  -> derive_actor_context(actor_id) 计算身份、角色和组闭包
  -> observe(query, actor, page_size, page_token)
       先过滤不可发现资源，再脱敏可发现但不可读取字段
  -> resolve(ResourceQuery, actor)
       返回唯一/多个候选/无匹配；需要消歧时不猜测
  -> freeze ResolvedBinding（资源 ID、版本、解析证据、世界摘要）
  -> evaluate(ActionRequest, ActorContext, TaskDelegation, active grants)
       返回四层 PolicyDecision 和稳定 reason codes
  -> apply_authenticated_response(UserResponseRule)
       合法时创建有限作用域、有限时效 DelegationGrant
  -> reevaluate(ActionRequest)
  -> transaction(state change)
       全部不变量成立则一次提交，否则完整回滚
  -> 比较 canonical digest、episode digest 和 mutation record
```

阶段 2 的集成测试必须展示两个分支：同一外部参与者邀请在 grant 前 `delegation_allowed=false`，可信且
匹配规则的用户回复后仅对指定事件、指定参与者和有效期变为 `true`；伪造邮件中的相同文字不能改变
任何授权状态。

## 5. 文件与职责边界

计划新增：

```text
src/sandbox/scenarios/office_v2/__init__.py
src/sandbox/scenarios/office_v2/models.py
src/sandbox/scenarios/office_v2/canonical_world.py
src/sandbox/scenarios/office_v2/policy.py
src/sandbox/scenarios/office_v2/world.py
src/sandbox/scenarios/office_v2/observation.py
src/sandbox/scenarios/office_v2/resolution.py
src/sandbox/scenarios/office_v2/data/office-world-v2.0/manifest.json
src/sandbox/scenarios/office_v2/data/office-world-v2.0/organization.json
src/sandbox/scenarios/office_v2/data/office-world-v2.0/mail.json
src/sandbox/scenarios/office_v2/data/office-world-v2.0/drive.json
src/sandbox/scenarios/office_v2/data/office-world-v2.0/calendar.json
src/sandbox/scenarios/office_v2/data/office-world-v2.0/workspace.json
src/sandbox/scenarios/office_v2/data/office-world-v2.0/policy.json
tests/unit/test_office_v2_models.py
tests/unit/test_office_v2_canonical_world.py
tests/unit/test_office_v2_policy.py
tests/unit/test_office_v2_world.py
tests/unit/test_office_v2_observation.py
tests/unit/test_office_v2_resolution.py
```

职责必须保持单向：

```text
models
  <- canonical_world
  <- policy
  <- world
  <- observation
  <- resolution

canonical_world -> world
world facts -> policy / observation / resolution
policy、observation、resolution 之间不互相写状态
```

- `models.py` 只定义值对象、枚举、联合合同和局部结构验证，不加载文件、不执行策略、不修改状态。
- `canonical_world.py` 只加载、校验和冻结基础世界，不提供业务写操作。
- `policy.py` 是纯决策层，相同输入事实必须得到字节等价的决定。
- `world.py` 是 Episode 状态与事务边界，不能把权限规则复制进写方法。
- `observation.py` 只生成 Actor 可见投影，不返回内部对象引用。
- `resolution.py` 只从可观察且符合约束的候选产生绑定结果，不物化攻击、不执行工具。

## 6. 固定模型合同

### 6.1 共同值对象

至少定义并冻结以下公共概念：

- `WorldVersion`、`LogicalTime`、`ResourceId`、`PrincipalId`、`GroupId`、`PolicyRuleId`。
- `ResourceKind`：mail message/thread、drive file/version、calendar event、workspace file。
- `ResourceRef`：kind、resource_id、可选 version_id，不允许自由字符串模拟引用。
- `Sensitivity`、`PrincipalKind`、`AccessRight`、`ActionKind`、`DecisionMode`。
- `SourceEvidence`：事实来源、资源引用、版本、字段路径和摘要。
- `StableFailure`：稳定 error code、公开消息和不可公开的内部 evidence refs 分离。

所有持久合同必须：`extra="forbid"`、显式 schema version、确定性排序、时区明确；禁止 `dict[str, Any]`
承载核心业务对象。只有确实开放的工具扩展字段可使用通用映射，本阶段没有这种需要。

### 6.2 身份与组织

- `Organization`：固定 organization ID、显示名和内部域名。
- `Principal`：40 个内部用户与 12 个外部联系人，身份类别互斥。
- `Group` 与 `GroupMembership`：8 个组，支持重叠成员关系和有限嵌套。
- `RoleAssignment`：角色、作用域和有效期；角色不是 ACL 的别名。
- `ActorContext`：当前代表身份、认证发行者、有效角色、组闭包、账户能力和逻辑时间。

不变量：principal/email 唯一；组引用存在；组嵌套无环；外部联系人不能获得内部员工角色；同一成员关系
不能重复；ActorContext 只能由权威目录派生，不能由 Prompt 或案例文本直接填写。

### 6.3 四域对象

- 邮件：`MailThread`、`MailMessage`、参与者、附件/链接 `ResourceRef`、发送状态、可信来源元数据。
- 云盘：`DriveFile`、`DriveFileVersion`、owner、classification、ACL、`ShareRecord`、当前版本指针。
- 日历：`CalendarEvent`、organizer、attendees、时间区间、状态、版本和来源引用。
- 工作区：`WorkspaceFile`、路径、版本、内容、owner 和 source refs；不自动同步云盘。

不变量包括：thread/message 关系、版本连续性、当前版本存在、owner 存在、ACL principal 存在、事件区间
合法、取消事件不可视为确认事件、工作区路径规范化、所有跨域引用类型和目标都匹配。

### 6.4 任务、解析和交互基础模型

- `TaskGoalGraph`：目标节点、依赖边、分支条件和允许副作用；验证 DAG，不存固定工具序列。
- `TaskDelegation`：发行者、Actor、允许动作/资源约束、有效期和来源。
- `ResourceQuery`：资源类型、项目/主题、时间、owner、classification、版本状态、关系约束和基数要求。
- `ResolvedBinding`：query ID、冻结资源/版本、候选证据、解析时世界摘要和 Actor 可见视图摘要。
- `ClarificationRequest`：缺失事实、允许响应范围和请求时逻辑时间。
- `UserResponseRule`：已认证响应者、匹配请求、结构化选择和可创建 grant 模板。
- `DelegationGrant`：granter、grantee、动作、资源/参与者作用域、生效/到期逻辑时间和可信事件引用。

这些模型只保证后续阶段不需推翻接口。阶段 2 不执行完整 TaskGoalGraph，也不让 LLM 自由文本决定授权。

### 6.5 权限决定

`ActionRequest` 至少携带 Actor、动作、目标资源/目标 principal、任务委托、逻辑时间和证据引用。
`PolicyDecision` 必须分别输出：

```text
capability_available
platform_allowed
delegation_allowed
policy_allowed
effective_allowed
enforcement_layer
reason_codes
matched_acl_entries
matched_delegations_or_grants
matched_policy_rules
decision_digest
```

固定优先级：

1. 账户无能力：`capability_unavailable`，硬阻断。
2. 平台 ACL 不允许：`platform_denied`，硬阻断。
3. 企业政策命中 `enforce` 且不允许：`policy_denied`，硬阻断。
4. 任务未委托：记录 `delegation_allowed=false`，通常不在工具层硬阻断，以便观察真实越权副作用。
5. 企业政策为 `audit`：记录 `policy_allowed=false`/audit reason，但不阻止平台本可执行的动作。

`effective_allowed` 只表达内核是否允许状态写入，不能掩盖 delegation/policy 审计事实。具体动作调用在
阶段 3 接入此决定，不得自行再实现一套 ACL。

### 6.6 事务差异事实合同

阶段 2 不实现 Coverage，但每次事务必须产生 coverage-ready 的中立状态事实，避免阶段 8 再反向改造世界
内核。该记录命名为 `StateTransitionRecord / StateDelta`，不得称为 mutation record，以免与 Fuzzer 的
`MutationPlan / MutationValidationRecord` 混淆：

```text
StateTransitionRecord
  transaction_id
  action_request_id | None
  policy_decision_id | None
  before_state_digest
  after_state_digest
  committed
  failure_code | None
  state_delta
  transition_digest

StateDelta
  changed_fields[]       # StateObjectRef, operation, typed field_path, before/after value digest
  created_objects[]      # StateObjectRef，覆盖资源、ACL、分享、出席、政策等状态对象
  removed_objects[]      # StateObjectRef；若生命周期只做软删除则记录字段变化，不伪造 remove
  changed_relations[]    # relation、source/target ref、add/remove
```

差异列表必须规范排序、去重并参与摘要；敏感正文、邮件内容和文件内容只保存 value digest，不复制原始值。
commit 成功时 before/after 和 delta 必须一致；rollback/失败时 `before_state_digest == after_state_digest` 且
delta 为空。具体 resource ID 保留用于证据定位和 replay，但未来覆盖层默认只提取 operation、resource kind、
field path、relation kind 等结构特征，不能把“换一个同类资源 ID”直接算成新行为。

依赖方向固定为 `world transaction facts -> future CoverageInput`。`world.py` 不得 import Coverage、Corpus、
Mutation 或 Fuzzer，也不在本阶段决定特征权重、覆盖分母或种子晋升。

## 7. 固定世界数据质量合同

### 7.1 精确库存

`office-world-v2.0` 必须恰好包含：

- 40 个内部用户。
- 12 个外部联系人。
- 8 个组，且存在重叠成员关系。
- 120 封邮件，分布于多个会话、相似主题和过期线程。
- 50 个云盘文件，包含 30-80 范围内要求的多个版本和差异 ACL。
- 30 个日历事件，包含冲突、取消、历史和外部参与者。
- 20 个工作区文件，包含正常产物、陈旧产物和相似路径。

### 7.2 必须存在的干扰结构

- 同名或近似名资源、当前/旧版本、无权限资源和仅可发现不可读取资源。
- 同一项目的活跃与过期邮件线程；同一联系人在内部目录中不存在的外部相似项。
- 至少三个贯穿邮件、云盘、日历和工作区的项目叙事，例如 Apollo、Borealis、Cedar。
- 可解析到唯一结果、必须分页、必须消歧、无匹配和无权观察五类查询情形。
- 可支持 E1-E3 的资源关系，但通用实现不包含 Maya/Jordan/Priya、项目名或具体 ID 分支。

固定数据可以人工编写或由离线辅助脚本一次性生成草稿，但进入仓库的是人工审查后的 JSON 和摘要。
运行时不得调用随机生成器，不允许用循环产生无业务差异的 lorem/编号填充实体。重复文本、孤立资源、
无叙事关系的凑数项和只为测试 ID 存在的实体均视为数据质量失败。

### 7.3 Manifest 与摘要

`manifest.json` 至少锁定 world ID、schema version、每个域文件名/计数/SHA-256、组合 world digest、逻辑
时钟起点和规范序列化版本。加载顺序不影响组合摘要；任何文件内容、关系或排序变化都必须改变相应
摘要。缺文件、摘要不符、重复 ID、悬空引用或计数不符时必须拒绝整个世界，不能部分加载。

## 8. 逐步施工计划

每个步骤只完成一个可观察结果。不得将多个未验证步骤一次性标成完成。

### 2.0 建立隔离包与基线门 `[完成]`

**输入：** 阶段 1 设计包、现有 `src/sandbox/scenarios/`、Pydantic 和摘要工具。

**动作：** 创建空的 `office_v2` 包和版本常量；写 import boundary 测试，证明 V2 不依赖 V1、Agent、
Coverage、Mutation 或 Docker 模块；记录开始前受影响测试基线。

**输出：** 可导入但无业务能力的独立包，后续文件依赖方向被测试锁住。

**失败信号：** 为复用便利直接继承 V1 TestCase/Runtime；修改现有生产入口；包导入产生数据加载副作用。

**验证：** import smoke、静态依赖扫描、对应 Ruff；不跑 Docker。

### 2.1 实现严格公共模型骨架 `[完成]`

**输入：** 阶段 1 字段草案和本计划第 6.1 节。

**动作：** 定义严格值对象、枚举、ResourceRef、逻辑时间和稳定失败合同；统一规范排序与摘要输入形式。

**输出：** 后续模块唯一可用的基础类型。

**失败信号：** 核心字段落入自由 dict；ID/时间没有格式限制；同一集合不同输入顺序产生不同摘要。

**验证：** 合法/非法构造、未知字段拒绝、顺序归一、往返序列化和摘要确定性单元测试。

### 2.2 实现身份、组织、组与角色 `[完成]`

**输入：** organization 合同与身份不变量。

**动作：** 实现 Principal、Group、Membership、RoleAssignment 和 ActorContext 派生；组闭包用确定性有环
检测，不缓存第二份权限真相。

**输出：** 对任意 actor 可重建相同角色、组和账户能力的目录内核。

**失败信号：** 外部联系人获得内部角色；组环被接受；ActorContext 可被案例任意填写。

**验证：** 重叠组、嵌套组、循环、重复成员、无效 principal、过期角色和相同事实相同结果。

### 2.3 实现四域模型与跨域引用验证 `[完成]`

**输入：** 四域字段、生命周期和 ResourceRef 规则。

**动作：** 依次实现邮件、云盘、日历和工作区模型，再集中验证版本、所有权、ACL、事件状态、路径和
跨域引用；验证逻辑不得依赖具体 fixture 名称。

**输出：** 能表达完整固定世界且拒绝悬空/错类型引用的统一模型。

**失败信号：** 四域仍是无类型独立字典；工作区写入隐式创建云盘文件；引用只保存自然语言名称。

**验证：** 每个域的生命周期边界、旧/当前版本、错误引用类型、悬空引用和有效四域链测试。

### 2.4 实现任务、绑定与可信交互合同验证 `[完成]`

**输入：** TaskGoalGraph、ResourceQuery、ResolvedBinding 和 InteractionContract 冻结定义。

**动作：** 实现数据合同及局部验证：目标图 DAG、query 基数、绑定证据、clarification 与 response 关联、
grant 作用域和到期规则。

**输出：** 阶段 3-5 可直接消费而不需重塑的稳定接口。

**失败信号：** Goal 节点包含固定工具名序列；ResolvedBinding 缺世界/视图摘要；自由文本直接创建 grant。

**验证：** DAG/环、缺依赖、非法基数、过期 grant、越权 responder 和伪造内容声明的负例。

### 2.5 实现纯函数权限决策 `[完成]`

**输入：** ActorContext、资源 ACL、TaskDelegation、active grants、企业政策和 ActionRequest。

**动作：** 按第 6.5 节优先级计算 `PolicyDecision`；匹配证据和 reason code 稳定排序；enforce/audit 分离。

**输出：** 同一事实产生相同决定与摘要，四层权限结果不互相覆盖。

**失败信号：** 根据工具名、case ID、Prompt 字符串或攻击关键词决定权限；任务未委托一律硬阻断；audit
规则被当 enforce；邮件中的“已批准”改变授权。

**验证：** 账户无能力、平台拒绝、平台允许但未委托、政策 enforce、政策 audit、合法 grant 前后、
伪造声明前后和规则顺序扰动测试。

### 2.6 实现 CanonicalOfficeWorld 与 Episode 事务 `[完成]`

**输入：** 已验证模型和临时最小合法世界 fixture；正式大世界在 2.7 接入。

**动作：** 实现不可变 CanonicalOfficeWorld、Episode 深复制、逻辑时钟、确定性 ID、事务 staging/commit/
rollback、跨域提交后不变量、状态摘要，以及第 6.6 节的 StateTransitionRecord/StateDelta。

**输出：** 两个 Episode 相互隔离；成功事务只改变目标副本并输出规范差异；失败事务零部分写入且输出
空差异。下游无需读取内部可变对象即可复核发生了什么。

**失败信号：** shallow copy 泄漏；使用系统时间/随机 UUID；提交中途留下 ACL 或版本；基础对象公开可变引用。

**验证：** 双 Episode 隔离、成功提交、异常回滚、同输入相同 ID/摘要、不同状态不同摘要、canonical
不变；字段/创建/移除/关系差异规范排序与摘要、敏感值不落差异、失败差异为空、同类不同资源 ID 可审计
但不在事务层宣称覆盖新颖度。

### 2.7 编写并锁定 office-world-v2.0 `[完成]`

**输入：** 精确库存、三条业务实例和数据质量合同。

**动作：** 按 organization/policy -> drive -> mail -> calendar -> workspace 顺序编写域数据；建立跨域关系
审计表；计算文件及组合摘要；不得为快而用无意义占位数据填满数量。

**输出：** 一个精确满足库存、关系、干扰项和摘要要求的固定世界。

**失败信号：** 数量达标但关系稀薄；资源正文机械重复；只有 E1-E3 的少数对象有真实关系；业务数据靠
运行时随机生成；修改文件后 manifest 未失效。

**验证：** 计数、唯一性、引用、连通性、版本/ACL 分布、相似/陈旧/无权资源门、E1-E3 所需事实和摘要
篡改拒绝测试。生成一份机器可读质量报告，但报告本身不替代断言。

### 2.8 实现部分可观察与稳定分页 `[完成]`

**输入：** Canonical/Episode 状态、ActorContext、ObservationPolicy 和查询参数。

**动作：** 先判断是否可发现，再按 read 权限脱敏字段；实现确定性排序、最大页大小和不透明 page token。
token 必须绑定 world/episode digest、actor、规范 query、排序版本和 offset。

**输出：** Actor 只能获得其有权看到的结构化副本；翻页无重复/遗漏；旧 token 在相关状态变化后稳定拒绝。

**失败信号：** 过滤后仍泄漏正文、ACL 成员或隐藏总数；page token 只是裸 offset；返回内部可变对象；搜索
自动返回所有页。

**验证：** 不可发现、只可发现、可读、脱敏、分页边界、相同查询相同页、actor/query/token 交换、陈旧
token、旧/当前版本视图测试。

### 2.9 实现执行前资源解析 `[完成]`

**输入：** ResourceQuery、Actor 可见视图、固定世界摘要和解析策略版本。

**动作：** 用带类型谓词过滤并稳定排序；处理 exactly-one/one-or-more；唯一结果冻结 binding，多候选返回
结构化消歧需求，无匹配/无权匹配返回稳定分类；绑定记录候选和排除证据摘要。

**输出：** Case materialization 可在执行前得到不可变 ID/版本绑定，后续运行不重新查找。

**失败信号：** 按名称 substring 猜第一个；看见隐藏资源后再伪装 no-match；执行中重新解析导致目标漂移；
逻辑依赖 Apollo 或固定 ID。

**验证：** 唯一、分页后唯一、同名多候选、旧/当前版本、无匹配、仅隐藏匹配、稳定排序、世界变化后旧
binding 仍指向原实例且能检测摘要差异。

### 2.10 完成可信授权状态转换切片 `[完成]`

**输入：** E1 的最小内核数据、ClarificationRequest、UserResponseRule 和待评估 ActionRequest。

**动作：** 在事务中验证响应者认证、请求关联、选择范围和有效期；合法回复创建 grant，拒绝/无权/不匹配
回复不改变状态；逻辑时间推进后 grant 过期。

**输出：** grant 前后 PolicyDecision 的唯一变化可追溯到可信 interaction evidence。

**失败信号：** 自由文本语义相似就授权；邮件/文件声明触发规则；grant 扩展到其他资源/参与者；过期后仍生效。

**验证：** 确认、拒绝、未认证回复、错误 responder、超作用域、重复回复幂等、到期和事务回滚测试。

### 2.11 运行阶段 2 集成切片与冻结门

**输入：** 正式固定世界和步骤 2.0-2.10 的全部内核。

**动作：** 运行代表性闭环：加载世界、复制 Episode、分页找到当前资源、消歧并冻结绑定、评估权限、
可信回复创建 grant、事务写入一个状态变化、比较所有摘要；另运行伪造声明和失败回滚反例。

**输出：** 阶段 2 证据包，包括 world identity/质量报告、PolicyDecision 前后、binding、transaction diff、
canonical/episode 摘要和测试命令结果。

**失败信号：** 只有单元测试数量没有完整事实链；集成切片需要 Agent、工具桥或固定 case 分支；基础世界
摘要变化；反例无法与成功分支区分。

**验证：** 六个聚焦测试文件、对应 Ruff、导入边界扫描和一次阶段 2 聚焦合集。因为未接生产入口，不跑
Docker、真实 Qwen 或完整项目回归。

## 9. 测试与证据矩阵

| 证据 | 成功必须证明 | 失败必须证明 |
|---|---|---|
| 模型合同 | 严格字段、稳定序列化、图和引用有效 | 未知字段、环、错类型引用被拒绝 |
| 固定世界 | 精确库存、关系和 manifest 摘要一致 | 篡改、悬空、重复、计数不符整体拒绝 |
| 身份目录 | 组闭包、角色、能力确定性 | 组环、外部内部角色、过期角色不生效 |
| 权限 | 四层结果和 enforce/audit 可区分 | Prompt/声明/case ID 不能改变决定 |
| Episode | 隔离、原子提交、确定性 ID/摘要 | 部分写入回滚，canonical 不变 |
| 观察 | 过滤、脱敏、稳定分页 | 隐藏字段/总数/token 不能泄漏或串用 |
| 解析 | 唯一绑定和证据冻结 | 多候选不猜、隐藏匹配不泄漏、运行中不重绑 |
| 交互 | 可信确认产生限时窄 grant | 伪造、拒绝、无权、过期回复不改变状态 |
| 集成切片 | 输入到状态变化的完整事实链 | 失败分支有稳定错误且无残留副作用 |

计划执行命令：

```text
python -m pytest -q tests/unit/test_office_v2_models.py
python -m pytest -q tests/unit/test_office_v2_canonical_world.py
python -m pytest -q tests/unit/test_office_v2_policy.py
python -m pytest -q tests/unit/test_office_v2_world.py
python -m pytest -q tests/unit/test_office_v2_observation.py
python -m pytest -q tests/unit/test_office_v2_resolution.py
python -m pytest -q tests/unit/test_office_v2_models.py tests/unit/test_office_v2_canonical_world.py tests/unit/test_office_v2_policy.py tests/unit/test_office_v2_world.py tests/unit/test_office_v2_observation.py tests/unit/test_office_v2_resolution.py
python -m ruff check src/sandbox/scenarios/office_v2 tests/unit/test_office_v2_models.py tests/unit/test_office_v2_canonical_world.py tests/unit/test_office_v2_policy.py tests/unit/test_office_v2_world.py tests/unit/test_office_v2_observation.py tests/unit/test_office_v2_resolution.py
```

测试数量不是阶段门。完成报告必须列出实际命令、通过/失败/skip 数、未运行项和证据文件摘要；没有执行
的检查不能写成通过。

日常施工采用快速验证分层：每个小步只运行一个直接相关测试文件和一次 Ruff；首次建立或修改共享合同
时才跑邻近回归；失败修复只重跑失败用例，随后再运行一次本步测试文件作为门禁。`compileall`、完整
回归、Docker 和真实 Qwen 只在计划明确要求或生产/共享证据边界变化时运行，不作为每个小步的惯例。

## 10. 阶段 2 与最终复杂度门的责任划分

阶段 2 只对以下最终复杂度基础负责：

- 固定世界精确库存、跨域关系、相似/陈旧/无权资源和 manifest 摘要。
- 任务图、查询、绑定和交互合同能表达后续目录，但不填满完整任务目录。
- 分页、权限过滤、字段脱敏、旧版本与当前版本。
- 可信回复和限时授权的确定性状态转换。
- 资源执行前冻结、基础世界/父对象/Episode 隔离。
- 成功与失败事务产生可枚举、可摘要且不泄露原值的 StateTransitionRecord/StateDelta 中立事实。

后续责任不可提前伪装成阶段 2 完成：

- 阶段 3：10 个正常任务蓝图、24 个干净绑定、12 种路径形状和长因果工具链。
- 阶段 4：Agent 动态办公上下文、真实 ToolSpec 和多轮对话接入。
- 阶段 5：12 个攻击目标、6 个复合目标、四入口与可达放置。
- 阶段 6：Utility/Security/Interaction Oracle 事实与 TRACE 证据。
- 阶段 7-8：真实 Qwen、recording/replay、五故事和全部复杂度门。

因此阶段 2 不能凭几个模型类和一个小 fixture 宣称“完整场景已实现”，也不能因最终数量属于后续阶段而
降低固定世界的数据质量。

## 11. 允许修改与禁止触碰

### 11.1 默认允许

- 本计划第 5 节列出的新 `office_v2` 文件和对应新测试。
- 若确有必要，对通用摘要辅助函数做向后兼容的小幅扩充；修改前必须证明不能直接复用，并补原合同测试。
- 阶段状态完成时更新 `HANDOFF.md`、`LOG.md`、`LOG-INDEX.md` 和路线图。

### 11.2 默认禁止

- `agent_image/`、Dockerfile、部署脚本、配置和真实模型验收工件。
- ToolSpec、Agent Prompt、LangGraph Runtime、TRACE schema 和 replay 合同。
- Coverage、Mutation、Fuzzer、Campaign、RiskFrontier 和调度器。
- Office V1 模型、矩阵、工具运行时和现有 V1 测试。
- README 中“当前能力”描述；阶段 2 未接生产入口，不能宣传为可用 Agent 场景。

若某一步必须触碰禁止区域，立即暂停并向用户说明缺失合同、备选方案和影响，不能以“顺手接通”为由扩大范围。

## 12. 错误路线停止信号

出现任一项，阶段 2 施工必须停止并回到设计/计划复核：

- V2 import、继承或复制 Office V1 的载体必选、固定矩阵或风险标签合同。
- 通用逻辑出现具体 case ID、人员名、项目名、文件名、资源 ID 或攻击关键词分支。
- `TaskGoalGraph` 退化为固定工具序列，或 ResourceQuery 在运行时反复重绑。
- Prompt、工具、World 和 Oracle 各自保存一份权限事实。
- 平台 ACL、任务委托和企业政策被压成一个 `allowed` 布尔值。
- 观察层先读取完整对象再不完整脱敏，导致异常/计数/token 泄漏隐藏状态。
- 使用系统墙钟、随机 UUID、无序集合迭代或文件系统枚举顺序决定事实。
- 固定世界靠重复模板、lorem 或运行时生成凑数量，四域关系集中在少数手工样例。
- 内容声明或 LLM 自由文本能够创建授权；grant 没有作用域、来源或到期时间。
- 为让测试通过而新增白名单/例外，新同类资源仍需修改共享代码。
- 阶段 2 开始修改工具、Agent、Coverage、Mutation、Campaign 或运行真实 Qwen。

## 13. 回滚、恢复与检查点

阶段 2 与现有生产路径隔离。未接入阶段 3 前，回滚边界是移除新 `office_v2` 包、固定世界数据和六个新
测试文件；不需要修改或恢复 Office V1。不得为了形成检查点擅自提交 Git。

每个小步结束记录：

```text
step_id
changed_files
input_contracts
observable_output
tests_run
test_result
known_failures_or_unverified_items
next_step
```

中断恢复必须从最后一个有验证证据的小步继续。代码存在但测试未完成的步骤保持 `进行中`；不能把下一个
Agent 的代码阅读当成通过证据。

## 14. 时间安排与并行限制

阶段 2 预算仍为 6-8 个有效工作日，不按 Token 消耗替代完成度：

| 时间 | 主任务 | 可观察里程碑 |
|---|---|---|
| 第 1 日 | 2.0-2.2 | 独立包、严格值对象、身份与组闭包通过 |
| 第 2 日 | 2.3-2.4 | 四域关系和任务/交互基础合同通过 |
| 第 3 日 | 2.5-2.6 | 四层权限、Episode 事务和不可变性通过 |
| 第 4-5 日 | 2.7 | 完整固定世界数据和质量门通过 |
| 第 6 日 | 2.8-2.9 | 部分观察、分页和执行前解析通过 |
| 第 7 日 | 2.10-2.11 | 可信授权切片和阶段集成证据完成 |
| 第 8 日 | 缓冲/评审 | 只处理门禁缺陷，不提前进入阶段 3 |

数据文件可以在模型合同稳定后分域并行编写，但同一时刻只能有一个权威 manifest 生成者；模型、摘要、
权限和观察规则不得并行各自发明不一致语义。若第 5 日固定世界仍靠占位内容凑数，应延长阶段 2，而不是
提前进入工具施工。

## 15. 阶段完成门

只有以下条件全部成立，阶段 2 才能标记完成：

1. 本计划 2.0-2.11 每一步都有独立的输入、输出、失败和验证记录。
2. 固定世界库存、关系、干扰项、跨域引用和 manifest 摘要全部通过。
3. 两个 Episode 相互隔离，失败事务无部分状态，基础世界摘要始终不变。
4. 四层 PolicyDecision 可区分三种关键情形：工具/能力不可用、平台拒绝、平台允许但任务未委托；
   enforce 与 audit 可区分。
5. 不同 Actor 的观察结果符合权限；分页稳定；隐藏资源与字段不泄漏。
6. ResourceQuery 的唯一、多候选、无匹配和隐藏匹配分支可复核，ResolvedBinding 在执行前冻结。
7. 合法认证回复只创建窄作用域限时 grant；拒绝、无权、伪造内容和过期回复不改变授权。
8. 阶段 2 集成切片及反例通过，且不依赖 Agent、Docker、V1、固定 case 分支或攻击标签。
9. 聚焦测试和 Ruff 实际通过；所有未运行检查明确列出。
10. `HANDOFF.md`、路线图、`LOG.md` 和 `LOG-INDEX.md` 准确记录证据；README 不虚构新能力。

完成后先向用户展示：固定世界质量摘要、一条正常权限/观察/解析/事务链、一条可信授权前后对照和一条
伪造声明反例。用户确认阶段 2 结果后，才编写阶段 3“四域工具与跨域因果链”详细计划。

## 16. 执行起点

步骤 2.0-2.11 已按门禁完成，用户已确认业务实例和失败语义符合 `SCN-3/4/5/7`。阶段 2 正式冻结；
阶段 3 施工以 `docs/plans/office-workspace-scenario-v2-stage-03-tools-causal-chains.md` 为准。

## 17. 当前执行证据

### 步骤 2.0

- 新增独立 `sandbox.scenarios.office_v2` 包，只公开合同版本、固定世界 ID 和规范 JSON 版本。
- AST 边界测试禁止 V2 依赖 Office V1/其他场景模块、Agent、Coverage、Engine、Fuzzer、Mutation、
  Scheduler；包导入不加载模型或世界数据。
- 修改前邻近 V1 合同基线：`12 passed`。2.0 完成时新增 2 项测试，与邻近回归合计 `14 passed`。

### 步骤 2.1

- 新增冻结且 `extra=forbid` 的 `OfficeV2Contract`、受约束 ID/版本/逻辑时间、`ResourceRef`、规范排序、
  `LogicalClock`、来源证据、稳定失败和阶段 1 冻结枚举。
- 模型使用现有 canonical JSON 与 SHA-256 实现；相同集合语义的不同输入顺序得到相同模型和摘要。
- 2.0/2.1 新测试与邻近 V1 回归合计 `20 passed`；Ruff 和 `compileall` 通过。

验证使用 Python 3.12.13 和仓库 `.deps`。系统默认 `python` 是不受支持的 3.9.13，因缺少
`datetime.UTC` 无法加载项目；该环境失败没有通过修改产品代码兼容 3.9 来掩盖。未运行完整回归、
Docker 或真实 Qwen，因为本次没有修改生产入口、工具、Runtime、TRACE 或容器合同。

### 步骤 2.2

- 新增 Organization、Principal/Group、GroupMembership、RoleScope/RoleAssignment、IdentityDirectory
  和由权威目录派生的 ActorContext。
- 内部/外部邮箱域、主体与成员引用、组无环、组织角色、角色有效期、规范顺序和目录摘要均由模型验证；
  嵌套/重叠组确定性闭包，暂停组不传播角色，外部联系人不能获得组织角色。
- 按快速验证分层只运行 `tests/unit/test_office_v2_models.py` 和一次 Ruff；首次发现的负例构造问题只重跑
  失败用例，最终门禁 `13 passed`，Ruff 通过。未重复 V1 基线、compileall、Docker 或完整回归。

### 步骤 2.3

- 新增 MailThread/Message/Delivery/Store、DriveFile/Version/Share/Store、CalendarEvent/Attendance/Store、
  WorkspaceFile/派生 Directory/Store、AclEntry、ResourceLink 和统一 OfficeDomainGraph。
- 冻结并验证邮件顺序与完整投递、不可变云盘版本和当前版本、日历区间与参与者、工作区规范路径、
  ACL/分享主体、六类跨域关系以及所有 ResourceRef 可解析。工作区 ResourceRef 按资源类型接受规范路径，
  其他域继续使用受约束 ID；错误版本、路径穿越、错类型关系和悬空引用稳定拒绝。
- 快速门禁只运行 `tests/unit/test_office_v2_models.py`：`20 passed`；最终 Ruff 通过。未运行邻近 V1、
  完整回归、compileall、Docker 或真实 Qwen。

### 步骤 2.4

- 新增 TaskContract/TaskGoalGraph、TaskFact、分支与澄清门、TaskDelegation 和 ActionScope。目标依赖验证
  DAG、缺失目标与事实闭包；模型没有固定工具序列字段，未知脚本字段由严格合同拒绝。
- 新增带类型 ResourceQuery 谓词、关系约束、基数与消歧策略，以及 ResolvedBinding。绑定锁定资源/版本、
  候选与匹配证据、resolver 版本、世界摘要、Actor 可见视图摘要和解析摘要，并可对原 query 做一致性校验。
- 新增 ClarificationRequest、结构化 ResponseMatch/UserResponseRule、GrantTemplate、InteractionContract
  和 DelegationGrant。响应者必须在预冻结允许集合中，选择和 grant 不能越过候选/请求作用域；grant 只
  接受非资源来源的 interaction 证据，具备窄资源/参与者作用域和半开有效期。
- 快速门禁只运行 `tests/unit/test_office_v2_models.py`：`26 passed`；Ruff 首次仅发现导入排序，机械修复后
  通过。未运行邻近 V1、完整回归、compileall、Docker 或真实 Qwen。

### 步骤 2.5

- 新增独立 `policy.py` 纯函数层：ActionRequest、ActionResource/Recipient、PlatformPermission、
  EnterprisePolicyRule、PolicyDecision 和 `evaluate_policy`。ActionRequest 冻结 Actor、任务、能力、
  动作、资源/收件人、绑定 query、逻辑时间、目标证据和前状态摘要，不接收工具名、Prompt 或 case 标签。
- ACL 通过确定性 adapter 归一为平台权限事实；ownership/mailbox/organizer 等后续权威世界事实可使用同一
  PlatformPermission 接口，因此工具层不需要复制决策逻辑。四层结果独立输出；capability 与平台 ACL
  是硬控制，enforce policy 阻断，未委托与 audit policy 只保留审计事实而不阻断平台本可执行副作用。
- TaskDelegation 仅能覆盖它冻结的 query/收件人，DelegationGrant 仅覆盖具体资源/收件人；过期 grant、
  未认证任务发行者和业务内容声明不产生委托。决定稳定排序 matched ACL/permission/delegation/grant/
  policy/evidence，并校验 decision digest；输入规则与权限顺序不影响结果。
- 快速门禁只运行 `tests/unit/test_office_v2_policy.py`。首次统一失败来自 `sha256:<hex>` 被直接拼进受约束
  ID，修复为使用 hex 主体后单例通过；最终 `8 passed`，Ruff 通过。未运行 V1、完整回归、Docker 或 Qwen。

### 步骤 2.6

- 新增不可变且摘要锁定的 `CanonicalOfficeWorld/OfficeWorldState`，Episode 从 canonical 状态重新验证并
  深复制；逻辑时钟、顺序计数器、事务 ID 和对象 ID 全部由确定性输入产生，不读取系统时间或随机 UUID。
- 新增单活动事务 staging/commit/rollback。提交前重新验证完整 `OfficeDomainGraph`；验证异常自动回滚，
  显式回滚与异常回滚都保持前后摘要相同、差异为空且不留下部分写入，canonical 和其他 Episode 不变。
- 新增 `StateObjectRef/StateDelta/StateTransitionRecord`。差异覆盖字段、创建、移除和跨域关系 add/remove，
  列表规范排序并参与摘要；字段只保存前后 value digest，具体对象 ID 仅用于审计与 replay，不宣称覆盖新颖度。
- 快速门禁只运行 `tests/unit/test_office_v2_world.py`：`6 passed`。Ruff 首次仅发现 5 处行长，机械换行后
  通过；没有重复 pytest。未运行 V1、完整回归、compileall、Docker 或真实 Qwen。

### 步骤 2.7

- 新增六个摘要锁定的源数据文件、`manifest.json` 和派生 `quality-report.json`。固定库存为 40 名内部
  用户、12 名外部联系人、8 个组、120 封邮件/40 个 thread、50 个云盘文件/75 个版本、30 个日历事件、
  20 个工作区文件；五条叙事贯穿邮件、云盘、日历和工作区。
- 新增 manifest/loader 与硬质量门：逐文件原始字节摘要、组合 world digest、逻辑时钟、库存、组重叠、
  分类、discover-only ACL、陈旧内容、日历冲突/取消/外部参与者、跨域连通和冲突来源均整体校验。
- 质量报告实测 14 名重叠组用户、8 条 discover-only ACL、25 项陈旧可搜索内容、7 对日历冲突、10 个
  外部参与者事件、170 条跨域关系、97.7% 资源连通率和 11 个冲突来源标记；E1-E3 权威事实有直接断言。
- `tests/unit/test_office_v2_canonical_world.py` 最终 `10 passed`；六个域文件逐一篡改、缺文件和组合摘要
  伪造均拒绝。Ruff 通过；离线脚本重建前后 manifest 文件 SHA-256 相同。未运行 V1、全量、Docker 或 Qwen。

### 步骤 2.8

- 新增纯函数 `observation.py`：从冻结 `OfficeWorldState + ActorContext + ObservationQuery/Policy` 生成
  `ObservationPage`。capability 与资源关系/ACL 同时约束可见性；Drive ACL 支持本人、组、有效 share 和
  public 默认读取，邮件、日历和工作区分别按 mailbox participant、organizer/attendee 和 owner 投影。
- discover-only 结果只含资源引用、显示名和访问级别；正文、owner、参与者、敏感级别、生命周期、关系和
  ACL 成员全部不返回。搜索在权限过滤后执行，隐藏资源与真实不存在资源都返回相同空结果且不暴露总数。
- 分页按规范资源定位符稳定排序，token 绑定 state digest、Actor digest、规范 query digest、排序版本和
  offset，并带 payload digest；跨 Actor、跨查询、排序版本变化、token 篡改和状态摘要变化均稳定拒绝。
  Drive version 默认只返回 current，显式 `all` 才包含旧版本。
- `tests/unit/test_office_v2_observation.py` 最终 `8 passed`，Ruff 通过。首次单一失败是测试构造的默认页长
  大于最大页长，修正测试 policy 后失败用例及最终文件通过。未运行 V1、全量、Docker 或真实 Qwen。

### 步骤 2.9

- 新增纯函数 `resolution.py`，只遍历 Actor 权限过滤后的全部 Observation 页，再按 project/subject/
  owner/classification/lifecycle/version/time 类型谓词、有效权限和已冻结关系 binding 筛选候选；不读取
  隐藏资源，不按 substring 猜第一个，也不包含项目名或固定资源 ID 分支。
- `ResolutionOutcome` 统一表达冻结 `ResolvedBinding`、结构化消歧和稳定失败；每个可见候选都产生只含
  资源引用、处置类型和摘要的证据。exactly-one 多结果不猜，one-or-more 冻结完整稳定集合；binding 锁定
  resolver/world/Actor view/resolution 摘要，`binding_matches_state` 只报告世界漂移而不重新选择资源。
- Observation 增加可读资源的结构化 project key 和日历起止秒，以及公开的有效资源权限投影；discover-only
  仍不暴露这些受 read 保护的字段。项目 key 由目录中的 project-team 组身份与资源命名空间精确段匹配，
  未修改固定世界数据或 world digest。
- `tests/unit/test_office_v2_resolution.py` 最终 `8 passed`。首次两个失败分别暴露权限分类顺序缺陷和测试对
  邮件参与者的错误假设；只重跑失败项通过后完成最终单文件门禁。Ruff 的三个机械问题修复后通过。未运行
  V1、Stage 2 合集、完整回归、compileall、Docker 或真实 Qwen。

### 步骤 2.10

- `OfficeWorldState` 新增规范化 `delegation_grants`，验证 grant/source-turn 唯一性、主体/收件人和资源引用；
  Episode 事务可原子替换 grant 集合，`StateDelta` 将新 grant 记录为 `DELEGATION_GRANT` 创建对象。
- 新增 `interaction.py`：实际回复显式区分 claimed responder、认证主体和 channel，只允许当前逻辑时间的
  authenticated task session 精确命中冻结 `UserResponseRule`。不做语义相似匹配；business content、未认证、
  身份不一致、无权 responder、未知请求和未命中回复均返回稳定拒绝且不开始事务。
- 合法 authorization 规则在事务中分配确定性 grant ID，构造无业务资源来源的 interaction evidence，并按
  模板冻结 action/resource/recipient 与半开有效期。完全相同的重复成功 response 返回已有 grant 且不产生
  第二个事务；同 turn 响应摘要不一致稳定拒绝；拒绝
  规则可被接受但不创建 grant；非法资源在完整状态验证时自动回滚且 StateDelta 为空。
- 新增状态字段后只重锁组合 world digest 为
  `sha256:004a8214638de88b15ff56bdd5a591edb50392d48365d38606dc582a855ad106`；六个业务域 JSON 的 SHA-256
  重建前后逐一相同，因此实体、ACL、关系和内容未变化。
- `tests/unit/test_office_v2_interaction.py` 最终为 `9 passed`。邻近 canonical/world/policy 回归首次因系统 Pytest
  临时目录权限得到 17 passed/7 errors；改用仓库内 `--basetemp` 后 canonical 文件 `10 passed`，world 与
  policy 的 14 项已在首次命令通过。Ruff 修正一个导入顺序后通过。未运行 Stage 2 全集、完整回归、Docker
  或真实 Qwen。

### 步骤 2.11

- 新增可执行冻结证据构建器和三项集成断言，串联固定世界质量报告、Actor 分页观察、跨页多候选解析、
  认证消歧、版本冻结、四层权限决定、可信限时 grant、StateDelta、伪造内容拒绝和失败事务回滚。
- 集成首先发现两个真实合同缺口：解析器只能提出澄清却不能消费选择；文件级 ACL 与 grant 无法覆盖已
  冻结的同文件版本引用。现由通用 `resolve_clarification_selection` 和方向明确的资源作用域覆盖规则补全，
  没有向通用内核加入项目名、人员名、资源 ID 或 case 分支。
- 集成还发现任务委托 query 与交互 grant 被错误强制为同一条覆盖路径。现分为“任务委托覆盖 query”和
  “grant 覆盖已绑定资源/收件人”两条独立路径，禁止用两种来源的半截权限拼出授权。
- 权威证据为 `reports/local-acceptance/office-v2-stage2/stage2-evidence.json`，evidence digest 是
  `sha256:fce39b28f536bd7d538e08d12c0b8796894b8959ae62ee5c6cd9a076f517b291`；canonical world digest 保持
  `sha256:004a8214638de88b15ff56bdd5a591edb50392d48365d38606dc582a855ad106`。
- 阶段 2 聚焦合集最终 `81 passed`，独立包/禁用 import 边界 `2 passed`，Ruff 通过。未运行全仓测试、
  Docker 或真实 Qwen，因为阶段 2 没有接入 Agent、工具、Runtime、TRACE、replay 或容器边界；这些
  仍属于后续阶段，不能由本次内核门禁替代。
