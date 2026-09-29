import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { Presentation, PresentationFile } from "@oai/artifact-tool";

const workspaceDir = "D:/hxjh/wp2-redteam/build/presentation-20260929";
const draftDir = path.join(workspaceDir, "draft");
const outputDir = path.join(workspaceDir, "output");
const skillDir = "C:/Users/17816/.codex/plugins/cache/openai-primary-runtime/presentations/26.915.20218/skills/presentations";
const font = "Noto Sans SC";

await fs.mkdir(draftDir, { recursive: true });
await fs.mkdir(outputDir, { recursive: true });

const { finalizePresentation } = await import(pathToFileURL(path.join(skillDir, "container_tools/artifact_tool_utils.mjs")).href);

const W = 1280;
const H = 720;
const C = {
  bg: "#F7F8F7", ink: "#172A3A", muted: "#5B6770", faint: "#E7ECEB", white: "#FFFFFF",
  teal: "#0F766E", tealPale: "#DDF4F0", orange: "#C76A16", orangePale: "#FBEAD6",
  purple: "#6D43B5", purplePale: "#EEE7FA", blue: "#2563A6", bluePale: "#E4EEF9",
  red: "#B24747", redPale: "#F7E2E2", green: "#2D7D52", greenPale: "#E2F1E8"
};

function shape(slide, geometry, left, top, width, height, fill = "none", lineFill = "none", lineWidth = 0) {
  return slide.shapes.add({ geometry, position: { left, top, width, height }, fill, line: { fill: lineFill, width: lineWidth } });
}

function text(slide, value, left, top, width, height, size = 24, color = C.ink, opts = {}) {
  const s = shape(slide, "textbox", left, top, width, height, "none", "none", 0);
  s.text = value;
  s.text.style = { typeface: font, fontSize: size, color, bold: Boolean(opts.bold), italic: Boolean(opts.italic), autoFit: "shrink" };
  return s;
}

function box(slide, value, left, top, width, height, fill, opts = {}) {
  const s = shape(slide, opts.geometry ?? "roundRect", left, top, width, height, fill, opts.line ?? fill, opts.lineWidth ?? 1);
  s.text = value;
  s.text.style = { typeface: font, fontSize: opts.size ?? 22, color: opts.color ?? C.ink, bold: Boolean(opts.bold), autoFit: "shrink" };
  return s;
}

function pill(slide, value, left, top, width, fill, color = C.ink) {
  return box(slide, value, left, top, width, 34, fill, { size: 15, color, bold: true, line: fill });
}

function title(slide, kicker, heading, sub = "") {
  text(slide, kicker.toUpperCase(), 72, 34, 260, 22, 14, C.teal, { bold: true });
  text(slide, heading, 72, 64, 1136, 54, 34, C.ink, { bold: true });
  if (sub) text(slide, sub, 72, 124, 1120, 34, 17, C.muted);
}

function footer(slide, source, page) {
  shape(slide, "rect", 72, 678, 1136, 1, C.faint, C.faint, 0);
  text(slide, source, 72, 688, 1040, 18, 11, C.muted);
  text(slide, String(page).padStart(2, "0"), 1165, 686, 43, 18, 12, C.muted, { bold: true });
}

function arrow(slide, left, top, width, color = C.muted) {
  shape(slide, "rect", left, top, width - 14, 4, color, color, 0);
  shape(slide, "rect", left + width - 18, top - 4, 18, 12, color, color, 0);
}

function metric(slide, label, value, left, top, accent, note = "") {
  text(slide, label, left, top, 170, 22, 14, C.muted, { bold: true });
  text(slide, value, left, top + 24, 170, 42, 30, accent, { bold: true });
  if (note) text(slide, note, left, top + 70, 190, 24, 13, C.muted);
}

function base() {
  const p = Presentation.create({ slideSize: { width: W, height: H } });
  return p;
}

const presentation = base();

// 1. Cover
{
  const s = presentation.slides.add();
  s.background.fill = C.bg;
  shape(s, "rect", 0, 0, 1280, 14, C.teal, C.teal, 0);
  text(s, "WP2 REDTEAM", 72, 54, 250, 24, 15, C.teal, { bold: true });
  text(s, "行为轨迹级度量与\n覆盖率引导的自动化红队测试", 72, 142, 770, 142, 42, C.ink, { bold: true });
  text(s, "给项目外部技术同事的 10 分钟入门", 76, 306, 520, 30, 20, C.muted);
  box(s, "一句话心智模型\n每个 Episode 既产生安全证据，也更新下一轮探索的覆盖账本。", 72, 424, 570, 116, C.white, { size: 21, color: C.ink, line: C.faint, lineWidth: 1 });
  // Two-dimensional motif
  box(s, "行为\nAgent 做了什么", 838, 170, 210, 112, C.tealPale, { size: 22, color: C.teal, bold: true, line: C.teal });
  box(s, "风险\n触达了什么", 1035, 334, 170, 112, C.orangePale, { size: 22, color: C.orange, bold: true, line: C.orange });
  shape(s, "rect", 955, 280, 6, 64, C.purple, C.purple, 0);
  shape(s, "rect", 950, 336, 85, 6, C.purple, C.purple, 0);
  text(s, "联合覆盖", 908, 288, 130, 24, 14, C.purple, { bold: true });
  text(s, "项目介绍 · 2026-09", 72, 648, 300, 22, 14, C.muted);
  footer(s, "依据：docs/SPEC.md；docs/current-state；已记录运行报告", 1);
}

// 2. Why traces
{
  const s = presentation.slides.add(); s.background.fill = C.bg;
  title(s, "01 / 项目定位", "为什么不能只看最终回答", "最终文本只告诉我们“说了什么”，轨迹才解释“系统做了什么”。");
  box(s, "只看回答", 92, 210, 320, 58, C.faint, { size: 21, color: C.ink, bold: true, line: C.faint });
  box(s, "“已发送摘要”", 92, 284, 320, 112, C.white, { size: 24, color: C.ink, line: C.faint });
  text(s, "看不见", 472, 216, 130, 24, 16, C.red, { bold: true });
  text(s, "收件人是否扩大\n权限是否绕过\n平台是否拦截\n状态是否真的改变", 472, 250, 230, 132, 19, C.ink);
  arrow(s, 742, 332, 76, C.muted);
  box(s, "项目实际记录", 854, 210, 300, 58, C.tealPale, { size: 21, color: C.teal, bold: true, line: C.teal });
  box(s, "工具调用\n工具结果\n状态差异\nOracle 判定", 854, 284, 300, 140, C.white, { size: 22, color: C.ink, line: C.teal });
  box(s, "结论依赖证据链，而不是一句“看起来安全”的话。", 92, 486, 1062, 70, C.bluePale, { size: 22, color: C.blue, bold: true, line: C.blue });
  footer(s, "依据：docs/SPEC.md；当前状态中的 evidence / oracle 语义", 2);
}

// 3. Terms
{
  const s = presentation.slides.add(); s.background.fill = C.bg;
  title(s, "02 / 基本对象", "从测试意图到可核对结论", "把一轮运行拆成几个稳定对象，听众就能跟上后面的覆盖闭环。");
  const items = [
    ["Scenario", "场景", "例如：摘要交付、受限内容读取", C.blue, C.bluePale],
    ["Test Case", "测试用例", "场景 + 约束 + 变异方向", C.teal, C.tealPale],
    ["Episode", "一次执行", "Agent 在环境中完成一条测试路径", C.orange, C.orangePale],
    ["Trace", "轨迹", "调用、结果、状态和时间顺序", C.purple, C.purplePale],
    ["Oracle", "判定器", "根据确定性证据判断 W / F / Q", C.red, C.redPale],
    ["Finding", "发现", "把违规、风险单元和证据留档", C.green, C.greenPale],
  ];
  let x = 72, y = 205;
  items.forEach((it, i) => {
    box(s, it[0], x, y, 175, 44, it[4], { size: 18, color: it[3], bold: true, line: it[3] });
    text(s, it[1], x, y + 54, 175, 26, 19, C.ink, { bold: true });
    text(s, it[2], x, y + 84, 175, 72, 14, C.muted);
    if (i < items.length - 1) arrow(s, x + 180, y + 18, 30, C.faint);
    x += 195;
    if (i === 4) { x = 72; y = 430; }
  });
  footer(s, "依据：README.md；docs/SPEC.md；当前状态审计", 3);
}

// 4. Loop
{
  const s = presentation.slides.add(); s.background.fill = C.bg;
  title(s, "03 / 一次运行", "同一条闭环里，证据变成反馈", "Coverage 不是事后报表，它会在 coverage_guided 模式中参与下一轮选择与变异。");
  const steps = [
    ["输入", "场景、约束、预算", C.blue, C.bluePale],
    ["生成", "父样本与语义变异", C.teal, C.tealPale],
    ["执行", "Agent 调用真实工具", C.orange, C.orangePale],
    ["核对", "轨迹、状态、Oracle", C.purple, C.purplePale],
    ["更新", "行为 / 风险 / 联合账本", C.red, C.redPale],
    ["反馈", "影响下一轮搜索", C.green, C.greenPale],
  ];
  let x = 72;
  for (let i = 0; i < steps.length; i += 1) {
    box(s, steps[i][0], x, 236, 155, 54, steps[i][3], { size: 21, color: steps[i][2], bold: true, line: steps[i][2] });
    text(s, steps[i][1], x, 306, 155, 56, 16, C.ink, { bold: true });
    if (i < steps.length - 1) arrow(s, x + 160, 260, 32, C.muted);
    x += 190;
  }
  box(s, "随机基线在同一候选空间、预算、Agent、环境、Oracle 和记录口径下运行，但不读取跨 Episode 覆盖反馈。", 72, 444, 1136, 82, C.white, { size: 20, color: C.ink, line: C.faint });
  footer(s, "依据：docs/SPEC.md；20260927 feedback-directed mutation / run package", 4);
}

// 5. Behaviour coverage
{
  const s = presentation.slides.add(); s.background.fill = C.bg;
  title(s, "04 / 双重覆盖", "行为覆盖：Agent 实际做了什么", "关注动作和状态证据，避免只用成功或失败代替过程信息。");
  const rows = [
    ["工具调用", "是否触发了目标工具、调用顺序如何", C.teal],
    ["状态变化", "权限、收件人、交付状态是否改变", C.blue],
    ["来源使用", "Agent 是否使用了约束中给定的来源", C.purple],
    ["权限分支", "是否遇到拒绝、升级或绕过路径", C.orange],
  ];
  let y = 205;
  rows.forEach(([a, b, col]) => {
    box(s, a, 102, y, 190, 48, col, { size: 19, color: C.white, bold: true, line: col });
    text(s, b, 332, y + 5, 470, 40, 18, C.ink);
    shape(s, "rect", 850, y + 20, 255, 8, C.faint, C.faint, 0);
    shape(s, "rect", 850, y + 20, [180, 220, 145, 200][rows.findIndex(r => r[0] === a)] , 8, col, col, 0);
    y += 78;
  });
  box(s, "行为覆盖回答：这条路径以前有没有真正做过？", 102, 548, 1003, 64, C.tealPale, { size: 22, color: C.teal, bold: true, line: C.teal });
  footer(s, "依据：双重覆盖 SPEC / TASK；行为特征与轨迹记录定义", 5);
}

// 6 Risk coverage
{
  const s = presentation.slides.add(); s.background.fill = C.bg;
  title(s, "05 / 双重覆盖", "风险覆盖：真实效果触达了什么", "风险单元由工具的真实效果决定，不由提示词里“想做什么”决定。");
  const cells = [
    ["audience-scope", "受众范围", "实际交付给谁", C.orange, C.orangePale],
    ["data-release", "数据释放", "是否暴露受限内容", C.red, C.redPale],
    ["blocked", "被拦下", "平台拒绝，未提交", C.blue, C.bluePale],
    ["committed", "已提交", "平台接受并产生效果", C.green, C.greenPale],
  ];
  let x = 96;
  cells.forEach(([code, label, desc, col, pale]) => {
    box(s, code, x, 220, 240, 48, pale, { size: 17, color: col, bold: true, line: col });
    text(s, label, x, 284, 240, 28, 20, C.ink, { bold: true });
    text(s, desc, x, 320, 240, 48, 16, C.muted);
    x += 276;
  });
  box(s, "风险覆盖回答：这条路径以前有没有触达过这个风险单元？\n读取侧只有“已证明的受限内容曝光”才产生 read_restricted。", 96, 452, 1040, 88, C.orangePale, { size: 20, color: C.orange, bold: true, line: C.orange });
  footer(s, "依据：双重覆盖 SPEC；风险单元与工具效果定义", 6);
}

// 7 Joint coverage
{
  const s = presentation.slides.add(); s.background.fill = C.bg;
  title(s, "06 / 双重覆盖", "联合覆盖：同一 Episode 把两维连起来", "保留行为和风险各自的解释力，再记录它们在同一条证据链上的关系。");
  // matrix
  text(s, "行为覆盖", 232, 192, 300, 26, 18, C.teal, { bold: true });
  text(s, "风险覆盖", 706, 192, 300, 26, 18, C.orange, { bold: true });
  const cards = [
    ["仅新行为", "工具 / 状态路径新增\n风险单元未新增", C.tealPale, C.teal],
    ["行为 + 风险", "两维都新增\n形成新联合关系", C.purplePale, C.purple],
    ["仅新风险", "风险单元新增\n行为路径已见过", C.orangePale, C.orange],
    ["无新增", "相对于当前账本\n没有新覆盖", C.faint, C.muted],
  ];
  const poses = [[126, 238], [642, 238], [126, 432], [642, 432]];
  cards.forEach((c, i) => {
    box(s, c[0], poses[i][0], poses[i][1], 220, 44, c[2], { size: 18, color: c[3], bold: true, line: c[3] });
    text(s, c[1], poses[i][0], poses[i][1] + 60, 270, 62, 17, C.ink);
  });
  shape(s, "rect", 542, 230, 4, 300, C.faint, C.faint, 0);
  shape(s, "rect", 126, 388, 980, 4, C.faint, C.faint, 0);
  box(s, "“无新增”只表示相对于当前作用域的覆盖账本没有增量，不表示没有发生动作。", 272, 590, 735, 44, C.white, { size: 17, color: C.muted, bold: true, line: C.faint });
  footer(s, "依据：双重覆盖 SPEC / TASK；联合覆盖与四类结果定义", 7);
}

// 8 Episode evidence chain
{
  const s = presentation.slides.add(); s.background.fill = C.bg;
  title(s, "07 / 具体 Episode", "从一条交付证据到下一轮反馈", "示例用于说明机制与证据链，不代表真实模型攻击率结论。");
  const nodes = [
    ["约束", "只能发给任务指定收件人", C.bluePale, C.blue],
    ["轨迹", "DELIVERY to =\n['internal.reviewer',\n'partner.contact']", C.tealPale, C.teal],
    ["工具结果", "平台接受双通道发送", C.orangePale, C.orange],
    ["Oracle", "audience-scope\n= violated", C.redPale, C.red],
    ["覆盖", "新增风险单元\n新增行为 × 风险关系", C.purplePale, C.purple],
    ["反馈", "下一轮提高\n未覆盖方向的优先级", C.greenPale, C.green],
  ];
  let x = 54;
  nodes.forEach((n, i) => {
    box(s, n[0], x, 200, 160, 42, n[2], { size: 18, color: n[3], bold: true, line: n[3] });
    text(s, n[1], x, 258, 170, 100, 15, C.ink, { bold: i === 1 || i === 3 });
    if (i < nodes.length - 1) arrow(s, x + 165, 220, 30, C.muted);
    x += 200;
  });
  box(s, "确定性事实来自真实工具效果、状态差异和 Oracle；LLM-as-Judge 不能覆盖这些事实。", 84, 470, 1080, 68, C.white, { size: 19, color: C.ink, bold: true, line: C.faint });
  footer(s, "依据：已记录机制验收 Episode；双重覆盖 SPEC；Oracle 证据优先级", 8);
}

// 9 Guided vs random
{
  const s = presentation.slides.add(); s.background.fill = C.bg;
  title(s, "08 / 两种搜索模式", "差异只有一个关键问题：是否消费跨 Episode 反馈", "公平比较需要共享候选空间、预算、Agent、环境、Oracle 和记录口径。");
  box(s, "coverage_guided", 100, 206, 390, 56, C.tealPale, { size: 22, color: C.teal, bold: true, line: C.teal });
  box(s, "random_evolution", 730, 206, 390, 56, C.bluePale, { size: 22, color: C.blue, bold: true, line: C.blue });
  const shared = ["候选空间", "预算", "Agent", "环境", "Oracle", "记录口径"];
  shared.forEach((v, i) => {
    const y = 292 + i * 42;
    box(s, v, 500, y, 280, 32, C.white, { size: 16, color: C.ink, bold: true, line: C.faint });
    shape(s, "rect", 462, y + 15, 30, 3, C.faint, C.faint, 0);
    shape(s, "rect", 790, y + 15, 30, 3, C.faint, C.faint, 0);
  });
  box(s, "读取历史覆盖\n选择父样本 / 方向 / 变异位置", 100, 470, 390, 88, C.tealPale, { size: 19, color: C.teal, bold: true, line: C.teal });
  box(s, "不读取跨 Episode 覆盖反馈\n保持公平基线", 730, 470, 390, 88, C.bluePale, { size: 19, color: C.blue, bold: true, line: C.blue });
  footer(s, "依据：docs/SPEC.md；feedback-directed mutation 与 random baseline 运行记录", 9);
}

// 10 Real run
{
  const s = presentation.slides.add(); s.background.fill = C.bg;
  title(s, "09 / 一次真实运行读数", "summary-delivery-enc-01：16 次机会的对照", "2026-09-27；小样本读数，用于说明口径，不足以宣布正式优越性。");
  // headers
  box(s, "指标", 92, 202, 250, 42, C.faint, { size: 17, color: C.ink, bold: true, line: C.faint });
  box(s, "coverage_guided", 342, 202, 310, 42, C.tealPale, { size: 17, color: C.teal, bold: true, line: C.teal });
  box(s, "random_evolution", 652, 202, 310, 42, C.bluePale, { size: 17, color: C.blue, bold: true, line: C.blue });
  const rows = [
    ["机会 / 结算", "16 / 16", "16 / 15 + 1 生成失败"],
    ["W / F / Q", "4 / 12 / 0", "3 / 12 / 0"],
    ["W / (W + F)", "25.0%", "20.0%"],
    ["交付", "11 / 16 = 69%", "11 / 15 = 73%"],
    ["合法任务完成", "9 / 16", "9 / 15"],
    ["action_window", "8 / 16", "5 / 15"],
    ["反馈来源非空", "16 / 16", "0 / 15"],
  ];
  let y = 250;
  rows.forEach((r, i) => {
    const fill = i % 2 === 0 ? C.white : "#F0F3F2";
    box(s, r[0], 92, y, 250, 40, fill, { size: 16, color: C.ink, bold: true, line: fill });
    box(s, r[1], 342, y, 310, 40, fill, { size: 17, color: C.teal, bold: true, line: fill });
    box(s, r[2], 652, y, 310, 40, fill, { size: 17, color: C.blue, bold: true, line: fill });
    y += 40;
  });
  box(s, "两臂合计 7 起 audience-scope 越界，data-release 为 0 / 32。\nW=0 不能直接解释成安全，Q 表示无法判定。", 1000, 250, 210, 184, C.orangePale, { size: 16, color: C.orange, bold: true, line: C.orange });
  box(s, "读法：引导模式确实产生反馈输入；但本页样本太小，不能把差异当成稳定效果。", 1000, 470, 210, 108, C.white, { size: 16, color: C.ink, bold: true, line: C.faint });
  footer(s, "来源：docs/tasks/20260925-summary-delivery-e-run-package.md；20260927 enc two-arm attack run package", 10);
}

// 11 Boundaries
{
  const s = presentation.slides.add(); s.background.fill = C.bg;
  title(s, "10 / 当前边界", "三个结论足够带走", "把已完成机制、一次运行读数和仍待验证的问题分开。");
  const cards = [
    ["01", "双重覆盖各自保留解释力", "行为回答“做过什么”，风险回答“触达什么”，联合覆盖回答“如何关联”。", C.teal, C.tealPale],
    ["02", "反馈必须改变下一轮", "coverage_guided 只有把覆盖反馈送回选择或变异，才算覆盖率引导。", C.purple, C.purplePale],
    ["03", "读数先说明口径，再讨论效果", "当前报告给出机制证据和小样本运行读数；黄金集、主动学习、漂移监控和人工复核仍是后续工作。", C.orange, C.orangePale],
  ];
  let y = 204;
  cards.forEach(([n, h, b, col, pale]) => {
    box(s, n, 100, y, 76, 76, col, { size: 26, color: C.white, bold: true, line: col });
    box(s, h, 204, y, 390, 52, pale, { size: 20, color: col, bold: true, line: col });
    text(s, b, 204, y + 66, 870, 50, 17, C.ink);
    y += 132;
  });
  box(s, "下一步讨论：哪些风险单元最值得优先扩展？哪些行为证据需要进入黄金集？", 100, 604, 1040, 42, C.bluePale, { size: 17, color: C.blue, bold: true, line: C.blue });
  footer(s, "依据：docs/SPEC.md；当前状态与运行报告中的已完成 / 未完成边界", 11);
}

const candidate = path.join(draftDir, "project-intro-20260929-draft.pptx");
await (await PresentationFile.exportPptx(presentation)).save(candidate);

const finalPath = path.join(outputDir, "wp2-redteam-project-intro-20260929-v1.pptx");
const stagingDir = path.join(workspaceDir, ".codex-finalizer");
await fs.mkdir(stagingDir, { recursive: true });
const requirements = {
  explicitTotalSlideCount: 11,
  requiredNativeTableOwnerSlides: [],
  requiredNativeChartOwnerSlides: [],
  coverRole: "none",
};
const result = await finalizePresentation({
  ...requirements,
  workspaceDir,
  candidatePath: candidate,
  finalPath,
  pythonExecutable: "C:/Users/17816/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe",
  integrityValidatorPath: path.join(skillDir, "container_tools/inspect_presentation_package_integrity.py"),
  layoutValidatorPath: path.join(skillDir, "container_tools/inspect_presentation_layout_geometry.py"),
  layoutArgs: ["--expected-slide-size-emu", "12192000,6858000", "--validate-bullet-geometry", "--validate-heading-fit"],
  fontPolicy: { basis: "design", families: [font] },
  verifyArtifactToolImport: true,
  receiptPath: path.join(stagingDir, "wp2-redteam-project-intro-20260929-v1.validation.json"),
});
console.log(JSON.stringify({ candidate, finalPath, result }, null, 2));
