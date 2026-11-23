/* Levels & σ-fade: trading futures around key options levels, and fading statistically extreme moves. */
"use strict";

const LX = { sym: "NG", part: "opt", ref: "month", k: 2.5, opt: null, sig: null, busy: false };

function lxLadder(p) {
  const lv = [["CALL WALL", p.call_wall, "var(--s2)"], ["GAMMA FLIP", p.flip, "var(--ink)"], ["MAX PAIN", p.max_pain, "var(--s4)"],
    ["PUT WALL", p.put_wall, "var(--s3)"], ["NOW", p.price, "var(--warn)"]].filter((x) => x[1] != null);
  const sd = p.sd_day || 0;
  const vals = lv.map((x) => x[1]).concat([p.price + sd, p.price - sd]);
  const lo = Math.min(...vals), hi = Math.max(...vals), pad = (hi - lo) * 0.08 || 1;
  const W = 400, H = 230, y = (v) => 10 + (1 - (v - (lo - pad)) / (hi - lo + 2 * pad)) * (H - 20);
  const sorted = [...lv].sort((a, b) => b[1] - a[1]);
  let lastY = -99;
  const labels = sorted.map(([lab, v, col]) => {
    let ly = Math.max(y(v), lastY + 13); lastY = ly;
    return `<line x1="62" x2="${W - 130}" y1="${y(v)}" y2="${y(v)}" style="stroke:${col}" stroke-width="${lab === "NOW" ? 2.5 : 1.5}" ${lab === "GAMMA FLIP" ? 'stroke-dasharray="5 4"' : ""}/>
      <text x="${W - 125}" y="${ly + 4}" font-size="11" style="fill:${col}">${lab} ${px(p.symbol, v)}</text>
      <text x="56" y="${ly + 4}" font-size="10" text-anchor="end" style="fill:var(--muted)">${signed((v - p.price) / (sd || 1), 2)}σ</text>`;
  }).join("");
  const band = sd ? `<rect x="62" width="${W - 192}" y="${y(p.price + sd)}" height="${y(p.price - sd) - y(p.price + sd)}" style="fill:var(--s1);opacity:.10"/>
    <text x="66" y="${y(p.price - sd) - 4}" font-size="9" style="fill:var(--muted)">±1 daily σ (${px(p.symbol, sd)})</text>` : "";
  const zone = p.range_hint ? `<rect x="62" width="${W - 192}" y="${y(p.range_hint[1])}" height="${y(p.range_hint[0]) - y(p.range_hint[1])}" style="fill:var(--s1);opacity:.06"/>` : "";
  return `<svg viewBox="0 0 ${W} ${H}" class="opt-svg" role="img" aria-label="Key options levels relative to the current price">${zone}${band}${labels}
    </svg>`;
}

function lxPaths(ev) {
  const dn = ev.down?.path_sd || [], up = ev.up?.path_sd || [];
  if (!dn.length && !up.length) return '<div class="muted">Not enough extreme days yet.</div>';
  const all = [0, ...dn, ...up], W = 560, H = 200, padL = 44, n = Math.max(dn.length, up.length);
  const ya = Math.max(...all.map(Math.abs), 0.5);
  const x = (i) => padL + (i / n) * (W - padL - 70), y = (v) => 10 + (1 - (v + ya) / (2 * ya)) * (H - 34);
  const line = (p, col, lab) => p.length ? `<polyline fill="none" stroke-width="2" style="stroke:${col}" points="${[0, ...p].map((v, i) => `${x(i)},${y(v)}`).join(" ")}"/>
    <text x="${x(p.length) + 4}" y="${y(p[p.length - 1]) + 4}" font-size="10" style="fill:${col}">${lab}</text>` : "";
  return `<svg viewBox="0 0 ${W} ${H}" class="opt-svg" role="img" aria-label="Average price path after extreme days, in daily sigma">
    <line x1="${padL}" x2="${W - 70}" y1="${y(0)}" y2="${y(0)}" style="stroke:var(--line)"/>
    ${line(dn, "var(--s1)", "after DOWN")}${line(up, "var(--s2)", "after UP")}
    ${Array.from({ length: n + 1 }, (_, i) => i % 2 === 0 ? `<text x="${x(i)}" y="${H - 8}" font-size="9" text-anchor="middle" style="fill:var(--muted)">${i}d</text>` : "").join("")}
    <text x="4" y="${y(ya) + 4}" font-size="9" style="fill:var(--muted)">${signed(ya, 1)}σ</text><text x="4" y="${y(-ya)}" font-size="9" style="fill:var(--muted)">${signed(-ya, 1)}σ</text>
  </svg>`;
}

const st4 = (s) => s && s.n ? `${s.n} · <span class="${cls(s.avg)}">${susd(s.avg)}</span> · t ${fx(s.t_stat, 2)}` : '<span class="muted">—</span>';

function lxGrid(part, title, note) {
  const ch = part.chosen;
  return `<div class="box"><div class="sec">${title} <span class="muted">${note}</span></div>
    <table class="tbl"><thead><tr><th>THRESHOLD</th><th>CONFIRMATION</th><th>ALL TRADES (n · avg · t)</th><th>IN-SAMPLE</th><th>OUT-OF-SAMPLE</th><th>FADING DROPS (longs)</th><th>FADING RALLIES (shorts)</th></tr></thead><tbody>
    ${part.grid.map((r) => `<tr class="${r.k === ch.k && r.confirm === ch.confirm ? "hl" : ""}"><td class="sym">±${r.k}σ${r.k === ch.k && r.confirm === ch.confirm ? " ★" : ""}</td>
      <td>${r.confirm === "none" ? "none (enter at once)" : "wait for reversal"}</td><td>${st4(r.all)}</td><td>${st4(r.is)}</td><td>${st4(r.oos)}</td><td>${st4(r.longs)}</td><td>${st4(r.shorts)}</td></tr>`).join("")}
    </tbody></table>
    ${gsLine(part.equity || [], 1300)}
    <div class="muted">★ = best in-sample; judge it on the out-of-sample column. Equity curve: the starred configuration, per 1 contract after costs.</div></div>`;
}

function lxRenderSig(d) {
  const t = d.today, bt = d.backtest, m = t.ref_stats.month || {}, mw = t.ref_stats.month_weekday || {}, o = t.ref_stats.other_months || {};
  const sim = bt.source === "SIM";
  const ng = t.symbol === "NG" && [11, 12, 1, 2, 3].includes(new Date().getMonth() + 1);
  return `
    <div class="tiles">
      ${tile("Move since prior close", signed(t.move_pct, 2) + "%", cls(t.move_pct))}${tile("Z-score now", signed(t.z_now, 2) + "σ", Math.abs(t.z_now) >= t.k ? "warn" : "")}
      ${tile("Percentile in this month", t.pct_rank_month == null ? "—" : `P${fx(t.pct_rank_month, 0)}`)}${tile("Daily σ (this month)", fx(t.sd_day_pct, 2) + "%")}
      ${tile("Session elapsed", fx(100 * t.session_frac, 0) + "%")}${tile("Share of day's variance so far", fx(100 * t.var_frac, 0) + "%")}
    </div>
    <div class="box"><div class="sec">STATE</div><div class="headline"><span class="badge ${t.armed ? "sim" : "live"}">${esc(t.state)}</span>
      <span class="muted">Threshold ±${t.k}σ, reference: ${esc(t.ref_label)}</span></div></div>
    <div class="dt-row dt-r2b">
      <div class="box"><div class="sec">SIGMA BANDS FROM YESTERDAY'S CLOSE <span class="muted">${px(t.symbol, t.prev_close)}</span></div>
        <table class="tbl"><thead><tr><th>LEVEL</th><th>FADE-LONG ZONE NOW</th><th>FADE-SHORT ZONE NOW</th><th>BY THE CLOSE (down)</th><th>BY THE CLOSE (up)</th></tr></thead><tbody>
        ${t.bands.map((b) => `<tr><td class="sym">±${b.k}σ</td><td class="up">≤ ${px(t.symbol, b.now_down)}</td><td class="down">≥ ${px(t.symbol, b.now_up)}</td><td>${px(t.symbol, b.close_down)}</td><td>${px(t.symbol, b.close_up)}</td></tr>`).join("")}
        </tbody></table>
        <div class="muted">"Now" bands are scaled to the time of day: overnight gap share plus the elapsed part of the session. Last price ${px(t.symbol, t.last)}.</div></div>
      <div class="box"><div class="sec">HOW FAT ARE THIS MONTH'S TAILS? <span class="muted">daily returns, ${new Date().toLocaleString("en", { month: "long" })}</span></div>
        <table class="tbl"><thead><tr><th>SAMPLE</th><th>DAYS</th><th>DAILY SD</th><th>P1</th><th>P99</th><th>&gt;3 SD DAYS</th><th>IF NORMAL</th></tr></thead><tbody>
        ${[["This month", m], ["This month & weekday", mw], ["Other months", o]].map(([lab, s]) => s.n >= 10 ? `<tr><td class="sym">${lab}</td><td>${s.n}</td><td>${fx(s.sd_pct, 2)}%</td><td class="down">${signed(s.p01_pct, 2)}%</td><td class="up">${signed(s.p99_pct, 2)}%</td><td>${s.n_3sd} (${s.n_3sd_down}↓ ${s.n_3sd_up}↑)</td><td>${s.normal_3sd_expected}</td></tr>` : "").join("")}
        </tbody></table>
        <div class="muted">More 3σ days than a normal distribution predicts means fat tails: extremes happen more often, and fading them needs a stop.</div></div>
    </div>
    <div class="dt-row dt-r2b">
      <div class="box"><div class="sec">WHAT HAPPENED AFTER ±${t.k} SD DAYS? <span class="muted">average path, in daily σ (10 years of daily bars)</span></div>${lxPaths(t.events)}
        <div class="muted">After down days: ${t.events.down?.n || 0} events, ${t.events.down?.reverted_5d_pct ?? "—"}% higher 5 days later. After up days: ${t.events.up?.n || 0} events, ${t.events.up?.reverted_5d_pct ?? "—"}% lower 5 days later.
        A line going back toward zero = reversion (fade works); moving further away = momentum (fade fails).</div></div>
      <div class="box"><div class="sec">THE RULES</div><ul class="dt-list">
        <li><b>Measure:</b> today's move vs the same calendar month in earlier years (or the same month and weekday, or the last 60 sessions). Only past data is used.</li>
        <li><b>Arm:</b> at ±${t.k}σ for the time of day. Fade drops (long) and rallies (short).</li>
        <li><b>Confirm:</b> don't catch the knife. Wait for a 5-minute bar to close above the previous bar's high (long) or below its low (short). Swing: wait for a 0.5σ bounce off the low within 3 days.</li>
        <li><b>Stop</b> beyond the day's extreme. <b>Target</b> halfway back to yesterday's close. Flat at the close (swing: after 5 sessions).</li>
        ${ng ? '<li class="down"><b>NG in winter:</b> fading a rally means shorting into weather risk. The winter policy keeps those intraday-only at half size; fading drops (buying) fits the winter long bias.</li>' : ""}
        <li><b>Why it can fail:</b> a 3σ move driven by news (a weather-model shift, a storage surprise) often keeps going. That's why the confirmation and the stop matter.</li></ul></div>
    </div>
    ${lxGrid(bt.intraday, "INTRADAY BACKTEST", `${bt.intraday.sessions} sessions of 1-minute bars · split at ${bt.intraday.split}`)}
    ${lxGrid(bt.swing, "SWING BACKTEST", `daily bars from ${bt.swing.from} · split at ${bt.swing.split} · hold ≤ 5 sessions`)}
    <div class="box"><div class="sec">DATA</div><div class="muted">${sim ? '<span class="down">SIMULATED prices</span>: simulated returns have no built-in reversion, so any "edge" here is noise. Load real daily and 1-minute data to test it.' : esc(bt.source)} Round-trip cost ${usd(bt.rt_cost)} per contract.</div></div>`;
}

function lxRenderOpt(d) {
  const p = d.playbook, bt = d.backtest;
  const pos = p.regime === "POSITIVE";
  return `
    <div class="tiles">
      ${tile("Dealer gamma", pos ? "LONG γ · range" : "SHORT γ · breakout", pos ? "up" : "down")}${tile("Call wall", px(p.symbol, p.call_wall))}
      ${tile("Gamma flip", p.flip == null ? "none in range" : px(p.symbol, p.flip))}${tile("Put wall", px(p.symbol, p.put_wall))}
      ${tile("Max pain (front)", px(p.symbol, p.max_pain))}${tile("Front expiry", p.front_days == null ? "—" : `${p.front_days} day${p.front_days === 1 ? "" : "s"}`)}
      ${tile("Straddle move", p.expected_move ? "±" + px(p.symbol, p.expected_move) : "—")}${tile("1 daily σ", px(p.symbol, p.sd_day))}
    </div>
    <div class="dt-row dt-r2b">
      <div class="box"><div class="sec">LEVEL LADDER <span class="muted">${esc(p.source)} · dealer assumption: ${p.mode === "standard" ? "standard" : "short calls & puts"}</span></div>${lxLadder(p)}</div>
      <div class="box"><div class="sec">TODAY'S PLAYBOOK <span class="muted">nearest level first</span></div>
        <table class="tbl"><thead><tr><th>RULE</th><th>SIDE</th><th>TRIGGER</th><th>ENTRY</th><th>STOP</th><th>TARGET</th><th>AWAY</th></tr></thead><tbody>
        ${p.plays.map((x) => `<tr><td class="sym">${x.rule.replace("_", " ")}</td><td class="${x.side === "LONG" ? "up" : "down"}">${x.side}</td><td>${esc(x.trigger)}</td>
          <td>${px(p.symbol, x.entry)}</td><td>${px(p.symbol, x.stop)}</td><td>${px(p.symbol, x.target)}</td><td>${signed(x.distance_sd, 2)}σ</td></tr>`).join("") || '<tr><td colspan="7" class="muted">No level in play.</td></tr>'}
        </tbody></table>
        <ul class="dt-list">${p.plays.map((x) => `<li><b>${x.rule.replace("_", " ")} ${x.side}:</b> ${esc(x.why)}</li>`).join("")}</ul>
        <div class="muted">These setups also appear on TRADE SIGNALS (setup "opt_wall"), with a notification when the entry triggers.</div></div>
    </div>
    <div class="box"><div class="sec">HOW INSTITUTIONS USE THESE LEVELS</div><ul class="dt-list">
        <li><b>Who moves the market:</b> option dealers hedge their books with futures. Their hedging, not the options themselves, is what makes these levels matter.</li>
        <li><b>Long gamma (positive GEX, above the flip):</b> dealers sell rallies and buy dips. Moves get damped, ranges hold, and the <b>call wall</b> (biggest call strike above) and <b>put wall</b> (biggest put strike below) act as resistance and support. Play: fade the walls, take profits quickly.</li>
        <li><b>Short gamma (negative GEX, below the flip):</b> dealers sell drops and buy rallies, so moves accelerate. Walls stop being walls: a break can run. Play: trade breakouts, keep stops tight.</li>
        <li><b>Gamma flip:</b> where dealer gamma changes sign. A close through it changes how the market behaves; treat it like a regime switch.</li>
        <li><b>Into expiry:</b> gamma piles up at the big strikes and price tends to pin near them (max pain is a rough guide). After a big expiry, the pin lets go and moves often widen.</li>
        <li><b>What desks watch with it:</b> 0DTE/weekly flows in ES/NQ, vanna (IV falling lifts dealers' deltas) and charm (time decay into expiry), and whether open interest is customer-long or customer-short. That last one is the big unknown, so the screen lets you switch the assumption.</li></ul></div>
      <div class="box"><div class="sec">BACKTEST <span class="muted">${bt.sessions} sessions · split at ${bt.split} · per 1 contract after ${usd(bt.rt_cost)} round-trip costs</span></div>
        <table class="tbl"><thead><tr><th>RULE</th><th>ALL (n · avg · t)</th><th>WIN %</th><th>IN-SAMPLE</th><th>OUT-OF-SAMPLE</th><th>LONGS</th><th>SHORTS</th></tr></thead><tbody>
        ${bt.rules.map((r) => `<tr><td class="sym">${esc(r.name)}</td><td>${st4(r.all)}</td><td>${fx(r.all.win_pct, 0)}</td><td>${st4(r.is)}</td><td>${st4(r.oos)}</td><td>${st4(r.longs)}</td><td>${st4(r.shorts)}</td></tr>`).join("")}
        <tr class="hl"><td class="sym">All rules combined</td><td>${st4(bt.combined.all)}</td><td>${fx(bt.combined.all.win_pct, 0)}</td><td></td><td>${st4(bt.combined.oos)}</td><td></td><td></td></tr>
        </tbody></table>
        ${gsLine(bt.combined.equity || [], 1300)}
        <div class="muted"><b>Do the regimes behave as advertised?</b> Average day range: ${fx(bt.regime_check.positive.avg_range_sd, 2)}σ on long-gamma days (${bt.regime_check.positive.n}) vs ${fx(bt.regime_check.negative.avg_range_sd, 2)}σ on short-gamma days (${bt.regime_check.negative.n}). With real data, long-gamma days should be noticeably quieter.</div></div>
    <div class="box"><div class="sec">DATA</div><ul class="dt-list">
      <li>Levels history: ${esc(bt.levels_source)}. ${bt.levels_source.startsWith("SIM") ? '<span class="down">Synthetic levels have no link to real dealer positioning, so these results show the mechanics, not an edge.</span>' : ""}</li>
      <li>Real test: save daily levels as <code>backend/data/options/${p.symbol}_levels.csv</code> (date, call_wall, put_wall, flip, max_pain, gex; optional front_days). Each row is used for the NEXT session. Sources: CME QuikStrike/OI history, Databento CME options, or a GEX data vendor.</li>
      <li>Price bars: ${esc(bt.source)}.</li></ul></div>`;
}

function lxRender() {
  const v = $("#view");
  if (!v || S.view !== "LX") return;
  const d = LX.part === "opt" ? LX.opt : LX.sig;
  const ctl = `<div class="gs-ctl">
    <div class="dt-switch">${OPT_SYMS.map((s) => `<button class="tbtn ${s === LX.sym ? "on" : ""}" data-lxsym="${s}">${s}</button>`).join("")}</div>
    <div class="dt-switch"><button class="tbtn ${LX.part === "opt" ? "on" : ""}" data-lxpart="opt">OPTIONS LEVELS</button><button class="tbtn ${LX.part === "sig" ? "on" : ""}" data-lxpart="sig">σ-EXTREME FADE</button></div>
    ${LX.part === "sig" ? `<label>Compare with <select data-lx="ref"><option value="month" ${LX.ref === "month" ? "selected" : ""}>Same month, earlier years</option><option value="month_weekday" ${LX.ref === "month_weekday" ? "selected" : ""}>Same month &amp; weekday</option><option value="trailing" ${LX.ref === "trailing" ? "selected" : ""}>Last 60 sessions</option></select></label>
      <label>Alert threshold <select data-lx="k">${[2, 2.5, 3].map((k) => `<option value="${k}" ${LX.k === k ? "selected" : ""}>±${k}σ</option>`).join("")}</select></label>` : ""}</div>`;
  if (!d) { v.innerHTML = `<div class="dt">${ctl}<div class="pad muted">${LX.busy ? "Running the backtests…" : ""}</div></div>`; return; }
  if (d.error) { v.innerHTML = `<div class="dt">${ctl}<div class="pad down">${esc(d.error)}</div></div>`; return; }
  const price = LX.part === "opt" ? d.playbook.price : d.today.last;
  v.innerHTML = `<div class="dt">
    <div class="dt-head"><div class="dt-title"><div class="dt-sym">${LX.sym}<span>${LX.part === "opt" ? "futures around key options levels" : "fading statistically extreme moves"}</span></div>
      <div class="headline"><span class="big">${px(LX.sym, price)}</span></div></div></div>
    ${ctl}${LX.part === "opt" ? lxRenderOpt(d) : lxRenderSig(d)}</div>`;
}

async function lxLoad() {
  LX.busy = true;
  if (LX.part === "opt") LX.opt = null; else LX.sig = null;
  lxRender();
  try {
    if (LX.part === "opt") LX.opt = await api(`/api/strategy/optlevels/${LX.sym}`);
    else LX.sig = await api(`/api/strategy/sigma/${LX.sym}?ref=${LX.ref}&k=${LX.k}`);
  } catch (e) {
    const err = { error: String(e.message || e) };
    if (LX.part === "opt") LX.opt = err; else LX.sig = err;
  }
  LX.busy = false; lxRender();
}

VIEWS.LX = async function () {
  setTitle("LEVELS & SIGMA FADE · OPTIONS LEVELS PLAYBOOK · EXTREME-MOVE REVERSION");
  tools("");
  const base = S.universe[S.sel]?.parent || S.sel;
  if (OPT_SYMS.includes(base) && base !== LX.sym) { LX.sym = base; LX.opt = LX.sig = null; }
  const d = LX.part === "opt" ? LX.opt : LX.sig;
  if (d && !d.error) return lxRender();
  await lxLoad();
};
document.addEventListener("click", (ev) => {
  if (S.view !== "LX") return;
  const s = ev.target.closest("[data-lxsym]");
  if (s) { LX.sym = s.dataset.lxsym; LX.opt = LX.sig = null; lxLoad(); return; }
  const p = ev.target.closest("[data-lxpart]");
  if (p) { LX.part = p.dataset.lxpart; const d = LX.part === "opt" ? LX.opt : LX.sig; if (d && !d.error) lxRender(); else lxLoad(); }
});
document.addEventListener("change", (ev) => {
  const k = ev.target.dataset?.lx;
  if (!k || S.view !== "LX") return;
  LX[k] = k === "k" ? Number(ev.target.value) : ev.target.value;
  lxLoad();
});
