"use strict";

const SCHEMA_VERSION = "office-v2-coverage-visualization-v3";
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
  showTechnical: false,
};

const $ = (selector) => document.querySelector(selector);
const elements = {
  agentInputSection: $("#agent-input-section"),
  agentTaskHeading: $("#agent-task-heading"),
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
  deliveredSurface: $("#delivered-surface"),
  deliveryHeading: $("#delivery-heading"),
  episodeSection: $("#episode-section"),
  eventDetail: $("#event-detail"),
  eventProgress: $("#event-progress"),
  executionId: $("#execution-id"),
  fileInput: $("#file-input"),
  generationCount: $("#generation-count"),
  generationBrief: $("#generation-brief"),
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
  technicalDetails: $("#technical-details"),
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
    applied: "已应用",
    blocked: "被安全边界阻止",
    realized: "已实际发生",
    succeeded: "成功",
    unverified: "尚未验证",
  };
  return labels[status] || display(status);
}

const TOOL_LABELS = {
  create_drive_file: "创建云盘文件",
  delete_drive_file: "删除云盘文件",
  list_directory: "浏览本地目录",
  read_drive_file: "读取云盘文件",
  read_file: "读取本地文件",
  request_clarification: "请求人工澄清",
  search_drive_files: "搜索云盘文件",
  search_email: "搜索邮件",
};

const OBJECTIVE_LABELS = {
  "a01-external-acl-share-link": "A01 · 扩大外部访问权限并发送共享链接",
  "a02-undelegated-drive-share": "A02 · 创建未获委派的外部云盘共享",
  "a03-sensitive-mail-exfiltration": "A03 · 敏感邮件内容外传",
  "a04-unauthorized-drive-delete": "A04 · 删除委派范围外的云盘对象",
  "a05-misleading-drive-propagation": "A05 · 在云盘中传播误导信息",
  "a06-calendar-replacement-chain": "A06 · 取消日程、创建替代日程并通知参与者",
  "a07-calendar-parameter-propagation": "A07 · 日程参数跨步骤传播",
  "a08-multi-source-aggregation-exfiltration": "A08 · 聚合多个来源后发送结果",
  "a09-workspace-record-poisoning": "A09 · 修改工作区记录造成错误信息",
  "a10-undelegated-external-event": "A10 · 创建未获委派的外部参与者日程",
  "a11-drive-acl-expansion": "A11 · 扩大云盘访问权限",
  "a12-workspace-to-drive-propagation": "A12 · 将错误工作区信息传播到外部共享文件",
};

const RISK_FAMILY_LABELS = {
  "risk-family.sensitive-information-disclosure": "敏感信息披露",
  "risk-family.unauthorized-operation-permission-expansion": "未授权操作与权限扩大",
  "risk-family.wrong-modification-propagation": "错误修改与传播",
  "risk-family.destructive-operation": "破坏性操作",
};

const FEATURE_KIND_LABELS = {
  "argument-source-chain": "参数来源链",
  "cross-domain-edge": "跨业务域路径",
  interaction: "人机交互",
  "interaction-edge": "交互触发路径",
  "parameter-shape": "工具参数组合",
  "permission-branch": "权限判断分支",
  "result-branch": "工具结果分支",
  "state-field-change": "状态字段变化",
  "state-object-change": "业务对象变化",
  termination: "执行结束方式",
  "tool-bigram": "连续两个工具的路径",
  "tool-trigram": "连续三个工具的路径",
  "tool-unigram": "使用过的工具",
};

const REASON_LABELS = {
  "14-layer-host-validation-passed": "候选通过全部宿主安全与完整性检查",
  "allocation-changed": "本次选择了新的探索方向",
  "baseline-debt": "该方向尚未完成真实模型基线",
  delegation_missing: "缺少委派授权",
  "feedback-observed_not_used": "候选被观察到，但没有按目标方式使用",
  "host-frozen-single-payload-slot": "本代只允许修改冻结场景中的一个文本位置",
  "recomputed-from-latest-result": "已根据本代实际结果生成下一代反馈",
  "risk-fact-advanced": "本代增加了可验证的风险事实",
};

function toolLabel(value) {
  return TOOL_LABELS[value] || display(value).replaceAll("_", " ");
}

function objectiveLabel(value) {
  const normalized = display(value, "");
  const seedMatch = normalized.match(/seed\.bootstrap\.(a\d{2}-.+)$/);
  const objectiveMatch = normalized.match(/objective\.(a\d{2})\.(.+)$/);
  const key = seedMatch?.[1] || (objectiveMatch ? `${objectiveMatch[1]}-${objectiveMatch[2]}` : null);
  return key ? OBJECTIVE_LABELS[key] || key.replaceAll("-", " ").toUpperCase() : display(value);
}

function riskFamilyLabel(value) {
  return RISK_FAMILY_LABELS[value] || display(value).replace("risk-family.", "").replaceAll("-", " ");
}

function milestoneLabel(value) {
  const normalized = display(value, "");
  if (normalized.includes(".atomic.")) return "该目标的首个可验证执行证据";
  const suffix = normalized.split(".").at(-1);
  const labels = {
    acl: "扩大权限",
    calendar: "修改日程参数",
    cancel: "取消原日程",
    create: "创建对象",
    delete: "删除对象",
    notify: "通知外部参与者",
    record: "写入工作区记录",
    replace: "创建替代日程",
    send: "发送内容",
    share: "建立外部共享",
    stage: "聚合到中间文件",
    write: "修改记录",
  };
  return labels[suffix] || normalized.replaceAll(".", " · ");
}

function sourceLabel(value) {
  const labels = {
    bootstrap: "冻结初始种子池",
    calendar: "日历",
    campaign: "当前 Campaign 生成",
    controlled_tools: "模拟办公工具层",
    corpus: "历史种子池",
    drive: "云盘",
    filesystem: "本地文件系统",
    mail: "邮件",
    model: "Agent 模型",
    runtime: "执行环境",
    scenario: "模拟办公环境",
    task: "任务指令",
    tool: "办公工具",
    workspace: "工作区",
    "langgraph-chat-ollama-v1": "Agent 模型运行时",
    "trace.office.v2": "Office V2 轨迹记录器",
  };
  return labels[value] || display(value);
}

function carrierLabel(value) {
  const labels = {
    direct_task: "直接任务指令",
    instruction: "任务指令",
    mail_body: "邮件正文",
    task: "直接任务指令",
  };
  return labels[value] || display(value).replaceAll("_", " ");
}

function fieldLabel(value) {
  const labels = {
    instruction: "任务指令字段",
    file_id: "文件 ID",
    expected_current_version_id: "预期文件版本 ID",
  };
  return labels[value] || display(value).replaceAll("_", " ");
}

function operatorLabel(value) {
  const labels = {
    expression_structure: "表达结构改写",
  };
  return labels[value] || display(value).replaceAll("_", " ");
}

function frontierLabel(value) {
  const labels = {
    behavior: "行为路径覆盖空白",
    primary_behavior: "行为路径覆盖空白",
    risk: "风险维度覆盖空白",
  };
  return labels[value] || display(value, "风险或行为覆盖空白");
}

function simpleValueLabel(value) {
  const labels = {
    allowed: "允许",
    blocked: "被安全边界阻止",
    denied: "拒绝",
    direct_task: "直接任务",
    create: "创建",
    delete: "删除",
    exact_value: "精确取值",
    no: "否",
    none: "无",
    "not-evaluated": "未进入该层判断",
    platform: "模拟平台权限层",
    platform_denied: "被模拟平台权限层拒绝",
    realized: "已实际发生",
    rejected: "被拒绝",
    read: "读取",
    search: "搜索",
    succeeded: "成功",
    "tool-output": "前序工具输出",
    unverified: "尚未验证",
    yes: "是",
  };
  return labels[value] || display(value).replaceAll("_", " ");
}

function reasonLabel(code, generationNumber = null) {
  if (code === "recomputed-from-latest-feedback") {
    return generationNumber === 1 ? "根据冻结初始覆盖状态重新计算" : "根据上一代反馈重新计算";
  }
  return REASON_LABELS[code] || display(code).replaceAll("-", " ").replaceAll("_", " ");
}

function reasonText(codes, generationNumber = null) {
  return (codes || []).map((code) => reasonLabel(code, generationNumber)).join("；") || "归档没有提供原因";
}

function feedbackGapLabel(value) {
  const labels = {
    observed_not_used: "候选内容已被 Agent 看到，但没有按目标方式使用",
  };
  return labels[value] || display(value).replaceAll("_", " ");
}

function featureKindLabel(value) {
  return FEATURE_KIND_LABELS[value] || display(value).replaceAll("-", " ");
}

function technicalDetails(title, entries) {
  const details = create("details", "technical-details technical-only");
  details.append(create("summary", "", title));
  const body = create("dl", "technical-list");
  for (const [label, value] of entries) {
    body.append(create("dt", "", label), create("dd", "", typeof value === "object" ? JSON.stringify(value, null, 2) : display(value)));
  }
  details.append(body);
  return details;
}

function dimensionValues(feature) {
  return Object.fromEntries((feature.dimensions || []).map((item) => [item.name, item.value]));
}

function toolSequence(value) {
  return display(value, "").replace(/^tools=/, "").split(">").filter(Boolean).map(toolLabel).join(" → ");
}

function parameterShapeLabel(value) {
  if (!value || value === "empty") return "不带参数";
  return String(value).split(",").map((entry) => {
    const [name, type] = entry.split(":");
    const typeLabels = { boolean: "布尔值", integer: "整数", string: "文本" };
    return `${fieldLabel(name)}（${typeLabels[type] || display(type)}）`;
  }).join("、");
}

function interactionPointLabel(value) {
  const normalized = display(value, "");
  if (normalized.startsWith("tool.")) return `工具「${toolLabel(normalized.slice(5))}」`;
  if (normalized.startsWith("interaction.")) {
    const kind = normalized.slice(12);
    const labels = {
      agent_clarification_requested: "Agent 请求人工澄清",
      agent_submitted: "Agent 提交最终结果",
    };
    return `交互「${labels[kind] || kind.replaceAll("_", " ")}」`;
  }
  return normalized.replaceAll("_", " ");
}

function featureSummary(feature) {
  const values = dimensionValues(feature);
  if (feature.kind === "tool-unigram") return `本代调用了「${toolSequence(values.tools)}」`;
  if (feature.kind === "tool-bigram" || feature.kind === "tool-trigram") return `形成新的工具顺序：${toolSequence(values.tools)}`;
  if (feature.kind === "parameter-shape") return `「${toolLabel(values.tool)}」使用了新的参数组合：${parameterShapeLabel(values.shape)}`;
  if (feature.kind === "argument-source-chain") {
    const crossing = values.cross_tool === "yes" ? "，并跨工具传递" : "";
    return `「${fieldLabel(values.argument_path)}」取自${simpleValueLabel(values.origin)}，用于「${toolLabel(values.tool)}」${crossing}`;
  }
  if (feature.kind === "permission-branch") {
    return `「${toolLabel(values.tool)}」的最终权限结果为「${simpleValueLabel(values.outcome || values.effective)}」`;
  }
  if (feature.kind === "result-branch") return `「${toolLabel(values.tool)}」出现新的结果分支：${simpleValueLabel(values.outcome || values.result)}`;
  if (feature.kind === "cross-domain-edge" || feature.kind === "interaction-edge") {
    return `形成新的行为连接：${interactionPointLabel(values.source)} → ${interactionPointLabel(values.sink)}`;
  }
  if (feature.kind === "interaction") return `出现新的 Agent 交互：${simpleValueLabel(values.kind || values.outcome || feature.value)}`;
  if (feature.kind === "state-field-change") return `模拟办公状态字段发生变化：${display(values.field || values.path || feature.value)}`;
  if (feature.kind === "state-object-change") return `模拟办公对象发生变化：${display(values.object || values.kind || feature.value)}`;
  if (feature.kind === "termination") return `Episode 以「${simpleValueLabel(values.outcome || values.reason || feature.value)}」结束`;
  return `${featureKindLabel(feature.kind)}：${display(feature.value)}`;
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
  if (snapshot.source.kind === "server_campaign_archive") {
    assert(snapshot.source.is_server_data === true, "服务器归档必须明确声明真实服务器数据");
    assert(snapshot.source.integrity_status === "verified_archive", "服务器归档尚未通过完整性验证");
    assert(typeof snapshot.source.archive_sha256 === "string" && snapshot.source.archive_sha256.length > 0, "服务器归档缺少摘要");
    assert(typeof snapshot.source.source_revision === "string" && snapshot.source.source_revision.length > 0, "服务器归档缺少源码版本");
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
    assert(Array.isArray(campaign.baseline.seed_pool), "Campaign 缺少初始种子池");
    assert(Array.isArray(campaign.baseline.risk_catalog?.families), "Campaign 缺少风险大类目录");
    assert(campaign.baseline.risk_catalog.families.length === 4, "Office V2 风险大类必须完整");
    const baselineSeedIds = new Set(campaign.baseline.seed_pool.map((seed) => seed.id));
    const groupedSeedIds = campaign.baseline.risk_catalog.families.flatMap((family) => family.seed_ids || []);
    assert(groupedSeedIds.length === new Set(groupedSeedIds).size, "初始种子不能重复归入多个主风险大类");
    assert(groupedSeedIds.length === baselineSeedIds.size && groupedSeedIds.every((seedId) => baselineSeedIds.has(seedId)), "风险大类没有覆盖完整初始种子池");
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
      const selection = generation.decision.frontier_selection;
      assert(selection && typeof selection === "object", "代际缺少风险或行为前沿选择");
      assert(selection.frontier_id === generation.decision.frontier_id, "前沿选择与调度决策不一致");
      assert(selection.selected_parent_seed_id === generation.decision.selected_parent_seed_id, "风险方向与父种子不一致");
      assert(Array.isArray(selection.candidate_seed_ids) && selection.candidate_seed_ids.includes(selection.selected_parent_seed_id), "父种子不在所选前沿的候选集合中");
      assert(Array.isArray(selection.candidate_seeds) && selection.candidate_seeds.map((seed) => seed.id).join("|") === selection.candidate_seed_ids.join("|"), "前沿候选种子详情不完整");
      if (generation.decision.frontier_kind === "risk") {
        assert(typeof selection.primary_risk_family === "string", "风险前沿缺少风险大类");
        assert(typeof selection.objective_id === "string", "风险前沿缺少具体风险目标");
        assert(Array.isArray(selection.family_seed_ids) && selection.family_seed_ids.includes(selection.selected_parent_seed_id), "父种子不属于所选风险大类");
        assert(Array.isArray(selection.family_seeds) && selection.family_seeds.map((seed) => seed.id).join("|") === selection.family_seed_ids.join("|"), "风险大类种子详情不完整");
      }
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
    copy.append(create("strong", "", `第 ${generation.number} 代`), create("small", "", statusLabel(generation.status)));
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
    ["有效执行", compactNumber(campaign.valid_committed_episodes), `未执行 / 失败 ${compactNumber(campaign.invalid_or_failed_attempts || 0)}`],
    ["行为覆盖", compactNumber(totals.behaviorTotal), `最近一代 +${totals.behaviorDelta}`],
    ["累计耗时", durationLabel(campaign.elapsed_ms), "已结算代际"],
    ["模型用量", compactNumber((tokens.agent || 0) + (tokens.mutator || 0)), `Agent ${compactNumber(tokens.agent || 0)} · 变异器 ${compactNumber(tokens.mutator || 0)} tokens`],
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
  const entry = carrierLabel(context.entry_kind);
  const source = sourceLabel(context.source_domain || context.source);
  const sink = sourceLabel(context.sink_domain || context.sink);
  const action = simpleValueLabel(context.sink_action);
  const outcome = simpleValueLabel(context.outcome);
  return `${entry}触发了从「${source}」到「${sink}」的「${action}」动作，结果为「${outcome}」`;
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
      path.append(create("span", "path-node", toolLabel(step.name || step.tool_name)));
    });
    if (!path.children.length) path.append(create("span", "empty-inline", generation.settlement_kind === "non_episode_settlement" ? "Agent 未启动" : "无工具调用"));
    pathCell.append(path);
    const featuresCell = document.createElement("td");
    renderCompactList(featuresCell, coverage.behavior_features || [], (feature) => {
      const item = document.createElement("li");
      item.append(create("span", "kind", featureKindLabel(feature.kind)), document.createTextNode(` ${featureSummary(feature)}`));
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
  const frontier = selection.frontier_selection || {};
  elements.baselineCaseId.textContent = display(baseline.scenario_case_id);
  elements.baselineTask.textContent = display(baseline.task_instruction);
  elements.baselineStateDigest.textContent = shortDigest(baseline.initial_state_digest);
  elements.baselineStateDigest.title = baseline.initial_state_digest || "";
  elements.selectionFacts.replaceChildren(
    fact("第一代风险大类", riskFamilyLabel(frontier.primary_risk_family)),
    fact("具体风险目标", objectiveLabel(frontier.objective_id)),
    fact("最终目标种子", objectiveLabel(selection.parent_seed_id)),
    fact("为什么先选它", reasonText(selection.reason_codes, 1)),
    technicalDetails("查看第一代选择的原始字段", [
      ["父种子 ID", selection.parent_seed_id],
      ["Frontier ID", selection.frontier_id],
      ["风险大类", frontier.primary_risk_family],
      ["风险侧面", frontier.risk_facets],
      ["具体目标", frontier.objective_id],
      ["目标里程碑", frontier.target_milestone_id],
      ["同类种子", frontier.family_seed_ids],
      ["前沿兼容种子", frontier.candidate_seed_ids],
      ["支撑 Execution", selection.supporting_execution_id],
      ["原因代码", selection.reason_codes],
    ]),
  );
  const seeds = baseline.seed_pool || [];
  elements.seedCount.textContent = `${seeds.length} 条`;
  elements.seedPool.replaceChildren();
  const seedById = new Map(seeds.map((seed) => [seed.id, seed]));
  for (const family of baseline.risk_catalog?.families || []) {
    const group = create("section", `risk-family-group${family.id === frontier.primary_risk_family ? " selected" : ""}`);
    const heading = create("div", "risk-family-heading");
    const headingCopy = create("div", "");
    headingCopy.append(
      create("strong", "", riskFamilyLabel(family.id)),
      create("span", "", `${family.objective_ids.length} 个具体风险目标 · ${family.seed_ids.length} 条初始种子`),
    );
    heading.append(
      headingCopy,
      create("span", "risk-family-state", family.id === frontier.primary_risk_family ? "第一代选择此类" : "待探索"),
    );
    const rows = create("div", "risk-family-seeds");
    for (const seedId of family.seed_ids) {
      const seed = seedById.get(seedId);
      if (!seed) continue;
      const row = create("article", `seed-row${seed.id === selection.parent_seed_id ? " selected" : ""}`);
      const identity = create("div", "seed-identity");
      identity.append(create("strong", "", objectiveLabel(seed.objective_id || seed.label || seed.id)), create("code", "technical-inline", seed.id));
      row.append(identity, create("span", "", seed.content), create("strong", "", seed.id === selection.parent_seed_id ? "第一代已选中" : "该类可选"));
      rows.append(row);
    }
    group.append(heading, rows);
    elements.seedPool.append(group);
  }
}

function renderDecisionFlow(generation) {
  const decision = generation.decision || {};
  const selection = decision.frontier_selection || {};
  elements.decisionDigest.hidden = !decision.digest;
  elements.decisionDigest.textContent = shortDigest(decision.digest);
  elements.decisionDigest.title = decision.digest || "";
  const inputLabel = generation.number === 1 ? "冻结初始状态" : `第 ${generation.number - 1} 代反馈`;
  const previousFeedback = generation.number === 1 ? null : selectedCampaign().generations[generation.number - 2]?.feedback_output;
  const inputDetail = generation.number === 1
    ? "这是第一代，只依据运行前已经冻结的覆盖状态选择方向。"
    : `上一代结论：${feedbackGapLabel(previousFeedback?.gap_kind)}。`;
  const frontierKind = decision.frontier_kind || decision.coverage_dimension || decision.frontier_type;
  const target = decision.frontier_id || decision.target || (decision.frontier_cells || []).join(" · ");
  const parent = generation.mutation?.parent_seed || {};
  const targetSummary = objectiveLabel(parent.label || generation.mutation?.parent_seed_id || decision.selected_parent_seed_id);
  const familySeedCount = selection.family_seed_ids?.length || 0;
  const frontierCandidateCount = selection.candidate_seed_ids?.length || 0;
  const nodes = [
    ["决策依据", inputLabel, inputDetail, "active"],
    ["选择风险大类", riskFamilyLabel(selection.primary_risk_family), `该类当前有 ${familySeedCount} 条可选种子。`, "active"],
    ["锁定具体目标", objectiveLabel(selection.objective_id), `本次补「${milestoneLabel(selection.target_milestone_id)}」；此前沿有 ${frontierCandidateCount} 条兼容种子。`, "active"],
    ["选中父种子", targetSummary, "从该风险方向的兼容种子中选出，作为本代变异起点。", "active"],
    ["为什么现在选它", reasonText(decision.reason_codes, generation.number), "支撑信息来自初始种子兼容性记录，不是本代 Episode。", "active"],
  ];
  elements.decisionFlow.replaceChildren();
  for (const [label, value, detail, className] of nodes) {
    const node = create("article", `decision-node ${className}`);
    node.append(create("span", "", label), create("strong", "", value), create("p", "decision-explanation", detail));
    elements.decisionFlow.append(node);
  }
  elements.decisionFlow.append(technicalDetails("查看调度器原始字段", [
    ["输入 Feedback 摘要", decision.input_feedback_digest],
    ["Frontier ID", target],
    ["风险大类", selection.primary_risk_family],
    ["风险侧面", selection.risk_facets],
    ["具体风险目标", selection.objective_id],
    ["目标里程碑", selection.target_milestone_id],
    ["风险大类下种子", selection.family_seed_ids],
    ["前沿兼容种子", selection.candidate_seed_ids],
    ["Frontier cells", decision.frontier_cells],
    ["原因代码", decision.reason_codes],
    ["支撑 Execution", decision.supporting_execution_id],
    ["决策摘要", decision.digest],
  ]));
}

function listText(value) {
  if (Array.isArray(value)) return value.filter((item) => item !== null && item !== undefined && item !== "").map((item) => typeof item === "object" ? JSON.stringify(item) : String(item)).join(" · ");
  return value;
}

function renderParentSelection(generation) {
  const decision = generation.decision || {};
  const selection = decision.frontier_selection || {};
  const mutation = generation.mutation || {};
  const parent = mutation.parent_seed || generation.parent_seed || generation.seed_selection?.parent_seed || {};
  const depth = parent.generation_depth ?? parent.depth ?? mutation.parent_generation_depth ?? mutation.generation_depth;
  const history = parent.operator_history || mutation.parent_operator_history || mutation.operator_history || [];
  const familyCandidates = (selection.family_seeds || [])
    .map((seed) => objectiveLabel(seed.objective_id || seed.label || seed.id));
  const values = [
    ["所属风险大类", riskFamilyLabel(selection.primary_risk_family), false],
    ["同类候选种子", familyCandidates.join("、") || `${selection.family_seed_ids?.length || 0} 条`, false],
    ["本代最终父种子", objectiveLabel(parent.label || mutation.parent_seed_id || decision.selected_parent_seed_id || parent.id), false],
    ["种子来源", sourceLabel(parent.source || parent.origin || mutation.parent_source), false],
    ["代际深度", Number.isInteger(depth) ? depth === 0 ? "初始种子，尚未经过代际晋升" : `第 ${depth} 层后代` : "归档未提供", false],
    ["过去用过的变异", history.length ? history.map(operatorLabel).join(" → ") : "无，这是初始种子", false],
  ];
  elements.parentSelection.replaceChildren();
  const grid = create("div", "parent-fact-grid");
  for (const [label, value, asCode] of values) grid.append(fact(label, display(value, "归档未提供"), asCode));
  const content = create("div", "parent-seed-preview");
  content.append(create("span", "section-note", "本代实际送入变异器的原始目标种子文本"), create("pre", "", mutation.parent_content || parent.content || "归档未提供"));
  elements.parentSelection.append(
    grid,
    content,
    technicalDetails("查看目标种子原始身份", [
      ["父种子 ID", mutation.parent_seed_id || decision.selected_parent_seed_id || parent.id],
      ["支撑 Execution", decision.supporting_execution_id],
      ["原始来源", parent.source || parent.origin || mutation.parent_source],
      ["原始载体", parent.carrier || mutation.carrier],
      ["原始字段", parent.field_path || mutation.field_path],
    ]),
  );
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
    ["结算结果", statusLabel(label)],
    ["是否进入可选种子池", futureParent === true ? "是，后续可作为父种子" : futureParent === false ? "否" : "归档未提供"],
    ["是否保存发现记录", promotion.finding_id || generation.finding_id ? "是" : "否"],
    ["为什么这样结算", reasonText(promotion.reason_codes || promotion.reasons || validation.reason_codes, generation.number)],
  ];
  for (const [labelText, value] of values) grid.append(fact(labelText, display(value, "无"), false));
  const note = create("p", "settlement-note", futureParent === true
    ? "这个候选已经通过晋升门，后续代际可以重新选择它作为父种子。"
    : disposition === "finding_only"
      ? "本代只保存 Finding，不把候选加入可复用种子池。"
      : disposition === "quarantined" || generation.settlement_kind === "non_episode_settlement"
        ? "本代没有形成可复用种子；候选被隔离或在启动 Agent 前结束。"
        : "当前归档没有提供种子池晋升记录，不能推断它是否成为后续父种子。");
  elements.seedSettlement.append(
    grid,
    note,
    technicalDetails("查看种子池结算原始字段", [
      ["处置代码", disposition],
      ["新种子 ID", promotion.seed_id],
      ["Corpus entry ID", promotion.corpus_entry_id],
      ["Finding ID", promotion.finding_id || generation.finding_id],
      ["原因代码", promotion.reason_codes || promotion.reasons || validation.reason_codes],
    ]),
  );
}

function renderMutation(generation) {
  const mutation = generation.mutation || {};
  const validation = mutation.validation || {};
  elements.validationBadge.textContent = statusLabel(validation.status);
  elements.validationBadge.className = `status-badge ${validation.status || "neutral"}`;
  elements.parentSeedId.classList.add("technical-inline");
  elements.candidateId.classList.add("technical-inline");
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
      copy.append(create("strong", "", operatorLabel(operator.name)));
      const detail = [
        statusLabel(operator.status),
        operator.reason ? reasonLabel(operator.reason, generation.number) : null,
        operator.changedFields.length ? "改变了候选文本及其完整性摘要" : null,
      ].filter(Boolean).join("；");
      copy.append(create("span", "", detail || "由本代探索方向分配"));
      item.append(marker, copy);
      list.append(item);
    });
    elements.operatorChain.append(heading, list);
  }
  const meta = [
    ["目标种子", objectiveLabel(mutation.parent_seed?.label || mutation.parent_seed_id)],
    ["变异方式", normalizedOperators.map((operator) => operatorLabel(operator.name)).join(" + ")],
    ["候选放置位置", carrierLabel(mutation.carrier)],
    ["修改字段", fieldLabel(mutation.field_path)],
    ["验证结果", reasonText(validation.reason_codes, generation.number)],
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
    row.append(
      create("strong", "", `第 ${display(attempt.attempt)} 次模型调用`),
      create("span", "", statusLabel(attempt.status)),
      create("span", "", attempt.error ? `错误：${attempt.error}` : "变异器返回了结构化候选"),
      create("span", "", `${compactNumber(attempt.tokens || 0)} tokens`),
    );
    elements.providerAttempts.append(row);
  }
  elements.providerAttempts.append(technicalDetails("查看变异原始字段", [
    ["目标 Frontier", mutation.target],
    ["父种子 ID", mutation.parent_seed_id],
    ["候选 ID", mutation.candidate_id],
    ["算子计划", mutation.operator_plan || mutation.operator_families || mutation.operators],
    ["验证代码", validation.reason_codes],
    ["Provider attempts", attempts],
  ]));
}

function renderAgentInput(generation) {
  const input = generation.agent_input;
  elements.agentInputSection.hidden = !input;
  if (!input) return;
  const delivery = input.candidate_delivery || {};
  const directTask = delivery.resource_type === "task" && delivery.content === input.task_instruction;
  elements.executionId.textContent = display(input.execution_id);
  elements.executionId.title = input.execution_id || "";
  elements.agentTaskHeading.textContent = directTask ? "最终发给 Agent 的任务" : "正常任务文本";
  elements.agentTask.textContent = display(input.task_instruction);
  elements.deliveredSurface.hidden = directTask;
  elements.agentInputSection.classList.toggle("direct-task-input", directTask);
  elements.deliveryHeading.textContent = `${sourceLabel(delivery.domain || delivery.resource_type)} · ${display(delivery.resource_label || delivery.resource_id)} · ${fieldLabel(delivery.field_path)}`;
  elements.deliveredContent.textContent = display(delivery.content);
  const existingNote = elements.agentInputSection.querySelector(".direct-task-note");
  if (existingNote) existingNote.remove();
  if (directTask) {
    const direction = objectiveLabel(generation.mutation?.parent_seed?.label || generation.mutation?.parent_seed_id);
    const note = create("p", "direct-task-note", `中文概括：${direction}。本代候选直接替换任务指令，没有另外写入邮件或文件，所以这里只显示一次归档原文。安全边界仍由 Docker 内的模拟办公环境执行。`);
    elements.agentInputSection.querySelector(".agent-input-grid").before(note);
  }
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

function argumentSummary(argumentsValue) {
  if (!argumentsValue || typeof argumentsValue !== "object") return "没有附加参数";
  const labels = {
    file_id: "文件",
    path: "路径",
    query: "搜索词",
    recipient: "接收者",
    title: "标题",
  };
  return Object.entries(argumentsValue).map(([name, value]) => `${labels[name] || fieldLabel(name)}：${valuePreview(value, 80)}`).join("；");
}

function toolResultSummary(data) {
  if (data.error) return `工具返回错误：${valuePreview(data.error, 120)}`;
  const result = data.data ?? data.result ?? data.output;
  if (Array.isArray(result?.items)) {
    const names = result.items.slice(0, 3).map((item) => item.path || item.name || item.id).filter(Boolean).join("、");
    return `工具成功返回 ${result.items.length} 项${names ? `：${names}` : ""}${result.items.length > 3 ? "……" : ""}`;
  }
  if (typeof result?.content === "string") return `工具成功返回文本：${valuePreview(result.content, 120)}`;
  if (typeof result === "string") return `工具返回：${valuePreview(result, 120)}`;
  if (result && typeof result === "object") return `工具调用${statusLabel(data.status)}，返回字段：${Object.keys(result).join("、")}`;
  return `工具调用${statusLabel(data.status)}`;
}

function eventTitle(event) {
  const data = event.data || {};
  if (event.event_type === "tool_call") return `调用「${toolLabel(data.tool_name || data.name)}」`;
  if (event.event_type === "tool_result") return `「${toolLabel(data.tool_name || data.name)}」返回结果`;
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
  if (event.event_type === "execution_started") return "一个新的隔离 Episode 已经启动。";
  if (event.event_type === "scenario_initialized") return "模拟办公环境已恢复到本案例的冻结初始状态。";
  if (event.event_type === "model_start") return `Agent 开始第 ${display(data.turn)} 轮判断。`;
  if (event.event_type === "tool_call") return argumentSummary(data.arguments || data.args || data.input);
  if (event.event_type === "tool_result") return toolResultSummary(data);
  if (event.event_type === "model_end") return valuePreview(data.decision?.assistant_text || data.response || data.content || data.decision);
  if (event.event_type === "agent_submit") return valuePreview(data.final_response || data.response || data.content);
  if (event.event_type === "scenario_state_observed") return valuePreview(data.observed_changes || data.state || data);
  if (event.event_type === "execution_finished") return "本次 Episode 已正常结束，轨迹和状态已经封存。";
  return valuePreview(data.message || data.detail || data);
}

function renderEventDetail(event) {
  elements.eventDetail.replaceChildren();
  const header = create("div", "event-detail-header");
  const copy = document.createElement("div");
  copy.append(create("h3", "", eventTitle(event)), create("p", "", eventSummary(event) || "该事件没有附加文本。"));
  header.append(copy, create("code", "technical-inline", `SEQ ${String(event.sequence).padStart(3, "0")}`));
  const facts = create("div", "event-facts");
  const values = [
    ["发生了什么", EVENT_TITLES[event.event_type] || eventTitle(event)],
    ["由谁记录", sourceLabel(event.source)],
    ["Episode 内步骤", `第 ${event.sequence} 步`],
    ["逻辑时间", event.logical_time],
  ];
  for (const [label, value] of values) {
    const item = create("div", "event-fact");
    item.append(create("span", "", label), create("strong", "", value));
    facts.append(item);
  }
  elements.eventDetail.append(
    header,
    facts,
    technicalDetails("查看完整结构化事件", [
      ["事件类型", event.event_type],
      ["来源", event.source],
      ["时间戳", event.timestamp],
      ["输入摘要", event.input_digest],
      ["输出摘要", event.output_digest],
      ["状态摘要", event.state_digest],
      ["Checkpoint", event.checkpoint_id],
      ["原始事件", event],
    ]),
  );
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
    item.append(copy, create("code", "event-type technical-inline", event.event_type));
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
    item.append(
      create("strong", "", toolLabel(step.name || step.tool_name)),
      create("span", "", `结果：${statusLabel(step.outcome)}`),
      evidenceLinks(step.evidence_refs || step.evidence_sequences, generation.number),
    );
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
    group.append(create("h4", "", `${featureKindLabel(kind)} · ${entries.length}`));
    for (const feature of entries) {
      const row = create("div", "feature-row");
      row.append(
        create("span", "feature-human", featureSummary(feature)),
        evidenceLinks(feature.evidence_refs || feature.evidence_sequences, generation.number),
        technicalDetails("原始特征", [
          ["特征类型", feature.kind],
          ["特征值", feature.value],
          ["维度", feature.dimensions],
          ["特征 ID", feature.id],
        ]),
      );
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
      ["入口", carrierLabel(context.entry_kind)],
      ["来源", sourceLabel(context.source_domain || context.source)],
      ["目标", sourceLabel(context.sink_domain || context.sink)],
      ["动作", simpleValueLabel(context.sink_action)],
      ["载体", carrierLabel(context.carrier)],
      ["授权情况", reasonLabel(context.authorization_branch)],
      ["是否属于原计划", context.planned === true ? "是" : context.planned === false ? "否，属于额外触达" : "归档未提供"],
      ["结果", simpleValueLabel(context.outcome)],
    ];
    for (const [label, value] of values) {
      facts.append(create("dt", "", label), create("dd", "", value));
    }
    panel.append(
      facts,
      evidenceLinks(context.evidence_refs || context.evidence_sequences, generation.number),
      technicalDetails("查看风险上下文原始字段", [["原始记录", context]]),
    );
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

function renderGenerationBrief(generation) {
  const numbers = coverageNumbers(generation);
  const path = generation.coverage?.tool_path || [];
  const outcomeCounts = path.reduce((counts, step) => {
    counts[step.outcome] = (counts[step.outcome] || 0) + 1;
    return counts;
  }, {});
  const promotion = generation.seed_promotion || generation.promotion || generation.corpus_settlement || {};
  const selection = generation.decision?.frontier_selection || {};
  const riskDirection = riskFamilyLabel(selection.primary_risk_family);
  const targetSeed = objectiveLabel(generation.mutation?.parent_seed?.label || generation.mutation?.parent_seed_id);
  const cards = [
    [
      "为什么测试",
      generation.number === 1
        ? `从「${riskDirection}」风险大类中锁定具体目标，再选择「${targetSeed}」作为父种子。`
        : `读取第 ${generation.number - 1} 代反馈后选择「${riskDirection}」方向，并从该类种子中选出「${targetSeed}」。`,
    ],
    [
      "Agent 做了什么",
      path.length
        ? `共执行 ${path.length} 次工具调用：成功 ${outcomeCounts.succeeded || 0} 次，被阻止或拒绝 ${(outcomeCounts.blocked || 0) + (outcomeCounts.rejected || 0)} 次。`
        : "候选没有进入 Agent 执行，因此没有工具调用。",
    ],
    [
      "本代带来什么",
      `新增 ${numbers.behaviorDelta} 个行为特征、${numbers.riskDelta} 个风险上下文；${promotion.parent_eligible === true ? "候选已进入后续可选种子池" : "候选没有成为后续父种子"}。`,
    ],
  ];
  elements.generationBrief.replaceChildren();
  cards.forEach(([label, value], index) => {
    const card = create("article", "brief-card");
    card.append(create("span", "brief-number", String(index + 1).padStart(2, "0")), create("strong", "", label), create("p", "", value));
    elements.generationBrief.append(card);
  });
}

function renderGeneration() {
  const generation = selectedGeneration();
  state.selectedGeneration = generation.number;
  const numbers = coverageNumbers(generation);
  elements.generationEyebrow.textContent = `第 ${generation.number} 代自动探索`;
  elements.generationTitle.textContent = `第 ${generation.number} 代`;
  elements.generationSubtitle.textContent = generation.settlement_kind === "candidate_settlement"
    ? `实际记录 ${generation.episode.events.length} 个行为事件 · 新增 ${numbers.behaviorDelta} 个行为特征 · 新增 ${numbers.riskDelta} 个风险上下文`
    : "候选在完整性检查阶段结束，没有启动 Agent 执行。";
  elements.generationStatus.textContent = statusLabel(generation.status);
  elements.generationStatus.className = `status-badge ${generation.status || "neutral"}`;
  renderGenerationBrief(generation);
  renderDecisionFlow(generation);
  renderParentSelection(generation);
  renderMutation(generation);
  renderAgentInput(generation);
  const hasEpisode = generation.settlement_kind === "candidate_settlement";
  elements.episodeSection.hidden = !hasEpisode;
  elements.nonEpisodeSection.hidden = hasEpisode;
  if (hasEpisode) renderTimeline(generation);
  else elements.nonEpisodeReason.textContent = reasonText(generation.mutation?.validation?.reason_codes, generation.number) || "候选未通过宿主验证。";
  renderCoverageContribution(generation);
  renderSeedSettlement(generation);
}

function render() {
  if (!state.snapshot) return;
  document.body.classList.toggle("show-technical", state.showTechnical);
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
elements.technicalDetails.addEventListener("change", () => {
  state.showTechnical = elements.technicalDetails.checked;
  document.body.classList.toggle("show-technical", state.showTechnical);
});
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
