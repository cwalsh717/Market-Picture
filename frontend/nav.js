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
          <p id="nav-search-error" class="nav-search-error hidden"></p>
        </div>
      </div>
    `;

    wireSearch();
  }

  function wireSearch() {
    const input = document.getElementById("nav-search-input");
    if (!input) return;

    const debouncedSearch = debounce((val) => handleSearch(val), 400);

    input.addEventListener("input", (e) => debouncedSearch(e.target.value));
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        handleSearch(input.value);
      }
    });
  }

  async function handleSearch(query) {
    const trimmed = query.trim().toUpperCase();
    const errorEl = document.getElementById("nav-search-error");
    const spinner = document.getElementById("nav-search-spinner");

    if (!trimmed) {
      if (errorEl) errorEl.classList.add("hidden");
      return;
    }

    if (spinner) spinner.classList.remove("hidden");
    if (errorEl) errorEl.classList.add("hidden");

    try {
      const resp = await fetch(
        `${API_BASE}/api/search/${encodeURIComponent(trimmed)}`
      );
      if (resp.status === 404) {
        if (errorEl) {
          errorEl.textContent = `No results for "${escapeHtml(trimmed)}"`;
          errorEl.classList.remove("hidden");
        }
        return;
      }
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      window.location.href = `/chart.html?symbol=${encodeURIComponent(data.symbol)}`;
    } catch (err) {
      console.error("Nav search failed:", err);
      if (errorEl) {
        errorEl.textContent = "Search unavailable";
        errorEl.classList.remove("hidden");
      }
    } finally {
      if (spinner) spinner.classList.add("hidden");
    }
  }

  // Build nav as soon as DOM is ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", buildNav);
  } else {
    buildNav();
  }
})();
