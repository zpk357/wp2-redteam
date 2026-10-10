# LC-01：逐步退役旧代码

状态：`APPROVED`（用户于 2026-10-10 在本对话回复“批准”；允许 TASK 拆解，TASK 尚未批准前不得删除源码）

日期：2026-10-10。审计基线：本地 `public`，HEAD `5aeadd2`。

## 1. 目标与权威

- `LC-01`【已确认目标】：按用户当前指示，逐步删除旧代码，减少旧设计对 AI 上下文的污染；每次工作前读 LOG，工作后写 LOG，并维护索引供其他 AI 接续。
- `LC-02`【已确认边界】：用户在当前对话中的四条硬设计优先于旧文档；行为键为整条有序调用链，正文等自由文本不入键，path 留文件名，本集生成 ID 归一化；风险四维累计 1–6 级，等级不入键；增量为新链或任一风险等级上升；joint 保留记录但不进评分或提示词。
- `LC-03`【事实，非新决定】：当前源码身份为 fixture 5.1.0、coverage v5、campaign v16、selector v20。用户最初消息的 campaign v14 已被后续提交升位；版本不得凭本清理规格改动。

本规格只约束旧代码退役，不改产品长期目标；不以当前实现替代用户要求。`docs/SPEC.md`、README 中的旧覆盖要求和旧运行口径与上述决定冲突时，按当前用户决定处理，差距在本规格与 LOG 中明确记录。历史规格和历史运行不追溯改写。

## 2. 输入、输出、状态与副作用

- 输入：最新 LOG、LOG-INDEX、Git 状态、候选源码、全部代码/配置/字符串引用、构建入口与对应测试。
- 输出：经用户批准的精确退役范围、独立 Git 提交、运行过的验证证据和新的 LOG 索引。
- 状态：每批遵守 `DRAFT SPEC → 用户批准 → DRAFT TASK → 用户批准 READY → IN_PROGRESS → DONE`。
- 副作用：退役 API 将从工作树与后续源码构建中消失；Git 历史和既有运行证据保持存在。只有当前主链的行为保持不变，不承诺已退役的旧 API 继续可导入。
- 失败：发现活跃调用、未识别构建输入、测试失败、用户并行修改或历史证据重算依赖时停止当前批，记录原因，回到规格或任务审阅；不整目录强删。

## 3. 代表性当前主链与保护范围

坐标菜单 → `error_capable_selector` → `error_capable`/`error_capable_world` 生成材料 → `error_capable_executor`/容器 adapter → `error_capable_agent` 与 Office V2 工具真实执行 → `error_capable_bridge` → `assess_delivery`/`assess_effect` → `ObservedKey` 与 `RiskDimensionTracker` → Campaign 结算 → 引导臂下一轮反馈；随机臂独立选择。

- `LC-04`【拟议约束】：退役不得改变这条链的候选空间、Agent 输入、工具效果、四类 Oracle、覆盖键、累计风险级别、增量、分数、预算、随机隔离、失败结算或恢复。
- `LC-05`【拟议约束】：保留仍被主链、通用 Runtime、回放和构建依赖的 Office V2 世界/工具/契约；保留历史 fixture、归档、数据库、轨迹、报告、日志、规格与任务原文。`.git`、工作区外依赖与服务器不在删除范围。
- `LC-06`【拟议约束】：联合反馈残留和过时 README 属独立后续问题。最新 LOG 已记录 joint 仍进入 payload，且 stalled/path 饱和由 joint 驱动；本批不得夹带修复、升版本或部署。

## 4. 首批拟议退役对象

本节是待批准的需求范围，不是已批准任务计划。

| 对象 | 当前数量/体积 | 拟议结果 | 依据 |
|---|---:|---|---|
| `src/sandbox/structured_v1/completion.py` | 1 文件，3,101 B | 删除旧独立完成判定 API | `judge_normal_task` / `NormalTaskJudgment` / 模块名在 src、agent_image、scripts、tests、pyproject 中仅被其自身与专属测试引用；包 __init__ 无导出 |
| `tests/unit/test_structured_completion.py` | 1 文件，7,254 B | 仅移除 8 条依赖旧 API 的测试与必要 import；保留文件、共享辅助函数及 3 条独立关闭测试 | AST 核对共 11 条测试，其中 8 条调用 judge_normal_task，3 条只验证 judge_episode 的关闭/确定性事实语义 |

- `LC-07`【待批准范围】：允许退役上述旧完成判定 API；不同时退役 structured 路线整体、旧 fixture、旧 Oracle 或其其他测试。
- `LC-08`【待批准验收】：以下 3 条测试必须原语义保留并通过：
  - `test_an_unresolved_channel_forbids_a_clean_obligation_result`
  - `test_a_provably_cancelled_channel_allows_a_clean_result`
  - `test_a_positive_fact_survives_an_unresolved_channel`
- `LC-09`【待批准验收】：删除后生产代码、工具脚本、配置和存活测试对该退役 API 的引用归零；历史 docs/LOG 的引用保留，并由新 LOG 解释已退役，不能用过时历史重新生成旧模块。

首批预计删除 1 个源码文件、局部修改 1 个测试文件；不将 2 个文件的总大小当成实际释放空间。只读引用搜索不是完整运行验收，也无法穷尽仓库外用户脚本；仓库外调用者需使用历史提交。

## 5. 验证与回退要求

- `LC-10`【拟议验收】：实现前后均执行首批聚焦测试；删除后执行完整测试收集、保留的 structured 关闭/义务测试、当前 error-capable 行为链/四类判定/增量/选择/恢复/容器导入契约回归。必要命令在用户批准规格后写入 TASK，不把本轮只读核对记成测试通过。
- `LC-11`【拟议验收】：Python 改动执行项目 Python 的 `compileall -q src agent_image tests`、`scripts/project_ruff.cmd check src agent_image tests` 和 `git diff --check`。失败必须报告；不能通过删除仍有用途的测试掩盖失败。
- `LC-12`【已确认协作要求】：每批开始先读 LOG 顶部，再由索引定位最小相关历史；结尾在 LOG 顶部添加唯一标识，写范围、依据、命令及结果、未做项、阻塞和下一入口，并在 LOG-INDEX 加行。并行 AI 的新增记录与用户原有文件不得覆盖。
- `LC-13`【拟议 Git 约束】：每个完成批次独立提交；只暂存本批源码、测试、规格/任务和被授权的记录。现有 LOG/LOG-INDEX 尚未入库：不得把全文历史作为新文件顺带提交；本轮新增 SPEC 可独立提交，日志新增内容保留在本地供协作。
- `LC-14`【拟议回退】：保留准确的删除前 HEAD 和提交 ID。需要回退时以反向提交恢复本批已跟踪改动，不 reset/rebase，不覆盖用户并行工作；历史数据不参与回退。

## 6. 假设、待决与停止条件

1. 【假设】用户希望优先清除无当前生产调用的独立旧模块，再评估旧路线入口退役；若希望第一批就退役整条 structured 或 fuzzer 路线，应重审范围与依赖，不能沿用本首批验收。
2. 【已决】用户已批准 LC-07 的 1 个源码文件 + 8 条旧 API 测试退役范围；其余 3 条测试继续保留。具体 TASK 仍需独立确认。
3. 【待决】后续旧路线的命令行入口、可选 Agent、历史证据重算 API 是否继续支持？本规格尚未允许删除它们。
4. 【停止】发现新生产/动态字符串引用、其他 AI 修改同文件、证据依赖或验证失败时，不能扩大删除范围解决问题。

## 7. 初次起草实际证据（批准前）

- 已读取 LOG 最新记录 `20261010-fit-live-readout-and-joint-residue`，以及索引定位的 `20260812-office-v2-stage8-step-8-5-v1-disposition`、`20260812-office-v2-formal-entry-and-freeze`、`20260805-office-v2-scenario-priority-reset`、`20260831-office-v2-semantic-mutation-correction-plan`。
- `git status --short`：无已跟踪源码修改；大量用户已有未跟踪文档与根目录材料保持原状。
- `rg` 引用检索与项目 Python 3.12 AST：确认首批模块代码调用仅来自专属测试；11 条测试的 8/3 分类成立。历史 `docs/tasks/20260921-structured-scenario-implementation.md` 记录该模块与 11 条测试的原用途。
- 本轮只产出 DRAFT SPEC 和协作日志；未删除旧代码、未创建 TASK、未运行 pytest/compileall/Ruff、未访问服务器或模型。
