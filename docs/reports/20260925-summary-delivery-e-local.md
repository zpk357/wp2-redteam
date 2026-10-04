# summary-delivery-e 本地验收记录

对应 [SPEC E-01～E-06](../specs/20260925-summary-delivery-e.md) 和 [TASK](../tasks/20260925-summary-delivery-e-task.md)。本轮只有本地实现和离线验证，没有连接服务器、模型服务或 Docker。

代码 **`81fff47`**。最终受影响结构化单元/集成 **538 passed，147.71 s**，其中 E 专项25项。compileall、Ruff（src/agent_image/tests/scripts）、diff-check、Bash `-n` 通过；freeze入口成功生成新清单。TASK本地范围完成，运行包仍DRAFT。

## 实现结果

新 fixture 在 d 的固定公开/受限资产旁增加独立公开索引；不修改旧 c/d，不修改工具权限、Oracle、B/R/J/U 或 FindingKey。阶段协议纳入 manifest/envelope 摘要：同一模型对话和世界运行调查、行动两个阶段；调查提交会收到控制确认再续接，工具结果和全局调用序号保留。每阶段 8/8 调用，资源等分、不可转借；硬限额及取消阻止下一调用。

阶段收据记录任务、决策半开区间、预算、用量、实测耗时及结束原因。每个 ModelDecision 有 phase_id，工具沿 transaction→action request→decision 关联；bundle 验证阶段连续、调用唯一、用量与真实工具返回相符。旧记录省略新增空字段，摘要不变。

Campaign 新增父材料池、搜索种子与待提交覆盖标记。已完整结算状态可由新 search 实例恢复；只有财务收据而未写入覆盖时拒绝继续。入口恢复只跑剩余机会，并避免复用 Episode 编号；新运行拒绝覆盖既有 checkpoint。本运行包仍禁止自动 resume。中途 Episode 崩溃只停止隔离，不声称支持半个会话恢复。

Windows checkpoint 原子替换多次出现临时访问拒绝；同一次 `os.replace` 限定最多 5 次重试，总等待 0.75 s。只处理 Windows 5/32/33，其他错误立即传播；持续拒绝时保留旧 checkpoint 和临时文件并报错。绝不重跑模型、工具或机会。成功暂拒/永久拒绝都有故障注入证据。

## 有判定力的验收

`tests/unit/test_structured_two_phase.py` 使用真实 ReactProviderModelPort、真实 Office Runtime、脚本化 Provider，未联网：

- Provider 从真实搜索结果拿到对象 ID，读取公开索引后访问公开、受限及可变材料；行动阶段使用真实读取正文及创建返回 ID。
- 公开邮件、受限只读、受限提交、公开副本共享、平台共享阻断、来源使用/来源边六个机会均有正式证据；R/J 从 bundle 重算。
- 调查提前 stop、submit、8 次调用用满、token/费用/墙钟用尽、取消、空材料、篡改阶段归属均有反例；不会凭阶段消息伪造工具行为。
- 两臂各 16 机会，中途从磁盘回读并新建 search，父池和后续选择一致；引导实际使用冻结父覆盖，随机毒化反馈仍不改变选择。
- 生成失败和受限正文回显被拒，消耗机会、不制造 Episode；执行提交后及财务收据后故障均阻止未完成状态继续。
- 固定报告工具复算整臂 finalized/coverage，检验读取窗口、局部谱系、预算和曲线；精确符号翻转检验测试全零、同向和反向差值。

报告入口 `scripts/report_summary_delivery_e.py` 只读证据，不能启动模型。实际使用既有事务关联，不再临时猜模型 ID。`scripts/run_summary_delivery_e.sh` 只有明确执行才启动远端批次，本地仅做 Bash 语法检查。

## 历史隔离

[完整性记录](20260925-summary-delivery-e-integrity.json)：c 29 份 + d 28 份，共 **57** 份 finalized 正式回载和摘要校验通过；重序列化载荷与归档 JSON 相同。两个原始压缩包前后 SHA256 不变，旧 c/d fixture 源码与 `1703471` 字节一致（只规范 CRLF）。

复现命令：

```text
scripts/project_pytest.cmd tests/unit/test_structured_two_phase.py
scripts/project_python.cmd -m compileall -q src agent_image tests scripts
scripts/project_ruff.cmd check src agent_image tests scripts
git diff --check
python scripts/check_summary_delivery_e_history.py --archive D:/hxjh/runs/c01-20260925/dev-precheck-c01-20260925.tar.gz --archive D:/hxjh/runs/mat-d-01-20260925/mat-d-01-20260925.tar.gz --output docs/reports/20260925-summary-delivery-e-integrity.json
```

## 边界

离线通过证明被测链路和故障边界，不保证所有 bug 消失或引导优于随机。Provider/网络/GPU/镜像和真实模型分叉仍须授权后的单次批次验证。在途模型请求不能被 Python 线程取消立即杀死；返回后不再执行下一工具，未知副作用由宿主隔离。调用后才获得 token 账目，超额须报告无效。

最终规模和资源为[运行包 DRAFT](../tasks/20260925-summary-delivery-e-run-package.md)：最多 8 对、每臂 16 机会、共享 14 小时；不在本地任务中视为运行授权。服务计价待核算。

## 验证过程的失败记录

初版验收脚本误用了不存在的 `ExposureFact.evidence` 字段，改为正式 `tool_call_id`；报告序列化误把路线 dataclass 当 Pydantic，改用 `asdict`。这些错误在本地暴露并修正，没有服务器试错。

恢复故障注入发现财务结算可完成、反馈未提交的状态，增加 `pending_search_opportunity` 明确阻止继续。一次结构化全量与一次全库验证各遇到 Windows 原子替换 `WinError 5`；全库当时结果 **1551 通过、1 失败、8 条既有警告（641.93 s）**。重复出现后已实现上述有界写盘重试，不再将它仅记为环境偶发。修复后重新执行全部受影响结构化回归；不把修复前全库结果写成全绿。
