# 引导臂新晋升种子优先探索

状态：`READY`（用户批准；基线 HEAD `7b63766`）
对应规格：[20260920-guided-seed-priority.md](../specs/20260920-guided-seed-priority.md)

## 目的

实现并验证：引导臂新晋升种子进入优先队列，在下一次父样本选择时获得一次性优先机会；队列耗尽后恢复均匀随机。随机臂保持独立随机。

## 前置条件

- [x] 用户批准对应 SPEC，并将本任务置为 `READY`（2026-09-20）。
- [x] 当前工作区无未审查的并发生产代码修改（HEAD `7b63766`，仅两个用户未跟踪文件）。
- [x] 不使用历史 Campaign 证明新机制；需新建 Campaign 做 smoke 和正式运行（本次只做本地实现与验收）。

## 包含范围

- 在现有引导语料/晋升状态中增加可恢复的优先队列状态，优先复用现有 Campaign state 与 digest 校验。
- 在引导选择入口接入队列优先分支，并记录选择原因和队列状态。
- 为恢复、幂等、多晋升、空队列和随机臂增加测试。
- 更新 CLI/报告或运行记录，使新旧协议可区分。

## 不包含范围

- 不改变晋升条件、覆盖特征、变异算子、攻击效果判定或预算。
- 不修改随机臂选择规则。
- 不启动服务器、不重跑历史实验，除非另行授权。

## 预计修改区域

- `src/sandbox/fuzzer/` 中负责 Campaign state、种子池晋升和父样本选择的模块。
- 对应 `tests/unit` 的状态、选择、恢复测试；必要时增加一个最小集成测试。
- `docs/specs/`、`docs/tasks/`、README/运行模板中的协议标识。

## 实施清单

- [x] 定位现有晋升事件、父样本选择和 Campaign state 持久化边界。
      （晋升：`promote_coverage_artifact` → `add_promoted_seed_to_catalog`（`_settle_episode`）；
      选择：`choose_next_allocation` → `build_formal_risk_allocation`；状态：`V2CampaignStateSnapshot`。）
- [x] 设计并实现稳定、可序列化、参与 digest 的优先队列状态。
      （`new_seed_priority_queue` 为有序元组、禁止重复、必须闭包到 catalog；非空时进入 state digest，
      为空时排除以保证历史 Campaign 仍可读取。）
- [x] 实现引导臂“队首优先、空队列均匀随机”的选择路径。
      （`build_priority_risk_allocation` 用 `new_seed_priority_v1` 单选项收据闭合方向与父样本；
      空队列时仍走 `build_formal_risk_allocation`。）
- [x] 确保一次优先机会消费后不重复发放；明确准备拒绝、执行失败和恢复时的消费语义。
      （消费点＝代次结算：`_settle_episode` 与 `_close_non_episode` 都调用 `consume_new_seed_priority`；
      消费由 allocation 的父样本收据决定，重复提交同一结算为幂等空操作。）
- [x] 确保随机臂不读取或写入优先队列。
      （选择侧按策略跳过队列；入队侧 `_enqueue_promoted_seed` 只对 guided 生效。）
- [x] 记录 `new_seed_priority` / `uniform_pool` 及 seed ID，支持离线核对。
      （allocation 原因码 + 收据策略 + 每代 state 快照中的队列；报告 `guided_seed_priority.last_selection_source`。）
- [x] 更新协议版本或运行记录模板，防止新旧 Campaign 混比。
      （不新增持久化协议字段：队列为空即旧行为；报告与收据策略使新旧可区分，运行模板要求新建 Campaign。）

## 验收清单

- [x] 一个新晋升种子在下一次选择中被优先选中。
      （入队规则 `_enqueue_promoted_seed` 与选择规则 `choose_next_allocation` 分别覆盖；本地合成 Episode
      永远停在 `finding_only` 闸门，晋升→选择的整链需要一次真实运行才能端到端复现，见"未验证边界"。）
- [x] 多个新晋升种子按稳定顺序各消费一次优先机会。
      （FIFO 唯一性 + 连续两次选择按入队顺序命中。）
- [x] 队列为空时与旧均匀随机选择保持一致（固定种子回归）。
      （空队列仍走 `build_formal_risk_allocation`，收据策略为 `random_uniform_v1`。）
- [x] 重启、重复结算、重复恢复不丢失或重复消费队列。
      （暂停/恢复/扩展预算/风险进度重建都透传队列；消费为幂等空操作；空队列状态与历史 digest 相同。）
- [x] 随机臂测试证明不存在优先队列路径或跨 Episode 覆盖反馈读取。
      （非空队列下随机臂仍走均匀收据且不消费队列；随机臂跑完后队列恒为空。）
- [x] 总预算和变异调用次数未增加。
      （只改选择顺序：候选数、算子、结算与预算代码未动。）
- [x] 选择日志可重建入队→消费→父样本命中链。
      （每代 state 快照保存队列；allocation 的收据策略与原因码标出命中；报告输出队列与最近来源。）
- [x] 受影响单测、Ruff、compileall、git diff --check 通过。
      （见下方验证记录；未运行集成测试与远端。）

## 验证命令

```text
python -m pytest tests/unit/<affected_tests> -q
python -m pytest tests/integration/<affected_tests> -q
python -m compileall -q src agent_image tests
scripts/project_ruff.cmd check src agent_image tests
git diff --check
```

## 运行与回滚

先执行本地 smoke，确认同一新 Campaign 中优先选择和恢复均可重建，再另行冻结模型、镜像、预算和协议后申请服务器 smoke。若 digest、恢复或随机臂公平性失败，回滚本任务提交；历史 Campaign 和正式实验原件不得改写。

## 验证记录（2026-09-20，本地，无 Docker／无模型／无远端）

- 新增 `tests/unit/test_guided_seed_priority.py`：14 passed。覆盖单选项收据策略与两选项伪造拒绝、
  空队列的历史 digest 兼容、非空队列进入 digest、FIFO 唯一性与 catalog 闭包、消费由收据驱动且幂等、
  暂停/恢复/扩展预算后队列不丢失、随机臂忽略且不填充队列、入队规则只对 guided 晋升生效、
  队首优先与原因码、非 Episode 关闭同样消费、报告暴露队列与最近来源。
- 相邻回归：`test_office_v2_exploration_fairness`、`test_office_v2_comparison_sample`、
  `test_office_v2_blocked_attempt_sample`、`test_exploratory_recovery`、`test_exploratory_campaign`、
  `test_office_v2_campaign_report`、`test_office_v2_comparison_report`、
  `test_v2_effectiveness_protocol`、`test_v2_effectiveness_report`、`test_v2_seed_pools` 全通过。
- `scripts/project_ruff.cmd check src tests` 通过；`compileall -q src tests` 通过；`git diff --check` 通过。
- `build_campaign_state` 改为必填队列参数，全部 11 个调用点已显式透传（漏传会直接报错而不是静默丢队列）。

### 未验证边界（不得当作已验收）

- 本地合成 Episode 全部停在 `resolve_promotion_decision` 的 `finding_only`（`utility-failed`），
  语料晋升从未真正发生；因此"晋升 → 入队 → 下一次优先 → 结算消费"的整链只能在真实运行中复现。
  本次以两个独立环节覆盖：入队规则（`_enqueue_promoted_seed`）与选择/消费规则（含 FIFO）。
- 未运行 `tests/integration`、Docker、真实模型或任何远端任务；未新建 Campaign。
- 新机制对**新** Campaign 生效；旧 Campaign 队列为空时行为与改动前一致，不追溯改判。

## 风险与未决问题

- 优先机会可能把预算集中到低价值新种子，结果可能改善路径扩展但不改善攻击成功率；报告必须分别呈现两者。
- “选择成功并生成准备记录”是否作为优先机会消费点，当前为暂定规则；实现前若发现现有恢复语义冲突，必须回到 SPEC 讨论。
  （2026-09-20 实施决定：消费点定为代次结算，理由与幂等语义见 SPEC"实施决定"第 1 条。）
- 新协议不能与旧协议结果直接合并或作为同一冻结条件下的判优证据。

## 用户决定与批次口径（2026-09-20）

用户决定下一次服务器批次**带上本机制**（原话："带上它跑"）。因此该批次的两臂冻结条件为：

| 项 | guided 臂 | independent 臂 |
|---|---|---|
| 选择规则 | 覆盖率引导 + 新晋升种子一次性优先（`new_seed_priority_v1`） | 均匀随机，不读队列 |
| 其他 | 模型、镜像、场景、种子、Oracle、K、上限、阈值两臂一致 | 同左 |

运行记录必须写明此差异，报告用 `guided_seed_priority`（队列、长度、最近来源）与 allocation 收据策略
区分新旧协议；不得把本次结果与 formal/formal2 拼接或作为同一冻结条件下的判优证据。

若批次想要"纯覆盖率引导"的对照，只能另起一次代码冻结并新建 Campaign，本任务不提供开关。
