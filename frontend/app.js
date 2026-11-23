/* Commodities Desk Terminal - client */
"use strict";

const S = {
  universe: {}, rows: {}, alerts: [], prop: null, sel: "ES", view: "DT", ticketPreset: null,
  snap: null, charts: [], mainSeries: null, chartMode: "D", ws: null, tapeAt: 0,
};
const LWC = window.LightweightCharts;
const SECTORS = ["Indices", "Energy", "Metals", "Grains", "Softs", "Livestock"];
const FACTORS = [
  ["trend", "Trend (TSMOM)", "TREND"], ["breakout", "Breakout", "BRKOUT"], ["mean_reversion", "Mean reversion", "MEANREV"],
  ["carry", "Carry / curve", "CARRY"], ["seasonality", "Seasonality", "SEASON"], ["cot", "COT positioning", "COT"],
  ["supply_demand", "Supply / demand", "S&amp;D"],
];
const $ = (s, el = document) => el.querySelector(s);
// Theme tokens live in CSS custom properties; charts need concrete colours, so read them at render time.
const C = (name) => getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim();
const CA = (name, a) => `rgba(${C(name).split(/\s+/).join(",")},${a})`;
const THEMES = ["nightfall", "classic"];
function applyTheme(t) {
  document.documentElement.dataset.theme = THEMES.includes(t) ? t : "nightfall";
  try { localStorage.setItem("theme", document.documentElement.dataset.theme); } catch { /* no storage */ }
}
applyTheme((() => { try { return localStorage.getItem("theme"); } catch { return null; } })());
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// ------------------------------------------------------------------ utils
async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json" }, ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(data.detail?.message || data.detail || r.statusText), { data });
  return data;
}
function decimals(sym) {
  const t = S.universe[sym]?.tick_size ?? 0.01;
  const s = String(t);
  return s.includes(".") ? s.split(".")[1].length : 0;
}
const px = (sym, v) => (v == null ? "—" : Number(v).toFixed(decimals(sym)));
const fx = (v, d = 1) => (v == null || Number.isNaN(v) ? "—" : Number(v).toFixed(d));
const signed = (v, d = 1) => (v == null ? "—" : (v > 0 ? "+" : "") + Number(v).toFixed(d));
const money = (v) => (v == null ? "—" : (v < 0 ? "-$" : "$") + Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 }));
const cls = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");
const biasCls = (b) => (b?.includes("BUY") ? "b-up" : b?.includes("SELL") ? "b-down" : "b-flat");
const chip = (v) => {
  if (v == null) return '<span class="chip muted">n/a</span>';
  const a = Math.min(1, Math.abs(v) / 100) * 0.55;
  const bg = v >= 0 ? `rgb(var(--up-rgb) / ${a})` : `rgb(var(--down-rgb) / ${a})`;
  return `<span class="chip" style="background:${bg}">${signed(v, 0)}</span>`;
};
const srcBadge = (label, v) => `<span class="badge ${String(v).toLowerCase()}" title="${label} source">${label}:${v}</span>`;
function toast(title, detail, sev = "INFO") {
  const el = document.createElement("div");
  el.className = `toast ${sev}`;
  el.innerHTML = `<b>${esc(title)}</b><div>${esc(detail)}</div>`;
  $("#toasts").prepend(el);
  setTimeout(() => el.remove(), 7000);
  while ($("#toasts").children.length > 4) $("#toasts").lastChild.remove();
}
const foot = (m) => ($("#footMsg").textContent = m);
function nextBusinessDays(dateStr, n) {
  const out = [];
  const d = new Date(dateStr + "T00:00:00Z");
  while (out.length < n) {
    d.setUTCDate(d.getUTCDate() + 1);
    if (d.getUTCDay() % 6 !== 0) out.push(d.toISOString().slice(0, 10));
  }
  return out;
}

// ------------------------------------------------------------------ clocks
function tickClocks() {
  const z = [["NY", "America/New_York"], ["CHI", "America/Chicago"], ["LDN", "Europe/London"], ["SGP", "Asia/Singapore"]];
  $("#clocks").innerHTML = z.map(([k, tz]) =>
    `<span><b>${k}</b> ${new Date().toLocaleTimeString("en-GB", { timeZone: tz, hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span>`).join("");
}

// ------------------------------------------------------------------ watchlist
function renderWatch() {
  const head = `<thead><tr><th>SYM</th><th>LAST</th><th>CHG%</th><th title="Swing composite (quant + quantamental)">SWING</th><th title="Intraday composite">DAY</th><th title="Managed-money COT index, 3y">COT</th><th title="Model probability price is higher in 5 days">P↑5D</th><th title="Time-decayed news sentiment">NEWS</th></tr></thead>`;
  let body = "";
  for (const sec of SECTORS) {
    const rows = Object.values(S.rows).filter((r) => r.sector === sec);
    if (!rows.length) continue;
    body += `<tr class="sector"><td colspan="8">${sec.toUpperCase()}</td></tr>`;
    for (const r of rows) {
      body += `<tr class="row ${r.symbol === S.sel ? "sel" : ""}" data-sym="${r.symbol}">
        <td class="sym" title="${esc(r.name)}">${r.symbol}</td>
        <td data-f="last">${px(r.symbol, r.last)}</td>
        <td data-f="chg_pct" class="${cls(r.chg_pct)}">${signed(r.chg_pct, 2)}</td>
        <td>${chip(r.swing)}</td><td>${chip(r.day)}</td>
        <td title="${esc(r.cot_state)}">${fx(r.cot_index, 0)}</td>
        <td>${fx(r.p_up_5d * 100, 0)}%</td><td>${r.news == null ? '<span class="muted">·</span>' : chip(r.news)}</td></tr>`;
    }
  }
  $("#watch").innerHTML = head + `<tbody>${body}</tbody>`;
}
function updateQuoteCells(sym, q) {
  const tr = document.querySelector(`#watch tr[data-sym="${sym}"]`);
  if (!tr) return;
  const lastTd = tr.querySelector('[data-f="last"]');
  const prev = parseFloat(lastTd.textContent);
  lastTd.textContent = px(sym, q.last);
  if (!Number.isNaN(prev) && prev !== q.last) {
    lastTd.classList.remove("flash-up", "flash-down");
    void lastTd.offsetWidth;
    lastTd.classList.add(q.last > prev ? "flash-up" : "flash-down");
  }
  const c = tr.querySelector('[data-f="chg_pct"]');
  c.textContent = signed(q.chg_pct, 2);
  c.className = cls(q.chg_pct);
}
function renderTape() {
  $("#tape").innerHTML = Object.values(S.rows).map((r) =>
    `<span><b class="amber">${r.symbol}</b> ${px(r.symbol, r.last)} <span class="${cls(r.chg_pct)}">${signed(r.chg_pct, 2)}%</span></span>`).join("");
}

// ------------------------------------------------------------------ selection
async function select(sym, view) {
  sym = sym.toUpperCase();
  if (!S.universe[sym]) return foot(`Unknown security: ${sym}`);
  S.sel = sym;
  if (view) S.view = view;
  document.querySelectorAll("#watch tr.row").forEach((tr) => tr.classList.toggle("sel", tr.dataset.sym === sym));
  try {
    S.snap = await api(`/api/snapshot/${sym}`);
  } catch (e) {
    return foot(e.message);
  }
  renderDetail();
  renderView();
}

// ------------------------------------------------------------------ detail panel
function sigBar(label, v) {
  const w = v == null ? 0 : Math.min(50, Math.abs(v) / 2);
  return `<div class="sigrow"><span class="lbl">${label}</span>
    <div class="bar" role="img" aria-label="${label} ${v == null ? "n/a" : signed(v, 0)}">${v == null ? "" : `<i class="${v >= 0 ? "pos" : "neg"}" style="width:${w}%"></i>`}</div>
    <span class="num ${cls(v)}">${v == null ? "n/a" : signed(v, 0)}</span></div>`;
}
function renderDetail() {
  const s = S.snap, sym = S.sel, q = s.quote, lv = s.levels, f = s.forecast, c = s.cot, sd = s.supply_demand;
  const ct = s.contract;
  $("#detailTitle").textContent = `${sym} SIGNAL STACK`;
  $("#srcBadges").innerHTML = Object.entries(s.sources).map(([k, v]) => srcBadge(k.slice(0, 3).toUpperCase(), v)).join(" ");
  const h5 = f.horizons["5"], h20 = f.horizons["20"];
  $("#detail").innerHTML = `
    <div class="headline"><span class="big" id="dLast">${px(sym, q.last)}</span>
      <span class="${cls(q.chg)}" id="dChg">${signed(q.chg, decimals(sym))} (${signed(q.chg_pct, 2)}%)</span></div>
    <div class="row-actions">
      <span>SWING <span class="bias ${biasCls(s.swing.bias)}">${s.swing.bias}</span> ${chip(s.swing.score)} <span class="muted">agree ${s.swing.agreement}</span></span>
    </div>
    <div class="row-actions"><span>DAY <span class="bias ${biasCls(s.day.bias)}">${s.day.bias}</span> ${chip(s.day.score)}</span>
      <span class="muted">Quant ${signed(s.quant_score, 0)} · Fundamental ${signed(s.fundamental_score, 0)}</span></div>

    <div class="sec">FACTORS <span class="muted">−100 bearish · +100 bullish</span></div>
    ${FACTORS.map(([k, l]) => sigBar(l, s.scores[k])).join("")}
    ${sigBar("Intraday VWAP", s.day.vwap_score)}
    ${sigBar("News flow (day)", s.day.news_score)}

    <div class="sec">DAY-TRADE LEVELS <span class="muted">${lv.bias}</span></div>
    <div class="kv">
      <span>R2 / R1</span><span>${px(sym, lv.r2)} / ${px(sym, lv.r1)}</span>
      <span>Pivot</span><span class="amber">${px(sym, lv.pivot)}</span>
      <span>S1 / S2</span><span>${px(sym, lv.s1)} / ${px(sym, lv.s2)}</span>
      <span>Prev H / L</span><span>${px(sym, lv.prev_high)} / ${px(sym, lv.prev_low)}</span>
      <span>VWAP</span><span>${px(sym, lv.vwap)} <span class="muted">(${signed(lv.vwap_dev_atr, 2)} ATR)</span></span>
      <span>ATR(14)</span><span>${px(sym, lv.atr)} · ${lv.atr_ticks} ticks · ${money(lv.atr_ticks * ct.tick_value)}/ct</span>
      <span>Vol regime</span><span>${s.detail.vol.regime} (${fx(s.detail.vol.percentile, 0)} pct)</span>
    </div>

    <div class="sec">FORECAST <span class="muted">5d hit rate ${f.hit_rate_5d == null ? "—" : fx(f.hit_rate_5d * 100, 0) + "%"}</span></div>
    <div class="kv">
      <span>5d target</span><span>${px(sym, h5.target)} <span class="muted">P↑ ${fx(h5.p_up * 100, 0)}%</span></span>
      <span>5d 1σ range</span><span>${px(sym, h5.low_1s)} – ${px(sym, h5.high_1s)}</span>
      <span>20d target</span><span>${px(sym, h20.target)} <span class="muted">P↑ ${fx(h20.p_up * 100, 0)}%</span></span>
      <span>20d 1σ range</span><span>${px(sym, h20.low_1s)} – ${px(sym, h20.high_1s)}</span>
    </div>

    <div class="sec">COT <span class="muted">${c.report_date}</span></div>
    <div class="kv">
      <span>MM net</span><span class="${cls(c.mm_net)}">${c.mm_net.toLocaleString()} <span class="muted">(${signed(c.mm_net_chg, 0)})</span></span>
      <span>MM index 3y / 1y</span><span>${fx(c.mm_index_3y, 0)} / ${fx(c.mm_index_1y, 0)}</span>
      <span>Commercial index</span><span>${fx(c.pm_index_3y, 0)}</span>
      <span>State</span><span class="amber">${c.state}</span>
    </div>
    ${sd ? `<div class="sec">SUPPLY / DEMAND <span class="muted">${sd.date}</span></div>
    <div class="kv">
      <span>Stocks vs 5y</span><span class="${cls(-sd.dev_pct)}">${signed(sd.dev_pct, 1)}% (${signed(sd.dev_z, 1)}σ)</span>
      <span>Weekly chg / normal</span><span>${signed(sd.weekly_chg, 1)} / ${signed(sd.normal_chg, 1)}</span>
      <span>Balance</span><span class="amber">${sd.balance}</span></div>` : ""}
    ${typeof newsDetail === "function" ? newsDetail(sym) : ""}
    <div class="row-actions">
      <button class="btn buy" data-act="ticket" data-side="BUY">BUY ${sym}</button>
      <button class="btn sell" data-act="ticket" data-side="SELL">SELL ${sym}</button>
    </div>`;
}

// ------------------------------------------------------------------ charts
function destroyCharts() {
  S.charts.forEach((c) => c.remove());
  S.charts = [];
  S.mainSeries = null;
}
function makeChart(el, extra = {}) {
  const chart = LWC.createChart(el, {
    autoSize: true,
    layout: { background: { type: "solid", color: C("surface") }, textColor: C("ink-2"), fontFamily: "ui-monospace, Menlo, monospace", fontSize: 11 },
    grid: { vertLines: { color: C("chart-grid") }, horzLines: { color: C("chart-grid") } },
    rightPriceScale: { borderColor: C("line") },
    timeScale: { borderColor: C("line"), rightOffset: 4 },
    crosshair: { mode: LWC.CrosshairMode.Normal },
    ...extra,
  });
  S.charts.push(chart);
  return chart;
}
function readout(chart, el, fmt) {
  chart.subscribeCrosshairMove((p) => {
    if (!p || !p.time || !p.seriesData.size) return (el.innerHTML = el.dataset.base || "");
    el.innerHTML = fmt(p);
  });
}

async function renderPriceChart(container, { cone = true, days = 260 } = {}) {
  const sym = S.sel, s = S.snap, dataSym = s.contract.parent || sym;
  container.innerHTML = `<div class="legend" id="pxLegend"></div><div class="chart" id="pxChart"></div>`;
  const chart = makeChart($("#pxChart", container), {
    timeScale: { borderColor: C("line"), rightOffset: 4, timeVisible: S.chartMode === "I", secondsVisible: false },
  });
  const candles = chart.addCandlestickSeries({
    upColor: C("up"), downColor: C("down"), borderVisible: false, wickUpColor: C("up"), wickDownColor: C("down"),
    priceFormat: { type: "price", precision: decimals(sym), minMove: S.universe[sym].tick_size },
  });
  S.mainSeries = candles;
  let data;
  if (S.chartMode === "I") {
    data = await api(`/api/intraday/${dataSym}`);
    if (data.length < 2) foot("Intraday bars build up while the terminal runs (1-minute bars).");
  } else {
    data = await api(`/api/history/${dataSym}?days=${days}`);
  }
  candles.setData(data);
  const lv = s.levels;
  const line = (price, title, color, style = 2) => candles.createPriceLine({ price, title, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true });
  line(lv.r1, "R1", C("muted")); line(lv.s1, "S1", C("muted")); line(lv.pivot, "PIV", C("accent-dim"));
  line(lv.vwap, "VWAP", C("s2"), 0);

  let legendBase = `<span><b class="amber">${sym}</b> ${S.chartMode === "I" ? "1-min" : "daily"}</span>`;
  if (cone && S.chartMode === "D" && data.length) {
    const last = data[data.length - 1];
    const dates = nextBusinessDays(last.time, s.forecast.cone.length - 1);
    const pts = (k) => s.forecast.cone.map((c, i) => ({ time: i === 0 ? last.time : dates[i - 1], value: c[k] }));
    const mk = (k, style, w = 1) => chart.addLineSeries({ color: C("s1"), lineWidth: w, lineStyle: style, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }).setData(pts(k));
    mk("mid", 0, 2); mk("hi1", 2); mk("lo1", 2); mk("hi2", 3); mk("lo2", 3);
    legendBase += `<span><i style="background:var(--s1)"></i>20d forecast · 1σ dashed · 2σ dotted</span><span><i style="background:var(--s2)"></i>VWAP</span>`;
  }
  const leg = $("#pxLegend", container);
  leg.dataset.base = legendBase;
  leg.innerHTML = legendBase;
  readout(chart, leg, (p) => {
    const d = p.seriesData.get(candles);
    if (!d) return legendBase;
    const t = typeof p.time === "number" ? new Date(p.time * 1000).toLocaleTimeString() : p.time;
    return `<span><b class="amber">${sym}</b> ${t}</span><span>O ${px(sym, d.open)} H ${px(sym, d.high)} L ${px(sym, d.low)} C <b>${px(sym, d.close)}</b></span>`;
  });
  chart.timeScale().fitContent();
  return chart;
}

function tools(html) { $("#centerTools").innerHTML = html; }
function setTitle(t) { $("#centerTitle").textContent = t; }

// ------------------------------------------------------------------ views
const VIEWS = {
  async MON(v) {
    setTitle(`${S.sel} · ${S.snap.name.toUpperCase()} · ${S.chartMode === "I" ? "INTRADAY" : "DAILY"}`);
    tools(`<button class="tbtn ${S.chartMode === "D" ? "on" : ""}" data-mode="D">DAILY</button><button class="tbtn ${S.chartMode === "I" ? "on" : ""}" data-mode="I">INTRADAY</button>`);
    await renderPriceChart(v);
  },

  async COT(v) {
    setTitle(`COT · CFTC DISAGGREGATED · ${S.sel}`);
    tools("");
    const [table, hist] = await Promise.all([api("/api/cot"), api(`/api/cot/${S.sel}`)]);
    v.innerHTML = `
      <div class="legend" id="cotLegend"></div><div class="chart small" id="cotChart"></div>
      <div class="legend" id="idxLegend"></div><div class="chart small" id="idxChart" style="flex-basis:150px;min-height:150px"></div>
      <div class="scroll" style="flex:0 0 auto"><table class="tbl"><thead><tr><th>SYM</th><th>REPORT</th><th>MM NET</th><th>Δ WK</th><th>Δσ</th><th>MM IDX 3Y</th><th>COMM IDX</th><th>MM %OI</th><th>STATE</th><th>SRC</th></tr></thead>
      <tbody>${table.map((r) => `<tr class="row ${r.symbol === S.sel ? "sel" : ""}" data-sym="${r.symbol}" data-view="COT">
        <td class="sym">${r.symbol}</td><td>${r.report_date}</td><td class="${cls(r.mm_net)}">${r.mm_net.toLocaleString()}</td>
        <td class="${cls(r.mm_net_chg)}">${signed(r.mm_net_chg, 0)}</td><td>${signed(r.mm_chg_z, 1)}</td>
        <td><span class="chip" style="background:${r.mm_index_3y >= 90 || r.mm_index_3y <= 10 ? "rgb(var(--warn-rgb) / .35)" : "transparent"}">${fx(r.mm_index_3y, 0)}</span></td>
        <td>${fx(r.pm_index_3y, 0)}</td><td>${signed(r.mm_pct_oi, 1)}%</td><td>${r.state}</td><td>${srcBadge("", r.source).replace(":", "")}</td></tr>`).join("")}</tbody></table></div>`;
    const c1 = makeChart($("#cotChart", v));
    const mm = c1.addHistogramSeries({ color: C("s1"), priceFormat: { type: "volume" }, priceLineVisible: false });
    mm.setData(hist.map((h) => ({ time: h.date, value: h.mm_net })));
    const pm = c1.addLineSeries({ color: C("s2"), lineWidth: 2, priceFormat: { type: "volume" }, priceLineVisible: false });
    pm.setData(hist.map((h) => ({ time: h.date, value: h.pm_net })));
    const base1 = `<span><i style="background:var(--s1)"></i>Managed money net (contracts)</span><span><i style="background:var(--s2)"></i>Producer/merchant net</span>`;
    const l1 = $("#cotLegend", v); l1.dataset.base = base1; l1.innerHTML = base1;
    readout(c1, l1, (p) => `<span>${p.time}</span><span><i style="background:var(--s1)"></i>MM ${(p.seriesData.get(mm)?.value ?? 0).toLocaleString()}</span><span><i style="background:var(--s2)"></i>Comm ${(p.seriesData.get(pm)?.value ?? 0).toLocaleString()}</span>`);
    c1.timeScale().fitContent();

    const c2 = makeChart($("#idxChart", v));
    const idx = c2.addLineSeries({ color: C("s1"), lineWidth: 2, priceLineVisible: false });
    idx.setData(hist.map((h) => ({ time: h.date, value: h.mm_index })));
    idx.createPriceLine({ price: 90, color: C("warn"), lineStyle: 2, lineWidth: 1, title: "CROWDED LONG" });
    idx.createPriceLine({ price: 10, color: C("warn"), lineStyle: 2, lineWidth: 1, title: "CROWDED SHORT" });
    const base2 = `<span><i style="background:var(--s1)"></i>MM COT index (3y range, 0–100)</span>`;
    const l2 = $("#idxLegend", v); l2.dataset.base = base2; l2.innerHTML = base2;
    readout(c2, l2, (p) => `<span>${p.time}</span><span>Index ${fx(p.seriesData.get(idx)?.value, 0)}</span>`);
    c2.timeScale().fitContent();
  },

  async SD(v) {
    const table = await api("/api/supply-demand");
    const sym = table.find((r) => r.symbol === (S.snap.contract.parent || S.sel)) ? (S.snap.contract.parent || S.sel) : table[0]?.symbol;
    setTitle(`SUPPLY & DEMAND · WEEKLY INVENTORIES · ${sym}`);
    tools("");
    const snap = sym === S.snap.symbol ? S.snap : await api(`/api/snapshot/${sym}`);
    const hist = snap.supply_demand.history;
    v.innerHTML = `
      <div class="legend" id="sdLegend"></div><div class="chart" id="sdChart"></div>
      <div class="scroll" style="flex:0 0 auto"><table class="tbl"><thead><tr><th>SYM</th><th>WEEK</th><th>STOCKS</th><th>5Y AVG</th><th>VS 5Y</th><th>σ</th><th>WK CHG</th><th>NORMAL</th><th>SURPRISE σ</th><th>BALANCE</th><th>SRC</th></tr></thead>
      <tbody>${table.map((r) => `<tr class="row ${r.symbol === sym ? "sel" : ""}" data-sym="${r.symbol}" data-view="SD">
        <td class="sym">${r.symbol}</td><td>${r.date}</td><td>${fx(r.stocks, 1)}</td><td>${fx(r.five_yr_avg, 1)}</td>
        <td class="${cls(-r.dev_pct)}">${signed(r.dev_pct, 1)}%</td><td>${signed(r.dev_z, 2)}</td>
        <td>${signed(r.weekly_chg, 1)}</td><td>${signed(r.normal_chg, 1)}</td><td class="${cls(-r.surprise_z)}">${signed(r.surprise_z, 2)}</td>
        <td>${r.balance}</td><td>${srcBadge("", r.source).replace(":", "")}</td></tr>`).join("")}</tbody></table>
      <div class="pad muted">Stocks below the 5-year seasonal norm = tight balance (bullish); colour shows bullish (green) / bearish (red). Metals, grains, softs and livestock S&amp;D (USDA WASDE, LME/COMEX stocks) are on the roadmap.</div></div>`;
    const ch = makeChart($("#sdChart", v));
    const a = ch.addLineSeries({ color: C("s1"), lineWidth: 2, priceLineVisible: false });
    a.setData(hist.map((h) => ({ time: h.date, value: h.stocks })));
    const b = ch.addLineSeries({ color: C("s2"), lineWidth: 2, lineStyle: 2, priceLineVisible: false });
    b.setData(hist.filter((h) => h.norm != null).map((h) => ({ time: h.date, value: h.norm })));
    const base = `<span><i style="background:var(--s1)"></i>${sym} stocks</span><span><i style="background:var(--s2)"></i>5-yr same-week average</span>`;
    const l = $("#sdLegend", v); l.dataset.base = base; l.innerHTML = base;
    readout(ch, l, (p) => `<span>${p.time}</span><span><i style="background:var(--s1)"></i>Stocks ${fx(p.seriesData.get(a)?.value, 1)}</span><span><i style="background:var(--s2)"></i>5y ${fx(p.seriesData.get(b)?.value, 1)}</span>`);
    ch.timeScale().fitContent();
  },

  async FCST(v) {
    setTitle(`FORECAST · ${S.sel} · ENSEMBLE (MOMENTUM + AR1 + SEASONAL)`);
    tools("");
    const sym = S.sel, f = S.snap.forecast;
    v.innerHTML = `<div id="fc" style="display:flex;flex-direction:column;flex:1;min-height:300px"></div>
      <div class="cols">
        <div class="box"><div class="sec">HORIZONS</div><table class="tbl"><thead><tr><th>H</th><th>TARGET</th><th>EXP RET</th><th>P↑</th><th>1σ</th><th>2σ</th></tr></thead><tbody>
        ${Object.entries(f.horizons).map(([h, x]) => `<tr><td>${h}d</td><td>${px(sym, x.target)}</td><td class="${cls(x.exp_return)}">${signed(x.exp_return * 100, 2)}%</td><td>${fx(x.p_up * 100, 0)}%</td><td>${px(sym, x.low_1s)}–${px(sym, x.high_1s)}</td><td>${px(sym, x.low_2s)}–${px(sym, x.high_2s)}</td></tr>`).join("")}
        </tbody></table></div>
        <div class="box"><div class="sec">MODEL HONESTY</div><div class="kv">
          <span>Walk-forward 5d hit rate</span><span>${f.hit_rate_5d == null ? "—" : fx(f.hit_rate_5d * 100, 1) + "%"}</span>
          <span>Daily vol (EWMA)</span><span>${fx(f.daily_vol * 100, 2)}%</span>
          ${Object.entries(f.horizons["20"].components).map(([k, x]) => `<span>20d ${k}</span><span>${signed(x * 100, 2)}%</span>`).join("")}
          </div><div class="muted" style="margin-top:6px">A hit rate near 50% means no directional edge; use the range for sizing and stops, not as a price call.</div></div>
      </div>
      <div class="scroll" style="flex:0 0 auto"><table class="tbl"><thead><tr><th>SYM</th><th>LAST</th><th>P↑5D</th><th>SWING</th><th>BIAS</th><th>VOL</th></tr></thead><tbody>
      ${Object.values(S.rows).map((r) => `<tr class="row ${r.symbol === sym ? "sel" : ""}" data-sym="${r.symbol}" data-view="FCST"><td class="sym">${r.symbol}</td><td>${px(r.symbol, r.last)}</td><td>${fx(r.p_up_5d * 100, 0)}%</td><td>${chip(r.swing)}</td><td><span class="bias ${biasCls(r.swing_bias)}">${r.swing_bias}</span></td><td>${r.vol_regime}</td></tr>`).join("")}
      </tbody></table></div>`;
    const prev = S.chartMode; S.chartMode = "D";
    await renderPriceChart($("#fc", v), { cone: true, days: 90 });
    S.chartMode = prev;
  },

  async SIG(v) {
    setTitle("SIGNAL MATRIX · ALL FACTORS · ALL MARKETS");
    tools("");
    const m = await api("/api/matrix");
    v.innerHTML = `<div class="scroll"><table class="tbl"><thead><tr><th>SYM</th>${FACTORS.map(([, l, sh]) => `<th title="${l}">${sh}</th>`).join("")}<th>QUANT</th><th>FUND.</th><th>SWING</th><th>DAY</th><th>BIAS</th></tr></thead><tbody>
      ${m.map((r) => `<tr class="row ${r.symbol === S.sel ? "sel" : ""}" data-sym="${r.symbol}" data-view="MON"><td class="sym">${r.symbol}</td>
        ${FACTORS.map(([k]) => `<td>${chip(r.scores[k])}</td>`).join("")}
        <td>${chip(r.quant)}</td><td>${chip(r.fundamental)}</td><td>${chip(r.swing)}</td><td>${chip(r.day)}</td>
        <td><span class="bias ${biasCls(r.bias)}">${r.bias}</span></td></tr>`).join("")}
      </tbody></table>
      <div class="pad muted">Cells show the score (−100…+100); green = bullish, red = bearish, depth = strength. n/a = factor not available for this market or data source.
      Swing weights: trend 25%, COT 20%, S&amp;D 20%, breakout 10%, carry 10%, seasonality 10%, mean-reversion 5% (renormalised when a factor is n/a).</div></div>`;
  },

  async PROP(v) {
    setTitle("PROP DESK · EVALUATION RULES · RISK · ORDER TICKET");
    tools(`<button class="tbtn" data-act="flatten">FLATTEN ALL</button>`);
    const [st, presets] = await Promise.all([api("/api/prop"), api("/api/prop/presets")]);
    S.prop = st;
    const r = st.rules, sym = S.sel, lv = S.snap.levels;
    // A plan loaded from Day-Trade Insider pre-fills the ticket; otherwise default to ~20% of daily ATR
    const pre = S.ticketPreset;
    S.ticketPreset = null;
    const defStop = pre ? pre.stop : Math.max(4, Math.round(lv.atr_ticks * 0.2));
    const tSym = pre ? pre.symbol : sym;
    v.innerHTML = `<div class="cols">
      <div class="box"><div class="sec">ACCOUNT RULES</div>
        <div class="form">
          <label>Preset<select id="preset">${Object.entries(presets).map(([k, p]) => `<option value="${k}" ${p.name === r.name ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></label>
          <label>Account size<input id="r_account_size" type="number" value="${r.account_size}"></label>
          <label>Profit target<input id="r_profit_target" type="number" value="${r.profit_target}"></label>
          <label>Daily loss limit<input id="r_daily_loss_limit" type="number" value="${r.daily_loss_limit ?? ""}" placeholder="none"></label>
          <label>Max drawdown<input id="r_max_drawdown" type="number" value="${r.max_drawdown}"></label>
          <label>Drawdown type<select id="r_drawdown_mode">${["trailing_eod", "trailing_intraday", "static"].map((m) => `<option ${m === r.drawdown_mode ? "selected" : ""}>${m}</option>`).join("")}</select></label>
          <label>Max contracts<input id="r_max_contracts" type="number" value="${r.max_contracts}"></label>
          <label>Consistency %<input id="r_consistency_pct" type="number" value="${r.consistency_pct ?? ""}" placeholder="none"></label>
          <label>Min trading days<input id="r_min_trading_days" type="number" value="${r.min_trading_days}"></label>
          <label>Commission / side<input id="r_commission_per_side" type="number" step="0.01" value="${r.commission_per_side}"></label>
        </div>
        <div class="row-actions"><button class="btn" data-act="loadPreset">LOAD PRESET</button><button class="btn amber" data-act="saveRules">APPLY CUSTOM RULES</button><button class="btn" data-act="resetProp">RESET ACCOUNT</button></div>
        <div class="notice">Presets are generic templates. Enter your firm's current rulebook numbers; firms change rules often.</div>
      </div>
      <div class="box"><div class="sec">ORDER TICKET <span class="muted">paper · pre-trade rule check</span></div>
        <div class="form">
          <label>Symbol<select id="t_sym">${Object.keys(S.universe).map((k) => `<option ${k === tSym ? "selected" : ""}>${k}</option>`).join("")}</select></label>
          <label>Qty<input id="t_qty" type="number" min="1" value="${pre ? pre.qty : 1}"></label>
          <label>Stop (ticks)<input id="t_stop" type="number" min="1" value="${defStop}"></label>
          <label>Target (ticks)<input id="t_target" type="number" min="1" value="${pre ? pre.target : defStop * 2}"></label>
          <label>Risk % of buffer<input id="t_risk" type="number" min="1" max="100" value="20"></label>
          <label>Route<select id="t_route"><option value="paper">PAPER</option><option value="live">LIVE (broker)</option></select></label>
        </div>
        <div class="row-actions"><button class="btn" data-act="size">SIZE IT</button><span id="sizeOut" class="muted"></span></div>
        <div class="row-actions"><button class="btn buy" data-act="order" data-side="BUY">BUY MKT</button><button class="btn sell" data-act="order" data-side="SELL">SELL MKT</button>
          <span class="muted">Brackets: stop + target as OCO</span></div>
        <div id="ticketMsg"></div>
      </div></div>
      <div class="cols">
        <div class="box"><div class="sec">POSITIONS</div><div id="posTbl"></div><div class="sec">WORKING ORDERS</div><div id="workTbl"></div></div>
        <div class="box"><div class="sec">TRADE LOG</div><div id="tradeTbl" class="scroll" style="max-height:220px"></div></div>
      </div>`;
    renderPropTables(st);
  },

  async ALRT(v) {
    setTitle("ALERTS · BUILT-IN TRIGGERS + CUSTOM RULES");
    tools("");
    const rules = await api("/api/alerts/rules");
    const fields = ["last", "chg_pct", "swing", "day", "trend", "cot_index", "sd_z", "p_up_5d"];
    v.innerHTML = `<div class="cols"><div class="box"><div class="sec">NEW RULE</div>
      <div class="form">
        <label>Symbol<select id="a_sym">${Object.keys(S.universe).map((k) => `<option ${k === S.sel ? "selected" : ""}>${k}</option>`).join("")}</select></label>
        <label>Field<select id="a_field">${fields.map((f) => `<option>${f}</option>`).join("")}</select></label>
        <label>Op<select id="a_op"><option>&gt;</option><option>&lt;</option><option>&gt;=</option><option>&lt;=</option></select></label>
        <label>Value<input id="a_val" type="number" step="any" value="${S.rows[S.sel]?.last ?? 0}"></label>
        <label>Note<input id="a_note" placeholder="optional"></label>
      </div><div class="row-actions"><button class="btn amber" data-act="addRule">ADD RULE</button></div>
      <div class="muted" style="margin-top:6px">Built-in: COT crowded long/short (index ≥90/≤10), spec repositioning ≥2σ, inventory vs 5y ≥1.25σ, inventory surprise ≥1.5σ, swing composite → STRONG, intraday bias flips, pivot/prev-day level crosses, vol regime → HIGH, prop drawdown/daily-loss proximity.</div></div>
      <div class="box"><div class="sec">ACTIVE RULES</div><table class="tbl"><tbody>${rules.map((r) => `<tr><td class="sym">${r.symbol}</td><td>${r.field} ${esc(r.op)} ${r.value}</td><td>${r.active ? "ACTIVE" : "FIRED"}</td><td><button class="tbtn" data-act="delRule" data-id="${r.id}">DEL</button></td></tr>`).join("") || '<tr><td class="muted">No custom rules yet.</td></tr>'}</tbody></table></div></div>
      <div class="scroll" id="alertFull"></div>`;
    $("#alertFull", v).innerHTML = S.alerts.map(alertRow).join("");
  },

  async HELP(v) {
    setTitle("HELP · COMMANDS");
    tools("");
    const cmds = [
      ["CL", "Load a security (any root: ES NQ YM CL NG RB HO GC SI HG PL ZC ZS ZW KC SB CC CT LE HE + micros MES MNQ MYM MCL MGC)"],
      ["CL COT / CL SD / CL FCST / CL MON", "Load security into a function screen"],
      ["COT · SD · FCST · SIG · PROP · ALRT · MON · BT · NEWS", "Open a function screen (or F1–F10)"],
      ["BUY 2 MCL / SELL 1 CL", "Paper market order with the ticket's stop/target ticks, after prop rule checks"],
      ["ALRT CL > 70 / ALRT NG SWING < -50", "Create a custom alert (field defaults to LAST)"],
      ["FLAT", "Flatten all paper positions"],
      ["DT / INSIDER, CL DT", "Day-Trade Insider: today's game plan, levels, volume profile, regime, catalysts, ranked playbook, risk plan"],
      ["TS / TRADES", "Trade signals: entry, stop loss, TP1/TP2, strategy, confidence, live status, notifications, track record"],
      ["IR / RESEARCH, ES IR", "Intraday research: top-down W→D→4H→1H→15m→5m→1m, research-backed strategies, optimal timeframe with out-of-sample validation"],
      ["BT / BACKTEST (F9)", "Backtest lab: run, compare, optimise, walk-forward, Monte Carlo"],
      ["NEWS (F10) / CL NEWS", "News feed with LONG/SHORT signal and ★–★★★ importance per market"],
      ["↑ / ↓", "Move through the monitor"],
    ];
    v.innerHTML = `<div class="pad help"><table class="tbl"><tbody>${cmds.map(([c, d]) => `<tr><td><code>${c}</code></td><td>${d}</td></tr>`).join("")}</tbody></table>
      <div class="sec">METHODOLOGY</div>
      <p><b class="amber">Quant:</b> time-series momentum (20/60/120d vol-normalised t-stats), Donchian breakout, 20d z-score + RSI mean reversion, front/second-month carry, same-window seasonality, realised-vol regime.</p>
      <p><b class="amber">Quantamental:</b> CFTC disaggregated COT (managed-money index contrarian at extremes, commercial index confirming), weekly inventories vs 5-year seasonal norm and surprise vs normal weekly change.</p>
      <p><b class="amber">Forecast:</b> shrunk momentum drift + AR(1) + seasonal drift, EWMA volatility cone, walk-forward hit rate disclosed.</p>
      <p><b class="amber">Prop:</b> trailing EOD/intraday/static drawdown with lock at start balance, daily loss limit, consistency, contract caps, risk-of-buffer position sizing, bracket orders.</p></div>`;
  },
};

async function renderView() {
  destroyCharts();
  document.querySelectorAll("#fkeys button").forEach((b) => b.classList.toggle("active", b.dataset.view === S.view));
  document.body.classList.toggle("wide", ["BT", "DT", "IR", "TS"].includes(S.view)); // these use the detail column too
  const v = $("#view");
  v.innerHTML = "";
  try {
    await VIEWS[S.view](v);
  } catch (e) {
    console.error(e);
    v.innerHTML = `<div class="pad notice danger">${esc(e.message)}</div>`;
  }
}

// ------------------------------------------------------------------ prop
function meter(label, value, max, valText, invert = false) {
  const pct = max ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  const level = invert ? (pct > 75 ? "bad" : pct > 50 ? "warn" : "ok") : (pct < 25 ? "bad" : pct < 50 ? "warn" : "ok");
  return `<div class="meter"><div class="top"><span>${label}</span><span>${valText}</span></div>
    <div class="track" role="meter" aria-valuenow="${pct.toFixed(0)}" aria-valuemin="0" aria-valuemax="100" aria-label="${label}"><div class="fill ${level}-fill" style="width:${pct}%"></div></div></div>`;
}
function renderPropMini(st) {
  if (!st) return;
  S.prop = { ...S.prop, ...st };
  const r = st.rules;
  $("#propName").textContent = r.name;
  const dlUsed = r.daily_loss_limit ? Math.max(0, -st.today_pnl) : 0;
  $("#propMini").innerHTML = `
    ${st.breached ? `<div class="notice danger">BREACHED: ${esc(st.breached)}</div>` : st.passed ? '<div class="notice">OBJECTIVES MET ✓</div>' : ""}
    <div class="kv"><span>Equity</span><span class="big ${cls(st.total_pnl)}" style="font-size:16px">${money(st.equity)}</span>
      <span>Today / Total</span><span><span class="${cls(st.today_pnl)}">${money(st.today_pnl)}</span> / <span class="${cls(st.total_pnl)}">${money(st.total_pnl)}</span></span></div>
    ${r.profit_target ? meter("Profit target", Math.max(0, st.total_pnl), r.profit_target, `${fx(Math.max(0, st.target_progress_pct), 0)}% of ${money(r.profit_target)}`, false).replace(/(bad|warn)-fill/, "warn-fill") : ""}
    ${meter("Drawdown buffer", st.distance_to_breach, r.max_drawdown, `${money(st.distance_to_breach)} · liq ${money(st.threshold)}`)}
    ${r.daily_loss_limit ? meter("Daily loss used", dlUsed, r.daily_loss_limit, `${money(dlUsed)} / ${money(r.daily_loss_limit)}`, true) : ""}
    <div class="kv"><span>Days · Consistency</span><span>${st.trading_days}/${r.min_trading_days} · ${st.consistency_ratio == null ? "—" : fx(st.consistency_ratio, 0) + "%"} ${st.consistency_ok ? "✓" : "✗"}</span>
      <span>Open contracts</span><span>${fx(st.open_contracts, 1)} / ${r.max_contracts}</span></div>`;
}
function renderPropTables(st) {
  const pos = Object.entries(st.positions || {});
  const pTbl = $("#posTbl"); if (!pTbl) return;
  pTbl.innerHTML = pos.length ? `<table class="tbl"><thead><tr><th>SYM</th><th>QTY</th><th>AVG</th><th>LAST</th><th>UPNL</th></tr></thead><tbody>${pos.map(([s, p]) => {
    const u = S.universe[s], last = S.rows[s]?.last;
    const upnl = last != null ? (last - p.avg) * p.qty * u.point_value : null;
    return `<tr><td class="sym">${s}</td><td class="${cls(p.qty)}">${p.qty}</td><td>${px(s, p.avg)}</td><td>${px(s, last)}</td><td class="${cls(upnl)}">${money(upnl)}</td></tr>`;
  }).join("")}</tbody></table>` : '<div class="muted">Flat.</div>';
  const w = st.orders?.working || [];
  $("#workTbl").innerHTML = w.length ? `<table class="tbl"><tbody>${w.map((o) => `<tr><td class="sym">${o.symbol}</td><td class="${cls(o.qty)}">${o.qty}</td><td>${o.type}</td><td>${px(o.symbol, o.price)}</td><td><button class="tbtn" data-act="cancel" data-id="${o.id}">CXL</button></td></tr>`).join("")}</tbody></table>` : '<div class="muted">None.</div>';
  const t = (st.trades || []).slice().reverse();
  $("#tradeTbl").innerHTML = t.length ? `<table class="tbl"><thead><tr><th>TIME</th><th>SYM</th><th>PRICE</th><th>REALISED</th><th>TYPE</th></tr></thead><tbody>${t.map((x) => `<tr><td>${new Date(x.ts * 1000).toLocaleTimeString()}</td><td class="sym">${x.symbol}</td><td>${px(x.symbol, x.price)}</td><td class="${cls(x.realized)}">${money(x.realized)}</td><td>${x.tag}</td></tr>`).join("")}</tbody></table>` : '<div class="muted">No fills yet.</div>';
}
async function refreshPropView() {
  if (S.view !== "PROP") return;
  const st = await api("/api/prop");
  S.prop = st;
  renderPropTables(st);
}
async function sendOrder(side, opts = {}) {
  const body = {
    symbol: opts.symbol || $("#t_sym")?.value || S.sel, side,
    qty: opts.qty || parseInt($("#t_qty")?.value || "1", 10),
    stop_ticks: opts.stop ?? (parseInt($("#t_stop")?.value || "0", 10) || null),
    target_ticks: opts.target ?? (parseInt($("#t_target")?.value || "0", 10) || null),
    route: $("#t_route")?.value || "paper",
  };
  if (body.stop_ticks == null) {
    const lv = S.snap.levels; body.stop_ticks = Math.max(4, Math.round(lv.atr_ticks * 0.2)); body.target_ticks = body.stop_ticks * 2;
  }
  const msg = $("#ticketMsg");
  try {
    const res = await api("/api/orders", { method: "POST", body });
    const o = res.order;
    toast(`${side} ${body.qty} ${body.symbol} @ ${px(body.symbol, o.fill_price)}`, `Stop ${body.stop_ticks}t · target ${body.target_ticks}t · worst-case ${money(res.check.worst_case_loss)}`, "INFO");
    if (msg) msg.innerHTML = res.check.warnings.map((w) => `<div class="notice">${esc(w)}</div>`).join("");
    refreshPropView();
  } catch (e) {
    const reasons = e.data?.detail?.reasons || [e.message];
    toast("ORDER REJECTED", reasons.join(" · "), "CRITICAL");
    if (msg) msg.innerHTML = reasons.map((w) => `<div class="notice danger">${esc(w)}</div>`).join("");
  }
}

// ------------------------------------------------------------------ alerts
function alertRow(a, isNew = false) {
  const t = new Date(a.ts * 1000).toLocaleTimeString("en-GB");
  return `<div class="al ${isNew ? "new" : ""}" data-sym="${a.symbol}"><span class="t">${t}</span><span class="sev ${a.severity}">${a.severity}</span><span class="s">${a.kind}</span>
    <span><b>${esc(a.title)}</b> <span class="d">${esc(a.detail)}</span></span></div>`;
}
function renderAlerts() {
  $("#alertFeed").innerHTML = S.alerts.slice(0, 80).map((a) => alertRow(a)).join("");
  $("#alertCount").textContent = `${S.alerts.length} today`;
}

// ------------------------------------------------------------------ command line
async function runCommand(raw) {
  const parts = raw.trim().toUpperCase().split(/\s+/).filter(Boolean);
  if (!parts.length) return;
  const views = { MON: "MON", COT: "COT", SD: "SD", "S&D": "SD", FCST: "FCST", SIG: "SIG", PROP: "PROP", ALRT: "ALRT", HELP: "HELP", BT: "BT", BACKTEST: "BT", NEWS: "NEWS", N: "NEWS", DT: "DT", INSIDER: "DT", IR: "IR", RESEARCH: "IR", TS: "TS", TRADES: "TS", SIGNALS: "TS" };
  const [a, b, c, d, e] = parts;
  if (a === "BUY" || a === "SELL") {
    const qty = parseInt(b, 10);
    const sym = Number.isNaN(qty) ? b : c;
    if (!S.universe[sym]) return foot("Usage: BUY 2 MCL");
    if (sym !== S.sel) await select(sym);
    return sendOrder(a, { symbol: sym, qty: Number.isNaN(qty) ? 1 : qty });
  }
  if (a === "FLAT" || a === "FLATTEN") { await api("/api/prop/flatten", { method: "POST" }); toast("FLATTENED", "All paper positions closed", "WARN"); return refreshPropView(); }
  if (a === "ALRT" && b && S.universe[b] && c) {
    let field = "last", op = c, val = d;
    if (![">", "<", ">=", "<="].includes(c)) { field = c.toLowerCase(); op = d; val = e; }
    try {
      await api("/api/alerts/rules", { method: "POST", body: { symbol: b, field, op, value: parseFloat(val) } });
      toast("ALERT RULE ADDED", `${b} ${field} ${op} ${val}`);
    } catch (err) { foot(err.message); }
    if (S.view === "ALRT") renderView();
    return;
  }
  if (views[a]) { S.view = views[a]; return renderView(); }
  if (S.universe[a]) {
    if (views[b] === "NEWS" && typeof NEWS !== "undefined") NEWS.filter.symbol = S.universe[a].parent || a;
    return select(a, views[b] || S.view);
  }
  foot(`Unknown command: ${raw}. Type HELP.`);
}

// ------------------------------------------------------------------ websocket
function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  S.ws = ws;
  ws.onopen = () => { $("#connBadge").textContent = "LIVE STREAM"; $("#connBadge").className = "badge ok"; };
  ws.onclose = () => { $("#connBadge").textContent = "RECONNECTING"; $("#connBadge").className = "badge bad"; setTimeout(connect, 2000); };
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === "hello") {
      m.rows.forEach((r) => (S.rows[r.symbol] = r));
      S.alerts = m.alerts;
      const mode = m.data_mode.toUpperCase();
      $("#modeBadge").textContent = `DATA: ${mode}`;
      $("#modeBadge").className = `badge ${mode === "LIVE" ? "live" : "sim"}`;
      renderWatch(); renderTape(); renderAlerts(); renderPropMini(m.prop);
    } else if (m.type === "tick") {
      for (const [sym, q] of Object.entries(m.quotes)) {
        const targets = [sym, ...Object.values(S.universe).filter((u) => u.parent === sym).map((u) => u.symbol)];
        for (const t of targets) {
          if (!S.rows[t]) continue;
          Object.assign(S.rows[t], { last: q.last, chg: q.chg, chg_pct: q.chg_pct });
          updateQuoteCells(t, q);
        }
      }
      onSelectedTick(m.quotes);
      renderPropMini(m.prop);
      if (Date.now() - S.tapeAt > 15000) { renderTape(); S.tapeAt = Date.now(); }
      if (S.view === "PROP") renderPropTables({ ...S.prop, ...m.prop, trades: S.prop?.trades, orders: S.prop?.orders });
    } else if (m.type === "signals") {
      m.rows.forEach((r) => (S.rows[r.symbol] = r));
      renderWatch();
      api(`/api/snapshot/${S.sel}`).then((s) => { S.snap = s; renderDetail(); }).catch(() => {});
    } else if (m.type === "alert") {
      S.alerts.unshift(m.alert);
      const feed = $("#alertFeed");
      feed.insertAdjacentHTML("afterbegin", alertRow(m.alert, true));
      while (feed.children.length > 80) feed.lastChild.remove();
      $("#alertCount").textContent = `${S.alerts.length} today`;
      // trade-signal events have their own richer toast (see tsignals.js)
      if (m.alert.kind !== "TRADE" && (m.alert.severity !== "INFO" || m.alert.symbol === S.sel)) toast(m.alert.title, m.alert.detail, m.alert.severity);
    } else if (m.type === "fill") {
      refreshPropView();
    } else if (m.type === "tsignal" && typeof onTradeSignal === "function") {
      onTradeSignal(m);
    } else if (m.type === "news" && typeof onNews === "function") {
      onNews(m.items);
    }
  };
}
function onSelectedTick(quotes) {
  const sym = S.sel, u = S.universe[sym];
  const q = quotes[u?.parent || sym];
  if (!q) return;
  const l = $("#dLast"), c = $("#dChg");
  if (l) l.textContent = px(sym, q.last);
  const dl = $("#dtLast"), dc = $("#dtChg");
  if (dl) dl.textContent = px(sym, q.last);
  if (dc) { dc.textContent = `${signed(q.chg, decimals(sym))} (${signed(q.chg_pct, 2)}%)`; dc.className = cls(q.chg); }
  if (c) { c.textContent = `${signed(q.chg, decimals(sym))} (${signed(q.chg_pct, 2)}%)`; c.className = cls(q.chg); }
  if (S.mainSeries && (S.view === "MON" || S.view === "FCST")) {
    const data = S.mainSeries.data();
    const last = data[data.length - 1];
    if (!last) return;
    if (S.chartMode === "I" && S.view === "MON") {
      const minute = Math.floor(Date.now() / 60000) * 60;
      if (minute > last.time) S.mainSeries.update({ time: minute, open: q.last, high: q.last, low: q.last, close: q.last });
      else S.mainSeries.update({ ...last, high: Math.max(last.high, q.last), low: Math.min(last.low, q.last), close: q.last });
    } else {
      S.mainSeries.update({ ...last, high: Math.max(last.high, q.last), low: Math.min(last.low, q.last), close: q.last });
    }
  }
}

// ------------------------------------------------------------------ events
document.addEventListener("click", async (ev) => {
  const t = ev.target.closest("[data-act],[data-view],[data-sym],[data-mode]");
  if (!t) return;
  if (t.dataset.mode) { S.chartMode = t.dataset.mode; return renderView(); }
  if (t.closest("#fkeys")) { S.view = t.dataset.view; return renderView(); }
  const act = t.dataset.act;
  if (act === "ticket") { S.view = "PROP"; await renderView(); return; }
  if (act === "order") return sendOrder(t.dataset.side);
  if (act === "flatten") return runCommand("FLAT");
  if (act === "cancel") { await api(`/api/orders/${t.dataset.id}`, { method: "DELETE" }); return refreshPropView(); }
  if (act === "size") {
    try {
      const r = await api("/api/prop/size", { method: "POST", body: { symbol: $("#t_sym").value, stop_ticks: parseInt($("#t_stop").value, 10), risk_pct: parseFloat($("#t_risk").value) } });
      $("#t_qty").value = Math.max(1, r.contracts);
      $("#sizeOut").textContent = `${r.contracts} contracts · budget ${money(r.risk_budget)} · ${money(r.risk_per_contract)}/ct${r.capped_by_max_contracts ? " · capped" : ""}`;
    } catch (e) { $("#sizeOut").textContent = e.message; }
    return;
  }
  if (act === "loadPreset") { await api("/api/prop/rules", { method: "POST", body: { preset: $("#preset").value } }); toast("PRESET LOADED", $("#preset").selectedOptions[0].text); return renderView(); }
  if (act === "saveRules") {
    const num = (id) => { const v = $(`#r_${id}`).value; return v === "" ? null : parseFloat(v); };
    const rules = {
      name: "Custom", account_size: num("account_size"), profit_target: num("profit_target") ?? 0,
      daily_loss_limit: num("daily_loss_limit"), max_drawdown: num("max_drawdown"), drawdown_mode: $("#r_drawdown_mode").value,
      trail_locks_at_start: $("#r_drawdown_mode").value !== "static", max_contracts: num("max_contracts"),
      consistency_pct: num("consistency_pct"), min_trading_days: num("min_trading_days") ?? 0, commission_per_side: num("commission_per_side") ?? 0,
    };
    try { await api("/api/prop/rules", { method: "POST", body: { rules } }); toast("RULES APPLIED", "Account reset with custom rules"); } catch (e) { toast("RULES REJECTED", e.message, "CRITICAL"); }
    return renderView();
  }
  if (act === "resetProp") { await api("/api/prop/reset", { method: "POST" }); return renderView(); }
  if (act === "addRule") {
    try {
      await api("/api/alerts/rules", { method: "POST", body: { symbol: $("#a_sym").value, field: $("#a_field").value, op: $("#a_op").value, value: parseFloat($("#a_val").value), note: $("#a_note").value } });
      toast("ALERT RULE ADDED", `${$("#a_sym").value} ${$("#a_field").value} ${$("#a_op").value} ${$("#a_val").value}`);
    } catch (e) { toast("RULE REJECTED", e.message, "CRITICAL"); }
    return renderView();
  }
  if (act === "delRule") { await api(`/api/alerts/rules/${t.dataset.id}`, { method: "DELETE" }); return renderView(); }
  if (t.dataset.sym && t.dataset.sym !== "PROP" && S.universe[t.dataset.sym]) return select(t.dataset.sym, t.dataset.view || undefined);
});
document.addEventListener("keydown", (ev) => {
  const fmap = { F1: "MON", F2: "COT", F3: "SD", F4: "FCST", F5: "SIG", F6: "PROP", F7: "ALRT", F8: "HELP", F9: "BT", F10: "NEWS" };
  if (fmap[ev.key]) { ev.preventDefault(); S.view = fmap[ev.key]; return renderView(); }
  const inInput = ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName);
  if (!inInput && (ev.key === "ArrowDown" || ev.key === "ArrowUp")) {
    ev.preventDefault();
    const syms = [...document.querySelectorAll("#watch tr.row")].map((r) => r.dataset.sym);
    const i = syms.indexOf(S.sel) + (ev.key === "ArrowDown" ? 1 : -1);
    if (syms[i]) select(syms[i]);
  } else if (!inInput && ev.key.length === 1 && /[a-z]/i.test(ev.key) && !ev.metaKey && !ev.ctrlKey) {
    $("#cmd").focus();
  }
});
$("#themeBtn").addEventListener("click", () => {
  const cur = document.documentElement.dataset.theme;
  applyTheme(THEMES[(THEMES.indexOf(cur) + 1) % THEMES.length]);
  renderWatch();
  renderView();               // rebuild charts with the new theme's colours
  foot(`Theme: ${document.documentElement.dataset.theme}`);
});
$("#cmdForm").addEventListener("submit", (ev) => { ev.preventDefault(); const v = $("#cmd").value; $("#cmd").value = ""; runCommand(v); });

// ------------------------------------------------------------------ boot
(async function boot() {
  tickClocks(); setInterval(tickClocks, 1000);
  const u = await api("/api/universe");
  u.forEach((c) => (S.universe[c.symbol] = c));
  const rows = await api("/api/watchlist");
  rows.forEach((r) => (S.rows[r.symbol] = r));
  renderWatch(); renderTape();
  connect();
  await select(S.sel);
})();
