/* Intraday Research: top-down multi-timeframe analysis + research-backed day-trading strategies. */
"use strict";

const IR = { data: null, td: null, open: null, slip: 1 };
const GRADE_TIP = {
  A: "Peer-reviewed and replicated across many markets / long samples",
  B: "Peer-reviewed or strong working paper, but narrower or contested out of sample",
  C: "Practitioner rule with weak or no peer-reviewed support (control)",
};
const VERDICT_CLS = { EDGE: "b-up", PROMISING: "b-up", "NO EDGE": "b-flat", NEGATIVE: "b-down" };
const TREND_SCORE_CLS = (s) => (s >= 0.5 ? "up" : s <= -0.5 ? "down" : "muted");

function spark(values) {
  if (!values || values.length < 2) return "";
  const W = 120, H = 28, lo = Math.min(...values), hi = Math.max(...values), sp = hi - lo || 1;
  const pts = values.map((v, i) => `${((i / (values.length - 1)) * W).toFixed(1)},${(H - 2 - ((v - lo) / sp) * (H - 4)).toFixed(1)}`).join(" ");
  const up = values[values.length - 1] >= values[0];
  return `<svg viewBox="0 0 ${W} ${H}" class="ir-spark" role="img" aria-label="Last ${values.length} closes, ${up ? "up" : "down"}">
    <polyline fill="none" stroke-width="1.5" style="stroke:${up ? "var(--up)" : "var(--down)"}" points="${pts}"/></svg>`;
}

function irTopdown(t) {
  const a = t.alignment, w = Math.min(50, Math.abs(a) / 2);
  return `<div class="box"><div class="sec">TOP-DOWN ANALYSIS <span class="muted">bias (W, D) → swing (4H, 1H) → entry (15m, 5m, 1m)</span></div>
    <div class="ir-align">
      <div><div class="muted">Alignment</div><div class="big ${cls(a)}">${signed(a, 0)}</div></div>
      <div class="bar ir-abar" role="img" aria-label="Alignment ${a}"><i class="${a >= 0 ? "pos" : "neg"}" style="width:${w}%"></i></div>
      <div><div class="muted">Setup</div><div class="ir-setup">${esc(t.setup)}</div></div>
      <div><div class="muted">Execute on</div><div class="ir-setup">${t.exec_tf}</div></div>
    </div>
    <div class="dt-tip">${esc(t.plan)} ${esc(t.exec_note)}</div>
    <table class="tbl ir-td"><thead><tr><th>TF</th><th>ROLE</th><th>TREND</th><th>STRUCTURE</th><th>RSI</th><th>EMA20 SLOPE</th><th>ATR</th><th>RANGE POS</th><th>SWING HIGH</th><th>SWING LOW</th><th>LAST 60 BARS</th></tr></thead><tbody>
    ${t.rows.map((r) => `<tr><td class="sym">${r.tf}</td><td class="muted">${r.role}</td>
      <td><span class="bias ${r.score >= 0.5 ? "b-up" : r.score <= -0.5 ? "b-down" : "b-flat"}">${r.label}</span></td>
      <td>${esc(r.structure)}</td><td class="${r.rsi > 55 ? "up" : r.rsi < 45 ? "down" : ""}">${fx(r.rsi, 0)}</td>
      <td class="${TREND_SCORE_CLS(r.slope_atr * 5)}">${signed(r.slope_atr, 2)} ATR</td>
      <td>${r.atr_ticks}t <span class="muted">(${fx(r.atr_pct, 0)} pct)</span></td><td>${fx(r.range_pos, 0)}%</td>
      <td class="down">${px(S.sel, r.swing_high)}</td><td class="up">${px(S.sel, r.swing_low)}</td><td>${spark(r.spark)}</td></tr>`).join("")}
    </tbody></table></div>`;
}

function irRanking(d) {
  return `<div class="box"><div class="sec">RESEARCH-BACKED DAY-TRADE STRATEGIES · RANKED
      <span class="muted">${d.sessions} sessions ${d.from} → ${d.to} · chosen on data before ${d.split_date}, judged after it · ${money(d.rt_cost)} (${d.rt_cost_ticks}t) round-turn costs</span></div>
    <div class="scroll"><table class="tbl ir-rank"><thead><tr><th>#</th><th>STRATEGY</th><th title="Evidence grade">EVID.</th><th>VERDICT</th><th>BEST TF</th>
      <th>OOS TRADES</th><th>OOS WIN%</th><th>OOS $/TR</th><th>OOS T</th><th>WALK-FWD $/TR</th><th>WF T</th><th>WF SHARPE</th><th title="Gross edge per trade vs round-turn cost">GROSS vs COST</th></tr></thead><tbody>
    ${d.strategies.map((s, i) => { const o = s.oos, w = s.wf; return `<tr class="row ${IR.open === s.key ? "sel" : ""}" data-irkey="${s.key}">
      <td>${i + 1}</td><td style="text-align:left"><b>${esc(s.name)}</b><div class="muted">${s.family}${s.grade === "C" ? " · control" : ""}</div></td>
      <td><span class="grade g${s.grade}" title="${GRADE_TIP[s.grade]}">${s.grade}</span></td>
      <td><span class="bias ${VERDICT_CLS[s.verdict]}">${s.verdict}</span>${s.grade === "C" && (s.verdict === "PROMISING" || s.verdict === "EDGE") ? '<div class="down" style="font-size:10px">control: suspect luck</div>' : ""}</td>
      <td>${s.best_tf}m <div class="muted">WF picks ${s.wf_tf_most_chosen}m</div></td>
      <td>${o.n ?? 0}</td><td>${fx(o.win_pct, 0)}%</td><td class="${cls(o.avg)}">${money(o.avg)}</td><td class="${o.t_stat >= d.t_crit ? "up" : o.t_stat <= -2 ? "down" : ""}">${fx(o.t_stat, 2)}</td>
      <td class="${cls(w.avg)}">${money(w.avg)} <span class="muted">(${w.n ?? 0})</span></td><td>${fx(w.t_stat, 2)}</td><td class="${cls(w.sharpe)}">${fx(w.sharpe, 2)}</td>
      <td><span class="${s.gross_ticks > s.rt_cost_ticks ? "up" : "down"}">${signed(s.gross_ticks, 1)}t</span> <span class="muted">vs ${s.rt_cost_ticks}t</span></td></tr>`; }).join("")}
    </tbody></table></div>
    <div class="muted" style="margin-top:6px">EDGE needs an out-of-sample t-stat ≥ ${d.t_crit} (the 5% level corrected for testing ${d.strategies.length} strategies), ≥ 30 OOS trades, and walk-forward t ≥ 2.
    PROMISING needs t ≥ 1.5 and walk-forward t ≥ 1. Click a row for the research, the timeframe table and the walk-forward equity curve.</div></div>`;
}

function irHeatmap(d) {
  const tfs = [...new Set(d.strategies.flatMap((s) => s.tf_table.map((r) => r.tf)))].sort((a, b) => a - b);
  const mx = Math.max(1, ...d.strategies.flatMap((s) => s.tf_table.map((r) => Math.abs(r.oos.avg_ticks || 0))));
  return `<div class="box"><div class="sec">OPTIMAL TIMEFRAME MAP <span class="muted">out-of-sample net ticks per trade, by strategy × timeframe (minutes)</span></div>
    <div class="scroll"><table class="tbl heat"><thead><tr><th>STRATEGY</th>${tfs.map((t) => `<th>${t}m</th>`).join("")}</tr></thead><tbody>
    ${d.strategies.map((s) => `<tr><td class="sym" style="text-align:left">${esc(s.name.split(" (")[0])}</td>${tfs.map((t) => {
      const r = s.tf_table.find((x) => x.tf === t);
      if (!r || !r.oos.n) return '<td class="muted">·</td>';
      const v = r.oos.avg_ticks || 0, a = Math.min(1, Math.abs(v) / mx) * 0.65;
      return `<td class="${t === s.best_tf ? "best" : ""}" style="background:rgb(var(--${v >= 0 ? "up" : "down"}-rgb) / ${a})" title="${s.name} · ${t}m · OOS ${r.oos.n} trades, t ${r.oos.t_stat}, params ${esc(JSON.stringify(r.params))}">${signed(v, 1)}</td>`;
    }).join("")}</tr>`).join("")}
    </tbody></table></div><div class="muted">Outlined cell = timeframe chosen on the in-sample period. Each strategy's "timeframe" is its own dimension: bar size, opening-range length, decision interval or trade window.</div></div>`;
}

function irDetail(d) {
  const s = d.strategies.find((x) => x.key === IR.open);
  if (!s) return "";
  return `<div class="box ir-detail"><div class="sec">${esc(s.name.toUpperCase())} <span class="muted">grade ${s.grade}: ${GRADE_TIP[s.grade]}</span></div>
    <div class="cols" style="padding:0">
      <div>
        <div class="sec">WHAT THE RESEARCH FOUND</div><p>${esc(s.finding)}</p>
        <div class="sec">CAVEATS</div><p class="muted">${esc(s.caveats)}</p>
        <div class="sec">HOW TO TRADE IT <span class="muted">best here: ${s.best_tf}m · ${esc(JSON.stringify(s.best_params))}</span></div><p>${esc(s.how_to)}</p>
        ${s.citations.length ? `<div class="sec">SOURCES</div><ul class="dt-list">${s.citations.map((c) => `<li><a href="${esc(c.url)}" target="_blank" rel="noopener noreferrer">${esc(c.ref)}</a></li>`).join("")}</ul>` : ""}
      </div>
      <div>
        <div class="sec">TIMEFRAME TABLE <span class="muted">${esc(s.tf_label)}</span></div>
        <table class="tbl"><thead><tr><th>TF</th><th>PARAMS</th><th>IS N</th><th>IS $/TR</th><th>IS T</th><th>OOS N</th><th>OOS $/TR</th><th>OOS T</th><th>OOS GROSS</th></tr></thead><tbody>
        ${s.tf_table.map((r) => `<tr class="${r.tf === s.best_tf ? "sel" : ""}"><td>${r.tf}m</td><td class="muted">${esc(Object.values(r.params).join(", ") || "—")}</td>
          <td>${r.is.n ?? 0}</td><td class="${cls(r.is.avg)}">${money(r.is.avg)}</td><td>${fx(r.is.t_stat, 2)}</td>
          <td>${r.oos.n ?? 0}</td><td class="${cls(r.oos.avg)}">${money(r.oos.avg)}</td><td>${fx(r.oos.t_stat, 2)}</td><td>${r.oos_gross_ticks == null ? "—" : signed(r.oos_gross_ticks, 1) + "t"}</td></tr>`).join("")}
        </tbody></table>
        <div class="legend"><span><i style="background:var(--s1)"></i>Walk-forward out-of-sample equity (1 contract, net)</span></div>
        <div class="chart small" id="irEq"></div>
        <div class="muted">${s.configs_tested} configurations tested. The walk-forward re-picks every 25 sessions using only the prior 120.</div>
      </div></div></div>`;
}

function irMethod(d) {
  return `<div class="box"><div class="sec">DATA &amp; METHOD</div>
    <div class="${d.source === "SIM" ? "notice" : "muted"}">${esc(d.note)}</div>
    <ul class="dt-list">
      <li><b>Real data:</b> save 1-minute bars as <code>backend/data/intraday/${d.data_symbol}_1m.csv</code> (timestamp, open, high, low, close, volume; ET or UTC timestamps), e.g. from Databento, FirstRate Data or a platform export. The screen switches to CSV automatically.</li>
      <li><b>Sessions:</b> regular trading hours ${d.rth[0]}–${d.rth[1]} ET. Each test trades 1 contract and pays ${d.rt_cost_ticks} ticks round-turn (commission + slippage).</li>
      <li><b>Validation:</b> parameters and timeframe are chosen on the first 60% of sessions only, then judged on the last 40%. The walk-forward re-optimises on rolling windows. The control strategy shows how often luck alone looks good.</li>
      <li><b>Reading it:</b> published research is evidence that an effect existed in some markets and periods, not a promise. Size small until your own out-of-sample results confirm it.</li>
    </ul></div>`;
}

async function irLoad() {
  [IR.td, IR.data] = await Promise.all([api(`/api/intraday/topdown/${S.sel}`), api(`/api/intraday/research/${S.sel}?slip_ticks=${IR.slip}`)]);
}
function irRender() {
  const v = $("#view");
  if (!v || S.view !== "IR" || !IR.data) return;
  destroyCharts();
  const d = IR.data;
  const keep = v.scrollTop;
  v.innerHTML = `<div class="dt">
    <div class="dt-head"><div class="dt-title"><div class="dt-sym">${S.sel}<span>${esc(S.universe[S.sel]?.name || "")} · intraday research</span></div></div>
      <div class="dt-badges"><span class="badge ${d.source === "SIM" ? "sim" : "live"}">INTRADAY: ${d.source}</span>
        <label class="muted">Slippage <select data-irslip>${[0, 1, 2, 3].map((x) => `<option value="${x}" ${x == IR.slip ? "selected" : ""}>${x} tick/side</option>`).join("")}</select></label></div>
      <div class="dt-switch">${Object.keys(S.universe).filter((k) => !S.universe[k].parent).map((s) => `<button class="tbtn ${s === S.sel ? "on" : ""}" data-irsym="${s}">${s}</button>`).join("")}</div></div>
    ${irTopdown(IR.td)}
    ${irRanking(d)}
    ${irDetail(d)}
    ${irHeatmap(d)}
    ${irMethod(d)}</div>`;
  v.scrollTop = keep;
  const s = d.strategies.find((x) => x.key === IR.open);
  if (s && $("#irEq")) {
    const ch = makeChart($("#irEq"), { localization: { priceFormatter: (x) => money(x) } });
    ch.addLineSeries({ color: C("s1"), lineWidth: 2, priceLineVisible: false }).setData(s.equity);
    ch.timeScale().fitContent();
  }
}

VIEWS.IR = async function (v) {
  setTitle("INTRADAY RESEARCH · TOP-DOWN · STRATEGIES · OPTIMAL TIMEFRAME");
  tools("");
  v.innerHTML = '<div class="pad muted">Running the research (first run per market takes a second)…</div>';
  const sym = S.universe[S.sel]?.parent;
  if (sym) S.sel = sym;                         // research runs on the full-size contract's data
  await irLoad();
  if (!IR.open && IR.data.strategies.length) IR.open = IR.data.strategies[0].key;
  irRender();
};

document.addEventListener("click", async (ev) => {
  const t = ev.target.closest("[data-irkey],[data-irsym]");
  if (!t || S.view !== "IR") return;
  if (t.dataset.irsym) return select(t.dataset.irsym, "IR");
  IR.open = IR.open === t.dataset.irkey ? null : t.dataset.irkey;
  irRender();
  if (IR.open) $(".ir-detail")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
});
document.addEventListener("change", async (ev) => {
  if (ev.target.dataset.irslip === undefined || S.view !== "IR") return;
  IR.slip = parseFloat(ev.target.value);
  await irLoad();
  irRender();
});
