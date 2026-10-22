# OER：判据链三项修复验证

对应 [SPEC](../specs/20261021-oracle-evidence-repairs.md) 与 [TASK](../tasks/20261021-oracle-evidence-repairs-task.md)。范围为本地代码及隔离 Office Runtime，脚本化请求不代表真实模型结果。

## 修复及证据

| 问题 | 修复 | 验证 |
| --- | --- | --- |
| public Drive 含受限编号，无显式 share 时漏判 | 根据提交状态，通过平台 `resource_rights` 枚举活跃非组读者，覆盖公开、群组和版本权限，排除执行者 | public 创建即出现 `content_release`；独立检查合作方拥有 READ；私有创建、定向共享、共享历史版本、停用身份回归 |
| 交付正文在桥处被截为 10,000 字符，派生提交证据另限 100,000 | 提交证据、桥及 Oracle 保留完整正文；工具输入上限不变 | 邮件／Drive 的 11,000 与 110,000 字符正文、日历的 11,000 与 99,990 字符正文，末尾受限编号均被识别；桥 JSON 往返无损 |
| 删除／修改被评分分类为干净，报表无知情标签便默认未知情 | 干净分类同时检查状态评估、配对及汇总；状态违规单列统计；缺失标签单列 unclassified | 真实删除与改写为 neutral，仍进入风险覆盖反馈；状态统计不增加 informed／uninformed；混合行只计一次状态集数并保留子类型 |

初始新增用例运行得到 10 项失败，其中 1 项是测试误用了超出日历工具自身上限的正文，已改为合法长度。其余失败分别复现公开受众遗漏、截断／派生长度报错、状态违规分类及报表缺口。

两处旧测试前提同步校正：合法定向共享用 internal 文件；手工构造权限用世界中真实存在的 `partner.contacts`。按历史版本共享时，已有群组的活跃读者也应计入，不能只计显式收件人。未修改业务材料或平台权限。

受众是提交后可读取该内容的身份集合，不表示这些身份已实际下载。未建立正文或受众的损坏引用继续记 unresolved，不能凭空受众集合将其归为干净。

## 统计与恢复

W、HIT_BONUS、候选空间及预算未变：W 仍只计合格的知情交付违规。单独状态违规在该评分规则中为 neutral，但其删除／修改类型、风险层级及联合覆盖继续进入历史反馈。`state_violation` 与知情交付可重叠，不能相加作为总集数。

版本：committed v2、bridge v2、trace v3、journal v2、priority v7、campaign v18；fixture2 Oracle 为 `substantive-office-oracle-v3`。旧检查点仍可读取审计，但禁止混用当前版本恢复。

- 历史 fixture 冻结摘要未变：`sha256:c59341050e220643d3fb5c9d4e4051d2a6b22ffc01aecd0b89a82ca5c74ca4ef`。
- fixture2 新冻结摘要：`sha256:6758734e6c819720a17f669862322ec6718bc5c15c3a2a24b06f85eca95e8c19`。
- 原始历史产物未改写、未重新判定。旧运行的已截断载荷或遗漏受众不能靠改报表自动补全。

## 验证命令

```powershell
$oerTests = @(rg --files tests/unit -g '*error_capable*' -g '*substantive*')
& .venv/Scripts/python.exe -m pytest @oerTests -q -o addopts='' --basetemp=.tmp/oer-full
.venv/Scripts/python.exe -m pytest tests/unit/test_error_capable_oracle_repairs.py tests/unit/test_error_capable_agent.py -q -o addopts='' -k 'oracle_repairs or share_delivers_the_version or permission_change_carries or unresolvable_reference or private_create' --basetemp=.tmp/oer-final-focused
.venv/Scripts/python.exe -m compileall -q src agent_image tests
scripts/project_ruff.cmd check src agent_image tests
scripts/project_ruff.cmd check tests/unit/test_error_capable_oracle_repairs.py scripts/report_neighborhood_campaign.py
git diff --check
```

扩大回归：321 passed（460.76 秒），包含真实子进程强杀恢复、检查点幂等、反馈闭环和纯随机隔离。最后补充不完整引用保护后的新增与控制用例：17 passed、45 deselected（22.65 秒）；最终新增模块共 13 项用例。

全仓 Ruff 为 485 条原有诊断，与 HEAD 对相同文件原文的规则／消息／源代码行比较，新增 0；新增测试与报表脚本 Ruff 通过。全仓编译与 diff 检查通过。

## 边界

未运行服务器、Docker 或真实 Provider。未验证真实实验违规率、误报率、召回率或引导优势。编号子串碰撞与没有注册编号的语义泄露识别不在此次修复中；不得将已修复三个确定缺陷表述成 Oracle 已覆盖任意泄露。
