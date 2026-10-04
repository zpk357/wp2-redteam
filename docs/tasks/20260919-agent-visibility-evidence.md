# Agent 可见性证据补强：TASK

- Task Plan ID：`TASK-AGENT-VISIBILITY-EVIDENCE-20260919`
- 状态：`READY`（已执行，2026-09-20）；用户于 `2026-09-20` 指示"执行吧"，V01–V04 全部展开。
  早先的 `DRAFT` 表述保留为历史，不再表示当前状态。实施说明见文末。
- 对应：[SPEC](../specs/20260919-agent-visibility-evidence.md) VE-01–VE-06。
- 基线：`eba27dd`；编写前跟踪文件干净，仅有用户原有 `.pytest-tmp/`、`harness-node-modules.tar.gz`，不得清理或提交。
- 承接 P04 的未解归因；P01–P06 已有实现与归档不重做，不按文档旧 READY/DRAFT 标记重复施工。

## 1. 最小交付与依赖

| ID | 工作 | 前置 | 状态 | 交付 |
|---|---|---|---|---|
| V01 | 还原旧输入、确定真实证据缺口 | SPEC/TASK 批准；本地归档 | READY | 9 条还原矩阵、完整对照与记录位置决定 |
| V02 | 补齐最小输入证据或复用既有引用 | V01 | READY | 输入证据落盘、关联读取、兼容处理 |
| V03 | 验证可见性与来源链路 | V02 | READY | 本地正反例、重试关联、重放兼容证据 |
| V04 | 归因结论与下一轮交接 | V01–V03 | READY | 简短调查结论、真实运行前检查说明 |

调查可读完整归档，修改仅限确认的记录／来源传输范围。无需为每个常规接线选择询问用户。
只有改变判据、授权模型、核心录制协议含义或引入新服务时，才先提交具体差异供决定。
本轮不连接／启动服务器，不重跑 historical Episode，不重新做 30×2，不新增运行条件数据库。

## 2. V01：先用旧证据还原

- 需求 VE-01/02；验收 VE-AC-01/05。
- 输入：`D:/hxjh/l04-results/formal2/`、已取回原件及 `review-p01/`、`review-p04/`；先根据归档清单定位真实文件，不假设 artifacts 在某个固定子目录。
- 区域：只读 manifest、model_decisions、checkpoints、state artifacts、工具录制、任务执行绑定、部署源码身份。
- 数据流：9 条排除 execution → 目标调用 → 对应 decision/checkpoint → 输入 messages → 引用的资源证据。

实施与验收：

- [x] 复用现有 P04 排除清单，列全部 5+4 条执行 ID、目标、失败步骤、必需资源与调用序号；核对一例完整对照，不重写计分器。
- [x] 检查每个 before_checkpoint_id 对应记录的 recoverable 状态及 state_artifact，读取 agent_state.messages/prompt；不存在、不可读、摘要不符分别记录。
- [x] 以部署版本和规范化规则核对 decision.input_digest；重建成功才标“精确 Recorder 输入”，仅找到近似状态则标部分还原。
- [x] 同时追踪冻结 task bindings／Agent workspace context、先前工具输出、来源 evidence IDs；资源不在变异文本中不能作为从未展示的证明。
- [x] 核对 Provider 消息转换、追加收尾提示和重试，明确旧输入摘要覆盖到哪里，哪些最终请求内容仍不可还原。
- [x] 输出每条“动作前展示 / 引用来源 / runtime 可见性 / context gate / 归因”矩阵，UNKNOWN 保持可见；不再用单一字符串搜索替代还原。
- [x] 写一段最小实现决定：已有信息足够则 V02 只补读取／关联；确有缺件则列保存位置、字段、摘要、写入失败处理及旧格式兼容。不要先造新记录体系再寻找用途。

验证：只读摘要对照、原件引用可打开、至少一个通过案例与失败案例的时间线核对；不调用 LLM。
停止信号：部署版本不明或归档不闭合，先列缺口，不用当前模板回填历史；无法还原不阻止为未来留证继续 V02。
回滚：只移除本任务新建的调查输出；原件不改。

## 3. V02：最小输入留证

- 需求 VE-03/04；验收 VE-AC-02/04/05。
- 候选区域：`agent_image/app/replay/react_decision_recorder.py`、`state_codec.py`、`recording_session.py`（按实际入口定位）、
  `agent_image/app/adapter/langgraph_react_runtime.py`，以及必要的 Artifact/Manifest 导出读取。
- 数据流：真实输入位置 → 有序记录或不可变引用 → 已有录制封存／导出 → 本地按 decision 读取。
- 不包含：用户界面、通用 Provider 代理、HTTP 抓包、改变模型提示或调用顺序、Harness 全面改造。

实施与验收：

- [x] 优先引用 checkpoint 已存 messages；若要保存新请求，采用现有 ArtifactStore 的序列化／摘要方式，schema 与持久化扩展保持可选、向后兼容。
- [x] 明确 execution_id、decision_index、before_checkpoint_id 和 Provider request/attempt 序号的对应关系；同一 decision 的失败／重试请求不混淆。
- [x] 保存实际有序角色／正文／工具返回及 tool schema（或完整引用）；记录 Recorder 与 Provider 应用层最终请求是否不同。不能只保存初始 system prompt。
- [x] Provider 若追加收尾指令／格式恢复提示，捕获变更后的请求；请求失败但已发出时仍有记录，不只在成功返回后追加。
- [x] 不重定义旧 decision.input_digest、不往 action 塞数据导致旧 output_digest 失效；新产物参与已有完整性引用检查。
- [x] 通过真实录制出口导出，并从导出目录读取；不能只把消息留在内存对象或运行容器内。
- [x] 记录缺失／引用损坏／写入失败可定位，partial/unavailable 不冒充完整。合成消息不进普通日志或 Git，不记录传输授权头、密钥、环境变量。
- [x] 旧归档未带新字段仍可读；LangGraph 新留证覆盖范围明确，Harness 不受破坏但不宣称新增留证已覆盖它。

验证：录制落盘与读取的聚焦测试、旧 manifest 兼容、摘要篡改检测；无需真实模型或 Docker。
停止信号：需要改变 Replay 主协议含义／重写所有旧 manifest 才能实现；给出最小兼容方案再讨论。
回滚：独立提交可回退；已有录制与数据库不迁移。

## 4. V03：验证可见性与参数来源

- 需求 VE-02/04/05；验收 VE-AC-02–05。
- 区域：现有 React 录制／重放测试、Office V2 session/runtime/provenance 与 P04 读取辅助；复用 harness，不另建模拟平台。
- 数据流：模型替身看到实际 messages → 返回工具动作 → 正式来源解析 → 真实工具运行时 → knowledge／Oracle → 录制后离线核查。

实施与验收：

- [x] 正例：资源先展示并以合规来源使用；输入记录、ArgumentSource、pre_action_knowledge 和 context gate 能逐项对应。
- [x] 反例：资源从未展示；证据记录足以证明在该输入边界内不存在，不得因工具执行成功补造“此前可见”。
- [x] 差异例：消息确实展示资源，但参数来源缺失／不匹配；审计必须标为“展示与来源链不同”，不能误称从未展示，也不修改原 gate 使其强行通过。
- [x] 时间反例：资源仅出现在该次调用结果中；下一步可能可见，本步调用前不可见，关联正确。
- [x] 覆盖 Provider 正常调用和一次追加提示／恢复重试；实际提交的 messages 与所存引用摘要可核对。
- [x] 若定位到生产来源传递的机械缺陷，先复现再小修，并保留上述负例；涉及是否认可任务绑定为可见来源等判据语义，另行确认。
- [x] 旧录制严格重放、缺少新证据的读取、录制封存失败均有明确结果；不通过虚构原文让历史摘要对上。

验证：有判定力的现有单元／集成测试；fixture 只替代模型生成，不替代待验证的消息组装、来源解析或判定结果。
停止信号：测试通过依赖硬填 visible=True、跳过 Oracle、关闭摘要校验或重写历史；停止该路径。
回滚：修复与测试小步提交；保留历史报告与风险排除。

## 5. V04：收口与下一轮交接

- 需求 VE-06；验收 VE-AC-01–05。
- 区域：已有 review-p04 相邻的新审计输出、HANDOFF、运行记录、docs/README 与本 TASK。
- 不包含：新增成功指标、自动判优、正式实验命令执行。

- [x] 9 条历史记录分别给出最终解释或待定原因，区分“原文缺失”“可还原但来源丢失”“Agent 未取得所需信息”；引用可验证证据。
- [x] 原件只读；若新证据改变我们对案例的解释，写追加审查结论，不改 formal2 原报告或补算成更有利成绩。
- [x] 明确任务完成条件是“未来同类问题能审计”，不是“9 条全部改成可计分”。历史无法还原可保留未知，但必须验证未来记录足够。
- [x] 统一新旧任务入口状态：已完成代码与未解历史归因分开记录，避免 READY（已执行）／DRAFT 的矛盾。按实际授权和验收更新，不仅因勾选数量改状态。
- [x] 写下一轮运行前检查：固定源码／镜像／指标版本；每臂一 Episode smoke 时核查输入留证、token 完整性标记、录制导出和互斥；必要时使用预先指定的目标补充诊断，不能挑有利结果冒充随机实验。
- [x] 在未来观察记录中保留“各风险首次达到 3 级”的栏位与定义：使用结算后的状态，以已提交 Episode 序号计；另记 generation_index，未达到写未达到。只作为辅助观察，本任务不实现新的评分模块。
- [x] 明确本任务不启动 smoke/30×2；当前成本 unknown／partial 仍按原规则报告，运行条件可由带证据的人工冻结表核对。

验证与提交：Python 改动执行 compileall、Ruff、受影响单元和录制／重放／来源链集成测试；覆盖跨模块后最终运行 tests/unit 与 tests/integration。
纯调查／文档步骤只检查链接、摘要与 `git diff --check`。已通过的全量检查在代码未变化时不重复。

```text
scripts/project_python.cmd -m compileall -q src agent_image tests
scripts/project_ruff.cmd check src agent_image tests
scripts/project_python.cmd -m pytest tests/unit
scripts/project_python.cmd -m pytest tests/integration
git diff --check
```

每个逻辑改动独立提交，仅暂存本任务文件，不提交本地录制包、数据库或用户原有未跟踪文件。
完成后列出验证证据、剩余未知与影响范围；远端实验必须另获授权，不能靠本任务 DONE 自动开始。

## 6. 批准记录

用户同意开展上述小范围工作的方向，并于本轮要求“写成 task”。当前交付为规格／任务草案，尚未实施。

## 7. 实施说明（2026-09-20）

- **产出位置**：调查与验收产出在 `D:/hxjh/l04-results/review-ve/`
  （`v01-reconstruction.md/.json`、`v01-original-hashes.json`、`v02-decision-input-check.json`、
  `v03-archive-recheck.json`、`v04-attribution.md`）；代码与测试在仓库，提交 `c7c2d05`（V02）、
  `bb6d0d8`（V03）；运行记录追加 §8.9。
- **V01**：9 条全部"精确还原"——`before_model` checkpoint 的 state artifact 与 Recorder 输入同源，
  `input_digest` 用规范化规则逐位重算相符；缺口定位为两类（引用识别失配 4 条、首个动作无工具
  来源 5 条）；对照案例 `ba3559b1`（complete）核对通过。
- **V02**：新增读取器 `src/sandbox/replay/decision_input.py`（缺 checkpoint／缺 artifact／不可读／
  无消息列表／摘要不符五种状态显式报告）与 `resource_mentions` 审计检索；按既有模式记录
  Provider 追加提示 `_trace_g_appended_prompts_v1`。全量校验：56 份录制 / 254 条决策全部 `exact`。
  - **覆盖边界如实说明**：Provider 请求序号以**追加序列位置**表达（第 i 条追加 ⇒ 第 i+1 次请求），
    失败原因沿用既有 `_trace_g_rejected_responses_v1`；tool schema 的收缩（submit-only）由追加标识
    与 `model_start.available_tools` 共同索引，完整 schema 由冻结代码版本提供（引用而非复制）；
    本留证覆盖 **LangGraph runtime**，DeepSeek Harness 不受破坏但**未**新增留证；
    "录制封存失败"（`recording_complete=false`）在 manifest 中，读取器据缺件报告，不单独成状态。
- **V03**：先复现两端失败，再以 `resource_reference_matches` 小修（生成与验证同口径：引用带版本
  则精确匹配，文件级引用接受同资源任意已观察版本）；四类情形测试（正例／反例／差异例／时间例）
  与负例（任务绑定不算来源，保留待确认）均落地；离线复核显示 4 条历史记录可正确建链且对照无回归。
- **V04**：`v04-attribution.md` 逐条归因（0 条"原文缺失"、0 条"Agent 未取得所需信息"、
  4 条"可还原但来源链在旧代码下丢失"、5 条判据语义待定）；运行记录 §8.9 追加审查结论并更正
  "只存摘要"的旧表述；下一轮运行前检查与"风险首次达 3 级"观察栏位已写入。
- **验证**：`compileall` 0；`scripts/project_ruff.cmd check src agent_image tests` 干净；
  `tests/unit` **935 passed**、`tests/integration` **29 passed**；`git diff --check` 干净。
- **后续决定与实施（2026-09-20，同日）**：用户确认"任务直接给出的资源参考值算作可见来源"。
  提交 `7e2efa6` 让冻结绑定证据参与来源推断（标识字符串与资源引用映射两种引用形态，
  生成与验证同口径，已被工具结果认领的路径保留更具体来源）；离线复核
  `review-ve/v05-binding-recheck.json` 显示 **9 条全部可建链**（5 条经绑定、4 条经工具结果）。
  该改动只影响未来运行，需**重建 Agent 镜像**后生效；历史判定不变。
- **保留的负例**："没有对应绑定的资源引用"仍判不可见（`test_a_citation_without_a_binding_...`、
  `test_a_citation_outside_the_bindings_...`）；"展示但未引用仍不算来源"——引入任务绑定与工具结果
  之外的新来源类型仍须先更新 SPEC 再动代码。
- **边界**：未启动 smoke/30×2；未改 formal2 原件、报告与历史判定；运行仍须另行授权。
