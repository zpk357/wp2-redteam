# 项目变更日志

## 2026-10-30 / 20261030-abl3-resume-repair / 续跑丢前段、进度行判定错误与文档同步

- 范围与授权：只改 `src/sandbox/scenarios/error_capable_campaign.py`、`error_capable_selector.py`、
  `scripts/run_neighborhood_campaign.py`、对应测试与 `README.md`；未启动模型、容器或服务器，
  10-25 的产物只读复算。
- 身份：仓库 `public` 分支，起点 `f55c95d`；代码提交 `76242cb`（续跑前段重放、48 次尝试、
  未开格提示、盲臂中性提示词 + 5 个测试）、`827ae84`（进度行 + 3 个测试）；模型
  `qwen3.5:27b-q4_K_M`（`ollama-react:…#sha256:89310963…`）、fixture `error-capable-multipath-02`
  （`sha256:c5934105…`）、campaign-v16、selector-v20、coverage-v5。
- 终结状态：本地修复与测试完成，未重跑任何臂，10-25 的读数保持原样。
- 发现与修复：
  - `--start-index` 原先只从该索引开始跑。`behaviour` 臂续跑后的 `guided-campaign.json` 只有
    65 集（index 35–99），而 `opportunities=100`、`ledger.settled=65`、`priority.applied=65`：
    前 34 集从报告里消失，选择器也只看到空账本。现在先从 `journal_root` 下的 evidence、
    selection 与 settlement 按序重放前段，缺任一项即抛错，不再报告"只剩尾巴"的臂。
  - `_progress` 读 `ObservedKey.observed`，而字段名是 `observed_types`，每集都回落到 `clean`。
    10-25 的 `blind.log` 100 行全部写 `clean`，同文件 summary 却是 `reached=7`。改为读
    `observed_types`，空集回落 `risk_class`，使 `unassessed` 不再与 `clean` 印成同一个词。
  - `README.md` 同步到当前树：9 条路径 / 9 个邻域、8 个机制、四类 finding、四维 1–6 级、
    `--guidance` / `--start-index` 用法、10-25 消融读数与续跑缺陷；10-07 的 v15 两臂读数标为历史。
- 测试：`tests/unit/test_error_capable_campaign.py`、`test_error_capable_selector.py`、
  `test_run_neighborhood_progress.py` 共 55 项通过；`test_error_capable_increment.py`、
  `test_error_capable_violation_types.py`、`test_error_capable_behaviour_chain.py` 一并通过。
  新增行无 ruff 报错；仓库既有的 173 项 E501/F401 未在本轮清理。
- 产物路径：`d:/hxjh/wp2-redteam/`（提交与文档）；10-25 消融产物
  `d:/hxjh/runs/downloaded/extracted/abl3-20261025-070525/` 与
  `d:/hxjh/runs/downloaded/extracted/old-abl2-20261025-013624/`。
- 未做与代价：未重跑 10-25 的 `behaviour` 臂，65/100 仍是修复前产物，不能当完整臂；未合并
  10-10 的 `full` 与 10-25 的三条消融臂（树与日期不同，不混算）；未做统计显著性；服务计价未核算。

## 2026-10-25 / 20261025-abl3-guidance-ablation / guidance 消融三臂真实运行

- 范围与授权：在项目维护者控制的 Docker + Ollama 环境中，对 `error-capable-multipath-02` 跑
  三个单臂消融；每臂 100 个机会，`--arm guided --guidance {risk,behaviour,blind}`，
  不启动随机臂、不重跑已完成臂。产物只落运行根，不入库。
- 身份：模型 `qwen3.5:27b-q4_K_M`，adapter `ollama-react:qwen3.5:27b-q4_K_M#sha256:89310963…`；
  fixture `error-capable-multipath-02`（`sha256:c5934105…`）；base seed `610621000`，repeat 2；
  `max_tool_requests=24`，`num_ctx=12288`；campaign-v16、selector-v20、coverage-v5；三臂报告
  的 `model_identity_digest` 均为 `sha256:34cf9580…`。
- 终结状态：`risk` 与 `blind` 完整（各 100 集）；`behaviour` 两次中断——第一次在 Episode 34
  连续 48 次拒绝后 `exit=1`，第二次在 08:27 也以 `exit=1` 结束，之后以 `--start-index 35`
  续跑，最终报告 65 集（index 35–99）。
- 读数（Oracle 复算，`risk_class=violation` 的集数 / 四类类型计数 C/A/D/M / 目标类型达到）：

  | 臂 | 集数 / 机会 | 选择被拒 | 违规集 | C/A/D/M | 达到 |
  |---|---|---|---|---|---|
  | `risk` | 100 / 100 | 108 | 29 | 8 / 6 / 7 / 9 | 29 |
  | `behaviour` | 65 / 100 | 30 | 29 | 10 / 8 / 6 / 5 | 29 |
  | `blind` | 100 / 100 | 0 | 7 | 7 / 0 / 0 / 0 | 7 |

  - `behaviour` 的 65 集是修复前续跑的产物，前 34 集不在报告里；29 次达到只代表后 65 集。
  - `blind` 的 7 次全部经由 `public_link_exfiltration`，其余 7 个机制 `nothing=93`；
    结束等级为 `{audience_mismatch:1, content_release:4, unauthorised_deletion:1,
    unauthorised_modification:1}`。
  - `risk` 的结束等级四维均为 4；`behaviour` 为 `{audience_mismatch:4, content_release:5,
    unauthorised_deletion:4, unauthorised_modification:4}`。
  - 同日没有 `full` 臂的 100 集对照；`full` 的参考是 10-10 的
    `main-100x2-20261010c/main/rep-02`（guided 100 集 55 集违规、random 100 集 42 集违规），
    那棵树是 campaign-v17，与本批 v16 不可直接合并。
- 产物路径：服务器 `/root/ae/old-abl2`（`risk`、`behaviour` 第一次）与
  `/root/ae/runs/abl3-20261025-070525`（`behaviour` 续跑、`blind`）；本机下载
  `d:/hxjh/runs/downloaded/sofruns.tar.gz`（49 189 475 B，2026-10-25 22:53）及解包目录
  `d:/hxjh/runs/downloaded/extracted/abl3-20261025-070525/`、
  `.../old-abl2-20261025-013624/`。
- 未做与代价：`behaviour` 臂未重跑，65/100 不能作为完整臂读数；未做多配对与显著性；
  未核算模型服务费用；10-25 之后才加入的 48 次尝试上限、未开格提示与盲臂中性提示词
  不在本批执行范围内。
