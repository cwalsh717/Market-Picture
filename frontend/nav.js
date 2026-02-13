/* Market Picture — Shared navigation bar */

(function () {
  const API_BASE = "";

  function debounce(fn, ms) {
    let timer;
    return function (...args) {
      clearTimeout(timer);
      timer = setTimeout(() => fn.apply(this, args), ms);
    };
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  function getActivePage() {
    const path = window.location.pathname;
    if (path === "/" || path === "/index.html") return "dashboard";
    if (path === "/chart.html") return "chart";
    if (path === "/journal.html") return "journal";
    if (path === "/about.html") return "about";
    return "";
  }

  function buildNav() {
    const container = document.getElementById("main-nav");
    if (!container) return;

    const active = getActivePage();

    function linkClass(page) {
      return page === active
        ? "nav-link nav-link-active"
        : "nav-link";
    }

    container.innerHTML = `
      <div class="nav-inner">
        <div class="nav-left">
          <a href="/" class="nav-logo">Market Picture</a>
          <div class="nav-links">
            <a href="/" class="${linkClass("dashboard")}">Dashboard</a>
            <a href="/chart.html" class="${linkClass("chart")}">Chart</a>
            <a href="/journal.html" class="${linkClass("journal")}">Journal</a>
            <a href="/about.html" class="${linkClass("about")}">About</a>
          </div>
        </div>
        <div class="nav-search">
          <input
            id="nav-search-input"
            type="text"
            placeholder="Search ticker\u2026"
            autocomplete="off"
            spellcheck="false"
          />
          <div id="nav-search-spinner" class="nav-search-spinner hidden">
            <svg class="animate-spin" width="16" height="16" viewBox="0 0 24 24" fill="none">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
            </svg>
          </div>
          <div id="nav-search-dropdown" class="nav-search-dropdown hidden"></div>
        </div>
      </div>
    `;

    wireSearch();
  }

  /* ── Dropdown helpers ─────────────────────────────────────────────────── */

  function hideDropdown() {
    var dd = document.getElementById("nav-search-dropdown");
    if (dd) {
      dd.classList.add("hidden");
      dd.innerHTML = "";
    }
  }

  function showDropdownItem(symbol) {
    var dd = document.getElementById("nav-search-dropdown");
    if (!dd) return;

    dd.innerHTML =
      '<div class="nav-search-dropdown-item" data-symbol="' +
      escapeHtml(symbol) +
      '">' +
      '<span style="font-weight:600;">' + escapeHtml(symbol) + '</span>' +
      '<span style="color:#6b7280;">Go to chart</span>' +
      '</div>';
    dd.classList.remove("hidden");

    dd.querySelector(".nav-search-dropdown-item").addEventListener("click", function () {
      hideDropdown();
      window.location.href = "/chart.html?symbol=" + encodeURIComponent(symbol);
    });
  }

  function showDropdownEmpty(query) {
    var dd = document.getElementById("nav-search-dropdown");
    if (!dd) return;

    dd.innerHTML =
      '<div class="nav-search-dropdown-empty">No results for "' +
      escapeHtml(query) +
      '"</div>';
    dd.classList.remove("hidden");
  }

  function showDropdownError() {
    var dd = document.getElementById("nav-search-dropdown");
    if (!dd) return;

    dd.innerHTML =
      '<div class="nav-search-dropdown-empty">Search unavailable</div>';
    dd.classList.remove("hidden");
  }

  /* ── Preview (dropdown only, no navigation) ───────────────────────────── */

  async function showPreview(query) {
    var trimmed = query.trim().toUpperCase();
    var spinner = document.getElementById("nav-search-spinner");

    if (!trimmed) {
      hideDropdown();
      return;
    }

    if (spinner) spinner.classList.remove("hidden");

    try {
      var resp = await fetch(
        API_BASE + "/api/search/" + encodeURIComponent(trimmed)
      );
      if (resp.status === 404) {
        showDropdownEmpty(trimmed);
        return;
      }
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      var data = await resp.json();
      showDropdownItem(data.symbol);
    } catch (err) {
      console.error("Nav search preview failed:", err);
      showDropdownError();
    } finally {
      if (spinner) spinner.classList.add("hidden");
    }
  }

  /* ── Navigate (Enter key or direct action) ────────────────────────────── */

  async function navigateToSymbol(query) {
    var trimmed = query.trim().toUpperCase();
    var spinner = document.getElementById("nav-search-spinner");

    if (!trimmed) return;

    if (spinner) spinner.classList.remove("hidden");

    try {
      var resp = await fetch(
        API_BASE + "/api/search/" + encodeURIComponent(trimmed)
      );
      if (resp.status === 404) {
        showDropdownEmpty(trimmed);
        return;
      }
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      var data = await resp.json();
      window.location.href = "/chart.html?symbol=" + encodeURIComponent(data.symbol);
    } catch (err) {
      console.error("Nav search failed:", err);
      showDropdownError();
    } finally {
      if (spinner) spinner.classList.add("hidden");
    }
  }

  /* ── Wire up search input ─────────────────────────────────────────────── */

  function wireSearch() {
    var input = document.getElementById("nav-search-input");
    if (!input) return;

    var debouncedPreview = debounce(function (val) { showPreview(val); }, 400);

    input.addEventListener("input", function (e) {
      var val = e.target.value.trim();
      if (!val) {
        hideDropdown();
        return;
      }
      debouncedPreview(val);
    });

    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        hideDropdown();
        navigateToSymbol(input.value.trim());
      }
      if (e.key === "Escape") {
        hideDropdown();
      }
    });

    // Close dropdown on outside click
    document.addEventListener("click", function (e) {
      if (!e.target.closest(".nav-search")) {
        hideDropdown();
      }
    });
  }

  // Build nav as soon as DOM is ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", buildNav);
  } else {
    buildNav();
  }
})();
