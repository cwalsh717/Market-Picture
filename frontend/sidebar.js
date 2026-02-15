/* Bradan — Widget panel sidebar (desktop persistent, mobile bottom sheet) */

(function () {
  var API_BASE = "";
  var COLLAPSED_KEY = "bradan_sidebar_collapsed";
  var isCollapsed = localStorage.getItem(COLLAPSED_KEY) === "true";
  var sheetOpen = false;
  var watchlistsData = []; // Array of {id, name, is_default, items: [{symbol, position}]}
  var priceCache = {};     // symbol -> {price, change_pct, change_abs}
  var collapsedWidgets = {}; // watchlist_id -> boolean (collapsed state)
  var addDropdownOpen = false;

  // SVGs / icons
  var LIST_ICON = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><circle cx="3" cy="6" r="1" fill="currentColor"/><circle cx="3" cy="12" r="1" fill="currentColor"/><circle cx="3" cy="18" r="1" fill="currentColor"/></svg>';

  // ─── Helpers ────────────────────────────────────────────────────────

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

  // ─── Price cache ────────────────────────────────────────────────────

  function buildPriceCacheFromSnapshot() {
    var snap = window.bradanSnapshotData;
    if (!snap || !snap.assets) return;
    var groups = snap.assets;
    for (var key in groups) {
      if (!groups.hasOwnProperty(key)) continue;
      var items = groups[key];
      if (!Array.isArray(items)) continue;
      for (var i = 0; i < items.length; i++) {
        var item = items[i];
        if (item.symbol) {
          priceCache[item.symbol] = {
            price: item.price,
            change_pct: item.change_pct,
            change_abs: item.change_abs
          };
        }
      }
    }
  }

  function getPrice(symbol) {
    return priceCache[symbol] || null;
  }

  async function fetchMissingPrices(watchlistId, symbols) {
    // Filter symbols not in cache
    var missing = [];
    for (var i = 0; i < symbols.length; i++) {
      if (!priceCache[symbols[i]]) {
        missing.push(symbols[i]);
      }
    }
    if (missing.length === 0) return;

    try {
      var resp = await fetch(API_BASE + "/api/watchlists/" + watchlistId + "/prices", {
        credentials: "same-origin"
      });
      if (!resp.ok) return;
      var data = await resp.json();
      var items = data.items || [];
      for (var j = 0; j < items.length; j++) {
        var item = items[j];
        if (item.symbol && !priceCache[item.symbol]) {
          priceCache[item.symbol] = {
            price: item.price,
            change_pct: item.change_pct,
            change_abs: item.change_abs
          };
        }
      }
    } catch (e) {
      console.warn("Failed to fetch prices for watchlist", watchlistId, e);
    }
  }

  // ─── API calls ──────────────────────────────────────────────────────

  async function fetchWatchlists() {
    try {
      var resp = await fetch(API_BASE + "/api/watchlists", { credentials: "same-origin" });
      if (!resp.ok) return [];
      var data = await resp.json();
      return data.watchlists || [];
    } catch (e) {
      console.warn("Failed to fetch watchlists", e);
      return [];
    }
  }

  async function createWatchlist(name) {
    try {
      var resp = await fetch(API_BASE + "/api/watchlists", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name })
      });
      if (!resp.ok) return null;
      return await resp.json();
    } catch (e) {
      console.warn("Failed to create watchlist", e);
      return null;
    }
  }

  async function renameWatchlist(id, name) {
    try {
      var resp = await fetch(API_BASE + "/api/watchlists/" + id, {
        method: "PUT",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name })
      });
      return resp.ok;
    } catch (e) {
      console.warn("Failed to rename watchlist", e);
      return false;
    }
  }

  async function deleteWatchlist(id) {
    try {
      var resp = await fetch(API_BASE + "/api/watchlists/" + id, {
        method: "DELETE",
        credentials: "same-origin"
      });
      return resp.ok;
    } catch (e) {
      console.warn("Failed to delete watchlist", e);
      return false;
    }
  }

  async function addItem(watchlistId, symbol) {
    try {
      var resp = await fetch(API_BASE + "/api/watchlists/" + watchlistId + "/items", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol: symbol })
      });
      return resp.ok || resp.status === 409;
    } catch (e) {
      console.warn("Failed to add item", e);
      return false;
    }
  }

  async function removeItem(watchlistId, symbol) {
    try {
      var resp = await fetch(API_BASE + "/api/watchlists/" + watchlistId + "/items/" + encodeURIComponent(symbol), {
        method: "DELETE",
        credentials: "same-origin"
      });
      return resp.ok;
    } catch (e) {
      console.warn("Failed to remove item", e);
      return false;
    }
  }

  async function reorderItems(watchlistId, symbols) {
    try {
      var resp = await fetch(API_BASE + "/api/watchlists/" + watchlistId + "/items/reorder", {
        method: "PUT",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbols: symbols })
      });
      return resp.ok;
    } catch (e) {
      console.warn("Failed to reorder items", e);
      return false;
    }
  }

  // ─── Widget HTML builders ───────────────────────────────────────────

  function buildTickerRow(wlId, symbol) {
    var p = getPrice(symbol);
    var priceStr = p ? formatPrice(p.price) : "&mdash;";
    var changeStr = p ? formatChange(p.change_pct) : '<span class="text-gray-500">&mdash;</span>';

    return '<div class="sidebar-item" draggable="true" data-symbol="' + escapeHtml(symbol) + '" data-wl-id="' + wlId + '">' +
      '<span class="sidebar-item-symbol">' + escapeHtml(symbol) + '</span>' +
      '<span class="sidebar-item-data">' +
        '<span class="sidebar-item-price">' + priceStr + '</span>' +
        changeStr +
      '</span>' +
      '<button class="sidebar-item-remove" title="Remove">&times;</button>' +
    '</div>';
  }

  function buildWidgetHtml(wl) {
    var wlId = wl.id;
    var isWidgetCollapsed = !!collapsedWidgets[wlId];
    var collapseIcon = isWidgetCollapsed ? "&#9656;" : "&#9662;"; // ▸ or ▾
    var bodyClass = isWidgetCollapsed ? "sidebar-widget-body hidden" : "sidebar-widget-body";

    var html = '<div class="sidebar-widget" data-wl-id="' + wlId + '">';

    // Widget header
    html += '<div class="sidebar-widget-header">';
    html += '<span class="sidebar-widget-name" data-wl-id="' + wlId + '">' + escapeHtml(wl.name) + '</span>';
    html += '<div style="display:flex;gap:0.25rem;align-items:center">';
    html += '<button class="sidebar-widget-collapse" data-wl-id="' + wlId + '" title="' + (isWidgetCollapsed ? "Expand" : "Collapse") + '">' + collapseIcon + '</button>';
    html += '<button class="sidebar-widget-menu-btn" data-wl-id="' + wlId + '" title="Options">&#8943;</button>';
    html += '</div>';
    html += '</div>';

    // Menu (hidden by default)
    html += '<div class="sidebar-widget-menu hidden" data-wl-id="' + wlId + '">';
    html += '<button class="sidebar-widget-menu-item" data-action="rename" data-wl-id="' + wlId + '">Rename</button>';
    html += '<button class="sidebar-widget-menu-item" data-action="delete" data-wl-id="' + wlId + '">Delete</button>';
    html += '</div>';

    // Widget body
    html += '<div class="' + bodyClass + '" data-wl-id="' + wlId + '">';

    // Ticker rows
    var items = wl.items || [];
    for (var i = 0; i < items.length; i++) {
      html += buildTickerRow(wlId, items[i].symbol);
    }

    // Add ticker input
    html += '<div class="sidebar-add-ticker">';
    html += '<input type="text" placeholder="Add ticker..." class="sidebar-ticker-input" data-wl-id="' + wlId + '" />';
    html += '</div>';

    html += '</div>'; // .sidebar-widget-body
    html += '</div>'; // .sidebar-widget

    return html;
  }

  function buildAddDropdownHtml() {
    var cls = addDropdownOpen ? "sidebar-add-dropdown" : "sidebar-add-dropdown hidden";
    var html = '<div class="' + cls + '" id="sidebar-add-dropdown">';
    html += '<div class="sidebar-add-option" data-action="new">+ New Watchlist</div>';
    html += '</div>';
    return html;
  }

  function buildWidgetsArea() {
    var html = '';
    for (var i = 0; i < watchlistsData.length; i++) {
      html += buildWidgetHtml(watchlistsData[i]);
    }
    return html;
  }

  // ─── Desktop sidebar rendering ─────────────────────────────────────

  function renderDesktopSidebar() {
    var container = document.getElementById("sidebar-container");
    if (!container) return;

    if (!watchlistsData.length && !window.bradanUser) {
      container.innerHTML = '';
      container.className = '';
      return;
    }

    // Even if no watchlists, show sidebar for logged-in users (they can create one)
    if (!window.bradanUser) {
      container.innerHTML = '';
      container.className = '';
      return;
    }

    var expandedHTML =
      '<div class="sidebar-header">' +
        '<span class="sidebar-title">Widgets</span>' +
        '<div style="display:flex;gap:0.25rem;align-items:center">' +
          '<button class="sidebar-add-btn" id="sidebar-add-btn" title="Add widget">+</button>' +
          '<button class="sidebar-toggle" id="sidebar-toggle-btn" title="Collapse">&#9001;</button>' +
        '</div>' +
      '</div>' +
      buildAddDropdownHtml() +
      '<div class="sidebar-items">' + buildWidgetsArea() + '</div>' +
      '<a href="/watchlist.html" class="sidebar-manage">Manage watchlists</a>';

    var collapsedHTML =
      '<div class="sidebar-collapsed-content" id="sidebar-expand-btn" title="Expand Widgets">' +
        LIST_ICON +
      '</div>';

    container.innerHTML = isCollapsed ? collapsedHTML : expandedHTML;
    container.className = isCollapsed ? "sidebar sidebar-collapsed" : "sidebar sidebar-expanded";

    wireDesktopEvents(container);
  }

  function wireDesktopEvents(container) {
    // Toggle collapse/expand
    var toggleBtn = document.getElementById("sidebar-toggle-btn");
    var expandBtn = document.getElementById("sidebar-expand-btn");
    if (toggleBtn) {
      toggleBtn.addEventListener("click", function () {
        isCollapsed = true;
        localStorage.setItem(COLLAPSED_KEY, "true");
        renderDesktopSidebar();
      });
    }
    if (expandBtn) {
      expandBtn.addEventListener("click", function () {
        isCollapsed = false;
        localStorage.setItem(COLLAPSED_KEY, "false");
        renderDesktopSidebar();
      });
    }

    // "+" button to open add dropdown
    var addBtn = document.getElementById("sidebar-add-btn");
    if (addBtn) {
      addBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        addDropdownOpen = !addDropdownOpen;
        var dd = document.getElementById("sidebar-add-dropdown");
        if (dd) {
          dd.classList.toggle("hidden", !addDropdownOpen);
        }
      });
    }

    // Event delegation on the container for all widget interactions
    container.addEventListener("click", handleSidebarClick);
    container.addEventListener("keydown", handleSidebarKeydown);

    // Wire drag-and-drop on ticker rows
    wireDragAndDrop(container);

    // Close add dropdown on outside click
    document.addEventListener("click", function closeAddDropdown(e) {
      if (addDropdownOpen && !e.target.closest("#sidebar-add-btn") && !e.target.closest("#sidebar-add-dropdown")) {
        addDropdownOpen = false;
        var dd = document.getElementById("sidebar-add-dropdown");
        if (dd) dd.classList.add("hidden");
      }
    });
  }

  // ─── Event handlers (delegated) ────────────────────────────────────

  function handleSidebarClick(e) {
    var target = e.target;

    // Add dropdown: "New Watchlist"
    if (target.classList.contains("sidebar-add-option") && target.dataset.action === "new") {
      e.stopPropagation();
      addDropdownOpen = false;
      var name = prompt("Watchlist name:");
      if (name && name.trim()) {
        createWatchlist(name.trim()).then(function () {
          loadData();
        });
      }
      return;
    }

    // Widget collapse/expand toggle
    if (target.classList.contains("sidebar-widget-collapse")) {
      var wlId = parseInt(target.dataset.wlId, 10);
      collapsedWidgets[wlId] = !collapsedWidgets[wlId];
      renderDesktopSidebar();
      renderMobileSheet();
      return;
    }

    // Widget menu button (three dots)
    if (target.classList.contains("sidebar-widget-menu-btn")) {
      e.stopPropagation();
      var wlId = parseInt(target.dataset.wlId, 10);
      var menu = document.querySelector('.sidebar-widget-menu[data-wl-id="' + wlId + '"]');
      if (menu) {
        // Close all other menus first
        var allMenus = document.querySelectorAll(".sidebar-widget-menu");
        for (var i = 0; i < allMenus.length; i++) {
          if (parseInt(allMenus[i].dataset.wlId, 10) !== wlId) {
            allMenus[i].classList.add("hidden");
          }
        }
        menu.classList.toggle("hidden");
      }
      return;
    }

    // Widget menu item actions
    if (target.classList.contains("sidebar-widget-menu-item")) {
      var action = target.dataset.action;
      var wlId = parseInt(target.dataset.wlId, 10);

      if (action === "rename") {
        startInlineRename(wlId);
      } else if (action === "delete") {
        if (confirm("Delete this watchlist?")) {
          deleteWatchlist(wlId).then(function () {
            loadData();
          });
        }
      }
      // Close menu
      var menu = target.closest(".sidebar-widget-menu");
      if (menu) menu.classList.add("hidden");
      return;
    }

    // Widget name click -> inline rename
    if (target.classList.contains("sidebar-widget-name")) {
      var wlId = parseInt(target.dataset.wlId, 10);
      startInlineRename(wlId);
      return;
    }

    // Remove ticker button
    if (target.classList.contains("sidebar-item-remove")) {
      e.stopPropagation();
      var row = target.closest(".sidebar-item");
      if (!row) return;
      var symbol = row.dataset.symbol;
      var wlId = parseInt(row.dataset.wlId, 10);
      removeItem(wlId, symbol).then(function (ok) {
        if (ok) loadData();
      });
      return;
    }

    // Click on a ticker row (navigate to chart)
    var row = target.closest(".sidebar-item");
    if (row && !target.classList.contains("sidebar-item-remove")) {
      var symbol = row.dataset.symbol;
      if (symbol) {
        window.location.href = "/chart.html?symbol=" + encodeURIComponent(symbol);
      }
      return;
    }
  }

  function handleSidebarKeydown(e) {
    var target = e.target;

    // Add ticker input: Enter to add
    if (target.classList.contains("sidebar-ticker-input") && e.key === "Enter") {
      var symbol = target.value.trim().toUpperCase();
      if (!symbol) return;
      var wlId = parseInt(target.dataset.wlId, 10);
      target.value = "";
      addItem(wlId, symbol).then(function () {
        loadData();
      });
      return;
    }

    // Rename input: Enter to confirm, Escape to cancel
    if (target.classList.contains("sidebar-rename-input")) {
      var wlId = parseInt(target.dataset.wlId, 10);
      if (e.key === "Enter") {
        var newName = target.value.trim();
        if (newName) {
          renameWatchlist(wlId, newName).then(function () {
            loadData();
          });
        } else {
          renderDesktopSidebar();
          renderMobileSheet();
        }
      } else if (e.key === "Escape") {
        renderDesktopSidebar();
        renderMobileSheet();
      }
      return;
    }
  }

  // ─── Inline rename ──────────────────────────────────────────────────

  function startInlineRename(wlId) {
    var nameSpan = document.querySelector('.sidebar-widget-name[data-wl-id="' + wlId + '"]');
    if (!nameSpan) return;

    var currentName = "";
    for (var i = 0; i < watchlistsData.length; i++) {
      if (watchlistsData[i].id === wlId) {
        currentName = watchlistsData[i].name;
        break;
      }
    }

    var input = document.createElement("input");
    input.type = "text";
    input.className = "sidebar-rename-input";
    input.value = currentName;
    input.dataset.wlId = wlId;

    nameSpan.parentNode.replaceChild(input, nameSpan);
    input.focus();
    input.select();

    // On blur, confirm rename
    input.addEventListener("blur", function () {
      var newName = input.value.trim();
      if (newName && newName !== currentName) {
        renameWatchlist(wlId, newName).then(function () {
          loadData();
        });
      } else {
        renderDesktopSidebar();
        renderMobileSheet();
      }
    });
  }

  // ─── Drag and drop ──────────────────────────────────────────────────

  function wireDragAndDrop(container) {
    var dragSymbol = null;
    var dragWlId = null;

    container.addEventListener("dragstart", function (e) {
      var row = e.target.closest(".sidebar-item");
      if (!row) return;
      dragSymbol = row.dataset.symbol;
      dragWlId = parseInt(row.dataset.wlId, 10);
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", dragSymbol);
      row.style.opacity = "0.5";
    });

    container.addEventListener("dragend", function (e) {
      var row = e.target.closest(".sidebar-item");
      if (row) row.style.opacity = "";
      // Clear all drag-over styles
      var items = container.querySelectorAll(".sidebar-item.drag-over");
      for (var i = 0; i < items.length; i++) {
        items[i].classList.remove("drag-over");
      }
      dragSymbol = null;
      dragWlId = null;
    });

    container.addEventListener("dragover", function (e) {
      var row = e.target.closest(".sidebar-item");
      if (!row) return;
      // Only allow reorder within the same watchlist
      if (parseInt(row.dataset.wlId, 10) !== dragWlId) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";

      // Visual feedback
      var items = container.querySelectorAll(".sidebar-item.drag-over");
      for (var i = 0; i < items.length; i++) {
        items[i].classList.remove("drag-over");
      }
      row.classList.add("drag-over");
    });

    container.addEventListener("dragleave", function (e) {
      var row = e.target.closest(".sidebar-item");
      if (row) row.classList.remove("drag-over");
    });

    container.addEventListener("drop", function (e) {
      e.preventDefault();
      var targetRow = e.target.closest(".sidebar-item");
      if (!targetRow) return;

      var targetWlId = parseInt(targetRow.dataset.wlId, 10);
      if (targetWlId !== dragWlId) return;

      var targetSymbol = targetRow.dataset.symbol;
      if (targetSymbol === dragSymbol) return;

      // Collect current order for this watchlist
      var wl = null;
      for (var i = 0; i < watchlistsData.length; i++) {
        if (watchlistsData[i].id === dragWlId) {
          wl = watchlistsData[i];
          break;
        }
      }
      if (!wl) return;

      var symbols = [];
      for (var j = 0; j < wl.items.length; j++) {
        symbols.push(wl.items[j].symbol);
      }

      // Remove dragged symbol from its current position
      var fromIdx = symbols.indexOf(dragSymbol);
      if (fromIdx === -1) return;
      symbols.splice(fromIdx, 1);

      // Insert before the target
      var toIdx = symbols.indexOf(targetSymbol);
      if (toIdx === -1) return;
      symbols.splice(toIdx, 0, dragSymbol);

      // Update locally for instant feedback
      wl.items = symbols.map(function (s, idx) { return { symbol: s, position: idx }; });

      // Clear drag-over styles
      var overItems = container.querySelectorAll(".sidebar-item.drag-over");
      for (var k = 0; k < overItems.length; k++) {
        overItems[k].classList.remove("drag-over");
      }

      renderDesktopSidebar();
      renderMobileSheet();

      // Persist to API
      reorderItems(dragWlId, symbols);
    });
  }

  // ─── Mobile bottom sheet ────────────────────────────────────────────

  function renderMobileSheet() {
    var sheet = document.getElementById("mobile-watchlist-sheet");
    var btn = document.getElementById("mobile-watchlist-btn");
    if (!sheet || !btn) return;

    if (!window.bradanUser) {
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
          '<span class="sidebar-title">Widgets</span>' +
          '<button class="mobile-sheet-close" id="mobile-sheet-close">&times;</button>' +
        '</div>' +
        '<div class="sidebar-items">' + buildWidgetsArea() + '</div>' +
        '<a href="/watchlist.html" class="sidebar-manage">Manage watchlists</a>' +
      '</div>';

    // Wire open/close
    btn.onclick = function () { openSheet(); };
    document.getElementById("mobile-sheet-overlay").onclick = function () { closeSheet(); };
    document.getElementById("mobile-sheet-close").onclick = function () { closeSheet(); };

    // Wire events inside the sheet content
    var content = sheet.querySelector(".mobile-sheet-content");
    if (content) {
      content.addEventListener("click", handleSidebarClick);
      content.addEventListener("keydown", handleSidebarKeydown);
      wireDragAndDrop(content);
    }
  }

  function openSheet() {
    var sheet = document.getElementById("mobile-watchlist-sheet");
    if (sheet) {
      sheetOpen = true;
      sheet.classList.remove("hidden");
      setTimeout(function () { sheet.classList.add("open"); }, 10);
    }
  }

  function closeSheet() {
    var sheet = document.getElementById("mobile-watchlist-sheet");
    if (sheet) {
      sheetOpen = false;
      sheet.classList.remove("open");
      setTimeout(function () { sheet.classList.add("hidden"); }, 300);
    }
  }

  // ─── Data loading ───────────────────────────────────────────────────

  async function loadData() {
    // Build price cache from snapshot first
    buildPriceCacheFromSnapshot();

    // Fetch all watchlists with items
    watchlistsData = await fetchWatchlists();

    // Fetch missing prices for each watchlist
    var pricePromises = [];
    for (var i = 0; i < watchlistsData.length; i++) {
      var wl = watchlistsData[i];
      var symbols = [];
      for (var j = 0; j < wl.items.length; j++) {
        symbols.push(wl.items[j].symbol);
      }
      if (symbols.length > 0) {
        pricePromises.push(fetchMissingPrices(wl.id, symbols));
      }
    }
    await Promise.allSettled(pricePromises);

    renderDesktopSidebar();
    renderMobileSheet();
  }

  // ─── Auth state handler ─────────────────────────────────────────────

  function onAuthReady(user) {
    if (user) {
      loadData();
    } else {
      watchlistsData = [];
      priceCache = {};
      renderDesktopSidebar();
      renderMobileSheet();
    }
  }

  // ─── Init ───────────────────────────────────────────────────────────

  window.addEventListener("bradan-auth-ready", function (e) {
    onAuthReady(e.detail);
  });

  // If auth already resolved before this script loaded
  if (window.bradanUser) {
    onAuthReady(window.bradanUser);
  }
})();
