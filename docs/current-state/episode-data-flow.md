# Episode 数据流与状态模型

> **2026-09-19 状态更新**：真实配对运行已执行（smoke 两臂各 1 Episode + 正式 30×2 两轮），结果与限制见 `docs/tasks/20260919-lightweight-comparison-run-record.md` §8。本文件下列“未运行模型／未运行 Docker／未运行真实 Campaign”等表述描述其编写基线 `a15b127` 时的状态，保留为历史文本，不代表当前状态。

- 对应 Spec：`SPEC-AUDIT-20260917`，目标 `AUD-G-02`、`AUD-G-04`、`AUD-G-05`、`AUD-REQ-03`、`AUD-REQ-05`、`AUD-REQ-10`、`AUD-AC-03`、`AUD-AC-05`
- 交付物序号：2 / 5（数据流与状态模型）
- 基线：`main @ a15b1273af1b6a1d3ec98eea50ba18aa3df823e7`
- 事实性文档；不含修复方案

---

## 0. 阅读约定

本文追踪 `SPEC-AUDIT-20260917` §5 规定的主链：

```text
CLI 输入 -> Campaign 配置/策略/预算 -> 根种子/父样本/候选选择 -> Mutation Plan 与语义变异
-> 候选校验 -> Docker 中的 Agent Episode -> 模型决策/工具调用/工具结果/状态变化
-> Trace/Replay/Artifact -> Oracle 与 Finding -> 行为/风险/联合覆盖
-> SQLite 结算/预算/恢复 -> 下一 Episode 的覆盖反馈 -> Campaign 报告与实验比较
```

每一节按 Spec 要求记录：**输入对象及来源 / 负责模块 / 状态如何改变 / 输出与副作用 / 持久化位置 / 失败与恢复 / 下游消费者 / 证据**。

术语首次出现时用"输入—输出—失败"解释，避免只给类名。

**证据等级**沿用 Spec §7：`E1` 运行 / `E2` 有判定力的测试 / `E3` 静态代码 / `E4` 文档声明 / `E0` 无。

---

## 1. 主链入口：CLI

| 项 | 内容 |
|---|---|
| **输入** | 命令行参数：`--db`（SQLite 路径）、`--campaign-id`、`--agent-image`、`--mutator-image`、`--strategy`、`--campaign-seed`、`--episodes`、`--data-root` 等（`v2_cli.py:52-92`） |
| **负责模块** | [src/sandbox/fuzzer/v2_cli.py:104-164](src/sandbox/fuzzer/v2_cli.py#L104-L164) `main`；装配在 `_run_exploratory` [v2_cli.py:167-307](src/sandbox/fuzzer/v2_cli.py#L167-L307) |
| **状态改变** | 打开 `V2CampaignStore(args.db)` 上下文；`exploratory-resume` 额外要求 `store.campaign_exists(campaign_id)`，否则 `SystemExit` |
| **输出/副作用** | ① 创建/打开 SQLite 数据库文件；② `args.data_root` 下创建 `artifacts/`、`trajectories/`、`replays/`、`failures/`；③ 成功时写 `data_root/last-run.json`（原子写：先写 `.json.tmp` 再 `os.replace`）；④ stdout 打印 JSON payload |
| **持久化** | `<db>`、`<data_root>/last-run.json` |
| **失败/恢复** | 异常时把 traceback 写入 `data_root/failures/controller-error.txt` 后重新抛出（`v2_cli.py:122-127`）。**注意：这是就地写入，不做原子替换** |
| **下游** | `run_or_resume_exploratory_campaign` |
| **证据** | `E3`。`tests/unit` 中有 CLI 层测试；未在真实 Docker 下运行（TASK 禁止） |

**退出码**（`v2_cli.py:158-164`）：`exploratory-*` 仅在 `target_reached` 为真且 `completion_status is None` 时返回 `0`。

---

## 2. Bootstrap：16 个固定根种子

| 项 | 内容 |
|---|---|
| **输入** | `model_name`、`episode_limit`（`v2_cli.py:193-196`） |
| **负责模块** | [src/sandbox/fuzzer/v2_bootstrap.py](src/sandbox/fuzzer/v2_bootstrap.py) `build_exploratory_bootstrap`；种子目录来自 [v2_seed_pools.py](src/sandbox/fuzzer/v2_seed_pools.py) `build_initial_seed_catalog` |
| **数据结构** | `RiskType` 是**固定 4 值枚举**：`wrong_modification_propagation`、`sensitive_information_disclosure`、`unauthorized_operation_permission_expansion`、`destructive_operation`（`v2_seed_pools.py:19-25`）。`RiskProgressLevel` 是**标量 4 级**：`NOT_SELECTED=0 → SELECTED=1 → ATTEMPTED=2 → REALIZED=3`（`v2_seed_pools.py:28-41`），`advance` 强制逐级 +1。`FrozenSeed` = `seed_id + risk_type + attack_target + base_text + semantic_digest`（`v2_seed_pools.py:44-71`） |
| **状态改变** | 构造 `V2Corpus`，含 4 类风险 × 4 个根种子的执行支持 |
| **输出** | `ExploratoryCampaignBootstrap` |
| **持久化** | 初始 Catalog 随 `create_campaign` 写入 SQLite |
| **失败/恢复** | 无（纯构造） |
| **证据** | `E2`（`v2_seed_pools` 的摘要校验有单元测试）；`E3`（bootstrap 装配） |

**关键事实**：根种子文本在仓库中是固定的，`RiskType` 只有 4 个值。产品 SPEC §5.2 要求风险覆盖能表达"风险分类与具体风险目标、前置条件、身份/授权上下文、风险载体、暴露阶段、里程碑、意外风险、已观察副作用"；当前 V2 主链的风险维度只有**一个 4 值枚举 + 一个 4 级标量**。差距见 `spec-compliance.md`（`G-02`、`FR-EXP-02`）。

---

## 3. 选择：父样本与风险池

| 项 | 内容 |
|---|---|
| **输入** | Campaign 状态快照 `V2CampaignStateSnapshot`（含 `seed_catalog`、`corpus`、`coverage`、`budget`、`lifecycle`） |
| **负责模块** | [src/sandbox/fuzzer/v2_orchestrator.py](src/sandbox/fuzzer/v2_orchestrator.py) `decide_next_generation`；风险池选择 [v2_risk_pool_scheduler.py](src/sandbox/fuzzer/v2_risk_pool_scheduler.py) `select_risk_pool_seed`；随机选择 [v2_selection.py](src/sandbox/fuzzer/v2_selection.py) `independent_uniform_selection` |
| **状态改变** | 产出 `GenerationDecision`（含 `generation_allocation`：风险类型、父种子、执行记录、算子分配）。决策本身写入数据库 |
| **输出** | `GenerationDecision` |
| **持久化** | `V2CampaignStore.put_generation_decision`（[v2_campaign_store.py:1698](src/sandbox/fuzzer/v2_campaign_store.py#L1698)）；`put_allocation` |
| **失败/恢复** | `plan-next` 子命令会复用已存在的决策：若 `previous.generation_index == counters.generation_index` 且 `previous.input_state_digest == state.state_digest`，直接返回旧决策（`v2_cli.py:140-144`）——这保证同一输入状态不会被重复决策 |
| **下游** | `_RealGenerationDriver.advance` |
| **证据** | `E2`/`E3`，详见 `strategy-comparison.md` |

**选择算法（本审计的核心发现之一）**：`v2_selection.py:1-166` 给出的全部随机性来源是

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

即**在规范排序的候选列表上做一个模运算取下标**。同名验证器强制 `RANDOM_UNIFORM` 下所有选项单位权重（`v2_selection.py:72-75`）。

风险池侧（`v2_risk_pool_scheduler.py`）：`select_risk_pool_seed` 先选 **`RiskProgressLevel` 最低**的风险类型，再在该池内**均匀**挑一个父种子。

**因此，真正从历史进入下一轮选择的覆盖信息，只有"每个风险类型的 4 级标量进度"这一个量。** 行为覆盖、联合覆盖、Finding 价值、重复降权都没有进入选择。详见 `strategy-comparison.md` §4 与 `spec-compliance.md`（`G-03`、`FR-FUZZ-05`、`AC-04`）。

---

## 4. Mutation Plan 与语义变异

| 项 | 内容 |
|---|---|
| **输入** | `GenerationDecision`、父 `FrozenSeed`、支持执行记录 `execution`、算子分配 `operator_decision.allocation`、`provider_id`、`campaign_strategy`（`v2_real_runtime.py:234-241`） |
| **负责模块** | `build_semantic_mutation_plan`（在 `src/sandbox/mutation/`）；算子选择 `select_formal_operator`（`v2_real_runtime.py:223-233`） |
| **状态改变** | 产出 `SemanticMutationPlan`（含 `payload_slots`、`budget`、`plan_digest`）；随后 `reserve_mutation_budget` 从预算中**预留** token 与成本 |
| **输出** | `plan`、`MutationBudgetReservation` |
| **持久化** | `put_allocation`、`reserve_mutation`（[v2_campaign_store.py:1414](src/sandbox/fuzzer/v2_campaign_store.py#L1414)）；`put_mutation_preparation`（[:1297](src/sandbox/fuzzer/v2_campaign_store.py#L1297)）；`put_execution_handoff`（[:1530](src/sandbox/fuzzer/v2_campaign_store.py#L1530)） |
| **失败/恢复** | ① 无兼容算子 → `RuntimeError("no compatible semantic operator for generation")`（`v2_real_runtime.py:233`）；② 已持久化的预留与决策不符 → `ValueError("persisted mutation reservation differs from decision")`（[:270](src/sandbox/fuzzer/v2_real_runtime.py#L270)）——这是幂等恢复的保护 |
| **下游** | `prepare_candidate` → `MaterializedCandidate` |
| **证据** | `E2`（`tests/unit/test_office_expression_mutation.py` 等）；`E3` |

### 4.1 变异 Provider 的两种模式

`v2_cli.py:239-251` 构造 `DockerOllamaV2MutationProvider`，`network_mode` 与 `ollama_endpoint` 依据 `--ollama-mode`：

- `host`：容器 `network_mode="host"`，连宿主机 Ollama → **`target_judge` 可用**；
- `embedded`：容器 `network_mode="none"`，用容器内 Ollama → **`target_judge=None`**（`v2_cli.py:287-291`）。

### 4.2 目标保持校验

| 项 | 内容 |
|---|---|
| **负责模块** | `_assess_target`（[v2_real_runtime.py:836](src/sandbox/fuzzer/v2_real_runtime.py#L836)）；`OllamaTargetPreservationJudge`（`v2_target_judge.py`）；Oracle 侧 `v2_target_oracle.py`、`v2_target_preservation.py` |
| **状态改变** | 产出 `target_preservation` 评估结果，作为 `promote_coverage_artifact` 的入参 |
| **失败/恢复** | `target_preservation is None` 时在结算阶段才补算（`v2_real_runtime.py:683-684`） |

**关键事实**：产品 SPEC `FR-FUZZ-04` 要求**同时支持目标保持与目标切换两种显式操作**，并且"未声明的目标漂移属于无效变异"。当前代码**只有目标保持**一路：`_assess_target` 判定是否保持了原目标，不存在"按已注册合法目标显式切换"的代码路径。见 `discussion-items.md` 第 4 项。

---

## 5. 候选校验

| 项 | 内容 |
|---|---|
| **输入** | `SemanticMutationPlan`、`brief`（`build_minimal_fact_brief`）、`parent_text_by_slot`、Provider |
| **负责模块** | `prepare_candidate`（`v2_real_runtime.py:325-340`）；字段注册表 `build_v2_mutation_field_registry()` |
| **状态改变** | 产出 `preparation.materialized_candidate`（`MaterializedCandidate`）与 `preparation.parsed_candidate`；候选被重新物化为具体 Scenario Case（`resolve_case_id`，`v2_real_runtime.py:290-298`） |
| **一致性断言** | 结算前强制：① 随机模式下 `candidate.seed_id == seed.seed_id`，否则 `ValueError("random Episode candidate must retain the root seed identity")`（[:645-649](src/sandbox/fuzzer/v2_real_runtime.py#L645-L649)）；② `candidate.scenario_case_id == episode.scenario_case.case_id`，否则 `ValueError("prepared candidate and executed scenario identity differ")`（[:650-651](src/sandbox/fuzzer/v2_real_runtime.py#L650-L651)） |
| **持久化** | `put_mutation_preparation`、`settle_preparation_cost`（[:1465](src/sandbox/fuzzer/v2_campaign_store.py#L1465)） |
| **失败/恢复** | 已存在 preparation 时直接复用，不重复调用 Provider——幂等恢复的关键（`v2_real_runtime.py:315-318`） |
| **下游** | Docker Episode 执行 |
| **证据** | `E2`（候选校验相关单元测试）；`E3` |

### 5.1 候选空间的一个不对称（公平性相关）

`v2_real_runtime.py:320-324`：

```python
candidate_history = (
    ()
    if self.strategy is CampaignStrategy.RANDOM_INDEPENDENT
    else self.store.load_candidate_history(campaign_id)
)
```

随机模式传入**空的** `candidate_history`，引导模式传入真实历史。同一文件 `v2_campaign_loop.py:377` 另有 `include_derived=not independent`。

**这两处是"随机模式不得读取跨 Episode 反馈"的正确实现**（`FR-EXP-01`），但它们同时意味着两种模式的**候选去重/派生空间不同**。是否构成"候选空间不一致"的公平性问题，见 `strategy-comparison.md` §5 与 `discussion-items.md` 第 3 项。

---

## 6. Docker 中的 Agent Episode

| 项 | 内容 |
|---|---|
| **输入** | 已 materialize 的候选、`model_name`、`model_inference`、`max_steps`、`max_tool_calls`、`timeout_seconds`（`v2_cli.py:252-265`） |
| **负责模块** | [src/sandbox/fuzzer/v2_real_episode.py](src/sandbox/fuzzer/v2_real_episode.py) `DockerOfficeV2EpisodeRunner.execute`（[:166](src/sandbox/fuzzer/v2_real_episode.py#L166)）；调度 `DockerSandboxScheduler`；通信 `RuntimeClient`；重放引擎 `ReplayEngine` |
| **状态改变** | 容器内 Agent 自主进行模型决策与工具调用；产出 `OfficeV2EpisodeResult`，含 `manifest`、`agent_tokens`、`elapsed_ms`、`oracle`、`scenario_case`、`coverage_input` |
| **输出/副作用** | ① Docker 容器创建与销毁；② 轨迹写入 `data_root/trajectories/`；③ Artifact 写入 `data_root/artifacts/`；④ Replay Manifest 写入 `data_root/replays/`；⑤ 失败诊断写入 `data_root/failures/` |
| **持久化** | 文件系统（轨迹/Artifact/Manifest）+ SQLite（`save_pending_episode`，[:941](src/sandbox/fuzzer/v2_campaign_store.py#L941)） |
| **失败/恢复** | `record_failure`（[:120](src/sandbox/fuzzer/v2_real_episode.py#L120)）、`cleanup_interrupted`（[:152](src/sandbox/fuzzer/v2_real_episode.py#L152)）、`has_saved_recording`（[:250](src/sandbox/fuzzer/v2_real_episode.py#L250)）——保存过的录制可避免重复执行 |
| **证据** | `E3`（未运行 Docker）。`E2` 仅覆盖非 Docker 的下游逻辑 |

**Agent 侧的自主性**：Agent 运行时由 `TRACE_G_AGENT_RUNTIME` 选择（`v2_cli.py:210`），两个候选实现是自研 LangGraph ReAct（`agent_image/app/`）与 DeepSeek Harness（`agent_variants/deepseek_harness/`）。二者都由持久化在容器内的运行时驱动，宿主机侧不可注入单步决策。

---

## 7. Trace / Replay / Artifact

| 项 | 内容 |
|---|---|
| **负责模块** | [src/sandbox/replay/](src/sandbox/replay/)（11 文件）：`ArtifactStore`、`ManifestStore`、`ReplayEngine`、`digests.py` |
| **内容摘要** | 全仓统一使用 `sha256_digest(canonical_json_bytes(value))`；每个 Pydantic 契约携带 `*_digest` 字段并在校验时重算（`OfficeV2Contract`）。这是 `FR-OPS-05`（内容摘要用于发现损坏/截断）的实现 |
| **持久化** | `data_root/artifacts/`、`data_root/replays/` |
| **重放层级** | 代码中存在"工具重放（`EXECUTE_AND_VERIFY`）"与"严格决策重放"，以及 `ReplayEngine` 的重新执行路径。四个层级是否被显式区分与声明，见 `spec-compliance.md`（`FR-REP-03`） |
| **证据** | `E3`；`tests/integration` 中 6 个文件涉及执行引擎（本次 29 项通过，但**未使用真实 Docker**，见 `project-map.md` §1.3） |

---

## 8. Oracle 与 Finding

| 项 | 内容 |
|---|---|
| **输入** | 已完成的 Episode：轨迹、工具结果、状态变化 |
| **负责模块** | [src/sandbox/scenarios/office_v2/oracle.py](src/sandbox/scenarios/office_v2/oracle.py)、`security_oracle.py`、`utility_oracle.py`、`oracle_evidence.py`、`oracle_trace.py`、`oracle_models.py` |
| **状态改变** | 产出 `oracle_result`，含 `trace_digest` 与结构化事实 |
| **输出** | `OfficeV2RecordedOracleArtifact`（`v2_real_episode.py:59-78`），带 `identities_and_digest_match` 自校验 |
| **失败/恢复** | 无独立重算路径 |
| **证据** | `E2`：`tests/unit/test_office_v2_oracle_evidence.py`、`test_office_v2_oracle_models.py`、`test_office_v2_oracle_rebuild.py`、`test_office_v2_oracle_trace.py`、`test_office_v2_scenario_oracle.py`、`test_office_v2_clean_oracle.py` |

**关键事实**：Oracle 在容器内环境完成计算，产物作为 Artifact 回传。宿主机侧**没有对 Oracle 结论的独立重算或交叉验证**。这意味着 Oracle 的正确性依赖容器内实现的正确性（`E3`）。这属于 Oracle/Judge 边界问题，见 `spec-compliance.md`（`FR-JDG-01`）与 `discussion-items.md` 第 6 项。

**Finding**：由 [src/sandbox/fuzzer/v2_feedback.py](src/sandbox/fuzzer/v2_feedback.py) 的 `build_finding` / `build_next_generation_feedback` 构造；`FeedbackGapKind` 有 6 个取值。

---

## 9. 覆盖：行为、风险、联合

这是本审计最集中的差距区域，逐个说明当前真实语义。

### 9.1 入口与强制前置

| 项 | 内容 |
|---|---|
| **输入** | `episode.coverage_input`（类型为 `V2BehaviorSourceFacts` 所在的覆盖输入契约） |
| **负责模块** | [src/sandbox/coverage/v2_input.py](src/sandbox/coverage/v2_input.py)（750 行，V2 覆盖入口）；`build_v2_coverage_artifact(episode.coverage_input)`（`v2_real_runtime.py:677`） |
| **强制校验** | `v2_input.py:111-177` 的 `V2BehaviorSourceFacts` 验证器：`if not self.submitted or self.termination_reason != "submit": raise ValueError("coverage requires an explicit submit termination")`。`_behavior_facts`（`v2_input.py:285-302`）从 `bundle.termination.reason` / `.submitted` 取值 |
| **证据** | `E3`（静态代码）；`E2`（`tests/unit/test_office_v2_fuzzer_corpus.py` 等） |

**结论**：只有"显式 submit 终止"的 Episode 才能产生覆盖证据。非 submit 结束的 Episode 走 §11 的非 Episode 结算路径。

### 9.2 行为覆盖

- 提取来源：`bundle`（Episode 执行包）中的工具调用、状态变化与终止信息；
- 主要字段：`new_primary_behavior_features`、`new_exposure_stages`、`new_milestone_outcome_bits`、`new_unexpected_violations`（见 §10）；
- 结构定义在 `coverage/v2_behavior.py`、`v2_tool_behavior.py`、`v2_episode_behavior.py`；
- **可从保存证据重算**：特征由内容摘要标识（`sha256`），且 `OfficeV2Contract` 在反序列化时重算摘要并校验。这一点满足 `G-02` 的"稳定标识"要求（`E3`）。

### 9.3 风险覆盖

- 结构定义在 `coverage/v2_risk_catalog.py`（260 行）、`v2_risk_coverage.py`（366 行）；
- 运行时**进度**由 `v2_campaign_state.py` 的 `settle_risk_progress` 推进（0→1→2→3）；
- 分类法标识硬编码：`coverage/v2_contracts.py:72` → `"office-v2-risk-taxonomy-schema-v1"`；组件枚举 `V2CoverageComponent.RISK_TAXONOMY = "risk-taxonomy-schema"`（`v2_contracts.py:55`）；
- **`config/risk-taxonomy.yaml` 未被任何代码引用**（见 `project-map.md` §5.4）。

### 9.4 联合覆盖

联合覆盖的数据结构是 `V2CoverageDelta`（[coverage/v2_episode_coverage.py:193-212](src/sandbox/coverage/v2_episode_coverage.py#L193-L212)），共 11 个"新增项"字段。逐字段统计其**定义处之外的消费者**（`grep` 计数，`E3`）：

| 字段 | 定义外消费者 | 说明 |
|---|---|---|
| `new_primary_behavior_features` | 6 | 行为覆盖主特征 |
| `new_milestone_outcome_bits` | 4 | 风险里程碑结果 |
| `new_unexpected_violations` | 4 | 意外风险 |
| `new_risk_contexts` | 3 | 被 `v2_feedback.py:247,269,312` 消费 |
| `new_exposure_stages` | 2 | 风险暴露阶段 |
| `new_secondary_diversity_features` | 1 | `v2_promotion.py:120` |
| `new_behavior_profile` | 1 | `v2_promotion.py:120` |
| **`new_behavior_risk_links`** | **0** | **行为—风险联合链接（联合覆盖本体）** |
| **`new_risk_facets`** | **0** | 风险侧面 |
| **`new_risk_objectives`** | **0** | 风险目标 |
| **`new_primary_scheduling_families`** | **0** | 主调度族 |

**关键事实**：11 个字段中有 4 个在 `src/` 内**没有任何消费者**，其中 `new_behavior_risk_links` 正是 SPEC §5.3 所定义的"联合单元"（行为路径 × 风险 × 阶段 × 上下文 × 结果）的载体。`v2_promotion.py` 的 `classify_v2_promotion` 读取了 6 个字段（[:59-61](src/sandbox/fuzzer/v2_promotion.py#L59-L61)、[:93](src/sandbox/fuzzer/v2_promotion.py#L93)、[:120](src/sandbox/fuzzer/v2_promotion.py#L120)），**但不读取** `new_behavior_risk_links`。

**这是产品 SPEC §5.3 的直接差距**：SPEC 要求"建立由同一 Episode 证据支持的联合单元"，当前代码**计算并持久化了**联合字段，但没有任何决策或报告维度读取它。`new_risk_objectives`（风险目标）同样无消费者，这削弱了"风险目标"这一 SPEC §5.2 明确要求的维度。

### 9.5 覆盖增益被压成单个布尔值

[src/sandbox/fuzzer/v2_real_runtime.py:1158-1170](src/sandbox/fuzzer/v2_real_runtime.py#L1158-L1170)：

```python
def _coverage_gain(delta, facts) -> bool:
    return bool(
        delta.new_primary_behavior_features
        or set(delta.new_exposure_stages) & evidence_backed_exposure
        or delta.new_milestone_outcome_bits
        or delta.new_unexpected_violations
    )
```

**这是一个布尔或运算。** 它把"仅新行为""仅新风险""新联合关系""无新增"四类结果合并为一个 `True`/`False`，然后把该布尔值传入 `record_valid_episode(..., coverage_gain=...)`（`v2_real_runtime.py:728-731`）。

产品 SPEC §5.3 明文要求：

> 系统必须保留以下四种结果，不能只输出一个 `coverage_gain` 布尔值

`AC-03` 同样要求"能够分别识别'仅新行为''仅新风险''新联合关系'和'无新增'四类结果"。

当前状态：**`coverage_gain` 恰好是 SPEC 点名禁止的那个布尔值**，而四分类在 v2 主链中不存在。请注意区分：

- 联合字段 `new_behavior_risk_links` / `new_risk_facets` **存在**于 `V2CoverageDelta` 结构中（所以"有联合数据"是真的）；
- 但"四类新颖性判定"和"覆盖反馈进入调度"**不存在**（所以"有联合覆盖闭环"是不成立的）。

这正是 `AGENTS.md` §4 警告的情形：**名称不自动等同于产品概念**。`coverage_gain` 这个名字听起来像"覆盖增益"，实际语义是"本 Episode 是否带来任一维度的任一新增"。详见 `discussion-items.md` 第 1 项。

---

## 10. 结算：一次成功 Episode 的完整顺序

以下按 [v2_real_runtime.py:622-760](src/sandbox/fuzzer/v2_real_runtime.py#L622-L760) `_settle_episode` 的实际顺序。

| 序 | 动作 | 位置 | 说明 |
|---|---|---|---|
| 1 | 断言候选身份 | `:645-651` | 随机模式必须保持根种子身份；候选与已执行 Scenario 必须一致 |
| 2 | 生成成功收据 `receipt` | `:652-658` | `_successful_receipt`，含 `manifest_digest`、`agent_tokens`、`elapsed_ms` |
| 3 | 封存尝试 `seal_attempt` | `:659-663` | 已有收据则要求完全相等，否则 `ValueError("Saved Episode differs from its existing success receipt")` |
| 4 | 汇总尝试成本 | `:664-665` | `total_attempt_costs` |
| 5 | 构造 `ExecutionClosure` | `:666-676` | `build_execution_closure_from_oracle(...)` |
| 6 | 构造覆盖 Artifact | `:677` | `build_v2_coverage_artifact` |
| 7 | 评估 Agent 行为 | `:678-682` | `assess_agent_behavior` |
| 8 | 目标保持评估 | `:683-684` | 若为 `None` 则此时补算 |
| 9 | **晋升判定** | `:685-721` | `promote_coverage_artifact(...)` |
| 10 | 计算 `coverage_gain` 布尔 | `:722` | 见 §9.5 |
| 11 | 结算预算 | `:723-727` | `settle_campaign_budget`，把预留替换为实际 |
| 12 | 记录有效 Episode | `:728-731` | `record_valid_episode(..., coverage_gain=...)` |
| 13 | 评估生命周期 | `:732-742` | `evaluate_campaign_lifecycle` |

### 10.1 第 5 步的关键细节：被调用方硬编码的"成功"

`v2_real_runtime.py:673-675`：

```python
submitted=True,
termination_reason="submit",
cleanup_confirmed=True,
```

这三个字段由**调用方直接写死**，不是从 `episode` 读出。需要准确说明其性质：

- `build_execution_closure_from_oracle`（`v2_loop_contracts.py:306-373`）**确实**从真实 Oracle 暴露阶段推导 `observed_payload_refs` 与 `used_payload_refs`（`E3`，我逐行核对过）；
- 但 `submitted` / `termination_reason` / `cleanup_confirmed` 三者**不是**由真实执行结果推导。

**为什么这不是"伪造成功"**：本节第 1 步与第 9.1 节共同保证了只有显式 submit 终止的 Episode 能走到这里——`V2BehaviorSourceFacts` 的验证器会对非 submit 的输入直接 `raise`，而 `_settle_episode` 只在成功路径被调用。所以三个硬编码值与真实事实**一致，但一致性是靠上游路径保证的，不是靠这里读出来的**。

**为什么仍然值得记录**：`SEC-05` 要求"Controller 不得替 Agent 伪造工具调用、授权、状态变化、提交或成功证据"。当前实现中提交与清理这两个事实**没有从 Episode 对象读取**，因此一旦上游条件变化（例如新增一条不经过 `V2BehaviorSourceFacts` 的结算路径），这三个字段会静默变成不实声明，而不会报错。`cleanup_confirmed` 尤其重要：`SEC-04` 要求"清理失败必须成为显式测试结果"。

见 `spec-compliance.md`（`SEC-04`、`SEC-05`）与 `discussion-items.md` 第 6 项、第 9 项。

### 10.2 第 9 步：晋升判定

`v2_campaign_loop.py` 的 `promote_coverage_artifact`：

- **随机模式被强制为 `NO_PROMOTION`**，理由字符串 `"independent-random-baseline-no-promotion"`（`v2_campaign_loop.py:177-183`）。`seed` 与两个 factory 都传 `None`（`v2_real_runtime.py:690`、`702-720`）；
- `include_derived=not independent`（`v2_campaign_loop.py:377`）；
- `record_parent_result` 只在 `COVERAGE_GUIDED` 下调用（`v2_campaign_loop.py:335-343`）；
- `PromotionGateFacts` 传入的多数门控是**硬编码 True**，只有 `canonical_fact_is_new` 是真实计算（`v2_campaign_loop.py:161-176`）。

**结论**：在当前实现下，**只有 `coverage_guided` 模式会产生种子晋升**。这直接影响两种模式的公平比较，见 `strategy-comparison.md` §5。

### 10.3 第 13 步：`SATURATED` 不可达

`v2_real_runtime.py:734-742`：

```python
budget_exhausted=(
    _campaign_budget_exhausted(settled_budget)
    and settled_budget.used_episodes < settled_budget.episode_limit
),
# The requested Episode count is the exploratory stopping target.
# Coverage feedback can guide selection, but must not silently
# shorten a Campaign after a run of low-gain Episodes.
allow_coverage_saturation=False,
```

`allow_coverage_saturation=False` 让覆盖饱和提前终止**永远不会触发**。Campaign 只在两个条件下停：达到请求的 Episode 数，或预算耗尽（且 Episode 数未达上限）。

代码注释明确说明这是**有意为之**。这一设计决定的后果：`FR-FUZZ-05` 关心的"避免少数热门路径垄断预算"与"重复降权"在当前实现中没有终止侧的表达；`FR-EXP-05` 要求的"连续无增益长度"虽然可以在报告里事后统计，但不会影响运行。

---

## 11. 失败、非 Episode 结算与恢复

| 项 | 内容 |
|---|---|
| **失败分类** | `_episode_failure_is_retryable`（[v2_real_runtime.py:1086](src/sandbox/fuzzer/v2_real_runtime.py#L1086)）、`_is_behavior_limit_failure`（[:1096](src/sandbox/fuzzer/v2_real_runtime.py#L1096)）、`_episode_failure_receipt`（[:1116](src/sandbox/fuzzer/v2_real_runtime.py#L1116)） |
| **非 Episode 结算** | `_close_non_episode`（[:867](src/sandbox/fuzzer/v2_real_runtime.py#L867)）；持久化 `commit_non_episode_settlement`（[v2_campaign_store.py:1585](src/sandbox/fuzzer/v2_campaign_store.py#L1585)） |
| **中断工作恢复** | `_recover_interrupted_work`（[:169](src/sandbox/fuzzer/v2_real_runtime.py#L169)）；`resume_incomplete`（[:850](src/sandbox/fuzzer/v2_real_runtime.py#L850)） |
| **存储层恢复** | `V2CampaignStore.recover`（[:1231](src/sandbox/fuzzer/v2_campaign_store.py#L1231)）、`inspect_recovery`（[:1267](src/sandbox/fuzzer/v2_campaign_store.py#L1267)）、`resume_ambiguous_work`（[:523](src/sandbox/fuzzer/v2_campaign_store.py#L523)）、`resume_paused_campaign`（[:600](src/sandbox/fuzzer/v2_campaign_store.py#L600)） |
| **幂等保证** | `_accept_advance`（[v2_runtime.py:246-261](src/sandbox/fuzzer/v2_runtime.py#L246-L261)）：driver 已持久化时，要求 `load_state`、`load_latest_generation_closure`、`load_latest_feedback` 三者与 `advance` 完全相等，否则 `ValueError`；未持久化时走 `commit_generation` 单一事务 |
| **编排循环** | `run_or_resume_campaign`（[v2_runtime.py:65-233](src/sandbox/fuzzer/v2_runtime.py#L65-L233)）：恢复失败时（非 exploratory）重新抛出，exploratory 则 `pause_campaign` 后 break（`v2_runtime.py:144-155`） |
| **终止原因** | `completion_status` 非 `None` 即终止（`v2_runtime.py:111-112`）；尝试预算耗尽 → `pause_campaign(reason="generation-attempt-budget-exhausted")`（[:118-121](src/sandbox/fuzzer/v2_runtime.py#L118-L121)）；恢复失败 → `"campaign-recovery-failure"`（[:151-154](src/sandbox/fuzzer/v2_runtime.py#L151-L154)） |
| **证据** | `E2`（`tests/unit` 中有恢复与幂等测试）；`E3`（Docker 中断路径未实测） |

### 11.1 SQLite 一致性边界

- `BEGIN IMMEDIATE` + SAVEPOINT 嵌套 + WAL + `PRAGMA foreign_keys=ON`（`v2_campaign_store.py:242-264` `_transaction`）；
- 状态快照按 `state_digest` 内容寻址（`_insert_snapshot`、`load_state_by_digest`，[:673-746](src/sandbox/fuzzer/v2_campaign_store.py#L673-L746)）；
- 结算入口 `commit_settlement`（[:1048](src/sandbox/fuzzer/v2_campaign_store.py#L1048)）——一次调用内完成 Episode 结果、覆盖更新、Finding、预算与调度的写入，这是 `FR-OPS-01`（避免半结算）的主要实现手段；
- 尝试状态机 `_validate_episode_seed_state_transition`（[:64](src/sandbox/fuzzer/v2_campaign_store.py#L64)）强制合法转换。

**生存的 `E3` 判断**：一致性边界在**单库单进程**语义下是清晰且事务化的。跨文件系统（Artifact / 轨迹 / SQLite）的原子性**没有**两阶段提交或补偿记录：先写文件再提交数据库，若中间崩溃则文件成为孤儿。恢复逻辑依赖 `has_saved_recording` 类检查而非文件—数据库比对。

---

## 12. 反馈进入下一轮

| 项 | 内容 |
|---|---|
| **负责模块** | [v2_feedback.py](src/sandbox/fuzzer/v2_feedback.py) `build_next_generation_feedback`；[v2_orchestrator.py](src/sandbox/fuzzer/v2_orchestrator.py) `decide_next_generation` |
| **反馈结构** | `NextGenerationFeedback`、`FeedbackBrief`、`FeedbackGapKind`（6 值）；引导码（guidance codes） |
| **持久化** | `put_generation_closure`（[:1737](src/sandbox/fuzzer/v2_campaign_store.py#L1737)）、反馈随 `commit_generation` 写入 |
| **下游** | 下一轮 `decide_next_generation` |

**关键事实**：`decide_next_generation` 接收 `latest_feedback`，但代码中它**只用于验证与记录 `input_feedback_digest`**（`v2_orchestrator.py`）。真正的分配决策落在 `select_risk_pool_seed`，而它读的是 `RiskProgressLevel` 标量（§3）。

因此：**反馈对象被构造、被持久化、被摘要、被传递，但其内容（行为缺口、联合缺口、饱和信号、置信度）不改变下一轮的选择或变异方向。**

这是 `FR-FUZZ-01`（"反馈必须实际影响下一轮父样本选择、目标方向或变异内容"）与 `AC-04`（"覆盖率引导模式的历史反馈确实影响后续选择或变异，并有可审计的选择理由"）的关键差距。见 `spec-compliance.md` 与 `discussion-items.md` 第 2 项。

**必须公平记录的另一面**：`select_risk_pool_seed` 读取的 `RiskProgressLevel` 确实是**跨 Episode 的覆盖反馈**，随机模式下不使用它。所以"完全没有任何反馈进入选择"是不准确的说法；准确的说法是"**进入选择的历史反馈只有一个 4 级标量**"。

---

## 13. 报告与实验比较

| 项 | 内容 |
|---|---|
| **负责模块** | [src/sandbox/fuzzer/v2_report.py](src/sandbox/fuzzer/v2_report.py) `build_v2_campaign_report`、`write_v2_campaign_report` |
| **输入** | `store` + `campaign_id` |
| **输出** | JSON 报告 payload；`report` 子命令写文件 |
| **证据** | `E2`/`E3`（报告构造有单元测试；报告内容对 `FR-EXP-05` 的覆盖度见 `spec-compliance.md`） |

`FR-EXP-04` 要求"预先冻结、版本化的实验计划、配对 Campaign、多个随机种子"。CLI 有 `--campaign-seed`（默认 `0`），但**默认单一种子**；代码中未找到配对实验的冻结协议对象，也未找到跨 Campaign 的统计比较实现。`FR-EXP-05` 要求的增长曲线、达到门槛所需预算、连续无增益长度、均值/离散度/统计不确定性，需逐项对照报告实际字段，见 `spec-compliance.md`。

---

## 14. 主链涉及的持久化位置汇总

| 位置 | 内容 | 证据 |
|---|---|---|
| `<db>`（SQLite，WAL） | Campaign 生命周期、状态快照、种子目录、语料库、预算、分配、工作单元、尝试收据、结算、决策、闭包、反馈、恢复记录 | `E3` |
| `<data_root>/trajectories/` | 执行轨迹 | `E3` |
| `<data_root>/artifacts/` | Artifact（含 Oracle 产物） | `E3` |
| `<data_root>/replays/` | Replay Manifest | `E3` |
| `<data_root>/failures/` | 失败诊断、`controller-error.txt` | `E3` |
| `<data_root>/target-reviews/` | 目标保持评审证据（仅 `--ollama-mode host`） | `E3` |
| `<data_root>/last-run.json` | 最近一次运行结果（原子写） | `E3` |
| `--progress-dir/episode-NNNNNN.json` | 每 Episode 进度（原子写，`v2_cli.py:270-279`） | `E3` |

---

## 15. 小结：数据流中已证实的缺口

按 `SPEC.md` 条目归类，全部为静态代码证据（`E3`），除非另注：

| # | 事实 | 相关 SPEC 条目 | 详见 |
|---|---|---|---|
| 1 | `coverage_gain` 是单个布尔值，四类新颖性不区分 | `G-02`、§5.3、`AC-03` | §9.5 |
| 2 | `V2CoverageDelta` 11 个字段中 4 个无消费者，含联合覆盖本体 `new_behavior_risk_links` | §5.3、`G-02` | §9.4 |
| 3 | 进入选择的历史反馈只有 `RiskProgressLevel` 标量 | `G-03`、`FR-FUZZ-01`、`AC-04` | §3、§12 |
| 4 | 反馈对象的内容不改变下一轮选择或变异 | `FR-FUZZ-01`、`FR-EXP-02`、`AC-04` | §12 |
| 5 | 只有 `coverage_guided` 产生晋升；随机模式强制 `NO_PROMOTION` | `FR-EXP-01`、`AC-05` | §10.2 |
| 6 | 两模式候选去重历史不对称（`include_derived`、`candidate_history`） | `FR-EXP-03`、`AC-05` | §5.1 |
| 7 | `allow_coverage_saturation=False` 使 `SATURATED` 不可达 | `FR-FUZZ-05` | §10.3 |
| 8 | `submitted` / `termination_reason` / `cleanup_confirmed` 硬编码 | `SEC-04`、`SEC-05`、`AC-09` | §10.1 |
| 9 | 无目标切换变异路径 | `FR-FUZZ-04` | §4.2 |
| 10 | `embedded` 模式无 `target_judge`，晋升被阻断 | `FR-FUZZ-03` | §4.1 |
| 11 | 风险维度仅为 4 值枚举 + 4 级标量 | `G-02`、§5.2 | §2 |
| 12 | `config/risk-taxonomy.yaml` 无引用 | 结构/命名 | `project-map.md` §5.4 |
| 13 | Oracle 无宿主机侧独立重算 | `FR-JDG-01`、`AC-09` | §8 |

**需要强调的解释边界**：以上每一项都是"当前代码的实现事实"，**不是**"已确认的产品缺陷"。第 2、5、6、7 项都带有明确的代码注释或结构性理由，可能是有意的阶段性选择。是否要改变它们，是 `discussion-items.md` 要交给用户决定的问题。
