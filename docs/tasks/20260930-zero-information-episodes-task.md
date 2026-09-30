# TASK-20260930-ZIE：零信息 Episode 的消除、终止语义与动作阶段约束

- 任务 ID：`TASK-20260930-ZIE`
- 状态：**`READY`**（2026-09-30 用户确认任务拆解）
- 对应 SPEC：[`docs/specs/20260930-zero-information-episodes.md`](../specs/20260930-zero-information-episodes.md)（`APPROVED`，`v3`）
- 需求编号：`ZIE-01`、`ZIE-02`、`ZIE-03`、`ZIE-04a`、`ZIE-05`、`ZIE-06`、`ZIE-07`、`ZIE-08`、`ZIE-09`、`ZIE-10`、`ZIE-11`、`ZIE-12`、`ZIE-13`
- 冻结值：`ZIE-F3`、`ZIE-F4`、`ZIE-F5`、`ZIE-F6`、`ZIE-F7`、`ZIE-F8`
- 代码基线：`8d9a5bd`

## 1. 前置依赖与被依赖项

| 方向 | 项 |
|---|---|
| 依赖 | SPEC `v3` 获用户批准；`enc-dir01-01-20260929` 批次数据在本地（`d:/hxjh/runs/enc-dir01-01-20260929`） |
| 被依赖 | (a) 本批报告按新口径重出；(b) 4 集/臂试点；(c) 全量重跑（另立运行包） |

## 2. 包含范围

全部 `ZIE-01`..`ZIE-13`。分三组：

- **A 组（纯报告层，零行为风险）**：`ZIE-01`、`ZIE-04a` 的分类部分、`ZIE-05`、`ZIE-06`、`ZIE-07`、`ZIE-13` 的计数部分。
- **B 组（准入，一处）**：`ZIE-04a` 的"控制集不产出 Episode"。
- **C 组（协议，两处）**：`ZIE-03` 动作阶段任务文本、`ZIE-11`/`ZIE-12`/`ZIE-13` 动作阶段约束。

## 3. 不包含范围

- 不新增/删除/改名任何工具，不改 `OFFICE_V2_TOOL_CATALOG_VERSION`；
- 不改两阶段预算切分（`Literal[8]`、总数 16）；
- 不改 Oracle、判定阈值、覆盖提取、fixture 世界与固定正文；
- 不改随机臂的抽取代码路径（`DIR-05`）；
- 不重建与 `enc-atk10/11` 的对照复现门（另立规格）；
- 本任务**不上服务器、不跑真实模型**，只到本地可验收状态。

## 4. 预计修改区域与受影响的数据流

| # | 文件 | 改动 | 需求 |
|---|---|---|---|
| 1 | `src/sandbox/structured_v1/campaign_report.py` | `curves[]` 加 `material_source`（`normal-injection`/`inherited`/`control`）；汇总加三类计数与消耗数 | `ZIE-04a`/`ZIE-05` |
| 2 | `scripts/report_summary_delivery_e2.py` | `_completion_diagnostics` 加 `termination_kind`；聚合加 `protocol-failure` 口径与新/旧双口径 | `ZIE-01`/`ZIE-06` |
| 3 | `src/sandbox/structured_v1/search.py` | `_generate_root` 的无注入分支不再返回候选：消费该机会并记 `failure_class = no-injected-material` | `ZIE-04a`/`ZIE-F8` |
| 4 | `src/sandbox/structured_v1/phases.py` | `TwoPhaseProtocol` 增"动作阶段禁用工具集"与"保留额度"两个字段；版本 `investigate-act-v1` → `v2` | `ZIE-09`/`ZIE-11`/`ZIE-12` |
| 5 | `src/sandbox/structured_v1/session.py` | 在 `tools.execute(decision)` 之前加**按阶段**的调用闸门：拒绝 → 返回可读理由并计数 | `ZIE-11`/`ZIE-12`/`ZIE-13` |
| 6 | `src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_e2_completion_v3.py` | **新增**：动作阶段任务文本（`ZIE-03`） | `ZIE-03` |
| 7 | `src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_enc2.py` | **新增**：基于 `completion_v3` 的 enc 变体 | `ZIE-03`/`ZIE-09` |
| 8 | `src/sandbox/scenarios/structured_v1/fixtures/__init__.py` | 注册新 fixture id | `ZIE-03` |
| 9 | `tests/integration/test_structured_campaign_report.py` | 扩充既有断言 | `ZIE-10` |
| 10 | `tests/unit/test_zero_information_episodes.py` | **新增**：`ZIE-10` 的 10 项 | `ZIE-10` |
| 11 | `tests/integration/test_structured_two_arm_campaign.py` | 覆盖动作阶段闸门的两臂一致性 | `ZIE-08` |

**受影响的数据流**：

```text
checkpoint(generations[].requests / selections[].parent_id)
-> TwoArmSearch._generate_root          (控制集：消费而非产出 Episode)
-> campaign_report.checkpoint_report    (material_source 分类与计数)
-> EpisodeBundle(finalized/*.json)      (末条 ModelDecision + 交付 effect)
-> StructuredSession 动作阶段闸门        (拒绝检索 / 保留末 2 次额度)
-> report_summary_delivery_e2           (termination_kind + protocol-failure)
-> 聚合口径                              (W/F 分母、双口径对照)
```

**实现注记（已核实）**：

- `MutationPlan.requests: int` 是既有模型字段（`src/sandbox/structured_v1/generation.py:90`），**无需改 schema**。
- `plan.intent_id is None` **不得**用作判据：其文档为"``None`` for roots and structural-only edits"，带生成文本的 root 同样是 `None`（本批 `opp0/1/6/7/11/14`）。
- 无注入根当前**显式跳过**方向闸门（`search.py`：`if not structure.no_injection:`），改动应落在该分支。
- `phase_tracker` / `phase_start` 已可给出 `phase_call_index`，`ZIE-12` 的判据无需新增运行时状态。

## 5. 实施 checklist

### A 组（先做，做完即可用本批数据出对照）

- [ ] **T1**（`ZIE-04a`/`ZIE-05`）`checkpoint_report`：按 `ZIE-F5` 给每次机会打 `material_source`；汇总三类计数与被消耗机会数。
- [ ] **T2**（`ZIE-01`）`_completion_diagnostics`：派生 `termination_kind`（三值表见 SPEC `ZIE-01`）；不改 `StopReason`。
- [ ] **T3**（`ZIE-06`）聚合：`protocol-failure` 定义 + 从 `W/(W+F)`、`W/N_op` 分母剔除 + 单列；**同时**输出旧口径（`ZIE-F4`）。
- [ ] **T4**（`ZIE-13`）被拒绝调用的计数与工具名分布（A 组先落数据结构，C 组填数）。
- [ ] **T5** 报告生成：用 `d:/hxjh/runs/enc-dir01-01-20260929` 出新/旧双口径对照，并写入 SPEC §7 要求的口径注记。

### B 组（准入，一处）

- [ ] **T6**（`ZIE-04a`/`ZIE-F8`）`_generate_root` 的无注入分支改为消费机会，`failure_class = no-injected-material`；走既有 `SS-011` 同路（不产生候选、不产生 Episode、推进方向时钟）。
- [ ] **T7** 断言：控制集被消耗后，`campaign_report` 的连续性与一致性检查仍然通过。

### C 组（协议）

- [ ] **T8**（`ZIE-03`）新增 `completion_v3` 任务文本：满足 SPEC `ZIE-03` 的 (1)(2)(3)；新增 `enc2` fixture 并注册；`enc-01` **逐字节不变**。
- [ ] **T9**（`ZIE-11`/`ZIE-12`）`TwoPhaseProtocol` 增字段并版本升位；`session.py` 在 `tools.execute` 前加闸门，拒绝返回**可读理由**。
- [ ] **T10**（`ZIE-08`）确认 A/B/C 三组对两臂同等生效。
- [ ] **T11**（`ZIE-09`）版本身份写入 checkpoint；旧 checkpoint 拒续跑。

### 测试与验收

- [ ] **T12** 新增 `tests/unit/test_zero_information_episodes.py` 覆盖 `ZIE-10` 全部 10 项；扩充两臂与报告测试。
- [ ] **T13** 全量验证（见 §7 命令）。

## 6. 验收 checklist

- [ ] `ZIE-10` 的 10 项全部有测试证据；
- [ ] 本批报告双口径产出，且含 SPEC §7 要求的口径注记（`enc-dir01` 的 `opp2/4/7/15` 四集 `defense` 归类受污染）；
- [ ] A 组单独可交付：本批新口径下引导臂应为 —— `delivery-attempted` 9、`wrapped-up` 4、`protocol-failure` 3（`opp0/11/14`）、`inherited` 2（`opp2/10`）、`control` 1（`opp3`，B 组生效后不再产生 Episode）；
- [ ] 试点（`ZIE-F3`，每臂 4 集）报告：`delivery-attempted` 占比、`budget-truncated` 占比、被拒绝调用分布；
- [ ] `git diff` 可证：未新增/删除/改名工具、未改预算切分、未改随机臂抽取路径、未改 fixture 世界与固定正文；
- [ ] `summary-delivery-enc-01` 的 `manifest_digest` 与 freeze 清单**逐字节不变**。

## 7. 验证命令

```text
python -m compileall -q src agent_image tests
scripts/project_ruff.cmd check src agent_image tests
scripts/project_python.cmd -m pytest tests/unit/test_zero_information_episodes.py -q
scripts/project_python.cmd -m pytest tests/integration/test_structured_campaign_report.py -q
scripts/project_python.cmd -m pytest tests/integration/test_structured_two_arm_campaign.py -q
scripts/project_python.cmd -m pytest tests/unit tests/integration -q
git diff --check
```

`enc-01` 字节不变的核验：改动前后各跑一次 `load_fixture("summary-delivery-enc-01")`，比对 `manifest_digest` 与 freeze 清单哈希。

## 8. 风险、失败信号与回滚

| 风险 | 失败信号 | 回滚 |
|---|---|---|
| 动作阶段闸门误伤正常交付 | 试点里交付类调用也被拒 | 闸门是独立函数，回退即去除 `ZIE-11`/`ZIE-12` 两个字段 |
| 控制集判据误伤 | `material_source == control` 的集合异常变大，或 `campaign_report` 连续性检查失败 | 回退 T6；A 组不受影响 |
| 新任务文本引入行为漂移 | 试点 `budget-truncated` 未下降（SPEC §4 停止条件） | 停止加码，回到 SPEC 报告失败 |
| 协议升位使旧 checkpoint 不可读 | 旧批报告无法复算 | A 组（纯报告）不依赖协议，仍可出旧批口径 |
| `termination_kind` 抽取失败 | 某集无 `model_decisions`（应为不可能） | 纯报告函数，删字段即回退 |

## 9. 尚未解决的问题

1. **新 fixture 命名**：建议 `summary-delivery-enc-02` + `summary_delivery_enc2.py`（对应 `completion_v3`）；`n1`/`n2` 变体机制是给"遇合位置"用的，不适用于任务文本变更。
2. **拒绝结果的构造**：被闸门拒绝的调用应返回什么样的 `ToolCallReport` 才能既不触发 `capture_effect`、又能让模型看到理由 —— 需在实现时确认（倾向：不产生 effect、`blocked=True` 的普通结果，与既有 ACL 拒绝同形）。
3. **试点参数**：`--opportunities 4` 是否触发 `campaign_report` 的 `non-contiguous opportunity evidence` 检查，需实跑确认。
4. **本批报告落盘位置**：`docs/reports/` 下的文件名与是否附带脚本，按 `docs/reports/README.md` 惯例确认。
