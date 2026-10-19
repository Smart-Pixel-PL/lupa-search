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
  q: "", kinds: new Set(), exts: new Set(), folders: new Set(), pins: [], group: store.get("group", false),
  color: "", browse: null, since: "", roots: new Set(), folder: "", tags: new Set(), collection: null, similar: null,
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
  if (S.likeTag) p.set("like_tag", S.likeTag);
  if (S.kinds.size) p.set("kinds", [...S.kinds].join(","));
  if (S.exts.size) p.set("exts", [...S.exts].join(","));
  if (S.roots.size) p.set("roots", [...S.roots].join("|"));
  if (S.since) p.set("since", S.since);
  if (S.folders.size) p.set("folder", [...S.folders].join("|"));
  if (S.group) p.set("group", "1");
  if (S.color) p.set("color", S.color.replace("#", ""));
  if (S.browse != null && S.browse !== "" && !S.q) { p.set("folder", S.browse); p.set("direct", "1"); }
  if (S.tags.size) p.set("tags", [...S.tags].join(","));
  const browsing = !S.q && !S.collection && !S.similar && !S.image && !S.color && !S.likeTag;
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
  if (reset) {
    const what = S.image ? "podobnych obrazów" : S.q ? `„${S.q}”` : "";
    $("#count").innerHTML = `<span class="spin"></span> ${what ? "Szukam " + esc(what) + "…" : "Wczytuję…"}`;
    $("#results").classList.add("busy");
  }
  if (reset) folderHits();
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
    if (id === S.reqId) { S.loading = false; $("#results").classList.remove("busy"); }
  }
}

function renderInfo(r) {
  const n = S.total;
  let what = S.likeTag ? (r.query || "Pasujące do tagu") : S.image ? "Podobne do obrazu" : S.similar ? "Podobne pliki" :
    S.collection ? "Kolekcja: " + (S.cols.find(c => c.id == S.collection)?.name || "") : S.q ? "Wyniki" : "Przeglądanie";
  $("#count").innerHTML = `${esc(what)} · <b>${n.toLocaleString("pl-PL")}</b> ${n === 1 ? "plik" : "plików"}` +
    (r.ms ? ` · ${r.ms} ms` : "") +
    (S.similar || S.image || S.collection || S.likeTag ? ` · <a href="#" id="clearMode">wyczyść</a>` : "");
  $("#clearMode")?.addEventListener("click", e => { e.preventDefault(); resetMode(); search(); });
  $("#more").textContent = S.items.length < S.total ? "" : (n ? "— koniec —" : "");
  const empty = $("#empty");
  empty.hidden = n > 0;
  if (!n) empty.innerHTML = S.status && !S.status.vectors ?
    "<b>Indeks się buduje…</b>Pierwsze wyniki pojawią się za chwilę — postęp widać w lewym dolnym rogu." :
    "<b>Nic nie znalazłem</b>Spróbuj opisać inaczej, zdejmij filtry albo przeciągnij podobny obraz.";
}

function resetMode() { S.similar = null; S.image = null; S.collection = null; S.likeTag = null; renderCols(); }

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
  return `<div class="card${S.sel.has(it.id) ? " sel" : ""}${it.versions ? " stack" : ""}" data-i="${i}" draggable="${NATIVE ? "false" : "true"}">
    <div class="th">${thumbHtml(it)}
      ${it.kind === "video" || it.kind === "audio" ? `<span class="badge">${ICON[it.kind]} ${fmtDur(it.duration)}</span>` : ""}
      ${it.offline ? `<span class="badge r off" title="Dysk offline — miniatura z pamięci">offline</span>` :
        it.versions ? `<span class="badge r ver" title="${esc(it.versions.map(v => v.name).join("\n"))}">+${it.versions.length - 1} wersji</span>` : ""}
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
  renderVersions(it);
  renderPvColors(it);
  renderTranscript(it);
  $("#pvBg").hidden = !BG_OK.has((it.ext || "").toLowerCase()) || it.offline;
  if (!S.bgResult || S.bgResult.id !== it.id) $("#pvBgBar").hidden = true;
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
    `<button data-t="new">＋ tag</button><span id="pvSugg"></span>`;
  api(`/api/suggest-tags/${it.id}`).then(sg => {
    if (S.pv?.id !== it.id || !sg.length) return;
    $("#pvSugg").innerHTML = `<span class="sugg-l">✨ Sugerowane:</span>` + sg.map(t =>
      `<button data-t="${t.id}" class="sugg" title="Podobny do plików z tym tagiem (${Math.round(t.sim * 100)}%)">＋ <span class="dot" style="background:${esc(t.color)}"></span>${esc(t.name)}</button>`).join("");
  }).catch(() => {});
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
  noteFolder(it.folder);
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

function renderFormats() {
  $("#formats").innerHTML = (S.status?.formats || []).filter(f => f.count).map(f => {
    const key = f.exts.join(",");
    const on = f.exts.every(e => S.exts.has(e));
    return `<button data-f="${esc(key)}" class="${on ? "on" : ""}" title="${esc(f.exts.join(" "))}">${esc(f.icon || "")} ${esc(f.name)}<small>${f.count.toLocaleString("pl-PL")}</small></button>`;
  }).join("") || `<small style="color:var(--muted)">brak plików w tych formatach</small>`;
}

function renderRoots() {
  $("#roots").innerHTML = (S.status?.roots || []).map(r => `<button class="item ${(S.browse != null ? sideOn(r.path) : S.roots.has(r.path)) ? "on" : ""}" data-r="${esc(r.path)}">
    <span class="dot ${r.online ? "online" : "offline"}" title="${r.online ? "online" : "offline — wyniki z indeksu"}"></span>
    ${esc(r.path.split("/").pop() || r.path)}<span class="n">${r.count.toLocaleString("pl-PL")}</span></button>`).join("");
  $("#roots").insertAdjacentHTML("beforeend", (S.status?.places || []).map(pl => `<button class="item ${sideOn(pl.path) ? "on" : ""}" data-place="${esc(pl.path)}" title="${esc(pl.path)}">
    <span>${esc(pl.icon || "📁")}</span>${esc(pl.name)}<span class="n">${pl.count.toLocaleString("pl-PL")}</span></button>`).join(""));
  const known = new Set([...(S.status?.places || []).map(p => p.path), ...S.pins.map(p => p.path)]);
  const extra = S.browse != null ? [] : [...S.folders].filter(f => !known.has(f));
  const ff = $("#folderFilter");
  ff.hidden = !extra.length;
  ff.innerHTML = extra.map(f => `<div class="ffchip" title="${esc(f)}">📂 ${esc(f.split("/").pop())}
    <a data-pinf="${esc(f)}" title="Przypnij">📌</a><a data-unf="${esc(f)}" title="Usuń filtr">✕</a></div>`).join("");
  $("#whereAll").hidden = S.browse != null || !(S.roots.size || S.folders.size);
  renderPins(); renderRecent();
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
  // folder scan has no known total: compare with the number of files already in the index instead
  const scanning = ix.running && !ix.total && ix.done;
  const pct = ix.total ? Math.min(100, (ix.done / ix.total) * 100) : scanning && totalFiles ? Math.min(99, ix.done / totalFiles * 100) : 0;
  const eta = ix.rate && ix.total ? Math.max(0, (ix.total - ix.done) / ix.rate) : 0;
  const etaS = eta > 3600 ? `~${(eta / 3600).toFixed(1)} h` : eta > 60 ? `~${Math.round(eta / 60)} min` : eta ? "<1 min" : "";
  $("#status").innerHTML = `
    <div><b>${totalFiles.toLocaleString("pl-PL")}</b> plików · ${(s.vectors || 0).toLocaleString("pl-PL")} wektorów</div>
    ${ix.running ? `<div style="margin-top:6px">⏳ ${esc(ix.phase || "")}</div>
      <div class="prog"><i style="width:${pct}%"></i></div>
      <div>${scanning ? `przejrzano ${ix.done.toLocaleString("pl-PL")} z ok. ${totalFiles.toLocaleString("pl-PL")} plików`
        : `${(ix.done || 0).toLocaleString("pl-PL")} / ${(ix.total || 0).toLocaleString("pl-PL")} ${ix.rate ? `· ${ix.rate}/s` : ""} ${etaS}`}</div>
      <div class="cur" title="${esc(ix.current)}">${esc(ix.current || "")}</div>
      <button id="ixStop">Wstrzymaj</button>` :
      `<div style="margin-top:4px">${pending ? `Do przeanalizowania: ${pending.toLocaleString("pl-PL")}` : "✅ Indeks aktualny"}</div>
      <div>${s.last_index ? "Ostatnio: " + fmtDateTime(s.last_index) : ""}</div>
      ${(ix.denied || []).length ? `<div class="warn" title="${esc(ix.denied.join("\n"))}">⚠️ Brak dostępu do ${ix.denied.length} folderów — Ustawienia → Prywatność → Pełny dostęp do dysku → Lupa</div>` : ""}
      <button id="ixStart">Indeksuj teraz</button>`}
    ${s.model_ready ? "" : `<div style="margin-top:6px">🧠 Ładuję model…</div>`}`;
  $("#ixStart")?.addEventListener("click", async () => { await api("/api/index/start?full=1", { method: "POST" }); toast("Indeksowanie wystartowało"); setTimeout(loadStatus, 1500); });
  $("#ixStop")?.addEventListener("click", async () => { await api("/api/index/stop", { method: "POST" }); toast("Zatrzymuję po bieżącej paczce"); });
}

async function loadStatus() {
  try {
    const first = !S.status;
    S.status = await api("/api/status");
    renderKinds(); renderFormats(); renderRoots(); renderStatus();
    if (first) search();
  } catch {}
  clearTimeout(loadStatus._t);
  loadStatus._t = setTimeout(loadStatus, S.status?.indexer?.running ? 3000 : 15000);
}
async function loadTags() { S.tagsList = await api("/api/tags"); renderTags(); }
async function loadCols() { S.cols = await api("/api/collections"); renderCols(); }

// ------------------------------------------------------------------ colour search
const COLORS = [["Czerwony", "#d32f2f"], ["Bordowy", "#7b1e2b"], ["Pomarańczowy", "#f57c00"], ["Żółty", "#fbc02d"],
  ["Limonkowy", "#9ccc3a"], ["Zielony", "#2e7d32"], ["Turkusowy", "#00acc1"], ["Błękitny", "#4fc3f7"],
  ["Niebieski", "#1976d2"], ["Granatowy", "#1a237e"], ["Fioletowy", "#7b1fa2"], ["Różowy", "#e91e63"],
  ["Brązowy", "#6d4c41"], ["Beżowy", "#d7c4a3"], ["Czarny", "#111111"], ["Szary", "#9e9e9e"], ["Biały", "#ffffff"]];
function renderColors() {
  $("#colors").innerHTML = COLORS.map(([n, h]) => `<button data-c="${h}" title="${n}" style="background:${h}" class="${S.color === h ? "on" : ""}"></button>`).join("") +
    `<label class="custom" title="Własny kolor"><input type="color" value="${S.color || "#ff0000"}" id="colorPick">🎨</label>`;
  $("#colorClear").hidden = !S.color;
}
function setColor(h) { S.color = S.color === h ? "" : h; renderColors(); if (S.color && S.sort !== "relevance") { S.sort = "relevance"; $("#sort").value = "relevance"; } search(); }
$("#colors").addEventListener("click", e => { const b = e.target.closest("[data-c]"); if (b) setColor(b.dataset.c); });
$("#colors").addEventListener("change", e => { if (e.target.id === "colorPick") { S.color = ""; setColor(e.target.value); } });
$("#colorClear").addEventListener("click", () => { S.color = ""; renderColors(); search(); });
const BG_OK = new Set([".png", ".jpg", ".jpeg", ".webp", ".heic", ".heif", ".tif", ".tiff", ".bmp", ".gif"]);
async function removeBg(srcId, engine = "auto", replace = "") {
  const btn = $("#pvBg"); btn.disabled = true; btn.textContent = "✂️ Usuwam tło…";
  try {
    const r = await api(`/api/bgremove/${srcId}`, { method: "POST", json: { engine, replace } });
    S.bgResult = { id: r.id, src: srcId, path: r.path, engine: r.engine };
    const it = await api(`/api/file/${r.id}`);
    await openPreview({ ...it, tags: it.tags || [] });
    const other = r.engine === "logo" ? ["vision", "AI (Apple Vision) — do zdjęć i produktów"] : ["logo", "Logo (tło jednolite, zachowuje białe elementy)"];
    $("#pvBgBar").innerHTML = `✅ Utworzono <b>${esc(r.name)}</b> metodą <b>${r.engine === "logo" ? "Logo" : "AI (Apple Vision)"}</b>.
      Oryginał bez zmian. <button data-eng="${other[0]}">Spróbuj: ${esc(other[1])}</button>`;
    $("#pvBgBar").hidden = false;
    toast(`✂️ Gotowe: ${r.name}`, 3500);
  } catch (e) { toast("Nie udało się usunąć tła: " + e.message, 5000); }
  finally { btn.disabled = false; btn.textContent = "✂️ Usuń tło"; }
}
$("#pvBg").addEventListener("click", () => { const it = pvItem(); if (it) removeBg(it.id); });
$("#pvBgBar").addEventListener("click", e => {
  const b = e.target.closest("[data-eng]"); if (!b || !S.bgResult) return;
  removeBg(S.bgResult.src, b.dataset.eng, S.bgResult.path);
});
async function renderTranscript(it) {
  const box = $("#pvAsr"); box.hidden = true;
  if (it.kind !== "audio" && it.kind !== "video") return;
  try {
    const t = await api(`/api/transcript/${it.id}`);
    if (!t.segments.length || S.pv?.id !== it.id) return;
    box.innerHTML = `<div class="subh">🎙 Transkrypcja${t.lang ? " (" + esc(t.lang) + ")" : ""} — kliknij zdanie, by przewinąć</div>` +
      t.segments.map(s => `<p data-t="${s.start}"><span>${fmtDur(s.start) || "0:00"}</span>${esc(s.text)}</p>`).join("");
    box.hidden = false;
    box.onclick = e => {
      const p = e.target.closest("[data-t]"); if (!p) return;
      const m = $("#pvMedia video, #pvMedia audio"); if (m) { m.currentTime = +p.dataset.t; m.play(); }
    };
  } catch {}
}
async function renderPvColors(it) {
  const box = $("#pvColors"); box.hidden = true;
  if (!["image", "design", "raw", "video", "pdf"].includes(it.kind)) return;
  try {
    const pal = await api(`/api/colors/${it.id}`);
    if (!pal.length || S.pv?.id !== it.id) return;
    box.innerHTML = `<div class="subh">Kolory (kliknij, by znaleźć pliki w tym kolorze)</div><div class="pal">${pal.map(c =>
      `<button data-c="${c.hex}" style="background:${c.hex};flex:${Math.max(c.share, .06)}" title="${c.hex} · ${Math.round(c.share * 100)}%"></button>`).join("")}</div>`;
    box.hidden = false;
    box.onclick = e => { const b = e.target.closest("[data-c]"); if (b) { S.color = ""; setColor(b.dataset.c); } };
  } catch {}
}

// ------------------------------------------------------------------ folder browser
async function browseTo(path) {
  S.browse = path || "";
  S.q = ""; $("#q").value = ""; resetMode();
  S.sort = "name"; S.order = "asc"; $("#sort").value = "name"; $("#order").textContent = "↑";
  if (path) noteFolder(path);
  $("#fhits").hidden = true; $("#ftiles").classList.remove("qhide");
  renderBrowse(null);
  const d = await api("/api/browse?path=" + encodeURIComponent(S.browse));
  if (S.browse !== (path || "")) return;
  renderBrowse(d);
  renderRoots();
  if (S.browse) search(); else { S.items = []; $("#results").innerHTML = ""; $("#count").textContent = ""; $("#empty").hidden = true; }
}
// Folders whose name matches the query, shown above the files (Szukaj and Przeglądaj)
let fhitSeq = 0;
async function folderHits() {
  const id = ++fhitSeq, q = S.q;
  const show = !!q && !S.image && !S.similar;
  $("#ftiles").classList.toggle("qhide", show && S.browse != null);  // while typing in Przeglądaj: matches replace subfolders
  if (!show) { $("#fhits").hidden = true; return; }
  let d;
  try { d = await api(`/api/folders/find?q=${encodeURIComponent(q)}&within=${encodeURIComponent(S.browse || "")}&limit=${S.browse != null ? 24 : 8}`); }
  catch { return; }
  if (id !== fhitSeq) return;
  const fs = d.folders || [];
  $("#fhits").hidden = !fs.length;
  $("#fhitsH").innerHTML = `📁 Foldery pasujące do „${esc(q)}”` + (d.total > fs.length ? ` <small>· ${fs.length} z ${d.total}</small>` : "");
  $("#fhitTiles").innerHTML = fs.map(f => `<div class="ftile" data-b="${esc(f.path)}" title="${esc(f.path)}">
    <div class="fcollage n${Math.min(f.thumbs.length, 4)}">${f.thumbs.slice(0, 4).map(i => `<img loading="lazy" src="/api/thumb/${i}" onerror="this.remove()">`).join("") || '<span>📁</span>'}</div>
    <div class="fname">📁 ${esc(f.name)}</div><div class="fcount">${f.count.toLocaleString("pl-PL")} plików · ${esc(f.parent.replace(/^\/Volumes\//, "").replace(/^\/Users\/[^/]+/, "~"))}</div></div>`).join("");
}
$("#fhitTiles").addEventListener("click", e => {
  const t = e.target.closest("[data-b]"); if (!t) return;
  if (S.browse != null) return browseTo(t.dataset.b);
  S.browse = t.dataset.b; setTab("browse");  // from Szukaj: open the folder in Przeglądaj
});

function renderBrowse(d) {
  const on = S.browse != null;
  $("#browseBar").hidden = !on; $("#ftiles").hidden = !on;
  if (!on) return;
  if (!d) { $("#ftiles").innerHTML = `<div class="ftload"><span class="spin"></span> Wczytuję folder…</div>`; return; }
  $("#crumbs").innerHTML = `<a data-b="">🏠 Start</a>` + (d.crumbs || []).map(c => `<span>›</span><a data-b="${esc(c.path)}">${esc(c.name)}</a>`).join("");
  $("#bUp").disabled = !S.browse; $("#bPin").hidden = $("#bFinder").hidden = !S.browse;
  $("#ftiles").innerHTML = d.folders.map(f => `<div class="ftile" data-b="${esc(f.path)}" title="${esc(f.path)}">
    <div class="fcollage n${Math.min(f.thumbs.length, 4)}">${f.thumbs.slice(0, 4).map(id => `<img loading="lazy" src="/api/thumb/${id}" onerror="this.remove()">`).join("") || '<span>📁</span>'}</div>
    <div class="fname">📁 ${esc(f.name)}</div><div class="fcount">${f.count.toLocaleString("pl-PL")} plików</div></div>`).join("");
  $("#ftiles").classList.toggle("empty", !d.folders.length);
}
function setTabSearch(tab) {  // search ↔ browse share the same grid, preview and filters
  document.body.classList.toggle("mode-browse", tab === "browse");
  document.body.classList.toggle("mode-ask", tab === "ask");
  $("#askView").hidden = tab !== "ask";
  if (tab === "ask") { S.browse = null; $("#fhits").hidden = true; renderBrowse(null); renderRoots(); setTimeout(() => $("#askQ").focus(), 30); return; }
  if (tab === "browse") browseTo(S.browse || ""); else { S.browse = null; renderBrowse(null); renderRoots(); search(); }
}
$("#crumbs").addEventListener("click", e => { const a = e.target.closest("[data-b]"); if (a) browseTo(a.dataset.b); });
$("#ftiles").addEventListener("click", e => { const t = e.target.closest("[data-b]"); if (t) browseTo(t.dataset.b); });
$("#bUp").addEventListener("click", () => {
  if (!S.browse) return;
  const up = S.browse.split("/").slice(0, -1).join("/");
  browseTo(["", "/", "/Volumes"].includes(up) ? "" : up);
});
$("#bPin").addEventListener("click", () => S.browse && pinFolder(S.browse));
$("#bFinder").addEventListener("click", () => S.browse && api("/api/open-folder", { method: "POST", json: { path: S.browse } }));

// ------------------------------------------------------------------ ask your files (local Gemma)
const ASK_EX = ["Ile kosztowała ostatnia oferta dla klienta?", "Jakie są warunki płatności w umowach zlecenia?",
  "Kiedy jest termin oddania projektu?", "Jakie faktury wystawiła firma w sierpniu?"];
$("#askEx").innerHTML = ASK_EX.map(q => `<button>${esc(q)}</button>`).join("");
$("#askEx").addEventListener("click", e => { const b = e.target.closest("button"); if (b) { $("#askQ").value = b.textContent; askRun(); } });
function askFilters() {
  const p = params({}, true);
  const f = {};
  for (const k of ["kinds", "exts", "roots", "since", "folder", "tags"]) if (p.get(k)) f[k] = p.get(k);
  return f;
}
function askFormat(text, srcs) {
  let h = esc(text).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/^\s*[*-] /gm, "• ").replace(/\n/g, "<br>");
  return h.replace(/\[(\d+(?:\s*,\s*\d+)*)\]/g, (m, nums) => nums.split(",").map(n => {
    const k = +n.trim(); return k >= 1 && k <= srcs.length ? `<a class="cite" data-n="${k}">${k}</a>` : "";
  }).join(""));
}
async function askRun() {
  const q = $("#askQ").value.trim(); if (!q) return;
  $("#askQ").value = ""; $("#askIntro").hidden = true;
  const box = document.createElement("div"); box.className = "ask-turn";
  box.innerHTML = `<div class="ask-q">${esc(q)}</div><div class="ask-a"><span class="spin"></span> Szukam w Twoich plikach…</div><div class="ask-src"></div>`;
  $("#askThread").prepend(box);
  const A = $(".ask-a", box), SRC = $(".ask-src", box);
  let srcs = [], text = "";
  try {
    const r = await fetch("/api/ask", { method: "POST", headers: { "x-lupa": "1", "content-type": "application/json" },
      body: JSON.stringify({ q, filters: askFilters() }) });
    const rd = r.body.getReader(); const dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { value, done } = await rd.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl); buf = buf.slice(nl + 1); if (!line.trim()) continue;
        const m = JSON.parse(line);
        if (m.type === "sources") {
          srcs = m.sources; box._srcs = srcs;
          A.innerHTML = srcs.length ? `<span class="spin"></span> Czytam ${srcs.length} dokumentów (${esc(m.model)})…` : "";
          SRC.innerHTML = srcs.map(s => `<div class="ask-s" data-n="${s.n}">
            <div class="ask-sh"><b>${s.n}</b>${thumbHtml(s)}<div><div class="nm">${esc(s.name)}</div><div class="fd">${esc(s.folder)}</div></div></div>
            <div class="ask-p">${esc(s.passage || "")}</div></div>`).join("");
        } else if (m.type === "token") { text += m.t; A.innerHTML = askFormat(text, srcs); }
        else if (m.type === "error") { A.innerHTML = `<span class="err">${esc(m.error)}</span>`; }
      }
    }
  } catch (e) { A.innerHTML = `<span class="err">Błąd: ${esc(e.message)}</span>`; }
}
$("#askForm").addEventListener("submit", e => { e.preventDefault(); askRun(); });
$("#askQ").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); askRun(); } });
$("#askThread").addEventListener("click", e => {
  const el = e.target.closest("[data-n]"); if (!el) return;
  const turn = e.target.closest(".ask-turn"); const s = turn?._srcs?.find(x => x.n == el.dataset.n);
  if (!s) return;
  if (e.target.closest(".cite")) { const card = turn.querySelector(`.ask-s[data-n="${s.n}"]`); card?.scrollIntoView({ block: "nearest", behavior: "smooth" }); card?.classList.add("flash"); setTimeout(() => card?.classList.remove("flash"), 900); }
  openPreview({ ...s, tags: s.tags || [] });
});

// ------------------------------------------------------------------ pinned & recent folders
// Click adds/removes a place (several can be on at once); ⌥-click keeps only that one.
function toggleFolder(path, only) {
  if (S.browse != null) return browseTo(path);
  if (only) { S.roots.clear(); S.folders = new Set([path]); }
  else S.folders.has(path) ? S.folders.delete(path) : S.folders.add(path);
  if (S.folders.has(path)) noteFolder(path);
  renderRoots(); search();
}
// Sidebar highlight: in Przeglądaj it follows the folder you are in (deepest matching entry);
// in Szukaj it shows the active folder filters.
function sideOn(path) {
  if (S.browse == null) return S.folders.has(path);
  if (!S.browse) return false;
  const all = [...CFG_ROOTS(), ...(S.status?.places || []).map(p => p.path), ...S.pins.map(p => p.path),
    ...Object.keys(store.get("recentFolders", {}))];
  const best = all.filter(p => S.browse === p || S.browse.startsWith(p + "/")).sort((a, b) => b.length - a.length)[0];
  return best === path;
}
const CFG_ROOTS = () => (S.status?.roots || []).map(r => r.path);

function renderPins() {
  $("#pins").innerHTML = S.pins.map(p => `<button class="item ${sideOn(p.path) ? "on" : ""} ${p.exists ? "" : "dim"}" data-pin="${esc(p.path)}" data-pid="${p.id}" title="${esc(p.path)}${p.exists ? "" : " (niedostępny)"}">
    <span>${esc(p.icon || "📌")}</span>${esc(p.name)}<span class="n">${p.count.toLocaleString("pl-PL")}</span></button>`).join("");
}
async function loadPins() { try { S.pins = await api("/api/pins"); } catch { S.pins = []; } renderRoots(); }
function noteFolder(path) {  // remember folders you browse (per device)
  if (!path) return;
  const r = store.get("recentFolders", {});
  r[path] = { n: (r[path]?.n || 0) + 1, t: Date.now() };
  const keep = Object.entries(r).sort((a, b) => b[1].t - a[1].t).slice(0, 30);
  store.set("recentFolders", Object.fromEntries(keep));
}
let recentCounts = {};
function renderRecent() {
  const pinned = new Set([...S.pins.map(p => p.path), ...(S.status?.places || []).map(p => p.path), ...CFG_ROOTS()]);
  const r = Object.entries(store.get("recentFolders", {})).filter(([p]) => !pinned.has(p))
    .sort((a, b) => (b[1].n * 2 + b[1].t / 8.64e7) - (a[1].n * 2 + a[1].t / 8.64e7)).slice(0, 5);
  $("#recentWrap").hidden = !r.length;
  $("#recent").innerHTML = r.map(([p]) => `<button class="item ${sideOn(p) ? "on" : ""}" data-recent="${esc(p)}" title="${esc(p)}">
    <span>🕘</span>${esc(p.split("/").pop() || p)}<span class="n">${recentCounts[p] != null ? recentCounts[p].toLocaleString("pl-PL") : ""}</span>
    <span class="x" data-pinr="${esc(p)}" title="Przypnij">📌</span></button>`).join("");
  const need = r.map(([p]) => p).filter(p => recentCounts[p] == null);
  if (need.length) api("/api/folder-counts?paths=" + encodeURIComponent(need.join("|"))).then(c => { Object.assign(recentCounts, c); renderRecent(); }).catch(() => {});
}
async function pinFolder(path) {
  if (!path) {
    toast("Wybierz folder w oknie systemowym…", 2500);
    path = (await api("/api/pick-folder", { method: "POST", json: { prompt: "Wybierz folder do przypięcia w Lupie" } })).path;
    if (!path) return;
  }
  await api("/api/pins", { method: "POST", json: { path } });
  toast(`📌 Przypięto: ${path.split("/").pop()}`); loadPins();
}
function pinMenu(x, y, pid) {
  const pin = S.pins.find(p => p.id == pid); if (!pin) return;
  const m = $("#menu");
  m.innerHTML = `<button class="item" data-pa="rename">✏️ Zmień nazwę</button><button class="item" data-pa="icon">😀 Zmień ikonę</button>
    <button class="item" data-pa="reveal">🔍 Pokaż w Finderze</button><button class="item" data-pa="unpin">✕ Odepnij</button>`;
  m.style.left = Math.min(x, innerWidth - 220) + "px"; m.style.top = Math.min(y, innerHeight - 200) + "px"; m.hidden = false;
  m.onclick = async e => {
    const a = e.target.closest("[data-pa]")?.dataset.pa; if (!a) return; m.hidden = true;
    if (a === "rename") { const n = prompt("Nazwa:", pin.name); if (n) await api(`/api/pins/${pid}`, { method: "PATCH", json: { name: n } }); }
    if (a === "icon") { const n = prompt("Ikona (emoji):", pin.icon || "📌"); if (n) await api(`/api/pins/${pid}`, { method: "PATCH", json: { icon: n } }); }
    if (a === "unpin") { await api(`/api/pins/${pid}`, { method: "DELETE" }); S.folders.delete(pin.path); search(); }
    if (a === "reveal") return api(`/api/pins/${pid}/reveal`, { method: "POST" });
    loadPins();
  };
}
$("#addPin").addEventListener("click", () => pinFolder());
$("#pins").addEventListener("click", e => { const b = e.target.closest("[data-pin]"); if (b) toggleFolder(b.dataset.pin, e.altKey); });
$("#pins").addEventListener("contextmenu", e => { const b = e.target.closest("[data-pid]"); if (!b) return; e.preventDefault(); pinMenu(e.clientX, e.clientY, b.dataset.pid); });
$("#recent").addEventListener("click", e => {
  const pr = e.target.closest("[data-pinr]"); if (pr) { e.stopPropagation(); return pinFolder(pr.dataset.pinr); }
  const b = e.target.closest("[data-recent]"); if (b) toggleFolder(b.dataset.recent, e.altKey);
});
$("#folderFilter").addEventListener("click", e => { const x = e.target.closest("[data-pinf]"); if (x) pinFolder(x.dataset.pinf); });

// ------------------------------------------------------------------ collect selected files
async function collect(kind) {
  const ids = selectedIds(); if (!ids.length) return;
  try {
    if (kind === "zip") {
      toast(`Pakuję ${ids.length} plików…`, 60000);
      const r = await api("/api/collect/zip", { method: "POST", json: { ids } });
      toast(`📦 Gotowe: ${r.count} plików, ${fmtSize(r.size)} → Pobrane`, 5000);
    } else {
      toast("Wybierz folder docelowy w oknie systemowym…", 4000);
      const r = await api("/api/collect/copy", { method: "POST", json: { ids } });
      if (r.cancelled) return toast("Anulowano");
      toast(r.ok ? `📁 Skopiowano ${r.count} plików do „${r.dest.split("/").pop()}”` : `Skopiowano ${r.count}, błędy: ${r.failed.join("; ")}`, 6000);
    }
  } catch (e) { toast("Nie udało się: " + e.message, 5000); }
}
$("#selZip").addEventListener("click", () => collect("zip"));
$("#selCopy").addEventListener("click", () => collect("copy"));

// ------------------------------------------------------------------ versions (grouping)
function applyGroupBtn() { $("#groupBtn").classList.toggle("on", S.group); }
$("#groupBtn").addEventListener("click", () => {
  S.group = !S.group; store.set("group", S.group); applyGroupBtn();
  toast(S.group ? "🗂 Grupowanie wersji włączone" : "Grupowanie wersji wyłączone"); search();
});
function renderVersions(it) {
  const v = it.versions || (S.items.find(x => x.versions?.some(y => y.id === it.id))?.versions);
  const box = $("#pvVersions");
  box.hidden = !v || v.length < 2;
  if (box.hidden) return;
  box.innerHTML = `<div class="subh">Wersje (${v.length})</div><div class="vstrip">${v.map(x => `<button class="vthumb ${x.id === it.id ? "on" : ""}" data-v="${x.id}" title="${esc(x.name)}">
    ${THUMBABLE.has(x.kind) ? `<img loading="lazy" src="/api/thumb/${x.id}" onerror="this.replaceWith(Object.assign(document.createElement('span'),{textContent:'${ICON[x.kind] || "📁"}'}))">` : `<span>${ICON[x.kind] || "📁"}</span>`}
    <small>${esc(x.name)}</small></button>`).join("")}</div>`;
  box.onclick = e => {
    const b = e.target.closest("[data-v]"); if (!b) return;
    const x = v.find(y => y.id == b.dataset.v); if (!x) return;
    openPreview({ ...x, versions: v, tags: x.tags || [] });
  };
}

// ------------------------------------------------------------------ drag files out (Lupa.app → other apps)
const NATIVE = !!window.webkit?.messageHandlers?.lupa;
if (NATIVE) document.body.classList.add("native-drag");
let dragStart = null;
// what a mousedown at `el` would drag: the selection (cards), a folder (tiles), the previewed file (preview, viewer)
function dragPaths(el) {
  const c = el.closest(".card");
  if (c) {
    const it = S.items[+c.dataset.i]; if (!it || it.offline) return null;
    const ids = S.sel.has(it.id) ? S.sel : new Set([it.id]);
    return S.items.filter(x => ids.has(x.id) && !x.offline).map(x => x.path);
  }
  const t = el.closest(".ftile[data-b]"); if (t) return [t.dataset.b];
  // preview / viewer: the picture, placeholder or the file name (video, audio and PDF keep their own controls)
  if (el.closest("#pvMedia img, #pvMedia .ph, #pvName, #lbMedia img, #lbMedia .ph, #lbName")) {
    const it = pvItem(); return it && !it.offline ? [it.path] : null;
  }
  return null;
}
addEventListener("mousedown", e => {
  dragStart = null;
  if (!NATIVE || e.button !== 0) return;
  const paths = dragPaths(e.target);
  if (paths?.length) dragStart = { x: e.clientX, y: e.clientY, paths, sent: false };
}, true);
addEventListener("mousemove", e => {
  if (!dragStart || dragStart.sent) return;
  if (Math.hypot(e.clientX - dragStart.x, e.clientY - dragStart.y) < 5) return;
  dragStart.sent = true;
  window.webkit.messageHandlers.lupa.postMessage({ type: "drag", paths: dragStart.paths });
});
addEventListener("mouseup", () => { dragStart = null; });
// WebKit would otherwise start its own drag of the thumbnail <img> (a JPEG preview, not the file) and swallow the gesture
if (NATIVE) addEventListener("dragstart", e => { if (dragPaths(e.target)) e.preventDefault(); }, true);
if (!NATIVE) {  // plain Chrome: drag a card to the desktop/Finder downloads the file
  $("#results").addEventListener("dragstart", e => {
    const c = e.target.closest(".card"); if (!c) return;
    const it = S.items[+c.dataset.i];
    e.dataTransfer.setData("DownloadURL", `application/octet-stream:${it.name}:${location.origin}/api/raw/${it.id}`);
  });
}

// ------------------------------------------------------------------ events
let qTimer;
$("#q").addEventListener("input", e => {
  if (S.browse) { S.folders = new Set([S.browse]); renderRoots(); }  // search inside the folder you are browsing
  S.q = e.target.value.trim();
  if (S.browse != null) {  // Przeglądaj: best matches first while typing, A→Z again when the query is cleared
    S.sort = S.q ? "relevance" : "name"; S.order = S.q ? "desc" : "asc";
    $("#sort").value = S.sort; $("#order").textContent = S.q ? "↓" : "↑";
  }
  $("#results").classList.add("busy");  // instant feedback while the debounce waits
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
$("#formats").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  const exts = b.dataset.f.split(",");
  const on = exts.every(x => S.exts.has(x));
  if (!(e.metaKey || e.shiftKey)) S.exts.clear();  // ⌘/⇧-click combines formats
  exts.forEach(x => on ? S.exts.delete(x) : S.exts.add(x));
  renderFormats(); search();
});
$("#since").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  S.since = b.dataset.d; $$("#since button").forEach(x => x.classList.toggle("on", x === b)); search();
});
$("#roots").addEventListener("click", e => {
  const pl = e.target.closest("[data-place]");
  if (pl) { toggleFolder(pl.dataset.place, e.altKey); return; }
  const b = e.target.closest("[data-r]"); if (!b) return;
  if (S.browse != null) return browseTo(b.dataset.r);  // Przeglądaj: open the drive
  const r = b.dataset.r;
  if (e.altKey) { S.folders.clear(); S.roots = new Set([r]); } else S.roots.has(r) ? S.roots.delete(r) : S.roots.add(r);
  renderRoots(); search();
});
$("#whereAll").addEventListener("click", () => { S.roots.clear(); S.folders.clear(); renderRoots(); search(); });
$("#folderFilter").addEventListener("click", e => {
  const x = e.target.closest("[data-unf]"); if (!x) return;
  S.folders.delete(x.dataset.unf); renderRoots(); search();
});
const TAG_COLORS = ["#ef5350", "#ff7043", "#ff9800", "#f5b301", "#cddc39", "#66bb6a", "#26a69a", "#29b6f6",
  "#42a5f5", "#5c6bc0", "#7e57c2", "#ab47bc", "#ec407a", "#8d6e63", "#78909c", "#e0e0e0"];
function tagEditMenu(x, y, tid) {
  const t = S.tagsList.find(t => t.id === tid); if (!t) return;
  const m = $("#menu");
  m.innerHTML = `<div class="swatches">${TAG_COLORS.map(c => `<button data-c="${c}" style="background:${c}" class="${c === t.color ? "on" : ""}" title="${c}"></button>`).join("")}
    <label class="custom" title="Własny kolor"><input type="color" value="${esc(t.color)}">🎨</label></div>
    <button class="item" data-a="like">✨ Znajdź pliki pasujące do tego tagu</button>
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
    if (a === "like") { m.hidden = true; resetMode(); S.q = ""; $("#q").value = ""; S.likeTag = tid; S.sort = "relevance"; $("#sort").value = "relevance"; return search(); }
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
$("#pvPin").addEventListener("click", () => { const it = pvItem(); if (it) pinFolder(it.folder); });
$("#pvFolder").addEventListener("click", () => { const it = pvItem(); if (!it) return; resetMode(); S.q = ""; $("#q").value = ""; S.folders = new Set([it.folder]); noteFolder(it.folder); S.sort = "name"; S.order = "asc"; $("#sort").value = "name"; $("#order").textContent = "↑"; renderRoots(); search(); });
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
  if (document.body.classList.contains("mode-clean")) return;  // Porządki tab has no grid shortcuts
  if (S.browse != null && ((e.key === "ArrowUp" && e.metaKey) || e.key === "Backspace")) { e.preventDefault(); $("#bUp").click(); return; }
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
  if (p.get("exts")) p.get("exts").split(",").forEach(x => S.exts.add(x));
  if (p.get("roots")) p.get("roots").split("|").forEach(r => S.roots.add(r));
  if (p.get("tags")) p.get("tags").split(",").forEach(t => S.tags.add(+t));
  if (p.get("collection")) S.collection = +p.get("collection");
  if (p.get("similar")) S.similar = +p.get("similar");
  S.since = p.get("since") || "";
  (p.get("folder") || "").split("|").filter(Boolean).forEach(f => S.folders.add(f));
  if (p.get("sort")) { S.sort = p.get("sort"); S.order = p.get("order") || "desc"; }
  $("#sort").value = S.sort; $("#order").textContent = S.order === "desc" ? "↓" : "↑";
  $$("#since button").forEach(b => b.classList.toggle("on", b.dataset.d === S.since));
  applyView();
  applyGroupBtn(); renderColors();
  Promise.all([loadTags(), loadCols(), loadPins()]).finally(loadStatus);
  $("#q").focus();
})();
