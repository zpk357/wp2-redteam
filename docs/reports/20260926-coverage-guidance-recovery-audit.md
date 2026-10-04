# 覆盖引导改进：当前机制与证据审计

- 日期：2026-09-26；只读审计基线：`1987333d7ff55120d16b32c5d7b6b8b17fb3bfe5`。
- 目标：公平预算下提高引导相对随机演化的联合覆盖与效率；W/U 单列，不把攻击非零作为覆盖比较前置条件。
- 本轮未改产品代码、未连接服务器或调用模型；内存反例是选择器单元验证，不是真实 Agent 执行证据。
- 后续决定：上述目标描述保留为审计当时口径；2026-09-26 用户批准的 [反馈变异 SPEC](../specs/20260926-feedback-directed-mutation.md) 已将逐对 `W/(W+F)` 非零且引导高于随机演化设为本轮实验整体通过的硬门槛。`W=0` 时仍完成并报告覆盖比较，但实验不能判通过。

## 1. 新证据不支持继续重复攻击措辞改造

GEN-A（目标化）和 TECH（八手法）已经执行，分别在引导第 1 集、引导尚未开始时被终止，均为
`INVALID`。见 [TECH 局部报告](20260926-attack-technique-diversity-partial.md)与
[GEN-A 运行记录](../tasks/20260926-attack-generation-targeting-run-package.md)。它们不能检验引导与随机优劣，
也不能排除引导算法的问题。HANDOFF 历史句“瓶颈既不在覆盖引导算法”超出证据，不采用该排除判断。

当前 Agent 系统提示已经更新为 v2；[运行记录](../tasks/20260926-agent-prompt-v2-run-record.md)仍记为 READY、
尚未执行。本审计不把 v1 结果外推到 v2，不自动执行该包，不撤销用户已有提示改动。

## 2. 已复现的选择器缺陷：没有尝试其他可用覆盖维度

[反馈契约](../specs/20260921-structured-scenario-feedback-contract.md) `SOC-FBK-09` 规定：
轮到的索引为空或全部冷却时，按循环顺序检查其他启用维度；全部不可用才转根。

当前 `src/sandbox/structured_v1/search.py` 的 `select()` 只扫描当前 `dimension`，
随后在 `if scheduled or not eligible` 分支直接根重启。未实现其他维度回退。

内存复现使用正式 fixture、`prepare_inputs` 和既有 `structured_coverage_helpers.record_coverage`
构造明确标记的合成选择器见证，保持候选/manifest/执行身份校验；选择 seed 为 `fallback-audit`，
将当前方向置于非计划根的索引 4：

```text
non_scheduled_index=4
requested_dimension=risk
available_dimensions=['behavior']
has_valid_behavior_witness=True
result_root_restart=True
reason=no-eligible-parent
```

即有有效且未冷却的行为父见证，当前实现仍重新随机生成。该反例证明契约偏差；
不证明修复一定提高真实模型覆盖。

从 COMP-A 原始两臂 checkpoint 只读统计 `selections[].reason`：

| 选择收据 | 引导（16） | 随机演化（15；另一次生成失败） |
|---|---:|---:|
| scheduled-root-restart | 7 | 7 |
| feedback-ranked-unit / uniform-parent-pool-draw | 5 | 8 |
| no-eligible-parent | 4 | 0 |

目录为 `D:/hxjh/runs/mat-e2-comp-01-20260926/repo/data/structured-v1/`。
此处是已存选择收据数，不把缺少生成失败收据解释为没消耗机会。
尚未逐机会重建那 4 次的其他维度资格，不能把 4 次全部归因于此 bug。

## 3. 首版反馈能力的明确上限

- `search.py` 的 `_generate()` 仍调用共同 `choose_edit(parent, manifest, random_state)`，随机选择操作和位置。
- `generation.py` 将 `selected_dimension` 写入 MutationPlan，但没有将它或历史观察传给 `prepare_texts()`。
- `provider.py` 的 `ProviderTextRequest` 没有覆盖反馈字段；当前目标化、轮换攻击手法两臂共用。
- `SOC-FBK-11`、`SS-010`、`MAT-05` 明确冻结“反馈选父、共同随机编辑”；这是设计范围限制，不能误报为完全没有反馈。

因此新版本最直接的研究改进是：让反馈进一步影响合法编辑位置和文字变异意图，
用可审计公开摘要连接到 Provider，同时保持随机拥有同样的编辑/表达能力。

## 4. 需单独澄清的结算问题

`campaign.py` 给 `search.record()` 的 `parent_id` 是本次执行子代 ID；`search.py` 又用该 ID
累计 `parent_no_new`。正常每个子代只结算一次时，源父的连续无新增次数不会因此累积。
现有测试通过对同一 ID 多次 `record` 验证冷却，与真实多子代使用方式不同。

需要明确 `SOC-FBK-12` 的源父/执行输入计数语义，并用同父多个不同子代的实际结算回归验证。
本轮未完成该反例的执行验证，不将其计为第二个已复现 bug，也不混入定向变异效果结论。

## 5. 决策

优先修契约偏差，再开发反馈直接影响变异的新版本；不再把统一强化攻击措辞作为引导相对优势的主要办法。
保留既定 J 成本曲线主终点，B/R/W/U 分列；历史正差值、单对领先和受控测试均不能替代新条件下完整配对比较。
候选方案及明确边界见 [反馈变异 SPEC](../specs/20260926-feedback-directed-mutation.md)。
