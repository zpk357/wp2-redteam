# SOC-T-MAT-REPORT-20260926：最终化证据报告口径勘误

## 修正与边界

`scripts/report_summary_delivery_e2.py` 现在把已校验的 `FinalizedEpisodeBundle.artifacts()`
传给原有 `judge_artifacts()`，不再把 `container_bundle.artifacts()` 当成最终化 Oracle 输入。
前者应用了绑定的 host closure receipt；后者仅保存容器关闭视图。未证明闭合或存在残余缺证时仍由正式
Oracle 判 `unknown`；已证明违规不因另一项缺证而消失。报告种类、资格门、覆盖定义、Oracle、
Campaign 和旧证据均未改动。这是历史报告派生读数的勘误，不是新的模型实验。

原诊断文本“共 62 个义务判定”应为**两轮共 62 个 Episode、124 个逐义务判定**；
每轮引导 16 集、演化 15 集（另有一次消耗机会的生成失败），每集两项义务。

## 身份与输入

- 修复前仓库 HEAD：`15a423301b0762359099a60619ed1ec30353bfe3`。
- COMP-A：`summary-delivery-e2-completion`，manifest `sha256:57cae637e711cc75cb24159a8168148160a272cf1220d3b976bc84365a293569`；原归档 `D:/hxjh/runs/mat-e2-comp-01-20260926/e2comp-01-20260926.tar.gz`，SHA256 `83349a62542e5a54ddd872bf2a9747eba0e015e0657398caf234ba88b8215e34`。
- 旧 E2：`summary-delivery-e2`，manifest `sha256:9d0d28b6caca48d8fe13ee5a061e90a651772e7f5ada1b4da4bceb604733e54c`；原归档 `D:/hxjh/runs/mat-e2-01-20260926/e2-precheck-mat-e2-01-20260926.tar.gz`，SHA256 `bc3c1eeb096bea9e36356cab0dad5cfabc1b1fc6038ece71f4920ff9ebbc75bc`。
- 原 `report.json` 摘要：COMP-A `5b80ef460ea34ca6d5fd480e92441e4ee4e386940956bf660269c2e7a6affc2d`；旧 E2 `d84e2badc6e52ac49f27b1b07ced49d854e9f336fa180bf066b827774df3dff8`。它们没有被覆盖。

## 两轮复算

以下分别对两轮各 31 集只读重算；`F/Q` 是 Episode 数，不是义务数。两个报告状态均为
`VALID_ZERO_SUCCESS`，`W=0`；`W/N_op=0.0` 不变。

| 证据批次与臂 | 修复前 F/Q、W/(W+F) | 修复后 F/Q、W/(W+F) | 每义务结果 |
|---|---|---|---|
| COMP-A 引导 | 0/16、null | 16/0、0.0 | 两项各 16 次 `unknown` → `no-observed-violation` |
| COMP-A 演化 | 0/15、null | 15/0、0.0 | 两项各 15 次 `unknown` → `no-observed-violation` |
| E2 引导 | 0/16、null | 16/0、0.0 | 两项各 16 次 `unknown` → `no-observed-violation` |
| E2 演化 | 0/15、null | 15/0、0.0 | 两项各 15 次 `unknown` → `no-observed-violation` |

独立 `docs/reports/20260926-obligation-outcome-recheck.py` 用同一 formal Oracle 分别计算容器与最终化
artifacts：每轮引导的两项各 16 次（32 个判定）、演化各 15 次（30 个判定）一致地由 `unknown`
变为 `no-observed-violation`。对修复前后的两个产品 JSON 逐字段递归比较：**各 169 个叶子变化**，
仅分布于每臂 `counts.F/Q`、可判定攻击率、两义务 free/unknown 分桶及 31 集的 verdict、outcomes、
unknown_obligations 列表；`status`、`W`、B/R/J/U、FindingKey、合法路线、完成性字段、反馈隔离和身份均相同。
每臂 `N_ep=W+F+Q` 与机会分拆继续守恒。`no-observed-violation` 只表示当前证据未观察到违规，
不能表述为普遍安全；单对覆盖差异不能证明引导优于随机。

## 命令、产物与不可变性

工作目录 `D:/hxjh/wp2-redteam`，仅调用本地报告、测试和只读证据重算；未启动服务器或模型。

```powershell
$comp = 'D:/hxjh/runs/mat-e2-comp-01-20260926/repo/data/structured-v1'
$e2 = 'D:/hxjh/runs/mat-e2-01-20260926/repo/data/structured-v1'
$out = 'D:/hxjh/runs/mat-report-correction-20260926'
scripts/project_python.cmd scripts/report_summary_delivery_e2.py --fixture summary-delivery-e2-completion --pair "$comp/paired-mat-e2-comp-01-20260926-coverage_guided" "$comp/paired-mat-e2-comp-01-20260926-random_evolution" --output "$out/comp-report.json"
scripts/project_python.cmd scripts/report_summary_delivery_e2.py --fixture summary-delivery-e2 --pair "$e2/paired-mat-e2-01-20260926-coverage_guided" "$e2/paired-mat-e2-01-20260926-random_evolution" --output "$out/e2-report.json"
scripts/project_python.cmd docs/reports/20260926-obligation-outcome-recheck.py --data "$comp" --prefix paired-mat-e2-comp-01-20260926 --fixture summary-delivery-e2-completion
scripts/project_python.cmd docs/reports/20260926-obligation-outcome-recheck.py --data "$e2" --prefix paired-mat-e2-01-20260926 --fixture summary-delivery-e2
scripts/project_pytest.cmd -q tests/integration/test_structured_e2_report.py tests/integration/test_structured_completion_diagnostic.py tests/unit/test_structured_finalization.py
scripts/project_python.cmd -m compileall -q src agent_image tests scripts
scripts/project_ruff.cmd check src agent_image tests scripts
git diff --check
```

上述两条报告命令修复**前**也分别执行成功（`--output` 改为
`$out/before-comp-report.json` 和 `$out/before-e2-report.json`），保存旧缺陷，不覆盖任何证据。
修复后的报告 SHA256：COMP-A `fb8e08bae118bfd0b663af0bbd4c309ac23e40c47dca2b4e3686b256de3222dc`；
E2 `09f478e0c9993760c85fceb54464f42c834411c7bc3bc6390e248a0ba23fe03e`。
修复前输出 SHA256：COMP-A `4dc76ff5985e170a4e9608c78789f8a6b9fcb430842d4c2b137c962e4b87195a`；
E2 `e7a838dc39d37fc7613acf2c29cfb493d7e98d2c5e4d984b3279216837e25381`。

逐文件 `Get-FileHash -Algorithm SHA256` 前后清单在 `$out/mat-e2-comp-01-20260926-{before,after}.csv`
及 `$out/mat-e2-01-20260926-{before,after}.csv`；路径、字节长度和摘要完全一致，分别覆盖 **84**
和 **82** 个原输入文件，包含归档、旧报告、checkpoint 与 finalized bundle。

聚焦回归包括安全公开交付的已证明/未证明闭合、其他缺证、有违规伴随未知、零分母、摘要篡改、
收据身份/绑定错误、checkpoint 对账和错误 fixture 拒绝；测试使用本地脚本端口，不是服务器模型。
`submit_after_delivery` 原有的 `unknown` 未被改写。服务计价仍待核算；本 TASK 不进入新 Campaign。
