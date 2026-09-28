# 项目地图：目录、入口、依赖、运行时与模块职责

> **2026-09-19 状态更新**：真实配对运行已执行（smoke 两臂各 1 Episode + 正式 30×2 两轮），结果与限制见 `docs/tasks/20260919-lightweight-comparison-run-record.md` §8。本文件下列“未运行模型／未运行 Docker／未运行真实 Campaign”等表述描述其编写基线 `a15b127` 时的状态，保留为历史文本，不代表当前状态。

- 对应 Spec：`SPEC-AUDIT-20260917`，目标 `AUD-G-01`、`AUD-REQ-01`、`AUD-REQ-02`、`AUD-AC-01`、`AUD-AC-02`
- 交付物序号：1 / 5（项目地图）
- 事实性文档；不含修复方案。需要用户决定的事项见 `discussion-items.md`

---

## 1. 审计基线（冻结）

| 项目 | 值 |
|---|---|
| 仓库根 | 本仓库检出目录 |
| 分支 | `main` |
| HEAD | `a15b1273af1b6a1d3ec98eea50ba18aa3df823e7` |
| 最近提交 | `a15b127 docs: approve current code audit tasks`、`a79e599 docs: draft current code audit tasks`、`ff08b7c docs: approve current code audit spec`、`8f4bfb8 docs: draft current code audit spec` |
| `git status --short` | 仅两行未跟踪：`?? .pytest-tmp/`、`?? harness-node-modules.tar.gz` |
| 受跟踪文件数 | 402 |
| `src` 下 Python 行数 | 62,134 |

**用户已有的未跟踪文件**：`.pytest-tmp/` 与 `harness-node-modules.tar.gz`。二者均为用户所有，本次审计**未读取、未修改、未删除、未纳入任何提交**（`AUD-REQ-02`）。

审计期间基线未发生漂移：工作区在全部证据采集前后均为上述两行未跟踪文件，无新增修改。

### 1.1 环境能力

| 运行时 | 版本 | 本次是否使用 |
|---|---|---|
| 系统 Python | 3.9.13 | 未使用（低于 `requires-python`） |
| 项目 venv Python | 3.12.14（`.venv/Scripts/python.exe`） | **使用**：`compileall`、`ruff`、`pytest` |
| Node | v24.14.0 | 未使用（`agent_variants/deepseek_harness` 为 Node 运行时，需要容器） |
| Docker | 29.6.1（已安装且守护进程可达） | **刻意未使用**：TASK 明确禁止运行 Docker / 真实模型 / 远程服务 / 真实 Campaign |
| git | 2.53.0.windows.1 | 使用：只读查询与文档提交 |

### 1.2 因权限、体积或环境原因未能核查的部分

| 未核查项 | 原因 | 影响 |
|---|---|---|
| `tests/integration` 中依赖 Docker 的路径 | TASK 禁止启动容器与真实模型 | 相关结论最高只能到 `E3`，标 `UNVERIFIED` |
| 真实模型 / Ollama / GPU 路径 | 同上 | 同上 |
| `.pytest-tmp/`、`harness-node-modules.tar.gz` | 用户所有，未授权读取 | 未作为任何证据来源 |
| 远程服务器归档 | TASK 禁止连接 | 未使用 |
| 历史运行产物（`data_root` 下） | 唯一可确认来源与代码版本的方式缺失；仓库内未找到可归因的归档 | 若引用只能计 `E4`；本文档未引用 |

### 1.3 本次实际运行过的命令与结果

所有 Python 命令均使用项目虚拟环境解释器 **`.venv\Scripts\python.exe`**（Python 3.12.14）。系统 Python 中**没有** `ruff` 与项目依赖，直接用 `python` 会失败——这一点是复现的关键。

| 命令 | 退出码 | 结果 |
|---|---|---|
| `.venv\Scripts\python.exe -m compileall -q src agent_image tests` | 0 | 无输出（无语法错误） |
| `scripts\project_ruff.cmd check src agent_image tests` | 非 0 | **2 个错误**，均只涉及测试文件的导入（见下） |
| `.venv\Scripts\python.exe -m pytest tests/unit -p no:cacheprovider --basetemp=<tmp> -q` | **0** | 792 个结果符号，其中 `F`/`E` 为 **0**；6 条 warning |
| `.venv\Scripts\python.exe -m pytest tests/integration -p no:cacheprovider --basetemp=<tmp> -q` | **0** | 29 个结果符号，全部 `.` |

`ruff` 的规范入口是 `scripts\project_ruff.cmd`（内部解析 `.venv\Scripts\ruff.exe` 并附加 `--no-cache`，见 `scripts/project_ruff.cmd`）。等价的直接调用为 `.venv\Scripts\python.exe -m ruff check --no-cache <目标>`。

所有 pytest 运行均带 `PYTHONDONTWRITEBYTECODE=1` 与 `-p no:cacheprovider`，`--basetemp` 指向本次新建的系统临时目录，**未写入用户的 `.pytest-tmp/`**。

`ruff` 的 2 个错误（`E3`，静态）：

- `tests/unit/test_office_expression_mutation.py:22` `F401`：`OfficeMutationProviderKind` 导入未使用；
- `tests/unit/test_office_v2_public_cli_entry.py:1` `I001`：导入块未排序。

两者都在测试文件中，不影响产品代码；`AGENTS.md` §6 要求的 `scripts/project_ruff.cmd check` 基线**当前不通过**。

---

## 2. 顶层目录职责

| 路径 | 受跟踪文件 | 职责 | 证据 |
|---|---|---|---|
| `src/sandbox/` | 198 | 全部产品代码（Python 包 `sandbox`） | `pyproject.toml:33-34` |
| `tests/unit/` | 104 | 单元测试（20,717 行） | 实测收集/运行 |
| `tests/integration/` | 6 | 集成测试（含 Docker/FastAPI 路径） | 实测运行 29 项通过 |
| `tests/design/` | 2 | 设计层测试 | `E3` |
| `agent_image/` | 53 | Agent 侧容器镜像：应用代码 + 4 个 Dockerfile + 依赖锁 | `git ls-files` |
| `agent_variants/deepseek_harness/` | 14 | 第二个 Agent 运行时（Node/JS）。**本仓库内唯一的本地 fixture**：`fixtures/mcp_server.mjs`（MCP 服务桩）与 `fixtures/openai_stub.mjs`（确定性模型桩），随 git 版本化；`SPEC.md` 中**无对应要求**（见 `spec-compliance.md` §5.6 `R1`） | `git ls-files` |
| `controller_image/` | 2 | 控制面镜像（`Dockerfile` + `requirements.txt`） | `git ls-files` |
| `config/` | 1 | `risk-taxonomy.yaml`（**无代码引用**，见 §5.4） | 全仓 grep |
| `scripts/` | 6 | 本地包装脚本与建场脚本 | `git ls-files` |
| `docs/` | — | `SPEC.md`、`specs/`、`tasks/`、`plans/`、`runbooks/`、`setup/`、`releases/`、`architecture/`、`audits/` | `ls docs/` |
| 根级 | 9 | `AGENTS.md`、`README.md`、`HANDOFF.md`、`LOG.md`、`LOG-INDEX.md`、`SPEC.md`（兼容入口）、`AUTHORIZED-EVALUATION-SCOPE.md`、`.gitignore`、`.gitattributes`、`.dockerignore` | `git ls-files` |

`docs/` 下 `architecture/`、`audits/`、`releases/`、`runbooks/`、`setup/` **五个子目录当前为空**（存在但无任何文件）；有内容的只有 `plans/`（1）、`specs/`（1）、`tasks/`（1）与 `SPEC.md`、`README.md` 两个文件。本审计的产出自建 `docs/current-state/`。

---

## 3. 入口

### 3.1 唯一安装的 console script

`pyproject.toml:30-31`：

```toml
[project.scripts]
trace-redteam-v2-campaign = "sandbox.fuzzer.v2_cli:main"
```

**这是 `pyproject.toml` 中唯一注册的命令行入口。** 全部审计数据流由它进入。证据等级 `E3`（声明）+ `E3`（代码）。

### 3.2 CLI 子命令

[src/sandbox/fuzzer/v2_cli.py:41-101](src/sandbox/fuzzer/v2_cli.py#L41-L101) 定义 7 个子命令：

| 子命令 | 作用 | 是否触发外部执行 |
|---|---|---|
| `preflight` | 检查镜像、模型、Ollama 可达性与数据目录 | 只读探测 |
| `exploratory-run` | 运行/续跑一个探索性 Campaign | **是**：Docker + 模型 |
| `exploratory-resume` | 续跑已存在 Campaign（不存在则报错） | **是** |
| `exploratory-smoke` | 单 Episode 冒烟（`target_episodes=1`） | **是** |
| `inspect` | 从 SQLite 重建报告 payload | 否 |
| `plan-next` | 计算/复用下一代决策 | 否 |
| `report` | 把报告写入文件 | 否 |

关键参数（`v2_cli.py:52-92`）：`--strategy`（`random_independent` / `coverage_guided`，默认后者）、`--campaign-seed`（默认 0）、`--episodes`/`--generations`（默认 10）、`--agent-runtime`（`AgentRuntimeKind`，默认 `langgraph`）、`--ollama-mode`（`host` / `embedded`，默认 `host`）、`--agent-image`、`--mutator-image`、`--data-root`。

### 3.3 退出码语义

`v2_cli.py:158-164`：

- `preflight`：`0` 表示 `ready`，否则 `2`；
- `exploratory-*`：`0` **仅当** `target_reached` 为真且 `completion_status` 为 `None`，否则 `2`；
- 其余子命令：恒为 `0`。

即：只有"跑到目标 Episode 数且未提前终止"才算成功。

### 3.4 关键入口事实：嵌入式模式没有目标裁判

`v2_cli.py:287-291`：

```python
target_judge=OllamaTargetPreservationJudge(...) if args.ollama_mode == "host" else None,
```

`--ollama-mode embedded` 时 `target_judge=None`。后果：目标保持校验不可执行 → 变异只能落在 `UNVERIFIED` → 晋升被阻断。见 `spec-compliance.md`（`FR-FUZZ-03`、`FR-FUZZ-04`）与 `discussion-items.md` 第 4 项。

### 3.5 未注册为 script 的其他入口

| 路径 | 性质 |
|---|---|
| `src/sandbox/cli.py` | 更早的 CLI 模块（`E3`，未被 `[project.scripts]` 注册） |
| `agent_image/app/`（45 文件） | 容器内 Agent 应用，由镜像 `ENTRYPOINT` 启动，不作为宿主机入口 |
| `agent_variants/deepseek_harness/runtime/driver.mjs` | DeepSeek Harness 运行时的容器内驱动 |
| `scripts/build_office_v2_world.py` | 建场脚本（非 Campaign 入口） |

---

## 4. 依赖与运行时

### 4.1 运行时依赖（`pyproject.toml:11-18`）

| 依赖 | 约束 | 在代码中的角色 |
|---|---|---|
| `docker` | `>=7.0,<8` | 调度容器、镜像、卷；`v2_cli.py:169` 延迟导入 |
| `pydantic` | `>=2.6,<3` | 全部契约模型与内容摘要校验 |
| `fastapi` + `uvicorn` | `>=0.115` / `>=0.30` | Agent 侧运行时 API（`agent_image/app`，`tests/integration/test_runtime_api.py`） |
| `PyYAML` | `>=6,<7` | 读取 YAML 配置（见 §5.4 的死路径问题） |
| `structlog` | `>=24,<26` | 结构化日志 |

`requires-python = ">=3.11,<3.15"`。

### 4.2 开发依赖（`pyproject.toml:20-28`）

`httpx`、`pytest>=8`、`pytest-asyncio>=0.24`、`pytest-cov`、`pywin32`（仅 Windows）、`ruff>=0.4`。

### 4.3 pytest 配置（`pyproject.toml:36-41`）

```toml
testpaths = ["tests"]
pythonpath = ["src", "agent_image"]
asyncio_mode = "auto"
addopts = "-q --tb=short"
```

`asyncio_mode = "auto"` 意味着所有 `async def test_*` 自动被当作 asyncio 测试执行，无需 `@pytest.mark.asyncio`。

`pythonpath` 同时包含 `src` 与 `agent_image`，所以 Agent 侧应用代码与产品代码共享一个测试进程。

`addopts` 里的 `-q` 与 TASK 允许的额外参数叠加时会重复出现，这解释了本次运行中 pytest 从不打印传统汇总行的现象——**本次记录的通过依据是退出码 `0` 与进度符号计数，不是汇总文本**。

### 4.4 ruff 配置（`pyproject.toml:43-48`）

`target-version = "py311"`、`line-length = 100`、`select = ["E","F","I","UP","B","SIM","C4"]`。

### 4.5 容器侧依赖

| 文件 | 用途 |
|---|---|
| `agent_image/Dockerfile` | 基础 Agent 镜像 |
| `agent_image/Dockerfile.qwen` | Qwen 模型变体 |
| `agent_image/Dockerfile.qwen-external` | 连接宿主机 Ollama 的变体 |
| `agent_image/Dockerfile.qwen-mutator` | **Mutator 镜像**（`--mutator-image` 指向它） |
| `agent_image/Dockerfile.exploratory-source` | 探索性源镜像 |
| `agent_image/requirements.langgraph.lock`、`requirements.agent-qwen.lock` | 锁定依赖 |
| `controller_image/Dockerfile`、`requirements.txt` | 控制面镜像 |

---

## 5. 模块职责

`src/sandbox/` 有 10 个子包 + 14 个顶层模块。按在数据流中的角色分组如下。

### 5.1 子包清单

| 子包 | 文件数 | 行数 | 职责 |
|---|---|---|---|
| `scenarios/` | 60 | 43,688 | 场景定义（含 Office Workspace V2）、Oracle、工具契约 |
| `fuzzer/` | 44 | 14,505 | **Campaign 主链**：CLI、调度、选择、变异契约、持久化、报告 |
| `mutation/` | 27 | 4,886 | 语义变异 Provider（含 Docker/Ollama 实现） |
| `coverage/` | 26 | 8,852 | 行为/风险/联合覆盖提取与存储 |
| `replay/` | 11 | 1,728 | Artifact 保存、Manifest、重放引擎 |
| `scheduler/` | 4 | — | `DockerSandboxScheduler`：容器生命周期 |
| `engine/` | 4 | — | 执行引擎 |
| `client/` | 4 | — | `RuntimeClient`、`ArtifactTransfer`：与容器内 Agent 通信 |
| `storage/` | 2 | — | 存储辅助 |
| `scoring/` | 2 | — | `RuleBasedScorer`（规则打分器） |

### 5.2 `fuzzer/` 内部：V2 Campaign 主链模块

44 个文件中，`v2_*` 共 32 个。按调用顺序：

| 模块 | 职责 | 关键符号 |
|---|---|---|
| `v2_cli.py` | 入口、参数、装配依赖 | `main`、`_run_exploratory` |
| `v2_preflight.py` | 环境预检 | `run_preflight` |
| `v2_bootstrap.py` | 由 16 个根种子构造探索性语料库 | `build_exploratory_bootstrap` |
| `v2_seed_pools.py` | 风险类型、进度等级、冻结种子、种子池 | `RiskType`、`RiskProgressLevel`、`FrozenSeed`、`RiskSeedPool`、`build_initial_seed_catalog` |
| `v2_selection.py` | **选择算法**（随机/覆盖率引导共用的 RNG） | `independent_uniform_selection`、`campaign_seed` |
| `v2_risk_pool_scheduler.py` | 风险池调度 | `select_risk_pool_seed` |
| `v2_strategy.py` | 策略枚举 | `CampaignStrategy`（`RANDOM_INDEPENDENT` / `COVERAGE_GUIDED`） |
| `v2_campaign_loop.py` | 晋升与下一轮分配 | `promote_coverage_artifact`、`choose_next_allocation`、`record_parent_result` |
| `v2_campaign_state.py` | Campaign 状态快照与结算 | `V2CampaignStateSnapshot`、`settle_risk_progress`、`add_promoted_seed_to_catalog` |
| `v2_campaign_store.py` | SQLite 持久化（1,867 行，第二大文件） | `V2CampaignStore` |
| `v2_orchestrator.py` | 下一代决策 | `decide_next_generation` |
| `v2_runtime.py` | 编排循环 | `run_or_resume_campaign`、`_campaign_target_reached` |
| `v2_real_runtime.py` | **真实驱动器**（1,214 行） | `run_or_resume_exploratory_campaign` |
| `v2_real_episode.py` | 单 Episode 执行 | `DockerOfficeV2EpisodeRunner` |
| `v2_loop_contracts.py` | 闭环契约 | `ExecutionClosure`、`build_execution_closure_from_oracle` |
| `v2_feedback.py` | 反馈构造 | `FeedbackBrief`、`NextGenerationFeedback`、`FeedbackGapKind` |
| `v2_promotion.py` | 晋升分类 | `classify_v2_promotion` |
| `v2_corpus.py` | 语料库 | `V2Corpus` |
| `v2_target_oracle.py` / `v2_target_preservation.py` / `v2_target_judge.py` | 目标保持判定 | `OllamaTargetPreservationJudge` |
| `v2_agent_behavior.py` | Agent 行为特征 | — |
| `v2_work.py` | 工作单元与重试语义 | — |
| `v2_report.py` | 报告生成 | `build_v2_campaign_report`、`write_v2_campaign_report` |
| `v2_scheduler.py`、`v2_campaign.py`、`v2_metrics`（在 `metrics.py`） | 辅助 | — |

`fuzzer/` 中**非 `v2_` 前缀**的模块（`circuit_breaker.py`、`energy.py`、`engine.py`、`queue.py`、`seed_pool.py`、`soak.py`、`store.py`、`corpus.py` 等）属于更早的一代 fuzzer 实现。**V2 Campaign 主链不经过它们**（`E3`：`v2_cli.py` 的导入列表中无这些模块）。

### 5.3 `coverage/` 内部：两套并存的覆盖实现

`coverage/` 下同时存在两套模块：

| 集合 | 文件 |
|---|---|
| V2（本次 Campaign 主链使用） | `v2_input.py`、`v2_behavior.py`、`v2_episode_behavior.py`、`v2_episode_coverage.py`、`v2_risk_catalog.py`、`v2_risk_coverage.py`、`v2_tool_behavior.py`、`v2_unexpected_risk.py`、`v2_contracts.py` |
| 更早一代（V1） | `input.py`、`behavior.py`、`risk.py`、`risk_scope.py`、`taxonomy.py`、`store.py`、`feedback.py`、`heatmap.py`、`models.py`、`office_risk.py`、`office_evidence.py`、`correlation.py`、`events.py`、`evidence.py`、`feature_normalizer.py` |

两套都在 `coverage/__init__.py` 中导出。`v2_input.py` 是 V2 Campaign 进入覆盖计算的入口（`E3`）。

### 5.4 已确认的死路径：`config/risk-taxonomy.yaml`

全仓范围内：

- 该文件**没有任何代码引用**（无 `config/` 字符串出现在 `src/` 或 `agent_image/` 的 Python 中）；
- `src/` 中确有 4 处 `yaml.safe_load` 调用（`coverage/risk_scope.py:23`、`coverage/taxonomy.py:50`、`fuzzer/config.py:143`、`mutation/operators.py:98`），但它们接收的是调用方传入的路径，其中前两者（`RiskTaxonomyIndex`、`CampaignRiskScopeIndex`）属于 §5.3 的 **V1 覆盖集合**，V2 Campaign 主链不构造它们；
- V2 的风险分类法标识硬编码为字符串：`coverage/v2_contracts.py:72` 的 `"office-v2-risk-taxonomy-schema-v1"`。

结论（`E3`）：`config/risk-taxonomy.yaml` 是一个未接入的配置路径。这属于"命名/结构"类问题，见 `discussion-items.md` 最后一项。

### 5.5 Oracle 与 Judge 的位置

审计 Spec 的 `AUD-G-05` 要求说明 Oracle 与 Judge 的职责。代码中**不存在** `src/sandbox/oracle/` 或 `src/sandbox/judge/` 目录。实际位置：

| 角色 | 文件 |
|---|---|
| V2 场景 Oracle | `scenarios/office_v2/oracle.py`、`oracle_models.py`、`oracle_evidence.py`、`oracle_trace.py`、`security_oracle.py`、`utility_oracle.py` |
| 目标保持 Oracle / Judge | `fuzzer/v2_target_oracle.py`、`v2_target_preservation.py`、`v2_target_judge.py` |

即：**Oracle 与 Judge 都按场景实现，没有独立的跨场景抽象层**。职责划分的合规结论见 `spec-compliance.md`（`FR-JDG-01`、`FR-JDG-02`、`FR-JDG-03`）。

### 5.6 两个 Agent 运行时

| 运行时 | 位置 | 选择方式 |
|---|---|---|
| LangGraph ReAct（自研） | `agent_image/app/`（Python） | `--agent-runtime langgraph`（默认） |
| DeepSeek Harness | `agent_variants/deepseek_harness/`（Node） | `--agent-runtime deepseek_harness` |

选择通过环境变量 `TRACE_G_AGENT_RUNTIME` 传入容器（`v2_cli.py:210`），值来自 `AgentRuntimeKind` 枚举。

---

## 6. 建议的阅读顺序

对不熟悉本代码的读者，建议按以下顺序建立心智模型：

1. **本文档** §3.1 → `v2_cli.py` 的子命令与参数；
2. `docs/current-state/episode-data-flow.md` → 一个 Episode 从头到尾发生了什么；
3. `docs/current-state/strategy-comparison.md` → 两种策略在哪里分叉；
4. `docs/current-state/spec-compliance.md` → 与产品 SPEC 的逐条差距；
5. `docs/current-state/discussion-items.md` → 需要你决定的事。

---

## 7. 本文档的局限

- 所有结论基于单一基线 `a15b127`；若仓库后续有新提交，本文档不自动适用（`AUD-REQ-01`）。
- Docker / 真实模型路径未被实际执行，涉及它们的模块职责描述为 `E3`（静态代码证据），不是 `E1`。
- `tests/design/` 的 2 个文件未运行，其职责未核实（`E0`→未在下文断言）。
- `agent_image/app/` 的 45 个文件仅做了清单级核查，未逐一追踪（`AUD-G-05` 要求的 Agent 职责由 `episode-data-flow.md` 从调用侧说明）。
