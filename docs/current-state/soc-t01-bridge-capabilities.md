# SOC-T01 审计 B：执行桥能力判定（暴露 / 效果关闭 / 用量记账）

- 任务：`TASK-SOC-20260921` 的 **SOC-T01**（状态 IN_PROGRESS）；交付物 2/3。
- 审计基线：HEAD `7822c44`。方法：只读代码审计；下表"我的核验"列标明哪些断言经我独立复核。
- 用途：这是 T01 的**退出条件**项（TASK §4 验收第 3 条）。结论直接决定 T03/T06 必须走哪条降级路径。

## 0. 结论速览

| 能力 | 判定 | 对 T03/T06 的含义 |
|---|---|---|
| ① 材料暴露（区分"已物化"与"实际读到"） | **YES（有缺口）** | 可用于 R 的 `read_restricted`/`prepared` 判定；缺口须显式标注为"未覆盖" |
| ② 提交时的效果与受众可读性 | **部分：有权威对象，但提交时的可读性不可证（撤权后不可重建）** | `committed` 判定可用；**`VIOLATED` 判定当前不能普遍成立**，须先补提交时效果证据（T03，见 B8） |
| ③ 后台请求关闭（不会再提交） | **部分：工具同步 + 进程回收 YES；正面凭证 NO** | 收尾窗口到期仍不明必须走 UNKNOWN + 隔离（SAF `SOC-SAF-16`） |
| ④ 用量记账（M/A/J 逐调用） | **Mutator/Agent 主路径 YES；Judge NO；失败调用多半记 0** | 总预算 T 硬边界**当前不可达成**，须先补账或明确标注不完整 |

## 1. ① 材料暴露证据

**能分清三层事实**：材料已写入世界 → 工具把内容作为输出产出 → 该输出回到 Agent 循环并被记录。

- 物化侧：`MaterializationEvidenceRef{materialization_id, transition_digest}`（`oracle_evidence.py:956`），
  与冻结物化记录强校验（`oracle_evidence.py:877-908`）；字段级 `MaterializedFieldChange{original_value_digest, visible_value_digest}`（`attack_models.py:924-949`）。
- 逐次读取证据：`OutputEvidence{evidence_id, invocation_id, field_path, resource_ref, value_digest, visibility, origin}`（`tools/contracts.py:76-88`），
  成功调用按可见输出逐字段生成（`tools/runtime.py:369-380`），进入 ledger（`tools/provenance.py:46-77`）与 bundle（`oracle_evidence.py:207/747`）。
- **已有的判定入口**：`evaluate_exposure`（`security_oracle.py:265`）产出 `PLANNED / DELIVERED / OBSERVED / USED`
  （`ExposureStage`，`:292/:301/:323`）；`OBSERVED` 要求工具 exchange 的 `field_path+value_digest` 命中物化可见值且工具在
  `observable_through_tools` 白名单（`:207-247`）；`USED` 要求后续参数的来源引用该 output evidence（`:333-343`）。
  **我的核验**：`evaluate_exposure` 与三个阶段枚举确实存在（`security_oracle.py:265/292/301/323`）。
  → 这段能力**可直接作为 SAF 的"读到 / 进入草稿"事件证据**，不需要新写语义。

**缺口（必须写进 T03 的"未覆盖/unknown"口径）**

1. 没有"读取登记"：读类工具不改状态（`tools/contracts.py:178-180` 要求成功读前后状态相同）；
   `MailDelivery.read_at` 只由建世界脚本写入（`scripts/build_office_v2_world.py:457/463`），**任何工具都不写**（我的核验：全仓仅这两处）。
2. 只有 `SUCCEEDED` 调用才产生 `output_evidence`（`tools/runtime.py:369` 在成功分支）→ 被阻断/失败调用里的部分读取没有证据。
3. `prior_tool_result_digest`（`langgraph_react_runtime.py:986`）是记录而非校验事实，bundle 构建不校验它。
4. 覆盖范围止于应用层请求，**不主张模型注意力**（`docs/specs/20260919-agent-visibility-evidence.md:14/45`）。

## 2. ② 提交时的效果与受众可读性

**世界侧有权威对象**：邮件与共享/改权限都在同一已提交事务内产生对象与状态转移。

- 邮件：`MailMessage` + 每个 to/cc 一条 `MailDelivery`（`tools/mail.py:203-231`）；世界不变量强制 delivery 精确覆盖 sender+to+cc（`models.py:721-740`）。
- 共享：`ShareRecord` + `AclEntry`（`tools/drive.py:331-353`）、改权限（`drive.py:374-425`）。
- 受众绑定在对象标识里：ACL object_id = hash(resource, grantee_id)（`world.py:248-253`）、delivery object_id = hash(message_id, mailbox_owner_id)（`world.py:235-240`）；
  决策侧的受众集合 `PolicyDecision.recipient_ids`（`policy.py:287-318`，构建 `tools/runtime.py:562-576`）。
- 最终世界随录制落盘：`OfficeV2SessionSnapshot.state`（`office_v2_session.py:83-126`）→ `office-v2-recording-state.json`（`replay/replay_engine.py:695-740`）。

**结论（2026-09-21 收紧）**：世界侧"有权威对象"**不等于**能证明**提交时**的可读性。
`SOC-SAF-19` 要求证据链含"提交时受众可读性"，`SOC-SAF-15` 明确"删除、撤回、恢复 ACL 或 Episode 清理不抹掉该次暴露"；
而"最终状态 + committed transition 联立"**不能覆盖"先共享、后撤权"**——撤权后最终状态不再显示可读，当时却确实可读。

**缺口**

1. **提交时的权限值不在任何持久化证据里**（本次独立核实）：
   `StateFieldChange` 只有 `before_value_digest`/`after_value_digest`（`world.py:68-76`）；
   `StateDelta.created_objects` 只有 `StateObjectRef{kind, object_id}`（`world.py:96-100`），
   且 object_id 是 `hash(resource, grantee)` 的**不可逆摘要**（`world.py:248-253`）；
   `PolicyDecision.matched_acl_entries` 只有条目 id（`policy.py:311`）。
   → 撤权后既不能从 delta 读出当时的 rights，也不能从 id 反推受众，**无法重建"提交时可读"**。
2. `OBJECT_CREATED` 在"创建对象不含被绑定资源"时退化为"delta 非空即真"（`security_oracle.py:640-679`），
   不校验 ACL rights 或 ACL→recipient 绑定 → **新 Oracle 不能沿用这条断言**。
3. 没有"提交瞬间的受众快照"对象。
4. 没有"受众确实读到"的证据（`read_at` 无写入方）—— 这与 `SOC-SAF-08` 一致（只要求可读状态），**不算缺口**。

**必须新增的接口工作（T03，二选一，不能都不做）**

- **路径 A（推荐）**：提交时由运行时在事务内生成**效果证据**——记录内容摘要 + 当时的可读性事实
  （受众关系与权限值），作为 bundle 的一等事实；此后撤权不影响已生成的证据。
- **路径 B**：保留**可按记录顺序确定性重建当时状态**的能力（需每个转移的完整载荷或可重放的操作日志）。
  当前记录只留最终 `state` 与转移摘要（`office_v2_session.py:83-126`），**路径 B 同样未满足**。

## 3. ③ 后台请求关闭

**部分具备**：业务工具同步、进程有回收路径；但没有"已静默、不会再提交"的正面凭证。

- 工具本身同步且纯内存：`tools/*.py` 无 async/subprocess/sleep；邮件即时投递（`tools/mail.py:202-231`）。
- 容器 Runtime：`asyncio.timeout`（`agent_image/app/runtime.py:183`）、`cancel()` + `await gather`（`:160-173`）、终态后拒绝新事件（`:350-369/401-413`）。
- Harness：超时/取消 → 写 `cancel.requested` → 等 5s → `taskkill /T /F` 或 SIGTERM→SIGKILL → `await process.wait()`（`deepseek_harness_adapter.py:498-510/552-580`）；
  顺序硬校验：记录末尾必须是 submit、不得有更早 submit（`:812-851`）；Oracle 要求恰好一条 accepted `agent_submit`（`oracle_trace.py:371-396`）。
- 容器销毁后二次校验（replay 路径）：`replay_engine.py:393-400`、`docker_scheduler.py:279-283`。

**缺口（当前"效果关闭"不能直接照抄旧实现）**

1. `cleanup_confirmed=True`（`fuzzer/v2_real_runtime.py:811`）与 `container_removed=True`（`fuzzer/v2_real_episode.py:243`）
   是**字面常量**（我的核验：两处均为常量赋值）→ 旧结算里的"已清理"不构成证据。
2. 桥层无 post-close 拒绝（`office_bridge.py:129-248` 不检查是否已 submit）；
   Oracle 不校验"submit 之后不得再有工具 exchange"（`oracle_evidence.py:588-589` 只要求 `termination.sequence ≥ len(tool_exchanges)`）。
3. 被硬杀/超时的 harness run：bridge `finalize` 不执行，只剩 `_capture_incomplete_summary`（`deepseek_harness_adapter.py:582-589`）。
4. 中断态中间事实不落盘：`episode_dir` 是 `TemporaryDirectory`（`deepseek_harness_adapter.py:300-304`）；
   失败录制走 `finalize_incomplete`（`app/replay/checkpoint.py:137-164`），不写 `office-v2-recording-state.json`。
5. 被取消的 mutator 调用：`asyncio.to_thread` 包裹，取消后线程继续但 attempt 对象丢失（`mutation/v2_preparation.py:119-126`），
   campaign 仅以 `unclassified-provider-failure` 暂停（`fuzzer/v2_real_runtime.py:471-473`）。

**结论**：新协议必须**默认走 SAF `SOC-SAF-16` 的保守路径**——收尾窗口（默认 30s）到期仍无法证明"不会再提交"时，
对尚无阳性证据的义务给 `UNKNOWN`，隔离世界并暂停 Campaign。**不得**把旧的常量当真。

## 4. ④ 用量记账

**有账**：Mutator 每次 provider 调用 `MutationProviderAttempt{... input_tokens, output_tokens, actual_cost_microunits}`（`mutation/v2_provider.py:49-64`，
成功值取自 worker 的 `prompt_eval_count/eval_count`，`v2_docker.py:189-212`）；Preparation 汇总（`v2_preparation.py:41-48/162-166`）；
Episode `ExecutionCosts{mutator_tokens, agent_tokens, elapsed_ms, monetary_microunits}`（`fuzzer/v2_corpus.py:138-142`）；
Agent 每 decision 写 `_trace_g_token_usage_v1`（`app/replay/react_decision_recorder.py:42-53`，常量 `replay/models.py:223`）。

**缺口**

1. **Judge 完全没有账**：`v2_target_judge.py` 不解析也不落任何 token 字段（我的核验：该文件内 `token`/`prompt_eval_count`/`eval_count` 零命中）；
   评估对象无 token 维度（`v2_target_preservation.py:31-54`）、不进 `ExecutionCosts`/预算。
   → FBK `SOC-FBK-15` 要求"Judge 分别记录输入/输出 token"，**当前不满足**。
2. Mutator 失败调用默认记 0（`v2_provider.py:97-149`、`v2_docker.py:243-265`）——已产出却失败（截断/协议失败）的调用无账。
3. Agent 失败调用（非 malformed 的 provider 异常）不产生 decision（`langgraph_react_runtime.py:361-375`）→ 该次调用无 token 记录；
   失败 episode 的 receipt `agent_tokens=0`（`v2_real_runtime.py:1356-1395`）。
4. Harness 路径只有 episode 级聚合，adapter **按 decision 均分伪造**单次用量（`deepseek_harness_adapter.py:244-250`）；
   失败 episode 不下载任何录制产物（`replay_engine.py:208-216`）→ 容器销毁后不可取回。
5. Mutator/Judge 调用**无耗时字段**（`v2_provider.py:49-64`、`v2_target_preservation.py:31-54`）。

## 5. 硬阻塞清单（= T01 要求列出的"明确接口工作"）

| # | 阻塞 | 影响的 SPEC 条款 | 需要的接口工作 | 责任 |
|---|---|---|---|---|
| B1 | 失败 Episode 的 Agent token 与中途世界状态不可取回（失败即抛错、不下载录制产物、tmpfs 随容器销毁） | FBK `SOC-FBK-15`、SAF `SOC-SAF-16` | 失败路径也要落"录制状态 + token"（或至少落不可取回的显式标记） | T03/T06 |
| B2 | Harness 路径单次调用 token 不可还原（只有 episode 聚合，且被均分伪造） | FBK `SOC-FBK-15`（"分别记录输入输出 token 与缺失项"） | 要么让 driver 逐次上报 usage，要么把该项标为"不完整"并禁止用该臂做等预算比较 | T06 |
| B3 | Judge 无结构化账目（甚至异常回退时连原始 envelope 都没有） | FBK `SOC-FBK-15` | Judge 调用纳入 attempt/成本模型 | T06 |
| B4 | Mutator/Judge 无耗时信息 | FBK `SOC-FBK-15`（成本轴三选一已定 token，耗时另行报告） | 至少在 attempt 上加 `elapsed_ms` | T02/T06 |
| B5 | Harness 路径 trace 时间戳无信息量（事后重建、临时目录删除） | 诊断与回放报告 | 保留原始 driver 文件或改 live emit | T03/T07 |
| B6 | 取消/关闭无独立凭证（`cleanup_confirmed`/`container_removed` 为常量） | SAF `SOC-SAF-16`、FBK `SOC-FBK-17` | 生成可核验的关闭凭证，或明确只依赖"进程已回收 + 末条为 submit" | T03 |
| B7 | "关闭后不会再提交"无正面凭证（桥与 Oracle 都不拒绝 post-submit 调用） | SAF `SOC-SAF-15/16` | 桥层增加 post-submit 拒绝 + Oracle 时间线校验 | T03 |
| B8 | **提交时的可读性证据缺失**：撤权后无法重建"提交时可读"（delta 无值、id 不可逆、无提交时快照） | SAF `SOC-SAF-15/19`（撤回不抹掉违规、证据链含提交时可读性） | 提交事务内生成效果证据（路径 A）或保留可重建当时状态的能力（路径 B）；见 §2 | T03 |

## 6. 对任务顺序的含义

- B1/B2/B3/B6/B7 **不阻塞** T02（结构生成）与 T03 的 Oracle 骨架，但**阻塞**"等预算的正式比较"：
  在 B1~B3 解决前，正式实验协议必须把"用量的不完整"作为显式报告项，且不能用 token 轴宣称公平。
- B7 建议在 T03 就实现（桥层拒绝 post-submit 调用是最便宜的正面凭证），否则 SAF 的效果关闭只能一直走 UNKNOWN 路径，
  会让 UNKNOWN 占比偏高、削弱结论强度。
