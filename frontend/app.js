/* Market Picture — frontend entry point */

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

// Moving-together group label → card accent
const GROUP_STYLES = {
  "Rallying together": { border: "border-emerald-700", accent: "text-emerald-400", icon: "\u25B2" },
  "Selling together":  { border: "border-red-700",     accent: "text-red-400",     icon: "\u25BC" },
  "Diverging":         { border: "border-amber-700",   accent: "text-amber-400",   icon: "\u21C4" },
};

// ---------------------------------------------------------------------------
// Data fetching
// ---------------------------------------------------------------------------

async function fetchSummary() {
  const resp = await fetch(`${API_BASE}/api/summary`);
  if (resp.status === 404) return null;
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
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
  let pills = "";
  if (regime.signals && regime.signals.length) {
    const items = regime.signals.map((s) => {
      const dc = DIRECTION_COLORS[s.direction] || DIRECTION_COLORS.neutral;
      const label = SIGNAL_LABELS[s.name] || s.name;
      return `<span class="${dc.bg} ${dc.border} border px-3 py-1 rounded-full text-xs flex items-center gap-1.5 cursor-default" title="${escapeAttr(s.detail)}"><span class="${dc.dot} w-1.5 h-1.5 rounded-full inline-block"></span>${escapeHtml(label)}</span>`;
    });
    pills = `<div class="flex flex-wrap gap-2 mt-3">${items.join("")}</div>`;
  }

  el.innerHTML = badge + reason + pills;
}

function renderNarrative(summaryText, date, period) {
  const el = document.getElementById("narrative-section");
  const periodLabel = period === "premarket" ? "Pre-market" : "After close";

  const heading = `<h2 class="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-3">${periodLabel} — ${escapeHtml(date)}</h2>`;

  const paragraphs = summaryText
    .split("\n\n")
    .filter((p) => p.trim())
    .map((p) => `<p class="text-gray-300">${escapeHtml(p.trim())}</p>`)
    .join("");

  el.innerHTML = heading + `<div class="narrative">${paragraphs}</div>`;
}

function renderMovingTogether(groups) {
  const el = document.getElementById("moving-together-section");

  if (!groups || !groups.length) {
    el.innerHTML = `<p class="text-gray-500 text-sm">No co-movement groups detected.</p>`;
    return;
  }

  const heading = `<h2 class="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-4">What's Moving Together</h2>`;

  const cards = groups.map((g) => {
    const style = GROUP_STYLES[g.label] || GROUP_STYLES["Diverging"];
    const chips = g.assets
      .map((a) => `<span class="bg-gray-800 text-gray-200 text-xs px-2 py-0.5 rounded">${escapeHtml(a)}</span>`)
      .join("");

    return `
      <div class="border ${style.border} bg-gray-900/50 rounded-lg p-4">
        <div class="flex items-center gap-2 mb-2">
          <span class="${style.accent} text-base">${style.icon}</span>
          <span class="${style.accent} font-semibold text-sm">${escapeHtml(g.label)}</span>
        </div>
        <div class="flex flex-wrap gap-1.5 mb-2">${chips}</div>
        <p class="text-gray-400 text-xs">${escapeHtml(g.detail)}</p>
      </div>`;
  });

  el.innerHTML = heading + `<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">${cards.join("")}</div>`;
}

function renderHeaderMeta(date, period) {
  const el = document.getElementById("header-meta");
  const periodLabel = period === "premarket" ? "Pre-market" : "After close";
  el.textContent = `${periodLabel} \u2022 ${date}`;
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
// Search
// ---------------------------------------------------------------------------

function debounce(fn, ms = 300) {
  let timer;
  return function (...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), ms);
  };
}

async function searchTicker(query) {
  const resp = await fetch(`${API_BASE}/api/search/${encodeURIComponent(query)}`);
  if (resp.status === 404) return null;
  if (!resp.ok) throw new Error(`Search error: ${resp.status}`);
  return resp.json();
}

function renderSearchResult(data) {
  const resultEl = document.getElementById("search-result");
  const errorEl = document.getElementById("search-error");
  errorEl.classList.add("hidden");

  document.getElementById("search-symbol").textContent = data.symbol;
  document.getElementById("search-price").textContent = Number(data.price).toFixed(2);

  const changeEl = document.getElementById("search-change");
  const pct = Number(data.change_pct);
  const abs = Number(data.change_abs);
  const positive = pct >= 0;
  const arrow = positive ? "\u25B2" : "\u25BC";
  const sign = positive ? "+" : "";
  changeEl.textContent = `${arrow} ${sign}${abs.toFixed(2)} (${sign}${pct.toFixed(2)}%)`;
  changeEl.className = `text-sm font-medium ${positive ? "text-emerald-400" : "text-red-400"}`;

  document.getElementById("search-timestamp").textContent = `Last updated: ${data.timestamp}`;
  resultEl.classList.remove("hidden");
}

function clearSearchResult() {
  document.getElementById("search-result").classList.add("hidden");
  document.getElementById("search-error").classList.add("hidden");
}

function setSearchLoading(on) {
  const spinner = document.getElementById("search-spinner");
  if (on) spinner.classList.remove("hidden");
  else spinner.classList.add("hidden");
}

async function handleSearch(query) {
  const trimmed = query.trim().toUpperCase();
  if (!trimmed) {
    clearSearchResult();
    return;
  }
  setSearchLoading(true);
  clearSearchResult();
  try {
    const data = await searchTicker(trimmed);
    if (!data) {
      const errorEl = document.getElementById("search-error");
      errorEl.textContent = `No results for "${trimmed}"`;
      errorEl.classList.remove("hidden");
    } else {
      renderSearchResult(data);
    }
  } catch (err) {
    console.error("Search failed:", err);
    const errorEl = document.getElementById("search-error");
    errorEl.textContent = "Search unavailable. Please try again later.";
    errorEl.classList.remove("hidden");
  } finally {
    setSearchLoading(false);
  }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

async function init() {
  try {
    const data = await fetchSummary();

    if (!data) {
      showError("No market data available yet. Check back after the next scheduled update.");
      return;
    }

    renderHeaderMeta(data.date, data.period);
    renderRegime(data.regime);
    renderNarrative(data.summary_text, data.date, data.period);
    renderMovingTogether(data.moving_together);
    showContent();
  } catch (err) {
    console.error("Failed to load summary:", err);
    showError("Unable to load market data. Please try again later.");
  }

  // Wire search
  const searchInput = document.getElementById("search-input");
  if (searchInput) {
    const debouncedSearch = debounce((e) => handleSearch(e.target.value));
    searchInput.addEventListener("input", debouncedSearch);
    searchInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        handleSearch(searchInput.value);
      }
    });
  }
}

document.addEventListener("DOMContentLoaded", init);
