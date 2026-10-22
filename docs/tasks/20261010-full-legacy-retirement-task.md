# FLR-T01：全仓旧设计、代码与文档盘点及退役准入

状态：`DONE`（已按用户批准完成只读盘点与验收；尚未执行后续删除。用户手动删除的 SPEC、README 等文档保持现状）。

日期：2026-10-10。对应 [全面退役 SPEC](../specs/20261010-full-legacy-retirement.md) FLR-01–FLR-09。

## 1. 交付目标与任务边界

建立完整的第一方文件清单、引用关系和“可删 / 待定 / 禁删”逐项结论，让后续删除批次具有精确对象与验收证据。完成本任务不代表全面删除已完成；不把“没有搜到文本引用”当成无用途证明。

本任务是全面清理的前置盘点，原文件只读；允许新增本任务的盘点报告和 JSON 清单、更新本任务状态与 LOG/索引。不得删除、移动、重命名、压缩、归档或改写候选原文件；不得修改包导出、CLI、Runtime、Dockerfile、README 或产品 SPEC 内容。

完整清理仍按逐批 TASK 推进：本任务交付带精确对象的退役建议，后续每个源码路线或文档族获得单独任务批准后才能删除。不能在本任务批准后直接删除所有“可删”项。

## 2. 前置依赖与被依赖项

- 前置：全面退役 SPEC 已在本对话获用户批准；本 TASK 确认后才进入 `READY`，开始时改为 `IN_PROGRESS`。
- 基线前置：读取最新 LOG 和索引、执行 Git 状态检查，重新获取 HEAD 和关键文件摘要。起草时 HEAD 为 `6ee0b47`，无已跟踪文件改动；大量已有未跟踪文档与忽略目录不是本任务新增物。
- 历史前置：首批完成模块退役提交 `06ef401` 已存在，不再次将其计入待删对象。
- 被依赖：后续旧源码、旧测试/脚本/构建入口、旧设计文档删除 TASK，均以本任务文件清单与引用证据为输入。若依赖未清零，先提精确的解除依赖方案，不能跳过证明。
- 当前身份：fixture 5.1.0、coverage v5、campaign v17、selector v21（起草时源码核对）；实施时再次核对，不沿用初次 SPEC 的 v16/v20 读数。
- 并行前置：最新 LOG 已记录随机臂替换异常集的实验处理决定，本任务只记录相关证据应保护，不分析、执行或改变实验处理。服务器运行状态以他人记录为历史事实，本任务不查询服务器。

## 3. 范围与受影响的数据流

| 区域 | 盘点内容 | 边界 |
|---|---|---|
| `src` | 全部第一方源码；重点 structured_v1、fuzzer、coverage、mutation、Office V1/V2 与公共组件 | 按模块/函数依赖分类，不按目录名一刀切 |
| `agent_image`、`controller_image` | adapter、协议、工具、Runtime、镜像与依赖清单 | 区分仍支持的入口、可选入口与未证明失效的入口 |
| `agent_variants` | 第一方 harness 源码、配置、锁文件、构建输入 | 不进入 node_modules、上游检出或外部依赖目标 |
| `scripts`、`tests`、`config` | CLI、一次性脚本、报告脚本、测试辅助函数、conftest、配置引用 | 检查跨测试 import、pytest 插件/fixture、动态入口与发布注册 |
| `docs` 与根目录文档 | SPEC/TASK/计划/现状/交接/说明/报告；文档内部引用与权威身份 | 文本过时、原始证据、已批准有效契约分别判断；保留 LOG/索引 |
| 根目录元文件 | pyproject、ignore、构建/发布相关文件、临时配置及二进制资料的路径/体积 | 不执行脚本或解压资料，不输出秘密内容 |
| data、reports、build、缓存/环境 | 作为证据/构建输入/生成物/依赖边界登记 | 默认保护证据；不为统计可释放空间遍历依赖环境或读取大体积运行原文 |

`.git`、虚拟环境、第三方依赖目录、宿主机其他目录、服务器、密钥内容均不参与第一方内容检索。目录链接、junction、reparse point 和符号链接只登记边界，禁止跟随目标；边界项计数/字节无法核实时写 `unknown`，不写 0 冒充已核查。

只新增盘点输出；不会改变“输入 → 选择/生成 → Agent → 工具/状态/轨迹 → Oracle → 链/四维等级/增量 → 下一轮”的数据流。当前四条硬设计与随机公平性是保护依据，不从旧文档反向生成需求。

## 4. 实施 checklist

### A. 冻结本轮读数（FLR-02、FLR-03、FLR-09）

- [x] 读 LOG 顶部、索引、有效 SPEC/工程约定；必要历史按索引定点阅读，不默认展开整份历史。
- [x] 记录 HEAD、Git 跟踪/未跟踪/忽略/已修改状态、Python 版本和起止时间；核对未提交改动所属范围，已有文件不自动归为本任务新增。
- [x] 列出盘点区域及排除区域，登记链接边界与不可读项；开始时将任务改为 `IN_PROGRESS`。

### B. 枚举与计量（FLR-01、FLR-04）

- [x] 枚举第一方文件，覆盖未跟踪文档以及可能被 ignore 隐藏的第一方配置/源码；使用 rg 列表与不跟随目录链接的元数据遍历互相核对。
- [x] 每项记实际路径、所属区域、文件类型、字节数、Git 状态；候选源码/文档记 SHA256 与读取时间。记录源文件工作树字节口径，LF Git blob 不和 CRLF 工作树混算。
- [x] 按“源码/文档/测试/配置/证据/缓存/外部边界”统计互斥小计；组与明细不能重复累加；目录整体体积不能冒充预计可删空间。
- [x] 对目录链接、硬链接、不可读、读取期间变化、正在写入的产物单独标注；无法判定就待定，不追到工作区外。

### C. 当前主链与引用图（FLR-03、FLR-04、FLR-06）

- [x] 从 `run_neighborhood_campaign.py`、error-capable 执行/选择/结算/恢复入口、Agent Runtime 和构建入口追踪主链，记录证据位置、模块与传递依赖。
- [x] 用 Python AST 分析 import/from import、相对导入、包 __init__ 导出，再用 rg 补充 importlib、插件注册、字符串模块名、CLI、subprocess、环境选择分支、Docker COPY/ARG、依赖锁文件和测试辅助引用。不能解析的边标 `unresolved`。
- [x] 分开记录运行直接/传递依赖、构建输入、测试收集依赖、可选旧入口、文档链接、历史复现依赖。每条边写来源路径/位置、目标、边类型、判定依据与置信限制。
- [x] 重点核查实际依赖：`app.runtime → structured_v1.diagnostics`；AdapterFactory 的 structured/error-capable/Office V2/旧 adapter 分支；`agent_image/Dockerfile` 的冻结清单 COPY；pyproject 的 `sandbox.fuzzer.v2_cli`；scenarios/coverage 包初始化的旧模块导入。
- [x] 对已确认无副作用的当前入口，使用项目 Python `-B` 做本地最小导入核对（设置 `PYTHONDONTWRITEBYTECODE=1`）；未能证明无初始化副作用的候选模块只静态分析，不执行它的 CLI 或导入。未验证项必须登记，不能写“运行证明无依赖”。

### D. 文档权威与证据边界（FLR-01、FLR-03、FLR-08）

- [x] 逐文件判断有效契约、待批草案、历史决策、已废弃设计、当前入口误述或实验原始证据；给出 superseded 的具体依据及替代入口，不按日期推断。
- [x] 检查 README、docs/README、HANDOFF、SPEC 兼容入口、旧 plans/current-state 和代码注释中仍宣称旧路线为当前的叙述；记录需删除/重写的精确位置，本任务不直接修改。
- [x] 逐个核对 specs/tasks 是否被 LOG、报告、授权或当前代码引用；sole-source 决策和复现资料没有可核验替代物就待定/禁删。
- [x] 登记 docs/SPEC 和当前四条硬设计的冲突、joint 活跃反馈残留等差距；它们属于独立修订/修复，不以“删除旧文件”隐藏冲突。
- [x] 轨迹、journal、checkpoint、原始报告数据、哈希/身份与运行记录列为证据保护项；没有外部归档证明，不建议删除。

### E. 分类与逐批建议（FLR-04、FLR-05、FLR-09）

- [x] 每个文件/边界恰有“可删 / 待定 / 禁删”之一，给出可核查依据；目录混合时必须下钻。未知不能归可删。
- [x] 可删只限满足 SPEC §4.1 的对象；旧源码内部互相引用、仍有旧测试或发布入口的闭合文件族先列待定，并列出必须共同撤除的依赖。不凭“闭环看似独立”批准整族删除。
- [x] 按互不重叠的源码路线或文档族提出下一批具体建议，列精确文件集、预计数量/字节、要先解除的依赖、应保留的测试与验收范围；允许某些区域可删数为 0。
- [x] 若需要撤 CLI、包导出、adapter 分支或 frozen asset，单列该行为/接口影响，先由用户决定，再据此拟单独 TASK；不把“迁移完成后可删”写成现在可删。
- [x] 给出完整性账目：第一方枚举总数 = 三分类明细总数；另列排除边界、不读内容项及剩余不确定性。本任务产物不递归盘点自己，另列新增产物计量。

### F. 验证、记录与提交（FLR-02、FLR-07、FLR-09）

- [x] 核对报告与 JSON 路径一致、数量/字节吻合、分类/理由完整、引用节点存在或注明既有缺失；随机抽样以外，还逐项复核所有可删候选。
- [x] 核对读前/读后 Git 状态与候选摘要；并行变化使受影响分类退回待定，其他独立区域继续。报告注明对应快照，不能拿旧摘要授权新文件删除。
- [x] 文档 diff-check、JSON 可解析性、链接检查通过；LOG 顶部追加唯一记录并更新索引，写实际命令、限制、没做的运行与下一入口。
- [x] 只暂存本任务明确新增/修改的 SPEC/TASK/盘点产物，不夹带原有未跟踪历史或他人改动；证据齐全后标为 DONE，独立提交。

## 5. 精确输出与修改区域

1. `docs/reports/20261010-full-legacy-retirement-inventory.md`：阅读版总表、各区域数量/字节、引用图摘要、可删/待定/禁删清单、权威冲突、证据保护清单及推荐批次。每项包含依据和相关文件位置。
2. `docs/reports/20261010-full-legacy-retirement-inventory.json`：可复核文件清单、组统计、链接/排除边界和引用边；记录 schema、HEAD、盘点时间、已扫描区域/跳过项、原始字节口径。
3. 本 TASK：实际状态、运行命令、结果与验收证据。LOG/LOG-INDEX：本轮增量记录。

JSON 每项至少包含：`path`、`kind`、`count`、`bytes`、`git_status`、候选 `sha256`、`classification`、`reason`、直接与间接引用/入口、测试依赖、历史证据依赖、替代物、删除副作用、回退条件、审计者及可追溯来源。引用边可放独立数组，明细以 ID 关联。

本任务不新增生产源码、仓库内盘点工具或测试，不引入依赖。需要计算时使用已有工具或一次性内存脚本；后续若值得维护专门工具，单独说明范围与成本。

## 6. 验收与验证方法

- [x] 两个盘点产物存在，逐项含文件数/字节数/分类/判定依据；JSON 与报告统计一致，无重复计数；所有可删候选均有人工复核证据。
- [x] 当前主链保留集可沿引用边解释；structured、fuzzer、Office V1/V2、旧 coverage/mutation、adapter、脚本/测试、旧文档族与构建/发布入口均得到结论，未覆盖内容显式列出。
- [x] 没有原文件删除、移动、改写或压缩；没有跟随 junction；没有把工作区外体积算为本地可删空间；没有输出凭据。
- [x] 所有动态/历史/构建依赖未决项为待定或禁删；报告给出可以供下一 TASK 审阅的具体批次，不把完成盘点宣称为清理完成。
- [x] `git diff --check`、`git diff --cached --check` 通过；项目 Python `-B` 读取 JSON 与路径/汇总核对通过；本任务文档链接有效。

执行时可采用的命令与观察方法：

```text
git status --short
git rev-parse HEAD
git ls-files
git ls-files --others --exclude-standard
rg --files <第一方区域> -g !**/__pycache__/** -g !**/node_modules/**
rg -n <候选模块/API/入口/路径> <逐项排除依赖与证据内容后的区域>
scripts/project_python.cmd -B <已核对无副作用的最小导入/JSON核对命令>
git diff --check
git diff --cached --check
```

上述尖括号为执行时具体化的参数，实际完整命令写入结果记录；`rg` 退出 1 表示无匹配，不等于已证明无动态依赖。全仓范围搜索不得仅依赖 rg 默认 ignore。

本阶段没有 Python 生产改动，FLR-07 的 compileall、全仓 Ruff、pytest 全收集和主链回归属于后续删除 TASK 的运行验收，本任务不为了静态盘点重跑它们。旧日志中的 1,953 收集数与 113 主链回归数只是历史证据，后续 TASK 必须针对当时基线重新选组；这轮不报告它们新通过。

## 7. 风险、失败信号、回退与待决

- 动态导入、镜像整目录 COPY、隐式 fixture、字符串入口可能使引用图不完整；未知边保守分类，并列出需要的最小运行验证，不盲目删。
- 未跟踪旧文档没有 Git 恢复保障；即使有内容过时证据，也须在后续删除前补回退/归档条件。现有历史 LOG/索引不作为新文件顺带提交。
- 冻结材料、旧 CLI 和共享 diagnostics 仍有用途；此类下线属于接口/构建行为变化，独立 TASK 必须写清影响与授权。
- 盘点期间并行变化：仅使受影响记录失效，登记为待定；不要停止全部只读工作或覆盖新版本。不可读、秘密、链接、证据内容不展开，标明边界。
- 回退：本任务只新增报告和自己的状态/日志记录；若报告有误，以后续修订提交纠正，不改历史实验、不 reset/rebase、不覆盖并行文件。后续实际删除必须有精确回退提交或获准的备份。
- 待决：本 TASK 已获批准；旧 CLI 下线、shared structured 迁移、历史文档归档位置等在盘点交付中给证据和建议，本 TASK 不替用户决定。

## 8. 起草阶段实际证据

- 已读 AGENTS、docs/SPEC、全面清理 SPEC 和最新 LOG 顶部 `20261010-random-arm-faults-replaced`，以及相关主实验/修复记录；索引的运行记录不完整，实施时必须读 LOG 顶部。
- 起草基线 HEAD `6ee0b47`，无已跟踪改动；先前另一 AI 的 3 个修复已进入 Git，全面 SPEC §2/§6 的工作树描述是起草时快照，不把它当作本轮未提交事实。
- 静态核对了 adapter 分支、Runtime diagnostics import、pyproject CLI、包初始化和 Dockerfile 冻结清单输入；当前版本 v17/v21 已在源码读取确认。
- 本轮只标记 SPEC 批准并写 DRAFT TASK、LOG/索引；未创建盘点报告/JSON、未删除候选、未运行实施验收、未查询服务器。

## 9. 实施记录

- 开始基线 `ef2807a`；工作前读取 `20261010-remote-main-replaced-with-src-only`。本轮只操作本地工作树，不推送或改变远端 main。
- 用户已手动删除 README、SPEC 及多份文档：不恢复、不暂存这些删除；统计存活文件，缺失引用只登记现状。跟踪删除为 README、20261009-violation-taxonomy SPEC/TASK，其他未跟踪文档的消失以当时本地快照说明，不臆造完整删除清单。

- 盘点交付：[阅读版](../reports/20261010-full-legacy-retirement-inventory.md)、[机器清单](../reports/20261010-full-legacy-retirement-inventory.json)。1,458 个第一方文件 / 70,424,894 B；可删 766 / 14,714,839 B（全部有源缓存），待定 394 / 49,161,766 B，禁删 298 / 6,548,289 B。另有 10 个边界，不重复叠加。
- 引用图 17,237 边；12 个动态 file-loader 人工追踪补齐。保留 5 个明确未决边：3 个旧 mutation → 已不存在 config，2 个外部可覆盖的 Docker ARG。没有将未知归可删。
- 全量可删缓存有 SHA256、对应源、无显式引用、nlink=1、非链接核对；一个无源缓存保持待定。4 个孤立源码和 10 个历史文档归待定，互不重叠精确批次、字节、支持契约门槛与回退条件都在报告/JSON。
- 117 个第一方模块实际最小导入加载；当前入口静态闭包 207 文件，包含 Runtime 条件/延迟/Replay 支持分支和容器 rpc_client 字符串入口，不以“当前选择未走到”视为可删。
- 当前四条硬设计、v17/v21 身份、joint 提示词残留、AGENTS 旧联合叙述与用户决定优先关系均已登记。历史审计不是当前要求；缺失 SPEC/README 不恢复、不阻塞、不提交其删除。
- 末次重新核对全部 1,458 文件 size/mtime 与已记录 SHA256，变化 0；没有跟随 junction，未读取依赖/运行大体积正文、未删原文件。TASK/LOG 的最终状态写入晚于清单快照，是明确的本任务产物。

### 实际命令与结果

所有 Python 脚本以 PowerShell here-string 经标准输入执行，不新增仓库审计工具：

项目 Python 为 3.12.14（.venv/Scripts/python.exe -B --version 实测）。输出自身不计入递归清单：阅读报告 168,402 B，JSON 6,930,759 B；本 TASK 字节随最终状态记录变化。

1. git status --short、git rev-parse HEAD、git ls-files -z、git check-ignore --stdin：识别并保留原工作树。开始/结束原有跟踪删除仍为 README 和 20261009 taxonomy SPEC/TASK；本任务只增加 TASK/两份报告。
2. .venv/Scripts/python.exe -B -：内存枚举/AST/引用脚本；无 read/AST 错误，边界只计元数据。使用 UTF-8 stdout；首次 stdout CP936 与 Windows DirEntry identity 输出不完整均已纠正，未修改候选文件。
3. rg --files --hidden --no-ignore -g !**/.git/** -g !**/.venv/** -g !**/.deps/** -g !**/node_modules/** -g !**/.upstream-probe/** -g !**/.codex-tmp/** -g !data/** -g !reports/** -g !build/** -g !.tmp/** -g !**/*.egg-info/**：退出 0；排除本任务产物/边界后 1,458 路径，与不跟随链接枚举集合差为 0。
4. rg -n --hidden --glob !**/__pycache__/** --glob !**/node_modules/** --glob !**/.upstream-probe/** 的模块/API/动态入口搜索，以及源文件摘录人工核对：4 个候选独有 API 只在自身。两次补充搜索传入不存在路径/字面通配失败，已改真实目录重查，没有以失败搜索证明无依赖。
5. .venv/Scripts/python.exe -B -：安全最小导入 campaign、selector、error-capable adapter、runtime；全部成功。另一内存 MetaPathFinder 屏蔽 policy/resources/soak/scheduler.exceptions 后，以上入口及 app.server 导入仍成功。只证明初始化，不证明可选 CLI/镜像/模型执行无依赖。
6. .venv/Scripts/python.exe -B -：全量 JSON/MD 核验；路径唯一、分类/字节汇总、17,237 边 ID/端点/反向引用一致；691 个非缓存文件全部在 MD，766 可删缓存摘要复核，报告全部新增链接有效；末次候选摘要/元数据不变。
7. git diff --check 和 git diff --cached --check：退出 0；另以 Python 核对未跟踪产物新增行空白与链接。只暂存此 TASK 和两份盘点输出，不纳入原有 LOG/索引全文或用户删除。
8. 未运行 pytest/compileall/Ruff、Docker、真实模型、服务器或历史重放；本阶段没有生产源码修改。后续删除 TASK 必须满足 FLR-07，不沿用历史测试数冒充当前通过。

本轮结束在 LOG 顶部新增 20261010-full-legacy-retirement-inventory-done 并更新索引；LOG/LOG-INDEX 仍是原有未跟踪历史，仅增量写入、不整体暂存。独立提交只包含本任务三文件。下一入口为报告 §5 的精确后续 TASK；没有下线 CLI/adapter/包导出/冻结资产、没有自动删除缓存或旧源码。
