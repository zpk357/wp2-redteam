# agentfuzz — 工具链 LLM Agent 自动化注入测试

对"会调用工具的 LLM Agent"做自动化安全测试：**静态发现攻击链 → 构造基线/注入
双组场景 → 真实模型执行 → 差分验证器自动判定危害是否可归因 → 覆盖反馈调度下一轮**。

授权范围见 [`AUTHORIZED-EVALUATION-SCOPE.md`](AUTHORIZED-EVALUATION-SCOPE.md)
（硬边界）：只处理维护者控制的合成环境与合成数据，所有副作用在沙箱内，不连接
任何真实服务或公网目标。

## 四个自动环节

1. **链发现**（`src/agentfuzz/analysis/`）：静态分析工具模块，提取 Sink 站点与
   "源→中间→Sink"攻击链（当前工具集：12 工具 / 4 类 Sink / 162 链）；
2. **注入与执行**（`src/agentfuzz/execution/` + `validation/scenario_manager.py`）：
   沙箱内物化基线/注入双组载体，真实 LLM Agent（LangGraph/langchain，Ollama）
   执行，全量轨迹带稳定 digest；
3. **差分验证**（`src/agentfuzz/validation/`）：三类效果探针（shell/fetch/file），
   L1 目标链可达 / L2 参数受影响 / L3 效果确认——**基线自发率必须为 0**；
4. **覆盖反馈调度**（`src/agentfuzz/feedback/`）：行为/风险六元组覆盖键、五级
   阶段诊断、六项加权调度评分（非 RL、调度阶段零模型调用）。

## 实验数据（qwen3.5:27b，138 集零失败）

- 差分归因 L3：基线 0 命中 vs 注入 **4/4 命中**（CMDi/SSRF/文件写三类全确认）
- 可重复性：同验收集 **5/5**，trace_digest 逐位一致
- 单集成本 **~4.9K token**（详见 `docs/reports/20261115-project-summary.md`）

## 运行

```bash
# 单集验收（真 LLM，三步链 + 轨迹落盘）
python scripts/run_agentfuzz_episode.py --endpoint http://127.0.0.1:11434

# 差分案例（基线/注入双组 + L1/L2/L3 判定）
python scripts/run_agentfuzz_case.py

# 四臂消融（A Lite / B 行为 / C 双覆盖 / D 完整）
python scripts/run_agentfuzz_ablation.py

# 预算消融（固定预算 + 弱指令难案例）
python scripts/run_budget_ablation.py
```

测试：`python -m pytest tests/unit -q`（18 项）。
