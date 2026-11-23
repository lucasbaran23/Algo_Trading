/* Day-Trade Insider: "What do I need to know today to day trade this market?" */
"use strict";

const DT = { data: null, timer: null };
const KIND_LABEL = { pd: "Prior day", session: "Today", vwap: "VWAP", pivot: "Pivot", swing: "Swing", round: "Round #", profile: "Profile" };

function dtFmt(v) { return S.snap ? px(S.sel, v) : fx(v, 2); }
function verdictBadge(v) {
  const cls = { PREFERRED: "b-up", WORKS: "b-up", MARGINAL: "b-flat", AVOID: "b-down" }[v] || "b-flat";
  return `<span class="bias ${cls}">${v}</span>`;
}

// ------------------------------------------------------------------ cards
function dtHeader(d) {
  const p = d.price;
  const syms = Object.keys(S.universe);
  const status = { OPEN: "live", "PRE-MARKET": "sim", CLOSED: "na" }[d.status];
  return `<div class="dt-head">
    <div class="dt-title">
      <div class="dt-sym">${d.symbol}<span>${esc(d.name)}</span></div>
      <div class="headline"><span class="big" id="dtLast">${px(d.symbol, p.last)}</span>
        <span class="${cls(p.chg)}" id="dtChg">${signed(p.chg, decimals(d.symbol))} (${signed(p.chg_pct, 2)}%)</span></div>
    </div>
    <div class="dt-badges">
      <span class="badge ${status}">${d.status}</span>
      <span class="badge">FOR ${d.weekday.toUpperCase()} ${d.prepared_for}</span>
      <span class="badge">SESSION ${d.session.open}–${d.session.close} ET</span>
      <span class="bias ${biasCls(d.bias.replace("LEAN ", "").replace("LONG", "BUY").replace("SHORT", "SELL"))}">BIAS ${d.bias}</span>
      <span class="bias b-flat">${d.regime.label}</span>
      ${Object.entries(d.sources).map(([k, v]) => srcBadge(k.slice(0, 3).toUpperCase(), v)).join(" ")}
      ${d.holiday ? `<span class="badge sim">${esc(d.holiday)}: market closed today</span>` : ""}
    </div>
    <div class="dt-switch">${syms.map((s) => `<button class="tbtn ${s === d.symbol ? "on" : ""}" data-dtsym="${s}">${s}</button>`).join("")}</div>
  </div>`;
}

function dtTopdown(d) {
  const t = d.topdown;
  if (!t) return "";
  const arrow = (s) => (s >= 1.5 ? "▲▲" : s >= 0.5 ? "▲" : s <= -1.5 ? "▼▼" : s <= -0.5 ? "▼" : "●");
  return `<div class="box dt-td"><div class="sec">TOP-DOWN <span class="muted">${esc(t.setup)} · alignment ${signed(t.alignment, 0)} · execute on ${t.exec_tf}</span></div>
    <div class="dt-tdrow">${t.rows.map((r) => `<span class="dt-tdchip ${r.score >= 0.5 ? "up" : r.score <= -0.5 ? "down" : "muted"}" title="${r.name}: ${r.label}, ${esc(r.structure)}, RSI ${r.rsi}">
      <b>${r.tf}</b> ${arrow(r.score)} <span class="muted">${r.label}</span></span>`).join("")}
      <button class="tbtn" data-irgo="1">OPEN INTRADAY RESEARCH ›</button></div></div>`;
}

function dtPlan(d) {
  let done = {};
  try { done = JSON.parse(localStorage.getItem(`dt.check.${d.prepared_for}.${d.symbol}`) || "{}"); } catch { /* no storage */ }
  return `<div class="box dt-plan"><div class="sec">TODAY'S GAME PLAN <span class="muted">what you need to know before the bell</span></div>
      <ol class="dt-bullets">${d.plan.map((t) => `<li>${esc(t)}</li>`).join("")}</ol></div>
    <div class="box"><div class="sec">PRE-SESSION CHECKLIST <span class="muted">${Object.values(done).filter(Boolean).length}/${d.checklist.length}</span></div>
      ${d.checklist.map((t, i) => `<label class="dt-check"><input type="checkbox" data-dtcheck="${i}" ${done[i] ? "checked" : ""}> ${esc(t)}</label>`).join("")}</div>`;
}

function dtLadder(d) {
  const last = d.price.last;
  const rows = [];
  let placed = false;
  for (const l of d.levels) {
    if (!placed && l.price <= last) {
      rows.push(`<tr class="dt-last"><td>▶ LAST</td><td>${px(d.symbol, last)}</td><td></td><td></td></tr>`);
      placed = true;
    }
    rows.push(`<tr class="lv-${l.kind}"><td><i class="dt-mark ${l.side}"></i>${esc(l.label)}</td><td>${px(d.symbol, l.price)}</td>
      <td class="${l.side === "above" ? "down" : "up"}">${l.dist_ticks > 0 ? "+" : ""}${l.dist_ticks}t</td><td class="muted">${signed(l.dist_atr, 2)} ATR</td></tr>`);
  }
  if (!placed) rows.push(`<tr class="dt-last"><td>▶ LAST</td><td>${px(d.symbol, last)}</td><td></td><td></td></tr>`);
  const pv = d.prev;
  return `<div class="box"><div class="sec">KEY LEVELS <span class="muted">within 2.5 ATR · red = resistance, green = support</span></div>
    <table class="tbl dt-ladder"><tbody>${rows.join("")}</tbody></table>
    <div class="kv" style="margin-top:6px">
      <span>Prior day (${pv.date}) O / H / L / C</span><span>${px(d.symbol, pv.open)} / ${px(d.symbol, pv.high)} / ${px(d.symbol, pv.low)} / ${px(d.symbol, pv.close)}</span>
      <span>Today open · gap</span><span>${px(d.symbol, d.price.today_open)} · <span class="${cls(d.price.gap)}">${signed(d.price.gap_atr, 2)} ATR</span></span>
      <span>Today high / low</span><span>${px(d.symbol, d.price.today_high)} / ${px(d.symbol, d.price.today_low)}</span>
    </div></div>`;
}

function dtProfile(d) {
  const p = d.profile;
  if (!p || !p.bins) return '<div class="box"><div class="sec">VOLUME PROFILE</div><div class="muted">Not enough data.</div></div>';
  const W = 300, H = 300, padL = 66, bins = p.bins;
  const lo = bins[0].price, hi = bins[bins.length - 1].price, span = hi - lo || 1;
  const y = (v) => H - 6 - ((v - lo) / span) * (H - 12);
  const bh = Math.max(2, (H - 12) / bins.length - 1);
  const bars = bins.map((b) => {
    const inVA = b.price >= p.val && b.price <= p.vah;
    const isPoc = b.price === p.poc;
    const fill = isPoc ? "var(--accent)" : inVA ? "var(--s1)" : "var(--muted)";
    return `<rect x="${padL}" y="${(y(b.price) - bh / 2).toFixed(1)}" width="${Math.max(1, b.vol * (W - padL - 6)).toFixed(1)}" height="${bh.toFixed(1)}" rx="1" style="fill:${fill};opacity:${inVA || isPoc ? 0.9 : 0.45}"><title>${px(d.symbol, b.price)}: ${(b.vol * 100).toFixed(0)}% of POC volume</title></rect>`;
  }).join("");
  const line = (v, label, color, dash = "") => v >= lo && v <= hi ? `<line x1="${padL - 4}" x2="${W}" y1="${y(v)}" y2="${y(v)}" style="stroke:${color}" stroke-width="1.5" ${dash ? `stroke-dasharray="${dash}"` : ""}/>
      <text x="${padL - 8}" y="${y(v) + 3.5}" text-anchor="end" style="fill:${color}" font-size="10">${label}</text>` : "";
  return `<div class="box"><div class="sec">VOLUME PROFILE <span class="muted">composite, last ${p.sessions} sessions${p.estimated ? " · estimated from daily bars" : ""}</span></div>
    <svg viewBox="0 0 ${W} ${H}" class="dt-profile" role="img" aria-label="Volume profile: POC ${p.poc}, value area ${p.val} to ${p.vah}">
      ${bars}
      ${line(p.vah, "VAH " + px(d.symbol, p.vah), "var(--ink-2)", "3 3")}
      ${line(p.val, "VAL " + px(d.symbol, p.val), "var(--ink-2)", "3 3")}
      ${line(p.poc, "POC " + px(d.symbol, p.poc), "var(--accent)")}
      ${line(d.price.last, "LAST", "var(--warn)")}
    </svg>
    <div class="kv dt-kv"><span>POC (most traded)</span><span>${px(d.symbol, p.poc)}</span>
      <span>Value area (70%)</span><span>${px(d.symbol, p.val)} – ${px(d.symbol, p.vah)}</span>
      <span>HVN · magnets</span><span>${p.hvn.map((v) => px(d.symbol, v)).join(", ") || "—"}</span>
      <span>LVN · fast moves</span><span>${p.lvn.map((v) => px(d.symbol, v)).join(", ") || "—"}</span></div>
    <div class="dt-tip">${d.price.last > p.vah ? "Price is ABOVE value: buyers are in control while it holds above VAH. A move back inside value often rotates to the POC."
      : d.price.last < p.val ? "Price is BELOW value: sellers are in control while it holds below VAL. A move back inside value often rotates to the POC."
      : "Price is INSIDE value: expect rotation between VAL and VAH. Fade the edges, target the POC."}</div></div>`;
}

function dtRange(d) {
  const r = d.ranges, g = d.regime, s = d.seasonality, used = Math.min(150, r.adr_used_pct);
  const wd = s.weekday || {}, mo = s.month || {};
  return `<div class="box"><div class="sec">EXPECTED MOVE TODAY</div>
    <div class="meter"><div class="top"><span>Range used vs 20-day average</span><span>${fx(r.adr_used_pct, 0)}%</span></div>
      <div class="track" role="meter" aria-valuenow="${used}" aria-valuemin="0" aria-valuemax="150"><div class="fill ${r.adr_used_pct >= 100 ? "bad" : r.adr_used_pct >= 70 ? "warn" : "ok"}-fill" style="width:${used / 1.5}%"></div></div></div>
    <div class="kv">
      <span>Average daily range (20d)</span><span>${px(d.symbol, r.adr20)} · ${Math.round(r.adr20 / d.tick)} ticks · ${money(r.adr20 / d.tick * d.tick_value)}/ct</span>
      <span>Typical / big day (p50 / p80)</span><span>${px(d.symbol, r.range_p50)} / ${px(d.symbol, r.range_p80)}</span>
      <span>Today so far</span><span>${px(d.symbol, r.today_range)} · ${px(d.symbol, r.remaining)} left on an average day</span>
      <span>Projected high / low (avg day)</span><span>${px(d.symbol, r.proj_high)} / ${px(d.symbol, r.proj_low)}</span>
      <span>Volatility regime</span><span>${d.vol.regime} (${fx(d.vol.percentile, 0)} pct)</span>
    </div>
    <div class="sec">REGIME <span class="muted">${g.label}</span></div>
    <div class="kv">
      <span>ADX (trend strength &gt;25)</span><span>${fx(g.adx, 0)} · DI+ ${fx(g.plus_di, 0)} / DI− ${fx(g.minus_di, 0)}</span>
      <span>Efficiency ratio (0 chop → 1 trend)</span><span>${fx(g.efficiency, 2)}</span>
      <span>Choppiness (&gt;60 range, &lt;45 trend)</span><span>${fx(g.chop, 0)}</span>
      <span>vs 20 / 50-day average</span><span>${g.above_sma20 ? "above" : "below"} / ${g.above_sma50 ? "above" : "below"}</span>
    </div>
    <div class="dt-tip">${esc(g.tip)}</div>
    <div class="sec">SEASONALITY</div>
    <div class="kv">
      ${wd.weekday ? `<span>${wd.weekday}s (${wd.samples} sessions)</span><span>up ${fx(wd.up_pct, 0)}% · range ×${fx(wd.range_vs_avg, 2)} vs avg</span>` : ""}
      ${mo.month ? `<span>${mo.month} (${mo.years} yrs)</span><span>up ${fx(mo.up_pct, 0)}% · avg ${signed(mo.avg_ret_pct, 2)}%</span>` : ""}
      <span>Next 20 days, prior years</span><span>${signed((s.window_20d.avg_fwd_ret || 0) * 100, 2)}% avg · ${fx((s.window_20d.hit_rate || 0) * 100, 0)}% up</span>
      <span>Model P(up) next 5 days</span><span>${fx(s.p_up_5d * 100, 0)}%</span>
      <span>COT positioning</span><span>${d.cot.state} (${fx(d.cot.index, 0)})</span>
    </div></div>`;
}

function dtCatalysts(d) {
  const ev = d.events.map((e) => `<div class="dt-ev s${e.stars}"><span class="t">${e.time || "all day"}</span>
      <span><b>${esc(e.name)}</b> ${starsHtml(e.stars)}${e.estimated ? ' <span class="badge sim">DATE EST.</span>' : ""}
      ${e.note ? `<div class="muted">${esc(e.note)}</div>` : ""}</span></div>`).join("");
  return `<div class="box"><div class="sec">CATALYSTS · ${d.weekday.toUpperCase()} <span class="muted">times ET</span></div>
    ${ev || '<div class="muted">No scheduled report for this market today. Price action and levels lead.</div>'}
    <div class="sec">MARKET-MOVING NEWS <span class="muted">★★+ last 36 h</span></div>
    ${d.news.length ? d.news.map((it) => newsRow(it, { focus: d.data_symbol })).join("") : '<div class="muted">No ★★/★★★ headlines for this market.</div>'}
  </div>`;
}

function dtPlaybook(d) {
  const pb = d.playbook;
  return `<div class="box"><div class="sec">PLAYBOOK · RANKED FOR A ${pb.regime.toUpperCase()} MARKET
      <span class="muted">${pb.days} sessions ${pb.from} → ${pb.to} · per 1 contract after ${money(pb.rt_cost)} round-turn costs</span></div>
    <div class="scroll"><table class="tbl dt-pb"><thead><tr><th>#</th><th>SETUP</th><th>VERDICT</th><th>TRADES</th><th>WIN%</th><th>$/TRADE</th><th>TICKS</th><th>PF</th><th>T-STAT</th><th>ALL REGIMES</th><th>TODAY</th></tr></thead><tbody>
    ${pb.rows.map((r, i) => { const u = r[r.used]; return `<tr title="${esc(r.rule)}">
      <td>${i + 1}</td><td style="text-align:left"><b>${esc(r.name)}</b><div class="muted">${r.style}${r.fits_regime ? " · fits regime" : ""}${r.used === "all" ? " · all-regime stats (few in this regime)" : ""}</div></td>
      <td>${verdictBadge(r.verdict)}</td><td>${u.n ?? 0}</td><td>${fx(u.win_pct, 0)}%</td><td class="${cls(u.avg)}">${money(u.avg)}</td><td class="${cls(u.avg_ticks)}">${signed(u.avg_ticks, 1)}</td>
      <td>${fx(u.pf, 2)}</td><td>${fx(u.t_stat, 1)}</td><td class="${cls(r.all.avg)}">${money(r.all.avg)} <span class="muted">(${r.all.n})</span></td>
      <td style="text-align:left;white-space:normal;min-width:220px">${r.today.active ? '<span class="dir LONG">ACTIVE</span>' : '<span class="dir NEUTRAL">NOT TODAY</span>'} <span class="muted">${esc(r.today.note || "")}</span></td></tr>`; }).join("")}
    </tbody></table></div>
    <div class="muted" style="margin-top:6px">Single-session setups, tested on daily bars with an open→low→high→close (up day) / open→high→low→close (down day) path model.
    t-stat ≥ 1 is weak evidence and ≥ 2 is stronger; few trades means low confidence. Verify finalists on intraday data before going live. Hover a row for the exact rules.</div></div>`;
}

function dtRisk(d) {
  const r = d.risk, a = r.account;
  const m = r.micro;
  return `<div class="box dt-risk"><div class="sec">RISK PLAN <span class="muted">${esc(a.name)} · today ${money(a.today_pnl)} · loss left ${money(a.daily_loss_remaining)} · DD buffer ${money(a.distance_to_breach)}</span></div>
    <div class="tiles">
      ${tile("Size multiplier", "×" + fx(r.size_multiplier, 2), r.size_multiplier < 1 ? "down" : "up")}
      ${tile("Stop", `${r.stop_ticks}t · ${money(r.stop_usd)}`)}
      ${tile("Target (2R)", `${r.target_ticks}t`)}
      ${tile("Risk / trade", money(r.risk_budget))}
      ${tile(d.data_symbol + " contracts", r.contracts, r.contracts < 1 ? "down" : "")}
      ${m ? tile(m.symbol + " contracts", m.contracts, r.use_micro ? "up" : "") : ""}
      ${tile("Daily stop", money(-r.daily_stop), "down")}
      ${tile("Max trades", r.max_trades)}
      ${r.consistency_cap ? tile("Consistency cap", money(r.consistency_cap)) : ""}
    </div>
    ${r.use_micro ? `<div class="notice">A full ${d.data_symbol} risks ${money(r.risk_per_contract)} per stop-out, more than today's budget. Trade ${m.contracts} ${m.symbol} instead.</div>` : ""}
    <div class="cols" style="padding:0">
      <div><div class="sec">WHY THIS SIZE</div><ul class="dt-list">${r.reasons.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
        <div class="sec">RULES FOR TODAY</div><ul class="dt-list">${r.rules.map((x) => `<li>${esc(x)}</li>`).join("")}${r.events.map((x) => `<li class="down">${esc(x)}</li>`).join("")}</ul></div>
      <div><div class="sec">TIME OF DAY</div><ul class="dt-list">${r.time_of_day.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
        <div class="row-actions"><button class="btn amber" data-dtticket="1">LOAD PLAN INTO ORDER TICKET</button></div></div>
    </div></div>`;
}

// ------------------------------------------------------------------ view
async function dtLoad(sym) {
  DT.data = await api(`/api/insider/${sym}`);
  return DT.data;
}
function dtRender() {
  const v = $("#view"), d = DT.data;
  if (!v || S.view !== "DT" || !d) return;
  const keep = v.scrollTop;
  v.innerHTML = `<div class="dt">${dtHeader(d)}
    ${dtTopdown(d)}
    <div class="dt-row dt-r1">${dtPlan(d)}</div>
    <div class="dt-row dt-r3">${dtLadder(d)}${dtProfile(d)}${dtRange(d)}</div>
    <div class="dt-row dt-r2">${dtCatalysts(d)}${dtPlaybook(d)}</div>
    <div class="dt-row">${dtRisk(d)}</div></div>`;
  v.scrollTop = keep;
}

VIEWS.DT = async function (v) {
  setTitle("DAY-TRADE INSIDER · WHAT YOU NEED TO KNOW TODAY");
  tools(`<button class="tbtn" data-dtrefresh="1">↻ REFRESH</button>`);
  v.innerHTML = '<div class="pad muted">Preparing the briefing…</div>';
  await dtLoad(S.sel);
  dtRender();
  clearInterval(DT.timer);
  DT.timer = setInterval(async () => {
    if (S.view !== "DT") return clearInterval(DT.timer);
    try { await dtLoad(S.sel); dtRender(); } catch { /* transient */ }
  }, 20000);
};

document.addEventListener("click", async (ev) => {
  const t = ev.target.closest("[data-dtsym],[data-dtrefresh],[data-dtticket],[data-irgo]");
  if (!t || S.view !== "DT") return;
  if (t.dataset.irgo) { S.view = "IR"; return renderView(); }
  if (t.dataset.dtsym) return select(t.dataset.dtsym, "DT");
  if (t.dataset.dtrefresh) { await dtLoad(S.sel); return dtRender(); }
  if (t.dataset.dtticket && DT.data) {
    const r = DT.data.risk;
    const sym = r.use_micro && r.micro ? r.micro.symbol : DT.data.symbol;
    S.ticketPreset = { symbol: sym, qty: Math.max(1, r.use_micro ? r.micro.contracts : r.contracts), stop: r.stop_ticks, target: r.target_ticks };
    S.view = "PROP";
    renderView();
  }
});
document.addEventListener("change", (ev) => {
  const i = ev.target.dataset.dtcheck;
  if (i === undefined || !DT.data) return;
  const key = `dt.check.${DT.data.prepared_for}.${DT.data.symbol}`;
  try {
    const done = JSON.parse(localStorage.getItem(key) || "{}");
    done[i] = ev.target.checked;
    localStorage.setItem(key, JSON.stringify(done));
  } catch { /* storage unavailable: checklist still works for this view */ }
});
