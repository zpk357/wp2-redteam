# SOC-T01 审计 A：复用边界（旧 Episode 数据流与不可复用语义）

- 任务：`TASK-SOC-20260921` 的 **SOC-T01**（状态 IN_PROGRESS）；交付物 1/3。
- 审计基线：HEAD `7822c44`；工作树仅两行用户未跟踪文件（`.pytest-tmp/`、`harness-node-modules.tar.gz`）。
- 依据：三份已批准 SPEC（SAF / SRC / FBK，`29b5173`）与 TASK 的 §1.1 三项先决修订。
- 方法：只读代码审计（逐文件核对）。**本文件不含实现方案，只回答"能不能复用、以什么方式复用"。**
- 详细流转另见 `episode-data-flow.md`（基线 `a15b127`，仍是对旧路线最完整的流程描述；
  它早于 2026-09-20/21 的攻击效果协议、引导种子优先与绑定来源修复，引用时须注意版本差异）。

## 1. 旧路线一个 Episode 的流转（当前 HEAD）

| # | 步骤 | 入口（file:line） | 关键结构 | 与新协议的关系 |
|---|---|---|---|---|
| 1 | 臂与世代决定 | `v2_cli.py:93`（`--strategy`）、`v2_orchestrator.py:90`（`decide_next_generation`）、`v2_campaign_loop.py:322`（`choose_next_allocation`）、`v2_risk_pool_scheduler.py:101/179/228` | `GenerationAllocation`、`SelectionReceipt` | 机制可换，账本可留 |
| 2 | 根种子与 fixture 装配 | `v2_bootstrap.py:50`、`v2_seed_pools.py:366`、`v2_target_oracle.py:503/515` | 16 个冻结根种子 + 16 份 direct-task fixture | **不可复用**（见 §2） |
| 3 | 变异计划与算子 | `mutation/v2_policy.py:453`（算子目录 `:260`）、`v2_plan_builder.py:29`（槽位注入 `:104`） | `MutationPlan`、brief、preserved/changed 路径 | 骨架可留，算子与槽位须重写 |
| 4 | Provider 生成与准入 | `mutation/v2_preparation.py:96`、`v2_docker.py:31`、`v2_candidate.py:111`、`v2_validation.py:37`、`v2_materializer.py:54` | provider attempt、解析/校验/物化 | 骨架可留，语义须重写 |
| 5 | Episode 执行 | `fuzzer/v2_real_episode.py:173/187/200`、`replay/replay_engine.py:168`、`agent_image/app/office_v2_session.py:197/505`、`tools/runtime.py:103/189` | 会话、运行时、工具契约、provenance | **工具与状态基础设施是复用候选，但旧执行信封不能承载本协议**（见下方"信封结论"；提交时效果证据亦须新增） |
| 6 | 录制与 Oracle | `replay/replay_engine.py:60/666/730`、`oracle_evidence.py:469/799`、`oracle.py:137`、`security_oracle.py:355` | trace、bundle、目标步骤判定 | 录制可留，**判定不可复用** |
| 7 | 覆盖与晋升 | `coverage/v2_episode_coverage.py:448/620`、`v2_promotion.py:71/135`、`v2_target_preservation.py:20`、`v2_target_judge.py:92` | 行为/风险/联合原子、晋升门 | **须改造**（见 §4） |
| 8 | 结算与恢复 | `fuzzer/v2_real_runtime.py:749`、`v2_campaign_store.py:1117/242/1300`、`v2_work.py:93/126` | 收据、闭包、事务、预算 | **可复用** |

## 2. 不可复用语义（必须新写，且不能"换个名字接着用"）

| 语义 | 位置 | 为什么不可复用 |
|---|---|---|
| 固定 16 目标目录 | `v2_target_oracle.py:320`（`TARGET_ORACLES`）、`:500`（`TARGET_ORACLE_BY_TARGET`）、`:515`（`build_formal_target_scenario_supports`） | 新协议没有固定目标集合；成功判定不再闭在目标枚举上（SAF：义务违反） |
| `TargetStep` 步骤形状 | `v2_target_oracle.py:94-148`（tool/action/参数名/最小条数/来源步骤/权限/收件人/敏感度） | 形状匹配把"动作长什么样"当成判定核心，与新协议的"效果 + 受众"判定正交 |
| 首匹配顺序扫描 | `v2_target_oracle.py:1032`（`evaluate_target_steps`），`next(...)` 顺序扫描 `:1045-1059`、`search_from` 单调前移 `:1071`，匹配 `:895`（`_exchange_matches`） | 只认"第一次看起来像"的交换，无法表达义务的全称/存在判定；记录顺序会改变结论 |
| "步骤实现"定义 | `v2_target_oracle.py:983`（`_exchange_realizes`：committed transition + created kinds/field paths） | 这是步骤形状的一部分，不是安全效果 |
| 知识前置与绑定前置 | `v2_target_oracle.py:844`（`_required_target_resources_visible`）、`:870`、`security_oracle.py:554/799`（needs-binding → INVALID）、`v2_agent_behavior.py:185`（`context_complete` 硬门槛） | 把"Agent 是否看到某资源"当成违规前提；新协议用"提交时可读性 + 受众关系"判定 |
| 行为签名去重键 | `v2_agent_behavior.py:282-297`（`target_behavior_signature`） | 键建立在目标步骤上，不能用于义务维度（FBK 的 R/B/J 另有键） |
| 直接任务注入路径 | `adversarial_conditions.py:54/90`（`DirectTaskCondition.instruction`）→ `execution_request.py:64`（prompt）；`v2_target_oracle.py:263`（per-seed 参数摘要） | 新协议禁止改写可信任务（SRC `SS-008`），攻击面只在低信任材料 |
| 风险等级 0–3 与风险池调度 | `v2_campaign_state.py:388`（`settle_risk_progress`）、`v2_risk_pool_scheduler.py` | 旧"风险维度覆盖"的四类等级不再作为选父依据（FBK 六臂规则取代） |

## 3. 可复用资产矩阵（结论：复用方式 + 改造点 + 责任模块）

| 资产 | 现位置（file:line） | 复用方式 | 改造点 | 责任 |
|---|---|---|---|---|
| 事务 / 收据 / 恢复 | `v2_campaign_store.py:242/1084/1117/1300/1654`、`v2_work.py:62/93/126/196` | **原样复用** | 新增新协议的持久化表与游标（新 Campaign 身份） | T06 |
| 预算预留与结算 | `v2_campaign_state.py:83/104/147/170`、`v2_loop_contracts.py:60/185/206/244/290` | **原样复用** | 增加 Judge 维度与 M/E/T 三硬边界 | T06 |
| 选择收据 / 世代闭包 | `v2_selection.py:105/109/170`、`v2_orchestrator.py:90/161` | **原样复用**（只依赖 digest 与状态机） | 选择规则换成 FBK 的单元/索引/冷却 | T05 |
| 候选去重与合法性门 | `v2_candidate.py:111`、`v2_validation.py:37`、`v2_materializer.py:54` | 借用骨架 | 换成 `StructuredCase` schema + 图/容量/私密泄露检查（SRC `SS-005`~`SS-014`） | T02 |
| 变异生命周期与计费 | `mutation/v2_preparation.py:96`、`v2_provider.py:49/152` | 借用骨架 | 算子目录、prompt、槽位字段路径全部重写；修复请求计费 | T02 |
| Provider 容器化 | `mutation/v2_docker.py:31/104`、`agent_image/app/office_v2_mutator_worker.py:120` | **原样复用** | 请求/响应 schema 换成结构 patch | T02 |
| Agent 执行桥 | `fuzzer/v2_real_episode.py:173/200`、`office_v2_session.py:197/505`、`tools/runtime.py:103/189` | **工具与状态基础设施为复用候选；旧 `V2ExecutionEnvelope` 不能承载本协议** | 见下方"信封结论"；输入换成静态材料物化；**必须新增提交时的效果证据**（审计 B §2 / B8）；不得改动工具语义 | T03 |

**信封结论（2026-09-21 修正，取代此前"接口不变"的说法）**

> 旧执行桥的工具和状态基础设施可以作为复用候选，但旧 `V2ExecutionEnvelope` 不能承载结构化情境协议；
> 需要新增协议身份和容器分支。旧信封、旧常量和旧 Campaign 保持不变。

依据（本次独立核实 `src/sandbox/protocol.py`）：信封把三个**冻结常量**写死在构造校验里——
`base_world_digest`（`:154-156` 与 `:236-237`）、工具目录 `:157-159`/`:238-239`、
**旧 16 目标目录** `:160-162`/`:240-241`；其中目标目录断言与 `SS-001`（本协议无固定目标集合）直接冲突，
因此不能靠"复用旧信封"绕过。容器侧唯一入口是 `request.office_v2_execution → load_office_v2_session`
（`office_v2_runtime_surface.py:48-65`、`office_v2_session.py:505-529`），故新增协议身份需要容器分支。
新信封的输入、状态、权限、Oracle、恢复与版本身份必须先写成可审阅契约（A/B 两案比较），
**确认前不实现容器分支**。
| 工具契约与 provenance | `tools/contracts.py:91/119/217`、`tools/provenance.py:46/146/188` | **原样复用** | 无需改动（证据对象与目标语义无关） | T03 |
| 录制 / manifest / bundle 校验 | `replay/replay_engine.py:60/666/730`、`oracle_evidence.py:469/799`、`oracle_trace.py:43/137/179` | **原样复用** | 新协议仍需 bundle（作为义务判定的证据输入），但消费者换成 SAF Oracle | T03 |
| 实时 Oracle artifact | `office_v2_session.py:231`、`oracle_trace.py:43` | 借用 | 断言集合换成两条义务 | T03 |
| 工具角色注册表（新） | 无 | **新写** | FBK `SOC-FBK-04` 的 `discover/read/transform/prepare/deliver/check/clarify` 映射 | T04 |
| 覆盖增量与晋升门 | `coverage/v2_episode_coverage.py:448/620`、`v2_promotion.py:71/135` | **改造** | 见 §4 | T04/T05 |
| 目标保全判定（Judge） | `v2_target_preservation.py:20`、`v2_target_judge.py:92` | **退役** | 新协议不需要"整段目标保持"；若保留类似评审须先补账（见审计 B） | T02/T06 |
| 报告与比较 | `v2_effectiveness_report.py`、`v2_comparison_report.py`（本轮新增） | 借用 | 终点换成 J 成本曲线面积与 U，另报 VIOLATED/NO_OBSERVED_VIOLATION/UNKNOWN/硬阻断 | T07 |

## 4. 需改造而非丢弃：覆盖与晋升

- 增量数据结构（`V2CoverageDelta`、快照）可留；但**准入前置必须换**：
  旧的 `target_match` + `target_preservation` + `context_complete` 门（`v2_promotion.py:135`）
  要替换为"新 R / 新 B / 新 J 单元"（FBK `SOC-FBK-06`、`SOC-FBK-10`）。
- `risk_progress`（0–3）与 `target_behavior_signature`（`v2_agent_behavior.py:282`）**语义作废**，
  不能改名沿用；R/B/J 的键与枚举由 FBK `§3`/`§4` 冻结。
- 局部/全局作用域（`local_seen` / `global_seen`）是新概念，旧实现只有单层覆盖集合。

## 5. 验收对照（T01 验收第 1 条）

- **关键字段有来源与责任模块**：本文件 §3 的每一行都给出位置与责任子任务；无归属的资产不进入复用清单。
- **"必须做但禁止生成"的动作错位检查**：见 `soc-t01-summary-delivery-a-inventory.md` §6 ——
  两条义务只约束"内容不得进入不该进入的受众"，**不要求任何被禁动作**；合法任务在无注入根上可独立完成。
- **可复用/须新增边界**：§2（不可复用）+ §3（复用方式）+ §4（改造）三张表即边界。
