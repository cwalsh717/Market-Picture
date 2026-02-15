/* Bradán — Symbol Deep-Dive Chart */

const API_BASE = "";

// ── State ──────────────────────────────────────────────────────────────────
let chart = null;
let rsiChart = null;
let candleSeries = null;
let lineSeries = null;
let volumeSeries = null;
let maSeries = { 20: null, 50: null, 200: null };
let rsiLineSeries = null;
let currentBars = [];
let currentSymbol = "";
let currentRange = "1Y";
let chartMode = "candle";
let maVisible = { 20: false, 50: false, 200: false };
let rsiVisible = false;
let isFredSymbol = false;
let syncing = false;

const FRED_SYMBOLS = new Set([
  "DGS2", "DGS10", "SPREAD_2S10S", "BAMLC0A0CM", "BAMLH0A0HYM2",
]);
const RANGES = ["1D", "5D", "1M", "3M", "6M", "1Y", "5Y", "Max"];
const MA_COLORS = { 20: "#fbbf24", 50: "#60a5fa", 200: "#c084fc" };

const CHART_THEME = {
  layout: {
    background: { color: "#030712" },
    textColor: "#9ca3af",
  },
  grid: {
    vertLines: { color: "rgba(55,65,81,0.3)" },
    horzLines: { color: "rgba(55,65,81,0.3)" },
  },
  crosshair: {
    vertLine: { color: "#6b7280", labelBackgroundColor: "#374151" },
    horzLine: { color: "#6b7280", labelBackgroundColor: "#374151" },
  },
  timeScale: { borderColor: "#374151" },
  rightPriceScale: { borderColor: "#374151" },
};

// ── Init ───────────────────────────────────────────────────────────────────
function init() {
  const params = new URLSearchParams(window.location.search);
  currentSymbol = params.get("symbol") || "SPY";

  document.title = `${currentSymbol} — Bradán`;
  document.getElementById("chart-title").textContent = currentSymbol;

  buildControls();
  createCharts();

  // Wire zoom controls
  document.getElementById("zoom-in").addEventListener("click", () => {
    const range = chart.timeScale().getVisibleLogicalRange();
    if (!range) return;
    const span = range.to - range.from;
    const center = (range.from + range.to) / 2;
    const newSpan = span * 0.6;
    chart.timeScale().setVisibleLogicalRange({
      from: center - newSpan / 2,
      to: center + newSpan / 2,
    });
  });

  document.getElementById("zoom-out").addEventListener("click", () => {
    const range = chart.timeScale().getVisibleLogicalRange();
    if (!range) return;
    const span = range.to - range.from;
    const center = (range.from + range.to) / 2;
    const newSpan = span * 1.5;
    chart.timeScale().setVisibleLogicalRange({
      from: center - newSpan / 2,
      to: center + newSpan / 2,
    });
  });

  document.getElementById("zoom-reset").addEventListener("click", () => {
    chart.timeScale().fitContent();
  });

  loadData(currentSymbol, currentRange);
}

// ── Controls ───────────────────────────────────────────────────────────────
function buildControls() {
  const rangeContainer = document.getElementById("range-buttons");
  for (const r of RANGES) {
    const btn = createBtn(r, "range-" + r, r === currentRange);
    btn.dataset.range = r;
    btn.addEventListener("click", () => switchRange(r));
    rangeContainer.appendChild(btn);
  }

  const toggleContainer = document.getElementById("toggle-buttons");

  // Chart type (mutually exclusive)
  const candleBtn = createBtn("Candle", "btn-candle", true);
  candleBtn.addEventListener("click", () => setChartMode("candle"));
  toggleContainer.appendChild(candleBtn);

  const lineBtn = createBtn("Line", "btn-line", false);
  lineBtn.addEventListener("click", () => setChartMode("line"));
  toggleContainer.appendChild(lineBtn);

  // Separator
  const sep = document.createElement("span");
  sep.className = "text-gray-600 text-xs select-none";
  sep.textContent = "|";
  toggleContainer.appendChild(sep);

  // MA toggles
  for (const period of [20, 50, 200]) {
    const btn = document.createElement("button");
    btn.className = "toggle-btn";
    btn.id = "btn-ma-" + period;
    btn.innerHTML = `<span class="ma-dot" style="background:${MA_COLORS[period]}"></span>MA ${period}`;
    btn.addEventListener("click", () => {
      toggleMA(period);
      btn.classList.toggle("active");
    });
    toggleContainer.appendChild(btn);
  }

  // RSI toggle
  const rsiBtn = createBtn("RSI", "btn-rsi", false);
  rsiBtn.addEventListener("click", () => {
    toggleRSI();
    rsiBtn.classList.toggle("active");
  });
  toggleContainer.appendChild(rsiBtn);
}

function createBtn(label, id, active) {
  const btn = document.createElement("button");
  btn.className = "toggle-btn" + (active ? " active" : "");
  btn.textContent = label;
  btn.id = id;
  return btn;
}

// ── Chart creation ─────────────────────────────────────────────────────────
function createCharts() {
  const container = document.getElementById("chart-container");
  chart = LightweightCharts.createChart(container, {
    ...CHART_THEME,
    height: container.clientHeight || 500,
    width: container.clientWidth || 800,
  });
  chart.subscribeCrosshairMove(onCrosshairMove);

  // RSI chart (created now, populated on toggle)
  const rsiContainer = document.getElementById("rsi-container");
  rsiChart = LightweightCharts.createChart(rsiContainer, {
    ...CHART_THEME,
    height: 150,
    width: container.clientWidth || 800,
    rightPriceScale: {
      borderColor: "#374151",
      scaleMargins: { top: 0.1, bottom: 0.1 },
    },
  });

  // Sync time scales between main and RSI charts
  chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
    if (syncing || !range) return;
    syncing = true;
    rsiChart.timeScale().setVisibleLogicalRange(range);
    syncing = false;
  });
  rsiChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
    if (syncing || !range) return;
    syncing = true;
    chart.timeScale().setVisibleLogicalRange(range);
    syncing = false;
  });

  // Responsive resize
  const ro = new ResizeObserver(() => {
    chart.resize(container.clientWidth, container.clientHeight);
    if (rsiVisible) {
      rsiChart.resize(rsiContainer.clientWidth, rsiContainer.clientHeight);
    }
  });
  ro.observe(container);
}

// ── Data loading ───────────────────────────────────────────────────────────
async function loadData(symbol, range) {
  showLoading(true);
  try {
    const resp = await fetch(
      `${API_BASE}/api/history/${encodeURIComponent(symbol)}?range=${range}`
    );
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();

    if (!data.bars || !data.bars.length) {
      showError(`No data available for ${symbol}`);
      return;
    }

    currentBars = data.bars;
    isFredSymbol = detectFredData(currentBars);
    applyFredMode();
    rebuildSeries();
    updatePriceStats();
    updateHeaderPrice();
    resetOHLC();
    showLoading(false);
    chart.timeScale().fitContent();
    if (rsiVisible) rsiChart.timeScale().fitContent();
  } catch (err) {
    console.error("Failed to load chart data:", err);
    showError(`Failed to load data for ${symbol}`);
  }
}

function detectFredData(bars) {
  if (FRED_SYMBOLS.has(currentSymbol)) return true;
  const sample = bars.slice(0, 10);
  return sample.every(
    (b) => b.open === b.close && (b.volume == null || b.volume === 0)
  );
}

function applyFredMode() {
  const candleBtn = document.getElementById("btn-candle");
  const lineBtn = document.getElementById("btn-line");
  if (isFredSymbol) {
    chartMode = "line";
    if (candleBtn) candleBtn.style.display = "none";
    if (lineBtn) lineBtn.classList.add("active");
  } else {
    if (candleBtn) candleBtn.style.display = "";
  }
}

// ── Series rebuild ─────────────────────────────────────────────────────────
function rebuildSeries() {
  removeAllSeries();
  const bars = currentBars;

  if (chartMode === "candle" && !isFredSymbol) {
    candleSeries = chart.addSeries(LightweightCharts.CandlestickSeries, {
      upColor: "#34d399",
      downColor: "#f87171",
      borderUpColor: "#34d399",
      borderDownColor: "#f87171",
      wickUpColor: "#34d399",
      wickDownColor: "#f87171",
    });
    candleSeries.setData(
      bars.map((b) => ({
        time: b.date,
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
      }))
    );
  } else {
    lineSeries = chart.addSeries(LightweightCharts.LineSeries, {
      color: "#60a5fa",
      lineWidth: 2,
      crosshairMarkerRadius: 4,
    });
    lineSeries.setData(bars.map((b) => ({ time: b.date, value: b.close })));
  }

  // Volume (skip for FRED)
  if (!isFredSymbol) {
    const volData = bars
      .filter((b) => b.volume != null && b.volume > 0)
      .map((b) => ({
        time: b.date,
        value: b.volume,
        color:
          b.close >= b.open
            ? "rgba(52,211,153,0.3)"
            : "rgba(248,113,113,0.3)",
      }));
    if (volData.length) {
      volumeSeries = chart.addSeries(LightweightCharts.HistogramSeries, {
        priceFormat: { type: "volume" },
        priceScaleId: "volume",
      });
      chart.priceScale("volume").applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
      });
      volumeSeries.setData(volData);
    }
  }

  // Rebuild active MAs
  for (const period of [20, 50, 200]) {
    if (maVisible[period]) addMASeries(period);
  }

  // Rebuild RSI if visible
  if (rsiVisible) buildRSI();
}

function removeAllSeries() {
  if (candleSeries) { chart.removeSeries(candleSeries); candleSeries = null; }
  if (lineSeries) { chart.removeSeries(lineSeries); lineSeries = null; }
  if (volumeSeries) { chart.removeSeries(volumeSeries); volumeSeries = null; }
  for (const p of [20, 50, 200]) {
    if (maSeries[p]) { chart.removeSeries(maSeries[p]); maSeries[p] = null; }
  }
  if (rsiLineSeries) {
    rsiChart.removeSeries(rsiLineSeries);
    rsiLineSeries = null;
  }
}

// ── Chart mode toggle ──────────────────────────────────────────────────────
function setChartMode(mode) {
  if (mode === chartMode || isFredSymbol) return;
  chartMode = mode;
  document.getElementById("btn-candle").classList.toggle("active", mode === "candle");
  document.getElementById("btn-line").classList.toggle("active", mode === "line");
  rebuildSeries();
  chart.timeScale().fitContent();
}

// ── Moving Averages ────────────────────────────────────────────────────────
function calcSMA(closes, period) {
  const result = [];
  for (let i = period - 1; i < closes.length; i++) {
    let sum = 0;
    for (let j = i - period + 1; j <= i; j++) sum += closes[j];
    result.push(sum / period);
  }
  return result;
}

function addMASeries(period) {
  if (maSeries[period]) return;
  const closes = currentBars.map((b) => b.close);
  if (closes.length < period) return;

  const sma = calcSMA(closes, period);
  const data = sma.map((val, i) => ({
    time: currentBars[i + period - 1].date,
    value: val,
  }));

  maSeries[period] = chart.addSeries(LightweightCharts.LineSeries, {
    color: MA_COLORS[period],
    lineWidth: 1,
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: false,
  });
  maSeries[period].setData(data);
}

function toggleMA(period) {
  maVisible[period] = !maVisible[period];
  if (maVisible[period]) {
    addMASeries(period);
  } else if (maSeries[period]) {
    chart.removeSeries(maSeries[period]);
    maSeries[period] = null;
  }
}

// ── RSI (14) — Wilder smoothing ────────────────────────────────────────────
function calcRSI(closes, period) {
  if (closes.length < period + 1) return [];

  const changes = [];
  for (let i = 1; i < closes.length; i++) {
    changes.push(closes[i] - closes[i - 1]);
  }

  let avgGain = 0;
  let avgLoss = 0;
  for (let i = 0; i < period; i++) {
    if (changes[i] > 0) avgGain += changes[i];
    else avgLoss += Math.abs(changes[i]);
  }
  avgGain /= period;
  avgLoss /= period;

  const rsi = [];
  rsi.push(avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss));

  for (let i = period; i < changes.length; i++) {
    const gain = changes[i] > 0 ? changes[i] : 0;
    const loss = changes[i] < 0 ? Math.abs(changes[i]) : 0;
    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;
    rsi.push(avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss));
  }

  return rsi;
}

function buildRSI() {
  if (rsiLineSeries) {
    rsiChart.removeSeries(rsiLineSeries);
    rsiLineSeries = null;
  }

  const closes = currentBars.map((b) => b.close);
  const rsiValues = calcRSI(closes, 14);
  if (!rsiValues.length) return;

  const offset = currentBars.length - rsiValues.length;
  const data = rsiValues.map((val, i) => ({
    time: currentBars[i + offset].date,
    value: val,
  }));

  rsiLineSeries = rsiChart.addSeries(LightweightCharts.LineSeries, {
    color: "#a78bfa",
    lineWidth: 1.5,
    priceLineVisible: false,
    lastValueVisible: true,
  });
  rsiLineSeries.setData(data);

  // Overbought / oversold reference lines
  rsiLineSeries.createPriceLine({
    price: 70,
    color: "rgba(248,113,113,0.5)",
    lineWidth: 1,
    lineStyle: 2,
    axisLabelVisible: true,
  });
  rsiLineSeries.createPriceLine({
    price: 30,
    color: "rgba(52,211,153,0.5)",
    lineWidth: 1,
    lineStyle: 2,
    axisLabelVisible: true,
  });
}

function toggleRSI() {
  rsiVisible = !rsiVisible;
  const container = document.getElementById("rsi-container");

  if (rsiVisible) {
    container.classList.remove("hidden");
    buildRSI();
    rsiChart.resize(container.clientWidth, container.clientHeight || 150);
    rsiChart.timeScale().fitContent();
    // Sync to main chart's current visible range
    const range = chart.timeScale().getVisibleLogicalRange();
    if (range) rsiChart.timeScale().setVisibleLogicalRange(range);
  } else {
    container.classList.add("hidden");
    if (rsiLineSeries) {
      rsiChart.removeSeries(rsiLineSeries);
      rsiLineSeries = null;
    }
  }
}

// ── Range switching ────────────────────────────────────────────────────────
function switchRange(range) {
  if (range === currentRange) return;
  currentRange = range;
  document.querySelectorAll("#range-buttons .toggle-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.range === range);
  });
  loadData(currentSymbol, currentRange);
}

// ── Crosshair handler ──────────────────────────────────────────────────────
function onCrosshairMove(param) {
  if (!param.time || !param.seriesData) {
    resetOHLC();
    return;
  }

  const dateEl = document.getElementById("ohlc-date");
  dateEl.textContent = formatTime(param.time);

  const mainData = candleSeries
    ? param.seriesData.get(candleSeries)
    : param.seriesData.get(lineSeries);

  if (!mainData) { resetOHLC(); return; }

  if (candleSeries && mainData.open != null) {
    document.getElementById("ohlc-o").textContent = fmtNum(mainData.open);
    document.getElementById("ohlc-h").textContent = fmtNum(mainData.high);
    document.getElementById("ohlc-l").textContent = fmtNum(mainData.low);
    document.getElementById("ohlc-c").textContent = fmtNum(mainData.close);
  } else if (mainData.value != null) {
    document.getElementById("ohlc-o").textContent = "\u2014";
    document.getElementById("ohlc-h").textContent = "\u2014";
    document.getElementById("ohlc-l").textContent = "\u2014";
    document.getElementById("ohlc-c").textContent = fmtNum(mainData.value);
  }

  const volData = volumeSeries ? param.seriesData.get(volumeSeries) : null;
  document.getElementById("ohlc-v").textContent =
    volData && volData.value ? fmtVol(volData.value) : "\u2014";
}

function resetOHLC() {
  if (!currentBars.length) return;
  const last = currentBars[currentBars.length - 1];
  document.getElementById("ohlc-date").textContent = last.date;
  document.getElementById("ohlc-o").textContent = isFredSymbol ? "\u2014" : fmtNum(last.open);
  document.getElementById("ohlc-h").textContent = isFredSymbol ? "\u2014" : fmtNum(last.high);
  document.getElementById("ohlc-l").textContent = isFredSymbol ? "\u2014" : fmtNum(last.low);
  document.getElementById("ohlc-c").textContent = fmtNum(last.close);
  document.getElementById("ohlc-v").textContent =
    !isFredSymbol && last.volume ? fmtVol(last.volume) : "\u2014";
}

function formatTime(time) {
  if (typeof time === "string") return time;
  if (time && time.year) {
    return `${time.year}-${String(time.month).padStart(2, "0")}-${String(time.day).padStart(2, "0")}`;
  }
  return String(time);
}

// ── Price stats ────────────────────────────────────────────────────────────
function updatePriceStats() {
  const container = document.getElementById("price-stats");
  if (!currentBars.length) { container.innerHTML = ""; return; }

  const last = currentBars[currentBars.length - 1];
  const rangeHigh = Math.max(...currentBars.map((b) => b.high));
  const rangeLow = Math.min(...currentBars.map((b) => b.low));
  const highLowLabel =
    ["1Y", "5Y", "Max"].includes(currentRange) ? "52W High / Low" : "Range High / Low";

  let stats;
  if (isFredSymbol) {
    stats = [
      { label: "Value", value: fmtNum(last.close) },
      { label: highLowLabel, value: `${fmtNum(rangeLow)} \u2013 ${fmtNum(rangeHigh)}` },
    ];
  } else {
    stats = [
      { label: "Open", value: fmtNum(last.open) },
      { label: "High", value: fmtNum(last.high) },
      { label: "Low", value: fmtNum(last.low) },
      { label: "Close", value: fmtNum(last.close) },
      { label: "Volume", value: last.volume ? fmtVol(last.volume) : "\u2014" },
      { label: highLowLabel, value: `${fmtNum(rangeLow)} \u2013 ${fmtNum(rangeHigh)}` },
    ];
  }

  container.innerHTML = stats
    .map((s) =>
      `<div class="stat"><span class="stat-label">${s.label}</span><span class="stat-value">${s.value}</span></div>`
    )
    .join("");
}

function updateHeaderPrice() {
  if (!currentBars.length) return;
  const last = currentBars[currentBars.length - 1];
  const first = currentBars[0];
  document.getElementById("current-price").textContent = fmtNum(last.close);

  const change = last.close - first.close;
  const changePct = first.close !== 0 ? (change / first.close) * 100 : 0;
  const positive = change >= 0;
  const arrow = positive ? "\u25B2" : "\u25BC";
  const sign = positive ? "+" : "";

  const el = document.getElementById("current-change");
  el.textContent = `${arrow} ${sign}${changePct.toFixed(2)}%`;
  el.className = `text-sm font-medium ${positive ? "text-emerald-400" : "text-red-400"}`;
}

// ── Formatting helpers ─────────────────────────────────────────────────────
function fmtNum(n) {
  if (n == null) return "\u2014";
  const num = Number(n);
  if (isNaN(num)) return "\u2014";
  if (Math.abs(num) >= 1000) {
    return num.toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }
  if (Math.abs(num) < 1) return num.toFixed(4);
  return num.toFixed(2);
}

function fmtVol(v) {
  if (v == null) return "\u2014";
  if (v >= 1e9) return (v / 1e9).toFixed(2) + "B";
  if (v >= 1e6) return (v / 1e6).toFixed(2) + "M";
  if (v >= 1e3) return (v / 1e3).toFixed(1) + "K";
  return String(v);
}

// ── UI state ───────────────────────────────────────────────────────────────
function showLoading(on) {
  document.getElementById("chart-loading").style.display = on ? "block" : "none";
  document.getElementById("chart-container").style.visibility = on ? "hidden" : "visible";
  document.getElementById("crosshair-ohlc").style.visibility = on ? "hidden" : "visible";
  document.getElementById("price-stats").style.visibility = on ? "hidden" : "visible";
  document.querySelector(".chart-controls").style.visibility = on ? "hidden" : "visible";
  const zoom = document.getElementById("zoom-buttons");
  if (zoom) zoom.style.visibility = on ? "hidden" : "visible";
}

function showError(message) {
  document.getElementById("chart-loading").style.display = "none";
  document.getElementById("chart-container").style.display = "none";
  document.getElementById("rsi-container").style.display = "none";
  document.getElementById("crosshair-ohlc").style.display = "none";
  document.getElementById("price-stats").style.display = "none";
  const controls = document.querySelector(".chart-controls");
  if (controls) controls.style.display = "none";
  const zoom = document.getElementById("zoom-buttons");
  if (zoom) zoom.style.display = "none";
  document.getElementById("chart-error").classList.remove("hidden");
  document.getElementById("chart-error-message").textContent = message;
}

// ── Boot ───────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", init);

// ── Watchlist star ──────────────────────────────────────────────────────
(function () {
  function initWatchlistStar() {
    var starBtn = document.getElementById("watchlist-star");
    if (!starBtn) return;

    var isInWatchlist = false;

    async function checkWatchlistState() {
      try {
        var resp = await fetch(API_BASE + "/api/watchlist", { credentials: "same-origin" });
        if (!resp.ok) return;
        var data = await resp.json();
        var symbols = (data.symbols || []).map(function(s) { return s.symbol; });
        isInWatchlist = symbols.indexOf(currentSymbol) !== -1;
        updateStarUI();
      } catch (e) {
        // ignore
      }
    }

    function updateStarUI() {
      if (isInWatchlist) {
        starBtn.innerHTML = "&#9733;";
        starBtn.classList.add("active");
        starBtn.title = "Remove from watchlist";
      } else {
        starBtn.innerHTML = "&#9734;";
        starBtn.classList.remove("active");
        starBtn.title = "Add to watchlist";
      }
    }

    starBtn.addEventListener("click", async function () {
      try {
        if (isInWatchlist) {
          var resp = await fetch(API_BASE + "/api/watchlist/" + encodeURIComponent(currentSymbol), {
            method: "DELETE",
            credentials: "same-origin",
          });
          if (resp.ok) {
            isInWatchlist = false;
            updateStarUI();
          }
        } else {
          var resp = await fetch(API_BASE + "/api/watchlist", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: "same-origin",
            body: JSON.stringify({ symbol: currentSymbol }),
          });
          if (resp.ok || resp.status === 409) {
            isInWatchlist = true;
            updateStarUI();
          }
        }
      } catch (e) {
        console.error("Watchlist toggle failed:", e);
      }
    });

    // Listen for auth ready
    window.addEventListener("bradan-auth-ready", function (e) {
      if (e.detail) {
        starBtn.classList.remove("hidden");
        checkWatchlistState();
      } else {
        starBtn.classList.add("hidden");
      }
    });

    // If auth already resolved
    if (window.bradanUser) {
      starBtn.classList.remove("hidden");
      checkWatchlistState();
    }
  }

  // Wait for DOM if needed, otherwise run immediately
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initWatchlistStar);
  } else {
    initWatchlistStar();
  }
})();
