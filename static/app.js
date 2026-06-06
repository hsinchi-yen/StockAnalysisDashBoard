function $(id) {
  return document.getElementById(id);
}

function formatNumber(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  if (typeof value === "number") return value.toLocaleString("en-US");
  return String(value);
}

function formatFloat2(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  const n = Number(value);
  if (Number.isNaN(n)) return "-";
  return n.toFixed(2);
}

function formatSignedPercent(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  const n = Number(value);
  if (Number.isNaN(n)) return "-";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(1)}%`;
}

// Format large numbers in 億 / 萬 units for readability
function formatCF(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  const n = Number(value);
  if (Number.isNaN(n)) return "-";
  const abs = Math.abs(n);
  if (abs >= 100000) return (n / 100000).toFixed(2) + "億";
  if (abs >= 10000) return (n / 10000).toFixed(1) + "萬";
  return n.toLocaleString("en-US");
}

function setStatus(text) {
  $("status").textContent = text || "";
}

function setLoading(isLoading) {
  $("go").disabled = isLoading;
  $("stockId").disabled = isLoading;
  $("years").disabled = isLoading;
  $("token").disabled = isLoading;
  if ($("tokenSave")) $("tokenSave").disabled = isLoading;
  if ($("tokenClear")) $("tokenClear").disabled = isLoading;
}

function showSkeleton(id) {
  const el = $(id);
  if (el) {
    el.classList.add("skeleton-section");
    el.textContent = "";
  }
}

function clearSkeleton(id) {
  const el = $(id);
  if (el) el.classList.remove("skeleton-section");
}

function renderMetrics(container, items) {
  container.innerHTML = "";
  for (const { key, value } of items) {
    const div = document.createElement("div");
    div.className = "metric";
    div.innerHTML = `<div class="k">${key}</div><div class="v">${value}</div>`;
    container.appendChild(div);
  }
}

function renderTable(container, headers, rows) {
  const table = document.createElement("table");
  table.className = "table";

  const thead = document.createElement("thead");
  const trh = document.createElement("tr");
  for (const h of headers) {
    const th = document.createElement("th");
    th.textContent = h;
    trh.appendChild(th);
  }
  thead.appendChild(trh);

  const tbody = document.createElement("tbody");
  for (const r of rows) {
    const tr = document.createElement("tr");
    for (const c of r) {
      const td = document.createElement("td");
      td.innerHTML = c;
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }

  table.appendChild(thead);
  table.appendChild(tbody);
  container.innerHTML = "";

  const wrapper = document.createElement("div");
  wrapper.className = "table-scroll";
  wrapper.appendChild(table);
  container.appendChild(wrapper);
}

async function fetchJson(url, token) {
  let finalUrl = url;
  if (token && token.trim()) {
    const sep = url.includes("?") ? "&" : "?";
    finalUrl = url + sep + "token=" + encodeURIComponent(token.trim());
  }
  const res = await fetch(finalUrl);
  if (!res.ok) {
    let detail = "";
    try {
      const payload = await res.json();
      detail = payload.detail || JSON.stringify(payload);
    } catch {
      detail = await res.text();
    }
    
    if (res.status === 502) {
      if (detail.includes("timeout") || detail.includes("Read timed out")) {
        detail = "連線 FinMind 或 Goodinfo 時發生逾時。這通常是因為查詢區間較長或平台負載較高，請稍後再試。";
      } else if (detail.includes("quota exceeded") || detail.includes("402")) {
        const isPaid = localStorage.getItem(PAID_API_STORAGE_KEY) === "1";
        detail = isPaid
          ? "FinMind API 呼叫次數已達本小時上限（6000 次），請稍後再試。"
          : "FinMind API 呼叫次數已達上限。請等待免費額度重置（每日 UTC 00:00）後再查詢。";
      } else {
        detail = "第三方資料平台暫時無回應或發生錯誤 (" + detail + ")";
      }
    } else if (res.status === 401) {
      detail = "未設定或無效的 API Key，請重新輸入。";
    }
    
    throw new Error(`[錯誤 ${res.status}] ${detail}`.trim());
  }
  return await res.json();
}

const PLOTLY_CONFIG = {
  responsive: true,
  displaylogo: false,
  scrollZoom: false,
  doubleClick: 'reset+autosize',
  modeBarButtonsToRemove: ["lasso2d", "select2d"],
};

// ── Theme (Dark / Daylight) ──────────────────────────────────────────────────
const THEME_KEY = "microeco.theme";

function isDarkMode() {
  return document.documentElement.getAttribute("data-theme") === "dark";
}

function initTheme() {
  const saved = localStorage.getItem(THEME_KEY) || "light";
  document.documentElement.setAttribute("data-theme", saved);

  const btn = document.getElementById("themeToggle");
  if (!btn) return;
  btn.addEventListener("click", () => {
    const next = isDarkMode() ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem(THEME_KEY, next); } catch {}
    rerenderDashboard();
  });
}

const dashboardState = {
  stockId: "",
  revenueRows: [],
  priceRows: [],
  dividendYieldRows: [],
  volumeRows: [],
  roeRoaRows: [],
  debtRatioRows: [],
  fcfData: null,
  fcfQuarterlyData: null,
  dcfData: null,
  shareholdingData: null,
  turnoverDaysRows: [],
  peRiverData: null,
  // === NEW ===
  marginsRows: [],
  epsTrendRows: [],
  liquidityRows: [],
  foreignHoldingData: null,
  valuationExtraData: null,
  latestSnapshot: null,
  // === cache-restored extras ===
  cashRows: [],
  buyScoreData: null,
  shareholdingSpreadData: null,
};

// DCF parameter state (preserved across re-queries)
const dcfState = { g: 0.04, r: 0.08, mos: 0.30 };
const FINMIND_TOKEN_STORAGE_KEY = "microeco.finmind_api_key";
const PAID_API_STORAGE_KEY = "microeco.paid_api";

// ── Last-query cache (localStorage) ───────────────────────────────────────────
// Persists the most recent successful query so the dashboard opens instantly
// with the previous result instead of auto-querying a hard-coded default stock.
const QUERY_CACHE_KEY = "microeco.last_query_v1";
let _cacheSaveTimer = null;

function saveQueryCache(immediate = false) {
  if (!dashboardState.stockId) return;
  const write = () => {
    const payload = {
      stockId: dashboardState.stockId,
      years: Number($("years").value) || 3,
      dcf: { ...dcfState },
      state: dashboardState,
      savedAt: Date.now(),
    };
    try {
      localStorage.setItem(QUERY_CACHE_KEY, JSON.stringify(payload));
    } catch (e) {
      // Quota exceeded or serialization failure — drop the cache silently.
      console.warn("saveQueryCache failed:", e);
      try { localStorage.removeItem(QUERY_CACHE_KEY); } catch {}
    }
  };
  window.clearTimeout(_cacheSaveTimer);
  if (immediate) { write(); return; }
  // Debounce: avoid thrashing localStorage during a burst of render callbacks.
  _cacheSaveTimer = window.setTimeout(write, 1500);
}

function loadQueryCache() {
  let payload = null;
  try {
    const raw = localStorage.getItem(QUERY_CACHE_KEY);
    if (!raw) return false;
    payload = JSON.parse(raw);
  } catch {
    return false;
  }
  if (!payload || !payload.state || !payload.stockId) return false;

  // Restore inputs
  $("stockId").value = payload.stockId;
  if (payload.years) $("years").value = String(payload.years);
  if (payload.dcf) Object.assign(dcfState, payload.dcf);

  // Restore state object (replace contents in place so references stay valid)
  Object.keys(dashboardState).forEach((k) => { delete dashboardState[k]; });
  Object.assign(dashboardState, payload.state);

  restoreFromState();

  const when = payload.savedAt ? new Date(payload.savedAt).toLocaleString("zh-TW", { hour12: false }) : "";
  setStatus(`已載入上次查詢的快取資料：${payload.stockId}${when ? `（${when}）` : ""}，按「查詢」可更新`);
  return true;
}

// Re-render every panel from dashboardState. Each panel is isolated so one
// missing/old field cannot break the rest of the restore.
function restoreFromState() {
  if (!dashboardState.stockId) return;
  const sid = dashboardState.stockId;
  const safe = (label, fn) => { try { fn(); } catch (e) { console.warn(`restore ${label}:`, e); } };

  // Core charts / tables (shared with rerenderDashboard)
  safe("dashboard", () => rerenderDashboard());

  // Latest price + institutional snapshot
  safe("latest", () => {
    const latest = dashboardState.latestSnapshot;
    if (latest) renderLatestSnapshot(latest, sid);
  });
  // Revenue / dividend tables
  safe("revenueTable", () => {
    const rows = dashboardState.revenueRows || [];
    if (rows.length) renderTable($("revenueTable"), ["月份", "營收", "MA3", "MA6", "MA12"],
      rows.map((r) => [r.month || "-",
        r.revenue == null ? "-" : formatNumber(r.revenue),
        r.ma_3 == null ? "-" : formatNumber(r.ma_3),
        r.ma_6 == null ? "-" : formatNumber(r.ma_6),
        r.ma_12 == null ? "-" : formatNumber(r.ma_12)]));
  });
  safe("dividendTable", () => {
    if ((dashboardState.cashRows || []).length && (dashboardState.priceRows || []).length)
      renderDividendTable(dashboardState.cashRows, dashboardState.priceRows);
  });
  safe("dcf", () => { if (dashboardState.dcfData) renderDcf(dashboardState.dcfData); });
  safe("capitalFormation", () => { if (dashboardState.capitalFormationData) renderCapitalFormation(dashboardState.capitalFormationData); });
  safe("shareholding", () => { if (dashboardState.shareholdingData) renderShareholding(dashboardState.shareholdingData); });
  safe("shareholdingSpread", () => { if (dashboardState.shareholdingSpreadData) renderShareholdingSpread(dashboardState.shareholdingSpreadData); });
  safe("buyScore", () => { if (dashboardState.buyScoreData) renderBuyScore(dashboardState.buyScoreData); });
  safe("signalCard", () => renderSignalCard(dashboardState));
}

let resizeTimer = null;

const oneHandState = {
  range: "today",
  account: "總帳戶",
};

const tabState = {
  current: "tab-basic",
  history: ["tab-basic"],
};

function updateTabBackButtonState() {
  const backBtn = $("quickTabBack");
  if (!backBtn) return;
  backBtn.disabled = tabState.history.length <= 1;
}

function switchTab(tabId, options = {}) {
  const { recordHistory = true, scrollToTop = false } = options;
  const panel = $(tabId);
  if (!panel) return;

  document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
  document.querySelectorAll(".tab-btn, .bottom-nav-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.tab === tabId);
  });

  panel.classList.add("active");

  if (recordHistory) {
    const last = tabState.history[tabState.history.length - 1];
    if (last !== tabId) {
      tabState.history.push(tabId);
      if (tabState.history.length > 20) tabState.history.shift();
    }
  }

  tabState.current = tabId;
  updateTabBackButtonState();

  if (scrollToTop) {
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  // Resize all Plotly charts inside the newly visible tab
  setTimeout(() => {
    panel.querySelectorAll(".chart").forEach((el) => {
      if (el.data) Plotly.relayout(el, {});
    });
  }, 50);
}

function switchToPreviousTab() {
  if (tabState.history.length <= 1) return;
  tabState.history.pop();
  const prev = tabState.history[tabState.history.length - 1] || "tab-basic";
  switchTab(prev, { recordHistory: false, scrollToTop: true });
}

function initQuickActions() {
  // Risk badge link
  const riskLink = $("riskBadgeLink");
  if (riskLink) {
    riskLink.addEventListener("click", () => switchTab("tab-buyscore", { recordHistory: true }));
  }
}

function getSavedToken() {
  try {
    const raw = localStorage.getItem(FINMIND_TOKEN_STORAGE_KEY);
    return raw ? raw.trim() : "";
  } catch {
    return "";
  }
}

function setSavedToken(token) {
  try {
    localStorage.setItem(FINMIND_TOKEN_STORAGE_KEY, token);
  } catch {
    // no-op if storage is unavailable
  }
}

function clearSavedToken() {
  try {
    localStorage.removeItem(FINMIND_TOKEN_STORAGE_KEY);
  } catch {
    // no-op if storage is unavailable
  }
}

function getFinmindToken() {
  const typed = ($("token")?.value || "").trim();
  if (typed) return typed;
  return getSavedToken();
}

function saveTokenFromInput() {
  const token = ($("token")?.value || "").trim();
  if (!token) {
    setStatus("請先輸入 FinMind API Key 再儲存");
    return false;
  }
  setSavedToken(token);
  setStatus("已儲存 API Key（本機永久保存，移除 App 才會清除）");
  return true;
}

function requireTokenBeforeQuery() {
  const token = getFinmindToken();
  if (token) {
    if ($("token") && !$("token").value.trim()) {
      $("token").value = token;
    }
    return token;
  }
  setStatus("請先輸入並儲存 FinMind API Key，才能開始查詢");
  return "";
}

function bindPaidApiToggle() {
  // Plan badge is auto-detected from api_request_limit when updateTokenUsage() runs.
  // Restore badge from last known state so it shows on page load before next quota check.
  const badge = $("apiPlanBadge");
  if (!badge) return;
  const isPaid = localStorage.getItem(PAID_API_STORAGE_KEY) === "1";
  if (isPaid) {
    badge.textContent = "SponsorYear";
    badge.className = "api-plan-badge paid";
  }
}

function bindTokenSettings() {
  const tokenInput = $("token");
  const saveBtn = $("tokenSave");
  const clearBtn = $("tokenClear");
  if (!tokenInput) return;

  const saved = getSavedToken();
  if (saved) {
    tokenInput.value = saved;
    setStatus("已讀取已儲存的 FinMind API Key");
  } else {
    setStatus("請先輸入並儲存 FinMind API Key");
  }

  if (saveBtn) {
    saveBtn.addEventListener("click", () => {
      saveTokenFromInput();
    });
  }

  if (clearBtn) {
    clearBtn.addEventListener("click", () => {
      clearSavedToken();
      tokenInput.value = "";
      setStatus("已清除 API Key。請重新輸入後儲存");
    });
  }

  const tokenUsageBtn = $("tokenUsage");
  if (tokenUsageBtn) {
    tokenUsageBtn.addEventListener("click", async () => {
      const token = getFinmindToken();
      if (!token) {
        setStatus("查無 API Key，請先輸入並儲存。");
        return;
      }
      setLoading(true);
      await updateTokenUsage(token);
      setLoading(false);
    });
  }
}

async function updateTokenUsage(token) {
  try {
    const res = await fetchJson(`/api/token_usage`, token);
    const usedNum = Number(res.user_count);
    const limitNum = Number(res.api_request_limit);
    const remNum = Number(res.remaining);

    const used = Number.isFinite(usedNum) && usedNum >= 0 ? usedNum : 0;
    const limit = Number.isFinite(limitNum) && limitNum > 0 ? limitNum : 600;
    const rem = Number.isFinite(remNum) && remNum >= 0 ? remNum : Math.max(0, limit - used);
    const pct = limit > 0 ? (rem / limit) * 100 : 0;

    // Auto-detect plan: limit >= 1600 = paid (SponsorYear 6000/hr), else free (600/day)
    const isPaid = limit >= 1600;
    // Persist detected plan so fetchJson error messages are consistent
    localStorage.setItem(PAID_API_STORAGE_KEY, isPaid ? "1" : "0");

    // Update badge
    const badge = $("apiPlanBadge");
    if (badge) {
      badge.textContent = isPaid ? "SponsorYear" : "免費";
      badge.className = "api-plan-badge " + (isPaid ? "paid" : "free");
    }

    // Warn thresholds: paid → % based (5%/10%); free → absolute (20/50)
    let color = "#10b981";
    if (isPaid) {
      if (pct < 5) color = "#ef4444";
      else if (pct < 10) color = "#f59e0b";
    } else {
      if (rem < 20) color = "#ef4444";
      else if (rem < 50) color = "#f59e0b";
    }

    const container = $("tokenUsageContainer");
    const bar = $("tokenUsageBar");
    const text = $("tokenUsageText");

    if (container && bar && text) {
      container.style.display = "block";
      bar.style.width = `${pct}%`;
      bar.style.backgroundColor = color;
      const cycle = isPaid ? "每小時" : "每日";
      text.textContent = `已使用 ${used} 次｜剩餘 ${rem} 次 / ${limit} 次 (${pct.toFixed(1)}%)（${cycle}）`;
    }
  } catch (e) {
    const container = $("tokenUsageContainer");
    const bar = $("tokenUsageBar");
    const text = $("tokenUsageText");
    if (container && text && bar) {
      container.style.display = "block";
      bar.style.width = "0%";
      const msg = (e.message === "Failed to fetch" || e.message === "NetworkError when attempting to fetch resource.")
        ? "無法連線至伺服器，請確認服務已啟動"
        : e.message;
      text.textContent = `Token 狀態查詢失敗: ${msg}`;
    }
  }
}

function isCompactViewport() {
  return window.innerWidth <= 768;
}

function baseChartLayout(title, overrides = {}) {
  const compact = isCompactViewport();
  const dark = isDarkMode();
  const bgColor   = dark ? "#1e293b" : "#ffffff";
  const gridColor = dark ? "#334155" : "#e5e7eb";
  const fontColor = dark ? "#cbd5e1" : "#374151";
  const axisBase  = { gridcolor: gridColor, zerolinecolor: gridColor, linecolor: gridColor };

  const { xaxis: oX, yaxis: oY, yaxis2: oY2, ...rest } = overrides;
  const layout = {
    title,
    paper_bgcolor: bgColor,
    plot_bgcolor:  bgColor,
    font: { color: fontColor },
    margin: compact ? { l: 44, r: 16, t: 48, b: 52 } : { l: 55, r: 20, t: 50, b: 40 },
    hovermode: "x unified",
    legend: compact
      ? { orientation: "h", y: -0.22, x: 0, font: { size: 11 } }
      : { orientation: "h" },
    xaxis: { ...axisBase, ...oX },
    yaxis: { ...axisBase, ...oY },
    ...rest,
  };
  if (oY2) layout.yaxis2 = { ...axisBase, ...oY2 };
  return layout;
}

function getLastValid(rows, key) {
  const valid = (rows || []).filter((row) => row && row[key] !== null && row[key] !== undefined && !Number.isNaN(Number(row[key])));
  return valid.length ? valid[valid.length - 1] : null;
}

function getPreviousValid(rows, key, currentMonth) {
  const valid = (rows || []).filter((row) => row && row.month !== currentMonth && row[key] !== null && row[key] !== undefined && !Number.isNaN(Number(row[key])));
  return valid.length ? valid[valid.length - 1] : null;
}

function formatFeedCompact(value, decimals = 0) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  const n = Number(value);
  return n.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function animateCount(el, target, options = {}) {
  if (!el) return;
  const {
    prefix = "",
    suffix = "",
    decimals = 0,
    duration = 420,
  } = options;

  const to = Number(target);
  if (!Number.isFinite(to)) {
    el.textContent = "-";
    return;
  }

  const from = Number(el.dataset.value ?? to);
  const start = Number.isFinite(from) ? from : to;
  const delta = to - start;
  const t0 = performance.now();

  if (el.__rafId) {
    cancelAnimationFrame(el.__rafId);
  }

  const frame = (now) => {
    const p = Math.min((now - t0) / duration, 1);
    const eased = 1 - Math.pow(1 - p, 3);
    const cur = start + delta * eased;
    el.textContent = `${prefix}${formatFeedCompact(cur, decimals)}${suffix}`;
    if (p < 1) {
      el.__rafId = requestAnimationFrame(frame);
      return;
    }
    el.dataset.value = String(to);
    delete el.__rafId;
  };

  el.__rafId = requestAnimationFrame(frame);
}

function activeRangeButton(range) {
  document.querySelectorAll(".range-chip").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.range === range);
  });
}

function scrollFeedToRange(range) {
  const card = $(`feed-${range}`);
  if (!card) return;
  card.scrollIntoView({ behavior: "smooth", block: "start" });
}

function plotFeedWaterfall(targetId, title, labels, measure, values) {
  const el = $(targetId);
  if (!el) return;
  clearSkeleton(targetId);

  if (!values.every((v) => Number.isFinite(Number(v)))) {
    el.textContent = "資料不足，暫時無法繪製此區段瀑布圖。";
    return;
  }

  const trace = {
    type: "waterfall",
    x: labels,
    y: values,
    measure,
    connector: { line: { color: "#9ca3af" } },
    decreasing: { marker: { color: "#ef4444" } },
    increasing: { marker: { color: "#10b981" } },
    totals: { marker: { color: "#111827" } },
    textposition: "outside",
  };

  const layout = baseChartLayout(title, {
    margin: isCompactViewport()
      ? { l: 32, r: 12, t: 34, b: 42 }
      : { l: 40, r: 16, t: 40, b: 42 },
    showlegend: false,
    xaxis: { fixedrange: true },
    yaxis: { fixedrange: true, tickformat: ",.2f" },
  });
  Plotly.newPlot(targetId, [trace], layout, PLOTLY_CONFIG);
}

function renderFinancialFeed() {
  // Feed sections removed
}

function initOneHandControls() {
  // One-hand dock controls were removed.
  return;
}

function classifyDelta(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "neutral";
  if (value > 0) return "up";
  if (value < 0) return "down";
  return "neutral";
}

function renderTrendSummary(revenueRows, priceRows, dividendYieldRows) {
  const container = $("trendSummary");
  if (!container) return;

  const latestRevenue = getLastValid(revenueRows, "revenue");
  const previousRevenue = latestRevenue ? getPreviousValid(revenueRows, "revenue", latestRevenue.month) : null;
  const latestPrice = getLastValid(priceRows, "close");
  const previousPrice = latestPrice ? getPreviousValid(priceRows, "close", latestPrice.month) : null;
  const latestYield = getLastValid(dividendYieldRows, "dividend_yield");
  const previousYield = latestYield ? getPreviousValid(dividendYieldRows, "dividend_yield", latestYield.month) : null;
  const latestRevenueTrend = getLastValid(revenueRows, "ma_12");

  const revenueDelta = latestRevenue && previousRevenue && Number(previousRevenue.revenue) !== 0
    ? ((Number(latestRevenue.revenue) - Number(previousRevenue.revenue)) / Number(previousRevenue.revenue)) * 100
    : null;
  const priceDelta = latestPrice && previousPrice && Number(previousPrice.close) !== 0
    ? ((Number(latestPrice.close) - Number(previousPrice.close)) / Number(previousPrice.close)) * 100
    : null;
  const yieldDelta = latestYield && previousYield
    ? Number(latestYield.dividend_yield) - Number(previousYield.dividend_yield)
    : null;
  const revenueVsTrend = latestRevenue && latestRevenueTrend && Number(latestRevenueTrend.ma_12) !== 0
    ? ((Number(latestRevenue.revenue) - Number(latestRevenueTrend.ma_12)) / Number(latestRevenueTrend.ma_12)) * 100
    : null;
  const priceRevenueGap = priceDelta !== null && revenueDelta !== null ? priceDelta - revenueDelta : null;

  const cards = [
    {
      label: "最新月營收變化",
      value: formatSignedPercent(revenueDelta),
      detail: latestRevenue ? `最新月份 ${latestRevenue.month}` : "查無資料",
      tone: classifyDelta(revenueDelta),
    },
    {
      label: "股價月變化",
      value: formatSignedPercent(priceDelta),
      detail: latestPrice ? `最新月份 ${latestPrice.month}` : "查無資料",
      tone: classifyDelta(priceDelta),
    },
    {
      label: "營收相對 MA12",
      value: formatSignedPercent(revenueVsTrend),
      detail: revenueVsTrend === null ? "查無趨勢基準" : revenueVsTrend >= 0 ? "高於 12 月均線" : "低於 12 月均線",
      tone: classifyDelta(revenueVsTrend),
    },
    {
      label: "殖利率變動",
      value: yieldDelta === null ? "-" : `${yieldDelta > 0 ? "+" : ""}${formatFloat2(yieldDelta)} pt`,
      detail: latestYield ? `最新值 ${formatFloat2(latestYield.dividend_yield)}%` : "查無資料",
      tone: classifyDelta(yieldDelta),
    },
    {
      label: "股價 vs 營收動能差",
      value: formatSignedPercent(priceRevenueGap),
      detail: priceRevenueGap === null ? "查無法比較" : priceRevenueGap >= 0 ? "股價動能領先營收" : "營收動能領先股價",
      tone: classifyDelta(priceRevenueGap),
    },
  ];

  container.innerHTML = cards.map((card) => `
    <article class="summary-card summary-${card.tone}">
      <div class="summary-label">${card.label}</div>
      <div class="summary-value">${card.value}</div>
      <div class="summary-detail">${card.detail}</div>
    </article>
  `).join("");
}

function renderTrendNarrative(revenueRows, priceRows, dividendYieldRows) {
  const container = $("trendNarrative");
  if (!container) return;

  const latestRevenue = getLastValid(revenueRows, "revenue");
  const previousRevenue = latestRevenue ? getPreviousValid(revenueRows, "revenue", latestRevenue.month) : null;
  const latestPrice = getLastValid(priceRows, "close");
  const previousPrice = latestPrice ? getPreviousValid(priceRows, "close", latestPrice.month) : null;
  const latestYield = getLastValid(dividendYieldRows, "dividend_yield");
  const previousYield = latestYield ? getPreviousValid(dividendYieldRows, "dividend_yield", latestYield.month) : null;
  const latestRevenueTrend = getLastValid(revenueRows, "ma_12");

  const revenueDelta = latestRevenue && previousRevenue && Number(previousRevenue.revenue) !== 0
    ? ((Number(latestRevenue.revenue) - Number(previousRevenue.revenue)) / Number(previousRevenue.revenue)) * 100
    : null;
  const priceDelta = latestPrice && previousPrice && Number(previousPrice.close) !== 0
    ? ((Number(latestPrice.close) - Number(previousPrice.close)) / Number(previousPrice.close)) * 100
    : null;
  const yieldDelta = latestYield && previousYield
    ? Number(latestYield.dividend_yield) - Number(previousYield.dividend_yield)
    : null;
  const revenueVsTrend = latestRevenue && latestRevenueTrend && Number(latestRevenueTrend.ma_12) !== 0
    ? ((Number(latestRevenue.revenue) - Number(latestRevenueTrend.ma_12)) / Number(latestRevenueTrend.ma_12)) * 100
    : null;

  const insights = [];

  if (revenueDelta !== null && priceDelta !== null) {
    if (priceDelta - revenueDelta >= 5) {
      insights.push("股價短線動能明顯快於營收，市場可能已提前反映預期，需留意後續基本面是否跟上。");
    } else if (revenueDelta - priceDelta >= 5) {
      insights.push("營收改善速度快於股價，代表基本面回升尚未完全反映到價格。 ");
    } else {
      insights.push("股價與營收短線變化大致同步，市場對基本面反應相對一致。");
    }
  }

  if (revenueVsTrend !== null) {
    if (revenueVsTrend >= 8) {
      insights.push("最新月營收明顯高於 12 月均線，營運動能偏強。");
    } else if (revenueVsTrend <= -8) {
      insights.push("最新月營收明顯低於 12 月均線，短期營運動能轉弱。");
    } else {
      insights.push("最新月營收接近 12 月均線，營運動能目前屬中性區間。");
    }
  }

  if (yieldDelta !== null) {
    if (yieldDelta > 0.2) {
      insights.push("殖利率上升，多半來自股價回落或現金股利提升，適合和價格走勢一起解讀。");
    } else if (yieldDelta < -0.2) {
      insights.push("殖利率下降，可能代表股價上升速度快於股利變化。 ");
    }
  }

  if (insights.length === 0) {
    container.textContent = "目前資料不足，暫時無法提供趨勢判讀。";
    return;
  }

  container.innerHTML = insights.map((text) => `<p>${text.trim()}</p>`).join("");
}

function plotRevenue(rows, stockId) {
  clearSkeleton("revenueChart");
  const x = rows.map((r) => r.month);
  const revenue = rows.map((r) => r.revenue);
  const ma3 = rows.map((r) => r.ma_3);
  const ma6 = rows.map((r) => r.ma_6);
  const ma12 = rows.map((r) => r.ma_12);

  const traces = [
    {
      x,
      y: revenue,
      type: "scatter",
      mode: "lines+markers",
      name: "Revenue",
      connectgaps: false,
    },
    {
      x,
      y: ma3,
      type: "scatter",
      mode: "lines",
      name: "MA 3M",
      connectgaps: false,
    },
    {
      x,
      y: ma6,
      type: "scatter",
      mode: "lines",
      name: "MA 6M",
      connectgaps: false,
    },
    {
      x,
      y: ma12,
      type: "scatter",
      mode: "lines",
      name: "MA 12M",
      connectgaps: false,
    },
  ];

  const layout = baseChartLayout(`${stockId} 月營收 + MA(3/6/12)`, {
    xaxis: { tickformat: "%Y-%m" },
  });

  Plotly.newPlot("revenueChart", traces, layout, PLOTLY_CONFIG);
}

function plotPriceVsRevenue(revenueRows, priceRows, stockId) {
  clearSkeleton("priceChart");
  const x = revenueRows.map((r) => r.month);
  const revenue = revenueRows.map((r) =>
    r.revenue === null || r.revenue === undefined || Number.isNaN(Number(r.revenue)) ? null : Number(r.revenue) / 10000
  );
  const close = priceRows.map((r) => r.close);

  const traces = [
    {
      x,
      y: revenue,
      type: "bar",
      name: "營收",
      yaxis: "y",
      hovertemplate: "%{x|%Y-%m}<br>營收：%{y:,.0f} 10K<extra></extra>",
      marker: { opacity: 0.55 },
    },
    {
      x,
      y: close,
      type: "scatter",
      mode: "lines",
      name: "股價(月收盤)",
      yaxis: "y2",
      connectgaps: false,
      hovertemplate: "%{x|%Y-%m}<br>月收盤：%{y:,.2f}<extra></extra>",
    },
  ];

  const layout = baseChartLayout(`${stockId} 股價 vs 營收`, {
    margin: isCompactViewport() ? { l: 44, r: 46, t: 48, b: 52 } : { l: 55, r: 55, t: 50, b: 40 },
    barmode: "overlay",
    xaxis: { tickformat: "%Y-%m" },
    yaxis: { title: "營收 (10K)", tickformat: "~s", rangemode: "tozero" },
    yaxis2: {
      title: "股價",
      tickformat: ",.2f",
      overlaying: "y",
      side: "right",
      rangemode: "tozero",
    },
  });

  Plotly.newPlot("priceChart", traces, layout, PLOTLY_CONFIG);
}

function normalizeSeries(rows, valueKey) {
  const cleaned = (rows || []).map((row) => {
    const raw = row ? row[valueKey] : null;
    const numeric = raw === null || raw === undefined ? NaN : Number(raw);
    return {
      month: row ? row.month : null,
      value: Number.isNaN(numeric) ? null : numeric,
    };
  });

  const baseRow = cleaned.find((row) => row.month && row.value !== null && row.value !== 0);
  if (!baseRow) {
    return cleaned.map((row) => ({ month: row.month, value: null, raw: row.value }));
  }

  return cleaned.map((row) => ({
    month: row.month,
    raw: row.value,
    value: row.value === null ? null : Number((row.value / baseRow.value) * 100),
  }));
}

function mergeTrendSeries(revenueRows, priceRows, dividendYieldRows) {
  const revenue = normalizeSeries(revenueRows, "revenue");
  const ma12 = normalizeSeries(revenueRows, "ma_12");
  const price = normalizeSeries(priceRows, "close");
  const yieldPct = normalizeSeries(dividendYieldRows, "dividend_yield");

  const monthSet = new Set();
  for (const row of [...revenue, ...ma12, ...price, ...yieldPct]) {
    if (row.month) monthSet.add(row.month);
  }

  const months = Array.from(monthSet).sort();
  const toMap = (rows) => new Map(rows.filter((row) => row.month).map((row) => [row.month, row]));

  const revenueMap = toMap(revenue);
  const ma12Map = toMap(ma12);
  const priceMap = toMap(price);
  const yieldMap = toMap(yieldPct);

  return months.map((month) => ({
    month,
    revenue: revenueMap.get(month)?.value ?? null,
    revenueRaw: revenueMap.get(month)?.raw ?? null,
    ma12: ma12Map.get(month)?.value ?? null,
    ma12Raw: ma12Map.get(month)?.raw ?? null,
    price: priceMap.get(month)?.value ?? null,
    priceRaw: priceMap.get(month)?.raw ?? null,
    dividendYield: yieldMap.get(month)?.value ?? null,
    dividendYieldRaw: yieldMap.get(month)?.raw ?? null,
  }));
}

function plotTrendOverview(revenueRows, priceRows, dividendYieldRows, stockId) {
  clearSkeleton("trendOverviewChart");
  const merged = mergeTrendSeries(revenueRows, priceRows, dividendYieldRows);
  const hasData = merged.some((row) =>
    row.revenue !== null || row.ma12 !== null || row.price !== null || row.dividendYield !== null
  );

  if (!hasData) {
    const el = $("trendOverviewChart");
    if (el) el.textContent = "查無足夠資料可繪製時間趨勢對照圖。";
    return;
  }

  const x = merged.map((row) => row.month);
  const traces = [
    {
      x,
      y: merged.map((row) => row.revenue),
      customdata: merged.map((row) => row.revenueRaw),
      type: "scatter",
      mode: "lines+markers",
      name: "月營收",
      connectgaps: false,
      line: { color: "#111827", width: 2 },
      hovertemplate: "%{x|%Y-%m}<br>月營收指數：%{y:.1f}<br>原始值：%{customdata:,.0f}<extra></extra>",
    },
    {
      x,
      y: merged.map((row) => row.ma12),
      customdata: merged.map((row) => row.ma12Raw),
      type: "scatter",
      mode: "lines",
      name: "營收 MA12",
      connectgaps: false,
      line: { color: "#e07b39", width: 2, dash: "dot" },
      hovertemplate: "%{x|%Y-%m}<br>營收 MA12 指數：%{y:.1f}<br>原始值：%{customdata:,.0f}<extra></extra>",
    },
    {
      x,
      y: merged.map((row) => row.price),
      customdata: merged.map((row) => row.priceRaw),
      type: "scatter",
      mode: "lines",
      name: "月收盤價",
      connectgaps: false,
      line: { color: "#4a90d9", width: 2 },
      hovertemplate: "%{x|%Y-%m}<br>股價指數：%{y:.1f}<br>原始值：%{customdata:,.2f}<extra></extra>",
    },
    {
      x,
      y: merged.map((row) => row.dividendYield),
      customdata: merged.map((row) => row.dividendYieldRaw),
      type: "scatter",
      mode: "lines",
      name: "殖利率",
      connectgaps: false,
      line: { color: "#1d8f6a", width: 2 },
      hovertemplate: "%{x|%Y-%m}<br>殖利率指數：%{y:.1f}<br>原始值：%{customdata:.2f}%<extra></extra>",
    },
  ];

  const layout = baseChartLayout(`${stockId} 時間趨勢對照圖`, {
    xaxis: { tickformat: "%Y-%m" },
    yaxis: { title: "基準指數（首個有效值 = 100）", tickformat: ".0f", zeroline: true },
  });

  Plotly.newPlot("trendOverviewChart", traces, layout, PLOTLY_CONFIG);
}

function plotVolumeTurnoverRecent(rows, stockId) {
  clearSkeleton("volumeTurnoverChart");
  const x = (rows || []).map((r) => r.date);
  const volume = (rows || []).map((r) => r.volume);
  const turnover = (rows || []).map((r) => r.turnover);

  const traces = [
    {
      x,
      y: volume,
      type: "scatter",
      mode: "lines",
      name: "成交量",
      connectgaps: false,
      yaxis: "y",
      hovertemplate: "%{x}<br>成交量：%{y:,}<extra></extra>",
    },
    {
      x,
      y: turnover,
      type: "scatter",
      mode: "lines",
      name: "成交筆數",
      connectgaps: false,
      yaxis: "y2",
      hovertemplate: "%{x}<br>成交筆數：%{y:,}<extra></extra>",
    },
  ];

  const layout = baseChartLayout(`${stockId} 近 3 個月每日成交量 / 成交筆數`, {
    margin: isCompactViewport() ? { l: 44, r: 46, t: 48, b: 52 } : { l: 55, r: 55, t: 50, b: 40 },
    xaxis: { tickformat: "%m-%d" },
    yaxis: { title: "成交量", tickformat: "~s", rangemode: "tozero" },
    yaxis2: {
      title: "成交筆數",
      tickformat: "~s",
      overlaying: "y",
      side: "right",
      rangemode: "tozero",
    },
  });

  Plotly.newPlot("volumeTurnoverChart", traces, layout, PLOTLY_CONFIG);
}

function plotRoeRoa(rows, stockId) {
  clearSkeleton("roeRoaChart");
  if (!rows || rows.length === 0) {
    const el = document.getElementById("roeRoaChart");
    if (el) el.textContent = "查無 ROE / ROA 資料（FinMind 財報資料尚未提供或該股票不支援）。";
    return;
  }

  const labels = rows.map((r) => r.quarter_label || r.quarter);
  const roe = rows.map((r) => r.roe);
  const roa = rows.map((r) => r.roa);

  const refROE = 15;
  const refROA = 5;

  const traces = [
    {
      x: labels,
      y: roe,
      type: "scatter",
      mode: "lines+markers",
      name: "ROE (%)",
      connectgaps: false,
      line: { color: "#e07b39", width: 2 },
      marker: { color: "#e07b39", size: 5 },
      hovertemplate: "%{x}<br>ROE：%{y:.2f}%<extra></extra>",
    },
    {
      x: labels,
      y: roa,
      type: "scatter",
      mode: "lines+markers",
      name: "ROA (%)",
      connectgaps: false,
      line: { color: "#4a90d9", width: 2 },
      marker: { color: "#4a90d9", size: 5 },
      hovertemplate: "%{x}<br>ROA：%{y:.2f}%<extra></extra>",
    },
    {
      x: [labels[0], labels[labels.length - 1]],
      y: [refROE, refROE],
      type: "scatter",
      mode: "lines",
      name: `ROE 優質基準 ${refROE}%`,
      line: { color: "#e07b39", width: 1, dash: "dot" },
      hoverinfo: "skip",
    },
    {
      x: [labels[0], labels[labels.length - 1]],
      y: [refROA, refROA],
      type: "scatter",
      mode: "lines",
      name: `ROA 優質基準 ${refROA}%`,
      line: { color: "#4a90d9", width: 1, dash: "dot" },
      hoverinfo: "skip",
    },
  ];

  const layout = baseChartLayout(`${stockId} ROE / ROA（季度年化，%）`, {
    margin: isCompactViewport() ? { l: 44, r: 16, t: 48, b: 72 } : { l: 50, r: 20, t: 50, b: 60 },
    xaxis: { tickangle: -35, type: "category" },
    yaxis: { title: "（%）", tickformat: ".2f", zeroline: true },
  });

  Plotly.newPlot("roeRoaChart", traces, layout, PLOTLY_CONFIG);

  renderTable(
    document.getElementById("roeRoaTable"),
    ["季度", "ROE (%)", "ROA (%)"],
    [...rows].reverse().map((r) => [
      r.quarter_label || r.quarter || "-",
      r.roe === null ? "-" : formatFloat2(r.roe),
      r.roa === null ? "-" : formatFloat2(r.roa),
    ])
  );
}

// ── NEW: Debt Ratio ─────────────────────────────────────────────────────────

function plotDebtRatio(rows, stockId) {
  clearSkeleton("debtRatioChart");
  if (!rows || rows.length === 0) {
    const el = $("debtRatioChart");
    if (el) el.textContent = "查無負債比資料。";
    return;
  }

  const labels = rows.map((r) => r.quarter_label || r.quarter);
  const ratios = rows.map((r) => r.debt_ratio);
  const n = labels.length;

  const traces = [
    {
      x: labels,
      y: ratios,
      type: "scatter",
      mode: "lines+markers",
      name: "負債比 (%)",
      line: { color: "#7c3aed", width: 2 },
      marker: { color: "#7c3aed", size: 5 },
      connectgaps: false,
      hovertemplate: "%{x}<br>負債比：%{y:.2f}%<extra></extra>",
    },
    {
      x: [labels[0], labels[n - 1]],
      y: [40, 40],
      type: "scatter",
      mode: "lines",
      name: "健康門檻 40%",
      line: { color: "#16a34a", width: 1.5, dash: "dot" },
      hoverinfo: "skip",
    },
    {
      x: [labels[0], labels[n - 1]],
      y: [60, 60],
      type: "scatter",
      mode: "lines",
      name: "風險門檻 60%",
      line: { color: "#dc2626", width: 1.5, dash: "dot" },
      hoverinfo: "skip",
    },
  ];

  const layout = baseChartLayout(`${stockId} 歷年負債比（%）`, {
    margin: isCompactViewport() ? { l: 44, r: 16, t: 48, b: 72 } : { l: 50, r: 20, t: 50, b: 60 },
    xaxis: { tickangle: -35, type: "category" },
    yaxis: { title: "負債比（%）", tickformat: ".1f", range: [0, Math.max(80, ...ratios.filter(Boolean)) + 5] },
  });

  Plotly.newPlot("debtRatioChart", traces, layout, PLOTLY_CONFIG);

  renderTable(
    $("debtRatioTable"),
    ["季度", "負債比 (%)", "負債 (千元)", "資產 (千元)"],
    [...rows].reverse().map((r) => [
      r.quarter_label || r.quarter || "-",
      r.debt_ratio === null ? "-" : formatFloat2(r.debt_ratio),
      r.liabilities === null ? "-" : formatNumber(r.liabilities),
      r.assets === null ? "-" : formatNumber(r.assets),
    ])
  );
}

// ── NEW: Free Cash Flow ──────────────────────────────────────────────────────

function plotFreeCashFlow(data, stockId, years) {
  clearSkeleton("fcfChart");
  const rows = (data && data.rows) || [];
  if (!rows.length) {
    const el = $("fcfChart");
    if (el) el.textContent = "查無自由現金流量資料（需要現金流量表資料）。";
    return;
  }

  const labels = rows.map((r) => r.is_full_year === false ? `${r.year}*` : String(r.year));
  const opcf = rows.map((r) => r.operating_cf !== null ? r.operating_cf / 1000 : null); // convert to 百萬
  const capex = rows.map((r) => r.capex !== null ? -r.capex / 1000 : null); // show as negative bar
  const fcf = rows.map((r) => r.fcf !== null ? r.fcf / 1000 : null);
  const pctCap = rows.map((r) => r.fcf_pct_capital);

  const traces = [
    {
      x: labels,
      y: opcf,
      type: "bar",
      name: "營業現金流",
      marker: { color: "#3b82f6", opacity: 0.75 },
      hovertemplate: "%{x}年<br>營業CF：%{y:,.1f} M<extra></extra>",
    },
    {
      x: labels,
      y: capex,
      type: "bar",
      name: "資本支出（負）",
      marker: { color: "#f97316", opacity: 0.75 },
      hovertemplate: "%{x}年<br>資本支出：%{y:,.1f} M<extra></extra>",
    },
    {
      x: labels,
      y: fcf,
      type: "bar",
      name: "自由現金流",
      marker: { color: "#22c55e", opacity: 0.85 },
      hovertemplate: "%{x}年<br>自由CF：%{y:,.1f} M<extra></extra>",
    },
    {
      x: labels,
      y: pctCap,
      type: "scatter",
      mode: "lines+markers",
      name: "FCF 佔股本 %",
      yaxis: "y2",
      line: { color: "#7c3aed", width: 2 },
      marker: { color: "#7c3aed", size: 6 },
      connectgaps: false,
      hovertemplate: "%{x}年<br>佔股本：%{y:.1f}%<extra></extra>",
    },
  ];

  const layout = baseChartLayout(`${stockId} 自由現金流量（年度，${years}年）`, {
    margin: isCompactViewport() ? { l: 44, r: 46, t: 48, b: 52 } : { l: 60, r: 60, t: 50, b: 40 },
    barmode: "group",
    xaxis: { type: "category" },
    yaxis: { title: "金額（百萬元）", tickformat: ",.0f", zeroline: true },
    yaxis2: {
      title: "佔股本 (%)",
      overlaying: "y",
      side: "right",
      tickformat: ".1f",
      showgrid: false,
    },
  });

  Plotly.newPlot("fcfChart", traces, layout, PLOTLY_CONFIG);

  // Summary table rows
  const avgFcf = data.fcf_avg;
  const avgPct = data.fcf_avg_pct_capital;
  const tableRows = [...rows].reverse().map((r) => [
    r.is_full_year === false ? `${r.quarter_label || r.year}（累計）` : String(r.year),
    formatCF(r.operating_cf),
    formatCF(r.capex),
    formatCF(r.fcf),
    r.fcf_pct_capital !== null ? formatFloat2(r.fcf_pct_capital) + "%" : "-",
  ]);
  if (avgFcf !== null || avgPct !== null) {
    tableRows.push([
      `${years}年平均`,
      "-",
      "-",
      `<strong>${formatCF(avgFcf)}</strong>`,
      `<strong>${avgPct !== null ? formatFloat2(avgPct) + "%" : "-"}</strong>`,
    ]);
  }

  renderTable(
    $("fcfTable"),
    ["年度", "營業現金流", "資本支出", "自由現金流", "佔股本 %"],
    tableRows
  );
}

// ── NEW: Free Cash Flow (quarterly cumulative) ───────────────────────────────

function plotFreeCashFlowQuarterly(data, stockId, years) {
  clearSkeleton("fcfQuarterlyChart");
  const rows = (data && data.rows) || [];
  const el = $("fcfQuarterlyChart");
  if (!rows.length) {
    if (el) el.textContent = "查無季累積自由現金流量資料（需要現金流量表資料）。";
    return;
  }

  const labels = rows.map((r) => r.quarter_label);
  const opcf = rows.map((r) => r.operating_cf !== null ? r.operating_cf / 1000 : null); // 百萬
  const capex = rows.map((r) => r.capex !== null ? -r.capex / 1000 : null);
  const fcf = rows.map((r) => r.fcf !== null ? r.fcf / 1000 : null);

  const traces = [
    {
      x: labels, y: opcf, type: "bar", name: "營業現金流（累計）",
      marker: { color: "#3b82f6", opacity: 0.75 },
      hovertemplate: "%{x}<br>營業CF：%{y:,.1f} M<extra></extra>",
    },
    {
      x: labels, y: capex, type: "bar", name: "資本支出（負）",
      marker: { color: "#f97316", opacity: 0.75 },
      hovertemplate: "%{x}<br>資本支出：%{y:,.1f} M<extra></extra>",
    },
    {
      x: labels, y: fcf, type: "scatter", mode: "lines+markers", name: "自由現金流（累計）",
      line: { color: "#22c55e", width: 2 },
      marker: { color: "#22c55e", size: 6 },
      connectgaps: false,
      hovertemplate: "%{x}<br>自由CF：%{y:,.1f} M<extra></extra>",
    },
  ];

  const layout = baseChartLayout(`${stockId} 自由現金流量（季累積，${years}年）`, {
    margin: isCompactViewport() ? { l: 44, r: 20, t: 48, b: 64 } : { l: 60, r: 30, t: 50, b: 60 },
    barmode: "group",
    xaxis: { type: "category", tickangle: -45 },
    yaxis: { title: "金額（百萬元）", tickformat: ",.0f", zeroline: true },
  });

  Plotly.newPlot("fcfQuarterlyChart", traces, layout, PLOTLY_CONFIG);

  const tableRows = [...rows].reverse().map((r) => [
    r.quarter_label,
    formatCF(r.operating_cf),
    formatCF(r.capex),
    formatCF(r.fcf),
  ]);
  renderTable(
    $("fcfQuarterlyTable"),
    ["季度", "營業現金流（累計）", "資本支出", "自由現金流（累計）"],
    tableRows
  );
}

// ── NEW: DCF valuation ───────────────────────────────────────────────────────

function renderDcf(data) {
  const container = $("dcfResult");
  clearSkeleton("dcfResult");
  if (!container) return;

  if (!data || data.fair_value == null) {
    container.innerHTML = '<p class="dcf-na">查無足夠財報資料（需要近期 EPS 及配息率歷史）。</p>';
    return;
  }

  const fv = data.fair_value;
  const cp = data.current_price;
  const ep = data.entry_price;
  const mos = data.margin_of_safety_pct;
  const eps = data.eps_avg;
  const pr = data.payout_ratio;
  const yrs = data.params && data.params.years_for_avg;

  let statusClass = "neutral";
  let statusText = "";
  if (cp != null) {
    if (cp <= ep) {
      statusClass = "up";
      statusText = "✓ 目前股價低於買入門檻，具安全邊際";
    } else if (cp <= fv) {
      statusClass = "neutral";
      statusText = "目前股價低於公允價值（位於安全邊際區間上方）";
    } else {
      statusClass = "down";
      statusText = "✗ 目前股價高於公允價值，估值偏貴";
    }
  }

  container.innerHTML = `
    <div class="dcf-grid">
      <div class="dcf-item">
        <div class="dcf-label">公允價值</div>
        <div class="dcf-value dcf-primary">NT$ ${formatFloat2(fv)}</div>
      </div>
      <div class="dcf-item">
        <div class="dcf-label">目前股價</div>
        <div class="dcf-value">${cp != null ? "NT$ " + formatFloat2(cp) : "-"}</div>
      </div>
      <div class="dcf-item">
        <div class="dcf-label">建議買入價（含安全邊際）</div>
        <div class="dcf-value">NT$ ${formatFloat2(ep)}</div>
      </div>
      <div class="dcf-item">
        <div class="dcf-label">相對公允價值折溢價</div>
        <div class="dcf-value dcf-${statusClass}">${mos != null ? formatSignedPercent(mos) : "-"}</div>
      </div>
    </div>
    <div class="dcf-basis">
      計算基礎（${yrs || "?"} 年平均）：EPS = ${formatFloat2(eps)} 元 ｜ 配息率 = ${pr != null ? formatFloat2(pr * 100) + "%" : "-"}
    </div>
    ${statusText ? `<div class="dcf-status dcf-status-${statusClass}">${statusText}</div>` : ""}
  `;
}

// ── 股本形成 (Capital Formation) ──────────────────────────────────────────────

function renderCapitalFormation(data) {
  const panel = $("capitalFormationPanel");
  clearSkeleton("capitalFormationPanel");
  if (!panel) return;
  panel.innerHTML = "";

  const rows = (data && data.rows) || [];
  if (!rows.length) {
    panel.textContent = "查無股本形成資料。";
    return;
  }

  if (data.note) {
    const noteEl = document.createElement("p");
    noteEl.className = "chart-note";
    noteEl.style.color = "var(--accent)";
    noteEl.textContent = data.note;
    panel.appendChild(noteEl);
  }

  const years  = rows.map(r => String(r.year));
  const cash   = rows.map(r => r.cash);
  const earn   = rows.map(r => r.earnings);
  const other  = rows.map(r => r.other);

  // Stacked bar chart
  const barDiv = document.createElement("div");
  barDiv.className = "chart";
  barDiv.style.height = "320px";
  panel.appendChild(barDiv);

  const barTraces = [
    {
      x: years, y: cash, name: "現金增資",
      type: "bar", marker: { color: "#3b82f6" },
      hovertemplate: "現金增資<br>%{x} 年：%{y:.2f} 億元<extra></extra>",
    },
    {
      x: years, y: earn, name: "盈餘轉增資",
      type: "bar", marker: { color: "#22c55e" },
      hovertemplate: "盈餘轉增資<br>%{x} 年：%{y:.2f} 億元<extra></extra>",
    },
    {
      x: years, y: other, name: "其他",
      type: "bar", marker: { color: "#f59e0b" },
      hovertemplate: "其他<br>%{x} 年：%{y:.2f} 億元<extra></extra>",
    },
  ];

  Plotly.newPlot(
    barDiv,
    barTraces,
    baseChartLayout("累計股本形成結構（億元）", {
      height: 320,
      barmode: "stack",
      xaxis: { title: "年份", type: "category" },
      yaxis: { title: "累計金額（億元）", rangemode: "tozero" },
      legend: { orientation: "h", x: 0, y: -0.2 },
      margin: { l: 60, r: 20, t: 50, b: 60 },
    }),
    { ...PLOTLY_CONFIG, responsive: true }
  );

  // Pie chart for latest year
  const latest = rows[rows.length - 1];
  const pieSection = document.createElement("div");
  pieSection.style.marginTop = "20px";

  const pieTitle = document.createElement("h3");
  pieTitle.className = "subchart-title";
  pieTitle.textContent = `最近年度股本結構圓餅圖（${latest.year} 年）`;
  pieSection.appendChild(pieTitle);

  const pieDiv = document.createElement("div");
  pieDiv.style.height = "300px";
  pieSection.appendChild(pieDiv);
  panel.appendChild(pieSection);

  const pieLabels = ["現金增資", "盈餘轉增資", "其他"];
  const pieValues = [latest.cash, latest.earnings, latest.other];
  const pieColors = ["#3b82f6", "#22c55e", "#f59e0b"];

  Plotly.newPlot(
    pieDiv,
    [{
      type: "pie",
      labels: pieLabels,
      values: pieValues,
      marker: { colors: pieColors },
      textinfo: "percent+label",
      sort: false,
      hole: 0.4,
      hovertemplate: "%{label}<br>%{value:.2f} 億元（%{percent}）<extra></extra>",
    }],
    baseChartLayout(`股本結構（${latest.year}）`, {
      height: 300,
      margin: { l: 20, r: 20, t: 50, b: 20 },
      legend: { orientation: "h", x: 0, y: -0.15 },
    }),
    { ...PLOTLY_CONFIG, responsive: true }
  );

  const srcNote = document.createElement("p");
  srcNote.className = "chart-note";
  srcNote.style.marginTop = "8px";
  srcNote.textContent = `資料來源：${data.source || "未知"}。數值為歷年累計（億元）。`;
  panel.appendChild(srcNote);
}

// ── NEW: Shareholder structure distribution (集保股權分散，逐週) ──────────────

// 15-step palette: cool blues (小額散戶) → warm reds (大戶).
const SPREAD_COLORS = [
  "#0ea5e9", "#22d3ee", "#2dd4bf", "#34d399", "#a3e635",
  "#fde047", "#fbbf24", "#fb923c", "#f97316", "#f87171",
  "#ef4444", "#dc2626", "#e11d48", "#be123c", "#9f1239",
];

function renderShareholdingSpread(data) {
  const panel = $("shareholdingSpreadPanel");
  clearSkeleton("shareholdingSpreadPanel");
  if (!panel) return;
  panel.innerHTML = "";

  const levels = (data && data.levels) || [];
  const dates = (data && data.dates) || [];
  const percent = (data && data.percent) || {};

  if (!dates.length) {
    panel.textContent = (data && data.error) || "查無集保股權分散資料。";
    return;
  }

  // Show TDCC data-source note (non-critical) if backend fell back to scraper
  if (data && data.error) {
    const noteEl = document.createElement("p");
    noteEl.className = "chart-note";
    noteEl.style.color = "var(--accent)";
    noteEl.textContent = data.error;
    panel.appendChild(noteEl);
  }

  const dark = isDarkMode();
  const labelOf = (lv) => lv.label;

  // ── Stacked area chart over weekly dates ──────────────────────────────
  const stackedDiv = document.createElement("div");
  stackedDiv.className = "chart";
  stackedDiv.style.height = "320px";
  panel.appendChild(stackedDiv);

  const traces = levels.map((lv, i) => ({
    x: dates,
    y: percent[lv.level] || [],
    type: "scatter",
    mode: "lines",
    stackgroup: "one",
    name: lv.label,
    line: { width: 0.5, color: SPREAD_COLORS[i] },
    fillcolor: SPREAD_COLORS[i],
    hovertemplate: `${lv.label}<br>%{x}<br>%{y:.2f}%<extra></extra>`,
  }));

  Plotly.newPlot(
    stackedDiv,
    traces,
    baseChartLayout("持股級距占比堆疊（逐週）", {
      height: 320,
      hovermode: "closest",
      xaxis: { type: "date", tickformat: "%Y/%m", rangeslider: { visible: true, thickness: 0.07 } },
      yaxis: { title: "占比 (%)", rangemode: "tozero", ticksuffix: "%" },
      legend: { orientation: "v", x: 1.02, y: 0.5, font: { size: 10 } },
      margin: { l: 52, r: 160, t: 50, b: 36 },
    }),
    { ...PLOTLY_CONFIG, responsive: true }
  );

  // ── Pie chart for a selected week, driven by a time slider ────────────
  const pieSection = document.createElement("div");
  pieSection.style.marginTop = "20px";

  const pieTitle = document.createElement("h3");
  pieTitle.className = "subchart-title";
  pieTitle.textContent = "持股結構圓餅圖（拖動時間軸選取週別）";
  pieSection.appendChild(pieTitle);

  const sliderRow = document.createElement("div");
  sliderRow.className = "slider-row";

  const slider = document.createElement("input");
  slider.type = "range";
  slider.min = "0";
  slider.max = String(dates.length - 1);
  slider.value = String(dates.length - 1);
  slider.className = "time-slider";

  const sliderLabel = document.createElement("span");
  sliderLabel.className = "slider-label";
  sliderLabel.textContent = dates[dates.length - 1] || "";

  sliderRow.appendChild(slider);
  sliderRow.appendChild(sliderLabel);
  pieSection.appendChild(sliderRow);

  const pieDiv = document.createElement("div");
  pieDiv.style.height = "340px";
  pieSection.appendChild(pieDiv);
  panel.appendChild(pieSection);

  function updatePie(idx) {
    sliderLabel.textContent = dates[idx] || "";
    const labels = [];
    const values = [];
    const colors = [];
    levels.forEach((lv, i) => {
      const v = (percent[lv.level] || [])[idx];
      if (v != null && !Number.isNaN(v)) {
        labels.push(labelOf(lv));
        values.push(v);
        colors.push(SPREAD_COLORS[i]);
      }
    });
    if (!values.length) {
      Plotly.purge(pieDiv);
      pieDiv.textContent = "此週無比例資料";
      return;
    }
    Plotly.react(
      pieDiv,
      [{
        type: "pie",
        labels,
        values,
        marker: { colors },
        textinfo: "percent",
        sort: false,
        direction: "clockwise",
        hovertemplate: "%{label}<br>%{value:.2f}%<extra></extra>",
        hole: 0.4,
      }],
      baseChartLayout(`持股結構 ${dates[idx] || ""}`, {
        height: 340,
        hovermode: "closest",
        margin: { l: 20, r: 20, t: 50, b: 20 },
        legend: { orientation: "v", x: 1.0, y: 0.5, font: { size: 10 } },
      }),
      { ...PLOTLY_CONFIG, responsive: true }
    );
  }

  slider.addEventListener("input", () => updatePie(Number(slider.value)));
  updatePie(dates.length - 1);

  const note = document.createElement("p");
  note.className = "chart-note";
  note.style.marginTop = "8px";
  note.textContent = "資料來源：FinMind 集保戶股權分散表（逐週）。級距 1 為小額散戶、級距 15 為持股逾百萬股大戶。";
  panel.appendChild(note);
}

// ── NEW: Shareholding ────────────────────────────────────────────────────────

function renderShareholding(data) {
  const container = $("shareholdingTable");
  clearSkeleton("shareholdingTable");
  if (!container) return;
  container.innerHTML = "";

  const rows = (data && data.rows) || [];

  if (!rows.length) {
    container.textContent = "查無董監事持股資料（Goodinfo / MOPS 均無資料）。";
    return;
  }

  const source = data.source || "unknown";

  // Sort by date ascending
  const sorted = [...rows].filter((r) => r.date).sort((a, b) => a.date.localeCompare(b.date));

  // ── Check if we have Goodinfo time-series (has non_indep_shares field) ──
  const hasTimeSeries = sorted.some(
    (r) => r.non_indep_shares != null || r.total_dir_shares != null || r.foreign_shares != null
  );

  if (!hasTimeSeries) {
    // MOPS single-snapshot fallback: just show a simple table
    const persons = sorted[0]?._persons || [];
    const note = document.createElement("p");
    note.className = "chart-note";
    note.textContent = `資料日期：${sorted[0]?.date || "—"}（資料來源：${source}）`;
    container.appendChild(note);
    renderTable(
      container,
      ["姓名 / 機構", "身分別", "持股（張）", "持股比例 (%)"],
      persons.map((r) => [
        r.person_name || "-",
        r.person_type || "-",
        r.shares != null ? formatNumber(r.shares) : "-",
        r.ratio != null ? formatFloat2(r.ratio) : "-",
      ])
    );
    return;
  }

  const dates = sorted.map((r) => r.date);

  // Theme-aware chart colors (kept in sync with the active light/dark theme).
  const shDark = isDarkMode();
  const shBg = shDark ? "#1e293b" : "#ffffff";
  const shFont = shDark ? "#e2e8f0" : "#374151";

  // Helper: extract series, replace null with null (gaps are fine)
  function col(field) {
    return sorted.map((r) => (r[field] != null ? r[field] : null));
  }

  const nonIndepShares   = col("non_indep_shares");
  const nonIndepPledged  = col("non_indep_pledged");
  const nonIndepRatio    = col("non_indep_ratio");
  const indepShares      = col("indep_shares");
  const indepPledged     = col("indep_pledged");
  const indepRatio       = col("indep_ratio");
  const totalDirShares   = col("total_dir_shares");
  const totalDirPledged  = col("total_dir_pledged");
  const totalDirRatio    = col("total_dir_ratio");
  const foreignShares    = col("foreign_shares");
  const foreignRatio     = col("foreign_ratio");
  const issuedShares     = col("total_issued_shares");

  const CHART_H = 180;
  const MARGIN = { l: 70, r: 70, t: 30, b: 30 };

  const XAXIS_SLIDER = {
    rangeslider: { visible: true, thickness: 0.08 },
    type: "date",
    tickformat: "%Y/%m",
  };
  const XAXIS_PLAIN = { type: "date", tickformat: "%Y/%m", matches: "x" };

  // ── Chart 1: 非獨立董監 ──────────────────────────────────────────────
  const div1 = document.createElement("div");
  div1.className = "chart";
  div1.style.height = CHART_H + "px";
  container.appendChild(div1);

  Plotly.newPlot(
    div1,
    [
      {
        x: dates, y: nonIndepShares, type: "bar", name: "非獨立董監持股張數",
        marker: { color: "#f59e0b" }, yaxis: "y",
        hovertemplate: "%{x}<br>持股：%{y:,.0f} 張<extra>非獨立董監</extra>",
      },
      {
        x: dates, y: nonIndepPledged, type: "bar", name: "非獨立董監質押張數",
        marker: { color: "#ef4444", opacity: 0.7 }, yaxis: "y",
        hovertemplate: "%{x}<br>質押：%{y:,.0f} 張<extra>非獨立董監</extra>",
      },
      {
        x: dates, y: nonIndepRatio, type: "scatter", mode: "lines", name: "非獨立持股比率",
        line: { color: "#dc2626", width: 1.5 }, yaxis: "y2", connectgaps: false,
        hovertemplate: "%{x}<br>比率：%{y:.2f}%<extra></extra>",
      },
    ],
    {
      height: CHART_H, margin: MARGIN, barmode: "overlay",
      xaxis: { ...XAXIS_SLIDER },
      yaxis: { title: "張數", tickformat: ",.0f", side: "left" },
      yaxis2: { title: "%", overlaying: "y", side: "right", tickformat: ".2f" },
      legend: { orientation: "h", y: 1.15, x: 0, font: { size: 11 } },
      title: { text: "非獨立董監持股", font: { size: 13 }, x: 0.01 },
      plot_bgcolor: shBg, paper_bgcolor: shBg,
      font: { color: shFont },
    },
    { ...PLOTLY_CONFIG, responsive: true }
  );

  // ── Chart 2: 獨立董監 ────────────────────────────────────────────────
  const div2 = document.createElement("div");
  div2.className = "chart";
  div2.style.height = CHART_H + "px";
  container.appendChild(div2);

  Plotly.newPlot(
    div2,
    [
      {
        x: dates, y: indepShares, type: "bar", name: "獨立董監持股張數",
        marker: { color: "#f59e0b" }, yaxis: "y",
        hovertemplate: "%{x}<br>持股：%{y:,.0f} 張<extra>獨立董監</extra>",
      },
      {
        x: dates, y: indepPledged, type: "bar", name: "獨立董監質押張數",
        marker: { color: "#ef4444", opacity: 0.7 }, yaxis: "y",
        hovertemplate: "%{x}<br>質押：%{y:,.0f} 張<extra>獨立董監</extra>",
      },
      {
        x: dates, y: indepRatio, type: "scatter", mode: "lines", name: "獨立持股比率",
        line: { color: "#dc2626", width: 1.5 }, yaxis: "y2", connectgaps: false,
        hovertemplate: "%{x}<br>比率：%{y:.2f}%<extra></extra>",
      },
    ],
    {
      height: CHART_H, margin: MARGIN, barmode: "overlay",
      xaxis: XAXIS_PLAIN,
      yaxis: { title: "張數", tickformat: ",.0f", side: "left" },
      yaxis2: { title: "%", overlaying: "y", side: "right", tickformat: ".2f" },
      legend: { orientation: "h", y: 1.15, x: 0, font: { size: 11 } },
      title: { text: "獨立董監持股", font: { size: 13 }, x: 0.01 },
      plot_bgcolor: shBg, paper_bgcolor: shBg,
      font: { color: shFont },
    },
    { ...PLOTLY_CONFIG, responsive: true }
  );

  // ── Chart 3: 全體董監 ────────────────────────────────────────────────
  const div3 = document.createElement("div");
  div3.className = "chart";
  div3.style.height = CHART_H + "px";
  container.appendChild(div3);

  Plotly.newPlot(
    div3,
    [
      {
        x: dates, y: totalDirShares, type: "bar", name: "全體董監持股張數",
        marker: { color: "#f59e0b" }, yaxis: "y",
        hovertemplate: "%{x}<br>持股：%{y:,.0f} 張<extra>全體董監</extra>",
      },
      {
        x: dates, y: totalDirPledged, type: "bar", name: "全體董監質押張數",
        marker: { color: "#ef4444", opacity: 0.7 }, yaxis: "y",
        hovertemplate: "%{x}<br>質押：%{y:,.0f} 張<extra>全體董監</extra>",
      },
      {
        x: dates, y: totalDirRatio, type: "scatter", mode: "lines", name: "全體董監持股比率",
        line: { color: "#dc2626", width: 1.5 }, yaxis: "y2", connectgaps: false,
        hovertemplate: "%{x}<br>比率：%{y:.2f}%<extra></extra>",
      },
    ],
    {
      height: CHART_H, margin: MARGIN, barmode: "overlay",
      xaxis: XAXIS_PLAIN,
      yaxis: { title: "張數", tickformat: ",.0f", side: "left" },
      yaxis2: { title: "%", overlaying: "y", side: "right", tickformat: ".2f" },
      legend: { orientation: "h", y: 1.15, x: 0, font: { size: 11 } },
      title: { text: "全體董監持股", font: { size: 13 }, x: 0.01 },
      plot_bgcolor: shBg, paper_bgcolor: shBg,
      font: { color: shFont },
    },
    { ...PLOTLY_CONFIG, responsive: true }
  );

  // ── Chart 4: 外資 ────────────────────────────────────────────────────
  const div4 = document.createElement("div");
  div4.className = "chart";
  div4.style.height = CHART_H + "px";
  container.appendChild(div4);

  Plotly.newPlot(
    div4,
    [
      {
        x: dates, y: foreignShares, type: "bar", name: "外資持股張數",
        marker: { color: "#3b82f6" }, yaxis: "y",
        hovertemplate: "%{x}<br>持股：%{y:,.0f} 張<extra>外資</extra>",
      },
      {
        x: dates, y: foreignRatio, type: "scatter", mode: "lines", name: "外資持股比率",
        line: { color: "#dc2626", width: 1.5 }, yaxis: "y2", connectgaps: false,
        hovertemplate: "%{x}<br>比率：%{y:.2f}%<extra></extra>",
      },
    ],
    {
      height: CHART_H, margin: MARGIN,
      xaxis: XAXIS_PLAIN,
      yaxis: { title: "張數", tickformat: ",.0f", side: "left" },
      yaxis2: { title: "%", overlaying: "y", side: "right", tickformat: ".2f" },
      legend: { orientation: "h", y: 1.15, x: 0, font: { size: 11 } },
      title: { text: "外資持股", font: { size: 13 }, x: 0.01 },
      plot_bgcolor: shBg, paper_bgcolor: shBg,
      font: { color: shFont },
    },
    { ...PLOTLY_CONFIG, responsive: true }
  );

  // ── Chart 5: 發行張數 ────────────────────────────────────────────────
  const div5 = document.createElement("div");
  div5.className = "chart";
  div5.style.height = CHART_H + "px";
  container.appendChild(div5);

  Plotly.newPlot(
    div5,
    [
      {
        x: dates, y: issuedShares, type: "bar", name: "發行張數",
        marker: { color: "#ef4444" }, yaxis: "y",
        hovertemplate: "%{x}<br>發行張數：%{y:,.0f} 張<extra></extra>",
      },
    ],
    {
      height: CHART_H, margin: MARGIN,
      xaxis: XAXIS_PLAIN,
      yaxis: { title: "張數", tickformat: ",.0f", side: "left" },
      legend: { orientation: "h", y: 1.15, x: 0, font: { size: 11 } },
      title: { text: "【發行張數】", font: { size: 13 }, x: 0.01 },
      plot_bgcolor: shBg, paper_bgcolor: shBg,
      font: { color: shFont },
    },
    { ...PLOTLY_CONFIG, responsive: true }
  );

  // Sync range-slider of chart 1 to charts 2-5
  div1.on("plotly_relayout", (evtData) => {
    const upd = {};
    if (evtData["xaxis.range[0]"]) {
      upd["xaxis.range[0]"] = evtData["xaxis.range[0]"];
      upd["xaxis.range[1]"] = evtData["xaxis.range[1]"];
    } else if (evtData["xaxis.autorange"]) {
      upd["xaxis.autorange"] = true;
    }
    if (Object.keys(upd).length) {
      [div2, div3, div4, div5].forEach((d) => Plotly.relayout(d, upd));
    }
  });

  // ── Pie chart with time slider ────────────────────────────────────────
  const pieSection = document.createElement("div");
  pieSection.style.marginTop = "24px";

  const pieTitle = document.createElement("h3");
  pieTitle.style.cssText = "margin:0 0 8px;font-size:14px;color:#94a3b8;";
  pieTitle.textContent = "持股比例圓餅圖（拖動下方時間軸選取月份）";
  pieSection.appendChild(pieTitle);

  const sliderRow = document.createElement("div");
  sliderRow.style.cssText = "display:flex;align-items:center;gap:12px;margin-bottom:8px;";

  const sliderLabel = document.createElement("span");
  sliderLabel.style.cssText = "font-size:12px;color:#94a3b8;min-width:72px;text-align:right;";
  sliderLabel.textContent = dates[dates.length - 1] || "";

  const slider = document.createElement("input");
  slider.type = "range";
  slider.min = "0";
  slider.max = String(sorted.length - 1);
  slider.value = String(sorted.length - 1);
  slider.style.cssText = "flex:1;accent-color:#3b82f6;";

  sliderRow.appendChild(slider);
  sliderRow.appendChild(sliderLabel);
  pieSection.appendChild(sliderRow);

  const pieDiv = document.createElement("div");
  pieDiv.style.cssText = "height:320px;";
  pieSection.appendChild(pieDiv);
  container.appendChild(pieSection);

  function buildPieData(idx) {
    const r = sorted[idx];
    if (!r) return { labels: [], values: [] };

    const ni = r.non_indep_ratio;
    const ind = r.indep_ratio;
    const fo = r.foreign_ratio;
    const tot = r.total_issued_shares;

    // Calculate "其他" category
    let otherPct = null;
    const dirTotal = (r.total_dir_ratio != null ? r.total_dir_ratio : ((ni || 0) + (ind || 0)));
    if (fo != null && dirTotal != null && (fo + dirTotal) <= 100) {
      otherPct = Math.max(0, 100 - fo - dirTotal);
    }

    const labels = [];
    const values = [];
    const colors = [];

    if (ni != null) { labels.push("非獨立董監"); values.push(ni); colors.push("#f59e0b"); }
    if (ind != null) { labels.push("獨立董監"); values.push(ind); colors.push("#a78bfa"); }
    if (fo != null) { labels.push("外資"); values.push(fo); colors.push("#3b82f6"); }
    if (otherPct != null) { labels.push("其他"); values.push(otherPct); colors.push("#64748b"); }

    return { labels, values, colors };
  }

  function updatePie(idx) {
    const { labels, values, colors } = buildPieData(idx);
    const r = sorted[idx];
    sliderLabel.textContent = r?.date || "";

    if (!values.length) {
      Plotly.purge(pieDiv);
      pieDiv.textContent = "此月份無比例資料";
      return;
    }

    Plotly.react(
      pieDiv,
      [
        {
          type: "pie",
          labels,
          values,
          marker: { colors },
          textinfo: "label+percent",
          hovertemplate: "%{label}<br>%{value:.2f}%<extra></extra>",
          hole: 0.35,
        },
      ],
      {
        height: 320,
        margin: { l: 20, r: 20, t: 40, b: 20 },
        title: { text: `持股結構 ${r?.date || ""}`, font: { size: 13 }, x: 0.5 },
        plot_bgcolor: shBg,
        paper_bgcolor: shBg,
        font: { color: shFont },
        legend: { orientation: "v", x: 1.02, y: 0.5 },
      },
      { ...PLOTLY_CONFIG, responsive: true }
    );
  }

  slider.addEventListener("input", () => updatePie(Number(slider.value)));
  updatePie(sorted.length - 1);

  // Source note
  const note = document.createElement("p");
  note.className = "chart-note";
  note.style.marginTop = "8px";
  note.textContent = `資料來源：${source === "goodinfo" ? "Goodinfo.tw" : source}`;
  container.appendChild(note);
}

// ── NEW: Operating Turnover Days ─────────────────────────────────────────────

function plotTurnoverDays(rows, stockId) {
  clearSkeleton("turnoverDaysChart");
  if (!rows || rows.length === 0) {
    const el = $("turnoverDaysChart");
    if (el) el.textContent = "查無營運週轉天數資料（需要損益表及資產負債表資料）。";
    return;
  }

  const labels = rows.map((r) => r.quarter_label || r.quarter);
  const dso = rows.map((r) => r.dso);
  const dio = rows.map((r) => r.dio);
  const dpo = rows.map((r) => r.dpo);
  const ccc = rows.map((r) => r.ccc);

  const traces = [
    {
      x: labels, y: dso, type: "scatter", mode: "lines+markers",
      name: "DSO 應收天數", connectgaps: false,
      line: { color: "#3b82f6", width: 2 }, marker: { size: 5 },
      hovertemplate: "%{x}<br>DSO：%{y:.1f} 天<extra></extra>",
    },
    {
      x: labels, y: dio, type: "scatter", mode: "lines+markers",
      name: "DIO 存貨天數", connectgaps: false,
      line: { color: "#f97316", width: 2 }, marker: { size: 5 },
      hovertemplate: "%{x}<br>DIO：%{y:.1f} 天<extra></extra>",
    },
    {
      x: labels, y: dpo, type: "scatter", mode: "lines+markers",
      name: "DPO 應付天數", connectgaps: false,
      line: { color: "#22c55e", width: 2, dash: "dash" }, marker: { size: 5 },
      hovertemplate: "%{x}<br>DPO：%{y:.1f} 天<extra></extra>",
    },
    {
      x: labels, y: ccc, type: "scatter", mode: "lines+markers",
      name: "CCC 現金循環", connectgaps: false,
      line: { color: "#7c3aed", width: 2.5 }, marker: { size: 6 },
      hovertemplate: "%{x}<br>CCC：%{y:.1f} 天<extra></extra>",
    },
  ];

  const layout = baseChartLayout(`${stockId} 營運週轉天數（季度）`, {
    margin: isCompactViewport() ? { l: 44, r: 16, t: 48, b: 72 } : { l: 50, r: 20, t: 50, b: 70 },
    xaxis: { tickangle: -35, type: "category" },
    yaxis: { title: "天數", tickformat: ".0f", zeroline: true },
  });

  Plotly.newPlot("turnoverDaysChart", traces, layout, PLOTLY_CONFIG);

  renderTable(
    $("turnoverDaysTable"),
    ["季度", "DSO (天)", "DIO (天)", "DPO (天)", "CCC (天)"],
    [...rows].reverse().map((r) => [
      r.quarter_label || r.quarter || "-",
      r.dso === null ? "-" : formatFloat2(r.dso),
      r.dio === null ? "-" : formatFloat2(r.dio),
      r.dpo === null ? "-" : formatFloat2(r.dpo),
      r.ccc === null ? "-" : formatFloat2(r.ccc),
    ])
  );
}

// ── NEW: P/E River Chart ──────────────────────────────────────────────────────

function plotPERiver(data, stockId) {
  clearSkeleton("peRiverChart");
  const perRows = (data && data.per_rows) || [];
  const bands = (data && data.bands) || {};

  if (!perRows.length || !bands.p50) {
    const el = $("peRiverChart");
    if (el) el.textContent = "查無本益比資料（需要 TaiwanStockPER 資料）。";
    return;
  }

  const dates = perRows.map((r) => r.date);
  const perVals = perRows.map((r) => r.per);
  const { p5, p25, p50, p75, p95 } = bands;
  const n = dates.length;

  const bandY = (v) => Array(n).fill(v);

  const traces = [
    // p5 → p25 (blue fill)
    { x: dates, y: bandY(p5), type: "scatter", mode: "lines", line: { width: 0 }, showlegend: false, hoverinfo: "skip" },
    {
      x: dates, y: bandY(p25), type: "scatter", mode: "lines",
      fill: "tonexty", fillcolor: "rgba(59,130,246,0.15)",
      line: { width: 0.5, color: "rgba(59,130,246,0.3)" },
      name: `低估區 (≤P25 ${p25 != null ? p25.toFixed(1) : "?"}x)`, hoverinfo: "skip",
    },
    // p25 → p75 (green fill)
    { x: dates, y: bandY(p25), type: "scatter", mode: "lines", line: { width: 0 }, showlegend: false, hoverinfo: "skip" },
    {
      x: dates, y: bandY(p75), type: "scatter", mode: "lines",
      fill: "tonexty", fillcolor: "rgba(34,197,94,0.12)",
      line: { width: 0.5, color: "rgba(34,197,94,0.3)" },
      name: "合理區 (P25–P75)", hoverinfo: "skip",
    },
    // p75 → p95 (red fill)
    { x: dates, y: bandY(p75), type: "scatter", mode: "lines", line: { width: 0 }, showlegend: false, hoverinfo: "skip" },
    {
      x: dates, y: bandY(p95), type: "scatter", mode: "lines",
      fill: "tonexty", fillcolor: "rgba(239,68,68,0.12)",
      line: { width: 0.5, color: "rgba(239,68,68,0.3)" },
      name: `高估區 (≥P75 ${p75 != null ? p75.toFixed(1) : "?"}x)`, hoverinfo: "skip",
    },
    // Median dashed line
    {
      x: dates, y: bandY(p50), type: "scatter", mode: "lines",
      name: `中位數 ${p50 != null ? p50.toFixed(1) : "?"}x`,
      line: { color: "rgba(100,116,139,0.7)", width: 1.5, dash: "dash" },
      hovertemplate: `中位數 PER：${p50 != null ? p50.toFixed(1) : "?"}x<extra></extra>`,
    },
    // Actual PER
    {
      x: dates, y: perVals, type: "scatter", mode: "lines",
      name: "本益比 (PER)", connectgaps: false,
      line: { color: "#1e40af", width: 2 },
      hovertemplate: "%{x}<br>PER：%{y:.1f}x<extra></extra>",
    },
  ];

  const layout = baseChartLayout(`${stockId} 本益比河流圖`, {
    xaxis: { tickformat: "%Y-%m" },
    yaxis: { title: "本益比（倍）", tickformat: ".1f", rangemode: "tozero" },
  });

  Plotly.newPlot("peRiverChart", traces, layout, PLOTLY_CONFIG);

  const bandsEl = $("peRiverBands");
  if (bandsEl) {
    renderMetrics(bandsEl, [
      { key: "P5 極低", value: p5 != null ? `${p5.toFixed(1)}x` : "-" },
      { key: "P25 低估", value: p25 != null ? `${p25.toFixed(1)}x` : "-" },
      { key: "P50 中位", value: p50 != null ? `${p50.toFixed(1)}x` : "-" },
      { key: "P75 高估", value: p75 != null ? `${p75.toFixed(1)}x` : "-" },
      { key: "P95 極高", value: p95 != null ? `${p95.toFixed(1)}x` : "-" },
    ]);
  }
}

function renderDividendTable(cashRows, priceRows) {
  clearSkeleton("dividendTable");
  const cashByYear = new Map();
  for (const r of cashRows || []) {
    const m = r && r.month ? String(r.month) : "";
    const year = m.length >= 4 ? m.slice(0, 4) : "";
    const v = r && r.cash_dividend !== null && r.cash_dividend !== undefined ? Number(r.cash_dividend) : NaN;
    if (!year || Number.isNaN(v) || v === 0) continue;
    cashByYear.set(year, (cashByYear.get(year) || 0) + v);
  }

  const janPriceByYear = new Map();
  for (const r of priceRows || []) {
    const m = r && r.month ? String(r.month) : "";
    if (m.length < 10) continue;
    const year = m.slice(0, 4);
    const monthPart = m.slice(5, 7);
    if (monthPart !== "01") continue;
    const close = r && r.close !== null && r.close !== undefined ? Number(r.close) : NaN;
    if (!year || Number.isNaN(close)) continue;
    janPriceByYear.set(year, close);
  }

  const years = Array.from(new Set([...cashByYear.keys(), ...janPriceByYear.keys()]))
    .sort()
    .reverse();

  if (years.length === 0) {
    const el = $("dividendTable");
    if (el) el.textContent = "查無配息資料。";
    return;
  }

  const rows = years.map((y) => {
    const cash = cashByYear.get(y);
    const jan = janPriceByYear.get(y);
    const yieldPct =
      cash !== undefined && jan !== undefined && jan !== 0 ? (cash / jan) * 100 : undefined;

    return [
      y,
      cash === undefined ? "-" : formatFloat2(cash),
      jan === undefined ? "-" : formatFloat2(jan),
      yieldPct === undefined ? "-" : `${formatFloat2(yieldPct)}%`,
    ];
  });

  renderTable($("dividendTable"), ["年度", "配息金額(元)", "1月價格(元)", "殖利率(%)"], rows);
}

// ============================================================
// === NEW: Progress Bar ===
// ============================================================

function progressStart() {
  const el = $("topProgress");
  if (!el) return;
  el.style.width = "0%";
  el.className = "top-progress active";
  el.style.width = "30%";
}
function progressAdvance(pct) {
  const el = $("topProgress");
  if (!el) return;
  el.style.width = pct + "%";
}
function progressDone(isError) {
  const el = $("topProgress");
  if (!el) return;
  el.style.width = "100%";
  el.className = "top-progress active " + (isError ? "error" : "success");
  setTimeout(() => {
    el.style.opacity = "0";
    setTimeout(() => { el.className = "top-progress"; el.style.width = "0%"; el.style.opacity = ""; }, 400);
  }, 600);
}

// ============================================================
// === NEW: Error Retry Helper ===
// ============================================================

function showRetryError(elementId, message, retryFn) {
  const el = $(elementId);
  if (!el) return;
  el.classList.remove("skeleton-section");
  el.innerHTML = `
    <div class="error-block">
      <span>${message}</span>
      <button class="retry-btn" onclick="(${retryFn.toString()})()">重試</button>
    </div>`;
}

// ============================================================
// === Buy Score Tab Renderer ===
// ============================================================

function renderBuyScore(data) {
  clearSkeleton("buyScoreSkeleton");

  const header    = $("buyScoreHeader");
  const stage1El  = $("buyScoreStage1");
  const stage2El  = $("buyScoreStage2");
  const skeleton  = $("buyScoreSkeleton");

  if (!data || !Array.isArray(data.criteria)) {
    if (skeleton) skeleton.textContent = "買入評分資料格式錯誤";
    return;
  }

  // ── Hide skeleton, show panels ───────────────────────────────
  if (skeleton) skeleton.style.display = "none";
  if (header)   header.style.display = "block";
  if (stage1El) stage1El.style.display = "block";
  if (stage2El) stage2El.style.display = "block";

  // ── Total score & signal badge ────────────────────────────────
  const totalEl = $("buyScoreTotal");
  if (totalEl) totalEl.textContent = String(data.score);
  const denomEl = $("buyScoreDenom");
  if (denomEl) denomEl.textContent = `/${data.max_score ?? 24}`;

  const badge = $("buyScoreSignalBadge");
  if (badge) {
    const classMap = {
      strong_buy: "strong-buy",
      buy:        "buy",
      watch:      "watch",
      neutral:    "neutral",
    };
    badge.textContent = data.recommendation_label || data.signal_label || "—";
    badge.className = "buy-signal-badge " + (classMap[data.signal] || "");
  }

  // ── Warnings ─────────────────────────────────────────────────
  const warnEl = $("buyScoreWarnings");
  if (warnEl && data.warnings && data.warnings.length > 0) {
    warnEl.textContent = "⚠ " + data.warnings.join("；");
  }

  // ── Total score label ─────────────────────────────────────────
  const s2Label = $("stage2ScoreLabel");
  if (s2Label) {
    const rateText = (data.pass_rate !== null && data.pass_rate !== undefined)
      ? ` (${data.pass_rate.toFixed ? data.pass_rate.toFixed(1) : data.pass_rate}%)`
      : "";
    s2Label.textContent = `${data.score}/${data.max_score}${rateText}`;
  }

  // ── Render criteria items ─────────────────────────────────────
  const stage1Criteria = data.criteria.filter(c => c.weight === 2);
  const stage2Criteria = data.criteria.filter(c => c.weight === 1);
  const allCriteria = data.criteria;

  // Show industry badge if available
  const industryBadgeEl = $("buyScoreIndustryBadge");
  if (industryBadgeEl) {
    if (data.industry) {
      industryBadgeEl.textContent = data.industry;
      industryBadgeEl.style.display = "inline-block";
    } else {
      industryBadgeEl.style.display = "none";
    }
  }

  function criterionHTML(c) {
    let icon, valueClass;
    if (c.not_applicable) {
      icon = "—"; valueClass = "unknown";
    } else if (c.pass === true) {
      icon = "✅"; valueClass = "pass";
    } else if (c.pass === false) {
      icon = "❌"; valueClass = "fail";
    } else {
      icon = "⬜"; valueClass = "unknown";
    }

    const disabled = (c.pass === null) ? " disabled" : "";
    const naStyle = c.not_applicable ? " style=\"opacity:0.45\"" : "";
    const weightBadge = c.weight === 2
      ? `<span class="criterion-weight-badge">×2</span>`
      : "";
    const naTag = c.not_applicable
      ? `<span style="font-size:10px;color:var(--text-muted);margin-left:6px;">產業不適用</span>`
      : "";
    const warning = c.warning && !c.not_applicable
      ? `<div class="criterion-warning">⚠ ${c.warning}</div>`
      : "";

    return `
      <div class="criterion-item${disabled}"${naStyle}>
        <div class="criterion-icon">${icon}</div>
        <div class="criterion-body">
          <div class="criterion-label">${c.label}${weightBadge}${naTag}</div>
          <div class="criterion-detail">${c.threshold}</div>
          ${warning}
        </div>
        <div class="criterion-value ${valueClass}">${c.not_applicable ? "產業不適用" : (c.value_label || "—")}</div>
      </div>`;
  }

  const s1Container = $("criteriaStage1");
  const hasStage1 = stage1Criteria.length > 0;
  if (s1Container) s1Container.innerHTML = hasStage1 ? stage1Criteria.map(criterionHTML).join("") : "";

  if (stage1El) stage1El.style.display = hasStage1 ? "block" : "none";

  const s2Container = $("criteriaStage2");
  if (s2Container) {
    const renderItems = hasStage1 ? stage2Criteria : allCriteria;
    s2Container.innerHTML = renderItems.map(criterionHTML).join("");
  }

  // ======= NEW: Risk Rendering =======
  const riskCriteria = data.risk_criteria || [];
  const riskScore = data.risk_score || 0;
  const riskEl = $("buyScoreRisk");
  const riskContainer = $("criteriaRisk");
  const riskLabel = $("riskScoreLabel");
  
  if (riskCriteria.length > 0 && riskEl && riskContainer) {
    riskEl.style.display = "block";
    if (riskLabel) riskLabel.textContent = `(觸發 ${riskScore} 項警示)`;

    // Group by category
    const groups = {};
    riskCriteria.forEach(c => {
      const cat = c.category || "其他";
      if (!groups[cat]) groups[cat] = [];
      groups[cat].push(c);
    });
    const catOrder = ["財務造假", "財務惡化", "籌碼治理", "籌碼排雷", "估值排雷", "品質排雷", "獲利排雷", "存貨排雷", "配息排雷", "其他"];
    const sortedCats = Object.keys(groups).sort((a, b) => {
      const ia = catOrder.indexOf(a), ib = catOrder.indexOf(b);
      return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
    });

    riskContainer.innerHTML = sortedCats.map(cat => {
      const items = groups[cat];
      const rows = items.map(c => `
        <div class="criterion-row" style="background:var(--bg-faint);">
          <div class="criterion-name" style="align-items:center;">
            <span style="color:#ef4444; margin-right:4px;">●</span>
            ${c.name}
            <div class="criterion-desc" style="margin-left:12px;font-size:11px;color:var(--text-muted)">${c.description}</div>
          </div>
          <div class="criterion-value risk-val" style="color:#ef4444; font-weight:600;">${c.value_label}</div>
        </div>`).join("");
      return `
        <div style="margin-top:8px;">
          <div style="font-size:11px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em;padding:4px 0 2px;border-bottom:1px solid var(--border);">
            ${cat} <span style="font-weight:400;">(${items.length})</span>
          </div>
          ${rows}
        </div>`;
    }).join("");
  } else if (riskEl) {
    riskEl.style.display = "none";
  }

  // Update risk badge in tab-basic
  const riskBadge = $("riskBadgeSummary");
  const riskBadgeCount = $("riskBadgeCount");
  if (riskBadge) {
    if (riskCriteria.length > 0) {
      riskBadge.style.display = "flex";
      if (riskBadgeCount) riskBadgeCount.textContent = String(riskCriteria.length);
    } else {
      riskBadge.style.display = "none";
    }
  }
  // ===================================
}

// ============================================================
// === NEW: Tab Navigation ===
// ============================================================

function initTabs() {
  const activePanel = document.querySelector(".tab-panel.active");
  if (activePanel && activePanel.id) {
    tabState.current = activePanel.id;
    tabState.history = [activePanel.id];
  }

  document.querySelectorAll(".tab-btn, .bottom-nav-btn").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab, { recordHistory: true }));
  });

  updateTabBackButtonState();
}

// ============================================================
// === NEW: Margin Ratios Chart ===
// ============================================================

function plotMargins(rows, stockId) {
  clearSkeleton("marginsChart");
  const el = $("marginsChart");
  if (!rows || rows.length === 0) {
    if (el) el.textContent = "查無利潤率資料（需要損益表資料）。";
    return;
  }
  const labels = rows.map((r) => r.quarter_label || r.quarter);
  const gross = rows.map((r) => r.gross_margin);
  const op = rows.map((r) => r.operating_margin);
  const net = rows.map((r) => r.net_margin);

  const traces = [
    { x: labels, y: gross, type: "scatter", mode: "lines+markers", name: "毛利率 (%)",
      line: { color: "#3b82f6", width: 2 }, connectgaps: false,
      hovertemplate: "%{x}<br>毛利率：%{y:.2f}%<extra></extra>" },
    { x: labels, y: op, type: "scatter", mode: "lines+markers", name: "營業利益率 (%)",
      line: { color: "#f59e0b", width: 2 }, connectgaps: false,
      hovertemplate: "%{x}<br>營業利益率：%{y:.2f}%<extra></extra>" },
    { x: labels, y: net, type: "scatter", mode: "lines+markers", name: "淨利率 (%)",
      line: { color: "#10b981", width: 2 }, connectgaps: false,
      hovertemplate: "%{x}<br>淨利率：%{y:.2f}%<extra></extra>" },
  ];
  const layout = baseChartLayout(`${stockId} 利潤率趨勢`, {
    xaxis: { tickangle: -35, type: "category" },
    yaxis: { title: "（%）", tickformat: ".1f", zeroline: true },
  });
  Plotly.newPlot("marginsChart", traces, layout, PLOTLY_CONFIG);

  renderTable($("marginsTable"),
    ["季度", "毛利率 (%)", "營業利益率 (%)", "淨利率 (%)"],
    [...rows].reverse().map((r) => [
      r.quarter_label || r.quarter || "-",
      r.gross_margin === null ? "-" : formatFloat2(r.gross_margin),
      r.operating_margin === null ? "-" : formatFloat2(r.operating_margin),
      r.net_margin === null ? "-" : formatFloat2(r.net_margin),
    ])
  );
}

// ============================================================
// === NEW: EPS Trend Chart ===
// ============================================================

function plotEpsTrend(rows, stockId) {
  clearSkeleton("epsTrendChart");
  const el = $("epsTrendChart");
  if (!rows || rows.length === 0) {
    if (el) el.textContent = "查無 EPS 資料。";
    return;
  }
  const labels = rows.map((r) => r.quarter_label || r.quarter);
  const eps = rows.map((r) => r.eps);
  const yoy = rows.map((r) => r.eps_yoy);

  const traces = [
    { x: labels, y: eps, type: "scatter", mode: "lines+markers", name: "EPS (元)",
      line: { color: "#1d4ed8", width: 2 }, connectgaps: false, yaxis: "y",
      hovertemplate: "%{x}<br>EPS：%{y:.2f} 元<extra></extra>" },
    { x: labels, y: yoy,
      type: "bar", name: "YoY 成長率 (%)", yaxis: "y2",
      marker: { color: yoy.map((v) => (v === null ? "#d1d5db" : v >= 0 ? "#10b981" : "#ef4444")) },
      hovertemplate: "%{x}<br>YoY：%{y:.1f}%<extra></extra>" },
  ];
  const layout = baseChartLayout(`${stockId} EPS 季度趨勢`, {
    xaxis: { tickangle: -35, type: "category" },
    yaxis: { title: "EPS（元）", tickformat: ".2f" },
    yaxis2: { title: "YoY (%)", overlaying: "y", side: "right", tickformat: ".1f", showgrid: false },
    barmode: "overlay",
  });
  Plotly.newPlot("epsTrendChart", traces, layout, PLOTLY_CONFIG);

  renderTable($("epsTrendTable"),
    ["季度", "EPS (元)", "YoY 成長率 (%)"],
    [...rows].reverse().map((r) => [
      r.quarter_label || r.quarter || "-",
      r.eps === null ? "-" : formatFloat2(r.eps),
      r.eps_yoy === null ? "-" : formatSignedPercent(r.eps_yoy),
    ])
  );
}

// ============================================================
// === NEW: Revenue YoY Chart ===
// ============================================================

function plotRevenueYoY(revenueRows, stockId) {
  clearSkeleton("revenueYoyChart");
  const el = $("revenueYoyChart");
  if (!revenueRows || revenueRows.length === 0) {
    if (el) el.textContent = "查無營收資料。";
    return;
  }
  // String-based prev-year key: avoid Date/timezone arithmetic that can shift
  // "YYYY-MM-01" by one day across the year boundary in UTC+8 hosts.
  // r.month from backend is "YYYY-MM-DD".
  const byMonth = new Map(revenueRows.map((r) => [r.month, r.revenue]));
  const prevYearKey = (monthStr) => {
    if (typeof monthStr !== "string" || monthStr.length < 7) return null;
    const y = parseInt(monthStr.slice(0, 4), 10);
    if (!Number.isFinite(y)) return null;
    return `${y - 1}${monthStr.slice(4)}`;
  };

  // Keep only months where YoY is computable — matches the original chart's
  // visible range; pushing the first ~12 months as null made the x-axis stretch
  // left into empty space and looked broken.
  const rows = [];
  for (const r of revenueRows) {
    if (r.revenue === null || r.revenue === undefined || r.month === null) continue;
    const pKey = prevYearKey(r.month);
    const prevVal = pKey ? byMonth.get(pKey) : undefined;
    if (prevVal === null || prevVal === undefined || prevVal === 0) continue;
    rows.push({ month: r.month, yoy: ((r.revenue - prevVal) / Math.abs(prevVal)) * 100 });
  }
  if (rows.length === 0) {
    if (el) el.textContent = "YoY 資料不足（需要至少 13 個月的營收資料）。";
    return;
  }

  const x = rows.map((r) => r.month);
  const y = rows.map((r) => r.yoy);

  // 3-month trailing MA over the YoY series — trend line is null until the window has 2+ points
  const ma3 = y.map((_, i) => {
    const win = y.slice(Math.max(0, i - 2), i + 1);
    if (win.length < 2) return null;
    return win.reduce((a, b) => a + b, 0) / win.length;
  });

  const traces = [
    {
      x, y,
      type: "bar",
      name: "月營收 YoY (%)",
      marker: { color: y.map((v) => (v >= 0 ? "#10b981" : "#ef4444")) },
      hovertemplate: "%{x|%Y-%m}<br>YoY：%{y:.1f}%<extra></extra>",
    },
    {
      x, y: ma3,
      type: "scatter",
      mode: "lines+markers",
      name: "YoY 3 個月趨勢線",
      line: { color: isDarkMode() ? "#e2e8f0" : "#1f2937", width: 2, dash: "dot" },
      marker: { size: 4, color: isDarkMode() ? "#e2e8f0" : "#1f2937" },
      connectgaps: false,
      hovertemplate: "%{x|%Y-%m}<br>YoY MA3：%{y:.1f}%<extra></extra>",
    },
  ];
  const layout = baseChartLayout(`${stockId} 月營收 YoY 成長率`, {
    xaxis: { tickformat: "%Y-%m" },
    yaxis: { title: "YoY (%)", tickformat: ".1f", zeroline: true },
    legend: { orientation: "h", x: 0, y: 1.12 },
  });
  Plotly.newPlot("revenueYoyChart", traces, layout, PLOTLY_CONFIG);
}

// ============================================================
// === NEW: Liquidity Chart ===
// ============================================================

function plotLiquidity(rows, stockId) {
  clearSkeleton("liquidityChart");
  const el = $("liquidityChart");
  if (!rows || rows.length === 0) {
    if (el) el.textContent = "查無流動比率資料。";
    return;
  }
  const labels = rows.map((r) => r.quarter_label || r.quarter);
  const cr = rows.map((r) => r.current_ratio);
  const qr = rows.map((r) => r.quick_ratio);
  const n = labels.length;

  const traces = [
    { x: labels, y: cr, type: "scatter", mode: "lines+markers", name: "流動比率",
      line: { color: "#3b82f6", width: 2 }, connectgaps: false,
      hovertemplate: "%{x}<br>流動比率：%{y:.2f}<extra></extra>" },
    { x: labels, y: qr, type: "scatter", mode: "lines+markers", name: "速動比率",
      line: { color: "#f59e0b", width: 2 }, connectgaps: false,
      hovertemplate: "%{x}<br>速動比率：%{y:.2f}<extra></extra>" },
    { x: [labels[0], labels[n-1]], y: [2, 2], type: "scatter", mode: "lines",
      name: "流動比率安全線 2.0", line: { color: "#3b82f6", width: 1, dash: "dot" }, hoverinfo: "skip" },
    { x: [labels[0], labels[n-1]], y: [1, 1], type: "scatter", mode: "lines",
      name: "速動比率安全線 1.0", line: { color: "#f59e0b", width: 1, dash: "dot" }, hoverinfo: "skip" },
  ];
  const layout = baseChartLayout(`${stockId} 流動比率 / 速動比率`, {
    xaxis: { tickangle: -35, type: "category" },
    yaxis: { title: "比率", tickformat: ".2f" },
  });
  Plotly.newPlot("liquidityChart", traces, layout, PLOTLY_CONFIG);

  renderTable($("liquidityTable"),
    ["季度", "流動比率", "速動比率", "BVPS (元)"],
    [...rows].reverse().map((r) => [
      r.quarter_label || r.quarter || "-",
      r.current_ratio === null ? "-" : formatFloat2(r.current_ratio),
      r.quick_ratio === null ? "-" : formatFloat2(r.quick_ratio),
      r.bvps === null ? "-" : formatFloat2(r.bvps),
    ])
  );
}

// ============================================================
// === NEW: BVPS Chart ===
// ============================================================

function plotBvps(rows, stockId) {
  clearSkeleton("bvpsChart");
  const el = $("bvpsChart");
  if (!rows || rows.length === 0) {
    if (el) el.textContent = "查無 BVPS 資料。";
    return;
  }
  const valid = rows.filter((r) => r.bvps !== null);
  if (valid.length === 0) {
    if (el) el.textContent = "查無 BVPS 資料。";
    return;
  }
  const labels = valid.map((r) => r.quarter_label || r.quarter);
  const bvps = valid.map((r) => r.bvps);
  const traces = [{
    x: labels, y: bvps, type: "scatter", mode: "lines+markers", name: "BVPS (元)",
    line: { color: "#7c3aed", width: 2 },
    hovertemplate: "%{x}<br>BVPS：NT$ %{y:.2f}<extra></extra>",
  }];
  const layout = baseChartLayout(`${stockId} 每股淨值 BVPS`, {
    xaxis: { tickangle: -35, type: "category" },
    yaxis: { title: "BVPS（元）", tickformat: ".2f" },
  });
  Plotly.newPlot("bvpsChart", traces, layout, PLOTLY_CONFIG);
}

// ============================================================
// === NEW: Foreign Holding Chart ===
// ============================================================

function plotForeignHolding(data, stockId) {
  clearSkeleton("foreignHoldingChart");
  const el = $("foreignHoldingChart");
  if (!el) return;

  if (!data || (data.dates && data.dates.length === 0)) {
    const errMsg = (data && data.error) ? data.error : "查無外資持股資料";
    el.textContent = errMsg;
    return;
  }
  const { dates, holding_pct } = data;
  const latest = holding_pct[holding_pct.length - 1];

  const traces = [{
    x: dates, y: holding_pct, type: "scatter", mode: "lines", name: "外資持股 (%)",
    line: { color: "#3b82f6", width: 2 },
    fill: "tozeroy", fillcolor: "rgba(59,130,246,0.1)",
    hovertemplate: "%{x}<br>外資持股：%{y:.2f}%<extra></extra>",
  }];
  const layout = baseChartLayout(`${stockId} 外資持股比例`, {
    xaxis: { tickformat: "%Y-%m" },
    yaxis: { title: "持股 (%)", tickformat: ".1f", rangemode: "tozero" },
    annotations: latest !== undefined ? [{
      x: dates[dates.length - 1], y: latest,
      text: `當前 ${latest.toFixed(2)}%`,
      showarrow: true, arrowhead: 2, arrowcolor: "#3b82f6",
      font: { size: 12, color: "#1d4ed8" }, bgcolor: "#eff6ff",
      bordercolor: "#3b82f6", borderwidth: 1, borderpad: 4,
    }] : [],
  });
  Plotly.newPlot("foreignHoldingChart", traces, layout, PLOTLY_CONFIG);
}

// ============================================================
// === NEW: Valuation Extra Panel ===
// ============================================================

function renderValuationExtra(data) {
  clearSkeleton("valuationExtraPanel");
  const el = $("valuationExtraPanel");
  if (!el) return;

  if (!data) {
    el.innerHTML = '<p class="dcf-na">查無估值資料。</p>';
    return;
  }

  function mosColor(pct) {
    if (pct === null || pct === undefined) return "val-neutral";
    if (pct >= 20) return "val-green";
    if (pct >= 0)  return "val-yellow";
    return "val-red";
  }
  function pegColor(peg) {
    if (peg === null || peg === undefined) return "val-neutral";
    if (peg < 1)  return "val-green";
    if (peg <= 2) return "val-yellow";
    return "val-red";
  }

  const graham = data.graham_number;
  const mos = data.graham_mos_pct;
  const peg = data.peg;
  const cagr = data.eps_cagr;
  const bvps = data.latest_bvps;
  const avgEps = data.avg_eps;
  const per = data.current_per;

  el.innerHTML = `
    <div class="valuation-grid">
      <div class="val-item">
        <div class="val-label">Graham Number</div>
        <div class="val-value ${mosColor(mos)}">${graham !== null ? "NT$ " + formatFloat2(graham) : "N/A"}</div>
        <div class="val-detail">= √(22.5 × EPS × BVPS)</div>
      </div>
      <div class="val-item">
        <div class="val-label">Graham 安全邊際</div>
        <div class="val-value ${mosColor(mos)}">${mos !== null ? formatSignedPercent(mos) : "N/A"}</div>
        <div class="val-detail">(Graham − 現價) / Graham</div>
      </div>
      <div class="val-item">
        <div class="val-label">PEG 比率</div>
        <div class="val-value ${pegColor(peg)}">${peg !== null ? formatFloat2(peg) : (cagr !== null && cagr <= 0 ? "EPS衰退" : "N/A")}</div>
        <div class="val-detail">PER ${per !== null ? formatFloat2(per) : "-"} ÷ EPS 3年CAGR ${cagr !== null && cagr > 0 ? formatFloat2(cagr) + "%" : (cagr !== null ? "衰退，PEG無參考意義" : "-")}</div>
      </div>
      <div class="val-item">
        <div class="val-label">5年平均 EPS</div>
        <div class="val-value val-neutral">${avgEps !== null ? "NT$ " + formatFloat2(avgEps) : "N/A"}</div>
        <div class="val-detail">近 5 年完整年度 EPS 平均</div>
      </div>
      <div class="val-item">
        <div class="val-label">最新 BVPS</div>
        <div class="val-value val-neutral">${bvps !== null ? "NT$ " + formatFloat2(bvps) : "N/A"}</div>
        <div class="val-detail">最近一季每股淨值</div>
      </div>
    </div>
    <p class="chart-note" style="margin-top:10px;">
      PEG &lt; 1 代表成長被低估；PEG &gt; 2 代表成長已充分定價。Graham Number 僅適用於有獲利的傳統產業股。
    </p>`;
}

// ============================================================
// === NEW: Investment Signal Card ===
// ============================================================

function renderSignalCard(state) {
  const card = $("signalCard");
  if (!card) return;

  function setSignal(id, color, detail) {
    const item = $(id);
    if (!item) return;
    const dot = item.querySelector(".signal-dot");
    const det = item.querySelector(".signal-detail");
    if (dot) dot.className = "signal-dot " + color;
    if (det) det.textContent = detail;
  }

  // --- Growth signal ---
  let growthColor = "yellow", growthDetail = "資料不足";
  {
    const eps = state.epsTrendRows;
    const rev = state.revenueRows;
    const lastEpsYoy = eps.length ? eps[eps.length - 1].eps_yoy : null;
    const revYoys = rev.filter((r) => r.revenue !== null).slice(-13);
    let posCount = 0, total = 0;
    if (revYoys.length >= 13) {
      for (let i = 1; i < revYoys.length; i++) {
        const prev = revYoys[i - 1].revenue;
        if (prev && prev !== 0) {
          const yoy = (revYoys[i].revenue - prev) / Math.abs(prev) * 100;
          if (yoy > 0) posCount++;
          total++;
        }
      }
    }
    const revOk = total > 0 ? posCount / total >= 0.6 : null;
    const epsOk = lastEpsYoy !== null ? lastEpsYoy > 0 : null;
    if (epsOk === true && revOk === true) { growthColor = "green"; growthDetail = "EPS 與營收均正成長"; }
    else if (epsOk === false || revOk === false) { growthColor = "red"; growthDetail = "成長動能減弱"; }
    else { growthColor = "yellow"; growthDetail = epsOk === null && revOk === null ? "資料不足" : "混合訊號"; }
  }
  setSignal("sig-growth", growthColor, growthDetail);

  // --- Health signal ---
  let healthColor = "yellow", healthDetail = "資料不足";
  {
    const liq = state.liquidityRows;
    const debt = state.debtRatioRows;
    const roe = state.roeRoaRows;
    const lastLiq = liq.length ? liq[liq.length - 1] : null;
    const lastDebt = debt.length ? debt.filter((r) => r.debt_ratio !== null).at(-1) : null;
    const lastRoe = roe.length ? roe[roe.length - 1] : null;
    let score = 0, checks = 0;
    if (lastLiq && lastLiq.current_ratio !== null) { checks++; if (lastLiq.current_ratio >= 1.5) score++; }
    if (lastDebt && lastDebt.debt_ratio !== null) { checks++; if (lastDebt.debt_ratio < 50) score++; }
    if (lastRoe && lastRoe.roe !== null) { checks++; if (lastRoe.roe >= 10) score++; }
    if (checks === 0) { healthColor = "yellow"; healthDetail = "資料不足"; }
    else if (score === checks) { healthColor = "green"; healthDetail = `全部 ${checks} 項指標健康`; }
    else if (score >= checks * 0.6) { healthColor = "yellow"; healthDetail = `${score}/${checks} 項指標健康`; }
    else { healthColor = "red"; healthDetail = `${score}/${checks} 項指標健康`; }
  }
  setSignal("sig-health", healthColor, healthDetail);

  // --- Valuation signal ---
  let valColor = "yellow", valDetail = "資料不足";
  {
    const dcf = state.dcfData;
    const extra = state.valuationExtraData;
    let posCount = 0, totalChecks = 0;
    if (dcf && dcf.margin_of_safety_pct !== null && dcf.margin_of_safety_pct !== undefined) {
      totalChecks++;
      if (dcf.margin_of_safety_pct > 0) posCount++;
    }
    if (extra && extra.graham_mos_pct !== null && extra.graham_mos_pct !== undefined) {
      totalChecks++;
      if (extra.graham_mos_pct > 0) posCount++;
    }
    if (extra && extra.peg !== null && extra.peg !== undefined) {
      totalChecks++;
      if (extra.peg < 1.5) posCount++;
    }
    if (totalChecks === 0) { valColor = "yellow"; valDetail = "資料不足"; }
    else if (posCount === totalChecks) { valColor = "green"; valDetail = "估值具安全邊際"; }
    else if (posCount >= totalChecks * 0.5) { valColor = "yellow"; valDetail = "估值中性"; }
    else { valColor = "red"; valDetail = "估值偏貴"; }
  }
  setSignal("sig-valuation", valColor, valDetail);

  card.style.display = "block";
}

function rerenderDashboard() {
  if (!dashboardState.stockId) return;
  const sid = dashboardState.stockId;
  const years = Number($("years").value);

  plotRevenue(dashboardState.revenueRows, sid);
  plotRevenueYoY(dashboardState.revenueRows, sid);
  plotPriceVsRevenue(dashboardState.revenueRows, dashboardState.priceRows, sid);
  plotTrendOverview(dashboardState.revenueRows, dashboardState.priceRows, dashboardState.dividendYieldRows, sid);
  plotVolumeTurnoverRecent(dashboardState.volumeRows, sid);
  plotRoeRoa(dashboardState.roeRoaRows, sid);
  plotDebtRatio(dashboardState.debtRatioRows, sid);
  if (dashboardState.fcfData) plotFreeCashFlow(dashboardState.fcfData, sid, years);
  if (dashboardState.fcfQuarterlyData) plotFreeCashFlowQuarterly(dashboardState.fcfQuarterlyData, sid, years);
  if (dashboardState.turnoverDaysRows && dashboardState.turnoverDaysRows.length > 0) plotTurnoverDays(dashboardState.turnoverDaysRows, sid);
  if (dashboardState.peRiverData) plotPERiver(dashboardState.peRiverData, sid);
  renderTrendSummary(dashboardState.revenueRows, dashboardState.priceRows, dashboardState.dividendYieldRows);
  renderTrendNarrative(dashboardState.revenueRows, dashboardState.priceRows, dashboardState.dividendYieldRows);
  // New
  plotMargins(dashboardState.marginsRows, sid);
  plotEpsTrend(dashboardState.epsTrendRows, sid);
  plotLiquidity(dashboardState.liquidityRows, sid);
  plotBvps(dashboardState.liquidityRows, sid);
  if (dashboardState.foreignHoldingData) plotForeignHolding(dashboardState.foreignHoldingData, sid);
  if (dashboardState.valuationExtraData) renderValuationExtra(dashboardState.valuationExtraData);
  renderFinancialFeed();
}

// ── DCF recalculate button ────────────────────────────────────────────────────

function hookDcfRecalc() {
  const btn = $("dcfRecalc");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    if (!dashboardState.stockId) return;

    const gInput = parseFloat($("dcfG").value);
    const rInput = parseFloat($("dcfR").value);
    const mosInput = parseFloat($("dcfMos").value);
    if (isNaN(gInput) || isNaN(rInput) || isNaN(mosInput)) return;
    if (rInput <= gInput) {
      alert("折現率必須大於成長率");
      return;
    }

    const g = gInput / 100;
    const r = rInput / 100;
    const mos = mosInput / 100;
    dcfState.g = g;
    dcfState.r = r;
    dcfState.mos = mos;

    const years = Number($("years").value);
    const token = requireTokenBeforeQuery();
    if (!token) return;
    const sid = dashboardState.stockId;

    btn.disabled = true;
    const container = $("dcfResult");
    if (container) {
      container.classList.add("skeleton-section");
      container.textContent = "";
    }
    try {
      const data = await fetchJson(
        `/api/stocks/${encodeURIComponent(sid)}/dcf?years=${years}&growth_rate=${g}&discount_rate=${r}&margin_of_safety=${mos}`,
        token
      );
      dashboardState.dcfData = data;
      renderDcf(data);
    } catch (err) {
      if (container) {
        container.classList.remove("skeleton-section");
        container.innerHTML = `<p class="dcf-na">試算失敗：${err.message}</p>`;
      }
    } finally {
      btn.disabled = false;
    }
  });
}

// ── Latest price + institutional snapshot (shared by query & cache-restore) ──
function renderLatestSnapshot(latest, stockId) {
  clearSkeleton("latestPrice");
  clearSkeleton("institutional");
  const stockName = (latest && latest.stock_name) || "";
  const titleEl = $("latestPriceTitle");
  if (titleEl) titleEl.textContent = `${stockId}${stockName ? " " + stockName : ""} 最新股價`;

  const p = (latest && latest.price) || {};
  renderMetrics($("latestPrice"), [
    { key: "日期", value: p.date || "-" },
    { key: "開盤", value: formatFloat2(p.open) },
    { key: "最高", value: formatFloat2(p.high) },
    { key: "最低", value: formatFloat2(p.low) },
    { key: "收盤", value: formatFloat2(p.close) },
    { key: "漲跌", value: formatFloat2(p.spread) },
    { key: "成交量", value: formatNumber(p.volume) },
    { key: "成交金額", value: formatNumber(p.money) },
    { key: "成交筆數", value: formatNumber(p.turnover) },
  ]);

  const inst = (latest && latest.institutional) || [];
  const instStatus = (latest && latest.institutional_status) || (inst.length ? "fresh" : "unavailable");
  const instAsOf = (latest && latest.institutional_as_of) || null;
  const instLag = Number((latest && latest.institutional_lag_days) || 0);
  const noteEl = $("institutionalNote");
  if (noteEl) {
    let noteText = "";
    let noteClass = "chart-note";
    if (instStatus === "fresh" && instAsOf) {
      noteText = `資料截至 ${instAsOf}`;
    } else if (instStatus === "stale" && instAsOf) {
      noteText = `資料截至 ${instAsOf}（延遲 ${instLag} 個交易日，T86 尚未更新最新交易日）`;
      noteClass = "chart-note chart-note-warn";
    } else if (instStatus === "unavailable") {
      noteText = "法人資料尚未公布（TWSE T86 通常於盤後 15:30 後釋出，FinMind 同步可能再延遲數分鐘）。";
      noteClass = "chart-note chart-note-warn";
    }
    if (noteText) {
      noteEl.textContent = noteText;
      noteEl.className = noteClass;
      noteEl.style.display = "";
    } else {
      noteEl.style.display = "none";
    }
  }
  if (inst.length === 0) {
    $("institutional").textContent =
      instStatus === "unavailable"
        ? "法人買賣資料尚未公布，請於盤後 16:30 後重新查詢。"
        : "查無法人買賣資訊。";
  } else {
    renderTable(
      $("institutional"),
      ["類別", "買進", "賣出", "買賣超"],
      inst.map((row) => [row.category, formatNumber(row.buy), formatNumber(row.sell), formatNumber(row.net)])
    );
  }
}

// ── Main query ───────────────────────────────────────────────────────────────

async function runQuery() {
  const stockId = $("stockId").value.trim();
  const years = Number($("years").value);
  const token = requireTokenBeforeQuery();

  if (!stockId) {
    setStatus("請輸入股票代號");
    return;
  }
  if (!token) return;

  setLoading(true);
  setStatus("查詢中...");
  progressStart();

  // Show skeleton loaders for slow sections
  showSkeleton("buyScoreSkeleton");
  showSkeleton("debtRatioChart");
  showSkeleton("fcfChart");
  showSkeleton("fcfQuarterlyChart");
  showSkeleton("dcfResult");
  showSkeleton("shareholdingTable");
  showSkeleton("shareholdingSpreadPanel");
  showSkeleton("turnoverDaysChart");
  showSkeleton("peRiverChart");
  showSkeleton("marginsChart");
  showSkeleton("epsTrendChart");
  showSkeleton("liquidityChart");
  showSkeleton("bvpsChart");
  showSkeleton("foreignHoldingChart");

  updateTokenUsage(token);

  const enc = encodeURIComponent;
  const base = `/api/stocks/${enc(stockId)}`;
  const yr = enc(years);
  const g = dcfState.g, r = dcfState.r, mos = dcfState.mos;

  // Fire all requests simultaneously
  const pLatest = fetchJson(`${base}/latest`, token);
  const pRevenue = fetchJson(`${base}/revenue?years=${yr}`, token);
  const pPrice = fetchJson(`${base}/price_history?years=${yr}`, token);
  const pDYield = fetchJson(`${base}/dividend_yield?years=${yr}`, token);
  const pDivCash = fetchJson(`${base}/dividends_cash?years=${yr}`, token);
  const pVolume = fetchJson(`${base}/volume_turnover_recent`, token);
  const pRoeRoa = fetchJson(`${base}/roe_roa?years=${yr}`, token);
  const pDcf = fetchJson(`${base}/dcf?years=${yr}&growth_rate=${g}&discount_rate=${r}&margin_of_safety=${mos}`, token);
  const pDebt = fetchJson(`${base}/debt_ratio?years=${yr}`, token);
  const pFcf = fetchJson(`${base}/free_cash_flow?years=${yr}`, token);
  const pFcfQuarterly = fetchJson(`${base}/free_cash_flow_quarterly?years=${yr}`, token);
  const pCapitalFormation = fetchJson(`${base}/capital_formation`, token);
  const pShareholding = fetchJson(`${base}/shareholding`, token);
  const pShareholdingSpread = fetchJson(`${base}/shareholding_spread?years=${yr}`, token);
  const pTurnoverDays = fetchJson(`${base}/turnover_days?years=${yr}`, token);
  const pPeRiver = fetchJson(`${base}/pe_river?years=${yr}`, token);
  // New financial indicator endpoints
  const pMargins = fetchJson(`${base}/margins?years=${yr}`, token);
  const pEpsTrend = fetchJson(`${base}/eps_trend?years=${yr}`, token);
  const pLiquidity = fetchJson(`${base}/liquidity?years=${yr}`, token);
  const pForeignHolding = fetchJson(`${base}/foreign_holding?years=${yr}`, token);
  const pValuationExtra = fetchJson(`${base}/valuation_extra?years=${yr}`, token);
  const pBuyScore = fetchJson(`${base}/buy_score`, token);

  const allPromises = [
    pLatest, pRevenue, pPrice, pDYield, pDivCash, pVolume, pRoeRoa, pDcf,
    pDebt, pFcf, pFcfQuarterly, pCapitalFormation, pShareholding, pShareholdingSpread,
    pTurnoverDays, pPeRiver, pMargins, pEpsTrend, pLiquidity, pForeignHolding,
    pValuationExtra, pBuyScore,
  ];
  let doneCount = 0;
  let hasError = false;

  function onDone(isErr) {
    if (isErr) hasError = true;
    doneCount++;
    const pct = 30 + Math.floor((doneCount / allPromises.length) * 65);
    progressAdvance(pct);
    if (doneCount >= allPromises.length) {
      setLoading(false);
      setStatus(`完成：${stockId}（${years} 年）`);
      progressDone(hasError);
      renderSignalCard(dashboardState);
      // Persist this successful query so the next open restores it instantly.
      saveQueryCache();
    } else {
      setStatus(`查詢中… ${doneCount}/${allPromises.length}`);
    }
  }

  // ── Group 1: fast core data ────────────────────────────────────────────────
  // Render latest price & institutional immediately when ready
  pLatest.then((latest) => {
    dashboardState.latestSnapshot = latest;
    renderLatestSnapshot(latest, stockId);
    renderFinancialFeed();
    onDone();
  }).catch((err) => {
    setStatus(`股價查詢失敗：${err.message}`);
    onDone();
  });

  // Render revenue chart as soon as revenue data arrives
  pRevenue.then((revenue) => {
    const rows = revenue.rows || [];
    dashboardState.stockId = stockId;
    dashboardState.revenueRows = rows;
    plotRevenue(rows, stockId);
    plotRevenueYoY(rows, stockId);
    renderTable(
      $("revenueTable"),
      ["月份", "營收", "MA3", "MA6", "MA12"],
      rows.map((r) => [
        r.month || "-",
        r.revenue === null ? "-" : formatNumber(r.revenue),
        r.ma_3 === null ? "-" : formatNumber(r.ma_3),
        r.ma_6 === null ? "-" : formatNumber(r.ma_6),
        r.ma_12 === null ? "-" : formatNumber(r.ma_12),
      ])
    );
    // Update combined charts if partner data is ready
    if (dashboardState.priceRows.length > 0) {
      plotPriceVsRevenue(rows, dashboardState.priceRows, stockId);
    }
    if (dashboardState.priceRows.length > 0 && dashboardState.dividendYieldRows.length > 0) {
      renderTrendSummary(rows, dashboardState.priceRows, dashboardState.dividendYieldRows);
      renderTrendNarrative(rows, dashboardState.priceRows, dashboardState.dividendYieldRows);
      plotTrendOverview(rows, dashboardState.priceRows, dashboardState.dividendYieldRows, stockId);
    }
    renderFinancialFeed();
    onDone();
  }).catch((err) => {
    console.error("revenue:", err);
    onDone();
  });

  // Price history — triggers combined charts when revenue also ready
  pPrice.then((priceHistory) => {
    const rows = (priceHistory && priceHistory.rows) || [];
    dashboardState.priceRows = rows;
    if (dashboardState.revenueRows.length > 0) {
      plotPriceVsRevenue(dashboardState.revenueRows, rows, stockId);
      if (dashboardState.dividendYieldRows.length > 0) {
        renderTrendSummary(dashboardState.revenueRows, rows, dashboardState.dividendYieldRows);
        renderTrendNarrative(dashboardState.revenueRows, rows, dashboardState.dividendYieldRows);
        plotTrendOverview(dashboardState.revenueRows, rows, dashboardState.dividendYieldRows, stockId);
      }
    }
    // Dividend table depends on cash + price
    if (dashboardState.cashRows && dashboardState.cashRows.length > 0) {
      renderDividendTable(dashboardState.cashRows, rows);
    }
    renderFinancialFeed();
    onDone();
  }).catch((err) => {
    console.error("price:", err);
    onDone();
  });

  // Dividend yield — triggers trend overview when all three ready
  pDYield.then((dividendYield) => {
    const rows = (dividendYield && dividendYield.rows) || [];
    dashboardState.dividendYieldRows = rows;
    if (dashboardState.revenueRows.length > 0 && dashboardState.priceRows.length > 0) {
      renderTrendSummary(dashboardState.revenueRows, dashboardState.priceRows, rows);
      renderTrendNarrative(dashboardState.revenueRows, dashboardState.priceRows, rows);
      plotTrendOverview(dashboardState.revenueRows, dashboardState.priceRows, rows, stockId);
    }
    onDone();
  }).catch((err) => {
    console.error("dyield:", err);
    onDone();
  });

  // Cash dividends — renders dividend table when price also ready
  pDivCash.then((dividendsCash) => {
    const rows = (dividendsCash && dividendsCash.rows) || [];
    dashboardState.cashRows = rows;
    if (dashboardState.priceRows.length > 0) {
      renderDividendTable(rows, dashboardState.priceRows);
    }
    onDone();
  }).catch((err) => {
    console.error("divcash:", err);
    onDone();
  });

  // Volume — independent
  pVolume.then((data) => {
    dashboardState.volumeRows = (data && data.rows) || [];
    plotVolumeTurnoverRecent(dashboardState.volumeRows, stockId);
    renderFinancialFeed();
    onDone();
  }).catch((err) => {
    console.error("volume:", err);
    onDone();
  });

  // ROE/ROA — slow, independent
  pRoeRoa.then((data) => {
    dashboardState.roeRoaRows = (data && data.rows) || [];
    plotRoeRoa(dashboardState.roeRoaRows, stockId);
    onDone();
  }).catch((err) => {
    console.error("roe:", err);
    const el = $("roeRoaChart");
    if (el) el.textContent = "ROE/ROA 載入失敗：" + err.message;
    onDone();
  });

  // DCF — slow, independent
  pDcf.then((data) => {
    dashboardState.dcfData = data;
    renderDcf(data);
    onDone();
  }).catch((err) => {
    console.error("dcf:", err);
    const c = $("dcfResult");
    if (c) {
      c.classList.remove("skeleton-section");
      c.innerHTML = `<p class="dcf-na">查無資料（${err.message}）</p>`;
    }
    onDone();
  });

  // Debt ratio — slow, independent
  pDebt.then((data) => {
    dashboardState.debtRatioRows = (data && data.rows) || [];
    plotDebtRatio(dashboardState.debtRatioRows, stockId);
    onDone();
  }).catch((err) => {
    console.error("debt:", err);
    const el = $("debtRatioChart");
    if (el) {
      el.classList.remove("skeleton-section");
      el.textContent = "負債比資料載入失敗: " + err.message;
    }
    onDone();
  });

  // FCF — slow, independent
  pFcf.then((data) => {
    dashboardState.fcfData = data;
    plotFreeCashFlow(data, stockId, years);
    onDone();
  }).catch((err) => {
    console.error("fcf:", err);
    const el = $("fcfChart");
    if (el) {
      el.classList.remove("skeleton-section");
      el.textContent = "現金流量資料載入失敗: " + err.message;
    }
    onDone();
  });

  // FCF quarterly cumulative — slow, independent
  pFcfQuarterly.then((data) => {
    dashboardState.fcfQuarterlyData = data;
    plotFreeCashFlowQuarterly(data, stockId, years);
    onDone();
  }).catch((err) => {
    console.error("fcf_quarterly:", err);
    const el = $("fcfQuarterlyChart");
    if (el) {
      el.classList.remove("skeleton-section");
      el.textContent = "季累積現金流量資料載入失敗: " + err.message;
    }
    onDone();
  });

  // Capital formation — independent
  pCapitalFormation.then((data) => {
    dashboardState.capitalFormationData = data;
    renderCapitalFormation(data);
    onDone();
  }).catch((err) => {
    console.error("capital_formation:", err);
    clearSkeleton("capitalFormationPanel");
    const el = $("capitalFormationPanel");
    if (el) el.textContent = "股本形成資料暫不可用：" + err.message;
    onDone();
  });

  // Shareholding — slow, independent
  pShareholding.then((data) => {
    dashboardState.shareholdingData = data;
    renderShareholding(data);
    onDone();
  }).catch((err) => {
    console.error("shareholding:", err);
    const el = $("shareholdingTable");
    if (el) {
      el.classList.remove("skeleton-section");
      el.textContent = "董監事持股資料暫不可用";
    }
    onDone();
  });

  // Shareholder structure distribution (集保股權分散) — slow, independent
  pShareholdingSpread.then((data) => {
    dashboardState.shareholdingSpreadData = data;
    renderShareholdingSpread(data);
    onDone();
  }).catch((err) => {
    console.error("shareholding_spread:", err);
    clearSkeleton("shareholdingSpreadPanel");
    const el = $("shareholdingSpreadPanel");
    if (el) el.textContent = "股東持股結構分佈資料暫不可用：" + err.message;
    onDone();
  });

  // Turnover Days — slow, independent
  pTurnoverDays.then((data) => {
    const rows = (data && data.rows) || [];
    dashboardState.turnoverDaysRows = rows;
    plotTurnoverDays(rows, stockId);
    onDone();
  }).catch((err) => {
    console.error("turnover_days:", err);
    clearSkeleton("turnoverDaysChart");
    const el = $("turnoverDaysChart");
    if (el) el.textContent = "營運週轉天數資料暫不可用";
    onDone();
  });

  // PE River — slow, independent
  pPeRiver.then((data) => {
    dashboardState.peRiverData = data;
    plotPERiver(data, stockId);
    onDone();
  }).catch((err) => {
    console.error("pe_river:", err);
    clearSkeleton("peRiverChart");
    const el = $("peRiverChart");
    if (el) el.textContent = "本益比河流圖資料暫不可用";
    onDone(true);
  });

  // Margin ratios — slow, independent
  pMargins.then((data) => {
    dashboardState.marginsRows = (data && data.rows) || [];
    plotMargins(dashboardState.marginsRows, stockId);
    onDone();
  }).catch((err) => {
    console.error("margins:", err);
    clearSkeleton("marginsChart");
    const el = $("marginsChart");
    if (el) el.textContent = "利潤率資料暫不可用：" + err.message;
    onDone(true);
  });

  // EPS trend — slow, independent
  pEpsTrend.then((data) => {
    dashboardState.epsTrendRows = (data && data.rows) || [];
    plotEpsTrend(dashboardState.epsTrendRows, stockId);
    onDone();
  }).catch((err) => {
    console.error("eps_trend:", err);
    clearSkeleton("epsTrendChart");
    const el = $("epsTrendChart");
    if (el) el.textContent = "EPS 趨勢資料暫不可用：" + err.message;
    onDone(true);
  });

  // Liquidity ratios + BVPS — slow, independent
  pLiquidity.then((data) => {
    dashboardState.liquidityRows = (data && data.rows) || [];
    plotLiquidity(dashboardState.liquidityRows, stockId);
    plotBvps(dashboardState.liquidityRows, stockId);
    onDone();
  }).catch((err) => {
    console.error("liquidity:", err);
    clearSkeleton("liquidityChart");
    clearSkeleton("bvpsChart");
    const el = $("liquidityChart");
    if (el) el.textContent = "流動性資料暫不可用：" + err.message;
    onDone(true);
  });

  // Foreign holding — graceful fallback on free tier
  pForeignHolding.then((data) => {
    dashboardState.foreignHoldingData = data;
    plotForeignHolding(data, stockId);
    onDone();
  }).catch((err) => {
    console.error("foreign_holding:", err);
    clearSkeleton("foreignHoldingChart");
    const el = $("foreignHoldingChart");
    if (el) el.textContent = "外資持股比例資料暫不可用";
    onDone(true);
  });

  // Valuation extra (PEG + Graham) — slow, independent
  pValuationExtra.then((data) => {
    dashboardState.valuationExtraData = data;
    renderValuationExtra(data);
    onDone();
  }).catch((err) => {
    console.error("valuation_extra:", err);
    const el = $("valuationExtraPanel");
    if (el) el.textContent = "進階估值資料暫不可用：" + err.message;
    onDone(true);
  });

  pBuyScore.then((data) => {
    dashboardState.buyScoreData = data;
    renderBuyScore(data);
    onDone();
  }).catch((err) => {
    console.error("buy_score:", err);
    clearSkeleton("buyScoreSkeleton");
    const el = $("buyScoreSkeleton");
    if (el) el.textContent = "買入評分資料暫不可用：" + err.message;
    onDone(true);
  });
}

$("queryForm").addEventListener("submit", (e) => {
  e.preventDefault();
  runQuery();
});

window.addEventListener("resize", () => {
  window.clearTimeout(resizeTimer);
  resizeTimer = window.setTimeout(() => {
    rerenderDashboard();
  }, 180);
});

// Hook DCF recalculate button
hookDcfRecalc();
bindTokenSettings();
bindPaidApiToggle();
initTabs();
initTheme();
initQuickActions();
initOneHandControls();

// On open: restore the last successful query from localStorage instead of
// auto-querying a hard-coded default stock. If no cache exists, show a welcome
// empty state and wait for the user to query.
(function bootInitialData() {
  const restored = loadQueryCache();
  if (!restored) {
    // No cached query: stop the initial skeleton shimmers so the page reads as
    // an idle welcome state rather than a perpetual loading screen.
    document.querySelectorAll(".skeleton-section").forEach((el) => {
      el.classList.remove("skeleton-section");
    });
    const hint = $("shareholdingSpreadPanel");
    if (hint && !hint.textContent.trim()) {
      hint.innerHTML = '<p class="chart-note" style="margin:0;">輸入股票代號並按「查詢」即可載入分析資料。系統會記住您最後一次查詢，下次開啟時自動顯示。</p>';
    }
    setStatus("輸入股票代號後按「查詢」開始分析");
  }
})();

// Flush the latest query to localStorage before the tab is hidden/closed so the
// most recent result is always available on next open. (JS cannot run after the
// page is fully closed, so we persist at the last reliable lifecycle hook.)
window.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") saveQueryCache(true);
});
window.addEventListener("pagehide", () => saveQueryCache(true));
