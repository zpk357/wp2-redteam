# Office V2 覆盖率变异展示前端

这是 Campaign 封存证据的只读展示层。它不启动 Agent、Mutator、Docker 或 Campaign，
也不修改 Coverage、Corpus、Scheduler、Mutation feedback 或运行数据库。

## 当前数据

`data/latest.json` 是明确标注的本地确定性合同 Fixture，不是服务器 Campaign 结果。
它覆盖同一 `TraceEvent 1.2 + CoverageInput` 展示合同下的两种 Runtime：

- DeepSeek Harness：三代，前两代提交 Episode，第三代在宿主验证阶段被拒绝。
- LangGraph Agent：两代，事件数量和工具路径与 Harness 不同。

收到服务器完整归档后，应由只读归档转换器发布相同 v2 合同；不能把 Fixture 改名冒充归档。

## 页面语义

- `初始基线` 不计入 Campaign 代数，保存冻结任务、初始种子池和 G1 选择结果。
- `Generation 1` 的上一代 Feedback 为空，但仍会从初始基线选择父种子并调用 Mutator。
- 后续代际必须引用上一代 `feedback_output.digest`。
- `candidate_settlement` 必须有完整 Episode；`non_episode_settlement` 不得带 Agent 输入、
  TraceEvent 或 Coverage 增量。
- Agent 直接任务文本与写入模拟办公环境的候选内容是两个独立字段。

## 启动

```powershell
python scripts/serve_office_v2_coverage_ui.py
```

默认地址是 `http://127.0.0.1:8765`。`--snapshot` 可指向其他 v2 快照。
自动更新每 5 秒读取一次已验证快照；同名 Campaign 身份变化、代数回退或 Feedback
血缘不闭合时，浏览器拒绝替换当前视图。
