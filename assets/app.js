const state = {
  itemsAi: [],
  itemsAll: [],
  itemsAllRaw: [],
  statsAi: [],
  totalAi: 0,
  totalRaw: 0,
  totalAllMode: 0,
  allDedup: true,
  allDataLoaded: false,
  allDataUrl: "data/latest-24h-all.json",
  allDataPromise: null,
  siteFilters: new Set(),
  query: "",
  mode: "ai",
  siteOrder: [],
  hiddenSites: new Set(),
  waytoagiVisible: true,
  waytoagiMode: "today",
  waytoagiData: null,
  sourceStatus: null,
  generatedAt: null,
  bilingual: true,
  signalFilters: new Set(),
};

try {
  const saved = localStorage.getItem("ainr-bilingual");
  if (saved === "0" || saved === "1") state.bilingual = saved === "1";
} catch (_) {}

const statsEl = document.getElementById("stats");
const sitePillsEl = document.getElementById("sitePills");
const newsListEl = document.getElementById("newsList");
const updatedAtEl = document.getElementById("updatedAt");
const searchInputEl = document.getElementById("searchInput");
const resultCountEl = document.getElementById("resultCount");
const listTitleEl = document.getElementById("listTitle");
const itemTpl = document.getElementById("itemTpl");
const modeAiBtnEl = document.getElementById("modeAiBtn");
const modeAllBtnEl = document.getElementById("modeAllBtn");
const modeHintEl = document.getElementById("modeHint");
const allDedupeWrapEl = document.getElementById("allDedupeWrap");
const allDedupeToggleEl = document.getElementById("allDedupeToggle");
const allDedupeLabelEl = document.getElementById("allDedupeLabel");
const advancedSummaryEl = document.getElementById("advancedSummary");
const sourceHealthEl = document.getElementById("sourceHealth");
const coverageStripEl = document.getElementById("coverageStrip");
const themeToggleEl = document.getElementById("themeToggle");
const translateToggleEl = document.getElementById("translateToggle");

function applyThemeLabel() {
  if (!themeToggleEl) return;
  const cur = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
  themeToggleEl.textContent = cur === "dark" ? "LIGHT" : "DARK";
  themeToggleEl.setAttribute("aria-label", cur === "dark" ? "切换到日间模式" : "切换到夜间模式");
}

function toggleTheme() {
  const cur = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
  const next = cur === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("ainr-theme", next); } catch (_) {}
  applyThemeLabel();
}

applyThemeLabel();
if (themeToggleEl) themeToggleEl.addEventListener("click", toggleTheme);

const SOURCE_KINDS = {
  official_ai: { label: "官方", tone: "official" },
  aibreakfast: { label: "日报", tone: "newsletter" },
  followbuilders: { label: "Builders/X", tone: "builders" },
  xapi: { label: "X API", tone: "builders" },
  aihubtoday: { label: "AI站点", tone: "aihub" },
  aibase: { label: "AI站点", tone: "aihub" },
  hf_papers: { label: "HF论文", tone: "official" },
  hf_mlx: { label: "MLX", tone: "builders" },
  reddit_ai: { label: "Reddit", tone: "newsletter" },
  hf_spaces: { label: "Spaces", tone: "aihub" },
  findit: { label: "Findit", tone: "official" },
  github_topics: { label: "GitHub", tone: "builders" },
  github_releases: { label: "Releases", tone: "builders" },
  github_trending: { label: "Trending", tone: "builders" },
  arxiv: { label: "arXiv", tone: "official" },
  hn_ai: { label: "HN", tone: "newsletter" },
  aihot: { label: "AI热点", tone: "aihub" },
  opmlrss: { label: "RSS", tone: "aggregate" },
};

/* ── Site Order Persistence ── */

const SITE_ORDER_KEY = "ai-news-radar-site-order";

function saveSiteOrder() {
  const validIds = new Set(currentSiteStats().map((s) => s.site_id));
  state.siteOrder = state.siteOrder.filter((id) => validIds.has(id));
  try {
    localStorage.setItem(SITE_ORDER_KEY, JSON.stringify(state.siteOrder));
  } catch (_) {}
}

function loadSiteOrder() {
  try {
    const raw = localStorage.getItem(SITE_ORDER_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) state.siteOrder = parsed;
    }
  } catch (_) {}
}

function applySiteOrder(stats) {
  if (!state.siteOrder.length) return stats;
  const orderMap = new Map(state.siteOrder.map((id, i) => [id, i]));
  const ordered = [];
  const unordered = [];
  for (const s of stats) {
    (orderMap.has(s.site_id) ? ordered : unordered).push(s);
  }
  ordered.sort((a, b) => orderMap.get(a.site_id) - orderMap.get(b.site_id));
  unordered.sort((a, b) => b.count - a.count);
  return [...ordered, ...unordered];
}

function orderSiteGroups(groups) {
  if (!state.siteOrder.length) {
    return groups.sort((a, b) => b[2].length - a[2].length || a[1].localeCompare(b[1], "zh-CN"));
  }
  const orderMap = new Map(state.siteOrder.map((id, i) => [id, i]));
  const ordered = [];
  const unordered = [];
  for (const g of groups) {
    (orderMap.has(g[0]) ? ordered : unordered).push(g);
  }
  ordered.sort((a, b) => orderMap.get(a[0]) - orderMap.get(b[0]));
  unordered.sort((a, b) => b[2].length - a[2].length || a[1].localeCompare(b[1], "zh-CN"));
  return [...ordered, ...unordered];
}

/* ── Drag State ── */

const dragState = { gripInitiated: false };

function hideSite(siteId) {
  if (siteId === "__waytoagi__") {
    state.waytoagiVisible = false;
  } else {
    state.hiddenSites.add(siteId);
  }
  renderSiteFilters();
  renderList();
}

function showSite(siteId) {
  if (siteId === "__waytoagi__") {
    state.waytoagiVisible = true;
  } else {
    state.hiddenSites.delete(siteId);
  }
  renderSiteFilters();
  renderList();
}

function fmtNumber(n) {
  return new Intl.NumberFormat("zh-CN").format(n || 0);
}

function fmtTime(iso) {
  if (!iso) return "时间未知";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(d);
}

function fmtDate(iso) {
  if (!iso) return "未知日期";
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
  }).format(d);
}

function setStats(payload) {
  const cards = [
    ["AI 信号", fmtNumber(payload.total_items)],
    ["站点数", fmtNumber(payload.site_count)],
    ["来源分组", fmtNumber(payload.source_count)],
    ["归档", fmtNumber(payload.archive_total || 0)]
  ];

  statsEl.innerHTML = "";
  cards.forEach(([k, v]) => {
    const node = document.createElement("div");
    node.className = "stat";
    node.innerHTML = `<div class="v">${v}</div><div class="k">${k}</div>`;
    statsEl.appendChild(node);
  });
}

function sourceKind(siteId) {
  return SOURCE_KINDS[siteId] || { label: "来源", tone: "default" };
}

function siteRows() {
  return Array.isArray(state.sourceStatus?.sites) ? state.sourceStatus.sites : [];
}

function siteRow(siteId) {
  return siteRows().find((site) => site.site_id === siteId) || null;
}

function renderCoverageCard(label, value, meta, tone = "") {
  const node = document.createElement("div");
  node.className = "coverage-chip";
  const dotEl = document.createElement("span");
  dotEl.className = `coverage-dot ${tone === "warn" ? "warn" : tone === "ok" ? "ok" : "ok"}`.trim();
  const nameEl = document.createElement("span");
  nameEl.className = "coverage-name";
  nameEl.textContent = label;
  const countEl = document.createElement("span");
  countEl.className = "coverage-count";
  countEl.textContent = value;
  node.append(dotEl, nameEl, countEl);
  node.title = meta;
  return node;
}

function renderCoverageStrip(errorMessage = "") {
  if (!coverageStripEl) return;
  coverageStripEl.innerHTML = "";

  const rows = siteRows();
  const failedSites = Array.isArray(state.sourceStatus?.failed_sites) ? state.sourceStatus.failed_sites : [];
  const rss = state.sourceStatus?.rss_opml || {};
  const agentmail = state.sourceStatus?.agentmail || {};
  const xApi = state.sourceStatus?.x_api || {};
  const allCount = Number(state.sourceStatus?.items_before_topic_filter || state.totalAllMode || state.itemsAll.length || 0);
  const coverageCount = Number(state.sourceStatus?.fetched_raw_items || state.totalRaw || allCount || 0);
  const officialCount = Number(siteRow("official_ai")?.item_count || 0);
  const newsletterCount = Number(siteRow("aibreakfast")?.item_count || 0);
  const buildersCount = Number(siteRow("followbuilders")?.item_count || 0);
  const totalSites = rows.length;
  const okSites = Number(state.sourceStatus?.successful_sites || 0);
  const opmlValue = rss.enabled ? `${fmtNumber(rss.ok_feeds || 0)}/${fmtNumber(rss.effective_feed_total || 0)}` : "OPML";
  const opmlMeta = rss.enabled ? "RSS示例/自定义订阅已接入" : "可用OPML批量接入RSS";
  const xApiLabel = xApi.enabled ? `X ${xApi.skipped ? "待窗口" : fmtNumber(xApi.item_count || 0)}` : "X待配置";
  const mailLabel = agentmail.enabled ? `Mail ${fmtNumber(agentmail.item_count || 0)}` : "Mail待配置";
  const advancedMeta = xApi.enabled || agentmail.enabled
    ? `额度保护 · ${xApiLabel} / ${mailLabel}`
    : "X API 与 AgentMail 默认关闭";

  const cards = [
    ["源健康", totalSites ? `${fmtNumber(okSites)}/${fmtNumber(totalSites)}` : "加载中", failedSites.length ? `${fmtNumber(failedSites.length)} 个失败源` : (errorMessage || "内置源正常"), failedSites.length ? "warn" : "ok"],
    ["今日覆盖池", `${fmtNumber(coverageCount)} 条`, allCount ? `全网抓取原始信号 · ${fmtNumber(allCount)} 条入池` : "全网抓取原始信号", "signal"],
    ["AI精选", `${fmtNumber(state.totalAi)} 条`, "24小时强相关信号", "signal"],
    ["官方/日报源池", `${fmtNumber(officialCount + newsletterCount)} 条`, "官方节点 + AI Breakfast", "official"],
    ["Builders/X源池", `${fmtNumber(buildersCount)} 条`, "Follow Builders公开feed", "builders"],
    ["RSS/OPML扩展", opmlValue, opmlMeta, "private"],
    ["高级源", "X / Mail", advancedMeta, "private"],
  ];

  cards.forEach(([label, value, meta, tone]) => {
    coverageStripEl.appendChild(renderCoverageCard(label, value, meta, tone));
  });
}

function renderAdvancedSummary() {
  if (!advancedSummaryEl) return;
  const status = state.sourceStatus;
  const allCount = state.allDedup
    ? (state.totalAllMode || state.itemsAll.length)
    : (state.totalRaw || state.itemsAllRaw.length);
  if (!status) {
    advancedSummaryEl.textContent = `全量 ${fmtNumber(allCount)} 条`;
    return;
  }
  const sites = Array.isArray(status.sites) ? status.sites : [];
  const totalSites = sites.length;
  const okSites = Number(status.successful_sites || 0);
  advancedSummaryEl.textContent = `${fmtNumber(okSites)}/${fmtNumber(totalSites)} 源可用 · 全量 ${fmtNumber(allCount)} 条`;
}

function computeSiteStats(items) {
  const m = new Map();
  items.forEach((item) => {
    if (!m.has(item.site_id)) {
      m.set(item.site_id, { site_id: item.site_id, site_name: item.site_name, count: 0, raw_count: 0 });
    }
    const row = m.get(item.site_id);
    row.count += 1;
    row.raw_count += 1;
  });
  return Array.from(m.values()).sort((a, b) => b.count - a.count || a.site_name.localeCompare(b.site_name, "zh-CN"));
}

function currentSiteStats() {
  if (state.mode === "ai") return state.statsAi || [];
  return computeSiteStats(state.allDedup ? (state.itemsAll || []) : (state.itemsAllRaw || []));
}

function saveSiteFiltersToHash() {
  const hash = state.siteFilters.size > 0 ? `sites=${Array.from(state.siteFilters).join(",")}` : "";
  history.replaceState(null, "", hash ? `#${hash}` : location.pathname);
}

function loadSiteFiltersFromHash() {
  const match = location.hash.match(/sites=([^&]+)/);
  if (!match) return;
  const validIds = new Set(currentSiteStats().filter((s) => s.count > 0).map((s) => s.site_id));
  match[1].split(",").forEach((id) => {
    if (id && validIds.has(id)) state.siteFilters.add(id);
  });
}

function makePillGrip() {
  const grip = document.createElement("span");
  grip.className = "pill-grip";
  for (let i = 0; i < 6; i++) {
    const dot = document.createElement("span");
    dot.className = "pill-grip-dot";
    grip.appendChild(dot);
  }
  return grip;
}

function renderSiteFilters() {
  const rawStats = currentSiteStats();
  const stats = applySiteOrder(rawStats);
  const hasFilter = state.siteFilters.size > 0;
  const visibleStats = stats.filter((s) => !state.hiddenSites.has(s.site_id));
  const hiddenStats = stats.filter((s) => state.hiddenSites.has(s.site_id) && s.count > 0);

  sitePillsEl.innerHTML = "";

  // "All" button — non-draggable
  const allBtn = document.createElement("button");
  allBtn.className = `pill pill-all ${!hasFilter ? "active" : ""}`;
  allBtn.textContent = "全部";
  allBtn.onclick = () => {
    state.siteFilters.clear();
    renderSiteFilters();
    renderList();
  };
  sitePillsEl.appendChild(allBtn);

  // WaytoAGI toggle pill
  if (state.waytoagiData) {
    const wBtn = document.createElement("button");
    wBtn.className = `pill ${state.waytoagiVisible ? "active" : ""}`;
    wBtn.textContent = "WaytoAGI";
    wBtn.onclick = () => {
      if (state.waytoagiVisible) {
        hideSite("__waytoagi__");
      } else {
        showSite("__waytoagi__");
      }
    };
    sitePillsEl.appendChild(wBtn);
  }

  // Per-site pills — draggable
  for (const s of visibleStats) {
    if (s.count === 0) continue;
    const btn = document.createElement("button");
    const active = hasFilter ? state.siteFilters.has(s.site_id) : true;
    btn.className = `pill ${active ? "active" : ""}`;
    btn.dataset.siteId = s.site_id;
    btn.draggable = true;

    btn.appendChild(makePillGrip());

    const label = document.createElement("span");
    label.className = "pill-label";
    label.textContent = `${s.site_name} ${s.count}`;
    btn.appendChild(label);

    // Grip-only drag initiation
    btn.addEventListener("mousedown", (e) => {
      dragState.gripInitiated = !!e.target.closest(".pill-grip");
    });

    // Click handler with drag guard
    btn.onclick = () => {
      if (dragState.gripInitiated) {
        dragState.gripInitiated = false;
        return;
      }
      if (!hasFilter) {
        state.siteFilters.clear();
        state.siteFilters.add(s.site_id);
      } else if (state.siteFilters.has(s.site_id)) {
        state.siteFilters.delete(s.site_id);
      } else {
        state.siteFilters.add(s.site_id);
      }
      renderSiteFilters();
      renderList();
    };

    // Drag events
    btn.addEventListener("dragstart", onPillDragStart);
    btn.addEventListener("dragend", onPillDragEnd);
    btn.addEventListener("dragover", onPillDragOver);
    btn.addEventListener("dragenter", onPillDragEnter);
    btn.addEventListener("dragleave", onPillDragLeave);
    btn.addEventListener("drop", onPillDrop);

    sitePillsEl.appendChild(btn);
  }

  // Hidden site pills — inactive, clickable to restore
  for (const s of hiddenStats) {
    const btn = document.createElement("button");
    btn.className = "pill pill-hidden";
    btn.textContent = `${s.site_name} ${s.count}`;
    btn.onclick = () => showSite(s.site_id);
    sitePillsEl.appendChild(btn);
  }

  saveSiteFiltersToHash();
}

/* ── Pill Drag Handlers ── */

function onPillDragStart(e) {
  if (!dragState.gripInitiated) {
    e.preventDefault();
    return;
  }
  const pill = e.currentTarget;
  pill.classList.add("dragging");
  e.dataTransfer.effectAllowed = "move";
  e.dataTransfer.setData("text/plain", pill.dataset.siteId);
}

function onPillDragEnd(e) {
  e.currentTarget.classList.remove("dragging");
  dragState.gripInitiated = false;
  sitePillsEl.querySelectorAll(".drag-over").forEach((el) => el.classList.remove("drag-over"));
}

function onPillDragOver(e) {
  e.preventDefault();
  e.dataTransfer.dropEffect = "move";
}

function onPillDragEnter(e) {
  e.preventDefault();
  const pill = e.currentTarget;
  if (!pill.classList.contains("dragging")) {
    pill.classList.add("drag-over");
  }
}

function onPillDragLeave(e) {
  e.currentTarget.classList.remove("drag-over");
}

function onPillDrop(e) {
  e.preventDefault();
  const targetPill = e.currentTarget;
  targetPill.classList.remove("drag-over");

  const draggedId = e.dataTransfer.getData("text/plain");
  const targetId = targetPill.dataset.siteId;
  if (!draggedId || draggedId === targetId) return;

  // Read current DOM order, move dragged item to target position
  const currentOrder = Array.from(sitePillsEl.querySelectorAll(".pill[data-site-id]"))
    .map((el) => el.dataset.siteId);
  const fromIdx = currentOrder.indexOf(draggedId);
  const toIdx = currentOrder.indexOf(targetId);
  if (fromIdx === -1 || toIdx === -1) return;

  currentOrder.splice(fromIdx, 1);
  currentOrder.splice(toIdx, 0, draggedId);

  state.siteOrder = currentOrder;
  saveSiteOrder();
  renderSiteFilters();
  renderList();
}

function renderModeSwitch() {
  modeAiBtnEl.classList.toggle("active", state.mode === "ai");
  modeAllBtnEl.classList.toggle("active", state.mode === "all");
  if (allDedupeWrapEl) allDedupeWrapEl.classList.toggle("show", state.mode === "all");
  if (allDedupeToggleEl) allDedupeToggleEl.checked = state.allDedup;
  if (allDedupeLabelEl) allDedupeLabelEl.textContent = state.allDedup ? "去重开" : "去重关";
  if (state.mode === "ai") {
    modeHintEl.textContent = `AI强相关 · ${fmtNumber(state.totalAi)} 条`;
    if (listTitleEl) listTitleEl.textContent = "AI 信号流";
  } else {
    const allCount = state.allDedup
      ? (state.totalAllMode || state.itemsAll.length)
      : (state.totalRaw || state.itemsAllRaw.length);
    modeHintEl.textContent = `全量 · ${state.allDedup ? "去重开" : "去重关"} · ${fmtNumber(allCount)} 条`;
    if (listTitleEl) listTitleEl.textContent = "全量更新";
  }
  renderAdvancedSummary();
}

function effectiveAllItems() {
  return state.allDedup ? state.itemsAll : state.itemsAllRaw;
}

function modeItems() {
  return state.mode === "all" ? effectiveAllItems() : state.itemsAi;
}

// Signal words that are really source/site identifiers rather than topics.
const SIGNAL_CHIP_BLACKLIST = new Set([
  "huggingface",
  "hugging face",
  "aihot",
  "aibase",
  "aihubtoday",
  "zeli_24h_hot",
  "reddit",
  "arxiv",
  "github",
]);

const SIGNAL_CHIP_LIMIT = 14;

function itemSignals(item) {
  const arr = item && item.ai_signals;
  return Array.isArray(arr) ? arr : [];
}

function itemMatchesSignals(item) {
  if (state.signalFilters.size === 0) return true;
  const sigs = itemSignals(item);
  for (const s of sigs) {
    if (state.signalFilters.has(s)) return true;
  }
  return false;
}

function getFilteredItems() {
  const q = state.query.trim().toLowerCase();
  return modeItems().filter((item) => {
    if (state.siteFilters.size > 0 && !state.siteFilters.has(item.site_id)) return false;
    if (!itemMatchesSignals(item)) return false;
    if (!q) return true;
    const hay = `${item.title || ""} ${item.title_zh || ""} ${item.title_en || ""} ${item.site_name || ""} ${item.source || ""}`.toLowerCase();
    return hay.includes(q);
  });
}

function renderItemNode(item) {
  const node = itemTpl.content.firstElementChild.cloneNode(true);

  const titleEl = node.querySelector(".card-title");
  const zh = (item.title_zh || "").trim();
  const en = (item.title_en || "").trim();
  const original = (item.title || "").trim();
  titleEl.textContent = "";
  if (state.bilingual && zh && en && zh !== en) {
    const primary = document.createElement("span");
    primary.textContent = zh;
    const sub = document.createElement("span");
    sub.className = "title-sub";
    sub.textContent = en;
    titleEl.appendChild(primary);
    titleEl.appendChild(sub);
  } else if (state.bilingual) {
    titleEl.textContent = original || zh || en;
  } else {
    titleEl.textContent = original || en || zh;
  }
  titleEl.href = item.url;

  node.querySelector(".site").textContent = item.site_name;
  const kind = sourceKind(item.site_id);
  const categoryEl = node.querySelector(".category");
  categoryEl.textContent = kind.label;
  categoryEl.classList.add(`kind-${kind.tone}`);
  node.querySelector(".source").textContent = `分区: ${item.source}`;
  node.querySelector(".time").textContent = fmtTime(item.published_at || item.first_seen_at);

  return node;
}

const BENTO_PREVIEW_COUNT = 3;

// Aggregator sites whose items come from many different domains — pin a
// canonical home so the bento title is still clickable.
const SITE_HOME_OVERRIDES = {
  hn_ai: "https://news.ycombinator.com/",
  aihot: "https://aihot.virxact.com/",
  reddit_ai: "https://www.reddit.com/r/MachineLearning/",
  github_topics: "https://github.com/topics",
  github_trending: "https://github.com/trending",
  hf_papers: "https://huggingface.co/papers",
  hf_spaces: "https://huggingface.co/spaces",
};

function resolveSiteHome(siteId, items) {
  return SITE_HOME_OVERRIDES[siteId] || inferSiteHome(items);
}

function inferSiteHome(items) {
  const urls = items.map((it) => it.url).filter(Boolean);
  if (!urls.length) return null;
  try {
    const parsed = urls.map((u) => new URL(u));
    const origin = parsed[0].origin;
    if (!parsed.every((p) => p.origin === origin)) return null;
    const segs = parsed.map((p) => p.pathname.split("/").filter(Boolean));
    const first = segs[0] || [];
    const prefix = [];
    for (let i = 0; i < first.length; i++) {
      if (segs.every((s) => s[i] === first[i])) prefix.push(first[i]);
      else break;
    }
    return origin + (prefix.length ? "/" + prefix.join("/") : "");
  } catch {
    return null;
  }
}

function buildTitleNode(siteName, homeUrl) {
  const title = document.createElement("h3");
  if (homeUrl) {
    const link = document.createElement("a");
    link.href = homeUrl;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = siteName;
    link.title = `打开 ${siteName}`;
    title.appendChild(link);
  } else {
    title.textContent = siteName;
  }
  return title;
}

function buildBentoBox(siteId, siteName, items) {
  const box = document.createElement("section");
  box.className = "bento-box";
  box.id = `bento-${siteId}`;

  // Header
  const head = document.createElement("header");
  head.className = "bento-box-head";
  const title = buildTitleNode(siteName, resolveSiteHome(siteId, items));

  const actions = document.createElement("div");
  actions.className = "bento-box-actions";
  const count = document.createElement("span");
  count.className = "bento-box-count";
  count.textContent = `${fmtNumber(items.length)} 条`;
  const closeBtn = document.createElement("button");
  closeBtn.className = "bento-box-close";
  closeBtn.textContent = "×";
  closeBtn.title = `隐藏 ${siteName}`;
  closeBtn.setAttribute("aria-label", `隐藏 ${siteName}`);
  closeBtn.onclick = (e) => {
    e.stopPropagation();
    hideSite(siteId);
  };
  actions.append(count, closeBtn);
  head.append(title, actions);
  box.appendChild(head);

  // Body with items
  const body = document.createElement("div");
  body.className = "bento-box-body";
  const previewItems = items.slice(0, BENTO_PREVIEW_COUNT);
  const remainingItems = items.slice(BENTO_PREVIEW_COUNT);

  previewItems.forEach((item) => body.appendChild(renderItemNode(item)));
  box.appendChild(body);

  // Expand / collapse toggle
  if (remainingItems.length > 0) {
    const toggleBtn = document.createElement("button");
    toggleBtn.className = "bento-box-more";
    toggleBtn.textContent = `展开剩余 ${fmtNumber(remainingItems.length)} 条`;
    let expanded = false;
    toggleBtn.onclick = () => {
      if (!expanded) {
        remainingItems.forEach((item) => body.appendChild(renderItemNode(item)));
        toggleBtn.textContent = "收起";
        expanded = true;
      } else {
        remainingItems.forEach(() => {
          if (body.children.length > BENTO_PREVIEW_COUNT) body.lastChild.remove();
        });
        toggleBtn.textContent = `展开剩余 ${fmtNumber(remainingItems.length)} 条`;
        expanded = false;
        box.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    };
    box.appendChild(toggleBtn);
  }

  return box;
}

function buildWaytoagiBento() {
  const box = document.createElement("section");
  box.className = "bento-box bento-waytoagi";

  // Head — consistent height with other bento boxes
  const head = document.createElement("header");
  head.className = "bento-box-head";
  const waytoagiHome = state.waytoagiData?.root_url || "https://waytoagi.com";
  const title = buildTitleNode("WaytoAGI", waytoagiHome);
  const tools = document.createElement("div");
  tools.className = "bento-waytoagi-tools";
  const sw = document.createElement("div");
  sw.className = "bento-waytoagi-switch";
  const todayBtn = document.createElement("button");
  todayBtn.className = "bento-waytoagi-btn active";
  todayBtn.textContent = "今日";
  todayBtn.id = "waytoagiTodayBtn";
  const weekBtn = document.createElement("button");
  weekBtn.className = "bento-waytoagi-btn";
  weekBtn.textContent = "7日";
  weekBtn.id = "waytoagi7dBtn";
  sw.append(todayBtn, weekBtn);
  tools.appendChild(sw);
  const closeBtn = document.createElement("button");
  closeBtn.className = "bento-box-close";
  closeBtn.textContent = "×";
  closeBtn.title = "隐藏 WaytoAGI";
  closeBtn.onclick = (e) => {
    e.stopPropagation();
    hideSite("__waytoagi__");
  };
  head.append(title, tools, closeBtn);
  box.appendChild(head);

  // Meta area
  const meta = document.createElement("div");
  meta.className = "waytoagi-meta";
  meta.id = "waytoagiMeta";
  box.appendChild(meta);

  // List area
  const list = document.createElement("div");
  list.className = "waytoagi-list";
  list.id = "waytoagiList";
  box.appendChild(list);

  return box;
}

function renderSiteNav(siteGroups) {
  const navEl = document.getElementById("siteNav");
  if (!navEl) return;
  navEl.innerHTML = "";

  siteGroups.forEach(([siteId, siteName, items]) => {
    const btn = document.createElement("button");
    btn.className = "site-nav-item";
    btn.dataset.siteId = siteId;

    const dot = document.createElement("span");
    dot.className = "site-nav-dot";
    const name = document.createElement("span");
    name.textContent = siteName;
    const cnt = document.createElement("span");
    cnt.className = "site-nav-count";
    cnt.textContent = items.length;
    btn.append(dot, name, cnt);

    btn.onclick = () => {
      const target = document.getElementById(`bento-${siteId}`);
      if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
    };
    navEl.appendChild(btn);
  });
}

function setupNavObserver() {
  const navItems = document.querySelectorAll(".site-nav-item");
  if (!navItems.length) return;

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        const navItem = document.querySelector(`.site-nav-item[data-site-id="${entry.target.id.replace("bento-", "")}"]`);
        if (navItem) {
          navItem.classList.toggle("active", entry.isIntersecting && entry.intersectionRatio > 0);
        }
      });
    },
    { rootMargin: "-80px 0px -60% 0px", threshold: 0 }
  );

  document.querySelectorAll(".bento-box[id^='bento-']").forEach((box) => observer.observe(box));
}

function renderSignalChips() {
  const bar = document.getElementById("signalChipsBar");
  const chipsEl = document.getElementById("signalChips");
  const clearBtn = document.getElementById("signalChipsClear");
  if (!bar || !chipsEl) return;

  // Topic chips are derived from ai_signals, which only exist on AI-mode items.
  if (state.mode !== "ai") {
    bar.hidden = true;
    chipsEl.innerHTML = "";
    if (state.signalFilters.size) state.signalFilters.clear();
    return;
  }

  // Apply every filter except signals to scope chip counts to the current view.
  const q = state.query.trim().toLowerCase();
  const scoped = modeItems().filter((item) => {
    if (state.siteFilters.size > 0 && !state.siteFilters.has(item.site_id)) return false;
    if (!q) return true;
    const hay = `${item.title || ""} ${item.title_zh || ""} ${item.title_en || ""} ${item.site_name || ""} ${item.source || ""}`.toLowerCase();
    return hay.includes(q);
  });

  const freq = new Map();
  for (const it of scoped) {
    for (const s of itemSignals(it)) {
      if (!s || SIGNAL_CHIP_BLACKLIST.has(s)) continue;
      freq.set(s, (freq.get(s) || 0) + 1);
    }
  }

  // Always show currently-selected chips even if their fresh count is below the cut-off.
  const ranked = Array.from(freq.entries()).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const top = ranked.slice(0, SIGNAL_CHIP_LIMIT);
  const topNames = new Set(top.map(([s]) => s));
  for (const s of state.signalFilters) {
    if (!topNames.has(s)) top.push([s, freq.get(s) || 0]);
  }

  if (!top.length) {
    bar.hidden = true;
    chipsEl.innerHTML = "";
    return;
  }
  bar.hidden = false;
  chipsEl.innerHTML = "";

  for (const [name, count] of top) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `signal-chip${state.signalFilters.has(name) ? " active" : ""}`;
    btn.dataset.signal = name;
    btn.title = `${name} · ${count} 条`;
    btn.innerHTML = `${name}<span class="signal-chip-count">${count}</span>`;
    btn.onclick = () => {
      if (state.signalFilters.has(name)) state.signalFilters.delete(name);
      else state.signalFilters.add(name);
      renderList();
    };
    chipsEl.appendChild(btn);
  }

  if (clearBtn) clearBtn.hidden = state.signalFilters.size === 0;
}

function renderList() {
  const filtered = getFilteredItems();
  resultCountEl.textContent = `${fmtNumber(filtered.length)} 条`;
  renderSignalChips();

  newsListEl.innerHTML = "";

  if (!filtered.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "当前筛选条件下没有结果。";
    newsListEl.appendChild(empty);
    renderSiteNav([]);
    return;
  }

  // Group items by site
  const siteMap = new Map();
  filtered.forEach((item) => {
    if (!siteMap.has(item.site_id)) {
      siteMap.set(item.site_id, { siteName: item.site_name || item.site_id, items: [] });
    }
    siteMap.get(item.site_id).items.push(item);
  });

  const siteGroups = orderSiteGroups(
    Array.from(siteMap.entries())
      .filter(([id]) => !state.hiddenSites.has(id))
      .map(([id, data]) => [id, data.siteName, data.items])
  );

  // Render nav
  renderSiteNav(siteGroups);

  // Render WaytoAGI bento if data exists and visible
  if (state.waytoagiData && state.waytoagiVisible) {
    newsListEl.appendChild(buildWaytoagiBento());
    // Re-render WaytoAGI content into the new DOM elements
    const metaEl = document.getElementById("waytoagiMeta");
    const listEl = document.getElementById("waytoagiList");
    const todayBtnEl = document.getElementById("waytoagiTodayBtn");
    const weekBtnEl = document.getElementById("waytoagi7dBtn");
    // Store refs for renderWaytoagi
    state._waytoagiMetaEl = metaEl;
    state._waytoagiListEl = listEl;
    state._waytoagiTodayBtnEl = todayBtnEl;
    state._waytoagi7dBtnEl = weekBtnEl;
    renderWaytoagi(state.waytoagiData);
  }

  // Render bento boxes
  siteGroups.forEach(([siteId, siteName, items]) => {
    newsListEl.appendChild(buildBentoBox(siteId, siteName, items));
  });

  // Setup nav observer
  requestAnimationFrame(setupNavObserver);
}

function waytoagiViews(waytoagi) {
  const updates7d = Array.isArray(waytoagi?.updates_7d) ? waytoagi.updates_7d : [];
  const latestDate = waytoagi?.latest_date || (updates7d.length ? updates7d[0].date : null);
  const updatesToday = Array.isArray(waytoagi?.updates_today) && waytoagi.updates_today.length
    ? waytoagi.updates_today
    : (latestDate ? updates7d.filter((u) => u.date === latestDate) : []);
  return { updates7d, updatesToday, latestDate };
}

function renderWaytoagi(waytoagi) {
  const { updates7d, updatesToday, latestDate } = waytoagiViews(waytoagi);
  const metaEl = state._waytoagiMetaEl || waytoagiMetaEl;
  const listEl = state._waytoagiListEl || waytoagiListEl;
  const todayBtn = state._waytoagiTodayBtnEl;
  const weekBtn = state._waytoagi7dBtnEl;

  if (todayBtn) todayBtn.classList.toggle("active", state.waytoagiMode === "today");
  if (weekBtn) weekBtn.classList.toggle("active", state.waytoagiMode === "7d");

  if (metaEl) {
    metaEl.innerHTML = "";
    const rootLink = document.createElement("a");
    rootLink.href = waytoagi.root_url || "#";
    rootLink.target = "_blank";
    rootLink.rel = "noopener noreferrer";
    rootLink.textContent = "主页面";
    const historyLink = document.createElement("a");
    historyLink.href = waytoagi.history_url || "#";
    historyLink.target = "_blank";
    historyLink.rel = "noopener noreferrer";
    historyLink.textContent = "历史更新页";
    const todayCount = document.createElement("span");
    todayCount.textContent = `今日 ${fmtNumber(waytoagi.count_today || updatesToday.length)} 条`;
    const weekCount = document.createElement("span");
    weekCount.textContent = `7日 ${fmtNumber(waytoagi.count_7d || updates7d.length)} 条`;
    [rootLink, "·", historyLink, "·", todayCount, "·", weekCount].forEach((part) => {
      if (typeof part === "string") {
        const sep = document.createElement("span");
        sep.textContent = part;
        metaEl.appendChild(sep);
      } else {
        metaEl.appendChild(part);
      }
    });
  }

  if (!listEl) return;
  listEl.innerHTML = "";
  if (waytoagi.has_error) {
    const div = document.createElement("div");
    div.className = "waytoagi-error";
    div.textContent = waytoagi.error || "WaytoAGI 数据加载失败";
    listEl.appendChild(div);
    return;
  }

  const updates = state.waytoagiMode === "today" ? updatesToday : updates7d;
  if (!updates.length) {
    const div = document.createElement("div");
    div.className = "waytoagi-empty";
    div.textContent = state.waytoagiMode === "today"
      ? "最近更新日没有更新，可切换到近7日查看。"
      : (waytoagi.warning || "近 7 日没有更新");
    listEl.appendChild(div);
    return;
  }

  updates.forEach((u) => {
    const row = document.createElement("a");
    row.className = "waytoagi-item";
    row.href = u.url || "#";
    row.target = "_blank";
    row.rel = "noopener noreferrer";
    const dateEl = document.createElement("span");
    dateEl.className = "d";
    dateEl.textContent = fmtDate(u.date);
    const titleEl = document.createElement("span");
    titleEl.className = "t";
    titleEl.textContent = u.title;
    row.append(dateEl, titleEl);
    listEl.appendChild(row);
  });
}

function renderMetric(label, value, tone = "") {
  const node = document.createElement("div");
  node.className = `health-metric ${tone}`.trim();
  const labelEl = document.createElement("span");
  labelEl.className = "health-label";
  labelEl.textContent = label;
  const valueEl = document.createElement("strong");
  valueEl.textContent = value;
  node.append(labelEl, valueEl);
  return node;
}

function renderIssueList(title, items) {
  const wrap = document.createElement("div");
  wrap.className = "health-issue";
  const titleEl = document.createElement("div");
  titleEl.className = "health-issue-title";
  titleEl.textContent = title;
  const list = document.createElement("ul");
  items.slice(0, 6).forEach((item) => {
    const li = document.createElement("li");
    li.textContent = typeof item === "string" ? item : JSON.stringify(item);
    list.appendChild(li);
  });
  if (items.length > 6) {
    const li = document.createElement("li");
    li.textContent = `另有 ${fmtNumber(items.length - 6)} 项`;
    list.appendChild(li);
  }
  wrap.append(titleEl, list);
  return wrap;
}

function renderSourceHealth(errorMessage = "") {
  if (!sourceHealthEl) return;
  sourceHealthEl.innerHTML = "";

  const status = state.sourceStatus;
  if (!status) {
    const empty = document.createElement("div");
    empty.className = "health-empty";
    empty.textContent = errorMessage || "源状态未生成";
    sourceHealthEl.appendChild(empty);
    renderAdvancedSummary();
    return;
  }

  const sites = Array.isArray(status.sites) ? status.sites : [];
  const failedSites = Array.isArray(status.failed_sites) ? status.failed_sites : [];
  const zeroSites = Array.isArray(status.zero_item_sites) ? status.zero_item_sites : [];
  const rss = status.rss_opml || {};
  const agentmail = status.agentmail || {};
  const xApi = status.x_api || {};
  const failedFeeds = Array.isArray(rss.failed_feeds) ? rss.failed_feeds : [];
  const skippedFeeds = Array.isArray(rss.skipped_feeds) ? rss.skipped_feeds : [];
  const replacedFeeds = Array.isArray(rss.replaced_feeds) ? rss.replaced_feeds : [];

  const metricGrid = document.createElement("div");
  metricGrid.className = "health-grid";
  metricGrid.append(
    renderMetric("内置源", `${fmtNumber(status.successful_sites || 0)}/${fmtNumber(sites.length)}`, failedSites.length ? "warn" : "ok"),
    renderMetric("RSS", rss.enabled ? `${fmtNumber(rss.ok_feeds || 0)}/${fmtNumber(rss.effective_feed_total || 0)}` : "未启用"),
    renderMetric("X API", xApi.enabled ? (xApi.skipped ? "待窗口" : `${fmtNumber(xApi.item_count || 0)}条`) : "未启用", xApi.error ? "bad" : ""),
    renderMetric("AgentMail", agentmail.enabled ? `${fmtNumber(agentmail.item_count || 0)}封` : "未启用", agentmail.error ? "bad" : ""),
    renderMetric("失败源", fmtNumber(failedSites.length + failedFeeds.length), failedSites.length || failedFeeds.length ? "bad" : "ok"),
    renderMetric("替换/跳过", `${fmtNumber(replacedFeeds.length)}/${fmtNumber(skippedFeeds.length)}`)
  );
  sourceHealthEl.appendChild(metricGrid);

  const issues = document.createElement("div");
  issues.className = "health-issues";
  if (failedSites.length) issues.appendChild(renderIssueList("失败站点", failedSites));
  if (zeroSites.length) issues.appendChild(renderIssueList("零结果站点", zeroSites));
  if (failedFeeds.length) issues.appendChild(renderIssueList("失败 RSS", failedFeeds));
  if (skippedFeeds.length) {
    issues.appendChild(renderIssueList("跳过 RSS", skippedFeeds.map((item) => `${item.feed_url} · ${item.reason || "skipped"}`)));
  }

  if (issues.childElementCount) {
    sourceHealthEl.appendChild(issues);
  } else {
    const ok = document.createElement("div");
    ok.className = "health-ok";
    ok.textContent = "源状态正常";
    sourceHealthEl.appendChild(ok);
  }
  renderAdvancedSummary();
}

async function loadNewsData() {
  const res = await fetch(`./data/latest-24h.json?t=${Date.now()}`);
  if (!res.ok) throw new Error(`加载 latest-24h.json 失败: ${res.status}`);
  return res.json();
}

async function loadAllModeData() {
  if (state.allDataLoaded) return;
  if (!state.allDataPromise) {
    state.allDataPromise = fetch(`./${state.allDataUrl}?t=${Date.now()}`)
      .then((res) => {
        if (!res.ok) throw new Error(`加载 latest-24h-all.json 失败: ${res.status}`);
        return res.json();
      })
      .then((payload) => {
        state.itemsAllRaw = payload.items_all_raw || payload.items_all || state.itemsAi;
        state.itemsAll = payload.items_all || state.itemsAi;
        state.totalRaw = payload.total_items_raw || state.itemsAllRaw.length;
        state.totalAllMode = payload.total_items_all_mode || state.itemsAll.length;
        state.allDataLoaded = true;
      })
      .catch((err) => {
        state.allDataPromise = null;
        throw err;
      });
  }
  return state.allDataPromise;
}

async function loadWaytoagiData() {
  const res = await fetch(`./data/waytoagi-7d.json?t=${Date.now()}`);
  if (!res.ok) throw new Error(`加载 waytoagi-7d.json 失败: ${res.status}`);
  return res.json();
}

async function loadSourceStatusData() {
  const res = await fetch(`./data/source-status.json?t=${Date.now()}`);
  if (!res.ok) throw new Error(`加载 source-status.json 失败: ${res.status}`);
  return res.json();
}

async function init() {
  const [newsResult, waytoagiResult, statusResult] = await Promise.allSettled([
    loadNewsData(),
    loadWaytoagiData(),
    loadSourceStatusData(),
  ]);

  if (newsResult.status === "fulfilled") {
    const payload = newsResult.value;
    state.itemsAi = payload.items_ai || payload.items || [];
    state.itemsAllRaw = payload.items_all_raw || payload.items_all || [];
    state.itemsAll = payload.items_all || [];
    state.statsAi = payload.site_stats || [];
    state.totalAi = payload.total_items || state.itemsAi.length;
    state.totalRaw = payload.total_items_raw || state.itemsAllRaw.length;
    state.totalAllMode = payload.total_items_all_mode || state.itemsAll.length;
    state.allDataUrl = payload.all_mode_data_url || state.allDataUrl;
    state.allDataLoaded = Boolean(payload.items_all || payload.items_all_raw);
    state.generatedAt = payload.generated_at;

    setStats(payload);
    renderModeSwitch();
    renderCoverageStrip();
    loadSiteFiltersFromHash();
    loadSiteOrder();
    renderSiteFilters();
    renderList();
    updatedAtEl.textContent = `更新时间：${fmtTime(state.generatedAt)}`;
  } else {
    updatedAtEl.textContent = "新闻数据加载失败";
    newsListEl.innerHTML = `<div class="empty">${newsResult.reason.message}</div>`;
    renderCoverageStrip(newsResult.reason.message);
  }

  if (statusResult.status === "fulfilled") {
    state.sourceStatus = statusResult.value;
    renderSourceHealth();
    renderCoverageStrip();
  } else {
    renderSourceHealth(statusResult.reason.message);
    renderCoverageStrip(statusResult.reason.message);
  }

  if (waytoagiResult.status === "fulfilled") {
    state.waytoagiData = waytoagiResult.value;
    // WaytoAGI will be rendered inside renderList() as a bento module
  }
}

searchInputEl.addEventListener("input", (e) => {
  state.query = e.target.value;
  renderList();
});

modeAiBtnEl.addEventListener("click", () => {
  state.mode = "ai";
  renderModeSwitch();
  renderSiteFilters();
  renderList();
});

modeAllBtnEl.addEventListener("click", async () => {
  state.mode = "all";
  renderModeSwitch();
  newsListEl.innerHTML = "";
  const loading = document.createElement("div");
  loading.className = "empty";
  loading.textContent = "正在加载全量更新...";
  newsListEl.appendChild(loading);
  try {
    await loadAllModeData();
    renderSiteFilters();
    renderList();
  } catch (err) {
    newsListEl.innerHTML = "";
    const failed = document.createElement("div");
    failed.className = "empty";
    failed.textContent = err.message;
    newsListEl.appendChild(failed);
  }
});

if (allDedupeToggleEl) {
  allDedupeToggleEl.addEventListener("change", (e) => {
    state.allDedup = Boolean(e.target.checked);
    renderModeSwitch();
    renderSiteFilters();
    renderList();
  });
}

if (translateToggleEl) {
  translateToggleEl.checked = state.bilingual;
  translateToggleEl.addEventListener("change", (e) => {
    state.bilingual = Boolean(e.target.checked);
    try { localStorage.setItem("ainr-bilingual", state.bilingual ? "1" : "0"); } catch (_) {}
    renderList();
  });
}

const signalChipsClearEl = document.getElementById("signalChipsClear");
if (signalChipsClearEl) {
  signalChipsClearEl.addEventListener("click", () => {
    state.signalFilters.clear();
    renderList();
  });
}

// WaytoAGI button delegation (buttons are created dynamically in bento modules)
document.addEventListener("click", (e) => {
  const btn = e.target.closest("#waytoagiTodayBtn, #waytoagi7dBtn");
  if (!btn) return;
  state.waytoagiMode = btn.id === "waytoagiTodayBtn" ? "today" : "7d";
  if (state.waytoagiData) renderWaytoagi(state.waytoagiData);
});

init();
