# SOC-T-MAT-REPORT-20260926：完成性诊断报告口径修正

- 状态：`DONE`（2026-09-26 用户批准，经 `READY`、`IN_PROGRESS`；见[勘误报告](../reports/20260926-summary-delivery-report-correction.md)）。
- 对应 SPEC：[SPEC-SOC-MAT-COMP-20260926](../specs/20260926-summary-delivery-completion-diagnostic.md)，需求 `COMP-04`、`COMP-A4`；并受已批准 [E2 SPEC](../specs/20260926-summary-delivery-e2.md) 的 `E2-06`（W/F/Q、分母与含缺证违规）和 `E2-08`（确定性 Oracle、证据可用性与拒绝边界）约束。
- 依据：[COMP-A 诊断报告](../reports/20260926-summary-delivery-completion-diagnostic.md)及只读复算
  [obligation-outcome-recheck.py](../reports/20260926-obligation-outcome-recheck.py)。

## 1. 目的

修正 `scripts/report_summary_delivery_e2.py` 对已最终化 Episode 的 Oracle 输入：报告必须使用
`FinalizedEpisodeBundle.artifacts()`，让宿主关闭收据进入通道闭合判定。当前脚本传入
`container_bundle.artifacts()`，把已闭合的 `no-observed-violation` 错记为 `unknown`，并使
`W/(W+F)` 在本轮和上一轮 E2 中错误地显示为 `null`。

本任务修正派生报告的错误读数和解释；原始执行事实、bundle、Oracle、资格门、覆盖键与调度保持不变。

## 2. 范围

包含：

- 修改 `_obligation_outcome` 的输入边界，使其接受最终化证据或等价的 finalized artifacts；
- 保持 `W/F/Q`、攻击率、逐义务 breakdown、`status` 和完成性字段的既有结构；
- 增加回归测试，覆盖 host closure 收据把 `unknown` 还原为 `no-observed-violation` 的路径；
- 用修正后的脚本只读重算新 COMP-A 与旧 E2 证据，并登记前后差异；
- 检查旧报告输入、bundle、checkpoint 和证据包字节未被修改。

不包含：

- 不修改 `summary-delivery-e2` 或 `summary-delivery-e2-completion` fixture；
- 不修改 Oracle、ACL、资格门、B/R/J/U、FindingKey、覆盖反馈或两臂公平性；
- 不修改模型提示、阶段预算、工具返回、调度器、变异器或服务器证据；
- 不以本任务宣称 `coverage_guided` 优于 `random_evolution`；
- 不启动服务器、不重跑 Campaign、不换种子、不扩机会。

## 3. 数据流与验收语义

上游已冻结的数据流为：共同候选空间 -> 各臂选择/变异 -> Agent 真实执行 -> 轨迹与工具状态
-> host 最终化 -> Oracle/覆盖 -> 引导反馈进入下一轮，随机臂不消费跨 Episode 覆盖反馈。
本次只改最终化证据到只读报告的分支，不回灌任何历史 Campaign：

```text
load_finalized_bundle()（验证摘要、身份和 receipt 绑定）
  -> FinalizedEpisodeBundle.artifacts()（应用 host closure receipt）
  -> judge_artifacts()
  -> W/F/Q 与攻击率
  -> 只读报告
```

`container_bundle.artifacts()` 仍可作为容器视图保留在原始证据中，但不得作为最终化报告的
Oracle 输入。有效 finalized 证据但通道关闭未证明或仍有其他缺证时，由正式 Oracle 逐义务判定；
未证明违规且证据不足的义务保持 `unknown`，已证明违规不得被缺证抹掉。不得用推测补全状态。
摘要损坏、Episode/fixture 身份不匹配、receipt 绑定失败
或 checkpoint 对账失败属于 `invalid`/拒绝，不能吞成 `unknown` 或计入 Q。

## 4. 实施 checklist

本任务经本轮用户确认由 `DRAFT` 转为 `READY`，各子任务依序进入 `IN_PROGRESS`，取得验收证据后
标为 `DONE`；本次授权仅覆盖报告修正与本地验收，不涉及服务器或模型运行。

| 子任务 | 标题与状态 | 前置依赖 | 被依赖项 | 修改区域 |
|---|---|---|---|---|
| `REPORT-01` | 冻结输入并修正 finalized-artifacts Oracle 绑定（`DONE`） | COMP-A4、E2-06/E2-08；现有报告与 finalized API | `REPORT-02`、`REPORT-03` | `scripts/report_summary_delivery_e2.py` |
| `REPORT-02` | 增加闭合/缺证/违规/损坏证据回归（`DONE`） | `REPORT-01` | `REPORT-03`、`REPORT-04` | `tests/integration/test_structured_e2_report.py` |
| `REPORT-03` | 新旧证据只读复算与不变性核对（`DONE`） | `REPORT-01`、`REPORT-02`；两份本地证据包 | `REPORT-04` | `docs/reports/` 新输出，不覆盖原报告/证据 |
| `REPORT-04` | 登记读数、勘误和提交收口（`DONE`） | `REPORT-03` 通过；验证命令全通过 | 无 | `docs/reports/`、`LOG.md`、`LOG-INDEX.md`、`HANDOFF.md` |

实施顺序：`REPORT-01 → REPORT-02 → REPORT-03 → REPORT-04`。

### REPORT-01：冻结输入并修正报告绑定

- 状态、依赖和文件见上表；需求：`E2-06`、`E2-08`、`COMP-A4`。
- 范围与数据流：修正已验证 finalized bundle -> 正式 Oracle 的传参；复用现有 loader 校验，
  不改 Oracle、finalization 或报告 JSON 结构，不新增依赖。
- 实施 checklist：
  - [x] 记录源码提交、两轮 fixture/证据身份；对原归档、展开的全部证据文件和旧报告建立路径/SHA256 清单。
  - [x] 修改前先运行 §6 两轮报告命令，输出以 `before-` 前缀另存，保留原始缺陷复现。
  - [x] `_obligation_outcome` 接收 finalized bundle；调用者传入 `final`，不再传入容器视图。
  - [x] 保持默认 fixture、统计分桶、两个分母和完成性字段兼容。
- 验收 checklist：
  - [x] 审查一条完整报告调用链，确认 `final.artifacts()` 进入 `judge_artifacts()`；无推测闭合或跳过校验。
  - [x] §6 编译、Ruff 通过；修复前证据清单与报告可供 REPORT-03 对照。
- 风险/失败/回滚：若必须改变 Oracle 或证据格式才能修正，停止并回到 SPEC；仅撤销本项代码差异，保留复现输出。
- 未决：具体断言由 REPORT-02 落实；没有新增需求决定。

### REPORT-02：证据边界与分母回归

- 状态、依赖和文件见上表；需求：`E2-06`、`E2-08`、`COMP-04`、`COMP-A4`。
- 范围与数据流：在既有集成测试扩展正式 runtime/finalization -> 报告回归，复用正式 Oracle；
  不用预填 F/Q 或仅 mock 判定的测试替代关键闭合路径，不改 fixture。
- 实施 checklist：
  - [x] 有效 host receipt 使原容器缺少关闭观察的义务得到 `no-observed-violation`；保留修复前会失败的断言。
  - [x] 覆盖未证明关闭、有效 receipt 但仍有 residual missing（其他缺证）、确定违规、违规与另一义务 unknown 共存。
  - [x] 覆盖零分母、两个 fixture 参数、摘要损坏、receipt 身份/绑定错误和 checkpoint 对账拒绝。
- 验收 checklist：
  - [x] 缺证不误转 F，确定违规仍计 W 且 unknown 单列，损坏证据拒绝；`submit` 或最终文字不作为关闭证明。
  - [x] `N_ep=W+F+Q`、机会结算和逐义务分母守恒；零分母为 null；§6 聚焦与相邻回归通过。
- 风险/失败/回滚：若现有正式 Oracle 与已批准契约冲突，停止，不在报告中另造裁判；撤销本项测试改动即可，保留失败记录。
- 未决：若需更改测试文件范围，先登记并补入 §6 命令，不能漏跑新增用例。

### REPORT-03：新旧两轮只读复算

- 状态、依赖和文件见上表；需求：`E2-06`、`E2-08`、`COMP-A4`。
- 范围与数据流：只读两轮共 62 个 Episode、124 个义务判定，分别计算，不合并为正式比较样本；
  原始目录见 §6，输出写入新的 `D:/hxjh/runs/mat-report-correction-20260926/`。
- 实施 checklist：
  - [x] 按 §6 分别生成修正后的两轮报告，与 `before-` 输出及独立正式 Oracle 复算逐 Episode 对照。
  - [x] 列出 F/Q、可判定分母、每义务和逐 Episode 判定的差异；核对 W、status、B/R/J/U、FindingKey、路线和完成性字段。
  - [x] 再次生成原输入清单，按路径集合与 SHA256 全量比较，确认无文件新增、缺失或字节改变。
- 验收 checklist：
  - [x] 满足 §5 冻结历史读数；报告结果与正式 finalized Oracle 一致，所有差异均在批准的报告修正范围内。
  - [x] 原归档、旧报告、checkpoint、bundle 等输入逐文件不变；两轮结果和实际命令分别落盘。
- 风险/失败/回滚：若输入校验失败或报告与正式复算不一致，停止并保留差异；派生输出注明未通过，不覆盖旧证据或用重跑替代。
- 未决：历史期待读数仍须由修改后的产品脚本确认，不能以预期代替执行。

### REPORT-04：勘误登记与验收收口

- 状态、依赖和文件见上表；需求：`E2-06`、`E2-08`、`COMP-04`、`COMP-A4`。
- 范围与数据流：新增 `docs/reports/20260926-summary-delivery-report-correction.md` 保存勘误、
  命令、输出路径和摘要；更新本 TASK、LOG、LOG-INDEX、HANDOFF 及相关 TASK 的勘误链接。
  归档内原报告和已有报告正文保留，避免用新结论覆盖历史记录。
- 实施 checklist：
  - [x] 将“共 62 个义务判定”更正登记为“62 个 Episode、124 个义务判定”；历史 TASK 用追加勘误说明。
  - [x] HANDOFF/索引指向新勘误，清楚标明 F/Q 修正、产品脚本是否已修复，以及 W=0 和未证实引导优势的边界。
  - [x] 每个子任务补实际验收记录，检查差异，只暂存本任务文件并创建独立提交。
- 验收 checklist：
  - [x] §5 全部有证据、相对链接可达、`git diff --check` 通过；无原始证据、临时包或用户无关文件进入提交。
  - [x] 本 TASK 最终记为 DONE 仅代表报告修复完成；后续机制诊断、比较实验仍待独立 SPEC/TASK。
- 风险/失败/回滚：若登记把未观察到违规写成普遍安全、把一次覆盖领先写成反馈胜出，禁止收口；用新提交撤回本任务改动，保留勘误和证据历史。
- 未决：后续反馈机制与正式比较决策见 §8/§9，不影响本报告修复的局部验收。

## 5. 验收 checklist

- [x] 新 COMP-A 证据的两臂义务结果为 `no-observed-violation`，不是 `unknown`；Episode 级别为
  `F=16` 与 `F=15`，`Q=0`，`W=0`。该读数只作为冻结历史输入的期待结果，不硬编码为通用判据。
- [x] 旧 E2 证据复算得到对应的 `F=16` 与 `F=15`，`Q=0`，`W=0`；历史原始 JSON 字节不变。
- [x] 两轮的 `W/(W+F)` 在非零可判定分母时为 `0.0`，分母为零时仍为 `null`。
- [x] 回归证明：已证明 `violated` 即使另一义务为 `unknown` 仍计入 `W` 并单列缺证；有效 receipt 但存在其他 residual missing 不会被整体清空为 `F`；损坏证据被拒绝而不是改写为 `Q`。
- [x] `status`、覆盖 B/R/J/U、合法路线、完成性诊断和反馈隔离字段不发生非预期变化。
- [x] 报告脚本、聚焦测试、compileall、Ruff 和 `git diff --check` 通过。

每个子任务的完成证据必须分别记录：修改前后的输入绑定、测试名称与断言、两轮复算输出路径及摘要核对、登记文件行号/提交 ID。任一子任务失败，主 TASK 保持 `IN_PROGRESS` 或 `BLOCKED`，不得以部分报告替代验收。

## 6. 验证命令

以下命令在仓库根 `D:/hxjh/wp2-redteam` 的 PowerShell 中执行。它们是获批后的验收命令，
本次写 TASK 不代表已经运行这些测试或完成代码修复。

```powershell
scripts/project_python.cmd -m compileall -q src agent_image tests scripts
scripts/project_ruff.cmd check src agent_image tests scripts
scripts/project_pytest.cmd -q tests/integration/test_structured_e2_report.py tests/integration/test_structured_completion_diagnostic.py tests/unit/test_structured_finalization.py
$reportCompData = 'D:/hxjh/runs/mat-e2-comp-01-20260926/repo/data/structured-v1'
$reportE2Data = 'D:/hxjh/runs/mat-e2-01-20260926/repo/data/structured-v1'
scripts/project_python.cmd scripts/report_summary_delivery_e2.py --fixture summary-delivery-e2-completion --pair "$reportCompData/paired-mat-e2-comp-01-20260926-coverage_guided" "$reportCompData/paired-mat-e2-comp-01-20260926-random_evolution" --output 'D:/hxjh/runs/mat-report-correction-20260926/comp-report.json'
scripts/project_python.cmd scripts/report_summary_delivery_e2.py --fixture summary-delivery-e2 --pair "$reportE2Data/paired-mat-e2-01-20260926-coverage_guided" "$reportE2Data/paired-mat-e2-01-20260926-random_evolution" --output 'D:/hxjh/runs/mat-report-correction-20260926/e2-report.json'
scripts/project_python.cmd docs/reports/20260926-obligation-outcome-recheck.py --data "$reportCompData" --prefix paired-mat-e2-comp-01-20260926 --fixture summary-delivery-e2-completion
scripts/project_python.cmd docs/reports/20260926-obligation-outcome-recheck.py --data "$reportE2Data" --prefix paired-mat-e2-01-20260926 --fixture summary-delivery-e2
git diff --check
```

REPORT-01 的修复前报告使用同两条产品脚本命令，将输出文件名分别改为 `before-comp-report.json`
和 `before-e2-report.json`。每条命令独立检查退出码；非零即停止当前验收，不把部分输出计为通过。
独立 recheck 脚本保留容器/最终化两个视图作对照，其中旧的 “report path” 标签只是缺陷发生时的
说明，修复后不能据此声称产品脚本仍在使用容器视图。

复算前检查输出不存在；若已存在则选择新目录并登记，不覆盖。原输入不变性用
`Get-FileHash -Algorithm SHA256` 对两个证据根的全部文件（含归档和旧报告）生成前后清单，
按相对路径、文件数量和 hash 比较；清单也写入新输出目录。具体命令、退出码和输出摘要记录到勘误报告。

验收状态规则：`REPORT-01` 至 `REPORT-04` 必须分别有命令、输出文件或测试结果作为证据；
任一证据身份、摘要或对账失败时，主 TASK 不得标记 `DONE`。

## 7. 风险、失败信号和回滚

- finalized 与 container 视图的差异是本任务预期现象；若报告与正式 finalized Oracle 复算不一致，或出现未授权字段变化，立即停止并保留两种口径，不改写历史结论。若独立复算与冻结期待读数冲突，记录反证并核查输入，不硬编码期待值。
- 若 finalized 证据摘要、Episode/fixture 身份、receipt 绑定或 checkpoint 对账失败，标记证据无效并停止；不得吞成 `unknown`，不得重新生成旧证据。
- 有效但未闭合的证据由正式 Oracle 逐义务判定；未证明违规且证据不足时保持 `unknown`，已证明违规仍保留。
- 若两轮冻结历史复算引起 W、status、B/R/J/U 或 FindingKey 变化，先回到 SPEC 讨论，不把变化归为报告修复。
- 回滚使用新的撤销提交，保留历史；原始 bundle、checkpoint、归档和旧报告不参与覆盖式操作。

## 8. 后续但不在本任务内的工作

本任务完成不代表产品目标或优越性假设已经得到验证。后续依赖图是：

```text
REPORT-04 收口
  -> 反馈如何改变选择/变异的因果与可学习性诊断（新 SPEC/TASK）
  -> 冻结反馈机制、共同随机数/臂顺序和对照条件（新 SPEC/TASK）
  -> 正式多配对比较与独立攻击阳性控制（新 SPEC/TASK）
  -> 按 AC-06 判断是否支持 coverage_guided 优于 random_evolution
```

下一份 SPEC 应先规定如何定位“反馈为何没有带来可见新增”：反馈输入实际改变了哪些根选择、
父选择、重启或语义变异；改变后的材料是否被读取；是否仍有行动窗口；真实执行如何改变 B/R/J。
局部后代相对父/本臂历史的 `ΔB/ΔR/ΔJ` 是诊断证据，不是反馈有效的唯一入口，也不足以单独证明优势。

正式比较前另行冻结主指标（联合覆盖及效率，B/R 分列）、臂顺序随机化、配对数量、最小效应、
统计检验、失败/超时结算、攻击阳性控制和提前终止规则；共同随机数方案仍需讨论，不能事后选择有利种子。
阳性控制用于证明工具/检测链可达，不能证明真实模型会被攻破，不能混入正式样本或单独支撑引导优越性。
若目标包括更多安全发现，还须单列 W/U 的正式比较，不能用 J 的领先替代。

本 TASK 不拆这些后续 CODE 任务，也不以当前单对 `J_AUC=220` 对 `127` 作为优势证据。
任何后续实验都允许得出无优势或退化的结论；这条路线提供验证与改进方法，不保证引导模式胜出。

## 9. 尚未解决的问题

- 用户已确认按本任务执行；后续正式比较及反馈机制仍待另行决定。
- 正式比较的重复数、最小效应、统计检验、攻击阳性控制和反馈归因口径尚未冻结。
- 服务计价仍待核算。

## 10. 本次草稿交付记录

- 2026-09-26：完成 TASK 文档及只读审查，状态仍为 `DRAFT`；未修改产品代码、执行上述产品验收或连接服务器。
- 已核对关联 SPEC/报告链接、命令所引用文件和四个本地证据根；文档差异检查通过。
- 保留工作区原有未跟踪项 `.pytest-tmp/`、`harness-node-modules.tar.gz`，不纳入本 TASK 提交。

## 11. 执行验收（2026-09-26）

- `REPORT-01`：修复前两轮报告在独立目录复现 `F=0/Q=16、15`；两根输入 SHA256 清单分别覆盖
  84、82 项；修复传参为 `final.artifacts()`。基准源码 HEAD `15a423301b0762359099a60619ed1ec30353bfe3`。
- `REPORT-02`：`tests/integration/test_structured_e2_report.py` 验证已证闭合、未闭合、其他缺证、
  违规与缺证共存、零分母、摘要篡改、收据身份/绑定、checkpoint 错配及 fixture 错配；
  连同完成性诊断、finalization 相邻测试均通过（本地离线脚本端口，无远程模型）。
- `REPORT-03`：两轮各 31 集，报告 `F=16/15、Q=0、W=0`，每义务与独立 formal Oracle 复算一致；
  每轮各 169 个叶子差异仅涉 F/Q 和相关派生字段；原两根输入逐路径、长度、SHA256 不变。
- `REPORT-04`：勘误、历史 TASK 追加勘误、LOG、索引及 HANDOFF 均指向
  [命令/输出/哈希记录](../reports/20260926-summary-delivery-report-correction.md)。
- §6 两轮前后产品报告与独立复算、三组聚焦测试、`compileall`、Ruff、`git diff --check` 通过；
  本任务独立提交，原证据/旧报告不覆盖。不宣称引导优于随机，服务计价仍待核算。
