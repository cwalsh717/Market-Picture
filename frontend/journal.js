/* Bradán — Journal (narrative archive) */

const API_BASE = "";

const REGIME_STYLES = {
  risk_on:  { bg: "bg-emerald-600", label: "RISK-ON" },
  risk_off: { bg: "bg-red-600",     label: "RISK-OFF" },
  mixed:    { bg: "bg-amber-600",   label: "MIXED" },
};

const SIGNAL_LABELS = {
  spx_trend: "S&P Trend",
  vix: "Volatility",
  hy_spread: "Credit Spreads",
  dxy: "US Dollar",
  gold_vs_equities: "Gold vs Equities",
};

const DIRECTION_COLORS = {
  risk_on:  "text-emerald-400",
  risk_off: "text-red-400",
  neutral:  "text-gray-400",
};

// ── Data fetching ───────────────────────────────────────────────────────

async function fetchRecent(days) {
  const resp = await fetch(`${API_BASE}/api/narratives/recent?days=${days}`);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

async function fetchByDate(date) {
  const resp = await fetch(`${API_BASE}/api/narratives?date=${encodeURIComponent(date)}`);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

// ── Rendering ───────────────────────────────────────────────────────────

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function formatTimestamp(ts) {
  try {
    const d = new Date(ts);
    return d.toLocaleDateString("en-US", {
      weekday: "short",
      month: "short",
      day: "numeric",
      year: "numeric",
    }) + " at " + d.toLocaleTimeString("en-US", {
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return ts;
  }
}

function renderNarrativeType(type) {
  if (type === "pre_market") {
    return '<span class="journal-type-badge badge-pre-market">Pre-Market</span>';
  }
  return '<span class="journal-type-badge badge-after-close">After Close</span>';
}

function renderRegimeBadge(label) {
  const style = REGIME_STYLES[label] || REGIME_STYLES.mixed;
  return `<span class="regime-badge-sm ${style.bg} px-2 py-0.5 rounded-full text-white">${style.label}</span>`;
}

function renderSignals(signalInputs) {
  if (!signalInputs || !Array.isArray(signalInputs) || !signalInputs.length) return "";

  const pills = signalInputs.map((s) => {
    const label = SIGNAL_LABELS[s.name] || s.name;
    const color = DIRECTION_COLORS[s.direction] || DIRECTION_COLORS.neutral;
    return `<span class="${color} text-xs">${escapeHtml(label)}</span>`;
  }).join('<span class="text-gray-700">&middot;</span>');

  return `<div class="journal-signals">${pills}</div>`;
}

function renderNarrativeText(text) {
  if (!text) return "";
  return text
    .split("\n\n")
    .filter((p) => p.trim())
    .map((p) => `<p>${escapeHtml(p.trim())}</p>`)
    .join("");
}

function renderEntries(narratives) {
  const container = document.getElementById("journal-entries");
  const emptyEl = document.getElementById("journal-empty");

  if (!narratives || !narratives.length) {
    container.innerHTML = "";
    emptyEl.classList.remove("hidden");
    return;
  }

  emptyEl.classList.add("hidden");

  container.innerHTML = narratives.map((n) => `
    <article class="journal-entry">
      <div class="journal-meta">
        <span class="journal-date">${formatTimestamp(n.timestamp)}</span>
        ${renderNarrativeType(n.narrative_type)}
        ${renderRegimeBadge(n.regime_label)}
      </div>
      <div class="journal-narrative">
        ${renderNarrativeText(n.narrative_text)}
      </div>
      ${renderSignals(n.signal_inputs)}
    </article>
  `).join("");
}

// ── UI state ────────────────────────────────────────────────────────────

function showLoading(on) {
  document.getElementById("journal-loading").style.display = on ? "block" : "none";
  if (on) {
    document.getElementById("journal-entries").innerHTML = "";
    document.getElementById("journal-empty").classList.add("hidden");
    document.getElementById("journal-error").classList.add("hidden");
  }
}

function showError(message) {
  showLoading(false);
  document.getElementById("journal-error").classList.remove("hidden");
  document.getElementById("journal-error-message").textContent = message;
}

function setStatus(text) {
  document.getElementById("journal-status").textContent = text;
}

// ── Actions ─────────────────────────────────────────────────────────────

async function loadRecent() {
  showLoading(true);
  setStatus("");
  try {
    const data = await fetchRecent(30);
    showLoading(false);
    renderEntries(data.narratives);
    setStatus(`Showing last 30 days`);
  } catch (err) {
    console.error("Failed to load narratives:", err);
    showError("Failed to load narratives. Please try again later.");
  }
}

async function loadDate(dateStr) {
  showLoading(true);
  setStatus("");
  try {
    const data = await fetchByDate(dateStr);
    showLoading(false);
    renderEntries(data.narratives);
    setStatus(`Showing ${dateStr}`);
  } catch (err) {
    console.error("Failed to load narratives for date:", err);
    showError("Failed to load narratives for this date.");
  }
}

// ── Init ────────────────────────────────────────────────────────────────

function init() {
  const datePicker = document.getElementById("date-picker");
  const goBtn = document.getElementById("date-go");
  const resetBtn = document.getElementById("date-reset");

  // Set default date to today
  const today = new Date().toISOString().split("T")[0];
  datePicker.value = today;

  goBtn.addEventListener("click", () => {
    const val = datePicker.value;
    if (val) loadDate(val);
  });

  datePicker.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      const val = datePicker.value;
      if (val) loadDate(val);
    }
  });

  resetBtn.addEventListener("click", () => {
    datePicker.value = today;
    loadRecent();
  });

  // Load recent narratives on page load
  loadRecent();
}

document.addEventListener("DOMContentLoaded", init);
