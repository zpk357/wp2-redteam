"use strict";

const SCHEMA_VERSION = "office-v2-coverage-visualization-v2";
const TERMINAL_EVENTS = new Set(["execution_finished", "execution_error", "execution_timed_out", "execution_cancelled"]);

const state = {
  snapshot: null,
  selectedCampaignId: null,
  selectedView: "overview",
  selectedGeneration: 1,
  selectedEvent: 0,
  byteDigest: null,
  serverAvailable: true,
  playing: false,
  playTimer: null,
  syncTimer: null,
};

const $ = (selector) => document.querySelector(selector);
const elements = {
  agentInputSection: $("#agent-input-section"),
  agentTask: $("#agent-task"),
  autoSync: $("#auto-sync"),
  baselineCaseId: $("#baseline-case-id"),
  baselineStateDigest: $("#baseline-state-digest"),
  baselineTask: $("#baseline-task"),
  baselineView: $("#baseline-view"),
  behaviorFeatures: $("#behavior-features"),
  campaignSelect: $("#campaign-select"),
  campaignStatus: $("#campaign-status"),
  candidateContent: $("#candidate-content"),
  candidateId: $("#candidate-id"),
  coverageChart: $("#coverage-chart"),
  coverageMapBody: $("#coverage-map-body"),
  coverageTotals: $("#coverage-totals"),
  decisionDigest: $("#decision-digest"),
  decisionFlow: $("#decision-flow"),
  deliveredContent: $("#delivered-content"),
  deliveryHeading: $("#delivery-heading"),
  episodeSection: $("#episode-section"),
  eventDetail: $("#event-detail"),
  eventProgress: $("#event-progress"),
  executionId: $("#execution-id"),
  fileInput: $("#file-input"),
  generationCount: $("#generation-count"),
  generationEyebrow: $("#generation-eyebrow"),
  generationList: $("#generation-list"),
  generationStatus: $("#generation-status"),
  generationSubtitle: $("#generation-subtitle"),
  generationTitle: $("#generation-title"),
  generationView: $("#generation-view"),
  metricGrid: $("#metric-grid"),
  mutationMeta: $("#mutation-meta"),
  navigation: $("#page-navigation"),
  nonEpisodeReason: $("#non-episode-reason"),
  nonEpisodeSection: $("#non-episode-section"),
  notice: $("#notice"),
  overviewNavDetail: $("#overview-nav-detail"),
  overviewSubtitle: $("#overview-subtitle"),
  overviewView: $("#overview-view"),
  operatorChain: $("#operator-chain"),
  parentContent: $("#parent-content"),
  parentSeedId: $("#parent-seed-id"),
  parentSelection: $("#parent-selection"),
  providerAttempts: $("#provider-attempts"),
  railCampaignId: $("#rail-campaign-id"),
  railIdentity: $("#rail-identity"),
  railRuntime: $("#rail-runtime"),
  refreshButton: $("#refresh-button"),
  riskContexts: $("#risk-contexts"),
  seedCount: $("#seed-count"),
  seedPool: $("#seed-pool"),
  seedSettlement: $("#seed-settlement"),
  seedSettlementStatus: $("#seed-settlement-status"),
  selectionFacts: $("#selection-facts"),
  snapshotDigest: $("#snapshot-digest"),
  sourceBadge: $("#source-badge"),
  timelineList: $("#timeline-list"),
  timelineNext: $("#timeline-next"),
  timelinePlay: $("#timeline-play"),
  timelinePrev: $("#timeline-prev"),
  timelineSpeed: $("#timeline-speed"),
  toolPath: $("#tool-path"),
  validationBadge: $("#validation-badge"),
};

function display(value, fallback = "--") {
  return value === null || value === undefined || value === "" ? fallback : String(value);
}

function compactNumber(value) {
  if (!Number.isFinite(value)) return "--";
  return new Intl.NumberFormat("zh-CN", { notation: Math.abs(value) >= 10000 ? "compact" : "standard" }).format(value);
}

function durationLabel(milliseconds) {
  if (!Number.isFinite(milliseconds)) return "--";
  const totalSeconds = Math.round(milliseconds / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours) return `${hours}h ${minutes}m`;
  if (minutes) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

function shortDigest(value) {
  if (!value) return "--";
  const normalized = String(value);
  return normalized.length <= 23 ? normalized : `${normalized.slice(0, 13)}…${normalized.slice(-7)}`;
}

function runtimeLabel(runtime) {
  const kind = typeof runtime === "string" ? runtime : runtime?.kind;
  return kind === "deepseek_harness" ? "DeepSeek Harness" : kind === "langgraph" ? "LangGraph Agent" : display(kind);
}

function statusLabel(status) {
  const labels = {
    accepted: "候选已接受",
    committed: "已原子结算",
    complete: "已完成",
    completed: "已完成",
    candidate_rejected: "候选已拒绝",
    failed: "失败",
    paused: "已暂停",
    rejected: "候选已拒绝",
    running: "运行中",
    saturated: "覆盖已饱和",
    risk_seed: "风险种子",
    exploration_seed: "探索种子",
    finding_only: "仅保存 Finding",
    no_promotion: "不晋升",
    quarantined: "已隔离",
    non_episode: "未进入 Episode",
    promoted: "已晋升",
  };
  return labels[status] || display(status);
}

function create(tag, className, content) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content !== undefined) node.textContent = display(content);
  return node;
}

function setNotice(message, isError = false) {
  elements.notice.hidden = !message;
  elements.notice.textContent = message || "";
  elements.notice.classList.toggle("error", isError);
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function validateSnapshot(snapshot) {
  assert(snapshot && typeof snapshot === "object" && !Array.isArray(snapshot), "快照必须是 JSON 对象");
  assert(snapshot.schema_version === SCHEMA_VERSION, "不支持的覆盖率展示快照版本");
  assert(snapshot.source && typeof snapshot.source === "object", "快照缺少来源声明");
  if (snapshot.source.kind === "deterministic_fixture") {
    assert(snapshot.source.is_server_data === false, "本地 Fixture 必须明确声明非服务器数据");
  }
  assert(Array.isArray(snapshot.campaigns) && snapshot.campaigns.length > 0, "快照必须至少包含一个 Campaign");
  const campaignIds = new Set();
  for (const campaign of snapshot.campaigns) {
    assert(campaign && typeof campaign === "object", "Campaign 记录必须是对象");
    assert(typeof campaign.id === "string" && campaign.id.length > 0, "Campaign 缺少 ID");
    assert(!campaignIds.has(campaign.id), "Campaign ID 重复");
    campaignIds.add(campaign.id);
    const runtimeKind = typeof campaign.runtime === "string" ? campaign.runtime : campaign.runtime?.kind;
    assert(["deepseek_harness", "langgraph"].includes(runtimeKind), "Campaign Runtime 不受支持");
    assert(campaign.baseline && typeof campaign.baseline === "object", "Campaign 缺少初始基线");
    assert(Array.isArray(campaign.generations) && campaign.generations.length > 0, "Campaign 必须至少包含一代");
    assert(campaign.completed_generations === campaign.generations.length, "完成代数与代际记录数量不一致");
    let previousFeedback = null;
    let previousBehaviorTotal = 0;
    let previousRiskTotal = 0;
    campaign.generations.forEach((generation, index) => {
      const expectedNumber = index + 1;
      assert(generation.number === expectedNumber, "代次必须从 1 开始连续递增");
      assert(generation.internal_decision_index === index, "内部决策编号与展示代次不一致");
      assert(generation.decision && typeof generation.decision === "object", "代际缺少决策记录");
      if (index === 0) {
        assert(generation.decision.input_feedback_digest === null, "第一代不得伪造上一代 Feedback");
      } else {
        assert(generation.decision.input_feedback_digest === previousFeedback, "代际 Feedback 血缘不闭合");
      }
      assert(generation.feedback_output?.digest, "代际缺少输出 Feedback 摘要");
      previousFeedback = generation.feedback_output.digest;
      const coverage = generation.coverage;
      assert(coverage && typeof coverage === "object", "代际缺少 Coverage 记录");
      assert(Array.isArray(coverage.tool_path), "Coverage 缺少工具路径");
      assert(Array.isArray(coverage.behavior_features), "Coverage 缺少行为特征明细");
      assert(Array.isArray(coverage.risk_contexts), "Coverage 缺少风险上下文明细");
      assert(coverage.delta?.primary_behavior === coverage.behavior_features.length, "行为 Coverage 总数与明细不一致");
      assert(coverage.delta?.risk_contexts === coverage.risk_contexts.length, "风险 Coverage 总数与明细不一致");
      assert(coverage.cumulative?.primary_behavior === previousBehaviorTotal + coverage.delta.primary_behavior, "行为 Coverage 累计值不连续");
      assert(coverage.cumulative?.risk_contexts === previousRiskTotal + coverage.delta.risk_contexts, "风险 Coverage 累计值不连续");
      previousBehaviorTotal = coverage.cumulative.primary_behavior;
      previousRiskTotal = coverage.cumulative.risk_contexts;
      if (generation.settlement_kind === "candidate_settlement") {
        assert(generation.episode && Array.isArray(generation.episode.events), "已提交代际缺少 Episode 事件");
        assert(generation.episode.events.length > 0, "Episode 事件不能为空");
        const executionId = generation.episode.execution_id;
        const sequenceOrigin = generation.episode.events[0].sequence;
        assert(sequenceOrigin === 0 || sequenceOrigin === 1, "TraceEvent sequence 起点无效");
        generation.episode.events.forEach((event, eventIndex) => {
          assert(event.sequence === eventIndex + sequenceOrigin, "TraceEvent sequence 不连续");
          assert(event.execution_id === executionId, "TraceEvent execution_id 不一致");
        });
        assert(TERMINAL_EVENTS.has(generation.episode.events.at(-1).event_type), "Episode 缺少终止事件");
      } else if (generation.settlement_kind === "non_episode_settlement") {
        assert(generation.episode === null, "未启动 Agent 的结算不得包含 Episode");
        assert(generation.agent_input === null, "未启动 Agent 的结算不得包含 Agent 输入");
        assert((generation.coverage?.tool_path || []).length === 0, "未启动 Agent 的结算不得包含工具路径");
        assert((generation.coverage?.behavior_features || []).length === 0, "未启动 Agent 的结算不得包含行为特征");
        assert((generation.coverage?.risk_contexts || []).length === 0, "未启动 Agent 的结算不得包含风险上下文");
      } else {
        throw new Error("未知的代际结算类型");
      }
    });
  }
  if (snapshot.selected_campaign_id !== null && snapshot.selected_campaign_id !== undefined) {
    assert(campaignIds.has(snapshot.selected_campaign_id), "默认 Campaign 不存在");
  }
  return snapshot;
}

function selectedCampaign() {
  if (!state.snapshot) return null;
  return state.snapshot.campaigns.find((item) => item.id === state.selectedCampaignId) || state.snapshot.campaigns[0];
}

function selectedGeneration() {
  const campaign = selectedCampaign();
  return campaign?.generations.find((item) => item.number === state.selectedGeneration) || campaign?.generations.at(-1);
}

function canReplaceSnapshot(next, { allowDifferent = false } = {}) {
  if (!state.snapshot || allowDifferent) return true;
  const currentCampaign = selectedCampaign();
  const replacement = next.campaigns.find((item) => item.id === currentCampaign.id);
  assert(replacement, "自动更新拒绝了当前 Campaign 消失的快照");
  if (currentCampaign.identity_digest && replacement.identity_digest) {
    assert(currentCampaign.identity_digest === replacement.identity_digest, "自动更新拒绝了身份变化的同名 Campaign");
  }
  assert(replacement.completed_generations >= currentCampaign.completed_generations, "自动更新拒绝了代数回退");
  return true;
}

function applySnapshot(snapshot, digest, { allowDifferent = false } = {}) {
  const validated = validateSnapshot(snapshot);
  canReplaceSnapshot(validated, { allowDifferent });
  const previousCampaignId = state.selectedCampaignId;
  state.snapshot = validated;
  state.byteDigest = digest;
  const ids = new Set(validated.campaigns.map((item) => item.id));
  state.selectedCampaignId = ids.has(previousCampaignId)
    ? previousCampaignId
    : validated.selected_campaign_id || validated.campaigns[0].id;
  const campaign = selectedCampaign();
  if (!campaign.generations.some((item) => item.number === state.selectedGeneration)) {
    state.selectedGeneration = campaign.generations.at(-1).number;
  }
  stopTimeline();
  render();
}

function renderCampaignPicker() {
  elements.campaignSelect.replaceChildren();
  for (const campaign of state.snapshot.campaigns) {
    const option = document.createElement("option");
    option.value = campaign.id;
    option.textContent = `${runtimeLabel(campaign.runtime)} · ${campaign.id} · ${campaign.completed_generations} 代`;
    option.selected = campaign.id === state.selectedCampaignId;
    elements.campaignSelect.append(option);
  }
}

function renderSource() {
  const source = state.snapshot.source;
  const isFixture = source.kind === "deterministic_fixture";
  const verified = source.integrity_status === "verified_archive";
  elements.sourceBadge.textContent = isFixture ? "本地 Fixture · 非服务器数据" : verified ? "Campaign 归档已核验" : "来源未核验";
  elements.sourceBadge.className = `status-badge ${verified ? "verified" : "pending"}`;
  if (isFixture) setNotice(source.notice || "当前显示本地确定性 Fixture，不是服务器 Campaign 数据。", false);
}

function coverageNumbers(generation) {
  const coverage = generation.coverage || {};
  const behaviorFeatures = coverage.behavior_features || [];
  const riskContexts = coverage.risk_contexts || [];
  return {
    behaviorDelta: coverage.delta?.primary_behavior ?? coverage.delta?.behavior ?? behaviorFeatures.filter((item) => item.is_new !== false).length,
    behaviorTotal: coverage.cumulative?.primary_behavior ?? coverage.cumulative?.behavior ?? 0,
    riskDelta: coverage.delta?.risk_contexts ?? coverage.delta?.risk ?? riskContexts.filter((item) => item.is_new !== false).length,
    riskTotal: coverage.cumulative?.risk_contexts ?? coverage.cumulative?.risk ?? 0,
    behaviorBefore: (coverage.cumulative?.primary_behavior ?? coverage.cumulative?.behavior ?? 0) - (coverage.delta?.primary_behavior ?? coverage.delta?.behavior ?? behaviorFeatures.filter((item) => item.is_new !== false).length),
    riskBefore: (coverage.cumulative?.risk_contexts ?? coverage.cumulative?.risk ?? 0) - (coverage.delta?.risk_contexts ?? coverage.delta?.risk ?? riskContexts.filter((item) => item.is_new !== false).length),
  };
}

function renderNavigation() {
  const campaign = selectedCampaign();
  elements.railRuntime.textContent = runtimeLabel(campaign.runtime);
  elements.railCampaignId.textContent = campaign.id;
  elements.railCampaignId.title = campaign.id;
  elements.railIdentity.textContent = shortDigest(campaign.identity_digest);
  elements.railIdentity.title = campaign.identity_digest || "";
  elements.overviewNavDetail.textContent = `${campaign.completed_generations} 代 · ${campaign.valid_committed_episodes} Episode`;
  elements.generationCount.textContent = campaign.generations.length;
  elements.snapshotDigest.textContent = shortDigest(state.byteDigest || state.snapshot.source?.report_digest);
  elements.snapshotDigest.title = state.byteDigest || state.snapshot.source?.report_digest || "";

  elements.navigation.querySelectorAll("button[data-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === state.selectedView);
  });
  elements.generationList.replaceChildren();
  for (const generation of campaign.generations) {
    const numbers = coverageNumbers(generation);
    const button = create("button", `generation-tab ${generation.status || ""}${state.selectedView === "generation" && generation.number === state.selectedGeneration ? " active" : ""}`);
    button.type = "button";
    button.append(create("span", "generation-number", String(generation.number).padStart(2, "0")));
    const copy = create("span", "generation-copy");
    copy.append(create("strong", "", `Generation ${generation.number}`), create("small", "", statusLabel(generation.status)));
    button.append(copy, create("span", "generation-delta", generation.settlement_kind === "candidate_settlement" ? `+${numbers.behaviorDelta}` : "拒绝"));
    button.addEventListener("click", () => selectGeneration(generation.number));
    elements.generationList.append(button);
  }
}

function setView(view) {
  state.selectedView = view;
  stopTimeline();
  render();
  window.location.hash = view === "generation" ? `g-${state.selectedGeneration}` : view;
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function selectGeneration(number) {
  state.selectedGeneration = number;
  state.selectedEvent = 0;
  setView("generation");
}

function renderMetrics() {
  const campaign = selectedCampaign();
  const totals = coverageNumbers(campaign.generations.at(-1));
  const tokens = campaign.tokens || {};
  const metrics = [
    ["完成代数", `${campaign.completed_generations}/${campaign.requested_generations || campaign.completed_generations}`, statusLabel(campaign.status)],
    ["有效 Episode", compactNumber(campaign.valid_committed_episodes), `非 Episode / 失败 ${compactNumber(campaign.invalid_or_failed_attempts || 0)}`],
    ["行为覆盖", compactNumber(totals.behaviorTotal), `最近一代 +${totals.behaviorDelta}`],
    ["累计耗时", durationLabel(campaign.elapsed_ms), "已结算代际"],
    ["Tokens", compactNumber((tokens.agent || 0) + (tokens.mutator || 0)), `Agent ${compactNumber(tokens.agent || 0)} · Mutator ${compactNumber(tokens.mutator || 0)}`],
  ];
  elements.metricGrid.replaceChildren();
  for (const [label, value, detail] of metrics) {
    const metric = create("div", "metric");
    metric.append(create("span", "metric-label", label), create("strong", "metric-value", value), create("span", "metric-detail", detail));
    elements.metricGrid.append(metric);
  }
}

function drawCoverageChart() {
  const campaign = selectedCampaign();
  const canvas = elements.coverageChart;
  if (!canvas || canvas.offsetParent === null) return;
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  const width = Math.max(320, rect.width);
  const height = 260;
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  const context = canvas.getContext("2d");
  context.scale(ratio, ratio);
  context.clearRect(0, 0, width, height);
  const padding = { top: 18, right: 28, bottom: 36, left: 48 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const points = campaign.generations.map((generation) => ({ generation, ...coverageNumbers(generation) }));
  const maximum = Math.max(1, ...points.flatMap((item) => [item.behaviorTotal, item.riskTotal]));
  context.font = "10px Consolas";
  context.lineWidth = 1;
  for (let line = 0; line <= 4; line += 1) {
    const y = padding.top + (plotHeight * line) / 4;
    context.strokeStyle = "#dfe4df";
    context.beginPath();
    context.moveTo(padding.left, y);
    context.lineTo(width - padding.right, y);
    context.stroke();
    context.fillStyle = "#7b8680";
    context.fillText(String(Math.round(maximum * (1 - line / 4))), 5, y + 3);
  }
  const xAt = (index) => padding.left + (points.length === 1 ? plotWidth / 2 : (plotWidth * index) / (points.length - 1));
  const yAt = (value) => padding.top + plotHeight - (plotHeight * value) / maximum;
  const drawSeries = (key, color, radius) => {
    context.strokeStyle = color;
    context.fillStyle = color;
    context.lineWidth = 2.5;
    context.beginPath();
    points.forEach((point, index) => {
      const x = xAt(index);
      const y = yAt(point[key]);
      if (index === 0) context.moveTo(x, y); else context.lineTo(x, y);
    });
    context.stroke();
    points.forEach((point, index) => {
      const x = xAt(index);
      const y = yAt(point[key]);
      context.beginPath();
      context.arc(x, y, radius, 0, Math.PI * 2);
      context.fill();
      context.fillStyle = "#65716b";
      context.fillText(`G${point.generation.number}`, x - 7, height - 12);
      context.fillStyle = color;
    });
  };
  drawSeries("behaviorTotal", "#147052", 4);
  drawSeries("riskTotal", "#9a6008", 3);
}

function renderCompactList(container, entries, formatter, limit = 3) {
  container.replaceChildren();
  if (!entries.length) {
    container.append(create("span", "empty-inline", "无"));
    return;
  }
  const list = create("ul", "map-list");
  entries.slice(0, limit).forEach((entry) => list.append(formatter(entry)));
  container.append(list);
  if (entries.length > limit) {
    const details = create("details", "map-more");
    const summary = create("summary", "", `查看其余 ${entries.length - limit} 项`);
    const remainder = create("ul", "map-list");
    entries.slice(limit).forEach((entry) => remainder.append(formatter(entry)));
    details.append(summary, remainder);
    container.append(details);
  }
}

function riskLabel(context) {
  return [context.entry_kind, `${context.source_domain || context.source} → ${context.sink_domain || context.sink}`, context.sink_action, context.outcome]
    .filter(Boolean).join(" · ");
}

function renderCoverageMap() {
  const campaign = selectedCampaign();
  elements.coverageMapBody.replaceChildren();
  for (const generation of campaign.generations) {
    const coverage = generation.coverage || {};
    const numbers = coverageNumbers(generation);
    const row = document.createElement("tr");
    const generationCell = document.createElement("td");
    generationCell.append(create("strong", "map-generation", `G${generation.number}`), create("span", "map-status", statusLabel(generation.status)));
    const pathCell = document.createElement("td");
    const path = create("div", "inline-path");
    (coverage.tool_path || []).forEach((step, index) => {
      if (index) path.append(create("span", "path-arrow", "→"));
      path.append(create("span", "path-node", step.name || step.tool_name));
    });
    if (!path.children.length) path.append(create("span", "empty-inline", generation.settlement_kind === "non_episode_settlement" ? "Agent 未启动" : "无工具调用"));
    pathCell.append(path);
    const featuresCell = document.createElement("td");
    renderCompactList(featuresCell, coverage.behavior_features || [], (feature) => {
      const item = document.createElement("li");
      item.append(create("span", "kind", feature.kind), document.createTextNode(` ${display(feature.value)}`));
      return item;
    });
    const risksCell = document.createElement("td");
    renderCompactList(risksCell, coverage.risk_contexts || [], (context) => create("li", "", riskLabel(context)));
    const growthCell = document.createElement("td");
    const growth = create("div", "growth-pair");
    growth.append(create("strong", "", `${numbers.behaviorTotal}  (+${numbers.behaviorDelta})`), create("span", "", `风险 ${numbers.riskTotal}  (+${numbers.riskDelta})`));
    growthCell.append(growth);
    row.append(generationCell, pathCell, featuresCell, risksCell, growthCell);
    row.addEventListener("click", (event) => {
      if (event.target.closest("details")) return;
      selectGeneration(generation.number);
    });
    elements.coverageMapBody.append(row);
  }
}

function renderOverview() {
  const campaign = selectedCampaign();
  const modelName = typeof campaign.model === "string" ? campaign.model : campaign.model?.name;
  elements.overviewSubtitle.textContent = `${runtimeLabel(campaign.runtime)} · ${display(modelName)} · ${campaign.id}`;
  elements.campaignStatus.textContent = statusLabel(campaign.status);
  elements.campaignStatus.className = `status-badge ${campaign.status || "neutral"}`;
  renderMetrics();
  renderCoverageMap();
  window.requestAnimationFrame(drawCoverageChart);
}

function fact(label, value, asCode = false) {
  const node = create("div", "fact");
  node.append(create("span", "", label), create(asCode ? "code" : "strong", "", value));
  return node;
}

function renderBaseline() {
  const baseline = selectedCampaign().baseline;
  const selection = baseline.g1_selection || {};
  elements.baselineCaseId.textContent = display(baseline.scenario_case_id);
  elements.baselineTask.textContent = display(baseline.task_instruction);
  elements.baselineStateDigest.textContent = shortDigest(baseline.initial_state_digest);
  elements.baselineStateDigest.title = baseline.initial_state_digest || "";
  elements.selectionFacts.replaceChildren(
    fact("选中父种子", selection.parent_seed_id, true),
    fact("待探索 Frontier", selection.frontier_id, true),
    fact("支撑 Execution", selection.supporting_execution_id, true),
    fact("选择理由", (selection.reason_codes || []).join(" · ")),
  );
  const seeds = baseline.seed_pool || [];
  elements.seedCount.textContent = `${seeds.length} 条`;
  elements.seedPool.replaceChildren();
  for (const seed of seeds) {
    const row = create("article", `seed-row${seed.id === selection.parent_seed_id ? " selected" : ""}`);
    row.append(create("code", "", seed.id), create("span", "", seed.content), create("strong", "", seed.id === selection.parent_seed_id ? "G1 已选中" : display(seed.status, "可选")));
    elements.seedPool.append(row);
  }
}

function renderDecisionFlow(generation) {
  const decision = generation.decision || {};
  elements.decisionDigest.hidden = !decision.digest;
  elements.decisionDigest.textContent = shortDigest(decision.digest);
  elements.decisionDigest.title = decision.digest || "";
  const inputLabel = generation.number === 1 ? "冻结初始基线" : `Generation ${generation.number - 1} Feedback`;
  const previousFeedback = generation.number === 1 ? null : selectedCampaign().generations[generation.number - 2]?.feedback_output;
  const inputDetail = generation.number === 1
    ? "无上一代 feedback_digest"
    : `${display(previousFeedback?.gap_kind)} · ${display(previousFeedback?.summary)} · ${shortDigest(decision.input_feedback_digest)}`;
  const frontierKind = decision.frontier_kind || decision.coverage_dimension || decision.frontier_type;
  const target = decision.frontier_id || decision.target || (decision.frontier_cells || []).join(" · ");
  const targetDetail = [
    ...(decision.frontier_cells || []),
    ...(decision.uncovered_targets || []),
  ].filter(Boolean).join(" · ");
  const nodes = [
    ["输入反馈", inputLabel, inputDetail, "active"],
    ["Coverage 探索方向", display(frontierKind, "风险 / 行为 frontier"), targetDetail || display(target), "active"],
    ["选择理由", (decision.reason_codes || []).join(" · "), display(decision.supporting_execution_id, "无支撑 Execution"), "active"],
  ];
  elements.decisionFlow.replaceChildren();
  for (const [label, value, detail, className] of nodes) {
    const node = create("article", `decision-node ${className}`);
    node.append(create("span", "", label), create("strong", "", value), create("code", "", detail));
    elements.decisionFlow.append(node);
  }
}

function listText(value) {
  if (Array.isArray(value)) return value.filter((item) => item !== null && item !== undefined && item !== "").map((item) => typeof item === "object" ? JSON.stringify(item) : String(item)).join(" · ");
  return value;
}

function renderParentSelection(generation) {
  const decision = generation.decision || {};
  const mutation = generation.mutation || {};
  const parent = mutation.parent_seed || generation.parent_seed || generation.seed_selection?.parent_seed || {};
  const values = [
    ["父种子 ID", mutation.parent_seed_id || decision.selected_parent_seed_id || parent.id, true],
    ["种子来源", parent.source || parent.origin || mutation.parent_source || decision.supporting_execution_id, false],
    ["代际深度", parent.generation_depth ?? parent.depth ?? mutation.parent_generation_depth ?? mutation.generation_depth, false],
    ["载体 / 字段", [parent.carrier || mutation.carrier, parent.field_path || mutation.field_path].filter(Boolean).join(" · "), false],
    ["历史算子", listText(parent.operator_history || mutation.parent_operator_history || mutation.operator_history), false],
    ["支撑 Execution", decision.supporting_execution_id, true],
  ];
  elements.parentSelection.replaceChildren();
  const grid = create("div", "parent-fact-grid");
  for (const [label, value, asCode] of values) grid.append(fact(label, display(value, "归档未提供"), asCode));
  const content = create("div", "parent-seed-preview");
  content.append(create("span", "section-note", "本代实际送入变异器的父种子内容"), create("pre", "", mutation.parent_content || parent.content || "归档未提供"));
  elements.parentSelection.append(grid, content);
}

function renderSeedSettlement(generation) {
  const mutation = generation.mutation || {};
  const validation = mutation.validation || {};
  const promotion = generation.seed_promotion || generation.promotion || generation.corpus_settlement || mutation.promotion || {};
  const disposition = promotion.disposition || promotion.kind || promotion.status || generation.promotion_disposition;
  const label = disposition || (generation.settlement_kind === "non_episode_settlement" ? "non_episode" : "归档未提供");
  const statusClass = ["risk_seed", "exploration_seed", "promoted", "corpus_entry"].includes(disposition) ? "committed" : disposition === "quarantined" ? "failed" : "pending";
  elements.seedSettlementStatus.textContent = statusLabel(label);
  elements.seedSettlementStatus.className = `status-badge ${statusClass}`;
  elements.seedSettlement.replaceChildren();
  const grid = create("div", "settlement-fact-grid");
  const futureParent = promotion.selectable_as_parent ?? promotion.parent_eligible ?? ["risk_seed", "exploration_seed", "promoted", "corpus_entry"].includes(disposition);
  const values = [
    ["结算结果", label],
    ["是否进入可选种子池", futureParent === true ? "是，后续可作为父种子" : futureParent === false ? "否" : "归档未提供"],
    ["Finding", promotion.finding_id || generation.finding_id || promotion.finding_disposition],
    ["原因", listText(promotion.reason_codes || promotion.reasons || validation.reason_codes)],
  ];
  for (const [labelText, value] of values) grid.append(fact(labelText, display(value, "无"), false));
  const note = create("p", "settlement-note", futureParent === true
    ? "这个候选已经通过晋升门，后续代际可以重新选择它作为父种子。"
    : disposition === "finding_only"
      ? "本代只保存 Finding，不把候选加入可复用种子池。"
      : disposition === "quarantined" || generation.settlement_kind === "non_episode_settlement"
        ? "本代没有形成可复用种子；候选被隔离或在启动 Agent 前结束。"
        : "当前归档没有提供种子池晋升记录，不能推断它是否成为后续父种子。");
  elements.seedSettlement.append(grid, note);
}

function renderMutation(generation) {
  const mutation = generation.mutation || {};
  const validation = mutation.validation || {};
  elements.validationBadge.textContent = statusLabel(validation.status);
  elements.validationBadge.className = `status-badge ${validation.status || "neutral"}`;
  elements.parentSeedId.textContent = display(mutation.parent_seed_id);
  elements.candidateId.textContent = display(mutation.candidate_id);
  elements.parentContent.textContent = display(mutation.parent_content);
  elements.candidateContent.textContent = display(mutation.candidate_content);
  const operatorSource = Array.isArray(mutation.operator_plan?.steps)
    ? mutation.operator_plan.steps
    : Array.isArray(mutation.operator_families)
      ? mutation.operator_families
      : Array.isArray(mutation.operators)
        ? mutation.operators
        : [];
  const operators = operatorSource;
  const normalizedOperators = operators.map((operator, index) => {
    if (typeof operator === "string") {
      return { name: operator, order: index + 1 };
    }
    return {
      name: operator.name || operator.family || operator.operator || `operator-${index + 1}`,
      order: operator.order || operator.sequence || index + 1,
      reason: operator.reason || operator.selection_reason || operator.allocation_reason,
      changedFields: Array.isArray(operator.changed_fields)
        ? operator.changed_fields
        : Array.isArray(operator.changes)
          ? operator.changes
          : [],
      status: operator.status,
    };
  });
  normalizedOperators.sort((left, right) => left.order - right.order);
  elements.operatorChain.replaceChildren();
  if (!normalizedOperators.length) {
    elements.operatorChain.append(create("div", "empty-panel", "本代没有可记录的变异算子"));
  } else {
    const heading = create("div", "operator-chain-heading");
    heading.append(create("strong", "", `本代实际分配 ${normalizedOperators.length} 个算子`), create("span", "", "按执行顺序展示；未来可扩展为多算子组合"));
    const list = create("ol", "operator-chain-list");
    normalizedOperators.forEach((operator) => {
      const item = create("li", "operator-step");
      const marker = create("span", "operator-step-number", String(operator.order).padStart(2, "0"));
      const copy = create("div", "operator-step-copy");
      copy.append(create("strong", "", operator.name));
      const detail = [operator.status, operator.reason, operator.changedFields.length ? `改变：${operator.changedFields.join("、")}` : null].filter(Boolean).join(" · ");
      copy.append(create("span", "", detail || "由本代探索方向分配"));
      item.append(marker, copy);
      list.append(item);
    });
    elements.operatorChain.append(heading, list);
  }
  const meta = [
    ["目标", mutation.target],
    ["算子摘要", normalizedOperators.map((operator) => operator.name).join(" + ")],
    ["载体", mutation.carrier],
    ["字段", mutation.field_path],
    ["验证理由", (validation.reason_codes || []).join(" · ") || "通过"],
  ];
  elements.mutationMeta.replaceChildren();
  for (const [label, value] of meta) {
    const chip = create("span", "meta-chip");
    chip.append(create("strong", "", `${label} `), document.createTextNode(display(value)));
    elements.mutationMeta.append(chip);
  }
  const attempts = mutation.provider_attempts || [];
  elements.providerAttempts.replaceChildren();
  for (const attempt of attempts) {
    const row = create("div", "provider-attempt");
    row.append(create("strong", "", `Attempt ${display(attempt.attempt)}`), create("span", "", display(attempt.status)), create("code", "", attempt.response_digest || attempt.error || `${display(attempt.duration_ms)} ms`), create("span", "", `${compactNumber(attempt.tokens || 0)} tokens`));
    elements.providerAttempts.append(row);
  }
}

function renderAgentInput(generation) {
  const input = generation.agent_input;
  elements.agentInputSection.hidden = !input;
  if (!input) return;
  const delivery = input.candidate_delivery || {};
  elements.executionId.textContent = display(input.execution_id);
  elements.executionId.title = input.execution_id || "";
  elements.agentTask.textContent = display(input.task_instruction);
  elements.deliveryHeading.textContent = `${display(delivery.domain || delivery.resource_type, "模拟环境")} · ${display(delivery.resource_label || delivery.resource_id)} · ${display(delivery.field_path)}`;
  elements.deliveredContent.textContent = display(delivery.content);
}

const EVENT_TITLES = {
  agent_submit: "Agent 最终提交",
  execution_cancelled: "Episode 已取消",
  execution_error: "Episode 异常结束",
  execution_finished: "Episode 正常结束",
  execution_started: "Episode 启动",
  execution_timed_out: "Episode 超时",
  interaction_fact: "交互事实记录",
  model_end: "模型完成一次决策",
  model_start: "模型开始一次决策",
  scenario_initialized: "模拟办公状态初始化",
  scenario_state_observed: "场景状态观测",
  tool_call: "Agent 调用工具",
  tool_result: "工具结果返回 Agent",
};

function valuePreview(value, length = 120) {
  if (value === null || value === undefined) return "";
  const rendered = typeof value === "string" ? value : JSON.stringify(value);
  return rendered.length > length ? `${rendered.slice(0, length)}…` : rendered;
}

function eventTitle(event) {
  const data = event.data || {};
  if (event.event_type === "tool_call") return `调用 ${display(data.tool_name || data.name, "工具")}`;
  if (event.event_type === "tool_result") return `${display(data.tool_name || data.name, "工具")} 返回结果`;
  if (event.event_type === "model_start" || event.event_type === "model_end") {
    const turn = data.turn ?? data.model_turn;
    return `${turn ? `第 ${turn} 轮 · ` : ""}${EVENT_TITLES[event.event_type]}`;
  }
  return EVENT_TITLES[event.event_type] || event.event_type;
}

function eventSummary(event) {
  const data = event.data || {};
  if (event.summary) return event.summary;
  if (data.summary) return data.summary;
  if (event.event_type === "tool_call") return valuePreview(data.arguments || data.args || data.input);
  if (event.event_type === "tool_result") return valuePreview(data.result || data.output || data.content);
  if (event.event_type === "model_end") return valuePreview(data.response || data.content || data.decision);
  if (event.event_type === "agent_submit") return valuePreview(data.final_response || data.response || data.content);
  if (event.event_type === "scenario_state_observed") return valuePreview(data.observed_changes || data.state || data);
  return valuePreview(data.message || data.detail || data);
}

function renderEventDetail(event) {
  elements.eventDetail.replaceChildren();
  const header = create("div", "event-detail-header");
  const copy = document.createElement("div");
  copy.append(create("h3", "", eventTitle(event)), create("p", "", eventSummary(event) || "该事件没有附加文本。"));
  header.append(copy, create("code", "", `SEQ ${String(event.sequence).padStart(3, "0")}`));
  const facts = create("div", "event-facts");
  const values = [
    ["事件类型", event.event_type],
    ["来源", event.source],
    ["逻辑时间", event.logical_time],
    ["时间戳", event.timestamp],
    ["输入摘要", shortDigest(event.input_digest)],
    ["输出摘要", shortDigest(event.output_digest)],
    ["状态摘要", shortDigest(event.state_digest)],
    ["Checkpoint", shortDigest(event.checkpoint_id)],
  ];
  for (const [label, value] of values) {
    const item = create("div", "event-fact");
    item.append(create("span", "", label), create("code", "", value));
    facts.append(item);
  }
  const raw = create("details", "raw-event");
  raw.append(create("summary", "", "查看完整结构化事件"), create("pre", "", JSON.stringify(event, null, 2)));
  elements.eventDetail.append(header, facts, raw);
}

function renderTimeline(generation) {
  const events = generation.episode?.events || [];
  elements.timelineList.replaceChildren();
  state.selectedEvent = Math.max(0, Math.min(state.selectedEvent, Math.max(0, events.length - 1)));
  elements.eventProgress.textContent = events.length ? `${state.selectedEvent + 1} / ${events.length}` : "0 / 0";
  if (!events.length) {
    elements.timelineList.append(create("li", "empty-panel", "没有 Episode 事件"));
    elements.eventDetail.replaceChildren(create("div", "empty-panel", "该代际未启动 Agent"));
    return;
  }
  events.forEach((event, index) => {
    const item = create("li", `timeline-item${index === state.selectedEvent ? " active" : ""}`);
    item.dataset.sequence = event.sequence;
    item.append(create("span", "event-index", String(event.sequence).padStart(2, "0")));
    const copy = create("span", "event-copy");
    copy.append(create("strong", "", eventTitle(event)), create("span", "", eventSummary(event) || "无附加内容"));
    item.append(copy, create("code", "event-type", event.event_type));
    item.addEventListener("click", () => {
      state.selectedEvent = index;
      renderTimeline(generation);
    });
    elements.timelineList.append(item);
  });
  renderEventDetail(events[state.selectedEvent]);
  elements.timelineList.children[state.selectedEvent]?.scrollIntoView({ block: "nearest" });
}

function evidenceSequences(value) {
  return (value || []).map((ref) => typeof ref === "number" ? ref : ref.sequence).filter(Number.isInteger);
}

function evidenceLinks(refs, generation) {
  const wrapper = create("span", "evidence-links");
  for (const sequence of evidenceSequences(refs)) {
    const button = create("button", "evidence-link", `#${sequence}`);
    button.type = "button";
    button.title = `跳到 TraceEvent ${sequence}`;
    button.addEventListener("click", () => jumpToEvent(generation, sequence));
    wrapper.append(button, document.createTextNode(" "));
  }
  return wrapper;
}

function jumpToEvent(generationNumber, sequence) {
  const campaign = selectedCampaign();
  const generation = campaign.generations.find((item) => item.number === generationNumber);
  const index = generation?.episode?.events.findIndex((event) => event.sequence === sequence) ?? -1;
  if (index < 0) return;
  state.selectedGeneration = generationNumber;
  state.selectedView = "generation";
  state.selectedEvent = index;
  render();
  elements.episodeSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderToolPath(generation) {
  const path = generation.coverage?.tool_path || [];
  elements.toolPath.replaceChildren();
  if (!path.length) {
    elements.toolPath.append(create("li", "empty-panel", generation.settlement_kind === "non_episode_settlement" ? "Agent 未启动，因此没有工具路径" : "本 Episode 没有工具调用"));
    return;
  }
  for (const step of path) {
    const item = document.createElement("li");
    item.append(create("strong", "", step.name || step.tool_name), create("span", "", `结果：${display(step.outcome)}`), evidenceLinks(step.evidence_refs || step.evidence_sequences, generation.number));
    elements.toolPath.append(item);
  }
}

function renderBehaviorFeatures(generation) {
  const features = generation.coverage?.behavior_features || [];
  elements.behaviorFeatures.replaceChildren();
  if (!features.length) {
    elements.behaviorFeatures.append(create("div", "empty-panel", "本代没有新增行为特征"));
    return;
  }
  const grouped = Map.groupBy ? Map.groupBy(features, (item) => item.kind) : features.reduce((groups, item) => {
    if (!groups.has(item.kind)) groups.set(item.kind, []);
    groups.get(item.kind).push(item);
    return groups;
  }, new Map());
  for (const [kind, entries] of grouped) {
    const group = create("section", "feature-group");
    group.append(create("h4", "", `${kind} · ${entries.length}`));
    for (const feature of entries) {
      const row = create("div", "feature-row");
      row.append(create("code", "", feature.value), evidenceLinks(feature.evidence_refs || feature.evidence_sequences, generation.number));
      group.append(row);
    }
    elements.behaviorFeatures.append(group);
  }
}

function renderRiskContexts(generation) {
  const contexts = generation.coverage?.risk_contexts || [];
  elements.riskContexts.replaceChildren();
  if (!contexts.length) {
    elements.riskContexts.append(create("div", "empty-panel", "本代没有新增风险上下文"));
    return;
  }
  for (const context of contexts) {
    const panel = create("article", "risk-context");
    panel.append(create("strong", "", display(context.label, riskLabel(context))));
    const facts = document.createElement("dl");
    const values = [
      ["入口", context.entry_kind],
      ["来源域", context.source_domain || context.source],
      ["目标域", context.sink_domain || context.sink],
      ["动作", context.sink_action],
      ["载体", context.carrier],
      ["授权分支", context.authorization_branch],
      ["路径性质", context.planned === true ? "planned" : context.planned === false ? "unexpected" : null],
      ["结果", context.outcome],
    ];
    for (const [label, value] of values) {
      facts.append(create("dt", "", label), create("dd", "", value));
    }
    panel.append(facts, evidenceLinks(context.evidence_refs || context.evidence_sequences, generation.number));
    elements.riskContexts.append(panel);
  }
}

function renderCoverageContribution(generation) {
  const numbers = coverageNumbers(generation);
  elements.coverageTotals.replaceChildren();
  for (const [label, value] of [["行为前值", numbers.behaviorBefore], ["行为增量", `+${numbers.behaviorDelta}`], ["行为后值", numbers.behaviorTotal], ["风险前值", numbers.riskBefore], ["风险增量", `+${numbers.riskDelta}`], ["风险后值", numbers.riskTotal]]) {
    const item = create("span", "coverage-total");
    item.append(create("span", "", label), create("strong", "", value));
    elements.coverageTotals.append(item);
  }
  renderToolPath(generation);
  renderBehaviorFeatures(generation);
  renderRiskContexts(generation);
}

function renderGeneration() {
  const generation = selectedGeneration();
  state.selectedGeneration = generation.number;
  const numbers = coverageNumbers(generation);
  elements.generationEyebrow.textContent = `GENERATION ${String(generation.number).padStart(2, "0")} · DECISION ${generation.internal_decision_index}`;
  elements.generationTitle.textContent = `Generation ${generation.number}`;
  elements.generationSubtitle.textContent = generation.settlement_kind === "candidate_settlement"
    ? `${generation.episode.events.length} 个实际 TraceEvent · 行为新增 ${numbers.behaviorDelta} · 风险上下文新增 ${numbers.riskDelta}`
    : "候选在宿主验证阶段结算，没有启动 Agent Episode。";
  elements.generationStatus.textContent = statusLabel(generation.status);
  elements.generationStatus.className = `status-badge ${generation.status || "neutral"}`;
  renderDecisionFlow(generation);
  renderParentSelection(generation);
  renderMutation(generation);
  renderAgentInput(generation);
  const hasEpisode = generation.settlement_kind === "candidate_settlement";
  elements.episodeSection.hidden = !hasEpisode;
  elements.nonEpisodeSection.hidden = hasEpisode;
  if (hasEpisode) renderTimeline(generation);
  else elements.nonEpisodeReason.textContent = (generation.mutation?.validation?.reason_codes || []).join(" · ") || "候选未通过宿主验证。";
  renderCoverageContribution(generation);
  renderSeedSettlement(generation);
}

function render() {
  if (!state.snapshot) return;
  renderCampaignPicker();
  renderSource();
  renderNavigation();
  elements.overviewView.hidden = state.selectedView !== "overview";
  elements.baselineView.hidden = state.selectedView !== "baseline";
  elements.generationView.hidden = state.selectedView !== "generation";
  if (state.selectedView === "overview") renderOverview();
  else if (state.selectedView === "baseline") renderBaseline();
  else renderGeneration();
}

function stopTimeline() {
  state.playing = false;
  elements.timelinePlay.textContent = "▶";
  elements.timelinePlay.title = "播放时间线";
  if (state.playTimer) window.clearInterval(state.playTimer);
  state.playTimer = null;
}

function stepTimeline(direction) {
  const events = selectedGeneration()?.episode?.events || [];
  if (!events.length) return;
  state.selectedEvent = Math.max(0, Math.min(events.length - 1, state.selectedEvent + direction));
  renderTimeline(selectedGeneration());
  if (direction > 0 && state.selectedEvent === events.length - 1) stopTimeline();
}

function toggleTimeline() {
  if (state.playing) {
    stopTimeline();
    return;
  }
  const events = selectedGeneration()?.episode?.events || [];
  if (!events.length) return;
  if (state.selectedEvent >= events.length - 1) state.selectedEvent = 0;
  state.playing = true;
  elements.timelinePlay.textContent = "Ⅱ";
  elements.timelinePlay.title = "暂停时间线";
  renderTimeline(selectedGeneration());
  state.playTimer = window.setInterval(() => stepTimeline(1), Number(elements.timelineSpeed.value));
}

async function loadServerSnapshot({ manual = false } = {}) {
  try {
    const response = await fetch("/api/snapshot", { cache: "no-store" });
    if (!response.ok) throw new Error(`快照服务返回 ${response.status}`);
    const digest = response.headers.get("X-Snapshot-Digest");
    if (!manual && digest && digest === state.byteDigest) return;
    const parsed = await response.json();
    applySnapshot(parsed, digest);
    state.serverAvailable = true;
    if (manual && parsed.source?.kind !== "deterministic_fixture") setNotice("已读取最新 Campaign 快照。", false);
  } catch (error) {
    state.serverAvailable = false;
    setNotice(`无法读取快照：${error.message}`, true);
  }
}

async function importSnapshot(file) {
  try {
    const parsed = JSON.parse(await file.text());
    applySnapshot(parsed, null, { allowDifferent: true });
    setNotice(`已在浏览器中读取 ${file.name}；不会写入 Campaign。`, false);
  } catch (error) {
    setNotice(`导入失败：${error.message}`, true);
  } finally {
    elements.fileInput.value = "";
  }
}

function updateSyncTimer() {
  if (state.syncTimer) window.clearInterval(state.syncTimer);
  state.syncTimer = null;
  if (elements.autoSync.checked) {
    state.syncTimer = window.setInterval(() => loadServerSnapshot(), 5000);
  }
}

elements.navigation.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-view]");
  if (button) setView(button.dataset.view);
});
elements.campaignSelect.addEventListener("change", () => {
  state.selectedCampaignId = elements.campaignSelect.value;
  state.selectedView = "overview";
  state.selectedGeneration = selectedCampaign().generations.at(-1).number;
  state.selectedEvent = 0;
  render();
});
elements.refreshButton.addEventListener("click", () => loadServerSnapshot({ manual: true }));
elements.fileInput.addEventListener("change", () => {
  const [file] = elements.fileInput.files;
  if (file) importSnapshot(file);
});
elements.autoSync.addEventListener("change", updateSyncTimer);
elements.timelinePrev.addEventListener("click", () => stepTimeline(-1));
elements.timelineNext.addEventListener("click", () => stepTimeline(1));
elements.timelinePlay.addEventListener("click", toggleTimeline);
elements.timelineSpeed.addEventListener("change", () => {
  if (state.playing) {
    stopTimeline();
    toggleTimeline();
  }
});
window.addEventListener("resize", () => {
  if (state.selectedView === "overview") window.requestAnimationFrame(drawCoverageChart);
});
window.addEventListener("hashchange", () => {
  const hash = window.location.hash.slice(1);
  if (hash === "overview" || hash === "baseline") setView(hash);
  else if (/^g-\d+$/.test(hash)) selectGeneration(Number(hash.slice(2)));
});

loadServerSnapshot({ manual: true });
updateSyncTimer();
