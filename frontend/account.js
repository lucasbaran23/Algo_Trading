/* Accounts & plans: sign-in, tier badge, locked tabs, upgrade prompts, plans page.
   In local mode (AUTH_MODE=off) everything is unlocked and this stays out of the way. */
"use strict";

const ACC = { me: null, plans: null, origViews: null };
const TIER_LABEL = { free: "FREE", pro: "PRO", premium: "PREMIUM", ultimate: "ULTIMATE" };

async function accLoad() {
  try {
    [ACC.me, ACC.plans] = await Promise.all([fetch("/api/auth/me").then((r) => r.json()), fetch("/api/plans").then((r) => r.json())]);
  } catch { ACC.me = ACC.me || null; }
  accHeader();
  accApplyLocks();
}

function accHeader() {
  let el = $("#acctArea");
  if (!el) {
    el = document.createElement("div");
    el.id = "acctArea";
    el.className = "acct";
    document.querySelector(".topbar .status").prepend(el);
  }
  const m = ACC.me;
  if (!m || m.auth_mode !== "on") { el.innerHTML = ""; return; }
  el.innerHTML = m.user
    ? `<button class="tbtn" data-acc="plans" title="Plans and billing"><span class="tierchip t-${m.tier}">${TIER_LABEL[m.tier]}</span> ${esc(m.user.email)}</button>
       <button class="tbtn" data-acc="logout">SIGN OUT</button>`
    : `<button class="tbtn" data-acc="plans">PLANS</button><button class="tbtn on" data-acc="signin">SIGN IN</button>`;
}

function accPropPanel() {
  const m = ACC.me;
  if (!m || m.auth_mode !== "on") return;
  const can = m.user && (m.view_tiers.PROP ? !m.locked_views.includes("PROP") : true);
  if (!can) {
    $("#propName").textContent = "";
    $("#propMini").innerHTML = `<div class="muted">Track a prop evaluation live: drawdown buffer, daily loss, consistency and risk sizing.</div>
      <div class="row-actions">${m.user ? '<button class="tbtn on" data-acc="upgrade" data-tier="pro">UPGRADE TO PRO</button>' : '<button class="tbtn on" data-acc="signin">SIGN IN</button>'}</div>`;
  }
}

function accApplyLocks() {
  accPropPanel();
  if (!ACC.origViews) ACC.origViews = { ...VIEWS };
  const locked = new Set(ACC.me?.locked_views || []);
  const need = ACC.me?.view_tiers || {};
  for (const [v, fn] of Object.entries(ACC.origViews)) {
    VIEWS[v] = locked.has(v) ? (el) => accLockedView(el, need[v]) : fn;
  }
  document.querySelectorAll("#fkeys button[data-view]").forEach((b) => {
    const v = b.dataset.view, isLocked = locked.has(v);
    b.classList.toggle("locked", isLocked);
    b.querySelector(".lockchip")?.remove();
    if (isLocked) b.insertAdjacentHTML("beforeend", `<span class="lockchip t-${need[v]}">${TIER_LABEL[need[v]]}</span>`);
  });
}

function accLockedView(el, tier) {
  setTitle(`${TIER_LABEL[tier]} FEATURE`);
  tools("");
  const plan = ACC.plans?.plans?.[tier];
  el.innerHTML = `<div class="pad"><div class="box lockbox">
    <div class="sec">THIS SCREEN IS PART OF ${TIER_LABEL[tier]}</div>
    ${plan ? `<p>${esc(plan.tagline)}</p><ul class="dt-list">${plan.features.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>` : ""}
    <div class="row-actions">${ACC.me?.user ? `<button class="tbtn on" data-acc="upgrade" data-tier="${tier}">UPGRADE TO ${TIER_LABEL[tier]}</button>` :
      `<button class="tbtn on" data-acc="signin">SIGN IN</button>`}<button class="tbtn" data-acc="plans">COMPARE PLANS</button></div></div></div>`;
  if (!ACC.plans) fetch("/api/plans").then((r) => r.json()).then((p) => { ACC.plans = p; if (S.view && VIEWS[S.view]) renderView(); });
}

// ---------------------------------------------------------------- sign-in modal
function accModal(mode = "signin", msg = "") {
  $("#accModal")?.remove();
  document.body.insertAdjacentHTML("beforeend", `<div id="accModal" class="modal-bg"><form class="modal box" id="accForm">
    <div class="sec">${mode === "signin" ? "SIGN IN" : "CREATE YOUR ACCOUNT"}</div>
    ${msg ? `<div class="notice">${esc(msg)}</div>` : ""}
    <label>Email <input name="email" type="email" required autocomplete="email"></label>
    <label>Password <input name="password" type="password" required minlength="8" autocomplete="${mode === "signin" ? "current-password" : "new-password"}"></label>
    <div class="muted" id="accErr"></div>
    <div class="row-actions"><button class="tbtn on" type="submit">${mode === "signin" ? "SIGN IN" : "CREATE ACCOUNT"}</button>
      <button class="tbtn" type="button" data-acc="${mode === "signin" ? "signup" : "signin"}">${mode === "signin" ? "NEW HERE? CREATE AN ACCOUNT" : "I HAVE AN ACCOUNT"}</button>
      <button class="tbtn" type="button" data-acc="close">CANCEL</button></div>
    <div class="muted">Free accounts get the Nat Gas Desk, COT, storage, supply &amp; demand and news. Not investment advice.</div>
  </form></div>`);
  $("#accForm").dataset.mode = mode;
  $("#accForm input[name=email]").focus();
}

document.addEventListener("submit", async (ev) => {
  if (ev.target.id !== "accForm") return;
  ev.preventDefault();
  const f = ev.target, body = { email: f.email.value, password: f.password.value };
  const r = await fetch(`/api/auth/${f.dataset.mode === "signin" ? "login" : "signup"}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) { $("#accErr").textContent = d.detail?.message || d.detail || "Something went wrong."; $("#accErr").className = "down"; return; }
  $("#accModal")?.remove();
  await accLoad();
  renderView();
});

// ---------------------------------------------------------------- plans page
VIEWS.PLANS = async function (v) {
  setTitle("PLANS & BILLING");
  tools("");
  v.innerHTML = '<div class="pad muted">Loading plans…</div>';
  ACC.plans = await fetch("/api/plans").then((r) => r.json());
  const p = ACC.plans, cur = ACC.me?.tier || "free", hosted = ACC.me?.auth_mode === "on";
  v.innerHTML = `<div class="dt">
    <div class="plans">${p.order.map((t) => { const pl = p.plans[t]; const here = t === cur;
      return `<div class="box plan ${here ? "current" : ""}"><div class="sec">${esc(pl.name.toUpperCase())}</div>
        <div class="price">${pl.price_month ? `$${pl.price_month}<small>/month</small>` : "Free"}</div>
        <div class="muted">${esc(pl.tagline)}</div>
        <ul class="dt-list">${pl.features.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>
        <div class="row-actions">${here ? '<span class="badge live">YOUR PLAN</span>' :
          !hosted ? "" : !ACC.me?.user ? '<button class="tbtn on" data-acc="signup">CREATE ACCOUNT</button>' :
          pl.price_month ? `<button class="tbtn on" data-acc="upgrade" data-tier="${t}">${p.order.indexOf(t) > p.order.indexOf(cur) ? "UPGRADE" : "SWITCH"}</button>` : ""}</div></div>`; }).join("")}</div>
    ${hosted && ACC.me?.user && cur !== "free" ? '<div class="row-actions"><button class="tbtn" data-acc="portal">MANAGE BILLING / CANCEL</button></div>' : ""}
    <div class="box muted">${hosted ? (p.billing ? "Secure checkout by Stripe. Cancel any time from Manage billing." : "Payments are not switched on yet.") :
      "Local mode: everything is unlocked on your own computer. Plans apply to the hosted version."}
      Prices in USD. The terminal is a research and risk tool, not investment advice; futures trading carries substantial risk of loss.</div></div>`;
};

document.addEventListener("click", async (ev) => {
  const b = ev.target.closest("[data-acc]");
  if (!b) return;
  const a = b.dataset.acc;
  if (a === "signin" || a === "signup") return accModal(a);
  if (a === "close") return $("#accModal")?.remove();
  if (a === "plans") { S.view = "PLANS"; return renderView(); }
  if (a === "logout") { await fetch("/api/auth/logout", { method: "POST" }); await accLoad(); return renderView(); }
  if (a === "upgrade" || a === "portal") {
    const url = a === "upgrade" ? `/api/billing/checkout?tier=${b.dataset.tier}` : "/api/billing/portal";
    const r = await fetch(url, { method: "POST" });
    const d = await r.json().catch(() => ({}));
    if (r.ok && d.url) { location.href = d.url; return; }
    if (r.status === 401) return accModal("signin", "Sign in first.");
    alert(d.detail?.message || d.detail || "Billing is not available right now.");
  }
});

// Any API call that hits a locked feature or needs sign-in opens the right prompt.
const _apiPlain = api;
api = async function (path, opts) {          // eslint-disable-line no-global-assign
  try {
    return await _apiPlain(path, opts);
  } catch (e) {
    const d = e.data?.detail;                // background calls never pop a window; locked screens show their own prompt
    if ((d?.signin || d?.upgrade) && ACC.me?.auth_mode === "on") accLoad();
    throw e;
  }
};

if (new URLSearchParams(location.search).get("billing") === "success") {
  setTimeout(() => alert("Payment received. Your plan updates within a few seconds; refresh if a screen still shows locked."), 500);
  history.replaceState(null, "", "/");
}
accLoad();
