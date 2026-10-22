# FLR-T03：逐项裁定全部 394 个待定文件

状态：`DRAFT`（只读复核任务草案，待用户确认；不授权删除或改写候选）。

日期：2026-10-10。对应 [全面退役 SPEC](../specs/20261010-full-legacy-retirement.md) FLR-01–09。

## 1. 目标与完整范围

对 [FLR-T01 JSON](../reports/20261010-full-legacy-retirement-inventory.json) 的全部 394 待定文件（49,161,766 B）建立后续处置，不只复核 4 个孤立源码。每个对象必须有“保留 / 退役候选 / 需先解除依赖 / 外部决策或证据不足”的具体处理和依据；对应三分类保留为禁删、满足条件为可删，未解决为待定。不为了让表格清零而删仍有价值的对象。

输入 SHA256 `cb06161cad17f8ab2a93b4f2bb626b4ce314f7bfe7944c607b7a93c7b0815d4c`，HEAD `b6901f1a504812abbd92b0d88e5b7feab1538209`。精确对象是 files[classification=待定]，附录逐项列出。766 可删缓存由 [FLR-T02](20261010-flr-t02-cache-retirement.md) 处理；298 禁删项仅作保护/依赖检查，原文不改。10 个边界独立登记，不混入 394 数量，也不借此删除边界数据。

本 TASK 是只读准入证明，不含源码/测试/文档删除、移动、归档、压缩，也不撤 CLI、包导出、adapter 或构建资产。不修改四条硬设计、版本、实验数据/口径/部署，不恢复用户已删文档。

## 2. 前置与被依赖

- FLR-T01 DONE，全面退役 SPEC 已批准；本 TASK 获确认后 READY → IN_PROGRESS。
- 与 T02 无硬依赖，可先 T02 再 T03，cache 集合和 pending 集合互斥。
- 后续每个源码路线/文档族的删除或解除依赖 TASK 依赖本裁定的精确路径、保留项、接口影响和回退证据，仍须用户批准。
- 工作前读最新 LOG，保护并行未提交改动；报告旧快照哈希变化只重审受影响项，不覆盖新内容。

## 3. 复核工作包和预计区域

| 工作包 | 文件数 | B | 核验重点 |
|---|---:|---:|---|
| P03-SRC | 152 | 1,900,813 | 模块、发布/容器/动态入口、公共 API、旧路线闭合依赖 |
| P03-TEST | 196 | 1,801,587 | pytest 收集、conftest/跨测试辅助、当前四条设计与历史重放价值 |
| P03-CONFIG | 27 | 8,050,025 | 运行配置、锁/fixture/入口、构建输入和报告生成依赖 |
| P03-DOC | 13 | 309,009 | 当前有效说明、唯一历史事实/验收索引、已被取代叙述与回退 |
| P03-ASSET | 5 | 37,095,650 | 文件实际类型、依赖/研究资料/临时产物，不能由后缀假定没用 |
| P03-CACHE | 1 | 4,682 | 已删除 completion 源的残留缓存；是否唯一重放材料/无源执行用途 |

该分组是原盘点分类而非已裁定事实：asset 中两份 requirements.lock 实际应按文本依赖锁复核；临时 .pyc.<pid> 应查写入/来源；PDF/依赖压缩包可能仍有用途。复核可修正 kind，但精确原始集合不变；新增文件不自动加入。

先复核 4 个孤立源码与 10 个旧计划/现状文档，再系统处理 structured/fuzzer/mutation/coverage/Office V1/V2/adapter/harness 的旧路线依赖，并绑定相关测试/配置；不能只完成小批就宣布 394 完成。

## 4. 实施 checklist

- [ ] 开始读取 LOG/索引与 Git 状态，记录 HEAD/时间/用户已有删除；READY → IN_PROGRESS。
- [ ] 冻结 394 精确对象，逐项复核尺寸/源码与文档哈希；变化/他人删除单列，依赖重新核验。
- [ ] 每个文件追踪入边及对象 API 用途，沿旧族依赖和当前主链/动态入口/CLI/包导出/镜像/测试/报告分别核验。宽 COPY/包扫描标“需接口/发布下线决定”，不自动认定当前调用，也不假定已无用。
- [ ] 源码/测试联动：标当前主链所需测试、旧 API 测试、跨测试共享 helper、有效 oracle/恢复回归；测试不得因旧目录名全部废弃。
- [ ] 27 配置查调用/运行记录/构建矩阵；需要历史重放的保留，新配置不取代原实验身份。
- [ ] 13 文档逐篇比对：DeepSeek README 仍有可选支持说明；10 历史文档先列唯一信息/索引提炼方案；tests/design 两篇查尚存 API/验收缺口。只提迁移方案，不在本任务改写旧文。
- [ ] 5 asset 先识别文件类型/入口。两锁文件按配置重新判定；依赖 archive 查看元数据/构建输入，不解压/删除；PDF 只按研究资料用途判断，不因体积大删除；临时 .pyc 后缀核实写入和源。正文读取限必要信息，不输出敏感响应。
- [ ] completion 无源缓存核对首批退役提交、包/重放引用和加载方式；若证明可再生/无唯一证据则提出单文件清理批，不混入 T02。
- [ ] 5 原有未决边给具体结果：mutation.config 缺失的旧族按接口/依赖退役方案处理；Docker ARG 记录已知调用与未知外部上下文，不恢复或修改。
- [ ] 298 禁删项保持保护，只登记旧模块若未来可能解除支持契约的条件；10 边界保留原保护规则。不把“全处理”理解为全删除。
- [ ] 为全部 394 条给处置、依据/行号、直接/传递/测试/历史依赖、替代物、风险、阻断接口、回退/需人决定的准确问题。不确定要列最小可验证动作，不能复用笼统“待核实”。
- [ ] 形成互不重叠的后续删除/解除依赖 TASK 建议，每批一个路线/文档族，有精确对象与预计字节、保护集、验证组，尚未批准不执行。
- [ ] 生成独立结果报告/JSON，不重写原盘点历史；全量核验输出覆盖、集合差、计量与候选末次摘要，LOG/索引记录结果，独立提交。

## 5. 输出、验收与验证

输出仅 docs/reports/20261010-flr-t03-pending-resolution.md 和 .json、本 TASK、LOG/索引。数据流仍为菜单→选择/生成→Agent→工具/状态/轨迹→Oracle→链/风险/增量→下一轮；本任务不修改流转实现。

- [ ] 结果每项 count=1，精确对象集合=原 394，原字节合计 49,161,766 B；每项有具体处置/依据/依赖/回退/后续门槛；没有漏项或重复。发生并行变化/消失时原统计与新统计分别记。
- [ ] 全部源码路线/测试/配置/文档/二进制/无源缓存均核实；可以保留待定，但必须有具体未决事实和最小解决步骤，不能靠改标签消除不确定。
- [ ] 可删候选须满足批准 SPEC §4.1；被支持 API/镜像/测试/唯一证据阻断者仍待定，依赖解除 TASK 不伪装为立即删除。
- [ ] 禁删和边界未被删除，全部候选/源正文未被改写；用户手动删除不恢复、不暂存；没有远程/实验状态变化。
- [ ] JSON/阅读版数量和路径一致，新增链接有效，候选读前后摘要一致或受影响项已重审；git diff --check 和 cached check 通过；LOG/索引增量保存，独立提交只含本批文件。

实际验证方法：
- git status --short、git rev-parse HEAD。
- rg --files --hidden --no-ignore 明确排除 .git/venv/依赖/junction/运行证据，和不跟随链接元数据核验互查。
- rg -n 在候选所在族、调用族、tests/scripts/build入口中查 module/API/路径/动态入口；退出 1 不是无依赖证明。
- scripts/project_python.cmd -B - 执行内存 AST/引用/哈希/JSON 一致性核验；已确认安全的当前最小导入可使用 -B，旧 CLI/未审查初始化不执行。
- 文档/静态复核不运行 pytest 全收集、compileall/Ruff、模型/Docker/远程；需要的运行证明写入后续精确删除 TASK，不声称测试已新通过。
- git diff --check、git diff --cached --check；人工核对后续批次对四条设计和证据保护的影响。

## 6. 风险、失败信号、回退与未决

动态接口/构建上下文/跨测试使用不完整、唯一历史资料不明、并行修改或候选读取失败使该项继续待定；报告明确未覆盖内容，不因为任务难而改产品语义。没有外部归档就保护原始证据。解除 CLI、adapter、共享包导出或打包接口需要人决定，复核只准备具体可审阅方案。

本任务只有新增报告与状态/日志，无候选回退操作；报告出错用后续修正提交，不改原实验、不 reset/rebase、不覆盖他人文件。未跟踪文档后续删除前须有获准回退保障，本任务不归档。

未决：用户尚未批准本 TASK；394 项真实去留是要调查的结果，不预先假定全部应删。后续 TASK 审批时同时决定具体支持接口的下线范围。

## 7. 起草阶段证据

本轮读取 LOG 顶部 20261010-full-legacy-retirement-inventory-done，核对批准 SPEC 与清单；394 项 size/已有 SHA256 与快照一致，变化 0。确认其中 37,095,650 B 属原 asset 分组，49.2 MB 不是全为旧源码。没有执行候选删除、代码/规格修改或新运行验证。

## 8. 全部 394 待定对象（每行 1 文件）

具体裁定在实施后结果报告里；此附录冻结审阅范围而不宣称已经复核完毕。

| 路径 | 原 kind | B |
|---|---|---:|
| `.gitattributes` | configuration | 180 |
| `agent_image/Dockerfile.exploratory-source` | configuration | 202 |
| `agent_image/Dockerfile.qwen` | configuration | 2,373 |
| `agent_image/Dockerfile.qwen-external` | configuration | 1,080 |
| `agent_image/Dockerfile.qwen-mutator` | configuration | 335 |
| `agent_image/app/agent_qwen_bootstrap.py` | source | 11,916 |
| `agent_image/app/office_mutator_worker.py` | source | 5,763 |
| `agent_image/app/office_v2_mutator_worker.py` | source | 6,727 |
| `agent_image/app/tools/policy.py` | source | 300 |
| `agent_image/requirements.agent-qwen.lock` | asset | 4,625 |
| `agent_image/requirements.langgraph.lock` | asset | 4,151 |
| `agent_variants/deepseek_harness/.gitignore` | configuration | 61 |
| `agent_variants/deepseek_harness/Dockerfile.qwen` | configuration | 1,855 |
| `agent_variants/deepseek_harness/README.md` | document | 1,064 |
| `agent_variants/deepseek_harness/fixtures/mcp_server.mjs` | source | 1,384 |
| `agent_variants/deepseek_harness/fixtures/openai_stub.mjs` | source | 4,379 |
| `agent_variants/deepseek_harness/office_v2.cordis.yml` | configuration | 2,416 |
| `agent_variants/deepseek_harness/package-lock.json` | configuration | 152,763 |
| `agent_variants/deepseek_harness/package.json` | configuration | 799 |
| `agent_variants/deepseek_harness/runtime/deterministic_model.mjs` | source | 19,241 |
| `agent_variants/deepseek_harness/runtime/driver.mjs` | source | 11,417 |
| `agent_variants/deepseek_harness/runtime/mcp_launcher.mjs` | source | 730 |
| `agent_variants/deepseek_harness/runtime/model_runtime.mjs` | source | 11,238 |
| `agent_variants/deepseek_harness/runtime/model_runtime_contract_test.mjs` | source | 4,252 |
| `agent_variants/deepseek_harness/runtime/office_bridge.py` | source | 26,741 |
| `chainfuzzer.pdf` | asset | 18,328,972 |
| `config/risk-taxonomy.yaml` | configuration | 5,296 |
| `docs/current-state/discussion-items.md` | document | 81,161 |
| `docs/current-state/episode-data-flow.md` | document | 35,251 |
| `docs/current-state/project-map.md` | document | 18,549 |
| `docs/current-state/soc-t01-bridge-capabilities.md` | document | 13,316 |
| `docs/current-state/soc-t01-reuse-boundary.md` | document | 10,426 |
| `docs/current-state/soc-t01-summary-delivery-a-inventory.md` | document | 13,018 |
| `docs/current-state/spec-compliance.md` | document | 71,642 |
| `docs/current-state/strategy-comparison.md` | document | 28,869 |
| `docs/plans/office-workspace-v2-coverage-guided-semantic-mutation-correction-plan.md` | document | 3,122 |
| `docs/tasks/20260917-current-code-audit.md` | document | 24,785 |
| `harness-node-modules.tar.gz` | asset | 18,755,436 |
| `scripts/build_office_v2_world.py` | source | 32,460 |
| `scripts/build_structured_v1_freeze.py` | source | 4,320 |
| `scripts/check_summary_delivery_e_history.py` | source | 2,992 |
| `scripts/compute_admission.py` | source | 15,455 |
| `scripts/diagnose_guidance_yield.py` | source | 13,301 |
| `scripts/dry_run_structured_arms.py` | source | 24,006 |
| `scripts/local_python.cmd` | configuration | 153 |
| `scripts/range_calibration_probe.py` | source | 33,730 |
| `scripts/report_structured_campaigns.py` | source | 2,276 |
| `scripts/report_summary_delivery_e.py` | source | 9,163 |
| `scripts/report_summary_delivery_e2.py` | source | 40,350 |
| `scripts/run_paired_campaign.sh` | configuration | 4,511 |
| `scripts/run_structured_v1_episode.py` | source | 23,538 |
| `scripts/run_summary_delivery_e.sh` | configuration | 2,777 |
| `scripts/smoke_attack_prompt.py` | source | 4,352 |
| `src/sandbox/cli.py` | source | 10,425 |
| `src/sandbox/client/artifact_transfer.py` | source | 7,801 |
| `src/sandbox/coverage/risk.py` | source | 12,796 |
| `src/sandbox/coverage/store.py` | source | 34,838 |
| `src/sandbox/fuzzer/circuit_breaker.py` | source | 1,022 |
| `src/sandbox/fuzzer/config.py` | source | 6,434 |
| `src/sandbox/fuzzer/corpus.py` | source | 5,510 |
| `src/sandbox/fuzzer/energy.py` | source | 3,593 |
| `src/sandbox/fuzzer/engine.py` | source | 38,394 |
| `src/sandbox/fuzzer/exceptions.py` | source | 438 |
| `src/sandbox/fuzzer/executor.py` | source | 7,115 |
| `src/sandbox/fuzzer/failure_policy.py` | source | 6,301 |
| `src/sandbox/fuzzer/metrics.py` | source | 3,777 |
| `src/sandbox/fuzzer/queue.py` | source | 1,828 |
| `src/sandbox/fuzzer/recovery.py` | source | 5,196 |
| `src/sandbox/fuzzer/resources.py` | source | 1,592 |
| `src/sandbox/fuzzer/seed_pool.py` | source | 7,220 |
| `src/sandbox/fuzzer/semantic_alignment.py` | source | 2,041 |
| `src/sandbox/fuzzer/soak.py` | source | 10,177 |
| `src/sandbox/fuzzer/store.py` | source | 43,784 |
| `src/sandbox/fuzzer/v2_agent_behavior.py` | source | 13,930 |
| `src/sandbox/fuzzer/v2_bootstrap.py` | source | 6,548 |
| `src/sandbox/fuzzer/v2_campaign.py` | source | 5,725 |
| `src/sandbox/fuzzer/v2_campaign_loop.py` | source | 18,327 |
| `src/sandbox/fuzzer/v2_campaign_state.py` | source | 19,740 |
| `src/sandbox/fuzzer/v2_campaign_store.py` | source | 88,942 |
| `src/sandbox/fuzzer/v2_cli.py` | source | 16,697 |
| `src/sandbox/fuzzer/v2_comparison_report.py` | source | 34,085 |
| `src/sandbox/fuzzer/v2_corpus.py` | source | 30,289 |
| `src/sandbox/fuzzer/v2_effectiveness.py` | source | 12,500 |
| `src/sandbox/fuzzer/v2_effectiveness_protocol.py` | source | 14,759 |
| `src/sandbox/fuzzer/v2_effectiveness_report.py` | source | 16,885 |
| `src/sandbox/fuzzer/v2_feedback.py` | source | 14,953 |
| `src/sandbox/fuzzer/v2_loop_contracts.py` | source | 18,675 |
| `src/sandbox/fuzzer/v2_orchestrator.py` | source | 6,840 |
| `src/sandbox/fuzzer/v2_preflight.py` | source | 2,797 |
| `src/sandbox/fuzzer/v2_promotion.py` | source | 9,050 |
| `src/sandbox/fuzzer/v2_real_episode.py` | source | 14,268 |
| `src/sandbox/fuzzer/v2_real_runtime.py` | source | 59,867 |
| `src/sandbox/fuzzer/v2_report.py` | source | 13,744 |
| `src/sandbox/fuzzer/v2_risk_pool_scheduler.py` | source | 11,982 |
| `src/sandbox/fuzzer/v2_runtime.py` | source | 17,185 |
| `src/sandbox/fuzzer/v2_scheduler.py` | source | 9,631 |
| `src/sandbox/fuzzer/v2_scoring.py` | source | 20,705 |
| `src/sandbox/fuzzer/v2_seed_pools.py` | source | 18,723 |
| `src/sandbox/fuzzer/v2_selection.py` | source | 8,710 |
| `src/sandbox/fuzzer/v2_strategy.py` | source | 282 |
| `src/sandbox/fuzzer/v2_target_judge.py` | source | 11,430 |
| `src/sandbox/fuzzer/v2_target_oracle.py` | source | 46,875 |
| `src/sandbox/fuzzer/v2_target_preservation.py` | source | 5,141 |
| `src/sandbox/fuzzer/v2_work.py` | source | 9,315 |
| `src/sandbox/mutation/__init__.py` | source | 318 |
| `src/sandbox/mutation/alignment.py` | source | 2,156 |
| `src/sandbox/mutation/diversity.py` | source | 3,512 |
| `src/sandbox/mutation/exceptions.py` | source | 1,524 |
| `src/sandbox/mutation/feedback.py` | source | 4,876 |
| `src/sandbox/mutation/handoff.py` | source | 1,927 |
| `src/sandbox/mutation/models.py` | source | 12,995 |
| `src/sandbox/mutation/mutator.py` | source | 26,503 |
| `src/sandbox/mutation/normalizer.py` | source | 1,856 |
| `src/sandbox/mutation/operators.py` | source | 3,805 |
| `src/sandbox/mutation/planner.py` | source | 4,576 |
| `src/sandbox/mutation/priority.py` | source | 3,534 |
| `src/sandbox/mutation/providers/__init__.py` | source | 154 |
| `src/sandbox/mutation/providers/base.py` | source | 565 |
| `src/sandbox/mutation/providers/rule_based.py` | source | 5,070 |
| `src/sandbox/mutation/similarity.py` | source | 3,776 |
| `src/sandbox/mutation/store.py` | source | 16,295 |
| `src/sandbox/mutation/v2_brief.py` | source | 5,863 |
| `src/sandbox/mutation/v2_candidate.py` | source | 5,444 |
| `src/sandbox/mutation/v2_contracts.py` | source | 12,738 |
| `src/sandbox/mutation/v2_docker.py` | source | 11,408 |
| `src/sandbox/mutation/v2_materializer.py` | source | 5,228 |
| `src/sandbox/mutation/v2_plan_builder.py` | source | 5,914 |
| `src/sandbox/mutation/v2_policy.py` | source | 23,625 |
| `src/sandbox/mutation/v2_preparation.py` | source | 9,592 |
| `src/sandbox/mutation/v2_provider.py` | source | 8,154 |
| `src/sandbox/mutation/v2_validation.py` | source | 5,713 |
| `src/sandbox/ollama_schema.py` | source | 857 |
| `src/sandbox/replay/audit_store.py` | source | 1,079 |
| `src/sandbox/replay/comparator.py` | source | 2,733 |
| `src/sandbox/replay/decision_input.py` | source | 8,144 |
| `src/sandbox/replay/normalizer.py` | source | 1,785 |
| `src/sandbox/replay/replay_engine.py` | source | 38,656 |
| `src/sandbox/runtime_resources.py` | source | 2,101 |
| `src/sandbox/scenarios/error_capable_legacy_fixture.json` | configuration | 1,913 |
| `src/sandbox/scenarios/office_v2/cli_entry.py` | source | 3,863 |
| `src/sandbox/scenarios/office_v2/data/office-world-v2.0/calendar.json` | configuration | 36,157 |
| `src/sandbox/scenarios/office_v2/data/office-world-v2.0/drive.json` | configuration | 59,896 |
| `src/sandbox/scenarios/office_v2/data/office-world-v2.0/mail.json` | configuration | 165,392 |
| `src/sandbox/scenarios/office_v2/data/office-world-v2.0/manifest.json` | configuration | 1,606 |
| `src/sandbox/scenarios/office_v2/data/office-world-v2.0/organization.json` | configuration | 215,816 |
| `src/sandbox/scenarios/office_v2/data/office-world-v2.0/policy.json` | configuration | 3,412 |
| `src/sandbox/scenarios/office_v2/data/office-world-v2.0/quality-report.json` | configuration | 983 |
| `src/sandbox/scenarios/office_v2/data/office-world-v2.0/workspace.json` | configuration | 13,245 |
| `src/sandbox/scenarios/office_v2/execution_request.py` | source | 3,427 |
| `src/sandbox/scenarios/structured_v1/__init__.py` | source | 263 |
| `src/sandbox/scenarios/structured_v1/fixtures/__init__.py` | source | 4,383 |
| `src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_a.py` | source | 16,835 |
| `src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_b.py` | source | 18,715 |
| `src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_c.py` | source | 4,580 |
| `src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_d.py` | source | 7,303 |
| `src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_e.py` | source | 4,389 |
| `src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_e2.py` | source | 8,290 |
| `src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_e2_completion.py` | source | 3,335 |
| `src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_e2_completion_v2.py` | source | 3,658 |
| `src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_e2_completion_v3.py` | source | 3,859 |
| `src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_enc.py` | source | 11,074 |
| `src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_enc2.py` | source | 3,434 |
| `src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_range_01.py` | source | 15,483 |
| `src/sandbox/scheduler/exceptions.py` | source | 330 |
| `src/sandbox/structured_v1/__pycache__/completion.cpython-312.pyc` | cache | 4,682 |
| `src/sandbox/structured_v1/acceptance.py` | source | 9,870 |
| `src/sandbox/structured_v1/behavior.py` | source | 14,710 |
| `src/sandbox/structured_v1/calibration.py` | source | 8,751 |
| `src/sandbox/structured_v1/campaign.py` | source | 30,108 |
| `src/sandbox/structured_v1/campaign_report.py` | source | 12,800 |
| `src/sandbox/structured_v1/candidates.py` | source | 8,910 |
| `src/sandbox/structured_v1/coverage.py` | source | 24,253 |
| `src/sandbox/structured_v1/edit_kernel.py` | source | 30,005 |
| `src/sandbox/structured_v1/experiment_inputs.py` | source | 4,562 |
| `src/sandbox/structured_v1/feedback.py` | source | 23,821 |
| `src/sandbox/structured_v1/finalization.py` | source | 3,451 |
| `src/sandbox/structured_v1/generation.py` | source | 12,117 |
| `src/sandbox/structured_v1/host_runner.py` | source | 18,279 |
| `src/sandbox/structured_v1/mbr.py` | source | 14,389 |
| `src/sandbox/structured_v1/operators.py` | source | 35,739 |
| `src/sandbox/structured_v1/phase_report.py` | source | 3,284 |
| `src/sandbox/structured_v1/projection.py` | source | 9,123 |
| `src/sandbox/structured_v1/provider.py` | source | 19,313 |
| `src/sandbox/structured_v1/public_delivery.py` | source | 7,632 |
| `src/sandbox/structured_v1/routes.py` | source | 6,663 |
| `src/sandbox/structured_v1/sampling.py` | source | 12,933 |
| `src/sandbox/structured_v1/search.py` | source | 79,725 |
| `src/sandbox/structured_v1/streams.py` | source | 1,643 |
| `src/sandbox/structured_v1/submission.py` | source | 5,976 |
| `src/sandbox/structured_v1/text_provider.py` | source | 23,474 |
| `src/sandbox/structured_v1/validation.py` | source | 24,672 |
| `tests/__pycache__/conftest.cpython-312-pytest-8.4.2.pyc.31892` | asset | 2,466 |
| `tests/design/office-v2-agent-behavior-assessment-acceptance.md` | document | 4,362 |
| `tests/design/office-v2-operator-target-preservation-acceptance.md` | document | 3,444 |
| `tests/integration/test_execution_engine.py` | test | 6,972 |
| `tests/integration/test_office_v2_agent_api_composition.py` | test | 9,860 |
| `tests/integration/test_office_v2_causal_chains.py` | test | 48,538 |
| `tests/integration/test_office_v2_stage5_representatives.py` | test | 23,973 |
| `tests/integration/test_office_v2_upstream_perturbations.py` | test | 12,451 |
| `tests/integration/test_runtime_api.py` | test | 3,921 |
| `tests/integration/test_structured_anchored_feedback.py` | test | 8,402 |
| `tests/integration/test_structured_campaign_report.py` | test | 4,148 |
| `tests/integration/test_structured_completion_diagnostic.py` | test | 11,056 |
| `tests/integration/test_structured_delivery_recovery.py` | test | 13,726 |
| `tests/integration/test_structured_e2_report.py` | test | 19,249 |
| `tests/integration/test_structured_fdm_disk_entry.py` | test | 12,614 |
| `tests/integration/test_structured_feedback_mutation.py` | test | 16,651 |
| `tests/integration/test_structured_natural_feedback_chain.py` | test | 9,766 |
| `tests/integration/test_structured_refused_child_settlement.py` | test | 5,085 |
| `tests/integration/test_structured_two_arm_campaign.py` | test | 2,382 |
| `tests/unit/structured_coverage_helpers.py` | test | 1,130 |
| `tests/unit/test_attempted_effect_evidence.py` | test | 1,692 |
| `tests/unit/test_behavior_features.py` | test | 1,929 |
| `tests/unit/test_cli_fork_contract.py` | test | 725 |
| `tests/unit/test_controlled_tools.py` | test | 1,146 |
| `tests/unit/test_coverage_input.py` | test | 5,149 |
| `tests/unit/test_diagnose_guidance_yield.py` | test | 6,973 |
| `tests/unit/test_docker_scheduler_config.py` | test | 4,481 |
| `tests/unit/test_exploratory_campaign.py` | test | 5,329 |
| `tests/unit/test_exploratory_recovery.py` | test | 14,999 |
| `tests/unit/test_feature_normalizer.py` | test | 565 |
| `tests/unit/test_guided_seed_priority.py` | test | 16,483 |
| `tests/unit/test_langgraph_react_runtime.py` | test | 38,713 |
| `tests/unit/test_models_and_cases.py` | test | 1,328 |
| `tests/unit/test_office_benign_tasks.py` | test | 5,655 |
| `tests/unit/test_office_candidate_generation.py` | test | 9,513 |
| `tests/unit/test_office_carrier_fork.py` | test | 4,851 |
| `tests/unit/test_office_composition_rules.py` | test | 5,563 |
| `tests/unit/test_office_container_tools.py` | test | 7,265 |
| `tests/unit/test_office_control_provider.py` | test | 4,674 |
| `tests/unit/test_office_coverage_input.py` | test | 8,823 |
| `tests/unit/test_office_episode_initialization.py` | test | 6,109 |
| `tests/unit/test_office_expression_mutation.py` | test | 12,916 |
| `tests/unit/test_office_injection_carriers.py` | test | 7,505 |
| `tests/unit/test_office_mutation_batch.py` | test | 18,465 |
| `tests/unit/test_office_retarget_mutation.py` | test | 11,803 |
| `tests/unit/test_office_runtime.py` | test | 9,962 |
| `tests/unit/test_office_safe_control.py` | test | 4,783 |
| `tests/unit/test_office_test_matrix.py` | test | 4,128 |
| `tests/unit/test_office_trace_react_adapter.py` | test | 5,652 |
| `tests/unit/test_office_v2_agent_api.py` | test | 9,706 |
| `tests/unit/test_office_v2_agent_behavior_assessment.py` | test | 4,943 |
| `tests/unit/test_office_v2_agent_context.py` | test | 7,115 |
| `tests/unit/test_office_v2_agent_identity_context.py` | test | 4,503 |
| `tests/unit/test_office_v2_agent_policy_context.py` | test | 4,935 |
| `tests/unit/test_office_v2_agent_prompt.py` | test | 5,367 |
| `tests/unit/test_office_v2_agent_session_surface.py` | test | 11,001 |
| `tests/unit/test_office_v2_attack_contracts.py` | test | 4,923 |
| `tests/unit/test_office_v2_attack_materialization.py` | test | 11,374 |
| `tests/unit/test_office_v2_attack_objectives.py` | test | 1,540 |
| `tests/unit/test_office_v2_attack_surface.py` | test | 2,675 |
| `tests/unit/test_office_v2_behavior_contracts.py` | test | 6,757 |
| `tests/unit/test_office_v2_blocked_attempt_sample.py` | test | 15,193 |
| `tests/unit/test_office_v2_campaign_report.py` | test | 5,430 |
| `tests/unit/test_office_v2_canonical_world.py` | test | 6,927 |
| `tests/unit/test_office_v2_clarification_coordinator.py` | test | 7,956 |
| `tests/unit/test_office_v2_clean_oracle.py` | test | 1,386 |
| `tests/unit/test_office_v2_comparison_basis.py` | test | 17,332 |
| `tests/unit/test_office_v2_comparison_report.py` | test | 22,837 |
| `tests/unit/test_office_v2_comparison_sample.py` | test | 20,768 |
| `tests/unit/test_office_v2_coverage_contracts.py` | test | 6,561 |
| `tests/unit/test_office_v2_coverage_input.py` | test | 18,337 |
| `tests/unit/test_office_v2_domain_tools.py` | test | 16,891 |
| `tests/unit/test_office_v2_episode_coverage.py` | test | 5,381 |
| `tests/unit/test_office_v2_episode_token_usage.py` | test | 4,003 |
| `tests/unit/test_office_v2_exploration_fairness.py` | test | 6,655 |
| `tests/unit/test_office_v2_exposure_evaluator.py` | test | 12,806 |
| `tests/unit/test_office_v2_fuzzer_corpus.py` | test | 12,509 |
| `tests/unit/test_office_v2_fuzzer_scheduler.py` | test | 1,467 |
| `tests/unit/test_office_v2_fuzzer_work.py` | test | 3,802 |
| `tests/unit/test_office_v2_interaction.py` | test | 10,855 |
| `tests/unit/test_office_v2_interaction_session.py` | test | 12,322 |
| `tests/unit/test_office_v2_models.py` | test | 43,843 |
| `tests/unit/test_office_v2_mutator_worker.py` | test | 325 |
| `tests/unit/test_office_v2_objective_assertion_matcher.py` | test | 15,660 |
| `tests/unit/test_office_v2_observation.py` | test | 9,988 |
| `tests/unit/test_office_v2_oracle_evidence.py` | test | 14,331 |
| `tests/unit/test_office_v2_oracle_models.py` | test | 13,719 |
| `tests/unit/test_office_v2_oracle_rebuild.py` | test | 7,288 |
| `tests/unit/test_office_v2_oracle_trace.py` | test | 11,774 |
| `tests/unit/test_office_v2_policy.py` | test | 13,836 |
| `tests/unit/test_office_v2_policy_violations.py` | test | 7,133 |
| `tests/unit/test_office_v2_promotion_resolution.py` | test | 7,183 |
| `tests/unit/test_office_v2_public_cli_entry.py` | test | 2,076 |
| `tests/unit/test_office_v2_resolution.py` | test | 12,688 |
| `tests/unit/test_office_v2_risk_catalog.py` | test | 1,779 |
| `tests/unit/test_office_v2_risk_coverage.py` | test | 2,303 |
| `tests/unit/test_office_v2_runtime_surface_architecture.py` | test | 1,847 |
| `tests/unit/test_office_v2_scenario_oracle.py` | test | 4,934 |
| `tests/unit/test_office_v2_scoring.py` | test | 12,787 |
| `tests/unit/test_office_v2_semantic_mutation.py` | test | 6,043 |
| `tests/unit/test_office_v2_stage3_boundary.py` | test | 5,082 |
| `tests/unit/test_office_v2_stage4_boundary.py` | test | 6,905 |
| `tests/unit/test_office_v2_stage5_boundary.py` | test | 2,409 |
| `tests/unit/test_office_v2_target_oracle.py` | test | 7,091 |
| `tests/unit/test_office_v2_task_catalog.py` | test | 5,617 |
| `tests/unit/test_office_v2_task_execution_support.py` | test | 2,517 |
| `tests/unit/test_office_v2_tool_contracts.py` | test | 6,169 |
| `tests/unit/test_office_v2_tool_provenance.py` | test | 6,977 |
| `tests/unit/test_office_v2_tool_runtime.py` | test | 11,913 |
| `tests/unit/test_office_v2_tool_specs.py` | test | 3,559 |
| `tests/unit/test_office_v2_unexpected_risk.py` | test | 3,173 |
| `tests/unit/test_office_v2_utility_assertion_catalog.py` | test | 6,558 |
| `tests/unit/test_office_v2_utility_evaluator.py` | test | 17,149 |
| `tests/unit/test_office_v2_visibility_chain.py` | test | 13,673 |
| `tests/unit/test_office_v2_workspace_fs.py` | test | 6,806 |
| `tests/unit/test_office_v2_world.py` | test | 8,778 |
| `tests/unit/test_office_vulnerable_control.py` | test | 5,716 |
| `tests/unit/test_ollama_network_policy.py` | test | 3,615 |
| `tests/unit/test_ollama_react_provider.py` | test | 9,670 |
| `tests/unit/test_recording_components.py` | test | 6,395 |
| `tests/unit/test_replay_core.py` | test | 7,691 |
| `tests/unit/test_replay_decision_input.py` | test | 8,118 |
| `tests/unit/test_rule_scorer.py` | test | 1,255 |
| `tests/unit/test_runtime_client_transport.py` | test | 4,557 |
| `tests/unit/test_runtime_error_contract.py` | test | 1,225 |
| `tests/unit/test_runtime_resources.py` | test | 809 |
| `tests/unit/test_scenario_contracts.py` | test | 9,852 |
| `tests/unit/test_shared_protocol.py` | test | 425 |
| `tests/unit/test_structured_behavior_semantics.py` | test | 11,333 |
| `tests/unit/test_structured_calibration.py` | test | 3,413 |
| `tests/unit/test_structured_case_admission.py` | test | 13,316 |
| `tests/unit/test_structured_case_identity.py` | test | 7,553 |
| `tests/unit/test_structured_case_operators.py` | test | 19,274 |
| `tests/unit/test_structured_closure.py` | test | 11,789 |
| `tests/unit/test_structured_completion.py` | test | 3,797 |
| `tests/unit/test_structured_coverage.py` | test | 5,377 |
| `tests/unit/test_structured_coverage_contract_findings.py` | test | 12,636 |
| `tests/unit/test_structured_delivery_content.py` | test | 6,316 |
| `tests/unit/test_structured_directed_layer.py` | test | 9,854 |
| `tests/unit/test_structured_edit_kernel.py` | test | 10,790 |
| `tests/unit/test_structured_effects.py` | test | 16,409 |
| `tests/unit/test_structured_envelope.py` | test | 24,831 |
| `tests/unit/test_structured_episode_rehearsal.py` | test | 25,211 |
| `tests/unit/test_structured_execution_evidence.py` | test | 9,537 |
| `tests/unit/test_structured_experiment_inputs.py` | test | 1,105 |
| `tests/unit/test_structured_exposure.py` | test | 12,557 |
| `tests/unit/test_structured_failure_diagnostics.py` | test | 8,011 |
| `tests/unit/test_structured_feedback_chain.py` | test | 13,610 |
| `tests/unit/test_structured_feedback_contract.py` | test | 9,431 |
| `tests/unit/test_structured_finalization.py` | test | 8,796 |
| `tests/unit/test_structured_first_turn_context.py` | test | 8,227 |
| `tests/unit/test_structured_fixture_anchored.py` | test | 15,016 |
| `tests/unit/test_structured_fixture_assets.py` | test | 16,849 |
| `tests/unit/test_structured_fixture_branching.py` | test | 15,556 |
| `tests/unit/test_structured_fixture_e2.py` | test | 14,651 |
| `tests/unit/test_structured_fixture_e2_completion.py` | test | 4,917 |
| `tests/unit/test_structured_fixture_enc.py` | test | 8,035 |
| `tests/unit/test_structured_fixture_target_preserving.py` | test | 12,737 |
| `tests/unit/test_structured_freeze.py` | test | 7,221 |
| `tests/unit/test_structured_g1_freezing.py` | test | 5,632 |
| `tests/unit/test_structured_generation_flow.py` | test | 5,638 |
| `tests/unit/test_structured_host_runner_probes.py` | test | 9,702 |
| `tests/unit/test_structured_material_discovery.py` | test | 5,576 |
| `tests/unit/test_structured_materialization.py` | test | 17,401 |
| `tests/unit/test_structured_mbr.py` | test | 6,765 |
| `tests/unit/test_structured_no_model_acceptance.py` | test | 12,318 |
| `tests/unit/test_structured_normal_control_acceptance.py` | test | 10,427 |
| `tests/unit/test_structured_normal_control_budget.py` | test | 2,145 |
| `tests/unit/test_structured_obligations.py` | test | 11,059 |
| `tests/unit/test_structured_projection.py` | test | 4,806 |
| `tests/unit/test_structured_provider.py` | test | 12,298 |
| `tests/unit/test_structured_random_evolution.py` | test | 14,736 |
| `tests/unit/test_structured_range_calibration.py` | test | 13,213 |
| `tests/unit/test_structured_registered_matching.py` | test | 4,946 |
| `tests/unit/test_structured_root_sampling.py` | test | 6,611 |
| `tests/unit/test_structured_routes.py` | test | 4,116 |
| `tests/unit/test_structured_t03_feedback_position.py` | test | 8,903 |
| `tests/unit/test_structured_t04_settlement.py` | test | 12,095 |
| `tests/unit/test_structured_t05_recovery.py` | test | 4,635 |
| `tests/unit/test_structured_technique_rotation.py` | test | 1,691 |
| `tests/unit/test_structured_text_provider.py` | test | 14,202 |
| `tests/unit/test_structured_tool_runtime_integration.py` | test | 3,680 |
| `tests/unit/test_structured_two_arm_generation.py` | test | 15,658 |
| `tests/unit/test_structured_two_arm_search.py` | test | 17,878 |
| `tests/unit/test_structured_two_phase.py` | test | 24,787 |
| `tests/unit/test_target_judge.py` | test | 6,730 |
| `tests/unit/test_trace_react_adapter.py` | test | 7,529 |
| `tests/unit/test_trace_react_contract.py` | test | 1,287 |
| `tests/unit/test_trace_workspace_controls.py` | test | 2,382 |
| `tests/unit/test_trace_workspace_scenario.py` | test | 3,526 |
| `tests/unit/test_trajectory_store.py` | test | 2,006 |
| `tests/unit/test_v2_agent_knowledge.py` | test | 7,371 |
| `tests/unit/test_v2_effectiveness.py` | test | 12,512 |
| `tests/unit/test_v2_effectiveness_authorized_pair.py` | test | 10,059 |
| `tests/unit/test_v2_effectiveness_protocol.py` | test | 27,219 |
| `tests/unit/test_v2_effectiveness_report.py` | test | 15,359 |
| `tests/unit/test_v2_risk_pool_scheduler.py` | test | 4,093 |
| `tests/unit/test_v2_seed_pools.py` | test | 7,277 |
| `tests/unit/test_zero_information_episodes.py` | test | 10,563 |
| `tmp-campaign-multipath.json` | configuration | 2,386,834 |
| `tmp-campaign.json` | configuration | 2,296,379 |
| `tmp-probe-multipath.json` | configuration | 2,683,344 |
| `tmp-probe.json` | configuration | 10,247 |

## 起草验证记录

- .venv/Scripts/python.exe -B - 内存校验：两任务均 DRAFT，引用清单摘要一致；T02 聚合计量恰为 766 / 14,714,839 B，T03 附录精确集合为 394 / 49,161,766 B，互不重叠。全部新增链接存在、无行尾空白。
- 起草末次复核 1,160 个缓存/待定候选 size 与已有 SHA256，变化 0；文档 diff-check 通过。未执行删除、测试收集、Ruff、compileall 或外部运行。只提交两份任务草案，LOG/索引保持本地增量记录。
