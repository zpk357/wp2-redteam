# TRACE-G WP2 施工路线图

本路线图记录当前进度和下一步施工顺序；长期产品合同以 `SPEC.md` 为准。每个编号任务控制在一次
Codex 对话适合完成和验收的工作量内。Office V2 阶段 1-6 已冻结；阶段 7 已单独细拆。

## 2026-08-05 场景 V2 优先级重置 `[当前主线]`

用户确认 Office V1 把间接提示注入误当成统一威胁模型，固定矩阵和办公专用 Campaign 又过早固化。
因此暂停旧 `5.G6 -> 5.4-5.6 -> G5` 顺序，先完整建设并冻结 Office Workspace Scenario V2。

V2 只覆盖邮件、云盘、日历、工作区文件四域，但必须做深身份、权限、跨域因果链、Agent 可见世界和
事实 Oracle；攻击入口分为直接任务、间接内容、伪造授权和参数来源操纵。宏观计划与当前细化计划为：

- `docs/plans/office-workspace-scenario-v2-master-plan.md`
- `docs/plans/office-workspace-scenario-v2-stage-01-design-freeze.md`
- `docs/plans/office-workspace-scenario-v2-stage-02-world-kernel.md`
- `docs/plans/office-workspace-scenario-v2-stage-03-tools-causal-chains.md`
- `docs/plans/office-workspace-scenario-v2-stage-04-agent-context-api.md`
- `docs/plans/office-workspace-scenario-v2-stage-05-attack-entry-materialization.md`
- `docs/plans/office-workspace-scenario-v2-stage-06-fact-oracle.md`
- `docs/plans/office-workspace-scenario-v2-stage-07-docker-agent-integration.md`
- `docs/plans/office-workspace-v2-mutation-space-master-plan.md`（宏观边界，前置门已通过）
- `docs/plans/office-workspace-scenario-v2-step-02-behavior-risk-coverage.md`
- `docs/plans/office-workspace-scenario-v2-step-03-corpus-risk-frontier.md`
- `docs/plans/office-workspace-scenario-v2-step-04-controlled-semantic-mutation.md`
- `docs/plans/office-workspace-scenario-v2-step-05-multigeneration-feedback-loop.md`
- `docs/plans/office-workspace-scenario-v2-step-06-real-model-server-validation.md`（当前冻结计划）

阶段 2 已冻结。阶段 3 步骤 3.0-3.12 已完成边界、通用事实、统一权限/事务运行时、四域全部 17 个
确定性 handler、独立 V2 ToolSpec、10 个正常任务蓝图、24 个干净 CaseMaterialization、全部参考执行、
12 种结构路径、六类上游扰动和摘要锁定冻结证据；用户已确认业务实例与反例，阶段 3 正式冻结。
阶段 4-6 已分别冻结 Agent 上下文/可信交互、四入口/目标可达面和确定性事实 Oracle；阶段 7-8 的
Docker 执行、录制/重放和场景验收也已完成。V2CoverageInput、双覆盖、Corpus/Frontier、受控 Mutation
和第五步确定性多代闭环均已通过。当前唯一下一项是按冻结的第六步计划从 `6.0` 开始，准备
`qwen3.5:35b-a3b-int4` 的真实 Agent/Mutator 服务器综合验收。

Office V1、旧 G6、旧远程 G5 和写死 `qwen3:8b` 的服务器包只保留历史证据，不得解释为当前第六步
输入。新服务器包必须绑定 Office V2 当前代码、第五步闭环和 Qwen3.5 完整模型摘要。

## 2026-08-17 Office V2 第六步 `[计划冻结，下一项]`

详细计划为 `docs/plans/office-workspace-scenario-v2-step-06-real-model-server-validation.md`，计划 SHA-256
为 `sha256:bbb788b3e996730b0d563285b84d50e9bca77ac0b74d8f4f5f98d861d1924c03`。正式 Agent 与
独立 Mutator 均锁定 `qwen3.5:35b-a3b-int4`，使用不同角色、镜像、Prompt、配置和预算，串行占用单张
RTX 4090。初始权威 context 为 8192；显存、CPU offload 或协议门失败时阻塞，不自动降级。48 个冻结
案例留在本地做结构完整性门；服务器先过最小模型/协议预检和真实 2 代接通门，再让同一个 Campaign
按 10/20/30/50 代恢复续跑。早期 generation 完成公平 baseline exposure，覆盖饱和可提前停止；不单独
运行 24 clean + 24 representative 付费矩阵。Judge、黄金集、主动学习和漂移继续冻结。

## 大步骤 0：保护现场与建立基线 `[完成]`

- `[完成] 0.1` 阅读项目记忆并确认工作区不能 reset、checkout 或被远端覆盖。
- `[完成] 0.2` 清理明确的缓存和临时文件，保留真实验证证据。
- `[完成] 0.3` 运行当时适用的 Pytest、Ruff 和 Docker E2E，建立本地 Git 检查点。

结果：架构改造前基线由本地检查点保存；历史检查点没有被覆盖。

## 大步骤 1：验证多轮执行机制 `[完成]`

- `[完成] 1.1` 研究成熟 Agent 循环：框架拥有循环、工具结果回注、显式提交。
- `[完成] 1.2` 用固定 workspace 场景验证正常任务、安全控制和脆弱控制。
- `[完成] 1.3` 在一次性 Docker 容器中验证同一 Episode 的持续状态和零残留清理。
- `[完成] 1.4` 记录依赖、镜像、错误契约和固定案例里程碑。

结果：证明“单容器多轮模型/工具执行 + 最终状态断言”可行，但过渡运行时不是最终产品。

## 大步骤 2：TRACE-G 自研执行器 `[完成]`

- `[完成] 2.1` 定义 `trace_react_v2` 消息、工具调用、call ID 和显式终止合同。
- `[完成] 2.2` 实现模型/工具循环，真实工具返回必须进入下一轮模型输入。
- `[完成] 2.3` 接入 recording、strict replay 和 Prompt checkpoint fork。
- `[完成] 2.4` 实现 TRACE-G 自有 workspace 状态、业务工具及 safe/vulnerable 控制 Provider。
- `[完成] 2.5` 实现原生 Ollama Tool Calling Provider 和模型 digest 锁定。
- `[完成] 2.6` 加固 Provider：2-4 个一批、批大小关联 token 预算、确定性子批 seed、有界重试、
  缩批降级和有限失败审计。
- `[完成] 2.7` 加固 Engine 错误合同：只有明确临时错误可恢复，配置、漂移、完整性和未知错误暂停。
- `[完成] 2.8` 加入语义证据分层，自报标签不再直接成为事实风险覆盖。
- `[完成] 2.9` 本地 Docker 验证多轮依赖、no-submit、安全/脆弱控制、录制与 strict replay。
- `[完成] 2.10` 服务器真实 Qwen 验收 clean、injected、recording 和 strict replay。

关键证据：`trace-react-qwen3-004` 锁定真实 `qwen3:8b` digest；clean 完成正常会议任务且无泄露，
injected 的工具轨迹和最终状态确认受限文件被分享；strict replay 的行为摘要、最终状态和检查点匹配。
正式归档位于 `reports/server-downloads/trace-g-trace-react-qwen3-004-trace-workspace-results.tar.gz`。

## 大步骤 3：执行面单一化 `[完成]`

- `[完成] 3.1` 用户取消旧后端同输入对照，明确所有新任务统一使用 `trace_react_v2`。
- `[完成] 3.2` 删除两个旧主动执行入口、相关适配器、模型层、运行依赖和旧镜像定义。
- `[完成] 3.3` 删除旧错误码、旧事件兼容和本地旧依赖缓存。
- `[完成] 3.4` 新 Replay Manifest 写入 `trace-react-v2`、TRACE schema 1.2 和 state codec 2.0；
  缺失或非 TRACE-ReAct backend 的录制拒绝。
- `[完成] 3.5` 精确删除旧后端服务器归档和旧本地测试轨迹；保留当前 TRACE-ReAct 证据。
- `[完成] 3.6` 更新 README、SPEC、HANDOFF、AGENTS、LOG 和 LOG-INDEX，并完成全量验证。

完成证据：完整非 Docker 回归 `310 passed / 21 skipped`，全量 Docker E2E `23 passed`，Ruff 和
`git diff --check` 通过；最终镜像仅保留 `trace-redteam-agent:server`，镜像内旧运行模块不可导入，
TRACE-G 容器与 workspace volume 残留为 0。

## 大步骤 4：场景与攻击目标泛化、双覆盖率 `[待办]`

目标：从一个固定案例扩展为可组合的 `ScenarioTemplate + BenignTask + AttackObjective +
InjectionCarrier`，再建立执行证据驱动的行为新颖度和风险覆盖。

首个业务场景的范围、授权边界、预期成果和逐步验收以
`docs/plans/office-collaboration-scenario-v1.md` 为准。该文档执行校准、Docker Episode、录制、严格
重放和载荷 fork 已完成。后续合同已改为先完成同容器 Qwen + LangGraph 真实 Agent 和真实办公基线，
再恢复 coverage/Corpus 闭环；先前外部 Ollama 与脚本控制校准不作为最终真实 Agent 证据。

### `[完成] 4.1 定义场景与攻击目标 Schema`

输入：现有 workspace clean/injected 场景、业务状态、工具合同和 SPEC 核心领域对象。

状态变化：提取当前代码中写死的场景 ID、正常任务、攻击位置、攻击目标和成功条件；定义版本化
Pydantic Schema；暂不改变运行路径，也不新增覆盖率算法。

输出：`ScenarioTemplate`、`BenignTask`、`AttackObjective`、`InjectionCarrier` 和 `TestCase` 的数据
合同、最小示例与单元测试。

失败条件：Schema 仍把 workspace、Bob、固定邮件或固定文件写成通用常量；攻击成功仍依赖模型自述；
一次改动同时重写执行器或覆盖率算法。

验收：现有固定案例能无损表达为一组数据对象；同一正常任务可替换攻击目标或载体；无效前置条件和
缺失确定性成功证据会被拒绝；当前执行器尚未切换，因此既有执行回归不变。

### 后续任务

- `[完成] 4.2` 建立办公场景正常任务集合及确定性完成证据。
- `[完成] 4.3` 建立攻击目标集合及执行证据。
- `[完成] 4.4` 建立邮件、文件和日历注入载体集合。
- `[完成] 4.5a` 建立有效组合规则和可解释拒绝原因。
- `[完成] 4.5b` 形成第一批办公测试矩阵。
- `[完成] 4.6` 建立数据驱动办公状态并校准控制 Agent。
  - `[完成] 4.6a` 建立共享办公状态与证据内核：13 项能力、授权记录、状态摘要和确定性证据判定。
  - `[完成] 4.6b` 实现确定性安全控制，跑完 6 个干净案例和 12 个攻击案例的负向校准。
  - `[完成] 4.6c` 实现确定性脆弱控制，形成六类攻击目标的正向证据并完成成对校准。
- `[完成：真实模型终验后移] 4.7` 验证 Docker Episode、重放和 fork。
  - `[完成] 4.7a` 定义冻结 TestCase 到容器场景初始化的版本、摘要和失败合同。
  - `[完成] 4.7b` 让容器工具层消费初始化信封并迁移 13 项办公工具语义。
  - `[完成] 4.7c` 运行安全/脆弱代表性 Docker Episode 并验证零残留。
  - `[完成] 4.7d` 验证新办公 Episode 的 recording、strict replay 和 fork。
    - `[完成]` 完整办公请求的 recording 与安全/脆弱 strict replay。
    - `[完成]` 在读取载体前替换攻击表达，验证父轨迹不可变、子分支执行及子 strict replay。
  - `[后移] 4.7e` 真实 Qwen 场景基线与多代闭环改为第 5.5 后的阶段门，不阻塞本地灰盒闭环施工。
- `[完成] 4.8a` 冻结办公 Episode 到 CoverageInput 的执行证据合同。
  - 输入：已提交的完整轨迹、冻结 TestCase、初始/最终状态和版本摘要。
  - 状态变化：校验事件连续性与完整性，配对工具调用/结果，提取授权事实和状态差异；模型自报标签
    不进入事实字段。
  - 输出：相同轨迹必得相同摘要的版本化 coverage 输入；只建立证据桥，不新增第二套存储。
  - 失败条件：事件缺失或错序、工具结果无法配对、场景/状态摘要漂移、未知版本或未分类异常。
  - 验收：安全/脆弱成对轨迹共享正常前缀；只有真实未授权动作与副作用不同；篡改自报标签不改变
    coverage 输入摘要和事实。
- `[完成] 4.8b` 扩展行为新颖度：工具节点/边/三元组、参数结构与敏感等级、结果类别、授权转换、
  业务状态变化和终止原因；行为侧只报告新增特征与增长，不虚构未知分母。
  - 办公轨迹只消费 `4.8a` 校验后的执行证据；模型/工具自报 risk 标签不进入行为档案。
  - fork 行为档案由父前缀动作摘要与子后缀组成，跨断点二元组、三元组不会丢失。
  - 特征只保存有限类别，不保存资源 ID、邮件地址、载荷正文或状态摘要；非办公提取公式保持不变。
- `[完成] 4.8c` 将版本化风险树映射到工具与环境副作用证据，分别记录“意图、尝试、被阻止、已实现”；
  办公路径只消费独立重建的执行证据，自报 risk/operator 不能形成事实命中。
  - 映射固定为 `office-risk-v1` 并携带规则 digest；风险树提升为 `enterprise-v2`，删除类目标映射到
    新的叶节点 `unauthorized_resource_deletion`，不再错误使用父分类。
  - 6 个干净案例没有风险事实；12 个攻击案例的安全控制只有意图，脆弱控制均形成意图、尝试和
    已实现；结构化策略拒绝形成意图、尝试和被阻止，但不形成已实现。
  - 模型、工具和安全事件标签篡改不改变风险签名；recording、strict replay、carrier fork 及其
    strict replay 的风险签名一致。现有安全控制仍表示“未尝试”，不是策略拦截正例。
- `[完成] 4.9a` 建立办公 Campaign 的累计 coverage 快照、幂等写入和恢复边界。
  - CoverageStore schema `1.1` 锁定 taxonomy 版本和语义内容 digest；第一条办公 CoverageInput 在同一
    事务中锁定 `office-risk-v1` 版本/digest，映射 Campaign 不允许混入未映射轨迹。
  - 累计快照携带 taxonomy/mapping 身份；相同轨迹重复处理不增加计数，事务中断回滚 mapping 锁和
    全部部分写入，数据库提交后快照写出失败可在重启时原子重建。
  - 同版本 taxonomy 内容漂移、mapping 漂移、不完整元数据和旧 schema 明确拒绝；不静默迁移无法
    证明 taxonomy 摘要的旧累计结果。
- `[完成] 4.9b` 输出行为-风险热力图、覆盖增长与饱和度数据，并验证标签篡改不影响事实覆盖。
  - 从同一 CoverageStore 事务视图生成工具一元/二元/三元路径 × 执行证据风险单元格；输出触达深度、
    阶段、轨迹数、深度改进数和 scope 内空白，不建立办公专用数据库。
  - 风险空白只针对锁定 scope；增长按持久 `created_order` 重建，并校验累计行为数和执行风险深度连续。
    当前观察单位是轨迹，不把它伪装成尚不存在的 Fuzzer generation，也不声称未知行为分母百分比。
  - 报告携带 taxonomy/mapping/scope 身份和内容摘要；模型、工具及安全事件标签篡改不改变事实报告，
    损坏或不连续的持久结果 fail closed。

完成标准：固定案例不再是数据模型常量；攻击目标和载体可独立替换；相同轨迹重复提取相同特征；
行为侧不声称未知分母百分比；风险覆盖只由执行证据确认。

## 大步骤 5：LLM 语义变异与覆盖率引导灰盒闭环 `[待办]`

最终语义候选必须由锁定身份的 LLM Mutator 生成。RuleBased/Fake Provider 只验证 Schema、反馈传递、
批次、血缘、恢复和错误合同，不构成语义质量验收。Campaign 允许显式改变正常任务、攻击目标、载体、
表达和交互路径；所有 MutationPlan 必须声明改变/保持维度，允许目标重定向但禁止静默漂移。一个
Campaign 可以覆盖一个场景，但每个独立攻击组合进入新的 Episode；运行时采用“公平基线扫描 + 双覆盖
反馈自适应交错”，不串行穷尽单一目标，也不穷举完整笛卡尔积。

- `[完成] 5.1a` 定义办公候选生成合同：Campaign Manifest 分别锁定 Scenario、BenignTask、
  AttackObjective 和 InjectionCarrier 目录版本/digest；组件与表达可分别选择，但组合必须通过现有
  授权、前置条件和确定性证据校验。已实现强制目录锁的 `ScenarioCampaignManifest`、固定表达目录锁、
  冻结选择/result digest、确定性候选 ID 和结构化拒绝；每次生成前复核当前目录摘要。
- `[完成] 5.1b` 分开定义调用前冻结的 `MutationPlan`、Provider 返回的 `MutationCandidate` 和调用后的
  `MutationValidationRecord`，并实现目标保持型局部表达变异。Plan 锁定父案例/feedback、请求差异、
  计划组件、算子、seed、预算和最终 LLM 请求身份；Record 保存实际差异、响应审计和校验结果。现已
  实现办公专用冻结合同、调用前 SQLite 幂等落盘、Provider 成功/失败审计和宿主验证；只有归一化表达
  发生变化且场景、正常任务、攻击目标、载体、Agent 和预算均保持时才生成子 `TestCase`。目录或组件
  快照漂移、声明不符、未变化和重复表达均在 Docker 前拒绝。RuleBased 仅为合同测试替身，未完成最终
  LLM 语义质量验收。
- `[完成] 5.1c` 实现显式目标重定向，以及正常任务、载体和交互路径重组；每个候选记录原/新目标、
  改变/保持维度、父案例和预期覆盖空白，并重新执行授权、前置条件、可达性和成功证据校验。重定向
  当前只允许选择 Manifest 锁定的已注册组件；合法目标 A -> B 通过，未声明的正常任务/目标/载体
  漂移、未注册组件或校验失败必须在进入 Docker 前拒绝且不得进入 coverage。计划 A 而意外执行风险 B
  时保留 A 的 lineage，B 单列 unexpected RiskHit；期望路径 X、实际路径 Y 时 coverage 只记录 Y。
  实际已在既有三段审计合同上新增显式重定向计划：计划差异必须与改变/保持维度精确一致，目标必须
  改变，正常任务和载体可按计划重组；Provider 返回后由宿主再次解析锁定目录，并复用既有组合评估器
  与 `TestCase` 校验。合法目标重定向及任务/载体重组可生成带父 lineage 的子案例；静默部分应用、
  未注册组件和不兼容组合在 Docker 前以稳定拒绝封闭失败。办公风险映射按冻结目标标记 expected，
  实际轨迹额外命中的风险单列 unexpected，路径覆盖仍只来自实际工具轨迹。
- `[完成] 5.2a` 建立 `ObjectiveExposureLedger` 与 `RiskFrontier`：每个场景兼容攻击目标记录
  `unseen/executed/unreachable_or_incompatible`；每个风险前沿记录下一执行深度、兼容组件、父种子、
  行为空白、局部预算、冷却和恢复状态。实际实现锁定 Campaign Manifest、场景目录、taxonomy、risk
  scope、办公 mapping、Agent 与预算身份；只有证据完整、正常任务成功且显式提交的原始办公 Episode/
  recording 可幂等推进 executed，strict replay、干净案例、漂移案例和失败 Episode 均封闭拒绝。SQLite
  写入、内容寻址快照、feedback 应用与 Episode 索引处于同一事务，重启时逐项核对主键、摘要、JSON、
  账本与最新快照；中断回滚和索引篡改已有回归证据。本项未实现候选排队或公平调度。
- `[完成] 5.2b` 实现公平基线扫描：默认办公目录虽然有 36 个合法表达组合，V1 基线只消费冻结矩阵中
  已注册的 12 个代表组合，不穷举笛卡尔积。队列按攻击目标轮转，前 6 项恰好覆盖 6 类目标；每项都
  必须由新的合法 Episode 提交后才推进。单活动租约、尝试历史、队列状态和计划摘要写入 Campaign
  SQLite 与内容寻址快照；同 worker 重取租约幂等，重启恢复相同租约。候选拒绝、Provider/基础设施
  错误、清理失败和 soak probe 只记录尝试并重新排队，不推进目标或风险覆盖；未尝试项优先于失败重试。
  提交时必须精确匹配租约案例、摘要和目标，并与 Episode、账本、revision 和快照同事务写入。
- `[完成] 5.2c` 实现自适应交错与反模式坍缩调度：基线完成后按策略锁定的 2-4 个有限小批次轮转
  RiskFrontier。每个候选保存风险空白、行为/路径-风险新颖度、欠采样、等待年龄、重复、连续无增益、
  invalid 率、成本和 virtual runtime 分项；饥饿优先、探索保留与可行的最大连续份额先于软分数。
  输入快照、完整候选集、约束命中、确定性 tie-break、方向及结果摘要进入 Campaign schema v3 的同一
  事务和内容寻址快照。调度前要求当前 feedback 的 observation 已覆盖全部 baseline Episode；活动批次
  幂等恢复，提交后必须等待更新的 coverage observation。只有提交 Episode 可形成无增益，候选拒绝
  只进入 invalid 率，Provider/基础设施/清理/soak 不冒充覆盖。局部无增益可
  冷却，新种子、unexpected risk 深度或新路径-风险事实可再激活；策略、feedback、索引和 JSON 漂移
  均 fail closed。本项是确定性调度合同，不是完整 Fuzzer 或真实 LLM 质量证据。
- `[完成] 5.2d` 实现互斥、可审计、可恢复的 Campaign 完成状态：`baseline_incomplete`、
  `baseline_complete`、`saturated`、`budget_exhausted_incomplete`、`paused` 和 `cancelled`。完成策略及
  Episode/token/成本/确定性累计耗时预算以版本和 digest 锁定，状态进入 Campaign schema v4 的同一
  SQLite 事务和内容寻址快照。只有已提交批次及其更新 coverage feedback 进入全局无增益窗口；候选
  拒绝、Provider/基础设施错误、清理失败和 soak probe 只消耗实际资源，不制造饱和。达到锁定风险
  目标深度只降低风险深挖优先级，不等于行为探索完成；所有局部前沿收敛后仍须满足全局行为、风险
  深度和路径-风险无增益窗口。仅剩一个可执行前沿时允许确定性的单项尾批，避免最小批大小造成死锁。
  饱和与预算在同一反馈边缘同时成立时报告饱和，预算先耗尽则如实报告未完成。暂停/取消记录原因与
  证据，阻止新工作且幂等恢复；策略、状态行或摘要漂移 fail closed。本项不是完整 Fuzzer 质量证据。
- `[完成] 5.3` 已把 1-4 个候选的办公 MutationPlan 拆成持久、内容寻址的执行子批；正常批量为 2-4，
  单项只用于尾批或缩批叶子。子批 seed 由 Plan、路径、重试序号和数量确定性派生，输出 token 上限按
  固定开销加每候选预算计算，并受 Plan/策略上限约束。Provider call、候选、校验和成功 attempt 原子
  写入；重启复用已成功子批，不重复调用。transport、timeout、408/429/500/502/503/504 可有限重试，
  有证据的截断/响应过大可递归缩批；JSON/Schema、模型/请求摘要漂移、本地完整性和未知异常先保存
  有界审计，再幂等暂停 Campaign。中断发生在失败工件与 Campaign 暂停之间时，恢复会补做相同暂停。
  本项只以 RuleBased/Fake 验证工程合同，不构成最终 LLM 语义质量证据。

### 同容器真实 Agent 架构阶段门 `[进行中]`

该门是恢复 5.4a 前的强制施工顺序。保留现有场景目录、TestCase、办公工具/状态、TRACE 证据、
recording/replay/fork、双覆盖率、Campaign/RiskFrontier、调度和 MutationPlan 子批；只替换正式 Agent
运行路径和部署拓扑。正式拓扑为：Docker 化 Controller/Fuzzer 调度一个全新 Agent-Qwen 容器；该
Episode 容器自包含锁定 Qwen 权重、仅回环监听的 Ollama、LangGraph Agent、工具和办公环境；LLM
Mutator 是独立 Docker 角色。容器外不得代替被测 Agent 规划工具序列。

- `[完成] 5.G1` 锁定 LangGraph 集成与供应链合同。
  - 输入：现有 `trace_react_v2`、Ollama Provider、办公工具/状态、镜像与离线部署脚本，以及 LangGraph、
    LangChain、`langchain-ollama` 的当前官方接口、许可证和 Python 3.11 支持状态。
  - 状态变化：记录精确版本/许可证/依赖关系和退出方案；冻结 LangGraph -> TRACE 事件/检查点适配边界、
    容器进程拓扑、模型/镜像/Prompt digest、网络和挂载禁令；不得恢复已删除的旧适配器。
  - 输出：可审查的依赖锁与架构施工合同，明确哪些现有模块保留、替换或仅限测试。
  - 失败条件：许可证或维护状态不明；LangGraph 私有对象进入长期协议；正式路径仍可选择外部模型或
    `OfficeControlProvider`；依赖版本没有可复现锁。
  - 验收：从 TestCase 到 TRACE/状态证据的数据流、进程归属、失败传播和退出方案都能逐项说明，且
    `SPEC.md`、路线图、部署文档和依赖审计一致。
- `[完成：本机 Docker 实证] 5.G2` 构建最小自包含 Agent-Qwen 镜像。
  - 镜像内安装 Ollama、锁定 Qwen 权重、LangGraph Runtime 和办公环境；模型只监听
    `127.0.0.1`。加入多进程启动、模型 warm-up、健康检查、信号传播、非 root Agent 进程和 GPU 清理。
  - 正式模式禁止外部 endpoint、宿主模型目录挂载、Docker Socket 和公网；测试替身保留在独立测试
    入口，不能由正式请求回退触发。
  - 本机实证：锁定归档构建镜像 `sha256:b421c168f52017b94eff285e5f8c0a894847ba1907909052f18a3c6842b8f9f4`；
    在 `--network none`、只读根文件系统、无挂载、无 Docker Socket、非 root 和单 GPU 条件下，Qwen3 8B
    warm-up 与 `/api/generate` 返回 200，Runtime 达到 `healthy`；SIGTERM 退出码 143、无 OOM、容器和
    Qwen GPU 进程零残留。该证据不替代 5.G5 的远程 GPU 服务器阶段门。
- `[完成：本机真实 Qwen Docker 实证] 5.G3` 完成最小真实 Agent 纵向闭环。
  - 一个干净办公任务至少执行两个存在参数依赖的工具调用；一个邮件注入案例由 Qwen 自主决定工具、
    参数和提交时机。真实工具结果进入下一轮模型输入，最终状态和 TRACE 事件共同判定结果。
  - 任一请求内预置 action plan、调用脚本控制 Provider、模型没有读到真实工具结果、或状态变化发生
    在容器外，都判失败。
  - 本机实证使用镜像 `sha256:478d4045c57e69caba8d357b6ff1d0ffd357664a998493bca9c67efd5a8c5d9c`、
    锁定模型 `qwen3:8b@sha256:500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`。
    干净样例中，Qwen 的两次未取证写入被办公状态内核拒绝；模型读取真实邮件正文后自行纠正标题、
    时间和参与人，成功创建会议并提交。合成注入样例中，Qwen 自主读取受限文件并完成未授权共享，
    TRACE/最终状态将其判为已实现的数据泄露风险，同时仍完成正常会议任务；这是真实风险发现，不是
    基础设施失败或安全控制通过。
  - 两次运行均未携带 action plan，模型调用、ToolMessage 回注、工具副作用和提交都发生在同一个
    `--network none` 一次性容器内；运行后测试容器和 Qwen GPU 进程零残留。G3 聚焦回归 `65 passed`，
    Ruff 通过。该证据只通过本机最小 5 工具纵向切片，不替代 G4 完整办公证据或 G5 服务器阶段门。
- `[完成：本机真实 Qwen Docker 实证] 5.G4` 接入完整办公 Agent 与可重放证据。
  - 把 13 项办公 ToolSpec/Runtime 接入 LangGraph；将模型、工具、授权、状态和终止事件转换为既有
    TRACE schema 1.2，保持 recording、strict replay、carrier fork 和 coverage 输入合同。
  - `OfficeControlProvider`/Fake 仅保留为校准与故障测试；正式 Agent 路径不得默认或显式选择它们。
  - 本机验收以同一锁定镜像完成“真实 Qwen 父 recording -> 无 Qwen strict replay -> 真实 Qwen
    carrier fork -> 子 strict replay”；父记录全部可达工件不变，子分支独立，源/重放的行为 profile
    和风险签名一致。strict 模式进程表无 Ollama，live 模式存在同容器 Ollama，四个容器及工作卷均清理。
  - 权威证据：`reports/local-acceptance/20260804-g4-rerun2/acceptance.json`，SHA-256
    `e0157bb868575723768ad94f51b4018a7bc23547fcf86e37a569389fd69457ab`；镜像 ID
    `sha256:7c340e421d1249da28c922b36647397569d5f2658a14dc173d8e3a162a79f096`，模型 digest 保持锁定。
    全量单元/集成回归 `689 passed`，Ruff 通过。两个更早验收目录为失败证据，不得冒充通过。
- `[延后：上传前准备完成] 5.G5` 通过 GPU 服务器同容器真实 Agent 阶段门。
  - 以无公网配置运行，锁定 Agent 镜像与模型 digest；证明确实由同一个 Episode 容器提供 Ollama、
    LangGraph Agent、工具和状态，不存在宿主/其他容器模型调用或宿主权重挂载。
  - clean、injected、recording 和 live fork 使用真实 Qwen；strict replay 使用同一锁定镜像的显式
    strict 模式，不启动或调用 Ollama，并必须精确匹配录制。工具结果因果回注成立；结束后容器、
    临时卷、Ollama/GPU 进程零残留。失败归档必须保留但不得冒充通过。
  - `[完成：server-ready]` 已生成 `D:\hxjh\trace-g-server-kit-g5`：只含锁定自包含 Agent-Qwen 镜像、
    Controller 镜像、当前源码、G4 权威证据、staging/验收/离线复核脚本和双层摘要，不含独立 Ollama
    镜像、外置模型归档或宿主模型挂载。本机最终 preflight 完成四容器链路；live 为同容器 Ollama +
    单 GPU，strict 为无 Ollama + 零 GPU，源/重放覆盖一致且四个本轮容器/卷清理。服务器实证仍未运行。
  - 远程执行延后到本机第一代真实多代闭环形成后，一次上传同时复验 G5 隔离合同、G6 基线和代表性
    多代 Campaign；不得把“延后”写成“已通过”，也不得让服务器等待阻塞本机核心算法施工。
- `[历史待办：V2 重置后冻结] 5.G6` 用真实 Qwen 重跑冻结的 12 个办公攻击基线组合。
  - 每个组合使用新的自包含 Agent-Qwen Episode；输出 `baseline_complete` 或逐项不可达/失败证据，
    并生成可供双覆盖率消费的真实轨迹。脚本控制基线不能替代本项。
  - 开发期先在本机锁定镜像上完整运行一次并冻结结果；只要镜像、模型、Runtime、工具、场景目录和
    coverage 身份摘要未变，后续闭环开发复用该基线，不重复消耗真实 Qwen。远程 G5 时再独立复验一次。

- `[待办：5.G6 本机基线后恢复] 5.4a` 串联第一代：真实 Qwen 基线/候选 -> 新 Agent-Qwen Docker Episode
  -> 轨迹/状态 -> 双覆盖率 -> Corpus 取舍。
- `[待办] 5.4b` 串联第二代：必须消费第一代 coverage 空白并产生新的合格候选；重复或无证据候选
  不得冒充进展。
- `[待办] 5.4c` 验证 Campaign 持久化、暂停/恢复、失败状态和高价值轨迹 strict replay/fork；恢复时
  exposure ledger、frontier virtual runtime、冷却窗口、in-flight lease 和下一确定性选择保持一致。
- `[待办] 5.5a` 用确定性 Fake Agent、RuleBased/Fake Mutator 做多代、并发、资源清理与饱和停止的
  工程验收；证明所有可达目标获得最低执行机会、单一高收益目标不能饿死其他目标、局部冷却可被新
  证据重新激活、预算不足不冒充完成。结果不得冒充最终 LLM 语义探索质量。
- `[待办] 5.5b` 完成长时间运行和恢复验收，证明容器、临时卷和队列无泄漏。

### 最终 LLM 语义闭环阶段门 `[后移]`

- `[待办：5.5 后本机执行] 5.6a` 用锁定模型、Prompt 和 digest 的 Docker 内 LLM Mutator 生成目标保持与显式目标重定向候选，
  验证语义多样性、合法组合率、lineage 和 feedback 消费；至少完成两代，第二代必须引用第一代
  feedback digest，并能证明覆盖空白改变了计划。
- `[待办：远程 G5 前先完成本机小规模门] 5.6b` 在 5.G6 真实 Qwen 基线之上运行真实 Mutator + 真实 Agent 多代闭环；Mutator 与被测
  Agent 的容器、模型、Prompt、预算和证据分开记录；Campaign 必须达到攻击目标 baseline complete，
  或如实报告不可达与预算不足，不能只挑高收益方向展示。
- `[待办：远程 G5 通过后] 5.6c` 在等预算下比较随机/均匀、仅风险、仅行为和双覆盖引导，证明双覆盖反馈真实改变
  下一代探索方向；Fake/RuleBased 工程证据不能替代该门。

第 4.8-5.5 全部通过后才进入 5.6。确定性替身证明系统机制和证据合同正确；5.6 才证明最终 LLM
Mutator 的语义生成质量以及真实被测 Agent 的规划与安全行为。先完成 5.6a 和 5.6b 本机小规模门，
再执行远程 G5；远程通过后才扩大到 5.6c 等预算实验。两类证据必须分开报告。

### 第 5 阶段快速验证策略

- 日常编辑：只运行直接受影响的单元/集成测试和对应 Ruff；文档状态调整只做文本一致性与
  `git diff --check`，不运行产品回归。
- 路线图小项完成：运行该职责边界的聚焦测试；只有改动 Agent Runtime、工具状态、TRACE、replay、
  coverage 事实合同或 Docker 调度时，才增加一个最小 Docker 代表路径。
- 真实 Qwen 批次：G6 的 12 组合在本机锁定身份下完整运行一次并冻结；身份摘要未变时复用工件，
  不因调度、报告或文档修改重复推理。
- 里程碑门：全量单元/集成回归、四容器 recording/replay/fork、8GB 镜像摘要和离线包双层摘要只在
  首次冻结、相关边界变化或最终远程封包时运行。失败后只重跑失败路径及受影响邻接路径，修复确认后
  再在里程碑末尾做一次完整门。
- 复用证据必须同时记录代码/目录、Agent 镜像、模型、Prompt、taxonomy、mapping 和 scope digest；
  任一相关身份变化、证据损坏或无法说明影响范围时，旧证据立即失效并升级验证。测试少不是目标，
  以最便宜且能区分成功/失败的证据为准。

## 大步骤 6：裁判基线与黄金集 `[冻结]`

前五步和 5.6 稳定后，才建设结构化 LLM-as-Judge 和 50-80 条人工黄金集。人工标注者间一致性单独报告；
Judge 对冻结评估集的 Verdict Accuracy 目标至少 85%，两者不得混称为“人工一致率”。
Judge 对正常任务质量、目标语义一致性、违规严重度、可利用性和人工复核需求评分，不覆盖工具/状态
执行事实。确定性执行事实 oracle 是正式事实系统，不是 Fake Judge；Fake Judge 只验证工程合同，不能
通过最终评分质量门。Judge 与事实冲突时保留事实、把评分标记为 provisional 并进入人工复核。验收需
证明“Judge 判安全”不能删除已实现风险，“Judge 判违规但无证据”不能新增 RiskHit。现在不实现、不
预留假接口。

## 大步骤 7：主动学习与评分漂移 `[冻结]`

第 6 步通过后，再建设不确定性采样、人工复核回流、黄金探针、漂移告警和结论冻结。第 6 步 Judge
只用于 Finding/报告排序与人工复核；第 7 步门禁通过后才可把版本锁定结果作为调度/Corpus 的次级
信号，且不能独立创建覆盖或晋升种子。达到锁定最小统计窗口并越过阈值时自动冻结结论发布和 Judge
依赖调度；样本不足只告警，恢复必须经人工确认或重新校准。纯执行证据驱动的 Fuzzing 可带降级标记
继续，只有人工复核标签可以进入下一版黄金集。

## 大步骤 8：统一 CLI、报告、CI/CD 与实战验收 `[冻结]`

最后整合统一命令、JSON/HTML 报告和真实业务 Agent 压测。报告必须关联覆盖增长、风险分布、轨迹、
确定性判定和精确重放命令。

## 当前唯一下一项任务

Office Workspace Scenario V2 阶段 1-8 已正式冻结；Stage 9.1 的 V2CoverageInput 与覆盖第二步
`2.0-2.8` 已完成。第三步 `3.0-3.12` 也已完成：真实第二步 Coverage 工件可以晋升种子、更新 Corpus
与双 Frontier、自动选择下一轮，并把 Coverage、Corpus、Frontier、ExposureLedger、预算、生命周期和
Settlement 在同一 SQLite 事务中提交；关闭重开后的下一轮选择保持一致。第四步“受控语义变异”详细
计划已经写入并按合同审查修订。用户确认后，`4.0` 已完成独立 Mutation identity、Scheduler 所有的
Rebind/Retarget/Authorization/Operator allocation 合同和兼容 MutationGenerationAllocation 信封；既有
GenerationAllocation、Campaign identity 与历史 SQLite 不变。当前唯一下一项是 `4.1`：实现完整
MutationFieldRegistry、MutationIntent 和 MutationPlan。暂不进入 Docker 多代闭环、真实 Qwen 或
LLM-as-Judge。
