/* Gamma Scalp Lab: backtest buying (or selling) an ATM straddle and delta-hedging it with futures. */
"use strict";

const GS = { sym: "NG", dte: 5, side: 1, iv_mode: "rv_mult", iv_value: 1.1, slip: 1, spread: 0.01, d: null, reg: null, busy: false };
const GS_DTE = { NG: 5, ES: 1, NQ: 1, CL: 5, GC: 5 };
const usd = (v) => (v == null ? "—" : (v < 0 ? "-$" : "$") + Math.abs(Math.round(v)).toLocaleString());
const susd = (v) => (v == null ? "—" : (v > 0 ? "+" : "") + usd(v));
const HEDGE_LABEL = { "5m": "Every 5 min", "15m": "Every 15 min", "60m": "Hourly", close: "Once, at the close", band: "Delta band (±0.25)" };

function gsLine(rows) {
  if (!rows.length) return "";
  const W = 560, H = 200, padL = 58, padB = 20, ys = rows.map((r) => r.value);
  const y0 = Math.min(0, ...ys), y1 = Math.max(0, ...ys), span = y1 - y0 || 1;
  const x = (i) => padL + (i / Math.max(1, rows.length - 1)) * (W - padL - 8), y = (v) => 8 + (1 - (v - y0) / span) * (H - padB - 16);
  const pts = rows.map((r, i) => `${x(i).toFixed(1)},${y(r.value).toFixed(1)}`).join(" ");
  const last = ys[ys.length - 1];
  return `<svg viewBox="0 0 ${W} ${H}" class="opt-svg" role="img" aria-label="Cumulative P&L of the gamma scalp, ending at ${usd(last)}">
    <line x1="${padL}" x2="${W - 8}" y1="${y(0)}" y2="${y(0)}" style="stroke:var(--line)"/>
    <polyline fill="none" stroke-width="2" style="stroke:${last >= 0 ? "var(--s1)" : "var(--s2)"}" points="${pts}"/>
    <text x="4" y="${y(y1) + 4}" font-size="9" style="fill:var(--muted)">${usd(y1)}</text><text x="4" y="${y(y0)}" font-size="9" style="fill:var(--muted)">${usd(y0)}</text>
    <text x="${padL}" y="${H - 4}" font-size="9" style="fill:var(--muted)">${rows[0].time}</text><text x="${W - 8}" y="${H - 4}" font-size="9" text-anchor="end" style="fill:var(--muted)">${rows[rows.length - 1].time}</text>
  </svg>`;
}

function gsScatter(pts) {
  if (!pts.length) return "";
  const W = 560, H = 220, padL = 58, padB = 24;
  const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
  const xa = Math.max(...xs.map(Math.abs), 1), ya = Math.max(...ys.map(Math.abs), 1);
  const x = (v) => padL + ((v + xa) / (2 * xa)) * (W - padL - 8), y = (v) => 8 + (1 - (v + ya) / (2 * ya)) * (H - padB - 16);
  return `<svg viewBox="0 0 ${W} ${H}" class="opt-svg" role="img" aria-label="Each cycle: realised minus implied volatility against P&L">
    <line x1="${padL}" x2="${W - 8}" y1="${y(0)}" y2="${y(0)}" style="stroke:var(--line)"/><line x1="${x(0)}" x2="${x(0)}" y1="8" y2="${H - padB - 8}" style="stroke:var(--line)"/>
    ${pts.map((p) => `<circle cx="${x(p.x).toFixed(1)}" cy="${y(p.y).toFixed(1)}" r="3" style="fill:${p.y >= 0 ? "var(--s1)" : "var(--s2)"};opacity:.75"><title>RV−IV ${signed(p.x, 1)} vol pts · P&L ${usd(p.y)}</title></circle>`).join("")}
    <text x="4" y="${y(ya) + 4}" font-size="9" style="fill:var(--muted)">${usd(ya)}</text><text x="4" y="${y(-ya)}" font-size="9" style="fill:var(--muted)">${usd(-ya)}</text>
    <text x="${padL}" y="${H - 4}" font-size="9" style="fill:var(--muted)">RV−IV ${signed(-xa, 0)}</text><text x="${x(0)}" y="${H - 4}" font-size="9" text-anchor="middle" style="fill:var(--muted)">0 vol pts</text>
    <text x="${W - 8}" y="${H - 4}" font-size="9" text-anchor="end" style="fill:var(--muted)">${signed(xa, 0)}</text>
  </svg>`;
}

function gsIvBars(rows, be) {
  if (!rows.length) return '<div class="muted">The IV sweep runs when the IV mode is "realised vol × multiplier".</div>';
  const W = 560, H = 190, padL = 58, padB = 22, bw = (W - padL - 8) / rows.length;
  const ya = Math.max(...rows.map((r) => Math.abs(r.avg || 0)), 1), mid = 8 + (H - padB - 16) / 2;
  const h = (v) => (Math.abs(v) / ya) * ((H - padB - 16) / 2);
  return `<svg viewBox="0 0 ${W} ${H}" class="opt-svg" role="img" aria-label="Average P&L per cycle for each implied-volatility assumption">
    ${rows.map((r, i) => `<rect x="${padL + i * bw + 6}" y="${r.avg >= 0 ? mid - h(r.avg) : mid}" width="${bw - 12}" height="${Math.max(1, h(r.avg))}" rx="2" style="fill:${r.avg >= 0 ? "var(--s1)" : "var(--s2)"}"><title>IV = RV × ${r.mult}: avg ${usd(r.avg)} per cycle, t ${fx(r.t_stat, 2)}</title></rect>
      <text x="${padL + i * bw + bw / 2}" y="${H - 6}" font-size="10" text-anchor="middle" style="fill:var(--muted)">×${r.mult.toFixed(1)}</text>
      <text x="${padL + i * bw + bw / 2}" y="${r.avg >= 0 ? mid - h(r.avg) - 3 : mid + h(r.avg) + 10}" font-size="9" text-anchor="middle" style="fill:var(--ink)">${usd(r.avg)}</text>`).join("")}
    <line x1="${padL}" x2="${W - 8}" y1="${mid}" y2="${mid}" style="stroke:var(--line)"/>
    ${be != null ? (() => { const i = rows.findIndex((r) => r.mult > be) - 1, bx = padL + (i + 0.5 + (be - rows[i].mult) / (rows[i + 1].mult - rows[i].mult)) * bw;
      return `<line x1="${bx}" x2="${bx}" y1="8" y2="${H - padB}" style="stroke:var(--warn)" stroke-dasharray="4 3"/><text x="${bx + 3}" y="16" font-size="10" style="fill:var(--warn)">BREAKEVEN ×${be}</text>`; })() : ""}
    <text x="4" y="${mid - (H - padB - 16) / 2 + 8}" font-size="9" style="fill:var(--muted)">${usd(ya)}</text><text x="4" y="${mid + (H - padB - 16) / 2}" font-size="9" style="fill:var(--muted)">${usd(-ya)}</text>
  </svg>`;
}

function gsRender() {
  const v = $("#view");
  if (!v || S.view !== "GS") return;
  const d = GS.d, st = d?.detail?.stats || {};
  const sel = (k, opts) => `<select data-gs="${k}">${opts.map(([val, lab]) => `<option value="${val}" ${String(GS[k]) === String(val) ? "selected" : ""}>${lab}</option>`).join("")}</select>`;
  const controls = `<div class="gs-ctl">
    <div class="dt-switch">${OPT_SYMS.map((s) => `<button class="tbtn ${s === GS.sym ? "on" : ""}" data-gssym="${s}">${s}</button>`).join("")}</div>
    <label>Position ${sel("side", [[1, "LONG gamma (buy straddle, scalp)"], [-1, "SHORT gamma (sell straddle, hedge)"]])}</label>
    <label>Days to expiry ${sel("dte", [[1, "1 (0DTE-style daily)"], [2, "2"], [5, "5 (weekly)"], [10, "10"], [20, "20 (monthly)"]])}</label>
    <label>Implied vol ${sel("iv_mode", [["rv_mult", "Realised vol × multiplier"], ["fixed", "Fixed IV"], ["csv", "From CSV (real IV history)"]])}</label>
    <label>${GS.iv_mode === "fixed" ? "IV (decimal)" : "Multiplier"} <input data-gs="iv_value" type="number" step="0.05" min="0.05" max="3" value="${GS.iv_value}" style="width:70px"></label>
    <label>Slippage (ticks) <input data-gs="slip" type="number" step="0.5" min="0" max="5" value="${GS.slip}" style="width:60px"></label>
    <label>Option spread (vol pts) <input data-gs="spread" type="number" step="0.5" min="0" max="5" value="${(GS.spread * 100).toFixed(1)}" style="width:60px"></label>
    <button class="tbtn on" data-gsrun>${GS.busy ? "RUNNING…" : "RUN BACKTEST"}</button></div>`;
  if (!d) { v.innerHTML = `<div class="dt">${controls}<div class="pad muted">${GS.busy ? "Running 11 backtests (5 hedge schedules + 6 IV assumptions)…" : ""}</div></div>`; return; }
  if (d.error) { v.innerHTML = `<div class="dt">${controls}<div class="pad down">${esc(d.error)}</div></div>`; return; }
  const sim = String(d.source).includes("SIM");
  const cyc = (d.detail.cycles || []).slice(-15).reverse();
  const reg = GS.reg;
  v.innerHTML = `<div class="dt">
    <div class="dt-head"><div class="dt-title"><div class="dt-sym">${d.symbol}<span>gamma scalping backtest · ${esc(d.side)} · ${d.dte}-day ATM straddle</span></div>
      <div class="headline"><span class="badge ${sim ? "sim" : "live"}">${sim ? "SIMULATED 1-MIN BARS" : esc(d.source)}</span><span class="muted">${esc(d.iv_label || "")}</span></div></div></div>
    ${controls}
    <div class="tiles">
      ${tile("Cycles", st.n)}${tile("Avg P&L / cycle", susd(st.avg), cls(st.avg))}${tile("Total", susd(st.total), cls(st.total))}${tile("Win rate", fx(st.win_pct, 0) + "%")}
      ${tile("t-stat", fx(st.t_stat, 2), Math.abs(st.t_stat) >= 2 ? cls(st.t_stat) : "")}${tile("Sharpe (ann.)", fx(st.sharpe, 2))}
      ${tile("Avg IV paid", fx(st.avg_iv, 1) + "%")}${tile("Avg RV delivered", fx(st.avg_rv, 1) + "%", st.avg_rv > st.avg_iv ? "up" : "down")}
      ${tile("Best hedge", HEDGE_LABEL[d.best_hedge] || d.best_hedge)}${tile("Breakeven IV", d.breakeven_mult == null ? "none in ×0.8–1.3" : "RV × " + d.breakeven_mult)}
      ${tile("Options P&L", susd(st.option_pnl), cls(st.option_pnl))}${tile("Hedging P&L", susd(st.hedge_pnl), cls(st.hedge_pnl))}${tile("Corr(P&L, RV−IV)", fx(st.corr_pnl_rv_minus_iv, 2))}
    </div>
    <div class="dt-row dt-r2b">
      <div class="box"><div class="sec">EQUITY CURVE <span class="muted">best hedge schedule, 1 straddle per cycle, after costs</span></div>${gsLine(d.detail.equity || [])}</div>
      <div class="box"><div class="sec">P&amp;L vs REALISED − IMPLIED VOL <span class="muted">one dot per cycle</span></div>${gsScatter(d.detail.scatter || [])}
        <div class="muted">Long gamma makes money when the market moves more than the options priced in (dots right of zero), and pays theta when it doesn't.</div></div>
    </div>
    <div class="dt-row dt-r2b">
      <div class="box"><div class="sec">HEDGE FREQUENCY <span class="muted">same straddles, different re-hedging schedules</span></div>
        <table class="tbl"><thead><tr><th>HEDGE</th><th>CYCLES</th><th>AVG</th><th>WIN %</th><th>t</th><th>HEDGE TRADES</th><th>COSTS / CYCLE</th><th>OPTION P&amp;L</th><th>HEDGE P&amp;L</th></tr></thead><tbody>
        ${d.by_hedge.map((r) => `<tr class="${r.hedge === d.best_hedge ? "hl" : ""}"><td class="sym">${HEDGE_LABEL[r.hedge] || r.hedge}${r.hedge === d.best_hedge ? " ★" : ""}</td><td>${r.n}</td><td class="${cls(r.avg)}">${susd(r.avg)}</td><td>${fx(r.win_pct, 0)}</td>
          <td>${fx(r.t_stat, 2)}</td><td>${fx(r.avg_hedge_trades, 1)}</td><td>${usd(r.avg_costs)}</td><td class="${cls(r.option_pnl)}">${susd(r.option_pnl)}</td><td class="${cls(r.hedge_pnl)}">${susd(r.hedge_pnl)}</td></tr>`).join("")}
        </tbody></table>
        <div class="muted">Hedging more often locks in more of the realised move but pays more slippage. Hedging only at the close lets intraday swings come and go.</div></div>
      <div class="box"><div class="sec">HOW RICH CAN THE OPTIONS BE? <span class="muted">avg P&amp;L per cycle vs the IV you pay</span></div>${gsIvBars(d.by_iv, d.breakeven_mult)}
        <div class="muted">${d.breakeven_mult == null ? "No sign change across the tested range." : `Above IV ≈ realised × ${d.breakeven_mult} the ${GS.side > 0 ? "long" : "short"}-gamma trade stops paying. Compare with the live ATM IV on the Options &amp; Gamma tab before buying straddles.`}</div></div>
    </div>
    <div class="box"><div class="sec">RECENT CYCLES</div>
      <table class="tbl"><thead><tr><th>START</th><th>END</th><th>STRIKE</th><th>IV</th><th>RV</th><th>PREMIUM</th><th>OPTION P&amp;L</th><th>HEDGE P&amp;L</th><th>COSTS</th><th>NET</th><th>HEDGES</th></tr></thead><tbody>
      ${cyc.map((c) => `<tr><td>${c.start}</td><td>${c.end}</td><td class="sym">${px(d.symbol, c.strike)}</td><td>${fx(c.iv, 1)}%</td><td class="${c.rv > c.iv ? "up" : "down"}">${fx(c.rv, 1)}%</td><td>${usd(c.premium_usd)}</td>
        <td class="${cls(c.option_pnl)}">${susd(c.option_pnl)}</td><td class="${cls(c.hedge_pnl)}">${susd(c.hedge_pnl)}</td><td>${usd(c.costs)}</td><td class="${cls(c.pnl)}"><b>${susd(c.pnl)}</b></td><td>${fx(c.hedge_trades, 0)}</td></tr>`).join("")}
      </tbody></table></div>
    ${reg ? `<div class="box"><div class="sec">FUTURES DAY TRADING BY DEALER GAMMA REGIME <span class="muted">${esc(reg.theory)}</span></div>
      <table class="tbl"><thead><tr><th>STRATEGY</th><th>TF</th><th>+γ DAYS (n)</th><th>+γ AVG</th><th>+γ WIN %</th><th>+γ t</th><th>−γ DAYS (n)</th><th>−γ AVG</th><th>−γ WIN %</th><th>−γ t</th></tr></thead><tbody>
      ${reg.rows.map((r) => `<tr><td class="sym">${esc(r.name)}</td><td>${r.tf}m</td>${["positive", "negative"].map((k) => `<td>${r[k].n}</td><td class="${cls(r[k].avg)}">${susd(r[k].avg)}</td><td>${fx(r[k].win_pct, 0)}</td><td>${fx(r[k].t, 2)}</td>`).join("")}</tr>`).join("")}
      </tbody></table>
      <div class="muted">Gamma history: ${esc(reg.gex_source)}. ${reg.gex_source.startsWith("SIM") ? '<span class="down">With synthetic gamma the split is random, so don\'t read anything into it.</span>' : ""} Save daily dealer gamma as <code>backend/data/options/${reg.symbol}_gex.csv</code> (date, gex) to test it for real.</div></div>` : ""}
    <div class="cols" style="padding:0">
      <div class="box"><div class="sec">HOW THE BACKTEST WORKS</div><ul class="dt-list">
        <li>Each cycle buys (or sells) one at-the-money straddle at the regular-session open, priced with Black-76 at the assumed IV, and holds it for ${d.dte} session${d.dte > 1 ? "s" : ""} to expiry.</li>
        <li>The straddle's delta is hedged with futures on the chosen schedule. Every hedge pays slippage and commission; the option pays half the bid/ask (in vol points) on entry.</li>
        <li>P&amp;L ≈ ½·Γ·F²·(σ²<sub>realised</sub> − σ²<sub>implied</sub>)·t: long gamma wins when the market moves more than priced, short gamma wins when it moves less.</li>
        <li>Cycles run back to back with no overlap. The IV is set before each cycle starts, using only past data.</li></ul></div>
      <div class="box"><div class="sec">READ THIS BEFORE TRADING IT</div><ul class="dt-list">
        <li><b>The IV is an assumption.</b> There is no free history of futures-option implied vol, so the default prices options at trailing realised vol × a multiplier. Real options usually trade above realised vol (the variance risk premium), which is why long gamma tends to lose on average and short gamma tends to win, with painful tails.</li>
        <li>Real IV: save <code>backend/data/options/${d.symbol}_iv.csv</code> (date, iv) from CME QuikStrike, Databento or your broker, then choose "From CSV".</li>
        <li>${sim ? "<span class=\"down\">These results use simulated 1-minute bars and show how the method works, not a real edge.</span> Import real 1-minute bars for real conclusions." : "Uses your imported 1-minute bars."}</li>
        <li>Short gamma has unlimited risk on gaps. Size it for the worst cycle (${usd(st.worst)} here), not the average.</li></ul></div>
    </div></div>`;
}

async function gsRun() {
  if (GS.busy) return;
  GS.busy = true; GS.d = null; gsRender();
  const q = `symbol=${GS.sym}&dte=${GS.dte}&side=${GS.side}&iv_mode=${GS.iv_mode}&iv_value=${GS.iv_value}&slip_ticks=${GS.slip}&opt_spread_vol=${GS.spread}`;
  try {
    const [d, reg] = await Promise.all([api(`/api/backtest/gammascalp?${q}`), api(`/api/backtest/gamma-regime?symbol=${GS.sym}`).catch(() => null)]);
    GS.d = d; GS.reg = reg;
  } catch (e) {
    GS.d = { error: String(e.message || e) };
  }
  GS.busy = false; gsRender();
}

VIEWS.GS = async function (v) {
  setTitle("GAMMA SCALP LAB · STRADDLE + DELTA-HEDGE BACKTEST");
  tools("");
  const base = S.universe[S.sel]?.parent || S.sel;
  if (OPT_SYMS.includes(base) && base !== GS.sym) { GS.sym = base; GS.dte = GS_DTE[base]; }
  if (GS.d && GS.d.symbol === GS.sym) return gsRender();
  await gsRun();
};
document.addEventListener("click", (ev) => {
  if (S.view !== "GS") return;
  const s = ev.target.closest("[data-gssym]");
  if (s) { GS.sym = s.dataset.gssym; GS.dte = GS_DTE[GS.sym]; gsRun(); return; }
  if (ev.target.closest("[data-gsrun]")) gsRun();
});
document.addEventListener("change", (ev) => {
  const k = ev.target.dataset?.gs;
  if (!k || S.view !== "GS") return;
  const val = ev.target.value;
  if (k === "iv_mode") { GS.iv_mode = val; GS.iv_value = val === "fixed" ? 0.5 : 1.1; gsRender(); return; }
  if (k === "spread") GS.spread = Number(val) / 100;
  else GS[k] = k === "iv_mode" ? val : Number(val);
  if (k === "side" || k === "dte") gsRun();
});
