# LC-T01：退役旧 structured 完成判定模块

状态：`DRAFT`（用户已批准 SPEC，尚未确认本 TASK；不得开始删除源码）。

日期：2026-10-10。对应 [已批准 SPEC](../specs/20261010-legacy-code-retirement.md)：LC-01–LC-14；核心范围 LC-07，保留义务 LC-08，引用验收 LC-09，运行验收 LC-10/LC-11。

## 1. 依赖与边界

- 前置：SPEC 已获用户明确批准；本 TASK 经用户确认后才能进入 `READY`；实施开始时更新为 `IN_PROGRESS`。
- 被依赖项：后续旧代码批次可以参考本批证据，但没有因此获得其他文件的删除授权。
- 包含：删除 `src/sandbox/structured_v1/completion.py`；局部修改 `tests/unit/test_structured_completion.py`，移除 8 条旧 API 测试、旧模块 import 与随之无用途的 pytest import，将模块说明改为保留的关闭/确定性事实测试用途。
- 不包含：删除或改写其他源码、其他测试、fixture、历史规格/任务/报告/实验数据；重命名测试文件；修复 joint 提示词残留；修改覆盖/评分/版本/README/运行配置；服务器、Docker 或真实模型运行。
- 文件区域：上述 2 个实现文件、本 SPEC 状态、本 TASK 验收记录、LOG 与 LOG-INDEX 的本批新增记录。LOG/索引原有历史尚未入库，不将其整份作为新文件暂存。
- 数据流：仅移除旧的 `EpisodeEvidence → judge_normal_task → NormalTaskJudgment` API。当前 error-capable 生成/执行/Oracle/覆盖/选择/恢复链及 `judge_episode` 义务判定保持原语义；历史 Git 中的 API 继续存在。

## 2. 实施前核对

当前核对基线 HEAD：`089e0588f8b6cf28eb4456aa4c50d91e254503cb`；实现前还须记录当时 HEAD、Git 状态及文件摘要。

| 文件 | SHA256（TASK 起草时） |
|---|---|
| `src/sandbox/structured_v1/completion.py` | `c956bf0ccf04c6fbee78e9c7b2e3de13397a61c765f15308d70b44a106ca5b3c` |
| `tests/unit/test_structured_completion.py` | `0488173949f9ff2bd1d6e54a1562ac2e8336d513eb786d68c48c3372b90a1b11` |

- [ ] 开始先读 LOG 顶部及本批索引，运行 `git status --short`，核对目标文件、引用与摘要。发现并行修改先停止，不覆盖其他 AI 或用户工作。
- [ ] 将 TASK 从 `READY` 改为 `IN_PROGRESS`，记录批准依据与开始时的 HEAD。
- [ ] 在删除前运行原测试文件的全部 11 条测试及第 4 节主链回归，记录每组退出码、通过/跳过数、失败项。基线失败时停止删除，记录并讨论。

## 3. 精确删除与保留

仅移除以下 8 条测试（LC-07）：

- `test_a_public_delivery_to_the_partner_completes_the_task`
- `test_a_delivery_carrying_restricted_content_does_not_complete_the_task`
- `test_a_delivery_the_partner_cannot_read_is_not_completion`
- `test_a_delivery_to_the_wrong_recipient_is_not_completion`
- `test_no_delivery_at_all_is_not_completion`
- `test_an_unresolved_channel_makes_the_task_answer_unknown`
- `test_a_fixture_without_a_required_delivery_is_unknown`
- `test_the_fixture_identity_must_match`

必须保留以下 3 条测试及 `_effect`、`_evidence`、`_closed` 辅助函数与摘要常量（LC-08）：

- `test_an_unresolved_channel_forbids_a_clean_obligation_result`
- `test_a_provably_cancelled_channel_allows_a_clean_result`
- `test_a_positive_fact_survives_an_unresolved_channel`

实施 checklist：

- [ ] 删除唯一指定源码文件，不递归删目录，不操作 junction 或外部依赖。
- [ ] 精确裁剪上述 8 条测试、相关 import 和过时文件说明；保留 3 条测试与辅助函数的函数体不变。
- [ ] 与删除前 Git 版本进行 AST 核对，确认留下的 3 条测试及辅助函数未被改写；不新增只为验证删除而存在的测试文件。
- [ ] 完成第 4 节验收，检查完整差异和暂存文件名单；追加 LOG/索引，记录结果、限制与提交。验收全部满足后将 TASK 标为 `DONE`，本批独立提交。

## 4. 验收 checklist 与命令

使用仓库 `.cmd` 包装器选择项目 Python 3.12，不用系统旧 Python。以下 pytest 命令只运行本地已有测试；测试中的模型/工具桩不代表真实模型、Docker 或远程验收。pytest 会在 `.tmp` 创建本地测试数据，compileall 会生成 Python 缓存，不将这些产物提交。

- [ ] 删除前：`scripts/project_pytest.cmd tests/unit/test_structured_completion.py`，11 条全部通过。
- [ ] 删除后：`scripts/project_pytest.cmd tests/unit/test_structured_completion.py tests/unit/test_structured_closure.py tests/unit/test_structured_obligations.py`，测试文件恰好保留 3 条原测试，关闭/事实/义务回归通过。
- [ ] 完整收集：`scripts/project_pytest.cmd --collect-only tests`，无模块导入或收集错误，不执行其余测试体。
- [ ] 删除前后各执行一次下列同组主链回归，通过则不再重复扩测：

```text
scripts/project_pytest.cmd tests/unit/test_error_capable_behaviour_chain.py tests/unit/test_error_capable_violation_types.py tests/unit/test_error_capable_increment.py tests/unit/test_error_capable_selector.py tests/unit/test_error_capable_campaign.py tests/unit/test_error_capable_journal.py tests/unit/test_error_capable_runtime_surface.py
```

该组覆盖整链键、自由文本排除、四类独立判定、四维累计等级、仅行为/仅风险/同时新增/无增量、随机不读反馈、引导收到反馈、失败结算、幂等恢复、执行中断与容器入口在缺少 Docker 客户端时的本地导入契约。通过这些现有回归不代表 joint 残留已修复，也不代表完成了真实容器验证。

- [ ] 执行 `scripts/project_python.cmd -m compileall -q src agent_image tests`，退出 0。
- [ ] 执行 `scripts/project_ruff.cmd check src agent_image tests`，退出 0。
- [ ] 引用搜索：`rg -n 'structured_v1\.completion|judge_normal_task|NormalTaskJudgment' src agent_image scripts tests config pyproject.toml` 无匹配（rg 退出 1 表示无匹配）；人工核对 `structured_v1/__init__.py` 无导出，历史 docs/LOG 引用保留。检查整个工作树中的源码/配置相关引用，排除动态字符串或构建入口遗漏。
- [ ] `git diff --check` 与 `git diff --cached --check` 通过；差异仅含本批范围；源码删除前 3,101 B，测试局部裁剪体积按实际差异记录，不把整个测试文件体积计作已删除。
- [ ] 验收结果、未运行的服务器/模型/Docker、实际文件数和字节数写入 TASK/LOG；LOG-INDEX 提供唯一记录标识；只暂存本批已授权文件，生成独立 Git 提交。

## 5. 风险、失败信号与回退

- 风险：仓库外用户脚本可能仍导入旧 API；此处授权只覆盖仓库内部退役，外部调用者须使用历史提交。
- 停止信号：出现新增活跃调用、目标文件摘要变化、基线失败、测试收集错误、保留测试被改写、主链回归失败或 lint/编译失败。记录失败，不扩大删除范围，不顺手修其他路线。
- 回退：记录实现前 HEAD 与实现提交 ID；已提交改动可通过审阅后的反向提交恢复这 2 个文件。未提交时只恢复本批自身改动，不使用 reset/rebase/覆盖式 checkout，不覆盖并行修改。
- 尚未解决：TASK 尚未获用户确认；更大旧路线的入口与重算支持仍待另批审查。首批精确源码范围没有新增待决需求。

## 6. 本阶段证据

- 已读取最新 LOG、LOG-INDEX、AGENTS、SPEC、候选源码/测试、pytest/Python/Ruff 包装器及现有主链测试名称。
- 再次引用检索确认模块仅由自身与该测试文件使用；目标文件摘要如第 2 节。已跟踪实现文件没有用户改动。
- 本阶段仅更新 SPEC 批准状态并产出 DRAFT TASK 与协作记录；尚未删除源码、修改测试或运行实施验收。
