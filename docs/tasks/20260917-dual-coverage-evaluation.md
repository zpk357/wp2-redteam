# 双重覆盖反馈与覆盖率引导优越性评测：任务拆解

> 2026-09-19 用户决定：停止沿用本计划的未执行安排。T00–T03 的实现与验收记录保留；
> T04–T07 不再按本计划推进。以下状态为停止时的历史记录，不表示当前执行授权。
> 新方向见已批准的 [轻量探索比较规格](../specs/20260919-lightweight-exploration-comparison.md)，
> 替代计划为 [新 TASK](20260919-lightweight-exploration-comparison.md)（`DRAFT`，尚未执行）。

- Task Plan ID：`TASK-DUAL-COVERAGE-EVAL-20260917`
- 状态：`IN_PROGRESS`
- 日期：`2026-09-17`（状态更新于 `2026-09-19`）
- 对应需求 SPEC：[`../specs/20260917-dual-coverage-evaluation-design.md`](../specs/20260917-dual-coverage-evaluation-design.md)（`SPEC-DUAL-COVERAGE-EVAL-20260917`，已批准）
- 对应产品 SPEC：[`../SPEC.md`](../SPEC.md)
- 基线：`main @ 33e9611`（本 TASK 创建时）
- 执行条件：已满足；用户于 `2026-09-17` 批准本任务拆解方向并授权按 `AGENTS.md` §7 提交

## 0. 进度摘要（给接手的人）

**读完这一节再往下看。子任务状态以 §4 总表为准。**

| 范围 | 状态 | 说明 |
|---|---|---|
| `T00`–`T03` | `DONE` | 代码改动、测试与报告层已完成并各自提交，见 §17 |
| `T04`–`T06` | `BLOCKED` | 需要用户租用的 Docker / 模型环境；当前环境不可运行 |
| `T07` | 未开始 | 属确定性评分层，等用户确认联合单元四元组后启动 |

**尚未解决、会影响后续工作的两项**（不要当成已定论）：

1. §6 `T01` 实现说明记录的**"两条件" vs "三条充分条件"**解释差异，用户尚未确认；
2. §7 `T02` 记录的行为变化——随机臂不再享有风险进度轮转，**必须在实验报告中声明**。

**语义评分层不在本 TASK 内**，见
[`20260917-finding-severity-scoring.md`](20260917-finding-severity-scoring.md)。

## 1. 目标

在本地完成使「引导 vs 随机」对比结论成立所必需的代码改动与报告层，并准备一次可量化成本与晋升频率的预实验；正式配对实验待用户租用环境后按冻结协议执行。

本 TASK 不评判任何实验结论，也不宣称引导模式更优。

## 2. 状态规则

只使用 `DRAFT`、`READY`、`IN_PROGRESS`、`BLOCKED`、`DONE`。

- 当前全部子任务为 `DRAFT`，表示拆解待用户 review。
- 用户确认后，第一个无依赖子任务改为 `READY`，其余在前置完成前保持 `BLOCKED`。
- 缺少环境、授权或判定证据时使用 `BLOCKED`，记录恢复条件，不得把未知项当成结论。
- 预实验与正式实验相关子任务在用户提供环境前一律 `BLOCKED`。

## 3. 总体依赖关系

```text
T00 工程基线与验证命令
  -> T01 入队判据改为"两条件满足其一"
  -> T02 随机臂方向与候选去重公平化
  -> T03 报告层逐代指标与晋升计数
      -> T04 预实验（一对 x 每臂 30 Episode）   [需环境]
          -> T05 冻结实验协议与成对数量
              -> T06 正式配对实验               [需环境]
T03 -> T07 联合单元粗粒度定义与选项一主指标（本轮不排期）
```

T01 必须先于 T04：预实验要统计晋升次数，而晋升判据正是 T01 要改的对象。

## 4. 子任务总表

| ID | 子任务 | 状态 | 前置依赖 | 对应 SPEC |
|---|---|---|---|---|
| `T00` | 工程基线与验证命令 | `DONE` | 无 | 设计说明 §6.1 |
| `T01` | 入队判据改为"两条件满足其一" | `DONE` | `T00` | 设计说明 §2.1、§9 决定 2 |
| `T02` | 随机臂方向与候选去重公平化 | `DONE` | `T00` | 设计说明 §3、§6.1 `C1`/`C2` |
| `T03` | 报告层逐代指标与晋升计数 | `DONE` | `T00` | 设计说明 §4.1、§6.1 `C3` |
| `T04` | 预实验：一对 × 每臂 30 Episode | `BLOCKED` | `T01`–`T03`、环境 | 设计说明 §9.1 |
| `T05` | 冻结实验协议与成对数量 | `BLOCKED` | `T04` | 设计说明 §4.4、§9.1 |
| `T06` | 正式配对实验 | `BLOCKED` | `T05`、环境 | 设计说明 §4、§5 |
| `T07` | 联合单元粗粒度定义与选项一主指标 | `DRAFT` | `T03` | 设计说明 §2、§4.1、§9 决定 3 |

## 5. T00：工程基线与验证命令

- 状态：`DONE`
- 前置依赖：无
- 被依赖项：`T01`、`T02`、`T03`

### 背景

`AGENTS.md` §6 要求的最低检查之一 `scripts/project_ruff.cmd check src agent_image tests` 当前**不通过**，存在 2 个基线错误，均在测试文件：

- `tests/unit/test_office_expression_mutation.py:22` `F401`（导入未使用）；
- `tests/unit/test_office_v2_public_cli_entry.py:1` `I001`（导入块未排序）。

若不先修，后续每个任务的 Ruff 验证都会被这 2 个与改动无关的错误干扰。

### 包含范围

- 清理上述 2 个导入问题（纯导入语句，不改变行为）。

### 不包含范围

- 不改任何生产代码逻辑、不改测试断言、不调整 Ruff 规则配置。

### 验收 checklist

- [x] `scripts\project_ruff.cmd check src agent_image tests` 退出码 0；
- [x] `tests/unit` 仍全部通过（数量与基线一致，不得减少）。

### 验证结果（2026-09-17）

| 命令 | 退出码 | 结果 |
|---|---|---|
| `.venv\Scripts\python.exe -m compileall -q src agent_image tests` | 0 | 无输出 |
| `scripts\project_ruff.cmd check src agent_image tests` | **0** | `All checks passed!`（修复前为 2 个错误） |
| `.venv\Scripts\python.exe -m pytest tests/unit -o addopts="" -q` | 0 | **832 passed**, 6 warnings |

修复内容：删除 `tests/unit/test_office_expression_mutation.py` 中未使用的 `OfficeMutationProviderKind` 导入；在 `tests/unit/test_office_v2_public_cli_entry.py` 的导入块后补足空行。

计数口径说明：旧审计记录 `tests/unit` 为“792 个结果符号”，本次为 `832 passed`。两者计数方式不同（进度符号数与测试数），未追查差异来源；两次运行均为退出码 0 且无 `F`/`E`。

### 验证方法

```text
.venv\Scripts\python.exe -m compileall -q src agent_image tests
scripts\project_ruff.cmd check src agent_image tests
.venv\Scripts\python.exe -m pytest tests/unit -p no:cacheprovider --basetemp=<tmp> -q
```

### 风险与回滚

- 风险：低。仅导入语句。回滚：单独提交，`git revert` 即可。

### 待确认

- 本项属于 `AGENTS.md` §2 所述"不改变行为的微小维护"，是否需要独立 TASK 由用户确认。

## 6. T01：入队判据改为"两条件满足其一"

- 状态：`DONE`
- 前置依赖：`T00`
- 被依赖项：`T04`

### 对应需求

设计说明 §2.1（入队判据双轨的事实）与 §9 决定 2。

### 包含范围

修改引导臂的入队判定，使以下两条件**满足其一**即可晋升：

1. 发现新的风险证据（现有 `RISK` 路径）；
2. 走出新的行为路径 **且** 正常任务完成（即放开当前不可达的 `EXPLORATION` 路径）。

两种情形都继续要求：目标保持 = `PRESERVED`、目标匹配 = `MATCHED`、证据完整，并保留 `target_behavior_signature` 去重（已出现过的行为组合不重复入队）。

### 不包含范围

- 不改随机臂（继续强制 `NO_PROMOTION`）；
- 不新增联合单元判定（属 `T07`）；
- 不改风险进度等级语义；
- 不改变异算子、提示、Agent 或 Oracle。

### 预计修改区域

- `src/sandbox/fuzzer/v2_campaign_loop.py`（引导臂晋级判定分支）；
- `src/sandbox/fuzzer/v2_promotion.py`（如需为接线调整分类，保持枚举与语义稳定）；
- 对应单元测试。

### 受影响数据流

Episode 结算 → 行为评估 → 晋级判定 → 语料目录 → 下一轮父种子池。

### 实施 checklist

- [x] 确认当前两套判定的确切控制流与不可达分类；
- [x] 实现"两条件满足其一"，保持硬门与去重不变；
- [x] 新增测试：新风险证据 → 晋升；
- [x] 新增测试：新主要行为 + 正常任务完成 → 晋升；
- [x] 新增测试：新主要行为但正常任务未完成 → 不晋升；
- [x] 新增测试：目标漂移 / 目标保持未验证 → 不晋升；
- [x] 新增测试：已出现的行为签名 → 不晋升；
- [x] 新增测试：随机臂始终 `NO_PROMOTION`。

### 验收 checklist

- [x] 上述 7 条断言均能直接区分行为成立与不成立（不是只断言"无异常"）；
- [x] `compileall`、Ruff、`tests/unit` 全部通过；
- [x] 每个晋升决定仍带 `reason_codes`，可审计。

### 实现说明（2026-09-17）

把引导臂的入队判定从 `promote_coverage_artifact` 内联分支抽出为纯函数
`resolve_promotion_decision`（`src/sandbox/fuzzer/v2_promotion.py`），`promote_coverage_artifact`
改为调用它。这样判定可以被直接断言，也消除了审计指出的"两处判定、一套不可达"的状态。

满足以下**任一**条件即入队：

| # | 条件 | 来源 | 对应 disposition |
|---|---|---|---|
| 1 | 新风险证据 | `classify_v2_promotion` 的 `RISK` | `RISK` |
| 2 | 新主要行为 **且** 正常任务完成 | `classify_v2_promotion` 的 `EXPLORATION`（原先不可达） | `EXPLORATION` |
| 3 | 已尝试选定目标 **且** 行为签名未入队 | 现有行为（保留，避免减少晋升） | `RISK` |

三条路径共同要求：目标保持 `PRESERVED`、目标匹配 `MATCHED`、证据完整、行为签名未出现过。
一律使用 `target_behavior_signature` 作为 `behavior_contribution_keys`，保持去重键空间单一。

**需要用户确认的一处解释**：设计说明写的是"两条件满足其一"。本次实现保留为**三条充分条件**
（即前两条 **加上** 现有路径），原因是第 3 条自 2026-09-10 起就是真实生效的晋升通道，
删除它会**减少**晋升频率，而"晋升太少"正是 §9.1 列出的最大实验风险。当前实现是现有行为的
严格超集：**不减少任何现有晋升**。若要改为严格的两条件版本，是一处小改动，需用户明确指示。

### 验证结果（2026-09-17）

| 命令 | 退出码 | 结果 |
|---|---|---|
| `.venv\Scripts\python.exe -m compileall -q src agent_image tests` | 0 | 无输出 |
| `scripts\project_ruff.cmd check src agent_image tests` | 0 | `All checks passed!` |
| `pytest tests/unit/test_office_v2_promotion_resolution.py` | 0 | **16 passed** |
| `pytest tests/unit` | 0 | **848 passed**（`T00` 后为 832，新增 16） |
| `pytest tests/integration` | 0 | **29 passed** |

新测试文件：`tests/unit/test_office_v2_promotion_resolution.py`（16 项，逐条对应上面 checklist）。

**未验证项**：无真实 Docker / 真实模型运行；晋升频率的实际变化需要在 `T04` 预实验中测量。

### 风险与回滚

- 风险：放宽后晋升频率显著上升，语料池被"只探索"的样本稀释，反而削弱引导效果。
- 失败信号：预实验中几乎每轮都晋升（池子线性爆炸）。
- 回滚：本子任务单独提交，`git revert` 可恢复原判据。

### 尚未解决

- 若预实验显示晋升过多或过少，此判据可能需要再次调整；调整属于新 SPEC 变更，不得在预实验中途暗改。

## 7. T02：随机臂方向与候选去重公平化

- 状态：`DONE`
- 前置依赖：`T00`
- 被依赖项：`T04`

### 对应需求

设计说明 §3（两臂设计表）、§6.1 `C1` 与 `C2`、§9 决定 1。

### 包含范围

1. 随机臂的方向选择改为在 4 个风险池之间**均匀随机**，不读取 `state.risk_progress`、不读取任何历史 Episode 结果；
2. 候选文本/结构层面的重复检查在**两臂统一启用**（该检查只使用本臂已生成内容，不属于覆盖反馈）；
3. 新增"随机臂选择不受历史影响"的断言测试。

### 不包含范围

- 不改引导臂的方向规则（仍按最低风险进度）；
- 不改行为签名去重（属入队机制，见 `T01`）；
- 不改变异算子选择方式。

### 预计修改区域

- `src/sandbox/fuzzer/v2_campaign_loop.py`（`progress_states` 的策略分支）；
- `src/sandbox/fuzzer/v2_risk_pool_scheduler.py`（如需一个不读进度的方向选择入口）；
- `src/sandbox/fuzzer/v2_real_runtime.py`（去重摘要集合与 `candidate_history` 的策略分支）；
- `src/sandbox/fuzzer/v2_campaign_store.py`（若存储层断言需要同步，仅限一致性约束，不放宽既有守卫）；
- 对应单元测试。

### 受影响数据流

下一代分配 → 方向选择 → 候选生成门控 → 父种子池。

### 实施 checklist

- [x] 确认随机臂当前读取 `risk_progress` 的确切路径；
- [x] 随机臂方向改为均匀随机；
- [x] 两臂统一启用候选文本/结构重复检查；
- [x] 新增测试：随机臂在同一 `campaign_seed` 下，其方向选择不随历史 Episode 结果改变；
- [x] 新增测试：两臂的重复检查行为一致；
- [x] 确认存储层"随机模式不晋升、不改目录"的既有守卫仍通过。

### 验收 checklist

- [x] 新测试能直接区分"随机臂读了历史"与"没读历史"；
- [x] `compileall`、Ruff、`tests/unit`、`tests/integration` 全部通过；
- [x] 存储层断言未被放宽。

### 实现说明（2026-09-17）

**方向规则**：`v2_risk_pool_scheduler` 的选择原语新增 `prefer_lowest_progress` 参数。
`True` 保持最低进度优先；`False` 把全部非空风险池视为并列，并用 `uniform-risk-direction`
作为收据理由。`choose_next_allocation` 对随机臂传入 `progress_states=()` 与
`prefer_lowest_progress=False`。

副作用（有意）：随机臂的 `FormalRiskAllocation.risk_progress_digest` 现在是对空进度的摘要，
因此**同一 `campaign_seed` 下，随机臂的 allocation 摘要与历史风险进度无关**——独立性成为可机器校验的性质。

**候选去重**：把原先内联的三处 `frozenset() if RANDOM_INDEPENDENT else ...` 计算抽出为模块级
`build_candidate_dedup_filters`，它**不接受策略参数**，两臂因此执行同一套重复检查。该门控只使用
"本臂已生成的内容"（本臂语料 + 谱系 + 候选历史），不读取任何覆盖结果，符合修订后的 `FR-EXP-01`。

### 验证结果（2026-09-17）

| 命令 | 退出码 | 结果 |
|---|---|---|
| `.venv\Scripts\python.exe -m compileall -q src agent_image tests` | 0 | 无输出 |
| `scripts\project_ruff.cmd check src agent_image tests` | 0 | `All checks passed!` |
| `pytest tests/unit/test_office_v2_exploration_fairness.py` | 0 | **8 passed** |
| `pytest tests/unit` | 0 | **856 passed**（`T01` 后为 848，新增 8） |
| `pytest tests/integration` | 0 | **29 passed** |

新测试文件：`tests/unit/test_office_v2_exploration_fairness.py`。

关键断言：同一 `campaign_seed` 下，仅改变记录的风险进度时，随机臂 64 个种子的 allocation 摘要集合
完全相同（引导臂则仍排除最高进度类型）；去重构造函数不含 `strategy` 参数。

### 需要在实验报告中声明的行为变化

随机臂不再享有"按最低进度轮转"的隐性加成，其**风险类型铺开速度可能低于改动前**。
这是修正对照组的独立性，不是削弱对照组，但必须在预实验与正式报告中显式声明。

### 未验证项

无真实 Docker / 真实模型运行；去重门控在两臂上的**实际拒绝次数**需要在 `T04` 预实验中观察。

### 风险与回滚

- 风险：随机臂去掉风险类型轮转后，其"风险类型铺开速度"会**下降**。这是修正基线、而非削弱基线，但必须在预实验报告中显式声明该变更。
- 失败信号：`FR-EXP-01` 相关守卫被为了通过测试而放宽。
- 回滚：本子任务单独提交，`git revert`。

## 8. T03：报告层逐代指标与晋升计数

- 状态：`DONE`
- 前置依赖：`T00`
- 被依赖项：`T04`（成本量化）、`T07`

### 对应需求

设计说明 §4.1（选项二）、§6.1 `C3`、§9 决定 4。

### 包含范围

在报告层增加**逐代时间序列**与聚合指标：

- 累计行为路径种类数（按 Episode）；
- 累计风险证据数（按 Episode）；
- 独特 Finding 数、重复率、连续无增益长度；
- 失败率、超时率、恢复情况；
- 成本：Episode、Token、时间；
- **晋升次数与晋升理由分布**（`T04` 判定实验是否有意义的关键输入）。

### 不包含范围

- 不做联合单元指标（属 `T07`）；
- 不做统计检验与跨运行聚合（属 `T05`）；
- 不改变已有报告字段的语义。

### 预计修改区域

- `src/sandbox/fuzzer/v2_report.py`；
- 如需新增读取方法，在 `src/sandbox/fuzzer/v2_campaign_store.py` 上新增**公开方法**；
- 对应单元测试。

### 实施 checklist

- [x] 明确每个指标的定义（分子、分母、按什么分组、缺失值如何处理），并**在运行实验前冻结**；
- [x] 实现逐代时间序列；
- [x] 实现晋升计数与理由分布；
- [x] 优先使用 `V2CampaignStore` 公开方法；若必须新增，走公开方法而非 `store._db`；
- [x] 新增测试：用已有 fixture / 本地临时库断言指标数值正确。

### 验收 checklist

- [x] 指标定义写入文档，可被独立复核；
- [x] 指标可从保存证据重算；
- [x] `compileall`、Ruff、`tests/unit` 通过；
- [x] 报告模板包含限制声明：只按选项二报告时不得声称"联合覆盖优越性"。

### 冻结的指标定义

**逐代时间序列** `generation_series`，每个已记录的代次一行：

| 字段 | 定义 |
|---|---|
| `coverage_counts` | 4 个维度**分别**计数（canonical facts、primary behavior features、risk contexts、oracle outcome bits），读自该代**起始状态** |
| `corpus_entries`、`used_episodes`、`consumed` | 读自该代起始状态 |
| `no_coverage_gain` | 该代反馈记录的 `FeedbackBrief.consecutive_no_gain`。**语义修正：尽管字段名如此，它是"本代无增益"的单代布尔值，不是连续长度**；连续长度由聚合层按连续计数得出 |
| `new_primary_behavior_count`、`new_risk_fact_count` | 该代反馈记录的原值，分别属于行为维度与风险维度 |
| `promoted_entries` | 该代新入队的语料条目数 = 下一代起始状态中的非 bootstrap 条目 − 本代起始状态中的非 bootstrap 条目 |
| `promotion_reasons`、`promotion_seed_kinds` | 上述新入队条目的理由集合与种子种类集合 |

**聚合指标** `aggregates`：

| 字段 | 定义 |
|---|---|
| `promotions` | 最终状态中**非 bootstrap** 的语料条目总数（`promotion_reasons` 不含 `exploratory-bootstrap-parent`） |
| `promotions_by_seed_kind` / `promotion_reasons` | 上述条目的 RIK/EXPLORATION 分布与理由分布 |
| `longest_no_gain_run` | `no_coverage_gain=True` 的最长连续长度；遇到 `False` **或缺失**都重置（缺失不桥接） |
| `generated_candidates` / `unique_candidates` / `repeat_rate` | 来自 `load_candidate_history`：生成总数、内容摘要去重后的数量、`1 − 去重/总数`。**未使用** `FeedbackBrief.repeated_behavior_path_keys`，因为该字段在代码中从未被赋值（恒为空），用它算重复率会得到虚假的 0 |
| `attempt_receipts` / `failed_attempts` / `retryable_attempts` / `timeouts` / `failure_rate` / `timeout_rate` | 来自 `attempt_receipt` 表按 Campaign 关联；`timeouts` 按 `error_code == "episode-timeout"` |
| `unique_findings` / `findings_awaiting_replay` | 来自 `finding` 表，按 `replay_status` 分类 |
| `cost` / `budget` | `CampaignBudgetSnapshot` 与 `ExecutionCosts` 原值 |

### 实现说明（2026-09-17）

- `V2CampaignStore` 新增 4 个**公开只读**方法：`list_generation_decisions`、`list_generation_feedback`、
  `list_findings`、`list_attempt_receipts`。报告不再访问 `store._db`。
- 报告的既有字段与取值保持不变（`decisions`/`feedback`/`findings` 仍为逐个模型的 JSON 对象），
  新增 `generation_series` 与 `aggregates` 两个键。
- **`report_digest` 的取值会变化**（它是整个 payload 的内容摘要，payload 增加了两个键）；
  摘要机制本身未变。这影响既有报告文件的字节比对，不影响任何语义。
- 指标分为行为维度、风险维度、入队维度、成本维度、失败维度分别报告，**不做合成总分**。

### 验证结果（2026-09-17）

| 命令 | 退出码 | 结果 |
|---|---|---|
| `.venv\Scripts\python.exe -m compileall -q src agent_image tests` | 0 | 无输出 |
| `scripts\project_ruff.cmd check src agent_image tests` | 0 | `All checks passed!` |
| `pytest tests/unit/test_office_v2_campaign_report.py` | 0 | **8 passed** |
| `pytest tests/unit` | 0 | **864 passed**（`T02` 后为 856，新增 8） |
| `pytest tests/integration` | 0 | **29 passed** |

新测试文件：`tests/unit/test_office_v2_campaign_report.py`。

### 未验证项

`generation_series` 与 `aggregates` 的**真实数值**尚未在任何真实 Campaign 上验证
（本地测试只覆盖空 Campaign 与指标定义函数）。首次真实产出见 `T04`。

### 报告模板必须包含的限制

只按设计说明 §4.1 的**选项二**报告时，**不得**声称"联合覆盖优越性"；
只能声称行为维度与风险维度各自的差异。这句话必须写入 `T04`/`T06` 的报告模板。

### 风险与回滚

- 风险：指标定义在实现过程中被"看数据"影响。控制：定义先冻结，先写文档再写实现。
- 回滚：本子任务单独提交。

## 9. T04：预实验（一对 × 每臂 30 Episode）

- 状态：`BLOCKED`（等待用户提供环境）
- 前置依赖：`T01`、`T02`、`T03`，以及可用的 Docker / 模型 / 镜像 / 授权

### 目的（明确不是出结论）

1. 统计 **晋升次数**，判断引导臂与随机臂在机制上是否真的不同；
2. 量化成本：每 Episode 的耗时、Token、失败率；
3. 观察"累计行为路径种类数""累计风险证据数"在两臂间是否有区分度。

### 包含范围

- 一次单 Episode smoke；
- 一对 Campaign：同一 `--campaign-seed` 各跑 30 Episode（引导臂与随机臂）；
- 结果保存到独立的 SQLite、Replay、Artifact 目录。

### 不包含范围

- 不宣称任何优越性结论；
- 不做统计检验；
- 不复用或覆盖任何历史运行结果。

### 实施 checklist

- [ ] 记录环境基线：镜像引用、模型名、GPU、Docker 版本；
- [ ] 运行 smoke 并确认闭环可运行；
- [ ] 运行引导臂 30 Episode；
- [ ] 运行随机臂 30 Episode（同一 `campaign-seed`）；
- [ ] 记录晋升次数、晋升理由分布、成本、失败与超时；
- [ ] 保存命令、退出码、跳过项与阻塞项。

### 验收 checklist

- [ ] 两臂均达到 30 个有效 Episode，或明确记录未达标的原因与是否暂停；
- [ ] 结果可被 `inspect` / `report` 重建；
- [ ] 预实验记录明确标注"不构成结论"。

### 停止信号

- 环境不可用、镜像缺失、模型不可达；
- 晋升次数为 0 或 1：停止并回到设计讨论，不得据此宣称任何优劣；
- 任一臂无法达到目标 Episode 数且原因属于机制缺陷。

### 风险与回滚

- 风险：真实运行可能暴露审计未发现的缺陷（审计中 `AC-01` 为 `UNVERIFIED`）。
- 回滚：预实验使用独立目录与数据库，不覆盖任何既有数据。

## 10. T05：冻结实验协议与成对数量

- 状态：`BLOCKED`
- 前置依赖：`T04`

### 包含范围

依据预实验数据，在**看正式结果之前**冻结：

- 成对数量；
- 每臂 Episode 数（当前为 30，可据成本调整）；
- 预算上限；
- 主指标与分维度指标的定义与计算脚本；
- 优越性判据与统计方法。

冻结结论写入本 TASK 与需求 SPEC。

### 验收 checklist

- [ ] 判据、指标定义与计算脚本在正式运行前完成冻结并留痕；
- [ ] 冻结版本号/日期可追溯。

## 11. T06：正式配对实验

- 状态：`BLOCKED`
- 前置依赖：`T05` 与环境

### 包含范围

- 按冻结协议运行配对 Campaign；
- 完整保存成功、失败、超时与无增益 Episode；
- 行为覆盖与风险覆盖**分别**报告，不做合成总分；
- 报告包含 §9.2 的使用限制。

### 验收 checklist

- [ ] 报告可拆解回原始维度；
- [ ] 结论只在其证据支持的范围内表述；
- [ ] 若证据不支持主张 A，如实报告，不得更换指标或隐藏样本。

## 12. T07：联合单元粗粒度定义与选项一主指标（本轮不排期）

- 状态：`DRAFT`
- 前置依赖：`T03`

### 包含范围

- 冻结粗粒度联合单元定义：`风险类型 × 行为类别 × 权限上下文类别 × 阶段`；
- 建立稳定标识并随 Episode 保存；
- 接入报告的联合覆盖曲线，作为选项一主指标。

### 不包含范围

- 不改为细粒度（`target_behavior_signature` 直用）；
- 不参与方向选择或入队判定（联合覆盖只用于归因与报告）。

### 待确认

- 粒度定义、标识规则与是否参与去重，需在启动本子任务前单独确认。

## 13. 全局约束

- **网络与安全**：不得连接未授权目标；默认不改动任何远程服务器；预实验环境由用户提供并授权。
- **不修改用户既有产物**：`.pytest-tmp/`、`harness-node-modules.tar.gz` 不读取、不修改、不提交。
- **不夹带**：裁判置信度加固、目标切换变异、重放四层级、安全默认值调整、报告解耦、命名清理均不在本 TASK 范围内。
- **验证纪律**：不得以"阅读代码"代替运行验证；不得把静态证据写成运行证据。

## 14. Git 提交计划

每个子任务完成验证后单独提交，只暂存该子任务涉及的文件：

1. `docs/SPEC.md` 产品规格修订；
2. 需求 SPEC（`docs/specs/20260917-dual-coverage-evaluation-design.md`）；
3. 本 TASK 拆解；
4. 之后 `T00`–`T07` 各自单独提交。

每次提交前执行 `git diff --check` 与 `git status --short`，不得提交未跟踪的用户文件与运行产物。

## 15. 风险与停止信号

- 预实验暴露闭环不可运行或机制缺陷 → 停止，回到 SPEC 讨论；
- 晋升机制在真实运行中几乎不触发 → 停止，重新审视 `T01` 的判据；
- 出现需要改变产品语义、数据格式或安全边界的新需求 → 停止，先写 SPEC。

## 16. 尚未解决的问题

- 成对数量与最终预算（`T05` 冻结）；
- 联合单元粒度细节与是否参与去重（`T07` 确认）；
- 风险覆盖指标的区分度（`T04` 观察）；
- `T00` 是否按微小维护处理（待用户确认）。

## 17. 执行记录

### 已完成（2026-09-17）

用户于 `2026-09-17` 确认本 TASK 拆解，并同意 `T00` 按 `AGENTS.md` §2 的"不改变行为的微小维护"处理。

| 子任务 | 提交 | 结果 |
|---|---|---|
| `T00` | `843ee8b` | Ruff 基线恢复通过；`tests/unit` 832 passed |
| `T01` | `f0e2a36` | 入队判定抽为纯函数并放开行为条件；新增 16 项测试；`tests/unit` 848 passed |
| `T02` | `3638b9a` | 随机臂不再读风险进度；两臂共用候选去重；新增 8 项测试；`tests/unit` 856 passed |
| `T03` | 本提交 | 报告新增逐代序列与聚合指标；store 新增 4 个公开读取方法；新增 8 项测试；`tests/unit` 864 passed |

每个子任务均已通过：`compileall`、`scripts\project_ruff.cmd check src agent_image tests`、
`tests/unit`、`tests/integration`（29 passed）。

### 未开始

- `T04`–`T06`：`BLOCKED`，等待用户提供可用的 Docker / 模型 / 镜像环境与真实运行授权。
  用户于 `2026-09-17` 说明当前环境不可运行，需要时另行租用。
- `T07`：本轮重新激活，等用户确认联合单元四元组后启动。

### 语义评分层的拆分（2026-09-17 用户决定）

用户要求新增"评分机制"来判断表现优劣，并明确：

- **两层都要**：先建确定性评分层（`T07` 为主），再加 Finding 质量分级；
- **接受**"确定性层决定胜负 + 语义层只作附加且显式标注"的结构。

因此：

- `T07`（联合单元 + 选项一主指标）仍属本 TASK，属于确定性评分层的一部分；
- Finding 质量分级拆为独立规格与任务：`SPEC-FINDING-SEVERITY-20260917`、
  `TASK-FINDING-SEVERITY-20260917`，**全程本地可完成，不依赖 Docker 或模型**
  （标注由 AI 离线完成，临时参考集标注为 provisional）；
- 该结构与 `docs/SPEC.md` 一致，**不需要修改产品 SPEC**：`FR-JDG-02` 已把 Judge 限定在
  语义判断，§12 已禁止以单次分数替代轨迹与工具证据；
- 裁判置信度加固的六项机制仍全部未实现，任何文档不得声称已校准。

### 基线与副作用

- 审计基线 `a15b127` 之后，本次是首次代码改动；`T00` 之前 HEAD 为 `33e9611`。
- 未读取、未修改、未提交 `.pytest-tmp/` 与 `harness-node-modules.tar.gz`。
- 未运行 Docker、未调用模型、未连接任何远程服务。

### 需要在 `T04` 之前解决的两件事

1. `T01` 实现说明中记录的**三条充分条件 vs 两条**的解释差异，需要用户确认（否则严格两条件版本已在同一处可改）。
2. 指标定义（本节 §8）已冻结，但**真实数值未验证**；`T04` 是首次真实产出。
