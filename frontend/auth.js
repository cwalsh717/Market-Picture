/* Bradan — Auth UI (login/register modal + nav state) */

(function () {
  var currentUser = null;

  /* ── Check login state on load ──────────────────────────────────────── */

  async function checkAuth() {
    try {
      var resp = await fetch("/api/auth/me", { credentials: "same-origin" });
      if (resp.ok) {
        currentUser = await resp.json();
      }
    } catch (e) {
      // not logged in
    }
    renderNavAuth();
  }

  /* ── Nav auth area ──────────────────────────────────────────────────── */

  function renderNavAuth() {
    // Insert auth controls into nav
    var navInner = document.querySelector(".nav-inner");
    if (!navInner) return;

    // Remove existing auth element if present
    var existing = document.getElementById("nav-auth");
    if (existing) existing.remove();

    var el = document.createElement("div");
    el.id = "nav-auth";
    el.className = "nav-auth";

    if (currentUser) {
      el.innerHTML =
        '<span class="auth-email">' + escapeHtml(currentUser.email) + "</span>" +
        '<button class="auth-trigger auth-trigger-sm" id="auth-signout-btn">Sign out</button>';
      navInner.appendChild(el);
      document.getElementById("auth-signout-btn").addEventListener("click", handleSignOut);
    } else {
      el.innerHTML =
        '<button class="auth-trigger" id="auth-signin-btn">Sign in</button>';
      navInner.appendChild(el);
      document.getElementById("auth-signin-btn").addEventListener("click", function () {
        openModal("login");
      });
    }
  }

  function escapeHtml(str) {
    var div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  /* ── Sign out ───────────────────────────────────────────────────────── */

  async function handleSignOut() {
    try {
      await fetch("/api/auth/logout", {
        method: "POST",
        credentials: "same-origin",
      });
    } catch (e) {
      // ignore
    }
    currentUser = null;
    renderNavAuth();
  }

  /* ── Modal ──────────────────────────────────────────────────────────── */

  var modalMode = "login"; // "login" or "register"

  function openModal(mode) {
    modalMode = mode || "login";
    // Remove any existing modal
    closeModal();

    var overlay = document.createElement("div");
    overlay.id = "auth-overlay";
    overlay.className = "auth-overlay";
    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) closeModal();
    });

    var modal = document.createElement("div");
    modal.className = "auth-modal";
    modal.innerHTML = buildModalHTML();
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    wireModal(modal);
    // Focus email field
    var emailInput = modal.querySelector("#auth-email");
    if (emailInput) emailInput.focus();
  }

  function closeModal() {
    var overlay = document.getElementById("auth-overlay");
    if (overlay) overlay.remove();
  }

  function buildModalHTML() {
    var isLogin = modalMode === "login";
    var title = isLogin ? "Sign in" : "Create account";
    var submitLabel = isLogin ? "Sign in" : "Create account";
    var switchText = isLogin
      ? 'Don\'t have an account? <button type="button" class="auth-switch" id="auth-switch-btn">Create one</button>'
      : 'Already have an account? <button type="button" class="auth-switch" id="auth-switch-btn">Sign in</button>';

    return (
      '<h2 class="auth-title">' + title + "</h2>" +
      '<form id="auth-form" autocomplete="on">' +
      '<div class="auth-field">' +
      '<label for="auth-email">Email</label>' +
      '<input id="auth-email" type="email" autocomplete="email" required />' +
      "</div>" +
      '<div class="auth-field">' +
      '<label for="auth-password">Password</label>' +
      '<input id="auth-password" type="password" autocomplete="' +
      (isLogin ? "current-password" : "new-password") +
      '" required minlength="8" />' +
      "</div>" +
      '<div id="auth-error" class="auth-error hidden"></div>' +
      '<button type="submit" class="auth-submit" id="auth-submit-btn">' +
      submitLabel +
      "</button>" +
      "</form>" +
      '<div class="auth-footer">' + switchText + "</div>"
    );
  }

  function wireModal(modal) {
    var form = modal.querySelector("#auth-form");
    var switchBtn = modal.querySelector("#auth-switch-btn");

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      handleSubmit(modal);
    });

    switchBtn.addEventListener("click", function () {
      var newMode = modalMode === "login" ? "register" : "login";
      openModal(newMode);
    });
  }

  async function handleSubmit(modal) {
    var email = modal.querySelector("#auth-email").value.trim();
    var password = modal.querySelector("#auth-password").value;
    var errorEl = modal.querySelector("#auth-error");
    var submitBtn = modal.querySelector("#auth-submit-btn");

    errorEl.classList.add("hidden");
    errorEl.textContent = "";
    submitBtn.disabled = true;
    submitBtn.textContent = modalMode === "login" ? "Signing in..." : "Creating account...";

    var endpoint = modalMode === "login" ? "/api/auth/login" : "/api/auth/register";

    try {
      var resp = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ email: email, password: password }),
      });

      if (!resp.ok) {
        var data = await resp.json().catch(function () {
          return { detail: "Something went wrong" };
        });
        throw new Error(data.detail || "Request failed");
      }

      var user = await resp.json();
      currentUser = { id: user.id, email: user.email };
      closeModal();
      renderNavAuth();
    } catch (err) {
      errorEl.textContent = err.message;
      errorEl.classList.remove("hidden");
      submitBtn.disabled = false;
      submitBtn.textContent = modalMode === "login" ? "Sign in" : "Create account";
    }
  }

  /* ── Keyboard shortcut ──────────────────────────────────────────────── */

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeModal();
  });

  /* ── Init ────────────────────────────────────────────────────────────── */

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", checkAuth);
  } else {
    checkAuth();
  }
})();
