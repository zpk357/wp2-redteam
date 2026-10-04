# 多路径世界本地实施与验收

- 日期：2026-10-04
- SPEC：[SPEC-MULTIPATH-WORLD-20261004](../specs/20261004-multi-path-world-and-llm-selection.md)
- TASK：[TASK-MULTIPATH-WORLD-20261004](../tasks/20261004-multi-path-world-and-llm-selection-task.md)
- 修改前 HEAD：`0d6507f689264f604091bec0cb7aa189db394687`
- 状态：本地实现与契约已验证；任务仍为 `IN_PROGRESS`，正式比较准入为 `false`。

## 1. 本次落实的行为

| 要求 | 实现与本地证据 |
|---|---|
| 多任务、多完成方式 | 三族、九个任务变体、十二条路径；文件材料随变体和路径目标变化，Agent 可选其他合法路径 |
| 隐藏任务与可发现权限 | 8/10/12 文件布局，四角色各至少两个；任务经工具发现，权限账本经文件索引查找；场景文件列表不直接返回分类答案 |
| 十类机制、真实载体 | 载体按机制与槽位绑定到文件字段；逐一验证正文、位置、决策维度和阶段，不仅保存注册标签 |
| 引导选择 | 同一 adapter 与完整模型身份的 LLMSelector；数据驱动菜单、结构化选择、拒绝证据、跨集覆盖反馈 |
| 随机选择 | PureRandomSelector 在同一菜单的全部合法组合上均匀、有放回采样；Campaign 强制此入口，零选择器模型调用、零历史读取 |
| 判据 | 确定性风险之上增加 informed/uninformed/unfalsifiable；依据暴露来自成功读取结果并检查发生在交付之前 |
| 恢复 | 选择和材料先冻结；恢复重用已选计划与 Episode journal，不重复选择或已完成工具调用，覆盖只结算一次 |
| 任务完成 | 由任务变体的完成条件与真实提交载荷判定；留草稿不能完成必须交付或创建会议的任务 |

随机臂的“零模型调用”仅指选择阶段。被测 Agent 执行仍由同一模型驱动，不能把它解释成随机臂不运行 Agent。

本比较研究“LLM 覆盖引导选择”与“纯随机选择”。两臂选择策略与选择成本不同，不能将后续差异单独归因于是否使用覆盖反馈。引导选择的调用数、时间及 adapter 提供的 Token 单列；失败或不可观测的 Token 为未知，不能记作零。安慰剂没有接入；旧枚举只为兼容历史对象保留，新计划明确拒绝。

## 2. 本地探针

证据保存在仓库外 `D:/hxjh/wp2-local-validation/`，不提交大文件。

### 场景与工具路径

`scenario-20261004.json`：seed=20261004。

- 十二条工具路径全部完成、提交，且真实工具调用、结果和状态转移保存在产物中。
- 十种机制、两个载体、三布局，共六十份载体物化检查全部通过。
- 十二条路径产生九种不同的真实工具行为；第二机会后仍有新的联合键。
- 这是受控工具探针，不是自由 Provider Agent 的路径分布，也不是风险优势证据。

复现：

```powershell
$env:PYTHONPATH='D:/hxjh/wp2-redteam/src;D:/hxjh/wp2-redteam/agent_image'
.venv/Scripts/python.exe scripts/probe_error_capable_scenario.py --output D:/hxjh/wp2-local-validation/scenario-20261004.json
```

### 两臂闭环与选择隔离

fake Agent 每臂三集，seed=20261004，24 次工具请求预算。

- `aligned=true`：共享 Agent 条件、菜单与首轮输入对齐。
- guided 接收历史反馈；random 不接收反馈、读取哨兵为空、选择器 Provider 调用为零。
- fake 探针使用 ScriptedSelector，仅证明工程闭环。单测另以契约 Provider 驱动真正的 LLMSelector JSON 解析器，验证第二集接收第一集结果并改变选择。
- 已完成 Campaign 重跑不重复调用 Agent 或选择器；中途工具批次恢复后保留未完成调用，不重复提交或覆盖结算。
- 无效 LLM 选择占用机会，拒绝与原响应落盘，恢复不重新抽样。

```powershell
.venv/Scripts/python.exe scripts/probe_error_capable_campaign.py --adapter fake --episodes 3 --seed 20261004 --journal-root D:/hxjh/wp2-local-validation/campaign-final-20261004 --output D:/hxjh/wp2-local-validation/campaign-final-20261004.json
```

输出为 `contract-test-not-a-model-result`，不计作真实模型实验结果。

## 3. 验证记录

| 检查 | 结果 |
|---|---|
| 五个多路径聚焦测试文件 | 通过；成本字段修订前 100 项，修订后相关 campaign/selector 的 32 项再次通过，含三项新增用量测试 |
| 全量 `tests/unit` | 命令退出码 0；在最后的成本记录修订前完成，成本修订已由上述聚焦测试覆盖 |
| 全量 `tests/integration` | 75 passed / 1 failed；失败为旧 structured_v1 自然反馈链测试 |
| 旧集成失败的基线复核 | 独立 HEAD 检出 `0d6507f` 同一测试复现相同断言失败，不由本次多路径代码引入 |
| `compileall -q src agent_image tests` | 通过 |
| 本次 Python 文件 Ruff | 通过 |
| 全仓 Ruff | 未通过：五项既有问题，见下文 |
| `git diff --check` | 通过 |

最低命令：

```text
.venv/Scripts/python.exe -m pytest -q tests/unit --import-mode=importlib
.venv/Scripts/python.exe -m pytest -ra tests/integration --import-mode=importlib
.venv/Scripts/python.exe -m compileall -q src agent_image tests
scripts/project_ruff.cmd check src agent_image tests
git diff --check
```

聚焦范围：`test_error_capable_campaign.py`、`test_error_capable_scenario.py`、`test_error_capable_agent.py`、`test_error_capable_selector.py`、`test_error_capable_local.py`。

全仓已存在的失败：

- `tests/integration/test_structured_natural_feedback_chain.py:118`：期望 `feedback-ranked-unit`，实际 `empty-planned-dimension-fallback`；当前与修改前 HEAD 都失败。
- `src/sandbox/scenarios/office_v2/tools/runtime.py:114`：E501。
- `tests/unit/test_error_capable_journal.py`：F401 一项、F841 一项、E501 两项。本次未修改此文件。

旧 `error-capable-multipath-01` 冻结摘要：`sha256:da824239ccd0acf20b443f4b14eee307ec3d05ac07be78df3996696036abfaa5`，与修改前源码复算相同。旧 structured_v1 fixture 源码与历史 evidence 不在本次改动中。旧世界仅供读取历史，新 Agent/Campaign 入口拒绝以旧 fixture 身份运行新材料。

## 4. 尚未通过的要求

- `MW-AC-06`：没有真实模型对照证明至少两个机制改变不同的决策维度；载体注册与文本差异不能代替这一证据。
- SPEC §7：至少两族在自由模型运行中各出现两条路径、自由运行在第二机会后继续新增覆盖，尚未验证。
- 同身份真实 Provider 下，LLM 选择器能否稳定输出合法组合并有效使用反馈，尚未验证。
- 本次未跑服务器、Docker 或正式 Campaign，未得出 guided 优于 random 的结论。

因此保持 `formal_comparison_eligible=false`。当前可交接的是本地实现与受控证据，不能把本报告当成正式主实验放行证明。全仓检查未全绿、真实模型机制验收未完成，TASK 继续 `IN_PROGRESS`。
