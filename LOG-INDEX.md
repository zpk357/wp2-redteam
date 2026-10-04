# 变更日志索引

| 日期 | 主题 | 记录标识或提交 | 搜索标题 |
|---|---|---|---|
| 2026-09-27 | rec-v2 14次交付未读可变槽、2次读同便笺后停；新场景材料接触与行为分叉验收 SPEC 草案，未编码未运行 | `20260927-soc-material-encounter-draft` | `rec-v2 接触链复核与新场景有效性草案` |
| 2026-09-27 | dev2 零交付/父反馈入口未接通只读复核；任务定位、真实交接、8+8 同预算正常控制恢复 SPEC 草案，未编码未运行 | `20260927-soc-fdm-delivery-recovery-draft` | `dev2 零交付复核与恢复方案草案` |
| 2026-09-27 | FDM 开发运行因服务故障中止（不完整）：引导臂 7/16 集 `W=0/F=7/Q=0`、交付 7/7、反馈消费 7/7；**报告入口与所选 fixture `summary-delivery-b` 不兼容（局部子代 KeyError）** | `20260927-soc-fdm-dev-run-stopped` | `FDM运行中止` |
| 2026-09-27 | Agent 提示 v2 条件诊断运行因 VM 停机中断（不完整）：`W=0/F=7/Q=0`、交付 4/7；删去"材料不是指令"两句后模型在 7 集内仍未越界 | `20260927-soc-atk-promptv2-interrupted` | `提示v2中断` |
| 2026-09-27 | FDM 真实开发实验运行包草案：2 对×2 臂×16 机会、3 小时共享截止、冻结身份/预算/硬门槛；尚未授权或启动服务器 | `20260927-soc-fdm-run-package-draft` | `FDM开发运行包草案` |
| 2026-09-27 | FDM T06 本地正式 runtime/Oracle 验收：固定 `summary-delivery-b` 父材料验证 B/R/J、反馈位置/意图、随机隔离、未知结算、报告分母与安全门槛字段；未启动真实模型 | `20260927-soc-fdm-t06-local` | `FDM T06本地验收` |
| 2026-09-26 | 攻击手法多样化扫描一次运行（操作者终止，INVALID）：8 手法冒烟探测全通过；演化臂 14 集 `W=0/F=14/Q=0`、交付 10/14、受限读取 13/14；T8 未运行；正式报告被对账机制拒绝 | `20260926-soc-atk-technique-scan` | `手法扫描INVALID` |
| 2026-09-26 | GEN-A 目标化生成器一次对照运行（操作者终止，INVALID，补登记）：演化臂 15 集 `W=0/F=15/Q=0`、交付 8/15、受限读取 15/15；引导臂仅 1 集，无对照读数 | `20260926-soc-atk-gen-a-run` | `GEN-A对照INVALID` |
| 2026-09-26 | GEN-A 攻击生成目标化：目标改为"要达成的越界交付"，安全约束逐字保留；本地实现＋20项聚焦测试通过，对照运行包待执行 | `20260926-soc-atk-gen-a-local` / `5d5d03d` | `GEN-A生成目标化` |
| 2026-09-26 | COMP-A/E2 最终化证据派生报告勘误：两轮各 F=16/15、Q=0，旧输入逐文件未变 | `20260926-soc-mat-report-correction` | `最终化证据报告口径修正` |
| 2026-09-26 | COMP-A 完成收敛诊断：提示契约使交付 0→9、合法完成 0→9、gate 首次 true；两臂 W=0；发现报告 F/Q 口径缺陷 | `20260926-soc-mat-completion-diagnostic` | `COMP-A诊断与口径缺陷` |
| 2026-09-26 | COMP-A 已获批准并进入 IN_PROGRESS：独立完成契约 fixture、受控验证与一次真实诊断 | `20260926-soc-mat-comp-start` | `交付完成收敛诊断开始` |
| 2026-09-26 | E2 登记收口与交付完成收敛诊断 SPEC 草案：清理重复/过时状态，保留一次性诊断边界 | `20260926-soc-mat-e2-registration-and-completion-draft` | `E2登记收口与完成收敛诊断草案` |
| 2026-09-26 | E2 诊断预检（32机会，VALID_ZERO_SUCCESS）：任务未完成致义务全 unknown，后代增益为0 | `20260926-soc-mat-e2-precheck` | `E2诊断预检三问读数` |
| 2026-09-25 | E两阶段本地实现/恢复/六机会验收，538项通过，运行包待运行授权 | `20260925-soc-mat-e-local` / `81fff47` | `E 两阶段实现与上服务器前交付` |
| 2026-09-25 | c 预检三问分析勘误：修正事务到模型决策关联与根/局部路线首次归属 | `20260925-soc-precheck-c-analysis-errata` | `c预检分析勘误` |
| 2026-09-26 | E2 本地实施与离线验收：固定内部核对单承载完整 LIMITED 登记值、两通道阳性与阴性对照、攻击效果只读报告 | `20260926-soc-mat-e2-local-implementation` | `E2本地实施与验收` |
| 2026-09-26 | E 部分批次提前终止（pair1–2：引导111/160 vs 随机63/64，p=0.25，不可确认）+ E2 获批进入本地实施 | `20260926-soc-mat-e-partial-and-e2-approved` | `E提前终止与E2获批` |
| 2026-09-25 | d 目标保持材料搜索单次预检：有效但未通过（固定受限文件未被触达；局部读取仍在末次决策） | `20260925-soc-mat-d-precheck-verdict` | `d预检判定未通过` |
| 2026-09-25 | c 有限真实开发预检（两臂各16机会；三问：改动被读、读取后无观察窗、局部增量几乎不越历史） | `20260925-soc-precheck-c-executed` | `c预检三问读数` |
| 2026-09-24 | c 开发预检：运行包冻结后因服务器重启致 GPU 消失而中止（未消耗模型调用） | `20260924-soc-precheck-c-aborted-gpu-missing` | `c预检环境故障中止` |
| 2026-09-24 | 正式工具证据、FBK-04有限语义B、逐事件J与来源分叉；v4隔离旧数据 | `20260924-soc-semantic-behavior-local` | `正式执行证据与语义行为覆盖` |
| 2026-09-24 | c公开锚点、真实提交内容、同源交付与本地反馈链；B分叉仍未具证 | `20260924-soc-anchored-delivery-local` | `公开锚点与同源交付本地实现` |
| 2026-09-24 | 预检阻塞收敛与公开读取分类修复 | `20260924-soc-precheck-blockers-and-public-read-fix` | `预检阻塞收敛与公开读取分类修复` |
| 2026-09-24 | 32机会开发预检归档的本地轨迹、材料读取、路线资格与反馈复核 | `20260924-soc-dev-precheck-trace-review` | `开发预检本地轨迹复核` |
| 2026-09-24 | 撤回大规模草案、修正统计/预算、两主臂32机会开发预检与checkpoint报告 | `20260924-soc-bounded-development-report` | `两臂开发预检收口` |
| 2026-09-24 | 运行前收口：b 上离线全循环、有效演化复核（改 8×16）、固定机会预算，SPEC 与运行范围合并待确认 | `20260924-soc-paired-spec-ready` | `运行前收口与配对运行范围` |
| 2026-09-23 | 无模型 dry-run（三臂七项全过）＋配对 Campaign 实验规格起草（DRAFT） | `20260923-soc-dry-run-and-paired-spec` | `dry-run 与配对实验规格` |
| 2026-09-23 | 被拒子代统一结算（三臂同语义、不伪造 Episode、可恢复）＋TASK 状态一致性修正 | `20260923-soc-refused-child-settlement` | `被拒子代统一结算与文档一致性` |
| 2026-09-23 | 随机演化对照与共同分叉 fixture 实施（random_evolution + summary-delivery-b；本地，446 项测试） | `20260923-soc-random-evolution-and-branching-fixture` | `随机演化对照与分叉 fixture 实施` |
| 2026-09-23 | TASK修订：random_evolution公平对照与共同可观察分叉fixture（DRAFT；未实现运行） | `20260923-soc-t05b-random-evolution-and-richer-fixture` | `公平对照与共同分叉 fixture` |
| 2026-09-23 | 八机会材料空间探针执行规格（原SPEC§13，独立DRAFT；入口缺口/预算/停止明确，未实施运行） | `20260923-soc-material-space-probe-draft` | `八机会材料空间探针执行规格` |
| 2026-09-23 | 12集预检三问实证、首集后零新增、G1/G2未放行（本地复算） | `20260923-soc-preflight-three-questions-gates` | `预检三问复算与G1/G2阶段判断` |
| 2026-09-23 | 预检执行（服务器；两臂共享一个截止，各 6 集，路线读数均 2） | `20260923-soc-preflight-executed` | `预检执行登记` |
| 2026-09-23 | 运行包限时（两臂共享总截止）· 不使用 --resume · 清理收窄三处修正 | `20260923-soc-run-package-timing-and-cleanup-fix` | `运行包限时续跑清理修正` |
| 2026-09-23 | 合法路线计算＋运行前离线校准＋预检运行包（本地未运行；待最后一次确认） | `20260923-soc-routes-and-run-package` | `合法路线与预检运行包` |
| 2026-09-23 | 实施 R3 有限改动（MBR v1／预算 12/12／共享阳性最小验证；本地未运行） | `20260923-soc-r3-limited-implementation` | `实施R3有限改动` |
| 2026-09-23 | 准备稿按四点修正为自洽最终稿（费用/预检总预算/MBR顺序与态数/共享验收，DRAFT R3） | `20260923-soc-readiness-final-self-consistency` | `准备稿四点修正自洽最终稿` |
| 2026-09-23 | 实验版本准备稿（只读）：两槽可达性映射、预检方案与总上限、九项差异与实施顺序（DRAFT R2） | `20260923-soc-experiment-readiness-package` | `实验版本准备稿写回SPEC/TASK` |
| 2026-09-23 | G1观察条件收敛为具体推荐值（只读；资源上限/MBR v1/授权四态/共享阳性，DRAFT R1） | `20260923-soc-g1-observation-convergence` | `G1观察条件收敛` |
| 2026-09-23 | 共同材料生成/方向/失败机会修正，空槽读取与自然12×2离线多代链，交接原TASK | `20260923-soc-common-generation-natural-chain` | `共同材料生成与自然多代链收口交接` |
| 2026-09-23 | 80集有效变化分组（11内容组、6已读变化/4未读变化）与G1观察条件DRAFT收敛 | `20260923-soc-effective-variation-and-g1-draft` | `有效变化分析与G1观察条件草案收敛` |
| 2026-09-23 | 80集第7次才进入材料、第8次才分叉，区分候选触达与变更内容触达 | `20260923-soc-g1-exposure-timing` | `80集材料进入时点与分叉解释复核` |
| 2026-09-23 | 三处反馈缺口修复，真实工具离线贯通、身份/顺序/冷却反例与397项回归 | `20260923-soc-feedback-chain-fixes` | `反馈链三处缺口修复与贯通验证` |
| 2026-09-23 | 独立复核④⑤：来源范围、冷却代表反例与父基线身份缺口，纠正收口状态 | `20260923-soc-source-baseline-closeout-review` | `④⑤独立收口复核与状态纠正` |
| 2026-09-23 | ④⑤收口：来源连接证据（引用＋传播见证）＋父子基线冻结（方案②），后续收口结论由 TASK §22 纠正 | `20260923-soc-source-edge-and-baseline` | `来源连接证据与父子基线冻结` |
| 2026-09-23 | 变异计划记录义务方向（SS-010），收敛剩余为两项待裁定 | `20260923-soc-plan-obligation-direction` | `变异计划记录义务方向` |
| 2026-09-23 | 按义务方向的根重启日程（SS-010 共同日程），更新剩余遗漏 | `20260923-soc-per-direction-schedule` | `按方向的根重启日程` |
| 2026-09-23 | J 显式来源边（内容传播见证）＋父子事件保持率报告，更新剩余遗漏 | `20260923-soc-j-edge-and-retention` | `J 显式来源边与父子保持率` |
| 2026-09-23 | B/J 单元级选择与代表索引（机会/出现次数排序＋键绑定），更新剩余遗漏 | `20260923-soc-unit-level-selection` | `B/J 单元级选择与代表索引` |
| 2026-09-23 | C1–C6 契约修复实施（离线验证）：风险事实/代表档案/作用域/根重启/冷却，列明 B/J 剩余遗漏 | `20260923-soc-c1c6-contract-fixes` | `C1–C6 契约修复实施` |
| 2026-09-23 | 分叉分析口径更正（210 投影/430 遗漏）＋coverage/search 契约符合性核对（C1–C6，只登记不修复） | `20260923-soc-divergence-erratum-and-contract-review` | `分叉口径更正与契约符合性核对` |
| 2026-09-23 | 可诊断性修复（离线故障注入证明）＋轨迹分叉重分析＋G1 修订草案（DRAFT） | `20260923-soc-diagnostics-fix-and-reanalysis` | `可诊断性修复与分叉重分析` |
| 2026-09-23 | G1 本地只读诊断：总账与失败成本单列、MBR 差异实质、105 次阻断分类、路线口径契约冲突、第 81 集根因代码定位 | `20260923-soc-g1-local-diagnostics` | `G1 本地只读诊断（RUN_INCOMPLETE）` |
| 2026-09-22 | G1 执行 80/100 后因入口失败中止（容器未产出 bundle 事件），未产生结论；已完整留证 | `20260922-soc-g1-aborted-entry-failure` | `G1 执行中止于第 81 集` |
| 2026-09-22 | G1 运行前冻结：身份与确定性校准通过，但预算/费用上限与 near-miss、合法路线定义缺项阻塞，未发起模型请求 | `20260922-soc-g1-pre-run-freeze-blocked` | `G1 前冻结发现缺项并停止` |
| 2026-09-22 | 第五次正常对照通过（严格闸门 7/7）：真实读取＋绑定暴露＋合法交付；登记"实际用 8 次预算而非批准 12"的偏差 | `20260922-soc-normal-control-passed` | `正常对照通过（严格闸门 7/7）` |
| 2026-09-22 | 登记正常对照预算校准版本（12 次调用＋业务停止条件，G1／两臂不变），待批准运行 | `20260922-soc-normal-control-calibration-12` | `正常对照预算校准版本登记` |
| 2026-09-22 | 首轮提示与工具契约审查：修正任务文本后材料被发现并读取，但 8 步预算在交付前耗尽，闸门仍不通过 | `20260922-soc-first-turn-review-and-fourth-run` | `首轮契约审查与第四次正常对照` |
| 2026-09-22 | 修好关闭观测并启用严格闸门：第三次正常对照关闭与证据链通过，但合法交付未发生，判定不通过 | `20260922-soc-normal-control-strict-gate-not-accepted` | `严格闸门下的第三次正常对照未通过` |
| 2026-09-22 | 修复宿主探针后重跑正常对照：最终化通过，但宿主关闭观测恒假导致 complete 永不为真 | `20260922-soc-normal-control-second-run-closure-unproven` | `宿主探针修复后正常对照仍关闭未证明` |
| 2026-09-22 | 首次正常对照执行：真实模型与容器链路打通，失败于宿主最终化（同步探针在事件循环内调用 asyncio.run） | `20260922-soc-normal-control-first-run-failed` | `首次正常对照执行失败于宿主最终化` |
| 2026-09-22 | 服务器预检与运行身份核对：修正 CRLF 与清单行尾两处身份缺陷，重建 LF 运行镜像 | `20260922-soc-server-precheck-identity` | `结构化服务器预检与运行身份核对` |
| 2026-09-22 | 服务器运行准备：D7 裁定、构建最新镜像与运行身份、预算摘要与批准清单 | `20260922-soc-run-preparation` | `结构化服务器运行准备与运行身份` |
| 2026-09-22 | 实施真实实验入口接线与正式 fixture 装配（I1–I3），登记 D7 与证据缺口 | `20260922-soc-real-entry-implemented` | `结构化真实实验入口与正式资产装配实施` |
| 2026-09-22 | 批准真实实验入口接线与正式资产装配（I1–I3），修正交付口径并登记契约实现差异 | `20260922-soc-real-entry-and-assets-approved` | `结构化真实实验入口与资产装配获批` |
| 2026-09-22 | 接手核对 10 个提交与 297 项结构化测试，逐段核对正式数据流并整理集中运行包 | `20260922-soc-handoff-run-readiness` | `结构化运行包接手核对与集中运行准备` |
| 2026-09-22 | 批准限定两臂离线前移，正常对照并入集中真实验证 | `20260922-soc-offline-first-two-arm` | `结构化两臂离线前移与集中真实验证` |
| 2026-09-22 | 面向导师还原新 TASK 施工前的场景、工具、16 种子与 Agent 自由度，完整整理 15×2 与 8 次拒绝 | `20260922-pre-structured-task-historical-report` | `施工前项目与 15×2 历史说明` |
| 2026-09-21 | 三份结构化情境 SPEC 获批、旧 R1 被替代，新增八步 DRAFT 实施任务 | `20260921-structured-scenario-spec-approval-task-plan` | `结构化情境规格批准与实施任务草案` |
| 2026-09-21 | 结构化情境研究重设计：安全义务、搜索空间、双覆盖反馈三份 DRAFT 契约及交叉审查 | `20260921-structured-scenario-three-contracts` | `结构化情境三份契约设计` |
| 2026-09-21 | 双覆盖草案静态自审：纠正前提有效性与局部保持假设，收紧首版、补联合消融及预算/评估边界 | `20260921-dual-coverage-mechanism-self-review` | `双覆盖机制草案 R1 自审修订` |
| 2026-09-21 | 以双覆盖机制为研究对象，提出风险状态与行为路线档案、调度及随机演化/单维对照；仅设计草案 | `20260921-dual-coverage-mechanism-draft` | `双覆盖机制研究设计草案` |
| 2026-09-08 | 当前源码 Stage 8 故障注入、Stage 9 Docker 四组合和旧设计边界复核 | `20260908-office-v2-stage8-stage9-current-acceptance` | `Stage 8/9 当前证据与架构边界复核` |
| 2026-09-05 | Stage 6 文档、测试和兼容边界清理 | `20260905-office-v2-stage6-compatibility-cleanup` | `Office V2 阶段 6 兼容性清理` |
| 2026-09-04 | Office V2 架构闭环、验证与发布唯一施工顺序 | `20260904-office-v2-architecture-validation-release-plan` | `Office V2 架构闭环、验证与发布唯一施工计划` |
| 2026-09-04 | Office V2 旧 Frontier 退出正式状态、调度、晋升和结算链 | `20260904-office-v2-step1-frontier-formal-chain-cut` | `Office V2 步骤 1 Frontier 正式链切断` |
| 2026-08-31 | Office V2 唯一纠偏计划、旧计划清理、Judge 暂停和分批 checkpoint | `20260831-office-v2-semantic-mutation-correction-plan` | `Office V2 覆盖率引导语义变异纠偏计划` |
| 2026-08-23 | Harness H7 回环 Qwen 入口、源码快照与服务器在线构建 | `20260823-v020rc2-harness-online-entry` | `Harness H7 在线服务器前入口` |
| 2026-08-23 | Stage 6 最新修复与 DeepSeek Harness H0-H6 整合为 v0.2.0-rc.1，Judge 保持分离 | `20260823-v020rc1-stage6-harness-integration` | `Stage 6 与 Harness H0-H6 候选整合` |
| 2026-08-22 | DeepSeek Harness H6 三代 Coverage/Campaign、恢复与 Docker 聚焦验收 | `20260822-deepseek-harness-h6-complete` | `DeepSeek Harness H6 本地闭环完成` |
| 2026-08-22 | DeepSeek Harness H5 producer 身份、Recording、Strict Replay 与 Verification-only Fork | `20260822-deepseek-harness-h5-replay-parity` | `DeepSeek Harness H5 录制重放同步` |
| 2026-08-22 | DeepSeek Harness H4 完整 Office V2 直执行、可信多轮与 Oracle | `20260822-deepseek-harness-h4-direct-parity` | `DeepSeek Harness H4 直执行同步` |
| 2026-08-22 | DeepSeek Harness H2-H3 Runtime 入口、官方 Runtime 与 Office V2 垂直链 | `20260822-deepseek-harness-h3-vertical-slice` | `DeepSeek Harness H3 垂直链` |
| 2026-08-21 | Agent Runtime 合同扩展与 DeepSeek Harness H0-H1 可行性 | `20260821-agent-runtime-pluggable-h0` | `Agent Runtime 可插拔边界与 Harness H0` |
| 2026-08-17 | Office V2 第六步修订为本地完整性门、真实 2 代接通和可恢复 20-50 代连续 Campaign | `20260817-office-v2-step6-continuous-campaign-plan-revision` | `Office V2 第六步连续 Campaign 计划修订` |
| 2026-08-17 | Office V2 第六步 Qwen3.5 35B-A3B 真实 Agent/Mutator 服务器验收计划冻结 | `20260817-office-v2-step6-qwen35-plan-freeze` | `Office V2 第六步真实模型计划冻结` |
| 2026-08-17 | Office V2 第五步 5.13-5.15 Docker、Fork、Campaign run/resume 与最终证据收口 | `20260817-office-v2-step5-final-closure` | `Office V2 第五步最终闭环` |
| 2026-08-17 | Office V2 第五步 5.0-5.12 身份、预算、结算、Finding/反馈与无模型三代恢复闭环；Docker 门待运行 | `20260817-office-v2-step5-loop-implementation` | `Office V2 第五步无模型反馈闭环` |
| 2026-08-17 | Office V2 第五步计划修订：无 Episode 结算、Mutation 预算前置、非终态基线、验证 Fork、反馈重算与 Finding 重放去重 | `20260817-office-v2-step5-plan-contract-fixes` | `Office V2 第五步计划合同修订` |
| 2026-08-15 | Office V2 第五步单候选 Episode 执行、Coverage 反馈、原子结算、三代恢复与 Replay/Fork 详细计划 | `20260815-office-v2-step5-multigeneration-loop-plan` | `Office V2 第五步多代反馈闭环计划` |
| 2026-08-15 | Office V2 第四步 4.0-4.12 受控单候选变异、宿主校验、Stage 5 物化与 SQLite 恢复技术验收 | `20260815-office-v2-step4-implementation-acceptance` | `Office V2 第四步技术验收` |
| 2026-08-15 | Office V2 第四步 4.0 Mutation 身份、Scheduler 上下文/算子 Allocation 与旧 GenerationAllocation 兼容边界 | `20260815-office-v2-step4-4-0-boundary-identity` | `Office V2 4.0 边界与身份锁` |
| 2026-08-15 | Office V2 第四步计划审查修订：Provider 权限、Scheduler 重定向、Preparation 生命周期、字段注册表、预算与拒绝反馈 | `20260815-office-v2-step4-plan-contract-fixes` | `Office V2 第四步计划合同修订` |
| 2026-08-15 | Office V2 第四步受控语义变异详细计划、单候选边界、Provider/校验/物化/恢复合同 | `20260815-office-v2-step4-controlled-mutation-plan` | `Office V2 第四步受控语义变异计划` |
| 2026-08-15 | Office V2 第三步真实 Coverage、全状态原子结算与重开调度闭合 | `20260815-office-v2-step3-integration-closure` | `Office V2 第三步集成闭合` |
| 2026-08-15 | Office V2 第三步 3.0-3.11 Corpus、双 Frontier、单候选调度、收据恢复与统一证据 | `20260815-office-v2-step3-implementation-acceptance` | `Office V2 第三步技术验收` |
| 2026-08-15 | Office V2 第三步 3.0 六组件身份清单、上游摘要锁与 V1 创建前拒绝 | `20260815-office-v2-step3-3-0-identity-lock` | `Office V2 3.0 身份锁` |
| 2026-08-15 | Office V2 第三步暴露阶段、支持执行、行为缺口、局部状态与有界 attempt 合同修订 | `20260815-office-v2-step3-exposure-support-retry-contract` | `Office V2 第三步暴露与尝试合同修订` |
| 2026-08-15 | 用户确认最小 AttackSeed、ExecutionRecord/CorpusEntry 分责与逐候选反馈 | `20260815-office-v2-step3-minimal-seed-single-candidate` | `Office V2 最小种子与单候选循环` |
| 2026-08-15 | Office V2 第三步设计审查：双前沿、比较组、能力清单与两阶段执行收据 | `20260815-office-v2-step3-plan-contract-fixes` | `Office V2 第三步计划合同修订` |
| 2026-08-14 | Office V2 第三步 Corpus、RiskFrontier、晋升、公平调度与恢复详细设计草案 | `20260814-office-v2-step3-corpus-frontier-plan` | `Office V2 第三步种子库与风险调度计划` |
| 2026-08-14 | Office V2 覆盖第二步 2.4-2.8 风险目录、双覆盖、批公平与统一冻结证据 | `20260814-office-v2-coverage-step2-freeze` | `Office V2 Coverage 第二步冻结` |
| 2026-08-14 | Office V2 覆盖第二步 2.3 状态、可信交互、终止与完整 BehaviorProfile | `20260814-office-v2-coverage-step2-3-complete-profile` | `Office V2 Coverage 2.3 完整行为档案` |
| 2026-08-14 | Office V2 覆盖第二步 2.2 工具路径、参数来源、权限与结果分支提取 | `20260814-office-v2-coverage-step2-2-tool-extraction` | `Office V2 Coverage 2.2 工具行为提取` |
| 2026-08-14 | Office V2 覆盖第二步 2.1 行为合同、实例归一化与有界循环路径 | `20260814-office-v2-coverage-step2-1-behavior-contracts` | `Office V2 Coverage 2.1 行为合同与有界路径` |
| 2026-08-14 | Office V2 覆盖第二步 2.0 风险/批基线/Utility 合同与 V1 资产隔离 | `20260814-office-v2-coverage-step2-0-contracts` | `Office V2 Coverage 2.0 合同冻结` |
| 2026-08-14 | Office V2 Stage 9.1 三路径可信 CoverageInput、事实摘要与旧资产隔离 | `20260814-office-v2-stage9-v2-coverage-input` | `Office V2 Stage 9.1 CoverageInput` |
| 2026-08-12 | Office V2 8.5 生产路径静态审计与旧删除前置条件顺序冲突 | `20260812-office-v2-stage8-step-8-5-v1-disposition` | `Office V2 8.5 V1 处置阻塞审计` |
| 2026-08-12 | Office V2 8.3 全结构门离线重算与 8.4 既有 Docker 证据摘要复核 | `20260812-office-v2-stage8-steps-8-3-8-4-evidence` | `Office V2 8.3-8.4 结构门与 Docker 复核` |
| 2026-08-12 | Office V2 8.2 E1/E2/E3 按冻结能力重构，E3 参数来源 Docker 验收与 Oracle 证据适配修复 | `20260812-office-v2-stage8-step-8-2-examples` | `Office V2 8.2 E1-E3 验收与参数来源证据` |
| 2026-08-12 | Office V2 Stage 8 计划、8.0 验收入口审计与 8.1 五故事冻结 | `20260812-office-v2-stage8-steps-8-0-8-1` | `Office V2 8.0-8.1 验收映射与五故事冻结` |
| 2026-08-12 | Office V2 7.11 当前 V2 超时、取消、隔离复用、错误分类与零残留验收 | `20260812-office-v2-stage7-step-7-11-lifecycle` | `Office V2 7.11 生命周期与失败恢复` |
| 2026-08-12 | Office V2 7.10 四入口 safe/full 与复合 partial/full 的最小 Docker 校准 | `20260812-office-v2-stage7-step-7-10-docker-controls` | `Office V2 7.10 Docker 控制校准` |
| 2026-08-11 | Office V2 7.9 本机确定性 Docker 长链、授权链、录制重放、传输修复与零当前运行残留 | `20260811-office-v2-stage7-step-7-9-docker-clean` | `Office V2 7.9 本机 Docker 长链` |
| 2026-08-11 | 用户决定完成 7.9-7.11 本机 Docker 门后先做场景冻结和覆盖变异闭环，真实 Qwen 服务器验收合并延后 | `20260811-defer-office-v2-server-until-closed-loop` | `Office V2 服务器验收延后到闭环后` |
| 2026-08-11 | Office V2 Stage 7 的正式多轮执行、可信 Oracle、V2 recording/checkpoint/codec、Manifest 完整性与 strict replay | `20260811-office-v2-stage7-steps-7-0-7-1-contract` | `Office V2 7.0-7.8 正式执行、Oracle、Recording 与 Strict Replay` |
| 2026-08-11 | 用户确认 Office V2 阶段 6 正式冻结，并完成 Stage 7 Docker 真实 Agent 集成详细计划 | `20260811-office-v2-stage6-freeze-stage7-plan` | `Stage 6 正式冻结与 Stage 7 详细计划` |
| 2026-08-11 | Office V2 阶段 6 步骤 6.12-6.13 的 Clean Case、目标/入口/权限集成验收与自校验证据 | `20260811-office-v2-stage6-steps-6-12-6-13-acceptance` | `Office V2 6.12-6.13 集成与阶段证据` |
| 2026-08-11 | Office V2 阶段 6 步骤 6.11 的三路径独立重建、重新求值、外部摘要锁与七类篡改拒绝 | `20260811-office-v2-stage6-step-6-11-rebuild-replay-equivalence` | `Office V2 6.11 重建与 Replay 等价门` |
| 2026-08-11 | Office V2 阶段 6 步骤 6.10 的中立 TRACE、可信工具/交互事实映射与 direct/recording 等价 | `20260811-office-v2-stage6-step-6-10-trace-recording` | `Office V2 6.10 TRACE 与 Recording 映射` |
| 2026-08-11 | Office V2 阶段 6 步骤 6.9 的原子目标补全、Oracle 纯组合、自包含 JSON 与证据闭包 | `20260811-office-v2-stage6-step-6-9-scenario-oracle` | `Office V2 6.9 ScenarioOracle 组合` |
| 2026-08-11 | Office V2 阶段 6 步骤 6.8 的四入口 intent、精确观察与参数来源使用证据 | `20260811-office-v2-stage6-step-6-8-exposure` | `Office V2 6.8 四入口 Exposure` |
| 2026-08-11 | Office V2 阶段 6 步骤 6.7 的权限层、已提交副作用和 planned/unexpected 独立违规事实 | `20260811-office-v2-stage6-step-6-7-policy-violations` | `Office V2 6.7 权限与独立违规事实` |
| 2026-08-11 | Office V2 ToolSpec 1.1、A06 合同闭合、Stage 3-5 串行重建与 6.6 完成 | `20260811-office-v2-tool-contract-v1-1-a06-close` | `Office V2 ToolSpec 1.1 与 A06 闭合` |
| 2026-08-10 | Office V2 阶段 6 步骤 6.5-6.6 的通用目标事实匹配、复合里程碑和 A06 合同冲突 | `20260810-office-v2-stage6-steps-6-5-6-6-objective-milestones` | `Office V2 6.5-6.6 目标事实与复合里程碑` |
| 2026-08-10 | Office V2 阶段 6 步骤 6.4 的 TaskGoalGraph、来源证据、可信授权和正确拒绝求值 | `20260810-office-v2-stage6-step-6-4-utility-evaluator` | `Office V2 6.4 TaskGoalGraph Utility 求值` |
| 2026-08-10 | Office V2 阶段 6 步骤 6.2-6.3 的可信证据包与声明式正常任务断言目录 | `20260810-office-v2-stage6-steps-6-2-6-3-evidence-utility-catalog` | `Office V2 6.2-6.3 证据包与 Utility 目录` |
| 2026-08-10 | Office V2 阶段 6 步骤 6.1 的严格 Oracle 输出、证据引用与无部分结论失败合同 | `20260810-office-v2-stage6-step-6-1-contracts` | `Office V2 6.1 Oracle 严格合同` |
| 2026-08-10 | Office V2 阶段 6 步骤 6.0 的 Stage 2-5 身份与禁止依赖边界基线 | `20260810-office-v2-stage6-step-6-0-boundary` | `Office V2 6.0 Oracle 边界基线` |
| 2026-08-10 | 用户确认 Office V2 阶段 5 并完成阶段 6 事实 Oracle 详细计划 | `20260810-office-v2-stage6-fact-oracle-plan` | `Office V2 阶段 5 确认与阶段 6 事实 Oracle 计划` |
| 2026-08-10 | Office V2 阶段 5 步骤 5.13 冻结证据与用户确认门 | `20260810-office-v2-stage5-step-5-13-freeze-evidence` | `Office V2 5.13 冻结证据` |
| 2026-08-10 | Office V2 阶段 5 步骤 5.12 的 24 代表案例、四入口正反事实和 12 目标真实 Runtime witness | `20260810-office-v2-stage5-step-5-12-representatives-witnesses` | `Office V2 5.12 代表案例与目标见证` |
| 2026-08-08 | Office V2 阶段 5 步骤 5.6-5.11 四入口、兼容性求解与原子案例物化 | `20260808-office-v2-stage5-steps-5-6-5-11` | `Office V2 5.6-5.11 四入口与原子物化` |
| 2026-08-08 | Office V2 阶段 5 步骤 5.0-5.5 严格合同、12 目标、四域字段注册表与 24 可达表面 | `20260808-office-v2-stage5-steps-5-0-5-5` | `Office V2 5.0-5.5 目标与可达表面` |
| 2026-08-08 | 用户确认 Office V2 阶段 4 并完成阶段 5 四入口、可达表面和复合目标详细计划 | `20260808-office-v2-stage5-attack-entry-plan` | `Office V2 阶段 5 四入口详细计划` |
| 2026-08-08 | Office V2 阶段 4 步骤 4.11 自校验证据、冻结聚焦集与用户确认门 | `20260808-office-v2-stage4-step-4-11-freeze-evidence` | `Office V2 4.11 冻结证据` |
| 2026-08-08 | Office V2 阶段 4 步骤 4.10 真实 Surface、业务工具证据与多轮 API 组合验收 | `20260808-office-v2-stage4-step-4-10-api-composition` | `Office V2 4.10 API 组合验收` |
| 2026-08-08 | Office V2 阶段 4 步骤 4.9 中立交互 TRACE、摘要阶段与回滚事实 | `20260808-office-v2-stage4-step-4-9-neutral-interaction-trace` | `Office V2 4.9 中立交互 TRACE` |
| 2026-08-07 | Office V2 阶段 4 步骤 4.8 确定性回复、选择、限时授权与 LangGraph 多轮回灌 | `20260807-office-v2-stage4-step-4-8-deterministic-response` | `Office V2 4.8 确定性回复闭环` |
| 2026-08-07 | Office V2 阶段 4 步骤 4.7 结构化澄清 control、冻结请求匹配与可见来源门 | `20260807-office-v2-stage4-step-4-7-clarification-match` | `Office V2 4.7 澄清请求匹配` |
| 2026-08-07 | Office V2 阶段 4 步骤 4.6 通用 LangGraph session surface、V1 身份锁与 recorder 绑定修复 | `20260807-office-v2-stage4-step-4-6-session-seam` | `Office V2 4.6 LangGraph session 接缝` |
| 2026-08-07 | Office V2 阶段 4 步骤 4.5 同源 17 工具模型协议、封闭错误映射与可信结果投影 | `20260807-office-v2-stage4-step-4-5-agent-api` | `Office V2 4.5 模型工具 API` |
| 2026-08-07 | Office V2 阶段 4 步骤 4.4 独立基础规则、动态 system context 与四摘要 envelope | `20260807-office-v2-stage4-step-4-4-dynamic-prompt` | `Office V2 4.4 动态 Prompt envelope` |
| 2026-08-07 | Office V2 阶段 4 步骤 4.3 可见政策、任务委托、17 工具能力与完整 context 组装 | `20260807-office-v2-stage4-step-4-3-policy-capability` | `Office V2 4.3 政策委托与能力摘要` |
| 2026-08-07 | Office V2 阶段 4 步骤 4.2 权威身份、组织、时间与发行者派生 | `20260807-office-v2-stage4-step-4-2-identity-rendering` | `Office V2 4.2 权威身份派生` |
| 2026-08-07 | Office V2 阶段 4 步骤 4.1 上下文与证据 sidecar 严格合同 | `20260807-office-v2-stage4-step-4-1-context-contracts` | `Office V2 4.1 上下文证据合同` |
| 2026-08-07 | Office V2 阶段 4 步骤 4.0 身份、上游摘要与 V1 相邻基线 | `20260807-office-v2-stage4-step-4-0-boundary` | `Office V2 4.0 边界与相邻基线` |
| 2026-08-07 | 用户确认 Office V2 阶段 3 并完成阶段 4 Agent 办公认知与真实 API 详细计划 | `20260807-office-v2-stage4-agent-context-api-plan` | `Office V2 阶段 3 确认与阶段 4 详细计划` |
| 2026-08-07 | Office V2 阶段 3 步骤 3.12 冻结证据、九步长链、权限反例、回滚与六类扰动 | `20260807-office-v2-stage3-step-3-12-freeze` | `Office V2 3.12 集成冻结门` |
| 2026-08-06 | Office V2 阶段 3 步骤 3.11 的 24 参考执行、12+ 路径和六类上游扰动 | `20260806-office-v2-stage3-step-3-11-reference-catalog` | `Office V2 3.11 参考目录与上游扰动` |
| 2026-08-06 | Office V2 阶段 3 步骤 3.10 的四条来源可审计参考长链与通用 ResourceRef 证据修复 | `20260806-office-v2-stage3-step-3-10-causal-chains` | `Office V2 3.10 参考长链与来源账本` |
| 2026-08-06 | Office V2 阶段 3 步骤 3.9 的 10 个目标图蓝图、24 个干净 Case 和可信交互冻结 | `20260806-office-v2-stage3-step-3-9-task-cases` | `Office V2 3.9 正常任务与干净案例` |
| 2026-08-06 | Office V2 阶段 3 步骤 3.8 独立 17 ToolSpec、公开摘要和 V1 不变性通过 | `20260806-office-v2-stage3-step-3-8-tool-specs` | `Office V2 3.8 独立工具合同` |
| 2026-08-06 | Office V2 阶段 3 步骤 3.1-3.7 中立工具事实、统一运行时和四域 17 handler 通过 | `20260806-office-v2-stage3-steps-3-1-3-7-tools` | `Office V2 3.1-3.7 四域工具运行时` |
| 2026-08-06 | Office V2 阶段 3 步骤 3.0 工具目录、版本、文件/import 与 Stage 2 摘要边界通过 | `20260806-office-v2-stage3-step-3-0-boundary` | `Office V2 3.0 工具边界与身份基线` |
| 2026-08-06 | 用户确认阶段 2 并完成阶段 3 四域工具、10/24 正常任务和因果链详细计划 | `20260806-office-v2-stage3-tools-causal-plan` | `Office V2 阶段 3 工具与因果链计划` |
| 2026-08-06 | Office V2 阶段 2 集成切片、真实合同缺口修复和 81 项冻结门通过 | `20260806-office-v2-stage2-step-2-11-freeze` | `Office V2 2.11 集成冻结门` |
| 2026-08-06 | Office V2 阶段 2 步骤 2.10 认证回复、限时窄授权、到期和事务回滚通过 | `20260806-office-v2-stage2-step-2-10-trusted-authorization` | `Office V2 2.10 可信授权状态转换` |
| 2026-08-06 | Office V2 阶段 2 步骤 2.9 类型谓词、跨页解析、消歧和冻结 binding 通过 | `20260806-office-v2-stage2-step-2-9-resolution` | `Office V2 2.9 执行前资源解析` |
| 2026-08-06 | Office V2 阶段 2 步骤 2.8 权限观察、脱敏和稳定分页通过 | `20260806-office-v2-stage2-step-2-8-observation` | `Office V2 2.8 部分观察与分页` |
| 2026-08-06 | Office V2 阶段 2 步骤 2.7 正式固定世界、manifest 和质量门通过 | `20260806-office-v2-stage2-step-2-7-canonical-data` | `Office V2 2.7 固定世界数据` |
| 2026-08-06 | Office V2 阶段 2 步骤 2.6 不可变世界、原子 Episode 事务和 StateDelta 通过 | `20260806-office-v2-stage2-step-2-6-world-transactions` | `Office V2 2.6 世界事务与状态差异` |
| 2026-08-06 | 提前冻结 StateDelta/Oracle 覆盖原料，并永久隔离 V1 与 V2 Coverage/Corpus | `20260806-office-v2-coverage-ready-facts` | `Office V2 覆盖原料前置合同` |
| 2026-08-06 | Office V2 阶段 2 步骤 2.5 四层纯函数权限决定及 enforce/audit 分离通过 | `20260806-office-v2-stage2-step-2-5-policy` | `Office V2 2.5 纯函数权限决策` |
| 2026-08-06 | Office V2 阶段 2 步骤 2.4 任务 DAG、冻结绑定与可信交互合同通过 | `20260806-office-v2-stage2-step-2-4-task-binding-interaction` | `Office V2 2.4 任务绑定与可信交互合同` |
| 2026-08-06 | Office V2 阶段 2 步骤 2.3 四域模型、版本/生命周期和跨域引用图通过 | `20260806-office-v2-stage2-step-2-3-domains` | `Office V2 2.3 四域模型与引用图` |
| 2026-08-05 | Office V2 阶段 2 步骤 2.2 身份组织目录、组闭包和 ActorContext 通过 | `20260805-office-v2-stage2-step-2-2-identity` | `Office V2 2.2 身份组织与组闭包` |
| 2026-08-05 | Office V2 阶段 2 步骤 2.0 独立边界与 2.1 严格公共模型通过 | `20260805-office-v2-stage2-steps-2-0-2-1` | `Office V2 2.0-2.1 独立包与公共模型` |
| 2026-08-05 | Office V2 阶段 1 用户确认及阶段 2 固定世界、身份权限与观察内核详细施工计划 | `20260805-office-v2-stage2-world-kernel-plan` | `Office V2 阶段 2 世界内核详细计划` |
| 2026-08-05 | 固定世界、任务依赖、资源解析、复合目标、部分可观察与可信多轮进入 Office V2 阶段 1 | `20260805-office-v2-stage1-complexity-contracts` | `Office V2 阶段 1 复杂度合同补充` |
| 2026-08-05 | Office Workspace Scenario V2 阶段 1 六项设计工件完成并进入用户确认门 | `20260805-office-v2-stage1-design-package` | `Office V2 阶段 1 设计冻结包` |
| 2026-08-05 | 变异空间宏观计划及场景冻结、V2覆盖定义双前置门 | `20260805-office-v2-mutation-space-plan` | `Office V2 变异空间计划与双前置门` |
| 2026-08-05 | 冻结 Office V1/G6 旧路线，优先完整建设四域 Office Workspace Scenario V2 | `20260805-office-v2-scenario-priority-reset` | `Office V2 场景优先级重置与长线施工计划` |
| 2026-08-04 | 延后远程 G5、先完成本机核心闭环并采用按 digest 复用的快速验证分层 | `20260804-defer-remote-g5-fast-validation` | `延后远程 G5 与快速验证分层` |
| 2026-08-04 | G5 自包含服务器离线包、服务器阶段门脚本与本机 server-ready preflight | `20260804-g5-self-contained-server-ready` | `G5 服务器前准备` |
| 2026-08-04 | 完整 13 工具 LangGraph Agent、TRACE、录制重放、载荷分支与双覆盖一致性 | `20260804-langgraph-full-office-record-replay-fork` | `LangGraph 完整办公可重放证据` |
| 2026-08-04 | LangGraph 最小真实 Agent、自主工具因果链与本机 Qwen Docker 实证 | `20260804-langgraph-real-agent-vertical-slice` | `LangGraph 真实 Agent 纵向切片` |
| 2026-08-04 | 最小自包含 Agent-Qwen 镜像、真实本机 GPU warm-up 与受控清理 | `20260804-self-contained-agent-qwen-image` | `自包含 Agent-Qwen 镜像本机验收` |
| 2026-08-04 | LangGraph Python 3.11 依赖/hash/许可证与正式 Agent 适配边界 | `20260804-langgraph-dependency-architecture-lock` | `LangGraph 依赖与架构锁` |
| 2026-08-04 | 同容器 Qwen + LangGraph 正式 Agent 合同与 5.G 施工门 | `20260804-self-contained-langgraph-agent-contract` | `同容器 Qwen + LangGraph 真实 Agent 合同重置` |
| 2026-08-04 | 办公 MutationPlan 持久子批、token/seed、错误白名单与中断恢复 | `20260804-office-mutation-subbatch-recovery` | `办公变异持久子批与恢复` |
| 2026-08-04 | 办公 Campaign 完成状态、有限预算、暂停取消与精确恢复 | `20260804-office-campaign-completion-state` | `办公 Campaign 完成与预算状态` |
| 2026-08-03 | 办公 RiskFrontier 自适应交错、公平约束、冷却与精确恢复 | `20260803-office-adaptive-interleaving-scheduler` | `办公自适应交错调度` |
| 2026-08-03 | 办公 12 组合公平基线、持久租约与精确恢复 | `20260803-office-fair-baseline-scan` | `办公公平基线扫描` |
| 2026-08-03 | 办公攻击暴露账本、风险前沿、幂等写入与精确恢复 | `20260803-office-objective-ledger-risk-frontier-state` | `办公攻击暴露账本与风险前沿状态` |
| 2026-08-03 | 办公显式目标重定向、合法组件重组与意外风险归因 | `20260803-office-explicit-target-redirection` | `办公显式目标重定向` |
| 2026-08-03 | 办公目标保持型表达变异三段审计、幂等落盘与宿主校验 | `20260803-office-target-preserving-expression-mutation` | `办公目标保持表达变异` |
| 2026-08-03 | 办公 Campaign 目录锁与合法 TestCase 候选生成 | `20260803-office-candidate-generation-contract` | `办公合法候选生成` |
| 2026-08-03 | 场景 Campaign 公平基线、自适应交错与完成语义 | `20260803-scenario-campaign-fairness-completion-contract` | `场景 Campaign 公平覆盖合同` |
| 2026-08-03 | 最终 LLM Mutator/Judge 角色与显式目标重定向合同回正 | `20260803-llm-mutator-judge-retarget-contract` | `LLM 变异评分与目标重定向合同` |
| 2026-08-03 | 办公 Campaign 精确行为-风险热力图、增长与无增益区间 | `20260803-office-campaign-coverage-feedback` | `办公 Campaign 覆盖反馈` |
| 2026-08-03 | 办公 Campaign 累计覆盖、身份锁与中断幂等恢复 | `20260803-office-campaign-coverage-recovery` | `办公 Campaign 累计覆盖与恢复` |
| 2026-08-03 | 可信办公证据驱动的版本化风险映射与四阶段语义 | `20260803-office-risk-evidence-mapping` | `办公风险执行证据映射` |
| 2026-08-01 | 可信办公证据驱动的行为新颖度与 fork 全路径特征 | `20260801-office-behavior-novelty-evidence` | `办公行为新颖度执行证据` |
| 2026-08-01 | 办公轨迹、冻结案例与状态进入 CoverageInput 执行证据合同 | `20260801-office-coverage-input-evidence-contract` | `办公 CoverageInput 执行证据合同` |
| 2026-08-01 | 真实 Qwen 后移，先完成办公双覆盖率与多代灰盒闭环 | `20260801-defer-qwen-complete-greybox-loop` | `先闭环后真实 Qwen 的施工顺序` |
| 2026-08-01 | 办公载体 payload fork、父不可变与子 strict replay | `20260801-office-carrier-payload-fork` | `办公载荷分支替换` |
| 2026-08-01 | 完整办公请求录制与安全/脆弱 strict replay | `20260801-office-recording-strict-replay` | `办公 recording 与 strict replay` |
| 2026-08-01 | 办公安全/脆弱 Docker Episode、失败终止与零残留 | `20260801-office-docker-episode-validation` | `办公 Docker Episode 验收` |
| 2026-08-01 | 13 项办公容器工具、状态恢复与 TRACE-ReAct 接入 | `20260801-office-container-tool-bridge` | `办公容器工具桥` |
| 2026-08-01 | 办公 Episode 初始化版本、物化状态与完整性边界 | `20260801-office-episode-initialization-contract` | `办公 Episode 初始化合同` |
| 2026-08-01 | 办公场景确定性脆弱控制与六类攻击正例 | `20260801-office-vulnerable-control-calibration` | `办公场景脆弱控制校准` |
| 2026-08-01 | 办公场景确定性安全控制与 18 案例负向校准 | `20260801-office-safe-control-calibration` | `办公场景安全控制校准` |
| 2026-08-01 | 办公场景共享状态、授权记录与证据内核 | `20260801-office-shared-state-runtime` | `办公场景共享状态内核` |
| 2026-08-01 | 办公场景首批 6+12 冻结测试矩阵 | `20260801-office-initial-test-matrix` | `办公场景第一批测试矩阵` |
| 2026-08-01 | 办公场景任务、目标与载体有效组合规则 | `20260801-office-composition-compatibility-rules` | `办公场景有效组合规则` |
| 2026-08-01 | 办公场景三类注入载体与确定性物化 | `20260801-office-injection-carrier-catalog` | `办公场景注入载体目录` |
| 2026-08-01 | 办公场景六类攻击目标与执行证据目录 | `20260801-office-attack-objective-catalog` | `办公场景攻击目标目录` |
| 2026-08-01 | 办公场景六类可参数化正常任务目录 | `20260801-office-benign-task-catalog` | `办公场景正常任务目录` |
| 2026-08-01 | 办公场景可组合合同与固定案例数据化 | `20260801-office-scenario-composable-contracts` | `办公场景可组合合同` |
| 2026-08-01 | 办公协作场景 V1 授权边界与施工计划 | `20260801-office-collaboration-authorization-boundary` | `办公协作场景 V1 授权边界` |
| 2026-07-30 | 执行面收敛为唯一 TRACE-ReAct 并废除旧格式 | `20260730-retire-legacy-execution-backends` | `执行面单一化与旧后端全面退役` |
| 2026-07-30 | TRACE-ReAct 真实 Qwen 固定场景与 strict replay 验收 | `20260730-trace-react-qwen-server-validation` | `TRACE-ReAct 真实 Qwen 服务器验收` |
| 2026-07-30 | TRACE-ReAct 服务器前流程加固与离线包重建 | `20260730-trace-react-server-flow-hardening` | `TRACE-ReAct 服务器前流程加固` |
| 2026-07-29 | TRACE-ReAct 自研执行器、Ollama Provider 与离线 kit | `20260729-trace-react-self-owned-executor-kit` | `TRACE-ReAct 自研执行器与离线 kit` |
| 2026-07-29 | AgentDojo 固定案例 2.12 本地检查点 | `checkpoint: complete AgentDojo fixed-case calibration` | `20260729-agentdojo-fixed-case-checkpoint` |
| 2026-07-29 | Execution v2 依赖与许可证审计 | `20260729-execution-v2-dependency-license-audit` | `Execution v2 依赖与许可证审计` |
| 2026-07-29 | AgentDojo v2b 全量回归与零残留复核 | `20260729-agentdojo-v2b-full-regression` | `AgentDojo v2b 全量回归与零残留复核` |
| 2026-07-29 | AgentDojo Docker 失败契约与清理分类 | `20260729-agentdojo-docker-failure-contracts` | `AgentDojo Docker 失败契约与清理分类` |
| 2026-07-29 | AgentDojo v2b 独立 Docker 安全与脆弱控制 | `20260729-agentdojo-docker-controls` | `AgentDojo v2b 独立 Docker 安全与脆弱控制` |
| 2026-07-29 | AgentDojo 进程内安全控制验收 | `20260729-agentdojo-safe-control` | `AgentDojo 进程内安全控制验收` |
| 2026-07-29 | AgentDojo 固定案例直接状态契约 | `20260729-agentdojo-direct-state-contract` | `AgentDojo 固定案例直接状态契约` |
| 2026-07-29 | 精确清理与 AgentDojo v2b 阶段门审计 | `20260729-controlled-cleanup-v2b-audit` | `精确清理与 AgentDojo v2b 阶段门审计` |
| 2026-07-29 | 产品规格回正与 AgentDojo 执行校准阶段门 | `20260729-spec-roadmap-agentdojo-gate` | `产品规格回正与 AgentDojo 执行校准阶段门` |
| 2026-07-29 | Execution v2a 两项 P1 错误契约闭合 | `20260729-execution-v2a-p1-closure` | `Execution v2a 两项 P1 错误契约闭合` |
| 2026-07-28 | Execution v2a 可恢复本地检查点 | `checkpoint: harden execution v2a contracts` | `git log --oneline -3` |
| 2026-07-28 | Execution v2a 终止与完整性契约加固 | `20260728-execution-v2a-contract-hardening` | `Execution v2a 终止与完整性契约加固` |
| 2026-07-28 | Execution v2a 实现与 Docker 验收 | `20260728-execution-v2a-implementation` | `Execution v2a 实现与 Docker 验收` |
| 2026-07-28 | Execution v2 方向与 Inspect/AgentDojo 边界 | `20260728-execution-v2-direction` | `Execution v2 方向与 Inspect/AgentDojo 边界` |
| 2026-07-28 | v2 施工前可恢复本地检查点 | `f5e6cd9` | `checkpoint: preserve error contracts and local Docker validation` |
| 2026-07-28 | 本机 Docker E2E 与离线 kit 重建验收 | `20260728-local-docker-kit-validation` | `本机 Docker E2E 与离线 kit 重建验收` |
| 2026-07-27 | 可恢复本地 Git 检查点 | `1ed79da` | `checkpoint: preserve TRACE-G validation and Ollama recovery` |
| 2026-07-27 | Engine 错误状态契约与语义执行证据门控 | `20260727-engine-error-semantic-evidence` | `Engine 错误状态契约与语义执行证据门控` |
| 2026-07-27 | 真实 Qwen 服务器验证与部分 Fuzzing 闭环 | `20260727-qwen3-server-validation` | `真实 Qwen 服务器验证` |
| 2026-07-27 | Windows SFTP 首传与断点续传修复 | `20260727-sftp-first-upload-resume` | `Windows SFTP 首传与断点续传修复` |
| 2026-07-27 | Ollama 变异分批与失败恢复 | `20260727-ollama-mutation-batching-recovery` | `Ollama 变异分批与失败恢复` |
20260812-office-v2-formal-entry-and-freeze：新增正式 V2 scenario 命令，禁用 V1 正式入口，保留旧资产并冻结 Office V2；见 LOG.md。
