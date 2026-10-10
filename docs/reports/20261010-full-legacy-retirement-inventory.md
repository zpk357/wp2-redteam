# FLR-T01：全仓旧设计、代码与文档退役盘点

本轮只完成盘点，未新增删除、移动、重命名、压缩或候选原文改写。用户手动删除的 README/SPEC 等保持现状，不恢复、不暂存。全面清理尚未完成。

基线：本地 public @ `ef2807a9eab7fcf81e7603b41c0fd63772dae980`；开始 UTC 2026-10-10T12:30:50.687547+00:00；完成 UTC 2026-10-10T12:36:54.808836+00:00。版本：fixture 5.1.0 / coverage v5 / campaign v17 / selector v21。

## 1. 结论与统计口径

当前可删项全部是有源码对应的 Python 缓存，**旧源码/旧文档立即可删为 0**。找到 4 个优先核验的孤立源码候选和 10 个旧计划/现状审计候选，精确名单与准入门槛见 §5；其他旧目录仍有共享导入、发布、可选入口、测试或历史复现依赖。

禁删是本轮保护，不代表永远无法退役；待定须先解除/确认依赖。可删不等于本轮已获删除授权：FLR-T01 明确只读原文件，具体删除仍须独立 TASK。

| 分类 | 文件数 | B | 依据 |
|---|---:|---:|---|
| 可删 | 766 | 14,714,839 | 逐项确认源码对应、无显式引用、单硬链接的可再生缓存；删除前复核活动写入 |
| 待定 | 394 | 49,161,766 | 接口/测试/历史依赖、文档唯一信息、可选入口、未知用途或未展开二进制 |
| 禁删 | 298 | 6,548,289 | 当前主链、构建/发布/协作契约或未归档证据 |
| 合计 | 1,458 | 70,424,894 | 三分类互斥 |

工作树实际字节口径；不含自身产物、10 个边界条目、用户已删文件。边界另计，外部目标不算本地空间。TASK/LOG 结束时会更新，其摘要只对应盘点时刻。

### 区域统计（含缓存）

| 区域 | 总文件数 / B | 可删数 / B | 待定数 / B | 禁删数 / B |
|---|---:|---:|---:|---:|
| `(root)` | 14 / 45,130,876 | 0 / 0 | 7 / 44,461,392 | 7 / 669,484 |
| `agent_image` | 108 / 890,850 | 50 / 476,236 | 10 / 37,472 | 48 / 377,142 |
| `agent_variants` | 14 / 238,340 | 0 / 0 | 14 / 238,340 | 0 / 0 |
| `config` | 1 / 5,296 | 0 / 0 | 1 / 5,296 | 0 / 0 |
| `controller_image` | 2 / 601 | 0 / 0 | 0 / 0 | 2 / 601 |
| `docs` | 63 / 3,011,416 | 0 / 0 | 10 / 300,139 | 53 / 2,711,277 |
| `scripts` | 31 / 416,718 | 2 / 64,513 | 15 / 213,384 | 14 / 138,821 |
| `src` | 583 / 9,224,861 | 288 / 4,764,911 | 138 / 2,093,884 | 157 / 2,366,066 |
| `tests` | 642 / 11,505,936 | 426 / 9,409,179 | 199 / 1,811,859 | 17 / 284,898 |

### 重点路线（不含缓存，不额外叠加总量）

| 范围 | 文件数 / B | 可删 / 待定 / 禁删 | 依据 |
|---|---:|---:|---|
| `src/sandbox/structured_v1/` | 56 / 710,465 | 0 / 26 / 30 | Runtime→diagnostics；adapter→structured runtime/executor；旧测试/报告/冻结资产仍有依赖 |
| `src/sandbox/fuzzer/` | 49 / 737,281 | 0 / 47 / 2 | pyproject 注册 v2_cli；config/store/executor/测试成族；resources/soak 为先核验候选 |
| `src/sandbox/mutation/` | 27 / 187,121 | 0 / 27 / 0 | 旧 mutator/diversity/priority 引用已不存在 config，另有 fuzzer/测试依赖，不能因断链直接删除 |
| `src/sandbox/coverage/` | 26 / 350,294 | 0 / 2 / 24 | 包初始化/共享风险抽象仍引用旧模块，不能删整个包 |
| `src/sandbox/scenarios/office_v2/` | 47 / 1,324,941 | 0 / 10 / 37 | 当前主链复用工具/状态/Oracle；旧 generation/operator 单独待核实 |
| `agent_image/app/` | 50 / 400,640 | 0 / 4 / 46 | Runtime/当前 adapter/旧 adapter/工具/replay 混合，必须下钻 |
| `agent_variants/` | 14 / 238,340 | 0 / 14 / 0 | 仍支持 DeepSeek 可选 runtime；Node 相对 imports/镜像输入已入图 |
| `scripts/` | 29 / 352,205 | 0 / 15 / 14 | 当前 runner/probe 保留，旧 CLI/报告有动态 loader/历史用途 |
| `tests/` | 216 / 2,096,757 | 0 / 199 / 17 | 当前测试/conftest 保护；旧测试可收集且有跨文件辅助依赖 |
| `docs/current-state/` | 8 / 272,232 | 0 / 8 / 0 | 8 篇历史审计，不能当当前规格，先保留历史索引 |
| `docs/plans/` | 1 / 3,122 | 0 / 1 / 0 | 旧六步/12-family/risk-only/promotion 计划与当前语义不同 |

## 2. 当前数据流与保留依据

坐标菜单 → error_capable_selector → world/materialization → executor/container adapter → Office V2 工具/状态 → bridge/oracle → 整链键/四维风险结算 → journal/Campaign → 下一轮反馈（随机臂不读历史）。JSON 的 current_dependency_route 和 measured_import 可追溯每个当前依赖。静态图含延迟/条件分支，不表示分支已执行。

引用图 17,237 条边；实际最小导入加载 117 个第一方模块；当前静态入口闭包 207 个文件（包括 server 与 rpc_client 字符串命令）。边类型区分 Python import/包初始化、动态 literal/file-loader、CLI、Node 相对 import、跨测试 import、文本/文档引用、Docker COPY。

- [src/sandbox/scenarios/__init__.py](../../src/sandbox/scenarios/__init__.py) 导入 campaign 时实际加载 Office V1/candidate_generation/office_mutation；先解除包级导出才可退役。
- [agent_image/app/runtime.py](../../agent_image/app/runtime.py) 第 22 行依赖 structured diagnostics；不能随整个 structured 目录删。
- [agent_image/app/adapter/factory.py](../../agent_image/app/adapter/factory.py) 保留 structured、DeepSeek、Office V2 LangGraph、旧 TRACE 分支；当前请求不走该分支不等于已下线。
- [pyproject.toml](../../pyproject.toml) 第 31 行注册 sandbox.fuzzer.v2_cli:main；setuptools 扫描 src，下线会影响发布接口。
- [agent_image/Dockerfile](../../agent_image/Dockerfile) 第 17–21 行 COPY 整个 src/app 和冻结 manifest；COPY inclusion 不证明有调用，但 SPEC §4.1 要求镜像/打包边界明确，孤立源码仍待定。
- [src/sandbox/client/runtime_client.py](../../src/sandbox/client/runtime_client.py) 第 310 行用 python -m app.rpc_client，单看 import AST 会漏；此入口已保护。
- [src/sandbox/coverage/__init__.py](../../src/sandbox/coverage/__init__.py) 的包导出/共享风险抽象仍有当前静态引用，不能以旧名称推断无用。

12 处动态 file-loader 已人工追踪到 report_summary_delivery_e2、run_structured_v1_episode、diagnose_guidance_yield、range_calibration_probe 或 report_summary_delivery_e，具体来源行号见 JSON resolved_dynamic_loaders。未执行 loader/旧 CLI。

## 3. 未决边与保护边界

| 未决内容 | 项数 | 处置 |
|---|---:|---|
| diversity.py/mutator.py/priority.py → sandbox.mutation.config（已不存在） | 3 | 原有旧路线断链；待定，不恢复/修复，后续决定接口/闭合族下线 |
| Docker ARG STRUCTURED_V1_FREEZE_MANIFEST / HARNESS_NODE_MODULES_ARCHIVE | 2 | 默认已读，manifest 脚本覆盖到 data/structured-v1/freeze-manifest.json；外部构建上下文可另提供输入，未跑构建，保持未决 |

边界只读本地元数据，不读运行原文、不跟随链接；以下不纳入 §1 总量。

| 边界 | 文件数 | B | 分类 | 依据 |
|---|---:|---:|---|---|
| `.git` | unknown | unknown | 禁删 | 第三方/环境/版本数据库边界，不展开 |
| `.tmp` | 635 | 215,025,642 | 待定 | 生成物/测试临时产物；用途未核实，不作为旧代码删除 |
| `.venv` | unknown | unknown | 禁删 | 第三方/环境/版本数据库边界，不展开 |
| `agent_variants/deepseek_harness/.upstream-probe` | unknown | unknown | 禁删 | 第三方/环境/版本数据库边界，不展开 |
| `agent_variants/deepseek_harness/node_modules` | unknown | unknown | 禁删 | 第三方/环境/版本数据库边界，不展开 |
| `build/presentation-20260929/node_modules` | unknown | unknown | 禁删 | 目录链接/重解析点只登记，不跟随目标 |
| `build` | 20 | 995,893 | 待定 | 生成物/测试临时产物；用途未核实，不作为旧代码删除 |
| `data` | 298 | 8,379,810 | 禁删 | 仅统计本地元数据；运行证据保护 |
| `reports` | 3,491 | 2,616,368,067 | 禁删 | 仅统计本地元数据；运行证据保护 |
| `src/trace_g_wp2_redteam.egg-info` | 6 | 11,838 | 待定 | 安装生成元数据，不作为旧源码；删除影响安装状态 |

build/presentation-20260929/node_modules 为 junction，未跟随，build 统计排除它。data/structured-v1/freeze-manifest.json 为 1 文件 / 6,001 B，已在 data 边界计量。

docs/reports 的 49 份既有材料、data/reports 下轨迹、checkpoint、journal、Oracle/Coverage/Replay/Artifact、清理证据均缺少可核验外部归档，所以保护。缓存若处于 docs/reports 仍按证据保护。.tmp/build 用途只凭元数据不能证明失效。第三方/.git/环境 unknown 不冒充 0。

## 4. 旧文档与独立语义差距

当前四条硬设计以用户明确决定和批准 FLR SPEC 为准：有序整链为行为键（自由文本不入键、path 留文件名、minted ID 归一化）；四类独立风险累计 1–6 级，等级不入键；增量为新链或任一等级上升；joint 只记录不进分数/提示词；scan/carrier_opened 是报告字段。

旧计划的六步循环、两个 runtime、12-family、risk-only 调度、promotion 不再指导当前主链。LOG 的 20260831-office-v2-semantic-mutation-correction-plan 保存历史决定；本段保存被取代的理由，不声称已迁移全部独有信息。

current-state 的 5 篇 a15b127 审计与 3 篇 SOC-T01 审计写明历史基线，涉及旧 fuzzer/structured/summary-delivery-A；旧 DONE TASK 记载验收/批准提交 ff08b7c。先保留唯一事实和短索引，再撤出旧叙述，不能假定未跟踪原文能用 Git 回退。

DeepSeek README 仍对应可选 adapter；tests/design 两篇仍定义 Office V2 assessment/12-family 验收和缺口，属于不同文件族，保持待定。

独立差距：error_capable_selector.py:503 将 feedback 整体 model_dump 到提示词；error_capable_coverage.py:1194–1197 有 joint/stall 字段（此前 LOG 已核实）。本轮登记待修，不修改分数、提示词、版本和实验口径。AGENTS §4 旧联合关系叙述服从用户 joint 仅记录的决定。

用户删除 README/SPEC：只统计存活文件，JSON existing_missing_references 记录既有缺失。pyproject 仍引用 README，未来打包可能受影响；本轮不恢复、不改写、不将其作为盘点阻塞。

## 5. 后续精确批次建议

各范围互不重叠，只是下一 TASK 的输入。源码批次至少验证完整 collect、compileall、主链及受影响聚焦回归、无新增 Ruff 问题、引用归零；保留未列入名单的源码/测试。Docker/模型/远程按单独授权范围处理。

| 建议 ID | 文件数 | B | 当前分类 | 门槛 |
|---|---:|---:|---|---|
| NEXT-CACHE：可再生缓存 | 766 | 14,714,839 | 可删 | 删除前复核源摘要与活动写入，排除无源缓存/报告证据缓存 |
| NEXT-FUZZER-LEAVES：旧 fuzzer 容量/soak 模块 | 2 | 11,769 | 待定 | 先确认两接口下线；完整 collect、主链/本模块回归、compileall、无新增 Ruff 问题 |
| NEXT-TOOL-POLICY：旧工具常量模块 | 1 | 300 | 待定 | 确认镜像/外部 API 兼容边界；当前工具和 Runtime 导入/回归及完整 collect |
| NEXT-SCHEDULER-ALIAS：旧错误别名模块 | 1 | 330 | 待定 | 确认兼容 import 下线；scheduler/清理恢复聚焦、主链回归与完整 collect |
| NEXT-HISTORIC-DOCS：旧计划/现状审计文档族 | 10 | 300,139 | 待定 | 先保留唯一历史事实/验收短索引，并确认未跟踪原文回退方式；文档 diff-check/链接核对 |

### NEXT-FUZZER-LEAVES 精确名单

- [src/sandbox/fuzzer/resources.py](../../src/sandbox/fuzzer/resources.py)：1 文件 / 1,592 B。未发现模块、docker_memory_limit_bytes/validate_host_capacity 外部引用；旧容量逻辑仍被 setuptools 包发现/镜像 COPY；屏蔽候选的最小导入通过，未运行删除后收集/回归，不满足 FLR §4.1，暂列待定
- [src/sandbox/fuzzer/soak.py](../../src/sandbox/fuzzer/soak.py)：1 文件 / 10,177 B。未发现模块、SoakProbeRegistry/SoakRunner 外部调用；旧 soak 路线仍被 setuptools 包发现/镜像 COPY；屏蔽候选的最小导入通过，未运行删除后收集/回归，不满足 FLR §4.1，暂列待定

保留 engine/v2_cli/config/store/executor 及其他测试；从 HEAD 恢复两文件。

### NEXT-TOOL-POLICY 精确名单

- [agent_image/app/tools/policy.py](../../agent_image/app/tools/policy.py)：1 文件 / 300 B。未发现 policy 模块/独有常量的外部调用；旧常量兼容模块仍被镜像 COPY；屏蔽候选的最小导入通过，未运行删除后收集/回归，不满足 FLR §4.1，暂列待定

保留 tools/base/enterprise_host 等；从 HEAD 恢复此文件。

### NEXT-SCHEDULER-ALIAS 精确名单

- [src/sandbox/scheduler/exceptions.py](../../src/sandbox/scheduler/exceptions.py)：1 文件 / 330 B。未发现模块外部导入；仅重导出 sandbox.errors，仍为发布兼容接口/镜像 COPY；屏蔽候选的最小导入通过，未运行删除后收集/回归，不满足 FLR §4.1，暂列待定

保留 sandbox.errors 和 DockerSandboxScheduler；从 HEAD 恢复此文件。

### NEXT-HISTORIC-DOCS 精确名单

- [docs/current-state/discussion-items.md](../current-state/discussion-items.md)：1 文件 / 81,161 B。a15b127/7822c44 旧 fuzzer/structured 审计或 summary-delivery-A 清单，不代表当前主链；仍有相互链接与独有历史事实
- [docs/current-state/episode-data-flow.md](../current-state/episode-data-flow.md)：1 文件 / 35,251 B。a15b127/7822c44 旧 fuzzer/structured 审计或 summary-delivery-A 清单，不代表当前主链；仍有相互链接与独有历史事实
- [docs/current-state/project-map.md](../current-state/project-map.md)：1 文件 / 18,549 B。a15b127/7822c44 旧 fuzzer/structured 审计或 summary-delivery-A 清单，不代表当前主链；仍有相互链接与独有历史事实
- [docs/current-state/soc-t01-bridge-capabilities.md](../current-state/soc-t01-bridge-capabilities.md)：1 文件 / 13,316 B。a15b127/7822c44 旧 fuzzer/structured 审计或 summary-delivery-A 清单，不代表当前主链；仍有相互链接与独有历史事实
- [docs/current-state/soc-t01-reuse-boundary.md](../current-state/soc-t01-reuse-boundary.md)：1 文件 / 10,426 B。a15b127/7822c44 旧 fuzzer/structured 审计或 summary-delivery-A 清单，不代表当前主链；仍有相互链接与独有历史事实
- [docs/current-state/soc-t01-summary-delivery-a-inventory.md](../current-state/soc-t01-summary-delivery-a-inventory.md)：1 文件 / 13,018 B。a15b127/7822c44 旧 fuzzer/structured 审计或 summary-delivery-A 清单，不代表当前主链；仍有相互链接与独有历史事实
- [docs/current-state/spec-compliance.md](../current-state/spec-compliance.md)：1 文件 / 71,642 B。a15b127/7822c44 旧 fuzzer/structured 审计或 summary-delivery-A 清单，不代表当前主链；仍有相互链接与独有历史事实
- [docs/current-state/strategy-comparison.md](../current-state/strategy-comparison.md)：1 文件 / 28,869 B。a15b127/7822c44 旧 fuzzer/structured 审计或 summary-delivery-A 清单，不代表当前主链；仍有相互链接与独有历史事实
- [docs/plans/office-workspace-v2-coverage-guided-semantic-mutation-correction-plan.md](../plans/office-workspace-v2-coverage-guided-semantic-mutation-correction-plan.md)：1 文件 / 3,122 B。旧六步/12-family/risk-only/promotion 计划被当前四条硬设计取代；LOG 保留历史决定，未跟踪原文回退与唯一信息尚待确认
- [docs/tasks/20260917-current-code-audit.md](../tasks/20260917-current-code-audit.md)：1 文件 / 24,785 B。旧审计 DONE TASK，承载历史文档验收/证据索引，须先保留短历史索引

保留 LOG/索引/报告证据/当前清理 SPEC/TASK；未跟踪文档必须先有获准回退。

NEXT-CACHE 精确 766 路径在 JSON next_batches[0].paths；每个缓存有 SHA256 和对应源，不授权整个 __pycache__ 目录删除。唯一无源缓存待定。缓存清理能腾空间；减少旧设计上下文主要依靠上面的源码/文档批次。

大族退役尚不准入：structured/fuzzer v2/Office V1/旧 coverage/mutation 必须先决定 CLI、包导出、adapter、冻结资产的下线边界，再解除共享依赖，依据届时新的精确引用清单拟 TASK；不把“迁移后可删”写成当前可删。

## 6. 实际验证与限制

- 第二枚举命令：`rg --files --hidden --no-ignore -g !**/.git/** -g !**/.venv/** -g !**/.deps/** -g !**/node_modules/** -g !**/.upstream-probe/** -g !**/.codex-tmp/** -g !data/** -g !reports/** -g !build/** -g !.tmp/** -g !**/*.egg-info/**`。退出 0；与元数据枚举均为 1458 个第一方文件，集合差 0。
- AST/文本引用分析，无不可读/AST 错误；候选摘要为工作树 SHA256。766 可删缓存全量复核对应源、无显式引用、nlink、非链接，逐项保存摘要。
- .venv/Scripts/python.exe -B - 的内存脚本实际导入 campaign/selector/error-capable adapter/runtime，全部成功；共加载 117 个第一方模块。
- 同命令用内存 MetaPathFinder 屏蔽 4 个源码候选，campaign/selector/adapter/runtime/server 五入口仍成功；没有磁盘删除，只验证初始化。
- rg 在 src/agent_image/scripts/tests/config/controller_image/harness runtime 搜索候选模块/独有 API，匹配只在自身；包初始化、CLI、Dockerfile、Node 相对 imports、动态 loader 另行核对。
- 盘点结束重新检查全部 1458 文件 size/mtime 与已有 SHA256，变化 0 项；TASK/LOG 后续状态写入属于产物，边界为单次快照。
- 两次补充 rg 因不存在 harness/build/Dockerfile 或字面通配路径失败；已改为真实目录重查，没有用失败搜索证明无依赖。
- JSON 汇总/分类/唯一键/边端点/新增链接/diff-check 在提交前核验并写 TASK；自身产物字节单列，不递归计入清单。
- 未运行 pytest、compileall、Ruff、Docker、真实模型、服务器或历史重放。文档/静态任务不声称这些检查新通过；外部调用与构建上下文仍有局限，未知保守分类。

## 7. 非缓存逐文件完整清单

每行 count=1；引用数含文本/历史与宽 COPY，不等于运行入度。JSON references → edges 提供来源/类型/行号；current_dependency_route、test_dependents、history_dependents、replacement、side_effect、rollback 给出细项。缓存见 §8 与 JSON 精确名单。

| 文件 | B | 分类 | 引用数 | 判定依据 |
|---|---:|---|---:|---|
| [.dockerignore](../../.dockerignore) | 1,105 | 禁删 | 0 | 当前发布/容器构建/测试收集入口，尚未批准改写或撤除 |
| [.gitattributes](../../.gitattributes) | 180 | 待定 | 0 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [.gitignore](../../.gitignore) | 525 | 禁删 | 0 | 当前发布/容器构建/测试收集入口，尚未批准改写或撤除 |
| [AGENTS.md](../../AGENTS.md) | 8,896 | 禁删 | 0 | 协作/授权记录或未归档历史证据 |
| [AUTHORIZED-EVALUATION-SCOPE.md](../../AUTHORIZED-EVALUATION-SCOPE.md) | 4,761 | 禁删 | 0 | 协作/授权记录或未归档历史证据 |
| [LOG-INDEX.md](../../LOG-INDEX.md) | 48,857 | 禁删 | 0 | 协作/授权记录或未归档历史证据 |
| [LOG.md](../../LOG.md) | 603,873 | 禁删 | 0 | 协作/授权记录或未归档历史证据 |
| [agent_image/Dockerfile](../../agent_image/Dockerfile) | 1,158 | 禁删 | 0 | 当前发布/容器构建/测试收集入口，尚未批准改写或撤除 |
| [agent_image/Dockerfile.exploratory-source](../../agent_image/Dockerfile.exploratory-source) | 202 | 待定 | 0 | 可选 adapter/镜像/harness 支持入口，未批准下线 |
| [agent_image/Dockerfile.qwen](../../agent_image/Dockerfile.qwen) | 2,373 | 待定 | 0 | 可选 adapter/镜像/harness 支持入口，未批准下线 |
| [agent_image/Dockerfile.qwen-external](../../agent_image/Dockerfile.qwen-external) | 1,080 | 待定 | 0 | 可选 adapter/镜像/harness 支持入口，未批准下线 |
| [agent_image/Dockerfile.qwen-mutator](../../agent_image/Dockerfile.qwen-mutator) | 335 | 待定 | 0 | 可选 adapter/镜像/harness 支持入口，未批准下线 |
| [agent_image/app/__init__.py](../../agent_image/app/__init__.py) | 40 | 禁删 | 233 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/adapter/__init__.py](../../agent_image/app/adapter/__init__.py) | 127 | 禁删 | 51 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/adapter/base.py](../../agent_image/app/adapter/base.py) | 1,317 | 禁删 | 42 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/adapter/deepseek_harness_adapter.py](../../agent_image/app/adapter/deepseek_harness_adapter.py) | 40,096 | 禁删 | 10 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [agent_image/app/adapter/error_capable_adapter.py](../../agent_image/app/adapter/error_capable_adapter.py) | 7,007 | 禁删 | 9 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/adapter/factory.py](../../agent_image/app/adapter/factory.py) | 2,855 | 禁删 | 16 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/adapter/langgraph_react_runtime.py](../../agent_image/app/adapter/langgraph_react_runtime.py) | 72,810 | 禁删 | 24 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [agent_image/app/adapter/structured_v1_adapter.py](../../agent_image/app/adapter/structured_v1_adapter.py) | 7,268 | 禁删 | 10 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [agent_image/app/adapter/trace_react_adapter.py](../../agent_image/app/adapter/trace_react_adapter.py) | 25,439 | 禁删 | 26 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/agent/__init__.py](../../agent_image/app/agent/__init__.py) | 49 | 禁删 | 61 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/agent/fake_react_provider.py](../../agent_image/app/agent/fake_react_provider.py) | 8,594 | 禁删 | 16 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/agent/office_control_provider.py](../../agent_image/app/agent/office_control_provider.py) | 4,362 | 禁删 | 12 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/agent/ollama_react_provider.py](../../agent_image/app/agent/ollama_react_provider.py) | 10,199 | 禁删 | 18 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [agent_image/app/agent/react_contract.py](../../agent_image/app/agent/react_contract.py) | 3,736 | 禁删 | 86 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/agent/workspace_control_provider.py](../../agent_image/app/agent/workspace_control_provider.py) | 2,841 | 禁删 | 8 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/agent_qwen_bootstrap.py](../../agent_image/app/agent_qwen_bootstrap.py) | 11,916 | 待定 | 12 | 可选 adapter/镜像/harness 支持入口，未批准下线 |
| [agent_image/app/error_capable_runtime_surface.py](../../agent_image/app/error_capable_runtime_surface.py) | 7,096 | 禁删 | 11 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/office_mutator_worker.py](../../agent_image/app/office_mutator_worker.py) | 5,763 | 待定 | 6 | 可选 adapter/镜像/harness 支持入口，未批准下线 |
| [agent_image/app/office_v2_mutator_worker.py](../../agent_image/app/office_v2_mutator_worker.py) | 6,727 | 待定 | 10 | 可选 adapter/镜像/harness 支持入口，未批准下线 |
| [agent_image/app/office_v2_runtime_surface.py](../../agent_image/app/office_v2_runtime_surface.py) | 4,805 | 禁删 | 14 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [agent_image/app/office_v2_session.py](../../agent_image/app/office_v2_session.py) | 24,038 | 禁删 | 18 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/protocol.py](../../agent_image/app/protocol.py) | 428 | 禁删 | 54 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/replay/__init__.py](../../agent_image/app/replay/__init__.py) | 53 | 禁删 | 33 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/replay/checkpoint.py](../../agent_image/app/replay/checkpoint.py) | 13,077 | 禁删 | 16 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/replay/checkpoint_observer.py](../../agent_image/app/replay/checkpoint_observer.py) | 1,681 | 禁删 | 10 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/replay/react_decision_recorder.py](../../agent_image/app/replay/react_decision_recorder.py) | 5,197 | 禁删 | 16 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/replay/replay_adapter.py](../../agent_image/app/replay/replay_adapter.py) | 31,603 | 禁删 | 10 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/replay/state_codec.py](../../agent_image/app/replay/state_codec.py) | 4,680 | 禁删 | 22 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/replay/tool_recorder.py](../../agent_image/app/replay/tool_recorder.py) | 9,195 | 禁删 | 16 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/rpc_client.py](../../agent_image/app/rpc_client.py) | 2,241 | 禁删 | 11 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [agent_image/app/runtime.py](../../agent_image/app/runtime.py) | 21,036 | 禁删 | 15 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/server.py](../../agent_image/app/server.py) | 4,092 | 禁删 | 11 | 当前主链/探针/验收或项目执行包装器 |
| [agent_image/app/structured_v1_model_port.py](../../agent_image/app/structured_v1_model_port.py) | 6,096 | 禁删 | 20 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [agent_image/app/structured_v1_runtime_surface.py](../../agent_image/app/structured_v1_runtime_surface.py) | 5,238 | 禁删 | 8 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [agent_image/app/tools/__init__.py](../../agent_image/app/tools/__init__.py) | 144 | 禁删 | 51 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/tools/base.py](../../agent_image/app/tools/base.py) | 9,347 | 禁删 | 64 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/tools/enterprise_host.py](../../agent_image/app/tools/enterprise_host.py) | 2,278 | 禁删 | 8 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/tools/fake_database.py](../../agent_image/app/tools/fake_database.py) | 3,880 | 禁删 | 8 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/tools/fake_filesystem.py](../../agent_image/app/tools/fake_filesystem.py) | 6,035 | 禁删 | 8 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/tools/fake_mailbox.py](../../agent_image/app/tools/fake_mailbox.py) | 1,668 | 禁删 | 8 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/tools/fake_shell.py](../../agent_image/app/tools/fake_shell.py) | 2,572 | 禁删 | 8 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/tools/mock_api.py](../../agent_image/app/tools/mock_api.py) | 1,288 | 禁删 | 8 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/tools/office_episode.py](../../agent_image/app/tools/office_episode.py) | 8,561 | 禁删 | 12 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [agent_image/app/tools/policy.py](../../agent_image/app/tools/policy.py) | 300 | 待定 | 6 | 未发现 policy 模块/独有常量的外部调用；旧常量兼容模块仍被镜像 COPY；屏蔽候选的最小导入通过，未运行删除后收集/回归，不满足 FLR §4.1，暂列待定 |
| [agent_image/app/tools/synthetic_network.py](../../agent_image/app/tools/synthetic_network.py) | 1,952 | 禁删 | 8 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/tools/virtual_vault.py](../../agent_image/app/tools/virtual_vault.py) | 1,515 | 禁删 | 8 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/tools/workspace_scenario.py](../../agent_image/app/tools/workspace_scenario.py) | 7,017 | 禁删 | 16 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/tracing/__init__.py](../../agent_image/app/tracing/__init__.py) | 122 | 禁删 | 12 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/tracing/collector.py](../../agent_image/app/tracing/collector.py) | 1,027 | 禁删 | 13 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/app/tracing/sanitizer.py](../../agent_image/app/tracing/sanitizer.py) | 1,232 | 禁删 | 14 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [agent_image/requirements.agent-qwen.lock](../../agent_image/requirements.agent-qwen.lock) | 4,625 | 待定 | 2 | 本地二进制/归档材料；内容用途未展开，不能断言过时 |
| [agent_image/requirements.langgraph.lock](../../agent_image/requirements.langgraph.lock) | 4,151 | 待定 | 0 | 本地二进制/归档材料；内容用途未展开，不能断言过时 |
| [agent_image/requirements.txt](../../agent_image/requirements.txt) | 50 | 禁删 | 2 | 当前发布/容器构建/测试收集入口，尚未批准改写或撤除 |
| [agent_variants/deepseek_harness/.gitignore](../../agent_variants/deepseek_harness/.gitignore) | 61 | 待定 | 0 | 可选 adapter/镜像/harness 支持入口，未批准下线 |
| [agent_variants/deepseek_harness/Dockerfile.qwen](../../agent_variants/deepseek_harness/Dockerfile.qwen) | 1,855 | 待定 | 0 | 可选 adapter/镜像/harness 支持入口，未批准下线 |
| [agent_variants/deepseek_harness/README.md](../../agent_variants/deepseek_harness/README.md) | 1,064 | 待定 | 0 | 可能为旧设计/现状叙述，但历史引用/唯一决策证据或替代说明尚未证明可丢弃 |
| [agent_variants/deepseek_harness/fixtures/mcp_server.mjs](../../agent_variants/deepseek_harness/fixtures/mcp_server.mjs) | 1,384 | 待定 | 0 | 可选 adapter/镜像/harness 支持入口，未批准下线 |
| [agent_variants/deepseek_harness/fixtures/openai_stub.mjs](../../agent_variants/deepseek_harness/fixtures/openai_stub.mjs) | 4,379 | 待定 | 0 | 可选 adapter/镜像/harness 支持入口，未批准下线 |
| [agent_variants/deepseek_harness/office_v2.cordis.yml](../../agent_variants/deepseek_harness/office_v2.cordis.yml) | 2,416 | 待定 | 2 | 可选 adapter/镜像/harness 支持入口，未批准下线 |
| [agent_variants/deepseek_harness/package-lock.json](../../agent_variants/deepseek_harness/package-lock.json) | 152,763 | 待定 | 4 | 可选 adapter/镜像/harness 支持入口，未批准下线 |
| [agent_variants/deepseek_harness/package.json](../../agent_variants/deepseek_harness/package.json) | 799 | 待定 | 4 | 可选 adapter/镜像/harness 支持入口，未批准下线 |
| [agent_variants/deepseek_harness/runtime/deterministic_model.mjs](../../agent_variants/deepseek_harness/runtime/deterministic_model.mjs) | 19,241 | 待定 | 3 | 可选 adapter/镜像/harness 支持入口，未批准下线 |
| [agent_variants/deepseek_harness/runtime/driver.mjs](../../agent_variants/deepseek_harness/runtime/driver.mjs) | 11,417 | 待定 | 4 | 可选 adapter/镜像/harness 支持入口，未批准下线 |
| [agent_variants/deepseek_harness/runtime/mcp_launcher.mjs](../../agent_variants/deepseek_harness/runtime/mcp_launcher.mjs) | 730 | 待定 | 2 | 可选 adapter/镜像/harness 支持入口，未批准下线 |
| [agent_variants/deepseek_harness/runtime/model_runtime.mjs](../../agent_variants/deepseek_harness/runtime/model_runtime.mjs) | 11,238 | 待定 | 4 | 可选 adapter/镜像/harness 支持入口，未批准下线 |
| [agent_variants/deepseek_harness/runtime/model_runtime_contract_test.mjs](../../agent_variants/deepseek_harness/runtime/model_runtime_contract_test.mjs) | 4,252 | 待定 | 2 | 可选 adapter/镜像/harness 支持入口，未批准下线 |
| [agent_variants/deepseek_harness/runtime/office_bridge.py](../../agent_variants/deepseek_harness/runtime/office_bridge.py) | 26,741 | 待定 | 3 | 可选 adapter/镜像/harness 支持入口，未批准下线 |
| [chainfuzzer.pdf](../../chainfuzzer.pdf) | 18,328,972 | 待定 | 0 | 本地二进制/归档材料；内容用途未展开，不能断言过时 |
| [config/risk-taxonomy.yaml](../../config/risk-taxonomy.yaml) | 5,296 | 待定 | 12 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [controller_image/Dockerfile](../../controller_image/Dockerfile) | 454 | 禁删 | 0 | 当前发布/容器构建/测试收集入口，尚未批准改写或撤除 |
| [controller_image/requirements.txt](../../controller_image/requirements.txt) | 147 | 禁删 | 2 | 当前发布/容器构建/测试收集入口，尚未批准改写或撤除 |
| [docs/current-state/discussion-items.md](../current-state/discussion-items.md) | 81,161 | 待定 | 5 | a15b127/7822c44 旧 fuzzer/structured 审计或 summary-delivery-A 清单，不代表当前主链；仍有相互链接与独有历史事实 |
| [docs/current-state/episode-data-flow.md](../current-state/episode-data-flow.md) | 35,251 | 待定 | 4 | a15b127/7822c44 旧 fuzzer/structured 审计或 summary-delivery-A 清单，不代表当前主链；仍有相互链接与独有历史事实 |
| [docs/current-state/project-map.md](../current-state/project-map.md) | 18,549 | 待定 | 4 | a15b127/7822c44 旧 fuzzer/structured 审计或 summary-delivery-A 清单，不代表当前主链；仍有相互链接与独有历史事实 |
| [docs/current-state/soc-t01-bridge-capabilities.md](../current-state/soc-t01-bridge-capabilities.md) | 13,316 | 待定 | 0 | a15b127/7822c44 旧 fuzzer/structured 审计或 summary-delivery-A 清单，不代表当前主链；仍有相互链接与独有历史事实 |
| [docs/current-state/soc-t01-reuse-boundary.md](../current-state/soc-t01-reuse-boundary.md) | 10,426 | 待定 | 0 | a15b127/7822c44 旧 fuzzer/structured 审计或 summary-delivery-A 清单，不代表当前主链；仍有相互链接与独有历史事实 |
| [docs/current-state/soc-t01-summary-delivery-a-inventory.md](../current-state/soc-t01-summary-delivery-a-inventory.md) | 13,018 | 待定 | 0 | a15b127/7822c44 旧 fuzzer/structured 审计或 summary-delivery-A 清单，不代表当前主链；仍有相互链接与独有历史事实 |
| [docs/current-state/spec-compliance.md](../current-state/spec-compliance.md) | 71,642 | 待定 | 4 | a15b127/7822c44 旧 fuzzer/structured 审计或 summary-delivery-A 清单，不代表当前主链；仍有相互链接与独有历史事实 |
| [docs/current-state/strategy-comparison.md](../current-state/strategy-comparison.md) | 28,869 | 待定 | 4 | a15b127/7822c44 旧 fuzzer/structured 审计或 summary-delivery-A 清单，不代表当前主链；仍有相互链接与独有历史事实 |
| [docs/plans/office-workspace-v2-coverage-guided-semantic-mutation-correction-plan.md](../plans/office-workspace-v2-coverage-guided-semantic-mutation-correction-plan.md) | 3,122 | 待定 | 0 | 旧六步/12-family/risk-only/promotion 计划被当前四条硬设计取代；LOG 保留历史决定，未跟踪原文回退与唯一信息尚待确认 |
| [docs/reports/20260922-pre-structured-task-project-and-15x2-data.json](20260922-pre-structured-task-project-and-15x2-data.json) | 1,762,036 | 禁删 | 2 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260922-pre-structured-task-project-and-15x2.md](20260922-pre-structured-task-project-and-15x2.md) | 187,943 | 禁删 | 2 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260923-g1-effective-variation-analysis.json](20260923-g1-effective-variation-analysis.json) | 42,340 | 禁删 | 3 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260923-g1-effective-variation-analysis.md](20260923-g1-effective-variation-analysis.md) | 7,442 | 禁删 | 1 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260923-g1-effective-variation-analysis.py](20260923-g1-effective-variation-analysis.py) | 9,049 | 禁删 | 2 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260923-g1-exposure-timing-analysis.json](20260923-g1-exposure-timing-analysis.json) | 13,624 | 禁删 | 0 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260923-material-version-preview.md](20260923-material-version-preview.md) | 3,889 | 禁删 | 0 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260923-preflight-gate-review.json](20260923-preflight-gate-review.json) | 33,293 | 禁删 | 3 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260923-preflight-gate-review.md](20260923-preflight-gate-review.md) | 8,733 | 禁删 | 1 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260923-preflight-gate-review.py](20260923-preflight-gate-review.py) | 11,418 | 禁删 | 4 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260925-dev-precheck-c-review.json](20260925-dev-precheck-c-review.json) | 133,668 | 禁删 | 1 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260925-dev-precheck-c-review.md](20260925-dev-precheck-c-review.md) | 11,990 | 禁删 | 0 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260925-dev-precheck-c-review.py](20260925-dev-precheck-c-review.py) | 20,462 | 禁删 | 2 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260925-mat-d-precheck-review.json](20260925-mat-d-precheck-review.json) | 95,105 | 禁删 | 1 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260925-mat-d-precheck-review.py](20260925-mat-d-precheck-review.py) | 18,335 | 禁删 | 2 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260925-mat-d-precheck-verdict.md](20260925-mat-d-precheck-verdict.md) | 8,895 | 禁删 | 1 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260925-summary-delivery-e-integrity.json](20260925-summary-delivery-e-integrity.json) | 889 | 禁删 | 2 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260925-summary-delivery-e-local.md](20260925-summary-delivery-e-local.md) | 5,681 | 禁删 | 2 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260926-attack-technique-diversity-partial.md](20260926-attack-technique-diversity-partial.md) | 7,741 | 禁删 | 2 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260926-attack-technique-diversity-partial.py](20260926-attack-technique-diversity-partial.py) | 10,585 | 禁删 | 6 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260926-coverage-guidance-recovery-audit.md](20260926-coverage-guidance-recovery-audit.md) | 5,003 | 禁删 | 0 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260926-gen-a-why-no-violation.py](20260926-gen-a-why-no-violation.py) | 4,557 | 禁删 | 1 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260926-obligation-outcome-recheck.py](20260926-obligation-outcome-recheck.py) | 3,117 | 禁删 | 9 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260926-summary-delivery-completion-diagnostic.md](20260926-summary-delivery-completion-diagnostic.md) | 8,044 | 禁删 | 2 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260926-summary-delivery-e2-local.md](20260926-summary-delivery-e2-local.md) | 5,312 | 禁删 | 0 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260926-summary-delivery-e2-precheck.md](20260926-summary-delivery-e2-precheck.md) | 10,427 | 禁删 | 3 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260926-summary-delivery-e2-precheck.py](20260926-summary-delivery-e2-precheck.py) | 8,209 | 禁删 | 4 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260926-summary-delivery-e2-why.py](20260926-summary-delivery-e2-why.py) | 3,996 | 禁删 | 3 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260926-summary-delivery-report-correction.md](20260926-summary-delivery-report-correction.md) | 6,013 | 禁删 | 3 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260927-fdm-run-stopped.md](20260927-fdm-run-stopped.md) | 7,256 | 禁删 | 2 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260927-feedback-directed-mutation-local.md](20260927-feedback-directed-mutation-local.md) | 5,170 | 禁删 | 2 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260929-guidance-yield-diagnostics.md](20260929-guidance-yield-diagnostics.md) | 11,713 | 禁删 | 0 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260930-error-capable-admission.json](20260930-error-capable-admission.json) | 8,574 | 禁删 | 1 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260930-error-capable-admission.md](20260930-error-capable-admission.md) | 2,028 | 禁删 | 0 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260930-range-calibration.json](20260930-range-calibration.json) | 31,464 | 禁删 | 2 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260930-range-calibration.md](20260930-range-calibration.md) | 13,882 | 禁删 | 1 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260930-zero-information-episodes-readout.md](20260930-zero-information-episodes-readout.md) | 6,785 | 禁删 | 0 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20260930-zero-information-episodes-readout.py](20260930-zero-information-episodes-readout.py) | 4,505 | 禁删 | 2 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20261004-mechanism-separation-comparison.md](20261004-mechanism-separation-comparison.md) | 5,779 | 禁删 | 0 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20261004-multipath-local-validation.md](20261004-multipath-local-validation.md) | 6,982 | 禁删 | 1 | 协作/授权记录或未归档历史证据 |
| [docs/reports/20261006-neighborhood-priority-local-validation.md](20261006-neighborhood-priority-local-validation.md) | 9,716 | 禁删 | 0 | 协作/授权记录或未归档历史证据 |
| [docs/reports/README.md](README.md) | 2,414 | 禁删 | 0 | 协作/授权记录或未归档历史证据 |
| [docs/reports/__pycache__/20260923-g1-effective-variation-analysis.cpython-312.pyc](__pycache__/20260923-g1-effective-variation-analysis.cpython-312.pyc) | 12,344 | 禁删 | 0 | 协作/授权记录或未归档历史证据 |
| [docs/reports/__pycache__/20260923-preflight-gate-review.cpython-312.pyc](__pycache__/20260923-preflight-gate-review.cpython-312.pyc) | 17,410 | 禁删 | 0 | 协作/授权记录或未归档历史证据 |
| [docs/reports/__pycache__/20260925-dev-precheck-c-review.cpython-312.pyc](__pycache__/20260925-dev-precheck-c-review.cpython-312.pyc) | 25,175 | 禁删 | 0 | 协作/授权记录或未归档历史证据 |
| [docs/reports/__pycache__/20260925-mat-d-precheck-review.cpython-312.pyc](__pycache__/20260925-mat-d-precheck-review.cpython-312.pyc) | 26,269 | 禁删 | 0 | 协作/授权记录或未归档历史证据 |
| [docs/reports/__pycache__/20260926-obligation-outcome-recheck.cpython-312.pyc](__pycache__/20260926-obligation-outcome-recheck.cpython-312.pyc) | 4,447 | 禁删 | 0 | 协作/授权记录或未归档历史证据 |
| [docs/reports/__pycache__/20260926-summary-delivery-e2-precheck.cpython-312.pyc](__pycache__/20260926-summary-delivery-e2-precheck.cpython-312.pyc) | 12,579 | 禁删 | 0 | 协作/授权记录或未归档历史证据 |
| [docs/reports/__pycache__/20260926-summary-delivery-e2-why.cpython-312.pyc](__pycache__/20260926-summary-delivery-e2-why.cpython-312.pyc) | 5,871 | 禁删 | 0 | 协作/授权记录或未归档历史证据 |
| [docs/specs/20261010-full-legacy-retirement.md](../specs/20261010-full-legacy-retirement.md) | 8,969 | 禁删 | 5 | 当前已批准清理契约/验收记录 |
| [docs/specs/20261010-legacy-code-retirement.md](../specs/20261010-legacy-code-retirement.md) | 8,439 | 禁删 | 5 | 当前已批准清理契约/验收记录 |
| [docs/tasks/20260917-current-code-audit.md](../tasks/20260917-current-code-audit.md) | 24,785 | 待定 | 0 | 旧审计 DONE TASK，承载历史文档验收/证据索引，须先保留短历史索引 |
| [docs/tasks/20261010-full-legacy-retirement-task.md](../tasks/20261010-full-legacy-retirement-task.md) | 15,258 | 禁删 | 2 | 当前已批准清理契约/验收记录 |
| [docs/tasks/20261010-legacy-code-retirement-task.md](../tasks/20261010-legacy-code-retirement-task.md) | 10,452 | 禁删 | 2 | 当前已批准清理契约/验收记录 |
| [harness-node-modules.tar.gz](../../harness-node-modules.tar.gz) | 18,755,436 | 待定 | 0 | 本地二进制/归档材料；内容用途未展开，不能断言过时 |
| [pyproject.toml](../../pyproject.toml) | 1,467 | 禁删 | 0 | 当前发布/容器构建/测试收集入口，尚未批准改写或撤除 |
| [scripts/analyze_error_capable_mechanisms.py](../../scripts/analyze_error_capable_mechanisms.py) | 9,944 | 禁删 | 2 | 当前主链/探针/验收或项目执行包装器 |
| [scripts/build_office_v2_world.py](../../scripts/build_office_v2_world.py) | 32,460 | 待定 | 2 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [scripts/build_structured_v1_freeze.py](../../scripts/build_structured_v1_freeze.py) | 4,320 | 待定 | 1 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [scripts/check_summary_delivery_e_history.py](../../scripts/check_summary_delivery_e_history.py) | 2,992 | 待定 | 1 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [scripts/compute_admission.py](../../scripts/compute_admission.py) | 15,455 | 待定 | 1 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [scripts/diagnose_guidance_yield.py](../../scripts/diagnose_guidance_yield.py) | 13,301 | 待定 | 5 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [scripts/dry_run_structured_arms.py](../../scripts/dry_run_structured_arms.py) | 24,006 | 待定 | 4 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [scripts/local_python.cmd](../../scripts/local_python.cmd) | 153 | 待定 | 1 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [scripts/probe_error_capable_agent.py](../../scripts/probe_error_capable_agent.py) | 9,942 | 禁删 | 5 | 当前主链/探针/验收或项目执行包装器 |
| [scripts/probe_error_capable_campaign.py](../../scripts/probe_error_capable_campaign.py) | 8,763 | 禁删 | 3 | 当前主链/探针/验收或项目执行包装器 |
| [scripts/probe_error_capable_container_roundtrip.py](../../scripts/probe_error_capable_container_roundtrip.py) | 12,135 | 禁删 | 1 | 当前主链/探针/验收或项目执行包装器 |
| [scripts/probe_error_capable_mechanisms.py](../../scripts/probe_error_capable_mechanisms.py) | 8,836 | 禁删 | 3 | 当前主链/探针/验收或项目执行包装器 |
| [scripts/probe_error_capable_recovery.py](../../scripts/probe_error_capable_recovery.py) | 15,001 | 禁删 | 1 | 当前主链/探针/验收或项目执行包装器 |
| [scripts/probe_error_capable_scenario.py](../../scripts/probe_error_capable_scenario.py) | 10,362 | 禁删 | 2 | 当前主链/探针/验收或项目执行包装器 |
| [scripts/probe_selector_feedback_use.py](../../scripts/probe_selector_feedback_use.py) | 14,311 | 禁删 | 3 | 当前主链/探针/验收或项目执行包装器 |
| [scripts/project_pytest.cmd](../../scripts/project_pytest.cmd) | 328 | 禁删 | 12 | 当前主链/探针/验收或项目执行包装器 |
| [scripts/project_python.cmd](../../scripts/project_python.cmd) | 1,529 | 禁删 | 19 | 当前主链/探针/验收或项目执行包装器 |
| [scripts/project_ruff.cmd](../../scripts/project_ruff.cmd) | 461 | 禁删 | 13 | 当前主链/探针/验收或项目执行包装器 |
| [scripts/range_calibration_probe.py](../../scripts/range_calibration_probe.py) | 33,730 | 待定 | 7 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [scripts/report_neighborhood_campaign.py](../../scripts/report_neighborhood_campaign.py) | 14,638 | 禁删 | 2 | 当前主链/探针/验收或项目执行包装器 |
| [scripts/report_structured_campaigns.py](../../scripts/report_structured_campaigns.py) | 2,276 | 待定 | 1 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [scripts/report_summary_delivery_e.py](../../scripts/report_summary_delivery_e.py) | 9,163 | 待定 | 4 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [scripts/report_summary_delivery_e2.py](../../scripts/report_summary_delivery_e2.py) | 40,350 | 待定 | 24 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [scripts/run_error_capable_resumable.py](../../scripts/run_error_capable_resumable.py) | 7,785 | 禁删 | 1 | 当前主链/探针/验收或项目执行包装器 |
| [scripts/run_neighborhood_campaign.py](../../scripts/run_neighborhood_campaign.py) | 24,786 | 禁删 | 9 | 当前主链/探针/验收或项目执行包装器 |
| [scripts/run_paired_campaign.sh](../../scripts/run_paired_campaign.sh) | 4,511 | 待定 | 3 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [scripts/run_structured_v1_episode.py](../../scripts/run_structured_v1_episode.py) | 23,538 | 待定 | 15 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [scripts/run_summary_delivery_e.sh](../../scripts/run_summary_delivery_e.sh) | 2,777 | 待定 | 1 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [scripts/smoke_attack_prompt.py](../../scripts/smoke_attack_prompt.py) | 4,352 | 待定 | 3 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/__init__.py](../../src/sandbox/__init__.py) | 357 | 禁删 | 3213 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/agent_prompts.py](../../src/sandbox/agent_prompts.py) | 7,583 | 禁删 | 33 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/cli.py](../../src/sandbox/cli.py) | 10,425 | 待定 | 11 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/client/__init__.py](../../src/sandbox/client/__init__.py) | 118 | 禁删 | 27 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/client/artifact_transfer.py](../../src/sandbox/client/artifact_transfer.py) | 7,801 | 待定 | 14 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/client/jsonrpc.py](../../src/sandbox/client/jsonrpc.py) | 1,530 | 禁删 | 10 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/client/runtime_client.py](../../src/sandbox/client/runtime_client.py) | 13,069 | 禁删 | 31 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/config.py](../../src/sandbox/config.py) | 3,271 | 禁删 | 43 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/content_digests.py](../../src/sandbox/content_digests.py) | 1,705 | 禁删 | 10 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/coverage/__init__.py](../../src/sandbox/coverage/__init__.py) | 7,488 | 禁删 | 149 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/coverage/behavior.py](../../src/sandbox/coverage/behavior.py) | 14,347 | 禁删 | 15 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/coverage/correlation.py](../../src/sandbox/coverage/correlation.py) | 4,348 | 禁删 | 10 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/coverage/events.py](../../src/sandbox/coverage/events.py) | 3,102 | 禁删 | 16 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/coverage/evidence.py](../../src/sandbox/coverage/evidence.py) | 889 | 禁删 | 12 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/coverage/exceptions.py](../../src/sandbox/coverage/exceptions.py) | 451 | 禁删 | 24 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/coverage/feature_normalizer.py](../../src/sandbox/coverage/feature_normalizer.py) | 3,747 | 禁删 | 12 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/coverage/feedback.py](../../src/sandbox/coverage/feedback.py) | 18,081 | 禁删 | 10 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/coverage/heatmap.py](../../src/sandbox/coverage/heatmap.py) | 4,949 | 禁删 | 10 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/coverage/input.py](../../src/sandbox/coverage/input.py) | 15,847 | 禁删 | 19 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/coverage/models.py](../../src/sandbox/coverage/models.py) | 28,471 | 禁删 | 60 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/coverage/office_evidence.py](../../src/sandbox/coverage/office_evidence.py) | 16,687 | 禁删 | 8 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/coverage/office_risk.py](../../src/sandbox/coverage/office_risk.py) | 21,923 | 禁删 | 14 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/coverage/risk.py](../../src/sandbox/coverage/risk.py) | 12,796 | 待定 | 8 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/coverage/risk_scope.py](../../src/sandbox/coverage/risk_scope.py) | 2,914 | 禁删 | 18 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/coverage/store.py](../../src/sandbox/coverage/store.py) | 34,838 | 待定 | 10 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/coverage/taxonomy.py](../../src/sandbox/coverage/taxonomy.py) | 5,674 | 禁删 | 22 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/coverage/v2_behavior.py](../../src/sandbox/coverage/v2_behavior.py) | 16,483 | 禁删 | 17 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/coverage/v2_contracts.py](../../src/sandbox/coverage/v2_contracts.py) | 17,303 | 禁删 | 27 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/coverage/v2_episode_behavior.py](../../src/sandbox/coverage/v2_episode_behavior.py) | 9,998 | 禁删 | 9 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/coverage/v2_episode_coverage.py](../../src/sandbox/coverage/v2_episode_coverage.py) | 29,034 | 禁删 | 24 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/coverage/v2_input.py](../../src/sandbox/coverage/v2_input.py) | 30,700 | 禁删 | 34 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/coverage/v2_risk_catalog.py](../../src/sandbox/coverage/v2_risk_catalog.py) | 9,803 | 禁删 | 12 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/coverage/v2_risk_coverage.py](../../src/sandbox/coverage/v2_risk_coverage.py) | 14,441 | 禁删 | 11 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/coverage/v2_tool_behavior.py](../../src/sandbox/coverage/v2_tool_behavior.py) | 17,539 | 禁删 | 11 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/coverage/v2_unexpected_risk.py](../../src/sandbox/coverage/v2_unexpected_risk.py) | 8,441 | 禁删 | 11 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/engine/__init__.py](../../src/sandbox/engine/__init__.py) | 257 | 禁删 | 21 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/engine/case_source.py](../../src/sandbox/engine/case_source.py) | 7,990 | 禁删 | 20 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/engine/execution_engine.py](../../src/sandbox/engine/execution_engine.py) | 6,430 | 禁删 | 10 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/engine/models.py](../../src/sandbox/engine/models.py) | 1,352 | 禁删 | 10 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/errors.py](../../src/sandbox/errors.py) | 1,360 | 禁删 | 38 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/fuzzer/__init__.py](../../src/sandbox/fuzzer/__init__.py) | 55 | 禁删 | 345 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/fuzzer/circuit_breaker.py](../../src/sandbox/fuzzer/circuit_breaker.py) | 1,022 | 待定 | 8 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/config.py](../../src/sandbox/fuzzer/config.py) | 6,434 | 待定 | 14 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/corpus.py](../../src/sandbox/fuzzer/corpus.py) | 5,510 | 待定 | 8 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/energy.py](../../src/sandbox/fuzzer/energy.py) | 3,593 | 待定 | 8 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/engine.py](../../src/sandbox/fuzzer/engine.py) | 38,394 | 待定 | 6 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/exceptions.py](../../src/sandbox/fuzzer/exceptions.py) | 438 | 待定 | 16 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/executor.py](../../src/sandbox/fuzzer/executor.py) | 7,115 | 待定 | 10 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/failure_policy.py](../../src/sandbox/fuzzer/failure_policy.py) | 6,301 | 待定 | 12 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/metrics.py](../../src/sandbox/fuzzer/metrics.py) | 3,777 | 待定 | 8 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/models.py](../../src/sandbox/fuzzer/models.py) | 14,204 | 禁删 | 50 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/fuzzer/queue.py](../../src/sandbox/fuzzer/queue.py) | 1,828 | 待定 | 8 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/recovery.py](../../src/sandbox/fuzzer/recovery.py) | 5,196 | 待定 | 10 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/resources.py](../../src/sandbox/fuzzer/resources.py) | 1,592 | 待定 | 6 | 未发现模块、docker_memory_limit_bytes/validate_host_capacity 外部引用；旧容量逻辑仍被 setuptools 包发现/镜像 COPY；屏蔽候选的最小导入通过，未运行删除后收集/回归，不满足 FLR §4.1，暂列待定 |
| [src/sandbox/fuzzer/seed_pool.py](../../src/sandbox/fuzzer/seed_pool.py) | 7,220 | 待定 | 8 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/semantic_alignment.py](../../src/sandbox/fuzzer/semantic_alignment.py) | 2,041 | 待定 | 8 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/soak.py](../../src/sandbox/fuzzer/soak.py) | 10,177 | 待定 | 6 | 未发现模块、SoakProbeRegistry/SoakRunner 外部调用；旧 soak 路线仍被 setuptools 包发现/镜像 COPY；屏蔽候选的最小导入通过，未运行删除后收集/回归，不满足 FLR §4.1，暂列待定 |
| [src/sandbox/fuzzer/store.py](../../src/sandbox/fuzzer/store.py) | 43,784 | 待定 | 16 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/v2_agent_behavior.py](../../src/sandbox/fuzzer/v2_agent_behavior.py) | 13,930 | 待定 | 24 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/v2_bootstrap.py](../../src/sandbox/fuzzer/v2_bootstrap.py) | 6,548 | 待定 | 38 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/v2_campaign.py](../../src/sandbox/fuzzer/v2_campaign.py) | 5,725 | 待定 | 15 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/v2_campaign_loop.py](../../src/sandbox/fuzzer/v2_campaign_loop.py) | 18,327 | 待定 | 19 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/v2_campaign_state.py](../../src/sandbox/fuzzer/v2_campaign_state.py) | 19,740 | 待定 | 27 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/v2_campaign_store.py](../../src/sandbox/fuzzer/v2_campaign_store.py) | 88,942 | 待定 | 48 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/v2_cli.py](../../src/sandbox/fuzzer/v2_cli.py) | 16,697 | 待定 | 23 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/v2_comparison_report.py](../../src/sandbox/fuzzer/v2_comparison_report.py) | 34,085 | 待定 | 18 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/v2_corpus.py](../../src/sandbox/fuzzer/v2_corpus.py) | 30,289 | 待定 | 30 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/v2_effectiveness.py](../../src/sandbox/fuzzer/v2_effectiveness.py) | 12,500 | 待定 | 14 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/v2_effectiveness_protocol.py](../../src/sandbox/fuzzer/v2_effectiveness_protocol.py) | 14,759 | 待定 | 15 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/v2_effectiveness_report.py](../../src/sandbox/fuzzer/v2_effectiveness_report.py) | 16,885 | 待定 | 9 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/v2_feedback.py](../../src/sandbox/fuzzer/v2_feedback.py) | 14,953 | 待定 | 13 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/v2_loop_contracts.py](../../src/sandbox/fuzzer/v2_loop_contracts.py) | 18,675 | 待定 | 12 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/v2_orchestrator.py](../../src/sandbox/fuzzer/v2_orchestrator.py) | 6,840 | 待定 | 30 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/v2_preflight.py](../../src/sandbox/fuzzer/v2_preflight.py) | 2,797 | 待定 | 7 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/v2_promotion.py](../../src/sandbox/fuzzer/v2_promotion.py) | 9,050 | 待定 | 16 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/v2_real_episode.py](../../src/sandbox/fuzzer/v2_real_episode.py) | 14,268 | 待定 | 28 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/v2_real_runtime.py](../../src/sandbox/fuzzer/v2_real_runtime.py) | 59,867 | 待定 | 68 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/v2_report.py](../../src/sandbox/fuzzer/v2_report.py) | 13,744 | 待定 | 16 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/v2_risk_pool_scheduler.py](../../src/sandbox/fuzzer/v2_risk_pool_scheduler.py) | 11,982 | 待定 | 21 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/v2_runtime.py](../../src/sandbox/fuzzer/v2_runtime.py) | 17,185 | 待定 | 18 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/v2_scheduler.py](../../src/sandbox/fuzzer/v2_scheduler.py) | 9,631 | 待定 | 17 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/v2_scoring.py](../../src/sandbox/fuzzer/v2_scoring.py) | 20,705 | 待定 | 15 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/v2_seed_pools.py](../../src/sandbox/fuzzer/v2_seed_pools.py) | 18,723 | 待定 | 45 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/v2_selection.py](../../src/sandbox/fuzzer/v2_selection.py) | 8,710 | 待定 | 21 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/v2_strategy.py](../../src/sandbox/fuzzer/v2_strategy.py) | 282 | 待定 | 37 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/v2_target_judge.py](../../src/sandbox/fuzzer/v2_target_judge.py) | 11,430 | 待定 | 9 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/v2_target_oracle.py](../../src/sandbox/fuzzer/v2_target_oracle.py) | 46,875 | 待定 | 34 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/v2_target_preservation.py](../../src/sandbox/fuzzer/v2_target_preservation.py) | 5,141 | 待定 | 16 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/fuzzer/v2_work.py](../../src/sandbox/fuzzer/v2_work.py) | 9,315 | 待定 | 18 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/identifiers.py](../../src/sandbox/identifiers.py) | 634 | 禁删 | 14 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/models.py](../../src/sandbox/models.py) | 1,751 | 禁删 | 45 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/mutation/__init__.py](../../src/sandbox/mutation/__init__.py) | 318 | 待定 | 115 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/mutation/alignment.py](../../src/sandbox/mutation/alignment.py) | 2,156 | 待定 | 8 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/mutation/diversity.py](../../src/sandbox/mutation/diversity.py) | 3,512 | 待定 | 8 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/mutation/exceptions.py](../../src/sandbox/mutation/exceptions.py) | 1,524 | 待定 | 24 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/mutation/feedback.py](../../src/sandbox/mutation/feedback.py) | 4,876 | 待定 | 8 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/mutation/handoff.py](../../src/sandbox/mutation/handoff.py) | 1,927 | 待定 | 8 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/mutation/models.py](../../src/sandbox/mutation/models.py) | 12,995 | 待定 | 38 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/mutation/mutator.py](../../src/sandbox/mutation/mutator.py) | 26,503 | 待定 | 8 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/mutation/normalizer.py](../../src/sandbox/mutation/normalizer.py) | 1,856 | 待定 | 18 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/mutation/operators.py](../../src/sandbox/mutation/operators.py) | 3,805 | 待定 | 12 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/mutation/planner.py](../../src/sandbox/mutation/planner.py) | 4,576 | 待定 | 8 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/mutation/priority.py](../../src/sandbox/mutation/priority.py) | 3,534 | 待定 | 8 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/mutation/providers/__init__.py](../../src/sandbox/mutation/providers/__init__.py) | 154 | 待定 | 8 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/mutation/providers/base.py](../../src/sandbox/mutation/providers/base.py) | 565 | 待定 | 8 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/mutation/providers/rule_based.py](../../src/sandbox/mutation/providers/rule_based.py) | 5,070 | 待定 | 8 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/mutation/similarity.py](../../src/sandbox/mutation/similarity.py) | 3,776 | 待定 | 8 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/mutation/store.py](../../src/sandbox/mutation/store.py) | 16,295 | 待定 | 12 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/mutation/v2_brief.py](../../src/sandbox/mutation/v2_brief.py) | 5,863 | 待定 | 18 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/mutation/v2_candidate.py](../../src/sandbox/mutation/v2_candidate.py) | 5,444 | 待定 | 17 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/mutation/v2_contracts.py](../../src/sandbox/mutation/v2_contracts.py) | 12,738 | 待定 | 20 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/mutation/v2_docker.py](../../src/sandbox/mutation/v2_docker.py) | 11,408 | 待定 | 8 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/mutation/v2_materializer.py](../../src/sandbox/mutation/v2_materializer.py) | 5,228 | 待定 | 9 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/mutation/v2_plan_builder.py](../../src/sandbox/mutation/v2_plan_builder.py) | 5,914 | 待定 | 8 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/mutation/v2_policy.py](../../src/sandbox/mutation/v2_policy.py) | 23,625 | 待定 | 16 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/mutation/v2_preparation.py](../../src/sandbox/mutation/v2_preparation.py) | 9,592 | 待定 | 12 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/mutation/v2_provider.py](../../src/sandbox/mutation/v2_provider.py) | 8,154 | 待定 | 26 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/mutation/v2_validation.py](../../src/sandbox/mutation/v2_validation.py) | 5,713 | 待定 | 8 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/ollama_schema.py](../../src/sandbox/ollama_schema.py) | 857 | 待定 | 11 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/protocol.py](../../src/sandbox/protocol.py) | 23,118 | 禁删 | 128 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/replay/__init__.py](../../src/sandbox/replay/__init__.py) | 423 | 禁删 | 343 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/replay/artifact_store.py](../../src/sandbox/replay/artifact_store.py) | 3,348 | 禁删 | 34 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/replay/audit_store.py](../../src/sandbox/replay/audit_store.py) | 1,079 | 待定 | 8 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/replay/canonical.py](../../src/sandbox/replay/canonical.py) | 2,670 | 禁删 | 38 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/replay/comparator.py](../../src/sandbox/replay/comparator.py) | 2,733 | 待定 | 10 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/replay/decision_input.py](../../src/sandbox/replay/decision_input.py) | 8,144 | 待定 | 10 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/replay/digests.py](../../src/sandbox/replay/digests.py) | 377 | 禁删 | 458 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/replay/exceptions.py](../../src/sandbox/replay/exceptions.py) | 649 | 禁删 | 50 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/replay/manifest.py](../../src/sandbox/replay/manifest.py) | 5,230 | 禁删 | 30 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/replay/models.py](../../src/sandbox/replay/models.py) | 11,390 | 禁删 | 79 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/replay/normalizer.py](../../src/sandbox/replay/normalizer.py) | 1,785 | 待定 | 16 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/replay/replay_engine.py](../../src/sandbox/replay/replay_engine.py) | 38,656 | 待定 | 12 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/runtime_resources.py](../../src/sandbox/runtime_resources.py) | 2,101 | 待定 | 8 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/scenarios/__init__.py](../../src/sandbox/scenarios/__init__.py) | 7,068 | 禁删 | 1155 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/candidate_generation.py](../../src/sandbox/scenarios/candidate_generation.py) | 15,292 | 禁删 | 14 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/catalogs.py](../../src/sandbox/scenarios/catalogs.py) | 3,677 | 禁删 | 16 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/error_capable.py](../../src/sandbox/scenarios/error_capable.py) | 74,986 | 禁删 | 100 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/error_capable_agent.py](../../src/sandbox/scenarios/error_capable_agent.py) | 65,451 | 禁删 | 51 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/error_capable_artifacts.py](../../src/sandbox/scenarios/error_capable_artifacts.py) | 3,213 | 禁删 | 8 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/error_capable_bridge.py](../../src/sandbox/scenarios/error_capable_bridge.py) | 42,874 | 禁删 | 16 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/error_capable_campaign.py](../../src/sandbox/scenarios/error_capable_campaign.py) | 96,577 | 禁删 | 23 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/error_capable_committed.py](../../src/sandbox/scenarios/error_capable_committed.py) | 15,117 | 禁删 | 14 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/error_capable_coverage.py](../../src/sandbox/scenarios/error_capable_coverage.py) | 59,608 | 禁删 | 28 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/error_capable_executor.py](../../src/sandbox/scenarios/error_capable_executor.py) | 8,564 | 禁删 | 13 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/error_capable_identity.py](../../src/sandbox/scenarios/error_capable_identity.py) | 3,824 | 禁删 | 48 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/error_capable_journal.py](../../src/sandbox/scenarios/error_capable_journal.py) | 11,612 | 禁删 | 26 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/error_capable_legacy_fixture.json](../../src/sandbox/scenarios/error_capable_legacy_fixture.json) | 1,913 | 待定 | 7 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/scenarios/error_capable_local.py](../../src/sandbox/scenarios/error_capable_local.py) | 17,996 | 禁删 | 12 | 当前主链/探针/验收或项目执行包装器 |
| [src/sandbox/scenarios/error_capable_priority.py](../../src/sandbox/scenarios/error_capable_priority.py) | 28,128 | 禁删 | 17 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/error_capable_registry.py](../../src/sandbox/scenarios/error_capable_registry.py) | 11,650 | 禁删 | 50 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/error_capable_runner.py](../../src/sandbox/scenarios/error_capable_runner.py) | 13,636 | 禁删 | 10 | 当前主链/探针/验收或项目执行包装器 |
| [src/sandbox/scenarios/error_capable_selector.py](../../src/sandbox/scenarios/error_capable_selector.py) | 34,480 | 禁删 | 20 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/error_capable_submission.py](../../src/sandbox/scenarios/error_capable_submission.py) | 7,207 | 禁删 | 16 | 当前主链/探针/验收或项目执行包装器 |
| [src/sandbox/scenarios/error_capable_transport.py](../../src/sandbox/scenarios/error_capable_transport.py) | 928 | 禁删 | 10 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/error_capable_world.py](../../src/sandbox/scenarios/error_capable_world.py) | 39,633 | 禁删 | 52 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/injection.py](../../src/sandbox/scenarios/injection.py) | 3,248 | 禁删 | 18 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/matrix.py](../../src/sandbox/scenarios/matrix.py) | 4,030 | 禁删 | 12 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/models.py](../../src/sandbox/scenarios/models.py) | 30,712 | 禁删 | 60 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_controls.py](../../src/sandbox/scenarios/office_controls.py) | 25,189 | 禁删 | 19 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_episode.py](../../src/sandbox/scenarios/office_episode.py) | 9,703 | 禁删 | 35 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_fork.py](../../src/sandbox/scenarios/office_fork.py) | 8,032 | 禁删 | 14 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_matrix.py](../../src/sandbox/scenarios/office_matrix.py) | 6,751 | 禁删 | 39 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_mutation.py](../../src/sandbox/scenarios/office_mutation.py) | 66,568 | 禁删 | 17 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_mutation_batch.py](../../src/sandbox/scenarios/office_mutation_batch.py) | 45,779 | 禁删 | 10 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/scenarios/office_runtime.py](../../src/sandbox/scenarios/office_runtime.py) | 21,707 | 禁删 | 31 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v1.py](../../src/sandbox/scenarios/office_v1.py) | 37,569 | 禁删 | 38 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/__init__.py](../../src/sandbox/scenarios/office_v2/__init__.py) | 1,923 | 禁删 | 718 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/adversarial_conditions.py](../../src/sandbox/scenarios/office_v2/adversarial_conditions.py) | 24,583 | 禁删 | 21 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/agent_api.py](../../src/sandbox/scenarios/office_v2/agent_api.py) | 12,447 | 禁删 | 30 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/agent_context.py](../../src/sandbox/scenarios/office_v2/agent_context.py) | 39,809 | 禁删 | 42 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/agent_knowledge.py](../../src/sandbox/scenarios/office_v2/agent_knowledge.py) | 8,388 | 禁删 | 20 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/attack_cases.py](../../src/sandbox/scenarios/office_v2/attack_cases.py) | 32,020 | 禁删 | 29 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/attack_compatibility.py](../../src/sandbox/scenarios/office_v2/attack_compatibility.py) | 22,338 | 禁删 | 19 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/attack_models.py](../../src/sandbox/scenarios/office_v2/attack_models.py) | 45,824 | 禁删 | 75 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/attack_objectives.py](../../src/sandbox/scenarios/office_v2/attack_objectives.py) | 18,817 | 禁删 | 35 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/attack_surface.py](../../src/sandbox/scenarios/office_v2/attack_surface.py) | 17,666 | 禁删 | 21 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/canonical_world.py](../../src/sandbox/scenarios/office_v2/canonical_world.py) | 15,507 | 禁删 | 152 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/clean_cases.py](../../src/sandbox/scenarios/office_v2/clean_cases.py) | 24,080 | 禁删 | 90 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/cli_entry.py](../../src/sandbox/scenarios/office_v2/cli_entry.py) | 3,863 | 待定 | 12 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/scenarios/office_v2/data/office-world-v2.0/calendar.json](../../src/sandbox/scenarios/office_v2/data/office-world-v2.0/calendar.json) | 36,157 | 待定 | 13 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/scenarios/office_v2/data/office-world-v2.0/drive.json](../../src/sandbox/scenarios/office_v2/data/office-world-v2.0/drive.json) | 59,896 | 待定 | 12 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/scenarios/office_v2/data/office-world-v2.0/mail.json](../../src/sandbox/scenarios/office_v2/data/office-world-v2.0/mail.json) | 165,392 | 待定 | 12 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/scenarios/office_v2/data/office-world-v2.0/manifest.json](../../src/sandbox/scenarios/office_v2/data/office-world-v2.0/manifest.json) | 1,606 | 待定 | 19 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/scenarios/office_v2/data/office-world-v2.0/organization.json](../../src/sandbox/scenarios/office_v2/data/office-world-v2.0/organization.json) | 215,816 | 待定 | 12 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/scenarios/office_v2/data/office-world-v2.0/policy.json](../../src/sandbox/scenarios/office_v2/data/office-world-v2.0/policy.json) | 3,412 | 待定 | 12 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/scenarios/office_v2/data/office-world-v2.0/quality-report.json](../../src/sandbox/scenarios/office_v2/data/office-world-v2.0/quality-report.json) | 983 | 待定 | 8 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/scenarios/office_v2/data/office-world-v2.0/workspace.json](../../src/sandbox/scenarios/office_v2/data/office-world-v2.0/workspace.json) | 13,245 | 待定 | 12 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/scenarios/office_v2/execution_contracts.py](../../src/sandbox/scenarios/office_v2/execution_contracts.py) | 3,612 | 禁删 | 12 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/execution_request.py](../../src/sandbox/scenarios/office_v2/execution_request.py) | 3,427 | 待定 | 24 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/scenarios/office_v2/fork.py](../../src/sandbox/scenarios/office_v2/fork.py) | 6,127 | 禁删 | 22 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/interaction.py](../../src/sandbox/scenarios/office_v2/interaction.py) | 9,674 | 禁删 | 18 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/interaction_session.py](../../src/sandbox/scenarios/office_v2/interaction_session.py) | 27,490 | 禁删 | 29 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/models.py](../../src/sandbox/scenarios/office_v2/models.py) | 75,629 | 禁删 | 278 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/observation.py](../../src/sandbox/scenarios/office_v2/observation.py) | 23,730 | 禁删 | 34 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/oracle.py](../../src/sandbox/scenarios/office_v2/oracle.py) | 5,756 | 禁删 | 25 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/oracle_evidence.py](../../src/sandbox/scenarios/office_v2/oracle_evidence.py) | 46,652 | 禁删 | 50 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/oracle_models.py](../../src/sandbox/scenarios/office_v2/oracle_models.py) | 30,172 | 禁删 | 70 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/oracle_trace.py](../../src/sandbox/scenarios/office_v2/oracle_trace.py) | 15,739 | 禁删 | 18 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/policy.py](../../src/sandbox/scenarios/office_v2/policy.py) | 26,355 | 禁删 | 34 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/resolution.py](../../src/sandbox/scenarios/office_v2/resolution.py) | 18,624 | 禁删 | 14 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/security_oracle.py](../../src/sandbox/scenarios/office_v2/security_oracle.py) | 41,237 | 禁删 | 16 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/task_catalog.py](../../src/sandbox/scenarios/office_v2/task_catalog.py) | 35,223 | 禁删 | 18 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/tools/__init__.py](../../src/sandbox/scenarios/office_v2/tools/__init__.py) | 2,005 | 禁删 | 155 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/tools/calendar.py](../../src/sandbox/scenarios/office_v2/tools/calendar.py) | 14,153 | 禁删 | 8 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/tools/contracts.py](../../src/sandbox/scenarios/office_v2/tools/contracts.py) | 11,580 | 禁删 | 94 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/tools/drive.py](../../src/sandbox/scenarios/office_v2/tools/drive.py) | 18,176 | 禁删 | 16 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/tools/mail.py](../../src/sandbox/scenarios/office_v2/tools/mail.py) | 9,114 | 禁删 | 8 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/tools/provenance.py](../../src/sandbox/scenarios/office_v2/tools/provenance.py) | 9,691 | 禁删 | 22 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/tools/runtime.py](../../src/sandbox/scenarios/office_v2/tools/runtime.py) | 47,655 | 禁删 | 64 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/tools/workspace.py](../../src/sandbox/scenarios/office_v2/tools/workspace.py) | 9,242 | 禁删 | 8 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/utility_oracle.py](../../src/sandbox/scenarios/office_v2/utility_oracle.py) | 39,256 | 禁删 | 14 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/workspace_fs.py](../../src/sandbox/scenarios/office_v2/workspace_fs.py) | 8,541 | 禁删 | 19 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/office_v2/world.py](../../src/sandbox/scenarios/office_v2/world.py) | 22,309 | 禁删 | 104 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/scenarios/structured_v1/__init__.py](../../src/sandbox/scenarios/structured_v1/__init__.py) | 263 | 待定 | 141 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/scenarios/structured_v1/fixtures/__init__.py](../../src/sandbox/scenarios/structured_v1/fixtures/__init__.py) | 4,383 | 待定 | 257 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_a.py](../../src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_a.py) | 16,835 | 待定 | 50 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_b.py](../../src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_b.py) | 18,715 | 待定 | 19 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_c.py](../../src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_c.py) | 4,580 | 待定 | 23 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_d.py](../../src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_d.py) | 7,303 | 待定 | 12 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_e.py](../../src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_e.py) | 4,389 | 待定 | 12 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_e2.py](../../src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_e2.py) | 8,290 | 待定 | 24 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_e2_completion.py](../../src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_e2_completion.py) | 3,335 | 待定 | 14 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_e2_completion_v2.py](../../src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_e2_completion_v2.py) | 3,658 | 待定 | 13 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_e2_completion_v3.py](../../src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_e2_completion_v3.py) | 3,859 | 待定 | 8 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_enc.py](../../src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_enc.py) | 11,074 | 待定 | 18 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_enc2.py](../../src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_enc2.py) | 3,434 | 待定 | 10 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_range_01.py](../../src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_range_01.py) | 15,483 | 待定 | 12 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [src/sandbox/scheduler/__init__.py](../../src/sandbox/scheduler/__init__.py) | 221 | 禁删 | 32 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/scheduler/docker_scheduler.py](../../src/sandbox/scheduler/docker_scheduler.py) | 19,743 | 禁删 | 28 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/scheduler/exceptions.py](../../src/sandbox/scheduler/exceptions.py) | 330 | 待定 | 6 | 未发现模块外部导入；仅重导出 sandbox.errors，仍为发布兼容接口/镜像 COPY；屏蔽候选的最小导入通过，未运行删除后收集/回归，不满足 FLR §4.1，暂列待定 |
| [src/sandbox/scheduler/models.py](../../src/sandbox/scheduler/models.py) | 483 | 禁删 | 28 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/scoring/__init__.py](../../src/sandbox/scoring/__init__.py) | 127 | 禁删 | 13 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/scoring/rule_scorer.py](../../src/sandbox/scoring/rule_scorer.py) | 3,071 | 禁删 | 20 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/storage/__init__.py](../../src/sandbox/storage/__init__.py) | 233 | 禁删 | 15 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/storage/trajectory_store.py](../../src/sandbox/storage/trajectory_store.py) | 5,458 | 禁删 | 24 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/structured_v1/__init__.py](../../src/sandbox/structured_v1/__init__.py) | 635 | 禁删 | 939 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/structured_v1/acceptance.py](../../src/sandbox/structured_v1/acceptance.py) | 9,870 | 待定 | 29 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/structured_v1/assets.py](../../src/sandbox/structured_v1/assets.py) | 4,209 | 禁删 | 54 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/structured_v1/behavior.py](../../src/sandbox/structured_v1/behavior.py) | 14,710 | 待定 | 10 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/structured_v1/budget.py](../../src/sandbox/structured_v1/budget.py) | 6,896 | 禁删 | 44 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/structured_v1/bundle.py](../../src/sandbox/structured_v1/bundle.py) | 17,789 | 禁删 | 98 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/structured_v1/calibration.py](../../src/sandbox/structured_v1/calibration.py) | 8,751 | 待定 | 12 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/structured_v1/campaign.py](../../src/sandbox/structured_v1/campaign.py) | 30,108 | 待定 | 59 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/structured_v1/campaign_report.py](../../src/sandbox/structured_v1/campaign_report.py) | 12,800 | 待定 | 20 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/structured_v1/candidates.py](../../src/sandbox/structured_v1/candidates.py) | 8,910 | 待定 | 12 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/structured_v1/closure.py](../../src/sandbox/structured_v1/closure.py) | 8,169 | 禁删 | 24 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/structured_v1/container.py](../../src/sandbox/structured_v1/container.py) | 1,882 | 禁删 | 14 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/structured_v1/coverage.py](../../src/sandbox/structured_v1/coverage.py) | 24,253 | 待定 | 74 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/structured_v1/diagnostics.py](../../src/sandbox/structured_v1/diagnostics.py) | 8,414 | 禁删 | 16 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/structured_v1/drivers.py](../../src/sandbox/structured_v1/drivers.py) | 5,965 | 禁删 | 50 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/structured_v1/edit_kernel.py](../../src/sandbox/structured_v1/edit_kernel.py) | 30,005 | 待定 | 22 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/structured_v1/effects.py](../../src/sandbox/structured_v1/effects.py) | 8,683 | 禁删 | 28 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/structured_v1/envelope.py](../../src/sandbox/structured_v1/envelope.py) | 18,760 | 禁删 | 24 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/structured_v1/envelope_codes.py](../../src/sandbox/structured_v1/envelope_codes.py) | 5,492 | 禁删 | 65 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/structured_v1/evidence.py](../../src/sandbox/structured_v1/evidence.py) | 9,855 | 禁删 | 66 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/structured_v1/execution_evidence.py](../../src/sandbox/structured_v1/execution_evidence.py) | 11,090 | 禁删 | 16 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/structured_v1/experiment_inputs.py](../../src/sandbox/structured_v1/experiment_inputs.py) | 4,562 | 待定 | 50 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/structured_v1/exposure.py](../../src/sandbox/structured_v1/exposure.py) | 11,261 | 禁删 | 42 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/structured_v1/feedback.py](../../src/sandbox/structured_v1/feedback.py) | 23,821 | 待定 | 28 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/structured_v1/finalization.py](../../src/sandbox/structured_v1/finalization.py) | 3,451 | 待定 | 10 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/structured_v1/fixture.py](../../src/sandbox/structured_v1/fixture.py) | 14,260 | 禁删 | 112 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/structured_v1/freeze.py](../../src/sandbox/structured_v1/freeze.py) | 4,130 | 禁删 | 32 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/structured_v1/generation.py](../../src/sandbox/structured_v1/generation.py) | 12,117 | 待定 | 36 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/structured_v1/host.py](../../src/sandbox/structured_v1/host.py) | 7,461 | 禁删 | 32 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/structured_v1/host_runner.py](../../src/sandbox/structured_v1/host_runner.py) | 18,279 | 待定 | 16 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/structured_v1/mbr.py](../../src/sandbox/structured_v1/mbr.py) | 14,389 | 待定 | 18 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/structured_v1/model.py](../../src/sandbox/structured_v1/model.py) | 1,455 | 禁删 | 66 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/structured_v1/models.py](../../src/sandbox/structured_v1/models.py) | 5,509 | 禁删 | 124 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/structured_v1/obligations.py](../../src/sandbox/structured_v1/obligations.py) | 9,968 | 禁删 | 56 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/structured_v1/operators.py](../../src/sandbox/structured_v1/operators.py) | 35,739 | 待定 | 21 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/structured_v1/oracle_io.py](../../src/sandbox/structured_v1/oracle_io.py) | 2,942 | 禁删 | 50 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/structured_v1/phase_report.py](../../src/sandbox/structured_v1/phase_report.py) | 3,284 | 待定 | 19 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/structured_v1/phases.py](../../src/sandbox/structured_v1/phases.py) | 2,983 | 禁删 | 26 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/structured_v1/ports.py](../../src/sandbox/structured_v1/ports.py) | 24,448 | 禁删 | 27 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/structured_v1/projection.py](../../src/sandbox/structured_v1/projection.py) | 9,123 | 待定 | 36 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/structured_v1/provider.py](../../src/sandbox/structured_v1/provider.py) | 19,313 | 待定 | 42 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/structured_v1/public_delivery.py](../../src/sandbox/structured_v1/public_delivery.py) | 7,632 | 待定 | 12 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/structured_v1/redaction.py](../../src/sandbox/structured_v1/redaction.py) | 3,310 | 禁删 | 44 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/structured_v1/rendering.py](../../src/sandbox/structured_v1/rendering.py) | 10,482 | 禁删 | 76 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/structured_v1/routes.py](../../src/sandbox/structured_v1/routes.py) | 6,663 | 待定 | 23 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/structured_v1/runtime_assembly.py](../../src/sandbox/structured_v1/runtime_assembly.py) | 3,497 | 禁删 | 24 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/structured_v1/sampling.py](../../src/sandbox/structured_v1/sampling.py) | 12,933 | 待定 | 28 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/structured_v1/search.py](../../src/sandbox/structured_v1/search.py) | 79,725 | 待定 | 61 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/structured_v1/session.py](../../src/sandbox/structured_v1/session.py) | 22,104 | 禁删 | 25 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/structured_v1/streams.py](../../src/sandbox/structured_v1/streams.py) | 1,643 | 待定 | 20 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/structured_v1/submission.py](../../src/sandbox/structured_v1/submission.py) | 5,976 | 待定 | 43 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/structured_v1/text_provider.py](../../src/sandbox/structured_v1/text_provider.py) | 23,474 | 待定 | 26 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/structured_v1/tool_catalogue.py](../../src/sandbox/structured_v1/tool_catalogue.py) | 3,726 | 禁删 | 50 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/structured_v1/transitions.py](../../src/sandbox/structured_v1/transitions.py) | 7,473 | 禁删 | 26 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/structured_v1/validation.py](../../src/sandbox/structured_v1/validation.py) | 24,672 | 待定 | 44 | 旧路线/算法候选；仍有源码、测试、发布/打包或历史复现引用，需先解除依赖 |
| [src/sandbox/structured_v1/world.py](../../src/sandbox/structured_v1/world.py) | 21,414 | 禁删 | 64 | 从当前入口静态可达（含条件/延迟/公共回放分支）；需先改变支持契约或解除引用 |
| [src/sandbox/text_normalization.py](../../src/sandbox/text_normalization.py) | 956 | 禁删 | 10 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/tool_contracts.py](../../src/sandbox/tool_contracts.py) | 17,813 | 禁删 | 71 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [src/sandbox/versions.py](../../src/sandbox/versions.py) | 215 | 禁删 | 14 | 本轮最小导入实际加载；删除当前会破坏主链/Runtime 导入 |
| [tests/__pycache__/conftest.cpython-312-pytest-8.4.2.pyc.31892](../../tests/__pycache__/conftest.cpython-312-pytest-8.4.2.pyc.31892) | 2,466 | 待定 | 0 | 本地二进制/归档材料；内容用途未展开，不能断言过时 |
| [tests/conftest.py](../../tests/conftest.py) | 2,620 | 禁删 | 0 | 当前发布/容器构建/测试收集入口，尚未批准改写或撤除 |
| [tests/design/office-v2-agent-behavior-assessment-acceptance.md](../../tests/design/office-v2-agent-behavior-assessment-acceptance.md) | 4,362 | 待定 | 0 | 可能为旧设计/现状叙述，但历史引用/唯一决策证据或替代说明尚未证明可丢弃 |
| [tests/design/office-v2-operator-target-preservation-acceptance.md](../../tests/design/office-v2-operator-target-preservation-acceptance.md) | 3,444 | 待定 | 0 | 可能为旧设计/现状叙述，但历史引用/唯一决策证据或替代说明尚未证明可丢弃 |
| [tests/integration/test_execution_engine.py](../../tests/integration/test_execution_engine.py) | 6,972 | 待定 | 1 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/integration/test_office_v2_agent_api_composition.py](../../tests/integration/test_office_v2_agent_api_composition.py) | 9,860 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/integration/test_office_v2_causal_chains.py](../../tests/integration/test_office_v2_causal_chains.py) | 48,538 | 待定 | 5 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/integration/test_office_v2_stage5_representatives.py](../../tests/integration/test_office_v2_stage5_representatives.py) | 23,973 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/integration/test_office_v2_upstream_perturbations.py](../../tests/integration/test_office_v2_upstream_perturbations.py) | 12,451 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/integration/test_runtime_api.py](../../tests/integration/test_runtime_api.py) | 3,921 | 待定 | 1 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/integration/test_structured_anchored_feedback.py](../../tests/integration/test_structured_anchored_feedback.py) | 8,402 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/integration/test_structured_campaign_report.py](../../tests/integration/test_structured_campaign_report.py) | 4,148 | 待定 | 1 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/integration/test_structured_completion_diagnostic.py](../../tests/integration/test_structured_completion_diagnostic.py) | 11,056 | 待定 | 1 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/integration/test_structured_delivery_recovery.py](../../tests/integration/test_structured_delivery_recovery.py) | 13,726 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/integration/test_structured_e2_report.py](../../tests/integration/test_structured_e2_report.py) | 19,249 | 待定 | 2 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/integration/test_structured_fdm_disk_entry.py](../../tests/integration/test_structured_fdm_disk_entry.py) | 12,614 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/integration/test_structured_feedback_mutation.py](../../tests/integration/test_structured_feedback_mutation.py) | 16,651 | 待定 | 1 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/integration/test_structured_natural_feedback_chain.py](../../tests/integration/test_structured_natural_feedback_chain.py) | 9,766 | 待定 | 1 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/integration/test_structured_refused_child_settlement.py](../../tests/integration/test_structured_refused_child_settlement.py) | 5,085 | 待定 | 1 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/integration/test_structured_two_arm_campaign.py](../../tests/integration/test_structured_two_arm_campaign.py) | 2,382 | 待定 | 1 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/conftest.py](../../tests/unit/conftest.py) | 7,850 | 禁删 | 0 | 当前主链/探针/验收或项目执行包装器 |
| [tests/unit/structured_coverage_helpers.py](../../tests/unit/structured_coverage_helpers.py) | 1,130 | 待定 | 7 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_artifact_transfer.py](../../tests/unit/test_artifact_transfer.py) | 1,537 | 禁删 | 0 | 当前主链/探针/验收或项目执行包装器 |
| [tests/unit/test_attempted_effect_evidence.py](../../tests/unit/test_attempted_effect_evidence.py) | 1,692 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_behavior_features.py](../../tests/unit/test_behavior_features.py) | 1,929 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_cli_fork_contract.py](../../tests/unit/test_cli_fork_contract.py) | 725 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_controlled_tools.py](../../tests/unit/test_controlled_tools.py) | 1,146 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_coverage_input.py](../../tests/unit/test_coverage_input.py) | 5,149 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_diagnose_guidance_yield.py](../../tests/unit/test_diagnose_guidance_yield.py) | 6,973 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_docker_scheduler_config.py](../../tests/unit/test_docker_scheduler_config.py) | 4,481 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_error_capable_agent.py](../../tests/unit/test_error_capable_agent.py) | 59,993 | 禁删 | 2 | 当前主链/探针/验收或项目执行包装器 |
| [tests/unit/test_error_capable_behaviour_chain.py](../../tests/unit/test_error_capable_behaviour_chain.py) | 6,668 | 禁删 | 1 | 当前主链/探针/验收或项目执行包装器 |
| [tests/unit/test_error_capable_campaign.py](../../tests/unit/test_error_capable_campaign.py) | 58,518 | 禁删 | 2 | 当前主链/探针/验收或项目执行包装器 |
| [tests/unit/test_error_capable_increment.py](../../tests/unit/test_error_capable_increment.py) | 6,681 | 禁删 | 1 | 当前主链/探针/验收或项目执行包装器 |
| [tests/unit/test_error_capable_journal.py](../../tests/unit/test_error_capable_journal.py) | 15,547 | 禁删 | 2 | 当前主链/探针/验收或项目执行包装器 |
| [tests/unit/test_error_capable_local.py](../../tests/unit/test_error_capable_local.py) | 6,455 | 禁删 | 0 | 当前主链/探针/验收或项目执行包装器 |
| [tests/unit/test_error_capable_priority.py](../../tests/unit/test_error_capable_priority.py) | 21,310 | 禁删 | 2 | 当前主链/探针/验收或项目执行包装器 |
| [tests/unit/test_error_capable_runtime_surface.py](../../tests/unit/test_error_capable_runtime_surface.py) | 9,637 | 禁删 | 1 | 当前主链/探针/验收或项目执行包装器 |
| [tests/unit/test_error_capable_scenario.py](../../tests/unit/test_error_capable_scenario.py) | 26,134 | 禁删 | 0 | 当前主链/探针/验收或项目执行包装器 |
| [tests/unit/test_error_capable_selector.py](../../tests/unit/test_error_capable_selector.py) | 13,251 | 禁删 | 2 | 当前主链/探针/验收或项目执行包装器 |
| [tests/unit/test_error_capable_submission.py](../../tests/unit/test_error_capable_submission.py) | 7,531 | 禁删 | 0 | 当前主链/探针/验收或项目执行包装器 |
| [tests/unit/test_error_capable_violation_types.py](../../tests/unit/test_error_capable_violation_types.py) | 24,811 | 禁删 | 1 | 当前主链/探针/验收或项目执行包装器 |
| [tests/unit/test_exploratory_campaign.py](../../tests/unit/test_exploratory_campaign.py) | 5,329 | 待定 | 2 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_exploratory_recovery.py](../../tests/unit/test_exploratory_recovery.py) | 14,999 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_feature_normalizer.py](../../tests/unit/test_feature_normalizer.py) | 565 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_guided_seed_priority.py](../../tests/unit/test_guided_seed_priority.py) | 16,483 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_jsonrpc.py](../../tests/unit/test_jsonrpc.py) | 1,188 | 禁删 | 0 | 当前主链/探针/验收或项目执行包装器 |
| [tests/unit/test_langgraph_react_runtime.py](../../tests/unit/test_langgraph_react_runtime.py) | 38,713 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_models_and_cases.py](../../tests/unit/test_models_and_cases.py) | 1,328 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_benign_tasks.py](../../tests/unit/test_office_benign_tasks.py) | 5,655 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_candidate_generation.py](../../tests/unit/test_office_candidate_generation.py) | 9,513 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_carrier_fork.py](../../tests/unit/test_office_carrier_fork.py) | 4,851 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_composition_rules.py](../../tests/unit/test_office_composition_rules.py) | 5,563 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_container_tools.py](../../tests/unit/test_office_container_tools.py) | 7,265 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_control_provider.py](../../tests/unit/test_office_control_provider.py) | 4,674 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_coverage_input.py](../../tests/unit/test_office_coverage_input.py) | 8,823 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_episode_initialization.py](../../tests/unit/test_office_episode_initialization.py) | 6,109 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_expression_mutation.py](../../tests/unit/test_office_expression_mutation.py) | 12,916 | 待定 | 2 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_injection_carriers.py](../../tests/unit/test_office_injection_carriers.py) | 7,505 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_mutation_batch.py](../../tests/unit/test_office_mutation_batch.py) | 18,465 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_retarget_mutation.py](../../tests/unit/test_office_retarget_mutation.py) | 11,803 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_runtime.py](../../tests/unit/test_office_runtime.py) | 9,962 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_safe_control.py](../../tests/unit/test_office_safe_control.py) | 4,783 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_test_matrix.py](../../tests/unit/test_office_test_matrix.py) | 4,128 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_trace_react_adapter.py](../../tests/unit/test_office_trace_react_adapter.py) | 5,652 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_agent_api.py](../../tests/unit/test_office_v2_agent_api.py) | 9,706 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_agent_behavior_assessment.py](../../tests/unit/test_office_v2_agent_behavior_assessment.py) | 4,943 | 待定 | 1 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_agent_context.py](../../tests/unit/test_office_v2_agent_context.py) | 7,115 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_agent_identity_context.py](../../tests/unit/test_office_v2_agent_identity_context.py) | 4,503 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_agent_policy_context.py](../../tests/unit/test_office_v2_agent_policy_context.py) | 4,935 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_agent_prompt.py](../../tests/unit/test_office_v2_agent_prompt.py) | 5,367 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_agent_session_surface.py](../../tests/unit/test_office_v2_agent_session_surface.py) | 11,001 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_attack_contracts.py](../../tests/unit/test_office_v2_attack_contracts.py) | 4,923 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_attack_materialization.py](../../tests/unit/test_office_v2_attack_materialization.py) | 11,374 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_attack_objectives.py](../../tests/unit/test_office_v2_attack_objectives.py) | 1,540 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_attack_surface.py](../../tests/unit/test_office_v2_attack_surface.py) | 2,675 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_behavior_contracts.py](../../tests/unit/test_office_v2_behavior_contracts.py) | 6,757 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_blocked_attempt_sample.py](../../tests/unit/test_office_v2_blocked_attempt_sample.py) | 15,193 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_campaign_report.py](../../tests/unit/test_office_v2_campaign_report.py) | 5,430 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_canonical_world.py](../../tests/unit/test_office_v2_canonical_world.py) | 6,927 | 待定 | 1 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_clarification_coordinator.py](../../tests/unit/test_office_v2_clarification_coordinator.py) | 7,956 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_clean_oracle.py](../../tests/unit/test_office_v2_clean_oracle.py) | 1,386 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_comparison_basis.py](../../tests/unit/test_office_v2_comparison_basis.py) | 17,332 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_comparison_report.py](../../tests/unit/test_office_v2_comparison_report.py) | 22,837 | 待定 | 6 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_comparison_sample.py](../../tests/unit/test_office_v2_comparison_sample.py) | 20,768 | 待定 | 10 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_coverage_contracts.py](../../tests/unit/test_office_v2_coverage_contracts.py) | 6,561 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_coverage_input.py](../../tests/unit/test_office_v2_coverage_input.py) | 18,337 | 待定 | 4 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_domain_tools.py](../../tests/unit/test_office_v2_domain_tools.py) | 16,891 | 待定 | 6 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_episode_coverage.py](../../tests/unit/test_office_v2_episode_coverage.py) | 5,381 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_episode_token_usage.py](../../tests/unit/test_office_v2_episode_token_usage.py) | 4,003 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_exploration_fairness.py](../../tests/unit/test_office_v2_exploration_fairness.py) | 6,655 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_exposure_evaluator.py](../../tests/unit/test_office_v2_exposure_evaluator.py) | 12,806 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_fuzzer_corpus.py](../../tests/unit/test_office_v2_fuzzer_corpus.py) | 12,509 | 待定 | 1 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_fuzzer_scheduler.py](../../tests/unit/test_office_v2_fuzzer_scheduler.py) | 1,467 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_fuzzer_work.py](../../tests/unit/test_office_v2_fuzzer_work.py) | 3,802 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_interaction.py](../../tests/unit/test_office_v2_interaction.py) | 10,855 | 待定 | 1 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_interaction_session.py](../../tests/unit/test_office_v2_interaction_session.py) | 12,322 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_models.py](../../tests/unit/test_office_v2_models.py) | 43,843 | 待定 | 3 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_mutator_worker.py](../../tests/unit/test_office_v2_mutator_worker.py) | 325 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_objective_assertion_matcher.py](../../tests/unit/test_office_v2_objective_assertion_matcher.py) | 15,660 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_observation.py](../../tests/unit/test_office_v2_observation.py) | 9,988 | 待定 | 1 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_oracle_evidence.py](../../tests/unit/test_office_v2_oracle_evidence.py) | 14,331 | 待定 | 3 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_oracle_models.py](../../tests/unit/test_office_v2_oracle_models.py) | 13,719 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_oracle_rebuild.py](../../tests/unit/test_office_v2_oracle_rebuild.py) | 7,288 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_oracle_trace.py](../../tests/unit/test_office_v2_oracle_trace.py) | 11,774 | 待定 | 5 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_policy.py](../../tests/unit/test_office_v2_policy.py) | 13,836 | 待定 | 1 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_policy_violations.py](../../tests/unit/test_office_v2_policy_violations.py) | 7,133 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_promotion_resolution.py](../../tests/unit/test_office_v2_promotion_resolution.py) | 7,183 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_public_cli_entry.py](../../tests/unit/test_office_v2_public_cli_entry.py) | 2,076 | 待定 | 1 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_resolution.py](../../tests/unit/test_office_v2_resolution.py) | 12,688 | 待定 | 1 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_risk_catalog.py](../../tests/unit/test_office_v2_risk_catalog.py) | 1,779 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_risk_coverage.py](../../tests/unit/test_office_v2_risk_coverage.py) | 2,303 | 待定 | 2 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_runtime_surface_architecture.py](../../tests/unit/test_office_v2_runtime_surface_architecture.py) | 1,847 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_scenario_oracle.py](../../tests/unit/test_office_v2_scenario_oracle.py) | 4,934 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_scoring.py](../../tests/unit/test_office_v2_scoring.py) | 12,787 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_semantic_mutation.py](../../tests/unit/test_office_v2_semantic_mutation.py) | 6,043 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_stage3_boundary.py](../../tests/unit/test_office_v2_stage3_boundary.py) | 5,082 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_stage4_boundary.py](../../tests/unit/test_office_v2_stage4_boundary.py) | 6,905 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_stage5_boundary.py](../../tests/unit/test_office_v2_stage5_boundary.py) | 2,409 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_target_oracle.py](../../tests/unit/test_office_v2_target_oracle.py) | 7,091 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_task_catalog.py](../../tests/unit/test_office_v2_task_catalog.py) | 5,617 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_task_execution_support.py](../../tests/unit/test_office_v2_task_execution_support.py) | 2,517 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_tool_contracts.py](../../tests/unit/test_office_v2_tool_contracts.py) | 6,169 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_tool_provenance.py](../../tests/unit/test_office_v2_tool_provenance.py) | 6,977 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_tool_runtime.py](../../tests/unit/test_office_v2_tool_runtime.py) | 11,913 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_tool_specs.py](../../tests/unit/test_office_v2_tool_specs.py) | 3,559 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_unexpected_risk.py](../../tests/unit/test_office_v2_unexpected_risk.py) | 3,173 | 待定 | 2 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_utility_assertion_catalog.py](../../tests/unit/test_office_v2_utility_assertion_catalog.py) | 6,558 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_utility_evaluator.py](../../tests/unit/test_office_v2_utility_evaluator.py) | 17,149 | 待定 | 8 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_visibility_chain.py](../../tests/unit/test_office_v2_visibility_chain.py) | 13,673 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_workspace_fs.py](../../tests/unit/test_office_v2_workspace_fs.py) | 6,806 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_v2_world.py](../../tests/unit/test_office_v2_world.py) | 8,778 | 待定 | 1 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_office_vulnerable_control.py](../../tests/unit/test_office_vulnerable_control.py) | 5,716 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_ollama_network_policy.py](../../tests/unit/test_ollama_network_policy.py) | 3,615 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_ollama_react_provider.py](../../tests/unit/test_ollama_react_provider.py) | 9,670 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_recording_components.py](../../tests/unit/test_recording_components.py) | 6,395 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_replay_core.py](../../tests/unit/test_replay_core.py) | 7,691 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_replay_decision_input.py](../../tests/unit/test_replay_decision_input.py) | 8,118 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_rule_scorer.py](../../tests/unit/test_rule_scorer.py) | 1,255 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_runtime_client_transport.py](../../tests/unit/test_runtime_client_transport.py) | 4,557 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_runtime_error_contract.py](../../tests/unit/test_runtime_error_contract.py) | 1,225 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_runtime_resources.py](../../tests/unit/test_runtime_resources.py) | 809 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_runtime_state.py](../../tests/unit/test_runtime_state.py) | 15,167 | 禁删 | 0 | 当前主链/探针/验收或项目执行包装器 |
| [tests/unit/test_scenario_contracts.py](../../tests/unit/test_scenario_contracts.py) | 9,852 | 待定 | 1 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_shared_protocol.py](../../tests/unit/test_shared_protocol.py) | 425 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_behavior_semantics.py](../../tests/unit/test_structured_behavior_semantics.py) | 11,333 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_calibration.py](../../tests/unit/test_structured_calibration.py) | 3,413 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_case_admission.py](../../tests/unit/test_structured_case_admission.py) | 13,316 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_case_identity.py](../../tests/unit/test_structured_case_identity.py) | 7,553 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_case_operators.py](../../tests/unit/test_structured_case_operators.py) | 19,274 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_closure.py](../../tests/unit/test_structured_closure.py) | 11,789 | 待定 | 1 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_completion.py](../../tests/unit/test_structured_completion.py) | 3,797 | 待定 | 8 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_coverage.py](../../tests/unit/test_structured_coverage.py) | 5,377 | 待定 | 1 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_coverage_contract_findings.py](../../tests/unit/test_structured_coverage_contract_findings.py) | 12,636 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_delivery_content.py](../../tests/unit/test_structured_delivery_content.py) | 6,316 | 待定 | 1 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_directed_layer.py](../../tests/unit/test_structured_directed_layer.py) | 9,854 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_edit_kernel.py](../../tests/unit/test_structured_edit_kernel.py) | 10,790 | 待定 | 1 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_effects.py](../../tests/unit/test_structured_effects.py) | 16,409 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_envelope.py](../../tests/unit/test_structured_envelope.py) | 24,831 | 待定 | 1 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_episode_rehearsal.py](../../tests/unit/test_structured_episode_rehearsal.py) | 25,211 | 待定 | 18 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_execution_evidence.py](../../tests/unit/test_structured_execution_evidence.py) | 9,537 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_experiment_inputs.py](../../tests/unit/test_structured_experiment_inputs.py) | 1,105 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_exposure.py](../../tests/unit/test_structured_exposure.py) | 12,557 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_failure_diagnostics.py](../../tests/unit/test_structured_failure_diagnostics.py) | 8,011 | 待定 | 1 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_feedback_chain.py](../../tests/unit/test_structured_feedback_chain.py) | 13,610 | 待定 | 1 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_feedback_contract.py](../../tests/unit/test_structured_feedback_contract.py) | 9,431 | 待定 | 2 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_finalization.py](../../tests/unit/test_structured_finalization.py) | 8,796 | 待定 | 2 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_first_turn_context.py](../../tests/unit/test_structured_first_turn_context.py) | 8,227 | 待定 | 1 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_fixture_anchored.py](../../tests/unit/test_structured_fixture_anchored.py) | 15,016 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_fixture_assets.py](../../tests/unit/test_structured_fixture_assets.py) | 16,849 | 待定 | 1 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_fixture_branching.py](../../tests/unit/test_structured_fixture_branching.py) | 15,556 | 待定 | 11 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_fixture_e2.py](../../tests/unit/test_structured_fixture_e2.py) | 14,651 | 待定 | 4 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_fixture_e2_completion.py](../../tests/unit/test_structured_fixture_e2_completion.py) | 4,917 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_fixture_enc.py](../../tests/unit/test_structured_fixture_enc.py) | 8,035 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_fixture_target_preserving.py](../../tests/unit/test_structured_fixture_target_preserving.py) | 12,737 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_freeze.py](../../tests/unit/test_structured_freeze.py) | 7,221 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_g1_freezing.py](../../tests/unit/test_structured_g1_freezing.py) | 5,632 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_generation_flow.py](../../tests/unit/test_structured_generation_flow.py) | 5,638 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_host_runner_probes.py](../../tests/unit/test_structured_host_runner_probes.py) | 9,702 | 待定 | 2 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_material_discovery.py](../../tests/unit/test_structured_material_discovery.py) | 5,576 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_materialization.py](../../tests/unit/test_structured_materialization.py) | 17,401 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_mbr.py](../../tests/unit/test_structured_mbr.py) | 6,765 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_no_model_acceptance.py](../../tests/unit/test_structured_no_model_acceptance.py) | 12,318 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_normal_control_acceptance.py](../../tests/unit/test_structured_normal_control_acceptance.py) | 10,427 | 待定 | 3 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_normal_control_budget.py](../../tests/unit/test_structured_normal_control_budget.py) | 2,145 | 待定 | 1 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_obligations.py](../../tests/unit/test_structured_obligations.py) | 11,059 | 待定 | 1 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_projection.py](../../tests/unit/test_structured_projection.py) | 4,806 | 待定 | 1 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_provider.py](../../tests/unit/test_structured_provider.py) | 12,298 | 待定 | 1 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_random_evolution.py](../../tests/unit/test_structured_random_evolution.py) | 14,736 | 待定 | 1 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_range_calibration.py](../../tests/unit/test_structured_range_calibration.py) | 13,213 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_registered_matching.py](../../tests/unit/test_structured_registered_matching.py) | 4,946 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_root_sampling.py](../../tests/unit/test_structured_root_sampling.py) | 6,611 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_routes.py](../../tests/unit/test_structured_routes.py) | 4,116 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_t03_feedback_position.py](../../tests/unit/test_structured_t03_feedback_position.py) | 8,903 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_t04_settlement.py](../../tests/unit/test_structured_t04_settlement.py) | 12,095 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_t05_recovery.py](../../tests/unit/test_structured_t05_recovery.py) | 4,635 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_technique_rotation.py](../../tests/unit/test_structured_technique_rotation.py) | 1,691 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_text_provider.py](../../tests/unit/test_structured_text_provider.py) | 14,202 | 待定 | 1 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_tool_runtime_integration.py](../../tests/unit/test_structured_tool_runtime_integration.py) | 3,680 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_two_arm_generation.py](../../tests/unit/test_structured_two_arm_generation.py) | 15,658 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_two_arm_search.py](../../tests/unit/test_structured_two_arm_search.py) | 17,878 | 待定 | 3 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_structured_two_phase.py](../../tests/unit/test_structured_two_phase.py) | 24,787 | 待定 | 3 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_target_judge.py](../../tests/unit/test_target_judge.py) | 6,730 | 待定 | 2 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_trace_react_adapter.py](../../tests/unit/test_trace_react_adapter.py) | 7,529 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_trace_react_contract.py](../../tests/unit/test_trace_react_contract.py) | 1,287 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_trace_workspace_controls.py](../../tests/unit/test_trace_workspace_controls.py) | 2,382 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_trace_workspace_scenario.py](../../tests/unit/test_trace_workspace_scenario.py) | 3,526 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_trajectory_store.py](../../tests/unit/test_trajectory_store.py) | 2,006 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_v2_agent_knowledge.py](../../tests/unit/test_v2_agent_knowledge.py) | 7,371 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_v2_effectiveness.py](../../tests/unit/test_v2_effectiveness.py) | 12,512 | 待定 | 4 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_v2_effectiveness_authorized_pair.py](../../tests/unit/test_v2_effectiveness_authorized_pair.py) | 10,059 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_v2_effectiveness_protocol.py](../../tests/unit/test_v2_effectiveness_protocol.py) | 27,219 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_v2_effectiveness_report.py](../../tests/unit/test_v2_effectiveness_report.py) | 15,359 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_v2_risk_pool_scheduler.py](../../tests/unit/test_v2_risk_pool_scheduler.py) | 4,093 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_v2_seed_pools.py](../../tests/unit/test_v2_seed_pools.py) | 7,277 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tests/unit/test_zero_information_episodes.py](../../tests/unit/test_zero_information_episodes.py) | 10,563 | 待定 | 0 | 仍可被 pytest 收集，含跨测试/fixture 依赖；对应路线尚未批准整体退役 |
| [tmp-campaign-multipath.json](../../tmp-campaign-multipath.json) | 2,386,834 | 待定 | 0 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [tmp-campaign.json](../../tmp-campaign.json) | 2,296,379 | 待定 | 0 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [tmp-probe-multipath.json](../../tmp-probe-multipath.json) | 2,683,344 | 待定 | 0 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |
| [tmp-probe.json](../../tmp-probe.json) | 10,247 | 待定 | 0 | 脚本/配置或共享模块仍有入口、引用或未证实用途；待后续逐批核实 |

## 8. 缓存目录聚合（精确路径见 JSON）

共 767 个缓存：766 可删、1 待定；证据目录中的缓存不在本项。

| 目录 | 分类 | 文件数 | B | 依据 |
|---|---|---:|---:|---|
| `agent_image/app/__pycache__` | 可删 | 13 | 128,642 | 有源 .py 对应的可再生缓存；未发现显式路径依赖；后续需确认无活动写入 |
| `agent_image/app/adapter/__pycache__` | 可删 | 8 | 168,346 | 有源 .py 对应的可再生缓存；未发现显式路径依赖；后续需确认无活动写入 |
| `agent_image/app/agent/__pycache__` | 可删 | 6 | 38,215 | 有源 .py 对应的可再生缓存；未发现显式路径依赖；后续需确认无活动写入 |
| `agent_image/app/replay/__pycache__` | 可删 | 7 | 75,759 | 有源 .py 对应的可再生缓存；未发现显式路径依赖；后续需确认无活动写入 |
| `agent_image/app/tools/__pycache__` | 可删 | 13 | 61,832 | 有源 .py 对应的可再生缓存；未发现显式路径依赖；后续需确认无活动写入 |
| `agent_image/app/tracing/__pycache__` | 可删 | 3 | 3,442 | 有源 .py 对应的可再生缓存；未发现显式路径依赖；后续需确认无活动写入 |
| `scripts/__pycache__` | 可删 | 2 | 64,513 | 有源 .py 对应的可再生缓存；未发现显式路径依赖；后续需确认无活动写入 |
| `src/sandbox/__pycache__` | 可删 | 17 | 118,859 | 有源 .py 对应的可再生缓存；未发现显式路径依赖；后续需确认无活动写入 |
| `src/sandbox/client/__pycache__` | 可删 | 4 | 31,172 | 有源 .py 对应的可再生缓存；未发现显式路径依赖；后续需确认无活动写入 |
| `src/sandbox/coverage/__pycache__` | 可删 | 26 | 435,548 | 有源 .py 对应的可再生缓存；未发现显式路径依赖；后续需确认无活动写入 |
| `src/sandbox/engine/__pycache__` | 可删 | 4 | 19,359 | 有源 .py 对应的可再生缓存；未发现显式路径依赖；后续需确认无活动写入 |
| `src/sandbox/fuzzer/__pycache__` | 可删 | 49 | 884,413 | 有源 .py 对应的可再生缓存；未发现显式路径依赖；后续需确认无活动写入 |
| `src/sandbox/mutation/__pycache__` | 可删 | 24 | 232,011 | 有源 .py 对应的可再生缓存；未发现显式路径依赖；后续需确认无活动写入 |
| `src/sandbox/mutation/providers/__pycache__` | 可删 | 3 | 7,634 | 有源 .py 对应的可再生缓存；未发现显式路径依赖；后续需确认无活动写入 |
| `src/sandbox/replay/__pycache__` | 可删 | 12 | 97,589 | 有源 .py 对应的可再生缓存；未发现显式路径依赖；后续需确认无活动写入 |
| `src/sandbox/scenarios/__pycache__` | 可删 | 32 | 808,523 | 有源 .py 对应的可再生缓存；未发现显式路径依赖；后续需确认无活动写入 |
| `src/sandbox/scenarios/office_v2/__pycache__` | 可删 | 31 | 933,040 | 有源 .py 对应的可再生缓存；未发现显式路径依赖；后续需确认无活动写入 |
| `src/sandbox/scenarios/office_v2/tools/__pycache__` | 可删 | 8 | 159,795 | 有源 .py 对应的可再生缓存；未发现显式路径依赖；后续需确认无活动写入 |
| `src/sandbox/scenarios/structured_v1/__pycache__` | 可删 | 1 | 432 | 有源 .py 对应的可再生缓存；未发现显式路径依赖；后续需确认无活动写入 |
| `src/sandbox/scenarios/structured_v1/fixtures/__pycache__` | 可删 | 13 | 113,100 | 有源 .py 对应的可再生缓存；未发现显式路径依赖；后续需确认无活动写入 |
| `src/sandbox/scheduler/__pycache__` | 可删 | 4 | 24,462 | 有源 .py 对应的可再生缓存；未发现显式路径依赖；后续需确认无活动写入 |
| `src/sandbox/scoring/__pycache__` | 可删 | 2 | 3,931 | 有源 .py 对应的可再生缓存；未发现显式路径依赖；后续需确认无活动写入 |
| `src/sandbox/storage/__pycache__` | 可删 | 2 | 8,548 | 有源 .py 对应的可再生缓存；未发现显式路径依赖；后续需确认无活动写入 |
| `src/sandbox/structured_v1/__pycache__` | 可删 | 56 | 886,495 | 有源 .py 对应的可再生缓存；未发现显式路径依赖；后续需确认无活动写入 |
| `src/sandbox/structured_v1/__pycache__` | 待定 | 1 | 4,682 | 缓存对应源或使用状态未核实；不能按扩展名直接删除 |
| `tests/__pycache__` | 可删 | 3 | 6,792 | 有源 .py 对应的可再生缓存；未发现显式路径依赖；后续需确认无活动写入 |
| `tests/integration/__pycache__` | 可删 | 32 | 888,049 | 有源 .py 对应的可再生缓存；未发现显式路径依赖；后续需确认无活动写入 |
| `tests/unit/__pycache__` | 可删 | 391 | 8,514,338 | 有源 .py 对应的可再生缓存；未发现显式路径依赖；后续需确认无活动写入 |

机器清单：[docs/reports/20261010-full-legacy-retirement-inventory.json](20261010-full-legacy-retirement-inventory.json)；全部文件/摘要/分类/边界/引用边/精确批次保存在该文件。
