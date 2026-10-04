# SOC-T-FDM-20260926：反馈定向变异本地实施

- 状态：`DONE`；用户于 2026-09-26 明确批准本 TASK 拆解，T01 开始实施。
- 对应规格：[主 SPEC](../specs/20260926-feedback-directed-mutation.md) `FDM-01..10`、[参数 SPEC](../specs/20260926-feedback-directed-mutation-parameters.md) `FDM-P01..06`。
- 依据：[恢复审计](../reports/20260926-coverage-guidance-recovery-audit.md)；任务编写基线 `4f80c9e`。
- 实施范围：本地机制、持久化、离线正式 runtime/Oracle 验收、报告与交接。不启动服务器或真实模型 Campaign。

## 1. 目标、边界和数据流

让引导臂实际消费已验证父执行证据改变下一轮合法位置和公开文字意图；随机演化臂使用同一候选集合、Provider、意图目录、执行环境和成本口径，不消费跨 Episode 反馈。先修空索引回退，再逐步接入新机制。沿用现有 Pydantic 合同、合法编辑核、bundle loader、Provider 尝试收据、Campaign checkpoint；不新增依赖或调度平台。

```text
已执行父候选 + 覆盖执行身份 + 已落盘最终化 bundle
 -> 启用维度回退/选父 -> 按槽回查暴露和行动窗口
 -> 同一合法位置全集（引导 3/4 优先、1/4 共同；随机共同核）
 -> 公开意图 + 有界反馈 -> 完整请求校验 -> Provider/最多一次修复
 -> 准入与真实材料差异 -> Agent 自主执行 -> 最终化证据
 -> 原 Oracle/B/R/J/U -> 局部新增/源父冷却/持久化 -> 下一轮
```

包括新请求/选择/计划/恢复身份及只读诊断；不修改 Agent prompt v2、权限、fixture 固定资产、Oracle、覆盖键、FindingKey、工具能力或攻击手法目录。既有 `random_independent` 保持无跨 Episode 反馈；主公平对照为 `random_evolution`。新算法只用于新 Campaign，旧 checkpoint 不静默升级。

规格批准不等于实验运行授权。`FDM-09` 的两对开发 Campaign、token/费用/截止、种子与臂顺序仍待独立运行包审批；正式比较还须单独冻结统计协议。

## 2. 依赖与推进顺序

本 TASK 已获用户确认；依赖满足的子任务进入 `READY`，开始实施时转为 `IN_PROGRESS`。每项验收有证据后才转为 `DONE`，每个逻辑改动单独提交。

| ID | 标题 | 前置依赖 | 被依赖项 | 状态 |
|---|---|---|---|---|
| FDM-T01 | 空索引回退及缺陷回归 | 两份获批 SPEC、TASK 确认 | T02、T03、T04 | DONE |
| FDM-T02 | 父最终化证据回查与公开反馈合同 | T01 | T03、T04、T05 | DONE |
| FDM-T03 | 反馈位置选择与共同文字意图 | T02、T04 | T05 | DONE |
| FDM-T04 | 源父结算、重复签名与独立冷却 | T02 | T03 的重复分支最终验收、T05 | DONE |
| FDM-T05 | Campaign 持久化、恢复及公平性贯通 | T03、T04 | T06 | DONE |
| FDM-T06 | 正式 runtime 本地验收、报告与交接 | T05 | 后续独立实验运行包 | DONE |

执行顺序为 T01 → T02 → T04 → T03 → T05 → T06；T03 在 T04 之后完成重复观察的接入，避免占位结果进入验收。

## 3. 子任务合同

### FDM-T01：空索引回退及缺陷回归

- 需求：主 SPEC `FDM-01/08`；范围为选维度/父数据流，不包含新反馈或概率。
- 修改区域：`src/sandbox/structured_v1/search.py`；现有 selection、two-arm、feedback-chain 单元测试。
- 实施 checklist：
  - [x] 保存空 R、有效 B/J 的修复前反例；在缺陷基线上确认预期断言失败。
  - [x] 按启用 R/B/R/J 循环查找合格索引，重复 R 去重；保留方向和根日程时钟。
  - [x] 收据分别记录计划维度、实际维度与回退原因；仅计划根或全空时根重启。
- 验收 checklist：
  - [x] B-only、R-only、J-only、全空、冷却及身份不合格父均按冻结规则选择；随机臂选择不变。
  - [x] 聚焦测试及相邻日程测试通过，记录修复前失败与修复后成功。
- 验证：§4 的 selection 回归组与公共检查。
- 风险/失败/回滚：若必须改变维度定义或日程才能回退，停止回到 SPEC；以独立撤销提交回滚本项，不覆盖旧数据。
- 未决：无需求待决；测试函数名由实施时按现有结构确定。

### FDM-T02：父证据回查与公开反馈合同

- 需求：`FDM-03/05/06/07`、`FDM-P02/03/06`；范围为父身份 → 最终化证据 → 有界投影，不包含概率选择。
- 修改区域：`projection.py`、`provider.py`、`bundle.py` 的调用边界、`host_runner.py`/既有运行入口；可新增小型反馈合同模块及对应单元测试，不更改底层证据定义。
- 实施 checklist：
  - [x] 通过容器 bundle_digest 定位持久化最终化 bundle，复用 loader 重验摘要、父候选、manifest、配置和 coverage 身份。
  - [x] 回查 ExposureFact → 工具交易 → action_request_id → 有序模型决策，按槽证明暴露及后续工具行动；停止决策不算窗口。
  - [x] 白名单构造公开字段；宿主保存来源引用、容器/最终化摘要、投影与请求摘要。unknown 不当否定事实。
  - [x] 每次生成和修复前校验公开投影摘要、序列化请求与最终 system/user 消息；修复不接收无效输出正文。
  - [x] 父证据缺失/仍不完整失败结算；摘要或身份错误显式拒绝；完整但无暴露保留独立证实字段并回退抽样。
- 验收 checklist：
  - [x] 宿主补齐 complete 的父可用；仅有覆盖键、错误暴露摘要、错父/配置或缺最终化 bundle 不可伪造可用反馈。
  - [x] A 槽窗口不能借给 B 槽；ExposureFact.sequence 不替代模型轮次。
  - [x] 正常和修复请求的私值/隐藏字段注入均在传输前拒绝；Provider 不见宿主证据引用或原始 R/J 键。
- 验证：§4 的新增反馈测试、provider/projection/finalization 相邻回归与公共检查。
- 风险/失败/回滚：任何证据绑定错误、私值泄漏或需修改 Oracle 的方案立即停止；撤销本项新合同接入，保留旧 loader 与旧归档，不迁移旧 Campaign。
- 未决：无参数待决；现有运行入口如何提供按摘要加载的只读回调须在实施记录中列明，不新增服务。

### FDM-T03：位置选择与共同文字意图

- 需求：`FDM-02..05/08`、`FDM-P01..04`；范围为已验证反馈 → 位置/意图 → 请求 → 单次编辑，不改变候选空间。
- 修改区域：`search.py`、`edit_kernel.py`、`generation.py`、`provider.py`、`text_provider.py` 与对应测试。
- 实施 checklist：
  - [x] 两臂复用 `legal_positions`；引导固定 3/4 优先分支、1/4 共同核；分支内先均匀操作再均匀位置，无优先则共同核。
  - [x] 固定位置到目标槽映射：移动任一相关槽合格即可、引用只看源槽；发请求前确定目标，生成后核验实际变化。
  - [x] 两臂共同四意图目录；随机均匀且无历史反馈；引导先过目标槽暴露/行动窗口门槛，再按重复或 R/B/J 映射。
  - [x] 根和无文字操作 `intent_id=null`；无文字操作不加调用；拒绝不重抽，尝试和 token 如实入计划。
  - [x] 收据保存集合摘要、概率版本、随机流、选择位置及证据来源；字典顺序不影响抽样。
- 验收 checklist：
  - [x] 双槽、移动源/目标、引用反例均正确；共同分支可选未读槽；固定随机流改变合法父证据可改变引导选择/请求。
  - [x] 未知、重复但无目标槽暴露、仅停止决策均走共同意图抽样；B-only/R-only/J-new 映射有证据。
  - [x] 注入跨 Episode 历史不改变随机演化位置、意图和最终请求；两臂合法集合完全相同。
  - [x] 文字不变/拒绝、格式修复、无文字操作的机会和调用成本守恒。
- 验证：§4 的反馈 mutation 测试、edit-kernel/random-evolution/provider 相邻回归与公共检查；用可控随机抽签验证分支，不用小样本频率代替 3/4 算法验证。
- 风险/失败/回滚：随机臂读历史、集合不等或请求前要求子代结果时停止；撤销新版本接入，不修改旧计划或已落盘请求。
- 未决：无需求待决；接口字段布局和版本号已登记为 `structured-fdm-position-v1`、`structured-intent-v1`。

### FDM-T04：源父结算、重复与独立冷却

- 需求：`FDM-01/05/07/08`、`FDM-P02/05/06`；范围为子代真实执行 → 局部覆盖 → 源父/单元状态，不包含生成能力变化。
- 修改区域：`search.py` 的 record/状态、`campaign.py` 的结算参数、相邻 coverage/feedback-chain/two-arm 测试。
- 实施 checklist：
  - [x] 分离执行子代身份和选择收据源父；以持久收据归因，根无源父/单元计数。
  - [x] 定义同源父不同已结算子代语义签名，排序 B/R/J 与逐义务结果，排除实例、摘要及证据编号。
  - [x] 新增有完整单元见证即重置选中计数；无新增只在最终化完整、judgment_missing 空、除 termination 诊断外 not_admitted 空时累计。
  - [x] 父和单元独立达到三次，分别归零并冷却十次；选择先判资格再递减，第十一次恢复。
  - [x] 所有不同子代与冷却时钟恰好一次；根/失败消耗时钟，不完整无新增不增加次数；其他父/单元不变。
- 验收 checklist：
  - [x] 同源父不同子代达到阈值；换父/别名不清单元计数；父触发不误暂停未达阈值的单元。
  - [x] 部分证据有独立新覆盖可重置，部分证据无新覆盖保持计数；固定 termination 不阻断正常无新增。
  - [x] 三次、十次、第十一次边界和结算重放有断言；随机臂不读结果冷却。
- 验证：§4 的新增结算测试、two-arm/coverage/finalization 回归与公共检查。
- 风险/失败/回滚：源父误归因、未知算无新增、重复收费或时钟漂移即失败；以新撤销提交回滚，不删除覆盖或违规事实。
- 未决：无冻结参数待决；旧重复诊断保留历史身份，不能重算旧档案冒充新规则。

### FDM-T05：持久化、恢复与公平性贯通

- 需求：`FDM-04/06/07/08`、`FDM-P02..06`；范围为选择/请求/执行/结算各中断边界，不新增并行 Campaign 调度。
- 修改区域：`campaign.py`、`search.py`、`generation.py`、`host_runner.py` 及已有运行入口/checkpoint 测试。
- 实施 checklist：
  - [x] 新协议身份包含算法、反馈和概率版本；旧 checkpoint 继续按原身份读取，禁止新算法静默恢复。
  - [x] 选择收据、父证据引用、随机状态、完整尝试、子代身份、预算和冷却按现有持久化链保存。
  - [x] 最终化证据落盘后才能作为下一轮父反馈；恢复从落盘证据读取，不依赖 runner 内存缓存。
  - [x] 扩展 reservation、selection、generation、submission、receipt、settlement 故障注入；串行完成待结算机会再选下一次。
- 验收 checklist：
  - [x] 恢复重现同一位置/意图/请求，已执行或结算子代不重复执行、调用、计费或冷却。
  - [x] 缺父 bundle 失败且不发请求；身份损坏拒绝；超时、拒绝、取消均保全用量与已有违规。
  - [x] 随机与引导共享预算、修复上限及失败机会口径，随机未消费跨 Episode 反馈。
- 验证：§4 的新恢复集成组与 campaign/two-phase 相邻回归；记录每个故障边界恢复前后身份及成本。
- 风险/失败/回滚：无法确认关闭、必要用量缺失或预算对账失败时按既有受控停止，不重跑掩盖；新协议隔离后撤销接入，旧归档不覆盖。
- 未决：无需求待决；真实服务器故障验证留给获批运行包，本项离线证据不替代远程验证。

### FDM-T06：本地端到端验收与交接

- 需求：`FDM-08..10`、`FDM-P01..06`；范围为正式 Office runtime/Oracle 的本地受控端口、只读报告及持久记忆，不运行真实模型实验。
- 修改区域：新增本地反馈变异集成测试；必要时扩展 `scripts/report_summary_delivery_e2.py` 的诊断字段；`docs/reports/`、本 TASK、`README.md`、`HANDOFF.md`、`LOG.md`、`LOG-INDEX.md`。
- 实施 checklist：
  - [x] 固定候选/随机流，仅改变获准父历史，演示下一代决策和真实工具状态/B/R/J 分叉；使用正式 runtime 和 Oracle，不预填结果。
  - [x] 覆盖仅新 B、仅新 R、新 J、重复/无新增、未知及私值拒绝；脚本模型只用于机制验证。
  - [x] 报告独立列 B/R/J、局部/全局增量、父保留、暴露/窗口、失败及调用/token/费用；W/F/Q 与两个分母沿用正式 E2 口径。
  - [x] 逐对验收逻辑为引导 W/(W+F)>0 且严格高于随机；零分母 null、W=0 继续覆盖比较但不通过；Q/不完整不能作完整效果验收。
  - [x] 记录所有命令、测试数量、跳过项、提交及剩余风险；README 只写已验证能力，交接清楚区分本地机制通过与真实效果未测。
- 验收 checklist：
  - [x] 反馈改变下一轮且随机不变的证据完整；脚本分叉不被称为真实模型违规成功率或引导优势。
  - [x] 聚焦、相邻、跨模块集成及公共检查通过；每个子任务均有可追溯验证记录。
  - [x] 后续实验运行包所需字段和预算待决项列明；不启动服务器，不事后改主终点或筛掉落后配对。
- 验证：§4 全部本地组、报告边界测试、git 差异及文档链接检查；报告合成数据仅验证门槛逻辑。
- 风险/失败/回滚：状态/B/R/J 不分叉或真实反馈未进入下一轮则不能 DONE；报告错分母/遗漏失败须修正；撤销派生报告改动但保留原证据。
- 未决：服务器开发预算/模型身份/种子/臂顺序、正式 token 区间/统计规模和服务计价仍待运行包或实验 SPEC 决定，不属于本地机制通过结论。

## 4. 验证命令与证据纪律

以下命令已在当前工作区执行；新增测试文件名与实际路径一致。每条命令单独核对退出码，失败不标通过。

```text
scripts/project_python.cmd -m compileall -q src agent_image tests
scripts/project_ruff.cmd check src agent_image tests scripts
scripts/project_pytest.cmd -q tests/unit/test_structured_two_arm_search.py tests/unit/test_structured_feedback_chain.py tests/unit/test_structured_random_evolution.py
scripts/project_pytest.cmd -q tests/unit/test_structured_provider.py tests/unit/test_structured_text_provider.py tests/unit/test_structured_projection.py tests/unit/test_structured_edit_kernel.py
scripts/project_pytest.cmd -q tests/unit/test_structured_feedback_mutation.py tests/unit/test_structured_coverage.py tests/unit/test_structured_finalization.py
scripts/project_pytest.cmd -q tests/unit/test_structured_two_phase.py tests/integration/test_structured_two_arm_campaign.py tests/integration/test_structured_campaign_report.py
scripts/project_pytest.cmd -q tests/integration/test_structured_feedback_mutation.py tests/integration/test_structured_e2_report.py
git diff --check
```

现有 provider、projection、edit-kernel、campaign、two-phase 测试路径已用 `rg --files tests` 确认。上述五组聚焦/相邻命令均退出 `0`；最后一组收集并通过 13 项 T06 集成测试。缺陷基线回归使用隔离 checkout 或最小反例，不覆盖当前工作区。未启动服务器或真实模型 Campaign；没有把脚本模型结果写成真实模型优势。

文档阶段只核对链接、权威入口、术语和 `git diff --check`，不能据此填写代码验收项。旧证据、归档、checkpoint 和报告不参与覆盖式回滚；原有 `.pytest-tmp/`、`harness-node-modules.tar.gz` 不纳入提交。

## 5. 批准与完成条件

- [x] 用户明确确认本 TASK 拆解，状态转为 READY；T01 开始后进入 IN_PROGRESS。
- [x] T01..T06 按顺序实施，每项验收都有证据和独立提交。
- [x] 本地机制与恢复验证通过，持久记忆准确记录剩余真实模型/远程实验限制。

本地 TASK 完成不等于 `FDM-09` 开发实验通过，也不等于 `FDM-10` 正式优越性成立。开发运行需单独审批具体运行包；结果可以无优势或退化，必须完整保留。

## 6. 实施证据

- T01 (`FDM-01/08`)：修复前 `scripts/project_pytest.cmd -q tests/unit/test_structured_two_arm_search.py -k empty_planned_dimension` 为 1 failed，计划 R 无父时错误根重启。修复后 `scripts/project_pytest.cmd -q tests/unit/test_structured_two_arm_search.py tests/unit/test_structured_feedback_chain.py tests/unit/test_structured_random_evolution.py` 全通过；`scripts/project_python.cmd -m compileall -q src agent_image tests` 通过；`scripts/project_ruff.cmd check src agent_image tests scripts` 通过。覆盖 B-only、R-only、J-only 回退；既有全空、冷却、身份与随机臂回归。代码提交见 Git 历史。
- T02 (`FDM-03/05/06/07`, `FDM-P02/03/06`)：新增 `src/sandbox/structured_v1/feedback.py` 的 finalized bundle 查找、身份重验、按槽暴露/行动窗口证据和 host-only 收据；`projection.py` 增加摘要/fixture/manifest 完整性重验；`provider.py`/`text_provider.py` 增加版本化公开反馈、请求摘要、正常/修复请求及最终消息传输前边界检查；`generation.py` 保存投影、反馈和请求摘要。新增 `tests/unit/test_structured_feedback_mutation.py` 覆盖完整无槽暴露 unknown 回退、缺 bundle/不完整父、候选与 coverage 身份绑定、槽级窗口和 Provider 无宿主证据字段。验证：`scripts/project_pytest.cmd -q tests/unit/test_structured_feedback_mutation.py tests/unit/test_structured_provider.py tests/unit/test_structured_projection.py tests/unit/test_structured_text_provider.py tests/unit/test_structured_finalization.py`（52 passed）；`scripts/project_pytest.cmd -q tests/unit/test_structured_two_arm_generation.py tests/unit/test_structured_two_arm_search.py tests/unit/test_structured_two_phase.py`（56 passed）；`scripts/project_python.cmd -m compileall -q src agent_image tests`（通过）；`scripts/project_ruff.cmd check src/sandbox/structured_v1 tests/unit/test_structured_feedback_mutation.py`（通过）。未启动服务器或真实模型 Campaign；未宣称脚本机制测试产生真实模型效果。
- T04 (`FDM-01/05/07/08`, `FDM-P02/05/06`)：`SelectionReceipt` 明确保存 `source_parent_id`/`selected_unit`，正式 `run_opportunity` 传递源父、选中单元、选择时冷却和最终化完整性；`record()` 将执行子代、源父和选中单元分离，按完整证据门槛累计无新增，保留可解释 B/R/J 覆盖，拒绝不完整结果成为父反馈；语义重复签名只含排序去重的 B/R/J 键和按义务结果，精确结算重放幂等；引导冷却在资格判断后递减，父/单元独立三次阈值与十次暂停，随机演化不读取或递减结果冷却。新增 `tests/unit/test_structured_t04_settlement.py` 覆盖源父归因、部分证据/termination、独立冷却、语义重复、重放及第 11 次恢复。验证：`scripts/project_pytest.cmd -q tests/unit/test_structured_t04_settlement.py`（5 passed）；`scripts/project_pytest.cmd -q tests/unit/test_structured_two_arm_search.py tests/unit/test_structured_feedback_chain.py tests/unit/test_structured_random_evolution.py tests/unit/test_structured_two_arm_generation.py tests/unit/test_structured_feedback_mutation.py tests/unit/test_structured_provider.py tests/unit/test_structured_projection.py tests/unit/test_structured_text_provider.py tests/unit/test_structured_finalization.py tests/unit/test_structured_coverage.py tests/unit/test_structured_two_phase.py`（全部通过）；`scripts/project_ruff.cmd check src/sandbox/structured_v1/search.py src/sandbox/structured_v1/campaign.py tests/unit/test_structured_t04_settlement.py`（通过）。首次未带工作区外 `PYTEST_BASETEMP` 的回归受系统 pytest 临时目录权限阻断，改用脚本外临时目录后通过；未启动服务器或真实模型 Campaign。

- T03 (`FDM-02..05/08`, `FDM-P01..04`)：`legal_positions()` 统一提供两臂完整合法集合；引导局部机会以独立稳定随机流执行 3/4 优先位置、1/4 共同核，无可信槽证据时回退共同核；位置目标槽明确区分移动双槽和引用源槽，并在应用后比较目标槽实际材料变化。新增 `ParentFeedbackEvidence`/公开反馈在 `SearchState` 与 `SelectionReceipt` 的可恢复绑定，保存合法/优先集合摘要、选中位置摘要、分支、概率/意图版本、随机状态、证据引用和反馈摘要。四个公开意图通过 `ProviderTextRequest` 与完整消息边界传入；根/无文字操作保持 `intent_id=None`，随机演化臂不读跨 Episode 反馈。新增 `tests/unit/test_structured_t03_feedback_position.py` 覆盖双槽、移动/引用目标槽、优先和共同分支、随机历史隔离、Provider 意图/反馈传输与无文字调用口径。验证：`scripts/project_pytest.cmd -q tests/unit/test_structured_t03_feedback_position.py tests/unit/test_structured_feedback_mutation.py tests/unit/test_structured_provider.py tests/unit/test_structured_text_provider.py tests/unit/test_structured_edit_kernel.py tests/unit/test_structured_two_arm_search.py tests/unit/test_structured_two_arm_generation.py tests/unit/test_structured_t04_settlement.py tests/unit/test_structured_finalization.py tests/unit/test_structured_coverage.py tests/unit/test_structured_two_phase.py`（全部通过）；`scripts/project_python.cmd -m compileall -q src agent_image tests`（通过）；`scripts/project_ruff.cmd check src/sandbox/structured_v1/edit_kernel.py src/sandbox/structured_v1/provider.py src/sandbox/structured_v1/text_provider.py src/sandbox/structured_v1/generation.py src/sandbox/structured_v1/search.py tests/unit/test_structured_t03_feedback_position.py`（通过）；`git diff --check`（通过）。仅运行本地脚本/单元机制验证，未启动服务器或真实模型 Campaign，脚本结果不解释为真实模型优势。

- T05 (`FDM-04/06/07/08`, `FDM-P02..06`)：新增 `SearchProtocolIdentity`/`CampaignProtocolIdentity`，绑定算法、反馈、概率、意图及随机流版本；旧 JSON 仍可读取，但有工作的旧 checkpoint 或身份不匹配时拒绝恢复。checkpoint 在 reservation、selection、generation、submission、receipt 和 settlement 边界保存 pending selection/candidate/plan、随机/反馈/位置收据及完整预算；预提交中断可复用同一收据，已提交但未完成最终化的 receipt 恢复为隔离停止，避免重复执行或计费。新增从落盘 finalized bundle 显式加载父反馈的 `TwoArmSearch.load_parent_feedback()`，不依赖 runner 内存缓存；随机臂仍无跨 Episode 反馈。新增 `tests/unit/test_structured_t05_recovery.py` 覆盖协议隔离、选择/生成/提交边界持久化和未最终化收据恢复。验证：`scripts/project_pytest.cmd -q tests/unit/test_structured_t05_recovery.py tests/unit/test_structured_two_phase.py tests/integration/test_structured_two_arm_campaign.py tests/integration/test_structured_refused_child_settlement.py tests/unit/test_structured_two_arm_search.py tests/unit/test_structured_two_arm_generation.py tests/unit/test_structured_feedback_chain.py tests/unit/test_structured_random_evolution.py tests/unit/test_structured_feedback_mutation.py tests/unit/test_structured_provider.py tests/unit/test_structured_projection.py tests/unit/test_structured_text_provider.py tests/unit/test_structured_finalization.py tests/unit/test_structured_coverage.py`（全部通过）；`scripts/project_python.cmd -m compileall -q src agent_image tests`（通过）；`scripts/project_ruff.cmd check src agent_image tests scripts`（通过）；`git diff --check`（通过）。仅完成本地 checkpoint/恢复和脚本模型机制验证，未启动服务器或真实模型 Campaign；脚本结果不解释为真实模型优势。

- T06 (`FDM-08..10`, `FDM-P01..06`)：新增 `tests/integration/test_structured_feedback_mutation.py`，用固定 `summary-delivery-b` 父材料和确定性模型决策走正式 `build_envelope → drive_structured_v1_episode → finalize_bundle → finalized.artifacts() → extract_coverage/bind_coverage_execution → CoverageLedger` 链；覆盖 B-only、R/J 新增、重复/无新增、closure 不完整 unknown、父槽 `s3` 暴露与读后工具行动窗口、引导位置/意图/Provider 请求变化、随机演化反馈隔离和 `summary-delivery-e2` 私值传输前拒绝。新增 `scripts/report_summary_delivery_e2.py` 的 B/R/J 局部/全局增量、父子保留、暴露/行动窗口、失败分类、调用/token/费用/wall-clock/mutator 成本、`N_op/N_ep/W/F/Q` 分母、`W/N_op`、`W/(W+F)` 和逐对 `safety_gate`；`tests/integration/test_structured_e2_report.py` 对账字段与硬门槛逻辑。验证：T06 集成两文件共 13 项通过；选择/反馈/随机、Provider/投影/编辑、覆盖/最终化、two-phase/Campaign 四组相邻命令均退出 `0`；报告与本地边界记录见 [T06 本地验收报告](../reports/20260927-feedback-directed-mutation-local.md)。固定脚本模型只证明本地机制，不代表真实模型 ASR 或引导优势；服务器、真实模型 Campaign 和后续 `W/(W+F)` 门槛未测。
