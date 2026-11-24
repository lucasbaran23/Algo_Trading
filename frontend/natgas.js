/* Nat Gas Desk: the MVP market. Contracts & roll, next-session outlook, weather, storage, seasonality macro -> micro. */
"use strict";

const NGD = { o: null, s: null, w: null, k: null, ins: null };
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WDS = ["Mon", "Tue", "Wed", "Thu", "Fri"];

function ngHeat(v, max, diverging = true) {
  if (v == null) return "";
  const a = Math.min(1, Math.abs(v) / (max || 1)) * 0.6;
  return diverging ? `background:rgb(var(--${v >= 0 ? "up" : "down"}-rgb) / ${a})` : `background:color-mix(in srgb, var(--s1) ${Math.round(a * 100)}%, transparent)`;
}

// ------------------------------------------------------------------ cards
function ngContracts() {
  const k = NGD.k, i = k.info;
  return `<div class="box"><div class="sec">CONTRACTS &amp; ROLL <span class="muted">session ${k.session}</span></div>
    <div class="ng-strip">${k.strip.map((c, idx) => `<div class="ng-ct ${idx === 0 ? "front" : idx === 1 && i.roll_window ? "active" : ""}">
      <b>${c.code}</b><span>${c.month} ${c.year}</span><span class="muted">LTD ${c.last_trade}</span>
      ${idx === 0 ? `<span class="badge ${i.roll_window ? "sim" : "live"}">${i.expiry_day ? "EXPIRES TODAY" : i.roll_window ? `${i.trading_days_left} SESSION${i.trading_days_left === 1 ? "" : "S"} LEFT` : "FRONT"}</span>` : idx === 1 && i.roll_window ? '<span class="badge live">TRADE THIS</span>' : ""}</div>`).join("")}</div>
    <div class="kv" style="margin-top:6px">
      <span>Front-month last trade</span><span>${i.last_trade_weekday} ${i.last_trade}</span>
      <span>NG options (LNE) expiry</span><span>${i.options_expiry}</span>
      <span>Micro (MNG) last trade</span><span>${i.micro_last_trade}</span>
      <span>Contract sizes</span><span>NG 10,000 · QG 2,500 · MNG 1,000 MMBtu</span>
      <span>Tick value</span><span>NG $10 · QG $12.50 · MNG $1</span></div>
    ${i.roll_window ? `<div class="notice">${i.front} is in its expiry window: liquidity has moved to ${i.next}. Day-trade ${i.next}; the expiring contract can be thin and erratic.</div>` : ""}
    <div class="muted">Rule: trading ends 3 business days before the delivery month (holidays skipped). Verify on CME's calendar.</div></div>`;
}

function ngOutlook() {
  const o = NGD.o, e = o.expected, f = o.factors;
  const bar = (label, v) => `<div class="sigrow"><span class="lbl">${label}</span><div class="bar"><i class="${v >= 1 ? "pos" : "neg"}" style="width:${Math.min(50, Math.abs(v - 1) * 100)}%"></i></div><span class="num">×${fx(v, 2)}</span></div>`;
  return `<div class="box ng-outlook"><div class="sec">NEXT SESSION · ${o.weekday.toUpperCase()} ${o.date} <span class="muted">what to expect</span></div>
    <div class="tiles">
      ${tile("Expected range", `${fx(e.pct, 1)}%`)}${tile("Per NG contract", money(e.usd))}${tile("Per QG", money(e.usd_qg))}${tile("Per MNG", money(e.usd_mng))}
      ${tile("Ticks", e.ticks)}${tile("Big day (p80)", `${fx(e.p80_pct, 1)}%`, "down")}${tile("If centred here: high", px("NG", e.high), "up")}${tile("low", px("NG", e.low), "down")}
    </div>
    <div class="sec">BUILD-UP <span class="muted">recent ${fx(f.base_pct, 2)}%/day · ${MON[parseInt(o.date.slice(5, 7)) - 1]} norm ${fx(f.month_norm_pct, 2)}% · year ${fx(f.year_norm_pct, 2)}%</span></div>
    ${bar(`${o.weekday} effect`, f.weekday)}${bar("Expiry cycle", f.expiry)}${bar("Holiday", f.holiday)}${bar("Now vs season", f.vs_season)}
    <ul class="dt-bullets">${o.bullets.map((b) => `<li>${esc(b)}</li>`).join("")}</ul>
    ${o.events.length ? `<div class="sec">SCHEDULED</div>${o.events.map((ev) => `<div class="dt-ev s${ev.stars}"><span class="t">${ev.time || "all day"}</span><span><b>${esc(ev.name)}</b> ${starsHtml(ev.stars)}</span></div>`).join("")}` : ""}</div>`;
}

function ngWeather() {
  const w = NGD.w, d = w.daily;
  const W = 520, H = 170, pad = 22, bw = (W - pad) / d.length;
  const mx = Math.max(...d.map((x) => Math.max(x.hdd, x.normal)), 1);
  const y = (v) => H - 18 - (v / mx) * (H - 30);
  const bars = d.map((x, i) => `<rect x="${pad + i * bw + 1}" y="${y(x.hdd)}" width="${Math.max(1, bw - 2)}" height="${H - 18 - y(x.hdd)}" rx="1.5"
      style="fill:${x.kind === "fc" ? "var(--s3)" : "var(--muted)"};opacity:${x.kind === "fc" ? 0.9 : 0.6}"><title>${x.date}: ${x.hdd} HDD (${x.kind === "fc" ? "forecast" : "observed"}), normal ${x.normal}</title></rect>`).join("");
  const norm = d.map((x, i) => `${pad + i * bw + bw / 2},${y(x.normal)}`).join(" ");
  const today = d.findIndex((x) => x.kind === "fc");
  return `<div class="box"><div class="sec">WEATHER · GAS-WEIGHTED HDD <span class="muted">${esc(w.source)}${w.source === "SIM" ? " · synthetic, link to price illustrative" : ""}</span></div>
    <div class="headline"><span class="bias ${w.bias === "BULLISH" ? "b-up" : w.bias === "BEARISH" ? "b-down" : "b-flat"}">${w.bias}</span><span>${esc(w.bias_why)}</span></div>
    <svg viewBox="0 0 ${W} ${H}" class="ng-wx" role="img" aria-label="Heating degree days, last 3 weeks observed and next 14 days forecast, versus normal">
      ${bars}<polyline fill="none" stroke-width="2" style="stroke:var(--warn)" points="${norm}"/>
      ${today > 0 ? `<line x1="${pad + today * bw}" x2="${pad + today * bw}" y1="6" y2="${H - 18}" stroke-dasharray="3 3" style="stroke:var(--ink-2)"/><text x="${pad + today * bw + 4}" y="14" font-size="10" style="fill:var(--ink-2)">today → forecast</text>` : ""}
      <text x="${pad}" y="${H - 4}" font-size="10" style="fill:var(--muted)">${d[0]?.date}</text><text x="${W - 4}" y="${H - 4}" font-size="10" text-anchor="end" style="fill:var(--muted)">${d[d.length - 1]?.date}</text>
    </svg>
    <div class="legend"><span><i style="background:var(--muted)"></i>observed</span><span><i style="background:var(--s3)"></i>forecast</span><span><i style="background:var(--warn)"></i>normal (10-yr)</span></div>
    <div class="kv"><span>Next 7 days</span><span>${fx(w.next7.hdd, 0)} HDD vs ${fx(w.next7.normal, 0)} normal (${signed(w.next7.anomaly, 0)})</span>
      <span>Next 14 days</span><span>${fx(w.next14.hdd, 0)} vs ${fx(w.next14.normal, 0)} (${signed(w.next14.pct, 1)}%)</span>
      <span>Cities</span><span class="muted">${w.cities.length} demand-weighted (${w.cities.slice(0, 4).join(", ")}…)</span></div></div>`;
}

function ngCurve() {
  const cv = NGD.k?.curve;
  if (!cv) return "";
  const rows = cv.contracts.filter((r) => r.last != null), sm = cv.summary;
  if (!rows.length) return "";
  const W = 620, H = 200, padL = 40, padB = 34, bw = (W - padL - 8) / rows.length;
  const lo = Math.min(...rows.map((r) => r.last)) * 0.95, hi = Math.max(...rows.map((r) => r.last)) * 1.03;
  const y = (v) => 8 + (1 - (v - lo) / (hi - lo)) * (H - padB - 8);
  const bars = rows.map((r, i) => `<rect x="${padL + i * bw + 2}" y="${y(r.last)}" width="${bw - 4}" height="${H - padB - y(r.last)}" rx="2"
      style="fill:${r.winter ? "var(--s3)" : "var(--s1)"};opacity:${i === 0 ? 1 : 0.8}"><title>${r.code} ${r.label}: ${px("NG", r.last)} (${signed(r.chg, 3)}) · LTD ${r.last_trade}</title></rect>
    <text x="${padL + i * bw + bw / 2}" y="${y(r.last) - 3}" font-size="9" text-anchor="middle" style="fill:var(--ink-2)">${fx(r.last, 2)}</text>
    <text x="${padL + i * bw + bw / 2}" y="${H - padB + 12}" font-size="9" text-anchor="middle" style="fill:var(--muted)">${r.label.split(" ")[0]}</text>
    ${i === 0 || r.month === 1 ? `<text x="${padL + i * bw + bw / 2}" y="${H - padB + 24}" font-size="9" text-anchor="middle" style="fill:var(--muted)">'${r.label.split(" ")[1]}</text>` : ""}`).join("");
  const live = cv.source.startsWith("LIVE");
  return `<div class="box"><div class="sec">FUTURES CURVE <span class="muted">${esc(cv.source)}${live ? "" : " · synthetic shape"}</span></div>
    <div class="tiles">${tile("M1–M2", `${signed(sm.m1_m2, 3)} (${signed(sm.m1_m2_pct, 1)}%)`, sm.m1_m2 > 0 ? "" : "up")}${tile("Front shape", sm.shape || "—")}
      ${tile("Winter strip", px("NG", sm.winter_strip))}${tile("Summer strip", px("NG", sm.summer_strip))}
      ${tile("Winter premium", signed(sm.winter_premium, 3))}${tile(sm.h_j_codes ? `H/J ${sm.h_j_codes.map((c) => c.slice(2)).join("/")}` : "H/J", signed(sm.h_j, 3), sm.h_j > 0.3 ? "up" : "")}</div>
    <svg viewBox="0 0 ${W} ${H}" class="opt-svg" role="img" aria-label="Natural gas futures prices by contract month">${bars}
      <text x="4" y="${y(hi / 1.03) + 4}" font-size="9" style="fill:var(--muted)">${fx(hi / 1.03, 2)}</text><text x="4" y="${H - padB}" font-size="9" style="fill:var(--muted)">${fx(lo / 0.95, 2)}</text></svg>
    <div class="legend"><span><i style="background:var(--s3)"></i>Winter (Dec–Mar)</span><span><i style="background:var(--s1)"></i>Other months</span></div>
    <div class="muted">Contango at the front (M2 above M1) = comfortable supply; backwardation = a tight prompt market. A wide H/J (March over April) = the market fears low end-of-winter storage.
      ${NGD.k.rolls?.length ? `Continuous NG=F history is back-adjusted at each roll (latest: ${NGD.k.rolls[NGD.k.rolls.length - 1].date}, ${NGD.k.rolls[NGD.k.rolls.length - 1].expiring} → next, gap ${signed(100 * NGD.k.rolls[NGD.k.rolls.length - 1].gap, 1)}%).` : ""}</div></div>`;
}

function ngStorageReport() {
  const st = NGD.st;
  if (!st) return "";
  const rep = st.report, h = st.sd?.history || [];
  let chart = "";
  if (h.length) {
    const W = 620, H = 170, padL = 44, vals = h.flatMap((r) => [r.stocks, r.norm].filter((v) => v != null));
    const lo = Math.min(...vals) * 0.95, hi = Math.max(...vals) * 1.02;
    const x = (i) => padL + (i / (h.length - 1)) * (W - padL - 8), y = (v) => 8 + (1 - (v - lo) / (hi - lo)) * (H - 28);
    const line = (k, col, dash) => `<polyline fill="none" stroke-width="2" ${dash ? 'stroke-dasharray="4 3"' : ""} style="stroke:${col}" points="${h.map((r, i) => r[k] == null ? null : `${x(i)},${y(r[k])}`).filter(Boolean).join(" ")}"/>`;
    chart = `<svg viewBox="0 0 ${W} ${H}" class="opt-svg" role="img" aria-label="Working gas in storage vs the 5-year average, last two years">
      ${line("norm", "var(--warn)", true)}${line("stocks", "var(--s1)")}
      <text x="4" y="${y(hi / 1.02) + 4}" font-size="9" style="fill:var(--muted)">${fx(hi / 1.02, 0)}</text><text x="4" y="${y(lo / 0.95)}" font-size="9" style="fill:var(--muted)">${fx(lo / 0.95, 0)}</text>
      <text x="${padL}" y="${H - 4}" font-size="9" style="fill:var(--muted)">${h[0].date}</text><text x="${W - 8}" y="${H - 4}" font-size="9" text-anchor="end" style="fill:var(--muted)">${h[h.length - 1].date}</text></svg>
      <div class="legend"><span><i style="background:var(--s1)"></i>Working gas (Bcf)</span><span><i style="background:var(--warn)"></i>5-year average, same week</span></div>`;
  }
  return `<div class="box"><div class="sec">EIA STORAGE REPORT <span class="muted">${st.source === "LIVE" ? `EIA · ${rep ? `released ${rep.release_date} · week ending ${rep.week}` : ""}` : "SIM"}</span></div>
    ${rep ? `<table class="tbl"><thead><tr><th>REGION</th><th>BCF</th><th>NET CHANGE</th><th>VS YEAR AGO</th><th>VS 5-YR</th></tr></thead><tbody>
      ${rep.series.map((r) => `<tr><td class="sym">${esc(r.name.replace(" region", ""))}</td><td>${fx(r.now, 0)}</td><td class="${cls(r.net_change)}">${signed(r.net_change, 0)}</td>
        <td class="${cls(-r["pct-change_yrago"])}">${signed(r["pct-change_yrago"], 1)}%</td><td class="${cls(-r["pct-chg_5yr-avg"])}">${signed(r["pct-chg_5yr-avg"], 1)}%</td></tr>`).join("")}
      </tbody></table>` : ""}
    ${chart}
    <div class="muted">Released Thursdays 10:30 ET (Wednesday in Thanksgiving week). Green = below normal (bullish for price), red = above.</div></div>`;
}

// ------------------------------------------------------------------ supply & demand balance
function ngBalChart(b) {
  const sr = b.series.filter((r) => r.SUPPLY != null);
  if (!sr.length) return "";
  const W = 640, H = 230, padL = 40, padB = 22, n = sr.length;
  const vals = sr.flatMap((r) => [r.SUPPLY, r.DEMAND]);
  const lo = Math.min(...vals) * 0.97, hi = Math.max(...vals) * 1.02, bmax = Math.max(...sr.map((r) => Math.abs(r.BAL || 0)), 1);
  const x = (i) => padL + (i + 0.5) * ((W - padL - 8) / n), bw = (W - padL - 8) / n;
  const y = (v) => 8 + (1 - (v - lo) / (hi - lo)) * (H - padB - 60);
  const yb0 = H - padB - 26, yb = (v) => yb0 - (v / bmax) * 24;
  const fi = sr.findIndex((r) => r.forecast);
  const line = (k, col) => `<polyline fill="none" stroke-width="2" style="stroke:${col}" points="${sr.map((r, i) => `${x(i)},${y(r[k])}`).join(" ")}"/>`;
  return `<svg viewBox="0 0 ${W} ${H}" class="opt-svg" role="img" aria-label="Monthly U.S. gas supply and demand, with the implied storage change below">
    ${fi >= 0 ? `<rect x="${x(fi) - bw / 2}" y="4" width="${W - 8 - (x(fi) - bw / 2)}" height="${H - padB - 4}" style="fill:var(--ink);opacity:.05"/>
      <text x="${x(fi) - bw / 2 + 4}" y="16" font-size="10" style="fill:var(--muted)">EIA forecast →</text>` : ""}
    ${line("DEMAND", "var(--s2)")}${line("SUPPLY", "var(--s1)")}
    <line x1="${padL}" x2="${W - 8}" y1="${yb0}" y2="${yb0}" style="stroke:var(--line)"/>
    ${sr.map((r, i) => `<rect x="${x(i) - bw * 0.35}" width="${bw * 0.7}" y="${Math.min(yb(r.BAL), yb0)}" height="${Math.abs(yb(r.BAL) - yb0)}" style="fill:${r.BAL >= 0 ? "var(--down)" : "var(--up)"};opacity:.75"><title>${r.month}: ${signed(r.BAL, 1)} Bcf/d ${r.BAL >= 0 ? "injection" : "withdrawal"}</title></rect>`).join("")}
    ${sr.map((r, i) => r.month.endsWith("-01") ? `<text x="${x(i)}" y="${H - 6}" font-size="9" text-anchor="middle" style="fill:var(--muted)">${r.month.slice(0, 4)}</text>` : "").join("")}
    <text x="4" y="${y(hi / 1.02) + 4}" font-size="9" style="fill:var(--muted)">${fx(hi / 1.02, 0)}</text><text x="4" y="${y(lo / 0.97)}" font-size="9" style="fill:var(--muted)">${fx(lo / 0.97, 0)}</text>
    <text x="4" y="${yb0 + 4}" font-size="9" style="fill:var(--muted)">0</text></svg>
    <div class="legend"><span><i style="background:var(--s1)"></i>Supply</span><span><i style="background:var(--s2)"></i>Demand</span><span><i style="background:var(--down)"></i>Storage injection (surplus)</span><span><i style="background:var(--up)"></i>Withdrawal (deficit)</span></div>`;
}

function ngDrivers(b) {
  const d = b.drivers.filter((x) => Math.abs(x.yoy_bcfd) >= 0.05);
  if (!d.length) return "";
  const mx = Math.max(...d.map((x) => Math.abs(x.yoy_bcfd)), 0.5);
  return d.map((x) => {
    const bull = (x.side === "demand") === (x.yoy_bcfd > 0);        // more demand or less supply = tighter
    return `<div class="sigrow"><span class="lbl">${esc(x.name)} <span class="muted">${x.side}</span></span><div class="bar"><i style="${x.yoy_bcfd >= 0 ? "left:50%" : "right:50%"};width:${Math.min(50, Math.abs(x.yoy_bcfd) / mx * 50)}%;background:var(--${bull ? "up" : "down"})"></i></div><span class="num ${bull ? "up" : "down"}">${signed(x.yoy_bcfd, 2)}</span></div>`;
  }).join("");
}

function ngWeekly(wk) {
  if (!wk?.available) return '<div class="muted">Needs real weather and storage history (live mode).</div>';
  const w = wk.weeks.slice(-26), W = 640, H = 150, padL = 36, bw = (W - padL - 8) / w.length;
  const mx = Math.max(...w.map((r) => Math.abs(r.residual_bcfd)), 1), y0 = H / 2 - 6;
  return `<svg viewBox="0 0 ${W} ${H}" class="opt-svg" role="img" aria-label="Weekly storage change minus what weather explains">
    <line x1="${padL}" x2="${W - 8}" y1="${y0}" y2="${y0}" style="stroke:var(--line)"/>
    ${w.map((r, i) => `<rect x="${padL + i * bw + 2}" width="${bw - 4}" y="${r.residual_bcfd >= 0 ? y0 - r.residual_bcfd / mx * (y0 - 8) : y0}" height="${Math.abs(r.residual_bcfd) / mx * (y0 - 8)}" rx="1.5" style="fill:${r.residual_bcfd >= 0 ? "var(--down)" : "var(--up)"}"><title>Week ending ${r.date}: actual ${signed(r.actual, 0)} Bcf vs weather model ${signed(r.weather_model, 0)} (${r.hdd} HDD, ${r.cdd} CDD) → ${signed(r.residual_bcfd, 2)} Bcf/d</title></rect>`).join("")}
    <text x="4" y="14" font-size="9" style="fill:var(--muted)">looser</text><text x="4" y="${H - 18}" font-size="9" style="fill:var(--muted)">tighter</text>
    <text x="${padL}" y="${H - 4}" font-size="9" style="fill:var(--muted)">${w[0].date}</text><text x="${W - 8}" y="${H - 4}" font-size="9" text-anchor="end" style="fill:var(--muted)">${w[w.length - 1].date}</text></svg>`;
}

function ngBalance() {
  const b = NGD.bal;
  if (!b) return "";
  const t = b.totals, live = b.source.startsWith("LIVE");
  const get = (code) => [...b.supply, ...b.demand].find((x) => x.code === code) || {};
  const tl = (lab, code, flip) => { const g = get(code); return tile(lab, `${fx(g.value, 1)} <small class="${cls(flip ? -g.yoy : g.yoy)}">${signed(g.yoy, 1)} y/y</small>`); };
  const rc = get("NGRCPUS").value + get("NGCCPUS").value, rcy = get("NGRCPUS").yoy + get("NGCCPUS").yoy;
  const months = b.table, codes = [["NGPRPUS", "Dry production"], ["NGIMPUS_PIPE", "Canada imports"], ["SUPPLY", "TOTAL SUPPLY"],
    ["NGRCPUS", "Residential"], ["NGCCPUS", "Commercial"], ["NGINX", "Industrial"], ["NGEPCON", "Power burn"], ["NGEXPUS_LNG", "LNG exports"],
    ["NGEXPUS_PIPE", "Mexico exports"], ["NGLPPUS", "Lease & plant"], ["NGACPUS", "Pipeline fuel"], ["DEMAND", "TOTAL DEMAND"], ["BAL", "STORAGE CHANGE"], ["NGWGPUS", "Storage, end of month (Bcf)"]];
  const show = months.filter((_, i) => i >= months.length - 18).filter((_, i, a) => i >= a.findIndex((m) => m.forecast) - 6 && i < a.findIndex((m) => m.forecast) + 7);
  return `<div class="box"><div class="sec">SUPPLY &amp; DEMAND BALANCE <span class="muted">${esc(b.source)}${live ? ` · ${esc(b.forecast_date)} · actuals to ${b.last_actual}, forecast from ${b.forecast_start}` : " · synthetic"} · Bcf/d</span></div>
    <div class="tiles">
      ${tile(`Supply ${b.current_month}`, `${fx(t.supply, 1)} <small class="${cls(-t.supply_yoy)}">${signed(t.supply_yoy, 1)} y/y</small>`)}
      ${tile(`Demand ${b.current_month}`, `${fx(t.demand, 1)} <small class="${cls(t.demand_yoy)}">${signed(t.demand_yoy, 1)} y/y</small>`)}
      ${tile("Implied storage change", `${signed(t.balance, 1)} <small>${t.balance >= 0 ? "injection" : "withdrawal"}</small>`, t.balance_yoy > 0 ? "down" : "up")}
      ${tl("Dry production", "NGPRPUS", true)}${tl("LNG exports (≈ feedgas)", "NGEXPUS_LNG")}${tl("Power burn", "NGEPCON")}
      ${tile("Res + Com", `${fx(rc, 1)} <small class="${cls(rcy)}">${signed(rcy, 1)} y/y</small>`)}${tl("Industrial", "NGINX")}${tl("Mexico exports", "NGEXPUS_PIPE")}${tl("Canada imports", "NGIMPUS_PIPE", true)}
    </div>
    <ul class="dt-bullets">${b.bullets.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
    <div class="dt-row dt-r2b">
      <div><div class="sec">SUPPLY vs DEMAND, MONTHLY</div>${ngBalChart(b)}</div>
      <div><div class="sec">WHAT'S CHANGING <span class="muted">next 6 months vs a year earlier · green = tightens (bullish)</span></div>${ngDrivers(b)}</div>
    </div>
    <div class="sec">MONTH BY MONTH <span class="muted">shaded = EIA forecast · hover for y/y</span></div>
    <div style="overflow-x:auto"><table class="tbl ng-bal"><thead><tr><th></th>${show.map((m) => `<th class="${m.forecast ? "fc" : ""}">${m.month}</th>`).join("")}</tr></thead><tbody>
      ${codes.map(([c, lab]) => `<tr class="${["SUPPLY", "DEMAND", "BAL"].includes(c) ? "hl" : ""}"><td class="sym">${lab}</td>${show.map((m) => `<td class="${m.forecast ? "fc" : ""} ${c === "BAL" ? (m[c] >= 0 ? "down" : "up") : ""}" title="${m[c + "_yoy"] == null ? "" : `y/y ${signed(m[c + "_yoy"], 2)}`}">${m[c] == null ? "—" : c === "NGWGPUS" ? fx(m[c], 0) : c === "BAL" ? signed(m[c], 1) : fx(m[c], 1)}</td>`).join("")}</tr>`).join("")}
    </tbody></table></div>
    <div class="dt-row dt-r2b">
      <div><div class="sec">PRODUCTION BY BASIN <span class="muted">marketed, ${b.last_actual}</span></div>
        <table class="tbl"><thead><tr><th>BASIN</th><th>BCF/D</th><th>Y/Y</th></tr></thead><tbody>
        ${b.basins.map((x) => `<tr><td class="sym">${esc(x.name)}</td><td>${fx(x.value, 1)}</td><td class="${cls(-x.yoy)}">${signed(x.yoy, 2)}</td></tr>`).join("")}</tbody></table>
        ${b.end_of_march ? `<div class="kv" style="margin-top:6px"><span>End-of-March ${b.end_of_march.month.slice(0, 4)} storage (EIA)</span><span>${fx(b.end_of_march.forecast_bcf, 0)} Bcf vs 5-yr ${fx(b.end_of_march.avg5_bcf, 0)} (<b class="${cls(-b.end_of_march.vs_avg_pct)}">${signed(b.end_of_march.vs_avg_pct, 1)}%</b>)</span></div>` : ""}</div>
      <div><div class="sec">WEEKLY: TIGHTER OR LOOSER THAN WEATHER? <span class="muted">${b.weekly?.available ? `last 4 reports ${signed(b.weekly.last4_bcfd, 2)} Bcf/d · ${esc(b.weekly.verdict)}` : ""}</span></div>${ngWeekly(b.weekly)}
        <div class="muted">Each bar: the EIA weekly storage change minus what that week's heating/cooling degree days normally produce (fitted on the past 3 years). Below zero = more gas left storage than weather explains: LNG, power or exports pulling harder, or production slipping.</div></div>
    </div>
    <div class="muted">${live ? "Monthly data: EIA Short-Term Energy Outlook (history + forecast, updated monthly). Weekly: computed from EIA storage and Open-Meteo weather." : "Simulated balance. Set DATA_MODE=live for EIA data."}
      Daily pipeline flows (LNG feedgas by terminal, daily production) need a paid vendor (Wood Mackenzie, LSEG, Criterion); the monthly LNG export figure is the free proxy.</div></div>`;
}

function ngDataStatus() {
  const ds = NGD.ds;
  if (!ds) return "";
  const col = { LIVE: "live", SIM: "sim", ERROR: "bad", NA: "sim" };
  return `<details class="box ng-ds" ${ds.mode !== "live" || ds.rows.some((r) => r.status === "ERROR") ? "open" : ""}><summary class="sec">DATA SOURCES <span class="muted">mode ${ds.mode.toUpperCase()} ·
      ${ds.rows.filter((r) => r.status === "LIVE").length}/${ds.rows.length} live${ds.rows.some((r) => r.status === "ERROR") ? ' · <span class="down">errors</span>' : ""} · click to expand</span></summary>
    <table class="tbl"><thead><tr><th>DATA</th><th>STATUS</th><th>SOURCE</th><th>DETAIL</th></tr></thead><tbody>
    ${ds.rows.map((r) => `<tr><td class="sym">${esc(r.what)}</td><td><span class="badge ${col[r.status] || "sim"}">${r.status}</span></td><td>${esc(r.source)}</td><td class="muted">${esc(r.detail || "")}</td></tr>`).join("")}
    </tbody></table>
    ${ds.hint ? `<div class="notice">${esc(ds.hint)}</div>` : ""}</details>`;
}

function ngStorage() {
  const sd = NGD.ins?.supply_demand;
  if (!sd) return "";
  return `<div class="box"><div class="sec">STORAGE · EIA WEEKLY <span class="muted">${sd.date}</span></div>
    <div class="kv"><span>Working gas</span><span>${fx(sd.stocks, 0)} Bcf</span>
      <span>vs 5-yr average</span><span class="${cls(-sd.dev_pct)}">${signed(sd.dev_pct, 1)}% (${signed(sd.dev_z, 1)}σ)</span>
      <span>Last change / normal</span><span>${signed(sd.weekly_chg, 0)} / ${signed(sd.normal_chg, 0)} Bcf</span>
      <span>Balance</span><span class="amber">${sd.balance}</span></div>
    <div class="muted" style="margin-top:4px">Report: Thursdays 10:30 ET (moved in holiday weeks). Below-normal stocks into winter = tighter market.</div></div>`;
}

function ngSeasonHeat() {
  const s = NGD.s, years = [...new Set(s.year_month.map((r) => r.year))].sort();
  const get = (y, m) => s.year_month.find((r) => r.year === y && r.month === m);
  const mx = Math.max(...s.year_month.map((r) => Math.abs(r.ret_pct)));
  const cur = new Date().getMonth() + 1;
  return `<div class="box"><div class="sec">MACRO · MONTHLY RETURNS BY YEAR <span class="muted">front-month chained, roll gaps removed · ${s.years}</span></div>
    <div class="scroll"><table class="tbl heat"><thead><tr><th>YEAR</th>${MON.map((m, i) => `<th class="${i + 1 === cur ? "ng-cur" : ""}">${m}</th>`).join("")}</tr></thead><tbody>
    ${years.map((y) => `<tr><td class="sym">${y}</td>${MON.map((_, i) => { const r = get(y, i + 1); return r ? `<td style="${ngHeat(r.ret_pct, mx)}" title="${y} ${MON[i]}: ${r.ret_pct}% · vol ${r.vol_pct}% · range ${r.range_pct}%/day · ${r.contract}">${signed(r.ret_pct, 0)}</td>` : '<td class="muted">·</td>'; }).join("")}</tr>`).join("")}
    <tr class="ng-sum"><td>AVG</td>${s.profile.map((p) => `<td class="${cls(p.avg_ret_pct)}">${signed(p.avg_ret_pct, 1)}</td>`).join("")}</tr>
    <tr class="ng-sum"><td>% UP</td>${s.profile.map((p) => `<td>${fx(p.up_pct, 0)}</td>`).join("")}</tr>
    </tbody></table></div></div>`;
}

function ngProfile() {
  const s = NGD.s, mxr = Math.max(...s.profile.map((p) => p.range_pct));
  const cur = new Date().getMonth() + 1;
  return `<div class="box"><div class="sec">MONTH PROFILE <span class="muted">which contract is front, and how volatile each month is ($ at today's price)</span></div>
    <table class="tbl"><thead><tr><th>MONTH</th><th>FRONT</th><th>AVG RET</th><th>% UP</th><th>VOL (ANN.)</th><th>AVG DAY RANGE</th><th>$/NG</th><th>$/MNG</th><th>TICKS</th></tr></thead><tbody>
    ${s.profile.map((p) => `<tr class="${p.month === cur ? "sel" : ""}"><td class="sym">${p.name}</td><td>${p.front}</td><td class="${cls(p.avg_ret_pct)}">${signed(p.avg_ret_pct, 1)}%</td><td>${fx(p.up_pct, 0)}%</td>
      <td>${fx(p.vol_pct, 0)}%</td><td style="${ngHeat(p.range_pct, mxr, false)}">${fx(p.range_pct, 2)}%</td><td>${money(p.range_usd)}</td><td>${money(p.range_usd / 10)}</td><td>${p.range_ticks}</td></tr>`).join("")}
    </tbody></table></div>`;
}

function ngWeekday() {
  const t = NGD.s.weekday_month, cur = new Date().getMonth() + 1, nwd = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"].indexOf(NGD.o.weekday);
  const mx = Math.max(...t.table.flatMap((r) => r.cells.map((c) => c.range_pct || 0)));
  return `<div class="box"><div class="sec">MICRO · AVERAGE DAILY RANGE BY WEEKDAY × MONTH <span class="muted">% of price · ×factor vs that month's average · next session outlined</span></div>
    <table class="tbl heat"><thead><tr><th>MONTH</th>${WDS.map((d, i) => `<th class="${i === nwd ? "ng-cur" : ""}">${d}</th>`).join("")}</tr></thead><tbody>
    ${t.table.map((r) => `<tr class="${r.month === cur ? "sel" : ""}"><td class="sym">${r.name}</td>${r.cells.map((c, i) => `<td class="${r.month === cur && i === nwd ? "best" : ""}" style="${ngHeat(c.range_pct, mx, false)}" title="${r.name} ${WDS[i]}: ${c.range_pct}% avg range (×${c.factor}), up ${c.up_pct}% of ${c.n} sessions">${fx(c.range_pct, 1)}<span class="muted"> ×${fx(c.factor, 2)}</span></td>`).join("")}</tr>`).join("")}
    <tr class="ng-sum"><td>ALL</td>${t.overall.map((o) => `<td>×${fx(o.factor, 2)}</td>`).join("")}</tr></tbody></table>
    <div class="muted">Thursdays carry the EIA storage report; Mondays reprice the weekend's weather-model runs.</div></div>`;
}

function ngExpiry() {
  const c = NGD.s.expiry_curve, cur = NGD.o.contract.trading_days_left;
  const W = 520, H = 150, pad = 26, bw = (W - pad) / c.length, mx = Math.max(...c.map((x) => x.factor), 1.2);
  const y = (v) => H - 20 - (v / mx) * (H - 34);
  return `<div class="box"><div class="sec">EXPIRY CYCLE <span class="muted">daily range vs that month's average, by sessions left until the front contract expires</span></div>
    <svg viewBox="0 0 ${W} ${H}" class="ng-wx" role="img" aria-label="Range factor by trading days to expiry">
      <line x1="${pad}" x2="${W}" y1="${y(1)}" y2="${y(1)}" stroke-dasharray="3 3" style="stroke:var(--muted)"/><text x="2" y="${y(1) + 3}" font-size="9" style="fill:var(--muted)">1.0</text>
      ${c.slice().reverse().map((x, i) => `<rect x="${pad + i * bw + 2}" y="${y(x.factor)}" width="${Math.max(2, bw - 4)}" height="${H - 20 - y(x.factor)}" rx="2"
        style="fill:${x.tdte === cur ? "var(--warn)" : "var(--s1)"};opacity:${x.tdte === cur ? 1 : 0.75}"><title>${x.tdte} sessions to expiry: ×${x.factor} (n=${x.n})</title></rect>
        <text x="${pad + i * bw + bw / 2}" y="${H - 6}" font-size="9" text-anchor="middle" style="fill:var(--muted)">${x.tdte}</text>`).join("")}
    </svg><div class="muted">Highlighted = next session (${cur} left). 0 = expiry day. Bars above 1.0 mean wider ranges than normal.</div></div>`;
}

function ngAnalogs() {
  const a = NGD.s.analogs, lines = a.lines, avg = a.average;
  const all = [...lines.flatMap((l) => l.path), ...avg];
  const lo = Math.min(...all, -1), hi = Math.max(...all, 1), n = Math.max(...lines.map((l) => l.path.length), avg.length);
  const W = 520, H = 220, pad = 30;
  const x = (i) => pad + (i / Math.max(1, n - 1)) * (W - pad - 6), y = (v) => 8 + (1 - (v - lo) / (hi - lo)) * (H - 28);
  const poly = (p) => p.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const cur = lines.find((l) => l.current);
  return `<div class="box"><div class="sec">${a.name.toUpperCase()} IN PAST YEARS <span class="muted">cumulative % move through the month, from its first session</span></div>
    <svg viewBox="0 0 ${W} ${H}" class="ng-an" role="img" aria-label="${a.name} paths by year, average and this year">
      <line x1="${pad}" x2="${W}" y1="${y(0)}" y2="${y(0)}" style="stroke:var(--line)"/>
      <text x="2" y="${y(hi) + 8}" font-size="9" style="fill:var(--muted)">${signed(hi, 0)}%</text><text x="2" y="${y(lo)}" font-size="9" style="fill:var(--muted)">${signed(lo, 0)}%</text>
      ${lines.filter((l) => !l.current).map((l) => `<polyline fill="none" stroke-width="1" style="stroke:var(--muted);opacity:.55" points="${poly(l.path)}"><title>${l.year}: ${signed(l.path[l.path.length - 1], 1)}%</title></polyline>
        <text x="${x(l.path.length - 1) + 2}" y="${y(l.path[l.path.length - 1]) + 3}" font-size="8" style="fill:var(--muted)">${String(l.year).slice(2)}</text>`).join("")}
      <polyline fill="none" stroke-width="2.5" style="stroke:var(--s1)" points="${poly(avg)}"><title>Average</title></polyline>
      ${cur ? `<polyline fill="none" stroke-width="2.5" style="stroke:var(--s2)" points="${poly(cur.path)}"><title>${cur.year} so far: ${signed(cur.path[cur.path.length - 1], 1)}%</title></polyline>` : ""}
    </svg>
    <div class="legend"><span><i style="background:var(--s1)"></i>Average of past years</span>${cur ? `<span><i style="background:var(--s2)"></i>${cur.year} so far</span>` : ""}<span><i style="background:var(--muted)"></i>Each past year (labelled)</span></div></div>`;
}

function ngWeatherLink() {
  const l = NGD.s.weather_link;
  return `<div class="box"><div class="sec">WEATHER vs PRICE · ${l.month.toUpperCase()} <span class="muted">observed HDD anomaly for the month vs the front month's move · ${esc(l.source)}</span></div>
    <table class="tbl"><thead><tr><th>YEAR</th><th>HDD vs NORMAL</th><th>MONTH MOVE</th><th>VOL</th><th>READ</th></tr></thead><tbody>
    ${l.rows.map((r) => `<tr><td class="sym">${r.year}</td><td class="${r.hdd_anom > 0 ? "up" : r.hdd_anom < 0 ? "down" : ""}">${r.hdd_anom == null ? "—" : signed(r.hdd_anom, 0) + (r.hdd_anom > 0 ? " colder" : " warmer")}</td>
      <td class="${cls(r.ret_pct)}">${signed(r.ret_pct, 1)}%</td><td>${fx(r.vol_pct, 0)}%</td>
      <td class="muted">${r.hdd_anom == null ? "" : (r.hdd_anom > 0) === (r.ret_pct > 0) ? "weather-led" : "other drivers won"}</td></tr>`).join("")}</tbody></table>
    <div class="muted" style="margin-top:4px">Correlation HDD anomaly ↔ move: <b>${l.corr ?? "—"}</b>. Past years use observed weather (what happened), not the forecast traders saw at the time.</div></div>`;
}

function ngDynamics() {
  const d = NGD.dyn;
  if (!d) return "";
  const sv = d.sensitivity, ws = d.winter_summary, os_ = d.other_summary, r = d.revision, im = d.implied_move;
  const b = (x) => (x && x.pct_per_10hdd != null ? `${signed(x.pct_per_10hdd, 2)}% <span class="muted">(t ${fx(x.t, 1)}, n ${x.n})</span>` : "—");
  const mxu = Math.max(...d.tails.map((t) => Math.max(t.up95_pct, Math.abs(t.down05_pct))));
  return `<div class="box ng-policy ${d.policy.active ? "on" : ""}"><div class="sec">${d.policy.label} <span class="muted">"rather long and wrong than short and dead"</span></div>
      <ul class="dt-list">${d.policy.rules.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>
    <div class="dt-row dt-r2b">
      <div class="box"><div class="sec">WEATHER → PRICE SENSITIVITY <span class="muted">${esc(sv.method)}</span></div>
        <div class="kv"><span>Winter (Nov–Mar), per +10 HDD</span><span>${b(sv.winter)}</span>
          <span>Rest of the year</span><span>${b(sv.other)}</span>
          <span>Winter, colder revisions</span><span>${b(sv.winter_colder)}</span>
          <span>Winter, warmer revisions</span><span>${b(sv.winter_warmer)}</span></div>
        ${sv.winter_colder?.pct_per_10hdd != null && sv.winter_warmer?.pct_per_10hdd != null ? `<div class="dt-tip">Asymmetry: colder revisions move price ${fx(sv.winter_colder.pct_per_10hdd / (sv.winter_warmer.pct_per_10hdd || 1), 1)}× as much as warmer ones: the "call skew" in action.</div>` : ""}
        <div class="sec">TODAY'S FORECAST REVISION</div>
        ${r.available ? `<div class="kv"><span>HDD change vs previous run</span><span class="${cls(r.hdd_change)}">${signed(r.hdd_change, 1)} HDD (${r.days_compared} days compared · ${esc(r.source)})</span>
          <span>Implied move (winter sensitivity)</span><span class="${cls(im?.pct)}">${im ? `${signed(im.pct, 2)}% ≈ ${money(im.usd_ng)}/NG · ${money(im.usd_mng)}/MNG` : "—"}</span></div>` : `<div class="muted">${esc(r.note)}</div>`}
        <div class="muted" style="margin-top:4px">The desk saves a forecast snapshot every day; after ~60 days it measures sensitivity from real forecast revisions instead of the proxy.</div></div>
      <div class="box"><div class="sec">UPSIDE vs DOWNSIDE TAILS BY MONTH <span class="muted">95th-pct up day vs 5th-pct down day</span></div>
        <table class="tbl"><thead><tr><th>MONTH</th><th>BIG UP DAY</th><th>BIG DOWN DAY</th><th>RATIO</th><th>SKEW</th><th>SHORT 5D RISK</th><th>LONG 5D RISK</th></tr></thead><tbody>
        ${d.tails.map((t) => `<tr class="${[11, 12, 1, 2, 3].includes(t.month) ? "ng-win" : ""}"><td class="sym">${MON[t.month - 1]}</td><td class="up" style="${ngHeat(t.up95_pct, mxu)}">+${fx(t.up95_pct, 1)}%</td>
          <td class="down" style="${ngHeat(t.down05_pct, mxu)}">${fx(t.down05_pct, 1)}%</td><td>${fx(t.tail_ratio, 2)}</td><td>${signed(t.skew, 2)}</td>
          <td class="${t.short_5d_p90_pct > t.long_5d_p90_pct ? "down" : ""}">+${fx(t.short_5d_p90_pct, 1)}%</td><td>${fx(t.long_5d_p90_pct, 1)}%</td></tr>`).join("")}</tbody></table>
        <div class="muted">"5D risk" = 90th-percentile adverse move against a position held 5 sessions. Winter avg: short ${fx(ws.short_5d_p90_pct, 1)}% vs long ${fx(ws.long_5d_p90_pct, 1)}% (rest of year ${fx(os_.short_5d_p90_pct, 1)}% vs ${fx(os_.long_5d_p90_pct, 1)}%).</div></div>
    </div>`;
}

function ngOptions() {
  const o = NGD.opt;
  if (!o) return "";
  const f = o.front || {};
  return `<div class="box"><div class="sec">NG OPTIONS POSITIONING <span class="muted">${esc(o.source)} · dealers ${o.mode.replace("_", " ")}</span></div>
    <div class="kv"><span>Dealer gamma / 1%</span><span class="${o.total_gex >= 0 ? "up" : "down"}">${o.regime} (${o.total_gex >= 0 ? "damped" : "accelerating"})</span>
      <span>Call wall / put wall</span><span>${px("NG", o.call_wall)} / ${px("NG", o.put_wall)}</span>
      <span>Front expiry ${f.expiry}</span><span>±${px("NG", f.expected_move)} straddle · max pain ${px("NG", f.max_pain)}</span>
      <span>25Δ risk reversal</span><span class="${f.rr25 > 0.5 ? "up" : ""}">${f.rr25 == null ? "—" : signed(f.rr25, 1) + " vol"} ${f.rr25 > 0.5 ? "(call skew)" : ""}</span></div>
    <div class="row-actions"><button class="tbtn" data-ngjump="OPT">FULL OPTIONS &amp; GAMMA ›</button></div></div>`;
}

// ------------------------------------------------------------------ view
async function ngLoad() {
  [NGD.o, NGD.s, NGD.w, NGD.k, NGD.ins, NGD.dyn, NGD.opt, NGD.st] = await Promise.all([api("/api/natgas/outlook"), api("/api/natgas/seasonality"),
    api("/api/natgas/weather"), api("/api/natgas/contracts"), api("/api/snapshot/NG"), api("/api/natgas/dynamics"), api("/api/options/NG"),
    api("/api/natgas/storage").catch(() => null)]);
  [NGD.ds, NGD.bal] = await Promise.all([api("/api/datastatus/NG").catch(() => null), api("/api/natgas/balance").catch(() => null)]);
}
function ngRender() {
  const v = $("#view");
  if (!v || S.view !== "NGD" || !NGD.o) return;
  const keep = v.scrollTop, q = S.rows.NG || {};
  v.innerHTML = `<div class="dt">
    <div class="dt-head"><div class="dt-title"><div class="dt-sym">NG<span>Henry Hub Natural Gas · front ${NGD.k.info.front}${NGD.k.info.roll_window ? ` → trade ${NGD.k.info.next}` : ""}</span></div>
      <div class="headline"><span class="big">${px("NG", q.last)}</span><span class="${cls(q.chg_pct)}">${signed(q.chg_pct, 2)}%</span></div></div>
      <div class="dt-badges">${Object.entries(NGD.ins.sources || {}).map(([k, x]) => srcBadge(k.slice(0, 3).toUpperCase(), x)).join(" ")} ${srcBadge("WX", NGD.w.source.startsWith("LIVE") ? "LIVE" : "SIM")}
        <span class="bias ${biasCls(NGD.ins.swing.bias)}">SWING ${NGD.ins.swing.bias}</span><span class="bias ${biasCls(NGD.ins.day.bias)}">DAY ${NGD.ins.day.bias}</span>
        <button class="tbtn" data-ngjump="DT">INSIDER ›</button><button class="tbtn" data-ngjump="OPT">OPTIONS ›</button><button class="tbtn" data-ngjump="TS">SIGNALS ›</button><button class="tbtn" data-ngjump="IR">RESEARCH ›</button></div></div>
    ${ngDataStatus()}
    <div class="dt-row ng-r1">${ngOutlook()}<div class="ng-col">${ngContracts()}${ngOptions()}${ngStorage()}</div></div>
    ${ngBalance()}
    <div class="dt-row dt-r2b">${ngCurve()}${ngStorageReport()}</div>
    ${ngDynamics()}
    <div class="dt-row dt-r2b">${ngWeather()}${ngExpiry()}</div>
    ${ngSeasonHeat()}
    <div class="dt-row dt-r2b">${ngAnalogs()}${ngWeatherLink()}</div>
    <div class="dt-row dt-r2b">${ngWeekday()}${ngProfile()}</div>
    <div class="box muted">Data: ${NGD.s.sessions} sessions of front-month history (${NGD.s.years}, source ${NGD.s.source}).
      ${NGD.s.source === "LIVE" ? "Continuous NG=F from Yahoo (delayed), back-adjusted at each contract roll so the roll gap doesn't count as a move. Weather: Open-Meteo. Storage: EIA." : "Simulated. Set DATA_MODE=live for Yahoo prices, EIA storage and Open-Meteo weather."}
      Contract-by-contract history (e.g. December contracts only) needs a vendor feed. Seasonal tendencies describe the past; size by today's volatility.</div></div>`;
  v.scrollTop = keep;
}

VIEWS.NGD = async function (v) {
  setTitle("NAT GAS DESK · CONTRACTS · NEXT SESSION · WEATHER · STORAGE · SEASONALITY");
  tools(`<button class="tbtn" data-ngrefresh="1">↻ REFRESH</button>`);
  v.innerHTML = '<div class="pad muted">Loading the natural gas desk…</div>';
  if (S.sel !== "NG") { S.sel = "NG"; S.snap = await api("/api/snapshot/NG"); }
  await ngLoad();
  ngRender();
};

document.addEventListener("click", async (ev) => {
  const t = ev.target.closest("[data-ngjump],[data-ngrefresh]");
  if (!t || S.view !== "NGD") return;
  if (t.dataset.ngrefresh) { await ngLoad(); return ngRender(); }
  if (t.dataset.ngjump === "TS" && typeof TS !== "undefined") TS.filter.symbol = "NG";
  if (t.dataset.ngjump === "OPT" && typeof OPT !== "undefined") { OPT.sym = "NG"; OPT.mode = null; }
  S.view = t.dataset.ngjump;
  renderView();
});
