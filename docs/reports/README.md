# 研究说明与历史报告

- [多路径世界本地实施与验收](20261004-multipath-local-validation.md)：三任务族、九变体、十二条真实工具路径与六十份载体检查；引导采用 LLM 选择，随机采用纯随机采样，零选择器模型调用、零历史读取。仅本地契约与受控工具证据；真实模型多路径、机制对照未验证，正式比较准入仍为 false。

- [实验量程校准](20260930-range-calibration.md)：新 fixture `summary-delivery-range-01` 打开了三类内容类与两种受众关系（旧世界只有 `not_applicable`），两个方向各有一个独立可达落点且 Oracle 各判出一次违规，六个被关闭的路径每个都新增覆盖（未饱和）。但 `attempted` 仍进不了被判定的一集：端口层已能产生该 effect（`send_email` 发给本人，11 条失败调用中 1 条），提交层却使该集无法关闭（`evidence.commit_unknown`），故未满足 `RANGE-01` 的阶段区分要求，**`range_usable=false`**、`formal_comparison_eligible=false`，规格要求的动作是停止正式比较并回到工具路径设计。含[数据](20260930-range-calibration.json)与[复算脚本](../../scripts/range_calibration_probe.py)。确定性校准，不计入 `W`。

- [12集预检三问与G1/G2阶段判断](20260923-preflight-gate-review.md)：真实材料触达/多代反馈已见、两次子代零新增；含[数据](20260923-preflight-gate-review.json)与[复算脚本](20260923-preflight-gate-review.py)，不放行正式G1/G2。

- [旧80集有效变化分析](20260923-g1-effective-variation-analysis.md)：按实际读取内容区分变化触达与未读编辑；含[数据](20260923-g1-effective-variation-analysis.json)与[复算脚本](20260923-g1-effective-variation-analysis.py)。探索性分析，不重判G1。

- [新 TASK 施工前的场景、Agent 自由度与 15×2 完整实验说明](20260922-pre-structured-task-project-and-15x2.md)：面向导师；以实际部署源码和归档为基线，包含 16 种子、19 工具、30 个 Episode 与 8 次准备拒绝。
- [配套完整数据](20260922-pre-structured-task-project-and-15x2-data.json)：完整 comparison、两臂 report、逐代生成、判定、工具参数与返回。历史原件位置见报告附录 D。

这些报告说明特定历史版本，不是当前实现能力声明或新实验的验收证明。
