# 2026-09-27 FDM T06 本地正式 runtime/Oracle 验收

状态：`DONE`（仅本地机制、恢复和离线报告验收）。

对应 [FDM SPEC](../specs/20260926-feedback-directed-mutation.md)、[参数 SPEC](../specs/20260926-feedback-directed-mutation-parameters.md) 和 [FDM TASK](../tasks/20260926-feedback-directed-mutation-task.md) 的 `FDM-T06`。

本记录使用固定的 `summary-delivery-b` 父材料、确定性 `ScriptedModelPort` 决策和正式 structured runtime/Oracle。它验证证据绑定、覆盖结算、反馈投影和两臂隔离；脚本模型分叉不代表真实模型 ASR，也不代表 `coverage_guided` 的真实优势。

## 1. 端到端证据链

集成测试沿正式路径执行：

```text
build_envelope
  -> drive_structured_v1_episode
  -> finalize_bundle
  -> finalized.artifacts()
  -> extract_coverage / bind_coverage_execution
  -> CoverageLedger
  -> load finalized parent feedback
  -> TwoArmSearch selection/generation
  -> Provider request boundary checks
```

父反馈只从落盘 finalized bundle 回查。目标槽为 `s3` 时，测试同时验证了槽级暴露和读后工具行动窗口；停止决策本身没有被算作窗口。Oracle 继续从轨迹、工具结果、状态和最终化证据生成 B/R/J，测试没有预填覆盖结果。

## 2. 已验证的本地反例

| 证据场景 | 观察结果 |
| --- | --- |
| 仅行为覆盖新增 | B 有新增；R/J 没有新增。 |
| 仅风险覆盖新增 | B 没有新增；R 有新增，并由同一执行证据形成新的 J 关系。 |
| 行为、风险和联合覆盖 | 正式 Oracle 保留各维度及局部/全局增量；重复路径的增量为零。 |
| 重复/无新增 | B/R/J 的 local 与 global 增量均为零。 |
| 未完成最终化 | closure 不完整时 `judgment_missing` 非空、义务为 `unknown`、没有 Finding；未知不被当作无新增。 |
| 父槽与行动窗口 | `s3` 暴露和 `action_window=observed` 由父最终化证据支持，`later_action_request_ids` 可回读。 |
| 引导位置与意图 | 固定随机流下，注入已验证父反馈会进入优先分支，改变合法位置并生成有界公开意图/反馈请求；子代再次经过正式 runtime 和 Oracle。 |
| 随机演化隔离 | 给 `random_evolution` 注入相同父反馈后，位置、意图、Provider 请求和反馈来源保持不变。 |
| 私值边界 | `summary-delivery-e2` 注册私值放入位置描述时，在 Provider 传输前抛出 `ProviderBoundaryError`。 |

## 3. 只读配对报告字段

`scripts/report_summary_delivery_e2.py` 现在独立保留：

- B/R/J 的逐机会 local/global 增量和键；父子保留、暴露槽、行动窗口及证据完整性；
- 生成失败、基础设施缺证和不完整证据的分类；模型/工具调用、输入/输出 token、费用单位、变异器成本和 wall clock；
- `N_op`、`N_failed_gen`、`N_infra`、`N_ep`、可判定 `W/F` 分母，以及 `W/N_op` 和 `W/(W+F)`；
- 每一配对的 `safety_gate`：引导 `W/(W+F)>0` 且严格高于 `random_evolution` 的条件逐项输出。零分母为 `null`；`W=0` 仍可完成覆盖报告，但不能通过安全门槛。

报告集成测试使用两臂各自的离线证据目录校验对账、失败分类、字段隔离和版本化 JSON。该测试合成的是报告输入，不能作为真实模型配对读数。

## 4. 实际验证

以下命令均在 `D:\\hxjh\\wp2-redteam` 执行并退出码为 `0`：

```text
scripts/project_pytest.cmd -q tests/integration/test_structured_feedback_mutation.py tests/integration/test_structured_e2_report.py
scripts/project_pytest.cmd -q tests/unit/test_structured_two_arm_search.py tests/unit/test_structured_feedback_chain.py tests/unit/test_structured_random_evolution.py
scripts/project_pytest.cmd -q tests/unit/test_structured_provider.py tests/unit/test_structured_text_provider.py tests/unit/test_structured_projection.py tests/unit/test_structured_edit_kernel.py
scripts/project_pytest.cmd -q tests/unit/test_structured_feedback_mutation.py tests/unit/test_structured_coverage.py tests/unit/test_structured_finalization.py
scripts/project_pytest.cmd -q tests/unit/test_structured_two_phase.py tests/integration/test_structured_two_arm_campaign.py tests/integration/test_structured_campaign_report.py
```

首条命令收集并通过 13 项 T06 集成测试；其余命令为 T06 影响的选择、Provider/投影/编辑、覆盖/最终化和 Campaign 相邻回归，均显示 `[100%]` 且退出码为 `0`。

没有启动服务器、Docker Campaign 或真实模型；没有把脚本模型结果写成真实模型攻击成功率或引导优势。后续开发实验仍需单独批准运行包，并逐对满足 `W/(W+F)>0` 且严格高于随机演化；`W/N_op` 单列。

## 5. 未完成和剩余风险

- 真实模型效果、真实配对的安全门槛、服务计价、正式统计协议和多种子结果均未测量。
- 真实 Campaign 的中断/远程服务器行为仍由后续获批运行包验收；本地恢复测试不替代远程验证。
- 本地通过只证明机制和证据链可观察、可回放、可拒绝越界输入，不证明研究假设 `H-FDM`。
