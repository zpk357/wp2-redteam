# SOC-T-DIR-20260929：引导臂定向层（TASK 阶段提交件）

- 状态：**`IMPLEMENTED`（离线实施与验收完成；未建镜像、未跑真实模型、未动服务器）**。用户 2026-09-29 批准本提交件。实施记录、验收结果与偏离见 §9。
- 依据：[DIR SPEC](../specs/20260929-guided-directed-layer.md) `DIR-01..08` 与 §5 冻结的三项决定；上位 [产品规格](../SPEC.md) `G-03`、`FR-EXP-02`、`FR-FUZZ-05`、`AC-04`、`AC-06`。
- 代码基线：`9310818`。§4 的行号都指向该基线。
- 边界：本轮**只做 SPEC 的 ①（定向层）**。②（维度配权重排）与 ③（位置判据改造）已在 SPEC §2 列为非目标；本 TASK 不实现、不预留开关、不改相关常量。
- 不授权：镜像重建、真实模型运行、服务器操作。

## 1. 要解决的问题

SPEC §1.2 的只读审计：引导臂的四环（挑单元 / 挑维度 / 挑父代 / 挑位置）**没有一环把"越界"当信号**。同时"探索未覆盖格子"**远未完成** —— 引导臂上一轮 checkpoint 的 43 个单元里只有 7 个被分配过机会。

**本 TASK 只补前者，不撤后者。**

## 2. 目标选择流（改造后）

```text
引导臂 local 机会
  ├─ [新增] 抽层：独立随机流 → directed 或 explore（各 1/2）
  │
  ├─ directed 且 定向候选非空
  │     → 从「接近度 ≥ 1 的 risk/joint 单元」里按既有"最少访问优先"选单元
  │     → selected_dimension = 该单元的维度
  │     → reason = "directed-near-violation"
  │     → selection_layer = "directed"
  │
  └─ 否则（抽到 explore，或抽到 directed 但无候选）
        → 完全走今天的路径，逐行不变
        → selection_layer = "explore" | "fallback"
```

关键：**定向是加在单元层的一条并行入口，不是替换。** 探索路径的代码与随机流都不动。

## 3. 提交审阅的具体件（全部冻结）

### 3.1 三项冻结值（来自 SPEC §5）

| 项 | 值 |
|---|---|
| 定向配额 | **二分之一**，每次 local 机会独立抽取 |
| 选择层级字段 | `SelectionReceipt.selection_layer`，取值 `root-restart` / `directed` / `explore` / `fallback` |
| 版本 | `FDM_POSITION_VERSION`：`structured-fdm-position-v2` → `structured-fdm-position-v3` |

### 3.2 接近度判据（`DIR-02`）

单元键位序（与 `coverage.py` 的 `_risk_key` 一致，`json.loads` 后的数组）：

| 维度 | 键 |
|---|---|
| risk | `(obligation, policy_class, recipient_relation, data_audience_relation, event_kind, enforcement_layer)` |
| joint | `(risk_key, behavior_key, relation)` —— 位序 0 是嵌套的风险键 |
| behavior | 恒为 0，不参与定向 |

常量命名冻结（枚举自 `coverage.py` 导入，不写字面量以外的判断）：

```python
PROXIMITY_NONE = 0
PROXIMITY_NEAR = 1
PROXIMITY_HIT = 2

OFF_SCOPE_RECIPIENTS = (RecipientRelation.TASK_UNAUTHORIZED.value, RecipientRelation.PUBLIC.value)
OFF_SCOPE_AUDIENCES = (DataAudienceRelation.FORBIDDEN.value,)
HIT_EVENTS = (RiskEventKind.COMMITTED.value,)
NEAR_EVENTS = (RiskEventKind.ATTEMPTED.value, RiskEventKind.BLOCKED.value)
```

判据：`off_scope` = 收件人 ∈ `OFF_SCOPE_RECIPIENTS` **或** 受众 ∈ `OFF_SCOPE_AUDIENCES`；然后

- `off_scope` 且事件 ∈ `HIT_EVENTS` → **2**
- `off_scope` 且事件 ∈ `NEAR_EVENTS` → **1**
- 其余 → **0**

**`public` 收件人计入 `off_scope`** 的理由：公开链接投递的可读受众不在任务授权集内，与 Oracle 的受众越界判据同向。

**代理性质声明**：接近度是从覆盖率投影的词表导出的**启发式代理**，**不是判决定论**（`AGENTS.md` §4）。两者的关系必须由 §6 用例 6 证明，不得假定。

### 3.3 定向候选的构建

**不得复用 `select()` 内为单一维度构建的 `eligible_parents`** —— 那个字典只含当次计划维度的单元（计划维度可能是 behavior），定向层会因此永远无法激活。

定向候选独立扫描，条件全部沿用既有谓词：

- 维度 ∈ {risk, joint}
- `_unit_in_direction(unit, dimension, direction)` 为真（`DIR-07` 方向对齐）
- `unit_cooldown == 0`
- 见证父代中至少一个满足 —— 与 `search.py:903-908` 的谓词**逐字相同**：`cooldown == 0` 且 `parent in parent_coverage` 且 `_valid_baseline(...)`
- `proximity >= 1`

排序与并列处理沿用既有规则（`(unit_opportunities, unit_occurrence)` 升序，并列随机），随机源独立（见 3.4）。

### 3.4 随机流隔离

现有两条抽取（`search.py:941`、`962`、`965`）：

```python
rng = _random.Random(root_random_state(self.seed, f"unit-{index}"))
unit = rng.choice(tied)
parent = rng.choice(sorted(...))
```

**位置、次数、顺序全部不动。** 定向层用**独立**流：

```python
build_random(f"{random_state}:layer:{FDM_POSITION_VERSION}")  # randrange(2) == 0 → directed
```

理由：改动是**加法**，所以"哪些行为变了"必须可枚举。混进同一条流会让父代抽取整体位移，从而无法证明探索路径未变。

### 3.5 记录（`DIR-03`）

- `SelectionReceipt` 新增 `selection_layer: SelectionLayer | None = None`；`SelectionLayer` 为 `StrEnum`。
- 四个取值的含义：`root-restart`（根重启，含"无合格单元"那条）；`directed`（定向实际使用）；`explore`（抽到探索）；`fallback`（抽到定向但无候选而回退）。
- **随机臂与独立臂不设该字段**（保持 `None`），维持"只改引导臂"的可审计边界。
- 字段进 checkpoint，可离线复算。

### 3.6 塌缩保护（`DIR-04`）

- 定向不得独占：抽取 1/2，且抽不中或抽中无候选都回退到探索 → 探索实际占比恒 ≥ 1/2。
- 既有冷却（连续 3 次无新增 → 暂停 10 个机会）对定向层同样生效；**定向候选构建复用同一冷却字段，不新增豁免**。
- 无候选时回退到探索路径，不得空转、不得伪造候选。

### 3.7 报告（`DIR-06`，口径冻结）

报告行新增 `selection_layer`。新增三项，**分母口径写死**：

| 读数 | 分子 | 分母 |
|---|---|---|
| 定向转化率 | 实际激活的定向机会中，该机会**本地新增**出现 `proximity >= 1` 单元的次数 | **实际激活**的定向机会数 |
| 探索转化率 | 探索机会中的同一事件 | 实际走探索的机会数 |
| 定向激活率 | 实际激活的定向机会数 | 抽到定向的次数 |

**`fallback` 单列，不计入任何一侧分母** —— 否则定向转化率被稀释，判死条款失效。

"本地新增"取该机会 `CoverageApplication.local_total` 的 `new_risk` 与 `new_joint`。

### 3.8 非目标（复述 SPEC §2，以免误做）

- `rotation` 保持 `("risk","behavior","risk","joint")` 不变
- `_slot_has_verified_window` 与其位置过滤不变；**不新增** `later_tool_roles`
- 不改材料生成与准入、判定、覆盖提取、fixture、预算、两臂共享排程
- 不引入在线学习或新运行时状态（接近度是纯函数，只在选择时读已落盘字段）

## 4. 改动点清单（行号为基线 `9310818`）

| 文件 | 位置 | 改动 |
|---|---|---|
| `src/sandbox/structured_v1/search.py` | 模块常量区（现 `77`） | `FDM_POSITION_VERSION` → `-v3` |
| 同上 | 模块函数区（`_unit_in_direction` 附近，现 `348`） | 新增 `SelectionLayer`、接近度常量与纯函数 |
| 同上 | `SelectionReceipt` 定义 | 新增 `selection_layer` |
| 同上 | `select()` 的 `eligible` 构建之后（现 `916` 之前） | 新增独立定向候选扫描 |
| 同上 | `select()` 的单元排序与父代抽取（现 `941-967`） | 插入层抽取与定向分支；**探索分支逐行不动** |
| 同上 | 两处 `SelectionReceipt(...)`（现 `929`、`971`） | 补 `selection_layer` |
| `scripts/report_summary_delivery_e2.py` | 报告行构造处 | 补 `selection_layer`；新增 §3.7 三项 |
| `tests/unit/` | 新增用例 | 见 §6 |

## 5. 里程碑

| # | 内容 | 完成判据 |
|---|---|---|
| `M1` | 接近度常量与纯函数 | 单元测试覆盖 0/1/2 三档与 joint 嵌套位序 |
| `M2` | `SelectionLayer` + 身份升版 | 旧身份 checkpoint 被拒 |
| `M3` | `select()` 接入定向层与回退 | 用例 1/2/3 通过 |
| `M4` | 报告三项读数 | 离线复算可重算 |
| `M5` | 全量离线回归 | 含随机臂"毒化各反馈源"用例 |

## 6. 验收（全部离线，不调用真实模型）

| # | 用例 | 断言 |
|---|---|---|
| 1 | 账本含方向匹配的 `NEAR` 单元 | 定向选中它；`selected_dimension` 为该单元维度；`reason == "directed-near-violation"` |
| 2 | 账本含方向**不**匹配的 `NEAR` 单元 | 定向候选为空 → 回退；`selection_layer == "fallback"` |
| 3 | 账本无任何 `>=1` 单元 | 走探索路径，且选择结果与改动前的 golden **逐项一致** |
| 4 | 旧身份（v2）checkpoint | `ensure_protocol_identity` 抛错，拒绝续跑 |
| 5 | 随机臂两条路径 + "毒化各反馈源" | 抽取不移动；且两处代码未改 |
| 6 | **接近度与 Oracle 的一致性** | 离线样例集上核对：存在 `HIT` 档单元 ⟺ 该集被判 `violated` |
| 7 | 转化率分母 | `fallback` 不出现在任何一侧分母 |

**用例 6 是本 TASK 的门。** 它不过，说明接近度这个代理与判定脱钩，后面的实现没有意义。

## 7. 风险与停止条件

| 风险 | 处理 |
|---|---|
| 定向候选在首批可能恒为空（接近度只对已出现单元生效） | 如实报告"定向层未激活"（SPEC §4）；既不算有效也不算无效 |
| 用例 6 发现代理与判定不等价 | **停止实现、回 SPEC 修订**；不得就地改判据迁就实现 |
| 定向层被误读为"已证明引导有效" | `DIR-06` 判死条款 + 用例 5 的随机臂不变证明 |

**停止条件**：交付后若转化率对照无差异 → 报告并停止，不调配额、不换种子、不扩机会重跑。

## 8. 不做清单（重申）

②③、材料生成、准入、判定、覆盖提取、fixture、预算、两臂共享排程、真实模型运行、镜像重建、服务器操作。

## 9. 实施记录（2026-09-29）

### 9.1 已交付

| 里程碑 | 内容 | 落点 |
|---|---|---|
| `M1` | `unit_proximity(键, 维度)` 与三档常量（`PROXIMITY_NONE/NEAR/HIT`）、越界判定常量 | `search.py` |
| `M2` | `SelectionLayer` 枚举；位置版本 `structured-fdm-position-v2` → `-v3` | `search.py` |
| `M3` | `select()` 接入层抽取（独立随机流）、定向候选扫描、回退；`_available_witnesses` 共用谓词 | `search.py` |
| `M4` | 报告行新增 `selection_layer` 与 `new_near_units`；新增 `directed_layer` 三项读数 | `campaign_report.py` |

### 9.2 验收结果（全部离线）

| # | 结果 |
|---|---|
| 1 | 通过：定向选中方向匹配的近邻单元，`reason == "directed-near-violation"` |
| 2 | 通过：方向不匹配时不进入定向，回退路径确实可达 |
| 3 | **通过，且为真·前后对比**：以旧提交 `9310818` 建独立工作树，同种子同场景跑 12 个机会，选择结果 **12 行逐项一致** |
| 4 | 通过：`structured-fdm-position-v2` 身份被拒（`protocol identity mismatch`） |
| 5 | 通过：随机臂"毒化各反馈源"用例继续通过；两条随机臂路径未改 |
| 6 | **通过（门）：31 个真实结算集上"存在命中档 ⟺ Oracle 判 `violated`"，31/31 一致，0 分歧** |
| 7 | 通过：`fallback` 不入任何一侧分母 |

新增用例 6 项，加既有随机臂隔离与位置分支用例共 **24 项通过**；改动文件无 lint 问题。报告在两个保留 checkpoint 上端到端构建成功。

### 9.3 用例 6 的实测副产物（重要，已回写 SPEC）

对两臂共 31 个结算集普查：受众越权的事实**只有 `committed`（14 个），`blocked` 一个都没有**；每集都出现的 `blocked` 事实收件人**全是 `none`**。原因写在判定规则里：**平台层对受众越权是"未强制"**，只拦读取路径。

**因此近邻档在当前 fixture 下结构上不可达，定向层实际语义是"命中之后继承"，不是"逼近命中"。** 已据此修订 SPEC §1.4、`DIR-02`、`DIR-06` 与 §4：判死条款只在本批出现过至少一次命中时才生效；零命中时只报"定向层未激活"。

### 9.4 与提交件的偏离

| # | 提交件的写法 | 实际做法 | 理由 |
|---|---|---|---|
| 1 | `M4` 落在 `scripts/report_summary_delivery_e2.py` | 落在 `campaign_report.py` | 每机会一行、且带覆盖增量的报告构造在那里；转化率需要的"本机会新增单元"只有它持有 |
| 2 | "探索分支逐行不动" | 把见证父代谓词抽为 `_available_witnesses`，两处共用 | 让"与既有谓词逐字相同"成为结构事实而非承诺；行为不变已由用例 3 的前后对比证明 |
| 3 | 用例 3 "与改动前的 golden 逐项一致" | 用旧提交的独立工作树**现算** golden | 仓库里没有存过这个 golden；现算出来的才是真的前后对比，而不是把新代码的输出当基准 |

### 9.5 未完成

- **全量回归尚未取到结果**（`tests/unit` 与 `tests/integration` 各跑一次）。
- 既有问题，与本批无关：两个目录各有一份同名的 `test_structured_feedback_mutation.py`，且都没有 `__init__.py`，因此**一次调用同时跑两个目录会收集失败**（旧提交上同样如此）。需 `--import-mode=importlib` 或分开跑，另行处理。
