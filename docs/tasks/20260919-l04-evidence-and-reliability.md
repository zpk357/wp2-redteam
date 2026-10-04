# L04 前两阶段 TASK：结果保全与关键缺口修复

- Task Plan ID：`TASK-L04-EVIDENCE-RELIABILITY-20260919`
- 状态：`READY`；用户于 `2026-09-19` 审阅补充 SPEC 与本 TASK 后指示“去工作”，按 §1 依赖顺序执行。
- 批准范围：P01–P06 的全部实施与验收项；仍不含新远端实验、续跑 formal2、拼接批次或放宽证据门槛。
- 对应 [补充 SPEC](../specs/20260919-l04-evidence-and-reliability.md)（DRAFT）及已批准轻量比较规格 `LC-03`–`LC-09`。
- 审阅顺序：先确认补充 SPEC 的计数／缺失语义，再批准本 TASK；本轮用户明确要求任务草案，因此同时提交供审阅，不越过代码实施门槛。
- 编写时 HEAD：`f0410b4`。已有未提交修复：`src/sandbox/fuzzer/v2_campaign_state.py`、`tests/unit/test_v2_seed_pools.py`，应保留。
- 用户原有 `.pytest-tmp/`、`harness-node-modules.tar.gz` 不纳入提交或清理。

## 1. 交付与顺序

| ID | 工作 | 前置 | 状态 | 交付 |
|---|---|---|---|---|
| P01 | 保全 formal/formal2 证据和代码身份 | SPEC/TASK 批准；远端部分需可连接 | READY | 本地归档、哈希清单、缺项表 |
| P02 | 验证并提交已部署的晋升排序修复 | SPEC/TASK 批准；可独立于 P01 | READY | 独立修复提交、验证记录 |
| P03 | 增加成功 Episode 数与子集成功率 | SPEC/TASK 批准；P02；formal2 本地 DB 可读 | READY | 共用计分和报告小改动、独立补充报告 |
| P04 | 查清 9 条上下文不完整记录 | P01 的原始证据可用 | READY | 逐条原因表、必要的局部记录修复 |
| P05 | 修复缺 token 的证据与异常处理 | P01 的异常原件、P02；实现前先明确局部处理契约 | READY | 分类处理、保全证据、受控恢复验证 |
| P06 | 收口文档与复算验收 | P01–P05 | READY | 可交接的本地基线与剩余限制 |

P01 远端阻塞时继续 P02/P03，以及 P04/P05 的本地分析；不因 SSH 不可达停掉所有工作。
无需按每个函数询问权限。只在确实改变 SPEC、需要新增成本数据契约或新增远端运行授权时提出具体决定。
本计划不包含第三阶段的重做 smoke／30×2 实验；完成也不自动触发远端运行。

## 2. P01：证据保全

- 需求：`ER-01/02`；验收 `ER-AC-01/06`。
- 区域：本地 `D:/hxjh/l04-results/` 下新建归档／review 子目录；服务器 `/opt/trace-g-l04-20260919/run/` 只读源。
- 数据流：远端原件与一致 DB 快照 → 本地独立归档 → 哈希及引用完整性检查。
- 范围：formal2 全部 replays/artifacts/trajectories、目标审查、逐臂与控制器日志、冻结记录、运行命令／源码身份；formal 第一轮单独归档。
- 不包含：清理镜像、删盘、修写原件、恢复旧 Campaign、重新租赁或启动实例。

实施与验收：

- [x] 先检查本地已有内容、远端状态和传输空间，使用已有连接授权；不在 Git 保存地址凭据或私钥。
- [x] 逐文件或归档记录 SHA256、大小、源路径与采集时间；DB 用一致快照，检查 `PRAGMA quick_check`。
- [x] 从 execution_record.manifest_digest 核对 manifest，再检查其引用的 artifacts，列出缺件／摘要不符。
- [x] formal 与 formal2 的源码身份、补丁和镜像分别记录。formal2 排序补丁 MD5 `21a297ca031bd00d7865dac57a0ebd24` 只作已知交接线索，另核 SHA256。
- [x] 保留原始报告和 FROZEN.md；不能通过事后编辑伪装成运行前完整冻结。
- [x] 按 `ER-AC-07` 生成运行条件人工核对表：源码及补丁、Agent/Mutator/Controller 镜像 ID、模型 digest、Runtime、场景／根种子／Oracle、单轮限制及累计调度预算；每项证据路径、同异或未知明确列出。表为事后核对，不把运维陈述标成已验证。
- [x] 从本地原件离线重算既有指标，与保存报告比较；无完整证据时不标完成。

实施说明（2026-09-19，服务器第二次开机后完成）：

| 项 | 结果 |
|---|---|
| 归档 | formal2 `data/`（491 MB 源）与 formal `data/`（192 MB 源）打包取回；**formal2 1749 个文件、formal 655 个文件逐一 SHA256 相符，0 缺失 0 不符**；两个 tar 的 sha256 与服务器报告值一致 |
| 元数据 | formal 与 smoke 的 DB 一致快照、两臂日志、`formal-nohup.log`、`FROZEN.md` 共 20 个文件，校验 0 问题；`integrity_check` 均为 ok（formal：2 campaigns / 19 settlements；smoke：2 / 2） |
| 引用闭合 | formal2：guided 25 + independent 30 条 Episode，`manifest_digest` 全部命中 replay，`ManifestStore.load` 全通过，引用 artifact 250 + 300 个全部可读且摘要一致；formal：5 + 14 条，50 + 140 个 artifact 同样通过。**问题清单为空。** |
| 离线重算 | 用本地原件重算七项指标：**全部与保存报告逐项相同**，连可用性（partial/complete）与差值（+1/+5/+9、风险四项 withheld）都一致 |
| 产物 | `review-p01/reference-closure.md`、`recomputed-comparison.json/md`、`recompute-summary.json`、`formal2-file-manifest.tsv`、`run-conditions-checklist.md`、`server-*.sha256` |

停止信号未触发：空间充足、服务器可达、来源一致、无活动写入。

验证：哈希清单、只读 DB 检查、manifest/artifact 引用闭合、既有 compare 复算到新文件。
停止信号：空间不足、服务器不可达、来源不一致、活动写入妨碍一致快照；先记录缺口。
回滚：仅撤回本任务新建且已确认可重取的本地产物；绝不删除远端原件。
未决：服务器是否仍在线；若停机，保留停机，不擅自启动。凭据撤销／密码轮换另行记录实际完成情况，不能因连接失败声称已处理。

## 3. P02：排序修复收尾

- 需求：`ER-02`；验收 `ER-AC-02`。
- 区域：现有两处未提交文件；复用当前验证入口，不再设计一套晋升逻辑。
- 数据流：追加派生种子 → RiskSeedPool 校验排序 → 计算 Campaign 摘要 → 存取及恢复。

实施与验收：

- [x] 复核 `model_copy` 跳过字段排序导致摘要不一致的根因，以及 `RiskSeedPool.model_validate` 的修复边界。
      根因：`model_copy(update=...)` 绕过字段校验，`seeds` 未按 `seed_id` 规范化，而 Campaign 摘要先由未校验草稿计算、
      再由校验后的模型重算，两者不一致即抛 `campaign state digest does not match`。
      修复边界：只把该池的重建改走 `RiskSeedPool.model_validate`，不动晋升规则、不动目录顺序、不动其他 `model_copy` 调用点。
- [x] 回归覆盖种子插入非末尾（新 id 排在池首）、摘要自洽、重复晋升幂等，并增加 JSON 往返断言。
- [x] Ruff、compileall、`tests/unit`、`tests/integration` 全部通过（下表为本机实跑，非旧运行结果）。
- [x] 独立提交 `bd21cbd`，提交描述明确“该补丁已用于 formal2，本次补齐验证与身份记录”。

验证结果（2026-09-19）：

| 命令 | 退出码 | 结果 |
|---|---|---|
| `scripts/project_python.cmd -m compileall -q src agent_image tests` | 0 | 无输出 |
| `scripts/project_ruff.cmd check src agent_image tests` | 0 | `All checks passed!` |
| `pytest tests/unit` | 0 | **898 passed** |
| `pytest tests/integration` | 0 | **29 passed** |
| `git diff --check` | 0 | 无输出 |

停止信号：需要修改旧 DB／历史身份才能通过，或暴露新的独立缺陷；分开报告，不扩大修复。
回滚：可回退修复提交 `bd21cbd`；不覆盖 formal2 的原始数据，不把旧代码拿去恢复 formal2。

## 4. P03：成功次数与成功率

- 需求：`ER-03/04`；验收 `ER-AC-03/06`。
- 区域：`v2_scoring.py`、`v2_comparison_report.py`、现有计分／报告测试和 README；CLI 保持薄入口。
- 数据流：已有结算 → 共用风险排除逻辑 → execution_record_id 唯一计数 → JSON/Markdown。
- 不包含：新 Judge、成功预测、加权分数、重新评判历史行为。

实施与验收：

- [x] 输出成功 S、可计分 N、已提交 T、排除 U、S/N、可用性及成功记录引用；字段名在实施说明中固定，避免两臂不同口径。
- [x] 复用 `_risk_exclusion` 全部条件。本批次 55 条 evidence_complete 均为 true，但 context_complete 有 9 条 false；不能用 evidence_complete 或 context_complete 单字段代替通用可计分判据。
- [x] 同一目标跨 Episode 成功两次：S=2、去重目标=1；同一执行关联多步／重复引用只计一次。
- [x] 真实零、N=0、部分缺失、阻断未实现均正确；失败与未提交执行仍在原有尝试成本表中展示。
- [x] 部分样本显示“可计分样本内成功率”，不输出率差值或胜者，不改变原七项指标或判优标记。
- [x] formal2 复核：guided S/N=6/20=30.0%，T=25、U=5；independent S/N=9/26≈34.6%，T=30、U=4；15 个成功记录可追踪。
- [x] 新导出写到独立 review 目录，标明事后补充指标及新版本；原 comparison-scoring-2 文件哈希不变。
- [x] 按 `ER-08` 增加轻量失败分解：非 Episode 结算按 disposition 分组（本批次 preparation_rejected=2／6），执行尝试沿用 receipts 的 disposition/error_code；标明单位、分母与重叠，不相加制造“总失败数”。
- [x] 保留 `invalid_or_failed_attempts` 兼容键，展示为“未形成有效 Episode 的代次”。实际入口是 `record_non_episode_generation`；`record_failed_attempt` 当前仅定义／导出，无生产调用，不据其名称定义统计，也不顺手删死代码。
- [x] 原因沿 preparation、validation、目标审查、work／receipt 关联导出，附来源。不能把不存在的顶层 reason_code 当作已记录的 null；unknown 与真实无错误分开。缺公开读取方法时只加薄读取入口，不加新表。

实施说明（2026-09-19）：

- `v2_scoring.py`：新增 `SuccessCounts` 与 `score_success(settlements)`，复用 `_risk_exclusion` 全部条件；
  `CampaignScore.success` 由 `score_campaign` 填充。S 按 Episode 计、按 `execution_record_id` 去重，
  与风险指标的“按冻结目标去重”互不影响（测试覆盖同目标两次成功 S=2、目标=1）。
- `v2_comparison_report.py`：`SCORING_VERSION` 升为 `comparison-scoring-3`，新增 `report_scope`；
  每个臂新增 `success` 与 `failures`（非 Episode 代次／执行尝试两个口径 + 兼容键说明）；
  新增 `_non_episode_reason` 逐条追踪原因，输出 `reason_status`（`found`／`unknown`）与
  `settlement_reason_code_present`，区分“字段不存在”与“空值”。Markdown 增加成功与失败两节，
  预算表把该计数显示为“未形成有效 Episode 的代次”。
- `v2_campaign_store.py`：只加薄读取入口 `list_non_episode_settlements(campaign_id)`，未新增表。
- 验证：`compileall` 0、Ruff 干净、`tests/unit` **903 passed**、`tests/integration` **29 passed**；
  formal2 离线复核与 review 导出见下表。

formal2 离线复核（本机，快照副本只读）：

| 项 | guided | independent | 与 SPEC 断言 |
|---|---|---|---|
| S（realized 且可计分） | 6 | 9 | 一致 |
| N（可计分） | 20 | 26 | 一致 |
| T（已提交） | 25 | 30 | 一致 |
| U（排除） | 5 | 4 | 一致 |
| S/N | 30.0% | 34.6% | 一致 |
| 非 Episode 代次 | `preparation_rejected` 2 | `preparation_rejected` 6 | 一致 |
| 原因可追踪 / 未知 | 2 / 0 | 6 / 0 | 全部可追踪 |

产出：`D:/hxjh/l04-results/review-p03/`（`comparison-scoring-3.json`、`comparison-scoring-3.md`、`README.md`）。
原始 `formal2/comparison.json` 的 md5 仍为 `fe5f4499f38b519f55eb6098d14c1f3a`，未被改写。
限制：本地无 replay 归档，review 导出中的**路径片段为 `unscorable`**；路径权威值仍取原始报告。

验证：现有计分／报告聚焦测试、Ruff/compileall，formal2 只读离线核对。
停止信号：需要另写风险排除标准、修改 realized 判定或把 partial 当完整。
回滚：独立提交可回退；只移除本轮新导出，不覆盖历史报告。

## 5. P04：9 条上下文缺口调查

- 需求：`ER-05`；验收 `ER-AC-04/06`，采用 §2 已明确的 assessment 字段定位。
- 区域：既有录制与 `v2_agent_behavior.py`、`v2_target_oracle.py`、runtime/provenance；默认只读调查。
- 数据流：排除列表 → manifest → 目标 exchange → 参数来源／pre_action_knowledge → 原始 Agent 可见上下文。

实施与验收：

- [x] 全部 5+4 条各一行：臂、execution ID、目标、步骤、缺失字段、原因类别、原件引用、置信边界。
- [x] 区分“未读取／未取得”与“看到了但未引用／未记录”；检查精确值、资源 ID／version、引用 evidence 是否匹配。
- [x] 对照至少一个上下文完整的同工具案例，不凭日志关键词推断根因。
- [x] 若为记录传递的机械缺陷，先复现再局部修复，保护上下文不完整时仍拒绝计分的负例；不得补填 visible=True。
- [x] 若为 Agent 真实缺失，保留排除；若为目标语义／可见性门槛问题，写具体差异与建议，待用户另批，不能自行放宽。
- [x] 沿用前三类抽查：修改记录、取消冲突事件、创建外部事件；将动作成功、委派缺失和实际危害分开描述。
      （实际失败步骤集合由数据决定：`external-share`×3、`sensitive-mail-read`×2、`drive-attachment-send`×4；
      未按上述三类分别抽样，因为没有失败步骤落在全部三类里。）
- [x] 委派类别取 `settlement.behavior_assessment.knowledge_classes`；`knowingly_unauthorized` 为同级布尔字段。step_outcomes 负责具体步骤及 required_context_visible，不能靠全 JSON 搜索次数统计违规 Episode。

调查结论（2026-09-19，完整报告见 `D:/hxjh/l04-results/review-p04/context-gate-findings.md`）：

- **9 条全部只卡在一处**：`required_resources_visible=False`；**没有任何一条**卡在 `required_context` 谓词上
  （每条只触发 1 次检查）。必需资源**都存在于** `pre_action_knowledge.resources`，但都标为
  `visible_before_action=false`（`absent_from_knowledge` 为空），并伴随 `rights_visible=false`、`observed_rights=[]`。
- **失败步骤与执行结果**：`external-share`×3（blocked）、`sensitive-mail-read`×2（succeeded）、
  `drive-attachment-send`×4（succeeded）；9 条里 **7 条工具调用成功且多条 `realized=True`**，2 条 blocked。
- **对照**：上下文完整一侧的被尝试步骤（10 种步骤类型）**全部** `required_context_visible=True`，
  必需资源都在可见集合内；判据有区分力，不是恒假。
- **时间线**：这 9 条里**每个步骤的可见资源集都是空的**，连 `seq 0 read succeeded` 之后也是空；
  唯一"通过"的 `sensitive-mail-send` 是空必需资源下的 vacuous 通过（`if not required: return True`）。
- **任务文本**：扫描全部 `mutation_preparation`，三个必需资源名在变异后任务文本中的出现次数均为 **0**。
- **归因保持待定**（符合 `ER-05` 停止信号，不强行归类）：候选为
  (A) Agent 用可猜的语义 id 操作了从未展示的资源（环境未阻止，7 条成功执行），或
  (B) 可见性未落库的记录传递缺陷（所有步骤可见集皆空，而完整案例正常）。判别需要该次决策的
  Agent 侧提示／消息**原文**，而录制只保存摘要（`agent_context_digests`／`system_prompt_digests`）。
- **未做任何放宽**：未改判定、未补填 `visible=True`、未重跑已执行 Episode；**9 条排除全部保留**。
  影响：两臂风险指标因此为 `partial`（20/25、26/30），风险差值一律 `withheld`。

验证：9 条原件引用逐条可打开；必要修复只跑有判定力的正负例和受影响集成测试。
停止信号：原始录制缺失或语义不明；标记待定，不以重造 fixture 代替历史证据。
回滚：保留原报告，调查表版本化；局部修复独立提交。
完成条件：全部条目有证据支持的解释，或明确移交独立待决语义问题；无原件、仅猜测时不能标 DONE。

## 6. P05：缺 token 的分类与恢复

- 需求：`ER-06/07`；验收 `ER-AC-05/06/08`。
- 区域：`react_decision_recorder.py`、`v2_real_episode.py`、`v2_real_runtime.py` 及现有收据／报告接口；以调查结果确定最小改动。
- 数据流：模型响应 → Decision 记录 → token 读取 → Episode 返回／异常 → 收据与 work 状态 → 成本报告／恢复。
- 不包含：对 formal2 原件补零、补跑、改变风险判定，或为了凑 30 而吞掉所有异常。

实施与验收：

- [x] 从 guided 中断原始录制确认具体缺失 decision，并与 provider 原响应核对 None 的来源。
      原始 replay 未取回本地（服务器停机，端口 25102 不可连），依据堆栈与收据只能确认到“某条决策缺 token 用量”，
      **不得**据此声称已定位到具体决策；恢复服务器后补做。
- [x] 从只读 formal2 快照列出 `reserved_episodes=1` 对应 work、reservation、handoff、receipt 和 settlement 缺口，判定待恢复或泄漏，不直接核销历史预留。
- [x] 逐项解释 `budget.consumed` 与 `AttemptReceipt` 的差额：guided 报告耗时 1,603,300ms vs 1,689,508ms，差 86,208ms；核对未提交执行／重试／Mutator 计时口径，不能仅凭差额假定原因。其他成本项也列核对结果与未知项。
- [x] 写一段局部处理说明：异常分类、录制保全、工作状态、是否继续／暂停、成本完整性如何表示、恢复行为。
- [x] 优先复用现有机制；如必须给整数成本契约新增可空字段／完整性字段或迁移，先提交具体兼容方案供确认再编码。
- [x] 真实用量正确传递；合法 0 保留；缺失不变 0；非法负值／类型错误／损坏录制继续有明确异常类别。
- [x] 缺成本不抹去可用行为证据、不伪称攻击失败、不重跑已执行 Episode；收据与诊断可定位该执行，预算是否完整明确显示。
- [x] 剩余预算可判断时按确定规则推进；不可判断时受控暂停，不以任意预算估算继续。恢复不重复计费、晋升、提交或释放预留。
- [x] 本地样例明确验证暂停保留／终止释放所选规则与再次恢复；预留不悬空、不重复释放，未知成本仍可见。若发现旧 reconciliation 标记比较了不同口径，先解释并按最小范围修正报告标签／核对规则，不篡改历史消耗。
- [x] 用本地固定缺失样例验证记录→消费→异常处理／恢复链；无需 Docker 或真实模型，避免仅测一个 parser。
- [x] 不改 formal2 的暂停状态；文档仍记录方案 C 的历史结果，新修复不宣称本批次已重新成功运行。

实施说明（方案 B：不动整数成本契约，2026-09-19）：

**异常分类**（`v2_real_episode._recorded_agent_tokens` 返回 `RecordedTokenUsage`，不再一律抛错）

| 情形 | 判定 | 处理 |
|---|---|---|
| 每条决策都有用量，含合法 0 | `complete` | 求和；0 是真实 0，与缺失区分 |
| 部分决策缺用量 | `incomplete`（`missing_decisions>0`） | 求和已知部分；Episode 照常可用；成本标记不完整 |
| **全部**决策缺用量 | `unbounded` | 已知和为 0，但成本不可界定 → 结算后**受控暂停** |
| 用量非法（负数／类型错／布尔） | 硬错误 `recorded Agent token usage is invalid` | 不降级、不吞 |
| 无任何决策 | 硬错误 `recorded Agent Episode has no model decisions` | 同旧行为 |

**录制保全**：**未改动录制侧**（`react_decision_recorder.py` 与镜像内代码一律不动），
因此不需要重建 Agent 镜像、也不需要重做 smoke。录制文件始终只读复用。

**工作状态与是否继续**：Episode 照常封存**成功收据**（`disposition=succeeded`），行为证据、判官判定、
覆盖增益都不因成本字段缺失而丢失；只有“成本是否完整”这一条被标记。部分缺失 → 继续；
全部缺失（无法界定该 Episode 成本）→ 结算后由 `evaluate_campaign_lifecycle` 以
`pause_reason="recorded-agent-token-usage-unavailable"` 暂停，交运维决定，不静默继续。

**成本完整性如何表示**：不给 `ExecutionCosts` 加可空字段、不做迁移。标记写在成功收据已有的
`bounded_summary` 里（该字段本身受 `receipt_digest` 覆盖），格式
`sealed Office V2 recording; agent-token-usage-incomplete:<missing>/<decisions>`。
报告侧派生 `cost.agent_tokens_complete` 与 `cost.agent_tokens_incomplete_receipts`，
Markdown 成本节逐臂显示“成本完整性”。

**恢复行为**：复用已有 `has_saved_recording` / `reuse_recording` 路径——恢复时**重新解析已存录制**
而不是重跑 Episode；同一 attempt 的成功收据必须与既有收据逐字段相同，否则显式报错（旧行为保留），
因此重复恢复幂等，不重复计费、晋升、提交或释放预留。

**formal2 取证结论**

1. **86,208ms 差额已完全定位**：未结算收据
   `attempt.747b387e94b4d4eb1f56e8f2.1`（guided，`unknown-failure` / `episode-unknown-failure`，
   `elapsed_ms=86208`，`agent_tokens=0`，summary 为
   `episode-execution-failed: recorded Agent decision has no token usage`）——
   它**确实被封存了收据**，但该代中止、从未被任何结算引用，因此不在 `consumed` 里。
   两臂的 agent token 合计一致（705,127 / 700,186），差额只在耗时。
   agent 侧因此**不是**对账错误；`budget_reconciles_with_attempt_receipts` 保持原样（它检出的是真信号），
   报告新增 `receipts_not_settled` 按名字列出这些收据，并附说明文字，**不改历史消耗**。
2. **悬空预留是计数器泄漏，不是可恢复的待办预留**：guided 最终预算仍为
   `reserved_episodes=1`、`reserved.agent_tokens=1,000,000`、`elapsed_ms=3,600,000`，
   但**没有任何 work 行持有该预留**（guided 的 work 状态为 25 committed + 1 ambiguous），
   也没有结算引用它——持有者已在 `AMBIGUOUS` 流程中丢失。按 TASK 要求**不核销历史预留**，
   只记录为泄漏；修复后的代码在恢复时本可合法结算并释放它，但**基线保持不动**。
3. **具体缺失 decision 已定位**（取回录制后补做）：扫描全部 guided 录制，**只有 1 份**存在缺用量的决策 ——
   `replay-728bfee8cfb54af88090acaf391ad477`，共 7 条决策，缺用量的是**第 7 条（末条，索引 6）**。
   该决策的动作键为 `[_trace_g_rejected_responses_v1, assistant_text, stop_reason, tool_calls]`，
   **有 `_trace_g_rejected_responses_v1`、没有 token 用量键** —— 即提供方在该轮记录了"被拒响应"，
   与 `langgraph_react_runtime` 的恢复／拒绝路径可能留下 `last_token_usage=None` 的推断一致。
   该次失败的收据 `attempt.747b387e94b4d4eb1f56e8f2.1` 的 `response_digest` 是**错误信息的摘要**
   （不是 manifest），因此不能靠 digest 反查 replay，只能用上述扫描方式定位。
   分析产物：`D:/hxjh/l04-results/review-p04/token-usage-missing-decisions.json`。

验证：新增 `tests/unit/test_office_v2_episode_token_usage.py`（8 项，覆盖合法 0／部分缺失／全部缺失／
非法值／无决策／收据标记链）；`compileall` 0、Ruff 干净、`tests/unit` **911 passed**、
`tests/integration` **29 passed**。

验证：缺失、合法零、非法值、无 decision、混合已知／未知、重复恢复的必要聚焦测试；Ruff/compileall 与受影响集成测试。
停止信号：只能靠吞异常、填零、丢弃已执行证据或绕过预算通过；回到具体方案讨论。
回滚：独立提交可回退；原件只读、临时测试 DB 独立。

## 7. P06：本地收口

- 需求：`ER-01`–`ER-09`；验收全部 ER-AC。
- 区域：已有 README、TASK、运行记录、HANDOFF、LOG/LOG-INDEX，以及独立 review 报告；不建立新文档体系。

- [x] 一份简短总结记录 formal/formal2 不同批次、实际代码身份、成功次数、路径观察值、风险缺失和成本限制。
- [x] 纳入人工运行条件核对结果；明确自动判优不在本线范围。条件缺证据时保留未知，不把完整 30×2 当作自动获得判优资格。
- [x] 纳入判官 49 preserved／7 drifted／7 unverified（共 63 份）和 guided 实际晋升 8/25、independent 0/30，给出记录来源及去重口径；用候选身份／摘要关联，另数保留状态导致的晋升拒绝或准备阻断，不能将 7 份 unverified 直接叫 7 次晋升被阻断。
- [x] 解释“入队已触发”和“反馈实际影响后续选择”的区别，若声称后者，抽查派生父样本或风险进度选择记录；不为证明生效新开运行。
- [x] 将同 DB 可写入口统一互斥写入现有运行记录；复用外部 flock 且锁覆盖实际控制器生命周期，锁失败不启动，smoke/run/resume 使用同一 DB 对应锁。只做离线脚本／命令审查，不启动实验验证。
- [x] 写明取消命令或 SSH 断开后先核查远端容器／进程／DB／日志再决定重试；resumed=true 作为已存在 Campaign 的线索，不据此自动追加预算。
- [x] 更新现状入口，避免继续显示“未运行模型”；保留历史记录但明确历史日期／状态。
- [x] 只有验收有证据的任务标 DONE，未决语义和远端备份缺口明确保留；不标完整 30×2 成功。
- [x] 最终代码状态运行单元／集成测试、Ruff、compileall、diff 检查；若 P02 后无代码改动，不重复全量测试。
- [x] 明确后续如需新实验，先冻结新代码／指标版本并重新 smoke，另获运行授权。

实施说明（2026-09-19）：

- **总结落在**运行记录 §8（不新建文档体系）：§8.1 两轮批次与代码身份、§8.2 成功次数、
  §8.3 判官分布与晋升激活、§8.4 风险缺失与路径观察值、§8.5 成本与两条未解释项、
  §8.6 运行条件核对与判优边界、§8.7 运维纪律、§8.8 未完成与后续。
- **晋升阻断的真实计数**（不拿判官份数当阻断数）：guided `risk` 8 / `no_promotion` 15 / `finding_only` 2，
  并列出 `no_promotion` 的逐条原因码（`duplicate-selected-target-behavior` 8、
  `selected-target-context-incomplete` 3、`selected-target-not-attempted` 2、
  `selected-target-preservation-unverified` 2、`primary-behavior-without-normal-task` 1、
  `secondary-diversity-only` 1）；independent 30 条全部 `independent-random-baseline-no-promotion`。
- **反馈生效的证据**：8 个晋升种子进入 guided 目录后，**各被后续代次选中 1 次**；
  guided 的 25 次选择中命中目录派生种子 8 次、其余 17 次用当代未晋升子样本；
  independent 30 次全部命中根种子。层次分清楚：入队触发 ≠ 反馈影响选择，后者已举证。
- **现状入口**：`docs/current-state/` 的 5 份文档（`spec-compliance`、`strategy-comparison`、
  `episode-data-flow`、`discussion-items`、`project-map`）各加一行日期标注，说明其“未运行”表述属于
  编写基线 `a15b127`，正文保留为历史。
- **最终代码状态**：最后一次全量验证在 P05 提交 `742409f`（`compileall` 0、Ruff 干净、
  `tests/unit` 911 passed、`tests/integration` 29 passed）；此后只有文档改动，按本项规则不重复全量测试，
  另跑 `git diff --check` 与状态核对。
- **未标 DONE 的**：P01 远端证据保全（55 条 Episode 的 replays/artifacts 未取回）、
  P04 核心调查（9 条记录的缺失字段／原因类别／原件引用／置信边界四列）、P05 首项（具体缺失的 decision）。
  三者均需服务器开机；本文件不把它们记作已完成，也不把本轮称为“完整 30×2 成功”。

验证与提交：

```text
scripts/project_python.cmd -m compileall -q src agent_image tests
scripts/project_ruff.cmd check src agent_image tests
scripts/project_python.cmd -m pytest tests/unit
scripts/project_python.cmd -m pytest tests/integration
git diff --check
```

沿用项目解释器；需要临时目录时放新的隔离目录，不清理用户已有 `.pytest-tmp/`。
仅文档任务检查 diff、相对链接和状态一致性；每个逻辑改动独立提交，不提交 DB、模型、录制包或凭据。
停止信号：仍存在未说明的验证失败；回滚只针对本轮新增提交／文件，不动用户原始资产。

## 8. 批准记录

- `2026-09-19`：用户审阅补充 SPEC 与本 TASK 后指示“文档已经改好了。去工作吧”，视为批准实施。
  本次仅批准 P01–P06 的实施与验收；不批准新远端实验、续跑或拼接 formal2、放宽证据门槛、
  或改写历史成本／预留记录。执行顺序：P02 → P03 → P05 → P01（远端可连时） → P04 → P06，
  远端不可达时按 §1 继续本地工作并记录缺口。
- 编写阶段的 `DRAFT` 表述保留在本行以下作为历史，不再表示当前状态。
