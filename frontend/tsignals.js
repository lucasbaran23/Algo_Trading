/* Trade Signals: actionable entries with stop loss / take profit, lifecycle tracking and notifications. */
"use strict";

const TS = {
  open: [], closed: [], stats: null, filter: { symbol: "", horizon: "", minConf: 1 },
  prefs: (() => { try { return { desktop: false, sound: true, ...JSON.parse(localStorage.getItem("ts.prefs") || "{}") }; } catch { return { desktop: false, sound: true }; } })(),
};
const STATUS_CLS = { PENDING: "b-flat", ACTIVE: "b-up", CLOSED: "b-flat", EXPIRED: "b-flat", CANCELLED: "b-flat" };
const OUTCOME_CLS = { TARGET: "up", BREAKEVEN: "", TIME: "", STOPPED: "down", INVALIDATED: "muted", OCO: "muted", "NOT FILLED": "muted", DISMISSED: "muted" };

function tsSavePrefs() { try { localStorage.setItem("ts.prefs", JSON.stringify(TS.prefs)); } catch { /* storage unavailable */ } }

// ------------------------------------------------------------------ notifications
let _audio;
function tsBeep(kind) {
  if (!TS.prefs.sound) return;
  try {
    _audio = _audio || new (window.AudioContext || window.webkitAudioContext)();
    const tones = { activated: [880, 1320], tp1: [990], closed: [660, 440], created: [740] }[kind] || [700];
    tones.forEach((f, i) => {
      const o = _audio.createOscillator(), g = _audio.createGain();
      o.frequency.value = f; o.type = "sine";
      g.gain.setValueAtTime(0.0001, _audio.currentTime + i * 0.16);
      g.gain.exponentialRampToValueAtTime(0.15, _audio.currentTime + i * 0.16 + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, _audio.currentTime + i * 0.16 + 0.15);
      o.connect(g).connect(_audio.destination);
      o.start(_audio.currentTime + i * 0.16); o.stop(_audio.currentTime + i * 0.16 + 0.16);
    });
  } catch { /* audio blocked until the user interacts with the page */ }
}
function tsDesktop(title, body) {
  if (!TS.prefs.desktop || !("Notification" in window) || Notification.permission !== "granted") return;
  try { new Notification(title, { body, tag: title + body, silent: true }); } catch { /* not supported */ }
}
function onTradeSignal(msg) {
  const s = msg.signal;
  const all = [...TS.open, ...TS.closed].filter((x) => x.id !== s.id);
  const isOpen = s.status === "PENDING" || s.status === "ACTIVE";
  TS.open = (isOpen ? [s, ...all.filter((x) => x.status === "PENDING" || x.status === "ACTIVE")] : all.filter((x) => x.status === "PENDING" || x.status === "ACTIVE"));
  TS.closed = (isOpen ? all.filter((x) => !(x.status === "PENDING" || x.status === "ACTIVE")) : [s, ...all.filter((x) => !(x.status === "PENDING" || x.status === "ACTIVE"))]).slice(0, 200);
  tsBadge();
  const title = { created: "New signal", activated: "SIGNAL ACTIVATED", tp1: "TP1 hit", closed: "Signal closed" }[msg.event];
  if (msg.event !== "created" || s.confidence >= 2) {
    toast(`${title}: ${s.direction} ${s.symbol}`, msg.text.replace(/^[A-Z0-9 ]+: /, ""), msg.event === "activated" ? "CRITICAL" : "WARN");
    tsBeep(msg.event);
    tsDesktop(`${title}: ${s.direction} ${s.symbol}`, msg.text.replace(/^[A-Z0-9 ]+: /, ""));
  }
  if (S.view === "TS") tsRenderBody();
  if (msg.event !== "created") api("/api/tsignals/stats").then((x) => { TS.stats = x; if (S.view === "TS") tsRenderBody(); }).catch(() => {});
}
function tsBadge() {
  const b = document.querySelector('[data-view="TS"] .cnt');
  if (!b) return;
  const act = TS.open.filter((x) => x.status === "ACTIVE").length, pend = TS.open.filter((x) => x.status === "PENDING").length;
  b.textContent = act || pend ? `${act}/${pend}` : "";
  b.title = `${act} active, ${pend} pending`;
}

// ------------------------------------------------------------------ rendering
function tsLadder(s) {
  const last = S.rows[s.symbol]?.last ?? s.price_at_signal;
  const pts = [["TP2", s.tp2, "up"], ["TP1", s.tp1, "up"], ["ENTRY", s.fill ?? s.entry, "amber"], ["SL", s.stop, "down"], ["NOW", last, "now"]];
  const vals = pts.map((p) => p[1]), lo = Math.min(...vals), hi = Math.max(...vals), sp = hi - lo || 1;
  // place by price, then push labels apart so close levels never overlap (min 15% of the ladder)
  const placed = pts.map(([k, v, c]) => ({ k, v, c, y: ((v - lo) / sp) * 100 })).sort((a, b) => a.y - b.y);
  for (let i = 1; i < placed.length; i++) placed[i].y = Math.max(placed[i].y, placed[i - 1].y + 15);
  const over = placed[placed.length - 1].y - 100;
  if (over > 0) placed.forEach((p) => (p.y = Math.max(0, p.y - over)));
  for (let i = placed.length - 2; i >= 0; i--) placed[i].y = Math.min(placed[i].y, placed[i + 1].y - 15);
  return `<div class="ts-ladder" role="img" aria-label="Entry ${s.entry}, stop ${s.stop}, targets ${s.tp1} and ${s.tp2}, last ${last}">
    ${placed.map(({ k, v, c, y }) => `<div class="ts-lv ${c}" style="bottom:${y}%"><span>${k}</span><b>${px(s.symbol, v)}</b></div>`).join("")}
  </div>`;
}
function tsCard(s) {
  const last = S.rows[s.symbol]?.last;
  const tick = S.universe[s.symbol]?.tick_size || 0.01;
  const toEntry = last != null ? Math.round((s.entry - last) / tick) : null;
  const pnlR = last != null && s.status === "ACTIVE" ? ((last - s.fill) * s.side) / (Math.abs(s.fill - s.stop_initial) || 1) : null;
  const left = Math.max(0, s.expires - Date.now() / 1000);
  const m = s.size.micro;
  const tooBig = s.size.contracts < 1 && !(m && m.contracts >= 1);
  return `<div class="ts-card ${s.direction} ${s.status}">
    <div class="ts-top">
      <span class="dir ${s.direction}">${s.direction}</span><b class="sym">${s.symbol}</b>
      <span class="bias ${STATUS_CLS[s.status]}">${s.status}${s.tp1_hit ? " · TP1 ✓" : ""}</span>
      ${starsHtml(s.confidence)}<span class="grade g${s.grade}" title="Evidence grade">${s.grade}</span>
      <span class="muted" style="margin-left:auto">${new Date(s.created * 1000).toLocaleTimeString()}</span>
    </div>
    <div class="ts-name">${esc(s.name)} <span class="muted">· ${s.style} · ${s.timeframe} · ${s.horizon}</span></div>
    <div class="ts-body">
      <div class="kv">
        <span>Entry (${s.entry_type})</span><span class="amber">${px(s.symbol, s.entry)}</span>
        <span>Stop loss</span><span class="down">${px(s.symbol, s.stop)}${s.tp1_hit ? " (breakeven)" : ""}</span>
        <span>Take profit 1 / 2</span><span class="up">${px(s.symbol, s.tp1)} / ${px(s.symbol, s.tp2)}</span>
        <span>Reward : risk</span><span>${fx(s.rr1, 1)}R / ${fx(s.rr2, 1)}R</span>
        <span>Risk per contract</span><span>${s.risk_ticks}t · ${money(s.risk_usd)}</span>
        <span>Size (prop risk)</span><span>${s.size.contracts} ${s.symbol}${m ? ` · ${m.contracts} ${m.symbol}` : ""}</span>
        <span>${s.status === "ACTIVE" ? "Open P&L" : "Distance to entry"}</span><span>${s.status === "ACTIVE" ? `<span class="${cls(pnlR)}">${signed(pnlR, 2)}R</span>` : toEntry == null ? "—" : `${toEntry > 0 ? "+" : ""}${toEntry}t`}</span>
        <span>${s.status === "PENDING" ? "Expires in" : "Time stop in"}</span><span>${left > 86400 ? fx(left / 86400, 1) + "d" : left > 3600 ? fx(left / 3600, 1) + "h" : Math.round(left / 60) + "m"}</span>
      </div>
      ${tsLadder(s)}
    </div>
    ${tooBig ? '<div class="notice danger">Stop is too wide for today\'s risk budget on this account. Skip it, or trade it on a larger account.</div>' : ""}
    <ul class="dt-list">${s.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}${s.confidence_why.length ? `<li class="muted">Confidence: ${s.confidence_why.join(", ")}</li>` : ""}</ul>
    <div class="row-actions">
      <button class="btn ${s.side > 0 ? "buy" : "sell"}" data-tspaper="${s.id}">PAPER TRADE ${s.size.contracts >= 1 ? s.symbol : m ? m.symbol : s.symbol}</button>
      <button class="tbtn" data-tsgo="${s.symbol}">CHART</button>
      <button class="tbtn" data-tsdismiss="${s.id}">DISMISS</button>
    </div></div>`;
}
function tsStats() {
  const st = TS.stats;
  if (!st) return "";
  const o = st.overall;
  return `<div class="box"><div class="sec">TRACK RECORD <span class="muted">every signal this terminal has issued · result in R (risk multiples), half off at TP1</span></div>
    <div class="tiles">${tile("Closed signals", o.n || 0)}${tile("Win rate", o.n ? fx(o.win_pct, 0) + "%" : "—")}${tile("Avg result", o.n ? signed(o.avg_r, 2) + "R" : "—", cls(o.avg_r))}${tile("Total", o.n ? signed(o.total_r, 1) + "R" : "—", cls(o.total_r))}
      ${tile("Active", st.counts.ACTIVE || 0)}${tile("Pending", st.counts.PENDING || 0)}</div>
    ${Object.keys(st.by_setup).length ? `<table class="tbl"><thead><tr><th>SETUP</th><th>SIGNALS</th><th>WIN%</th><th>AVG R</th><th>TOTAL R</th></tr></thead><tbody>
      ${Object.entries(st.by_setup).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${v.n}</td><td>${fx(v.win_pct, 0)}%</td><td class="${cls(v.avg_r)}">${signed(v.avg_r, 2)}</td><td class="${cls(v.total_r)}">${signed(v.total_r, 1)}</td></tr>`).join("")}
    </tbody></table>` : '<div class="muted">No closed signals yet. Results appear here as signals hit their stop or targets.</div>'}
    <div class="muted" style="margin-top:6px">A live forward record, not a backtest. Simulated prices mean simulated results.</div></div>`;
}
function tsSettings() {
  const ch = TS.stats?.channels || {};
  const perm = "Notification" in window ? Notification.permission : "unsupported";
  return `<div class="box"><div class="sec">NOTIFICATIONS</div>
    <label class="dt-check"><input type="checkbox" data-tspref="sound" ${TS.prefs.sound ? "checked" : ""}> Sound on new / activated / closed signals</label>
    <label class="dt-check"><input type="checkbox" data-tspref="desktop" ${TS.prefs.desktop ? "checked" : ""}> Desktop notifications <span class="muted">(browser permission: ${perm})</span></label>
    <div class="kv" style="margin-top:6px">
      <span>Discord</span><span>${ch.discord ? '<span class="up">connected</span>' : '<span class="muted">not set</span>'}</span>
      <span>Telegram</span><span>${ch.telegram ? '<span class="up">connected</span>' : '<span class="muted">not set</span>'}</span>
      <span>Webhook</span><span>${ch.webhook ? '<span class="up">connected</span>' : '<span class="muted">not set</span>'}</span>
      <span>Events pushed</span><span>${(ch.events || []).join(", ")}</span></div>
    <div class="row-actions"><button class="tbtn" data-tstest="1">SEND TEST</button></div>
    <div class="muted">Phone alerts: set DISCORD_WEBHOOK_URL, or TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID, in the .env file and restart.</div></div>`;
}
function tsFiltered(list) {
  const f = TS.filter;
  return list.filter((s) => (!f.symbol || s.symbol === f.symbol) && (!f.horizon || s.horizon.startsWith(f.horizon)) && s.confidence >= f.minConf);
}
function tsRenderBody() {
  const el = $("#tsBody");
  if (!el) return;
  const open = tsFiltered(TS.open).sort((a, b) => (a.status === b.status ? b.created - a.created : a.status === "ACTIVE" ? -1 : 1));
  const closed = tsFiltered(TS.closed).slice(0, 60);
  el.innerHTML = `<div class="ts-grid">${open.length ? open.map(tsCard).join("") : '<div class="pad muted">No open signals match these filters. The engine scans every market every 15 seconds; new setups appear here with a sound and a toast.</div>'}</div>
    <div class="cols" style="padding:0">${tsStats()}${tsSettings()}</div>
    <div class="box"><div class="sec">SIGNAL HISTORY</div><div class="scroll"><table class="tbl"><thead><tr><th>TIME</th><th>MKT</th><th>SIDE</th><th>SETUP</th><th>TF</th><th>CONF</th><th>ENTRY</th><th>SL</th><th>TP1/TP2</th><th>STATUS</th><th>OUTCOME</th><th>R</th></tr></thead><tbody>
    ${closed.map((s) => `<tr><td>${new Date(s.created * 1000).toLocaleString()}</td><td class="sym">${s.symbol}</td><td><span class="dir ${s.direction}">${s.direction}</span></td>
      <td style="text-align:left">${esc(s.name)}</td><td>${s.timeframe}</td><td>${starsHtml(s.confidence)}</td><td>${px(s.symbol, s.fill ?? s.entry)}</td><td>${px(s.symbol, s.stop_initial)}</td>
      <td>${px(s.symbol, s.tp1)} / ${px(s.symbol, s.tp2)}</td><td>${s.status}</td><td class="${OUTCOME_CLS[s.outcome] || ""}">${s.outcome || ""}</td><td class="${cls(s.r)}">${s.r == null ? "—" : signed(s.r, 2)}</td></tr>`).join("") || '<tr><td colspan="12" class="muted">No history yet.</td></tr>'}
    </tbody></table></div></div>`;
}

VIEWS.TS = async function (v) {
  setTitle("TRADE SIGNALS · ENTRY · STOP LOSS · TAKE PROFIT · LIVE TRACKING");
  tools("");
  const [open, closed, stats] = await Promise.all([api("/api/tsignals?status=open"), api("/api/tsignals?status=closed&limit=200"), api("/api/tsignals/stats")]);
  TS.open = open; TS.closed = closed; TS.stats = stats;
  const f = TS.filter;
  v.innerHTML = `<div class="dt"><div class="filters">
      <label>Market <select data-tsf="symbol"><option value="">All</option>${Object.keys(S.universe).filter((k) => !S.universe[k].parent).map((k) => `<option ${k === f.symbol ? "selected" : ""}>${k}</option>`).join("")}</select></label>
      <label>Horizon <select data-tsf="horizon"><option value="">All</option><option ${f.horizon === "Intraday" ? "selected" : ""}>Intraday</option><option value="Swing" ${f.horizon === "Swing" ? "selected" : ""}>Swing</option></select></label>
      <label>Confidence <select data-tsf="minConf"><option value="1">★ and up</option><option value="2" ${f.minConf == 2 ? "selected" : ""}>★★ and up</option><option value="3" ${f.minConf == 3 ? "selected" : ""}>★★★ only</option></select></label>
      <span class="muted" style="margin-left:auto">Signals are trade ideas generated by rules, not advice. Each shows its evidence grade; most intraday setups are grade C (structure, no proven edge).</span>
    </div><div id="tsBody"></div></div>`;
  tsRenderBody();
  tsBadge();
};

document.addEventListener("click", async (ev) => {
  const t = ev.target.closest("[data-tspaper],[data-tsdismiss],[data-tsgo],[data-tstest]");
  if (!t) return;
  if (t.dataset.tsgo) return select(t.dataset.tsgo, "MON");
  if (t.dataset.tstest) {
    const r = await api("/api/tsignals/test-notify", { method: "POST" });
    toast("Test notification", r.sent ? "Sent to your configured channels" : "No Discord / Telegram / webhook configured in .env", r.sent ? "INFO" : "WARN");
    tsBeep("activated"); tsDesktop("Test notification", "Desktop alerts are working");
    return;
  }
  if (t.dataset.tsdismiss) { await api(`/api/tsignals/${t.dataset.tsdismiss}/dismiss`, { method: "POST" }); return; }
  if (t.dataset.tspaper) {
    const s = TS.open.find((x) => x.id === t.dataset.tspaper);
    const micro = s && s.size.contracts < 1 && s.size.micro && s.size.micro.contracts >= 1;
    try {
      const r = await api(`/api/tsignals/${t.dataset.tspaper}/paper?use_micro=${micro}`, { method: "POST" });
      toast("Paper trade placed", `${r.order.qty > 0 ? "BUY" : "SELL"} ${Math.abs(r.order.qty)} ${r.order.symbol} @ ${px(r.order.symbol, r.order.fill_price)} with stop and TP2 bracket`, "INFO");
    } catch (e) {
      toast("Order rejected", (e.data?.detail?.reasons || [e.message]).join(" · "), "CRITICAL");
    }
  }
});
document.addEventListener("change", async (ev) => {
  const t = ev.target;
  if (t.dataset.tsf !== undefined && S.view === "TS") {
    TS.filter[t.dataset.tsf] = t.dataset.tsf === "minConf" ? parseInt(t.value, 10) : t.value;
    return tsRenderBody();
  }
  if (t.dataset.tspref) {
    TS.prefs[t.dataset.tspref] = t.checked;
    if (t.dataset.tspref === "desktop" && t.checked && "Notification" in window && Notification.permission !== "granted") {
      const p = await Notification.requestPermission();
      if (p !== "granted") { TS.prefs.desktop = false; t.checked = false; toast("Desktop alerts blocked", "Allow notifications for this site in your browser settings", "WARN"); }
    }
    tsSavePrefs();
    if (S.view === "TS") tsRenderBody();
  }
});

(async function bootTS() {
  try { [TS.open, TS.stats] = await Promise.all([api("/api/tsignals?status=open"), api("/api/tsignals/stats")]); tsBadge(); } catch { /* server warming up */ }
})();
