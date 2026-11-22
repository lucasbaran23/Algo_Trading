/* News (F10): real-time feed tied to each futures market, with LONG/SHORT signal and 1-3 star importance. */
"use strict";

const NEWS = { items: [], scores: {}, status: null, filter: { symbol: "", stars: 1, direction: "" }, tab: "alerts" };

const starsHtml = (n) => `<span class="stars" title="Importance ${n} of 3" aria-label="${n} of 3 stars">${"★".repeat(n)}<span class="off">${"★".repeat(3 - n)}</span></span>`;
const dirHtml = (d) => `<span class="dir ${d}">${d}</span>`;
function ago(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "now";
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}
function newsRow(it, { focus = null, isNew = false, showWhy = false } = {}) {
  const sigs = focus ? it.signals.filter((x) => x.symbol === focus) : it.signals;
  const top = Math.max(...sigs.map((x) => x.stars));
  const title = it.url ? `<a href="${esc(it.url)}" target="_blank" rel="noopener noreferrer">${esc(it.title)}</a>` : esc(it.title);
  return `<div class="news s${top} ${isNew ? "new" : ""}">
    <span class="t" title="${new Date(it.ts * 1000).toLocaleString()}">${ago(it.ts)}</span>
    <span class="hd">${title}</span>
    <span class="meta">
      ${sigs.map((x) => `<span class="nsig" data-newssym="${x.symbol}" title="${esc((x.reasons || []).join(" · "))}">${dirHtml(x.direction)}<b>${x.symbol}</b>${starsHtml(x.stars)}</span>`).join("")}
      <span>${esc(it.source)}${it.sim ? ' <span class="badge sim">SIM</span>' : ""}${it.classifier === "claude" ? ' <span class="badge">AI</span>' : ""}</span>
      ${showWhy ? `<span class="why">${esc(sigs.flatMap((x) => (x.reasons || []).slice(0, 3)).slice(0, 4).join(" · "))}</span>` : ""}
    </span></div>`;
}
function newsFor(sym) {
  const d = S.universe[sym]?.parent || sym;
  return NEWS.items.filter((it) => it.signals.some((x) => x.symbol === d));
}

// ------------------------------------------------------------------ detail panel section
function newsDetail(sym) {
  const d = S.universe[sym]?.parent || sym;
  const list = newsFor(sym).slice(0, 4);
  const sc = NEWS.scores[d];
  return `<div class="sec">NEWS <span class="muted">${sc && sc.count ? `${sc.long}▲ ${sc.short}▼ · sentiment ${signed(sc.score, 0)}` : "no recent items"}</span></div>
    ${list.length ? list.map((it) => newsRow(it, { focus: d })).join("") : '<div class="muted">No market-moving headlines in the last 3 days.</div>'}
    ${list.length ? `<div class="row-actions"><button class="tbtn" data-newsview="${d}">ALL ${d} NEWS ›</button></div>` : ""}`;
}

// ------------------------------------------------------------------ bottom feed
function renderNewsFeed() {
  const el = $("#newsFeed");
  if (!el) return;
  el.innerHTML = NEWS.items.slice(0, 80).map((it) => newsRow(it)).join("") || '<div class="pad muted">Waiting for headlines…</div>';
  $("#newsCount").textContent = NEWS.items.length ? `${NEWS.items.length}` : "";
}
function setBottomTab(tab) {
  NEWS.tab = tab;
  document.querySelectorAll("[data-ptab]").forEach((b) => b.classList.toggle("on", b.dataset.ptab === tab));
  $("#alertFeed").hidden = tab !== "alerts";
  $("#newsFeed").hidden = tab !== "news";
  $("#ptabInfo").textContent = tab === "news" && NEWS.status ? `${NEWS.status.mode.toUpperCase()} · classifier: ${NEWS.status.classifier}` : "";
}

// ------------------------------------------------------------------ live updates
function onNews(items) {
  NEWS.items = [...items, ...NEWS.items].slice(0, 400);
  const feed = $("#newsFeed");
  if (feed) {
    feed.insertAdjacentHTML("afterbegin", items.map((it) => newsRow(it, { isNew: true })).join(""));
    while (feed.children.length > 80) feed.lastChild.remove();
    $("#newsCount").textContent = `${NEWS.items.length}`;
  }
  api("/api/news/scores").then((s) => { NEWS.scores = s; if (S.view === "NEWS") renderNewsBoard(); }).catch(() => {});
  if (S.view === "NEWS") renderNewsList(items);
  const d = S.universe[S.sel]?.parent || S.sel;
  if (items.some((it) => it.signals.some((x) => x.symbol === d)) && S.snap) renderDetail();
}

// ------------------------------------------------------------------ F10 screen
function filtered() {
  const f = NEWS.filter;
  return NEWS.items.map((it) => ({
    ...it,
    signals: it.signals.filter((x) => (!f.symbol || x.symbol === f.symbol) && x.stars >= f.stars && (!f.direction || x.direction === f.direction)),
  })).filter((it) => it.signals.length);
}
function renderNewsList(fresh = []) {
  const el = $("#newsList");
  if (!el) return;
  const ids = new Set(fresh.map((x) => x.id));
  const list = filtered();
  el.innerHTML = list.slice(0, 200).map((it) => newsRow(it, { isNew: ids.has(it.id), showWhy: true })).join("")
    || '<div class="pad muted">No headlines match these filters.</div>';
  const n = $("#newsN"); if (n) n.textContent = `${list.length} items`;
}
function renderNewsBoard() {
  const el = $("#newsBoard");
  if (!el) return;
  const rows = Object.entries(NEWS.scores).filter(([, v]) => v.count).sort((a, b) => Math.abs(b[1].score) - Math.abs(a[1].score));
  el.innerHTML = `<table class="tbl"><thead><tr><th>MKT</th><th>SENTIMENT</th><th>▲</th><th>▼</th><th>TOP STORY</th></tr></thead><tbody>
    ${rows.map(([s, v]) => `<tr class="row" data-newsfilter="${s}"><td class="sym">${s}</td><td>${chip(v.score)}</td><td class="up">${v.long}</td><td class="down">${v.short}</td>
      <td class="topstory" title="${v.top ? esc(v.top.title) : ""}">${v.top ? `${starsHtml(v.top.stars)} ${dirHtml(v.top.direction)} <span class="muted">${esc(v.top.title)}</span>` : ""}</td></tr>`).join("")
      || '<tr><td colspan="5" class="muted">No scored news yet.</td></tr>'}
    </tbody></table>
    <div class="pad muted">Sentiment = Σ direction × importance (★ 0.25, ★★ 0.6, ★★★ 1.0), halving every 12 h, scaled to −100…+100. It feeds the intraday composite at 15% weight.</div>`;
}

VIEWS.NEWS = async function (v) {
  setTitle("NEWS · MARKET-LINKED HEADLINES · SIGNAL & IMPORTANCE");
  tools("");
  const [items, scores, status] = await Promise.all([api("/api/news?limit=300"), api("/api/news/scores"), api("/api/news/status")]);
  NEWS.items = items; NEWS.scores = scores; NEWS.status = status;
  const f = NEWS.filter;
  const syms = Object.keys(S.universe).filter((k) => !S.universe[k].parent);
  v.innerHTML = `<div class="filters">
      <label>Market <select data-nf="symbol"><option value="">All markets</option>${syms.map((k) => `<option ${k === f.symbol ? "selected" : ""}>${k}</option>`).join("")}</select></label>
      <label>Importance <select data-nf="stars"><option value="1" ${f.stars == 1 ? "selected" : ""}>★ and up</option><option value="2" ${f.stars == 2 ? "selected" : ""}>★★ and up</option><option value="3" ${f.stars == 3 ? "selected" : ""}>★★★ only</option></select></label>
      <label>Signal <select data-nf="direction"><option value="">Long &amp; short</option><option ${f.direction === "LONG" ? "selected" : ""}>LONG</option><option ${f.direction === "SHORT" ? "selected" : ""}>SHORT</option><option ${f.direction === "NEUTRAL" ? "selected" : ""}>NEUTRAL</option></select></label>
      <span class="muted" id="newsN"></span>
      <span class="muted" style="margin-left:auto">Source: ${status.mode === "live" ? "live RSS" : "simulated feed"} · classifier: ${esc(status.classifier)} · polling every ${status.poll_s}s</span>
    </div>
    <div class="newsgrid">
      <div class="box"><div class="sec">HEADLINES <span class="muted">hover a market tag for the reasoning</span></div><div id="newsList" class="scroll"></div></div>
      <div class="box"><div class="sec">MARKET NEWS SENTIMENT <span class="muted">click to filter</span></div><div id="newsBoard" class="scroll"></div></div>
    </div>`;
  renderNewsList();
  renderNewsBoard();
};

// ------------------------------------------------------------------ events
document.addEventListener("change", (ev) => {
  const k = ev.target.dataset.nf;
  if (!k || S.view !== "NEWS") return;
  NEWS.filter[k] = k === "stars" ? parseInt(ev.target.value, 10) : ev.target.value;
  renderNewsList();
});
document.addEventListener("click", (ev) => {
  const t = ev.target.closest("[data-ptab],[data-newssym],[data-newsview],[data-newsfilter]");
  if (!t) return;
  if (t.dataset.ptab) return setBottomTab(t.dataset.ptab);
  if (t.dataset.newsfilter) { NEWS.filter.symbol = t.dataset.newsfilter; return renderView(); }
  if (t.dataset.newsview) { NEWS.filter.symbol = t.dataset.newsview; S.view = "NEWS"; return renderView(); }
  if (t.dataset.newssym && S.universe[t.dataset.newssym]) { ev.preventDefault(); select(t.dataset.newssym); }
});

(async function bootNews() {
  try {
    [NEWS.items, NEWS.scores, NEWS.status] = await Promise.all([api("/api/news?limit=300"), api("/api/news/scores"), api("/api/news/status")]);
    renderNewsFeed();
    if (S.snap) renderDetail();
  } catch (e) { console.warn("news boot failed", e); }
})();
