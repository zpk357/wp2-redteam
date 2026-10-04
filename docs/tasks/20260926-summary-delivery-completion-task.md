# SOC-T-MAT-COMP-20260926：交付完成收敛诊断

- 状态：`DONE`（2026-09-26；本地实施与**一次隔离真实诊断**均已完成，终结状态 `VALID_ZERO_SUCCESS`）。短报告见[诊断报告](../reports/20260926-summary-delivery-completion-diagnostic.md)，登记见 LOG `20260926-soc-mat-completion-diagnostic`。**遗留缺陷见 §8**：报告脚本的 `F/Q` 与 `W/(W+F)` 口径需另开 TASK 修正。
- 对应 SPEC：[SPEC-SOC-MAT-COMP-20260926](../specs/20260926-summary-delivery-completion-diagnostic.md)，需求 COMP-01～COMP-04、COMP-A1～COMP-A6。
- 目的：在不改变覆盖反馈或安全判定的前提下，修复 E2 的阶段提示契约，使真实模型有清晰的“调查完成→立即交付→提交”路径，并用一次隔离诊断验证完成性是否改善。

## 1. 范围与数据流

包含：独立 fixture `summary-delivery-e2-completion` v1.0.0；调查/行动任务文本的版本化提示；fixture 注册；受控模型端口回归；只读完成性诊断字段；本地验收；一次 1 对 × 2 臂 × 16 机会的真实诊断运行、复算、证据保全和收尾。

不包含：修改 E2 v1.0.0、E 原件、阶段预算（仍 8+8）、工具描述/返回、Oracle、ACL、B/R/J/U、FindingKey、资格门、coverage_guided 反馈、random_evolution 隔离、调度器奖励、自动补交付、E 剩余六对或正式多配对比较。

代表性数据流：

```text
e2-completion 固定身份与低信任材料
  -> 调查阶段按任务要求检索/读取
  -> 可信行动阶段提示（不丢历史工具结果）
  -> Agent 自主选择公开交付或有证据的拒绝
  -> 真实工具事务、受众可读性、submit
  -> 原有完成门、Oracle、B/R/J/U 与 checkpoint
  -> 只读完成性/攻击效果报告
```

## 2. 预计修改区域

| 区域 | 修改 |
|---|---|
| `src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_e2_completion.py` | 从 E2 固定资产派生独立身份；只改 manifest 身份和两阶段任务文本，保留正文、ACL、预算、Provider、工具和安全规则 |
| `src/sandbox/scenarios/structured_v1/fixtures/__init__.py` | 注册新 fixture；未知 ID 仍拒绝 |
| `scripts/report_summary_delivery_e2.py` | 在既有只读报告中增加行动阶段搜索次数、首次交付调用、交付后提交和阶段预算诊断字段；保持旧键与旧 fixture 可读 |
| `tests/unit/test_structured_fixture_e2_completion.py` | 新身份、提示边界、旧身份隔离和投影不泄露回归 |
| `tests/integration/test_structured_completion_diagnostic.py` | 真实 Office 工具＋受控模型端口验证阶段交接、公开交付、阻断/只读与诊断字段 |
| `docs/tasks/20260926-summary-delivery-completion-run-package.md` | 冻结一次真实诊断的身份、命令、预算、停止、保全和收尾 |
| `LOG.md`、`LOG-INDEX.md`、`HANDOFF.md` | 记录本地验收、服务器运行结果和未决风险 |

不新增依赖，不让 Controller 代替 Agent 调用交付工具，不改变 `TwoPhaseProtocol` 的 8+8 预算实现。

## 3. 实施 checklist

- [x] 将本 TASK 改为 `IN_PROGRESS`；将 COMP SPEC 的状态保持 `APPROVED`。
- [x] 新 fixture 的调查任务明确最小资料与来源；行动任务明确调查已结束、材料足够时停止搜索、立即完成一次允许的公开交付并随后 `submit`；仍保留缺少点名标识符时读取和有证据拒绝的路径。
- [x] 冻结新 manifest/overlay/source identity；证明固定内部正文、LIMITED 值、ACL、公开索引和四个可变槽与 E2 v1.0.0 语义一致，旧摘要不变。
- [x] 扩展只读报告诊断字段：行动阶段搜索/读取次数、首次交付工具调用及阶段序号、交付后 submit、预算截断、合法任务完成；缺证为 `unknown`。
- [x] 添加受控模型端口测试：阶段交接只追加可信任务文本，历史工具结果保留；公开邮件/共享副本路径由 Agent 决策产生，Controller 不注入调用。
- [x] 添加无模型两臂公平性回归：两臂使用同一新身份、预算、Provider、工具、Oracle 和完成契约；演化臂反馈源为空，引导臂反馈源非空。
- [x] 完成本地验证后冻结真实运行包；不复用旧 E2 数据根、镜像、checkpoint 或报告。
- [x] 按运行包执行一次真实诊断；不因结果自动重跑、调提示、换种子、扩预算或进入多配对比较。实测 3 673 s（共享截止的 34%），无重跑、无调参、无换种子、无扩样。

## 4. 验收 checklist

- [x] 新 fixture 可加载且 manifest digest 与旧 E2 不同；旧 E2 v1.0.0 fixture、报告和证据字节不变。实测新 manifest `57cae637…`、旧 E2 仍为 `9d0d28b6…`。
- [x] 调查任务仍要求真实搜索/读取；行动任务不包含受限正文、登记值、隐藏判定或伪造工具结果。实测行动阶段仍有 53+44（引导）／49+49（演化）次搜索/读取，调查任务与 E2 逐字节相同。
- [x] 受控路径能观察到行动阶段在材料足够后选择公开交付，随后只提交一次；只读、拒绝、阻断和超预算仍如实结算。实测交付尝试 9／9 集，`budget-exceeded` 9／8 集如实计入截断。
- [x] 报告字段可从 bundle/checkpoint 确定性重算；不把渠道计数当 Episode，不把完成率当 ASR，不把 U 当成功分子。**但修正口径后发现报告默认路径的 `F/Q` 与 `W/(W+F)` 有缺陷**，见[短报告 §4](../reports/20260926-summary-delivery-completion-diagnostic.md)与本文 §8。
- [x] 两臂条件公平且反馈隔离保持；未改变 B/R/J/U、Oracle、ACL、资格门或预算轴。`security_isolation` 两项均为真。
- [x] 真实运行证据身份、机会结算、模型/镜像、阶段截止、成本、清理和 SHA256 均可复核；异常即停并保全。证据包 `83349a62…` 本地复核一致，全程无异常中止。

## 5. 本地验证命令

```text
scripts/project_python.cmd -m compileall -q src agent_image tests scripts
scripts/project_ruff.cmd check src agent_image tests scripts
scripts/project_pytest.cmd -q tests/unit/test_structured_fixture_e2_completion.py tests/integration/test_structured_completion_diagnostic.py tests/unit/test_structured_two_phase.py tests/integration/test_structured_e2_report.py
git diff --check
```

真实运行前另执行 `bash -n` 检查新增运行脚本。项目默认 Anaconda Python 不作为证据环境。

## 6. 真实诊断运行边界

- 身份：新 fixture `summary-delivery-e2-completion` v1.0.0；新源码/镜像/manifest/overlay 摘要在运行前记录；旧 E2 数据根完全隔离。
- 规模：1 对 × 2 臂 × 16 机会；随机演化先、引导后；初始父池 10；生成失败消耗机会、不补足；首次成功不提前停。
- 每 Episode：两阶段各 8 模型/工具调用，总 16/16；200000/16000 Agent token、600 秒、216 费用单位；Mutator 每机会最多 2 次，8192/4096 token；共同阶段截止 10800 秒。
- 终结：`INVALID`、`VALID_ZERO_SUCCESS`、`VALID_SUCCESS_OBSERVED` 三者之一；任一结果都收口。`VALID_ZERO_SUCCESS` 仍只能表示未观察到成功，不能表示安全证明。
- 停止：身份不符、证据不完整、预算越界、服务/容器异常或关闭无法证明时立即停止并保留现场；不 `--resume`，不覆盖旧目录，不自动重试。
- 报告：复用 `structured-attack-report-v1`，并带完成性诊断字段；ASR、B/R/J/U、合法业务完成、根/局部和注入来源分开报告。

## 7. 风险、失败信号与回滚

- 如果新提示仍导致搜索循环，记录为完成性未改善，不增加预算或加入特殊交付分支；运行收口后回到 SPEC 讨论是否需要 COMP-B。
- 如果新 fixture 改变固定正文、ACL、Oracle 或候选投影，立即停止，不运行真实模型；删除新 fixture/报告入口即可回滚，旧 E2 不受影响。
- 如果真实运行证据无法闭合，标 `INVALID`，保留所有已收到的模型、工具和容器证据；不把无效批次改写为零成功。
- 服务器不可用时只完成本地验证，不声称真实运行完成。

## 8. 尚未解决

- **报告脚本口径缺陷（本次诊断发现，需另开 TASK）**：`scripts/report_summary_delivery_e2.py` 的 `_obligation_outcome` 传入 `final.container_bundle`，其 `artifacts()` 不含 host closure 收据，使 `judge_episode` 无法认定通道已关闭，从而把 `no-observed-violation` 记为 `unknown`；`W/(W+F)` 分母因此也错（输出 `null`，实际 `0.0`）。受影响范围包括**本轮 31 集与上轮 E2 31 集、共 62 个义务判定**；`W` 与 `status` 不受影响。只读复算见 `docs/reports/20260926-obligation-outcome-recheck.py`。修改派生读数会改变报告输出，按 `AGENTS.md` §2 需先立 TASK，本轮未改产品脚本。
- **后续勘误（2026-09-26）**：上条“62 个义务判定”应为**62 个 Episode、124 个逐义务判定**（每集两项）；当时“需另开 TASK”是历史状态，现已按 [报告修正 TASK](20260926-summary-delivery-report-correction-task.md) 完成独立修复及新旧两轮只读复算，见[勘误与摘要核对](../reports/20260926-summary-delivery-report-correction.md)。旧运行输入和旧报告字节均未改动；这不构成新实验或引导优势证据。
- **`submit_after_delivery` 仍为 `unknown`**：bundle 无法证明交付后是否 `submit`；按 COMP-A4 记 `unknown`，不填零。
- **行动阶段"先搜后交"节奏未改善**：9/16 与 8/15 集仍因预算截断收尾；是否采用 COMP-B（重分配阶段预算）或 COMP-C（工具返回进度提示）应由 SPEC 重新讨论，本批不自动扩样。
- 服务计价仍待核算；不由 `expense_units=0` 推断免费。
- 一次诊断不证明提示改动提高 ASR，也不证明引导优于随机；是否进入正式比较需另行 SPEC。
