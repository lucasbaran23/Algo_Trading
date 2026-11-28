/* Prop Copilot: firm rules, account limits, today's risk plan (deterministic) and an AI chat over risk tools. */
"use strict";

const COP = { st: null, plan: null, syms: ["NG", "CL"], chat: [], busy: false };
const COP_SUGGEST = ["Build my risk plan for today", "Can I take 3 NG with a 25-tick stop?",
  "What are my odds of passing at 55% wins, +$300 / -$200, 3 trades a day?", "What should I avoid trading today and why?",
  "Explain my firm's drawdown rule and how close I am"];

function copMd(t) {        // minimal, safe markdown: escape first, then bold / bullets / line breaks
  return esc(t).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").split(/\n/).map((l) => /^\s*[-•*]\s+/.test(l) ? `<li>${l.replace(/^\s*[-•*]\s+/, "")}</li>` : l ? `<p>${l}</p>` : "").join("")
    .replace(/(<li>.*?<\/li>)+/gs, (m) => `<ul class="dt-list">${m}</ul>`);
}

function copAccount() {
  const a = COP.st.account, r = a.rules;
  const firms = COP.st.firms;
  return `<div class="box"><div class="sec">ACCOUNT <span class="muted">${esc(r.name)} · the source of truth for every number</span></div>
    ${a.breached ? `<div class="notice danger">BREACHED: ${esc(a.breached)}</div>` : ""}
    <div class="tiles">${tile("Equity", money(a.equity), cls(a.total_pnl))}${tile("Drawdown buffer", money(a.distance_to_breach), a.distance_to_breach < 0.3 * r.max_drawdown ? "down" : "")}
      ${tile("Daily loss left", a.daily_loss_remaining == null ? "no daily limit" : money(a.daily_loss_remaining))}${tile("Target", r.profit_target ? `${fx(a.target_progress_pct, 0)}%` : "funded")}
      ${tile("Max contracts", r.max_contracts)}${tile("Drawdown type", r.drawdown_mode.replace("_", " "))}</div>
    <div class="sec">LOAD A FIRM TEMPLATE</div>
    <div class="gs-ctl"><label>Firm <select id="copFirm">${Object.entries(firms).map(([k, f]) => `<option value="${k}">${esc(f.name)}</option>`).join("")}</select></label>
      <label>Plan <select id="copPlan"></select></label><button class="tbtn" data-cop="firm">APPLY</button></div>
    <div class="muted">Templates are unverified copies of commonly published rules: check them on the firm's site, or set exact numbers on the Prop desk (F6). Applying resets the paper account.</div></div>`;
}

function copPlan() {
  const p = COP.plan;
  if (!p) return "";
  return `<div class="box"><div class="sec">TODAY'S RISK PLAN <span class="muted">${esc(p.rules_name)} · computed from your account and today's volatility (no AI)</span></div>
    <div class="gs-ctl"><label>Markets <input id="copSyms" value="${COP.syms.join(",")}" style="width:140px"></label><button class="tbtn" data-cop="plan">UPDATE</button>
      <span class="muted">Day loss budget: <b>${money(p.day_loss_budget_usd)}</b></span></div>
    <table class="tbl cop-plan"><thead><tr><th>MARKET</th><th>REGIME</th><th>BIAS</th><th>STOP</th><th>RISK / TRADE</th><th>CONTRACTS</th><th>MICROS</th></tr></thead><tbody>
    ${p.markets.map((m) => m.error ? `<tr><td class="sym">${m.symbol}</td><td colspan="6" class="down">${esc(m.error)}</td></tr>` :
      `<tr><td class="sym">${m.symbol}</td><td>${esc(m.regime || "—")}</td><td>${esc(m.bias || "—")}</td><td>${m.stop_ticks} ticks (${money(m.stop_usd_per_contract)})</td>
       <td>${money(m.risk_per_trade_usd)}</td><td><b>${m.contracts}</b></td><td>${m.micro_contracts ?? "—"}</td>
       </tr><tr class="cop-why"><td colspan="7" class="muted">${esc((m.why_size || []).join(" · "))}${m.events_today?.length ? ` · Events: ${esc(m.events_today.map((e) => `${e.time || ""} ${e.name}`).join(", "))}` : ""}</td></tr>`).join("")}
    </tbody></table>
    <ul class="dt-list">${p.day_rules.map((r) => `<li>${esc(r)}</li>`).join("")}</ul></div>`;
}

function copChat() {
  const q = COP.st.quota;
  return `<div class="box cop-chat"><div class="sec">ASK THE COPILOT <span class="muted">${COP.st.ai_configured ? esc(COP.st.model) : "AI not configured on this server"}${q.limit != null ? ` · ${q.remaining} of ${q.limit} questions left this month` : ""}</span></div>
    <div class="cop-msgs" id="copMsgs">${COP.chat.length ? COP.chat.map((m) => `<div class="cop-msg ${m.role}">${m.role === "user" ? esc(m.text) : copMd(m.text)}
        ${m.meta ? `<div class="muted cop-meta">${esc(m.meta)}</div>` : ""}</div>`).join("") :
      `<div class="muted">Ask about your rules, today's plan, a trade you're considering, or your odds of passing. Every number comes from the terminal's risk tools.</div>`}
      ${COP.busy ? '<div class="cop-msg assistant muted">Checking your account and the market…</div>' : ""}</div>
    <div class="dt-tdrow">${COP_SUGGEST.map((s) => `<button class="tbtn" data-cop="ask" data-q="${esc(s)}">${esc(s)}</button>`).join("")}</div>
    <form id="copForm" class="cop-form"><input id="copInput" placeholder="e.g. I'm down $600 today on Topstep 50K. Should I keep trading NG?" maxlength="2000" ${COP.busy ? "disabled" : ""}>
      <button class="tbtn on" type="submit" ${COP.busy ? "disabled" : ""}>ASK</button><button class="tbtn" type="button" data-cop="reset">NEW CHAT</button></form>
    <div class="muted">Educational risk analysis based on your settings; not investment advice. You place every order yourself.</div></div>`;
}

function copRender() {
  const v = $("#view");
  if (!v || S.view !== "COP" || !COP.st) return;
  v.innerHTML = `<div class="dt"><div class="dt-row dt-r2b"><div class="cop-left">${copAccount()}${copPlan()}</div>${copChat()}</div></div>`;
  const firm = $("#copFirm");
  const fillPlans = () => { $("#copPlan").innerHTML = COP.st.firms[firm.value].plans.map((p) => `<option>${p}</option>`).join(""); };
  firm.onchange = fillPlans;
  fillPlans();
  const m = $("#copMsgs");
  if (m) m.scrollTop = m.scrollHeight;
}

async function copLoad() {
  [COP.st, COP.plan] = await Promise.all([api("/api/copilot/state"), api(`/api/copilot/plan?symbols=${COP.syms.join(",")}`)]);
}

async function copAsk(q) {
  if (!q || COP.busy) return;
  COP.chat.push({ role: "user", text: q });
  COP.busy = true; copRender();
  try {
    const r = await api("/api/copilot/chat", { method: "POST", body: { message: q } });
    COP.chat.push({ role: "assistant", text: r.reply, meta: `tools: ${r.tools_used.join(", ") || "none"} · ${r.seconds}s` });
    COP.st.quota = r.quota;
    if (r.tools_used.some((t) => t !== "firm_rules" && t !== "market_brief")) COP.st.account = (await api("/api/copilot/state")).account;
  } catch (e) {
    COP.chat.push({ role: "assistant", text: e.message || "Something went wrong." });
  }
  COP.busy = false; copRender();
}

VIEWS.COP = async function (v) {
  setTitle("PROP COPILOT · FIRM RULES · TODAY'S RISK PLAN · AI RISK ASSISTANT");
  tools("");
  v.innerHTML = '<div class="pad muted">Loading your account…</div>';
  if (S.sel && !COP.syms.includes(S.universe[S.sel]?.parent || S.sel)) COP.syms = [S.universe[S.sel]?.parent || S.sel, ...COP.syms].slice(0, 3);
  await copLoad();
  copRender();
};

document.addEventListener("click", async (ev) => {
  const b = ev.target.closest("[data-cop]");
  if (!b || S.view !== "COP") return;
  const a = b.dataset.cop;
  if (a === "ask") return copAsk(b.dataset.q);
  if (a === "reset") { await api("/api/copilot/reset", { method: "POST" }); COP.chat = []; return copRender(); }
  if (a === "plan") { COP.syms = $("#copSyms").value.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean).slice(0, 4); await copLoad(); return copRender(); }
  if (a === "firm") {
    const r = await api("/api/copilot/firm", { method: "POST", body: { firm: $("#copFirm").value, plan: $("#copPlan").value } });
    toast("RULES LOADED", `${r.rules.name}${r.verified ? "" : " (unverified template: check the firm's site)"}`, r.verified ? "INFO" : "WARN");
    await copLoad(); return copRender();
  }
});
document.addEventListener("submit", (ev) => {
  if (ev.target.id !== "copForm") return;
  ev.preventDefault();
  const q = $("#copInput").value.trim();
  $("#copInput").value = "";
  copAsk(q);
});
