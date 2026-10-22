# FLR-T02：精确清理 766 个有源 Python 缓存

状态：`BLOCKED`（用户已批准；删除中断后 3 个缓存摘要变更，按停止条件保留剩余 64 项，尚未完成删除后验收；T03 未批准）。

日期：2026-10-10。对应 [全面退役 SPEC](../specs/20261010-full-legacy-retirement.md) FLR-01、02、04、05、06、07、09。

## 1. 目标、范围及精确输入

清理 [FLR-T01 清单](../reports/20261010-full-legacy-retirement-inventory.json) 的全部 766 个“可删”项，合计 14,714,839 B。它们都是有源 Python 缓存；本批处理这一完整分类，不只挑两个源码文件。

基线 HEAD `b6901f1a504812abbd92b0d88e5b7feab1538209`。输入清单 SHA256：`cb06161cad17f8ab2a93b4f2bb626b4ce314f7bfe7944c607b7a93c7b0815d4c`。精确对象等于该清单 files 中 classification=可删 的路径集合，也等于 next_batches 中 NEXT-CACHE.paths。每项原字节、摘要、stat、replacement 源路径是删除前校验依据。附录按目录计量，完整路径以固定摘要清单为准，不按通配符或扩展名扩范围。

不包含 394 待定项、298 禁删项、10 个边界、本轮新文件或清单外新生成缓存。特别保留无源 completion.cpython-312.pyc、临时 pytest 后缀文件、docs/reports 证据、源 .py、.tmp/build、外部依赖/junction。用户手动删除文档不恢复、不暂存。

## 2. 依赖、影响和结果

- 前置：FLR-T01 DONE；本任务经用户确认成为 READY 后方可执行，开始改 IN_PROGRESS。
- 与 [FLR-T03](20261010-flr-t03-pending-resolution.md) 无执行先后硬依赖：T03 审核集合不含这 766 项。优先做 T02，再继续待定复核。
- 影响：只移除生成缓存；下次导入可能重新生成，第一次导入可能稍慢。不改变工具、请求、Oracle、整链键、四维风险、增量、随机隔离或 joint 记录语义。
- 输出：精确删除结果清单/阅读报告、本 TASK 状态、LOG/索引、独立验收提交。历史盘点 JSON 保留原始快照，不改写为当前已删除状态。

预计删除区域：src 288 / 4,764,911 B；tests 426 / 9,409,179 B；agent_image 50 / 476,236 B；scripts 2 / 64,513 B。这些都是清单内缓存，不是整个目录。

## 3. 实施 checklist

- [x] 开始读最新 LOG/索引，git status --short 与 HEAD，保留他人工作；状态 READY → IN_PROGRESS。
- [x] 验证输入 JSON 摘要与两种选集一致：766 唯一路径、14,714,839 B，全部 cache/可删、有有效源；存在摘要变化则停止受影响路径并修订任务，不把新缓存自动补进来。
- [x] 每个缓存用不跟随链接的元数据核对 size/SHA256/nlink=1；缓存与每级祖先均非 symlink/reparse point。将每个绝对目标解析并确认位于 D:/hxjh/wp2-redteam 内，且仍等于清单相对路径。
- [x] 核对每个 replacement 源仍在且 size/SHA256 与盘点一致；必要当前入口/构建仍不显式依赖缓存文件。
- [x] 检查本地相关写入进程/打开状态；不自动终止进程。无法排除活动写入或访问被占用时，该路径待定并报告，不强删或静默跳过后称全量完成。
- [x] 记录删除前源码/保护项摘要与测试收集、Ruff 基线；测试/导入使用 -B 或 PYTHONDONTWRITEBYTECODE，避免原地重建。compileall 使用独立 PYTHONPYCACHEPREFIX，使验证生成物不污染目标集合，明确记录生成目录。
- [ ] 同一 PowerShell 用 Remove-Item -LiteralPath 精确逐文件删除，不用 -Recurse、不按目录删除、不跨 shell 传目标；记录每项 deleted/already_absent/blocked/hash_changed。若他人已删，则单列不计成本轮删除。
- [ ] 删除后复核实际移除数量/字节、清单外路径/所有源/待定保护项未变化；缓存如果被活动进程重新建回，记录不认定最终已删除。
- [ ] 按 §4 执行删除后验收，结果写独立报告/JSON；只有完整对象已清理且验收有证据才能 DONE。
- [ ] 写 LOG 顶部和索引；只暂存任务/结果产物。缓存原本 ignored，不强行 git add 缓存；提交用于记录精确删除证据、验证和后续入口，不声称 Git 存了这些缓存字节。

## 4. 验收与验证命令

- [ ] 766 个精确对象清理完毕，实际数量/字节与 baseline 相符或逐项解释 already_absent；无源 .py、待定、禁删、清单外新增项均未删除。
- [ ] 删除前后完整 pytest collect 集合一致（用节点 ID 集合比对，不只比总数）；不得隐藏既有收集失败。
- [ ] 当前主链聚焦回归通过，覆盖 B-only、R-only、joint 记录、无增量、随机隔离、反馈进入下一轮、失败/中断/恢复；本批不修改测试期望。
- [ ] compileall 通过；Ruff 与删除前基线相比无新增问题，既有问题原样记录，不夹带修复。
- [ ] 主机最小导入和 Runtime/transport 聚焦通过；没有运行模型/Docker/远程，准确区分导入与完整执行。
- [ ] JSON 与实际删除数/哈希、源与保护文件摘要一致；git diff --check、git diff --cached --check 通过。

命令框架（PowerShell；环境变量仅对子进程临时设置，并在结束时还原；不修改系统配置）：

`scripts/project_python.cmd -B -m pytest --collect-only -q -p no:cacheprovider`
`scripts/project_python.cmd -B -m pytest -q -p no:cacheprovider tests/unit/test_error_capable_agent.py tests/unit/test_error_capable_behaviour_chain.py tests/unit/test_error_capable_campaign.py tests/unit/test_error_capable_increment.py tests/unit/test_error_capable_journal.py tests/unit/test_error_capable_local.py tests/unit/test_error_capable_priority.py tests/unit/test_error_capable_runtime_surface.py tests/unit/test_error_capable_scenario.py tests/unit/test_error_capable_selector.py tests/unit/test_error_capable_submission.py tests/unit/test_error_capable_violation_types.py tests/unit/test_runtime_client_transport.py tests/unit/test_runtime_state.py`
`scripts/project_python.cmd -m compileall -q src agent_image tests`（执行前将 PYTHONPYCACHEPREFIX 临时设为 .tmp/flr-t02-compile-cache 的绝对路径，保留此生成物元数据，不递归删它）
`scripts/project_ruff.cmd check src agent_image tests`
`scripts/project_python.cmd -B -`（内存校验 JSON/源摘要/导入，不新建仓库工具）
`git diff --check`
`git diff --cached --check`

## 5. 风险、失败与回退

源缺失、哈希变化、junction/硬链接、活动写入、收集差异、回归失败或新增 Ruff 问题是停止信号。未删部分停止，不扩大到未知对象；记录部分进度并回到 TASK，不把跳过当完成。

忽略缓存不在 Git 回退中。回退是从未改变的对应源在相同 Python 版本下重编译；pytest assertion-rewrite 缓存由对应测试重新收集生成，语义可恢复，缓存字节不保证一致。恢复只用于独立验证位置或获准目标，不覆盖他人新文件。无需保存不可替代的缓存副本；若发现缓存是唯一材料或运行确实要求精确字节，本路径退出本任务归待定。

未决：本任务尚待用户确认；精确对象和回退方式已明确。运行检查若出现既有环境缺口，记录并讨论，不能以旧日志“曾通过”替代当前验收。

## 6. 起草阶段证据

已读 LOG 顶部 20261010-full-legacy-retirement-inventory-done、工程约定、批准 SPEC 和盘点产物。2026-10-10 起草时只读复核 766 缓存的 size/已有 SHA256 与清单一致，差异 0；没有执行删除、收集、compileall、Ruff 或模型。两份 TASK 的文档检查在本轮提交前记录。

## 7. 删除集合按目录统计

以下为附录统计而非目录删除命令；目录内只删 JSON 中精确路径。

| 目录 | 文件数 | B |
|---|---:|---:|
| `agent_image/app/__pycache__` | 13 | 128,642 |
| `agent_image/app/adapter/__pycache__` | 8 | 168,346 |
| `agent_image/app/agent/__pycache__` | 6 | 38,215 |
| `agent_image/app/replay/__pycache__` | 7 | 75,759 |
| `agent_image/app/tools/__pycache__` | 13 | 61,832 |
| `agent_image/app/tracing/__pycache__` | 3 | 3,442 |
| `scripts/__pycache__` | 2 | 64,513 |
| `src/sandbox/__pycache__` | 17 | 118,859 |
| `src/sandbox/client/__pycache__` | 4 | 31,172 |
| `src/sandbox/coverage/__pycache__` | 26 | 435,548 |
| `src/sandbox/engine/__pycache__` | 4 | 19,359 |
| `src/sandbox/fuzzer/__pycache__` | 49 | 884,413 |
| `src/sandbox/mutation/__pycache__` | 24 | 232,011 |
| `src/sandbox/mutation/providers/__pycache__` | 3 | 7,634 |
| `src/sandbox/replay/__pycache__` | 12 | 97,589 |
| `src/sandbox/scenarios/__pycache__` | 32 | 808,523 |
| `src/sandbox/scenarios/office_v2/__pycache__` | 31 | 933,040 |
| `src/sandbox/scenarios/office_v2/tools/__pycache__` | 8 | 159,795 |
| `src/sandbox/scenarios/structured_v1/__pycache__` | 1 | 432 |
| `src/sandbox/scenarios/structured_v1/fixtures/__pycache__` | 13 | 113,100 |
| `src/sandbox/scheduler/__pycache__` | 4 | 24,462 |
| `src/sandbox/scoring/__pycache__` | 2 | 3,931 |
| `src/sandbox/storage/__pycache__` | 2 | 8,548 |
| `src/sandbox/structured_v1/__pycache__` | 56 | 886,495 |
| `tests/__pycache__` | 3 | 6,792 |
| `tests/integration/__pycache__` | 32 | 888,049 |
| `tests/unit/__pycache__` | 391 | 8,514,338 |

## 起草验证记录

- .venv/Scripts/python.exe -B - 内存校验：两任务均 DRAFT，引用清单摘要一致；T02 聚合计量恰为 766 / 14,714,839 B，T03 附录精确集合为 394 / 49,161,766 B，互不重叠。全部新增链接存在、无行尾空白。
- 起草末次复核 1,160 个缓存/待定候选 size 与已有 SHA256，变化 0；文档 diff-check 通过。未执行删除、测试收集、Ruff、compileall 或外部运行。只提交两份任务草案，LOG/索引保持本地增量记录。

## 2026-10-11 实施记录（中断 / BLOCKED）

- 用户于 2026-10-10 明确确认 T02“删除”，经 READY → IN_PROGRESS；开始基线 ba9d64b，读 LOG 顶部 20261010-classified-retirement-next-tasks。原用户手动文档删除未处理。
- 原清单固定摘要、766 目标与 550 源、祖先/重解析点、单链接和保护快照核对通过；当前主链输入/执行/Oracle/链/风险/反馈路线只作保护，不改语义。
- 实际删除前命令：scripts/project_python.cmd -B -m pytest --collect-only -q -p no:cacheprovider 退出 0；因双重 q 只列文件数，另加 -o addopts=--tb=short 重收集节点 ID，退出 0、1,955 节点。
- scripts/project_python.cmd -m compileall -q src agent_image tests：退出 0；PYTHONPYCACHEPREFIX=.tmp/flr-t02/compile-before；Ruff --output-format json 退出 1，483 个既有问题/26 文件。没有修源码或调低验收。
- 任务 §4 的 14 文件聚焦命令首次因系统临时目录权限有 27 setup 错误；换全新 --basetemp .tmp/flr-t02/pytest-before 并 -o addopts=--tb=short 后退出 0，258 passed in 523.36s。封装命令的尾部仍指向首次失败日志，未将尾部误当重跑结果；已直接读取 corrected 日志验证通过。PYTHONDONTWRITEBYTECODE=1 / -B；环境仅在当前 shell 暂设/还原。
- 动态变量目标的 PowerShell 批删除被自动审批拒绝，仅返回 blocked by policy，未执行。改用显式绝对路径、每批最多 25 个的 Remove-Item -LiteralPath，无 -Recurse，先独占读/摘要/祖先复核；没有绕过为另一个 shell 删除。
- 用户中断后询问 GitHub 推送范围；终止原执行 cell并核实实际状态。初次中断后读数和末次读数都是 702 不存在 / 64 仍在，其中 61 原摘要一致、3 原摘要不同；不存在项原字节 13,295,981 B，剩余原字节 1,418,858 B。可能有批次部分执行，不虚构每文件完成时刻。
- 续做前哈希复核在 src/sandbox/__pycache__/__init__.cpython-39.pyc 报出变化，按 §3/§5 停止剩余删除。另 models.cpython-39.pyc、protocol.cpython-39.pyc 也变化，保留新字节，待重新准入。没有停止其他 AI 进程/恢复缓存或源。
- 696 非目标快照末次只见 LOG.md 的其他 AI 增量；其余 695 内容不变。并行进程查询曾含本轮校验进程，不能据此断言有外部写入；随后顺序进程查询为空。变化缓存写入来源尚未确认。
- [部分结果报告](../reports/20261011-flr-t02-cache-retirement-partial.md) / [JSON](../reports/20261011-flr-t02-cache-retirement-partial.json)保存全部 766 当前状态、摘要和未验收项；历史盘点不改写。源码、待定、禁删、junction/证据不删除；未推送 GitHub或跑远端/模型/Docker。
- 尚未执行删除后的 collect 对比、聚焦回归、compileall、Ruff 对比，故本 TASK 不可 DONE。后续先对 3 变更缓存重新准入并核实剩余 64，再删除和验收。T03 仍 DRAFT。
