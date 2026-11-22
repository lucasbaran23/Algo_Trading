/* Backtest Lab (F9): run, compare, optimise, walk-forward and Monte Carlo strategies. */
"use strict";

const BT = {
  strategies: [], presets: {}, cfg: null, tab: "SUMMARY",
  result: null, opt: null, wf: null, mc: null, compare: [], busy: false,
};
const btColors = () => [C("s1"), C("s2"), C("s3"), C("s4")]; // theme series, validated vs each theme's surface
const BT_STYLES = [0, 2, 1, 3];                                  // solid / dashed / dotted / large dash
const BT_DEFAULT = {
  symbol: "CL", strategy: "sma_cross", params: {}, sizing: "fixed", contracts: 1, risk_pct: 0.5,
  max_contracts: 10, account_size: 50000, commission: 2.5, slippage_ticks: 1, stop_atr: 0, target_atr: 0,
  start: "", end: "", prop_preset: "eval_50k_eod", ranges: {}, objective: "sharpe", oos_pct: 30,
  train_days: 504, test_days: 126, anchored: false, n_sims: 1000,
};
const OBJECTIVES = { sharpe: "Sharpe", sortino: "Sortino", calmar: "Calmar", net_pnl: "Net P&L", profit_factor: "Profit factor" };

function btLoadCfg() {
  try { return { ...BT_DEFAULT, ...JSON.parse(localStorage.getItem("bt.cfg") || "{}") }; } catch { return { ...BT_DEFAULT }; }
}
function btSaveCfg() {
  try { localStorage.setItem("bt.cfg", JSON.stringify(BT.cfg)); } catch { /* storage unavailable */ }
}
const btStrat = () => BT.strategies.find((s) => s.key === BT.cfg.strategy) || BT.strategies[0];

// ------------------------------------------------------------------ config form
function btParamRows(strat) {
  return strat.params.map((p) => {
    const v = BT.cfg.params[p.name] ?? p.default;
    const r = BT.cfg.ranges[p.name];
    const on = !!r;
    if (p.type === "choice") {
      return `<tr><td>${esc(p.label)}</td>
        <td><select data-param="${p.name}">${p.choices.map((c) => `<option ${c === v ? "selected" : ""}>${c}</option>`).join("")}</select></td>
        <td><label class="opt"><input type="checkbox" data-opt="${p.name}" ${on ? "checked" : ""}> opt</label></td>
        <td colspan="3" class="muted">${on ? "tests all choices" : ""}</td></tr>`;
    }
    const [lo, hi, st] = r || [p.min, p.max, p.step];
    return `<tr><td>${esc(p.label)}</td>
      <td><input type="number" data-param="${p.name}" value="${v}" step="${p.step}" min="${p.min}" max="${p.max}"></td>
      <td><label class="opt"><input type="checkbox" data-opt="${p.name}" ${on ? "checked" : ""}> opt</label></td>
      <td><input type="number" data-rng="${p.name}:0" value="${lo}" step="${p.step}" ${on ? "" : "disabled"} aria-label="${p.label} min"></td>
      <td><input type="number" data-rng="${p.name}:1" value="${hi}" step="${p.step}" ${on ? "" : "disabled"} aria-label="${p.label} max"></td>
      <td><input type="number" data-rng="${p.name}:2" value="${st}" step="${p.step}" ${on ? "" : "disabled"} aria-label="${p.label} step"></td></tr>`;
  }).join("");
}
function btCombos() {
  const strat = btStrat();
  let n = 1;
  for (const [k, r] of Object.entries(BT.cfg.ranges)) {
    const p = strat.params.find((x) => x.name === k);
    if (!p) continue;
    if (p.type === "choice") n *= p.choices.length;
    else n *= Math.max(1, Math.floor((r[1] - r[0]) / r[2] + 1e-9) + 1);
  }
  return Object.keys(BT.cfg.ranges).length ? n : 0;
}
function btConfigHtml() {
  const c = BT.cfg, strat = btStrat();
  const fam = [...new Set(BT.strategies.map((s) => s.family))];
  const num = (k, label, step = "any", extra = "") => `<label>${label}<input type="number" data-cfg="${k}" value="${c[k]}" step="${step}" ${extra}></label>`;
  return `<div class="bt-config">
    <div class="box"><div class="sec">STRATEGY</div>
      <div class="form">
        <label>Market<select data-cfg="symbol">${Object.keys(S.universe).map((k) => `<option ${k === c.symbol ? "selected" : ""}>${k}</option>`).join("")}</select></label>
        <label style="grid-column: span 2">Strategy<select data-cfg="strategy">${fam.map((f) => `<optgroup label="${f}">${BT.strategies.filter((s) => s.family === f).map((s) => `<option value="${s.key}" ${s.key === c.strategy ? "selected" : ""}>${esc(s.name)}</option>`).join("")}</optgroup>`).join("")}</select></label>
      </div>
      <div class="muted" style="margin:4px 0">${esc(strat.description)}</div>
      <table class="tbl bt-params"><thead><tr><th>PARAMETER</th><th>VALUE</th><th>OPTIMISE</th><th>MIN</th><th>MAX</th><th>STEP</th></tr></thead><tbody>${btParamRows(strat)}</tbody></table>
      <div class="muted" id="btCombos" style="margin-top:4px"></div>
    </div>
    <div class="box"><div class="sec">EXECUTION &amp; RISK</div>
      <div class="form">
        <label>Sizing<select data-cfg="sizing"><option value="fixed" ${c.sizing === "fixed" ? "selected" : ""}>Fixed contracts</option><option value="risk" ${c.sizing === "risk" ? "selected" : ""}>Risk % per ATR</option></select></label>
        ${c.sizing === "fixed" ? num("contracts", "Contracts", 1, 'min="1"') : num("risk_pct", "Risk % / ATR", 0.1)}
        ${num("max_contracts", "Max contracts", 1)}
        ${num("account_size", "Account $", 1000)}
        ${num("commission", "Commission / side", 0.01)}
        ${num("slippage_ticks", "Slippage ticks / side", 0.5)}
        ${num("stop_atr", "Stop (ATR, 0=off)", 0.25)}
        ${num("target_atr", "Target (ATR, 0=off)", 0.25)}
        <label>From<input type="date" data-cfg="start" value="${c.start || ""}"></label>
        <label>To<input type="date" data-cfg="end" value="${c.end || ""}"></label>
        <label style="grid-column: span 2">Prop rules for eval sim<select data-cfg="prop_preset"><option value="">None</option>${Object.entries(BT.presets).map(([k, p]) => `<option value="${k}" ${k === c.prop_preset ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></label>
      </div>
      <div class="sec">OPTIMISATION</div>
      <div class="form">
        <label>Objective<select data-cfg="objective">${Object.entries(OBJECTIVES).map(([k, v]) => `<option value="${k}" ${k === c.objective ? "selected" : ""}>${v}</option>`).join("")}</select></label>
        ${num("oos_pct", "Out-of-sample %", 5)}
        ${num("train_days", "WF train days", 21)}
        ${num("test_days", "WF test days", 21)}
        <label>WF window<select data-cfg="anchored"><option value="false" ${!c.anchored ? "selected" : ""}>Rolling</option><option value="true" ${c.anchored ? "selected" : ""}>Anchored</option></select></label>
        ${num("n_sims", "MC simulations", 100)}
      </div>
      <div class="row-actions">
        <button class="btn amber" data-bt="run">▶ RUN</button>
        <button class="btn" data-bt="optimize">OPTIMISE</button>
        <button class="btn" data-bt="walkforward">WALK-FORWARD</button>
        <button class="btn" data-bt="montecarlo">MONTE CARLO</button>
        <button class="btn" data-bt="compare" ${BT.result ? "" : "disabled"}>+ COMPARE</button>
        <button class="btn" data-bt="clearcmp" ${BT.compare.length ? "" : "disabled"}>CLEAR (${BT.compare.length})</button>
        <span id="btBusy" class="muted"></span>
      </div>
    </div></div>`;
}
function btUpdateCombos() {
  const n = btCombos(), el = $("#btCombos");
  if (!el) return;
  el.innerHTML = n ? `Grid: <b class="${n > 600 ? "down" : "amber"}">${n}</b> combinations${n > 600 ? " (max 600: widen the steps)" : ""}` : "Tick “opt” on parameters to optimise them.";
}

// ------------------------------------------------------------------ results
const tile = (label, v, cl = "") => `<div class="tile"><span>${label}</span><b class="${cl}">${v}</b></div>`;
function btTiles(m, bm) {
  return `<div class="tiles">
    ${tile("Net P&L", money(m.net_pnl), cls(m.net_pnl))}
    ${tile("Ann. return", fx(m.ann_return_pct, 1) + "%", cls(m.ann_return_pct))}
    ${tile("Sharpe", fx(m.sharpe, 2), cls(m.sharpe))}
    ${tile("Sortino", fx(m.sortino, 2))}
    ${tile("Max DD", money(-m.max_dd), "down")}
    ${tile("Calmar", fx(m.calmar, 2))}
    ${tile("Trades", m.trades ?? "—")}
    ${tile("Win rate", fx(m.win_rate_pct, 1) + "%")}
    ${tile("Profit factor", fx(m.profit_factor, 2))}
    ${tile("Avg trade", money(m.avg_trade), cls(m.avg_trade))}
    ${tile("Exposure", fx(m.exposure_pct, 0) + "%")}
    ${tile("Buy & hold", money(bm?.net_pnl), cls(bm?.net_pnl))}
  </div>`;
}
function btPropBox(pe) {
  if (!pe) return "";
  return `<div class="box"><div class="sec">PROP EVALUATION SIMULATION <span class="muted">${esc(pe.rules.name)}</span></div>
    <div class="muted">A new evaluation started every 5 trading days (${pe.attempts} attempts, ${pe.max_days}-day limit), replaying this strategy's daily P&amp;L through the rules.</div>
    <div class="stack" role="img" aria-label="Pass ${pe.pass_pct}%, drawdown fail ${pe.fail_drawdown_pct}%, daily loss fail ${pe.fail_daily_pct}%, timeout ${pe.timeout_pct}%">
      <i class="ok-fill" style="width:${pe.pass_pct}%"></i><i class="bad-fill" style="width:${pe.fail_drawdown_pct}%"></i><i class="warn-fill" style="width:${pe.fail_daily_pct}%"></i><i class="na-fill" style="width:${pe.timeout_pct}%"></i></div>
    <div class="kv">
      <span><i class="sw ok-fill"></i>Passed</span><span>${fx(pe.pass_pct, 1)}%${pe.median_days_to_pass ? ` · median ${pe.median_days_to_pass} days` : ""}</span>
      <span><i class="sw bad-fill"></i>Failed: max drawdown</span><span>${fx(pe.fail_drawdown_pct, 1)}%</span>
      <span><i class="sw warn-fill"></i>Failed: daily loss</span><span>${fx(pe.fail_daily_pct, 1)}%</span>
      <span><i class="sw na-fill"></i>Timed out</span><span>${fx(pe.timeout_pct, 1)}%</span>
    </div><div class="muted" style="margin-top:4px">Daily bars can't see intraday swings, so real pass rates will be lower.</div></div>`;
}
function btEquityChart(el, extra) {
  const r = BT.result;
  el.innerHTML = `<div class="legend" id="btLeg"></div><div class="chart" id="btEq" style="min-height:260px"></div>
    <div class="legend"><span><i style="background:var(--down)"></i>Drawdown ($)</span></div><div class="chart small" id="btDd" style="flex-basis:130px;min-height:130px"></div>${extra || ""}`;
  const usd = { localization: { priceFormatter: (v) => money(v) } };
  const ch = makeChart($("#btEq", el), usd);
  const series = [];
  const add = (label, data, color, style, width = 2) => {
    const s = ch.addLineSeries({ color, lineWidth: width, lineStyle: style, priceLineVisible: false, lastValueVisible: false });
    s.setData(data);
    series.push({ label, s, color });
  };
  add("Buy & hold", r.benchmark, C("muted"), 1, 1);
  BT.compare.forEach((c, i) => add(c.label, c.equity, btColors()[i + 1], BT_STYLES[i + 1]));
  add(btLabel(r.config), r.equity, btColors()[0], 0);
  const base = series.slice().reverse().map((x) => `<span><i style="background:${x.color}"></i>${esc(x.label)}</span>`).join("");
  const leg = $("#btLeg", el); leg.dataset.base = base; leg.innerHTML = base;
  readout(ch, leg, (p) => `<span>${p.time}</span>` + series.slice().reverse().map((x) => {
    const d = p.seriesData.get(x.s); return d ? `<span><i style="background:${x.color}"></i>${esc(x.label)} ${money(d.value)}</span>` : "";
  }).join(""));
  ch.timeScale().fitContent();
  const dd = makeChart($("#btDd", el), usd);
  const a = dd.addAreaSeries({ lineColor: C("down"), topColor: CA("down-rgb", 0.05), bottomColor: CA("down-rgb", 0.35), lineWidth: 1, priceLineVisible: false, invertFilledArea: true });
  a.setData(r.drawdown);
  dd.timeScale().fitContent();
}
function btLabel(c) {
  const p = Object.entries(c.params).filter(([k]) => k !== "mode").map(([, v]) => v).join("/");
  return `${c.symbol} ${c.strategy} ${p}`;
}
function btMonthly(rows) {
  const mx = Math.max(1, ...rows.flatMap((r) => r.months.filter((v) => v != null).map(Math.abs)));
  const cell = (v) => {
    if (v == null) return '<td class="muted">·</td>';
    const a = Math.min(1, Math.abs(v) / mx) * 0.6;
    return `<td style="background:${v >= 0 ? `rgb(var(--up-rgb) / ${a})` : `rgb(var(--down-rgb) / ${a})`}">${Math.round(v).toLocaleString()}</td>`;
  };
  return `<table class="tbl"><thead><tr><th>YEAR</th>${"JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(" ").map((m) => `<th>${m}</th>`).join("")}<th>TOTAL</th></tr></thead><tbody>
    ${rows.map((r) => `<tr><td class="sym">${r.year}</td>${r.months.map(cell).join("")}<td class="${cls(r.total)}"><b>${Math.round(r.total).toLocaleString()}</b></td></tr>`).join("")}</tbody></table>
    <div class="pad muted">Monthly P&amp;L in $. Colour depth is relative to the largest month.</div>`;
}
function btTrades(tr) {
  return `<table class="tbl"><thead><tr><th>#</th><th>ENTRY</th><th>EXIT</th><th>SIDE</th><th>QTY</th><th>ENTRY PX</th><th>EXIT PX</th><th>BARS</th><th>EXIT</th><th>P&amp;L</th></tr></thead><tbody>
    ${tr.map((t, i) => `<tr><td>${i + 1}</td><td>${t.entry_date}</td><td>${t.exit_date}</td><td class="${t.side === "LONG" ? "up" : "down"}">${t.side}</td><td>${t.qty}</td><td>${px(BT.result.config.symbol, t.entry)}</td><td>${px(BT.result.config.symbol, t.exit)}</td><td>${t.bars}</td><td>${t.reason}</td><td class="${cls(t.pnl)}">${money(t.pnl)}</td></tr>`).join("")}
    </tbody></table>`;
}
function btOptHtml(o) {
  const d = o.diagnostics, names = o.param_names, ob = OBJECTIVES[o.objective];
  const verdict = d.is_oos_rank_corr == null ? "" : d.is_oos_rank_corr >= 0.5 ? '<span class="up">robust: in-sample ranking carries over</span>'
    : d.is_oos_rank_corr >= 0.2 ? '<span class="amber">partly robust</span>' : '<span class="down">likely curve-fit: in-sample ranking does not predict out-of-sample</span>';
  let heat = "";
  if (o.heatmap) {
    const h = o.heatmap, vals = h.z.flat().filter((v) => v != null && !Number.isNaN(v));
    const mx = Math.max(1e-9, ...vals.map(Math.abs));
    const bp = o.best.params;
    heat = `<div class="box"><div class="sec">HEATMAP · in-sample ${ob} <span class="muted">best over other params · ${esc(h.y)} ↓ × ${esc(h.x)} →</span></div>
      <div class="scroll"><table class="tbl heat"><thead><tr><th>${esc(h.y)} \\ ${esc(h.x)}</th>${h.xs.map((x) => `<th>${x}</th>`).join("")}</tr></thead><tbody>
      ${h.ys.map((y, j) => `<tr><td class="sym">${y}</td>${h.z[j].map((v, i) => {
        if (v == null || Number.isNaN(v)) return '<td class="muted">·</td>';
        const a = Math.min(1, Math.abs(v) / mx) * 0.65;
        const best = bp[h.x] === h.xs[i] && bp[h.y] === y;
        return `<td class="${best ? "best" : ""}" style="background:${v >= 0 ? `rgb(var(--up-rgb) / ${a})` : `rgb(var(--down-rgb) / ${a})`}" title="${h.x}=${h.xs[i]}, ${h.y}=${y}: ${v}">${fx(v, 2)}</td>`;
      }).join("")}</tr>`).join("")}</tbody></table></div>
      <div class="muted">Look for a broad plateau of good values, not a single bright cell. Isolated peaks rarely survive live trading.</div></div>`;
  }
  return `<div class="box"><div class="sec">OPTIMISATION · ${o.combos} combos${o.invalid_combos ? ` (+${o.invalid_combos} that never traded, excluded)` : ""} · objective ${ob} · OOS from ${o.split_date || "—"} (${o.oos_pct}%)</div>
      <div class="kv">
        <span>In-sample vs out-of-sample rank correlation</span><span>${d.is_oos_rank_corr ?? "—"} ${verdict}</span>
        <span>Share of combos profitable in-sample</span><span>${fx(d.profitable_share_pct, 0)}%</span>
        <span>Best combo: OOS / IS ${ob}</span><span>${d.best_oos_vs_is ?? "—"}</span>
      </div></div>
    ${heat}
    <div class="box"><div class="sec">TOP RESULTS <span class="muted">ranked by in-sample ${ob} · click a row to load &amp; run it</span></div>
    <div class="scroll"><table class="tbl"><thead><tr><th>#</th>${names.map((n) => `<th>${esc(n.toUpperCase())}</th>`).join("")}<th>IS ${ob.toUpperCase()}</th><th>IS NET</th><th>IS MAX DD</th><th>IS TRADES</th><th>OOS ${ob.toUpperCase()}</th><th>OOS NET</th><th>OOS MAX DD</th></tr></thead><tbody>
    ${o.results.map((r, i) => `<tr class="row" data-btparams='${esc(JSON.stringify(r.params))}'><td>${i + 1}</td>${names.map((n) => `<td>${r.params[n]}</td>`).join("")}
      <td>${fx(r.is_score, 2)}</td><td class="${cls(r.is.net_pnl)}">${money(r.is.net_pnl)}</td><td>${money(-r.is.max_dd)}</td><td>${r.is.trades}</td>
      <td class="${cls(r.oos_score)}">${fx(r.oos_score, 2)}</td><td class="${cls(r.oos.net_pnl)}">${money(r.oos.net_pnl)}</td><td>${money(-r.oos.max_dd)}</td></tr>`).join("")}
    </tbody></table></div></div>`;
}
function btWfHtml(w) {
  const eff = w.wf_efficiency;
  return `<div class="box"><div class="sec">WALK-FORWARD · ${w.folds.length} folds · stitched out-of-sample</div>
    <div class="tiles">${tile("OOS net", money(w.oos_metrics.net_pnl), cls(w.oos_metrics.net_pnl))}${tile("OOS Sharpe", fx(w.oos_metrics.sharpe, 2), cls(w.oos_metrics.sharpe))}
      ${tile("OOS max DD", money(-w.oos_metrics.max_dd), "down")}${tile("WF efficiency", eff == null ? "—" : fx(eff, 2), eff >= 0.5 ? "up" : "down")}</div>
    <div class="muted">WF efficiency = average annualised out-of-sample return ÷ in-sample. Above 0.5 is generally considered robust; near 0 or negative means the optimisation didn't generalise.</div></div>
    <div class="legend"><span><i style="background:var(--s1)"></i>Walk-forward out-of-sample equity</span></div><div class="chart small" id="wfEq"></div>
    <div class="box"><div class="sec">FOLDS <span class="muted">parameter stability: ${Object.entries(w.param_stability).map(([k, v]) => `${esc(k)} ∈ {${v.join(", ")}}`).join(" · ")}</span></div>
    <div class="scroll"><table class="tbl"><thead><tr><th>TRAIN</th><th>TEST</th><th>CHOSEN PARAMS</th><th>IS SHARPE</th><th>OOS SHARPE</th><th>OOS NET</th><th>OOS TRADES</th></tr></thead><tbody>
    ${w.folds.map((f) => `<tr><td>${f.train[0]} → ${f.train[1]}</td><td>${f.test[0]} → ${f.test[1]}</td><td>${esc(Object.entries(f.params).map(([k, v]) => `${k}=${v}`).join(" "))}</td>
      <td>${fx(f.is.sharpe, 2)}</td><td class="${cls(f.oos.sharpe)}">${fx(f.oos.sharpe, 2)}</td><td class="${cls(f.oos.net_pnl)}">${money(f.oos.net_pnl)}</td><td>${f.oos.trades}</td></tr>`).join("")}
    </tbody></table></div></div>`;
}
function btMcHtml(m) {
  if (m.error) return `<div class="notice">${esc(m.error)}</div>`;
  const all = m.paths.flat();
  const lo = Math.min(...all, 0), hi = Math.max(...all, 0), W = 640, H = 220;
  const y = (v) => H - ((v - lo) / (hi - lo || 1)) * (H - 10) - 5;
  const lines = m.paths.map((p) => `<polyline fill="none" stroke=C("s1") stroke-opacity="0.35" stroke-width="1" points="${p.map((v, i) => `${(i / (p.length - 1)) * W},${y(v)}`).join(" ")}"/>`).join("");
  const row = (label, o) => `<tr><td>${label}</td>${["5", "25", "50", "75", "95"].map((q) => `<td>${money(o[q])}</td>`).join("")}</tr>`;
  return `<div class="box"><div class="sec">MONTE CARLO · ${m.sims} reshuffles of ${m.trades_per_sim} trades</div>
    <div class="tiles">${tile("P(loss)", fx(m.prob_loss_pct, 1) + "%", m.prob_loss_pct > 20 ? "down" : "")}
      ${m.prob_breach_max_dd_pct != null ? tile(`P(DD ≥ ${money(m.prop_max_drawdown)})`, fx(m.prob_breach_max_dd_pct, 1) + "%", m.prob_breach_max_dd_pct > 20 ? "down" : "up") : ""}
      ${tile("Median max DD", money(-m.max_dd["50"]), "down")}${tile("95th pct max DD", money(-m.max_dd["95"]), "down")}</div>
    <svg viewBox="0 0 ${W} ${H}" class="fan" role="img" aria-label="30 sample equity paths from resampled trades">
      <line x1="0" x2="${W}" y1="${y(0)}" y2="${y(0)}" stroke=C("muted") stroke-dasharray="3 3"/>${lines}</svg>
    <div class="muted">30 of ${m.sims} resampled equity paths (cumulative P&amp;L by trade). Dashed line = break-even.</div>
    <table class="tbl"><thead><tr><th>PERCENTILE</th><th>5th</th><th>25th</th><th>50th</th><th>75th</th><th>95th</th></tr></thead><tbody>
      ${row("Final P&L", m.final_pnl)}${row("Max drawdown", m.max_dd)}</tbody></table>
    <div class="muted">Prop firms fail you on the drawdown you hit, not the average. Size so the 95th-percentile drawdown fits inside the account's max drawdown.</div></div>`;
}

function btRenderResults() {
  const el = $("#btResults");
  if (!el) return;
  destroyCharts();
  const tabs = ["SUMMARY", "TRADES", "MONTHLY", "OPTIMISE", "WALK-FWD", "MONTE CARLO"];
  const head = `<div class="tabs">${tabs.map((t) => `<button class="tbtn ${BT.tab === t ? "on" : ""}" data-bttab="${t}">${t}</button>`).join("")}</div>`;
  const r = BT.result;
  let body = "";
  if (BT.tab === "SUMMARY") {
    if (!r) body = '<div class="pad muted">Configure a strategy and press ▶ RUN. Try OPTIMISE, then WALK-FORWARD to check the optimised parameters hold up on unseen data.</div>';
    else body = `${r.warnings.map((w) => `<div class="notice">${esc(w)}</div>`).join("")}${btTiles(r.metrics, r.benchmark_metrics)}<div id="btCharts" style="display:flex;flex-direction:column"></div>${btPropBox(r.prop_eval)}`;
  } else if (BT.tab === "TRADES") body = r ? `<div class="scroll">${btTrades(r.trades)}</div>` : "";
  else if (BT.tab === "MONTHLY") body = r ? btMonthly(r.monthly) : "";
  else if (BT.tab === "OPTIMISE") body = BT.opt ? btOptHtml(BT.opt) : '<div class="pad muted">Tick “opt” on one or more parameters, set min/max/step, then press OPTIMISE.</div>';
  else if (BT.tab === "WALK-FWD") body = BT.wf ? btWfHtml(BT.wf) : '<div class="pad muted">Walk-forward re-optimises on each training window and trades the winner on the next unseen window. It uses the same “opt” ranges.</div>';
  else if (BT.tab === "MONTE CARLO") body = BT.mc ? btMcHtml(BT.mc) : '<div class="pad muted">Press MONTE CARLO to resample this strategy’s trades.</div>';
  el.innerHTML = head + `<div class="bt-body">${body}</div>`;
  if (BT.tab === "SUMMARY" && r) btEquityChart($("#btCharts", el));
  if (BT.tab === "WALK-FWD" && BT.wf) {
    const ch = makeChart($("#wfEq", el), { localization: { priceFormatter: (v) => money(v) } });
    ch.addLineSeries({ color: C("s1"), lineWidth: 2, priceLineVisible: false }).setData(BT.wf.oos_equity);
    ch.timeScale().fitContent();
  }
}

// ------------------------------------------------------------------ actions
function btPayload() {
  const c = { ...BT.cfg };
  c.start = c.start || null; c.end = c.end || null; c.prop_preset = c.prop_preset || null;
  return c;
}
async function btAction(kind) {
  if (BT.busy) return;
  BT.busy = true;
  const busy = $("#btBusy");
  if (busy) busy.textContent = kind === "run" ? "running…" : `${kind}… ${btCombos() ? btCombos() + " combos" : ""}`;
  try {
    if (kind === "run") { BT.result = await api("/api/backtest/run", { method: "POST", body: btPayload() }); BT.tab = "SUMMARY"; }
    if (kind === "optimize") { BT.opt = await api("/api/backtest/optimize", { method: "POST", body: btPayload() }); BT.tab = "OPTIMISE"; }
    if (kind === "walkforward") { BT.wf = await api("/api/backtest/walkforward", { method: "POST", body: btPayload() }); BT.tab = "WALK-FWD"; }
    if (kind === "montecarlo") { BT.mc = await api("/api/backtest/montecarlo", { method: "POST", body: btPayload() }); BT.tab = "MONTE CARLO"; }
    foot(`Backtest ${kind} done.`);
  } catch (e) {
    toast("BACKTEST ERROR", e.message, "CRITICAL");
  } finally {
    BT.busy = false;
  }
  btRender();
}
function btRender() {
  const v = $("#view");
  if (!v || S.view !== "BT") return;
  v.innerHTML = btConfigHtml() + '<div id="btResults" class="bt-results"></div>';
  btUpdateCombos();
  btRenderResults();
}

// ------------------------------------------------------------------ events
document.addEventListener("change", (ev) => {
  const t = ev.target;
  if (S.view !== "BT") return;
  if (t.dataset.cfg) {
    const k = t.dataset.cfg;
    let v = t.value;
    if (t.type === "number") v = v === "" ? 0 : parseFloat(v);
    if (k === "anchored") v = v === "true";
    BT.cfg[k] = v;
    if (k === "strategy") { BT.cfg.params = {}; BT.cfg.ranges = {}; BT.opt = null; BT.wf = null; }
    btSaveCfg();
    if (["strategy", "sizing"].includes(k)) btRender();
  } else if (t.dataset.param) {
    BT.cfg.params[t.dataset.param] = t.type === "number" ? parseFloat(t.value) : t.value;
    btSaveCfg();
  } else if (t.dataset.opt) {
    const name = t.dataset.opt, p = btStrat().params.find((x) => x.name === name);
    if (t.checked) BT.cfg.ranges[name] = p.type === "choice" ? [...p.choices] : [p.min, p.max, p.step];
    else delete BT.cfg.ranges[name];
    btSaveCfg(); btRender();
  } else if (t.dataset.rng) {
    const [name, i] = t.dataset.rng.split(":");
    if (BT.cfg.ranges[name]) BT.cfg.ranges[name][+i] = parseFloat(t.value);
    btSaveCfg(); btUpdateCombos();
  }
});
document.addEventListener("click", (ev) => {
  const t = ev.target.closest("[data-bt],[data-bttab],[data-btparams]");
  if (!t || S.view !== "BT") return;
  if (t.dataset.bttab) { BT.tab = t.dataset.bttab; return btRenderResults(); }
  if (t.dataset.btparams) {
    BT.cfg.params = { ...BT.cfg.params, ...JSON.parse(t.dataset.btparams) };
    btSaveCfg();
    return btAction("run");
  }
  const k = t.dataset.bt;
  if (k === "compare" && BT.result) {
    if (BT.compare.length >= 3) BT.compare.shift();
    BT.compare.push({ label: btLabel(BT.result.config), equity: BT.result.equity });
    return btRender();
  }
  if (k === "clearcmp") { BT.compare = []; return btRender(); }
  btAction(k);
});

VIEWS.BT = async function (v) {
  setTitle("BACKTEST LAB · STRATEGIES · OPTIMISATION · ROBUSTNESS");
  tools("");
  if (!BT.strategies.length) {
    [BT.strategies, BT.presets] = await Promise.all([api("/api/backtest/strategies"), api("/api/prop/presets")]);
  }
  if (!BT.cfg) {
    BT.cfg = btLoadCfg();
    if (!BT.strategies.some((s) => s.key === BT.cfg.strategy)) BT.cfg.strategy = BT.strategies[0].key;
    if (!S.universe[BT.cfg.symbol]) BT.cfg.symbol = "CL";
  }
  v.innerHTML = "";
  btRender();
};
