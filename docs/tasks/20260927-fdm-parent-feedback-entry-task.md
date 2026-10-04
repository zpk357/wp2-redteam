# SOC-T-FDM-ENTRY-20260927：真实入口父反馈与恢复补齐

- 状态：`DONE`；用户于 2026-09-27 确认 TASK，本地实施与下列验收已完成。对应 [获批恢复 SPEC](../specs/20260927-fdm-delivery-recovery.md) `REC-01/04/05`、[FDM 主 SPEC](../specs/20260926-feedback-directed-mutation.md) `FDM-02/03/04/06/07/08`、[参数 SPEC](../specs/20260926-feedback-directed-mutation-parameters.md) `FDM-P02/03/04/06`。
- 前置：FDM-T01～T06 已完成；本任务完成后，[交付恢复 TASK](20260927-fdm-delivery-recovery-task.md) 可做完整入口验收。两项各自提交，不续跑 `fdm-dev2`。
- 范围：仅本地机制、checkpoint/恢复、只读诊断与脚本/受控 runtime 验收。不改 Agent 提示、固定资料、Oracle、B/R/J/U、概率、意图目录、随机演化的候选空间或预算；不启动服务器或真实模型 Campaign。

## 1. 数据流及修改区域

```text
父候选执行 → CoverageExecutionIdentity / 覆盖结算 → finalized bundle 落盘
→ 新机会按摘要重验所选源父 → 已验证槽窗口 / 完整无窗口 / 缺证
→ 引导位置和公开意图 → Provider → Agent 自主执行 → 最终化与 checkpoint
→ 下一个机会；随机演化不读取跨 Episode 父证据
```

预计修改：`scripts/run_structured_v1_episode.py` 的 two-arm CLI 循环、`src/sandbox/structured_v1/campaign.py` 的选择及恢复边界、`search.py` 的已选父证据装载接口与版本身份；必要时扩展既有只读报告及相邻单元/集成测试。复用 `load_parent_feedback()`、`resolve_parent_feedback()`、`SelectionReceipt`、checkpoint 和现有 bundle 校验，不新增依赖或独立调度服务。实施前核对旧身份与新身份的恢复兼容性并记录版本变更。

## 2. 实施 checklist

- [x] 任务开始改为 `IN_PROGRESS`，检查工作区及适用代码；确认真实 CLI 没有调用已有 loader，恢复先覆盖搜索状态。
- [x] 正式入口传入 finalized 目录；选中局部源父后、位置与意图抽样前按摘要重验，并将反馈与收据持久化。根机会及随机演化不触发该加载。
- [x] 在恢复搜索状态后装载父证据；待选收据恢复时再次重验。既有提交后隔离与结算测试保持通过；最终化文件尚未落盘时下一局部机会按缺证结算。
- [x] 缺失或不完整均消耗机会、保存零请求收据且不生成；完整无槽暴露回共同抽样；损坏身份隔离停止。失败机会的冷却时钟只推进一次。
- [x] 报告分列父证据失败、已验证反馈、优先资格、实际分支、文字请求及无文字操作；不以 `feedback_sources` 代替命中证据。

## 3. 验收 checklist 与验证

- [x] 真实 CLI 入口传入 finalized 目录；受控 Office 父证据从磁盘形成有资格优先集合并写入收据，完整无暴露回共同分支。既有反馈变更/随机隔离反例回归通过；未运行真实模型。
- [x] 既有 B-only、R-only、J-new、重复/无新增及正式 B/R/J 回归通过；新增完整无暴露、缺失、不完整和损坏证据反例通过。
- [x] 既有 reservation、selection、generation、submission、receipt、settlement 与隔离恢复回归通过；新增待选收据恢复时删除父文件的反例通过，未重复执行或计费。
- [x] Office 受控 runtime 与报告相邻回归通过；脚本模型证据只作机制验证，不推断真实模型优势。
- [x] 实际命令、结果与边界如下；本任务独立提交。

预定验证：`scripts/project_python.cmd -m compileall -q src agent_image tests scripts`；`scripts/project_ruff.cmd check src agent_image tests scripts`；聚焦 `tests/unit/test_structured_t05_recovery.py`、`tests/unit/test_structured_feedback_chain.py`、`tests/unit/test_structured_two_arm_search.py`、`tests/integration/test_structured_two_arm_campaign.py`、`tests/integration/test_structured_refused_child_settlement.py`，以及新增真实入口跨机会/恢复集成测试；`git diff --check`。具体新增测试名与命令在实施记录中写明。

实际验证（2026-09-27）：`scripts/project_python.cmd -m compileall -q src agent_image tests scripts` 退出 0；`scripts/project_ruff.cmd check src agent_image tests scripts` 全部通过；`scripts/project_pytest.cmd -q tests/integration/test_structured_fdm_disk_entry.py tests/unit/test_structured_t05_recovery.py tests/unit/test_structured_feedback_chain.py tests/unit/test_structured_two_arm_search.py tests/unit/test_structured_feedback_mutation.py tests/unit/test_structured_random_evolution.py tests/integration/test_structured_two_arm_campaign.py tests/integration/test_structured_refused_child_settlement.py tests/integration/test_structured_e2_report.py tests/integration/test_structured_campaign_report.py` 退出 0；补充运行 `scripts/project_pytest.cmd -q tests/integration/test_structured_fdm_disk_entry.py` 为 8 项通过；`git diff --check` 退出 0。首次报告回归因新增父证据字段的旧键集合断言失败，更新断言后复测通过；首次夹具直接篡改最终化摘要被正式校验拒绝，改为落盘后损坏字节，复测通过。未运行服务器或真实模型 Campaign。

风险/失败信号：缺证被降为无窗口、随机臂读取反馈、Provider 先于证据重验、恢复覆盖投影、重复请求/计费、旧 checkpoint 被静默接纳，任一出现均不标 `DONE`。回滚为撤销本任务独立提交并保留已生成证据；旧运行根和 checkpoint 不迁移、不覆盖。

未决：无需求参数待决；实现中若发现需要改变 `FDM-P06` 失败口径、恢复语义或实验身份，停止编码返回 SPEC 审阅。
