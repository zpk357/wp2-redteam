# Office V2 覆盖率变异展示前端

这是 Campaign 封存证据的只读展示层。它不启动 Agent、Mutator、Docker 或 Campaign，
也不修改 Coverage、Corpus、Scheduler、Mutation feedback 或运行数据库。

## 当前数据

`data/latest.json` 来自已核验的真实服务器 Campaign
`office-v2-real-g3-98478fd`，源码版本为
`98478fd629d3004b84f5f5af83b20470efafb57c`。当前归档包含：

- Qwen3.5 27B Mutator 的三次真实候选生成与宿主验证；
- Office V2 LangGraph Agent 的三个完整 Episode；
- 每代完整 TRACE 时间线、Coverage 结算、Feedback 血缘和种子晋升；
- 累计 71 个主要行为特征和 1 个风险上下文。

`scripts/build_office_v2_coverage_ui_snapshot.py` 是通用只读转换器，不绑定三代。
后续 5/10/20 代归档可用同一命令更新：

```powershell
python scripts/build_office_v2_coverage_ui_snapshot.py `
  --archive-root <已解压归档目录> `
  --campaign-id <Campaign ID> `
  --archive-sha256 <归档 SHA-256>
```

转换器会验证核心归档文件、ReplayManifest、Recording/Oracle 事实、实际 Agent
输入和每代 CoverageDelta；任何身份或结算不一致都会拒绝生成。

## 页面语义

- 页面默认使用中文读者视图；摘要、内部 ID、状态码和原始 JSON 只有打开
  `技术细节` 后才会出现，原始证据不会被改写或丢弃。
- 页面按冻结的四个 Office V2 风险大类组织 A01-A12 初始目标种子。Scheduler 先选择
  风险/行为前沿及具体目标里程碑，再从兼容种子中选择本代父种子；这些关系由转换器从
  冻结风险目录和逐代 Campaign 状态恢复，不在浏览器中写死。
- A01-A12 是具体风险目标的初始种子，不是四个风险大类本身。实际风险上下文仍只由
  Episode 工具轨迹、Oracle 和状态变化结算。
- `初始基线` 不计入 Campaign 代数，保存冻结任务、初始种子池和 G1 选择结果。
- `Generation 1` 的上一代 Feedback 为空，但仍会从初始基线选择父种子并调用 Mutator。
- 后续代际必须引用上一代 `feedback_output.digest`。
- `candidate_settlement` 必须有完整 Episode；`non_episode_settlement` 不得带 Agent 输入、
  TraceEvent 或 Coverage 增量。
- Agent 直接任务文本与写入模拟办公环境的候选内容是两个独立字段；直接任务载体中两者相同。

## 启动

```powershell
python scripts/serve_office_v2_coverage_ui.py
```

默认地址是 `http://127.0.0.1:8765`。`--snapshot` 可指向其他 v2 快照。
自动更新每 5 秒读取一次已验证快照；同名 Campaign 身份变化、代数回退或 Feedback
血缘不闭合时，浏览器拒绝替换当前视图。
