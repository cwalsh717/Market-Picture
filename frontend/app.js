/* Bradán — frontend entry point */

const API_BASE = ""; // empty = same origin; set to "http://localhost:8000" for dev

// ---------------------------------------------------------------------------
// Signal name → display label
// ---------------------------------------------------------------------------
const SIGNAL_LABELS = {
  spx_trend: "S&P Trend",
  vix: "Volatility",
  hy_spread: "Credit Spreads",
  dxy: "US Dollar",
  gold_vs_equities: "Gold vs Equities",
};

// ---------------------------------------------------------------------------
// Regime badge colors
// ---------------------------------------------------------------------------
const REGIME_COLORS = {
  "RISK-ON":  { bg: "bg-emerald-600", border: "border-emerald-500", text: "text-emerald-400" },
  "RISK-OFF": { bg: "bg-red-600",     border: "border-red-500",     text: "text-red-400"     },
  "MIXED":    { bg: "bg-amber-600",   border: "border-amber-500",   text: "text-amber-400"   },
};

// Signal direction → pill color
const DIRECTION_COLORS = {
  risk_on:  { bg: "bg-emerald-900/60", border: "border-emerald-700", dot: "bg-emerald-400" },
  risk_off: { bg: "bg-red-900/60",     border: "border-red-700",     dot: "bg-red-400"     },
  neutral:  { bg: "bg-gray-800/60",    border: "border-gray-600",    dot: "bg-gray-400"    },
};

// ---------------------------------------------------------------------------
// Asset section config
// ---------------------------------------------------------------------------
const SECTION_ORDER = [
  { key: "equities",          label: "Equities" },
  { key: "international",     label: "International" },
  { key: "rates",             label: "Rates" },
  { key: "credit",            label: "Credit" },
  { key: "currencies",        label: "Currencies" },
  { key: "commodities",       label: "Commodities" },
  { key: "critical_minerals", label: "Critical Minerals" },
  { key: "crypto",            label: "Crypto" },
];

const CREDIT_SYMBOLS = new Set(["BAMLC0A0CM", "BAMLH0A0HYM2"]);

const YIELD_SPREAD_SYMBOLS = new Set([
  "DGS2", "DGS10", "SPREAD_2S10S", "BAMLC0A0CM", "BAMLH0A0HYM2",
]);

const ASSET_EXPLAINERS = {
  SPY: "The S&P 500 ETF tracks the 500 largest US companies — the benchmark for the American stock market.",
  QQQ: "The Nasdaq 100 ETF is heavily weighted toward tech giants like Apple, Microsoft, and Nvidia.",
  IWM: "The Russell 2000 tracks small US companies — often a gauge of domestic economic health.",
  VIXY: "Tracks short-term VIX futures — rises when markets expect turbulence ahead.",
  EWJ: "An ETF tracking Japanese stocks — reflects Asia-Pacific economic trends.",
  UKX: "The FTSE 100 tracks the 100 largest UK-listed companies.",
  FEZ: "An ETF tracking the 50 largest eurozone stocks across France, Germany, and others.",
  EWH: "An ETF tracking Hong Kong stocks — a barometer for China-exposed markets.",
  UUP: "Tracks the US dollar against a basket of major currencies. A strong dollar can pressure commodities and emerging markets.",
  USO: "The US Oil Fund ETF tracks crude oil futures — a proxy for oil prices.",
  UNG: "The US Natural Gas Fund ETF — sensitive to weather, storage, and energy demand.",
  GLD: "The SPDR Gold ETF — the classic safe-haven asset. Tends to rise when investors fear uncertainty.",
  CPER: "A copper ETF — copper is sometimes called 'Dr. Copper' because its demand reflects global industrial health.",
  URA: "A uranium ETF — tracks companies involved in uranium mining and nuclear energy.",
  LIT: "A lithium ETF — tracks companies in lithium mining and battery production.",
  REMX: "A rare earths ETF — covers companies mining minerals critical for electronics and defense.",
  "BTC/USD": "Bitcoin — the largest cryptocurrency by market cap, traded 24/7 worldwide.",
  "ETH/USD": "Ethereum — the second-largest cryptocurrency, powering smart contracts and DeFi.",
  DGS2: "The 2-year US Treasury yield — reflects expectations for near-term interest rates.",
  DGS10: "The 10-year US Treasury yield — the benchmark for mortgages and long-term borrowing costs.",
  SPREAD_2S10S: "The gap between 10-year and 2-year Treasury yields. When negative (inverted), it has historically preceded recessions.",
  BAMLC0A0CM: "The extra yield investors demand to hold investment-grade corporate bonds over Treasuries.",
  BAMLH0A0HYM2: "The extra yield investors demand to hold high-yield (junk) bonds over Treasuries. Widens when credit risk rises.",
};

// State
let currentPeriod = "1D";
const sparklineCache = {};  // { "SYMBOL:1D": bars, ... }
const sparklineCharts = {}; // { "SYMBOL": Chart instance }

// ---------------------------------------------------------------------------
// Data fetching
// ---------------------------------------------------------------------------

async function fetchSummary() {
  const resp = await fetch(`${API_BASE}/api/summary`);
  if (resp.status === 404) return null;
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

async function fetchSnapshot() {
  const resp = await fetch(`${API_BASE}/api/snapshot`);
  if (!resp.ok) throw new Error(`Snapshot error: ${resp.status}`);
  return resp.json();
}

async function fetchHistory(symbol, range) {
  const resp = await fetch(
    `${API_BASE}/api/history/${encodeURIComponent(symbol)}?range=${range}`
  );
  if (!resp.ok) throw new Error(`History error: ${resp.status}`);
  return resp.json();
}

async function fetchIntraday(symbol) {
  const resp = await fetch(
    `${API_BASE}/api/intraday/${encodeURIComponent(symbol)}`
  );
  if (!resp.ok) throw new Error(`Intraday error: ${resp.status}`);
  return resp.json();
}

function splitRatesAndCredit(assets) {
  const result = Object.assign({}, assets);
  if (!result.rates) return result;

  const rates = [];
  const credit = [];

  for (const asset of result.rates) {
    if (CREDIT_SYMBOLS.has(asset.symbol)) {
      credit.push(asset);
    } else {
      rates.push(asset);
    }
  }

  result.rates = rates;
  if (credit.length) result.credit = credit;
  return result;
}

// ---------------------------------------------------------------------------
// Renderers
// ---------------------------------------------------------------------------

function renderRegime(regime) {
  const el = document.getElementById("regime-section");
  const colors = REGIME_COLORS[regime.label] || REGIME_COLORS["MIXED"];

  // Badge
  const badge = `<span class="${colors.bg} px-4 py-2 rounded-lg text-lg font-bold tracking-wide inline-block">${regime.label}</span>`;

  // Reason
  const reason = `<p class="text-gray-400 text-sm mt-2">${escapeHtml(regime.reason)}</p>`;

  // Signal pills
  const directionLabels = { risk_on: "Bullish", risk_off: "Bearish", neutral: "Neutral" };
  let pills = "";
  if (regime.signals && regime.signals.length) {
    const items = regime.signals.map((s) => {
      const dc = DIRECTION_COLORS[s.direction] || DIRECTION_COLORS.neutral;
      const label = SIGNAL_LABELS[s.name] || s.name;
      const dirLabel = directionLabels[s.direction] || "Neutral";
      const titleText = `${dirLabel} — ${s.detail}`;
      return `<span class="${dc.bg} ${dc.border} border px-3 py-1 rounded-full text-xs flex items-center gap-1.5 cursor-default" title="${escapeAttr(titleText)}"><span class="${dc.dot} w-1.5 h-1.5 rounded-full inline-block"></span>${escapeHtml(label)}</span>`;
    });
    const legend = `<div class="flex gap-3 mt-2 text-xs text-gray-500"><span class="flex items-center gap-1"><span class="bg-emerald-400 w-1.5 h-1.5 rounded-full inline-block"></span> Bullish</span><span class="flex items-center gap-1"><span class="bg-red-400 w-1.5 h-1.5 rounded-full inline-block"></span> Bearish</span><span class="flex items-center gap-1"><span class="bg-gray-400 w-1.5 h-1.5 rounded-full inline-block"></span> Neutral</span></div>`;
    pills = `<div class="flex flex-wrap gap-2 mt-3">${items.join("")}</div>${legend}`;
  }

  el.innerHTML = badge + reason + pills;
}

function renderNarrative(summaryText, date, period) {
  const el = document.getElementById("narrative-section");
  const periodLabel = period === "premarket" ? "Pre-market" : "After close";

  const heading = `<h2 class="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-4">${periodLabel} — ${escapeHtml(date)}</h2>`;

  const paragraphs = summaryText
    .split("\n\n")
    .filter((p) => p.trim())
    .map((p) => `<p class="text-gray-300">${escapeHtml(p.trim())}</p>`)
    .join("");

  el.innerHTML = heading + `<div class="narrative">${paragraphs}</div>`;
}

function renderHeaderMeta(date, period) {
  const el = document.getElementById("header-meta");
  const periodLabel = period === "premarket" ? "Pre-market" : "After close";
  el.textContent = `${periodLabel} \u2022 ${date}`;
}

// ---------------------------------------------------------------------------
// Asset section rendering
// ---------------------------------------------------------------------------

function formatPrice(symbol, price) {
  const num = Number(price);
  if (isNaN(num)) return "—";

  if (YIELD_SPREAD_SYMBOLS.has(symbol)) {
    return num.toFixed(2) + "%";
  }
  if (symbol === "BTC/USD") {
    return "$" + Math.round(num).toLocaleString("en-US");
  }
  return "$" + num.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function renderAssetCard(asset) {
  const hasPrice = asset.price != null && !isNaN(asset.price);
  const pct = Number(asset.change_pct) || 0;
  const positive = pct >= 0;
  const arrow = positive ? "\u25B2" : "\u25BC";
  const sign = positive ? "+" : "";
  const colorClass = positive ? "text-emerald-400" : "text-red-400";
  const explainer = ASSET_EXPLAINERS[asset.symbol] || "";
  const chartUrl = `/chart.html?symbol=${encodeURIComponent(asset.symbol)}`;
  const staleStyle = asset.is_stale ? "opacity:0.8;" : "";
  const staleLabel = asset.is_stale ? `<span class="text-gray-500 text-xs ml-1">Prev Close</span>` : "";
  const priceDisplay = hasPrice ? formatPrice(asset.symbol, asset.price) : "\u2014";
  const changeDisplay = hasPrice
    ? `<span class="text-sm font-medium ${colorClass}">${arrow} ${sign}${pct.toFixed(2)}%</span>`
    : `<span class="text-xs text-gray-500">No data</span>`;

  return `
    <a href="${chartUrl}" class="asset-card block" style="text-decoration:none;color:inherit;${staleStyle}">
      <div class="flex items-start justify-between gap-2 mb-1">
        <div class="min-w-0">
          <div class="flex items-center gap-1.5">
            <span class="text-sm font-semibold text-gray-100">${escapeHtml(asset.name)}</span>
            ${explainer ? `<button class="info-btn" data-symbol="${escapeAttr(asset.symbol)}" title="What is this?">&#9432;</button>` : ""}
          </div>
          <span class="text-xs text-gray-500">${escapeHtml(asset.symbol)}</span>
        </div>
        <div class="sparkline-container">
          <canvas data-symbol="${escapeAttr(asset.symbol)}"></canvas>
        </div>
      </div>
      <div class="flex items-baseline justify-between gap-2 mt-2">
        <span class="text-base font-medium text-gray-200">${priceDisplay}${staleLabel}</span>
        ${changeDisplay}
      </div>
      ${explainer ? `<div class="info-tooltip hidden text-xs text-gray-400">${escapeHtml(explainer)}</div>` : ""}
    </a>`;
}

function renderAssetSections(assets) {
  const container = document.getElementById("asset-sections");
  let html = "";

  for (const section of SECTION_ORDER) {
    const items = assets[section.key];
    if (!items || !items.length) continue;

    const colClass = items.length <= 2
      ? "grid-cols-1 sm:grid-cols-2"
      : "grid-cols-1 sm:grid-cols-2 lg:grid-cols-3";

    html += `
      <section class="mb-8">
        <h2 class="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-4">${escapeHtml(section.label)}</h2>
        <div class="grid ${colClass} gap-3">
          ${items.map(renderAssetCard).join("")}
        </div>
      </section>`;
  }

  container.innerHTML = html;
}

// ---------------------------------------------------------------------------
// Sparklines
// ---------------------------------------------------------------------------

function renderSparkline(symbol, bars) {
  const canvas = document.querySelector(`canvas[data-symbol="${CSS.escape(symbol)}"]`);
  if (!canvas || !bars || !bars.length) return;

  // Destroy previous chart instance
  if (sparklineCharts[symbol]) {
    sparklineCharts[symbol].destroy();
  }

  const closes = bars.map((b) => b.close);
  const up = closes[closes.length - 1] >= closes[0];
  const lineColor = up ? "rgb(52, 211, 153)" : "rgb(248, 113, 113)";
  const fillColor = up ? "rgba(52, 211, 153, 0.1)" : "rgba(248, 113, 113, 0.1)";

  sparklineCharts[symbol] = new Chart(canvas, {
    type: "line",
    data: {
      labels: bars.map((b) => b.date),
      datasets: [{
        data: closes,
        borderColor: lineColor,
        backgroundColor: fillColor,
        borderWidth: 1.5,
        pointRadius: 0,
        tension: 0.3,
        fill: true,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: {
        x: { display: false },
        y: { display: false },
      },
      animation: { duration: 300 },
    },
  });
}

async function loadAllSparklines(assets, period) {
  // Collect all symbols from all displayed sections
  const symbols = [];
  for (const section of SECTION_ORDER) {
    const items = assets[section.key];
    if (!items) continue;
    for (const asset of items) {
      symbols.push(asset.symbol);
    }
  }

  // Batch in groups of 6
  const batchSize = 6;
  for (let i = 0; i < symbols.length; i += batchSize) {
    const batch = symbols.slice(i, i + batchSize);
    const promises = batch.map(async (symbol) => {
      const cacheKey = symbol + ":" + period;
      if (sparklineCache[cacheKey]) {
        renderSparkline(symbol, sparklineCache[cacheKey]);
        return;
      }
      try {
        const data = period === "1D"
          ? await fetchIntraday(symbol)
          : await fetchHistory(symbol, period);
        sparklineCache[cacheKey] = data.bars;
        renderSparkline(symbol, data.bars);
      } catch (err) {
        console.warn("Sparkline failed for", symbol, err);
      }
    });
    await Promise.allSettled(promises);
  }
}


// ---------------------------------------------------------------------------
// Interactions
// ---------------------------------------------------------------------------

function initPeriodToggle(assets) {
  const buttons = document.querySelectorAll(".period-btn");
  buttons.forEach((btn) => {
    btn.addEventListener("click", () => {
      buttons.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentPeriod = btn.dataset.period;
      loadAllSparklines(assets, currentPeriod);
    });
  });
}

function initInfoTooltips() {
  const container = document.getElementById("asset-sections");
  if (!container) return;
  container.addEventListener("click", (e) => {
    const btn = e.target.closest(".info-btn");
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    const card = btn.closest(".asset-card");
    if (!card) return;
    const tooltip = card.querySelector(".info-tooltip");
    if (tooltip) tooltip.classList.toggle("hidden");
  });
}


function showError(message) {
  document.getElementById("loading").classList.add("hidden");
  document.getElementById("content").classList.add("hidden");
  const errorEl = document.getElementById("error");
  errorEl.classList.remove("hidden");
  document.getElementById("error-message").textContent = message;
}

function showContent() {
  document.getElementById("loading").classList.add("hidden");
  document.getElementById("error").classList.add("hidden");
  document.getElementById("content").classList.remove("hidden");
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function escapeAttr(str) {
  return str.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

async function init() {
  // Fetch summary + snapshot in parallel
  const [summaryResult, snapshotResult] = await Promise.allSettled([
    fetchSummary(),
    fetchSnapshot(),
  ]);

  const summaryData = summaryResult.status === "fulfilled" ? summaryResult.value : null;
  const snapshotData = snapshotResult.status === "fulfilled" ? snapshotResult.value : null;

  if (!summaryData && !snapshotData) {
    showError("No market data available yet. Check back after the next scheduled update.");
    return;
  }

  // Render narrative sections from summary
  if (summaryData) {
    renderHeaderMeta(summaryData.date, summaryData.period);
    renderRegime(summaryData.regime);
    renderNarrative(summaryData.summary_text, summaryData.date, summaryData.period);
  }

  // Render asset sections from snapshot
  let assets = null;
  if (snapshotData && snapshotData.assets) {
    assets = splitRatesAndCredit(snapshotData.assets);
    renderAssetSections(assets);
    initInfoTooltips();
    initPeriodToggle(assets);
  }

  showContent();

  // Show "Last updated" footer
  const footerEl = document.getElementById("last-updated");
  if (footerEl) {
    const updatedAt = (snapshotData && snapshotData.generated_at) || new Date().toISOString();
    const d = new Date(updatedAt);
    const formatted = d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })
      + " at "
      + d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
    footerEl.textContent = `Last updated: ${formatted}`;
    footerEl.classList.remove("hidden");
  }

  // Load sparklines asynchronously (progressive)
  if (assets) {
    loadAllSparklines(assets, currentPeriod);
  }
}

document.addEventListener("DOMContentLoaded", init);
