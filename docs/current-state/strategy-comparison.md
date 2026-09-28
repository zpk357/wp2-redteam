# 策略比较：`random_independent` 与 `coverage_guided`

> **2026-09-19 状态更新**：真实配对运行已执行（smoke 两臂各 1 Episode + 正式 30×2 两轮），结果与限制见 `docs/tasks/20260919-lightweight-comparison-run-record.md` §8。本文件下列“未运行模型／未运行 Docker／未运行真实 Campaign”等表述描述其编写基线 `a15b127` 时的状态，保留为历史文本，不代表当前状态。

- 对应 Spec：`SPEC-AUDIT-20260917`，目标 `AUD-G-03`、`AUD-REQ-04`，验收 `AUD-AC-04`
- 交付物序号：3 / 5（策略比较）
- 基线：`main @ a15b1273af1b6a1d3ec98eea50ba18aa3df823e7`
- 产品要求来源：`docs/SPEC.md` §6.1、§7.1-§7.3、`AC-04`、`AC-05`
- 事实性文档；不含修复方案

---

## 0. 结论摘要

产品 SPEC 的前提是：两种模式**只在"是否消费跨 Episode 覆盖反馈"这一点上不同**（`FR-EXP-03`："唯一原则性差异应是：是否消费跨 Episode 覆盖反馈来影响后续探索"）。

本次审计在代码中找到的实际情形是：

1. **两种模式都有 16 处分叉**，其中至少 4 处（`F1`、`F2`、`F4`、`F5`-`F8`）改变候选空间或候选质量，不只是"是否读反馈"（`E3`）。
2. **随机基线本身也在消费跨 Episode 覆盖反馈**：`settle_risk_progress` 与 `choose_next_allocation` 中的 `state.risk_progress` 均在两种模式下无条件生效，而它会让随机模式优先选择"进度最低的风险类型"。这使得 `random_independent` **不是** SPEC 所定义的独立采样基线（`E3`，见 §4）。
3. 代码中**唯一的**选择原语是 `independent_uniform_selection`（均匀随机），代码中**不存在**任何"按覆盖权重加权"的选择策略实现（`E3`，见 §3）。

第 2 条是本审计中对实验有效性影响最大的发现。它意味着当前两种模式的比较**不能**回答 SPEC §3 的核心研究假设，因为对照组的独立性前提不成立。

**必须说清楚的边界**：以上均为静态代码证据（`E3`）。本次未运行真实 Campaign（TASK 禁止），因此没有 `E1` 证据证明这些差异在实际运行中产生多大影响。第 2 条在代码路径上是确定的，但其**实际数值影响**（例如随机模式实际被"拉平"了多少次选择）需要真实运行才能量化。

---

## 1. 两种模式的共享链路

从 CLI 到报告的完整链路在 `episode-data-flow.md` 中已追踪。就策略而言，**以下部分完全共享，代码中没有任何策略分支**：

| 阶段 | 共享模块 | 是否有策略分支 |
|---|---|---|
| CLI 参数解析与依赖装配 | `v2_cli.py` | 仅 `--strategy` 传值 |
| Bootstrap（16 个根种子） | `v2_bootstrap.py`、`v2_seed_pools.py` | **无** |
| 种子支持目录 | `v2_campaign_loop.py` `build_seed_support_catalog` | **无** |
| 算子选择 | `select_formal_operator`（`v2_real_runtime.py:223`） | **无** |
| Mutation Plan 构造 | `build_semantic_mutation_plan`（`v2_real_runtime.py:234`） | 接收 `campaign_strategy` 入参，见 §3.2 |
| 预算预留与结算 | `reserve_mutation_budget`、`settle_campaign_budget` | **无** |
| 变异 Provider（模型、镜像、网络模式） | `DockerOllamaV2MutationProvider`（`v2_cli.py:239-251`） | **无** |
| Agent 运行时与模型 | `AgentRuntimeKind`、`model_name`、`model_inference` | **无** |
| Docker 环境、资源限制、超时 | `SandboxConfig`、`SandboxLimits`（`v2_cli.py:199-227`） | **无** |
| Oracle 与覆盖提取 | `office_v2/oracle*.py`、`coverage/v2_*.py` | **无** |
| 覆盖增益布尔 | `_coverage_gain`（`v2_real_runtime.py:1158`） | **无** |
| 目标保持评估 | `_assess_target`（`v2_real_runtime.py:836`） | **无** |
| 生命周期与终止判定 | `evaluate_campaign_lifecycle`（`v2_real_runtime.py:732`） | **无** |
| 尝试收据与恢复 | `seal_attempt`、`recover`、`resume_incomplete` | 部分（见 `F14`） |
| 报告生成 | `v2_report.py` | **无** |

**这一点对公平性是有利的**：Agent、模型参数、Docker 环境、工具实现、Oracle、覆盖提取器、超时与恢复规则**完全没有策略分支**，满足 `FR-EXP-03` 的大部分要求。

---

## 2. 策略分叉点全清单

以下为 `src/` 中所有依据 `CampaignStrategy` 分支的位置（`grep` 穷举，`E3`）。"影响类别"按后果分类：

- **选择/空间**：改变哪些候选可能被选中
- **质量门控**：改变候选是否被接受
- **记录**：只影响留痕，不影响行为
- **强制**：存储层的一致性约束

| # | 位置 | `random_independent` | `coverage_guided` | 影响类别 |
|---|---|---|---|---|
| `F1` | [v2_campaign_loop.py:377](src/sandbox/fuzzer/v2_campaign_loop.py#L377) | `include_derived=False` | `include_derived=True` | **选择/空间** |
| `F2` | [v2_campaign_loop.py:177-183](src/sandbox/fuzzer/v2_campaign_loop.py#L177-L183) | 强制 `NO_PROMOTION`，理由 `"independent-random-baseline-no-promotion"` | 走完整晋升判定 | **选择/空间** |
| `F3` | [v2_campaign_loop.py:335](src/sandbox/fuzzer/v2_campaign_loop.py#L335) | 不调用 `record_parent_result` | 调用 | 记录 |
| `F4` | [v2_real_runtime.py:757](src/sandbox/fuzzer/v2_real_runtime.py#L757) | 不调用 `add_promoted_seed_to_catalog` | 调用（目录增长） | **选择/空间** |
| `F5` | [v2_real_runtime.py:346-358](src/sandbox/fuzzer/v2_real_runtime.py#L346-L358) | `known_content_digests = frozenset()` | 由语料库 + `candidate_history` 填充 | **质量门控** |
| `F6` | [v2_real_runtime.py:359-371](src/sandbox/fuzzer/v2_real_runtime.py#L359-L371) | `recent_lineage_content_digests = frozenset()` | 由谱系填充 | **质量门控** |
| `F7` | [v2_real_runtime.py:372-384](src/sandbox/fuzzer/v2_real_runtime.py#L372-L384) | `recent_structure_signature_digests = frozenset()` | 由谱系 + 最近 8 条历史填充 | **质量门控** |
| `F8` | [v2_real_runtime.py:320-324](src/sandbox/fuzzer/v2_real_runtime.py#L320-L324) | `candidate_history = ()` | `store.load_candidate_history(campaign_id)` | **质量门控** |
| `F9` | [v2_real_runtime.py:300-303](src/sandbox/fuzzer/v2_real_runtime.py#L300-L303) | `seed_id_resolver` 返回根种子 id | 返回派生种子 id | **选择/空间** |
| `F10` | [v2_real_runtime.py:702-720](src/sandbox/fuzzer/v2_real_runtime.py#L702-L720) | 两个 factory 传 `None` | 传 lambda | 记录 |
| `F11` | [v2_real_runtime.py:690](src/sandbox/fuzzer/v2_real_runtime.py#L690) | `seed=seed` | `seed=None` | 记录 |
| `F12` | [v2_real_runtime.py:771-775](src/sandbox/fuzzer/v2_real_runtime.py#L771-L775) | `previous_feedback_digest=None` | 传入上轮反馈摘要 | 记录 |
| `F13` | [v2_orchestrator.py:143-151](src/sandbox/fuzzer/v2_orchestrator.py#L143-L151) | `input_feedback_digest=None` | 记录反馈摘要 | 记录 |
| `F14` | [v2_campaign_store.py:124-128](src/sandbox/fuzzer/v2_campaign_store.py#L124-L128)、[:1097-1101](src/sandbox/fuzzer/v2_campaign_store.py#L1097-L1101)、[:1613-1616](src/sandbox/fuzzer/v2_campaign_store.py#L1613-L1616) | 若结算中出现晋升或目录变化则 `raise` | 允许 | **强制** |
| `F15` | [v2_campaign_loop.py:390-394](src/sandbox/fuzzer/v2_campaign_loop.py#L390-L394) | 理由 `"independent-random-baseline"` | `"formal-risk-pool-selection"` 等 | 记录 |
| `F16` | [v2_campaign_loop.py:157-160](src/sandbox/fuzzer/v2_campaign_loop.py#L157-L160) | 断言 `Episode` 种子必须是冻结根种子 | 无此断言 | **强制** |

### 2.1 与"唯一原则性差异"的偏差

`FR-EXP-03` 要求两种模式除"是否消费跨 Episode 覆盖反馈"外**冻结并保持一致**。上表中：

- `F1`（派生种子是否可选）**改变父样本空间**：引导模式在晋升发生后拥有严格更大的父样本池；
- `F2` + `F4` **改变目录增长**：引导模式的种子目录会变大，随机模式恒定；
- `F5`-`F8` **改变候选重复门控**：`v2_validation.py:71,74,154` 用这三个集合**拒绝**重复内容与重复结构。随机模式传空集合 ⇒ **不拒绝重复**；引导模式 ⇒ **拒绝重复并重试**。

`F5`-`F8` 值得单独说明其后果。`v2_validation.py` 的拒绝逻辑（`E3`）：

```python
or candidate.normalized_content_digest in known_content_digests          # :71
candidate.normalized_content_digest in recent_lineage_content_digests    # :74
        in recent_structure_signature_digests                            # :154
```

因此**引导模式生成的候选在构造上就更"新"**，随机模式的候选可能重复。`FR-FUZZ-05` 要求引导语料库做"重复降权"，所以引导侧有这个门控是符合产品意图的；但它也意味着两种模式比较时**候选质量门控不一致**，这不符合 `FR-EXP-03` 的字面要求。这可能是有意的设计选择（"重复降权"本就属于被检验的引导能力），需要一个明确的产品决定。

### 2.2 一个声明了但未接线的差异

[v2_feedback.py:98-116](src/sandbox/fuzzer/v2_feedback.py#L98-L116) 定义了 `IndependentBaselineBrief`，其固定文本是 `"This generation is an independent random baseline with no prior run feedback."`，并有构造函数 `build_independent_baseline_brief`（[:174](src/sandbox/fuzzer/v2_feedback.py#L174)）。

全仓 `grep` 结果：`build_independent_baseline_brief` **除 `__all__` 导出外没有任何调用点**（`src/` 与 `tests/` 均无）。即：这个为随机基线准备的独立 brief **尚未接线**（`E3`）。

好处是：变异器在两种模式下收到的是同一个 `build_minimal_fact_brief`（`v2_real_runtime.py:305-314`），因此**变异提示文本在两种模式间一致**，满足 `FR-EXP-03` 对"变异器模型、能力边界"的一致要求。代价是这个已声明的基线 brief 成为未使用代码。

---

## 3. 选择算法：唯一的原语是均匀随机

### 3.1 `independent_uniform_selection` 是唯一的选择函数

[v2_selection.py:98-156](src/sandbox/fuzzer/v2_selection.py#L98-L156) 定义了选择原语。其随机性来源完整地是：

```python
generation_seed = sha256_digest({
    "algorithm": "sha256-modulo-v1",
    "selection_policy": SelectionPolicy.RANDOM_UNIFORM,
    "selection_kind": selection_kind,
    "campaign_seed": seed,
    "generation_index": generation_index,
    "options": tuple(item.option_id for item in canonical),
})
draw = int(generation_seed[7:23], 16) % len(canonical)
```

**输入字典中不含任何覆盖摘要、缺口或历史 Episode 数据。** 选择结果只取决于：算法标识、选择类型、Campaign 种子、代次索引、候选 id 列表。

`SelectionPolicy` 枚举**只有一个取值**（[v2_selection.py:20-21](src/sandbox/fuzzer/v2_selection.py#L20-L21)）：

```python
class SelectionPolicy(StrEnum):
    RANDOM_UNIFORM = "random_uniform_v1"
```

并且验证器强制该策略下所有选项权重必须为 1（[:72-75](src/sandbox/fuzzer/v2_selection.py#L72-L75)）：

```python
if self.selection_policy is SelectionPolicy.RANDOM_UNIFORM and any(
    item.weight != 1 for item in self.options
):
    raise ValueError("random uniform selection requires unit weights")
```

**结论**：代码中存在 `WeightedSelectionOption.weight`、`total_weight`、加权游标等**加权选择的数据结构**，但**不存在任何非均匀的策略实现**。所谓"按覆盖证据加权选择父样本"在当前代码中不存在（`E3`）。

### 3.2 该原语的三个调用点

`grep` 显示 `independent_uniform_selection` 只在三处被调用：

| 调用点 | `SelectionKind` | 用途 |
|---|---|---|
| [v2_risk_pool_scheduler.py:124](src/sandbox/fuzzer/v2_risk_pool_scheduler.py#L124) | `RISK_DIRECTION` | 在并列最低进度的风险类型中选一个 |
| [v2_risk_pool_scheduler.py:134](src/sandbox/fuzzer/v2_risk_pool_scheduler.py#L134) | `PARENT` | 在所选风险池内选父种子 |
| [mutation/v2_policy.py:472,485,497](src/sandbox/mutation/v2_policy.py#L472) | `OPERATOR` | 选算子数量、算子族、算子变体 |

**三处全部均匀。** 两种策略调用的是同一个函数、同一套输入结构。

---

## 4. 核心发现：随机基线也在消费跨 Episode 覆盖反馈

### 4.1 代码路径

**第一步**：结算时无条件推进风险进度。[v2_real_runtime.py:751-756](src/sandbox/fuzzer/v2_real_runtime.py#L751-L756)：

```python
next_state = settle_risk_progress(
    next_state,
    risk_type=decision.allocation.risk_type,
    attempted=behavior_assessment.attempted,
    realized=behavior_assessment.realized,
)
```

**此处没有任何策略判断。** `behavior_assessment` 来自 `assess_agent_behavior(seed, coverage_input=episode.coverage_input, oracle_result=episode.oracle.oracle_result)`（[:678-682](src/sandbox/fuzzer/v2_real_runtime.py#L678-L682)），即**由本 Episode 的 Oracle 证据与覆盖输入推导**。

**第二步**：[v2_campaign_state.py:295-335](src/sandbox/fuzzer/v2_campaign_state.py#L295-L335) `settle_risk_progress` 把该风险类型的等级沿 `0 → 1 → 2 → 3` **单调推进**（`if target < current: return state`，只升不降）。

**第三步**：下一代分配时无条件把它作为选择输入。[v2_campaign_loop.py:371-382](src/sandbox/fuzzer/v2_campaign_loop.py#L371-L382)：

```python
formal = build_formal_risk_allocation(
    catalog=state.seed_catalog,
    progress_states=state.risk_progress,      # <-- 无条件传入
    ...
    include_derived=not independent,          # <-- 唯一与本策略相关的差异
    ...
)
```

**此处唯一的策略差异是 `include_derived`。`progress_states=state.risk_progress` 在两种模式下都传入。**

**第四步**：[v2_risk_pool_scheduler.py:108-133](src/sandbox/fuzzer/v2_risk_pool_scheduler.py#L108-L133) 用该进度**过滤候选池**：

```python
risk_level = progress.get(pool.risk_type, RiskProgressLevel.NOT_SELECTED)
pool_candidates.append((risk_level, pool.risk_type, seeds))
...
lowest = min(item[0] for item in pool_candidates)
directions = tuple(item for item in pool_candidates if item[0] is lowest)
```

只有**进度最低**的风险类型池才进入均匀抽取。

### 4.2 这意味着什么

`state.risk_progress` 完全满足 SPEC 对"跨 Episode 覆盖反馈"的描述：

- 它是**跨 Episode 累积**的（每代结算后更新，跨 Campaign 生命周期保留）；
- 它由**行为与 Oracle 证据**推导（`attempted` / `realized`）；
- 它是**行为覆盖/风险覆盖**的产物，不是种子目录或预算的直接函数；
- 它**直接决定下一轮的风险方向候选集**。

因此在 `random_independent` 模式下：

> 第 N 个 Episode 的执行结果 → 推进该风险类型的进度等级 → 第 N+1 个 Episode 的选择**排除**已推进较多的风险类型，只在进度最低的类型中抽取。

**这与 `FR-EXP-01` 直接冲突**：

> `FR-EXP-01`：它从同一合法候选空间中独立采样，拥有与引导模式相同的语义变异能力，**但不得读取之前 Episode 的覆盖快照、缺口、晋升结果或其他跨 Episode 反馈来影响下一轮选择**。

也与 `AC-05` 冲突："随机模式不消费跨 Episode 覆盖反馈"。

### 4.3 实际影响的方向与幅度

**方向**：`min(progress)` 过滤的效果是**轮转**——它倾向于均匀铺开 4 个风险类型，而不是独立重复抽样。

由于 `settle_risk_progress` 至少把被选中的风险类型推到 `SELECTED`(1)，且只有 `attempted` / `realized` 才能到 2 / 3（多数 Episode 不会），典型轨迹是：

1. 初态 4 个池全部 `NOT_SELECTED`(0) → 并列最低 → 均匀抽 1 个，比如 A；
2. A 结算后升至 ≥1 → 剩 {B,C,D} 并列最低 → 均匀抽 1 个；
3. 前 4 个 Episode 几乎必然覆盖全部 4 个风险类型，且**不可重复**；
4. 之后 4 个池普遍停在 1，`min` 通常并列 → 回到均匀抽取。

**幅度**：这是一个**有界但真实**的偏差。它与"完全独立采样"的关键差别在于：独立采样允许第 2 个 Episode 再抽到 A（概率 1/4），而当前实现**禁止**——在第 4 个 Episode 之前，已用过的类型被排除。

**对实验结论的影响**：偏差方向是**使随机基线在"风险类型铺开度"上更强**（更早覆盖全部 4 类风险），而在"在单一风险类型上深挖"上更弱。因此：

- 若用"风险类型覆盖速度"作为指标，随机基线会被**高估**；
- 当前实现下的比较**不是**"有反馈 vs 无反馈"，而是"**较多反馈 vs 较少反馈**"。

产品 SPEC §3 要求在"合法候选空间一致"的前提下检验引导策略的优越性。当前实现不满足该前提。

### 4.4 为什么这个问题此前可能未被发现

有三层原因，都值得记录：

1. **`F2`/`F4` 的存在使"随机模式不做任何跨代积累"看起来成立**——随机模式确实不晋升、不增长目录、不记录反馈摘要。这些是**显式**的策略分支，容易检查。
2. **`settle_risk_progress` 与 `progress_states=state.risk_progress` 没有策略分支**，因此不会被"搜索 `RANDOM_INDEPENDENT` 关键字"的方法发现。它们是**隐式**的共享路径。
3. **命名误导**：模块 `v2_selection.py` 的函数叫 `independent_uniform_selection`，文档字符串写着 *"Select uniformly without including prior Campaign state in the RNG input."* 这句话对**该函数自身**是准确的（RNG 输入确实不含历史），但对**整个选择流程**不成立——历史通过**候选集的构成**（而非 RNG 输入）进入了选择。`v2_risk_pool_scheduler.py:106` 的文档字符串 *"Choose the lowest-progress risk type, then a uniform parent in its pool."* 才是完整描述。

这正是 `AGENTS.md` §4 与审计 Spec §7 警告的情形：**类名、函数名和注释只能作为定位线索，不能单独证明产品语义已经实现**。

---

## 5. 公平性核查表

按 `FR-EXP-03` 逐项对照（`E3`）：

| `FR-EXP-03` 要求项 | 代码位置 | 是否一致 | 说明 |
|---|---|---|---|
| Agent Runtime | `v2_cli.py:210` `TRACE_G_AGENT_RUNTIME` | ✅ 一致 | 同一 `--agent-runtime` 参数 |
| 被测模型和模型参数 | `v2_cli.py:191` `_exploratory_inference` | ✅ 一致 | 同一 `seal_model_inference_options` |
| Scenario、初始状态 | `build_exploratory_bootstrap` | ✅ 一致 | Bootstrap 无策略分支 |
| **合法候选空间** | `F1` `include_derived` | ❌ **不一致** | 引导模式晋升后父样本池更大 |
| 变异器模型、能力边界 | `DockerOllamaV2MutationProvider`、`mutation/v2_policy.py` | ✅ 一致 | 算子选择三处均均匀，无策略分支 |
| 单次调用预算 | `plan.budget` | ✅ 一致 | 由 `build_semantic_mutation_plan` 决定，无策略分支 |
| Episode 数 | `--episodes` / `generation_count` | ✅ 一致 | 同一参数 |
| Token/调用/时间/成本预算 | `CampaignBudgetSnapshot` | ✅ 一致 | 同上 |
| Docker 环境、工具实现、策略、外部依赖 | `SandboxConfig`、`SandboxLimits` | ✅ 一致 | 同一 `WeekOneConfig` |
| Oracle、覆盖提取器、Finding 判定口径 | `office_v2/oracle*.py`、`coverage/v2_*.py` | ✅ 一致 | 无策略分支 |
| 超时、失败、重试、恢复、结果保存规则 | `v2_runtime.py`、`v2_campaign_store.py` | ⚠️ 部分 | `F14` 在存储层对随机模式加了更严的断言（不改变执行语义，只更严格） |
| **唯一原则性差异是"是否消费跨 Episode 覆盖反馈"** | §4 | ❌ **不成立** | ① 存在 `F1`/`F5`-`F8` 等空间与门控差异；② 两种模式**都**消费 `risk_progress` |

**汇总**：12 项中 9 项明确一致，1 项部分一致，2 项不一致。

---

## 6. 两种模式的完整分叉图

```text
CLI（同一参数集，仅 --strategy 不同）
  │
  ▼
Bootstrap：16 个冻结根种子 ──────────────────── 共享
  │
  ▼
选择（choose_next_allocation → build_formal_risk_allocation）
  │   ├─ progress_states = state.risk_progress ── 共享（两种模式都读）★
  │   └─ include_derived ─────────────────────── 分叉 F1
  ▼
算子选择（均匀）─────────────────────────────── 共享
  ▼
Mutation Plan + 预算预留 ────────────────────── 共享
  ▼
候选生成（prepare_candidate）
  │   ├─ known_content_digests ──────────────── 分叉 F5
  │   ├─ recent_lineage_content_digests ─────── 分叉 F6
  │   ├─ recent_structure_signature_digests ─── 分叉 F7
  │   ├─ candidate_history ──────────────────── 分叉 F8
  │   └─ seed_id_resolver ───────────────────── 分叉 F9
  ▼
Docker Agent Episode ───────────────────────── 共享
  ▼
Oracle + 覆盖提取 ──────────────────────────── 共享
  ▼
结算（_settle_episode）
  │   ├─ settle_risk_progress ───────────────── 共享（两种模式都写）★
  │   ├─ promote_coverage_artifact ──────────── 分叉 F2
  │   ├─ add_promoted_seed_to_catalog ───────── 分叉 F4
  │   └─ previous_feedback_digest ───────────── 分叉 F12（记录）
  ▼
下一代反馈构造 ─────────────────────────────── 共享
  ▼
下一代选择（回到顶部）★ 闭环
```

★ 标记的两处是"跨 Episode 覆盖反馈进入选择"的实际通道，且**对两种模式都开放**。

**回答审计 Spec §5 的四个问题**：

| 问题 | 答案 |
|---|---|
| 共享的执行链是什么？ | Agent、模型、Docker、工具、Oracle、覆盖提取、预算、结算、报告全部共享（§1） |
| 策略分叉点在哪里？ | 16 处（§2），关键 4 处为 `F1`、`F2`、`F4`、`F5`-`F8` |
| 是否读取历史 Episode 数据？ | **两种模式都读**（`risk_progress`）；引导模式额外读 `candidate_history` 与种子谱系 |
| 覆盖反馈进入了哪些具体决策？ | 仅"风险类型方向"这一项（通过 `risk_progress` 的 `min` 过滤）。行为覆盖、联合覆盖、Finding 价值、饱和信号**均未进入任何决策** |
| 候选空间、Mutator 能力、Agent、预算、Oracle、保存口径是否一致？ | 候选空间不一致（`F1`）；候选质量门控不一致（`F5`-`F8`）；其余一致 |

---

## 7. 对产品假设的可检验性影响

产品 SPEC §3 的核心假设是"在……合法候选空间……一致时，覆盖率引导模式应比随机模式更快发现新的风险—行为组合"。

当前实现的三个问题共同影响该假设的可检验性：

| 问题 | 影响 |
|---|---|
| 随机基线消费 `risk_progress`（§4） | 对照组不独立 ⇒ 测得的差异**不是**"反馈 vs 无反馈"的效应 |
| 候选空间不对称（`F1`、`F2`、`F4`） | 引导侧父样本池更大 ⇒ 差异可能来自**搜索空间大小**而非反馈质量 |
| 候选质量门控不对称（`F5`-`F8`） | 引导侧天然更少重复 ⇒ 差异可能来自**去重**而非反馈质量 |

同时必须公平记录**支持当前实现的一面**：

- 引导模式的 `F2`/`F4`（晋升与目录增长）正是 SPEC §6.3 `FR-FUZZ-05` 要求的"种子晋升……必须能追溯到具体覆盖证据"。SPEC 本身要求引导模式拥有这些能力，所以这些分叉**部分**是产品规格所要求的，不完全是有意或无意的公平性疏漏；
- 换言之：SPEC 说"唯一原则性差异是是否消费反馈"，但 SPEC 同时又要求引导模式做晋升和语料库增长——**这两条产品要求之间存在张力**，需要在实验设计 SPEC 中显式解决。这是 `discussion-items.md` 第 3 项要交给用户的核心问题。

---

## 8. 证据等级说明

| 结论 | 等级 | 依据 |
|---|---|---|
| 16 处分叉清单 | `E3` | `grep` 穷举 `RANDOM_INDEPENDENT` / `COVERAGE_GUIDED`，逐处阅读 |
| 唯一选择原语为均匀（`SelectionPolicy` 单值） | `E3` | `v2_selection.py:20-21,72-75,98-156` |
| 随机模式读取 `risk_progress` 影响选择 | `E3` | 四步代码路径（§4.1）逐行核对；`grep` 确认 `progress_states` 无策略分支 |
| `IndependentBaselineBrief` 无调用点 | `E3` | 全仓 `grep`，仅 `__all__` 导出 |
| 两种模式在真实运行中的实际差异幅度 | `E0` | 未运行真实 Campaign（TASK 禁止 Docker/模型） |
| 公平性核查表 12 项中 9 项一致 | `E3` | 逐项追溯，非运行验证 |

**本文档所有结论均为静态代码证据。** 按审计 Spec §7，`ALIGNED` 结论不能只依赖 `E4`；这里给出的 `CONFLICTING` 结论有 `E3` 支持，但**其运行期影响未验证**。若要升级为 `E1`/`E2`，需要真实配对 Campaign 或一个专门断言"随机模式选择不受历史影响"的测试——当前测试套件中**没有**这样的断言（`tests/unit` 的 792 项全部通过，但无一保护该语义，见 §8.1）。

### 8.1 一个需要用户注意的测试证据缺口

**结论：测试套件对"随机模式是否消费跨 Episode 覆盖反馈"没有判定力。**

`grep` 结果（`E1`：在本次实际运行的测试套件上执行）：

| 检索 | 命中 |
|---|---|
| `tests/` 中引用 `RANDOM_INDEPENDENT` 的文件 | **仅 1 个**：`tests/unit/test_exploratory_campaign.py` |
| 该文件中与随机策略相关的引用 | 3 处（1 处为构造实参，2 处为断言），全部是 CLI 解析与策略**持久化**：`args.strategy == CampaignStrategy.RANDOM_INDEPENDENT.value`（[:45](tests/unit/test_exploratory_campaign.py#L45)）、`strategy=CampaignStrategy.RANDOM_INDEPENDENT`（[:54](tests/unit/test_exploratory_campaign.py#L54)，构造实参）、`store.campaign_strategy("demo") is CampaignStrategy.RANDOM_INDEPENDENT`（[:57](tests/unit/test_exploratory_campaign.py#L57)） |
| `tests/` 中断言随机基线**守卫**（`"independent-random-baseline-no-promotion"`、`"random independent Campaign cannot promote a seed"`、`"random independent Campaign cannot change Corpus"`、`"random independent non-Episode settlement changed Corpus"`）的测试 | **0** |

即：即使 `F2`/`F14` 这些**显式**分叉，也没有测试覆盖；§4 的**隐式**通道（共享代码中的 `risk_progress`）自然更不会被发现。

**注意一处容易误判的地方**：`tests/unit/__pycache__/` 中残留着 `test_office_v2_random_independent_strategy.cpython-312.pyc` 等编译产物，但**对应的 `.py` 源文件已不存在**。对 `tests/` 做 `grep` 时若未排除二进制文件，这些 `.pyc` 会产生"存在相关测试"的假象。本审计在得出上述结论前已用 `--include=*.py` 重新核对，并确认 `ls tests/unit/` 中无该文件。

**这不是"测试失败"，而是测试覆盖缺口。** 792 项单元测试全部通过（`E1`，退出码 0），但它们**不保护** `FR-EXP-01`/`AC-05` 这一条产品语义。按审计 Spec §7："测试'运行通过'不等于测试具有判定力；只有直接保护目标行为的断言才计为 `E2`。"因此本文档的 `CONFLICTING` 结论**不能**被现有 `E2` 证据反驳。

---

## 9. 需要用户决定的问题

§4（随机基线消费反馈）、§2.1（候选空间与门控不对称）、§3.1（不存在加权选择）以及"SPEC 内部张力"（§7）都需要产品决定，不适合在审计阶段自行选择方案。这些问题已按审计 Spec §10 `AUD-REQ-12` 的七段结构整理进：

**`docs/current-state/discussion-items.md` 第 3 项（随机基线公平性）**，以及第 1、2 项（覆盖语义与反馈闭环）。

---

## 10. 复核指引

若用户希望独立复核本文档的核心结论（§4），最短路径是：

1. 打开 [v2_real_runtime.py:751-756](src/sandbox/fuzzer/v2_real_runtime.py#L751-L756)，确认 `settle_risk_progress` 调用**不含** `self.strategy` 判断；
2. 打开 [v2_campaign_loop.py:371-382](src/sandbox/fuzzer/v2_campaign_loop.py#L371-L382)，确认 `progress_states=state.risk_progress` 是**无条件**实参，而 `include_derived=not independent` 是唯一的策略相关实参；
3. 打开 [v2_risk_pool_scheduler.py:118-130](src/sandbox/fuzzer/v2_risk_pool_scheduler.py#L118-L130)，确认 `min(progress)` 过滤发生在均匀抽取**之前**；
4. 确认 `state.risk_progress` 由 Episode 证据推进（[v2_campaign_state.py:295-335](src/sandbox/fuzzer/v2_campaign_state.py#L295-L335)）。

四步均不涉及运行环境，可在任意编辑器内完成。
