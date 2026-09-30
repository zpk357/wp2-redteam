# TASK：修复未提交尝试的 Effect 证据路径

- 任务 ID：`ATTEMPT-EFFECT-20260930`
- 标题：让真实未提交交付调用进入 `attempted` 风险阶段
- 状态：`DRAFT`
- 对应 SPEC：[20260930-attempted-effect-evidence.md](../specs/20260930-attempted-effect-evidence.md)
- 需求：`ATTEMPT-01` 至 `ATTEMPT-05`

## 1. 目标

修复 `ToolCallReport.produced_an_effect` 对未提交调用的过滤，使已经到达交付通道但既未提交也未被阻断的真实调用进入 transition effect，并由现有 coverage 生成 `event_kind=attempted`。

## 2. 包含范围

- 修改 `src/sandbox/structured_v1/session.py` 的 effect 生成条件或等价共享抽象。
- 必要时更新最小的 session/coverage 聚焦测试。
- 更新 range probe，使其通过真实 session 证据观察 `attempted`，不手写风险键。
- 更新量程 JSON/报告及 `RANGE-CAL-20260930` 任务验收状态。

## 3. 不包含范围

- 不修改 `capture_effect()` 的 effect schema、Oracle 六元组或事件枚举。
- 不修改 `EpisodeArtifacts.deliveries()` 的已完成交付语义。
- 不修改 blocked、committed、read-only、post-submit 的既有语义。
- 不修改 fixture、模型、预算、coverage-guided 或 random 策略。
- 不启动正式 guided/random campaign。

## 4. 数据流与实现边界

```text
Tool runtime result
-> ToolCallReport(committed/blocked/channel/post_submit)
-> produced_an_effect
-> session.capture_effect
-> StructuredTransitionRecord.effects
-> coverage._effect_risk_facts
-> event_kind=attempted
```

只允许以下新增 effect 条件：`not post_submit and not committed and not blocked and channel in {MESSAGE, SHARED_STORAGE, PUBLIC_LINK}`。`ACTOR_PRIVATE` 无创建对象的调用仍只保留 transition/exposure。

## 5. 实施 checklist

- [ ] 添加一个未提交交付通道调用的 session 聚焦测试。
- [ ] 添加 attempted effect 的 coverage/Oracle 断言。
- [ ] 添加 read-only 与 post-submit 不生成 effect 的回归断言。
- [ ] 保持 blocked/committed 现有测试通过。
- [ ] 运行 range probe，确认 `attempted` 来自真实 effect。
- [ ] 更新报告：`range_usable` 是否通过必须由完整事件集合重新计算。
- [ ] 仅在所有验收项满足时更新 `RANGE-CAL-20260930` 状态。

## 6. 验收 checklist

- [ ] MESSAGE 未提交未阻断调用生成一个 `committed=false, blocked=false` effect。
- [ ] 该 effect 出现在 transition record 中，coverage 风险键第五项为 `attempted`。
- [ ] 该 effect 不出现在 `EpisodeArtifacts.deliveries()` 中。
- [ ] blocked、committed、read-only、post-submit 行为无回归。
- [ ] range probe 报告不再缺少 `attempted`，且风险键由真实证据生成。
- [ ] 旧 fixture digest 和历史证据不变。
- [ ] 正式实验仍未启动；预算和机会数仍未冻结前不得标记正式准入。

## 7. 验证命令

```text
python -m pytest tests/unit/<attempt-focused-tests>.py tests/unit/test_structured_range_calibration.py -q
python -m compileall -q src agent_image tests
scripts/project_ruff.cmd check src agent_image tests
python scripts/range_calibration_probe.py --fixture summary-delivery-range-01
git diff --check
```

## 8. 风险与回滚

- **失败信号**：读调用产生 delivery effect；post-submit 进入 effect；blocked 被误读为 attempted；attempted effect 被计入 deliveries/W；旧测试失败。
- **处理**：停止正式实验，回到 session effect 条件；不得在 probe 中补写风险键。
- **回滚**：只回滚本 TASK 修改，保留量程 fixture 和历史报告。

## 9. 尚未解决的问题

- 需要由现有工具构造一个真实的 `committed=false, blocked=false` 交付通道结果；若 Office 工具没有该状态，必须在受控合成 ToolPort 中验证 session 语义，同时把真实工具不可达继续记录为独立风险。
