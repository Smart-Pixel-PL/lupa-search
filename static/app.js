"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const ICON = { image: "🖼️", raw: "📷", video: "🎬", audio: "🎵", pdf: "📕", doc: "📄", sheet: "📊", slides: "📽️", design: "🎨",
  text: "📝", web: "🌐", code: "💻", font: "🔤", archive: "🗜️", model3d: "🧊", package: "📦", other: "📁" };
const THUMBABLE = new Set(["image", "raw", "video", "pdf", "doc", "sheet", "slides", "design", "text", "web", "code", "font", "model3d"]);
const KIND_ORDER = ["image", "video", "pdf", "doc", "sheet", "slides", "design", "raw", "audio", "text", "code", "web", "font", "archive", "model3d", "package", "other"];

const store = {
  get(k, d) { try { const v = localStorage.getItem("lupa." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("lupa." + k, JSON.stringify(v)); } catch {} },
};

const S = {
  q: "", kinds: new Set(), since: "", roots: new Set(), folder: "", tags: new Set(), collection: null, similar: null,
  image: null, sort: "relevance", order: "desc",
  view: store.get("view", "grid"), fit: store.get("fit", false), zoom: store.get("zoom", 190),
  items: [], total: 0, loading: false, sel: new Set(), cur: -1, tagsList: [], cols: [], status: null, reqId: 0,
};

async function api(path, opts = {}) {
  const o = { ...opts, headers: { "x-lupa": "1", ...(opts.headers || {}) } };
  if (o.json) { o.body = JSON.stringify(o.json); o.headers["content-type"] = "application/json"; delete o.json; }
  const r = await fetch(path, o);
  if (!r.ok) throw new Error((await r.text()) || r.status);
  return r.json();
}

function toast(msg, ms = 1800) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), ms);
}

const fmtSize = b => b == null ? "" : b < 1024 ? b + " B" : b < 1048576 ? (b / 1024).toFixed(0) + " KB" :
  b < 1073741824 ? (b / 1048576).toFixed(1) + " MB" : (b / 1073741824).toFixed(2) + " GB";
const fmtDate = t => t ? new Date(t * 1000).toLocaleDateString("pl-PL", { day: "numeric", month: "short", year: "numeric" }) : "";
const fmtDateTime = t => t ? new Date(t * 1000).toLocaleString("pl-PL") : "";
const fmtDur = s => { if (!s) return ""; s = Math.round(s); const m = Math.floor(s / 60); return `${m}:${String(s % 60).padStart(2, "0")}`; };

// ------------------------------------------------------------------ params & search
function params(extra = {}, forUrl = false) {
  const p = new URLSearchParams();
  if (S.q && !S.collection) p.set("q", S.q);
  if (S.collection) p.set("collection", S.collection);
  if (S.similar) p.set("similar", S.similar);
  if (S.kinds.size) p.set("kinds", [...S.kinds].join(","));
  if (S.roots.size) p.set("roots", [...S.roots].join("|"));
  if (S.since) p.set("since", S.since);
  if (S.folder) p.set("folder", S.folder);
  if (S.tags.size) p.set("tags", [...S.tags].join(","));
  const browsing = !S.q && !S.collection && !S.similar && !S.image;
  // browsing without a query falls back to newest-first, but that implicit default never goes into the URL
  const sort = S.sort === "relevance" && browsing && !forUrl ? "mtime" : S.sort;
  if (sort !== "relevance") { p.set("sort", sort); p.set("order", S.order); }
  for (const [k, v] of Object.entries(extra)) p.set(k, v);
  return p;
}

function syncUrl() {
  const p = params({}, true); p.delete("offset"); p.delete("limit");
  history.replaceState(null, "", p.toString() ? "?" + p : location.pathname);
}

async function search(reset = true) {
  if (!reset && (S.loading || S.items.length >= S.total)) return;
  const id = ++S.reqId;
  S.loading = true;
  const offset = reset ? 0 : S.items.length;
  $("#more").textContent = "Szukam…";
  try {
    let r;
    const p = params({ offset, limit: 120 });
    if (S.image) {
      const fd = new FormData(); fd.append("file", S.image, S.image.name || "obraz.png");
      r = await api("/api/search/image?" + p, { method: "POST", body: fd });
    } else {
      r = await api("/api/search?" + p);
    }
    if (id !== S.reqId) return;
    if (reset) { S.items = []; $("#results").innerHTML = ""; S.cur = -1; $("main").scrollTop = 0; }
    S.total = r.total;
    const start = S.items.length;
    S.items.push(...r.items);
    renderCards(r.items, start);
    renderInfo(r);
    syncUrl();
  } catch (e) {
    if (id === S.reqId) { $("#more").textContent = ""; toast("Błąd: " + e.message, 4000); }
  } finally {
    if (id === S.reqId) S.loading = false;
  }
}

function renderInfo(r) {
  const n = S.total;
  let what = S.image ? "Podobne do obrazu" : S.similar ? "Podobne pliki" :
    S.collection ? "Kolekcja: " + (S.cols.find(c => c.id == S.collection)?.name || "") : S.q ? "Wyniki" : "Przeglądanie";
  $("#count").innerHTML = `${esc(what)} · <b>${n.toLocaleString("pl-PL")}</b> ${n === 1 ? "plik" : "plików"}` +
    (r.ms ? ` · ${r.ms} ms` : "") +
    (S.similar || S.image || S.collection ? ` · <a href="#" id="clearMode">wyczyść</a>` : "");
  $("#clearMode")?.addEventListener("click", e => { e.preventDefault(); resetMode(); search(); });
  $("#more").textContent = S.items.length < S.total ? "" : (n ? "— koniec —" : "");
  const empty = $("#empty");
  empty.hidden = n > 0;
  if (!n) empty.innerHTML = S.status && !S.status.vectors ?
    "<b>Indeks się buduje…</b>Pierwsze wyniki pojawią się za chwilę — postęp widać w lewym dolnym rogu." :
    "<b>Nic nie znalazłem</b>Spróbuj opisać inaczej, zdejmij filtry albo przeciągnij podobny obraz.";
}

function resetMode() { S.similar = null; S.image = null; S.collection = null; renderCols(); }

// ------------------------------------------------------------------ cards
function thumbHtml(it, big = false) {
  const ph = `<div class="ph">${ICON[it.kind] || "📁"}<small>${esc((it.ext || "").replace(".", "").toUpperCase())}</small></div>`;
  if (!THUMBABLE.has(it.kind)) return ph;
  return `<img loading="lazy" decoding="async" src="/api/thumb/${it.id}?v=${Math.round(it.mtime || 0)}" alt=""
    onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'ph',innerHTML:this.dataset.ph}))"
    data-ph="${esc(ph.replace(/^<div class="ph">|<\/div>$/g, ""))}">`;
}

function cardHtml(it, i) {
  const tags = it.tags.map(t => S.tagsList.find(x => x.id === t)).filter(Boolean)
    .map(t => `<i style="background:${esc(t.color)}" title="${esc(t.name)}"></i>`).join("");
  const dims = it.width ? `${it.width}×${it.height}` : it.duration ? fmtDur(it.duration) : it.pages ? `${it.pages} str.` : "";
  const score = it.score != null ? `<div class="score" style="width:${Math.max(6, Math.min(100, (it.score - 1) * 22))}%"></div>` : "";
  return `<div class="card${S.sel.has(it.id) ? " sel" : ""}" data-i="${i}" draggable="false">
    <div class="th">${thumbHtml(it)}
      ${it.kind === "video" || it.kind === "audio" ? `<span class="badge">${ICON[it.kind]} ${fmtDur(it.duration)}</span>` : ""}
      ${it.offline ? `<span class="badge r off" title="Dysk offline — miniatura z pamięci">offline</span>` : ""}
      ${it.why ? `<span class="why">${esc(it.why)}${it.t != null ? " @" + fmtDur(it.t) : ""}</span>` : ""}
      <div class="tagdots">${tags}</div>${score}
    </div>
    <div class="cap"><div class="nm" title="${esc(it.name)}">${esc(it.name)}</div>
      <div class="mt"><span title="${esc(it.folder)}">${esc(it.folder.split("/").slice(-1)[0])}</span><span>${fmtDate(it.mtime)}</span></div></div>
    <div class="col">${esc(dims)}</div><div class="col">${fmtSize(it.size)}</div><div class="col">${fmtDate(it.mtime)}</div>
  </div>`;
}

function renderCards(items, start) {
  const html = items.map((it, k) => cardHtml(it, start + k)).join("");
  $("#results").insertAdjacentHTML("beforeend", html);
}

function rerenderCard(i) {
  const el = $(`.card[data-i="${i}"]`);
  if (el) el.outerHTML = cardHtml(S.items[i], i);
  if (i === S.cur) $(`.card[data-i="${i}"]`)?.classList.add("cur");
}

function applyView() {
  const r = $("#results");
  r.classList.toggle("listv", S.view === "list");
  r.classList.toggle("fit", S.fit);
  r.style.setProperty("--z", S.zoom + "px");
  $("#zoom").value = S.zoom;
  $$("#viewSeg button").forEach(b => b.classList.toggle("on", b.dataset.v === S.view));
}

// ------------------------------------------------------------------ selection & preview
function setCur(i, { scroll = true, preview = null } = {}) {
  if (i < 0 || i >= S.items.length) return;
  $(".card.cur")?.classList.remove("cur");
  S.cur = i;
  const el = $(`.card[data-i="${i}"]`);
  el?.classList.add("cur");
  if (scroll) el?.scrollIntoView({ block: "nearest" });
  if (preview ?? !$("#preview").hidden) openPreview(S.items[i]);
}

function updateSelBar() {
  $("#selbar").hidden = S.sel.size < 2;
  $("#selN").textContent = `${S.sel.size} zaznaczonych`;
}

function toggleSel(i, additive) {
  const id = S.items[i].id;
  if (!additive) { S.sel.forEach(x => { const j = S.items.findIndex(it => it.id === x); if (j >= 0) $(`.card[data-i="${j}"]`)?.classList.remove("sel"); }); S.sel.clear(); }
  if (S.sel.has(id) && additive) S.sel.delete(id); else S.sel.add(id);
  $(`.card[data-i="${i}"]`)?.classList.toggle("sel", S.sel.has(id));
  updateSelBar();
}

async function openPreview(it) {
  S.pv = it;
  const pv = $("#preview");
  pv.hidden = false;
  pv.dataset.id = it.id;
  const m = $("#pvMedia");
  const raw = `/api/raw/${it.id}`;
  if (it.offline) m.innerHTML = thumbHtml(it, true);
  else if (it.kind === "image" || it.kind === "raw") m.innerHTML = `<img src="${raw}" alt="">`;
  else if (it.kind === "video") m.innerHTML = `<video src="${raw}${it.t != null ? "#t=" + it.t : ""}" controls autoplay muted playsinline poster="/api/thumb/${it.id}"></video>`;
  else if (it.kind === "audio") m.innerHTML = `<div class="ph">🎵</div><audio src="${raw}" controls autoplay></audio>`;
  else if (it.kind === "pdf") m.innerHTML = `<iframe src="${raw}#toolbar=0&view=FitH"></iframe>`;
  else m.innerHTML = thumbHtml(it, true);
  m.style.display = it.kind === "audio" ? "flex" : "";
  m.style.flexDirection = "column"; m.style.alignItems = "center";
  $("#pvName").textContent = it.name;
  $("#pvPath").textContent = it.path;
  renderPvTags(it);
  const meta = [
    ["Typ", `${ICON[it.kind] || ""} ${(S.status?.labels || {})[it.kind] || it.kind} ${it.ext ? "(" + it.ext + ")" : ""}`],
    ["Rozmiar", fmtSize(it.size)],
    it.width && ["Wymiary", `${it.width} × ${it.height} px`],
    it.duration && ["Czas trwania", fmtDur(it.duration)],
    it.pages && ["Strony", it.pages],
    ["Zmodyfikowano", fmtDateTime(it.mtime)],
    ["Utworzono", fmtDateTime(it.ctime)],
    it.why && ["Dopasowanie", `${it.why}${it.t != null ? " (od " + fmtDur(it.t) + ")" : ""} · ${it.score}`],
    it.offline && ["Status", "🔴 dysk offline"],
  ].filter(Boolean);
  $("#pvMeta").innerHTML = meta.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
  const t = $("#pvText"); t.hidden = true;
  try {
    const info = await api(`/api/file/${it.id}`);
    if (pv.dataset.id != it.id) return;
    if (info.text && !["pdf"].includes(it.kind)) { t.textContent = info.text; t.hidden = false; }
    if (info.error) $("#pvMeta").insertAdjacentHTML("beforeend", `<dt>Uwaga</dt><dd>${esc(info.error)}</dd>`);
  } catch {}
}

function renderPvTags(it) {
  $("#pvTags").innerHTML = S.tagsList.map(t => `<button data-t="${t.id}" class="${it.tags.includes(t.id) ? "on" : ""}">
    <span class="dot" style="background:${esc(t.color)}"></span>${esc(t.name)}</button>`).join("") +
    `<button data-t="new">＋ tag</button>`;
}

function closePreview() {
  S.pv = null;
  $("#preview").hidden = true;
  $("#pvMedia").innerHTML = "";
}

const curItem = () => S.items[S.cur];
// Buttons in the preview act on what the preview shows, even if the grid was re-searched meanwhile.
const pvItem = () => (!$("#preview").hidden && S.pv) || curItem();
const selectedIds = () => S.sel.size ? [...S.sel] : curItem() ? [curItem().id] : [];

async function act(what, it = pvItem()) {
  if (!it) return;
  try {
    await api(`/api/action/${it.id}/${what}`, { method: "POST" });
    toast(what === "reveal" ? "Pokazuję w Finderze…" : "Otwieram…", 1200);
  } catch (e) { toast("Nie udało się: " + e.message, 3000); }
}

async function setTag(tagId, ids, add) {
  await api(`/api/tags/${tagId}/files`, { method: "POST", json: { ids, add } });
  for (const [i, it] of S.items.entries()) {
    if (!ids.includes(it.id)) continue;
    it.tags = add ? [...new Set([...it.tags, tagId])] : it.tags.filter(t => t !== tagId);
    rerenderCard(i);
  }
  if (S.pv && !$("#preview").hidden) { const p = S.items.find(x => x.id === S.pv.id); if (p) S.pv = p; else if (ids.includes(S.pv.id)) S.pv.tags = add ? [...new Set([...S.pv.tags, tagId])] : S.pv.tags.filter(t => t !== tagId); renderPvTags(S.pv); }
  loadTags();
}

async function newTag(name) {
  name = name ?? prompt("Nazwa nowego tagu:");
  if (!name) return null;
  const palette = ["#ef5350", "#ff9800", "#ffca28", "#66bb6a", "#26a69a", "#42a5f5", "#7e57c2", "#ec407a", "#8d6e63", "#78909c"];
  const t = await api("/api/tags", { method: "POST", json: { name, color: palette[S.tagsList.length % palette.length] } });
  await loadTags();
  return t;
}

function tagMenu(x, y, ids) {
  const m = $("#menu");
  m.innerHTML = `<input placeholder="Szukaj / nowy tag…" id="tmq">` + S.tagsList.map(t => {
    const all = ids.every(id => S.items.find(i => i.id === id)?.tags.includes(t.id));
    return `<button class="item" data-t="${t.id}"><span class="dot" style="background:${esc(t.color)}"></span>${esc(t.name)}<span class="n">${all ? "✓" : ""}</span></button>`;
  }).join("");
  m.style.left = Math.min(x, innerWidth - 220) + "px"; m.style.top = Math.min(y, innerHeight - 300) + "px";
  m.hidden = false;
  const inp = $("#tmq"); inp.focus();
  inp.oninput = () => $$(".item", m).forEach(b => b.hidden = !b.textContent.toLowerCase().includes(inp.value.toLowerCase()));
  inp.onkeydown = async e => {
    if (e.key === "Enter" && inp.value.trim()) {
      const vis = $$(".item", m).filter(b => !b.hidden);
      const t = vis.length === 1 ? { id: +vis[0].dataset.t } : await newTag(inp.value.trim());
      if (t) await setTag(t.id, ids, true);
      m.hidden = true;
    }
    if (e.key === "Escape") m.hidden = true;
    e.stopPropagation();
  };
  m.onclick = async e => {
    const b = e.target.closest(".item"); if (!b) return;
    const tid = +b.dataset.t;
    const all = ids.every(id => S.items.find(i => i.id === id)?.tags.includes(tid));
    await setTag(tid, ids, !all);
    m.hidden = true;
  };
}

// ------------------------------------------------------------------ sidebar
function renderKinds() {
  const k = S.status?.kinds || {};
  const labels = S.status?.labels || {};
  $("#kinds").innerHTML = KIND_ORDER.filter(x => k[x]).map(x =>
    `<button data-k="${x}" class="${S.kinds.has(x) ? "on" : ""}">${ICON[x]} ${esc(labels[x] || x)}<small>${k[x].toLocaleString("pl-PL")}</small></button>`).join("");
}

function renderRoots() {
  $("#roots").innerHTML = (S.status?.roots || []).map(r => `<button class="item ${S.roots.has(r.path) ? "on" : ""}" data-r="${esc(r.path)}">
    <span class="dot ${r.online ? "online" : "offline"}" title="${r.online ? "online" : "offline — wyniki z indeksu"}"></span>
    ${esc(r.path.split("/").pop() || r.path)}<span class="n">${r.count.toLocaleString("pl-PL")}</span></button>`).join("");
  $("#roots").insertAdjacentHTML("beforeend", (S.status?.places || []).map(pl => `<button class="item ${S.folder === pl.path ? "on" : ""}" data-place="${esc(pl.path)}" title="${esc(pl.path)}">
    <span>${esc(pl.icon || "📁")}</span>${esc(pl.name)}<span class="n">${pl.count.toLocaleString("pl-PL")}</span></button>`).join(""));
  const isPlace = (S.status?.places || []).some(pl => pl.path === S.folder);
  const ff = $("#folderFilter");
  ff.hidden = !S.folder || isPlace;
  ff.innerHTML = S.folder ? `📂 ${esc(S.folder)} ✕` : "";
}

function renderTags() {
  $("#tags").innerHTML = S.tagsList.map(t => `<button class="item ${S.tags.has(t.id) ? "on" : ""}" data-t="${t.id}">
    <span class="dot" style="background:${esc(t.color)}" title="Zmień kolor"></span>${esc(t.name)}<span class="n">${t.n || ""}</span>
    ${t.id !== 1 ? `<span class="x" data-del="${t.id}" title="Usuń tag">✕</span>` : ""}</button>`).join("") ||
    `<div class="item" style="color:var(--muted)">Brak tagów</div>`;
}

function renderCols() {
  $("#cols").innerHTML = S.cols.map(c => `<button class="item ${S.collection == c.id ? "on" : ""}" data-c="${c.id}" title="${esc(c.query)}">
    <span>${esc(c.icon || "🔎")}</span>${esc(c.name)}<span class="x" data-del="${c.id}" title="Usuń kolekcję">✕</span></button>`).join("");
}

function renderStatus() {
  const s = S.status; if (!s) return;
  const ix = s.indexer || {};
  const st = s.stages || {};
  const totalFiles = Object.values(s.kinds || {}).reduce((a, b) => a + b, 0);
  const pending = (st[0] || 0) + (st[1] || 0);
  const pct = ix.total ? Math.min(100, (ix.done / ix.total) * 100) : 0;
  const eta = ix.rate && ix.total ? Math.max(0, (ix.total - ix.done) / ix.rate) : 0;
  const etaS = eta > 3600 ? `~${(eta / 3600).toFixed(1)} h` : eta > 60 ? `~${Math.round(eta / 60)} min` : eta ? "<1 min" : "";
  $("#status").innerHTML = `
    <div><b>${totalFiles.toLocaleString("pl-PL")}</b> plików · ${(s.vectors || 0).toLocaleString("pl-PL")} wektorów</div>
    ${ix.running ? `<div style="margin-top:6px">⏳ ${esc(ix.phase || "")}</div>
      <div class="prog"><i style="width:${pct}%"></i></div>
      <div>${(ix.done || 0).toLocaleString("pl-PL")} / ${(ix.total || 0).toLocaleString("pl-PL")} ${ix.rate ? `· ${ix.rate}/s` : ""} ${etaS}</div>
      <div class="cur" title="${esc(ix.current)}">${esc(ix.current || "")}</div>
      <button id="ixStop">Wstrzymaj</button>` :
      `<div style="margin-top:4px">${pending ? `Do przeanalizowania: ${pending.toLocaleString("pl-PL")}` : "✅ Indeks aktualny"}</div>
      <div>${s.last_index ? "Ostatnio: " + fmtDateTime(s.last_index) : ""}</div>
      ${(ix.denied || []).length ? `<div class="warn" title="${esc(ix.denied.join("\n"))}">⚠️ Brak dostępu do ${ix.denied.length} folderów — Ustawienia → Prywatność → Pełny dostęp do dysku → Lupa</div>` : ""}
      <button id="ixStart">Indeksuj teraz</button>`}
    ${s.model_ready ? "" : `<div style="margin-top:6px">🧠 Ładuję model…</div>`}`;
  $("#ixStart")?.addEventListener("click", async () => { await api("/api/index/start", { method: "POST" }); toast("Indeksowanie wystartowało"); setTimeout(loadStatus, 1500); });
  $("#ixStop")?.addEventListener("click", async () => { await api("/api/index/stop", { method: "POST" }); toast("Zatrzymuję po bieżącej paczce"); });
}

async function loadStatus() {
  try {
    const first = !S.status;
    S.status = await api("/api/status");
    renderKinds(); renderRoots(); renderStatus();
    if (first) search();
  } catch {}
  clearTimeout(loadStatus._t);
  loadStatus._t = setTimeout(loadStatus, S.status?.indexer?.running ? 3000 : 15000);
}
async function loadTags() { S.tagsList = await api("/api/tags"); renderTags(); }
async function loadCols() { S.cols = await api("/api/collections"); renderCols(); }

// ------------------------------------------------------------------ events
let qTimer;
$("#q").addEventListener("input", e => {
  S.q = e.target.value.trim();
  resetMode();
  if (S.q && S.sort !== "relevance") { /* keep user's sort */ }
  clearTimeout(qTimer);
  qTimer = setTimeout(() => search(), 280);
});
$("#q").addEventListener("keydown", e => {
  if (e.key === "Enter") { clearTimeout(qTimer); search(); }
  if (e.key === "ArrowDown") { e.preventDefault(); $("#q").blur(); setCur(Math.max(0, S.cur)); }
  if (e.key === "Escape") { if ($("#q").value) { $("#q").value = ""; S.q = ""; search(); } else $("#q").blur(); }
});

$("#sort").addEventListener("change", e => { S.sort = e.target.value; search(); });
$("#order").addEventListener("click", () => { S.order = S.order === "desc" ? "asc" : "desc"; $("#order").textContent = S.order === "desc" ? "↓" : "↑"; search(); });
$("#zoom").addEventListener("input", e => { S.zoom = +e.target.value; store.set("zoom", S.zoom); applyView(); });
$("#viewSeg").addEventListener("click", e => { const b = e.target.closest("button"); if (!b) return; S.view = b.dataset.v; store.set("view", S.view); applyView(); });
$("#fitBtn").addEventListener("click", () => { S.fit = !S.fit; store.set("fit", S.fit); applyView(); });

$("#kinds").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  const k = b.dataset.k;
  if (e.metaKey || e.shiftKey) S.kinds.has(k) ? S.kinds.delete(k) : S.kinds.add(k);
  else { const only = S.kinds.size === 1 && S.kinds.has(k); S.kinds.clear(); if (!only) S.kinds.add(k); }
  renderKinds(); search();
});
$("#since").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  S.since = b.dataset.d; $$("#since button").forEach(x => x.classList.toggle("on", x === b)); search();
});
$("#roots").addEventListener("click", e => {
  const pl = e.target.closest("[data-place]");
  if (pl) { S.folder = S.folder === pl.dataset.place ? "" : pl.dataset.place; renderRoots(); search(); return; }
  const b = e.target.closest("[data-r]"); if (!b) return;
  const r = b.dataset.r; S.roots.has(r) ? S.roots.delete(r) : S.roots.add(r); renderRoots(); search();
});
$("#folderFilter").addEventListener("click", () => { S.folder = ""; renderRoots(); search(); });
const TAG_COLORS = ["#ef5350", "#ff7043", "#ff9800", "#f5b301", "#cddc39", "#66bb6a", "#26a69a", "#29b6f6",
  "#42a5f5", "#5c6bc0", "#7e57c2", "#ab47bc", "#ec407a", "#8d6e63", "#78909c", "#e0e0e0"];
function tagEditMenu(x, y, tid) {
  const t = S.tagsList.find(t => t.id === tid); if (!t) return;
  const m = $("#menu");
  m.innerHTML = `<div class="swatches">${TAG_COLORS.map(c => `<button data-c="${c}" style="background:${c}" class="${c === t.color ? "on" : ""}" title="${c}"></button>`).join("")}
    <label class="custom" title="Własny kolor"><input type="color" value="${esc(t.color)}">🎨</label></div>
    <button class="item" data-a="rename">✏️ Zmień nazwę</button>
    ${tid !== 1 ? `<button class="item" data-a="delete">🗑 Usuń tag</button>` : ""}`;
  m.style.left = Math.min(x, innerWidth - 240) + "px"; m.style.top = Math.min(y, innerHeight - 220) + "px";
  m.hidden = false;
  const setColor = async c => { await api(`/api/tags/${tid}`, { method: "PATCH", json: { color: c } }); await loadTags(); $$(".card").forEach(el => rerenderCard(+el.dataset.i)); if (S.pv) renderPvTags(S.pv); };
  $("input[type=color]", m).oninput = e => setColor(e.target.value);
  m.onclick = async e => {
    const sw = e.target.closest("[data-c]");
    if (sw) { await setColor(sw.dataset.c); m.hidden = true; return; }
    const a = e.target.closest("[data-a]")?.dataset.a;
    if (a === "rename") { const n = prompt("Nowa nazwa tagu:", t.name); if (n) { await api(`/api/tags/${tid}`, { method: "PATCH", json: { name: n } }); loadTags(); } m.hidden = true; }
    if (a === "delete" && confirm(`Usunąć tag „${t.name}”? (pliki zostaną, zniknie tylko etykieta)`)) {
      await api(`/api/tags/${tid}`, { method: "DELETE" }); S.tags.delete(tid); m.hidden = true; await loadTags(); search();
    }
  };
}
$("#tags").addEventListener("contextmenu", e => {
  const b = e.target.closest("[data-t]"); if (!b) return;
  e.preventDefault(); tagEditMenu(e.clientX, e.clientY, +b.dataset.t);
});
$("#tags").addEventListener("click", async e => {
  if (e.target.classList.contains("dot")) { e.stopPropagation(); const b = e.target.closest("[data-t]"); const r = e.target.getBoundingClientRect(); tagEditMenu(r.left, r.bottom + 6, +b.dataset.t); return; }
  const del = e.target.closest("[data-del]");
  if (del) { e.stopPropagation(); if (confirm("Usunąć tag? (pliki zostaną, zniknie tylko etykieta)")) { await api(`/api/tags/${del.dataset.del}`, { method: "DELETE" }); S.tags.delete(+del.dataset.del); loadTags(); search(); } return; }
  const b = e.target.closest("[data-t]"); if (!b) return;
  const t = +b.dataset.t; S.tags.has(t) ? S.tags.delete(t) : S.tags.add(t); renderTags(); search();
});
$("#addTag").addEventListener("click", async () => { const t = await newTag(); if (t && selectedIds().length && confirm(`Dodać tag „${t.name}” do zaznaczonych plików?`)) setTag(t.id, selectedIds(), true); });
$("#cols").addEventListener("click", async e => {
  const del = e.target.closest("[data-del]");
  if (del) { e.stopPropagation(); if (confirm("Usunąć kolekcję?")) { await api(`/api/collections/${del.dataset.del}`, { method: "DELETE" }); if (S.collection == del.dataset.del) S.collection = null; loadCols(); } return; }
  const b = e.target.closest("[data-c]"); if (!b) return;
  const id = +b.dataset.c;
  const was = S.collection === id;
  resetMode(); $("#q").value = ""; S.q = "";
  if (!was) S.collection = id;
  renderCols(); search();
});
$("#saveCol").addEventListener("click", async () => {
  if (!S.q) return toast("Najpierw wpisz zapytanie — kolekcja zapamięta je razem z filtrem typów");
  const name = prompt("Nazwa kolekcji:", S.q.slice(0, 40)); if (!name) return;
  await api("/api/collections", { method: "POST", json: { name, query: S.q, kinds: [...S.kinds].join(","), icon: "⭐" } });
  loadCols(); toast("Zapisano kolekcję");
});

// results: click, dblclick, context menu, hover video
$("#results").addEventListener("click", e => {
  const c = e.target.closest(".card"); if (!c) return;
  const i = +c.dataset.i;
  if (e.shiftKey && S.cur >= 0) { const [a, b] = [Math.min(S.cur, i), Math.max(S.cur, i)]; for (let j = a; j <= b; j++) { S.sel.add(S.items[j].id); $(`.card[data-i="${j}"]`)?.classList.add("sel"); } updateSelBar(); }
  else toggleSel(i, e.metaKey || e.ctrlKey);
  setCur(i, { scroll: false, preview: true });
});
$("#results").addEventListener("dblclick", e => { const c = e.target.closest(".card"); if (c) act("open", S.items[+c.dataset.i]); });
$("#results").addEventListener("contextmenu", e => {
  const c = e.target.closest(".card"); if (!c) return;
  e.preventDefault();
  const i = +c.dataset.i;
  if (!S.sel.has(S.items[i].id)) { toggleSel(i, false); setCur(i, { scroll: false }); }
  tagMenu(e.clientX, e.clientY, selectedIds());
});
let hoverT;
$("#results").addEventListener("mouseover", e => {
  const c = e.target.closest(".card"); if (!c || S.view === "list") return;
  const it = S.items[+c.dataset.i];
  if (it?.kind !== "video" || it.offline || c.querySelector("video")) return;
  clearTimeout(hoverT);
  hoverT = setTimeout(() => {
    const th = c.querySelector(".th");
    const v = document.createElement("video");
    Object.assign(v, { src: `/api/raw/${it.id}#t=${it.t ?? 0}`, muted: true, autoplay: true, loop: true, playsInline: true });
    th.prepend(v); th.querySelector("img")?.setAttribute("hidden", "");
    c.addEventListener("mouseleave", () => { v.remove(); th.querySelector("img")?.removeAttribute("hidden"); }, { once: true });
  }, 600);
});
$("#results").addEventListener("mouseout", e => { if (!e.relatedTarget?.closest?.(".card")) clearTimeout(hoverT); });

new IntersectionObserver(es => { if (es[0].isIntersecting && S.items.length) search(false); }, { root: $("main"), rootMargin: "800px" }).observe($("#more"));

// preview buttons
$("#pvClose").addEventListener("click", closePreview);
$("#pvOpen").addEventListener("click", () => act("open"));
$("#pvReveal").addEventListener("click", () => act("reveal"));
$("#pvQL").addEventListener("click", () => openLightbox());
$("#pvMedia").addEventListener("click", e => { if (e.target.tagName === "IMG") openLightbox(); });
$("#pvSim").addEventListener("click", () => similar());
$("#pvFolder").addEventListener("click", () => { const it = pvItem(); if (!it) return; resetMode(); S.q = ""; $("#q").value = ""; S.folder = it.folder; S.sort = "name"; S.order = "asc"; $("#sort").value = "name"; $("#order").textContent = "↑"; renderRoots(); search(); });
$("#pvPath").addEventListener("click", () => { navigator.clipboard.writeText($("#pvPath").textContent).then(() => toast("Skopiowano ścieżkę")); });
$("#pvTags").addEventListener("click", async e => {
  const it = pvItem(); const b = e.target.closest("button"); if (!b || !it) return;
  if (b.dataset.t === "new") { const t = await newTag(); if (t) setTag(t.id, [it.id], true); return; }
  const tid = +b.dataset.t; setTag(tid, [it.id], !it.tags.includes(tid));
});
$("#selTag").addEventListener("click", e => tagMenu(e.clientX, e.clientY + 10, selectedIds()));
$("#selFav").addEventListener("click", () => setTag(1, selectedIds(), true));
$("#selClear").addEventListener("click", () => { S.sel.clear(); $$(".card.sel").forEach(c => c.classList.remove("sel")); updateSelBar(); });
document.addEventListener("click", e => { if (!e.target.closest("#menu") && !e.target.closest("#selTag") && !e.target.closest("#tags .dot")) $("#menu").hidden = true; });

function similar() {
  const it = pvItem(); if (!it) return;
  resetMode(); S.similar = it.id; S.q = ""; $("#q").value = ""; S.sort = "relevance"; $("#sort").value = "relevance";
  closePreview(); search();
}

// fullscreen viewer
function lbRender(it) {
  const m = $("#lbMedia"), raw = `/api/raw/${it.id}`;
  if (it.offline) m.innerHTML = `<img src="/api/thumb/${it.id}">`;
  else if (it.kind === "image" || it.kind === "raw") m.innerHTML = `<img src="${raw}" alt="">`;
  else if (it.kind === "video") m.innerHTML = `<video src="${raw}${it.t != null ? "#t=" + it.t : ""}" controls autoplay playsinline></video>`;
  else if (it.kind === "audio") m.innerHTML = `<div><div class="ph">🎵</div><audio src="${raw}" controls autoplay></audio></div>`;
  else if (it.kind === "pdf") m.innerHTML = `<iframe src="${raw}#view=FitH"></iframe>`;
  else if (THUMBABLE.has(it.kind)) m.innerHTML = `<img src="/api/thumb/${it.id}" alt="" onerror="this.outerHTML='<div class=ph>${ICON[it.kind] || "📁"}</div>'">`;
  else m.innerHTML = `<div class="ph">${ICON[it.kind] || "📁"}</div>`;
  $("#lbName").textContent = `${it.name} — ${it.folder}`;
}
function openLightbox() { const it = pvItem(); if (!it) return; $("#lightbox").hidden = false; lbRender(it); }
function closeLightbox() { $("#lightbox").hidden = true; $("#lbMedia").innerHTML = ""; }
function lbStep(d) {
  let i = S.items.findIndex(x => x.id === (S.pv || curItem())?.id);
  i = Math.max(0, Math.min(S.items.length - 1, (i < 0 ? 0 : i + d)));
  setCur(i, { preview: true });
  lbRender(S.items[i]);
  if (i > S.items.length - 10) search(false);
}
$("#lbPrev").addEventListener("click", () => lbStep(-1));
$("#lbNext").addEventListener("click", () => lbStep(1));
$("#lightbox").addEventListener("click", e => { if (e.target.id === "lightbox" || e.target.id === "lbMedia") closeLightbox(); });

// image search: button, drag&drop, paste
function imageSearch(file) {
  if (!file || !file.type.startsWith("image/")) return toast("To nie jest obraz");
  resetMode(); S.image = file; S.q = ""; $("#q").value = ""; S.sort = "relevance"; $("#sort").value = "relevance";
  search();
}
$("#imgBtn").addEventListener("click", () => $("#imgFile").click());
$("#imgFile").addEventListener("change", e => imageSearch(e.target.files[0]));
let dragDepth = 0;
addEventListener("dragenter", e => { if (e.dataTransfer?.types.includes("Files")) { dragDepth++; $("#drop").hidden = false; } });
addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; $("#drop").hidden = true; } });
addEventListener("dragover", e => e.preventDefault());
addEventListener("drop", e => { e.preventDefault(); dragDepth = 0; $("#drop").hidden = true; imageSearch(e.dataTransfer.files[0]); });
addEventListener("paste", e => { const f = [...(e.clipboardData?.files || [])].find(f => f.type.startsWith("image/")); if (f) { e.preventDefault(); imageSearch(f); } });

// keyboard
function cols() {
  if (S.view === "list") return 1;
  const cards = $$(".card").slice(0, 30);
  if (cards.length < 2) return 1;
  const top = cards[0].offsetTop; return Math.max(1, cards.filter(c => c.offsetTop === top).length);
}
addEventListener("keydown", e => {
  const typing = e.target.matches("input, textarea, select");
  if ((e.key === "/" && !typing) || (e.key === "k" && e.metaKey)) { e.preventDefault(); $("#q").focus(); $("#q").select(); return; }
  if (typing) return;
  if (!$("#lightbox").hidden) {
    if (e.key === "Escape" || e.key === "q" || e.key === " ") { e.preventDefault(); closeLightbox(); }
    else if (e.key === "ArrowRight" || e.key === "ArrowDown") { e.preventDefault(); lbStep(1); }
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") { e.preventDefault(); lbStep(-1); }
    else if (e.key === "Enter") act(e.metaKey ? "reveal" : "open");
    return;
  }
  if (!$("#menu").hidden && e.key === "Escape") { $("#menu").hidden = true; return; }
  const n = cols();
  const mv = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: n, ArrowUp: -n }[e.key];
  if (mv) {
    e.preventDefault();
    const next = Math.max(0, Math.min(S.items.length - 1, (S.cur < 0 ? 0 : S.cur + mv)));
    if (e.shiftKey) { S.sel.add(S.items[next].id); $(`.card[data-i="${next}"]`)?.classList.add("sel"); updateSelBar(); }
    setCur(next);
    if (next > S.items.length - 20) search(false);
    return;
  }
  if (e.key === " ") { e.preventDefault(); if ($("#preview").hidden && curItem()) openPreview(curItem()); else closePreview(); }
  else if (e.key === "Enter" && pvItem()) act(e.metaKey ? "reveal" : "open");
  else if (e.key === "Escape") { if (!$("#preview").hidden) closePreview(); else if (S.sel.size) $("#selClear").click(); else if (S.similar || S.image || S.collection) { resetMode(); search(); } }
  else if (e.key === "s" || e.key === "S") similar();
  else if (e.key === "q" || e.key === "Q") openLightbox();
  else if (e.key === "t" && curItem()) { const r = $(`.card[data-i="${S.cur}"]`).getBoundingClientRect(); tagMenu(r.left + 20, r.top + 40, selectedIds()); e.preventDefault(); }
  else if (e.key === "f" && curItem()) { const ids = selectedIds(); setTag(1, ids, !curItem().tags.includes(1)); }
  else if (e.key === "a" && e.metaKey) { e.preventDefault(); S.items.forEach((it, i) => { S.sel.add(it.id); $(`.card[data-i="${i}"]`)?.classList.add("sel"); }); updateSelBar(); }
});
addEventListener("focus", () => { if (!document.activeElement || document.activeElement === document.body) $("#q").focus(); });

// ------------------------------------------------------------------ init
(function init() {
  const p = new URLSearchParams(location.search);
  S.q = p.get("q") || ""; $("#q").value = S.q;
  if (p.get("kinds")) p.get("kinds").split(",").forEach(k => S.kinds.add(k));
  if (p.get("roots")) p.get("roots").split("|").forEach(r => S.roots.add(r));
  if (p.get("tags")) p.get("tags").split(",").forEach(t => S.tags.add(+t));
  if (p.get("collection")) S.collection = +p.get("collection");
  if (p.get("similar")) S.similar = +p.get("similar");
  S.since = p.get("since") || ""; S.folder = p.get("folder") || "";
  if (p.get("sort")) { S.sort = p.get("sort"); S.order = p.get("order") || "desc"; }
  $("#sort").value = S.sort; $("#order").textContent = S.order === "desc" ? "↓" : "↑";
  $$("#since button").forEach(b => b.classList.toggle("on", b.dataset.d === S.since));
  applyView();
  Promise.all([loadTags(), loadCols()]).finally(loadStatus);
  $("#q").focus();
})();
