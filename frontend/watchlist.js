/* Bradán — Watchlist page */

(function () {
  var API_BASE = "";
  var watchlistData = []; // current watchlist items from API
  var analysisCache = {}; // { "SYMBOL": "analysis text" }
  var searchTimer = null;

  /* ── Helpers ──────────────────────────────────────────────────────────── */

  function escapeHtml(str) {
    var div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  function escapeAttr(str) {
    return str
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function debounce(fn, ms) {
    var timer;
    return function () {
      var args = arguments;
      var ctx = this;
      clearTimeout(timer);
      timer = setTimeout(function () {
        fn.apply(ctx, args);
      }, ms);
    };
  }

  function formatPrice(price) {
    var num = Number(price);
    if (isNaN(num)) return "\u2014";
    return "$" + num.toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  /* ── Loading / content toggle ────────────────────────────────────────── */

  function hideLoading() {
    var el = document.getElementById("watchlist-loading");
    if (el) el.style.display = "none";
  }

  function showContent() {
    hideLoading();
    var el = document.getElementById("watchlist-content");
    if (el) el.classList.remove("hidden");
  }

  /* ── Logged-out CTA ──────────────────────────────────────────────────── */

  function renderLoggedOut() {
    showContent();
    var content = document.getElementById("watchlist-content");
    content.innerHTML =
      '<div class="watchlist-cta">' +
        '<div class="text-lg font-semibold mb-2">Track your portfolio</div>' +
        '<p class="text-gray-400 mb-4">Sign in to create a personal watchlist. Track prices, get AI-powered analysis, and monitor your holdings — all in one place.</p>' +
        '<button class="watchlist-cta-btn" id="watchlist-signin-btn">Sign in to get started</button>' +
      '</div>';

    var btn = document.getElementById("watchlist-signin-btn");
    if (btn) {
      btn.addEventListener("click", function () {
        var navSignIn = document.getElementById("auth-signin-btn");
        if (navSignIn) {
          navSignIn.click();
        }
      });
    }
  }

  /* ── Logged-in rendering ─────────────────────────────────────────────── */

  function renderLoggedIn() {
    showContent();
    var content = document.getElementById("watchlist-content");
    content.innerHTML =
      '<div class="watchlist-add-bar">' +
        '<div class="watchlist-add-bar-inner">' +
          '<input type="text" id="watchlist-search-input" class="watchlist-search-input" placeholder="Search ticker to add\u2026" autocomplete="off" spellcheck="false" />' +
          '<div id="watchlist-search-spinner" class="watchlist-search-spinner hidden">' +
            '<svg class="animate-spin" width="16" height="16" viewBox="0 0 24 24" fill="none">' +
              '<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>' +
              '<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>' +
            '</svg>' +
          '</div>' +
        '</div>' +
        '<div id="watchlist-search-dropdown" class="watchlist-add-dropdown hidden"></div>' +
        '<div id="watchlist-search-message" class="watchlist-search-message hidden"></div>' +
      '</div>' +
      '<div id="watchlist-cards"></div>';

    wireSearch();
    loadWatchlist();
  }

  /* ── API calls ───────────────────────────────────────────────────────── */

  async function loadWatchlist() {
    try {
      var resp = await fetch(API_BASE + "/api/watchlist", {
        credentials: "same-origin",
      });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      var data = await resp.json();
      watchlistData = data.symbols || [];
      renderCards();
    } catch (err) {
      console.error("Failed to load watchlist:", err);
      renderCardsError();
    }
  }

  async function addSymbol(symbol) {
    try {
      var resp = await fetch(API_BASE + "/api/watchlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ symbol: symbol }),
      });

      if (resp.status === 409) {
        showSearchMessage("Already in watchlist", "warn");
        return;
      }
      if (!resp.ok) {
        var errData = await resp.json().catch(function () {
          return { detail: "Failed to add symbol" };
        });
        showSearchMessage(errData.detail || "Failed to add symbol", "error");
        return;
      }

      showSearchMessage(symbol + " added", "success");
      hideSearchDropdown();
      clearSearchInput();
      await loadWatchlist();
    } catch (err) {
      console.error("Failed to add symbol:", err);
      showSearchMessage("Failed to add symbol", "error");
    }
  }

  async function removeSymbol(symbol) {
    try {
      var resp = await fetch(
        API_BASE + "/api/watchlist/" + encodeURIComponent(symbol),
        {
          method: "DELETE",
          credentials: "same-origin",
        }
      );
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      await loadWatchlist();
    } catch (err) {
      console.error("Failed to remove symbol:", err);
    }
  }

  async function reorderSymbols(orderedSymbols) {
    try {
      var resp = await fetch(API_BASE + "/api/watchlist/reorder", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ symbols: orderedSymbols }),
      });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
    } catch (err) {
      console.error("Failed to reorder watchlist:", err);
    }
  }

  async function requestAnalysis(symbol) {
    try {
      var resp = await fetch(
        API_BASE + "/api/watchlist/" + encodeURIComponent(symbol) + "/analysis",
        {
          method: "POST",
          credentials: "same-origin",
        }
      );
      if (resp.status === 501) {
        return { error: "Analysis coming soon" };
      }
      if (!resp.ok) {
        var errData = await resp.json().catch(function () {
          return { detail: "Analysis failed" };
        });
        return { error: errData.detail || "Analysis failed" };
      }
      return await resp.json();
    } catch (err) {
      console.error("Analysis request failed:", err);
      return { error: "Analysis unavailable" };
    }
  }

  /* ── Search bar ──────────────────────────────────────────────────────── */

  function wireSearch() {
    var input = document.getElementById("watchlist-search-input");
    if (!input) return;

    var debouncedSearch = debounce(function (val) {
      searchSymbol(val);
    }, 400);

    input.addEventListener("input", function (e) {
      var val = e.target.value.trim();
      hideSearchMessage();
      if (!val) {
        hideSearchDropdown();
        return;
      }
      debouncedSearch(val);
    });

    input.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        hideSearchDropdown();
        hideSearchMessage();
      }
    });

    // Close dropdown on outside click
    document.addEventListener("click", function (e) {
      if (!e.target.closest(".watchlist-add-bar")) {
        hideSearchDropdown();
      }
    });
  }

  async function searchSymbol(query) {
    var trimmed = query.trim().toUpperCase();
    var spinner = document.getElementById("watchlist-search-spinner");

    if (!trimmed) {
      hideSearchDropdown();
      return;
    }

    if (spinner) spinner.classList.remove("hidden");

    try {
      var resp = await fetch(
        API_BASE + "/api/search/" + encodeURIComponent(trimmed)
      );
      if (resp.status === 404) {
        showSearchDropdownEmpty(trimmed);
        return;
      }
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      var data = await resp.json();
      showSearchDropdownResult(data);
    } catch (err) {
      console.error("Watchlist search failed:", err);
      showSearchDropdownError();
    } finally {
      if (spinner) spinner.classList.add("hidden");
    }
  }

  function showSearchDropdownResult(data) {
    var dd = document.getElementById("watchlist-search-dropdown");
    if (!dd) return;

    var symbol = data.symbol || "";
    var price = data.price != null ? formatPrice(data.price) : "";
    var changePct = data.change_pct != null ? Number(data.change_pct) : null;

    var changeHtml = "";
    if (changePct !== null) {
      var positive = changePct >= 0;
      var sign = positive ? "+" : "";
      var colorClass = positive ? "positive" : "negative";
      changeHtml =
        '<span class="watchlist-dropdown-change ' + colorClass + '">' +
          sign + changePct.toFixed(2) + "%" +
        '</span>';
    }

    var priceHtml = price
      ? '<span class="watchlist-dropdown-price">' + escapeHtml(price) + '</span>'
      : "";

    // Check if already in watchlist
    var alreadyAdded = watchlistData.some(function (item) {
      return item.symbol === symbol;
    });

    var actionHtml = alreadyAdded
      ? '<span class="watchlist-dropdown-added">In watchlist</span>'
      : '<button class="watchlist-dropdown-add-btn" data-symbol="' +
          escapeAttr(symbol) +
        '">+ Add</button>';

    dd.innerHTML =
      '<div class="watchlist-dropdown-item">' +
        '<div class="watchlist-dropdown-left">' +
          '<span class="watchlist-dropdown-symbol">' + escapeHtml(symbol) + '</span>' +
          priceHtml +
          changeHtml +
        '</div>' +
        actionHtml +
      '</div>';
    dd.classList.remove("hidden");

    // Wire add button
    var addBtn = dd.querySelector(".watchlist-dropdown-add-btn");
    if (addBtn) {
      addBtn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        var sym = addBtn.getAttribute("data-symbol");
        addSymbol(sym);
      });
    }
  }

  function showSearchDropdownEmpty(query) {
    var dd = document.getElementById("watchlist-search-dropdown");
    if (!dd) return;
    dd.innerHTML =
      '<div class="watchlist-dropdown-empty">No results for "' +
        escapeHtml(query) +
      '"</div>';
    dd.classList.remove("hidden");
  }

  function showSearchDropdownError() {
    var dd = document.getElementById("watchlist-search-dropdown");
    if (!dd) return;
    dd.innerHTML =
      '<div class="watchlist-dropdown-empty">Search unavailable</div>';
    dd.classList.remove("hidden");
  }

  function hideSearchDropdown() {
    var dd = document.getElementById("watchlist-search-dropdown");
    if (dd) {
      dd.classList.add("hidden");
      dd.innerHTML = "";
    }
  }

  function clearSearchInput() {
    var input = document.getElementById("watchlist-search-input");
    if (input) input.value = "";
  }

  function showSearchMessage(text, type) {
    var el = document.getElementById("watchlist-search-message");
    if (!el) return;
    el.textContent = text;
    el.className = "watchlist-search-message " + (type || "");
    el.classList.remove("hidden");
    // Auto-hide after 3 seconds
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function () {
      hideSearchMessage();
    }, 3000);
  }

  function hideSearchMessage() {
    var el = document.getElementById("watchlist-search-message");
    if (el) el.classList.add("hidden");
  }

  /* ── Watchlist cards ─────────────────────────────────────────────────── */

  function renderCards() {
    var container = document.getElementById("watchlist-cards");
    if (!container) return;

    if (!watchlistData.length) {
      container.innerHTML =
        '<div class="watchlist-empty">' +
          '<p class="text-gray-400">Your watchlist is empty</p>' +
          '<p class="text-gray-500 text-sm mt-1">Use the search bar above to add symbols</p>' +
        '</div>';
      return;
    }

    var html = "";
    for (var i = 0; i < watchlistData.length; i++) {
      var item = watchlistData[i];
      html += renderCard(item, i);
    }
    container.innerHTML = html;

    wireCardEvents(container);
  }

  function renderCard(item, index) {
    var symbol = item.symbol;
    var hasPrice = item.price != null && !isNaN(item.price);
    var priceDisplay = hasPrice ? formatPrice(item.price) : "\u2014";
    var pct = Number(item.change_pct) || 0;
    var positive = pct >= 0;
    var sign = positive ? "+" : "";
    var colorClass = positive ? "positive" : "negative";

    var changeHtml = hasPrice
      ? '<span class="watchlist-card-change ' + colorClass + '">' +
          sign + pct.toFixed(2) + '%' +
        '</span>'
      : '<span class="watchlist-card-change">No data</span>';

    var isFirst = index === 0;
    var isLast = index === watchlistData.length - 1;

    var analysisResult = analysisCache[symbol];
    var analysisHtml = "";
    if (analysisResult) {
      analysisHtml =
        '<div class="watchlist-analysis-result" id="analysis-' + escapeAttr(symbol) + '">' +
          '<p class="text-sm text-gray-300">' + escapeHtml(analysisResult) + '</p>' +
        '</div>';
    }

    return (
      '<div class="watchlist-card" data-symbol="' + escapeAttr(symbol) + '">' +
        '<div class="watchlist-card-header">' +
          '<div class="watchlist-card-symbol">' +
            '<a href="/chart.html?symbol=' + encodeURIComponent(symbol) + '" class="watchlist-card-ticker">' + escapeHtml(symbol) + '</a>' +
          '</div>' +
          '<div class="watchlist-card-price">' +
            '<span class="watchlist-card-current">' + priceDisplay + '</span>' +
            changeHtml +
          '</div>' +
        '</div>' +
        '<div class="watchlist-card-actions">' +
          '<button class="watchlist-move-btn" data-dir="up" data-symbol="' + escapeAttr(symbol) + '" title="Move up"' +
            (isFirst ? ' disabled' : '') + '>&#9650;</button>' +
          '<button class="watchlist-move-btn" data-dir="down" data-symbol="' + escapeAttr(symbol) + '" title="Move down"' +
            (isLast ? ' disabled' : '') + '>&#9660;</button>' +
          '<button class="watchlist-analysis-btn" data-symbol="' + escapeAttr(symbol) + '">Generate Analysis</button>' +
          '<button class="watchlist-remove-btn" data-symbol="' + escapeAttr(symbol) + '" title="Remove">&times;</button>' +
        '</div>' +
        analysisHtml +
      '</div>'
    );
  }

  function renderCardsError() {
    var container = document.getElementById("watchlist-cards");
    if (!container) return;
    container.innerHTML =
      '<div class="watchlist-empty">' +
        '<p class="text-gray-400">Failed to load watchlist</p>' +
        '<p class="text-gray-500 text-sm mt-1">Please try refreshing the page</p>' +
      '</div>';
  }

  /* ── Card event wiring ───────────────────────────────────────────────── */

  function wireCardEvents(container) {
    // Move buttons
    container.querySelectorAll(".watchlist-move-btn").forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        var symbol = btn.getAttribute("data-symbol");
        var dir = btn.getAttribute("data-dir");
        handleMove(symbol, dir);
      });
    });

    // Remove buttons
    container.querySelectorAll(".watchlist-remove-btn").forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        var symbol = btn.getAttribute("data-symbol");
        removeSymbol(symbol);
      });
    });

    // Analysis buttons
    container.querySelectorAll(".watchlist-analysis-btn").forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        var symbol = btn.getAttribute("data-symbol");
        handleAnalysis(symbol, btn);
      });
    });
  }

  function handleMove(symbol, direction) {
    var idx = -1;
    for (var i = 0; i < watchlistData.length; i++) {
      if (watchlistData[i].symbol === symbol) {
        idx = i;
        break;
      }
    }
    if (idx === -1) return;

    var newIdx = direction === "up" ? idx - 1 : idx + 1;
    if (newIdx < 0 || newIdx >= watchlistData.length) return;

    // Swap in local data
    var temp = watchlistData[idx];
    watchlistData[idx] = watchlistData[newIdx];
    watchlistData[newIdx] = temp;

    // Re-render immediately
    renderCards();

    // Persist the new order to the backend
    var orderedSymbols = watchlistData.map(function (item) {
      return item.symbol;
    });
    reorderSymbols(orderedSymbols);
  }

  async function handleAnalysis(symbol, btn) {
    // If we already have cached analysis, just toggle visibility
    if (analysisCache[symbol]) {
      var existing = document.getElementById("analysis-" + symbol);
      if (existing) {
        existing.classList.toggle("hidden");
      }
      return;
    }

    // Show loading state on button
    var originalText = btn.textContent;
    btn.disabled = true;
    btn.innerHTML = '<span class="auth-spinner"></span>Loading\u2026';

    var result = await requestAnalysis(symbol);

    btn.disabled = false;
    btn.innerHTML = originalText;

    if (result.error) {
      // Show error inline
      analysisCache[symbol] = result.error;
    } else if (result.analysis) {
      analysisCache[symbol] = result.analysis;
    } else {
      analysisCache[symbol] = "Analysis coming soon";
    }

    // Re-render to show the analysis
    renderCards();
  }

  /* ── Auth-aware initialization ───────────────────────────────────────── */

  function onAuthReady(user) {
    if (user) {
      renderLoggedIn();
    } else {
      renderLoggedOut();
    }
  }

  function init() {
    // Listen for auth state
    window.addEventListener("bradan-auth-ready", function (e) {
      onAuthReady(e.detail);
    });

    // If auth already resolved before this script loaded
    if (typeof window.bradanUser !== "undefined") {
      onAuthReady(window.bradanUser);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
