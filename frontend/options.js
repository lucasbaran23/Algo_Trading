/* Options & Gamma: open interest, volume, expiries, skew, max pain and dealer gamma (GEX). */
"use strict";

const OPT = { sym: "NG", mode: null, d: null };
const OPT_SYMS = ["NG", "ES", "NQ", "CL", "GC"];
const fmtB = (v) => { const a = Math.abs(v); return (v < 0 ? "-" : "+") + (a >= 1e9 ? `$${(a / 1e9).toFixed(2)}B` : a >= 1e6 ? `$${(a / 1e6).toFixed(1)}M` : `$${(a / 1e3).toFixed(0)}K`); };

function optGexChart(d) {
  const p = d.profile, W = 560, H = 220, padL = 52, padB = 22;
  const xs = p.map((r) => r.price), ys = p.map((r) => r.gex);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), ymax = Math.max(...ys.map(Math.abs), 1);
  const x = (v) => padL + ((v - x0) / (x1 - x0)) * (W - padL - 8), y = (v) => (H - padB) / 2 + 4 - (v / ymax) * ((H - padB) / 2 - 8);
  const pts = p.map((r) => `${x(r.price).toFixed(1)},${y(r.gex).toFixed(1)}`).join(" ");
  const area = `${x(x0)},${y(0)} ${pts} ${x(x1)},${y(0)}`;
  let row = 0;                                        // stagger labels so nearby levels don't overlap
  const vline = (v, label, color, dash) => {
    if (v == null || v < x0 || v > x1) return "";
    const ly = 14 + 12 * (row++ % 4), right = x(v) > W - 90;
    return `<line x1="${x(v)}" x2="${x(v)}" y1="6" y2="${H - padB}" style="stroke:${color}" stroke-width="1.5" ${dash ? 'stroke-dasharray="4 3"' : ""}/>
    <text x="${x(v) + (right ? -3 : 3)}" y="${ly}" font-size="10" text-anchor="${right ? "end" : "start"}" style="fill:${color}">${label}</text>`;
  };
  return `<svg viewBox="0 0 ${W} ${H}" class="opt-svg" role="img" aria-label="Dealer gamma exposure across prices; spot ${d.price}, flip ${d.flip}">
    <line x1="${padL}" x2="${W - 8}" y1="${y(0)}" y2="${y(0)}" style="stroke:var(--line)"/>
    <polygon points="${area}" style="fill:var(--s1);opacity:.18"/><polyline fill="none" stroke-width="2" style="stroke:var(--s1)" points="${pts}"/>
    ${vline(d.price, "NOW " + px(d.symbol, d.price), "var(--warn)")}${vline(d.flip, "FLIP", "var(--ink)", true)}
    ${vline(d.call_wall, "CALL WALL", "var(--s2)", true)}${vline(d.put_wall, "PUT WALL", "var(--s3)", true)}
    <text x="4" y="${y(ymax) + 4}" font-size="9" style="fill:var(--muted)">${fmtB(ymax)}</text><text x="4" y="${y(-ymax)}" font-size="9" style="fill:var(--muted)">${fmtB(-ymax)}</text>
    <text x="${padL}" y="${H - 6}" font-size="9" style="fill:var(--muted)">${px(d.symbol, x0)}</text><text x="${W - 8}" y="${H - 6}" font-size="9" text-anchor="end" style="fill:var(--muted)">${px(d.symbol, x1)}</text>
  </svg>`;
}

function optOiChart(d) {
  const s = d.strikes.filter((r) => r.call_oi || r.put_oi);
  if (!s.length) return "";
  const W = 560, H = 230, padL = 52, mid = H / 2, bw = (W - padL - 8) / s.length;
  const mx = Math.max(...s.map((r) => Math.max(r.call_oi, r.put_oi)), 1);
  const every = Math.ceil(s.length / 12);
  return `<svg viewBox="0 0 ${W} ${H}" class="opt-svg" role="img" aria-label="Open interest by strike: calls above, puts below">
    ${s.map((r, i) => `<rect x="${padL + i * bw + 1}" y="${mid - (r.call_oi / mx) * (mid - 18)}" width="${Math.max(1, bw - 2)}" height="${(r.call_oi / mx) * (mid - 18)}" rx="1" style="fill:var(--s2)"><title>${px(d.symbol, r.strike)} calls: ${r.call_oi.toLocaleString()} OI</title></rect>
      <rect x="${padL + i * bw + 1}" y="${mid + 1}" width="${Math.max(1, bw - 2)}" height="${(r.put_oi / mx) * (mid - 18)}" rx="1" style="fill:var(--s3)"><title>${px(d.symbol, r.strike)} puts: ${r.put_oi.toLocaleString()} OI</title></rect>
      ${i % every === 0 ? `<text x="${padL + i * bw + bw / 2}" y="${H - 3}" font-size="9" text-anchor="middle" style="fill:var(--muted)">${px(d.symbol, r.strike)}</text>` : ""}`).join("")}
    <line x1="${padL}" x2="${W - 8}" y1="${mid}" y2="${mid}" style="stroke:var(--line)"/>
    <text x="4" y="16" font-size="9" style="fill:var(--muted)">CALLS</text><text x="4" y="${H - 16}" font-size="9" style="fill:var(--muted)">PUTS</text>
  </svg>`;
}

function optRender() {
  const v = $("#view"), d = OPT.d;
  if (!v || S.view !== "OPT" || !d) return;
  const f = d.front || {};
  v.innerHTML = `<div class="dt">
    <div class="dt-head"><div class="dt-title"><div class="dt-sym">${d.symbol}<span>options positioning &amp; dealer gamma</span></div>
      <div class="headline"><span class="big">${px(d.symbol, d.price)}</span><span class="badge ${d.source === "SIM" ? "sim" : "live"}">${esc(d.source)}</span></div></div>
      <div class="dt-badges">
        <label class="muted">Dealer assumption <select data-optmode><option value="standard" ${d.mode === "standard" ? "selected" : ""}>Standard: dealers long calls / short puts</option><option value="short_calls" ${d.mode === "short_calls" ? "selected" : ""}>Dealers short calls &amp; puts (call buyers)</option></select></label></div>
      <div class="dt-switch">${OPT_SYMS.map((s) => `<button class="tbtn ${s === d.symbol ? "on" : ""}" data-optsym="${s}">${s}</button>`).join("")}</div></div>
    <div class="tiles">
      ${tile("Dealer gamma / 1%", fmtB(d.total_gex), d.total_gex >= 0 ? "up" : "down")}${tile("Regime", d.regime === "POSITIVE" ? "LONG γ · damped" : "SHORT γ · accelerating", d.regime === "POSITIVE" ? "up" : "down")}
      ${tile("Gamma flip", d.flip == null ? "none in range" : px(d.symbol, d.flip))}${tile("Call wall", px(d.symbol, d.call_wall))}${tile("Put wall", px(d.symbol, d.put_wall))}
      ${tile("Max pain (front)", px(d.symbol, f.max_pain))}${tile("Put/call OI", fx(d.pcr_oi, 2))}${tile("Put/call volume", fx(d.pcr_vol, 2))}
      ${tile("Front ATM IV", fx(f.atm_iv, 1) + "%")}${tile("25Δ risk reversal", f.rr25 == null ? "—" : signed(f.rr25, 1) + " vol", f.rr25 > 0.5 ? "up" : f.rr25 < -0.5 ? "down" : "")}
      ${tile("Straddle move", `±${px(d.symbol, f.expected_move)} (${fx(f.expected_move_pct, 1)}%)`)}${tile("Hedge flow / +1%", `${d.hedge_contracts_per_1pct > 0 ? "buy" : "sell"} ${Math.abs(d.hedge_contracts_per_1pct).toLocaleString()}`)}
    </div>
    <div class="dt-row dt-r2b">
      <div class="box"><div class="sec">GAMMA EXPOSURE ACROSS PRICES <span class="muted">$ per 1% move · dealer side</span></div>${optGexChart(d)}
        <div class="muted">Above zero: dealers long gamma (they sell rallies and buy dips). Below zero: short gamma (they chase moves).</div></div>
      <div class="box"><div class="sec">OPEN INTEREST BY STRIKE <span class="muted">all listed expiries</span></div>${optOiChart(d)}
        <div class="legend"><span><i style="background:var(--s2)"></i>Calls</span><span><i style="background:var(--s3)"></i>Puts</span></div></div>
    </div>
    <div class="dt-row dt-r2b">
      <div class="box"><div class="sec">WHAT IT MEANS TODAY</div><ul class="dt-bullets">${d.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul></div>
      <div class="box"><div class="sec">KEY STRIKES</div>
        <div class="cols" style="padding:0">
          <table class="tbl"><thead><tr><th>CALL STRIKE</th><th>OI</th><th>VOL</th></tr></thead><tbody>${d.top_calls.map((r) => `<tr><td class="sym">${px(d.symbol, r.strike)}</td><td>${r.oi.toLocaleString()}</td><td>${r.volume.toLocaleString()}</td></tr>`).join("")}</tbody></table>
          <table class="tbl"><thead><tr><th>PUT STRIKE</th><th>OI</th><th>VOL</th></tr></thead><tbody>${d.top_puts.map((r) => `<tr><td class="sym">${px(d.symbol, r.strike)}</td><td>${r.oi.toLocaleString()}</td><td>${r.volume.toLocaleString()}</td></tr>`).join("")}</tbody></table>
        </div>
        <div class="sec">MOST ACTIVE TODAY</div><div class="dt-tdrow">${d.vol_leaders.map((r) => `<span class="dt-tdchip"><b>${px(d.symbol, r.strike)}${r.type}</b> ${r.volume.toLocaleString()}</span>`).join("")}</div></div>
    </div>
    <div class="box"><div class="sec">EXPIRIES <span class="muted">where the open interest sits, and what each expiry prices in</span></div>
      <table class="tbl"><thead><tr><th>EXPIRY</th><th>DAYS</th><th>OI SHARE</th><th>CALL OI</th><th>PUT OI</th><th>P/C OI</th><th>P/C VOL</th><th>ATM IV</th><th>25Δ RR</th><th>STRADDLE MOVE</th><th>MAX PAIN</th><th>GEX / 1%</th></tr></thead><tbody>
      ${d.expiries.map((e) => `<tr><td class="sym">${e.expiry}</td><td>${e.days}</td><td>${fx(e.oi_share, 0)}%</td><td>${e.oi_calls.toLocaleString()}</td><td>${e.oi_puts.toLocaleString()}</td>
        <td>${fx(e.pcr_oi, 2)}</td><td>${fx(e.pcr_vol, 2)}</td><td>${fx(e.atm_iv, 1)}%</td><td class="${e.rr25 > 0.5 ? "up" : e.rr25 < -0.5 ? "down" : ""}">${e.rr25 == null ? "—" : signed(e.rr25, 1)}</td>
        <td>±${px(d.symbol, e.expected_move)} (${fx(e.expected_move_pct, 1)}%)</td><td>${px(d.symbol, e.max_pain)}</td><td class="${cls(e.gex)}">${fmtB(e.gex)}</td></tr>`).join("")}
      </tbody></table></div>
    <div class="cols" style="padding:0">
      <div class="box"><div class="sec">HOW TO USE IT</div><ul class="dt-list">
        <li><b>Delta hedging:</b> option dealers hedge with futures. When they are short gamma, a rally forces them to buy (and a drop to sell), which feeds the move.</li>
        <li><b>Gamma scalping:</b> when dealers are long gamma they buy dips and sell rips around big strikes. Day traders can fade moves toward the walls and target the pin and max pain into expiry.</li>
        <li><b>Flip level:</b> above or below it the market's character changes. Treat a decisive cross of the flip like a regime change.</li>
        <li><b>Expiry:</b> gamma is largest in the last days before expiry. Pinning is strongest then, and the release after expiry often brings a bigger move.</li>
        <li><b>Skew:</b> a positive 25Δ risk reversal (calls richer) is the natural gas winter pattern: the market pays for upside. Index futures usually show put skew.</li></ul></div>
      <div class="box"><div class="sec">DATA</div><ul class="dt-list">
        <li>${d.source === "SIM" ? '<span class="down">SIMULATED chain</span>: for demonstrating the analytics only.' : esc(d.source)}</li>
        <li>Real data: save a chain as <code>backend/data/options/${d.symbol}_chain.csv</code> (expiry, strike, type, open_interest, volume, iv, underlying). Export it from CME QuikStrike, Databento (CME options), your broker, or a licensed Barchart API feed. Scraping Barchart's site breaks its terms.</li>
        <li>ES/NQ in live mode: CBOE's free delayed SPX/NDX chains, scaled to the futures.</li>
        <li>Dealer positioning is an assumption, not observed data. Compare both settings.</li></ul></div>
    </div></div>`;
}

async function optLoad() {
  OPT.d = await api(`/api/options/${OPT.sym}${OPT.mode ? `?mode=${OPT.mode}` : ""}`);
}
VIEWS.OPT = async function (v) {
  setTitle("OPTIONS & GAMMA · OPEN INTEREST · SKEW · DEALER POSITIONING");
  tools("");
  const base = S.universe[S.sel]?.parent || S.sel;
  if (OPT_SYMS.includes(base)) OPT.sym = base;
  v.innerHTML = '<div class="pad muted">Loading the option chain…</div>';
  await optLoad();
  optRender();
};
document.addEventListener("click", async (ev) => {
  const t = ev.target.closest("[data-optsym]");
  if (!t || S.view !== "OPT") return;
  OPT.sym = t.dataset.optsym; OPT.mode = null;
  await optLoad(); optRender();
});
document.addEventListener("change", async (ev) => {
  if (ev.target.dataset.optmode === undefined || S.view !== "OPT") return;
  OPT.mode = ev.target.value;
  await optLoad(); optRender();
});
