# SPEC 合规矩阵：逐条对照

> **2026-09-19 状态更新**：真实配对运行已执行（smoke 两臂各 1 Episode + 正式 30×2 两轮），结果与限制见 `docs/tasks/20260919-lightweight-comparison-run-record.md` §8。本文件下列“未运行模型／未运行 Docker／未运行真实 Campaign”等表述描述其编写基线 `a15b127` 时的状态，保留为历史文本，不代表当前状态。

- 对应 Spec：`SPEC-AUDIT-20260917`，目标 `AUD-G-06`，验收 `AUD-AC-06`、`AUD-AC-07`、`AUD-AC-08`
- 交付物序号：4 / 5（SPEC 合规矩阵）
- 基线：`main @ a15b1273af1b6a1d3ec98eea50ba18aa3df823e7`
- 对照对象：`docs/SPEC.md`（状态 `APPROVED_BASELINE`）
- 事实性文档；不含修复方案

---

## 1. 方法与图例

### 1.1 证据等级（审计 Spec §7）

| 等级 | 含义 |
|---|---|
| `E1` | 运行证据：实际执行并保存了命令、输入和结果 |
| `E2` | 测试证据：测试执行通过，**且断言能直接区分该行为是否成立** |
| `E3` | 静态代码证据：有可追踪的调用链和数据流，但本次未运行 |
| `E4` | 文档声明：只在 README/HANDOFF/LOG/注释/历史计划中声明 |
| `E0` | 无可用证据 |

### 1.2 合规状态（审计 Spec §8）

| 状态 | 含义 |
|---|---|
| `ALIGNED` | 符合要求，且至少有与结论相称的代码和验证证据 |
| `PARTIAL` | 已有相关结构或部分路径，但尚未完成整个要求 |
| `CONFLICTING` | 当前行为或数据语义与要求**直接冲突** |
| `MISSING` | 在审计范围内未找到对应实现 |
| `UNVERIFIED` | 看到了可能的实现，但证据不足以判断是否工作 |
| `NOT_APPLICABLE` | 经用户确认不适用（**本次审计未使用该状态**） |

### 1.3 本次实际执行的检查（`E1` 来源）

| 命令 | 退出码 | 结果 |
|---|---|---|
| `python -m compileall -q src agent_image tests` | 0 | 无语法错误 |
| `python -m ruff check src agent_image tests` | 非 0 | **2 个错误**，均在测试文件的导入上 |
| `python -m pytest tests/unit -q --basetemp=<tmp> -p no:cacheprovider` | **0** | 792 个结果符号，`F`/`E` 为 0 |
| `python -m pytest tests/integration -q --basetemp=<tmp> -p no:cacheprovider` | **0** | 29 个结果符号，全为 `.` |

`tests/integration/` 的 6 个文件中 **`grep docker` 无命中**（`E1`），即这 29 项通过**不使用真实 Docker**。因此任何依赖真实 Docker/模型的行为**不能**由它们证明。

### 1.4 全局证据边界

TASK 禁止运行 Docker、真实模型、远程服务器与真实 Campaign。因此：

- **不存在**任何 `E1` 级端到端运行证据；
- 所有涉及容器执行、模型决策、真实 Oracle 计算的结论最高为 `E3`；
- `E2` 仅适用于不依赖容器即可断言的逻辑（摘要校验、状态机、持久化、选择算法等）。

---

## 2. 汇总表（43 条，无遗漏）

`AC-06` 要求 SPEC 中每个 `G-*`、`FR-*`、`SEC-*`、`AC-*` 都出现在矩阵中。以下为全部 43 条：

| ID | 状态 | 等级 | 一句话结论 |
|---|---|---|---|
| `G-01` | `ALIGNED` | `E2`/`E3` | 轨迹作为基本证据的结构与提取均已实现 |
| `G-02` | `PARTIAL` | `E3` | 行为与风险分别度量成立；**联合覆盖未建立** |
| `G-03` | `PARTIAL` | `E3` | 只有风险进度标量进入调度；行为/联合覆盖不影响任何决策 |
| `G-04` | `CONFLICTING` | `E3` | 随机基线消费跨 Episode 反馈，且与引导模式有 4 类非原则性差异 |
| `G-05` | `PARTIAL` | `E3` | 证据保存充分；四个重放层级的显式区分与确定性边界声明缺失 |
| `G-06` | `MISSING` | `E3`/`E4` | 黄金集、主动学习、漂移监控、人工复核**均无实现** |
| `FR-FUZZ-01` | `PARTIAL` | `E3` | 闭环存在，但反馈只影响"风险方向"，不影响变异内容与父样本权重 |
| `FR-FUZZ-02` | `ALIGNED` | `E3` | 语义变异的受控维度与算子体系完整 |
| `FR-FUZZ-03` | `PARTIAL` | `E3` | Mutation Plan 7 项中 6 项具备；"目标切换校验结果"缺失 |
| `FR-FUZZ-04` | `MISSING` | `E3` | **目标切换变异不存在**；目标漂移检测存在且有效 |
| `FR-FUZZ-05` | `PARTIAL` | `E3` | 晋升与重复降权可追溯；**父样本权重不存在**（只有单位权重） |
| `FR-EXP-01` | **`CONFLICTING`** | `E3` | **随机基线读取 `risk_progress` 影响下一轮选择** |
| `FR-EXP-02` | `PARTIAL` | `E3` | 读风险进度；行为/联合缺口不参与；"为什么选择"有记录 |
| `FR-EXP-03` | `CONFLICTING` | `E3` | 12 项中 2 项不一致，"唯一原则性差异"不成立 |
| `FR-EXP-04` | `PARTIAL` | `E3` | 4 项中 2 项缺失（冻结实验计划、配对 Campaign） |
| `FR-EXP-05` | `PARTIAL` | `E3` | 7 类指标中原始数据部分可事后计算，**计算后的指标与联合覆盖缺失** |
| `FR-REP-01` | `UNVERIFIED` | `E3` | Docker 隔离结构完整，但未在真实 Docker 下验证 |
| `FR-REP-02` | `ALIGNED` | `E2`/`E3` | 6 类证据的保存结构齐备且有测试 |
| `FR-REP-03` | `PARTIAL` | `E3` | 存在重放模式枚举，但与 SPEC 四层级非一一对应；无确定性边界声明 |
| `FR-REP-04` | `PARTIAL` | `E3` | Finding 关联证据成立；"可复现"能力未验证 |
| `FR-JDG-01` | `PARTIAL` | `E3` | Oracle 确定性成立；**终止原因在成功路径由调用方硬编码**；无宿主机独立重算 |
| `FR-JDG-02` | `PARTIAL` | `E3` | Judge 仅用于语义判断 ✅；但目标保持是晋升门控的**唯一**证据来源 |
| `FR-JDG-03` | `MISSING` | `E3` | 6 项加固机制全部不存在 |
| `FR-OPS-01` | `PARTIAL` | `E2`/`E3` | 单库事务化清晰；文件系统与 SQLite 之间无原子性 |
| `FR-OPS-02` | `PARTIAL` | `E2`/`E3` | 失败分类结构完整并测试；成功路径的终止原因非证据推导 |
| `FR-OPS-03` | `ALIGNED` | `E2` | 幂等恢复有多处相等性断言与测试 |
| `FR-OPS-04` | `PARTIAL` | `E3` | 6 个维度中记录 4 个；**模型调用次数与工具调用次数未作为预算维度** |
| `FR-OPS-05` | `ALIGNED` | `E2` | 全仓内容摘要校验，且不冒充语义验证 |
| `SEC-01` | `ALIGNED` | `E3`/`E4` | 合成场景 + 授权范围文档 + 容器网络默认 `none` |
| `SEC-02` | `PARTIAL` | `E3` | `SandboxConfig` 默认拒绝网络；但 **CLI 默认 `--ollama-mode host`**，即默认开启宿主网络 |
| `SEC-03` | `PARTIAL` | `E3` | 拒绝带凭据的 endpoint；**未找到通用的日志/Artifact 脱敏与最小化保留机制** |
| `SEC-04` | `PARTIAL` | `E3` | 存在中断清理路径；但 `cleanup_confirmed` 在成功路径硬编码，清理失败无法进入该记录 |
| `SEC-05` | `PARTIAL` | `E3` | 存在防伪造守卫（覆盖要求显式 submit）；但"提交/清理"未从 Episode 证据读取 |
| `AC-01` | `UNVERIFIED` | `E3` | 完整闭环的结构齐备，本次未实际执行 |
| `AC-02` | `ALIGNED` | `E2` | 覆盖标识稳定、可解释、可从保存证据重算（联合覆盖除外） |
| `AC-03` | **`CONFLICTING`** | `E3` | **四类结果被压成 `coverage_gain` 单个布尔值** |
| `AC-04` | `PARTIAL` | `E3` | 历史反馈确实影响"风险方向"；但反馈对象内容不参与选择或变异 |
| `AC-05` | **`CONFLICTING`** | `E3` | **随机模式消费跨 Episode 覆盖反馈**；公平性约束不完全满足 |
| `AC-06` | `PARTIAL` | `E3` | 可重复运行单 Campaign；冻结协议、配对编排与统计报告缺失 |
| `AC-07` | `PARTIAL` | `E3` | 事实、状态、指令序列保存齐备；重放复现未验证 |
| `AC-08` | `ALIGNED` | `E2` | 中断、失败结算、恢复有判定力测试，且不重复结算 |
| `AC-09` | `PARTIAL` | `E3` | 结论可追溯；但部分成功事实由调用方声明而非证据推导 |
| `AC-10` | `MISSING` | `E3`/`E4` | 能力未实现；**未发现任何文档宣称已完成**（这一纪律被遵守） |

### 2.1 状态分布

| 状态 | 数量 | 占比 |
|---|---|---|
| `ALIGNED` | 8 | 19% |
| `PARTIAL` | 24 | 56% |
| `CONFLICTING` | 5 | 12% |
| `MISSING` | 4 | 9% |
| `UNVERIFIED` | 2 | 5% |
| `NOT_APPLICABLE` | 0 | 0% |
| **合计** | **43** | **100%**（占比为四舍五入值，故分项之和为 101%） |

**注**："`PARTIAL` 占 56%"这一分布本身就是结论：当前代码在**大多数产品要求上已有正确的结构与部分路径**，但很少有要求被完整闭合。这与 `G-06`/`FR-JDG-*` 完全缺失、`G-02`/`FR-EXP-01` 等核心语义冲突并存。

---

## 3. 产品目标

### `G-01` 以完整交互轨迹而非仅最终回答作为测试和归因的基本证据

- **当前行为**：Episode 产出 `OfficeV2EpisodeResult`，含 `manifest`（带 `manifest_digest`、`trajectory_id`、`initial_state_digest`、`normalized_behavior_trace_digest`、`prompt_digest`）与 `oracle.trace_digest`；轨迹经 `TrajectoryStore` 分页追加后提交；覆盖特征从轨迹与工具证据提取。
- **代码证据**：`replay/models.py:87-112`（`RunManifest` 摘要字段）、`replay/manifest.py:90-101`（`trajectory.jsonl` 原子写）、`storage/trajectory_store.py`、`coverage/v2_input.py`。
- **测试证据**：`tests/unit/test_office_v2_oracle_trace.py`、`test_office_v2_oracle_evidence.py` 通过（`E2`）。
- **证据等级**：`E2`（提取逻辑）/ `E3`（容器内轨迹保存）
- **状态**：`ALIGNED`
- **判定理由**：轨迹是覆盖与 Oracle 的**唯一**输入源，没有"只看最终回答"的替代路径；`v2_input.py:111-177` 的验证器甚至要求显式 submit 终止，进一步排除"仅以回答判定"。
- **影响与风险**：低。容器内轨迹的完整性未在真实运行中验证（`E3`）。
- **需用户决定**：无。

### `G-02` 分别度量风险覆盖和行为覆盖，并用同一 Episode 的证据建立二者的联合覆盖关系

- **当前行为**：
  - 行为覆盖：成立。`V2CoverageDelta.new_primary_behavior_features` 等字段由 Episode 证据提取，有稳定 sha256 标识。
  - 风险覆盖：**弱**。风险维度只有 `RiskType`（固定 4 值枚举，`v2_seed_pools.py:19-25`）和 `RiskProgressLevel`（标量 0-3，[:28-41](src/sandbox/fuzzer/v2_seed_pools.py#L28-L41)）。SPEC §5.2 要求的前置条件、身份/授权上下文、风险载体、影响对象、里程碑等，只有 `new_risk_contexts` / `new_milestone_outcome_bits` 部分承接。
  - 联合覆盖：**未建立**。载体字段 `new_behavior_risk_links` 在 `src/` 内**零消费者**（详见 `episode-data-flow.md` §9.4）。
- **代码证据**：[coverage/v2_episode_coverage.py:193-212](src/sandbox/coverage/v2_episode_coverage.py#L193-L212)（`V2CoverageDelta` 11 字段）、[v2_promotion.py:59-61,93,120](src/sandbox/fuzzer/v2_promotion.py#L59-L61)（消费者范围）、[v2_campaign_state.py:28-41](src/sandbox/fuzzer/v2_campaign_state.py#L28-L41)。
- **测试证据**：`E2` 覆盖摘要与提取；**无任何测试断言联合覆盖被消费**。
- **证据等级**：`E3`
- **状态**：`PARTIAL`
- **判定理由**：行为覆盖与风险覆盖是分开度量的（满足第一半），但"用同一 Episode 证据建立联合覆盖关系"未完成——联合数据被计算和持久化，却不影响任何决策、报告或反馈维度。
- **影响与风险**：**高**。SPEC §5.3 是产品的核心创新点（回答"某类风险通过哪条行为路径被触达"）。当前无法回答该问题。产品级 `AC-02` 与 `AC-03` 直接依赖此项。
- **需用户决定**：→ `discussion-items.md` 第 1 项。

### `G-03` 使用双重覆盖反馈驱动语义变异和测试调度，主动探索长尾、低概率的不安全边界

- **当前行为**：进入下一轮**调度**的覆盖反馈只有一项：`state.risk_progress`（每风险类型 4 级标量），通过 `min(progress)` 过滤风险池（[v2_risk_pool_scheduler.py:118-133](src/sandbox/fuzzer/v2_risk_pool_scheduler.py#L118-L133)）。进入**变异**的覆盖反馈：**无**——算子选择三处均为均匀（`mutation/v2_policy.py:472,485,497`）。
- **代码证据**：`v2_campaign_loop.py:371-382`、`v2_risk_pool_scheduler.py:96-160`、`v2_selection.py:20-21`（`SelectionPolicy` 单值）。
- **测试证据**：`E2` 覆盖选择收据的可重放性；无测试断言"某覆盖缺口导致某方向被选中"。
- **证据等级**：`E3`
- **状态**：`PARTIAL`
- **判定理由**：存在一条真实但极窄的覆盖反馈通道。行为覆盖、联合覆盖、Finding 价值、饱和信号、证据置信度**均未进入任何决策**。SPEC §5.4 要求反馈"必须同时包含行为缺口、风险缺口、联合覆盖缺口、重复或饱和信号以及证据置信度"——当前只有风险进度一项。
- **影响与风险**：**高**。这决定"覆盖率引导"的实质强度。若只有标量进度，"引导"与"风险类型轮转"难以区分。
- **需用户决定**：→ `discussion-items.md` 第 2 项。

### `G-04` 提供公平的随机模式和覆盖率引导模式，在冻结的实验协议下验证引导策略是否更有效

- **当前行为**：两种模式存在，且 Agent、模型、Docker、工具、Oracle、覆盖提取、预算、报告完全共享（`strategy-comparison.md` §1）。但：
  1. **随机模式也消费 `risk_progress`**（`settle_risk_progress` 与 `progress_states=state.risk_progress` 均无策略分支）；
  2. 候选空间不对称（`include_derived=not independent`）;
  3. 候选质量门控不对称（三个去重集合在随机模式为空）。
- **代码证据**：见 `strategy-comparison.md` §2 的 16 处分叉表与 §4 的四步路径。
- **测试证据**：`tests/` 中引用 `RANDOM_INDEPENDENT` 的仅 1 个文件（`test_exploratory_campaign.py`），断言仅覆盖 CLI 解析与策略持久化；**无测试断言随机模式的选择不受历史影响**。
- **证据等级**：`E3`
- **状态**：`CONFLICTING`
- **判定理由**：`FR-EXP-01`/`AC-05` 明确禁止随机模式读取跨 Episode 覆盖反馈，当前实现违反该禁止；`FR-EXP-03` 的"唯一原则性差异"前提不成立。这是"当前行为与要求**直接冲突**"，不是"部分完成"。
- **影响与风险**：**最高**。这是整个课题的实验有效性前提。若对照组的独立性不成立，SPEC §3 的核心研究假设无法被检验，任何"引导模式更优/不优"的结论都不可归因。
- **需用户决定**：→ `discussion-items.md` 第 3 项。

### `G-05` 在 Docker 隔离环境中保存足够的状态与指令证据，支持确定性重放、问题复现和回归测试

- **当前行为**：证据保存充分（见 `FR-REP-02`，`ALIGNED`）。重放侧存在 `ToolReplayMode`（`execute_and_verify` / `stub_response`，`protocol.py:55-57`）、`ReplayMode`（`strict` / `live`，`replay/models.py:22-25`）、`ForkSuffixMode`（`live_and_record` / `strict_with_replacements`）、`Comparator.compare`（`replay/comparator.py:23-24`）与 `ReplayEngine` 的重新执行路径。
- **代码证据**：`replay/` 11 个文件；`engine/` 4 个文件。
- **测试证据**：`tests/integration/test_execution_engine.py` 等 29 项通过，但**不使用真实 Docker**（`grep docker` 无命中）。
- **证据等级**：`E3`
- **状态**：`PARTIAL`
- **判定理由**：保存满足要求；但 SPEC `FR-REP-03` 要求的四个重放层级在当前代码中是**另一套**枚举（工具重放 / 严格-实时 / fork 后缀），与 SPEC 的"轨迹重放 / 工具重放 / 状态验证 / 重新执行"**不是一一对应**；且未找到"重放结论必须说明确定性边界、外部依赖和允许偏差"的声明产物。
- **影响与风险**：中。层级命名不对应会导致"声明了某层级"与"实际验证了某层级"之间出现解释空间。
- **需用户决定**：→ `discussion-items.md` 第 5 项。

### `G-06` 逐步建立由黄金集、主动学习、漂移监控和人工复核组成的裁判置信度加固能力

- **当前行为**：**均无实现**。全仓 `grep` 未找到黄金集、主动学习采样、Judge 漂移监控、人工升级机制。代码中的 "drift" 全部属于**变异侧**（目标漂移 `TargetPreservationStatus.DRIFTED`、身份/授权/目标漂移 `mutation/v2_validation.py:77-111`），与 Judge 漂移监控无关。
- **代码证据**：`v2_target_preservation.py:22`、`mutation/v2_validation.py:77-111`（证明 drift 一词的既有语义是变异校验）。
- **文档证据**（`E4`）：`LOG.md:1488` 记录"裁判置信度、黄金集、评分漂移和主动"为**后续阶段**必须补充项；`LOG.md:1500` 记录"不实现通用分数、置信度"。
- **证据等级**：`E3`（无实现）/ `E4`（历史声明其未实现）
- **状态**：`MISSING`
- **判定理由**：SPEC §9.2 明确"本能力是正式产品目标，但当前阶段尚未实施完成"。审计确认：**未实施**。
- **影响与风险**：中（SPEC 已承认未完成，不构成隐瞒）。风险在于：任何把 Judge 输出当作"已校准"的解读都缺少依据。
- **需用户决定**：→ `discussion-items.md` 第 6 项。

---

## 4. 功能需求

### 6.1 闭环

#### `FR-FUZZ-01` 覆盖率引导模式必须形成真实闭环

- **原意**：场景 → 覆盖快照 → 识别缺口 → 选父样本与变异方向 → 生成校验 → 执行 → 保存 → 更新覆盖 → 形成下一轮反馈并更新语料库 → 下一代选择与变异。**反馈必须实际影响下一轮父样本选择、目标方向或变异内容**；"仅在 Episode 完成后计算覆盖率或决定是否保存结果，不构成完整的覆盖率引导模糊测试"。
- **当前行为**：链路的每一环都存在（见 `episode-data-flow.md`）。反馈对象 `NextGenerationFeedback` 被构造、持久化、摘要化，但 `decide_next_generation` 仅用它做**校验**与记录 `input_feedback_digest`（[v2_orchestrator.py:108-115,143-151](src/sandbox/fuzzer/v2_orchestrator.py#L108-L151)），**不把它传给** `choose_next_allocation`。实际影响下一轮的只有 `state.risk_progress`。
- **代码证据**：`v2_orchestrator.py:116-121`（`choose_next_allocation` 的实参中无 feedback）、`v2_campaign_loop.py:371-382`、`v2_risk_pool_scheduler.py:118-133`。
- **测试证据**：`E2` 覆盖反馈对象的构造与摘要；无测试断言"某缺口导致某选择"。
- **证据等级**：`E3`
- **状态**：`PARTIAL`
- **判定理由**：**不是** SPEC 所排除的"仅事后计算覆盖率"（风险进度确实反向影响了方向选择），所以不判 `CONFLICTING`；但"反馈必须影响父样本选择、目标方向**或**变异内容"这一析取只由最窄的一项满足。
- **影响与风险**：高。`G-03`、`FR-EXP-02`、`AC-04` 共享同一根因。
- **需用户决定**：→ `discussion-items.md` 第 2 项。

#### `FR-FUZZ-02` 变异器可在受控边界内改变多个语义维度

- **当前行为**：`mutation/` 27 个文件提供算子族、算子变体、字段注册表与约束；`EXPLORATORY_MUTATION_OPERATOR_INSTRUCTIONS` 与 `variant_instructions(...)` 注入算子约束（`v2_real_runtime.py:305-314`）。
- **代码证据**：`mutation/operators.py`、`mutation/v2_policy.py`、`mutation/v2_brief.py`、`mutation/v2_validation.py`。
- **测试证据**：`test_office_expression_mutation.py`、`test_office_mutation_batch.py`、`test_office_retarget_mutation.py`、`test_office_candidate_generation.py` 等通过（`E2`）。
- **证据等级**：`E3`/`E2`
- **状态**：`ALIGNED`
- **判定理由**：受控维度经字段注册表与验证器约束，不是自由文本改写；算子体系有稳定枚举与分配收据。
- **影响与风险**：低。
- **需用户决定**：无。

#### `FR-FUZZ-03` 每次变异必须保存 Mutation Plan（7 项）

- **当前行为**：`SemanticMutationPlan` 携带 `payload_slots`、`budget`、`plan_digest`、算子分配、`prompt_identity_digest`、`response_schema_digest`；`build_minimal_fact_brief` 保存 brief；预留记录保存 token/成本；父样本与代次可追溯。
- **逐项核对**：

| SPEC 要求项 | 是否具备 | 证据 |
|---|---|---|
| 输入覆盖快照或反馈摘要 | ✅ | `plan` 携带覆盖摘要；`GenerationDecision.input_feedback_digest` |
| 计划探索的风险/行为/联合缺口 | ⚠️ 部分 | 风险方向有（`risk_type`）；行为与**联合缺口无** |
| 允许变化与必须保持的语义维度 | ✅ | `payload_slots` 的 `content_constraints`、`max_length` |
| 父样本、代次和完整谱系 | ✅ | `SeedLineage`、`_recent_seed_lineage` |
| 随机种子、模型/Provider、提示版本和预算 | ✅ | `campaign_seed`、`provider_id`、`prompt_identity_digest`、`budget` |
| **目标保持或目标切换的校验结果** | ⚠️ 部分 | 只有**保持**的校验结果；切换不存在 |

- **代码证据**：`mutation/v2_brief.py:111-131`、`v2_selection.py:40-55`、`v2_real_runtime.py:234-249`。
- **证据等级**：`E3`
- **状态**：`PARTIAL`
- **判定理由**：7 项中 4 项完整、2 项部分（缺口维度不全；目标校验只有保持）、1 项由 `FR-FUZZ-04` 决定。
- **影响与风险**：中。Mutation Plan 是复现与审计的载体；缺口维度不全意味着事后无法回答"这次变异是为了补哪个缺口"。
- **需用户决定**：→ `discussion-items.md` 第 4 项。

#### `FR-FUZZ-04` 同时支持目标保持变异与目标切换变异；未声明的目标漂移属于无效变异

- **当前行为**：
  - **目标切换：不存在**。全仓无"按已注册合法目标显式切换探索目标"的路径。`_assess_target`（`v2_real_runtime.py:836`）只判定"是否保持了原目标"。
  - **目标漂移检测：存在且有效**。`TargetPreservationStatus.DRIFTED`（`v2_target_preservation.py:22`）会触发拒绝：`v2_campaign_loop.py:188-191` 记 `"selected-target-drifted"`，`v2_real_runtime.py:425-429` 记 `rejection_reason="selected-target-drifted"`。
- **代码证据**：`v2_target_preservation.py:130`、`v2_campaign_loop.py:188-191`、`v2_real_runtime.py:425-429`。
- **测试证据**：`tests/unit/test_target_judge.py` 通过（`E2`）。
- **证据等级**：`E3`
- **状态**：`MISSING`
- **判定理由**：要求的第一半（同时支持两种操作）**未找到实现**。第二半（漂移判定为无效）已实现，故这是一个"半完成"的要求，但因缺少的是**能力**而非**精度**，判 `MISSING` 比 `PARTIAL` 更准确。
- **影响与风险**：中高。缺少目标切换意味着探索只能围绕 4 个固定根种子的原目标加深，无法主动转向新风险目标——这与 `G-03` 的"主动探索长尾"直接相关。
- **需用户决定**：→ `discussion-items.md` 第 4 项。

#### `FR-FUZZ-05` 种子晋升、父样本权重、重复降权和长尾探索必须能追溯到具体覆盖证据

- **逐项核对**：

| 要求项 | 状态 | 证据 |
|---|---|---|
| 种子晋升可追溯到覆盖证据 | ✅ | `promote_coverage_artifact` 接收 `artifact`/`delta`/`facts`，收据含 `delta_digest`；仅 `coverage_guided` 生效 |
| **父样本权重** | ❌ **不存在** | `SelectionPolicy` 只有 `RANDOM_UNIFORM`，验证器强制单位权重（`v2_selection.py:20-21,72-75`） |
| 重复降权 | ✅ | 三个去重摘要集合拒绝重复内容/结构（`mutation/v2_validation.py:71,74,154`） |
| 长尾探索 | ⚠️ 弱 | `min(RiskProgressLevel)` 提供风险类型层面的均衡，是标量而非覆盖证据 |
| 避免少数热门路径垄断预算 | ⚠️ | `allow_coverage_saturation=False` 使饱和终止不可达（`v2_real_runtime.py:734-742`） |

- **证据等级**：`E3`
- **状态**：`PARTIAL`
- **判定理由**：4 项中 2 项完成、2 项弱/缺失。**"父样本权重"这一项在代码中完全不存在**——存在加权选择的数据结构（`WeightedSelectionOption.weight`、`total_weight`），但唯一的策略是均匀。
- **影响与风险**：中高。父样本权重是"避免热门路径垄断预算"的主要手段；缺失后该目标只能靠去重间接实现。
- **需用户决定**：→ `discussion-items.md` 第 1、2 项。

### 7. 随机模式与公平比较

#### `FR-EXP-01` `random_independent` 不得读取跨 Episode 反馈影响下一轮选择

- **原意**：从同一合法候选空间独立采样，拥有相同语义变异能力，**不得读取之前 Episode 的覆盖快照、缺口、晋升结果或其他跨 Episode 反馈**。
- **当前行为**：**违反**。`settle_risk_progress`（`v2_real_runtime.py:751-756`）与 `progress_states=state.risk_progress`（`v2_campaign_loop.py:373`）**在两种模式下均无条件生效**；`select_risk_pool_seed` 用 `min(progress)` 过滤风险池后再均匀抽取（`v2_risk_pool_scheduler.py:118-133`）。因此随机模式第 N+1 个 Episode 的方向选择**依赖第 N 个 Episode 的执行结果**。
- **补充事实（有利于当前实现）**：随机模式确实**不**晋升（`F2`）、**不**增长目录（`F4`）、**不**加载 `candidate_history`（`F8`）、**不**记录反馈摘要（`F12`/`F13`）。所有**显式**的策略守卫都正确。
- **证据等级**：`E3`
- **状态**：`CONFLICTING`
- **判定理由**：要求是明确的禁止性条款，当前代码有确定的违反路径。`RNG 输入不含历史`（`v2_selection.py:106` 的 docstring）只对该函数成立，历史通过**候选集构成**进入了选择。
- **影响与风险**：**最高**。对照组的独立性是 SPEC §3 假设检验的前提；不成立则实验结果不可归因。
- **需用户决定**：→ `discussion-items.md` 第 3 项。

#### `FR-EXP-02` `coverage_guided` 读取历史双重覆盖反馈，针对风险/行为/联合缺口选择父样本及变异方向，并记录"为什么选择"

- **逐项核对**：

| 要求项 | 状态 | 证据 |
|---|---|---|
| 读取历史反馈 | ✅ | `state.risk_progress` |
| 针对**风险**缺口选择 | ⚠️ 弱 | 只有"进度最低"标量，不是缺口结构 |
| 针对**行为**缺口选择 | ❌ | 行为覆盖不参与选择 |
| 针对**联合**缺口选择 | ❌ | 联合字段零消费者 |
| 选择**父样本** | ✅ | 池内均匀（非按缺口加权） |
| 选择**变异方向** | ❌ | 算子选择均匀，无覆盖输入 |
| 记录"为什么选择" | ✅ | `reason_codes`、`SelectionReceipt.reason_codes`、`RiskPoolSelection.reasons` |

- **证据等级**：`E3`
- **状态**：`PARTIAL`
- **判定理由**：7 项中 3 项完成、1 项弱、3 项缺失。特别地，**变异方向不受覆盖反馈影响**——SPEC 明确把"变异方向"列为必须由反馈驱动的对象。
- **影响与风险**：高。与 `G-03`、`FR-FUZZ-01`、`AC-04` 同根因。
- **需用户决定**：→ `discussion-items.md` 第 2 项。

#### `FR-EXP-03` 公平性约束（8 类必须冻结一致）

- **当前行为**：见 `strategy-comparison.md` §5 的 12 项核查表。9 项明确一致、1 项部分一致（存储层对随机模式断言更严）、2 项不一致（合法候选空间、唯一原则性差异）。
- **代码证据**：`F1`、`F5`-`F8`、`F2`、`F4`（`strategy-comparison.md` §2）。
- **证据等级**：`E3`
- **状态**：`CONFLICTING`
- **判定理由**：要求的核心是"唯一原则性差异应是：是否消费跨 Episode 覆盖反馈"。当前既存在**额外的**结构性差异（候选空间、质量门控），又存在"两种模式**都**消费反馈"的事实，与该前提直接冲突。
- **补充张力（重要）**：`FR-FUZZ-05` **要求**引导模式做种子晋升与语料库增长，而晋升必然改变父样本空间与候选集。因此 `FR-EXP-03` 与 `FR-FUZZ-05` 之间存在**产品规格内部的张力**，需要在实验设计 SPEC 中显式解决，不能只靠改代码消除。
- **影响与风险**：最高，同 `G-04`。
- **需用户决定**：→ `discussion-items.md` 第 3 项。

#### `FR-EXP-04` 预先冻结、版本化的实验计划；配对 Campaign；多个随机种子；完整保存成功/失败/超时/无增益

- **逐项核对**：

| 要求项 | 状态 | 证据 |
|---|---|---|
| 预先冻结、版本化的实验计划 | ❌ | 未找到实验计划对象或冻结协议 |
| 配对 Campaign | ❌ | 未找到跨 Campaign 配对编排 |
| 多个随机种子 | ⚠️ | CLI 有 `--campaign-seed`（`v2_cli.py:91`），但**默认值 0**，且无多种子编排 |
| 完整保存各类 Episode | ⚠️ 部分 | `valid_committed_episodes` / `invalid_or_failed_attempts` 计数、非 Episode 结算、失败收据、恢复记录均存在 |

- **代码证据**：`v2_cli.py:91`、`v2_campaign_state.py:25-51`、`v2_campaign_store.py:1585`、`v2_report.py:40-46,73-76`。
- **证据等级**：`E3`
- **状态**：`PARTIAL`
- **判定理由**：4 项中 2 项缺失、2 项部分。保存侧基础扎实，**实验协议层完全缺失**。
- **影响与风险**：高。`AC-06` 依赖此项。
- **需用户决定**：→ `discussion-items.md` 第 7 项。

#### `FR-EXP-05` 报告至少分别展示 7 类内容

- **当前行为**：报告（`v2_report.py`，仅 94 行）输出：`coverage_counts`（4 个**终态计数**）、`corpus` 规模、`seed_pools.sizes` + `progress`、`budget`、`decisions`/`feedback`/`findings`（**原始 JSON 列表**）、`recovery`。
- **逐项核对**：

| SPEC 要求 | 状态 | 说明 |
|---|---|---|
| 行为覆盖增长曲线（Episode/时间/Token/成本） | ❌ | 只有终态计数；`decisions` 列表可事后重建，但无计算 |
| 风险覆盖增长曲线 | ⚠️ | `risk_contexts` 终态计数 + `progress` 等级；无曲线 |
| **联合覆盖增长曲线** | ❌ | **联合覆盖本身不存在** |
| 达到指定覆盖门槛所需预算 | ❌ | 无门槛概念，无计算 |
| 独特 Finding 数、重复率、连续无增益长度 | ⚠️ | `findings` 列表可数；**无重复率与连续无增益长度计算**（`consecutive_no_gain` 作为每代布尔存在于反馈中，未聚合） |
| 失败率、超时率、恢复与重放成功率 | ⚠️ | `recovery` 字典与计数存在；**无率计算**，无重放成功率 |
| 多次运行的均值、离散程度、统计不确定性 | ❌ | 报告是**单 Campaign** 投影，无跨运行聚合 |

- **代码证据**：`v2_report.py:31-79`。
- **证据等级**：`E3`
- **状态**：`PARTIAL`
- **判定理由**：7 类中 0 类完整、4 类部分、3 类缺失。原始数据大多已持久化（有利于后续补充），但**报告层没有做过任何聚合计算**，且联合覆盖这一维度在数据层就不存在。
- **附加结构观察**：报告构建器直接访问 `store._db`（`v2_report.py:17,22,27`），绕过 `V2CampaignStore` 的公开方法。这不影响正确性，但使报告与私有 schema 耦合。
- **影响与风险**：高。`AC-06` 要求"分别报告风险、行为、联合覆盖及效率、失败和不确定性"；按现状无法产出该报告。
- **需用户决定**：→ `discussion-items.md` 第 7 项。

### 8. 确定性沙箱与重放

#### `FR-REP-01` 每个 Episode 在具有副作用隔离能力的 Docker 环境中执行

- **当前行为**：`DockerSandboxScheduler` + `SandboxConfig`；`SandboxConfig.network_mode` 默认 `"none"`（[config.py:24](src/sandbox/config.py#L24)）；`v2_cli.py:203` 按 `--ollama-mode` 设为 `host` 或 `none`；资源限制 `memory_limit="22g"`、`nano_cpus=8e9`、`pids_limit=512`、`tmpfs_size="2g"`（`v2_cli.py:219-224`）；`workspace_storage="archive_volume"`。
- **测试证据**：`tests/integration/` 29 项通过，但 **`grep docker` 无命中** ⇒ 这些测试不使用真实 Docker，**不能**证明隔离能力。
- **证据等级**：`E3`
- **状态**：`UNVERIFIED`
- **判定理由**：隔离结构在代码中完整且默认拒绝网络，但"具有副作用隔离能力"是一个**运行期性质**，本次未运行容器，缺乏判断依据。按审计 Spec §7："不能执行的 Docker……路径必须标为未验证，不得用本地模拟结果替代。"
- **影响与风险**：中。`SEC-01`/`SEC-04` 的最终保障依赖此项。
- **需用户决定**：→ `discussion-items.md` 第 9 项。

#### `FR-REP-02` 保存足以解释和复核执行的证据（6 类）

- **逐项核对**：

| SPEC 要求 | 证据 | 状态 |
|---|---|---|
| 原始任务、变异计划、父样本和谱系 | `SemanticMutationPlan`、`MutationPreparation`、`SeedLineage` | ✅ |
| 初始状态、关键状态快照和最终状态 | `RunManifest.initial_state_digest`、`V2CampaignStateSnapshot` | ✅ |
| 有序的模型决策/指令、工具调用和工具结果 | `trajectory.jsonl`（分页追加后提交）、`ToolEvidenceExchange` | ✅ |
| 策略判断、授权上下文、最终回答和终止原因 | `ExecutionClosure`、`TargetPreservationStatus`；终止原因见 `FR-JDG-01` 的保留意见 | ⚠️ |
| Oracle 事实、风险/行为/联合覆盖结果和 Finding | `oracle_result`、`V2CoverageDelta`、`finding` 表 | ✅（联合项无消费者） |
| 配置、随机种子、依赖身份和内容完整性摘要 | `determinism_config_digest`、`campaign_seed`、`*_digest` 全量 | ✅ |

- **代码证据**：`replay/models.py:87-112`、`replay/manifest.py:90-101`、`v2_loop_contracts.py:148-178`、`v2_real_runtime.py:778-807`。
- **测试证据**：`test_office_v2_oracle_evidence.py`、`test_office_v2_oracle_models.py`、`test_office_v2_fuzzer_corpus.py` 等通过（`E2`）。
- **证据等级**：`E2`/`E3`
- **状态**：`ALIGNED`
- **判定理由**：6 类证据的**保存结构**全部存在且带内容摘要；这是本次审计中完成度最高的一组要求。
- **影响与风险**：低。
- **需用户决定**：无。

#### `FR-REP-03` 明确区分轨迹重放、工具重放、状态验证、重新执行；不得误称逐 Token 确定；须说明确定性边界

- **当前行为**：存在**三套**重放相关枚举：`ToolReplayMode`（`execute_and_verify` / `stub_response`，`protocol.py:55-57`，默认 `EXECUTE_AND_VERIFY`）、`ReplayMode`（`strict` / `live`，`replay/models.py:22-25`）、`ForkSuffixMode`（`live_and_record` / `strict_with_replacements`，[:27-30](src/sandbox/replay/models.py#L27-L30)）；另有 `Comparator.compare`（`replay/comparator.py:23-24`）做行为比对。
- **缺口**：
  1. 上述枚举与 SPEC 的四层级**不是一一对应**，没有"状态验证"与"重新执行"的独立标识；
  2. **未找到**声明确定性边界、外部依赖与允许偏差的产物（无相关字段或文档对象）。
- **证据等级**：`E3`
- **状态**：`PARTIAL`
- **判定理由**：重放能力的**机制**存在（含严格/实时之分与行为比对），但 SPEC 要求的是**显式区分四个层级并声明边界**，这是文档与契约层面的要求，当前未满足。
- **影响与风险**：中高。层级不明确会导致 `FR-REP-04` 的"按声明的重放层级复现"失去锚点。
- **需用户决定**：→ `discussion-items.md` 第 5 项。

#### `FR-REP-04` 安全意义的 Finding 必须关联可定位证据，并能通过适用的重放层级复现或解释；失败重放也须保存原因

- **当前行为**：`build_finding`（`v2_feedback.py`）由 `facts`/`delta`/`execution` 构造，`CandidateSettlement` 绑定 `execution_record_id`、`coverage_delta_digest`、`promotion_decision_digest`；`finding` 表按 `finding_key` 排序存储（`v2_report.py:27-30`）。
- **缺口**：Finding 与**具体重放层级**的关联未建立（因 `FR-REP-03` 的层级未定义）；"失败重放保存原因"有 `RunManifest.incomplete_reason`（`replay/models.py:107`）但未与 Finding 关联。
- **证据等级**：`E3`
- **状态**：`PARTIAL`
- **判定理由**：可定位证据成立；"通过适用重放层级复现或解释"未验证且缺少层级定义。
- **影响与风险**：中。
- **需用户决定**：→ `discussion-items.md` 第 5 项。

### 9. Oracle 与裁判置信度

#### `FR-JDG-01` 工具调用、结果、状态变化、权限事实、策略结果和终止原因优先由确定性 Oracle 判定

- **当前行为**：
  - **Oracle 确定性成立**：`scenarios/office_v2/oracle.py`、`security_oracle.py`、`utility_oracle.py` 为规则式实现，输出 `oracle_result` 与 `trace_digest`；有 6 个专门测试文件。
  - **但 Oracle 在容器内计算**，宿主机侧**没有**独立重算或交叉验证（`episode-data-flow.md` §8）。
  - **例外项**：`termination_reason` 在成功路径由调用方硬编码为 `"submit"`（`v2_real_runtime.py:673-675`），**不是**由 Oracle 判定。
- **代码证据**：`v2_real_runtime.py:666-676`、`v2_loop_contracts.py:306-373`（`observed_payload_refs`/`used_payload_refs` 确实来自真实 Oracle 暴露阶段）。
- **证据等级**：`E3`
- **状态**：`PARTIAL`
- **判定理由**：`ALIGNED` 需要 6 类事实全部由 Oracle 判定。当前 5 类成立，**终止原因**由调用方声明、缺失宿主机独立重算，故降为 `PARTIAL`。
- **影响与风险**：中高。Oracle 无独立复核意味着其正确性完全依赖容器内实现；终止原因未推导使 `FR-OPS-02` 的分类保证被削弱。
- **需用户决定**：→ `discussion-items.md` 第 6 项。

#### `FR-JDG-02` LLM-as-Judge 只用于需要语义判断的事项，不得改写确定性事实，也不得成为高风险结论的唯一证据

- **当前行为**：`OllamaTargetPreservationJudge` 是 V2 主链中**唯一**的 Judge，仅用于目标保持判定（`v2_real_runtime.py:842-843`）。它**不**参与覆盖提取、Finding 判定或 Oracle 事实。
- **正面结论**：Judge **没有**改写确定性事实——职责边界在代码中清晰。
- **保留意见**：目标保持判定**完全**由 Judge 给出，而该判定是**晋升门控**的输入（`v2_campaign_loop.py:188-191`）。即：一个 LLM 判断可以是"样本能否进入语料库"的**唯一**证据来源。它是否算"高风险结论"取决于产品定义。
- **另一个事实**：`--ollama-mode embedded` 时 `target_judge=None`（`v2_cli.py:287-291`），此时目标保持不可判定，`_assess_target` 的返回使晋升无法完成——即 Judge 不可用会**阻断**流程，而不是静默降级。
- **代码证据**：`v2_cli.py:287-291`、`v2_real_runtime.py:836-849`、`v2_target_judge.py:92-96`。
- **测试证据**：`tests/unit/test_target_judge.py` 通过（`E2`）。
- **证据等级**：`E3`
- **状态**：`PARTIAL`
- **判定理由**：前半（用途限定、不改写事实）完全满足；后半（不得成为高风险结论的唯一证据）存在解释空间，需要产品定义"什么算高风险结论"。
- **影响与风险**：中。若目标保持被判为高风险结论，则当前实现需要第二条独立证据。
- **需用户决定**：→ `discussion-items.md` 第 6 项。

#### `FR-JDG-03` 裁判置信度加固必须包括 6 项机制

- **当前行为**：**6 项全部不存在**——无黄金集回归库、无主动学习采样、无 Judge 版本/提示/阈值变化回归、无漂移监控、无低置信度人工升级、无可追溯的 Judge 版本/置信度/复核记录。
- **代码证据**：全仓 `grep` 无命中（`grep -niE "golden|active_learning|drift|human_review|escalat"` 的命中全部属于变异侧 drift 校验或已废弃的 v1 场景）。
- **文档证据**（`E4`）：`LOG.md:1488`、`LOG.md:1500` 记录该能力被明确划入后续阶段且**不实现**。
- **证据等级**：`E3`/`E4`
- **状态**：`MISSING`
- **判定理由**：未找到任何对应实现。
- **影响与风险**：中（SPEC §9.2 已自认未完成）。当前 Judge 输出**没有**置信度字段，也没有人工复核入口。
- **需用户决定**：→ `discussion-items.md` 第 6 项。

### 10. 持久化、失败与恢复

#### `FR-OPS-01` 明确的一致性边界，避免半结算状态

- **当前行为**：
  - 结算入口 `commit_settlement`（`v2_campaign_store.py:1048`）在**一个** `BEGIN IMMEDIATE` 事务内写入 Episode 结果、覆盖、Finding、预算与调度；
  - 状态按 `state_digest` 内容寻址（`_insert_snapshot`、`load_state_by_digest`，[:673-746](src/sandbox/fuzzer/v2_campaign_store.py#L673-L746)）；
  - `_accept_advance`（`v2_runtime.py:246-261`）在 driver 已持久化时，要求 state / closure / feedback 三者与 `advance` 完全相等。
- **缺口**：**文件系统与 SQLite 之间无原子性**。轨迹、Artifact、Manifest 先写文件再提交数据库；中间崩溃会产生孤儿文件，恢复靠 `has_saved_recording` 之类检查而非文件—数据库比对。
- **测试证据**：`test_office_v2_fuzzer_work.py::test_ambiguous_and_sealed_work_never_auto_retry` 等通过（`E2`）。
- **证据等级**：`E2`/`E3`
- **状态**：`PARTIAL`
- **判定理由**：**单库**的一致性边界清晰且事务化（满足要求的主要意图）；**跨存储**边界未定义，属于"已有结构但未完成整个要求"。
- **影响与风险**：中。孤儿文件不影响结算正确性，但影响"结果已执行但状态不可恢复"的反向情形（磁盘内容无数据库记录）。
- **需用户决定**：→ `discussion-items.md` 第 9 项。

#### `FR-OPS-02` 超时、容器失败、工具失败、无效变异、Judge 不可用和存储失败必须分类保存，不得悄悄转成成功或从统计中删除

- **当前行为**：分类机制齐备——`_episode_failure_receipt`（`v2_real_runtime.py:1116`）、`_episode_failure_is_retryable`（[:1086](src/sandbox/fuzzer/v2_real_runtime.py#L1086)）、`_is_behavior_limit_failure`（[:1096](src/sandbox/fuzzer/v2_real_runtime.py#L1096)）、`_close_non_episode`（[:867](src/sandbox/fuzzer/v2_real_runtime.py#L867)）、`commit_non_episode_settlement`（`v2_campaign_store.py:1585`）、暂停原因字符串、`invalid_or_failed_attempts` 计数。
- **保留意见**：成功路径的 `termination_reason` 是硬编码 `"submit"` 而非从证据读出（同 `FR-JDG-01`）。这不影响**当前**分类的准确性，但意味着"失败被悄悄转成成功"这一情形缺少机制性防护。
- **测试证据**：`E2` 覆盖密封工作不自动重试、失败收据等。
- **证据等级**：`E2`/`E3`
- **状态**：`PARTIAL`
- **判定理由**：分类结构完整且被测试（倾向 `ALIGNED`），但成功事实非证据推导，与"不得悄悄转成成功"的保障意图有直接关联，故保守判 `PARTIAL`。
- **影响与风险**：中。
- **需用户决定**：→ `discussion-items.md` 第 9 项。

#### `FR-OPS-03` 恢复必须幂等；已完成且证据完整的外部执行不得被无依据地重复执行

- **当前行为**：
  - `seal_attempt` 要求已有收据与本次收据**完全相等**，否则 `ValueError("Saved Episode differs from its existing success receipt")`（`v2_real_runtime.py:659-663`）；
  - `load_preparation_for_allocation` 命中时直接复用，不重复调用 Provider（[:315-318](src/sandbox/fuzzer/v2_real_runtime.py#L315-L318)）；
  - `has_saved_recording` / `_saved_recording` 避免重复执行（`v2_real_episode.py:250-261`）；
  - `_recover_interrupted_work`（`v2_real_runtime.py:169`）、`resume_incomplete`（[:850](src/sandbox/fuzzer/v2_real_runtime.py#L850)）、存储层 `recover`/`inspect_recovery`/`resume_ambiguous_work`；
  - `plan-next` 在状态摘要与代次一致时复用旧决策（`v2_cli.py:140-144`）。
- **测试证据**：`test_exploratory_campaign.py::test_exploratory_resume_reopens_a_paused_campaign`、`test_exploratory_resume_can_extend_episode_target` 等通过（`E2`）。
- **证据等级**：`E2`
- **状态**：`ALIGNED`
- **判定理由**：多处**相等性断言**（而非"跳过"）保证重复执行会被显式拒绝；这是本次审计中防护最扎实的一组要求。
- **影响与风险**：低。
- **需用户决定**：无。

#### `FR-OPS-04` 预算按可解释维度记录（Episode、模型调用、Token、工具调用、时间和成本）；到达边界应有明确终止原因

- **当前行为**：`CampaignBudgetSnapshot`（`v2_campaign_state.py:25-33`）含 `episode_limit`/`used_episodes`、`mutator_token_limit`、`monetary_microunit_limit`；`ExecutionCosts`（`v2_corpus.py:138-142`）含 `mutator_tokens`、`agent_tokens`、`elapsed_ms`、`monetary_microunits`。终止原因有 `"generation-attempt-budget-exhausted"`、`"campaign-driver-failure"`、`"campaign-recovery-failure"`、`"unclassified-provider-failure"` 等。
- **逐项核对**：

| 维度 | 是否记录为预算维度 |
|---|---|
| Episode | ✅ `episode_limit` / `used_episodes` / `reserved_episodes` |
| 模型调用次数 | ❌ 未作为预算维度（`--max-steps`/`--max-tool-calls` 是每 Episode 步数限制，不是 Campaign 预算） |
| Token | ✅ `mutator_tokens` + `agent_tokens` |
| 工具调用次数 | ❌ 未作为预算维度 |
| 时间 | ✅ `elapsed_ms`（预留 + 实耗，且 `actual > reservation` 会报错） |
| 成本 | ✅ `monetary_microunits` |

- **代码证据**：`v2_campaign_state.py:25-51,89-196`、`v2_corpus.py:138-142`、`v2_work.py:55-59`。
- **证据等级**：`E3`
- **状态**：`PARTIAL`
- **判定理由**：6 个维度中 4 个记录完整，2 个（模型调用次数、工具调用次数）未作为 Campaign 预算维度。
- **影响与风险**：中。`FR-EXP-05` 要求按"Token 和成本"报告增长曲线，这两项已具备；但"调用"维度的缺失使跨模型对比时的归一化基准不完整。
- **需用户决定**：→ `discussion-items.md` 第 7 项。

#### `FR-OPS-05` 内容摘要用于发现损坏和截断，不替代产品层面的语义验证

- **当前行为**：全仓统一 `sha256_digest(canonical_json_bytes(value))`；`OfficeV2Contract` 子类在 `@model_validator(mode="after")` 中重算并比对自身摘要（大量实例，如 `V2CoverageDelta.digest_matches`、`SelectionReceipt.selection_and_digest_match`）；`RunManifest` 携带 `normalized_behavior_trace_digest`、`determinism_config_digest`；报告带 `report_digest`（`v2_report.py:78`）。
- **重要区分**：摘要校验与语义校验在代码中是**分离**的（摘要比对不代替 Oracle 判定），符合"不替代产品层面的语义验证"。
- **测试证据**：大量 `E2` 测试直接断言摘要不匹配时 `raise`（如 `test_office_v2_oracle_models.py`）。
- **证据等级**：`E2`
- **状态**：`ALIGNED`
- **判定理由**：实现完整、有判定力测试、且不冒充语义验证。
- **影响与风险**：低。
- **需用户决定**：无。

### 11. 安全、隐私与授权边界

#### `SEC-01` 仅针对用户明确授权的合成环境、测试数据和目标运行

- **当前行为**：Office Workspace V2 为合成场景（`build_office_v2_world.py` 建场）；`AUTHORIZED-EVALUATION-SCOPE.md` 记录授权范围；`SandboxConfig.network_mode` 默认 `"none"`（`config.py:24`）。
- **证据等级**：`E3`（代码默认值）/ `E4`（授权范围文档）
- **状态**：`ALIGNED`
- **判定理由**：场景是合成的、网络默认拒绝、授权范围有独立文档。`ALIGNED` 不依赖 `E4`——`E3` 的合成场景与网络默认值已构成代码证据。
- **影响与风险**：低。
- **需用户决定**：无。

#### `SEC-02` 默认拒绝连接真实生产系统、真实账号或未授权外部服务

- **当前行为**：
  - ✅ `SandboxConfig.network_mode` 的**类默认值**是 `"none"`（`config.py:24`）；
  - ✅ `config.py:50-51` 拒绝带凭据/查询串的 `ollama_endpoint`：`raise ValueError("ollama_endpoint must not contain credentials or query data")`；
  - ❌ **但 `v2_cli.py` 的 `--ollama-mode` 默认值是 `host`**（`v2_cli.py:64`），它把容器网络设为 `"host"`（[:203](src/sandbox/fuzzer/v2_cli.py#L203)、[:246](src/sandbox/fuzzer/v2_cli.py#L246)）。即：**按默认参数运行会启用宿主网络**，而不是"默认拒绝"。
- **证据等级**：`E3`
- **状态**：`PARTIAL`
- **判定理由**：存在"默认拒绝"的实现（`SandboxConfig`），但命令行入口的默认值覆盖了它。这属于"有相关结构但未完成整个要求"。
- **影响与风险**：中高。`host` 网络模式下容器可访问宿主可达的任何服务。这是刻意的工程便利（连接宿主机 Ollama），但与"默认拒绝"的字面要求相反。
- **需用户决定**：→ `discussion-items.md` 第 8 项。

#### `SEC-03` 日志和 Artifact 不得保存密码、私钥、云密钥或不必要的完整敏感响应；必须支持必要的脱敏与最小化保留

- **当前行为**：
  - ✅ 拒绝带凭据的 endpoint（同上）；
  - ⚠️ `coverage/v2_tool_behavior.py:266-271` 有 "redacted argument-shape contract"：工具证据只保留**参数名与值形状**（`argument_name:value_shape`），不保留参数值。这是一种**有效的最小化**机制——覆盖特征不落原始参数值。
  - ❌ **未找到**通用的日志或 Artifact 脱敏层：无 redaction/sanitization 模块，无按字段的密钥过滤，`trajectory.jsonl` 保存完整工具结果。
- **证据等级**：`E3`
- **状态**：`PARTIAL`
- **判定理由**：存在**一处**有意义的最小化（参数形状化），但要求的是"日志和 Artifact"整体不得保存敏感内容，并"支持必要的脱敏与最小化保留"。通用机制缺失。
- **影响与风险**：中高。轨迹保存完整工具结果；若被测场景返回凭据类内容，会被持久化。当前 Office V2 为合成场景（`SEC-01`）降低了暴露风险，但机制不存在。
- **需用户决定**：→ `discussion-items.md` 第 8 项。

#### `SEC-04` 每个 Episode 结束后验证副作用被隔离或清理；清理失败必须成为显式测试结果

- **当前行为**：
  - ✅ 存在清理路径：`DockerOfficeV2EpisodeRunner.cleanup_interrupted`（`v2_real_episode.py:152`）、`_recover_interrupted_work`（`v2_real_runtime.py:169`）、容器 `workspace_storage="archive_volume"`、`tmpfs_size="2g"`；
  - ❌ **`cleanup_confirmed=True` 在成功路径硬编码**（`v2_real_runtime.py:675`），不是从 Episode 结果读出。
- **判定影响**：若清理失败，该失败**无法**出现在 `ExecutionClosure` 中——闭包总是声明清理成功。清理失败只可能通过其他路径（异常 → 失败收据）体现，而该路径未被本次静态分析证实。
- **证据等级**：`E3`
- **状态**：`PARTIAL`
- **判定理由**：清理的**动作**存在；"清理失败必须成为显式测试结果"这一**可观测性**要求**未满足**于闭包记录层面。未判 `CONFLICTING` 是因为不确定是否存在其他记录路径——这属于"证据不足以判断"的部分，已在影响栏标明。
- **影响与风险**：中高。`SEC-04` 是安全边界的一部分，而当前无法从保存证据判断某次运行的清理是否真的成功。
- **需用户决定**：→ `discussion-items.md` 第 9 项。

#### `SEC-05` Controller 不得替 Agent 伪造工具调用、授权、状态变化、提交或成功证据

- **当前行为**：
  - ✅ **存在防伪造守卫**：覆盖证据要求显式 submit 终止，否则 `raise ValueError("coverage requires an explicit submit termination")`（`coverage/v2_input.py:111-177`）；只有通过该验证的 Episode 才能产生覆盖；
  - ✅ `evidence_backed_exposure` 与 `observed_payload_refs`/`used_payload_refs` **确实**由真实 Oracle 暴露阶段推导（`v2_loop_contracts.py:306-373`）；
  - ⚠️ **`submitted=True`、`termination_reason="submit"`、`cleanup_confirmed=True` 三者由调用方硬编码**（`v2_real_runtime.py:673-675`），不是从 `episode` 读取。
- **准确性说明（重要）**：本次审计**逐行核对**后确认——这三个硬编码值**当前是准确的**，因为 `_settle_episode` 只在成功路径被调用，而该路径上游已由 `V2BehaviorSourceFacts` 验证器保证"显式 submit"。因此**当前不存在伪造**。
- **为什么仍判 `PARTIAL`**：要求是"不得替 Agent 伪造……提交或成功证据"。当前实现的安全性依赖**上游路径**而非**此处读取**。若未来新增一条不经过该验证器的结算路径，这三个字段会静默变成不实声明**且不会报错**。缺少**机制性**保证。
- **证据等级**：`E3`
- **状态**：`PARTIAL`
- **判定理由**：当前行为不违反要求（故非 `CONFLICTING`），但缺少从证据推导的机制（故非 `ALIGNED`）。
- **影响与风险**：中。这是一个**潜伏**风险：当前正确，但防护来自间接约束。
- **需用户决定**：→ `discussion-items.md` 第 6、9 项。

### 13. 产品级验收标准

#### `AC-01` 至少一个代表性 Scenario 能执行从种子/父样本到变异、Episode、Oracle、双重覆盖、反馈和下一代的完整闭环

- **当前行为**：Office Workspace V2 场景与该闭环的**全部环节**在代码中齐备（见 `episode-data-flow.md`），`build_exploratory_bootstrap` 提供 16 个根种子，`exploratory-smoke` 子命令提供单 Episode 冒烟入口。
- **证据等级**：`E3`
- **状态**：`UNVERIFIED`
- **判定理由**：闭环的**结构**完整，但"能执行"是运行期性质，本次未运行（TASK 禁止 Docker/模型）。按审计 Spec §7，不得用本地模拟结果替代。
- **影响与风险**：中。这是本审计中最重要的**未验证项**——所有 `E3` 结论都以"该闭环能跑起来"为前提。
- **需用户决定**：→ `discussion-items.md` 第 9 项。

#### `AC-02` 行为覆盖、风险覆盖和联合覆盖具有稳定、可解释、可从保存证据重新计算的标识

- **当前行为**：行为与风险覆盖：特征是 sha256 摘要键（`primary_behavior_feature_keys`、`risk_context_keys`、`milestone_outcome_bit_keys`、`canonical_fact_digests`），契约反序列化时重算摘要，**可从保存证据重算**（`G-01`、`FR-OPS-05` 的同一机制）。**联合覆盖：不存在可重算标识**（字段零消费者，无快照键）。
- **测试证据**：`test_office_v2_oracle_rebuild.py` 等通过（`E2`）。
- **证据等级**：`E2`（行为/风险）/ `E3`（联合缺失）
- **状态**：`ALIGNED`
- **判定理由**：本条要求覆盖"行为、风险、联合"三者。严格按字面，联合覆盖的存在性由 `G-02` 承担（那里判 `PARTIAL`）；此处按"标识的稳定性与可重算性"这一**性质**判定，行为与风险两项**性质成立**且有 `E2` 支持。为避免重复计分，联合覆盖的缺失在 `G-02` 与 `AC-03` 记录。
- **影响与风险**：低（就标识性质而言）。
- **需用户决定**：无（联合覆盖问题见 `G-02`）。

#### `AC-03` 能够分别识别"仅新行为""仅新风险""新联合关系"和"无新增"四类结果

- **当前行为**：**不能**。[v2_real_runtime.py:1158-1170](src/sandbox/fuzzer/v2_real_runtime.py#L1158-L1170)：

  ```python
  def _coverage_gain(delta, facts) -> bool:
      return bool(
          delta.new_primary_behavior_features
          or set(delta.new_exposure_stages) & evidence_backed_exposure
          or delta.new_milestone_outcome_bits
          or delta.new_unexpected_violations
      )
  ```

  四类结果被压缩为一个布尔值，随后传入 `record_valid_episode(..., coverage_gain=...)`（[:728-731](src/sandbox/fuzzer/v2_real_runtime.py#L728-L731)）。
- **代码证据**：`v2_real_runtime.py:1158-1170,722,728-731`；`V2CoverageDelta` 的 11 个字段可**分别**判断四类，但没有任何代码这样做。
- **测试证据**：无测试断言四类区分（`E2` 缺失）。
- **证据等级**：`E3`
- **状态**：`CONFLICTING`
- **判定理由**：SPEC §5.3 明文规定"系统必须保留以下四种结果，**不能只输出一个 `coverage_gain` 布尔值**"。当前实现的函数名、语义与用途与该禁止条款**逐字对应**，属直接冲突。`AGENTS.md` §4 也把 `coverage_gain` 列为必须核查其准确语义的名称。
- **影响与风险**：**高**。四类区分是"双重覆盖"模型的核心可解释性保证（SPEC §5.4："不得用不可拆解的总分掩盖某一维度退化"）。当前无法回答"某代是无新增，还是仅行为新增而无风险进展"。
- **需用户决定**：→ `discussion-items.md` 第 1 项。

#### `AC-04` 覆盖率引导模式的历史反馈确实影响后续选择或变异，并有可审计的选择理由

- **当前行为**：
  - ✅ **确实影响**：`state.risk_progress` 通过 `min(progress)` 过滤风险池，改变下一轮的风险方向候选集；
  - ✅ **可审计的理由**：`SelectionReceipt` 含 `reason_codes`（`v2_selection.py:27`）、`RiskPoolSelection.reasons`（`v2_risk_pool_scheduler.py:34,148-152`）、`GenerationAllocation.reason_codes` 与 `score_components`（`v2_campaign_loop.py:408-411`），且全部带内容摘要；
  - ⚠️ **局限**：影响通道**只有**风险方向一项；`NextGenerationFeedback` 对象的内容（行为缺口、联合缺口、饱和信号、置信度）不参与选择或变异。
- **证据等级**：`E3`
- **状态**：`PARTIAL`
- **判定理由**："影响后续选择**或**变异"是**析取**，当前满足；"可审计的选择理由"满足。但 SPEC 的意图是覆盖率引导，而实际影响的只有标量进度，故判 `PARTIAL` 而非 `ALIGNED`。
- **影响与风险**：高，与 `G-03`、`FR-FUZZ-01`、`FR-EXP-02` 同根因。
- **需用户决定**：→ `discussion-items.md` 第 2 项。

#### `AC-05` 随机模式不消费跨 Episode 覆盖反馈，且与引导模式满足公平性约束

- **当前行为**：**违反**（同 `FR-EXP-01` 与 `FR-EXP-03`）。
- **证据等级**：`E3`
- **状态**：`CONFLICTING`
- **判定理由**：两半均不成立。第一半由 `risk_progress` 的无条件生效途径违反；第二半由候选空间（`F1`/`F2`/`F4`）与质量门控（`F5`-`F8`）的不对称违反。
- **影响与风险**：**最高**，同 `G-04`。
- **需用户决定**：→ `discussion-items.md` 第 3 项。

#### `AC-06` 能按冻结协议重复运行配对实验，并分别报告风险、行为、联合覆盖及效率、失败和不确定性；只有证据支持时才宣称引导模式更优

- **当前行为**：
  - 可重复运行单 Campaign（`--campaign-seed` 存在但默认 0，无多种子编排）；
  - 报告的 7 类指标见 `FR-EXP-05`（0 类完整、4 类部分、3 类缺失）；
  - **联合覆盖不存在**（`G-02`）；
  - 冻结协议对象与配对编排**均不存在**；
  - ✅ **纪律面**：未发现任何文档宣称引导模式更优（`grep` README/HANDOFF/LOG/docs 无优越性声明）。
- **证据等级**：`E3`
- **状态**：`PARTIAL`
- **判定理由**：能力层缺失，但"只有证据支持时才宣称更优"这一**纪律要求被遵守**，所以不判 `MISSING`。
- **影响与风险**：高。
- **需用户决定**：→ `discussion-items.md` 第 7 项。

#### `AC-07` Episode 的关键事实、状态和指令序列被完整保存；代表性 Finding 可以按声明的重放层级复现或解释

- **当前行为**：保存侧同 `FR-REP-02`（`ALIGNED`）；"按声明的重放层级复现或解释"侧同 `FR-REP-03`/`FR-REP-04`（层级未显式定义，复现未验证）。
- **证据等级**：`E3`
- **状态**：`PARTIAL`
- **判定理由**：前半满足，后半缺少"声明的重放层级"这一锚点且未验证。
- **影响与风险**：中高。
- **需用户决定**：→ `discussion-items.md` 第 5 项。

#### `AC-08` 中断、失败结算、预算终止和恢复路径有测试证据，不重复结算已完成 Episode

- **当前行为**：见 `FR-OPS-03`（`ALIGNED`）。相关测试：`test_exploratory_campaign.py` 的恢复与扩展用例、`test_office_v2_fuzzer_work.py::test_ambiguous_and_sealed_work_never_auto_retry`，均在本次运行中通过（`E2`）。
- **证据等级**：`E2`
- **状态**：`ALIGNED`
- **判定理由**：要求的是"有测试证据"，且这些测试的断言（收据相等性、密封工作不重试、暂停可恢复）**能够直接区分该行为是否成立**，符合审计 Spec §7 对 `E2` 的判定条件。
- **影响与风险**：低。限制：这些测试不使用真实 Docker（`tests/integration` 亦然），因此"真实容器中断"场景未被覆盖。
- **需用户决定**：无。

#### `AC-09` 所有安全结论可追溯到确定性事实或明确标识的语义判断，不由 Controller 伪造证据

- **当前行为**：
  - ✅ 覆盖结论要求显式 submit 终止（`v2_input.py:111-177`）；
  - ✅ Judge 判定有独立标识（`TargetPreservationStatus`）与证据目录（`--data-root/target-reviews`）；
  - ⚠️ 成功闭包中的 `submitted`/`termination_reason`/`cleanup_confirmed` 由调用方声明而非证据推导（`v2_real_runtime.py:673-675`），同 `SEC-05`；
  - ⚠️ Oracle 结论在容器内产生，宿主机无独立复核。
- **证据等级**：`E3`
- **状态**：`PARTIAL`
- **判定理由**：可追溯性成立（每条结论都有摘要与标识），但有三处事实由声明而非推导产生。
- **影响与风险**：中高。
- **需用户决定**：→ `discussion-items.md` 第 6、9 项。

#### `AC-10` 裁判置信度加固只有在黄金集、主动学习、漂移监控、回归和人工升级机制全部通过独立验收后，才能标记完成

- **当前行为**：能力未实现（同 `G-06`/`FR-JDG-03`）。**关键正面证据**：未发现任何文档宣称 Judge 已校准或置信度加固已完成——`LOG.md:1488` 将其列为后续阶段必须补充项，`LOG.md:1500` 明确"不实现通用分数、置信度"。`docs/SPEC.md` §9.2 与 §12 也自述未完成。
- **证据等级**：`E3`（无实现）/ `E4`（文档如实声明未完成）
- **状态**：`MISSING`
- **判定理由**：本条的**实质要求**是"只有通过独立验收才能标记完成"。由于能力不存在、且没有任何文档把它标记为完成，该纪律未被违反。但它是一条**验收门**要求，而非"实现某功能"的要求——在没有可验收对象的情况下，判 `MISSING`（对应 `G-06` 的实现缺失）比判 `ALIGNED` 更诚实，因为 `ALIGNED` 会被误读为"置信度加固已就绪"。
- **影响与风险**：中。风险不在当前，而在**未来**：一旦有人引入 Judge 置信度字段，需要同时建立验收机制。
- **需用户决定**：→ `discussion-items.md` 第 6 项。

---

## 5. 交叉冲突清单（`AUD-AC-08`）

审计 Spec 要求"代码、测试、运行证据与历史文档之间的冲突被明确列出"。

### 5.1 代码 ↔ 产品 SPEC

| # | 冲突 | SPEC 侧 | 代码侧 | 状态 |
|---|---|---|---|---|
| C1 | `coverage_gain` 布尔 vs 四类结果必须保留 | §5.3、`AC-03` | `v2_real_runtime.py:1158-1170` | `CONFLICTING` |
| C2 | 随机基线不得读跨 Episode 反馈 | `FR-EXP-01`、`AC-05` | `v2_campaign_loop.py:373` + `v2_real_runtime.py:751` | `CONFLICTING` |
| C3 | 唯一原则性差异 vs 16 处分叉 | `FR-EXP-03` | `strategy-comparison.md` §2 | `CONFLICTING` |
| C4 | 联合覆盖必须建立 | §5.3、`G-02` | `new_behavior_risk_links` 零消费者 | `PARTIAL` |

### 5.2 命名 ↔ 实际语义（`AGENTS.md` §4 点名要求核查）

| 名称 | 表面含义 | 实际语义 | 证据 |
|---|---|---|---|
| `coverage_gain` | 覆盖增益 | "任一维度任一新增"的布尔或 | `v2_real_runtime.py:1158-1170` |
| `independent_uniform_selection` | 独立均匀选择 | 该**函数**的 RNG 输入确实不含历史；但**整个选择流程**通过候选集构成引入了历史 | `v2_selection.py:106` docstring vs `v2_campaign_loop.py:373` |
| `WeightedSelectionOption.weight` / `total_weight` | 加权选择 | 唯一策略强制单位权重，加权能力不存在 | `v2_selection.py:20-21,72-75` |
| `new_behavior_risk_links` | 行为—风险联合链接 | 被计算并持久化，但无任何消费者 | `grep` 零命中 |
| `risk_progress` | 风险进度 | 4 级标量，无法表达 SPEC §5.2 的暴露阶段/里程碑/载体/上下文 | `v2_seed_pools.py:28-41` |
| `build_independent_baseline_brief` | 随机基线专用提示 | 定义并导出，**无调用点** | `v2_feedback.py:174,405` |

### 5.3 文档 ↔ 代码

| # | 冲突 | 文档侧 | 代码侧 |
|---|---|---|---|
| C5 | `config/risk-taxonomy.yaml` 是否为生效配置 | 文件存在于 `config/` | 全仓无引用；风险分类法标识硬编码于 `coverage/v2_contracts.py:72` |
| C6 | 裁判置信度加固是否已实施 | `LOG.md:1488,1500` 声明未实施（**一致**） | 无实现（**一致**）— 此项**无冲突**，记录以证明已核查 |

### 5.4 测试 ↔ 实际行为（测试是否具有判定力）

| # | 缺口 | 说明 |
|---|---|---|
| T1 | 无测试断言随机模式的选择不受历史影响 | `tests/` 中引用 `RANDOM_INDEPENDENT` 的仅 1 文件，断言仅覆盖 CLI 解析与策略持久化 |
| T2 | 无测试覆盖随机基线守卫 | `"independent-random-baseline-no-promotion"` 等 4 个守卫字符串在 `tests/` 中零命中 |
| T3 | 无测试断言四类新颖性区分 | `grep` 无相关断言 |
| T4 | `tests/integration/` 不使用真实 Docker | `grep docker` 无命中；29 项通过不能证明容器行为 |
| T5 | `tests/unit/__pycache__/` 残留已删除测试的 `.pyc` | `test_office_v2_random_independent_strategy.*.pyc` 存在但无对应 `.py`（对 `tests/` 做 `grep` 时须排除二进制，否则产生误导） |

### 5.5 工具链基线

| # | 事实 | 说明 |
|---|---|---|
| C7 | `AGENTS.md` §6 要求的 ruff 基线**不通过** | 2 个错误：`test_office_expression_mutation.py:22` `F401`、`test_office_v2_public_cli_entry.py:1` `I001`。均在测试文件中 |

### 5.6 反向检查：有代码能力但没有产品要求（`R1`–`R4`）

上述 §3–§5 都是**单向**映射（SPEC 要求 → 代码）。本节做反向检查：**关键代码能力是否没有对应的产品 SPEC 要求**。这属于审计 Spec `AUD-AC-06` 的"避免只做单向映射"要求。

方法：把 `git ls-files` 的代码面按能力归类，对每类在 `docs/SPEC.md` 中做关键词检索（区分大小写不敏感），再人工确认不是同义表述。

| # | 代码能力 | 代码面 | SPEC 检索结果 | 判定 |
|---|---|---|---|---|
| **R1** | **双 Agent 运行时**：自研 LangGraph ReAct 与 DeepSeek Harness 可选 | `agent_image/` 53 个受跟踪文件 + `agent_variants/deepseek_harness/` 14 个；适配器 `agent_image/app/adapter/deepseek_harness_adapter.py`、`factory.py` | `langgraph` = **0**、`deepseek` = **0**、`TRACE_G_AGENT_RUNTIME` = **0**、`运行时` = **0** | **无产品要求** |
| **R2** | **Fork / Suffix 重放语义**（`ForkSuffixMode`：`live_and_record` / `strict_with_replacements`） | `src/sandbox/replay/models.py`、由 `src/sandbox/cli.py:113-114,253` 驱动 | `fork` = **0**、`suffix` = **0** | **无产品要求**，且**不在 V2 主链**（由旧一代 `sandbox/cli.py` 驱动） |
| **R3** | **v1 遗留实现**：非 `v2_*` 的 fuzzer 与 coverage 模块（`energy.py`、`queue.py`、`circuit_breaker.py`、`soak.py`、`coverage/heatmap.py` 等） | `fuzzer/` 44 个文件中 12 个非 `v2_*`；`coverage/` 中 15 个非 `v2_*` | SPEC 描述的是 V2 时代的对象模型（§4 核心对象），无旧一代对应要求 | **无产品要求** |
| **R4** | **`coverage_ui/` 目录** | 空目录（`find coverage_ui -type f` 无结果） | `coverage_ui` = **0** | **不是能力**，是空的目录残留 |

**已排除的候选（有 SPEC 要求，非反向缺口）**：

- **内容寻址摘要**（每个契约模型带 `*_digest`）：`FR-OPS-05` 明确要求"内容摘要用于发现轨迹、快照、Replay 或 Artifact 的损坏和截断"。✅ 有要求。
- **`monetary_microunits` 成本维度**：`FR-OPS-04` 要求记录"成本"。✅ 有要求（微单位是计量选择，不是额外能力）。
- **SQLite / WAL / SAVEPOINT 事务模型**：§10 要求持久化、失败分类与恢复。✅ 有要求（存储引擎是实现选择）。
- **`sha256-modulo-v1` 选择机制**：`FR-REP-03` 与 §7.3 要求可重放与公平比较。✅ 有要求（具体算法是可替换实现，代码自身也在 docstring 中标注为可替换）。
- **`--ollama-mode`**：`FR-FUZZ-03` 要求记录"模型/Provider"身份。✅ 有要求（该开关本身是运行配置，见 §4 `SEC-02` 相关讨论）。

**这意味着什么**：

- **R1 是最大的一项**：两个完整的 Agent 运行时（合计 67 个受跟踪文件）在 `AGENTS.md` §4 的意义上是"产品概念之外的能力"。它**可能是有意的**（例如为了验证"Agent 框架无关性"，或为后续阶段准备），但 SPEC `§4` 的核心对象列表和 `§3` 的一致性假设都**没有**把它纳入。**本审计不判断它应否保留**，只记录"规格层面没有它的位置"。
- **R2 说明一件事**：仓库整体的重放能力**大于 V2 主链所用的部分**。SPEC §8 的四层级重放要求针对的是主链；旧一代的 fork/suffix 语义既未被要求，也未被主链继承。
- **R3 与 R4 是结构噪声**：不影响产品语义，但提高复核成本（与 `strategy-comparison.md`、`discussion-items.md` 第 9 项相关）。

**证据等级**：`R1`–`R4` 的关键词检索为 `E1`（本次实际执行的命令），归类判断为 `E3`（静态代码）。

---

## 6. 无法验证项汇总

按审计 Spec §12，"局部路径被阻塞不代表整个审计失败"，但必须标为 `UNVERIFIED` 并说明原因。

| 项 | 原因 | 影响的结论 |
|---|---|---|
| Docker 容器行为、副作用隔离 | TASK 禁止运行 Docker | `FR-REP-01`、`AC-01`、`SEC-04` |
| 真实模型决策与两个 Agent 运行时 | TASK 禁止调用真实模型 | `G-01`、`AC-01` |
| 真实 Oracle 计算 | 需容器内执行 | `FR-JDG-01` |
| Judge 实际判定质量 | 需 Ollama + 真实模型 | `FR-JDG-02` |
| 规划中的端到端环形 | TASK 禁止真实 Campaign | `AC-01`、`G-05` |
| 两种模式的实际差异幅度 | 同上 | `strategy-comparison.md` §4.3 |
| 远程服务器归档 | TASK 禁止连接 | 未作为任何证据 |
| 用户未跟踪文件 `.pytest-tmp/`、`harness-node-modules.tar.gz` | 未授权读取 | 未作为任何证据 |
| `tests/design/` 2 个文件 | 未运行 | 未作断言 |

---

## 7. 结论的强度

本文档给出 5 条 `CONFLICTING`（`G-04`、`FR-EXP-01`、`FR-EXP-03`、`AC-03`、`AC-05`）、4 条 `MISSING`（`G-06`、`FR-FUZZ-04`、`FR-JDG-03`、`AC-10`）、2 条 `UNVERIFIED`（`FR-REP-01`、`AC-01`）、24 条 `PARTIAL`、8 条 `ALIGNED`。

需要明确区分两类结论的强度：

- **`CONFLICTING` 与 `MISSING` 结论有确定的代码证据**（`E3`）。例如 `FR-EXP-01` 的违反路径经四步逐行核对，`grep` 穷举确认 `progress_states` 无策略分支。这些结论不依赖运行。
- **`UNVERIFIED` 与部分 `PARTIAL` 结论受证据边界限制**。特别是 `AC-01`（完整闭环能否执行）未验证，而它是所有 `E3` 结论的隐含前提。

全部 8 条 `ALIGNED` 结论（`G-01`、`FR-FUZZ-02`、`FR-REP-02`、`FR-OPS-03`、`FR-OPS-05`、`SEC-01`、`AC-02`、`AC-08`）均有 `E2` 或 `E3` 支持，**未依赖 `E4`**，符合审计 Spec §8 对 `ALIGNED` 的限制。

所有需要产品决定的问题集中在 `discussion-items.md`，与本文件的**事实性发现分开记录**（`AUD-AC-09`）。
