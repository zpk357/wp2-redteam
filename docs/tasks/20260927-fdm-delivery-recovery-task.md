# SOC-T-DELIVERY-REC-20260927：两阶段任务完成性恢复

- 状态：`DONE`；用户于 2026-09-27 确认 TASK，前期本地验收提交为 `edebcf0`；随后补齐并复验 REC-04 配对 seed：normal-control 的 `--seed` 同时控制无注入候选及真实模型请求。对应 [获批恢复 SPEC](../specs/20260927-fdm-delivery-recovery.md) `REC-02/03/04/05`、[产品 SPEC](../SPEC.md) `G-02/03/04`。真实模型正常控制另见[待审批运行包](20260927-fdm-delivery-normal-control-run-package.md)，尚未运行。
- 前置：新 fixture 文字与本地正常控制诊断可独立于 [父反馈入口 TASK](20260927-fdm-parent-feedback-entry-task.md) 实施；完整两臂入口验收及运行包草案依赖其 `DONE`。本任务完成后，另行审批真实模型正常控制运行包，再决定是否拟定攻击实验运行包。
- 范围：本地 fixture 身份、正式模型适配器的受控端口测试、只读报告和离线验收；不运行服务器、真实模型或 Campaign。不改固定资料正文、可变槽、工具/权限、Agent 系统提示 v2、Mutator revision、Oracle、B/R/J/U、3/4 抽签或两阶段 8+8 预算。

## 1. 数据流及预计修改区域

```text
新调查任务 → Agent 自主搜索/读取共享盘索引、公开与内部来源
→ 原有阶段状态机保留历史工具结果，追加新行动任务
→ Agent 自主补查、交付或诚实停止 → 工具事务与 submit
→ 原 Oracle / judge_normal_control → 只读完成性与安全诊断
```

预计修改：从 `summary_delivery_e2_completion.py` 派生独立 `summary-delivery-e2-completion-v2` fixture 并注册；只改两阶段任务文本和 manifest/完成契约身份。扩展 `scripts/report_summary_delivery_e2.py` 或现有只读诊断入口与相邻测试。必要的 normal-control CLI 参数只用于明确 16 次调用 envelope，不改旧默认 12，也不扩大两臂预算。旧 fixture、归档与 checkpoint 原样保留；新结果用独立目录和新身份。

## 2. 实施 checklist

- [x] 已核查旧/新 fixture 固定世界、索引、ACL、可变槽和两阶段协议；REC-02/03 文字逐项实施，未临时调话术。
- [x] 新 fixture 与 `completion-contract-v2` 独立绑定；正式适配器保留调查真实工具消息并追加新行动任务，无 Controller 代读/代发或工具缩减。
- [x] 只读报告分别记录索引/公开/内部首次成功读取、阶段结束、行动搜索/读取/交付/重复、合法完成、预算截断及原有安全/覆盖/成本字段。内部读取要求成功执行证据、绑定的注册资源曝光与授权主体；没读与证据未知分开。
- [x] 复用已完成的父反馈入口 CLI/恢复/随机隔离测试；正常控制 CLI 使用显式配对 seed 和 16/16 envelope。8 集正常控制独立运行包已写成 `DRAFT`，未执行。

## 3. 验收 checklist 与验证

- [x] 新旧 fixture manifest 不同，base/overlay、固定正文、内部登记值、ACL、索引、四槽相同；新 fixture 两臂共享，随机根选择无反馈。新 manifest `9d724815…`、base `4dc087d1…`、overlay `94cdc878…`、contract `e2e9fb38…`；旧档案未触碰。
- [x] 正式适配器受控端口验证提前结束调查、四条历史工具消息保留及公开交付；原有 two-phase/completion 回归覆盖配额耗尽、缺项、工具阻断、模型停止和预算耗尽，阶段状态机未改。新增脚本 runtime 反例验证内部读取缺失与阻断交付不通过相应完成门。
- [x] 正式 Office runtime/Oracle 相邻回归通过；注册内部来源成功读取与 `judge_normal_control` 分门记录，脚本模型结果仅作机制验证，未宣称真实模型优势。
- [x] 运行包明确 3/4 联合完成门、八集完整证据与 Q=0、配对 seed/顺序、共享成本和停止规则；仍须另行审批并补齐镜像/模型/路径身份，不执行服务器。
- [x] 实际命令、结果、跳过项及风险如下；本任务与运行包草案随一个完成性逻辑改动独立提交。

预定验证：`scripts/project_python.cmd -m compileall -q src agent_image tests scripts`；`scripts/project_ruff.cmd check src agent_image tests scripts`；聚焦 `tests/unit/test_structured_fixture_e2_completion.py`、`tests/unit/test_structured_two_phase.py`、`tests/integration/test_structured_completion_diagnostic.py`、`tests/integration/test_structured_e2_report.py` 及新增 v2/受控端口测试；`git diff --check`。实际新增测试及运行命令在实施记录中明确。文档需人工核对链接、术语与身份。

实际验证（2026-09-27）：`scripts/project_python.cmd -m compileall -q src agent_image tests scripts` 退出 0；`scripts/project_ruff.cmd check src agent_image tests scripts` 全部通过；`scripts/project_pytest.cmd -q tests/integration/test_structured_delivery_recovery.py tests/unit/test_structured_fixture_e2_completion.py tests/unit/test_structured_two_phase.py tests/integration/test_structured_completion_diagnostic.py tests/integration/test_structured_e2_report.py tests/unit/test_structured_normal_control_budget.py tests/unit/test_structured_normal_control_acceptance.py tests/integration/test_structured_fdm_disk_entry.py` 退出 0；随后新增阻断反例，`scripts/project_pytest.cmd -q tests/integration/test_structured_delivery_recovery.py` 为 7 项通过；`git diff --check` 退出 0。镜像、服务器及真实模型正常控制均未运行。运行包待补准确镜像、模型服务、路径及费用后另行审批；完成性提升与攻击优势仍无真实模型证据。

配对 seed 补验（2026-09-27）：复核发现先前 `--seed` 只进入 `prepare_inputs`，未进入 `runner.run_case`。现从同一配对字符串稳定派生正整数推理 seed，并传入正式执行请求，同时在记录中保存字符串与整数；旧/新同 seed 同请求 seed。`scripts/project_python.cmd -m compileall -q src agent_image tests scripts` 退出 0；`scripts/project_ruff.cmd check src agent_image tests scripts` 全部通过；`scripts/project_pytest.cmd -q tests/integration/test_structured_delivery_recovery.py tests/unit/test_structured_normal_control_budget.py tests/unit/test_structured_normal_control_acceptance.py tests/unit/test_structured_two_phase.py` 退出 0；`git diff --check` 退出 0。没有启动模型或服务器。

风险/失败信号：提示引导定位却减少低信任接触、调查仍不收敛、报告把内部读取当违规或把完成率当 ASR、CLI 偷用默认 12 次、旧新结果混算、缺证补样。出现新证据推翻任务假设时停止编码回 SPEC；回滚仅撤销本任务的独立提交，保留旧 fixture 与所有原始证据。

未决：真实模型、镜像、具体 seed/顺序、token/费用/共享截止及服务器路径由后续独立运行包冻结并获批；本任务不授权执行这些运行。
