/* Bradan — Auth UI (login/register modal, user menu, account settings) */

(function () {
  var currentUser = null;

  /* ── SVG constants ──────────────────────────────────────────────────── */

  var EYE_OPEN_SVG =
    '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>';

  var EYE_CLOSED_SVG =
    '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path><line x1="1" y1="1" x2="23" y2="23"></line></svg>';

  var CHEVRON_SVG =
    '<svg class="user-menu-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"></polyline></svg>';

  /* ── Helpers ─────────────────────────────────────────────────────────── */

  function escapeHtml(str) {
    var div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  function avatarLetter(email) {
    return (email && email.charAt(0) || "?").toUpperCase();
  }

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
    // Prefer the auth slot inside nav-collapse; fall back to nav-inner
    var authSlot = document.getElementById("nav-auth-slot") || document.querySelector(".nav-collapse") || document.querySelector(".nav-inner");
    if (!authSlot) return;

    // Remove existing auth element if present
    var existing = document.getElementById("nav-auth");
    if (existing) existing.remove();

    var el = document.createElement("div");
    el.id = "nav-auth";
    el.className = "nav-auth";

    if (currentUser) {
      el.innerHTML =
        '<div class="user-menu-wrapper">' +
          '<button class="user-menu-trigger" id="user-menu-btn">' +
            '<span class="user-avatar">' + escapeHtml(avatarLetter(currentUser.email)) + '</span>' +
            '<span class="user-menu-email">' + escapeHtml(currentUser.email) + '</span>' +
            CHEVRON_SVG +
          '</button>' +
          '<div class="user-menu-dropdown hidden" id="user-menu-dropdown">' +
            '<div class="user-menu-header">' +
              '<span class="user-menu-email-full">' + escapeHtml(currentUser.email) + '</span>' +
            '</div>' +
            '<div class="user-menu-divider"></div>' +
            '<button class="user-menu-item" id="user-menu-account">Account settings</button>' +
            '<button class="user-menu-item user-menu-signout" id="user-menu-signout">Sign out</button>' +
          '</div>' +
        '</div>';
      authSlot.appendChild(el);

      // Wire user menu events
      var menuBtn = document.getElementById("user-menu-btn");
      var dropdown = document.getElementById("user-menu-dropdown");

      menuBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        dropdown.classList.toggle("hidden");
      });

      document.getElementById("user-menu-account").addEventListener("click", function () {
        dropdown.classList.add("hidden");
        openAccountModal();
      });

      document.getElementById("user-menu-signout").addEventListener("click", function () {
        dropdown.classList.add("hidden");
        handleSignOut();
      });

      // Close dropdown on outside click
      document.addEventListener("click", function (e) {
        if (!e.target.closest(".user-menu-wrapper")) {
          dropdown.classList.add("hidden");
        }
      });
    } else {
      el.innerHTML =
        '<button class="auth-trigger" id="auth-signin-btn">Sign in</button>';
      authSlot.appendChild(el);
      document.getElementById("auth-signin-btn").addEventListener("click", function () {
        openModal("login");
      });
    }
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

  /* ── Auth Modal (login / register) ──────────────────────────────────── */

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

    var passwordHint = isLogin
      ? ""
      : '<div class="auth-hint">Minimum 8 characters</div>';

    return (
      '<button class="auth-close" id="auth-close-btn">&times;</button>' +
      '<div class="auth-logo-row"><img src="/static/bradan-logo.jpg" alt="" class="auth-logo-img" onerror="this.style.display=\'none\'">' + title + '</div>' +
      '<form id="auth-form" autocomplete="on">' +
        '<div class="auth-field">' +
          '<label for="auth-email">Email</label>' +
          '<input id="auth-email" type="email" autocomplete="email" required />' +
        '</div>' +
        '<div class="auth-field">' +
          '<label for="auth-password">Password</label>' +
          '<div class="auth-pw-wrapper">' +
            '<input id="auth-password" type="password" autocomplete="' +
            (isLogin ? 'current-password' : 'new-password') +
            '" required minlength="8" />' +
            '<button type="button" class="auth-pw-toggle" id="auth-pw-toggle" tabindex="-1">' +
              EYE_CLOSED_SVG +
            '</button>' +
          '</div>' +
          passwordHint +
        '</div>' +
        '<div id="auth-error" class="auth-error hidden"></div>' +
        '<button type="submit" class="auth-submit" id="auth-submit-btn">' +
          submitLabel +
        '</button>' +
      '</form>' +
      '<div class="auth-footer">' + switchText + '</div>'
    );
  }

  function wireModal(modal) {
    var form = modal.querySelector("#auth-form");
    var switchBtn = modal.querySelector("#auth-switch-btn");
    var closeBtn = modal.querySelector("#auth-close-btn");
    var pwToggle = modal.querySelector("#auth-pw-toggle");
    var pwInput = modal.querySelector("#auth-password");

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      handleSubmit(modal);
    });

    switchBtn.addEventListener("click", function () {
      var newMode = modalMode === "login" ? "register" : "login";
      openModal(newMode);
    });

    closeBtn.addEventListener("click", function () {
      closeModal();
    });

    // Password visibility toggle
    var pwVisible = false;
    pwToggle.addEventListener("click", function () {
      pwVisible = !pwVisible;
      pwInput.type = pwVisible ? "text" : "password";
      pwToggle.innerHTML = pwVisible ? EYE_OPEN_SVG : EYE_CLOSED_SVG;
    });
  }

  async function handleSubmit(modal) {
    var email = modal.querySelector("#auth-email").value.trim();
    var password = modal.querySelector("#auth-password").value;
    var errorEl = modal.querySelector("#auth-error");
    var submitBtn = modal.querySelector("#auth-submit-btn");

    var originalLabel = modalMode === "login" ? "Sign in" : "Create account";
    var loadingLabel = modalMode === "login" ? "Signing in..." : "Creating account...";

    errorEl.classList.add("hidden");
    errorEl.textContent = "";
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span class="auth-spinner"></span>' + loadingLabel;

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
      submitBtn.innerHTML = originalLabel;
    }
  }

  /* ── Account Settings Modal ─────────────────────────────────────────── */

  function openAccountModal() {
    // Remove any existing account modal
    closeAccountModal();

    var overlay = document.createElement("div");
    overlay.id = "account-overlay";
    overlay.className = "auth-overlay";
    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) closeAccountModal();
    });

    var modal = document.createElement("div");
    modal.className = "auth-modal account-modal";
    modal.innerHTML = buildAccountHTML();
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    wireAccountModal(modal);
  }

  function closeAccountModal() {
    var overlay = document.getElementById("account-overlay");
    if (overlay) overlay.remove();
  }

  function buildAccountHTML() {
    var email = currentUser ? escapeHtml(currentUser.email) : "";

    return (
      '<button class="auth-close" id="account-close-btn">&times;</button>' +
      '<div class="auth-logo-row"><img src="/static/bradan-logo.jpg" alt="" class="auth-logo-img" onerror="this.style.display=\'none\'">Account Settings</div>' +

      '<div class="account-section">' +
        '<div class="account-section-title">Change Email</div>' +
        '<div class="account-current-email">Current: ' + email + '</div>' +
        '<div class="auth-field">' +
          '<label for="account-new-email">New email</label>' +
          '<input id="account-new-email" type="email" required>' +
        '</div>' +
        '<div class="auth-field">' +
          '<label for="account-email-password">Confirm password</label>' +
          '<div class="auth-pw-wrapper">' +
            '<input id="account-email-password" type="password" required>' +
            '<button type="button" class="auth-pw-toggle" data-target="account-email-password" tabindex="-1">' +
              EYE_CLOSED_SVG +
            '</button>' +
          '</div>' +
        '</div>' +
        '<div class="account-feedback" id="email-feedback"></div>' +
        '<button class="account-btn" id="account-email-submit">Update Email</button>' +
      '</div>' +

      '<div class="account-section">' +
        '<div class="account-section-title">Change Password</div>' +
        '<div class="auth-field">' +
          '<label for="account-current-pw">Current password</label>' +
          '<div class="auth-pw-wrapper">' +
            '<input id="account-current-pw" type="password" required>' +
            '<button type="button" class="auth-pw-toggle" data-target="account-current-pw" tabindex="-1">' +
              EYE_CLOSED_SVG +
            '</button>' +
          '</div>' +
        '</div>' +
        '<div class="auth-field">' +
          '<label for="account-new-pw">New password</label>' +
          '<div class="auth-pw-wrapper">' +
            '<input id="account-new-pw" type="password" required>' +
            '<button type="button" class="auth-pw-toggle" data-target="account-new-pw" tabindex="-1">' +
              EYE_CLOSED_SVG +
            '</button>' +
          '</div>' +
          '<div class="auth-hint">Minimum 8 characters</div>' +
        '</div>' +
        '<div class="account-feedback" id="password-feedback"></div>' +
        '<button class="account-btn" id="account-pw-submit">Update Password</button>' +
      '</div>'
    );
  }

  function wireAccountModal(modal) {
    // Close button
    modal.querySelector("#account-close-btn").addEventListener("click", function () {
      closeAccountModal();
    });

    // Password visibility toggles (all of them in the account modal)
    var toggles = modal.querySelectorAll(".auth-pw-toggle");
    for (var i = 0; i < toggles.length; i++) {
      (function (toggle) {
        var visible = false;
        var targetId = toggle.getAttribute("data-target");
        var input = modal.querySelector("#" + targetId);
        if (!input) return;
        toggle.addEventListener("click", function () {
          visible = !visible;
          input.type = visible ? "text" : "password";
          toggle.innerHTML = visible ? EYE_OPEN_SVG : EYE_CLOSED_SVG;
        });
      })(toggles[i]);
    }

    // Update Email
    modal.querySelector("#account-email-submit").addEventListener("click", function () {
      handleUpdateEmail(modal);
    });

    // Update Password
    modal.querySelector("#account-pw-submit").addEventListener("click", function () {
      handleUpdatePassword(modal);
    });
  }

  async function handleUpdateEmail(modal) {
    var newEmail = modal.querySelector("#account-new-email").value.trim();
    var password = modal.querySelector("#account-email-password").value;
    var feedback = modal.querySelector("#email-feedback");
    var btn = modal.querySelector("#account-email-submit");

    if (!newEmail || !password) {
      feedback.textContent = "Please fill in all fields";
      feedback.className = "account-feedback error";
      return;
    }

    btn.disabled = true;
    btn.innerHTML = '<span class="auth-spinner"></span>Updating...';
    feedback.textContent = "";
    feedback.className = "account-feedback";

    try {
      var resp = await fetch("/api/auth/change-email", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ new_email: newEmail, password: password }),
      });

      if (!resp.ok) {
        var data = await resp.json().catch(function () {
          return { detail: "Something went wrong" };
        });
        throw new Error(data.detail || "Request failed");
      }

      // Update local state
      currentUser.email = newEmail;
      renderNavAuth();

      // Update the "Current:" display in the modal
      var currentLabel = modal.querySelector(".account-current-email");
      if (currentLabel) currentLabel.textContent = "Current: " + newEmail;

      // Clear inputs and show success
      modal.querySelector("#account-new-email").value = "";
      modal.querySelector("#account-email-password").value = "";
      feedback.textContent = "Email updated successfully";
      feedback.className = "account-feedback success";
    } catch (err) {
      feedback.textContent = err.message;
      feedback.className = "account-feedback error";
    } finally {
      btn.disabled = false;
      btn.innerHTML = "Update Email";
    }
  }

  async function handleUpdatePassword(modal) {
    var currentPw = modal.querySelector("#account-current-pw").value;
    var newPw = modal.querySelector("#account-new-pw").value;
    var feedback = modal.querySelector("#password-feedback");
    var btn = modal.querySelector("#account-pw-submit");

    if (!currentPw || !newPw) {
      feedback.textContent = "Please fill in all fields";
      feedback.className = "account-feedback error";
      return;
    }

    if (newPw.length < 8) {
      feedback.textContent = "New password must be at least 8 characters";
      feedback.className = "account-feedback error";
      return;
    }

    btn.disabled = true;
    btn.innerHTML = '<span class="auth-spinner"></span>Updating...';
    feedback.textContent = "";
    feedback.className = "account-feedback";

    try {
      var resp = await fetch("/api/auth/change-password", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ current_password: currentPw, new_password: newPw }),
      });

      if (!resp.ok) {
        var data = await resp.json().catch(function () {
          return { detail: "Something went wrong" };
        });
        throw new Error(data.detail || "Request failed");
      }

      // Clear inputs and show success
      modal.querySelector("#account-current-pw").value = "";
      modal.querySelector("#account-new-pw").value = "";
      feedback.textContent = "Password updated successfully";
      feedback.className = "account-feedback success";
    } catch (err) {
      feedback.textContent = err.message;
      feedback.className = "account-feedback error";
    } finally {
      btn.disabled = false;
      btn.innerHTML = "Update Password";
    }
  }

  /* ── Keyboard shortcut ──────────────────────────────────────────────── */

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      // Close account modal first if open, then auth modal
      var accountOverlay = document.getElementById("account-overlay");
      if (accountOverlay) {
        closeAccountModal();
        return;
      }
      closeModal();
    }
  });

  /* ── Init ────────────────────────────────────────────────────────────── */

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", checkAuth);
  } else {
    checkAuth();
  }
})();
