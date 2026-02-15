/* Bradan — Dashboard sidebar (desktop persistent, mobile bottom sheet) */

(function () {
  var API_BASE = "";
  var COLLAPSED_KEY = "bradan_sidebar_collapsed";
  var sidebarData = [];
  var isCollapsed = localStorage.getItem(COLLAPSED_KEY) === "true";
  var sheetOpen = false;

  // SVGs for chevron toggle
  var CHEVRON_LEFT = '&#9001;';
  var CHEVRON_RIGHT = '&#9002;';
  var LIST_ICON = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><circle cx="3" cy="6" r="1" fill="currentColor"/><circle cx="3" cy="12" r="1" fill="currentColor"/><circle cx="3" cy="18" r="1" fill="currentColor"/></svg>';

  function escapeHtml(str) {
    var div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  function formatChange(val) {
    if (val == null) return '<span class="text-gray-500">&mdash;</span>';
    var cls = val >= 0 ? "sidebar-change-pos" : "sidebar-change-neg";
    var sign = val >= 0 ? "+" : "";
    return '<span class="' + cls + '">' + sign + val.toFixed(2) + '%</span>';
  }

  function formatPrice(val) {
    if (val == null) return "&mdash;";
    return val >= 1000 ? val.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})
      : val < 1 ? val.toFixed(4)
      : val.toFixed(2);
  }

  // Build sidebar content HTML (shared between desktop and mobile)
  function buildSymbolRows() {
    if (!sidebarData.length) return '';
    var html = '';
    for (var i = 0; i < sidebarData.length; i++) {
      var item = sidebarData[i];
      html += '<a href="/chart.html?symbol=' + encodeURIComponent(item.symbol) + '" class="sidebar-item">' +
        '<span class="sidebar-item-symbol">' + escapeHtml(item.symbol) + '</span>' +
        '<span class="sidebar-item-data">' +
          '<span class="sidebar-item-price">' + formatPrice(item.price) + '</span>' +
          formatChange(item.change_pct) +
        '</span>' +
      '</a>';
    }
    return html;
  }

  // Desktop sidebar
  function renderDesktopSidebar() {
    var container = document.getElementById("sidebar-container");
    if (!container) return;

    if (!sidebarData.length) {
      container.innerHTML = '';
      container.className = '';
      document.body.classList.remove("has-sidebar", "sidebar-collapsed");
      return;
    }

    var expandedHTML =
      '<div class="sidebar-header">' +
        '<span class="sidebar-title">Watchlist</span>' +
        '<button class="sidebar-toggle" id="sidebar-toggle-btn" title="Collapse">' + CHEVRON_LEFT + '</button>' +
      '</div>' +
      '<div class="sidebar-items">' + buildSymbolRows() + '</div>' +
      '<a href="/watchlist.html" class="sidebar-manage">Manage watchlist</a>';

    var collapsedHTML =
      '<div class="sidebar-collapsed-content" id="sidebar-expand-btn" title="Expand Watchlist">' +
        LIST_ICON +
      '</div>';

    container.innerHTML = isCollapsed ? collapsedHTML : expandedHTML;
    container.className = isCollapsed ? "sidebar sidebar-collapsed" : "sidebar sidebar-expanded";

    document.body.classList.add("has-sidebar");
    if (isCollapsed) {
      document.body.classList.add("sidebar-collapsed");
    } else {
      document.body.classList.remove("sidebar-collapsed");
    }

    // Wire toggle
    var toggleBtn = document.getElementById("sidebar-toggle-btn");
    var expandBtn = document.getElementById("sidebar-expand-btn");
    if (toggleBtn) {
      toggleBtn.addEventListener("click", function() {
        isCollapsed = true;
        localStorage.setItem(COLLAPSED_KEY, "true");
        renderDesktopSidebar();
      });
    }
    if (expandBtn) {
      expandBtn.addEventListener("click", function() {
        isCollapsed = false;
        localStorage.setItem(COLLAPSED_KEY, "false");
        renderDesktopSidebar();
      });
    }
  }

  // Mobile bottom sheet
  function renderMobileSheet() {
    var sheet = document.getElementById("mobile-watchlist-sheet");
    var btn = document.getElementById("mobile-watchlist-btn");
    if (!sheet || !btn) return;

    if (!sidebarData.length) {
      btn.classList.add("hidden");
      sheet.classList.add("hidden");
      return;
    }

    btn.classList.remove("hidden");

    sheet.innerHTML =
      '<div class="mobile-sheet-overlay" id="mobile-sheet-overlay"></div>' +
      '<div class="mobile-sheet-content">' +
        '<div class="mobile-sheet-handle"></div>' +
        '<div class="sidebar-header">' +
          '<span class="sidebar-title">Watchlist</span>' +
          '<button class="mobile-sheet-close" id="mobile-sheet-close">&times;</button>' +
        '</div>' +
        '<div class="sidebar-items">' + buildSymbolRows() + '</div>' +
        '<a href="/watchlist.html" class="sidebar-manage">Manage watchlist</a>' +
      '</div>';

    // Wire open/close
    btn.onclick = function() { openSheet(); };
    document.getElementById("mobile-sheet-overlay").onclick = function() { closeSheet(); };
    document.getElementById("mobile-sheet-close").onclick = function() { closeSheet(); };
  }

  function openSheet() {
    var sheet = document.getElementById("mobile-watchlist-sheet");
    if (sheet) {
      sheetOpen = true;
      sheet.classList.remove("hidden");
      setTimeout(function() { sheet.classList.add("open"); }, 10);
    }
  }

  function closeSheet() {
    var sheet = document.getElementById("mobile-watchlist-sheet");
    if (sheet) {
      sheetOpen = false;
      sheet.classList.remove("open");
      setTimeout(function() { sheet.classList.add("hidden"); }, 300);
    }
  }

  // Fetch watchlist data
  async function loadData() {
    try {
      var resp = await fetch(API_BASE + "/api/watchlist", { credentials: "same-origin" });
      if (!resp.ok) return;
      var data = await resp.json();
      sidebarData = data.symbols || [];
    } catch (e) {
      sidebarData = [];
    }
    renderDesktopSidebar();
    renderMobileSheet();
  }

  // Auth state handler
  function onAuthReady(user) {
    if (user) {
      loadData();
    } else {
      sidebarData = [];
      renderDesktopSidebar();
      renderMobileSheet();
    }
  }

  // Init
  window.addEventListener("bradan-auth-ready", function(e) {
    onAuthReady(e.detail);
  });

  // If auth already resolved before this script loaded
  if (window.bradanUser) {
    onAuthReady(window.bradanUser);
  }
})();
