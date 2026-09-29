const FIXTURE_URL = "./fixtures/report.json";
const queryApiBase = new URLSearchParams(window.location.search).get("api") || "";
const apiBase = String(window.SIGNAL_RADAR_API_BASE || queryApiBase).replace(/\/+$/, "");
const API_URL = `${apiBase}/api/report`;
const RUN_URL = `${apiBase}/api/run`;

const el = (id) => document.getElementById(id);
const NS = "http://www.w3.org/2000/svg";

function text(value, fallback = "—") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function number(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function safeUrl(value) {
  if (!value) return "";
  try {
    const url = new URL(value, window.location.href);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
}

function riskLevelLabel(value) {
  const labels = { low: "低风险", medium: "中风险", high: "高风险", critical: "严重风险" };
  return labels[String(value || "").toLowerCase()] || text(value, "暂无评级");
}

function dateLabel(value, options = {}) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return text(value);
  return new Intl.DateTimeFormat("zh-CN", options).format(date);
}

function unwrapReport(payload) {
  return payload?.report && typeof payload.report === "object" ? payload.report : payload;
}

function normalizeReport(payload) {
  const report = unwrapReport(payload) || {};
  report.project = report.project || {};
  report.summary = report.summary || {};
  report.trends = Array.isArray(report.trends) ? report.trends : [];
  report.topics = Array.isArray(report.topics) ? report.topics : [];
  report.articles = Array.isArray(report.articles) ? report.articles : [];
  report.claims = Array.isArray(report.claims) ? report.claims : [];
  report.events = Array.isArray(report.events) ? report.events : [];
  report.evidence = Array.isArray(report.evidence) ? report.evidence : [];
  report.sources = Array.isArray(report.sources) ? report.sources : [];
  report.access_status = Array.isArray(report.access_status) ? report.access_status : [];
  return report;
}

function setDataMode(label, variant = "muted") {
  const chip = el("data-mode");
  chip.className = `status-chip status-chip--${variant}`;
  chip.textContent = label;
}

function showError(message) {
  el("error-message").textContent = message;
  el("error-banner").hidden = !message;
}

function displayProject(report) {
  const project = report.project;
  const repository = text(project.repository, "browser-use/browser-use");
  const repoUrl = safeUrl(project.url) || `https://github.com/${repository.replace(/^https?:\/\/github\.com\//, "")}`;
  el("project-heading").textContent = text(project.name, repository);
  el("project-description").textContent = text(project.description, "尚未提供项目描述。");
  el("project-version").textContent = `版本 ${text(project.version, "—")}`;
  el("project-repository").href = repoUrl;
  el("project-repository").setAttribute("aria-label", `在 GitHub 查看 ${repository}`);
  el("last-release").textContent = dateLabel(project.last_release_at, { year: "numeric", month: "2-digit", day: "2-digit" });
  el("window-days").textContent = text(report.window_days, "30");
  el("run-id").textContent = text(report.run_id, "静态演示");
}

function displaySummary(report) {
  const summary = report.summary;
  const score = Math.max(0, Math.min(100, number(summary.risk_score, 0)));
  const level = riskLevelLabel(summary.risk_level);
  const eventCount = number(summary.events_count, report.events?.length || 0);
  const sourceCount = number(summary.source_count, report.sources.length || report.access_status.length);
  const coverage = number(summary.coverage_pct, NaN);
  const accessItems = report.access_status.length ? report.access_status : report.sources;
  const inaccessible = accessItems.filter((item) => ["auth_required", "blocked", "error", "unavailable"].includes(String(item.status || item.access_status || "").toLowerCase())).length;

  el("risk-score").textContent = String(score);
  el("risk-track-fill").style.width = `${score}%`;
  el("risk-level").textContent = level;
  el("events-count").textContent = String(eventCount);
  el("source-count").textContent = String(sourceCount);
  el("coverage-percent").textContent = Number.isFinite(coverage) ? `${Math.round(coverage)}%` : "—";
  el("access-summary").textContent = inaccessible ? `${inaccessible} 项需关注` : "访问正常";
  el("access-detail").textContent = inaccessible ? "登录、拦截或请求失败" : `${sourceCount} 个来源完成检查`;
  const generated = dateLabel(report.generated_at, { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  el("generated-at").textContent = generated;
  el("footer-updated").textContent = report.demo ? "界面演示数据，非实时采集" : `报告生成于 ${generated}`;
  el("trend-range").textContent = `最近 ${text(report.window_days, "30")} 天`;
}

function svgNode(tag, attrs = {}) {
  const node = document.createElementNS(NS, tag);
  Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
  return node;
}

function renderTrends(trends) {
  const chart = el("trend-chart");
  chart.replaceChildren();
  const usable = trends.map((point) => ({
    label: point.date || point.label || point.day || "",
    value: number(point.risk_score ?? point.score ?? point.value, NaN),
  })).filter((point) => Number.isFinite(point.value));

  el("trend-empty").hidden = usable.length > 1;
  el("trend-labels").replaceChildren();
  if (usable.length < 2) return;

  const width = 720;
  const height = 230;
  const pad = { left: 35, right: 10, top: 12, bottom: 16 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  [0, 25, 50, 75, 100].forEach((tick) => {
    const y = pad.top + plotH * (1 - tick / 100);
    chart.append(svgNode("line", { x1: pad.left, y1: y, x2: width - pad.right, y2: y, class: "grid-line" }));
    const label = svgNode("text", { x: 0, y: y + 3, class: "axis-label" });
    label.textContent = String(tick);
    chart.append(label);
  });

  const points = usable.map((point, index) => ({
    ...point,
    x: pad.left + (usable.length === 1 ? 0 : (index / (usable.length - 1)) * plotW),
    y: pad.top + plotH * (1 - Math.max(0, Math.min(100, point.value)) / 100),
  }));
  const linePath = points.map((point, index) => `${index ? "L" : "M"}${point.x},${point.y}`).join(" ");
  const areaPath = `${linePath} L${points.at(-1).x},${pad.top + plotH} L${points[0].x},${pad.top + plotH} Z`;
  chart.append(svgNode("path", { d: areaPath, class: "trend-area" }));
  chart.append(svgNode("path", { d: linePath, class: "trend-line" }));
  points.forEach((point) => chart.append(svgNode("circle", { cx: point.x, cy: point.y, r: 4, class: "trend-point" })));

  const endpoints = [usable[0], usable[Math.floor((usable.length - 1) / 2)], usable.at(-1)];
  endpoints.forEach((point) => {
    const label = document.createElement("span");
    label.textContent = point.label ? dateLabel(point.label, { month: "2-digit", day: "2-digit" }) : "";
    el("trend-labels").append(label);
  });
}

function renderTopics(topics) {
  const list = el("topics-list");
  list.replaceChildren();
  el("topics-empty").hidden = topics.length > 0;
  const max = Math.max(1, ...topics.map((topic) => number(topic.mentions ?? topic.mention_count ?? topic.count, 0)));
  topics.slice(0, 8).forEach((topic) => {
    const count = number(topic.mentions ?? topic.mention_count ?? topic.count, 0);
    const item = document.createElement("div");
    item.className = "topic-item";
    const name = document.createElement("span");
    name.className = "topic-name";
    name.textContent = text(topic.name || topic.label || topic.topic, "未命名主题");
    const bar = document.createElement("span");
    bar.className = "topic-bar";
    const fill = document.createElement("span");
    fill.style.width = `${Math.max(3, (count / max) * 100)}%`;
    bar.append(fill);
    const meta = document.createElement("span");
    meta.className = "topic-meta";
    meta.textContent = `${count} 次`;
    item.append(name, bar, meta);
    list.append(item);
  });
}

function renderEvidence(evidence) {
  const list = el("evidence-list");
  list.replaceChildren();
  el("evidence-empty").hidden = evidence.length > 0;
  const items = [...evidence].sort((a, b) => number(b.relevance ?? b.confidence, 0) - number(a.relevance ?? a.confidence, 0)).slice(0, 6);
  el("evidence-count").textContent = `${evidence.length} 条可引用证据`;
  items.forEach((item) => {
    const article = document.createElement("article");
    article.className = "evidence-card";
    const header = document.createElement("div");
    header.className = "evidence-card-header";
    const title = document.createElement("a");
    title.className = "evidence-title";
    title.textContent = text(item.title || item.claim, "未命名证据");
    const url = safeUrl(item.url || item.source_url);
    if (url) {
      title.href = url;
      title.target = "_blank";
      title.rel = "noreferrer";
    } else {
      title.removeAttribute("href");
      title.setAttribute("aria-disabled", "true");
    }
    const confidence = number(item.confidence ?? item.relevance, NaN);
    const badge = document.createElement("span");
    badge.className = "evidence-confidence";
    badge.textContent = Number.isFinite(confidence) ? `${Math.round(confidence <= 1 ? confidence * 100 : confidence)}%` : "证据";
    header.append(title, badge);

    const quote = document.createElement("p");
    quote.className = "evidence-quote";
    quote.textContent = text(item.quote || item.excerpt || item.summary, "未提供原文摘录。");

    const meta = document.createElement("div");
    meta.className = "evidence-meta";
    const source = document.createElement("span");
    source.textContent = text(item.source || item.source_name, "未知来源");
    const date = document.createElement("span");
    date.textContent = dateLabel(item.published_at || item.created_at, { year: "numeric", month: "2-digit", day: "2-digit" });
    const stance = document.createElement("span");
    stance.className = "stance-tag";
    stance.textContent = text(item.stance || item.sentiment, "未分类");
    meta.append(source, date, stance);
    article.append(header, quote, meta);
    list.append(article);
  });
}

function riskClass(value) {
  const raw = String(value || "low").toLowerCase();
  return ["low", "medium", "high", "critical"].includes(raw) ? raw : "low";
}

function eventEvidenceLinks(event, report) {
  const evidenceById = new Map(report.evidence.map((item) => [item.id, item]));
  const articleById = new Map(report.articles.map((item) => [item.id, item]));
  const links = [];
  const seen = new Set();
  const add = (item, fallbackLabel) => {
    const url = safeUrl(item?.url || item?.source_url || item?.link);
    if (!url || seen.has(url)) return;
    seen.add(url);
    links.push({ url, label: text(item.title || item.name, fallbackLabel) });
  };
  (event.evidence_ids || []).forEach((id) => add(evidenceById.get(id), "证据"));
  (event.article_ids || []).forEach((id) => add(articleById.get(id), "原文"));
  return links.slice(0, 3);
}

function renderEvents(events, report) {
  const list = el("events-list");
  list.replaceChildren();
  const items = [...events].sort((a, b) => {
    const riskDelta = number(b.risk_score, 0) - number(a.risk_score, 0);
    if (riskDelta) return riskDelta;
    return String(b.occurred_at || "").localeCompare(String(a.occurred_at || ""));
  });
  el("events-empty").hidden = items.length > 0;
  el("events-note").textContent = `${events.length} 个事件，按风险分数排序`;
  items.slice(0, 10).forEach((event) => {
    const row = document.createElement("article");
    row.className = `event-row event-row--${riskClass(event.risk_level)}`;

    const identity = document.createElement("div");
    identity.className = "event-identity";
    const title = document.createElement("strong");
    title.className = "event-title";
    title.textContent = text(event.title, "未命名事件");
    identity.append(title);
    if (event.summary) {
      const summary = document.createElement("p");
      summary.className = "event-summary";
      summary.textContent = text(event.summary);
      identity.append(summary);
    }

    const category = document.createElement("span");
    category.className = "event-category";
    category.textContent = text(event.category, "反馈");

    const level = document.createElement("span");
    level.className = `event-level event-level--${riskClass(event.risk_level)}`;
    level.textContent = riskLevelLabel(event.risk_level);

    const score = document.createElement("span");
    score.className = "event-score";
    score.textContent = `${Math.round(Math.max(0, Math.min(100, number(event.risk_score, 0))))} / 100`;

    const date = document.createElement("time");
    date.className = "event-date";
    date.dateTime = text(event.occurred_at, "");
    date.textContent = dateLabel(event.occurred_at, { year: "numeric", month: "2-digit", day: "2-digit" });

    const evidence = document.createElement("div");
    evidence.className = "event-evidence";
    const links = eventEvidenceLinks(event, report);
    if (links.length) {
      links.forEach((item, index) => {
        const link = document.createElement("a");
        link.href = item.url;
        link.target = "_blank";
        link.rel = "noreferrer";
        link.textContent = `证据 ${index + 1}`;
        link.title = item.label;
        evidence.append(link);
      });
    } else {
      const missing = document.createElement("span");
      missing.className = "event-evidence--missing";
      missing.textContent = "暂无链接";
      evidence.append(missing);
    }

    row.append(identity, category, level, score, date, evidence);
    list.append(row);
  });
}

function statusPresentation(status) {
  const raw = String(status || "unknown").toLowerCase();
  const map = {
    public: ["公开可访问", ""], available: ["可访问", ""], success: ["已采集", ""], ok: ["已采集", ""],
    dynamic: ["动态页面", ""], auth_required: ["需要登录", " access-status--auth"], login_required: ["需要登录", " access-status--auth"],
    metadata_only: ["仅有元数据", " access-status--metadata"], blocked: ["访问受阻", " access-status--blocked"],
    error: ["请求失败", " access-status--error"], unavailable: ["不可用", " access-status--error"], skipped: ["未采集", " access-status--metadata"], replay: ["回放数据", " access-status--metadata"],
  };
  return map[raw] || [text(status, "状态未知"), " access-status--metadata"];
}

function renderSources(report) {
  const tbody = el("sources-body");
  tbody.replaceChildren();
  const sourceByName = new Map(report.sources.map((source) => [source.source || source.name, source]));
  const sourceRows = report.access_status.length
    ? report.access_status.map((access) => ({ ...(sourceByName.get(access.source) || {}), ...access }))
    : report.sources;
  el("sources-empty").hidden = sourceRows.length > 0;
  sourceRows.forEach((source) => {
    const row = document.createElement("tr");
    const name = document.createElement("td");
    name.textContent = text(source.name || source.source || source.platform, "未知来源");
    const type = document.createElement("td");
    type.className = "source-type";
    type.textContent = text(source.type || source.source_type, "社区来源");
    const statusCell = document.createElement("td");
    const status = document.createElement("span");
    const [label, modifier] = statusPresentation(source.status || source.access_status);
    status.className = `access-status${modifier}`;
    status.textContent = label;
    statusCell.append(status);
    const count = document.createElement("td");
    count.textContent = String(number(source.evidence_count ?? source.items_count ?? source.records ?? source.count, 0));
    const note = document.createElement("td");
    note.textContent = text(source.note || source.reason || source.message, "—");
    row.append(name, type, statusCell, count, note);
    tbody.append(row);
  });
}

function renderReport(rawReport) {
  const report = normalizeReport(rawReport);
  displayProject(report);
  displaySummary(report);
  renderTrends(report.trends);
  renderTopics(report.topics);
  renderEvents(report.events, report);
  renderEvidence(report.evidence);
  renderSources(report);
  return report;
}

async function loadReport({ quiet = false } = {}) {
  const refresh = el("refresh-button");
  refresh.classList.add("is-loading");
  if (!quiet) showError("");
  try {
    const response = await fetch(API_URL, { headers: { Accept: "application/json" }, cache: "no-store" });
    if (!response.ok) throw new Error(`API ${response.status}`);
    const payload = await response.json();
    if (!payload?.report && payload?.run?.status === "failed") {
      throw new Error(payload.run.error || "采集任务失败");
    }
    const report = renderReport(payload);
    const isReplay = report.demo || /^replay/i.test(text(report.report_id)) || /^replay/i.test(text(report.run_id));
    setDataMode(isReplay ? "回放数据" : "实时报告", isReplay ? "warn" : "");
    showError("");
    return report;
  } catch (apiError) {
    try {
      const response = await fetch(FIXTURE_URL, { headers: { Accept: "application/json" }, cache: "no-store" });
      if (!response.ok) throw new Error(`Fixture ${response.status}`);
      const report = renderReport(await response.json());
      setDataMode("静态演示", "warn");
      if (!quiet) showError("后端 API 不可用，已切换到本地演示报告。静态页面不能启动新的采集任务。");
      return report;
    } catch (fixtureError) {
      setDataMode("数据不可用", "error");
      showError(`无法加载报告：${fixtureError.message || apiError.message}`);
      throw fixtureError;
    }
  } finally {
    refresh.classList.remove("is-loading");
  }
}

async function runAnalysis(event) {
  event.preventDefault();
  const button = el("run-button");
  const values = {
    project: el("project-input").value.trim(),
    window_days: Number(el("window-input").value),
    source: el("source-input").value,
    mode: el("mode-input").value,
  };
  if (!values.project) {
    showError("请输入 GitHub 仓库名或项目标识。");
    el("project-input").focus();
    return;
  }
  button.disabled = true;
  button.querySelector("span").textContent = "分析中…";
  showError("");
  try {
    const response = await fetch(RUN_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(values),
    });
    if (!response.ok) throw new Error(`API ${response.status}`);
    const payload = await response.json();
    if (!payload?.report && payload?.run?.status === "failed") {
      throw new Error(payload.run.error || "采集任务失败");
    }
    const report = renderReport(payload);
    const isReplay = report.demo || /^replay/i.test(text(report.report_id)) || /^replay/i.test(text(report.run_id));
    setDataMode(isReplay ? "回放数据" : "实时报告", isReplay ? "warn" : "");
    showError("");
  } catch (error) {
    const message = values.mode === "live"
      ? "实时采集未完成，请检查后端、模型 Key 和来源权限。"
      : `无法启动回放任务：${error.message || "服务不可用"}。`;
    showError(message);
    setDataMode("静态演示", "warn");
  } finally {
    button.disabled = false;
    button.querySelector("span").textContent = "运行分析";
  }
}

el("run-form").addEventListener("submit", runAnalysis);
el("refresh-button").addEventListener("click", () => loadReport().catch(() => {}));
loadReport().catch(() => {});
